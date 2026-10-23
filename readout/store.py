"""Settings and reading positions, persisted to ~/.readout/state.json."""

import json
import os
import time
from pathlib import Path

STATE_FILE = Path.home() / ".readout" / "state.json"
MAX_RECENT = 12

DEFAULT_SETTINGS = {
    "rate": 180,
    "volume": 1.0,
    "voice": None,
    "follow": True,
    "skip_margins": False,
    "night": False,
}


def _key(path):
    return os.path.normcase(os.path.abspath(path))


class Store:
    def __init__(self):
        self.data = {"settings": dict(DEFAULT_SETTINGS), "recent": []}
        try:
            saved = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            self.data["settings"].update(saved.get("settings", {}))
            self.data["recent"] = list(saved.get("recent", []))
        except (OSError, ValueError):
            pass

    @property
    def settings(self):
        return self.data["settings"]

    def recent(self):
        return [r for r in self.data["recent"] if os.path.exists(r["path"])]

    def position(self, path):
        for r in self.data["recent"]:
            if _key(r["path"]) == _key(path):
                return r.get("page", 0), r.get("token", 0)
        return None

    def remember(self, path, *, page, token, pages, title):
        entries = [r for r in self.data["recent"] if _key(r["path"]) != _key(path)]
        entries.insert(0, {"path": os.path.abspath(path), "title": title, "page": page,
                           "token": token, "pages": pages, "opened": time.time()})
        self.data["recent"] = entries[:MAX_RECENT]

    def forget(self, path):
        self.data["recent"] = [r for r in self.data["recent"] if _key(r["path"]) != _key(path)]

    def save(self):
        try:
            STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            tmp = STATE_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
            os.replace(tmp, STATE_FILE)
        except OSError:
            pass
