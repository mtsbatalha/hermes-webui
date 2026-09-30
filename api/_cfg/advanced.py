"""Advanced model options + OpenAI fast-tier helpers extracted from api/config.py.

Re-exported via ``api.config`` so ``from api.config import _public_advanced_model_options``
keeps working.  No external module should import from ``api._cfg.advanced`` directly.
"""

from __future__ import annotations

import copy
import json
import logging
import threading

logger = logging.getLogger(__name__)


def _resolve_alias(provider_id: str) -> str:
    try:
        import api.config as _ac
        fn = getattr(_ac, "_resolve_provider_alias", None)
        if callable(fn):
            return str(fn(provider_id) or "").strip().lower()
    except Exception:
        pass
    try:
        import api._cfg.provider_helpers as _ph
        fn = getattr(_ph, "_resolve_provider_alias", None)
        if callable(fn):
            return str(fn(provider_id) or "").strip().lower()
    except Exception:
        pass
    return str(provider_id or "").strip().lower()


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


def _invalidate_models_cache() -> None:
    try:
        import api.config as _ac
        fn = getattr(_ac, "invalidate_models_cache", None)
        if callable(fn):
            return fn()
    except Exception:
        pass


def _coerce_optional_positive_int(value, field: str):
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        if value == "":
            return ""
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a positive integer") from exc
    if number < 1:
        raise ValueError(f"{field} must be a positive integer")
    return number

def _public_advanced_model_options(model_cfg: dict) -> dict:
    """Return write-only-safe advanced options from a model config block."""
    if not isinstance(model_cfg, dict):
        model_cfg = {}
    return {
        "base_url": str(model_cfg.get("base_url") or "").strip(),
        "timeout": model_cfg.get("timeout", ""),
        "download_timeout": model_cfg.get("download_timeout", ""),
        "max_concurrency": model_cfg.get("max_concurrency", ""),
        "extra_body": model_cfg.get("extra_body") if isinstance(model_cfg.get("extra_body"), dict) else {},
        "api_key_set": bool(str(model_cfg.get("api_key") or "").strip()),
    }


def _is_openai_family_provider(provider: str | None) -> bool:
    """Return True when a provider should receive OpenAI-family request overrides."""
    if not provider:
        return False
    resolved = str(_resolve_alias(str(provider).strip().lower()))
    return resolved in ("openai", "openai-api", "openai-codex")


def _normalize_openai_family_model_id(model_id: str | None) -> str:
    """Return a model id in the form expected by hermes_cli fast-mode resolution."""
    model = str(model_id or "").strip()
    if not model:
        return ""

    if model.startswith("@") and ":" in model:
        model = model.split(":", 1)[1].strip()

    if "://" in model:
        return model

    if "/" in model:
        provider_hint, candidate = model.split("/", 1)
        if provider_hint.strip().lower() in {"openai", "openai-api", "openai-codex"}:
            model = candidate.strip()
        else:
            return ""

    return model


def _legacy_openai_service_tier_overrides(model_id: str | None, provider: str | None) -> dict:
    """Compatibility fallback for standalone WebUI installs without hermes_cli.

    Normal operation delegates to Hermes Agent model metadata.  This fallback
    preserves the old WebUI behavior when the agent package is unavailable,
    while still failing closed for codex model slugs and foreign provider IDs.
    """
    if not _is_openai_family_provider(provider):
        return {}
    resolved_provider = str(_resolve_alias(str(provider or "").strip().lower()))
    raw_model = str(model_id or "").strip()
    if "://" not in raw_model and "/" in raw_model:
        provider_hint = raw_model.split("/", 1)[0].strip().lower()
        if provider_hint not in {"openai", "openai-api", "openai-codex"}:
            return {}
    normalized_model = _normalize_openai_family_model_id(model_id)
    if not normalized_model:
        if resolved_provider == "openai-codex":
            return {}
        return {"service_tier": "priority"}
    lowered = normalized_model.lower()
    if "codex" in lowered:
        return {}
    if lowered.startswith(("gpt-", "o1", "o3", "o4")):
        return {"service_tier": "priority"}
    return {}


def _resolve_main_model_fast_mode_overrides(model_id: str | None, provider: str | None = None) -> dict:
    """Return provider request overrides for the main model fast-mode setting."""
    normalized_model = _normalize_openai_family_model_id(model_id)
    if not normalized_model:
        return _legacy_openai_service_tier_overrides(model_id, provider)
    try:
        from hermes_cli.models import resolve_fast_mode_overrides
    except Exception:
        logger.debug("Failed to import hermes_cli.models.resolve_fast_mode_overrides; using WebUI compatibility fallback.")
        return _legacy_openai_service_tier_overrides(model_id, provider)
    try:
        resolved = resolve_fast_mode_overrides(normalized_model)
    except Exception:
        logger.debug("Failed to resolve fast-mode overrides for %r; using WebUI compatibility fallback.", normalized_model)
        return _legacy_openai_service_tier_overrides(model_id, provider)
    return resolved if isinstance(resolved, dict) else {}


