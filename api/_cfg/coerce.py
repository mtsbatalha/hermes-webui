"""Coercion + status helpers extracted from api/config.py.

Re-exported via ``api.config`` so ``from api.config import coerce_reasoning_effort_for_model``
keeps working.  No external module should import from ``api._cfg.coerce`` directly.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


# Lazy helpers — resolved through api.config / api._cfg at call time.

def _cfg() -> dict:
    try:
        import api.config as _ac
        fn = getattr(_ac, "get_config", None)
        if callable(fn):
            return fn() or {}
    except Exception:
        pass
    return {}


def _get_cfg_path():
    try:
        import api.config as _ac
        fn = getattr(_ac, "_get_config_path", None)
        if callable(fn):
            return fn()
    except Exception:
        pass
    return None


def _load_yaml(path):
    try:
        import api.config as _ac
        fn = getattr(_ac, "_load_yaml_config_file", None)
        if callable(fn):
            return fn(path)
    except Exception:
        pass
    return {}


def _load_yaml_raw(path):
    try:
        import api.config as _ac
        fn = getattr(_ac, "_load_yaml_config_file_raw", None)
        if callable(fn):
            return fn(path)
    except Exception:
        pass
    return {}


def _save_yaml(path, data) -> None:
    try:
        import api.config as _ac
        fn = getattr(_ac, "_save_yaml_config_file", None)
        if callable(fn):
            return fn(path, data)
    except Exception:
        pass


def _cfg_lock_cm():
    try:
        import api.config as _ac
        return getattr(_ac, "_cfg_lock")
    except Exception:
        import threading
        return threading.Lock()


def _reload_cfg() -> None:
    try:
        import api.config as _ac
        fn = getattr(_ac, "reload_config", None)
        if callable(fn):
            return fn()
    except Exception:
        pass


def _valid_efforts():
    try:
        import api.config as _ac
        return getattr(_ac, "VALID_REASONING_EFFORTS", ("minimal", "low", "medium", "high", "xhigh", "max"))
    except Exception:
        pass
    try:
        import api._cfg.reasoning as _r
        return getattr(_r, "VALID_REASONING_EFFORTS", ("minimal", "low", "medium", "high", "xhigh", "max"))
    except Exception:
        pass
    return ("minimal", "low", "medium", "high", "xhigh", "max")


def _filter_efforts(efforts, model_id, provider_id):
    try:
        import api.config as _ac
        fn = getattr(_ac, "_filter_reasoning_efforts_for_provider", None)
        if callable(fn):
            return fn(efforts, model_id, provider_id)
    except Exception:
        pass
    try:
        import api._cfg.reasoning as _r
        fn = getattr(_r, "_filter_reasoning_efforts_for_provider", None)
        if callable(fn):
            return fn(efforts, model_id, provider_id)
    except Exception:
        pass
    return list(efforts or [])


def _zai_classification(model_id, provider_id):
    try:
        import api.config as _ac
        fn = getattr(_ac, "_zai_glm_classification", None)
        if callable(fn):
            return fn(model_id, provider_id)
    except Exception:
        pass
    try:
        import api._cfg.reasoning as _r
        fn = getattr(_r, "_zai_glm_classification", None)
        if callable(fn):
            return fn(model_id, provider_id)
    except Exception:
        pass
    return None


def _zai_reasoning_supported(model_id, provider_id):
    try:
        import api.config as _ac
        fn = getattr(_ac, "_zai_glm_reasoning_efforts_supported", None)
        if callable(fn):
            return fn(model_id, provider_id)
    except Exception:
        pass
    try:
        import api._cfg.reasoning as _r
        fn = getattr(_r, "_zai_glm_reasoning_efforts_supported", None)
        if callable(fn):
            return fn(model_id, provider_id)
    except Exception:
        pass
    return None


def _zai_toggle_supported(model_id, provider_id):
    try:
        import api.config as _ac
        fn = getattr(_ac, "_zai_glm_thinking_toggle_supported", None)
        if callable(fn):
            return fn(model_id, provider_id)
    except Exception:
        pass
    try:
        import api._cfg.reasoning as _r
        fn = getattr(_r, "_zai_glm_thinking_toggle_supported", None)
        if callable(fn):
            return fn(model_id, provider_id)
    except Exception:
        pass
    return None


def _provider_capable(provider_id):
    try:
        import api.config as _ac
        fn = getattr(_ac, "_provider_known_reasoning_capable", None)
        if callable(fn):
            return fn(provider_id)
    except Exception:
        pass
    try:
        import api._cfg.reasoning as _r
        fn = getattr(_r, "_provider_known_reasoning_capable", None)
        if callable(fn):
            return fn(provider_id)
    except Exception:
        pass
    return False


def _resolve_efforts(model_id, provider_id=None, base_url=None):
    try:
        import api.config as _ac
        fn = getattr(_ac, "resolve_model_reasoning_efforts", None)
        if callable(fn):
            return fn(model_id, provider_id=provider_id, base_url=base_url)
    except Exception:
        pass
    try:
        import api._cfg.reasoning as _r
        fn = getattr(_r, "resolve_model_reasoning_efforts", None)
        if callable(fn):
            return fn(model_id, provider_id=provider_id, base_url=base_url)
    except Exception:
        pass
    return []

def coerce_reasoning_effort_for_model(
    effort: str | None,
    model_id: str | None = None,
    provider_id: str | None = None,
    base_url: str | None = None,
) -> str:
    """Return the closest supported effort for the target model/provider."""
    raw = str(effort or "").strip().lower()
    if not raw:
        return ""
    # Forced-thinking models (GLM-4.7 on native zai) cannot have reasoning
    # disabled at all — a stored 'none' must coerce to '' (provider default =
    # thinking on) so streaming does not build disabled reasoning for a model
    # that forces thinking on regardless. Checked BEFORE the generic 'none'
    # early-return below so the forced-tier contract wins. (#6219 round-3)
    if raw == "none" and _zai_classification(model_id, provider_id) == "forced":
        return ""
    if raw == "none":
        return "none"
    if raw not in _valid_efforts():
        return ""
    supported = _resolve_efforts(
        model_id,
        provider_id=provider_id,
        base_url=base_url,
    )
    # Hard provider ceilings must win regardless of what the sourced capability
    # list says. _resolve_efforts() draws from hermes_cli /
    # models.dev / heuristics, and those can (a) return [] for an unrecognized
    # model or (b) wrongly advertise 'max' for a provider
    # whose native ladder tops out lower. _filter_reasoning_efforts_for_provider
    # encodes the known ceilings (OpenAI-family GPT-5 before 5.6, Gemini, and
    # pre-adaptive Anthropic all cap below 'max'); if it actively EXCLUDES the
    # requested level, honor that ceiling and degrade down the ladder even when
    # the sourced list is empty or (mistakenly) includes the level. This keeps a
    # stored/CLI 'max' from reaching an adapter that would silently downgrade it
    # worse than xhigh/high (Gemini→medium, legacy Claude manual-thinking→8k).
    # GPT-5.6 is intentionally not capped. For providers with NO ceiling rule the
    # filter returns the full list unchanged, so genuinely unknown models still
    # preserve the configured effort (#3505 behavior).
    ceiling = _filter_efforts(
        list(_valid_efforts()), str(model_id or ""), str(provider_id or "")
    )
    if ceiling and raw not in ceiling:
        ladder = list(_valid_efforts())  # ascending: minimal..xhigh..max
        try:
            raw_idx = ladder.index(raw)
        except ValueError:
            raw_idx = None
        if raw_idx is not None:
            for level in reversed(ladder[:raw_idx]):  # strictly lower, highest first
                if level in ceiling:
                    return level
    # An empty list is ambiguous: _resolve_efforts() returns []
    # both for models KNOWN not to support reasoning AND for models we simply
    # don't recognize (custom providers, aggregator-rewritten ids, brand-new
    # releases). Coercion exists to avoid sending a level a KNOWN-incompatible
    # model rejects (e.g. pre-5.6 GPT-5 'max', o1/o3/o4 above 'high') -
    # those paths return a NON-empty clamped set, so the degrade ladder below
    # still applies. When the set is empty we can't tell "unsupported" from
    # "unknown", so preserve the user's configured effort verbatim where it is
    # still valid. (#3505 review)
    #
    # EXCEPTION for 'max' (the #3505 default-deny refinement, maintainer call
    # 2026-07-11): 'max' is ABOVE the universally-safe ceiling 'xhigh'. A
    # genuinely unknown/custom provider will 400 on it. So when the
    # capability list is empty AND the provider is not one we recognize as
    # reasoning-capable, degrade 'max' -> 'xhigh' rather than send an unsupported
    # supra-ceiling level. But do NOT degrade for a RECOGNIZED reasoning provider
    # whose specific model id we simply couldn't resolve (e.g. claude-opus-latest,
    # a brand-new adaptive id) — those genuinely support 'max', and the ceiling
    # filter above already stripped it for any KNOWN-capped model. All other
    # levels (minimal..xhigh) keep the conservative preserve-verbatim behavior.
    #
    # EXCEPTION for the ZAI native-endpoint gate: a pre-5.2 GLM model (incl. the
    # forced-thinking GLM-4.7) is KNOWN not to accept reasoning_effort at all, so
    # any stored level must coerce to "" (send no field) — NOT be preserved
    # verbatim, which Z.AI would silently ignore. This keeps the value actually
    # sent in agreement with the UI (which offers no options for these models).
    if not supported:
        if _zai_reasoning_supported(model_id, provider_id) is False:
            return ""
        if raw == "max" and not _provider_capable(provider_id):
            return "xhigh"
        return raw
    if raw in supported:
        return raw
    # Degrade to the closest *lower* supported level instead of silently
    # disabling reasoning. e.g. max -> xhigh -> high, or xhigh -> high when the
    # target model caps below the configured effort. Never escalate.
    ladder = list(_valid_efforts())  # ascending: minimal..xhigh..max
    try:
        raw_idx = ladder.index(raw)
    except ValueError:
        return raw
    for level in reversed(ladder[:raw_idx]):  # strictly lower, highest first
        if level in supported:
            return level
    # raw is below every supported level (shouldn't happen for a non-empty set
    # that excludes raw, but be safe): preserve the configured effort rather
    # than blank it.
    return raw


def get_reasoning_status(
    *,
    model_id: str | None = None,
    provider_id: str | None = None,
    base_url: str | None = None,
) -> dict:
    """Return current reasoning configuration from the active profile's
    config.yaml — the same source of truth the CLI reads from.

    Keys:
      - show_reasoning: bool — from ``display.show_reasoning`` (default True)
      - reasoning_effort: str — from ``agent.reasoning_effort`` ('' = default)
    """
    config_data = _load_yaml(_get_cfg_path())
    display_cfg = config_data.get("display") or {}
    agent_cfg = config_data.get("agent") or {}
    show_raw = display_cfg.get("show_reasoning") if isinstance(display_cfg, dict) else None
    effort_raw = agent_cfg.get("reasoning_effort") if isinstance(agent_cfg, dict) else None

    resolve_model = model_id
    resolve_provider = provider_id
    resolve_base_url = base_url
    if not resolve_model:
        model_cfg = config_data.get("model") or {}
        if isinstance(model_cfg, dict):
            resolve_model = str(model_cfg.get("default") or "").strip() or None
            if not resolve_provider and model_cfg.get("provider"):
                resolve_provider = str(model_cfg["provider"]).strip()
            if not resolve_base_url and model_cfg.get("base_url"):
                resolve_base_url = str(model_cfg["base_url"]).strip()

    supported_efforts = _resolve_efforts(
        resolve_model,
        provider_id=resolve_provider,
        base_url=resolve_base_url,
    )
    # supports_thinking_toggle: can the user turn thinking on/off at all? An
    # effort-capable model obviously can. The ZAI gate separately exposes the
    # toggle for GLM-4.5–5.1 (which accept `thinking: {"type": ...}` but NOT the
    # `reasoning_effort` ladder), so the composer still renders an On/None control
    # when supported_efforts is empty. Without this, returning [] for those models
    # would hide the entire reasoning chip and silently regress the working
    # thinking on/off control (#6219 round-2 review).
    zai_thinking = _zai_toggle_supported(
        resolve_model, resolve_provider
    )
    supports_thinking_toggle = bool(supported_efforts) or (zai_thinking is True)
    return {
        # Match CLI default (True if unset in config.yaml)
        "show_reasoning": bool(show_raw) if isinstance(show_raw, bool) else True,
        # Report the COERCED effort so boot/status/chip read paths agree with
        # what streaming actually sends. (Codex review of the drop-max alignment.)
        "reasoning_effort": coerce_reasoning_effort_for_model(
            str(effort_raw or "").strip().lower(),
            resolve_model,
            provider_id=resolve_provider,
            base_url=resolve_base_url,
        ),
        "supported_efforts": supported_efforts,
        "supports_reasoning_effort": bool(supported_efforts),
        # Whether the composer should render ANY reasoning control. True for any
        # effort-capable model OR a ZAI GLM model that accepts the thinking
        # toggle but not the effort ladder. False hides the chip entirely.
        "supports_thinking_toggle": supports_thinking_toggle,
    }


def _parse_positive_int_config_value(raw) -> int | None:
    if raw is None:
        return None
    try:
        parsed = int(raw)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def get_max_tokens_status() -> dict[str, int | None]:
    """Return the Settings-facing max_tokens state from the active profile config.

    ``max_tokens`` is the root override the Settings field owns directly.
    ``max_tokens_fallback`` is the agent-level fallback when the root config
    resolves to ``None``, matching the streaming path exactly.
    ``max_tokens_effective`` is the runtime cap a new streaming turn would
    currently use.
    """
    config_data = _load_yaml(_get_cfg_path())
    if not isinstance(config_data, dict):
        return {
            "max_tokens": None,
            "max_tokens_effective": None,
            "max_tokens_fallback": None,
        }

    raw_root_value = config_data.get("max_tokens")
    root_value = _parse_positive_int_config_value(raw_root_value)

    fallback_value = None
    if raw_root_value is None:
        agent_cfg = config_data.get("agent")
        if isinstance(agent_cfg, dict):
            fallback_value = _parse_positive_int_config_value(agent_cfg.get("max_tokens"))

    effective_value = root_value if root_value is not None else fallback_value
    return {
        "max_tokens": root_value,
        "max_tokens_effective": effective_value,
        "max_tokens_fallback": fallback_value,
    }


def set_max_tokens(max_tokens) -> dict[str, int | None]:
    """Persist a root-level ``max_tokens`` override to the active profile config.

    Blank/``None`` clears the root override so ``agent.max_tokens`` can resume.
    Positive integers are written to the active profile's ``config.yaml``.
    Unrelated YAML keys are preserved verbatim.
    """
    if isinstance(max_tokens, str):
        max_tokens = max_tokens.strip()
    clear_root = max_tokens in (None, "")
    parsed_max_tokens = _parse_positive_int_config_value(max_tokens)
    if not clear_root and parsed_max_tokens is None:
        return get_max_tokens_status()

    config_path = _get_cfg_path()
    should_save = True
    with _cfg_lock_cm():
        config_data = _load_yaml_raw(config_path)
        if clear_root:
            if "max_tokens" not in config_data:
                should_save = False
            else:
                config_data.pop("max_tokens", None)
        elif parsed_max_tokens is not None:
            config_data["max_tokens"] = parsed_max_tokens
        if should_save:
            _save_yaml(config_path, config_data)
    if not should_save:
        return get_max_tokens_status()
    _reload_cfg()
    return get_max_tokens_status()


def set_reasoning_display(show: bool) -> dict:
    """Persist ``display.show_reasoning`` to the active profile's config.yaml.

    Mirrors CLI ``/reasoning show|hide``: writes the same key that the CLI
    writes, so the preference is shared across the WebUI and the terminal
    REPL for the same profile.
    """
    config_path = _get_cfg_path()
    with _cfg_lock_cm():
        config_data = _load_yaml(config_path)
        display_cfg = config_data.get("display")
        if not isinstance(display_cfg, dict):
            display_cfg = {}
        display_cfg["show_reasoning"] = bool(show)
        config_data["display"] = display_cfg
        _save_yaml(config_path, config_data)
    _reload_cfg()
    return get_reasoning_status()


def set_reasoning_effort(
    effort: str,
    *,
    model_id: str | None = None,
    provider_id: str | None = None,
    base_url: str | None = None,
) -> dict:
    """Persist ``agent.reasoning_effort`` to the active profile's config.yaml.

    Mirrors CLI ``/reasoning <level>``: same key, same valid values
    (``none`` | ``minimal`` | ``low`` | ``medium`` | ``high`` | ``xhigh`` | ``max``).

    An empty string is accepted as "clear the override" — it removes the
    ``agent.reasoning_effort`` key so the provider default takes effect. This is
    the re-enable path for thinking-toggle-only models (GLM-4.5–5.1 on native
    zai): the dropdown's "Default"/"On" option POSTs ``effort:''`` to switch
    thinking back on after the user selected "None". Without this, the toggle
    would be one-way (off-only) for those models. (#6219 round-3)

    Raises ``ValueError`` on any other unrecognised level so callers can 400.
    """
    raw = str(effort or "").strip().lower()
    if raw and raw != "none" and raw not in _valid_efforts():
        raise ValueError(
            f"Unknown reasoning effort '{effort}'. "
            f"Valid: none, {', '.join(_valid_efforts())}."
        )
    config_path = _get_cfg_path()
    with _cfg_lock_cm():
        config_data = _load_yaml(config_path)
        agent_cfg = config_data.get("agent")
        if not isinstance(agent_cfg, dict):
            agent_cfg = {}
        if raw:
            agent_cfg["reasoning_effort"] = raw
        else:
            # Clear the override so the provider default takes effect (the
            # "Default"/"On" re-enable path for thinking-toggle-only models).
            # Drop the key entirely rather than writing an empty string so the
            # CLI's "is reasoning_effort configured?" check stays simple.
            agent_cfg.pop("reasoning_effort", None)
        config_data["agent"] = agent_cfg
        _save_yaml(config_path, config_data)
    _reload_cfg()
    return get_reasoning_status(
        model_id=model_id,
        provider_id=provider_id,
        base_url=base_url,
    )


