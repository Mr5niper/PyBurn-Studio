# PyBurn Studio

PyBurn Studio is a desktop disc authoring application built with Python and
PyQt6. It burns and rips discs on both Linux and Windows, choosing the right
engine for each job automatically. On Linux it drives the standard command-line
disc tools (mkisofs/genisoimage, cdrecord/wodim, growisofs, cdrdao, ffmpeg,
dvdauthor, cdparanoia, and friends). On Windows it burns and reads discs with
its own built-in engine that talks to the drive directly (SPTI/MMC for writing,
IOCTL for reading), with no external tools, and uses a WSL2 Linux environment
only for the DVD and Blu-ray authoring steps that have no Windows equivalent. It
shows real progress, a job queue, verification, and history.

## What it does

- Burn data discs (CD, DVD, Blu-ray) from files and folders. On Linux an ISO is
  built by mkisofs and written by growisofs or cdrecord; on Windows the built-in
  engine builds an ISO9660/Joliet image in pure Python and writes it to the
  drive directly with SPTI/MMC commands, with no external tools.
- Author audio CDs from MP3/WAV/FLAC/OGG/M4A/AAC. On Linux via cdrdao with
  optional CD-Text; on Windows via the built-in SPTI/MMC engine (gapless
  Disc-At-Once Red Book audio) with ffmpeg decoding the tracks.
- Author video DVDs (transcode with ffmpeg, structure with dvdauthor) and
  Blu-ray BDMV discs (requires tsMuxeR).
- Rip audio CDs to MP3, FLAC, or WAV, with optional MusicBrainz metadata lookup.
  On Linux via cdparanoia; on Windows by reading the disc directly through the
  operating system (IOCTL) and encoding with ffmpeg.
- Queue multiple jobs and run them one at a time so the drive is never double
  booked, with a live progress bar, a global job log, and a persistent history
  you can retry from.
- Verify burns two ways: read the disc back and compare size plus a SHA-256
  checksum for small images, or fall back to an isoinfo listing compare.

If a required tool is missing, the app can run the job in simulation mode so you
can still exercise the interface. This is controlled in Settings.

## Requirements

Runtime Python dependency:

- PyQt6 (pinned to 6.9.1 for the packaged build)

Optional Python dependency:

- requests (enables MusicBrainz lookups; the feature is disabled cleanly if it
  is not installed)

External command-line tools (install the ones you need for the disc types you
plan to author):

- ISO creation: mkisofs or genisoimage, xorriso
- Burning: cdrecord or wodim, growisofs, cdrdao
- Media control: dvd+rw-mediainfo, dvd+rw-format, eject
- Verification: isoinfo, readom or readcd
- Audio and video: ffmpeg, ffprobe, cdparanoia, lame, flac
- DVD authoring: dvdauthor
- Blu-ray authoring: tsMuxeR (optional; the Blu-ray path needs it)
- Metadata: cd-discid (optional; needed for MusicBrainz)

### Installing the external tools

Debian/Ubuntu:

```bash
sudo apt install genisoimage wodim dvd+rw-tools cdrdao ffmpeg \
  cdparanoia lame flac dvdauthor xorriso eject cd-discid
```

macOS (Homebrew):

```bash
brew install cdrtools dvd+rw-tools ffmpeg cdrdao cdparanoia \
  lame flac dvdauthor xorriso
```

Windows: most jobs need no external tools at all. Data discs, ISO burning, audio
CDs, blanking, media info, eject, and CD ripping run through the built-in Windows
engine: writing goes directly to the drive with SPTI/MMC commands (data and audio
both burn this way, with an ISO9660/Joliet image built in pure Python for data),
and reading uses IOCTL. ffmpeg is the only helper worth adding, and the app can
download an official build for you from the Setup screen. Video DVD and Blu-ray
authoring use a WSL2 Linux environment for the authoring step and then burn the
result with the native Windows burner; the Setup screen installs WSL2 and the
needed Linux tools for you (one Windows approval and one reboot are required,
which Windows mandates for any feature install). See the Platform notes section
below.

## Running from source

```bash
python -m venv .venv
# Linux/macOS:
source .venv/bin/activate
# Windows:
.venv\Scripts\activate

pip install PyQt6 requests
python pyburn_studio.py
```

Run the built-in self-test (no hardware or tools required):

```bash
python pyburn_studio.py --self-test
```

The self-test enqueues five simulated jobs (data, audio, rip, video DVD,
Blu-ray) and confirms the queue runs them all to completion in order.

## Building a single-file executable

The repository ships a reproducible build. Requirements are fully pinned in
`requirements.txt`, the build creates its own virtual environment, and the
PyInstaller recipe is the checked-in `pyburn_studio.spec`.

On Windows, double-click `BUILD_EXE.bat`. It verifies Python 3.13.12 through the
`py -3.13` launcher, creates `.\venv`, installs the pinned requirements, and
produces `dist\PyBurnStudio.exe` as one file. Full instructions, including the
manual PowerShell steps, are in `Docs/BUILD_EXE.md`.

## How to use it

1. Open the tab for the kind of disc you want (Data, Audio, Video DVD, Blu-ray,
   or Rip CD).
2. Add files or folders. For data and media discs the capacity gauge shows how
   full the disc will be.
3. Set options (volume label, verify, auto-blank rewritable media, eject after
   burn, and so on). Global defaults live in Settings.
4. Click the Queue Job button. The job goes into the queue at the bottom and
   starts when the drive is free.
5. Watch progress in the Queue tab, open the job log for detail, and find
   finished jobs in the History tab, where you can show the log, export it, or
   retry the job.

## Configuration and data locations

- Settings: `~/.pyburn_config.json`
- History: `~/.pyburn_history.json`
- Per-job logs: `~/.pyburn_logs/`
- Temporary build files: the temp directory set in Settings
  (default `~/PyBurn_Temp`)

## Platform notes

- Linux is the primary platform. Optical drives appear as `/dev/sr0` and the
  like. You may need your user in the `cdrom` group to access the drive.
- macOS works with the tools installed through Homebrew. Device paths differ
  (for example `/dev/disk2`).
- Windows is a first-class platform. Data discs, ISO, audio CD, blanking, media
  info, eject, and ripping run natively through the built-in Windows engine, with
  no external tools: writing goes straight to the drive using SPTI/MMC commands
  (for data, an ISO9660/Joliet image is built in pure Python first; audio is
  burned gapless in Disc-At-Once mode), and reading uses IOCTL. Video DVD and
  Blu-ray authoring run inside a WSL2 Linux environment and are then burned by the
  native Windows burner. The Setup screen reports what each feature can do, can
  download ffmpeg into a local tools folder, and can install WSL2 and its Linux
  tools for you. Installing WSL2 requires one Windows elevation approval and one
  reboot; everything else is automatic. If setup fails, the app writes a full log
  to pyburn_setup.log next to the program. The Windows build detects optical
  drives with a PowerShell CIM query (Win32_CDROMDrive) and falls back to the
  older WMIC only if that fails, because WMIC was removed from Windows 11 24H2.

## License

PyBurn Studio is released under the GNU General Public License, version 3. See
the `LICENSE` file for the full text.
