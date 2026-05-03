# Solución — BetterBasket Engineering Technical Assessment

**Autor**: Julian Irusta Roure
**Empresa**: BetterBasket
**Posición**: Summer 2026 Internship — Engineering
**Tarea**: Cross-retailer product matching (Walmart ↔ Wegmans)

---

> Este documento es la versión narrativa pulida de las dos fuentes de verdad del repo: `docs/dataset_audit.md` (qué dicen los CSV) y `docs/algorithm_recommendation.md` (qué algoritmo recomendamos). Si encuentro alguna discrepancia entre este documento y esos dos, **gana el `docs/`**.

## TL;DR

- **El problema**: dado un producto del catálogo de Walmart (A, ~233k items), encontrar el "single closest match" en el catálogo de Wegmans (B, ~55k items). Entregar como mínimo 4.000 matches; el set completo se estima en >10.000.
- **Lo que descubrió la auditoría**: las columnas pre-procesadas (`name_clean`, `category`, `department`, `subcategory`, `size_raw`, `is_private_label`, `item_type`, `is_organic`) están **100% vacías** en sus respectivos archivos. El UPC no es viable: A no tiene campos UPC-like y B sólo tiene 681 `ic_item_id`. A tiene **5 filas malformadas** con `item_id` no numérico (columnas corridas, e.g. `" | Pack of 12"`) que hay que cuarentenar antes de cualquier matching. `brand_raw` está blank en 45.87% de A (63.89% en Food). El problema es **100% entity resolution sobre atributos textuales**.
- **La solución recomendada (precision-first, determinista)**: streaming ingest + validación → parseo tolerante de JSON/tags → normalización (brand, private-label, taxonomía, size, pack, organic/form/storage/flavor) → scope filtering → blocking por `matchable_group` compartido → **retrieval con TF-IDF word + char n-grams** sobre B → reglas duras → score determinístico ponderado → un único best B por A → `matches.csv` + `matches_audit.csv`. **GPT-5 nano queda como árbitro opcional** sólo para zonas grises sobre candidate sets ya filtrados.
- **Por qué TF-IDF word + char y no BM25/FAISS/RRF como motor principal**: el dry-run del repo (`scripts/retrieval_probe.py`) lo demostró sobre los 55.516 items de B: TF-IDF word+char rankea ambos ejemplos del PDF en #1, mientras que BM25 con marca incluida hundió a `Great Value Organic Tomato Sauce 8 oz` al rank 5 (top-1: `Colgate Fluoride Toothpaste, Great Regular Flavor, 3 Value Pack`). Embeddings semánticos quedan como capa opcional posterior, no como primer entregable.
- **Costo y tiempo**: el deterministic core no cuesta API. Si activamos el árbitro LLM sobre la zona gris, cae bien por debajo de los $2 USD. Runtime objetivo: una corrida en menos de una hora end-to-end.

---

## Tabla de contenidos

