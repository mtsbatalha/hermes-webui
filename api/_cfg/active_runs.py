"""Active-run and agent-cache registries extracted from api/config.py.

Re-exported via ``api.config`` so ``from api.config import ACTIVE_RUNS`` keeps
working.  No external module should import from ``api._cfg.active_runs``
directly.

The dicts and their locks are defined HERE and re-exported through
``api.config`` -- object identity is preserved so
``config.ACTIVE_RUNS.clear()`` in tests clears the canonical store.

``_evict_session_agent`` lives here because it reads ACTIVE_RUNS; it lazily
imports ``api.session_lifecycle`` to avoid a cycle with ``api.config`` (which
imports this module).  ``collections`` is imported locally for the OrderedDict.
"""

from __future__ import annotations

import collections
import logging
import threading
import time

logger = logging.getLogger("api.config")

from api._cfg.env import _env_int
from api._cfg.stream_registry import unregister_stream_owner

# Active agent-run registry. This intentionally tracks worker lifecycle rather
# than SSE lifecycle: cancel/reconnect may remove STREAMS while the worker is
# still unwinding, blocked in a provider call, or waiting for delegated work.
ACTIVE_RUNS: dict = {}
ACTIVE_RUNS_LOCK = threading.Lock()
LAST_RUN_FINISHED_AT: float | None = None
SERVER_START_TIME = time.time()


def active_run_is_attachable(run_entry) -> bool:
    """Return whether a run row still represents renderable live work.

    ``ACTIVE_RUNS`` tracks WORKER LIFECYCLE, which is deliberately broader than
    "a turn a browser may attach to": ``cancel_stream()`` leaves the row in
    ``phase="cancelling"`` while the worker unwinds so a successor cannot start
    on top of it. That row is already terminal from the client's perspective --
    its run journal ends in a terminal event -- so recovery paths that hand a
    stream id to the renderer must exclude it. Otherwise every fresh
    ``/api/session/stream`` subscription replays ``server_turn_started`` for a
    cancelled run, the client attaches, consumes the terminal event, tears the
    renderer down and resubscribes, and the loop repeats indefinitely.

    Non-dict entries stay attachable so callers that store an opaque marker are
    unaffected; production registrations are dicts carrying ``phase``.
    """
    return not (
        isinstance(run_entry, dict)
        and str(run_entry.get("phase") or "").strip() == "cancelling"
    )


def active_run_cancel_is_stale(
    run_entry,
    *,
    grace_seconds: float,
    now: float | None = None,
) -> bool:
    """Return whether a cancelling worker outlived its bounded unwind window.

    The age anchor is ``cancelled_at`` rather than the original ``started_at``
    so a long-running turn that was just cancelled is never mistaken for an
    orphan; ``started_at`` remains the fallback for rows created before the
    cancellation timestamp existed. Callers own the grace window because the
    tolerated unwind differs per surface.
    """
    if not isinstance(run_entry, dict):
        return False
    if str(run_entry.get("phase") or "").strip() != "cancelling":
        return False
    anchor = run_entry.get("cancelled_at") or run_entry.get("started_at")
    if not anchor:
        return False
    try:
        age = (time.time() if now is None else float(now)) - float(anchor)
        return age >= float(grace_seconds)
    except (TypeError, ValueError):
        return False


def register_active_run(stream_id: str, **metadata) -> None:
    """Mark a WebUI agent worker as alive until its outer finally exits."""
    if not stream_id:
        return
    now = time.time()
    entry = dict(metadata or {})
    entry.setdefault("stream_id", stream_id)
    entry.setdefault("started_at", now)
    entry.setdefault("phase", "running")
    with ACTIVE_RUNS_LOCK:
        ACTIVE_RUNS[stream_id] = entry


def update_active_run(stream_id: str, **metadata) -> None:
    """Update active-run metadata without creating a new run implicitly."""
    if not stream_id:
        return
    with ACTIVE_RUNS_LOCK:
        entry = ACTIVE_RUNS.get(stream_id)
        if entry is not None:
            entry.update(metadata)


