"""Startup settings bootstrap extracted from api/config.py.

Re-exported via ``api.config`` (``_startup_settings``).  Runs once at import
time: loads ``settings.json``, reconciles ``DEFAULT_WORKSPACE`` against the
env var, and rewrites settings if stale.  No external module should import from
``api._cfg.startup_settings`` directly.
"""

from __future__ import annotations

import json
import os


def _bootstrap_startup_settings() -> dict:
    """Run the startup settings reconciliation; returns ``_startup_settings``."""
    # Lazy imports: must run after api.config has installed its proxy and
    # populated SETTINGS_FILE / DEFAULT_WORKSPACE.
    import api.config as _ac

    _startup_settings = _ac.load_settings()
    try:
        _settings_file_exists = _ac.SETTINGS_FILE.exists()
    except OSError:
        _settings_file_exists = False
    if _settings_file_exists:
        if not os.getenv("HERMES_WEBUI_DEFAULT_WORKSPACE"):
            resolved = _ac.resolve_default_workspace(
                _startup_settings.get("default_workspace")
            )
            # Update the canonical DEFAULT_WORKSPACE via the proxy so all
            # aliases (config + workspace module) stay coherent.
            _ac.DEFAULT_WORKSPACE = resolved
        _startup_settings.pop("default_model", None)
        if _startup_settings.get("default_workspace") != str(_ac.DEFAULT_WORKSPACE):
            _startup_settings["default_workspace"] = str(_ac.DEFAULT_WORKSPACE)
            try:
                startup_persisted_speech_keys = _ac._extract_persisted_speech_keys(
                    _ac._read_raw_settings_file()
                )
                _ac._atomic_write_settings_text(
                    _ac.SETTINGS_FILE,
                    json.dumps(
                        _ac._settings_payload_for_write(
                            _startup_settings, startup_persisted_speech_keys
                        ),
                        ensure_ascii=False,
                        indent=2,
                    ),
                )
            except Exception:
                pass
    return _startup_settings


_startup_settings: dict = _bootstrap_startup_settings()
