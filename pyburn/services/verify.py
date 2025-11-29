from __future__ import annotations
import hashlib
import subprocess
import time
from pathlib import Path
from typing import Callable, Optional, Set
from .exec import ProcessRunner
from ..core.tools import ToolFinder
class VerificationTools:
    def __init__(self, tools: ToolFinder, runner: ProcessRunner):
        self.tools = tools
        self.runner = runner
    def _sha256(self, p: Path) -> str:
        h = hashlib.sha256()
        with p.open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                if self.runner.cancelled:
                    raise RuntimeError("Verification cancelled")
                h.update(chunk)
        return h.hexdigest()
    def _monitor_file_growth(self, target: Path, total: int, phase_emit: Callable[[int], None], timeout: float = 120.0):
        start = time.time()
        last = -1
        while not self.runner.cancelled:
            if time.time() - start > timeout:
                break
            time.sleep(0.1)
            if not target.exists():
                continue
            try:
                cur = target.stat().st_size
            except Exception:
                cur = 0
            if total > 0:
                pct = int(min(100, max(0, (cur / total) * 100)))
                if pct != last:
                    phase_emit(pct)
                    last = pct
            if cur >= total:
                break
    def verify(self, iso_path: Path, device: str, temp_dir: Path,
               on_status: Callable[[str], None], on_log: Callable[[str], None],
               phase_emit: Callable[[int], None]) -> bool:
        readom = self.tools.find("readom")
        # Level 1: readback
        if readom:
            on_status("Verification: readback (size/hash)...")
            verify_iso = temp_dir / "pyburn_verify.iso"
            ok = False
            try:
                import threading
                t = threading.Thread(target=self._monitor_file_growth, args=(verify_iso, max(1, iso_path.stat().st_size), phase_emit), daemon=True)
                t.start()
                self.runner.run_stream([readom, f"dev={device}", f"f={verify_iso}"], on_stdout=on_log, on_stderr=on_log, check=True)
                t.join(timeout=0.2)
                phase_emit(100)
                if not verify_iso.exists() or verify_iso.stat().st_size < iso_path.stat().st_size:
                    raise RuntimeError("Readback size mismatch")
                if iso_path.stat().st_size < 100 * 1024 * 1024:
                    if self._sha256(iso_path) != self._sha256(verify_iso):
                        raise RuntimeError("Checksum mismatch")
                ok = True
                on_status("Verification OK (readback).")
                return ok
            except Exception as e:
                on_log(f"Readback verify failed ({e}); trying listing compare.")
            finally:
                try: verify_iso.unlink(missing_ok=True)
                except Exception: pass
        # Level 2: isoinfo listing
        isoinfo = self.tools.find("isoinfo")
        if not isoinfo:
            on_status("Warning: Verification tools unavailable.")
            return True
        on_status("Verification: listing compare...")
        try:
            p1 = subprocess.run([isoinfo, "-R", "-f", "-i", str(iso_path)], capture_output=True, text=True, timeout=25)
            p2 = subprocess.run([isoinfo, "-R", "-f", "-i", device], capture_output=True, text=True, timeout=25)
            s1: Set[str] = set((p1.stdout or "").strip().splitlines())
            s2: Set[str] = set((p2.stdout or "").strip().splitlines())
            missing = s1 - s2
            if missing:
                on_status(f"Verification failed: {len(missing)} missing/different entries.")
                return False
            on_status("Verification OK (listing).")
            phase_emit(100)
            return True
        except Exception as e:
            on_status(f"Verification error: {e}")
            return False