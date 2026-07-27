# PyBurn Studio - Changelog

## [1.15.0] - 2026 - Native SPTI/MMC burn engine on Windows

Windows disc burning was rewritten to talk to the drive directly instead of
going through the IMAPI2 COM API. IMAPI2's progress-event model corrupted burns
on some USB drives (the progress callback fires inside the synchronous write and
the drive rejects it), and there was no safe way to get live progress from it on
that hardware. The burn now issues the low-level drive commands itself, the same
way the ripper already reads discs, so every write is under the app's control,
progress is exact, and there is no COM in the write path to interfere with it.

### Added
- Native SPTI/MMC write engine (pyburn/services/spti_writer.py). It opens the
  drive directly and sends raw MMC commands over IOCTL_SCSI_PASS_THROUGH_DIRECT,
  following the standard command sequence (GET CONFIGURATION, READ DISC
  INFORMATION, MODE SELECT write parameters, READ TRACK INFORMATION, a WRITE(10)
  loop, SYNCHRONIZE CACHE, CLOSE TRACK SESSION). Because the app issues every
  write, progress is exact (sectors written / total) with no events, no polling,
  and nothing that can overlap the write.
- Data CDs burn in Track-At-Once mode; audio CDs burn gapless in Session-At-Once
  (Disc-At-Once) mode with a cue sheet, raw 2352-byte CD-DA sectors, and the
  mandatory pre-gap, matching Red Book audio.
- Optimal Power Calibration (OPC) is performed before the first write on both the
  data and audio paths, as mature burners do. Skipping it let a write start on
  default laser power and then fail partway through with a medium/write error;
  running it first makes the write reliable.
- Pure-Python ISO9660 + Joliet image builder (pyburn/services/iso_builder.py).
  Data images are now authored in the app with no COM and no external tools:
  both an ISO9660 (8.3) and a Joliet (long-name, UCS-2) directory tree sharing
  the same file extents, verified against a standard ISO parser across empty,
  single-sector, multi-sector, large, and deeply nested files.

### Changed
- Windows data and audio burns run in a dedicated one-shot subprocess (the app
  re-invoked with a hidden CLI command), so the actual burn is completely
  isolated from the GUI process. Progress, status, and log lines stream back over
  stdout. This is the same isolation pattern the sibling audio-control tool uses
  for COM work, applied here so nothing in the GUI can perturb a burn in flight.
- The Setup screen and the About/readiness report now name the real engines:
  "native SPTI/MMC burn" for data and audio, and "WSL2 authors, native SPTI
  burns" for Video DVD and Blu-ray, instead of IMAPI2. The first-run guidance is
  explicit that nothing needs to be installed to burn or rip, that ffmpeg is an
  optional download for better audio decode and rip encoding, and that WSL2 is
  needed only for Video DVD and Blu-ray.

### Fixed
- Data burns no longer corrupt on drives where IMAPI2's write-progress events
  interfered with the synchronous write; the SPTI engine removes that failure
  mode entirely.
- Audio decode progress reached only 40 percent regardless of the number of
  tracks (a leftover scaling factor). Decoding now fills its own 0-100 bar, and
  the burn then runs its own 0-100 bar.
- The audio write loop no longer reports progress on every single WRITE(10); it
  reports on a short time gate so a slow progress consumer cannot stall the write
  stream and starve the drive buffer.

### Dependencies and setup
- No new dependencies. The SPTI engine and ISO builder use only the Python
  standard library and the Windows API through ctypes. The pinned build
  requirements and the build recipe are unchanged. comtypes remains a dependency
  because IMAPI2 is still used for Windows media introspection, blanking, and
  eject, and as an audio fallback.
- Nothing new to install for a first-time setup. Burning and ripping work out of
  the box; ffmpeg (optional) and WSL2 for DVD/Blu-ray (optional) are installed
  from the Setup screen as before.

---

## [1.9.0] - 2025 - Cross-platform engine architecture (Windows native + WSL2)

PyBurn now runs as a first-class Windows application as well as Linux, instead
of assuming Unix tools everywhere. It picks the right engine for each job based
on the platform and what is installed, so a clean Windows machine can burn discs
with no manual tool hunting.

### Added
- Per-operation engine routing. A new capability resolver decides, for each job
  and platform, whether a step runs through the Unix command-line tools, the
  native Windows burning API (IMAPI2), native Windows CD reading (IOCTL), or a
  WSL2 Linux environment. Linux behavior is unchanged: everything still runs
  through the command-line tools.
