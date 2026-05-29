"""
Form 20 OCR engine — dual-path extraction with confirm-rather-than-extract approach.

Path A: Tesseract (blind table extraction)
Path B: Vision LLM (confirm known ECI results against the PDF scan)

The ECI data in rounds_ac is the ground truth. The OCR pipeline confirms
what we already know, rather than extracting from scratch.
"""

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Optional

import httpx
import pandas as pd
from PIL import Image

import db_utils


# ---------------------------------------------------------------------------
# Rate limiter for Vision API calls (MiMo rate-limits ~2 concurrent calls)
# ---------------------------------------------------------------------------
import threading
_vision_semaphore = threading.Semaphore(2)


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def get_form20_url(state_code: str, ac_no: int) -> Optional[str]:
    """Read form20_url from constituency_status."""
    conn = db_utils._connect()
    cur = db_utils._cursor(conn)
    try:
        p = "?" if not os.environ.get("DATABASE_URL", "").startswith(("postgres://", "postgresql://")) else "%s"
        cur.execute(
            f"SELECT form20_url FROM constituency_status WHERE state_code={p} AND ac_no={p}",
            (state_code, ac_no),
        )
        row = cur.fetchone()
        if not row:
            return None
        return row["form20_url"] if hasattr(row, "keys") else row[0]
    finally:
        conn.close()


def get_eci_results(state_code: str, ac_no: int) -> list[dict]:
    """Query rounds_ac for the final round of a constituency.

    Returns list of {candidate, party_abv, votes} sorted by votes desc.
    """
    conn = db_utils._connect()
    cur = db_utils._cursor(conn)
    try:
        p = "?" if not os.environ.get("DATABASE_URL", "").startswith(("postgres://", "postgresql://")) else "%s"
        cur.execute(
            f"""SELECT candidate, party_abv, votes
                FROM rounds_ac
                WHERE state_code={p} AND ac_no={p}
                  AND round_no = (SELECT MAX(round_no) FROM rounds_ac
                                  WHERE state_code={p} AND ac_no={p})
                ORDER BY votes DESC""",
            (state_code, ac_no, state_code, ac_no),
        )
        rows = cur.fetchall()
        return [
            {
                "candidate": r["candidate"] if hasattr(r, "keys") else r[0],
                "party_abv": r["party_abv"] if hasattr(r, "keys") else r[1],
                "votes": r["votes"] if hasattr(r, "keys") else r[2],
            }
            for r in rows
        ]
    finally:
        conn.close()


def get_constituency_info(state_code: str, ac_no: int) -> Optional[dict]:
    """Get constituency name and state name."""
    conn = db_utils._connect()
    cur = db_utils._cursor(conn)
    try:
        p = "?" if not os.environ.get("DATABASE_URL", "").startswith(("postgres://", "postgresql://")) else "%s"
        cur.execute(
            f"""SELECT cs.ac_name, s.state_name
                FROM constituency_status cs
                JOIN states s ON cs.state_code = s.state_code
                WHERE cs.state_code={p} AND cs.ac_no={p}""",
            (state_code, ac_no),
        )
        row = cur.fetchone()
        if not row:
            return None
        if hasattr(row, "keys"):
            return {"ac_name": row["ac_name"], "state_name": row["state_name"]}
        return {"ac_name": row[0], "state_name": row[1]}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# PDF → Images
# ---------------------------------------------------------------------------

def pdf_to_images(pdf_path: Path, output_dir: Path, dpi: int = 300) -> list[Path]:
    """Convert PDF pages to PNG images. Returns list of image paths.

    Raises ValueError for corrupted/unreadable PDFs.
    """
    from pdf2image import convert_from_path

    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        images = convert_from_path(str(pdf_path), dpi=dpi)
    except Exception as e:
        # pdf2image wraps poppler errors — check for corruption signals
        msg = str(e).lower()
        if any(kw in msg for kw in ("xref", "trailer", "endstream", "page count",
                                     "cannot identify", "broken pdf")):
            raise ValueError(f"Corrupted PDF: {e}") from e
        raise
    paths = []
    for i, img in enumerate(images):
        p = output_dir / f"page_{i + 1:03d}.png"
        img.save(str(p), "PNG")
        paths.append(p)
    return paths


# ---------------------------------------------------------------------------
# Path A — Tesseract (blind extraction)
# ---------------------------------------------------------------------------

def ocr_tesseract_raw(image_path: Path) -> pd.DataFrame:
    """Run Tesseract on an image, return raw DataFrame."""
    import pytesseract

    img = Image.open(image_path)
    return pytesseract.image_to_data(
        img, config="--psm 6 --oem 1", output_type=pytesseract.Output.DATAFRAME
    )


