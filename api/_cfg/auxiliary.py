"""Auxiliary model configuration extracted from api/config.py.

Re-exported via ``api.config`` so ``from api.config import get_auxiliary_models``
keeps working.  No external module should import from ``api._cfg.auxiliary`` directly.
"""

from __future__ import annotations

import threading

# Lazy helpers — resolved through api.config / api._cfg at call time to avoid
# circular imports (auxiliary -> config -> auxiliary).


def _cfg() -> dict:
    try:
        import api.config as _ac
        fn = getattr(_ac, "get_config", None)
        if callable(fn):
            return fn() or {}
        # fallback: direct cfg dict
        cfg_obj = getattr(_ac, "cfg", None)
        if isinstance(cfg_obj, dict):
            return cfg_obj
    except Exception:
        pass
    return {}


def _get_cfg_path():
    try:
        import api.config as _ac
        fn = getattr(_ac, "_get_config_path", None)
        if callable(fn):
            return fn()
    except Exception:
        pass
    return None


def _load_yaml(path):
    try:
        import api.config as _ac
        fn = getattr(_ac, "_load_yaml_config_file", None)
        if callable(fn):
            return fn(path)
    except Exception:
        pass
    return {}


def _save_yaml(path, data) -> None:
    try:
        import api.config as _ac
        fn = getattr(_ac, "_save_yaml_config_file", None)
        if callable(fn):
            return fn(path, data)
    except Exception:
        pass


def _cfg_lock_cm():
    try:
        import api.config as _ac
        return _ac._cfg_lock
    except Exception:
        return threading.Lock()


def _reload_cfg() -> None:
    try:
        import api.config as _ac
        fn = getattr(_ac, "reload_config", None)
        if callable(fn):
            return fn()
    except Exception:
        pass


def _apply_advanced(slot_cfg, advanced) -> None:
    # Must propagate ValueError so invalid extra_body still fails closed.
    try:
        import api.config as _ac
        fn = getattr(_ac, "_apply_advanced_model_options", None)
        if callable(fn):
            return fn(slot_cfg, advanced)
    except ValueError:
        raise
    except Exception:
        pass
    try:
        import api._cfg.advanced as _adv
        fn = getattr(_adv, "_apply_advanced_model_options", None)
        if callable(fn):
            return fn(slot_cfg, advanced)
    except ValueError:
        raise
    except Exception:
        pass


def _main_supports_service_tier(model, provider) -> bool:
    try:
        import api.config as _ac
        fn = getattr(_ac, "_main_model_supports_service_tier", None)
        if callable(fn):
            return bool(fn(model, provider))
    except Exception:
        pass
    try:
        import api._cfg.advanced as _adv
        fn = getattr(_adv, "_main_model_supports_service_tier", None)
        if callable(fn):
            return bool(fn(model, provider))
    except Exception:
        pass
    return False


def _public_main_service_tier(model_cfg) -> str:
    try:
        import api.config as _ac
        fn = getattr(_ac, "_public_main_service_tier", None)
        if callable(fn):
            return str(fn(model_cfg) or "")
    except Exception:
        pass
    try:
        import api._cfg.advanced as _adv
        fn = getattr(_adv, "_public_main_service_tier", None)
        if callable(fn):
            return str(fn(model_cfg) or "")
    except Exception:
        pass
    return ""


def _public_advanced_opts(model_cfg) -> dict:
    try:
        import api.config as _ac
        fn = getattr(_ac, "_public_advanced_model_options", None)
        if callable(fn):
            return fn(model_cfg) or {}
    except Exception:
        pass
    try:
        import api._cfg.advanced as _adv
        fn = getattr(_adv, "_public_advanced_model_options", None)
        if callable(fn):
            return fn(model_cfg) or {}
    except Exception:
        pass
    return {}


def _resolve_model_provider(model_id: str):
    try:
        import api.config as _ac
        fn = getattr(_ac, "resolve_model_provider", None)
        if callable(fn):
            return fn(model_id)
    except Exception:
        pass
    try:
        import api._cfg.routing as _routing
        fn = getattr(_routing, "resolve_model_provider", None)
        if callable(fn):
            return fn(model_id)
    except Exception:
        pass
    return model_id, "", None


