# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository posture

The matcher is **implemented, tested, and shipped**. The current `matches.csv` (4,394 accepted rows) is the assignment deliverable. Future sessions are maintenance / submission polish, not phased construction. Do not reintroduce "to be built" language into any doc. There is no remaining design phase.

`docs/HANDOFF.md` is the chronological session log (Phase 0 through Phase 11). Read it for *why* a decision was made, not for *what to do next*. New sessions append to it; they do not gate work on it.

The other reference docs are stable, and any drift between them and the code should be fixed in the doc:

- `docs/dataset_audit.md` + `docs/audit_stats.json` — what the real CSVs actually contain (decoys, brand coverage, malformed rows, duplicates).
- `docs/algorithm_recommendation.md` — the precision-first deterministic pipeline design that ships in `betterbasket_matcher/`.
- `docs/retrieval_probe_results.md` + `docs/retrieval_probe_results.json` — TF-IDF / BM25 dry-run that motivates private-label brand suppression and the "Great Value -> Great Lakes" trap that hard rules prevent.
- `README.md`, `solution.md`, `solutioneasyexplained.md`, `app.md` — narrative retellings of the shipped solution; do not drive implementation from them.
- `eval/phase10b_calibration.md`, `eval/group_breakdown.md`, `eval/manual_eval_phase10b.{csv,md}` — calibration evidence for the 0.75 / 0.05 threshold floor (provisional AI-assisted labels).
- `SUBMISSION_CHECKLIST.md` — reviewer-facing gates with verifiable commands.

## Common commands

```bash
python3 -m pytest -q                                  # full suite, 412 tests
python3 -m pytest tests/test_pipeline_fixtures.py -v  # one module
python3 -m pytest tests/test_normalize.py::TestBrandInferenceCanonical::test_great_value -v
pip install -r requirements.txt                       # scikit-learn, scipy, pytest, rank-bm25, pyyaml, openai
```

The production entry point is `scripts/run_pipeline.py`. The shipped `matches.csv` was produced with the calibrated CLI floor:

```bash
python3 scripts/run_pipeline.py \
    --a-csv /Users/jirustaroure/Downloads/grocery_store_a_items_final.csv \
    --b-csv /Users/jirustaroure/Downloads/grocery_store_b_items_final.csv \
    --min-score 0.75 --min-margin 0.05 --top-k 50
```

The two probe scripts are reproducible and not part of the matcher path:

```bash
python3 scripts/audit_data.py     --a-csv ... --b-csv ... --json-out docs/audit_stats.json --markdown-out docs/dataset_audit_stats.md
python3 scripts/retrieval_probe.py --a-csv ... --b-csv ... --json-out docs/retrieval_probe_results.json --markdown-out docs/retrieval_probe_results.md
```

The real CSVs live at `/Users/jirustaroure/Downloads/grocery_store_{a,b}_items_final.csv`. They are not committed and must not be copied into the repo.

## Final run reference

For the calibrated `--min-score 0.75 --min-margin 0.05 --top-k 50` invocation against the real CSVs the pipeline reports:

```
valid_a=233194  quarantined_a=5  valid_b=55516  in_scope_a=181289
accepted=4394  rejected_by_rule=24759  below_threshold=118140  no_candidates=85901
validation: ok=True  row_count=4394  errors=0
```

These counters are stable; any change in matching logic that moves them must be justified.

## Maintenance workflow

Treat the project as a small, maintained Python package, not a phase tree:

1. **Baseline gate** — `git status --short`, `git diff --stat`, `python3 -m pytest -q`. Suite must be green at 412 before changing anything; if not, stop and report.
2. **Minimal, narrow change** — touch only the files the task requires. No drive-by refactors. No editing of frozen fixtures.
3. **Tests first when behavior changes** — add or extend the test before changing logic; the suite is the regression contract.
4. **Verify** — re-run the affected test file, then the full suite. Test count is allowed to grow but never to drop.
5. **Append to `docs/HANDOFF.md`** — one session block (`## Session - YYYY-MM-DD HH:MM <TZ>`) summarizing what changed, what was verified, and any open notes.
6. **Do not commit** unless the user explicitly asks.

