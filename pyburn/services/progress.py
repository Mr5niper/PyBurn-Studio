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

    @staticmethod
    def parse_cdrdao(line: str) -> Optional[int]:
        """Parse real progress out of cdrdao's write output.

        cdrdao reports written data in a few shapes depending on version, e.g.
            "Wrote 123 of 456 MB"
            "Writing track 01 (mode ...): 45 of 90 MB written"
        A ratio line is turned into a percent. A bare trailing percent is used
        as a fallback. Returns None when the line carries no progress, so the
        caller can leave the bar where it is instead of jumping around.
        """
        m = re.search(r"(\d+)\s+of\s+(\d+)\s*MB", line, re.IGNORECASE)
        if m:
            try:
                done = int(m.group(1))
                total = int(m.group(2))
                if total > 0:
                    return ProgressTools._clamp(int((done / total) * 100))
            except Exception:
                return None
        m2 = re.search(r"(\d{1,3})\s*%", line)
        if m2:
            return ProgressTools._clamp(int(m2.group(1)))
        return None
