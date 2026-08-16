"""LLM endpoint selection (app.core.config.LLMEndpoint).

The property under test is one-directional and worth pinning: local development
may move chat completions onto a machine in the room, but production must keep
going to OpenRouter, with OpenRouter's key and OpenRouter's cost accounting.
Nothing in the local overlay can reach production — but "nothing can" is exactly
the kind of claim that stops being true quietly, when a default changes or a new
call site reads the wrong field.

The token-budget tests are here for a related reason. Their defaults are sized
for a model that answers directly; a reasoning model spends the same budget
thinking and returns empty content at the cap, which surfaces as a chat that
asks questions forever and never builds. That is a silent failure, so the
defaults are pinned rather than assumed.
"""

from __future__ import annotations

import pytest

from app.core.config import OPENROUTER_URL, LLMEndpoint
from app.services.chat_models import ChatModelConfig
from app.services.llm import get_chat_model

_LOCAL = "http://172.30.32.1:1234/v1"


def _endpoint(base_url: str = "") -> LLMEndpoint:
    return LLMEndpoint(
        base_url, local_key="local-placeholder", openrouter_key="sk-or-real"
    )


# --- Production: unset means OpenRouter ---------------------------------------


def test_unset_base_url_is_openrouter():
    """The production configuration: no override anywhere."""
    endpoint = _endpoint()
    assert endpoint.is_openrouter
    assert endpoint.url == OPENROUTER_URL


def test_unset_base_url_uses_the_openrouter_key():
    """Not the LLM_API_KEY placeholder, which is only for a local server."""
    assert _endpoint().api_key == "sk-or-real"


def test_settings_default_to_openrouter(monkeypatch):
    """Both endpoints, straight off Settings, with no override set.

    Guards the actual wiring rather than LLMEndpoint in isolation: a chat_endpoint
    that read DISCOVERY_LLM_BASE_URL by mistake would still pass the tests above.
    """
    from app.core.config import settings

    monkeypatch.setattr(settings, "LLM_BASE_URL", "")
    monkeypatch.setattr(settings, "DISCOVERY_LLM_BASE_URL", "")

    assert settings.chat_endpoint.is_openrouter
    assert settings.discovery_endpoint.is_openrouter
    assert settings.chat_endpoint.url == OPENROUTER_URL
    assert settings.discovery_endpoint.url == OPENROUTER_URL


# --- Local: an override moves chat, and only chat -----------------------------


def test_base_url_moves_the_endpoint_off_openrouter():
    endpoint = _endpoint(_LOCAL)
    assert not endpoint.is_openrouter
    assert endpoint.url == _LOCAL
    assert endpoint.api_key == "local-placeholder"


def test_explicit_openrouter_url_still_counts_as_openrouter():
    """DISCOVERY_LLM_BASE_URL names OpenRouter outright to hold discovery there
    while chat moves to a local server. If that read as "somewhere else" the
    call would go out with the placeholder key and lose its cost accounting."""
    endpoint = _endpoint(OPENROUTER_URL)
    assert endpoint.is_openrouter
    assert endpoint.api_key == "sk-or-real"


def test_discovery_can_stay_on_openrouter_while_chat_is_local(monkeypatch):
    """The local overlay's arrangement, end to end."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "LLM_BASE_URL", _LOCAL)
    monkeypatch.setattr(settings, "DISCOVERY_LLM_BASE_URL", OPENROUTER_URL)

    assert not settings.chat_endpoint.is_openrouter
    assert settings.discovery_endpoint.is_openrouter


def test_discovery_follows_chat_when_it_has_no_override(monkeypatch):
    """An empty DISCOVERY_LLM_BASE_URL is "follow chat", not "use OpenRouter"."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "LLM_BASE_URL", _LOCAL)
    monkeypatch.setattr(settings, "DISCOVERY_LLM_BASE_URL", "")

    assert settings.discovery_endpoint.url == _LOCAL
    assert not settings.discovery_endpoint.is_openrouter


# --- Token budgets ------------------------------------------------------------


@pytest.mark.parametrize(
    ("attr", "expected"),
    [
        ("ROUTE_MAX_TOKENS", 8),
        ("RECOMMEND_MAX_TOKENS", 128),
        ("ELICIT_MAX_TOKENS", 256),
    ],
)
def test_chat_token_budget_defaults(attr, expected):
    """Production's values. Making these configurable must not change them."""
    assert getattr(ChatModelConfig, attr) == expected


def test_dspy_token_budget_default():
    from app.services.recommender import dspy_pipeline

    assert dspy_pipeline.RECOMMEND_MAX_TOKENS == 1024


# --- OpenRouter provider pinning ----------------------------------------------


def test_no_provider_pin_by_default(monkeypatch):
    """Unset must send exactly the request every deployment has always sent.

    The pin is a response to a degenerate completion traced to one upstream; it
    is not something to switch on speculatively, because narrowing routing
    trades a rare bad sample for a dependency on one provider's uptime.
    """
    from app.core.config import settings

    monkeypatch.setattr(settings, "OPENROUTER_PROVIDER", None)
    monkeypatch.setattr(settings, "LLM_BASE_URL", "")

    model = get_chat_model("google/gemma-4-31b-it")
    assert not getattr(model, "model_kwargs", None)


def test_provider_pin_reaches_the_chat_model(monkeypatch):
    from app.core.config import settings

    pin = {"ignore": ["SomeUpstream"], "allow_fallbacks": True}
    monkeypatch.setattr(settings, "OPENROUTER_PROVIDER", pin)
    monkeypatch.setattr(settings, "LLM_BASE_URL", "")

    model = get_chat_model("google/gemma-4-31b-it")
    assert model.model_kwargs["provider"] == pin


def test_provider_pin_reaches_discovery_and_dspy(monkeypatch):
    """A pin that covered only some calls to a model would be worse than none."""
    from app.core.config import settings
    from app.services.discovery.openrouter_client import extra_body
    from app.services.recommender.dspy_pipeline import _openrouter_extra_body

    pin = {"ignore": ["SomeUpstream"]}
    monkeypatch.setattr(settings, "OPENROUTER_PROVIDER", pin)
    monkeypatch.setattr(settings, "LLM_BASE_URL", "")
    monkeypatch.setattr(settings, "DISCOVERY_LLM_BASE_URL", "")

    assert extra_body(None)["extra_body"]["provider"] == pin
    assert _openrouter_extra_body()["provider"] == pin


def test_provider_pin_is_not_sent_to_a_local_server(monkeypatch):
    """ProviderPreferences is an OpenRouter concept; LM Studio would reject it."""
    from app.core.config import settings
    from app.services.recommender.dspy_pipeline import configure_dspy

    monkeypatch.setattr(settings, "OPENROUTER_PROVIDER", {"ignore": ["X"]})
    monkeypatch.setattr(settings, "LLM_BASE_URL", _LOCAL)

    model = get_chat_model("qwen3.8-27b")
    assert not getattr(model, "model_kwargs", None)

    configure_dspy()
    import dspy

    assert not dspy.settings.lm.kwargs.get("extra_body")
