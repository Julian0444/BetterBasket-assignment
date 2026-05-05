# BetterBasket — Cross-Retailer Product Matching

Author: Julian Irusta Roure
Assignment: BetterBasket Engineering Technical Assessment
Stores: Walmart (A) ↔ Wegmans (B)

## What this repo is

An executable, deterministic, precision-first product-matching pipeline for the BetterBasket assignment. It reads the Walmart/Wegmans CSVs, normalizes product attributes, retrieves candidates with TF-IDF, applies compatibility rules, scores one best B candidate per A item, and writes the required `matches.csv` plus a richer `matches_audit.csv`.

## Canonical sources of truth

The repository is organized so that exactly two documents define the technical plan, and every other narrative file is consistent with them:

| File | Role |
|---|---|
| `docs/dataset_audit.md` | Findings from the real CSVs (counts, decoys, UPC reality, brand coverage, sizes, duplicates, examples). |
| `docs/algorithm_recommendation.md` | The recommended algorithm: pipeline stages, retrieval choice, hard rules, scoring, optional layers. |
| `docs/dataset_audit_stats.md` | Compact stat table extracted from `docs/audit_stats.json`. |
| `docs/retrieval_probe_results.md` | Retrieval dry-run rankings extracted from `docs/retrieval_probe_results.json`. |
| `solution.md` | Polished technical narrative for an interviewer. Built from the two canonical docs. |
| `solutioneasyexplained.md` | Plain-Spanish walkthrough of the same plan. |
| `app.md` | Visual/diagrammatic view of the same plan. |

The two `docs/` plus the two reproducible JSON outputs are the source of truth. The narrative `.md` files in the repo root retell the same story for different audiences.

`docs/audits/` and `docs/plans/` contain earlier drafts that have been retired; each now points to the canonical docs.

## Headline facts (from the audit)

- Store A: **233,199 rows**, 158.86 MB.
- Store B: **55,516 rows**, 64.25 MB.
- Naive pair space: **12,946,275,684**.
- Store A has **5 malformed rows** with non-numeric `item_id` (shifted columns, e.g. `" | Pack of 12"`). The pipeline must require `^\d+$` on `item_id` and quarantine the rest before matching.
- `name_clean`, `category`, `department`, `subcategory`, `size_raw`, `item_type`, `is_private_label`, `is_organic` are **100% blank decoys** in their respective files.
- `brand_raw` is blank/null in **106,962 / 233,199 (45.87%)** of A and **5,545 / 55,516 (9.99%)** of B.
- **Shared normalized brands**: 2,441.
- Conservative private-label estimates: **A ≈ 22,363 rows**, **B ≈ 8,498 rows** (B includes `Wegmans` and tag `wegmans brand`).
- A has **0 UPC-like fields**. B has 681 `item_info.ic_item_id` values. A UPC join is **not** viable here.
- A clear category exclusion list removes **at least 48,007 A rows** with no plausible Wegmans counterpart: Toys, Clothing, Home Improvement, Sports & Outdoors, Party & Occasions, Office Supplies, Auto & Tires, Electronics, Arts Crafts & Sewing, Jewelry, Books, Cell Phones.
- Size: A grocery items typically expose size in `name`; B reliably exposes size in `sizing_comp.size_user_friendly` (95.63% nonblank).
- B duplicate-like groups: **2,796 groups** spanning **6,320 rows** — mostly size/pack variants of the same product concept (e.g. Wegmans Organic Tomato Sauce in 8, 15, 29 oz).
- PDF examples confirmed in the data: A `2197626` ↔ B `92544` (Chobani 5.3 oz honey blended yogurt) and A `1929544` ↔ B `105624` (organic tomato sauce, 8 oz).

## Current validated output

The current generated `matches.csv` contains **4,394** data rows, above the assignment minimum of 4,000. It was produced by `scripts/run_pipeline.py` invoked with the calibrated CLI args:

```
python3 scripts/run_pipeline.py \
    --a-csv ... --b-csv ... \
    --min-score 0.75 --min-margin 0.05 --top-k 50
```

It validates with:

