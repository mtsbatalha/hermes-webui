"""Stream / stream-owner / SSE-buffer registries extracted from api/config.py.

Re-exported via ``api.config`` so ``from api.config import STREAMS`` keeps
working.  No external module should import from ``api._cfg.stream_registry``
directly.

The dicts and their locks are defined HERE and re-exported through
``api.config`` -- object identity is preserved so ``config.STREAMS.clear()``
in tests clears the canonical store.  Functions are self-contained and have
no config-cycle dependencies.
"""

from __future__ import annotations

import threading

STREAMS: dict = {}
STREAMS_LOCK = threading.Lock()


def peek_stream(stream_id):
    """Lock-disciplined stream queue lookup.

    Writers mutate STREAMS under STREAMS_LOCK (teardown in api/streaming.py,
    the route layer's start/cancel paths); reads must take the same lock so a
    read racing a teardown pop can never observe-and-use a queue the registry
    has already released. Returns the queue or None -- callers keep their
    existing None-guard fallbacks.
    """
    with STREAMS_LOCK:
        return STREAMS.get(stream_id)


# stream_id -> session_id owner, populated synchronously before worker startup so
# stream-id authorization does not depend on worker lifecycle registration.
STREAM_SESSION_OWNERS: dict = {}
STREAM_SESSION_OWNERS_LOCK = threading.Lock()
CANCEL_FLAGS: dict = {}
AGENT_INSTANCES: dict = {}  # stream_id -> AIAgent instance for interrupt propagation
STREAM_PARTIAL_TEXT: dict = {}  # stream_id -> partial assistant text accumulated during streaming
STREAM_REASONING_TEXT: dict = {}  # stream_id -> reasoning trace accumulated during streaming (#1361 §A)
STREAM_LIVE_TOOL_CALLS: dict = {}  # stream_id -> live tool calls accumulated during streaming (#1361 §B)
STREAM_GOAL_RELATED: dict = {}  # stream_id -> bool: only evaluate goal for goal-related turns (#1932)
STREAM_LAST_EVENT_ID: dict = {}  # stream_id -> latest journal event_id for `id:` field on live SSE frames (stage-364)
PENDING_GOAL_CONTINUATION: set = set()  # session_ids awaiting a goal continuation turn (#1932)


def register_stream_owner(stream_id: str, session_id: str) -> None:
    """Record the session that owns a stream before worker startup."""
    stream_id = str(stream_id or "").strip()
    session_id = str(session_id or "").strip()
    if not stream_id or not session_id:
        return
    with STREAM_SESSION_OWNERS_LOCK:
        STREAM_SESSION_OWNERS[stream_id] = session_id


def stream_owner_session_id(stream_id: str) -> str | None:
    """Return the synchronously-recorded owner session for a stream, if any."""
    stream_id = str(stream_id or "").strip()
    if not stream_id:
        return None
    with STREAM_SESSION_OWNERS_LOCK:
        owner = STREAM_SESSION_OWNERS.get(stream_id)
    owner = str(owner or "").strip()
    return owner or None


def unregister_stream_owner(stream_id: str) -> None:
    """Forget the pre-worker stream owner once the stream has torn down."""
    stream_id = str(stream_id or "").strip()
    if not stream_id:
        return
    with STREAM_SESSION_OWNERS_LOCK:
        STREAM_SESSION_OWNERS.pop(stream_id, None)
