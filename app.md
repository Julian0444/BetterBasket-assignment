# Visual Pipeline — BetterBasket Product Matching

> Scannable view of the already-implemented pipeline. For detailed technical rationale, see [solution.md](solution.md). The sources of truth are `docs/dataset_audit.md`, `docs/algorithm_recommendation.md`, and the code in `betterbasket_matcher/`.

> Status: deliverable shipped. `matches.csv` has **4,394 rows** generated with `python3 scripts/run_pipeline.py --min-score 0.75 --min-margin 0.05 --top-k 50`. 412 green tests. The LLM arbiter is implemented but **off by default** and **was not used to produce `matches.csv`**.

---

## 1. Full pipeline (11 stages)

```
+---- A: 233,199 items (Walmart) ----+         +---- B: 55,516 items (Wegmans) ----+
|                                    |         |                                    |
+------+-----------------------------+         +------+-----------------------------+
       |                                              |
       v                                              v
================================================================================
| Stage 1 — INGEST + VALIDATE                  (betterbasket_matcher/io.py)    |
|  - csv.DictReader streaming (no pandas)                                      |
|  - Validate item_id with ^\d+$ and QUARANTINE the 5 malformed A rows         |
|    (non-numeric item_id + shifted columns, e.g. " | Pack of 12")             |
|  - Validate non-blank name                                                   |
|  - Output: (valid_a=233,194, quarantined_a=5, valid_b=55,516)                |
================================================================================
                                       |
                                       v
================================================================================
| Stage 2 — PARSE JSON / TAGS                  (betterbasket_matcher/io.py)    |
|  - item_info and sizing_comp: tolerant parse_json_dict (json -> {} when not  |
|    a dict; never raises)                                                     |
|  - B tags: tolerant parse_tags (JSON list -> Postgres array -> comma split)  |
|  - A tags: 100% blank (except the 5 malformed); not used                     |
================================================================================
                                       |
                                       v
================================================================================
| Stage 3 — NORMALIZE                  (betterbasket_matcher/normalize.py)     |
|  - Brand: A uses brand_raw or infers via PL whitelist (brand_inferred=True), |
|         B uses normalized brand_raw                                          |
|  - Private label:                                                            |
|    - A: canonical brand in {great value, marketside, freshness guaranteed,   |
|                              equate, mainstays, bettergoods, sam s choice,   |
|                              parent s choice, ol roy, special kitty,         |
|                              clear american, ...} (prefix match w/ boundary) |
|    - B: brand_norm == 'wegmans' OR tag 'wegmans brand' / 'wegmans_brand'     |
|  - Categories from item_info.category_0..2 (raw category/dept/subcat are     |
|    100% blank in both files)                                                 |
|  - Asymmetric size: A first from name, B from sizing_comp.size_user_friendly |
|  - Pack count parsed SEPARATELY from per-unit size (renderer: "8oz", not     |
|    "8.0oz")                                                                  |
|  - organic / form / storage / flavor from item_info + name + B tags          |
|  - retrieval_text: brand suppressed when is_private_label=True               |
================================================================================
                                       |
                                       v
================================================================================
| Stage 4 — SCOPE FILTER (A-only)              (betterbasket_matcher/scope.py) |
|  Exclude A.cat0 in {Toys, Clothing, Home Improvement, Sports & Outdoors,     |
|                     Party & Occasions, Office Supplies, Auto & Tires,        |
|                     Electronics, Arts Crafts & Sewing, Jewelry, Books,       |
|                     Cell Phones} -> reason="excluded_category"               |
|  Exclude Home > {Home Decor, Picture Frames, Bedding, Furniture, Rugs,       |
|                  Wall Art} -> reason="excluded_home_decor"                   |
|  Keep Home > Kitchen & Dining (reason="in_scope")                            |
|  Real result: in_scope_a=181,289 (out of 233,194 valid_a)                    |
================================================================================
                                       |
                                       v
================================================================================
| Stage 5 — TAXONOMY BLOCKING       (betterbasket_matcher/taxonomy.py)         |
|  Map both stores to a shared `matchable_group`. GROUPS:                      |
|    pantry, snacks, candy, beverages, dairy, cheese, frozen, produce,         |
|    meat, seafood, bakery, prepared_foods, baby, pets, household,             |
|    personal_care, health, beauty, kitchen_home, wine_beer_spirits            |
|                                                                              |
|  groups_compatible() is conservative: exact equality + symmetric             |
|  dairy <-> cheese (B carves cheese as a separate cat0 from A's dairy).       |
|  Any other pair returns False. None/'' returns False as well.                |
================================================================================
                                       |
                                       v
================================================================================
| Stage 6 — CANDIDATE RETRIEVAL    (betterbasket_matcher/retrieval.py)         |
|  TfidfRetriever: global index over B + post-filter by matchable_group        |
|  Concatenated vectorizers (hstack):                                          |
|    - word ngram_range=(1, 2), sublinear_tf, L2-norm                          |
|    - char_wb ngram_range=(3, 5), sublinear_tf, L2-norm                       |
|                                                                              |
|  ASYMMETRIC retrieval text by brand type (built in Stage 3):                 |
|    - National brand -> "{brand} {core_name} {size} {cat0} {cat1} {attrs}"    |
|    - Private label  -> "{core_name} {size} {cat0} {cat1} {attrs}"            |
|      (suppress great value / marketside / wegmans to avoid the documented    |
|       "Great Value" -> "Great Lakes" trap)                                   |
|                                                                              |
|  query(product_a, k=50): top-k among B compatible by group, sorted by score  |
|  DESC, tie-break by item_id_b ASC. BM25/embeddings are not used.             |
================================================================================
                                       |
                                       v
================================================================================
| Stage 7 — HARD RULES (before scoring)   (betterbasket_matcher/rules.py)      |
|  evaluate_hard_rules(a, b) -> RuleResult(passed, reason). Ordered chain;     |
|  the first failing rule wins the reason. None on either side of an optional |
|  attribute = "does not apply", not a rejection.                              |
|   1. alcohol_mismatch (group wine_beer_spirits or core_name keyword)         |
|   2. group_mismatch (groups_compatible)                                      |
|   3. national_vs_private_label (trusted NB vs PL)                            |
|   4. brand_mismatch (NB<->NB with different brand_norm)                      |
|   5. storage_mismatch (frozen vs refrigerated)                               |
|   6. form_mismatch (powder/liquid/sliced/shredded/whole_bean/ground)         |
|   7. pet_type_mismatch (cat vs dog within pets)                              |
|   8. flavor_mismatch (vanilla vs chocolate)                                  |
|   9. size_mismatch (compatible families, drift > 8% of base)                 |
|  10. pack_mismatch (multipack vs multipack with different base > 1%)         |
|                                                                              |
|  Real result: rejected_by_rule=24,759 A rows.                                |
================================================================================
                                       |
                                       v
================================================================================
| Stage 8 — DETERMINISTIC SCORE   (betterbasket_matcher/scoring.py)            |
|  score_pair(a, b, retrieval_score) -> ScoreBreakdown                         |
|  Fixed weights (not recalibrated):                                           |
|    score = 0.35 * core_name_TF-IDF                                           |
|          + 0.15 * token_overlap (no units / no numeric)                      |
|          + 0.15 * brand_compatibility (NB<->NB, PL<->PL, mixed)              |
|          + 0.20 * size_pack_compatibility                                    |
|          + 0.10 * category_group_compatibility                               |
|          + 0.05 * attributes (organic, form, flavor, storage)                |
|                                                                              |
|  select_best applies rules + ranking. Accepts top-1 if:                      |
|    - score >= min_score    (CLI default 0.55; production 0.75)               |
|    - top1 - top2 >= margin (CLI default 0.05; production 0.05)               |
|                                                                              |
|  Real result (0.75/0.05/50): accepted=4,394, below_threshold=118,140.        |
================================================================================
                                       |
                                       v
================================================================================
| Stage 9 — TIE-BREAK B DUPLICATES (2,796 groups, 6,320 rows)                  |
|  Implemented in scoring._tiebreak_key (composite key, stable sort):          |
|   1. total score DESC                                                        |
|   2. exact_size_match (rel diff <= 1%, same family)                          |
|   3. equal pack_count                                                        |
|   4. attr_agreement_count (storage / form / flavor / organic)                |
|   5. b_metadata_richness                                                     |
|   6. item_id_b ASC (stable, deterministic)                                   |
|                                                                              |
|  The 8 / 15 / 29 oz variants are never picked on lexical score alone:        |
|  size_mismatch (R9) or size_pack_compat=0 cuts the wrong ones first.         |
================================================================================
                                       |
                                       v
================================================================================
| Stage 10 — OUTPUT + AUDIT + VALIDATION   (betterbasket_matcher/output.py)    |
|  matches.csv          -> header item_id_A,item_id_B  <- deliverable (4,394) |
|  matches_audit.csv    -> 9 columns, one row per processed valid A:           |
|                          item_id_A, item_id_B, score, retrieval_score,       |
|                          top1_top2_margin, source, decision, reason,         |
|                          llm_confidence (empty in deterministic run)         |
|  validate_matches_csv -> exact header, numeric IDs in valid sets,            |
|                          no item_id_A duplicates, >=4,000 rows,              |
|                          required_pairs (PDF) intact.                        |
|  Real result: validation ok=True, row_count=4,394, errors=0.                 |
================================================================================
                                       |
                                       v
================================================================================
| Stage 11 — OPTIONAL GPT-5.4 NANO ARBITER (off by default)                    |
|                                  (betterbasket_matcher/llm_arbiter.py)       |
|  Implemented and tested, but NOT used for the shipped matches.csv.           |
|  Activation: scripts/run_pipeline.py --use-llm-arbiter                       |
|                                                                              |
|  Only sees rows decision=below_threshold with score in [0.65, 0.75) (fixed   |
|  band LLM_RESCUE_SCORE_LOW/HIGH in pipeline.py). It cannot:                  |
|    - override hard rules (already passed evaluate_hard_rules)                |
|    - change decision=accepted rows (already cleared the threshold)           |
|    - change rejected_by_rule rows                                            |
|                                                                              |
|  Two-pass: collect (no network) -> commit (sort score DESC, per-group cap    |
|  max(50, max_calls/n_groups), free cache hits, bounded API calls).           |
|  Strict JSON {same_product_for_customer, confidence in [0,1], reason,        |
|  blocking_issue}. Rescues only if confidence >= --llm-min-confidence 0.60.   |
|  Fail-closed on any exception / parse error / NaN / out-of-range.            |
|                                                                              |
|  Real smoke test (1 call) executed on 2026-05-04: api_call=ok,               |
|  latency_s=2.40, json_parsed=valid, confidence=0.95.                         |
================================================================================
```

