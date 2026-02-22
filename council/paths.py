from __future__ import annotations

import os
import sys
from pathlib import Path


APP_NAME = "council"
COUNCIL_HOME_ENV_VAR = "COUNCIL_HOME"


def get_council_home(create: bool = False) -> Path:
    override = os.getenv(COUNCIL_HOME_ENV_VAR, "").strip()
    if override:
        home = Path(override).expanduser()
    else:
        home = _default_council_home()

    if create:
        home.mkdir(parents=True, exist_ok=True)
    return home


def get_tui_state_file_path() -> Path:
    return get_council_home(create=False) / "tui_state.json"


def get_council_db_dir(create: bool = False) -> Path:
    db_dir = get_council_home(create=create) / "db"
    if create:
        db_dir.mkdir(parents=True, exist_ok=True)
    return db_dir


def get_run_history_db_path() -> Path:
    return get_council_db_dir(create=False) / "history.sqlite3"


def get_council_log_path() -> Path:
    return get_council_home(create=False) / "council.log"


def get_user_flow_config_path() -> Path:
    return get_council_home(create=False) / "flow.json"


def _default_council_home() -> Path:
    if sys.platform == "win32":
        app_data = os.getenv("APPDATA", "")
        if app_data:
            return Path(app_data) / APP_NAME
        return Path.home() / "AppData" / "Roaming" / APP_NAME

    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME

    xdg_config_home = os.getenv("XDG_CONFIG_HOME", "").strip()
    if xdg_config_home:
        return Path(xdg_config_home).expanduser() / APP_NAME
    return Path.home() / ".config" / APP_NAME
