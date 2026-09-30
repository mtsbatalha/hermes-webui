"""
Hermes Web UI -- Shared configuration, constants, and global state.
Imported by all other api/* modules and by server.py.

Discovery order for all paths:
  1. Explicit environment variable
  2. Filesystem heuristics (sibling checkout, parent dir, common install locations)
  3. Hardened defaults relative to $HOME
  4. Fail loudly with a human-readable fix-it message if required modules are missing
"""

import collections
import copy
import hashlib
import json
import logging
import math
import os
import queue
import re
import socket
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
import uuid
import weakref
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

# ── Basic layout ──────────────────────────────────────────────────────────────
import api.paths as _paths
from api.plugin_providers import (
    effective_provider_display_name as _effective_provider_display_name,
    is_plugin_model_provider as _is_plugin_model_provider,
    plugin_model_provider_profiles as _plugin_model_provider_profiles,
)

HOME = _paths.HOME
_hermes_home_has_webui_state = _paths._hermes_home_has_webui_state
_platform_default_hermes_home = _paths._platform_default_hermes_home

# REPO_ROOT is the directory that contains this file's parent (api/ -> repo root)
REPO_ROOT = Path(__file__).parent.parent.resolve()

# ── Network config (env-overridable) ─────────────────────────────────────────
HOST = os.getenv("HERMES_WEBUI_HOST", "127.0.0.1")
PORT = int(os.getenv("HERMES_WEBUI_PORT", "8787"))


from api._cfg.env import _env_int, _env_int_clamped, _env_mb_bytes  # split: env helpers live in api/_cfg/env.py


# Sidebar recency window. Resolved here, before profile init, so a profile
# .env cannot override a server-wide resource bound. Clamped at 200.
CLI_VISIBLE_SESSION_LIMIT = _env_int_clamped(
    "HERMES_WEBUI_VISIBLE_SESSION_LIMIT", 20, maximum=200,
)

# ── TLS/HTTPS config (optional, env-overridable) ────────────────────────────
TLS_CERT = os.getenv("HERMES_WEBUI_TLS_CERT", "").strip() or None
TLS_KEY = os.getenv("HERMES_WEBUI_TLS_KEY", "").strip() or None
TLS_ENABLED = TLS_CERT is not None and TLS_KEY is not None

# ── State directory (split: canonical defs in api/_cfg/state.py) ──────────────
from api._cfg.state import (  # noqa: F401
    CUSTOM_MODELS_ENDPOINT_TIMEOUT_SECONDS,
    LAST_WORKSPACE_FILE,
    PROJECTS_FILE,
    SESSION_DIR,
    SESSION_INDEX_FILE,
    SETTINGS_FILE,
    STATE_DIR,
    WORKSPACES_FILE,
    _DEFAULT_HERMES_HOME,
    _DEFAULT_STATE_HOME,
    _resolve_settings_file,
)

logger = logging.getLogger(__name__)


# _env_mb_bytes now imported from api._cfg.env (see top of file)


# ── Agent discovery + thread-local env (split modules) ───────────────────────
# Canonical implementations live in api/_cfg/discovery.py and thread_env.py.
# Re-exported here so ``from api.config import _AGENT_DIR`` etc. keep working
# and so _expand_env_vars can resolve ${VAR} during import-time reload_config().
from api._cfg.discovery import (  # noqa: F401
    _AGENT_DIR,
    _HERMES_FOUND,
    PYTHON_EXE,
    _discover_agent_dir,
    _discover_python,
    _looks_like_agent_source_root,
    _looks_like_pip_style_agent_source_root,
)
from api._cfg.thread_env import (  # noqa: F401
    _expand_env_vars,
    _thread_ctx,
    _thread_local_env_value,
)


# ── Config file (reloadable -- supports profile switching) ──────────────────
# _expand_env_vars is imported from api._cfg.thread_env (see Agent discovery header above);
# the duplicate def that was here has been removed — the re-exported one from
# thread_env.py is byte-identical.

_cfg_cache = {}
_cfg_lock = threading.Lock()
_cfg_mtime: float = 0.0  # last known mtime of config.yaml; 0 = never loaded
_cfg_path: Path | None = None  # active config.yaml path for the disk-loaded cache
_cfg_fingerprint: str | None = None  # serialized snapshot from the last disk load


def _fingerprint_config(data: dict) -> str:
    """Return a stable fingerprint for config dictionaries.

    A few tests and legacy call sites still mutate ``cfg`` directly for
    in-memory overrides.  Path-aware reloads should not immediately discard
    those overrides just because the active profile path differs from the last
    disk load, but an unchanged disk-loaded cache must still reload on profile
    switches.
    """
    try:
        return json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)
    except Exception:
        return repr(data)


def _cfg_has_in_memory_overrides() -> bool:
    """True when cfg was changed after the last successful reload_config().

    Detects two override shapes:
      1. ``_cfg_cache`` was mutated in place (fingerprint differs).
      2. ``cfg`` (the module attribute) was rebound to a different dict —
         e.g. ``monkeypatch.setattr(config, "cfg", {...})`` in tests. The
         alias-with-the-cache pattern at module load means this is a common
         test-isolation override, and silently reloading from disk over it
         (the v0.51.7 path-aware reload regression) breaks any test that
         relies on the override.
    """
    if _cfg_fingerprint is not None and _fingerprint_config(_cfg_cache) != _cfg_fingerprint:
        return True
    # Module attribute rebound away from _cfg_cache by a test or runtime caller.
    try:
        return cfg is not _cfg_cache
    except NameError:
        # cfg not yet defined (during initial reload_config() at import time).
        return False


def _get_config_path() -> Path:
    """Return config.yaml path for the active profile."""
    env_override = os.getenv("HERMES_CONFIG_PATH")
    if env_override:
        return Path(env_override).expanduser()
    try:
        from api.profiles import get_active_hermes_home

        return get_active_hermes_home() / "config.yaml"
    except ImportError:
        return _DEFAULT_HERMES_HOME / "config.yaml"


