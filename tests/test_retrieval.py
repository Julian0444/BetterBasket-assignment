"""Tests for betterbasket_matcher.retrieval — Phase 5 TF-IDF candidate retrieval."""
from __future__ import annotations

from pathlib import Path

import pytest

from betterbasket_matcher.io import read_products
from betterbasket_matcher.normalize import NormalizedProduct, normalize_product
from betterbasket_matcher.retrieval import (
    Candidate,
    TfidfRetriever,
    build_retrieval_text,
)


FIXTURES = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Module fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def a_by_id():
    valid, _ = read_products(FIXTURES / "mini_a.csv", "A")
    return {r["item_id"]: r for r in valid}


@pytest.fixture(scope="module")
def b_by_id():
    valid, _ = read_products(FIXTURES / "mini_b.csv", "B")
    return {r["item_id"]: r for r in valid}


@pytest.fixture(scope="module")
def normalized_b(b_by_id):
    return [normalize_product(r, "B") for r in b_by_id.values()]


@pytest.fixture(scope="module")
def fitted_retriever(normalized_b):
    r = TfidfRetriever()
    r.fit(normalized_b)
    return r


def _np_a(item_id, a_by_id):
    return normalize_product(a_by_id[item_id], "A")


def _np_b(item_id, b_by_id):
    return normalize_product(b_by_id[item_id], "B")


# ---------------------------------------------------------------------------
# 1-2. build_retrieval_text contract
# ---------------------------------------------------------------------------

class TestBuildRetrievalText:
    def test_includes_chobani_a(self, a_by_id):
        p = _np_a("2197626", a_by_id)
        assert "chobani" in build_retrieval_text(p)

    def test_includes_chobani_b(self, b_by_id):
        p = _np_b("92544", b_by_id)
        assert "chobani" in build_retrieval_text(p)

    def test_suppresses_great_value_a(self, a_by_id):
        p = _np_a("1929544", a_by_id)
        assert "great value" not in build_retrieval_text(p)

    def test_suppresses_wegmans_b(self, b_by_id):
        p = _np_b("105624", b_by_id)
        assert "wegmans" not in build_retrieval_text(p)

    def test_returns_string(self, a_by_id):
        p = _np_a("2197626", a_by_id)
        assert isinstance(build_retrieval_text(p), str)


# ---------------------------------------------------------------------------
# Candidate dataclass
# ---------------------------------------------------------------------------

class TestCandidate:
    def test_fields(self):
        c = Candidate(item_id_b="92544", score=0.9, rank=1)
        assert c.item_id_b == "92544"
        assert c.score == 0.9
        assert c.rank == 1


# ---------------------------------------------------------------------------
# 3-4. PDF regression: top-5 retrieval
# ---------------------------------------------------------------------------

class TestPdfRegressionTopK:
    def test_a_2197626_finds_b_92544_top5(self, fitted_retriever, a_by_id):
        pa = _np_a("2197626", a_by_id)
        cands = fitted_retriever.query(pa, k=5)
        assert "92544" in {c.item_id_b for c in cands}

    def test_a_1929544_finds_b_105624_top5(self, fitted_retriever, a_by_id):
        pa = _np_a("1929544", a_by_id)
        cands = fitted_retriever.query(pa, k=5)
        assert "105624" in {c.item_id_b for c in cands}


# ---------------------------------------------------------------------------
# 5. Tomato variants are reachable as candidates with large k
# ---------------------------------------------------------------------------

class TestTomatoVariants:
    def test_variants_present_in_candidates(self, fitted_retriever, a_by_id):
        pa = _np_a("1929544", a_by_id)
        cands = fitted_retriever.query(pa, k=50)
        ids = {c.item_id_b for c in cands}
        # All three tomato sauce variants should appear among candidates;
        # Phase 7 size hard rule will later prune the wrong sizes.
        assert {"105624", "103620", "1086860"}.issubset(ids)


# ---------------------------------------------------------------------------
# 6. Group compatibility filter
# ---------------------------------------------------------------------------

