from __future__ import annotations
import re
import shutil
from pathlib import Path
from typing import List
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QHBoxLayout, QPushButton, QProgressBar, QFileDialog,
    QMessageBox, QGroupBox, QFormLayout, QComboBox, QCheckBox, QLineEdit, QProgressDialog
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFont
from ..core.config import Config
from ..core.jobs import Job, JobOptions, JobType
from ..core.tools import ToolFinder
from .widgets import FileListWidget, CapacityGauge, compute_total_size
from ..services.queue import JobQueueService
from ..services.metadata import musicbrainz_lookup
from ..services.media import MediaTools
from ..services.exec import ProcessRunner
CD_BYTES = 737_280_000
DVD_BYTES = 4_700_000_000
BD25_BYTES = 25_000_000_000
def disk_free_bytes(path: Path) -> int:
    try:
        usage = shutil.disk_usage(str(path))
        return usage.free
    except Exception:
        return 0
class BaseTab(QWidget):
    def __init__(self, cfg: Config, tools: ToolFinder, queue: JobQueueService):
        super().__init__()
        self.cfg = cfg
        self.tools = tools
        self.queue = queue
        self.progress = QProgressBar()
        self.status = QLabel("Ready.")
        self.queue.sig_status_update.connect(self._status_update)
    def _status_update(self, job_id: str, status: str, progress: int):
        jobs = self.queue.get_list()
        if jobs and jobs[0].id == job_id:
            if not status.startswith("LOG:"):
                self.status.setText(status)
            self.progress.setValue(progress)
