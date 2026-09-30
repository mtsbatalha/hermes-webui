"""Startup diagnostics extracted from api/config.py.

Re-exported via ``api.config`` so ``from api.config import
print_startup_config`` keeps working.  No external module should import
from ``api._cfg.startup`` directly.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def _warn_state_dir_divergence(warn_prefix: str) -> None:
    """Check if SESSION_DIR is empty but a sibling state directory has session data."""
    import api.config as _ac

    try:
        session_dir_empty = False
        if _ac.SESSION_DIR.exists():
            json_files = [f for f in _ac.SESSION_DIR.glob("*.json") if f.name != "_index.json"]
            session_dir_empty = len(json_files) == 0
        else:
            session_dir_empty = True

        index_file_empty = True
        if _ac.SESSION_INDEX_FILE.exists():
            try:
                with open(_ac.SESSION_INDEX_FILE, "r") as f:
                    content = f.read().strip()
                    if content and content not in ("{}", "[]", "null"):
                        index_file_empty = False
            except Exception:
                pass

        if session_dir_empty and index_file_empty:
            state_parent = _ac.STATE_DIR.parent
            if state_parent.exists():
                for sibling in state_parent.iterdir():
                    if not sibling.is_dir() or sibling == _ac.STATE_DIR:
                        continue
                    sibling_sessions = sibling / "sessions"
                    if sibling_sessions.exists():
                        json_files = [f for f in sibling_sessions.glob("*.json") if f.name != "_index.json"]
                        if json_files:
                            print(
                                f"{warn_prefix}  STATE_DIR is empty but a sibling state directory has session data.\n"
                                f"        Current : {_ac.STATE_DIR}\n"
                                f"        Sibling : {sibling}\n"
                                f"        If you switched launch methods (bootstrap.py / ctl.sh / systemd),\n"
                                f"        the active HERMES_WEBUI_STATE_DIR env var may differ from the\n"
                                f"        previous run. Set it explicitly to restore access:\n"
                                f"          export HERMES_WEBUI_STATE_DIR={sibling}",
                                flush=True,
                            )
                            return
    except Exception:
        pass


def print_startup_config() -> None:
    """Print detected configuration at startup so the user can verify what was found."""
    import api.config as _ac

    ok = "\033[32m[ok]\033[0m"
    warn = "\033[33m[!!]\033[0m"
    err = "\033[31m[XX]\033[0m"

    lines = [
        "",
        "  Hermes Web UI -- startup config",
        "  --------------------------------",
        f"  repo root   : {_ac.REPO_ROOT}",
        f"  agent dir   : {_ac._AGENT_DIR if _ac._AGENT_DIR else 'NOT FOUND'}  {ok if _ac._AGENT_DIR else err}",
        f"  python      : {_ac.PYTHON_EXE}",
        f"  state dir   : {_ac.STATE_DIR}",
        f"  workspace   : {_ac.DEFAULT_WORKSPACE}",
        f"  host:port   : {_ac.HOST}:{_ac.PORT}",
        f"  config file : {_ac._get_config_path()}  {'(found)' if _ac._get_config_path().exists() else '(not found, using defaults)'}",
        "",
    ]
    print("\n".join(lines), flush=True)

    try:
        _warn_state_dir_divergence(warn)
    except Exception:
        pass

    if not _ac._HERMES_FOUND:
        print(
            f"{err}  Could not find the Hermes agent directory.\n"
            "      The server will start but agent features will not work.\n"
            "\n"
            "      To fix, set one of:\n"
            "        export HERMES_WEBUI_AGENT_DIR=/path/to/hermes-agent\n"
            "        export HERMES_HOME=/path/to/.hermes\n"
            "\n"
            "      Or clone hermes-agent as a sibling of this repo:\n"
            "        git clone <hermes-agent-repo> ../hermes-agent\n",
            flush=True,
        )


def verify_hermes_imports() -> tuple:
    """
    Attempt to import the key Hermes modules.
    Returns (ok: bool, missing: list[str], errors: dict[str, str]).
    """
    required = ["run_agent"]
    missing = []
    errors = {}
    for mod in required:
        try:
            __import__(mod)
        except Exception as e:
            missing.append(mod)
            errors[mod] = f"{type(e).__name__}: {e}"
    return (len(missing) == 0), missing, errors
