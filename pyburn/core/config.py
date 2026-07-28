from __future__ import annotations
import json
import os
import sys
import platform
from pathlib import Path
from typing import Any, Dict, List

# Settings that are specific to a single optical drive. Each discovered drive
# gets its own copy of these, edited per-drive in the Settings window.
PER_DRIVE_DEFAULTS: Dict[str, Any] = {
    "burn_speed": "Auto",
    "verify_after_burn": True,
    "auto_blank_rw": True,
    "eject_after_burn": True,
}

# Application-wide settings that are not tied to a particular drive.
GLOBAL_DEFAULTS: Dict[str, Any] = {
    "temp_dir": str(Path.home() / "PyBurn_Temp"),
    "audio_format": "MP3",
    "audio_bitrate": 320,
    "video_format": "MPEG2",
    "default_device": None,
    "simulate_when_missing_tools": True,
    "musicbrainz_enabled": True,
    "history_file": str(Path.home() / ".pyburn_history.json"),
    "logs_dir": str(Path.home() / ".pyburn_logs"),
    "setup_completed": False,
}


def _config_path() -> Path:
    """Location of pyburn_studio.config: next to the executable when frozen,
    otherwise next to pyburn_studio.py (the project root). This file is
    PC-specific (it records the drives found on this machine and their per-drive
    settings), so it is created by the program at runtime and excluded from the
    repository."""
    try:
        if getattr(sys, "frozen", False):
            base = Path(sys.executable).resolve().parent
        else:
            # pyburn/core/config.py -> project root is two parents up.
            base = Path(__file__).resolve().parent.parent.parent
        return base / "pyburn_studio.config"
    except Exception:
        return Path.home() / "pyburn_studio.config"


class Config:
    """Single source of truth for all settings, backed by pyburn_studio.config.

    The public surface intentionally keeps `settings` as a flat dict of GLOBAL
    settings so existing callers that read cfg.settings["temp_dir"] etc. keep
    working. Per-drive settings are accessed through drive_setting()/
    set_drive_setting()/drive_options().
    """

    def __init__(self, path: Path | None = None):
        self.path = path or _config_path()
        self.settings: Dict[str, Any] = {}   # global settings (flat, as before)
        self.drives: Dict[str, Dict[str, Any]] = {}  # per-drive settings by id
        self.load()

    # -- load / save ----------------------------------------------------------
    def load(self):
        self.settings = dict(GLOBAL_DEFAULTS)
        self.drives = {}
        data = {}
        if self.path.exists():
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                data = {}
        if isinstance(data, dict):
            # New format: {"global": {...}, "drives": {...}}.
            if "global" in data or "drives" in data:
                g = data.get("global")
                if isinstance(g, dict):
                    self.settings.update(g)
                d = data.get("drives")
                if isinstance(d, dict):
                    for k, v in d.items():
                        if isinstance(v, dict):
                            merged = dict(PER_DRIVE_DEFAULTS)
                            merged.update(v)
                            self.drives[k] = merged
            else:
                # Legacy flat format: pull known globals, and seed per-drive
                # defaults for the old default_device from any per-drive keys.
                for key in GLOBAL_DEFAULTS:
                    if key in data:
                        self.settings[key] = data[key]
                legacy_drive = {}
                for key in PER_DRIVE_DEFAULTS:
                    if key in data:
                        legacy_drive[key] = data[key]
                dev = data.get("default_device")
                if dev:
                    merged = dict(PER_DRIVE_DEFAULTS)
                    merged.update(legacy_drive)
                    self.drives[dev] = merged

        # Ensure a default device exists.
        if not self.settings.get("default_device"):
            try:
                from .devices import get_devices
                devs = get_devices()
                self.settings["default_device"] = devs[0].id if devs else self._fallback_device()
            except Exception:
                self.settings["default_device"] = self._fallback_device()

        # Make sure every currently-attached drive has a settings entry, seeded
        # with defaults. This is what "fills out the config when drives are
        # discovered" means.
        try:
            self.ensure_drives_registered()
        except Exception:
            pass

        try:
            Path(self.settings["temp_dir"]).mkdir(parents=True, exist_ok=True)
            Path(self.settings["logs_dir"]).mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

        # Persist immediately so the file exists on first run, seeded with the
        # discovered drives and defaults.
        self.save()

    def _fallback_device(self) -> str:
        return "0,0,0" if platform.system().lower() == "windows" else "/dev/sr0"

    def ensure_drives_registered(self, devices: List[str] | None = None):
        """Seed a per-drive settings block (with defaults) for each given drive
        id, or for each currently-detected drive if none are given. Existing
        entries are left as-is."""
        ids = devices
        if ids is None:
            try:
                from .devices import get_devices
                ids = [d.id for d in get_devices()]
            except Exception:
                ids = []
        cur = self.settings.get("default_device")
        if cur and cur not in ids:
            ids = list(ids) + [cur]
        changed = False
        for did in ids:
            if did and did not in self.drives:
                self.drives[did] = dict(PER_DRIVE_DEFAULTS)
                changed = True
        return changed

    def save(self):
        try:
            Path(self.settings["temp_dir"]).mkdir(parents=True, exist_ok=True)
            Path(self.settings["logs_dir"]).mkdir(parents=True, exist_ok=True)
        except Exception:
            self.settings["temp_dir"] = str(Path.home() / "PyBurn_Temp")
            try:
                Path(self.settings["temp_dir"]).mkdir(parents=True, exist_ok=True)
            except Exception:
                pass
        payload = {"global": self.settings, "drives": self.drives}
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
        except Exception:
            pass

    # -- per-drive accessors --------------------------------------------------
    def drive_options(self, device: str) -> Dict[str, Any]:
        """All per-drive settings for a device, defaulted if the drive has no
        stored block yet."""
        block = self.drives.get(device)
        merged = dict(PER_DRIVE_DEFAULTS)
        if isinstance(block, dict):
            merged.update(block)
        return merged

    def drive_setting(self, device: str, key: str, default: Any = None) -> Any:
        opts = self.drive_options(device)
        if key in opts:
            return opts[key]
        return default if default is not None else PER_DRIVE_DEFAULTS.get(key)

    def set_drive_setting(self, device: str, key: str, value: Any):
        if device not in self.drives:
            self.drives[device] = dict(PER_DRIVE_DEFAULTS)
        self.drives[device][key] = value