def _unique_entry(entries, slug_key: str):
    # Propagate AmbiguousCustomProviderError so collisions fail closed.
    _AmbCls = _AmbiguousError()
    try:
        import api.config as _ac
        fn = getattr(_ac, "_unique_custom_provider_entry", None)
        if callable(fn):
            return fn(entries, slug_key)
    except _AmbCls:
        raise
    except Exception:
        pass
    try:
        import api._cfg.custom_bundles as _cb
        fn = getattr(_cb, "_unique_custom_provider_entry", None)
        if callable(fn):
            return fn(entries, slug_key)
    except _AmbCls:
        raise
    except Exception:
        pass
    try:
        import api._cfg.routing as _routing
        fn = getattr(_routing, "_unique_custom_provider_entry", None)
        if callable(fn):
            return fn(entries, slug_key)
    except _AmbCls:
        raise
    except Exception:
        pass
    # Fallback: minimal uniqueness helper when neither module is available.
    # Must raise on collision, not silently return None.
    try:
        import api._cfg.provider_helpers as _ph

        fn = getattr(_ph, "_unique_custom_provider_entry", None)
        if callable(fn):
            return fn(entries, slug_key)
    except _AmbCls:
        raise
    except Exception:
        pass
    if not slug_key or not isinstance(entries, list):
        return None
    matches = []
    for ent in entries:
        if not isinstance(ent, dict):
            continue
        name = str(ent.get("name") or "").strip()
        if not name:
            continue
        slug = _slug_key(f"custom:{name}" if not name.lower().startswith("custom:") else name)
        # _slug_key returns bare slug; compare directly
        bare = slug.split(":", 1)[-1] if ":" in slug else slug
        if bare == slug_key:
            matches.append(ent)
        elif slug == slug_key:
            matches.append(ent)
    if len(matches) > 1:
        raise _AmbCls(f"Multiple custom providers normalize to the same slug: {slug_key!r}")
    return matches[0] if matches else None


def _slug_key(provider: str) -> str:
    try:
        import api.config as _ac
        fn = getattr(_ac, "_custom_provider_slug_key", None)
        if callable(fn):
            return str(fn(provider) or "")
    except Exception:
        pass
    try:
        import api._cfg.custom_bundles as _cb
        fn = getattr(_cb, "_custom_provider_slug_key", None)
        if callable(fn):
            return str(fn(provider) or "")
    except Exception:
        pass
    try:
        import api._cfg.routing as _routing
        fn = getattr(_routing, "_custom_provider_slug_key", None)
        if callable(fn):
            return str(fn(provider) or "")
    except Exception:
        pass
    return str(provider or "").strip().lower().replace("custom:", "")


def _AmbiguousError():
    try:
        import api.config as _ac
        err = getattr(_ac, "AmbiguousCustomProviderError", None)
        if err is not None:
            return err
    except Exception:
        pass
    try:
        import api._cfg.routing as _routing
        err = getattr(_routing, "AmbiguousCustomProviderError", None)
        if err is not None:
            return err
    except Exception:
        pass
    try:
        import api._cfg.custom_bundles as _cb
        err = getattr(_cb, "AmbiguousCustomProviderError", None)
        if err is not None:
            return err
    except Exception:
        pass
    return Exception

# ── Auxiliary model configuration ──────────────────────────────────────────

# Canonical auxiliary task catalog.
# Keep in sync with hermes_cli/config.py DEFAULT_CONFIG["auxiliary"] and
# hermes_cli/web_server.py _AUX_TASK_SLOTS.
AUXILIARY_TASK_CATALOG: tuple[dict[str, str], ...] = (
    {"key": "vision", "label": "Vision", "description": "image/screenshot analysis"},
    {"key": "web_extract", "label": "Web extract", "description": "web page summarization"},
    {"key": "compression", "label": "Compression", "description": "context summarization"},
    {"key": "approval", "label": "Approval", "description": "smart command approval"},
    {"key": "mcp", "label": "MCP", "description": "MCP tool reasoning"},
    {"key": "title_generation", "label": "Title generation", "description": "session titles"},
    {"key": "skills_hub", "label": "Skills hub", "description": "skills search/install"},
    {"key": "curator", "label": "Curator", "description": "skill-usage review pass"},
    {"key": "kanban_decomposer", "label": "Kanban decomposer", "description": "task decomposition"},
    {"key": "profile_describer", "label": "Profile describer", "description": "profile summaries"},
    {"key": "triage_specifier", "label": "Triage specifier", "description": "issue/task triage specs"},
)

AUX_TASK_SLOTS: tuple[str, ...] = tuple(item["key"] for item in AUXILIARY_TASK_CATALOG)

