"""Workspace discovery extracted from api/config.py.

Re-exported via ``api.config`` (see ``api/config.py`` shim).
Depends on ``api._cfg.state`` for STATE_DIR/HOME.
No external module should import from ``api._cfg.workspace`` directly.
"""

import os
from pathlib import Path

import api._cfg.state as _state_mod

# HOME/STATE_DIR must reflect monkeypatch on api.config (tests do
# monkeypatch.setattr(api.config, "STATE_DIR", ...)). Importing directly
# from api._cfg.state freezes the value, so we proxy at call time via
# api.config when available.  Module-level aliases are kept for
# ``from api._cfg.workspace import HOME`` but internal logic uses helpers.
from api._cfg.state import HOME as _HOME_FALLBACK  # noqa: F401
from api._cfg.state import STATE_DIR as _STATE_DIR_FALLBACK  # noqa: F401

HOME = _HOME_FALLBACK
STATE_DIR = _STATE_DIR_FALLBACK


def _home() -> Path:
    try:
        import api.config as _ac  # type: ignore[import-not-found]

        return _ac.HOME  # type: ignore[attr-defined]
    except Exception:
        return _state_mod.HOME


def _state_dir() -> Path:
    try:
        import api.config as _ac  # type: ignore[import-not-found]

        return _ac.STATE_DIR  # type: ignore[attr-defined]
    except Exception:
        return _state_mod.STATE_DIR


def _workspace_candidates(raw: str | Path | None = None) -> list[Path]:
    """Return ordered candidate workspace paths, de-duplicated."""
    candidates: list[Path] = []

    def add(candidate: str | Path | None) -> None:
        if candidate in (None, ""):
            return
        try:
            path = Path(candidate).expanduser().resolve()
        except Exception:
            return
        if path not in candidates:
            candidates.append(path)

    add(raw)
    if os.getenv("HERMES_WEBUI_DEFAULT_WORKSPACE"):
        add(os.getenv("HERMES_WEBUI_DEFAULT_WORKSPACE"))

    home_workspace = _home() / "workspace"
    home_work = _home() / "work"
    if home_workspace.exists():
        add(home_workspace)
    if home_work.exists():
        add(home_work)

    add(home_workspace)
    add(_state_dir() / "workspace")
    return candidates


def _ensure_workspace_dir(path: Path) -> bool:
    """Best-effort check that a workspace directory exists and is writable."""
    try:
        path = path.expanduser().resolve()
        path.mkdir(parents=True, exist_ok=True)
        return path.is_dir() and os.access(path, os.R_OK | os.W_OK | os.X_OK)
    except Exception:
        return False


def resolve_default_workspace(raw: str | Path | None = None) -> Path:
    """Return the first usable workspace path, creating it when possible."""
    for candidate in _workspace_candidates(raw):
        if _ensure_workspace_dir(candidate):
            return candidate
    raise RuntimeError(
        "Could not create or access any usable workspace directory. "
        "Set HERMES_WEBUI_DEFAULT_WORKSPACE to a writable path."
    )


def _discover_default_workspace() -> Path:
    """
    Resolve the default workspace in order:
      1. HERMES_WEBUI_DEFAULT_WORKSPACE env var
      2. ~/workspace if it already exists
      3. ~/work if it already exists
      4. ~/workspace (create if needed)
      5. STATE_DIR / workspace
    """
    return resolve_default_workspace()


DEFAULT_WORKSPACE = _discover_default_workspace()
DEFAULT_MODEL = os.getenv("HERMES_WEBUI_DEFAULT_MODEL", "")
