from __future__ import annotations
import os
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Callable, List, Optional
from .exec import ProcessRunner
from ..core.tools import ToolFinder
from .progress import ProgressTools
from .media import MediaTools
from .verify import VerificationTools
OnStatus = Callable[[str], None]
OnProgress = Callable[[int], None]
OnLog = Callable[[str], None]
class Phase:
    def __init__(self, on_progress: OnProgress, start: int, span: int):
        self.on_progress = on_progress
        self.start = start
        self.span = span
    def emit(self, pct: int):
        pct = max(0, min(100, pct))
        overall = self.start + int(self.span * (pct / 100.0))
        self.on_progress(min(99, overall))
class BackendBase:
    def __init__(self, tools: ToolFinder):
        self.tools = tools
        self.runner = ProcessRunner()
        self.media = MediaTools(tools, self.runner)
        self.verify = VerificationTools(tools, self.runner)
        self._cancelled = False
    def cancel(self):
        self._cancelled = True
        self.runner.cancel()
    def _file_total_size(self, paths: List[Path]) -> int:
        total = 0
        for p in paths:
            if p.is_file():
                try: total += p.stat().st_size
                except Exception: pass
            elif p.is_dir():
                for root, _, files in os.walk(p, followlinks=False):
                    for fn in files:
                        fp = Path(root) / fn
                        try: total += fp.stat().st_size
                        except Exception: pass
        return total
class SimulatedBackend(BackendBase):
    def burn_data(self, files: List[Path], device: str, temp_dir: Path, volume: str, speed: any,
                  verify: bool, on_status: OnStatus, on_progress: OnProgress, on_log: OnLog,
                  auto_blank: bool = True, eject_after: bool = True, dummy: bool = False):
        on_status("Creating ISO image (simulated)...")
        for i in range(40):
            if self.runner.cancelled: raise RuntimeError("cancelled")
            import time; time.sleep(0.02); on_progress(i)
        on_status("Burning (simulated)...")
        for i in range(50):
            if self.runner.cancelled: raise RuntimeError("cancelled")
            import time; time.sleep(0.03); on_progress(40 + i)
        if verify:
            on_status("Verifying (simulated)...")
            for i in range(10): import time; time.sleep(0.02); on_progress(90 + i)
        if eject_after: on_status("Ejecting (simulated)...")
        on_progress(100); on_status("Data disc burned (simulated)")
    def burn_audio(self, files: List[Path], device: str, temp_dir: Path, speed: any,
                   on_status: OnStatus, on_progress: OnProgress, on_log: OnLog, eject_after: bool = True,
                   album_title: Optional[str] = None, album_performer: Optional[str] = None,
                   track_titles: Optional[List[str]] = None, track_performers: Optional[List[str]] = None):
        on_status("Converting audio (simulated)...")
        n = max(1, len(files))
        for idx in range(1, n + 1):
            if self.runner.cancelled: raise RuntimeError("cancelled")
            import time; time.sleep(0.05); on_progress(10 + int((idx / n) * 40))
        on_status("Burning (simulated)...")
        for i in range(50): import time; time.sleep(0.03); on_progress(50 + i)
        if eject_after: on_status("Ejecting (simulated)...")
        on_progress(100); on_status("Audio CD created (simulated)")
    def burn_video_dvd(self, files: List[Path], device: str, temp_dir: Path, speed: any,
                       on_status: OnStatus, on_progress: OnProgress, on_log: OnLog,
                       auto_blank: bool = True, eject_after: bool = True):
        on_status("Transcoding video (simulated)...")
        n = max(1, len(files))
        for idx in range(1, n + 1):
            for i in range(10):
                if self.runner.cancelled: raise RuntimeError("cancelled")
                import time; time.sleep(0.04); on_progress(min(60, 10 + int((idx - 1 + i / 10) / n * 50)))
        on_status("Authoring DVD (simulated)..."); on_progress(70); import time; time.sleep(0.4)
        on_status("Burning DVD (simulated)...")
        for i in range(30): time.sleep(0.05); on_progress(70 + i)
        if eject_after: on_status("Ejecting (simulated)...")
        on_progress(100); on_status("Video DVD created (simulated)")
    def burn_video_bd(self, files: List[Path], device: str, temp_dir: Path, speed: any,
                      on_status: OnStatus, on_progress: OnProgress, on_log: OnLog,
                      auto_blank: bool = True, eject_after: bool = True):
        on_status("Transcoding for BDMV (simulated)...")
        n = max(1, len(files))
        for idx in range(1, n + 1):
            for i in range(10): import time; time.sleep(0.05); on_progress(min(60, 10 + int((idx - 1 + i / 10) / n * 50)))
        on_status("Authoring BDMV (simulated)..."); on_progress(70); import time; time.sleep(0.4)
        on_status("Burning Blu-ray (simulated)...")
        for i in range(30): time.sleep(0.05); on_progress(70 + i)
        if eject_after: on_status("Ejecting (simulated)...")
        on_progress(100); on_status("Blu-ray created (simulated)")
    def rip_cd(self, device: str, out_dir: Path, fmt: str, bitrate: int,
               on_status: OnStatus, on_progress: OnProgress, on_log: OnLog,
               track_titles: Optional[List[str]] = None):
        on_status("Detecting tracks (simulated)...")
        import time; time.sleep(0.2)
        tracks = 10
        for t in range(1, tracks + 1):
            on_status(f"Ripping track {t}/{tracks} (simulated)...")
            time.sleep(0.06)
            if fmt != "WAV": time.sleep(0.04)
            on_progress(int(5 + (t / tracks) * 95))
        on_progress(100); on_status(f"Ripped {tracks} tracks to {out_dir} (simulated)")
