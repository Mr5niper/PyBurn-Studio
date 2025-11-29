from __future__ import annotations
import shutil
import subprocess
from typing import Dict, Optional, List
class ToolFinder:
    TOOL_CANDIDATES: Dict[str, List[str]] = {
        "mkisofs": ["mkisofs", "genisoimage"],
        "xorriso": ["xorriso"],
        "cdrecord": ["cdrecord", "wodim"],
        "growisofs": ["growisofs"],
        "cdrdao": ["cdrdao"],
        "ffmpeg": ["ffmpeg"],
        "ffprobe": ["ffprobe"],
        "cdparanoia": ["cdparanoia"],
        "lame": ["lame"],
        "flac": ["flac"],
        "dvdauthor": ["dvdauthor"],
        "isoinfo": ["isoinfo"],
        "readom": ["readom", "readcd"],
        "dvd+rw-mediainfo": ["dvd+rw-mediainfo"],
        "dvd+rw-format": ["dvd+rw-format"],
        "eject": ["eject"],
        "cd-discid": ["cd-discid"],
        "tsMuxeR": ["tsMuxeR", "tsmuxer"],
    }
    def __init__(self):
        self._resolved: Dict[str, Optional[str]] = {}
    def find(self, logical_name: str) -> Optional[str]:
        if logical_name in self._resolved:
            return self._resolved[logical_name]
        for exe in self.TOOL_CANDIDATES.get(logical_name, [logical_name]):
            path = shutil.which(exe)
            if path:
                self._resolved[logical_name] = path
                return path
        self._resolved[logical_name] = None
        return None
    def require(self, logical_name: str) -> str:
        exe = self.find(logical_name)
        if not exe:
            raise FileNotFoundError(f"Required tool '{logical_name}' not found")
        return exe
    def missing(self, logical_names: List[str]) -> List[str]:
        return [n for n in logical_names if not self.find(n)]
    def versions(self) -> Dict[str, Optional[str]]:
        v: Dict[str, Optional[str]] = {}
        for name in self.TOOL_CANDIDATES.keys():
            exe = self.find(name)
            if not exe:
                v[name] = None
            else:
                try:
                    proc = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=3)
                    out = (proc.stdout or proc.stderr or "").strip()
                    v[name] = (out.splitlines()[0] if out else "present")
                except Exception:
                    v[name] = "present"
        return v
