"""Reasoning-effort helpers extracted from api/config.py.

Re-exported via ``api.config`` so ``from api.config import resolve_model_reasoning_efforts``
keeps working.  No external module should import from ``api._cfg.reasoning`` directly.

Helpers that would otherwise create circular imports are resolved lazily through
``api.config`` or ``api._cfg.provider_helpers`` at call time.
"""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)


# Lazy helpers for cross-module state.

def _cfg() -> dict:
    try:
        import api.config as _ac
        fn = getattr(_ac, "get_config", None)
        if callable(fn):
            return fn() or {}
    except Exception:
        pass
    return {}


def _resolve_alias(provider_id: str) -> str:
    try:
        import api.config as _ac
        fn = getattr(_ac, "_resolve_provider_alias", None)
        if callable(fn):
            return str(fn(provider_id) or "").strip().lower()
    except Exception:
        pass
    try:
        import api._cfg.provider_helpers as _ph
        fn = getattr(_ph, "_resolve_provider_alias", None)
        if callable(fn):
            return str(fn(provider_id) or "").strip().lower()
    except Exception:
        pass
    return str(provider_id or "").strip().lower()


def _custom_entries(cfg_dict=None):
    try:
        import api.config as _ac
        fn = getattr(_ac, "_custom_provider_entries", None)
        if callable(fn):
            if cfg_dict is not None:
                try:
                    return fn(cfg_dict)
                except TypeError:
                    pass
            return fn()
    except Exception:
        pass
    try:
        import api._cfg.provider_helpers as _ph
        fn = getattr(_ph, "_custom_provider_entries", None)
        if callable(fn):
            if cfg_dict is not None:
                try:
                    return fn(cfg_dict)
                except TypeError:
                    pass
            return fn()
    except Exception:
        pass
    return []


def _slug_from_name(name: object) -> str:
    try:
        import api.config as _ac
        fn = getattr(_ac, "_custom_provider_slug_from_name", None)
        if callable(fn):
            return str(fn(name) or "")
    except Exception:
        pass
    try:
        import api._cfg.provider_helpers as _ph
        fn = getattr(_ph, "_custom_provider_slug_from_name", None)
        if callable(fn):
            return str(fn(name) or "")
    except Exception:
        pass
    return ""


def _provider_base_url(provider_id: str) -> str | None:
    try:
        import api.config as _ac
        fn = getattr(_ac, "_get_provider_base_url", None)
        if callable(fn):
            return fn(provider_id)
    except Exception:
        pass
    try:
        import api._cfg.routing as _routing
        fn = getattr(_routing, "_get_provider_base_url", None)
        if callable(fn):
            return fn(provider_id)
    except Exception:
        pass
    return None


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


def _resolve_model_provider(model_id: str):
    try:
        import api.config as _ac
        fn = getattr(_ac, "resolve_model_provider", None)
        if callable(fn):
            return fn(model_id)
    except Exception:
        pass
    return model_id, "", None

# ── Reasoning config (CLI parity for /reasoning) ─────────────────────────────
# Mirrors hermes_constants.parse_reasoning_effort so WebUI can validate without
# importing from the agent tree (which may not be installed).  Any drift here
# will show up in the shared test suite since both sides accept the same set.
# Keep this WebUI-visible set aligned with hermes-agent#29248.
VALID_REASONING_EFFORTS = ("minimal", "low", "medium", "high", "xhigh", "max")


def parse_reasoning_effort(effort):
    """Parse an effort level into the dict the agent expects.

    Returns None when *effort* is empty or unrecognised (caller interprets as
    "use default"), ``{"enabled": False}`` for ``"none"``, and
    ``{"enabled": True, "effort": <level>}`` for any of
    ``VALID_REASONING_EFFORTS``.
    """
    if not effort or not str(effort).strip():
        return None
    eff = str(effort).strip().lower()
    if eff == "none":
        return {"enabled": False}
    if eff in VALID_REASONING_EFFORTS:
        return {"enabled": True, "effort": eff}
    return None


def _strip_provider_hint_for_reasoning(model_id: str, provider: str | None = None) -> str:
    """Remove WebUI routing hints before provider-specific capability lookup.

    A plain ``@provider:model`` hint strips cleanly on the first colon. But a
    *named* custom provider hint is ``@custom:<slug>:model`` — two colons —
    and the naive first-colon split only strips the leading ``@custom:``,
    leaving ``<slug>:model`` behind. That leftover slug fragment can hide a
    nested gateway route from prefix-based checks like
    ``_nested_route_reasoning_denied()`` (e.g. ``agg:vertex/gemini-image-1.0``
    no longer starts with ``vertex/gemini-``), silently re-enabling reasoning
    controls on routes that must never expose them.

    When the resolved *provider* is known (e.g. ``"custom:agg"``), strip the
    exact ``@{provider}:`` prefix first so both segments are removed in one
    pass. Falls back to the generic first-colon split when no provider is
    given or it doesn't match — preserving prior behavior for plain
    ``@provider:model`` hints.
    """
    model = str(model_id or "").strip()
    if not model.startswith("@"):
        return model
    if provider:
        exact_prefix = f"@{provider}:".lower()
        if model.lower().startswith(exact_prefix):
            return model[len(exact_prefix) :]
    if ":" in model:
        return model.split(":", 1)[1]
    return model


