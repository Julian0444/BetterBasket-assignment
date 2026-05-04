"""Scoring tests for Phase 6 (score_pair + select_best)."""
from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from betterbasket_matcher.io import read_products
from betterbasket_matcher.normalize import (
    NormalizedProduct,
    SizeInfo,
    normalize_product,
)
from betterbasket_matcher.scoring import (
    ScoreBreakdown,
    SelectionResult,
    score_pair,
    select_best,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Fixture loaders
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def a_index() -> dict:
    rows, _ = read_products(FIXTURE_DIR / "mini_a.csv", "A")
    return {r["item_id"]: normalize_product(r, "A") for r in rows}


@pytest.fixture(scope="module")
def b_index() -> dict:
    rows, _ = read_products(FIXTURE_DIR / "mini_b.csv", "B")
    return {r["item_id"]: normalize_product(r, "B") for r in rows}


def _make_product(
    *,
    item_id: str,
    source: str,
    brand_norm: str = "",
    is_private_label: bool = False,
    brand_inferred: bool = False,
    size: SizeInfo = None,
    is_organic: bool = False,
    storage_type=None,
    form=None,
    flavor=None,
    category_0=None,
    category_1=None,
    category_2=None,
    core_name: str = "",
    retrieval_text: str = "",
) -> NormalizedProduct:
    return NormalizedProduct(
        item_id=item_id,
        source=source,
        brand_norm=brand_norm,
        is_private_label=is_private_label,
        brand_inferred=brand_inferred,
        size=size if size is not None else SizeInfo(),
        is_organic=is_organic,
        storage_type=storage_type,
        form=form,
        flavor=flavor,
        category_0=category_0,
        category_1=category_1,
        category_2=category_2,
        core_name=core_name,
        retrieval_text=retrieval_text,
    )


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

class TestScoreBreakdownDataclass:
    def test_fields_and_total(self):
        bd = ScoreBreakdown(
            total=0.0,
            core_name=0.5,
            token_overlap=0.5,
            brand=0.5,
            size_pack=0.5,
            group=0.5,
            attributes=0.5,
        )
        # weights: 0.35 + 0.15 + 0.15 + 0.20 + 0.10 + 0.05 = 1.00
        # total here was constructed as 0; the dataclass holds whatever caller passed
        # but score_pair's outputs satisfy the weight identity (tested below).
        assert bd.core_name == 0.5
        assert bd.size_pack == 0.5

    def test_frozen(self):
        bd = ScoreBreakdown(
            total=0.5, core_name=0.0, token_overlap=0.0,
            brand=0.0, size_pack=0.0, group=0.0, attributes=0.0,
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            bd.total = 1.0  # type: ignore[misc]

    def test_score_pair_total_equals_weighted_sum(self, a_index, b_index):
        a = a_index["2197626"]
        b = b_index["92544"]
        bd = score_pair(a, b, retrieval_score=2.0)
        expected = (
            0.35 * bd.core_name
            + 0.15 * bd.token_overlap
            + 0.15 * bd.brand
            + 0.20 * bd.size_pack
            + 0.10 * bd.group
            + 0.05 * bd.attributes
        )
        assert abs(bd.total - expected) < 1e-9


class TestSelectionResultDataclass:
    def test_frozen(self):
        sr = SelectionResult(
            selected_item_id_b=None,
            score=None,
            margin=None,
            reason="no_candidates",
            breakdown=None,
            rejected=tuple(),
            runner_up_item_id_b=None,
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            sr.reason = "selected"  # type: ignore[misc]

    def test_top_item_id_b_field_defaults_none(self):
        """Backward-compatible default; existing kwargs constructions stay valid."""
        sr = SelectionResult(
            selected_item_id_b=None,
            score=None,
            margin=None,
            reason="no_candidates",
            breakdown=None,
            rejected=tuple(),
            runner_up_item_id_b=None,
        )
        assert hasattr(sr, "top_item_id_b")
        assert sr.top_item_id_b is None


# ---------------------------------------------------------------------------
# Core-name normalization
# ---------------------------------------------------------------------------

class TestCoreNameNormalization:
    def _stub(self):
        return _make_product(
            item_id="x",
            source="A",
            brand_norm="acme",
            size=SizeInfo(unit="oz", unit_size=10.0, pack_count=1, total_size=10.0),
            category_0="food",
            category_1="pantry",
        )

    def _stub_b(self):
        return _make_product(
            item_id="y",
            source="B",
            brand_norm="acme",
            size=SizeInfo(unit="oz", unit_size=10.0, pack_count=1, total_size=10.0),
            category_0="grocery",
            category_1="pantry",
        )

    def test_score_2_maps_to_1(self):
        bd = score_pair(self._stub(), self._stub_b(), retrieval_score=2.0)
        assert bd.core_name == pytest.approx(1.0)

    def test_score_1_maps_to_0_5(self):
        bd = score_pair(self._stub(), self._stub_b(), retrieval_score=1.0)
        assert bd.core_name == pytest.approx(0.5)

    def test_score_0_maps_to_0(self):
        bd = score_pair(self._stub(), self._stub_b(), retrieval_score=0.0)
        assert bd.core_name == pytest.approx(0.0)

    def test_overshoot_clipped_to_1(self):
        bd = score_pair(self._stub(), self._stub_b(), retrieval_score=2.5)
        assert bd.core_name == pytest.approx(1.0)

    def test_negative_clipped_to_0(self):
        bd = score_pair(self._stub(), self._stub_b(), retrieval_score=-0.5)
        assert bd.core_name == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# score_pair main cases
# ---------------------------------------------------------------------------

class TestScorePair:
    def test_chobani_exact_match_high(self, a_index, b_index):
        a = a_index["2197626"]
        b = b_index["92544"]
        bd = score_pair(a, b, retrieval_score=2.0)
        assert bd.total >= 0.85

    def test_pl_tomato_8oz_high(self, a_index, b_index):
        a = a_index["1929544"]  # GV Organic Tomato Sauce 8 oz
        b = b_index["105624"]   # Wegmans Organic Tomato Sauce 8 oz
        bd = score_pair(a, b, retrieval_score=1.5)
        assert bd.total >= 0.75

    def test_inferred_brand_gets_partial_brand_compat(self):
        a = _make_product(
            item_id="i1",
            source="A",
            brand_norm="jif",
            is_private_label=False,
            brand_inferred=True,
            size=SizeInfo(unit="oz", unit_size=16.0, pack_count=1, total_size=16.0),
            category_0="food",
            category_1="pantry",
            core_name="creamy peanut butter",
        )
        b = _make_product(
            item_id="i2",
            source="B",
            brand_norm="jif",
            is_private_label=False,
            brand_inferred=False,
            size=SizeInfo(unit="oz", unit_size=16.0, pack_count=1, total_size=16.0),
            category_0="grocery",
            category_1="nut butters and spreads",
            core_name="creamy peanut butter",
        )
        bd = score_pair(a, b, retrieval_score=1.5)
        # With one side inferred, brand component is partial (0.6), not a full 1.0.
        assert bd.brand == pytest.approx(0.6)
        assert bd.total > 0.0

    def test_organic_true_vs_false_lowers_attributes(self):
        # Build two B-side rows identical except is_organic differs.
        size = SizeInfo(unit="oz", unit_size=10.0, pack_count=1, total_size=10.0)
        a = _make_product(
            item_id="oa",
            source="A",
            brand_norm="acme",
            is_organic=True,
            size=size,
            category_0="food",
            category_1="pantry",
            storage_type="shelf_stable",
        )
        b_match = _make_product(
            item_id="ob1",
            source="B",
            brand_norm="acme",
            is_organic=True,
            size=size,
            category_0="grocery",
            category_1="pantry",
            storage_type="shelf_stable",
        )
        b_mismatch = _make_product(
            item_id="ob2",
            source="B",
            brand_norm="acme",
            is_organic=False,
            size=size,
            category_0="grocery",
            category_1="pantry",
            storage_type="shelf_stable",
        )
        bd_match = score_pair(a, b_match, retrieval_score=1.5)
        bd_mismatch = score_pair(a, b_mismatch, retrieval_score=1.5)
        assert bd_mismatch.attributes < bd_match.attributes
        assert bd_mismatch.total < bd_match.total


# ---------------------------------------------------------------------------
# select_best
# ---------------------------------------------------------------------------

class TestSelectBest:
    def test_no_candidates(self, a_index):
        a = a_index["1929544"]
        result = select_best(a, [], min_score=0.5, min_margin=0.05)
        assert result.selected_item_id_b is None
        assert result.reason == "no_candidates"
        assert result.score is None
        assert result.margin is None

    def test_tomato_picks_8oz_over_15_and_29(self, a_index, b_index):
        a = a_index["1929544"]
        pairs = [
            (b_index["105624"], 1.6),   # 8 oz
            (b_index["103620"], 1.55),  # 15 oz
            (b_index["1086860"], 1.5),  # 29 oz
        ]
        result = select_best(a, pairs, min_score=0.5, min_margin=0.0)
        assert result.selected_item_id_b == "105624"
        assert result.reason == "selected"
        rejected_ids = {r[0] for r in result.rejected}
        assert "103620" in rejected_ids
        assert "1086860" in rejected_ids
        rejected_reasons = {r[0]: r[1] for r in result.rejected}
        assert rejected_reasons["103620"] == "size_mismatch"
        assert rejected_reasons["1086860"] == "size_mismatch"

    def test_all_rejected(self, a_index, b_index):
        a = a_index["1929544"]
        pairs = [
            (b_index["103620"], 1.0),
            (b_index["1086860"], 1.0),
        ]
        result = select_best(a, pairs, min_score=0.5, min_margin=0.0)
        assert result.selected_item_id_b is None
        assert result.reason == "all_rejected"
        assert len(result.rejected) == 2

    def test_below_min_score(self, a_index, b_index):
        a = a_index["1929544"]
        # Only one survivor. If we set min_score very high, it should fail.
        pairs = [(b_index["105624"], 0.0)]  # retrieval=0 so core_name=0
        result = select_best(a, pairs, min_score=0.99, min_margin=0.0)
        assert result.selected_item_id_b is None
        assert result.reason == "below_min_score"
        assert result.breakdown is not None  # populated for audit
        assert result.score is not None

    def test_below_min_margin(self, a_index, b_index):
        # Two near-identical scoring candidates so margin is tiny.
        a = a_index["2197626"]  # Chobani 5.3 oz
        b1 = b_index["92544"]   # Chobani 5.3 oz
        # Synthesize a near-twin B candidate to force a small margin.
        b2 = _make_product(
            item_id="999999",
            source="B",
            brand_norm="chobani",
            size=SizeInfo(unit="oz", unit_size=5.3, pack_count=1, total_size=5.3),
            category_0="dairy",
            category_1="yogurt",
            category_2="greek",
            core_name="greek honey blended yogurt",
        )
        pairs = [(b1, 2.0), (b2, 2.0)]
        result = select_best(a, pairs, min_score=0.5, min_margin=0.5)
        assert result.reason == "below_min_margin"
        assert result.breakdown is not None
        assert result.margin is not None
        assert result.margin < 0.5


# ---------------------------------------------------------------------------
# Single-survivor margin behavior (M3)
# ---------------------------------------------------------------------------

class TestSingleSurvivorMargin:
    def test_single_survivor_ignores_min_margin(self, a_index, b_index):
        # Only one rule-surviving candidate. margin must be None (no runner-up
        # to compare to) and min_margin must not gate the selection.
        a = a_index["1929544"]
        pairs = [(b_index["105624"], 1.6)]
        result = select_best(a, pairs, min_score=0.3, min_margin=0.99)
        assert result.reason == "selected"
        assert result.selected_item_id_b == "105624"
        assert result.margin is None
        assert result.runner_up_item_id_b is None

    def test_single_survivor_below_min_score_still_reports_margin_none(
        self, a_index, b_index,
    ):
        a = a_index["1929544"]
        pairs = [(b_index["105624"], 0.0)]  # core_name=0 -> low total
        result = select_best(a, pairs, min_score=0.99, min_margin=0.0)
        assert result.reason == "below_min_score"
        assert result.margin is None
        assert result.breakdown is not None

    def test_single_survivor_after_rejects_still_ignores_min_margin(
        self, a_index, b_index,
    ):
        # Three candidates, only one passes hard rules. min_margin=0.99 must
        # not block the lone survivor.
        a = a_index["1929544"]
        pairs = [
            (b_index["105624"], 1.6),   # 8 oz — passes
            (b_index["103620"], 1.55),  # 15 oz — size_mismatch
            (b_index["1086860"], 1.5),  # 29 oz — size_mismatch
        ]
        result = select_best(a, pairs, min_score=0.3, min_margin=0.99)
        assert result.reason == "selected"
        assert result.selected_item_id_b == "105624"
        assert result.margin is None
        assert result.runner_up_item_id_b is None
        assert len(result.rejected) == 2


# ---------------------------------------------------------------------------
# top_item_id_b exposure (Phase 7 contract closure)
# ---------------------------------------------------------------------------

class TestTopItemIdB:
    """top_item_id_b must be populated for every reason branch so the pipeline
    audit row can carry the top scored survivor's id without re-running rules.
    """

    def test_selected_top_equals_selected(self, a_index, b_index):
        a = a_index["2197626"]
        pairs = [(b_index["92544"], 2.0)]
        result = select_best(a, pairs, min_score=0.3, min_margin=0.0)
        assert result.reason == "selected"
        assert result.top_item_id_b == result.selected_item_id_b
        assert result.top_item_id_b == "92544"

    def test_no_candidates_top_is_none(self, a_index):
        a = a_index["1929544"]
        result = select_best(a, [], min_score=0.5, min_margin=0.05)
        assert result.reason == "no_candidates"
        assert result.top_item_id_b is None

    def test_all_rejected_top_is_none(self, a_index, b_index):
        a = a_index["1929544"]
        pairs = [
            (b_index["103620"], 1.0),
            (b_index["1086860"], 1.0),
        ]
        result = select_best(a, pairs, min_score=0.5, min_margin=0.0)
        assert result.reason == "all_rejected"
        assert result.top_item_id_b is None

    def test_below_min_score_top_is_top_survivor(self, a_index, b_index):
        a = a_index["1929544"]
        pairs = [(b_index["105624"], 0.0)]  # only survivor; score below threshold
        result = select_best(a, pairs, min_score=0.99, min_margin=0.0)
        assert result.reason == "below_min_score"
        assert result.top_item_id_b == "105624"
        assert result.selected_item_id_b is None

    def test_below_min_margin_top_is_top_survivor(self, a_index, b_index):
        a = a_index["2197626"]
        b1 = b_index["92544"]
        b2 = _make_product(
            item_id="999999",
            source="B",
            brand_norm="chobani",
            size=SizeInfo(unit="oz", unit_size=5.3, pack_count=1, total_size=5.3),
            category_0="dairy",
            category_1="yogurt",
            category_2="greek",
            core_name="greek honey blended yogurt",
        )
        pairs = [(b1, 2.0), (b2, 2.0)]
        result = select_best(a, pairs, min_score=0.5, min_margin=0.5)
        assert result.reason == "below_min_margin"
        # Top survivor is the lower-id one by tie-break (stable id ascending).
        assert result.top_item_id_b in {"92544", "999999"}
        # Specifically, with equal totals, smaller numeric id wins.
        assert result.top_item_id_b == "92544"
        assert result.selected_item_id_b is None


# ---------------------------------------------------------------------------
# Tie-break
# ---------------------------------------------------------------------------

class TestTieBreak:
    def test_exact_size_preferred(self):
        a = _make_product(
            item_id="ta",
            source="A",
            brand_norm="acme",
            size=SizeInfo(unit="oz", unit_size=8.0, pack_count=1, total_size=8.0),
            category_0="food",
            category_1="pantry",
            core_name="apple sauce",
        )
        b_exact = _make_product(
            item_id="100",
            source="B",
            brand_norm="acme",
            size=SizeInfo(unit="oz", unit_size=8.0, pack_count=1, total_size=8.0),
            category_0="grocery",
            category_1="pantry",
            core_name="apple sauce",
        )
        b_close = _make_product(
            item_id="101",
            source="B",
            brand_norm="acme",
            # 4% off — within size_mismatch tolerance, but not the exact size.
            size=SizeInfo(unit="oz", unit_size=8.32, pack_count=1, total_size=8.32),
            category_0="grocery",
            category_1="pantry",
            core_name="apple sauce",
        )
        # Equal retrieval scores so total_top1 == total_top2 except for
        # size_pack component.
        pairs = [(b_close, 1.5), (b_exact, 1.5)]
        result = select_best(a, pairs, min_score=0.0, min_margin=0.0)
        assert result.selected_item_id_b == "100"

    def test_metadata_richness_prefers_richer_b(self):
        a = _make_product(
            item_id="ma",
            source="A",
            brand_norm="acme",
            size=SizeInfo(unit="oz", unit_size=10.0, pack_count=1, total_size=10.0),
            category_0="food",
            category_1="pantry",
            core_name="something",
            storage_type=None,
            form=None,
            flavor=None,
        )
        b_rich = _make_product(
            item_id="200",
            source="B",
            brand_norm="acme",
            size=SizeInfo(unit="oz", unit_size=10.0, pack_count=1, total_size=10.0),
            category_0="grocery",
            category_1="pantry",
            core_name="something",
            storage_type="shelf_stable",
            form="liquid",
            flavor="vanilla",
        )
        b_sparse = _make_product(
            item_id="201",
            source="B",
            brand_norm="acme",
            size=SizeInfo(unit="oz", unit_size=10.0, pack_count=1, total_size=10.0),
            category_0="grocery",
            category_1="pantry",
            core_name="something",
            storage_type=None,
            form=None,
            flavor=None,
        )
        pairs = [(b_sparse, 1.5), (b_rich, 1.5)]
        result = select_best(a, pairs, min_score=0.0, min_margin=0.0)
        assert result.selected_item_id_b == "200"

    def test_stable_id_break_prefers_smaller_id(self):
        a = _make_product(
            item_id="ia",
            source="A",
            brand_norm="acme",
            size=SizeInfo(unit="oz", unit_size=10.0, pack_count=1, total_size=10.0),
            category_0="food",
            category_1="pantry",
            core_name="thing",
        )
        b_low = _make_product(
            item_id="100",
            source="B",
            brand_norm="acme",
            size=SizeInfo(unit="oz", unit_size=10.0, pack_count=1, total_size=10.0),
            category_0="grocery",
            category_1="pantry",
            core_name="thing",
        )
        b_high = _make_product(
            item_id="500",
            source="B",
            brand_norm="acme",
            size=SizeInfo(unit="oz", unit_size=10.0, pack_count=1, total_size=10.0),
            category_0="grocery",
            category_1="pantry",
            core_name="thing",
        )
        pairs = [(b_high, 1.5), (b_low, 1.5)]
        result = select_best(a, pairs, min_score=0.0, min_margin=0.0)
        assert result.selected_item_id_b == "100"