## Frozen artifacts

- `tests/fixtures/mini_a.csv`, `tests/fixtures/mini_b.csv`, `tests/fixtures/expected_matches.csv` — Phase 1 oracles. They encode 17 case_ids (PDF examples plus targeted positive/negative cases) and must not be edited. If a defect appears to require a new fixture row, stop and report instead of editing.
- `matches.csv` — the shipped 4,394-row deliverable; produced at `--min-score 0.75 --min-margin 0.05 --top-k 50`. Do not regenerate as a side effect of unrelated changes.
- The 100% blank decoy columns in source CSVs (`name_clean`, `category`, `department`, `subcategory`, `size_raw`, `item_type`, `is_private_label`, `is_organic`) must never be trusted; everything is read from `name`, `brand_raw`, `item_info`, `sizing_comp`, and B `tags`.

## Architecture

The matcher is a single Python package, `betterbasket_matcher/`, with strictly layered modules. Each module reads only from earlier layers; tests in `tests/test_*.py` mirror the layering.

```
io.py          -> read_products, parse_json_dict, parse_tags, is_numeric_id
normalize.py   -> NormalizedProduct, SizeInfo, normalize_product, infer_brand_from_name, _is_private_label_a
taxonomy.py    -> assign_matchable_group, groups_compatible, GROUPS, _norm_cat
scope.py       -> is_a_in_scope                       (depends on taxonomy._norm_cat)
retrieval.py   -> TfidfRetriever.fit / .query         (consumes NormalizedProduct.retrieval_text)
rules.py       -> evaluate_hard_rules                 (group, brand/PL, size, pack, organic, form, storage, flavor, alcohol, pet)
scoring.py     -> score_pair, select_best             (deterministic weighted score + margin + tie-break)
pipeline.py    -> run_pipeline, PipelineConfig, PipelineResult (one best B per A; optional arbiter slot)
output.py      -> write_matches, write_matches_audit, validate_matches_csv
llm_arbiter.py -> LLMArbiter, ArbiterInput, ArbiterOpinion (opt-in GPT-5.4 nano gray-zone rescue, off by default)
```

Critical contracts that span modules:

- **Numeric ID quarantine** — `read_products` returns `(valid_rows, quarantined_rows)`; the 5 malformed A rows (e.g. `item_id=" | Pack of 12"`) must never reach normalization or output.
- **Tolerant parsing** — `item_info` and `sizing_comp` are JSON dicts that may be blank, malformed, or non-dict; `parse_json_dict` returns `{}` on anything that isn't a dict. B `tags` come in **two** wire formats (postgres `{"a","b"}` and Python list `["a","b"]`); `parse_tags` handles both.
- **Brand inference (A only)** — when `brand_raw` is blank, `infer_brand_from_name` matches the leading tokens of `name` against the canonical private-label set with **longest word-boundary prefix match**. `"Equate Extra Strength"` -> `"equate"` (canonical), not `"equate extra"`. National brand inference fires only when `known_brands` is passed (currently never; reserved for a later enhancement). The `brand_inferred` flag must reflect reality so brand-compatibility scoring can downweight inferred brands.
- **Private-label detection is prefix-match, not exact match** — `_is_private_label_a("equate extra")` is True; `_is_private_label_a("equator")` is False. `_PRIVATE_LABEL_A` holds canonical brand strings only; never add variants.
- **Private-label brand suppression in `retrieval_text`** — for PL items the brand token is omitted from `retrieval_text` so TF-IDF cannot pull `Great Value Provolone` to `Great Lakes Provolone Cheese`. This is the documented "Great Value -> Great Lakes" trap; any change that re-introduces the brand token regresses retrieval.
- **Attribute fields mean "unknown" when None** — `storage_type`, `form`, `flavor` are normalized from `item_info` when present, then fall back to a conservative whole-word name-token scan (and tag scan for B storage). `None` means not detected; hard rules treat `None` as no-mismatch, not as a rejection.
- **Asymmetric size parsing** — A reads size from `name` (with `(N Pack)` prefix extraction); B reads size from `sizing_comp.size_user_friendly` (with `12 x 5.3 ounce` pack-x format). Both produce a `SizeInfo(unit, unit_size, pack_count, total_size)`. The retrieval-text size renderer drops `.0` on integer-valued floats (`"8oz"`, not `"8.0oz"`).
- **`groups_compatible` is conservative** — exact group equality OR symmetric `dairy <-> cheese` only. Every other pair returns False. Widening this requires evidence from the calibration sample, never a speculative edit. `None`/`""` group on either side returns False, so unclassifiable products are routed away from candidates.
- **A-only scope filter** — `is_a_in_scope` returns `(True, "not_store_a")` for B (so the same call site works for both stores), `(False, "excluded_category")` for the 12 Walmart-only top-level categories, `(False, "excluded_home_decor")` for `Home > {Home Decor, Picture Frames, Bedding, Furniture, Rugs, Wall Art}`, `(True, "in_scope")` otherwise. Reasons are stable strings used by the audit output.
- **Category normalization** — `_norm_cat` lowercases, replaces `&` with `and`, collapses punctuation/whitespace. Mapping dictionaries use the normalized form (`"sports and outdoors"`, `"wine beer and spirits"`, `"dairy and eggs"`); never key on title-case or ampersand variants.
- **Score and margin floors** — `pipeline.py` / `run_pipeline.py` defaults remain `min_score=0.55 / min_margin=0.05` so the test suite keeps the broader behavior covered; the **shipped run** uses the calibrated `--min-score 0.75 --min-margin 0.05 --top-k 50` floor on the CLI.

