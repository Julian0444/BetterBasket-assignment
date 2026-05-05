# Solution — BetterBasket Engineering Technical Assessment

**Author**: Julian Irusta Roure
**Company**: BetterBasket
**Position**: Summer 2026 Internship — Engineering
**Task**: Cross-retailer product matching (Walmart <-> Wegmans)

---

> This document is the polished narrative version of the repo's two sources of truth: `docs/dataset_audit.md` (what the CSVs actually say) and `docs/algorithm_recommendation.md` (what algorithm we recommend). If I find any discrepancy between this document and those two, **`docs/` wins**.

## TL;DR

- **The problem**: given a product from Walmart's catalog (A, ~233k items), find the "single closest match" in Wegmans's catalog (B, ~55k items). Deliver at least 4,000 matches.
- **What the audit found**: the pre-processed columns (`name_clean`, `category`, `department`, `subcategory`, `size_raw`, `is_private_label`, `item_type`, `is_organic`) are **100% empty** in their respective files. UPC is not viable: A has no UPC-like fields and B only has 681 `ic_item_id` values. A has **5 malformed rows** with non-numeric `item_id` (shifted columns, e.g. `" | Pack of 12"`) that must be quarantined before any matching. `brand_raw` is blank in 45.87% of A (63.89% in Food). The problem is **100% entity resolution over textual attributes**.
- **The shipped solution (precision-first, deterministic, already implemented)**: streaming ingest + validation -> tolerant JSON/tags parsing -> normalization (brand, private-label, taxonomy, size, pack, organic/form/storage/flavor) -> scope filtering -> blocking by shared `matchable_group` -> **retrieval with TF-IDF word + char n-grams** over B -> hard rules -> deterministic weighted score -> a single best B per A -> `matches.csv` + `matches_audit.csv`. **GPT-5.4 nano is implemented as an optional arbiter** (off by default) only for gray zones over already-filtered candidate sets; the shipped `matches.csv` was generated **without** the arbiter.
- **The concrete deliverable**: `matches.csv` with **4,394 accepted rows** produced by `python3 scripts/run_pipeline.py --min-score 0.75 --min-margin 0.05 --top-k 50`. Validation OK (exact header `item_id_A,item_id_B`, 0 duplicate `item_id_A`, both PDF rows correct: `2197626 -> 92544` and `1929544 -> 105624`). 412 tests passing locally.
- **Why TF-IDF word + char and not BM25/FAISS/RRF as the primary engine**: the repo's dry-run (`scripts/retrieval_probe.py`) demonstrated it on the 55,516 B items: TF-IDF word+char ranks both PDF examples at #1, while BM25 with brand included sinks `Great Value Organic Tomato Sauce 8 oz` to rank 5 (top-1: `Colgate Fluoride Toothpaste, Great Regular Flavor, 3 Value Pack`). Semantic embeddings were ruled out for this deliverable (weaker on numeric sizes/packs/flavors).
- **Real cost and time**: the deterministic core runs on local CPU in seconds and costs nothing in API. The LLM arbiter was never executed full over the corpus to produce the deliverable; only a 1-call smoke test (latency 2.40s, confidence 0.95) confirmed the wiring is OK.

---

## Table of contents