def _main_model_supports_service_tier(
    model_id: str | None,
    provider: str | None,
) -> bool:
    """Return True when the current main-model selection can use OpenAI service tier."""
    if not _is_openai_family_provider(provider):
        return False
    return (
        str(_resolve_main_model_fast_mode_overrides(model_id, provider).get("service_tier", "")).strip().lower()
        == "priority"
    )


def _model_supports_fast_tier_for_provider(model_id: str | None, provider: str | None) -> bool:
    """Return whether a provider/model entry supports WebUI's service-tier toggle."""
    return _main_model_supports_service_tier(model_id, provider)


def _annotate_fast_tier_model_groups(payload: dict | None) -> dict | None:
    """Add service-tier capability metadata to OpenAI-family model groups."""
    if not isinstance(payload, dict):
        return payload
    groups = payload.get("groups")
    if not isinstance(groups, list):
        return payload
    for group in groups:
        if not isinstance(group, dict):
            continue
        provider_id = str(group.get("provider_id") or "").strip()
        if not _is_openai_family_provider(provider_id):
            continue
        for bucket in ("models", "extra_models"):
            models = group.get(bucket)
            if not isinstance(models, list):
                continue
            for model in models:
                if not isinstance(model, dict):
                    continue
                model_id = str(model.get("id") or "").strip()
                if model_id:
                    model["supports_fast_tier"] = _model_supports_fast_tier_for_provider(model_id, provider_id)
    return payload


def _public_main_service_tier(model_cfg: dict) -> str:
    """Return the saved main-model service tier only for OpenAI-family providers."""
    if not isinstance(model_cfg, dict):
        return ""
    model_id = str(model_cfg.get("default") or model_cfg.get("name") or "").strip()
    provider = str(model_cfg.get("provider") or "").strip().lower()
    if not provider:
        _, provider, _ = _resolve_model_provider(model_id)
    if not _main_model_supports_service_tier(model_id, provider):
        return ""
    service_tier = str(model_cfg.get("service_tier") or "").strip().lower()
    return "priority" if service_tier == "priority" else ""


def _main_model_request_overrides(
    config_data: dict,
    effective_model: str | None = None,
    effective_provider: str | None = None,
) -> dict:
    """Return supported runtime request overrides for the main chat model.

    When *effective_model* / *effective_provider* are supplied, the
    service-tier gate checks those instead of the saved default model,
    so a per-session model switch to a non-OpenAI provider does not
    leak ``service_tier`` onto an unsupported request.
    """
    if not isinstance(config_data, dict):
        return {}
    model_cfg = config_data.get("model", {})
    if not isinstance(model_cfg, dict):
        return {}
    overrides = {}
    gate_model = effective_model
    gate_provider = effective_provider
    if not gate_model:
        gate_model = str(model_cfg.get("default") or model_cfg.get("name") or "").strip()
    if not gate_provider:
        gate_provider = str(model_cfg.get("provider") or "").strip().lower()
        if not gate_provider:
            _, gate_provider, _ = _resolve_model_provider(gate_model)
    if _main_model_supports_service_tier(gate_model, gate_provider):
        service_tier = str(model_cfg.get("service_tier") or "").strip().lower()
        if service_tier == "priority":
            overrides["service_tier"] = "priority"
    extra_body = model_cfg.get("extra_body")
    if isinstance(extra_body, dict) and extra_body:
        overrides["extra_body"] = copy.deepcopy(extra_body)
    return overrides