## PDF regression invariants

These two end-to-end matches are baked into the assignment, the Phase 1 fixtures, and the validator's `required_pairs` gate; they must remain correct:

- A `2197626` (Chobani 5.3 oz honey blended yogurt) -> B `92544`.
- A `1929544` (Great Value Organic Tomato Sauce 8 oz) -> B `105624`, **not** the 15 oz B `103620` or the 29 oz B `1086860`.

## Output contract

`matches.csv` must satisfy (enforced by `validate_matches_csv` and verified at every shipped run):

- exact header `item_id_A,item_id_B`;
- only numeric IDs that exist in the validated A and B ID sets (no quarantined rows);
- no duplicate `item_id_A`;
- at least 4,000 rows;
- both PDF examples resolve to the correct sizes.

`matches_audit.csv` is a debug / audit trace (one row per valid A item processed) with columns `item_id_A, item_id_B, score, retrieval_score, top1_top2_margin, source, decision, reason, llm_confidence`. It is not the primary deliverable; treat it as a regenerable side artifact.

## Optional GPT-5.4 nano arbiter

`betterbasket_matcher/llm_arbiter.py` implements an opt-in gray-zone rescue layer. **Off by default.** The shipped `matches.csv` was produced *without* it. When enabled with `--use-llm-arbiter`, the arbiter only sees `below_threshold` rows whose deterministic score lands in `[0.65, 0.75)`; it cannot override hard-rule rejections, cannot change rows already cleared above 0.75, and fail-closes on any API or parse error. The credential resolution order is `--llm-creds` -> `$BB_OPENAI_CREDS` -> `/tmp/openai_artifacts/openai_creds.yaml` -> `~/Downloads/openai_creds.yaml`. The cache lives at `.cache/llm_arbiter.jsonl` and is gitignored. `api_key` is held in a `field(repr=False)` slot and is never logged, printed, or written to the cache.

## Constraints baked into the design

- **No pandas.** Streaming CSV plus `csv.DictReader` is the only ingest path. `scripts/audit_data.py` and the matcher are deliberately pandas-free for memory.
- **No all-pairs comparison, no LLM-first matching, no UPC-first matching, no fuzzy-only matching.** UPC join is not viable on this dataset (A has zero UPC-like fields).
- **Hard rules run before scoring; the LLM arbiter cannot override them.**
- **No emojis in code or commits.** Use ASCII arrows (`->`) in docs/comments rather than Unicode.
- **No credentials in tracked files.** The credential loader is the single ingress point and never echoes values.
- **Do not commit unless the user explicitly asks.**
