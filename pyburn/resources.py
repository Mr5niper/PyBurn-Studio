from __future__ import annotations
import os
import sys
from functools import lru_cache

# Icon filename shipped next to the app and bundled into the onefile exe.
ICON_FILENAME = "pyburn.ico"


def resource_path(relative: str) -> str:
    """Resolve a bundled resource path in both source and frozen modes.

    In a PyInstaller onefile build the bootloader unpacks bundled data files
    into a temporary directory whose path is exposed as sys._MEIPASS. In source
    mode there is no _MEIPASS, so we resolve relative to the project root (the
    parent of this package directory). This is the same idea the sibling repos
    use for their icons, adapted for PyQt6.
    """
    base = getattr(sys, "_MEIPASS", None)
    if base:
        # Frozen: look inside the unpacked bundle.
        candidate = os.path.join(base, relative)
        if os.path.exists(candidate):
            return candidate
    # Source mode (or the bundled copy was placed at the package root): resolve
    # relative to the project root, i.e. the directory that contains the
    # `pyburn` package.
    here = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(here)
    candidate = os.path.join(project_root, relative)
    if os.path.exists(candidate):
        return candidate
    # Last resort: next to the executable (onefile exes chdir-independent).
    try:
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        candidate = os.path.join(exe_dir, relative)
        if os.path.exists(candidate):
            return candidate
    except Exception:
        pass
    # Return the best guess even if it does not exist; callers guard for this.
    return os.path.join(project_root, relative)


def icon_path() -> str:
    """Absolute path to the application icon, resolved for the current mode."""
    return resource_path(ICON_FILENAME)


@lru_cache(maxsize=1)
def app_icon():
    """Return a QIcon for the app icon, or None if it cannot be loaded.

    Cached so repeated dialogs do not re-read the file. Imported lazily so this
    module stays importable without Qt (e.g. during the headless self-test).
    """
    try:
        from PyQt6.QtGui import QIcon
    except Exception:
        return None
    p = icon_path()
    if not os.path.exists(p):
        return None
    ic = QIcon(p)
    if ic.isNull():
        return None
    return ic


def set_windows_app_id(app_id: str = "Mr5niper5oft.PyBurnStudio") -> None:
    """Tell Windows this process is its own application, not a Python host.

    Without an explicit AppUserModelID, a PyInstaller GUI process is treated by
    the Windows shell as generic Python, so the taskbar button shows the Python
    icon and groups under Python. Setting a distinct ID makes the taskbar use
    the window icon we set with setWindowIcon and groups the app under its own
    identity. No-op on non-Windows.
    """
    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
    except Exception:
        # Best-effort only; failure just falls back to default shell behavior.
        pass