def _reasoning_name_candidates(model_id: str) -> list[str]:
    """Return normalized model-name candidates for heuristic capability checks."""
    bare = str(model_id or "").strip().lower().rsplit("/", 1)[-1]
    if not bare:
        return []

    candidates: list[str] = []

    def _add(value: str) -> None:
        candidate = str(value or "").strip().lower()
        if candidate and candidate not in candidates:
            candidates.append(candidate)

    _add(bare)

    dot_parts = [part for part in bare.split(".") if part]
    if len(dot_parts) > 1:
        # Try progressively stripping dot-separated vendor namespaces so inputs like
        # "moonshotai.kimi-k2.5" and "vendor.deepseek.v3.2" both surface the real
        # model family rather than treating every dot as part of the provider slug.
        for index in range(1, len(dot_parts)):
            suffix = ".".join(dot_parts[index:])
            if any(ch.isalpha() for ch in suffix):
                _add(suffix)

    for candidate in list(candidates):
        normalized = re.sub(r"[^a-z0-9]+", "-", candidate).strip("-")
        _add(normalized)

    return candidates


def _candidate_supports_reasoning(candidate: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "-", str(candidate or "").strip().lower()).strip("-")
    if not normalized:
        return False

    tokens = [token for token in normalized.split("-") if token]
    token_set = set(tokens)

    if "thinking" in token_set or "reasoning" in token_set:
        return True
    if "gpt" in token_set or normalized.startswith("gpt"):
        # Restrict to GPT-5+ (exclude GPT-4o/4.1/3.5 — reasoning_effort unsupported)
        m = re.search(r"gpt-(\d+)", normalized)
        if m and int(m.group(1)) >= 5:
            return True
        return False
    if normalized in {"o1", "o3", "o4"} or normalized.startswith(("o1-", "o3-", "o4-")):
        return True
    if "claude" in token_set or normalized.startswith("claude"):
        # Restrict to Claude 4+ or Claude 3.7+ (exclude Claude 3.0/3.5).
        # The minor group is capped at 1-2 digits with a (?!\d) guard so a
        # trailing date stamp is NOT captured as a minor version — otherwise a
        # bare, date-stamped Claude 3.0 id ("claude-3-opus-20240229") would read
        # minor=20240229 and wrongly satisfy the 3.7+ gate. (Same date-stamp
        # defense _is_pre_adaptive_anthropic already uses.)
        match = re.search(r"claude.*?(\d+)(?:\D+(\d{1,2})(?!\d))?", normalized)
        if match:
            major = int(match.group(1))
            minor = int(match.group(2)) if match.group(2) else 0
            if major >= 4 or (major == 3 and minor >= 7):
                return True
        return False
    # Positive-only prefixed Qwen 3+ detection (e.g. "al-qwen3-8-max-preview"
    # → tokens ["al","qwen3","8",...]). Scan for any token starting with
    # "qwen" followed by version >= 3 and immediately allow. Do NOT return
    # False here — Qwen 2.x embedded in hybrid IDs like
    # "deepseek-r1-distill-qwen2.5-bakeneko-32b" must fall through to the
    # DeepSeek detector below.
    for token in tokens:
        m = re.match(r"qwen(\d+)", token)
        if m and int(m.group(1)) >= 3:
            return True
    # Terminal guard for standalone/bare Qwen IDs (original behavior):
    # "qwen" as a standalone token or normalized starting with "qwen" means
    # this IS a Qwen model — apply the 3+ gate and block 2.x.
    if "qwen" in token_set or normalized.startswith("qwen"):
        match = re.search(r"qwen.*?(\d+)(?:\D+(\d+))?", normalized)
        if match:
            major = int(match.group(1))
            if major >= 3:
                return True
        return False
    if "kimi" in token_set or normalized.startswith("kimi"):
        return True
    if "minimax" in token_set or normalized.startswith("minimax"):
        return True
    if "mimo" in token_set or normalized.startswith("mimo"):
        return True
    if "glm" in token_set or normalized.startswith("glm"):
        return True
    if "step" in token_set or normalized.startswith("step"):
        return True
    if "deepseek" in token_set:
        # Check the token immediately after "deepseek" for a V-series or R-series
        # version marker (e.g. "v4", "r1"). This is position-independent so it
        # handles bare "deepseek-v4-flash" and @custom:name:DeepSeek-V4-Flash
        # (→ "my-provider-deepseek-v4-flash") equally, while correctly excluding
        # non-reasoning models like deepseek-chat and deepseek-coder.
        # Using tokens.index() ensures the provider slug (e.g. "vertex" in
        # "vertex-deepseek-chat") cannot falsely trigger the version guard.
        idx = tokens.index("deepseek")
        if idx + 1 < len(tokens) and tokens[idx + 1].startswith(("v", "r")):
            return True
    return False


