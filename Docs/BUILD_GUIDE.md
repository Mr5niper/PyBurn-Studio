```markdown
# PyBurn Studio - Build and Distribution Guide

## Overview

This guide walks you through building PyBurn Studio into a standalone executable using PyInstaller. The result is a single-file application that can run on systems without Python installed.

## Prerequisites

### Required Software

- Python 3.9 or higher
- PyInstaller 5.0 or higher
- PyQt6 6.4.0 or higher
- Git (optional, for version control)

### Install Build Tools

```bash
pip install pyinstaller pyqt6 requests
```

## Project Structure

Your project should be organized like this:

```
PyBurnStudio/
├── pyburn_studio.py          # Main application file (2300+ lines)
├── README.md                  # User documentation
├── CHANGELOG.md               # Version history
├── LICENSE                    # Your chosen license
├── requirements.txt           # Python dependencies
└── build/                     # Created during build (ignore)
```

## Step 1: Prepare Your Environment

### Create requirements.txt

Create a file named `requirements.txt` with the following content:

```
PyQt6>=6.4.0
requests>=2.28.0
```

### Verify Your Code

Before building, make sure the application runs correctly:

```bash
python pyburn_studio.py
```

Run the self-test:

```bash
python pyburn_studio.py --self-test
```

If you see "Self-test passed", you're ready to build.

## Step 2: Create PyInstaller Spec File

PyInstaller uses a spec file to configure the build. Create `pyburn_studio.spec`:

```python
# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

