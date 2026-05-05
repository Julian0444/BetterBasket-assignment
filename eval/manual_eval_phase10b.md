# Phase 10B Manual Evaluation Guide

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