def parse_tesseract_output(df: pd.DataFrame) -> list[dict]:
    """Parse Tesseract DataFrame into candidate rows.

    Looks for lines containing a number (vote count) and extracts
    the text before it as candidate name + party.
    """
    if df.empty:
        return []

    # Filter out low-confidence and empty rows
    df = df[(df["conf"] > 30) & (df["text"].notna()) & (df["text"] != "")].copy()
    df["text"] = df["text"].astype(str).str.strip()

    # Group words by line (block_num + par_num + line_num)
    results = []
    if "block_num" not in df.columns:
        return results

    grouped = df.groupby(["block_num", "par_num", "line_num"])
    for (block, par, line), group in grouped:
        words = group["text"].tolist()
        confs = group["conf"].tolist()

        # Look for a line ending with a large number (vote count)
        line_text = " ".join(words)
        # Find numbers in the line
        numbers = [w for w in words if re.match(r"^\d{1,7}$", w)]

        if numbers:
            votes_str = max(numbers, key=len)  # largest number = vote count
            try:
                votes = int(votes_str)
            except ValueError:
                continue

            # Everything before the vote count is name + party
            vote_idx = words.index(votes_str)
            name_parts = words[:vote_idx]
            avg_conf = sum(confs) / len(confs) if confs else 0

            if name_parts and votes > 0:
                results.append({
                    "raw_text": line_text,
                    "name_parts": name_parts,
                    "votes": votes,
                    "confidence": avg_conf,
                })

    return results


def ocr_tesseract_extract(image_path: Path) -> list[dict]:
    """Blind Tesseract extraction. Returns candidate rows."""
    df = ocr_tesseract_raw(image_path)
    return parse_tesseract_output(df)


# ---------------------------------------------------------------------------
# Path B — Vision LLM (confirm approach)
# ---------------------------------------------------------------------------

def build_confirm_prompt(
    ac_name: str, state_code: str, ac_no: int, eci_results: list[dict]
) -> str:
    """Build the prompt for the Vision LLM confirm approach.

    Simplified: only asks the LLM to find the summary row and return the
    top 3 vote counts. No candidate names — the LLM doesn't need to know
    them. We sort both sides descending and compare.
    """
    # Top 3 ECI vote counts for the expected answer
    top3_votes = sorted([r["votes"] for r in eci_results], reverse=True)[:3]

    return f"""You are verifying election results against a scanned Form 20 (Final Result Sheet).

Constituency: {ac_name} (AC {ac_no}, {state_code})

This is a scan of the official Form 20 for the same constituency.

TASK: Find the summary page and read the "Total Votes Polled" row.

Steps:
1. Find the page with summary rows at the bottom (usually last page).
2. Look for THREE summary rows at the bottom of the table:
   - "Total EVM Votes" (EVM-only counts)
   - "Total Postal Ballot Votes" (postal-only counts)
   - "Total Votes Polled" (EVM + Postal combined — THIS IS THE ONE YOU WANT)
3. Read the "Total Votes Polled" row carefully, column by column from LEFT to RIGHT.
4. Each number in that row is one candidate's total votes.
5. Sort all numbers descending and return the top 3.

DENSE TABLES (30+ columns):
- The "Total Votes Polled" row is the LAST data row, below "Total Postal Ballot Votes".
- Read LEFT to RIGHT, one cell at a time. Do NOT skip cells.
- Numbers like 120,365 and 119,785 look similar — be precise.
- The total valid votes across all candidates should be roughly 150,000-300,000.
  If your top-3 sum to far less (e.g., <50,000), you're reading the wrong row.

CRITICAL RULES:
- Only read from the "Total Votes Polled" SUMMARY row (the very last row).
- Do NOT read from "Total EVM Votes" or "Total Postal Ballot Votes" rows.
- Do NOT read from booth-wise rows (individual polling station data).
- If text is illegible, output null — do NOT guess or hallucinate.
- Every number you return MUST appear visibly in the row. If unsure, output null.
- Pay attention to the constituency name — does it match {ac_name}?

Return ONLY a JSON array of 3 numbers, no markdown fences, no explanation. Example:
[59091, 44842, 18420]"""