- Windows IMAPI2 backend (via comtypes) for data discs, ISO burning, audio CD
  writing, blanking rewritable media, and eject. No external tools required.
- Windows IOCTL ripper that reads audio CDs directly through the operating
  system and encodes with ffmpeg when present, so ripping works on Windows
  without cdparanoia.
- WSL2 authoring path for Video DVD and Blu-ray on Windows. The authoring tools
  that have no Windows build (dvdauthor, tsMuxeR, xorriso) run inside a WSL2
  distro to generate the disc image, and the native Windows burner writes that
  image to the drive. This is the split-at-the-file design: Linux userland
  authors, Windows burns the hardware.
- First-run Setup screen. On first launch the app shows what each feature can do
  on this machine, detects WSL2, and can install the Linux toolchain into an
  existing WSL2 distro with one click. A Setup button re-opens it any time.
- On-demand tool acquisition on Windows. Setup can download an official ffmpeg
  build into a local tools\ folder next to the exe (no system install, no admin)
  to enable audio decode and rip encoding. A single "Enable DVD/Blu-ray (WSL2)"
  button does the entire authoring setup: it installs a WSL2 Linux distribution
  if none is present (non-interactively, no username/password prompt), installs
  the Linux disc tools as root with no password prompt, and fetches tsMuxeR
  (which is not in apt) so Blu-ray authoring works. If the WSL2 feature itself
  is not enabled yet, the button starts that one elevated step and asks for a
  single reboot, then finishes automatically on the next click. WSL2 detection
  now distinguishes "not installed" from "installed but no distribution yet" so
  the app gives the right action instead of a dead-end instruction. WSL commands
  run with the console window suppressed so setup no longer flashes command
  windows, and the tsMuxeR fetch handles the official Linux release being a .zip
  (it is unzipped rather than assumed to be a tarball).
- The tool finder now also searches the local tools\ folder in addition to PATH,
  so a downloaded ffmpeg is picked up with no restart or manual configuration.

### Changed
- The About screen now reports readiness per feature (Data disc, Audio CD, Video
  DVD, Blu-ray, Rip) and the engine each will use, instead of a flat list of
  every command-line tool marked present or missing. On Windows this reflects
  the real native and WSL2 paths, so features that work no longer read as
  errors.
- Installing WSL2 itself is now driven by the app. When the WSL2 feature is not
  installed, the Enable DVD/Blu-ray button runs the Windows installer for it
  through a single elevation prompt; the user approves that prompt and reboots
  once, and the app finishes the rest on the next click. The only steps that
  cannot be automated are that one approval and the reboot, which Windows
  requires for any feature install.

### Fixed (Windows setup, during 1.9.0 stabilization)
- WSL2 detection no longer trusts the mere presence of wsl.exe, which exists as
  a built-in stub on every Windows 11 machine even when the feature is not
  installed. Detection now reads what wsl actually reports and treats a
  "not installed" response as feature-absent, so a clean machine correctly runs
  the installer instead of trying to add a distribution to a feature that is not
  there.
- Detection probes no longer hang. On a machine without WSL2, `wsl -l -v` prints
  a prompt and waits up to 60 seconds for a keypress; the app now closes stdin
  on these calls so the prompt cannot block startup or setup.
- The Enable DVD/Blu-ray flow no longer reports success when a step actually
  failed. The tsMuxeR install is verified with a real presence check and its
  true result is honored, and a failed distro install that is caused by the WSL2
  feature being absent now triggers the feature install instead of stopping with
  a misleading message.
- The tsMuxeR install script is delivered to WSL2 as base64 that is decoded to a
  file and run, rather than passed inline. Passing it inline mangled nested
  quotes, so the install failed inside the app even though the same commands
  worked when run by hand. The official tsMuxeR Linux release is a .zip and is
  unzipped rather than assumed to be a tarball.
- Setup now writes a full log to pyburn_setup.log next to the program, including
  the raw wsl output and the detection result, so a failed setup can be
  diagnosed from the file.

### Notes on Windows coverage
- Data disc, ISO, audio CD, blank, media info, eject, and ripping run natively
  on Windows with no external tools (installing ffmpeg improves audio decode and
  rip encoding).
- Video DVD and Blu-ray authoring on Windows require WSL2 with the Linux tools
  installed; without WSL2 those two features report as unavailable rather than
  pretending to work.

---

## [1.8.0] - 2025 - Reliability fixes and reproducible onefile build

