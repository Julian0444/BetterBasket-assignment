# Phase 9 Per-Group Precision Scaffold

Labels not yet filled — `correct` / `wrong` / `partial` / `unsure` columns and `est_precision` are TBD until `eval/manual_eval_template.csv` is hand-labeled. Re-run `sample_eval.py` after labeling to populate them.

| matchable_group | accepted_count | sampled | correct | wrong | partial | unsure | est_precision |
|---|---:|---:|---:|---:|---:|---:|---:|
| pantry | 220 | 17 | TBD | TBD | TBD | TBD | TBD |
| beverages | 108 | 19 | TBD | TBD | TBD | TBD | TBD |
| candy | 100 | 19 | TBD | TBD | TBD | TBD | TBD |
| frozen | 62 | 16 | TBD | TBD | TBD | TBD | TBD |
| dairy | 18 | 8 | TBD | TBD | TBD | TBD | TBD |
| cheese | 11 | 9 | TBD | TBD | TBD | TBD | TBD |
| meat | 3 | 3 | TBD | TBD | TBD | TBD | TBD |
| kitchen_home | 1 | 1 | TBD | TBD | TBD | TBD | TBD |
| produce | 1 | 1 | TBD | TBD | TBD | TBD | TBD |
| **TOTAL** | **524** | **93** | TBD | TBD | TBD | TBD | TBD |

Notes: `est_precision = correct / (correct + wrong + partial)` — `unsure` rows are excluded from the denominator. `partial` is treated as a precision miss; loosen this if Phase 9 calibration decides to admit partials as acceptable substitutions.

