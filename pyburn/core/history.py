from __future__ import annotations
import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import List, Dict, Any, Optional
@dataclass
class HistoryEntry:
    id: str
    job_type: str
    device: str
    files: List[str]
    options: Dict[str, Any]
    created_at: str
    finished_at: str
    success: bool
    message: str
    log_file: Optional[str] = None
class HistoryStore:
    def __init__(self, history_path: Path, logs_dir: Path):
        self.history_path = history_path
        self.logs_dir = logs_dir
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self._entries: List[HistoryEntry] = []
        self._load()
    def _load(self):
        if self.history_path.exists():
            try:
                data = json.loads(self.history_path.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    self._entries = [HistoryEntry(**e) for e in data]
            except Exception:
                self._entries = []
    def add(self, entry: HistoryEntry):
        self._entries.append(entry)
        self._save()
    def _save(self):
        try:
            self.history_path.write_text(json.dumps([asdict(e) for e in self._entries], indent=2), encoding="utf-8")
        except Exception:
            pass
    def all(self) -> List[HistoryEntry]:
        return list(self._entries)
    def find(self, job_id: str) -> Optional[HistoryEntry]:
        for e in self._entries:
            if e.id == job_id: return e
        return None