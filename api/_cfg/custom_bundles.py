"""Custom provider record helpers extracted from api/config.py.

Re-exported via ``api.config`` so ``from api.config import resolve_custom_provider_bundle``
keeps working.  No external module should import from ``api._cfg.custom_bundles`` directly.

Runtime lookups that would otherwise create circular imports are done lazily
through ``api.config`` (which re-exports the canonical helpers).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Local alias for AmbiguousCustomProviderError — imported lazily from routing when
# custom_bundles is loaded before routing has finished initializing. We resolve it
# once on first use and cache it to avoid repeated imports.
_AmbiguousError: type[Exception] | None = None


def _ambiguous_error() -> type[Exception]:
    global _AmbiguousError
    if _AmbiguousError is not None:
        return _AmbiguousError
    try:
        import api._cfg.routing as _routing  # type: ignore[import]

        _AmbiguousError = _routing.AmbiguousCustomProviderError  # type: ignore[assignment]
        return _AmbiguousError
    except Exception:
        pass
    try:
        import api.config as _ac  # type: ignore[import]

        _AmbiguousError = _ac.AmbiguousCustomProviderError  # type: ignore[assignment]
        return _AmbiguousError
    except Exception:
        pass

    class _FallbackAmbiguous(ValueError):
        pass

    _AmbiguousError = _FallbackAmbiguous
    return _AmbiguousError


# Keep the canonical name available at module scope so `raise AmbiguousCustomProviderError`
# works when the block is exec'd with this module as its namespace.
AmbiguousCustomProviderError = _ambiguous_error()  # type: ignore[assignment]

# Lazy helpers — resolved through api.config at call time to avoid import cycles.

def _slug_key(value: object) -> str:
    try:
        import api.config as _ac
        fn = getattr(_ac, "_custom_provider_slug_key", None)
        if callable(fn):
            return fn(value)
    except Exception:
        pass
    return str(value or "").strip().lower().replace(" ", "-")


def _thread_env_value(name: str) -> str:
    try:
        import api.config as _ac
        fn = getattr(_ac, "_thread_local_env_value", None)
        if callable(fn):
            return str(fn(name) or "")
    except Exception:
        pass
    return ""


def _lookup_key_env(provider_hint):
    try:
        import api.config as _ac
        fn = getattr(_ac, "_lookup_custom_api_key_env", None)
        if callable(fn):
            return fn(provider_hint)
    except Exception:
        pass
    return None


def _unique_entry(custom_providers: object, slug_key: str):
    try:
        import api.config as _ac
        fn = getattr(_ac, "_unique_custom_provider_entry", None)
        if callable(fn):
            return fn(custom_providers, slug_key)
    except AmbiguousCustomProviderError:
        raise
    except Exception:
        pass
    return None


def _get_cfg() -> dict:
    try:
        import api.config as _ac
        fn = getattr(_ac, "get_config", None)
        if callable(fn):
            return fn() or {}
    except Exception:
        pass
    return {}

def _custom_record_base_url(record: object) -> str | None:
    """Return a custom record's OWN endpoint, or None when it declares none."""
    if not isinstance(record, dict):
        return None
    return str(record.get("base_url") or "").strip() or None


def _resolve_custom_record_key(
    raw_api_key: object,
    raw_key_env: object,
    provider_hint: object = None,
) -> str | None:
    """Static credential declared by ONE custom record.

    Accepts a literal key, a ``${ENV_VAR}`` reference or a ``key_env`` env-var
    hint, then falls back to the ``CUSTOM_<SLUG>_API_KEY`` convention. Reading
    every form from the SAME record is what keeps an endpoint and a credential
    from being resolved out of two different authorities.
    """
    api_key = None
    if raw_api_key is not None:
        key_text = str(raw_api_key).strip()
        if key_text.startswith("${") and key_text.endswith("}") and len(key_text) > 3:
            api_key = _thread_env_value(key_text[2:-1]).strip() or None
        elif key_text:
            api_key = key_text
    if not api_key:
        key_env = str(raw_key_env or "").strip()
        if key_env:
            api_key = _thread_env_value(key_env).strip() or None
    if not api_key and provider_hint:
        api_key = _lookup_key_env(provider_hint)
    return api_key


# Explicit outcomes of a named ``custom:<slug>`` selection.
#
# The status travels with the resolved bundle so a caller can tell "a record
# OWNS this route" from "nothing owns this route" instead of inferring it from
# an empty URL/key pair. That difference is load-bearing: an unowned named route
# must fail closed — no ambient endpoint, no ambient credential, no ambient
# pool/transport, no rewrite to the generic ``custom`` provider and no keyless
# placeholder — rather than borrow whatever the ambient runtime resolved.
CUSTOM_SELECTION_EXACT = "exact"           # exact custom_providers[] row
CUSTOM_SELECTION_KEYED = "keyed"           # identity-owned providers:/model: record
CUSTOM_SELECTION_RESOLVER = "resolver"     # injected connection_resolver reported a pair
CUSTOM_SELECTION_MISSING = "missing"       # no authority owns this slug
CUSTOM_SELECTION_MALFORMED = "malformed"   # ``custom:`` with no slug behind it
# ``ambiguous`` is never RETURNED: a slug collision raises
# AmbiguousCustomProviderError out of _unique_custom_provider_entry, so the
# actionable rename message reaches the user (routes turn it into a 400) instead
# of being silently degraded into a keyless or ambient-authority send.
CUSTOM_SELECTION_AMBIGUOUS = "ambiguous"
# Statuses for which NO authority owns the route.
CUSTOM_SELECTION_UNOWNED = (CUSTOM_SELECTION_MISSING, CUSTOM_SELECTION_MALFORMED)


# ── Terminal routing verdicts ────────────────────────────────────────────────
#
# Hermes Agent does NOT read an incomplete connection pair as a refusal. Its
# ``agent/agent_init.py:_init_openai_client()`` honours the explicit endpoint and
# credential the constructor was handed only when BOTH are truthy:
#
#     if api_key and base_url:
#         client_kwargs = _explicit_client_kwargs(...)
#     else:
#         client_kwargs = _routed_client_kwargs(...)
#
# and ``_routed_client_kwargs()`` resolves a provider AGAIN — through the
# centralized router, then the init-time fallback chain. So a bundle that "fails
# closed" by clearing its endpoint and/or its credential is not terminal at the
# constructor boundary; clearing those fields is precisely the signal to route
# somewhere else, which is how an unroutable custom slug ends up talking to the
# ambient or fallback provider after all.
#
# A route that cannot be resolved therefore has to be represented as an explicit
# verdict rather than as an ordinary constructor-ready dict with a hole in it.
# :data:`CUSTOM_ROUTE_ERROR_FIELD` carries that verdict on every merged bundle
# (``None`` when the route is fine), and every consumer that builds an AIAgent —
# or writes the agent cache, or hands the bundle to an auxiliary client — must
# stop on it and emit a controlled failure instead.
CUSTOM_ROUTE_UNOWNED = "unowned_custom_provider"
CUSTOM_ROUTE_NO_CREDENTIAL = "custom_provider_credential_unresolved"
CUSTOM_ROUTE_NO_ENDPOINT = "custom_provider_endpoint_unresolved"

# Key the verdict travels under on a merged bundle. ``None`` == routable.
CUSTOM_ROUTE_ERROR_FIELD = "route_error"


class CustomProviderRouteError(ValueError):
    """Raised when a named ``custom:<slug>`` route must not reach a provider client.

    The route resolved to no usable ``(api_key, base_url)`` pair, and passing
    that pair to AIAgent would re-enter provider routing rather than fail (see
    the :data:`CUSTOM_ROUTE_ERROR_FIELD` note above). Raising is how the refusal
    stays terminal across the constructor boundary.

    Subclasses ``ValueError`` for the same reason
    :class:`AmbiguousCustomProviderError` does: existing ``except ValueError`` /
    broad-``except`` handlers in the non-streaming routes already turn it into a
    controlled 400 / deterministic fallback instead of a traceback.
    """

    def __init__(self, message: str, *, reason: str, provider: str | None = None, hint: str = ""):
        super().__init__(message)
        # Forwarded verbatim by HTTP handlers, exactly like the ambiguous-slug
        # error's ``.message``.
        self.message = message
        self.reason = reason
        self.provider = provider
        self.hint = hint


