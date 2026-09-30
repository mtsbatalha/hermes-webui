"""Picker helpers extracted from api/config.py.

Re-exported via ``api.config`` so ``from api.config import _build_nous_featured_set`` keeps
working.  No external module should import from ``api._cfg.picker_helpers`` directly.

Runtime catalog reads are lazy so the module can be imported before ``api.config`` finishes
initialization.
"""

from __future__ import annotations

import copy

def _catalog(name: str):
    try:
        import api.config as _cfg
        return getattr(_cfg, name, {}) or {}
    except Exception:
        return {}  # type: ignore[return-value]

# Soft cap on how many Nous Portal models surface in the picker dropdown.
# Above this count, _build_nous_featured_set() trims the visible list to
# ~_NOUS_FEATURED_TARGET entries; the full catalog is still returned to the
# client under ``extra_models`` so /model autocomplete covers everything.
# Caps reflect human scannability — a 25-row dropdown is the practical UX
# ceiling, and per-vendor sampling at 15 keeps the flagship shape visible
# without one vendor dominating.
_NOUS_FEATURED_THRESHOLD = 25
_NOUS_FEATURED_TARGET = 15
_MODEL_PICKER_OVERFLOW_THRESHOLD = _NOUS_FEATURED_THRESHOLD
_MODEL_PICKER_VISIBLE_TARGET = _NOUS_FEATURED_TARGET
_OPENROUTER_FREE_TIER_AUGMENT_CAP = 30

# Vendor-prefix priority order for featured selection. Lower index = picked
# earlier when sampling the live catalog. Reflects which vendors users have
# historically reached for first via Nous Portal (driven by the curated
# static list maintained in _PROVIDER_MODELS["nous"] and Discord feedback).
_NOUS_VENDOR_PRIORITY = (
    "anthropic", "openai", "google", "moonshotai", "z-ai",
    "minimax", "qwen", "x-ai", "deepseek", "stepfun",
    "xiaomi", "tencent", "nvidia", "arcee-ai",
)


def _build_nous_featured_set(
    live_ids: list[str],
    *,
    selected_model_id: str | None = None,
    target: int = _NOUS_FEATURED_TARGET,
) -> tuple[list[str], list[str]]:
    """Trim a Nous Portal catalog into a (featured, extras) split.

    ``featured`` is what the picker dropdown renders. ``extras`` is everything
    else — kept available so the slash-command `/model` autocomplete and the
    ``_dynamicModelLabels`` map cover the full catalog.

    Selection rules (in order, deterministic):

    1. Always include the user's currently-selected model if it's in the
       catalog (preserves selection stickiness — no orphan IDs in the
       dropdown after a refresh).
    2. Always include every entry from the curated static
       ``_PROVIDER_MODELS["nous"]`` list whose id maps onto a live id —
       those four are explicitly maintained as flagship picks.
    3. Top up to ``target`` by walking ``_NOUS_VENDOR_PRIORITY`` round-robin
       (one model per vendor each pass) so no vendor monopolises the slot
       budget. Within a vendor, the original ``live_ids`` order is preserved
       — that's the order Nous Portal returned, which approximates recency.

    Returns ``(featured_ids, extras_ids)`` — both lists are subsets of
    ``live_ids`` with disjoint membership and union equal to ``live_ids``.

    For catalogs ≤ ``_NOUS_FEATURED_THRESHOLD`` entries the function is a
    no-op: ``featured == live_ids``, ``extras == []``.
    """
    if not live_ids:
        return [], []
    if len(live_ids) <= _NOUS_FEATURED_THRESHOLD:
        return list(live_ids), []

    chosen: list[str] = []  # preserves insertion order
    chosen_set: set[str] = set()

    def _add(mid: str) -> None:
        if mid and mid not in chosen_set:
            chosen.append(mid)
            chosen_set.add(mid)

    # Rule 1: sticky selection. Strip "@nous:" prefix if present so we can
    # match against the live id space (which is bare "vendor/model").
    if selected_model_id:
        sel = selected_model_id
        if sel.startswith("@nous:"):
            sel = sel[len("@nous:"):]
        if sel in live_ids:
            _add(sel)

    # Rule 2: curated flagships. Extract the bare ids from the static list
    # entries (which are stored as "@nous:vendor/model").
    for static in _catalog("_PROVIDER_MODELS").get("nous", []):
        sid = static.get("id", "")
        if sid.startswith("@nous:"):
            sid = sid[len("@nous:"):]
        if sid in live_ids:
            _add(sid)

    # Rule 3: vendor-priority round-robin top-up.
    by_vendor: dict[str, list[str]] = {}
    for mid in live_ids:
        if mid in chosen_set:
            continue
        vendor = mid.split("/", 1)[0] if "/" in mid else ""
        by_vendor.setdefault(vendor, []).append(mid)

    # Walk vendors in priority order, then any leftover vendors alphabetically.
    priority = list(_NOUS_VENDOR_PRIORITY)
    leftover = sorted(v for v in by_vendor if v not in set(priority))
    vendor_order = priority + leftover

    # Round-robin: one model per vendor per pass until we hit the target or
    # exhaust every bucket.
    while len(chosen) < target:
        added_this_pass = 0
        for vendor in vendor_order:
            if len(chosen) >= target:
                break
            bucket = by_vendor.get(vendor)
            if not bucket:
                continue
            _add(bucket.pop(0))
            added_this_pass += 1
        if added_this_pass == 0:
            break  # all buckets empty

    # Anything not chosen becomes extras (full-catalog completion surface).
    extras = [m for m in live_ids if m not in chosen_set]
    return chosen, extras


