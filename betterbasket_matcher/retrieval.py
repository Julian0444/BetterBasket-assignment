"""TF-IDF candidate retrieval for the BetterBasket matching pipeline.

Phase 5: builds a single B-side index over `retrieval_text` using a word
(1, 2) + char-wb (3, 5) TF-IDF concatenation, and exposes per-A top-k
candidate generation with `groups_compatible` post-filtering. Hard rules,
scoring, and pipeline orchestration are deferred to Phases 6-8.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence

import numpy as np
from scipy.sparse import csr_matrix, hstack
from sklearn.feature_extraction.text import TfidfVectorizer

from betterbasket_matcher.normalize import NormalizedProduct
from betterbasket_matcher.taxonomy import (
    assign_matchable_group,
    groups_compatible,
)


def build_retrieval_text(product: NormalizedProduct) -> str:
    """Return the retrieval text for a product.

    Phase 3 owns the construction (including private-label brand suppression
    and `.0` size cleanup). This is a thin accessor so future callers do not
    reach into NormalizedProduct fields directly.
    """
    return product.retrieval_text or ""


@dataclass(frozen=True)
class Candidate:
    item_id_b: str
    score: float
    rank: int


class TfidfRetriever:
    """Global TF-IDF index over B with compatible-group post-filter.

    Score is the dot product of L2-normalized concatenated word + char-wb
    TF-IDF vectors. Each sub-vector is L2-normalized to 1 by sklearn, so the
    concatenated row has L2 = sqrt(2); ranking is unaffected, and Phase 9
    calibration will set thresholds against actual scores.
    """

    def __init__(self) -> None:
        self._fitted = False
        self._word: Optional[TfidfVectorizer] = None
        self._char: Optional[TfidfVectorizer] = None
        self._matrix: Optional[csr_matrix] = None
        self._products: List[NormalizedProduct] = []
        self._b_groups: List[Optional[str]] = []

    # ------------------------------------------------------------------
    # Fit
    # ------------------------------------------------------------------

    def fit(self, products_b: Sequence[NormalizedProduct]) -> "TfidfRetriever":
        if not products_b:
            raise ValueError("cannot fit TfidfRetriever on empty corpus")

        # Drop blank-text rows so they cannot be returned as candidates.
        kept: List[NormalizedProduct] = []
        texts: List[str] = []
        for p in products_b:
            t = build_retrieval_text(p)
            if t.strip():
                kept.append(p)
                texts.append(t)

        if not texts:
            raise ValueError("cannot fit TfidfRetriever: all corpus texts are blank")

        self._word = TfidfVectorizer(
            analyzer="word",
            ngram_range=(1, 2),
            lowercase=False,
            norm="l2",
            sublinear_tf=True,
        )
        self._char = TfidfVectorizer(
            analyzer="char_wb",
            ngram_range=(3, 5),
            lowercase=False,
            norm="l2",
            sublinear_tf=True,
        )
        xw = self._word.fit_transform(texts)
        xc = self._char.fit_transform(texts)
        self._matrix = hstack([xw, xc]).tocsr()

        self._products = kept
        self._b_groups = [assign_matchable_group(p) for p in kept]
        self._fitted = True
        return self

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def query(self, product_a: NormalizedProduct, k: int = 50) -> List[Candidate]:
        if not self._fitted:
            raise RuntimeError("TfidfRetriever.query called before fit")

        a_group = assign_matchable_group(product_a)
        if a_group is None:
            return []

        # Compatible-group mask
        mask = np.array(
            [groups_compatible(a_group, g) for g in self._b_groups],
            dtype=bool,
        )
        if not mask.any():
            return []

        text = build_retrieval_text(product_a)
        if not text.strip():
            return []

        qw = self._word.transform([text])
        qc = self._char.transform([text])
        q = hstack([qw, qc]).tocsr()

        # Cosine-like dot product against the L2-normalized index rows.
        # Result shape: (n_b,)
        scores = (q @ self._matrix.T).toarray().ravel()

        # Restrict to compatible Bs
        compat_idx = np.where(mask)[0]
        compat_scores = scores[compat_idx]

        if compat_idx.size == 0:
            return []

        k_eff = min(k, compat_idx.size)
        # Sort all compatible by score desc then by item_id_b asc for stability
        order = sorted(
            range(compat_idx.size),
            key=lambda i: (-float(compat_scores[i]), self._products[compat_idx[i]].item_id),
        )[:k_eff]

        return [
            Candidate(
                item_id_b=self._products[compat_idx[i]].item_id,
                score=float(compat_scores[i]),
                rank=rank,
            )
            for rank, i in enumerate(order, start=1)
        ]
