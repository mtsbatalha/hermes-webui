"""Models-cache fingerprint + disk-cache helpers extracted from api/config.py.

Re-exported via ``api.config`` so ``from api.config import _models_cache_source_fingerprint``
keeps working.  No external module should import from ``api._cfg.models_cache`` directly.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from pathlib import Path

from api._cfg.state import HOME, STATE_DIR, _DEFAULT_HERMES_HOME
from api._cfg.providers_catalog import _PROVIDER_DISPLAY, _PROVIDER_MODELS

logger = logging.getLogger(__name__)

# Lazy helper for config path (lives in api/config.py, avoid circular import)
def _get_config_path() -> Path:
    try:
        import api.config as _ac
        fn = getattr(_ac, "_get_config_path", None)
        if callable(fn) and fn is not _get_config_path:
            return fn()
    except Exception:
        pass
    # fallback
    try:
        from api.profiles import get_active_hermes_home as _gah2
        return _gah2() / "config.yaml"
    except Exception:
        pass
    return _DEFAULT_HERMES_HOME / "config.yaml"

def _current_webui_version() -> str | None:
    """Lazy resolver for the WebUI version, used to stamp the disk cache (#1633).

    `api.updates` imports `api.config` at module-load time, so we cannot
    `from api.updates import WEBUI_VERSION` at the top of this module without a
    circular import. Instead we resolve lazily on each cache load/save.

    Returns the runtime version string (e.g. ``v0.50.293``) when api.updates
    has been imported, or None if it isn't loaded yet (boot-time corner case
    before the server has finished initializing). A None return is treated as
    "do not stamp / do not validate" by the cache layer so cache reads/writes
    that happen during early init still work — the next call after init will
    stamp normally.
    """
    # Honor monkeypatch on api.config (test isolation).
    try:
        import api.config as _ac_cw
        fn = getattr(_ac_cw, '_current_webui_version', None)
        if callable(fn) and fn is not _current_webui_version:
            try:
                return fn()
            except Exception:
                pass
    except Exception:
        pass
    try:
        # Read attribute via dotted lookup so we don't add an import-time edge.
        import sys as _sys
        mod = _sys.modules.get('api.updates')
        if mod is None:
            return None
        v = getattr(mod, 'WEBUI_VERSION', None)
        return str(v) if v else None
    except Exception:
        return None


# Disk-cache schema version (#1633).
#
# Bumped any time the disk cache shape changes in a backward-incompatible way
# (e.g. new required field, renamed key). Independent of the WebUI version
# stamp — _webui_version forces a rebuild on every release; _schema_version
# guarantees that even if a future release accidentally reuses the same
# WebUI version string (or a debug build doesn't have a version), a structural
# change still invalidates the cache.
_MODELS_CACHE_SCHEMA_VERSION = 3


_models_cache_path = STATE_DIR / "models_cache.json"


def _get_models_cache_path(profile: str | None = None) -> Path:
    """Return the /api/models disk-cache path for the *active* profile (#3957).

    WebUI profile switching is per-client/cookie scoped (issue #798), but the
    models disk cache used to be a single import-time ``STATE_DIR /
    "models_cache.json"`` shared across every profile.  The cache's
    ``_source_fingerprint`` is profile-specific (it hashes the active profile's
    config.yaml + auth.json), so a non-default profile rejected the shared
    snapshot on every read and cold-rebuilt the catalog — the serial live
    provider probes behind that cold build are what pushed ``/api/models`` (and
    the Settings → Providers panel) past the 30s frontend timeout.

    Profile-key the filename so each profile keeps its own warm cache:
      - default / root profile  → ``models_cache.json``  (unchanged path; no
        migration of the existing file)
      - named profile ``<name>`` → ``models_cache.<name>.json``

    The active profile is resolved per-request via ``get_active_profile_name()``
    (thread-local cookie context), falling back to the module-level default
    path if the profiles module is unavailable (very early boot / import cycle).

    The named-profile path is derived from ``_models_cache_path`` (the
    module-level default), not from ``STATE_DIR`` directly, so the path stays
    correct if the default is repointed (e.g. tests monkeypatch
    ``_models_cache_path`` to an isolated tmp file). Pass *profile* to get
    another profile's path (profile delete/create).
    """
    # Honor monkeypatch on api.config._models_cache_path (test isolation).
    try:
        import api.config as _ac_monkey
        maybe = getattr(_ac_monkey, '_models_cache_path', None)
        if isinstance(maybe, Path) and maybe is not _models_cache_path:
            base_override = maybe
            if profile is not None:
                _safe2 = re.sub(r"[^a-z0-9_-]", "_", str(profile).strip().lower())[:64]
                if not _safe2:
                    return base_override
                return base_override.with_name(f"{base_override.stem}.{_safe2}{base_override.suffix}")
            try:
                from api.profiles import get_active_profile_name as _gap, _is_root_profile as _irp
                _name2 = (_gap() or "").strip()
                if not _name2 or _irp(_name2):
                    return base_override
                _safe2b = re.sub(r"[^a-z0-9_-]", "_", _name2.lower())[:64]
                if not _safe2b:
                    return base_override
                return base_override.with_name(f"{base_override.stem}.{_safe2b}{base_override.suffix}")
            except Exception:
                return base_override
    except Exception:
        pass
    try:
        from api.profiles import get_active_profile_name, _is_root_profile

        name = (profile or get_active_profile_name() or "").strip()
        if not name or _is_root_profile(name):
            return _models_cache_path
        # Defensive filename sanitization: the cookie-derived profile name is
        # already validated by _PROFILE_ID_RE at the request boundary, but keep
        # the on-disk filename safe regardless of how the name was resolved.
        safe = re.sub(r"[^a-z0-9_-]", "_", name.lower())[:64]
        if not safe:
            return _models_cache_path
        # Splice the profile into the default filename: models_cache.json →
        # models_cache.<safe>.json, keeping the default's parent dir + suffix.
        base = _models_cache_path
        return base.with_name(f"{base.stem}.{safe}{base.suffix}")
    except Exception:
        return _models_cache_path


def _get_auth_store_path() -> Path:
    """Return the auth.json path for the active Hermes profile."""
    try:
        import api.config as _ac_monkey2
        fn = getattr(_ac_monkey2, '_get_auth_store_path', None)
        if callable(fn) and fn is not _get_auth_store_path:
            try:
                return fn()
            except Exception:
                pass
    except Exception:
        pass
    try:
        from api.profiles import get_active_hermes_home as _gah

        return _gah() / "auth.json"
    except ImportError:
        return _DEFAULT_HERMES_HOME / "auth.json"


def _models_cache_file_fingerprint(path: Path) -> dict:
    """Return non-secret identity metadata for a cache dependency file.

    The /api/models response depends on config.yaml (model/provider defaults)
    and auth.json (active_provider + credential_pool).  The cache only needs
    cheap invalidation signals here, not file contents; never include secrets.
    """
    fingerprint = {"path": str(Path(path).expanduser())}
    try:
        st = Path(path).stat()
    except OSError:
        fingerprint["missing"] = True
        return fingerprint
    fingerprint["mtime_ns"] = st.st_mtime_ns
    fingerprint["size"] = st.st_size
    return fingerprint


# Codex's ~/.codex/models_cache.json is rewritten on Codex's own refresh timer.
# Each rewrite bumps mtime_ns + size, but the payload usually only refreshes
# the volatile timestamp fields (`fetched_at`, plus `updated_at` when present)
# while the model catalog (client_version, etag, models[]) stays identical.
# Fingerprinting that file by stat (#2443's _models_cache_file_fingerprint)
# therefore invalidated the 24h /api/models cache on every Codex refresh, and
# the next session visit paid a full live rebuild whose serial provider probes
# starved the session-open path (#7540).
#
# This is a DENY-list, not an allow-list, on purpose — same safety direction as
# _AUTH_FINGERPRINT_VOLATILE_KEYS: every other field (client_version, etag,
# models, and any future model-affecting key) stays IN the fingerprint, so a
# genuine catalog change still invalidates the cache.
_CODEX_CACHE_FINGERPRINT_VOLATILE_KEYS = frozenset({
    # Whole-file refresh timestamp, rewritten by every Codex models refresh.
    "fetched_at",
    # Same-family save timestamp (mirrors the auth.json deny-list).
    "updated_at",
})


def _strip_volatile_codex_cache_fields(obj):
    """Recursively drop refresh-timestamp-only keys from a Codex cache tree.

    Pure structural transform; never mutates the input. Any key NOT in the
    deny-list is preserved verbatim so real catalog changes still show through
    in the fingerprint.
    """
    if isinstance(obj, dict):
        return {
            k: _strip_volatile_codex_cache_fields(v)
            for k, v in obj.items()
            if k not in _CODEX_CACHE_FINGERPRINT_VOLATILE_KEYS
        }
    if isinstance(obj, list):
        return [_strip_volatile_codex_cache_fields(v) for v in obj]
    return obj


def _codex_models_cache_fingerprint(path: Path) -> dict:
    """Return a content fingerprint of Codex's models_cache.json.

    Unlike _models_cache_file_fingerprint() (mtime_ns + size), this hashes the
    JSON content with the refresh-timestamp fields stripped, so a Codex
    refresh that only bumps `fetched_at` does NOT invalidate the 24h
    /api/models cache and therefore does not force the live rebuild that
    stalled session opens (#7540). A change to anything that actually feeds the
    Codex models we surface (client_version, etag, models[], an unknown future
    field) still changes the hash and correctly busts the cache.

    Failure modes are deliberately conservative — a missing file is recorded,
    and an unreadable/undecodable file falls back to the stat-based fingerprint
    so behaviour is never *less* safe than the stat-only version.
    """
    # Honor monkeypatch on api.config (test isolation).
    try:
        import api.config as _ac_cc
        fn = getattr(_ac_cc, '_codex_models_cache_fingerprint', None)
        if callable(fn) and fn is not _codex_models_cache_fingerprint:
            try:
                return fn(path)
            except Exception:
                pass
    except Exception:
        pass
    p = Path(path).expanduser()
    fp: dict = {"path": str(p)}
    try:
        st = p.stat()
    except OSError:
        fp["missing"] = True
        return fp
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        # Unreadable / corrupt / mid-write: keep the stat-based fingerprint.
        # Strictly no less safe than the pre-fix behaviour (every write still
        # invalidates) for this rare path only.
        fp["mtime_ns"] = st.st_mtime_ns
        fp["size"] = st.st_size
        fp["semantic"] = "unparsed-fallback"
        return fp
    try:
        # The recursive strip can raise (e.g. RecursionError on a pathologically
        # deep JSON tree) — keep it inside the fallback try so any transform
        # failure degrades to the stat fingerprint rather than 500ing /api/models.
        # Honor monkeypatch on api.config._strip_volatile_codex_cache_fields (test isolation).
        # If the patched function raises, let it propagate to the outer encode-fallback handler
        # rather than recovering via the local implementation (test_deeply_nested expects fallback).
        try:
            import api.config as _ac_strip
            _strip_fn = getattr(_ac_strip, '_strip_volatile_codex_cache_fields', None)
            if callable(_strip_fn) and _strip_fn is not _strip_volatile_codex_cache_fields:
                stripped = _strip_fn(raw)
            else:
                stripped = _strip_volatile_codex_cache_fields(raw)
        except Exception:
            raise
        encoded = json.dumps(
            stripped,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            default=str,
        ).encode("utf-8")
        fp["semantic_sha256"] = hashlib.sha256(encoded).hexdigest()
    except Exception:
        fp["mtime_ns"] = st.st_mtime_ns
        fp["size"] = st.st_size
        fp["semantic"] = "encode-fallback"
    return fp


def _models_cache_catalog_fingerprint() -> dict:
    """Return non-secret model-catalog identity metadata for cache invalidation.

    The /api/models payload is not only a function of user config/auth files.
    It also depends on the provider/model catalog baked into this module and on
    small local catalogs such as Codex's models_cache.json. Keep this cheap and
    deterministic so a server restart after catalog changes does not keep
    serving an otherwise-valid persisted models_cache.json until the 24h TTL
    expires (#2443).

    The Codex axis uses a *content* fingerprint that excludes the refresh
    timestamp fields (see _codex_models_cache_fingerprint): Codex rewrites
    ~/.codex/models_cache.json on its own timer, bumping mtime_ns + size while
    the model payload stays identical, so a stat-based fingerprint invalidated
    the 24h cache on every Codex refresh and the next session visit paid a live
    rebuild (#7540).
    """
    catalog_payload = {
        "provider_models": _PROVIDER_MODELS,
        "provider_display": _PROVIDER_DISPLAY,
    }
    try:
        encoded = json.dumps(
            catalog_payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            default=str,
        ).encode("utf-8")
        provider_catalog_sha = hashlib.sha256(encoded).hexdigest()
    except Exception:
        provider_catalog_sha = "unavailable"

    codex_home = Path(os.getenv("CODEX_HOME", "").strip() or (HOME / ".codex")).expanduser()
    return {
        "provider_catalog_sha256": provider_catalog_sha,
        "codex_models_cache": _codex_models_cache_fingerprint(codex_home / "models_cache.json"),
    }


# Credential-rotation fields inside auth.json that churn on a ~14-minute
# period (credential-pool / OAuth token refresh rewrites the whole file) but
# DO NOT change the set of available providers or models that /api/models
# returns. mtime/size-based fingerprinting (#1699's _models_cache_file_
# fingerprint) treats every one of these rewrites as a cache-invalidating
# change, so the 24h models cache is effectively dead — every few minutes a
# tab pays a full cold get_available_models() rebuild (see RCA t_d127953d /
# t_16551f61). We strip ONLY these known-inert fields and fingerprint the
# rest of auth.json by content, so token rotation no longer busts the cache.
#
# This is a DENY-list, not an allow-list, on purpose: a field we don't know
# about stays IN the fingerprint, so any genuine change to provider
# enablement / endpoint / api-base / model-allow (active_provider, a NEW
# credential_pool entry id, base_url, source, label, key_source, auth_type,
# priority, the providers{} block, …) still correctly invalidates the cache.
# The safety invariant is one-directional: excluding these fields can only
# ever make the fingerprint MORE stable, never make it miss a real
# provider/model-set change — because none of these fields feed
# detected_providers / the catalog in _build_available_models_uncached().
_AUTH_FINGERPRINT_VOLATILE_KEYS = frozenset({
    # Secret material — rotates on refresh, never gates the provider/model set.
    "access_token",
    "refresh_token",
    "id_token",
    "api_key",
    "secret",
    "client_secret",  # rotation-only on purpose; not a model-cache differentiator
    # Expiry / liveness — bumped every refresh, derived from the token above.
    "expires_at",
    "expires_at_ms",
    "expires_in",
    # Per-credential status/telemetry — churns on every request, not config.
    "last_status",
    "last_status_at",
    "last_error_code",
    "last_error_reason",
    "last_error_message",
    "last_error_reset_at",
    "request_count",
    # Whole-file save timestamp — rewritten on every _save_auth_store().
    "updated_at",
})


def _strip_volatile_auth_fields(obj):
    """Recursively drop credential-rotation-only keys from an auth.json tree.

    Pure structural transform; never mutates the input. Any key NOT in the
    deny-list is preserved verbatim so real provider/endpoint changes still
    show through in the fingerprint.
    """
    if isinstance(obj, dict):
        return {
            k: _strip_volatile_auth_fields(v)
            for k, v in obj.items()
            if k not in _AUTH_FINGERPRINT_VOLATILE_KEYS
        }
    if isinstance(obj, list):
        return [_strip_volatile_auth_fields(v) for v in obj]
    return obj


def _auth_store_semantic_fingerprint(path: Path) -> dict:
    """Return a content fingerprint of auth.json that ignores token churn.

    Unlike _models_cache_file_fingerprint() (mtime_ns + size), this hashes
    the JSON content with the credential-rotation fields stripped, so the
    ~14-min token-refresh rewrite of auth.json does NOT invalidate the 24h
    /api/models cache. A change to anything that actually affects the
    provider/model set (active_provider, a new credential_pool entry, a
    changed base_url/source/label/auth_type, the providers{} block, …)
    still changes the hash and correctly busts the cache.

    Failure modes are deliberately conservative — if the file is missing we
    record that, and if it can't be read/parsed we fall back to the old
    mtime/size fingerprint so behaviour is never *less* safe than #1699.
    """
    p = Path(path).expanduser()
    fp: dict = {"path": str(p)}
    try:
        st = p.stat()
    except OSError:
        fp["missing"] = True
        return fp
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        # Unreadable / corrupt / mid-write: fall back to the stat-based
        # fingerprint. Strictly no less safe than the pre-fix behaviour
        # (every write still invalidates) for this rare path only.
        fp["mtime_ns"] = st.st_mtime_ns
        fp["size"] = st.st_size
        fp["semantic"] = "unparsed-fallback"
        return fp
    stripped = _strip_volatile_auth_fields(raw)
    try:
        encoded = json.dumps(
            stripped,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            default=str,
        ).encode("utf-8")
        fp["semantic_sha256"] = hashlib.sha256(encoded).hexdigest()
    except Exception:
        fp["mtime_ns"] = st.st_mtime_ns
        fp["size"] = st.st_size
        fp["semantic"] = "encode-fallback"
    return fp


def _active_profile_home() -> Path:
    try:
        from api.profiles import get_active_hermes_home as _gah

        return _gah()
    except ImportError:
        return _DEFAULT_HERMES_HOME


def _models_cache_env_fingerprint(path: Path) -> list:
    """``[key, HMAC(value)]`` per non-empty ``.env`` entry, parsed like provider detection.

    Values are keyed-hashed with the WebUI signing key so the cache file never holds a secret.
    """
    import hmac
    from api.auth import _signing_key
    from api.providers import _load_env_file

    key = _signing_key()
    return [
        [k, hmac.new(key, v.encode("utf-8"), hashlib.sha256).hexdigest()]
        for k, v in sorted(_load_env_file(Path(path).expanduser()).items())
        if v
    ]


def _declares_model_provider_kind(plugin_dir: Path) -> bool:
    # Same parse as the agent's providers._declares_model_provider_kind: PyYAML, then a line scan.
    for filename in ("plugin.yaml", "plugin.yml"):
        manifest = plugin_dir / filename
        if not manifest.is_file():
            continue
        try:
            text = manifest.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return False
        try:
            from api import yaml_compat as _yaml

            data = _yaml.safe_load(text)
            if isinstance(data, dict):
                return str(data.get("kind", "")).strip() == "model-provider"
        except Exception:
            pass
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or ":" not in stripped:
                continue
            key, _, value = stripped.partition(":")
            if key.strip() == "kind":
                return value.strip().strip("\"'") == "model-provider"
        return False
    return False


def _models_cache_plugin_fingerprint(home: Path) -> list:
    """``[dir, file stamps]`` per model-provider plugin, discovered like providers._scan_home_layer."""
    found = []
    plugins_root = Path(home).expanduser() / "plugins"
    for base, flat in ((plugins_root / "model-providers", False), (plugins_root, True)):
        try:
            children = sorted(base.iterdir())
        except OSError:
            continue
        for child in children:
            if not child.is_dir() or child.name.startswith(("_", ".")):
                continue
            if flat and (child.name == "model-providers" or not _declares_model_provider_kind(child)):
                continue
            found.append([str(child.relative_to(plugins_root)), _plugin_tree_stamps(child)])
    return found


def _plugin_tree_stamps(plugin_dir: Path) -> list:
    # The loader execs __init__.py, which may import siblings or read data files; skip bytecode.
    stamps = []
    for root, dirs, files in os.walk(plugin_dir):
        dirs[:] = sorted(d for d in dirs if d != "__pycache__" and not d.startswith("."))
        for name in sorted(files):
            if name.endswith((".pyc", ".pyo")):
                continue
            path = os.path.join(root, name)
            try:
                st = os.stat(path)
            except OSError:
                continue
            stamps.append([os.path.relpath(path, plugin_dir), st.st_mtime_ns, st.st_size])
    return stamps


def _models_cache_source_fingerprint() -> dict:
    """Return the current config/auth/catalog fingerprint for /api/models cache.

    The auth.json axis uses a *content* fingerprint that excludes pure
    credential-rotation fields (see _auth_store_semantic_fingerprint): the
    auth store is rewritten roughly every 14 minutes by token refresh, and
    a stat-based (mtime/size) fingerprint made the 24h cache churn on every
    one of those rewrites (RCA t_16551f61). config.yaml keeps the cheap
    mtime/size fingerprint because it is only rewritten on deliberate user
    edits (which can change anything) and does not churn on a timer.
    """
    # Honor monkeypatch on api.config (test isolation).
    try:
        import api.config as _ac_src
        fn = getattr(_ac_src, '_models_cache_source_fingerprint', None)
        if callable(fn) and fn is not _models_cache_source_fingerprint:
            try:
                return fn()
            except Exception:
                pass
    except Exception:
        pass
    home = _active_profile_home()
    return {
        "config_yaml": _models_cache_file_fingerprint(_get_config_path()),
        "auth_json": _auth_store_semantic_fingerprint(_get_auth_store_path()),
        "env": _models_cache_env_fingerprint(home / ".env"),
        "plugins": _models_cache_plugin_fingerprint(home),
        "catalog": _models_cache_catalog_fingerprint(),
    }


def _delete_models_cache_on_disk() -> None:
    try:
        os.unlink(str(_get_models_cache_path()))
    except OSError:
        pass  # already absent


def _is_valid_models_cache(cache: object) -> bool:
    """Return True when a cache payload has the full /api/models shape.

    SHAPE-only check: validates structural correctness of an in-memory or
    on-disk cache. Use _is_loadable_disk_cache() for the strictness needed
    when reading from disk (it adds version-stamp invalidation per #1633).

    Kept loose so in-memory cache writes (which never touch disk and so don't
    need version stamping) can use this validator unchanged.
    """
    if not isinstance(cache, dict):
        return False
    if not {"active_provider", "default_model", "configured_model_badges", "groups"}.issubset(cache):
        return False
    active_provider = cache.get("active_provider")
    return (
        (active_provider is None or isinstance(active_provider, str))
        and isinstance(cache.get("default_model"), str)
        and isinstance(cache.get("configured_model_badges"), dict)
        and isinstance(cache.get("groups"), list)
    )


def _is_loadable_disk_cache(cache: object) -> bool:
    """Return True when an on-disk cache is safe to use after a process boot.

    Adds two checks on top of _is_valid_models_cache (#1633):
      1. ``_schema_version`` matches `_MODELS_CACHE_SCHEMA_VERSION`. A bumped
         schema version unconditionally invalidates older cache files.
      2. ``_webui_version`` matches the current runtime version. Forces a
         rebuild after every release so users see picker-shape fixes
         immediately, instead of waiting up to 24 hours for the TTL to expire.
         If the runtime version cannot be resolved (early-init edge case),
         skip this check rather than wedge the boot.

    Note: ``_webui_version`` is a string equality check, not a semver compare —
    two debug builds with the same `WEBUI_VERSION` string but different actual
    code wouldn't invalidate via this axis. ``_schema_version`` is the
    independent invalidation axis for breaking changes that lack a tag bump;
    bump it whenever the cache shape changes incompatibly.
    """
    # Honor monkeypatch on api.config (test isolation).
    try:
        import api.config as _ac_il2
        fn2 = getattr(_ac_il2, '_is_loadable_disk_cache', None)
        if callable(fn2) and fn2 is not _is_loadable_disk_cache:
            return bool(fn2(cache))
    except Exception:
        pass
    if not _is_valid_models_cache(cache):
        return False
    if not isinstance(cache, dict):  # appease type-narrowing — already guarded above
        return False
    cached_schema = cache.get("_schema_version")
    if cached_schema != _MODELS_CACHE_SCHEMA_VERSION:
        # DEBUG telemetry per stage-294 absorption: makes "why did my cache
        # rebuild" investigations one log-grep away.
        logger.debug(
            "models cache rejected: schema=%r vs runtime=%r",
            cached_schema, _MODELS_CACHE_SCHEMA_VERSION,
        )
        return False
    runtime_version = _current_webui_version()
    if runtime_version is not None:
        cached_version = cache.get("_webui_version")
        if not isinstance(cached_version, str) or cached_version != runtime_version:
            logger.debug(
                "models cache rejected: webui_version=%r vs runtime=%r",
                cached_version, runtime_version,
            )
            return False
    cached_sources = cache.get("_source_fingerprint")
    runtime_sources = _models_cache_source_fingerprint()
    if cached_sources != runtime_sources:
        logger.debug(
            "models cache rejected: source_fingerprint=%r vs runtime=%r",
            cached_sources,
            runtime_sources,
        )
        return False
    return True


