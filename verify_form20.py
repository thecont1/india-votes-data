#!/usr/bin/env python3
"""
Form 20 Verification CLI

Download a Form 20 PDF, run Vision LLM OCR (Tesseract as fallback),
compare against ECI results, and present a human-readable report.

Usage:
    python verify_form20.py <state_code> [ac_no] [--force] [--skip-vision]

Examples:
    # Verify a single AC:
    python verify_form20.py S25 110
    python verify_form20.py S25 275 --force

    # Verify ALL ACs in a state (updates DB):
    python verify_form20.py S25

    # Incremental re-run (skips VERIFIED):
    python verify_form20.py S03
    python verify_form20.py S03 --force  # re-run everything
"""

import argparse
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()


def render_report(report: dict) -> None:
    """Render the verification report to the terminal."""
    state_code = report["state_code"]
    ac_no = report["ac_no"]
    ac_name = report.get("ac_name", f"AC {ac_no}")
    state_name = report.get("state_name", state_code)
    difficulty = report["difficulty"]
    label = report["difficulty_label"]
    summary = report["summary"]

    # Header
    console.print()
    console.print(
        Panel(
            f"[bold]{ac_name}[/bold] ({state_name}, {state_code}, AC {ac_no})\n"
            f"ECI results: {summary['total']} candidates | "
            f"Form 20: {report['page_count']} page(s)",
            title="Form 20 Verification",
            border_style="blue",
        )
    )

    # Difficulty score
    if label == "IMPOSSIBLE":
        score_style = "bold red"
    elif label == "HARD":
        score_style = "bold yellow"
    elif label == "MODERATE":
        score_style = "bold cyan"
    else:
        score_style = "bold green"

    console.print()
    console.print(
        f"  DIFFICULTY SCORE: [{score_style}]{difficulty}/100 — {label}[/]"
    )
    console.print()

    # If impossible, show a short message and exit
    if label == "IMPOSSIBLE":
        console.print(
            "  [red]The Vision LLM could not confirm most candidates.[/]\n"
            "  [red]This scan is too degraded for automated verification.[/]\n"
            "  [red]Manual verification required — cannot assist further.[/]"
        )
        console.print()
        return

    # Results table
    table = Table(show_header=True, header_style="bold", box=None, padding=(0, 1))
    table.add_column("#", style="dim", width=3, justify="right")
    table.add_column("Candidate", min_width=25)
    table.add_column("Party", width=8)
    table.add_column("ECI Votes", justify="right", min_width=10)
    table.add_column("Form 20", justify="right", min_width=10)
    table.add_column("Δ", justify="right", min_width=8)
    table.add_column("Conf", width=10)
    table.add_column("Notes", min_width=15)

    for i, r in enumerate(report["reconciled"], 1):
        eci = f"{r['eci_votes']:,}"
        f20 = f"{r['form20_votes']:,}" if r.get("form20_votes") is not None else "—"

        # Delta
        delta = r.get("delta")
        if delta is None:
            delta_text = "—"
        elif delta == 0:
            delta_text = "[green]✓[/]"
        else:
            sign = "+" if delta > 0 else ""
            delta_text = f"[red]{sign}{delta:,}[/]"

        # Confidence
        conf = r.get("confidence", "low")
        if conf == "high":
            conf_text = "[green]HIGH[/]"
        elif conf == "medium":
            conf_text = "[yellow]MED[/]"
        else:
            conf_text = "[red]LOW[/]"

        # Name visibility
        name_vis = r.get("name_visible", "unknown")
        notes = r.get("notes") or ""
        if name_vis == "partial":
            notes = "name partial" + (f"; {notes}" if notes else "")
        elif name_vis == "no":
            notes = "name not visible" + (f"; {notes}" if notes else "")

        table.add_row(
            str(i), r["candidate"], r["party_abv"],
            eci, f20, delta_text, conf_text, notes,
        )

    console.print(table)

    # Summary
    console.print()
    confirmed = summary["confirmed"]
    mismatched = summary["mismatched"]
    low_conf = summary["low_confidence"]
    total = summary["total"]

    parts = [f"[green]{confirmed} confirmed[/]"]
    if mismatched:
        parts.append(f"[red]{mismatched} mismatched[/]")
    if low_conf:
        parts.append(f"[yellow]{low_conf} low confidence[/]")
    console.print(f"  SUMMARY: {' | '.join(parts)} of {total} total")
    console.print()


