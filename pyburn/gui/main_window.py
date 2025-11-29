from __future__ import annotations
from PyQt6.QtWidgets import QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTabWidget, QSplitter, QMessageBox, QDialog
from PyQt6.QtGui import QShortcut
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QKeySequence
from ..core.config import Config
from ..core.tools import ToolFinder
from ..services.queue import JobQueueService
from .dialogs import SettingsDialog, LogDialog
from .tabs import DataBurnTab, AudioCDTab, VideoDVDTab, VideoBDTab, RipCDTab
from .widgets import JobQueueWidget, HistoryWidget
from pyburn import __version__
class MainWindow(QMainWindow):
    def __init__(self, cfg: Config, tools: ToolFinder):
        super().__init__()
        self.cfg = cfg
        self.tools = tools
        self.queue = JobQueueService(tools, cfg.settings)
        self.setWindowTitle(f"PyBurn Studio v{__version__}")
        self.resize(1200, 860)
        self.log_dialog = LogDialog(self)
        self.queue.sig_log_line.connect(self._log)
        cw = QWidget(); self.setCentralWidget(cw)
        lay = QVBoxLayout(cw)
        header = QHBoxLayout()
        title = QLabel("PyBurn Studio"); title.setFont(QFont("Arial", 18, QFont.Weight.Bold))
        header.addWidget(title); header.addStretch()
        b_settings = QPushButton("Settings"); b_settings.clicked.connect(self._settings)
        b_logs = QPushButton("Job Logs"); b_logs.clicked.connect(self.log_dialog.show)
        b_about = QPushButton("About"); b_about.clicked.connect(self._about)
        header.addWidget(b_settings); header.addWidget(b_logs); header.addWidget(b_about)
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
    def _about(self):
        versions = ToolFinder().versions()
        lines = "\n".join([f"{k}: {'missing' if v is None else 'present'}" for k, v in versions.items()])
        QMessageBox.information(self, "About PyBurn Studio", f"PyBurn Studio v{__version__}\n\nDetected tools:\n{lines}")
    def _log(self, job_id: str, line: str):
        self.log_dialog.append(f"[{job_id}] {line}")