This release works through a full code review and adds a reproducible
single-file build. Behavior for a working setup is unchanged; the fixes remove
crashes, wrong results, and misleading progress in the edge cases below.

### Fixed (critical)
- Queue thread lifecycle reworked to remove a double-finish and a possible
  deadlock. Completion is now idempotent per job through a `_finalized_id`
  guard: the worker's finished signal and the thread's finished signal can both
  try to complete the same job, and whichever runs first wins while the other
  becomes a no-op. The completion slot no longer calls `wait()` while the worker
  thread is still in its event loop (that was the deadlock path); it calls
  `quit()` and lets teardown happen after the thread stops. Teardown disconnects
  the thread's finished signal before joining so a torn-down thread cannot
  re-enter the teardown slot.
- A worker that crashes or is killed before signalling completion is now marked
  FAILED by a safety net on the thread's finished signal, and the queue still
  advances instead of stalling.
- ProcessRunner now has a `reset()` that clears its cancel flag. A fresh backend
  (and runner) is created per job, so this is belt-and-suspenders, but it means
  a reused runner can never get stuck refusing all further work because of a
  stale cancel flag.

### Fixed (correctness)
- CD-Text titles and performers are now escaped before they are written into the
  cdrdao `.toc` file. A double quote or backslash in an album or track field
  used to produce a malformed TOC and fail the whole audio burn. Backslash is
  escaped first, then the double quote.
- Audio CD burn progress is now parsed from cdrdao's real output through a new
  `parse_cdrdao` parser (it reads "N of M MB" ratios and a trailing percent)
  instead of jumping between hardcoded values. The bar now tracks the actual
  write.
- Auto burn-speed selection now picks the true middle of the drive's advertised
  speed list. The old index math picked the slowest speed on a two-speed drive.
  For example a [4, 8] drive now selects 8, and [4, 8, 16] selects 8.
- Verification no longer forces a fully simulated burn just because `readom` is
  absent. Real-versus-simulated is gated only by the burn tools now.
  Verification uses `readom` when present and falls back to `isoinfo`. If the
  user asks to verify but neither tool exists, the burn still runs for real and
  a warning is logged that verification will be skipped.
- Windows device scanning now prefers a PowerShell CIM query of
  `Win32_CDROMDrive` and only falls back to WMIC if that returns nothing,
  because WMIC was removed from Windows 11 24H2.

### Fixed (minor)
- Removed dead MusicBrainz thread import-guard code in the Rip tab.
- The Rip tab's MusicBrainz lookup runs in a background thread with a modal
  progress dialog, so the interface stays responsive during the network call.
- Each tab now updates its own progress bar and status line only for the jobs it
  enqueued, tracked through a per-tab job-id set. Previously every tab mirrored
  whatever job was running, regardless of which tab started it.
- The About dialog reuses the app's existing tool finder instead of building a
  throwaway one.
- The History view refreshes automatically when a job finishes.

### Added
- Reproducible single-file build. `requirements.txt` is fully pinned, so the
  same wheels install on every machine and the produced executable is identical
  each time.
- `BUILD_EXE.bat` builds the onefile executable on Windows: it verifies Python
  3.13.12 through the `py -3.13` launcher, creates a virtual environment,
  installs the pinned requirements, and runs PyInstaller with the project spec.
- `pyburn_studio.spec` rewritten for a onefile, windowed build that collects all
  of PyQt6 (so the Qt platform plugins and sip ship inside the exe) and the
  `pyburn` submodules (loaded dynamically by the GUI), embeds an icon, and reads
  Windows metadata from `version.txt`.
- `version.txt` with Windows file metadata, and `pyburn.ico` as the executable
  icon.
- New documentation: a rewritten README, a build guide in `Docs/BUILD_EXE.md`,
  and an internal `Docs/ENGINEERING_GUIDE.md`.

### Upgrade notes
- No configuration or data-format changes. Existing `~/.pyburn_config.json`,
  `~/.pyburn_history.json`, and logs continue to work.
- The external command-line tools are still not bundled and must be installed
  separately, as before.

---

## [1.7.2] - 2025-01-XX - Final polish and edge case hardening
### Added
- Asynchronous device scan in Settings with a "Scanning devices..." placeholder,
  which avoids UI stalls on slow systems.
- Gradient progress bar chunks for better contrast across themes.
- Audio CD track Move Up and Move Down controls to preserve track order.
- Informational warning when data size is under 10 percent of the selected media
  capacity, to prevent media waste.
- Stronger device save logic: validates that a device id is present and excludes
  the scanning placeholder.