def _custom_route_verdict(reason: str, provider: str | None) -> dict:
    """Build the terminal verdict recorded on an unroutable bundle."""
    name = str(provider or "").strip() or "custom"
    if reason == CUSTOM_ROUTE_UNOWNED:
        message = (
            f"Custom provider '{name}' is not configured: no custom_providers[] row, "
            "keyed providers[] record or model: authority owns that name."
        )
        hint = (
            "Add the provider under Settings -> Providers (or config.yaml "
            "custom_providers[]), or pick a configured provider, then send again."
        )
    elif reason == CUSTOM_ROUTE_NO_CREDENTIAL:
        message = (
            f"Custom provider '{name}' declares a credential source that produced no "
            "API key, so the route has no usable connection."
        )
        hint = (
            "Check the provider's api_key / key_env / key_cmd / credential_pool "
            "setting and the environment variable it names, then send again."
        )
    else:
        message = (
            f"Custom provider '{name}' resolved no endpoint, so the route has no "
            "usable connection."
        )
        hint = (
            "Set a base_url for the provider under Settings -> Providers (or "
            "config.yaml custom_providers[]), then send again."
        )
    return {"reason": reason, "provider": name, "message": message, "hint": hint}


def custom_provider_route_error(bundle: object) -> dict | None:
    """Return ``bundle``'s terminal verdict, or ``None`` when it is routable.

    The verdict dict carries ``reason`` (one of :data:`CUSTOM_ROUTE_UNOWNED`,
    :data:`CUSTOM_ROUTE_NO_CREDENTIAL`, :data:`CUSTOM_ROUTE_NO_ENDPOINT`),
    ``provider``, a user-facing ``message`` and a ``hint``.
    """
    if not isinstance(bundle, dict):
        return None
    verdict = bundle.get(CUSTOM_ROUTE_ERROR_FIELD)
    return verdict if isinstance(verdict, dict) else None


def raise_for_custom_provider_route(bundle: dict) -> dict:
    """Return ``bundle`` when routable; raise :class:`CustomProviderRouteError` otherwise.

    The single chokepoint every AIAgent-constructing consumer goes through, so
    "this route is unresolvable" cannot degrade into "resolve it some other way"
    at the constructor.
    """
    verdict = custom_provider_route_error(bundle)
    if verdict is None:
        return bundle
    raise CustomProviderRouteError(
        verdict["message"],
        reason=verdict["reason"],
        provider=verdict["provider"],
        hint=verdict["hint"],
    )


# Identity fields a record can use to NAME the custom provider it belongs to.
# ``name`` is the friendly name a ``custom_providers[]`` entry carries, so a
# ``providers:`` record spelled the same way is claiming the same identity. A
# ``model:`` block's ``name`` is the MODEL's name and never claims a provider,
# which is why that call site passes ``allow_name=False``.
_CUSTOM_RECORD_IDENTITY_FIELDS = ("provider_key", "provider", "custom_provider", "name")


def _custom_record_claims_slug(record: object, slug: str, *, allow_name: bool = True) -> bool:
    """True when ``record`` names ``slug`` as its OWN identity.

    A generic record — ``providers['custom']`` or a ``model:`` block whose
    provider is the bare string ``custom`` — belongs to no slug by itself, so it
    is an authority for ``custom:<slug>`` only when it names that slug. Without
    this test any unknown route adopts whichever generic record happens to be
    configured, which is a credential handed to an endpoint the user never named.
    """
    if not isinstance(record, dict) or not slug:
        return False
    for field in _CUSTOM_RECORD_IDENTITY_FIELDS:
        if field == "name" and not allow_name:
            continue
        value = record.get(field)
        if not isinstance(value, str) or not value.strip():
            continue
        if _slug_key(value) == slug:
            return True
    return False


def _custom_record_owns_connection(record: dict, pid: str) -> bool:
    """True when ``record`` declares ANY field the connection bundle carries.

    The bundle is more than a URL/key pair: ``key_cmd`` mints a per-request
    bearer, ``credential_pool`` supplies a rotating one, and ``api_mode`` and the
    ACP command/args decide the wire protocol and the transport process. A record
    keyed to this slug that declares ONLY those still owns the route — reporting
    it ``missing`` would fail the route closed and then clear the very fields it
    declares (see :func:`merge_custom_provider_runtime_bundle`).
    """
    base_url = _custom_record_base_url(record)
    if base_url:
        return True
    # DECLARATION, not resolution: a ``key_env`` naming an unset variable or a
    # pool that is momentarily empty still names this slug's credential source.
    # Judging by "did a static key resolve?" hands the route to the ambient
    # authority instead of surfacing the misconfiguration.
    if _custom_record_declares_credential(record, base_url, None):
        return True
    if _resolve_custom_record_key(record.get("api_key"), record.get("key_env"), pid):
        return True
    if _custom_record_api_mode(record):
        return True
    if _custom_record_acp_transport(record):
        return True
    return False


# ── raw ``providers:<key>`` records (the standard Hermes v12 config shape) ────
#
# The installed Agent's authoritative matcher for a named custom route is
# ``hermes_cli.runtime_provider_custom._match_new_style_provider()``. It scans
# the ENABLED entries of the RAW ``providers:`` mapping and takes the first
# whose alias set contains the requested name, where the alias set is minted by
# ``hermes_cli.providers.custom_provider_aliases()`` from BOTH the entry's
# display ``name`` and its config KEY. So the standard v12 shape
#
#     model:
#       provider: custom:omni
#     providers:
#       omni:
#         base_url: https://omni.example/v1
#         key_env: OMNI_GATE_KEY
#
# routes through ``providers['omni']``: that record names the identity by its
# own config key, exactly the way ``providers['custom:<slug>']`` does. Looking
# only at the ``custom:``-prefixed spellings made every send on that config fail
# closed as ``unowned_custom_provider`` while the Agent resolved it fine.
#
# The alias/enabled rules are MIRRORED here rather than imported for the same
# reason :data:`_API_MODE_ALIASES` is: selection has to keep working when the
# installed runtime module is unavailable or replaced by a test double.


def _agent_custom_provider_slug(value: object) -> str:
    """``custom:<name>`` identity the Agent mints for ``value``.

    Mirror of ``hermes_cli.providers.custom_provider_slug()``: lowercase, spaces
    to dashes, prefixed unless it already carries one. Deliberately NOT
    :func:`_custom_provider_slug_key`'s normalization — this one reproduces the
    Agent's alias vocabulary, and both are consulted by
    :func:`_custom_record_names_identity`.
    """
    identity = str(value or "").strip().lower().replace(" ", "-")
    if not identity:
        return ""
    return identity if identity.startswith("custom:") else f"custom:{identity}"


def _custom_provider_alias_set(display_name: object, provider_key: object) -> frozenset[str]:
    """Every identity the Agent accepts for ONE custom-provider record.

    Mirror of ``hermes_cli.providers.custom_provider_aliases()``, including its
    legacy ``custom:custom:<name>`` spelling, so a record the Agent would route
    to is not reported unowned here.
    """
    aliases: set[str] = set()
    for value in (display_name, provider_key):
        raw = str(value or "").strip().lower()
        if not raw:
            continue
        normalized = raw.replace(" ", "-")
        aliases.update({raw, normalized, _agent_custom_provider_slug(normalized)})
        if normalized.startswith("custom:"):
            suffix = normalized.split(":", 1)[1]
            if suffix:
                aliases.update({suffix, f"custom:{normalized}"})
    aliases.discard("")
    return frozenset(aliases)


def _custom_record_names_identity(display_name: object, provider_key: object, pid: str, slug: str) -> bool:
    """True when a record keyed ``provider_key`` / named ``display_name`` IS ``pid``.

    Two vocabularies, because two producers mint these ids. The Agent's alias set
    is what decides a CLI send, so honouring it is what keeps the two in
    agreement. :func:`_custom_provider_slug_key` is what the WebUI itself mints
    from a friendly name (``Local (127.0.0.1:11434)`` -> ``local-127.0.0.1-11434``),
    so a record the WebUI wrote is recognised by its own rules too. Both are
    identity tests on the record's OWN name/key — neither widens selection to a
    record that names some other provider.
    """
    if pid in _custom_provider_alias_set(display_name, provider_key):
        return True
    return any(
        _slug_key(value) == slug
        for value in (display_name, provider_key)
        if str(value or "").strip()
    )


# ``enabled:`` words YAML may hand us as strings, matching
# hermes_cli.config_providers._FALSE_WORDS.
_PROVIDER_DISABLED_WORDS = frozenset({"false", "0", "no", "off"})


def _raw_provider_record_enabled(record: object) -> bool:
    """Mirror of ``hermes_cli.config_providers.is_provider_enabled()``.

    Default True; only an explicit falsey ``enabled`` hides the entry. A disabled
    entry is invisible to the Agent's resolver, so it must not own a WebUI route
    either — the route fails closed instead of quietly using a row the user
    switched off.
    """
    if not isinstance(record, dict):
        return False
    flag = record.get("enabled", True)
    if isinstance(flag, bool):
        return flag
    if isinstance(flag, str):
        return flag.strip().lower() not in _PROVIDER_DISABLED_WORDS
    return bool(flag)