---

## 2. Real volume-reduction funnel

```
+---------------------------------------------------------------------+
|  Cartesian product A x B                                            |
|  12,946,275,684 pairs  (impossible to evaluate fully)               |
+----------------------------+----------------------------------------+
                              |  Stage 1 — quarantine 5 A rows
                              v
+---------------------------------------------------------------------+
|  Post-validation universe                                           |
|  valid_a=233,194  *  valid_b=55,516                                 |
+----------------------------+----------------------------------------+
                              |  Stage 4 — A scope filter
                              v
+---------------------------------------------------------------------+
|  in_scope_a=181,289  *  out_of_scope=51,905                         |
+----------------------------+----------------------------------------+
                              |  Stage 5-6 — group blocking + TF-IDF k=50
                              v
+---------------------------------------------------------------------+
|  Materialized candidates (top-50 per A in compatible group)         |
|  no_candidates=85,901  (A in group without counterpart or empty B)  |
+----------------------------+----------------------------------------+
                              |  Stage 7 — hard rules
                              v
+---------------------------------------------------------------------+
|  rejected_by_rule=24,759  *  survivors go to scoring                |
+----------------------------+----------------------------------------+
                              |  Stage 8 — score + min_score/min_margin
                              v
+---------------------------------------------------------------------+
|  below_threshold=118,140 (score < 0.75 or margin < 0.05)            |
|  accepted=4,394          (top-1 above the floor)                    |
+----------------------------+----------------------------------------+
                              |  Stage 11 (optional, off by default)
                              v
+---------------------------------------------------------------------+
|  matches.csv                                                        |
|  4,394 rows (item_id_A, item_id_B), validated                       |
+---------------------------------------------------------------------+
```