_WEBUI_SESSION_SAVE_MODES = {"deferred", "eager"}
_DEFAULT_WEBUI_SESSION_SAVE_MODE = "deferred"
_DEFAULT_EXPERIMENTAL_CONFIG = {
    # Dormant first slice for the unified SessionDB migration. Runtime WebUI
    # session call sites must continue using the existing JSON paths unless a
    # later PR deliberately enables and wires this flag.
    "unified_session_db": False,
}
_DEFAULT_AGENT_PERSONALITIES = {
    # Mirrors the Hermes Agent CLI built-ins so WebUI's config-derived
    # /personality path is not empty for fresh profiles.
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


def _apply_config_defaults(config_data: dict) -> None:
    """Populate documented default-only config keys in-place."""
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
        # Keep behavior aligned with CLI loader defaults: if personalities are
        # absent or malformed, replace the section entirely with built-ins.
        agent_cfg["personalities"] = copy.deepcopy(_DEFAULT_AGENT_PERSONALITIES)

    experimental = config_data.get("experimental")
    if not isinstance(experimental, dict):
        experimental = {}
        config_data["experimental"] = experimental
    for key, value in _DEFAULT_EXPERIMENTAL_CONFIG.items():
        experimental.setdefault(key, value)

 
def reload_config_if_stale() -> None:
    """Refresh config.yaml once for concurrent stale read paths."""
    global cfg
    with _cfg_lock:
        try:
            config_path = _get_config_path()
            current_mtime = config_path.stat().st_mtime
        except OSError:
            current_mtime = 0.0
        path_changed = _cfg_path != config_path
        mtime_stale = current_mtime != _cfg_mtime
        if not _cfg_cache or path_changed or (mtime_stale and not _cfg_has_in_memory_overrides()):
            _refresh_config_cache(config_path)
            if path_changed:
                cfg = _cfg_cache


def get_config() -> dict:
    """Return the cached config dict, loading from disk if needed."""
    config_path = _get_config_path()
    try:
        current_mtime = config_path.stat().st_mtime
    except OSError:
        current_mtime = 0.0
    path_changed = _cfg_path != config_path
    mtime_stale = current_mtime != _cfg_mtime
    if not _cfg_cache or path_changed or (mtime_stale and not _cfg_has_in_memory_overrides()):
        reload_config_if_stale()
    # When a test (or runtime caller) has rebound ``cfg`` to a different dict
    # via monkeypatch.setattr(config, "cfg", ...), return that override rather
    # than the underlying _cfg_cache. Without this branch, get_config() would
    # silently bypass the override even though _cfg_has_in_memory_overrides()
    # correctly suppressed the reload.
    try:
        if cfg is not _cfg_cache:
            return cfg
    except NameError:
        pass
    return _cfg_cache


def get_config_snapshot() -> dict:
    """Return a request-owned config snapshot captured under the cache lock."""
    with _cfg_lock:
        config_path = _get_config_path()
        try:
            current_mtime = config_path.stat().st_mtime
        except OSError:
            current_mtime = 0.0
        path_changed = _cfg_path != config_path
        mtime_stale = current_mtime != _cfg_mtime
        if not _cfg_cache or path_changed or (mtime_stale and not _cfg_has_in_memory_overrides()):
            _refresh_config_cache(config_path)
        try:
            active_cfg = cfg if cfg is not _cfg_cache else _cfg_cache
        except NameError:
            active_cfg = _cfg_cache
        return copy.deepcopy(active_cfg)


def get_webui_session_save_mode(config_data: dict | None = None) -> str:
    """Return the validated first-turn session persistence mode.

    ``deferred`` preserves the current first-turn sidecar behaviour: persist
    pending_user_message/runtime fields before streaming, then merge the turn
    after the agent finishes. ``eager`` additionally checkpoints the current
    user turn into ``messages`` before launching the agent thread. Unknown
    values fail closed to ``deferred`` so a typo never reintroduces eager disk
    writes unexpectedly.
    """
    active_cfg = config_data if isinstance(config_data, dict) else cfg
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
    """Return the dormant unified-session-db feature flag.

    The default is intentionally false so adding the JSON adapter cannot change
    runtime persistence until a later migration PR switches call sites.
    """
    active_cfg = config_data if isinstance(config_data, dict) else cfg
    experimental = active_cfg.get("experimental", {}) if isinstance(active_cfg, dict) else {}
    if not isinstance(experimental, dict):
        return False
    return experimental.get("unified_session_db") is True


def _refresh_config_cache(config_path: Path | None = None) -> None:
    """Refresh _cfg_cache for ``config_path``.

    Callers must hold _cfg_lock when invoking this helper because it mutates
    shared state.
    """
    global _cfg_mtime, _cfg_path, _cfg_fingerprint
    if config_path is None:
        config_path = _get_config_path()
    _cfg_cache.clear()
    # Remember the old mtime so we can tell whether config actually changed
    # vs. first-ever load (mtime == 0.0, e.g. server start or profile switch).
    _old_cfg_mtime = _cfg_mtime
    _old_cfg_path = _cfg_path
    _cfg_path = config_path
    _cfg_mtime = 0.0
    try:
        if config_path.exists():
            # Route the parse through the mtime-keyed cache (#4652) so an
            # unchanged config.yaml isn't re-parsed (~125ms+ on a large file)
            # on every reload_config() on the hot path (profile switch /
            # load_settings, #4662 Phase 2). We take the RAW cached dict and
            # run the env expansion HERE, pinned to the unscoped process-env
            # view (below) — never the helper's per-call expansion — for the
            # #798 TLS reason documented in the pin block.
            loaded = _load_yaml_config_file_raw(config_path)
            if isinstance(loaded, dict):
                if loaded:
                    # The process-global _cfg_cache must reflect PROCESS-env
                    # expansion, never a profile-scoped block_process_env_fallback
                    # view — otherwise a reload that fires while a readonly/worker
                    # scope is active (profile alternation resolves _get_config_path
                    # to the named profile, #798 TLS) would bake under-expanded
                    # literal ${VAR}s into the shared cache and starve concurrent
                    # readers of the module-level `cfg` alias. Expansion re-runs
                    # per-read elsewhere; here we pin the cache to the unscoped view.
                    _prev_block = getattr(_thread_ctx, "block_process_env_fallback", False)
                    _prev_env = getattr(_thread_ctx, "env", None)
                    try:
                        _thread_ctx.block_process_env_fallback = False
                        _thread_ctx.env = {}
                        _cfg_cache.update(_expand_env_vars(loaded))
                    finally:
                        _thread_ctx.block_process_env_fallback = _prev_block
                        if _prev_env is None:
                            try:
                                del _thread_ctx.env
                            except AttributeError:
                                pass
                        else:
                            _thread_ctx.env = _prev_env
                # Stamp _cfg_mtime whenever the file parsed to a dict — INCLUDING
                # an empty {} config. The cache-update above is skipped for {} (it's
                # a no-op), but _cfg_mtime MUST still be set or get_config()'s
                # `current_mtime != _cfg_mtime` stale check fires on every call and
                # spins reload_config() under _cfg_lock forever (a `{}` config from a
                # freshly created/reset profile is reachable on the switch hot path).
                # This matches master's pre-#4662 behavior (it entered the block for
                # {} and set the mtime); the inner `if loaded:` only gates the no-op
                # cache update, not the mtime stamp.
                try:
                    _cfg_mtime = Path(config_path).stat().st_mtime
                except OSError:
                    _cfg_mtime = 0.0
    except Exception:
        logger.debug("Failed to load yaml config from %s", config_path)
    _apply_config_defaults(_cfg_cache)
    _cfg_fingerprint = _fingerprint_config(_cfg_cache)
    # Bust the models cache so the next request sees fresh config values.
    # Only delete the disk cache when config has actually changed -- not on
    # first-ever load (when _old_cfg_mtime == 0.0, i.e. server start) and not
    # on a path change (per-client profile switch leaves the old profile's
    # mtime) -- preserving the disk cache so the next restart
    # still hits the fast path without a cold run.
    if _old_cfg_mtime != 0.0 and _old_cfg_path == config_path:
        _delete_models_cache_on_disk()


def reload_config() -> None:
    """Reload config.yaml from the active profile's directory."""
    with _cfg_lock:
        _refresh_config_cache(_get_config_path())


# ── YAML memoization (split: api/_cfg/yaml_cache.py) ─────────────────────────
# Canonical implementations live in api/_cfg/yaml_cache.py; re-exported here so
# ``from api.config import _yaml_file_cache`` keeps working and the dict
# identity is preserved for ``config._yaml_file_cache.clear()`` in tests.
from api._cfg.yaml_cache import (  # noqa: F401
    _config_for_yaml_save,
    _load_yaml_config_file,
    _load_yaml_config_file_raw,
    _save_yaml_config_file,
    _yaml_file_cache,
    _yaml_file_cache_lock,
)


def get_config_for_profile_home(profile_home: "Path | str | None") -> dict:
    """Return the config dict for an explicit profile home directory.

    The streaming agent runs on a detached worker thread that does NOT inherit
    the per-request thread-local profile context (set from the ``hermes_profile``
    cookie on the HTTP handler thread). On that worker, the ambient
    ``get_config()`` resolves through ``get_active_profile_name()`` which falls
    back to the process-global ``_active_profile`` (usually ``default``) — so a
    session running under a non-default profile would silently read the
    **default** profile's ``config.yaml`` for toolsets, prefill context, and
    fallback chains (issue #3294).

    This helper reads the config for a *known* profile home directly off disk,
    bypassing the thread-local resolver entirely. When ``profile_home`` matches
    the path the ambient resolver would pick (the common single-profile case),
    we return the cached ``get_config()`` to preserve in-memory overrides used
    by tests and runtime callers, and to honour an authoritative
    ``HERMES_CONFIG_PATH`` override. Only when the session's profile home
    diverges from the ambient path do we read the session profile's file
    directly — a pure read with no global cache mutation, so it is race-free
    across concurrent sessions on different profiles. Divergent profiles stay
    isolated: a nonexistent home returns ``{}`` and an existing home without a
    ``config.yaml`` yields defaults — neither ever falls back to the ambient
    config (profiles-are-islands).
    """
    if not profile_home:
        return get_config()
    try:
        target = Path(profile_home).expanduser()
    except Exception:
        return get_config()

    from api.workspace import _safe_resolve as _cfg_safe_resolve

    # Canonicalize BOTH sides before every identity comparison (#7168 re-gate
    # round 5): when HERMES_HOME (or the config parent) is a symlink alias,
    # lexical equality fails and an authoritative HERMES_CONFIG_PATH inside
    # the aliased home would be bypassed in favor of a direct — wrong — read.
    target = _cfg_safe_resolve(target)
    try:
        from api.profiles import get_active_hermes_home

        if _cfg_safe_resolve(Path(get_active_hermes_home()).expanduser()) == target:
            return get_config()
    except Exception:
        pass
    # If the ambient resolver already points at this profile home, defer to
    # get_config() so in-memory overrides (monkeypatched cfg) are honored. This
    # MUST run before the nonexistent-home guard below: a matching ambient home
    # whose directory doesn't physically exist yet (fresh install, monkeypatched
    # cfg) must still resolve through get_config(), not return {} (#4516 gate).
    try:
        if _cfg_safe_resolve(_get_config_path().parent) == target:
            return get_config()
    except Exception:
        pass
    if not target.exists():
        return {}
    # Read the profile file directly and apply documented defaults locally so the
    # returned dict matches ambient get_config() shape (including built-in
    # personalities) without mutating any global cache state.
    profile_cfg = _load_yaml_config_file(target / "config.yaml")
    _apply_config_defaults(profile_cfg)
    return profile_cfg


def _config_for_yaml_save(config_data: dict) -> dict:
    """Return a YAML-safe config copy without runtime-only expanded defaults."""
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
                if _DEFAULT_AGENT_PERSONALITIES.get(name) != value
            }
            if custom_personalities:
                agent_cfg["personalities"] = custom_personalities
            else:
                agent_cfg.pop("personalities", None)
        if not agent_cfg:
            data.pop("agent", None)
    return data