# Matches the nested Gemini gateway route prefix anywhere it appears in a
# model id, as long as it isn't embedded inside a larger alphanumeric token
# (the negative lookbehind excludes false positives like "notvertex/gemini-x").
# Scanning for the pattern at any position — rather than requiring the whole
# string to start with it — makes the check independent of how many wrapper
# layers precede the route: ``@provider:``, a named custom-provider slug
# (``@custom:<slug>:``), or any future nesting scheme none of us have
# invented yet. This invariant (Gemini image/embedding routes must never
# expose a reasoning toggle) was bypassed twice via different edge cases in
# the prefix-stripping logic before being made structurally boundary-based
# instead of prefix-based — see PR #5313 review history.
_NESTED_ROUTE_PATTERN = re.compile(r"(?<![a-z0-9])(vertex/gemini-|gemini_cli/gemini-)(.*)$")


def _nested_route_reasoning_denied(model: str) -> bool:
    """Hard deny for nested Gemini gateway routes that must never show a reasoning toggle.

    Matches the route pattern anywhere in *model*, not just when the whole
    string starts with it, so this check does not depend on a caller having
    stripped exactly the right wrapper prefix first. Callers should still
    pass the least-wrapped form they have (e.g. after
    ``_strip_provider_hint_for_reasoning``) for clarity, but correctness no
    longer hinges on it.
    """
    lower = str(model or "").strip().lower()
    if not lower:
        return False
    match = _NESTED_ROUTE_PATTERN.search(lower)
    if not match:
        return False
    tail = match.group(2)
    return tail.startswith("embedding") or "image" in tail or "imagine" in tail


def _nested_gateway_route_reasoning(model: str) -> bool:
    """Recognize nested ``vertex/gemini-`` and ``gemini_cli/gemini-`` routes on custom providers.

    The slash-prefix heuristic list includes ``google/gemini-2`` but not gateway-prefixed
    Gemini ids, so capable models behind custom aggregators stayed hidden.
    """
    lower = str(model or "").strip().lower()
    if not lower:
        return False
    for prefix in ("vertex/gemini-", "gemini_cli/gemini-"):
        if lower.startswith(prefix):
            tail = lower[len(prefix) :]
            if tail.startswith("embedding") or "image" in tail or "imagine" in tail:
                return False
            # Gemini thinking/reasoning controls are documented for the 2.5
            # series and 3-era models only — 1.5 (and earlier) have no thinking
            # support, so a reasoning selector on e.g. ``vertex/gemini-1.5-pro``
            # would let a user pick an effort that the route then rejects.
            # Version-gate the allow to the reasoning-capable families.
            if (
                tail == "2.5"
                or tail.startswith(("2.5-", "2.5.", "3-", "3."))
                or "thinking" in tail
                or "reasoning" in tail
            ):
                return True
            return False
    return False


def _zai_glm_classification(model_id: str, provider_id: str) -> str | None:
    """Classify a model on the native ``zai`` endpoint into a Z.AI capability tier.

    Returns one of:

    * ``"effort"``  — accepts the ``reasoning_effort`` intensity ladder
      (GLM-5.2+; Z.AI's max/xhigh/high/medium/low/minimal values match
      ``VALID_REASONING_EFFORTS`` exactly).
    * ``"thinking"`` — does NOT accept the effort ladder but DOES accept the
      ``thinking: {"type": "enabled"|"disabled"}`` on/off toggle (GLM-4.5,
      4.5-air/flash, 4.6, 5, 5.1, 5-turbo, and other 4.5+ non-4.7 GLM models).
    * ``"forced"``  — GLM-4.7 family: forced thinking, neither the toggle nor
      the ladder is configurable.
    * ``None``      — not a native-``zai`` GLM model (non-GLM id, non-zai
      provider, or an aggregator/custom provider that routes through its own
      router rather than Z.AI's per-model docs).

    Scoped to the native ``zai`` endpoint (aliases ``glm``/``z-ai``/``z.ai``/
    ``zhipu`` all resolve to ``zai`` via ``_resolve_provider_alias``). Per
    docs.z.ai: ``thinking`` is supported by GLM-4.5+ (4.7 forces it on),
    ``reasoning_effort`` is GLM-5.2+ exclusive.

    Shared by ``_filter_reasoning_efforts_for_provider`` (UI dropdown options),
    ``coerce_reasoning_effort_for_model`` (what is actually sent to Z.AI), and
    ``get_reasoning_status`` (whether the composer renders an On/None toggle when
    the effort ladder is empty) so all three surfaces agree.
    """
    provider = _resolve_alias(str(provider_id or "").strip().lower())
    if provider != "zai":
        return None
    bare = _strip_provider_hint_for_reasoning(str(model_id or "")).lower().rsplit("/", 1)[-1]
    if "glm" not in bare:
        return None
    # GLM-4.7 family: forced thinking — reasoning is not configurable at all.
    if bare.startswith("glm-4.7"):
        return "forced"
    m = re.search(r"glm-(\d+)(?:\D+(\d+))?", bare)
    if m:
        major = int(m.group(1))
        minor = int(m.group(2)) if m.group(2) else 0
        # GLM-5.2+ accepts the effort ladder.
        if (major, minor) >= (5, 2):
            return "effort"
        # GLM-4.5+ (but below 5.2) accepts the thinking toggle only.
        if (major, minor) >= (4, 5):
            return "thinking"
    # Pre-4.5 GLM (e.g. glm-4, glm-3): no thinking support documented by Z.AI.
    return None


