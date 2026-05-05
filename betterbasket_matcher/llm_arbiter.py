"""Optional GPT-5 nano arbiter for the BetterBasket matching pipeline (Phase 10C).

The arbiter is **disabled by default**. The deterministic pipeline ships
the same `matches.csv` it always has unless the user passes
`--use-llm-arbiter` to `scripts/run_pipeline.py`. The arbiter:

- Sees only pairs that already passed `evaluate_hard_rules` (i.e. rows
  the pipeline labeled `below_threshold`). It cannot rescue
  `rejected_by_rule` rows.
- Operates in two passes inside one pipeline run:
    Pass 1 (`collect`): the pipeline's `below_threshold` branch
    records eligible gray-zone candidates onto the arbiter and writes
    the existing audit row.
    Pass 2 (`commit`): after the loop, candidates are sorted by
    `(deterministic_score DESC, item_id_a ASC)`, a per-group cap is
    applied (`max(50, max_calls // n_groups)`), and the surviving
    list is walked. Cache hits are free. Cache misses call the API
    only while `api_calls_made < max_calls`; remaining misses are
    skipped (later cache hits are still served).
- Fail-closed everywhere: API errors and parse errors return no opinion;
  the deterministic decision stands. Confidence below `min_confidence`
  is recorded but does not rescue.

Mirrors the SDK shape from `/tmp/openai_artifacts/openai_sample.py`:

    from openai import OpenAI
    client = OpenAI(base_url=ENDPOINT, api_key=API_KEY)
    client.chat.completions.create(model=DEPLOYMENT_NAME, messages=[...])

The `api_key` is held on `LLMArbiterConfig.api_key` as a `field(repr=False)`
slot so it never appears in `__repr__`, debug logging, or printf output;
it is read only inside `_call_client`. The cache writer never persists
it. The credential loader is the single ingress point and never logs
its values.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

# Lazy-import openai/yaml inside the helpers that need them so tests can
# run without the network or credentials.

from betterbasket_matcher.normalize import NormalizedProduct, SizeInfo
from betterbasket_matcher.taxonomy import assign_matchable_group


# ---------------------------------------------------------------------------
# Default credential lookup
# ---------------------------------------------------------------------------

DEFAULT_CREDS_PATHS: Tuple[Path, ...] = (
    Path("/tmp/openai_artifacts/openai_creds.yaml"),
    Path("/Users/jirustaroure/Downloads/openai_creds.yaml"),
)


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ArbiterInput:
    item_id_a: str
    item_id_b: str
    a_name: str
    b_name: str
    a_brand_norm: str
    b_brand_norm: str
    a_is_private_label: bool
    b_is_private_label: bool
    a_size: Optional[Tuple[Optional[str], Optional[float], int]]
    b_size: Optional[Tuple[Optional[str], Optional[float], int]]
    a_matchable_group: Optional[str]
    b_matchable_group: Optional[str]
    a_category_0: Optional[str]
    a_category_1: Optional[str]
    b_category_0: Optional[str]
    b_category_1: Optional[str]
    deterministic_score: Optional[float]
    deterministic_margin: Optional[float]
    retrieval_score: Optional[float]
    failure_reason: str


@dataclass(frozen=True)
class ArbiterOpinion:
    same_product_for_customer: bool
    confidence: float
    reason: str
    blocking_issue: Optional[str]
    cache_hit: bool
    raw_response_id: Optional[str]


@dataclass
class LLMArbiterConfig:
    creds_path: Path
    cache_path: Path = Path(".cache/llm_arbiter.jsonl")
    max_calls: int = 1000
    min_confidence: float = 0.60
    deployment_name: str = ""
    endpoint: str = ""
    api_key: str = field(default="", repr=False)
    prompt_version: str = "p10c.v1"
    timeout_seconds: float = 15.0


# ---------------------------------------------------------------------------
# ArbiterInput construction
# ---------------------------------------------------------------------------

def _size_tuple(size: Optional[SizeInfo]) -> Optional[Tuple[Optional[str], Optional[float], int]]:
    if size is None:
        return None
    return (size.unit, size.unit_size, size.pack_count)


def build_arbiter_input(
    a: NormalizedProduct,
    b: NormalizedProduct,
    a_raw: Dict[str, Any],
    b_raw: Dict[str, Any],
    *,
    deterministic_score: Optional[float],
    deterministic_margin: Optional[float],
    retrieval_score: Optional[float],
    failure_reason: str,
) -> ArbiterInput:
    """Build an ArbiterInput from a normalized pair plus raw display rows.

    `matchable_group` is computed via
    `betterbasket_matcher.taxonomy.assign_matchable_group(product)`; it
    is NOT a NormalizedProduct field.
    """
    return ArbiterInput(
        item_id_a=str(a.item_id),
        item_id_b=str(b.item_id),
        a_name=str((a_raw or {}).get("name", "") or ""),
        b_name=str((b_raw or {}).get("name", "") or ""),
        a_brand_norm=a.brand_norm or "",
        b_brand_norm=b.brand_norm or "",
        a_is_private_label=bool(a.is_private_label),
        b_is_private_label=bool(b.is_private_label),
        a_size=_size_tuple(a.size),
        b_size=_size_tuple(b.size),
        a_matchable_group=assign_matchable_group(a),
        b_matchable_group=assign_matchable_group(b),
        a_category_0=a.category_0,
        a_category_1=a.category_1,
        b_category_0=b.category_0,
        b_category_1=b.category_1,
        deterministic_score=deterministic_score,
        deterministic_margin=deterministic_margin,
        retrieval_score=retrieval_score,
        failure_reason=failure_reason,
    )


# ---------------------------------------------------------------------------
# Cache key
# ---------------------------------------------------------------------------

def _round_score(s: Optional[float]) -> str:
    if s is None:
        return ""
    return f"{round(float(s), 4):.4f}"


def cache_key(prompt_version: str, deployment_name: str,
              a: ArbiterInput) -> str:
    """Stable sha256 key for an arbiter call. Never includes api_key."""
    parts = [
        prompt_version,
        deployment_name,
        a.item_id_a,
        a.item_id_b,
        a.a_name,
        a.b_name,
        a.a_brand_norm,
        a.b_brand_norm,
        repr(a.a_size),
        repr(a.b_size),
        a.a_matchable_group or "",
        a.b_matchable_group or "",
        a.failure_reason,
        _round_score(a.deterministic_score),
    ]
    h = hashlib.sha256("|".join(parts).encode("utf-8"))
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Prompt rendering
# ---------------------------------------------------------------------------

_SYSTEM_MESSAGE_P10C_V1 = (
    "You are a strict grocery product matcher. Decide whether two items, "
    "one from store A (Walmart) and one from store B (Wegmans), would be "
    "treated as the same product by a typical shopper. Same brand "
    "(or private-label cross-store equivalent), same form, same flavor, "
    "and equivalent usable size. Pack-count differences within the same "
    "total size are acceptable only if a shopper would treat them as "
    "interchangeable. Respond with strict JSON matching this schema and "
    "nothing else: {\"same_product_for_customer\": <true|false>, "
    "\"confidence\": <number in [0.0, 1.0]>, \"reason\": <short string>, "
    "\"blocking_issue\": <string or null>}. confidence MUST be a number "
    "in the closed interval [0.0, 1.0]; do not exceed 1.0 or go below 0.0."
)


def _fmt_size(t: Optional[Tuple[Optional[str], Optional[float], int]]) -> str:
    if t is None:
        return "unknown"
    unit, us, pack = t
    if unit is None or us is None:
        return f"unknown (pack={pack})"
    return f"{us} {unit} (pack={pack})"


def render_prompt(a: ArbiterInput, *, prompt_version: str) -> Tuple[str, str]:
    """Return (system_message, user_message). Strict structured fields;
    no URLs, no api_key, no free-text from raw audit columns."""
    if prompt_version != "p10c.v1":
        raise ValueError(f"unsupported prompt_version: {prompt_version}")
    system = _SYSTEM_MESSAGE_P10C_V1
    lines = [
        "Compare these two items and respond with strict JSON.",
        "",
        "A (Walmart):",
        f"  name: {a.a_name}",
        f"  brand_norm: {a.a_brand_norm}",
        f"  is_private_label: {a.a_is_private_label}",
        f"  size: {_fmt_size(a.a_size)}",
        f"  matchable_group: {a.a_matchable_group or 'unknown'}",
        f"  category_0: {a.a_category_0 or 'unknown'}",
        f"  category_1: {a.a_category_1 or 'unknown'}",
        "",
        "B (Wegmans):",
        f"  name: {a.b_name}",
        f"  brand_norm: {a.b_brand_norm}",
        f"  is_private_label: {a.b_is_private_label}",
        f"  size: {_fmt_size(a.b_size)}",
        f"  matchable_group: {a.b_matchable_group or 'unknown'}",
        f"  category_0: {a.b_category_0 or 'unknown'}",
        f"  category_1: {a.b_category_1 or 'unknown'}",
        "",
        "Deterministic context:",
        f"  failure_reason: {a.failure_reason}",
        f"  deterministic_score: {_round_score(a.deterministic_score)}",
        f"  deterministic_margin: {_round_score(a.deterministic_margin)}",
        "",
        "Respond with strict JSON only (no fences, no prose).",
    ]
    return system, "\n".join(lines)


# ---------------------------------------------------------------------------
# JSON parsing
# ---------------------------------------------------------------------------

_REQUIRED_FIELDS = {"same_product_for_customer", "confidence",
                    "reason", "blocking_issue"}


def _strip_code_fences(text: str) -> str:
    s = text.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[-1] if "\n" in s else s[3:]
        if s.endswith("```"):
            s = s[: -3]
        s = s.strip()
        # Strip leading "json" language tag if it survived the split
        if s.lower().startswith("json\n"):
            s = s[5:].strip()
    return s


def parse_llm_response(text: str) -> Optional[Dict[str, Any]]:
    """Parse a strict-JSON arbiter response.

    Returns the parsed dict on success, or None for any of:
      - JSON decode failure (incl. NaN/Infinity)
      - missing required field
      - confidence not numeric, NaN, or out of [0.0, 1.0]
      - same_product_for_customer not a bool

    Confidence is **not** clamped; out-of-range values fail parsing.
    """
    if not isinstance(text, str):
        return None
    candidate = _strip_code_fences(text)
    try:
        data = json.loads(candidate, parse_constant=lambda x: None)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    if not _REQUIRED_FIELDS.issubset(data.keys()):
        return None
    same = data.get("same_product_for_customer")
    if not isinstance(same, bool):
        return None
    conf = data.get("confidence")
    if isinstance(conf, bool) or not isinstance(conf, (int, float)):
        return None
    try:
        f = float(conf)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    if f < 0.0 or f > 1.0:
        return None
    reason = data.get("reason")
    if not isinstance(reason, str):
        return None
    bi = data.get("blocking_issue")
    if bi is not None and not isinstance(bi, str):
        return None
    return {
        "same_product_for_customer": same,
        "confidence": f,
        "reason": reason,
        "blocking_issue": bi,
    }


# ---------------------------------------------------------------------------
# Credential loading
# ---------------------------------------------------------------------------

def load_credentials(path: Path) -> Dict[str, str]:
    """Load openai.endpoint / openai.api_key / openai.deployment_name.

    Raises FileNotFoundError if path is missing; ValueError if any of
    the three required keys is missing. Error messages name the missing
    key but never echo any *value* read from the YAML.
    """
    import yaml  # lazy

    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"credentials file not found: {p}")
    with p.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(
            f"credentials file does not contain a YAML mapping: {p.name}"
        )
    section = data.get("openai")
    if not isinstance(section, dict):
        raise ValueError(
            f"credentials file missing top-level 'openai' mapping: {p.name}"
        )
    out: Dict[str, str] = {}
    for key in ("endpoint", "api_key", "deployment_name"):
        v = section.get(key)
        if not isinstance(v, str) or not v.strip():
            raise ValueError(
                f"credentials missing or empty key 'openai.{key}' in {p.name}"
            )
        out[key] = v.strip()
    return out


def _resolve_creds_path(
    *,
    cli_arg: Optional[str],
    env: Dict[str, str],
    defaults: Iterable[Path] = DEFAULT_CREDS_PATHS,
) -> Path:
    """Resolve the credentials file path.

    Order: cli_arg -> $BB_OPENAI_CREDS -> first existing file in
    `defaults`. Raises FileNotFoundError if none found.
    """
    if cli_arg:
        p = Path(cli_arg)
        if not p.exists():
            raise FileNotFoundError(f"--llm-creds path not found: {p}")
        return p
    env_val = env.get("BB_OPENAI_CREDS") if env else None
    if env_val:
        p = Path(env_val)
        if not p.exists():
            raise FileNotFoundError(
                f"BB_OPENAI_CREDS path not found: {p}"
            )
        return p
    for d in defaults:
        dp = Path(d)
        if dp.exists():
            return dp
    raise FileNotFoundError(
        "No credentials file found (checked --llm-creds, "
        "$BB_OPENAI_CREDS, and default search paths)"
    )


# ---------------------------------------------------------------------------
# Cache I/O
# ---------------------------------------------------------------------------

def _opinion_to_jsonable(op: ArbiterOpinion) -> Dict[str, Any]:
    return {
        "same_product_for_customer": op.same_product_for_customer,
        "confidence": op.confidence,
        "reason": op.reason,
        "blocking_issue": op.blocking_issue,
        "raw_response_id": op.raw_response_id,
    }


def _opinion_from_jsonable(d: Dict[str, Any]) -> ArbiterOpinion:
    return ArbiterOpinion(
        same_product_for_customer=bool(d["same_product_for_customer"]),
        confidence=float(d["confidence"]),
        reason=str(d.get("reason") or ""),
        blocking_issue=d.get("blocking_issue"),
        cache_hit=True,
        raw_response_id=d.get("raw_response_id"),
    )


def _scan_cache(cache_path: Path) -> Dict[str, ArbiterOpinion]:
    out: Dict[str, ArbiterOpinion] = {}
    if not cache_path.exists():
        return out
    with cache_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            key = rec.get("key")
            op = rec.get("opinion")
            if not key or not isinstance(op, dict):
                continue
            if op.get("parse_error"):
                # Parse-error sentinels are valid cache lines but
                # produce no usable opinion. Skip.
                continue
            try:
                out[key] = _opinion_from_jsonable(op)
            except (KeyError, TypeError, ValueError):
                continue
    return out


def cache_lookup(cache_path: Path, key: str) -> Optional[ArbiterOpinion]:
    return _scan_cache(cache_path).get(key)


def cache_append(
    cache_path: Path,
    key: str,
    opinion: ArbiterOpinion,
    input_meta: Dict[str, str],
) -> None:
    """Append one cache line. Never persists api_key.

    `input_meta` is a small bookkeeping dict (item_id_a, item_id_b,
    deployment_name, prompt_version). Anything keyed `api_key` /
    `Authorization` is dropped defensively.
    """
    cache_path = Path(cache_path)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    safe_meta = {
        k: str(v) for k, v in (input_meta or {}).items()
        if k.lower() not in ("api_key", "authorization", "bearer")
    }
    safe_meta["ts"] = datetime.now(timezone.utc).isoformat()
    rec = {
        "key": key,
        "opinion": _opinion_to_jsonable(opinion),
        "meta": safe_meta,
    }
    with cache_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _cache_append_parse_error(
    cache_path: Path, key: str, input_meta: Dict[str, str],
) -> None:
    cache_path = Path(cache_path)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    safe_meta = {
        k: str(v) for k, v in (input_meta or {}).items()
        if k.lower() not in ("api_key", "authorization", "bearer")
    }
    safe_meta["ts"] = datetime.now(timezone.utc).isoformat()
    rec = {
        "key": key,
        "opinion": {"parse_error": True},
        "meta": safe_meta,
    }
    with cache_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# LLMArbiter (collect / commit)
# ---------------------------------------------------------------------------

@dataclass
class _Candidate:
    arbiter_input: ArbiterInput
    deterministic_score: float
    matchable_group: str   # "unknown" if None
    cache_key_hex: str


class LLMArbiter:
    """Two-pass arbiter. `collect` is called from inside the pipeline
    loop; `commit` is called once after the loop and returns
    `{item_id_a: ArbiterOpinion}`.
    """

    def __init__(self, config: LLMArbiterConfig, client=None):
        self._cfg = config
        self._client = client
        self._candidates: List[_Candidate] = []
        self._api_calls_made = 0
        self._cache_hits = 0
        # Stat counters exposed to the pipeline for stdout reporting
        self._opinions_returned = 0

    @classmethod
    def from_cli(cls, args, *, env: Optional[Dict[str, str]] = None,
                 defaults: Iterable[Path] = DEFAULT_CREDS_PATHS) -> "LLMArbiter":
        env = os.environ if env is None else env
        creds_path = _resolve_creds_path(
            cli_arg=getattr(args, "llm_creds", None),
            env=env,
            defaults=defaults,
        )
        creds = load_credentials(creds_path)
        cfg = LLMArbiterConfig(
            creds_path=creds_path,
            cache_path=Path(getattr(args, "llm_cache",
                                    ".cache/llm_arbiter.jsonl")),
            max_calls=int(getattr(args, "llm_max_calls", 1000)),
            min_confidence=float(getattr(args, "llm_min_confidence", 0.60)),
            deployment_name=creds["deployment_name"],
            endpoint=creds["endpoint"],
            api_key=creds["api_key"],
        )
        from openai import OpenAI  # lazy
        client = OpenAI(base_url=cfg.endpoint, api_key=cfg.api_key)
        return cls(cfg, client=client)

    @property
    def calls_remaining(self) -> int:
        return max(0, self._cfg.max_calls - self._api_calls_made)

    @property
    def min_confidence(self) -> float:
        return self._cfg.min_confidence

    @property
    def calls_made(self) -> int:
        return self._api_calls_made

    @property
    def cache_hits(self) -> int:
        return self._cache_hits

    def collect(
        self,
        *,
        a: NormalizedProduct,
        b: NormalizedProduct,
        a_raw: Dict[str, Any],
        b_raw: Dict[str, Any],
        deterministic_score: Optional[float],
        deterministic_margin: Optional[float],
        retrieval_score: Optional[float],
        failure_reason: str,
    ) -> None:
        """Pass-1: record a gray-zone candidate. No API call.

        Eligibility (the [0.65, 0.75) score window) is enforced by the
        caller in `betterbasket_matcher.pipeline`; this method records
        whatever the caller hands it. A None score is silently skipped
        because the cache key would be unstable.
        """
        if deterministic_score is None:
            return
        s = float(deterministic_score)
        arb_in = build_arbiter_input(
            a, b, a_raw, b_raw,
            deterministic_score=s,
            deterministic_margin=deterministic_margin,
            retrieval_score=retrieval_score,
            failure_reason=failure_reason,
        )
        ck = cache_key(self._cfg.prompt_version,
                       self._cfg.deployment_name, arb_in)
        group = arb_in.a_matchable_group or "unknown"
        self._candidates.append(_Candidate(
            arbiter_input=arb_in,
            deterministic_score=s,
            matchable_group=group,
            cache_key_hex=ck,
        ))

    def commit(self) -> Dict[str, ArbiterOpinion]:
        """Pass-2: sort, per-group cap, walk in score-DESC order,
        cache hits free, API calls bounded. Returns
        `{item_id_a: ArbiterOpinion}`."""
        if not self._candidates:
            return {}

        cache = _scan_cache(self._cfg.cache_path)

        sorted_cands = sorted(
            self._candidates,
            key=lambda c: (-c.deterministic_score, c.arbiter_input.item_id_a),
        )
        n_groups = len({c.matchable_group for c in sorted_cands}) or 1
        cap = max(50, self._cfg.max_calls // n_groups)

        per_group: Dict[str, int] = defaultdict(int)
        arbitration_order: List[_Candidate] = []
        for c in sorted_cands:
            if per_group[c.matchable_group] >= cap:
                continue
            per_group[c.matchable_group] += 1
            arbitration_order.append(c)

        out: Dict[str, ArbiterOpinion] = {}
        for c in arbitration_order:
            cached = cache.get(c.cache_key_hex)
            if cached is not None:
                out[c.arbiter_input.item_id_a] = cached
                self._cache_hits += 1
                continue
            if self._api_calls_made >= self._cfg.max_calls:
                continue
            opinion = self._call_client(c)
            if opinion is not None:
                out[c.arbiter_input.item_id_a] = opinion
        return out

    # -----------------------------------------------------------------
    # Internal: client call + caching
    # -----------------------------------------------------------------

    def _call_client(self, c: _Candidate) -> Optional[ArbiterOpinion]:
        """Single API call. Returns None on any failure or parse error.

        - Counts as one API call regardless of outcome (so a misbehaving
          deployment cannot exhaust budget retries).
        - On parse error, writes a `parse_error` sentinel to the cache.
        - On API exception, the WARNING goes to stderr (no api_key,
          prompt body, or response body) and the call is **not** cached.
        """
        if self._client is None:
            return None
        system, user = render_prompt(
            c.arbiter_input, prompt_version=self._cfg.prompt_version,
        )
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        self._api_calls_made += 1
        text: Optional[str] = None
        response_id: Optional[str] = None
        try:
            try:
                resp = self._client.chat.completions.create(
                    model=self._cfg.deployment_name,
                    messages=messages,
                    response_format={"type": "json_object"},
                    timeout=self._cfg.timeout_seconds,
                )
            except TypeError:
                # response_format not supported by this deployment; retry
                resp = self._client.chat.completions.create(
                    model=self._cfg.deployment_name,
                    messages=messages,
                    timeout=self._cfg.timeout_seconds,
                )
            text = resp.choices[0].message.content
            response_id = getattr(resp, "id", None)
        except Exception as exc:
            sys.stderr.write(
                f"[llm_arbiter] API failure ({type(exc).__name__}) "
                f"on item_id_a={c.arbiter_input.item_id_a}; "
                f"deterministic decision retained.\n"
            )
            return None

        parsed = parse_llm_response(text or "")
        meta = {
            "item_id_a": c.arbiter_input.item_id_a,
            "item_id_b": c.arbiter_input.item_id_b,
            "deployment_name": self._cfg.deployment_name,
            "prompt_version": self._cfg.prompt_version,
        }
        if parsed is None:
            _cache_append_parse_error(
                self._cfg.cache_path, c.cache_key_hex, meta,
            )
            return None

        opinion = ArbiterOpinion(
            same_product_for_customer=parsed["same_product_for_customer"],
            confidence=parsed["confidence"],
            reason=parsed["reason"],
            blocking_issue=parsed["blocking_issue"],
            cache_hit=False,
            raw_response_id=response_id,
        )
        cache_append(self._cfg.cache_path, c.cache_key_hex, opinion, meta)
        return opinion
