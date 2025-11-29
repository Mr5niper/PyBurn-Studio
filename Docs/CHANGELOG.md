# PyBurn Studio - Changelog

## [1.7.2] – 2025-01-XX – Final polish and edge case hardening
### Added
- Asynchronous device scan in Settings with "Scanning devices…" placeholder; avoids UI stalls on slow systems.
- Gradient progress bar chunks (`qlineargradient`) for better contrast across themes.
- Audio CD track Move Up/Move Down controls to preserve track order.
- Informational warning when data size is <10% of selected media capacity (prevents media waste).
- Strengthened device save logic: validates `device_id is not None` and excludes scanning placeholder.

### Changed
- Default burn speed is string "Auto"; auto-resolves per media capabilities via `MediaTools.resolve_speed()`.
- Data/temp-space preflight margins adjusted: data=1.2x; audio=1.5x; video/DVD/BD=2.5x (formerly a flat ~1.1x).
- History view now sorted by parsed finish timestamp (descending); exposes "Show Log" (open in system viewer) and "Export Log" (save-as dialog).
- Settings dialog device combo validation: only saves valid device IDs, not scanning placeholders.

### Fixed
- **ProcessRunner race and hangs**: proper locking around `Popen`, daemon pump threads, kill-on-cancel with 2s timeout, join timeouts (5s).
- **Verification monitors**: bounded timeouts (120s default), cancel-aware SHA-256 hashing (checks `runner.cancelled` per chunk).
- **Device scan output handling**: no crashes on missing stdout/stderr; uses `(p.stdout or "") + "\n" + (p.stderr or "")` pattern.
- **Speed resolution**: accepts "Auto" (case-insensitive) and numeric strings ("8") in addition to ints; gracefully converts or falls back.
- **Queue remove/start-next**: removing pending jobs while idle immediately calls `_start_next()` to advance queue.
- **Crash recovery**: `_thread_cleanup()` handler marks job FAILED if `sig_finished` didn't fire; advances queue automatically.
- **Temp file cleanup**: all `RealBackend` burn methods now use `try/finally` to clean temporary ISO/readback files even after errors; eject moved to finally block.
- **MusicBrainz lookup**: runs in background `QThread` with modal `QProgressDialog`; UI stays responsive during 8s+ network requests.
- **File list duplicate prevention**: uses normalized `Path.resolve()` set to prevent same path from appearing twice (handles `/tmp` vs `/tmp/` edge cases).
- **History sorting**: datetime parsing with fallback to `datetime.min` for malformed timestamps.
- **Self-test cleanup**: cancels on 30s timeout, quits thread cleanly with 2s wait, removes dummy files and directories.
- **Config logs_dir**: removed redundant `Path()` constructor: `str(Path.home() / ".pyburn_logs")` instead of `str(Path(Path.home() / ".pyburn_logs"))`.

### Upgrade notes
- If you scripted burn speeds as integers, "Auto" is now the preferred default; scripts can continue to pass ints or numeric strings.
- Ensure `~/.pyburn_logs` exists or will be auto-created; logs are written there by default.
- Optional dependency `requests` enables MusicBrainz lookup (gracefully disabled if missing).
- No breaking changes; all fixes are backward-compatible.

---

## [1.7.1] – 2025-01-XX – Stability and UX hardening (superseded by 1.7.2)
### Added
- Initial async device scan implementation.
- Audio CD track reordering controls.
- Temp space multipliers.

### Changed
- Speed resolution to accept "Auto" strings.

### Fixed
- ProcessRunner threading issues.
- Verification timeout handling.
- Queue crash recovery basics.

### Note
**This version was superseded by 1.7.2 with additional polish and edge case fixes.**

---

## [1.7.0] – 2024-XX-XX – Modular verification and helpers
### Added
- **VerificationTools**: two-level verification system
  - Level 1: readback with `readom`/`readcd`, size comparison, SHA-256 checksum for ISOs <100MB.
  - Level 2: `isoinfo -R -f` listing compare when readback unavailable or fails.
