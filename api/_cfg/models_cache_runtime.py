"""TTL + in-memory cache runtime helpers extracted from api/config.py.

Re-exported via ``api.config`` so ``from api.config import invalidate_models_cache``
keeps working.  No external module should import from ``api._cfg.models_cache_runtime`` directly.
"""

from __future__ import annotations

import copy
import logging
import time


logger = logging.getLogger(__name__)

# Lazy helpers for cross-module symbols
def _get_models_cache_path(*a, **kw):
    try:
        import api.config as _ac
        return _ac._get_models_cache_path(*a, **kw)
    except Exception:
        from api._cfg.models_cache import _get_models_cache_path as _real
        return _real(*a, **kw)

def _delete_models_cache_on_disk(*a, **kw):
    try:
        import api.config as _ac
        return _ac._delete_models_cache_on_disk(*a, **kw)
    except Exception:
        from api._cfg.models_cache import _delete_models_cache_on_disk as _real2
        return _real2(*a, **kw)

def _models_cache_source_fingerprint(*a, **kw):
    try:
        import api.config as _ac
        return _ac._models_cache_source_fingerprint(*a, **kw)
    except Exception:
        from api._cfg.models_cache import _models_cache_source_fingerprint as _real3
        return _real3(*a, **kw)

def _is_valid_models_cache(*a, **kw):
    try:
        import api.config as _ac
        return _ac._is_valid_models_cache(*a, **kw)
    except Exception:
        from api._cfg.models_cache import _is_valid_models_cache as _real4
        return _real4(*a, **kw)

def _annotate_fast_tier_model_groups(*a, **kw):
    try:
        import api.config as _ac
        return _ac._annotate_fast_tier_model_groups(*a, **kw)
    except Exception:
        from api._cfg.advanced import _annotate_fast_tier_model_groups as _real5
        return _real5(*a, **kw)

def _resolve_provider_alias(*a, **kw):
    try:
        import api.config as _ac
        return _ac._resolve_provider_alias(*a, **kw)
    except Exception:
        return str(a[0]).strip().lower() if a else ""

def _credential_pool_profile_tag(*a, **kw):
    try:
        import api.config as _ac
        return _ac._credential_pool_profile_tag(*a, **kw)
    except Exception:
        return ""

def _sync_models_cache_provenance() -> None:
    import api.config as _ac  # monkeypatch-aware proxy
    """Republish the atomic (snapshot, fingerprint) provenance pair.

    MUST be called at every site that assigns ``_ac._available_models_cache`` and
    ``_ac._available_models_cache_source_fingerprint`` (publish and invalidate),
    AFTER both have been set. It snapshots the current pair into one immutable
    tuple so ``_endpoint_advertised_model_ids`` reads both consistently with a
    single lock-free load. A reader that races between an underlying assignment
    and this call sees the PREVIOUS consistent tuple (never a torn pair); once
    this runs, readers see the new consistent pair.
    """
    snap = _ac._available_models_cache
    _ac._models_cache_provenance = (
        (snap, _ac._available_models_cache_source_fingerprint) if snap is not None else None
    )


