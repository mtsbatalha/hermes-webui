"""SESSIONS LRU + runtime diagnostics snapshot extracted from api/config.py.

Re-exported via ``api.config`` so ``from api.config import SESSIONS`` keeps
working.  No external module should import from ``api._cfg.runtime_diag``
directly.

``SESSIONS`` is the canonical OrderedDict; ``get_runtime_diagnostics_snapshot``
is lock-disciplined and reads only scalars so it never blocks.  All mutable
globals are resolved via ``api.config`` at call time (through the proxy) so
``monkeypatch.setattr(config, ...)`` remains coherent even though this module
holds the canonical storage.
"""

from __future__ import annotations

import collections
import time

SESSIONS: collections.OrderedDict = collections.OrderedDict()


def get_runtime_diagnostics_snapshot() -> dict[str, dict[str, object]]:
    """Return nonblocking scalar observations owned by the config module."""
    # Resolve via api.config so monkeypatched replacements are observed.
    import api.config as _ac

    result: dict[str, dict[str, object]] = {
        "sessions": {"available": False, "resident": 0, "cap": 0},
        "models_cache": {
            "available": False,
            "groups": 0,
            "models": 0,
            "age_seconds": None,
        },
    }
    try:
        lock = getattr(_ac, "LOCK", None)
        sessions = getattr(_ac, "SESSIONS", None)
        cap = getattr(_ac, "_LAST_APPLIED_SESSIONS_CACHE_MAX", 0)
        if lock is not None and sessions is not None and lock.acquire(blocking=False):
            try:
                result["sessions"] = {
                    "available": True,
                    "resident": max(0, int(len(sessions))),
                    "cap": max(0, int(cap)),
                }
            finally:
                lock.release()
    except Exception:
        pass
    try:
        cache_lock = getattr(_ac, "_available_models_cache_lock", None)
        if cache_lock is not None and cache_lock.acquire(blocking=False):
            try:
                snapshot = getattr(_ac, "_available_models_cache", None)
                groups = snapshot.get("groups") if isinstance(snapshot, dict) else None
                group_count = len(groups) if isinstance(groups, list) else 0
                model_count = 0
                if isinstance(groups, list):
                    for group in groups:
                        if isinstance(group, dict):
                            for bucket in ("models", "extra_models"):
                                models = group.get(bucket)
                                if isinstance(models, list):
                                    model_count += len(models)
                age = None
                ts = getattr(_ac, "_available_models_cache_ts", None)
                if snapshot is not None and ts:
                    age = max(0.0, time.monotonic() - float(ts))
                result["models_cache"] = {
                    "available": True,
                    "groups": max(0, int(group_count)),
                    "models": max(0, int(model_count)),
                    "age_seconds": age,
                }
            finally:
                cache_lock.release()
    except Exception:
        pass
    return result
