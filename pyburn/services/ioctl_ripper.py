from __future__ import annotations
import struct
import subprocess
import wave
from pathlib import Path
from typing import Callable, List, Optional

# Windows-native audio CD ripper. Reads CDDA sectors straight off the drive
# through DeviceIoControl, no cdparanoia needed. Encoding to MP3/FLAC is done by
# ffmpeg (native Windows build) when present; otherwise tracks are left as WAV.
#
# This is the reverse direction from burning: IMAPI2 writes, this reads. The
# read path is Win32 CreateFile on \\.\<drive> plus two control codes:
#   IOCTL_CDROM_READ_TOC_EX  - get track boundaries
#   IOCTL_CDROM_RAW_READ     - pull raw 2352-byte CDDA sectors
#
# Windows-only. On Linux the app uses cdparanoia through the CLI backend.

OnStatus = Callable[[str], None]
OnProgress = Callable[[int], None]
OnLog = Callable[[str], None]

# Control codes (from ntddcdrm.h). Computed with CTL_CODE macro semantics.
IOCTL_CDROM_READ_TOC = 0x00024000
IOCTL_CDROM_RAW_READ = 0x0002403E
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
OPEN_EXISTING = 3
GENERIC_READ = 0x80000000
RAW_SECTOR_SIZE = 2352            # CDDA raw sector
SECTORS_PER_READ = 20             # batch reads for throughput


def _require_windows():
    import platform
    if platform.system().lower() != "windows":
        raise RuntimeError("IOCTL ripper is only available on Windows")


