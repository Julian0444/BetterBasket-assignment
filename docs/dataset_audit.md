# BetterBasket Dataset Audit

Inputs audited:

- `/Users/jirustaroure/Downloads/grocery_store_a_items_final.csv`
- `/Users/jirustaroure/Downloads/grocery_store_b_items_final.csv`
- `/Users/jirustaroure/Downloads/[BetterBasket] Engineering Technical Assessment.pdf`

Reproducible outputs:

- `scripts/audit_data.py`
- `scripts/retrieval_probe.py`
- `docs/audit_stats.json`
- `docs/dataset_audit_stats.md`
- `docs/retrieval_probe_results.json`
- `docs/retrieval_probe_results.md`

## Executive Summary

This is an entity-resolution problem over grocery catalogs, not a UPC join. Store A is much larger and noisier than Store B: 233,199 A rows versus 55,516 B rows, with five malformed A rows and many A categories that have no plausible Wegmans counterpart. Most useful attributes are embedded in `item_info`, `sizing_comp`, `tags`, `name`, and `brand_raw`; the pre-cleaned columns are empty decoys.

The final algorithm should be deterministic and precision-first: validate rows, normalize product attributes, block by category/brand/private-label rules, retrieve candidates with TF-IDF word + character n-grams, enforce hard rules for category, brand/private-label, size, pack, organic/form/storage, then score and select at most one B item per A item. GPT-5 nano should be reserved for gray-zone arbitration after deterministic retrieval, not used over all pairs.

## Row Counts And Data Quality

| Metric | Store A | Store B |
|---|---:|---:|
| File size | 158.86 MB | 64.25 MB |
| Parsed rows | 233,199 | 55,516 |
| Naive pair space | 12,946,275,684 | n/a |
| Non-numeric / malformed item IDs | 5 | 0 |
| Duplicate item IDs | 1 repeated malformed ID | 0 |

Store A has five shifted/malformed rows. Example malformed `item_id` values include ` | Pack of 6`, ` | Pack of 8`, ` | Pack of 12`, and `Acrylic Tortoise Thick Gripjaw Non Holdmetal Small Hair`. The pipeline should quarantine rows unless `item_id` matches `^\d+$`.

## Schemas

Store A columns:

`item_id`, `name`, `brand_raw`, `name_clean`, `description`, `category`, `department`, `url`, `item_type`, `item_info`, `tags`, `subcategory`, `is_private_label`, `sizing_comp`, `size_raw`, `datapoint_id`, `raw_data_id`, `created_at_utc`, `updated_at_utc`

Store B columns:

`item_id`, `name`, `brand_raw`, `name_clean`, `description`, `category`, `department`, `url`, `is_organic`, `item_info`, `tags`, `subcategory`, `sizing_comp`, `size_raw`, `datapoint_id`, `raw_data_id`, `created_at_utc`, `updated_at_utc`

## Empty And Decoy Columns

| Column | Store A blank/null rate | Store B blank/null rate | Usefulness |
|---|---:|---:|---|
| `name_clean` | 100.00% | 100.00% | Decoy |
| `category` | 100.00% | 100.00% | Decoy |
| `department` | 100.00% | 100.00% | Decoy |
| `subcategory` | 100.00% | 100.00% | Decoy |
| `size_raw` | 100.00% | 100.00% | Decoy |
| `item_type` | 100.00% | n/a | Decoy |
| `is_private_label` | 100.00% | n/a | Decoy |
| `is_organic` | n/a | 100.00% | Decoy |
| `tags` | 100.00% except malformed shifted rows | 58.96% blank | Useful only in B |
| `description` | 91.85% blank | 6.44% blank | Secondary text only |
| `brand_raw` | 45.87% blank/null | 9.99% blank/null | Useful but incomplete |

Useful matching columns are `item_id`, `name`, `brand_raw`, `url`, `item_info`, `sizing_comp`, and B `tags`. `description` can help only after stripping HTML, and A descriptions are too sparse to depend on.

## JSON Field Health