**Key takeaway**: the deterministic core delivers 100% of the shipped output. The LLM arbiter, if turned on, would only see the `[0.65, 0.75)` sub-band of `below_threshold` — a small subset of the original Cartesian product.

---

## 3. The 3 types of match

```
=======================================================================
| TYPE 1 — Exact match with UPC                                       |
|                                                                     |
|   A: UPC=...  ----[ direct JOIN ]---->  B: UPC=...                  |
|                                                                     |
|   Confidence: 99%   |   In this dataset: NOT APPLICABLE             |
|                                                                     |
|   Reason: A has 0 UPC-like fields, B has 681 ic_item_id (1.23%).    |
|   Without a counterpart in A, the join is not viable.               |
=======================================================================

=======================================================================
| TYPE 2 — Exact via attributes (same brand, same size, no UPC)       |
|                                                                     |
|   A: "Chobani Whole Milk Greek Honey Blended 5.3 oz"                |
|                          |                                          |
|                          v  exact brand + exact size + name sim     |
|   B: "Chobani Greek Honey Blended Yogurt"  +  size 5.3 oz           |
|                                                                     |
|   Confidence: 90-95%   |   PDF gate: A 2197626 -> B 92544           |
=======================================================================

=======================================================================
| TYPE 3 — Non-exact private label (cross-store)                      |
|                                                                     |
|   A: "Great Value Organic Tomato Sauce, 8 oz"  (Walmart PL)         |
|                          |                                          |
|                          v  PL bridge + hard rules + score          |
|   B: "Wegmans Organic Tomato Sauce"  +  size 8 oz   (Wegmans PL)    |
|                                                                     |
|   Confidence: 70-85%   |   PDF gate: A 1929544 -> B 105624          |
|                                                                     |
|   Trap: B also has 15 oz (103620) and 29 oz (1086860).              |
|   The size hard rule (R9) cuts the wrong pairs.                     |
=======================================================================
```

