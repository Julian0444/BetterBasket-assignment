"""Phase 9 manual-eval sampling + post-hoc diagnostics for the matcher.

Reads ``matches_audit.csv`` (Phase 7 schema) and the source A / B CSVs.
Enriches audit rows with ``matchable_group`` (A side), names, and
``category_0/1/2`` using the existing ``normalize_product`` /
``assign_matchable_group`` modules. Emits three artifacts:

- ``eval/manual_eval_template.csv``  — 50 random accepted, 50 bottom-Q1
  accepted, 30 near-miss below_threshold, plus the 2 PDF rows appended
  with ``sample_type=pdf_regression``.
- ``eval/group_breakdown.md``        — per-``matchable_group`` precision
  scaffold; recomputed from labels if labels are present on rerun.
- ``eval/phase9_diagnostics.md``     — decision distribution, group
  breakdowns, no-candidates analysis, B coverage gaps, PDF traces, a
  targeted in-process top-50 retrieval+rules+scoring diagnostic for
  A 1929544, and a post-hoc threshold grid derived from the audit.

No full pipeline rerun. No changes to ``matches_audit.csv`` schema.
No changes to ``betterbasket_matcher/*``.
"""
from __future__ import annotations

import argparse
import csv
import random
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

# Make the script runnable from the project root without `pip install -e .`.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from betterbasket_matcher.io import read_products  # noqa: E402
from betterbasket_matcher.normalize import (  # noqa: E402
    NormalizedProduct,
    normalize_product,
)
from betterbasket_matcher.retrieval import TfidfRetriever  # noqa: E402
from betterbasket_matcher.rules import evaluate_hard_rules  # noqa: E402
from betterbasket_matcher.scope import is_a_in_scope  # noqa: E402
from betterbasket_matcher.scoring import score_pair  # noqa: E402
from betterbasket_matcher.taxonomy import assign_matchable_group  # noqa: E402


TEMPLATE_HEADER: Tuple[str, ...] = (
    "sample_type",
    "matchable_group",
    "item_id_A",
    "item_id_B",
    "A_name",
    "B_name",
    "score",
    "margin",
    "source",
    "reason",
    "decision",
    "label",
    "notes",
)

PDF_REQUIRED: Tuple[Tuple[str, str], ...] = (
    ("2197626", "92544"),
    ("1929544", "105624"),
)

NEAR_MISS_SCORE_FLOOR = 0.75
NEAR_MISS_NONBLANK = 20
NEAR_MISS_BLANK = 10

THRESH_SCORES = (0.78, 0.80, 0.82, 0.84)
THRESH_MARGINS = (0.05, 0.08, 0.10)
PIPELINE_MIN_ROWS = 4000

# ---------------------------------------------------------------------------
# Phase 10B sampling configuration
# ---------------------------------------------------------------------------

# (label, score_lo, score_hi_exclusive, quota)
PHASE10B_SCORE_BUCKETS: Tuple[Tuple[str, float, float, int], ...] = (
    ("score_0.55_0.60", 0.55, 0.60, 12),
    ("score_0.60_0.65", 0.60, 0.65, 12),
    ("score_0.65_0.70", 0.65, 0.70, 12),
    ("score_0.70_0.75_threshold_zone", 0.70, 0.75, 18),
    ("score_0.75_0.80", 0.75, 0.80, 12),
    ("score_ge_0.80", 0.80, 1.01, 8),
)
# (group, quota); restricted to score < 0.70
PHASE10B_WEAK_GROUPS: Tuple[Tuple[str, int], ...] = (
    ("household", 3),
    ("frozen", 2),
    ("pantry", 2),
    ("kitchen_home", 2),
    ("seafood", 1),
)
PHASE10B_PL_QUOTA = 6
PHASE10B_THRESH_SCORES = (0.65, 0.70, 0.75, 0.80)
PHASE10B_THRESH_MARGIN = 0.05


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Phase 9 sampling + diagnostics. Enriches matches_audit.csv with "
            "matchable_group / names / categories and writes the manual-eval "
            "template, group-breakdown scaffold, and diagnostics markdown."
        )
    )
    p.add_argument("--a-csv", required=True)
    p.add_argument("--b-csv", required=True)
    p.add_argument("--audit-csv", default="matches_audit.csv")
    p.add_argument("--out-csv", default="eval/manual_eval_template.csv")
    p.add_argument(
        "--group-breakdown-out", default="eval/group_breakdown.md"
    )
    p.add_argument(
        "--diagnostics-out", default="eval/phase9_diagnostics.md"
    )
    p.add_argument("--seed", type=int, default=42)
    p.add_argument(
        "--mode",
        choices=("phase9", "phase10b"),
        default="phase9",
        help=(
            "phase9 (default): legacy 50/50/30+2 buckets and Phase 9 "
            "diagnostics including A 1929544 in-process top-50 trace. "
            "phase10b: score-bucket + weak-group + PL strata for Phase 10B "
            "calibration; lighter calibration diagnostics doc."
        ),
    )
    return p.parse_args(argv)


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def _load_audit(path: str) -> List[Dict[str, str]]:
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _load_source(path: str, source: str) -> Tuple[
    Dict[str, dict],
    Dict[str, NormalizedProduct],
    List[NormalizedProduct],
]:
    """Return (raw_by_id, norm_by_id, normalized_list).

    ``raw_by_id`` keeps the original ``name`` field for the human-readable
    template; ``norm_by_id`` is keyed by ``item_id`` for sampling /
    diagnostics; ``normalized_list`` preserves source order for retrieval
    fitting.
    """
    valid_rows, _ = read_products(path, source)
    raw_by_id: Dict[str, dict] = {r["item_id"]: r for r in valid_rows}
    normalized_list: List[NormalizedProduct] = [
        normalize_product(r, source) for r in valid_rows
    ]
    norm_by_id: Dict[str, NormalizedProduct] = {
        p.item_id: p for p in normalized_list
    }
    return raw_by_id, norm_by_id, normalized_list


# ---------------------------------------------------------------------------
# Sampling helpers
# ---------------------------------------------------------------------------

def _safe_float(value: str) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _group_for_a(a: Optional[NormalizedProduct]) -> Optional[str]:
    if a is None:
        return None
    return assign_matchable_group(a)


def _stratified_round_robin(
    rows: List[Dict[str, str]],
    group_of: Callable[[Dict[str, str]], Optional[str]],
    quota: int,
    rng: random.Random,
) -> List[Dict[str, str]]:
    """Round-robin sampling across groups with a deterministic RNG.

    Each group is shuffled once; we then iterate the sorted group keys
    in a stable order, popping one row per group per pass until we hit
    the quota or every group is empty.
    """
    if quota <= 0 or not rows:
        return []
    by_group: Dict[Optional[str], List[Dict[str, str]]] = defaultdict(list)
    for r in rows:
        by_group[group_of(r)].append(r)
    for k in by_group:
        rng.shuffle(by_group[k])
    keys = sorted(by_group.keys(), key=lambda x: (x is None, str(x)))
    picked: List[Dict[str, str]] = []
    while len(picked) < quota:
        progressed = False
        for k in keys:
            if not by_group[k]:
                continue
            picked.append(by_group[k].pop())
            progressed = True
            if len(picked) >= quota:
                break
        if not progressed:
            break
    return picked


def _bucket_random_accepted(
    audit: List[Dict[str, str]],
    a_norm: Dict[str, NormalizedProduct],
    rng: random.Random,
) -> List[Dict[str, str]]:
    accepted = [r for r in audit if r["decision"] == "accepted"]
    return _stratified_round_robin(
        accepted,
        lambda r: _group_for_a(a_norm.get(r["item_id_A"])),
        50,
        rng,
    )


