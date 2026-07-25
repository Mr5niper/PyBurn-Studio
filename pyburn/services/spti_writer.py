from __future__ import annotations
import ctypes
import struct
import time
from ctypes import wintypes
from pathlib import Path
from typing import Callable, List, Optional

# Windows-native data-CD burner that talks to the drive DIRECTLY through
# IOCTL_SCSI_PASS_THROUGH_DIRECT (SPTI), sending raw MMC commands. It does NOT
# use IMAPI2 or COM at all. This is deliberate: on some USB drives IMAPI2's
# progress-event model corrupts the write (the event callback fires inside the
# synchronous Write() and the drive rejects it). Here WE issue every WRITE(10)
# ourselves, in our own loop, so:
#   - nothing overlaps the write (no COM, no callbacks, no second thread)
#   - progress is EXACT: we know precisely how many sectors we have sent
#
# The MMC command sequence for a blank CD-R in Track-At-Once (TAO) mode follows
# the libburn cookbook (which reads mmc5r03c.pdf), section "Writing a session to
# CD in TAO mode":
#   46h GET CONFIGURATION        -> confirm CD-R / CD-RW current profile
#   51h READ DISC INFORMATION    -> confirm blank/appendable
#   55h MODE SELECT (page 05h)   -> write parameters: TAO, data, 2048-byte blocks
#   52h READ TRACK INFORMATION   -> Next Writable Address (where to start)
#   2Ah WRITE(10) (looped)       -> send the ISO image, 2048-byte sectors
#   35h SYNCHRONIZE CACHE        -> force drive buffer to media (mandatory)
#   5Bh CLOSE TRACK SESSION      -> close/finalize the session
#
# This module builds the ISO image itself is NOT done here; the caller passes a
# path to a finished ISO (built by MsftFileSystemImage or mkisofs). This file is
# purely the "put these bytes on the disc" half.
#
# Windows-only.

OnStatus = Callable[[str], None]
OnProgress = Callable[[int], None]
OnLog = Callable[[str], None]

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
OPEN_EXISTING = 3

# DeviceIoControl codes.
IOCTL_SCSI_PASS_THROUGH_DIRECT = 0x0004D014
IOCTL_STORAGE_MEDIA_REMOVAL = 0x002D4804
FSCTL_LOCK_VOLUME = 0x00090018
FSCTL_UNLOCK_VOLUME = 0x0009001C
FSCTL_DISMOUNT_VOLUME = 0x00090020

SCSI_IOCTL_DATA_OUT = 0
SCSI_IOCTL_DATA_IN = 1
SCSI_IOCTL_DATA_UNSPECIFIED = 2

CD_SECTOR_DATA = 2048        # Mode-1 data block payload
SECTORS_PER_WRITE = 16       # 32 KiB per WRITE(10); a common, safe transfer unit


def _require_windows():
    import platform
    if platform.system().lower() != "windows":
        raise RuntimeError("SPTI writer is only available on Windows")


class _SCSI_PASS_THROUGH_DIRECT(ctypes.Structure):
    _fields_ = [
        ("Length", wintypes.USHORT),
        ("ScsiStatus", ctypes.c_ubyte),
        ("PathId", ctypes.c_ubyte),
        ("TargetId", ctypes.c_ubyte),
        ("Lun", ctypes.c_ubyte),
        ("CdbLength", ctypes.c_ubyte),
        ("SenseInfoLength", ctypes.c_ubyte),
        ("DataIn", ctypes.c_ubyte),
        ("DataTransferLength", wintypes.ULONG),
        ("TimeOutValue", wintypes.ULONG),
        ("DataBuffer", ctypes.c_void_p),
        ("SenseInfoOffset", wintypes.ULONG),
        ("Cdb", ctypes.c_ubyte * 16),
    ]


# The struct we actually pass to DeviceIoControl bundles the pass-through plus a
# sense buffer right after it, and SenseInfoOffset points at that sense buffer.
class _SPTD_WITH_SENSE(ctypes.Structure):
    _fields_ = [
        ("sptd", _SCSI_PASS_THROUGH_DIRECT),
        ("sense", ctypes.c_ubyte * 32),
    ]


