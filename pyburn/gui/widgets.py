from __future__ import annotations
import os
from pathlib import Path
from typing import Iterable, List, Optional
from PyQt6.QtWidgets import (
    QListWidget, QListWidgetItem, QWidget, QVBoxLayout, QProgressBar, QLabel,
    QTableWidget, QTableWidgetItem, QHBoxLayout, QPushButton, QMessageBox, QHeaderView, QFileDialog
)
from PyQt6.QtCore import QMimeData, pyqtSignal, Qt, QTimer, QUrl
from PyQt6.QtGui import QDragEnterEvent, QDropEvent, QDesktopServices
from ..core.history import HistoryStore, HistoryEntry
from datetime import datetime
def compute_total_size(paths: List[str], max_files: int = 50000) -> int:
    total = 0
    file_count = 0
    for p in paths:
        pp = Path(p)
        if pp.is_file():
            try:
                total += pp.stat().st_size
                file_count += 1
            except Exception:
                pass
        elif pp.is_dir():
            for root, _, files in os.walk(pp, followlinks=False):
                for fn in files:
                    if file_count >= max_files:
                        return total
                    fp = Path(root) / fn
                    try:
                        total += fp.stat().st_size
                        file_count += 1
                    except Exception:
                        pass
    return total
class FileListWidget(QListWidget):
    files_changed = pyqtSignal(list)
    def __init__(self, allow_dirs: bool = True, exts: Iterable[str] | None = None):
        super().__init__()
        self.setAcceptDrops(True)
        self.exts = set(e.lower() for e in (exts or []))
        self.allow_dirs = allow_dirs
        self._paths_set = set()
    def add_path(self, p: str):
        try:
            normalized = str(Path(p).resolve())
        except Exception:
            normalized = p
        if normalized in self._paths_set:
            return
        if os.path.isdir(p):
            if not self.allow_dirs: return
        else:
            if self.exts:
                ext = Path(p).suffix.lower().lstrip(".")
                if ext and ext not in self.exts:
                    return
        self.addItem(QListWidgetItem(p))
        self._paths_set.add(normalized)
        self.files_changed.emit(self.get_file_list())
    def dragEnterEvent(self, e: QDragEnterEvent):
        if e.mimeData().hasUrls(): e.acceptProposedAction()
        else: e.ignore()
    def dropEvent(self, e: QDropEvent):
        md: QMimeData = e.mimeData()
        if md.hasUrls():
            for url in md.urls():
                p = url.toLocalFile()
                if os.path.exists(p): self.add_path(p)
        e.acceptProposedAction()
    def takeItem(self, row):
        it = self.item(row)
        if it:
            try:
                normalized = str(Path(it.text()).resolve())
                self._paths_set.discard(normalized)
            except Exception:
                pass
        res = super().takeItem(row)
        self.files_changed.emit(self.get_file_list())
        return res
    def clear(self):
        super().clear()
        self._paths_set.clear()
        self.files_changed.emit(self.get_file_list())
    def get_file_list(self) -> List[str]:
        return [self.item(i).text() for i in range(self.count())]
class CapacityGauge(QWidget):
    def __init__(self, max_capacity_bytes: int):
        super().__init__()
        self.max_capacity = max_capacity_bytes
        self.current_size = 0
        lay = QVBoxLayout(self)
        self.lbl = QLabel("")
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setTextVisible(True)
        lay.addWidget(self.lbl)
        lay.addWidget(self.bar)
        self.update_size(0)
    def _human(self, n: int) -> str:
        units = ["B","KB","MB","GB","TB"]
        v = float(n); i = 0
        while v >= 1024 and i < len(units)-1:
            v /= 1024.0; i += 1
        return f"{v:.2f} {units[i]}"
    def update_size(self, size_bytes: int):
        self.current_size = size_bytes
        pct = int((size_bytes / self.max_capacity) * 100) if self.max_capacity > 0 else 0
        pct = max(0, min(100, pct))
        self.bar.setValue(pct)
        self.lbl.setText(f"{self._human(size_bytes)} / {self._human(self.max_capacity)} ({pct}%)")
        color = "#E74C3C" if size_bytes > self.max_capacity else "#2ECC71"
        self.bar.setStyleSheet(f"""
            QProgressBar {{ border: 1px solid #5e81ac; border-radius: 4px; background:#3b4252; color: white; }}
            QProgressBar::chunk {{ background-color:{color}; }}
        """)
