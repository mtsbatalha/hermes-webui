"""Disk-cache IO helpers for /api/models extracted from api/config.py.

Re-exported via ``api.config`` so ``from api.config import _load_models_cache_from_disk``
keeps working.  No external module should import from ``api._cfg.models_cache_io`` directly.
"""

from __future__ import annotations

import copy
import json
import logging
import os
from pathlib import Path

import api.paths as _paths
from api._cfg.state import _DEFAULT_HERMES_HOME

logger = logging.getLogger(__name__)

# Lazy helpers for monkeypatch-aware delegation (tests patch api.config).

def _get_models_cache_path(*args, **kwargs):
    try:
        import api.config as _ac
        fn = getattr(_ac, "_get_models_cache_path", None)
        if callable(fn) and fn is not _get_models_cache_path:
            return fn(*args, **kwargs)
    except Exception:
        pass
    try:
        from api._cfg.models_cache import _get_models_cache_path as _real
        return _real(*args, **kwargs)
    except Exception:
        pass
    try:
        from api.profiles import get_active_hermes_home as _gah
        return _gah() / "models_cache.json"
    except Exception:
        return _DEFAULT_HERMES_HOME / "models_cache.json"

def _is_loadable_disk_cache(cache):
    try:
        import api.config as _ac2
        fn = getattr(_ac2, "_is_loadable_disk_cache", None)
        if callable(fn) and fn is not _is_loadable_disk_cache:
            return bool(fn(cache))
    except Exception:
        pass
    try:
        from api._cfg.models_cache import _is_loadable_disk_cache as _real2
        return bool(_real2(cache))
    except Exception:
        pass
    return False

def _is_valid_models_cache(cache):
    try:
        import api.config as _ac3
        fn = getattr(_ac3, "_is_valid_models_cache", None)
        if callable(fn) and fn is not _is_valid_models_cache:
            return bool(fn(cache))
    except Exception:
        pass
    try:
        from api._cfg.models_cache import _is_valid_models_cache as _real3
        return bool(_real3(cache))
    except Exception:
        pass
    return False

def _models_cache_source_fingerprint(*args, **kwargs):
    try:
        import api.config as _ac4
        fn = getattr(_ac4, "_models_cache_source_fingerprint", None)
        if callable(fn) and fn is not _models_cache_source_fingerprint:
            return fn(*args, **kwargs)
    except Exception:
        pass
    try:
        from api._cfg.models_cache import _models_cache_source_fingerprint as _real4
        return _real4(*args, **kwargs)
    except Exception:
        pass
    return {}

def _current_webui_version(*args, **kwargs):
    try:
        import api.config as _ac5
        fn = getattr(_ac5, "_current_webui_version", None)
        if callable(fn) and fn is not _current_webui_version:
            return fn(*args, **kwargs)
    except Exception:
        pass
    try:
        from api._cfg.models_cache import _current_webui_version as _real5
        return _real5(*args, **kwargs)
    except Exception:
        pass
    return None

def _annotate_fast_tier_model_groups(groups):
    try:
        import api.config as _ac6
        fn = getattr(_ac6, "_annotate_fast_tier_model_groups", None)
        if callable(fn) and fn is not _annotate_fast_tier_model_groups:
            return fn(groups)
    except Exception:
        pass
    try:
        from api._cfg.advanced import _annotate_fast_tier_model_groups as _real6
        return _real6(groups)
    except Exception:
        pass
    return groups

def _cfg_dict():
    try:
        import api.config as _ac7
        fn = getattr(_ac7, "get_config", None)
        if callable(fn):
            return fn() or {}
        v = getattr(_ac7, "cfg", None)
        if isinstance(v, dict):
            return v
    except Exception:
        pass
    return {}

_MODELS_CACHE_SCHEMA_VERSION = 3
try:
    from api._cfg.models_cache import _MODELS_CACHE_SCHEMA_VERSION as _MCSV
    _MODELS_CACHE_SCHEMA_VERSION = _MCSV
except Exception:
    pass

def _load_models_cache_from_disk() -> dict | None:
    """Load /api/models cache from disk if it exists and has current metadata.

    Adds the per-release version check from #1633: a cache stamped with a
    different WebUI version is treated as missing, forcing a fresh rebuild
    that picks up any picker-shape fixes shipped in the new release. The
    returned dict is the SHAPE-only cache (without the `_webui_version` /
    `_schema_version` stamps) so callers don't have to know about the
    on-disk metadata fields.
    """
    try:
        import json as _j

        cache_path = _get_models_cache_path()
        if not cache_path.exists():
            return None
        with open(cache_path, encoding="utf-8") as f:
            cache = _j.load(f)
        if not _is_loadable_disk_cache(cache):
            return None
        # Strip the disk-only metadata before returning, so the in-memory
        # cache shape stays exactly what the rest of the code expects. The
        # disk save path does not persist `aliases`, so reconstruct them from
        # current config to keep the /api/models.aliases contract intact (a
        # disk-cache hit must not silently drop `/model <alias>` resolution).
        return _annotate_fast_tier_model_groups({
            "active_provider": cache["active_provider"],
            "default_model": cache["default_model"],
            "configured_model_badges": cache["configured_model_badges"],
            "groups": cache["groups"],
            "aliases": (
                cache["aliases"]
                if isinstance(cache.get("aliases"), dict)
                else _model_aliases_from_config()
            ),
        })
    except Exception:
        return None


