# BetterBasket Algorithm Recommendation

## Final Verdict

Use a deterministic, precision-first hybrid entity-resolution stack:

1. Streaming CSV ingest and row validation.
2. Attribute normalization from `name`, `brand_raw`, `item_info`, `sizing_comp`, and B `tags`.
3. Scope filtering and shared category taxonomy.
4. Candidate retrieval with TF-IDF word + character n-grams over Store B.
5. Hard compatibility rules before acceptance.
6. Weighted deterministic scoring and margin checks.
7. Optional GPT-5 nano arbitration only for gray-zone candidate sets.

Do not implement an LLM-first, UPC-first, or fuzzy-only matcher for this dataset.

## Why Not LLM-First

The naive pair space is 233,199 A rows x 55,516 B rows = 12.95 billion pairs. Calling an LLM over even a tiny fraction of all pairs would be slow, expensive, and hard to audit. The LLM would also still need normalized fields, candidate retrieval, and hard size/category/brand rules to avoid obvious mistakes like matching 8 oz tomato sauce to 15 oz tomato sauce.

GPT-5 nano should be a late-stage arbiter, not the retrieval engine.

Recommended GPT-5 nano use:

- Only after deterministic retrieval has produced a small candidate set, such as top 2-5.
- Only for borderline scores or small score margins.
- Only after hard rejections have already removed impossible pairs.
- Ask for structured JSON: `same_product_for_customer`, `confidence`, `reason`, and `blocking_issue`.
- Cache decisions by normalized attribute tuple.
- Never allow the model to override hard rules for incompatible size, incompatible category, or national-brand/private-label conflict.

## Why Not Fuzzy-Only

Fuzzy string similarity cannot represent the matching contract:

- B has many same-name variants where size is the only critical difference, such as Wegmans Organic Tomato Sauce in 8, 15, and 29 oz.
- Private-label exact brand mismatch is expected: Great Value can match Wegmans, but Coca-Cola should not match Wegmans Cola.
- Private-label brand tokens create lexical traps. In the dry run, BM25 with brand tokens ranked Colgate `Great Regular Flavor, 3 Value Pack` above the correct tomato sauce, and `Great Value Provolone` pulled `Great Lakes Provolone Cheese` to the top.
- Grocery form, pack, organic, storage, and category constraints matter as much as name similarity.

Fuzzy matching is useful as one feature, not as the algorithm.

## Retrieval Choice

Use TF-IDF word + character n-grams as the first candidate retrieval layer.

Evidence from `scripts/retrieval_probe.py`:

| Probe | Retrieval mode | Expected B | Rank |
|---|---|---:|---:|
| Chobani 5.3 oz honey blended yogurt | TF-IDF brand included | 92544 | 1 |
| Chobani 5.3 oz honey blended yogurt | TF-IDF private-label suppressed | 92544 | 1 |
| Great Value Organic Tomato Sauce 8 oz | TF-IDF brand included | 105624 | 1 |
| Great Value Organic Tomato Sauce 8 oz | TF-IDF private-label suppressed | 105624 | 1 |
| Great Value Organic Tomato Sauce 8 oz | BM25 brand included | 105624 | 5 |
| Great Value Organic Tomato Sauce 8 oz | BM25 private-label suppressed | 105624 | 1 |

Recommendation:

- Primary retrieval: TF-IDF word n-grams `(1, 2)` plus char-wb n-grams `(3, 5)`.
- Build indexes per broad `matchable_group` when possible; otherwise retrieve globally and filter by group.
- Use private-label-suppressed retrieval text for private-label A/B items.
- Keep BM25 as optional diagnostic or secondary retrieval, not the primary scorer unless private-label suppression and category filters are applied first.
- Treat embeddings as optional later-stage enrichment, not part of the first deliverable. Embeddings may help semantic private-label/fresh items, but they are weaker for exact sizes, packs, flavors, and numeric variants.

## Recommended Pipeline

### 1. Ingest And Validate

Read both CSVs with `csv.DictReader`. Validate:

- `item_id` must be numeric.
- `name` must be nonblank.
- JSON fields are parsed into dicts or empty dicts.
- Output IDs must exist in the validated source ID sets.

Quarantine the five malformed A rows before normalization.

### 2. Normalize Products

Create one normalized record per valid item:

- `item_id`
- `source`
- `name_raw`
- `name_norm`
- `core_name`
- `brand_norm`
- `brand_inferred`
- `is_private_label`
- `category_0`, `category_1`, `category_2`
- `matchable_group`
- `size_value`, `size_unit`, `size_family`
- `pack_count`, `unit_size`, `total_size`
- `is_organic`
- `storage_type`
- `form`
- `flavor_tokens`
- `retrieval_text`

Source preference:

- A brand: `brand_raw`, then conservative prefix inference from `name`.
- B brand: `brand_raw`.
- A size: parse from `name` first, then `sizing_comp.size_user_friendly`.
- B size: parse from `sizing_comp.size_user_friendly` first, then `name`.
- B organic/private-label/dietary signals: parse `tags`.
- Categories: parse `item_info`; ignore raw `category`, `department`, and `subcategory`.

### 3. Scope Filter

Exclude A categories that clearly do not have B counterparts:

`Toys`, `Clothing`, `Home Improvement`, `Sports & Outdoors`, `Party & Occasions`, `Office Supplies`, `Auto & Tires`, `Electronics`, `Arts Crafts & Sewing`, `Jewelry`, `Books`, `Cell Phones`.

Treat `Home` selectively:

- Keep kitchen/home utility products that map to B `Kitchen and Home`.
- Exclude decor, frames, bedding, furniture, rugs, and wall art.

### 4. Taxonomy Blocking

Map both stores into shared groups, for example:

`pantry`, `snacks`, `candy`, `beverages`, `dairy`, `cheese`, `frozen`, `produce`, `meat`, `seafood`, `bakery`, `prepared_foods`, `baby`, `pets`, `household`, `personal_care`, `health`, `beauty`, `kitchen_home`, `wine_beer_spirits`.

Retrieve within compatible groups. Allow narrow cross-group links only when the stores split taxonomy differently, such as B `Cheese` versus A `Food > Dairy & Eggs`.

### 5. Candidate Retrieval

Build a B-side TF-IDF index using:

- normalized brand when national brand;
- private-label-suppressed name for private labels;
- core product name;
- category tokens;
- normalized size tokens;
- optional form/flavor tokens.

For each scoped A item, retrieve top `k`, initially `k=50`. Keep a larger `k` for sparse categories or blank-brand A rows, such as `k=100`, but avoid global all-pair scoring.

### 6. Hard Rules Before Scoring

Reject before scoring when any hard incompatibility is present:

- Invalid IDs.
- Incompatible `matchable_group`.
- National-brand mismatch when both brands are known and neither side is private label.
- National-brand to private-label mismatch, except explicitly allowed fresh/loose cases.
- Comparable size conflict when both sides have reliable size.
- Pack conflict when pack is material, such as single bottle versus 24-pack.
- Organic mismatch when an organic compatible candidate exists.
- Storage conflict for clear cases: frozen versus shelf-stable versus refrigerated.
- Product form conflict: powder versus liquid, whole bean versus ground, sliced versus shredded, cat versus dog, adult versus baby, etc.
- Alcohol/non-alcohol mismatch.

These rules matter more than a lexical score.

### 7. Deterministic Scoring

Proposed score, after hard rules:

| Component | Weight |
|---|---:|
| Core name TF-IDF/char similarity | 0.35 |
| Token-level name overlap after removing brand and size | 0.15 |
| Brand compatibility | 0.15 |
| Size and pack compatibility | 0.20 |
| Category/group compatibility | 0.10 |
| Attribute compatibility: organic, form, flavor, storage, dietary | 0.05 |

Suggested component behavior:

