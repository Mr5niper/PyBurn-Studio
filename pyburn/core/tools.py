from __future__ import annotations
import os
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

    def __init__(self, extra_dirs: Optional[List[str]] = None):
        self._resolved: Dict[str, Optional[str]] = {}
        # Directories searched IN ADDITION to PATH, e.g. the local tools\ folder
        # that ffmpeg is downloaded into. Checked before PATH so a downloaded
        # copy wins over a stale system one.
        self._extra_dirs: List[str] = list(extra_dirs or [])

    def add_search_dir(self, directory: str) -> None:
        """Add a directory to search for tools, and clear the resolve cache."""
        if directory and directory not in self._extra_dirs:
            self._extra_dirs.append(directory)
            self._resolved.clear()

    def _which_in_extra(self, exe: str) -> Optional[str]:
        for d in self._extra_dirs:
            if not d or not os.path.isdir(d):
                continue
            # Try the name and common Windows executable extensions.
            for cand in (exe, exe + ".exe"):
                full = os.path.join(d, cand)
                if os.path.isfile(full) and os.access(full, os.X_OK) or os.path.isfile(full):
                    return full
            # Also look one level down (archive extractions often nest in bin/).
            for sub in ("bin",):
                for cand in (exe, exe + ".exe"):
                    full = os.path.join(d, sub, cand)
                    if os.path.isfile(full):
                        return full
        return None

    def find(self, logical_name: str) -> Optional[str]:
        if logical_name in self._resolved:
            return self._resolved[logical_name]
        for exe in self.TOOL_CANDIDATES.get(logical_name, [logical_name]):
            # Local tools\ folder first, then PATH.
            path = self._which_in_extra(exe) or shutil.which(exe)
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