---

## 4. Critical audit findings (visual summary)

```
+---------------------------------------------------------------------+
|  Malformed A rows (non-numeric item_id)                             |
|      5 rows with shifted columns, e.g. " | Pack of 12"              |
|      -> Validate item_id with ^\d+$ and QUARANTINE before matching. |
+---------------------------------------------------------------------+
|  UPC is not viable                                                  |
|      A: 0 UPC-like fields   *   B: 681 ic_item_id (1.23%)           |
|      -> No exact join key. 100% text-based problem.                 |
+---------------------------------------------------------------------+
|  Decoy columns (all 100% empty)                                     |
|      name_clean, category, department, subcategory, size_raw,       |
|      item_type, is_private_label, is_organic                        |
|      -> Reconstruct EVERYTHING from name/brand_raw/JSON/tags.       |
+---------------------------------------------------------------------+
|  Brand crisis in A                                                  |
|      45.87% blank globally   *   63.89% blank in Food               |
|      Shared normalized brands: 2,441                                |
|      -> Brand inference from PL whitelist + word-boundary prefix.   |
+---------------------------------------------------------------------+
|  Asymmetric size location                                           |
|      A: regex over name = 90.06% in Food                            |
|      B: sizing_comp.size_user_friendly = 95.51% parseable           |
|      -> Asymmetric extraction strategy.                             |
+---------------------------------------------------------------------+
|  B duplicates (size is decisive)                                    |
|      2,796 groups (brand, name) with >1 row, 6,320 total rows       |
|      Wegmans Organic Tomato Sauce -> 8 oz, 15 oz, 29 oz             |
|      -> Size is a HARD rule, tie-break ordered by exact size match. |
+---------------------------------------------------------------------+
|  Pack noise in A                                                    |
|      22,882 rows with "(N pack)" or "Pack of N" prefix              |
|      -> Clean before tokenizing; pack_count stored separately.      |
+---------------------------------------------------------------------+
|  Incompatible taxonomies                                            |
|      A: Walmart (Food/Toys/Pets/...)                                |
|      B: Wegmans (Grocery/Frozen/Dairy/Bakery/Cheese/Seafood/...)    |
|      -> Shared matchable_group + dairy<->cheese adjacency.          |
+---------------------------------------------------------------------+
|  A categories without counterpart (>= 48,007 rows)                  |
|      Toys, Clothing, Home Improvement, Sports & Outdoors,           |
|      Party & Occasions, Office Supplies, Auto & Tires,              |
|      Electronics, Arts Crafts & Sewing, Jewelry, Books, Cell Phones |
|      -> scope.is_a_in_scope before retrieval.                       |
+---------------------------------------------------------------------+
|  Private-label retrieval pulls noise                                |
|      "Great Value" -> BM25 retrieves "Colgate Great Regular Flavor" |
|        (rank 1) and "Great Lakes Provolone Cheese"                  |
|      -> TF-IDF word+char + PL suppression in retrieval_text.        |
+---------------------------------------------------------------------+
|  B tags carry valuable signal                                       |
|      ~41% of B with populated tags: wegmans brand, organic, gluten  |
|      free, family pack, vegan, food you feel good about             |
|      -> Tolerant parse_tags (JSON / Postgres array / split).        |
+---------------------------------------------------------------------+
```

---

## 5. Stage 6 — Retrieval with TF-IDF word + char

```
                 A item: "Great Value Organic Tomato Sauce 8 oz" (private label)
                 -> brand-suppressed retrieval_text:
                    "organic tomato sauce 8oz food pantry"
                                       |
                                       v
              +------------------------------------------+
              |  Combined TF-IDF                         |
              |   - word ngram_range=(1, 2)              |
              |   - char_wb ngram_range=(3, 5)           |
              |   - L2-norm + hstack                     |
              +------------------------------------------+
                                       |
                                       v
              +------------------------------------------+
              |  Top-50 (filtered by matchable_group)    |
              |  1. Wegmans Organic Tomato Sauce 8 oz    |
              |  2. Wegmans Organic Tomato Sauce 15 oz   |
              |  3. Wegmans Organic Tomato Sauce 29 oz   |
              |  4. Wegmans Tomato Sauce 8 oz            |
              |  5. ...                                  |
              +------------------------------------------+
                                       |
                                       v
              +------------------------------------------+
              |  Stage 7 — hard rule R9 (size)           |
              |  -> Only the 8 oz one survives           |
              |  -> Stage 8 score = 0.864 (PDF row 2)    |
              +------------------------------------------+
```

