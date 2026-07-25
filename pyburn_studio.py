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


def cli_burn_data(args):
    """One-shot data burn in a DEDICATED process, with REAL per-sector progress.

    Two-stage, and neither stage overlaps a COM call with a disc write:
      1) Build an ISO image file from the selected files/folders using IMAPI2
         authoring (MsftFileSystemImage). This is COM, but it finishes BEFORE any
         writing begins, so it cannot collide with a write.
      2) Burn that ISO to the blank CD-R with SPTIWriter, which talks to the drive
         directly via IOCTL_SCSI_PASS_THROUGH_DIRECT and raw MMC commands (NO COM,
         NO IMAPI2). Because we issue every WRITE(10) ourselves, progress is exact
         (sectors written / total) and nothing can overlap the write.

    Stdout line protocol the parent GUI parses:
        PROGRESS <int 0-100>
        STATUS <text>
        LOG <text>
        RESULT OK
        RESULT FAIL <text>
    """
    import sys as _sys
    import tempfile as _tempfile
    from pathlib import Path as _Path

    def emit(line):
        try:
            _sys.stdout.write(line + "\n")
            _sys.stdout.flush()
        except Exception:
            pass

    def on_status(s):
        emit("STATUS " + str(s))

    def on_progress(p):
        try:
            emit("PROGRESS " + str(int(p)))
        except Exception:
            pass

    def on_log(m):
        emit("LOG " + str(m))

    # COM is needed only for stage 1 (ISO authoring). Initialize MTA like the
    # worker does so IMAPI2 does not deadlock without a message pump.
    com_ready = False
    try:
        import comtypes
        try:
            comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
        except Exception:
            import ctypes
            ctypes.windll.ole32.CoInitializeEx(None, 0x0)
        com_ready = True
    except Exception as e:
        emit("LOG COM init (MTA) warning: " + str(e))

    iso_tmp = None
    try:
        from pyburn.services.imapi2_backend import IMAPI2Backend
        from pyburn.services.spti_writer import SPTIWriter

        files = [_Path(p) for p in args.file]
        temp_dir = _Path(args.temp_dir) if args.temp_dir else _Path(_tempfile.gettempdir())
        temp_dir.mkdir(parents=True, exist_ok=True)
        iso_tmp = temp_dir / ("pyburn_%d.iso" % (abs(hash(tuple(str(f) for f in files))) % 10_000_000))

        # Stage 1: author the ISO (COM, before any write).
        backend = IMAPI2Backend()
        backend.build_iso(files, iso_tmp, args.volume, on_status, on_log)

        # COM is done; we do not need it for the SPTI write. Uninitialize now so
        # nothing COM is even alive during the write.
        if com_ready:
            try:
                import comtypes
                comtypes.CoUninitialize()
            except Exception:
                pass
            com_ready = False

        # Convert the CD x-multiplier speed to kbytes/sec for SET CD SPEED.
        # 1x CD = 176.4 kB/s (1000-byte kB per MMC). 0 = fastest.
        speed_kbps = 0
        try:
            s = str(args.speed).strip().lower()
            if s not in ("", "auto"):
                speed_kbps = int(round(float(s) * 176))
        except Exception:
            speed_kbps = 0

        # Stage 2: burn via SPTI/MMC (no COM), real per-sector progress.
        writer = SPTIWriter()
        writer.burn_iso(iso_tmp, args.device, on_status, on_progress, on_log,
                        speed_kbps=speed_kbps, dummy=args.dummy, eject_after=args.eject)

        emit("PROGRESS 100")
        emit("RESULT OK")
        return 0
    except Exception as e:
        emit("RESULT FAIL " + str(e))
        return 1
    finally:
        if com_ready:
            try:
                import comtypes
                comtypes.CoUninitialize()
            except Exception:
                pass
        # Clean up the temp ISO.
        try:
            if iso_tmp is not None and _Path(iso_tmp).exists():
                _Path(iso_tmp).unlink()
        except Exception:
            pass



if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PyBurn Studio")
    parser.add_argument("--self-test", action="store_true", help="Run built-in non-destructive self-tests")
    sub = parser.add_subparsers(dest="cli_command")
    p_bd = sub.add_parser("cli-burn-data", help=argparse.SUPPRESS)
    p_bd.add_argument("--file", action="append", required=True, help="File or folder to add (repeatable)")
    p_bd.add_argument("--device", required=True, help="Target drive, e.g. H:")
    p_bd.add_argument("--volume", default="DATA_DISC", help="Volume label")
    p_bd.add_argument("--temp-dir", default="", help="Temp directory")
    p_bd.add_argument("--speed", default="Auto", help="Burn speed (Auto or x-multiplier)")
    p_bd.add_argument("--auto-blank", action="store_true", help="Erase rewritable media if not blank")
    p_bd.add_argument("--eject", action="store_true", help="Eject after burn")
    p_bd.add_argument("--dummy", action="store_true", help="Simulate write (no disc written)")
    args = parser.parse_args()
    if args.self_test:
        sys.exit(self_test())
    if args.cli_command == "cli-burn-data":
        sys.exit(cli_burn_data(args))
    run_gui()
