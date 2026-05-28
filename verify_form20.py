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


def _print_review_summary(
    state_code: str,
    mismatch_reports: list[dict],
    error_acs: list[dict],
    counts: dict,
    already_verified: int,
    total_acs: int,
    elapsed: float,
) -> None:
    """Print and save a detailed review summary for MISMATCH and ERROR ACs.

    Saves to {project_root}/form20-review-{state_code}.md
    """
    import datetime
    from pathlib import Path

    lines: list[str] = []
    lines.append(f"# Form 20 Review — {state_code}")
    lines.append(f"")
    lines.append(f"Generated: {datetime.datetime.now().isoformat(timespec='seconds')}")
    lines.append(f"ACs: {already_verified + sum(counts.values()) - already_verified}/{total_acs} processed")
    lines.append(f"Time: {elapsed:.0f}s")
    lines.append(f"")

    # Stats
    parts = []
    for s in ("VERIFIED", "MISMATCH", "ERROR"):
        c = counts.get(s, 0)
        if c:
            parts.append(f"{c} {s}")
    lines.append(f"**Results:** {' | '.join(parts)}")
    lines.append(f"")

    # MISMATCH details
    if mismatch_reports:
        lines.append(f"## MISMATCH — Manual Verification Needed ({len(mismatch_reports)})")
        lines.append(f"")
        for report in mismatch_reports:
            ac_no = report["ac_no"]
            ac_name = report.get("ac_name", "?")
            difficulty = report.get("difficulty", "?")
            summary = report.get("summary", {})
            reconciled = report.get("reconciled", [])
            url = report.get("form20_url", "")

            lines.append(f"### AC {ac_no} — {ac_name} (difficulty {difficulty})")
            if url:
                lines.append(f"PDF: {url}")
            lines.append(f"")

            # Candidate table (only verified candidates — top 3)
            lines.append(f"| Candidate | Party | ECI | Form 20 | Delta | Notes |")
            lines.append(f"|-----------|-------|----:|--------:|------:|-------|")
            for r in reconciled:
                if r.get("form20_votes") is None:
                    continue  # skip unverified candidates
                candidate = r.get("candidate", "?")
                party = r.get("party_abv", "?")
                eci = r.get("eci_votes", "?")
                f20 = r.get("form20_votes", "—")
                delta = r.get("delta")
                delta_str = f"{delta:+d}" if delta is not None else "—"
                notes = r.get("notes", "") or ""
                lines.append(f"| {candidate} | {party} | {eci} | {f20} | {delta_str} | {notes} |")
            lines.append(f"")

        # SQL helper
        lines.append(f"## SQL — Mark as VERIFIED after manual review")
        lines.append(f"")
        lines.append(f"```sql")
        for report in mismatch_reports:
            ac_no = report["ac_no"]
            lines.append(
                f"UPDATE constituency_status SET form20_status='VERIFIED' "
                f"WHERE state_code='{state_code}' AND ac_no={ac_no};"
            )
        lines.append(f"```")
        lines.append(f"")

    # ERROR details
    if error_acs:
        lines.append(f"## ERROR — Pipeline Failed ({len(error_acs)})")
        lines.append(f"")
        lines.append(f"| AC | Name | Difficulty | Confirmed |")
        lines.append(f"|----|------|-----------|-----------|")
        for ac in error_acs:
            d = ac.get("difficulty", "?")
            c = ac.get("confirmed", "?")
            lines.append(f"| {ac['ac_no']} | {ac['ac_name']} | {d} | {c} |")
        lines.append(f"")

    # Write file
    content = "\n".join(lines)
    review_path = Path(__file__).resolve().parent / f"form20-review-{state_code}.md"
    review_path.write_text(content)

    # Print to terminal
    console.print()
    console.print(f"  [bold]Review summary saved to:[/] {review_path.name}")
    console.print()
    if mismatch_reports:
        console.print(f"  [red]MISMATCH ACs requiring manual review:[/]")
        for report in mismatch_reports:
            ac_no = report["ac_no"]
            ac_name = report.get("ac_name", "?")
            reconciled = report.get("reconciled", [])
            mismatches = [r for r in reconciled if r.get("delta") and r["delta"] != 0]
            top = sorted(mismatches, key=lambda r: abs(r.get("delta", 0)), reverse=True)[:3]
            detail = ", ".join(
                f"{r['party_abv']} {r.get('delta', 0):+d}" for r in top
            )
            console.print(f"    [red]AC {ac_no:>3}[/] {ac_name}  →  {detail}")
        console.print()
    if error_acs:
        console.print(f"  [yellow]ERROR ACs (pipeline failed):[/]")
        for ac in error_acs:
            console.print(f"    [yellow]AC {ac['ac_no']:>3}[/] {ac['ac_name']}")
        console.print()


