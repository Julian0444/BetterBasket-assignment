# Solution explained simply — for someone just getting started

> "Assume nothing" version of [solution.md](solution.md). If you already have an ML, retrieval, or NLP background, go straight to `solution.md`. If not, start here.
>
> The technical sources of truth are `docs/dataset_audit.md` and `docs/algorithm_recommendation.md`. This document tells the same story in everyday language.

---

## 1. What are we being asked to do?

Imagine you own a small grocery store. You want to know: **"is my Chobani yogurt price competitive?"**. To figure that out, you need to compare your price against the price of the same yogurt at Walmart, Wegmans, Costco, etc.

The problem: **Walmart doesn't tell you which product is which**. They give you a list of 233,000 products with names like `"Chobani Whole Milk Greek Yogurt Honey Blended 5.3 oz Cup"`, and you have your own Wegmans list with `"Chobani Greek Honey Blended Yogurt"`. **You have to figure out which ones are the same product.**

That is what BetterBasket does: **match products across grocery stores** so its customers (other grocery stores) can compare prices.

### The concrete task

```
You are given:
  • List A: 233,199 Walmart products (with name, brand, description, etc.)
  • List B: 55,516 Wegmans products (same format)

You must return:
  • A matches.csv file that says "Walmart product X =
    Wegmans product Y"
  • At least 4,000 matches
  • Every match must be real: a customer should say
    "yes, those are essentially the same product"
```

### What we deliver

```
matches.csv  ->  4,394 rows (header item_id_A,item_id_B)
                 - clears the 4,000 floor
                 - both PDF examples resolve correctly:
                     A 2197626 -> B 92544       (Chobani 5.3 oz)
                     A 1929544 -> B 105624      (Wegmans Organic Tomato Sauce 8 oz)
                 - 0 duplicate item_id_A values
                 - all IDs are numeric and exist in the original CSVs

Generated with:
  python3 scripts/run_pipeline.py \
      --a-csv ... --b-csv ... \
      --min-score 0.75 --min-margin 0.05 --top-k 50

Test suite: 412 passing.
```

---

## 2. Why is this not trivial? (The 5 reasons)

### Reason 1: The volumes are enormous

If you wanted to compare **every product in A against every product in B**, you would do:

```
233,199 x 55,516 = 12,946,275,684 comparisons
```

That is **almost 13 billion**. Impossible to send all of them to an LLM or to a cross-encoder. You have to be smart: only compare products that have a real chance of matching.

### Reason 2: We do not have the "product DNI" (UPC)

In the real world, every product has a 12-digit code called a **UPC** — the barcode on the box. If two products share the same UPC, **they are literally the same product**, no doubt about it.

**The problem in this dataset**: Walmart does not bring UPC at all, and Wegmans only has a UPC-like `ic_item_id` in 681 / 55,516 rows (1.23%). Since Walmart has no counterpart, **a UPC join is not viable**.

### Reason 3: The data is dirty and ships with decoy columns

When we open the CSVs, we find that many columns that look useful come **100% empty**:

- `name_clean`, `category`, `department`, `subcategory`, `size_raw` — empty in both files.
- `is_private_label`, `item_type` (Walmart) and `is_organic` (Wegmans) — also empty.

**Conclusion**: we have to **rebuild** brand, category, size, "is private label", "is organic", etc. from the raw data (`name`, `brand_raw`, `item_info`, `sizing_comp`, `tags`).

### Reason 4: There are 5 broken rows in Walmart

Walmart ships **5 rows where `item_id` is not a number** — they come with shifted columns, e.g. an `item_id` that says `" | Pack of 12"` or `"Acrylic Tortoise Thick Gripjaw Non Holdmetal Small Hair"`. If we let those rows through, we end up with garbage tokens and, worse, with non-numeric `item_id` values in the final output.

**Conclusion**: the very first thing the pipeline does is validate `item_id` against the regex `^\d+$` and quarantine the 5 bad rows. **Before anything else.**

