"""Tests for Form 20 verification pipeline.

TDD: these tests define the expected behavior before implementation.
"""
import json
import os
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def test_db():
    """Create a test database with constituency_status + rounds_ac tables."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    conn = sqlite3.connect(db_path)
    conn.row_factory = lambda cursor, row: {col[0]: row[i] for i, col in enumerate(cursor.description)}
    cur = conn.cursor()

    cur.executescript("""
        CREATE TABLE constituency_status (
            state_code    TEXT NOT NULL,
            ac_no         INTEGER NOT NULL,
            ac_name       TEXT,
            status        TEXT NOT NULL DEFAULT 'PENDING',
            current_round INTEGER DEFAULT 0,
            error_count   INTEGER DEFAULT 0,
            won           INTEGER DEFAULT 0,
            form20_url    TEXT,
            form20_status TEXT NOT NULL DEFAULT 'UNAVAILABLE',
            PRIMARY KEY (state_code, ac_no)
        );

        CREATE TABLE rounds_ac (
            state_code TEXT NOT NULL,
            ac_no      INTEGER NOT NULL,
            ac_name    TEXT,
            round_no   INTEGER NOT NULL,
            candidate  TEXT NOT NULL,
            party_abv  TEXT NOT NULL,
            votes      INTEGER NOT NULL,
            PRIMARY KEY (state_code, ac_no, round_no, candidate, party_abv)
        );

        CREATE TABLE parties (
            abv  TEXT PRIMARY KEY,
            name TEXT NOT NULL
        );

        CREATE TABLE states (
            state_code     TEXT PRIMARY KEY,
            state_code_std TEXT,
            state_name     TEXT NOT NULL,
            assembly_seats INTEGER
        );
    """)

    # Seed test data: WB AC 110 — a "good" constituency
    cur.execute(
        "INSERT INTO constituency_status VALUES (?,?,?,?,?,?,?,?,?)",
        ("S25", 110, "TEST_AC", "DONE", 999, 0, 1,
         "https://example.com/form20/110.pdf", "UNVERIFIED"),
    )
    # Seed test data: WB AC 275 — the "impossible" constituency
    cur.execute(
        "INSERT INTO constituency_status VALUES (?,?,?,?,?,?,?,?,?)",
        ("S25", 275, "PANDABESWAR", "DONE", 999, 0, 1,
         "https://example.com/form20/275.pdf", "UNVERIFIED"),
    )
    # Seed test data: AC with no form20_url
    cur.execute(
        "INSERT INTO constituency_status VALUES (?,?,?,?,?,?,?,?,?)",
        ("S25", 1, "NO_FORM20", "DONE", 999, 0, 1, None, "UNAVAILABLE"),
    )

    # Seed rounds_ac for AC 110 (final round = 999)
    candidates_110 = [
        ("S25", 110, "TEST_AC", 999, "ABDUS SOBAHAN ALI", "AITC", 182609),
        ("S25", 110, "TEST_AC", 999, "NIZANUR RAHMAN", "CPM", 63678),
        ("S25", 110, "TEST_AC", 999, "GOPAL CHANDRA ROY", "BJP", 41203),
        ("S25", 110, "TEST_AC", 999, "NOTA", "NOTA", 2891),
    ]
    cur.executemany(
        "INSERT INTO rounds_ac VALUES (?,?,?,?,?,?,?)", candidates_110
    )

    # Seed rounds_ac for AC 275
    candidates_275 = [
        ("S25", 275, "PANDABESWAR", 999, "CANDIDATE A", "AITC", 95432),
        ("S25", 275, "PANDABESWAR", 999, "CANDIDATE B", "CPM", 62108),
        ("S25", 275, "PANDABESWAR", 999, "CANDIDATE C", "BJP", 35872),
        ("S25", 275, "PANDABESWAR", 999, "NOTA", "NOTA", 2100),
    ]
    cur.executemany(
        "INSERT INTO rounds_ac VALUES (?,?,?,?,?,?,?)", candidates_275
    )

    # Parties
    for abv, name in [("AITC", "All India Trinamool Congress"),
                       ("CPM", "Communist Party of India (Marxist)"),
                       ("BJP", "Bharatiya Janata Party"),
                       ("NOTA", "None of the Above")]:
        cur.execute("INSERT INTO parties VALUES (?,?)", (abv, name))

    # States
    cur.execute("INSERT INTO states VALUES (?,?,?,?)",
                ("S25", "WB", "West Bengal", 294))

    conn.commit()
    conn.close()

    import db_utils
    original_connect = db_utils._connect
    original_is_pg = db_utils.IS_PG
    db_utils._connect = lambda: sqlite3.connect(db_path)
    db_utils.IS_PG = False

    yield db_path

    db_utils._connect = original_connect
    db_utils.IS_PG = original_is_pg
    os.unlink(db_path)


# ---------------------------------------------------------------------------
# Test: get_form20_url
# ---------------------------------------------------------------------------

class TestGetForm20Url:
    def test_returns_url_when_set(self, test_db):
        from tools.ocr_engine import get_form20_url
        url = get_form20_url("S25", 110)
        assert url == "https://example.com/form20/110.pdf"

    def test_returns_none_when_not_set(self, test_db):
        from tools.ocr_engine import get_form20_url
        url = get_form20_url("S25", 1)
        assert url is None

    def test_returns_none_for_nonexistent_ac(self, test_db):
        from tools.ocr_engine import get_form20_url
        url = get_form20_url("S25", 9999)
        assert url is None


# ---------------------------------------------------------------------------
# Test: get_eci_results
# ---------------------------------------------------------------------------

class TestGetEciResults:
    def test_returns_candidates_for_ac(self, test_db):
        from tools.ocr_engine import get_eci_results
        results = get_eci_results("S25", 110)
        assert len(results) == 4
        assert results[0]["candidate"] == "ABDUS SOBAHAN ALI"
        assert results[0]["party_abv"] == "AITC"
        assert results[0]["votes"] == 182609

    def test_returns_empty_for_no_data(self, test_db):
        from tools.ocr_engine import get_eci_results
        results = get_eci_results("S25", 9999)
        assert results == []

    def test_results_sorted_by_votes_desc(self, test_db):
        from tools.ocr_engine import get_eci_results
        results = get_eci_results("S25", 110)
        votes = [r["votes"] for r in results]
        assert votes == sorted(votes, reverse=True)


# ---------------------------------------------------------------------------
# Test: Tesseract extraction (parser only — no actual OCR)
# ---------------------------------------------------------------------------

class TestTesseractParser:
    def test_parse_tesseract_dataframe(self):
        """Given a mock Tesseract DataFrame, extract candidate rows."""
        import pandas as pd
        from tools.ocr_engine import parse_tesseract_output

        # Simulate Tesseract output: a table with candidate rows
        # Columns: text, conf, left, top, width, height, block_num, par_num, line_num, word_num
        data = {
            "text": ["1", "ABDUS", "SOBAHAN", "ALI", "AITC", "182609",
                     "2", "NIZANUR", "RAHMAN", "CPM", "63678",
                     "Total", "290378"],
            "conf": [90, 85, 80, 88, 95, 92, 90, 70, 75, 95, 88, 90, 95],
            "block_num": [1]*13,
            "par_num": [1]*13,
            "line_num": [1,1,1,1,1,1, 2,2,2,2,2, 3,3],
            "word_num": [1,2,3,4,5,6, 1,2,3,4,5, 1,2],
        }
        df = pd.DataFrame(data)

        result = parse_tesseract_output(df)
        # Should extract at least the rows with numeric vote counts
        assert len(result) >= 2
        # First candidate should have votes
        assert any(r.get("votes") == 182609 for r in result)

    def test_parse_empty_dataframe(self):
        import pandas as pd
        from tools.ocr_engine import parse_tesseract_output
        df = pd.DataFrame(columns=["text", "conf", "block_num", "par_num", "line_num", "word_num"])
        result = parse_tesseract_output(df)
        assert result == []


# ---------------------------------------------------------------------------
# Test: Vision LLM confirm prompt generation
# ---------------------------------------------------------------------------

class TestVisionPrompt:
    def test_prompt_contains_known_candidates(self):
        from tools.ocr_engine import build_confirm_prompt
        eci = [
            {"candidate": "ABDUS SOBAHAN ALI", "party_abv": "AITC", "votes": 182609},
            {"candidate": "NIZANUR RAHMAN", "party_abv": "CPM", "votes": 63678},
        ]
        prompt = build_confirm_prompt("TEST_AC", "S25", 110, eci)
        assert "ABDUS SOBAHAN ALI" in prompt
        assert "182,609" in prompt
        assert "NIZANUR RAHMAN" in prompt
        assert "confirm" in prompt.lower() or "verify" in prompt.lower()

    def test_prompt_instructs_not_to_guess(self):
        from tools.ocr_engine import build_confirm_prompt
        eci = [{"candidate": "TEST", "party_abv": "TST", "votes": 100}]
        prompt = build_confirm_prompt("AC", "S01", 1, eci)
        assert "null" in prompt.lower() or "illegible" in prompt.lower() or "not guess" in prompt.lower()


# ---------------------------------------------------------------------------
# Test: Vision LLM response parsing
# ---------------------------------------------------------------------------

class TestVisionResponseParser:
    def test_parse_valid_json_response(self):
        from tools.ocr_engine import parse_vision_response
        response = json.dumps([
            {"candidate": "ABDUS SOBAHAN ALI", "party_abv": "AITC",
             "eci_votes": 182609, "form20_votes": 182609,
             "name_visible": "yes", "confirmed": True},
            {"candidate": "NIZANUR RAHMAN", "party_abv": "CPM",
             "eci_votes": 63678, "form20_votes": 63472,
             "name_visible": "yes", "confirmed": False},
        ])
        result = parse_vision_response(response)
        assert len(result) == 2
        assert result[0]["confirmed"] is True
        assert result[1]["form20_votes"] == 63472

    def test_parse_response_with_nulls(self):
        from tools.ocr_engine import parse_vision_response
        response = json.dumps([
            {"candidate": "CANDIDATE A", "party_abv": "AITC",
             "eci_votes": 95432, "form20_votes": None,
             "name_visible": "no", "confirmed": None},
        ])
        result = parse_vision_response(response)
        assert result[0]["form20_votes"] is None
        assert result[0]["confirmed"] is None

    def test_parse_malformed_json_returns_empty(self):
        from tools.ocr_engine import parse_vision_response
        result = parse_vision_response("this is not json")
        assert result == []


# ---------------------------------------------------------------------------
# Test: Reconciliation
# ---------------------------------------------------------------------------

class TestReconcile:
    def test_both_agree_high_confidence(self):
        from tools.ocr_engine import reconcile_row
        result = reconcile_row(
            eci_votes=182609,
            tesseract_votes=182609,
            llm_votes=182609,
            llm_confirmed=True,
            llm_name_visible="yes",
        )
        assert result["confidence"] == "high"
        assert result["delta"] == 0

    def test_llm_confirms_tesseract_none(self):
        from tools.ocr_engine import reconcile_row
        result = reconcile_row(
            eci_votes=182609,
            tesseract_votes=None,
            llm_votes=182609,
            llm_confirmed=True,
            llm_name_visible="yes",
        )
        assert result["confidence"] == "high"
        assert result["delta"] == 0

    def test_small_mismatch_medium_confidence(self):
        from tools.ocr_engine import reconcile_row
        result = reconcile_row(
            eci_votes=63678,
            tesseract_votes=None,
            llm_votes=63472,
            llm_confirmed=False,
            llm_name_visible="yes",
        )
        assert result["confidence"] == "medium"
        assert result["delta"] == -206

    def test_name_not_visible_medium(self):
        from tools.ocr_engine import reconcile_row
        result = reconcile_row(
            eci_votes=95432,
            tesseract_votes=None,
            llm_votes=95432,
            llm_confirmed=True,
            llm_name_visible="partial",
        )
        assert result["confidence"] == "medium"

    def test_neither_path_low_confidence(self):
        from tools.ocr_engine import reconcile_row
        result = reconcile_row(
            eci_votes=95432,
            tesseract_votes=None,
            llm_votes=None,
            llm_confirmed=None,
            llm_name_visible="no",
        )
        assert result["confidence"] == "low"

    def test_large_mismatch_low_confidence(self):
        from tools.ocr_engine import reconcile_row
        result = reconcile_row(
            eci_votes=95432,
            tesseract_votes=50000,
            llm_votes=50000,
            llm_confirmed=False,
            llm_name_visible="yes",
        )
        assert result["confidence"] == "low"
        assert abs(result["delta"]) > 0


# ---------------------------------------------------------------------------
# Test: Difficulty score
# ---------------------------------------------------------------------------

class TestDifficultyScore:
    def test_all_high_confidence_easy(self):
        from tools.ocr_engine import compute_difficulty
        reconciled = [
            {"confidence": "high", "form20_votes": 100, "eci_votes": 100, "name_visible": "yes"},
            {"confidence": "high", "form20_votes": 200, "eci_votes": 200, "name_visible": "yes"},
            {"confidence": "high", "form20_votes": 300, "eci_votes": 300, "name_visible": "yes"},
            {"confidence": "high", "form20_votes": 50, "eci_votes": 50, "name_visible": "yes"},
        ]
        score, label = compute_difficulty(reconciled, page_count=1)
        assert score >= 80
        assert label == "EASY"

    def test_all_low_confidence_impossible(self):
        from tools.ocr_engine import compute_difficulty
        reconciled = [
            {"confidence": "low", "form20_votes": None, "eci_votes": 100, "name_visible": "no"},
            {"confidence": "low", "form20_votes": None, "eci_votes": 200, "name_visible": "no"},
            {"confidence": "low", "form20_votes": None, "eci_votes": 300, "name_visible": "no"},
        ]
        score, label = compute_difficulty(reconciled, page_count=1)
        assert score < 20
        assert label == "IMPOSSIBLE"

    def test_mixed_confidence_moderate(self):
        from tools.ocr_engine import compute_difficulty
        reconciled = [
            {"confidence": "high", "form20_votes": 100, "eci_votes": 100, "name_visible": "yes"},
            {"confidence": "medium", "form20_votes": 200, "eci_votes": 200, "name_visible": "partial"},
            {"confidence": "low", "form20_votes": None, "eci_votes": 300, "name_visible": "no"},
            {"confidence": "high", "form20_votes": 50, "eci_votes": 50, "name_visible": "yes"},
        ]
        score, label = compute_difficulty(reconciled, page_count=1)
        assert 20 <= score <= 79
        assert label in ("MODERATE", "HARD")

    def test_many_pages_penalty(self):
        from tools.ocr_engine import compute_difficulty
        reconciled = [
            {"confidence": "high", "form20_votes": 100, "eci_votes": 100, "name_visible": "yes"},
        ]
        score_1page, _ = compute_difficulty(reconciled, page_count=1)
        score_10pages, _ = compute_difficulty(reconciled, page_count=10)
        assert score_10pages < score_1page


# ---------------------------------------------------------------------------
# Test: Full pipeline integration (mocked OCR)
# ---------------------------------------------------------------------------

class TestPipeline:
    def test_full_pipeline_with_mocked_ocr(self, test_db, tmp_path):
        """End-to-end: mock PDF conversion and OCR, verify report structure."""
        from tools.ocr_engine import run_pipeline

        # Mock the PDF download and image conversion
        mock_images = [tmp_path / "page_001.png"]

        # Create a dummy image
        from PIL import Image
        img = Image.new("RGB", (100, 100), "white")
        img.save(str(mock_images[0]))

        # Mock vision LLM response
        vision_response = json.dumps([
            {"candidate": "ABDUS SOBAHAN ALI", "party_abv": "AITC",
             "eci_votes": 182609, "form20_votes": 182609,
             "name_visible": "yes", "confirmed": True},
            {"candidate": "NIZANUR RAHMAN", "party_abv": "CPM",
             "eci_votes": 63678, "form20_votes": 63472,
             "name_visible": "yes", "confirmed": False},
            {"candidate": "GOPAL CHANDRA ROY", "party_abv": "BJP",
             "eci_votes": 41203, "form20_votes": 41203,
             "name_visible": "yes", "confirmed": True},
            {"candidate": "NOTA", "party_abv": "NOTA",
             "eci_votes": 2891, "form20_votes": 2891,
             "name_visible": "yes", "confirmed": True},
        ])

        with patch("tools.ocr_engine.pdf_to_images", return_value=mock_images), \
             patch("tools.ocr_engine.ocr_tesseract_extract", return_value=[]), \
             patch("tools.ocr_engine.ocr_vision_confirm", return_value=json.loads(vision_response)), \
             patch("tools.ocr_engine._download_pdf"):

            report = run_pipeline("S25", 110, output_dir=tmp_path, force=True)

        assert report["state_code"] == "S25"
        assert report["ac_no"] == 110
        assert report["difficulty"] > 0
        assert report["difficulty_label"] in ("EASY", "MODERATE", "HARD", "IMPOSSIBLE")
        assert len(report["reconciled"]) == 4
        assert report["summary"]["total"] == 4
        assert "confirmed" in report["summary"]
        assert "mismatched" in report["summary"]

    def test_pipeline_exits_gracefully_no_url(self, test_db, tmp_path):
        from tools.ocr_engine import run_pipeline
        with pytest.raises(SystemExit):
            run_pipeline("S25", 1, output_dir=tmp_path)

    def test_pipeline_caches_by_default(self, test_db, tmp_path):
        """If report.json already exists and not --force, return cached."""
        from tools.ocr_engine import run_pipeline

        # Create a fake cached report
        report_dir = tmp_path / "S25" / "110"
        report_dir.mkdir(parents=True)
        cached = {"state_code": "S25", "ac_no": 110, "difficulty": 99, "cached": True}
        (report_dir / "report.json").write_text(json.dumps(cached))

        result = run_pipeline("S25", 110, output_dir=tmp_path, force=False)
        assert result.get("cached") is True
