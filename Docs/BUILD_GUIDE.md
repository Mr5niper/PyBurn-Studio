# PyBurn Studio - Build Guide

The build instructions now live in `Docs/BUILD_EXE.md`, which covers the
automatic Windows build (`BUILD_EXE.bat`), the manual PowerShell steps, and the
Linux/macOS build. The requirements are pinned in `requirements.txt` and the
PyInstaller command is run inline by the build (no spec file is checked in or
used; any generated `pyburn_studio.spec` is a throwaway byproduct), so every
build produces the same single-file executable.

See `Docs/BUILD_EXE.md`.