def _bucket_bottom_q1_accepted(
    audit: List[Dict[str, str]],
    a_norm: Dict[str, NormalizedProduct],
    rng: random.Random,
) -> List[Dict[str, str]]:
    accepted = [r for r in audit if r["decision"] == "accepted"]
    scored = [(r, _safe_float(r["score"])) for r in accepted]
    scored = [(r, s) for r, s in scored if s is not None]
    if len(scored) < 4:
        return []
    scores = sorted(s for _, s in scored)
    q1 = statistics.quantiles(scores, n=4)[0]
    bottom = [r for r, s in scored if s <= q1]
    return _stratified_round_robin(
        bottom,
        lambda r: _group_for_a(a_norm.get(r["item_id_A"])),
        50,
        rng,
    )


def _bucket_near_miss(
    audit: List[Dict[str, str]],
) -> List[Dict[str, str]]:
    near = [r for r in audit if r["decision"] == "below_threshold"]
    near = [
        r for r in near
        if (_safe_float(r["score"]) or 0.0) >= NEAR_MISS_SCORE_FLOOR
    ]
    blank = [r for r in near if r.get("top1_top2_margin", "") == ""]
    nonblank = [r for r in near if r.get("top1_top2_margin", "") != ""]
    nonblank.sort(key=lambda r: _safe_float(r["top1_top2_margin"]) or 0.0)
    blank.sort(key=lambda r: -(_safe_float(r["score"]) or 0.0))
    return nonblank[:NEAR_MISS_NONBLANK] + blank[:NEAR_MISS_BLANK]


def _bucket_pdf(audit: List[Dict[str, str]]) -> List[Dict[str, str]]:
    by_a = {r["item_id_A"]: r for r in audit}
    out: List[Dict[str, str]] = []
    for a_id, _expected_b in PDF_REQUIRED:
        if a_id in by_a:
            out.append(by_a[a_id])
        else:
            out.append({
                "item_id_A": a_id,
                "item_id_B": "",
                "score": "",
                "retrieval_score": "",
                "top1_top2_margin": "",
                "source": "deterministic",
                "decision": "absent_from_audit",
                "reason": "absent_from_audit",
                "llm_confidence": "",
            })
    return out


# ---------------------------------------------------------------------------
# Audit row -> template row
# ---------------------------------------------------------------------------

def _to_template_row(
    audit_row: Dict[str, str],
    sample_type: str,
    a_raw: Dict[str, dict],
    b_raw: Dict[str, dict],
    a_norm: Dict[str, NormalizedProduct],
) -> Dict[str, str]:
    a_id = audit_row["item_id_A"]
    b_id = audit_row.get("item_id_B", "") or ""
    a_n = a_norm.get(a_id)
    return {
        "sample_type": sample_type,
        "matchable_group": _group_for_a(a_n) or "",
        "item_id_A": a_id,
        "item_id_B": b_id,
        "A_name": (a_raw.get(a_id, {}) or {}).get("name", "") or "",
        "B_name": (b_raw.get(b_id, {}) or {}).get("name", "") if b_id else "",
        "score": audit_row.get("score", "") or "",
        "margin": audit_row.get("top1_top2_margin", "") or "",
        "source": audit_row.get("source", "") or "",
        "reason": audit_row.get("reason", "") or "",
        "decision": audit_row.get("decision", "") or "",
        "label": "",
        "notes": "",
    }