### Reason 5: Products do not always have an equivalent

Walmart sells toys, clothing, tools. Wegmans only sells food and grocery items. If A has a product like "Mainstays Picture Frame 5x7", **there is nothing in B to compare it against**. The audit identified at least **48,007 rows** of A in categories with no counterpart in B (Toys, Clothing, Home Improvement, Sports & Outdoors, Party & Occasions, Office Supplies, Auto & Tires, Electronics, Arts Crafts & Sewing, Jewelry, Books, Cell Phones).

**Conclusion**: we must filter **before** comparing. It's free work and leaves the matcher operating only on relevant A items.

---

## 3. The big idea: think the way a human would

If you, as a person, had to do this task by hand, how would you do it?

1. **Throw away the broken rows** (the 5 with non-numeric `item_id`).
2. **Clean up the names**. "(12 pack) Great Value Tomato Sauce, 8 oz" -> "Great Value Tomato Sauce 8 oz" (and store `pack=12` separately).
3. **Rebuild brand, category, size** from the raw data because the pre-cleaned columns are empty.
4. **Discard what does not apply** (toys, clothing, electronics): Wegmans does not sell that.
5. **Map categories to an intermediate taxonomy** (`dairy`, `pantry`, `frozen`, ...) so you can compare across Walmart and Wegmans.
6. **For each A product, find plausible B candidates** — not all 55,000, just the 50 most promising ones.
7. **Apply common-sense rules**: "same size?", "same physical form?", "compatible brand?". Any "no" eliminates the candidate.
8. **Score the survivors** and pick the best one.
9. **In doubtful cases, ask for help** from someone who knows.

That is exactly what our pipeline does. The difference is that, instead of doing it by hand (it would take you months), we do it with code and it finishes well under a minute. Step 9 ("ask for help") is implemented with **GPT-5.4 nano** (`betterbasket_matcher/llm_arbiter.py`), but it is **off by default**: the `matches.csv` we ship was generated **without** any LLM calls. The arbiter is available behind `--use-llm-arbiter` if the reviewer wants to see gray-zone rescues.

---

## 4. The 3 types of match

### Type 1: Identical with UPC (the easy case)

```
A: "Coca-Cola 12 oz" UPC=049000028911
B: "Coca-Cola 12 oz" UPC=049000028911

-> Same UPC = same product. CASE CLOSED.
```

**In this dataset: does NOT apply** (Walmart has no UPC). But conceptually it is the easiest case.

### Type 2: Identical without UPC (same product, different catalogs)

```
A (Walmart): "Chobani Whole Milk Greek Yogurt Honey Blended 5.3 oz Cup"
B (Wegmans): "Chobani Greek Honey Blended Yogurt"  +  size: 5.3 oz

-> Same brand (Chobani), same concept, same size. It's a match.
```

What confirms it: **exact brand** + **core name** + **size**.

### Type 3: Conceptual equivalent (private label)

**Private label** = "the store's own brand". Each chain has its own:

- Walmart: `Great Value`, `Marketside`, `Freshness Guaranteed`, `Equate`, `Mainstays`, `Bettergoods`, etc.
- Wegmans: `Wegmans` (the private brand has the same name as the chain) + tag `wegmans brand`.

```
A (Walmart): "Great Value Organic Tomato Sauce, 8 oz"   (Walmart PL)
B (Wegmans): "Wegmans Organic Tomato Sauce"  +  size 8 oz   (Wegmans PL)

-> Different brands, both PL, same concept, same size. It's a match.
```

This is the **hardest** case because there is no "hard" attribute that lines up. But **watch the size**: Wegmans also has the same sauce in 15 oz and 29 oz, and those are different products. The hard size rule is what prevents picking the wrong one.

---

## 5. The technical tools, in plain terms

### 5.1 TF-IDF — the word ranker (our main engine)

**What is it?** A classic technique that ranks documents by the words they share with the query, weighted by how **rare** those words are in the corpus. If "tomato" appears in many products, it weighs little; if "wegmans" appears rarely, it weighs a lot.

