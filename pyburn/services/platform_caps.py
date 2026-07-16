from __future__ import annotations
import platform
import shutil
import subprocess
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional
from ..core.tools import ToolFinder


class Engine(str, Enum):
    """Which mechanism actually performs a step."""
    CLI = "cli"          # Unix command-line tool (native on Linux, or a real Windows build)
    IMAPI2 = "imapi2"    # Windows native burning API (data/ISO/audio/blank/eject)
    IOCTL = "ioctl"      # Windows native CD read (ripping)
    WSL = "wsl"          # tool run inside a WSL2 distro (authoring/generation only)
    SIM = "sim"          # simulated (no real hardware/tools)
    NONE = "none"        # no path available on this platform


def is_windows() -> bool:
    return platform.system().lower() == "windows"


def is_linux() -> bool:
    return platform.system().lower() == "linux"


def is_macos() -> bool:
    return platform.system().lower() == "darwin"


@dataclass
class WSLInfo:
    """State of the Windows Subsystem for Linux on this machine."""
    available: bool = False          # a usable distro exists (platform + distro both present)
    platform_present: bool = False   # the `wsl` command works but there may be NO distro yet
    version2: bool = False           # at least one distro is WSL version 2
    default_distro: Optional[str] = None
    distros: List[str] = field(default_factory=list)
    tools_present: Dict[str, bool] = field(default_factory=dict)  # tool -> found inside WSL


def _no_window_kwargs() -> dict:
    """subprocess kwargs that suppress the console window on Windows.

    Without CREATE_NO_WINDOW, each wsl/apt invocation flashes a console window,
    which is what caused the blinking-cmd storm during setup. No effect off
    Windows.
    """
    if platform.system().lower() != "windows":
        return {}
    flags = 0
    try:
        flags |= subprocess.CREATE_NO_WINDOW  # 0x08000000
    except Exception:
        flags |= 0x08000000
    return {"creationflags": flags}