**Why TF-IDF word + char and not BM25/FAISS/RRF** (see `docs/retrieval_probe_results.md`):

| Probe | Ranker | Mode | Expected B | Rank | Top-1 |
|---|---|---|---:|---:|---|
| Chobani 5.3 oz honey yogurt | TF-IDF | brand_included | 92544 | 1 | 92544 |
| Chobani 5.3 oz honey yogurt | TF-IDF | suppress PL | 92544 | 1 | 92544 |
| Great Value Organic Tomato 8 oz | TF-IDF | brand_included | 105624 | 1 | 105624 |
| Great Value Organic Tomato 8 oz | TF-IDF | suppress PL | 105624 | 1 | 105624 |
| Great Value Organic Tomato 8 oz | BM25 | brand_included | 105624 | 5 | Colgate Great Regular Flavor |
| Great Value Organic Tomato 8 oz | BM25 | suppress PL | 105624 | 1 | 105624 |
| Great Value Provolone (text) | BM25 | brand_included | n/a | n/a | Great Lakes Provolone Cheese |

- TF-IDF word + char wins on private label without needing fusion or a second ranker.
- BM25 with brand included falls into lexical traps (`Great` / `Value` inflate unrelated candidates).
- Semantic embeddings are weaker on numeric sizes/packs/flavors, which is where B duplicates hurt most; ruled out for this deliverable.

---

## 6. Stage 7 — Hard rules in cascade (real order)

`evaluate_hard_rules` short-circuits on the first failure; the rest of the chain is not evaluated. `None` on either side of an optional attribute -> the rule does not apply, no rejection.

```
                      Candidate (A, B) enters
                              |
                              v
        +------------------------------------------------------+
        |  R1. alcohol_mismatch                                |
        |     (group wine_beer_spirits or core_name keyword)   |
        +------+-----------------------------------------+-----+
         FAIL  |                                         | PASS
               v                                         v
        +--------------+    +--------------------------------------+
        | DISCARDED    |    |  R2. group_mismatch                  |
        +--------------+    +------+-----------------+-------------+
                             FAIL  |                 | PASS
                                   v                 v
                            +--------------+  +-----------------------------+
                            | DISCARDED    |  |  R3. national_vs_PL         |
                            +--------------+  |      (trusted NB vs PL)     |
                                              +----+------------+-----------+
                                              FAIL |            | PASS
                                                   v            v
                                          +------------+  +--------------------+
                                          | DISCARDED  |  | R4. brand_mismatch |
                                          +------------+  |  (NB<->NB diff)    |
                                                          +--+---------+-------+
                                                          FAIL|         | PASS
                                                              v         v
                                                    +------------+  +--------------+
                                                    | DISCARDED  |  | R5..R10      |
                                                    +------------+  | storage/form |
                                                                    | pet/flavor   |
                                                                    | size/pack    |
                                                                    +--+-------+---+
                                                                  FAIL |       | PASS
                                                                       v       v
                                                            +------------+  +------------------+
                                                            | DISCARDED  |  | Goes to Stage 8  |
                                                            +------------+  +------------------+
```

**The most critical** (in terms of avoiding false matches):

- R3 / R4 (brand and PL). Without them, "Coca-Cola Classic" matches "Wegmans Cola" on lexical similarity — a false match for pricing.
- R9 (size). Without it, B duplicates (8/15/29 oz) win on lexical alone and the wrong jar is delivered.
- R1 (alcohol). The "non-alcoholic phrases" override (ginger beer / wine vinegar / rum cake / beer cheese) avoids false rejects.

---

## 7. Stage 8 — Score composition

```
+--------------------------------------------------------------------+
|  score = 0.35 * core_name_TF-IDF                                   |
|        + 0.15 * token_overlap (post strip of brand and size)       |
|        + 0.15 * brand_compatibility                                |
|        + 0.20 * size_and_pack_compatibility                        |
|        + 0.10 * category_group_compatibility                       |
|        + 0.05 * attributes (organic, form, flavor, storage)        |
+--------------------------------------------------------------------+

Visualized weights:

  core_name_TF-IDF      ###################################   35%
  size_and_pack         ####################                  20%
  token_overlap         ###############                       15%
  brand_compatibility   ###############                       15%
  category_group        ##########                            10%
  attributes            #####                                  5%
```

