# BetterBasket Handoff

This is the operational source of truth for implementation sessions. New agents should read this file first, then only the canonical references listed below as needed.

## Current State

- Branch: `main` tracking `origin/main`.
- Recent commit: `c3dc5f2 Initial commit`.
- Working tree: documentation and support files are currently uncommitted.
- Matcher package status: not implemented yet. There is no `betterbasket_matcher/` package and no `scripts/run_pipeline.py` yet.
- Existing reproducible support scripts:
  - `scripts/audit_data.py`
  - `scripts/retrieval_probe.py`
- Existing canonical references:
  - `docs/dataset_audit.md`
  - `docs/algorithm_recommendation.md`
  - `docs/audit_stats.json`
  - `docs/retrieval_probe_results.json`
- Narrative docs exist, but should not drive implementation unless explicitly requested:
  - `README.md`
  - `solution.md`
  - `solutioneasyexplained.md`
  - `app.md`

## Documents Removed

The retired docs were removed to reduce AI confusion:

- `docs/audits/2026-05-02-dataset-audit.md`
- `docs/plans/2026-05-02-betterbasket-product-matching.md`

Do not recreate retired docs. If implementation planning is needed, keep it in this handoff or in the active conversation unless the user explicitly asks for a separate plan file.

## Core Product Understanding

The project is a BetterBasket engineering assessment for cross-retailer product matching:

- Store A: Walmart, about 233k rows.
- Store B: Wegmans, about 55k rows.
- Required output: `matches.csv` with header `item_id_A,item_id_B`.
- Minimum output size: at least 4,000 matches.
- Goal: for each matched A product, select the single closest B product a shopper would consider essentially the same product.

Important audit facts:

- Store A has 5 malformed rows with non-numeric `item_id`; these must be quarantined before matching.
- UPC join is not viable because Store A has no UPC-like fields.
- Decoy columns are blank and should not be trusted: `name_clean`, `category`, `department`, `subcategory`, `size_raw`, `item_type`, `is_private_label`, `is_organic`.
- Useful fields are `item_id`, `name`, `brand_raw`, `url`, `item_info`, `sizing_comp`, and B `tags`.
- Store A has poor brand coverage, especially in Food, so conservative brand inference from name is needed.
- Private-label matching is required: Walmart `Great Value` can match Wegmans private label when product attributes align.
- Store B has many same-name size variants; size and pack rules are hard requirements.

Known PDF examples that must validate end-to-end:

- A `2197626` Chobani 5.3 oz -> B `92544`.
- A `1929544` Great Value Organic Tomato Sauce 8 oz -> B `105624`, not B `103620` 15 oz or B `1086860` 29 oz.

## Current Algorithm Decision

Use a deterministic, precision-first pipeline:

1. Streaming CSV ingest with numeric ID validation.
2. Tolerant parsing for `item_info`, `sizing_comp`, and B `tags`.
3. Normalization of brand, private label, category, size, pack, organic, form, storage, flavor, and retrieval text.
4. A-side scope filter for categories Wegmans cannot plausibly match.
5. Shared `matchable_group` taxonomy.
6. TF-IDF candidate retrieval over B using word `(1, 2)` and char-wb `(3, 5)` n-grams.
7. Private-label brand-token suppression in retrieval text.
8. Hard rules before scoring: group, brand/private-label, size, pack, organic, form, storage, flavor, alcohol.
9. Deterministic weighted scoring and margin check.
10. One best B per A.
11. Output `matches.csv` and `matches_audit.csv`.
12. Optional GPT-5 nano only for gray-zone candidate sets. It must never override hard rules.

Avoid:

- all-pairs comparison;
- LLM-first matching;
- UPC-first matching;
- fuzzy-only matching;
- accepting same-name different-size variants without size/pack checks;
- requiring global brand equality.

## Preferred Session Workflow

Use three prompts per batch when switching sessions or accounts:

1. Restore context
   - Read `docs/HANDOFF.md` and only the canonical docs needed.
   - Do not modify code.
   - Do not implement.
   - Report current state, likely files, constraints, and broken assumptions.

