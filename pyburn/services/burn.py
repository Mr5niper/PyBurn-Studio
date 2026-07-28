from __future__ import annotations
from PyQt6.QtCore import QObject, pyqtSignal
from pathlib import Path
import platform
import shutil
from ..core.jobs import Job, JobType
from ..core.tools import ToolFinder
from .backend import RealBackend, SimulatedBackend
from .platform_caps import CapabilityResolver, Engine, WSLManager, is_windows


class BurnWorker(QObject):
    sig_status = pyqtSignal(str)
    sig_progress = pyqtSignal(int)
    sig_log = pyqtSignal(str)
    sig_finished = pyqtSignal(bool, str)

    def __init__(self, job: Job, tools: ToolFinder, simulate_if_missing: bool = True,
                 wsl: WSLManager | None = None):
        super().__init__()
        self.job = job
        self.tools = tools
        self.simulate_if_missing = simulate_if_missing
        self.resolver = CapabilityResolver(tools, wsl)
        # Resolve which engine handles this job on this platform.
        cap_map = {
            JobType.DATA: self.resolver.resolve_data,
            JobType.AUDIO: self.resolver.resolve_audio,
            JobType.VIDEO_DVD: self.resolver.resolve_video_dvd,
            JobType.VIDEO_BD: self.resolver.resolve_video_bd,
            JobType.RIP: self.resolver.resolve_rip,
        }
        self.cap = cap_map[job.job_type]()
        self._backend = None  # set lazily per engine in start()

    def _mk_cli_backend(self):
        # On the CLI path, real vs simulated depends on the tools actually being
        # present (the resolver already told us CLI is the engine, but if it
        # returned SIM the tools are missing).
        if self.cap.engine == Engine.SIM:
            return SimulatedBackend(self.tools)
        return RealBackend(self.tools)

    def start(self):
        try:
            o = self.job.options
            eng = self.cap.engine
            self.sig_log.emit(f"Engine for {self.job.job_type.value}: {eng.value} ({self.cap.detail})")

            # NONE means there is genuinely no path on this platform (e.g. DVD
            # authoring on Windows with no WSL2). Fail clearly instead of faking.
            if eng == Engine.NONE:
                self.sig_finished.emit(
                    False,
                    f"Not available on this system: {self.cap.detail}. "
                    f"See the About screen for what to install."
                )
                return

            # SIM: simulated backend regardless of platform.
            if eng == Engine.SIM:
                self._backend = SimulatedBackend(self.tools)
                self._run_cli_like(sim=True)
                return

            # CLI: the existing Unix-tool backend (native Linux, or Windows builds).
            if eng == Engine.CLI:
                self._backend = RealBackend(self.tools)
                self._run_cli_like(sim=False)
                return

            # IMAPI2: Windows-native burning (data/ISO/audio).
            if eng == Engine.IMAPI2:
                self._run_imapi2()
                return

            # IOCTL: Windows-native ripping.
            if eng == Engine.IOCTL:
                self._run_ioctl_rip()
                return

            # WSL: author in WSL2, burn via IMAPI2 (DVD/BD on Windows).
            if eng == Engine.WSL:
                self._run_wsl_author()
                return

            self.sig_finished.emit(False, f"Unknown engine: {eng}")
        except Exception as e:
            self.sig_finished.emit(False, str(e))

    # ---- CLI / simulated path (unchanged behavior) --------------------------
    def _run_cli_like(self, sim: bool):
        o = self.job.options
        b = self._backend
        tag = " (simulated)" if sim else ""
        if self.job.job_type == JobType.DATA:
            b.burn_data(self.job.files, self.job.device, o.temp_dir, o.volume_label, o.speed,
                        o.verify, self.sig_status.emit, self.sig_progress.emit, self.sig_log.emit,
                        auto_blank=o.auto_blank, eject_after=o.eject_after, dummy=o.dummy)
            self.sig_finished.emit(True, f"Data disc burned successfully{tag}")
        elif self.job.job_type == JobType.AUDIO:
            b.burn_audio(self.job.files, self.job.device, o.temp_dir, o.speed, self.sig_status.emit,
                         self.sig_progress.emit, self.sig_log.emit, eject_after=o.eject_after,
                         album_title=o.album_title, album_performer=o.album_performer,
                         track_titles=o.track_titles, track_performers=o.track_performers)
            self.sig_finished.emit(True, f"Audio CD created successfully{tag}")
        elif self.job.job_type == JobType.VIDEO_DVD:
            b.burn_video_dvd(self.job.files, self.job.device, o.temp_dir, o.speed, self.sig_status.emit,
                             self.sig_progress.emit, self.sig_log.emit, auto_blank=o.auto_blank, eject_after=o.eject_after)
            self.sig_finished.emit(True, f"Video DVD created successfully{tag}")
        elif self.job.job_type == JobType.VIDEO_BD:
            b.burn_video_bd(self.job.files, self.job.device, o.temp_dir, o.speed, self.sig_status.emit,
                            self.sig_progress.emit, self.sig_log.emit, auto_blank=o.auto_blank, eject_after=o.eject_after)
            self.sig_finished.emit(True, f"Blu-ray created successfully{tag}")
        elif self.job.job_type == JobType.RIP:
            out_dir = o.output_dir or Path.home() / "Music"
            out_dir.mkdir(parents=True, exist_ok=True)
            b.rip_cd(self.job.device, out_dir, o.rip_format, o.rip_bitrate,
                     self.sig_status.emit, self.sig_progress.emit, self.sig_log.emit,
                     track_titles=o.track_titles)
            self.sig_finished.emit(True, f"CD ripped to {out_dir}{tag}")

    # ---- IMAPI2 path (Windows native) --------------------------------------
    def _run_imapi2(self):
        # DATA burns run in a DEDICATED subprocess (see below). This keeps the
        # exact working burn_data code, but moves progress OUT of COM entirely:
        # the subprocess prints a percentage computed by a plain timer thread that
        # never touches COM, so it cannot overlap Write() and corrupt the burn.
        # Audio stays in-process (it already works via TrackAtOnce).
        if self.job.job_type == JobType.DATA:
            self._run_imapi2_data_subprocess()
            return
        if self.job.job_type == JobType.AUDIO:
            self._run_spti_audio_subprocess()
            return

        from .imapi2_backend import IMAPI2Backend
        o = self.job.options
        # CRITICAL: this runs on a Qt worker thread. IMAPI2 (COM) must have a COM
        # apartment on this thread. Plain CoInitialize() gives a Single-Threaded
        # Apartment (STA), and IMAPI2's disc-master enumeration deadlocks in an
        # STA that has no Windows message pump (a worker thread has none): the
        # first enumeration call blocks forever. Initialize a MULTI-THREADED
        # apartment (MTA) instead, which does not require a message pump.
        _com_ready = False
        try:
            import comtypes
            try:
                comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
            except Exception:
                import ctypes
                ctypes.windll.ole32.CoInitializeEx(None, 0x0)
            _com_ready = True
            self.sig_log.emit("COM initialized (MTA) on burn worker thread")
        except Exception as e:
            self.sig_log.emit(f"COM init (MTA) warning: {e}")
        try:
            self.sig_log.emit("Creating IMAPI2 backend...")
            self._backend = IMAPI2Backend()
            if self.job.job_type == JobType.AUDIO:
                self.sig_log.emit("Decoding audio to WAV...")
                wavs = self._decode_audio_to_wav(self.job.files, o.temp_dir)
                self.sig_log.emit(f"Decoded {len(wavs)} tracks; dispatching to burn_audio (IMAPI2)...")
                self._backend.burn_audio(wavs, self.job.device, self.sig_status.emit,
                                         self.sig_progress.emit, self.sig_log.emit,
                                         eject_after=o.eject_after, speed=o.speed)
                self.sig_finished.emit(True, "Audio CD created successfully (IMAPI2)")
            else:
                self.sig_finished.emit(False, f"IMAPI2 does not handle {self.job.job_type.value}")
        finally:
            if _com_ready:
                try:
                    import comtypes
                    comtypes.CoUninitialize()
                except Exception:
                    pass

    def _run_imapi2_data_subprocess(self):
        """Burn a data disc via a one-shot CLI subprocess (this same exe).

        The child owns COM for the burn and runs the exact working burn_data. It
        streams PROGRESS/STATUS/LOG/RESULT on stdout; the percentage is produced
        by a plain timer thread in the child that makes no COM calls, so burn
        integrity is fully decoupled from progress. The GUI never touches comtypes
        for the data burn.
        """
        import sys as _sys
        import subprocess
        import os
        o = self.job.options

        if getattr(_sys, "frozen", False):
            cmd = [_sys.executable, "cli-burn-data"]
        else:
            script = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__)))), "pyburn_studio.py")
            cmd = [_sys.executable, script, "cli-burn-data"]
        # If the Data tab composed an explicit disc layout (rename/new-folder/
        # move), pass it as a JSON tree file and the child authors via
        # build_tree(). Otherwise fall back to the flat --file list exactly as
        # before, so the original path is untouched when no tree is supplied.
        self._tree_tmp = None
        disc_tree = getattr(o, "disc_tree", None)
        if disc_tree:
            import json, tempfile
            try:
                fd, tpath = tempfile.mkstemp(suffix=".json", prefix="pyburn_tree_",
                                             dir=str(o.temp_dir))
                with os.fdopen(fd, "w", encoding="utf-8") as tf:
                    json.dump(disc_tree, tf)
                self._tree_tmp = tpath
                cmd += ["--tree", tpath]
            except Exception as e:
                self.sig_log.emit(f"Could not stage disc layout, using flat list: {e}")
                for f in self.job.files:
                    cmd += ["--file", str(f)]
        else:
            for f in self.job.files:
                cmd += ["--file", str(f)]
        cmd += ["--device", str(self.job.device),
                "--volume", str(o.volume_label or "DATA_DISC"),
                "--temp-dir", str(o.temp_dir),
                "--speed", str(o.speed or "Auto")]
        if o.auto_blank:
            cmd += ["--auto-blank"]
        if o.eject_after:
            cmd += ["--eject"]
        if getattr(o, "dummy", False):
            cmd += ["--dummy"]

        self.sig_log.emit("Launching isolated burn process (cli-burn-data)...")
        creationflags = 0
        try:
            creationflags = subprocess.CREATE_NO_WINDOW
        except Exception:
            creationflags = 0x08000000
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1, creationflags=creationflags,
            )
        except Exception as e:
            self.sig_finished.emit(False, f"Could not start burn process: {e}")
            return

        self._burn_proc = proc
        result_ok = None
        result_msg = "Data disc burned successfully (IMAPI2)"
        try:
            for raw in proc.stdout:
                line = raw.rstrip("\r\n")
                if not line:
                    continue
                if line.startswith("PROGRESS "):
                    try:
                        self.sig_progress.emit(int(line[9:].strip()))
                    except Exception:
                        pass
                elif line.startswith("STATUS "):
                    self.sig_status.emit(line[7:])
                elif line.startswith("LOG "):
                    self.sig_log.emit(line[4:])
                elif line.startswith("RESULT OK"):
                    result_ok = True
                elif line.startswith("RESULT FAIL"):
                    result_ok = False
                    msg = line[len("RESULT FAIL"):].strip()
                    if msg:
                        result_msg = msg
                else:
                    self.sig_log.emit(line)
        except Exception as e:
            self.sig_log.emit(f"Error reading burn process output: {e}")
        rc = proc.wait()
        if result_ok is None:
            result_ok = (rc == 0)
            if not result_ok:
                result_msg = f"Burn process exited with code {rc}"
        # Remove the staged disc-layout temp file if we created one.
        tt = getattr(self, "_tree_tmp", None)
        if tt:
            try:
                import os as _os
                _os.unlink(tt)
            except Exception:
                pass
            self._tree_tmp = None
        self.sig_finished.emit(bool(result_ok), result_msg)

    def _cli_base_cmd(self, subcommand: str):
        """Build the [exe, subcommand] prefix to re-invoke ourselves."""
        import sys as _sys
        if getattr(_sys, "frozen", False):
            return [_sys.executable, subcommand]
        import os
        script = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))), "pyburn_studio.py")
        return [_sys.executable, script, subcommand]

    def _pump_burn_subprocess(self, cmd, ok_msg: str):
        """Launch cmd, translate its PROGRESS/STATUS/LOG/RESULT stdout into
        signals, and emit sig_finished. Shared by data and audio burns."""
        import subprocess
        self.sig_log.emit(f"Launching isolated burn process ({cmd[1] if len(cmd) > 1 else '?'})...")
        try:
            creationflags = subprocess.CREATE_NO_WINDOW
        except Exception:
            creationflags = 0x08000000
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1, creationflags=creationflags,
            )
        except Exception as e:
            self.sig_finished.emit(False, f"Could not start burn process: {e}")
            return
        self._burn_proc = proc
        result_ok = None
        result_msg = ok_msg
        try:
            for raw in proc.stdout:
                line = raw.rstrip("\r\n")
                if not line:
                    continue
                if line.startswith("PROGRESS "):
                    try:
                        self.sig_progress.emit(int(line[9:].strip()))
                    except Exception:
                        pass
                elif line.startswith("STATUS "):
                    self.sig_status.emit(line[7:])
                elif line.startswith("LOG "):
                    self.sig_log.emit(line[4:])
                elif line.startswith("RESULT OK"):
                    result_ok = True
                elif line.startswith("RESULT FAIL"):
                    result_ok = False
                    msg = line[len("RESULT FAIL"):].strip()
                    if msg:
                        result_msg = msg
                else:
                    self.sig_log.emit(line)
        except Exception as e:
            self.sig_log.emit(f"Error reading burn process output: {e}")
        rc = proc.wait()
        if result_ok is None:
            result_ok = (rc == 0)
            if not result_ok:
                result_msg = f"Burn process exited with code {rc}"
        self.sig_finished.emit(bool(result_ok), result_msg)

    def _run_spti_audio_subprocess(self):
        """Decode inputs to WAV, then burn a CD-DA audio disc via the SPTI
        subprocess (cli-burn-audio). No COM anywhere."""
        o = self.job.options
        try:
            self.sig_status.emit("Decoding audio to WAV...")
            wavs = self._decode_audio_to_wav(self.job.files, o.temp_dir)
            self.sig_log.emit(f"Decoded {len(wavs)} tracks; launching SPTI audio burn...")
        except Exception as e:
            self.sig_finished.emit(False, f"Audio decode failed: {e}")
            return
        cmd = self._cli_base_cmd("cli-burn-audio")
        for w in wavs:
            cmd += ["--file", str(w)]
        cmd += ["--device", str(self.job.device), "--speed", str(o.speed or "Auto")]
        if o.eject_after:
            cmd += ["--eject"]
        if getattr(o, "dummy", False):
            cmd += ["--dummy"]
        self._pump_burn_subprocess(cmd, "Audio CD created successfully (SPTI/MMC)")

    def _decode_audio_to_wav(self, files, temp_dir: Path):
        # IMAPI2 audio wants 44100/16/stereo WAV. Use native ffmpeg if present,
        # else WSL2 ffmpeg, else assume inputs are already WAV.
        ffmpeg = self.tools.find("ffmpeg")
        out_dir = Path(temp_dir) / "pyburn_audio_wav"
        out_dir.mkdir(parents=True, exist_ok=True)
        wavs = []
        for idx, src in enumerate(files, start=1):
            target = out_dir / f"track_{idx:02d}.wav"
            src = Path(src)
            if ffmpeg:
                import subprocess
                self.sig_status.emit(f"Decoding track {idx}/{len(files)} (ffmpeg)...")
                # -map_metadata -1 drops the source MP3 tags so ffmpeg does not
                # write a LIST/INFO chunk into the WAV. -rf64 never and an
                # explicit pcm_s16le codec keep it a plain 44100/16/stereo PCM
                # WAV, which is what the audio CD path expects. -bitexact avoids
                # ffmpeg writing its own encoder-info metadata chunk.
                subprocess.run([ffmpeg, "-y", "-i", str(src),
                                "-map_metadata", "-1", "-bitexact",
                                "-ar", "44100", "-ac", "2",
                                "-c:a", "pcm_s16le", "-sample_fmt", "s16",
                                str(target)], capture_output=True, text=True)
                wavs.append(target)
            else:
                # No encoder: only usable if the input already is WAV.
                if src.suffix.lower() == ".wav":
                    shutil.copy2(src, target)
                    wavs.append(target)
                else:
                    raise RuntimeError("No ffmpeg available to decode audio; provide WAV files or install ffmpeg")
            self.sig_progress.emit(int((idx / max(1, len(files))) * 100))
        return wavs

    # ---- IOCTL rip path (Windows native) -----------------------------------
    def _run_ioctl_rip(self):
        from .ioctl_ripper import IOCTLRipper
        o = self.job.options
        out_dir = o.output_dir or Path.home() / "Music"
        self._backend = IOCTLRipper()
        ffmpeg = self.tools.find("ffmpeg")
        self._backend.rip(self.job.device, out_dir, o.rip_format, o.rip_bitrate,
                          self.sig_status.emit, self.sig_progress.emit, self.sig_log.emit,
                          track_titles=o.track_titles, ffmpeg_path=ffmpeg)
        self.sig_finished.emit(True, f"CD ripped to {out_dir} (Windows IOCTL)")

    # ---- WSL author + IMAPI2 burn path -------------------------------------
    def _run_wsl_author(self):
        from .wsl_backend import WSLAuthorBackend
        o = self.job.options
        # The burn half of this path uses IMAPI2 (COM) on this worker thread, so
        # the COM apartment must be initialized here too (see _run_imapi2).
        _com_ready = False
        try:
            import comtypes
            comtypes.CoInitialize()
            _com_ready = True
        except Exception as e:
            self.sig_log.emit(f"COM init warning: {e}")
        try:
            self._backend = WSLAuthorBackend(self.resolver.wsl)
            if self.job.job_type == JobType.VIDEO_DVD:
                self._backend.burn_video_dvd(self.job.files, self.job.device, self.sig_status.emit,
                                            self.sig_progress.emit, self.sig_log.emit,
                                            auto_blank=o.auto_blank, eject_after=o.eject_after)
                self.sig_finished.emit(True, "Video DVD created successfully (WSL2 author + IMAPI2 burn)")
            elif self.job.job_type == JobType.VIDEO_BD:
                self._backend.burn_video_bd(self.job.files, self.job.device, self.sig_status.emit,
                                           self.sig_progress.emit, self.sig_log.emit,
                                           auto_blank=o.auto_blank, eject_after=o.eject_after)
                self.sig_finished.emit(True, "Blu-ray created successfully (WSL2 author + IMAPI2 burn)")
            else:
                self.sig_finished.emit(False, f"WSL path does not handle {self.job.job_type.value}")
        finally:
            if _com_ready:
                try:
                    import comtypes
                    comtypes.CoUninitialize()
                except Exception:
                    pass

    def cancel(self):
        proc = getattr(self, "_burn_proc", None)
        if proc is not None:
            try:
                proc.terminate()
            except Exception:
                pass
        if self._backend is not None and hasattr(self._backend, "cancel"):
            self._backend.cancel()