def _endpoint_advertised_model_ids(provider_id: str | None) -> frozenset | None:
    import api.config as _ac  # monkeypatch-aware proxy
    """Model ids the given provider's group advertised in the current catalog.

    Reads ONLY the already-published in-memory catalog snapshot
    (``_ac._available_models_cache``) — it never builds, live-probes, or touches
    disk, so it is safe to call on the per-turn send hot path. Returns:

      * a ``frozenset`` of the ids advertised by ``provider_id``'s own group
        (bare ids for the active provider, e.g. ``x-ai/grok-4.5``), or
      * ``None`` when the catalog is cold/unbuilt OR the provider has no group.

    ``None`` means "no provenance signal available" — callers MUST treat that as
    "preserve the model id verbatim" so a cache miss never silently strips a
    vendor namespace off an id the user actively selected (#5979). Scoping to
    the provider's OWN group prevents a same-named id in a sibling group (e.g.
    an ``openai/gpt-5.4`` sitting in the OpenRouter group) from masquerading as
    something this custom endpoint advertised.
    """
    # Single lock-free atomic read of the immutable (snapshot, fingerprint) pair
    # published by _sync_models_cache_provenance(). Reading one tuple can never
    # tear, and acquiring no lock means this per-send check adds no lock-ordering
    # edge (no _cfg_lock ↔ _ac._available_models_cache_lock deadlock) and never waits
    # behind a catalog rebuild.
    provenance = _ac._models_cache_provenance
    if provenance is None:
        return None
    snapshot, published_fp = provenance
    if snapshot is None:
        return None
    # Profile-isolation fail-safe (profiles are islands): the catalog cache is a
    # process global, so a concurrently-active profile could have published the
    # snapshot we're now reading. Only trust it for provenance when the
    # fingerprint captured AT PUBLISH TIME still matches the current runtime
    # fingerprint — the ``config_yaml`` axis of that fingerprint is the
    # PROFILE-SPECIFIC config path (_get_config_path -> get_active_hermes_home),
    # so a match guarantees the snapshot belongs to the profile asking. Any
    # mismatch (foreign profile, config edit, stale) returns None so the caller
    # preserves the id verbatim rather than stripping against another profile's
    # catalog.
    try:
        if published_fp != _models_cache_source_fingerprint():
            return None
    except Exception:
        return None  # fingerprint unavailable → no trustworthy provenance
    memo = _ac._advertised_model_ids_memo
    # Identity check (``is``), not id(): holding the snapshot reference in the
    # memo keeps it alive, so a freed-then-reused id() can't cause a false hit.
    if memo is None or memo[0] is not snapshot:
        by_slug: dict[str, frozenset] = {}
        try:
            groups = snapshot.get("groups", []) or []
        except AttributeError:
            return None
        for group in groups:
            if not isinstance(group, dict):
                continue
            slug = str(group.get("provider_id") or "").strip().lower()
            if not slug:
                continue
            # Union BOTH catalog buckets: a provider's models can be split across
            # ``models`` (visible) and ``extra_models`` (overflow) by the picker,
            # so an id the endpoint genuinely advertised may live in either. Only
            # reading ``models`` would miss it and mis-resolve (e.g. leave the
            # #433 bare id unstripped because it sits in extra_models).
            ids = frozenset(
                str(m.get("id"))
                for bucket in ("models", "extra_models")
                for m in (group.get(bucket) or [])
                if isinstance(m, dict) and m.get("id")
            )
            by_slug[slug] = by_slug.get(slug, frozenset()) | ids
        memo = (snapshot, by_slug)
        _ac._advertised_model_ids_memo = memo
    slug = str(provider_id or "").strip().lower()
    return memo[1].get(slug)


def _should_warn_budget(reason: str, cooldown_s: float | None = None) -> bool:
    """Return True iff the budget warning for ``reason`` should log at
    warning level (first hit, or last warn-level emit was more than
    ``cooldown_s`` seconds ago). Otherwise False — the caller should demote
    to info for the same payload so the signal is retained but the noise is
    capped. Thread-safe; the cooldown is shared across all live-rebuild
    callers in this process.
    """
    import api.config as _ac  # monkeypatch-aware proxy
    cooldown = (
        _ac._BUDGET_WARN_COOLDOWN_SECONDS if cooldown_s is None else float(cooldown_s)
    )
    now = time.monotonic()
    with _ac._BUDGET_WARN_LOCK:
        last = _ac._BUDGET_WARN_STATE.get(reason)
        if last is None or (now - last) >= cooldown:
            _ac._BUDGET_WARN_STATE[reason] = now
            return True
        return False


def _get_fresh_memory_models_cache(now: float) -> dict | None:
    import api.config as _ac  # monkeypatch-aware proxy
    """Return a valid fresh in-memory /api/models cache, or clear stale shapes."""
    if _ac._available_models_cache is None:
        return None
    if (now - _ac._available_models_cache_ts) >= _ac._AVAILABLE_MODELS_CACHE_TTL:
        return None
    current_sources = _models_cache_source_fingerprint()
    if _ac._available_models_cache_source_fingerprint != current_sources:
        logger.debug(
            "models memory cache rejected: source_fingerprint=%r vs runtime=%r",
            _ac._available_models_cache_source_fingerprint,
            current_sources,
        )
        _ac._available_models_cache = None
        _ac._available_models_cache_ts = 0.0
        _ac._available_models_live_rebuild_ts = 0.0
        _ac._available_models_cache_source_fingerprint = None
        _sync_models_cache_provenance()
        return None
    if _is_valid_models_cache(_ac._available_models_cache):
        return _annotate_fast_tier_model_groups(copy.deepcopy(_ac._available_models_cache))
    _ac._available_models_cache = None
    _ac._available_models_cache_ts = 0.0
    _ac._available_models_live_rebuild_ts = 0.0
    _ac._available_models_cache_source_fingerprint = None
    _sync_models_cache_provenance()
    return None