- exact header `item_id_A,item_id_B`;
- numeric A/B IDs that exist in the validated source files;
- no duplicate `item_id_A`;
- required PDF regressions: A `2197626` → B `92544`, and A `1929544` → B `105624`.

An earlier recall-heavy run at `--min-score 0.55 --min-margin 0.05` emitted **16,218** rows and was used in Phase 10B as the labeling base; it was retired in favor of the 0.75 floor because the labeled cumulative-precision evidence showed the 0.55–0.75 score band carried most of the false positives. The 4,394-row file is the current shipped output.

`matches_audit.csv` contains one audit row for each valid A item processed, including score, retrieval score, decision, and reason.

## Retrieval probe (already run)

`scripts/retrieval_probe.py` indexed all 55,516 B rows and tested two query modes (`brand_included` and `suppress_private_label`). Highlights:

- **TF-IDF word + char n-grams** ranks both known examples at #1 in both modes.
- **BM25 with brand tokens** placed `Great Value Organic Tomato Sauce 8 oz`'s correct match at rank 5; the top result was `Colgate Fluoride Toothpaste, Great Regular Flavor, 3 Value Pack`. Suppressing private-label brand tokens fixed it.
- The `Great Value Provolone` text probe pulls `Great Lakes Provolone Cheese` to the top under BM25 with brand included; private-label suppression reduces but does not eliminate this trap, so category filters and brand/private-label hard rules are required.

These results back the choice of TF-IDF word + char n-grams as the **primary** candidate generator and motivate private-label brand suppression in the query for private-label items.

## Recommended pipeline (canonical)

The full version lives in `docs/algorithm_recommendation.md`. Summary:

1. **Streaming CSV ingest + validation.** Require `item_id ~ ^\d+$`; quarantine the 5 malformed A rows.
2. **JSON/tag parsing.** Tolerant parsers for `item_info`, `sizing_comp`, and B `tags`.
3. **Normalization.** Brand normalization and conservative inference (`brand_inferred` flag), private-label detection on both sides, category fields from `item_info`, asymmetric size parsing, pack count extracted separately, organic / form / storage / flavor signals.
4. **Scope filtering.** Drop A categories with no plausible B counterpart; treat `Home` selectively (keep `Kitchen & Dining`-style items).
5. **Shared `matchable_group` taxonomy.** Map both stores into groups such as `pantry`, `dairy`, `frozen`, `produce`, `meat`, `seafood`, `bakery`, `prepared_foods`, `beverages`, `personal_care`, `household`, `baby`, `pets`, `health`, `beauty`, `kitchen_home`, `wine_beer_spirits`.
6. **Candidate retrieval.** TF-IDF word `(1, 2)` + char-wb `(3, 5)` n-grams over B, ideally per `matchable_group`. Use **private-label-suppressed** retrieval text for private-label items. Initial `k = 50`, larger `k` for blank-brand or sparse-category A rows.
7. **Hard rules before scoring.** Reject before scoring on incompatible: `matchable_group`, brand / private-label compatibility, comparable size, pack, organic, form, storage, flavor, alcohol/non-alcohol.
8. **Weighted deterministic scoring.** Suggested weights — core name TF-IDF/char similarity 0.35, token overlap (post brand/size strip) 0.15, brand compatibility 0.15, size+pack 0.20, category/group 0.10, attribute compatibility 0.05.
9. **One best B per A.** Require minimum score and a margin over the second candidate. Tie-break B duplicates by exact size/pack, then attribute match, then metadata richness, then stable `item_id`.
10. **Outputs.** `matches.csv` with `item_id_A,item_id_B`, plus a `matches_audit.csv` with score, source, and reason columns.
11. **GPT-5.4 nano (optional, gray-zone arbiter — implemented in Phase 10C, off by default).** Small candidate sets only, after deterministic retrieval and hard rules. Structured JSON output (`same_product_for_customer`, `confidence`, `reason`, `blocking_issue`). Cached. Cannot override hard rules. Opt-in via `--use-llm-arbiter`; the shipped `matches.csv` was produced **without** it. See "Optional GPT-5.4 nano arbiter" below.
12. **Embeddings (optional, deferred).** Considered as a future recall layer for semantic private-label/fresh items; not part of the first deliverable.
13. **Avoided.** All-pairs comparison, LLM-first design, UPC-first design, fuzzy-only matching, requiring brand equality globally, accepting same-name different-size variants without size hard rules, and treating any of the decoy columns as populated.

