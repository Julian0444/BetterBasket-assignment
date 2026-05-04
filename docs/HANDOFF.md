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

## Session - 2026-05-03 02:30 PDT

Completed:

- Phase 2: implemented IO and tolerant parsing in `betterbasket_matcher/io.py`.
- Wrote `tests/test_io.py` first (TDD, 37 tests); confirmed import error before `io.py` existed.
- Implemented all four public functions in `betterbasket_matcher/io.py`.
- No normalization, taxonomy, retrieval, scoring, or pipeline logic was added.
- Phase 1 fixtures were not modified.

Changed files:

- `betterbasket_matcher/io.py` (new)
- `tests/test_io.py` (new)
- `docs/HANDOFF.md` (updated — this entry)

Parser behavior summary:

- `is_numeric_id(value)`: accepts only `^\d+$`; rejects empty, whitespace, pipes, letters, floats, negatives.
- `parse_json_dict(value)`: returns the parsed dict for valid JSON objects; returns `{}` for blank, invalid JSON, or any non-dict JSON type (list, scalar, null, boolean, number).
- `parse_tags(value)`: handles blank → `[]`, `{}` → `[]`, JSON list style `["a","b"]` via `ast.literal_eval`, Postgres brace style `{a,b}` and `{"a","b"}` via inner-split, comma-separated fallback for everything else. Strips whitespace and surrounding quotes from each tag.
- `read_products(path, source)`: reads CSV via `csv.DictReader`; quarantines rows with non-numeric `item_id` (reason `non_numeric_item_id`) or blank `name` (reason `blank_name`); raises `ValueError` for source other than "A" or "B"; preserves all original row fields in quarantined rows.

Verification:

- `python3 -m pytest tests/test_io.py -v`: 37 passed (import error before io.py existed).
- `python3 -m pytest`: 50 passed (37 IO + 12 sanity + 1 import contract).
- mini_a: 1 quarantined row (item_id ` | Pack of 12`, reason `non_numeric_item_id`); 38 valid rows.
- mini_b: 0 quarantined rows; 30 valid rows.

Blockers: none.

Next suggested action: Phase 4 — taxonomy and scope filter. Create `betterbasket_matcher/taxonomy.py` with a `matchable_group` function that maps (category_0, category_1, category_2) to a shared group key, plus an `in_scope_a` predicate that returns False for categories Wegmans cannot plausibly match (home decor, automotive, etc.). Write `tests/test_taxonomy.py` driven by the mini fixtures.

## Session - 2026-05-03 03:15 PDT

Completed:

- Phase 3: implemented normalization in `betterbasket_matcher/normalize.py`.
- Wrote `tests/test_normalize.py` first (TDD, 48 tests); confirmed ImportError before `normalize.py` existed.
- Implemented all normalization in `betterbasket_matcher/normalize.py`.
- No taxonomy, retrieval, scoring, or pipeline logic was added.
- Phase 1 and Phase 2 files were not modified.

Changed files:

- `betterbasket_matcher/normalize.py` (new)
- `tests/test_normalize.py` (new)
- `docs/HANDOFF.md` (updated — this entry)

Implementation summary:

- `SizeInfo`: dataclass with `unit`, `unit_size`, `pack_count`, `total_size`.
- `NormalizedProduct`: dataclass with all normalized attributes.
- `normalize_product(row, source)`: main public entry point.
- Brand normalization: lowercases and collapses whitespace; detects A private labels from a frozen set (`great value`, `marketside`, etc.); detects B private labels when `brand_norm == "wegmans"` or tags contain `wegmans brand`/`wegmans_brand`.
- Size parsing A: strips `(N Pack)` prefix from name, extracts `N oz` / `N gallon` etc. via regex, falls back to `sizing_comp.size_user_friendly`.
- Size parsing B: parses `sizing_comp.size_user_friendly`; handles `12 x 5.3 ounce` pack-x format and plain `8 ounce`.
- Unit normalization: ounce/oz → oz; gallon/gal → gal; lb/pound → lb; g/gram → g; kg → kg; ml → ml; l/liter → l; ct/pk/count → ct; fl oz → fl oz.
- Organic: A detected from name token "organic" or `item_info.is_organic`; B detected from `is_organic` column, `item_info.is_organic`, or `organic` tag.
- Storage, form, flavor: passed through from `item_info`.
- Categories: lowercased from `item_info.category_0/1/2`.
- `core_name`: strips pack prefix, brand prefix, size tokens, container words, then collapses whitespace.
- `retrieval_text`: brand (suppressed for private labels) + core_name + size_str + category tokens + attribute tokens, joined by single space with no leading/trailing whitespace.

Verification:

- `python3 -m pytest tests/test_normalize.py -v`: 48 passed (ImportError before normalize.py existed).
- `python3 -m pytest`: 98 passed (48 normalize + 37 IO + 12 sanity + 1 import contract).

Blockers: none.

## Session - 2026-05-03 03:45 PDT

Completed:

- Phase 3-fix: closed six defects in `betterbasket_matcher/normalize.py` detected by cross-checking the implementation against `docs/audit_stats.json` and `docs/retrieval_probe_results.md`.
- Wrote 38 new tests in `tests/test_normalize.py` first (TDD); confirmed they failed at collection (`ImportError: cannot import name 'infer_brand_from_name'`) before the implementation landed.
- Implementation is narrow: no taxonomy, scope, retrieval, rules, scoring, pipeline, or LLM logic was introduced. Phase 1 fixtures and Phase 2 `io.py` were not modified.

Changed files:

- `betterbasket_matcher/normalize.py` (modified)
- `tests/test_normalize.py` (modified)
- `docs/HANDOFF.md` (updated — this entry)

Defect summary:

