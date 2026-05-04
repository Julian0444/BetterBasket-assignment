"""Hard rule tests for Phase 6.

Drives evaluate_hard_rules against the mini fixtures plus a small set of
synthetic NormalizedProduct objects for narrow rule edge cases (16 oz vs
1 lb conversion, 8 oz weight vs 8 fl oz volume, distinct national brands,
cat-vs-dog where the fixture's B dog row falls outside the pets group).
"""
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
from betterbasket_matcher.rules import RuleResult, evaluate_hard_rules


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


# ---------------------------------------------------------------------------
# Synthetic helpers (allowed only for narrow rule edge cases)
# ---------------------------------------------------------------------------

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
# RuleResult dataclass
# ---------------------------------------------------------------------------

class TestRuleResultDataclass:
    def test_fields(self):
        r = RuleResult(passed=True, reason="ok")
        assert r.passed is True
        assert r.reason == "ok"

    def test_frozen(self):
        r = RuleResult(passed=False, reason="size_mismatch")
        with pytest.raises(dataclasses.FrozenInstanceError):
            r.passed = True  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Pass cases (the PDF regressions)
# ---------------------------------------------------------------------------

class TestPasses:
    def test_chobani_5_3_oz(self, a_index, b_index):
        a = a_index["2197626"]
        b = b_index["92544"]
        result = evaluate_hard_rules(a, b)
        assert result.passed is True
        assert result.reason == "ok"

    def test_great_value_tomato_8oz_to_wegmans_8oz(self, a_index, b_index):
        a = a_index["1929544"]
        b = b_index["105624"]
        result = evaluate_hard_rules(a, b)
        assert result.passed is True
        assert result.reason == "ok"

    def test_chobani_pack_12(self, a_index, b_index):
        # A 9000004: (12 Pack) Chobani 5.3 oz; B 9100003: 12 x 5.3 ounce
        a = a_index["9000004"]
        b = b_index["9100003"]
        result = evaluate_hard_rules(a, b)
        assert result.passed is True


# ---------------------------------------------------------------------------
# Group mismatch
# ---------------------------------------------------------------------------

class TestGroupMismatch:
    def test_kitchen_home_vs_cheese(self, a_index, b_index):
        # KitchenAid spatula (kitchen_home) vs Kraft shredded cheese (cheese)
        a = a_index["9000005"]
        b = b_index["9100009"]
        result = evaluate_hard_rules(a, b)
        assert result.passed is False
        assert result.reason == "group_mismatch"

    def test_unknown_b_group_rejects(self, a_index, b_index):
        # B 9100010 (Purina dog) has c0='More Departments', c1='Pet Supplies'
        # which doesn't map; so vs a known A group it's group_mismatch.
        a = a_index["9000005"]  # kitchen_home
        b = b_index["9100010"]  # group None
        result = evaluate_hard_rules(a, b)
        assert result.passed is False
        assert result.reason == "group_mismatch"


# ---------------------------------------------------------------------------
# Alcohol mismatch (must beat group_mismatch)
# ---------------------------------------------------------------------------

class TestAlcoholMismatch:
    def test_apple_cider_vs_apple_wine(self, a_index, b_index):
        # A 9000012 cider -> beverages; B 9100011 wine -> wine_beer_spirits.
        a = a_index["9000012"]
        b = b_index["9100011"]
        result = evaluate_hard_rules(a, b)
        assert result.passed is False
        assert result.reason == "alcohol_mismatch"

    def test_alcohol_runs_before_group(self, a_index, b_index):
        # Same case as above; explicit ordering check: reason is the
        # specific 'alcohol_mismatch', not the generic 'group_mismatch'.
        a = a_index["9000012"]
        b = b_index["9100011"]
        result = evaluate_hard_rules(a, b)
        assert result.reason == "alcohol_mismatch"
        assert result.reason != "group_mismatch"


# ---------------------------------------------------------------------------
# National vs private-label
# ---------------------------------------------------------------------------

class TestNationalVsPrivateLabel:
    def test_coca_cola_vs_wegmans_cola(self, a_index, b_index):
        a = a_index["9000006"]
        b = b_index["9100005"]
        result = evaluate_hard_rules(a, b)
        assert result.passed is False
        assert result.reason == "national_vs_private_label"

    def test_pl_to_pl_does_not_reject(self, a_index, b_index):
        # Tomato 8oz: Great Value (PL) vs Wegmans (PL). Must not fire.
        a = a_index["1929544"]
        b = b_index["105624"]
        result = evaluate_hard_rules(a, b)
        assert result.reason != "national_vs_private_label"