class RealBackend(BackendBase):
    def burn_data(self, files: List[Path], device: str, temp_dir: Path, volume: str, speed: any,
                  verify: bool, on_status: OnStatus, on_progress: OnProgress, on_log: OnLog,
                  auto_blank: bool = True, eject_after: bool = True, dummy: bool = False):
        mkisofs = self.tools.require("mkisofs")
        iso_path = temp_dir / "pyburn_data.iso"
        verify_iso = temp_dir / "pyburn_verify.iso"  # potential readback
        speed_val = self.media.resolve_speed(speed, device)
        info = self.media.get_info(device)
        try:
            if auto_blank and info.get("rewritable") and info.get("blank") is False:
                on_status("Blanking rewritable media...")
                self.media.blank_media(device)
            # Phase 1: ISO
            total_in = self._file_total_size(files)
            phase1 = Phase(on_progress, 0, 45)
            phase1.emit(0)
            on_status("Creating ISO image...")
            mon = threading.Thread(target=self.verify._monitor_file_growth, args=(iso_path, max(1, total_in), phase1.emit), daemon=True)
            mon.start()
            self.runner.run_stream([mkisofs, "-o", str(iso_path), "-J", "-R", "-V", volume] + [str(p) for p in files],
                                   on_stdout=on_log, on_stderr=on_log, check=True)
            phase1.emit(100)
            # Phase 2: Burn
            phase2 = Phase(on_progress, 45, 50)
            on_status("Burning ISO to disc...")
            grow = self.tools.find("growisofs")
            if grow:
                self.runner.run_stream([grow, "-dvd-compat", "-Z", f"{device}={iso_path}", f"-speed={speed_val}"],
                                       on_stdout=lambda s: (on_log(s), phase2.emit(ProgressTools.parse_growisofs(s) or 0)),
                                       on_stderr=on_log, check=True)
            else:
                rec = self.tools.require("cdrecord")
                cmd = [rec, f"dev={device}", f"speed={speed_val}", "-v", "-dao"]
                if dummy: cmd.append("-dummy")
                cmd.append(str(iso_path))
                self.runner.run_stream(cmd, on_stdout=lambda s: (on_log(s), phase2.emit(ProgressTools.parse_cdrecord(s) or 0)),
                                       on_stderr=lambda s: (on_log(s), phase2.emit(ProgressTools.parse_cdrecord(s) or 0)), check=True)
            phase2.emit(100)
            # Verification
            ok = True
            if verify:
                on_status("Verifying disc...")
                ok = self.verify.verify(iso_path, device, temp_dir, on_status, on_log, Phase(on_progress, 95, 5).emit)
            on_progress(100)
            if not ok:
                raise RuntimeError("Data disc verification failed.")
            on_status("Data disc burned successfully")
        finally:
            try: iso_path.unlink(missing_ok=True)
            except Exception: pass
            try: verify_iso.unlink(missing_ok=True)
            except Exception: pass
            if eject_after:
                self.media.eject(device)
    def _write_cdtext_toc(self, temp_audio: Path, n: int,
                          album_title: Optional[str], album_performer: Optional[str],
                          track_titles: Optional[List[str]], track_performers: Optional[List[str]]) -> Path:
        toc = temp_audio / "cd.toc"
        with open(toc, "w", encoding="utf-8") as f:
            f.write("CD_DA\n\n")
            if album_title or album_performer:
                f.write("CD_TEXT {\n")
                if album_title: f.write(f'  LANGUAGE 0 {{"TITLE"="{album_title}"}}\n')
                if album_performer: f.write(f'  LANGUAGE 0 {{"PERFORMER"="{album_performer}"}}\n')
                f.write("}\n\n")
            for i in range(1, n + 1):
                f.write("TRACK AUDIO\n")
                if track_titles or track_performers:
                    f.write("CD_TEXT {\n")
                    title = (track_titles[i-1] if track_titles and i-1 < len(track_titles) else f"Track {i}")
                    performer = (track_performers[i-1] if track_performers and i-1 < len(track_performers) else (album_performer or ""))
                    f.write(f'  LANGUAGE 0 {{"TITLE"="{title}"}}\n')
                    if performer:
                        f.write(f'  LANGUAGE 0 {{"PERFORMER"="{performer}"}}\n')
                    f.write("}\n")
                f.write(f'FILE "track_{i:02d}.wav" 0\n\n')
        return toc
    def burn_audio(self, files: List[Path], device: str, temp_dir: Path, speed: any,
                   on_status: OnStatus, on_progress: OnProgress, on_log: OnLog, eject_after: bool = True,
                   album_title: Optional[str] = None, album_performer: Optional[str] = None,
                   track_titles: Optional[List[str]] = None, track_performers: Optional[List[str]] = None):
        ffmpeg = self.tools.require("ffmpeg")
        cdrdao = self.tools.require("cdrdao")
        speed_val = self.media.resolve_speed(speed, device)
        temp_audio = temp_dir / "audio_cd"
        shutil.rmtree(temp_audio, ignore_errors=True)
        temp_audio.mkdir(exist_ok=True)
        try:
            n = max(1, len(files))
            for idx, src in enumerate(files, start=1):
                on_status(f"Converting track {idx}/{n}...")
                wav = temp_audio / f"track_{idx:02d}.wav"
                self.runner.run_stream([ffmpeg, "-y", "-i", str(src), "-ar", "44100", "-ac", "2", "-sample_fmt", "s16", str(wav)],
                                       on_stdout=on_log, on_stderr=on_log, check=True)
                on_progress(5 + int((idx / n) * 35))
            toc = self._write_cdtext_toc(temp_audio, n, album_title, album_performer, track_titles, track_performers)
            on_status("Burning audio CD...")
            phase = Phase(on_progress, 40, 60)
            self.runner.run_stream([cdrdao, "write", "--device", device, "--speed", str(speed_val), toc.name],
                                   cwd=str(temp_audio),
                                   on_stdout=lambda s: (on_log(s), phase.emit(70)),
                                   on_stderr=lambda s: (on_log(s), phase.emit(90)), check=True)
            phase.emit(100)
            on_progress(100); on_status("Audio CD created successfully")
        finally:
            try: shutil.rmtree(temp_audio, ignore_errors=True)
            except Exception: pass
            if eject_after:
                self.media.eject(device)
    def burn_video_dvd(self, files: List[Path], device: str, temp_dir: Path, speed: any,
                       on_status: OnStatus, on_progress: OnProgress, on_log: OnLog,
                       auto_blank: bool = True, eject_after: bool = True):
        ffmpeg = self.tools.require("ffmpeg")
        dvdauthor = self.tools.require("dvdauthor")
        mkisofs = self.tools.require("mkisofs")
        grow = self.tools.find("growisofs")
        speed_val = self.media.resolve_speed(speed, device)
        info = self.media.get_info(device)
        dvd_temp = temp_dir / "dvd_temp"
        shutil.rmtree(dvd_temp, ignore_errors=True)
        dvd_temp.mkdir(exist_ok=True)
        try:
            if auto_blank and info.get("rewritable") and info.get("blank") is False:
                on_status("Blanking rewritable media..."); self.media.blank_media(device)
            mpegs: List[Path] = []
            n = max(1, len(files))
            for idx, src in enumerate(files, start=1):
                on_status(f"Transcoding video {idx}/{n}...")
                mpg = dvd_temp / f"title_{idx:02d}.mpg"
                self.runner.run_stream([ffmpeg, "-y", "-i", str(src), "-target", "pal-dvd", "-aspect", "16:9", str(mpg)],
                                       on_stdout=on_log, on_stderr=on_log, check=True)
                mpegs.append(mpg)
                on_progress(10 + int((idx / n) * 50))
            on_status("Authoring DVD structure...")
            xml = dvd_temp / "author.xml"
            with open(xml, "w", encoding="utf-8") as f:
                f.write("<dvdauthor>\n  <vmgm />\n  <titleset>\n    <titles>\n      <pgc>\n")
                for m in mpegs:
                    f.write(f'        <vob file="{m}" />\n')
                f.write("      </pgc>\n    </titles>\n  </titleset>\n</dvdauthor>\n")
            dvd_dir = dvd_temp / "DVD_ROOT"
            self.runner.run_stream([dvdauthor, "-o", str(dvd_dir), "-x", str(xml)], on_stdout=on_log, on_stderr=on_log, check=True)
            on_progress(70)
            on_status("Creating ISO...")
            iso = dvd_temp / "dvd.iso"
            self.runner.run_stream([mkisofs, "-dvd-video", "-o", str(iso), str(dvd_dir)], on_stdout=on_log, on_stderr=on_log, check=True)
            on_progress(85)
            on_status("Burning DVD...")
            phase = Phase(on_progress, 85, 15)
            if grow:
                self.runner.run_stream([grow, "-dvd-compat", "-Z", f"{device}={iso}", f"-speed={speed_val}"],
                                       on_stdout=lambda s: (on_log(s), phase.emit(ProgressTools.parse_growisofs(s) or 0)),
                                       on_stderr=on_log, check=True)
            else:
                rec = self.tools.require("cdrecord")
                self.runner.run_stream([rec, f"dev={device}", f"speed={speed_val}", "-v", "-dao", str(iso)],
                                       on_stdout=lambda s: (on_log(s), phase.emit(ProgressTools.parse_cdrecord(s) or 0)),
                                       on_stderr=lambda s: (on_log(s), phase.emit(ProgressTools.parse_cdrecord(s) or 0)), check=True)
            phase.emit(100)
            on_progress(100); on_status("Video DVD created successfully")
        finally:
            try: shutil.rmtree(dvd_temp, ignore_errors=True)
            except Exception: pass
            if eject_after:
                self.media.eject(device)
    def burn_video_bd(self, files: List[Path], device: str, temp_dir: Path, speed: any,
                      on_status: OnStatus, on_progress: OnProgress, on_log: OnLog,
                      auto_blank: bool = True, eject_after: bool = True):
        ffmpeg = self.tools.require("ffmpeg")
        tsmuxer = self.tools.find("tsMuxeR")
        mkisofs = self.tools.find("mkisofs") or self.tools.find("xorriso")
        grow = self.tools.find("growisofs")
        speed_val = self.media.resolve_speed(speed, device)
        info = self.media.get_info(device)
        bd_temp = temp_dir / "bd_temp"
        shutil.rmtree(bd_temp, ignore_errors=True)
        bd_temp.mkdir(exist_ok=True)
        try:
            if auto_blank and info.get("rewritable") and info.get("blank") is False:
                on_status("Blanking rewritable media..."); self.media.blank_media(device)
            ts_files: List[Path] = []
            n = max(1, len(files))
            for idx, src in enumerate(files, start=1):
                on_status(f"Transcoding video {idx}/{n} for BDMV...")
                ts = bd_temp / f"clip_{idx:02d}.ts"
                self.runner.run_stream([ffmpeg, "-y", "-i", str(src), "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                                        "-c:a", "ac3", "-b:a", "192k", "-pix_fmt", "yuv420p", "-f", "mpegts", str(ts)],
                                       on_stdout=on_log, on_stderr=on_log, check=True)
                ts_files.append(ts)
                on_progress(10 + int((idx / n) * 50))
            if not tsmuxer:
                raise RuntimeError("tsMuxeR not found; cannot author BDMV")
            on_status("Authoring BDMV with tsMuxeR...")
            meta = bd_temp / "meta.bd"
            with open(meta, "w", encoding="utf-8") as f:
                f.write("MUXOPT --no-pcr-on-video-pid --new-audio-pes --blu-ray --vbr --auto-chapters=10\n")
                for ts in ts_files:
                    f.write(f"V_MPEG4/ISO/AVC, {ts}, fps=25, insertSEI, contSPS\n")
                    f.write(f"A_AC3, {ts}, track=2\n")
            bdmv_dir = bd_temp / "BDMV_OUT"
            self.runner.run_stream([tsmuxer, str(meta), str(bdmv_dir)], on_stdout=on_log, on_stderr=on_log, check=True)
            on_progress(70)
            on_status("Creating ISO...")
            iso = bd_temp / "bd.iso"
            if mkisofs and "xorriso" not in mkisofs:
                self.runner.run_stream([mkisofs, "-udf", "-o", str(iso), str(bdmv_dir)], on_stdout=on_log, on_stderr=on_log, check=True)
            else:
                x = self.tools.require("xorriso")
                self.runner.run_stream([x, "-outdev", str(iso), "-blank", "as_needed", "-map", str(bdmv_dir), "/"], on_stdout=on_log, on_stderr=on_log, check=True)
            on_progress(85)
            on_status("Burning Blu-ray...")
            if grow:
                phase = Phase(on_progress, 85, 15)
                self.runner.run_stream([grow, "-speed="+str(speed_val), "-Z", f"{device}={iso}"],
                                       on_stdout=lambda s: (on_log(s), phase.emit(ProgressTools.parse_growisofs(s) or 0)),
                                       on_stderr=on_log, check=True)
                phase.emit(100)
            else:
                rec = self.tools.require("cdrecord")
                self.runner.run_stream([rec, f"dev={device}", f"speed={speed_val}", "-v", "-dao", str(iso)],
                                       on_stdout=lambda s: (on_log(s), None),
                                       on_stderr=lambda s: (on_log(s), None), check=True)
                on_progress(100)
            on_status("Blu-ray created successfully")
        finally:
            try: shutil.rmtree(bd_temp, ignore_errors=True)
            except Exception: pass
            if eject_after:
                self.media.eject(device)
    def rip_cd(self, device: str, out_dir: Path, fmt: str, bitrate: int,
               on_status: OnStatus, on_progress: OnProgress, on_log: OnLog,
               track_titles: Optional[List[str]] = None):
        cdparanoia = self.tools.require("cdparanoia")
        p = subprocess.run([cdparanoia, "-Q", "-d", device], capture_output=True, text=True)
        import re
        lines = (p.stdout or "") + "\n" + (p.stderr or "")
        tracks = max(1, len(re.findall(r"^\s*\d+\.\s+\d+:\d{2}\.\d{2}", lines, re.MULTILINE)))
        on_progress(5)
        for t in range(1, tracks + 1):
            on_status(f"Ripping track {t}/{tracks}...")
            wav = out_dir / f"track_{t:02d}.wav"
            phase = Phase(on_progress, 5 + int((t - 1) * (90 / tracks)), int(40 / tracks))
            self.runner.run_stream([cdparanoia, "-d", device, str(t), str(wav)],
                                   on_stdout=lambda s: (on_log(s), None),
                                   on_stderr=lambda s: (on_log(s), phase.emit(ProgressTools.parse_cdparanoia(s) or 0)), check=True)
            out_name = f"{t:02d} - {track_titles[t-1] if track_titles and t-1 < len(track_titles) else f'Track {t}'}"
            fmtu = fmt.upper()
            if fmtu == "MP3":
                lame = self.tools.require("lame")
                self.runner.run_stream([lame, "-b", str(bitrate), str(wav), str(out_dir / f"{out_name}.mp3")],
                                       on_stdout=on_log, on_stderr=on_log, check=True)
                try: wav.unlink()
                except Exception: pass
            elif fmtu == "FLAC":
                flac = self.tools.require("flac")
                self.runner.run_stream([flac, "-8", str(wav), "-o", str(out_dir / f"{out_name}.flac")],
                                       on_stdout=on_log, on_stderr=on_log, check=True)
                try: wav.unlink()
                except Exception: pass
            else:
                wav.rename(out_dir / f"{out_name}.wav")
            on_progress(int(5 + (t / tracks) * 95))
        on_status(f"Ripped {tracks} tracks to {out_dir}")
        on_progress(100)
