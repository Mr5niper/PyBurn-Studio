from __future__ import annotations
import shutil
import tempfile
from pathlib import Path
from typing import Callable, List, Optional
from .platform_caps import WSLManager
from .imapi2_backend import IMAPI2Backend

# Windows DVD-Video and Blu-ray path. The authoring tools (dvdauthor, tsMuxeR,
# mkisofs/xorriso) have no usable native Windows builds, so this backend runs
# them inside WSL2 to GENERATE an image file, then hands that finished image to
# IMAPI2 to burn to the real drive. This is the split-at-the-file design:
# Linux userland authors, Windows burns the hardware.
#
# Windows-only. On Linux the app authors and burns entirely through the CLI
# backend and never uses this.

OnStatus = Callable[[str], None]
OnProgress = Callable[[int], None]
OnLog = Callable[[str], None]


class WSLAuthorBackend:
    def __init__(self, wsl: Optional[WSLManager] = None):
        self.wsl = wsl or WSLManager()
        if not self.wsl.info.available:
            self.wsl.detect()
        self._cancelled = False
        self._imapi2 = None

    def cancel(self):
        self._cancelled = True
        if self._imapi2:
            self._imapi2.cancel()

    def _work_win_dir(self) -> Path:
        # A Windows-side working directory both sides can reach: WSL2 sees it as
        # /mnt/<drive>/... and IMAPI2 reads the final image from the Windows path.
        d = Path(tempfile.gettempdir()) / "pyburn_wsl_work"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def burn_video_dvd(self, files: List[Path], device: str, on_status: OnStatus,
                       on_progress: OnProgress, on_log: OnLog,
                       auto_blank: bool = True, eject_after: bool = True) -> None:
        work = self._work_win_dir()
        wsl_work = self.wsl.win_to_wsl_path(str(work))
        on_status("Preparing DVD authoring in WSL2...")
        # 1) Transcode each input to DVD-compliant MPEG inside WSL2.
        mpegs_wsl = []
        n = max(1, len(files))
        for idx, src in enumerate(files, start=1):
            if self._cancelled:
                raise RuntimeError("cancelled")
            src_wsl = self.wsl.win_to_wsl_path(str(src))
            mpg_wsl = f"{wsl_work}/title_{idx:02d}.mpg"
            on_status(f"Transcoding video {idx}/{n} (WSL2 ffmpeg)...")
            rc = self.wsl.run(
                ["ffmpeg", "-y", "-i", src_wsl, "-target", "pal-dvd", "-aspect", "16:9", mpg_wsl],
                on_out=on_log, on_err=on_log,
            )
            if rc != 0:
                raise RuntimeError(f"WSL2 ffmpeg failed for {src}")
            mpegs_wsl.append(mpg_wsl)
            on_progress(10 + int((idx / n) * 45))
        # 2) Author VIDEO_TS with dvdauthor inside WSL2.
        on_status("Authoring DVD structure (WSL2 dvdauthor)...")
        xml_wsl = f"{wsl_work}/author.xml"
        dvd_root_wsl = f"{wsl_work}/DVD_ROOT"
        xml_lines = ["<dvdauthor dest=\"" + dvd_root_wsl + "\">", "  <vmgm />", "  <titleset>", "    <titles>", "      <pgc>"]
        for m in mpegs_wsl:
            xml_lines.append(f'        <vob file="{m}" />')
        xml_lines += ["      </pgc>", "    </titles>", "  </titleset>", "</dvdauthor>"]
        # Write the xml from inside WSL2 via a heredoc-style command.
        xml_text = "\n".join(xml_lines)
        rc = self.wsl.run(["bash", "-lc", f"cat > '{xml_wsl}' <<'XEOF'\n{xml_text}\nXEOF"], on_out=on_log, on_err=on_log)
        if rc != 0:
            raise RuntimeError("Failed to write dvdauthor XML in WSL2")
        rc = self.wsl.run(["dvdauthor", "-x", xml_wsl], on_out=on_log, on_err=on_log)
        if rc != 0:
            raise RuntimeError("WSL2 dvdauthor failed")
        on_progress(70)
        # 3) Wrap VIDEO_TS into a DVD-Video ISO with mkisofs inside WSL2.
        on_status("Creating DVD-Video ISO (WSL2 mkisofs)...")
        iso_wsl = f"{wsl_work}/dvd.iso"
        rc = self.wsl.run(["mkisofs", "-dvd-video", "-o", iso_wsl, dvd_root_wsl], on_out=on_log, on_err=on_log)
        if rc != 0:
            raise RuntimeError("WSL2 mkisofs failed")
        on_progress(82)
        # 4) Burn the Windows-visible ISO with IMAPI2.
        iso_win = work / "dvd.iso"
        if not iso_win.exists():
            raise RuntimeError("Authored ISO not visible on the Windows side")
        self._imapi2 = IMAPI2Backend()
        on_status("Burning DVD (IMAPI2)...")
        self._imapi2.burn_image(iso_win, device, on_status, on_progress, on_log, eject_after=eject_after)
        self._cleanup(work)

    def burn_video_bd(self, files: List[Path], device: str, on_status: OnStatus,
                      on_progress: OnProgress, on_log: OnLog,
                      auto_blank: bool = True, eject_after: bool = True) -> None:
        work = self._work_win_dir()
        wsl_work = self.wsl.win_to_wsl_path(str(work))
        on_status("Preparing Blu-ray authoring in WSL2...")
        ts_wsl = []
        n = max(1, len(files))
        for idx, src in enumerate(files, start=1):
            if self._cancelled:
                raise RuntimeError("cancelled")
            src_wsl = self.wsl.win_to_wsl_path(str(src))
            out_ts = f"{wsl_work}/clip_{idx:02d}.ts"
            on_status(f"Transcoding video {idx}/{n} for BDMV (WSL2 ffmpeg)...")
            rc = self.wsl.run(
                ["ffmpeg", "-y", "-i", src_wsl, "-c:v", "libx264", "-preset", "veryfast",
                 "-crf", "20", "-c:a", "ac3", "-b:a", "192k", "-pix_fmt", "yuv420p", "-f", "mpegts", out_ts],
                on_out=on_log, on_err=on_log,
            )
            if rc != 0:
                raise RuntimeError(f"WSL2 ffmpeg failed for {src}")
            ts_wsl.append(out_ts)
            on_progress(10 + int((idx / n) * 45))
        on_status("Authoring BDMV (WSL2 tsMuxeR)...")
        meta_wsl = f"{wsl_work}/meta.bd"
        meta_lines = ["MUXOPT --no-pcr-on-video-pid --new-audio-pes --blu-ray --vbr --auto-chapters=10"]
        for ts in ts_wsl:
            meta_lines.append(f"V_MPEG4/ISO/AVC, {ts}, fps=25, insertSEI, contSPS")
            meta_lines.append(f"A_AC3, {ts}, track=2")
        meta_text = "\n".join(meta_lines)
        bdmv_wsl = f"{wsl_work}/BDMV_OUT"
        rc = self.wsl.run(["bash", "-lc", f"cat > '{meta_wsl}' <<'MEOF'\n{meta_text}\nMEOF"], on_out=on_log, on_err=on_log)
        if rc != 0:
            raise RuntimeError("Failed to write tsMuxeR meta in WSL2")
        rc = self.wsl.run(["bash", "-lc", f"tsMuxeR '{meta_wsl}' '{bdmv_wsl}' || tsmuxer '{meta_wsl}' '{bdmv_wsl}'"], on_out=on_log, on_err=on_log)
        if rc != 0:
            raise RuntimeError("WSL2 tsMuxeR failed")
        on_progress(70)
        on_status("Creating UDF image (WSL2 xorriso/mkisofs)...")
        iso_wsl = f"{wsl_work}/bd.iso"
        # Prefer xorriso for UDF; fall back to mkisofs -udf.
        rc = self.wsl.run(
            ["bash", "-lc",
             f"(xorriso -as mkisofs -udf -o '{iso_wsl}' '{bdmv_wsl}') || (mkisofs -udf -o '{iso_wsl}' '{bdmv_wsl}')"],
            on_out=on_log, on_err=on_log,
        )
        if rc != 0:
            raise RuntimeError("WSL2 image creation failed")
        on_progress(82)
        iso_win = work / "bd.iso"
        if not iso_win.exists():
            raise RuntimeError("Authored BD image not visible on the Windows side")
        self._imapi2 = IMAPI2Backend()
        on_status("Burning Blu-ray (IMAPI2)...")
        self._imapi2.burn_image(iso_win, device, on_status, on_progress, on_log, eject_after=eject_after)
        self._cleanup(work)

    def _cleanup(self, work: Path):
        try:
            shutil.rmtree(work, ignore_errors=True)
        except Exception:
            pass
