# Phase 10B Calibration

Computed from current `matches_audit.csv` and `eval/manual_eval_phase10b.csv`. No pipeline rerun by this script. Threshold grid is exact, not interpolated. The per-bucket precision rows below derive from **AI-assisted preliminary labels (Codex)** and are **provisional** until a human pass edits the CSV; the threshold grid and PDF rows do not depend on labels and are exact.

## 1. Decision distribution (current audit)

| decision | count | share |
|:---|:---|:---|
| below_threshold | 118140 | 50.66% |
| no_candidates | 85901 | 36.84% |
| rejected_by_rule | 24759 | 10.62% |
| accepted | 4394 | 1.88% |

## 2. Accepted count by matchable_group

| matchable_group | accepted_count |
|:---|:---|
| pantry | 1453 |
| snacks | 521 |
| personal_care | 439 |
| beverages | 390 |
| household | 355 |
| candy | 335 |
| beauty | 209 |
| health | 175 |
| frozen | 164 |
| dairy | 99 |
| baby | 95 |
| pets | 58 |
| cheese | 35 |
| bakery | 29 |
| wine_beer_spirits | 20 |
| meat | 10 |
| produce | 4 |
| kitchen_home | 1 |
| prepared_foods | 1 |
| seafood | 1 |

## 3. Threshold grid (exact, current audit)

For each candidate cell, count = audit rows with `decision in {accepted, below_threshold}` whose stored `score` and `top1_top2_margin` clear the cell. Margin is held at 0.05; blank-margin (single-survivor) rows are admitted.

| min_score | min_margin | accepted_count | pdf1_admit (2197626->92544) | pdf2_admit (1929544->105624) | clears_4000_floor |
|:---|:---|:---|:---|:---|:---|
| 0.65 | 0.05 | 10246 | Y | Y | Y |
| 0.70 | 0.05 | 7279 | Y | Y | Y |
| 0.75 | 0.05 | 4394 | Y | Y | Y |
| 0.80 | 0.05 | 2162 | N | Y | N |

## 4. Accepted score-bucket distribution (current audit)

| score_bucket | accepted_count |
|:---|:---|
| score_0.75_0.80 | 2232 |
| score_ge_0.80 | 2162 |

## 5. Per-bucket precision (from labeled sample)

| score_bucket | correct | wrong | partial | unsure | est_precision |
|:---|:---|:---|:---|:---|:---|
| score_0.55_0.60 | 3 | 13 | 4 | 0 | 0.150 |
| score_0.60_0.65 | 8 | 8 | 2 | 0 | 0.444 |
| score_0.65_0.70 | 5 | 4 | 4 | 0 | 0.385 |
| score_0.70_0.75_threshold_zone | 12 | 4 | 3 | 0 | 0.632 |
| score_0.75_0.80 | 12 | 0 | 0 | 0 | 1.000 |
| score_ge_0.80 | 7 | 0 | 1 | 0 | 0.875 |

**Cumulative precision at candidate global thresholds** (labeled subset; `partial` counted as wrong, `unsure` excluded):

| min_score | correct | wrong | partial | unsure | est_precision_at_or_above |
|:---|:---|:---|:---|:---|:---|
| 0.65 | 36 | 8 | 8 | 0 | 0.692 |
| 0.70 | 31 | 4 | 4 | 0 | 0.795 |
| 0.75 | 19 | 0 | 1 | 0 | 0.950 |
| 0.80 | 7 | 0 | 1 | 0 | 0.875 |

## 6. PDF regressions (current audit)

| item_id_A | item_id_B (chosen) | decision | reason | score | retrieval_score | top1_top2_margin |
|:---|:---|:---|:---|:---|:---|:---|
| 2197626 | 92544 | accepted | ok | 0.7974333848848699 | 1.235333627913542 | 0.06230332687367568 |
| 1929544 | 105624 | accepted | ok | 0.8643303152802316 | 1.4604589444584672 | 0.1392602069566683 |
