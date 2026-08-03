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
from .widgets import FileListWidget, DiscTreeWidget, CapacityGauge, compute_total_size, compute_total_duration, dvd_max_minutes, bd_max_minutes
from ..services.queue import JobQueueService
from ..services.metadata import musicbrainz_lookup
from ..services.media import MediaTools
from ..services.exec import ProcessRunner

# CD_BYTES / DVD_BYTES are byte capacities used by the data-disc gauge. Video
# DVD capacity is time-based and computed by the fit-to-disc model in widgets.py
# (dvd_max_minutes), so there is no fixed DVD minutes constant here anymore.
CD_BYTES = 737_280_000
DVD_BYTES = 4_700_000_000
BD25_BYTES = 25_000_000_000


def disk_free_bytes(path: Path) -> int:
    try:
        usage = shutil.disk_usage(str(path))
        return usage.free
    except Exception:
        return 0


def _shbrowseforfolder(parent, title: str) -> str:
    """Show the classic Windows Shell 'Browse For Folder' dialog (the compact
    folder tree with OK/Cancel) and return the chosen path, or "" if cancelled.

    Uses the old dialog style (BIF_RETURNONLYFSDIRS, no BIF_NEWDIALOGSTYLE), which
    is the tree-only look. Windows-only; callers fall back to a Qt chooser
    elsewhere. Pure GUI helper.
    """
    import ctypes
    from ctypes import wintypes

    shell32 = ctypes.windll.shell32
    ole32 = ctypes.windll.ole32

    class BROWSEINFO(ctypes.Structure):
        _fields_ = [
            ("hwndOwner", wintypes.HWND),
            ("pidlRoot", ctypes.c_void_p),
            ("pszDisplayName", wintypes.LPWSTR),
            ("lpszTitle", wintypes.LPCWSTR),
            ("ulFlags", wintypes.UINT),
            ("lpfn", ctypes.c_void_p),
            ("lParam", wintypes.LPARAM),
            ("iImage", ctypes.c_int),
        ]

    BIF_RETURNONLYFSDIRS = 0x00000001

    shell32.SHBrowseForFolderW.argtypes = [ctypes.POINTER(BROWSEINFO)]
    shell32.SHBrowseForFolderW.restype = ctypes.c_void_p
    shell32.SHGetPathFromIDListW.argtypes = [ctypes.c_void_p, wintypes.LPWSTR]
    shell32.SHGetPathFromIDListW.restype = wintypes.BOOL
    ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    ole32.CoTaskMemFree.restype = None

    # Owner window handle, so the dialog is modal to the app when possible.
    hwnd = 0
    try:
        if parent is not None:
            hwnd = int(parent.winId())
    except Exception:
        hwnd = 0

    display_buf = ctypes.create_unicode_buffer(260)
    bi = BROWSEINFO()
    bi.hwndOwner = hwnd
    bi.pidlRoot = None
    bi.pszDisplayName = ctypes.cast(display_buf, wintypes.LPWSTR)
    bi.lpszTitle = title
    bi.ulFlags = BIF_RETURNONLYFSDIRS
    bi.lpfn = None
    bi.lParam = 0
    bi.iImage = 0

    pidl = shell32.SHBrowseForFolderW(ctypes.byref(bi))
    if not pidl:
        return ""
    try:
        path_buf = ctypes.create_unicode_buffer(260)
        if shell32.SHGetPathFromIDListW(pidl, path_buf):
            return path_buf.value or ""
        return ""
    finally:
        ole32.CoTaskMemFree(pidl)