def invalidate_models_cache(*, delete_disk: bool = True):
    import api.config as _ac  # monkeypatch-aware proxy
    """Force the TTL cache for get_available_models() to be cleared.

    Call this after modifying config.cfg in-memory (e.g. in tests) so
    the next call to get_available_models() picks up the changes rather
    than returning a stale cached result.

    Also deletes the on-disk cache so that a subsequent cold build does
    not immediately reload a stale disk snapshot and skip the fresh build.
    This is essential for test isolation: without the disk delete, tests
    that call invalidate_models_cache() still get back the previous test's
    result from the disk cache because the disk hit is checked before the memory
    cache rebuild runs.

    ``delete_disk=False`` keeps the fingerprint-guarded disk snapshot (profile switch).
    """
    with _ac._available_models_cache_lock:
        _ac._available_models_cache = None
        _ac._available_models_cache_ts = 0.0
        _ac._available_models_live_rebuild_ts = 0.0
        _ac._available_models_cache_source_fingerprint = None
        _sync_models_cache_provenance()
        _ac._cache_build_in_progress = False
        _ac._cache_build_cv.notify_all()
        # Clear the credential pool cache too (all profiles). Without this,
        # tests (and live provider key edits) see a stale CredentialPool from a
        # prior auth_store payload — the test_credential_pool_providers suite was
        # hitting this directly. A full reset is intentionally profile-wide.
        _ac._CREDENTIAL_POOL_CACHE.clear()
    # Also delete the disk cache so the next cold build starts fresh.
    # Disk delete is outside the lock — file I/O shouldn't block other readers.
    if delete_disk:
        _delete_models_cache_on_disk()
    try:
        from api.plugin_providers import invalidate_plugin_model_provider_cache

        invalidate_plugin_model_provider_cache()
    except Exception:
        pass


def invalidate_credential_pool_cache(provider_id: str):
    import api.config as _ac  # monkeypatch-aware proxy
    """Invalidate the credential pool cache for a specific provider.

    Used by the streaming layer's credential self-heal logic (#1401) to
    force a fresh credential pool load after re-reading auth.json.
    """
    with _ac._available_models_cache_lock:
        _cp_tag = _credential_pool_profile_tag()
        _ac._CREDENTIAL_POOL_CACHE.pop((_cp_tag, provider_id), None)
        _ac._CREDENTIAL_POOL_CACHE.pop((_cp_tag, _resolve_provider_alias(provider_id)), None)
    try:
        # api.providers imports from api.config; keep this lazy to avoid
        # import-cycle/module-initialization issues.
        from api.providers import invalidate_account_usage_status_cache

        invalidate_account_usage_status_cache(provider_id)
        invalidate_account_usage_status_cache(_resolve_provider_alias(provider_id))
    except Exception:
        logger.debug("Failed to invalidate account usage status cache", exc_info=True)


def invalidate_provider_models_cache(provider_id: str):
    import api.config as _ac  # monkeypatch-aware proxy
    """Invalidate cached models for a single provider.

    Also invalidates the full cache so that the next get_available_models()
    call rebuilds all groups cleanly (the rebuilt provider is merged with any
    other cached groups from the 24h TTL window).  After the next
    get_available_models() call, _ac._provider_models_invalidated_ts[provider_id]
    is cleared so the provider's fresh models are used.

    Args:
        provider_id: canonical provider id (e.g. 'openai', 'anthropic', 'custom:my-key')
    """
    with _ac._available_models_cache_lock:
        _ac._available_models_cache = None
        _ac._available_models_cache_ts = 0.0
        _ac._available_models_live_rebuild_ts = 0.0
        _ac._available_models_cache_source_fingerprint = None
        _sync_models_cache_provenance()
        _ac._provider_models_invalidated_ts[provider_id] = time.time()
        # Also evict the credential pool so the next cold path re-loads it.
        # Must evict both the original key and its canonical form (load_pool
        # may be called with either, and both paths cache under their own key),
        # scoped to the active profile's cache key.
        _cp_tag = _credential_pool_profile_tag()
        _ac._CREDENTIAL_POOL_CACHE.pop((_cp_tag, provider_id), None)
        _ac._CREDENTIAL_POOL_CACHE.pop((_cp_tag, _resolve_provider_alias(provider_id)), None)
    _delete_models_cache_on_disk()
