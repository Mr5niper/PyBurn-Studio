from __future__ import annotations
from pathlib import Path
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QLineEdit, QSpinBox, QCheckBox, QPushButton,
    QDialogButtonBox, QFileDialog, QTextEdit, QWidget, QHBoxLayout, QComboBox, QMessageBox
)
from PyQt6.QtCore import QTimer
from ..core.config import Config
from ..core.devices import DeviceScanner
class SettingsDialog(QDialog):
    def __init__(self, cfg: Config, parent: QWidget | None = None):
        super().__init__(parent)
        self.cfg = cfg
        self.setWindowTitle("Settings")
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.cbo_dev = QComboBox()
        self._populate()
        b_scan = QPushButton("Scan")
        b_scan.clicked.connect(self._populate)
        row = QHBoxLayout()
        row.addWidget(self.cbo_dev)
        row.addWidget(b_scan)
        form.addRow("Disc Device:", row)
        self.spd = QComboBox()
        self.spd.addItems(["Auto"] + [str(x) for x in [2,4,6,8,12,16,24,32,40,48,52]])
        self.spd.setCurrentText(str(self.cfg.settings.get("burn_speed", "Auto")))
        form.addRow("Default Burn Speed:", self.spd)
        self.temp = QLineEdit(cfg.settings.get("temp_dir", str(Path.home() / "PyBurn_Temp")))
        b_browse = QPushButton("Browse")
        b_browse.clicked.connect(self._choose)
        trow = QHBoxLayout()
        trow.addWidget(self.temp)
        trow.addWidget(b_browse)
        form.addRow("Temp Directory:", trow)
        self.chk_v = QCheckBox("Verify after burn")
        self.chk_v.setChecked(bool(cfg.settings.get("verify_after_burn", True)))
        form.addRow("", self.chk_v)
        self.chk_blank = QCheckBox("Auto-blank RW media")
        self.chk_blank.setChecked(bool(cfg.settings.get("auto_blank_rw", True)))
        form.addRow("", self.chk_blank)
        self.chk_eject = QCheckBox("Eject after burn")
        self.chk_eject.setChecked(bool(cfg.settings.get("eject_after_burn", True)))
        form.addRow("", self.chk_eject)
        self.chk_sim = QCheckBox("Simulate when tools are missing")
        self.chk_sim.setChecked(bool(cfg.settings.get("simulate_when_missing_tools", True)))
        form.addRow("", self.chk_sim)
        self.chk_mb = QCheckBox("Enable MusicBrainz lookup")
        self.chk_mb.setChecked(bool(cfg.settings.get("musicbrainz_enabled", True)))
        form.addRow("", self.chk_mb)
        lay.addLayout(form)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
    def _populate(self):
        # Non-blocking device scan with indicator
        self.cbo_dev.clear()
        self.cbo_dev.addItem("Scanning devices...")
        QTimer.singleShot(100, self._scan_async)
    def _scan_async(self):
        try:
            devs = DeviceScanner().scan_devices()
        except Exception:
            devs = []
        self.cbo_dev.clear()
        cur = self.cfg.settings.get("default_device", "")
        idx = -1
        for i, d in enumerate(devs):
            self.cbo_dev.addItem(d.display, d.id)
            if d.id == cur:
                idx = i
        if idx >= 0:
            self.cbo_dev.setCurrentIndex(idx)
    def _choose(self):
        d = QFileDialog.getExistingDirectory(self, "Choose Temporary Directory")
        if d:
            self.temp.setText(d)
    def accept(self):
        i = self.cbo_dev.currentIndex()
        if i >= 0:
            device_id = self.cbo_dev.itemData(i)
            # Ensure we have a valid device ID and it's not the scanning placeholder
            if device_id is not None and self.cbo_dev.itemText(i) != "Scanning devices...":
                self.cfg.settings["default_device"] = device_id
        self.cfg.settings["burn_speed"] = self.spd.currentText()
        temp_path = Path(self.temp.text().strip())
        try:
            temp_path.mkdir(parents=True, exist_ok=True)
            test_file = temp_path / ".pyburn_test"
            test_file.write_text("test")
            test_file.unlink()
        except Exception as e:
            QMessageBox.warning(self, "Invalid Temp Directory",
                                f"Cannot write to temp directory:\n{temp_path}\n\nError: {e}")
            return
        self.cfg.settings["temp_dir"] = str(temp_path)
        self.cfg.settings["verify_after_burn"] = self.chk_v.isChecked()
        self.cfg.settings["auto_blank_rw"] = self.chk_blank.isChecked()
        self.cfg.settings["eject_after_burn"] = self.chk_eject.isChecked()
        self.cfg.settings["simulate_when_missing_tools"] = self.chk_sim.isChecked()
        self.cfg.settings["musicbrainz_enabled"] = self.chk_mb.isChecked()
        self.cfg.save()
        super().accept()
class LogDialog(QDialog):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Job Log")
        self.resize(900, 480)
        lay = QVBoxLayout(self)
        self.text = QTextEdit()
        self.text.setReadOnly(True)
        lay.addWidget(self.text)
    def append(self, line: str):
        self.text.append(line)