- Defect 1 (`brand_inferred` hardcoded False) — added `infer_brand_from_name(name, known_brands=None)` returning `(canonical_brand, was_inferred)` and wired it into `normalize_product` for A rows with blank `brand_raw`. National-brand inference does not fire when `known_brands is None`.
- Defect 2 (PL detection used exact-match) — rewrote `_is_private_label_a` as longest word-boundary prefix match (`brand_norm == pl OR brand_norm.startswith(pl + " ")`), so `equate extra`/`marketside fresh`/`bettergoods organic` resolve to PL while `equator`/`greater value` do not collide.
- Defect 3 (PL set missing grocery brands) — extended `_PRIVATE_LABEL_A` with canonical entries `parent s choice`, `parents choice`, `ol roy`, `special kitty`, `clear american`. Variants stay out of the set; the prefix rule covers them.
- Defect 4 (attributes raw + no fallback) — added `_normalize_attribute` (lowercase/strip; non-string → None) and a conservative name-token fallback for `storage_type` (frozen/refrigerated; "powdered" stems to "powder"), `form` (powder/liquid/sliced/shredded/ground/whole_bean), and `flavor` (vanilla/chocolate). For B rows, tags are an extra source for storage. None means "unknown / not detected".
- Defect 5 (core_name brand strip with empty brand) — no separate code change required; once Defect 1 populates `brand_norm` for inferred-PL rows, the existing `text.startswith(brand_norm)` strip in `_build_core_name` handles the case. Locked by T3.
- Defect 6 (size float noise `8.0oz`) — added `_fmt_num` and applied to both single and pack-x branches of size rendering in `_build_retrieval_text`. Integral floats render as `"8"`, fractional floats keep their decimal (`"5.3"`).

Verification:

- Baseline before changes: `python3 -m pytest` → 98 passed.
- After tests added, before implementation: collection error (`ImportError: cannot import name 'infer_brand_from_name'`). Expected red.
- After implementation: `python3 -m pytest tests/test_normalize.py` → 86 passed; `python3 -m pytest` → 136 passed (86 normalize + 37 IO + 12 sanity + 1 import contract). Net delta: +38 tests, all green.
- `git diff --stat` confirms only `betterbasket_matcher/normalize.py` and `tests/test_normalize.py` changed; no fixtures, no `io.py`.

Open design notes for Phase 6:

- B `item_info.storage_type` is not a top key in `audit_stats`; B `storage_type` will usually be None unless the name or tag fallback fires. Phase 3 normalizes `item_info` attributes when present and applies a conservative name-token fallback for obvious storage/form/flavor signals. Phase 6 must still treat None as "unknown" (no mismatch fired) rather than rejecting.
- A `item_info.storage_type` IS a top key (201,970 rows, 86.6% coverage), so the storage hard rule will be informative on the A side.
- `infer_brand_from_name` accepts a `known_brands` set so Phase 6 (or a later phase) can pass a precomputed national-brand set when needed; calling without it preserves the conservative PL-only behavior.

Blockers: none.

Next suggested action: Phase 4 — taxonomy and scope. Create `betterbasket_matcher/taxonomy.py` with a `matchable_group` function and an `in_scope_a` predicate that excludes categories Wegmans cannot plausibly match. Drive it with the existing mini fixtures.

## Session - 2026-05-03 04:15 PDT

Completed:

- Phase 4: implemented matchable-group taxonomy and A-side scope filter.
- Wrote `tests/test_taxonomy_scope.py` first (TDD, 40 tests); confirmed `ModuleNotFoundError: No module named 'betterbasket_matcher.taxonomy'` before implementation.
- Implemented public API in two modules:
  - `betterbasket_matcher/taxonomy.py` — `assign_matchable_group(product)`, `groups_compatible(a_group, b_group)`, plus the package-internal `_norm_cat` helper.
  - `betterbasket_matcher/scope.py` — `is_a_in_scope(product)`.
- No retrieval, rules, scoring, pipeline, or LLM logic was added. Phase 1 fixtures, Phase 2 `io.py`, and Phase 3 `normalize.py` were not modified.

Changed files:

- `betterbasket_matcher/taxonomy.py` (new)
- `betterbasket_matcher/scope.py` (new)
- `tests/test_taxonomy_scope.py` (new)
- `docs/HANDOFF.md` (updated — this entry)

Behavior summary:

- Category normalization: `_norm_cat` lowercases, replaces `&` with `and`, collapses punctuation/whitespace, so `"Sports & Outdoors"` and `"Sports and Outdoors"` and `"Wine, Beer & Spirits"` all reach a stable form (`"sports and outdoors"`, `"wine beer and spirits"`).
- Stable group names exposed as `taxonomy.GROUPS`: pantry, snacks, candy, beverages, dairy, cheese, frozen, produce, meat, seafood, bakery, prepared_foods, baby, pets, household, personal_care, health, beauty, kitchen_home, wine_beer_spirits.
- Store A dispatch: prefers category fields over text. `Food > Dairy & Eggs > Cheese` overrides to `cheese`; `Food > Beverages > {Wine|Beer|Spirits|Liquor}` overrides to `wine_beer_spirits`. `Home > Kitchen & Dining` -> `kitchen_home`. Top-level mappings cover Baby/Pets/Household Essentials/Personal Care/Health and Medicine/Beauty.
- Store B dispatch: `Dairy > Cheese` (or `cheese` in `c2`) -> `cheese`, otherwise `dairy`. `More Departments > Kitchen and Home` -> `kitchen_home`. `Grocery` is disambiguated by `c1` to pantry/beverages/snacks/candy/baby/pets/household/personal_care/health/beauty. Top-level mappings cover Frozen, Produce & Floral, Meat, Seafood, Cheese, Bakery, Prepared Foods, Wine Beer & Spirits.
- `groups_compatible` is conservative: exact equality OR symmetric `dairy<->cheese` only. All other adjacencies (pantry<->snacks, beverages<->wine_beer_spirits, prepared_foods<->frozen, kitchen_home<->household, etc.) return False. Empty/None on either side returns False. Phase 9 calibration may widen this; Phase 4 does not.
- A-side scope: `is_a_in_scope` returns `(True, "not_store_a")` for B; `(False, "excluded_category")` for c0 in {Toys, Clothing, Electronics, Home Improvement, Books, Cell Phones, Sports & Outdoors, Party & Occasions, Office Supplies, Auto & Tires, Arts Crafts & Sewing, Jewelry}; `(False, "excluded_home_decor")` for `Home > {Home Decor, Picture Frames, Bedding, Furniture, Rugs, Wall Art}`; `(True, "in_scope")` otherwise (including `Home > Kitchen & Dining`).
- Functions read only existing `NormalizedProduct` fields (item_id, source, category_0/1/2, core_name). They do not access `tags` or `name_norm` and do not re-parse raw CSV.