The assignment floor is **4,000** matches; the current validated run emits **4,394** deterministic matches at the calibrated `--min-score 0.75 --min-margin 0.05` floor. Precision is preferred over recall, and any further recall expansion should be evidence-based — see `eval/phase10b_calibration.md` for the per-bucket and cumulative precision tables that selected the 0.75 floor — and/or optional GPT-5 nano arbitration on gray-zone candidates.

## Output validation contract

Before declaring a run done, the matcher must check that `matches.csv`:

- has the exact header `item_id_A,item_id_B`;
- contains only numeric IDs (`^\d+$`) that exist in the validated A and B ID sets (the 5 quarantined A rows must not appear);
- contains **no duplicate `item_id_A`** values;
- has at least 4,000 rows;
- resolves the two PDF examples to the correct sizes (A `2197626` → B `92544`, A `1929544` → B `105624`, **not** the 15 oz or 29 oz B variants).

## Repository layout

```
BetterBasket-assignment/
├── README.md                              ← this file
├── SUBMISSION_CHECKLIST.md                ← reviewer-facing submission gates
├── solution.md                            ← polished technical narrative
├── solutioneasyexplained.md               ← plain-Spanish walkthrough
├── app.md                                 ← visual/diagrammatic view
├── CLAUDE.md                              ← agent-facing repo instructions
├── requirements.txt
├── pytest.ini
├── betterbasket_matcher/
│   ├── io.py                              ← streaming CSV ingest, JSON/tag parsing
│   ├── normalize.py                       ← brand inference, private-label, size, attributes
│   ├── taxonomy.py                        ← matchable_group + group compatibility
│   ├── scope.py                           ← A-only category exclusions
│   ├── retrieval.py                       ← TF-IDF word + char-wb candidate retrieval
│   ├── rules.py                           ← hard rules (group, brand, size, pack, ...)
│   ├── scoring.py                         ← weighted deterministic score + margin
│   ├── pipeline.py                        ← orchestration; one best B per A
│   ├── output.py                          ← matches.csv + matches_audit.csv writers
│   └── llm_arbiter.py                     ← Phase 10C optional GPT-5.4 nano arbiter (off by default)
├── scripts/
│   ├── audit_data.py                      ← streaming CSV audit (no pandas)
│   ├── retrieval_probe.py                 ← TF-IDF / BM25 dry-run
│   ├── run_pipeline.py                    ← executable matcher entry point
│   └── sample_eval.py                     ← Phase 9 / 10B evaluation sampler
├── tests/
│   ├── fixtures/                          ← Phase 1 oracles (frozen)
│   └── test_*.py                          ← 412 tests covering every module
├── eval/
│   ├── manual_eval_phase10b.csv           ← Phase 10B labeled sample (AI-assisted preliminary)
│   ├── manual_eval_phase10b.md            ← labeling workflow
│   ├── group_breakdown.md                 ← per-group precision (provisional)
│   └── phase10b_calibration.md            ← threshold grid + cumulative precision
├── docs/
│   ├── HANDOFF.md                         ← session-by-session implementation log
│   ├── dataset_audit.md                   ← canonical audit (source of truth)
│   ├── dataset_audit_stats.md             ← compact stats table
│   ├── audit_stats.json                   ← machine-readable audit output
│   ├── algorithm_recommendation.md        ← canonical algorithm plan
│   ├── retrieval_probe_results.md         ← retrieval dry-run summary
│   ├── retrieval_probe_results.json       ← machine-readable probe output
│   ├── audits/2026-05-02-dataset-audit.md             ← retired, points to canonical
│   └── plans/2026-05-02-betterbasket-product-matching.md ← retired, points to canonical
├── matches.csv                            ← deliverable (force-added at submission)
└── matches_audit.csv                      ← debug/audit trace (gitignored, regenerated)
```

`betterbasket_matcher/` is strictly layered: each module reads only from earlier modules. `tests/test_*.py` mirror the layering one-to-one. The 5 PDF + targeted-positive/negative cases in `tests/fixtures/` are frozen Phase 1 oracles.

