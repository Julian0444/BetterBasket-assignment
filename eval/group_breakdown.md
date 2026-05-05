# Phase 10B Per-Group Precision

Computed from 92 **AI-assisted preliminary labels (Codex)** in `eval/manual_eval_phase10b.csv`. These labels are not human-reviewed; precision numbers below are **provisional** until a human pass edits the CSV. `partial` is treated as a precision miss; `unsure` is excluded from the denominator. Re-run `sample_eval.py --mode phase10b` after edits to refresh — the sampler now preserves prior labels and notes across reruns.

| matchable_group | accepted_count | sampled | correct | wrong | partial | unsure | est_precision |
|---|---:|---:|---:|---:|---:|---:|---:|
| pantry | 1453 | 4 | 1 | 3 | 0 | 0 | 0.250 |
| snacks | 521 | 4 | 1 | 1 | 2 | 0 | 0.250 |
| personal_care | 439 | 4 | 2 | 2 | 0 | 0 | 0.500 |
| beverages | 390 | 4 | 1 | 2 | 1 | 0 | 0.250 |
| household | 355 | 7 | 2 | 2 | 3 | 0 | 0.286 |
| candy | 335 | 2 | 2 | 0 | 0 | 0 | 1.000 |
| beauty | 209 | 2 | 1 | 1 | 0 | 0 | 0.500 |
| health | 175 | 3 | 3 | 0 | 0 | 0 | 1.000 |
| frozen | 164 | 7 | 4 | 3 | 0 | 0 | 0.571 |
| dairy | 99 | 3 | 2 | 1 | 0 | 0 | 0.667 |
| baby | 95 | 6 | 3 | 2 | 1 | 0 | 0.500 |
| pets | 58 | 7 | 6 | 1 | 0 | 0 | 0.857 |
| cheese | 35 | 5 | 0 | 2 | 3 | 0 | 0.000 |
| bakery | 29 | 4 | 2 | 2 | 0 | 0 | 0.500 |
| wine_beer_spirits | 20 | 3 | 2 | 1 | 0 | 0 | 0.667 |
| meat | 10 | 5 | 3 | 2 | 0 | 0 | 0.600 |
| produce | 4 | 5 | 4 | 0 | 1 | 0 | 0.800 |
| kitchen_home | 1 | 5 | 3 | 0 | 2 | 0 | 0.600 |
| prepared_foods | 1 | 4 | 3 | 1 | 0 | 0 | 0.750 |
| seafood | 1 | 6 | 2 | 3 | 1 | 0 | 0.333 |
| **TOTAL** | **4394** | **90** | **47** | **29** | **14** | **0** | **0.522** |

Notes: `est_precision = correct / (correct + wrong + partial)`. Groups with sampled < 5 are not load-bearing for the Phase 10B calibration target (>=80% per shipped group with >=5 labels).