Verification:

- Baseline before changes: `python3 -m pytest` -> 136 passed (Phase 3-fix baseline).
- After tests added, before implementation: `python3 -m pytest tests/test_taxonomy_scope.py` -> collection error (`ModuleNotFoundError: No module named 'betterbasket_matcher.taxonomy'`). Expected red.
- After implementation: `python3 -m pytest tests/test_taxonomy_scope.py -v` -> 40 passed; `python3 -m pytest` -> 176 passed (40 taxonomy/scope + 86 normalize + 37 IO + 12 sanity + 1 import contract). Net delta: +40 tests, all green.
- `git status --short` shows only the three new files added; no Phase 1/2/3 file was modified.

Open design notes for Phase 5 / 6:

- Phase 5 (TF-IDF retrieval) should call `is_a_in_scope` to drop A rows before fitting the vectorizer, and call `assign_matchable_group` on both stores so candidate retrieval can be partitioned by group.
- Phase 6 (hard rules) must call `groups_compatible(group_a, group_b)` as the first hard rule. Treat `None` group on either side as "do not match" (the function already returns False), so unclassifiable products are routed away from candidates.
- Cheese-vs-Dairy is the only documented adjacency. If Phase 9 reveals recall loss on, e.g., kitchen_home<->household, widen `_DAIRY_CHEESE` (or generalize) at that point — not in Phase 4.
- `_norm_cat` is private to `taxonomy` but re-imported by `scope`; if a third caller needs it, promote it from underscore-prefixed to a public helper.

Next suggested action: Phase 5 — TF-IDF candidate retrieval. Add `betterbasket_matcher/retrieval.py` that builds a sparse matrix from `retrieval_text` for B (post-`assign_matchable_group`), uses TF-IDF with word `(1, 2)` and char-wb `(3, 5)` n-grams (`scipy.sparse.hstack`), and exposes `top_k(query_product, k)` returning candidate B indices and similarity scores. Drive it with the existing mini fixtures.

Blockers: none.

## Session - 2026-05-03 04:45 PDT

Completed:

- Phase 5: implemented TF-IDF candidate retrieval in `betterbasket_matcher/retrieval.py`.
- Wrote `tests/test_retrieval.py` first (TDD, 20 tests); confirmed `ModuleNotFoundError: No module named 'betterbasket_matcher.retrieval'` before implementation.
- Implementation is narrow: no hard rules, scoring, pipeline, output validation, or LLM logic was added. Phase 1 fixtures and all Phase 0-4 modules were not modified.

Changed files:

- `betterbasket_matcher/retrieval.py` (new)
- `tests/test_retrieval.py` (new)
- `docs/HANDOFF.md` (updated — this entry)

Behavior summary:

- Public API: `build_retrieval_text(product) -> str`, `Candidate(item_id_b, score, rank)` (frozen dataclass), `TfidfRetriever.fit(products_b)` / `.query(product_a, k=50) -> list[Candidate]`.
- Architecture: **single global B-side index + compatible-group post-filter** (preflight-approved option). Per-group indexes deferred to Phase 9 calibration.
- Vectorizers: `TfidfVectorizer(analyzer="word", ngram_range=(1,2), lowercase=False, norm="l2", sublinear_tf=True)` + `TfidfVectorizer(analyzer="char_wb", ngram_range=(3,5), lowercase=False, norm="l2", sublinear_tf=True)`. `lowercase=False` because Phase 3 `retrieval_text` is already lowercased.
- Combination: `scipy.sparse.hstack([Xw, Xc]).tocsr()` once at fit; same transform applied to query at query time. Each L2-normalized sub-vector → concatenated row L2 = sqrt(2). Ranking is unaffected; absolute thresholds will be calibrated in Phase 9 against actual scores.
- Score: dot product `q @ X.T` (cosine-like with sqrt(2) scale).
- Group filter: `taxonomy.groups_compatible(a_group, b_group)` is applied per row before scoring. Conservative (exact match + symmetric `dairy<->cheese` only) carries through to retrieval.
- Edge cases (locked by tests):
  - `fit([])` raises `ValueError`.
  - `fit` with all-blank `retrieval_text` raises `ValueError`.
  - `fit` drops blank-text rows from the corpus so they cannot be returned as candidates.
  - `query` before `fit` raises `RuntimeError`.
  - `query` returns `[]` when A's group is None.
  - `query` returns `[]` when no B has a compatible group.
  - `query` returns `[]` when A's `retrieval_text` is blank/whitespace.
  - `query` clamps `k` to the compatible corpus size (no `argpartition` overflow).
