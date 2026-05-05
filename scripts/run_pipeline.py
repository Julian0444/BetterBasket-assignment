"""CLI for the BetterBasket matcher.

Wires the deterministic pipeline against any pair of A/B CSVs and writes
``matches.csv`` and ``matches_audit.csv``. After writing, validates the
matches file against the supplied A/B id sets and required-pair gate.

The ``--allow-under-min-rows`` flag exists to make small fixture/dev runs
reportable without lowering the production min_rows floor.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Dict, Set

# Make ``scripts/run_pipeline.py`` runnable directly from the project root
# without requiring ``pip install -e .``.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from betterbasket_matcher.io import read_products  # noqa: E402
from betterbasket_matcher.output import validate_matches_csv  # noqa: E402
from betterbasket_matcher.pipeline import PipelineConfig, run_pipeline  # noqa: E402


# Required-pair gate: PDF regressions baked into the assignment.
DEFAULT_REQUIRED_PAIRS: Dict[str, str] = {
    "2197626": "92544",
    "1929544": "105624",
}


def _parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Run the BetterBasket deterministic matching pipeline and emit "
            "matches.csv + matches_audit.csv."
        )
    )
    p.add_argument("--a-csv", required=True, help="Path to Store A CSV")
    p.add_argument("--b-csv", required=True, help="Path to Store B CSV")
    p.add_argument("--matches-out", default="matches.csv")
    p.add_argument("--audit-out", default="matches_audit.csv")
    p.add_argument("--min-score", type=float, default=0.55)
    p.add_argument("--min-margin", type=float, default=0.05)
    p.add_argument("--top-k", type=int, default=50)
    p.add_argument("--min-rows", type=int, default=4000)
    p.add_argument(
        "--allow-under-min-rows",
        action="store_true",
        help="Relax min_rows validation (fixture/dev runs only).",
    )
    p.add_argument(
        "--limit", type=int, default=None,
        help="Process only the first N valid A rows (post-quarantine).",
    )
    # Phase 10C: optional GPT-5 nano arbiter. Disabled by default; the
    # deterministic baseline ships unchanged unless --use-llm-arbiter is
    # passed. No credential loading happens when the flag is absent.
    p.add_argument(
        "--use-llm-arbiter", action="store_true",
        help="Enable optional GPT-5 nano gray-zone rescue arbiter.",
    )
    p.add_argument(
        "--llm-creds", default=None,
        help="Path to OpenAI credentials YAML (default: --llm-creds, "
             "BB_OPENAI_CREDS env, /tmp/openai_artifacts/openai_creds.yaml, "
             "~/Downloads/openai_creds.yaml).",
    )
    p.add_argument(
        "--llm-cache", default=".cache/llm_arbiter.jsonl",
        help="JSON-lines cache path for arbiter opinions.",
    )
    p.add_argument(
        "--llm-max-calls", type=int, default=1000,
        help="Max API calls per run (cache hits do not consume budget).",
    )
    p.add_argument(
        "--llm-min-confidence", type=float, default=0.60,
        help="Minimum LLM confidence required to flip a below_threshold "
             "row to accepted.",
    )
    return p.parse_args(argv)


def _id_sets(a_csv: str, b_csv: str) -> tuple:
    valid_a, _ = read_products(a_csv, "A")
    valid_b, _ = read_products(b_csv, "B")
    a_ids: Set[str] = {r["item_id"] for r in valid_a}
    b_ids: Set[str] = {r["item_id"] for r in valid_b}
    return a_ids, b_ids


def main(argv=None) -> int:
    args = _parse_args(argv)

    print(
        "[BetterBasket pipeline] running deterministic matcher.",
        flush=True,
    )

    arbiter = None
    if args.use_llm_arbiter:
        # Lazy import so the deterministic path never imports openai/yaml.
        from betterbasket_matcher.llm_arbiter import LLMArbiter
        arbiter = LLMArbiter.from_cli(args)
        print(
            "[BetterBasket pipeline] LLM arbiter enabled "
            f"(deployment={arbiter._cfg.deployment_name}, "
            f"max_calls={arbiter._cfg.max_calls}, "
            f"min_confidence={arbiter._cfg.min_confidence}, "
            f"cache={arbiter._cfg.cache_path}).",
            flush=True,
        )

    cfg = PipelineConfig(
        a_csv=args.a_csv,
        b_csv=args.b_csv,
        matches_out=args.matches_out,
        audit_out=args.audit_out,
        min_score=args.min_score,
        min_margin=args.min_margin,
        top_k=args.top_k,
        limit=args.limit,
        arbiter=arbiter,
    )
    result = run_pipeline(cfg)

    print(
        f"valid_a={result.valid_a_count} quarantined_a={result.quarantined_a_count} "
        f"valid_b={result.valid_b_count} in_scope_a={result.in_scope_a_count}\n"
        f"accepted={result.accepted_count} rejected_by_rule={result.rejected_by_rule_count} "
        f"below_threshold={result.below_threshold_count} no_candidates={result.no_candidates_count}\n"
        f"matches_out={args.matches_out} audit_out={args.audit_out}",
        flush=True,
    )
    if args.use_llm_arbiter:
        print(
            f"llm_arbiter: calls={result.llm_calls} "
            f"rescues={result.llm_rescues} cache_hits={result.llm_cache_hits}",
            flush=True,
        )

    a_ids, b_ids = _id_sets(args.a_csv, args.b_csv)
    enforced_min = 0 if args.allow_under_min_rows else args.min_rows
    validation = validate_matches_csv(
        args.matches_out,
        valid_ids_a=a_ids,
        valid_ids_b=b_ids,
        min_rows=enforced_min,
        required_pairs=DEFAULT_REQUIRED_PAIRS,
    )
    print(
        f"validation: ok={validation.ok} row_count={validation.row_count} "
        f"errors={len(validation.errors)}",
        flush=True,
    )
    if not validation.ok:
        for err in validation.errors:
            print(f"  ERROR: {err}", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
