"""Routing helpers extracted from api/config.py.

Re-exported via ``api.config`` so ``from api.config import resolve_model_provider``
keeps working.  No external module should import from ``api._cfg.routing`` directly.

Runtime config/catalog reads are lazy so the module can be imported before
``api.config`` finishes initialization.  Exception types are defined here so the
routing module is self-contained; api/config.py re-exports them.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlparse

# Lazy accessors for the live config / catalog state owned by api.config.
# Using functions instead of module globals avoids import-time circularity
# (api.config imports this module at ~line 918) and lets the hotspot helper
# ``_endpoint_advertised_model_ids`` read without holding _cfg_lock.

def _cfg() -> dict:
    try:
        import api.config as _ac
        return getattr(_ac, "cfg", {}) or {}
    except Exception:
        return {}

def _catalog(name: str):
    try:
        import api.config as _ac
        return getattr(_ac, name, {}) or {}
    except Exception:
        return {}  # type: ignore[return-value]

def _endpoint_ids(provider_id: str | None):
    try:
        import api.config as _ac
        fn = getattr(_ac, "_endpoint_advertised_model_ids", None)
        if callable(fn):
            return fn(provider_id)
    except Exception:
        pass
    return None

def _configured_ids(raw):
    try:
        import api.config as _ac
        fn = getattr(_ac, "_configured_model_ids", None)
        if callable(fn):
            return fn(raw)
    except Exception:
        pass
    return []

def _custom_entries():
    try:
        import api.config as _ac
        fn = getattr(_ac, "_custom_provider_entries", None)
        if callable(fn):
            return fn()
    except Exception:
        pass
    return []

def _custom_slug_from_name(name):
    try:
        import api.config as _ac
        fn = getattr(_ac, "_custom_provider_slug_from_name", None)
        if callable(fn):
            return fn(name)
    except Exception:
        pass
    return ""

def _canonicalise(pid):
    try:
        import api.config as _ac
        fn = getattr(_ac, "_canonicalise_provider_id", None)
        if callable(fn):
            return fn(pid)
    except Exception:
        pass
    return str(pid or "").strip().lower().replace("_", "-")

def _resolve_provider_cfg_id(provider, config_obj, base_url=None, resolve_alias=True):
    try:
        import api.config as _ac
        fn = getattr(_ac, "_resolve_configured_provider_id", None)
        if callable(fn):
            return fn(provider, config_obj, base_url=base_url, resolve_alias=resolve_alias)
    except Exception:
        pass
    return str(provider or "").strip().lower() if provider else ""

def _custom_slug_key(value: object) -> str:
    produced = _custom_slug_from_name(value)
    return produced.split(":", 1)[1] if produced.startswith("custom:") else produced

def _lookup_custom_api_key_env(provider_hint):
    try:
        import api.config as _ac
        fn = getattr(_ac, "_lookup_custom_api_key_env", None)
        if callable(fn):
            return fn(provider_hint)
    except Exception:
        pass
    return None


def _custom_endpoint_slugs_for_base_url(base_url: str | None) -> list[str]:
    try:
        import api.config as _ac
        fn = getattr(_ac, "_custom_endpoint_slugs_for_base_url", None)
        if callable(fn):
            return fn(base_url) or []
    except Exception:
        pass
    return []


class _AmbiguousCustomProviderError_routing_unused(ValueError):
    """Raised when two+ custom_providers[] entries normalize to the same slug."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message

_LOCAL_SERVER_PROVIDERS = {
    "lmstudio",     # canonical (in hermes_cli.models.CANONICAL_PROVIDERS)
    "lm-studio",    # alias used in some custom_providers configs (#1625 Opus NIT)
    "ollama",       # via custom_providers, common pattern
    "llamacpp",     # via custom_providers
    "llama-cpp",    # alias
    "vllm",         # via custom_providers
    "tabby",        # via custom_providers (TabbyAPI)
    "tabbyapi",     # alias
    "koboldcpp",    # local llama.cpp UI fork
    "textgen",      # text-generation-webui (oobabooga) OpenAI-compat extension
    "localai",      # LocalAI project (#1625 Opus NIT)
}


def _is_local_server_provider(provider_id: str) -> bool:
    """True when provider_id names a local model server.

    Named custom providers resolve to ``custom:<slug>``. Treat those as local
    when the bare slug is one of the known local-server provider names too.
    """
    provider = str(provider_id or "").strip().lower()
    if provider in _LOCAL_SERVER_PROVIDERS:
        return True
    if provider.startswith("custom:"):
        return provider.removeprefix("custom:") in _LOCAL_SERVER_PROVIDERS
    return False