def _save_yaml_config_file(config_path: Path, config_data: dict) -> None:
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
    # serve a stale dict (#4650 review) — evicting on our own write closes that gap.
    with _yaml_file_cache_lock:
        _yaml_file_cache.pop(str(config_path), None)


# Initial load
reload_config()
cfg = _cfg_cache  # alias for backward compat with existing references


# ── Default workspace discovery (split: api/_cfg/workspace.py) ─────────────────
from api._cfg.workspace import (  # noqa: F401
    DEFAULT_MODEL,
    DEFAULT_WORKSPACE,
    _discover_default_workspace,
    _ensure_workspace_dir,
    _workspace_candidates,
    resolve_default_workspace,
)


# ── Startup diagnostics (split: api/_cfg/startup.py) ───────────────────────
# Canonical implementations live in api/_cfg/startup.py; re-exported here so
# ``from api.config import print_startup_config`` keeps working.
from api._cfg.startup import (  # noqa: F401
    _warn_state_dir_divergence,
    print_startup_config,
    verify_hermes_imports,
)


# ── Limits ───────────────────────────────────────────────────────────────────
MAX_FILE_BYTES = 400_000
MAX_UPLOAD_BYTES = _env_mb_bytes("HERMES_WEBUI_MAX_UPLOAD_MB", 100)

# ── File type maps ───────────────────────────────────────────────────────────
# Canonical definitions live in api/_cfg/filetypes.py; re-exported here so
# external ``from api.config import MIME_MAP`` keeps working and
# ``api/config.py`` remains the public surface.
from api._cfg.filetypes import CODE_EXTS, IMAGE_EXTS, MD_EXTS, MIME_MAP  # noqa: F401

# Toolsets (split: api/_cfg/toolsets.py) --------------------------------------------------------
# Canonical definitions live in api/_cfg/toolsets.py; re-exported here.
from api._cfg.toolsets import (  # noqa: F401
    _DEFAULT_TOOLSETS,
    _LEGACY_CLI_TOOLSET_ALIASES,
    _normalize_cli_toolsets,
    _resolve_cli_toolsets,
)
# CLI_TOOLSETS is recomputed after get_config() is defined (see after
# the reload_config block) so _resolve_cli_toolsets() can call it
# without a forward-reference NameError.  Seed with defaults here so
# the name exists during module init; the helper import stays alive.
_CLI_TOOLSETS_RESOLVER = _resolve_cli_toolsets  # capture before shadowing
CLI_TOOLSETS: list[str] = list(_DEFAULT_TOOLSETS)
CLI_TOOLSETS = _CLI_TOOLSETS_RESOLVER()  # final: recompute with live get_config()

# ── Model / provider discovery ───────────────────────────────────────────────

# ── Provider catalog (split: api/_cfg/providers_catalog.py) ─────────────────────────
# Canonical literals live in api/_cfg/providers_catalog.py; re-exported here so
# ``from api.config import _PROVIDER_MODELS`` keeps working. Do not duplicate data.
from api._cfg.providers_catalog import (  # noqa: F401
    _FALLBACK_MODELS,
    _PROVIDER_ALIASES,
    _PROVIDER_DISPLAY,
    _PROVIDER_MODELS,
)


# Provider helpers (split: api/_cfg/provider_helpers.py) --------------------------------------------
# Canonical implementations live in api/_cfg/provider_helpers.py; re-exported here.
from api._cfg.provider_helpers import (  # noqa: F401
    _LEGACY_CUSTOM_API_KEY_ENV_WARNED,
    _api_key_env_name,
    _canonicalise_provider_id,
    _configured_model_ids,
    _configured_model_options,
    _custom_endpoint_slugs_for_base_url,
    _custom_provider_entries,
    _custom_provider_slug_from_name,
    _get_anthropic_fallback_env_vars,
    _is_known_model_provider,
    _legacy_custom_api_key_env_name,
    _lookup_custom_api_key_env,
    _merge_model_option_rows,
    _named_custom_provider_slug_for_base_url,
    _named_custom_provider_slug_for_provider,
    _named_custom_provider_slugs,
    _normalize_base_url_for_match,
    _provider_discover_allowed,
    _provider_is_known_or_configured,
    _provider_models_are_discovered_catalog,
    _resolve_configured_provider_id,
    _resolve_provider_alias,
)
# _PROVIDER_MODELS is re-exported from api/_cfg/providers_catalog.py (see Provider catalog header above).


from api._cfg.model_labels import _seed_provider_models_from_core  # noqa: F401
# Format / ambient helpers (split: api/_cfg/format_labels.py) ------------------------------
# Canonical implementations live in api/_cfg/format_labels.py; re-exported here.
from api._cfg.format_labels import (  # noqa: F401
    _AMBIENT_GH_CLI_MARKERS,
    _AMBIENT_GH_ENV_SOURCES,
    _format_nous_label,
    _format_ollama_label,
    _is_ambient_gh_cli_entry,
)


# Picker helpers (split: api/_cfg/picker_helpers.py) ---------------------------------------------------
# Canonical implementations live in api/_cfg/picker_helpers.py; re-exported here.
from api._cfg.picker_helpers import (  # noqa: F401
    _MODEL_PICKER_OVERFLOW_THRESHOLD,
    _MODEL_PICKER_VISIBLE_TARGET,
    _NOUS_FEATURED_TARGET,
    _NOUS_FEATURED_THRESHOLD,
    _NOUS_VENDOR_PRIORITY,
    _OPENROUTER_FREE_TIER_AUGMENT_CAP,
    _apply_provider_prefix,
    _build_nous_featured_set,
    _deduplicate_model_ids,
    _model_matches_picker_selection,
    _openrouter_model_display_name,
    _split_picker_overflow_models,
    _strip_picker_provider_hint,
)

# ── Local-server provider preservation (#1625) ─────────────────────────────
#
# LM Studio, Ollama, llama.cpp, vLLM, TabbyAPI etc. are inference servers,
# not OpenAI-compatible proxies. They register models under their FULL path
# as the registry key (the HuggingFace-style "namespace/model" id, e.g.
# "qwen/qwen3.6-27b"). Stripping the namespace prefix would cause a registry
# miss and the server loads a brand-new instance with default settings,
# silently ignoring the user's tuned context length / parallel slots.
#
# This is distinct from OpenAI-compatible proxies (LiteLLM, OpenRouter relays)
# where stripping "openai/gpt-5.4" → "gpt-5.4" is the correct behavior.
#
# Detection has two layers:
#   1. Static set of known local-server provider names (canonical + common
#      custom-provider naming).
#   2. Loopback / private-host base_url heuristic: an OpenAI-compatible URL
#      pointing at 127.0.0.1, localhost, or a private IP block is almost
#      certainly a local model server, regardless of the provider name.
#      Reuses the same private-IP detection logic used elsewhere in
#      api/config.py for SSRF host trust.
# Routing helpers (split: api/_cfg/routing.py) ---------------------------------------------------
# Canonical implementations live in api/_cfg/routing.py; re-exported here.
from api._cfg.routing import (  # noqa: F401
    _LOCAL_SERVER_PROVIDERS,
    _base_url_points_at_local_server,
    _custom_slug_key as _custom_provider_slug_key,
    _custom_slug_rest_looks_like_host_port,
    _get_provider_base_url,
    _get_provider_cfg,
    _get_providers_cfg,
    _is_first_party_model,
    _is_local_server_provider,
    _model_id_declared_in_config,
    _parse_provider_qualified_model_id,
    _unique_custom_provider_entry_routing as _unique_custom_provider_entry,
    AmbiguousCustomProviderError,
    resolve_model_provider,
)