**Acceptance rule**:

```
  Per A, pick the B candidate with the highest score (deterministic tie-break)
  Require:
    - score >= min_score        (CLI prod: 0.75)
    - score_top1 - score_top2 >= min_margin (CLI prod: 0.05)
  Otherwise, decision=below_threshold, not written to matches.csv
```

**Real audit examples** (`eval/phase10b_calibration.md` sec. 6):

```
+---------------------------------------------------------------------+
| PDF 1 — Chobani Honey 5.3oz <-> Chobani Greek Honey Blended 5.3oz   |
|   score=0.7974   retrieval_score=1.235   margin=0.062               |
|   decision=accepted   reason=ok                                     |
+---------------------------------------------------------------------+
| PDF 2 — Great Value Organic Tomato 8oz <-> Wegmans Organic Tomato 8oz|
|   score=0.8643   retrieval_score=1.460   margin=0.139               |
|   decision=accepted   reason=ok                                     |
|   (15oz / 29oz cut by R9 before scoring)                            |
+---------------------------------------------------------------------+
```

---

## 8. Stage 11 — LLM trigger (real decision)

The arbiter is implemented in `betterbasket_matcher/llm_arbiter.py`, **off by default**. When activated with `--use-llm-arbiter`, it only enters the cycle if the deterministic select_best returned `below_threshold` and `score in [0.65, 0.75)`.

```
                Top-1 candidate post Stage 8 with score s and margin m
                              |
                              v
                +-------------------------------+
                |  Are there candidates?        |
                +-----+-------------------+-----+
                  no  |                   | yes
                      v                   v
             +----------------+   +--------------------------------+
             | no_candidates  |   |  Is select_best == 'selected'? |
             |  (audit row,   |   +--+---------------------------+-+
             |   no match)    |   yes |                           | no
             +----------------+      v                           v
                            +-----------------------+   +-----------------------+
                            | ACCEPT                |   |  Is reason in         |
                            | source=deterministic  |   |   below_threshold and |
                            | matches.csv += pair   |   |   s in [0.65, 0.75)?  |
                            +-----------------------+   +-+---------------+-----+
                                                       yes |               | no
                                                          v               v
                                          +----------------------+  +--------------------+
                                          | Only if --use-llm-   |  | Keep               |
                                          | arbiter active:      |  | below_threshold    |
                                          |  arbiter.collect()   |  | or rejected_by_rule|
                                          +-----+----------------+  +--------------------+
                                                |
                                          (post-loop)
                                                |
                                                v
                                  +--------------------------+
                                  | arbiter.commit()         |
                                  |  - sort score DESC       |
                                  |  - per-group cap         |
                                  |  - free cache hits       |
                                  |  - bounded API calls     |
                                  |  - strict-JSON parse     |
                                  +-----+--------------+-----+
                                        |              |
                            same_product=True &&       |
                            confidence >= 0.60         | otherwise
                                        v              v
                            +-------------------+  +----------------------+
                            | FLIP to accepted  |  | Keep                 |
                            | source=llm_       |  | below_threshold      |
                            |   rescued         |  | (deterministic       |
                            | reason=llm_       |  |  decision intact)    |
                            |   rescue_<orig>   |  +----------------------+
                            | llm_confidence    |
                            |   populated       |
                            +-------------------+
```

**Guarantees**:

- The LLM **never** overrides hard rules (it is not called for rejected_by_rule).
- The LLM **never** changes accepted rows (they already cleared the threshold).
- Fail-closed on any exception / parse error / NaN / out-of-range / budget exhaustion.
- `api_key` never appears in `__repr__`, logs, prompts, or in the .jsonl cache.
- The shipped `matches.csv` was generated WITHOUT the arbiter.

---

## 9. Real output distribution (4,394 rows)

All rows in the shipped `matches.csv` have `source=deterministic`. The score band concentrates above the 0.75 floor:

```
+---------------------------------------------------------------------+
|  Accepted score-bucket distribution (eval/phase10b_calibration.md)  |
+---------------------------------------------------------------------+
|                                                                     |
|  score_0.75_0.80  ############################  2,232  (50.8%)      |
|  score_ge_0.80    ############################  2,162  (49.2%)      |
|                                                                     |
+---------------------------------------------------------------------+

Top-10 matchable_group by accepted_count:
  pantry          1,453      personal_care     439
  snacks            521      beverages         390
  household         355      candy             335
  beauty            209      health            175
  frozen            164      dairy              99
```

