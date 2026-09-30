"""Session-visit models helpers extracted from api/config.py.

Re-exported via ``api.config`` so ``from api.config import get_available_models_for_session_visit`` keeps working.
No external module should import from ``api._cfg.models_visit`` directly.
"""

from __future__ import annotations

import copy
import logging
import os
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Lazy helpers (monkeypatch-aware) ──────────────────────────────────────────


def _ac():  # type: ignore[no-redef]
    import api.config as _cfg  # noqa: WPS433

    return _cfg


def _get_models_cache_path(*a, **kw):  # type: ignore[no-untyped-def]
    try:
        fn = getattr(_ac(), "_get_models_cache_path", None)
        if callable(fn) and fn.__name__ != "_get_models_cache_path":
            return fn(*a, **kw)
    except Exception:
        pass
    try:
        from api._cfg.models_cache import _get_models_cache_path as _real

        return _real(*a, **kw)
    except Exception:
        pass
    try:
        from api.profiles import get_active_hermes_home as _gah

        return _gah() / "models_cache.json"
    except Exception:
        from api._cfg.state import _DEFAULT_HERMES_HOME

        return _DEFAULT_HERMES_HOME / "models_cache.json"


def _load_models_cache_from_disk(*a, **kw):  # type: ignore[no-untyped-def]
    try:
        fn = getattr(_ac(), "_load_models_cache_from_disk", None)
        if callable(fn) and fn.__name__ != "_load_models_cache_from_disk":
            return fn(*a, **kw)
    except Exception:
        pass
    try:
        from api._cfg.models_cache_io import _load_models_cache_from_disk as _real

        return _real(*a, **kw)
    except Exception:
        return None


def _load_stale_models_cache_from_disk(*a, **kw):  # type: ignore[no-untyped-def]
    try:
        fn = getattr(_ac(), "_load_stale_models_cache_from_disk", None)
        if callable(fn) and fn.__name__ != "_load_stale_models_cache_from_disk":
            return fn(*a, **kw)
    except Exception:
        pass
    try:
        from api._cfg.models_cache_io import _load_stale_models_cache_from_disk as _real

        return _real(*a, **kw)
    except Exception:
        return None


def _get_fresh_memory_models_cache(*a, **kw):  # type: ignore[no-untyped-def]
    try:
        fn = getattr(_ac(), "_get_fresh_memory_models_cache", None)
        if callable(fn):
            return fn(*a, **kw)
    except Exception:
        pass
    try:
        from api._cfg.models_cache_runtime import _get_fresh_memory_models_cache as _real

        return _real(*a, **kw)
    except Exception:
        return None


def _sync_models_cache_provenance(*a, **kw):  # type: ignore[no-untyped-def]
    try:
        fn = getattr(_ac(), "_sync_models_cache_provenance", None)
        if callable(fn):
            return fn(*a, **kw)
    except Exception:
        pass
    try:
        from api._cfg.models_cache_runtime import _sync_models_cache_provenance as _real

        return _real(*a, **kw)
    except Exception:
        return None


def _models_cache_source_fingerprint(*a, **kw):  # type: ignore[no-untyped-def]
    try:
        fn = getattr(_ac(), "_models_cache_source_fingerprint", None)
        if callable(fn) and fn.__name__ != "_models_cache_source_fingerprint":
            return fn(*a, **kw)
    except Exception:
        pass
    try:
        from api._cfg.models_cache import _models_cache_source_fingerprint as _real

        return _real(*a, **kw)
    except Exception:
        return {}


def get_available_models(*a, **kw):  # type: ignore[no-untyped-def]
    try:
        fn = getattr(_ac(), "get_available_models", None)
        if callable(fn) and fn.__name__ != "get_available_models":
            return fn(*a, **kw)
    except Exception:
        pass
    try:
        from api._cfg.static_catalog import _minimal_static_models_catalog as _real

        return _real()
    except Exception:
        return {
            "active_provider": None,
            "default_model": "",
            "configured_model_badges": {},
            "groups": [],
            "aliases": {},
        }


# ── Extracted helpers ─────────────────────────────────────────────────────────


def _models_cache_file_age_seconds(cache_path: Path, now: float) -> float | None:
    try:
        return max(0.0, now - cache_path.stat().st_mtime)
    except OSError:
        return None


def warm_models_catalog_provenance_if_cold() -> None:
    """Best-effort, NON-BLOCKING, disk-only publish of catalog provenance."""
    _cfg = _ac()

    def _provenance_is_current() -> bool:
        prov = _cfg._models_cache_provenance  # type: ignore[attr-defined]
        if prov is None:
            return False
        try:
            return prov[1] == _cfg._models_cache_source_fingerprint()  # type: ignore[attr-defined]
        except Exception:
            return False

    if _provenance_is_current():
        return
    got = _cfg._available_models_cache_lock.acquire(blocking=False)  # type: ignore[attr-defined]
    if not got:
        return
    try:
        if _provenance_is_current():
            return
        try:
            disk_groups = _cfg._load_models_cache_from_disk()  # type: ignore[attr-defined]
        except Exception:
            disk_groups = None
        if disk_groups is None:
            return
        _cfg._available_models_cache = disk_groups  # type: ignore[attr-defined]
        _cfg._available_models_cache_ts = time.monotonic()  # type: ignore[attr-defined]
        try:
            _cfg._available_models_cache_source_fingerprint = _cfg._models_cache_source_fingerprint()  # type: ignore[attr-defined]
        except Exception:
            _cfg._available_models_cache_source_fingerprint = None  # type: ignore[attr-defined]
        _cfg._sync_models_cache_provenance()  # type: ignore[attr-defined]
    except Exception:
        logger.debug("models catalog provenance warm failed", exc_info=True)
    finally:
        _cfg._available_models_cache_lock.release()  # type: ignore[attr-defined]