# ---------------------------------------------------------------------------
# Brand mismatch (synthetic — fixtures don't have a same-group two-national
# pair where neither side is PL nor inferred)
# ---------------------------------------------------------------------------

class TestBrandMismatch:
    def test_distinct_national_brands(self):
        a = _make_product(
            item_id="111",
            source="A",
            brand_norm="jif",
            is_private_label=False,
            brand_inferred=False,
            size=SizeInfo(unit="oz", unit_size=16.0, pack_count=1, total_size=16.0),
            category_0="food",
            category_1="pantry",
            category_2="nut butters",
            core_name="creamy peanut butter",
        )
        b = _make_product(
            item_id="222",
            source="B",
            brand_norm="skippy",
            is_private_label=False,
            brand_inferred=False,
            size=SizeInfo(unit="oz", unit_size=16.0, pack_count=1, total_size=16.0),
            category_0="grocery",
            category_1="pantry",  # taxonomy maps grocery>pantry; "nut butters and spreads" is unmapped
            category_2="peanut butter",
            core_name="creamy peanut butter",
        )
        result = evaluate_hard_rules(a, b)
        assert result.passed is False
        assert result.reason == "brand_mismatch"

    def test_inferred_brand_does_not_brand_mismatch(self):
        # If A's brand is inferred, brand_mismatch must not fire.
        a = _make_product(
            item_id="333",
            source="A",
            brand_norm="jif",
            is_private_label=False,
            brand_inferred=True,
            size=SizeInfo(unit="oz", unit_size=16.0, pack_count=1, total_size=16.0),
            category_0="food",
            category_1="pantry",
        )
        b = _make_product(
            item_id="444",
            source="B",
            brand_norm="skippy",
            is_private_label=False,
            brand_inferred=False,
            size=SizeInfo(unit="oz", unit_size=16.0, pack_count=1, total_size=16.0),
            category_0="grocery",
            category_1="pantry",
        )
        result = evaluate_hard_rules(a, b)
        assert result.reason != "brand_mismatch"


# ---------------------------------------------------------------------------
# Size mismatch
# ---------------------------------------------------------------------------

class TestSizeMismatch:
    def test_tomato_8oz_vs_15oz(self, a_index, b_index):
        a = a_index["1929544"]
        b = b_index["103620"]
        result = evaluate_hard_rules(a, b)
        assert result.passed is False
        assert result.reason == "size_mismatch"

    def test_tomato_8oz_vs_29oz(self, a_index, b_index):
        a = a_index["1929544"]
        b = b_index["1086860"]
        result = evaluate_hard_rules(a, b)
        assert result.passed is False
        assert result.reason == "size_mismatch"

    def test_jif_16oz_vs_18oz(self):
        # Synthetic: B-side 'Nut Butters & Spreads' c1 is not in the
        # taxonomy mapping, so the fixture pair group-mismatches before
        # the size rule. Use synthetic objects that share a mapped group
        # (pantry) so the size rule is what we actually exercise.
        a = _make_product(
            item_id="9000007",
            source="A",
            brand_norm="jif",
            is_private_label=False,
            brand_inferred=False,
            size=SizeInfo(unit="oz", unit_size=16.0, pack_count=1, total_size=16.0),
            category_0="food",
            category_1="pantry",
            core_name="creamy peanut butter",
        )
        b = _make_product(
            item_id="9100006",
            source="B",
            brand_norm="jif",
            is_private_label=False,
            brand_inferred=False,
            size=SizeInfo(unit="oz", unit_size=18.0, pack_count=1, total_size=18.0),
            category_0="grocery",
            category_1="pantry",
            core_name="creamy peanut butter",
        )
        result = evaluate_hard_rules(a, b)
        assert result.passed is False
        assert result.reason == "size_mismatch"

    def test_16oz_equals_1lb_same_family(self):
        # Synthetic: 16 oz weight should equal 1 lb after conversion.
        # Use c1='pantry' on both sides so the taxonomy assigns 'pantry'
        # to each (B 'baking' c1 is not in the grocery mapping).
        a = _make_product(
            item_id="555",
            source="A",
            brand_norm="armhammer",
            size=SizeInfo(unit="oz", unit_size=16.0, pack_count=1, total_size=16.0),
            category_0="food",
            category_1="pantry",
            category_2="baking",
        )
        b = _make_product(
            item_id="666",
            source="B",
            brand_norm="armhammer",
            size=SizeInfo(unit="lb", unit_size=1.0, pack_count=1, total_size=1.0),
            category_0="grocery",
            category_1="pantry",
            category_2="leavening agents",
        )
        result = evaluate_hard_rules(a, b)
        assert result.passed is True, f"expected pass, got {result.reason}"

    def test_8oz_weight_vs_8floz_volume_no_size_reject(self):
        # Different families: rule must skip (not compare 8 vs 8 as equal,
        # and not falsely reject either).
        a = _make_product(
            item_id="777",
            source="A",
            brand_norm="acme",
            size=SizeInfo(unit="oz", unit_size=8.0, pack_count=1, total_size=8.0),
            category_0="food",
            category_1="pantry",
        )
        b = _make_product(
            item_id="888",
            source="B",
            brand_norm="acme",
            size=SizeInfo(unit="fl oz", unit_size=8.0, pack_count=1, total_size=8.0),
            category_0="grocery",
            category_1="pantry",
        )
        result = evaluate_hard_rules(a, b)
        # Rule must skip rather than fire.
        assert result.reason != "size_mismatch"