class BaseTab(QWidget):
    def __init__(self, cfg: Config, tools: ToolFinder, queue: JobQueueService):
        super().__init__()
        self.cfg = cfg
        self.tools = tools
        self.queue = queue
        self.progress = QProgressBar()
        self.status = QLabel("Ready.")
        # Track the job IDs this tab enqueued so the tab's own progress bar and
        # status line only reflect its jobs, not whatever happens to be first
        # in the shared queue. Without this, every tab mirrors the running job
        # regardless of which tab started it.
        self._my_job_ids: set[str] = set()
        self.queue.sig_status_update.connect(self._status_update)
        # Surface job outcomes for this tab's jobs. Without this a failure (for
        # example ripping with no disc in the drive) only updated the Queue list,
        # so a user on this tab saw nothing happen and got no error.
        self.queue.sig_job_finished.connect(self._job_finished)

    def _register_job(self, job: Job):
        self._my_job_ids.add(job.id)

    def _make_drive_row(self):
        """Build a 'Drive:' row with a dropdown of optical drives plus a small
        Refresh button, for tabs to place in their layout. The selected drive is
        what the tab's job uses. Defaults to the configured default drive, so if
        the user does not touch it the device is exactly what it was before.

        Returns a QWidget (the row) ready to add to a layout.
        """
        from PyQt6.QtWidgets import QWidget as _QWidget
        from ..core.devices import get_devices
        row_w = _QWidget()
        row = QHBoxLayout(row_w)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(QLabel("Drive:"))
        self.cbo_drive = QComboBox()
        self._populate_drives(get_devices())
        row.addWidget(self.cbo_drive, 1)
        b_refresh = QPushButton("Refresh")
        b_refresh.setToolTip("Rescan for optical drives")
        b_refresh.clicked.connect(self._refresh_drives)
        row.addWidget(b_refresh)
        return row_w

    def _populate_drives(self, devs):
        cur = self.cfg.settings.get("default_device", "")
        self.cbo_drive.clear()
        sel = -1
        for i, d in enumerate(devs):
            self.cbo_drive.addItem(d.display, d.id)
            if d.id == cur:
                sel = i
        if not devs:
            # Keep a usable fallback so the dropdown is never empty.
            self.cbo_drive.addItem(str(cur or "default"), cur or "")
            sel = 0
        if sel >= 0:
            self.cbo_drive.setCurrentIndex(sel)

    def _refresh_drives(self):
        from ..core.devices import refresh_devices
        self._populate_drives(refresh_devices())

    def selected_device(self) -> str:
        """The drive chosen in this tab's dropdown, or the configured default if
        the tab has no dropdown, so callers always get a valid device."""
        cbo = getattr(self, "cbo_drive", None)
        if cbo is not None:
            data = cbo.currentData()
            if data:
                return data
        return self.cfg.settings.get("default_device", "/dev/sr0")

    def _confirm_blank_if_needed(self, device: str) -> bool:
        # Auto-blank is a per-drive setting now (Settings window), not a per-tab
        # checkbox. If it is off for this drive, nothing to confirm.
        if not self.cfg.drive_setting(device, "auto_blank_rw", True):
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

    def _job_finished(self, job_id: str, ok: bool, msg: str):
        if job_id not in self._my_job_ids:
            return
        if ok:
            self.status.setText(msg or "Done.")
        else:
            self.status.setText(f"Failed: {msg}" if msg else "Failed.")
            self.progress.setValue(0)
            QMessageBox.warning(self, "Job Failed",
                                msg or "The job did not complete. Check the drive and try again.")

    def _status_update(self, job_id: str, status: str, progress: int):
        if job_id not in self._my_job_ids:
            return
        if not status.startswith("LOG:"):
            self.status.setText(status)
        self.progress.setValue(progress)


