"""Guards the case pause: the build stops, is saved, and is resumed by a pick.

WHAT IS ACTUALLY AT RISK HERE. Nine LLM-backed decisions happen before the
pause, and the whole point of persisting them is that they are expensive and
unrepeatable — a resume that silently lost a field would produce a *different*
build than the one the user was shown case options for, with no error anywhere.
So the round-trip test below asserts field-by-field equality of the whole state
rather than spot-checking a few attributes.

The claim tests cover the other half: a paused build must be resumable exactly
once, or a double-click produces two builds from one pipeline run.
"""

from __future__ import annotations

import asyncio

from app.schemas.chat import BuildRequest
from app.services.chat_pipeline import resolve_case_choice
from app.services.recommender.dspy_pipeline import DSPyBuildState
from app.services.recommender.recording import BuildRecorder

_OPTIONS = [
    {"name": "Fractal North", "reason": "best value"},
    {"name": "NZXT H5 Flow", "reason": "cheaper"},
    {"name": "Lian Li A4-H2O", "reason": "smaller"},
]


def _request() -> BuildRequest:
    return BuildRequest(use_cases=["gaming"], budget_usd=2000)


def _populated_state() -> DSPyBuildState:
    """A state as it stands at the pause: every pre-case decision made."""
    state = DSPyBuildState(request=_request())
    state.session_id = "sess-1"
    state.use_case_summary = "Use cases: gaming"
    state.cpu_name = "Ryzen 7 9800X3D"
    state.cpu_socket = "AM5"
    state.cpu_tdp_w = 120
    state.cpu_ddr_gens = ["ddr5"]
    state.cooler_name = "Peerless Assassin 120"
    state.mobo_name = "B650 Tomahawk"
    state.mobo_form_factor = "atx"
    state.mobo_module_types = ["udimm"]
    state.ram_group = "32GB DDR5-6000"
    state.ram_name = "Corsair Vengeance 32GB"
    state.storage_groups = ["2TB Gen4"]
    state.storage_names = ["WD SN850X 2TB"]
    state.gpu_chipset = "RTX 5080"
    state.gpu_count = 2
    state.gpu_tdp_w = 360
    state.psu_group = "1000W Gold"
    state.psu_form_factor = "atx"
    state.case_options = _OPTIONS
    state.thresholds = {"gpu": "no faster card under budget"}
    return state


# ---------------------------------------------------- the state round trip --


def test_a_paused_state_survives_the_round_trip_intact():
    """Every decision made before the pause has to come back, or the resumed
    build is not the build the user was shown options for."""
    from dataclasses import fields

    original = _populated_state()
    restored = DSPyBuildState.from_dict(original.to_dict())

    carried = [
        f.name
        for f in fields(DSPyBuildState)
        if f.name not in ("progress_callback", "catalog_requirements", "request")
    ]
    for name in carried:
        assert getattr(restored, name) == getattr(original, name), name
    assert restored.request.budget_usd == original.request.budget_usd
    assert restored.request.use_cases == original.request.use_cases


def test_the_progress_callback_is_reattached_not_restored():
    """The stream a paused build was writing to is gone by resume time, so the
    resuming turn supplies its own sink."""
    calls: list[tuple[str, str]] = []
    restored = DSPyBuildState.from_dict(
        _populated_state().to_dict(),
        progress_callback=lambda step, msg: calls.append((step, msg)),
    )

    assert restored.progress_callback is not None
    restored.progress_callback("fans", "Checking airflow…")
    assert calls == [("fans", "Checking airflow…")]


def test_a_payload_from_an_older_deploy_still_restores():
    """Rolling deploys mean a pause written by the previous version can be
    resumed by the next one. Unknown keys are ignored and absent ones default,
    rather than the resume failing on a field it has never heard of."""
    payload = _populated_state().to_dict()
    payload["a_field_from_the_future"] = "ignored"
    del payload["cooler_name"]

    restored = DSPyBuildState.from_dict(payload)

    assert restored.cpu_name == "Ryzen 7 9800X3D"
    assert restored.cooler_name == ""


# ------------------------------------------------------- the recorder half --


def test_the_recorder_survives_the_pause_as_one_session():
    """The telemetry drain upserts with on_conflict_do_nothing, so a run split
    across two turns has to finish as the SAME build_sessions row — a second
    session id would be silently dropped instead of recorded."""
    recorder = BuildRecorder(_request(), "v-test", conversation_id=None)
    recorder.set_catalog_requirements({"min_vram_gb": 16})
    recorder.set_reference_build("ref_key", {"label": "Reference"})

    restored = BuildRecorder.restore(recorder.to_dict())

    assert restored.session_id == recorder.session_id
    assert restored.started_at == recorder.started_at
    assert restored.pipeline_version == "v-test"
    assert restored.catalog_requirements == {"min_vram_gb": 16}
    assert restored.reference_build_key == "ref_key"
    assert restored.reference_build == {"label": "Reference"}


def test_a_guest_conversation_id_restores_as_null():
    """Guests carry a synthetic 'turn:<uuid>' thread id, which __init__ already
    coerces to NULL; restore must not resurrect it as a UUID parse error."""
    recorder = BuildRecorder(
        _request(), "v-test", conversation_id="turn:not-a-real-uuid"
    )
    assert recorder.conversation_id is None
    assert BuildRecorder.restore(recorder.to_dict()).conversation_id is None