2. Preflight
   - Do not implement.
   - Turn the batch insight into an execution shape.
   - Define files, tests, acceptance criteria, verification commands, and risks.
   - Do not update `docs/HANDOFF.md`.

3. Execution
   - Run baseline tests first.
   - Implement narrowly scoped work.
   - Verify with agreed commands.
   - Update `docs/HANDOFF.md` with completed work, changed files, commands run, blockers, and next action.

## Implementation Batches

Recommended order:

1. Setup and handoff base.
2. Regression fixtures.
3. IO and tolerant parsing.
4. Normalization.
5. Taxonomy and scope.
6. TF-IDF retrieval.
7. Hard rules and scoring.
8. End-to-end fixture pipeline and output validation.
9. Full dataset run.
10. Threshold calibration and manual eval.
11. Optional GPT-5 nano arbiter.
12. Submission polish.

## Acceptance Criteria

The submission is ready only when:

1. `python3 -m pytest` passes.
2. `scripts/run_pipeline.py` runs with documented arguments.
3. `matches.csv` exists with exact header `item_id_A,item_id_B`.
4. `matches.csv` has at least 4,000 rows.
5. `matches.csv` has no duplicate `item_id_A`.
6. Every emitted ID is numeric and exists in the validated source datasets.
7. A `2197626` maps to B `92544`.
8. A `1929544` maps to B `105624`, not `103620` or `1086860`.
9. `matches_audit.csv` explains score, source, margin, and reason.
10. No OpenAI credentials or local secret files are committed.
11. README explains how to reproduce the output.

## Blockers And Warnings

- The executable matcher is not built yet.
- The real CSVs are expected at:
  - `/Users/jirustaroure/Downloads/grocery_store_a_items_final.csv`
  - `/Users/jirustaroure/Downloads/grocery_store_b_items_final.csv`
- Do not copy the large CSVs into the repo unless explicitly requested.
- Some narrative docs may still mention retired files until docs are polished; use this handoff as the implementation source of truth.
- Do not commit unless the user explicitly asks.

## Suggested Next Action

Start with the Restore context prompt for Batch 1: setup and regression fixtures. Then run a Preflight prompt to confirm exact files/tests, followed by Execution to create `betterbasket_matcher/`, `pytest.ini`, `requirements.txt`, and fixture sanity tests.

## Session - 2026-05-02 23:59 PDT

Completed:

- Adopted `docs/HANDOFF.md` as the operational source of truth for implementation sessions.
- Removed two retired docs that could confuse future AI agents.
- Confirmed the executable matcher has not been implemented yet.

Changed files:

- Added `docs/HANDOFF.md`.
- Deleted `docs/audits/2026-05-02-dataset-audit.md`.
- Deleted `docs/plans/2026-05-02-betterbasket-product-matching.md`.

Verification:

- File deletion verified after patch in the active session.
- No code tests were run because this change only updates documentation and removes retired docs.

## Session - 2026-05-03 00:50 PDT

Completed:

- Phase 0: created minimal repo skeleton for the BetterBasket executable pipeline.
- Wrote `tests/test_import_contracts.py` first (TDD); confirmed it failed with `ModuleNotFoundError` before the package existed.
- Created `betterbasket_matcher/__init__.py` with package docstring and `__version__ = "0.1.0"`.
- Created `requirements.txt` with exact dependencies: scikit-learn, scipy, pytest, rank-bm25, pyyaml, openai.
- Created `pytest.ini` pointing `testpaths = tests`.
- No matcher logic was introduced.

Changed files:

- `betterbasket_matcher/__init__.py` (new)
- `requirements.txt` (new)
- `pytest.ini` (new)
- `tests/test_import_contracts.py` (new)
- `docs/HANDOFF.md` (updated — this entry)

Verification:

- `python3 -m pytest tests/test_import_contracts.py` before package existed: 1 error (ModuleNotFoundError). Expected.
- `python3 -m pytest tests/test_import_contracts.py -v` after package created: 1 passed.
- `python3 -m pytest` (full suite): 1 passed.
- `python3 -c "import betterbasket_matcher; print(betterbasket_matcher.__version__)"` → `0.1.0`.