- **MediaTools**: device/media helper methods (capabilities, speeds, blank, eject, auto speed selection).
- **ProgressTools**: parsers for `growisofs`/`cdrecord`/`cdparanoia` outputs with progress clamping (0-100%).
- Persistent history enhancements: "Show Log" (open in system viewer), "Export Log" (save-as dialog).

### Changed
- `RealBackend` refactored to use `MediaTools`/`VerificationTools`/`ProgressTools`, reducing method complexity and improving testability.
- Safer default "Auto" speed selection using `MediaTools` (picks median conservative speed from media capabilities).

### Fixed
- Multiple minor resilience issues in device info detection and listing compare.

---

## [1.6.0] – 2024-XX-XX – Feature pack: BD authoring, history, MusicBrainz
### Added
- **Blu-ray (BDMV) authoring** via `tsMuxeR` (if installed). Creates BDMV structure and burns via `growisofs`/`cdrecord`. ISO via `mkisofs`/`xorriso`.
- **Persistent history** (JSON at `~/.pyburn_history.json`) and logs directory (`~/.pyburn_logs/`); retry of past jobs with reconstructed options.
- **Optional MusicBrainz metadata lookup** for CD ripping (requires `cd-discid` + Python `requests` library).
- **CD-Text authoring** for Audio CD (album title/performer, per-track titles via `.toc` file).

### Changed
- Job Queue UI: adds a **History tab** with Retry, Show Log, and Export Log buttons.
- Non-modal global log window improved with job ID tagging.

### Fixed
- Minor robustness around RW blanking and eject operations.

### Upgrade notes
- Install `tsMuxeR` for BDMV authoring; otherwise the Blu-ray tab shows a warning.
- Install `cd-discid` and Python `requests` to enable MusicBrainz lookup (optional).

---

## [1.5.0] – 2024-XX-XX – Media-aware burning, verification uplift, queue polish
### Added
- **Media introspection** using `dvd+rw-mediainfo`/`cdrecord -prcap` to detect media type (CD/DVD/BD), rewritable/blank status, and speed candidates.
- **Auto-blank rewritable media** (toggle in Settings; prompts user for confirmation before blanking).
- **Eject after burn** (toggle in Settings).
- **Readback verification pass** (`readom`/`readcd`) with SHA-256 checksum for small images; fallback to `isoinfo` listing compare if readback unavailable.
- Small-ISO-on-large-media warning (when data <10% of media capacity).

### Changed
- JobQueue UI improvements; better progress/state messaging with color-coded status.

### Fixed
- Safer subprocess handling with long-running tools (proper timeout and error handling).

---

## [1.4.0] – 2024-XX-XX – Job queue and batch processing
### Added
- **JobQueueService**: serialize multiple jobs; enqueue/remove/cancel; crash recovery; non-blocking UI.
- **JobQueueWidget UI** with live progress bars, status updates, and queue management buttons.
- Global non-modal **Job Log viewer** (Ctrl+L shortcut).

### Changed
- Tabs now enqueue jobs instead of running them directly; app becomes session-based with persistent queue.

### Fixed
- Threading correctness improvements; better status handling and signal propagation.

---

## [1.3.0] – 2024-XX-XX – Cancelable jobs, preflight checks, basic verification
### Added
- **Cancel support** for all jobs; cancellation cascades to subprocesses (SIGTERM → SIGKILL).
- **Preflight temp space checks** and capacity gauge warnings.
- Basic verify step (size comparison) and better error messages.

### Changed
- Real-time logs routed to a dedicated dialog (non-blocking).

### Fixed
- Minor robustness around path handling and error propagation.

---

## [1.2.0] – 2024-XX-XX – Real-time parsing and device detection
### Added
- **Real-time progress parsing** for `growisofs` (stdout), `cdrecord`/`wodim`, `cdparanoia`.
- **Device detection**: `wodim --devices`, `cdrecord -scanbus`, `lsblk` fallback; GUI device selection combo box.
- **Visual capacity gauge** for Data/Audio/Video tabs (shows used vs. media capacity with color coding).

### Changed
- Dark theme polish for better contrast (Nord-inspired color scheme).

### Fixed
- General error handling around tool presence (graceful degradation).

---

