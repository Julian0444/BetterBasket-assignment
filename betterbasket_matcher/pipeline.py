"""End-to-end fixture pipeline orchestration (Phase 7, extended in Phase 10C).

Wires:

    read_products -> normalize_product -> is_a_in_scope (A only)
                  -> TfidfRetriever.fit (B) / .query (per A)
                  -> select_best (rules + scoring)
                  -> matches.csv + matches_audit.csv

The pipeline writes a single best B per A. It does NOT call ``is_a_in_scope``
inside ``TfidfRetriever.query``; the scope filter lives here so audit output
can record the stable scope reason. Out-of-scope and in-scope-with-no-
candidates rows still produce one audit row per valid A (post-quarantine,
post-limit). Quarantined A rows never appear in either output file.

When ``PipelineConfig.arbiter`` is None (the default), every audit row
carries source="deterministic" and an empty ``llm_confidence`` cell —
behavior is byte-identical to the Phase 7 contract. When an
``LLMArbiter`` is provided, the pipeline runs in two passes inside one
call:

  Pass 1 — the loop's ``below_threshold`` branch records eligible
  gray-zone candidates onto the arbiter (``arbiter.collect``) and
  writes the existing ``below_threshold`` audit row.

  Pass 2 — after the loop, ``arbiter.commit()`` returns
  ``{item_id_a: ArbiterOpinion}``. The pipeline walks ``audit_rows``
  once and flips qualifying ``below_threshold`` rows to ``accepted``
  with ``source="llm_rescued"`` and a populated ``llm_confidence``.
  ``result.matches`` is rebuilt from the final audit rows so
  ``matches.csv`` reflects the rescues.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

from betterbasket_matcher.io import read_products
from betterbasket_matcher.normalize import NormalizedProduct, normalize_product
from betterbasket_matcher.output import write_matches, write_matches_audit
from betterbasket_matcher.retrieval import TfidfRetriever
from betterbasket_matcher.scope import is_a_in_scope
from betterbasket_matcher.scoring import select_best


SOURCE_DETERMINISTIC = "deterministic"
SOURCE_LLM_RESCUED = "llm_rescued"

# Phase 10C rescue-eligibility window. The arbiter is offered candidates
# only inside this score band so it spends budget on the most likely
# true positives. Pairs outside the window (already-accepted, very-low
# score, etc.) are not exposed to the arbiter at all.
LLM_RESCUE_SCORE_LOW = 0.65
LLM_RESCUE_SCORE_HIGH = 0.75


@dataclass
class PipelineConfig:
    a_csv: Union[str, Path]
    b_csv: Union[str, Path]
    matches_out: Union[str, Path]
    audit_out: Union[str, Path]
    min_score: float = 0.55
    min_margin: float = 0.05
    top_k: int = 50
    limit: Optional[int] = None
    # Phase 10C optional plumbing. None preserves Phase 7/10B behavior
    # byte-for-byte.
    arbiter: Optional[object] = None  # forward-typed: betterbasket_matcher.llm_arbiter.LLMArbiter
    a_raw_by_id: Optional[Dict[str, dict]] = None
    b_raw_by_id: Optional[Dict[str, dict]] = None


@dataclass
class PipelineResult:
    matches: List[Tuple[str, str]] = field(default_factory=list)
    audit_rows: List[Dict[str, str]] = field(default_factory=list)
    valid_a_count: int = 0
    quarantined_a_count: int = 0
    valid_b_count: int = 0
    in_scope_a_count: int = 0
    accepted_count: int = 0
    rejected_by_rule_count: int = 0
    below_threshold_count: int = 0
    no_candidates_count: int = 0
    # Phase 10C stat counters; remain 0 when arbiter is None.
    llm_calls: int = 0
    llm_rescues: int = 0
    llm_cache_hits: int = 0


def _fmt(value) -> str:
    """Serialize a numeric audit cell. None -> empty string."""
    if value is None:
        return ""
    return repr(float(value)) if isinstance(value, float) else str(value)


def _empty_audit_row(a_id: str) -> Dict[str, str]:
    return {
        "item_id_A": a_id,
        "item_id_B": "",
        "score": "",
        "retrieval_score": "",
        "top1_top2_margin": "",
        "source": SOURCE_DETERMINISTIC,
        "decision": "",
        "reason": "",
        "llm_confidence": "",
    }


def run_pipeline(config: PipelineConfig) -> PipelineResult:
    """Run the end-to-end fixture pipeline.

    Steps:
      1. Read A and B CSVs; quarantine non-numeric / blank-name A rows.
      2. Normalize A and B to NormalizedProduct.
      3. Apply ``config.limit`` to the normalized A list (post-quarantine,
         pre-scope) when set.
      4. Build a B-id index and fit TfidfRetriever on the normalized B corpus.
      5. For each A: scope filter -> retrieval -> select_best -> emit one
         audit row; append to matches.csv only on ``decision == accepted``.
      6. Write the two output CSVs.
    """
    valid_a_rows, quarantined_a = read_products(config.a_csv, "A")
    valid_b_rows, _ = read_products(config.b_csv, "B")

    a_products: List[NormalizedProduct] = [
        normalize_product(r, "A") for r in valid_a_rows
    ]
    b_products: List[NormalizedProduct] = [
        normalize_product(r, "B") for r in valid_b_rows
    ]

    if config.limit is not None:
        a_products = a_products[: config.limit]

    b_index: Dict[str, NormalizedProduct] = {p.item_id: p for p in b_products}

    # Raw-row lookups are only built when the arbiter is active; the
    # arbiter needs the human-readable display names which live only on
    # the raw CSV rows. Caller may also pre-supply them on the config.
    if config.arbiter is not None:
        a_raw_by_id: Dict[str, dict] = (
            config.a_raw_by_id
            if config.a_raw_by_id is not None
            else {r["item_id"]: r for r in valid_a_rows}
        )
        b_raw_by_id: Dict[str, dict] = (
            config.b_raw_by_id
            if config.b_raw_by_id is not None
            else {r["item_id"]: r for r in valid_b_rows}
        )
    else:
        a_raw_by_id = {}
        b_raw_by_id = {}

    retriever = TfidfRetriever().fit(b_products)

    result = PipelineResult(
        valid_a_count=len(a_products),
        quarantined_a_count=len(quarantined_a),
        valid_b_count=len(b_products),
    )

    for a in a_products:
        in_scope, scope_reason = is_a_in_scope(a)
        if not in_scope:
            row = _empty_audit_row(a.item_id)
            row["decision"] = "no_candidates"
            row["reason"] = scope_reason
            result.audit_rows.append(row)
            result.no_candidates_count += 1
            continue

        result.in_scope_a_count += 1

        cands = retriever.query(a, k=config.top_k)
        if not cands:
            row = _empty_audit_row(a.item_id)
            row["decision"] = "no_candidates"
            row["reason"] = "no_candidates"
            result.audit_rows.append(row)
            result.no_candidates_count += 1
            continue

        retrieval_lookup: Dict[str, float] = {c.item_id_b: c.score for c in cands}
        pairs = [(b_index[c.item_id_b], c.score) for c in cands]
        sel = select_best(a, pairs, config.min_score, config.min_margin)

        row = _empty_audit_row(a.item_id)

        if sel.reason == "selected":
            chosen = sel.selected_item_id_b
            row["item_id_B"] = chosen
            row["decision"] = "accepted"
            row["reason"] = "ok"
            row["score"] = _fmt(sel.score)
            row["retrieval_score"] = _fmt(retrieval_lookup.get(chosen))
            row["top1_top2_margin"] = _fmt(sel.margin)
            result.matches.append((a.item_id, chosen))
            result.accepted_count += 1

        elif sel.reason in ("below_min_score", "below_min_margin"):
            top = sel.top_item_id_b
            row["item_id_B"] = top or ""
            row["decision"] = "below_threshold"
            row["reason"] = sel.reason
            row["score"] = _fmt(sel.score)
            row["retrieval_score"] = _fmt(retrieval_lookup.get(top)) if top else ""
            row["top1_top2_margin"] = _fmt(sel.margin)
            result.below_threshold_count += 1

            # Phase 10C: offer this row to the optional arbiter for
            # gray-zone rescue. The arbiter records the candidate now;
            # API calls (if any) happen in the post-loop commit pass.
            # Pairs that fail the score-window or have no top survivor
            # are silently ignored by collect().
            if (
                config.arbiter is not None
                and top is not None
                and sel.score is not None
                and LLM_RESCUE_SCORE_LOW <= sel.score < LLM_RESCUE_SCORE_HIGH
            ):
                config.arbiter.collect(
                    a=a,
                    b=b_index[top],
                    a_raw=a_raw_by_id.get(a.item_id, {}),
                    b_raw=b_raw_by_id.get(top, {}),
                    deterministic_score=sel.score,
                    deterministic_margin=sel.margin,
                    retrieval_score=retrieval_lookup.get(top),
                    failure_reason=sel.reason,
                )

        elif sel.reason == "all_rejected":
            first_id, first_reason = sel.rejected[0]
            row["item_id_B"] = first_id
            row["decision"] = "rejected_by_rule"
            row["reason"] = first_reason
            row["retrieval_score"] = _fmt(retrieval_lookup.get(first_id))
            result.rejected_by_rule_count += 1

        else:  # "no_candidates" defensively
            row["decision"] = "no_candidates"
            row["reason"] = "no_candidates"
            result.no_candidates_count += 1

        result.audit_rows.append(row)

    # ------------------------------------------------------------------
    # Phase 10C: pass-2 — commit the arbiter and apply rescues in place.
    # ------------------------------------------------------------------
    if config.arbiter is not None:
        opinions = config.arbiter.commit()
        # Stat counters: cache_hits / calls_made are arbiter-internal.
        result.llm_calls = getattr(config.arbiter, "calls_made", 0)
        result.llm_cache_hits = getattr(config.arbiter, "cache_hits", 0)
        min_conf = getattr(config.arbiter, "min_confidence", 0.0)

        if opinions:
            for row in result.audit_rows:
                if row["decision"] != "below_threshold":
                    continue
                op = opinions.get(row["item_id_A"])
                if op is None:
                    continue
                # Always record the LLM confidence for traceability,
                # even when the opinion does not rescue the row.
                row["llm_confidence"] = _fmt(op.confidence)
                if (
                    op.same_product_for_customer
                    and op.confidence >= min_conf
                    and row["item_id_B"]
                ):
                    # Flip in place to accepted under the LLM rescue.
                    row["decision"] = "accepted"
                    row["reason"] = f"llm_rescue_{row['reason']}"
                    row["source"] = SOURCE_LLM_RESCUED
                    result.matches.append(
                        (row["item_id_A"], row["item_id_B"])
                    )
                    result.accepted_count += 1
                    result.below_threshold_count -= 1
                    result.llm_rescues += 1

    write_matches(config.matches_out, result.matches)
    write_matches_audit(config.audit_out, result.audit_rows)

    return result
