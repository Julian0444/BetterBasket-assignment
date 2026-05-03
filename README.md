# BetterBasket — Cross-Retailer Product Matching

Author: Julian Irusta Roure
Assignment: BetterBasket Engineering Technical Assessment
Stores: Walmart (A) ↔ Wegmans (B)

## What this repo is

A planned, deterministic, precision-first product-matching pipeline for the BetterBasket assignment. The narrative documents and the audit/probe scripts are committed; the matcher itself is the next implementation step. No `matches.csv` has been produced yet.

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
11. **GPT-5 nano (optional, gray-zone arbiter).** Small candidate sets only, after deterministic retrieval and hard rules. Structured JSON output (`same_product_for_customer`, `confidence`, `reason`, `blocking_issue`). Cached. Cannot override hard rules.
12. **Embeddings (optional, deferred).** Considered as a future recall layer for semantic private-label/fresh items; not part of the first deliverable.
13. **Avoided.** All-pairs comparison, LLM-first design, UPC-first design, fuzzy-only matching, requiring brand equality globally, accepting same-name different-size variants without size hard rules, and treating any of the decoy columns as populated.

The first deliverable targets **4,000–7,000 high-confidence matches**; precision is preferred over recall, and recall is expanded only after the deterministic floor is solid.

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
├── README.md                           ← this file
├── solution.md                         ← polished technical narrative
├── solutioneasyexplained.md            ← plain-Spanish walkthrough
├── app.md                              ← visual/diagrammatic view
├── docs/
│   ├── dataset_audit.md                ← canonical audit (source of truth)
│   ├── dataset_audit_stats.md          ← compact stats table
│   ├── audit_stats.json                ← machine-readable audit output
│   ├── algorithm_recommendation.md     ← canonical algorithm plan
│   ├── retrieval_probe_results.md      ← retrieval dry-run summary
│   ├── retrieval_probe_results.json    ← machine-readable probe output
│   ├── audits/2026-05-02-dataset-audit.md            ← retired, points to canonical
│   └── plans/2026-05-02-betterbasket-product-matching.md ← retired, points to canonical
├── scripts/
│   ├── audit_data.py                   ← streaming CSV audit (no pandas)
│   └── retrieval_probe.py              ← TF-IDF / BM25 dry-run
├── tests/
│   └── fixtures/                       ← reserved for matcher tests
└── [BetterBasket] Engineering Technical Assessment.pdf
```

The matcher package itself (`betterbasket_matcher/`, `scripts/run_pipeline.py`, `tests/test_*.py`) is the next planned implementation step.

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

Credentials such as `openai_creds.yaml` are not read or printed by anything in this repo today and will only be used by the optional GPT-5 nano arbiter when that stage is implemented.
