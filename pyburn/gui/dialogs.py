from __future__ import annotations
from pathlib import Path
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QLineEdit, QSpinBox, QCheckBox, QPushButton,
    QDialogButtonBox, QFileDialog, QTextEdit, QWidget, QHBoxLayout, QComboBox, QMessageBox
)
from ..core.config import Config


class SettingsDialog(QDialog):
    def __init__(self, cfg: Config, parent: QWidget | None = None):
        super().__init__(parent)
        self.cfg = cfg
        self.setWindowTitle("Settings")
        self._current_drive = None  # the drive whose per-drive settings are shown
        lay = QVBoxLayout(self)

        # ---- Per-drive section ---------------------------------------------
        from PyQt6.QtWidgets import QGroupBox, QLabel
        drive_box = QGroupBox("Drive settings (per drive)")
        dform = QFormLayout()
        self.cbo_dev = QComboBox()
        b_scan = QPushButton("Scan")
        b_scan.clicked.connect(self._rescan)
        drow = QHBoxLayout()
        drow.addWidget(self.cbo_dev, 1)
        drow.addWidget(b_scan)
        dform.addRow("Drive:", drow)

        self.lbl_default = QLabel("")
        self.btn_default = QPushButton("Set as default drive")
        self.btn_default.clicked.connect(self._set_default)
        defrow = QHBoxLayout()
        defrow.addWidget(self.lbl_default, 1)
        defrow.addWidget(self.btn_default)
        dform.addRow("", defrow)

        self.spd = QComboBox()
        self.spd.addItems(["Auto"] + [str(x) for x in [2, 4, 6, 8, 12, 16, 24, 32, 40, 48, 52]])
        dform.addRow("Burn Speed:", self.spd)
        self.chk_v = QCheckBox("Verify after burn")
        dform.addRow("", self.chk_v)
        self.chk_blank = QCheckBox("Auto-blank RW media")
        dform.addRow("", self.chk_blank)
        self.chk_eject = QCheckBox("Eject after burn")
        dform.addRow("", self.chk_eject)
        drive_box.setLayout(dform)
        lay.addWidget(drive_box)

        # Persist per-drive edits immediately as they change, into the currently
        # selected drive's block, so switching drives never loses edits.
        self.spd.currentTextChanged.connect(self._save_current_drive)
        self.chk_v.toggled.connect(self._save_current_drive)
        self.chk_blank.toggled.connect(self._save_current_drive)
        self.chk_eject.toggled.connect(self._save_current_drive)
        self.cbo_dev.currentIndexChanged.connect(self._on_drive_changed)

        # ---- Global section -------------------------------------------------
        global_box = QGroupBox("Global settings (all drives)")
        gform = QFormLayout()
        self.temp = QLineEdit(cfg.settings.get("temp_dir", str(Path.home() / "PyBurn_Temp")))
        b_browse = QPushButton("Browse")
        b_browse.clicked.connect(self._choose)
        trow = QHBoxLayout()
        trow.addWidget(self.temp, 1)
        trow.addWidget(b_browse)
        gform.addRow("Temp Directory:", trow)

        self.chk_sim = QCheckBox("Simulate when tools are missing")
        self.chk_sim.setChecked(bool(cfg.settings.get("simulate_when_missing_tools", True)))
        gform.addRow("", self.chk_sim)
        self.chk_mb = QCheckBox("Enable MusicBrainz lookup")
        self.chk_mb.setChecked(bool(cfg.settings.get("musicbrainz_enabled", True)))
        gform.addRow("", self.chk_mb)
        global_box.setLayout(gform)
        lay.addWidget(global_box)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

        # Populate drives from the shared cache (fast); this also selects the
        # default drive and loads its per-drive settings.
        self._load_drive_list(force=False)

    # -- drive list + per-drive load/save ------------------------------------
    def _load_drive_list(self, force: bool):
        from ..core.devices import get_devices, refresh_devices
        devs = refresh_devices() if force else get_devices()
        # Make sure each discovered drive has a settings block seeded.
        try:
            self.cfg.ensure_drives_registered([d.id for d in devs])
            self.cfg.save()
        except Exception:
            pass
        self.cbo_dev.blockSignals(True)
        self.cbo_dev.clear()
        cur = self.cfg.settings.get("default_device", "")
        sel = -1
        for i, d in enumerate(devs):
            self.cbo_dev.addItem(d.display, d.id)
            if d.id == cur:
                sel = i
        if not devs:
            self.cbo_dev.addItem(str(cur or "default"), cur or "")
            sel = 0
        if sel >= 0:
            self.cbo_dev.setCurrentIndex(sel)
        self.cbo_dev.blockSignals(False)
        self._on_drive_changed()

    def _rescan(self):
        self._load_drive_list(force=True)

    def _current_device_id(self):
        i = self.cbo_dev.currentIndex()
        if i < 0:
            return None
        return self.cbo_dev.itemData(i)

    def _on_drive_changed(self):
        dev = self._current_device_id()
        self._current_drive = dev
        if not dev:
            return
        opts = self.cfg.drive_options(dev)
        # Load without triggering the change-save handlers.
        for w in (self.spd, self.chk_v, self.chk_blank, self.chk_eject):
            w.blockSignals(True)
        self.spd.setCurrentText(str(opts.get("burn_speed", "Auto")))
        self.chk_v.setChecked(bool(opts.get("verify_after_burn", True)))
        self.chk_blank.setChecked(bool(opts.get("auto_blank_rw", True)))
        self.chk_eject.setChecked(bool(opts.get("eject_after_burn", True)))
        for w in (self.spd, self.chk_v, self.chk_blank, self.chk_eject):
            w.blockSignals(False)
        default_dev = self.cfg.settings.get("default_device", "")
        if dev == default_dev:
            self.lbl_default.setText("This is the default drive.")
            self.btn_default.setEnabled(False)
        else:
            self.lbl_default.setText("Not the default drive.")
            self.btn_default.setEnabled(True)

    def _save_current_drive(self):
        dev = self._current_drive
        if not dev:
            return
        self.cfg.set_drive_setting(dev, "burn_speed", self.spd.currentText())
        self.cfg.set_drive_setting(dev, "verify_after_burn", self.chk_v.isChecked())
        self.cfg.set_drive_setting(dev, "auto_blank_rw", self.chk_blank.isChecked())
        self.cfg.set_drive_setting(dev, "eject_after_burn", self.chk_eject.isChecked())
        self.cfg.save()

    def _set_default(self):
        dev = self._current_device_id()
        if dev:
            self.cfg.settings["default_device"] = dev
            self.cfg.save()
            self._on_drive_changed()

    def _choose(self):
        d = QFileDialog.getExistingDirectory(self, "Choose Temporary Directory")
        if d:
            self.temp.setText(d)

    def accept(self):
        # Per-drive settings are already saved live; here we commit the global
        # settings.
        self._save_current_drive()
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