def _model_id_declared_in_config(model_id: str, config_provider: str | None) -> bool:
    """True when the user's own config declares ``model_id`` verbatim (full form).

    This is the COLD-catalog provenance signal for #5979: when the live
    ``/v1/models`` catalog is unbuilt (fresh process, headless client), a
    vendor-namespaced id the user configured — ``model.default``, the
    ``model.models`` allowlist, or the matching ``custom_providers[].models`` /
    ``.model`` for a named ``custom:<slug>`` — is still authoritative provenance
    that the full id is intentional and must be preserved. Config is the one
    source available with zero network and no catalog dependency, so it survives
    a cold restart (b3nw's ``model.default: x-ai/grok-4.5``). Checked ONLY for
    custom providers; returns False for anything not verbatim-declared so the
    caller falls through to the legacy family heuristic.
    """
    model = str(model_id or "").strip()
    if not model:
        return False
    model_cfg = _cfg().get("model", {})
    if isinstance(model_cfg, dict):
        if str(model_cfg.get("default") or "").strip() == model:
            return True
        _declared = model_cfg.get("models")
        if model in _configured_ids(_declared):
            return True
    # Named custom:<slug> — scan its custom_providers[] entry for a verbatim id.
    prov = str(config_provider or "").strip().lower()
    if prov.startswith("custom:"):
        raw_suffix = prov.removeprefix("custom:")
        for entry in _custom_entries():
            slug = _custom_slug_from_name(entry.get("name"))
            entry_name = str(entry.get("name") or "").strip().lower()
            if not (prov in {entry_name, slug} or (slug and raw_suffix == slug.removeprefix("custom:"))):
                continue
            if str(entry.get("model") or "").strip() == model:
                return True
            if model in _configured_ids(entry.get("models")):
                return True
    return False


def _is_first_party_model(provider_id: str, model_id: str) -> bool:
    """True when ``model_id`` is listed in ``provider_id``'s own static catalog.

    Used to tell a *redundant* first-party prefix from an *intrinsic* routing
    prefix on a bare ``custom`` endpoint. ``openai/gpt-5.4`` → gpt-5.4 is a real
    OpenAI model, so ``openai/`` is a redundant leftover and strippable (#433).
    But ``bedrock/opus-4-6`` → opus-4-6 is NOT in bedrock's first-party catalog
    (those ids look like ``global.anthropic.claude-…``), so ``bedrock/`` is a
    vendor-routing segment a proxy needs whole (#3872). Returns False on any
    unknown provider or empty model so callers preserve the id.
    """
    provider = str(provider_id or "").strip().lower()
    model = str(model_id or "").strip()
    if not provider or not model:
        return False
    catalog = _catalog("_PROVIDER_MODELS").get(provider)
    if not isinstance(catalog, list):
        return False
    return any(
        isinstance(entry, dict) and entry.get("id") == model
        for entry in catalog
    )


def _base_url_points_at_local_server(base_url: str) -> bool:
    """True if base_url's host is a loopback or private IP (likely local server).

    Reuses ipaddress.is_loopback / is_private / is_link_local — the same
    heuristic used in the `api/config.py` SSRF/credential-routing code.
    Errors (DNS failure, malformed URL) return False so callers fall back to
    the static-provider-name check.
    """
    if not base_url:
        return False
    try:
        # (urlparse/ipaddress imported at module top)
        host = (urlparse(base_url).hostname or "").lower()
        if not host:
            return False
        # Plain-text "localhost" doesn't ipaddress-parse but is unambiguous.
        if host in ("localhost", "ip6-localhost", "ip6-loopback"):
            return True
        try:
            addr = ipaddress.ip_address(host)
        except ValueError:
            # Not an IP literal — could be a hostname like "ollama.internal".
            # Don't try DNS resolution here (slow + ambient): only IP literals
            # and the `localhost` alias get the no-strip treatment via this path.
            return False
        return addr.is_loopback or addr.is_private or addr.is_link_local
    except Exception:
        return False


def _custom_slug_rest_looks_like_host_port(rest: str) -> bool:
    """True when ``custom:<rest>`` is an endpoint-style slug ``host:port``.

    WebUI sometimes derives ``custom:10.8.71.41:8080`` from ``base_url`` authority.
    The #1776 peel must not treat that middle colon as part of an eaten model
    segment — otherwise ``@custom:10.8.71.41:8080:Qwen3`` wrongly becomes model
    ``8080:Qwen3``.
    """
    rest = str(rest or "").strip()
    if ":" not in rest:
        return False
    host, port_s = rest.rsplit(":", 1)
    if not host or ":" in host:
        return False
    if not port_s.isdigit():
        return False
    try:
        port_n = int(port_s)
    except ValueError:
        return False
    if not (1 <= port_n <= 65535):
        return False
    try:
        import ipaddress

        ipaddress.ip_address(host)
        return True
    except ValueError:
        pass
    hl = host.lower()
    if hl == "localhost":
        return True
    # Typical DNS hostname used as proxy slug (contains at least one label dot).
    if "." in host:
        return True
    return False


def _parse_provider_qualified_model_id(model_id: str) -> tuple[str, str] | None:
    """Parse WebUI's ``@provider:model`` route hint into ``(model, provider)``.

    The provider segment can contain colons for named custom providers, while
    the model segment can also contain colons for tags such as ``:free``.
    Keep this parser shared with ``resolve_model_provider`` so any caller that
    compares route-hinted model lanes uses the same grammar.
    """
    candidate = str(model_id or "").strip()
    if not candidate.startswith("@") or ":" not in candidate:
        return None
    inner = candidate[1:]
    provider_hint, bare_model = inner.rsplit(":", 1)
    if provider_hint.startswith("custom:") and provider_hint.count(":") >= 2:
        _slug_rest = provider_hint[len("custom:"):]
        if not _custom_slug_rest_looks_like_host_port(_slug_rest):
            provider_hint, extra = provider_hint.rsplit(":", 1)
            bare_model = f"{extra}:{bare_model}"
    elif (provider_hint not in _catalog("_PROVIDER_MODELS")
            and provider_hint not in _catalog("_PROVIDER_DISPLAY")
            and not provider_hint.startswith("custom:")):
        provider_hint, bare_model = inner.split(":", 1)
    return bare_model, provider_hint