| JSON field | Store A | Store B |
|---|---:|---:|
| `item_info` valid dict rows | 219,414 | 55,516 |
| `item_info` blank rows | 13,785 | 0 |
| `sizing_comp` valid dict rows | 219,571 | 55,516 |
| `sizing_comp` blank rows | 13,623 | 0 |
| `sizing_comp` valid non-dict rows | 5 | 0 |

Useful `item_info` keys:

- A: `category_0`, `category_1`, `category_2`, `category_3`, `storage_type`, `packaging_description`, `ingredients`.
- B: `category_0`, `category_1`, `category_2`, `category_3`, `ingredients`, `planogram`, `ic_item_id`, `ic_product_id`, `ext_id`.

Useful `sizing_comp` keys:

- A: `size_user_friendly` exists structurally on 219,571 rows but is often null.
- B: `size_user_friendly` is heavily populated and should be the primary B size source.

The JSON parser should tolerate blanks, invalid JSON, and valid JSON values that are not dicts.

## UPC Availability

Store A has zero UPC-like fields or values. Store B has 681 `item_info.ic_item_id` values, all digit-like in the 8-14 digit range. Because A has no counterpart UPC/GTIN/EAN field, a UPC join is not usable for cross-store matching in this dataset.

Implication: exact national-brand matches must still be found by attributes when UPC is absent.

## Brand Coverage

| Metric | Store A | Store B |
|---|---:|---:|
| Blank/null `brand_raw` rows | 106,962 | 5,545 |
| Blank/null `brand_raw` rate | 45.87% | 9.99% |
| Food rows with blank brand in A | 46,947 / 73,478 | n/a |
| Food blank-brand rate in A | 63.89% | n/a |
| Unique normalized nonblank brands | 18,562 | 5,632 |
| Shared normalized brands | 2,441 | 2,441 |
| Branded rows whose brand exists in other store | 34,584 / 126,237 (27.40%) | 28,752 / 49,971 (57.54%) |

Top normalized B brand is `wegmans` with 8,050 rows. Store A top nonblank brands include many Walmart/general-merchandise brands such as `mainstays`, `wonder nation`, and `great value`; many grocery private-label A rows still have blank `brand_raw` and only expose the brand in `name`.

Brand implications:

- Brand is strong evidence when both sides have a national brand.
- Brand cannot be globally required because A brand coverage is poor.
- A needs conservative brand inference from name prefixes.
- Private-label brand mismatch is expected and must be handled separately.

## Private-Label Signals

Conservative private-label estimates from brand/name/tag rules:

| Metric | Store A | Store B |
|---|---:|---:|
| Estimated private-label rows | 22,363 | 8,498 |

Likely A private-label signals:

- `great value`: 5,692 rows, many with blank `brand_raw`.
- `mainstays`: 3,487 rows.
- `freshness guaranteed`: 1,367 rows.
- `equate`: 815 rows.
- `marketside`: 255 rows.
- `bettergoods`: 97 rows.

Likely B private-label signals:

- `brand_raw == Wegmans`: 8,050 normalized rows.
- B tags include `wegmans brand`: 6,820 rows.
- Other useful B tags include `organic`, `family pack`, `gluten free`, `vegan`, and `food you feel good about`.

Implication: national-brand to private-label should normally be rejected; private-label to private-label should be allowed if category, size, form, and product attributes align.

## Category Structure

Raw `category`, `department`, and `subcategory` columns are empty in both files. Category information must come from `item_info`.

Top Store A `category_0` values:

| Category | Rows |
|---|---:|
| Food | 73,478 |
| Health and Medicine | 20,501 |
| Personal Care | 15,715 |
| Household Essentials | 14,802 |
| Toys | 14,138 |
| Pets | 13,158 |
| Baby | 12,805 |
| Clothing | 11,990 |
| Home | 9,497 |
| Beauty | 5,243 |

Top Store B `category_0` values:

| Category | Rows |
|---|---:|
| More Departments | 19,586 |
| Grocery | 18,438 |
| Wine, Beer & Spirits | 5,040 |
| Frozen | 3,679 |
| Dairy | 2,951 |
| Produce & Floral | 1,739 |
| Bakery | 1,248 |
| Meat | 959 |
| Prepared Foods | 669 |
| Cheese | 645 |