def _model_aliases_from_config() -> dict[str, str]:
    """Build the normalized model-alias map from current config.

    Mirrors the alias construction used by the live and static catalog paths so
    the `/api/models.aliases` contract is consistent across every catalog source
    (live, static, and the stale-disk fallback, which can't read aliases from a
    disk cache that never persisted them).
    """
    try:
        raw_aliases = _cfg_dict().get("model", {}).get("aliases", {})
        if isinstance(raw_aliases, dict):
            return {
                str(k).strip(): str(v).strip()
                for k, v in raw_aliases.items()
                if k and v
            }
    except Exception:
        pass
    return {}


def _load_stale_models_cache_from_disk() -> dict | None:
    """Load a shape-valid stale /api/models disk cache for timeout fallback only.

    The main cache loader enforces metadata stamps for a full cold-path cache hit.
    This helper intentionally does not apply that stricter policy, so we can still
    recover a useful fallback payload when the strict loader rejected cache because
    the WebUI version stamp is stale. It DOES still enforce the schema version (a
    cross-schema cache can have an incompatible groups/badge shape) and the source
    fingerprint: a snapshot built from other config/auth/.env/plugin sources is a
    wrong catalog, not merely an old one, so it is never served.
    """
    try:
        import json as _j

        cache_path = _get_models_cache_path()
        if not cache_path.exists():
            return None
        with open(cache_path, encoding="utf-8") as f:
            cache = _j.load(f)
        if not _is_valid_models_cache(cache):
            return None
        if cache.get("_schema_version") != _MODELS_CACHE_SCHEMA_VERSION:
            return None
        if cache.get("_source_fingerprint") != _models_cache_source_fingerprint():
            return None
        aliases = cache.get("aliases")
        if not isinstance(aliases, dict):
            # The disk cache save path does not persist `aliases`, so a cache
            # read back from disk lacks them. Defaulting to {} would silently
            # break `/model <alias>` slash-command resolution (static/commands.js
            # resolves slash aliases only from /api/models.aliases) for the
            # duration of the over-budget stale fallback. Reconstruct from
            # current config, mirroring the live/static catalog alias build.
            aliases = _model_aliases_from_config()
        return _annotate_fast_tier_model_groups({
            "active_provider": cache["active_provider"],
            "default_model": cache["default_model"],
            "configured_model_badges": cache["configured_model_badges"],
            "groups": cache["groups"],
            "aliases": aliases,
        })
    except Exception:
        return None


def _save_models_cache_to_disk(cache: dict) -> None:
    """Save cache to disk so it survives server restarts.

    Stamps the payload with `_webui_version` and `_schema_version` (#1633) so
    a subsequent process running a different WebUI version, or a future
    release that bumps the schema, will treat the file as invalid and
    rebuild from live provider data on its first /api/models call.

    The version stamp is omitted (not the literal None — the field is just
    skipped) when the runtime version cannot be resolved at the moment of
    save, which would happen only in a very early boot path before
    api.updates is loaded. _is_loadable_disk_cache treats a missing field as
    a mismatch (since runtime_version is non-None on every subsequent call),
    so this is safe — at worst we write one cache file that gets rejected
    once on the next boot.
    """
    try:
        if not _is_valid_models_cache(cache):
            return
        payload = {
            "_schema_version": _MODELS_CACHE_SCHEMA_VERSION,
            "_source_fingerprint": _models_cache_source_fingerprint(),
            "active_provider": cache["active_provider"],
            "default_model": cache["default_model"],
            "configured_model_badges": cache["configured_model_badges"],
            "groups": cache["groups"],
        }
        runtime_version = _current_webui_version()
        if runtime_version is not None:
            payload["_webui_version"] = runtime_version
        cache_path = _get_models_cache_path()
        tmp = str(cache_path) + f".{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        os.replace(tmp, str(cache_path))
    except Exception:
        try:
            os.unlink(tmp)  # type: ignore[name-defined]
        except Exception:
            pass
        pass  # Non-fatal -- cache will rebuild on next call


