# AppEndToEnd.md — How the app works, told simply

> A step-by-step story of what your app does, from the moment you press Enter in the terminal to the moment the answer file appears. With no assumption that you know TF-IDF, retrieval, or scoring. When a technical word shows up, I first explain it with an everyday example and then I tell you where it lives in the code.
>
> The assignment PDF is in the repo (`[BetterBasket] Engineering Technical Assessment.pdf`). I read it cover to cover. At the end there is a table that cross-checks what the brief asks for and what your repo delivers.

---

## 1. The problem, in one counter-top sentence

Imagine your small business sells products similar to Walmart's, and you want to know whether your prices are competitive. To compare them, you need a program that says: *"this yogurt of yours is the same yogurt as that one at Walmart"*. The problem: Walmart hands you a list of **233,199 products** with names like `"Chobani Whole Milk Greek Yogurt Honey Blended 5.3 oz Cup"`, and you have your own list (Wegmans, **55,516 products**) with names like `"Chobani Greek Honey Blended Yogurt"`. **Nobody told you which is which.** Your app is the one that has to figure it out.

The output is a `matches.csv` file that says, row by row: *"Walmart product X = Wegmans product Y"*. The brief asks for at least 4,000 matches. Yours delivers **4,394**.

---

## 2. What the app does, in a single pass

The application, viewed from afar, does this:

1. **Reads** the two files.
2. **Drops the broken rows** (those with malformed IDs).
3. **Translates** every product into a common format (what we call *normalizing*).
4. **Discards categories** that are nonsensical to look up (we are not going to suggest toys to a grocery store that does not sell them).
5. **For each Walmart product, looks up the 50 most similar ones in Wegmans** using an internal search engine.
6. **Puts a bouncer** that rejects impossible pairs (very different size, alcohol vs juice, cat vs dog).
7. **Gives a 0-to-1 grade** to each pair that passed the bouncer.
8. **Keeps the top-graded one**, as long as the grade is high and beats the runner-up by some distance.
9. **Writes two files**: `matches.csv` with the clean list of pairs, and `matches_audit.csv` with traceability of what happened to **every** Walmart product (accepted/no candidates/rejected by the bouncer/score too low).

That's all. What follows is each step explained properly.

---

## 3. The user presses Enter

You start the app with a command in the terminal:

```bash
python3 scripts/run_pipeline.py \
  --a-csv ...grocery_store_a_items_final.csv \
  --b-csv ...grocery_store_b_items_final.csv \
  --min-score 0.75 --min-margin 0.05 --top-k 50
```

`scripts/run_pipeline.py` is the app's "front door". The only thing that file does, in broad strokes, is:

- Record your parameters: which file is A, which file is B, what is the minimum grade (`--min-score`), what is the minimum margin to second place (`--min-margin`), and how many candidates to ask the internal search engine for (`--top-k 50`).
- Call the big function: `run_pipeline(...)` (which lives in `betterbasket_matcher/pipeline.py`).
- When it finishes, **revalidate the output file** and report whether everything is OK.

There are two important numbers stored there: the IDs of the two PDF products.

```python
DEFAULT_REQUIRED_PAIRS = {
    "2197626": "92544",     # Chobani 5.3 oz
    "1929544": "105624",    # Tomato sauce 8 oz
}
```

Those are **the two pairs the brief uses as examples** and that the final file must respect. If the pipeline picks anything else, the command exits with an error.

---

## 4. Reading the CSVs without dying in the attempt

The first real step is done by the `betterbasket_matcher/io.py` module. Imagine you have an Excel file with 233,000 rows. If you load it all into memory at once, that can be heavy. That's why the app reads **one row at a time** (this is called *streaming*). It's like reading a book page by page instead of printing the whole thing and reading it in a single glance.

While reading, the app validates every row against two basic things:

1. **`item_id` must be a whole number** (digits only). If it says `" | Pack of 12"`, that's a broken row. The file's philosophy: *anything that is not strictly numeric goes to a separate drawer called `quarantined`*.
2. **The name cannot be empty.** No name, no product.

