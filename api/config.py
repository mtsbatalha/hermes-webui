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


# ── Config cache + reload (split: api/_cfg/config_store.py) ───────────
# Canonical implementations live in api/_cfg/config_store.py; re-exported here
# so ``from api.config import _cfg_cache`` keeps working and the dict
# identity is preserved for ``config._cfg_cache is config.cfg`` in tests.
# Scalar globals (_cfg_mtime/_cfg_path/_cfg_fingerprint) live in the store
# module; we proxy attribute access so ``cfg._cfg_mtime`` always reflects the
# store's current value (``from import`` copies a scalar, so re-importing it
# here would go stale on reassignment — PEP 562 __getattr__ fixes that).
from api._cfg.config_store import (  # noqa: F401
    _WEBUI_SESSION_SAVE_MODES,
    _DEFAULT_WEBUI_SESSION_SAVE_MODE,
    _DEFAULT_EXPERIMENTAL_CONFIG,
    _DEFAULT_AGENT_PERSONALITIES,
    _cfg_cache,
    _cfg_lock,
    _fingerprint_config,
    _cfg_has_in_memory_overrides,
    _get_config_path,
    _apply_config_defaults,
    reload_config_if_stale,
    get_config,
    get_config_snapshot,
    get_webui_session_save_mode,
    is_unified_session_db_enabled,
    _refresh_config_cache,
    reload_config,
    get_config_for_profile_home,
)


# PEP 562 alone can't proxy *writes* (``config._cfg_mtime = 0`` just puts a
# shadow entry in ``config.__dict__``).  Tests do exactly that to suspend the
# mtime guard, and the engine writes the same scalars via
# ``_refresh_config_cache`` in the store module.  Turn the module object
# itself into a tiny proxy type so both sides share one canonical store.
import sys as _sys
import types as _types


