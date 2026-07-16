from __future__ import annotations
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Callable, Optional

# On-demand tool acquisition for Windows.
#
# ffmpeg: downloaded from an official build to a local tools\ folder next to the
#   exe. ffmpeg is LGPL/GPL; fetching an official build to the user's own
#   machine at runtime is fine. We do NOT bundle it in the exe (that would drag
#   GPL obligations onto the whole package).
# WSL2: we cannot install the WSL feature silently (it needs admin + reboot), so
#   we launch the official `wsl --install` elevated via a UAC prompt and let the
#   user reboot. After that, the WSLManager.provision() step installs the Linux
#   authoring tools inside the distro.
#
# cdrtools is deliberately NOT fetched on Windows: IMAPI2 already covers data
# and ISO burning natively, and cdrtools' license is contested.

OnLog = Callable[[str], None]

# Official ffmpeg Windows build (BtbN release assets, redistributable).
# A release-latest asset name that is stable across builds.
FFMPEG_WIN_URL = (
    "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/"
    "ffmpeg-master-latest-win64-gpl.zip"
)


def tools_dir() -> Path:
    """The local tools\\ folder next to the executable (or project root in dev)."""
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).parent
    else:
        # project root = parent of the pyburn package
        base = Path(__file__).resolve().parent.parent.parent
    d = base / "tools"
    d.mkdir(parents=True, exist_ok=True)
    return d


def is_windows() -> bool:
    return platform.system().lower() == "windows"


def ffmpeg_installed_path() -> Optional[str]:
    """Return the path to a downloaded ffmpeg in tools\\, if present."""
    d = tools_dir()
    for cand in (d / "ffmpeg.exe", d / "bin" / "ffmpeg.exe", d / "ffmpeg", d / "bin" / "ffmpeg"):
        if cand.is_file():
            return str(cand)
    # Also search any extracted subfolder (BtbN zips nest in a versioned dir).
    for sub in d.glob("**/ffmpeg.exe"):
        return str(sub)
    for sub in d.glob("**/ffmpeg"):
        if sub.is_file():
            return str(sub)
    return None


def download_ffmpeg(on_log: OnLog, progress: Optional[Callable[[int], None]] = None) -> Optional[str]:
    """Download and extract an official ffmpeg Windows build into tools\\.

    Returns the path to ffmpeg.exe on success, or None on failure. Network
    access is required. Uses urllib from the standard library so there is no
    extra dependency.
    """
    if not is_windows():
        on_log("ffmpeg auto-download is only wired for Windows; on Linux install it via your package manager.")
        return None
    import urllib.request

    d = tools_dir()
    existing = ffmpeg_installed_path()
    if existing:
        on_log(f"ffmpeg already present at {existing}")
        return existing

    zip_path = d / "ffmpeg_download.zip"
    on_log("Downloading ffmpeg (official BtbN build). This may take a minute...")
    try:
        def _hook(block_num, block_size, total_size):
            if progress and total_size > 0:
                pct = int(min(100, (block_num * block_size / total_size) * 100))
                progress(pct)
        urllib.request.urlretrieve(FFMPEG_WIN_URL, str(zip_path), _hook)
    except Exception as e:
        on_log(f"ffmpeg download failed: {e}")
        return None

    on_log("Extracting ffmpeg...")
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            # Extract only the ffmpeg/ffprobe binaries to keep tools\ tidy.
            members = zf.namelist()
            wanted = [m for m in members if m.endswith(("ffmpeg.exe", "ffprobe.exe"))]
            if not wanted:
                # Fall back to extracting everything if names differ.
                zf.extractall(d)
            else:
                for m in wanted:
                    # Flatten into tools\ root.
                    data = zf.read(m)
                    target = d / Path(m).name
                    target.write_bytes(data)
    except Exception as e:
        on_log(f"ffmpeg extract failed: {e}")
        return None
    finally:
        try:
            zip_path.unlink(missing_ok=True)
        except Exception:
            pass

    path = ffmpeg_installed_path()
    if path:
        on_log(f"ffmpeg installed at {path}")
    else:
        on_log("ffmpeg extraction finished but ffmpeg.exe was not found.")
    return path


def launch_wsl_install_elevated(on_log: OnLog) -> bool:
    """Launch the official `wsl --install` with a UAC elevation prompt.

    This cannot be silent: enabling the WSL Windows feature requires
    Administrator rights and a reboot. We trigger the elevated command via
    ShellExecute 'runas', which shows the standard Windows UAC dialog the user
    approves. After it completes the user must REBOOT, then reopen Setup.
    Returns True if the elevated process was launched (not that it finished).
    """
    if not is_windows():
        on_log("wsl --install is Windows-only.")
        return False
    try:
        import ctypes
        # ShellExecuteW(hwnd, 'runas', file, params, dir, show)
        # Run: wsl --install   (installs WSL2 + default Ubuntu distro)
        rc = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", "wsl.exe", "--install", None, 1
        )
        # ShellExecute returns > 32 on success.
        if int(rc) > 32:
            on_log("Windows is installing WSL2 (approve the UAC prompt). "
                   "REBOOT when it finishes, then reopen Setup and click "
                   "'Install Linux tools in WSL2'.")
            return True
        on_log(f"Could not launch elevated wsl --install (code {rc}). "
               f"Open an Administrator PowerShell and run: wsl --install")
        return False
    except Exception as e:
        on_log(f"Failed to launch wsl --install: {e}. "
               f"Open an Administrator PowerShell and run: wsl --install")
        return False