def _zai_glm_reasoning_efforts_supported(model_id: str, provider_id: str) -> bool | None:
    """Z.AI native-endpoint gate for the ``reasoning_effort`` intensity field.

    Returns True if the model accepts the effort ladder (GLM-5.2+), False if it
    is known NOT to (pre-5.2 GLM and the forced-thinking GLM-4.7 family), or None
    if this is not a native-``zai`` GLM model (caller should defer to other rules).

    Thin wrapper over ``_zai_glm_classification`` kept for the coercion path's
    explicit True/False/None contract. A known-False result means "send no
    ``reasoning_effort`` field" (distinct from the ambiguous empty list returned
    for genuinely unknown models, which preserves the configured effort verbatim
    per #3505).
    """
    cls = _zai_glm_classification(model_id, provider_id)
    if cls is None:
        return None
    return cls == "effort"


def _zai_glm_thinking_toggle_supported(model_id: str, provider_id: str) -> bool | None:
    """Z.AI native-endpoint gate for the ``thinking`` on/off toggle.

    Returns True if the model accepts the ``thinking: {"type": ...}`` toggle
    (GLM-4.5+ except the forced-thinking GLM-4.7), False if it does not
    (GLM-4.7 forced, or pre-4.5 GLM with no thinking support), or None if this
    is not a native-``zai`` GLM model (caller should defer — the toggle's
    availability is then governed by ``supported_efforts`` as before).

    Drives the ``supports_thinking_toggle`` field in ``get_reasoning_status`` so
    the composer can render an operable On/None control for GLM-4.5–5.1 models
    that accept the thinking toggle but not the effort ladder.
    """
    cls = _zai_glm_classification(model_id, provider_id)
    if cls is None:
        return None
    return cls in {"effort", "thinking"}


_OPENAI_FAMILY_REASONING_PROVIDERS = frozenset({
    "openai-codex", "openai", "openai-api",
    "azure-foundry", "azure-openai", "azure",
})

_GPT_5_6_REASONING_MODELS = frozenset({
    "gpt-5.6",
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-5.6-luna",
})


def _is_gpt_5_6_reasoning_model(bare_model: str) -> bool:
    """Return whether an OpenAI-family model uses GPT-5.6's max ladder."""
    return str(bare_model or "").strip().lower() in _GPT_5_6_REASONING_MODELS


def _filter_reasoning_efforts_for_provider(
    efforts: list[str],
    model_id: str,
    provider_id: str,
) -> list[str]:
    """Apply provider/model quirks to otherwise valid reasoning effort levels."""
    normalized = [
        str(eff).strip().lower()
        for eff in efforts
        if str(eff).strip().lower() in VALID_REASONING_EFFORTS
    ]
    normalized = list(dict.fromkeys(normalized))
    provider = _resolve_alias(str(provider_id or "").strip().lower())
    bare = _strip_provider_hint_for_reasoning(model_id).lower().rsplit("/", 1)[-1]
    # OpenAI-family lanes cap pre-GPT-5.6 GPT-5 models at xhigh and o-series at
    # high. GPT-5.6's alias and Sol/Terra/Luna variants natively accept max.
    if provider in _OPENAI_FAMILY_REASONING_PROVIDERS:
        if bare.startswith(("o1", "o3", "o4")):
            return [eff for eff in normalized if eff in {"low", "medium", "high"}]
        if bare.startswith("gpt-5") and not _is_gpt_5_6_reasoning_model(bare):
            return [eff for eff in normalized if eff != "max"]
    # Providers whose native ladder tops out below 'max' must NOT advertise it,
    # otherwise a stored/CLI 'max' degrades WORSE than the
    # prior max->xhigh coercion (Gemini's adapter treats unknown 'max' as medium;
    # pre-adaptive Anthropic manual-thinking lacks a 'max' budget and falls to 8k).
    # Dropping 'max' here lets the existing downgrade ladder land on xhigh/high.
    if provider in {"gemini", "google", "google-gemini", "google-vertex", "vertex"}:
        return [eff for eff in normalized if eff != "max"]
    # Legacy Claude is pre-adaptive whether served natively OR via Azure Foundry /
    # Bedrock / Vertex — the ceiling follows the MODEL, not just the provider name.
    _anthropic_lanes = {
        "anthropic", "claude", "anthropic-claude",
        "azure-foundry", "azure-openai", "azure", "bedrock", "aws-bedrock",
        "vertex", "google-vertex",
    }
    if provider in _anthropic_lanes and "claude" in bare and _is_pre_adaptive_anthropic(bare):
        return [eff for eff in normalized if eff != "max"]
    # Z.AI / GLM native-endpoint gate: see _zai_glm_reasoning_efforts_supported.
    # True → keep the full ladder (GLM-5.2+); False → strip it entirely (pre-5.2
    # GLM and forced-thinking GLM-4.7); None → not a zai GLM case, defer.
    zai_supports = _zai_glm_reasoning_efforts_supported(model_id, provider_id)
    if zai_supports is True:
        return normalized
    if zai_supports is False:
        return []
    return normalized