_REGISTRY_PROXY = {
    # config_store scalars
    "_cfg_mtime": "api._cfg.config_store",
    "_cfg_path": "api._cfg.config_store",
    "_cfg_fingerprint": "api._cfg.config_store",
    "_effective_config_path": "api._cfg.config_store",
    # stream_registry
    "STREAMS": "api._cfg.stream_registry",
    "STREAMS_LOCK": "api._cfg.stream_registry",
    "STREAM_SESSION_OWNERS": "api._cfg.stream_registry",
    "STREAM_SESSION_OWNERS_LOCK": "api._cfg.stream_registry",
    "CANCEL_FLAGS": "api._cfg.stream_registry",
    "AGENT_INSTANCES": "api._cfg.stream_registry",
    "STREAM_PARTIAL_TEXT": "api._cfg.stream_registry",
    "STREAM_REASONING_TEXT": "api._cfg.stream_registry",
    "STREAM_LIVE_TOOL_CALLS": "api._cfg.stream_registry",
    "STREAM_GOAL_RELATED": "api._cfg.stream_registry",
    "STREAM_LAST_EVENT_ID": "api._cfg.stream_registry",
    "PENDING_GOAL_CONTINUATION": "api._cfg.stream_registry",
    "peek_stream": "api._cfg.stream_registry",
    "register_stream_owner": "api._cfg.stream_registry",
    "stream_owner_session_id": "api._cfg.stream_registry",
    "unregister_stream_owner": "api._cfg.stream_registry",
    # session_writeback
    "SESSION_WRITEBACK_OWNERS": "api._cfg.session_writeback",
    "SESSION_WRITEBACK_OWNERS_LOCK": "api._cfg.session_writeback",
    "register_session_writeback_owner": "api._cfg.session_writeback",
    "session_writeback_owner": "api._cfg.session_writeback",
    "clear_session_writeback_owner_if_owned": "api._cfg.session_writeback",
    # process_wakeup
    "PROCESS_SESSION_INDEX": "api._cfg.process_wakeup",
    "PROCESS_SESSION_INDEX_LOCK": "api._cfg.process_wakeup",
    "PENDING_BG_TASK_COMPLETIONS": "api._cfg.process_wakeup",
    "BG_TASK_COMPLETE_EVENTS_SEEN": "api._cfg.process_wakeup",
    "BG_TASK_COMPLETE_EVENTS_SEEN_LOCK": "api._cfg.process_wakeup",
    "DEFERRED_PROCESS_WAKEUPS": "api._cfg.process_wakeup",
    "DEFERRED_PROCESS_WAKEUPS_LOCK": "api._cfg.process_wakeup",
    # active_runs
    "ACTIVE_RUNS": "api._cfg.active_runs",
    "ACTIVE_RUNS_LOCK": "api._cfg.active_runs",
    "LAST_RUN_FINISHED_AT": "api._cfg.active_runs",
    "SERVER_START_TIME": "api._cfg.active_runs",
    "SESSION_AGENT_CACHE": "api._cfg.active_runs",
    "SESSION_AGENT_CACHE_LOCK": "api._cfg.active_runs",
    "SESSION_AGENT_CACHE_MAX": "api._cfg.active_runs",
    "_evict_session_agent": "api._cfg.active_runs",
    "active_run_is_attachable": "api._cfg.active_runs",
    "active_run_cancel_is_stale": "api._cfg.active_runs",
    "register_active_run": "api._cfg.active_runs",
    "unregister_active_run": "api._cfg.active_runs",
    "update_active_run": "api._cfg.active_runs",
    # session_locks
    "SESSION_AGENT_LOCKS": "api._cfg.session_locks",
    "SESSION_AGENT_LOCKS_LOCK": "api._cfg.session_locks",
    "_get_session_agent_lock": "api._cfg.session_locks",
    "_alias_session_agent_lock": "api._cfg.session_locks",
    # thread_env
    "_thread_ctx": "api._cfg.thread_env",
    "_set_thread_env": "api._cfg.thread_env",
    "_clear_thread_env": "api._cfg.thread_env",
    "_thread_local_env_value": "api._cfg.thread_env",
    "_expand_env_vars": "api._cfg.thread_env",
    # stream_channel
    "StreamChannel": "api._cfg.stream_channel",
    "create_stream_channel": "api._cfg.stream_channel",
    # session_limits
    "LOCK": "api._cfg.session_limits",
    "DEFAULT_SESSIONS_CACHE_MAX": "api._cfg.session_limits",
    "SESSIONS_MAX": "api._cfg.session_limits",
    "get_sessions_cache_max": "api._cfg.session_limits",
    "_LAST_APPLIED_SESSIONS_CACHE_MAX": "api._cfg.session_limits",
    "CHAT_LOCK": "api._cfg.session_limits",
    "get_static_root": "api._cfg.session_limits",
    "get_index_html_path": "api._cfg.session_limits",
    "_INDEX_HTML_PATH": "api._cfg.session_limits",
    # runtime_diag
    "SESSIONS": "api._cfg.runtime_diag",
    "get_runtime_diagnostics_snapshot": "api._cfg.runtime_diag",
    # startup_settings
    "_startup_settings": "api._cfg.startup_settings",
    # models_state
    "_available_models_cache": "api._cfg.models_state",
    "_available_models_cache_ts": "api._cfg.models_state",
    "_available_models_live_rebuild_ts": "api._cfg.models_state",
    "_available_models_cache_source_fingerprint": "api._cfg.models_state",
    "_AVAILABLE_MODELS_CACHE_TTL": "api._cfg.models_state",
    "_SESSION_VISIT_MODELS_FRESHNESS_SECONDS": "api._cfg.models_state",
    "_available_models_cache_lock": "api._cfg.models_state",
    "_cache_build_cv": "api._cfg.models_state",
    "_cache_build_in_progress": "api._cfg.models_state",
    "_advertised_model_ids_memo": "api._cfg.models_state",
    "_models_cache_provenance": "api._cfg.models_state",
    "_LIVE_REBUILD_BUDGET_SECONDS": "api._cfg.models_state",
    "_BUDGET_WARN_COOLDOWN_SECONDS": "api._cfg.models_state",
    "_BUDGET_WARN_STATE": "api._cfg.models_state",
    "_BUDGET_WARN_LOCK": "api._cfg.models_state",
    "_CREDENTIAL_POOL_CACHE": "api._cfg.models_state",
    "_provider_models_invalidated_ts": "api._cfg.models_state",
}


class _ConfigModule(_types.ModuleType):
    def __getattribute__(self, name: str):  # type: ignore[override]
        # Delegate registry names live to canonical modules so scalar
        # rebindings (e.g. LAST_RUN_FINISHED_AT) stay coherent.
        proxy = globals().get("_REGISTRY_PROXY")
        if isinstance(proxy, dict) and name in proxy:
            import importlib

            m = importlib.import_module(proxy[name])
            return getattr(m, name)
        return super().__getattribute__(name)

    def __getattr__(self, name: str):  # type: ignore[override]
        # Fallback for names not yet in __dict__ but in proxy (import order)
        mod = _REGISTRY_PROXY.get(name)
        if mod is not None:
            import importlib

            m = importlib.import_module(mod)
            return getattr(m, name)
        raise AttributeError(f"module 'api.config' has no attribute {name!r}")

    def __setattr__(self, name: str, value) -> None:  # type: ignore[override]
        mod = _REGISTRY_PROXY.get(name)
        if mod is not None:
            import importlib

            m = importlib.import_module(mod)
            setattr(m, name, value)
        super().__setattr__(name, value)

    def __dir__(self):  # type: ignore[override]
        import api._cfg.config_store as _cs

        return sorted(set(super().__dir__()) | set(_REGISTRY_PROXY.keys()) | set(dir(_cs)))


