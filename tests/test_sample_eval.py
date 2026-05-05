"""Regression tests for scripts/sample_eval label-preservation helpers.

Phase 10B-fix: rerunning `python3 scripts/sample_eval.py --mode phase10b`
must not erase prior labels/notes in `eval/manual_eval_phase10b.csv`. The
fix loads existing annotations BEFORE writing the new template and
merges them by (item_id_A, item_id_B). These tests pin the helper
contract so the bug cannot regress.
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import sample_eval  # type: ignore  # noqa: E402


def _write_csv(path: Path, rows):
    fields = list(sample_eval.TEMPLATE_HEADER)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in fields})


def _row(a, b, label="", notes="", sample_type="random_accepted"):
    return {
        "sample_type": sample_type,
        "matchable_group": "pantry",
        "item_id_A": a,
        "item_id_B": b,
        "A_name": f"A-{a}",
        "B_name": f"B-{b}",
        "score": "0.80",
        "margin": "0.10",
        "source": "deterministic",
        "reason": "ok",
        "decision": "accepted",
        "label": label,
        "notes": notes,
    }


def test_load_existing_annotations_returns_empty_when_missing(tmp_path):
    out = sample_eval._load_existing_annotations(tmp_path / "nope.csv")
    assert out == {}


def test_load_existing_annotations_skips_blank_label_and_notes(tmp_path):
    p = tmp_path / "t.csv"
    _write_csv(p, [
        _row("1", "10", label="correct", notes="solid"),
        _row("2", "20", label="", notes=""),
        _row("3", "30", label="", notes="just a note"),
        _row("4", "40", label="wrong", notes=""),
    ])
    ann = sample_eval._load_existing_annotations(p)
    assert set(ann.keys()) == {("1", "10"), ("3", "30"), ("4", "40")}
    assert ann[("1", "10")] == {"label": "correct", "notes": "solid"}
    assert ann[("3", "30")] == {"label": "", "notes": "just a note"}
    assert ann[("4", "40")] == {"label": "wrong", "notes": ""}


def test_load_existing_annotations_lowercases_label(tmp_path):
    p = tmp_path / "t.csv"
    _write_csv(p, [_row("1", "10", label="  CORRECT  ", notes=" hi ")])
    ann = sample_eval._load_existing_annotations(p)
    assert ann[("1", "10")]["label"] == "correct"


def test_merge_existing_annotations_preserves_label_and_notes():
    fresh = [
        _row("1", "10"),
        _row("2", "20"),
        _row("3", "30"),
    ]
    ann = {
        ("1", "10"): {"label": "correct", "notes": "n1"},
        ("3", "30"): {"label": "wrong", "notes": ""},
    }
    merged = sample_eval._merge_existing_annotations(fresh, ann)
    assert merged == 2
    assert fresh[0]["label"] == "correct"
    assert fresh[0]["notes"] == "n1"
    assert fresh[1]["label"] == ""
    assert fresh[1]["notes"] == ""
    assert fresh[2]["label"] == "wrong"
    assert fresh[2]["notes"] == ""


def test_merge_existing_annotations_ignores_unmatched_keys():
    fresh = [_row("1", "10")]
    ann = {("99", "999"): {"label": "correct", "notes": "stale"}}
    merged = sample_eval._merge_existing_annotations(fresh, ann)
    assert merged == 0
    assert fresh[0]["label"] == ""


def test_merge_existing_annotations_does_not_overwrite_with_blank():
    fresh = [_row("1", "10", label="prefilled", notes="prior")]
    ann = {("1", "10"): {"label": "", "notes": ""}}
    sample_eval._merge_existing_annotations(fresh, ann)
    assert fresh[0]["label"] == "prefilled"
    assert fresh[0]["notes"] == "prior"


def test_labels_from_rows_excludes_blanks():
    rows = [
        _row("1", "10", label="correct"),
        _row("2", "20", label=""),
        _row("3", "30", label="  WRONG  "),
    ]
    out = sample_eval._labels_from_rows(rows)
    assert out == {("1", "10"): "correct", ("3", "30"): "wrong"}


def test_round_trip_load_then_merge_preserves_across_regeneration(tmp_path):
    """End-to-end: write a labeled template, regenerate fresh rows, merge.

    Simulates what main() now does: load BEFORE write, merge into the new
    template, then write. Even if the regenerated sample is a different
    ordering, the labeled rows that survive resampling keep their labels.
    """
    p = tmp_path / "t.csv"
    initial = [
        _row("1", "10", label="correct", notes="ok1"),
        _row("2", "20", label="wrong", notes="ok2"),
        _row("3", "30", label="partial"),
    ]
    _write_csv(p, initial)

    prior = sample_eval._load_existing_annotations(p)
    fresh = [_row("3", "30"), _row("1", "10"), _row("4", "40")]
    n = sample_eval._merge_existing_annotations(fresh, prior)
    sample_eval._write_template(p, fresh)

    assert n == 2
    reread = list(csv.DictReader(p.open(encoding="utf-8")))
    by_pair = {(r["item_id_A"], r["item_id_B"]): r for r in reread}
    assert by_pair[("1", "10")]["label"] == "correct"
    assert by_pair[("1", "10")]["notes"] == "ok1"
    assert by_pair[("3", "30")]["label"] == "partial"
    assert by_pair[("4", "40")]["label"] == ""


def test_maybe_load_labels_back_compat_returns_none_when_no_labels(tmp_path):
    p = tmp_path / "t.csv"
    _write_csv(p, [_row("1", "10", label="", notes="just notes")])
    assert sample_eval._maybe_load_labels(p) is None


def test_maybe_load_labels_back_compat_returns_label_only_map(tmp_path):
    p = tmp_path / "t.csv"
    _write_csv(p, [
        _row("1", "10", label="correct"),
        _row("2", "20", label="", notes="x"),
    ])
    out = sample_eval._maybe_load_labels(p)
    assert out == {("1", "10"): "correct"}