- Determinism: tie-break is `(-score, item_id_b ascending)`. `query` is idempotent across calls.
- Ranks are 1-based and dense within the returned list.

Verification:

- Baseline before changes: `python3 -m pytest` -> 176 passed (Phase 4 baseline).
- After tests added, before implementation: `python3 -m pytest tests/test_retrieval.py` -> collection error (`ModuleNotFoundError: No module named 'betterbasket_matcher.retrieval'`). Expected red.
- After implementation: `python3 -m pytest tests/test_retrieval.py -v` -> 20 passed; `python3 -m pytest` -> 196 passed (20 retrieval + 40 taxonomy/scope + 86 normalize + 37 IO + 12 sanity + 1 import contract). Net delta: +20 tests, all green.
- PDF regression locked: A 2197626 finds B 92544 in top-5; A 1929544 finds B 105624 in top-5; all three tomato variants (105624 / 103620 / 1086860) appear in candidates with k=50, so Phase 7 size hard rule will be the layer that prunes wrong sizes.

Open design notes for Phase 6:

- Phase 6 (hard rules) consumes `list[Candidate]` from Phase 5 and rejects pairs that violate group / brand / size / pack / organic / form / storage / flavor / alcohol compatibility. Treat `None` attributes as "unknown / no mismatch fired" per the Phase 3-fix design note. The retrieval `score` field carries forward to scoring as the lexical-similarity component; do not re-tokenize from raw fields.
- Group filter is already applied in retrieval, but Phase 6 should re-assert `groups_compatible` defensively so callers that bypass retrieval (e.g., LLM arbiter feeding hand-chosen candidates) still respect the rule.
- The `is_a_in_scope` filter is **not** invoked inside `query`. The Phase 8 pipeline must drop out-of-scope A rows before calling `retriever.query`. Document this on the `pipeline.py` docstring.
- Score-scale note: scores are not in [0, 1]; expect roughly [0, sqrt(2)] in practice. Phase 9 calibration will set thresholds against this scale, or normalize at that point if convenient.
- Per-group indexes: `_PRIVATE_LABEL_A`-style global term frequencies dilute IDF on tiny groups (bakery, prepared_foods). If Phase 9 calibration shows recall loss on those groups, switch from a single matrix to a `dict[group, (vectorizer, matrix, ids)]` and refit per group. Not worth doing speculatively in Phase 5.

Next suggested action: Phase 6 — hard rules and deterministic scoring. Add `betterbasket_matcher/rules.py` with a `passes_hard_rules(a, b) -> tuple[bool, str]` predicate over (group, brand/PL, size, pack, organic, form, storage, flavor, alcohol) and `betterbasket_matcher/scoring.py` with the documented weighted scorer. Drive both with the mini fixtures.

Blockers: none.

## Session - 2026-05-03 05:30 PDT

Completed:

- Phase 6: implemented hard compatibility rules and deterministic scoring.
- Wrote `tests/test_rules.py` (27 tests) and `tests/test_scoring.py` (21 tests) first (TDD); confirmed both failed with `ModuleNotFoundError` before implementation.
- Implemented `betterbasket_matcher/rules.py` (`RuleResult`, `evaluate_hard_rules`) and `betterbasket_matcher/scoring.py` (`ScoreBreakdown`, `SelectionResult`, `score_pair`, `select_best`).
- No pipeline orchestration, output writing, LLM arbitration, or earlier-phase modules were modified. Phase 1 fixtures were not touched.

Changed files:

- `betterbasket_matcher/rules.py` (new)
- `betterbasket_matcher/scoring.py` (new)
- `tests/test_rules.py` (new)
- `tests/test_scoring.py` (new)
- `docs/HANDOFF.md` (updated — this entry)

Behavior summary:

