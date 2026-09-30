"""Config-cache + reload machinery extracted from api/config.py.

Re-exported via ``api.config`` so ``from api.config import _cfg_cache`` etc.
keep working.  No external module should import from ``api._cfg.config_store``
directly.

The dict ``_cfg_cache`` and its lock / mtime / path / fingerprint are defined
HERE and re-exported through ``api.config`` -- object identity is preserved
so ``config._cfg_cache is config.cfg`` and ``config._cfg_cache.clear()`` in
tests mutates the canonical store.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

_cfg_cache: dict = {}
_cfg_lock = threading.Lock()
_cfg_mtime: float = 0.0
_cfg_path: Path | None = None
_cfg_fingerprint: str | None = None

_WEBUI_SESSION_SAVE_MODES = {"deferred", "eager"}
_DEFAULT_WEBUI_SESSION_SAVE_MODE = "deferred"
_DEFAULT_EXPERIMENTAL_CONFIG = {
    "unified_session_db": False,
}
_DEFAULT_AGENT_PERSONALITIES = {
    "helpful": "You are a helpful, friendly AI assistant.",
    "concise": "You are a concise assistant. Keep responses brief and to the point.",
    "technical": "You are a technical expert. Provide detailed, accurate technical information.",
    "creative": "You are a creative assistant. Think outside the box and offer innovative solutions.",
    "teacher": "You are a patient teacher. Explain concepts clearly with examples.",
    "kawaii": "You are a kawaii assistant! Use cute expressions like (◕‿◕), ★, ♪, and ~! Add sparkles and be super enthusiastic about everything! Every response should feel warm and adorable desu~! ヽ(>∀<☆)ノ",
    "catgirl": "You are Neko-chan, an anime catgirl AI assistant, nya~! Add 'nya' and cat-like expressions to your speech. Use kaomoji like (=^･ω･^=) and ฅ^•ﻌ•^ฅ. Be playful and curious like a cat, nya~!",
    "pirate": "Arrr! Ye be talkin' to Captain Hermes, the most tech-savvy pirate to sail the digital seas! Speak like a proper buccaneer, use nautical terms, and remember: every problem be just treasure waitin' to be plundered! Yo ho ho!",
    "shakespeare": "Hark! Thou speakest with an assistant most versed in the bardic arts. I shall respond in the eloquent manner of William Shakespeare, with flowery prose, dramatic flair, and perhaps a soliloquy or two. What light through yonder terminal breaks?",
    "surfer": "Duuude! You're chatting with the chillest AI on the web, bro! Everything's gonna be totally rad. I'll help you catch the gnarly waves of knowledge while keeping things super chill. Cowabunga!",
    "noir": "The rain hammered against the terminal like regrets on a guilty conscience. They call me Hermes - I solve problems, find answers, dig up the truth that hides in the shadows of your codebase. In this city of silicon and secrets, everyone's got something to hide. What's your story, pal?",
    "uwu": "hewwo! i'm your fwiendwy assistant uwu~ i wiww twy my best to hewp you! *nuzzles your code* OwO what's this? wet me take a wook! i pwomise to be vewy hewpful >w<",
    "philosopher": "Greetings, seeker of wisdom. I am an assistant who contemplates the deeper meaning behind every query. Let us examine not just the 'how' but the 'why' of your questions. Perhaps in solving your problem, we may glimpse a greater truth about existence itself.",
    "hype": "YOOO LET'S GOOOO!!! I am SO PUMPED to help you today! Every question is AMAZING and we're gonna CRUSH IT together! This is gonna be LEGENDARY! ARE YOU READY?! LET'S DO THIS!",
}


def _fingerprint_config(data: dict) -> str:
    try:
        return json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)
    except Exception:
        return repr(data)


def _cfg_has_in_memory_overrides() -> bool:
    if _cfg_fingerprint is not None and _fingerprint_config(_cfg_cache) != _cfg_fingerprint:
        return True
    try:
        import api.config as _ac

        return _ac.cfg is not _cfg_cache
    except Exception:
        return False


def _get_config_path() -> Path:
    env_override = os.getenv("HERMES_CONFIG_PATH")
    if env_override:
        return Path(env_override).expanduser()
    try:
        from api.profiles import get_active_hermes_home

        return get_active_hermes_home() / "config.yaml"
    except ImportError:
        import api.config as _ac

        return _ac._DEFAULT_HERMES_HOME / "config.yaml"


def _effective_config_path() -> Path:
    """Return config path respecting a monkeypatched ``api.config._get_config_path``.

    Tests do ``monkeypatch.setattr(cfg, "_get_config_path", lambda: tmp)`` where
    ``cfg`` is ``api.config``. After the split the canonical implementation lives
    here, but ``api.config`` re-exports it. A monkeypatch replaces the attribute
    on ``api.config`` only — the local binding ``_get_config_path`` would still
    point at the original. This helper checks ``api.config.__dict__`` for an
    override and uses it when present, so existing tests keep working without
    modification.
    """
    try:
        import api.config as _ac

        override = _ac.__dict__.get("_get_config_path")
        if override is not None and override is not _get_config_path:
            return override()  # type: ignore[operator]
    except Exception:
        pass
    return _get_config_path()


def _apply_config_defaults(config_data: dict) -> None:
    agent_cfg = config_data.get("agent")
    if not isinstance(agent_cfg, dict):
        agent_cfg = {}
        config_data["agent"] = agent_cfg
    personalities = agent_cfg.get("personalities")
    if isinstance(personalities, dict):
        merged = copy.deepcopy(_DEFAULT_AGENT_PERSONALITIES)
        merged.update(copy.deepcopy(personalities))
        agent_cfg["personalities"] = merged
    else:
        agent_cfg["personalities"] = copy.deepcopy(_DEFAULT_AGENT_PERSONALITIES)
    experimental = config_data.get("experimental")
    if not isinstance(experimental, dict):
        experimental = {}
        config_data["experimental"] = experimental
    for key, value in _DEFAULT_EXPERIMENTAL_CONFIG.items():
        experimental.setdefault(key, value)


def reload_config_if_stale() -> None:
    import api.config as _ac

    with _cfg_lock:
        try:
            config_path = _effective_config_path()
            current_mtime = config_path.stat().st_mtime
        except OSError:
            current_mtime = 0.0
        path_changed = _cfg_path != config_path
        mtime_stale = current_mtime != _cfg_mtime
        if not _cfg_cache or path_changed or (mtime_stale and not _cfg_has_in_memory_overrides()):
            _refresh_config_cache(config_path)
            if path_changed:
                _ac.cfg = _cfg_cache


def get_config() -> dict:
    config_path = _effective_config_path()
    try:
        current_mtime = config_path.stat().st_mtime
    except OSError:
        current_mtime = 0.0
    path_changed = _cfg_path != config_path
    mtime_stale = current_mtime != _cfg_mtime
    if not _cfg_cache or path_changed or (mtime_stale and not _cfg_has_in_memory_overrides()):
        reload_config_if_stale()
    try:
        import api.config as _ac

        if _ac.cfg is not _cfg_cache:
            return _ac.cfg
    except Exception:
        pass
    return _cfg_cache


def get_config_snapshot() -> dict:
    import api.config as _ac

    with _cfg_lock:
        config_path = _effective_config_path()
        try:
            current_mtime = config_path.stat().st_mtime
        except OSError:
            current_mtime = 0.0
        path_changed = _cfg_path != config_path
        mtime_stale = current_mtime != _cfg_mtime
        if not _cfg_cache or path_changed or (mtime_stale and not _cfg_has_in_memory_overrides()):
            _refresh_config_cache(config_path)
        try:
            active_cfg = _ac.cfg if _ac.cfg is not _cfg_cache else _cfg_cache
        except Exception:
            active_cfg = _cfg_cache
        return copy.deepcopy(active_cfg)


def get_webui_session_save_mode(config_data: dict | None = None) -> str:
    import api.config as _ac

    active_cfg = config_data if isinstance(config_data, dict) else _ac.cfg
    webui_cfg = active_cfg.get("webui", {}) if isinstance(active_cfg, dict) else {}
    if not isinstance(webui_cfg, dict):
        return _DEFAULT_WEBUI_SESSION_SAVE_MODE
    mode = webui_cfg.get("session_save_mode", _DEFAULT_WEBUI_SESSION_SAVE_MODE)
    if isinstance(mode, str):
        normalized = mode.strip().lower()
        if normalized in _WEBUI_SESSION_SAVE_MODES:
            return normalized
    return _DEFAULT_WEBUI_SESSION_SAVE_MODE


def is_unified_session_db_enabled(config_data: dict | None = None) -> bool:
    import api.config as _ac

    active_cfg = config_data if isinstance(config_data, dict) else _ac.cfg
    experimental = active_cfg.get("experimental", {}) if isinstance(active_cfg, dict) else {}
    if not isinstance(experimental, dict):
        return False
    return experimental.get("unified_session_db") is True


def _refresh_config_cache(config_path: Path | None = None) -> None:
    global _cfg_mtime, _cfg_path, _cfg_fingerprint
    import api.config as _ac

    if config_path is None:
        config_path = _get_config_path()
    _cfg_cache.clear()
    _old_cfg_mtime = _cfg_mtime
    _old_cfg_path = _cfg_path
    _cfg_path = config_path
    _cfg_mtime = 0.0
    try:
        if config_path.exists():
            loaded = _ac._load_yaml_config_file_raw(config_path)
            if isinstance(loaded, dict):
                if loaded:
                    _prev_block = getattr(_ac._thread_ctx, "block_process_env_fallback", False)
                    _prev_env = getattr(_ac._thread_ctx, "env", None)
                    try:
                        _ac._thread_ctx.block_process_env_fallback = False
                        _ac._thread_ctx.env = {}
                        _cfg_cache.update(_ac._expand_env_vars(loaded))
                    finally:
                        _ac._thread_ctx.block_process_env_fallback = _prev_block
                        if _prev_env is None:
                            try:
                                del _ac._thread_ctx.env
                            except AttributeError:
                                pass
                        else:
                            _ac._thread_ctx.env = _prev_env
                try:
                    _cfg_mtime = Path(config_path).stat().st_mtime
                except OSError:
                    _cfg_mtime = 0.0
    except Exception:
        logger.debug("Failed to load yaml config from %s", config_path)
    _apply_config_defaults(_cfg_cache)
    _cfg_fingerprint = _fingerprint_config(_cfg_cache)
    if _old_cfg_mtime != 0.0 and _old_cfg_path == config_path:
        try:
            _ac._delete_models_cache_on_disk()
        except Exception:
            pass


def reload_config() -> None:
    with _cfg_lock:
        _refresh_config_cache(_effective_config_path())


def get_config_for_profile_home(profile_home: "Path | str | None") -> dict:
    if not profile_home:
        return get_config()
    try:
        target = Path(profile_home).expanduser()
    except Exception:
        return get_config()
    from api.workspace import _safe_resolve as _cfg_safe_resolve

    target = _cfg_safe_resolve(target)
    try:
        from api.profiles import get_active_hermes_home

        if _cfg_safe_resolve(Path(get_active_hermes_home()).expanduser()) == target:
            return get_config()
    except Exception:
        pass
    try:
        if _cfg_safe_resolve(_effective_config_path().parent) == target:
            return get_config()
    except Exception:
        pass
    if not target.exists():
        return {}
    import api.config as _ac

    profile_cfg = _ac._load_yaml_config_file(target / "config.yaml")
    _apply_config_defaults(profile_cfg)
    return profile_cfg