# ``_entry_url``'s precedence in hermes_cli.runtime_provider_custom.
_RAW_PROVIDER_URL_FIELDS = ("api", "url", "base_url")


def _normalized_raw_provider_record(record: dict, ep_name: str) -> dict:
    """Return ``record`` respelled in the field names this module's readers use.

    ONE record in, ONE record out: every value is copied from ``record`` and
    nothing is sourced from anywhere else, so the endpoint, the credential, the
    wire protocol, the pool, the ACP transport, the capabilities and the request
    fields the caller then lifts all still belong to that single authority. Only
    the SPELLINGS differ between the raw ``providers:<key>`` shape and the
    ``custom_providers[]`` shape the field readers were written against:

    ``api`` / ``url`` -> ``base_url`` (the Agent's ``_entry_url`` precedence)
    ``api_key_env``   -> ``key_env``
    ``<key>``         -> ``provider_key``, when the record names none itself

    ``transport`` -> ``api_mode`` and ``command`` / ``args`` -> ``acp_*`` need no
    rewrite: :func:`_custom_record_api_mode` and
    :func:`_custom_record_acp_transport` already accept both spellings in place.

    Respelling a COPY rather than the live record keeps the config snapshot
    unmutated, so a second resolution of the same slug sees the same input.
    """
    normalized = dict(record)
    for field in _RAW_PROVIDER_URL_FIELDS:
        value = record.get(field)
        if isinstance(value, str) and value.strip():
            normalized["base_url"] = value.strip()
            break
    if not str(normalized.get("key_env") or "").strip():
        api_key_env = record.get("api_key_env")
        if isinstance(api_key_env, str) and api_key_env.strip():
            normalized["key_env"] = api_key_env.strip()
    if not str(normalized.get("provider_key") or "").strip() and str(ep_name or "").strip():
        # The record's OWN config key is its identity. Stamping it keeps the
        # credential-pool lookup and the ``key_cmd`` token process labelled with
        # this provider instead of the generic ``custom``.
        normalized["provider_key"] = str(ep_name).strip()
    return normalized


def _unique_raw_provider_record(providers_cfg: object, pid: str, slug: str) -> dict | None:
    """The ONE enabled raw ``providers:<key>`` record that owns ``pid``, else None.

    Skips the two keys that already have dedicated, higher-precedence candidates
    in :func:`_select_custom_provider_record` — ``providers['custom:<slug>']``
    and the generic ``providers['custom']`` — so one record can never be counted
    as two authorities, nor collide with itself.

    Fails closed on a genuine collision: when two DISTINCT raw records both name
    this identity and both declare connection fields, an endpoint and a
    credential could be lifted from different rows, which is the same
    split-authority pairing :func:`_unique_custom_provider_entry` raises on. A
    record that names the identity but declares nothing is not a competing
    authority, so it neither wins nor blocks — selection simply falls through to
    the next candidate, and to the terminal verdict when there is none.
    """
    if not isinstance(providers_cfg, dict):
        return None
    owning: list[tuple[str, dict]] = []
    for ep_name, record in providers_cfg.items():
        key = str(ep_name or "").strip()
        if not key or key.lower() in {pid, "custom"}:
            continue
        if not isinstance(record, dict) or not record:
            continue
        if not _raw_provider_record_enabled(record):
            continue
        if not _custom_record_names_identity(record.get("name") or key, key, pid, slug):
            continue
        normalized = _normalized_raw_provider_record(record, key)
        if _custom_record_owns_connection(normalized, pid):
            owning.append((key, normalized))
    if len(owning) >= 2:
        keys = [key for key, _record in owning]
        raise _ambiguous_error()(
            f"Custom provider records {keys!r} under providers: all name the provider "
            f"slug {slug!r}; an endpoint and API key could be resolved from different "
            f"records. Rename one so each custom provider has a unique slug."
        )
    return owning[0][1] if owning else None


def _select_custom_provider_record(
    pid: str,
    slug: str,
    cfg_data: dict,
) -> tuple[dict | None, str, bool, str]:
    """Return ``(record, source, is_exact, status)`` — the ONE authority for ``pid``.

    Selection is IDENTITY-OWNED: a candidate qualifies only by naming this slug
    (or by being the bare-``custom`` / explicitly-matching ``model:`` authority),
    never by being the only row around. Selection order is the WebUI routing
    contract:

    1. the exact ``custom_providers[]`` row whose name normalizes to ``slug``
       (authoritative for its slug even against a same-slug keyed record, and
       even when its ``base_url`` is blank — see #1806);
    2. otherwise the keyed ``providers['custom:<slug>']`` record, then the raw
       ``providers:<key>`` record that names this identity by its own config key
       or display name (``providers: {omni: ...}`` for ``custom:omni`` — the
       standard Hermes v12 shape, matched with the installed Agent's own alias
       rules; see :func:`_unique_raw_provider_record`), then a record that NAMES
       this slug (the generic ``providers['custom']`` entry whose own
       name/provider_key normalizes to it, or a ``model:`` block whose provider
       is ``custom:<slug>``), and last the generic ``providers['custom']`` entry
       when ``model.provider`` names this slug — the active-provider shape the
       WebUI itself writes. Each is taken as a COMPLETE record rather than
       field-by-field, and each is eligible as soon as it declares ANY field the
       connection bundle carries, not just a static key or a base_url (see
       :func:`_custom_record_owns_connection`).

    What is deliberately NOT eligible is the generic ``providers['custom']``
    record or a bare-``custom`` ``model:`` block that names no slug at all. Those
    are catch-alls, and while they matched every lookup, ``custom:ghost`` took
    whichever one was configured and inherited its endpoint, credential, pool,
    ``api_mode`` and ACP transport — the same wrong-authority pairing as the
    sole-row fallback below, sourced from ``providers:``/``model:`` instead.

    There is deliberately NO "the list holds exactly one row, so use it"
    fallback. That rule resolved ``custom:ghost`` to the endpoint AND credential
    of a sole unrelated row named ``omni`` — the same wrong-authority pairing the
    exact-row rule exists to prevent, and a credential leak to an endpoint the
    user never named. An unknown slug owns nothing, so it reports ``missing``
    and the caller fails closed. This matches the point-of-return rule
    ``resolve_model_provider`` already applies (#4728: no unique entry -> no
    base_url, never a guess).

    ``source`` is ``custom_providers`` / ``providers`` / ``model`` (or ``""``
    when nothing matched) and names the authority that owns every field the
    caller then lifts off ``record``. ``status`` is one of the
    ``CUSTOM_SELECTION_*`` values (``ambiguous`` raises instead of returning).
    """
    custom_providers = cfg_data.get("custom_providers", [])
    if not isinstance(custom_providers, list):
        custom_providers = []

    # Fail closed when the slug maps to multiple entries (raises); otherwise use
    # the single matching entry. Shared with resolve_model_provider so endpoint
    # and credential are always resolved from the SAME entry.
    matched_entry = _unique_entry(custom_providers, slug)
    if matched_entry is not None:
        return matched_entry, "custom_providers", True, CUSTOM_SELECTION_EXACT

    # Fallbacks for setups that don't use custom_providers names directly. Every
    # one of them is keyed on this provider's OWN identity.
    providers_cfg = cfg_data.get("providers", {})
    provider_specific = providers_cfg.get(pid, {}) if isinstance(providers_cfg, dict) else {}
    provider_custom = providers_cfg.get("custom", {}) if isinstance(providers_cfg, dict) else {}

    if (
        isinstance(provider_specific, dict)
        and provider_specific
        and not _raw_provider_record_enabled(provider_specific)
    ):
        # ``providers['custom:<slug>']`` names THIS slug by its own exact key, so
        # switching it off is a statement about this route and not merely about
        # one candidate among several. Falling through to the generic ``custom``
        # record or the ``model:`` block would honour the disable by routing the
        # turn somewhere ELSE — the ambient provider's endpoint and credential
        # under the name the user just took offline. The slug's own authority
        # said no, so the route is terminal here.
        return None, "", False, CUSTOM_SELECTION_MISSING

    model_cfg = cfg_data.get("model", {})
    model_provider = str(model_cfg.get("provider") or "").strip().lower() if isinstance(model_cfg, dict) else ""

    # The generic ``providers['custom']`` record and a ``model:`` block whose
    # provider is the bare string ``custom`` are catch-alls: they name no slug of
    # their own. While they were eligible for EVERY ``custom:<slug>`` lookup,
    # ``custom:ghost`` selected whichever one happened to be configured and
    # inherited its endpoint, credential, pool, api_mode and ACP transport — the
    # user's prompt and a credential sent to a provider they never named.
    # Candidates are ordered by how specifically they name THIS identity.
    named_by_model = model_provider in {pid, slug}
    # A disabled record is invisible to the Agent's resolver, so it is not an
    # authority here either — at EVERY rung, not just the raw-``providers:`` scan
    # that already filtered for it.
    has_generic_custom = (
        isinstance(provider_custom, dict)
        and bool(provider_custom)
        and _raw_provider_record_enabled(provider_custom)
    )
    # The generic record is keyed ``custom`` in ``providers:``, so it names the
    # literal ``custom:custom`` route by its own key; for any other slug it has
    # to say so itself.
    generic_claims_slug = has_generic_custom and (
        slug == "custom" or _custom_record_claims_slug(provider_custom, slug)
    )

    def _candidates():
        """Identity-owned candidates, most specific first — evaluated LAZILY.

        Laziness is load-bearing for the raw-``providers:`` scan alone: that one
        RAISES on an alias collision, and a config whose route is already decided
        by the exact ``providers['custom:<slug>']`` key must not be failed closed
        by a collision further down a list it never reaches.
        """
        if isinstance(provider_specific, dict) and provider_specific:
            # Keyed by this provider's own id: ``providers['custom:<slug>']``.
            yield provider_specific, "providers"
        # The standard v12 shape: a raw ``providers:<key>`` record that names
        # this identity by its own config key or display name (``providers:
        # {omni: ...}`` for ``custom:omni``), matched with the installed Agent's
        # alias rules. This is identity ownership, not an ambient-field escape —
        # a record that names some OTHER provider never appears here, and an
        # unknown slug still matches nothing and falls through to the terminal
        # verdict below.
        raw_record = _unique_raw_provider_record(providers_cfg, pid, slug)
        if raw_record is not None:
            yield raw_record, "providers"
        if generic_claims_slug:
            yield provider_custom, "providers"
        if isinstance(model_cfg, dict) and model_cfg and _raw_provider_record_enabled(model_cfg) and (
            named_by_model
            or (model_provider == "custom" and _custom_record_claims_slug(model_cfg, slug, allow_name=False))
        ):
            yield model_cfg, "model"
        if has_generic_custom and named_by_model and not generic_claims_slug:
            # Last: the active-provider shape the WebUI itself writes, where the
            # generic record holds the configuration of whichever custom provider
            # ``model.provider`` names — here, THIS slug. That is a provenance
            # tie rather than a declaration, so anything naming the slug outright
            # (above) outranks it.
            yield provider_custom, "providers"

    for cand, source in _candidates():
        # A record that names this slug is its authority as soon as it declares
        # ANY connection field — a static key and a base_url are not the only
        # things a record can own. One that declares only ``key_cmd``, a
        # credential pool, an ``api_mode`` or an ACP transport is still THIS
        # slug's record; reporting it missing would clear those very fields as
        # foreign in merge_custom_provider_runtime_bundle.
        #
        # The ``enabled`` re-check is the single chokepoint every candidate has
        # to pass: each branch above gates on it too, and one selection path
        # added later without that gate is exactly how a switched-off row's
        # endpoint and secret got back onto a live route.
        if _raw_provider_record_enabled(cand) and _custom_record_owns_connection(cand, pid):
            return cand, source, False, CUSTOM_SELECTION_KEYED

    return None, "", False, CUSTOM_SELECTION_MISSING