- `RuleResult(passed: bool, reason: str)` is `frozen=True`; `reason="ok"` on pass; stable failure reasons: `alcohol_mismatch`, `group_mismatch`, `national_vs_private_label`, `brand_mismatch`, `storage_mismatch`, `form_mismatch`, `pet_type_mismatch`, `flavor_mismatch`, `size_mismatch`, `pack_mismatch`.
- `evaluate_hard_rules` ordering (first failing rule wins): alcohol → group → national_vs_pl → brand → storage → form → pet_type → flavor → size → pack. Storage/form/pet/flavor are checked before size because differing forms (powder vs liquid, sliced vs shredded) make total-size comparison meaningless. Alcohol runs before group so the reason is the more specific one.
- `_to_base_size(size)` is a Phase-6-local helper that converts `SizeInfo` to `(family, base_value)`. Weight family → base ounces (`oz`×1, `lb`×16, `g`×0.035274, `kg`×35.274); volume family → base fluid ounces (`fl oz`×1, `ml`×0.033814, `l`×33.814, `gal`×128); count family → `ct`/`pk`/`count`×1. The function returns `None` if `unit` is unrecognized or no usable quantity is present (`total_size` preferred, falls back to `unit_size` only when `pack_count==1`). `normalize.py` is unchanged.
- Size rule reject threshold: 8% relative diff on converted base values, only when families match. 16 oz ↔ 1 lb passes (same family, equal). 8 oz weight vs 8 fl oz volume skips (different families).
- Pack rule uses observable `pack_count` only (no "explicit" flag): both >1 differ AND base diff >1% → reject; one >1 vs one =1 AND base diff >8% → reject; otherwise no rule.
- Brand: confidently known nationals = `brand_norm != "" and not brand_inferred and not is_private_label`. `national_vs_private_label` requires exactly one PL side and a confidently known national on the other. `brand_mismatch` requires both confidently known and names differ.
- Alcohol: `_is_alcoholic` returns True if matchable group is `wine_beer_spirits` OR `core_name` matches a conservative regex (`wine|beer|ale|lager|spirits|vodka|whiskey|whisky|rum|gin|tequila|liqueur|champagne|sake` plus `hard cider`). Mismatch fires before group check.
- Pet type: only fires when both products are in the `pets` group AND `core_name`/`category_2` token detection finds different species (cat/kitten vs dog/puppy).
- None semantics: storage_type/form/flavor `None` on either side never rejects. `is_organic` is bool only and is intentionally NOT a hard rule (scoring-only); confirmed by `test_organic_true_vs_false_does_not_hard_reject`.
- `score_pair` weights are exactly `0.35 / 0.15 / 0.15 / 0.20 / 0.10 / 0.05` (core_name/token_overlap/brand/size_pack/group/attributes); the dataclass `total` equals the weighted sum within 1e-9.
- `core_name` component normalizes the retrieval score by `min(max(retrieval_score / 2.0, 0.0), 1.0)`. Phase 5 hstacks two L2-normalized blocks without renormalizing the row, so an identical-text dot product reaches ~2.0 (not sqrt(2)). Boundary tests lock 2.0→1.0, 1.0→0.5, 0.0→0.0, overshoot→1.0, negative→0.0.
- Brand scoring: 1.0 for two confidently known equal nationals; 0.85 for both PL (cross-store); 0.6 if exactly one side is empty/inferred; 0.5 if both empty; 0.0 if both confidently known and differ.
- Size_pack scoring uses `_to_base_size` (never raw `total_size`): 1.0 if same family and ≤1% diff and equal pack_count; 0.7 if ≤5% and equal pack_count; 0.4 if either side returns `None` from the converter; 0.0 otherwise.
- Group scoring: 1.0 equal; 0.7 dairy↔cheese; 0.0 otherwise.
- Attribute scoring: equal-weighted average over storage_type/form/flavor (Optional → 1.0/0.5/0.0 with 0.5 for unknown) plus is_organic (bool → 1.0 if equal, 0.0 if true-vs-false; never 0.5 because there is no None for bools — true-vs-false is penalized, not treated as unknown).
- `select_best` accepts `Iterable[tuple[NormalizedProduct, float]]` of `(b_product, retrieval_score)`. Reasons: `selected`, `no_candidates`, `all_rejected`, `below_min_score`, `below_min_margin`. Rejected candidates returned as `tuple[tuple[item_id_b, rule_reason], ...]` for audit. `breakdown` and `runner_up_item_id_b` are populated even on threshold/margin failure.
- Tie-break order (descending preference): total → exact size match → equal pack_count → attribute agreement count → B metadata richness → ascending `int(item_id_b)`.

Verification:

- Baseline before changes: `python3 -m pytest -q` → 196 passed.
- After tests added, before implementation: `python3 -m pytest tests/test_rules.py tests/test_scoring.py -q` → 2 collection errors (`ModuleNotFoundError: No module named 'betterbasket_matcher.rules'` and `'betterbasket_matcher.scoring'`). Expected red.
- After implementation: `python3 -m pytest tests/test_rules.py tests/test_scoring.py -v` → 48 passed; `python3 -m pytest -q` → 244 passed (48 rules+scoring + 20 retrieval + 40 taxonomy/scope + 86 normalize + 37 IO + 12 sanity + 1 import contract). Net delta: +48 tests, all green.
- `git status --short` shows only the four new files added; `git diff --stat -- betterbasket_matcher/io.py betterbasket_matcher/normalize.py betterbasket_matcher/taxonomy.py betterbasket_matcher/scope.py betterbasket_matcher/retrieval.py tests/fixtures` is empty (no earlier-phase modules or fixtures touched).
- PDF regressions verified inside the test suite: A 2197626 vs B 92544 → `passed=True`; A 1929544 vs B 105624 → `passed=True`; A 1929544 vs B 103620 (15 oz) and B 1086860 (29 oz) → `size_mismatch`; `select_best` for A 1929544 with all three tomato candidates returns `selected_item_id_b="105624"` and lists the other two with reason `size_mismatch`.

Open design notes for Phase 7 (pipeline + output):

- Phase 7 should orchestrate: (1) `read_products` for A and B; (2) `normalize_product` per row; (3) `is_a_in_scope` filter on A (note: `query` does NOT call this — pipeline must); (4) build B index `dict[item_id, NormalizedProduct]`; (5) fit `TfidfRetriever` on normalized B; (6) for each in-scope A: `retriever.query(a, k=50)`, build pairs `[(b_index[c.item_id_b], c.score) for c in cands]`, call `select_best`; (7) write `matches.csv` and `matches_audit.csv`.
- Three fixture rows have `category_1` strings the Phase 4 taxonomy doesn't yet map to a B `grocery` group (`Soups`, `Nut Butters & Spreads`, `Baking`). The Phase 6 hard-rule tests for those storage/size cases were therefore written with synthetic NormalizedProduct objects that share a mapped group; the rules themselves are exercised correctly. Phase 9 calibration may want to widen `_B_GROCERY_C1` to include those c1 strings to recover real-world recall, but Phase 6 made no taxonomy changes.
- `min_score` and `min_margin` are not yet calibrated. Pick provisional values for the first end-to-end pipeline run (e.g., `min_score=0.55`, `min_margin=0.05`); Phase 9 will tune against the full dataset.
- The retrieval-score scale `[0, 2]` (because Phase 5 hstacks two L2-normalized blocks without renormalizing the row) is documented in `score_pair` and `TestCoreNameNormalization`. If Phase 9 ever switches retrieval to a renormalized output, change the single divisor in `_score_core_name`.
- `_to_base_size` lives in `rules.py` and is reused by `scoring.py`; a future module that needs unit conversion (e.g., a calibration helper) can import it from `rules`. Do not duplicate the table.
- Hard rules order matters: alcohol must stay above group, and storage/form/pet/flavor must stay above size to keep the right reason fired for the powder-vs-liquid (82.5 oz vs 64 oz) and similar cases.
- Output validation is intentionally NOT in Phase 6. Phase 7 (or a Phase 8 dedicated to it) owns the `matches.csv` shape, header check, A-set membership, dedup-by-item_id_A, and ≥4,000-row floor.

