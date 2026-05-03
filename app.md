# Pipeline Visual — BetterBasket Product Matching

> Vista escaneable del pipeline. Para fundamentación detallada de cada decisión, ver [solution.md](solution.md). Las fuentes de verdad técnicas son `docs/dataset_audit.md` y `docs/algorithm_recommendation.md`.

---

## 1. Pipeline completo (11 etapas)

```
┌──── A: 233.199 items (Walmart) ────┐         ┌──── B: 55.516 items (Wegmans) ────┐
│                                    │         │                                    │
└──────┬─────────────────────────────┘         └──────┬─────────────────────────────┘
       │                                              │
       ▼                                              ▼
╔══════════════════════════════════════════════════════════════════════════════╗
║ Stage 1 — INGEST + VALIDATE                                                  ║
║  • csv.DictReader streaming (sin pandas, footprint chico)                    ║
║  • Validar item_id con ^\d+$ y CUARENTENAR las 5 filas malformadas de A      ║
║    (item_id no numérico + columnas corridas, e.g. " | Pack of 12")           ║
║  • Validar name no blank                                                     ║
║  • Construir valid_ids_A y valid_ids_B (sets numéricos)                      ║
╚══════════════════════════════════════════════════════════════════════════════╝
                                       │
                                       ▼
╔══════════════════════════════════════════════════════════════════════════════╗
║ Stage 2 — PARSE JSON / TAGS                                                  ║
║  • item_info y sizing_comp: parser tolerante (json → ast → {} si no es dict) ║
║  • B tags: parser tolerante (JSON list → array Postgres → split por coma)    ║
║  • A tags: 100% blank (excepto las 5 malformadas), no se usa                 ║
║  • description: strip HTML cuando aplica                                     ║
╚══════════════════════════════════════════════════════════════════════════════╝
                                       │
                                       ▼
╔══════════════════════════════════════════════════════════════════════════════╗
║ Stage 3 — NORMALIZE                                                          ║
║  • Brand: A usa brand_raw o infiere por whitelist (brand_inferred=True),     ║
║         B usa brand_raw directo                                              ║
║  • Private label:                                                            ║
║    – A: brand ∈ {great value, marketside, freshness guaranteed, equate,      ║
║                  mainstays, bettergoods, wonder nation, sam s choice, ...}   ║
║    – B: brand_raw == 'wegmans' OR tag 'wegmans brand' / 'wegmans_brand'      ║
║  • Categorías desde item_info.category_0..3 (las raw category/dept/subcat    ║
║    están 100% blank en ambos archivos)                                       ║
║  • Size asimétrico: A primero del name, B primero de sizing_comp             ║
║  • Pack count parseado SEPARADO del per-unit size                            ║
║  • organic / form / storage / flavor por keyword + B tags                    ║
╚══════════════════════════════════════════════════════════════════════════════╝
                                       │
                                       ▼
╔══════════════════════════════════════════════════════════════════════════════╗
║ Stage 4 — SCOPE FILTER                                                       ║
║  Excluir A.cat0 ∈ {Toys, Clothing, Home Improvement, Sports & Outdoors,      ║
║                    Party & Occasions, Office Supplies, Auto & Tires,         ║
║                    Electronics, Arts Crafts & Sewing, Jewelry, Books,        ║
║                    Cell Phones} → ≥ 48.007 filas fuera                       ║
║  Conservar selectivamente Home (Kitchen & Dining sí; Decor/Frames/etc. no)   ║
║  → A reducido de 233.194 a ≤ ~185.000                                        ║
╚══════════════════════════════════════════════════════════════════════════════╝
                                       │
                                       ▼
╔══════════════════════════════════════════════════════════════════════════════╗
║ Stage 5 — TAXONOMY BLOCKING (matchable_group compartido)                     ║
║  Mapear ambas tiendas a grupos comunes:                                      ║
║    pantry, snacks, candy, beverages, dairy, cheese, frozen, produce,         ║
║    meat, seafood, bakery, prepared_foods, baby, pets, household,             ║
║    personal_care, health, beauty, kitchen_home, wine_beer_spirits            ║
║  Permitir cross-group narrow links donde la taxonomía se parte distinto      ║
║  (e.g. B Cheese ↔ A Food > Dairy & Eggs)                                     ║
╚══════════════════════════════════════════════════════════════════════════════╝
                                       │
                                       ▼
╔══════════════════════════════════════════════════════════════════════════════╗
║ Stage 6 — CANDIDATE RETRIEVAL (TF-IDF word + char, sobre B)                  ║
║  Vectorizers en paralelo:                                                    ║
║    • word ngram_range=(1, 2)                                                 ║
║    • char-wb ngram_range=(3, 5)                                              ║
║  L2-normalize y hstack para representación combinada                         ║
║                                                                              ║
║  Texto de retrieval ASIMÉTRICO según tipo de marca:                          ║
║    • Marca nacional → "{brand} {core_name} {size} {group} {cat1} {cat2}"     ║
║    • Private label  → "{core_name} {size} {group} {organic} {cat1} {cat2}"   ║
║      (suprime tokens great value / marketside / wegmans para evitar ruido    ║
║       tipo "Great Value" → "Great Lakes")                                    ║
║                                                                              ║
║  Índices por matchable_group cuando es posible. Top-k = 50 (k=100 si A       ║
║  tiene brand_blank o categoría sparse). Output ~5–10M pares.                 ║
║                                                                              ║
║  BM25 = diagnóstico opcional. Embeddings/FAISS = capa de recall diferida.    ║
╚══════════════════════════════════════════════════════════════════════════════╝
                                       │
                                       ▼
╔══════════════════════════════════════════════════════════════════════════════╗
║ Stage 7 — HARD RULES (antes del scoring)                                     ║
║  Cualquier REJECT elimina el candidato.                                      ║
║   R1. IDs válidos (^\d+$ y existen en sets)                                  ║
║   R2. matchable_group compatible o adyacente whitelist                       ║
║   R3. Brand / private-label compatibility:                                   ║
║       • National ↔ National: misma marca exacta                              ║
║       • Private-label ↔ Private-label: cross-store OK                        ║
║       • National ↔ Private-label: NUNCA (excepción narrow para fresh/loose)  ║
║   R4. Size compatible (ratio en [0.95, 1.05] = OK; [0.5, 2.0] = unknown)     ║
║   R5. Pack compatible (single bottle vs 24-pack = REJECT)                    ║
║   R6. Organic mismatch (si A organic y existe B organic compatible, reject   ║
║       el non-organic)                                                        ║
║   R7. Form (powder vs liquid, whole bean vs ground, sliced vs shredded,      ║
║       cat vs dog, adult vs baby)                                             ║
║   R8. Storage (frozen vs shelf-stable = REJECT; refrigerated vs fresh = ?)   ║
║   R9. Alcohol/non-alcohol mismatch                                           ║
║   R10. Flavor mismatch (vanilla vs chocolate cuando ambos detectados)        ║
║  Reduce a ~200–500k pares.                                                   ║
╚══════════════════════════════════════════════════════════════════════════════╝
                                       │
                                       ▼
╔══════════════════════════════════════════════════════════════════════════════╗
║ Stage 8 — DETERMINISTIC SCORE                                                ║
║  score = 0.35 · core_name_TF-IDF                                             ║
║        + 0.15 · token_overlap (post strip de brand y size)                   ║
║        + 0.15 · brand_compatibility                                          ║
║        + 0.20 · size_and_pack_compatibility                                  ║
║        + 0.10 · category_group_compatibility                                 ║
║        + 0.05 · attributes (organic, form, flavor, storage, dietary)         ║
║                                                                              ║
║  Acceptance: top-1 con score ≥ min_score y top1−top2 ≥ margin.               ║
║  Pesos a calibrar con muestra manual de 50 pares.                            ║
╚══════════════════════════════════════════════════════════════════════════════╝
                                       │
                                       ▼
╔══════════════════════════════════════════════════════════════════════════════╗
║ Stage 9 — TIE-BREAK B DUPLICATES (2.796 grupos, 6.320 filas)                 ║
║   1. Match exacto de size + pack                                             ║
║   2. Match de form / flavor / organic / storage                              ║
║   3. Mayor metadata richness (ingredients, tags, profundidad de category)    ║
║   4. item_id estable como tie-break determinístico                           ║
║  Nunca elegir entre 8/15/29 oz por puntaje léxico solo.                      ║
╚══════════════════════════════════════════════════════════════════════════════╝
                                       │
                                       ▼
╔══════════════════════════════════════════════════════════════════════════════╗
║ Stage 10 — OUTPUT + AUDIT + VALIDACIÓN DURA                                  ║
║  matches.csv          → (item_id_A, item_id_B)   ← deliverable principal     ║
║  matches_audit.csv    → + score, source, top1_top2_margin, llm_confidence,   ║
║                          reason, A_name, B_name                              ║
║  Validación dura del CSV (smoke test):                                       ║
║    • header exacto item_id_A,item_id_B                                       ║
║    • todos los IDs son numéricos (^\d+$) y existen en sets originales A/B    ║
║    • SIN duplicados de item_id_A (un best match por A)                       ║
║    • ≥ 4.000 filas                                                           ║
║    • ejemplos del PDF resuelven OK (8 oz, no 15/29 oz)                       ║
║  README.md            → pipeline, métricas, threshold elegido, sample eval   ║
║  Mini-eval manual sobre 50 pares random → estimar precision real             ║
╚══════════════════════════════════════════════════════════════════════════════╝
                                       │
                                       ▼
╔══════════════════════════════════════════════════════════════════════════════╗
║ Stage 11 — OPTIONAL GPT-5 NANO ARBITER (zona gris)                           ║
║  Solo se invoca cuando el deterministic score no es concluyente:             ║
║    • top-1 ∈ [0.55, 0.85] o margin < 0.05                                    ║
║    • A con brand_blank/brand_inferred y múltiples B plausibles               ║
║    • private-label cross-store con score moderado                            ║
║    • fresh/loose sin size confiable                                          ║
║                                                                              ║
║  Prompt compacto + JSON estructurado:                                        ║
║    {best_match_id, same_product_for_customer, confidence, reason,            ║
║     blocking_issue}                                                          ║
║                                                                              ║
║  Async + Semaphore, cache por hash de atributos normalizados, retry          ║
║  exponencial. El LLM NO puede override hard rules.                           ║
║  Estimado si activo: ≤ 5.000 calls, < $2 USD, ~15-25 min.                    ║
╚══════════════════════════════════════════════════════════════════════════════╝
```

