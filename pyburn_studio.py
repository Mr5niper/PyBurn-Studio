from __future__ import annotations
import sys
import argparse
from PyQt6.QtWidgets import QApplication, QMessageBox
from pyburn.core.config import Config
from pyburn.core.tools import ToolFinder
from pyburn.gui.main_window import MainWindow
from pyburn.style import APP_STYLESHEET
def run_gui():
    app = QApplication(sys.argv)
    app.setApplicationName("PyBurn Studio")
    app.setStyleSheet(APP_STYLESHEET)
    cfg = Config()
    tools = ToolFinder()
    if not cfg.settings.get("simulate_when_missing_tools", True):
        missing = tools.missing(["ffmpeg", "mkisofs"])
        if missing:
            QMessageBox.warning(None, "Missing Tools",
                                "Missing required tools: " + ", ".join(missing) +
                                "\nInstall them or enable simulation in Settings.")
    win = MainWindow(cfg, tools)
    win.show()
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
    try: dummy.write_text("x")
    except Exception: pass
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
    while q.get_list() or (q._thread and q._thread.isRunning()):
        app.processEvents()
        time.sleep(0.05)
        if time.time() - start > timeout:
            print("ERROR: Self-test timed out; cancelling current job and shutting down.")
            q.cancel_current()
            if q._thread:
                q._thread.quit()
                q._thread.wait(2000)
            break
    try: dummy.unlink()
    except Exception: pass
    try: (Path.cwd() / "out").rmdir()
    except Exception: pass
    if len(results) < 5 or not all(results):
        print("FAIL: Self-test did not complete successfully.")
        return 1
    print("✓ Self-test passed.")
    return 0
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PyBurn Studio")
    parser.add_argument("--self-test", action="store_true", help="Run built-in non-destructive self-tests")
    args = parser.parse_args()
    if args.self_test:
        sys.exit(self_test())
    run_gui()