class TestGroupCompatibilityFilter:
    def test_dairy_query_returns_only_compatible_groups(self, fitted_retriever, a_by_id):
        from betterbasket_matcher.taxonomy import (
            assign_matchable_group,
            groups_compatible,
        )
        pa = _np_a("2197626", a_by_id)
        a_group = assign_matchable_group(pa)
        assert a_group == "dairy"

        cands = fitted_retriever.query(pa, k=50)
        assert cands, "expected at least one candidate for a dairy A row"

        # Look up each returned B by item_id and check group compatibility
        b_groups = {p.item_id: assign_matchable_group(p) for p in fitted_retriever._products}
        for c in cands:
            assert groups_compatible(a_group, b_groups[c.item_id_b]) is True

    def test_pantry_query_returns_only_pantry(self, fitted_retriever, a_by_id):
        from betterbasket_matcher.taxonomy import (
            assign_matchable_group,
            groups_compatible,
        )
        pa = _np_a("1929544", a_by_id)
        a_group = assign_matchable_group(pa)
        assert a_group == "pantry"

        cands = fitted_retriever.query(pa, k=50)
        b_groups = {p.item_id: assign_matchable_group(p) for p in fitted_retriever._products}
        for c in cands:
            assert groups_compatible(a_group, b_groups[c.item_id_b]) is True


# ---------------------------------------------------------------------------
# 7-8. Empty / None group edge cases
# ---------------------------------------------------------------------------

class TestEmptyAndNoneGroup:
    def test_group_none_returns_empty(self, fitted_retriever):
        # category_0 = None -> assign_matchable_group returns None
        p = NormalizedProduct(item_id="x", source="A", retrieval_text="some words here")
        assert fitted_retriever.query(p, k=10) == []

    def test_no_compatible_b_returns_empty(self, fitted_retriever):
        # Build an A row whose group is "meat"; no B row in mini_b is meat.
        p = NormalizedProduct(
            item_id="x",
            source="A",
            category_0="food",
            category_1="meat",
            core_name="ground beef",
            retrieval_text="ground beef 1lb",
        )
        from betterbasket_matcher.taxonomy import assign_matchable_group
        assert assign_matchable_group(p) == "meat"
        assert fitted_retriever.query(p, k=10) == []

    def test_blank_retrieval_text_returns_empty(self, fitted_retriever):
        # Even with a valid group, a blank query string yields no candidates.
        p = NormalizedProduct(
            item_id="x",
            source="A",
            category_0="food",
            category_1="dairy and eggs",
            retrieval_text="",
        )
        assert fitted_retriever.query(p, k=10) == []


# ---------------------------------------------------------------------------
# Edge cases: k bounds, ranking, determinism
# ---------------------------------------------------------------------------

class TestRetrieverEdgeCases:
    def test_k_larger_than_corpus_does_not_raise(self, fitted_retriever, a_by_id):
        pa = _np_a("2197626", a_by_id)
        cands = fitted_retriever.query(pa, k=10_000)
        # Should not exceed compatible corpus; just confirm it returns cleanly.
        assert isinstance(cands, list)
        assert len(cands) <= len(fitted_retriever._products)

    def test_ranks_are_one_based_and_dense(self, fitted_retriever, a_by_id):
        pa = _np_a("2197626", a_by_id)
        cands = fitted_retriever.query(pa, k=5)
        ranks = [c.rank for c in cands]
        assert ranks == list(range(1, len(cands) + 1))

    def test_scores_are_descending(self, fitted_retriever, a_by_id):
        pa = _np_a("1929544", a_by_id)
        cands = fitted_retriever.query(pa, k=10)
        scores = [c.score for c in cands]
        assert scores == sorted(scores, reverse=True)

    def test_query_is_deterministic(self, fitted_retriever, a_by_id):
        pa = _np_a("2197626", a_by_id)
        a = [(c.item_id_b, c.rank) for c in fitted_retriever.query(pa, k=10)]
        b = [(c.item_id_b, c.rank) for c in fitted_retriever.query(pa, k=10)]
        assert a == b

    def test_fit_empty_corpus_raises(self):
        r = TfidfRetriever()
        with pytest.raises(ValueError):
            r.fit([])

    def test_query_before_fit_raises(self, a_by_id):
        r = TfidfRetriever()
        pa = _np_a("2197626", a_by_id)
        with pytest.raises(RuntimeError):
            r.query(pa, k=5)


# ---------------------------------------------------------------------------
# Phase 10A lock-in: retrieval optimization from the prior Codex pass
# ---------------------------------------------------------------------------