---

## 2. Funnel de reducción de volumen

El núcleo del pipeline es **reducir 12.946.275.684 pares posibles a 4.000–7.000 matches finales** sin perder los buenos.

```
┌──────────────────────────────────────────────────────────────────────┐
│  Producto cartesiano A × B                                           │
│  12.946.275.684 pares  (imposible evaluar todo con LLM o cross-enc)  │
└──────────────────────────────┬───────────────────────────────────────┘
                               │  Stage 1 — quarantine 5 filas A
                               ▼
┌──────────────────────────────────────────────────────────────────────┐
│  Universo post-validación                                            │
│  233.194 × 55.516                                                    │
└──────────────────────────────┬───────────────────────────────────────┘
                               │  Stage 4 — scope filter (≥ 48.007 fuera)
                               ▼
┌──────────────────────────────────────────────────────────────────────┐
│  Universo post-scope                                                 │
│  ≤ ~185.000 × 55.516                                                 │
└──────────────────────────────┬───────────────────────────────────────┘
                               │  Stage 5–6 — taxonomy blocking + TF-IDF k=50
                               ▼
┌──────────────────────────────────────────────────────────────────────┐
│  Candidatos generados                                                │
│  ~5–10M pares          (~ factor 1.000–2.500×)                       │
└──────────────────────────────┬───────────────────────────────────────┘
                               │  Stage 7 — hard rules
                               ▼
┌──────────────────────────────────────────────────────────────────────┐
│  Sobrevivientes a reglas duras                                       │
│  ~200–500k pares                                                     │
└──────────────────────────────┬───────────────────────────────────────┘
                               │  Stage 8 — score determinístico
                               ▼
┌──────────────────────────────────────────────────────────────────────┐
│  Pares con score viable                                              │
│  ~30–80k pares                                                       │
└──────────────────────────────┬───────────────────────────────────────┘
                               │  Stage 11 — LLM solo en zona gris
                               ▼
┌──────────────────────────────────────────────────────────────────────┐
│  Llamadas LLM (opcional)                                             │
│  ≤ 5.000 calls                                                       │
└──────────────────────────────┬───────────────────────────────────────┘
                               │  Stage 10 — output final
                               ▼
┌──────────────────────────────────────────────────────────────────────┐
│  matches.csv                                                         │
│  4.000–7.000 matches  (precision-first)                              │
└──────────────────────────────────────────────────────────────────────┘
```