**The variant we use**: TF-IDF with two parallel vectorizers:

- **Word (1, 2)-grams**: weights single words and consecutive word pairs.
- **Char-wb (3, 5)-grams**: weights character sequences inside each word. This adds robustness against typos and orthographic variants (`organic` vs `organics`, `mountain dew` vs `mtn dew`).

We concatenate the two vectors and that becomes the "search space". For each A product we look up the 50 closest B products in vector space.

**Why TF-IDF and not another algorithm**: the repo's dry-run (`scripts/retrieval_probe.py`) tested both TF-IDF word+char and BM25 on the real 55,516 Wegmans rows. TF-IDF ranked both PDF examples at **#1**. BM25 with brand included sank `Great Value Organic Tomato Sauce 8 oz` to **rank 5** because the word "Great" inflated candidates such as `Colgate Great Regular Flavor` and `Great Lakes Provolone Cheese`. **TF-IDF wins**.

### 5.2 BM25 — optional, only as a diagnostic

**What is it?** A more sophisticated cousin of TF-IDF. Excellent for many cases, but with private label it falls into hard lexical traps (the words "Great" and "Value" wreck the ranking). We keep it as a secondary diagnostic ranker to detect when TF-IDF might be missing something, not as the primary engine.

### 5.3 Embeddings — discarded for this pipeline

**What are they?** Dense vectors produced by a language model. Similar products end up close together in a high-dimensional map; different products end up far apart.

**Why we do not use them in this pipeline**: they are strong for paraphrase ("Whole Milk" ~= "Vit D Milk"), but weaker at distinguishing sizes, packs, flavors, and numeric variants — which is **exactly what hurts us most** here (Wegmans tomato sauce 8/15/29 oz). The deterministic core with TF-IDF word+char already ranks both PDF examples at #1 and ships 4,394 matches above the 4,000 floor.

### 5.4 GPT-5.4 nano (LLM) — the optional arbiter, off by default

**What is it?** The smallest model in the GPT-5 family via API. It is **implemented** in `betterbasket_matcher/llm_arbiter.py` and wired to the pipeline via `--use-llm-arbiter`, but **off by default**. The shipped `matches.csv` was generated **without** any LLM calls.

When activated, it only steps in to resolve cases where the deterministic score landed in a gray zone (`below_threshold` with score `[0.65, 0.75)`). We send it a compact prompt and demand strict JSON:

```json
{
  "same_product_for_customer": true,
  "confidence": 0.0,
  "reason": "<short>",
  "blocking_issue": "<size|brand|category|...|null>"
}
```

**Hard guardrails**:

- The LLM **cannot override** a hard rule. Rows the hard rules rejected are never offered to the arbiter.
- The LLM **cannot change** an already-`accepted` row. Rows the deterministic score already cleared above 0.75 are never offered.
- The arbiter fail-closes: any exception, parse error, NaN, out-of-range value, or exhausted budget -> no rescue; the deterministic decision stands.
- `api_key` never appears in logs, prompts, or the .jsonl cache.

---

## 6. The 11 pipeline stages (step by step)

### Stage 1 — Ingest + validate

**Mission**: read the CSVs in streaming and drop the broken rows.

- `csv.DictReader` over A and B, no pandas (small footprint).
- **Validate `item_id` against `^\d+$`**. The 5 A rows with non-numeric `item_id` are isolated in `quarantine_a.csv` and **do not enter the pipeline**.
- Build the numeric sets `valid_ids_A` and `valid_ids_B`. The final output validator uses them to verify every `item_id` actually exists.

### Stage 2 — Parse JSON and tags

**Mission**: convert the columns with raw JSON into structured data.

- `item_info` and `sizing_comp` (in A and B): tolerant parser. If it parses but is not a dict, return `{}`.
- B `tags`: tolerant parser (JSON list -> Postgres array `{a,b,c}` -> comma split -> fallback []).
- A `tags`: empty in all rows except the 5 malformed ones, not used.