def test_the_resumed_telemetry_stays_on_the_pausing_conversation():
    """The single build_sessions row a paused-then-resumed build produces has
    to point at the conversation that ran it, or cost-per-build analytics
    attribute a build to a thread that never asked for one."""
    conversation_id = "11111111-1111-1111-1111-111111111111"
    recorder = BuildRecorder(_request(), "v-test", conversation_id=conversation_id)

    restored = BuildRecorder.restore(recorder.to_dict())

    assert str(restored.conversation_id) == conversation_id
    assert restored.session_id == recorder.session_id


# --------------------------------------------------- which case gets built --


def test_an_offered_pick_is_honoured():
    assert resolve_case_choice("NZXT H5 Flow", _OPTIONS) == "NZXT H5 Flow"


def test_a_case_that_was_never_offered_is_refused():
    """The options live only in the paused payload, so this is the only place
    that can tell a real pick from a stale token or a hand-crafted request."""
    assert resolve_case_choice("Some Other Case", _OPTIONS) == "Fractal North"


def test_a_resume_with_no_case_name_falls_back():
    assert resolve_case_choice(None, _OPTIONS) == "Fractal North"


# ------------------------------------------------------------- the claim ----


class _FakeValkey:
    """Enough of the client for paused_build: SET, GET and an atomic GETDEL."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def set(self, key, value, ex=None):
        self.store[key] = value

    async def get(self, key):
        return self.store.get(key)

    async def getdel(self, key):
        return self.store.pop(key, None)


def _valkey_only(monkeypatch, client):
    """Point paused_build at a fake Valkey and stub Postgres out entirely, so
    the claim's Valkey half is exercised on its own."""
    from app.services import paused_build

    async def _client():
        return client

    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(paused_build, "get_client", _client)
    monkeypatch.setattr(paused_build, "_mark_resumed", _noop)
    monkeypatch.setattr(paused_build, "_claim_in_postgres", _noop)


_CONV = "11111111-1111-1111-1111-111111111111"
_OTHER_CONV = "22222222-2222-2222-2222-222222222222"


def _store_pause(client, token: str, conversation_id: str | None = _CONV) -> dict:
    import json

    from app.services import paused_build

    payload = {"state": {"x": 1}, "conversation_id": conversation_id}
    client.store[paused_build._key(token)] = json.dumps(payload)
    return payload


def test_a_paused_build_is_claimable_exactly_once(monkeypatch):
    """Two picks racing — a double click, a redelivered message — must not both
    come away with the payload and build the machine twice."""
    from app.services import paused_build

    client = _FakeValkey()
    _valkey_only(monkeypatch, client)
    payload = _store_pause(client, "tok-1")

    async def _run():
        return await asyncio.gather(
            paused_build.load_and_claim("tok-1", _CONV),
            paused_build.load_and_claim("tok-1", _CONV),
        )

    first, second = asyncio.run(_run())
    claimed = [r for r in (first, second) if r is not None]
    assert len(claimed) == 1
    assert claimed[0] == payload


def test_a_pick_from_another_conversation_is_refused(monkeypatch):
    """A resumed build is appended to whatever conversation the resuming turn
    names, so the token alone must not be enough — otherwise a pick could graft
    a build onto a thread whose picker was never shown."""
    from app.services import paused_build

    client = _FakeValkey()
    _valkey_only(monkeypatch, client)
    _store_pause(client, "tok-3")

    assert asyncio.run(paused_build.load_and_claim("tok-3", _OTHER_CONV)) is None


def test_a_refused_pick_does_not_consume_the_pause(monkeypatch):
    """Rejecting after claiming would let anyone holding a token burn a build
    its rightful conversation could still have finished."""
    from app.services import paused_build

    client = _FakeValkey()
    _valkey_only(monkeypatch, client)
    payload = _store_pause(client, "tok-4")

    async def _run():
        refused = await paused_build.load_and_claim("tok-4", _OTHER_CONV)
        return refused, await paused_build.load_and_claim("tok-4", _CONV)

    refused, legitimate = asyncio.run(_run())
    assert refused is None
    assert legitimate == payload


def test_a_guest_pause_matches_on_its_synthetic_id(monkeypatch):
    """Guest threads carry a 'turn:<uuid>' id that is deliberately not a UUID;
    matching is on the raw string, so it still has to line up."""
    from app.services import paused_build

    client = _FakeValkey()
    _valkey_only(monkeypatch, client)
    payload = _store_pause(client, "tok-5", conversation_id="turn:abc")

    async def _run():
        wrong = await paused_build.load_and_claim("tok-5", "turn:def")
        return wrong, await paused_build.load_and_claim("tok-5", "turn:abc")

    wrong, right = asyncio.run(_run())
    assert wrong is None
    assert right == payload


def test_an_unknown_token_claims_nothing(monkeypatch):
    from app.services import paused_build

    _valkey_only(monkeypatch, _FakeValkey())
    assert asyncio.run(paused_build.load_and_claim("nope", _CONV)) is None


def test_peek_does_not_claim(monkeypatch):
    """Diagnostics must not consume a build the user can still resume."""
    from app.services import paused_build

    client = _FakeValkey()
    _valkey_only(monkeypatch, client)
    payload = _store_pause(client, "tok-2")

    async def _run():
        peeked = await paused_build.peek("tok-2")
        claimed = await paused_build.load_and_claim("tok-2", _CONV)
        return peeked, claimed

    peeked, claimed = asyncio.run(_run())
    assert peeked == payload
    assert claimed == payload
