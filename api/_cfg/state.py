"""State-directory + workspace helpers extracted from api/config.py.

Re-exported via ``api.config`` so ``from api.config import STATE_DIR`` keeps
working.  No external module should import from ``api._cfg.state`` directly.
"""

import os
from pathlib import Path

import api.paths as _paths

HOME = _paths.HOME
_platform_default_hermes_home = _paths._platform_default_hermes_home

_DEFAULT_HERMES_HOME = _platform_default_hermes_home()
_DEFAULT_STATE_HOME = Path(os.getenv("HERMES_HOME") or _DEFAULT_HERMES_HOME).expanduser()

STATE_DIR = (
    Path(os.getenv("HERMES_WEBUI_STATE_DIR", str(_DEFAULT_STATE_HOME / "webui")))
    .expanduser()
    .resolve()
)


def _resolve_settings_file(state_dir: Path) -> Path:
    configured = os.getenv("HERMES_WEBUI_SETTINGS_FILE", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return state_dir / "settings.json"


SESSION_DIR = STATE_DIR / "sessions"
WORKSPACES_FILE = STATE_DIR / "workspaces.json"
SESSION_INDEX_FILE = SESSION_DIR / "_index.json"
SETTINGS_FILE = _resolve_settings_file(STATE_DIR)
LAST_WORKSPACE_FILE = STATE_DIR / "last_workspace.txt"
PROJECTS_FILE = STATE_DIR / "projects.json"


# Keep custom provider /v1/models probes below the frontend's generic request
# timeout even when one upstream is slow or unreachable.
CUSTOM_MODELS_ENDPOINT_TIMEOUT_SECONDS = 5.0