### Stage 3 — Normalize

**Mission**: convert each product into a "standardized record" with clean, comparable fields.

Each valid item is projected into an object with fields:

`item_id`, `source`, `name_raw`, `name_norm`, `core_name`, `brand_norm`, `brand_inferred`, `is_private_label`, `category_0..2`, `matchable_group`, `size_value`, `size_unit`, `size_family`, `pack_count`, `unit_size`, `total_size`, `is_organic`, `storage_type`, `form`, `flavor_tokens`, `retrieval_text`.

Rules:

- **Brand**:
  - A: `brand_raw` when populated. When blank (45.87% of A; **63.89% in Food**), we infer it from the prefix of `name` against a whitelist (Walmart PL + top B brands). We mark `brand_inferred=True`.
  - B: `brand_raw` directly (90% coverage).
- **Private label**: detected explicitly — A via whitelist (`great value`, `marketside`, ...), B via `brand_raw == Wegmans` or tag `wegmans brand`.
- **Categories**: parsed from `item_info.category_0..3` on both sides.
- **Size**: A first from `name`, fallback to `sizing_comp`; B first from `sizing_comp.size_user_friendly` (95.51% coverage), fallback to `name`.
- **Pack count**: parsed **separately** from per-unit size.
- **Organic / form / storage / flavor**: keyword extraction over `name`, complemented by B `tags`.

**Concrete example**:

```
INPUT (raw row from A):
  name: "(12 pack) Great Value Organic Tomato Sauce, 8 oz"
  brand_raw: ""  (empty)
  item_info: '{"category_0":"Food","category_1":"Pantry",...}'
  sizing_comp: '{"size_user_friendly":null,...}'

OUTPUT (normalized record):
  item_id: "1929544"
  brand_norm: "great value"
  brand_inferred: true
  is_private_label: true
  core_name: "organic tomato sauce"
  size_value: 8.0
  size_unit: "oz_weight"
  size_family: "weight"
  pack_count: 12
  unit_size: 8.0
  total_size: 96.0
  is_organic: true
  matchable_group: "pantry"
```

### Stage 4 — Filter out the irrelevant (scope filter)

**Mission**: discard A products that have no chance of having a match in B.

- **Keep** A in comparable categories: Food, Personal Care, Household Essentials, Health and Medicine, Baby, Pets, Beauty, selective parts of Home (Kitchen & Dining yes, Decor/Frames/Furniture/Bedding no).
- **Drop** A in `Toys`, `Clothing`, `Home Improvement`, `Sports & Outdoors`, `Party & Occasions`, `Office Supplies`, `Auto & Tires`, `Electronics`, `Arts Crafts & Sewing`, `Jewelry`, `Books`, `Cell Phones`. This removes at least **48,007 rows**.

### Stage 5 — Shared taxonomy (`matchable_group`)

**Mission**: since Walmart and Wegmans use different taxonomies, we map both to a **shared intermediate taxonomy** with groups such as:

`pantry`, `snacks`, `candy`, `beverages`, `dairy`, `cheese`, `frozen`, `produce`, `meat`, `seafood`, `bakery`, `prepared_foods`, `baby`, `pets`, `household`, `personal_care`, `health`, `beauty`, `kitchen_home`, `wine_beer_spirits`.

This is the "bridge" that lets us compare `category` between the two stores.

### Stage 6 — Generate candidates (TF-IDF word + char)

**Mission**: for each of the ~185,000 A products that survived the scope filter, find the 50 most promising B products.

- Build a **TF-IDF word `(1, 2)` + char-wb `(3, 5)`** index over B, ideally one per `matchable_group`.
- The **retrieval text is built differently** depending on brand type:
  - **National brand**: include strong brand. `"chobani greek honey blended yogurt 5.3 oz dairy yogurt"`.
  - **Private label**: **suppress** store-brand tokens (`great value`, `marketside`, `wegmans`, etc.) and rely on core name + size + group + organic. `"organic tomato sauce 8 oz pantry organic canned"`.