def resolve_custom_provider_connection(
    provider_id: str,
    *,
    return_provenance: bool = False,
) -> tuple[str | None, str | None] | tuple[str | None, str | None, bool]:
    """Return (api_key, base_url) for a named ``custom:*`` provider.

    Supports ``custom_providers[].api_key`` as either a literal key or
    ``${ENV_VAR}``, and ``custom_providers[].key_env`` as an env-var hint.
    Returns ``(None, None)`` when no named custom provider matches.
    If ``return_provenance=True``, returns ``(api_key, base_url, is_exact)``
    indicating whether the connection was resolved from an exact matching
    ``custom_providers[]`` entry.

    This is the URL/key VIEW of the authoritative record. Agent-construction
    paths want :func:`resolve_custom_provider_bundle` instead: the record also
    owns ``api_mode``, ``key_cmd``, pool credentials and ACP transport fields,
    and a caller that takes only two of them still builds a mixed-authority
    agent.
    """
    pid = str(provider_id or "").strip().lower()
    if not pid.startswith("custom:"):
        if return_provenance:
            return None, None, False
        return None, None

    slug = _slug_key(pid)
    if not slug:
        if return_provenance:
            return None, None, False
        return None, None

    # Read the live config snapshot to avoid stale module-level cache edge
    # cases after profile switches or runtime config edits.
    record, _source, is_exact, _status = _select_custom_provider_record(pid, slug, _get_cfg())
    if record is None:
        # Nothing owns this slug. Returning ``(None, None)`` is the whole point:
        # an unknown named route must not inherit an unrelated row's endpoint or
        # credential (see _select_custom_provider_record).
        if return_provenance:
            return None, None, False
        return None, None

    base_url = _custom_record_base_url(record)
    api_key = _resolve_custom_record_key(record.get("api_key"), record.get("key_env"), pid)
    if return_provenance:
        return api_key, base_url, is_exact
    return api_key, base_url


# Local OpenAI-compatible servers frequently run without authentication, so a
# missing key must not fail before the first request: hand the SDK a harmless
# placeholder and let the endpoint accept it or return its own auth error. It is
# applied ONLY after the authoritative record has been resolved in full and
# reported itself genuinely keyless (no api_key, no key_env, no key_cmd, no
# host-gated env key, no credential pool) — substituting it while any of those
# could still mint a credential is what turned a working ``key_cmd`` endpoint
# into a 401.
KEYLESS_CUSTOM_API_KEY = "dummy-key"

# The constructor-routing fields AIAgent takes beside provider/base_url/api_key.
# They travel with the connection: a bundle that replaces the endpoint and the
# credential but leaves these behind builds an agent whose wire protocol
# (api_mode), transport (ACP subprocess) or credential source (pool) still
# points at the previous authority.
CUSTOM_CONNECTION_SIDE_FIELDS = ("api_mode", "acp_command", "acp_args", "credential_pool")

# Alias spellings accepted for a record's ``api_mode`` / ``transport``, mirroring
# hermes_cli.config_providers._canonical_api_mode. Resolved locally rather than
# imported so a record's own transport survives even when the installed runtime
# module is unavailable (or replaced by a test double).
_API_MODE_ALIASES = {
    "chat-completions": "chat_completions",
    "chatcompletions": "chat_completions",
    "openai": "chat_completions",
    "responses": "codex_responses",
    "openai_responses": "codex_responses",
    "openai-responses": "codex_responses",
    "anthropic": "anthropic_messages",
    "anthropic-messages": "anthropic_messages",
    "messages": "anthropic_messages",
    "bedrock": "bedrock_converse",
    "bedrock-converse": "bedrock_converse",
}
_VALID_API_MODES = {
    "chat_completions",
    "codex_responses",
    "anthropic_messages",
    "bedrock_converse",
    "codex_app_server",
}


def _custom_record_api_mode(record: dict) -> str | None:
    """Return the wire protocol a custom record declares for ITSELF, else None.

    ``transport:`` is the v12-migration spelling of ``api_mode:``; hand-edited
    configs still use either. An unrecognized value returns None so the runtime
    keeps its own host/provider detection instead of being handed nonsense.
    """
    for field in ("api_mode", "transport"):
        raw = record.get(field)
        if not isinstance(raw, str):
            continue
        cleaned = raw.strip()
        if not cleaned:
            continue
        canonical = _API_MODE_ALIASES.get(cleaned.lower(), cleaned).lower()
        if canonical in _VALID_API_MODES:
            return canonical
    return None


def _custom_record_acp_transport(record: dict) -> dict:
    """Return the ACP subprocess transport a custom record declares for ITSELF."""
    owned: dict = {}
    command = record.get("acp_command") or record.get("command")
    if isinstance(command, str) and command.strip():
        owned["acp_command"] = command.strip()
    args = record.get("acp_args")
    if args is None:
        args = record.get("args")
    if isinstance(args, (list, tuple)) and len(args) > 0:
        owned["acp_args"] = list(args)
    return owned


def _custom_record_pool_runtime(base_url: str | None, record: dict) -> dict | None:
    """Runtime dict from the credential pool that owns ``base_url``, else None.

    Delegates to the installed runtime's own pool lookup so pool ownership,
    ordering and the loopback placeholder behave exactly as they do for a CLI
    send. Best-effort: builds that do not expose the helper (or a stubbed
    runtime module) simply report no pool.
    """
    if not base_url:
        return None
    try:
        import hermes_cli.runtime_provider as _runtime_provider

        resolve_pool = getattr(_runtime_provider, "_try_resolve_from_custom_pool", None)
        if resolve_pool is None:
            return None
        return resolve_pool(
            base_url,
            "custom",
            _custom_record_api_mode(record),
            provider_name=str(record.get("provider_key") or record.get("name") or "") or None,
        )
    except Exception:
        return None