1. [Entendimiento del problema](#1-entendimiento-del-problema)
2. [Auditoría exhaustiva de los datasets](#2-auditoría-exhaustiva-de-los-datasets)
3. [Decisiones arquitectónicas y trade-offs](#3-decisiones-arquitectónicas-y-trade-offs)
4. [El pipeline en detalle](#4-el-pipeline-en-detalle)
5. [Calibración y evaluación](#5-calibración-y-evaluación)
6. [Volúmenes, costos, tiempos](#6-volúmenes-costos-tiempos)
7. [Supuestos explícitos](#7-supuestos-explícitos)
8. [Plan de ejecución (timeline)](#8-plan-de-ejecución-timeline)
9. [Riesgos y mitigaciones](#9-riesgos-y-mitigaciones)
10. [Deliverables](#10-deliverables)
11. [Apéndice: alternativas descartadas](#11-apéndice-alternativas-descartadas)
12. [Glosario técnico](#12-glosario-técnico)

---

## 1. Entendimiento del problema

### 1.1 La consigna en una frase

> Para cada producto de A, encontrar el producto **más cercano** en B según el criterio: *"un cliente consideraría que ambos son esencialmente el mismo producto"*. Entregar `matches.csv` con al menos 4.000 filas `(item_id_A, item_id_B)`.

### 1.2 Los tres tipos de match (y por qué importan distinto)

#### Tipo 1: Exact match con UPC en ambos lados

El **UPC** es el código de 12 dígitos que escanea la caja. Cuando ambos lados lo exponen, es el gold standard para matching.

**Por qué no aplica en este dataset**: A tiene **0** campos UPC-like; B tiene 681 `item_info.ic_item_id` (1.2%). Sin contraparte en A, el UPC join es **inviable**.

#### Tipo 2: Exact match sin UPC (vía atributos)

Mismo producto, misma marca, mismo size, sin UPC compartido. Hay que matchear por `brand` + `core_name` + `size`.

**Ejemplo del PDF (verificado en datos reales)**:
- A `2197626`: "Chobani Whole Milk Greek Yogurt Honey Blended 5.3 oz Cup"
- B `92544`: "Chobani Greek Honey Blended Yogurt", `sizing_comp.size_user_friendly = "5.3 ounce"`

#### Tipo 3: Non-exact match (private label cross-store)

Productos diferentes pero **funcionalmente equivalentes**. Distintas marcas (private label de cada cadena), pero un cliente los consideraría sustitutos.

**Ejemplo del PDF (verificado)**:
- A `1929544`: "Great Value Organic Tomato Sauce, 8 oz" (Walmart private label)
- B `105624`: "Wegmans Organic Tomato Sauce", size 8 ounce (Wegmans private label)

Cuidado con el ruido: B tiene también `103620` (15 oz) y `1086860` (29 oz) con el mismo nombre — la elección correcta exige reglas duras de size.

#### Tabla resumen

| Tipo | Detección | Confianza | Coste |
|---|---|---|---|
| 1 (UPC) | JOIN directo | 99% | O(n) — pero **no aplica acá** |
| 2 (atributos) | brand exacto + size exacto + name similarity alta | 90-95% | O(retrieval) |
| 3 (private label cross) | reglas de PL compatibility + score determinístico + LLM opcional | 70-85% | O(retrieval + opcional LLM) |

### 1.3 La escala que cambia todo

| Cantidad | Valor |
|---|---|
| Items en A | 233.199 |
| Items en B | 55.516 |
| Producto cartesiano (A × B) | **12.946.275.684** pares |

Esto descarta cualquier idea de "le tiro todos los pares al LLM y que decida". El trabajo de ingeniería del pipeline es reducir 12.9B candidatos a ~1-3M con retrieval, después a ~50-200k tras reglas duras, y entregar 4-7k matches finales — sin perder los buenos.

### 1.4 La trampa del enunciado: GPT-5 nano

El PDF del assessment dice:

> *GPT-5 nano deployment credentials will be provided by BetterBasket to be used in the solution for the above task.*

La interpretación junior es "lo decide el modelo". La interpretación sólida es: **usar el LLM selectivamente** donde aporta criterio que las reglas no cubren (private label cross-store, score margin chico, A con brand blank o inferido), y justificar por qué no se usa en el resto. Para llegar bien al piso de 4.000 matches con precision defendible, el deterministic core es lo que paga el viaje; el LLM es el árbitro.

---

## 2. Auditoría exhaustiva de los datasets

> Fuente reproducible: `scripts/audit_data.py` → `docs/audit_stats.json` → `docs/dataset_audit.md` y `docs/dataset_audit_stats.md`. Todo lo que sigue está respaldado por esos artefactos.

### 2.1 Inventario rápido

| Métrica | A (Walmart) | B (Wegmans) |
|---|---|---|
| Filas | 233.199 | 55.516 |
| Tamaño en disco | 158.86 MB | 64.25 MB |
| Columnas | 19 | 18 |

### 2.2 Schemas

#### A (Walmart) — 19 columnas

```
item_id, name, brand_raw, name_clean, description, category, department, url,
item_type, item_info, tags, subcategory, is_private_label, sizing_comp,
size_raw, datapoint_id, raw_data_id, created_at_utc, updated_at_utc
```

#### B (Wegmans) — 18 columnas

```
item_id, name, brand_raw, name_clean, description, category, department, url,
is_organic, item_info, tags, subcategory, sizing_comp, size_raw,
datapoint_id, raw_data_id, created_at_utc, updated_at_utc
```

**Diferencias estructurales**: A tiene `item_type` y `is_private_label`; B tiene `is_organic`. (Las tres están blank al 100%, así que no cambian nada operativamente.)

### 2.3 Hallazgos críticos

#### Hallazgo 1: filas malformadas en A con `item_id` no numérico

A tiene **5 filas con `item_id` no numérico** y columnas corridas. Ejemplos de `item_id` repetidos: `" | Pack of 6"`, `" | Pack of 8"`, `" | Pack of 12"`, `"Acrylic Tortoise Thick Gripjaw Non Holdmetal Small Hair"`. Coincide con las 5 filas donde `sizing_comp` parsea a un valor JSON que no es dict.

**Implicación**: Stage 1 valida `item_id` con `^\d+$` y **cuarentena** las filas inválidas antes de normalizar, retrievear, scorear o emitir output. Stage final valida que cada `item_id` del CSV de salida exista en los sets numéricos originales y que no haya duplicados de `item_id_A`.

#### Hallazgo 2: las columnas pre-procesadas son señuelos

| Columna | A blank/null | B blank/null |
|---|---|---|
| `name_clean` | 100% | 100% |
| `category`, `department`, `subcategory` | 100% | 100% |
| `size_raw` | 100% | 100% |
| `item_type` | 100% | n/a |
| `is_private_label` | 100% | n/a |
| `is_organic` | n/a | 100% |
| `tags` | 100% (excepto las 5 malformadas) | 58.96% blank |
| `description` | 91.85% blank | 6.44% blank |

Las únicas columnas confiables para matching son `item_id`, `name`, `brand_raw`, `url`, `item_info`, `sizing_comp` y `tags` de B. `description` ayuda sólo después de strip HTML, y A casi no la trae.

**Implicación**: la normalización (Stage 1) es donde está el verdadero trabajo. Hay que reconstruir todo desde `name`, `brand_raw`, `item_info`, `sizing_comp` y B `tags`.

#### Hallazgo 3: UPC no es viable en este dataset

- A: 0 campos UPC-like, 0 valores plausibles.
- B: 681 valores `item_info.ic_item_id` (1.23%), todos en el rango 8-14 dígitos.

Sin contraparte en A no hay UPC join. Tratamos el problema como **pure entity resolution**.

#### Hallazgo 4: crisis de brand en A

| Población | Blank/null `brand_raw` |
|---|---|
| A total | 106.962 / 233.199 (45.87%) |
| A Food (cat0=Food) | 46.947 / 73.478 (63.89%) |
| B total | 5.545 / 55.516 (9.99%) |

| Métrica | Valor |
|---|---|
| Brands únicas normalizadas en A (nonblank) | 18.562 |
| Brands únicas normalizadas en B (nonblank) | 5.632 |
| **Shared normalized brands** | **2.441** |
| Filas branded en A cuyo brand existe en B | 34.584 / 126.237 (27.40%) |
| Filas branded en B cuyo brand existe en A | 28.752 / 49.971 (57.54%) |

**Implicación**:
- Brand es señal fuerte cuando ambos lados son national con marca poblada.
- Brand **no puede ser global-required**: A tiene demasiadas blanks.
- A necesita inferencia de brand conservadora desde el name (con `brand_inferred=True`).
- Mismatch de brand entre national y private-label es esperado y se maneja con reglas, no rechazando todo.

#### Hallazgo 5: private-label tiene que ser detectado explícitamente

Estimación conservadora desde reglas brand/name/tag:

| Señal | A | B |
|---|---|---|
| Filas private-label estimadas | 22.363 | 8.498 |

A se reconoce con whitelist: `great value` (5.692), `mainstays` (3.487), `freshness guaranteed` (1.367), `equate` (815), `marketside` (255), `bettergoods` (97), más `wonder nation`, `sam s choice`, `members mark`, etc.

B se reconoce con `brand_raw == Wegmans` (8.050) o tag `wegmans brand` (6.820). Otros tags útiles en B: `organic`, `family pack`, `gluten free`, `vegan`, `food you feel good about`.

#### Hallazgo 6: ubicación del size es asimétrica

| Métrica | A | B |
|---|---|---|
| Size parseado del `name` | 138.739 (59.49%) | 5.204 (9.37%) |
| Food size parseado del `name` (A) | 66.174 / 73.478 (90.06%) | n/a |
| `sizing_comp.size_user_friendly` nonblank | 39.765 (17.05%) | 53.090 (95.63%) |
| Size parseado de `sizing_comp.size_user_friendly` | 24.621 (10.56%) | 53.024 (95.51%) |
| Conflictos cuando ambas fuentes parsean | 4.412 | 4.112 |

**Implicación**:
- A grocery: parsear size desde `name` con regex; fallback a `sizing_comp`.
- B: usar `sizing_comp.size_user_friendly` como fuente primaria; fallback a `name`.
- Pack count se parsea **separado** del per-unit size (e.g. `(12 pack) ... 7 oz` → `pack_count=12`, `size=(7, oz)`).
- Strings de dimensión (`5 x 7`, `12 x 24`) son frames/home, no grocery, y no se usan como size de matching.

#### Hallazgo 7: B tiene duplicados estructurales por size/pack

Agrupando por `(brand_norm, name_without_size)`:

- **2.796 grupos** con más de una fila.
- **6.320 filas** dentro de esos grupos.

No son duplicados verdaderos en su mayoría: son **el mismo concepto de producto en sizes/packs distintos**.

Ejemplos del audit:
- `Wegmans Organic Tomato Sauce`: 8 oz, 15 oz, 29 oz.
- `Wegmans Tomato Sauce`: 8 oz, 15 oz, 29 oz.
- `FIJI Natural Artesian Water`: 6 × 16.9 fl. oz., 1.5 L, 1 L, 24 × 16.9 fl. oz., 700 ml, etc.
- `Hershey's Candy Assortment`: ~15 filas con sizes desde ~13 oz a 64+ oz.
- `Mountain Dew Citrus Soda`: 2 L, 6 × 7.5 fl. oz., 12 × 12 fl. oz., 24 × 12 fl. oz., y otros packs.

**Implicación**: el size debe ser **regla dura** antes del scoring. Elegir entre `Wegmans Organic Tomato Sauce` 8 / 15 / 29 oz por similitud de nombre solamente es un error garantizado.

#### Hallazgo 8: taxonomías de categoría son incompatibles

`category`, `department`, `subcategory` están vacías en ambos. La info de categoría hay que sacarla de `item_info` (`category_0..3`).

Top A `category_0`: Food (73.478), Health and Medicine (20.501), Personal Care (15.715), Household Essentials (14.802), Toys (14.138), Pets (13.158), Baby (12.805), Clothing (11.990), Home (9.497), Beauty (5.243).

Top B `category_0`: More Departments (19.586), Grocery (18.438), Wine/Beer/Spirits (5.040), Frozen (3.679), Dairy (2.951), Produce & Floral (1.739), Bakery (1.248), Meat (959), Prepared Foods (669), Cheese (645), Seafood (562).

**Exclusiones claras de A** (no tienen contraparte en B), que suman ≥ **48.007 filas**:

`Toys`, `Clothing`, `Home Improvement`, `Sports & Outdoors`, `Party & Occasions`, `Office Supplies`, `Auto & Tires`, `Electronics`, `Arts Crafts & Sewing`, `Jewelry`, `Books`, `Cell Phones`.

`Home` se trata selectivamente: `Home > Kitchen & Dining` puede mapear a B `More Departments > Kitchen and Home`, pero `Home > Decor`, frames, bedding, furniture y wall art se excluyen.

**Implicación**: construir una taxonomía intermedia compartida, `matchable_group`, en vez de comparar nombres de categoría crudos.

#### Hallazgo 9: pack noise y HTML

| Métrica | A | B |
|---|---|---|
| Pack-prefix en `name` | 22.882 | 0 |
| Multipack patterns | 370 | 699 |
| Dimension-like strings | 3.071 | 700 |
| Descriptions con HTML | 7.410 | 6 |

**Implicación**: limpiar `(N pack)`/`Pack of N` del `name` antes de tokenizar para retrieval; guardar `pack_count` aparte; strip HTML de description.

#### Hallazgo 10: tags de B aportan señal valiosa

Aunque A tiene tags vacíos (excepto las 5 malformadas), B trae tags estructurados (parser tolerante: JSON → array Postgres → split). Tags útiles: `wegmans brand`, `organic`, `gluten free`, `family pack`, `vegan`, `food you feel good about`.

**Implicación**: parsear B tags y usarlos para detectar `is_private_label`, `is_organic`, `is_family_pack`, dietéticos.

### 2.4 Verificación de los ejemplos del PDF en datos reales

| Ejemplo | Store | Item ID | Name | Size |
|---|---|---|---|---|
| Chobani honey blended yogurt | A | `2197626` | Chobani Whole Milk Greek Yogurt Honey Blended 5.3 oz Cup | 5.3 oz |
| Chobani honey blended yogurt | B | `92544` | Chobani Greek Honey Blended Yogurt | 5.3 ounce |
| Private-label organic tomato sauce | A | `1929544` | Great Value Organic Tomato Sauce, 8 oz | 8 oz |
| Private-label organic tomato sauce | B | `105624` | Wegmans Organic Tomato Sauce | 8 ounce |

Trampas cercanas en B: `103620` (15 oz), `1086860` (29 oz). Trampas cercanas en A: `1929545` (15 oz), `2116415` (`(4 pack) ... 15 oz`), `1949064` (`(8 pack) ... 8 oz`). Los ejemplos del PDF son reales y prueban por qué private-label compatibility y reglas de size/pack son obligatorias.

### 2.5 Retrieval probe: por qué TF-IDF word + char y no BM25

Fuente reproducible: `scripts/retrieval_probe.py` → `docs/retrieval_probe_results.json` y `docs/retrieval_probe_results.md`.

El probe indexó las 55.516 filas de B y comparó dos modos de query:

- `brand_included`: el query incluye los tokens de marca tal cual.
- `suppress_private_label`: para items private-label de A, se quitan los tokens de marca de tienda (`great value`, `marketside`, `wegmans`, etc.).

**Resultados TF-IDF (word `(1,2)` + char-wb `(3,5)`)**:

| Probe | Modo | Expected B | Rank | Top-1 |
|---|---|---|---:|---|
| Chobani 5.3 oz honey yogurt | brand_included | `92544` | 1 | `92544` (correcto) |
| Chobani 5.3 oz honey yogurt | suppress_private_label | `92544` | 1 | `92544` |
| Great Value Organic Tomato Sauce 8 oz | brand_included | `105624` | 1 | `105624` (correcto) |
| Great Value Organic Tomato Sauce 8 oz | suppress_private_label | `105624` | 1 | `105624` |

**Resultados BM25 (mismo corpus, mismas queries)**:

| Probe | Modo | Expected B | Rank | Failure mode |
|---|---|---|---:|---|
| Chobani 5.3 oz honey yogurt | brand_included | `92544` | 1 | OK |
| Great Value Organic Tomato Sauce 8 oz | brand_included | `105624` | **5** | Top-1: `Colgate Fluoride Toothpaste, Great Regular Flavor, 3 Value Pack` |
| Great Value Organic Tomato Sauce 8 oz | suppress_private_label | `105624` | 1 | Suprimir marca privada arregla el caso |
| Great Value Provolone (text probe) | brand_included | n/a | n/a | Top-1: `Great Lakes Provolone Cheese` |
| Great Value Provolone (text probe) | suppress_private_label | n/a | n/a | Top-1 vuelve a Wegmans/private-label cheese; los `Great Lakes` bajan |

Conteo de hits ruidosos `Great Lakes` en top-50:

| Modo | Probe | Hits `Great Lakes` |
|---|---|---:|
| brand_included | great_value_tomato_text | 0 |
| brand_included | great_value_provolone_text | 7 |
| brand_included | great_value_water_text | 1 |
| suppress_private_label | great_value_tomato_text | 0 |
| suppress_private_label | great_value_provolone_text | 2 |
| suppress_private_label | great_value_water_text | 0 |

**Conclusiones del probe**:

1. **TF-IDF word + char es el motor de retrieval**. Maneja bien tanto national brand (Chobani) como private-label cross-store (Great Value ↔ Wegmans), incluso sin suppression.
2. **BM25 con marca incluida tiene trampas léxicas duras** en private label (`Great Value` → `Great Lakes`, `Great Regular Flavor`). Sirve como diagnóstico secundario, no como retrieval primario.
3. **Suprimir tokens de marca privada en el query del lado A reduce el ruido** y es necesario para casos como `Great Value Provolone`. Para el lado B, el corpus se construye igual: mismas reglas de suppression aplicadas a items B private-label.
4. **Aun con TF-IDF perfecto, el size es decisivo**: el top-3 para `Great Value Organic Tomato Sauce 8 oz` son los Wegmans Organic Tomato Sauce de 8, 15 y 29 oz, en ese orden. Sin reglas duras de size, alguien va a quedar con el pote equivocado.
5. **Embeddings/FAISS quedan como capa opcional posterior** (mejor recall en paráfrasis para fresh y private-label semánticos), no como primer entregable. El deterministic core ya rankea los ejemplos en #1.

---

## 3. Decisiones arquitectónicas y trade-offs

### 3.1 Determinístico precision-first, no LLM-first

**LLM-first (descartado como motor)**:

- 12.946.275.684 pares hace inviable comparar todo. Aún reduciendo, el LLM **igual necesita** retrieval, normalización y reglas duras antes para no errar en obvios (8 oz vs 15 oz tomato sauce).
- El LLM aporta cuando hay zona gris; no aporta cuando la respuesta es determinística.

**Rules-only (descartado)**:

- No maneja paráfrasis ("Whole Milk" vs "Vit D Milk", "Greek" vs "Strained").
- Falla justo en non-exact matches (Tipo 3) que es donde el problema se vuelve interesante.

**Hybrid determinístico (elegido)**:

- TF-IDF word + char para recall (probado en `retrieval_probe.py`).
- Reglas duras para precision (size/category/brand/PL/pack/organic/form/storage/flavor).
- Score determinístico ponderado para ranking final.
- LLM como árbitro **opcional** para zona gris, sobre candidate sets ya filtrados.

### 3.2 Por qué TF-IDF word + char como retrieval primario (y BM25 como diagnóstico)

El retrieval probe del repo lo dejó claro: TF-IDF rankea ambos ejemplos del PDF en #1 con o sin suppression, mientras que BM25 con brand incluida se va al rank 5 en private label porque "Great" / "Value" inflan candidatos como `Great Regular Flavor` o `Great Lakes`. BM25 sigue siendo útil para validar (probar el espacio de candidatos desde otro algoritmo), pero el motor principal es TF-IDF word `(1,2)` + char-wb `(3,5)`.

Embeddings semánticos (e.g. sentence-transformers) son una **mejora de recall posterior**, no un requisito del primer entregable. Son más débiles para diferenciar sizes, packs, flavors y variantes numéricas, que es donde más nos hieren los duplicados de B.

### 3.3 Por qué precision-first (target 4.000–7.000, no 12.000)

| Argumento | Detalle |
|---|---|
| Literal del PDF | "single closest match" — no dice "todos los matches posibles". |
| El piso vs la meta | 4.000 es floor, no target. Defender 5–7k buenos > defender 12k con basura. |
| Costo de un match falso | En pricing real, un match falso indexa contra un producto incomparable. Mejor sin match que con match falso. |
| Reversibilidad | El threshold se puede **bajar** después si quedamos cortos (cuesta minutos). Subirlo después implica re-revisar matches contaminados (cuesta horas). |

### 3.4 Por qué private-label brand suppression es necesaria

Los tokens `Great Value`, `Marketside`, `Freshness Guaranteed`, `Wegmans` son trampa léxica para retrieval por similitud. Cuando el item A es private-label, el query se reescribe quitando esos tokens y apoyándose en core name + size + `matchable_group` + organic. El probe lo confirmó: con BM25 brand_included, `Great Value Organic Tomato Sauce 8 oz` cae al rank 5; con suppression, sube al rank 1.

### 3.5 Por qué LLM solo como árbitro

El **costo dominante** del LLM en este pipeline no es el dinero (es barato), es la **latencia** y **variabilidad**. Lo invocamos sólo cuando el deterministic core no es concluyente:

- Score top-1 en zona gris.
- Margin pequeño entre top-1 y top-2.
- A con `brand_blank` o `brand_inferred` y múltiples candidatos B plausibles.
- Private-label cross-store donde la decisión de "mismo producto para el cliente" requiere criterio.

Reglas duras de size/category/brand/PL/pack/etc. **no se overrideen** con la respuesta del LLM. El LLM no decide que un 8 oz matchee con un 15 oz porque "se ven parecidos".

### 3.6 Decisiones que descartamos explícitamente

| Idea | Por qué la descartamos |
|---|---|
| **All-pairs comparison** | 12.9B pares: imposible. |
| **LLM-first / LLM como motor** | Caro, lento, no determinista, no agrega valor donde reglas dan respuesta exacta. |
| **UPC-first** | A no tiene UPC. Plan no aplicable. |
| **Solo fuzzy / Levenshtein** | Falla en non-exact matches y en duplicados por size; no maneja PL cross. |
| **BM25 + FAISS + RRF como motor principal** | El probe mostró que BM25 brand_included tiene trampas léxicas críticas en private label. TF-IDF word+char ya rankea ambos ejemplos del PDF en #1 sin necesitar fusion. |
| **Embeddings (FAISS) como primera capa** | Optional later-stage enrichment. Más débiles para tamaños / packs / flavors; no son requisito del primer deliverable. |
| **Requerir brand global-equality** | A tiene 45.87% brand blank — esto excluiría más de la mitad del catálogo. |
| **Aceptar same-name diferentes-size sin reglas** | B tiene 2.796 grupos duplicate-like; sin reglas duras de size, garantizamos elegir el pote equivocado. |
| **Confiar en columnas pre-cleaned** | `name_clean`, `category`, `department`, `subcategory`, `size_raw`, `is_private_label`, `item_type`, `is_organic` están al 100% blank. |

---

## 4. El pipeline en detalle

### Diagrama global

```
┌──── A: 233.199 items (Walmart) ────┐         ┌──── B: 55.516 items (Wegmans) ────┐
│                                    │         │                                    │
└──────┬─────────────────────────────┘         └──────┬─────────────────────────────┘
       │                                              │
       ▼                                              ▼
┌──────────────────────────────────────────────────────────────────────────────────┐
│ Stage 1 — INGEST + VALIDATE     (item_id ~ ^\d+$, cuarentena 5 filas malformadas)│
│ Stage 2 — PARSE JSON / TAGS     (item_info, sizing_comp, B tags tolerantes)      │
│ Stage 3 — NORMALIZE             (brand, PL, taxonomía, size, pack, organic, ...) │
│ Stage 4 — SCOPE FILTER          (excluir Toys/Clothing/...; reducir A)           │
│ Stage 5 — TAXONOMY BLOCKING     (matchable_group compartido)                     │
│ Stage 6 — CANDIDATE RETRIEVAL   (TF-IDF word+char sobre B, suppression PL)       │
│ Stage 7 — HARD RULES            (group, brand/PL, size, pack, organic, ...)      │
│ Stage 8 — DETERMINISTIC SCORE   (weighted, top-1 + margin)                       │
│ Stage 9 — TIE-BREAK B DUPLICATES                                                 │
│ Stage 10 — OUTPUT + AUDIT       (matches.csv + matches_audit.csv)                │
│ Stage 11 — OPTIONAL LLM ARBITER (zona gris, sólo sobre candidate set chico)      │
└──────────────────────────────────────────────────────────────────────────────────┘
       │                                              │
       ▼                                              ▼
   matches.csv                              matches_audit.csv
```

### Stage 1 — Ingest and validate

- `csv.DictReader` streaming sobre A y B. Sin pandas (footprint chico).
- Validar `item_id ~ ^\d+$`. Las **5 filas de A con `item_id` no numérico** se aíslan en `quarantine_a.csv` y **no entran** al resto del pipeline.
- Validar `name` no blank.
- Construir los sets numéricos de IDs válidos `valid_ids_A`, `valid_ids_B`. El validador final (Stage 10) los usa para confirmar que cada `item_id` del output existe.

### Stage 2 — Parse JSON / tags

- `item_info` (A y B) y `sizing_comp` (A y B): parser tolerante (`json.loads` → `ast.literal_eval` → dict vacío). Si el valor parsea pero no es dict, devolver `{}`.
- B `tags`: parser tolerante que intenta JSON list, luego array Postgres `{a,b,c}`, luego split por coma. Caso fallback: lista vacía.
- A `tags`: 100% blank salvo las 5 malformadas — no se usa.
- A `description` y B `description`: strip HTML antes de tocar; opcional, sólo si se requiere reranking.

### Stage 3 — Normalize

Cada item válido se proyecta a un registro normalizado (campos en `docs/algorithm_recommendation.md` §2):

`item_id`, `source`, `name_raw`, `name_norm`, `core_name`, `brand_norm`, `brand_inferred`, `is_private_label`, `category_0..2`, `matchable_group`, `size_value`, `size_unit`, `size_family`, `pack_count`, `unit_size`, `total_size`, `is_organic`, `storage_type`, `form`, `flavor_tokens`, `retrieval_text`.

Reglas:

- **Brand**:
  - A: `brand_raw` si está poblado; si no, inferir de prefijo del `name` contra una whitelist (private labels Walmart + brands top de B). `brand_inferred=True` cuando se infirió.
  - B: `brand_raw` directo (cobertura 90%).
- **Private label**:
  - A: brand ∈ whitelist Walmart (`great value`, `marketside`, `freshness guaranteed`, `bettergoods`, `equate`, `mainstays`, `wonder nation`, `sam s choice`, `members mark`, etc., como en `scripts/audit_data.py`).
  - B: `brand_norm == 'wegmans'` OR tag `wegmans brand` / `wegmans_brand`.
- **Categorías**: parsear `item_info.category_{0..3}` en ambos lados; ignorar las columnas raw.
- **Size**:
  - A: regex sobre `name` primero, fallback a `sizing_comp.size_user_friendly`.
  - B: `sizing_comp.size_user_friendly` primero, fallback a `name`.
  - Canonicalizar a (`value`, `unit`, `size_family`) donde `size_family ∈ {weight, volume, count, dimension, each}`.
  - Pack count se parsea aparte (`(N pack)`, `Pack of N`, `N x …`).
  - Strings de dimensión (`5 x 7`, etc.) se etiquetan `size_family=dimension` y no se usan para grocery matching.
- **Organic / form / storage / flavor**: keyword extraction sobre `name`, complementado por B `tags` (`organic`, `family pack`, `gluten free`, `vegan`, etc.).

### Stage 4 — Scope filter

Excluir A donde `category_0` está en la lista del audit:

`Toys`, `Clothing`, `Home Improvement`, `Sports & Outdoors`, `Party & Occasions`, `Office Supplies`, `Auto & Tires`, `Electronics`, `Arts Crafts & Sewing`, `Jewelry`, `Books`, `Cell Phones`. (≥ 48.007 filas.)

`Home` se mantiene **selectivamente**: keep `Kitchen & Dining` y similar utility; drop `Decor`, frames, bedding, furniture, rugs, wall art.

B no se filtra (ya es 100% grocery-relevant).

### Stage 5 — Taxonomy blocking (`matchable_group`)

Construir una taxonomía intermedia compartida con grupos como:

`pantry`, `snacks`, `candy`, `beverages`, `dairy`, `cheese`, `frozen`, `produce`, `meat`, `seafood`, `bakery`, `prepared_foods`, `baby`, `pets`, `household`, `personal_care`, `health`, `beauty`, `kitchen_home`, `wine_beer_spirits`.

Mapeo por reglas sobre `(category_0..2)` de cada lado. Permitir cross-group narrow links cuando la taxonomía de las tiendas se parte distinto (e.g. B `Cheese` ↔ A `Food > Dairy & Eggs`).

### Stage 6 — Candidate retrieval

**Motor primario: TF-IDF word + char n-grams sobre B.**

- Tokenización: lowercase, strip de pack prefix, strip HTML cuando aplica.
- `TfidfVectorizer` 1: word, `ngram_range=(1, 2)`.
- `TfidfVectorizer` 2: char-wb, `ngram_range=(3, 5)`.
- L2-normalize y concat horizontal (`scipy.sparse.hstack`) para obtener una representación combinada.
- Construir índices **por `matchable_group`** cuando sea posible; si la fila tiene group ambiguo, indexar globalmente y filtrar a posteriori por group compatible.

**Texto de retrieval** (asimétrico según marca):

- **National brand**: incluir brand fuerte.
  ```
  text = f"{brand_norm} {core_name} {size_value} {size_unit} {matchable_group} {category_1} {category_2}"
  ```
  Ej. (Chobani A `2197626`): `"chobani greek honey blended yogurt 5.3 oz_weight dairy yogurt"`
- **Private label**: suppression de tokens de marca de tienda (`great value`, `marketside`, `freshness guaranteed`, `wegmans`, etc.).
  ```
  text = f"{core_name} {size_value} {size_unit} {matchable_group} {organic_token} {category_1} {category_2}"
  ```
  Ej. (Great Value A `1929544`): `"organic tomato sauce 8 oz_weight pantry organic canned"`

Esa asimetría está respaldada por el probe (§2.5). El corpus B se construye con la misma regla de suppression para items B private-label, así A y B comparten vocabulario en ese modo.

**Top-k**:

- Inicial `k = 50` por A item.
- Subir `k = 100` cuando A tiene `brand_blank` o categoría sparse.
- Evitar global all-pair scoring; filtrar por `matchable_group` compatible antes de evaluar.

**BM25** (opcional, diagnóstico): correr el mismo probe en paralelo para detectar candidatos que TF-IDF se pierde por puro lexical. **No** es el ranker primario.

**Embeddings/FAISS** (opcional, diferido): considerar como capa de recall extra una vez que el deterministic core esté entregando 4k+ matches. No forma parte del primer deliverable.

### Stage 7 — Hard rules (antes del scoring)

Cada regla emite `accept | reject | unknown`. Si **alguna** retorna `reject`, el candidato se descarta antes de llegar al scoring.

Reglas obligatorias:

1. **IDs válidos**: ambos `item_id` son numéricos y existen en sus sets.
2. **`matchable_group` compatible**: mismo group, o adyacente whitelist (e.g. B `Cheese` ↔ A `Dairy & Eggs`).
3. **Brand / private-label compatibility**:
   - National ↔ National: mismo `brand_norm`.
   - Private-label ↔ Private-label: cross-store permitido cuando los demás atributos alinean.
   - National ↔ Private-label: rechazar (excepción explícita opcional para fresh/loose).
4. **Size compatibility** cuando ambos lados tienen size confiable:
   - Mismo `size_family` y mismo unit family.
   - Tolerancia ratio en [0.95, 1.05] = accept; en [0.5, 2.0] = unknown (deja decidir al score); fuera = reject.
5. **Pack compatibility**: rechazar cuando pack es material (single bottle vs 24-pack).
6. **Organic mismatch**: cuando A es organic y existe candidato B organic compatible, rechazar el non-organic.
7. **Form**: powder vs liquid, whole bean vs ground, sliced vs shredded, cat vs dog, adult vs baby, etc.
8. **Storage**: frozen vs shelf-stable es reject; refrigerated vs fresh es unknown.
9. **Alcohol/non-alcohol**: rechazar mismatch.
10. **Flavor token mismatch** (vanilla vs chocolate): rechazar cuando ambos detectados y distintos.

### Stage 8 — Deterministic score

Después de las hard rules, los pares sobrevivientes se rankean con un score ponderado. Pesos sugeridos (alineados con `docs/algorithm_recommendation.md`):

| Componente | Peso |
|---|---:|
| Core name TF-IDF/char similarity | 0.35 |
| Token-level name overlap (post strip de brand y size) | 0.15 |
| Brand compatibility | 0.15 |
| Size + pack compatibility | 0.20 |
| Category / group compatibility | 0.10 |
| Atributos: organic, form, flavor, storage, dietary | 0.05 |

Reglas de los componentes:

- `brand_compatibility`: 1.0 same national brand; 0.85 PL↔PL cross-store; 0.4 cuando un lado tiene blank/inferred; 0.0 cuando known nationals incompatibles.
- `size_compatibility`: 1.0 si ratio ∈ [0.95, 1.05]; partial credit en [0.9, 1.1]; 0 fuera de rango (y normalmente ya cortó la regla dura).
- `category_compatibility`: 1.0 mismo group; menor para adyacentes permitidos.
- Penalties asimétricas: que un lado no tenga el campo no equivale a mismatch.

**Acceptance rule**:

- Por A item: el candidato B con score más alto.
- Requiere `score >= min_score` y `score_top1 - score_top2 >= margin` (calibrar con eval manual).
- Para el primer deliverable: empezar con threshold/margin altos y bajar hasta llegar a 4.000+ matches defendibles.

### Stage 9 — Tie-break B duplicates

B tiene 2.796 grupos duplicate-like. Cuando varios B sobreviven con score parecido para el mismo A:

1. Match exacto de size + pack.
2. Match de form / flavor / organic / storage.
3. Mayor metadata richness (`ingredients`, `tags`, profundidad de `category`).
4. `item_id` estable como tie-break determinístico.

Nunca elegir entre 8 / 15 / 29 oz por puntaje léxico solo.

### Stage 10 — Output + audit + validation

`matches.csv` (deliverable principal):

```csv
item_id_A,item_id_B
2197626,92544
1929544,105624
...
```

`matches_audit.csv` (extra, para defender en interview):

```csv
item_id_A,item_id_B,A_name,B_name,score,top1_top2_margin,source,llm_confidence,reason
```

Donde `source ∈ {deterministic_high, deterministic_unique, deterministic_mid, llm_accept, llm_reject_overruled}`.

**Validación dura del output** (smoke test final):

- Header exacto `item_id_A,item_id_B`.
- Cada `item_id` matchea `^\d+$` y existe en `valid_ids_A` / `valid_ids_B` (las 5 filas en cuarentena no aparecen).
- **No hay duplicados de `item_id_A`** (un único best match por A).
- ≥ 4.000 filas.
- Los ejemplos del PDF resuelven correctamente:
  - A `2197626` → B `92544` (Chobani, 5.3 oz).
  - A `1929544` → B `105624` (Wegmans Organic Tomato Sauce 8 oz, **no** la variante de 15 oz ni la de 29 oz).

### Stage 11 — GPT-5 nano arbiter (opcional, zona gris)

**Cuándo invocar**:

- top-1 score en zona gris (e.g. ∈ [0.55, 0.85]) o margin < 0.05.
- A con `brand_blank` o `brand_inferred` y múltiples candidatos B plausibles.
- Private-label cross-store con score moderado y atributos no del todo claros.
- Fresh/loose produce/meat sin size confiable.

**Cuándo NO invocar**:

- top-1 ≥ threshold alto y margin grande.
- top-1 < threshold de rechazo.
- candidato único post hard rules.

**Prompt** (compacto, structured output):

```
SYSTEM: You are a product matching expert for grocery retailers. Decide whether
the customer would consider these the same product.

USER: A product:
  brand=...
  core_name=...
  size=...
  category=...
  is_private_label=...

Top B candidates with hard-rule status:
  1. id=...  brand=...  name=...  size=...  category=...  PL=...
  2. ...

Respond ONLY with valid JSON:
{
  "best_match_id": "<item_id_B>" or null,
  "same_product_for_customer": true | false,
  "confidence": 0.0..1.0,
  "reason": "<one short sentence>",
  "blocking_issue": "<size|brand|category|...|null>"
}
```

**Garantías**:

- Cache local por hash de los atributos normalizados (idempotencia entre runs).
- Concurrencia con `asyncio.Semaphore`; retry con exponential backoff.
- El LLM **no puede** override hard rules: si la propuesta del LLM viola una regla dura (size incompatible, national↔PL, group incompatible), se descarta y el par queda como `llm_reject_overruled`.
- Las credenciales OpenAI provistas por BetterBasket se cargan desde el archivo de credenciales sin ser logueadas.

---

## 5. Calibración y evaluación

### 5.1 Mini-eval manual

Sin labeled data, los pesos del Stage 8 y los thresholds del Stage 11 son guesses. Procedimiento:

1. Después del primer run end-to-end, samplear 50 pares aceptados aleatorios de `matches.csv`.
2. Evaluar manualmente cada uno como `correct | wrong | partial`.
3. Samplear 30 near-misses (rechazados con score en zona gris).
4. Calcular precision estimada y recall estimado sobre el sample.

### 5.2 Threshold tuning

- Empezar con threshold/margin altos (precision-first).
- Bajar threshold sólo después del eval manual y sólo si quedamos por debajo de 4.000.
- Documentar threshold final, distribución por `source`, y precision estimada en README.

### 5.3 Auto-eval del run completo

Métricas requeridas en el README final:

- Total matches.
- Por `source`: `{deterministic_high, deterministic_unique, deterministic_mid, llm_accept}`.
- Avg score y distribución por bucket.
- Top brands en output (sanity check).
- Top `matchable_group` en output.

---

## 6. Volúmenes, costos, tiempos

| Métrica | Estimado |
|---|---|
| A items input | 233.199 |
| A items quarantined | 5 |
| A items post-scope-filter | 233.194 − ≥48.007 ≈ ≤185.000 |
| Pares evaluados (Stage 6) | ~5–10M (con `k=50`, dependiendo de filtros por group) |
| Pares post-hard-rules (Stage 7) | ~200–500k |
| Decisiones LLM (Stage 11, si activo) | ≤ 5.000 |
| Costo OpenAI estimado (si activo) | < $2 USD |
| Matches finales esperados | 4.000–7.000 (precision-first) |
| Threshold final | a calibrar; documentar en README |
| Tiempo total runtime | < 60 min en CPU |

Breakdown indicativo:

- Stages 1–3 (ingest + parse + normalize): ~5–10 min.
- Stage 4 (scope filter): segundos.
- Stage 5 (taxonomy): segundos.
- Stage 6 (TF-IDF retrieval): ~5–15 min (índices por group).
- Stages 7–9 (rules + score + tie-break): ~5–10 min.
- Stage 10 (output + validation): segundos.
- Stage 11 (LLM, si activo): ~15–25 min con concurrencia.

---

## 7. Supuestos explícitos

1. **Subset interpretation**: el deliverable es un subset high-confidence, no una fila por A item. Justificación: el PDF dice "at least 4.000 matches" y "complete set ~10k", incompatibles con 233k forzados.
2. **Scope filter es aceptable**: descartar Toys, Clothing, Picture Frames, etc. está justificado por la taxonomía y el conteo de exclusiones (≥48.007 filas). Documentado en README.
3. **GPT-5 nano selectivo**: usar el LLM como árbitro está alineado con la consigna; el costo y la calidad lo justifican.
4. **`ic_item_id` no es UPC universal**: 1.23% de B y 0% de A. No se usa como join key.
5. **Duplicados B son aceptables como output target**: si A `Hershey's Candy 23.05oz` matchea uno de N candidatos B con mismo brand+name, elegir el de size 23.05oz. Si hay 2+ con mismo size (re-scrapes), elegir el de mayor metadata + `item_id` estable.
6. **Single match por A**: si A tiene 2 matches igualmente válidos en B, elegir uno (mayor score, luego tie-break). El task es "single closest match", no "all matches".

---

## 8. Plan de ejecución (timeline)

Asumiendo submission lunes 4 mayo, hoy es sábado 2 mayo:

| Día | Bloque | Tarea | Output |
|---|---|---|---|
| Sábado 2/5 mañana | hecho | Audit + retrieval probe | `docs/dataset_audit.md`, `docs/algorithm_recommendation.md`, `docs/audit_stats.json`, `docs/retrieval_probe_results.json`, `scripts/audit_data.py`, `scripts/retrieval_probe.py` |
| Sábado 2/5 tarde | 4h | Stages 1–3 (ingest, parse, normalize) + tests | `betterbasket_matcher/io.py`, `normalize.py`, `taxonomy.py`, fixtures |
| Sábado 2/5 noche | 3h | Stages 4–5 + Stage 6 (TF-IDF retrieval) | `scope.py`, `retrieval.py` |
| Domingo 3/5 mañana | 3h | Stages 7–9 (rules + score + tie-break) + primer run end-to-end | `rules.py`, `scoring.py`, `pipeline.py`, primer `matches.csv` |
| Domingo 3/5 tarde | 3h | Mini-eval manual + threshold tuning | `eval/manual_eval.md`, ajustes a `scoring.py` |
| Domingo 3/5 noche | 3h | Re-run + Stage 10 validation + Stage 11 opcional | `matches.csv`, `matches_audit.csv` |
| Lunes 4/5 mañana | 3h | README polish + cleanup + smoke test final | repo listo |
| Lunes 4/5 mediodía | 1h | Submission email | sent |

Buffer: la noche del domingo tiene 3h de margen para re-run.

---

## 9. Riesgos y mitigaciones

| Riesgo | Probabilidad | Impacto | Mitigación |
|---|---|---|---|
| Filas malformadas A contaminan output | baja | crítico | Validación `^\d+$` + cuarentena en Stage 1. Validador final verifica IDs en sets numéricos. |
| Brand inference muy ruidosa | media | medio | Validar inferred brands contra B brand list; flag `brand_inferred=True`; bajar peso en score. |
| Retrieval por marca privada retrieva ruido | alta (sin mitigación) | alto | Suppression PL en query (probe lo confirma). Categoría hard rule corta `Great Lakes` cheese. |
| Duplicados B → elección arbitraria | alta | bajo | Tie-break: size exacta → atributos → metadata richness → `item_id` estable. |
| Quedamos cortos de 4.000 matches | baja | crítico | Bajar threshold/margin. Activar LLM en zona gris. |
| Output CSV malformado | baja | crítico | Smoke test final: header, IDs numéricos, no duplicates de `item_id_A`, ejemplos PDF. |
| LLM rate-limited / API caída | baja | medio | LLM es opcional. Fallback: deterministic-only. Cache local. |
| Costo LLM se dispara | baja | medio | Cap `max_calls` con assert. Logging del running cost. |
| `matchable_group` mapping deja A sin candidatos | media | alto | Empezar con narrow links cross-group; iterar mirando productos sin match en eval. |
| Embeddings necesarios para recall extra | media | bajo | Capa diferida; el deterministic core ya cubre los ejemplos del PDF. |

---

## 10. Deliverables

### Obligatorios (del PDF)

1. `matches.csv` — `(item_id_A, item_id_B)` con ≥ 4.000 filas, single best B per A, sin duplicados de `item_id_A`.
2. Código Python ejecutable y reproducible que genera el output.

### Extra (lo que diferencia)

3. `matches_audit.csv` — score, source, top1_top2_margin, llm_confidence, reason por par.
4. `README.md` — overview interview-ready, headline facts, pipeline summary, validación de output, layout, comandos para reproducir audit y probe.
5. `solution.md` (este archivo) — narrativa pulida.
6. `solutioneasyexplained.md` — versión plain-Spanish.
7. `app.md` — vista visual.
8. `docs/dataset_audit.md` + `docs/algorithm_recommendation.md` + JSON reproducibles + `scripts/audit_data.py` + `scripts/retrieval_probe.py`.
9. `requirements.txt` con versiones pinned.
10. `tests/` con unit tests para Stages 1–3 y Stage 7.
11. `eval/manual_eval.md` con los 50 pares evaluados.

---

## 11. Apéndice: alternativas descartadas

### Por qué no LLM-first

- 12.946.275.684 pares — imposible.
- Aún reduciendo el universo, el LLM **igual necesita** retrieval, normalización y reglas duras para no equivocar 8 oz vs 15 oz tomato sauce.
- No determinista, hace audit costoso.

### Por qué no UPC-first

- A: 0 campos UPC-like. No hay plan posible centrado en UPC.

### Por qué no fuzzy/Levenshtein-only

- Funciona OK para typos. Falla en paráfrasis ("Whole Milk" vs "Vit D Milk").
- No maneja PL cross-store.
- B duplicate-like groups (8/15/29 oz) requieren reglas de size, no string distance.

### Por qué no BM25 + FAISS + RRF como motor principal

- El dry-run del repo (`scripts/retrieval_probe.py`) mostró que BM25 con brand_included tiene trampas léxicas críticas en private label: rank 5 para `Great Value Organic Tomato Sauce 8 oz`, top-1 `Colgate ... Great Regular Flavor`. TF-IDF word + char rankea ambos ejemplos en #1 sin necesidad de fusion ni de un segundo ranker.
- FAISS con embeddings agrega complejidad y dependencia de un modelo grande. MTEB +1.5 puntos no compensan el costo del primer entregable.
- **BM25 sigue como diagnóstico** secundario; **embeddings/FAISS quedan como capa opcional posterior** para recall extra.

### Por qué no entrenar un classifier supervised

- Requiere labeled data. No tenemos. Out of scope para 3 días.

### Por qué no cross-encoder rerank

- Mejoraría rerank con calidad superior, pero ~1M pares × 30ms en CPU = ~8 horas. Inaceptable.

### Por qué no exponer un servicio (FastAPI)

- El task pide un script. Modular para convertir a servicio luego.

---

## 12. Glosario técnico

- **Entity resolution / record linkage**: matchear records de fuentes distintas que se refieren a la misma entidad real, sin clave de join exacta.
- **UPC**: Universal Product Code, código de 12 dígitos. Cuando ambos lados lo exponen, es la forma más confiable de matching.
- **TF-IDF**: term frequency × inverse document frequency. Ranking lexical clásico.
- **TF-IDF word + char n-grams**: dos vectorizers (word `(1, 2)` y char-wb `(3, 5)`) concatenados. Mejora robustez ante variaciones ortográficas y typos manteniendo precision lexical.
- **BM25**: Okapi BM25, ranking lexical estadístico, evolución de TF-IDF. Útil como diagnóstico, no como motor primario en este dataset por las trampas léxicas en private label.
- **Embeddings**: vectores densos producidos por un modelo de lenguaje. Cosine similarity ≈ similitud semántica. **Diferidos** acá como capa opcional de recall.
- **FAISS**: librería de Meta para búsqueda vectorial rápida. **Diferida** acá.
- **Private label**: marca propia de un retailer (Great Value de Walmart, Wegmans-brand de Wegmans).
- **National brand**: marca presente en múltiples retailers (Coca-Cola, Chobani).
- **Matchable group**: taxonomía intermedia compartida que permite comparar `category_0..2` entre A y B sin acoplarse a las taxonomías originales.
- **Hard rule**: regla que rechaza un par antes del scoring (size, group, brand/PL, pack, organic, form, storage, flavor, alcohol).
- **Score determinístico**: score calculado con una fórmula fija ponderada — sin random, sin LLM.
- **Audit trail**: registro `matches_audit.csv` con score, source, margin, confidence y reason de cada match para revisión manual.

---

**Fin del documento.** El siguiente paso de implementación es Stage 1 (`betterbasket_matcher/io.py`) según `docs/algorithm_recommendation.md` §6.
