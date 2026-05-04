# Phase 9 Manual Evaluation Guide

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