**Lección clave**: el LLM, si está activo, ve menos del **0.0001 %** del producto cartesiano original. El deterministic core hace el ~95–100% del trabajo.

---

## 3. Los 3 tipos de match

```
╔═════════════════════════════════════════════════════════════════════╗
║ TIPO 1 — Exact match con UPC                                        ║
║                                                                     ║
║   A: UPC=...  ────[ JOIN directo ]────►  B: UPC=...                 ║
║                                                                     ║
║   Confianza: 99%   |   En este dataset: ❌ no aplica                ║
║                                                                     ║
║   Razón: A tiene 0 campos UPC-like, B tiene 681 ic_item_id (1.23%). ║
║   Sin contraparte en A, el join no es viable.                       ║
╚═════════════════════════════════════════════════════════════════════╝

╔═════════════════════════════════════════════════════════════════════╗
║ TIPO 2 — Exact por atributos (mismo brand, mismo size, sin UPC)     ║
║                                                                     ║
║   A: "Chobani Whole Milk Greek Honey Blended 5.3 oz"                ║
║                          │                                          ║
║                          ▼  brand exacto + size exacto + name sim   ║
║   B: "Chobani Greek Honey Blended Yogurt"  +  size 5.3 oz           ║
║                                                                     ║
║   Confianza: 90-95%   |   % esperado del output: ~50%               ║
╚═════════════════════════════════════════════════════════════════════╝

╔═════════════════════════════════════════════════════════════════════╗
║ TIPO 3 — Non-exact private label (cross-store)                      ║
║                                                                     ║
║   A: "Great Value Organic Tomato Sauce, 8 oz"  (Walmart PL)         ║
║                          │                                          ║
║                          ▼  PL compatibility + reglas duras + LLM?  ║
║   B: "Wegmans Organic Tomato Sauce"  +  size 8 oz   (Wegmans PL)    ║
║                                                                     ║
║   Confianza: 70-85%   |   % esperado del output: ~50%               ║
║                                                                     ║
║   Trampa: B también tiene 15 oz (103620) y 29 oz (1086860).         ║
║   Hard rule de size es la que evita el match equivocado.            ║
╚═════════════════════════════════════════════════════════════════════╝
```