def _apply_advanced_model_options(model_cfg: dict, advanced: dict | None) -> None:
    """Apply supported advanced model options to a config block in-place."""
    if advanced is None:
        return
    if not isinstance(advanced, dict):
        raise ValueError("advanced model options must be an object")
    if "base_url" in advanced:
        base_url = str(advanced.get("base_url") or "").strip().rstrip("/")
        if base_url:
            model_cfg["base_url"] = base_url
        else:
            model_cfg.pop("base_url", None)
    for field in ("timeout", "download_timeout", "max_concurrency"):
        if field in advanced:
            coerced = _coerce_optional_positive_int(advanced.get(field), field)
            if coerced == "":
                model_cfg.pop(field, None)
            elif coerced is not None:
                model_cfg[field] = coerced
    if "extra_body" in advanced:
        extra_body = advanced.get("extra_body")
        if isinstance(extra_body, str):
            text = extra_body.strip()
            try:
                extra_body = json.loads(text) if text else {}
            except json.JSONDecodeError as exc:
                raise ValueError("extra_body must be valid JSON") from exc
        if extra_body in (None, ""):
            model_cfg.pop("extra_body", None)
        elif isinstance(extra_body, dict):
            if extra_body:
                model_cfg["extra_body"] = extra_body
            else:
                model_cfg.pop("extra_body", None)
        else:
            raise ValueError("extra_body must be a JSON object")
    if "service_tier" in advanced:
        service_tier = str(advanced.get("service_tier") or "").strip().lower()
        if not service_tier or service_tier == "default":
            model_cfg.pop("service_tier", None)
        elif service_tier == "priority":
            model_cfg["service_tier"] = "priority"
        else:
            raise ValueError("service_tier must be one of: default, priority")
    if advanced.get("api_key_clear"):
        model_cfg.pop("api_key", None)
    api_key = str(advanced.get("api_key") or "").strip()
    if api_key:
        model_cfg["api_key"] = api_key


def set_hermes_default_model(model_id: str, provider: str | None = None, advanced: dict | None = None) -> dict:
    """Persist the Hermes default model in config.yaml and reload runtime config."""
    selected_model = str(model_id or "").strip()
    if not selected_model:
        raise ValueError("model is required")

    config_path = _get_cfg_path()
    # Hold _cfg_lock only around the read-modify-write of the YAML file.
    # _reload_cfg() acquires _cfg_lock internally (it's not reentrant) so
    # it must be called AFTER releasing the lock to avoid deadlock.
    with _cfg_lock_cm():
        config_data = _load_yaml(config_path)
        model_cfg = config_data.get("model", {})
        if not isinstance(model_cfg, dict):
            model_cfg = {}

        previous_provider = str(model_cfg.get("provider") or "").strip()
        requested_provider = str(provider or "").strip()
        resolved_model, resolved_provider, resolved_base_url = _resolve_model_provider(
            selected_model
        )
        # Persist the resolved bare/slash form, NOT the `@provider:` prefix. The
        # prefix is a WebUI-internal routing hint that the hermes-agent CLI does
        # not understand — if we wrote `@nous:anthropic/claude-opus-4.6` to
        # config.yaml, a user who ran `hermes` in the terminal right after
        # saving via WebUI would have the agent send that literal string to the
        # Nous API, which would reject it (Nous expects `anthropic/claude-opus-4.6`,
        # not the prefixed form). The Settings picker handles the resulting
        # CLI-shaped bare form via `_applyModelToDropdown()`'s normalising
        # matcher — see `static/panels.js` (#895).
        persisted_model = str(resolved_model or selected_model).strip()
        persisted_provider = str(requested_provider or resolved_provider or previous_provider or "").strip()
        provider_override_won = bool(requested_provider and requested_provider != str(resolved_provider or "").strip())
        # Never persist the bogus ``local`` value — see #1384. The auto-detect
        # block in ``_build_available_models_uncached`` was rewriting unknown
        # loopback hosts to ``provider: "local"``, which is not registered and
        # broke compression/vision mid-conversation. Route through ``custom``
        # so the agent's auxiliary client uses the ``no-key-required`` path.
        if persisted_provider.lower() == "local":
            persisted_provider = "custom"

        model_cfg["default"] = persisted_model
        if persisted_provider:
            model_cfg["provider"] = persisted_provider

        if resolved_base_url and not provider_override_won:
            model_cfg["base_url"] = str(resolved_base_url).strip().rstrip("/")
        elif persisted_provider != previous_provider:
            if persisted_provider == "openai":
                model_cfg["base_url"] = "https://api.openai.com/v1"
            else:
                # Provider changed and we have no resolved URL for the new one.
                # Drop the previous provider's base_url so New Chat doesn't route
                # to the old endpoint — this MUST also cover custom:* providers
                # (a different custom provider has a different URL); leaving the
                # stale base_url sent requests to the wrong host (#4728).
                model_cfg.pop("base_url", None)

        _apply_advanced_model_options(model_cfg, advanced)
        if not _main_model_supports_service_tier(persisted_model, persisted_provider):
            model_cfg.pop("service_tier", None)

        config_data["model"] = model_cfg
        _save_yaml(config_path, config_data)
    # Reload outside the lock — _reload_cfg() acquires _cfg_lock itself.
    _reload_cfg()
    # Invalidate the TTL cache so the next /api/models call returns fresh data
    # with the new default model. Do NOT call get_available_models() here —
    # it triggers a live provider fetch (up to 8s) that blocks the HTTP response
    # to the browser, causing a visible freeze on every Settings save (#895).
    _invalidate_models_cache()
    return {"ok": True, "model": persisted_model, "provider": persisted_provider or None}