def _host_gated_env_key(base_url: str | None) -> str | None:
    """Env credential the installed runtime would accept for ``base_url``, else None.

    Host-GATED on purpose (GHSA-76xc-57q6-vm5m): the helper only yields
    OPENAI/OPENROUTER/``<VENDOR>``_API_KEY when the endpoint's host is the
    authoritative one, so this cannot leak a cloud key to an unrelated custom
    endpoint. Consulted here only so "is this endpoint genuinely keyless?" has
    the same answer in WebUI as it does at runtime.
    """
    if not base_url:
        return None
    try:
        import hermes_cli.runtime_provider as _runtime_provider

        candidates = getattr(_runtime_provider, "_host_gated_env_key_candidates", None)
        if candidates is None:
            return None
        for candidate in candidates(base_url, ollama=False):
            cleaned = str(candidate or "").strip()
            if cleaned:
                return cleaned
    except Exception:
        return None
    return None


def _custom_record_key_cmd_provider(base_url: str | None, record: dict, pid: str):
    """Per-request token provider for a record's ``key_cmd``, else None.

    ``key_cmd`` names a command that PRINTS a short-lived bearer; both wire
    clients accept a callable api_key and mint per request. It is a real
    credential source, so a record that declares one is NOT keyless and must
    never be handed :data:`KEYLESS_CUSTOM_API_KEY`.
    """
    key_cmd = str(record.get("key_cmd") or "").strip()
    if not key_cmd:
        return None
    try:
        from agent.command_token_source import build_command_token_provider

        return build_command_token_provider(
            key_cmd, str(record.get("name") or record.get("provider_key") or pid or "custom")
        )
    except Exception:
        logger.debug("key_cmd token provider unavailable for %s", pid, exc_info=True)
        return None


def _custom_record_declares_credential(
    record: dict,
    base_url: str | None,
    pool_runtime: dict | None,
) -> bool:
    """True when the record DECLARES any credential source, resolved or not.

    "Keyless" is a positive claim that an endpoint wants no authentication, and
    it is the only gate on :data:`KEYLESS_CUSTOM_API_KEY`. Deriving it from
    "``api_key`` came back empty" conflates two opposite situations: an
    unauthenticated local server, and an authenticated endpoint whose declared
    credential did not resolve (an ``${ENV}`` that is unset, a ``key_env``
    naming a missing variable, a configured pool that yielded nothing, an
    unbuildable ``key_cmd``). The second must NOT be handed the placeholder —
    that turns a missing-credential misconfiguration into an opaque 401 from the
    endpoint and hides the real cause.

    So the question asked here is declaration, not resolution: the RAW
    ``api_key`` (literal or ``${ENV}``), ``key_env``, ``key_cmd``, a configured
    ``credential_pool``, a pool that actually resolved for this endpoint, and a
    host-gated env credential the runtime would accept for it.
    """
    for field in ("api_key", "key_env", "key_cmd"):
        if str(record.get(field) or "").strip():
            return True
    # A configured pool is a declaration even when empty or exhausted: the user
    # pointed this endpoint at a credential source.
    if record.get("credential_pool") is not None:
        return True
    if pool_runtime:
        return True
    if _host_gated_env_key(base_url):
        return True
    return False


def _unowned_custom_provider_bundle(pid: str, slug: str, status: str) -> dict:
    """Bundle for a named ``custom:*`` route that NO authority owns.

    Every connection field is empty and ``keyless`` is False, so the merge below
    cannot substitute :data:`KEYLESS_CUSTOM_API_KEY`: "nobody owns this route" is
    not a claim that the route is unauthenticated.
    """
    return {
        "provider_id": pid,
        "slug": slug,
        "source": "",
        "status": status,
        "is_exact": False,
        "record": None,
        "base_url": None,
        "api_key": None,
        # Nothing owns the route, so nothing positively owns an endpoint for it
        # either. Stated rather than inferred: the merge must never read an
        # endpoint that reached it from somewhere else as this route's own.
        "endpoint_owned": False,
        "keyless": False,
        "owned": {},
    }


def resolve_custom_provider_bundle(
    provider_id: str,
    *,
    connection_resolver=None,
) -> dict | None:
    """Return the COMPLETE connection bundle a named ``custom:*`` record owns.

    ``None`` only when ``provider_id`` is not a named custom provider at all.
    When it IS one but nothing owns it, the result is an explicit unowned bundle
    (``status`` ``missing``/``malformed``, every connection field empty and
    ``keyless`` False) rather than ``None``, so the merge below can fail the
    route closed instead of silently leaving the ambient runtime's endpoint,
    credential and pool in place. Otherwise a dict with:

    ``base_url``
        the record's own endpoint (``None`` when it declares none).
    ``endpoint_owned``
        True only when the SELECTED record supplied that endpoint itself. It is
        the positive half of the provenance the merge needs: an endpoint already
        sitting in a caller's bundle, an endpoint that merely compares equal, and
        an absent runtime dict are all silence, and silence must never be read as
        "this endpoint belongs to the selected record". False here means the
        route has no endpoint of its own, whatever else is in flight.
    ``api_key``
        the record's own credential, resolved through the SAME ladder the
        runtime uses for a named custom provider: pool credential, then literal
        / ``${ENV}`` / ``key_env`` / ``CUSTOM_<SLUG>_API_KEY``, then a host-gated
        env key — with ``key_cmd`` overriding the static forms because it mints a
        fresh bearer per request. May be a callable (``key_cmd`` token provider).
    ``keyless``
        True only when the record DECLARES no credential source at all, i.e. the
        endpoint is genuinely unauthenticated. A declared-but-unresolved
        credential (unset ``${ENV}``, missing ``key_env`` variable, empty pool,
        unbuildable ``key_cmd``) leaves this False — see
        :func:`_custom_record_declares_credential`. This is the ONLY gate on
        :data:`KEYLESS_CUSTOM_API_KEY`.
    ``owned``
        the subset of :data:`CUSTOM_CONNECTION_SIDE_FIELDS` this record supplies
        itself. Callers keep these and must not overwrite them with the ambient
        runtime's values; fields ABSENT here are simply unowned, not proven
        foreign (see :func:`merge_custom_provider_runtime_bundle`).
    ``source`` / ``is_exact`` / ``record`` / ``status``
        the provenance of the selection, so a caller can tell an exact
        ``custom_providers[]`` row from a keyed fallback — and either of them
        from a route nothing owns (``status`` in
        :data:`CUSTOM_SELECTION_UNOWNED`).

    ``connection_resolver`` lets a caller inject its own module-bound reference
    to :func:`resolve_custom_provider_connection`, so monkeypatching that name in
    the caller's namespace still takes effect. An injected resolver can only
    report a URL/key pair — there is no record behind it — so the bundle it
    yields owns no side fields and the caller keeps the runtime's.
    """
    pid = str(provider_id or "").strip().lower()
    if not pid.startswith("custom:"):
        return None
    slug = _slug_key(pid)
    if not slug:
        # ``custom:`` with nothing behind it names no provider, so no record can
        # own it. Report it explicitly rather than as "not a custom route": the
        # caller asked to route somewhere and must fail closed, not inherit the
        # ambient connection.
        return _unowned_custom_provider_bundle(pid, slug, CUSTOM_SELECTION_MALFORMED)

    if connection_resolver is not None and connection_resolver is not resolve_custom_provider_connection:
        # Tolerate resolvers that predate/omit the provenance kwarg (older builds
        # and test doubles that patch in a plain two-value resolver).
        try:
            conn = connection_resolver(pid, return_provenance=True)
        except TypeError:
            conn = connection_resolver(pid)
        if len(conn) == 3:
            api_key, base_url, is_exact = conn
        else:
            api_key, base_url = conn
            is_exact = False
        if not (is_exact or api_key or base_url):
            # The injected resolver is the whole authority on this path, and it
            # reported nothing for the slug — so nothing owns the route.
            return _unowned_custom_provider_bundle(pid, slug, CUSTOM_SELECTION_MISSING)
        return {
            "provider_id": pid,
            "slug": slug,
            "source": "resolver",
            "status": CUSTOM_SELECTION_RESOLVER,
            "is_exact": bool(is_exact),
            "record": None,
            "base_url": base_url,
            "api_key": api_key,
            "endpoint_owned": bool(base_url),
            # A resolver reports a URL/key pair and nothing else; with no record
            # behind it there is no declaration to inspect, so an absent key is
            # the only keyless signal available here.
            "keyless": not api_key,
            "owned": {},
        }

    record, source, is_exact, status = _select_custom_provider_record(pid, slug, _get_cfg())
    if record is None:
        return _unowned_custom_provider_bundle(pid, slug, status)

    base_url = _custom_record_base_url(record)
    owned: dict = {}

    api_mode = _custom_record_api_mode(record)
    if api_mode:
        owned["api_mode"] = api_mode
    owned.update(_custom_record_acp_transport(record))

    # Pool first, exactly like the runtime's named-custom path: a pooled
    # endpoint's credential AND its pool object come from the same lookup.
    api_key = None
    pool_runtime = _custom_record_pool_runtime(base_url, record)
    if pool_runtime:
        api_key = pool_runtime.get("api_key") or None
        if pool_runtime.get("credential_pool") is not None:
            owned["credential_pool"] = pool_runtime.get("credential_pool")

    if not api_key:
        api_key = _resolve_custom_record_key(record.get("api_key"), record.get("key_env"), pid)
    if not api_key:
        api_key = _host_gated_env_key(base_url)

    # ``key_cmd`` beats a static api_key / key_env (short-lived bearers go stale
    # mid-session), but never displaces a pooled credential, which the pool
    # itself already rotates.
    if not pool_runtime:
        token_provider = _custom_record_key_cmd_provider(base_url, record, pid)
        if token_provider is not None:
            api_key = token_provider

    if "credential_pool" not in owned and record.get("credential_pool") is not None:
        owned["credential_pool"] = record.get("credential_pool")

    return {
        "provider_id": pid,
        "slug": slug,
        "source": source,
        "status": status,
        "is_exact": is_exact,
        "record": record,
        "base_url": base_url,
        "api_key": api_key,
        # POSITIVE endpoint provenance, resolved from the selected record and
        # from nothing else. The merge below pairs this record's credential with
        # an endpoint only while this is True.
        "endpoint_owned": bool(base_url),
        "keyless": not api_key
        and not _custom_record_declares_credential(record, base_url, pool_runtime),
        "owned": owned,
    }

