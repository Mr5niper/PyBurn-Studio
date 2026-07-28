# Building the PyBurn Studio executable

PyBurn Studio ships as a single-file Windows executable built with PyInstaller.
The build is reproducible: every dependency is pinned in `requirements.txt`, the
build makes its own clean virtual environment each time, and the PyInstaller
options are passed directly on the command line by `BUILD_EXE.bat`. There is no
checked-in spec file, and none is needed; PyInstaller may drop a generated
`pyburn_studio.spec` as a byproduct, but the build does not read it and it can be
deleted.

## Auto build (recommended)

Prerequisite: install Python 3.13.12.
Download: https://www.python.org/downloads/release/python-31312/
Enable the "py launcher" option during installation. The build resolves Python
through the launcher (`py -3.13`), so Python does not need to be on PATH.

1. Open the project folder.
2. Double-click `BUILD_EXE.bat`.
3. Find the single-file executable in the `dist` folder: `PyBurnStudio.exe`.

What the script does, in order:

1. Resolves Python 3.13 through the `py -3.13` launcher and checks it is exactly
   3.13.12. This works even when a different Python owns PATH. If the version is
   wrong or missing, it stops and prints the download link.
2. Checks that `pyburn.ico` and `version.txt` are present in the project root
   (the build needs both), and fails early with a clear message if either is
   missing.
3. Deletes any existing `.\venv` and creates a fresh one, so a stale dependency
   from a previous build can never carry over.
4. Upgrades pip and installs the pinned `requirements.txt`.
5. Verifies the burn-critical dependencies actually resolved (for example that
   comtypes is exactly 1.4.13 and PyQt6 imports), and fails the build rather than
   shipping an exe that cannot burn.
6. Runs PyInstaller with all options on the command line to produce the onefile
   `dist\PyBurnStudio.exe`.

## Manual build (Windows, PowerShell)

Prerequisite: install Python 3.13.12 as above. This mirrors what the batch file
does, if you would rather run it by hand.

1. Open PowerShell.
2. Change into the project folder, for example:
   `cd "C:\path\to\PyBurn-Studio"`
3. Create and activate a clean virtual environment:

   ```powershell
   py -3.13 -m venv .\venv
   .\venv\Scripts\Activate.ps1
   ```

4. Upgrade pip and install the pinned requirements:

   ```powershell
   python -m pip install --upgrade pip
   pip install -r requirements.txt
   ```

5. Build the single-file executable. This is the same command `BUILD_EXE.bat`
   runs:

   ```powershell
   pyinstaller -F --noupx --clean --noconfirm --windowed --name PyBurnStudio `
     --collect-all comtypes `
     --hidden-import comtypes.automation `
     --hidden-import comtypes._post_coinit `
     --hidden-import comtypes._post_coinit.unknwn `
     --hidden-import comtypes._post_coinit.misc `
     --collect-submodules pyburn `
     --hidden-import PyQt6.sip `
     --icon pyburn.ico --add-data "pyburn.ico;." --version-file version.txt `
     .\pyburn_studio.py
   ```

   The full command in the batch file also excludes a long list of unused PyQt6
   modules (WebEngine, Quick/QML, Charts, Multimedia, and so on) to keep the exe
   small; those `--exclude-module` flags are optional and only affect size.

The result is `dist\PyBurnStudio.exe`.

## Manual build (Linux/macOS)

A native single-file binary builds on Linux and macOS with the same PyInstaller
options, minus the Windows-only icon/metadata flags. The Python version pin in
`BUILD_EXE.bat` is Windows-specific, so build by hand:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
pyinstaller -F --noupx --clean --noconfirm --windowed --name PyBurnStudio \
  --collect-all comtypes \
  --collect-submodules pyburn \
  --hidden-import PyQt6.sip \
  ./pyburn_studio.py
```

The output is `dist/PyBurnStudio` (no extension). Confirm it works without a
display or any disc tools:

```bash
QT_QPA_PLATFORM=offscreen ./dist/PyBurnStudio --self-test
```

## What the build produces and why

- One file (onefile), windowed (no console window), because PyBurn Studio is a
  GUI app.
- All of comtypes is collected (`--collect-all comtypes` plus the
  `_post_coinit` hidden imports) so the parts of Windows that still use IMAPI2
  (media info, blanking, eject, and the audio fallback) work in the frozen exe,
  including generating the COM interfaces it needs at runtime.
- The `pyburn` package submodules are collected, because the GUI loads backend
  and queue modules dynamically and the module graph would otherwise miss some.
- `pyburn.ico` is embedded as the icon and Windows file metadata (company,
  version, description) is read from `version.txt`.
- UPX is disabled.

## Why the requirements are pinned

Pinning every dependency with `==` means the same set of wheels is installed on
every machine and every run, so the produced executable is the same each time.
If you bump a dependency, change it in `requirements.txt` so the pin stays the
single source of truth, and rebuild.

## Notes on the external tools

PyBurn Studio's Windows data and audio burning, and CD ripping, are native and
need no external tools. For Video DVD and Blu-ray authoring it drives Linux tools
(mkisofs, dvdauthor, tsMuxeR, growisofs, and so on) inside WSL2, and on Linux it
uses those tools directly. They are NOT bundled into the executable, by design;
they are large, platform-specific, and installed through the system (or via the
in-app Setup screen on Windows). When a needed tool is absent the app can run
that job in simulation mode so the interface still works.

## Troubleshooting

- "Failed to execute script": usually a missing dynamic import. Add the missing
  module as another `--hidden-import` in the PyInstaller command in
  `BUILD_EXE.bat` and rebuild. `PyQt6.sip` is already included.
- The window never appears: temporarily drop `--windowed` from the command (or
  add `--console`) to see stderr, or run the source with `python pyburn_studio.py`
  to read the traceback directly.
- A build succeeds but the exe cannot burn: check that the dependency
  verification step passed; a wrong comtypes version is the usual cause and the
  build is meant to stop on it.
- "Missing tools" at run time for DVD/Blu-ray: expected if WSL2 or the Linux
  tools are not installed. Use the in-app Setup screen to install them.
