"""Toolset helpers extracted from api/config.py.

Re-exported via ``api.config`` so ``from api.config import CLI_TOOLSETS`` keeps
working.  No external module should import from ``api._cfg.toolsets`` directly.
"""

from __future__ import annotations

_DEFAULT_TOOLSETS = [
    "browser",
    "clarify",
    "code_execution",
    "cronjob",
    "delegation",
    "file",
    "image_gen",
    "memory",
    "session_search",
    "skills",
    "terminal",
    "todo",
    "web",
    "webhook",
]

_LEGACY_CLI_TOOLSET_ALIASES = {
    "hermes": ("hermes-cli", "hermes-api-server"),
}


def _normalize_cli_toolsets(toolsets):
    """Expand legacy CLI toolset aliases while preserving order and de-duping."""
    normalized = []
    seen = set()
    for name in toolsets or []:
        replacements = _LEGACY_CLI_TOOLSET_ALIASES.get(name, (name,))
        for replacement in replacements:
            if replacement and replacement not in seen:
                seen.add(replacement)
                normalized.append(replacement)
    return normalized


def _resolve_cli_toolsets(cfg=None):
    """Resolve CLI toolsets using the agent's _get_platform_tools() so that
    MCP server toolsets are automatically included, matching CLI behaviour."""
    if cfg is None:
        # Lazy import avoids circular init (api.config imports this module at load).
        from api.config import get_config as _get_config  # noqa: WPS433

        cfg = _get_config()
    try:
        from hermes_cli.tools_config import _get_platform_tools

        return _normalize_cli_toolsets(_get_platform_tools(cfg, "cli"))
    except Exception:
        return _normalize_cli_toolsets(cfg.get("platform_toolsets", {}).get("cli", _DEFAULT_TOOLSETS))
