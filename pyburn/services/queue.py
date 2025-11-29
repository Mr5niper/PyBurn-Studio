from __future__ import annotations
from PyQt6.QtCore import QObject, pyqtSignal, QThread
from typing import List, Optional
from ..core.jobs import Job, JobType, JobOptions
from ..core.tools import ToolFinder
from ..core.history import HistoryStore, HistoryEntry
from .burn import BurnWorker
from datetime import datetime
from pathlib import Path
class JobQueueService(QObject):
    sig_queue_updated = pyqtSignal()
    sig_job_started = pyqtSignal(str)
    sig_status_update = pyqtSignal(str, str, int)
    sig_log_line = pyqtSignal(str, str)
    sig_job_finished = pyqtSignal(str, bool, str)
    def __init__(self, tools: ToolFinder, settings: dict):
        super().__init__()
        self.tools = tools
        self.settings = settings
        self._queue: List[Job] = []
        self._thread: Optional[QThread] = None
        self._worker: Optional[BurnWorker] = None
        self._current: Optional[Job] = None
        self.history = HistoryStore(Path(settings.get("history_file")), Path(settings.get("logs_dir")))
        self._log_lines: List[str] = []
    def enqueue(self, job: Job):
        self._queue.append(job)
        self.sig_queue_updated.emit()
        if not self._thread or not self._thread.isRunning():
            self._start_next()
    def remove(self, job_id: str):
        if self._current and self._current.id == job_id:
            return
        old_len = len(self._queue)
        self._queue = [j for j in self._queue if j.id != job_id]
        if len(self._queue) < old_len:
            self.sig_queue_updated.emit()
            if not self._current:
                self._start_next()
    def cancel_current(self):
        if self._worker:
            self._worker.cancel()
    def retry(self, entry: HistoryEntry):
        opts = entry.options
        job = Job(
            job_type=JobType(entry.job_type),
            files=[Path(p) for p in entry.files],
            device=entry.device,
            options=JobOptions(
                temp_dir=Path(opts.get("temp_dir", self.settings.get("temp_dir"))),
                verify=bool(opts.get("verify", False)),
                speed=opts.get("speed", "Auto"),
                volume_label=opts.get("volume_label", "DATA_DISC"),
                output_dir=Path(opts["output_dir"]) if opts.get("output_dir") else None,
                rip_format=opts.get("rip_format", "MP3"),
                rip_bitrate=int(opts.get("rip_bitrate", 320)),
                auto_blank=bool(opts.get("auto_blank", True)),
                eject_after=bool(opts.get("eject_after", True)),
                dummy=bool(opts.get("dummy", False)),
                album_title=opts.get("album_title"),
                album_performer=opts.get("album_performer"),
                track_titles=opts.get("track_titles"),
                track_performers=opts.get("track_performers"),
            ),
        )
        self.enqueue(job)
    def get_list(self) -> List[Job]:
        lst: List[Job] = []
        if self._current:
            lst.append(self._current)
        lst.extend(self._queue)
        return lst
    def _start_next(self):
        if self._current or not self._queue:
            return
        job = self._queue.pop(0)
        self._current = job
        job.status = "RUNNING"
        job.progress = 0
        self._log_lines = []
        self._worker = BurnWorker(job, self.tools, simulate_if_missing=self.settings.get("simulate_when_missing_tools", True))
        self._thread = QThread()
        self._worker.moveToThread(self._thread)
        self._worker.sig_status.connect(lambda s: self._status(job.id, s))
        self._worker.sig_progress.connect(lambda p: self._progress(job.id, p))
        self._worker.sig_log.connect(lambda line: self._log(job.id, line))
        self._worker.sig_finished.connect(lambda ok, msg: self._done(job.id, ok, msg))
        # Crash recovery: ensure cleanup if thread ends without sig_finished
        self._thread.finished.connect(lambda: self._thread_cleanup(job.id))
        self._thread.started.connect(self._worker.start)
        self.sig_job_started.emit(job.id)
        self.sig_queue_updated.emit()
        self._thread.start()
    def _thread_cleanup(self, job_id: str):
        if self._current and self._current.id == job_id:
            if self._current.status == "RUNNING":
                self._log_lines.append("ERROR: Worker thread terminated unexpectedly")
                self._done(job_id, False, "Worker crashed or was terminated")
    def _status(self, job_id: str, s: str):
        if self._current and self._current.id == job_id:
            self._current.status = s
            self.sig_status_update.emit(job_id, s, self._current.progress)
    def _progress(self, job_id: str, p: int):
        if self._current and self._current.id == job_id:
            self._current.progress = max(0, min(100, p))
            self.sig_status_update.emit(job_id, self._current.status, self._current.progress)
    def _log(self, job_id: str, line: str):
        self._log_lines.append(line)
        self.sig_log_line.emit(job_id, line)
    def _done(self, job_id: str, ok: bool, msg: str):
        log_path = None
        try:
            log_path = Path(self.settings.get("logs_dir")) / f"{job_id}.log"
            log_path.write_text("\n".join(self._log_lines), encoding="utf-8")
        except Exception:
            log_path = None
        if self._current and self._current.id == job_id:
            entry = HistoryEntry(
                id=job_id,
                job_type=self._current.job_type.value,
                device=self._current.device,
                files=[str(p) for p in self._current.files],
                options=self._current.to_dict()["options"],
                created_at=self._current.created_at,
                finished_at=datetime.now().isoformat(timespec="seconds"),
                success=ok,
                message=msg,
                log_file=str(log_path) if log_path else None
            )
            self.history.add(entry)
            self._current.status = "COMPLETED" if ok else "FAILED"
            if ok:
                self._current.progress = 100
            self.sig_job_finished.emit(job_id, ok, msg)
        if self._thread:
            self._thread.quit()
            self._thread.wait()
        self._thread = None
        self._worker = None
        self._current = None
        self.sig_queue_updated.emit()
        self._start_next()
