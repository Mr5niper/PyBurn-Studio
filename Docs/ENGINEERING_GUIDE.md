# ENGINEERING GUIDE

PyBurn Studio, disc authoring GUI (PyQt6, PyInstaller onefile EXE).
Audience: engineers maintaining or extending the codebase.
Platforms: Linux (primary), macOS, Windows (via WSL2 for the tools).
Version: see `pyburn/__init__.py` (single source of truth).
Build contract: Python 3.13.12 required for the Windows build, PyInstaller
required, requirements fully pinned.

This is not a usage manual. The README covers usage. This file covers how the
program is built, how it works internally, where it breaks, and how to extend it
safely.

## 1) Build and runtime contract

### 1.1 Python version
The Windows build is pinned to Python 3.13.12. `BUILD_EXE.bat` does a hard
preflight through the `py -3.13` launcher and refuses to build unless the
resolved version is exactly 3.13.12. The launcher is used instead of a bare
`python` on PATH so the build works on machines where another Python owns PATH.

### 1.2 Reproducible dependencies
`requirements.txt` pins every package with `==`. The build creates its own
`.\venv`, installs only those pins, and runs PyInstaller from the checked-in
`pyburn_studio.spec`. The same wheels produce the same onefile every time. If
you change a dependency, change the pin; the file is the single source of truth.

### 1.3 The frozen artifact
The supported artifact is a single-file, windowed executable:
`dist/PyBurnStudio.exe` on Windows, `dist/PyBurnStudio` elsewhere. The spec:
- collects all of PyQt6 (Qt platform plugins plus sip) so the frozen app can
  actually create a window;
- collects the `pyburn` submodules because the GUI imports backend and queue
  modules dynamically;
- embeds `pyburn.ico` and reads `version.txt` for Windows metadata;
- sets `console=False` (GUI) and does not use UPX.

### 1.4 The external tools are not bundled
PyBurn Studio is a front end. At run time it shells out to mkisofs, cdrecord,
growisofs, cdrdao, ffmpeg, dvdauthor, cdparanoia, and others. These are not
inside the exe. If a required tool is missing, the job either runs in the
simulated backend (when simulation is enabled) or is refused, depending on the
job and the setting.

## 2) Architecture

The code is split into three layers under `pyburn/`.

Core (`pyburn/core/`), the plain building blocks:
- `config.py` reads and writes `~/.pyburn_config.json`, resolves a default
  device, and migrates away from stale device formats.
- `tools.py` (ToolFinder) resolves logical tool names to real executables and
  reports which are missing.
- `devices.py` (DeviceScanner) enumerates optical drives per platform.
- `jobs.py` defines the Job and JobOptions data structures and the JobType enum.
- `history.py` (HistoryStore) reads and writes `~/.pyburn_history.json`.

Services (`pyburn/services/`), where the work happens:
- `exec.py` (ProcessRunner) runs one external process, streams stdout and
  stderr through pump threads, and supports cancel.
- `backend.py` holds RealBackend and SimulatedBackend with one method per disc
  type, plus the CD-Text TOC writer. This is the Linux/CLI path.
- `platform_caps.py` holds the engine routing. The Engine enum lists the ways a
  step can run (CLI, IMAPI2, IOCTL, WSL, SIM, NONE). CapabilityResolver decides,
  per job type and platform, which engine handles the job and reports why.
  WSLManager detects WSL2, installs a distro, provisions the Linux tools, runs
  commands inside the distro, and translates Windows paths to /mnt form.
- `imapi2_backend.py` is the Windows IMAPI2 backend via comtypes, now used for
  media introspection, blanking, and eject (and kept as an audio fallback). The
  data and audio writes no longer go through it.
- `spti_writer.py` is the native Windows write engine. It opens the drive with
  DeviceIoControl and sends raw MMC commands over IOCTL_SCSI_PASS_THROUGH_DIRECT
  to burn data CDs (Track-At-Once) and audio CDs (gapless Session-At-Once with a
  cue sheet), issuing every WRITE(10) itself so progress is exact and no COM is
  in the write path. It performs OPC (laser power calibration) before writing, and
  when verification is enabled it reads the finished data disc back with READ(10)
  and compares it to the authored ISO sector by sector before ejecting.
- `iso_builder.py` authors an ISO9660 + Joliet data image in pure Python (shared
  file extents for both directory trees), replacing IMAPI2 image authoring for
  the data burn. No COM, no external tools. `build()` takes a flat file list;
  `build_tree()` takes the explicit folder layout composed on the Data tab, both
  through one shared authoring pipeline.
- `ioctl_ripper.py` is the native Windows CD ripper via DeviceIoControl, with
  ffmpeg encoding when present.
