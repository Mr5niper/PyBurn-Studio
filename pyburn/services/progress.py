from __future__ import annotations
import re
from typing import Optional
class ProgressTools:
    @staticmethod
    def _clamp(v: Optional[int]) -> Optional[int]:
        if v is None:
            return None
        return max(0, min(100, v))
    @staticmethod
    def parse_cdrecord(line: str) -> Optional[int]:
        m = re.search(r"(\d{1,3})%\s*(?:done|written)", line)
        if m:
            return ProgressTools._clamp(int(m.group(1)))
        m2 = re.search(r"\bbuf(?:fer)?\s*\[?\s*(\d{1,3})\s*%?\]?", line)
        if m2:
            return ProgressTools._clamp(int(m2.group(1)))
        return None
    @staticmethod
    def parse_growisofs(line: str) -> Optional[int]:
        m = re.search(r"(\d+(?:\.\d+)?)%\s*done", line, re.IGNORECASE)
        if m:
            try:
                return ProgressTools._clamp(int(float(m.group(1))))
            except Exception:
                return None
        return None
    @staticmethod
    def parse_cdparanoia(line: str) -> Optional[int]:
        m = re.search(r"(\d{1,3})\s*%", line)
        if m:
            return ProgressTools._clamp(int(m.group(1)))
        return None
