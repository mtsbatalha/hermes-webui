"""Credential-pool helpers extracted from api/config.py.

Re-exported via ``api.config`` so ``from api.config import _pool_entry_payloads`` keeps working.
No external module should import from ``api._cfg.credential_pool`` directly.
"""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

# Lazy wrappers (monkeypatch-aware)
def _lazy_get_auth_store_path(*a, **kw):
    try:
        import api.config as _ac
        fn=getattr(_ac, "_get_auth_store_path", None)
        if callable(fn) and fn.__name__ != "_get_auth_store_path":
            return fn(*a, **kw)
    except Exception:
        pass
    try:
        from api._cfg.models_cache import _get_auth_store_path as _real
        return _real(*a, **kw)
    except Exception:
        pass
    try:
        from api.profiles import get_active_hermes_home as _gah
        return _gah() / "auth.json"
    except Exception:
        from api._cfg.state import _DEFAULT_HERMES_HOME
        return _DEFAULT_HERMES_HOME / "auth.json"

def _lazy_resolve_provider_alias(*a, **kw):
    try:
        import api.config as _ac
        fn=getattr(_ac, "_resolve_provider_alias", None)
        if callable(fn): return fn(*a, **kw)
    except Exception:
        pass
    try:
        from api._cfg.provider_helpers import _resolve_provider_alias as _real
        return _real(*a, **kw)
    except Exception:
        return str(a[0]).strip().lower() if a else ""

def _lazy_is_ambient_gh_cli_entry(*a, **kw):
    try:
        import api.config as _ac
        fn=getattr(_ac, "_is_ambient_gh_cli_entry", None)
        if callable(fn): return fn(*a, **kw)
    except Exception:
        pass
    return False

def _credential_pool_cache_ref():
    import api.config as _ac
    return getattr(_ac, "_CREDENTIAL_POOL_CACHE")

def _lazy_thread_ctx():
    try:
        import api.config as _ac
        return getattr(_ac, "_thread_ctx")
    except Exception:
        import threading
        return threading.local()

def _credential_pool_profile_tag() -> str:
    """Active-profile identity for the credential-pool cache key.

    The credential pool is per-Hermes-profile (it lives in that profile's
    auth.json). Keying the process-global cache by provider id ALONE lets a
    pool loaded under profile A satisfy a lookup under profile B in the same
    server process — so a custom provider configured only in A would falsely
    report configured in B (and then 401 at request time). Scoping every
    cache key by the active profile's auth-store path keeps pools from
    crossing profile boundaries.
    """
    try:
        return str(_lazy_get_auth_store_path())
    except Exception:
        return ""


def _pool_entry_payloads(provider_id: str) -> list[dict[str, Any]]:
    """Return explicit credential-pool entry payloads for the active profile.

    Readonly profile scopes must not let ``load_pool()`` seed from process env,
    because that can materialize server-default credentials into a named
    profile's auth store. In that mode, read raw auth.json payloads only.
    """
    _pid = _lazy_resolve_provider_alias(provider_id)
    if bool(getattr(_lazy_thread_ctx(), "block_process_env_fallback", False)):
        try:
            from hermes_cli.auth import read_credential_pool as _read_credential_pool

            raw_entries = _read_credential_pool(_pid)
        except ImportError:
            return []
        payloads: list[dict[str, Any]] = []
        for entry in raw_entries:
            if not isinstance(entry, dict):
                continue
            if _lazy_is_ambient_gh_cli_entry(
                str(entry.get("source", "") or ""),
                str(entry.get("label", "") or ""),
                str(entry.get("key_source", "") or ""),
            ):
                continue
            payloads.append(dict(entry))
        return payloads

    try:
        from agent.credential_pool import load_pool as _load_pool

        _ck = (_credential_pool_profile_tag(), _pid)
        _cached = _credential_pool_cache_ref().get(_ck)
        if _cached is not None:
            _cp_ts, _cp_pool = _cached
            if (time.time() - _cp_ts) < 86400.0:
                _all_entries = _cp_pool.entries() if _cp_pool is not None and hasattr(_cp_pool, "entries") else []
            else:
                _cp_pool = _load_pool(_pid)
                _credential_pool_cache_ref()[_ck] = (time.time(), _cp_pool)
                _all_entries = _cp_pool.entries() if _cp_pool is not None and hasattr(_cp_pool, "entries") else []
        else:
            _cp_pool = _load_pool(_pid)
            _credential_pool_cache_ref()[_ck] = (time.time(), _cp_pool)
            _all_entries = _cp_pool.entries() if _cp_pool is not None and hasattr(_cp_pool, "entries") else []
    except ImportError:
        return []

    payloads = []
    for entry in _all_entries:
        if _lazy_is_ambient_gh_cli_entry(
            str(getattr(entry, "source", "") or ""),
            str(getattr(entry, "label", "") or ""),
            str(getattr(entry, "key_source", "") or ""),
        ):
            continue
        if hasattr(entry, "to_dict") and callable(entry.to_dict):
            payload = entry.to_dict()
        elif isinstance(entry, dict):
            payload = dict(entry)
        else:
            try:
                payload = dict(vars(entry))
            except TypeError:
                payload = {}
        if not isinstance(payload, dict):
            payload = {}
        payload = dict(payload)
        payload.setdefault("source", str(getattr(entry, "source", "") or ""))
        payload.setdefault("label", str(getattr(entry, "label", "") or ""))
        payload.setdefault("key_source", str(getattr(entry, "key_source", "") or ""))
        runtime_api_key = getattr(entry, "runtime_api_key", None)
        if runtime_api_key:
            payload["runtime_api_key"] = runtime_api_key
        base_url = getattr(entry, "base_url", None)
        if base_url:
            payload["base_url"] = base_url
        inference_base_url = getattr(entry, "inference_base_url", None)
        if inference_base_url:
            payload["inference_base_url"] = inference_base_url
        payloads.append(payload)
    return payloads


def _has_explicit_pool_credentials(provider_id: str) -> bool:
    """Return True when the credential pool has at least one non-ambient entry
    for *provider_id* (i.e. not a gh-cli / GITHUB_TOKEN auto-detect).

    Reuses ``_credential_pool_cache_ref()`` so that callers on hot paths (provider
    detection, model listing, live-model fetch) don't pay the ~10s load_pool
    cost more than once per TTL window.
    """
    return bool(_pool_entry_payloads(provider_id))