_KNOWN_REASONING_PROVIDERS = frozenset({
    "anthropic", "claude", "anthropic-claude",
    "openai", "openai-api", "openai-codex",
    "azure", "azure-openai", "azure-foundry",
    "bedrock", "aws-bedrock", "vertex", "google-vertex",
    "gemini", "google", "google-gemini",
    "deepseek", "x-ai", "xai", "grok",
    "copilot", "github-copilot", "openrouter",
})


def _provider_known_reasoning_capable(provider_id) -> bool:
    """True if the provider is one we recognize as reasoning-capable.

    Used to gate the 'max' default-deny: for a RECOGNIZED provider whose specific
    model we couldn't resolve (empty capability list), preserve 'max' since those
    providers genuinely support it; for a truly unknown/custom provider, degrade
    'max' -> 'xhigh' so we never send a supra-ceiling level that would 400.
    """
    prov = _resolve_alias(str(provider_id or "").strip().lower())
    return prov in _KNOWN_REASONING_PROVIDERS


def _is_pre_adaptive_anthropic(bare_model: str) -> bool:
    """True for Claude models that predate the adaptive-thinking (4.6+) generation.

    Adaptive models (Opus/Sonnet 4.6+, 4.7, …) accept 'max'; earlier manual-thinking
    Claudes (3.x and 4.0–4.5) do not and must degrade 'max' to xhigh rather than
    fall through the manual-thinking budget table to its 8k default.

    Handles the ID shapes the Anthropic adapter uses:
      - claude-3-opus / claude-3-5-sonnet / claude-3-7-sonnet   → pre-adaptive
      - claude-sonnet-4-5 / claude-haiku-4-5                     → pre-adaptive (4.5)
      - claude-sonnet-4-20250514 (date-stamped 4.0 build)        → pre-adaptive
      - claude-opus-4.6 / claude-sonnet-4.6 / claude-opus-4.7    → adaptive
      - claude-opus-latest / unversioned                        → adaptive (flagship)
    """
    import re as _re
    m = (bare_model or "").lower()
    if "claude" not in m:
        return False
    # Claude 3.x family is always pre-adaptive.
    if _re.search(r"claude-3\b", m) or _re.search(r"claude-3[.\-]", m):
        return True
    # Find a major[.-]minor version. A date-stamp (>=6 digits) is NOT a minor
    # version — treat "4-20250514" as major 4 with no real minor (a 4.0 build).
    match = _re.search(r"(\d+)[.\-](\d{1,2})(?!\d)", m)
    if not match:
        # A bare major like "claude-sonnet-4" or "...-4-20250514" (date stamp
        # consumed no minor): major-4 with no minor is a pre-adaptive 4.0 build.
        major_only = _re.search(r"[-.](\d+)(?:[-.]\d{6,})?(?:\b|-)", m)
        if major_only:
            return int(major_only.group(1)) < 5  # 4.x (no minor) → pre-adaptive; ≥5 → adaptive
        # Unversioned / *-latest → treat as current flagship (adaptive).
        return False
    major, minor = int(match.group(1)), int(match.group(2))
    return (major, minor) < (4, 6)


def _heuristic_reasoning_efforts(model_id: str, provider_id: str) -> list[str]:
    """Fallback when hermes_cli is unavailable."""
    model = _strip_provider_hint_for_reasoning(model_id).lower()
    provider = _resolve_alias(str(provider_id or "").strip().lower())
    if not model or provider in {"cursor-acp", "copilot-acp"}:
        return []
    bare = model.rsplit("/", 1)[-1]
    if provider == "openai-codex" and bare.startswith(("gpt-5", "o1", "o3", "o4")):
        if bare.startswith(("o1", "o3", "o4")):
            return ["low", "medium", "high"]
        return _filter_reasoning_efforts_for_provider(
            list(VALID_REASONING_EFFORTS), model, provider
        )
    if provider in {"copilot", "github-copilot"}:
        if bare.startswith(("gpt-5", "o1", "o3", "o4")):
            if bare.startswith(("o1", "o3", "o4")):
                return ["low", "medium", "high"]
            return list(VALID_REASONING_EFFORTS)
    prefixes = (
        "deepseek/",
        "anthropic/",
        "openai/",
        "x-ai/",
        "google/gemini-2",
        "google/gemma-4",
        "qwen/qwen3",
        "tencent/hy3-preview",
        "xiaomi/",
    )
    if any(model.startswith(prefix) for prefix in prefixes):
        return list(VALID_REASONING_EFFORTS)
    if _nested_gateway_route_reasoning(model):
        return list(VALID_REASONING_EFFORTS)
    # Named custom providers often rewrite model ids with dots, underscores, or
    # extra vendor namespaces. Normalize those shapes before applying family-level
    # reasoning heuristics so "deepseek.v3.2", "deepseek_v4_flash", and
    # "vendor.deepseek.v3.2" are treated consistently.
    if any(_candidate_supports_reasoning(candidate) for candidate in _reasoning_name_candidates(bare)):
        return list(VALID_REASONING_EFFORTS)
    return []