Clear A exclusions with no meaningful B counterpart total at least 48,007 rows:

`Toys`, `Clothing`, `Home Improvement`, `Sports & Outdoors`, `Party & Occasions`, `Office Supplies`, `Auto & Tires`, `Electronics`, `Arts Crafts & Sewing`, `Jewelry`, `Books`, `Cell Phones`.

`Home` should not be globally included. `Home > Kitchen & Dining` can map to B `More Departments > Kitchen and Home`, but `Home > Decor`, frames, bedding, furniture, and wall art should be excluded.

Implication: build a shared `matchable_group` taxonomy rather than comparing raw category names.

## Size Coverage

| Metric | Store A | Store B |
|---|---:|---:|
| Size parsed from name | 138,739 (59.49%) | 5,204 (9.37%) |
| Food size parsed from name | 66,174 / 73,478 (90.06%) | n/a |
| `sizing_comp.size_user_friendly` nonblank | 39,765 (17.05%) | 53,090 (95.63%) |
| Size parsed from `size_user_friendly` | 24,621 (10.56%) | 53,024 (95.51%) |
| Both name and sizing parse | 22,104 | 5,059 |
| Conflicts when both parse | 4,412 | 4,112 |
| Pack-prefix names | 22,882 | 0 |
| Multipack patterns | 370 | 699 |
| Dimension-like strings | 3,071 | 700 |
| Descriptions with HTML tags | 7,410 | 6 |

Size source implications:

- A grocery size is usually in `name`.
- B size is usually in `sizing_comp.size_user_friendly`.
- `sizing_comp.size_user_friendly` in A is sparse and often non-grocery dimensions.
- Parse pack count separately from per-unit size.
- Normalize units into comparable families: weight, volume, count, dimensions.

Common size conflict examples:

- A names say `22 fl oz`, but A sizing sometimes says `22 oz`; that is a volume/weight ambiguity.
- Pack-prefixed A names such as `(12 pack) ...` can make the first size token the pack count rather than product size.
- B names may say `12 Pack`, while B sizing says `12 x 12 fl. oz.`
- Dimension strings such as `5 x 7`, `18 x 24`, and `12 x 12` mostly represent frames/home goods and should not be used as grocery size matches.

## B Duplicate Structure

Using normalized `(brand, name_without_size)` groups, B has:

- 2,796 duplicate-like groups.
- 6,320 rows inside those groups.

These are often not true duplicates; they are size/pack variants under the same product concept.

Examples:

- `Wegmans Organic Tomato Sauce`: 8 ounce, 15 ounce, 29 ounce.
- `Wegmans Tomato Sauce`: 8 ounce, 15 ounce, 29 ounce.
- `FIJI Natural Artesian Water`: 6 x 16.9 fl. oz., 1.5 liter, 1 liter, 24 x 16.9 fl. oz., 700 ml, and more.
- `Hershey's Candy Assortment`: 15 rows with sizes from about 13 oz to 64+ oz.
- `Mountain Dew Citrus Soda`: 2 liter, 6 x 7.5 fl. oz., 12 x 12 fl. oz., 24 x 12 fl. oz., and other packs.
- `Coca-Cola Cola`: single can, 6-pack, 12-pack, 24-pack, 2 liter, and other variants.

Implication: retrieval alone is insufficient. Size and pack disambiguation must happen before accepting a match.

## PDF Example Validation

Confirmed in the actual data:

| Example | Store | Item ID | Name | Size |
|---|---|---:|---|---|
| Chobani honey blended yogurt | A | 2197626 | Chobani Whole Milk Greek Yogurt Honey Blended 5.3 oz Cup | 5.3 oz |
| Chobani honey blended yogurt | B | 92544 | Chobani Greek Honey Blended Yogurt | 5.3 ounce |
| Private-label organic tomato sauce | A | 1929544 | Great Value Organic Tomato Sauce, 8 oz | 8 oz |
| Private-label organic tomato sauce | B | 105624 | Wegmans Organic Tomato Sauce | 8 ounce |

Nearby wrong tomato candidates in B:

- `103620`: Wegmans Organic Tomato Sauce, 15 ounce.
- `1086860`: Wegmans Organic Tomato Sauce, 29 ounce.

Nearby wrong A tomato candidates also exist:

- `1929545`: Great Value Organic Tomato Sauce, 15 oz Can.
- `2116415`: `(4 pack) Great Value Organic Tomato Sauce, 15 oz Can`.
- `1949064`: `(8 pack) Great Value Organic Tomato Sauce, 8 oz`.

Implication: the examples are real, and they prove why private-label compatibility and size/pack rules are mandatory.

## Retrieval Dry-Run

The retrieval probe indexed all 55,516 B rows and tested TF-IDF word + character n-grams and BM25. Two modes were compared:

- `brand_included`: brand tokens are included normally.
- `suppress_private_label`: store private-label tokens such as `Great Value` and `Wegmans` are removed for private-label items.

TF-IDF results:

| Probe | Mode | Expected B | Rank | Top candidate |
|---|---|---:|---:|---|
| Chobani 5.3 oz honey yogurt | Brand included | 92544 | 1 | 92544 |
| Chobani 5.3 oz honey yogurt | Private-label suppressed | 92544 | 1 | 92544 |
| Great Value Organic Tomato Sauce 8 oz | Brand included | 105624 | 1 | 105624 |
| Great Value Organic Tomato Sauce 8 oz | Private-label suppressed | 105624 | 1 | 105624 |

Important TF-IDF caveat: for tomato sauce, the top three candidates are the correct 8 oz item, then 15 oz, then 29 oz. Their scores are close enough that size hard rules are required.

BM25 results:

| Probe | Mode | Expected B | Rank | Failure mode |
|---|---|---:|---:|---|
| Chobani 5.3 oz honey yogurt | Brand included | 92544 | 1 | Good |
| Great Value Organic Tomato Sauce 8 oz | Brand included | 105624 | 5 | Top result was Colgate `Great Regular Flavor, 3 Value Pack` |
| Great Value Organic Tomato Sauce 8 oz | Private-label suppressed | 105624 | 1 | Fixed by suppressing private-label brand tokens |
| Great Value Provolone Cheese text probe | Brand included | n/a | n/a | Top results were `Great Lakes Provolone Cheese` |
| Great Value Provolone Cheese text probe | Private-label suppressed | n/a | n/a | Top results shifted to Wegmans/private-label cheese; Great Lakes dropped lower |

Private-label token noise:

- `Great Value Organic Tomato Sauce 8 oz`: no `Great Lakes` hits in TF-IDF top 50.
- `Great Value Provolone Deli Style Sliced Cheese 8 oz`: `Great Lakes` is a severe lexical trap when brand tokens are included; private-label suppression reduces but does not eliminate it.
- `Great Value Spring Water 24 x 16.9 fl oz`: one `Great Lakes` beer hit appears low in brand-included TF-IDF top 50; suppression removes it.

Implication: TF-IDF word + char n-grams is a good candidate generator, but private-label brand suppression and hard compatibility rules are necessary.

## Implementation Implications

1. Validate numeric IDs and quarantine malformed rows before matching.
2. Ignore empty/decoy columns and rebuild normalized fields.
3. Parse `item_info`, `sizing_comp`, and B `tags` with tolerant parsers.
4. Do not build a UPC-first solution for this dataset; A has no UPC-like values.
5. Infer A brand from name conservatively, and keep a `brand_inferred` flag.
6. Detect A and B private labels explicitly.
7. Build a shared taxonomy for grocery, dairy, frozen, produce, meat, seafood, bakery, prepared, beverages, pantry, personal care, household, baby, pets, health, beauty, kitchen/home.
8. Exclude clear non-counterpart A categories before retrieval.
9. Prefer A name size and B `sizing_comp.size_user_friendly`, while parsing pack count separately.
10. Use TF-IDF word + character retrieval, preferably per `matchable_group`.
11. Enforce size, category, brand/private-label, organic, form, storage, and pack rules before final scoring.
12. Use deterministic tie-breaking for B duplicates: compatible size first, richer metadata second, stable item ID third.