- `wsl_backend.py` authors DVD (dvdauthor) and Blu-ray (tsMuxeR) images inside
  WSL2 and hands the finished image to the native SPTI engine to burn.
- `installer.py` downloads ffmpeg into a local tools folder, runs the elevated
  WSL2 install, and fetches tsMuxeR into the distro.
- `queue.py` (JobQueueService) owns the single-job-at-a-time queue and the
  worker-thread lifecycle, and shares one WSLManager with every worker.
- `burn.py` (BurnWorker) is the QObject moved onto a worker thread that resolves
  the engine for one job, dispatches to the matching backend, and emits status,
  progress, log, and finished signals.
- `progress.py` (ProgressTools) parses tool output into a percent.
- `media.py` (MediaTools) reads media info and resolves burn speed, blank, and
  eject.
- `verify.py` (VerificationTools) is the Linux/CLI verification path (readback
  plus checksum, or isoinfo listing compare). On the native Windows data path,
  read-back verification is done by the SPTI writer itself, not this module.
- `metadata.py` does the optional MusicBrainz lookup.

Per-platform engine routing (the heart of the cross-platform design):
- Linux and macOS: every job runs through the CLI backend (backend.py), exactly
  as before. Nothing about the Linux path changed.
- Windows: media info, blank, and eject run through IMAPI2; data disc and audio
  CD writes run through the native SPTI/MMC engine (spti_writer.py) in a dedicated
  subprocess, with data images authored in pure Python (iso_builder.py); ripping
  runs through IOCTL; Video DVD and Blu-ray author inside WSL2 and are then burned
  by the SPTI engine. If a native Windows CLI tool build happens to be present it
  is preferred, otherwise the native path is used.
- When no path exists for a job on the current machine (for example DVD/BD with
  no WSL2), the resolver returns NONE and the worker fails the job with a clear
  message instead of pretending to succeed.

GUI (`pyburn/gui/`), everything on screen:
- `main_window.py` builds the window, the tabs, and the queue/history panels,
  and hosts the Setup button and first-run setup.
- `tabs.py` has the five tabs; each builds a Job and enqueues it.
- `widgets.py` has the file list, capacity gauge, queue table, and history view.
- `dialogs.py` has Settings, the job log window, and the Setup dialog (capability
  report, ffmpeg download, and the one-click Enable DVD/Blu-ray WSL2 flow).
- `style.py` holds the dark theme stylesheet.

Data flow for a burn: a tab builds a Job and calls `queue.enqueue`. The queue
starts a QThread with a BurnWorker. The worker asks CapabilityResolver which
engine to use, dispatches to that backend (CLI, IMAPI2, IOCTL, WSL, or the
simulated backend), and emits signals. The queue records history, writes the
per-job log, and advances to the next job.

## 3) The queue and threading model (read before touching queue.py)

This is the part most likely to break subtly, so it is spelled out.

One job runs at a time. `JobQueueService` keeps `_current`, a pending list, a
QThread, and a BurnWorker. The lifecycle is:

1. `_start_next` pops a job, creates the worker and thread, wires signals,
   clears the per-job finalize guard, and starts the thread.
2. Normal completion: the worker emits `sig_finished`. The slot
   `_on_worker_finished` finalizes the job, then calls `thread.quit()`. It does
   NOT call `wait()` here. Calling `wait()` from this slot would block the main
   thread while the worker thread is still inside its own event loop, which can
   deadlock. `quit()` lets the thread unwind and emit `finished()`.
3. `_on_thread_finished` runs after the thread's event loop has stopped. It is
   the safe place to tear the thread down. If the job was never finalized (the
   worker crashed or was killed before emitting `sig_finished`), it finalizes
   the job as a failure here. Then it tears down the thread and calls
   `_start_next`.

Two invariants keep this correct:

- `_finalize` is idempotent per job id, guarded by `_finalized_id`. Both the
  worker's `sig_finished` and the thread's `finished` can try to finish the same
  job; whichever runs first wins and the other is a no-op. This is what prevents
  the double-finish and double-history-entry bug.
- `_teardown_thread` disconnects the thread's `finished` signal before calling
  `wait()`, so a torn-down thread can never re-enter `_on_thread_finished`. The
  `wait()` there returns immediately because the thread has already stopped; it
  is only there to be certain the OS thread is joined before the object is
  released.

If you change any of this, keep those two invariants. Re-run
`python pyburn_studio.py --self-test` several times; it exercises five jobs
through the full lifecycle and will flush out double-finish or teardown races.

## 4) ProcessRunner