def unregister_active_run(stream_id: str) -> None:
    """Remove a worker from the active-run registry and record idle start."""
    if not stream_id:
        return
    global LAST_RUN_FINISHED_AT
    with ACTIVE_RUNS_LOCK:
        ACTIVE_RUNS.pop(stream_id, None)
        LAST_RUN_FINISHED_AT = time.time()
    unregister_stream_owner(stream_id)


# Agent cache: reuse AIAgent across messages in the same WebUI session so that
# _user_turn_count survives between turns.  This mirrors the gateway's
# _agent_cache pattern and is required for injectionFrequency: "first-turn".
# LRU cache with size limit to prevent memory bloat.
# All cache operations (get, set, move_to_end, popitem) are protected by
# SESSION_AGENT_CACHE_LOCK for thread safety in multi-threaded ASGI servers.
SESSION_AGENT_CACHE: collections.OrderedDict = collections.OrderedDict()  # LRU cache
# Each cached agent pins a full conversation transcript in RAM, so this cap is
# the dominant lever on WebUI resident memory (issue #3506). The default is kept
# deliberately modest -- large/long sessions can each weigh tens of MB, so 50
# live agents could pin >1 GB on a heavily multiplexed install. Operators can
# tune it via HERMES_WEBUI_AGENT_CACHE_MAX without editing source.
SESSION_AGENT_CACHE_MAX = _env_int("HERMES_WEBUI_AGENT_CACHE_MAX", 25)
SESSION_AGENT_CACHE_LOCK = threading.Lock()


def _evict_session_agent(session_id: str) -> None:
    """Remove a cached agent for a session (on delete, clear, or model switch).

    Attempts a lifecycle commit before dropping the agent handle so that
    batch-extraction memory providers can extract any pending work.  If the
    commit fails or there is uncommitted work with no successful commit, the
    lifecycle entry is preserved (not unregistered) so a future commit can
    retry.
    """
    agent = None
    with SESSION_AGENT_CACHE_LOCK:
        entry = SESSION_AGENT_CACHE.pop(session_id, None)
        if entry is not None:
            agent = entry[0] if isinstance(entry, tuple) else None
    if agent is None:
        return
    # A live run for this session may still hold this agent's _session_db (the
    # worker assigns agent._session_db at run start). Never close it out from
    # under an in-flight turn -- ACTIVE_RUNS is the authoritative liveness signal
    # (mirrors the worker's own LRU-eviction guard in streaming.py). When a run
    # is live we still drop the cache handle above (harmless -- the worker holds
    # a local ref), but skip the lifecycle commit + _session_db.close() so the
    # running turn can finish persisting. Hardens /clear + model-switch eviction
    # too, not just truncate (#5096 Bug D).
    _run_active = False
    try:
        with ACTIVE_RUNS_LOCK:
            for _entry in (ACTIVE_RUNS or {}).values():
                if (_entry or {}).get("session_id") == session_id:
                    _run_active = True
                    break
    except Exception:
        _run_active = False
    if _run_active:
        return
    should_close = True
    try:
        from api.session_lifecycle import commit_session_memory, discard_session, has_uncommitted_work, unregister_agent
        if has_uncommitted_work(session_id):
            commit_session_memory(session_id, agent=agent, wait=True)
        if not has_uncommitted_work(session_id):
            unregister_agent(session_id)
            # Bound the lifecycle dict: drop the entry now that the session has
            # no uncommitted work and the agent handle is gone (issue #3506).
            discard_session(session_id)
        else:
            should_close = False
    except Exception:
        should_close = False
        logger.debug("Lifecycle commit on eviction failed for %s", session_id, exc_info=True)
    if should_close and getattr(agent, '_session_db', None) is not None:
        try:
            agent._session_db.close()
        except Exception:
            logger.debug("Failed to close _session_db on eviction for %s", session_id, exc_info=True)
