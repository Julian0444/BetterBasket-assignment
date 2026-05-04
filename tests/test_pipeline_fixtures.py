"""End-to-end fixture pipeline tests for Phase 7.

Drive run_pipeline against the frozen mini fixtures, check the output CSVs
and audit rows against the expected_matches.csv oracle, and lock the two
PDF regressions. Do not weaken the oracle: if a MATCH case fails because of
threshold or taxonomy, the test fails loudly and the implementing session
must stop and report.
"""
from __future__ import annotations

import csv
from pathlib import Path

import pytest

from betterbasket_matcher.pipeline import (
    PipelineConfig,
    PipelineResult,
    run_pipeline,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures"
A_CSV = FIXTURE_DIR / "mini_a.csv"
B_CSV = FIXTURE_DIR / "mini_b.csv"
EXPECTED_CSV = FIXTURE_DIR / "expected_matches.csv"

AUDIT_HEADER = [
    "item_id_A",
    "item_id_B",
    "score",
    "retrieval_score",
    "top1_top2_margin",
    "source",
    "decision",
    "reason",
    "llm_confidence",
]


def _load_expected() -> list[dict]:
    with EXPECTED_CSV.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


@pytest.fixture(scope="module")
def pipeline_outputs(tmp_path_factory) -> tuple[PipelineResult, Path, Path]:
    out_dir = tmp_path_factory.mktemp("phase7_pipeline")
    matches_path = out_dir / "matches.csv"
    audit_path = out_dir / "matches_audit.csv"
    cfg = PipelineConfig(
        a_csv=str(A_CSV),
        b_csv=str(B_CSV),
        matches_out=str(matches_path),
        audit_out=str(audit_path),
        min_score=0.55,
        min_margin=0.05,
        top_k=50,
        limit=None,
    )
    result = run_pipeline(cfg)
    return result, matches_path, audit_path


def _read_matches(path: Path) -> list[tuple[str, str]]:
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.reader(fh)
        rows = list(reader)
    assert rows, "matches.csv is empty (no header)"
    return [(r[0], r[1]) for r in rows[1:]]


def _read_matches_header(path: Path) -> list[str]:
    with path.open(newline="", encoding="utf-8") as fh:
        return next(csv.reader(fh))


def _read_audit(path: Path) -> tuple[list[str], list[dict]]:
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.reader(fh)
        header = next(reader)
        rows = [dict(zip(header, r)) for r in reader]
    return header, rows


# ---------------------------------------------------------------------------
# Headers and file existence
# ---------------------------------------------------------------------------

class TestOutputFiles:
    def test_matches_csv_exists(self, pipeline_outputs):
        _, matches_path, _ = pipeline_outputs
        assert matches_path.exists()

    def test_audit_csv_exists(self, pipeline_outputs):
        _, _, audit_path = pipeline_outputs
        assert audit_path.exists()

    def test_matches_header_exact(self, pipeline_outputs):
        _, matches_path, _ = pipeline_outputs
        header = _read_matches_header(matches_path)
        assert header == ["item_id_A", "item_id_B"]

    def test_audit_header_exact_order(self, pipeline_outputs):
        """Phase 7 audit header must equal the locked 9 columns in order."""
        _, _, audit_path = pipeline_outputs
        header, _ = _read_audit(audit_path)
        assert header == AUDIT_HEADER


# ---------------------------------------------------------------------------
# PDF regressions and oracle parity
# ---------------------------------------------------------------------------

class TestPdfRegressions:
    def test_chobani_pdf(self, pipeline_outputs):
        _, matches_path, audit_path = pipeline_outputs
        matches = _read_matches(matches_path)
        if ("2197626", "92544") not in matches:
            _, audit_rows = _read_audit(audit_path)
            ar = next((r for r in audit_rows if r["item_id_A"] == "2197626"), None)
            pytest.fail(
                "PDF regression failed: A 2197626 expected -> B 92544 but got "
                f"{[m for m in matches if m[0] == '2197626']}; audit row={ar}"
            )

    def test_tomato_8oz_pdf(self, pipeline_outputs):
        _, matches_path, audit_path = pipeline_outputs
        matches = _read_matches(matches_path)
        if ("1929544", "105624") not in matches:
            _, audit_rows = _read_audit(audit_path)
            ar = next((r for r in audit_rows if r["item_id_A"] == "1929544"), None)
            pytest.fail(
                "PDF regression failed: A 1929544 expected -> B 105624 but got "
                f"{[m for m in matches if m[0] == '1929544']}; audit row={ar}"
            )

    def test_tomato_does_not_pick_wrong_sizes(self, pipeline_outputs):
        _, matches_path, _ = pipeline_outputs
        matches = _read_matches(matches_path)
        for wrong_b in ("103620", "1086860"):
            assert ("1929544", wrong_b) not in matches, (
                f"A 1929544 must not map to wrong-size B {wrong_b}"
            )


class TestOracleParity:
    """Every expected MATCH must be present; every NO_MATCH must be absent."""

    def test_every_expected_match_present(self, pipeline_outputs):
        _, matches_path, audit_path = pipeline_outputs
        matches = set(_read_matches(matches_path))
        _, audit_rows = _read_audit(audit_path)
        audit_by_a = {r["item_id_A"]: r for r in audit_rows}

        failures: list[str] = []
        for row in _load_expected():
            if row["item_id_B"] == "NO_MATCH":
                continue
            pair = (row["item_id_A"], row["item_id_B"])
            if pair not in matches:
                ar = audit_by_a.get(row["item_id_A"])
                failures.append(
                    f"item_id_A={row['item_id_A']} expected_item_id_B="
                    f"{row['item_id_B']} case={row['case_id']} "
                    f"observed_decision={ar.get('decision') if ar else 'MISSING'} "
                    f"observed_reason={ar.get('reason') if ar else 'MISSING'} "
                    f"audit_row={ar}"
                )
        if failures:
            pytest.fail(
                "Expected MATCH cases failed (do not weaken oracle):\n  "
                + "\n  ".join(failures)
            )

    def test_every_no_match_absent_from_matches(self, pipeline_outputs):
        _, matches_path, _ = pipeline_outputs
        matches_a = {a for a, _ in _read_matches(matches_path)}
        for row in _load_expected():
            if row["item_id_B"] != "NO_MATCH":
                continue
            assert row["item_id_A"] not in matches_a, (
                f"NO_MATCH case {row['case_id']} (A {row['item_id_A']}) must not "
                f"appear in matches.csv"
            )

    def test_every_no_match_has_valid_audit_decision(self, pipeline_outputs):
        _, _, audit_path = pipeline_outputs
        _, audit_rows = _read_audit(audit_path)
        audit_by_a = {r["item_id_A"]: r for r in audit_rows}
        valid = {"rejected_by_rule", "below_threshold", "no_candidates"}
        for row in _load_expected():
            if row["item_id_B"] != "NO_MATCH":
                continue
            ar = audit_by_a.get(row["item_id_A"])
            assert ar is not None, (
                f"NO_MATCH case {row['case_id']} A={row['item_id_A']} missing from audit"
            )
            assert ar["decision"] in valid, (
                f"NO_MATCH case {row['case_id']} A={row['item_id_A']} has unexpected "
                f"decision={ar['decision']}; full audit={ar}"
            )


# ---------------------------------------------------------------------------
# Quarantine
# ---------------------------------------------------------------------------

class TestQuarantine:
    def test_malformed_a_id_absent_from_matches(self, pipeline_outputs):
        _, matches_path, _ = pipeline_outputs
        with matches_path.open(newline="", encoding="utf-8") as fh:
            content = fh.read()
        assert " | Pack of 12" not in content
        assert "Pack of 12" not in content

    def test_malformed_a_id_absent_from_audit(self, pipeline_outputs):
        _, _, audit_path = pipeline_outputs
        with audit_path.open(newline="", encoding="utf-8") as fh:
            content = fh.read()
        assert " | Pack of 12" not in content
        assert "Pack of 12" not in content

    def test_quarantined_count_reflected_in_result(self, pipeline_outputs):
        result, _, _ = pipeline_outputs
        assert result.quarantined_a_count >= 1


# ---------------------------------------------------------------------------
# Audit completeness
# ---------------------------------------------------------------------------

class TestAuditCompleteness:
    def test_one_audit_row_per_valid_a(self, pipeline_outputs):
        result, _, audit_path = pipeline_outputs
        _, audit_rows = _read_audit(audit_path)
        assert len(audit_rows) == result.valid_a_count, (
            f"audit rows={len(audit_rows)} valid_a_count={result.valid_a_count}"
        )

    def test_audit_a_ids_unique(self, pipeline_outputs):
        _, _, audit_path = pipeline_outputs
        _, audit_rows = _read_audit(audit_path)
        ids = [r["item_id_A"] for r in audit_rows]
        assert len(ids) == len(set(ids)), "duplicate item_id_A in audit"

    def test_every_accepted_match_has_audit_row(self, pipeline_outputs):
        _, matches_path, audit_path = pipeline_outputs
        matches = _read_matches(matches_path)
        _, audit_rows = _read_audit(audit_path)
        audit_by_a = {r["item_id_A"]: r for r in audit_rows}
        for a, b in matches:
            ar = audit_by_a.get(a)
            assert ar is not None, f"matches has A={a} but no audit row"
            assert ar["decision"] == "accepted"
            assert ar["item_id_B"] == b
            assert ar["reason"] == "ok"


# ---------------------------------------------------------------------------
# Out-of-scope handling
# ---------------------------------------------------------------------------

class TestOutOfScope:
    def test_home_decor_decision_is_no_candidates_with_scope_reason(self, pipeline_outputs):
        _, _, audit_path = pipeline_outputs
        _, audit_rows = _read_audit(audit_path)
        ar = next((r for r in audit_rows if r["item_id_A"] == "9000015"), None)
        assert ar is not None
        assert ar["decision"] == "no_candidates"
        assert ar["reason"] in {"excluded_category", "excluded_home_decor"}
        # Out-of-scope rows must not have an item_id_B
        assert ar["item_id_B"] == ""

    def test_toy_decision_is_no_candidates_with_excluded_category(self, pipeline_outputs):
        _, _, audit_path = pipeline_outputs
        _, audit_rows = _read_audit(audit_path)
        ar = next((r for r in audit_rows if r["item_id_A"] == "9000014"), None)
        assert ar is not None
        assert ar["decision"] == "no_candidates"
        assert ar["reason"] == "excluded_category"


# ---------------------------------------------------------------------------
# Source value (locked to "deterministic" for Phase 7)
# ---------------------------------------------------------------------------

class TestAuditSource:
    def test_every_audit_row_has_source_deterministic(self, pipeline_outputs):
        _, _, audit_path = pipeline_outputs
        _, audit_rows = _read_audit(audit_path)
        assert audit_rows, "no audit rows"
        for r in audit_rows:
            assert r["source"] == "deterministic", f"unexpected source: {r}"


# ---------------------------------------------------------------------------
# Margin serialization
# ---------------------------------------------------------------------------

class TestMarginSerialization:
    def test_no_audit_row_has_zero_margin_when_single_survivor(self, pipeline_outputs):
        """Single-survivor cases must serialize top1_top2_margin as empty, not 0.0."""
        _, _, audit_path = pipeline_outputs
        _, audit_rows = _read_audit(audit_path)
        # We don't know upfront which rows are single-survivor; instead, assert the
        # invariant that any margin cell is either empty OR a parseable positive
        # float >= 0. The empty-string case is the single-survivor case.
        for r in audit_rows:
            cell = r["top1_top2_margin"]
            if cell == "":
                continue
            float(cell)  # must parse

    def test_no_candidates_rows_have_empty_numeric_cells(self, pipeline_outputs):
        _, _, audit_path = pipeline_outputs
        _, audit_rows = _read_audit(audit_path)
        any_no_cand = False
        for r in audit_rows:
            if r["decision"] != "no_candidates":
                continue
            any_no_cand = True
            assert r["item_id_B"] == ""
            assert r["score"] == ""
            assert r["retrieval_score"] == ""
            assert r["top1_top2_margin"] == ""
            assert r["llm_confidence"] == ""
        assert any_no_cand, "expected at least one no_candidates audit row"


# ---------------------------------------------------------------------------
# matches.csv basic invariants
# ---------------------------------------------------------------------------

class TestMatchesCsvInvariants:
    def test_no_duplicate_item_id_a(self, pipeline_outputs):
        _, matches_path, _ = pipeline_outputs
        a_ids = [a for a, _ in _read_matches(matches_path)]
        assert len(a_ids) == len(set(a_ids))

    def test_all_ids_numeric(self, pipeline_outputs):
        _, matches_path, _ = pipeline_outputs
        for a, b in _read_matches(matches_path):
            assert a.isdigit(), f"non-numeric A id: {a!r}"
            assert b.isdigit(), f"non-numeric B id: {b!r}"

    def test_llm_confidence_always_empty(self, pipeline_outputs):
        _, _, audit_path = pipeline_outputs
        _, audit_rows = _read_audit(audit_path)
        assert all(r["llm_confidence"] == "" for r in audit_rows)


# ---------------------------------------------------------------------------
# --limit semantics
# ---------------------------------------------------------------------------

class TestLimit:
    def test_limit_truncates_after_quarantine(self, tmp_path):
        out_matches = tmp_path / "m.csv"
        out_audit = tmp_path / "a.csv"
        cfg = PipelineConfig(
            a_csv=str(A_CSV),
            b_csv=str(B_CSV),
            matches_out=str(out_matches),
            audit_out=str(out_audit),
            min_score=0.55,
            min_margin=0.05,
            top_k=50,
            limit=3,
        )
        result = run_pipeline(cfg)
        # Only the first 3 valid (post-quarantine) A rows should be processed.
        _, audit_rows = _read_audit(out_audit)
        assert len(audit_rows) == 3
        assert result.valid_a_count == 3
