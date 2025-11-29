# PyBurn Studio - Build & Distribution Guide
This guide shows how to build a standalone executable with PyInstaller and package it for distribution.
## Prerequisites
- Python 3.9+
- pip
- PyInstaller 5+ (installed below)
- PyQt6 6.4.0+
- System tools are NOT bundled (users must install them separately)
## Setup
Create and activate a virtual environment (recommended), then install requirements and build tools:
```bash
python -m venv .venv
# Linux/macOS:
source .venv/bin/activate
# Windows:
.venv\Scripts\activate
pip install -r requirements.txt
pip install pyinstaller
```
Verify the app runs from source:
```bash
python pyburn_studio.py
python pyburn_studio.py --self-test
```
## Build with PyInstaller (using the spec)
From the pyburn_studio folder:
```bash
pyinstaller pyburn_studio.spec
```
- Output:
  - Linux/macOS: dist/PyBurnStudio
  - Windows: dist/PyBurnStudio.exe
If you prefer CLI options instead of the spec:
```bash
pyinstaller --noconsole --onefile pyburn_studio.py
```
Notes:
- onefile vs onedir: Use --onefile for a single executable. The provided spec produces a GUI build; you can switch to onedir by generating a new spec with `pyi-makespec`.
- If you see missing Qt plugins at runtime, add hidden imports in the spec, e.g., `PyQt6.sip`.
## Testing the Build
- Launch the built executable and confirm:
  - App starts and shows the main window
  - Settings opens and saves preferences
  - Device scan lists optical drives (or uses defaults if none)
  - Queue accepts jobs and logs output
  - Self-test still works:
```bash
./dist/PyBurnStudio --self-test
```
## Packaging (Optional)
- Linux: distribute the single binary under dist/
- AppImage: wrap the binary in an AppImage if desired
- macOS: codesign the .app bundle if you produce one
- Windows: create an installer (NSIS/Inno Setup) if desired
## Troubleshooting
- “Failed to execute script”:
  - Add missing PyQt6 modules to `hiddenimports` (e.g., PyQt6.sip).
- App starts with no output:
  - Temporarily build with a console: `--console` and check stderr/stdout.
- Missing external tools:
  - This is expected. The app uses system tools; instruct users to install them (see README).
## Full Repository Structure
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