# Merge / provenance helpers (appended: api/config.py _connection_identity..) -----

def _normalize_url(value: object) -> str | None:
    try:
        import api.config as _ac
        fn = getattr(_ac, "_normalize_base_url_for_match", None)
        if callable(fn):
            return fn(value)
    except Exception:
        pass
    try:
        import api._cfg.provider_helpers as _ph
        fn = getattr(_ph, "_normalize_base_url_for_match", None)
        if callable(fn):
            return fn(value)
    except Exception:
        pass
    return str(value or "").strip() or None

def _connection_identity(runtime_provider: dict, resolved_provider: str | None) -> str | None:
    """Name the provider the already-resolved connection fields belong to.

    The runtime provider dict names itself, so it wins when present. With no
    runtime dict the caller resolved the connection for ``resolved_provider``
    (every such call site passes ``requested=resolved_provider``), so that is
    the identity behind the fields.
    """
    rt_provider = str((runtime_provider or {}).get("provider") or "").strip()
    if rt_provider:
        return rt_provider.lower()
    return str(resolved_provider or "").strip().lower() or None


def _custom_bundle_endpoint_matches(bundle: dict, runtime_provider: dict) -> bool:
    """True when the runtime resolved the SAME endpoint the record owns.

    This is the provenance test that decides whether the ambient runtime's side
    fields are same-authority (keep) or foreign (clear). A record that declares
    no endpoint of its own cannot prove the runtime is foreign, so its runtime
    fields stand.

    Endpoint equality alone is NOT provenance. Two providers are free to share a
    base_url — a gateway fronting several accounts, a local proxy, the same host
    reached with different keys — so a URL match between a record and the ambient
    runtime says only that both point at one host, never that one authority
    resolved both. When the runtime dict NAMES itself and that name is some other
    provider, the identities settle it directly and the shared URL proves
    nothing.
    """
    if not isinstance(runtime_provider, dict) or not runtime_provider:
        return False
    rt_provider = str(runtime_provider.get("provider") or "").strip().lower()
    if rt_provider:
        slug = str(bundle.get("slug") or "").strip().lower()
        owned_names = {
            str(bundle.get("provider_id") or "").strip().lower(),
            slug,
            f"custom:{slug}" if slug else "",
            # The generic ``custom`` runtime is the shape the WebUI writes for
            # whichever custom provider is active, so it is not a competing name.
            "custom",
        }
        owned_names.discard("")
        if rt_provider not in owned_names:
            return False
    record_base_url = bundle.get("base_url")
    rt_base_url = runtime_provider.get("base_url")
    if not record_base_url and not rt_base_url:
        return True
    if not record_base_url or not rt_base_url:
        return False
    return _normalize_url(record_base_url) == _normalize_url(
        rt_base_url
    )


def _custom_runtime_endpoint_is_record_owned(bundle: dict, runtime_provider: dict) -> bool:
    """True when the runtime dict positively originated from the selected RECORD.

    Endpoint equality alone is NOT selected-record provenance: two distinct
    records (e.g. an exact list row and a same-slug keyed record) can share a
    normalized URL while carrying different credentials. A tie requires either
    an explicit source-record identity or matching record credentials;
    silence, missing runtime dicts, and distinct records sharing an endpoint
    never establish that the runtime spelling belongs to the selected record.
    """
    if not bundle.get("base_url"):
        return False
    if not isinstance(runtime_provider, dict) or not runtime_provider.get("base_url"):
        return False
    # Credential conflict strictly rejects a tie before checking identity metadata
    bundle_key = bundle.get("api_key")
    rt_key = runtime_provider.get("api_key")
    if bundle_key and rt_key and bundle_key != rt_key:
        return False
    rec = bundle.get("record")
    rt_rec = runtime_provider.get("record") or runtime_provider.get("source_record")
    if rt_rec is not None:
        return rt_rec == rec and _custom_bundle_endpoint_matches(bundle, runtime_provider)
    rec_id = bundle.get("record_id") or (rec.get("id") if isinstance(rec, dict) else None)
    rt_id = runtime_provider.get("record_id")
    if rt_id is not None:
        return rt_id == rec_id and _custom_bundle_endpoint_matches(bundle, runtime_provider)
    if bundle_key and rt_key and bundle_key == rt_key:
        return _custom_bundle_endpoint_matches(bundle, runtime_provider)
    return False


def merge_custom_provider_runtime_bundle(
    resolved_provider: str | None,
    resolved_api_key: str | None,
    resolved_base_url: str | None,
    runtime_provider: dict | None = None,
    *,
    lookup_provider: str | None = None,
    connection_resolver=None,
) -> dict:
    """Return the COMPLETE constructor-routing bundle for one send attempt.

    Keys: ``provider``, ``base_url``, ``api_key``, every field in
    :data:`CUSTOM_CONNECTION_SIDE_FIELDS`, and
    :data:`CUSTOM_ROUTE_ERROR_FIELD`. Callers must apply the WHOLE dict — that is
    the point: the connection and the transport/protocol/pool fields are one
    authority, and the agent-cache signature must be derived from the same dict
    so a bundle change always mints a new agent.

    :data:`CUSTOM_ROUTE_ERROR_FIELD` is the bundle's TERMINAL verdict, and it is
    not optional to check. A named ``custom:`` route that resolves no complete
    ``(api_key, base_url)`` pair is not constructor-ready: Hermes Agent reads the
    missing field as permission to resolve another provider, so every consumer
    that builds an AIAgent (or writes the agent cache, or feeds an auxiliary
    client) must run the bundle through :func:`raise_for_custom_provider_route`
    first and fail the turn with a controlled provider / missing-credential
    error. See the :data:`CUSTOM_ROUTE_ERROR_FIELD` commentary for the exact
    constructor branch this defends.

    Every consumer that builds an AIAgent for a ``custom:<slug>`` route goes
    through here so the endpoint, the credential and the routing fields all come
    from ONE record. The fill-only pattern this replaces
    (``if not api_key: api_key = ...``) mixed authorities whenever the runtime
    provider had already supplied a truthy value from a same-slug keyed
    ``providers:`` record: resolution deterministically produced the
    ``custom_providers[]`` row's URL while keeping the keyed row's API key.

    Ownership rules, per side field:

    * the selected record declares it -> the record's value wins (an exact list
      row's ``api_mode: anthropic_messages`` is not "ambient noise" to be
      cleared, and a keyed record keeps the pool/transport it owns);
    * the record declares no endpoint of its own, or the runtime resolved the
      SAME endpoint -> the runtime's value is same-authority and is kept;
    * otherwise the runtime resolved a DIFFERENT authority -> the field is
      cleared, because passing it through is what let a custom HTTP endpoint
      inherit Anthropic credential pooling and a Claude ACP subprocess.

    The ENDPOINT itself is decided by positive provenance only. A selected
    config record supplies the endpoint its credential is sent to, and the one
    exception is a runtime dict positively tied to that same record (same
    identity, same normalized URL), whose spelling is the one it can reach.
    Everything else is silence: the ``resolved_base_url`` the caller arrived
    with, a URL that merely compares equal, and a missing runtime dict say
    nothing about the record selected for this slug. So a record that declares
    no usable ``base_url`` does not borrow one — the route is terminal with
    :data:`CUSTOM_ROUTE_NO_ENDPOINT` and the bundle keeps neither the incoming
    endpoint nor this record's credential, because pairing them is precisely how
    a non-active provider's secret reached the ACTIVE provider's URL.

    The CREDENTIAL of an exact ``custom_providers[]`` row is exempt from the
    same-endpoint rule above: it is resolved from that row's own ladder and is
    never inherited from the runtime, because a same-slug keyed record may
    declare the identical ``base_url`` and endpoint equality would then be
    enough to hand the row somebody else's key.

    :data:`KEYLESS_CUSTOM_API_KEY` is substituted only once the selected record
    reports that it DECLARES no credential source at all. A record that declares
    one which produced nothing here (an unset ``${ENV}``, a ``key_env`` naming a
    missing variable, an empty pool, an unbuildable ``key_cmd``) is sent without
    a key rather than with a placeholder that guarantees a 401 and hides the
    real cause.

    A named ``custom:<slug>`` route that NO authority owns fails closed: the
    returned bundle keeps the named provider but carries no endpoint, no
    credential, no pool/transport and no placeholder key, so an unknown slug can
    never be routed through the ambient provider's connection. This holds even
    when the ambient runtime LABELS itself with that same slug — a provider
    string is self-assigned, not proof that the connection belongs to the slug.
    """
    return _custom_provider_runtime_bundle_with_provenance(
        resolved_provider,
        resolved_api_key,
        resolved_base_url,
        runtime_provider,
        lookup_provider=lookup_provider,
        connection_resolver=connection_resolver,
    )[0]


