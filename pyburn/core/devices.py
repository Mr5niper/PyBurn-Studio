from __future__ import annotations
import platform
import re
import subprocess
import shutil
from dataclasses import dataclass
from typing import List
@dataclass
class DeviceInfo:
    id: str
    display: str
class DeviceScanner:
    def scan_devices(self) -> List[DeviceInfo]:
        sysname = platform.system().lower()
        devs: List[DeviceInfo] = []
        wodim = shutil.which("wodim")
        if wodim:
            try:
                p = subprocess.run([wodim, "--devices"], capture_output=True, text=True, timeout=6)
                output = (p.stdout or "") + "\n" + (p.stderr or "")
                for ln in output.splitlines():
                    m = re.search(r"(\d+,\d+,\d+)\s+\d+\)\s+'([^']+)'\s+'([^']+)'", ln)
                    if m:
                        sid = m.group(1)
                        display = f"[{sid}] {m.group(2).strip()} {m.group(3).strip()}"
                        devs.append(DeviceInfo(sid, display))
            except Exception:
                pass
        if not devs:
            cdrecord = shutil.which("cdrecord")
            if cdrecord:
                try:
                    p = subprocess.run([cdrecord, "-scanbus"], capture_output=True, text=True, timeout=6)
                    output = (p.stdout or "") + "\n" + (p.stderr or "")
                    for ln in output.splitlines():
                        m = re.search(r"(\d+,\d+,\d+)\)\s+'([^']+)'\s+'([^']+)'", ln)
                        if m:
                            sid = m.group(1)
                            display = f"[{sid}] {m.group(2).strip()} {m.group(3).strip()}"
                            devs.append(DeviceInfo(sid, display))
                except Exception:
                    pass
        if not devs and sysname == "linux":
            try:
                p = subprocess.run(["lsblk", "-S", "-o", "NAME,TRAN,TYPE,MODEL"], capture_output=True, text=True, timeout=4)
                for ln in (p.stdout or "").splitlines():
                    if "rom" in ln or "cd" in ln.lower():
                        name = ln.split()[0]
                        dp = f"/dev/{name}"
                        devs.append(DeviceInfo(dp, f"{dp} (optical)"))
            except Exception:
                pass
            if not devs:
                devs.append(DeviceInfo("/dev/sr0", "/dev/sr0 (default)"))
        if not devs and sysname == "darwin":
            devs.append(DeviceInfo("/dev/disk2", "/dev/disk2 (default)"))
        if not devs and sysname == "windows":
            devs.append(DeviceInfo("0,0,0", "0,0,0 (default)"))
        return devs