# Custom provider record helpers (split: api/_cfg/custom_bundles.py) ----------------------
# Canonical implementations live in api/_cfg/custom_bundles.py; re-exported here.
from api._cfg.custom_bundles import (  # noqa: F401  pylint: disable=unused-import
    _custom_record_base_url,
    _resolve_custom_record_key,
    CUSTOM_SELECTION_EXACT,
    CUSTOM_SELECTION_KEYED,
    CUSTOM_SELECTION_RESOLVER,
    CUSTOM_SELECTION_MISSING,
    CUSTOM_SELECTION_MALFORMED,
    CUSTOM_SELECTION_AMBIGUOUS,
    CUSTOM_SELECTION_UNOWNED,
    CUSTOM_ROUTE_UNOWNED,
    CUSTOM_ROUTE_NO_CREDENTIAL,
    CUSTOM_ROUTE_NO_ENDPOINT,
    CUSTOM_ROUTE_ERROR_FIELD,
    CustomProviderRouteError,
    _custom_route_verdict,
    custom_provider_route_error,
    raise_for_custom_provider_route,
    _CUSTOM_RECORD_IDENTITY_FIELDS,
    _custom_record_claims_slug,
    _custom_record_owns_connection,
    _agent_custom_provider_slug,
    _custom_provider_alias_set,
    _custom_record_names_identity,
    _PROVIDER_DISABLED_WORDS,
    _raw_provider_record_enabled,
    _RAW_PROVIDER_URL_FIELDS,
    _normalized_raw_provider_record,
    _unique_raw_provider_record,
    _select_custom_provider_record,
    resolve_custom_provider_connection,
    KEYLESS_CUSTOM_API_KEY,
    CUSTOM_CONNECTION_SIDE_FIELDS,
    _API_MODE_ALIASES,
    _VALID_API_MODES,
    _custom_record_api_mode,
    _custom_record_acp_transport,
    _custom_record_pool_runtime,
    _host_gated_env_key,
    _custom_record_key_cmd_provider,
    _custom_record_declares_credential,
    _unowned_custom_provider_bundle,
    resolve_custom_provider_bundle,
)

# Merge / provenance helpers (split: api/_cfg/custom_bundles.py) -----------------------
# Canonical implementations live in api/_cfg/custom_bundles.py; re-exported here.
from api._cfg.custom_bundles import (  # noqa: F401  pylint: disable=unused-import
    _connection_identity,
    _custom_bundle_endpoint_matches,
    _custom_runtime_endpoint_is_record_owned,
    merge_custom_provider_runtime_bundle,
    _custom_provider_runtime_bundle_with_provenance,
    apply_custom_provider_connection_authority,
)

# Model-context helpers (split: api/_cfg/model_context.py) ------------------------------
# Canonical implementations live in api/_cfg/model_context.py; re-exported here.
from api._cfg.model_context import (  # noqa: F401
    _ACP_SUBPROCESS_PROVIDERS,
    canonical_model_provider_lane,
    get_effective_default_model,
    model_with_provider_context,
)


# Reasoning helpers (split: api/_cfg/reasoning.py) ----------------------------------------
# Canonical implementations live in api/_cfg/reasoning.py; re-exported here.
from api._cfg.reasoning import (  # noqa: F401  pylint: disable=unused-import
    VALID_REASONING_EFFORTS,
    parse_reasoning_effort,
    _strip_provider_hint_for_reasoning,
    _reasoning_name_candidates,
    _candidate_supports_reasoning,
    _NESTED_ROUTE_PATTERN,
    _nested_route_reasoning_denied,
    _nested_gateway_route_reasoning,
    _zai_glm_classification,
    _zai_glm_reasoning_efforts_supported,
    _zai_glm_thinking_toggle_supported,
    _OPENAI_FAMILY_REASONING_PROVIDERS,
    _GPT_5_6_REASONING_MODELS,
    _is_gpt_5_6_reasoning_model,
    _filter_reasoning_efforts_for_provider,
    _KNOWN_REASONING_PROVIDERS,
    _provider_known_reasoning_capable,
    _is_pre_adaptive_anthropic,
    _heuristic_reasoning_efforts,
    _models_dev_reasoning_efforts,
    _NoRedirectHandler,
    _get_lmstudio_reasoning_probe_api_key,
    _lmstudio_reasoning_probe_options_fallback,
    _lmstudio_model_reasoning_options,
    resolve_model_reasoning_efforts,
    _configured_reasoning_effort_lists,
    _resolve_model_reasoning_efforts_impl,
)

# Coercion + status helpers (split: api/_cfg/coerce.py) ------------------------------------
# Canonical implementations live in api/_cfg/coerce.py; re-exported here.
from api._cfg.coerce import (  # noqa: F401  pylint: disable=unused-import
    coerce_reasoning_effort_for_model,
    get_reasoning_status,
    _parse_positive_int_config_value,
    get_max_tokens_status,
    set_max_tokens,
    set_reasoning_display,
    set_reasoning_effort,
)

# Advanced model options + fast-tier (split: api/_cfg/advanced.py) ---------------------------
# Canonical implementations live in api/_cfg/advanced.py; re-exported here.
from api._cfg.advanced import (  # noqa: F401  pylint: disable=unused-import
    _public_advanced_model_options,
    _is_openai_family_provider,
    _normalize_openai_family_model_id,
    _legacy_openai_service_tier_overrides,
    _resolve_main_model_fast_mode_overrides,
    _main_model_supports_service_tier,
    _model_supports_fast_tier_for_provider,
    _annotate_fast_tier_model_groups,
    _public_main_service_tier,
    _main_model_request_overrides,
    _apply_advanced_model_options,
    set_hermes_default_model,
    _coerce_optional_positive_int,
)

# Auxiliary model configuration (split: api/_cfg/auxiliary.py) ---------------------------
# Canonical implementations live in api/_cfg/auxiliary.py; re-exported here.
from api._cfg.auxiliary import (  # noqa: F401  pylint: disable=unused-import
    AUXILIARY_TASK_CATALOG,
    AUX_TASK_SLOTS,
    RETIRED_AUX_TASK_SLOTS,
    _aux_task_payload,
    _iter_auxiliary_task_rows,
    get_auxiliary_models,
    _provider_native_auxiliary_model,
    set_auxiliary_model,
)

# Keep literal ``def ...`` + quoted slots for file-content regression tests
# (api/config.py is the public surface tests read via Path.read_text).
# These dummies are never executed at runtime — the real definitions live in
# api/_cfg/auxiliary.py and are re-exported above.
if False:  # pragma: no cover
    def get_auxiliary_models() -> dict:  # type: ignore[no-redef]
        raise NotImplementedError

    def set_auxiliary_model(task: str, provider: str, model: str, advanced: dict | None = None) -> dict:  # type: ignore[no-redef]
        raise NotImplementedError

    # keep slot names literal for ``"kanban_decomposer" in CONFIG_PY`` checks
    _LITERAL_AUX_SLOTS = (
        "kanban_decomposer",
        "profile_describer",
        "triage_specifier",
    )

# ── TTL cache for get_available_models() ─────────────────────────────────────
_available_models_cache: dict | None = None
_available_models_cache_ts: float = 0.0
_available_models_live_rebuild_ts: float = 0.0
_available_models_cache_source_fingerprint: dict | None = None
_AVAILABLE_MODELS_CACHE_TTL: float = 86400.0  # 24 hours
_SESSION_VISIT_MODELS_FRESHNESS_SECONDS: float = 300.0
_available_models_cache_lock = threading.RLock()  # must be RLock: cold path refactoring moved slow work inside this lock, requiring re-entry
_cache_build_cv = threading.Condition(_available_models_cache_lock)  # shares underlying RLock so notify_all() is safe inside with _available_models_cache_lock
_cache_build_in_progress = False  # True while a cold path is actively building

# Memoized (snapshot_ref, {provider_slug: frozenset(model_ids)}) derived from
# the published models-catalog snapshot. Used by _endpoint_advertised_model_ids
# to answer "did this endpoint actually advertise this exact id?" in O(1) per
# send without rebuilding. Keyed on the snapshot object identity so it is
# recomputed exactly once per catalog publish (the cache is replaced wholesale,
# never mutated) and can never serve stale ids from a superseded catalog.
_advertised_model_ids_memo: tuple | None = None

