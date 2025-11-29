# PyBurn Studio
A disc burning application for creating Data CDs/DVDs/Blu‑ray, Audio CDs, Video DVDs, Blu‑ray (BDMV), and ripping Audio CDs. Cross‑platform UI with Python and PyQt6; leverages standard command‑line tools under the hood.
## Features
- Burn data discs (CD/DVD/BD)
- Create Audio CDs (with optional CD‑Text)
- Author Video DVDs
- Create Blu‑ray (BDMV) discs
- Rip Audio CDs (MP3/FLAC/WAV)
- Job queue with logs and history
- Optional verification after burn
- Simulation mode when tools are missing
- Automatic/media‑aware burn speed selection
## Quick Start
1) Install Python requirements:
```bash
pip install -r requirements.txt
```
2) Install system tools (Linux example: Debian/Ubuntu):
```bash
sudo apt update
sudo apt install cdparanoia cdrdao cdrtools dvdauthor ffmpeg flac genisoimage \
                 growisofs lame wodim xorriso eject dvd+rw-tools
```
Notes:
- mkisofs/isoinfo are provided by genisoimage (or cdrtools on some distros).
- growisofs/dvd+rw-tools are needed for DVD/BD burning and media info.
- tsMuxeR (for Blu‑ray authoring) must be installed separately (not in most repos).
3) Run:
```bash
python pyburn_studio.py
```
4) Optional self-test (non-destructive, uses simulation backend):
```bash
python pyburn_studio.py --self-test
```
## Device Detection
- Linux: Now prefers real device nodes (/dev/srX), shows transport (USB/SATA) and vendor/model. This fixes issues with always picking “0,0,0”.
- macOS: Uses default /dev/disk2 (customize in Settings).
- Windows: Limited. Consider WSL2 for full tool support.
Open Settings, click Scan, and select your optical drive (e.g., “/dev/sr1 [USB] VENDOR MODEL”). The chosen device will be saved to ~/.pyburn_config.json.
## System Tools by Platform
- Linux (Debian/Ubuntu):
```bash
sudo apt install cdparanoia cdrdao cdrtools dvdauthor ffmpeg flac genisoimage \
                 growisofs lame wodim xorriso eject dvd+rw-tools
```
- macOS (Homebrew):
```bash
brew install cdrtools dvd+rw-tools ffmpeg cdrdao cdparanoia \
             lame flac dvdauthor xorriso
# (tsMuxeR for Blu-ray authoring: install manually from its website)
```
- Windows:
  - Recommended: WSL2 with Ubuntu and use Linux commands above.
  - Native Windows support is limited (you’ll need ports of these tools; not officially supported).
## Troubleshooting
- Missing tools:
  - Install the packages above, or enable “Simulate when tools are missing” in Settings.
- No drives found (Linux):
  - Ensure the drive is connected and powered.
  - Add your user to cdrom group: `sudo usermod -a -G cdrom $USER` then log out/in.
  - Open Settings and click Scan.
- Verification fails:
  - Try a slower speed and high-quality media; enable “Verify after burn” in Settings.
- UI issues (spinboxes, arrows):
  - Fixed in the stylesheet to ensure QSpinBox arrows and keyboard work reliably.
## License
GPL-3.0. See LICENSE.
## Full Repository Structure (alphabetical)
```
pyburn_studio/
├── LICENSE
├── pyburn_studio.py
├── pyburn_studio.spec
├── requirements.txt
├── docs/
│   ├── BUILD_GUIDE.md
│   ├── README.md
│   └── TECHNICAL_DOC.md
└── pyburn/
    ├── __init__.py
    ├── style.py
    ├── core/
    │   ├── config.py
    │   ├── devices.py
    │   ├── history.py
    │   ├── jobs.py
    │   └── tools.py
    ├── gui/
    │   ├── dialogs.py
    │   ├── main_window.py
    │   ├── tabs.py
    │   └── widgets.py
    └── services/
        ├── backend.py
        ├── burn.py
        ├── exec.py
        ├── media.py
        ├── metadata.py
        ├── progress.py
        ├── queue.py
        └── verify.py
```
