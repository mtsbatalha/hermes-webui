"""Model label + provider seeder extracted from api/config.py.

Re-exported via ``api.config`` so ``from api.config import _get_label_for_model``
keeps working (tests patch it via api.config).  No external module should
import from ``api._cfg.model_labels`` directly.
"""

from __future__ import annotations

import re

def _get_label_for_model(model_id: str, existing_groups: list) -> str:
    """Return a human-friendly label for *model_id*.

    Resolution order:
    1. If the model already appears in *existing_groups* with a label, use it.
    2. Strip @provider: prefix and namespace prefix, then title-case.

    This ensures the injected default model entry in the dropdown always shows
    the same label as the live-fetched or static-catalog version, rather than
    the raw lowercase ID string (#909).
    """
    # Strip @provider: prefix for lookup
    lookup_id = model_id
    if lookup_id.startswith("@") and ":" in lookup_id:
        lookup_id = lookup_id.split(":", 1)[1]

    # Check existing groups for a matching label.
    # Skip slash stripping for URI-scheme IDs (e.g. gpt://folder/model) (#3429).
    _has_scheme = lambda s: "://" in s
    _norm = lambda s: (s.split("/", 1)[-1] if ("/" in s and not _has_scheme(s)) else s).replace("-", ".").lower()
    norm_lookup = _norm(lookup_id)
    for g in existing_groups:
        for m in g.get("models", []):
            if m.get("label") and _norm(str(m.get("id", ""))) == norm_lookup:
                return m["label"]

    # Fall back: strip only the first slash-segment (provider prefix),
    # preserving vendor hierarchy for multi-slash IDs (#3360).
    # Skip for URI-scheme IDs whose slashes are path separators (#3429).
    bare = lookup_id.split("/", 1)[1] if ("/" in lookup_id and not _has_scheme(lookup_id)) else lookup_id
    # Bedrock/Vertex IDs carry a dotted cross-region routing prefix and a vendor
    # namespace -- ``us.anthropic.claude-opus-5``,
    # ``mistral.mistral-large-2407-v1:0`` -- plus sometimes a trailing ``:<n>``
    # provisioned-revision suffix. None of that belongs in a human label, which
    # otherwise reads "Us.anthropic.claude Opus 5" in the turn footer.
    #
    # Only the two documented shapes are stripped, against a CLOSED allow-list.
    # A generic "drop leading letters-only dot segments" loop rewrites any
    # uncatalogued dotted ID: ``deepseek.v3`` renders as "V3" (vendor silently
    # deleted) and ``foo.bar.baz`` as "BAZ".
    #
    # Inlined rather than factored into a module-level helper because the
    # regression harnesses in tests/test_issue3429_* extract this function's
    # source and eval it in isolation; a module-level call would NameError there.
    # Kept in lockstep with ``_stripDottedModelPrefix()`` in static/ui.js --
    # tests/test_dotted_model_label.py drives both from one table.
    if bare and "." in bare and not _has_scheme(bare):
        # ``global`` is a real Bedrock routing head, not just a region code --
        # the catalog at api/config.py:1901-1909 ships six
        # ``global.anthropic.claude-*`` IDs and the routing notes below use that
        # as the canonical Bedrock shape. Omitting it left those labels reading
        # "Global.anthropic.claude Opus 4 7".
        _regions = {"us", "eu", "apac", "global", "us-gov"}
        _vendors = {
            "anthropic", "amazon", "meta", "mistral", "cohere", "ai21",
            "stability", "writer", "deepseek", "qwen", "openai", "google",
            # Bedrock foundation-model vendors added after the first pass. Without
            # these, real IDs rendered with the namespace intact -- "Us.luma.ray 2",
            # "Twelvelabs.marengo Embed 2 7", "Ibm.granite 3 8B Instruct".
            "luma", "twelvelabs", "ibm", "nvidia", "snowflake",
        }
        _segs = bare.split(".")
        _i = 0
        if (len(_segs) - _i >= 3 and _segs[_i].lower() in _regions
                and _segs[_i + 1].lower() in _vendors):
            _i += 1
        if len(_segs) - _i >= 2 and _segs[_i].lower() in _vendors:
            # Dropping the vendor is only safe when what remains still names the
            # model. A bare version remainder (``deepseek.v3``) means the vendor
            # WAS the name.
            _rest = ".".join(_segs[_i + 1:])
            if not re.fullmatch(r"v?\d+(?:[.\-]\d+)*", _rest, re.IGNORECASE):
                _i += 1
        if _i > 0:
            bare = re.sub(r":\d+$", "", ".".join(_segs[_i:]))
    return " ".join(
        w.upper() if (len(w) <= 3 and w.replace(".", "").isalnum() and not w.isdigit()) else w.capitalize()
        for w in bare.replace("_", "-").split("-")
    )


