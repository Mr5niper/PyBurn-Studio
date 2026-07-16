from __future__ import annotations
import glob
import os
import platform
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
        if sysname == "windows":
            return self._scan_windows()
        if sysname == "darwin":
            return self._scan_macos()
        return [DeviceInfo("/dev/sr0", "/dev/sr0 (default)")]

    def _scan_linux(self) -> List[DeviceInfo]:
        devs: List[DeviceInfo] = []
        # Strict: only /dev/sr* (optical) to avoid HDDs/USB sticks
        sr_paths = sorted(glob.glob("/dev/sr*"))
        for path in sr_paths:
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
        try:
            if os.path.islink("/dev/cdrom"):
                real = os.path.realpath("/dev/cdrom")
                if real in sr_paths:
                    devs = [DeviceInfo(real, f"{real} (cdrom)")] + [d for d in devs if d.id != real]
        except Exception:
            pass
        if not devs:
            devs.append(DeviceInfo("/dev/sr0", "/dev/sr0 (default)"))
        return devs

    def _scan_windows(self) -> List[DeviceInfo]:
        devs: List[DeviceInfo] = []
        # Prefer PowerShell CIM (the supported, non-deprecated path on modern
        # Windows). WMIC was removed from Windows 11 24H2, so it is only a
        # legacy fallback now.
        try:
            cmd = [
                "powershell", "-NoProfile", "-Command",
                "Get-CimInstance Win32_CDROMDrive | Select-Object -ExpandProperty Drive"
            ]
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=8)
            for ln in (p.stdout or "").splitlines():
                drv = ln.strip()
                if drv and len(drv) >= 2 and drv[1] == ":":
                    devs.append(DeviceInfo(drv, f"{drv} (optical)"))
        except Exception:
            pass
        if not devs:
            try:
                p = subprocess.run(["wmic", "cdrom", "get", "drive"], capture_output=True, text=True, timeout=5)
                lines = (p.stdout or "").splitlines()
                for ln in lines[1:]:
                    drv = ln.strip()
                    if drv and len(drv) >= 2 and drv[1] == ":":
                        devs.append(DeviceInfo(drv, f"{drv} (optical)"))
            except Exception:
                pass
        if not devs:
            devs.append(DeviceInfo("0,0,0", "0,0,0 (default)"))
        return devs

    def _scan_macos(self) -> List[DeviceInfo]:
        return [DeviceInfo("/dev/disk2", "/dev/disk2 (default)")]
