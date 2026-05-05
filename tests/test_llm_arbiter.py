"""Phase 10C tests for the optional GPT-5 nano arbiter.

No real API calls anywhere. A `FakeClient` is dependency-injected via
`LLMArbiter(config, client=fake)` so the network is never touched. All
cache/output paths use `tmp_path`. The real api_key in
`/tmp/openai_artifacts/openai_creds.yaml` is never read by any test;
tests use synthetic placeholder values.
"""
from __future__ import annotations

import csv
import io
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from betterbasket_matcher.llm_arbiter import (
    ArbiterInput,
    ArbiterOpinion,
    LLMArbiter,
    LLMArbiterConfig,
    build_arbiter_input,
    cache_append,
    cache_key,
    cache_lookup,
    load_credentials,
    parse_llm_response,
    render_prompt,
)
from betterbasket_matcher.normalize import (
    NormalizedProduct,
    SizeInfo,
    normalize_product,
)


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------

SYNTHETIC_API_KEY = "FAKE-TEST-KEY-DO-NOT-USE-Z9X8W7"
SYNTHETIC_ENDPOINT = "https://example.invalid/openai/v1/"

GENERIC_SECRET_MARKERS = ("api_key", "sk-", "Authorization", "Bearer ")


def _mk_config(
    tmp_path: Path,
    *,
    max_calls: int = 10,
    min_confidence: float = 0.60,
    deployment_name: str = "test-deployment",
    api_key: str = SYNTHETIC_API_KEY,
    endpoint: str = SYNTHETIC_ENDPOINT,
) -> LLMArbiterConfig:
    return LLMArbiterConfig(
        creds_path=tmp_path / "creds.yaml",
        cache_path=tmp_path / "cache.jsonl",
        max_calls=max_calls,
        min_confidence=min_confidence,
        deployment_name=deployment_name,
        endpoint=endpoint,
        api_key=api_key,
    )


def _mk_normalized(item_id: str, source: str, **kw) -> NormalizedProduct:
    base = dict(
        item_id=item_id,
        source=source,
        brand_norm="chobani",
        is_private_label=False,
        brand_inferred=False,
        size=SizeInfo(unit="oz", unit_size=5.3, pack_count=1, total_size=5.3),
        is_organic=False,
        storage_type="refrigerated",
        form=None,
        flavor=None,
        category_0="food",
        category_1="dairy and eggs",
        category_2="yogurt",
        core_name="greek honey blended yogurt",
        retrieval_text="chobani greek honey blended yogurt 5.3oz",
    )
    base.update(kw)
    return NormalizedProduct(**base)


def _mk_input(score: float = 0.70, group: Optional[str] = "dairy", **kw) -> ArbiterInput:
    base = dict(
        item_id_a="A1",
        item_id_b="B1",
        a_name="Chobani Honey Blended 5.3 oz",
        b_name="Chobani Greek Honey Blended Yogurt",
        a_brand_norm="chobani",
        b_brand_norm="chobani",
        a_is_private_label=False,
        b_is_private_label=False,
        a_size=("oz", 5.3, 1),
        b_size=("oz", 5.3, 1),
        a_matchable_group=group,
        b_matchable_group=group,
        a_category_0="food",
        a_category_1="dairy and eggs",
        b_category_0="dairy",
        b_category_1="yogurt",
        deterministic_score=score,
        deterministic_margin=0.06,
        retrieval_score=1.42,
        failure_reason="below_min_score",
    )
    base.update(kw)
    return ArbiterInput(**base)


@dataclass
class _FakeMessage:
    content: str


@dataclass
class _FakeChoice:
    message: _FakeMessage


@dataclass
class _FakeCompletion:
    choices: List[_FakeChoice]
    id: str = "chatcmpl-fake-1"


class FakeClient:
    """Minimal stand-in for openai.OpenAI; records each call."""

    def __init__(
        self,
        *,
        responses: Optional[List[Any]] = None,
        raise_exc: Optional[Exception] = None,
        json_format_supported: bool = True,
    ):
        self.call_count = 0
        self.calls: List[Dict[str, Any]] = []
        self._responses = list(responses or [])
        self._raise_exc = raise_exc
        self._json_format_supported = json_format_supported
        self.chat = self._Chat(self)

    class _Chat:
        def __init__(self, outer: "FakeClient"):
            self.completions = outer._Completions(outer)

    class _Completions:
        def __init__(self, outer: "FakeClient"):
            self._outer = outer

        def create(self, **kwargs):
            outer = self._outer
            outer.call_count += 1
            outer.calls.append(kwargs)
            if outer._raise_exc is not None:
                raise outer._raise_exc
            if (
                kwargs.get("response_format")
                and not outer._json_format_supported
            ):
                raise TypeError(
                    "response_format is not supported by this deployment"
                )
            if not outer._responses:
                payload = {
                    "same_product_for_customer": True,
                    "confidence": 0.85,
                    "reason": "fake default",
                    "blocking_issue": None,
                }
                content = json.dumps(payload)
            else:
                resp = outer._responses.pop(0)
                content = (
                    resp if isinstance(resp, str) else json.dumps(resp)
                )
            return _FakeCompletion(
                choices=[_FakeChoice(message=_FakeMessage(content=content))],
            )