- For each A item: top-k = 50 B candidates.
- `k = 100` when A has `brand_blank` or a sparse category.

**Why the asymmetry**: the repo dry-run confirmed it. Without suppression, BM25 sends `Great Lakes Provolone Cheese` to top-1 when querying `Great Value Provolone Deli Style Sliced Cheese 8 oz`. With suppression, that noise drops dramatically. TF-IDF holds up better than BM25 without suppression for the PDF examples, but we still suppress to avoid relying on luck.

### Stage 7 — Hard rules (common sense)

**Mission**: discard candidates that clearly are not matches, no matter how similar the names look.

For each (A, B) pair we apply a checklist in cascade. Any "REJECT" eliminates the pair before scoring.

1. **Valid IDs** — both numeric and existing.
2. **Compatible `matchable_group`** — same group or a whitelisted adjacency (e.g. B `Cheese` <-> A `Dairy & Eggs`).
3. **Brand / private label compatibility**:
   - National <-> National: same exact brand.
   - Private-label <-> Private-label: cross-store allowed.
   - National <-> Private-label: reject (optional explicit exception for fresh/loose).
4. **Compatible size** — ratio in [0.95, 1.05] = OK; [0.5, 2.0] = decide later; outside = reject.
5. **Compatible pack** — single bottle vs 24-pack = reject.
6. **Organic mismatch** — if A is organic and a compatible organic B candidate exists, reject the non-organic.
7. **Form** — powder vs liquid, whole bean vs ground, sliced vs shredded, cat vs dog, adult vs baby.
8. **Storage** — frozen vs shelf-stable = reject; refrigerated vs fresh = unknown.
9. **Alcohol/non-alcohol** — reject mismatch.
10. **Flavor** — vanilla vs chocolate when both are detected = reject.

### Stage 8 — Deterministic score

**Mission**: assign a numeric score to the surviving pairs.

Suggested weights:

```
score = 0.35 x TF-IDF_name_similarity
      + 0.15 x token_overlap (post strip of brand and size)
      + 0.15 x brand_compatibility
      + 0.20 x size_and_pack_compatibility
      + 0.10 x category/group_compatibility
      + 0.05 x attributes (organic, form, flavor, storage, dietary)
```

- `brand_compatibility`: 1.0 same national; 0.85 PL<->PL cross-store; 0.4 when one side has blank/inferred; 0.0 when known nationals are incompatible.
- `size_and_pack_compatibility`: 1.0 same canonical size and same pack; partial credit in nearby ranges; 0.0 outside (and usually the hard rule already cut it).
- `category_compatibility`: 1.0 same group; lower for allowed adjacencies.
- Asymmetric penalties: one side **missing the field** is not equivalent to a mismatch.

**Acceptance**: per A, we pick the B candidate with the highest score. But we require `score >= min_score` and a margin `score_top1 - score_top2 >= margin`. Otherwise the row stays unmatched (or goes to the LLM arbiter if active).

### Stage 9 — Tie-break for B duplicates

B has 2,796 duplicate-like groups. When two B candidates survive with similar scores for the same A:

1. Exact size + pack match.
2. Form / flavor / organic / storage match.
3. Greater metadata richness (`ingredients`, `tags`, depth of `category`).
4. Stable `item_id` as a deterministic final tie-break.

Never pick between 8 / 15 / 29 oz on lexical score alone.

### Stage 10 — Output + audit + hard validation

We generate:

1. **`matches.csv`** (primary deliverable, two columns):
   ```csv
   item_id_A,item_id_B
   2197626,92544
   1929544,105624
   ```
2. **`matches_audit.csv`** (extra, for defending the work in interview): score, source, top1_top2_margin, llm_confidence, reason, A_name, B_name.
3. **`README.md`**: pipeline, metrics, threshold, sample eval.

**Before declaring the run valid, we run a smoke test**:

- Exact header `item_id_A,item_id_B`.
- Every `item_id_A` and `item_id_B` matches `^\d+$` and exists in the original numeric sets (quarantined rows **must not appear**).
- **No duplicate `item_id_A`** (one best match per A).
- At least 4,000 rows.
- The PDF examples resolve correctly: A `2197626` -> B `92544`, A `1929544` -> B `105624` (the **8 oz one**, not the 15 oz or 29 oz variants).

### Stage 11 — LLM as arbiter (optional, off by default)

**Mission**: use GPT-5.4 nano to resolve cases where the deterministic score landed in a specific gray band. **It was not used to produce the shipped `matches.csv`**; the arbiter is implemented and tested but opt-in via `--use-llm-arbiter`.

**When it kicks in (when active)**:

- Rows with `decision=below_threshold` whose score falls in `[0.65, 0.75)` (a fixed band defined in `pipeline.py`: `LLM_RESCUE_SCORE_LOW=0.65`, `LLM_RESCUE_SCORE_HIGH=0.75`).

**When it NEVER kicks in**:

- `rejected_by_rule` rows (the LLM cannot override hard rules; they are never offered).
- `accepted` rows (already cleared the threshold; never offered).
- `no_candidates` rows.
- `below_threshold` rows with score outside `[0.65, 0.75)`.

**Two-pass within a single run**:

1. **`collect`**: the main pipeline loop registers each in-band candidate. No network.
2. **`commit`** (after the loop): sort by score DESC, apply a per-group cap `max(50, max_calls // n_groups)`, and iterate. Cache hits are free. Cache misses only call the API while `api_calls_made < max_calls`.

**Technical guarantees**:

- **Local cache** at `.cache/llm_arbiter.jsonl` (gitignored). Re-runs serve cache for free.
- **Strict JSON parsing**: confidence outside `[0, 1]`, NaN, Infinity, missing field, or wrong type -> no rescue. No clamping.
- **Fail-closed**: API exception, deployment down, exhausted budget -> no rescue; the deterministic decision stands.
- **Hard rules win by construction**: the arbiter never sees rows the hard rules rejected.
- **Credentials**: loaded from `~/Downloads/openai_creds.yaml` (or `--llm-creds PATH`) without ever being logged. `api_key` lives in a `field(repr=False)` slot and never appears in `__repr__`, prompts, or cache.
- **Real smoke test executed** (1 call, 2026-05-04): `latency_s=2.40`, `confidence=0.95`, schema valid. Cost $~0. The full budget (1000 calls default) is available for the reviewer.

---

## 7. How do we know if it works? (evaluation)

There is no full ground truth, so we **estimate quality** with a stratified sample and we validate the output contract with automated tests.

### Phase 10B stratified sample (92 rows)

`scripts/sample_eval.py --mode phase10b` took 92 rows from an initial "recall-heavy" run (16,218 rows at `--min-score 0.55`) and distributed them across buckets:

- 6 score buckets (`score_0.55_0.60`, ..., `score_ge_0.80`), round-robin per `matchable_group` so pantry does not eat all the slots.
- Oversample of suspicious groups restricted to `score < 0.70`: `household`, `frozen`, `pantry`, `kitchen_home`, `seafood`.
- `private_label_cross_store` bucket: `A.is_private_label=True`, score in `[0.55, 0.80)`.
- The 2 PDF rows as `pdf_regression` (they do not count toward precision; they are output-contract gates).

The 92 rows were labeled in a **preliminary AI-assisted pass (Codex)**, not a human pass. That is why the numbers below are **provisional** until a human review, and they are explicitly tagged "provisional" in `eval/manual_eval_phase10b.md` and `eval/group_breakdown.md`. The sampler preserves human edits across re-runs.

### Cumulative precision (and why we chose `min_score=0.75`)

