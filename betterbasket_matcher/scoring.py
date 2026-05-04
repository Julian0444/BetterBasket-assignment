"""Deterministic weighted scoring and best-candidate selection (Phase 6).

score_pair produces a per-pair ScoreBreakdown using fixed component weights
(core_name 0.35, token_overlap 0.15, brand 0.15, size_pack 0.20, group 0.10,
attributes 0.05). select_best applies the hard rules from rules.py and
ranks survivors with a stable tie-break. No I/O, no pipeline orchestration,
no LLM.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Optional, Tuple

from betterbasket_matcher.normalize import NormalizedProduct
from betterbasket_matcher.rules import (
    _to_base_size,
    _relative_diff,
    evaluate_hard_rules,
)
from betterbasket_matcher.taxonomy import assign_matchable_group


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ScoreBreakdown:
    total: float
    core_name: float
    token_overlap: float
    brand: float
    size_pack: float
    group: float
    attributes: float


@dataclass(frozen=True)
class SelectionResult:
    selected_item_id_b: Optional[str]
    score: Optional[float]
    margin: Optional[float]
    reason: str
    breakdown: Optional[ScoreBreakdown]
    rejected: tuple
    runner_up_item_id_b: Optional[str]


# ---------------------------------------------------------------------------
# Component weights
# ---------------------------------------------------------------------------

_W_CORE_NAME = 0.35
_W_TOKEN = 0.15
_W_BRAND = 0.15
_W_SIZE_PACK = 0.20
_W_GROUP = 0.10
_W_ATTRS = 0.05

_DAIRY_CHEESE = frozenset({"dairy", "cheese"})

# Tokens dropped from token_overlap (units / pack words / numbers handled separately)
_UNIT_TOKENS = frozenset({
    "oz", "lb", "lbs", "g", "kg", "ml", "l", "gal", "ct", "pk",
    "count", "fl", "pack", "ounce", "ounces",
})


# ---------------------------------------------------------------------------
# Component scorers
# ---------------------------------------------------------------------------

def _clip01(x: float) -> float:
    if x < 0.0:
        return 0.0
    if x > 1.0:
        return 1.0
    return x


def _score_core_name(retrieval_score: float) -> float:
    """Phase 5 hstacks two L2-normalized blocks; max dot product is ~2.0."""
    return _clip01(retrieval_score / 2.0)


def _tokenize_for_overlap(text: str) -> set:
    if not text:
        return set()
    out = set()
    for tok in text.split():
        t = tok.strip().lower()
        if not t:
            continue
        if t in _UNIT_TOKENS:
            continue
        # drop pure-numeric tokens (e.g. "5.3", "12")
        try:
            float(t)
            continue
        except ValueError:
            pass
        out.add(t)
    return out


def _score_token_overlap(a: NormalizedProduct, b: NormalizedProduct) -> float:
    ta = _tokenize_for_overlap(a.core_name)
    tb = _tokenize_for_overlap(b.core_name)
    if not ta or not tb:
        return 0.0
    inter = ta & tb
    union = ta | tb
    return len(inter) / len(union)


def _score_brand(a: NormalizedProduct, b: NormalizedProduct) -> float:
    ba, bb = a.brand_norm, b.brand_norm
    # Cross-store private-label match: Great Value <-> Wegmans, etc.
    if a.is_private_label and b.is_private_label:
        return 0.85
    a_known = bool(ba) and not a.brand_inferred
    b_known = bool(bb) and not b.brand_inferred
    if a_known and b_known:
        return 1.0 if ba == bb else 0.0
    if not ba and not bb:
        return 0.5
    # Exactly one side is empty or inferred -> partial compatibility.
    return 0.6


def _score_size_pack(a: NormalizedProduct, b: NormalizedProduct) -> float:
    base_a = _to_base_size(a.size)
    base_b = _to_base_size(b.size)
    if base_a is None or base_b is None:
        return 0.4
    if base_a[0] != base_b[0]:
        return 0.0
    diff = _relative_diff(base_a[1], base_b[1])
    pack_equal = a.size.pack_count == b.size.pack_count
    if diff <= 0.01 and pack_equal:
        return 1.0
    if diff <= 0.05 and pack_equal:
        return 0.7
    return 0.0


def _score_group(group_a: Optional[str], group_b: Optional[str]) -> float:
    if not group_a or not group_b:
        return 0.0
    if group_a == group_b:
        return 1.0
    if group_a in _DAIRY_CHEESE and group_b in _DAIRY_CHEESE:
        return 0.7
    return 0.0


def _attr_pair_score(va, vb, treat_none_as_unknown: bool) -> float:
    """1.0 equal, 0.0 conflict, 0.5 unknown (when treat_none_as_unknown)."""
    if treat_none_as_unknown and (va is None or vb is None):
        return 0.5
    if va == vb:
        return 1.0
    return 0.0


def _score_attributes(a: NormalizedProduct, b: NormalizedProduct) -> float:
    parts: List[float] = []
    parts.append(_attr_pair_score(a.storage_type, b.storage_type, True))
    parts.append(_attr_pair_score(a.form, b.form, True))
    parts.append(_attr_pair_score(a.flavor, b.flavor, True))
    # is_organic: True/False both observable; True-vs-False is a real conflict.
    parts.append(_attr_pair_score(a.is_organic, b.is_organic, False))
    return sum(parts) / len(parts)


# ---------------------------------------------------------------------------
# Public: score_pair
# ---------------------------------------------------------------------------

def score_pair(
    a: NormalizedProduct,
    b: NormalizedProduct,
    retrieval_score: float,
) -> ScoreBreakdown:
    cn = _score_core_name(retrieval_score)
    to = _score_token_overlap(a, b)
    br = _score_brand(a, b)
    sp = _score_size_pack(a, b)
    gr = _score_group(assign_matchable_group(a), assign_matchable_group(b))
    at = _score_attributes(a, b)
    total = (
        _W_CORE_NAME * cn
        + _W_TOKEN * to
        + _W_BRAND * br
        + _W_SIZE_PACK * sp
        + _W_GROUP * gr
        + _W_ATTRS * at
    )
    return ScoreBreakdown(
        total=total,
        core_name=cn,
        token_overlap=to,
        brand=br,
        size_pack=sp,
        group=gr,
        attributes=at,
    )


# ---------------------------------------------------------------------------
# Tie-break helpers
# ---------------------------------------------------------------------------

def _exact_size_match(a: NormalizedProduct, b: NormalizedProduct) -> bool:
    base_a = _to_base_size(a.size)
    base_b = _to_base_size(b.size)
    if base_a is None or base_b is None:
        return False
    if base_a[0] != base_b[0]:
        return False
    return _relative_diff(base_a[1], base_b[1]) <= 0.01


def _attr_agreement_count(a: NormalizedProduct, b: NormalizedProduct) -> int:
    n = 0
    if a.storage_type and b.storage_type and a.storage_type == b.storage_type:
        n += 1
    if a.form and b.form and a.form == b.form:
        n += 1
    if a.flavor and b.flavor and a.flavor == b.flavor:
        n += 1
    if a.is_organic == b.is_organic:
        n += 1
    return n


def _b_metadata_richness(b: NormalizedProduct) -> int:
    n = 0
    if b.storage_type is not None:
        n += 1
    if b.form is not None:
        n += 1
    if b.flavor is not None:
        n += 1
    return n


def _tiebreak_key(
    a: NormalizedProduct,
    b: NormalizedProduct,
    bd: ScoreBreakdown,
):
    # Sort key: descending preference. Negate numeric items so we can use
    # ascending sort and put the winner first.
    pack_equal = a.size.pack_count == b.size.pack_count
    try:
        id_int = int(b.item_id)
    except (TypeError, ValueError):
        id_int = 10**18  # push non-numeric ids to the end deterministically
    return (
        -bd.total,
        -int(_exact_size_match(a, b)),
        -int(pack_equal),
        -_attr_agreement_count(a, b),
        -_b_metadata_richness(b),
        id_int,
    )


# ---------------------------------------------------------------------------
# Public: select_best
# ---------------------------------------------------------------------------

def select_best(
    a: NormalizedProduct,
    candidate_pairs: Iterable[Tuple[NormalizedProduct, float]],
    min_score: float,
    min_margin: float,
) -> SelectionResult:
    pairs = list(candidate_pairs)
    if not pairs:
        return SelectionResult(
            selected_item_id_b=None,
            score=None,
            margin=None,
            reason="no_candidates",
            breakdown=None,
            rejected=tuple(),
            runner_up_item_id_b=None,
        )

    survivors: List[Tuple[NormalizedProduct, float, ScoreBreakdown]] = []
    rejected: List[Tuple[str, str]] = []
    for b, retrieval_score in pairs:
        rule = evaluate_hard_rules(a, b)
        if not rule.passed:
            rejected.append((b.item_id, rule.reason))
            continue
        bd = score_pair(a, b, retrieval_score)
        survivors.append((b, retrieval_score, bd))

    if not survivors:
        return SelectionResult(
            selected_item_id_b=None,
            score=None,
            margin=None,
            reason="all_rejected",
            breakdown=None,
            rejected=tuple(rejected),
            runner_up_item_id_b=None,
        )

    survivors.sort(key=lambda triple: _tiebreak_key(a, triple[0], triple[2]))
    top_b, _, top_bd = survivors[0]
    if len(survivors) > 1:
        runner_b = survivors[1][0]
        margin: Optional[float] = top_bd.total - survivors[1][2].total
    else:
        # No runner-up to compare against; min_margin does not apply.
        runner_b = None
        margin = None
    runner_id = runner_b.item_id if runner_b else None

    if top_bd.total < min_score:
        return SelectionResult(
            selected_item_id_b=None,
            score=top_bd.total,
            margin=margin,
            reason="below_min_score",
            breakdown=top_bd,
            rejected=tuple(rejected),
            runner_up_item_id_b=runner_id,
        )

    if margin is not None and margin < min_margin:
        return SelectionResult(
            selected_item_id_b=None,
            score=top_bd.total,
            margin=margin,
            reason="below_min_margin",
            breakdown=top_bd,
            rejected=tuple(rejected),
            runner_up_item_id_b=runner_id,
        )

    return SelectionResult(
        selected_item_id_b=top_b.item_id,
        score=top_bd.total,
        margin=margin,
        reason="selected",
        breakdown=top_bd,
        rejected=tuple(rejected),
        runner_up_item_id_b=runner_id,
    )
