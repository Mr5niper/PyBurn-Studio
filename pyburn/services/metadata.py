from __future__ import annotations
import subprocess
from typing import Optional, Dict, List
from ..core.tools import ToolFinder
def musicbrainz_lookup(tools: ToolFinder, device: str) -> Optional[Dict]:
    cd_discid = tools.find("cd-discid")
    if not cd_discid:
        return None
    try:
        p = subprocess.run([cd_discid, device], capture_output=True, text=True, timeout=8)
        if p.returncode != 0:
            return None
        parts = (p.stdout or "").strip().split()
        if not parts:
            return None
        discid = parts[0]
    except Exception:
        return None
    try:
        import requests  # optional
    except Exception:
        return None
    try:
        url = f"https://musicbrainz.org/ws/2/discid/{discid}?inc=recordings&fmt=json"
        r = requests.get(url, headers={"User-Agent": "PyBurn/1.0"}, timeout=8)
        if r.status_code != 200:
            return None
        data = r.json()
        title = None
        tracks: List[str] = []
        try:
            releases = data.get("releases", [])
            if releases:
                rel = releases[0]
                title = rel.get("title")
                if rel.get("media"):
                    media = rel["media"][0]
                    for t in media.get("tracks", []):
                        tr = t.get("title")
                        tracks.append(tr or "")
        except Exception:
            pass
        return {"album": title or "Audio CD", "tracks": tracks or []}
    except Exception:
        return None