# ---------------------------------------------------------------------------
# Storage mismatch
# ---------------------------------------------------------------------------

class TestStorageMismatch:
    def test_progresso_frozen_vs_shelf_stable(self):
        # Synthetic: B fixture's c1='Soups' isn't in the grocery taxonomy
        # mapping, so the fixture pair group-mismatches before the storage
        # rule. Use synthetic objects in a mapped group (pantry) to actually
        # exercise the storage rule.
        a = _make_product(
            item_id="9000008",
            source="A",
            brand_norm="progresso",
            is_private_label=False,
            size=SizeInfo(unit="oz", unit_size=18.0, pack_count=1, total_size=18.0),
            category_0="food",
            category_1="pantry",
            storage_type="frozen",
            core_name="homestyle chicken noodle soup",
        )
        b = _make_product(
            item_id="9100007",
            source="B",
            brand_norm="progresso",
            is_private_label=False,
            size=SizeInfo(unit="oz", unit_size=19.0, pack_count=1, total_size=19.0),
            category_0="grocery",
            category_1="pantry",
            storage_type="shelf_stable",
            core_name="traditional chicken noodle soup",
        )
        result = evaluate_hard_rules(a, b)
        assert result.passed is False
        assert result.reason == "storage_mismatch"


# ---------------------------------------------------------------------------
# Form mismatch
# ---------------------------------------------------------------------------

class TestFormMismatch:
    def test_powder_vs_liquid(self, a_index, b_index):
        a = a_index["9000009"]
        b = b_index["9100008"]
        result = evaluate_hard_rules(a, b)
        assert result.passed is False
        assert result.reason == "form_mismatch"

    def test_sliced_vs_shredded(self, a_index, b_index):
        a = a_index["9000010"]
        b = b_index["9100009"]
        result = evaluate_hard_rules(a, b)
        assert result.passed is False
        assert result.reason == "form_mismatch"


# ---------------------------------------------------------------------------
# Pet type mismatch (synthetic — fixture B dog row has group None)
# ---------------------------------------------------------------------------

class TestPetTypeMismatch:
    def test_cat_vs_dog_in_pets_group(self):
        a = _make_product(
            item_id="9000011",
            source="A",
            brand_norm="purina one",
            size=SizeInfo(unit="oz", unit_size=7.0, pack_count=1, total_size=7.0),
            category_0="pets",
            category_1="cat supplies",
            category_2="cat food",
            core_name="tender selects blend adult cat food with real chicken",
        )
        b = _make_product(
            item_id="9100010",
            source="B",
            brand_norm="purina one",
            size=SizeInfo(unit="oz", unit_size=7.0, pack_count=1, total_size=7.0),
            category_0="pets",
            category_1="dog supplies",
            category_2="dog food",
            core_name="smartblend dog food with real chicken",
        )
        result = evaluate_hard_rules(a, b)
        assert result.passed is False
        assert result.reason == "pet_type_mismatch"


# ---------------------------------------------------------------------------
# Flavor mismatch
# ---------------------------------------------------------------------------

