"""Agent directory discovery extracted from api/config.py.

Re-exported via ``api.config`` so ``from api.config import _AGENT_DIR`` keeps
working.  No external module should import from ``api._cfg.discovery`` directly.
"""

import os
import sys
from pathlib import Path

import api.paths as _paths

HOME = _paths.HOME
_REPOS = _paths  # keep linter quiet
_REPOS = _REPOS
REPO_ROOT = Path(__file__).parent.parent.parent.resolve()


def _looks_like_agent_source_root(path: Path) -> bool:
    """Return True when a directory resembles a hermes-agent source root."""
    if (path / "run_agent.py").exists():
        return True
    return _looks_like_pip_style_agent_source_root(path)


def _looks_like_pip_style_agent_source_root(path: Path) -> bool:
    """Return True for pip-style agent roots with a real agent package signal."""
    if not (path / "cron" / "jobs.py").exists():
        return False
    if (path / "hermes").exists():
        return True
    hermes_cli_dir = path / "hermes_cli"
    return (
        (hermes_cli_dir / "__init__.py").exists()
        or (hermes_cli_dir / "main.py").exists()
    )


def _discover_agent_dir() -> Path | None:
    """
    Locate the hermes-agent checkout using a multi-strategy search.

    Priority:
      1. HERMES_WEBUI_AGENT_DIR env var  -- explicit override always wins
      2. HERMES_HOME / hermes-agent      -- e.g. ~/.hermes/hermes-agent
      3. Sibling of this repo            -- ../hermes-agent
      4. Parent of this repo             -- ../../hermes-agent (nested layout)
      5. Common install paths            -- ~/.hermes/hermes-agent (again as fallback)
      6. HOME / hermes-agent             -- ~/hermes-agent (simple flat layout)
    """
    _DEFAULT_HERMES_HOME = _paths._platform_default_hermes_home()
    explicit_override = os.getenv("HERMES_WEBUI_AGENT_DIR")
    if explicit_override:
        explicit_path = Path(explicit_override).expanduser().resolve()
        if explicit_path.exists() and _looks_like_agent_source_root(explicit_path):
            return explicit_path

    candidates = []

    hermes_home = os.getenv("HERMES_HOME", str(_DEFAULT_HERMES_HOME))
    candidates.append(Path(hermes_home).expanduser() / "hermes-agent")
    candidates.append(REPO_ROOT.parent / "hermes-agent")
    if _looks_like_agent_source_root(REPO_ROOT.parent):
        candidates.append(REPO_ROOT.parent)
    candidates.append(_DEFAULT_HERMES_HOME / "hermes-agent")
    candidates.append(HOME / "hermes-agent")
    xdg_data = Path(os.getenv("XDG_DATA_HOME", str(HOME / ".local" / "share")))
    candidates.append(xdg_data.expanduser() / "hermes-agent")
    for sys_prefix in ("/opt", "/usr/local", "/usr/local/share"):
        candidates.append(Path(sys_prefix) / "hermes-agent")

    for path in candidates:
        if path.exists() and (path / "run_agent.py").exists():
            return path.resolve()
    for path in candidates:
        if path.exists() and _looks_like_pip_style_agent_source_root(path):
            return path.resolve()
    return None


def _discover_python(agent_dir: Path | None) -> str:
    """
    Locate a Python executable that has the Hermes agent dependencies installed.

    Priority:
      1. HERMES_WEBUI_PYTHON env var
      2. Agent venv at <agent_dir>/venv/bin/python
      3. Local .venv inside this repo
      4. System python3
    """
    if os.getenv("HERMES_WEBUI_PYTHON"):
        return os.getenv("HERMES_WEBUI_PYTHON")

    if agent_dir:
        venv_py = agent_dir / "venv" / "bin" / "python"
        if venv_py.exists():
            return str(venv_py)
        venv_py = agent_dir / ".venv" / "bin" / "python"
        if venv_py.exists():
            return str(venv_py)
        venv_py_win = agent_dir / "venv" / "Scripts" / "python.exe"
        if venv_py_win.exists():
            return str(venv_py_win)
        venv_py_win = agent_dir / ".venv" / "Scripts" / "python.exe"
        if venv_py_win.exists():
            return str(venv_py_win)

    for subdir, binary in (("bin", "python"), ("Scripts", "python.exe")):
        local_venv = REPO_ROOT / ".venv" / subdir / binary
        if local_venv.exists():
            return str(local_venv)

    import shutil

    for name in ("python3", "python"):
        found = shutil.which(name)
        if found:
            return found
    return "python3"


_AGENT_DIR = _discover_agent_dir()
PYTHON_EXE = _discover_python(_AGENT_DIR)
_HERMES_FOUND = _AGENT_DIR is not None
if _AGENT_DIR is not None and str(_AGENT_DIR) not in sys.path:
    sys.path.append(str(_AGENT_DIR))