# Atomic provenance pair: an immutable (snapshot, publisher_fingerprint) tuple
# published together at every catalog publish/invalidate site via
# _sync_models_cache_provenance(). The resolver reads THIS single global with one
# lock-free load so it can never observe a torn snapshot/fingerprint pair (the
# two underlying globals are assigned as separate statements). Reading a tuple is
# atomic under the GIL and, crucially, acquires NO lock — so the per-send
# provenance check introduces no lock-ordering edge (avoids the _cfg_lock ↔
# _available_models_cache_lock deadlock) and never waits behind a catalog rebuild.
_models_cache_provenance: tuple | None = None


# TTL + in-memory cache runtime helpers (split: api/_cfg/models_cache_runtime.py) ----------------------
# Canonical implementations live in api/_cfg/models_cache_runtime.py; re-exported here.
from api._cfg.models_cache_runtime import (  # noqa: F401  pylint: disable=unused-import
    _sync_models_cache_provenance,
    _endpoint_advertised_model_ids,
    _should_warn_budget,
    _get_fresh_memory_models_cache,
    invalidate_models_cache,
    invalidate_credential_pool_cache,
    invalidate_provider_models_cache,
)




# Hard wall-clock budget for a COLD live provider-catalog rebuild when it is
# run from a foreground request path. The live rebuild does one network probe
# per detected provider (Copilot token-exchange HTTPS, OpenRouter /v1/models,
# Nous /models, ...). On a flaky / corp / WSL network any single probe can
# stall for its full per-call timeout (Copilot urllib timeout=10s) and, summed
# across N providers, block the request thread for tens of seconds. This bounds
# the time a foreground caller will wait: past the budget it returns a usable
# fallback (last-known disk cache or a network-free minimal catalog) and lets
# the rebuild finish out-of-band and populate the cache for the next call.
# Set HERMES_WEBUI_MODELS_REBUILD_BUDGET=0 to restore the legacy synchronous
# (unbounded) behaviour.
try:
    _LIVE_REBUILD_BUDGET_SECONDS: float = float(
        os.getenv("HERMES_WEBUI_MODELS_REBUILD_BUDGET", "4") or "4"
    )
except (TypeError, ValueError):
    _LIVE_REBUILD_BUDGET_SECONDS = 4.0


# ── Budget-exceeded warning rate-limit ───────────────────────────────────────
# Q-2979-A3 / Copilot discussion_r3305864400: the live-rebuild-budget-exceeded
# warning at _invoke_models_rebuild's slow-path is potentially high-volume —
# every provider catalog refresh that runs past _LIVE_REBUILD_BUDGET_SECONDS
# emits one, so a hung upstream probe (or a sustained burst of cold callers)
# could flood the log at warning level. Rate-limit per reason: the FIRST
# occurrence in a cooldown window logs at warning; subsequent occurrences in
# the same window log at info (so log signal stays useful but volume bounded).
# Override the default cooldown via HERMES_WEBUI_BUDGET_WARN_COOLDOWN (seconds).
try:
    _BUDGET_WARN_COOLDOWN_SECONDS: float = float(
        os.getenv("HERMES_WEBUI_BUDGET_WARN_COOLDOWN", "300") or "300"
    )
except (TypeError, ValueError):
    _BUDGET_WARN_COOLDOWN_SECONDS = 300.0

_BUDGET_WARN_STATE: dict[str, float] = {}
_BUDGET_WARN_LOCK = threading.Lock()




# Static-catalog helpers (split: api/_cfg/static_catalog.py) --------------------------------------
# Canonical implementations live in api/_cfg/static_catalog.py; re-exported here.
from api._cfg.static_catalog import (  # noqa: F401  pylint: disable=unused-import
    _invoke_models_rebuild,
    _configured_model_badges_from_static_catalog,
    _minimal_static_models_catalog,
    _static_models_catalog_without_live_probes,
)







# Cache for credential pool results -- calling load_pool() per-provider per-server
# session is expensive (~10s for zai due to endpoint probing).  The credential pool
# only changes when the user adds/removes credentials, which is rare; a 24h TTL
# is plenty safe and ensures get_available_models() cold paths are fast.
_CREDENTIAL_POOL_CACHE: dict[tuple[str, str], tuple[float, "CredentialPool"]] = {}  # noqa: F821  forward-ref string annotation, resolved at runtime  # (profile_tag, pid) -> (ts, pool)


# Credential-pool helpers (split: api/_cfg/credential_pool.py) ----------------------------------
# Canonical implementations live in api/_cfg/credential_pool.py; re-exported here.
from api._cfg.credential_pool import (  # noqa: F401  pylint: disable=unused-import
    _credential_pool_profile_tag,
    _pool_entry_payloads,
    _has_explicit_pool_credentials,
)




_provider_models_invalidated_ts: dict[str, float] = {}  # provider_id -> timestamp of last invalidation

# Disk-backed in-memory cache for get_available_models().
# Models-cache fingerprint + disk-cache helpers (split: api/_cfg/models_cache.py) ---------
# Canonical implementations live in api/_cfg/models_cache.py; re-exported here.
from api._cfg.models_cache import (  # noqa: F401  pylint: disable=unused-import
    _current_webui_version,
    _MODELS_CACHE_SCHEMA_VERSION,
    _models_cache_path,
    _get_models_cache_path,
    _get_auth_store_path,
    _models_cache_file_fingerprint,
    _CODEX_CACHE_FINGERPRINT_VOLATILE_KEYS,
    _strip_volatile_codex_cache_fields,
    _codex_models_cache_fingerprint,
    _models_cache_catalog_fingerprint,
    _AUTH_FINGERPRINT_VOLATILE_KEYS,
    _strip_volatile_auth_fields,
    _auth_store_semantic_fingerprint,
    _active_profile_home,
    _models_cache_env_fingerprint,
    _declares_model_provider_kind,
    _models_cache_plugin_fingerprint,
    _plugin_tree_stamps,
    _models_cache_source_fingerprint,
    _delete_models_cache_on_disk,
    _is_valid_models_cache,
    _is_loadable_disk_cache,
)

# Disk-cache IO helpers (split: api/_cfg/models_cache_io.py) ---------------------------------------
# Canonical implementations live in api/_cfg/models_cache_io.py; re-exported here.
from api._cfg.models_cache_io import (  # noqa: F401  pylint: disable=unused-import
    _load_models_cache_from_disk,
    _model_aliases_from_config,
    _load_stale_models_cache_from_disk,
    _save_models_cache_to_disk,
)









# Model labels (split: api/_cfg/model_labels.py) ---------------------------------------------------
# Canonical implementations live in api/_cfg/model_labels.py; re-exported here.
from api._cfg.model_labels import _get_label_for_model  # noqa: F401

# Live-model helpers (split: api/_cfg/live_models.py) -------------------------------
# Canonical implementations live in api/_cfg/live_models.py; re-exported here.
from api._cfg.live_models import (  # noqa: F401
    _hermes_cli_supports_opencode_go_live_catalog,
    _read_live_provider_model_ids,
)


# Models catalog (split: api/_cfg/models_catalog.py) ----------------------------------------
# Canonical implementations live in api/_cfg/models_catalog.py; re-exported here.
from api._cfg.models_catalog import (  # noqa: F401  pylint: disable=unused-import
    _models_from_live_provider_ids,
    _moa_preset_models_from_config,
    _read_visible_codex_cache_model_ids,
    get_available_models,
)


# Session-visit models helpers (split: api/_cfg/models_visit.py) ------------------------------
# Canonical implementations live in api/_cfg/models_visit.py; re-exported here.
from api._cfg.models_visit import (  # noqa: F401  pylint: disable=unused-import
    _maybe_log_slow_stages,
    _models_cache_file_age_seconds,
    get_available_models_for_session_visit,
    warm_models_catalog_provenance_if_cold,
)








# ── Static file path ─────────────────────────────────────────────────────────


def get_static_root() -> Path:
    return REPO_ROOT / "static"


def get_index_html_path() -> Path:
    return get_static_root() / "index.html"


_INDEX_HTML_PATH = get_index_html_path()

