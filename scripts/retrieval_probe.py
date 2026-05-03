#!/usr/bin/env python3
"""Lightweight retrieval dry-run for BetterBasket product matching.

This is not the final matching pipeline. It builds lexical retrieval indexes
over store B and probes a few known store A examples to answer whether TF-IDF
style retrieval is a viable first stage.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

try:
    from scipy.sparse import hstack
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.preprocessing import normalize

    HAS_SKLEARN = True
except Exception:
    HAS_SKLEARN = False

try:
    from rank_bm25 import BM25Okapi

    HAS_BM25 = True
except Exception:
    HAS_BM25 = False

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from audit_data import (  # noqa: E402
    DEFAULT_A,
    DEFAULT_B,
    ParsedSize,
    best_size,
    is_blank,
    normalize_text,
    parse_json_dict,
    parse_tags,
    private_label_a,
    private_label_b,
    remove_size_tokens,
)


DEFAULT_JSON_OUT = Path("docs/retrieval_probe_results.json")
DEFAULT_MARKDOWN_OUT = Path("docs/retrieval_probe_results.md")
PRIVATE_LABEL_BRANDS = {
    "great value",
    "marketside",
    "freshness guaranteed",
    "bettergoods",
    "sam s choice",
    "wegmans",
}

DEFAULT_A_PROBES = {
    "chobani_honey_yogurt": {"a_id": "2197626", "expected_b_id": "92544"},
    "great_value_organic_tomato_sauce_8oz": {"a_id": "1929544", "expected_b_id": "105624"},
    "great_value_provolone_noise_probe": {"a_id": "1930779", "expected_b_id": None},
}

TEXT_PROBES = {
    "great_value_tomato_text": "Great Value Organic Tomato Sauce 8 oz",
    "great_value_provolone_text": "Great Value Provolone Deli Style Sliced Cheese 8 oz",
    "great_value_water_text": "Great Value Spring Water 24 x 16.9 fl oz",
}


@dataclass
class Product:
    source: str
    item_id: str
    name: str
    brand_raw: str
    brand_norm: str
    private_label: bool
    category_0: str
    category_1: str
    category_2: str
    size_user_friendly: str
    name_size: ParsedSize | None
    sizing_size: ParsedSize | None

    @property
    def preferred_size(self) -> ParsedSize | None:
        if self.source == "B":
            return self.sizing_size or self.name_size
        return self.name_size or self.sizing_size


def strip_private_label_tokens(text: str) -> str:
    text_norm = normalize_text(text)
    for brand in sorted(PRIVATE_LABEL_BRANDS, key=len, reverse=True):
        text_norm = re.sub(rf"\b{re.escape(brand)}\b", " ", text_norm)
    return re.sub(r"\s+", " ", text_norm).strip()


def size_tokens(size: ParsedSize | None) -> str:
    if not size:
        return ""
    value = f"{size.comparable_value:.4f}".rstrip("0").rstrip(".")
    return f"{value} {size.comparable_unit} {normalize_text(size.raw)}"


def retrieval_text(product: Product, mode: str) -> str:
    name_text = normalize_text(product.name)
    brand_text = product.brand_norm
    if mode == "suppress_private_label" and product.private_label:
        name_text = strip_private_label_tokens(name_text)
        brand_text = ""
    pieces = [
        brand_text,
        name_text,
        remove_size_tokens(name_text),
        normalize_text(product.category_0),
        normalize_text(product.category_1),
        normalize_text(product.category_2),
        size_tokens(product.preferred_size),
    ]
    return " ".join(piece for piece in pieces if piece).strip()


def synthetic_product(text: str, mode: str = "A") -> Product:
    brand = ""
    text_norm = normalize_text(text)
    for label in PRIVATE_LABEL_BRANDS:
        if label in text_norm:
            brand = label
            break
    return Product(
        source=mode,
        item_id="TEXT_QUERY",
        name=text,
        brand_raw=brand,
        brand_norm=brand,
        private_label=bool(brand),
        category_0="",
        category_1="",
        category_2="",
        size_user_friendly="",
        name_size=best_size(text),
        sizing_size=None,
    )


def load_products(path: Path, source: str) -> list[Product]:
    products: list[Product] = []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            item_id = row.get("item_id") or ""
            if not re.fullmatch(r"\d+", item_id):
                continue
            item_info, _ = parse_json_dict(row.get("item_info", ""))
            sizing_info, _ = parse_json_dict(row.get("sizing_comp", ""))
            tags = parse_tags(row.get("tags", ""))
            is_pl = private_label_a(row) if source == "A" else private_label_b(row, tags)
            size_text = str(sizing_info.get("size_user_friendly") or "")
            products.append(
                Product(
                    source=source,
                    item_id=item_id,
                    name=row.get("name") or "",
                    brand_raw=row.get("brand_raw") or "",
                    brand_norm=normalize_text(row.get("brand_raw")),
                    private_label=is_pl,
                    category_0=str(item_info.get("category_0") or ""),
                    category_1=str(item_info.get("category_1") or ""),
                    category_2=str(item_info.get("category_2") or ""),
                    size_user_friendly=size_text,
                    name_size=best_size(row.get("name")),
                    sizing_size=best_size(size_text),
                )
            )
    return products


class TfidfIndex:
    def __init__(self, products: list[Product], mode: str) -> None:
        if not HAS_SKLEARN:
            raise RuntimeError("scikit-learn is not installed")
        self.products = products
        self.mode = mode
        self.word = TfidfVectorizer(
            analyzer="word",
            ngram_range=(1, 2),
            min_df=1,
            max_df=0.95,
            sublinear_tf=True,
            norm=None,
        )
        self.char = TfidfVectorizer(
            analyzer="char_wb",
            ngram_range=(3, 5),
            min_df=2,
            max_features=250000,
            sublinear_tf=True,
            norm=None,
        )
        texts = [retrieval_text(product, mode) for product in products]
        self.matrix = normalize(hstack([self.word.fit_transform(texts), self.char.fit_transform(texts)]))

    def query(self, product: Product, top_k: int) -> list[dict[str, Any]]:
        text = retrieval_text(product, self.mode)
        query_vec = normalize(hstack([self.word.transform([text]), self.char.transform([text])]))
        scores = (self.matrix @ query_vec.T).toarray().ravel()
        if top_k >= len(scores):
            candidate_idx = np.argsort(-scores)
        else:
            candidate_idx = np.argpartition(-scores, top_k)[:top_k]
            candidate_idx = candidate_idx[np.argsort(-scores[candidate_idx])]
        return [candidate_payload(self.products[idx], float(scores[idx])) for idx in candidate_idx[:top_k]]


class Bm25Index:
    def __init__(self, products: list[Product], mode: str) -> None:
        if not HAS_BM25:
            raise RuntimeError("rank_bm25 is not installed")
        self.products = products
        self.mode = mode
        self.tokens = [retrieval_text(product, mode).split() for product in products]
        self.index = BM25Okapi(self.tokens)

    def query(self, product: Product, top_k: int) -> list[dict[str, Any]]:
        scores = np.asarray(self.index.get_scores(retrieval_text(product, self.mode).split()))
        if top_k >= len(scores):
            candidate_idx = np.argsort(-scores)
        else:
            candidate_idx = np.argpartition(-scores, top_k)[:top_k]
            candidate_idx = candidate_idx[np.argsort(-scores[candidate_idx])]
        return [candidate_payload(self.products[idx], float(scores[idx])) for idx in candidate_idx[:top_k]]


def candidate_payload(product: Product, score: float) -> dict[str, Any]:
    return {
        "item_id": product.item_id,
        "score": round(score, 6),
        "name": product.name,
        "brand_raw": product.brand_raw,
        "private_label": product.private_label,
        "category": [product.category_0, product.category_1, product.category_2],
        "size_user_friendly": product.size_user_friendly,
    }


def rank_of(candidates: list[dict[str, Any]], expected_b_id: str | None) -> int | None:
    if not expected_b_id:
        return None
    for idx, candidate in enumerate(candidates, start=1):
        if candidate["item_id"] == expected_b_id:
            return idx
    return None


def great_lakes_hits(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    hits = []
    for idx, candidate in enumerate(candidates, start=1):
        haystack = normalize_text(f"{candidate['brand_raw']} {candidate['name']}")
        if "great lakes" in haystack:
            hit = dict(candidate)
            hit["rank"] = idx
            hits.append(hit)
    return hits


def run_probe(a_csv: Path, b_csv: Path, top_k: int) -> dict[str, Any]:
    b_products = load_products(b_csv, "B")
    a_products = {product.item_id: product for product in load_products(a_csv, "A")}

    modes = ["brand_included", "suppress_private_label"]
    results: dict[str, Any] = {
        "environment": {
            "has_sklearn": HAS_SKLEARN,
            "has_rank_bm25": HAS_BM25,
            "b_index_rows": len(b_products),
            "top_k": top_k,
        },
        "tfidf": {},
        "bm25": {},
        "private_label_noise": {},
    }

    if HAS_SKLEARN:
        for mode in modes:
            index = TfidfIndex(b_products, mode)
            results["tfidf"][mode] = {}
            for label, config in DEFAULT_A_PROBES.items():
                product = a_products.get(config["a_id"])
                if not product:
                    continue
                candidates = index.query(product, top_k)
                results["tfidf"][mode][label] = {
                    "query": candidate_payload(product, 0.0),
                    "expected_b_id": config["expected_b_id"],
                    "expected_rank": rank_of(candidates, config["expected_b_id"]),
                    "top_candidates": candidates[:10],
                    "great_lakes_hits_top_k": great_lakes_hits(candidates),
                }

            results["private_label_noise"][mode] = {}
            for label, text in TEXT_PROBES.items():
                product = synthetic_product(text)
                candidates = index.query(product, top_k)
                results["private_label_noise"][mode][label] = {
                    "query_text": text,
                    "top_candidates": candidates[:10],
                    "great_lakes_hits_top_k": great_lakes_hits(candidates),
                }

    if HAS_BM25:
        for mode in modes:
            index = Bm25Index(b_products, mode)
            results["bm25"][mode] = {}
            for label, config in DEFAULT_A_PROBES.items():
                product = a_products.get(config["a_id"])
                if not product:
                    continue
                candidates = index.query(product, min(top_k, 50))
                results["bm25"][mode][label] = {
                    "query": candidate_payload(product, 0.0),
                    "expected_b_id": config["expected_b_id"],
                    "expected_rank": rank_of(candidates, config["expected_b_id"]),
                    "top_candidates": candidates[:10],
                    "great_lakes_hits_top_k": great_lakes_hits(candidates),
                }

    return results


def write_markdown(results: dict[str, Any], path: Path) -> None:
    lines = [
        "# Retrieval Probe Results",
        "",
        "Generated by `scripts/retrieval_probe.py`.",
        "",
        "## Environment",
        "",
        f"- Store B rows indexed: {results['environment']['b_index_rows']:,}.",
        f"- scikit-learn TF-IDF available: {results['environment']['has_sklearn']}.",
        f"- BM25 available: {results['environment']['has_rank_bm25']}.",
        "",
    ]

    if results.get("tfidf"):
        lines.extend(["## TF-IDF Expected Ranks", ""])
        lines.append("| Mode | Probe | Expected B | Rank | Top 1 |")
        lines.append("|---|---|---:|---:|---|")
        for mode, probes in results["tfidf"].items():
            for label, probe in probes.items():
                if label not in DEFAULT_A_PROBES:
                    continue
                top = probe["top_candidates"][0] if probe["top_candidates"] else {}
                rank = probe["expected_rank"] if probe["expected_rank"] is not None else "not in top-k"
                lines.append(
                    f"| {mode} | {label} | {probe['expected_b_id'] or ''} | {rank} | "
                    f"{top.get('item_id', '')}: {top.get('name', '')} |"
                )
        lines.append("")

    if results.get("bm25"):
        lines.extend(["## BM25 Expected Ranks", ""])
        lines.append("| Mode | Probe | Expected B | Rank | Top 1 |")
        lines.append("|---|---|---:|---:|---|")
        for mode, probes in results["bm25"].items():
            for label, probe in probes.items():
                if label not in DEFAULT_A_PROBES:
                    continue
                top = probe["top_candidates"][0] if probe["top_candidates"] else {}
                rank = probe["expected_rank"] if probe["expected_rank"] is not None else "not in top-k"
                lines.append(
                    f"| {mode} | {label} | {probe['expected_b_id'] or ''} | {rank} | "
                    f"{top.get('item_id', '')}: {top.get('name', '')} |"
                )
        lines.append("")

    if results.get("private_label_noise"):
        lines.extend(["## Private-Label Brand Token Noise", ""])
        lines.append("| Mode | Probe | Great Lakes hits in top-k | First top candidate |")
        lines.append("|---|---|---:|---|")
        for mode, probes in results["private_label_noise"].items():
            for label, probe in probes.items():
                top = probe["top_candidates"][0] if probe["top_candidates"] else {}
                lines.append(
                    f"| {mode} | {label} | {len(probe['great_lakes_hits_top_k'])} | "
                    f"{top.get('item_id', '')}: {top.get('name', '')} |"
                )
        lines.append("")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--a-csv", type=Path, default=DEFAULT_A)
    parser.add_argument("--b-csv", type=Path, default=DEFAULT_B)
    parser.add_argument("--top-k", type=int, default=50)
    parser.add_argument("--json-out", type=Path, default=DEFAULT_JSON_OUT)
    parser.add_argument("--markdown-out", type=Path, default=DEFAULT_MARKDOWN_OUT)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    results = run_probe(args.a_csv, args.b_csv, args.top_k)
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(json.dumps(results, indent=2, sort_keys=True), encoding="utf-8")
    write_markdown(results, args.markdown_out)
    print(f"Wrote {args.json_out}")
    print(f"Wrote {args.markdown_out}")
    if results["tfidf"]:
        included = results["tfidf"]["brand_included"]
        suppressed = results["tfidf"]["suppress_private_label"]
        print(
            "TF-IDF ranks: "
            f"Chobani included={included['chobani_honey_yogurt']['expected_rank']}, "
            f"Chobani suppressed={suppressed['chobani_honey_yogurt']['expected_rank']}, "
            f"tomato included={included['great_value_organic_tomato_sauce_8oz']['expected_rank']}, "
            f"tomato suppressed={suppressed['great_value_organic_tomato_sauce_8oz']['expected_rank']}"
        )


if __name__ == "__main__":
    main()
