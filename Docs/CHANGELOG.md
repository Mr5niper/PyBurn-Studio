# PyBurn Studio - Changelog

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