- Brand compatibility: 1.0 for same national brand, 0.85 for private-label to private-label, 0.4 for one blank/inferred brand, 0.0 for incompatible known national brands.
- Size compatibility: 1.0 for same comparable size, partial credit for close numeric equivalents, 0.0 or rejection for clear mismatch.
- Category compatibility: 1.0 within same narrow mapped group, lower for known adjacent groups.
- Attribute penalties should be asymmetric when one side lacks the field; missing should not equal mismatch.

Acceptance rule:

- Select the highest scoring B candidate per A.
- Require minimum score and a margin over the second candidate.
- For first deliverable, prefer high threshold and high margin, then tune downward until at least 4,000 defensible matches are reached.

### 8. Duplicate Tie-Breaking

B duplicate groups are common. Tie-break only after compatibility/scoring:

1. Exact comparable size and pack.
2. Same form/flavor/organic/storage.
3. Higher metadata completeness: nonblank size, ingredients, tags, category depth.
4. Stable deterministic `item_id` order.

Never choose between 8, 15, and 29 oz variants by lexical score alone.

## Precision-First Versus Recall-First

Use precision-first for the assessment deliverable.

Reasoning:

- Required output is at least 4,000 matches, while the complete set is more than 10,000.
- A high-confidence subset is easier to defend in an interview than a larger noisy file.
- The dataset contains many traps: missing A brands, private-label brand mismatch, same-name size variants, multipacks, malformed rows, and non-overlapping categories.

Implementation target:

- First produce 4,000-7,000 high-confidence matches with strong audit columns.
- Then expand recall by lowering thresholds within safe categories and using GPT-5 nano only on gray-zone candidates.

## Optional GPT-5 Nano Layer

Use GPT-5 nano after deterministic scoring for cases like:

- Private-label pantry/fresh items where name similarity is high but brand differs.
- Blank or inferred A brand with multiple plausible B candidates.
- Fresh/loose produce or meat where size is absent or sold by weight.
- Score margin is small, but candidates differ in wording rather than hard attributes.

Prompt contents should be compact and structured:

- A normalized product attributes.
- Top B candidate attributes.
- Hard-rule status.
- Ask whether a shopper would consider them essentially the same product.
- Require JSON only.

Do not send full descriptions unless needed, and do not include credentials or secrets in logs.

## Risks And Mitigations

| Risk | Mitigation |
|---|---|
| Malformed A rows | Numeric ID validation and quarantine |
| Empty pre-cleaned columns | Rebuild normalization from raw name/JSON/tags |
| Missing A brand | Conservative brand inference plus `brand_inferred` flag |
| Private-label brand noise | Detect private labels and suppress store-brand tokens in retrieval |
| Great Value -> Great Lakes trap | Category filters, private-label suppression, national-brand/private-label rejection |
| Same-name B size variants | Hard size and pack compatibility before acceptance |
| Multipacks | Parse pack count separately from unit size and total size |
| Organic mismatch | Use name plus B tags; prefer organic when A is organic |
| Category mismatch | Shared taxonomy and clear A exclusion list |
| LLM nondeterminism | Deterministic first pass, structured output, caching, hard-rule guardrails |
| Recall pressure | Stage thresholds and keep an audit CSV for manual review |

## What To Implement First

1. `audit_data.py` style robust ingestion and parsing helpers.
2. Normalization module for brand, private label, categories, tags, sizes, packs, organic/form/storage.
3. Scope/category taxonomy and exclusion filter.
4. TF-IDF retrieval over B with private-label-suppressed mode.
5. Hard rules and deterministic scorer.
6. `matches.csv` writer plus `matches_audit.csv` for confidence/debugging.

## What Should Be Optional

- GPT-5 nano arbitration for gray-zone pairs.
- BM25 as a secondary retrieval probe.
- Embeddings for semantic recall after the deterministic system clears 4,000 matches.
- Description/ingredients reranking where available.
- Manual review notebook/report for threshold calibration.

## What Should Be Avoided

- All-pairs comparison.
- LLM calls over broad candidate spaces.
- UPC-first design.
- Raw fuzzy string matching as the final decision.
- Requiring brand equality globally.
- Ignoring size/pack when names match.
- Treating raw `category`, `department`, `subcategory`, `name_clean`, or `size_raw` as populated.