1. [Understanding the problem](#1-understanding-the-problem)
2. [Exhaustive dataset audit](#2-exhaustive-dataset-audit)
3. [Architectural decisions and trade-offs](#3-architectural-decisions-and-trade-offs)
4. [The pipeline in detail](#4-the-pipeline-in-detail)
5. [Calibration and evaluation](#5-calibration-and-evaluation)
6. [Volumes, costs, timing](#6-volumes-costs-timing-real-numbers)
7. [Explicit assumptions](#7-explicit-assumptions)
8. [Execution timeline](#8-effective-implementation-timeline)
9. [Risks and mitigations](#9-risks-and-mitigations)
10. [Deliverables](#10-deliverables-whats-in-the-repo)
11. [Appendix: discarded alternatives](#11-appendix-discarded-alternatives)
12. [Technical glossary](#12-technical-glossary)

---

## 1. Understanding the problem

### 1.1 The brief in one sentence

> For each product in A, find the **closest** product in B according to the criterion: *"a customer would consider both to be essentially the same product"*. Deliver `matches.csv` with at least 4,000 `(item_id_A, item_id_B)` rows.

### 1.2 The three types of match (and why they matter differently)

#### Type 1: Exact match with UPC on both sides

The **UPC** is the 12-digit code the cash register scans. When both sides expose it, it is the gold standard for matching.

**Why it does not apply in this dataset**: A has **0** UPC-like fields; B has 681 `item_info.ic_item_id` (1.2%). Without a counterpart in A, a UPC join is **infeasible**.

#### Type 2: Exact match without UPC (via attributes)

Same product, same brand, same size, no shared UPC. Must match by `brand` + `core_name` + `size`.

**PDF example (verified against real data)**:
- A `2197626`: "Chobani Whole Milk Greek Yogurt Honey Blended 5.3 oz Cup"
- B `92544`: "Chobani Greek Honey Blended Yogurt", `sizing_comp.size_user_friendly = "5.3 ounce"`

#### Type 3: Non-exact match (private label cross-store)

Different products but **functionally equivalent**. Different brands (each chain's private label), but a customer would consider them substitutes.

**PDF example (verified)**:
- A `1929544`: "Great Value Organic Tomato Sauce, 8 oz" (Walmart private label)
- B `105624`: "Wegmans Organic Tomato Sauce", size 8 ounce (Wegmans private label)

Beware the noise: B also has `103620` (15 oz) and `1086860` (29 oz) with the same name — picking the right one demands hard size rules.

#### Summary table

| Type | Detection | Confidence | Cost |
|---|---|---|---|
| 1 (UPC) | Direct JOIN | 99% | O(n) — but **does not apply here** |
| 2 (attributes) | exact brand + exact size + high name similarity | 90-95% | O(retrieval) |
| 3 (private label cross) | PL compatibility rules + deterministic score + optional LLM | 70-85% | O(retrieval + optional LLM) |

### 1.3 The scale that changes everything

| Quantity | Value |
|---|---|
| Items in A | 233,199 |
| Items in B | 55,516 |
| Cartesian product (A x B) | **12,946,275,684** pairs |

This rules out any "throw all the pairs at the LLM and let it decide" idea. The pipeline's engineering job is to reduce 12.9B candidates to a manageable funnel; the real numbers from the final run were: `valid_a=233,194` (5 quarantined), `in_scope_a=181,289`, `rejected_by_rule=24,759`, `below_threshold=118,140`, `no_candidates=85,901`, **`accepted=4,394`** — without losing the good ones (both PDF rows land at rank #1).

### 1.4 The brief's trap: GPT-5 nano

The assessment PDF says:

> *GPT-5 nano deployment credentials will be provided by BetterBasket to be used in the solution for the above task.*

The junior interpretation is "let the model decide". The solid interpretation is: **the LLM as a selective arbiter** where it adds judgment that rules cannot cover (private label cross-store, small score margins, A with blank or inferred brand), and justify why it is not used in the rest. The shipped pipeline implements exactly that arbiter in `betterbasket_matcher/llm_arbiter.py` (GPT-5.4 nano via Azure-compatible endpoint) — and leaves it **off by default**. The shipped `matches.csv` was generated **without** any LLM calls. The 1-call smoke test validated endpoint + parsing + strict-JSON schema; the full budget is available for the reviewer if they want to enable the arbiter with `--use-llm-arbiter`.

---

## 2. Exhaustive dataset audit

> Reproducible source: `scripts/audit_data.py` -> `docs/audit_stats.json` -> `docs/dataset_audit.md` and `docs/dataset_audit_stats.md`. Everything below is backed by those artifacts.

### 2.1 Quick inventory

| Metric | A (Walmart) | B (Wegmans) |
|---|---|---|
| Rows | 233,199 | 55,516 |
| On-disk size | 158.86 MB | 64.25 MB |
| Columns | 19 | 18 |

### 2.2 Schemas

#### A (Walmart) — 19 columns

```
item_id, name, brand_raw, name_clean, description, category, department, url,
item_type, item_info, tags, subcategory, is_private_label, sizing_comp,
size_raw, datapoint_id, raw_data_id, created_at_utc, updated_at_utc
```

#### B (Wegmans) — 18 columns

```
item_id, name, brand_raw, name_clean, description, category, department, url,
is_organic, item_info, tags, subcategory, sizing_comp, size_raw,
datapoint_id, raw_data_id, created_at_utc, updated_at_utc
```

**Structural differences**: A has `item_type` and `is_private_label`; B has `is_organic`. (All three are 100% blank, so they make no operational difference.)

### 2.3 Critical findings

#### Finding 1: malformed A rows with non-numeric `item_id`

A has **5 rows with non-numeric `item_id`** and shifted columns. Repeated `item_id` examples: `" | Pack of 6"`, `" | Pack of 8"`, `" | Pack of 12"`, `"Acrylic Tortoise Thick Gripjaw Non Holdmetal Small Hair"`. They coincide with the 5 rows where `sizing_comp` parses to a non-dict JSON value.

**Implication**: Stage 1 validates `item_id` against `^\d+$` and **quarantines** invalid rows before normalize, retrieve, score, or output. The final stage validates that every `item_id` in the output CSV exists in the original numeric sets and that there are no duplicate `item_id_A` values.

#### Finding 2: pre-processed columns are decoys

| Column | A blank/null | B blank/null |
|---|---|---|
| `name_clean` | 100% | 100% |
| `category`, `department`, `subcategory` | 100% | 100% |
| `size_raw` | 100% | 100% |
| `item_type` | 100% | n/a |
| `is_private_label` | 100% | n/a |
| `is_organic` | n/a | 100% |
| `tags` | 100% (except the 5 malformed) | 58.96% blank |
| `description` | 91.85% blank | 6.44% blank |

The only reliable columns for matching are `item_id`, `name`, `brand_raw`, `url`, `item_info`, `sizing_comp`, and B `tags`. `description` only helps after HTML stripping, and A barely has it.

**Implication**: normalization (Stage 1) is where the real work lives. We have to reconstruct everything from `name`, `brand_raw`, `item_info`, `sizing_comp`, and B `tags`.

#### Finding 3: UPC is not viable in this dataset

- A: 0 UPC-like fields, 0 plausible values.
- B: 681 `item_info.ic_item_id` values (1.23%), all in the 8-14 digit range.

Without a counterpart in A, there is no UPC join. We treat the problem as **pure entity resolution**.

#### Finding 4: brand crisis in A

| Population | Blank/null `brand_raw` |
|---|---|
| A total | 106,962 / 233,199 (45.87%) |
| A Food (cat0=Food) | 46,947 / 73,478 (63.89%) |
| B total | 5,545 / 55,516 (9.99%) |

| Metric | Value |
|---|---|
| Unique normalized brands in A (nonblank) | 18,562 |
| Unique normalized brands in B (nonblank) | 5,632 |
| **Shared normalized brands** | **2,441** |
| A branded rows whose brand exists in B | 34,584 / 126,237 (27.40%) |
| B branded rows whose brand exists in A | 28,752 / 49,971 (57.54%) |

**Implication**:
- Brand is a strong signal when both sides are national with populated brand.
- Brand **cannot be globally required**: A has too many blanks.
- A needs conservative brand inference from the name (with `brand_inferred=True`).
- Brand mismatch between national and private-label is expected and is handled with rules, not by rejecting everything.

#### Finding 5: private-label must be detected explicitly

Conservative estimate from brand/name/tag rules:

| Signal | A | B |
|---|---|---|
| Estimated private-label rows | 22,363 | 8,498 |

A is recognized via whitelist: `great value` (5,692), `mainstays` (3,487), `freshness guaranteed` (1,367), `equate` (815), `marketside` (255), `bettergoods` (97), plus `wonder nation`, `sam s choice`, `members mark`, etc.

B is recognized via `brand_raw == Wegmans` (8,050) or tag `wegmans brand` (6,820). Other useful tags in B: `organic`, `family pack`, `gluten free`, `vegan`, `food you feel good about`.

#### Finding 6: size location is asymmetric

| Metric | A | B |
|---|---|---|
| Size parsed from `name` | 138,739 (59.49%) | 5,204 (9.37%) |
| Food size parsed from `name` (A) | 66,174 / 73,478 (90.06%) | n/a |
| `sizing_comp.size_user_friendly` nonblank | 39,765 (17.05%) | 53,090 (95.63%) |
| Size parsed from `sizing_comp.size_user_friendly` | 24,621 (10.56%) | 53,024 (95.51%) |
| Conflicts when both sources parse | 4,412 | 4,112 |

**Implication**:
- A grocery: parse size from `name` with regex; fall back to `sizing_comp`.
- B: use `sizing_comp.size_user_friendly` as the primary source; fall back to `name`.
- Pack count is parsed **separately** from per-unit size (e.g. `(12 pack) ... 7 oz` -> `pack_count=12`, `size=(7, oz)`).
- Dimension strings (`5 x 7`, `12 x 24`) are frames/home, not grocery, and are not used as matching size.

#### Finding 7: B has structural duplicates by size/pack

Grouping by `(brand_norm, name_without_size)`:

- **2,796 groups** with more than one row.
- **6,320 rows** inside those groups.

Most of these are not true duplicates: they are **the same product concept in different sizes/packs**.

Audit examples:
- `Wegmans Organic Tomato Sauce`: 8 oz, 15 oz, 29 oz.
- `Wegmans Tomato Sauce`: 8 oz, 15 oz, 29 oz.
- `FIJI Natural Artesian Water`: 6 x 16.9 fl. oz., 1.5 L, 1 L, 24 x 16.9 fl. oz., 700 ml, etc.
- `Hershey's Candy Assortment`: ~15 rows with sizes from ~13 oz to 64+ oz.
- `Mountain Dew Citrus Soda`: 2 L, 6 x 7.5 fl. oz., 12 x 12 fl. oz., 24 x 12 fl. oz., and other packs.

**Implication**: size must be a **hard rule** before scoring. Picking between `Wegmans Organic Tomato Sauce` 8 / 15 / 29 oz on name similarity alone is a guaranteed error.

#### Finding 8: category taxonomies are incompatible

`category`, `department`, `subcategory` are empty on both sides. Category info has to come from `item_info` (`category_0..3`).

Top A `category_0`: Food (73,478), Health and Medicine (20,501), Personal Care (15,715), Household Essentials (14,802), Toys (14,138), Pets (13,158), Baby (12,805), Clothing (11,990), Home (9,497), Beauty (5,243).

Top B `category_0`: More Departments (19,586), Grocery (18,438), Wine/Beer/Spirits (5,040), Frozen (3,679), Dairy (2,951), Produce & Floral (1,739), Bakery (1,248), Meat (959), Prepared Foods (669), Cheese (645), Seafood (562).

**Clear A exclusions** (no counterpart in B), totaling >= **48,007 rows**:

`Toys`, `Clothing`, `Home Improvement`, `Sports & Outdoors`, `Party & Occasions`, `Office Supplies`, `Auto & Tires`, `Electronics`, `Arts Crafts & Sewing`, `Jewelry`, `Books`, `Cell Phones`.

`Home` is treated selectively: `Home > Kitchen & Dining` may map to B `More Departments > Kitchen and Home`, but `Home > Decor`, frames, bedding, furniture, and wall art are excluded.

**Implication**: build a shared intermediate taxonomy, `matchable_group`, instead of comparing raw category names.

#### Finding 9: pack noise and HTML

| Metric | A | B |
|---|---|---|
| Pack-prefix in `name` | 22,882 | 0 |
| Multipack patterns | 370 | 699 |
| Dimension-like strings | 3,071 | 700 |
| Descriptions with HTML | 7,410 | 6 |

**Implication**: clean `(N pack)`/`Pack of N` from `name` before tokenizing for retrieval; store `pack_count` separately; strip HTML from description.

#### Finding 10: B tags carry valuable signal

While A has empty tags (except the 5 malformed rows), B ships structured tags (tolerant parser: JSON -> Postgres array -> split). Useful tags: `wegmans brand`, `organic`, `gluten free`, `family pack`, `vegan`, `food you feel good about`.

**Implication**: parse B tags and use them to detect `is_private_label`, `is_organic`, `is_family_pack`, dietary attributes.

### 2.4 Verification of the PDF examples in real data

| Example | Store | Item ID | Name | Size |
|---|---|---|---|---|
| Chobani honey blended yogurt | A | `2197626` | Chobani Whole Milk Greek Yogurt Honey Blended 5.3 oz Cup | 5.3 oz |
| Chobani honey blended yogurt | B | `92544` | Chobani Greek Honey Blended Yogurt | 5.3 ounce |
| Private-label organic tomato sauce | A | `1929544` | Great Value Organic Tomato Sauce, 8 oz | 8 oz |
| Private-label organic tomato sauce | B | `105624` | Wegmans Organic Tomato Sauce | 8 ounce |

Nearby traps in B: `103620` (15 oz), `1086860` (29 oz). Nearby traps in A: `1929545` (15 oz), `2116415` (`(4 pack) ... 15 oz`), `1949064` (`(8 pack) ... 8 oz`). The PDF examples are real and prove why private-label compatibility and size/pack rules are mandatory.

### 2.5 Retrieval probe: why TF-IDF word + char and not BM25

Reproducible source: `scripts/retrieval_probe.py` -> `docs/retrieval_probe_results.json` and `docs/retrieval_probe_results.md`.

The probe indexed the 55,516 B rows and compared two query modes:

- `brand_included`: the query includes brand tokens as-is.
- `suppress_private_label`: for A's private-label items, the store-brand tokens (`great value`, `marketside`, `wegmans`, etc.) are removed from the query.

**TF-IDF results (word `(1,2)` + char-wb `(3,5)`)**:

| Probe | Mode | Expected B | Rank | Top-1 |
|---|---|---|---:|---|
| Chobani 5.3 oz honey yogurt | brand_included | `92544` | 1 | `92544` (correct) |
| Chobani 5.3 oz honey yogurt | suppress_private_label | `92544` | 1 | `92544` |
| Great Value Organic Tomato Sauce 8 oz | brand_included | `105624` | 1 | `105624` (correct) |
| Great Value Organic Tomato Sauce 8 oz | suppress_private_label | `105624` | 1 | `105624` |

**BM25 results (same corpus, same queries)**:

| Probe | Mode | Expected B | Rank | Failure mode |
|---|---|---|---:|---|
| Chobani 5.3 oz honey yogurt | brand_included | `92544` | 1 | OK |
| Great Value Organic Tomato Sauce 8 oz | brand_included | `105624` | **5** | Top-1: `Colgate Fluoride Toothpaste, Great Regular Flavor, 3 Value Pack` |
| Great Value Organic Tomato Sauce 8 oz | suppress_private_label | `105624` | 1 | Suppressing the private brand fixes the case |
| Great Value Provolone (text probe) | brand_included | n/a | n/a | Top-1: `Great Lakes Provolone Cheese` |
| Great Value Provolone (text probe) | suppress_private_label | n/a | n/a | Top-1 returns to Wegmans/private-label cheese; `Great Lakes` drops |

Noisy `Great Lakes` hit counts in top-50:

| Mode | Probe | `Great Lakes` hits |
|---|---|---:|
| brand_included | great_value_tomato_text | 0 |
| brand_included | great_value_provolone_text | 7 |
| brand_included | great_value_water_text | 1 |
| suppress_private_label | great_value_tomato_text | 0 |
| suppress_private_label | great_value_provolone_text | 2 |
| suppress_private_label | great_value_water_text | 0 |

**Probe conclusions**:

1. **TF-IDF word + char is the retrieval engine**. It handles both national brand (Chobani) and private-label cross-store (Great Value <-> Wegmans) well, even without suppression.
2. **BM25 with brand included has hard lexical traps** in private label (`Great Value` -> `Great Lakes`, `Great Regular Flavor`). Useful as a secondary diagnostic, not as the primary retrieval engine.
3. **Suppressing private-brand tokens on the A query reduces the noise** and is necessary for cases like `Great Value Provolone`. On the B side, the corpus is built the same way: the same suppression rules applied to B private-label items.
4. **Even with perfect TF-IDF, size is decisive**: the top-3 for `Great Value Organic Tomato Sauce 8 oz` are the 8, 15, and 29 oz Wegmans Organic Tomato Sauce, in that order. Without hard size rules, someone ends up with the wrong jar.
5. **Embeddings/FAISS were ruled out for this deliverable** (better recall on paraphrase for fresh and semantic private-label items, but weaker on sizes/packs/flavors). The deterministic core already ranks the examples at #1 and ships 4,394 matches over the 4,000 floor.

---

## 3. Architectural decisions and trade-offs

### 3.1 Deterministic precision-first, not LLM-first

**LLM-first (rejected as engine)**:

- 12,946,275,684 pairs makes it infeasible to compare them all. Even after reducing, the LLM **still needs** retrieval, normalization, and hard rules first to avoid getting obvious things wrong (8 oz vs 15 oz tomato sauce).
- The LLM contributes when there is a gray zone; it does not contribute when the answer is deterministic.

**Rules-only (rejected)**:

- Cannot handle paraphrase ("Whole Milk" vs "Vit D Milk", "Greek" vs "Strained").
- Fails precisely on non-exact matches (Type 3) which is where the problem becomes interesting.

**Deterministic hybrid (chosen)**:

- TF-IDF word + char for recall (proven in `retrieval_probe.py`).
- Hard rules for precision (size/category/brand/PL/pack/organic/form/storage/flavor).
- Deterministic weighted score for final ranking.
- LLM as **optional** arbiter for the gray zone, on already-filtered candidate sets.

### 3.2 Why TF-IDF word + char as primary retrieval (and BM25 as diagnostic)

The repo retrieval probe made it clear: TF-IDF ranks both PDF examples at #1 with or without suppression, while BM25 with brand included drops to rank 5 on private label because "Great" / "Value" inflate candidates like `Great Regular Flavor` or `Great Lakes`. BM25 stays useful for validation (probing the candidate space from another algorithm), but the main engine is TF-IDF word `(1,2)` + char-wb `(3,5)`.

Semantic embeddings (e.g. sentence-transformers) are **not included in the shipped pipeline**. They are weaker at differentiating sizes, packs, flavors, and numeric variants — which is where the B duplicates hurt us most; and the deterministic core already delivers 4,394 matches with cumulative precision 0.95 on the labeled sample at `>=0.75`.

### 3.3 Why precision-first (target 4,000-7,000, not 12,000)

| Argument | Detail |
|---|---|
| PDF wording | "single closest match" — does not say "every possible match". |
| Floor vs target | 4,000 is a floor, not a target. Defending 5-7k good ones > defending 12k with garbage. |
| Cost of a false match | In real pricing, a false match indexes against an incomparable product. Better no match than a false match. |
| Reversibility | The threshold can be **lowered** later if we fall short (costs minutes). Raising it later means re-reviewing contaminated matches (costs hours). |

### 3.4 Why private-label brand suppression is necessary

Tokens `Great Value`, `Marketside`, `Freshness Guaranteed`, `Wegmans` are lexical traps for similarity-based retrieval. When the A item is private-label, the query is rewritten by removing those tokens and relying on core name + size + `matchable_group` + organic. The probe confirmed it: with BM25 brand_included, `Great Value Organic Tomato Sauce 8 oz` falls to rank 5; with suppression, it rises to rank 1.

### 3.5 Why LLM only as arbiter (and off by default)

The **dominant cost** of the LLM in this pipeline is not money (it is cheap), it is **latency** and **variability**. The shipped pipeline leaves it off by default and uses the deterministic core to produce the 4,394 rows. When activated with `--use-llm-arbiter`, it only enters in a fixed score band `[0.65, 0.75)` over rows that already passed the hard rules.

Hard rules on size/category/brand/PL/pack/etc. are **not overridden** by the LLM's response by construction (the arbiter never sees `rejected_by_rule` rows). And the arbiter cannot change rows already `accepted` either (they are not offered). The LLM does not decide that an 8 oz matches a 15 oz "because they look similar".

### 3.6 Decisions we explicitly discarded

| Idea | Why we discarded it |
|---|---|
| **All-pairs comparison** | 12.9B pairs: impossible. |
| **LLM-first / LLM as engine** | Expensive, slow, non-deterministic, adds no value where rules give an exact answer. |
| **UPC-first** | A has no UPC. Plan not applicable. |
| **Fuzzy / Levenshtein only** | Fails on non-exact matches and on size duplicates; cannot handle PL cross. |
| **BM25 + FAISS + RRF as primary engine** | The probe showed that BM25 brand_included has critical lexical traps in private label. TF-IDF word+char already ranks both PDF examples at #1 without needing fusion. |
| **Embeddings (FAISS) as the first layer** | Considered and discarded. Weaker on numeric sizes / packs / flavors; not part of the shipped pipeline. |
| **Requiring brand global-equality** | A has 45.87% brand blank — this would exclude over half the catalog. |
| **Accepting same-name different-size without rules** | B has 2,796 duplicate-like groups; without hard size rules, we are guaranteed to pick the wrong jar. |
| **Trusting pre-cleaned columns** | `name_clean`, `category`, `department`, `subcategory`, `size_raw`, `is_private_label`, `item_type`, `is_organic` are 100% blank. |

---

## 4. The pipeline in detail

### Global diagram

```
+---- A: 233,199 items (Walmart) ----+         +---- B: 55,516 items (Wegmans) ----+
|                                    |         |                                    |
+------+-----------------------------+         +------+-----------------------------+
       |                                              |
       v                                              v
+----------------------------------------------------------------------------------+
| Stage 1 — INGEST + VALIDATE     (item_id ~ ^\d+$, quarantine 5 malformed rows)   |
| Stage 2 — PARSE JSON / TAGS     (item_info, sizing_comp, B tags tolerant)        |
| Stage 3 — NORMALIZE             (brand, PL, taxonomy, size, pack, organic, ...)  |
| Stage 4 — SCOPE FILTER          (exclude Toys/Clothing/...; reduce A)            |
| Stage 5 — TAXONOMY BLOCKING     (shared matchable_group)                         |
| Stage 6 — CANDIDATE RETRIEVAL   (TF-IDF word+char over B, PL suppression)        |
| Stage 7 — HARD RULES            (group, brand/PL, size, pack, organic, ...)      |
| Stage 8 — DETERMINISTIC SCORE   (weighted, top-1 + margin)                       |
| Stage 9 — TIE-BREAK B DUPLICATES                                                 |
| Stage 10 — OUTPUT + AUDIT       (matches.csv + matches_audit.csv)                |
| Stage 11 — OPTIONAL LLM ARBITER (gray zone, only on small candidate set)         |
+----------------------------------------------------------------------------------+
       |                                              |
       v                                              v
   matches.csv                              matches_audit.csv
```

### Stage 1 — Ingest and validate

Implementation: `betterbasket_matcher/io.py::read_products`.

- Streaming `csv.DictReader` over A and B. No pandas (small footprint).
- Validate `item_id ~ ^\d+$`. The **5 A rows with non-numeric `item_id`** are returned in the `quarantined_rows` list and **do not enter** the rest of the pipeline (`valid_a=233,194`, `quarantined_a=5`).
- Validate `name` is non-blank.
- Build the numeric valid-ID sets `valid_ids_A`, `valid_ids_B`. The final validator (Stage 10, `validate_matches_csv`) uses them to confirm every output `item_id` exists.

### Stage 2 — Parse JSON / tags

Implementation: `betterbasket_matcher/io.py::parse_json_dict` and `parse_tags`.

- `item_info` (A and B) and `sizing_comp` (A and B): tolerant parser (`json.loads` -> `{}` if it neither parses nor is a dict). Never raises.
- B `tags`: tolerant parser that tries JSON list, then Postgres array `{a,b,c}`, then comma split. Fallback: empty list.
- A `tags`: 100% blank except the 5 malformed — not used.
- A `description` and B `description`: not used in the production matcher; the `core_name` from Stage 3 is enough for lexical similarity.

### Stage 3 — Normalize

Implementation: `betterbasket_matcher/normalize.py::normalize_product`. Each valid row is projected into a `NormalizedProduct` with: `item_id`, `source`, `brand_norm`, `is_private_label`, `brand_inferred`, `size: SizeInfo(unit, unit_size, pack_count, total_size)`, `is_organic`, `storage_type`, `form`, `flavor`, `category_0..2`, `core_name`, `retrieval_text`.

Rules:

- **Brand**:
  - A: `brand_raw` if populated; otherwise, `infer_brand_from_name` with word-boundary prefix against `_PRIVATE_LABEL_A`. Sets `brand_inferred=True` when inferred. National brand inference is reserved for a future improvement (the API already accepts `known_brands` but is currently called without it).
  - B: `brand_raw` normalized (lowercase, collapse whitespace).
- **Private label**:
  - A: canonical prefix-match against `{great value, marketside, freshness guaranteed, equate, mainstays, bettergoods, sam s choice, parent s choice, ol roy, special kitty, clear american, ...}`. It is prefix-match, not exact, so "equate extra strength" counts and "equator" does not.
  - B: `brand_norm == 'wegmans'` OR tag `wegmans brand` / `wegmans_brand`.
- **Categories**: parse `item_info.category_{0..2}` on both sides; ignore the blank raw columns.
- **Size** (asymmetric):
  - A: regex over `name` first (with the `(N Pack)` prefix extracted), fallback to `sizing_comp.size_user_friendly`.
  - B: `sizing_comp.size_user_friendly` with support for the `12 x 5.3 ounce` format.
  - Produce `SizeInfo(unit, unit_size, pack_count, total_size)` where `unit ∈ {oz, lb, g, kg, fl oz, ml, l, gal, ct}`.
  - The retrieval-text renderer drops `.0` on integer-valued floats (`"8oz"`, not `"8.0oz"`).
- **Organic / form / storage / flavor**: priority read from `item_info`, fallback to keyword extraction over `name` (form: `whole_bean | powder | liquid | sliced | shredded | ground`; flavor: `vanilla | chocolate`; storage: `frozen | refrigerated`). On B, storage can also come from `tags`. `None` means not detected, and hard rules treat it as "does not apply".

### Stage 4 — Scope filter

Implementation: `betterbasket_matcher/scope.py::is_a_in_scope`.

Exclude A where `category_0` is in the audit's exclusion list (reason `excluded_category`):

`Toys`, `Clothing`, `Home Improvement`, `Sports & Outdoors`, `Party & Occasions`, `Office Supplies`, `Auto & Tires`, `Electronics`, `Arts Crafts & Sewing`, `Jewelry`, `Books`, `Cell Phones`.

`Home` is kept **selectively**: keep `Kitchen & Dining`; drop `Decor`, `Picture Frames`, `Bedding`, `Furniture`, `Rugs`, `Wall Art` (reason `excluded_home_decor`).

B is not filtered: `is_a_in_scope` returns `(True, "not_store_a")` when `product.source != "A"`, so the pipeline call site needs no special logic.

Real result over the full corpus: `in_scope_a=181,289` (out of 233,194 valid_a), 51,905 out of scope with stable reasons surfaced in `matches_audit.csv`.

### Stage 5 — Taxonomy blocking (`matchable_group`)

Implementation: `betterbasket_matcher/taxonomy.py::assign_matchable_group` and `groups_compatible`.

Shared intermediate taxonomy with these groups:

`pantry`, `snacks`, `candy`, `beverages`, `dairy`, `cheese`, `frozen`, `produce`, `meat`, `seafood`, `bakery`, `prepared_foods`, `baby`, `pets`, `household`, `personal_care`, `health`, `beauty`, `kitchen_home`, `wine_beer_spirits`.

Mapping done by rules over `(category_0..2)` on each side. `groups_compatible` is **deliberately conservative**: exact equality, plus a symmetric `dairy <-> cheese` only (B carves cheese out as a separate `category_0` from A's dairy). Any other pair returns False; `None`/`""` group on either side also returns False, so unclassifiable products do not enter retrieval. Widening this compatibility graph requires evidence from the labeled sample, never a speculative edit.

### Stage 6 — Candidate retrieval

Implementation: `betterbasket_matcher/retrieval.py::TfidfRetriever`.

**Primary engine: TF-IDF word + char n-grams over B.**

- `TfidfVectorizer` 1: word, `ngram_range=(1, 2)`, `sublinear_tf=True`, L2-norm.
- `TfidfVectorizer` 2: char_wb, `ngram_range=(3, 5)`, `sublinear_tf=True`, L2-norm.
- Horizontal concat (`scipy.sparse.hstack`) for a combined representation (the magnitude becomes sqrt(2); dot-product ranking is unaffected).
- Global index over B + post-filter by compatible `matchable_group`. In `fit`, per-group indices and submatrices (`_compat_indices_by_group`, `_compat_matrix_by_group`) are precomputed so `query` only evaluates compatible B rows.

**Retrieval text** (asymmetric by brand type, built in Stage 3):

- **National brand**: include strong brand.
  E.g. (Chobani A `2197626`): `"chobani greek honey blended yogurt 5.3oz dairy and eggs yogurt"`
- **Private label**: suppression of store-brand tokens (`great value`, `marketside`, `freshness guaranteed`, `wegmans`, etc.).
  E.g. (Great Value A `1929544`): `"organic tomato sauce 8oz food pantry organic"`

That asymmetry is backed by the probe (sec. 2.5). The B corpus is built with the same suppression rule for B private-label items, so A and B share vocabulary in that mode.

**Top-k**: the production CLI uses `k = 50` per A item. `query` sorts by score DESC and tie-breaks by `item_id_b` ASC for stable, reproducible results.

**BM25** and **embeddings/FAISS** stayed as discarded alternatives for this deliverable: the probe already ranked both PDF rows at #1 with TF-IDF word+char, and semantic embeddings are weaker than TF-IDF on numeric sizes/packs/flavors, which is where the B duplicates hurt the most.

### Stage 7 — Hard rules (before scoring)

Implementation: `betterbasket_matcher/rules.py::evaluate_hard_rules`. The chain short-circuits on the first failure (the first rule that returns `passed=False` provides the `reason`); when an optional attribute is `None` on either side, that rule does not apply (no rejection). ID validation does not live here: it is already covered by `read_products` and `validate_matches_csv`.

Real evaluation order:

1. **`alcohol_mismatch`**: runs before `group_mismatch` so the reason is the most specific. `wine_beer_spirits` counts as alcohol; a "non-alcoholic phrases" override (`ginger beer`, `wine vinegar`, `rum cake`, `beer cheese`) prevents false rejects from keywords.
2. **`group_mismatch`**: `groups_compatible(group_a, group_b)` (equality or `dairy <-> cheese`).
3. **`national_vs_private_label`**: when exactly one side is PL and the other is a trusted national brand (not inferred and not PL), reject.
4. **`brand_mismatch`**: two trusted national brands with different `brand_norm`.
5. **`storage_mismatch`**: `frozen` vs `refrigerated` when both are detected.
6. **`form_mismatch`**: `powder` vs `liquid`, `whole_bean` vs `ground`, `sliced` vs `shredded`, etc.
7. **`pet_type_mismatch`**: `cat` vs `dog` within the `pets` group.
8. **`flavor_mismatch`**: `vanilla` vs `chocolate` when both are detected.
9. **`size_mismatch`**: same family (`weight | volume | count`), drift > 8% of the base value.
10. **`pack_mismatch`**: both sides multipack with different `pack_count` and total-size drift > 1%. The single-vs-multipack case is already covered by `size_mismatch` with its 8% tolerance.

Real result over the full corpus: `rejected_by_rule=24,759` A rows.

### Stage 8 — Deterministic score

Implementation: `betterbasket_matcher/scoring.py::score_pair` and `select_best`. Fixed weights (not recalibrated per run; any change moves the output and forces re-running calibration).

| Component | Weight |
|---|---:|
| Core name TF-IDF/char similarity (Stage 6 hstack, scaled /2) | 0.35 |
| Token-level name overlap (post strip of brand, size, units, pure-numeric) | 0.15 |
| Brand compatibility | 0.15 |
| Size + pack compatibility | 0.20 |
| Category / group compatibility | 0.10 |
| Attributes (organic, form, flavor, storage) | 0.05 |

Real component rules:

- `brand_compatibility`: `1.0` two known nationals with same `brand_norm`; `0.85` PL<->PL cross-store; `0.6` when exactly one side is blank/inferred; `0.5` when both are blank; `0.0` when known nationals are incompatible.
- `size_pack_compatibility`: `1.0` when same family, `rel_diff <= 1%` and same `pack_count`; `0.7` when same family, `rel_diff <= 5%` and same pack; `0.4` when either side has no convertible base size; `0.0` for different families or larger drift.
- `category_group_compatibility`: `1.0` same group; `0.7` `dairy <-> cheese` adjacency; `0.0` otherwise.
- `attributes`: average of four pairs (storage, form, flavor, organic). For storage/form/flavor, `None` counts as "unknown" -> 0.5; for organic, True/False are both observable and True-vs-False is a real conflict (0.0).

**Acceptance rule** (`select_best`):

- Per A item: deterministic ranking with the key `(-score, -exact_size_match, -pack_equal, -attr_agreement, -b_metadata_richness, item_id_b)`.
- Requires `score >= min_score` and, when there is a runner-up, `score_top1 - score_top2 >= min_margin`.
- CLI/module defaults: `min_score=0.55`, `min_margin=0.05` (kept for fixtures and the test suite).
- **Production floor**: `--min-score 0.75 --min-margin 0.05 --top-k 50` — the threshold calibrated in Phase 10B against the labeled 92-row sample (`eval/phase10b_calibration.md`).

### Stage 9 — Tie-break for B duplicates

Implementation: `_tiebreak_key` inside `scoring.py`. B has 2,796 duplicate-like groups (6,320 rows). When several B candidates survive with similar scores for the same A, the lexicographic order of the composite key picks the winner:

1. Total `score` DESC.
2. `exact_size_match` (rel diff <= 1%, same family) DESC.
3. Equal `pack_count` DESC.
4. `attr_agreement_count` (storage / form / flavor / organic) DESC.
5. `b_metadata_richness` (how many B attributes are populated) DESC.
6. `item_id_b` ASC as a stable, deterministic final tie-break.

The 8 / 15 / 29 oz variants are never picked on lexical score alone: `size_mismatch` (R9) or `size_pack_compatibility=0` cuts the wrong pairs before tie-break.

### Stage 10 — Output + audit + validation

Implementation: `betterbasket_matcher/output.py::write_matches`, `write_matches_audit`, `validate_matches_csv`.

`matches.csv` (primary deliverable, **4,394 rows**):

```csv
item_id_A,item_id_B
2197626,92544
1929544,105624
...
```

`matches_audit.csv` (debug/audit, not the deliverable; one row per valid A item processed — accepted, below_threshold, rejected_by_rule, no_candidates):

```csv
item_id_A,item_id_B,score,retrieval_score,top1_top2_margin,source,decision,reason,llm_confidence
```

Where:
- `source ∈ {deterministic, llm_rescued}` (the latter only when `--use-llm-arbiter` is on).
- `decision ∈ {accepted, below_threshold, rejected_by_rule, no_candidates}`.
- `reason`: `ok` if accepted; `below_min_score` / `below_min_margin` for below_threshold; the hard-rule name for rejected_by_rule; `excluded_category` / `excluded_home_decor` / `no_candidates` otherwise.
- `llm_confidence`: empty in deterministic runs.

**Hard output validation** (`validate_matches_csv`, executed at the end of `run_pipeline.py`):

- Exact header `item_id_A,item_id_B`.
- Each `item_id` matches `^\d+$` and exists in `valid_ids_A` / `valid_ids_B` (the 5 quarantined rows do not appear).
- **No duplicate `item_id_A`** (a single best match per A).
- `row_count >= min_rows` (CLI default 4,000; relaxable for fixture runs with `--allow-under-min-rows`).
- `required_pairs` baked into the CLI: A `2197626 -> B 92544` and A `1929544 -> B 105624` (not the 15 oz or 29 oz variant).

Real final-run result: `validation: ok=True, row_count=4394, errors=0`.

### Stage 11 — GPT-5.4 nano arbiter (optional, gray zone)

Implementation: `betterbasket_matcher/llm_arbiter.py`. **Off by default**; the shipped `matches.csv` was generated **without** the arbiter. Activation: `python3 scripts/run_pipeline.py --use-llm-arbiter ...`.

**When the arbiter kicks in** (gated by `pipeline.py`):

- Only rows with `decision=below_threshold` whose `score` falls in the fixed band `[LLM_RESCUE_SCORE_LOW=0.65, LLM_RESCUE_SCORE_HIGH=0.75)`.
- This implies the pair has already passed `evaluate_hard_rules`. `rejected_by_rule` rows never reach the arbiter.

**When it does NOT kick in**:

- `accepted` rows (already cleared the threshold).
- `rejected_by_rule` rows (the LLM cannot override hard rules).
- `no_candidates` rows.
- `below_threshold` rows with score outside `[0.65, 0.75)`.

**Two-pass** within a single run:

1. **Pass 1 — `arbiter.collect`**: the main pipeline loop calls `collect` when the row falls in band. No network; only registers the candidate and builds the `cache_key` (sha256 over `prompt_version | deployment | item_ids | display_names | brands | sizes | groups | failure_reason | round(score, 4)`; never includes `api_key`).
2. **Pass 2 — `arbiter.commit`**: after the loop, sort candidates by `(score DESC, item_id_a ASC)`, apply a per-group cap `max(50, max_calls // n_groups)`, and iterate. Cache hits are served free; misses only call the API while `api_calls_made < max_calls`. Misses past the budget are skipped (cache hits keep being served).

**Prompt** (version `p10c.v1`, system + user):

```
SYSTEM: You are a strict grocery product matcher. Decide whether two items, one
from store A (Walmart) and one from store B (Wegmans), would be treated as the
same product by a typical shopper. ... Respond with strict JSON matching this
schema and nothing else: {"same_product_for_customer": <true|false>,
"confidence": <number in [0.0, 1.0]>, "reason": <short string>,
"blocking_issue": <string or null>}.

USER: Compare these two items and respond with strict JSON.
A (Walmart): name, brand_norm, is_private_label, size, matchable_group, cat_0, cat_1
B (Wegmans): name, brand_norm, is_private_label, size, matchable_group, cat_0, cat_1
Deterministic context: failure_reason, deterministic_score, deterministic_margin
```

**Rescue rule**: a `below_threshold` row is flipped to `accepted` only when the LLM returns `same_product_for_customer=True`, `confidence >= --llm-min-confidence` (default 0.60), and the row has `item_id_B != ""`. The audit becomes `decision=accepted`, `source=llm_rescued`, `reason=llm_rescue_<original_reason>`, and `llm_confidence` is populated for traceability.

**Guarantees**:

- JSON-lines cache at `.cache/llm_arbiter.jsonl` (gitignored). Cache hits are free and survive re-runs.
- Defensive filter: `cache_append` drops any `api_key` / `Authorization` / `Bearer` keys before serializing `input_meta`.
- The LLM **cannot** override hard rules (`rejected_by_rule` rows are never offered).
- The LLM **cannot** change `accepted` rows (they are never offered).
- `api_key` is held in a `field(repr=False)` config slot; it never appears in `__repr__`, logs, prompts, or cache.
- Fail-closed: API exception, JSON parse error, schema violation, NaN/Inf, confidence out of `[0, 1]`, missing field, or budget exhaustion -> no rescue; the deterministic decision stands.
- Credential resolution order: `--llm-creds PATH` -> `$BB_OPENAI_CREDS` -> `/tmp/openai_artifacts/openai_creds.yaml` -> `~/Downloads/openai_creds.yaml`. The YAML must be an `openai:` mapping with `endpoint`, `api_key`, `deployment_name`. `deployment_name = gpt-5.4-nano`.

**Real smoke test** (1 call, 2026-05-04): `api_call=ok`, `latency_s=2.40`, `json_parsed=valid`, `confidence=0.95`. No prompt body, response body, endpoint URL, or `api_key` was printed. No changes to `matches.csv` / `matches_audit.csv` / `.cache/`.

---

## 5. Calibration and evaluation

### 5.1 Phase 10B labeled sample

To calibrate the global `min_score`, a stratified 92-row sample was generated (`scripts/sample_eval.py --mode phase10b`) from the recall-heavy run `--min-score 0.55 --min-margin 0.05` (16,218 rows):

- 6 score buckets (`score_0.55_0.60`, ..., `score_ge_0.80`), round-robin per `matchable_group`.
- Oversample of suspicious groups restricted to `score < 0.70`: `household`, `frozen`, `pantry`, `kitchen_home`, `seafood`.
- `private_label_cross_store` bucket: `A.is_private_label=True`, score in `[0.55, 0.80)`.
- The 2 PDF rows as `pdf_regression` (they do not count toward the denominator; they are output-contract gates).

The sample was labeled in a **preliminary AI-assisted pass (Codex)**, not a human one. The labels in `eval/manual_eval_phase10b.csv` are treated as **provisional** until a human review, and the sampler preserves human edits across re-runs (workflow documented in `eval/manual_eval_phase10b.md`).

### 5.2 Chosen threshold — `--min-score 0.75 --min-margin 0.05`

`eval/phase10b_calibration.md` reports the exact threshold grid over the full audit and the cumulative precision against the labeled sample:

| `min_score` | accepted_count | pdf1 | pdf2 | clears 4k floor |
|:---:|:---:|:---:|:---:|:---:|
| 0.65 | 10,246 | Y | Y | Y |
| 0.70 |  7,279 | Y | Y | Y |
| **0.75** | **4,394** | **Y** | **Y** | **Y** |
| 0.80 |  2,162 | N | Y | N |

| Cumulative threshold | correct | wrong | partial | est_precision (provisional) |
|:---|:---:|:---:|:---:|:---:|
| score >= 0.65 | 36 | 8 | 8 | 0.692 |
| score >= 0.70 | 31 | 4 | 4 | 0.795 |
| **score >= 0.75** | **19** | **0** | **1** | **0.950** |
| score >= 0.80 | 7 | 0 | 1 | 0.875 |

The `0.75` floor is the sweet spot: clears the output-contract floor (`row_count >= 4,000`), keeps both PDF rows accepted, and cumulative precision on the sample jumps from 0.795 to 0.950 when raising the cut from 0.70 to 0.75. The 0.55-0.75 band carried most of the false positives, so the initial recall-heavy run (16,218 rows at `--min-score 0.55`) was retired.

### 5.3 Auto-eval of the final run

`eval/phase10b_calibration.md` also consolidates the auto-eval over the full audit:

- **Decision distribution**: `accepted=4,394 (1.88%)`, `below_threshold=118,140 (50.66%)`, `no_candidates=85,901 (36.84%)`, `rejected_by_rule=24,759 (10.62%)`.
- **Top accepted matchable_groups**: pantry 1,453, snacks 521, personal_care 439, beverages 390, household 355, candy 335, beauty 209, health 175, frozen 164, dairy 99.
- **Score distribution over accepted**: `score_0.75_0.80=2,232`, `score_ge_0.80=2,162` (both buckets weigh ~50%).
- **Per-group provisional precision** in `eval/group_breakdown.md` (90 labeled rows; provisional pre-human-review). Groups with very small samples (<5) are not load-bearing.
- **PDF rows**: A `2197626` accepted with score 0.797 / margin 0.062; A `1929544` accepted with score 0.864 / margin 0.139. Both above the 0.75/0.05 floor.

---

## 6. Volumes, costs, timing (real numbers)

| Metric | Real |
|---|---|
| A items input | 233,199 |
| A items quarantined | 5 |
| A items valid (post-quarantine) | 233,194 |
| A items in-scope (Stage 4) | 181,289 |
| A items rejected_by_rule (Stage 7) | 24,759 |
| A items below_threshold (Stage 8 + 0.75 floor) | 118,140 |
| A items no_candidates | 85,901 |
| **A items accepted -> matches.csv** | **4,394** |
| Final validation | `ok=True, row_count=4394, errors=0` |
| Test suite | **412 passed** |
| Final threshold | `--min-score 0.75 --min-margin 0.05 --top-k 50` |
| LLM calls used for shipped output | **0** |
| LLM smoke test (1 call, 2026-05-04) | `latency_s=2.40, confidence=0.95` |
| Total OpenAI cost | $0 |

Indicative real-runtime breakdown (local CPU, MacBook):

- Stages 1-3 (ingest + parse + normalize), 233k+55k rows: seconds to 1-2 min.
- Stage 4 (scope filter): seconds.
- Stage 5 (taxonomy mapping): seconds.
- Stage 6 (TF-IDF fit + queries with `k=50`, global index with per-group post-filter): ~10-30 seconds in `fit`, queries amortized.
- Stages 7-9 (rules + score + tie-break): seconds.
- Stage 10 (writers + validation): seconds.
- Stage 11 (LLM async, optional, not used for shipped): not applicable.

End-to-end total: well under a minute on standard hardware.

---

## 7. Explicit assumptions

1. **Subset interpretation**: the deliverable is a high-confidence subset, not one row per A item. Justification: the PDF says "at least 4,000 matches" and "complete set ~10k", which are incompatible with forcing 233k. The shipped run ships 4,394.
2. **Scope filter is acceptable**: dropping Toys, Clothing, Picture Frames, etc. is justified by the taxonomy and the exclusion counts (51,905 A rows out_of_scope over 233,194). Documented in the README and reflected in every audit row with a stable reason.
3. **GPT-5.4 nano selective, off by default**: using the LLM as an arbiter is aligned with the brief; the arbiter is implemented and tested but **was not used to produce the shipped `matches.csv`**. Activating it is optional via `--use-llm-arbiter`.
4. **`ic_item_id` is not a universal UPC**: 1.23% of B and 0% of A. Not used as a join key.
5. **B duplicates are an acceptable output target**: if A `Hershey's Candy 23.05oz` matches one of N B candidates with the same brand+name, the deterministic tie-break picks the exact-size match, then equal pack_count, then attr_agreement, then B metadata richness, then `item_id_b` ASC.
6. **Single match per A**: if A has 2 equally valid B matches, pick one (highest score, then tie-break). The task is "single closest match", not "all matches".
7. **Phase 10B labels are provisional**: the 0.75 floor calibration was done with AI-assisted preliminary labels (Codex), not human review. Cumulative precision (0.95 at `>=0.75`, 0.69 at `>=0.65`) and per-group precision are tagged "provisional" in the artifacts until a human pass over `eval/manual_eval_phase10b.csv`. The sampler preserves human edits across re-runs.

---

## 8. Effective implementation timeline

What was actually built, in numbered phases tracked in `docs/HANDOFF.md`:

| Phase | Output | Status |
|---|---|---|
| 0 | Audit + retrieval probe (`docs/dataset_audit.md`, `docs/algorithm_recommendation.md`, JSONs) | done |
| 1 | Frozen fixtures (`tests/fixtures/{mini_a, mini_b, expected_matches}.csv`) with 17 case_ids including the 2 PDF rows | done |
| 2 | `betterbasket_matcher/io.py` + tests | done |
| 3 | `betterbasket_matcher/normalize.py` + tests | done |
| 4 | `betterbasket_matcher/taxonomy.py` + `scope.py` + tests | done |
| 5 | `betterbasket_matcher/retrieval.py` (TF-IDF word + char) + tests | done |
| 6 | `betterbasket_matcher/rules.py` + `scoring.py` + tests | done |
| 7 | `betterbasket_matcher/pipeline.py` + `output.py` + `scripts/run_pipeline.py` + tests | done |
| 8 | Full corpus run, hard CSV validation, first threshold | done |
| 9 | `scripts/sample_eval.py` (sampler + diagnostics) | done |
| 10A | Phase 9 oversample + group breakdown | done |
| 10B | Stratified sample + AI-assisted labels + cumulative precision -> 0.75 floor chosen | done |
| 10C | `betterbasket_matcher/llm_arbiter.py` + CLI flags + tests + 1-call smoke | done (off by default) |
| 11 | Submission polish: README, SUBMISSION_CHECKLIST, narrative docs | done |

Test count grew monotonically phase by phase up to 412 passing. No commit was made until explicit user authorization.

---

## 9. Risks and mitigations

| Risk | Probability | Impact | Mitigation |
|---|---|---|---|
| Malformed A rows contaminate output | low | critical | `^\d+$` validation + quarantine in Stage 1. Final validator checks IDs against numeric sets. |
| Brand inference too noisy | medium | medium | Validate inferred brands against B brand list; flag `brand_inferred=True`; lower its weight in scoring. |
| Private-label retrieval pulls noise | high (without mitigation) | high | PL suppression in the query (probe confirms it). Category hard rule cuts `Great Lakes` cheese. |
| B duplicates -> arbitrary choice | high | low | Tie-break: exact size -> attributes -> metadata richness -> stable `item_id`. |
| We fall short of 4,000 matches | n/a | critical | The shipped run emits 4,394 (>4,000); the threshold grid (`eval/phase10b_calibration.md`) shows that 0.65 / 0.70 also clear if relaxation is needed. |
| Malformed output CSV | low | critical | Final smoke test: header, numeric IDs, no `item_id_A` duplicates, PDF examples. |
| LLM rate-limited / API down | low | medium | LLM is optional. Fallback: deterministic-only. Local cache. |
| LLM cost spikes | low | medium | `max_calls` cap with assert. Running cost logging. |
| `matchable_group` mapping leaves A without candidates | medium | high | Start with narrow cross-group links; iterate by inspecting unmatched products in eval. |
| Embeddings needed for extra recall | low | low | The deterministic core already ranks both PDF examples at #1 and ships 4,394 matches over the 4,000 floor; embeddings are not required. |

---

## 10. Deliverables (what's in the repo)

### Required (from the PDF)

1. `matches.csv` — `(item_id_A, item_id_B)` with **4,394 rows**, single best B per A, no `item_id_A` duplicates, both PDF rows correct. Exact header `item_id_A,item_id_B`. Generated by `scripts/run_pipeline.py --min-score 0.75 --min-margin 0.05 --top-k 50`.
2. Executable, reproducible Python code: `betterbasket_matcher/` (10 modules) + `scripts/run_pipeline.py`. No pandas, no all-pairs, no LLM in the deterministic path.

### Extras (what differentiates the submission)

3. `matches_audit.csv` — score, retrieval_score, top1_top2_margin, source, decision, reason, llm_confidence; one row per valid A item processed.
4. `README.md` — interview-ready overview, headline facts, pipeline summary, output validation, layout, commands to reproduce audit / probe / shipped run, and the detailed optional-arbiter section.
5. `solution.md` (this file) — polished narrative.
6. `solutioneasyexplained.md` — plain-English version.
7. `app.md` — visual view of the shipped pipeline.
8. `SUBMISSION_CHECKLIST.md` — reviewer-facing checklist with verifiable commands.
9. `docs/dataset_audit.md` + `docs/algorithm_recommendation.md` + reproducible JSONs + `scripts/audit_data.py` + `scripts/retrieval_probe.py`.
10. `requirements.txt` with scikit-learn, scipy, pytest, rank-bm25, pyyaml, openai.
11. `tests/` — **412 tests** covering IO, normalize, taxonomy, scope, retrieval, rules, scoring, pipeline (fixture and full), output validation, sample_eval, llm_arbiter (with FakeClient, no network, no credentials).
12. `eval/manual_eval_phase10b.{csv,md}` + `eval/group_breakdown.md` + `eval/phase10b_calibration.md` — stratified 92-row sample with AI-assisted preliminary labels, exact threshold grid, cumulative precision, per-group provisional precision.
13. `betterbasket_matcher/llm_arbiter.py` + `tests/test_llm_arbiter.py` — optional GPT-5.4 nano arbiter, off by default, fail-closed, cached at `.cache/llm_arbiter.jsonl`.
14. `docs/HANDOFF.md` — session-by-session log of phases 0-11.

---

## 11. Appendix: discarded alternatives

### Why not LLM-first

- 12,946,275,684 pairs — impossible.
- Even after reducing the universe, the LLM **still needs** retrieval, normalization, and hard rules to avoid getting 8 oz vs 15 oz tomato sauce wrong.
- Non-deterministic, expensive to audit.

### Why not UPC-first

- A: 0 UPC-like fields. No UPC-centric plan is viable.

### Why not fuzzy/Levenshtein-only

- Works OK for typos. Fails on paraphrase ("Whole Milk" vs "Vit D Milk").
- Cannot handle PL cross-store.
- B duplicate-like groups (8/15/29 oz) require size rules, not string distance.

### Why not BM25 + FAISS + RRF as primary engine

- The repo dry-run (`scripts/retrieval_probe.py`) showed that BM25 with brand_included has critical lexical traps in private label: rank 5 for `Great Value Organic Tomato Sauce 8 oz`, top-1 `Colgate ... Great Regular Flavor`. TF-IDF word + char ranks both examples at #1 without needing fusion or a second ranker.
- FAISS with embeddings adds complexity and a large-model dependency. MTEB +1.5 points do not offset the cost, and the shipped pipeline already ranks both PDF rows at #1 without embeddings.
- **BM25 stayed as a diagnostic** in the retrieval probe; **embeddings/FAISS are not part of the shipped pipeline** and are not needed given the current output.

### Why not train a supervised classifier

- Requires labeled data. We do not have it. Out of scope for 3 days.

### Why not a cross-encoder rerank

- It would improve rerank quality, but ~1M pairs x 30ms on CPU = ~8 hours. Unacceptable.

### Why not expose a service (FastAPI)

- The task asks for a script. Code is modular enough to be wrapped as a service later.

---

## 12. Technical glossary

- **Entity resolution / record linkage**: matching records from different sources that refer to the same real entity, without an exact join key.
- **UPC**: Universal Product Code, 12-digit code. When both sides expose it, it is the most reliable form of matching.
- **TF-IDF**: term frequency x inverse document frequency. Classic lexical ranking.
- **TF-IDF word + char n-grams**: two vectorizers (word `(1, 2)` and char-wb `(3, 5)`) concatenated. Improves robustness to orthographic variants and typos while preserving lexical precision.
- **BM25**: Okapi BM25, statistical lexical ranking, evolution of TF-IDF. Useful as a diagnostic, not as the primary engine on this dataset due to lexical traps in private label.
- **Embeddings**: dense vectors produced by a language model. Cosine similarity ~= semantic similarity. **Not included** in this pipeline; weaker on numeric sizes/packs/flavors.
- **FAISS**: Meta's library for fast vector search. **Not used** in this pipeline.
- **Private label**: a retailer's own brand (Great Value at Walmart, Wegmans-brand at Wegmans).
- **National brand**: brand present at multiple retailers (Coca-Cola, Chobani).
- **Matchable group**: shared intermediate taxonomy that lets us compare `category_0..2` between A and B without coupling to the original taxonomies.
- **Hard rule**: rule that rejects a pair before scoring (size, group, brand/PL, pack, organic, form, storage, flavor, alcohol).
- **Deterministic score**: score computed with a fixed weighted formula — no random, no LLM.
- **Audit trail**: the `matches_audit.csv` record with score, source, margin, confidence, and reason for each match for manual review.

---

**End of document.** The pipeline is implemented, tested (412 green tests), validated (4,394 rows in `matches.csv` with the exact header, both PDF rows correct, 0 `item_id_A` duplicates), and documented. To reproduce the shipped run end-to-end, see the `README.md` "Reproducing the shipped match output" section. To verify the submission gates, see `SUBMISSION_CHECKLIST.md`.