In the real dataset, Walmart has **5 broken rows** (a typical scraper annoyance, where columns "shift"). Those 5 rows are set aside and **never appear** in the final file. That is why the log says `quarantined_a=5`.

> *The functions that do this: `read_products` and `is_numeric_id` in `betterbasket_matcher/io.py`. It returns two lists: the good ones and the quarantined ones.*

The module also has two "translators" used later: one that knows how to read JSON even when it comes broken (`parse_json_dict`, returns an empty dict instead of blowing up) and another one that knows how to read "tags" in the two odd formats the dataset uses (`parse_tags`).

---

## 5. Translating each product to a common format

Here comes the first conceptually important part. Walmart and Wegmans store data differently. Two quick examples:

- **Size**. Walmart writes `"Chobani Whole Milk Greek Yogurt Honey Blended 5.3 oz Cup"` — the `5.3 oz` is inside the name. Wegmans has a separate column called `sizing_comp` with `{"size_user_friendly": "5.3 ounce"}`. We have to understand both formats.
- **Brand**. Walmart leaves the brand column **empty** in 46% of the rows. If the row is named `"Great Value Corn on the Cob"`, the brand is hidden in the name.

The `normalize.py` module does this work. It takes a raw row and turns it into an internal object (a `NormalizedProduct`) that is like a **shared form** with uniform fields:

| Field | What it stores |
|---|---|
| `brand_norm` | The brand in lowercase, no weird variants |
| `is_private_label` | Is it the store's own brand? (Great Value, Wegmans, Equate, etc.) |
| `brand_inferred` | Did we deduce the brand from the name because the column was empty? |
| `size` | Canonical size and unit: `(unit="oz", size=5.3, pack=1)` |
| `is_organic`, `storage_type`, `form`, `flavor` | Secondary attributes |
| `category_0/1/2` | The three hierarchical categories |
| `core_name` | The "clean" name, no brand, no size, no "(Pack of 12)" |
| `retrieval_text` | The text that goes to the internal search engine (key, we'll see) |

### How brand is inferred when the column is empty

There is a canonical list of Walmart private-label brands: `great value`, `marketside`, `equate`, `mainstays`, etc. When the `brand_raw` column is empty, the app looks at the product name and checks if it **starts** with any of those brands (careful: with whole-word match, not substring, so `"equator"` is not confused with `"equate"`).

If the brand starts the name, we keep that brand and mark `brand_inferred=True` so the rest of the program knows it was inferred and lowers the confidence accordingly.

> *Done by `infer_brand_from_name`, `_is_private_label_a`, and `_match_canonical_prefix` in `normalize.py`.*

### The most important trick of the whole system: **hide the brand when it's a private label**

This needs an analogy. Imagine you search `"Bagley cookies"` on a marketplace and, by mistake, you get `"Bagual cookies"` because the word is similar. Now imagine something worse: you search `"Great Value tomato sauce"` and the search engine returns `"Great Lakes Provolone Cheese"` as the first result, because `"Great"` is a very frequent word and the search engine gets confused. That actually happens — it is the famous "Great Value -> Great Lakes" example discovered by the testing phase (`docs/retrieval_probe_results.md`).

The fix: **when a product is private label, we erase the brand word from the text that goes to the search engine**. So instead of searching `"great value organic tomato sauce 8oz"`, we search `"organic tomato sauce 8oz"`. The search engine no longer gets distracted by the word `"great"`.

That is **the project's key sentence**. The code that implements it is three lines in `_build_retrieval_text`:

```python
if not is_private_label and brand_norm:
    parts.append(brand_norm)
```

If it's a private label, we do not add the brand to the search-engine text. Period.

> *Lives in `normalize.py`, function `_build_retrieval_text`.*

### How size is read (asymmetric on purpose)

- **Walmart**: size is in the name. The app extracts it with a detector that knows formats like `"5.3 oz"`, `"1 gallon"`, `"(12 Pack)"`, etc. If it finds `"(12 Pack)"` at the start, it stores it as `pack_count=12` and keeps looking for the per-unit size.
- **Wegmans**: size is in a separate JSON column (`sizing_comp.size_user_friendly`). It can also come as `"12 x 5.3 ounce"` (multipack).

The result is stored in a `SizeInfo(unit, unit_size, pack, total)`. Units are normalized to a canonical form: `"ounces"` becomes `"oz"`, `"fluid ounces"` becomes `"fl oz"`, etc. This is important because the bouncer later compares 16 oz with 1 lb and needs both to speak the same language.

> *Lives in `_parse_size_a` and `_parse_size_b` in `normalize.py`.*

---

## 6. The "scope" filter: things we don't even bother looking up

Walmart sells **toys, electronics, tools, clothing, jewelry**. Wegmans does not. There is no point looking up a competitor in Wegmans for a soccer ball or a screwdriver. Before spending time searching, the `scope.py` module discards those categories.

Specifically, it discards:

- 12 top-level categories: `toys`, `clothing`, `electronics`, `home improvement`, `books`, `cell phones`, `sports and outdoors`, `party and occasions`, `office supplies`, `auto and tires`, `arts crafts and sewing`, `jewelry`.
- Within `home`, it also discards decoration sub-categories (`home decor`, `picture frames`, `bedding`, `furniture`, `rugs`, `wall art`).
- **But it keeps** `home > kitchen and dining`, because that's where Wegmans-comparable items live (spatulas, glasses, etc.).

This drops approximately 48,000 Walmart rows before spending anything on search. The final log reflects it: `no_candidates=85901` (the bulk of which are out-of-scope; the rest are products without an assignable group).

> *Function `is_a_in_scope` in `scope.py`.*

---

## 7. The shared language: 20 groups for talking about the same things

Walmart organizes its products in a hierarchy: e.g., *Food -> Dairy & Eggs -> Cheese*. Wegmans has its own: *Dairy -> Cheese*. For the app to be able to say *"this is comparable to that"*, the two hierarchies are translated into a shared set of 20 groups: `pantry`, `snacks`, `dairy`, `cheese`, `frozen`, `produce`, `meat`, `seafood`, `bakery`, `prepared_foods`, `beverages`, `personal_care`, `household`, `baby`, `pets`, `health`, `beauty`, `kitchen_home`, `wine_beer_spirits`, etc.

This is called the `matchable_group`.

The compatibility rule (the `groups_compatible` function) is intentionally **conservative**: it only allows pairing products in the same group, with one exception: `dairy` and `cheese` are considered compatible (because Walmart puts cheeses under *Dairy* and Wegmans separates them). Any other crossing is forbidden. If a product cannot be assigned to any group, no search happens.

> *Lives in `taxonomy.py`. The key functions are `assign_matchable_group` and `groups_compatible`.*

---

## 8. The internal search engine: TF-IDF, in plain English

Now, for each in-scope Walmart product, the app needs to find the 50 most similar in Wegmans. For that it uses an internal Google-like search engine.

**What is TF-IDF, without getting heavy?** It's the classic formula for text search engines. It measures how similar one text is to another by counting which words appear and how rare they are. Frequent words (`"oz"`, `"of"`) weigh little; rare ones (`"chobani"`, `"provolone"`) weigh a lot. So when you throw it `"chobani honey blended yogurt 5.3oz"`, it returns the Wegmans products that share more of those "rare" words.

The app uses **two search engines in parallel**, one stacked on the other:

1. **Word search** (1- and 2-word n-grams). Captures things like `"organic tomato"` or `"greek yogurt"`.
2. **Character-chunk search** (3- to 5-character n-grams within words). Captures things like `"yogu"` or `"prov"`. This adds resistance to typos and variants (`"yogurt"` vs `"yoghurt"`).

The two similarities are summed at the end. That is the "retrieval score".

**Optimization**: the app does not search every Walmart product against the 55,516 Wegmans rows. First, it groups Wegmans products by `matchable_group` and, when a Walmart product of group `dairy` arrives, it only looks at Wegmans products in the `dairy` group (and `cheese`, by the compatibility rule). That is much faster.

The search engine returns the **top-50 candidates** ordered by similarity, with alphabetical ID tie-break so that the result is the same across runs.

> *Lives in `retrieval.py`, class `TfidfRetriever` with its methods `fit` (train the index) and `query` (search for an A item).*

---

## 9. The bouncer: the "hard rules"

Before grading a pair (Walmart_X, Wegmans_Y), the app passes the pair through a bouncer. If the bouncer rejects, that pair is discarded outright. The idea is that **there are differences nothing can compensate for**: a frozen product is not the same as a refrigerated one, an 8 oz sauce is not the same as a 29 oz one, cat food is not the same as dog food.

The bouncer runs 10 checks in order, and the first one that fails wins. That reason gets recorded in the audit file (so you can later open the file and see *"this pair was rejected because of size"*):

| # | Check | What it rejects |
|---|---|---|
| 1 | `alcohol_mismatch` | Wine vs juice. But careful: it has a blacklist not to confuse `ginger beer`, `wine vinegar`, `cooking wine`, `rum cake`, `beer cheese`, etc. |
| 2 | `group_mismatch` | Yogurt vs detergent. If the groups are not compatible, out. |
| 3 | `national_vs_private_label` | Coca-Cola (national brand) vs Wegmans Cola (private label). Those are not the same product. |
| 4 | `brand_mismatch` | Pepsi vs Coca-Cola: two known and different national brands. |
| 5 | `storage_mismatch` | Frozen vs refrigerated. |
| 6 | `form_mismatch` | Powder vs liquid, sliced vs shredded, ground vs whole bean. |
| 7 | `pet_type_mismatch` | Cat food vs dog food. Only inside the `pets` group. |
| 8 | `flavor_mismatch` | Vanilla vs chocolate. |
| 9 | `size_mismatch` | **The most important.** If both have a size in the same unit family (weight/volume/count) and the relative difference is greater than 8%, out. This is what prevents the 8 oz sauce from being confused with the 15 oz or the 29 oz. |
| 10 | `pack_mismatch` | 6-pack vs 12-pack (when the total-size difference was not caught by rule 9). |

An important decision: **`organic` is not a hard rule**. If one product is organic and the other is not, the bouncer lets it through; the score penalizes it later. This is to avoid losing candidates when organic information is incomplete.

Another key decision: **if an attribute is `None` (not detected), the bouncer treats it as "no conflict"**. The idea is not to reject for lack of data.

> *Lives in `rules.py`, function `evaluate_hard_rules`.*

---

## 10. The grade: how the winner is picked

The pairs that survive the bouncer get a 0-to-1 grade. The grade is the **weighted sum** of six sub-grades:

| Sub-grade | Weight | What it measures |
|---|---:|---|
| `core_name` | 35% | How similar the names are after removing brand, size, packs (this is the search-engine score, scaled to the 0-1 range) |
| `token_overlap` | 15% | What percentage of "interesting" words they share |
| `brand` | 15% | 1.0 if both are the same national brand; 0.85 if both are private labels (Great Value <-> Wegmans); 0.0 if two different national brands; intermediate values for cases with empty or inferred brand |
| `size_pack` | 20% | 1.0 if size matches within 1%; 0.7 within 5%; 0.0 if it differs more |
| `group` | 10% | 1.0 if in the same group; 0.7 for the special `dairy <-> cheese` case |
| `attributes` | 5% | How many secondary attributes coincide (storage, form, flavor, organic) |

Then the app **sorts the survivors by grade**, with a tidy tie-break: if two are tied on the grade, the one with exact size wins, then the one with the same pack count, then the one that shares more attributes, then the one with more metadata, and finally the smallest ID (this last one is purely for the run to be reproducible).

The first one in the list is the winner. But before accepting it, there are two more filters:

- **Grade floor** (`--min-score`, in production 0.75). If the winner's grade is below 0.75, no one is accepted. The row stays as `below_threshold` in the audit.
- **Margin to second place** (`--min-margin`, in production 0.05). If the winner beats the runner-up by less than 0.05, no one is accepted (the "the two are too close, I won't risk it" case). Unless there is a single survivor, in which case this filter does not apply.

Why both filters: **we want a winner that is good in absolute terms, **and** better than the runner-up**. That is the project's "precision-first" philosophy.

> *Lives in `scoring.py`, functions `score_pair` and `select_best`.*

---

## 11. The two PDF cases, step by step

These are the two examples the brief sets as references. **Both must appear correct** in `matches.csv`, otherwise the command exits with an error.

### 11.1 The easy case: Chobani 5.3 oz yogurt

```
A 2197626  Chobani Whole Milk Greek Yogurt Honey Blended 5.3 oz Cup   ->
B 92544    Chobani Greek Honey Blended Yogurt
```

This is the PDF's "exact match without UPC" case. The brand is national (Chobani), the size is identical (5.3 oz on both), and both are in the `dairy` group. The internal search engine pairs them as #1 effortlessly. The bouncer passes every check. The pair's final score is **0.7974** (with margin 0.0623 over the runner-up). It clears the 0.75/0.05 floors. **Accepted.**

### 11.2 The interesting case: 8 oz tomato sauce, not 15 nor 29

```
A 1929544  Great Value Organic Tomato Sauce 8 oz   ->
B 105624   Wegmans Organic Tomato Sauce 8 oz
```

Here is the acid test. **Wegmans has the same organic sauce in three sizes**: 8 oz (`105624`), 15 oz (`103620`), and 29 oz (`1086860`). All three are in the dataset. All three have an almost identical name. If the app gets it wrong, it is going to pick the 15 or 29 oz (which are larger and sometimes more popular).

Why does it pick the right one? Three reasons, in order:

1. **The brand-hiding trick**. `"Great Value Organic Tomato Sauce 8 oz"` is a private label. The app removes `"great value"` before sending it to the search engine. The text that goes to the search engine is `"organic tomato sauce 8oz pantry organic"`. That keeps the search engine from getting distracted by the word `"great"` and bringing back odd things.
2. **The bouncer's size rule (rule 9)**. Once the search engine returns the three Bs, the bouncer compares sizes:
   - **Walmart 8 oz vs Wegmans 8 oz**: relative difference = `|8 - 8| / 8 = 0%`. Pass.
   - **Walmart 8 oz vs Wegmans 15 oz**: relative difference = `|15 - 8| / 15 = 46.7%`. Rejected by `size_mismatch` (threshold: 8%).
   - **Walmart 8 oz vs Wegmans 29 oz**: relative difference = `|29 - 8| / 29 = 72.4%`. Rejected by `size_mismatch`.
3. **The final score**. The only survivor is the 8 oz one. The final score is **0.8643** with margin 0.1393. Much higher than the 0.75/0.05 floors. **Accepted.**

This is what makes the app pass the brief: understanding that **size is critical information**, not a decorative detail. And the project's tests block this explicitly: there is a test called `test_tomato_does_not_pick_wrong_sizes` that fails if the app were to pick the 15 oz or the 29 oz.

---

## 12. The "extra assistant" (LLM arbiter): what it is and why it is off

The brief mentions that BetterBasket gives you GPT-5 nano credentials to use if you want. The app has a module (`llm_arbiter.py`) that can use them, but it is **off by default**, and **was not used to generate the `matches.csv` you are submitting**.

### What does it do when it's on?

When you add `--use-llm-arbiter` to the command, after the entire deterministic pipeline, the app asks GPT about the doubtful cases. "Doubtful cases" are those whose grade fell between **0.65 and 0.75** (this is called the "gray window"). The idea is: the deterministic core has already discarded the obvious bad ones and accepted the obvious good ones; the doubtful cases sit in the middle. Those, which already passed the bouncer (i.e., they are not impossible), get GPT's opinion.

GPT receives a **structured** prompt (no improvisation freedom) with A's and B's data and must return a JSON with four fields:

```json
{
  "same_product_for_customer": true,
  "confidence": 0.85,
  "reason": "...",
  "blocking_issue": null
}
```

If GPT says `same_product_for_customer=true` and `confidence >= 0.60`, the app **rescues** the pair and adds it to the final file with a `source="llm_rescued"` stamp so it is clear where it came from.

### Why it's safe to leave off and breaks nothing when on

The guarantees are strong:

1. **It only sees cases that already passed the bouncer.** GPT never sees a pair that the size rule rejected. It cannot revive an impossible pair.
2. **It can only move from "below threshold" to "accepted".** It can never reject something the deterministic core accepted.
3. **If GPT replies anything weird** (malformed JSON, confidence > 1, NaN, network error, timeout), the app **does nothing**. The deterministic decision stands. This is called "fail-closed".
4. **It has a budget** (`--llm-max-calls`, default 1000). Once exhausted, unconsulted cases stay as `below_threshold`.
5. **There is a cache** at `.cache/llm_arbiter.jsonl`. If you re-run the same thing, it does not call GPT again: it reads from the cache. And the cache **never stores `api_key`**.
6. **The `api_key` is marked never to appear in logs or in `print()`.** The code says so explicitly with a Python trick (`field(repr=False)`).

The project log (`docs/HANDOFF.md`) records that **a single real test call** was made to GPT-5 nano on May 4, 2026, returning in 2.4 seconds with `confidence=0.95`. After that test it was confirmed that the integration works, but the final delivery was made **without** GPT, with the pure deterministic pipeline.

> *Lives in `betterbasket_matcher/llm_arbiter.py`. The integration with the rest is in `pipeline.py`, in the "Pass 2" block.*

---

## 13. The two output files

When done, the app writes two files:

### `matches.csv` — the deliverable

It has exactly two columns and a fixed header:

```
item_id_A,item_id_B
283870,1857099
283220,92472
...
```

4,394 rows (plus the header). Each row is an accepted match. **No duplicate A** (each Walmart product appears at most once).

### `matches_audit.csv` — the traceability

It has 9 columns:

```
item_id_A, item_id_B, score, retrieval_score, top1_top2_margin,
source, decision, reason, llm_confidence
```

There is **one row per Walmart product** that passed quarantine (the 5 broken ones do not appear). For each one we record what happened:

- `decision`: `accepted`, `below_threshold`, `rejected_by_rule`, or `no_candidates`.
- `reason`: the concrete reason. If rejected, it says why (`size_mismatch`, `group_mismatch`, etc.). If accepted, it says `ok`.
- `score` and `retrieval_score`: the two internal grades.
- `top1_top2_margin`: how much the winner beat the runner-up.
- `source`: `deterministic` or `llm_rescued`. In the current submission, all are `deterministic`.
- `llm_confidence`: empty in the current submission.

This file is what lets you, afterwards, open it in Excel and understand why the app decided each thing. It is the difference between a "black box" and an auditable tool.

### The final validation

After writing, the command reads `matches.csv` again and checks six things:

1. The header is exactly `item_id_A,item_id_B`.
2. All IDs are numeric.
3. The IDs exist in the source files (the 5 quarantined ones cannot sneak in).
4. There are no duplicate As.
5. **The two PDF pairs are correct.** If A `1929544` were to point to the 15 oz sauce, explicit error: *"required pair mismatch: expected 105624 but got 103620"*.
6. There are at least 4,000 rows.

If all is OK, the command prints `validation: ok=True row_count=4394 errors=0` and exits. Otherwise it prints the errors and exits with code 1.

> *Lives in `output.py`, functions `write_matches`, `write_matches_audit`, and `validate_matches_csv`.*

---

## 14. The numbers from the final run

From the shipped run log:

```
valid_a=233194           <- valid Walmart products (after dropping the 5 broken ones)
quarantined_a=5          <- the 5 broken ones
valid_b=55516            <- Wegmans products (all valid)
in_scope_a=181289        <- Walmart after dropping toys/electronics/etc.

accepted=4394            <- final matches in matches.csv
rejected_by_rule=24759   <- the bouncer rejected them
below_threshold=118140   <- passed the bouncer but the score did not reach 0.75
no_candidates=85901      <- nothing similar in Wegmans (mostly out-of-scope)

validation: ok=True row_count=4394 errors=0
```

If you sum `accepted + rejected_by_rule + below_threshold + no_candidates` you get 233,194 = `valid_a`. Each Walmart product appears **exactly once** in the audit, with a clear destination. A test verifies this (`test_one_audit_row_per_valid_a`).

---

## 15. Three closings

### 15.1 Interview summary (1-2 minutes)

> It's a product matcher between Walmart (233k items) and Wegmans (55k items). It is deterministic and precision-first: we prefer to be strict and deliver fewer reliable pairs rather than many with false positives. The user runs a command, the app reads the two CSVs in streaming, drops 5 rows with corrupt IDs, normalizes each product to a common format (canonical brand, private-label detection, asymmetric size parsing — Walmart from the name, Wegmans from a JSON column —, secondary attributes, and categories). Then it discards categories Wegmans does not sell (toys, electronics, decoration) and maps both hierarchies to 20 shared groups. It builds an internal Google-like search engine over the Wegmans products, with a critical trick: when the product is private label, it erases the brand from the search-engine text to avoid the "Great Value confused with Great Lakes" trap. For each in-scope Walmart item it searches 50 compatible candidates, runs them through a bouncer with 10 hard rules (alcohol, group, brand, storage, form, pet species, flavor, size with 8% tolerance, pack), and grades the survivors with a weighted score with six components. It accepts only if the grade is high in absolute terms (>=0.75) and beats the runner-up by some distance (>=0.05). That guarantees both PDF cases resolve correctly: the 5.3 oz Chobani yogurt is paired exactly and the 8 oz tomato sauce is not confused with the 15 nor 29 oz ones (the size rule rejects them). The result is 4,394 matches in `matches.csv`, plus a `matches_audit.csv` with one row per Walmart product explaining what happened. There is also an optional GPT-5 nano module to review doubtful cases in a "gray window" 0.65-0.75, but it is off by default and was not used for the deliverable; it has five layers of guarantees not to break anything. Tests: 412 in total, none of them touches the network.

### 15.2 If you want to read the code, start here

In this order, each file only depends on the previous ones. It's the best way not to get lost:

1. **`betterbasket_matcher/io.py`** (110 lines). How the data is read, how bad rows are quarantined, how JSON and odd tags are parsed. Test: `tests/test_io.py`.
2. **`betterbasket_matcher/normalize.py`** (572 lines). The densest one, but the most important. The critical part: the `_build_retrieval_text` function with the brand suppression for PL (3 lines, but they are the key sentence). Test: `tests/test_normalize.py`.
3. **`betterbasket_matcher/taxonomy.py`** (289 lines). How the Walmart and Wegmans hierarchies are translated to the shared 20-group language. Test: `tests/test_taxonomy_scope.py`.
4. **`betterbasket_matcher/scope.py`** (62 lines). The 12 excluded categories. Short.
5. **`betterbasket_matcher/retrieval.py`** (174 lines). The internal search engine. Test: `tests/test_retrieval.py`.
6. **`betterbasket_matcher/rules.py`** (255 lines). The bouncer with its 10 rules in order. Test: `tests/test_rules.py`.
7. **`betterbasket_matcher/scoring.py`** (365 lines). The 6 sub-grades and how winners are picked. Test: `tests/test_scoring.py`.
8. **`betterbasket_matcher/pipeline.py`** (293 lines). The orchestrator. Test: `tests/test_pipeline_fixtures.py` (includes both PDF cases).
9. **`betterbasket_matcher/output.py`** (179 lines). The two writers and the final validation. Test: `tests/test_output_validation.py`.
10. **`betterbasket_matcher/llm_arbiter.py`** (730 lines). The optional arbiter. Covered by `tests/test_llm_arbiter.py` with a FakeClient that simulates GPT (no network).
11. **`scripts/run_pipeline.py`** (169 lines). The CLI. The closer.

While reading, keep the `tests/fixtures/` folder open with the files `mini_a.csv`, `mini_b.csv`, and `expected_matches.csv`. They are 17 small cases (the two from the PDF plus 15 deliberately negative cases) that work as "exam questions" for the code. If you touch something and break those, the change is wrong.

### 15.3 Verification against the brief

I read the PDF (`[BetterBasket] Engineering Technical Assessment.pdf`) and cross-checked what each item asks against what your repo delivers:

| What the brief asks for | How the app delivers it | How the code verifies it |
|---|---|---|
| List of `(item_id_A, item_id_B)` matches in CSV | `matches.csv` with the exact header | The CLI revalidates the header after writing |
| Executable algorithm in Python | `python3 scripts/run_pipeline.py ...` with documented flags | The full command is in `README.md` |
| At least 4,000 matches | 4,394 rows | `wc -l matches.csv` -> 4395 (1 header + 4,394 data) |
| A unique Wegmans for each Walmart | The loop emits at most one B per A | Test `test_no_duplicate_item_id_a` |
| PDF case #1: Chobani 5.3 oz `2197626 -> 92544` | Accepted with score 0.7974, margin 0.0623 | The CLI enforces it via `DEFAULT_REQUIRED_PAIRS` |
| PDF case #2: 8 oz sauce `1929544 -> 105624` (not 15 nor 29) | Accepted with score 0.8643, margin 0.1393 | The CLI enforces it; the other two Bs are rejected by `size_mismatch` |
| "Customer would consider the same product" (shopper rule) | Hard rules + weighted score + cross-store private-label recognition (Great Value <-> Wegmans with score 0.85) | Built jointly by `rules.py` and `scoring.py` |
| Private brands can match different private brands | Yes: PL <-> PL is valid. PL <-> national is rejected by bouncer rule 3 | `rules.py`, rule `national_vs_private_label` |
| GPT-5 nano credentials available | Implemented in `llm_arbiter.py`. Off by default. Not used for the deliverable. There is a smoke test from 2026-05-04 confirming it works | `README.md` and `SUBMISSION_CHECKLIST.md` |
| Validatable output | Locked header, numeric IDs, no-dup, two PDF pairs correct, >=4,000 rows | `validate_matches_csv` in `output.py` |

**Things I did not directly verify in this session** (because you only asked me for reading, not running scripts):

- Behavior on a clean run from scratch against the real Walmart and Wegmans CSVs (those live in `~/Downloads/`, outside the repo). But the counters in the current files are consistent with each other: `matches_audit.csv` has 233,194 data rows = `valid_a_count`, and `matches.csv` has 4,394 = `accepted_count`. Those numbers match what is documented in `README.md` and `CLAUDE.md`.
- The actual measured precision over the 4,394 rows. The project has a 92-row labeled sample (in `eval/manual_eval_phase10b.csv`) that estimates 0.95 precision in the score>=0.75 range, **but those labels are provisional** (made by another AI, not a human). The project itself states this honestly in `eval/group_breakdown.md`: *"AI-assisted preliminary (Codex), not human-reviewed"*. The 0.75 threshold calibration over the 16,000+ candidates, however, is exact and does not depend on labels.

**No item in the PDF is left uncovered.** Both examples resolve correctly, the format is correct, the count clears the floor, and the algorithm is executable from a single command.

---

## Appendix — One small inconsistency

The GPT model name appears as `gpt-5.4-nano` in `CLAUDE.md` and `README.md`, but the CLI's `--help` describes it as `"GPT-5 nano"`. The actual deployment name is loaded from the credentials file (`creds["deployment_name"]`), so the "discrepancy" is purely cosmetic. The code does not assume it hardcoded anywhere.

Other than that, I did not find any case where a doc claims something the code does not deliver.
