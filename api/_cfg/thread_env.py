"""Thread-local profile env helpers extracted from api/config.py.

Re-exported via ``api.config`` so existing imports keep working.
No external module should import from ``api._cfg.thread_env`` directly.
"""

import os
import re
import threading

_thread_ctx = threading.local()


def _thread_local_env_value(name: str, default: str = "") -> str:
    """Return thread-local profile env first, then process env, for provider reads."""
    env_name = str(name or "").strip()
    if not env_name:
        return default or ""

    thread_env = getattr(_thread_ctx, "env", {})
    if isinstance(thread_env, dict) and env_name in thread_env:
        thread_value = thread_env.get(env_name)
        if thread_value is None:
            return default or ""
        return str(thread_value)

    if bool(getattr(_thread_ctx, "block_process_env_fallback", False)):
        return default or ""

    return str(os.getenv(env_name, default or ""))


def _expand_env_vars(obj):
    """Recursively expand ${VAR} references in config values.

    Uses the thread-local-first profile env lookup (_thread_local_env_value) so a
    ${VAR} reference in a profile's config.yaml resolves to that profile's value,
    and does NOT fall back to the server process os.environ when a
    profile-scoped readonly/background scope set block_process_env_fallback.
    """
    if isinstance(obj, str):
        return re.sub(
            r"\${([^}]+)}",
            lambda m: _thread_local_env_value(m.group(1), m.group(0)),
            obj,
        )
    if isinstance(obj, dict):
        return {k: _expand_env_vars(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_expand_env_vars(item) for item in obj]
    return obj


def _set_thread_env(**kwargs):
    _thread_ctx.env = kwargs


def _clear_thread_env():
    _thread_ctx.env = {}