class DataBurnTab(BaseTab):
    def __init__(self, cfg: Config, tools: ToolFinder, queue: JobQueueService):
        super().__init__(cfg, tools, queue)
        lay = QVBoxLayout(self)
        title = QLabel("Burn Data Disc"); title.setFont(QFont("Arial", 14, QFont.Weight.Bold))
        lay.addWidget(title)
        self.list = FileListWidget(allow_dirs=True)
        lay.addWidget(QLabel("Files/Folders (drag & drop):"))
        lay.addWidget(self.list)
        row = QHBoxLayout()
        b_add = QPushButton("Add Files"); b_add.clicked.connect(self._add_files)
        b_dir = QPushButton("Add Folder"); b_dir.clicked.connect(self._add_dir)
        b_rm = QPushButton("Remove Selected"); b_rm.clicked.connect(self._rm)
        b_cl = QPushButton("Clear"); b_cl.clicked.connect(self.list.clear)
        for b in (b_add, b_dir, b_rm, b_cl): row.addWidget(b)
        lay.addLayout(row)
        opts = QGroupBox("Options"); form = QFormLayout()
        self.ed_vol = QLineEdit("DATA_DISC")
        self.chk_verify = QCheckBox("Verify after burn"); self.chk_verify.setChecked(bool(self.cfg.settings.get("verify_after_burn", True)))
        self.chk_blank = QCheckBox("Auto-blank RW media"); self.chk_blank.setChecked(bool(self.cfg.settings.get("auto_blank_rw", True)))
        self.chk_eject = QCheckBox("Eject after burn"); self.chk_eject.setChecked(bool(self.cfg.settings.get("eject_after_burn", True)))
        self.chk_dummy = QCheckBox("Dummy burn (cdrecord)"); self.chk_dummy.setChecked(False)
        self.cbo_type = QComboBox(); self.cbo_type.addItems(["CD (700MB)", "DVD (4.7GB)", "Blu-ray (25GB)"])
        form.addRow("Volume Label:", self.ed_vol); form.addRow("Disc Type:", self.cbo_type)
        form.addRow("", self.chk_verify); form.addRow("", self.chk_blank); form.addRow("", self.chk_eject); form.addRow("", self.chk_dummy)
        opts.setLayout(form)
        lay.addWidget(opts)
        self.gauge = CapacityGauge(DVD_BYTES); lay.addWidget(self.gauge)
        self.btn = QPushButton("Queue Job: Burn Data Disc"); self.btn.clicked.connect(self._start)
        lay.addWidget(self.btn); lay.addWidget(self.progress); lay.addWidget(self.status)
        self.list.files_changed.connect(self._refresh)
        self.cbo_type.currentIndexChanged.connect(lambda: self._refresh(self.list.get_file_list()))
        self._refresh(self.list.get_file_list())
    def _capacity(self) -> int:
        return [CD_BYTES, DVD_BYTES, BD25_BYTES][self.cbo_type.currentIndex()]
    def _refresh(self, files: List[str]):
        self.gauge.max_capacity = self._capacity()
        self.gauge.update_size(compute_total_size(files))
    def _add_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Select Files")
        for f in files: self.list.add_path(f)
    def _add_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Select Folder")
        if d: self.list.add_path(d)
    def _rm(self):
        for it in self.list.selectedItems():
            self.list.takeItem(self.list.row(it))
        self._refresh(self.list.get_file_list())
    def _warn_oversized_media(self, data_bytes: int, cap_bytes: int) -> bool:
        if data_bytes > 0 and cap_bytes >= 10 * data_bytes:
            r = QMessageBox.question(self, "Small Data on Large Media",
                                     "The selected media capacity is much larger than the data size.\n"
                                     "Proceed anyway?",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            return r == QMessageBox.StandardButton.Yes
        return True
    def _confirm_blank_if_needed(self, device: str) -> bool:
        if not self.chk_blank.isChecked():
            return True
        # Best-effort detection via MediaTools
        try:
            media = MediaTools(self.tools, ProcessRunner())
            info = media.get_info(device)
            if info.get("rewritable") and info.get("blank") is False:
                r = QMessageBox.question(self, "Blank Media?",
                                         f"Rewritable media detected in {device}.\n"
                                         f"This will ERASE all existing data.\n\n"
                                         f"Continue with blanking?",
                                         QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
                return r == QMessageBox.StandardButton.Yes
        except Exception:
            pass
        return True
    def _start(self):
        files = self.list.get_file_list()
        if not files:
            QMessageBox.warning(self, "No Files", "Add files or folders.")
            return
        if self.gauge.current_size > self.gauge.max_capacity:
            r = QMessageBox.question(self, "Over Capacity", "Content exceeds disc capacity. Continue?",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if r != QMessageBox.StandardButton.Yes: return
        if not self._warn_oversized_media(self.gauge.current_size, self._capacity()):
            return
        device = self.cfg.settings.get("default_device", "/dev/sr0")
        if not self._confirm_blank_if_needed(device):
            QMessageBox.information(self, "Cancelled", "Blanking cancelled. Job not queued.")
            return
        temp_dir = Path(self.cfg.settings["temp_dir"])
        needed = max(1, self.gauge.current_size)
        free = disk_free_bytes(temp_dir)
        # Data burns: 1.2x
        multiplier = 1.2
        if free < needed * multiplier:
            r = QMessageBox.question(self, "Low Temp Space",
                                     f"Estimated ISO need ~ {needed*multiplier/1e9:.1f} GB; free ~ {free/1e9:.1f} GB.\nContinue?",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if r != QMessageBox.StandardButton.Yes:
                return
        job = Job(
            job_type=JobType.DATA,
            files=[Path(p) for p in files],
            device=device,
            options=JobOptions(
                temp_dir=temp_dir, verify=self.chk_verify.isChecked(),
                speed=self.cfg.settings.get("burn_speed", "Auto"),
                volume_label=self.ed_vol.text().strip() or "DATA_DISC",
                auto_blank=self.chk_blank.isChecked(),
                eject_after=self.chk_eject.isChecked(),
                dummy=self.chk_dummy.isChecked(),
            ),
        )
        self.queue.enqueue(job)
        QMessageBox.information(self, "Queued", f"Enqueued: {job.display_name}")
class AudioCDTab(BaseTab):
    def __init__(self, cfg: Config, tools: ToolFinder, queue: JobQueueService):
        super().__init__(cfg, tools, queue)
        lay = QVBoxLayout(self)
        title = QLabel("Create Audio CD"); title.setFont(QFont("Arial", 14, QFont.Weight.Bold))
        lay.addWidget(title)
        self.list = FileListWidget(allow_dirs=False, exts=["mp3", "wav", "flac", "ogg", "m4a", "aac"])
        lay.addWidget(QLabel("Audio files (drag & drop):"))
        lay.addWidget(self.list)
        row = QHBoxLayout()
        b_add = QPushButton("Add Audio Files"); b_add.clicked.connect(self._add)
        b_rm = QPushButton("Remove Selected"); b_rm.clicked.connect(self._rm)
        b_up = QPushButton("Move Up"); b_up.clicked.connect(self._move_up)
        b_down = QPushButton("Move Down"); b_down.clicked.connect(self._move_down)
        b_cl = QPushButton("Clear"); b_cl.clicked.connect(self.list.clear)
        for b in (b_add, b_rm, b_up, b_down, b_cl): row.addWidget(b)
        lay.addLayout(row)
        cdtext = QGroupBox("CD-Text"); form = QFormLayout()
        self.ed_album = QLineEdit(""); self.ed_artist = QLineEdit("")
        form.addRow("Album Title:", self.ed_album); form.addRow("Album Artist:", self.ed_artist); cdtext.setLayout(form)
        lay.addWidget(cdtext)
        self.btn_guess = QPushButton("Guess Track Titles From Filenames"); self.btn_guess.clicked.connect(self._guess_titles); lay.addWidget(self.btn_guess)
        self.gauge = CapacityGauge(CD_BYTES); lay.addWidget(self.gauge)
        self.chk_eject = QCheckBox("Eject after burn"); self.chk_eject.setChecked(bool(self.cfg.settings.get("eject_after_burn", True))); lay.addWidget(self.chk_eject)
        self.btn = QPushButton("Queue Job: Create Audio CD"); self.btn.clicked.connect(self._start)
        lay.addWidget(self.btn); lay.addWidget(self.progress); lay.addWidget(self.status)
        self.list.files_changed.connect(self._refresh)
        self._refresh(self.list.get_file_list())
        self.track_titles: List[str] = []
    def _move_up(self):
        row = self.list.currentRow()
        if row > 0:
            item = self.list.takeItem(row)
            self.list.insertItem(row - 1, item)
            self.list.setCurrentRow(row - 1)
    def _move_down(self):
        row = self.list.currentRow()
        if row < self.list.count() - 1:
            item = self.list.takeItem(row)
            self.list.insertItem(row + 1, item)
            self.list.setCurrentRow(row + 1)
    def _guess_titles(self):
        self.track_titles = []
        for i in range(self.list.count()):
            name = Path(self.list.item(i).text()).stem
            title = re.sub(r"^\d+\s*[-_. ]\s*", "", name)
            self.track_titles.append(title or f"Track {i+1}")
        if len(self.track_titles) != self.list.count():
            QMessageBox.warning(self, "CD-Text", f"Generated {len(self.track_titles)} titles for {self.list.count()} files")
            self.track_titles = []
        else:
            QMessageBox.information(self, "CD-Text", f"Generated {len(self.track_titles)} track titles.")
    def _refresh(self, files: List[str]):
        self.gauge.update_size(compute_total_size(files))
    def _add(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Select Audio Files", "", "Audio (*.mp3 *.wav *.flac *.ogg *.m4a *.aac)")
        for f in files: self.list.add_path(f)
    def _rm(self):
        for it in self.list.selectedItems():
            self.list.takeItem(self.list.row(it))
        self._refresh(self.list.get_file_list())
    def _start(self):
        cnt = self.list.count()
        if cnt == 0:
            QMessageBox.warning(self, "No Files", "Add audio files.")
            return
        if self.track_titles and len(self.track_titles) != cnt:
            QMessageBox.warning(self, "CD-Text", "Track titles count does not match number of files.")
            return
        temp_dir = Path(self.cfg.settings["temp_dir"])
        needed = max(1, self.gauge.current_size)
        free = disk_free_bytes(temp_dir)
        # Audio conversion slack ~1.5x
        if free < needed * 1.5:
            r = QMessageBox.question(self, "Low Temp Space",
                                     "Audio conversion may need extra temp space.\nContinue?",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if r != QMessageBox.StandardButton.Yes:
                return
        job = Job(
            job_type=JobType.AUDIO,
            files=[Path(self.list.item(i).text()) for i in range(cnt)],
            device=self.cfg.settings.get("default_device", "/dev/sr0"),
            options=JobOptions(
                temp_dir=temp_dir,
                speed=self.cfg.settings.get("burn_speed", "Auto"),
                eject_after=self.chk_eject.isChecked(),
                album_title=self.ed_album.text().strip() or None,
                album_performer=self.ed_artist.text().strip() or None,
                track_titles=self.track_titles if self.track_titles else None,
            ),
        )
        self.queue.enqueue(job)
        QMessageBox.information(self, "Queued", f"Enqueued: {job.display_name}")
class VideoDVDTab(BaseTab):
    def __init__(self, cfg: Config, tools: ToolFinder, queue: JobQueueService):
        super().__init__(cfg, tools, queue)
        lay = QVBoxLayout(self)
        title = QLabel("Create Video DVD"); title.setFont(QFont("Arial", 14, QFont.Weight.Bold)); lay.addWidget(title)
        self.list = FileListWidget(allow_dirs=False, exts=["mp4", "avi", "mkv", "mov", "wmv", "flv"])
        lay.addWidget(QLabel("Video files (drag & drop):")); lay.addWidget(self.list)
        row = QHBoxLayout(); b_add = QPushButton("Add Videos"); b_add.clicked.connect(self._add); b_rm = QPushButton("Remove Selected"); b_rm.clicked.connect(self._rm); b_cl = QPushButton("Clear"); b_cl.clicked.connect(self.list.clear)
        for b in (b_add, b_rm, b_cl): row.addWidget(b); lay.addLayout(row)
        self.gauge = CapacityGauge(DVD_BYTES); lay.addWidget(self.gauge)
        self.chk_blank = QCheckBox("Auto-blank RW media"); self.chk_blank.setChecked(bool(self.cfg.settings.get("auto_blank_rw", True)))
        self.chk_eject = QCheckBox("Eject after burn"); self.chk_eject.setChecked(bool(self.cfg.settings.get("eject_after_burn", True)))
        lay.addWidget(self.chk_blank); lay.addWidget(self.chk_eject)
        self.btn = QPushButton("Queue Job: Create Video DVD"); self.btn.clicked.connect(self._start)
        lay.addWidget(self.btn); lay.addWidget(self.progress); lay.addWidget(self.status)
        self.list.files_changed.connect(self._refresh)
        self._refresh(self.list.get_file_list())
    def _refresh(self, files: List[str]):
        self.gauge.update_size(compute_total_size(files))
    def _confirm_blank_if_needed(self, device: str) -> bool:
        if not self.chk_blank.isChecked():
            return True
        try:
            media = MediaTools(self.tools, ProcessRunner())
            info = media.get_info(device)
            if info.get("rewritable") and info.get("blank") is False:
                r = QMessageBox.question(self, "Blank Media?",
                                         f"Rewritable media detected in {device}.\n"
                                         f"This will ERASE all existing data.\n\n"
                                         f"Continue with blanking?",
                                         QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
                return r == QMessageBox.StandardButton.Yes
        except Exception:
            pass
        return True
    def _add(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Select Video Files", "", "Video (*.mp4 *.avi *.mkv *.mov *.wmv *.flv)")
        for f in files: self.list.add_path(f)
    def _rm(self):
        for it in self.list.selectedItems():
            self.list.takeItem(self.list.row(it))
        self._refresh(self.list.get_file_list())
    def _start(self):
        if self.list.count() == 0:
            QMessageBox.warning(self, "No Files", "Add video files.")
            return
        device = self.cfg.settings.get("default_device", "/dev/sr0")
        if not self._confirm_blank_if_needed(device):
            QMessageBox.information(self, "Cancelled", "Blanking cancelled. Job not queued.")
            return
        temp_dir = Path(self.cfg.settings["temp_dir"])
        needed = max(1, self.gauge.current_size)
        free = disk_free_bytes(temp_dir)
        # Video authoring: 2.5x
        if free < needed * 2.5:
            r = QMessageBox.question(self, "Low Temp Space",
                                     "Transcoding may require large temporary space.\nContinue?",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if r != QMessageBox.StandardButton.Yes:
                return
        job = Job(
            job_type=JobType.VIDEO_DVD,
            files=[Path(self.list.item(i).text()) for i in range(self.list.count())],
            device=device,
            options=JobOptions(temp_dir=temp_dir, speed=self.cfg.settings.get("burn_speed", "Auto"),
                               auto_blank=self.chk_blank.isChecked(), eject_after=self.chk_eject.isChecked()),
        )
        self.queue.enqueue(job)
        QMessageBox.information(self, "Queued", f"Enqueued: {job.display_name}")
class VideoBDTab(BaseTab):
    def __init__(self, cfg: Config, tools: ToolFinder, queue: JobQueueService):
        super().__init__(cfg, tools, queue)
        lay = QVBoxLayout(self)
        title = QLabel("Create Blu-ray (BDMV)"); title.setFont(QFont("Arial", 14, QFont.Weight.Bold)); lay.addWidget(title)
        self.list = FileListWidget(allow_dirs=False, exts=["mp4", "mkv", "mov", "ts", "m2ts"])
        lay.addWidget(QLabel("Video files (drag & drop):")); lay.addWidget(self.list)
        row = QHBoxLayout(); b_add = QPushButton("Add Videos"); b_add.clicked.connect(self._add); b_rm = QPushButton("Remove Selected"); b_rm.clicked.connect(self._rm); b_cl = QPushButton("Clear"); b_cl.clicked.connect(self.list.clear)
        for b in (b_add, b_rm, b_cl): row.addWidget(b); lay.addLayout(row)
        self.gauge = CapacityGauge(BD25_BYTES); lay.addWidget(self.gauge)
        self.chk_blank = QCheckBox("Auto-blank RW media"); self.chk_blank.setChecked(bool(self.cfg.settings.get("auto_blank_rw", True)))
        self.chk_eject = QCheckBox("Eject after burn"); self.chk_eject.setChecked(bool(self.cfg.settings.get("eject_after_burn", True)))
        lay.addWidget(self.chk_blank); lay.addWidget(self.chk_eject)
        self.btn = QPushButton("Queue Job: Create Blu-ray"); self.btn.clicked.connect(self._start)
        lay.addWidget(self.btn); lay.addWidget(self.progress); lay.addWidget(self.status)
        self.list.files_changed.connect(self._refresh)
        self._refresh(self.list.get_file_list())
    def _refresh(self, files: List[str]):
        self.gauge.update_size(compute_total_size(files))
    def _confirm_blank_if_needed(self, device: str) -> bool:
        if not self.chk_blank.isChecked():
            return True
        try:
            media = MediaTools(self.tools, ProcessRunner())
            info = media.get_info(device)
            if info.get("rewritable") and info.get("blank") is False:
                r = QMessageBox.question(self, "Blank Media?",
                                         f"Rewritable media detected in {device}.\n"
                                         f"This will ERASE all existing data.\n\n"
                                         f"Continue with blanking?",
                                         QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
                return r == QMessageBox.StandardButton.Yes
        except Exception:
            pass
        return True
    def _add(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Select Video Files", "", "Video (*.mp4 *.mkv *.mov *.ts *.m2ts)")
        for f in files: self.list.add_path(f)
    def _rm(self):
        for it in self.list.selectedItems():
            self.list.takeItem(self.list.row(it))
        self._refresh(self.list.get_file_list())
    def _start(self):
        if self.list.count() == 0:
            QMessageBox.warning(self, "No Files", "Add video files.")
            return
        device = self.cfg.settings.get("default_device", "/dev/sr0")
        if not self._confirm_blank_if_needed(device):
            QMessageBox.information(self, "Cancelled", "Blanking cancelled. Job not queued.")
            return
        temp_dir = Path(self.cfg.settings["temp_dir"])
        needed = max(1, self.gauge.current_size)
        free = disk_free_bytes(temp_dir)
        if free < needed * 2.5:
            r = QMessageBox.question(self, "Low Temp Space",
                                     "BD authoring may require large temporary space.\nContinue?",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if r != QMessageBox.StandardButton.Yes: return
        job = Job(
            job_type=JobType.VIDEO_BD,
            files=[Path(self.list.item(i).text()) for i in range(self.list.count())],
            device=device,
            options=JobOptions(temp_dir=temp_dir, speed=self.cfg.settings.get("burn_speed", "Auto"),
                               auto_blank=self.chk_blank.isChecked(), eject_after=self.chk_eject.isChecked()),
        )
        self.queue.enqueue(job)
        QMessageBox.information(self, "Queued", f"Enqueued: {job.display_name}")
class RipCDTab(BaseTab):
    def __init__(self, cfg: Config, tools: ToolFinder, queue: JobQueueService):
        super().__init__(cfg, tools, queue)
        from PyQt6.QtWidgets import QSpinBox
        lay = QVBoxLayout(self)
        title = QLabel("Rip Audio CD"); title.setFont(QFont("Arial", 14, QFont.Weight.Bold)); lay.addWidget(title)
        opts = QGroupBox("Rip Options"); form = QFormLayout()
        self.cbo_fmt = QComboBox(); self.cbo_fmt.addItems(["MP3", "FLAC", "WAV"])
        self.cbo_fmt.setCurrentText(str(self.cfg.settings.get("audio_format", "MP3")))
        self.sp_bitrate = QSpinBox(); self.sp_bitrate.setRange(128, 320); self.sp_bitrate.setValue(int(self.cfg.settings.get("audio_bitrate", 320)))
        self.sp_bitrate.setAccelerated(True)
        self.ed_out = QLineEdit(str(Path.home() / "Music")); self.ed_out.setReadOnly(True)
        b_out = QPushButton("Browse"); b_out.clicked.connect(self._choose)
        row = QHBoxLayout(); row.addWidget(self.ed_out); row.addWidget(b_out)
        form.addRow("Format:", self.cbo_fmt); form.addRow("MP3 Bitrate:", self.sp_bitrate); form.addRow("Output:", row)
        opts.setLayout(form); lay.addWidget(opts)
        self.btn_mb = QPushButton("Lookup Metadata (MusicBrainz)"); self.btn_mb.clicked.connect(self._lookup_mb); lay.addWidget(self.btn_mb)
        self.btn = QPushButton("Queue Job: Rip CD"); self.btn.clicked.connect(self._start)
        lay.addWidget(self.btn); lay.addWidget(self.progress); lay.addWidget(self.status); lay.addStretch(1)
        self.track_titles: List[str] = []
    def _choose(self):
        d = QFileDialog.getExistingDirectory(self, "Select Output Directory")
        if d: self.ed_out.setText(d)
    def _lookup_mb(self):
        if not bool(self.cfg.settings.get("musicbrainz_enabled", True)):
            QMessageBox.information(self, "MusicBrainz", "MusicBrainz lookup disabled.")
            return
        progress = QProgressDialog("Looking up CD metadata...", "Cancel", 0, 0, self)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.show()
        class MBThread(QThread):
            finished_data = pyqtSignal(object)
            def __init__(self, tools: ToolFinder, device: str):
                super().__init__()
                self.tools = tools; self.device = device
            def run(self):
                try:
                    from ..services.metadata import musicbrainz_lookup as mb
                except Exception:
                    mb = musicbrainz_lookup
                result = musicbrainz_lookup(self.tools, self.device)
                self.finished_data.emit(result)
        def done(md):
            progress.close()
            if not md:
                QMessageBox.information(self, "MusicBrainz", "No metadata found (or dependencies missing).")
                return
            self.track_titles = md.get("tracks") or []
            QMessageBox.information(self, "MusicBrainz", f"Found {len(self.track_titles)} track titles.")
        th = MBThread(self.tools, self.cfg.settings.get("default_device", "/dev/sr0"))
        th.finished_data.connect(done)
        th.start()
        self._mb_thread = th  # hold ref
    def _start(self):
        out = Path(self.ed_out.text())
        job = Job(
            job_type=JobType.RIP,
            files=[],
            device=self.cfg.settings.get("default_device", "/dev/sr0"),
            options=JobOptions(
                temp_dir=Path(self.cfg.settings["temp_dir"]),
                output_dir=out,
                rip_format=self.cbo_fmt.currentText(),
                rip_bitrate=self.sp_bitrate.value(),
                track_titles=self.track_titles if self.track_titles else None,
            ),
        )
        self.queue.enqueue(job)
        QMessageBox.information(self, "Queued", f"Enqueued: {job.display_name}")