## Reproducing the audit and probe

```bash
python3 scripts/audit_data.py \
  --a-csv /Users/jirustaroure/Downloads/grocery_store_a_items_final.csv \
  --b-csv /Users/jirustaroure/Downloads/grocery_store_b_items_final.csv \
  --json-out docs/audit_stats.json \
  --markdown-out docs/dataset_audit_stats.md

python3 scripts/retrieval_probe.py \
  --a-csv /Users/jirustaroure/Downloads/grocery_store_a_items_final.csv \
  --b-csv /Users/jirustaroure/Downloads/grocery_store_b_items_final.csv \
  --json-out docs/retrieval_probe_results.json \
  --markdown-out docs/retrieval_probe_results.md
```

`scripts/audit_data.py` is intentionally pandas-free for a small memory footprint. `scripts/retrieval_probe.py` requires `scikit-learn` (TF-IDF) and `rank_bm25` (optional, for the BM25 comparison).

## Reproducing the shipped match output

```bash
pip install -r requirements.txt

python3 scripts/run_pipeline.py \
    --a-csv /Users/jirustaroure/Downloads/grocery_store_a_items_final.csv \
    --b-csv /Users/jirustaroure/Downloads/grocery_store_b_items_final.csv \
    --min-score 0.75 --min-margin 0.05 --top-k 50
```

This is the deterministic command that produced the shipped `matches.csv` (4,394 data rows). It does not import `openai`, never reads any credentials, and never makes a network call. The CLI also writes `matches_audit.csv` next to the matches file with one audit row per valid A item, including `score`, `retrieval_score`, `top1_top2_margin`, `source`, `decision`, `reason`, and (when the arbiter is on) `llm_confidence`.

## Optional GPT-5.4 nano arbiter (Phase 10C)

A precision-preserving rescue layer for gray-zone candidates. **Disabled by default.** The shipped `matches.csv` was produced **without** the arbiter; turning it on can only flip a small number of `below_threshold` audit rows to `accepted` — it cannot change rule rejections and cannot change rows that already cleared the threshold.

How it works:

- After the deterministic loop, `below_threshold` rows whose `score` is in `[0.65, 0.75)` are collected (these already passed `evaluate_hard_rules`; the arbiter cannot override hard rules).
- Survivors are sorted by `score` DESC, capped per `matchable_group` at `max(50, max_calls // n_groups)`, and walked. Cache hits are free; cache misses call the API only while `api_calls_made < max_calls`.
- The model is called as a strict-JSON arbiter via the OpenAI-compatible Azure endpoint using the standard SDK pattern:

  ```python
  from openai import OpenAI
  client = OpenAI(base_url=endpoint, api_key=api_key)
  client.chat.completions.create(model=deployment_name, messages=[...])
  ```

  No `AzureOpenAI`, no `api_version`. The deployment name is `gpt-5.4-nano`.
- A row is rescued only when the model returns `same_product_for_customer=True` AND `confidence >= --llm-min-confidence` (default 0.60). The deterministic decision otherwise stands; API errors, parse errors, NaN/Inf, out-of-range confidence, or budget exhaustion all fail closed.

Opt-in run:

```bash
python3 scripts/run_pipeline.py \
    --a-csv ... --b-csv ... \
    --min-score 0.75 --min-margin 0.05 --top-k 50 \
    --use-llm-arbiter \
    --llm-creds /tmp/openai_artifacts/openai_creds.yaml \
    --llm-max-calls 1000 \
    --llm-min-confidence 0.60
```

Credential resolution order: `--llm-creds PATH` → `$BB_OPENAI_CREDS` → `/tmp/openai_artifacts/openai_creds.yaml` → `/Users/jirustaroure/Downloads/openai_creds.yaml`. The YAML must be a top-level `openai:` mapping with `endpoint`, `api_key`, and `deployment_name`. The `api_key` is held in a `field(repr=False)` slot, never logged, never written to the cache.

