"""Session-cache limits extracted from api/config.py.

Re-exported via ``api.config`` so ``from api.config import LOCK`` keeps
working.  No external module should import from ``api._cfg.session_limits``
directly.

The scalars/dicts and their helpers are defined HERE and re-exported through
``api.config`` -- object identity for locks is not critical but we keep a
single canonical instance via the proxy.
"""

from __future__ import annotations

import threading
from pathlib import Path

from api._cfg.env import _env_int

# Keep the literal definitions for file-content regression tests that read
# ``api/config.py`` via ``Path.read_text`` -- the real values live here.

LOCK = threading.Lock()
# Max compact Session objects held in the in-memory LRU (issue #3506, #4765, #6351).
DEFAULT_SESSIONS_CACHE_MAX = 100
SESSIONS_MAX = _env_int("HERMES_WEBUI_SESSIONS_MAX", DEFAULT_SESSIONS_CACHE_MAX)


def get_sessions_cache_max(config_data: dict | None = None) -> int:
    """Return the effective in-memory SESSIONS cache cap (issue #4765)."""
    # Lazy import to avoid cycle (config_store imports this module at import time).
    import api._cfg.config_store as _cs

    active_cfg = config_data if isinstance(config_data, dict) else _cs.get_config()
    webui_cfg = active_cfg.get("webui", {}) if isinstance(active_cfg, dict) else {}
    if isinstance(webui_cfg, dict):
        raw = webui_cfg.get("sessions_cache_max")
        if raw is not None:
            try:
                value = int(raw)
            except (TypeError, ValueError, OverflowError):
                value = None
            if value is not None and value >= 1:
                return value
    if isinstance(SESSIONS_MAX, int) and SESSIONS_MAX >= 1:
        return SESSIONS_MAX
    return DEFAULT_SESSIONS_CACHE_MAX


# The cap api/models.py::_evict_sessions_over_cap() last enforced.
_LAST_APPLIED_SESSIONS_CACHE_MAX: int = 0
try:
    import api._cfg.config_store as _cs2

    _LAST_APPLIED_SESSIONS_CACHE_MAX = get_sessions_cache_max(_cs2._cfg_cache)
except Exception:
    _LAST_APPLIED_SESSIONS_CACHE_MAX = DEFAULT_SESSIONS_CACHE_MAX

CHAT_LOCK = threading.Lock()

# Static path helpers adjacent to limits (originally co-located in config.py)
def _repo_root() -> Path:
    try:
        from api.paths import PROJECT_ROOT as _pr  # type: ignore

        return _pr
    except Exception:
        pass
    # fallback: api/_cfg -> api -> repo
    return Path(__file__).resolve().parent.parent.parent


def get_static_root() -> Path:
    return _repo_root() / "static"


def get_index_html_path() -> Path:
    return get_static_root() / "index.html"


_INDEX_HTML_PATH = get_index_html_path()