---

## 4. Hallazgos críticos del audit (resumen visual)

```
┌─────────────────────────────────────────────────────────────────────┐
│  🔴  Filas malformadas en A (item_id no numérico)                   │
│      5 filas con columnas corridas, e.g. " | Pack of 12"            │
│      → Validar item_id con ^\d+$ y CUARENTENAR antes de matching.   │
├─────────────────────────────────────────────────────────────────────┤
│  🔴  UPC no es viable                                               │
│      A: 0 campos UPC-like   ·   B: 681 ic_item_id (1.23%)           │
│      → No hay clave de join exacta. Problema 100% basado en texto.  │
├─────────────────────────────────────────────────────────────────────┤
│  🔴  Columnas señuelo (todas 100% vacías)                           │
│      name_clean, category, department, subcategory, size_raw,       │
│      item_type, is_private_label, is_organic                        │
│      → Hay que reconstruir TODO desde name/brand_raw/JSON/tags.     │
├─────────────────────────────────────────────────────────────────────┤
│  🟠  Brand crisis en A                                              │
│      45.87% blank globalmente   ·   63.89% blank en Food            │
│      Shared normalized brands: 2.441                                │
│      → Inferir brand desde whitelist PL + primer token.             │
├─────────────────────────────────────────────────────────────────────┤
│  🟢  Size location asimétrica                                       │
│      A: regex sobre name = 90.06% en Food                           │
│      B: sizing_comp.size_user_friendly = 95.51% parseable           │
│      → Estrategia asimétrica de extracción.                         │
├─────────────────────────────────────────────────────────────────────┤
│  🟠  Duplicados en B (size es decisivo)                             │
│      2.796 grupos (brand, name) con >1 fila, 6.320 filas total      │
│      Wegmans Organic Tomato Sauce → 8 oz, 15 oz, 29 oz              │
│      → Size es regla DURA, no solo blanda.                          │
├─────────────────────────────────────────────────────────────────────┤
│  🟡  Pack noise en A                                                │
│      22.882 filas con prefijo "(N pack)" o "Pack of N"              │
│      → Limpiar antes de tokenizar; pack_count guardado aparte.      │
├─────────────────────────────────────────────────────────────────────┤
│  🔴  Taxonomías incompatibles                                       │
│      A: Walmart (Food/Toys/Pets/...)                                │
│      B: Wegmans (Grocery/Frozen/Dairy/Bakery/Cheese/Seafood/...)    │
│      → Construir taxonomía intermedia "matchable_group".            │
├─────────────────────────────────────────────────────────────────────┤
│  🔴  Categorías de A sin contraparte (≥ 48.007 filas)               │
│      Toys, Clothing, Home Improvement, Sports & Outdoors,           │
│      Party & Occasions, Office Supplies, Auto & Tires,              │
│      Electronics, Arts Crafts & Sewing, Jewelry, Books, Cell Phones │
│      → Scope filter en Stage 4.                                     │
├─────────────────────────────────────────────────────────────────────┤
│  🟠  Retrieval con marca propia retrieva ruido                      │
│      "Great Value" → BM25 retrieva "Colgate Great Regular Flavor"   │
│        (rank 1) y "Great Lakes Provolone Cheese"                    │
│      → TF-IDF word+char + suppression de PL en query.               │
├─────────────────────────────────────────────────────────────────────┤
│  🟢  Tags B aportan señal valiosa                                   │
│      ~41% de B con tags poblado: wegmans brand, organic, gluten     │
│      free, family pack, vegan, food you feel good about             │
│      → Parser tolerante (JSON → array Postgres → split).            │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 5. Stage 6 — Retrieval con TF-IDF word + char

```
                 A item: "Great Value Organic Tomato Sauce 8 oz" (private label)
                 → query brand-suppressed: "organic tomato sauce 8 oz pantry"
                                       │
                                       ▼
              ┌──────────────────────────────────────────┐
              │  TF-IDF combinado                        │
              │   • word ngram_range=(1, 2)              │
              │   • char-wb ngram_range=(3, 5)           │
              │   • L2-normalize, hstack                 │
              └──────────────────────────────────────────┘
                                       │
                                       ▼
              ┌──────────────────────────────────────────┐
              │  Top-50 (filtrado por matchable_group)   │
              │  1. Wegmans Organic Tomato Sauce 8 oz ⭐ │
              │  2. Wegmans Organic Tomato Sauce 15 oz   │
              │  3. Wegmans Organic Tomato Sauce 29 oz   │
              │  4. Wegmans Tomato Sauce 8 oz            │
              │  5. ...                                  │
              └──────────────────────────────────────────┘
                                       │
                                       ▼
              ┌──────────────────────────────────────────┐
              │  Stage 7 — hard rule de size             │
              │  → Sobrevive solo el de 8 oz             │
              └──────────────────────────────────────────┘
