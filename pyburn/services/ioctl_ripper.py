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
        """Return list of (track_number, start_lba, end_lba) for audio tracks.

        The Windows TOC lays out entries as track 1..N followed by a LEADOUT
        entry (track number 0xAA) whose address is the end of the last track. A
        track's end is the START of the following entry: for tracks 1..N-1 that
        is the next track, and for the LAST track it is the leadout entry. The
        last track is the only one whose end comes from the leadout, which is why
        a wrong/missing leadout address makes only the last track read forever.
        """
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
            err = kernel32.GetLastError()
            # ERROR_NOT_READY (21) and ERROR_NO_MEDIA_IN_DRIVE (1112) mean the
            # drive is empty. Give the user a plain "no disc" message instead of a
            # raw IOCTL error.
            if err in (21, 1112):
                raise RuntimeError("No disc in the drive. Insert an audio CD and try again.")
            raise OSError(f"Could not read the disc (IOCTL_CDROM_READ_TOC failed, error {err}). "
                          f"Make sure an audio CD is inserted and readable.")
        first = buf.raw[2]
        last = buf.raw[3]

        def lba_at(idx):
            off = 4 + idx * 8
            addr = buf.raw[off + 4:off + 8]
            m, s, f = addr[1], addr[2], addr[3]
            return (m * 60 + s) * 75 + f - 150

        def track_no_at(idx):
            return buf.raw[4 + idx * 8 + 2]

        n = last - first + 1
        # Locate the leadout entry (track number 0xAA). It should be right after
        # the last track, but scan for it so we do not depend on exact placement.
        leadout_lba = None
        for j in range(n, n + 3):  # leadout is normally at index n
            try:
                if track_no_at(j) == 0xAA:
                    leadout_lba = lba_at(j)
                    break
            except Exception:
                break
        if leadout_lba is None:
            # Fall back to the entry immediately after the last track.
            leadout_lba = lba_at(n)

        tracks = []
        for i in range(n):
            off = 4 + i * 8
            track_no = buf.raw[off + 2]
            is_audio = (buf.raw[off + 1] & 0x04) == 0  # data bit clear => audio
            start = lba_at(i)
            if i < n - 1:
                end = lba_at(i + 1)          # next track's start
            else:
                end = leadout_lba            # last track ends at the leadout
            # Validate: a track's end must be greater than its start and within a
            # sane bound (a CD holds at most ~360000 sectors, ~80 min). If the
            # value is bad, skip this track rather than read forever.
            if end <= start or (end - start) > 360000:
                continue
            if is_audio:
                tracks.append((track_no, start, end))
        return tracks

    def _read_track(self, handle, start_lba: int, end_lba: int, out_wav: Path,
                    on_progress: OnProgress, is_last: bool = False) -> None:
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.windll.kernel32
        # Many drives cannot cleanly read the final couple of sectors of the last
        # audio track (the transition into the lead-out), and IOCTL_CDROM_RAW_READ
        # can block or fail there. Pull the end of the LAST track back by a few
        # sectors so we never ask the drive for the problem region. The lost
        # audio is a few milliseconds of the run-out, inaudible.
        if is_last:
            end_lba = max(start_lba, end_lba - 4)
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
                # RAW_READ_INFO.DiskOffset is a BYTE offset expressed in logical
                # 2048-byte units: DiskOffset = LBA * 2048. It is NOT the raw LBA
                # and NOT LBA * 2352. Passing the bare LBA made every read start
                # near byte 0, so tracks came out the correct length but silent.
                disk_offset = lba * 2048
                info = struct.pack("<qII", disk_offset, count, 2)  # 2 = CDDA
                ok = kernel32.DeviceIoControl(
                    handle, wintypes.DWORD(IOCTL_CDROM_RAW_READ),
                    info, len(info), outbuf, RAW_SECTOR_SIZE * count,
                    ctypes.byref(returned), None,
                )
                if not ok:
                    # Near the end of a track a failed read is the drive hitting
                    # the run-out. Rather than hang or abort the whole rip, stop
                    # this track cleanly; we already have essentially all of it.
                    remaining = end_lba - lba
                    if remaining <= SECTORS_PER_READ * 2:
                        break
                    raise OSError(f"IOCTL_CDROM_RAW_READ failed at lba {lba}")
                got = int(returned.value)
                if got <= 0:
                    # No data returned: treat like the run-out case above.
                    remaining = end_lba - lba
                    if remaining <= SECTORS_PER_READ * 2:
                        break
                    raise OSError(f"IOCTL_CDROM_RAW_READ returned no data at lba {lba}")
                # Write only what the drive actually returned.
                w.writeframes(outbuf.raw[:min(got, RAW_SECTOR_SIZE * count)])
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

                self._read_track(handle, start, end, wav_path, scaled, is_last=(idx == n))
                # Encode if ffmpeg available and a compressed format was requested.
                fmtu = (fmt or "WAV").upper()
                if fmtu in ("MP3", "FLAC") and ffmpeg_path:
                    ext = "mp3" if fmtu == "MP3" else "flac"
                    target = out_dir / f"{base}.{ext}"
                    # -nostdin is essential: in a windowless (frozen) Windows
                    # subprocess ffmpeg can block forever trying to read stdin,
                    # which is exactly the "stalls on the last track after the WAV
                    # is written" hang (the CD read is already done; only the
                    # WAV->MP3 encode is left). Name the encoder explicitly too so
                    # ffmpeg does not have to infer it.
                    args = [ffmpeg_path, "-nostdin", "-y", "-i", str(wav_path)]
                    if fmtu == "MP3":
                        args += ["-c:a", "libmp3lame", "-b:a", f"{bitrate}k"]
                    else:
                        args += ["-c:a", "flac"]
                    args += [str(target)]
                    try:
                        # No console window on Windows; redirect stdin from
                        # DEVNULL so ffmpeg never waits on it; timeout so a stuck
                        # encoder can never hang the whole rip.
                        creo = {}
                        try:
                            creo = {"creationflags": subprocess.CREATE_NO_WINDOW}
                        except Exception:
                            creo = {"creationflags": 0x08000000}
                        proc = subprocess.run(args, capture_output=True, text=True,
                                              stdin=subprocess.DEVNULL, timeout=300, **creo)
                        if proc.returncode != 0 or not target.exists():
                            on_log(f"ffmpeg encode failed for {base} (rc={proc.returncode}); keeping WAV. "
                                   f"stderr tail: {(proc.stderr or '')[-300:]}")
                        else:
                            wav_path.unlink(missing_ok=True)
                    except Exception as e:
                        on_log(f"ffmpeg encode error for {base}, keeping WAV: {e}")
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