### Changed
- Default burn speed is the string "Auto" and auto-resolves per media
  capabilities.
- Temp-space preflight margins adjusted: data 1.2x, audio 1.5x, video and DVD
  and BD 2.5x.
- History view sorted by parsed finish timestamp, with Show Log and Export Log.
- Settings device combo only saves valid device ids, not scanning placeholders.

### Fixed
- ProcessRunner race and hangs: locking around the process, daemon pump threads,
  kill on cancel with a timeout, and join timeouts.
- Verification monitors have bounded timeouts and cancel-aware hashing.
- Device scan output handling no longer crashes on missing stdout or stderr.
- Speed resolution accepts "Auto" (case-insensitive) and numeric strings.
- Removing pending jobs while idle advances the queue.
- Crash recovery marks a job FAILED if the finished signal did not fire.
- Temp file cleanup uses try/finally so temporary files are removed after
  errors.
- MusicBrainz lookup runs in the background with a modal progress dialog.
- Duplicate files in a list are prevented with resolved paths.

---

## [1.7.1] - 2025-01-XX - Stability and UX hardening (superseded by 1.7.2)
### Added
- Initial async device scan.
- Audio CD track reordering controls.
- Temp space multipliers.

### Note
This version was superseded by 1.7.2.

---

## [1.7.0] - 2024 - Modular verification and helpers
### Added
- Two-level verification: readback with size and checksum, then an isoinfo
  listing compare fallback.
- Media helper methods for capabilities, speeds, blank, eject, and auto speed.
- Progress parsers for growisofs, cdrecord, and cdparanoia.
- History Show Log and Export Log.

### Changed
- RealBackend refactored onto the helper classes for testability.

---

## [1.6.0] - 2024 - Feature pack: BD authoring, history, MusicBrainz
### Added
- Blu-ray BDMV authoring through tsMuxeR.
- Persistent history and logs, with retry of past jobs.
- Optional MusicBrainz metadata lookup for CD ripping.
- CD-Text authoring for audio CDs.

---

## [1.5.0] - 2024 - Media-aware burning, verification uplift, queue polish
### Added
- Media introspection to detect type, rewritable and blank status, and speeds.
- Auto-blank rewritable media, with confirmation.
- Eject after burn.
- Readback verification with checksum, isoinfo fallback.

---

## [1.4.0] - 2024 - Job queue and batch processing
### Added
- Serial job queue with enqueue, remove, cancel, and crash recovery.
- Queue widget with live progress and status.
- Global non-modal job log viewer.

---

## [1.3.0] - 2024 - Cancelable jobs, preflight checks, basic verification
### Added
- Cancel support that cascades to subprocesses.
- Preflight temp space checks and capacity warnings.
- Basic size-comparison verify.

---

## [1.2.0] - 2024 - Real-time parsing and device detection
### Added
- Real-time progress parsing for growisofs, cdrecord, wodim, and cdparanoia.
- Device detection and a device selection combo box.
- Visual capacity gauge.

---

## [1.1.0] - 2024 - Modularization and simulation backend
### Added
- Modular core, services, and gui separation.
- Simulation backend so the app works without tools or hardware.
- Non-blocking UI with subprocess streaming.

---

## [1.0.0] - 2024 - Initial release
### Added
- PyQt6 GUI with Data, Audio, Video DVD, and Rip tabs.
- Settings dialog with device, speed, temp directory, and toggles.
- Basic progress bars and status lines.

---

## Platform limitations

- Windows: the external tools are Unix programs. Use WSL2 to provide them. The
  Windows build otherwise runs in simulation mode.
- Blu-ray authoring needs tsMuxeR, which is not in most package managers. The
  Blu-ray path warns if it is missing.
- MusicBrainz lookup needs cd-discid and the Python requests library plus
  network access. The feature is disabled cleanly if they are missing.

## Tooling and dependencies

Required Python: PyQt6 (pinned to 6.9.1 for the packaged build).
Optional Python: requests, for MusicBrainz.

External tools by area:
- ISO creation: mkisofs or genisoimage, xorriso
- Burning: cdrecord or wodim, growisofs, cdrdao
- Media: dvd+rw-mediainfo, dvd+rw-format, eject
- Verification: isoinfo, readom or readcd
- Audio and video: ffmpeg, ffprobe, cdparanoia, lame, flac
- DVD authoring: dvdauthor
- Blu-ray authoring: tsMuxeR (optional)
- Metadata: cd-discid (optional)

End of changelog.
