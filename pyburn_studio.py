from __future__ import annotations
import sys
import argparse

# Frozen-build COM stability (mirrors the sibling audioctl app's compat shim).
# comtypes uses internal _post_coinit modules to finalize COM types and provide
# correct cleanup (__del__ -> Release()). In a PyInstaller onefile build these
# can be missed by the bundler or first imported during interpreter shutdown,
# which causes noisy or hard COM-cleanup crashes. Importing them here at startup
# makes them visible to the bundler and avoids the late-import timing. Guarded so
# non-Windows / source runs are unaffected.
try:
    import comtypes._post_coinit  # noqa: F401
    import comtypes._post_coinit.unknwn  # noqa: F401
except Exception:
    pass

from PyQt6.QtWidgets import QApplication, QMessageBox
from pyburn.core.config import Config
from pyburn.core.tools import ToolFinder
from pyburn.gui.main_window import MainWindow
from pyburn.style import APP_STYLESHEET
from pyburn.resources import app_icon, set_windows_app_id
from pyburn.services.installer import tools_dir


def run_gui():
    # On Windows, claim a distinct app identity BEFORE the QApplication and any
    # window exist, so the taskbar uses our icon instead of the generic Python
    # one and groups the app under its own button.
    set_windows_app_id()
    app = QApplication(sys.argv)
    app.setApplicationName("PyBurn Studio")
    app.setStyleSheet(APP_STYLESHEET)
    # App-level icon: every top-level window and message box inherits this
    # unless it sets its own, so this covers dialogs and popups automatically.
    icon = app_icon()
    if icon is not None:
        app.setWindowIcon(icon)
    cfg = Config()
    tools = ToolFinder()
    # Search the local tools\ folder (where ffmpeg is downloaded) in addition
    # to PATH, so a downloaded ffmpeg is picked up without a system install.
    try:
        tools.add_search_dir(str(tools_dir()))
    except Exception:
        pass
    # NOTE: no global startup tool check. Which tools are needed depends on the
    # platform and the job (on Windows most jobs use native engines and need no
    # external tools at all). The Setup and About screens report real per-feature
    # readiness; a blanket PATH check for mkisofs/ffmpeg at launch was obsolete
    # and fired a false "missing tools" popup on Windows where those are not
    # required.
    win = MainWindow(cfg, tools)
    win.show()
    win.maybe_first_run_setup()
    sys.exit(app.exec())


def self_test():
    from PyQt6.QtWidgets import QApplication
    from pyburn.services.queue import JobQueueService
    from pyburn.core.jobs import Job, JobType, JobOptions
    from pathlib import Path
    import time
    app = QApplication.instance() or QApplication(sys.argv)
    print("Running self-test (simulation backend + queue)...")
    cfg = Config()
    cfg.settings["simulate_when_missing_tools"] = True
    tools = ToolFinder()
    q = JobQueueService(tools, cfg.settings)
    results = []
    q.sig_job_finished.connect(lambda jid, ok, msg: (results.append(ok), print("Finished:", jid, ok, msg)))
    dummy = Path.cwd() / "dummy.txt"
    try:
        dummy.write_text("x")
    except Exception:
        pass
    q.enqueue(Job(job_type=JobType.DATA, files=[dummy], device="/dev/sr0",
                  options=JobOptions(temp_dir=Path(cfg.settings["temp_dir"]), verify=True, speed="Auto", volume_label="TEST")))
    q.enqueue(Job(job_type=JobType.AUDIO, files=[dummy], device="/dev/sr0",
                  options=JobOptions(temp_dir=Path(cfg.settings["temp_dir"]), speed="Auto")))
    q.enqueue(Job(job_type=JobType.RIP, device="/dev/sr0",
                  options=JobOptions(temp_dir=Path(cfg.settings["temp_dir"]), output_dir=Path.cwd() / "out", rip_format="MP3", rip_bitrate=192)))
    q.enqueue(Job(job_type=JobType.VIDEO_DVD, files=[dummy], device="/dev/sr0",
                  options=JobOptions(temp_dir=Path(cfg.settings["temp_dir"]), speed="Auto")))
    q.enqueue(Job(job_type=JobType.VIDEO_BD, files=[dummy], device="/dev/sr0",
                  options=JobOptions(temp_dir=Path(cfg.settings["temp_dir"]), speed="Auto")))
    start = time.time()
    timeout = 30.0
    expected = 5
    while len(results) < expected:
        app.processEvents()
        time.sleep(0.05)
        if time.time() - start > timeout:
            print("ERROR: Self-test timed out; cancelling current job and shutting down.")
            q.cancel_current()
            # Give the queue a moment to unwind the cancelled job cleanly.
            deadline = time.time() + 3.0
            while q.get_list() and time.time() < deadline:
                app.processEvents()
                time.sleep(0.05)
            break
    try:
        dummy.unlink()
    except Exception:
        pass
    try:
        (Path.cwd() / "out").rmdir()
    except Exception:
        pass
    if len(results) < expected or not all(results):
        print("FAIL: Self-test did not complete successfully.")
        return 1
    print("Self-test passed.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PyBurn Studio")
    parser.add_argument("--self-test", action="store_true", help="Run built-in non-destructive self-tests")
    args = parser.parse_args()
    if args.self_test:
        sys.exit(self_test())
    run_gui()
