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
        from .imapi2_backend import IMAPI2Backend
        o = self.job.options
        self._backend = IMAPI2Backend()
        if self.job.job_type == JobType.DATA:
            self._backend.burn_data(self.job.files, self.job.device, o.temp_dir, o.volume_label,
                                    self.sig_status.emit, self.sig_progress.emit, self.sig_log.emit,
                                    auto_blank=o.auto_blank, eject_after=o.eject_after)
            self.sig_finished.emit(True, "Data disc burned successfully (IMAPI2)")
        elif self.job.job_type == JobType.AUDIO:
            wavs = self._decode_audio_to_wav(self.job.files, o.temp_dir)
            self._backend.burn_audio(wavs, self.job.device, self.sig_status.emit,
                                     self.sig_progress.emit, self.sig_log.emit, eject_after=o.eject_after)
            self.sig_finished.emit(True, "Audio CD created successfully (IMAPI2)")
        else:
            self.sig_finished.emit(False, f"IMAPI2 does not handle {self.job.job_type.value}")

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
                subprocess.run([ffmpeg, "-y", "-i", str(src), "-ar", "44100", "-ac", "2",
                                "-sample_fmt", "s16", str(target)], capture_output=True, text=True)
                wavs.append(target)
            else:
                # No encoder: only usable if the input already is WAV.
                if src.suffix.lower() == ".wav":
                    shutil.copy2(src, target)
                    wavs.append(target)
                else:
                    raise RuntimeError("No ffmpeg available to decode audio; provide WAV files or install ffmpeg")
            self.sig_progress.emit(int((idx / max(1, len(files))) * 40))
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

    def cancel(self):
        if self._backend is not None and hasattr(self._backend, "cancel"):
            self._backend.cancel()