Blockers: none.

Next suggested action: Phase 1 — regression fixtures. Create `tests/fixtures/` JSON/CSV stubs for the two known PDF examples (A `2197626` → B `92544`; A `1929544` → B `105624`) and write `tests/test_regression_fixtures.py` that loads them and asserts the expected structure, so every future pipeline phase can assert end-to-end correctness from the start.

## Session - 2026-05-03 02:00 PDT

Completed:

- Phase 1: created regression fixtures and sanity test before any matcher logic.
- Wrote `tests/test_fixtures_sanity.py` first (TDD); confirmed all 12 tests failed when fixture CSVs did not exist.
- Generated `tests/fixtures/mini_a.csv`, `tests/fixtures/mini_b.csv`, and `tests/fixtures/expected_matches.csv` via a temporary Python generator script using `csv.DictWriter` (script discarded after use; not committed).
- All 17 required case_ids are present and verified by the sanity test.
- B tags format confirmed unavailable via direct file read (PermissionError on real CSV). Format inferred from `docs/audit_stats.json` (16,878 postgres-style vs 5,906 list-style rows). Used postgres-style `{"tag","tag"}` for most B rows; one row (`9100113`) uses Python list style `["tag", "tag"]` to exercise both parse_tags code paths.
- No matcher logic was introduced.

Changed files:

- `tests/test_fixtures_sanity.py` (new)
- `tests/fixtures/mini_a.csv` (new, 39 rows)
- `tests/fixtures/mini_b.csv` (new, 30 rows)
- `tests/fixtures/expected_matches.csv` (new, 17 rows)
- `docs/HANDOFF.md` (updated — this entry)

Fixture coverage summary:

- 39 A rows: 17 case rows + 1 malformed (` | Pack of 12`) + 21 filler rows.
- 30 B rows: 16 case counterpart rows (including 3 wrong-size tomato variants B 105624/103620/1086860) + 14 filler rows.
- 17 expected_matches rows: 7 MATCH + 10 NO_MATCH, one per required case_id, all unique item_id_A.
- MATCH cases: pdf_chobani, pdf_tomato_8oz, tomato_wrong_size_variants_present, private_label_cross_store, national_brand_exact, pack_prefix, home_kitchen_in_scope.
- NO_MATCH cases: national_vs_private_label_reject, size_mismatch, frozen_storage_mismatch, powder_liquid_mismatch, sliced_shredded_mismatch, cat_dog_mismatch, alcohol_mismatch, flavor_mismatch, out_of_scope_category, home_decor_out_of_scope.
- Decoy columns are blank in both stores as required.
- All nonblank item_info and sizing_comp values are valid JSON dicts (verified by test).
- Real PDF IDs present: A 2197626, A 1929544, B 92544, B 105624.

B tags format used and assumption:

- Real B CSV inaccessible (PermissionError). Format inferred from `docs/audit_stats.json` which recorded 16,878 postgres-style rows and 5,906 python-list-style rows.
- Postgres-style used in fixtures: `{"wegmans brand","organic"}`. Python list-style used in one row: `["wegmans brand", "organic"]`. Both are handled by `scripts/audit_data.py:parse_tags`.
- If CSV access is restored in a future session, spot-check one real B row with tags to confirm format before extending fixtures.

Verification:

- `python3 -m pytest tests/test_fixtures_sanity.py -v`: 12 passed.
- `python3 -m pytest`: 13 passed (12 sanity + 1 import contract).

Blockers: none.

Next suggested action: Phase 2 — IO and tolerant parsing. Create `betterbasket_matcher/io.py` with `load_store_a(path)` and `load_store_b(path)` that use `csv.DictReader`, validate numeric item_ids, quarantine malformed rows, and parse `item_info`, `sizing_comp`, and B `tags` tolerantly. Write `tests/test_io.py` driven by the mini fixtures. Expected interface: returns `(valid_rows, quarantined_rows)` for A and `validated_rows` for B.
