# Building the PyBurn Studio executable

PyBurn Studio ships as a single-file Windows executable built with PyInstaller.
The build is reproducible: every dependency is pinned in `requirements.txt`, the
build makes its own virtual environment, and the PyInstaller recipe is the
checked-in `pyburn_studio.spec`. Two paths are described below. The automatic
one is recommended.

## Auto build (recommended)

Prerequisite: install Python 3.13.12.
Download: https://www.python.org/downloads/release/python-31312/
Enable the "py launcher" option during installation. The build resolves Python
through the launcher, so Python does not need to be on PATH.

1. Open the project folder.
2. Double-click `BUILD_EXE.bat`.
   - The script resolves Python 3.13 through the `py -3.13` launcher and checks
     that it matches 3.13.12 exactly. This works even when a different Python
     version owns PATH.
   - If the correct version is not found, it pauses and prints the download
     link.
3. Wait for the build to finish. The script creates a virtual environment in
   `.\venv`, installs the pinned `requirements.txt`, and runs PyInstaller with
   the project spec.
4. Find the single-file executable in the `dist` folder: `PyBurnStudio.exe`.

## Manual build (Windows, PowerShell)

Prerequisite: install Python 3.13.12 as above.

1. Open PowerShell.
2. Change into the project folder, for example:
   `cd "C:\path\to\PyBurn-Studio"`
3. Create a virtual environment:

   ```powershell
   py -3.13 -m venv .\venv
   ```

4. Activate it:

   ```powershell
   .\venv\Scripts\Activate.ps1
   ```

5. Upgrade pip and install the pinned requirements:

   ```powershell
   python -m pip install --upgrade pip
   pip install -r requirements.txt
   ```

6. Build the single-file executable with the project spec:

   ```powershell
   pyinstaller --clean --noconfirm pyburn_studio.spec
   ```

The result is `dist\PyBurnStudio.exe`.

## Manual build (Linux/macOS)

The same spec builds a native single-file binary on Linux and macOS. The build
machine still needs the pinned Python packages, but the Python version pin in
`BUILD_EXE.bat` is Windows-specific, so build by hand:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
pyinstaller --clean --noconfirm pyburn_studio.spec
```

The output is `dist/PyBurnStudio` (no extension). You can confirm it works
without a display or any disc tools:

```bash
QT_QPA_PLATFORM=offscreen ./dist/PyBurnStudio --self-test
```

## What the spec does

- Builds one file (onefile), windowed (no console window) because PyBurn Studio
  is a GUI app.
- Collects all of PyQt6 so the Qt platform plugins and sip ship inside the exe.
  A missing platform plugin is the most common reason a PyInstaller GUI builds
  but will not launch, so this is collected in full on purpose.
- Collects the `pyburn` package submodules, because the GUI loads the backend
  and queue modules dynamically and the module graph would otherwise miss some.
- Embeds `pyburn.ico` as the executable icon and reads Windows file metadata
  (company, version, description) from `version.txt`.
- Does not use UPX.

## Why the requirements are pinned

Pinning every dependency with `==` means the same set of wheels is installed on
every machine and every run, so the produced executable is the same each time.
If you bump a dependency, change it in `requirements.txt` so the pin stays the
single source of truth, and rebuild.

## Notes on the external tools

PyBurn Studio drives external command-line tools at run time (cdrecord,
growisofs, cdrdao, ffmpeg, dvdauthor, cdparanoia, and so on). These are NOT
bundled into the executable, by design; they are large, platform-specific, and
usually installed through the system package manager. On Windows the supported
way to provide them is WSL2. When the tools are absent the app runs in
simulation mode so the interface still works.

## Troubleshooting

- "Failed to execute script": usually a missing dynamic import. Add the missing
  module to `hiddenimports` in `pyburn_studio.spec` and rebuild. `PyQt6.sip` is
  already listed.
- The window never appears: build once with `console=True` in the spec to see
  stderr, or run the source with `python pyburn_studio.py` to read the traceback
  directly.
- "Missing tools" at run time: expected if the disc tools are not installed.
  Install them, or enable simulation in Settings for interface testing.