def _models_dev_reasoning_efforts(model_id: str, provider_id: str) -> list[str] | None:
    """Return reasoning efforts from Hermes Agent model metadata when known.

    ``None`` means the metadata source is unavailable or has no answer, so the
    caller should continue to compatibility fallbacks. A concrete list (including
    ``[]``) is authoritative.
    """
    model = _strip_provider_hint_for_reasoning(model_id)
    provider = str(provider_id or "").strip().lower()
    if not model or not provider:
        return None

    try:
        from agent.models_dev import get_model_capabilities
    except Exception:
        return None

    try:
        capabilities = get_model_capabilities(provider=provider, model=model)
    except Exception:
        return None
    if capabilities is None:
        return None

    supports_reasoning = getattr(capabilities, "supports_reasoning", None)
    if supports_reasoning is True:
        return _filter_reasoning_efforts_for_provider(
            list(VALID_REASONING_EFFORTS), model, provider
        )
    if supports_reasoning is False:
        return []
    return None


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """urllib redirect handler that refuses to follow any redirect.

    Used by the LM Studio reasoning probe so a 3xx from the probe URL can never
    forward the ``Authorization`` header (the configured LM Studio key) to a
    redirected, possibly attacker-controlled host. ``redirect_request``
    returning ``None`` makes urllib raise the original 3xx as an ``HTTPError``,
    which the probe swallows. (#3837 security review)
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _get_lmstudio_reasoning_probe_api_key() -> str | None:
    """Resolve the LM Studio key for reasoning probes with WebUI precedence."""
    config_data = _cfg()
    model_cfg = config_data.get("model") or {}
    if isinstance(model_cfg, dict):
        active_provider = str(model_cfg.get("provider") or "").strip().lower()
        model_key = str(model_cfg.get("api_key") or "").strip()
        if active_provider == "lmstudio" and model_key:
            return model_key

    providers_cfg = config_data.get("providers") or {}
    if isinstance(providers_cfg, dict):
        lmstudio_cfg = providers_cfg.get("lmstudio") or {}
        if isinstance(lmstudio_cfg, dict):
            config_key = str(lmstudio_cfg.get("api_key") or "").strip()
            if config_key:
                return config_key

    env_key = str(os.getenv("LM_API_KEY") or "").strip()
    if env_key:
        return env_key

    legacy_env_key = str(os.getenv("LMSTUDIO_API_KEY") or "").strip()
    if legacy_env_key:
        return legacy_env_key

    return None


def _lmstudio_reasoning_probe_options_fallback(
    model: str,
    base_url: str | None,
    *,
    api_key: str | None = None,
    timeout: float = 5.0,
) -> list[str]:
    """Query LM Studio reasoning options without relying on hermes_cli."""
    server_root = str(base_url or "").strip().rstrip("/")
    if server_root.endswith("/v1"):
        server_root = server_root[:-3].rstrip("/")
    if not server_root or not model:
        return []

    headers = {
        "Accept": "application/json",
        "User-Agent": "hermes-webui-reasoning-probe",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    request = urllib.request.Request(
        server_root + "/api/v1/models",
        headers=headers,
        method="GET",
    )
    # SECURITY: never follow redirects on this probe. urllib re-sends request
    # headers (including Authorization: Bearer <lmstudio key>) to the redirect
    # target, so a 3xx from the probe URL could exfiltrate the configured
    # LM Studio credential to an attacker-controlled host. A no-redirect opener
    # turns any 3xx into an HTTPError we swallow below. (#3837 security review)
    opener = urllib.request.build_opener(_NoRedirectHandler)
    try:
        with opener.open(request, timeout=timeout) as response:  # nosec B310
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        logger.debug(
            "LM Studio reasoning probe at %s failed with HTTP %s",
            server_root,
            exc.code,
        )
        return []
    except Exception as exc:
        logger.debug("LM Studio reasoning probe at %s failed: %s", server_root, exc)
        return []

    raw_models = payload.get("models") if isinstance(payload, dict) else None
    if not isinstance(raw_models, list):
        logger.debug(
            "LM Studio reasoning probe at %s returned malformed payload",
            server_root,
        )
        return []

    for raw in raw_models:
        if not isinstance(raw, dict):
            continue
        if raw.get("key") != model and raw.get("id") != model:
            continue
        caps = raw.get("capabilities")
        reasoning = caps.get("reasoning") if isinstance(caps, dict) else None
        opts = reasoning.get("allowed_options") if isinstance(reasoning, dict) else None
        if isinstance(opts, list):
            return [str(opt).strip().lower() for opt in opts if isinstance(opt, str)]
        return []
    return []


def _lmstudio_model_reasoning_options(
    model: str,
    base_url: str | None,
    *,
    api_key: str | None = None,
    timeout: float = 5.0,
) -> list[str]:
    """Prefer hermes_cli, but keep WebUI reasoning probes working without it.

    SECURITY: when an ``api_key`` is being sent, always use the built-in
    no-redirect fallback probe rather than ``hermes_cli``. The bundled CLI probe
    uses a plain ``urllib.request.urlopen`` that follows redirects and re-sends
    the ``Authorization`` header to the redirect target, which could leak the
    configured LM Studio credential to another host. We can only guarantee
    redirect safety for code we control, so credentialed probes go through
    ``_lmstudio_reasoning_probe_options_fallback`` (no-redirect opener). Keyless
    probes have no credential to leak, so they may use the richer CLI path.
    (#3837 security review)
    """
    if api_key:
        return _lmstudio_reasoning_probe_options_fallback(
            model,
            base_url,
            api_key=api_key,
            timeout=timeout,
        )

    try:
        from api.agent_compat import agent_attr

        _cli_lmstudio_model_reasoning_options = agent_attr(
            "hermes_cli.models", "lmstudio_model_reasoning_options", "hermes_cli.models_local"
        )
    except Exception:
        return _lmstudio_reasoning_probe_options_fallback(
            model,
            base_url,
            api_key=api_key,
            timeout=timeout,
        )

    try:
        return _cli_lmstudio_model_reasoning_options(
            model,
            base_url,
            api_key=api_key,
            timeout=timeout,
        )
    except (TypeError, AttributeError):
        # Emit via api.config's logger as well so tests that monkeypatch
        # config.logger.warning observe the warning after the split.
        _cfg_warning = None
        try:
            import api.config as _ac

            _cfg_warning = getattr(getattr(_ac, "logger", None), "warning", None)
        except Exception:
            pass
        if callable(_cfg_warning) and _cfg_warning is not logger.warning:
            try:
                _cfg_warning(
                    "hermes_cli.lmstudio_model_reasoning_options has an unexpected signature; "
                    "falling back to the built-in LM Studio reasoning probe",
                    exc_info=True,
                )
            except Exception:
                pass
        logger.warning(
            "hermes_cli.lmstudio_model_reasoning_options has an unexpected signature; "
            "falling back to the built-in LM Studio reasoning probe",
            exc_info=True,
        )
        return _lmstudio_reasoning_probe_options_fallback(
            model,
            base_url,
            api_key=api_key,
            timeout=timeout,
        )
    except Exception:
        return _lmstudio_reasoning_probe_options_fallback(
            model,
            base_url,
            api_key=api_key,
            timeout=timeout,
        )


def resolve_model_reasoning_efforts(
    model_id: str | None = None,
    provider_id: str | None = None,
    base_url: str | None = None,
) -> list[str]:
    """Return supported reasoning-effort levels for *model_id*, or [] if none.

    Always passes the sourced list through _filter_reasoning_efforts_for_provider
    so the hard provider ceilings (OpenAI-family GPT-5 before 5.6 at xhigh and
    o-series at high; Gemini + pre-adaptive/cloud-hosted Claude at xhigh) are
    applied uniformly. The UI dropdown and coercion therefore agree: ``max`` is
    retained for GPT-5.6 and other models whose native ladder includes it, and
    stripped where it would be rejected or mishandled.
    """
    # Indirection through api.config so `mock.patch("api.config._resolve_model_reasoning_efforts_impl", ...)`
    # in tests patches the impl actually used here. Without it the test would patch
    # `api.config._resolve...` but `reasoning.py` would still call its own local copy.
    try:
        import api.config as _ac

        _impl = getattr(_ac, "_resolve_model_reasoning_efforts_impl", None)
        if callable(_impl) and _impl is not _resolve_model_reasoning_efforts_impl:
            raw = _impl(model_id, provider_id, base_url)
        else:
            raw = _resolve_model_reasoning_efforts_impl(model_id, provider_id, base_url)
    except Exception:
        raw = _resolve_model_reasoning_efforts_impl(model_id, provider_id, base_url)
    if not raw:
        return raw
    # Forced-thinking models (GLM-4.7 on native zai) cannot have reasoning
    # disabled, so the 'none' sentinel must NOT appear in their supported list —
    # otherwise the UI offers an "off" option that has no effect and contradicts
    # the forced-tier contract. (#6219 round-3)
    if _zai_glm_classification(model_id, provider_id) == "forced":
        return []
    # Preserve any explicit 'none' sentinel (valid UI option = "no reasoning");
    # the ceiling filter only knows the reasoning LEVELS.
    had_none = "none" in raw
    filtered = _filter_reasoning_efforts_for_provider(
        [e for e in raw if e != "none"], str(model_id or ""), str(provider_id or "")
    )
    if had_none:
        # Keep 'none' in its original leading position if it was there.
        return ["none", *filtered] if raw and raw[0] == "none" else [*filtered, "none"]
    return filtered


def _configured_reasoning_effort_lists(provider_entry, model_id: str) -> list:
    """Return model-level then provider-level effort lists from config."""
    if not isinstance(provider_entry, dict):
        return []

    configured_lists = []
    models = provider_entry.get("models")
    if isinstance(models, dict):
        model_key = str(model_id or "").strip().lower()
        model_entry = models.get(model_id)
        if not isinstance(model_entry, dict) and model_key:
            model_entry = next(
                (
                    metadata
                    for configured_id, metadata in models.items()
                    if str(configured_id).strip().lower() == model_key
                    and isinstance(metadata, dict)
                ),
                None,
            )
        if isinstance(model_entry, dict):
            configured_lists.append(model_entry.get("reasoning_efforts"))

    configured_lists.append(provider_entry.get("reasoning_efforts"))
    return configured_lists


def _resolve_model_reasoning_efforts_impl(
    model_id: str | None = None,
    provider_id: str | None = None,
    base_url: str | None = None,
) -> list[str]:
    """Return supported reasoning-effort levels for *model_id*, or [] if none."""
    model = str(model_id or "").strip()
    if not model:
        return []

    provider = str(provider_id or "").strip().lower() if provider_id else ""
    resolved_base_url = str(base_url or "").strip() or None
    if not provider:
        try:
            _, provider, resolved_base_url = _resolve_model_provider(model)
        except Exception:
            provider = str((_cfg().get("model") or {}).get("provider") or "").strip().lower()

    provider = _resolve_alias(provider)

    # IDE-copilot providers never expose reasoning effort options.
    # Guard early so a stray config entry can't override this.
    if provider in {"cursor-acp", "copilot-acp"}:
        return []

    hinted_model = _strip_provider_hint_for_reasoning(model, provider)

    # Master hides reasoning controls for nested image/embedding routes. Keep
    # that hard deny above provider config so an explicit allowlist cannot
    # re-enable controls for routes that should never expose them.
    if _nested_route_reasoning_denied(hinted_model):
        return []

    # 0. Model/provider config: a models.<model>.reasoning_efforts list takes
    # precedence over its provider-level reasoning_efforts list. Explicit valid
    # config is authoritative — no heuristics or models.dev lookup. Invalid or
    # empty model metadata falls through to the provider list, then heuristics.
    _re_lists = []
    try:
        if provider and provider.startswith("custom:"):
            for _entry in _custom_entries():
                if _slug_from_name(_entry.get("name")) == provider:
                    _re_lists = _configured_reasoning_effort_lists(
                        _entry, hinted_model
                    )
                    break
        elif provider:
            _prov_entry = (_cfg().get("providers") or {}).get(provider, {})
            if isinstance(_prov_entry, dict):
                _re_lists = _configured_reasoning_effort_lists(
                    _prov_entry, hinted_model
                )
        for _re_list in _re_lists:
            if isinstance(_re_list, list) and _re_list:
                _filtered = [str(x).strip().lower() for x in _re_list
                             if str(x).strip().lower() in {*VALID_REASONING_EFFORTS, "none"}]
                _filtered = list(dict.fromkeys(_filtered))
                if _filtered:
                    return _filtered
    except Exception:
        pass

    if provider in {"copilot", "github-copilot"}:
        try:
            from hermes_cli.models import github_model_reasoning_efforts
        except Exception:
            return _heuristic_reasoning_efforts(hinted_model, provider)
        return _filter_reasoning_efforts_for_provider(
            github_model_reasoning_efforts(hinted_model), hinted_model, provider
        )

    if provider == "lmstudio":
        configured_base = _provider_base_url(provider)
        probe_base = resolved_base_url or configured_base
        # SECURITY: only forward the configured LM Studio credential when the
        # probe target is the configured LM Studio endpoint. /api/reasoning
        # accepts a caller-supplied base_url, so a request could otherwise point
        # the probe at an arbitrary host and harvest the stored key. When the
        # caller supplies a base_url that does not normalize to the configured
        # one, probe it WITHOUT a key. (#3837 security review)
        probe_key: str | None = None
        if not resolved_base_url or (
            configured_base
            and _normalize_url(probe_base)
            == _normalize_url(configured_base)
        ):
            probe_key = _get_lmstudio_reasoning_probe_api_key()
        opts = _lmstudio_model_reasoning_options(
            hinted_model,
            probe_base,
            api_key=probe_key,
        )
        normalized = [str(opt).strip().lower() for opt in opts if str(opt).strip()]
        if not normalized or set(normalized).issubset({"off"}):
            return []
        level_opts = [opt for opt in normalized if opt in VALID_REASONING_EFFORTS]
        if level_opts:
            return _filter_reasoning_efforts_for_provider(
                level_opts, hinted_model, provider
            )
        if set(normalized).issubset({"off", "on"}):
            return []
        return []

    # _models_dev_reasoning_efforts already applies the provider/model filter
    # internally, so it is returned as-is here (filtering again would be
    # redundant — the filter is idempotent but the double pass obscures flow).
    metadata_efforts = _models_dev_reasoning_efforts(hinted_model, provider)
    if metadata_efforts is not None:
        return metadata_efforts

    return _heuristic_reasoning_efforts(hinted_model, provider)