# ── Thread synchronisation ───────────────────────────────────────────────────
LOCK = threading.Lock()
# Max compact Session objects held in the in-memory LRU (issue #3506, #4765, #6351).
# Lighter than the agent cache (no live agent runtime), but still bounded so a
# long-running self-hosted install cannot accumulate every session it ever
# touched in RAM and eventually segfault (the #4765/#2233/#4633 crash cluster).
# The shipped default is tuned for the common single-user install; larger
# deployments can keep raising it through config.yaml or the legacy env fallback.
#
# Precedence for the effective cap is resolved by get_sessions_cache_max():
#   1. config.yaml  webui.sessions_cache_max   (preferred, no new env var)
#   2. HERMES_WEBUI_SESSIONS_MAX env var        (legacy operator override)
#   3. DEFAULT_SESSIONS_CACHE_MAX               (sane bounded default)
DEFAULT_SESSIONS_CACHE_MAX = 100
SESSIONS_MAX = _env_int("HERMES_WEBUI_SESSIONS_MAX", DEFAULT_SESSIONS_CACHE_MAX)


def get_sessions_cache_max(config_data: dict | None = None) -> int:
    """Return the effective in-memory SESSIONS cache cap (issue #4765).

    The bound is configurable through ``webui.sessions_cache_max`` in
    ``config.yaml`` so operators of large self-hosted installs can size the
    cache without editing source or adding a new ``HERMES_*`` env var (this
    project forbids new env vars for non-secret config). A missing, empty,
    non-numeric, or below-1 value falls back to the legacy
    ``HERMES_WEBUI_SESSIONS_MAX`` env override, then to
    ``DEFAULT_SESSIONS_CACHE_MAX`` — a typo can never disable the bound and
    reintroduce unbounded memory growth.

    This is the sole resolution authority and it is side-effect free. The cap
    diagnostics report is published by the code that enforces it; see
    ``_LAST_APPLIED_SESSIONS_CACHE_MAX`` below.
    """
    active_cfg = config_data if isinstance(config_data, dict) else get_config()
    webui_cfg = active_cfg.get("webui", {}) if isinstance(active_cfg, dict) else {}
    if isinstance(webui_cfg, dict):
        raw = webui_cfg.get("sessions_cache_max")
        if raw is not None:
            try:
                value = int(raw)
            except (TypeError, ValueError, OverflowError):
                # OverflowError covers YAML's float infinities (`.inf`, `1e400`),
                # which safe_load resolves to a real float. Without it a typo
                # would escape the fallback and raise out of every caller.
                value = None
            if value is not None and value >= 1:
                return value
    # config.yaml did not specify a valid cap: honor the legacy env override
    # (already parsed into SESSIONS_MAX) and finally the hardened default.
    if isinstance(SESSIONS_MAX, int) and SESSIONS_MAX >= 1:
        return SESSIONS_MAX
    return DEFAULT_SESSIONS_CACHE_MAX


# The cap api/models.py::_evict_sessions_over_cap() last enforced. That function
# publishes it after its own fallback and range normalization, so a nonblocking
# diagnostics read reports what eviction applied without re-entering config or
# profile I/O, and nothing else writes this field. Seeded from the config
# reload_config() already loaded at import (see `cfg` above) through the getter's
# dict mode, which reads no file and takes no lock, so the value is right before
# the first eviction pass instead of after it.
_LAST_APPLIED_SESSIONS_CACHE_MAX: int = get_sessions_cache_max(cfg)
CHAT_LOCK = threading.Lock()


