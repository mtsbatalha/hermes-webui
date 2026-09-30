"""Per-session agent locks extracted from api/config.py.

Re-exported via ``api.config`` so ``from api.config import _get_session_agent_lock``
keeps working.  No external module should import from ``api._cfg.session_locks``
directly.

The dict ``SESSION_AGENT_LOCKS`` and its lock are defined HERE and re-exported
through ``api.config`` -- object identity is preserved so
``config.SESSION_AGENT_LOCKS.clear()`` in tests clears the canonical store.
"""

from __future__ import annotations

import threading
import weakref

# Weak values keep one lock for every overlapping holder/waiter without leaking
# one permanent registry entry per deleted session.  A caller's local reference
# keeps the lock alive for the whole critical section; once no operation can
# still use it, the registry entry disappears automatically.
SESSION_AGENT_LOCKS = weakref.WeakValueDictionary()
SESSION_AGENT_LOCKS_LOCK = threading.Lock()


def _get_session_agent_lock(session_id: str) -> threading.Lock:
    """Return the per-session Lock used to serialize all Session mutations.

    Lock lifecycle invariant:
      - A Lock is created lazily on first access. The weak registry retains it
        while any holder or waiter has a strong reference, then reclaims the
        entry automatically when no overlapping operation can still use it.
      - During context compression the agent may rotate session_id. The
        streaming thread atomically aliases both old and new IDs to the *same*
        Lock object under SESSION_AGENT_LOCKS_LOCK (see streaming.py's
        compression block). Keeping the old alias prevents a late old-ID caller
        from creating a second Lock while an earlier holder or waiter still
        exists. Both weak aliases disappear automatically after all strong
        references to the Lock are released.
      - Lock contract: hold for the in-memory mutation + s.save() only; never
        across network I/O (LLM calls, HTTP requests).
    """
    with SESSION_AGENT_LOCKS_LOCK:
        lock = SESSION_AGENT_LOCKS.get(session_id)
        if lock is None:
            lock = threading.Lock()
            SESSION_AGENT_LOCKS[session_id] = lock
        return lock


def _alias_session_agent_lock(
    old_session_id: str,
    new_session_id: str,
    lock: threading.Lock,
) -> None:
    """Alias a compression continuation to the same live mutation lock.

    Keep the old ID alias while any holder or waiter still references ``lock``.
    Because the registry values are weak, both aliases disappear automatically
    once no overlapping operation can use the pre-compression lock. Removing the
    old alias eagerly would let a late old-ID request create a second lock.
    """
    with SESSION_AGENT_LOCKS_LOCK:
        SESSION_AGENT_LOCKS[old_session_id] = lock
        SESSION_AGENT_LOCKS[new_session_id] = lock