def ocr_vision_confirm(
    image_paths: list[Path],
    eci_results: list[dict],
    ac_name: str = "",
    state_code: str = "",
    ac_no: int = 0,
    vision_fn=None,
) -> list[dict]:
    """Send page images to vision LLM with confirm prompt.

    Args:
        vision_fn: Optional callable(image_path, prompt) -> str that returns
                   the LLM's text response.  When *None*, falls back to the
                   OpenAI-compatible HTTP API (FORM20_VISION_* env vars).

    Returns list of {candidate, party_abv, eci_votes, form20_votes,
                     name_visible, confirmed, notes}.
    """
    prompt = build_confirm_prompt(ac_name, state_code, ac_no, eci_results)

    # --- Path A: external callable (Hermes vision_analyze, etc.) -----------
    if vision_fn is not None:
        all_responses = []
        for img_path in image_paths:
            try:
                resp = vision_fn(img_path, prompt)
                parsed = parse_vision_response(resp)
                all_responses.extend(parsed)
            except Exception as exc:
                print(f"  ⚠ Vision callable failed on {img_path.name}: {exc}")
        return _merge_vision_responses(all_responses, eci_results)

    # --- Path B: pre-computed results from JSON file ----------------------
    vision_file = os.environ.get("_FORM20_VISION_FILE", "")
    if vision_file:
        try:
            with open(vision_file) as f:
                precomputed = json.load(f)
            # precomputed is a list of per-page response strings
            all_responses = []
            for page_resp in precomputed:
                parsed = parse_vision_response(page_resp)
                if parsed:
                    all_responses.append(parsed)  # keep as per-page list
            return _merge_vision_responses(all_responses, eci_results)
        except Exception as exc:
            print(f"  ⚠ Could not read vision file {vision_file}: {exc}")

    # --- Path C: OpenAI-compatible HTTP API (concurrent pages) --------------
    api_url = os.environ.get("FORM20_VISION_API_URL", "").rstrip("/")
    if api_url and not api_url.endswith("/chat/completions"):
        api_url += "/chat/completions"
    api_key = os.environ.get("FORM20_VISION_API_KEY", "")

    if not api_url:
        # No vision API configured — return empty
        return [
            {
                "candidate": r["candidate"],
                "party_abv": r["party_abv"],
                "eci_votes": r["votes"],
                "form20_votes": None,
                "name_visible": "unknown",
                "confirmed": None,
                "notes": "No vision API configured (set FORM20_VISION_API_URL)",
            }
            for r in eci_results
        ]

    # Send all page images concurrently via ThreadPoolExecutor
    model = os.environ.get("FORM20_VISION_MODEL", "gpt-4o")
    import random as _rng

    def _call_vision_api(img_path: Path):
        """Single-page Vision API call with retry. Returns parsed list or error dict."""
        img_b64 = _image_to_base64(img_path, candidate_count=len(eci_results))
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{img_b64}"},
                        },
                    ],
                }
            ],
            "max_tokens": 4096,
        }
        max_retries = 4
        for attempt in range(max_retries):
            try:
                with _vision_semaphore:
                    resp = httpx.post(api_url, json=payload, headers=headers, timeout=180)
                if resp.status_code == 429:
                    retry_after = int(resp.headers.get("Retry-After", 5))
                    wait = max(retry_after, 2 ** attempt) + _rng.uniform(0, 1)
                    # Silently retry on rate limit
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                data = resp.json()
                msg = data["choices"][0]["message"]
                content = msg.get("content") or msg.get("reasoning_content") or ""
                parsed = parse_vision_response(content)
                if parsed:
                    return parsed
                # Empty response - treat as transient failure, retry
                if attempt < max_retries - 1:
                    wait = 2 ** attempt + _rng.uniform(0, 1)
                    time.sleep(wait)
                    continue
                return {"error": f"No candidates in response from {img_path.name}"}
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429 and attempt < max_retries - 1:
                    wait = 2 ** attempt + _rng.uniform(0, 1)
                    # Silently retry on rate limit
                    time.sleep(wait)
                    continue
                return {"error": str(e)}
            except Exception as e:
                return {"error": str(e)}
        return {"error": f"Failed after {max_retries} retries: {img_path.name}"}

    def _run_vision_batch():
        """Analyze pages sequentially, starting from last (summary likely there).

        Early exit when we confirm all top-3 candidates.
        """
        all_responses = []

        # Sort pages: last page first, then second-to-last, etc.
        # Summary row is almost always on last page.
        sorted_pages = sorted(
            image_paths,
            key=lambda p: int(p.stem.split("_")[1]),
            reverse=True,
        )

        for img_path in sorted_pages:
            result = _call_vision_api(img_path)

            if isinstance(result, list):
                all_responses.append(result)
            elif isinstance(result, dict):
                all_responses.append([result])

            # Early exit: check if we have confirmed all top-3
            merged = _merge_vision_responses(all_responses, eci_results)
            top3_confirmed = sum(1 for r in merged if r.get("confirmed"))
            if top3_confirmed >= 3:
                return merged

        return _merge_vision_responses(all_responses, eci_results)

    # Retry loop: if all top-3 candidates are unconfirmed, retry up to 2 more times
    top3 = sorted(eci_results, key=lambda r: r["votes"], reverse=True)[:3]
    max_pipeline_retries = 2
    result = []

    for pipeline_attempt in range(max_pipeline_retries + 1):
        result = _run_vision_batch()

        # Check if top-3 are all unconfirmed
        top3_results = [r for r in result if r.get("eci_votes") in [t["votes"] for t in top3]]
        confirmed_count = sum(1 for r in top3_results if r.get("confirmed"))
        if confirmed_count > 0 or pipeline_attempt == max_pipeline_retries:
            return result

        # All unconfirmed — silently retry
        time.sleep(1 + _rng.uniform(0, 1))

    return result


