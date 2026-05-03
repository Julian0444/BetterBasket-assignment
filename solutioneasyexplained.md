# Solución explicada fácil — para alguien que recién empieza

> Versión "sin asumir nada" de [solution.md](solution.md). Si ya tenés background en ML, retrieval o NLP, andá directo a `solution.md`. Si no, empezá acá.
>
> Las fuentes de verdad técnicas son `docs/dataset_audit.md` y `docs/algorithm_recommendation.md`. Este documento cuenta lo mismo en lenguaje cotidiano.

---

## 1. ¿Qué nos están pidiendo?

Imaginate que sos el dueño de un supermercado pequeño. Querés saber: **"¿mi precio del yogur Chobani es competitivo?"**. Para eso, necesitás comparar tu precio contra el del mismo yogur en Walmart, Wegmans, Costco, etc.

El problema: **Walmart no te avisa qué producto es cuál**. Te dan una lista de 233.000 productos con nombres tipo `"Chobani Whole Milk Greek Yogurt Honey Blended 5.3 oz Cup"`, y vos tenés tu propia lista de Wegmans con `"Chobani Greek Honey Blended Yogurt"`. **Vos tenés que figurar cuáles son el mismo producto.**

Eso es lo que hace BetterBasket: **emparejar productos entre supermercados** para que sus clientes (otros supermercados) puedan comparar precios.

### El task concreto

```
Te dan:
  • Lista A: 233.199 productos de Walmart (con nombre, marca, descripción, etc.)
  • Lista B: 55.516 productos de Wegmans (mismo formato)

Tenés que devolver:
  • Un archivo matches.csv que diga "el producto X de Walmart =
    el producto Y de Wegmans"
  • Mínimo 4.000 emparejamientos (matches)
  • Cada emparejamiento debe ser real: un cliente debería decir
    "sí, son esencialmente el mismo producto"
```

---

## 2. ¿Por qué no es trivial? (Las 5 razones)

### Razón 1: Los volúmenes son enormes

Si quisieras comparar **cada producto de A contra cada producto de B**, harías:

```
233.199 × 55.516 = 12.946.275.684 comparaciones
```

Eso son **casi 13 mil millones**. Imposible mandarlas todas a un LLM o a un cross-encoder. Hay que ser inteligente: solo comparar productos que tienen chance real de matchear.

### Razón 2: No tenemos el "DNI del producto" (UPC)

En el mundo real, cada producto tiene un código de 12 dígitos llamado **UPC** — el código de barras de la caja. Si dos productos tienen el mismo UPC, **son literalmente el mismo producto**, no hay duda.

**El problema en este dataset**: Walmart no trae UPC en absoluto, y Wegmans solo trae un `ic_item_id` parecido a UPC en 681 / 55.516 filas (1.23%). Como Walmart no tiene contraparte, **el join por UPC no es viable**.

### Razón 3: Los datos vienen sucios y con columnas señuelo

Cuando abrimos los CSV, encontramos que muchas columnas que parecen útiles vienen **100% vacías**:

- `name_clean`, `category`, `department`, `subcategory`, `size_raw` — vacías en ambos archivos.
- `is_private_label`, `item_type` (Walmart) y `is_organic` (Wegmans) — también vacías.

**Conclusión**: tenemos que **reconstruir** marca, categoría, tamaño, "es marca propia", "es orgánico", etc. desde los datos crudos (`name`, `brand_raw`, `item_info`, `sizing_comp`, `tags`).

### Razón 4: Hay 5 filas rotas en Walmart

Walmart trae **5 filas con `item_id` que no es un número** — vienen con las columnas corridas, e.g. un `item_id` que dice `" | Pack of 12"` o `"Acrylic Tortoise Thick Gripjaw Non Holdmetal Small Hair"`. Si dejamos que esas filas pasen, terminamos con basura tokenizada y, peor, con `item_id` no numéricos en el output final.

**Conclusión**: lo primero que hace el pipeline es validar `item_id` con la regex `^\d+$` y poner las 5 filas malas en cuarentena. **Antes de cualquier otra cosa.**

### Razón 5: Los productos no siempre tienen un equivalente