```

**Por qué TF-IDF word + char y no BM25/FAISS/RRF**:

| Probe | Ranker | Modo | Expected B | Rank | Top-1 |
|---|---|---|---:|---:|---|
| Chobani 5.3 oz honey yogurt | TF-IDF | brand_included | 92544 | 1 | 92544 ✅ |
| Chobani 5.3 oz honey yogurt | TF-IDF | suppress PL | 92544 | 1 | 92544 ✅ |
| Great Value Organic Tomato 8 oz | TF-IDF | brand_included | 105624 | 1 | 105624 ✅ |
| Great Value Organic Tomato 8 oz | TF-IDF | suppress PL | 105624 | 1 | 105624 ✅ |
| Great Value Organic Tomato 8 oz | BM25 | brand_included | 105624 | **5** | Colgate Great Regular Flavor ❌ |
| Great Value Organic Tomato 8 oz | BM25 | suppress PL | 105624 | 1 | 105624 ✅ |
| Great Value Provolone (text) | BM25 | brand_included | n/a | n/a | Great Lakes Provolone Cheese ❌ |

Probado en `scripts/retrieval_probe.py` sobre las 55.516 filas reales de Wegmans.

- TF-IDF word + char gana en private label sin necesidad de fusion ni de un segundo ranker.
- BM25 con marca incluida tiene trampas léxicas duras (`Great` / `Value` inflan candidatos no relacionados).
- Embeddings semánticos quedan diferidos: son más débiles para sizes/packs/flavors numéricos que es donde más nos hieren los duplicados.

---

## 6. Stage 7 — Hard rules en cascada

Cada regla puede emitir `ACCEPT`, `REJECT` o `UNKNOWN`. Si **alguna** retorna `REJECT`, el candidato se descarta.

```
                      Candidato (A, B) entra
                              │
                              ▼
        ┌──────────────────────────────────────────┐
        │  R1 — IDs válidos (^\d+$ + en sets)?     │
        └──────┬─────────────────────────┬─────────┘
         REJECT│                         │ACCEPT
               ▼                         ▼
        ┌──────────────┐    ┌──────────────────────────────────┐
        │ ❌ DESCARTADO │    │  R2 — matchable_group compatible?│
        └──────────────┘    └──────┬─────────────────┬─────────┘
                             REJECT│                 │ACCEPT/UNK
                                   ▼                 ▼
                            ┌──────────────┐  ┌─────────────────────────┐
                            │ ❌ DESCARTADO │  │  R3 — Brand / PL?       │
                            └──────────────┘  │   NB↔NB same brand      │
                                              │   PL↔PL cross OK        │
                                              │   NB↔PL nunca           │
                                              └────┬───────────┬────────┘
                                              REJECT│           │OK
                                                    ▼           ▼
                                          ┌────────────┐  ┌──────────────────┐
                                          │ ❌ DESCART. │  │ R4 — Size?       │
                                          └────────────┘  │ ratio [0.95,1.05]│
                                                          └────┬───────┬─────┘
                                                          REJECT│       │OK
                                                                ▼       ▼
                                                      ┌────────────┐  ┌──────────────┐
                                                      │ ❌ DESCART. │  │ R5..R10      │
                                                      └────────────┘  │ pack/organic/│
                                                                      │ form/storage/│
                                                                      │ alcohol/flav │
                                                                      └──┬──────┬────┘
                                                                  REJECT │      │OK
                                                                         ▼      ▼
                                                              ┌────────────┐  ┌──────────────────┐
                                                              │ ❌ DESCART. │  │ ✅ Pasa a Stage 8│
                                                              └────────────┘  └──────────────────┘