# Slots removed from the WebUI catalog whose persisted assignments should be
# discarded when the user explicitly resets all auxiliary-model routing.
RETIRED_AUX_TASK_SLOTS: tuple[str, ...] = ("session_search",)


def _aux_task_payload(task_key: str, entry: dict, fallback_label: str = "", fallback_description: str = "") -> dict:
    """Build the API payload row for a single auxiliary task."""
    if not isinstance(entry, dict):
        entry = {}
    return {
        "task": task_key,
        "provider": str(entry.get("provider") or "auto").strip() or "auto",
        "model": str(entry.get("model") or "").strip(),
        "base_url": str(entry.get("base_url") or "").strip(),
        "timeout": entry.get("timeout", ""),
        "download_timeout": entry.get("download_timeout", ""),
        "max_concurrency": entry.get("max_concurrency", ""),
        "extra_body": entry.get("extra_body") if isinstance(entry.get("extra_body"), dict) else {},
        "api_key_set": bool(str(entry.get("api_key") or "").strip()),
        "label": fallback_label,
        "description": fallback_description,
    }


def _iter_auxiliary_task_rows() -> list[dict]:
    """Return canonical auxiliary task payload rows."""
    aux_cfg = _cfg().get("auxiliary", {})
    if not isinstance(aux_cfg, dict):
        aux_cfg = {}

    rows: list[dict] = []

    # Canonical, first-class tasks from WebUI's catalog.
    for slot in AUXILIARY_TASK_CATALOG:
        key = str(slot["key"]).strip()
        if not key:
            continue
        rows.append(_aux_task_payload(key, aux_cfg.get(key, {}), slot["label"], slot["description"]))

    return rows


def get_auxiliary_models() -> dict:
    """Return current auxiliary task assignments from config.yaml.

    Shape:
    {
        "tasks": [
            {"task": "vision", "provider": "auto", "model": "", "base_url": ""},
            ...
        ],
        "main": {"provider": "openrouter", "model": "anthropic/claude-opus-4.7", "service_tier": ""},
    }
    """
    _reload_cfg()
    model_cfg = _cfg().get("model", {})
    if not isinstance(model_cfg, dict):
        model_cfg = {}
    main_provider = str(model_cfg.get("provider") or "").strip()
    main_model = str(model_cfg.get("default") or model_cfg.get("name") or "").strip()

    tasks = _iter_auxiliary_task_rows()

    return {
        "tasks": tasks,
        "main": {
            "provider": main_provider,
            "model": main_model,
            "supports_fast_tier": _main_supports_service_tier(main_model, main_provider),
            "service_tier": _public_main_service_tier(model_cfg),
            **_public_advanced_opts(model_cfg),
        },
    }


def _provider_native_auxiliary_model(provider: str, model: str) -> str:
    """Return the provider-native model stored by an auxiliary slot.

    ``@provider:model`` is a WebUI picker routing token. Auxiliary slots already
    store the selected provider separately, so only an exact matching prefix is
    safe to remove. Reject other qualified forms instead of persisting an
    ambiguous upstream model name.
    """
    provider_id = str(provider or "").strip() or "auto"
    model_id = str(model or "").strip()
    if not model_id.startswith("@") or ":" not in model_id:
        return model_id

    matching_prefix = f"@{provider_id}:"
    if provider_id != "auto" and model_id.startswith(matching_prefix):
        native_model = model_id[len(matching_prefix) :]
        if native_model:
            return native_model

    raise ValueError(
        "provider-qualified auxiliary model must match the selected provider "
        "and include a model name"
    )