## [1.1.0] – 2024-XX-XX – Modularization and simulation backend
### Added
- **Modular architecture** with `core`/`services`/`gui` separation for maintainability.
- **Simulation backend** for all operations; app works without tools/hardware for demos/tests.
- **Non-blocking UI** with subprocess streaming (stdout/stderr pump threads).

### Changed
- Code restructured from monolithic script to maintainable modules (20 files).

### Fixed
- Stability under missing-tool conditions (graceful fallback to simulation).

---

## [1.0.0] – 2024-XX-XX – Initial release
### Added
- **PyQt6 GUI** with tabs:
  - **Data Disc**: ISO creation (mkisofs) and burn (growisofs/cdrecord)
  - **Audio CD**: WAV conversion (ffmpeg) + burn (cdrdao)
  - **Video DVD**: transcode (ffmpeg) + author (dvdauthor) + burn
  - **Rip CD**: cdparanoia + encoding (lame/flac/WAV)
- **Settings dialog** with device selection, burn speed, temp directory, and toggles.
- Basic progress bars and status lines.

---

## Known Issues

### Platform Limitations
- **Windows**: Requires Unix-like tools via WSL, Cygwin, or MSYS2 for full functionality. Native Windows tool ports have limited compatibility.
  - **Workaround**: Use WSL2 for best results.

### Tool Availability
- **Blu-ray authoring**: Requires `tsMuxeR` (not in standard package managers). Download from official project releases.
  - **Workaround**: App automatically disables Blu-ray tab if tsMuxeR is not found.

### Optional Features
- **MusicBrainz lookup**: Requires `cd-discid` system tool + Python `requests` library + network connectivity.
  - **Behavior**: Feature gracefully disabled if dependencies missing; no impact on other functionality.

---

## Implementation Notes

These are intentional design choices, not bugs:

- **Progress indicators**: ffmpeg transcoding uses phase-based progress (10%, 50%, 90%) rather than frame-accurate percentages due to variable source formats. Provides smooth, responsive feedback.

- **Verification**: Two-tier approach (readback + checksum, then listing compare) instead of mount-based byte-diff. Avoids privilege requirements and ensures cross-platform compatibility.

- **Simulation mode**: Enables UI testing without hardware/tools. Clearly labeled as "(simulated)" in job logs.

---

## Performance Expectations

- **ISO creation**: Linear with data size (~50MB/s on SSD, ~20MB/s on HDD).
- **Verification**: Readback speed limited by optical drive (~8x DVD = ~11MB/s).
- **Video transcoding**: CPU-bound; large files (>10GB) may take 30+ minutes depending on hardware.

---

## Tooling and Dependencies

### System Tools (Required for full features)
- **ISO creation**: `mkisofs` or `genisoimage`, `xorriso`
- **Burning**: `cdrecord` or `wodim`, `growisofs`, `cdrdao`
- **Media**: `dvd+rw-mediainfo`, `dvd+rw-format`, `eject`
- **Verification**: `isoinfo`, `readom` or `readcd`
- **Audio/Video**: `ffmpeg`, `ffprobe`, `cdparanoia`, `lame`, `flac`
- **DVD authoring**: `dvdauthor`
- **Blu-ray authoring**: `tsMuxeR` (optional, for BDMV)
- **Metadata**: `cd-discid` (optional, for MusicBrainz)

### Python Dependencies
- **Required**: `PyQt6 >= 6.4.0`
- **Optional**: `requests >= 2.28.0` (for MusicBrainz lookup)

### Installation Examples

#### Debian/Ubuntu
```bash
sudo apt install genisoimage wodim growisofs cdrdao ffmpeg ffprobe \
  cdparanoia lame flac dvdauthor isoinfo dvd+rw-tools eject \
  cd-discid xorriso

pip install PyQt6 requests
```

#### macOS (Homebrew)
```bash
brew install cdrtools dvd+rw-tools ffmpeg cdrdao cdparanoia \
  lame flac dvdauthor xorriso

pip install PyQt6 requests
```

#### Windows (WSL recommended)
Follow Linux installation steps within WSL2 environment.

---

## Credits
PyBurn Studio is developed using modern Python practices with PyQt6 for the GUI framework. Special thanks to the open-source community for the underlying disc authoring tools.

---

**End of Changelog**

```