def _strip_picker_provider_hint(model_id: str) -> str:
    mid = str(model_id or "").strip()
    if mid.startswith("@") and ":" in mid:
        return mid[mid.index(":") + 1 :]
    return mid


def _model_matches_picker_selection(
    model_id: str,
    selected_model_id: str | None,
    provider_id: str | None = None,
) -> bool:
    selected = str(selected_model_id or "").strip()
    candidate = str(model_id or "").strip()
    if not selected or not candidate:
        return False
    if candidate == selected:
        return True

    selected_bare = _strip_picker_provider_hint(selected)
    candidate_bare = _strip_picker_provider_hint(candidate)
    if selected_bare != candidate_bare:
        return False

    selected_provider = ""
    if selected.startswith("@") and ":" in selected:
        selected_provider = selected[1 : selected.index(":")].lower()
    candidate_provider = str(provider_id or "").strip().lower()
    if candidate.startswith("@") and ":" in candidate:
        candidate_provider = candidate[1 : candidate.index(":")].lower()

    return not selected_provider or not candidate_provider or selected_provider == candidate_provider


def _openrouter_model_display_name(model_id: str) -> str:
    """Return the OpenRouter display name (e.g. ``Ox Alpha``) for *model_id*.

    Reads only the local shared metadata disk cache written by hermes-agent
    (``cache/openrouter_model_metadata.json``) — never touches the network.
    Falls back to the raw id when the model is unknown or the cache is
    unavailable, so picker rows are always populated (#7228).
    """
    if not model_id:
        return model_id
    try:
        from agent.model_metadata import _load_model_metadata_disk_cache

        cache = _load_model_metadata_disk_cache() or {}
    except Exception:
        return model_id
    entry = cache.get(model_id)
    if not isinstance(entry, dict):
        return model_id
    name = str(entry.get("name") or "").strip()
    return name or model_id


def _split_picker_overflow_models(
    ordered_models: list[dict],
    *,
    selected_model_id: str | None = None,
    provider_id: str | None = None,
    threshold: int = _MODEL_PICKER_OVERFLOW_THRESHOLD,
    target: int = _MODEL_PICKER_VISIBLE_TARGET,
) -> tuple[list[dict], list[dict]]:
    """Split an ordered picker catalog into visible rows plus an overflow tail."""
    models = [copy.deepcopy(m) for m in (ordered_models or []) if isinstance(m, dict) and m.get("id")]
    if len(models) <= threshold:
        return models, []

    visible = models[:target]
    extras = models[target:]
    if not selected_model_id:
        return visible, extras

    if any(_model_matches_picker_selection(m.get("id", ""), selected_model_id, provider_id) for m in visible):
        return visible, extras

    for idx, model in enumerate(extras):
        if not _model_matches_picker_selection(model.get("id", ""), selected_model_id, provider_id):
            continue
        displaced = visible[-1]
        visible[-1] = model
        extras[idx] = displaced
        break
    return visible, extras


