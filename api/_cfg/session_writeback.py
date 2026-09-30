"""Per-session writeback-ownership registry extracted from api/config.py.

Re-exported via ``api.config`` so ``from api.config import SESSION_WRITEBACK_OWNERS``
keeps working.  No external module should import from
``api._cfg.session_writeback`` directly.

The dict and its lock are defined HERE and re-exported through ``api.config``
-- object identity is preserved so ``config.SESSION_WRITEBACK_OWNERS.clear()``
in tests clears the canonical store.
"""

from __future__ import annotations

import threading

# ── Per-session writeback-ownership registry (#6623 re-gate) ────────────────
# Maps session_id -> stream_id of the turn that currently owns the session's
# writeback. Written whenever a turn is admitted (route layer, next to
# session.active_stream_id), REPLACED when a successor turn is admitted, and
# NEVER cleared by cancel_stream() -- cancel eagerly pops STREAMS/ACTIVE_RUNS
# and clears ``active_stream_id``, so a delayed finalizer from an old worker
# cannot tell "the session advanced to a successor" apart from "cancel simply
# cleared the field" by looking at its own (possibly LRU-evicted, detached)
# snapshot. This record survives cancel cleanup: the owning worker's own
# finally clears the entry, and only while it still owns it.
SESSION_WRITEBACK_OWNERS: dict = {}
SESSION_WRITEBACK_OWNERS_LOCK = threading.Lock()


def register_session_writeback_owner(session_id: str, stream_id: str) -> None:
    """Record the stream that currently owns a session's writeback."""
    session_id = str(session_id or "").strip()
    stream_id = str(stream_id or "").strip()
    if not session_id or not stream_id:
        return
    with SESSION_WRITEBACK_OWNERS_LOCK:
        SESSION_WRITEBACK_OWNERS[session_id] = stream_id


def session_writeback_owner(session_id: str) -> str | None:
    """Return the stream that currently owns the session's writeback, if any."""
    session_id = str(session_id or "").strip()
    if not session_id:
        return None
    with SESSION_WRITEBACK_OWNERS_LOCK:
        owner = SESSION_WRITEBACK_OWNERS.get(session_id)
    owner = str(owner or "").strip()
    return owner or None


def clear_session_writeback_owner_if_owned(session_id: str, stream_id: str) -> None:
    """Forget the writeback-ownership entry only while ``stream_id`` still owns it."""
    session_id = str(session_id or "").strip()
    stream_id = str(stream_id or "").strip()
    if not session_id or not stream_id:
        return
    with SESSION_WRITEBACK_OWNERS_LOCK:
        if SESSION_WRITEBACK_OWNERS.get(session_id) == stream_id:
            SESSION_WRITEBACK_OWNERS.pop(session_id, None)