def _custom_provider_runtime_bundle_with_provenance(
    resolved_provider: str | None,
    resolved_api_key: str | None,
    resolved_base_url: str | None,
    runtime_provider: dict | None = None,
    *,
    lookup_provider: str | None = None,
    connection_resolver=None,
) -> tuple[dict, dict | None]:
    """:func:`merge_custom_provider_runtime_bundle` plus the record it selected.

    Returns ``(bundle, custom)`` where ``custom`` is the
    :func:`resolve_custom_provider_bundle` result the merge applied (``None``
    when the route is not a named custom provider, or no record matched).
    Callers that also need the provenance take it from here rather than
    re-resolving: a second resolution re-reads the config and can mint a second
    ``key_cmd`` token provider or select a different pool entry.
    """
    _rt = runtime_provider if isinstance(runtime_provider, dict) else {}
    bundle = {
        "provider": resolved_provider,
        "base_url": resolved_base_url,
        "api_key": resolved_api_key,
        "api_mode": _rt.get("api_mode"),
        "acp_command": _rt.get("acp_command", _rt.get("command")),
        "acp_args": _rt.get("acp_args", _rt.get("args")),
        "credential_pool": _rt.get("credential_pool"),
        # Routable until a named custom route proves otherwise. Non-custom
        # routes legitimately leave the endpoint and/or credential to the
        # runtime provider, so only the named-``custom:`` branches below can
        # record a verdict here.
        CUSTOM_ROUTE_ERROR_FIELD: None,
    }

    lookup = lookup_provider or resolved_provider
    if not (isinstance(lookup, str) and lookup.startswith("custom:")):
        return bundle, None

    custom = resolve_custom_provider_bundle(lookup, connection_resolver=connection_resolver)
    if custom is None:
        return bundle, None

    if custom["status"] in CUSTOM_SELECTION_UNOWNED:
        # NO authority owns this slug: not an exact ``custom_providers[]`` row,
        # not a keyed ``providers:`` record, not a ``model:`` authority. Whatever
        # is already in ``bundle`` was resolved by someone else and reached us as
        # ambient state, so the whole bundle fails closed.
        #
        # A provider LABEL is not proof of ownership. The runtime dict's
        # ``provider: "custom:ghost"`` is a self-assigned string on a connection
        # this process authenticated for some other reason, and
        # :func:`_connection_identity` falls back to ``resolved_provider`` — the
        # very slug being looked up — so a caller that passes no runtime dict at
        # all would match itself. Trusting either would let any unknown slug
        # claim the ambient endpoint, credential, pool and transport simply by
        # naming itself. Ownership is decided by config records, and there are
        # none here.
        #
        # So: no endpoint and no credential; no ambient pool/transport/
        # wire-protocol carried alongside them; the provider stays the NAMED
        # slug, because rewriting it to the generic ``custom`` would present an
        # unresolvable route as a resolved one; and no KEYLESS_CUSTOM_API_KEY —
        # ``keyless`` is False on an unowned bundle precisely so the placeholder
        # cannot claim that an endpoint we never found is unauthenticated.
        logger.warning(
            "custom provider %s matches no custom_providers[] row, keyed "
            "providers[] record or model: authority, and the resolved connection "
            "belongs to %s; refusing to route it through that provider's "
            "endpoint and credential",
            lookup,
            _connection_identity(_rt, resolved_provider) or "an unnamed provider",
        )
        bundle["base_url"] = None
        bundle["api_key"] = None
        for field in CUSTOM_CONNECTION_SIDE_FIELDS:
            bundle[field] = None
        # Clearing the fields is NOT what makes this terminal — the constructor
        # reads an empty pair as "route me somewhere else". Record the verdict so
        # consumers stop before AIAgent instead.
        bundle[CUSTOM_ROUTE_ERROR_FIELD] = _custom_route_verdict(
            CUSTOM_ROUTE_UNOWNED, lookup
        )
        return bundle, custom

    same_authority = _custom_bundle_endpoint_matches(custom, _rt)
    # The positive tie: the runtime dict resolved the very endpoint the selected
    # record declares. Never True on absence of evidence, so it is the only
    # signal allowed to keep an endpoint this record did not supply itself.
    endpoint_tied = _custom_runtime_endpoint_is_record_owned(custom, _rt)

    if custom["record"] is not None and not custom["endpoint_owned"]:
        # A CONFIG RECORD owns this slug — an exact ``custom_providers[]`` row, a
        # keyed ``providers['custom:<slug>']``, a raw ``providers:<key>`` row (the
        # standard v12 shape) or a ``model:`` authority — and it declares no
        # usable endpoint. So this route HAS no endpoint: the one sitting in
        # ``bundle`` was resolved for whoever the process was already talking to
        # (the ACTIVE provider) and reached us as ambient state.
        #
        # Keeping it would pair THIS record's credential with THAT provider's
        # URL — the user's prompt and a non-active provider's secret delivered to
        # an endpoint neither the record nor the user named. The installed
        # Agent's ``_match_new_style_provider()`` skips an endpoint-less raw
        # record for exactly this reason.
        #
        # Nothing here may stand in for the missing endpoint: not the caller's
        # ``resolved_base_url``, not a URL that merely compares equal (a gateway
        # fronting two accounts is one host and two authorities), and not an
        # absent runtime dict. Absence of contrary evidence is not provenance.
        # So the route fails closed on its OWN name — the terminal
        # ``custom_provider_endpoint_unresolved``, naming the setting to fix —
        # rather than degrading into a send through the active provider.
        bundle["base_url"] = None
        bundle["api_key"] = None
        for field in CUSTOM_CONNECTION_SIDE_FIELDS:
            # The record keeps what it declares for ITSELF; every other side
            # field belongs to the ambient authority whose endpoint was just
            # refused, so it goes with it. Leaving those behind would hand the
            # refused provider's credential pool and ACP transport to a route
            # that is about to be reported unresolvable.
            bundle[field] = custom["owned"].get(field)
        bundle[CUSTOM_ROUTE_ERROR_FIELD] = _custom_route_verdict(
            CUSTOM_ROUTE_NO_ENDPOINT, custom["provider_id"]
        )
        logger.warning(
            "custom provider %s resolved no endpoint of its own; refusing to pair "
            "its credential with the connection resolved for %s",
            custom["provider_id"],
            _connection_identity(_rt, resolved_provider) or "another provider",
        )
        return bundle, custom

    if custom["is_exact"]:
        # An exact ``custom_providers[]`` row is authoritative for its slug: BOTH
        # the endpoint (including None when the row's base_url is blank) and the
        # credential are replaced, never merged — the sole exception being the
        # runtime's spelling of the row's OWN endpoint, handled below. The
        # credential comes from the
        # ROW's own ladder (pool, api_key/``${ENV}``/``key_env``/
        # ``CUSTOM_<SLUG>_API_KEY``, ``key_cmd``, host-gated env) and from
        # nowhere else — including when ``same_authority`` holds.
        #
        # Endpoint equality is NOT record provenance. A same-slug keyed
        # ``providers["custom:<slug>"]`` record is free to declare the very same
        # ``base_url``, and the ambient runtime dict carrying that URL is then
        # indistinguishable by URL from one the row itself resolved. Accepting
        # ``_rt["api_key"]`` on that evidence hands the keyed row's credential to
        # the exact row, which is precisely the split-authority merge this
        # function exists to stop: the row's URL with the keyed row's key.
        #
        # So a row whose declared credential produced nothing here keeps
        # ``api_key`` None and falls through to the terminal-route verdict below,
        # naming the setting to fix, rather than silently borrowing a credential
        # the row never declared. A row that declares NO credential at all is
        # reported ``keyless`` by the resolution above and gets
        # :data:`KEYLESS_CUSTOM_API_KEY` there — that is the row's own statement
        # that the endpoint is unauthenticated, not an inherited key either.
        bundle["api_key"] = custom["api_key"] or None
        # Same endpoint rule as the keyed branch below: the row's endpoint wins
        # unless the runtime dict is POSITIVELY tied to it — same identity, same
        # normalized URL — in which case the runtime's spelling of the ROW's own
        # endpoint is kept, because that is the form it can actually reach. This
        # is not a merge: an endpoint the row did not declare can never survive
        # here, since ``endpoint_tied`` is False whenever the row supplied none.
        bundle["base_url"] = _rt.get("base_url") if endpoint_tied else custom["base_url"]
    elif custom["record"] is not None:
        # No exact row, but a CONFIG RECORD was selected: a keyed
        # ``providers['custom:<slug>']``, a raw ``providers:<key>`` row (the
        # standard v12 shape) or a ``model:`` authority, picked as ONE complete
        # record. The endpoint and the credential must therefore come from it
        # together, decided by that record's OWN endpoint provenance and by
        # nothing that happened to be in flight when it was selected. A record
        # that declares NO endpoint never reaches here — the terminal branch
        # above already refused it rather than let it borrow one.
        #
        # The record DECLARES an endpoint, so that endpoint and the credential
        # sent to it are one authority's pair and the record's credential is the
        # only one that may accompany it — including when the ambient runtime
        # reports the SAME URL. A shared base_url is not shared provenance: a
        # gateway fronting two accounts hands both providers one host and two
        # different keys, and taking ``_rt["api_key"]`` there sends the ACTIVE
        # provider's secret to the non-active record's endpoint. A record whose
        # declared credential source resolved nothing keeps ``api_key`` None and
        # falls through to the terminal verdict naming the setting to fix, rather
        # than borrowing the ambient key across that host.
        bundle["api_key"] = custom["api_key"] or None
        # The record's endpoint wins unless the runtime dict is POSITIVELY tied
        # to it — same identity, same normalized URL — in which case the
        # runtime's spelling is kept, because a normalized form of the same
        # endpoint is the one it can actually reach. Any other endpoint already
        # in the bundle was resolved before this record was selected: absent
        # provenance is not a tie, so it is displaced rather than left beside the
        # credential this record just supplied.
        bundle["base_url"] = _rt.get("base_url") if endpoint_tied else custom["base_url"]
    else:
        # No record behind the selection: an injected ``connection_resolver``
        # reported a bare URL/key pair and IS the whole authority on this path.
        # There is no record to take endpoint provenance from, so this keeps the
        # historical fill-only shape — the caller's already-resolved endpoint
        # stands and the resolver fills only what it left empty (#2271).
        if custom["base_url"]:
            bundle["api_key"] = custom["api_key"] or None
        elif same_authority and _rt.get("api_key"):
            # The resolver reported NO endpoint, so the runtime's URL is filling
            # a hole rather than being displaced — and the ambient endpoint's own
            # credential is the coherent partner for it.
            bundle["api_key"] = _rt.get("api_key")
        elif custom["api_key"]:
            bundle["api_key"] = custom["api_key"]
        if custom["base_url"] and _rt.get("base_url") and not same_authority:
            bundle["base_url"] = custom["base_url"]
        elif not bundle["base_url"] and custom["base_url"]:
            bundle["base_url"] = custom["base_url"]

    for field in CUSTOM_CONNECTION_SIDE_FIELDS:
        if field in custom["owned"]:
            # The selected record declares this field for ITSELF, so it wins over
            # the ambient runtime even when the two disagree: an exact list row's
            # ``api_mode: anthropic_messages`` is the row's wire protocol, not
            # ambient noise, and a keyed record keeps the pool/ACP transport it
            # declares.
            bundle[field] = custom["owned"][field]
        elif not same_authority:
            # The record owns an endpoint the runtime did NOT resolve, so this
            # value provably came from a different authority. Clearing it is what
            # stops a custom HTTP endpoint inheriting Anthropic credential pooling
            # and a Claude ACP subprocess.
            bundle[field] = None
        # else: the runtime resolved the SAME endpoint the record owns, so its
        # value is same-authority and the seed above already kept it.

    if bundle["base_url"]:
        # Route through the generic custom OpenAI-compatible client once the
        # named provider has supplied the concrete endpoint. Keeping the provider
        # as custom:<slug> would make Agent init synthesize invalid env-var hints
        # like CUSTOM:SOMETHING-8000_API_KEY on keyless setups.
        bundle["provider"] = "custom"
        if not bundle["api_key"]:
            if custom["keyless"]:
                # Only now, with the record's full credential ladder exhausted
                # (pool, api_key, key_env, CUSTOM_<SLUG>_API_KEY, key_cmd,
                # host-gated env) and the runtime offering nothing either, is the
                # endpoint provably keyless.
                bundle["api_key"] = KEYLESS_CUSTOM_API_KEY
            else:
                # The record DECLARES a credential source that did not yield one
                # here (e.g. a ``key_cmd`` whose token provider could not be
                # built). Substituting the placeholder would turn that into a
                # silent 401 against an endpoint that does require auth; leave the
                # credential unset so the failure names its real cause.
                logger.warning(
                    "custom provider %s declares a credential source that produced "
                    "no key; sending without one rather than the keyless placeholder",
                    custom["provider_id"],
                )

    if not (bundle["api_key"] and bundle["base_url"]):
        # A record OWNS this route, but the merge could not produce the complete
        # explicit pair AIAgent needs to honour it. Leaving the hole would hand
        # the send straight back to ``_routed_client_kwargs()``, which re-resolves
        # the provider and can reach the ambient endpoint, a keyed row this record
        # deliberately displaced, or the init-time fallback chain. Name the real
        # cause instead and let the caller fail the turn.
        bundle[CUSTOM_ROUTE_ERROR_FIELD] = _custom_route_verdict(
            CUSTOM_ROUTE_NO_CREDENTIAL if bundle["base_url"] else CUSTOM_ROUTE_NO_ENDPOINT,
            custom["provider_id"],
        )
        logger.warning(
            "custom provider %s resolved no routable connection (%s); refusing to "
            "build an agent that would re-enter provider routing",
            custom["provider_id"],
            bundle[CUSTOM_ROUTE_ERROR_FIELD]["reason"],
        )

    return bundle, custom


