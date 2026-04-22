from __future__ import annotations

import os
import sys
from pathlib import Path


def default_config_dir() -> Path:
    home = Path.home()
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", home / "AppData/Roaming"))
    elif sys.platform == "darwin":
        base = home / "Library/Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config"))
    return base / "vocode"


def default_credentials_path() -> Path:
    return default_config_dir() / "credentials.json"