def tsmuxer_present(wsl) -> bool:
    """True if tsMuxeR is actually runnable inside the distro right now."""
    if not wsl or not getattr(wsl, "info", None) or not wsl.info.available:
        return False
    try:
        rc = wsl.run(["bash", "-lc", "command -v tsMuxeR >/dev/null 2>&1 || command -v tsmuxer >/dev/null 2>&1"])
        return rc == 0
    except Exception:
        return False


def install_tsmuxer_in_wsl(wsl, on_log: OnLog) -> bool:
    """Fetch tsMuxeR inside the WSL2 distro (not in apt by default).

    Downloads the official tsMuxeR Linux release (a .zip) into /usr/local/bin
    inside the distro, then VERIFIES it is runnable. Returns True only if
    tsMuxeR is actually present afterward. Only Blu-ray needs it.

    The shell script is delivered as base64 and decoded inside WSL, then run.
    This avoids the quote-mangling that happens when a long script with nested
    single/double quotes and $(...) is passed inline through
    `wsl -- bash -lc "..."`: base64 is pure ASCII with no shell metacharacters,
    so nothing in the script can be misinterpreted on the way in. That inline
    mangling was the real reason the install failed inside the app while the
    same commands worked when typed by hand.
    """
    if not wsl or not getattr(wsl, "info", None) or not wsl.info.available:
        on_log("WSL2 not available for tsMuxeR install.")
        return False

    # A clean, normal shell script. No escaping tricks needed here because it is
    # transported as base64, not embedded in another quoted string.
    body = r'''#!/bin/bash
cd /tmp || exit 1
apt-get install -y curl unzip tar >/dev/null 2>&1 || true
url=$(curl -s https://api.github.com/repos/justdan96/tsMuxer/releases/latest | grep browser_download_url | grep -i linux | head -n1 | cut -d '"' -f4)
if [ -z "$url" ]; then echo "TSMUX_FAIL: no linux asset found (GitHub blocked by network/policy?)"; exit 1; fi
echo "TSMUX: url=$url"
fname=$(basename "$url")
curl -L -o "$fname" "$url" || { echo "TSMUX_FAIL: download failed"; exit 1; }
sz=$(stat -c%s "$fname" 2>/dev/null || echo 0)
echo "TSMUX: bytes=$sz"
rm -rf tsm_extract; mkdir -p tsm_extract
case "$fname" in
  *.zip) unzip -o "$fname" -d tsm_extract >/dev/null 2>&1 || { echo "TSMUX_FAIL: unzip failed"; exit 1; } ;;
  *.tar.gz|*.tgz) tar -xzf "$fname" -C tsm_extract || { echo "TSMUX_FAIL: untar failed"; exit 1; } ;;
  *.tar) tar -xf "$fname" -C tsm_extract || { echo "TSMUX_FAIL: untar failed"; exit 1; } ;;
  *) echo "TSMUX_FAIL: unknown archive $fname"; exit 1 ;;
esac
bin=$(find tsm_extract -type f -iname 'tsmuxer' | head -n1)
if [ -z "$bin" ]; then echo "TSMUX_FAIL: binary not in archive"; find tsm_extract -type f; exit 1; fi
echo "TSMUX: bin=$bin"
cp "$bin" /usr/local/bin/tsMuxeR || { echo "TSMUX_FAIL: copy failed"; exit 1; }
chmod +x /usr/local/bin/tsMuxeR
if [ -x /usr/local/bin/tsMuxeR ]; then echo "TSMUX_OK"; else echo "TSMUX_FAIL: not runnable after copy"; exit 1; fi
'''
    import base64
    b64 = base64.b64encode(body.encode("utf-8")).decode("ascii")
    # Decode the base64 to a file inside WSL, then execute it. Only ASCII and
    # simple redirection cross the boundary, so nothing can be mangled.
    runner = f"echo {b64} | base64 -d > /tmp/pyburn_tsmux.sh && bash /tmp/pyburn_tsmux.sh"
    try:
        wsl.run(["bash", "-lc", runner], on_out=on_log, on_err=on_log)
    except Exception as e:
        on_log(f"tsMuxeR install error: {e}")
    # Trust a real post-check, not the script's exit code alone.
    ok = tsmuxer_present(wsl)
    if ok:
        on_log("tsMuxeR verified present.")
    else:
        on_log("tsMuxeR still not present after install attempt.")
    return ok
