"""Environment-variable helpers extracted from api/config.py.

Public surface re-exported via ``api.config`` (see ``api/config.py`` shim).
No external module should import from ``api._cfg.env`` directly.
"""

import logging
import os
import re

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int, *, minimum: int = 1) -> int:
    """Read a positive int from the environment, falling back on bad input.

    Used for operator-tunable memory caps (issue #3506) so large installs can
    shrink the agent/session caches without editing source. A missing, empty,
    non-numeric, or below-``minimum`` value falls back to ``default`` so a typo
    can never disable a cache bound entirely.
    """
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return default
    return value if value >= minimum else default


def _env_int_clamped(name: str, default: int, *, minimum: int = 1, maximum: int) -> int:
    """Like ``_env_int``, then clamp a valid override to ``maximum``."""
    value = _env_int(name, default, minimum=minimum)
    if not str(os.getenv(name) or "").strip():
        return value
    return min(value, maximum)


def _env_mb_bytes(name: str, default_mb: int) -> int:
    """Parse an optional megabyte environment variable into bytes.

    Accepts values like ``200``, ``200MB``, or ``200MiB``. Invalid or
    non-positive values fall back to the provided default.
    """
    raw = os.getenv(name, "").strip()
    if not raw:
        return default_mb * 1024 * 1024
    m = re.match(r"^(\d+)\s*(?:m|mb|mib)?$", raw, re.IGNORECASE)
    if not m:
        logger.warning(
            "Invalid %s=%r; expected a positive integer in MB. Falling back to %sMB.",
            name,
            raw,
            default_mb,
        )
        return default_mb * 1024 * 1024
    value_mb = int(m.group(1))
    if value_mb <= 0:
        logger.warning(
            "Invalid %s=%r; expected a value greater than zero. Falling back to %sMB.",
            name,
            raw,
            default_mb,
        )
        return default_mb * 1024 * 1024
    return value_mb * 1024 * 1024