```

**Las más críticas**:

- R3 (brand / PL compatibility). Sin ella, "Coca-Cola Classic" matchea "Wegmans Cola" por similitud lexical — match falso para pricing.
- R4 (size). Sin ella, B duplicados (8/15/29 oz) ganan por puro lexical y entregamos el pote equivocado.

---

## 7. Stage 8 — Composición del score

```
┌────────────────────────────────────────────────────────────────────┐
│  score = 0.35 · core_name_TF-IDF                                   │
│        + 0.15 · token_overlap (post strip de brand y size)         │
│        + 0.15 · brand_compatibility                                │
│        + 0.20 · size_and_pack_compatibility                        │
│        + 0.10 · category_group_compatibility                       │
│        + 0.05 · attributes (organic, form, flavor, storage, ...)   │
└────────────────────────────────────────────────────────────────────┘

Pesos visualizados:

  core_name_TF-IDF      ███████████████████████████████████   35%
  size_and_pack         ████████████████████                  20%
  token_overlap         ███████████████                       15%
  brand_compatibility   ███████████████                       15%
  category_group        ██████████                            10%
  attributes            █████                                  5%
```

**Acceptance rule**:

```
  Por A, elegir el candidato B con mayor score
  Requerir:
    • score ≥ min_score
    • score_top1 − score_top2 ≥ margin
  Si no, sin match (o pasa a árbitro LLM si está activo)
```

**Ejemplos de cálculo**:

```
┌─────────────────────────────────────────────────────────────────────┐
│ Par 1 — Chobani Honey 5.3oz ↔ Chobani Greek Honey Blended 5.3oz     │
│   core_name_TF-IDF 0.92  +  token_overlap 0.85  +                   │
│   brand 1.0 (NB exacto)  +  size+pack 1.0  +  group 1.0  +          │
│   attributes 0.6                                                    │
│   = 0.93  →  AUTO-ACCEPT (score alto + margin amplio)               │
├─────────────────────────────────────────────────────────────────────┤
│ Par 2 — Great Value Organic Tomato 8oz ↔ Wegmans Organic Tomato 8oz │
│   core_name_TF-IDF 0.86  +  token_overlap 0.78  +                   │
│   brand 0.85 (PL↔PL)  +  size+pack 1.0  +  group 1.0  +             │
│   attributes 0.8 (ambos organic)                                    │
│   = 0.87  →  AUTO-ACCEPT (margin sobre 15oz/29oz amplio porque      │
│              R4 cortó las variantes)                                │
├─────────────────────────────────────────────────────────────────────┤
│ Par 3 — Great Value Organic Tomato 8oz ↔ Wegmans Organic Tomato 29oz│
│   R4 (size) ya cortó este par antes del scoring                     │
│   ❌ no llega al scoring                                             │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 8. Stage 11 — LLM trigger (decisión)

¿Cuándo invocamos GPT-5 nano?

```
                Top-1 candidato post Stage 8 con score s y margin m
                              │
                              ▼
                ┌───────────────────────────────┐
                │  ¿Hay candidatos?             │
                └─────┬───────────────────┬─────┘
                  no  │                   │ sí
                      ▼                   ▼
             ┌────────────────┐   ┌──────────────────────┐
             │ ❌ Skip A item  │   │  ¿n_candidates = 1?  │
             └────────────────┘   └──┬───────────────┬───┘
                                  sí │               │ no
                                     ▼               ▼
                           ┌────────────────┐  ┌──────────────────┐
                           │ ✅ AUTO-ACCEPT  │  │  s ≥ 0.85 y      │
                           │  source=unique │  │  m ≥ 0.10?       │
                           └────────────────┘  └─┬────────────┬───┘
                                              sí │            │no
                                                 ▼            ▼
                                  ┌────────────────┐  ┌────────────┐
                                  │ ✅ AUTO-ACCEPT  │  │  s < 0.45? │
                                  │ source=det_high│  └─┬────────┬─┘
                                  └────────────────┘ sí│        │no
                                                       ▼        ▼
                                          ┌────────────────┐  ┌──────────────────┐
                                          │ ❌ AUTO-REJECT  │  │ 🤖 LLM ARBITER   │
                                          │ source=det_low │  │  GPT-5 nano      │
                                          └────────────────┘  │  JSON output     │
                                                              └──┬────────────┬──┘
                                                          conf≥0.6│           │conf<0.6
                                                                  ▼           ▼
                                                       ┌────────────────┐  ┌──────────────┐
                                                       │ ✅ ACCEPT       │  │ ❌ REJECT     │
                                                       │ source=llm_ok  │  │ source=llm_no│
                                                       └────────────────┘  └──────────────┘
```

**Garantía**: el LLM **no puede** override hard rules. Si la propuesta del LLM viola size/group/brand-PL, queda como `llm_reject_overruled`.

---

## 9. Distribución esperada del output final