| Cumulative threshold | correct | wrong | partial | est_precision (provisional) |
|:---|:---:|:---:|:---:|:---:|
| score >= 0.65 | 36 | 8 | 8 | 0.692 |
| score >= 0.70 | 31 | 4 | 4 | 0.795 |
| **score >= 0.75 (shipped)** | **19** | **0** | **1** | **0.950** |
| score >= 0.80 | 7 | 0 | 1 | 0.875 |

The jump from 0.795 to 0.950 when raising the cut from 0.70 to 0.75 confirmed that the 0.55-0.75 band carried most of the false positives. Raising further to 0.80 already starts losing PDF rows (the Chobani example score=0.797 would not clear) and would fall below the 4,000 floor.

### Precision vs Recall — the decision

- **Precision**: of the matches I declared, what % are correct?
- **Recall**: of the real matches that exist, what % did I find?

There is a **trade-off**: raising threshold -> more precision, less recall. Lowering it -> less precision, more recall.

**Chosen strategy**: precision-first with a 0.75 floor. Keeps cumulative precision ~0.95 on the sample, clears 4,394 rows (over the 4,000 floor), and both PDF rows are accepted. In real pricing, a false match is worse than a missing match.

---

## 8. How much does it cost and how long does it take? (real numbers)

| Resource | Real value |
|---|---|
| Your laptop (CPU) | well under a minute end-to-end |
| OpenAI API | **$0** (the arbiter was not used to produce the output) |
| LLM smoke test (1 call) | 2.40 s, $~0, confidence=0.95 |
| RAM | ~1-2 GB peak (TF-IDF indexes over 55k B items) |
| Disk | ~250 MB (CSVs in Downloads, not copied into the repo) |
| Internet | ~0 (no network needed for the deterministic core) |

**Qualitative comparison with alternatives** (conceptual reading of approaches, not measured numbers):

| Approach | Reading |
|---|---|
| Fuzzy / Levenshtein only | Fast and free, but high false-positive risk on paraphrase and on size duplicates. Not viable as a sole signal. |
| BM25 with brand included (no rules) | The repo probe showed it: in private label it collapses on lexical traps (`Great Value` -> `Great Lakes`, `Great Regular Flavor`). Useful only as a secondary diagnostic. |
| **TF-IDF word + char + hard rules (what we ship)** | **The deterministic core**: the probe ranked both PDF examples at #1, the hard rules cover size/pack/PL traps, and cumulative precision at the 0.75 floor is 0.95 on the labeled sample. |
| + GPT-5.4 nano in the gray zone (optional, off by default) | Implemented and tested, but not used to produce the shipped `matches.csv`. Activatable with `--use-llm-arbiter`; full budget available for the reviewer. |
| LLM on every post-retrieval candidate | Conceptually possible, but cost and latency scale with the number of pairs; without hard rules it still does not solve the size traps. |
| LLM-first over the full Cartesian product | Infeasible: with almost 13 billion pairs, no reasonable cost or time budget covers it. |

---

## 9. The important decisions (and why we made them this way)

### Decision 1: precision-first (do not maximize quantity)

The PDF says "single closest match" — it does not say "every possible match". It asks for >= 4,000 as a **floor**. We defend **4,394 good ones** with provisional cumulative precision 0.95 on the labeled sample, instead of 16,218 with half of them garbage (the initial recall-heavy run was retired for exactly that reason). In real pricing, a false match is worse than a missing match.

### Decision 2: deterministic-first, not LLM-first

12,946,275,684 pairs makes it infeasible to compare them all. Even after reducing, the LLM **still needs** retrieval, normalization, and hard rules first to avoid getting obvious things wrong (8 oz vs 15 oz tomato sauce). Rules are free and precise; LLM contributes judgment where rules cannot reach.

### Decision 3: TF-IDF word + char as the retrieval engine (not BM25/FAISS/RRF)

We tested it in `scripts/retrieval_probe.py` over the real 55,516 rows:

- TF-IDF word + char ranks both PDF examples at **#1**.
- BM25 with brand included sinks `Great Value Organic Tomato Sauce 8 oz` to **rank 5** (top-1: `Colgate Fluoride Toothpaste, Great Regular Flavor, 3 Value Pack`).
- `Great Value Provolone` -> BM25 sends `Great Lakes Provolone Cheese` to top-1.