class TestFlavorMismatch:
    def test_vanilla_vs_chocolate_ice_cream(self, a_index, b_index):
        a = a_index["9000013"]
        b = b_index["9100012"]
        result = evaluate_hard_rules(a, b)
        assert result.passed is False
        assert result.reason == "flavor_mismatch"


# ---------------------------------------------------------------------------
# None semantics: unknown attribute on either side must not reject.
# ---------------------------------------------------------------------------

class TestNoneSemantics:
    def test_form_none_does_not_reject(self):
        a = _make_product(
            item_id="a1",
            source="A",
            brand_norm="acme",
            size=SizeInfo(unit="oz", unit_size=10.0, pack_count=1, total_size=10.0),
            category_0="food",
            category_1="pantry",
            form=None,
        )
        b = _make_product(
            item_id="b1",
            source="B",
            brand_norm="acme",
            size=SizeInfo(unit="oz", unit_size=10.0, pack_count=1, total_size=10.0),
            category_0="grocery",
            category_1="pantry",
            form="powder",
        )
        result = evaluate_hard_rules(a, b)
        assert result.reason != "form_mismatch"

    def test_storage_none_does_not_reject(self):
        a = _make_product(
            item_id="a2",
            source="A",
            brand_norm="acme",
            size=SizeInfo(unit="oz", unit_size=10.0, pack_count=1, total_size=10.0),
            category_0="food",
            category_1="pantry",
            storage_type=None,
        )
        b = _make_product(
            item_id="b2",
            source="B",
            brand_norm="acme",
            size=SizeInfo(unit="oz", unit_size=10.0, pack_count=1, total_size=10.0),
            category_0="grocery",
            category_1="pantry",
            storage_type="frozen",
        )
        result = evaluate_hard_rules(a, b)
        assert result.reason != "storage_mismatch"

    def test_flavor_none_does_not_reject(self):
        a = _make_product(
            item_id="a3",
            source="A",
            brand_norm="acme",
            size=SizeInfo(unit="oz", unit_size=10.0, pack_count=1, total_size=10.0),
            category_0="food",
            category_1="pantry",
            flavor=None,
        )
        b = _make_product(
            item_id="b3",
            source="B",
            brand_norm="acme",
            size=SizeInfo(unit="oz", unit_size=10.0, pack_count=1, total_size=10.0),
            category_0="grocery",
            category_1="pantry",
            flavor="vanilla",
        )
        result = evaluate_hard_rules(a, b)
        assert result.reason != "flavor_mismatch"

    def test_organic_true_vs_false_does_not_hard_reject(self):
        # Organic is scoring-only; mismatch must not produce a hard reject.
        a = _make_product(
            item_id="a4",
            source="A",
            brand_norm="acme",
            is_organic=True,
            size=SizeInfo(unit="oz", unit_size=10.0, pack_count=1, total_size=10.0),
            category_0="food",
            category_1="pantry",
        )
        b = _make_product(
            item_id="b4",
            source="B",
            brand_norm="acme",
            is_organic=False,
            size=SizeInfo(unit="oz", unit_size=10.0, pack_count=1, total_size=10.0),
            category_0="grocery",
            category_1="pantry",
        )
        result = evaluate_hard_rules(a, b)
        assert result.passed is True
        assert result.reason == "ok"


# ---------------------------------------------------------------------------
# Alcohol false-positive guards (M1)
# ---------------------------------------------------------------------------