def run_single(state_code: str, ac_no: int, args) -> dict | None:
    """Run verification for a single AC. Returns report dict or None on error."""
    from tools.ocr_engine import run_pipeline

    try:
        report = run_pipeline(
            state_code,
            ac_no,
            output_dir=args.output_dir,
            force=args.force,
            skip_vision=args.skip_vision,
        )
    except SystemExit:
        return None
    except Exception as e:
        console.print(f"  [red]AC {ac_no} ERROR: {e}[/]")
        return None

    return report


def update_db(report: dict) -> str:
    """Write verification results back to constituency_status. Returns the status."""
    from tools.ocr_engine import update_form20_result

    s = report["summary"]
    return update_form20_result(
        state_code=report["state_code"],
        ac_no=report["ac_no"],
        difficulty=report["difficulty"],
        difficulty_label=report["difficulty_label"],
        mismatched=s["mismatched"],
        confirmed=s["confirmed"],
    )


def get_current_statuses(state_code: str) -> dict:
    """Get current form20_status for all ACs in a state. Returns {ac_no: status}."""
    import db_utils
    conn = db_utils._connect()
    cur = db_utils._cursor(conn)
    try:
        p = db_utils._placeholder()
        cur.execute(
            f"""SELECT ac_no, form20_status FROM constituency_status
                WHERE state_code = {p} AND form20_url IS NOT NULL""",
            (state_code,),
        )
        return {
            (r["ac_no"] if hasattr(r, "keys") else r[0]):
            (r["form20_status"] if hasattr(r, "keys") else r[1])
            for r in cur.fetchall()
        }
    finally:
        conn.close()


# Unicode block characters — universally supported, visually distinct
_CHAR = {
    "VERIFIED":   "█",   # solid block — complete
    "MISMATCH":   "█",   # solid block — something's off
    "ERROR":      "█",   # solid block — failed
    "UNVERIFIED": "·",   # dot — not yet processed
    "PENDING":    "·",   # dot — waiting
}
_LINE_WIDTH = 30  # characters per line
# ANSI background colors for grid blocks
_BG = {
    "VERIFIED": "\033[42m",   # green background
    "MISMATCH": "\033[41m",   # red background
    "ERROR":    "\033[43m",   # yellow background
}
_BG_RESET = "\033[0m"


def _process_one_ac(state_code: str, ac: dict, args) -> dict | None:
    """Process a single AC: run pipeline, update DB. Returns report dict or None.

    Always re-processes — the batch runner already filters out VERIFIED,
    so every AC here needs a fresh run (skip stale report.json cache).
    """
    ac_no = ac["ac_no"]

    # Force re-run: batch runner already excluded VERIFIED ACs
    saved_force = args.force
    args.force = True
    try:
        report = run_single(state_code, ac_no, args)
    finally:
        args.force = saved_force

    if report is None:
        return None

    # Update DB
    db_status = update_db(report)
    report["_db_status"] = db_status
    return report