class StreamChannel:
    """Broadcast SSE events to every connected browser tab for a stream.

    While no tab is connected, events are buffered so the first/reconnected
    subscriber still receives the stream tail that arrived during the gap.
    Once one or more subscribers are attached, new events are broadcast to all
    of them instead of being consumed destructively by a single queue reader.
    """

    # Cap on the offline replay buffer (drop-oldest). While no tab is subscribed,
    # put_nowait() buffers the stream tail so a first/reconnecting subscriber can
    # catch up. But a client that disconnects without cancelling leaves the turn
    # running with zero subscribers, so an unbounded buffer here grows for the
    # WHOLE turn (a busy turn emits thousands of coalesced token frames) — an OOM
    # risk per abandoned turn (#4633). Bounding to the most recent N frames keeps
    # a reconnecting tab's needed *tail* intact; older dropped frames stay
    # recoverable via the run journal by last_event_id. 8192 is generous enough
    # to hold a long multi-tool turn's backlog while capping worst-case memory to
    # a fixed number of small (event, data, id) tuples — deliberately far above
    # the per-subscriber queue cap below (that queue drops on a *slow* reader;
    # this buffer must survive a legitimate reconnect gap).
    _OFFLINE_BUFFER_MAXLEN = 8192
    # Per-subscriber queue cap (drop-oldest on full). Each connected tab gets its
    # own queue; a slow/backpressured or backgrounded tab used to hold an
    # UNBOUNDED queue.Queue that grew for the WHOLE turn (the producer is the
    # agent token stream), an OOM risk with many tabs × long agentic turns. This
    # caps the per-tab live-broadcast growth to a fixed bound.
    #
    # Bound is intentionally EQUAL to _OFFLINE_BUFFER_MAXLEN, not the much
    # smaller SessionChannel per-subscriber cap of 16. StreamChannel carries the
    # live chat token stream (thousands of frames per turn) and, unlike
    # SessionChannel's low-frequency UI pings, has a reconnect-replay contract:
    # a tab that briefly disconnected must receive the full retained offline
    # tail on resubscribe. Capping below the offline buffer would truncate that
    # replay and force every flaky-network reconnect through the run journal
    # (disk reads) instead of the in-memory fast path. Matching the offline
    # buffer bound preserves that contract while bounding live-broadcast memory
    # to the SAME worst-case #4633 already accepted (a fixed number of small
    # (event, data, id) tuples). The SSE write deadline
    # (SSE_WRITE_DEADLINE_SECONDS) independently breaks a stuck socket within
    # ~20s, so the overflow window is short; this cap bounds it by frame count
    # too. Older frames stay recoverable via the run journal by last_event_id.
    _SUBSCRIBER_QUEUE_MAXSIZE = _OFFLINE_BUFFER_MAXLEN

    def __init__(self):
        self._lock = threading.Lock()
        self._subscribers: list[queue.Queue] = []
        self._offline_buffer: collections.deque = collections.deque(
            maxlen=self._OFFLINE_BUFFER_MAXLEN
        )
        # Frames evicted at the cap from the CURRENT buffer content. Scoped to
        # the buffer, NOT to an attach cycle: it resets exactly when the buffer
        # itself is cleared (first live broadcast), never on subscribe/
        # unsubscribe alone — a drain is a non-destructive copy, so after a
        # transient attach the buffer is STILL truncated and reporting 0 would
        # hand the next subscriber a silently-holed tail. Whether a given
        # reconnect actually NEEDS the evicted frames is decided server-side
        # against offline_first_event_id (its cursor may sit inside the
        # retained tail). Also gates the one-shot eviction log.
        self._offline_dropped = 0
        # Cumulative evictions over the channel's lifetime, never reset — for ops
        # visibility via diagnostic_snapshot().
        self._offline_dropped_total = 0
        # Cumulative per-subscriber queue drops over the channel's lifetime
        # (broadcast + replay paths), never reset — ops visibility for slow tabs.
        self._subscriber_dropped_total = 0
        self._last_event_id: str | None = None

    def subscribe(self) -> queue.Queue:
        q, _snapshot = self.subscribe_with_snapshot()
        return q

    def subscribe_with_snapshot(self) -> tuple[queue.Queue, dict[str, object]]:
        q: queue.Queue = queue.Queue(maxsize=self._SUBSCRIBER_QUEUE_MAXSIZE)
        with self._lock:
            # Replay buffered events to the new subscriber INSIDE the lock so a
            # concurrent put_nowait() can't broadcast a newer event before we
            # finish replaying the older buffered tail. The queue is bounded, so
            # put_nowait raises queue.Full once the cap is reached — drop the
            # OLDEST already-replayed frame and retry, keeping the most recent
            # tail (a reconnecting tab needs the tail; older frames stay
            # recoverable via the run journal by last_event_id). Holding the
            # lock here is safe: no other put_nowait() can interleave. Per Opus
            # advisor on stage-292.
            replayed_dropped = 0
            for item in self._offline_buffer:
                while True:
                    try:
                        q.put_nowait(item)
                        break
                    except queue.Full:
                        # Drop oldest to make room for the newer (more useful)
                        # tail frame. The drained frame is the oldest in this
                        # subscriber's replay window only.
                        try:
                            q.get_nowait()
                        except queue.Empty:
                            # A concurrent consumer drained the queue between
                            # our Full and get_nowait — the queue now has space,
                            # so retry the put instead of dropping `item`. This
                            # path runs under self._lock with a freshly-created
                            # queue (no concurrent consumer), so it is not
                            # reached in practice, but `continue` is the
                            # correct, race-safe rule (see the broadcast path).
                            continue
                        replayed_dropped += 1
            if replayed_dropped:
                self._subscriber_dropped_total += replayed_dropped
                logger.debug(
                    "StreamChannel subscriber replay dropped %d oldest frames "
                    "(cap=%d) while catching up on %d buffered events",
                    replayed_dropped,
                    self._SUBSCRIBER_QUEUE_MAXSIZE,
                    len(self._offline_buffer),
                )
            first = self._offline_buffer[0] if self._offline_buffer else None
            snapshot = {
                "offline_buffered_events": len(self._offline_buffer),
                # Surface eviction so the SSE handler can tell the tail it is
                # about to drain may be truncated (older frames were dropped at
                # the cap) and must be proven contiguous before streaming.
                "offline_dropped_events": self._offline_dropped,
                # Event id of the oldest retained frame: the handler needs run-
                # journal coverage only for (client cursor → this frame); a
                # cursor already inside the retained tail needs no journal.
                "offline_first_event_id": (
                    first[2] if first is not None and len(first) >= 3 else None
                ),
                "last_event_id": self._last_event_id,
            }
            self._subscribers.append(q)
        return q, snapshot

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            try:
                self._subscribers.remove(q)
            except ValueError:
                pass

    def note_last_event_id(self, event_id: str | None) -> None:
        """Record the latest journal event id without changing the queue shape."""
        if not event_id:
            return
        with self._lock:
            self._last_event_id = event_id

    def put_nowait(self, item: tuple[str, object] | tuple[str, object, str | None]) -> None:
        event_id = item[2] if len(item) >= 3 else None
        with self._lock:
            if event_id:
                self._last_event_id = event_id
            subscribers = list(self._subscribers)
            if not subscribers:
                # deque(maxlen) evicts the oldest frame automatically when full.
                # Log once on the first eviction (debug: an abandoned/disconnected
                # turn is expected to hit this) and keep a running dropped count
                # for diagnostics.
                if len(self._offline_buffer) >= self._OFFLINE_BUFFER_MAXLEN:
                    if self._offline_dropped == 0:  # first eviction this cycle
                        logger.debug(
                            "StreamChannel offline buffer full (cap=%d); dropping "
                            "oldest frames while no subscriber is connected",
                            self._OFFLINE_BUFFER_MAXLEN,
                        )
                    self._offline_dropped += 1
                    self._offline_dropped_total += 1
                self._offline_buffer.append(item)
                return
            # A subscriber is live: events now broadcast directly, so the offline
            # tail is drained. Reset the per-cycle eviction count (which also
            # re-arms the one-shot log) so the NEXT disconnect/overflow cycle
            # reports and logs its own truncation, not a stale carry-over.
            self._offline_buffer.clear()
            self._offline_dropped = 0
        # Broadcast outside the lock so a slow put_nowait doesn't block other
        # subscribers or producers. The queue is bounded; on queue.Full drop the
        # OLDEST frame and retry so a slow/backpressured tab keeps its most
        # recent tail instead of growing unbounded for the whole turn. Older
        # frames stay recoverable via the run journal by last_event_id. Mirrors
        # SessionChannel.emit's drop-on-full contract.
        broadcast_dropped = 0
        for q in subscribers:
            while True:
                try:
                    q.put_nowait(item)
                    break
                except queue.Full:
                    try:
                        q.get_nowait()
                    except queue.Empty:
                        # A concurrent consumer (the SSE handler thread)
                        # drained the queue between our Full and get_nowait.
                        # The queue now has space — retry the put so `item` is
                        # delivered. `break` here would silently discard `item`,
                        # and if `item` is a terminal frame (stream_end/error/
                        # cancel) the subscriber never receives it and the client
                        # stays attached indefinitely (spinner-forever). Having
                        # space is exactly the condition we want, so continue.
                        continue
                    broadcast_dropped += 1
        if broadcast_dropped:
            with self._lock:
                self._subscriber_dropped_total += broadcast_dropped
            logger.debug(
                "StreamChannel broadcast dropped %d oldest frames across %d "
                "subscriber queue(s) (cap=%d)",
                broadcast_dropped,
                len(subscribers),
                self._SUBSCRIBER_QUEUE_MAXSIZE,
            )

    def _diagnostic_counters_locked(self) -> dict[str, object]:
        """Return the counter dict. CALLER CONTRACT: ``self._lock`` is held."""
        return {
            "subscriber_count": len(self._subscribers),
            "offline_buffered_events": len(self._offline_buffer),
            # Cumulative over the channel lifetime (ops visibility), vs. the
            # per-cycle count subscribe_with_snapshot() reports for truncation.
            "offline_dropped_events": self._offline_dropped_total,
            # Cumulative per-subscriber queue drops (replay + broadcast) over
            # the channel lifetime — surfaces slow/backpressured tabs.
            "subscriber_dropped_events": self._subscriber_dropped_total,
        }

    def diagnostic_snapshot(self) -> dict[str, object]:
        """Return non-sensitive stream observation counters for health checks."""
        with self._lock:
            return self._diagnostic_counters_locked()

    def try_diagnostic_snapshot(self) -> dict[str, object] | None:
        """Return the same counters without waiting, or ``None`` when busy.

        An aggregate health poll must never stall behind one channel's producer
        or subscriber work, so a contended channel is reported as unavailable
        instead of waited on. ``diagnostic_snapshot()`` keeps its blocking
        contract for the per-stream ``/health?deep=1`` view, which needs the
        counters of every stream rather than a best-effort aggregate.
        """
        if not self._lock.acquire(blocking=False):
            return None
        try:
            return self._diagnostic_counters_locked()
        finally:
            self._lock.release()


def create_stream_channel() -> StreamChannel:
    return StreamChannel()


STREAMS: dict = {}
STREAMS_LOCK = threading.Lock()