def run_state_batch(state_code: str, args) -> None:
    """Process all ACs in a state that have form20_urls.

    Shows status for EVERY AC as it processes. VERIFIED ACs are skipped
    instantly (read from DB), giving a speed impression. UNVERIFIED/MISMATCH/ERROR
    ACs go through the full Vision pipeline.
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

    # Get current statuses for all ACs
    total_acs = len(acs)
    current = get_current_statuses(state_code) if not args.force else {}

    console.print()
    console.print(
        Panel(
            f"[bold]State: {state_code}[/bold] — {total_acs} ACs total"
            + (" — processing all ACs" if args.force else ""),
            title="Form 20 Batch Verification",
            border_style="blue",
        )
    )
    console.print()

    counts = {"VERIFIED": 0, "MISMATCH": 0, "ERROR": 0}
    start_time = time.time()

    # Legend
    console.print()
    console.print("  [green]●[/] VERIFIED  [red]●[/] MISMATCH  [blue]●[/] ERROR")
    console.print()

    # Collect reports for summary
    mismatch_reports: list[dict] = []
    error_acs: list[dict] = []
    already_verified = 0

    # Process ALL ACs — one line each, starting from AC 1
    for i, ac in enumerate(acs):
        ac_no = ac["ac_no"]
        ac_name = ac["ac_name"]
        current_status = current.get(ac_no)

        # Skip VERIFIED unless --force
        if current_status == "VERIFIED" and not args.force:
            status_key = "VERIFIED"
            counts["VERIFIED"] += 1
            already_verified += 1
            color = "green"
            symbol = "●"
        else:
            report = _process_one_ac(state_code, ac, args)

            if report is None:
                status_key = "ERROR"
                counts["ERROR"] += 1
                color = "red"
                symbol = "✖"
                error_acs.append({"ac_no": ac_no, "ac_name": ac_name})
            else:
                status = report["_db_status"]
                status_key = status
                color = {"VERIFIED": "green", "MISMATCH": "red", "ERROR": "blue"}.get(status, "dim")
                symbol = {"VERIFIED": "●", "MISMATCH": "!", "ERROR": "✖"}.get(status, "?")
                counts[status] = counts.get(status, 0) + 1
                if status == "MISMATCH":
                    mismatch_reports.append(report)
                elif status == "ERROR":
                    error_acs.append({"ac_no": ac_no, "ac_name": ac_name,
                                      "difficulty": report.get("difficulty"),
                                      "confirmed": report.get("summary", {}).get("confirmed", 0)})

        console.print(f"  [bold {color}]{symbol}[/]  {ac_no:>3} {ac_name}")

    elapsed = time.time() - start_time

    # Summary
    console.print()
    console.print(
        Panel(
            f"[bold]{state_code}[/bold] — {sum(counts.values())}/{total_acs} ACs in {elapsed:.0f}s"
            + (f" ({elapsed / max(total_acs, 1):.1f}s/AC avg)" if total_acs else ""),
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

    # --- Detailed summary for manual review ---
    if mismatch_reports or error_acs:
        _print_review_summary(state_code, mismatch_reports, error_acs, counts,
                              already_verified, total_acs, elapsed)


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