class JobQueueWidget(QWidget):
    def __init__(self, service):
        super().__init__()
        self.service = service
        lay = QVBoxLayout(self)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Job", "Device", "Progress", "Status"])
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        lay.addWidget(self.table)
        btn_row = QHBoxLayout()
        self.btn_cancel = QPushButton("Cancel Current")
        self.btn_cancel.clicked.connect(self.service.cancel_current)
        self.btn_remove = QPushButton("Remove Selected (Queued)")
        self.btn_remove.clicked.connect(self._remove_selected)
        btn_row.addWidget(self.btn_cancel); btn_row.addWidget(self.btn_remove); btn_row.addStretch()
        lay.addLayout(btn_row)
        self.service.sig_queue_updated.connect(self.refresh)
        self.service.sig_status_update.connect(self._status_update)
        self.service.sig_job_started.connect(lambda _id: self.refresh())
        self.service.sig_job_finished.connect(lambda _id, ok, msg: self.refresh())
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(400)
        self.refresh()
    def refresh(self):
        jobs = self.service.get_list()
        self.table.setRowCount(len(jobs))
        for i, job in enumerate(jobs):
            self.table.setItem(i, 0, QTableWidgetItem(job.display_name))
            self.table.setItem(i, 1, QTableWidgetItem(job.device))
            pb = QProgressBar(); pb.setValue(job.progress)
            pb.setStyleSheet("QProgressBar { background:#4c566a; border:none; } QProgressBar::chunk { background:#a3be8c; }")
            self.table.setCellWidget(i, 2, pb)
            self.table.setItem(i, 3, QTableWidgetItem(job.status))
        self.btn_cancel.setEnabled(len(jobs) and jobs[0].status == "RUNNING")
    def _status_update(self, job_id: str, status: str, progress: int):
        jobs = self.service.get_list()
        for i, job in enumerate(jobs):
            if job.id == job_id:
                self.table.item(i, 3).setText(status)
                w = self.table.cellWidget(i, 2)
                if isinstance(w, QProgressBar):
                    w.setValue(progress)
    def _remove_selected(self):
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.information(self, "Remove", "Select a queued job to remove.")
            return
        jobs = self.service.get_list()
        if row >= len(jobs): return
        job = jobs[row]
        if job.status == "RUNNING":
            QMessageBox.warning(self, "Remove", "Cannot remove the currently running job.")
            return
        self.service.remove(job.id)
    def _tick(self):
        jobs = self.service.get_list()
        self.btn_cancel.setEnabled(len(jobs) and jobs[0].status == "RUNNING")
        for i, job in enumerate(jobs):
            w = self.table.cellWidget(i, 2)
            if isinstance(w, QProgressBar):
                w.setValue(job.progress)
            self.table.item(i, 3).setText(job.status)
class HistoryWidget(QWidget):
    def __init__(self, history: HistoryStore, queue):
        super().__init__()
        self.history = history
        self.queue = queue
        lay = QVBoxLayout(self)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Finished", "Job", "Device", "Success", "Message", "Log"])
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        lay.addWidget(self.table)
        btn_row = QHBoxLayout()
        self.btn_show = QPushButton("Show Log")
        self.btn_show.clicked.connect(self._show_log)
        self.btn_export = QPushButton("Export Log...")
        self.btn_export.clicked.connect(self._export_log)
        self.btn_retry = QPushButton("Retry Selected")
        self.btn_retry.clicked.connect(self._retry)
        btn_row.addWidget(self.btn_show); btn_row.addWidget(self.btn_export); btn_row.addWidget(self.btn_retry); btn_row.addStretch()
        lay.addLayout(btn_row)
        self.refresh()
    def _parse_dt(self, s: str):
        try:
            return datetime.fromisoformat(s)
        except Exception:
            return datetime.min
    def refresh(self):
        entries = sorted(self.history.all(), key=lambda e: self._parse_dt(e.finished_at), reverse=True)
        self.table.setRowCount(len(entries))
        for i, e in enumerate(entries):
            self.table.setItem(i, 0, QTableWidgetItem(e.finished_at))
            self.table.setItem(i, 1, QTableWidgetItem(e.job_type))
            self.table.setItem(i, 2, QTableWidgetItem(e.device))
            self.table.setItem(i, 3, QTableWidgetItem("Yes" if e.success else "No"))
            self.table.setItem(i, 4, QTableWidgetItem(e.message))
            self.table.setItem(i, 5, QTableWidgetItem(e.log_file or ""))
    def _selected_entry(self) -> Optional[HistoryEntry]:
        row = self.table.currentRow()
        if row < 0: return None
        entries = sorted(self.history.all(), key=lambda e: self._parse_dt(e.finished_at), reverse=True)
        if row >= len(entries): return None
        return entries[row]
    def _show_log(self):
        e = self._selected_entry()
        if not e or not e.log_file or not Path(e.log_file).exists():
            QMessageBox.information(self, "Show Log", "No log available.")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(e.log_file))
    def _export_log(self):
        e = self._selected_entry()
        if not e or not e.log_file or not Path(e.log_file).exists():
            QMessageBox.information(self, "Export Log", "No log available.")
            return
        dest, _ = QFileDialog.getSaveFileName(self, "Save Log As", Path.home().as_posix()+"/pyburn.log", "Log Files (*.log);;All Files (*)")
        if dest:
            try:
                Path(dest).write_text(Path(e.log_file).read_text(encoding="utf-8"), encoding="utf-8")
                QMessageBox.information(self, "Export Log", f"Log saved to {dest}")
            except Exception as ex:
                QMessageBox.warning(self, "Export Log", f"Failed to save: {ex}")
    def _retry(self):
        e = self._selected_entry()
        if not e:
            QMessageBox.information(self, "Retry", "Select a job to retry.")
            return
        self.queue.retry(e)
        QMessageBox.information(self, "Retry", "Job re-enqueued.")