def _apply_provider_prefix(
    raw_models: list[dict],
    provider_id: str,
    active_provider: str | None,
) -> list[dict]:
    """Return *raw_models* with @provider: prefixes applied when needed.

    Prefixing is skipped when (a) the provider is already the active one, or
    (b) a model id already starts with '@' or contains '/' (already routable).
    """
    _active = (active_provider or "").lower()
    if not _active or provider_id == _active:
        return list(raw_models)
    result = []
    for m in raw_models:
        mid = m["id"]
        entry = dict(m)
        if mid.startswith("@") or "/" in mid:
            result.append(entry)
        else:
            entry["id"] = f"@{provider_id}:{mid}"
            result.append(entry)
    return result


def _deduplicate_model_ids(groups: list[dict]) -> None:
    """Ensure every model ID across groups is globally unique.

    When multiple providers expose the same model ID (either bare names like
    ``gpt-5.4`` or slash-qualified IDs like ``google/gemma-4-27b``), the
    dropdown cannot distinguish them. This post-process detects such
    collisions and prefixes colliding entries with ``@provider_id:`` so the
    frontend can treat them as distinct options.

    The first occurrence (in provider-id order) is left unchanged for backward
    compatibility with sessions that already store the original bare/slash
    model name. If that provider is later removed from the config, the next
    cache rebuild re-runs dedup — the remaining provider becomes the sole
    occurrence and is left unchanged, so the session still matches.

    .. note::
       The "first occurrence wins" rule means the unchanged ID is not stable
       across config changes (adding, removing, or reordering providers).
       This is acceptable because the dedup runs on every cache rebuild,
       so sessions always resolve to the current canonical unchanged ID.

    The ``@provider_id:model`` format is consistent with the existing
    ``_apply_provider_prefix()`` function and is handled by
    ``resolve_model_provider()`` (rsplits on the last ``:`` to handle
    provider_ids that themselves contain ``:``).

    Operates in-place on *groups*.
    """
    if not groups:
        return

    # Collect {model_id: [(group_idx, bucket_name, model_idx), ...]} in
    # alphabetical provider_id order so that the "first occurrence stays
    # unchanged" rule is deterministic across config edits
    # (adding/removing/reordering providers). Include ``extra_models`` too:
    # slash-command resolution and picker filtering consume the full catalog.
    sorted_group_indices = sorted(
        range(len(groups)),
        key=lambda i: groups[i].get("provider_id", ""),
    )
    id_map: dict[str, list[tuple[int, str, int]]] = {}
    for gi in sorted_group_indices:
        group = groups[gi]
        for bucket_name in ("models", "extra_models"):
            for mi, model in enumerate(group.get(bucket_name, []) or []):
                mid = str(model.get("id", "") or "").strip()
                # Skip IDs that are already provider-qualified.
                if not mid or mid.startswith("@"):
                    continue
                id_map.setdefault(mid, []).append((gi, bucket_name, mi))

    # For any ID appearing in 2+ groups, prefix all but the first occurrence.
    # This handles N>2 providers correctly: the loop iterates over all
    # occurrences after the first, prefixing each with its own provider_id.
    for original_id, locations in id_map.items():
        if len(locations) < 2:
            continue
        for gi, bucket_name, mi in locations[1:]:
            group = groups[gi]
            model = group[bucket_name][mi]
            pid = group.get("provider_id", "")
            model["id"] = f"@{pid}:{original_id}"
            provider_name = group.get("provider", pid)
            if model.get("label") != original_id:
                model["label"] = f"{model['label']} ({provider_name})"
            else:
                model["label"] = f"{original_id} ({provider_name})"