class DataBurnTab(BaseTab):
    def __init__(self, cfg: Config, tools: ToolFinder, queue: JobQueueService):
        super().__init__(cfg, tools, queue)
        lay = QVBoxLayout(self)
        title = QLabel("Burn Data Disc")
        title.setFont(QFont("Arial", 14, QFont.Weight.Bold))
        lay.addWidget(title)
        lay.addWidget(self._make_drive_row())
        lay.addWidget(QLabel("Disc contents (this is the root of the disc; drag files and "
                             "folders here, and drag items into folders to arrange them):"))
        self.tree = DiscTreeWidget()
        lay.addWidget(self.tree)
        row = QHBoxLayout()
        b_add = QPushButton("Add Files")
        b_add.clicked.connect(self._add_files)
        b_dir = QPushButton("Add Folder")
        b_dir.clicked.connect(self._add_dir)
        b_newf = QPushButton("New Folder")
        b_newf.clicked.connect(self._new_folder)
        b_ren = QPushButton("Rename")
        b_ren.clicked.connect(self._rename)
        b_rm = QPushButton("Remove Selected")
        b_rm.clicked.connect(self._rm)
        b_cl = QPushButton("Clear")
        b_cl.clicked.connect(self._clear)
        for b in (b_add, b_dir, b_newf, b_ren, b_rm, b_cl):
            row.addWidget(b)
        lay.addLayout(row)
        opts = QGroupBox("Options")
        form = QFormLayout()
        self.ed_vol = QLineEdit("DATA_DISC")
        self.cbo_type = QComboBox()
        self.cbo_type.addItems(["CD (700MB)", "DVD (4.7GB)", "Blu-ray (25GB)"])
        form.addRow("Volume Label:", self.ed_vol)
        form.addRow("Disc Type:", self.cbo_type)
        opts.setLayout(form)
        lay.addWidget(opts)
        self.gauge = CapacityGauge(DVD_BYTES)
        lay.addWidget(self.gauge)
        self.btn = QPushButton("Burn Data Disc")
        self.btn.clicked.connect(self._start)
        lay.addWidget(self.btn)
        lay.addWidget(self.progress)
        lay.addWidget(self.status)
        self.tree.changed.connect(self._refresh)
        self.cbo_type.currentIndexChanged.connect(self._refresh)
        self._refresh()

    def _capacity(self) -> int:
        return [CD_BYTES, DVD_BYTES, BD25_BYTES][self.cbo_type.currentIndex()]

    def _refresh(self, *args):
        self.gauge.max_capacity = self._capacity()
        self.gauge.update_size(self.tree.total_size())

    def _add_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Select Files")
        if files:
            # Add into the selected folder if one is selected, else the root.
            sel = self.tree.selectedItems()
            parent = sel[0] if (sel and self.tree._is_dir(sel[0])) else None
            self.tree.add_files(files, parent)

    def _pick_folder(self) -> str:
        """Open a folder chooser and return the selected path (or "").

        On Windows this uses the classic Shell "Browse For Folder" dialog
        (SHBrowseForFolder, old style: a compact folder tree with OK/Cancel), via
        ctypes. On other platforms it falls back to Qt's directory chooser. GUI
        only; nothing here touches the burn engine.
        """
        try:
            from ..services.platform_caps import is_windows
            win = is_windows()
        except Exception:
            win = False
        if win:
            try:
                path = _shbrowseforfolder(self, "Select a folder to add to the disc")
                return path or ""
            except Exception:
                pass  # fall back to Qt below
        d = QFileDialog.getExistingDirectory(
            self, "Select Folder", "",
            QFileDialog.Option.ShowDirsOnly | QFileDialog.Option.DontUseNativeDialog,
        )
        return d or ""

    def _add_dir(self):
        d = self._pick_folder()
        if not d:
            return
        folder = Path(d)
        name = folder.name or str(folder)

        # Ask how the folder should be placed on the disc. Adding the folder
        # itself nests everything under a top-level folder; adding its contents
        # places the folder's files and subfolders directly at the disc root.
        box = QMessageBox(self)
        box.setWindowTitle("Add Folder")
        box.setIcon(QMessageBox.Icon.Question)
        box.setText(f"How would you like to add \"{name}\" to the disc?")
        box.setInformativeText(
            "Add folder: the disc will contain a top-level folder named "
            f"\"{name}\" holding all of its files and subfolders.\n\n"
            "Add contents: the folder's files and subfolders will be placed "
            "directly at the root of the disc."
        )
        btn_folder = box.addButton("Add Folder", QMessageBox.ButtonRole.AcceptRole)
        btn_contents = box.addButton("Add Contents", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(btn_folder)
        box.exec()
        clicked = box.clickedButton()
        # Add into the selected folder if one is selected, else the disc root.
        sel = self.tree.selectedItems()
        parent = sel[0] if (sel and self.tree._is_dir(sel[0])) else None
        if clicked is btn_folder:
            self.tree.add_folder_as_folder(str(folder), parent)
        elif clicked is btn_contents:
            self.tree.add_folder_contents(str(folder), parent)

    def _new_folder(self):
        sel = self.tree.selectedItems()
        parent = sel[0] if (sel and self.tree._is_dir(sel[0])) else None
        item = self.tree.new_folder(parent)
        self.tree.setCurrentItem(item)
        self.tree.editItem(item, 0)  # let the user type the name immediately

    def _rename(self):
        sel = self.tree.selectedItems()
        if not sel:
            QMessageBox.information(self, "Rename", "Select an item to rename.")
            return
        self.tree.editItem(sel[0], 0)

    def _rm(self):
        self.tree.remove_selected()

    def _clear(self):
        self.tree.clear_all()

    def _warn_oversized_media(self, data_bytes: int, cap_bytes: int) -> bool:
        if data_bytes > 0 and cap_bytes >= 10 * data_bytes:
            r = QMessageBox.question(self, "Small Data on Large Media",
                                     "The selected media capacity is much larger than the data size.\n"
                                     "Proceed anyway?",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            return r == QMessageBox.StandardButton.Yes
        return True

    def _start(self):
        if self.tree.is_empty():
            QMessageBox.warning(self, "No Files", "Add files or folders to the disc.")
            return
        if self.gauge.current_size > self.gauge.max_capacity:
            r = QMessageBox.question(self, "Over Capacity", "Content exceeds disc capacity. Continue?",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if r != QMessageBox.StandardButton.Yes:
                return
        if not self._warn_oversized_media(self.gauge.current_size, self._capacity()):
            return
        device = self.selected_device()
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
        disc_tree = self.tree.export_tree()
        job = Job(
            job_type=JobType.DATA,
            files=[],
            device=device,
            options=JobOptions(
                temp_dir=temp_dir,
                verify=self.cfg.drive_setting(device, "verify_after_burn", True),
                speed=self.cfg.drive_setting(device, "burn_speed", "Auto"),
                volume_label=self.ed_vol.text().strip() or "DATA_DISC",
                auto_blank=self.cfg.drive_setting(device, "auto_blank_rw", True),
                eject_after=self.cfg.drive_setting(device, "eject_after_burn", True),
                dummy=False,
                disc_tree=disc_tree,
            ),
        )
        self._register_job(job)
        self.queue.enqueue(job)
        # Job start is shown non-modally in the status line and the Queue tab;
        # no blocking popup is needed.
        self.status.setText(f"Started: {job.display_name}")


class AudioCDTab(BaseTab):
    def __init__(self, cfg: Config, tools: ToolFinder, queue: JobQueueService):
        super().__init__(cfg, tools, queue)
        lay = QVBoxLayout(self)
        title = QLabel("Create Audio CD")
        title.setFont(QFont("Arial", 14, QFont.Weight.Bold))
        lay.addWidget(title)
        lay.addWidget(self._make_drive_row())
        self.list = FileListWidget(allow_dirs=False, exts=["mp3", "wav", "flac", "ogg", "m4a", "aac"])
        lay.addWidget(QLabel("Audio files (drag & drop):"))
        lay.addWidget(self.list)
        row = QHBoxLayout()
        b_add = QPushButton("Add Audio Files")
        b_add.clicked.connect(self._add)
        b_rm = QPushButton("Remove Selected")
        b_rm.clicked.connect(self._rm)
        b_up = QPushButton("Move Up")
        b_up.clicked.connect(self._move_up)
        b_down = QPushButton("Move Down")
        b_down.clicked.connect(self._move_down)
        b_cl = QPushButton("Clear")
        b_cl.clicked.connect(self.list.clear)
        for b in (b_add, b_rm, b_up, b_down, b_cl):
            row.addWidget(b)
        lay.addLayout(row)
        cdtext = QGroupBox("CD-Text")
        form = QFormLayout()
        self.ed_album = QLineEdit("")
        self.ed_artist = QLineEdit("")
        form.addRow("Album Title:", self.ed_album)
        form.addRow("Album Artist:", self.ed_artist)
        cdtext.setLayout(form)
        lay.addWidget(cdtext)
        self.btn_guess = QPushButton("Guess Track Titles From Filenames")
        self.btn_guess.clicked.connect(self._guess_titles)
        lay.addWidget(self.btn_guess)
        self.gauge = CapacityGauge(CD_BYTES, mode="minutes", max_minutes=80.0)
        lay.addWidget(self.gauge)
        self.btn = QPushButton("Burn Audio CD")
        self.btn.clicked.connect(self._start)
        lay.addWidget(self.btn)
        lay.addWidget(self.progress)
        lay.addWidget(self.status)
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
        # Audio CDs are limited by playback time, not bytes. Compute total
        # duration with ffprobe in a background thread so dropping in a full
        # album does not freeze the UI. Fall back to a rough estimate if ffprobe
        # is unavailable.
        #
        # IMPORTANT: adding files fires this repeatedly. Each run starts a
        # QThread, and we MUST keep a reference to every running thread until it
        # finishes; otherwise Python garbage-collects a still-running QThread and
        # the app crashes hard with no error. We keep a set of live threads and
        # drop each one only when it has finished.
        ffprobe = self.tools.find("ffprobe")
        file_list = list(files)
        if not file_list:
            self.gauge.update_duration(0.0)
            return

        if not hasattr(self, "_dur_threads"):
            self._dur_threads = set()

        class DurThread(QThread):
            done = pyqtSignal(float)

            def __init__(self, paths, probe):
                super().__init__()
                self.paths = paths
                self.probe = probe

            def run(self):
                try:
                    secs = compute_total_duration(self.paths, self.probe)
                except Exception:
                    secs = 0.0
                self.done.emit(secs)

        snapshot = file_list
        thread = DurThread(file_list, ffprobe)

        def on_done(secs, th=thread):
            try:
                if secs and secs > 0:
                    self.gauge.update_duration(secs)
                else:
                    est_seconds = compute_total_size(snapshot) / (10 * 1024 * 1024) * 60.0
                    self.gauge.update_duration(est_seconds)
            finally:
                # Now that it has finished, stop tracking it. Do this after the
                # thread has fully finished to avoid destroying a running thread.
                self._dur_threads.discard(th)

        thread.done.connect(on_done)
        thread.finished.connect(lambda th=thread: self._dur_threads.discard(th))
        self._dur_threads.add(thread)
        thread.start()

    def _add(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Select Audio Files", "", "Audio (*.mp3 *.wav *.flac *.ogg *.m4a *.aac)")
        for f in files:
            self.list.add_path(f)

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
        # Warn if total playback time exceeds the disc (audio CDs hold ~80 min).
        if self.gauge.current_seconds > self.gauge.max_minutes * 60.0:
            r = QMessageBox.question(
                self, "Over Capacity",
                f"Total playback time is {int(self.gauge.current_seconds // 60)} min, which "
                f"exceeds the {int(self.gauge.max_minutes)}-minute audio CD limit.\nContinue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if r != QMessageBox.StandardButton.Yes:
                return
        temp_dir = Path(self.cfg.settings["temp_dir"])
        # Temp space for decoding: CD audio is ~10 MB/min, so size by duration.
        needed = max(1, int((self.gauge.current_seconds / 60.0) * 10 * 1024 * 1024))
        free = disk_free_bytes(temp_dir)
        if free < needed * 1.5:
            r = QMessageBox.question(self, "Low Temp Space",
                                     "Audio conversion may need extra temp space.\nContinue?",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if r != QMessageBox.StandardButton.Yes:
                return
        job = Job(
            job_type=JobType.AUDIO,
            files=[Path(self.list.item(i).text()) for i in range(cnt)],
            device=self.selected_device(),
            options=JobOptions(
                temp_dir=temp_dir,
                speed=self.cfg.drive_setting(self.selected_device(), "burn_speed", "Auto"),
                eject_after=self.cfg.drive_setting(self.selected_device(), "eject_after_burn", True),
                album_title=self.ed_album.text().strip() or None,
                album_performer=self.ed_artist.text().strip() or None,
                track_titles=self.track_titles if self.track_titles else None,
            ),
        )
        self._register_job(job)
        self.queue.enqueue(job)
        # Job start is shown non-modally in the status line and the Queue tab;
        # no blocking popup is needed.
        self.status.setText(f"Started: {job.display_name}")


class VideoDVDTab(BaseTab):
    def __init__(self, cfg: Config, tools: ToolFinder, queue: JobQueueService):
        super().__init__(cfg, tools, queue)
        lay = QVBoxLayout(self)
        title = QLabel("Create Video DVD")
        title.setFont(QFont("Arial", 14, QFont.Weight.Bold))
        lay.addWidget(title)
        lay.addWidget(self._make_drive_row())
        self.list = FileListWidget(allow_dirs=False, exts=["mp4", "avi", "mkv", "mov", "wmv", "flv"])
        lay.addWidget(QLabel("Video files (drag & drop):"))
        lay.addWidget(self.list)
        row = QHBoxLayout()
        b_add = QPushButton("Add Videos")
        b_add.clicked.connect(self._add)
        b_rm = QPushButton("Remove Selected")
        b_rm.clicked.connect(self._rm)
        b_cl = QPushButton("Clear")
        b_cl.clicked.connect(self.list.clear)
        for b in (b_add, b_rm, b_cl):
            row.addWidget(b)
        lay.addLayout(row)
        self.gauge = CapacityGauge(DVD_BYTES, mode="minutes", max_minutes=dvd_max_minutes())
        lay.addWidget(self.gauge)
        self.btn = QPushButton("Burn Video DVD")
        self.btn.clicked.connect(self._start)
        lay.addWidget(self.btn)
        lay.addWidget(self.progress)
        lay.addWidget(self.status)
        self.list.files_changed.connect(self._refresh)
        self._refresh(self.list.get_file_list())

    def _refresh(self, files: List[str]):
        # Video DVD capacity is governed by playback time at the fixed pal-dvd
        # transcode bitrate, NOT by the compressed source size (an h.264 MP4 and
        # a much larger MKV of the same length produce nearly identical MPEG-2).
        # Measure total duration with ffprobe in a background thread so dropping
        # in long videos does not freeze the UI, exactly like the audio CD tab.
        ffprobe = self.tools.find("ffprobe")
        file_list = list(files)
        if not file_list:
            self.gauge.update_duration(0.0)
            return

        if not hasattr(self, "_dur_threads"):
            self._dur_threads = set()

        class DurThread(QThread):
            done = pyqtSignal(float)

            def __init__(self, paths, probe):
                super().__init__()
                self.paths = paths
                self.probe = probe

            def run(self):
                try:
                    secs = compute_total_duration(self.paths, self.probe)
                except Exception:
                    secs = 0.0
                self.done.emit(secs)

        thread = DurThread(file_list, ffprobe)

        def on_done(secs, th=thread):
            try:
                self.gauge.update_duration(secs if secs and secs > 0 else 0.0)
            finally:
                self._dur_threads.discard(th)

        thread.done.connect(on_done)
        thread.finished.connect(lambda th=thread: self._dur_threads.discard(th))
        self._dur_threads.add(thread)
        thread.start()

    def _add(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Select Video Files", "", "Video (*.mp4 *.avi *.mkv *.mov *.wmv *.flv)")
        for f in files:
            self.list.add_path(f)

    def _rm(self):
        for it in self.list.selectedItems():
            self.list.takeItem(self.list.row(it))
        self._refresh(self.list.get_file_list())

    def _start(self):
        if self.list.count() == 0:
            QMessageBox.warning(self, "No Files", "Add video files.")
            return
        # DVD-Video capacity is playback time at the pal-dvd bitrate (~100 min on
        # a single layer). Warn if the total runtime exceeds that.
        if self.gauge.current_seconds > self.gauge.max_minutes * 60.0:
            r = QMessageBox.question(
                self, "Over Capacity",
                f"Total video runtime is {int(self.gauge.current_seconds // 60)} min, which "
                f"exceeds the ~{int(self.gauge.max_minutes)}-minute single-layer DVD limit.\nContinue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if r != QMessageBox.StandardButton.Yes:
                return
        device = self.selected_device()
        if not self._confirm_blank_if_needed(device):
            QMessageBox.information(self, "Cancelled", "Blanking cancelled. Job not queued.")
            return
        temp_dir = Path(self.cfg.settings["temp_dir"])
        # With fit-to-disc bitrate the authored output is always about one full
        # DVD (~4.7 GB) regardless of runtime; authoring + ISO roughly doubles
        # that on disk at peak.
        needed = DVD_BYTES
        free = disk_free_bytes(temp_dir)
        if free < needed * 2.0:
            r = QMessageBox.question(self, "Low Temp Space",
                                     f"DVD authoring needs about {needed*2.0/1e9:.1f} GB free; "
                                     f"about {free/1e9:.1f} GB available.\nContinue?",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if r != QMessageBox.StandardButton.Yes:
                return
        job = Job(
            job_type=JobType.VIDEO_DVD,
            files=[Path(self.list.item(i).text()) for i in range(self.list.count())],
            device=device,
            options=JobOptions(temp_dir=temp_dir, speed=self.cfg.settings.get("burn_speed", "Auto"),
                               auto_blank=self.cfg.drive_setting(device, "auto_blank_rw", True),
                               eject_after=self.cfg.drive_setting(device, "eject_after_burn", True)),
        )
        self._register_job(job)
        self.queue.enqueue(job)
        # Job start is shown non-modally in the status line and the Queue tab;
        # no blocking popup is needed.
        self.status.setText(f"Started: {job.display_name}")


class VideoBDTab(BaseTab):
    def __init__(self, cfg: Config, tools: ToolFinder, queue: JobQueueService):
        super().__init__(cfg, tools, queue)
        lay = QVBoxLayout(self)
        title = QLabel("Create Blu-ray (BDMV)")
        title.setFont(QFont("Arial", 14, QFont.Weight.Bold))
        lay.addWidget(title)
        lay.addWidget(self._make_drive_row())
        self.list = FileListWidget(allow_dirs=False, exts=["mp4", "mkv", "mov", "ts", "m2ts"])
        lay.addWidget(QLabel("Video files (drag & drop):"))
        lay.addWidget(self.list)
        row = QHBoxLayout()
        b_add = QPushButton("Add Videos")
        b_add.clicked.connect(self._add)
        b_rm = QPushButton("Remove Selected")
        b_rm.clicked.connect(self._rm)
        b_cl = QPushButton("Clear")
        b_cl.clicked.connect(self.list.clear)
        for b in (b_add, b_rm, b_cl):
            row.addWidget(b)
        lay.addLayout(row)
        self.gauge = CapacityGauge(BD25_BYTES, mode="minutes", max_minutes=bd_max_minutes())
        lay.addWidget(self.gauge)
        self.btn = QPushButton("Burn Blu-ray")
        self.btn.clicked.connect(self._start)
        lay.addWidget(self.btn)
        lay.addWidget(self.progress)
        lay.addWidget(self.status)
        self.list.files_changed.connect(self._refresh)
        self._refresh(self.list.get_file_list())

    def _refresh(self, files: List[str]):
        # Blu-ray capacity, like DVD, is governed by playback time at the
        # fit-to-disc transcode bitrate, not by the compressed source size.
        # Measure total duration with ffprobe in a background thread.
        ffprobe = self.tools.find("ffprobe")
        file_list = list(files)
        if not file_list:
            self.gauge.update_duration(0.0)
            return

        if not hasattr(self, "_dur_threads"):
            self._dur_threads = set()

        class DurThread(QThread):
            done = pyqtSignal(float)

            def __init__(self, paths, probe):
                super().__init__()
                self.paths = paths
                self.probe = probe

            def run(self):
                try:
                    secs = compute_total_duration(self.paths, self.probe)
                except Exception:
                    secs = 0.0
                self.done.emit(secs)

        thread = DurThread(file_list, ffprobe)

        def on_done(secs, th=thread):
            try:
                self.gauge.update_duration(secs if secs and secs > 0 else 0.0)
            finally:
                self._dur_threads.discard(th)

        thread.done.connect(on_done)
        thread.finished.connect(lambda th=thread: self._dur_threads.discard(th))
        self._dur_threads.add(thread)
        thread.start()

    def _add(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Select Video Files", "", "Video (*.mp4 *.mkv *.mov *.ts *.m2ts)")
        for f in files:
            self.list.add_path(f)

    def _rm(self):
        for it in self.list.selectedItems():
            self.list.takeItem(self.list.row(it))
        self._refresh(self.list.get_file_list())

    def _start(self):
        if self.list.count() == 0:
            QMessageBox.warning(self, "No Files", "Add video files.")
            return
        # BD capacity is playback time at the fit-to-disc bitrate; warn if the
        # total runtime exceeds what fits at acceptable quality.
        if self.gauge.current_seconds > self.gauge.max_minutes * 60.0:
            r = QMessageBox.question(
                self, "Over Capacity",
                f"Total video runtime is {int(self.gauge.current_seconds // 60)} min, which "
                f"exceeds the ~{int(self.gauge.max_minutes)}-minute single-layer Blu-ray limit.\nContinue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if r != QMessageBox.StandardButton.Yes:
                return
        device = self.selected_device()
        if not self._confirm_blank_if_needed(device):
            QMessageBox.information(self, "Cancelled", "Blanking cancelled. Job not queued.")
            return
        temp_dir = Path(self.cfg.settings["temp_dir"])
        # Fit-to-disc output is about one full BD-25 (~23.5 GB) regardless of
        # runtime; authoring + image roughly doubles that on disk at peak.
        needed = BD25_BYTES
        free = disk_free_bytes(temp_dir)
        if free < needed * 2.0:
            r = QMessageBox.question(self, "Low Temp Space",
                                     f"Blu-ray authoring needs about {needed*2.0/1e9:.1f} GB free; "
                                     f"about {free/1e9:.1f} GB available.\nContinue?",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if r != QMessageBox.StandardButton.Yes:
                return
        job = Job(
            job_type=JobType.VIDEO_BD,
            files=[Path(self.list.item(i).text()) for i in range(self.list.count())],
            device=device,
            options=JobOptions(temp_dir=temp_dir, speed=self.cfg.settings.get("burn_speed", "Auto"),
                               auto_blank=self.cfg.drive_setting(device, "auto_blank_rw", True),
                               eject_after=self.cfg.drive_setting(device, "eject_after_burn", True)),
        )
        self._register_job(job)
        self.queue.enqueue(job)
        # Job start is shown non-modally in the status line and the Queue tab;
        # no blocking popup is needed.
        self.status.setText(f"Started: {job.display_name}")


class RipCDTab(BaseTab):
    def __init__(self, cfg: Config, tools: ToolFinder, queue: JobQueueService):
        super().__init__(cfg, tools, queue)
        from PyQt6.QtWidgets import QSpinBox
        lay = QVBoxLayout(self)
        title = QLabel("Rip Audio CD")
        title.setFont(QFont("Arial", 14, QFont.Weight.Bold))
        lay.addWidget(title)
        lay.addWidget(self._make_drive_row())
        opts = QGroupBox("Rip Options")
        form = QFormLayout()
        self.cbo_fmt = QComboBox()
        self.cbo_fmt.addItems(["MP3", "FLAC", "WAV"])
        self.cbo_fmt.setCurrentText(str(self.cfg.settings.get("audio_format", "MP3")))
        self.sp_bitrate = QSpinBox()
        self.sp_bitrate.setRange(128, 320)
        self.sp_bitrate.setValue(int(self.cfg.settings.get("audio_bitrate", 320)))
        self.sp_bitrate.setAccelerated(True)
        self.ed_out = QLineEdit(str(Path.home() / "Music"))
        self.ed_out.setReadOnly(True)
        b_out = QPushButton("Browse")
        b_out.clicked.connect(self._choose)
        row = QHBoxLayout()
        row.addWidget(self.ed_out)
        row.addWidget(b_out)
        form.addRow("Format:", self.cbo_fmt)
        form.addRow("MP3 Bitrate:", self.sp_bitrate)
        form.addRow("Output:", row)
        opts.setLayout(form)
        lay.addWidget(opts)
        self.btn_mb = QPushButton("Lookup Metadata (MusicBrainz)")
        self.btn_mb.clicked.connect(self._lookup_mb)
        lay.addWidget(self.btn_mb)
        self.btn = QPushButton("Start Ripping CD")
        self.btn.clicked.connect(self._start)
        lay.addWidget(self.btn)
        lay.addWidget(self.progress)
        lay.addWidget(self.status)
        lay.addStretch(1)
        self.track_titles: List[str] = []

    def _choose(self):
        d = QFileDialog.getExistingDirectory(self, "Select Output Directory")
        if d:
            self.ed_out.setText(d)

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
                self.tools = tools
                self.device = device

            def run(self):
                result = musicbrainz_lookup(self.tools, self.device)
                self.finished_data.emit(result)

        def done(md):
            progress.close()
            if not md:
                QMessageBox.information(self, "MusicBrainz", "No metadata found (or dependencies missing).")
                return
            self.track_titles = md.get("tracks") or []
            QMessageBox.information(self, "MusicBrainz", f"Found {len(self.track_titles)} track titles.")

        th = MBThread(self.tools, self.selected_device())
        th.finished_data.connect(done)
        th.start()
        self._mb_thread = th  # hold ref

    def _start(self):
        # Ripping route depends on platform. On Windows the native IOCTL ripper
        # reads CD audio with NO external tool (cdparanoia is a Unix tool with no
        # Windows build), so requiring it here was wrong and blocked Windows rips.
        # Only gate on cdparanoia for the CLI path (Linux/macOS).
        from ..services.platform_caps import is_windows
        if not is_windows():
            missing = self.tools.missing(["cdparanoia"])
            if missing:
                if self.cfg.settings.get("simulate_when_missing_tools", True):
                    r = QMessageBox.question(
                        self,
                        "Required Tool Missing",
                        "The required tool 'cdparanoia' is not installed.\n\n"
                        "Do you want to run a SIMULATED rip (for testing) instead?\n\n"
                        "Choose No to cancel so you can install cdparanoia first.",
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                    )
                    if r != QMessageBox.StandardButton.Yes:
                        return
                else:
                    QMessageBox.warning(
                        self,
                        "Required Tool Missing",
                        "The required tool 'cdparanoia' is not installed.\n"
                        "Install it and try again."
                    )
                    return
        out = Path(self.ed_out.text())
        job = Job(
            job_type=JobType.RIP,
            files=[],
            device=self.selected_device(),
            options=JobOptions(
                temp_dir=Path(self.cfg.settings["temp_dir"]),
                output_dir=out,
                rip_format=self.cbo_fmt.currentText(),
                rip_bitrate=self.sp_bitrate.value(),
                track_titles=self.track_titles if self.track_titles else None,
            ),
        )
        self._register_job(job)
        self.queue.enqueue(job)
        # Job start is shown non-modally in the status line and the Queue tab;
        # no blocking popup is needed.
        self.status.setText(f"Started: {job.display_name}")