_sys.modules[__name__].__class__ = _ConfigModule
# Also patch the already-created instance's __class__ for this execution
try:
    import api.config as _self_proxy  # noqa: F401

    _self_proxy.__class__ = _ConfigModule  # type: ignore[attr-defined]
except Exception:
    pass

cfg = _cfg_cache  # alias for backward compat with existing references
# Initial load — must run after both re-exports so _refresh_config_cache can
# resolve _ac._load_yaml_config_file_raw and _ac._expand_env_vars.
reload_config()


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

# ── Models-cache shared mutable state (split: api/_cfg/models_state.py) ───────
# Canonical definitions live in api/_cfg/models_state.py; re-exported here so
# ``from api.config import _available_models_cache`` keeps working.
from api._cfg.models_state import (  # noqa: F401
    _AVAILABLE_MODELS_CACHE_TTL,
    _BUDGET_WARN_COOLDOWN_SECONDS,
    _BUDGET_WARN_LOCK,
    _BUDGET_WARN_STATE,
    _CREDENTIAL_POOL_CACHE,
    _LIVE_REBUILD_BUDGET_SECONDS,
    _SESSION_VISIT_MODELS_FRESHNESS_SECONDS,
    _advertised_model_ids_memo,
    _available_models_cache,
    _available_models_cache_lock,
    _available_models_cache_source_fingerprint,
    _available_models_cache_ts,
    _available_models_live_rebuild_ts,
    _cache_build_cv,
    _cache_build_in_progress,
    _models_cache_provenance,
    _provider_models_invalidated_ts,
)


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







# Static-catalog helpers (split: api/_cfg/static_catalog.py) --------------------------------------
# Canonical implementations live in api/_cfg/static_catalog.py; re-exported here.
from api._cfg.static_catalog import (  # noqa: F401  pylint: disable=unused-import
    _invoke_models_rebuild,
    _configured_model_badges_from_static_catalog,
    _minimal_static_models_catalog,
    _static_models_catalog_without_live_probes,
)







# Credential-pool helpers (split: api/_cfg/credential_pool.py) ----------------------------------
# Canonical implementations live in api/_cfg/credential_pool.py; re-exported here.
from api._cfg.credential_pool import (  # noqa: F401  pylint: disable=unused-import
    _credential_pool_profile_tag,
    _pool_entry_payloads,
    _has_explicit_pool_credentials,
)




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






# ── Static path + thread synchronisation (split: api/_cfg/session_limits.py) ─
# Canonical implementations live in api/_cfg/session_limits.py; re-exported here
# so ``from api.config import LOCK`` keeps working.
from api._cfg.session_limits import (  # noqa: F401
    CHAT_LOCK,
    DEFAULT_SESSIONS_CACHE_MAX,
    LOCK,
    SESSIONS_MAX,
    _INDEX_HTML_PATH,
    _LAST_APPLIED_SESSIONS_CACHE_MAX,
    get_index_html_path,
    get_sessions_cache_max,
    get_static_root,
)

# Keep literals for file-content regression tests that read api/config.py.
if False:  # pragma: no cover
    _INDEX_HTML_PATH = None  # type: ignore[no-redef]
    LOCK = None  # type: ignore[no-redef]
    CHAT_LOCK = None  # type: ignore[no-redef]
    SESSIONS_MAX = 0  # type: ignore[no-redef]
    DEFAULT_SESSIONS_CACHE_MAX = 0  # type: ignore[no-redef]

    def get_sessions_cache_max(config_data=None):  # type: ignore[no-redef]
        raise NotImplementedError

    def get_static_root():  # type: ignore[no-redef]
        raise NotImplementedError

    def get_index_html_path():  # type: ignore[no-redef]
        raise NotImplementedError


# -- StreamChannel (split: api/_cfg/stream_channel.py) ---------------------------
# Canonical implementation lives in api/_cfg/stream_channel.py; re-exported here
# so ``from api.config import StreamChannel`` keeps working.
from api._cfg.stream_channel import StreamChannel, create_stream_channel  # noqa: F401

# Keep literal ``class StreamChannel`` for file-content regression tests
# (api/config.py is the public surface some tests read via Path.read_text).
# Never executed at runtime -- the real definitions live in
# api/_cfg/stream_channel.py and are re-exported above.
if False:  # pragma: no cover
    class StreamChannel:  # type: ignore[no-redef]
        pass

    def create_stream_channel() -> StreamChannel:  # type: ignore[no-redef]
        raise NotImplementedError