Cumulative precision over the Phase 10B labeled sample (`eval/phase10b_calibration.md` sec. 5; AI-assisted labels, **provisional**):

| Cumulative threshold | correct | wrong | partial | est_precision |
|:---|:---:|:---:|:---:|:---:|
| score >= 0.65 | 36 | 8 | 8 | 0.692 |
| score >= 0.70 | 31 | 4 | 4 | 0.795 |
| **score >= 0.75 (shipped)** | **19** | **0** | **1** | **0.950** |
| score >= 0.80 | 7 | 0 | 1 | 0.875 |

The 0.75 floor was chosen because the 0.55-0.75 band carried most of the false positives. Human verification of the labels is left as an optional pre-submission improvement.

---

## 10. Qualitative comparison of approaches (final reading)

```
+----------------------------------+----------------------------------------------+
| Approach                         | Reading                                      |
+----------------------------------+----------------------------------------------+
| Fuzzy / regex only               | Fast but high risk of false positives.       |
|                                  | Does not cover paraphrase or size duplicates.|
| BM25 brand_included only         | Fails on private label (probe: "Great Value" |
|                                  | -> "Great Lakes" / "Great Regular Flavor").  |
|                                  | Useful only as a secondary diagnostic.       |
| TF-IDF word+char + hard rules    | What the repo ships. The probe ranked both   |
|   (deterministic core)           | PDF rows at #1, validation ok in production. |
| + GPT-5.4 nano optional arbiter  | Implemented, off by default. Only gray       |
|                                  | zone [0.65, 0.75); fail-closed; cached.      |
| Cross-encoder rerank             | Theoretical marginal improvement; CPU runtime|
|                                  | makes it impractical for the deadline.       |
| LLM on every candidate           | Costly, slow, and without rules it still     |
|                                  | does not solve the size traps.               |
| LLM-first over Cartesian         | Infeasible: ~13B pairs break any reasonable  |
|                                  | budget.                                      |
+----------------------------------+----------------------------------------------+
```

---

## 11. Real volumes and costs

```
+----------------------------------------------------------------------+
|  INPUT                                                               |
|  - A: 233,199 items   *   B: 55,516 items                            |
|  - Total on-disk size: 223 MB                                        |
+----------------------------------------------------------------------+
|  COMPUTE (everything on local CPU)                                   |
|  - Full deterministic end-to-end pipeline                            |
|  - Stage 1-10 (full run, no LLM)                          seconds    |
|    -> 4,394 accepted, validation ok                                  |
|  - Stage 11 (LLM async, optional, not used in shipped)    not run    |
|    -> 1-call smoke test: 2.40s, $~0                                  |
+----------------------------------------------------------------------+
|  COST                                                                |
|  - OpenAI GPT-5.4 nano (not used for shipped)                $0      |
|  - Compute                                                   $0      |
+----------------------------------------------------------------------+
|  OUTPUT (deliverable)                                                |
|  - matches.csv          4,394 rows (header item_id_A,item_id_B)      |
|  - matches_audit.csv    232,434 audit rows (debug/audit, not deliv.) |
|  - SUBMISSION_CHECKLIST.md  reviewer-facing gates                    |
+----------------------------------------------------------------------+
```

---

## 12. Risks covered by the shipped pipeline

```
+-------------------------------------+------+------+-----------------------------+
| Risk                                | Prob | Imp. | Covered by                  |
+-------------------------------------+------+------+-----------------------------+
| Malformed A rows in output          | low  | HIGH | ^\d+$ + quarantine io.py    |
| Falling short of 4,000 matches      | low  | HIGH | 4,394 with floor 0.75/0.05  |
| Malformed output CSV                | low  | HIGH | validate_matches_csv        |
| Noisy brand inference               | med  | med  | Whitelist + brand_inferred  |
| PL retrieval pulls noise            | high | HIGH | Suppression in retrieval_   |
|                                     |      |      | text + group filter         |
| B duplicates -> arbitrary choice    | high | low  | _tiebreak_key deterministic |
| Too-strict matchable_group          | med  | HIGH | Conservative + dairy<->cheese|
| LLM rate-limited / down             | low  | low  | Off by default + fail-      |
|                                     |      |      | closed                      |
| LLM cost spikes                     | low  | low  | --llm-max-calls (default    |
|                                     |      |      | 1000) + per-group cap       |
| Embeddings needed for recall        | low  | low  | TF-IDF word+char already    |
|                                     |      |      | ranks both PDF at #1        |
| Phase 10B AI-assisted labels        | med  | med  | Tagged provisional; sampler |
|                                     |      |      | preserves human edits       |
+-------------------------------------+------+------+-----------------------------+
```