class TestAlcoholFalsePositives:
    """Phrases that contain an alcohol keyword but describe non-alcoholic
    products. The keyword fallback must not flag these as alcoholic."""

    def _bev(self, item_id: str, source: str, core_name: str) -> NormalizedProduct:
        c0 = "food" if source == "A" else "grocery"
        return _make_product(
            item_id=item_id,
            source=source,
            brand_norm="acme",
            size=SizeInfo(unit="fl oz", unit_size=12.0, pack_count=1, total_size=12.0),
            category_0=c0,
            category_1="beverages",
            core_name=core_name,
        )

    def _pantry(self, item_id: str, source: str, core_name: str) -> NormalizedProduct:
        c0 = "food" if source == "A" else "grocery"
        return _make_product(
            item_id=item_id,
            source=source,
            brand_norm="acme",
            size=SizeInfo(unit="fl oz", unit_size=12.0, pack_count=1, total_size=12.0),
            category_0=c0,
            category_1="pantry",
            core_name=core_name,
        )

    def test_ginger_beer_pair_no_alcohol_mismatch(self):
        a = self._bev("gba", "A", "ginger beer")
        b = self._bev("gbb", "B", "ginger beer")
        result = evaluate_hard_rules(a, b)
        assert result.reason != "alcohol_mismatch"

    def test_root_beer_pair_no_alcohol_mismatch(self):
        a = self._bev("rba", "A", "classic root beer")
        b = self._bev("rbb", "B", "classic root beer")
        result = evaluate_hard_rules(a, b)
        assert result.reason != "alcohol_mismatch"

    def test_ginger_ale_pair_no_alcohol_mismatch(self):
        a = self._bev("gaa", "A", "ginger ale")
        b = self._bev("gab", "B", "ginger ale")
        result = evaluate_hard_rules(a, b)
        assert result.reason != "alcohol_mismatch"

    def test_wine_vinegar_pair_no_alcohol_mismatch(self):
        a = self._pantry("wva", "A", "red wine vinegar")
        b = self._pantry("wvb", "B", "red wine vinegar")
        result = evaluate_hard_rules(a, b)
        assert result.reason != "alcohol_mismatch"

    def test_cooking_wine_pair_no_alcohol_mismatch(self):
        a = self._pantry("cwa", "A", "cooking wine")
        b = self._pantry("cwb", "B", "cooking wine")
        result = evaluate_hard_rules(a, b)
        assert result.reason != "alcohol_mismatch"

    def test_rum_cake_pair_no_alcohol_mismatch(self):
        # Bakery items both sides; rum keyword in name must not flag.
        a = _make_product(
            item_id="rca", source="A", brand_norm="acme",
            size=SizeInfo(unit="oz", unit_size=8.0, pack_count=1, total_size=8.0),
            category_0="food", category_1="bakery",
            core_name="dark rum cake",
        )
        b = _make_product(
            item_id="rcb", source="B", brand_norm="acme",
            size=SizeInfo(unit="oz", unit_size=8.0, pack_count=1, total_size=8.0),
            category_0="bakery",
            core_name="dark rum cake",
        )
        result = evaluate_hard_rules(a, b)
        assert result.reason != "alcohol_mismatch"

    def test_beer_cheese_pair_no_alcohol_mismatch(self):
        a = _make_product(
            item_id="bca", source="A", brand_norm="acme",
            size=SizeInfo(unit="oz", unit_size=8.0, pack_count=1, total_size=8.0),
            category_0="food", category_1="dairy", category_2="cheese spreads",
            core_name="beer cheese spread",
        )
        b = _make_product(
            item_id="bcb", source="B", brand_norm="acme",
            size=SizeInfo(unit="oz", unit_size=8.0, pack_count=1, total_size=8.0),
            category_0="dairy", category_1="cheese",
            core_name="beer cheese spread",
        )
        result = evaluate_hard_rules(a, b)
        assert result.reason != "alcohol_mismatch"

    # Asymmetric pairs: these are the cases where the false-positive
    # actually manifests as a wrong rule outcome under the buggy code.

    def test_ginger_beer_vs_plain_soda_no_alcohol_mismatch(self):
        a = self._bev("gba2", "A", "ginger beer")
        b = self._bev("gbb2", "B", "lemon soda")
        result = evaluate_hard_rules(a, b)
        assert result.reason != "alcohol_mismatch"

    def test_root_beer_vs_cola_no_alcohol_mismatch(self):
        a = self._bev("rba2", "A", "classic root beer")
        b = self._bev("rbb2", "B", "cola")
        result = evaluate_hard_rules(a, b)
        assert result.reason != "alcohol_mismatch"

    def test_wine_vinegar_vs_apple_vinegar_no_alcohol_mismatch(self):
        a = self._pantry("wva2", "A", "red wine vinegar")
        b = self._pantry("wvb2", "B", "apple cider vinegar")
        result = evaluate_hard_rules(a, b)
        assert result.reason != "alcohol_mismatch"

    def test_cooking_wine_vs_cooking_oil_no_alcohol_mismatch(self):
        a = self._pantry("cwa2", "A", "cooking wine")
        b = self._pantry("cwb2", "B", "cooking oil")
        result = evaluate_hard_rules(a, b)
        assert result.reason != "alcohol_mismatch"

    def test_rum_cake_vs_pound_cake_no_alcohol_mismatch(self):
        a = _make_product(
            item_id="rca2", source="A", brand_norm="acme",
            size=SizeInfo(unit="oz", unit_size=8.0, pack_count=1, total_size=8.0),
            category_0="food", category_1="bakery",
            core_name="dark rum cake",
        )
        b = _make_product(
            item_id="rcb2", source="B", brand_norm="acme",
            size=SizeInfo(unit="oz", unit_size=8.0, pack_count=1, total_size=8.0),
            category_0="bakery",
            core_name="pound cake",
        )
        result = evaluate_hard_rules(a, b)
        assert result.reason != "alcohol_mismatch"

    def test_vodka_vs_juice_still_alcohol_mismatch(self):
        # Keyword fallback must still fire when the term is genuinely alcoholic.
        # A: vodka in the beverages group (lands in beverages by taxonomy
        # because c2 lacks the alcohol tokens; alcohol detection then comes
        # from the keyword regex). B: orange juice, no alcohol terms.
        a = self._bev("vka", "A", "premium vodka")
        b = self._bev("vkb", "B", "orange juice")
        result = evaluate_hard_rules(a, b)
        assert result.passed is False
        assert result.reason == "alcohol_mismatch"