def get_available_models_for_session_visit() -> dict:
    """Return /api/models with a short session-visit freshness horizon."""
    import time as _time
    import logging as _logging

    _cfg = _ac()
    _stagelog: list[tuple[str, float]] = [("enter", _time.monotonic())]

    def _mark(name: str) -> None:
        _stagelog.append((name, _time.monotonic()))

    _logger = _logging.getLogger("api.config")
    _slow_raw = (os.environ.get("HERMES_DEBUG_SLOW", "") or "").strip()
    if not _slow_raw:
        _slow_threshold_ms = 500.0
    else:
        try:
            _slow_threshold_ms = float(_slow_raw) or 500.0
        except ValueError:
            _slow_threshold_ms = 0.0

    cache_path = _cfg._get_models_cache_path()  # type: ignore[attr-defined]
    cache_age = _models_cache_file_age_seconds(cache_path, time.time())
    _mark(f"disk_age_check:{cache_age}")
    disk_cached = None
    if cache_age is not None and cache_age < _cfg._SESSION_VISIT_MODELS_FRESHNESS_SECONDS:  # type: ignore[attr-defined]
        _mark("cache_age_within_ttl")
        now_mono = time.monotonic()
        with _cfg._available_models_cache_lock:  # type: ignore[attr-defined]
            cached = _cfg._get_fresh_memory_models_cache(now_mono)  # type: ignore[attr-defined]
            if cached is not None:
                _mark("memory_cache_hit")
                _maybe_log_slow_stages(_logger, _stagelog, _slow_threshold_ms, "models.session_visit")
                return cached
        _mark("memory_cache_miss_loading_disk")
        disk_cached = _cfg._load_models_cache_from_disk()  # type: ignore[attr-defined]
        if disk_cached is not None:
            with _cfg._available_models_cache_lock:  # type: ignore[attr-defined]
                cached = _cfg._get_fresh_memory_models_cache(time.monotonic())  # type: ignore[attr-defined]
                if cached is not None:
                    _mark("disk_then_memory_cache_hit")
                    _maybe_log_slow_stages(_logger, _stagelog, _slow_threshold_ms, "models.session_visit")
                    return cached
                _cfg._available_models_cache = copy.deepcopy(disk_cached)  # type: ignore[attr-defined]
                _cfg._available_models_cache_ts = time.monotonic()  # type: ignore[attr-defined]
                _cfg._available_models_cache_source_fingerprint = _cfg._models_cache_source_fingerprint()  # type: ignore[attr-defined]
                _cfg._sync_models_cache_provenance()  # type: ignore[attr-defined]
            _mark("disk_cache_returned")
            _maybe_log_slow_stages(_logger, _stagelog, _slow_threshold_ms, "models.session_visit")
            return copy.deepcopy(disk_cached)

    _mark("cache_age_stale_or_missing")
    stale_cached = disk_cached or _cfg._load_stale_models_cache_from_disk()  # type: ignore[attr-defined]
    _mark(f"stale_cached_loaded:{bool(stale_cached)}")
    try:
        _mark("force_refresh_start")
        result = _cfg.get_available_models(force_refresh=True)  # type: ignore[attr-defined]
        _mark("force_refresh_done")
        _maybe_log_slow_stages(_logger, _stagelog, _slow_threshold_ms, "models.session_visit")
        return result
    except Exception:
        _mark("force_refresh_failed")
        logger.debug("session-visit models refresh failed", exc_info=True)
        if stale_cached is not None:
            _mark("stale_fallback_return")
            _maybe_log_slow_stages(_logger, _stagelog, _slow_threshold_ms, "models.session_visit")
            return copy.deepcopy(stale_cached)
        _mark("prefer_cache_fallback")
        _maybe_log_slow_stages(_logger, _stagelog, _slow_threshold_ms, "models.session_visit")
        return _cfg.get_available_models(prefer_cache=True)  # type: ignore[attr-defined]


def _maybe_log_slow_stages(
    logger_obj: "logging.Logger",
    stagelog: "list[tuple[str, float]]",
    threshold_ms: float,
    tag: str,
) -> None:
    """perf(session-load-latency) Phase 0: per-stage timing reporter."""
    if len(stagelog) < 2:
        return
    total_ms = (stagelog[-1][1] - stagelog[0][1]) * 1000.0
    if total_ms < threshold_ms:
        return
    parts: list[str] = []
    for i in range(1, len(stagelog)):
        prev_t = stagelog[i - 1][1]
        cur_t = stagelog[i][1]
        parts.append(f"{stagelog[i][0]}={((cur_t - prev_t) * 1000.0):.1f}ms")
    try:
        logger_obj.warning(
            "[SLOW] %s total=%.1fms stages: %s",
            tag,
            total_ms,
            " ".join(parts),
        )
    except Exception:
        pass
