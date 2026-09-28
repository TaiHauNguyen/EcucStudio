"""Persistent user settings (%APPDATA%/EcucStudio/settings.json)."""
from __future__ import annotations

import json
import os

APP_DIR = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "EcucStudio")
CACHE_DIR = os.path.join(os.environ.get("LOCALAPPDATA") or APP_DIR, "EcucStudio", "cache")
SETTINGS_FILE = os.path.join(APP_DIR, "settings.json")

DEFAULTS = {
    "dvcfgcmd": "",
    "recent": [],
    "geometry": "1500x900",
    "plugin_dirs": [],
    "backup_on_save": True,
    "validate_on_load": True,
    "auto_save_before_generate": True,
    "acks": {},        # dpa path -> {key: comment}
    "gen_modules": {}, # dpa path -> [module def paths]
}


class Settings(dict):
    def __init__(self):
        super().__init__(DEFAULTS)
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as fh:
                self.update(json.load(fh))
        except (OSError, ValueError):
            pass

    def save(self):
        os.makedirs(APP_DIR, exist_ok=True)
        tmp = SETTINGS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self, fh, indent=2)
        os.replace(tmp, SETTINGS_FILE)

    def add_recent(self, path: str):
        rec = [p for p in self.get("recent", []) if os.path.normcase(p) != os.path.normcase(path)]
        rec.insert(0, path)
        self["recent"] = rec[:10]

    def acks_for(self, dpa: str) -> dict:
        return self.setdefault("acks", {}).setdefault(os.path.normcase(dpa or "_"), {})