The on-disk cache lives at `.cache/llm_arbiter.jsonl` (one record per arbitrated pair, keyed by sha256 over prompt version + deployment + item ids + display names + brands + sizes + groups + failure reason + rounded score). Re-runs with the same gray-zone inputs replay the cache for free. The `.cache/` directory is gitignored.

Guardrails:

- The arbiter cannot override `rejected_by_rule`. It only sees pairs the deterministic pipeline already accepted under hard rules but did not score above 0.75.
- `confidence` is **not** clamped on parse — out-of-range values are rejected and the deterministic decision stands.
- The arbiter prints only a one-line summary (`llm_arbiter: calls=N rescues=R cache_hits=H`) at the end of the run. Prompt bodies, response bodies, endpoints, and `api_key` are never printed by the pipeline.
- Default `pipeline.py` / `run_pipeline.py` thresholds remain `min_score=0.55 / min_margin=0.05` for backwards-compatibility with the test suite; production runs use the calibrated `0.75 / 0.05` floor on the CLI.

A real one-call API smoke (`gpt-5.4-nano`) was run on 2026-05-04 against a synthetic dairy pair: the call succeeded in ~2.4 s, the response parsed valid against the strict schema, and confidence came back at 0.95. **No full LLM run was used to generate the shipped `matches.csv`**.

## Calibration evidence

Two artifacts back the choice of the 0.75 floor:

- `eval/phase10b_calibration.md` — exact decision distribution, accepted-count by `matchable_group`, threshold grid (`min_score ∈ {0.65, 0.70, 0.75, 0.80}` × `min_margin = 0.05`), accepted score-bucket distribution, per-bucket precision, and **cumulative precision at candidate global thresholds**. The threshold grid and PDF rows are exact and do not depend on labels.
- `eval/group_breakdown.md` — per-group precision over 92 labeled rows, sampled across all accepted matchable groups.

**Honest caveat on the labels.** The 92-row Phase 10B sample was labeled in an AI-assisted preliminary pass (Codex), not a human review pass. The precision tables are therefore **provisional** and tagged as such in the artifacts themselves. The headline numbers from that pass:

- Cumulative precision at `score >= 0.75`: **0.95** (19 correct / 0 wrong / 1 partial / 0 unsure on the labeled subset). The `score_0.75_0.80` bucket alone scored 12/0/0/0 → 1.000.
- Cumulative precision at `score >= 0.65`: **0.69** (36/8/8/0). The 0.55–0.75 band carried most of the false positives, which is why the 0.55-floor 16,218-row run was retired in favor of the 0.75-floor 4,394-row run.

A human review pass on `eval/manual_eval_phase10b.csv` is the next calibration step before any of these precision numbers should be quoted as final. The sampler now preserves prior labels and notes across reruns, so a reviewer can edit the CSV in place and rerun `python3 scripts/sample_eval.py --mode phase10b ...` without losing prior annotations.

## Running tests

```bash
python3 -m pytest -q                                 # full suite (412 tests)
python3 -m pytest tests/test_pipeline_fixtures.py -v # fixture pipeline only
python3 -m pytest tests/test_llm_arbiter.py -v       # arbiter unit tests (no network)
```

The arbiter tests inject a `FakeClient` so the `openai` SDK is never hit and no credentials are read; they assert prompt/cache do not leak `api_key`, `Authorization`, `Bearer `, or URLs. The deterministic CLI path likewise never imports `openai` (it is lazy-imported only inside the arbiter).

## Security and credentials

- No credentials are committed: `git ls-files | grep -E "openai_creds\.ya?ml$"` returns empty.
- `.gitignore` excludes `.env*`, `openai_creds.yaml`, `openai_creds.yml`, `*creds*.yaml`, `*credentials*.yaml`, `.cache/`, the source CSVs, and `matches_audit.csv`.
- The credential loader (`betterbasket_matcher.llm_arbiter.load_credentials`) reads only `openai.endpoint`, `openai.api_key`, `openai.deployment_name` and raises `ValueError` naming any missing key without echoing values.
- The arbiter cache writer (`cache_append`) defensively drops any meta key matching `api_key`, `Authorization`, or `Bearer` before serializing.
- The pipeline never prints `api_key`, the endpoint URL, the prompt body, or the full response body. On API exception it logs only the exception class and `item_id_a`.
