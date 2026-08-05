"""Per-request OpenRouter blocker for load tests.

WHY THIS EXISTS. Locust driving /chat would otherwise make real OpenRouter calls
— profile extraction, elicitation, recommendation, and one LM call per build
step. A few hundred simulated users is real money, spent to learn nothing about
the LLM. This stubs the two places the service talks to OpenRouter so a load test
exercises everything else for real (routing, auth, Postgres, SSE framing,
telemetry) while making zero outbound LLM calls.

HOW IT IS TRIGGERED. A request carrying `X-Palladium-Load-Test: <secret>` is
served with stub LMs. Everything else — every real user — takes the normal path
and reaches OpenRouter. That is what lets a load test run against the real
production deployment rather than a parallel copy of it.

SAFE BY DEFAULT. The secret comes from LOAD_TEST_SECRET, which is unset in
normal deployments; while it is unset the header is ignored entirely, so the
stub cannot be reached even by someone who guesses the header name. Comparison
is constant-time. The worst a leaked secret buys an attacker is a fake
recommendation for themselves — it grants no data access and spends nothing —
but it is still a credential, so it lives in palladium-secrets-builder rather
than in the ConfigMap.

WHERE THE STUBS ATTACH. Exactly two chokepoints, both on the async request path
where this ContextVar is reliably set:
  * chat_pipeline._get_client()        the streaming OpenAI/OpenRouter client
  * dspy_pipeline.session_lm()         every DSPy module call
Nothing else in the service makes an outbound LLM call.

IT HAS TO CROSS PUB/SUB. In any deployment with Valkey and Pub/Sub configured —
which is to say production — /chat does not run the turn at all: it publishes to
a topic and relays the worker's stream back. The worker is a different pod in a
different process, so a ContextVar set by this middleware means nothing there,
and every LM call of a load-tested turn would be real. The flag therefore
travels in the message body (routes/chat.py sets `load_test`) and is re-entered
on the far side with load_test_scope() (worker.py). Without that hop the two
chokepoints above are simply never reached under load.
"""

from __future__ import annotations

import hmac
import logging
from collections.abc import Awaitable, Callable, Generator, MutableMapping
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from app.core.config import settings

# Minimal ASGI aliases — enough for a middleware, without depending on
# starlette's private typing module.
Scope = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[MutableMapping[str, Any]]]
Send = Callable[[MutableMapping[str, Any]], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

logger = logging.getLogger(__name__)

# Lowercase: ASGI delivers header names lowercased in scope["headers"].
LOAD_TEST_HEADER = b"x-palladium-load-test"

_load_test: ContextVar[bool] = ContextVar("palladium_load_test", default=False)


def is_load_test() -> bool:
    """True when the current request opted into stub LMs with a valid secret."""
    return _load_test.get()


@contextmanager
def load_test_scope(enabled: bool) -> Generator[None]:
    """Re-enter load-test mode off the request path, for the Pub/Sub worker.

    The worker never sees the HTTP header — it sees a decoded message that says
    the turn was published by a load-tested request. This is where that claim is
    turned back into the ContextVar the stubs read.

    The claim is not taken on trust. It is ANDed with LOAD_TEST_SECRET being
    configured, which preserves the middleware's safe-by-default property on
    this side too: with the feature off, a message asserting `load_test` — a
    replay, a stale message left in the subscription across a config change —
    cannot put a worker into stub mode and start returning fake builds to real
    users.
    """
    token = _load_test.set(enabled and bool(settings.LOAD_TEST_SECRET))
    try:
        yield
    finally:
        _load_test.reset(token)


class LoadTestMiddleware:
    """Pure ASGI middleware — deliberately NOT BaseHTTPMiddleware.

    BaseHTTPMiddleware runs the downstream app in a separate anyio task, so a
    ContextVar set there is not visible to the endpoint. Plain ASGI middleware
    runs in the same task as the endpoint, which is the whole reason the flag
    survives all the way down to _get_client() and session_lm().
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        token = _load_test.set(_authorized(scope.get("headers") or []))
        try:
            await self.app(scope, receive, send)
        finally:
            # Reset rather than leave it set: under an ASGI server the same
            # task can be reused, and a leaked True would silently stub a real
            # user's request.
            _load_test.reset(token)


def _authorized(headers: list[tuple[bytes, bytes]]) -> bool:
    secret = settings.LOAD_TEST_SECRET
    if not secret:
        # Feature disabled. Do not even look at the header.
        return False

    for name, value in headers:
        if name.lower() != LOAD_TEST_HEADER:
            continue
        if hmac.compare_digest(value, secret.encode()):
            return True
        logger.warning("load-test header presented with an invalid secret; ignoring")
        return False
    return False