```
┌─────────────────────────────────────────────────────────────────────┐
│  Source de los 4.000–7.000 matches finales                          │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  deterministic_high (score ≥ 0.85, margin OK)  █████████████  50%   │
│  deterministic_unique (1 candidato post-rules) ██████          15%  │
│  deterministic_mid   (0.65–0.85 con margin)    █████           12%  │
│  llm_accept (zona gris confirmada por LLM)     ████████        20%  │
│  manual_override (eval manual)                 █                3%  │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘

Confianza promedio por source (target):
  deterministic_high     ~0.90
  deterministic_unique   ~0.82
  llm_accept             ~0.78
  deterministic_mid      ~0.72
```

Distribución a confirmar con eval manual sobre el primer run.

---

## 10. Timeline de ejecución (Sábado → Lunes)

```
Sábado 2/5
├─ HECHO    Audit + retrieval probe
│           → docs/dataset_audit.md, docs/algorithm_recommendation.md,
│             docs/audit_stats.json, docs/retrieval_probe_results.json,
│             scripts/audit_data.py, scripts/retrieval_probe.py
├─ Tarde     [4h]  ████████  Stages 1–3 (ingest + parse + normalize) + tests
└─ Noche     [3h]  ██████    Stages 4–6 (scope + taxonomy + TF-IDF retrieval)

Domingo 3/5
├─ Mañana    [3h]  ██████    Stages 7–9 (rules + score + tie-break) + first run
├─ Tarde     [3h]  ██████    Mini-eval manual de 50 pares + tuning
└─ Noche     [3h]  ██████    Re-run + Stage 10 validation + Stage 11 opcional
                             ↑ buffer si algo se rompe

Lunes 4/5
├─ Mañana    [3h]  ██████    README polish + cleanup código
└─ Mediodía  [1h]  ██        Smoke test final + submission email
```

---

## 11. Comparación cualitativa de approaches (por qué este plan)

> Lecturas conceptuales / orden de magnitud. Los números finales (precision,
> conteo, costo, runtime) se miden tras correr el pipeline y el eval manual
> de 50 pares; no se reportan estimados aquí.

```
┌──────────────────────────────────┬──────────────────────────────────────────────┐
│ Approach                         │ Lectura cualitativa                          │
├──────────────────────────────────┼──────────────────────────────────────────────┤
│ Solo fuzzy / regex               │ Rápido pero alto riesgo de falsos positivos. │
│                                  │ No cubre paráfrasis ni duplicados por size.  │
│ Solo BM25 brand_included         │ Falla en private label (probe: "Great Value" │
│                                  │ → "Great Lakes"/"Great Regular Flavor").     │
│                                  │ Útil sólo como diagnóstico secundario.       │
│ TF-IDF word+char + hard rules    │ Plan recomendado para el primer deliverable. │
│   (este plan)                    │ El probe rankeó ambos ejemplos del PDF en #1.│
│ + GPT-5 nano en zona gris        │ Capa opcional para mejorar recall en casos   │
│   (opcional)                     │ grises tras el deterministic core.           │
│ Cross-encoder rerank             │ Mejora marginal teórica; runtime en CPU lo   │
│                                  │ vuelve impráctico para el deadline.          │
│ LLM en todos los candidatos      │ Conceptualmente posible, costoso, sin reglas │
│                                  │ igual no resuelve los traps de size.         │
│ LLM-first sobre cartesiano       │ Inviable: ~13B pares quiebran cualquier      │
│                                  │ presupuesto razonable. Ejemplo pedagógico.   │
└──────────────────────────────────┴──────────────────────────────────────────────┘

  Sweet spot: TF-IDF word+char + reglas duras + LLM opcional en zona gris.
  Cifras concretas se reportan tras el primer run y el eval manual.
```

---

## 12. Volúmenes y costos resumidos

