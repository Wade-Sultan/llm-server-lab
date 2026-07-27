"""Process-local readiness flag for the DSPy/litellm warm-up.

Lives in its own module — not in chat_pipeline — so the readiness probe can
import it without dragging in the pipeline's import chain, which is the very
cost the warm-up exists to keep off the request path.

Set via threading.Event because the warm-up itself runs under
asyncio.to_thread, so the flag is written from a worker thread and read from
the event loop.
"""

import threading

_dspy_warm = threading.Event()


def mark_dspy_warm() -> None:
    """Record that the DSPy warm-up attempt has finished.

    Called on both success and failure: a failed warm-up falls back to
    configuring the LM lazily on the first /chat request, so the process is
    still able to serve. Leaving the flag unset in that case would keep the
    pod permanently out of the Service's endpoints for a recoverable problem.
    """
    _dspy_warm.set()


def is_dspy_warm() -> bool:
    return _dspy_warm.is_set()
