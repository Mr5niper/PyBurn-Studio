from __future__ import annotations
from PyQt6.QtCore import QObject, pyqtSignal
from pathlib import Path
from ..core.jobs import Job, JobType
from ..core.tools import ToolFinder
from .backend import RealBackend, SimulatedBackend


class BurnWorker(QObject):
    sig_status = pyqtSignal(str)
    sig_progress = pyqtSignal(int)
    sig_log = pyqtSignal(str)
    sig_finished = pyqtSignal(bool, str)

    def __init__(self, job: Job, tools: ToolFinder, simulate_if_missing: bool = True):
        super().__init__()
        self.job = job
        self.tools = tools
        req = {
            JobType.DATA: ["mkisofs"] + (["growisofs"] if tools.find("growisofs") else ["cdrecord"]),
            JobType.AUDIO: ["ffmpeg", "cdrdao"],
            JobType.VIDEO_DVD: ["ffmpeg", "dvdauthor", "mkisofs"],
            JobType.VIDEO_BD: ["ffmpeg", "tsMuxeR"] + (["mkisofs"] if tools.find("mkisofs") else ["xorriso"]),
            JobType.RIP: ["cdparanoia"],
        }[job.job_type]
        # Verification does NOT force simulation on its own. VerificationTools
        # uses readom when present and falls back to isoinfo, and finally to a
        # pass-through if neither exists. Requiring readom here would push a
        # user who only has isoinfo into a fully simulated burn just for asking
        # to verify, which is surprising. So only require a verify tool when the
        # user turned verify on AND neither readom nor isoinfo is available;
        # in that case the burn itself can still run for real, so we do not add
        # it to req (which gates real-vs-simulated). Instead we warn via log.
        self._verify_unavailable = False
        if job.job_type == JobType.DATA and job.options.verify:
            if not tools.find("readom") and not tools.find("isoinfo"):
                self._verify_unavailable = True

        missing = tools.missing(req)
        self.backend = SimulatedBackend(tools) if (missing and simulate_if_missing) else RealBackend(tools)
        self._missing = missing

    def start(self):
        try:
            if self._verify_unavailable:
                self.sig_log.emit("WARNING: verify requested but no readom/isoinfo found; "
                                  "burn will proceed without verification.")
            o = self.job.options
            if self.job.job_type == JobType.DATA:
                self.backend.burn_data(self.job.files, self.job.device, o.temp_dir, o.volume_label, o.speed,
                                       o.verify, self.sig_status.emit, self.sig_progress.emit, self.sig_log.emit,
                                       auto_blank=o.auto_blank, eject_after=o.eject_after, dummy=o.dummy)
                self.sig_finished.emit(True, "Data disc burned successfully" if not self._missing else "Simulated data burn complete")
            elif self.job.job_type == JobType.AUDIO:
                self.backend.burn_audio(self.job.files, self.job.device, o.temp_dir, o.speed, self.sig_status.emit,
                                        self.sig_progress.emit, self.sig_log.emit, eject_after=o.eject_after,
                                        album_title=o.album_title, album_performer=o.album_performer,
                                        track_titles=o.track_titles, track_performers=o.track_performers)
                self.sig_finished.emit(True, "Audio CD created successfully" if not self._missing else "Simulated audio CD complete")
            elif self.job.job_type == JobType.VIDEO_DVD:
                self.backend.burn_video_dvd(self.job.files, self.job.device, o.temp_dir, o.speed, self.sig_status.emit,
                                            self.sig_progress.emit, self.sig_log.emit, auto_blank=o.auto_blank, eject_after=o.eject_after)
                self.sig_finished.emit(True, "Video DVD created successfully" if not self._missing else "Simulated video DVD complete")
            elif self.job.job_type == JobType.VIDEO_BD:
                self.backend.burn_video_bd(self.job.files, self.job.device, o.temp_dir, o.speed, self.sig_status.emit,
                                           self.sig_progress.emit, self.sig_log.emit, auto_blank=o.auto_blank, eject_after=o.eject_after)
                self.sig_finished.emit(True, "Blu-ray created successfully" if not self._missing else "Simulated Blu-ray complete")
            elif self.job.job_type == JobType.RIP:
                out_dir = o.output_dir or Path.home() / "Music"
                out_dir.mkdir(parents=True, exist_ok=True)
                self.backend.rip_cd(self.job.device, out_dir, o.rip_format, o.rip_bitrate,
                                    self.sig_status.emit, self.sig_progress.emit, self.sig_log.emit,
                                    track_titles=o.track_titles)
                self.sig_finished.emit(True, f"CD ripped to {out_dir}" if not self._missing else f"Simulated rip to {out_dir}")
        except Exception as e:
            self.sig_finished.emit(False, str(e))

    def cancel(self):
        self.backend.cancel()