def run_state_batch(state_code: str, args) -> None:
    """Process all ACs in a state that have form20_urls.

    Without --force: skips ACs already VERIFIED.
    With --force: re-processes everything.

    Shows a block-character grid — one block per AC, 30 per line.
    """
    from tools.ocr_engine import get_state_acs_with_form20

    acs = get_state_acs_with_form20(state_code)
    if not acs:
        console.print(f"  [red]No ACs with form20_url found for {state_code}[/]")
        console.print(
            "  [dim]Set URLs first: UPDATE constituency_status SET form20_url='...' "
            f"WHERE state_code='{state_code}' AND ac_no=N;[/]"
        )
        return

    # Determine which ACs need processing
    total_acs = len(acs)
    current = get_current_statuses(state_code) if not args.force else {}
    already_verified = 0
    if not args.force:
        verified_acs = {ac["ac_no"] for ac in acs if current.get(ac["ac_no"]) == "VERIFIED"}
        already_verified = len(verified_acs)
        acs = [ac for ac in acs if ac["ac_no"] not in verified_acs]

    console.print()
    console.print(
        Panel(
            f"[bold]State: {state_code}[/bold] — {total_acs} ACs total"
            + (f" — [green]{already_verified} already verified[/], {len(acs)} remaining"
               if already_verified else f" — {len(acs)} ACs to process"),
            title="Form 20 Batch Verification",
            border_style="blue",
        )
    )
    console.print()

    # Print legend with background-colored blocks
    legend = "    ".join([
        f"{_BG['VERIFIED']} {_BG_RESET} = VERIFIED",
        f"{_BG['MISMATCH']} {_BG_RESET} = MISMATCH",
        f"{_BG['ERROR']} {_BG_RESET} = ERROR",
    ])
    console.print(f"  {legend}")
    console.print()

    # Grid state
    grid_chars: list[str] = [_CHAR["VERIFIED"]] * already_verified
    grid_colors: list[str] = ["VERIFIED"] * already_verified
    line_buf: list[str] = []
    line_colors: list[str] = []
    counts = {"VERIFIED": already_verified, "MISMATCH": 0, "ERROR": 0}
    start_time = time.time()

    # Print pre-filled rows from already-verified
    if already_verified >= _LINE_WIDTH:
        for row in range(already_verified // _LINE_WIDTH):
            chunk = "".join(_BG["VERIFIED"] + _CHAR["VERIFIED"] for _ in range(_LINE_WIDTH)) + _BG_RESET
            sys.stdout.write(f"  {chunk}\n")
        sys.stdout.flush()

    # Process each AC — one block character per completion, 30 per line
    for i, ac in enumerate(acs):
        ac_no = ac["ac_no"]
        ac_name = ac["ac_name"]

        # Show what's processing (single line, overwritten)
        short_name = (ac_name[:20] + "…") if len(ac_name) > 21 else ac_name
        sys.stdout.write(f"\033[2K\033[1G  ⠋ {ac_no:>3} {short_name}  ")
        sys.stdout.flush()

        report = _process_one_ac(state_code, ac, args)

        if report is None:
            ch = _CHAR["ERROR"]
            status_key = "ERROR"
            counts["ERROR"] += 1
        else:
            status = report["_db_status"]
            ch = _CHAR.get(status, "·")
            status_key = status
            counts[status] = counts.get(status, 0) + 1

        grid_chars.append(ch)
        grid_colors.append(status_key)
        line_buf.append(ch)
        line_colors.append(status_key)
        n = len(grid_chars)

        # Erase spinner, then print growing grid line with background colors
        grid_cells = "".join(_BG.get(c, "") + ch for ch, c in zip(line_buf, line_colors)) + _BG_RESET
        sys.stdout.write(f"\033[2K\033[1G  {grid_cells}")
        sys.stdout.flush()

        # If line is full, commit it
        if n % _LINE_WIDTH == 0:
            sys.stdout.write(f"\033[2K\033[1G  {grid_cells}\n")
            sys.stdout.flush()
            line_buf.clear()
            line_colors.clear()

    # Commit any remaining partial line
    if line_buf:
        sys.stdout.write("\n")
        sys.stdout.flush()

    elapsed = time.time() - start_time

    # Summary
    console.print()
    console.print(
        Panel(
            f"[bold]{state_code}[/bold] — {already_verified + len(acs)}/{total_acs} ACs verified in {elapsed:.0f}s"
            + (f" ({elapsed / max(len(acs), 1):.0f}s/AC)" if acs else ""),
            title="Batch Summary",
            border_style="green",
        )
    )

    # Aggregate stats
    console.print()
    parts = []
    for s in ("VERIFIED", "MISMATCH", "ERROR"):
        c = counts.get(s, 0)
        if c:
            style = {"VERIFIED": "green", "MISMATCH": "red", "ERROR": "yellow"}[s]
            parts.append(f"[{style}]{c} {s.lower()}[/]")
    console.print("  " + " | ".join(parts))
    console.print()


def main():
    parser = argparse.ArgumentParser(
        description="Verify Form 20 against ECI election results"
    )
    parser.add_argument("state_code", help="ECI state code (e.g. S25)")
    parser.add_argument("ac_no", type=int, nargs="?", default=None,
                        help="AC number (omit to verify all ACs in state)")
    parser.add_argument(
        "--force", action="store_true", help="Re-run even if report exists"
    )
    parser.add_argument(
        "--skip-vision",
        action="store_true",
        help="Skip Vision LLM (Tesseract only)",
    )
    parser.add_argument(
        "--prepare",
        action="store_true",
        help="Prepare images + prompt for external vision analysis (Hermes mode)",
    )
    parser.add_argument(
        "--vision-file",
        type=Path,
        default=None,
        help="Path to pre-computed vision results JSON (list of per-page response strings)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/form20"),
        help="Output directory for reports",
    )
    args = parser.parse_args()

    # --prepare mode: single AC only
    if args.prepare:
        if args.ac_no is None:
            console.print("  [red]ERROR: --prepare requires an AC number[/]")
            sys.exit(1)
        from tools.ocr_engine import get_form20_url, get_eci_results, get_constituency_info, pdf_to_images, build_confirm_prompt

        console.print()
        console.print(f"  [bold]Form 20 Verification[/] — {args.state_code} AC {args.ac_no}")
        console.print()

        info = get_constituency_info(args.state_code, args.ac_no)
        eci_results = get_eci_results(args.state_code, args.ac_no)
        if not eci_results:
            console.print(f"  [red]ERROR: No ECI results for {args.state_code} AC {args.ac_no}[/]")
            sys.exit(1)
        form20_url = get_form20_url(args.state_code, args.ac_no)
        if not form20_url:
            console.print(f"  [red]ERROR: No form20_url for {args.state_code} AC {args.ac_no}[/]")
            sys.exit(1)

        report_dir = args.output_dir / args.state_code / str(args.ac_no)
        report_dir.mkdir(parents=True, exist_ok=True)
        pdf_path = report_dir / "source.pdf"
        if not pdf_path.exists():
            from tools.ocr_engine import _download_pdf
            _download_pdf(form20_url, pdf_path)

        image_dir = report_dir / "pages"
        image_paths = pdf_to_images(pdf_path, image_dir)
        prompt = build_confirm_prompt(
            info["ac_name"] if info else f"AC {args.ac_no}",
            args.state_code, args.ac_no, eci_results,
        )

        import json as _json
        prepare_data = {
            "state_code": args.state_code,
            "ac_no": args.ac_no,
            "ac_name": info["ac_name"] if info else f"AC {args.ac_no}",
            "image_paths": [str(p) for p in image_paths],
            "prompt": prompt,
            "eci_results": eci_results,
        }
        out_path = report_dir / "prepare.json"
        with open(out_path, "w") as f:
            _json.dump(prepare_data, f, indent=2)
        console.print(f"  [green]Prepared {len(image_paths)} pages[/]")
        console.print(f"  Images: {image_dir}/")
        console.print(f"  Prompt + ECI data: {out_path}")
        console.print()
        console.print("  Next: run vision_analyze on each page, save responses to vision_results.json")
        console.print(f"  Then: python verify_form20.py {args.state_code} {args.ac_no} --vision-file {report_dir}/vision_results.json")
        console.print()
        return

    # --vision-file mode: single AC only
    if args.vision_file:
        import os
        os.environ["_FORM20_VISION_FILE"] = str(args.vision_file)

    # --- Dispatch: single AC or full state ---
    if args.ac_no is not None:
        # Single AC mode
        console.print()
        console.print(f"  [bold]Form 20 Verification[/] — {args.state_code} AC {args.ac_no}")
        console.print()

        report = run_single(args.state_code, args.ac_no, args)
        if report is None:
            sys.exit(1)

        if report.get("cached") and not args.force:
            console.print("  [dim](using cached report — use --force to re-run)[/]")
            console.print()

        render_report(report)

        # Update DB
        db_status = update_db(report)
        console.print(
            f"  [dim]DB updated: form20_score={report['difficulty']}, "
            f"form20_status={db_status}[/]"
        )

        report_path = args.output_dir / args.state_code / str(args.ac_no) / "report.json"
        console.print(f"  Full report: [blue]{report_path}[/]")
        console.print()
    else:
        # State-level batch mode
        run_state_batch(args.state_code, args)


if __name__ == "__main__":
    main()