class IOCTLRipper:
    def __init__(self):
        _require_windows()
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def _open_drive(self, device: str):
        import ctypes
        from ctypes import wintypes
        letter = (device or "").rstrip("\\/").upper()
        if len(letter) >= 2 and letter[1] == ":":
            path = f"\\\\.\\{letter[0]}:"
        else:
            path = f"\\\\.\\{letter}"
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.CreateFileW(
            ctypes.c_wchar_p(path),
            wintypes.DWORD(GENERIC_READ),
            wintypes.DWORD(FILE_SHARE_READ | FILE_SHARE_WRITE),
            None,
            wintypes.DWORD(OPEN_EXISTING),
            wintypes.DWORD(0),
            None,
        )
        if handle == wintypes.HANDLE(-1).value or handle == -1:
            raise OSError(f"Could not open drive {path}")
        return handle

    def _read_toc(self, handle) -> List[tuple]:
        """Return list of (track_number, start_lba, end_lba) for audio tracks."""
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.windll.kernel32
        # CDROM_TOC: 4 header bytes + up to 100 TRACK_DATA entries of 8 bytes.
        buf = ctypes.create_string_buffer(4 + 100 * 8)
        returned = wintypes.DWORD(0)
        ok = kernel32.DeviceIoControl(
            handle, wintypes.DWORD(IOCTL_CDROM_READ_TOC),
            None, 0, buf, len(buf), ctypes.byref(returned), None,
        )
        if not ok:
            raise OSError("IOCTL_CDROM_READ_TOC failed")
        first = buf.raw[2]
        last = buf.raw[3]
        entries = []
        # Each TRACK_DATA: Reserved, Control/Adr, TrackNumber, Reserved1, Address[4]
        def lba_at(idx):
            off = 4 + idx * 8
            addr = buf.raw[off + 4:off + 8]
            # MSF-style absolute address; last byte is frames, standard 2-sec offset.
            m, s, f = addr[1], addr[2], addr[3]
            return (m * 60 + s) * 75 + f - 150

        tracks = []
        n = last - first + 1
        for i in range(n):
            off = 4 + i * 8
            control = buf.raw[off + 1] >> 4
            track_no = buf.raw[off + 2]
            is_audio = (buf.raw[off + 1] & 0x04) == 0  # data bit clear => audio
            start = lba_at(i)
            end = lba_at(i + 1)  # next track start; leadout for last
            if is_audio:
                tracks.append((track_no, start, end))
        return tracks

    def _read_track(self, handle, start_lba: int, end_lba: int, out_wav: Path,
                    on_progress: OnProgress) -> None:
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.windll.kernel32
        total = max(1, end_lba - start_lba)
        # RAW_READ_INFO: LARGE_INTEGER DiskOffset, ULONG SectorCount, TRACK_MODE_TYPE TrackMode
        with wave.open(str(out_wav), "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(44100)
            lba = start_lba
            done = 0
            outbuf = ctypes.create_string_buffer(RAW_SECTOR_SIZE * SECTORS_PER_READ)
            returned = wintypes.DWORD(0)
            while lba < end_lba:
                if self._cancelled:
                    raise RuntimeError("Rip cancelled")
                count = min(SECTORS_PER_READ, end_lba - lba)
                # Build RAW_READ_INFO
                info = struct.pack("<qII", lba, count, 2)  # 2 = CDDA
                ok = kernel32.DeviceIoControl(
                    handle, wintypes.DWORD(IOCTL_CDROM_RAW_READ),
                    info, len(info), outbuf, RAW_SECTOR_SIZE * count,
                    ctypes.byref(returned), None,
                )
                if not ok:
                    raise OSError(f"IOCTL_CDROM_RAW_READ failed at lba {lba}")
                w.writeframes(outbuf.raw[:RAW_SECTOR_SIZE * count])
                lba += count
                done += count
                on_progress(max(0, min(100, int((done / total) * 100))))

    def rip(self, device: str, out_dir: Path, fmt: str, bitrate: int,
            on_status: OnStatus, on_progress: OnProgress, on_log: OnLog,
            track_titles: Optional[List[str]] = None,
            ffmpeg_path: Optional[str] = None) -> None:
        out_dir.mkdir(parents=True, exist_ok=True)
        handle = self._open_drive(device)
        try:
            import ctypes
            on_status("Reading table of contents (Windows IOCTL)...")
            tracks = self._read_toc(handle)
            if not tracks:
                raise RuntimeError("No audio tracks found on disc")
            n = len(tracks)
            for idx, (track_no, start, end) in enumerate(tracks, start=1):
                if self._cancelled:
                    raise RuntimeError("Rip cancelled")
                on_status(f"Ripping track {idx}/{n} (IOCTL)...")
                title = (track_titles[idx - 1] if track_titles and idx - 1 < len(track_titles) else f"Track {idx}")
                base = f"{idx:02d} - {title}"
                wav_path = out_dir / f"{base}.wav"

                def scaled(p, i=idx, total=n):
                    on_progress(int(((i - 1) / total) * 100 + (p / total)))

                self._read_track(handle, start, end, wav_path, scaled)
                # Encode if ffmpeg available and a compressed format was requested.
                fmtu = (fmt or "WAV").upper()
                if fmtu in ("MP3", "FLAC") and ffmpeg_path:
                    ext = "mp3" if fmtu == "MP3" else "flac"
                    target = out_dir / f"{base}.{ext}"
                    args = [ffmpeg_path, "-y", "-i", str(wav_path)]
                    if fmtu == "MP3":
                        args += ["-b:a", f"{bitrate}k"]
                    args += [str(target)]
                    try:
                        subprocess.run(args, capture_output=True, text=True, check=True)
                        wav_path.unlink(missing_ok=True)
                    except Exception as e:
                        on_log(f"ffmpeg encode failed for {base}, keeping WAV: {e}")
                elif fmtu in ("MP3", "FLAC") and not ffmpeg_path:
                    on_log(f"No ffmpeg found; leaving {base} as WAV.")
                on_progress(int((idx / n) * 100))
            on_status(f"Ripped {n} tracks to {out_dir}")
            on_progress(100)
        finally:
            try:
                import ctypes
                ctypes.windll.kernel32.CloseHandle(handle)
            except Exception:
                pass
