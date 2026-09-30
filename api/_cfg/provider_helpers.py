"""Provider helpers extracted from api/config.py.

Re-exported via ``api.config`` so ``from api.config import _resolve_provider_alias`` keeps
working.  No external module should import from ``api._cfg.provider_helpers`` directly.

Runtime context (catalog, cfg, thread-env) is read lazily from ``api.config`` so the
module can be imported before ``api.config`` finishes its own initialization.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_LEGACY_CUSTOM_API_KEY_ENV_WARNED: set[str] = set()

# Helpers to read shared state lazily (avoids import-time circularity).
def _cfg_dict() -> dict:
    try:
        import api.config as _cfg
        return getattr(_cfg, "cfg", {}) or {}
    except Exception:
        return {}

def _catalog(name: str):
    try:
        import api.config as _cfg
        return getattr(_cfg, name, {})
    except Exception:
        return {}

def _thread_local_env_value(name: str, default: str = "") -> str:  # type: ignore[override]
    try:
        import api.config as _cfg
        fn = getattr(_cfg, "_thread_local_env_value", None)
        if callable(fn):
            return fn(name, default)
    except Exception:
        pass
    import os as _os
    return str(_os.getenv(name, default or ""))

def _is_plugin_model_provider(pid: str) -> bool:  # type: ignore[override]
    try:
        from api.plugin_providers import is_plugin_model_provider as _impl
        return bool(_impl(pid))
    except Exception:
        return False

def _get_anthropic_fallback_env_vars() -> tuple[str, ...]:
    """Read Anthropic auth env vars from the shared agent registry when available."""
    fallback = (
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_TOKEN",
        "CLAUDE_CODE_OAUTH_TOKEN",
    )
    try:
        from hermes_cli.auth import PROVIDER_REGISTRY

        anthropic = (
            PROVIDER_REGISTRY.get("anthropic")
            if isinstance(PROVIDER_REGISTRY, dict)
            else None
        )
        env_vars = getattr(anthropic, "api_key_env_vars", None)
        if not env_vars:
            return fallback

        out = []
        for _var in env_vars:
            if not isinstance(_var, str):
                continue
            _normalized = _var.strip()
            if _normalized and _normalized not in out:
                out.append(_normalized)
        return tuple(out) if out else fallback
    except Exception:
        return fallback


def _resolve_provider_alias(name: str) -> str:
    """Return the canonical provider slug for *name*.

    Applies the WebUI's local alias table first, then merges any
    additional aliases the agent provides (when hermes_cli is on
    sys.path). Lookup is case-insensitive and whitespace-trimmed.
    Unknown names pass through unchanged.
    """
    if not name:
        return name
    raw = str(name).strip().lower()
    # Prefer the agent's table when available so new aliases added there
    # work automatically; otherwise fall through to our local copy.
    try:
        from hermes_cli.models import _PROVIDER_ALIASES as _agent_aliases
        if raw in _agent_aliases:
            return _agent_aliases[raw]
    except Exception:
        pass
    return _catalog("_PROVIDER_ALIASES").get(raw, name)


def _is_known_model_provider(provider_id: str) -> bool:
    """True when *provider_id* names a model provider WebUI can render.

    The credential pool (``auth.json`` → ``credential_pool``) stores keys for
    BOTH model providers (whose API keys belong in the model picker) and
    non-model platform plugins.  The Photon iMessage plugin, for example,
    writes ``photon`` / ``photon_project`` / ``photon_user`` pool entries that
    are messaging-platform credentials, not LLM API keys.  Only the former
    should surface as provider groups.

    Without this gate, #4247's pool-detection loop added *every* pool key to
    ``detected_providers``; unknown ids then fell through to the global
    auto-detected catalog and each phantom provider was painted with the full
    model list (#4324).  ``provider_id`` is expected to be the canonical slug
    (post ``_resolve_provider_alias``); the lookup is case-insensitive.

    A provider is "known" when it is a configured custom-provider slug
    (``custom:*``), appears in WebUI's static ``_PROVIDER_DISPLAY`` /
    ``_PROVIDER_MODELS`` tables, or is a registered model-provider plugin.
    """
    pid = (provider_id or "").strip().lower()
    if not pid:
        return False
    if pid.startswith("custom:"):
        return True
    if pid in _catalog("_PROVIDER_DISPLAY") or pid in _catalog("_PROVIDER_MODELS"):
        return True
    try:
        if _is_plugin_model_provider(pid):
            return True
    except Exception:
        # A transient failure here (import/IO hiccup in the plugin registry) makes
        # a real plugin-backed provider briefly look unknown and drop from the
        # picker until the next successful check. Surface at warning so it's
        # visible in default production logs rather than silently swallowed.
        logger.warning("plugin model-provider check failed for %s", pid, exc_info=True)
    return False


def _custom_provider_slug_from_name(name: object) -> str:
    raw = str(name or "").strip().lower()
    if not raw:
        return ""
    if raw.startswith("custom:"):
        return raw
    # Keep name-derived custom provider slugs out of the @provider:model colon
    # grammar. Endpoint-derived slugs may still be custom:<host>:<port>, but a
    # friendly name like "Local (127.0.0.1:15721)" should not preserve ':'.
    slug = re.sub(r"[^a-z0-9._-]+", "-", raw).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    if not slug:
        return ""
    return "custom:" + slug


def _custom_provider_entries(config_obj: dict | None = None) -> list[dict]:
    source = config_obj if isinstance(config_obj, dict) else _cfg_dict()
    entries = source.get("custom_providers", [])
    if not isinstance(entries, list):
        return []
    return [entry for entry in entries if isinstance(entry, dict)]


def _configured_model_ids(raw_models: object) -> list[str]:
    """Return ordered model IDs from supported config allowlist shapes."""
    if isinstance(raw_models, dict):
        candidates = (key for key in raw_models if isinstance(key, str))
    elif isinstance(raw_models, list):
        candidates = raw_models
    else:
        return []

    model_ids: list[str] = []
    for item in candidates:
        if isinstance(item, dict):
            candidate = item.get("id") or item.get("model") or item.get("name")
        else:
            candidate = item
        model_id = str(candidate or "").strip()
        if model_id and model_id not in model_ids:
            model_ids.append(model_id)
    return model_ids


def _provider_discover_allowed(provider_cfg: object) -> bool:
    """Mirror the Hermes Agent ``discover_models`` opt-out (``model_switch_providers._discover_flag``).

    ``discover_models`` defaults to True; the string forms ``"false"``/``"no"``/``"0"``
    (case-insensitive) mean False. A provider that pins its catalog with
    ``discover_models: false`` keeps its configured ``models:`` even when the entry is
    also marked ``models_discovered: true`` — the explicit opt-out wins.
    """
    if not isinstance(provider_cfg, dict):
        return True
    discover = provider_cfg.get("discover_models", True)
    if isinstance(discover, str):
        return discover.strip().lower() not in {"false", "no", "0"}
    return bool(discover)


def _provider_models_are_discovered_catalog(provider_cfg: object) -> bool:
    """True when ``models:`` is an auto-discovered catalog that should defer to the live probe.

    A provider entry marked ``models_discovered: true`` carries a per-model *metadata*
    mapping written by Hermes discovery, not a hand-curated allowlist — so the live
    ``/v1/models`` catalog is authoritative. But an explicit ``discover_models: false``
    re-pins the configured mapping as the source of truth, so honor that opt-out.
    """
    return (
        isinstance(provider_cfg, dict)
        and provider_cfg.get("models_discovered") is True
        and _provider_discover_allowed(provider_cfg)
    )


def _configured_model_options(raw_models: object) -> list[dict[str, str]]:
    """Return picker option rows from supported config allowlist shapes."""
    labels: dict[str, str] = {}
    if isinstance(raw_models, list):
        for item in raw_models:
            if not isinstance(item, dict):
                continue
            candidate = item.get("id") or item.get("model") or item.get("name")
            model_id = str(candidate or "").strip()
            if not model_id or model_id in labels:
                continue
            label = str(item.get("label") or model_id).strip() or model_id
            labels[model_id] = label
    return [
        {"id": model_id, "label": labels.get(model_id, model_id)}
        for model_id in _configured_model_ids(raw_models)
    ]


def _merge_model_option_rows(*row_lists: object) -> list[dict[str, str]]:
    """Merge picker option rows from multiple sources, first-seen order, deduped by id.

    Used to preserve a discovered provider's configured model IDs (ordered first) as a
    fallback when a live ``/v1/models`` probe transiently returns nothing, merged with
    any static built-in catalog without producing duplicate ids.
    """
    merged: list[dict[str, str]] = []
    seen: set[str] = set()
    for rows in row_lists:
        if not isinstance(rows, (list, tuple)):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            model_id = str(row.get("id") or "").strip()
            if not model_id or model_id in seen:
                continue
            seen.add(model_id)
            merged.append(row)
    return merged


def _named_custom_provider_slugs(config_obj: dict | None = None) -> set[str]:
    return {
        slug
        for slug in (
            _custom_provider_slug_from_name(entry.get("name"))
            for entry in _custom_provider_entries(config_obj)
        )
        if slug
    }


def _named_custom_provider_slug_for_provider(
    provider: object,
    config_obj: dict | None = None,
) -> str:
    raw = str(provider or "").strip().lower()
    if not raw:
        return ""
    raw_suffix = raw.removeprefix("custom:")
    for entry in _custom_provider_entries(config_obj):
        entry_name = str(entry.get("name") or "").strip().lower()
        slug = _custom_provider_slug_from_name(entry_name)
        if not entry_name or not slug:
            continue
        if raw in {entry_name, slug} or raw_suffix == slug.removeprefix("custom:"):
            return slug
    return ""


def _resolve_configured_provider_id(
    provider: object,
    config_obj: dict | None = None,
    *,
    base_url: object = None,
    resolve_alias: bool = True,
) -> str:
    """Normalize a configured provider id.

    When ``resolve_alias`` is True (default, used for active-provider /
    badge surfaces), falls through to ``_resolve_provider_alias`` after the
    named-custom check. When False (used by ``resolve_model_provider``),
    preserves the raw provider value so downstream local-server detection
    (`_LOCAL_SERVER_PROVIDERS` membership in #1625) sees the original name
    like ``ollama`` / ``lm-studio`` rather than alias-collapsed ``custom`` /
    ``lmstudio``. The base-url-to-named-slug fallback still runs in both
    modes when applicable.

    See in-stage absorption note on stage-313 for the #1625 regression that
    motivated the ``resolve_alias`` flag.
    """
    named_slug = _named_custom_provider_slug_for_provider(provider, config_obj)
    if named_slug:
        return named_slug

    if not resolve_alias:
        raw = str(provider or "").strip().lower()
        if base_url and raw == "custom":
            by_base_url = _named_custom_provider_slug_for_base_url(base_url, config_obj)
            if by_base_url:
                return by_base_url
        return str(provider or "")

    resolved = _resolve_provider_alias(provider)
    if (
        base_url
        and str(resolved or "").strip().lower() == "custom"
    ):
        by_base_url = _named_custom_provider_slug_for_base_url(base_url, config_obj)
        if by_base_url:
            return by_base_url

    return resolved


def _canonicalise_provider_id(name: object) -> str:
    """Normalise a provider id slug into a stable lowercase-hyphenated form.

    Folds underscores to hyphens and lowercases the result, so a user with
    ``providers.opencode_go.api_key`` in ``config.yaml`` and
    ``model.provider: opencode-go`` sees ONE provider group, not two
    (#1568). Then attempts alias resolution but only if the alias target
    is itself a known canonical id in ``_PROVIDER_DISPLAY`` —  this avoids
    converting ``x-ai`` (canonical in WebUI's data structures) to ``xai``
    (the hermes_cli alias target which the WebUI doesn't index by).

    Examples::

        opencode-go     -> opencode-go     (canonical, no change)
        opencode_go     -> opencode-go     (underscore folded)
        OpenCode-Go     -> opencode-go     (case folded)
        OPENCODE_GO     -> opencode-go     (both folded)
        z_ai            -> zai             (alias-resolved — zai is canonical)
        x-ai            -> x-ai            (preserved — x-ai is canonical)

    Empty input passes through as the empty string. Unknown ids preserve
    their normalised form.
    """
    if not name:
        return ""
    raw = str(name).strip().lower().replace("_", "-")
    if not raw:
        return ""
    # Already a canonical id known to _PROVIDER_DISPLAY/_PROVIDER_MODELS:
    # keep as-is to avoid round-tripping through aliases (e.g. x-ai → xai).
    if raw in _catalog("_PROVIDER_DISPLAY") or raw in _catalog("_PROVIDER_MODELS"):
        return raw
    # Try alias resolution. Accept the result if it's a canonical id known to
    # either _PROVIDER_DISPLAY OR _PROVIDER_MODELS (mirroring the direct-hit
    # check above) — some canonical targets (e.g. `gemini`) are indexed in
    # _PROVIDER_MODELS but not _PROVIDER_DISPLAY, so a _DISPLAY-only check
    # rejected valid aliases like `google-gemini`→`gemini`, leaving the id
    # uncanonicalised and silently breaking provider-ownership checks (#5511).
    # This still blocks aliases that point at non-canonical/legacy strings.
    resolved = _resolve_provider_alias(raw)
    if resolved and (resolved.lower() in _catalog("_PROVIDER_DISPLAY") or resolved.lower() in _catalog("_PROVIDER_MODELS")):
        return resolved.lower()
    return raw


def _normalize_base_url_for_match(value: object) -> str:
    url = str(value or "").strip().rstrip("/")
    if not url:
        return ""
    parsed_url = urlparse(url if "://" in url else f"http://{url}")
    scheme = (parsed_url.scheme or "http").lower()
    netloc = (parsed_url.netloc or parsed_url.path).lower().rstrip("/")
    path = parsed_url.path.rstrip("/")
    if not parsed_url.netloc:
        path = ""
    return f"{scheme}://{netloc}{path}"


def _custom_endpoint_slugs_for_base_url(value: object) -> set[str]:
    """Return custom provider slugs that WebUI may derive from a base URL.

    Model picker values for endpoint-discovered models have historically used
    both ``custom:<host>:<port>`` and ``custom:<host>-<port>`` forms. When the
    active config already names a local-server provider such as Ollama for that
    same base URL, those endpoint slugs are just UI routing hints and should
    resolve back to the configured provider rather than requiring a CUSTOM_* API
    key.
    """
    url = str(value or "").strip().rstrip("/")
    if not url:
        return set()
    parsed_url = urlparse(url if "://" in url else f"http://{url}")
    host = (parsed_url.hostname or "").strip().lower()
    if not host:
        return set()
    port = parsed_url.port
    if port is None:
        scheme = (parsed_url.scheme or "http").lower()
        port = 443 if scheme == "https" else 80
    return {f"custom:{host}:{port}", f"custom:{host}-{port}"}


_LEGACY_CUSTOM_API_KEY_ENV_WARNED: set[str] = set()


def _api_key_env_name(provider_id: object) -> str:
    """Return the POSIX-safe default API-key env var for a custom provider id."""
    sanitized = re.sub(r"[^A-Za-z0-9]", "_", str(provider_id or "")).upper().strip("_")
    if not sanitized:
        sanitized = "CUSTOM"
    if not sanitized.startswith("CUSTOM_"):
        sanitized = f"CUSTOM_{sanitized}"
    return f"{sanitized}_API_KEY"


def _legacy_custom_api_key_env_name(provider_id: object) -> str:
    """Return the pre-#2541 custom-provider env hint shape, if any."""
    raw = str(provider_id or "").strip().upper()
    if not raw:
        return ""
    return f"{raw}_API_KEY"


def _lookup_custom_api_key_env(provider_id: object) -> str | None:
    """Look up sanitized custom-provider env first, then legacy broken shape."""
    env_name = _api_key_env_name(provider_id)
    api_key = _thread_local_env_value(env_name).strip()
    if api_key:
        return api_key

    legacy_env_name = _legacy_custom_api_key_env_name(provider_id)
    if legacy_env_name and legacy_env_name != env_name:
        legacy_key = _thread_local_env_value(legacy_env_name).strip()
        if legacy_key:
            if legacy_env_name not in _LEGACY_CUSTOM_API_KEY_ENV_WARNED:
                _LEGACY_CUSTOM_API_KEY_ENV_WARNED.add(legacy_env_name)
                logger.warning(
                    "Custom provider API key env var %s is deprecated; use %s instead",
                    legacy_env_name,
                    env_name,
                )
            return legacy_key
    return None


def _named_custom_provider_slug_for_base_url(
    base_url: object,
    config_obj: dict | None = None,
) -> str:
    target = _normalize_base_url_for_match(base_url)
    if not target:
        return ""
    for entry in _custom_provider_entries(config_obj):
        entry_base_url = _normalize_base_url_for_match(entry.get("base_url"))
        if entry_base_url != target:
            continue
        return _custom_provider_slug_from_name(entry.get("name")) or "custom"
    return ""


def _provider_is_known_or_configured(
    provider_id: object,
    config_obj: dict | None = None,
) -> bool:
    """True when ``provider_id`` is a provider Hermes recognizes (static registry)
    or the user has configured (named custom provider), decided from the STATIC
    registry + config state only — never from a live/cold catalog snapshot.

    This distinguishes a provider Hermes knows how to route (e.g. ``ollama-cloud``,
    whose model group simply isn't folded into the current cached catalog yet, or a
    named ``custom_providers`` entry) from a *genuinely unknown* one
    (``@removed:...`` that is in no registry and configured nowhere). The former's
    explicitly-qualified selection is preserved across a cold catalog; the latter
    falls back to the default so chat/start doesn't route to an unrecognized
    provider.

    DELIBERATE SCOPE (see the @provider:model guard in
    ``_resolve_compatible_session_model_state``): registry membership counts as
    "known" even when the user has no key configured for that built-in. We do NOT
    require authenticated-credential evidence here, on purpose. The only fully
    reliable "is this provider authenticated" signal is the live auth store /
    catalog rebuild — exactly the cost the caller's ``prefer_cached_catalog`` hot
    path avoids — and a cheap env/config-only credential check would mis-classify
    providers authenticated via OAuth/auth-store (``ollama-cloud`` among them),
    re-introducing the original silent-revert bug for them. A known-but-unconfigured
    pick is therefore kept and surfaces a clear run-time auth error rather than a
    silent swap to the default.

    Deliberately does NOT consult ``get_available_models()`` / the catalog groups,
    which are exactly what is cold here — re-deriving them live would defeat the
    ``prefer_cached_catalog`` hot-path win this guards.
    """
    raw = str(provider_id or "").strip().lower()
    if not raw:
        return False
    # Configured custom provider: a named slug in custom_providers, or any
    # ``custom`` / ``custom:<slug>`` form when custom_providers are defined.
    if _named_custom_provider_slug_for_provider(raw, config_obj):
        return True
    if raw == "custom" or raw.startswith("custom:"):
        return bool(_custom_provider_entries(config_obj))
    # Known first-party / built-in provider id (alias-resolved). Static registry
    # knowledge that is always available, so a live-discovery provider whose
    # catalog group is momentarily absent still counts as known.
    canonical = _resolve_provider_alias(raw)
    return (
        raw in _catalog("_PROVIDER_DISPLAY")
        or canonical in _catalog("_PROVIDER_DISPLAY")
        or raw in _catalog("_PROVIDER_MODELS")
        or canonical in _catalog("_PROVIDER_MODELS")
    )


