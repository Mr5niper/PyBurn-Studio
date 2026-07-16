from __future__ import annotations
from PyQt6.QtWidgets import QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTabWidget, QSplitter, QMessageBox, QDialog
from PyQt6.QtGui import QShortcut
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QKeySequence
from ..core.config import Config
from ..core.tools import ToolFinder
from ..services.queue import JobQueueService
from .dialogs import SettingsDialog, LogDialog, SetupDialog
from .tabs import DataBurnTab, AudioCDTab, VideoDVDTab, VideoBDTab, RipCDTab
from .widgets import JobQueueWidget, HistoryWidget
from pyburn import __version__
from pyburn.resources import app_icon


class MainWindow(QMainWindow):
    def __init__(self, cfg: Config, tools: ToolFinder):
        super().__init__()
        self.cfg = cfg
        self.tools = tools
        self.queue = JobQueueService(tools, cfg.settings)
        self.setWindowTitle(f"PyBurn Studio v{__version__}")
        _icon = app_icon()
        if _icon is not None:
            self.setWindowIcon(_icon)
        self.resize(1200, 860)
        self.log_dialog = LogDialog(self)
        self.queue.sig_log_line.connect(self._log)
        cw = QWidget()
        self.setCentralWidget(cw)
        lay = QVBoxLayout(cw)
        header = QHBoxLayout()
        title = QLabel("PyBurn Studio")
        title.setFont(QFont("Arial", 18, QFont.Weight.Bold))
        header.addWidget(title)
        header.addStretch()
        b_settings = QPushButton("Settings")
        b_settings.clicked.connect(self._settings)
        b_setup = QPushButton("Setup")
        b_setup.clicked.connect(self._setup)
        b_logs = QPushButton("Job Logs")
        b_logs.clicked.connect(self.log_dialog.show)
        b_about = QPushButton("About")
        b_about.clicked.connect(self._about)
        header.addWidget(b_settings)
        header.addWidget(b_setup)
        header.addWidget(b_logs)
        header.addWidget(b_about)
        lay.addLayout(header)
        splitter = QSplitter(Qt.Orientation.Vertical)
        tabs = QTabWidget()
        tabs.addTab(DataBurnTab(self.cfg, self.tools, self.queue), "Data Disc")
        tabs.addTab(AudioCDTab(self.cfg, self.tools, self.queue), "Audio CD")
        tabs.addTab(VideoDVDTab(self.cfg, self.tools, self.queue), "Video DVD")
        tabs.addTab(VideoBDTab(self.cfg, self.tools, self.queue), "Blu-ray")
        tabs.addTab(RipCDTab(self.cfg, self.tools, self.queue), "Rip CD")
        splitter.addWidget(tabs)
        queue_panel = QTabWidget()
        queue_panel.addTab(JobQueueWidget(self.queue), "Queue")
        queue_panel.addTab(HistoryWidget(self.queue.history, self.queue), "History")
        splitter.addWidget(queue_panel)
        splitter.setSizes([650, 210])
        lay.addWidget(splitter)
        # Shortcuts
        QShortcut(QKeySequence("Ctrl+Q"), self, activated=self.close)
        QShortcut(QKeySequence("Ctrl+L"), self, activated=self.log_dialog.show)
        QShortcut(QKeySequence("F1"), self, activated=self._about)
        self.statusBar().showMessage(f"Ready. Device: {self.cfg.settings.get('default_device')}")

    def _settings(self):
        dlg = SettingsDialog(self.cfg, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.statusBar().showMessage(f"Settings updated. Device: {self.cfg.settings.get('default_device')}")

    def _setup(self):
        dlg = SetupDialog(self.cfg, self.tools, self.queue, self)
        dlg.exec()

    def maybe_first_run_setup(self):
        # Auto-open Setup once, the first time the app is launched, so a new user
        # immediately sees what works and how to enable the rest.
        if not self.cfg.settings.get("setup_completed", False):
            self._setup()

    def _about(self):
        # Show a per-FEATURE capability summary rather than a raw list of every
        # tool marked missing. On Windows this reflects the real engine that
        # will run each job (native tools, IMAPI2, IOCTL, or WSL2), so the user
        # sees "Data disc: ready via IMAPI2" instead of a wall of red.
        from pyburn.services.platform_caps import CapabilityResolver, Engine, is_windows
        resolver = CapabilityResolver(self.tools, self.queue.wsl)
        caps = resolver.resolve_all()
        label = {
            "DATA": "Data disc",
            "AUDIO": "Audio CD",
            "VIDEO_DVD": "Video DVD",
            "VIDEO_BD": "Blu-ray",
            "RIP": "Rip CD",
        }
        lines = []
        for key in ["DATA", "AUDIO", "VIDEO_DVD", "VIDEO_BD", "RIP"]:
            c = caps[key]
            if c.available and c.engine != Engine.SIM:
                lines.append(f"{label[key]}: ready ({c.detail})")
            elif c.engine == Engine.SIM:
                lines.append(f"{label[key]}: simulation only ({c.detail})")
            else:
                lines.append(f"{label[key]}: not available ({c.detail})")
        body = f"PyBurn Studio v{__version__}\n\nWhat this system can do right now:\n\n" + "\n".join(lines)
        if is_windows():
            body += ("\n\nOn Windows, data discs, audio CDs, blanking and ripping run natively "
                     "with no extra tools. DVD-Video and Blu-ray authoring use a WSL2 Linux "
                     "environment to build the image, then Windows burns it. Use Setup to check "
                     "or install what is needed.")
        QMessageBox.information(self, "About PyBurn Studio", body)

    def _log(self, job_id: str, line: str):
        self.log_dialog.append(f"[{job_id}] {line}")
