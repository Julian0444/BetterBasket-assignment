"""Output validation tests for Phase 7.

Validate the contract for matches.csv: header shape, numeric IDs,
membership in supplied A/B sets, dedup, required-pair gates, and the
configurable min_rows floor.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from betterbasket_matcher.output import (
    ValidationResult,
    validate_matches_csv,
    write_matches,
)


def _write_csv(path: Path, header: list[str], rows: list[tuple[str, str]]) -> None:
    lines = [",".join(header)] + [f"{a},{b}" for a, b in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.fixture
def valid_ids() -> tuple[set[str], set[str]]:
    return ({"1", "2", "3", "1929544", "2197626"}, {"10", "20", "30", "92544", "105624", "103620", "1086860"})


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

class TestValid:
    def test_valid_file_passes(self, tmp_path, valid_ids):
        a_set, b_set = valid_ids
        path = tmp_path / "matches.csv"
        _write_csv(path, ["item_id_A", "item_id_B"], [("1", "10"), ("2", "20"), ("3", "30")])
        res = validate_matches_csv(
            str(path), valid_ids_a=a_set, valid_ids_b=b_set,
            min_rows=3, required_pairs={},
        )
        assert isinstance(res, ValidationResult)
        assert res.ok is True
        assert res.errors == []
        assert res.row_count == 3


# ---------------------------------------------------------------------------
# Header errors
# ---------------------------------------------------------------------------

class TestHeader:
    def test_wrong_header_fails(self, tmp_path, valid_ids):
        a_set, b_set = valid_ids
        path = tmp_path / "m.csv"
        _write_csv(path, ["a_id", "b_id"], [("1", "10")])
        res = validate_matches_csv(
            str(path), valid_ids_a=a_set, valid_ids_b=b_set,
            min_rows=1, required_pairs={},
        )
        assert res.ok is False
        assert any("header" in e.lower() for e in res.errors)

    def test_extra_columns_in_header_fails(self, tmp_path, valid_ids):
        a_set, b_set = valid_ids
        path = tmp_path / "m.csv"
        _write_csv(path, ["item_id_A", "item_id_B", "extra"], [])
        res = validate_matches_csv(
            str(path), valid_ids_a=a_set, valid_ids_b=b_set,
            min_rows=0, required_pairs={},
        )
        assert res.ok is False
        assert any("header" in e.lower() for e in res.errors)


# ---------------------------------------------------------------------------
# Numeric / membership
# ---------------------------------------------------------------------------

class TestNumericAndMembership:
    def test_non_numeric_id_fails(self, tmp_path, valid_ids):
        a_set, b_set = valid_ids
        path = tmp_path / "m.csv"
        _write_csv(path, ["item_id_A", "item_id_B"], [("abc", "10"), ("1", "20")])
        res = validate_matches_csv(
            str(path), valid_ids_a=a_set, valid_ids_b=b_set,
            min_rows=1, required_pairs={},
        )
        assert res.ok is False
        assert any("numeric" in e.lower() for e in res.errors)

    def test_unknown_id_a_fails(self, tmp_path, valid_ids):
        a_set, b_set = valid_ids
        path = tmp_path / "m.csv"
        _write_csv(path, ["item_id_A", "item_id_B"], [("99999", "10")])
        res = validate_matches_csv(
            str(path), valid_ids_a=a_set, valid_ids_b=b_set,
            min_rows=1, required_pairs={},
        )
        assert res.ok is False
        assert any("99999" in e for e in res.errors)

    def test_unknown_id_b_fails(self, tmp_path, valid_ids):
        a_set, b_set = valid_ids
        path = tmp_path / "m.csv"
        _write_csv(path, ["item_id_A", "item_id_B"], [("1", "88888")])
        res = validate_matches_csv(
            str(path), valid_ids_a=a_set, valid_ids_b=b_set,
            min_rows=1, required_pairs={},
        )
        assert res.ok is False
        assert any("88888" in e for e in res.errors)


# ---------------------------------------------------------------------------
# Duplicates
# ---------------------------------------------------------------------------

class TestDuplicates:
    def test_duplicate_item_id_a_fails(self, tmp_path, valid_ids):
        a_set, b_set = valid_ids
        path = tmp_path / "m.csv"
        _write_csv(path, ["item_id_A", "item_id_B"], [("1", "10"), ("1", "20")])
        res = validate_matches_csv(
            str(path), valid_ids_a=a_set, valid_ids_b=b_set,
            min_rows=1, required_pairs={},
        )
        assert res.ok is False
        assert any("duplicate" in e.lower() for e in res.errors)

    def test_duplicate_item_id_a_identical_pair_still_fails(self, tmp_path, valid_ids):
        """Identical duplicate row is still a duplicate violation."""
        a_set, b_set = valid_ids
        path = tmp_path / "m.csv"
        _write_csv(path, ["item_id_A", "item_id_B"], [("1", "10"), ("1", "10")])
        res = validate_matches_csv(
            str(path), valid_ids_a=a_set, valid_ids_b=b_set,
            min_rows=1, required_pairs={},
        )
        assert res.ok is False
        assert any("duplicate" in e.lower() for e in res.errors)


# ---------------------------------------------------------------------------
# Required pairs (PDF regression gate)
# ---------------------------------------------------------------------------

class TestRequiredPairs:
    def test_required_pair_satisfied(self, tmp_path, valid_ids):
        a_set, b_set = valid_ids
        path = tmp_path / "m.csv"
        _write_csv(path, ["item_id_A", "item_id_B"], [("1929544", "105624")])
        res = validate_matches_csv(
            str(path), valid_ids_a=a_set, valid_ids_b=b_set,
            min_rows=1, required_pairs={"1929544": "105624"},
        )
        assert res.ok is True

    def test_required_pair_wrong_b_15oz_fails(self, tmp_path, valid_ids):
        a_set, b_set = valid_ids
        path = tmp_path / "m.csv"
        _write_csv(path, ["item_id_A", "item_id_B"], [("1929544", "103620")])
        res = validate_matches_csv(
            str(path), valid_ids_a=a_set, valid_ids_b=b_set,
            min_rows=1, required_pairs={"1929544": "105624"},
        )
        assert res.ok is False
        joined = " | ".join(res.errors)
        assert "1929544" in joined
        assert "105624" in joined
        assert "103620" in joined

    def test_required_pair_wrong_b_29oz_fails(self, tmp_path, valid_ids):
        a_set, b_set = valid_ids
        path = tmp_path / "m.csv"
        _write_csv(path, ["item_id_A", "item_id_B"], [("1929544", "1086860")])
        res = validate_matches_csv(
            str(path), valid_ids_a=a_set, valid_ids_b=b_set,
            min_rows=1, required_pairs={"1929544": "105624"},
        )
        assert res.ok is False
        joined = " | ".join(res.errors)
        assert "1086860" in joined

    def test_required_pair_a_missing_fails(self, tmp_path, valid_ids):
        a_set, b_set = valid_ids
        path = tmp_path / "m.csv"
        _write_csv(path, ["item_id_A", "item_id_B"], [("2197626", "92544")])
        res = validate_matches_csv(
            str(path), valid_ids_a=a_set, valid_ids_b=b_set,
            min_rows=1, required_pairs={"1929544": "105624"},
        )
        assert res.ok is False
        joined = " | ".join(res.errors)
        assert "1929544" in joined


# ---------------------------------------------------------------------------
# min_rows
# ---------------------------------------------------------------------------

class TestMinRows:
    def test_below_min_rows_fails(self, tmp_path, valid_ids):
        a_set, b_set = valid_ids
        path = tmp_path / "m.csv"
        _write_csv(path, ["item_id_A", "item_id_B"], [("1", "10"), ("2", "20")])
        res = validate_matches_csv(
            str(path), valid_ids_a=a_set, valid_ids_b=b_set,
            min_rows=3, required_pairs={},
        )
        assert res.ok is False
        assert any("min_rows" in e.lower() or "rows" in e.lower() for e in res.errors)

    def test_at_min_rows_passes(self, tmp_path, valid_ids):
        a_set, b_set = valid_ids
        path = tmp_path / "m.csv"
        _write_csv(path, ["item_id_A", "item_id_B"], [("1", "10"), ("2", "20"), ("3", "30")])
        res = validate_matches_csv(
            str(path), valid_ids_a=a_set, valid_ids_b=b_set,
            min_rows=3, required_pairs={},
        )
        assert res.ok is True

    def test_function_always_honors_supplied_min_rows(self, tmp_path, valid_ids):
        """validate_matches_csv has no CLI bypass; it always enforces what's passed."""
        a_set, b_set = valid_ids
        path = tmp_path / "m.csv"
        _write_csv(path, ["item_id_A", "item_id_B"], [("1", "10")])
        res = validate_matches_csv(
            str(path), valid_ids_a=a_set, valid_ids_b=b_set,
            min_rows=10, required_pairs={},
        )
        assert res.ok is False


# ---------------------------------------------------------------------------
# write_matches helper
# ---------------------------------------------------------------------------

class TestWriteMatches:
    def test_write_matches_header_and_rows(self, tmp_path):
        path = tmp_path / "out.csv"
        write_matches(str(path), [("1", "10"), ("2", "20")])
        text = path.read_text(encoding="utf-8").splitlines()
        assert text[0] == "item_id_A,item_id_B"
        assert text[1] == "1,10"
        assert text[2] == "2,20"

    def test_write_matches_empty(self, tmp_path):
        path = tmp_path / "out.csv"
        write_matches(str(path), [])
        text = path.read_text(encoding="utf-8").splitlines()
        assert text[0] == "item_id_A,item_id_B"
        assert len(text) == 1