# ── Stream / stream-owner / SSE-buffer registries (split: api/_cfg/stream_registry.py) ─
# Canonical implementations live in api/_cfg/stream_registry.py; re-exported here
# so ``from api.config import STREAMS`` keeps working and dict identity is
# preserved for ``config.STREAMS.clear()`` in tests.
from api._cfg.stream_registry import (  # noqa: F401
    AGENT_INSTANCES,
    CANCEL_FLAGS,
    PENDING_GOAL_CONTINUATION,
    STREAM_GOAL_RELATED,
    STREAM_LAST_EVENT_ID,
    STREAM_LIVE_TOOL_CALLS,
    STREAM_PARTIAL_TEXT,
    STREAM_REASONING_TEXT,
    STREAM_SESSION_OWNERS,
    STREAM_SESSION_OWNERS_LOCK,
    STREAMS,
    STREAMS_LOCK,
    peek_stream,
    register_stream_owner,
    stream_owner_session_id,
    unregister_stream_owner,
)


# ── Per-session writeback-ownership registry (split: api/_cfg/session_writeback.py) ─
# Canonical implementations live in api/_cfg/session_writeback.py; re-exported here
# so ``from api.config import SESSION_WRITEBACK_OWNERS`` keeps working and dict
# identity is preserved for tests.
from api._cfg.session_writeback import (  # noqa: F401
    SESSION_WRITEBACK_OWNERS,
    SESSION_WRITEBACK_OWNERS_LOCK,
    clear_session_writeback_owner_if_owned,
    register_session_writeback_owner,
    session_writeback_owner,
)


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


# ── notify_on_complete wakeup wiring (split: api/_cfg/process_wakeup.py) ─────────
# Canonical implementations live in api/_cfg/process_wakeup.py; re-exported here
# so ``from api.config import PROCESS_SESSION_INDEX`` keeps working and dict
# identity is preserved for tests.
from api._cfg.process_wakeup import (  # noqa: F401
    BG_TASK_COMPLETE_EVENTS_SEEN,
    BG_TASK_COMPLETE_EVENTS_SEEN_LOCK,
    DEFERRED_PROCESS_WAKEUPS,
    DEFERRED_PROCESS_WAKEUPS_LOCK,
    PENDING_BG_TASK_COMPLETIONS,
    PROCESS_SESSION_INDEX,
    PROCESS_SESSION_INDEX_LOCK,
)

# ── Persistent per-session SSE channel (Option X) ──────────────────────────
# Constants stay in api/config.py (2 lines; no dedicated module warranted).
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

# ── Active-run + agent-cache registries (split: api/_cfg/active_runs.py) ─────────
# Canonical implementations live in api/_cfg/active_runs.py; re-exported here so
# ``from api.config import ACTIVE_RUNS`` keeps working and dict identity is
# preserved for tests.
from api._cfg.active_runs import (  # noqa: F401
    ACTIVE_RUNS,
    ACTIVE_RUNS_LOCK,
    LAST_RUN_FINISHED_AT,
    SERVER_START_TIME,
    SESSION_AGENT_CACHE,
    SESSION_AGENT_CACHE_LOCK,
    SESSION_AGENT_CACHE_MAX,
    _evict_session_agent,
    active_run_cancel_is_stale,
    active_run_is_attachable,
    register_active_run,
    unregister_active_run,
    update_active_run,
)

# ── Thread-local env context (split: api/_cfg/thread_env.py) ────────────────
# Canonical implementations live in api/_cfg/thread_env.py; re-exported here so
# ``from api.config import _set_thread_env`` keeps working.  Kept near the top
# originally so _expand_env_vars could reference _thread_ctx at import-time
# reload_config() without a forward-reference NameError; now both live together.
from api._cfg.thread_env import _clear_thread_env as _clear_thread_env  # noqa: F401, PLC0414
from api._cfg.thread_env import _set_thread_env as _set_thread_env  # noqa: F401, PLC0414


# ── Per-session agent locks (split: api/_cfg/session_locks.py) ────────────────
# Canonical implementation lives in api/_cfg/session_locks.py; re-exported here
# so ``from api.config import SESSION_AGENT_LOCKS`` keeps working and the dict
# identity is preserved for ``config.SESSION_AGENT_LOCKS.clear()`` in tests.
from api._cfg.session_locks import (  # noqa: F401
    SESSION_AGENT_LOCKS,
    SESSION_AGENT_LOCKS_LOCK,
    _alias_session_agent_lock,
    _get_session_agent_lock,
)


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


# Startup settings bootstrap (split: api/_cfg/startup_settings.py) ────────────
# Canonical implementation lives in api/_cfg/startup_settings.py; re-exported here.
from api._cfg.startup_settings import _startup_settings  # noqa: F401

# ── SESSIONS LRU + runtime diagnostics (split: api/_cfg/runtime_diag.py) ─
# Canonical implementations live in api/_cfg/runtime_diag.py; re-exported here so
# ``from api.config import SESSIONS`` keeps working and dict identity is preserved.
from api._cfg.runtime_diag import SESSIONS, get_runtime_diagnostics_snapshot  # noqa: F401

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
