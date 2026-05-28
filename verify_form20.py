#!/usr/bin/env python3
"""
Form 20 Verification CLI

Download a Form 20 PDF, run dual OCR (Tesseract + Vision LLM),
compare against ECI results, and present a human-readable report.

Usage:
    python verify_form20.py <state_code> [ac_no] [--force] [--skip-vision] [--workers N]

Examples:
    # Verify a single AC:
    python verify_form20.py S25 110
    python verify_form20.py S25 275 --force

    # Verify ALL ACs in a state (updates DB):
    python verify_form20.py S25

    # With 4 parallel workers:
    python verify_form20.py S25 --workers 4

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
from rich.progress import (
    Progress, SpinnerColumn, TextColumn, BarColumn,
    TaskProgressColumn, MofNCompleteColumn, TimeElapsedColumn,
)

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
    from ocr_engine import run_pipeline

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
    from ocr_engine import update_form20_result

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


def _process_one_ac(state_code: str, ac: dict, args, progress, task) -> dict | None:
    """Process a single AC: run pipeline, update DB, update progress bar.

    Designed to be called from a worker thread. Returns report dict or None.
    """
    ac_no = ac["ac_no"]
    ac_name = ac["ac_name"]

    report = run_single(state_code, ac_no, args)
    if report is None:
        progress.update(task, advance=1, description=f"AC {ac_no} {ac_name} — FAILED")
        return None

    # Update DB
    db_status = update_db(report)
    report["_db_status"] = db_status

    # Build progress description
    s = report["summary"]
    if db_status == "ERROR":
        desc = f"AC {ac_no} {ac_name} — ERROR (will retry next run)"
    elif db_status == "MISMATCH":
        desc = f"AC {ac_no} {ac_name} — MISMATCH ({s['mismatched']} discrepancies)"
    elif db_status == "VERIFIED":
        desc = f"AC {ac_no} {ac_name} — VERIFIED ({s['confirmed']}/{s['total']})"
    else:
        desc = f"AC {ac_no} {ac_name} — {db_status}"

    progress.update(task, advance=1, description=desc)
    return report


def run_state_batch(state_code: str, args) -> None:
    """Process all ACs in a state that have form20_urls.

    Without --force: skips ACs already VERIFIED.
    With --force: re-processes everything.
    Uses --workers N for parallel processing (default: 1).
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from ocr_engine import get_state_acs_with_form20

    acs = get_state_acs_with_form20(state_code)
    if not acs:
        console.print(f"  [red]No ACs with form20_url found for {state_code}[/]")
        console.print(
            "  [dim]Set URLs first: UPDATE constituency_status SET form20_url='...' "
            f"WHERE state_code='{state_code}' AND ac_no=N;[/]"
        )
        return

    # Determine which ACs need processing
    current = get_current_statuses(state_code) if not args.force else {}
    skip_count = 0
    if not args.force:
        original_count = len(acs)
        acs = [ac for ac in acs if current.get(ac["ac_no"]) != "VERIFIED"]
        skip_count = original_count - len(acs)

    workers = args.workers
    console.print()
    console.print(
        Panel(
            f"[bold]State: {state_code}[/bold] — {len(acs)} ACs to process"
            + (f" ({skip_count} VERIFIED skipped)" if skip_count else "")
            + f" — {workers} worker{'s' if workers > 1 else ''}",
            title="Form 20 Batch Verification",
            border_style="blue",
        )
    )
    console.print()

    results = []
    start_time = time.time()

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Verifying ACs…", total=len(acs))

        if workers <= 1:
            # Sequential — original behavior
            for ac in acs:
                report = _process_one_ac(state_code, ac, args, progress, task)
                if report:
                    results.append(report)
        else:
            # Parallel — ThreadPoolExecutor
            # Use a lock for thread-safe progress + results list
            import threading
            lock = threading.Lock()

            def worker(ac):
                report = _process_one_ac(state_code, ac, args, progress, task)
                if report:
                    with lock:
                        results.append(report)

            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {executor.submit(worker, ac): ac for ac in acs}
                for future in as_completed(futures):
                    # Surface exceptions from workers
                    exc = future.exception()
                    if exc:
                        ac = futures[future]
                        console.print(
                            f"  [red]Worker exception for AC {ac['ac_no']}: {exc}[/]"
                        )

    elapsed = time.time() - start_time

    # Summary table
    console.print()
    console.print(
        Panel(
            f"[bold]{state_code}[/bold] — {len(results)}/{len(acs)} ACs verified in {elapsed:.0f}s"
            + (f" ({workers}x speedup)" if workers > 1 else ""),
            title="Batch Summary",
            border_style="green",
        )
    )

    if results:
        # Sort by AC number for stable output
        results.sort(key=lambda r: r["ac_no"])

        table = Table(show_header=True, header_style="bold", box=None, padding=(0, 1))
        table.add_column("AC", width=6, justify="right")
        table.add_column("Name", min_width=20)
        table.add_column("Score", width=6, justify="right")
        table.add_column("Status", width=12)
        table.add_column("Confirmed", width=10, justify="right")
        table.add_column("Mismatched", width=10, justify="right")

        for r in results:
            s = r["summary"]
            db_st = r.get("_db_status", r["difficulty_label"])
            if db_st == "MISMATCH":
                status_text = "[red]MISMATCH[/]"
            elif db_st == "ERROR":
                status_text = "[yellow]ERROR[/]"
            elif db_st == "VERIFIED":
                status_text = "[green]VERIFIED[/]"
            else:
                status_text = f"[dim]{db_st}[/]"

            table.add_row(
                str(r["ac_no"]),
                r.get("ac_name", "—"),
                str(r["difficulty"]),
                status_text,
                f"[green]{s['confirmed']}[/]",
                f"[red]{s['mismatched']}[/]" if s["mismatched"] else "[dim]0[/]",
            )

        console.print(table)

    # Aggregate stats
    total_verified = sum(1 for r in results if r.get("_db_status") == "VERIFIED")
    total_mismatch = sum(1 for r in results if r.get("_db_status") == "MISMATCH")
    total_error = sum(1 for r in results if r.get("_db_status") == "ERROR")
    console.print()
    console.print(
        f"  [green]{total_verified} verified[/] | "
        f"[red]{total_mismatch} mismatched[/] | "
        f"[yellow]{total_error} errors[/]"
    )
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
        "--workers", "-j",
        type=int, default=1,
        help="Number of parallel workers for batch mode (default: 1)",
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
        from ocr_engine import get_form20_url, get_eci_results, get_constituency_info, pdf_to_images, build_confirm_prompt

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
            from ocr_engine import _download_pdf
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
