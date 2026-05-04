# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Operational source of truth

`docs/HANDOFF.md` is the implementation source of truth. Read it first. It records the current phase state, completed work, verification commands, blockers, and the next suggested action. Every implementation session ends by appending a new `## Session - YYYY-MM-DD HH:MM <TZ>` block to it.

The other canonical docs are reference, not workflow:

- `docs/dataset_audit.md` + `docs/audit_stats.json` — what the real CSVs actually contain (decoys, brand coverage, malformed rows, duplicates).
- `docs/algorithm_recommendation.md` — the precision-first deterministic pipeline design.
- `docs/retrieval_probe_results.md` + `docs/retrieval_probe_results.json` — TF-IDF / BM25 dry-run that motivates private-label brand suppression and the "Great Value → Great Lakes" trap that hard rules must prevent.
- `README.md`, `solution.md`, `solutioneasyexplained.md`, `app.md` — narrative retellings; do not drive implementation from them.

## Common commands

```bash
python3 -m pytest                                  # full suite
python3 -m pytest tests/test_normalize.py -v       # one module
python3 -m pytest tests/test_normalize.py::TestBrandInferenceCanonical::test_great_value -v   # one test
pip install -r requirements.txt                    # deps: scikit-learn, scipy, pytest, rank-bm25, pyyaml, openai
```

The pipeline entry point (`scripts/run_pipeline.py`) and the executable matcher are not built yet; check `docs/HANDOFF.md` for which phase is current. The two existing scripts are reproducible probes, not the matcher:

```bash
python3 scripts/audit_data.py     --a-csv ... --b-csv ... --json-out docs/audit_stats.json --markdown-out docs/dataset_audit_stats.md
python3 scripts/retrieval_probe.py --a-csv ... --b-csv ... --json-out docs/retrieval_probe_results.json --markdown-out docs/retrieval_probe_results.md
```

The real CSVs live at `/Users/jirustaroure/Downloads/grocery_store_{a,b}_items_final.csv`. They are not committed and must not be copied into the repo.

## Phased implementation discipline

The matcher is being built in numbered phases (0–12) tracked in `docs/HANDOFF.md`. Each phase writes one or two production modules plus its test file; later phases must not be implemented early. Current state at time of writing: Phases 0–4 done; Phase 5 (TF-IDF retrieval) is next. The phases that still need modules: `retrieval.py`, `rules.py`, `scoring.py`, `pipeline.py`, `output.py`, optional `llm_arbiter.py`, plus `scripts/run_pipeline.py`.

Workflow per phase, enforced by every prior session:

1. **Baseline gate** — `git status --short`, `git diff --stat`, `python3 -m pytest`. If the suite is not green at the count recorded in `HANDOFF.md`, stop and report.
2. **Tests first (TDD)** — write the new test file, confirm it fails for the expected reason (ImportError, ModuleNotFoundError, or specific assertion).
3. **Narrow implementation** — only the files the phase scopes; no unrelated refactors; do not touch fixtures or earlier-phase modules unless the phase explicitly authorizes it.
4. **Verify** — re-run the new test file, then the full suite. Test count must monotonically increase.
5. **HANDOFF entry** — append a session block: completed, changed files, behavior summary, verification commands and counts, open design notes for the next phase, blockers, next suggested action.
6. **Do not commit** unless the user explicitly asks.

## Frozen artifacts

- `tests/fixtures/mini_a.csv`, `tests/fixtures/mini_b.csv`, `tests/fixtures/expected_matches.csv` — Phase 1 oracles. They encode 17 case_ids (PDF examples plus targeted positive/negative cases) and must not be edited. If a defect appears to require a new fixture row, stop and report instead of editing.
- The 100% blank decoy columns in source CSVs (`name_clean`, `category`, `department`, `subcategory`, `size_raw`, `item_type`, `is_private_label`, `is_organic`) must never be trusted; everything is read from `name`, `brand_raw`, `item_info`, `sizing_comp`, and B `tags`.

## Architecture

The matcher is a single Python package, `betterbasket_matcher/`, with strictly layered modules. Each module reads only from earlier layers; tests in `tests/test_*.py` mirror the layering.

```
io.py          → read_products, parse_json_dict, parse_tags, is_numeric_id
normalize.py   → NormalizedProduct, SizeInfo, normalize_product, infer_brand_from_name, _is_private_label_a
taxonomy.py    → assign_matchable_group, groups_compatible, GROUPS, _norm_cat
scope.py       → is_a_in_scope                       (depends on taxonomy._norm_cat)
[future] retrieval.py → top_k(query_product, k)      (consumes NormalizedProduct.retrieval_text)
[future] rules.py     → hard rules before scoring    (group, brand/PL, size, pack, organic, form, storage, flavor, alcohol)
[future] scoring.py   → weighted deterministic score + margin check
[future] pipeline.py  → orchestrates all of the above, one best B per A
[future] output.py    → matches.csv + matches_audit.csv
```

