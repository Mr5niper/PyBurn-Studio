from __future__ import annotations
import re
import subprocess
from typing import Optional, Dict, Any, List
from .exec import ProcessRunner
from ..core.tools import ToolFinder
class MediaTools:
    def __init__(self, tools: ToolFinder, runner: ProcessRunner):
        self.tools = tools
        self.runner = runner
    def get_info(self, device: str) -> Dict[str, Optional[Any]]:
        info: Dict[str, Optional[Any]] = {"type": "unknown", "rewritable": None, "blank": None, "speeds": None}
        mediainfo = self.tools.find("dvd+rw-mediainfo")
        if mediainfo and device.startswith("/"):
            try:
                p = subprocess.run([mediainfo, device], capture_output=True, text=True, timeout=8)
                out = (p.stdout or "")
                if "BD" in out: info["type"] = "BD"
                elif "DVD" in out: info["type"] = "DVD"
                elif "CD" in out: info["type"] = "CD"
                m = re.search(r"Disc status:\s*(\w+)", out)
                if m:
                    info["blank"] = (m.group(1).strip().lower() == "blank")
                if "rewritable" in out.lower():
                    info["rewritable"] = True
                elif "write once" in out.lower():
                    info["rewritable"] = False
                speeds = re.findall(r"Write speed #\d+: (\d+)\s*kB/s", out)
                if speeds:
                    factor = 1350 if info["type"] == "DVD" else (4495 if info["type"] == "BD" else 150)
                    xs: List[int] = []
                    for s in speeds:
                        try:
                            kb = int(s)
                            xs.append(max(1, int(round(kb / factor))))
                        except Exception:
                            pass
                    info["speeds"] = sorted(set(xs))
            except Exception:
                pass
        return info
    def resolve_speed(self, requested_speed: Any, device: str) -> int:
        # Accept "Auto" or numeric string/int
        if isinstance(requested_speed, str):
            if requested_speed.lower() != "auto":
                try:
                    requested_speed = int(requested_speed)
                except ValueError:
                    requested_speed = "Auto"
        if isinstance(requested_speed, int) and requested_speed != 0:
            return requested_speed
        info = self.get_info(device)
        speeds = info.get("speeds")
        if speeds and isinstance(speeds, list) and len(speeds) > 0:
            idx = max(0, (len(speeds) // 2) - 1)
            return speeds[idx]
        if info.get("type") == "CD": return 16
        if info.get("type") == "DVD": return 8
        if info.get("type") == "BD": return 4
        return 8
    def blank_media(self, device: str) -> bool:
        fmt = self.tools.find("dvd+rw-format")
        if fmt and device.startswith("/"):
            try:
                self.runner.run_stream([fmt, "-blank=full", device], check=True)
                return True
            except Exception:
                pass
        cdrecord = self.tools.find("cdrecord")
        if cdrecord:
            try:
                self.runner.run_stream([cdrecord, f"dev={device}", "blank=fast"], check=True)
                return True
            except Exception:
                pass
        return False
    def eject(self, device: str) -> None:
        ej = self.tools.find("eject")
        if ej and device.startswith("/"):
            try:
                self.runner.run_stream([ej, device], check=False)
                return
            except Exception:
                pass
        cdrecord = self.tools.find("cdrecord")
        if cdrecord:
            try:
                self.runner.run_stream([cdrecord, f"dev={device}", "-eject"], check=False)
            except Exception:
                pass
