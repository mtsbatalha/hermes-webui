"""YAML memoization + config save helpers extracted from api/config.py.

Re-exported via ``api.config`` so ``from api.config import _yaml_file_cache``
keeps working.  No external module should import from ``api._cfg.yaml_cache``
directly.

The dict ``_yaml_file_cache`` and its lock are defined HERE and re-exported
through ``api.config`` -- the object identity is preserved so
``config._yaml_file_cache.clear()`` in tests clears the canonical store.
"""

from __future__ import annotations

import copy
import logging
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

_yaml_file_cache: dict[str, tuple] = {}
_yaml_file_cache_lock = threading.Lock()


def _load_yaml_config_file_raw(config_path: Path, *, _copy: bool = True) -> dict:
    """Return the RAW (un-env-expanded) parsed config dict, memoized on
    (resolved path, st_mtime_ns, st_size). Shared parse core for
    _load_yaml_config_file() and reload_config(): the former runs the helper's
    own per-call env expansion on the result; the latter must run expansion
    under its own process-env-pinned thread context (#798), so it takes the raw
    dict and expands it itself. Either way the file is parsed at most once per
    (mtime, size) -- a UI sync storm can't turn into a YAML-reparse storm (#4650),
    and an unchanged config.yaml isn't reparsed on the profile-switch hot path
    (#4662 Phase 2).

    By default returns a deep copy so a caller can never mutate the shared cache
    entry (greptile #4741). Internal callers that immediately pass the result
    through _expand_env_vars() (which itself returns a fresh structure and never
    mutates its input) pass _copy=False to skip the redundant copy on the hot path.
    """
    try:
        from api import yaml_compat as _yaml
    except ImportError:
        return {}

    try:
        st = config_path.stat()
    except OSError:
        # Missing or unstattable file -- preserve the original "no config" contract.
        return {}

    cache_key = str(config_path)
    stat_key = (st.st_mtime_ns, st.st_size)
    with _yaml_file_cache_lock:
        cached = _yaml_file_cache.get(cache_key)
        if cached is not None and cached[0] == stat_key:
            raw = cached[1]
            if not isinstance(raw, dict):
                return {}
            return copy.deepcopy(raw) if _copy else raw

    # Cache miss / stale: parse off disk. Done outside the lock so a slow parse
    # doesn't serialize unrelated paths; a concurrent duplicate parse is harmless.
    try:
        loaded = _yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except Exception:
        logger.debug("Failed to parse yaml config from %s", config_path)
        return {}

    raw = loaded if isinstance(loaded, dict) else {}
    with _yaml_file_cache_lock:
        _yaml_file_cache[cache_key] = (stat_key, raw)
    return copy.deepcopy(raw) if _copy else raw


def _load_yaml_config_file(config_path: Path) -> dict:
    import api.config as _ac

    # _copy=False: _expand_env_vars returns a fresh structure and never mutates
    # its input, so the env-expanded result is already cache-safe -- no need to
    # deep-copy the raw dict first (keeps the /api/reasoning hot path cheap).
    raw = _load_yaml_config_file_raw(config_path, _copy=False)
    if not raw:
        return {}
    expanded = _ac._expand_env_vars(raw)
    return expanded if isinstance(expanded, dict) else {}


def _config_for_yaml_save(config_data: dict) -> dict:
    import api.config as _ac

    if not isinstance(config_data, dict):
        return {}
    data = copy.deepcopy(config_data)
    agent_cfg = data.get("agent")
    if isinstance(agent_cfg, dict):
        personalities = agent_cfg.get("personalities")
        if isinstance(personalities, dict):
            custom_personalities = {
                name: value
                for name, value in personalities.items()
                if _ac._DEFAULT_AGENT_PERSONALITIES.get(name) != value
            }
            if custom_personalities:
                agent_cfg["personalities"] = custom_personalities
            else:
                agent_cfg.pop("personalities", None)
        if not agent_cfg:
            data.pop("agent", None)
    return data


def _save_yaml_config_file(config_path: Path, config_data: dict) -> None:
    import api.paths as _paths

    try:
        from api import yaml_compat as _yaml
    except ImportError as exc:
        raise RuntimeError("PyYAML is required to write Hermes config.yaml") from exc

    config_path.parent.mkdir(parents=True, exist_ok=True)
    _paths._atomic_write_text(
        config_path,
        _yaml.safe_dump(_config_for_yaml_save(config_data), sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    # Invalidate the memoized parse for this path so the next read re-parses the
    # bytes we just wrote. mtime_ns+size keying normally catches edits, but a
    # WebUI save that preserves size with a coarse/unchanged mtime could otherwise
    # serve a stale dict (#4650 review) -- evicting on our own write closes that gap.
    with _yaml_file_cache_lock:
        _yaml_file_cache.pop(str(config_path), None)