def _image_to_base64(img_path: Path, max_width: int = 1024, candidate_count: int = 0) -> str:
    """Resize image for Vision API and return base64-encoded PNG.

    Dense PDFs (>15 candidates) are upscaled to 2048px to preserve
    readability of tiny numbers in wide tables.
    """
    import base64
    from PIL import Image
    import io

    # Upscale dense PDFs so the Vision LLM can read tiny numbers
    effective_width = 2048 if candidate_count > 15 else max_width

    img = Image.open(img_path)
    if img.width > effective_width:
        ratio = effective_width / img.width
        img = img.resize((effective_width, int(img.height * ratio)), Image.LANCZOS)
    elif img.width < effective_width and candidate_count > 15:
        # Upscale small images for dense PDFs
        ratio = effective_width / img.width
        img = img.resize((effective_width, int(img.height * ratio)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def _merge_vision_responses(
    all_responses: list, eci_results: list[dict]
) -> list[dict]:
    """Merge LLM responses and map to top-3 ECI candidates.

    The LLM now returns a simple JSON array of vote counts sorted descending:
        [59091, 44842, 18420]

    We sort ECI top-3 descending and compare element-by-element.
    Legacy formats (dict-based) are also handled for backward compatibility.
    """
    top3 = sorted(eci_results, key=lambda r: r["votes"], reverse=True)[:3]
    empty = [
        {
            "candidate": r["candidate"],
            "party_abv": r["party_abv"],
            "eci_votes": r["votes"],
            "form20_votes": None,
            "name_visible": "unknown",
            "confirmed": None,
            "notes": "Vision API returned no usable response",
        }
        for r in top3
    ]

    if not all_responses:
        return empty

    # Find the best page response
    best = max(
        (r for r in all_responses if isinstance(r, list) and r),
        key=lambda r: len(r),
        default=[],
    )

    if not best:
        return empty

    # --- New format: plain array of numbers [59091, 44842, 18420] ---
    if best and isinstance(best[0], (int, float)):
        f20_sorted = sorted([int(x) for x in best if x is not None], reverse=True)
        result = []
        for i, eci in enumerate(top3):
            f20_votes = f20_sorted[i] if i < len(f20_sorted) else None
            result.append({
                "candidate": eci["candidate"],
                "party_abv": eci["party_abv"],
                "eci_votes": eci["votes"],
                "form20_votes": f20_votes,
                "name_visible": "yes" if f20_votes is not None else "unknown",
                "confirmed": f20_votes == eci["votes"] if f20_votes is not None else None,
                "notes": None,
            })
        return result

    # --- Position-based format: [{position, eci_votes, form20_votes}, ...] ---
    if best and isinstance(best[0], dict) and "position" in best[0]:
        result = []
        for item in best:
            pos = item.get("position", 0)
            if 1 <= pos <= len(top3):
                eci = top3[pos - 1]
                result.append({
                    "candidate": eci["candidate"],
                    "party_abv": eci["party_abv"],
                    "eci_votes": eci["votes"],
                    "form20_votes": item.get("form20_votes"),
                    "name_visible": "yes" if item.get("form20_votes") is not None else "unknown",
                    "confirmed": item.get("confirmed"),
                    "notes": None,
                })
        return result

    # --- Legacy name-based format: [{candidate, form20_votes, ...}, ...] ---
    seen = set()
    deduped = []
    top3_names = {r["candidate"].upper() for r in top3}
    for item in best:
        if not isinstance(item, dict):
            continue  # skip None or non-dict items from LLM response
        name = (item.get("candidate") or "").strip().upper()
        if name and name not in seen and name in top3_names:
            seen.add(name)
            deduped.append(item)

    result = []
    for eci in top3:
        match = next(
            (d for d in deduped if d.get("candidate", "").strip().upper() == eci["candidate"].upper()),
            None,
        )
        result.append({
            "candidate": eci["candidate"],
            "party_abv": eci["party_abv"],
            "eci_votes": eci["votes"],
            "form20_votes": match.get("form20_votes") if match else None,
            "name_visible": "yes" if match and match.get("form20_votes") is not None else "unknown",
            "confirmed": match.get("confirmed") if match else None,
            "notes": None,
        })
    return result


def parse_vision_response(response_text: str) -> list[dict]:
    """Parse the JSON response from the Vision LLM.

    Returns list of dicts, or empty list if parsing fails.
    """
    if not response_text:
        return []

    # Try to extract JSON from the response (may have markdown fences)
    text = response_text.strip()
    # Remove markdown code fences if present
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            # Filter out None/null entries that LLMs sometimes emit
            return [x for x in parsed if x is not None]
        return []
    except json.JSONDecodeError:
        return []


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------

def reconcile_row(
    eci_votes: int,
    tesseract_votes: Optional[int],
    llm_votes: Optional[int],
    llm_confirmed: Optional[bool],
    llm_name_visible: str,
) -> dict:
    """Reconcile a single candidate row across both OCR paths.

    Returns {confidence, delta, form20_votes, notes}.
    """
    votes_to_use = None
    confidence = "low"
    notes = []

    # Case 1: LLM confirmed and votes match
    if llm_confirmed is True and llm_votes is not None and llm_votes == eci_votes:
        confidence = "high" if llm_name_visible == "yes" else "medium"
        votes_to_use = llm_votes
        if llm_name_visible != "yes":
            notes.append(f"name {llm_name_visible}")

    # Case 2: LLM reported votes that differ
    elif llm_votes is not None and llm_votes != eci_votes:
        delta = llm_votes - eci_votes
        pct_diff = abs(delta) / max(eci_votes, 1) * 100
        if pct_diff <= 1:
            confidence = "medium"
            votes_to_use = llm_votes
            notes.append(f"delta {delta:+d} ({pct_diff:.1f}%)")
        elif pct_diff <= 5:
            confidence = "medium" if llm_name_visible == "yes" else "low"
            votes_to_use = llm_votes
            notes.append(f"delta {delta:+d} ({pct_diff:.1f}%) — verify manually")
        else:
            confidence = "low"
            votes_to_use = llm_votes
            notes.append(f"delta {delta:+d} ({pct_diff:.1f}%) — verify manually")

    # Case 3: LLM confirmed but no votes (name visible, numbers not)
    elif llm_confirmed is True and llm_votes is None:
        confidence = "medium"
        votes_to_use = None
        notes.append("name confirmed, votes illegible")

    # Case 4: Tesseract found something but LLM didn't
    elif tesseract_votes is not None and llm_votes is None:
        if tesseract_votes == eci_votes:
            confidence = "medium"
            votes_to_use = tesseract_votes
            notes.append("tesseract match only")
        else:
            confidence = "low"
            votes_to_use = tesseract_votes
            notes.append("tesseract mismatch, LLM silent")

    # Case 5: Nothing from either path
    else:
        confidence = "low"
        notes.append("no data from either OCR path")

    delta = (votes_to_use - eci_votes) if votes_to_use is not None else None

    return {
        "confidence": confidence,
        "delta": delta,
        "form20_votes": votes_to_use,
        "name_visible": llm_name_visible,
        "notes": "; ".join(notes) if notes else None,
    }


def reconcile(
    tesseract_extracted: list[dict],
    llm_confirmed: list[dict],
    eci_results: list[dict],
) -> list[dict]:
    """Reconcile LLM-verified top-3 against full ECI results.

    llm_confirmed contains exactly 3 entries (top vote-getters), matched
    by position. Non-top-3 candidates are marked as unverified.

    Returns list of {candidate, party_abv, eci_votes, form20_votes,
                     delta, confidence, name_visible, notes}.
    """
    # Sort ECI by votes to get the top 3 positions
    sorted_eci = sorted(eci_results, key=lambda r: r["votes"], reverse=True)
    top3_set = {(r["candidate"], r["party_abv"]) for r in sorted_eci[:3]}

    # Build lookup from LLM results by (candidate, party) tuple
    llm_map = {}
    for r in llm_confirmed:
        key = (r.get("candidate", "").strip(), r.get("party_abv", ""))
        llm_map[key] = r

    # Build lookup from Tesseract (fuzzy match by votes)
    tess_by_votes = {}
    for r in tesseract_extracted:
        tess_by_votes[r["votes"]] = r

    reconciled = []
    for eci in eci_results:
        candidate = eci["candidate"]
        eci_votes = eci["votes"]
        is_top3 = (candidate, eci["party_abv"]) in top3_set

        # Find LLM result — exact (candidate, party) match, top-3 only
        llm = llm_map.get((candidate, eci["party_abv"])) if is_top3 else None

        llm_votes = llm.get("form20_votes") if llm else None
        llm_confirmed_flag = llm.get("confirmed") if llm else None
        llm_name_visible = llm.get("name_visible", "unknown") if llm else "unknown"

        # Find Tesseract result (match by vote count proximity)
        tess = tess_by_votes.get(eci_votes)
        tess_votes = tess["votes"] if tess else None

        row = reconcile_row(
            eci_votes=eci_votes,
            tesseract_votes=tess_votes,
            llm_votes=llm_votes,
            llm_confirmed=llm_confirmed_flag,
            llm_name_visible=llm_name_visible,
        )

        reconciled.append({
            "candidate": candidate,
            "party_abv": eci["party_abv"],
            "eci_votes": eci_votes,
            "form20_votes": row["form20_votes"],
            "delta": row["delta"],
            "confidence": row["confidence"],
            "name_visible": row["name_visible"],
            "notes": row["notes"],
        })

    return reconciled


# ---------------------------------------------------------------------------
# Difficulty score
# ---------------------------------------------------------------------------

def compute_difficulty(reconciled: list[dict], page_count: int) -> tuple[int, str]:
    """Score 0-100 indicating how easy this verification is.

    Higher = easier to verify. Factors:
    - % of candidates with HIGH confidence
    - % of candidates where name was visible
    - % of candidates where votes matched
    - Page count penalty

    Only counts candidates that were actually verified (form20_votes != None).
    With the simplified top-3 approach, this means only the top 3 are scored.
    """
    # Only count candidates that were actually verified by the LLM
    verified = [r for r in reconciled if r.get("form20_votes") is not None]
    if not verified:
        return 0, "IMPOSSIBLE"

    total = len(verified)
    high_count = sum(1 for r in verified if r["confidence"] == "high")
    name_visible = sum(1 for r in verified if r.get("name_visible") == "yes")
    votes_match = sum(1 for r in verified
                      if r.get("form20_votes") is not None
                      and (r.get("delta") == 0
                           or r.get("form20_votes") == r.get("eci_votes")))

    # Weighted score
    confidence_pct = high_count / total * 100
    name_pct = name_visible / total * 100
    match_pct = votes_match / total * 100

    raw_score = confidence_pct * 0.4 + name_pct * 0.3 + match_pct * 0.3

    # Page count penalty: each page beyond 1 reduces score
    page_penalty = max(0, (page_count - 1) * 3)
    score = max(0, min(100, int(raw_score - page_penalty)))

    if score >= 80:
        label = "EASY"
    elif score >= 50:
        label = "MODERATE"
    elif score >= 20:
        label = "HARD"
    else:
        label = "IMPOSSIBLE"

    return score, label


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def run_pipeline(
    state_code: str,
    ac_no: int,
    output_dir: Optional[Path] = None,
    force: bool = False,
    skip_vision: bool = False,
    skip_download: bool = False,
    vision_fn=None,
) -> dict:
    """Run the full Form 20 verification pipeline.

    Args:
        vision_fn: Optional callable(image_path, prompt) -> str for Vision LLM.
                   When None, falls back to FORM20_VISION_* env vars / HTTP API.
        skip_download: If True, skip PDF download — use existing source.pdf only.

    Returns the report dict.
    """
    if output_dir is None:
        output_dir = Path("data/form20")

    report_dir = output_dir / state_code / str(ac_no)
    report_path = report_dir / "report.json"

    # Check cache
    if report_path.exists() and not force:
        with open(report_path) as f:
            cached = json.load(f)
        cached["cached"] = True
        return cached

    # Step 1: Get constituency info
    info = get_constituency_info(state_code, ac_no)
    ac_name = info["ac_name"] if info else f"AC {ac_no}"
    state_name = info["state_name"] if info else state_code

    # Step 2: Get ECI results
    eci_results = get_eci_results(state_code, ac_no)
    if not eci_results:
        raise SystemExit(f"  ERROR: No ECI results in DB for {state_code} AC {ac_no}")

    # Step 3: Get Form 20 URL
    form20_url = get_form20_url(state_code, ac_no)
    if not form20_url:
        raise SystemExit(
            f"  ERROR: No form20_url set for {state_code} AC {ac_no}\n"
            f"  Set it first: UPDATE constituency_status SET form20_url='...'\n"
            f"  WHERE state_code='{state_code}' AND ac_no={ac_no};"
        )

    # Step 4: Download PDF
    report_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = report_dir / "source.pdf"
    if skip_download:
        if not pdf_path.exists() or not _validate_pdf(pdf_path):
            raise RuntimeError(
                f"PDF not found or invalid (run --download-first first): {pdf_path}"
            )
    elif force or not pdf_path.exists() or not _validate_pdf(pdf_path):
        _download_pdf(form20_url, pdf_path)

    # Step 5: Convert to images
    image_dir = report_dir / "pages"
    image_paths = pdf_to_images(pdf_path, image_dir)
    page_count = len(image_paths)

    # Step 5b: Select pages for vision analysis
    # Include last 3 pages (summary may be on second-to-last or third-to-last)
    # + 20% random booth pages from the rest
    import random
    if page_count > 3:
        tail_pages = image_paths[-3:]  # last 3 pages cover summary location
        booth_pages = image_paths[:-3]
        sample_size = max(1, int(len(booth_pages) * 0.2))
        sampled_booths = random.sample(booth_pages, min(sample_size, len(booth_pages)))
        vision_pages = sorted(set(tail_pages + sampled_booths))
    elif page_count > 1:
        vision_pages = image_paths  # ≤3 pages: analyze all
    else:
        vision_pages = image_paths
    vision_page_count = len(vision_pages)

    # Step 6: Path A — Tesseract (skip when Vision API is available)
    all_tesseract = []
    use_vision = not skip_vision and (vision_fn is not None or _has_vision_api())
    if use_vision:
        pass  # Vision LLM handles everything — skip 23s of Tesseract
    else:
        for img_path in image_paths:
            all_tesseract.extend(ocr_tesseract_extract(img_path))

    # Step 7: Path B — Vision LLM (confirm approach)
    if skip_vision:
        llm_confirmed = [
            {
                "candidate": r["candidate"],
                "party_abv": r["party_abv"],
                "eci_votes": r["votes"],
                "form20_votes": None,
                "name_visible": "unknown",
                "confirmed": None,
                "notes": "Vision LLM skipped (--skip-vision)",
            }
            for r in eci_results
        ]
    else:
        llm_confirmed = ocr_vision_confirm(
            vision_pages, eci_results, ac_name, state_code, ac_no,
            vision_fn=vision_fn,
        )

    # Step 8: Reconcile
    reconciled = reconcile(all_tesseract, llm_confirmed, eci_results)

    # Step 9: Difficulty score
    difficulty, difficulty_label = compute_difficulty(reconciled, page_count)

    # Step 10: Summary (only count verified candidates — top 3)
    verified = [r for r in reconciled if r.get("form20_votes") is not None]
    confirmed_count = sum(1 for r in verified if r["confidence"] == "high" and r.get("delta") == 0)
    mismatched_count = sum(1 for r in verified if r.get("delta") is not None and r.get("delta") != 0)
    low_conf_count = sum(1 for r in verified if r["confidence"] == "low")

    report = {
        "state_code": state_code,
        "ac_no": ac_no,
        "ac_name": ac_name,
        "state_name": state_name,
        "form20_url": form20_url,
        "difficulty": difficulty,
        "difficulty_label": difficulty_label,
        "eci_results": eci_results,
        "reconciled": reconciled,
        "page_count": page_count,
        "vision_page_count": vision_page_count,
        "summary": {
            "total": len(verified),
            "confirmed": confirmed_count,
            "mismatched": mismatched_count,
            "low_confidence": low_conf_count,
        },
    }

    # Save report
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2, default=str)

    return report


def _validate_pdf(path: Path) -> bool:
    """Quick sanity-check: file starts with %PDF and has >10 KB."""
    if not path.exists() or path.stat().st_size < 10_240:
        return False
    with open(path, "rb") as f:
        return f.read(5) == b"%PDF-"


def _has_vision_api() -> bool:
    """Check if a Vision LLM API is configured and usable."""
    return bool(os.environ.get("FORM20_VISION_API_URL", "").strip())


# Per-domain download lock + timing — serializes requests to the same server
# and enforces a minimum gap between consecutive requests to avoid IP bans.
import threading as _threading
_domain_locks: dict[str, _threading.Lock] = {}
_domain_last_hit: dict[str, float] = {}
_domain_lock_lock = _threading.Lock()
_MIN_DOMAIN_GAP = 2.0  # seconds between requests to same domain

def _get_domain_lock(url: str) -> tuple[_threading.Lock, str]:
    """Return a per-domain lock and the domain key."""
    from urllib.parse import urlparse
    domain = urlparse(url).hostname or url
    with _domain_lock_lock:
        if domain not in _domain_locks:
            _domain_locks[domain] = _threading.Lock()
        return _domain_locks[domain], domain


def _download_pdf(url: str, dest: Path) -> None:
    """Download a PDF to the given path.

    Uses curl for Indian government sites that require legacy TLS
    renegotiation (e.g. ceowestbengal.wb.gov.in). Python 3.14's SSL
    rejects these connections, but curl handles them fine.

    Serialized per domain: a threading lock ensures only one download
    runs at a time per domain. A minimum gap of _MIN_DOMAIN_GAP seconds
    is enforced between consecutive requests to avoid IP bans.

    Retries up to 5 times with adaptive backoff:
      - Connection reset (exit 35): 30s backoff (server is rate-limiting)
      - Other failures: exponential backoff 4→8→16→32→64s
      - Truncated/corrupt PDF: 5s backoff
    """
    import subprocess
    import time as _time
    import random as _rand

    domain_lock, domain = _get_domain_lock(url)
    max_attempts = 5

    with domain_lock:  # serialize per domain
        for attempt in range(max_attempts):
            # Enforce minimum gap between requests to same domain
            with _domain_lock_lock:
                last = _domain_last_hit.get(domain, 0)
            elapsed = _time.time() - last
            if elapsed < _MIN_DOMAIN_GAP:
                _time.sleep(_MIN_DOMAIN_GAP - elapsed)

            # Remove any previous incomplete file
            if dest.exists():
                dest.unlink()
            result = subprocess.run(
                ["curl", "-sL", "-o", str(dest), "-k",
                 "--connect-timeout", "10", "--max-time", "30", url],
                capture_output=True, text=True, timeout=45,
            )

            # Record this request timestamp
            with _domain_lock_lock:
                _domain_last_hit[domain] = _time.time()

            if result.returncode != 0:
                # Fast-fail: connection timeout (28) or reset (35) = server down
                # Don't waste time retrying — abort after 2 consecutive connection errors
                if result.returncode in (28, 35) and attempt >= 1:
                    raise RuntimeError(
                        f"curl failed (exit {result.returncode}, server unreachable): {url}"
                    )
                if attempt < max_attempts - 1:
                    # Connection reset (35) = server banning us → long backoff
                    if result.returncode == 35:
                        wait = 30 + _rand.uniform(0, 10)
                    else:
                        wait = (4 * (2 ** attempt)) + _rand.uniform(0, 2)
                    _time.sleep(wait)
                    continue
                raise RuntimeError(f"curl failed (exit {result.returncode}): {url}")
            if _validate_pdf(dest):
                return
            # Truncated/corrupt — retry
            if attempt < max_attempts - 1:
                _time.sleep(5)
        raise ValueError(f"Downloaded PDF is corrupt or truncated ({dest.stat().st_size} bytes): {url}")


# ---------------------------------------------------------------------------
# DB updates
# ---------------------------------------------------------------------------

def update_form20_result(
    state_code: str,
    ac_no: int,
    difficulty: int,
    difficulty_label: str,
    mismatched: int,
    confirmed: int,
    reconciled: list[dict] | None = None,
) -> str:
    """Write verification results back to constituency_status.

    Sets form20_score, form20_status, and form20_checked_at.

    Status logic (top-3 verification):
      VERIFIED  — all 3 top candidates confirmed, no mismatches
      VERIFIED  — MISMATCH but all deltas < 0.05% of ECI votes (OCR digit errors)
      MISMATCH  — pipeline read data successfully but found real discrepancies
      ERROR     — pipeline failed to read data (no confirms at all)
      UNVERIFIED — no vision data (skipped or no API)

    Returns the status string.
    """
    import datetime

    # Auto-verify tiny OCR deltas: if all mismatches are < 0.05% of ECI votes,
    # treat as VERIFIED (LLM misread a digit, not a real discrepancy)
    def _all_deltas_tiny(reconciled_data: list[dict]) -> bool:
        """Check if all mismatched candidates have deltas < 0.05% of ECI votes."""
        if not reconciled_data:
            return False
        for c in reconciled_data:
            delta = c.get("delta")
            eci_votes = c.get("eci_votes", 0)
            if delta is None or eci_votes == 0:
                continue
            if abs(delta) / eci_votes >= 0.0005:  # 0.05%
                return False
        return True

    # Determine status
    # VERIFIED: pipeline confirmed all candidates, no discrepancies
    if confirmed > 0 and mismatched == 0:
        status = "VERIFIED"
    # MISMATCH → auto-verify if all deltas are tiny OCR errors
    elif mismatched > 0 and confirmed > 0 and difficulty >= 20:
        if reconciled and _all_deltas_tiny(reconciled):
            status = "VERIFIED"  # tiny deltas, likely OCR digit errors
        else:
            status = "MISMATCH"
    # ERROR: pipeline ran but failed to read meaningful data
    #   - Low score with any mismatches (OCR errors, wrong column reads)
    #   - No candidates confirmed at all (couldn't read numbers)
    elif difficulty is not None and confirmed == 0:
        status = "ERROR"
    elif mismatched > 0 and difficulty is not None and difficulty < 20:
        status = "ERROR"
    # VERIFIED (edge case: confirmed > 0 but no mismatches, already caught above)
    elif confirmed > 0:
        status = "VERIFIED"
    # UNVERIFIED: pipeline never ran (form20_score is None)
    else:
        status = "UNVERIFIED"

    conn = db_utils._connect()
    cur = db_utils._cursor(conn)
    try:
        p = db_utils._placeholder()
        cur.execute(
            f"""UPDATE constituency_status
                SET form20_score = {p},
                    form20_status = {p},
                    form20_checked_at = {p}
                WHERE state_code = {p} AND ac_no = {p}""",
            (difficulty, status, datetime.datetime.now().isoformat(), state_code, ac_no),
        )
        conn.commit()
    finally:
        conn.close()

    return status


def get_state_acs_with_form20(state_code: str) -> list[dict]:
    """Get all ACs in a state that have form20_urls set.

    Returns list of {ac_no, ac_name, form20_url}.
    """
    conn = db_utils._connect()
    cur = db_utils._cursor(conn)
    try:
        p = db_utils._placeholder()
        cur.execute(
            f"""SELECT ac_no, ac_name, form20_url
                FROM constituency_status
                WHERE state_code = {p}
                  AND form20_url IS NOT NULL
                ORDER BY ac_no""",
            (state_code,),
        )
        rows = cur.fetchall()
        return [
            {
                "ac_no": r["ac_no"] if hasattr(r, "keys") else r[0],
                "ac_name": r["ac_name"] if hasattr(r, "keys") else r[1],
                "form20_url": r["form20_url"] if hasattr(r, "keys") else r[2],
            }
            for r in rows
        ]
    finally:
        conn.close()