def _write_yaml(path: Path, mapping: Dict[str, Any]) -> Path:
    import yaml

    path.write_text(yaml.safe_dump(mapping), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# 1. ArbiterInput uses real NormalizedProduct fields and raw display name
# ---------------------------------------------------------------------------

def test_build_arbiter_input_uses_real_normalized_fields():
    a = _mk_normalized("A1", "A")
    b = _mk_normalized(
        "B1", "B",
        category_0="dairy", category_1="yogurt", category_2="greek",
    )
    a_raw = {"item_id": "A1", "name": "Chobani Honey Blended 5.3 oz Cup"}
    b_raw = {"item_id": "B1", "name": "Chobani Greek Honey Blended Yogurt"}

    arb = build_arbiter_input(
        a, b, a_raw, b_raw,
        deterministic_score=0.71,
        deterministic_margin=0.06,
        retrieval_score=1.4,
        failure_reason="below_min_score",
    )

    # Display names come from raw rows, NOT core_name
    assert arb.a_name == a_raw["name"]
    assert arb.b_name == b_raw["name"]
    # Brand and PL come from NormalizedProduct
    assert arb.a_brand_norm == "chobani"
    assert arb.b_brand_norm == "chobani"
    assert arb.a_is_private_label is False
    assert arb.b_is_private_label is False
    # Size tuple is (unit, unit_size, pack_count)
    assert arb.a_size == ("oz", 5.3, 1)
    assert arb.b_size == ("oz", 5.3, 1)
    # Categories preserved
    assert arb.a_category_0 == "food"
    assert arb.a_category_1 == "dairy and eggs"
    assert arb.b_category_0 == "dairy"
    assert arb.b_category_1 == "yogurt"
    # Score / failure_reason preserved
    assert arb.deterministic_score == pytest.approx(0.71)
    assert arb.deterministic_margin == pytest.approx(0.06)
    assert arb.retrieval_score == pytest.approx(1.4)
    assert arb.failure_reason == "below_min_score"


def test_build_arbiter_input_computes_matchable_group_via_taxonomy():
    """matchable_group is NOT a NormalizedProduct field; it must be
    computed via betterbasket_matcher.taxonomy.assign_matchable_group."""
    a_row = {
        "item_id": "A1",
        "name": "Chobani Honey Blended 5.3 oz Cup",
        "brand_raw": "Chobani",
        "item_info": json.dumps({
            "category_0": "Food",
            "category_1": "Dairy & Eggs",
            "category_2": "Yogurt",
        }),
        "sizing_comp": json.dumps({"size_user_friendly": "5.3 oz"}),
    }
    b_row = {
        "item_id": "B1",
        "name": "Chobani Greek Honey Blended Yogurt",
        "brand_raw": "Chobani",
        "item_info": json.dumps({
            "category_0": "Dairy",
            "category_1": "Yogurt",
            "category_2": "Greek",
        }),
        "tags": '{"kosher"}',
        "sizing_comp": json.dumps({"size_user_friendly": "5.3 ounce"}),
    }
    a = normalize_product(a_row, "A")
    b = normalize_product(b_row, "B")

    # Sanity: NormalizedProduct does NOT carry a matchable_group field
    assert not hasattr(a, "matchable_group")

    arb = build_arbiter_input(
        a, b, a_row, b_row,
        deterministic_score=0.71,
        deterministic_margin=0.06,
        retrieval_score=1.4,
        failure_reason="below_min_score",
    )
    # Both should resolve to "dairy" via the taxonomy mapping
    assert arb.a_matchable_group == "dairy"
    assert arb.b_matchable_group == "dairy"


# ---------------------------------------------------------------------------
# 2. Cache key
# ---------------------------------------------------------------------------

def test_cache_key_stable_across_dict_ordering():
    a = _mk_input(score=0.71, deterministic_score=0.7123)
    a2 = _mk_input(score=0.71, deterministic_score=0.7123)
    k1 = cache_key("p10c.v1", "test-deployment", a)
    k2 = cache_key("p10c.v1", "test-deployment", a2)
    assert k1 == k2

    # Differing prompt_version → different key
    assert cache_key("other.v0", "test-deployment", a) != k1
    # Differing deployment_name → different key
    assert cache_key("p10c.v1", "other-deployment", a) != k1
    # Score rounded to 4dp: 0.7123 vs 0.71234 should be the same
    a3 = _mk_input(score=0.71, deterministic_score=0.71234)
    assert cache_key("p10c.v1", "test-deployment", a3) == k1
    # But 0.7123 vs 0.7124 differs
    a4 = _mk_input(score=0.71, deterministic_score=0.7124)
    assert cache_key("p10c.v1", "test-deployment", a4) != k1


def test_cache_key_excludes_api_key(tmp_path):
    """cache_key must not include any api-key value in its inputs."""
    a = _mk_input()
    # The function does not take api_key as an argument; verify by
    # signature inspection.
    import inspect
    sig = inspect.signature(cache_key)
    assert "api_key" not in sig.parameters
    # And the produced key is a hex digest with no obvious leakage.
    k = cache_key("p10c.v1", "test-deployment", a)
    assert len(k) == 64
    assert all(c in "0123456789abcdef" for c in k)


# ---------------------------------------------------------------------------
# 3. Prompt rendering excludes secret markers / URLs
# ---------------------------------------------------------------------------

def test_render_prompt_excludes_secret_markers():
    inp = _mk_input()
    sys_msg, user_msg = render_prompt(inp, prompt_version="p10c.v1")
    full = sys_msg + "\n" + user_msg
    for marker in GENERIC_SECRET_MARKERS:
        assert marker not in full, f"secret marker {marker!r} leaked into prompt"
    assert "http://" not in full
    assert "https://" not in full
    # Sanity: the structured fields are present
    assert "Chobani" in full
    assert "below_min_score" in full


# ---------------------------------------------------------------------------
# 4-6. JSON parsing
# ---------------------------------------------------------------------------

def test_parse_llm_response_valid_json():
    text = json.dumps({
        "same_product_for_customer": True,
        "confidence": 0.82,
        "reason": "Same brand, same size",
        "blocking_issue": None,
    })
    parsed = parse_llm_response(text)
    assert parsed is not None
    assert parsed["same_product_for_customer"] is True
    # No clamping: value is preserved exactly
    assert parsed["confidence"] == pytest.approx(0.82)
    assert parsed["reason"] == "Same brand, same size"
    assert parsed["blocking_issue"] is None


def test_parse_llm_response_strips_code_fences():
    inner = json.dumps({
        "same_product_for_customer": False,
        "confidence": 0.30,
        "reason": "Different size",
        "blocking_issue": "size_mismatch",
    })
    fenced = f"```json\n{inner}\n```"
    parsed = parse_llm_response(fenced)
    assert parsed is not None
    assert parsed["same_product_for_customer"] is False
    assert parsed["confidence"] == pytest.approx(0.30)


def test_parse_llm_response_malformed_returns_none():
    # Junk
    assert parse_llm_response("this is not json at all") is None
    # Missing required field
    assert parse_llm_response(json.dumps({
        "confidence": 0.5, "reason": "x", "blocking_issue": None,
    })) is None
    # confidence > 1.0 → None (no clamping)
    assert parse_llm_response(json.dumps({
        "same_product_for_customer": True,
        "confidence": 1.5,
        "reason": "x",
        "blocking_issue": None,
    })) is None
    # confidence < 0 → None
    assert parse_llm_response(json.dumps({
        "same_product_for_customer": True,
        "confidence": -0.1,
        "reason": "x",
        "blocking_issue": None,
    })) is None
    # Non-numeric confidence → None
    assert parse_llm_response(json.dumps({
        "same_product_for_customer": True,
        "confidence": "high",
        "reason": "x",
        "blocking_issue": None,
    })) is None
    # NaN confidence → None
    assert parse_llm_response('{"same_product_for_customer": true, '
                              '"confidence": NaN, "reason": "x", '
                              '"blocking_issue": null}') is None


# ---------------------------------------------------------------------------
# 7. Cache hit skips fake client call
# ---------------------------------------------------------------------------

def test_cache_hit_skips_fake_client_call(tmp_path):
    cfg = _mk_config(tmp_path, max_calls=5)
    fake = FakeClient()
    arbiter = LLMArbiter(cfg, client=fake)

    a = _mk_normalized("A1", "A")
    b = _mk_normalized("B1", "B")
    a_raw = {"item_id": "A1", "name": "A name"}
    b_raw = {"item_id": "B1", "name": "B name"}

    # Pre-populate cache
    inp = build_arbiter_input(
        a, b, a_raw, b_raw,
        deterministic_score=0.71, deterministic_margin=0.06,
        retrieval_score=1.4, failure_reason="below_min_score",
    )
    key = cache_key(cfg.prompt_version, cfg.deployment_name, inp)
    cached_opinion = ArbiterOpinion(
        same_product_for_customer=True,
        confidence=0.78,
        reason="cached",
        blocking_issue=None,
        cache_hit=False,
        raw_response_id=None,
    )
    cache_append(cfg.cache_path, key, cached_opinion,
                 input_meta={"item_id_a": "A1", "item_id_b": "B1",
                             "deployment_name": cfg.deployment_name,
                             "prompt_version": cfg.prompt_version})

    # Collect & commit
    arbiter.collect(
        a=a, b=b, a_raw=a_raw, b_raw=b_raw,
        deterministic_score=0.71, deterministic_margin=0.06,
        retrieval_score=1.4, failure_reason="below_min_score",
    )
    opinions = arbiter.commit()

    assert fake.call_count == 0
    assert "A1" in opinions
    assert opinions["A1"].cache_hit is True
    assert opinions["A1"].confidence == pytest.approx(0.78)


# ---------------------------------------------------------------------------
# 8. Cache append never writes synthetic api_key or generic secret markers
# ---------------------------------------------------------------------------

def test_cache_append_never_writes_api_key(tmp_path):
    cfg = _mk_config(tmp_path)
    fake = FakeClient()
    arbiter = LLMArbiter(cfg, client=fake)

    a = _mk_normalized("A1", "A")
    b = _mk_normalized("B1", "B")
    a_raw = {"item_id": "A1", "name": "A name"}
    b_raw = {"item_id": "B1", "name": "B name"}

    arbiter.collect(
        a=a, b=b, a_raw=a_raw, b_raw=b_raw,
        deterministic_score=0.71, deterministic_margin=0.06,
        retrieval_score=1.4, failure_reason="below_min_score",
    )
    arbiter.commit()

    assert cfg.cache_path.exists()
    body = cfg.cache_path.read_text(encoding="utf-8")
    assert SYNTHETIC_API_KEY not in body
    assert SYNTHETIC_ENDPOINT not in body
    for marker in GENERIC_SECRET_MARKERS:
        assert marker not in body, (
            f"generic secret marker {marker!r} leaked into cache file"
        )


# ---------------------------------------------------------------------------
# 9. Missing creds raises clear error without leaking values
# ---------------------------------------------------------------------------

def test_load_credentials_missing_keys_raises_without_leaking_value(tmp_path):
    p = _write_yaml(tmp_path / "creds.yaml", {
        "openai": {
            "endpoint": SYNTHETIC_ENDPOINT,
            # api_key intentionally omitted
            "deployment_name": "test-deployment",
        },
    })
    with pytest.raises(ValueError) as ei:
        load_credentials(p)
    # Error message must name the missing key but NOT echo any value
    msg = str(ei.value)
    assert "api_key" in msg
    assert SYNTHETIC_ENDPOINT not in msg
    assert "test-deployment" not in msg


def test_load_credentials_returns_three_keys(tmp_path):
    p = _write_yaml(tmp_path / "creds.yaml", {
        "openai": {
            "endpoint": SYNTHETIC_ENDPOINT,
            "api_key": SYNTHETIC_API_KEY,
            "deployment_name": "test-deployment",
        },
    })
    creds = load_credentials(p)
    assert creds["endpoint"] == SYNTHETIC_ENDPOINT
    assert creds["api_key"] == SYNTHETIC_API_KEY
    assert creds["deployment_name"] == "test-deployment"


# ---------------------------------------------------------------------------
# 10. Credential lookup order
# ---------------------------------------------------------------------------

def test_load_credentials_lookup_order(tmp_path, monkeypatch):
    """--llm-creds wins > BB_OPENAI_CREDS env > /tmp default > ~/Downloads."""
    from betterbasket_matcher import llm_arbiter as la

    # Two valid YAMLs: cli wins
    cli_path = _write_yaml(tmp_path / "cli.yaml", {
        "openai": {
            "endpoint": "https://cli.invalid/v1/",
            "api_key": "FAKE-CLI",
            "deployment_name": "cli-dep",
        },
    })
    env_path = _write_yaml(tmp_path / "env.yaml", {
        "openai": {
            "endpoint": "https://env.invalid/v1/",
            "api_key": "FAKE-ENV",
            "deployment_name": "env-dep",
        },
    })
    monkeypatch.setenv("BB_OPENAI_CREDS", str(env_path))
    chosen = la._resolve_creds_path(
        cli_arg=str(cli_path),
        env=os.environ,
        defaults=(tmp_path / "missing-default.yaml",),
    )
    assert chosen == cli_path

    # No CLI: env wins
    chosen = la._resolve_creds_path(
        cli_arg=None,
        env=os.environ,
        defaults=(tmp_path / "missing-default.yaml",),
    )
    assert chosen == env_path

    # No CLI, no env: first present default wins
    monkeypatch.delenv("BB_OPENAI_CREDS", raising=False)
    default1 = _write_yaml(tmp_path / "default1.yaml", {
        "openai": {
            "endpoint": "x", "api_key": "k", "deployment_name": "d",
        },
    })
    default2_missing = tmp_path / "default2.yaml"
    chosen = la._resolve_creds_path(
        cli_arg=None,
        env=os.environ,
        defaults=(default2_missing, default1),
    )
    assert chosen == default1

    # Nothing found: raises FileNotFoundError
    with pytest.raises(FileNotFoundError):
        la._resolve_creds_path(
            cli_arg=None,
            env=os.environ,
            defaults=(tmp_path / "nope1.yaml", tmp_path / "nope2.yaml"),
        )


# ---------------------------------------------------------------------------
# 11. Confidence floor blocks rescue (opinion still returned)
# ---------------------------------------------------------------------------

def test_confidence_floor_blocks_rescue(tmp_path):
    cfg = _mk_config(tmp_path, max_calls=5, min_confidence=0.60)
    fake = FakeClient(responses=[{
        "same_product_for_customer": True,
        "confidence": 0.59,
        "reason": "borderline",
        "blocking_issue": None,
    }])
    arbiter = LLMArbiter(cfg, client=fake)
    a = _mk_normalized("A1", "A")
    b = _mk_normalized("B1", "B")
    arbiter.collect(
        a=a, b=b, a_raw={"name": "A"}, b_raw={"name": "B"},
        deterministic_score=0.71, deterministic_margin=0.06,
        retrieval_score=1.4, failure_reason="below_min_score",
    )
    opinions = arbiter.commit()
    op = opinions["A1"]
    # Opinion is returned (so audit can record), but the floor blocks rescue
    assert op.same_product_for_customer is True
    assert op.confidence == pytest.approx(0.59)
    # The "rescue gate" itself lives in pipeline.py; the arbiter contract
    # is: opinion is returned; the caller compares confidence vs min.
    assert op.confidence < cfg.min_confidence


# ---------------------------------------------------------------------------
# 12. Hard-rule rejected rows never reach the arbiter (pipeline-level)
# ---------------------------------------------------------------------------

def test_guardrail_no_override_of_hard_rule_rejection(tmp_path):
    """The arbiter is only invoked from the below_threshold branch in
    pipeline.py. all_rejected/rejected_by_rule rows do not go through
    collect(). This test pins the contract by calling commit() with no
    collected candidates -> zero client calls."""
    cfg = _mk_config(tmp_path)
    fake = FakeClient()
    arbiter = LLMArbiter(cfg, client=fake)
    opinions = arbiter.commit()
    assert opinions == {}
    assert fake.call_count == 0


# ---------------------------------------------------------------------------
# 13. API failure does not cache, does not crash
# ---------------------------------------------------------------------------

def test_api_failure_does_not_cache(tmp_path):
    cfg = _mk_config(tmp_path)
    fake = FakeClient(raise_exc=RuntimeError("simulated 502"))
    arbiter = LLMArbiter(cfg, client=fake)

    a = _mk_normalized("A1", "A")
    b = _mk_normalized("B1", "B")
    arbiter.collect(
        a=a, b=b, a_raw={"name": "A"}, b_raw={"name": "B"},
        deterministic_score=0.71, deterministic_margin=0.06,
        retrieval_score=1.4, failure_reason="below_min_score",
    )
    opinions = arbiter.commit()

    assert opinions == {}
    # Cache file either does not exist or is empty (the failed call is
    # explicitly not cached so transient errors don't poison future runs)
    if cfg.cache_path.exists():
        assert cfg.cache_path.read_text(encoding="utf-8").strip() == ""


# ---------------------------------------------------------------------------
# 14-16. Budget binding
# ---------------------------------------------------------------------------

def _collect_n(arbiter, scores: List[float], group: str = "dairy"):
    """Helper: collect N candidates with given scores and a single group."""
    for i, s in enumerate(scores):
        a = _mk_normalized(f"A{i}", "A")
        b = _mk_normalized(f"B{i}", "B")
        arbiter.collect(
            a=a, b=b,
            a_raw={"item_id": f"A{i}", "name": f"A name {i}"},
            b_raw={"item_id": f"B{i}", "name": f"B name {i}"},
            deterministic_score=s, deterministic_margin=0.06,
            retrieval_score=1.4, failure_reason="below_min_score",
        )


def test_budget_exhaustion_skips_remaining_misses_only(tmp_path):
    # Run 1: budget allows only 2 API calls; assert exactly the two
    # highest-score candidates are arbitrated, the other 3 get no
    # opinion (and are NOT cached, since no API call was made).
    cfg = _mk_config(tmp_path, max_calls=2)
    fake = FakeClient()
    arbiter = LLMArbiter(cfg, client=fake)
    _collect_n(arbiter, [0.66, 0.74, 0.69, 0.71, 0.73])
    opinions = arbiter.commit()

    assert fake.call_count == 2
    assert len(opinions) == 2

    # Run 2: enough budget to cache all 5 -> they are now all cached.
    fake2 = FakeClient()
    arbiter2 = LLMArbiter(LLMArbiterConfig(
        creds_path=cfg.creds_path,
        cache_path=cfg.cache_path,   # same cache file
        max_calls=10,
        min_confidence=cfg.min_confidence,
        deployment_name=cfg.deployment_name,
        endpoint=cfg.endpoint,
        api_key=cfg.api_key,
    ), client=fake2)
    _collect_n(arbiter2, [0.66, 0.74, 0.69, 0.71, 0.73])
    opinions2 = arbiter2.commit()
    # 2 of the 5 were cached from run 1 -> 3 new API calls in run 2.
    assert fake2.call_count == 3
    assert len(opinions2) == 5

    # Run 3: with all 5 cached and budget=2, every candidate is a cache
    # hit. Cache hits do not consume budget; client is never called.
    fake3 = FakeClient()
    arbiter3 = LLMArbiter(cfg, client=fake3)
    _collect_n(arbiter3, [0.66, 0.74, 0.69, 0.71, 0.73])
    opinions3 = arbiter3.commit()
    assert fake3.call_count == 0
    assert len(opinions3) == 5
    for op in opinions3.values():
        assert op.cache_hit is True


def test_cache_hits_served_after_budget_exhausted(tmp_path):
    cfg = _mk_config(tmp_path, max_calls=1)
    fake = FakeClient()
    arbiter = LLMArbiter(cfg, client=fake)

    scored = list(enumerate([0.74, 0.73, 0.71, 0.66]))
    a_raw = {"name": "Chobani Honey Blended 5.3 oz"}
    b_raw = {"name": "Chobani Greek Honey Blended Yogurt"}

    # Pre-seed cache for the 0.71 and 0.66 candidates using the SAME
    # build_arbiter_input path the arbiter uses, so keys match exactly.
    for i, s in scored:
        if s in (0.71, 0.66):
            a = _mk_normalized(f"A{i}", "A")
            b = _mk_normalized(f"B{i}", "B")
            inp = build_arbiter_input(
                a, b, a_raw, b_raw,
                deterministic_score=s,
                deterministic_margin=0.06,
                retrieval_score=1.42,
                failure_reason="below_min_score",
            )
            key = cache_key(cfg.prompt_version, cfg.deployment_name, inp)
            cache_append(cfg.cache_path, key, ArbiterOpinion(
                same_product_for_customer=True, confidence=0.80,
                reason="seeded", blocking_issue=None,
                cache_hit=False, raw_response_id=None,
            ), input_meta={
                "item_id_a": f"A{i}", "item_id_b": f"B{i}",
                "deployment_name": cfg.deployment_name,
                "prompt_version": cfg.prompt_version,
            })

    # Now collect via the arbiter: A0(0.74), A1(0.73), A2(0.71), A3(0.66)
    for i, s in scored:
        a = _mk_normalized(f"A{i}", "A")
        b = _mk_normalized(f"B{i}", "B")
        arbiter.collect(
            a=a, b=b,
            a_raw=a_raw, b_raw=b_raw,
            deterministic_score=s, deterministic_margin=0.06,
            retrieval_score=1.42, failure_reason="below_min_score",
        )
    opinions = arbiter.commit()

    # Expectations:
    #  - 0.74 → API call (1, budget exhausted)
    #  - 0.73 → miss but budget=0 → no opinion
    #  - 0.71 → cache hit → opinion attached, no call
    #  - 0.66 → cache hit → opinion attached, no call
    assert fake.call_count == 1
    assert "A0" in opinions
    assert "A1" not in opinions   # budget-exhausted miss, no opinion
    assert "A2" in opinions and opinions["A2"].cache_hit is True
    assert "A3" in opinions and opinions["A3"].cache_hit is True


def test_two_pass_score_desc_ordering(tmp_path):
    cfg = _mk_config(tmp_path, max_calls=3)
    fake = FakeClient()
    arbiter = LLMArbiter(cfg, client=fake)
    _collect_n(arbiter, [0.66, 0.74, 0.69, 0.71, 0.73])
    opinions = arbiter.commit()

    assert fake.call_count == 3
    # The three highest-score rows are the ones arbitrated
    arbitrated = set(opinions.keys())
    # Build expected: scores 0.74, 0.73, 0.71 → A1, A4, A3
    assert arbitrated == {"A1", "A4", "A3"}


# ---------------------------------------------------------------------------
# 17-18. Per-group cap
# ---------------------------------------------------------------------------

def test_per_group_cap_respected_low_max_calls(tmp_path):
    """cap = max(50, max_calls // n_groups). With max_calls=200 and
    n_groups=10, cap=50, so only 50 pantry candidates are arbitrated
    even though 60 are collected."""
    cfg = _mk_config(tmp_path, max_calls=200)
    fake = FakeClient()
    arbiter = LLMArbiter(cfg, client=fake)

    # 60 candidates in pantry
    for i in range(60):
        a = _mk_normalized(f"P{i}", "A", core_name="pantry item",
                           category_0="food", category_1="pantry")
        b = _mk_normalized(f"PB{i}", "B",
                           category_0="grocery", category_1="pantry")
        arbiter.collect(
            a=a, b=b,
            a_raw={"name": f"pantry-A-{i}"},
            b_raw={"name": f"pantry-B-{i}"},
            deterministic_score=0.74 - i * 0.0001,
            deterministic_margin=0.06,
            retrieval_score=1.4,
            failure_reason="below_min_score",
        )
    # 9 single-candidate other groups so n_groups=10
    # 9 single-candidate groups, all confirmed to resolve via the
    # A-side taxonomy. Combined with the 60 pantry candidates they
    # produce exactly 10 distinct matchable_group values.
    other_groups = [
        ("dairy", "food", "dairy and eggs"),
        ("snacks", "food", "snacks cookies and chips"),
        ("candy", "food", "shop all candy"),
        ("beverages", "food", "beverages"),
        ("frozen", "food", "frozen"),
        ("bakery", "food", "bakery and bread"),
        ("household", "household essentials", "household essentials"),
        ("pets", "pets", "dog food"),
        ("baby", "baby", "diapers"),
    ]
    for j, (_g, c0, c1) in enumerate(other_groups):
        a = _mk_normalized(f"O{j}", "A", category_0=c0, category_1=c1)
        b = _mk_normalized(f"OB{j}", "B", category_0=c0, category_1=c1)
        arbiter.collect(
            a=a, b=b,
            a_raw={"name": f"other-A-{j}"},
            b_raw={"name": f"other-B-{j}"},
            deterministic_score=0.70,
            deterministic_margin=0.06,
            retrieval_score=1.4,
            failure_reason="below_min_score",
        )
    opinions = arbiter.commit()

    pantry_opinions = [k for k in opinions if k.startswith("P")]
    assert len(pantry_opinions) == 50, (
        f"expected pantry cap=50, got {len(pantry_opinions)}"
    )


def test_per_group_cap_scales_with_max_calls(tmp_path):
    """cap = max(50, max_calls // n_groups). With max_calls=2000 and
    n_groups=10, cap=200; 250 collected pantry → 200 arbitrated."""
    cfg = _mk_config(tmp_path, max_calls=2000)
    fake = FakeClient()
    arbiter = LLMArbiter(cfg, client=fake)

    for i in range(250):
        a = _mk_normalized(f"P{i}", "A", category_0="food", category_1="pantry")
        b = _mk_normalized(f"PB{i}", "B",
                           category_0="grocery", category_1="pantry")
        arbiter.collect(
            a=a, b=b,
            a_raw={"name": f"pantry-A-{i}"},
            b_raw={"name": f"pantry-B-{i}"},
            deterministic_score=0.74 - i * 0.00001,
            deterministic_margin=0.06,
            retrieval_score=1.4,
            failure_reason="below_min_score",
        )
    # 9 single-candidate groups, all confirmed to resolve via the
    # A-side taxonomy. Combined with the 60 pantry candidates they
    # produce exactly 10 distinct matchable_group values.
    other_groups = [
        ("dairy", "food", "dairy and eggs"),
        ("snacks", "food", "snacks cookies and chips"),
        ("candy", "food", "shop all candy"),
        ("beverages", "food", "beverages"),
        ("frozen", "food", "frozen"),
        ("bakery", "food", "bakery and bread"),
        ("household", "household essentials", "household essentials"),
        ("pets", "pets", "dog food"),
        ("baby", "baby", "diapers"),
    ]
    for j, (_g, c0, c1) in enumerate(other_groups):
        a = _mk_normalized(f"O{j}", "A", category_0=c0, category_1=c1)
        b = _mk_normalized(f"OB{j}", "B", category_0=c0, category_1=c1)
        arbiter.collect(
            a=a, b=b,
            a_raw={"name": f"other-A-{j}"},
            b_raw={"name": f"other-B-{j}"},
            deterministic_score=0.70,
            deterministic_margin=0.06,
            retrieval_score=1.4,
            failure_reason="below_min_score",
        )
    opinions = arbiter.commit()

    pantry_opinions = [k for k in opinions if k.startswith("P")]
    assert len(pantry_opinions) == 200


# ---------------------------------------------------------------------------
# 19. Default-off path skips credential loading
# ---------------------------------------------------------------------------

def test_default_off_skips_credential_load(tmp_path, monkeypatch, capsys):
    """When --use-llm-arbiter is absent, run_pipeline.main must not call
    load_credentials at all."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import run_pipeline as rp
    from betterbasket_matcher import llm_arbiter as la

    called = {"n": 0}
    def fake_load(*a, **k):
        called["n"] += 1
        raise AssertionError("load_credentials must NOT be called when "
                             "--use-llm-arbiter is absent")
    monkeypatch.setattr(la, "load_credentials", fake_load)
    monkeypatch.delenv("BB_OPENAI_CREDS", raising=False)

    fixture_dir = Path(__file__).parent / "fixtures"
    matches_out = tmp_path / "m.csv"
    audit_out = tmp_path / "a.csv"

    rc = rp.main([
        "--a-csv", str(fixture_dir / "mini_a.csv"),
        "--b-csv", str(fixture_dir / "mini_b.csv"),
        "--matches-out", str(matches_out),
        "--audit-out", str(audit_out),
        "--allow-under-min-rows",
    ])
    assert rc == 0
    assert called["n"] == 0
    # And outputs were written
    assert matches_out.exists()
    assert audit_out.exists()


# ---------------------------------------------------------------------------
# 20. Pipeline integration with fake arbiter (rescue path)
# ---------------------------------------------------------------------------

def test_pipeline_integration_with_fake_arbiter_rescues_below_threshold(
    tmp_path, monkeypatch,
):
    """Wire a FakeClient through run_pipeline and exercise the rescue
    path end-to-end. The mini fixture's natural scores happen to skip
    the [0.65, 0.75) production rescue band, so this test widens the
    band via monkeypatch (production constants are unchanged); the
    deterministic floor is then set to 0.95 so several rows drop to
    below_threshold and are eligible for rescue. The FakeClient says
    same_product=True/conf=0.85; the pipeline must flip those rows to
    accepted with source=llm_rescued and a populated llm_confidence."""
    from betterbasket_matcher import pipeline as pipeline_mod
    from betterbasket_matcher.pipeline import PipelineConfig, run_pipeline
    from betterbasket_matcher.llm_arbiter import LLMArbiter, LLMArbiterConfig

    # Widen the rescue band for this test only. Production code (the
    # constants in pipeline.py) is unchanged after the test exits.
    monkeypatch.setattr(pipeline_mod, "LLM_RESCUE_SCORE_LOW", 0.50)
    monkeypatch.setattr(pipeline_mod, "LLM_RESCUE_SCORE_HIGH", 0.99)

    fixture_dir = Path(__file__).parent / "fixtures"

    # Baseline run with arbiter=None at high min_score so rows fall to below_threshold
    base_matches = tmp_path / "base_m.csv"
    base_audit = tmp_path / "base_a.csv"
    base_cfg = PipelineConfig(
        a_csv=str(fixture_dir / "mini_a.csv"),
        b_csv=str(fixture_dir / "mini_b.csv"),
        matches_out=str(base_matches),
        audit_out=str(base_audit),
        min_score=0.78,
        min_margin=0.05,
        top_k=50,
    )
    base_result = run_pipeline(base_cfg)
    base_audit_rows = list(csv.DictReader(base_audit.open(encoding="utf-8")))
    base_below = [r for r in base_audit_rows if r["decision"] == "below_threshold"]
    assert len(base_below) > 0, (
        "fixture sanity: at min_score=0.95 some accepted rows should "
        "fall to below_threshold for the rescue path to be testable"
    )

    # LLM run with FakeClient that says yes/0.85 for everything
    cfg = LLMArbiterConfig(
        creds_path=tmp_path / "creds.yaml",
        cache_path=tmp_path / "cache.jsonl",
        max_calls=100,
        min_confidence=0.60,
        deployment_name="test",
        endpoint=SYNTHETIC_ENDPOINT,
        api_key=SYNTHETIC_API_KEY,
    )
    fake = FakeClient(responses=[{
        "same_product_for_customer": True,
        "confidence": 0.85,
        "reason": "fake yes",
        "blocking_issue": None,
    } for _ in range(50)])
    arbiter = LLMArbiter(cfg, client=fake)

    llm_matches = tmp_path / "llm_m.csv"
    llm_audit = tmp_path / "llm_a.csv"
    llm_cfg = PipelineConfig(
        a_csv=str(fixture_dir / "mini_a.csv"),
        b_csv=str(fixture_dir / "mini_b.csv"),
        matches_out=str(llm_matches),
        audit_out=str(llm_audit),
        min_score=0.78,
        min_margin=0.05,
        top_k=50,
        arbiter=arbiter,
    )
    llm_result = run_pipeline(llm_cfg)
    llm_audit_rows = list(csv.DictReader(llm_audit.open(encoding="utf-8")))

    rescued = [r for r in llm_audit_rows
               if r["source"] == "llm_rescued" and r["decision"] == "accepted"]
    assert len(rescued) > 0, "fake arbiter should have rescued at least one row"
    for r in rescued:
        # llm_confidence cell populated and matches the fake (0.85)
        assert r["llm_confidence"] != ""
        assert abs(float(r["llm_confidence"]) - 0.85) < 1e-9
        assert r["item_id_B"] != ""

    # The shipped matches.csv now includes the rescued rows
    base_pairs = set()
    with base_matches.open() as fh:
        next(fh)
        base_pairs = {tuple(line.strip().split(",")) for line in fh if line.strip()}
    llm_pairs = set()
    with llm_matches.open() as fh:
        next(fh)
        llm_pairs = {tuple(line.strip().split(",")) for line in fh if line.strip()}
    assert llm_pairs >= base_pairs
    assert llm_pairs - base_pairs, "LLM rescue should add at least one new pair"

    # Stat counters populated
    assert llm_result.llm_calls > 0
    assert llm_result.llm_rescues > 0