def _get_provider_base_url(provider_id):
    """Look up the configured base_url for a provider (e.g. lmstudio).

    Checks two locations, in order:
      1. ``cfg["providers"][<provider_id>]["base_url"]`` — the explicit
         per-provider override.
      2. ``cfg["model"]["base_url"]`` — falls back here when
         ``cfg["model"]["provider"] == provider_id``. This is the historical
         shape (the model block carries both the active provider AND the
         base URL for that provider in a single record).

    Returns the URL stripped of trailing ``/`` if configured, otherwise None.
    """
    prov_cfg = _get_provider_cfg(provider_id)
    explicit = (prov_cfg.get("base_url") or "").strip().rstrip("/")
    if explicit:
        return explicit
    model_cfg = _cfg().get("model", {}) or {}
    if isinstance(model_cfg, dict):
        model_provider = str(model_cfg.get("provider") or "").strip().lower()
        if model_provider == str(provider_id).strip().lower():
            model_base = (model_cfg.get("base_url") or "").strip().rstrip("/")
            if model_base:
                return model_base
    return None


def _get_providers_cfg() -> dict:
    providers_cfg = _cfg().get("providers")
    return providers_cfg if isinstance(providers_cfg, dict) else {}


def _get_provider_cfg(provider_id) -> dict:
    provider_cfg = _get_providers_cfg().get(provider_id, {})
    return provider_cfg if isinstance(provider_cfg, dict) else {}


class AmbiguousCustomProviderError(ValueError):
    """Raised when two+ custom_providers[] entries normalize to the same slug.

    A custom provider is identified downstream by a SLUG (``custom:<slug>``):
    ``resolve_model_provider()`` returns it, and the credential lookup
    (``resolve_custom_provider_connection``) resolves the API key + base_url by
    scanning ``custom_providers[]`` for the FIRST entry whose name normalizes to
    that slug — independent of model ownership or the endpoint chosen earlier. So
    when two distinct provider names normalize to the same slug (e.g. ``Foo Bar``
    and ``foo-bar`` both -> ``custom:foo-bar``), consuming the slug on ANY path
    could pair one entry's endpoint with another entry's credential — including
    the asymmetric case where only one of the colliding entries lists the
    requested model. Rather than guess, every slug-only boundary fails closed and
    surfaces the collision so the user can rename one provider. Subclasses
    ``ValueError`` so existing ``except ValueError`` / broad-``except`` fallbacks
    continue to catch it.
    """

    def __init__(self, message: str):
        super().__init__(message)
        # Expose the actionable rename text as ``.message`` so HTTP handlers can
        # forward it verbatim (as the JSON ``error``) without ``str(e)`` casts,
        # and it reaches the user on the handoff + save paths instead of being
        # swallowed into a generic fallback.
        self.message = message


def _custom_provider_slug_key_routing_unused(value: object) -> str:
    """Canonical bare slug for custom-provider identity + collision detection.

    Derived from the SINGLE authoritative slug PRODUCER
    ``_custom_slug_from_name()`` — the same function that mints the
    ``custom:<slug>`` ids resolve_model_provider() actually returns, persists,
    and routes on. Using the producer's normalization (not a private variant)
    everywhere means every slug-only boundary — bare + qualified resolution,
    credential lookup, auxiliary persistence — compares against ONE identity, so
    names that genuinely collide at the producer level (e.g. ``Foo (Bar)`` and
    ``foo-bar`` both -> ``custom:foo-bar``) are detected as collisions instead of
    slipping through a looser key. Returns the bare slug (no ``custom:`` prefix).
    Accepts a bare provider name or a ``custom:<slug>`` id.
    """
    produced = _custom_slug_from_name(value)
    return produced.split(":", 1)[1] if produced.startswith("custom:") else produced


def _unique_custom_provider_entry_routing(custom_providers: object, slug_key: str) -> dict | None:
    """Return the single named ``custom_providers`` entry matching ``slug_key``.

    Pure and lock-safe: operates only on the passed-in list, so it can be called
    while holding ``_cfg_lock`` (unlike ``get_config()``-based resolvers).

    Membership is built from ALL named entries, INDEPENDENT of model ownership,
    because slug-only credential resolution scans every same-slug entry and
    returns the first match. Raises ``AmbiguousCustomProviderError`` when 2+
    entries share the key so an endpoint and an API key can never be resolved
    from different entries on any path. Returns the matching entry, or ``None``
    when no entry matches.
    """
    if not slug_key or not isinstance(custom_providers, list):
        return None
    matches: list[dict] = []
    for entry in custom_providers:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()
        if not name:
            continue
        if _custom_slug_key(name) == slug_key:
            matches.append(entry)
    if len(matches) >= 2:
        names = [str(e.get("name") or "").strip() for e in matches]
        raise AmbiguousCustomProviderError(
            f"Custom providers {names!r} all normalize to the same provider slug "
            f"{slug_key!r}; an endpoint and API key could be resolved from "
            f"different entries. Rename one so each custom provider has a unique slug."
        )
    return matches[0] if matches else None