def _run(cmd: List[str], timeout: float = 8.0) -> tuple[int, str, str]:
    try:
        # stdin=DEVNULL is critical: on a machine without WSL installed,
        # `wsl -l -v` prints "not installed" and then INTERACTIVELY prompts
        # "Press any key to install..." waiting up to 60s. With stdin closed the
        # prompt cannot block us; the command returns immediately so detection
        # does not hang.
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL, **_no_window_kwargs())
        return p.returncode, (p.stdout or ""), (p.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, "", "timed out"
    except Exception as e:
        return 1, "", str(e)


class WSLManager:
    """Detects WSL2 and runs Unix tools inside it, with path translation.

    On Windows the app can hand authoring/generation work (dvdauthor, tsMuxeR,
    xorriso, cdparanoia, and so on) to a WSL2 distro, which produces a file that
    the Windows side then burns. This class is the boundary: it detects the
    distro, checks which tools are installed inside it, translates Windows paths
    to their /mnt/<drive> form, and runs a tool inside WSL2.

    Everything here is Windows-only in practice; on Linux the app uses the CLI
    tools directly and never touches this class.
    """

    # Tools we may need to run inside WSL2 when a native Windows build is absent.
    WSL_TOOL_CANDIDATES = {
        "mkisofs": ["mkisofs", "genisoimage"],
        "xorriso": ["xorriso"],
        "growisofs": ["growisofs"],
        "cdrdao": ["cdrdao"],
        "ffmpeg": ["ffmpeg"],
        "dvdauthor": ["dvdauthor"],
        "tsMuxeR": ["tsMuxeR", "tsmuxer"],
        "cdparanoia": ["cdparanoia"],
        "cd-discid": ["cd-discid"],
    }

    # apt package names for first-run provisioning inside a Debian/Ubuntu distro.
    APT_PACKAGES = [
        "genisoimage", "wodim", "dvdauthor", "xorriso", "cdrdao",
        "ffmpeg", "cdparanoia", "cd-discid", "growisofs",
    ]

    def __init__(self):
        self.info = WSLInfo()
        # Set True by install_distro when WSL reports the Subsystem feature is
        # not installed, so the caller knows to run the elevated feature install.
        self._feature_absent = False

    def detect(self, on_log=None) -> WSLInfo:
        log = on_log or (lambda s: None)
        info = WSLInfo()
        if not is_windows():
            self.info = info
            return info
        if not shutil.which("wsl"):
            # No wsl command at all: WSL platform is not installed.
            log("detect: wsl.exe not found on PATH")
            self.info = info
            return info
        # wsl.exe exists on every Windows 11 machine as a built-in stub, even
        # when the WSL feature is NOT installed. So we do NOT trust the command
        # existing. We use POSITIVE detection: the platform is only considered
        # present if `wsl -l -v` either returns a real distro listing OR
        # explicitly reports an empty-but-installed state. Anything else (any
        # error, any "not installed" text, any unrecognized output, a non-zero
        # exit) is treated as feature-absent, so the button runs the elevated
        # installer. This avoids guessing at error strings, which is what missed
        # the clean-machine case before.
        rc, out, err = _run(["wsl", "-l", "-v"])
        # WSL output is UTF-16 on many builds; strip nulls before matching.
        out_clean = (out or "").replace("\x00", "")
        err_clean = (err or "").replace("\x00", "")
        low = (out_clean + err_clean).lower()
        log(f"detect: `wsl -l -v` rc={rc}")
        for _ln in (out_clean + err_clean).splitlines():
            if _ln.strip():
                log(f"detect: | {_ln.strip()}")

        # Parse any real distro rows from `wsl -l -v`.
        distros: List[str] = []
        default_distro = None
        version2 = False
        for ln in out_clean.splitlines():
            s = ln.strip()
            if not s:
                continue
            # Skip the header row.
            if s.lower().startswith("name") or ("state" in s.lower() and "version" in s.lower()):
                continue
            default = s.startswith("*")
            s2 = s.lstrip("*").strip()
            parts = s2.split()
            if len(parts) < 2:
                continue
            # A real row looks like: NAME  STATE  VERSION  (version is 1 or 2)
            ver = parts[-1]
            if ver not in ("1", "2"):
                continue
            name = parts[0]
            distros.append(name)
            if default:
                default_distro = name
            if ver == "2":
                version2 = True

        if distros:
            # Real distros exist: feature present AND usable.
            info.platform_present = True
            info.distros = distros
            info.default_distro = default_distro or distros[0]
            info.version2 = version2
            info.available = True
            log(f"detect: distros found {distros}; available=True")
            self.info = info
            return info

        # No distro rows parsed. Decide between two very different states:
        #   (a) feature PRESENT but empty  -> install a distro
        #   (b) feature ABSENT             -> install the WSL feature
        # The message "has no installed distributions" is only ever produced
        # when the WSL feature IS installed, so it is the decisive signal for
        # (a). It also contains guidance text mentioning "wsl.exe --install",
        # so we must NOT let a generic "--install" match flip us to (b). The
        # empty-but-installed signal wins.
        empty_but_installed = (
            "no installed distributions" in low
            or "has no installed" in low
        )
        if empty_but_installed:
            info.platform_present = True   # present, zero distros
            log("detect: feature present, zero distros -> platform_present=True, available=False")
            self.info = info
            return info

        # Otherwise look for explicit feature-absent wording. "is not installed"
        # is the reliable phrase wsl prints when the feature itself is missing.
        says_absent = (
            "is not installed" in low
            or "optional component is not enabled" in low
            or "please enable the virtual machine platform" in low
        )
        info.platform_present = not says_absent
        log(f"detect: no distros, empty_but_installed=False, says_absent={says_absent} "
            f"-> platform_present={info.platform_present}")
        self.info = info
        return info

    def install_distro(self, distro: str = "Ubuntu", on_log=None, timeout: float = 1800.0) -> bool:
        """Install a WSL2 distro non-interactively and wait for it to finish.

        Uses `wsl --install -d <distro> --no-launch` so no interactive Ubuntu
        first-run window is required; the distro is registered without demanding
        a UNIX username/password up front. Commands the app later runs default
        to root inside the distro, which is what the apt provisioning needs.

        Requires the WSL platform to already be present (info.platform_present).
        Does not need elevation for the distro step itself on current Windows.
        Returns True if a distro is present after the attempt.
        """
        log = on_log or (lambda s: None)
        if not is_windows():
            return False
        if not shutil.which("wsl"):
            log("WSL platform not present; run Install WSL2 first.")
            return False
        log(f"Installing WSL distro '{distro}' (this downloads a few hundred MB)...")
        # --no-launch registers the distro without opening the interactive setup.
        rc, out, err = _run(["wsl", "--install", "-d", distro, "--no-launch"], timeout=timeout)
        msg = ((out or "") + (err or "")).strip()
        if msg:
            for ln in msg.replace("\x00", "").splitlines():
                if ln.strip():
                    log(ln.strip())
        low = msg.lower()

        # If WSL says the Subsystem itself is not installed, the distro step
        # cannot work: the WSL2 Windows feature has to be installed first. Match
        # only the reliable "is not installed" / feature-component phrases, NOT a
        # generic "--install" mention (that also appears in the harmless
        # "add a distribution" guidance and would cause a false feature-install
        # loop).
        subsystem_absent = (
            "is not installed" in low
            or "optional component is not enabled" in low
            or "please enable the virtual machine platform" in low
        )
        if subsystem_absent:
            log("WSL2 feature is not installed on this machine; it must be installed first.")
            self._feature_absent = True
            return False

        # Some Windows builds ignore --no-launch; retry without it if needed.
        if rc != 0 and "no-launch" in low:
            rc, out2, err2 = _run(["wsl", "--install", "-d", distro], timeout=timeout)
            low2 = ((out2 or "") + (err2 or "")).lower()
            if ("is not installed" in low2
                    or "optional component is not enabled" in low2):
                log("WSL2 feature is not installed on this machine; it must be installed first.")
                self._feature_absent = True
                return False
        # Give WSL a moment, then re-detect.
        import time as _t
        _t.sleep(3)
        self.detect()
        if self.info.available:
            log(f"Distro '{self.info.default_distro}' is now installed.")
            # Ensure the distro is initialized enough to run commands as root.
            _run(["wsl", "-d", self.info.default_distro, "-u", "root", "--", "true"], timeout=30)
            return True
        # Fall back: list what IS available online so the caller can report it.
        rc3, online, _ = _run(["wsl", "--list", "--online"], timeout=30)
        if rc3 == 0 and online.strip():
            log("Distro install did not complete. Distributions available to this machine:")
            for ln in online.replace("\x00", "").splitlines():
                if ln.strip():
                    log("  " + ln.strip())
        else:
            log("Distro install did not complete, and no online distro list was available.")
        return False

    def win_to_wsl_path(self, win_path: str) -> str:
        """Translate C:\\dir\\file to /mnt/c/dir/file for use inside WSL2."""
        p = str(win_path).replace("\\", "/")
        if len(p) >= 2 and p[1] == ":":
            drive = p[0].lower()
            rest = p[2:]
            if not rest.startswith("/"):
                rest = "/" + rest
            return f"/mnt/{drive}{rest}"
        return p

    def find_tool(self, logical: str) -> bool:
        """Return True if a logical tool is available inside the default distro."""
        if not self.info.available or not self.info.default_distro:
            return False
        for exe in self.WSL_TOOL_CANDIDATES.get(logical, [logical]):
            rc, out, _ = _run(["wsl", "-d", self.info.default_distro, "which", exe], timeout=6)
            if rc == 0 and out.strip():
                self.info.tools_present[logical] = True
                return True
        self.info.tools_present[logical] = False
        return False

    def refresh_tool_presence(self) -> Dict[str, bool]:
        result: Dict[str, bool] = {}
        for logical in self.WSL_TOOL_CANDIDATES.keys():
            result[logical] = self.find_tool(logical)
        self.info.tools_present = result
        return result

    def run(self, args: List[str], on_out=None, on_err=None, as_root: bool = True) -> int:
        """Run a command inside the default distro, streaming output.

        Runs as root by default so no sudo password is ever needed. A distro
        installed with --no-launch has no interactive user, and root is the
        correct account for the apt provisioning and authoring work.
        """
        if not self.info.available or not self.info.default_distro:
            raise RuntimeError("WSL2 is not available")
        full = ["wsl", "-d", self.info.default_distro]
        if as_root:
            full += ["-u", "root"]
        full += ["--"] + args
        proc = subprocess.Popen(full, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1, **_no_window_kwargs())
        import threading

        def pump(stream, cb):
            if not stream or not cb:
                return
            for line in iter(stream.readline, ""):
                cb(line.rstrip("\n"))
            try:
                stream.close()
            except Exception:
                pass

        t1 = threading.Thread(target=pump, args=(proc.stdout, on_out), daemon=True)
        t2 = threading.Thread(target=pump, args=(proc.stderr, on_err), daemon=True)
        t1.start(); t2.start()
        code = proc.wait()
        t1.join(timeout=5); t2.join(timeout=5)
        return code

    def provision(self, on_out=None, on_err=None) -> bool:
        """Install the Unix toolchain inside the default distro via apt.

        Runs as root with DEBIAN_FRONTEND=noninteractive so there is no password
        prompt and no interactive package configuration. Requires a distro to be
        present (install_distro handles getting one).
        """
        if not self.info.available or not self.info.default_distro:
            return False
        log = on_out or (lambda s: None)
        env_prefix = ["env", "DEBIAN_FRONTEND=noninteractive"]
        log("Updating package lists inside WSL2...")
        rc = self.run(env_prefix + ["apt-get", "update", "-y"], on_out=on_out, on_err=on_err)
        if rc != 0:
            log("apt-get update failed inside WSL2.")
            return False
        log("Installing disc tools inside WSL2 (this can take a few minutes)...")
        rc = self.run(env_prefix + ["apt-get", "install", "-y"] + self.APT_PACKAGES, on_out=on_out, on_err=on_err)
        if rc != 0:
            log("apt-get install failed.")
            return False
        self.refresh_tool_presence()
        log("WSL2 toolchain install complete.")
        return True


# Which engine handles each stage of each job, per platform. This is the single
# place that encodes the whole strategy the design settled on:
#   Linux  -> everything via CLI tools.
#   Windows-> native tools where a real Windows build exists; IMAPI2 for the
#             burn/blank/eject/media steps; IOCTL for ripping reads; WSL2 for
#             authoring/generation steps that have no Windows build; then the
#             generated image is burned by IMAPI2.
@dataclass
class Capability:
    engine: Engine
    available: bool
    detail: str = ""


class CapabilityResolver:
    """Resolves, per job type, how each stage will run on this machine."""

    def __init__(self, tools: ToolFinder, wsl: Optional[WSLManager] = None):
        self.tools = tools
        self.wsl = wsl or WSLManager()
        if is_windows():
            self.wsl.detect()

    # --- helpers -------------------------------------------------------------
    def _native(self, logical: str) -> bool:
        return bool(self.tools.find(logical))

    def _wsl_has(self, logical: str) -> bool:
        return is_windows() and self.wsl.info.available and self.wsl.find_tool(logical)

    def _imapi2_ok(self) -> bool:
        # IMAPI2 exists on Windows Vista+; we assume present on Windows and let
        # the backend surface any real COM error at run time.
        return is_windows()

    # --- burn/generate step resolution --------------------------------------
    def resolve_data(self) -> Capability:
        if is_linux() or is_macos():
            if self._native("mkisofs") and (self._native("growisofs") or self._native("cdrecord")):
                return Capability(Engine.CLI, True, "mkisofs + growisofs/cdrecord")
            return Capability(Engine.SIM, False, "missing mkisofs/growisofs/cdrecord")
        # Windows
        if self._native("mkisofs") and (self._native("growisofs") or self._native("cdrecord")):
            return Capability(Engine.CLI, True, "native Windows cdrtools build")
        if self._imapi2_ok():
            return Capability(Engine.IMAPI2, True, "Windows IMAPI2")
        return Capability(Engine.SIM, False, "no burn engine")

    def resolve_audio(self) -> Capability:
        if is_linux() or is_macos():
            if self._native("ffmpeg") and self._native("cdrdao"):
                return Capability(Engine.CLI, True, "ffmpeg + cdrdao")
            return Capability(Engine.SIM, False, "missing ffmpeg/cdrdao")
        # Windows: decode with native ffmpeg (or WSL ffmpeg), burn tracks via IMAPI2.
        has_ffmpeg = self._native("ffmpeg") or self._wsl_has("ffmpeg")
        if has_ffmpeg and self._imapi2_ok():
            src = "native ffmpeg" if self._native("ffmpeg") else "WSL2 ffmpeg"
            return Capability(Engine.IMAPI2, True, f"{src} decode + IMAPI2 audio burn")
        if self._imapi2_ok():
            return Capability(Engine.IMAPI2, True, "IMAPI2 audio burn (ffmpeg missing; WAV input only)")
        return Capability(Engine.SIM, False, "no audio burn engine")

    def resolve_video_dvd(self) -> Capability:
        if is_linux() or is_macos():
            if self._native("ffmpeg") and self._native("dvdauthor") and self._native("mkisofs"):
                return Capability(Engine.CLI, True, "ffmpeg + dvdauthor + mkisofs")
            return Capability(Engine.SIM, False, "missing ffmpeg/dvdauthor/mkisofs")
        # Windows: author in WSL2 (dvdauthor has no Windows build), burn ISO via IMAPI2.
        if self._wsl_has("dvdauthor") and (self._wsl_has("mkisofs")) and (self._wsl_has("ffmpeg") or self._native("ffmpeg")) and self._imapi2_ok():
            return Capability(Engine.WSL, True, "WSL2 authors VIDEO_TS/ISO, IMAPI2 burns")
        return Capability(Engine.NONE, False, "needs WSL2 with dvdauthor+mkisofs+ffmpeg")

    def resolve_video_bd(self) -> Capability:
        if is_linux() or is_macos():
            if self._native("ffmpeg") and self._native("tsMuxeR") and (self._native("mkisofs") or self._native("xorriso")):
                return Capability(Engine.CLI, True, "ffmpeg + tsMuxeR + mkisofs/xorriso")
            return Capability(Engine.SIM, False, "missing ffmpeg/tsMuxeR/mkisofs")
        # Windows: author BDMV in WSL2, burn UDF image via IMAPI2.
        if self._wsl_has("tsMuxeR") and (self._wsl_has("xorriso") or self._wsl_has("mkisofs")) and (self._wsl_has("ffmpeg") or self._native("ffmpeg")) and self._imapi2_ok():
            return Capability(Engine.WSL, True, "WSL2 authors BDMV/UDF, IMAPI2 burns")
        return Capability(Engine.NONE, False, "needs WSL2 with tsMuxeR+xorriso+ffmpeg")

    def resolve_rip(self) -> Capability:
        if is_linux() or is_macos():
            if self._native("cdparanoia"):
                return Capability(Engine.CLI, True, "cdparanoia")
            return Capability(Engine.SIM, False, "missing cdparanoia")
        # Windows: read CDDA via native IOCTL, encode with ffmpeg.
        if self._imapi2_ok():  # same Windows gate; IOCTL is always present on Windows
            enc = "native ffmpeg" if self._native("ffmpeg") else ("WSL2 ffmpeg" if self._wsl_has("ffmpeg") else "WAV only (no encoder)")
            return Capability(Engine.IOCTL, True, f"Windows IOCTL read + {enc}")
        return Capability(Engine.SIM, False, "no rip engine")

    def resolve_all(self) -> Dict[str, Capability]:
        return {
            "DATA": self.resolve_data(),
            "AUDIO": self.resolve_audio(),
            "VIDEO_DVD": self.resolve_video_dvd(),
            "VIDEO_BD": self.resolve_video_bd(),
            "RIP": self.resolve_rip(),
        }