# ---------------------------------------------------------------------------
# Pack mismatch (M2): explicit positive and negative coverage.
# ---------------------------------------------------------------------------

class TestPackMismatch:
    def test_pack_mismatch_fires_when_both_multi_and_total_drifts(self):
        # 2x5.0=10.0 vs 4x2.625=10.5 → diff 4.76% (under size_mismatch's 8%);
        # both packs > 1, packs differ, diff > 1% → pack_mismatch must fire.
        a = _make_product(
            item_id="pma", source="A", brand_norm="acme",
            size=SizeInfo(unit="oz", unit_size=5.0, pack_count=2, total_size=10.0),
            category_0="food", category_1="pantry",
            core_name="snack bars",
        )
        b = _make_product(
            item_id="pmb", source="B", brand_norm="acme",
            size=SizeInfo(unit="oz", unit_size=2.625, pack_count=4, total_size=10.5),
            category_0="grocery", category_1="pantry",
            core_name="snack bars",
        )
        result = evaluate_hard_rules(a, b)
        assert result.passed is False
        assert result.reason == "pack_mismatch"

    def test_pack_equal_no_pack_mismatch(self):
        a = _make_product(
            item_id="pea", source="A", brand_norm="acme",
            size=SizeInfo(unit="oz", unit_size=5.0, pack_count=4, total_size=20.0),
            category_0="food", category_1="pantry",
        )
        b = _make_product(
            item_id="peb", source="B", brand_norm="acme",
            size=SizeInfo(unit="oz", unit_size=5.0, pack_count=4, total_size=20.0),
            category_0="grocery", category_1="pantry",
        )
        result = evaluate_hard_rules(a, b)
        assert result.passed is True
        assert result.reason == "ok"

    def test_single_vs_multipack_equal_total_no_pack_mismatch(self):
        # 1x32oz vs 4x8oz, totals equal. size_mismatch doesn't fire (equal),
        # pack_mismatch must not fire either (the single-vs-multi branch is
        # removed; size_mismatch is the primary guard for total-size drift).
        a = _make_product(
            item_id="sva", source="A", brand_norm="acme",
            size=SizeInfo(unit="oz", unit_size=32.0, pack_count=1, total_size=32.0),
            category_0="food", category_1="pantry",
        )
        b = _make_product(
            item_id="svb", source="B", brand_norm="acme",
            size=SizeInfo(unit="oz", unit_size=8.0, pack_count=4, total_size=32.0),
            category_0="grocery", category_1="pantry",
        )
        result = evaluate_hard_rules(a, b)
        assert result.passed is True, f"expected pass, got {result.reason}"

    def test_both_multi_but_total_within_one_percent_no_pack_mismatch(self):
        # 2x5.0=10.0 vs 4x2.5=10.0 — packs differ but total identical;
        # rule must not fire (intentional, multipack repackage tolerance).
        a = _make_product(
            item_id="pmc", source="A", brand_norm="acme",
            size=SizeInfo(unit="oz", unit_size=5.0, pack_count=2, total_size=10.0),
            category_0="food", category_1="pantry",
        )
        b = _make_product(
            item_id="pmd", source="B", brand_norm="acme",
            size=SizeInfo(unit="oz", unit_size=2.5, pack_count=4, total_size=10.0),
            category_0="grocery", category_1="pantry",
        )
        result = evaluate_hard_rules(a, b)
        assert result.reason != "pack_mismatch"