---

## 13. Real repo structure

```
BetterBasket-assignment/
|
|-- README.md                                <- interview-ready overview
|-- SUBMISSION_CHECKLIST.md                  <- reviewer-facing gates
|-- solution.md                              <- technical narrative
|-- solutioneasyexplained.md                 <- plain-English version
|-- app.md                                   <- this file (visual)
|-- CLAUDE.md                                <- agent-facing repo instructions
|-- requirements.txt                         <- scikit-learn, scipy, pytest, rank-bm25, pyyaml, openai
|-- pytest.ini                               <- testpaths = tests
|
|-- betterbasket_matcher/                    <- production package (10 modules)
|   |-- __init__.py
|   |-- io.py                                <- Stages 1-2
|   |-- normalize.py                         <- Stage 3
|   |-- taxonomy.py                          <- Stage 5
|   |-- scope.py                             <- Stage 4
|   |-- retrieval.py                         <- Stage 6 (TF-IDF word + char)
|   |-- rules.py                             <- Stage 7 (10 hard rules)
|   |-- scoring.py                           <- Stage 8 + tie-break
|   |-- pipeline.py                          <- end-to-end orchestration
|   |-- output.py                            <- Stage 10 (writers + validator)
|   |-- llm_arbiter.py                       <- optional Stage 11 (off by default)
|
|-- scripts/
|   |-- audit_data.py                        <- streaming CSV audit (no pandas)
|   |-- retrieval_probe.py                   <- TF-IDF / BM25 dry-run
|   |-- run_pipeline.py                      <- production entry point
|   |-- sample_eval.py                       <- Phase 9 / 10B eval sampler
|
|-- tests/                                    <- 412 tests
|   |-- fixtures/                             <- Phase 1 oracles (frozen, 17 case_ids)
|   |-- test_io.py
|   |-- test_normalize.py
|   |-- test_taxonomy_scope.py
|   |-- test_retrieval.py
|   |-- test_rules.py
|   |-- test_scoring.py
|   |-- test_pipeline_fixtures.py
|   |-- test_output_validation.py
|   |-- test_llm_arbiter.py                  <- FakeClient, no network, no credentials
|   |-- test_sample_eval.py
|   |-- test_fixtures_sanity.py
|   |-- test_import_contracts.py
|
|-- eval/
|   |-- manual_eval_phase10b.csv             <- stratified sample (AI-assisted labels)
|   |-- manual_eval_phase10b.md              <- human labeling workflow
|   |-- group_breakdown.md                   <- per-group precision (provisional)
|   |-- phase10b_calibration.md              <- threshold grid + cumulative precision
|
|-- docs/
|   |-- HANDOFF.md                           <- session log Phase 0 -> Phase 11
|   |-- dataset_audit.md                     <- canonical audit (source of truth)
|   |-- dataset_audit_stats.md               <- compact stats
|   |-- audit_stats.json                     <- machine-readable audit output
|   |-- algorithm_recommendation.md          <- canonical algorithm plan
|   |-- retrieval_probe_results.md           <- retrieval dry-run summary
|   |-- retrieval_probe_results.json         <- machine-readable probe output
|
|-- matches.csv                              <- primary deliverable (4,394 rows)
|-- matches_audit.csv                        <- debug/audit trace (gitignored, regenerable)
```

---

## 14. What to read by role

```
+----------------------------+------------------------+---------------------------------+
| If you are...              | Read first             | Dig into                        |
+----------------------------+------------------------+---------------------------------+
| Technical reviewer         | README.md              | docs/dataset_audit.md           |
|                            | SUBMISSION_CHECKLIST.md| docs/algorithm_recommendation.md|
|                            |                        | solution.md sec. 4              |
| Manager / non-technical    | README.md TL;DR        | app.md sections 1, 2, 9         |
| Whoever runs the code      | README.md "Reproducing"| scripts/run_pipeline.py         |
| Match auditor              | matches_audit.csv      | eval/phase10b_calibration.md    |
| Whoever maintains the      | CLAUDE.md              | betterbasket_matcher/*.py       |
| matcher                    | docs/HANDOFF.md        | tests/test_*.py                 |
+----------------------------+------------------------+---------------------------------+
```

---

**For full textual rationale of every decision** -> [solution.md](solution.md).
**For the raw data behind the plan** -> [`docs/dataset_audit.md`](docs/dataset_audit.md) and [`docs/algorithm_recommendation.md`](docs/algorithm_recommendation.md).
**For reproducible verification of the deliverable** -> [`SUBMISSION_CHECKLIST.md`](SUBMISSION_CHECKLIST.md).