Walmart vende juguetes, ropa, herramientas. Wegmans solo vende comida y artículos de supermercado. Si en A tenés un producto "Mainstays Picture Frame 5x7", **no hay nada en B con qué comparar**. La auditoría identificó al menos **48.007 filas** de A en categorías sin contraparte en B (Toys, Clothing, Home Improvement, Sports & Outdoors, Party & Occasions, Office Supplies, Auto & Tires, Electronics, Arts Crafts & Sewing, Jewelry, Books, Cell Phones).

**Conclusión**: hay que filtrar **antes** de comparar. Es trabajo gratis y deja el matcher trabajando solo con A relevante.

---

## 3. La idea grande: pensá como un humano lo haría

Si vos, persona, tuvieras que hacer este task a mano, ¿cómo lo harías?

1. **Tirarías a la basura las filas rotas** (las 5 con `item_id` no numérico).
2. **Limpiarías los nombres**. "(12 pack) Great Value Tomato Sauce, 8 oz" → "Great Value Tomato Sauce 8 oz" (y guardás `pack=12` aparte).
3. **Reconstruirías marca, categoría, tamaño** de los datos crudos porque las columnas pre-cleaned están vacías.
4. **Descartarías lo que no aplica** (juguetes, ropa, electrónica): Wegmans no vende eso.
5. **Mapearías categorías a una taxonomía intermedia** (`dairy`, `pantry`, `frozen`, ...) para poder comparar entre Walmart y Wegmans.
6. **Para cada producto de A, buscarías candidatos plausibles en B** — no los 55.000, solo los 50 más prometedores.
7. **Aplicarías reglas de sentido común**: "¿mismo tamaño?", "¿misma forma física?", "¿marca compatible?". Cualquier "no" elimina el candidato.
8. **Le pondrías un puntaje a los sobrevivientes** y elegirías el mejor.
9. **En casos dudosos, pedirías ayuda** a alguien que sepa.

Eso es exactamente lo que hace nuestro pipeline. La diferencia es que en vez de hacerlo a mano (te llevaría meses), lo hacemos con código. Y para el paso 9 ("pedir ayuda") usamos un modelo de IA — **GPT-5 nano**, **opcional** y **solo en zona gris**.

---

## 4. Los 3 tipos de match

### Tipo 1: Idéntico con UPC (el caso fácil)

```
A: "Coca-Cola 12 oz" UPC=049000028911
B: "Coca-Cola 12 oz" UPC=049000028911

→ Mismo UPC = mismo producto. CASO RESUELTO.
```

**En este dataset: ❌ no aplica** (Walmart no tiene UPC). Pero conceptualmente es el caso más fácil.

### Tipo 2: Idéntico sin UPC (mismo producto, distintos catálogos)

```
A (Walmart): "Chobani Whole Milk Greek Yogurt Honey Blended 5.3 oz Cup"
B (Wegmans): "Chobani Greek Honey Blended Yogurt"  +  size: 5.3 oz

→ Misma marca (Chobani), mismo concepto, mismo tamaño. Es match.
```

Lo confirma: **marca exacta** + **nombre core** + **tamaño**.

### Tipo 3: Equivalente conceptual (private label)

**Private label** = "marca propia del supermercado". Cada cadena tiene la suya:

- Walmart: `Great Value`, `Marketside`, `Freshness Guaranteed`, `Equate`, `Mainstays`, `Bettergoods`, etc.
- Wegmans: `Wegmans` (la marca propia se llama igual que la cadena) + tag `wegmans brand`.

```
A (Walmart): "Great Value Organic Tomato Sauce, 8 oz"   (Walmart PL)
B (Wegmans): "Wegmans Organic Tomato Sauce"  +  size 8 oz   (Wegmans PL)

→ Marcas distintas, ambos PL, mismo concepto, mismo tamaño. Es match.
```

Este es el caso **más difícil** porque no hay un atributo "duro" igual. Pero **OJO con el tamaño**: Wegmans tiene también la misma salsa en 15 oz y 29 oz, y son productos distintos. La regla dura de tamaño es la que evita elegir mal.

---

## 5. Las herramientas técnicas explicadas en cristiano

### 5.1 TF-IDF — el ranker de palabras (nuestro motor principal)

**¿Qué es?** Una técnica clásica que rankea documentos por las palabras que comparten con la query, ponderando por qué tan **raras** son esas palabras en el corpus. Si "tomato" aparece en muchos productos, pesa poco; si "wegmans" aparece poco, pesa mucho.

**Variante que usamos**: TF-IDF con dos vectorizers en paralelo:

- **Word (1, 2)-grams**: pesa palabras solas y pares de palabras consecutivas.
- **Char-wb (3, 5)-grams**: pesa secuencias de caracteres dentro de cada palabra. Esto da robustez a typos y variaciones ortográficas (`organic` vs `organics`, `mountain dew` vs `mtn dew`).

Concatenamos los dos vectores y eso es el "espacio de búsqueda". Para cada producto de A, buscamos los 50 productos B con vectores más cercanos.

**Por qué TF-IDF y no otro algoritmo**: el dry-run del repo (`scripts/retrieval_probe.py`) probó tanto TF-IDF word+char como BM25 sobre las 55.516 filas reales de Wegmans. TF-IDF rankeó ambos ejemplos del PDF en **#1**. BM25 con marca incluida hundió a `Great Value Organic Tomato Sauce 8 oz` al **rank 5** porque la palabra "Great" infló candidatos como `Colgate Great Regular Flavor` y `Great Lakes Provolone Cheese`. **TF-IDF gana**.

### 5.2 BM25 — opcional, sólo como diagnóstico

**¿Qué es?** Un primo más sofisticado de TF-IDF. Excelente para muchos casos, pero en private label tiene trampas léxicas duras (las palabras "Great" y "Value" arruinan el ranking). Lo dejamos como segundo ranker de diagnóstico para detectar cuándo TF-IDF se está perdiendo algo, no como motor principal.

### 5.3 Embeddings — diferido para "después"

**¿Qué son?** Vectores densos producidos por un modelo de lenguaje. Productos parecidos quedan cerca en un mapa de muchas dimensiones; productos distintos quedan lejos.

**Por qué los dejamos para después**: son fuertes para paráfrasis ("Whole Milk" ≈ "Vit D Milk"), pero más débiles para distinguir tamaños, packs, sabores y variantes numéricas — que es **justo lo que más nos hiere** acá (Wegmans tomato sauce 8/15/29 oz). Como capa de recall extra son interesantes una vez que el deterministic core ya entrega 4.000 matches sólidos.

### 5.4 GPT-5 nano (LLM) — el árbitro opcional

**¿Qué es?** El modelo de lenguaje más chico de la familia GPT-5 vía API. Lo usamos **solo** cuando el pipeline determinístico ya hizo su trabajo y nos quedamos con candidatos en zona gris (puntaje moderado, atributos no del todo claros). Le mandamos un prompt compacto con un JSON estructurado de respuesta:

```json
{
  "best_match_id": "<item_id_B>" or null,
  "same_product_for_customer": true,
  "confidence": 0.0..1.0,
  "reason": "<short>",
  "blocking_issue": "<size|brand|category|...|null>"
}
```

**Reglas**: el LLM **no puede override** una hard rule. Si las reglas duras dicen que size es incompatible, el LLM no puede aceptar ese par. El LLM aporta criterio donde las reglas no llegan, no autoridad sobre las reglas.

---

## 6. Las 11 etapas del pipeline (paso a paso)

### Etapa 1 — Ingest + validar

**Misión**: leer los CSV en streaming y tirar las filas rotas.

- `csv.DictReader` sobre A y B, sin pandas (footprint chico).
- **Validar `item_id` con la regex `^\d+$`**. Las 5 filas de A con `item_id` no numérico se aíslan en `quarantine_a.csv` y **no entran al pipeline**.
- Construir los sets numéricos `valid_ids_A` y `valid_ids_B`. El validador final del CSV de salida los usa para verificar que cada `item_id` exista de verdad.

### Etapa 2 — Parsear JSON y tags

**Misión**: convertir las columnas con JSON crudo en estructuras manejables.

- `item_info` y `sizing_comp` (en A y en B): parser tolerante. Si parsea pero no es dict, devolver `{}`.
- `tags` de B: parser tolerante (JSON list → array Postgres `{a,b,c}` → split por coma → fallback []).
- `tags` de A: vacío en todas salvo las 5 malformadas, no se usa.

### Etapa 3 — Normalizar

**Misión**: convertir cada producto en un "registro estandarizado" con campos limpios y comparables.

Cada item válido se proyecta a un objeto con campos:

`item_id`, `source`, `name_raw`, `name_norm`, `core_name`, `brand_norm`, `brand_inferred`, `is_private_label`, `category_0..2`, `matchable_group`, `size_value`, `size_unit`, `size_family`, `pack_count`, `unit_size`, `total_size`, `is_organic`, `storage_type`, `form`, `flavor_tokens`, `retrieval_text`.

Reglas:

- **Brand**:
  - A: `brand_raw` cuando está poblado. Cuando está blank (45.87% de A; **63.89% en Food**), inferimos del prefijo del `name` contra una whitelist (PL Walmart + brands top de B). Marcamos `brand_inferred=True`.
  - B: `brand_raw` directo (cobertura 90%).
- **Private label**: detectado explícitamente — A por whitelist (`great value`, `marketside`, ...), B por `brand_raw == Wegmans` o tag `wegmans brand`.
- **Categorías**: parseadas de `item_info.category_0..3` en ambos lados.
- **Size**: A primero del `name`, fallback a `sizing_comp`; B primero de `sizing_comp.size_user_friendly` (95.51% cobertura), fallback a `name`.
- **Pack count**: parseado **separado** del per-unit size.
- **Organic / form / storage / flavor**: keyword extraction sobre `name`, complementado por B `tags`.

**Ejemplo concreto**:

```
INPUT (raw row de A):
  name: "(12 pack) Great Value Organic Tomato Sauce, 8 oz"
  brand_raw: ""  (vacío)
  item_info: '{"category_0":"Food","category_1":"Pantry",...}'
  sizing_comp: '{"size_user_friendly":null,...}'

OUTPUT (registro normalizado):
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

### Etapa 4 — Filtrar lo irrelevante (scope filter)

**Misión**: descartar productos de A que no tienen chance de tener match en B.

- **Conservar** A en categorías comparables: Food, Personal Care, Household Essentials, Health and Medicine, Baby, Pets, Beauty, parte selectiva de Home (Kitchen & Dining sí, Decor/Frames/Furniture/Bedding no).
- **Descartar** A en `Toys`, `Clothing`, `Home Improvement`, `Sports & Outdoors`, `Party & Occasions`, `Office Supplies`, `Auto & Tires`, `Electronics`, `Arts Crafts & Sewing`, `Jewelry`, `Books`, `Cell Phones`. Esto saca al menos **48.007 filas**.

### Etapa 5 — Taxonomía compartida (`matchable_group`)

**Misión**: como Walmart y Wegmans usan taxonomías distintas, mapeamos ambos a una **taxonomía intermedia común** con grupos como:

`pantry`, `snacks`, `candy`, `beverages`, `dairy`, `cheese`, `frozen`, `produce`, `meat`, `seafood`, `bakery`, `prepared_foods`, `baby`, `pets`, `household`, `personal_care`, `health`, `beauty`, `kitchen_home`, `wine_beer_spirits`.

Esto es el "bridge" para comparar `category` entre las tiendas.

### Etapa 6 — Generar candidatos (TF-IDF word + char)

**Misión**: para cada uno de los ~185.000 productos de A que sobrevivieron al scope, encontrar los 50 productos de B más prometedores.

- Construir un índice **TF-IDF word `(1, 2)` + char-wb `(3, 5)`** sobre B, idealmente uno por `matchable_group`.
- El **texto de retrieval se construye distinto** según el tipo de marca:
  - **Marca nacional**: incluye brand fuerte. `"chobani greek honey blended yogurt 5.3 oz dairy yogurt"`.
  - **Marca propia**: **suprime** los tokens de marca de tienda (`great value`, `marketside`, `wegmans`, etc.) y se apoya en core name + size + group + organic. `"organic tomato sauce 8 oz pantry organic canned"`.
- Para cada A item: top-k = 50 candidatos B.
- `k = 100` cuando A tiene `brand_blank` o categoría sparse.

**Por qué la asimetría**: el dry-run del repo lo confirmó. Sin suppression, BM25 manda a `Great Lakes Provolone Cheese` al top-1 cuando buscamos `Great Value Provolone Deli Style Sliced Cheese 8 oz`. Con suppression, ese ruido baja dramáticamente. TF-IDF aguanta mejor sin suppression para los ejemplos del PDF, pero es buena práctica suprimir igual para no confiar en suerte.

### Etapa 7 — Reglas duras (sentido común)

**Misión**: descartar candidatos que claramente no son match, sin importar lo parecidos que sean los nombres.

Para cada par (A, B), aplicamos un checklist en cascada. Cualquier "REJECT" elimina el par antes del scoring.

1. **IDs válidos** — ambos numéricos y existentes.
2. **`matchable_group` compatible** — mismo group o adyacente whitelist (e.g. B `Cheese` ↔ A `Dairy & Eggs`).
3. **Marca / private label compatibility**:
   - National ↔ National: misma marca exacta.
   - Private-label ↔ Private-label: cross-store permitido.
   - National ↔ Private-label: rechazar (excepción explícita opcional para fresh/loose).
4. **Tamaño compatible** — ratio en [0.95, 1.05] = OK; [0.5, 2.0] = a decidir; fuera = reject.
5. **Pack compatible** — single bottle vs 24-pack = reject.
6. **Organic mismatch** — si A es organic y existe candidato B organic compatible, rechazar el non-organic.
7. **Form** — powder vs liquid, whole bean vs ground, sliced vs shredded, cat vs dog, adult vs baby.
8. **Storage** — frozen vs shelf-stable = reject; refrigerated vs fresh = unknown.
9. **Alcohol/non-alcohol** — reject mismatch.
10. **Flavor** — vanilla vs chocolate cuando ambos están detectados = reject.

### Etapa 8 — Score determinístico

**Misión**: poner un puntaje numérico a los pares sobrevivientes.

Pesos sugeridos:

```
score = 0.35 × similitud_de_nombre_TF-IDF
      + 0.15 × overlap_de_tokens (post strip de brand y size)
      + 0.15 × compatibilidad_de_marca
      + 0.20 × compatibilidad_de_size_y_pack
      + 0.10 × compatibilidad_de_categoría/grupo
      + 0.05 × atributos (organic, form, flavor, storage, dietary)