def peek_stream(stream_id):
    """Lock-disciplined stream queue lookup.

    Writers mutate STREAMS under STREAMS_LOCK (teardown in api/streaming.py,
    the route layer's start/cancel paths); reads must take the same lock so a
    read racing a teardown pop can never observe-and-use a queue the registry
    has already released. Returns the queue or None — callers keep their
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


# ── Per-session writeback-ownership registry (#6623 re-gate) ────────────────
# Maps session_id -> stream_id of the turn that currently owns the session's
# writeback. Written whenever a turn is admitted (route layer, next to
# session.active_stream_id), REPLACED when a successor turn is admitted, and
# NEVER cleared by cancel_stream() — cancel eagerly pops STREAMS/ACTIVE_RUNS
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


# ── Gateway capability cache (split: api/_cfg/gateway.py) ─────────────────────
# Canonical implementations live in api/_cfg/gateway.py; re-exported here so
# ``from api.config import get_gateway_caps`` keeps working.
from api._cfg.gateway import (  # noqa: F401
    _GATEWAY_CAPS_CACHE,
    _GATEWAY_CAPS_LOCK,
    _GATEWAY_CAPS_TTL_S,
    _gateway_caps_probe_timed_out,
    gateway_approval_unavailable_reason,
    gateway_supports_approval,
    gateway_supports_approval_identity_v1,
    get_gateway_caps,
    invalidate_gateway_caps,
)


# ── notify_on_complete agent-wakeup wiring ─────────────────────────────────
# When terminal(notify_on_complete=true, background=true) fires, the process
# registry pushes a completion event onto tools.process_registry.completion_queue.
# A drain task spawned at WebUI startup (api/background_process.py) reads that
# queue and emits an SSE `process_complete` event to the matching session.
# PROCESS_SESSION_INDEX maps the per-process "session_key" (set in the spawned
# subprocess via HERMES_SESSION_KEY) back to the WebUI session_id that owns it,
# so the drain task can route the event to the right SSE channel.
# PENDING_BG_TASK_COMPLETIONS mirrors PENDING_GOAL_CONTINUATION: server-side
# marker discarded atomically by routes.py when the frontend re-POSTs the
# wakeup_prompt as the next user turn. (process_complete event, agent wakeup fix)
PROCESS_SESSION_INDEX: dict = {}  # process_registry session_key -> WebUI session_id
PROCESS_SESSION_INDEX_LOCK = threading.Lock()
PENDING_BG_TASK_COMPLETIONS: set = set()  # session_ids awaiting a process_complete wakeup turn
BG_TASK_COMPLETE_EVENTS_SEEN: dict = {}  # session_id -> set[process_id] for idempotency
BG_TASK_COMPLETE_EVENTS_SEEN_LOCK = threading.Lock()

# Defer-path fix (fast-bg-task wakeup race): when a completion arrives while a
# turn is active, Option Z's drain branch CANNOT start a turn (would 409). The
# pre-existing PENDING_BG_TASK_COMPLETIONS marker was a bare session_id flag —
# the wakeup_prompt was DISCARDED, and the only consumer (PR #2279 next-turn
# drain) reads completion_queue, which the Option Z drain thread already
# emptied. So for an autonomous agent (no next user turn) the deferred wakeup
# was lost forever. DEFERRED_PROCESS_WAKEUPS persists the actual prompt(s) so a
# turn-teardown idle-hook (api/streaming) can redeliver them once the session
# goes idle — symmetric with the idle branch (idle now → fire now; busy now →
# fire at turn-end). Atomic claim (pop under lock) guarantees single delivery:
# whoever claims first (teardown hook OR next-turn drain) fires; the other
# finds nothing → no double-fire, no wakeup loop.
DEFERRED_PROCESS_WAKEUPS: dict = {}  # session_id -> list[{"process_id", "wakeup_prompt"}]
DEFERRED_PROCESS_WAKEUPS_LOCK = threading.Lock()

# ── Persistent per-session SSE channel (Option X) ──────────────────────────
# A long-lived SSE channel scoped to a WebUI session_id rather than a single
# agent turn (stream_id). Subscribed to by the frontend on session mount,
# torn down on session unmount, and refcounted across tabs. Used to deliver
# events (currently process_complete) that fire while no agent turn is
# active — bridging the gap that PR #2242 + #2279 left when STREAMS has
# already been torn down. The registry lives in api.background_process; this
# constant is the idle-cap before the reaper collects an unsubscribed
# channel. 4h is a defensive ceiling against zombie connections; the
# subscribers-empty grace path (60s) handles ordinary tab-close traffic.
SESSION_CHANNEL_IDLE_TTL_SECS: int = 14400  # 4 hours
SESSION_CHANNEL_SUBSCRIBER_GRACE_SECS: int = 60  # subscribers-empty grace

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
    on top of it. That row is already terminal from the client's perspective —
    its run journal ends in a terminal event — so recovery paths that hand a
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
import collections
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
    # under an in-flight turn — ACTIVE_RUNS is the authoritative liveness signal
    # (mirrors the worker's own LRU-eviction guard in streaming.py). When a run
    # is live we still drop the cache handle above (harmless — the worker holds
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

# ── Thread-local env context ─────────────────────────────────────────────────
# (_thread_ctx + _thread_local_env_value are defined near the top of this module,
# above the config-file section, so _expand_env_vars can reference them at the
# import-time reload_config() without a forward-reference NameError.)


def _set_thread_env(**kwargs):
    _thread_ctx.env = kwargs


def _clear_thread_env():
    _thread_ctx.env = {}


# ── Per-session agent locks ───────────────────────────────────────────────────
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


# ── Settings persistence ─────────────────────────────────────────────────────

# ── Settings store (split: api/_cfg/settings_store.py) ──────────────────────────
# Canonical implementations live in api/_cfg/settings_store.py; re-exported here.
from api._cfg.settings_store import (  # noqa: F401  pylint: disable=unused-import
    _SETTINGS_ALLOWED_KEYS,
    _SETTINGS_BOOL_KEYS,
    _SETTINGS_DEFAULTS,
    _SETTINGS_ENUM_VALUES,
    _SETTINGS_FLOAT_RANGES,
    _SETTINGS_INT_RANGES,
    _SETTINGS_LANG_RE,
    _SETTINGS_LEGACY_DROP_KEYS,
    _SETTINGS_LEGACY_THEME_MAP,
    _SETTINGS_PERSISTED_SPEECH_KEYS_FIELD,
    _SETTINGS_SKIN_VALUES,
    _SETTINGS_SPEECH_KEYS,
    _SETTINGS_THEME_VALUES,
    _SETTINGS_TTS_ENGINE_RE,
    _atomic_write_settings_text,
    _coerce_provider_cost_budget,
    _current_umask,
    _extract_persisted_speech_keys,
    _normalize_appearance,
    _read_raw_settings_file,
    _settings_payload_for_write,
    _SETTINGS_WRITE_LOCK,
    _SETTINGS_WRITE_VERSION,
    load_settings,
    persisted_speech_settings_keys,
    save_settings,
)


# Apply saved settings on startup (override env-derived defaults)
# Exception: if HERMES_WEBUI_DEFAULT_WORKSPACE is explicitly set in the
# environment, it wins over whatever settings.json has stored.  Persisted
# config must never shadow an explicit env-var override (Docker deployments
# rely on this — otherwise deleting settings.json is the only escape).
_startup_settings = load_settings()
try:
    _settings_file_exists = SETTINGS_FILE.exists()
except OSError:
    _settings_file_exists = False
if _settings_file_exists:
    if not os.getenv("HERMES_WEBUI_DEFAULT_WORKSPACE"):
        DEFAULT_WORKSPACE = resolve_default_workspace(
            _startup_settings.get("default_workspace")
        )
    _startup_settings.pop("default_model", None)  # always drop stale value; model comes from config.yaml
    if _startup_settings.get("default_workspace") != str(DEFAULT_WORKSPACE):
        _startup_settings["default_workspace"] = str(DEFAULT_WORKSPACE)
        try:
            startup_persisted_speech_keys = _extract_persisted_speech_keys(
                _read_raw_settings_file()
            )
            _atomic_write_settings_text(
                SETTINGS_FILE,
                json.dumps(
                    _settings_payload_for_write(
                        _startup_settings, startup_persisted_speech_keys
                    ),
                    ensure_ascii=False,
                    indent=2,
                ),
            )
        except Exception:
            pass

# ── SESSIONS in-memory cache (LRU OrderedDict) ───────────────────────────────
SESSIONS: collections.OrderedDict = collections.OrderedDict()


def get_runtime_diagnostics_snapshot() -> dict[str, dict[str, object]]:
    """Return nonblocking scalar observations owned by the config module."""
    result = {
        "sessions": {"available": False, "resident": 0, "cap": 0},
        "models_cache": {
            "available": False,
            "groups": 0,
            "models": 0,
            "age_seconds": None,
        },
    }
    try:
        if LOCK.acquire(blocking=False):
            try:
                # Held-section discipline: len(), arithmetic, and owner-held
                # scalars only. Never call anything here that can resolve config
                # or a profile, touch the filesystem, import a module, or wait on
                # another lock — the cap is the scalar _evict_sessions_over_cap()
                # published, precisely so this section stays leaf-nonblocking.
                result["sessions"] = {
                    "available": True,
                    "resident": max(0, int(len(SESSIONS))),
                    "cap": max(0, int(_LAST_APPLIED_SESSIONS_CACHE_MAX)),
                }
            finally:
                LOCK.release()
    except Exception:
        pass
    try:
        if _available_models_cache_lock.acquire(blocking=False):
            try:
                # Same held-section discipline: len(), isinstance, float(), and
                # time.monotonic() only. _available_models_cache_lock is an RLock
                # (see its definition), so a nonblocking acquire from a thread
                # that already holds it would report available mid-build; safe
                # here because health collection is never nested inside a
                # catalog build, and nothing may be added that changes that.
                snapshot = _available_models_cache
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
                if snapshot is not None and _available_models_cache_ts:
                    age = max(0.0, time.monotonic() - float(_available_models_cache_ts))
                result["models_cache"] = {
                    "available": True,
                    "groups": max(0, int(group_count)),
                    "models": max(0, int(model_count)),
                    "age_seconds": age,
                }
            finally:
                _available_models_cache_lock.release()
    except Exception:
        pass
    return result

# ── Profile state initialisation ────────────────────────────────────────────
# Must run after all imports are resolved to correctly patch module-level caches
try:
    from api.profiles import init_profile_state

    init_profile_state()
except ImportError:
    pass  # hermes_cli not available -- default profile only


# Run the provider-model seeder once at import time. Must be at the END of the
# module because _seed_provider_models_from_core() calls _get_label_for_model,
# which is defined ~3000 lines above. Placing the invocation earlier (e.g. right
# after the seeder's def) caused a NameError that the bare except silently
# swallowed — exactly when the seeder had real work to do (#4413).
try:
    _seed_provider_models_from_core()
except ImportError:
    pass  # hermes_cli not available (standalone deployment)
except Exception:
    logger.warning("provider-model seeder failed", exc_info=True)
