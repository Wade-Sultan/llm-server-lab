"""
Quantization and context window as intake questions.

The failure these exist to stop: a user asked for a single-GPU box serving a 31B
model and was shown a $15,000 96GB workstation card, because nothing in the
intake ever established what precision they intended to serve at. The pipeline
assumed full precision by omission — the most expensive assumption available.

Pure-logic tests over the profile gate; no DB, no network.
"""

from __future__ import annotations

import pytest

from app.schemas.chat import BuildProfile
from app.services import chat_pipeline as cp


def _llm_profile(**overrides) -> BuildProfile:
    """A single-GPU local-LLM profile, complete except where overridden."""
    base = {
        "primary_use": "ai",
        "ai_workload": "inference",
        "ai_model_scale": "medium",
        "budget_tier": "elite",
        "price_sensitivity": "firm",
        "llm_quantization": "yes",
        "llm_context_tokens": "8k",
    }
    return BuildProfile(**{**base, **overrides})


def _server_profile(**overrides) -> BuildProfile:
    base = {
        "primary_use": "server",
        "server_workload": "ai_serving",
        "server_gpu_count": "1",
        "budget_tier": "elite",
        "price_sensitivity": "firm",
        "llm_quantization": "yes",
        "llm_context_tokens": "8k",
    }
    return BuildProfile(**{**base, **overrides})


# ---------------------------------------------------------------------------
# Which builds are LLM builds
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("workload", ["inference", "training"])
def test_llm_workloads_are_recognised(workload):
    assert cp._is_llm_build(_llm_profile(ai_workload=workload))


def test_image_generation_is_not_an_llm_build():
    """Diffusion VRAM is driven by resolution and batch, not weights plus KV
    cache — asking it for a context window would be noise."""
    assert not cp._is_llm_build(_llm_profile(ai_workload="image_gen"))


@pytest.mark.parametrize("workload", ["ai_serving", "ai_training"])
def test_ai_servers_are_llm_builds(workload):
    assert cp._is_llm_build(_server_profile(server_workload=workload))


@pytest.mark.parametrize("workload", ["hpc", "virtualization", "storage"])
def test_non_ai_servers_are_not_llm_builds(workload):
    assert not cp._is_llm_build(_server_profile(server_workload=workload))


def test_a_gaming_build_is_never_an_llm_build():
    assert not cp._is_llm_build(BuildProfile(primary_use="gaming", budget_tier="mid"))


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


def test_an_llm_build_is_incomplete_without_a_quantization_answer():
    profile = _llm_profile(llm_quantization=None)
    assert not cp.is_profile_complete(profile)
    assert any("quantized" in f for f in cp._missing_fields(profile))


def test_an_llm_build_is_incomplete_without_a_context_answer():
    profile = _llm_profile(llm_context_tokens=None)
    assert not cp.is_profile_complete(profile)
    assert any("context" in f for f in cp._missing_fields(profile))


def test_unsure_is_a_real_answer_and_completes_the_profile():
    """The whole point of accepting 'unsure': a user who has never heard of
    quantization must not be stuck behind the question."""
    profile = _llm_profile(llm_quantization="unsure", llm_context_tokens="unsure")
    assert cp.is_profile_complete(profile)
    assert cp._missing_fields(profile) == []


def test_an_ai_server_is_gated_on_the_same_two_answers():
    assert not cp.is_profile_complete(_server_profile(llm_quantization=None))
    assert cp.is_profile_complete(_server_profile())


def test_a_storage_server_is_not_gated_on_llm_questions():
    """Gating a NAS build on context window would be an unanswerable question."""
    profile = _server_profile(
        server_workload="storage", llm_quantization=None, llm_context_tokens=None
    )
    assert cp.is_profile_complete(profile)


def test_image_generation_is_not_gated_on_llm_questions():
    profile = _llm_profile(
        ai_workload="image_gen", llm_quantization=None, llm_context_tokens=None
    )
    assert cp.is_profile_complete(profile)


def test_quantization_is_not_asked_before_the_workload_is_known():
    """These are follow-ups to "you're running LLMs", not opening questions —
    the router must not surface them before the workload itself."""
    profile = _llm_profile(ai_workload=None, llm_quantization=None)
    missing = cp._missing_fields(profile)
    assert not any("quantized" in f for f in missing)
    assert any("AI workload" in f for f in missing)


def test_quantization_is_not_asked_before_the_model_scale():
    profile = _llm_profile(ai_model_scale=None, llm_quantization=None)
    missing = cp._missing_fields(profile)
    assert not any("quantized" in f for f in missing)
    assert any("how large" in f for f in missing)


def test_the_gate_and_the_missing_list_agree():
    """route() logs an error when these two diverge, so keep them in lockstep."""
    for profile in (
        _llm_profile(llm_quantization=None),
        _llm_profile(llm_context_tokens=None),
        _llm_profile(llm_quantization=None, llm_context_tokens=None),
        _server_profile(llm_quantization=None),
    ):
        assert not cp.is_profile_complete(profile)
        assert cp._missing_fields(profile)


# ---------------------------------------------------------------------------
# What the build steps are actually told
# ---------------------------------------------------------------------------


def test_the_answer_reaches_the_build_as_an_instruction():
    """The Decide* steps read prose, not enums — "llm.quantization: unsure" is
    not actionable, "size for 4-bit" is."""
    request = cp._profile_to_build_request(_llm_profile(llm_quantization="yes"))
    assert "4-bit" in request.answers["llm.quantization"]


def test_full_precision_is_passed_through_as_a_constraint():
    request = cp._profile_to_build_request(_llm_profile(llm_quantization="no"))
    guidance = request.answers["llm.quantization"]
    assert "FULL PRECISION" in guidance
    assert "2 bytes per parameter" in guidance


def test_unsure_resolves_to_the_quantized_default():
    """THE LOAD-BEARING DEFAULT. Sizing an unsure user for fp16 would reintroduce
    the original failure by way of politeness."""
    request = cp._profile_to_build_request(_llm_profile(llm_quantization="unsure"))
    assert "4-bit" in request.answers["llm.quantization"]


def test_long_context_warns_about_kv_cache():
    request = cp._profile_to_build_request(_llm_profile(llm_context_tokens="128k"))
    assert "KV cache" in request.answers["llm.context"]


def test_an_off_menu_value_degrades_to_the_default():
    """These strings come from a language model; an unexpected one must not
    KeyError the build."""
    request = cp._profile_to_build_request(
        _llm_profile(llm_quantization="q4_k_m", llm_context_tokens="1M")
    )
    assert "4-bit" in request.answers["llm.quantization"]
    assert "8k" in request.answers["llm.context"]


def test_a_non_llm_build_carries_no_llm_guidance():
    request = cp._profile_to_build_request(
        BuildProfile(primary_use="gaming", budget_tier="mid", gaming_resolution="1440p")
    )
    assert "llm.quantization" not in request.answers
    assert "llm.context" not in request.answers
