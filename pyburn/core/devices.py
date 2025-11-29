from __future__ import annotations
import os
import platform
import re
import subprocess
from dataclasses import dataclass
from typing import List

@dataclass
class DeviceInfo:
    id: str
    display: str

class DeviceScanner:
    def scan_devices(self) -> List[DeviceInfo]:
        sysname = platform.system().lower()
        if sysname == "linux":
            return self._scan_linux()
        if sysname == "darwin":
            return self._scan_macos()
        if sysname == "windows":
            return self._scan_windows()
        # unknown OS fallback
        return [DeviceInfo("/dev/sr0", "/dev/sr0 (default)")]

    def _scan_linux(self) -> List[DeviceInfo]:
        devs: List[DeviceInfo] = []

        # 1) Best: lsblk (JSON) by bus, type, vendor/model
        try:
            p = subprocess.run(
                ["lsblk", "-S", "-J", "-o", "NAME,TRAN,TYPE,MODEL,VENDOR"],
                capture_output=True, text=True, timeout=5
            )
            if p.stdout:
                import json
                data = json.loads(p.stdout)
                for d in data.get("blockdevices", []):
                    if str(d.get("type", "")).lower() == "rom":
                        name = d.get("name")
                        tran = (d.get("tran") or "").upper()
                        model = (d.get("model") or "").strip()
                        vendor = (d.get("vendor") or "").strip()
                        path = f"/dev/{name}"
                        if os.path.exists(path):
                            tag = f"[{tran}] " if tran else ""
                            display = f"{path} {tag}{vendor} {model}".strip()
                            devs.append(DeviceInfo(path, display))
        except Exception:
            pass

        # 2) Fallback: enumerate /dev/sr*
        try:
            import glob
            for path in sorted(glob.glob("/dev/sr*")):
                if any(x.id == path for x in devs):
                    continue
                base = os.path.basename(path)
                vendor = ""
                model = ""
                tran = ""
                try:
                    with open(f"/sys/class/block/{base}/device/vendor", "r", encoding="utf-8") as f:
                        vendor = f.read().strip()
                except Exception:
                    pass
                try:
                    with open(f"/sys/class/block/{base}/device/model", "r", encoding="utf-8") as f:
                        model = f.read().strip()
                except Exception:
                    pass
                try:
                    p = subprocess.run(["lsblk", "-no", "TRAN", path], capture_output=True, text=True, timeout=2)
                    tran = (p.stdout or "").strip().upper()
                except Exception:
                    pass
                tag = f"[{tran}] " if tran else ""
                display = f"{path} {tag}{vendor} {model}".strip()
                devs.append(DeviceInfo(path, display))
        except Exception:
            pass

        # 3) Add /dev/cdrom (symlink) first if it points to a real sr*
        try:
            if os.path.exists("/dev/cdrom"):
                real = os.path.realpath("/dev/cdrom")
                if any(d.id == real for d in devs):
                    # Prefer cdrom-promoted one at top
                    devs = [DeviceInfo(real, f"{real} (cdrom)")] + [d for d in devs if d.id != real]
        except Exception:
            pass

        # Ensure we have at least a default
        if not devs:
            devs.append(DeviceInfo("/dev/sr0", "/dev/sr0 (default)"))

        # Important: do NOT inject SCSI "0,0,0" IDs on Linux anymore.
        return devs

    def _scan_macos(self) -> List[DeviceInfo]:
        # Basic default; a more advanced version could parse `drutil list` or `diskutil list`
        return [DeviceInfo("/dev/disk2", "/dev/disk2 (default)")]

    def _scan_windows(self) -> List[DeviceInfo]:
        # Minimal: commonly optical drives are D:, E:, ...
        # A more robust approach could query WMI for CDROM drives, but we keep this simple.
        letters = [f"{chr(c)}:" for c in range(ord("D"), ord("Z") + 1)]
        devs: List[DeviceInfo] = []
        for d in letters:
            try:
                if os.path.exists(d + "\\"):
                    devs.append(DeviceInfo(d, f"{d} (optical)"))
            except Exception:
                pass
        if not devs:
            devs.append(DeviceInfo("0,0,0", "0,0,0 (default)"))
        return devs
