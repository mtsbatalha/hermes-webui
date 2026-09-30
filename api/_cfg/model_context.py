"""Model-context helpers extracted from api/config.py.

Re-exported via ``api.config`` so ``from api.config import
model_with_provider_context`` keeps working.  No external module should import
from ``api._cfg.model_context`` directly.

Runtime config/catalog reads are lazy (``import api.config as _ac`` inside
each function) so the module can be imported before ``api.config`` finishes
initialization and so tests that ``monkeypatch.setattr(config, \"cfg\", ...)``
see the override.
"""

from __future__ import annotations

import os

_ACP_SUBPROCESS_PROVIDERS = frozenset({"cursor-acp", "copilot-acp"})


def model_with_provider_context(model_id: str, model_provider: str | None = None) -> str:
    """Return the model string to pass to ``resolve_model_provider()``.

    Session persistence keeps the user's selected provider in ``model_provider``
    instead of forcing every selected model into ``@provider:model`` form. At
    runtime, however, ``resolve_model_provider()`` still understands that
    internal disambiguation form, so use it only when the provider context is
    needed to route away from the current default provider.
    """
    import api.config as _ac

    model = str(model_id or "").strip()
    provider = str(model_provider or "").strip().lower()
    if not model or not provider or provider == "default" or model.startswith("@"):
        return model

    cfg = getattr(_ac, "cfg", {}) or {}
    model_cfg = cfg.get("model", {}) if isinstance(cfg, dict) else {}
    config_provider = None
    if isinstance(model_cfg, dict):
        config_provider = str(model_cfg.get("provider") or "").strip().lower()

    # ACP subprocess providers always need the explicit hint — their slash IDs
    # are not OpenRouter paths and must not inherit config_provider routing.
    if provider in _ACP_SUBPROCESS_PROVIDERS:
        return f"@{provider}:{model}"

    # Plugin-only model providers route through the plugin, not the default.
    _is_plugin = getattr(_ac, "_is_plugin_model_provider", None)
    if callable(_is_plugin) and _is_plugin(provider):
        return f"@{provider}:{model}"
    # Fallback direct import when api.config hasn't yet re-exported the helper
    # (import-time ordering). Mirrors the pre-extract inline import.
    if _is_plugin is None:
        try:
            from api.plugin_providers import is_plugin_model_provider as _direct_is_plugin

            if _direct_is_plugin(provider):
                return f"@{provider}:{model}"
        except Exception:
            pass

    # Codex live/cache models are intentionally absent from the static catalog.
    if provider == "openai-codex":
        return f"@{provider}:{model}"

    # If the selected provider is already the configured provider, leaving the
    # model bare preserves provider-specific base_url/proxy settings.
    if provider == config_provider:
        return model

    # OpenRouter selections with slash IDs are explicit provider/model paths.
    if provider == "openrouter":
        return f"@{provider}:{model}"

    # Explicit providers configured in config.yaml must keep their hint.
    providers_cfg = cfg.get("providers") if isinstance(cfg, dict) else {}
    if isinstance(providers_cfg, dict) and provider in providers_cfg:
        return f"@{provider}:{model}"

    # For non-OpenRouter slash IDs without an explicit configured provider,
    # keep the ID intact unless the session provider is a known routable one.
    if "/" in model:
        _provider_models = getattr(_ac, "_PROVIDER_MODELS", {}) or {}
        _provider_display = getattr(_ac, "_PROVIDER_DISPLAY", {}) or {}
        if provider in _provider_models or provider in _provider_display:
            return f"@{provider}:{model}"
        if provider.startswith("custom:"):
            custom_providers = cfg.get("custom_providers") if isinstance(cfg, dict) else []
            # Lazy resolver — api.config re-exports this from routing.
            _unique_entry = getattr(_ac, "_unique_custom_provider_entry", None)
            _slug_key = getattr(_ac, "_custom_provider_slug_key", None)
            if callable(_unique_entry) and callable(_slug_key):
                # Fail closed on slug collision (AmbiguousCustomProviderError)
                # so the caller surfaces the rename fix, matching the inline
                # pre-extract behaviour.
                if _unique_entry(custom_providers, _slug_key(provider)) is not None:
                    return f"@{provider}:{model}"
            else:
                # Fallback direct import when api.config bridge not yet ready.
                from api._cfg.routing import (
                    _custom_slug_key as _direct_slug_key,
                    _unique_custom_provider_entry_routing as _direct_unique,
                )

                # Also fail closed here — do not swallow the ambiguity.
                if _direct_unique(custom_providers, _direct_slug_key(provider)) is not None:
                    return f"@{provider}:{model}"
        return model

    return f"@{provider}:{model}"


def canonical_model_provider_lane(model_id: str, model_provider: str | None = None) -> tuple[str, str | None]:
    """Return the runtime-resolved model/provider pair used for lane comparisons."""
    import api.config as _ac

    model = str(model_id or "").strip()
    provider = str(model_provider or "").strip() or None
    if not model:
        return "", provider
    # Resolve via the canonical provider resolver. Prefer the api.config bridge
    # so tests that monkeypatch resolve_model_provider see the override.
    _resolve = getattr(_ac, "resolve_model_provider", None)
    # model_with_provider_context is now in this module; use the local copy
    # rather than _ac.model_with_provider_context to avoid an extra hop, but
    # keep the bridge as fallback when api.config has already rebound it.
    _mctx = getattr(_ac, "model_with_provider_context", None)
    if callable(_mctx) and _mctx is not model_with_provider_context:
        ctx_model = _mctx(model, provider)
    else:
        ctx_model = model_with_provider_context(model, provider)
    if callable(_resolve):
        resolved_model, resolved_provider, _ = _resolve(ctx_model)
    else:
        from api._cfg.routing import resolve_model_provider as _direct_resolve

        resolved_model, resolved_provider, _ = _direct_resolve(ctx_model)
    resolved_provider = str(resolved_provider or "").strip() or None
    return str(resolved_model or "").strip(), resolved_provider


def get_effective_default_model(config_data: dict | None = None) -> str:
    """Resolve the effective Hermes default model from config, then env overrides."""
    import api.config as _ac

    if config_data is not None:
        active_cfg = config_data
    else:
        active_cfg = getattr(_ac, "cfg", {}) or {}
    # DEFAULT_MODEL lives in api._cfg.workspace but is re-exported via api.config.
    default_model = getattr(_ac, "DEFAULT_MODEL", "") or ""
    try:
        # Fallback direct import when bridge not yet wired.
        if not default_model:
            from api._cfg.workspace import DEFAULT_MODEL as _direct_default

            default_model = _direct_default or ""
    except Exception:
        pass

    model_cfg = active_cfg.get("model", {}) if isinstance(active_cfg, dict) else {}
    if isinstance(model_cfg, str):
        default_model = model_cfg.strip()
    elif isinstance(model_cfg, dict):
        cfg_default = str(model_cfg.get("default") or "").strip()
        if cfg_default:
            default_model = cfg_default

    env_model = (
        os.getenv("HERMES_MODEL") or os.getenv("OPENAI_MODEL") or os.getenv("LLM_MODEL")
    )
    if env_model:
        default_model = env_model.strip()
    return default_model