Blockers: none.

Next suggested action: Phase 7 — pipeline orchestration and output. Create `betterbasket_matcher/pipeline.py` (and `output.py` if desired) plus `scripts/run_pipeline.py`. Wire IO → normalize → scope filter → retrieval (Phase 5) → rules+scoring (Phase 6) → write `matches.csv` and `matches_audit.csv`. Drive an end-to-end test against the mini fixtures asserting the PDF regressions and a ≥-N-rows floor on the mini fixtures. Keep all hard rules and the LLM-arbiter ban from Phase 6 unchanged.

## Session - 2026-05-03 12:43 PDT

Phase 6 fix pass — addressed the three MAJOR findings from the hostile review (no Phase 7/pipeline/output/LLM scope creep, no fixture changes, no earlier-phase module changes).

Completed:

- M1 alcohol false positives — `_is_alcoholic` now applies a `_NON_ALCOHOL_PHRASE_RE` negative override before the keyword fallback. Phrases blocked: `ginger ale`, `(ginger|root|birch) beer`, `wine vinegar`, `cooking (wine|sherry)`, `rum (cake|extract|raisin)`, `beer (cheese|bread|battered)`, `non[-]?alcoholic`, `alcohol[-]?free`. The `wine_beer_spirits` group remains a strong positive signal (no negative override applied to the group path; department classification is trusted there). The original keyword regex (`wine|beer|ale|lager|spirits|vodka|whiskey|whisky|rum|gin|tequila|liqueur|champagne|sake` + `hard cider`) is unchanged.
- M2 pack_mismatch coverage and dead-branch removal — removed the unreachable `((pa>1) ^ (pb>1)) and diff > 0.08` branch (any drift >8% already triggers `size_mismatch` at rule 9, so the second branch was dead). The remaining rule fires only when both sides are multipack, pack counts differ, and converted base size drifts >1%. Added explicit positive (fires) and negative (doesn't fire) coverage including the multipack-repackage case (1×32oz vs 4×8oz, equal totals → no rule fires).
- M3 single-survivor margin behavior — `select_best` now returns `margin=None` when there is only one rule-surviving candidate and skips the `min_margin` gate in that case. Previously a single survivor compared against `runner_total=0.0`, which made `min_margin` falsely trigger `below_min_margin` whenever `top.total < min_margin`. The two-survivor path is unchanged: margin is computed as `top.total - runner_up.total` and `min_margin` still gates.

Changed files:

- `betterbasket_matcher/rules.py` (modified)
- `betterbasket_matcher/scoring.py` (modified)
- `tests/test_rules.py` (extended — `TestAlcoholFalsePositives`, `TestPackMismatch`)
- `tests/test_scoring.py` (extended — `TestSingleSurvivorMargin`, plus margin assertions in `test_below_min_margin`)
- `docs/HANDOFF.md` (this entry)

Behavior summary (deltas only):

- `_is_alcoholic(p, group)`: returns True if `group == "wine_beer_spirits"`; otherwise returns False if `_NON_ALCOHOL_PHRASE_RE` matches `core_name`, else returns True if the alcohol keyword regex (or `hard cider`) matches, else False. The negative-override-before-keyword order is the locked invariant.
- pack rule (rule 10): exactly one branch — both `pa > 1` and `pb > 1` and `pa != pb` and converted base diff > 1% → reject `pack_mismatch`. All other shapes fall through to `_PASS`. `size_mismatch` (rule 9) is the sole guard for total-size drift across pack-count shapes.
- `select_best` margin: `None` when `len(survivors) == 1`; populated when `len(survivors) >= 2`. `below_min_margin` reason now only reachable from the multi-survivor path. `below_min_score` and `selected` reasons both report `margin=None` for single-survivor cases (visible in audit output).

Verification:

- Baseline before changes: `python3 -m pytest tests/test_rules.py tests/test_scoring.py` → 48 passed.
- Red phase (tests added before implementation): `python3 -m pytest tests/test_rules.py tests/test_scoring.py` → 8 failed, 60 passed (5 alcohol asymmetric tests + 3 single-survivor margin tests). M2 pack tests passed under current code (they lock in already-correct positive/negative coverage; the dead-branch removal needed no failing test).
- Green phase: `python3 -m pytest tests/test_rules.py tests/test_scoring.py -v` → 68 passed (48 baseline + 20 new: 13 `TestAlcoholFalsePositives` + 4 `TestPackMismatch` + 3 `TestSingleSurvivorMargin`).
- Full suite: `python3 -m pytest -q` → 264 passed (was 244; net delta +20). No regressions in retrieval, taxonomy, scope, normalize, IO, or sanity tests.
- `git diff --stat -- tests/fixtures betterbasket_matcher/io.py betterbasket_matcher/normalize.py betterbasket_matcher/taxonomy.py betterbasket_matcher/scope.py betterbasket_matcher/retrieval.py` → empty. No fixture or earlier-phase module touched.
- PDF regressions still hold: `evaluate_hard_rules` passes A 2197626 vs B 92544 and A 1929544 vs B 105624; rejects A 1929544 vs B 103620/1086860 with `size_mismatch`; `select_best` picks B 105624 for the tomato case.

Open notes for Phase 7:

- The single-survivor margin contract (`margin=None`, no `min_margin` check) is the right semantic for the pipeline's audit CSV: write `margin` as empty cell when None, not `0.0`. The downstream output module should not assume `margin` is always numeric.
- No Phase 7/pipeline/output/LLM logic was added in this fix pass. The Phase 6 layer remains pure functions.
- The non-alcohol phrase list is conservative; if future analysis surfaces additional false positives (e.g. `mocktail`, `tequila lime [snack/seasoning]`, `champagne vinaigrette`), extend `_NON_ALCOHOL_PHRASE_RE` rather than weakening the keyword regex.

Blockers: none.

Next suggested action: Phase 7 — pipeline orchestration and output, unchanged from the prior session's recommendation.

## Session - 2026-05-03 14:30 PDT

Completed:

- Phase 7: implemented end-to-end fixture pipeline orchestration and output validation.
- Wrote `tests/test_pipeline_fixtures.py` (25 tests) and `tests/test_output_validation.py` (17 tests) first (TDD); confirmed `ModuleNotFoundError` on collection before implementation.
- Extended `tests/test_scoring.py` with 6 tests locking the `top_item_id_b` contract (one dataclass-default test plus the five `reason`-branch assertions). Confirmed red with `AttributeError: 'SelectionResult' object has no attribute 'top_item_id_b'` before the `scoring.py` edit landed.
- Implemented Phase 7 modules narrowly: `betterbasket_matcher/output.py`, `betterbasket_matcher/pipeline.py`, `scripts/run_pipeline.py`. The only earlier-phase touch is the approved `scoring.py` extension to expose `top_item_id_b` (no behavior change beyond surfacing the top survivor id).
- No taxonomy/rules/retrieval/normalize/IO/scope edits; no fixture edits; no LLM logic; no calibration; no full-dataset run.

Changed files:

- `betterbasket_matcher/output.py` (new)
- `betterbasket_matcher/pipeline.py` (new)
- `scripts/run_pipeline.py` (new)
- `tests/test_pipeline_fixtures.py` (new)
- `tests/test_output_validation.py` (new)
- `betterbasket_matcher/scoring.py` (modified — `SelectionResult.top_item_id_b` field with default `None`, populated in all four return paths; renamed local `top_b.item_id` to a single `top_id` binding)
- `tests/test_scoring.py` (modified — `TestSelectionResultDataclass.test_top_item_id_b_field_defaults_none` and a new `TestTopItemIdB` class with 5 assertions)
- `docs/HANDOFF.md` (this entry)

Behavior summary:

- `PipelineConfig(a_csv, b_csv, matches_out, audit_out, min_score=0.55, min_margin=0.05, top_k=50, limit=None)`. `PipelineResult` carries `matches`, `audit_rows`, plus 7 counts (`valid_a_count`, `quarantined_a_count`, `valid_b_count`, `in_scope_a_count`, `accepted_count`, `rejected_by_rule_count`, `below_threshold_count`, `no_candidates_count`). `valid_a_count` reflects the count actually processed (post-quarantine, post-`limit`) so the audit-completeness invariant `len(audit_rows) == valid_a_count` holds for both the no-limit and limited paths.
- `run_pipeline` flow: `read_products` quarantines non-numeric/blank-name A rows, `normalize_product` builds `NormalizedProduct`s for A and B, `--limit` truncates the normalized A list **after** quarantine and **before** scope/retrieval, `TfidfRetriever().fit(b_products)` runs once, then per-A: `is_a_in_scope` -> `retriever.query` -> `select_best` -> exactly one audit row. Out-of-scope A rows skip retrieval and emit `decision="no_candidates"` with `reason` set to the literal scope reason (`excluded_category`, `excluded_home_decor`, etc.). In-scope A rows that get zero retrieval candidates emit `decision="no_candidates"` with `reason="no_candidates"`.
- Audit row schema is exact: `item_id_A,item_id_B,score,retrieval_score,top1_top2_margin,source,decision,reason,llm_confidence`. `source` is the constant `"deterministic"` for every Phase 7 audit row; `llm_confidence` is always empty.
- Decision mapping (locked by tests): `selected` -> `decision=accepted`, `reason=ok`; `below_min_score`/`below_min_margin` -> `decision=below_threshold`, `item_id_B=top_item_id_b`; `all_rejected` -> `decision=rejected_by_rule`, `item_id_B=rejected[0][0]`, `reason=rejected[0][1]`; `no_candidates` -> `decision=no_candidates`. `top1_top2_margin` is serialized as an empty string when `SelectionResult.margin is None` (single-survivor case), never as `0.0`.
- `output.write_matches` writes `item_id_A,item_id_B` followed by accepted pairs in pipeline insertion order. `output.write_matches_audit` writes the locked 9-column header followed by audit rows. `output.validate_matches_csv(path, valid_ids_a, valid_ids_b, min_rows, required_pairs) -> ValidationResult(ok, errors, row_count)` collects header / numeric / membership / dedup / required-pair / `min_rows` violations into a single `errors` list and never raises on data violations. The duplicate check fires even when the duplicate row is byte-identical to its twin. `min_rows` is always honored as supplied; the CLI's `--allow-under-min-rows` is a CLI concern only.
- CLI (`scripts/run_pipeline.py`): argparse with `--a-csv`, `--b-csv`, `--matches-out`, `--audit-out`, `--min-score`, `--min-margin`, `--top-k`, `--min-rows`, `--allow-under-min-rows`, `--limit`. Prints a Phase 7 banner, the PipelineResult counts, and the ValidationResult summary; exits non-zero if validation fails. Required-pair gate is hard-coded to the two PDF regressions. The CLI script also adds the project root to `sys.path` so it is runnable from a clean checkout without editable installs.
- `SelectionResult.top_item_id_b` (default `None`) is populated as: `selected` -> equals `selected_item_id_b`; `below_min_score`/`below_min_margin` -> top scored survivor's `item_id` (resolved at sort time, not by the pipeline); `no_candidates`/`all_rejected` -> `None`. Pipeline never duplicates `select_best` logic; `runner_up_item_id_b` is not used as a fallback.

Verification:

- Baseline before changes: `git status --short` clean; `python3 -m pytest -q` -> 264 passed.
- Red phase: `python3 -m pytest tests/test_pipeline_fixtures.py tests/test_output_validation.py tests/test_scoring.py -q` -> 2 collection errors (`ModuleNotFoundError: No module named 'betterbasket_matcher.pipeline'` / `'betterbasket_matcher.output'`) plus 6 `AttributeError: ... no attribute 'top_item_id_b'` failures from the scoring extensions.
- Green phase per file: `python3 -m pytest tests/test_scoring.py -q` -> 30 passed; `python3 -m pytest tests/test_output_validation.py -q` -> 17 passed; `python3 -m pytest tests/test_pipeline_fixtures.py -q` -> 25 passed.
- Full suite: `python3 -m pytest -q` -> 312 passed (264 baseline + 48 new). No regressions.
- CLI fixture smoke: `python3 scripts/run_pipeline.py --a-csv tests/fixtures/mini_a.csv --b-csv tests/fixtures/mini_b.csv --matches-out /tmp/bb_matches.csv --audit-out /tmp/bb_matches_audit.csv --allow-under-min-rows --min-rows 1 --limit 50` -> exit 0; counts: `valid_a=38 quarantined_a=1 valid_b=30 in_scope_a=36 accepted=9 rejected_by_rule=14 below_threshold=2 no_candidates=13`; `validation: ok=True row_count=9 errors=0`. PDF regressions visible in audit output: `2197626,92544,...,deterministic,accepted,ok,` and `1929544,105624,...,deterministic,accepted,ok,`. Quarantined A id (`" | Pack of 12"`) absent from both files.
- `git diff --stat -- tests/fixtures betterbasket_matcher/io.py betterbasket_matcher/normalize.py betterbasket_matcher/taxonomy.py betterbasket_matcher/scope.py betterbasket_matcher/retrieval.py betterbasket_matcher/rules.py` -> empty. `git status --short` shows exactly the seven Phase 7 deltas (5 new files + scoring.py + test_scoring.py).
- All 7 expected MATCH rows from `tests/fixtures/expected_matches.csv` are present verbatim in `matches.csv`; all 10 NO_MATCH `item_id_A` values are absent from `matches.csv` and have audit rows with `decision in {rejected_by_rule, below_threshold, no_candidates}`. The fixture pipeline test would have stopped and reported any oracle failure; none occurred.

Open design notes for Phase 8 (full dataset run):

- The CLI is reproducible against the real Walmart/Wegmans CSVs, but Phase 8 should: (a) run on the full `/Users/jirustaroure/Downloads/grocery_store_{a,b}_items_final.csv` paths with default `--min-rows=4000`; (b) confirm at least 4,000 accepted matches; (c) verify the two PDF pairs survive at scale; (d) capture the PipelineResult counts in the next handoff entry. Do not enable `--allow-under-min-rows` in production submission.
- Provisional thresholds (`min_score=0.55`, `min_margin=0.05`) cleared all 7 mini-fixture MATCH cases without weakening. They are unverified at scale; Phase 9 calibration may tune them. If accepted_count is far from 4,000 on the full dataset, calibrate before declaring submission ready.
- 2 incidental filler matches landed on the mini fixtures (A `9000105` Pepsi -> B `9100105` Pepsi and A `9000107` Great Value Mozzarella -> B `9100107` Wegmans Mozzarella). Both look correct on inspection; they are not part of the oracle but do not regress it.
- The audit CSV is one row per valid A processed. For the full dataset (~233k A rows minus 5 quarantined minus out-of-scope), the audit file will be on the order of hundreds of MB. If that becomes inconvenient, Phase 8 can add a streaming writer or a downsampled audit; the function signature already supports an `Iterable`.
- `--limit` is a fixture/dev convenience and is not exposed for the production submission; document this in the Phase 12 polish step.
- Three B `c1` strings (`Soups`, `Nut Butters & Spreads`, `Baking`, plus several others observed in mini_b: `Cereals & Oatmeal`, `Bars & Crackers`, `Coffee & Tea`, `Cooking Oils & Vinegars`, `Chips & Pretzels`) currently land at `group=None` and contribute to the `no_candidates` audit count. Phase 9 calibration is the place to decide whether widening `_B_GROCERY_C1` recovers measurable recall.
- The two `below_threshold` audit rows on the mini fixtures are A `9000118` Prego (one survivor below 0.55 because the only group-compatible B is Heinz Ketchup) and A `9000120` Great Value Ranch Dressing (similar). Both correctly choose not to emit a match; the Phase 8 calibration loop should sanity-check whether the 0.55 floor over-suppresses real matches at scale.

Blockers: none.

Next suggested action: Phase 8 — full dataset run. Use `python3 scripts/run_pipeline.py --a-csv /Users/jirustaroure/Downloads/grocery_store_a_items_final.csv --b-csv /Users/jirustaroure/Downloads/grocery_store_b_items_final.csv --matches-out matches.csv --audit-out matches_audit.csv` (no `--allow-under-min-rows`, no `--limit`), record runtime + PipelineResult counts + validation outcome, and confirm both PDF regressions survive at scale before progressing to Phase 9 threshold calibration.

