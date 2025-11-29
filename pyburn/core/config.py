from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Dict
DEFAULT_CONFIG = {
    "burn_speed": "Auto",
    "verify_after_burn": True,
    "temp_dir": str(Path.home() / "PyBurn_Temp"),
    "audio_format": "MP3",
    "audio_bitrate": 320,
    "video_format": "MPEG2",
    "default_device": None,
    "simulate_when_missing_tools": True,
    "auto_blank_rw": True,
    "eject_after_burn": True,
    "history_file": str(Path.home() / ".pyburn_history.json"),
    "logs_dir": str(Path.home() / ".pyburn_logs"),
    "musicbrainz_enabled": True,
}
class Config:
    def __init__(self, path: Path | None = None):
        self.path = path or Path.home() / ".pyburn_config.json"
        self.settings: Dict[str, Any] = {}
        self.load()
    def load(self):
        self.settings = dict(DEFAULT_CONFIG)
        if self.path.exists():
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    self.settings.update(data)
            except Exception:
                pass
        if not self.settings.get("default_device"):
            try:
                from .devices import DeviceScanner
                devs = DeviceScanner().scan_devices()
                self.settings["default_device"] = devs[0].id if devs else "/dev/sr0"
            except Exception:
                self.settings["default_device"] = "/dev/sr0"
        Path(self.settings["temp_dir"]).mkdir(parents=True, exist_ok=True)
        Path(self.settings["logs_dir"]).mkdir(parents=True, exist_ok=True)
    def save(self):
        try:
            Path(self.settings["temp_dir"]).mkdir(parents=True, exist_ok=True)
            Path(self.settings["logs_dir"]).mkdir(parents=True, exist_ok=True)
        except Exception:
            self.settings["temp_dir"] = str(Path.home() / "PyBurn_Temp")
            Path(self.settings["temp_dir"]).mkdir(parents=True, exist_ok=True)
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.settings, f, indent=2)
        except Exception:
            pass