```

- `compatibilidad_de_marca`: 1.0 same national; 0.85 PL↔PL cross-store; 0.4 cuando un lado tiene blank/inferred; 0.0 cuando known nationals incompatibles.
- `compatibilidad_de_size_y_pack`: 1.0 mismo size canónico y mismo pack; partial credit en rangos cercanos; 0.0 fuera (y normalmente ya cortó la regla dura).
- `compatibilidad_de_categoría`: 1.0 mismo group; menor para adyacentes permitidos.
- Penalties asimétricas: que un lado **no tenga el campo** no equivale a mismatch.

**Aceptación**: por A, elegimos el candidato B con mayor score. Pero exigimos `score ≥ min_score` y un margen `score_top1 − score_top2 ≥ margin`. Si no, se queda sin match (o pasa al árbitro LLM si está activo).

### Etapa 9 — Tie-break para duplicados de B

B tiene 2.796 grupos duplicate-like. Cuando dos B sobreviven con score parecido para el mismo A:

1. Match exacto de size + pack.
2. Match de form / flavor / organic / storage.
3. Mayor metadata richness (`ingredients`, `tags`, profundidad de `category`).
4. `item_id` estable como tie-break determinístico.

Nunca elegir entre 8 / 15 / 29 oz por puntaje léxico solo.

### Etapa 10 — Output + auditoría + validación dura

Generamos:

1. **`matches.csv`** (deliverable principal, dos columnas):
   ```csv
   item_id_A,item_id_B
   2197626,92544
   1929544,105624
   ```
2. **`matches_audit.csv`** (extra, para defender en interview): score, source, top1_top2_margin, llm_confidence, reason, A_name, B_name.
3. **`README.md`**: pipeline, métricas, threshold, sample eval.

**Antes de declarar el run válido, corremos un smoke test**:

- Header exacto `item_id_A,item_id_B`.
- Todos los `item_id_A` y `item_id_B` matchean `^\d+$` y existen en los sets numéricos originales (las filas en cuarentena **no pueden aparecer**).
- **No hay duplicados de `item_id_A`** (un best match por A).
- Hay al menos 4.000 filas.
- Los ejemplos del PDF resuelven bien: A `2197626` → B `92544`, A `1929544` → B `105624` (la **de 8 oz**, no la variante de 15 oz ni la de 29 oz).

### Etapa 11 — LLM como árbitro (opcional)

**Misión**: usar GPT-5 nano para resolver casos donde el deterministic score no es claro.

**Cuándo lo llamamos**:

- Score top-1 en zona gris.
- Margen pequeño entre top-1 y top-2.
- A con `brand_blank` o `brand_inferred` y múltiples candidatos B plausibles.
- Private-label cross-store con score moderado.
- Fresh/loose sin size confiable.

**Cuándo NO**:

- Score alto y margen grande → aceptar sin LLM.
- Score muy bajo → rechazar sin LLM.
- Único candidato post hard rules → aceptar sin LLM.

**Optimizaciones técnicas**:

- **Cache local**: si la misma decisión se repite, no la pedimos de nuevo.
- **Async + Semaphore**: varias consultas en paralelo, respetando rate limits.
- **Retry con backoff exponencial** en 429/5xx.
- **Hard rules ganan**: el LLM no puede aceptar un par que viola una regla dura.
- **Credenciales**: se cargan desde el archivo de credenciales sin loguearse.

Costo estimado si se activa: **menos de $2 USD** total con ~5.000 calls.

---

## 7. ¿Cómo sabemos si funciona? (evaluación)

No hay ground truth completo, así que **estimamos calidad** con un sample manual.

### Mini-eval manual de 50 pares

1. Después del primer run, sampleamos 50 pares random del `matches.csv`.
2. Yo (Julian) los evalúo a mano: ¿correcto, incorrecto, parcial?
3. Calculamos precision estimada.

También sampleamos 30 "near-misses" (pares con score gris que rechazamos) para ver si perdimos buenos.

### Precision vs Recall

- **Precision**: de los matches que dije, ¿qué % son correctos?
- **Recall**: de los matches reales que existen, ¿qué % encontré?

Hay un **trade-off**: subir threshold → más precision, menos recall. Bajarlo → menos precision, más recall.

**Nuestra estrategia**: precision-first. Apuntamos a precision alta aunque eso signifique recall menor. Razón: en pricing real, un match falso es peor que un match faltante.

---

## 8. ¿Cuánto cuesta y cuánto tarda?

| Recurso | Cantidad |
|---|---|
| Tu computadora (CPU) | < 60 min end-to-end |
| OpenAI API (si se activa el árbitro) | < $2 USD |
| RAM | ~1–2 GB pico (índices TF-IDF) |
| Disco | ~250 MB |
| Internet | ~5 MB descarga (1 vez) + LLM calls si se activa |

**Comparación cualitativa con alternativas** (orden de magnitud, no resultados medidos; las cifras finales se reportan después de correr el pipeline y el eval manual):

| Approach | Lectura cualitativa |
|---|---|
| Solo fuzzy / Levenshtein | Rápido y gratis, pero alto riesgo de falsos positivos en paráfrasis y en duplicados por size. No es viable como única señal. |
| BM25 con brand incluida (sin reglas) | El probe del repo lo evidenció: en private label colapsa por trampas léxicas (`Great Value` → `Great Lakes`, `Great Regular Flavor`). Útil sólo como diagnóstico secundario. |
| **TF-IDF word + char + hard rules (este plan)** | **Recomendado para el primer deliverable**: el probe rankeó ambos ejemplos del PDF en #1, y las hard rules cubren los traps de size/pack/PL. |
| + GPT-5 nano en zona gris (opcional) | Capa adicional para mejorar recall en casos grises. Costo y latencia acotados por el cap de calls. Valor real a confirmar tras el primer run. |
| LLM en todos los candidatos post-retrieval | Conceptualmente posible, pero costo y latencia escalan con el número de pares; sin reglas duras igual no resuelve los traps de size. |
| LLM-first sobre el producto cartesiano completo | Inviable: con casi 13 mil millones de pares, ningún presupuesto razonable de costo o tiempo lo cubre. Útil sólo como ejemplo pedagógico de por qué hace falta retrieval. |

---

## 9. Las decisiones importantes (y por qué las tomamos así)

### Decisión 1: precision-first (no maximizar cantidad)

El PDF dice "single closest match" — no dice "todos los matches posibles". Pide ≥ 4.000 como **piso**. Defender 5–7k buenos en una entrevista es más fácil que defender 12k con basura. En pricing real, un match falso es peor que un match faltante.

### Decisión 2: deterministic-first, no LLM-first

12.946.275.684 pares hace inviable comparar todo. Aún reduciendo, el LLM **igual necesita** retrieval, normalización y reglas duras antes para no errar en obvios (8 oz vs 15 oz tomato sauce). Reglas son gratis y precisas; LLM aporta criterio donde reglas no llegan.

### Decisión 3: TF-IDF word + char como motor de retrieval (no BM25/FAISS/RRF)

Lo probamos en `scripts/retrieval_probe.py` sobre las 55.516 filas reales:

- TF-IDF word + char rankea ambos ejemplos del PDF en **#1**.
- BM25 con marca incluida hunde a `Great Value Organic Tomato Sauce 8 oz` al **rank 5** (top-1: `Colgate Fluoride Toothpaste, Great Regular Flavor, 3 Value Pack`).
- `Great Value Provolone` → BM25 manda `Great Lakes Provolone Cheese` al top-1.

BM25 queda como diagnóstico secundario. Embeddings/FAISS quedan como capa opcional posterior.

### Decisión 4: validación numérica de `item_id` desde la primera etapa

Walmart trae 5 filas con `item_id` no numérico (columnas corridas). Si las dejamos pasar, contaminamos retrieval, scoring, **y** el output final. La regla `^\d+$` en Stage 1 + cuarentena cierra esa puerta de entrada.

### Decisión 5: descartar UPC

A no tiene UPC. B tiene 681 (1.23%). No es viable como join key. El problema es 100% entity resolution.

### Decisión 6: scope filter (descartar Toys, Clothing, etc.)

Wegmans no vende picture frames. Buscarles match es 100% trabajo perdido. Filtrarlos antes ahorra ≥ 48.007 filas de A que no tienen contraparte plausible.

### Decisión 7: GPT-5 nano selectivo, opcional, sin override de hard rules

El LLM se reserva para zona gris. **No** puede aceptar pares que las reglas duras rechazan (size incompatible, national↔PL, group incompatible). Esto blinda la precision y hace el output reproducible aún sin LLM.

---

## 10. Mini glosario

| Término | Significado en una frase |
|---|---|
| **UPC** | Código universal de producto (12 dígitos), el "DNI" del producto. |
| **Entity resolution** | Tarea de matchear records de dos fuentes que se refieren a la misma cosa real, sin clave de join. |
| **National brand** | Marca presente en muchos retailers (Coca-Cola, Chobani). |
| **Private label** | Marca propia de un retailer (Great Value de Walmart, Wegmans-brand de Wegmans). |
| **TF-IDF** | Ranking lexical clásico (term frequency × inverse document frequency). |
| **TF-IDF word + char n-grams** | Variante que combina vectorizers de palabras y de secuencias de caracteres; robusta a typos. |
| **BM25** | Otro ranking lexical, primo de TF-IDF. Útil como diagnóstico, no como motor primario en este dataset. |
| **Embeddings** | Vectores que representan texto con similitud semántica. Diferidos acá. |
| **FAISS** | Librería para búsqueda vectorial rápida. Diferida acá. |
| **GPT-5 nano** | Modelo OpenAI provisto por BetterBasket, usado como árbitro opcional. |
| **`matchable_group`** | Taxonomía intermedia compartida entre Walmart y Wegmans para comparar categorías. |
| **Hard rule** | Regla que rechaza un candidato sí o sí (sin importar el score). |
| **Threshold** | Umbral mínimo de score para aceptar un match. |
| **Margin** | Diferencia mínima entre top-1 y top-2 para aceptar el match. |
| **Precision** | De lo que entregué, ¿qué % es correcto? |
| **Recall** | De lo que existe, ¿qué % encontré? |
| **Cuarentena** | Apartar filas malformadas (las 5 con `item_id` no numérico) antes de procesar. |
| **Audit trail** | Registro de "cómo se llegó a cada decisión" (`matches_audit.csv`). |

---

## 11. Si querés profundizar más

- **`solution.md`** — versión técnica completa con fundamentación profunda, fórmulas, alternativas descartadas con razón.
- **`app.md`** — vista visual con diagramas ASCII.
- **`docs/dataset_audit.md`** — auditoría real de los CSVs, fuente de verdad de los números.
- **`docs/algorithm_recommendation.md`** — la receta del algoritmo, fuente de verdad del plan.
- **`scripts/audit_data.py`** y **`scripts/retrieval_probe.py`** — los scripts reproducibles que generaron las dos fuentes anteriores.

---

**Si después de leer esto algo no te queda claro**, mandame qué parte y la reescribo. La idea es que cualquier persona que lea este archivo pueda entender qué hace el pipeline sin background en ML.