def _dedup_preserve_pdf(
    samples: List[Dict[str, str]],
    pdf: List[Dict[str, str]],
) -> List[Dict[str, str]]:
    """Drop duplicate (item_id_A, item_id_B) from samples; PDF rows always kept."""
    pdf_keys = {(r["item_id_A"], r["item_id_B"]) for r in pdf}
    seen: set = set(pdf_keys)
    deduped: List[Dict[str, str]] = []
    for r in samples:
        key = (r["item_id_A"], r["item_id_B"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(r)
    return deduped + pdf


# ---------------------------------------------------------------------------
# Template + manual-eval writers
# ---------------------------------------------------------------------------

def _write_template(path: Path, rows: List[Dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(TEMPLATE_HEADER))
        writer.writeheader()
        for r in rows:
            writer.writerow({col: r.get(col, "") for col in TEMPLATE_HEADER})


def _write_manual_eval_md(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = """# Phase 9 Manual Evaluation Guide

## Purpose

`manual_eval_template.csv` is a stratified sample of the Phase 8 matcher
output. Hand-labeling these rows is the precision/recall ground truth
that drives Phase 9 threshold calibration. Per-`matchable_group`
precision is what matters: the global accepted count (524) is too low to
hit the 4,000-row floor on its own, so calibration must be evidence-based
per group rather than a single global threshold tweak.

## Label values

Use exactly one of these in the `label` column:

- `correct` — A and B are the same product as a customer would consider
  it. Same brand-equivalence, same form, same flavor, same usable size.
  Pack count differences within the same total size are acceptable as
  `correct` only if a customer would treat them as interchangeable.
- `wrong`   — A and B are clearly different products. Different brand
  family without a private-label bridge, different size in a way that
  matters for purchase intent, different flavor, different form.
- `partial` — A and B are in the same product family but ambiguous. Same
  category and brand bridge, but a meaningful attribute differs (e.g.
  organic vs. non-organic, light vs. regular, scent variants) and a
  shopper would only sometimes accept the substitution.
- `unsure`  — Not enough information in `A_name` / `B_name` to decide.
  Use sparingly; prefer to look up the product before falling back here.

`notes` is free text. Useful for capturing the reason a row was
labeled `wrong` or `partial`, or any data-quality observation worth
feeding back into rules / retrieval.

## How to fill the template

1. Open `eval/manual_eval_template.csv` in a CSV-aware editor.
2. For each row, read `A_name` and `B_name`. The `score`, `margin`, and
   `reason` columns are diagnostic context, not labels.
3. Set `label` to `correct`, `wrong`, `partial`, or `unsure`. Leave
   blank only if you intend to skip the row.
4. Optionally fill `notes` with a one-line rationale.
5. Save the file in place.
6. Re-run `python3 scripts/sample_eval.py ...` with the same arguments
   and `eval/group_breakdown.md` will be regenerated with the per-group
   counts and an `est_precision` column computed from your labels.

## Column reference

- `sample_type` — bucket the row was drawn from:
  - `random_accepted`         (50 rows, stratified by matchable_group)
  - `bottom_q1_accepted`      (50 rows, stratified by matchable_group;
    drawn from the bottom 25% of accepted scores only)
  - `near_miss_below_threshold` (30 rows; 20 with smallest non-blank
    margin, 10 with blank margin; both gated at `score >= 0.75`)
  - `pdf_regression`          (2 rows, the assignment's required pairs;
    appended in addition to the 130 sampled rows)
- `matchable_group` — taxonomy group assigned to the A item via
  `taxonomy.assign_matchable_group`. May be empty when the A row was
  excluded by scope or had no group assignment.
- `item_id_A` / `item_id_B` — raw IDs from the source CSVs; numeric.
- `A_name` / `B_name` — original `name` field from each source CSV.
- `score`  — Phase 6 weighted score. Empty for `no_candidates` rows.
- `margin` — `top1_top2_margin` from the audit. **Sourced from the
  audit's `top1_top2_margin` column**, not recomputed. Blank when only
  one survivor cleared the hard rules (no runner-up to compare to).
- `source` — `deterministic` for every Phase 8 row.
- `reason` — selection reason from the audit (`ok`, `below_min_score`,
  `below_min_margin`, `brand_mismatch`, ...).
- `decision` — `accepted` / `below_threshold` / `rejected_by_rule` /
  `no_candidates`.

## Why per-group precision matters more than global precision

Phase 8 produced 524 accepted rows out of 233,194 valid A items. The
distribution across `matchable_group` is heavily skewed: a global
threshold like `min_score=0.82` may be too tight in groups with strong
size signals (where false positives are rare even at lower scores) and
too loose in groups dominated by ambiguous private-label items (where
brand suppression already lifts borderline scores). Phase 9 calibration
needs per-group precision before lowering or raising any global floor;
otherwise a global change can simultaneously over-accept in one group
and under-accept in another while the global precision number looks
unchanged.
"""
    path.write_text(body, encoding="utf-8")


# ---------------------------------------------------------------------------
# group_breakdown.md
# ---------------------------------------------------------------------------

def _load_existing_annotations(
    path: Path,
) -> Dict[Tuple[str, str], Dict[str, str]]:
    """Read existing label+notes from a previously written template.

    Returns a mapping keyed by (item_id_A, item_id_B). Each value is a dict
    with `label` (lowercased, stripped) and `notes` (raw, stripped of
    trailing whitespace). Rows with empty label AND empty notes are
    skipped so re-runs do not propagate noise. Missing file -> empty dict.
    """
    annotations: Dict[Tuple[str, str], Dict[str, str]] = {}
    if not path.exists():
        return annotations
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for r in reader:
            a_id = (r.get("item_id_A") or "").strip()
            b_id = (r.get("item_id_B") or "").strip()
            if not a_id:
                continue
            lbl = (r.get("label") or "").strip().lower()
            notes = (r.get("notes") or "").rstrip()
            if not lbl and not notes:
                continue
            annotations[(a_id, b_id)] = {"label": lbl, "notes": notes}
    return annotations


def _merge_existing_annotations(
    template_rows: List[Dict[str, str]],
    annotations: Dict[Tuple[str, str], Dict[str, str]],
) -> int:
    """Merge prior label/notes into freshly built template rows in place.

    Match key is (item_id_A, item_id_B). Returns the number of template
    rows that received a non-empty label OR notes from `annotations`.
    Annotations whose key is no longer present in the regenerated sample
    are silently dropped (the row was deduped out or the audit changed);
    this is the documented expectation for stratified resampling.
    """
    if not annotations:
        return 0
    merged = 0
    for r in template_rows:
        key = (r.get("item_id_A", ""), r.get("item_id_B", ""))
        ann = annotations.get(key)
        if ann is None:
            continue
        if ann.get("label"):
            r["label"] = ann["label"]
        if ann.get("notes"):
            r["notes"] = ann["notes"]
        if ann.get("label") or ann.get("notes"):
            merged += 1
    return merged


def _labels_from_rows(
    rows: List[Dict[str, str]],
) -> Dict[Tuple[str, str], str]:
    """Extract a (item_id_A, item_id_B) -> label map from template rows.

    Only rows with a non-empty `label` are included; this is the same
    contract Phase 9/10B writers expect from `_maybe_load_labels`.
    """
    out: Dict[Tuple[str, str], str] = {}
    for r in rows:
        lbl = (r.get("label") or "").strip().lower()
        if lbl:
            out[(r.get("item_id_A", ""), r.get("item_id_B", ""))] = lbl
    return out


def _maybe_load_labels(path: Path) -> Optional[Dict[Tuple[str, str], str]]:
    """Back-compat shim: returns label-only map or None if file/labels absent."""
    annotations = _load_existing_annotations(path)
    labels = {
        key: ann["label"]
        for key, ann in annotations.items()
        if ann.get("label")
    }
    return labels or None


def _write_group_breakdown(
    path: Path,
    audit: List[Dict[str, str]],
    sample_rows: List[Dict[str, str]],
    a_norm: Dict[str, NormalizedProduct],
    existing_labels: Optional[Dict[Tuple[str, str], str]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    accepted_by_group: Counter = Counter()
    for r in audit:
        if r["decision"] != "accepted":
            continue
        g = _group_for_a(a_norm.get(r["item_id_A"])) or "(none)"
        accepted_by_group[g] += 1

    sampled_by_group: Counter = Counter()
    for r in sample_rows:
        if r["sample_type"] in ("random_accepted", "bottom_q1_accepted"):
            sampled_by_group[r["matchable_group"] or "(none)"] += 1

    label_buckets: Dict[str, Counter] = defaultdict(Counter)
    if existing_labels:
        sample_index = {
            (r["item_id_A"], r["item_id_B"]): r for r in sample_rows
        }
        for key, lbl in existing_labels.items():
            r = sample_index.get(key)
            if r is None:
                continue
            if r["sample_type"] not in ("random_accepted", "bottom_q1_accepted"):
                continue
            g = r["matchable_group"] or "(none)"
            label_buckets[g][lbl] += 1

    lines: List[str] = []
    lines.append("# Phase 9 Per-Group Precision Scaffold")
    lines.append("")
    if existing_labels:
        lines.append(
            f"Computed from {len(existing_labels)} labels in "
            "`eval/manual_eval_template.csv` (provisional unless these were "
            "produced or confirmed by a human reviewer). Re-run "
            "`sample_eval.py` after editing labels to refresh this table; "
            "the sampler now preserves prior labels and notes across reruns."
        )
    else:
        lines.append(
            "Labels not yet filled — `correct` / `wrong` / `partial` / "
            "`unsure` columns and `est_precision` are TBD until "
            "`eval/manual_eval_template.csv` is labeled (AI-assisted or "
            "human). Re-run `sample_eval.py` after labeling to populate them."
        )
    lines.append("")
    lines.append(
        "| matchable_group | accepted_count | sampled | correct | wrong | "
        "partial | unsure | est_precision |"
    )
    lines.append(
        "|---|---:|---:|---:|---:|---:|---:|---:|"
    )

    all_groups = sorted(
        set(accepted_by_group) | set(sampled_by_group) | set(label_buckets),
        key=lambda x: (-accepted_by_group.get(x, 0), x),
    )
    total_acc = sum(accepted_by_group.values())
    total_samp = sum(sampled_by_group.values())
    total_correct = total_wrong = total_partial = total_unsure = 0

    for g in all_groups:
        acc = accepted_by_group.get(g, 0)
        samp = sampled_by_group.get(g, 0)
        if existing_labels is None:
            row = (
                f"| {g} | {acc} | {samp} | TBD | TBD | TBD | TBD | TBD |"
            )
        else:
            c = label_buckets[g].get("correct", 0)
            w = label_buckets[g].get("wrong", 0)
            pa = label_buckets[g].get("partial", 0)
            u = label_buckets[g].get("unsure", 0)
            denom = c + w + pa  # exclude unsure from precision
            est = f"{c / denom:.3f}" if denom > 0 else "TBD"
            total_correct += c
            total_wrong += w
            total_partial += pa
            total_unsure += u
            row = (
                f"| {g} | {acc} | {samp} | {c} | {w} | {pa} | {u} | {est} |"
            )
        lines.append(row)

    if existing_labels is None:
        lines.append(
            f"| **TOTAL** | **{total_acc}** | **{total_samp}** | TBD | TBD | "
            "TBD | TBD | TBD |"
        )
    else:
        denom_t = total_correct + total_wrong + total_partial
        est_t = (
            f"{total_correct / denom_t:.3f}" if denom_t > 0 else "TBD"
        )
        lines.append(
            f"| **TOTAL** | **{total_acc}** | **{total_samp}** | "
            f"**{total_correct}** | **{total_wrong}** | **{total_partial}** | "
            f"**{total_unsure}** | **{est_t}** |"
        )

    lines.append("")
    lines.append(
        "Notes: `est_precision = correct / (correct + wrong + partial)` — "
        "`unsure` rows are excluded from the denominator. `partial` is "
        "treated as a precision miss; loosen this if Phase 9 calibration "
        "decides to admit partials as acceptable substitutions."
    )
    lines.append("")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Threshold grid (post-hoc; no rerun)
# ---------------------------------------------------------------------------

def _threshold_grid(
    audit: List[Dict[str, str]],
) -> List[Dict[str, object]]:
    """For each (min_score, min_margin), simulate accept/reject from the
    audit's already-chosen B per A. Rows with no score (no_candidates,
    rejected_by_rule) cannot become accepted under any threshold."""
    candidates: List[Tuple[str, str, float, Optional[float]]] = []
    for r in audit:
        if r["decision"] not in ("accepted", "below_threshold"):
            continue
        s = _safe_float(r["score"])
        if s is None:
            continue
        m = _safe_float(r["top1_top2_margin"])
        candidates.append((r["item_id_A"], r["item_id_B"] or "", s, m))

    out: List[Dict[str, object]] = []
    for ms in THRESH_SCORES:
        for mm in THRESH_MARGINS:
            n = 0
            pdf1 = pdf2 = False
            for a_id, b_id, s, m in candidates:
                if s < ms:
                    continue
                if m is not None and m < mm:
                    continue
                n += 1
                if a_id == "2197626" and b_id == "92544":
                    pdf1 = True
                if a_id == "1929544" and b_id == "105624":
                    pdf2 = True
            out.append({
                "min_score": ms,
                "min_margin": mm,
                "accepted_count": n,
                "pdf1_admit": pdf1,
                "pdf2_admit": pdf2,
                "clears_4000_floor": n >= PIPELINE_MIN_ROWS,
            })
    return out


# ---------------------------------------------------------------------------
# Targeted A 1929544 diagnostic
# ---------------------------------------------------------------------------

def _targeted_a1929544(
    a_norm: Dict[str, NormalizedProduct],
    b_norm_by_id: Dict[str, NormalizedProduct],
    b_normalized_list: List[NormalizedProduct],
) -> Dict[str, object]:
    a_id = "1929544"
    expected_b = "105624"
    out: Dict[str, object] = {
        "a_id": a_id,
        "expected_b": expected_b,
        "a_present": False,
        "b_expected_present_in_corpus": expected_b in b_norm_by_id,
        "a_in_scope": None,
        "scope_reason": "",
        "candidates": [],
        "expected_in_top50": None,
        "expected_rank": None,
    }
    a = a_norm.get(a_id)
    if a is None:
        return out
    out["a_present"] = True
    in_scope, scope_reason = is_a_in_scope(a)
    out["a_in_scope"] = in_scope
    out["scope_reason"] = scope_reason
    if not in_scope:
        return out

    retriever = TfidfRetriever().fit(b_normalized_list)
    cands = retriever.query(a, k=50)
    rows: List[Dict[str, object]] = []
    for c in cands:
        b = b_norm_by_id.get(c.item_id_b)
        if b is None:
            continue
        rule = evaluate_hard_rules(a, b)
        breakdown = (
            score_pair(a, b, c.score) if rule.passed else None
        )
        rows.append({
            "rank": c.rank,
            "item_id_b": c.item_id_b,
            "b_name": b.core_name or "",
            "retrieval_score": c.score,
            "rule_passed": rule.passed,
            "rule_reason": rule.reason,
            "score": (breakdown.total if breakdown is not None else None),
        })
    out["candidates"] = rows
    for r in rows:
        if r["item_id_b"] == expected_b:
            out["expected_in_top50"] = True
            out["expected_rank"] = r["rank"]
            break
    if out["expected_in_top50"] is None:
        out["expected_in_top50"] = False
    return out


# ---------------------------------------------------------------------------
# phase9_diagnostics.md
# ---------------------------------------------------------------------------

def _section(lines: List[str], title: str) -> None:
    lines.append("")
    lines.append(f"## {title}")
    lines.append("")


def _table(lines: List[str], header: List[str], rows: List[List[str]]) -> None:
    lines.append("| " + " | ".join(header) + " |")
    aligns = [":---" for _ in header]
    lines.append("|" + "|".join(aligns) + "|")
    for r in rows:
        lines.append("| " + " | ".join(r) + " |")


def _diag_decision_distribution(audit: List[Dict[str, str]]) -> List[List[str]]:
    c: Counter = Counter(r["decision"] for r in audit)
    total = sum(c.values())
    return [
        [k, str(c[k]), f"{(c[k] / total * 100 if total else 0):.2f}%"]
        for k in sorted(c, key=lambda x: -c[x])
    ]


def _diag_by_group(
    audit: List[Dict[str, str]],
    decision: str,
    a_norm: Dict[str, NormalizedProduct],
) -> List[List[str]]:
    c: Counter = Counter()
    for r in audit:
        if r["decision"] != decision:
            continue
        g = _group_for_a(a_norm.get(r["item_id_A"])) or "(none)"
        c[g] += 1
    return [
        [k, str(c[k])] for k in sorted(c, key=lambda x: (-c[x], x))
    ]


def _diag_no_cand_by_reason(audit: List[Dict[str, str]]) -> List[List[str]]:
    c: Counter = Counter()
    for r in audit:
        if r["decision"] == "no_candidates":
            c[r["reason"] or "(none)"] += 1
    return [
        [k, str(c[k])] for k in sorted(c, key=lambda x: -c[x])
    ]


def _diag_in_scope_no_cand(
    audit: List[Dict[str, str]],
    a_norm: Dict[str, NormalizedProduct],
    top_n: int = 30,
) -> Tuple[List[List[str]], List[List[str]]]:
    """Return (by_category_rows, by_group_rows) for in-scope no_candidates."""
    cat_counter: Counter = Counter()
    grp_counter: Counter = Counter()
    for r in audit:
        if r["decision"] != "no_candidates":
            continue
        if (r.get("reason") or "") != "no_candidates":
            continue  # exclude excluded_category / excluded_home_decor
        a = a_norm.get(r["item_id_A"])
        if a is None:
            continue
        c0 = a.category_0 or "(none)"
        c1 = a.category_1 or "(none)"
        c2 = a.category_2 or "(none)"
        cat_counter[(c0, c1, c2)] += 1
        grp_counter[_group_for_a(a) or "(none)"] += 1
    cat_rows = [
        [c0, c1, c2, str(n)]
        for (c0, c1, c2), n in cat_counter.most_common(top_n)
    ]
    grp_rows = [
        [g, str(n)] for g, n in sorted(
            grp_counter.items(), key=lambda kv: (-kv[1], kv[0])
        )
    ]
    return cat_rows, grp_rows


def _diag_b_coverage(
    b_normalized_list: List[NormalizedProduct],
    top_n: int = 30,
) -> Tuple[List[List[str]], List[List[str]]]:
    cat_counter: Counter = Counter()
    grp_counter: Counter = Counter()
    for b in b_normalized_list:
        g = assign_matchable_group(b)
        grp_counter[g or "(none)"] += 1
        if g is None:
            cat_counter[(
                b.category_0 or "(none)",
                b.category_1 or "(none)",
                b.category_2 or "(none)",
            )] += 1
    cat_rows = [
        [c0, c1, c2, str(n)]
        for (c0, c1, c2), n in cat_counter.most_common(top_n)
    ]
    grp_rows = [
        [g, str(n)] for g, n in sorted(
            grp_counter.items(), key=lambda kv: (-kv[1], kv[0])
        )
    ]
    return cat_rows, grp_rows


def _format_audit_row(r: Dict[str, str]) -> List[str]:
    return [
        r.get("item_id_A", ""),
        r.get("item_id_B", "") or "(blank)",
        r.get("decision", ""),
        r.get("reason", ""),
        r.get("score", "") or "(blank)",
        r.get("retrieval_score", "") or "(blank)",
        r.get("top1_top2_margin", "") or "(blank)",
    ]


def _write_diagnostics(
    path: Path,
    audit: List[Dict[str, str]],
    a_norm: Dict[str, NormalizedProduct],
    b_norm_by_id: Dict[str, NormalizedProduct],
    b_normalized_list: List[NormalizedProduct],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: List[str] = []
    lines.append("# Phase 9 Diagnostics")
    lines.append("")
    lines.append(
        "All numbers below are derived from `matches_audit.csv` (Phase 8 "
        "full run) and the source A / B CSVs. No pipeline rerun. Audit "
        "schema is the locked Phase 7 9-column shape; enrichment uses "
        "`normalize_product` + `assign_matchable_group` in-process."
    )

    _section(lines, "1. Decision distribution")
    _table(lines, ["decision", "count", "share"],
           _diag_decision_distribution(audit))

    _section(lines, "2. Accepted count by matchable_group (A side)")
    _table(lines, ["matchable_group", "accepted_count"],
           _diag_by_group(audit, "accepted", a_norm))

    _section(lines, "3. Below-threshold count by matchable_group (A side)")
    _table(lines, ["matchable_group", "below_threshold_count"],
           _diag_by_group(audit, "below_threshold", a_norm))

    _section(lines, "4. No-candidates count by reason")
    _table(lines, ["reason", "count"], _diag_no_cand_by_reason(audit))
    lines.append("")
    lines.append(
        "`no_candidates` (reason) means the A row was in scope but TF-IDF "
        "retrieval returned 0 group-compatible Bs. `excluded_category` and "
        "`excluded_home_decor` are scope-filter rejections and are not "
        "candidates for any threshold change."
    )

    _section(lines, "5. In-scope no_candidates: A category breakdown + group")
    cat_rows, grp_rows = _diag_in_scope_no_cand(audit, a_norm)
    lines.append("**Top A category triples driving the 142,211 in-scope "
                 "zero-candidate rows (top 30):**")
    lines.append("")
    _table(lines, ["category_0", "category_1", "category_2", "count"], cat_rows)
    lines.append("")
    lines.append("**Same rows aggregated by assigned A matchable_group:**")
    lines.append("")
    _table(lines, ["assigned_a_group", "count"], grp_rows)

    _section(lines, "6. B coverage: matchable_group=None grouped by category")
    b_cat_rows, b_grp_rows = _diag_b_coverage(b_normalized_list)
    lines.append("**B group distribution (all B rows):**")
    lines.append("")
    _table(lines, ["matchable_group", "b_count"], b_grp_rows)
    lines.append("")
    lines.append("**Top B category triples that resolve to "
                 "`matchable_group=None` (top 30):**")
    lines.append("")
    _table(lines, ["category_0", "category_1", "category_2", "count"],
           b_cat_rows)

    _section(lines, "7. PDF regression traces")
    by_a = {r["item_id_A"]: r for r in audit}
    pdf_rows: List[List[str]] = []
    for a_id, expected_b in PDF_REQUIRED:
        r = by_a.get(a_id)
        if r is None:
            pdf_rows.append([a_id, "(missing from audit)", "", "", "", "", ""])
        else:
            pdf_rows.append(_format_audit_row(r))
    _table(lines,
           ["item_id_A", "item_id_B (chosen)", "decision", "reason",
            "score", "retrieval_score", "top1_top2_margin"],
           pdf_rows)
    lines.append("")
    lines.append("Required pairs (assignment): A 2197626 -> B 92544; "
                 "A 1929544 -> B 105624.")

    _section(lines, "7b. Targeted A 1929544 in-process top-50 diagnostic")
    diag = _targeted_a1929544(a_norm, b_norm_by_id, b_normalized_list)
    lines.append(f"- A 1929544 present in normalized A index: "
                 f"{diag['a_present']}.")
    lines.append(f"- Expected B 105624 present in normalized B corpus: "
                 f"{diag['b_expected_present_in_corpus']}.")
    lines.append(f"- A 1929544 in scope: {diag['a_in_scope']} "
                 f"(reason `{diag['scope_reason']}`).")
    lines.append(f"- Expected B 105624 in top-50: "
                 f"{diag['expected_in_top50']} (rank "
                 f"{diag['expected_rank']}).")
    lines.append("")
    lines.append("**Top-50 candidates with rule outcome and final score "
                 "where rules pass (sorted by retrieval_score desc):**")
    lines.append("")
    cand_rows: List[List[str]] = []
    for r in diag["candidates"]:
        cand_rows.append([
            str(r["rank"]),
            str(r["item_id_b"]),
            (r["b_name"] or "")[:80],
            f"{r['retrieval_score']:.4f}",
            "PASS" if r["rule_passed"] else "FAIL",
            r["rule_reason"],
            f"{r['score']:.4f}" if r["score"] is not None else "(rule-rejected)",
        ])
    _table(lines,
           ["rank", "item_id_B", "B_core_name", "retrieval_score",
            "rule", "rule_reason", "final_score"],
           cand_rows)

    _section(lines, "8. Post-hoc threshold grid (no rerun)")
    lines.append(
        "For each `(min_score, min_margin)` cell, the count is the number "
        "of audit rows with `decision in {accepted, below_threshold}` whose "
        "stored `score` and `top1_top2_margin` would clear that cell. "
        "Rows with `decision=no_candidates` or `decision=rejected_by_rule` "
        "have no score and cannot be admitted by any threshold change."
    )
    lines.append("")
    grid_rows: List[List[str]] = []
    for cell in _threshold_grid(audit):
        grid_rows.append([
            f"{cell['min_score']:.2f}",
            f"{cell['min_margin']:.2f}",
            str(cell["accepted_count"]),
            "Y" if cell["pdf1_admit"] else "N",
            "Y" if cell["pdf2_admit"] else "N",
            "Y" if cell["clears_4000_floor"] else "N",
        ])
    _table(lines,
           ["min_score", "min_margin", "accepted_count",
            "pdf1_admit (2197626->92544)",
            "pdf2_admit (1929544->105624)",
            "clears_4000_floor"],
           grid_rows)
    lines.append("")
    lines.append(
        "**Audit limitation:** the grid only sees the *single chosen B per A* "
        "that the Phase 8 algorithm picked. It cannot model what would have "
        "happened if a different B had won under different rules or "
        "retrieval. So PDF-2 (`A 1929544 -> B 105624`) can flip from N to Y "
        "in this grid only if B 105624 was already the chosen survivor in "
        "the audit; if a different B (e.g. 97690) won, lowering thresholds "
        "embeds the wrong answer rather than fixing it."
    )

    _section(lines, "9. Failure-mode classification")
    lines.append(
        "Combining the sections above, the Phase 8 output-contract failure "
        "is **a combination, dominated by retrieval coverage**, not a "
        "threshold-only problem:"
    )
    lines.append("")
    lines.append(
        "- **Coverage / taxonomy**: 142,211 in-scope A rows produced zero "
        "B candidates. Section 5 / 6 identify which A and B category triples "
        "drive this. Until `assign_matchable_group` covers more of those "
        "triples (and / or `groups_compatible` becomes less conservative on "
        "specific grocery pairs), no threshold change can reach the 4,000 "
        "row floor."
    )
    lines.append(
        "- **Threshold sensitivity** (section 8): even the most permissive "
        "cell in the grid is bounded by the `accepted + below_threshold` "
        "row count (524 + 29,894 = 30,418). The grid shows where the "
        "4,000-row floor is actually reachable from the existing audit "
        "data; below_threshold rows are the only relaxation lever."
    )
    lines.append(
        "- **PDF-2 retrieval / rule interaction**: section 7b shows whether "
        "B 105624 ever reaches A 1929544's top 50 and which rule, if any, "
        "rejects it. If it is absent or rule-rejected, no threshold change "
        "can fix PDF-2 — that case requires Phase 10 work on retrieval "
        "shaping or hard-rule loosening for the canned-tomato shape, which "
        "is explicitly out of Phase 9 scope."
    )
    lines.append(
        "- **Hard-rule pruning**: brand_mismatch + national_vs_private_label "
        "account for ~75% of `rejected_by_rule`. Per-group precision from "
        "the manual eval (group_breakdown.md) should validate these "
        "rejections are precision-correct before any rule change is "
        "proposed in Phase 10."
    )

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Phase 10B sampling
# ---------------------------------------------------------------------------

def _stratified_round_robin_shuffled(
    rows: List[Dict[str, str]],
    group_of: Callable[[Dict[str, str]], Optional[str]],
    quota: int,
    rng: random.Random,
) -> List[Dict[str, str]]:
    """Round-robin like _stratified_round_robin, but the *iteration order
    over groups* is randomized per call so quotas smaller than the number
    of groups don't always favor the alphabetically-first groups.
    """
    if quota <= 0 or not rows:
        return []
    by_group: Dict[Optional[str], List[Dict[str, str]]] = defaultdict(list)
    for r in rows:
        by_group[group_of(r)].append(r)
    for k in by_group:
        rng.shuffle(by_group[k])
    keys = list(by_group.keys())
    rng.shuffle(keys)
    picked: List[Dict[str, str]] = []
    while len(picked) < quota:
        progressed = False
        for k in keys:
            if not by_group[k]:
                continue
            picked.append(by_group[k].pop())
            progressed = True
            if len(picked) >= quota:
                break
        if not progressed:
            break
    return picked


def _build_phase10b_samples(
    audit: List[Dict[str, str]],
    a_norm: Dict[str, NormalizedProduct],
    rng: random.Random,
) -> List[Tuple[str, Dict[str, str]]]:
    """Return [(sample_type, audit_row), ...] for Phase 10B strata.

    Strata: 6 score buckets (round-robin by matchable_group), 5 weak-group
    quotas restricted to score < 0.70, and a private-label cross-store
    bucket. Dedup is performed by the caller via _dedup_preserve_pdf.
    """
    accepted = [r for r in audit if r["decision"] == "accepted"]
    out: List[Tuple[str, Dict[str, str]]] = []

    # Score buckets, group-stratified within each bucket.
    for label, lo, hi, quota in PHASE10B_SCORE_BUCKETS:
        in_bucket = []
        for r in accepted:
            s = _safe_float(r["score"])
            if s is None:
                continue
            if not (lo <= s < hi):
                continue
            in_bucket.append(r)
        picked = _stratified_round_robin_shuffled(
            in_bucket,
            lambda r: _group_for_a(a_norm.get(r["item_id_A"])),
            quota,
            rng,
        )
        for r in picked:
            out.append((label, r))

    # Weak-group oversample, restricted to score < 0.70.
    for grp, quota in PHASE10B_WEAK_GROUPS:
        eligible = []
        for r in accepted:
            s = _safe_float(r["score"])
            if s is None or s >= 0.70:
                continue
            g = _group_for_a(a_norm.get(r["item_id_A"]))
            if g == grp:
                eligible.append(r)
        rng.shuffle(eligible)
        for r in eligible[:quota]:
            out.append((f"weak_{grp}", r))

    # Private-label cross-store, score in [0.55, 0.80), group-stratified.
    pl_eligible = []
    for r in accepted:
        s = _safe_float(r["score"])
        if s is None or not (0.55 <= s < 0.80):
            continue
        a = a_norm.get(r["item_id_A"])
        if a is not None and a.is_private_label:
            pl_eligible.append(r)
    pl_picks = _stratified_round_robin_shuffled(
        pl_eligible,
        lambda r: _group_for_a(a_norm.get(r["item_id_A"])),
        PHASE10B_PL_QUOTA,
        rng,
    )
    for r in pl_picks:
        out.append(("private_label_cross_store", r))

    return out


def _phase10b_threshold_grid(
    audit: List[Dict[str, str]],
) -> List[Dict[str, object]]:
    """Recompute exact threshold counts for the Phase 10B candidate cells."""
    rows: List[Tuple[str, str, float, Optional[float]]] = []
    for r in audit:
        if r["decision"] not in ("accepted", "below_threshold"):
            continue
        s = _safe_float(r["score"])
        if s is None:
            continue
        m = _safe_float(r["top1_top2_margin"])
        rows.append((r["item_id_A"], r["item_id_B"] or "", s, m))

    out: List[Dict[str, object]] = []
    for ms in PHASE10B_THRESH_SCORES:
        mm = PHASE10B_THRESH_MARGIN
        n = 0
        pdf1 = pdf2 = False
        for a_id, b_id, s, m in rows:
            if s < ms:
                continue
            if m is not None and m < mm:
                continue
            n += 1
            if a_id == "2197626" and b_id == "92544":
                pdf1 = True
            if a_id == "1929544" and b_id == "105624":
                pdf2 = True
        out.append({
            "min_score": ms,
            "min_margin": mm,
            "accepted_count": n,
            "pdf1_admit": pdf1,
            "pdf2_admit": pdf2,
            "clears_4000_floor": n >= PIPELINE_MIN_ROWS,
        })
    return out


def _phase10b_score_bucket(score: Optional[float]) -> Optional[str]:
    if score is None:
        return None
    for label, lo, hi, _q in PHASE10B_SCORE_BUCKETS:
        if lo <= score < hi:
            return label
    return None


def _write_group_breakdown_phase10b(
    path: Path,
    audit: List[Dict[str, str]],
    sample_rows: List[Dict[str, str]],
    a_norm: Dict[str, NormalizedProduct],
    existing_labels: Optional[Dict[Tuple[str, str], str]],
) -> None:
    """Phase 10B per-group precision table.

    Counts every sample row except `pdf_regression` (PDFs are not part of
    the precision denominator; they are output-contract gates).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    accepted_by_group: Counter = Counter()
    for r in audit:
        if r["decision"] != "accepted":
            continue
        g = _group_for_a(a_norm.get(r["item_id_A"])) or "(none)"
        accepted_by_group[g] += 1

    sampled_by_group: Counter = Counter()
    for r in sample_rows:
        if r["sample_type"] == "pdf_regression":
            continue
        sampled_by_group[r["matchable_group"] or "(none)"] += 1

    label_buckets: Dict[str, Counter] = defaultdict(Counter)
    if existing_labels:
        sample_index = {
            (r["item_id_A"], r["item_id_B"]): r for r in sample_rows
        }
        for key, lbl in existing_labels.items():
            r = sample_index.get(key)
            if r is None:
                continue
            if r["sample_type"] == "pdf_regression":
                continue
            g = r["matchable_group"] or "(none)"
            label_buckets[g][lbl] += 1

    lines: List[str] = []
    lines.append("# Phase 10B Per-Group Precision")
    lines.append("")
    if existing_labels:
        lines.append(
            f"Computed from {len(existing_labels)} **AI-assisted preliminary "
            "labels (Codex)** in `eval/manual_eval_phase10b.csv`. These "
            "labels are not human-reviewed; precision numbers below are "
            "**provisional** until a human pass edits the CSV. `partial` is "
            "treated as a precision miss; `unsure` is excluded from the "
            "denominator. Re-run `sample_eval.py --mode phase10b` after "
            "edits to refresh — the sampler now preserves prior labels and "
            "notes across reruns."
        )
    else:
        lines.append(
            "Labels not yet filled — `est_precision` columns will populate "
            "after `eval/manual_eval_phase10b.csv` is labeled (AI-assisted "
            "or human) and the sampler is rerun in `--mode phase10b`."
        )
    lines.append("")
    lines.append(
        "| matchable_group | accepted_count | sampled | correct | wrong | "
        "partial | unsure | est_precision |"
    )
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|")

    all_groups = sorted(
        set(accepted_by_group) | set(sampled_by_group) | set(label_buckets),
        key=lambda x: (-accepted_by_group.get(x, 0), x),
    )
    total_acc = sum(accepted_by_group.values())
    total_samp = sum(sampled_by_group.values())
    total_correct = total_wrong = total_partial = total_unsure = 0

    for g in all_groups:
        acc = accepted_by_group.get(g, 0)
        samp = sampled_by_group.get(g, 0)
        if existing_labels is None:
            row = f"| {g} | {acc} | {samp} | TBD | TBD | TBD | TBD | TBD |"
        else:
            c = label_buckets[g].get("correct", 0)
            w = label_buckets[g].get("wrong", 0)
            pa = label_buckets[g].get("partial", 0)
            u = label_buckets[g].get("unsure", 0)
            denom = c + w + pa
            est = f"{c / denom:.3f}" if denom > 0 else "TBD"
            total_correct += c
            total_wrong += w
            total_partial += pa
            total_unsure += u
            row = (
                f"| {g} | {acc} | {samp} | {c} | {w} | {pa} | {u} | {est} |"
            )
        lines.append(row)

    if existing_labels is None:
        lines.append(
            f"| **TOTAL** | **{total_acc}** | **{total_samp}** | TBD | TBD | "
            "TBD | TBD | TBD |"
        )
    else:
        denom_t = total_correct + total_wrong + total_partial
        est_t = (
            f"{total_correct / denom_t:.3f}" if denom_t > 0 else "TBD"
        )
        lines.append(
            f"| **TOTAL** | **{total_acc}** | **{total_samp}** | "
            f"**{total_correct}** | **{total_wrong}** | **{total_partial}** | "
            f"**{total_unsure}** | **{est_t}** |"
        )

    lines.append("")
    lines.append(
        "Notes: `est_precision = correct / (correct + wrong + partial)`. "
        "Groups with sampled < 5 are not load-bearing for the Phase 10B "
        "calibration target (>=80% per shipped group with >=5 labels)."
    )
    lines.append("")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_phase10b_calibration_md(
    path: Path,
    audit: List[Dict[str, str]],
    a_norm: Dict[str, NormalizedProduct],
    sample_rows: List[Dict[str, str]],
    existing_labels: Optional[Dict[Tuple[str, str], str]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: List[str] = []
    lines.append("# Phase 10B Calibration")
    lines.append("")
    lines.append(
        "Computed from current `matches_audit.csv` and "
        "`eval/manual_eval_phase10b.csv`. No pipeline rerun by this "
        "script. Threshold grid is exact, not interpolated. The "
        "per-bucket precision rows below derive from **AI-assisted "
        "preliminary labels (Codex)** and are **provisional** until a "
        "human pass edits the CSV; the threshold grid and PDF rows do "
        "not depend on labels and are exact."
    )

    _section(lines, "1. Decision distribution (current audit)")
    _table(lines, ["decision", "count", "share"],
           _diag_decision_distribution(audit))

    _section(lines, "2. Accepted count by matchable_group")
    _table(lines, ["matchable_group", "accepted_count"],
           _diag_by_group(audit, "accepted", a_norm))

    _section(lines, "3. Threshold grid (exact, current audit)")
    lines.append(
        "For each candidate cell, count = audit rows with "
        "`decision in {accepted, below_threshold}` whose stored `score` "
        "and `top1_top2_margin` clear the cell. Margin is held at 0.05; "
        "blank-margin (single-survivor) rows are admitted."
    )
    lines.append("")
    grid = _phase10b_threshold_grid(audit)
    grid_rows: List[List[str]] = []
    for cell in grid:
        grid_rows.append([
            f"{cell['min_score']:.2f}",
            f"{cell['min_margin']:.2f}",
            str(cell["accepted_count"]),
            "Y" if cell["pdf1_admit"] else "N",
            "Y" if cell["pdf2_admit"] else "N",
            "Y" if cell["clears_4000_floor"] else "N",
        ])
    _table(lines,
           ["min_score", "min_margin", "accepted_count",
            "pdf1_admit (2197626->92544)",
            "pdf2_admit (1929544->105624)",
            "clears_4000_floor"],
           grid_rows)

    _section(lines, "4. Accepted score-bucket distribution (current audit)")
    bucket_counter: Counter = Counter()
    for r in audit:
        if r["decision"] != "accepted":
            continue
        s = _safe_float(r["score"])
        bucket_counter[_phase10b_score_bucket(s) or "(none)"] += 1
    bucket_rows: List[List[str]] = []
    bucket_order = [b[0] for b in PHASE10B_SCORE_BUCKETS] + ["(none)"]
    for label in bucket_order:
        if bucket_counter.get(label, 0) > 0:
            bucket_rows.append([label, str(bucket_counter[label])])
    _table(lines, ["score_bucket", "accepted_count"], bucket_rows)

    _section(lines, "5. Per-bucket precision (from labeled sample)")
    if not existing_labels:
        lines.append(
            "Labels not yet filled. Label "
            "`eval/manual_eval_phase10b.csv` (AI-assisted or human) and "
            "rerun the sampler — prior labels and notes are preserved."
        )
    else:
        # Map labels onto sample rows; bucket the sampled accepted rows.
        sample_index = {
            (r["item_id_A"], r["item_id_B"]): r for r in sample_rows
        }
        per_bucket: Dict[str, Counter] = defaultdict(Counter)
        for key, lbl in existing_labels.items():
            r = sample_index.get(key)
            if r is None:
                continue
            if r["sample_type"] == "pdf_regression":
                continue
            s = _safe_float(r.get("score") or "")
            b = _phase10b_score_bucket(s) or "(none)"
            per_bucket[b][lbl] += 1
        bp_rows: List[List[str]] = []
        for label in bucket_order:
            cnt = per_bucket.get(label, Counter())
            c = cnt.get("correct", 0)
            w = cnt.get("wrong", 0)
            pa = cnt.get("partial", 0)
            u = cnt.get("unsure", 0)
            denom = c + w + pa
            if c + w + pa + u == 0:
                continue
            est = f"{c / denom:.3f}" if denom > 0 else "TBD"
            bp_rows.append([label, str(c), str(w), str(pa), str(u), est])
        _table(lines,
               ["score_bucket", "correct", "wrong", "partial", "unsure",
                "est_precision"],
               bp_rows)
        # Cumulative precision by min_score floor (assuming partial=wrong,
        # unsure excluded). For each candidate min_score, take all labeled
        # rows whose audit score >= min_score and compute precision.
        lines.append("")
        lines.append(
            "**Cumulative precision at candidate global thresholds** "
            "(labeled subset; `partial` counted as wrong, `unsure` excluded):"
        )
        lines.append("")
        cum_rows: List[List[str]] = []
        for ms in PHASE10B_THRESH_SCORES:
            c = w = pa = u = 0
            for key, lbl in existing_labels.items():
                r = sample_index.get(key)
                if r is None:
                    continue
                if r["sample_type"] == "pdf_regression":
                    continue
                s = _safe_float(r.get("score") or "")
                if s is None or s < ms:
                    continue
                if lbl == "correct":
                    c += 1
                elif lbl == "wrong":
                    w += 1
                elif lbl == "partial":
                    pa += 1
                elif lbl == "unsure":
                    u += 1
            denom = c + w + pa
            est = f"{c / denom:.3f}" if denom > 0 else "TBD"
            cum_rows.append([
                f"{ms:.2f}", str(c), str(w), str(pa), str(u), est,
            ])
        _table(lines,
               ["min_score", "correct", "wrong", "partial", "unsure",
                "est_precision_at_or_above"],
               cum_rows)

    _section(lines, "6. PDF regressions (current audit)")
    by_a = {r["item_id_A"]: r for r in audit}
    pdf_rows: List[List[str]] = []
    for a_id, _b in PDF_REQUIRED:
        r = by_a.get(a_id)
        if r is None:
            pdf_rows.append([a_id, "(missing)", "", "", "", "", ""])
        else:
            pdf_rows.append(_format_audit_row(r))
    _table(lines,
           ["item_id_A", "item_id_B (chosen)", "decision", "reason",
            "score", "retrieval_score", "top1_top2_margin"],
           pdf_rows)

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_phase10b_eval_md(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = """# Phase 10B Manual Evaluation Guide

## Purpose

`eval/manual_eval_phase10b.csv` is a stratified sample of the
recall-heavy matcher run (16,218-row matches.csv at `--min-score 0.55
--min-margin 0.05`) used to calibrate the shipped global `min_score`.
The CSV ships pre-populated with **AI-assisted preliminary labels
(Codex)**; these are NOT a substitute for human review. Treat the
per-bucket precision in `eval/phase10b_calibration.md` and the
per-group precision in `eval/group_breakdown.md` as **provisional**
until a human re-reads the rows. The output contract (4,000+ rows,
both PDF pairs, no duplicate `item_id_A`) is validated independently
of these labels.

## Strata

- `score_0.55_0.60` ... `score_ge_0.80`: 6 score buckets across the
  current accepted distribution; each is round-robin sampled across
  `matchable_group` so high-volume groups don't saturate the bucket.
- `weak_<group>`: oversample of suspect groups (household, frozen,
  pantry, kitchen_home, seafood) restricted to score < 0.70.
- `private_label_cross_store`: A.is_private_label=True, score in
  [0.55, 0.80).
- `pdf_regression`: the two assignment-required pairs. Not counted in
  the precision denominator; they are output-contract gates.

## Label values (used by the AI-assisted pass; same vocabulary applies for human review)

- `correct` — same product as a customer would consider it (same
  brand-equivalence including PL bridge, same form, same flavor,
  equivalent usable size).
- `wrong` — clearly different products (different brand family without
  PL bridge, materially different size/flavor/form, different functional
  category).
- `partial` — same product family, ambiguous attribute mismatch
  (organic vs non-organic, light vs regular, scent variants). Counted
  as wrong for the precision target but reported separately.
- `unsure` — cannot decide from the names. Excluded from the precision
  denominator and reported.

## Workflow (human review pass)

1. Open `eval/manual_eval_phase10b.csv`.
2. For each row, read `A_name` / `B_name` and confirm or correct the
   AI-assisted `label`. Edit `notes` to capture rationale, especially
   on rows you flip.
3. Save the file in place.
4. Re-run `python3 scripts/sample_eval.py --mode phase10b ...` (same
   args). The sampler now loads existing labels and notes BEFORE
   regenerating the template, so your edits survive the rerun. It
   refreshes `eval/group_breakdown.md` and
   `eval/phase10b_calibration.md` with the updated per-group and
   per-bucket precision tables. Until a human pass is recorded,
   precision numbers in those tables remain provisional.

## Do NOT modify

`item_id_A`, `item_id_B`, `A_name`, `B_name`, `score`, `margin`,
`source`, `reason`, `decision`, `sample_type`, `matchable_group`.

## Estimated time

20-40 minutes for ~90 rows.
"""
    path.write_text(body, encoding="utf-8")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    args = _parse_args(argv)
    rng = random.Random(args.seed)

    tag = "Phase 10B sample_eval" if args.mode == "phase10b" else "Phase 9 sample_eval"

    print(f"[{tag}] loading audit...", flush=True)
    audit = _load_audit(args.audit_csv)
    print(f"  audit rows: {len(audit)}", flush=True)

    print(f"[{tag}] normalizing A side...", flush=True)
    a_raw, a_norm, _a_list = _load_source(args.a_csv, "A")
    print(f"  A normalized: {len(a_norm)}", flush=True)

    print(f"[{tag}] normalizing B side...", flush=True)
    b_raw, b_norm_by_id, b_normalized_list = _load_source(args.b_csv, "B")
    print(f"  B normalized: {len(b_norm_by_id)}", flush=True)

    print(f"[{tag}] building sample buckets...", flush=True)

    if args.mode == "phase10b":
        labeled_samples = _build_phase10b_samples(audit, a_norm, rng)
        template_rows: List[Dict[str, str]] = [
            _to_template_row(r, label, a_raw, b_raw, a_norm)
            for label, r in labeled_samples
        ]
        pdf_rows = [
            _to_template_row(r, "pdf_regression", a_raw, b_raw, a_norm)
            for r in _bucket_pdf(audit)
        ]
        template_rows = _dedup_preserve_pdf(template_rows, pdf_rows)
        bucket_summary = Counter(label for label, _ in labeled_samples)
        bucket_summary["pdf_regression"] = len(pdf_rows)
        print(
            "  strata: "
            + ", ".join(f"{k}={v}" for k, v in sorted(bucket_summary.items()))
            + f"; final_template_rows={len(template_rows)}",
            flush=True,
        )
    else:
        bucket_a = _bucket_random_accepted(audit, a_norm, rng)
        bucket_b = _bucket_bottom_q1_accepted(audit, a_norm, rng)
        bucket_c = _bucket_near_miss(audit)
        bucket_d = _bucket_pdf(audit)

        template_rows = []
        for r in bucket_a:
            template_rows.append(_to_template_row(r, "random_accepted",
                                                  a_raw, b_raw, a_norm))
        for r in bucket_b:
            template_rows.append(_to_template_row(r, "bottom_q1_accepted",
                                                  a_raw, b_raw, a_norm))
        for r in bucket_c:
            template_rows.append(_to_template_row(r, "near_miss_below_threshold",
                                                  a_raw, b_raw, a_norm))
        pdf_rows = [
            _to_template_row(r, "pdf_regression", a_raw, b_raw, a_norm)
            for r in bucket_d
        ]
        template_rows = _dedup_preserve_pdf(template_rows, pdf_rows)
        print(
            f"  bucketA={len(bucket_a)} bucketB={len(bucket_b)} "
            f"bucketC={len(bucket_c)} pdf={len(pdf_rows)} "
            f"final_template_rows={len(template_rows)}",
            flush=True,
        )

    out_csv = Path(args.out_csv)
    # Load any prior annotations BEFORE overwriting the template, so a
    # rerun with the same audit + same seed preserves human/AI-assisted
    # labels and notes across regenerations. Phase 10B previously read
    # labels AFTER writing, which silently erased the file.
    prior_annotations = _load_existing_annotations(out_csv)
    merged = _merge_existing_annotations(template_rows, prior_annotations)
    if prior_annotations:
        print(
            f"  preserved {merged}/{len(prior_annotations)} prior annotations "
            f"from {out_csv}",
            flush=True,
        )
    _write_template(out_csv, template_rows)
    print(f"  wrote {out_csv}", flush=True)

    if args.mode == "phase10b":
        manual_md = out_csv.parent / "manual_eval_phase10b.md"
        _write_phase10b_eval_md(manual_md)
    else:
        manual_md = out_csv.parent / "manual_eval.md"
        _write_manual_eval_md(manual_md)
    print(f"  wrote {manual_md}", flush=True)

    existing_labels = _labels_from_rows(template_rows) or None

    if args.mode == "phase10b":
        _write_group_breakdown_phase10b(
            Path(args.group_breakdown_out),
            audit,
            template_rows,
            a_norm,
            existing_labels,
        )
    else:
        _write_group_breakdown(
            Path(args.group_breakdown_out),
            audit,
            template_rows,
            a_norm,
            existing_labels,
        )
    print(f"  wrote {args.group_breakdown_out}", flush=True)

    if args.mode == "phase10b":
        print(f"[{tag}] writing calibration doc...", flush=True)
        _write_phase10b_calibration_md(
            Path(args.diagnostics_out),
            audit,
            a_norm,
            template_rows,
            existing_labels,
        )
    else:
        print(f"[{tag}] writing diagnostics (incl. targeted "
              "A 1929544 top-50)...", flush=True)
        _write_diagnostics(
            Path(args.diagnostics_out),
            audit,
            a_norm,
            b_norm_by_id,
            b_normalized_list,
        )
    print(f"  wrote {args.diagnostics_out}", flush=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