class SetupDialog(QDialog):
    """First-run / on-demand setup and capability check.

    Shows what each feature can do on this machine, detects WSL2 on Windows,
    and offers to install the Linux toolchain inside an existing WSL2 distro.
    It does not try to enable the WSL feature itself or install a distro (that
    needs Administrator rights and usually a reboot); instead it tells the user
    the exact command to run for that one-time step.
    """
    def __init__(self, cfg: Config, tools, queue, parent: QWidget | None = None):
        super().__init__(parent)
        self.cfg = cfg
        self.tools = tools
        self.queue = queue
        self.setWindowTitle("PyBurn Studio - Setup")
        self.resize(760, 560)
        from PyQt6.QtWidgets import QTextEdit, QVBoxLayout, QHBoxLayout, QPushButton, QLabel
        lay = QVBoxLayout(self)
        self.header = QLabel("Checking what this system can do...")
        lay.addWidget(self.header)
        self.report = QTextEdit()
        self.report.setReadOnly(True)
        lay.addWidget(self.report)
        row = QHBoxLayout()
        self.btn_recheck = QPushButton("Re-check")
        self.btn_recheck.clicked.connect(self.refresh)
        row.addWidget(self.btn_recheck)
        self.btn_ffmpeg = QPushButton("Download ffmpeg")
        self.btn_ffmpeg.clicked.connect(self._download_ffmpeg)
        row.addWidget(self.btn_ffmpeg)
        # One button that does the ENTIRE DVD/Blu-ray setup: install a WSL2
        # distro if there is none, then install the Linux authoring tools and
        # tsMuxeR inside it. No manual PowerShell steps.
        self.btn_dvdbd = QPushButton("Enable DVD/Blu-ray (WSL2)")
        self.btn_dvdbd.clicked.connect(self._enable_dvd_bd)
        row.addWidget(self.btn_dvdbd)
        row.addStretch()
        self.btn_close = QPushButton("Close")
        self.btn_close.clicked.connect(self.accept)
        row.addWidget(self.btn_close)
        lay.addLayout(row)
        self._busy = False
        self.refresh()

    def refresh(self):
        import platform
        from pyburn.services.platform_caps import CapabilityResolver, Engine, is_windows
        resolver = CapabilityResolver(self.tools, self.queue.wsl)
        caps = resolver.resolve_all()
        label = {"DATA": "Data disc", "AUDIO": "Audio CD", "VIDEO_DVD": "Video DVD",
                 "VIDEO_BD": "Blu-ray", "RIP": "Rip CD"}
        html = []
        html.append(f"<b>Platform:</b> {platform.system()} {platform.release()}<br><br>")
        html.append("<b>Feature readiness</b><br>")
        for key in ["DATA", "AUDIO", "VIDEO_DVD", "VIDEO_BD", "RIP"]:
            c = caps[key]
            if c.available and c.engine != Engine.SIM:
                color = "#2ECC71"; state = "ready"
            elif c.engine == Engine.SIM:
                color = "#E0A030"; state = "simulation only"
            else:
                color = "#E74C3C"; state = "not available"
            html.append(f"&nbsp;&nbsp;<b>{label[key]}:</b> "
                        f"<span style='color:{color}'>{state}</span> &mdash; {c.detail}<br>")
        if is_windows():
            wsl = self.queue.wsl
            wsl.detect()
            html.append("<br><b>DVD / Blu-ray (WSL2) status</b><br>")
            if wsl.info.available:
                html.append(f"&nbsp;&nbsp;Distro: {wsl.info.default_distro or 'unknown'} "
                            f"(WSL2: {'yes' if wsl.info.version2 else 'no'})<br>")
                present = wsl.refresh_tool_presence()
                for name, ok in present.items():
                    mark = "installed" if ok else "not yet"
                    html.append(f"&nbsp;&nbsp;&nbsp;&nbsp;{name}: {mark}<br>")
                html.append("&nbsp;&nbsp;If any tool shows 'not yet', click "
                            "<b>Enable DVD/Blu-ray (WSL2)</b> to install them.<br>")
            elif wsl.info.platform_present:
                html.append("&nbsp;&nbsp;WSL2 is installed but has no Linux distribution yet.<br>"
                            "&nbsp;&nbsp;Click <b>Enable DVD/Blu-ray (WSL2)</b> and the app will "
                            "install one and set up the tools. No manual steps.<br>")
            else:
                html.append("&nbsp;&nbsp;WSL2 is not installed. Click "
                            "<b>Enable DVD/Blu-ray (WSL2)</b>; if Windows needs the WSL feature "
                            "enabled first, the app will start that (you approve one Windows "
                            "prompt and may need one reboot), then finish the rest.<br>")
            html.append("<br>Native Windows notes: data discs and audio CDs burn through a "
                        "built-in SPTI/MMC engine that talks to the drive directly, and ripping "
                        "reads through IOCTL, so no external tools are needed for these. "
                        "First-time setup: nothing is required to burn or rip. Optionally click "
                        "<b>Download ffmpeg</b> to improve audio decode and rip encoding, and only "
                        "if you want Video DVD or Blu-ray, click <b>Enable DVD/Blu-ray (WSL2)</b> "
                        "to install the Linux authoring tools.")
            self.btn_dvdbd.setEnabled(not self._busy)
        else:
            html.append("<br>On Linux all features run through the standard command-line tools. "
                        "Install any shown as unavailable via your package manager.")
            self.btn_dvdbd.setEnabled(False)
        self.header.setText("Setup and capability check")
        self.report.setHtml("".join(html))
        # Remember that setup has been shown so first-run only auto-opens once.
        self.cfg.settings["setup_completed"] = True
        self.cfg.save()

    def _enable_dvd_bd(self):
        """One-click DVD/Blu-ray setup, entirely in a background thread.

        Steps, skipping any already done:
          1. If the WSL platform is missing, launch the elevated wsl --install
             and tell the user to reboot (that one step needs Windows elevation
             and a reboot; nothing else does).
          2. If there is no distro, install one with --no-launch (no interactive
             user setup).
          3. apt-install the authoring tools as root, non-interactively.
          4. Fetch tsMuxeR into the distro.
        Live output streams into the report box; the UI never freezes.
        """
        from PyQt6.QtWidgets import QMessageBox
        from PyQt6.QtCore import QThread, pyqtSignal
        from pyburn.services.installer import launch_wsl_install_elevated, install_tsmuxer_in_wsl
        if self._busy:
            return
        wsl = self.queue.wsl

        def setup_log(s):
            self.report.append(s)
            try:
                import os, sys, datetime
                base = os.path.dirname(os.path.abspath(sys.executable)) if getattr(sys, "frozen", False) else os.getcwd()
                with open(os.path.join(base, "pyburn_setup.log"), "a", encoding="utf-8") as f:
                    f.write(f"{datetime.datetime.now().isoformat(timespec='seconds')}  {s}\n")
            except Exception:
                pass

        wsl.detect(on_log=setup_log)

        # Case 1: WSL feature absent -> run the elevated one-time install.
        if not wsl.info.platform_present:
            r = QMessageBox.question(
                self, "Install WSL2",
                "WSL2 is not installed on this machine. The app will run the "
                "Windows installer for it now; approve the Windows security "
                "prompt. When it finishes you must REBOOT once, then open Setup "
                "and click this button again to finish automatically.\n\nProceed?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if r != QMessageBox.StandardButton.Yes:
                return
            self.report.append("<br><b>Installing WSL2 (approve the prompt, then reboot)...</b><br>")
            launch_wsl_install_elevated(on_log=setup_log)
            QMessageBox.information(self, "Reboot needed",
                                   "When WSL2 finishes installing, REBOOT, then open Setup and "
                                   "click Enable DVD/Blu-ray (WSL2) again.")
            return

        # Cases 2-4 run in the background: install distro (if needed) + tools.
        self._busy = True
        self.btn_dvdbd.setEnabled(False)
        self.btn_ffmpeg.setEnabled(False)
        self.report.append("<br><b>Setting up DVD/Blu-ray support in WSL2...</b><br>")

        dlg = self

        class SetupThread(QThread):
            logline = pyqtSignal(str)
            done = pyqtSignal(bool, str)
            need_feature_install = pyqtSignal()

            def run(self):
                def log(s):
                    self.logline.emit(str(s))
                try:
                    # Install a distro if none present.
                    if not wsl.info.available:
                        log("No Linux distribution found; installing Ubuntu (this downloads a few hundred MB)...")
                        ok = wsl.install_distro("Ubuntu", on_log=log)
                        if not ok:
                            # If the distro step failed because the WSL feature
                            # itself is not installed, ask the main thread to run
                            # the elevated feature install (UAC must be on the UI
                            # thread).
                            if getattr(wsl, "_feature_absent", False):
                                self.need_feature_install.emit()
                                return
                            self.done.emit(False,
                                "Could not install a WSL2 distribution automatically. "
                                "See pyburn_setup.log next to the program for the exact reason.")
                            return
                    # Install the apt toolchain (root, non-interactive).
                    log("Installing Linux disc tools...")
                    if not wsl.provision(on_out=log, on_err=log):
                        self.done.emit(False, "Installing the Linux tools failed; see the log.")
                        return
                    # tsMuxeR (not in apt) for Blu-ray. Honor the real result.
                    log("Installing tsMuxeR for Blu-ray authoring...")
                    tsmux_ok = install_tsmuxer_in_wsl(wsl, on_log=log)
                    if tsmux_ok:
                        self.done.emit(True, "DVD and Blu-ray support is ready.")
                    else:
                        self.done.emit(False,
                            "DVD is ready, but tsMuxeR (needed only for Blu-ray) did not "
                            "install. Look for a line starting with TSMUX_FAIL in the log "
                            "above for the exact reason. Everything except Blu-ray works.")
                except Exception as e:
                    self.done.emit(False, f"Setup error: {e}")

        def on_log(s):
            self.report.append(s)
            # Also write every line to a log file next to the exe so a failed
            # run can be diagnosed from the file instead of a screenshot.
            try:
                import os, sys, datetime
                base = os.path.dirname(os.path.abspath(sys.executable)) if getattr(sys, "frozen", False) else os.getcwd()
                with open(os.path.join(base, "pyburn_setup.log"), "a", encoding="utf-8") as f:
                    f.write(f"{datetime.datetime.now().isoformat(timespec='seconds')}  {s}\n")
            except Exception:
                pass

        def on_done(ok, msg):
            self._busy = False
            self.btn_ffmpeg.setEnabled(True)
            self.btn_dvdbd.setEnabled(True)
            # Record the final result line too.
            try:
                import os, sys, datetime
                base = os.path.dirname(os.path.abspath(sys.executable)) if getattr(sys, "frozen", False) else os.getcwd()
                with open(os.path.join(base, "pyburn_setup.log"), "a", encoding="utf-8") as f:
                    f.write(f"{datetime.datetime.now().isoformat(timespec='seconds')}  RESULT ok={ok}: {msg}\n")
            except Exception:
                pass
            if ok:
                QMessageBox.information(self, "DVD/Blu-ray", msg)
            else:
                QMessageBox.warning(self, "DVD/Blu-ray", msg + "\n\nA full log was written to pyburn_setup.log next to the program.")
            self.refresh()

        def on_need_feature_install():
            self._busy = False
            self.btn_ffmpeg.setEnabled(True)
            self.btn_dvdbd.setEnabled(True)
            r = QMessageBox.question(
                self, "Install WSL2",
                "The Linux distribution could not be installed because the WSL2 "
                "Windows feature is not installed yet. The app will run the "
                "Windows installer for it now; approve the security prompt, then "
                "REBOOT once and click Enable DVD/Blu-ray (WSL2) again.\n\nProceed?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if r == QMessageBox.StandardButton.Yes:
                self.report.append("<br><b>Installing WSL2 (approve the prompt, then reboot)...</b><br>")
                launch_wsl_install_elevated(on_log=setup_log)
                QMessageBox.information(self, "Reboot needed",
                                       "When WSL2 finishes installing, REBOOT, then open Setup "
                                       "and click Enable DVD/Blu-ray (WSL2) again.")

        self._setup_thread = SetupThread()
        self._setup_thread.logline.connect(on_log)
        self._setup_thread.done.connect(on_done)
        self._setup_thread.need_feature_install.connect(on_need_feature_install)
        self._setup_thread.start()

    def _download_ffmpeg(self):
        from PyQt6.QtWidgets import QMessageBox
        from PyQt6.QtCore import QThread, pyqtSignal
        from pyburn.services.installer import download_ffmpeg, tools_dir
        self.report.append("<br><b>Downloading ffmpeg...</b><br>")
        self.btn_ffmpeg.setEnabled(False)

        dlg = self

        class DlThread(QThread):
            done = pyqtSignal(object)
            logline = pyqtSignal(str)

            def run(self):
                path = download_ffmpeg(on_log=lambda s: self.logline.emit(s))
                self.done.emit(path)

        def on_log(s):
            self.report.append(s)

        def on_done(path):
            self.btn_ffmpeg.setEnabled(True)
            # Point the tool finder at tools\ so the new ffmpeg is picked up now.
            try:
                self.tools.add_search_dir(str(tools_dir()))
            except Exception:
                pass
            if path:
                QMessageBox.information(self, "ffmpeg", f"ffmpeg installed:\n{path}")
            else:
                QMessageBox.warning(self, "ffmpeg",
                                    "Download did not complete. Check your internet connection, "
                                    "or install ffmpeg manually and place it in the tools folder.")
            self.refresh()

        self._dl_thread = DlThread()
        self._dl_thread.logline.connect(on_log)
        self._dl_thread.done.connect(on_done)
        self._dl_thread.start()