BM25 stayed as a diagnostic from the retrieval probe. Embeddings/FAISS are not part of the shipped pipeline and are not needed: the deterministic core with TF-IDF word+char already delivers 4,394 matches with provisional precision ~0.95 at the 0.75 floor.

### Decision 4: numeric `item_id` validation from the very first stage

Walmart ships 5 rows with non-numeric `item_id` (shifted columns). If we let them through, we contaminate retrieval, scoring, **and** the final output. The `^\d+$` rule in Stage 1 + quarantine closes that entry door.

### Decision 5: drop UPC

A has no UPC. B has 681 (1.23%). Not viable as a join key. The problem is 100% entity resolution.

### Decision 6: scope filter (drop Toys, Clothing, etc.)

Wegmans does not sell picture frames. Searching for matches there is 100% wasted work. Filtering them up front saves >= 48,007 A rows that have no plausible counterpart.

### Decision 7: GPT-5.4 nano selective, optional, off by default

The LLM is reserved for a specific gray band (`below_threshold` with score `[0.65, 0.75)`). It **cannot** accept pairs that the hard rules reject (incompatible size, national<->PL, incompatible group) because the arbiter never receives `rejected_by_rule` rows. Nor can it change rows already `accepted` above 0.75. This protects precision and makes the output reproducible even without an LLM. The shipped `matches.csv` was generated **without** calling the API.

---

## 10. Mini glossary

| Term | Meaning in one sentence |
|---|---|
| **UPC** | Universal Product Code (12 digits), the product's "DNI". |
| **Entity resolution** | Task of matching records from two sources that refer to the same real thing, without a join key. |
| **National brand** | Brand present at many retailers (Coca-Cola, Chobani). |
| **Private label** | A retailer's own brand (Great Value at Walmart, Wegmans-brand at Wegmans). |
| **TF-IDF** | Classic lexical ranking (term frequency x inverse document frequency). |
| **TF-IDF word + char n-grams** | Variant that combines word and character-sequence vectorizers; robust to typos. |
| **BM25** | Another lexical ranking, cousin of TF-IDF. Useful as a diagnostic, not as the primary engine on this dataset. |
| **Embeddings** | Vectors that represent text with semantic similarity. Not used in this pipeline. |
| **FAISS** | Library for fast vector search. Not used in this pipeline. |
| **GPT-5.4 nano** | OpenAI model provided by BetterBasket, implemented as an optional arbiter (off by default). The shipped `matches.csv` was generated without LLM. |
| **`matchable_group`** | Shared intermediate taxonomy between Walmart and Wegmans for comparing categories. |
| **Hard rule** | Rule that rejects a candidate no matter what (regardless of score). |
| **Threshold** | Minimum score required to accept a match. |
| **Margin** | Minimum gap between top-1 and top-2 required to accept a match. |
| **Precision** | Of what I delivered, what % is correct? |
| **Recall** | Of what exists, what % did I find? |
| **Quarantine** | Setting aside malformed rows (the 5 with non-numeric `item_id`) before processing. |
| **Audit trail** | Record of "how each decision was reached" (`matches_audit.csv`). |

---

## 11. If you want to dig deeper

- **`solution.md`** — full technical version with deep rationale, formulas, and discarded alternatives with reasons.
- **`app.md`** — visual view with ASCII diagrams.
- **`docs/dataset_audit.md`** — the actual audit of the CSVs, the source of truth for the numbers.
- **`docs/algorithm_recommendation.md`** — the algorithm recipe, the source of truth for the plan.
- **`scripts/audit_data.py`** and **`scripts/retrieval_probe.py`** — the reproducible scripts that produced the two sources above.

---

**If after reading this something is still unclear**, send me which part and I'll rewrite it. The idea is that anyone reading this file can understand what the pipeline does without an ML background.