def resolve_model_provider(model_id: str, *, explicitly_picked: bool = False) -> tuple:
    """Resolve model name, provider, and base_url for AIAgent.

    Model IDs from the dropdown can be in several formats:
      - 'claude-sonnet-4.6'            (bare name, uses config default provider)
      - 'anthropic/claude-sonnet-4.6'  (OpenRouter-style provider/model)
      - '@minimax:MiniMax-M2.7'        (explicit provider hint from dropdown)

    The @provider:model format is used for models from non-default provider
    groups in the dropdown, so we can route them through the correct provider
    via resolve_runtime_provider(requested=provider) instead of the default.

    Custom OpenAI-compatible endpoints are special: their model IDs often look
    like provider/model (for example ``google/gemma-4-26b-a4b``), which would be
    mistaken for an OpenRouter model if we only looked at the slash. To avoid
    that, first check whether the selected model matches an entry in
    config.yaml -> custom_providers and route it through that named custom
    provider.

    Returns (model, provider, base_url) where provider and base_url may be None.

    ``explicitly_picked``: True when the caller knows the user DELIBERATELY
    selected ``model_id`` this session (persisted from an ``explicit_model_pick``
    UI action), as opposed to it being a stale session leftover. Used ONLY for
    the custom-proxy COLD-catalog decision (#5979): with no provenance available,
    a deliberately-picked ``vendor/model`` is preserved verbatim (the user chose
    it, the proxy routes on it), while an UNMARKED id (a stale cross-provider
    leftover, e.g. #433's ``openai/gpt-5.4`` on a bare-only relay) still gets the
    legacy redundant-prefix strip so it keeps routing when cold. Warm provenance
    (endpoint-advertised ids) always takes precedence over this flag.
    """
    config_provider = None
    config_base_url = None
    model_cfg = _cfg().get("model", {})
    if isinstance(model_cfg, dict):
        config_base_url = model_cfg.get("base_url")
        config_provider = _resolve_provider_cfg_id(
            model_cfg.get("provider"),
            _cfg(),
            base_url=config_base_url,
            resolve_alias=False,
        )

    # Heal legacy ``provider: local`` entries (written by WebUI < v0.50.252)
    # at read time. ``local`` is not a registered provider, so passing it
    # downstream raises a ``LOCAL_API_KEY`` error from the auxiliary client
    # mid-conversation when compression/vision/web-extract fires. Route
    # through ``custom`` instead — it takes the ``no-key-required``
    # OpenAI-compat path that local servers (Ollama, LM Studio, llama.cpp,
    # vLLM, TabbyAPI) actually use. See #1384.
    if isinstance(config_provider, str) and config_provider.strip().lower() == "local":
        config_provider = "custom"

    def _finalize(model: object, provider: object, base_url: object) -> tuple:
        """Point-of-return collision guard.

        Fail closed HERE — immediately before handing a ``custom:<slug>`` back —
        never up front. A slug collision on one custom pair must not block a
        request that resolves to a DIFFERENT provider (an unrelated
        ``@openrouter:...`` / ``@custom:safe-provider:...`` lane, or the bare
        ``custom`` proxy), so the ambiguity check runs only on the slug actually
        being returned. ``_unique_custom_provider_entry`` raises
        AmbiguousCustomProviderError when >=2 config entries share the slug, so a
        downstream credential lookup can never first-match a different entry than
        the one whose endpoint we resolved. No-op for bare ``custom`` and every
        non-custom provider.
        """
        if isinstance(provider, str) and provider.startswith("custom:"):
            _unique_custom_provider_entry_routing(
                _cfg().get('custom_providers', []),
                _custom_slug_key(provider),
            )
        return model, provider, base_url

    model_id = (model_id or "").strip()
    if not model_id:
        return _finalize(model_id, config_provider, config_base_url)

    # Custom providers declared in config.yaml should win over slash-based
    # OpenRouter heuristics. Their model IDs commonly contain '/' too.
    # However, when the active provider is an explicit non-custom provider and
    # the requested model_id is the configured default model, that active
    # provider takes precedence over overlapping custom_providers[] entries.
    # Otherwise WebUI routes to custom:<name> instead of the intended endpoint
    # and can surface a 401 from the wrong provider (#1922).
    # For all other cases, preserve custom_providers[] routing for explicitly
    # selected custom provider models.
    _is_explicit_non_custom_provider = (
        config_provider is not None
        and config_provider != 'custom'
        and not config_provider.startswith('custom:')
    )
    _default_model = model_cfg.get('default') if isinstance(model_cfg, dict) else None
    # Owns model if it appears in the static catalog for the configured provider.
    # _PROVIDER_MODELS is keyed by CANONICAL slug (e.g. 'zai', not the 'z-ai'
    # alias a user may write in config), so canonicalise config_provider before
    # the lookup — otherwise an aliased active provider gets an empty ownership
    # set and _skip_custom_providers guard-2 silently fails, letting another
    # providers.<slug>.models entry hijack an active-owned model (#5511).
    _canon_config_provider = _canonicalise(config_provider) if config_provider else ""
    _provider_models_set: set[str] = set()
    if (
        _canon_config_provider
        and _canon_config_provider in _catalog("_PROVIDER_MODELS")
        and isinstance(_catalog("_PROVIDER_MODELS").get(_canon_config_provider), list)
    ):
        _provider_models_set = {
            m.get('id', '') for m in _catalog("_PROVIDER_MODELS").get(_canon_config_provider, [])
            if isinstance(m, dict) and isinstance(m.get('id'), str)
        }
    # The active provider may be defined ENTIRELY via config.yaml `providers:`
    # (no static _PROVIDER_MODELS entry) with its own `models:` allowlist. Fold
    # that allowlist into the ownership set too, so the active provider owns its
    # own declared models and can't be hijacked by another providers.<slug>
    # entry that happens to list the same bare id earlier in config order (#5511).
    if _canon_config_provider:
        _providers_cfg_own = _cfg().get('providers', {})
        if isinstance(_providers_cfg_own, dict):
            for _slug, _pdef in _providers_cfg_own.items():
                if not isinstance(_pdef, dict):
                    continue
                if _canonicalise(_slug) != _canon_config_provider:
                    continue
                if _canon_config_provider == "copilot":
                    continue  # copilot.models is a settings map, not an allowlist
                _provider_models_set.update(_configured_ids(_pdef.get('models')))
    _skip_custom_providers = (
        _is_explicit_non_custom_provider
        and (
            # Guard 1: model is the configured default (existing behaviour).
            (_default_model is not None and model_id == _default_model)
            # Guard 2: model is owned by the configured non-custom provider.
            or model_id in _provider_models_set
        )
    )
    custom_providers = _cfg().get('custom_providers', [])
    if isinstance(custom_providers, list) and not _skip_custom_providers:
        # Disambiguation guard: when two custom_providers[] entries both list the
        # same bare model id (e.g. dogapi and packyapi both advertise
        # 'claude-sonnet-5'), a plain first-match scan routes on config WRITE
        # ORDER — silently hijacking the model to whichever entry appears first,
        # regardless of the active provider the user actually configured. If the
        # ACTIVE provider is itself a named custom provider (config_provider
        # resolved to 'custom:<slug>', including via model.base_url → named-slug
        # matching), prefer THAT entry when it also owns the model, so an explicit
        # active endpoint wins over an overlapping earlier entry. Falls through to
        # the ordered scan below when the active provider is bare 'custom' / not a
        # named custom entry, or when it doesn't list this model.
        _active_custom_slug = ''
        if isinstance(config_provider, str) and config_provider.startswith('custom:'):
            _active_custom_slug = config_provider

        def _entry_owns_model(entry: dict) -> bool:
            entry_model = (entry.get('model') or '').strip()
            ids = set()
            if entry_model:
                ids.add(entry_model)
            ids.update(_configured_ids(entry.get('models')))
            return model_id in ids

        # Explicit active named provider wins over config order when it owns the
        # model. Only CONSUME the active slug (and thus let _finalize fail closed
        # on a collision) when the active provider actually lists this model: if
        # it doesn't, fall through so an unrelated collision on the active slug
        # never blocks a request that resolves to a different provider.
        if _active_custom_slug:
            _active_key = _custom_slug_key(_active_custom_slug)
            _active_owner = next(
                (
                    e for e in custom_providers
                    if isinstance(e, dict)
                    and _entry_owns_model(e)
                    and _custom_slug_key(e.get('name')) == _active_key
                ),
                None,
            )
            # Exactly one entry carries the active slug AND owns the model ->
            # authoritative, even when model.base_url is stale/absent or points at
            # a different endpoint. An explicit, unambiguous named provider must
            # never lose to config order: a stale URL is not evidence to discard
            # it. _finalize() fails closed if the active slug is shared by >=2
            # entries (endpoint + credential could then split).
            if _active_owner is not None:
                return _finalize(
                    model_id,
                    _active_custom_slug,
                    (_active_owner.get('base_url') or '').strip() or None,
                )
        for entry in custom_providers:
            if not isinstance(entry, dict):
                continue
            entry_model = (entry.get('model') or '').strip()
            entry_name = (entry.get('name') or '').strip()
            entry_base_url = (entry.get('base_url') or '').strip()
            entry_model_ids = set()
            if entry_model:
                entry_model_ids.add(entry_model)
            entry_model_ids.update(_configured_ids(entry.get('models')))
            if entry_name and model_id in entry_model_ids:
                provider_hint = _custom_slug_from_name(entry_name)
                # _finalize() applies the all-entry collision guard on this
                # bare-'custom' / fall-through path before returning the slug.
                return _finalize(model_id, provider_hint, entry_base_url or None)

    # Check user-defined providers (config.yaml → providers:).
    # Mirrors the custom_providers scan above — exact match against each
    # entry's declared models list (case-sensitive to match custom_providers).
    providers_cfg = _cfg().get('providers', {})
    if isinstance(providers_cfg, dict):
        target = model_id.strip()
        # Honor the same active/default ownership guard as the custom_providers
        # scan (_skip_custom_providers, config.py:2535): when the active provider
        # explicitly owns this model (it's the configured default or in the
        # active provider's model set), another provider's overlapping
        # `providers.<slug>.models` entry must NOT hijack routing away from the
        # active provider (#5511 gate finding — e.g. active ai-gateway + default
        # gpt-5 was being pulled to providers.openai.models.gpt-5). In that case
        # restrict the scan to the active provider's own canonical slug.
        _active_slug = _canon_config_provider
        for slug, pdef in providers_cfg.items():
            if not isinstance(pdef, dict):
                continue
            # Copilot is the documented exception: `providers.copilot.models` is
            # a per-model SETTINGS map (reasoning_effort, limits, etc.), NOT a
            # routable allowlist (see the exception at the catalog-build site).
            # Scanning it here would let a Copilot per-model settings entry
            # hijack that model's routing away from its real provider (#5511).
            if _canonicalise(slug) == "copilot":
                continue
            # Ownership guard: when the active provider owns this model, only its
            # own providers: entry may match; skip all other slugs.
            if _skip_custom_providers and _canonicalise(slug) != _active_slug:
                continue
            if target in _configured_ids(pdef.get('models')):
                p_base_url = str(pdef.get('base_url') or '').strip()
                return model_id, slug, p_base_url or None

    # @provider:model format — explicit provider hint from the dropdown.
    # Route through that provider directly (resolve_runtime_provider will
    # resolve credentials in streaming.py).
    # Use rsplit to handle provider_ids that contain ':' (e.g. custom:my-key).
    # With rsplit, "@custom:my-key:model" → provider="custom:my-key", model="model".
    # BUT: model IDs that end in :free / :beta / :thinking collide with the
    # rsplit grammar (e.g. "@openrouter:tencent/hy3-preview:free" would split
    # into provider="openrouter:tencent/hy3-preview", model="free").  Guard
    # against that by falling back to split(":") when the rsplit result is not
    # a recognised provider (#1744).
    #
    # Edge case (#1776): for custom providers with the same suffix
    # ("@custom:my-key:some-model:free"), rsplit yields
    # provider_hint="custom:my-key:some-model", bare_model="free", and the
    # custom-prefix guard below skips the split-fallback. Detect the
    # over-split structurally — custom hints normally carry one slug segment
    # after ``custom:``. If ``provider_hint`` has extra ``:`` tokens because the
    # model ID contained tags like ``:free``, peel one segment back (#1776).
    #
    # Exception: ``custom:<ip-or-host>:<port>`` is a single logical slug derived
    # from OpenAI ``base_url`` authority and contains no eaten model segments.
    parsed_provider_hint = _parse_provider_qualified_model_id(model_id)
    if parsed_provider_hint is not None:
        bare_model, provider_hint = parsed_provider_hint
        # Session/send/handoff shapes encode the provider as @custom:<slug>:model
        # and reach here after the ownership scan only saw the ENCODED string.
        # _finalize() applies the all-entry uniqueness check before returning the
        # slug so the downstream credential lookup can't first-match a different
        # colliding entry — but ONLY on the custom:<slug> actually returned, so an
        # unrelated collision never blocks this explicit hint (@openrouter, a
        # non-colliding @custom:other, ...).
        if (
            provider_hint.startswith("custom:")
            and config_base_url
            and _is_local_server_provider(config_provider)
            and provider_hint.lower() in _custom_endpoint_slugs_for_base_url(config_base_url)
        ):
            return _finalize(bare_model, config_provider, config_base_url)
        # _get_provider_base_url() only reads `providers:` and the ACTIVE
        # `model.base_url`, so a named custom provider registered solely in
        # `custom_providers:` resolved to None there. Prefer that entry's own
        # endpoint so a non-active @custom:<slug> hint routes to its own URL
        # instead of falling back to the default endpoint (HTTP 400 "Invalid
        # model format or no credentials for provider: <bare-model>").
        #
        # Match via _unique_custom_provider_entry on the module-level `cfg` list:
        # it is pure and lock-safe, so it stays callable when resolve_model_provider()
        # is invoked while the caller already holds the non-reentrant _cfg_lock —
        # a get_config()-based resolver would self-deadlock there. It also does NOT
        # guess: an unmatched slug (e.g. a host:port-derived one absent from
        # custom_providers) keeps the prior None so no stale endpoint is persisted
        # for it (#4728). A colliding slug still fails closed via the raise.
        #
        # When an exact custom_providers[] entry exists, that row is authoritative
        # for its slug: use its stripped base_url directly (including None when
        # blank). It must NOT fall through to _get_provider_base_url(), which
        # could pair a same-slug keyed `providers:` endpoint with this entry's
        # credentials (violating same-entry endpoint/key parity). Only fall back
        # to _get_provider_base_url() when no exact custom_providers[] row exists.
        if provider_hint.startswith("custom:"):
            entry = _unique_custom_provider_entry_routing(
                _cfg().get('custom_providers', []),
                _custom_slug_key(provider_hint),
            )
            if entry is not None:
                base_url = str(entry.get('base_url') or '').strip() or None
            else:
                base_url = _get_provider_base_url(provider_hint)
        else:
            base_url = _get_provider_base_url(provider_hint)
        return _finalize(bare_model, provider_hint, base_url)

    if "/" in model_id:
        prefix, bare = model_id.split("/", 1)
        # OpenRouter always needs the full provider/model path (e.g. openrouter/free,
        # anthropic/claude-sonnet-4.6). Never strip the prefix for OpenRouter.
        if config_provider == "openrouter":
            return model_id, "openrouter", config_base_url
        # Portal providers (Nous, OpenCode, NVIDIA NIM) serve models from multiple
        # upstream namespaces — check them BEFORE the prefix-strip branch so that
        # a model id whose prefix happens to equal the config_provider (e.g.
        # nvidia/nemotron-... on NVIDIA NIM) still keeps the full namespaced path.
        # The earlier ordering ran this guard AFTER the prefix-strip, so it never
        # fired in the prefix==config_provider case, causing HTTP 404 from the
        # portal which requires the full provider/model id (#2177; sibling of
        # #854 / #894 for Nous, where this guard was originally added).
        _PORTAL_PROVIDERS = {"nous", "opencode-zen", "opencode-go", "nvidia"}
        if config_provider in _PORTAL_PROVIDERS:
            return _finalize(model_id, config_provider, config_base_url)
        # If prefix matches config provider exactly, strip it and use that provider directly.
        # e.g. config=anthropic, model=anthropic/claude-... → bare name to anthropic API
        if config_provider and prefix == config_provider:
            return _finalize(bare, config_provider, config_base_url)
        # The OpenAI Codex provider uses a real base_url, but its default
        # ChatGPT endpoint cannot serve OpenRouter-style provider/model IDs.
        # Keep that narrow exception before the custom endpoint protection so
        # selecting openai/gpt-5.5 from OpenRouter under active Codex still
        # routes through OpenRouter. Other base_url-backed real providers may be
        # custom/proxy endpoints, so they must fall through to the branch below.
        if (
            config_provider == "openai-codex"
            and str(config_base_url or "").strip().rstrip("/")
            == "https://chatgpt.com/backend-api/codex"
            and prefix in _catalog("_PROVIDER_MODELS")
            and prefix != config_provider
        ):
            return model_id, "openrouter", None
        # Cross-provider via custom_providers: if the prefix matches a named custom
        # provider entry (e.g. "ollama-local/glm-4.7-flash:q4_k_m"), route through it
        # instead of falling back to the default config provider. MUST come BEFORE
        # the config_base_url branch because many providers have a base_url set.
        if prefix and config_provider and prefix != config_provider:
            _custom_cfg = _cfg().get("custom_providers", [])
            if isinstance(_custom_cfg, list):
                for _entry in _custom_cfg:
                    if isinstance(_entry, dict) and _entry.get("name", "").strip() == prefix:
                        _slug = _custom_slug_from_name(prefix)
                        _base = (_entry.get("base_url") or "").strip()
                        return _finalize(model_id, _slug, _base or None)

        # If a custom endpoint base_url is configured, don't reroute through OpenRouter
        # just because the model name contains a slash (e.g. google/gemma-4-26b-a4b).
        # The user has explicitly pointed at a base_url, so trust their routing config.
        if config_base_url:
            # Local model servers (LM Studio, Ollama, llama.cpp, vLLM, TabbyAPI)
            # register models under their full HuggingFace-style id. Stripping the
            # prefix breaks the lookup and causes a fresh instance to load with
            # default settings, ignoring user-tuned context length / parallel slots.
            # See #1625. Detect either by canonical provider name OR by base_url
            # pointing at a loopback/private host.
            if (_is_local_server_provider(config_provider)
                    or _base_url_points_at_local_server(config_base_url)):
                return _finalize(model_id, config_provider, config_base_url)
            # Strip the provider prefix only when it's a known provider namespace
            # AND stripping is the right call for this configured provider:
            #
            #  * A real first-party provider pointed at an OpenAI-compatible proxy
            #    (e.g. provider=openai + base_url=litellm) expects the bare id —
            #    "openai/gpt-5.4" → "gpt-5.4", "google/gemma-…" → "gemma-…". This
            #    is the #433 behaviour and applies whenever config_provider is not
            #    the bare "custom" pseudo-provider.
            #
            #  * A *bare* ``custom`` provider (or a named ``custom:<slug>``) is a
            #    vendor-routing proxy (LiteLLM, Bedrock gateway, OpenRouter-style
            #    multi-vendor endpoint). There we strip ONLY a prefix that is
            #    redundant with the model's own first-party namespace
            #    ("openai/gpt-5.4" → gpt-5.4, since gpt-5.4 is genuinely an OpenAI
            #    model — #433). An intrinsic routing prefix whose bare id is NOT a
            #    first-party model of that namespace is kept whole, because the
            #    proxy routes on the full string and truncating it 403s "model not
            #    allowed": "bedrock/opus-4-6" stays intact (opus-4-6 ∉ bedrock
            #    catalog — #3872).
            #
            # Unknown prefixes (e.g. "zai-org/GLM-5.1" on DeepInfra) are intrinsic
            # to the model ID and always preserved (#548). The redundant-prefix
            # strip that matches the *configured* provider's own family is handled
            # earlier by the ``prefix == config_provider`` branch.
            _cp_lower = (config_provider or "").strip().lower()
            _is_custom = _cp_lower == "custom" or _cp_lower.startswith("custom:")
            if _is_custom:
                # Vendor-routing proxy: the reliable signal for whether the
                # endpoint wants the full ``vendor/model`` id or the bare id is
                # what its own catalog actually advertised (the ids the user
                # picked from the dropdown, populated by the endpoint's live
                # ``/v1/models`` probe or a ``custom_providers[].models``
                # allowlist). The catalog-FAMILY heuristic (_is_first_party_model)
                # is the wrong question: it answered "is this bare id a first-
                # party model of the prefix's home vendor?" which is True for BOTH
                # ``x-ai/grok-4.5`` (proxy advertised it whole — must preserve,
                # #5979) and ``openai/gpt-5.4`` (a stale leftover on a relay that
                # only serves bare ``gpt-5.4`` — must strip, #433). Those two are
                # structurally identical to the family heuristic, so a model
                # graduating into a first-party catalog (agent commit 62ada5175
                # adding grok-4.5) silently flipped a working custom-proxy id from
                # preserved to stripped. Tri-state provenance tells them apart:
                #
                # (1) Config declares the full id verbatim (model.default /
                #     model.models / custom_providers[].models). Authoritative and
                #     network-free, so #5979 survives a cold restart — preserve.
                if _model_id_declared_in_config(model_id, config_provider):
                    return _finalize(model_id, config_provider, config_base_url)
                # (2) The endpoint's live/cached catalog advertised it.
                _advertised = _endpoint_ids(config_provider)
                if _advertised:
                    # Full id advertised → route on it verbatim (#5979/#3872/#548).
                    if model_id in _advertised:
                        return _finalize(model_id, config_provider, config_base_url)
                    # ONLY the bare id advertised → the prefix is a redundant
                    # leftover the relay rejects; strip it (#433). Keep the
                    # ``prefix in _catalog("_PROVIDER_MODELS")`` belt so an adversarial catalog
                    # advertising a bare id can't strip an unknown-vendor prefix.
                    if bare in _advertised and prefix in _catalog("_PROVIDER_MODELS"):
                        return _finalize(bare, config_provider, config_base_url)
                    # Advertised but neither exact shape matched → intrinsic /
                    # unknown prefix the proxy routes on; preserve it whole.
                    return _finalize(model_id, config_provider, config_base_url)
                # (3) Provenance genuinely unavailable (cold/unbuilt or
                #     fingerprint-mismatched catalog AND not config-declared).
                #     Distinguish a DELIBERATE selection from a stale leftover:
                #
                #     * explicitly_picked → PRESERVE verbatim. The user chose this
                #       exact ``vendor/model`` in the UI this session; the proxy
                #       routes on it. A wrong strip destroys a namespace the proxy
                #       needs (recurs every turn, unrepairable short of declaring
                #       every model in config) — this is b3nw's #5979 case: a
                #       non-default pick on a custom:<slug> proxy, cold catalog.
                #     * NOT explicitly_picked → legacy redundant-prefix strip. An
                #       unmarked id here is a stale cross-provider leftover (the
                #       user switched providers and the old session model lingers,
                #       e.g. #433's ``openai/gpt-5.4`` on a relay that only serves
                #       bare ``gpt-5.4``); stripping keeps it routing while cold.
                #
                #     Warm provenance (case 2, endpoint-advertised ids) always
                #     wins over this flag; the send path also warms provenance
                #     network-free from the disk cache first
                #     (warm_models_catalog_provenance_if_cold), so this branch is
                #     reached only in the narrow no-disk-cache window. The flag
                #     removes the data-driven flaw where a model graduating into
                #     the static first-party catalog silently flipped routing
                #     (exactly how #5979 regressed).
                if explicitly_picked:
                    return _finalize(model_id, config_provider, config_base_url)
                if prefix in _catalog("_PROVIDER_MODELS") and _is_first_party_model(prefix, bare):
                    return _finalize(bare, config_provider, config_base_url)
                return _finalize(model_id, config_provider, config_base_url)
            # Non-custom first-party provider pointed at an OpenAI-compatible
            # proxy (e.g. provider=openai + base_url=litellm): the bare id is
            # what it expects — "openai/gpt-5.4" → "gpt-5.4" (#433).
            if prefix in _catalog("_PROVIDER_MODELS"):
                return _finalize(bare, config_provider, config_base_url)
            # Intrinsic / unknown prefix — pass the full model_id through unchanged.
            return _finalize(model_id, config_provider, config_base_url)

        # If prefix does NOT match config provider, the user picked a cross-provider model
        # from the OpenRouter dropdown (e.g. config=anthropic but picked openai/gpt-5.4-mini).
        # In this case always route through openrouter with the full provider/model string.
        # Exception (#4210): a custom provider (bare ``custom`` or named ``custom:<slug>``)
        # is a vendor-routing proxy, not a first-party provider — its model ids commonly
        # contain a known-provider prefix that the proxy uses for upstream routing, not
        # an OpenRouter dropdown selection. Keep the request on the custom provider; the
        # base_url-set sibling of this exception lives earlier in the ``config_base_url``
        # branch (#3872).
        _cp_lower_cross = (config_provider or "").strip().lower()
        _is_custom_cross = _cp_lower_cross == "custom" or _cp_lower_cross.startswith("custom:")
        _canon_prefix = _canonicalise(prefix)
        _canon_config_provider = _canonicalise(config_provider)
        if (
            _canon_prefix in _catalog("_PROVIDER_MODELS")
            and _canon_prefix != _canon_config_provider
            and not _is_custom_cross
        ):
            return model_id, "openrouter", None

    # Final active-provider fallback: when nothing more specific matched, route
    # on the configured provider. _finalize() fails closed here too if that
    # provider is a collision-shared custom:<slug> (per finding #2 — the check
    # must reach this last fallback, not just the earlier explicit paths).
    return _finalize(model_id, config_provider, config_base_url)