def set_auxiliary_model(task: str, provider: str, model: str, advanced: dict | None = None) -> dict:
    """Persist an auxiliary model assignment in config.yaml.

    Special case: task='__reset__' clears all auxiliary slots.
    ``advanced`` may update per-slot fields surfaced behind the WebUI gear menu.
    Sensitive api_key values are write-only: get_auxiliary_models() only reports
    whether one is set.
    """
    provider = str(provider or "").strip() or "auto"
    model = str(model or "").strip()
    config_path = _get_cfg_path()
    with _cfg_lock_cm():
        config_data = _load_yaml(config_path)
        if task != "__reset__" and task not in AUX_TASK_SLOTS:
            raise ValueError(f"Unknown auxiliary task slot: {task!r}. Valid: {list(AUX_TASK_SLOTS)}")
        if task == "__reset__":
            # Per-slot reset: set each slot to auto, preserving extra fields
            # (timeout, extra_body, api_key, base_url, download_timeout, etc.)
            aux_cfg = config_data.get("auxiliary", {})
            if not isinstance(aux_cfg, dict):
                aux_cfg = {}
            for retired_slot in RETIRED_AUX_TASK_SLOTS:
                aux_cfg.pop(retired_slot, None)
            for slot in AUX_TASK_SLOTS:
                slot_cfg = aux_cfg.get(slot, {})
                if not isinstance(slot_cfg, dict):
                    slot_cfg = {}
                slot_cfg["provider"] = "auto"
                slot_cfg["model"] = ""
                aux_cfg[slot] = slot_cfg
            config_data["auxiliary"] = aux_cfg
        else:
            aux_cfg = config_data.get("auxiliary", {})
            if not isinstance(aux_cfg, dict):
                aux_cfg = {}
            model = _provider_native_auxiliary_model(provider, model)
            slot_cfg = aux_cfg.get(task, {})
            if not isinstance(slot_cfg, dict):
                slot_cfg = {}
            slot_cfg["provider"] = provider
            slot_cfg["model"] = model
            if provider and (provider.startswith("custom:") or provider == "custom"):
                # Resolve the auxiliary slot's base_url against the SELECTED
                # provider, not the active main provider. A bare
                # _resolve_model_provider(model) ignores `provider` and routes the
                # model through whatever main provider is active — so when the
                # selected auxiliary provider (custom:A) and the active main
                # provider (custom:B) both list the same model id, the slot was
                # persisted with provider=custom:A but base_url=B's endpoint
                # (overlapping-id misroute, sibling of the resolve_model_provider
                # fix). For a named custom:<slug> selection, look up that
                # provider's OWN custom_providers[] entry directly. Note we do
                # NOT route through model_with_provider_context here: its
                # @custom:<slug>:model form re-resolves against the AMBIENT
                # module-level `cfg`, while this path must read the in-lock
                # `config_data` snapshot it is about to write. Those two can
                # disagree whenever the cache is stale or the profile path
                # changed, and ambient/global resolution while holding the
                # non-reentrant _cfg_lock is exactly what the direct lookup below
                # exists to avoid. (That qualified form DOES resolve a custom
                # entry's own base_url as of the non-active #1806 fix -- the
                # reason to stay direct here is the lock/snapshot, not a URL the
                # qualified path cannot produce.) Fall back to the bare resolve
                # only for the unnamed `custom` case, which has no own entry.
                resolved_base_url = None
                if provider.startswith("custom:"):
                    # Resolve the selected provider's base_url from the
                    # config_data already loaded under _cfg_lock above. Do NOT
                    # call resolve_custom_provider_connection() / get_config()
                    # here: they re-acquire the non-reentrant _cfg_lock we
                    # already hold, self-deadlocking whenever the cache is stale
                    # or the profile path changed. Use the shared uniqueness
                    # helper on the in-scope dict so this slug-only save fails
                    # closed on a collision (raises _AmbiguousError())
                    # exactly like every other path — otherwise the ambiguity
                    # would be swallowed and the wrong endpoint persisted.
                    _cp_match = _unique_entry(
                        config_data.get("custom_providers", []),
                        _slug_key(provider),
                    )
                    if _cp_match is not None:
                        resolved_base_url = str(_cp_match.get("base_url") or "").strip() or None
                if not resolved_base_url:
                    # Best-effort fallback for the unnamed `custom` case (no own
                    # entry). Keep it non-fatal for unexpected errors, but let a
                    # genuine ambiguity propagate so the save fails closed.
                    try:
                        _, _, resolved_base_url = _resolve_model_provider(model)
                    except _AmbiguousError():
                        raise
                    except Exception:
                        resolved_base_url = None
                if resolved_base_url:
                    slot_cfg["base_url"] = str(resolved_base_url).strip().rstrip("/")
            if advanced is not None:
                try:
                    _apply_advanced(slot_cfg, advanced)
                except ValueError as exc:
                    msg = str(exc).replace("advanced model options", "advanced auxiliary options")
                    raise ValueError(msg) from exc
            aux_cfg[task] = slot_cfg
            config_data["auxiliary"] = aux_cfg

        _save_yaml(config_path, config_data)

    _reload_cfg()
    return {"ok": True, "task": task, "provider": provider, "model": model}