a = Analysis(
    ['pyburn_studio.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[
        'PyQt6.QtCore',
        'PyQt6.QtGui',
        'PyQt6.QtWidgets',
        'requests',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='PyBurnStudio',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
```

**Key settings explained:**

- `console=False`: Creates a GUI application (no terminal window)
- `onefile=True` (implicit in EXE config above): Single executable file
- `name='PyBurnStudio'`: Output filename
- `upx=True`: Compress executable (optional, requires UPX installed)

## Step 3: Build the Executable

### Linux Build

```bash
pyinstaller pyburn_studio.spec
```

Output location: `dist/PyBurnStudio`

### macOS Build

```bash
pyinstaller pyburn_studio.spec
```

Output location: `dist/PyBurnStudio`

To create a .app bundle, modify the spec file and add:

```python
app = BUNDLE(
    exe,
    name='PyBurnStudio.app',
    icon=None,
    bundle_identifier='com.yourname.pyburn',
)
```

### Windows Build (via WSL or native Python)

```bash
pyinstaller pyburn_studio.spec
```

Output location: `dist\PyBurnStudio.exe`

## Step 4: Test the Executable

### Quick Test

Run the built executable:

```bash
./dist/PyBurnStudio
```

### Self-Test

The self-test flag should still work:

```bash
./dist/PyBurnStudio --self-test
```

### Full Test Checklist

- [ ] Application launches without errors
- [ ] Settings dialog opens and saves preferences
- [ ] Device scan detects optical drives (or shows simulation mode)
- [ ] Can add files to data burn tab
- [ ] Job queue accepts and displays jobs
- [ ] History tab loads (even if empty)
- [ ] About dialog shows tool detection
- [ ] Application closes cleanly

## Step 5: Package for Distribution

### Linux Distribution

**Option 1: Standalone Binary**

Simply distribute the `dist/PyBurnStudio` file. Users run it directly.

**Option 2: AppImage**

Use `appimagetool` to create a portable .AppImage:

```bash
# Create AppDir structure
mkdir -p PyBurnStudio.AppDir/usr/bin
cp dist/PyBurnStudio PyBurnStudio.AppDir/usr/bin/

# Create desktop file
cat > PyBurnStudio.AppDir/PyBurnStudio.desktop << EOF
[Desktop Entry]
Name=PyBurn Studio
Exec=PyBurnStudio
Icon=pyburn
Type=Application
Categories=AudioVideo;DiscBurning;
EOF

# Create AppImage
appimagetool PyBurnStudio.AppDir
```

**Option 3: .deb Package**

Create a Debian package structure:

```
pyburn-studio_1.7.2/
├── DEBIAN/
│   └── control
└── usr/
    ├── bin/
    │   └── pyburn-studio
    └── share/
        ├── applications/
        │   └── pyburn-studio.desktop
        └── doc/
            └── pyburn-studio/
                └── README.md
```

Build with: `dpkg-deb --build pyburn-studio_1.7.2`

### macOS Distribution

**Option 1: Signed .app Bundle**

If you have an Apple Developer account:

```bash
codesign --deep --force --verify --verbose --sign "Developer ID Application: YOUR NAME" dist/PyBurnStudio.app
```

**Option 2: DMG Image**

Create a distributable DMG:

```bash
hdiutil create -volname "PyBurn Studio" -srcfolder dist/PyBurnStudio.app -ov -format UDZO PyBurnStudio-1.7.2.dmg
```

### Windows Distribution

**Option 1: Standalone .exe**

Distribute `dist\PyBurnStudio.exe` directly.

**Option 2: Installer with NSIS**

Create `installer.nsi`:

```nsis
!define APPNAME "PyBurn Studio"
!define COMPANYNAME "YourName"
!define DESCRIPTION "Optical Disc Authoring"
!define VERSIONMAJOR 1
!define VERSIONMINOR 7
!define VERSIONBUILD 2

Name "${APPNAME}"
OutFile "PyBurnStudio-Setup-1.7.2.exe"
InstallDir "$PROGRAMFILES\${APPNAME}"

Page directory
Page instfiles

Section "install"
    SetOutPath $INSTDIR
    File "dist\PyBurnStudio.exe"
    WriteUninstaller "$INSTDIR\uninstall.exe"
SectionEnd

Section "Uninstall"
    Delete "$INSTDIR\PyBurnStudio.exe"
    Delete "$INSTDIR\uninstall.exe"
    RMDir "$INSTDIR"
SectionEnd
```

Build: `makensis installer.nsi`

## Troubleshooting

### "Failed to execute script" Error

**Cause:** Missing dependencies or PyQt6 plugins

**Solution:** Add to spec file under `hiddenimports`:

```python
hiddenimports=[
    'PyQt6.QtCore',
    'PyQt6.QtGui',
    'PyQt6.QtWidgets',
    'PyQt6.sip',
    'requests',
],
```

### Application Won't Start (No Error)

**Cause:** Console output suppressed

**Solution:** Temporarily change `console=False` to `console=True` in spec file, rebuild, and check terminal output for errors.

### "Tool Not Found" Warnings

**Expected behavior:** PyBurn Studio requires external system tools (mkisofs, cdrecord, ffmpeg, etc.)

The built executable only packages Python code. Users still need to install system tools:

```bash
# Debian/Ubuntu
sudo apt install genisoimage wodim growisofs cdrdao ffmpeg cdparanoia lame flac dvdauthor

# macOS
brew install cdrtools dvd+rw-tools ffmpeg cdrdao cdparanoia lame flac dvdauthor

# Windows (WSL2)
# Same as Debian/Ubuntu inside WSL environment
```

**Alternative:** Enable "Simulate when tools are missing" in Settings for testing without tools.

### Large Executable Size

**Cause:** PyQt6 includes many libraries

**Solutions:**

1. Enable UPX compression (`upx=True` in spec file)
2. Exclude unused PyQt6 modules in spec file
3. Accept ~80-120MB size as normal for PyQt6 applications

### ImportError: PyQt6 Not Found

**Cause:** Virtual environment not activated during build

**Solution:**

```bash
# Create and activate venv
python -m venv venv
source venv/bin/activate  # Linux/macOS
# or
venv\Scripts\activate  # Windows

# Install dependencies
pip install pyqt6 requests pyinstaller

# Build
pyinstaller pyburn_studio.spec
```

## Distribution Checklist

Before releasing your build:

- [ ] Test on clean system without Python installed
- [ ] Verify all tabs load correctly
- [ ] Check Settings dialog saves preferences
- [ ] Confirm device detection works (or simulation mode activates)
- [ ] Run self-test successfully
- [ ] Test with actual optical drive if available
- [ ] Include README.md with system tool installation instructions
- [ ] Include LICENSE file
- [ ] Include CHANGELOG.md
- [ ] Version number matches in code and filenames

## File Sizes Reference

Expected executable sizes (approximate):

- Linux: 80-100 MB
- macOS: 90-110 MB  
- Windows: 85-105 MB

With UPX compression: 40-60 MB (may cause antivirus false positives)

## Advanced: Multi-Platform Build

### Using Docker for Linux Build

Create `Dockerfile`:

```dockerfile
FROM python:3.11-slim
RUN apt-get update && apt-get install -y binutils
RUN pip install pyinstaller pyqt6 requests
WORKDIR /build
COPY pyburn_studio.py .
COPY pyburn_studio.spec .
RUN pyinstaller pyburn_studio.spec
```

Build:

```bash
docker build -t pyburn-builder .
docker create --name extract pyburn-builder
docker cp extract:/build/dist/PyBurnStudio ./PyBurnStudio-linux
docker rm extract
```

### GitHub Actions CI/CD

Create `.github/workflows/build.yml`:

```yaml
name: Build Executables

on:
  push:
    tags:
      - 'v*'

jobs:
  build-linux:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      - run: pip install pyinstaller pyqt6 requests
      - run: pyinstaller pyburn_studio.spec
      - uses: actions/upload-artifact@v3
        with:
          name: PyBurnStudio-Linux
          path: dist/PyBurnStudio

  build-macos:
    runs-on: macos-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      - run: pip install pyinstaller pyqt6 requests
      - run: pyinstaller pyburn_studio.spec
      - uses: actions/upload-artifact@v3
        with:
          name: PyBurnStudio-macOS
          path: dist/PyBurnStudio

  build-windows:
    runs-on: windows-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      - run: pip install pyinstaller pyqt6 requests
      - run: pyinstaller pyburn_studio.spec
      - uses: actions/upload-artifact@v3
        with:
          name: PyBurnStudio-Windows
          path: dist/PyBurnStudio.exe
```

## Support

If you encounter build issues:

1. Check PyInstaller documentation: https://pyinstaller.org
2. Verify PyQt6 is properly installed: `python -c "import PyQt6"`
3. Test the source code runs before building
4. Enable console output temporarily for debugging
5. Check the `build/` directory for detailed logs

## Next Steps

After successful build:

1. Create release notes
2. Upload to GitHub Releases or your distribution platform
3. Provide installation instructions for system tools
4. Consider creating platform-specific packages (.deb, .rpm, .dmg)
5. Sign executables if distributing publicly (especially macOS/Windows)