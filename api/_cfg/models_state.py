"""Models-cache shared mutable state extracted from api/config.py.

Re-exported via ``api.config`` so ``from api.config import _available_models_cache``
keeps working.  No external module should import from ``api._cfg.models_state``
directly.

All mutable globals live HERE and are re-exported through ``api.config`` via
``_REGISTRY_PROXY`` so ``monkeypatch.setattr(config, "_available_models_cache", ...)``
stays coherent and dict identity is preserved.
"""

from __future__ import annotations

import os
import threading

# ── TTL cache for get_available_models() ─────────────────────────────────────
_available_models_cache: dict | None = None
_available_models_cache_ts: float = 0.0
_available_models_live_rebuild_ts: float = 0.0
_available_models_cache_source_fingerprint: dict | None = None
_AVAILABLE_MODELS_CACHE_TTL: float = 86400.0  # 24 hours
_SESSION_VISIT_MODELS_FRESHNESS_SECONDS: float = 300.0
_available_models_cache_lock = threading.RLock()  # must be RLock: cold path refactoring moved slow work inside this lock, requiring re-entry
_cache_build_cv = threading.Condition(_available_models_cache_lock)  # shares underlying RLock so notify_all() is safe inside with _available_models_cache_lock
_cache_build_in_progress = False  # True while a cold path is actively building

# Memoized (snapshot_ref, {provider_slug: frozenset(model_ids)}) derived from
# the published models-catalog snapshot. Used by _endpoint_advertised_model_ids
# to answer "did this endpoint actually advertise this exact id?" in O(1) per
# send without rebuilding. Keyed on the snapshot object identity so it is
# recomputed exactly once per catalog publish (the cache is replaced wholesale,
# never mutated) and can never serve stale ids from a superseded catalog.
_advertised_model_ids_memo: tuple | None = None

# Atomic provenance pair: an immutable (snapshot, publisher_fingerprint) tuple
# published together at every catalog publish/invalidate site via
# _sync_models_cache_provenance(). The resolver reads THIS single global with one
# lock-free load so it can never observe a torn snapshot/fingerprint pair (the
# two underlying globals are assigned as separate statements). Reading a tuple is
# atomic under the GIL and, crucially, acquires NO lock — so the per-send
# provenance check introduces no lock-ordering edge (avoids the _cfg_lock ↔
# _available_models_cache_lock deadlock) and never waits behind a catalog rebuild.
_models_cache_provenance: tuple | None = None


# Hard wall-clock budget for a COLD live provider-catalog rebuild when it is
# run from a foreground request path. The live rebuild does one network probe
# per detected provider (Copilot token-exchange HTTPS, OpenRouter /v1/models,
# Nous /models, ...). On a flaky / corp / WSL network any single probe can
# stall for its full per-call timeout (Copilot urllib timeout=10s) and, summed
# across N providers, block the request thread for tens of seconds. This bounds
# the time a foreground caller will wait: past the budget it returns a usable
# fallback (last-known disk cache or a network-free minimal catalog) and lets
# the rebuild finish out-of-band and populate the cache for the next call.
# Set HERMES_WEBUI_MODELS_REBUILD_BUDGET=0 to restore the legacy synchronous
# (unbounded) behaviour.
try:
    _LIVE_REBUILD_BUDGET_SECONDS: float = float(
        os.getenv("HERMES_WEBUI_MODELS_REBUILD_BUDGET", "4") or "4"
    )
except (TypeError, ValueError):
    _LIVE_REBUILD_BUDGET_SECONDS = 4.0


# ── Budget-exceeded warning rate-limit ───────────────────────────────────────
# Q-2979-A3 / Copilot discussion_r3305864400: the live-rebuild-budget-exceeded
# warning at _invoke_models_rebuild's slow-path is potentially high-volume —
# every provider catalog refresh that runs past _LIVE_REBUILD_BUDGET_SECONDS
# emits one, so a hung upstream probe (or a sustained burst of cold callers)
# could flood the log at warning level. Rate-limit per reason: the FIRST
# occurrence in a cooldown window logs at warning; subsequent occurrences in
# the same window log at info (so log signal stays useful but volume bounded).
# Override the default cooldown via HERMES_WEBUI_BUDGET_WARN_COOLDOWN (seconds).
try:
    _BUDGET_WARN_COOLDOWN_SECONDS: float = float(
        os.getenv("HERMES_WEBUI_BUDGET_WARN_COOLDOWN", "300") or "300"
    )
except (TypeError, ValueError):
    _BUDGET_WARN_COOLDOWN_SECONDS = 300.0

_BUDGET_WARN_STATE: dict[str, float] = {}
_BUDGET_WARN_LOCK = threading.Lock()


# Cache for credential pool results -- calling load_pool() per-provider per-server
# session is expensive (~10s for zai due to endpoint probing).  The credential pool
# only changes when the user adds/removes credentials, which is rare; a 24h TTL
# is plenty safe and ensures get_available_models() cold paths are fast.
_CREDENTIAL_POOL_CACHE: dict[tuple[str, str], tuple[float, "CredentialPool"]] = {}  # noqa: F821  forward-ref string annotation, resolved at runtime  # (profile_tag, pid) -> (ts, pool)


_provider_models_invalidated_ts: dict[str, float] = {}  # provider_id -> timestamp of last invalidation