class SPTIWriter:
    def __init__(self):
        _require_windows()
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    # -- low-level device + SCSI helpers -------------------------------------
    def _open_drive(self, device: str):
        letter = (device or "").rstrip("\\/").upper()
        if len(letter) >= 2 and letter[1] == ":":
            path = f"\\\\.\\{letter[0]}:"
        else:
            path = f"\\\\.\\{letter}"
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.CreateFileW(
            ctypes.c_wchar_p(path),
            wintypes.DWORD(GENERIC_READ | GENERIC_WRITE),
            wintypes.DWORD(FILE_SHARE_READ | FILE_SHARE_WRITE),
            None,
            wintypes.DWORD(OPEN_EXISTING),
            wintypes.DWORD(0),
            None,
        )
        if handle == wintypes.HANDLE(-1).value or handle == -1:
            raise OSError(f"Could not open drive {path} for writing (need elevation?)")
        return handle

    def _lock_volume(self, handle, on_log: OnLog):
        # Lock + dismount so the filesystem does not fight our raw writes. Both
        # are best-effort; a blank disc has no mounted volume anyway.
        kernel32 = ctypes.windll.kernel32
        returned = wintypes.DWORD(0)
        for code, name in ((FSCTL_LOCK_VOLUME, "lock"),
                           (FSCTL_DISMOUNT_VOLUME, "dismount")):
            try:
                kernel32.DeviceIoControl(handle, wintypes.DWORD(code), None, 0,
                                         None, 0, ctypes.byref(returned), None)
            except Exception as e:
                on_log(f"spti: volume {name} warning: {e}")

    def _unlock_volume(self, handle):
        kernel32 = ctypes.windll.kernel32
        returned = wintypes.DWORD(0)
        try:
            kernel32.DeviceIoControl(handle, wintypes.DWORD(FSCTL_UNLOCK_VOLUME),
                                     None, 0, None, 0, ctypes.byref(returned), None)
        except Exception:
            pass

    def _scsi(self, handle, cdb: bytes, direction: int, data: Optional[bytearray] = None,
              data_len: int = 0, timeout: int = 60):
        """Issue one SCSI/MMC command via SPTI. Returns (ok, sense_bytes, data_buf).

        direction: SCSI_IOCTL_DATA_OUT (write to device),
                   SCSI_IOCTL_DATA_IN (read from device),
                   SCSI_IOCTL_DATA_UNSPECIFIED (no data).
        For DATA_IN, pass data_len; the returned data_buf holds the reply.
        For DATA_OUT, pass data (the bytes to send).
        """
        kernel32 = ctypes.windll.kernel32
        pkt = _SPTD_WITH_SENSE()
        ctypes.memset(ctypes.byref(pkt), 0, ctypes.sizeof(pkt))
        pkt.sptd.Length = ctypes.sizeof(_SCSI_PASS_THROUGH_DIRECT)
        pkt.sptd.CdbLength = len(cdb)
        pkt.sptd.SenseInfoLength = 32
        pkt.sptd.DataIn = direction
        pkt.sptd.TimeOutValue = timeout
        pkt.sptd.SenseInfoOffset = _SPTD_WITH_SENSE.sense.offset
        for i, b in enumerate(cdb):
            pkt.sptd.Cdb[i] = b

        data_buf = None
        if direction == SCSI_IOCTL_DATA_OUT and data is not None:
            data_buf = (ctypes.c_ubyte * len(data)).from_buffer_copy(bytes(data))
            pkt.sptd.DataTransferLength = len(data)
            pkt.sptd.DataBuffer = ctypes.cast(data_buf, ctypes.c_void_p)
        elif direction == SCSI_IOCTL_DATA_IN and data_len > 0:
            data_buf = (ctypes.c_ubyte * data_len)()
            pkt.sptd.DataTransferLength = data_len
            pkt.sptd.DataBuffer = ctypes.cast(data_buf, ctypes.c_void_p)
        else:
            pkt.sptd.DataTransferLength = 0
            pkt.sptd.DataBuffer = None

        returned = wintypes.DWORD(0)
        ok = kernel32.DeviceIoControl(
            handle, wintypes.DWORD(IOCTL_SCSI_PASS_THROUGH_DIRECT),
            ctypes.byref(pkt), ctypes.sizeof(pkt),
            ctypes.byref(pkt), ctypes.sizeof(pkt),
            ctypes.byref(returned), None,
        )
        sense = bytes(bytearray(pkt.sense))
        good = bool(ok) and pkt.sptd.ScsiStatus == 0
        out = bytes(bytearray(data_buf)) if (direction == SCSI_IOCTL_DATA_IN and data_buf is not None) else b""
        return good, sense, out, pkt.sptd.ScsiStatus

    def _sense_str(self, sense: bytes) -> str:
        if not sense or len(sense) < 14:
            return "no sense"
        key = sense[2] & 0x0F
        asc = sense[12]
        ascq = sense[13]
        return f"SenseKey=0x{key:02X} ASC=0x{asc:02X} ASCQ=0x{ascq:02X}"

    # -- MMC command wrappers -------------------------------------------------
    def _get_configuration(self, handle, on_log: OnLog) -> Optional[int]:
        # 46h GET CONFIGURATION, RT=0, returns current profile in bytes 6-7 of
        # the Feature Header.
        cdb = bytes([0x46, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x20, 0x00, 0x00, 0x00])
        ok, sense, data, status = self._scsi(handle, cdb, SCSI_IOCTL_DATA_IN, data_len=32)
        if not ok or len(data) < 8:
            on_log(f"spti: GET CONFIGURATION failed ({self._sense_str(sense)})")
            return None
        profile = (data[6] << 8) | data[7]
        return profile

    def _read_disc_information(self, handle, on_log: OnLog) -> Optional[int]:
        # 51h READ DISC INFORMATION, Data Type 000b. Disc Status is bits 0-1 of
        # byte 2 in the reply.
        cdb = bytes([0x51, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x20, 0x00])
        ok, sense, data, status = self._scsi(handle, cdb, SCSI_IOCTL_DATA_IN, data_len=32)
        if not ok or len(data) < 3:
            on_log(f"spti: READ DISC INFORMATION failed ({self._sense_str(sense)})")
            return None
        disc_status = data[2] & 0x03  # 0=blank 1=appendable 2=finalized 3=other
        return disc_status

    def _read_next_writable_address(self, handle, on_log: OnLog) -> Optional[int]:
        # 52h READ TRACK INFORMATION, Address/Number Type = 01b (track), track
        # number FFh (the invisible/incomplete track). NWA is bytes 12-15 of the
        # reply (big-endian). NWA_V (byte 7 bit 0) must be 1 for it to be valid.
        cdb = bytes([0x52, 0x01, 0x00, 0x00, 0x00, 0xFF, 0x00, 0x00, 0x40, 0x00])
        ok, sense, data, status = self._scsi(handle, cdb, SCSI_IOCTL_DATA_IN, data_len=64)
        if not ok or len(data) < 16:
            on_log(f"spti: READ TRACK INFORMATION failed ({self._sense_str(sense)})")
            return None
        nwa_valid = bool(data[7] & 0x01)
        nwa = struct.unpack(">I", data[12:16])[0]
        if not nwa_valid:
            on_log("spti: Next Writable Address not valid; defaulting to 0")
            return 0
        return nwa

    def _mode_select_write_params(self, handle, on_log: OnLog, dummy: bool = False) -> bool:
        # 55h MODE SELECT (10), sending an 8-byte mode parameter header (all
        # zeros) followed by Write Parameters mode page 05h.
        #
        # Page 05h layout (MMC-5 table 644), the bytes we set:
        #   byte 0: page code 05h (PS=0)
        #   byte 1: page length = 0x32 (50) for the standard length
        #   byte 2: Write Type in bits 0-3 = 01h (TAO);
        #           BUFE (bit 6) = 1 (buffer underrun free ON);
        #           (Test Write bit 4 = dummy)
        #   byte 3: Multi-session (bits 6-7) = 00b finalize;
        #           Copy (bit 4) = 0; Track Mode (bits 0-3) = 4 (data)
        #   byte 4: Data Block Type (bits 0-3) = 8 (2048-byte Mode-1 data)
        #   byte 8: Host Application Code = 0
        #   bytes 10-13: Audio Pause Length = 150 (big-endian) at byte 14-15 per
        #                spec; we set the pause field conservatively.
        page = bytearray(52)
        page[0] = 0x05
        page[1] = 0x32
        write_type = 0x01  # TAO
        b2 = write_type & 0x0F
        b2 |= (1 << 6)     # BUFE on
        if dummy:
            b2 |= (1 << 4)  # Test Write (dummy)
        page[2] = b2
        multisession = 0x00  # finalize
        track_mode = 0x04    # data
        page[3] = ((multisession & 0x03) << 6) | (track_mode & 0x0F)
        page[4] = 0x08       # Data Block Type = 2048-byte Mode-1 data
        # Audio Pause Length lives at bytes 14-15 of the page in MMC-5; 150 = 2s.
        page[14] = (150 >> 8) & 0xFF
        page[15] = 150 & 0xFF

        header = bytearray(8)  # 8-byte mode parameter header, all zeros
        payload = bytes(header) + bytes(page)
        # MODE SELECT (10): 55h, PF bit (byte1 bit4) = 1, param list length in
        # bytes 7-8 (big-endian).
        plen = len(payload)
        cdb = bytes([0x55, 0x10, 0x00, 0x00, 0x00, 0x00, 0x00,
                     (plen >> 8) & 0xFF, plen & 0xFF, 0x00])
        ok, sense, _data, status = self._scsi(handle, cdb, SCSI_IOCTL_DATA_OUT,
                                              data=bytearray(payload))
        if not ok:
            on_log(f"spti: MODE SELECT (write params) failed ({self._sense_str(sense)})")
        return ok

    def _set_cd_speed(self, handle, kbps: int, on_log: OnLog):
        # BBh SET CD SPEED: read speed (bytes 2-3) and write speed (bytes 4-5),
        # big-endian, in kbytes/sec (1000 bytes). 0xFFFF = fastest.
        rd = 0xFFFF
        wr = kbps if kbps and kbps > 0 else 0xFFFF
        cdb = bytes([0xBB, 0x00,
                     (rd >> 8) & 0xFF, rd & 0xFF,
                     (wr >> 8) & 0xFF, wr & 0xFF,
                     0x00, 0x00, 0x00, 0x00, 0x00, 0x00])
        ok, sense, _d, status = self._scsi(handle, cdb, SCSI_IOCTL_DATA_UNSPECIFIED)
        if not ok:
            on_log(f"spti: SET CD SPEED warning ({self._sense_str(sense)}); using drive default")

    def _write10(self, handle, lba: int, sectors: int, data: bytearray, on_log: OnLog) -> bool:
        # 2Ah WRITE(10): LBA in bytes 2-5 (big-endian), transfer length in
        # sectors in bytes 7-8 (big-endian). Data is sectors * 2048 bytes.
        cdb = bytes([0x2A, 0x00,
                     (lba >> 24) & 0xFF, (lba >> 16) & 0xFF, (lba >> 8) & 0xFF, lba & 0xFF,
                     0x00,
                     (sectors >> 8) & 0xFF, sectors & 0xFF,
                     0x00])
        ok, sense, _d, status = self._scsi(handle, cdb, SCSI_IOCTL_DATA_OUT,
                                           data=data, timeout=120)
        if not ok:
            on_log(f"spti: WRITE(10) failed at LBA {lba} ({self._sense_str(sense)})")
        return ok

    def _synchronize_cache(self, handle, on_log: OnLog) -> bool:
        # 35h SYNCHRONIZE CACHE.
        cdb = bytes([0x35, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])
        ok, sense, _d, status = self._scsi(handle, cdb, SCSI_IOCTL_DATA_UNSPECIFIED, timeout=180)
        if not ok:
            on_log(f"spti: SYNCHRONIZE CACHE failed ({self._sense_str(sense)})")
        return ok

    def _close_track_session(self, handle, close_function: int, track: int, on_log: OnLog) -> bool:
        # 5Bh CLOSE TRACK SESSION: byte 2 bits 0-2 = Close Function, bytes 4-5 =
        # Logical Track Number (big-endian). Immed bit (byte1 bit0) left 0 so the
        # call blocks until done. Close Function 010b (=2) with track 0 closes
        # the session (finalize per mode page Multi-session=00b).
        cdb = bytes([0x5B, 0x00, close_function & 0x07, 0x00,
                     (track >> 8) & 0xFF, track & 0xFF,
                     0x00, 0x00, 0x00, 0x00])
        ok, sense, _d, status = self._scsi(handle, cdb, SCSI_IOCTL_DATA_UNSPECIFIED, timeout=300)
        if not ok:
            on_log(f"spti: CLOSE TRACK SESSION (fn {close_function}) failed ({self._sense_str(sense)})")
        return ok

    # -- public: burn a finished ISO to a blank CD-R --------------------------
    def burn_iso(self, iso_path: Path, device: str,
                 on_status: OnStatus, on_progress: OnProgress, on_log: OnLog,
                 speed_kbps: int = 0, dummy: bool = False,
                 eject_after: bool = True) -> None:
        """Burn a finished ISO image to a blank CD-R via SPTI/MMC (TAO).

        speed_kbps: write speed in kbytes/sec (1000 bytes), or 0 for fastest.
        dummy: True runs a test/simulation write (laser off) if the drive allows.
        """
        iso_path = Path(iso_path)
        size = iso_path.stat().st_size
        total_sectors = (size + CD_SECTOR_DATA - 1) // CD_SECTOR_DATA
        on_log(f"spti: ISO {iso_path} size={size} bytes -> {total_sectors} sectors")

        handle = self._open_drive(device)
        try:
            self._lock_volume(handle, on_log)

            on_status("Checking media (SPTI/MMC)...")
            profile = self._get_configuration(handle, on_log)
            if profile is not None:
                on_log(f"spti: current profile = 0x{profile:04X} "
                       f"({'CD-R' if profile == 0x09 else 'CD-RW' if profile == 0x0A else 'other'})")
                if profile not in (0x09, 0x0A):
                    raise RuntimeError(f"Media is not CD-R/CD-RW (profile 0x{profile:04X}); "
                                       f"this writer handles CD only.")

            disc_status = self._read_disc_information(handle, on_log)
            if disc_status is not None:
                names = {0: "blank", 1: "appendable", 2: "finalized", 3: "other"}
                on_log(f"spti: disc status = {names.get(disc_status, disc_status)}")
                if disc_status == 2:
                    raise RuntimeError("Disc is finalized; cannot write. Use a blank CD-R.")
                if disc_status == 3:
                    raise RuntimeError("Disc is in an unsuitable state for writing.")

            # Speed first (before writing), then write parameters.
            self._set_cd_speed(handle, speed_kbps, on_log)

            on_status("Setting write parameters (SPTI/MMC)...")
            if not self._mode_select_write_params(handle, on_log, dummy=dummy):
                raise RuntimeError("Drive rejected the write parameters (MODE SELECT 05h).")

            nwa = self._read_next_writable_address(handle, on_log)
            if nwa is None:
                nwa = 0
            on_log(f"spti: starting write at LBA {nwa}")

            on_status("Burning data disc (SPTI/MMC)..." + (" [TEST]" if dummy else ""))
            written = 0
            lba = nwa
            with open(iso_path, "rb") as f:
                while True:
                    if self._cancelled:
                        raise RuntimeError("Burn cancelled")
                    chunk = f.read(CD_SECTOR_DATA * SECTORS_PER_WRITE)
                    if not chunk:
                        break
                    # Pad the final chunk up to a whole number of 2048-byte
                    # sectors; a CD can only be written in whole blocks.
                    rem = len(chunk) % CD_SECTOR_DATA
                    if rem:
                        chunk = chunk + (b"\x00" * (CD_SECTOR_DATA - rem))
                    sectors = len(chunk) // CD_SECTOR_DATA
                    if not self._write10(handle, lba, sectors, bytearray(chunk), on_log):
                        raise RuntimeError(f"Write failed at sector {lba}.")
                    lba += sectors
                    written += sectors
                    # EXACT progress: we know precisely how many sectors are on
                    # the disc. No COM, no events, no estimate.
                    on_progress(max(0, min(99, int((written * 100) / max(1, total_sectors)))))

            on_status("Flushing drive cache (SPTI/MMC)...")
            if not self._synchronize_cache(handle, on_log):
                raise RuntimeError("SYNCHRONIZE CACHE failed; disc may be incomplete.")

            on_status("Closing session (SPTI/MMC)...")
            # Close Function 010b (=2), track 0 -> close session; with mode page
            # Multi-session=00b this finalizes the disc.
            if not self._close_track_session(handle, 0x02, 0, on_log):
                # Some drives finalize purely on cache sync and error on an
                # explicit close; the data is already written, so warn only.
                on_log("spti: session close returned an error; disc data is written "
                       "but the disc may be left appendable/unfinalized.")

            on_progress(100)
            on_status("Data disc burned successfully (SPTI/MMC).")
        finally:
            self._unlock_volume(handle)
            if eject_after:
                try:
                    # START STOP UNIT (1Bh) with LoEj=1, Start=0 -> eject.
                    self._scsi(handle, bytes([0x1B, 0x00, 0x00, 0x00, 0x02, 0x00,
                                              0x00, 0x00, 0x00, 0x00]),
                               SCSI_IOCTL_DATA_UNSPECIFIED)
                except Exception:
                    pass
            try:
                ctypes.windll.kernel32.CloseHandle(handle)
            except Exception:
                pass
