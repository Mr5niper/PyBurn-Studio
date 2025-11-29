from __future__ import annotations
import subprocess
import threading
from typing import Callable, Optional, List
class ProcessRunner:
    def __init__(self):
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._cancelled = False
    def run_stream(
        self,
        args: List[str],
        cwd: Optional[str] = None,
        on_stdout: Optional[Callable[[str], None]] = None,
        on_stderr: Optional[Callable[[str], None]] = None,
        check: bool = True,
    ) -> int:
        with self._lock:
            if self._cancelled:
                return -1
            self._proc = subprocess.Popen(
                args,
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
            proc = self._proc  # capture under lock
        def pump(stream, cb):
            if not stream or not cb:
                return
            for line in iter(stream.readline, ""):
                if self._cancelled:
                    break
                cb(line.rstrip("\n"))
            try:
                stream.close()
            except Exception:
                pass
        t_out = threading.Thread(target=pump, args=(proc.stdout, on_stdout), daemon=True)
        t_err = threading.Thread(target=pump, args=(proc.stderr, on_stderr), daemon=True)
        t_out.start()
        t_err.start()
        code = proc.wait()
        t_out.join(timeout=5)
        t_err.join(timeout=5)
        with self._lock:
            if self._cancelled and proc.poll() is None:
                try:
                    self._proc.terminate()
                except Exception:
                    try:
                        self._proc.kill()
                    except Exception:
                        pass
        if check and code != 0 and not self._cancelled:
            raise subprocess.CalledProcessError(code, args)
        return code
    def cancel(self):
        with self._lock:
            self._cancelled = True
            if self._proc and self._proc.poll() is None:
                try:
                    self._proc.terminate()
                except Exception:
                    try:
                        self._proc.kill()
                    except Exception:
                        pass
    @property
    def cancelled(self) -> bool:
        return self._cancelled