def _seed_provider_models_from_core() -> None:
    """Enrich existing provider model lists with missing IDs from hermes_cli."""
    # Lazy imports avoid circular init (this module is imported by api.config).
    try:
        from api.config import _PROVIDER_MODELS, _resolve_provider_alias
    except Exception:
        return
    try:
        from hermes_cli.models import _PROVIDER_MODELS as _core_pm
    except ImportError:
        return

    # Build a canonical-id → WebUI-key lookup so that providers whose canonical
    # form differs between core and WebUI (e.g. core uses "xai" but WebUI
    # indexes by "x-ai") merge into the existing entry instead of creating a
    # duplicate (#4413).
    _webui_key_by_canonical: dict[str, str] = {}
    for _wk in _PROVIDER_MODELS:
        try:
            _canon = _resolve_provider_alias(_wk)
        except Exception:
            _canon = _wk
        if _canon not in _webui_key_by_canonical:
            _webui_key_by_canonical[_canon] = _wk

    for provider_id, core_models in _core_pm.items():
        if provider_id == "openai-codex":
            continue
        if not isinstance(core_models, list):
            continue

        # Resolve the core's provider_id to the WebUI's key for this provider.
        webui_key = provider_id
        webui_list = _PROVIDER_MODELS.get(provider_id)
        if webui_list is None:
            try:
                _canon_pid = _resolve_provider_alias(provider_id)
            except Exception:
                _canon_pid = provider_id
            webui_key = _webui_key_by_canonical.get(_canon_pid, provider_id)
            webui_list = _PROVIDER_MODELS.get(webui_key)

        if webui_list is None:
            # Provider exists in core but not in the WebUI catalog.
            # Do NOT seed — adding new vendors is a maintainer curation
            # decision, not something the seeder should do implicitly (#4413).
            continue
        if not isinstance(webui_list, list):
            continue
        # Provider exists in both — inject missing model IDs.
        # Detect per-provider ID prefix convention (e.g. nous uses @nous:).
        # The merge must respect each provider's existing ID format rather
        # than injecting the core's raw IDs (#4413).
        _existing_ids_raw: list[str] = [
            (m.get("id") if isinstance(m, dict) else str(m)) or ""
            for m in webui_list
            if isinstance(m, dict) and m.get("id")
        ]
        _prefix = ""
        if _existing_ids_raw and all(i.startswith("@") and ":" in i for i in _existing_ids_raw):
            _prefix = _existing_ids_raw[0].split(":", 1)[0] + ":"

        def _strip_prefix(mid: str, prefix: str = _prefix) -> str:
            if prefix and mid.startswith(prefix):
                return mid[len(prefix):]
            return mid

        existing_ids = {
            _strip_prefix(mid).replace("-", ".").lower()
            for mid in _existing_ids_raw
        }
        for mid in core_models:
            if not isinstance(mid, str) or not mid.strip():
                continue
            normed = mid.strip().replace("-", ".").lower()
            if normed not in existing_ids:
                inject_id = (_prefix + mid.strip()) if _prefix else mid.strip()
                webui_list.append({
                    "id": inject_id,
                    "label": _get_label_for_model(mid.strip(), []),
                })