One ProcessRunner is created per backend instance, and a fresh backend is
created per job, so the cancel flag does not need to survive across jobs.
`reset()` exists anyway so a runner can be reused safely if that ever changes; a
stale cancel flag would otherwise make every later run a silent no-op. Cancel
terminates the running process and, failing that, kills it. Pump threads are
daemon threads and are joined with a timeout.

## 5) Backends

`RealBackend` and `SimulatedBackend` expose the same methods: `burn_data`,
`burn_audio`, `burn_video_dvd`, `burn_video_bd`, `rip_cd`. The GUI does not care
which one it has. Progress within a method is split into phases (ISO build,
burn, verify) and each phase maps a tool's 0..100 percent into an overall span.

Two correctness points inside RealBackend:

- CD-Text TOC strings are double-quoted in the cdrdao `.toc` file, so any double
  quote or backslash in a title or performer must be escaped or the whole burn
  fails. `_toc_escape` escapes the backslash first, then the double quote. All
  CD-Text fields go through it.
- Audio burn progress is parsed from cdrdao's stderr through
  `ProgressTools.parse_cdrdao` rather than jumping between hardcoded values, so
  the bar reflects real write progress.

## 6) Verification gating (verify.py and burn.py)

VerificationTools tries readback first (readom or readcd), comparing size and,
for images under 100 MB, a SHA-256 checksum. If readback is unavailable or
fails, it falls back to an isoinfo listing compare. If neither readom nor
isoinfo exists, it passes through with a warning rather than failing the burn.

BurnWorker does not treat a missing verify tool as a reason to force the
simulated backend. Real-versus-simulated is gated only by the burn tools. If the
user asked to verify but neither readom nor isoinfo is present, the burn still
runs for real and a warning is logged that verification will be skipped. This
avoids the surprise of a fully simulated burn just because the user ticked
verify on a box that only has isoinfo.

The above describes the Linux/CLI path. On the native Windows data path there are
no external verify tools: when the drive's verify setting is on, spti_writer reads
the finished disc back with READ(10) after the write and session close, and
compares it to the authored ISO sector by sector before the disc is ejected,
failing with the first differing sector on a mismatch. The flag reaches the burn
through burn.py, which adds --verify to the cli-burn-data subprocess, and the CLI
passes it to SPTIWriter.burn_iso. The write path is unchanged; verification is a
read-only step layered after it.

## 7) Speed resolution (media.py)

`resolve_speed` accepts "Auto" (case-insensitive) or a numeric speed. On Auto it
reads the media's advertised write speeds and picks the true middle of the
sorted list using `len(speeds) // 2`. For a two-speed drive that is the faster
of the two; for odd counts it is the middle; the earlier off-by-one picked the
slowest on a two-speed drive. If no media info is available it falls back to a
conservative per-type default (CD 16x, DVD 8x, BD 4x).

## 8) Device scanning (devices.py)

Linux scanning is strict: only `/dev/sr*` optical nodes, so hard drives and USB
sticks are never offered as burn targets. Windows scanning prefers a PowerShell
CIM query of `Win32_CDROMDrive` and only falls back to WMIC if that returns
nothing, because WMIC was removed from Windows 11 24H2. macOS returns a
conservative default.

## 9) Per-tab status routing (tabs.py)

The queue broadcasts `sig_status_update` for the running job to every tab. Each
tab records the ids of the jobs it enqueued (`_my_job_ids`, set through
`_register_job` before every enqueue) and updates its own progress bar and
status line only for jobs it owns. Without this, every tab would mirror whatever
job happened to be running, regardless of which tab started it.

## 10) Extending safely

Add a new disc type:
1. Add a value to JobType in `core/jobs.py`.
2. Add a tab class in `gui/tabs.py` that builds the Job, calls `_register_job`,
   and enqueues it.
3. Add a backend method to both RealBackend and SimulatedBackend with the same
   signature.
4. Add the required tool names to the `req` map in `burn.py` so real-versus-
   simulated gating knows what the job needs.

Add support for a new tool:
1. Add it to `TOOL_CANDIDATES` in `core/tools.py`.
2. If it reports progress in a new shape, add a parser to `progress.py`.
3. Use it from `backend.py`.

Change the theme: edit `APP_STYLESHEET` in `gui/style.py`.

## 11) Common failure modes

- Frozen exe runs from source but fails when frozen: a missing hidden import.
  Keep `collect_all('PyQt6')` and the `pyburn` submodule collection in the spec,
  and keep `PyQt6.sip` in hiddenimports.
- App freezes or double-counts a job: almost always a regression in the queue
  lifecycle. Re-read section 3 and keep the two invariants.
- Burn fails immediately on an audio CD with a quote in a title: a regression in
  `_toc_escape` or a CD-Text field that bypassed it.
- A two-speed drive always burns slow on Auto: a regression in the middle-speed
  index in `resolve_speed`.
