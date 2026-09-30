"""Live-model helpers extracted from api/config.py.

Re-exported via ``api.config`` so ``from api.config import
_read_live_provider_model_ids`` keeps working.  No external module should
import from ``api._cfg.live_models`` directly.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)


def _read_live_provider_model_ids(provider_id: str) -> list[str]:
    """Return live model IDs from Hermes CLI for a provider, or [] on failure.

    WebUI's static ``_PROVIDER_MODELS`` table is only a fallback.  The agent CLI
    owns the provider registry and catalog-discovery logic, so ordinary picker
    groups should ask ``hermes_cli.models.provider_model_ids()`` first (#1240).
    Provider aliases are tried as a secondary lookup because WebUI keeps a few
    display-facing IDs (for example ``google`` / ``x-ai``) that Hermes CLI may
    normalize internally.
    """
    import api.config as _ac

    pid = str(provider_id or "").strip()
    if not pid:
        return []
    try:
        from hermes_cli.models import provider_model_ids as _provider_model_ids
    except Exception:
        return []

    candidates = [pid]
    try:
        # Use api.config bridge so tests that monkeypatch _resolve_provider_alias see it.
        _resolve = getattr(_ac, "_resolve_provider_alias", None)
        if callable(_resolve):
            alias = _resolve(pid)
        else:
            from api._cfg.provider_helpers import _resolve_provider_alias as _direct

            alias = _direct(pid)
    except Exception:
        alias = ""
    if alias and alias not in candidates:
        candidates.append(alias)

    seen: set[str] = set()
    for candidate in candidates:
        try:
            live_ids = _provider_model_ids(candidate) or []
        except Exception:
            logger.debug("Failed to load %s models from hermes_cli", candidate)
            continue
        result: list[str] = []
        for mid in live_ids:
            mid_s = str(mid or "").strip()
            if mid_s and mid_s not in seen:
                seen.add(mid_s)
                result.append(mid_s)
        if result:
            return result
    return []


def _hermes_cli_supports_opencode_go_live_catalog() -> bool:
    """Whether the installed Agent has Go-specific model discovery.

    Hermes core versions before 0.20.5 route ``opencode-go`` through a
    generic public catalog. That lookup can return a convincing non-empty
    list containing models the Go relay rejects with 404, so absence or an
    unparseable/prerelease version must fail closed to WebUI's static Go list.
    """
    try:
        import hermes_cli

        version = str(getattr(hermes_cli, "__version__", "")).strip()
    except Exception:
        return False
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", version)
    if not match:
        return False
    return tuple(int(part) for part in match.groups()) >= (0, 20, 5)