```
┌──────────────────────────────────────────────────────────────────────┐
│  INPUT                                                               │
│  • A: 233.199 items   ·   B: 55.516 items                            │
│  • Total tamaño en disco: 223 MB                                     │
├──────────────────────────────────────────────────────────────────────┤
│  COMPUTE (todo CPU local, $0)                                        │
│  • Stages 1–3 (ingest + parse + normalize)             ~5–10 min     │
│  • Stage 4–5 (scope + taxonomy)                        segundos      │
│  • Stage 6 (TF-IDF retrieval, índices por group)       ~5–15 min     │
│  • Stages 7–9 (rules + score + tie-break)              ~5–10 min     │
│  • Stage 10 (output + validation)                      segundos      │
│  • Stage 11 (LLM async, opcional, ≤5k calls)           ~15–25 min    │
├──────────────────────────────────────────────────────────────────────┤
│  COSTO                                                               │
│  • OpenAI GPT-5 nano (si activo)                       <$2 USD       │
│  • Compute                                              $0           │
│  • Total runtime                                        <60 min      │
├──────────────────────────────────────────────────────────────────────┤
│  OUTPUT                                                              │
│  • matches.csv               4.000–7.000 filas                       │
│  • matches_audit.csv         + score, source, reason por par         │
│  • README.md                 + sample eval manual                    │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 13. Riesgos principales

```
┌─────────────────────────────────────┬──────┬──────┬────────────────────────┐
│ Riesgo                              │ Prob │ Imp. │ Mitigación             │
├─────────────────────────────────────┼──────┼──────┼────────────────────────┤
│ Filas malformadas A en output       │ baja │ ALTO │ ^\d+$ + cuarentena S1  │
│ Quedar corto de 4.000 matches       │ baja │ ALTO │ Bajar threshold/margin │
│ Output CSV malformado               │ baja │ ALTO │ Smoke test + IDs check │
│ Brand inference ruidosa             │ med  │ med  │ Whitelist + flag       │
│ PL retrieval retrieva ruido         │ alta │ ALTO │ Suppression + categoría│
│ Duplicados B → elección arbitraria  │ alta │ bajo │ Tie-break determinista │
│ matchable_group muy estricto        │ med  │ ALTO │ Iterar con eval manual │
│ LLM rate-limited / down             │ baja │ med  │ LLM opcional + cache   │
│ Costo LLM se dispara                │ baja │ med  │ Cap max_calls          │
│ Embeddings necesarios para recall   │ med  │ bajo │ Capa diferida          │
└─────────────────────────────────────┴──────┴──────┴────────────────────────┘
```

---

## 14. Estructura de archivos del entregable

```
BetterBasket-assignment/
│
├── README.md                                ← interview-ready overview
├── solution.md                              ← narrativa técnica pulida
├── solutioneasyexplained.md                 ← versión plain-Spanish
├── app.md                                   ← este archivo (visual)
│
├── docs/
│   ├── dataset_audit.md                     ← canonical audit (source of truth)
│   ├── dataset_audit_stats.md               ← stats compactos
│   ├── audit_stats.json                     ← machine-readable audit output
│   ├── algorithm_recommendation.md          ← canonical algorithm plan
│   ├── retrieval_probe_results.md           ← retrieval dry-run summary
│   ├── retrieval_probe_results.json         ← machine-readable probe output
│   ├── audits/2026-05-02-dataset-audit.md   ← retired, points to canonical
│   └── plans/2026-05-02-betterbasket-product-matching.md ← retired, points to canonical
│
├── scripts/
│   ├── audit_data.py                        ← streaming CSV audit (no pandas)
│   └── retrieval_probe.py                   ← TF-IDF / BM25 dry-run
│
├── tests/
│   └── fixtures/                            ← reservado para tests del matcher
│
└── (planeado, próximo paso de implementación)
    ├── betterbasket_matcher/
    │   ├── __init__.py
    │   ├── io.py                            ← Stages 1–2
    │   ├── normalize.py                     ← Stage 3
    │   ├── taxonomy.py                      ← Stage 5
    │   ├── scope.py                         ← Stage 4
    │   ├── retrieval.py                     ← Stage 6 (TF-IDF word + char)
    │   ├── rules.py                         ← Stage 7
    │   ├── scoring.py                       ← Stage 8
    │   ├── pipeline.py                      ← orquestación
    │   ├── llm_arbiter.py                   ← Stage 11 (opcional)
    │   └── output.py                        ← Stage 10
    ├── scripts/run_pipeline.py
    ├── tests/test_io.py, test_normalize.py, test_rules.py, ...
    ├── matches.csv                          ← deliverable principal
    ├── matches_audit.csv                    ← audit trail
    └── eval/manual_eval.md                  ← 50 pares evaluados
```

---

## 15. Qué leer según rol

```
┌────────────────────────────┬────────────────────────┬──────────────────────────┐
│ Si sos...                  │ Leé primero            │ Profundizá en            │
├────────────────────────────┼────────────────────────┼──────────────────────────┤
│ Reviewer técnico           │ README.md              │ docs/dataset_audit.md    │
│                            │                        │ docs/algorithm_recom...  │
│                            │                        │ solution.md sec. 4       │
│ Manager / non-tech         │ README.md TL;DR        │ app.md secciones 1, 11   │
│ Quien va a correr el code  │ README.md quickstart   │ scripts/audit_data.py    │
│ Auditor de matches         │ matches_audit.csv      │ eval/manual_eval.md      │
│ Quien implementa el match  │ docs/algorithm_recom...│ solution.md sec. 4       │
└────────────────────────────┴────────────────────────┴──────────────────────────┘
```

---

**Para fundamentación textual completa de cada decisión** → ver [solution.md](solution.md).
**Para los datos crudos detrás del plan** → ver [`docs/dataset_audit.md`](docs/dataset_audit.md) y [`docs/algorithm_recommendation.md`](docs/algorithm_recommendation.md).