class TestPhase10APrecomputedCompatStructures:
    """fit() now precomputes per-query-group compatible indices and submatrices."""

    def test_compat_structures_exist_after_fit(self, fitted_retriever):
        assert hasattr(fitted_retriever, "_compat_indices_by_group")
        assert hasattr(fitted_retriever, "_compat_matrix_by_group")
        assert isinstance(fitted_retriever._compat_indices_by_group, dict)
        assert isinstance(fitted_retriever._compat_matrix_by_group, dict)
        assert len(fitted_retriever._compat_indices_by_group) > 0
        # Both dicts share the same key set.
        assert set(fitted_retriever._compat_indices_by_group.keys()) == set(
            fitted_retriever._compat_matrix_by_group.keys()
        )

    def test_compat_indices_match_groups_compatible(self, fitted_retriever):
        from betterbasket_matcher.taxonomy import groups_compatible

        for query_group, indices in fitted_retriever._compat_indices_by_group.items():
            expected = [
                i
                for i, b_group in enumerate(fitted_retriever._b_groups)
                if groups_compatible(query_group, b_group)
            ]
            assert list(indices) == expected, (
                f"compat indices for {query_group!r} disagree with groups_compatible"
            )
            mat = fitted_retriever._compat_matrix_by_group[query_group]
            assert mat.shape[0] == len(expected)
            # Submatrix rows must equal the corresponding rows of the full matrix.
            for local, global_idx in enumerate(indices):
                row_full = fitted_retriever._matrix[int(global_idx)].toarray()
                row_sub = mat[local].toarray()
                assert (row_full == row_sub).all()


class TestPhase10AMissingGroupReturnsEmpty:
    """An A whose group has no compatible B rows must short-circuit to []."""

    def test_query_with_group_absent_from_dict_returns_empty(self):
        # Fit a retriever on a single pantry-only B corpus; query an A whose
        # group is 'snacks'. 'snacks' will not appear in the precomputed dict.
        from betterbasket_matcher.taxonomy import assign_matchable_group

        b_pantry = NormalizedProduct(
            item_id="B_pantry",
            source="B",
            category_0="Grocery",
            category_1="Pantry",
            retrieval_text="organic tomato sauce 8oz",
        )
        retriever = TfidfRetriever().fit([b_pantry])

        a_snacks = NormalizedProduct(
            item_id="A_snacks",
            source="A",
            category_0="Food",
            category_1="Snacks",
            retrieval_text="potato chips salted",
        )
        assert assign_matchable_group(a_snacks) == "snacks"
        assert "snacks" not in retriever._compat_indices_by_group
        assert retriever.query(a_snacks, k=10) == []


class TestPhase10ANoZeroScoreCandidates:
    """Sparse retrieval must drop B rows with zero lexical overlap."""

    def test_zero_overlap_b_is_excluded(self):
        # Two pantry-group B rows: one shares all tokens with the A query,
        # one shares none. The zero-overlap B must not be returned.
        b_overlap = NormalizedProduct(
            item_id="B_overlap",
            source="B",
            category_0="Grocery",
            category_1="Pantry",
            retrieval_text="organic tomato sauce 8oz",
        )
        b_zero = NormalizedProduct(
            item_id="B_zero",
            source="B",
            category_0="Grocery",
            category_1="Pantry",
            retrieval_text="qzxqzx vbnvbn",
        )
        retriever = TfidfRetriever().fit([b_overlap, b_zero])

        a = NormalizedProduct(
            item_id="A_q",
            source="A",
            category_0="Food",
            category_1="Pantry",
            retrieval_text="organic tomato sauce 8oz",
        )
        cands = retriever.query(a, k=10)
        ids = {c.item_id_b for c in cands}
        assert "B_overlap" in ids
        assert "B_zero" not in ids
        assert all(c.score > 0.0 for c in cands)


class TestPhase10ADeterminism:
    """Two calls to query() with the same A return identical (id, score, rank)."""

    def test_full_tuple_equality(self, fitted_retriever, a_by_id):
        pa = _np_a("1929544", a_by_id)
        a = [(c.item_id_b, c.score, c.rank) for c in fitted_retriever.query(pa, k=20)]
        b = [(c.item_id_b, c.score, c.rank) for c in fitted_retriever.query(pa, k=20)]
        assert a == b


class TestPhase10APdfRetrievalRegression:
    """A 1929544 (Great Value Organic Tomato Sauce 8 oz) top-50 must contain B 105624."""

    def test_top50_contains_b_105624(self, fitted_retriever, a_by_id):
        pa = _np_a("1929544", a_by_id)
        cands = fitted_retriever.query(pa, k=50)
        assert "105624" in {c.item_id_b for c in cands}