Critical contracts that span modules:

- **Numeric ID quarantine** — `read_products` returns `(valid_rows, quarantined_rows)`; the 5 malformed A rows (e.g. `item_id=" | Pack of 12"`) must never reach normalization or output.
- **Tolerant parsing** — `item_info` and `sizing_comp` are JSON dicts that may be blank, malformed, or non-dict; `parse_json_dict` returns `{}` on anything that isn't a dict. B `tags` come in **two** wire formats (postgres `{"a","b"}` and Python list `["a","b"]`); `parse_tags` handles both.
- **Brand inference (A only)** — when `brand_raw` is blank, `infer_brand_from_name` matches the leading tokens of `name` against the canonical private-label set with **longest word-boundary prefix match**. `"Equate Extra Strength"` → `"equate"` (canonical), not `"equate extra"`. National brand inference fires only when `known_brands` is passed (currently never; reserved for a later phase). The `brand_inferred` flag must reflect reality so brand-compatibility scoring can downweight inferred brands.
- **Private-label detection is prefix-match, not exact match** — `_is_private_label_a("equate extra")` is True; `_is_private_label_a("equator")` is False. `_PRIVATE_LABEL_A` holds canonical brand strings only; never add variants.
- **Private-label brand suppression in `retrieval_text`** — for PL items the brand token is omitted from `retrieval_text` so TF-IDF cannot pull `Great Value Provolone` to `Great Lakes Provolone Cheese`. This is the documented "Great Value → Great Lakes" trap; any change that re-introduces the brand token regresses Phase 3.
- **Attribute fields mean "unknown" when None** — `storage_type`, `form`, `flavor` are normalized from `item_info` when present, then fall back to a conservative whole-word name-token scan (and tag scan for B storage). `None` means not detected; Phase 6 hard rules must treat `None` as no-mismatch, not as a rejection.
- **Asymmetric size parsing** — A reads size from `name` (with `(N Pack)` prefix extraction); B reads size from `sizing_comp.size_user_friendly` (with `12 x 5.3 ounce` pack-x format). Both produce a `SizeInfo(unit, unit_size, pack_count, total_size)`. The retrieval-text size renderer drops `.0` on integer-valued floats (`"8oz"`, not `"8.0oz"`).
- **`groups_compatible` is conservative** — exact group equality OR symmetric `dairy ↔ cheese` only. Every other pair returns False. Widen this in Phase 9 calibration if recall loss is measured, never speculatively. `None`/`""` group on either side returns False, so unclassifiable products are routed away from candidates.
- **A-only scope filter** — `is_a_in_scope` returns `(True, "not_store_a")` for B (so the same call site works for both stores), `(False, "excluded_category")` for the 12 Walmart-only top-level categories, `(False, "excluded_home_decor")` for `Home > {Home Decor, Picture Frames, Bedding, Furniture, Rugs, Wall Art}`, `(True, "in_scope")` otherwise. Reasons are stable strings used by the audit output.
- **Category normalization** — `_norm_cat` lowercases, replaces `&` with `and`, collapses punctuation/whitespace. Mapping dictionaries use the normalized form (`"sports and outdoors"`, `"wine beer and spirits"`, `"dairy and eggs"`); never key on title-case or ampersand variants.

## PDF regression invariants

These two end-to-end matches are baked into the assignment and into Phase 1 fixtures; they must remain correct through every phase:

- A `2197626` (Chobani 5.3 oz honey blended yogurt) → B `92544`.
- A `1929544` (Great Value Organic Tomato Sauce 8 oz) → B `105624`, **not** the 15 oz B `103620` or the 29 oz B `1086860`.

## Output contract

Before any pipeline run is declared done, `matches.csv` must satisfy:

- exact header `item_id_A,item_id_B`;
- only numeric IDs that exist in the validated A and B ID sets (no quarantined rows);
- no duplicate `item_id_A`;
- at least 4,000 rows;
- both PDF examples resolve to the correct sizes.

A companion `matches_audit.csv` with score, source, margin, and reason columns is required.

## Constraints baked into the design

- **No pandas.** Streaming CSV plus `csv.DictReader` is the only ingest path. `scripts/audit_data.py` is deliberately pandas-free for memory; the same constraint applies to the matcher.
- **No all-pairs comparison, no LLM-first matching, no UPC-first matching, no fuzzy-only matching.** UPC join is not viable on this dataset (A has zero UPC-like fields).
- **Hard rules run before scoring; the LLM arbiter (if added) cannot override them.**
- **No emojis in code or commits.** Use markdown ASCII arrows (`->`) in docs/comments rather than Unicode.
- **Do not commit unless the user explicitly asks.**