def apply_custom_provider_connection_authority(
    resolved_provider: str | None,
    resolved_api_key: str | None,
    resolved_base_url: str | None,
    *,
    lookup_provider: str | None = None,
    connection_resolver=None,
    runtime_provider: dict | None = None,
) -> tuple[str | None, str | None, str | None, bool]:
    """Connection-only VIEW of :func:`merge_custom_provider_runtime_bundle`.

    Returns ``(provider, api_key, base_url, custom_owned)``, where
    ``custom_owned`` reports whether a config-owned custom record supplied the
    connection — False for a named route nothing owns, whose key and base_url
    come back ``None`` rather than as the ambient runtime's. Kept for callers
    that genuinely construct nothing else (capability/vision lookups); anything
    that builds an AIAgent must take the whole bundle instead and honour its
    :data:`CUSTOM_ROUTE_ERROR_FIELD` verdict, because the three connection fields
    alone are neither a complete constructor contract nor a terminal refusal.
    This view deliberately does NOT raise: a capability lookup asking "can this
    route do vision" is not a constructor boundary and must not turn into a
    failed turn.
    """
    bundle, custom = _custom_provider_runtime_bundle_with_provenance(
        resolved_provider,
        resolved_api_key,
        resolved_base_url,
        runtime_provider,
        lookup_provider=lookup_provider,
        connection_resolver=connection_resolver,
    )
    custom_owned = custom is not None and custom["status"] not in CUSTOM_SELECTION_UNOWNED
    return bundle["provider"], bundle["api_key"], bundle["base_url"], custom_owned


# Subprocess ACP transports (Cursor/Copilot CLI). Model IDs often contain '/'
# but must still route via explicit @provider:model so they do not fall through
# to the configured default HTTP provider (e.g. openai-codex).
