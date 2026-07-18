from __future__ import annotations
import os
import time
from pathlib import Path
from typing import Callable, List, Optional

# This backend is Windows-only. It drives the built-in Windows burning engine,
# IMAPI2 (Image Mastering API v2), through COM using comtypes, the same binding
# the sibling audio project uses and ships cleanly in its PyInstaller builds.
#
# IMAPI2 covers, with NO external tools:
#   - building a data image and burning it (data discs)
#   - burning an already-prepared .iso image (stream to disc)
#   - writing audio tracks (audio CD)
#   - erasing rewritable media, ejecting
#   - reading current media info
#
# It does NOT author DVD-Video or BDMV structures; those are generated first
# (on Windows, inside WSL2) and handed to burn_image() here as a finished image.
#
# All COM work is guarded so an environment without IMAPI2 (e.g. running the
# import on Linux for a syntax/compile check) does not explode at import time.

OnStatus = Callable[[str], None]
OnProgress = Callable[[int], None]
OnLog = Callable[[str], None]


def _require_windows():
    import platform
    if platform.system().lower() != "windows":
        raise RuntimeError("IMAPI2 backend is only available on Windows")


class _Imapi2Progress:
    """Sink object IMAPI2 calls back with burn progress.

    IMAPI2 fires DiscFormat2Data Update events with the current sector and the
    last sector. comtypes lets us implement the event interface in Python; we
    translate it into a 0-100 percent for the caller. If wiring the event sink
    fails on a given system we fall back to an indeterminate pulse so the UI
    still moves.
    """
    def __init__(self, on_progress: OnProgress):
        self.on_progress = on_progress

    def Update(self, sender, progress):  # noqa: N802 (COM naming)
        try:
            # progress is an IDiscFormat2DataEventArgs; SectorCount and
            # LastWrittenLba/StartLba give us a ratio.
            total = int(progress.SectorCount)
            written = int(progress.LastWrittenLba) - int(progress.StartLba)
            if total > 0:
                pct = max(0, min(100, int((written / total) * 100)))
                self.on_progress(pct)
        except Exception:
            pass


class IMAPI2Backend:
    def __init__(self):
        _require_windows()
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    # -- device helpers -------------------------------------------------------
    def _new(self, progid: str):
        import comtypes.client
        return comtypes.client.CreateObject(progid)

    def _recorder_for(self, device: str, on_log: Optional[OnLog] = None):
        """Return an MsftDiscRecorder2 bound to the drive.

        `device` on Windows is a drive letter like 'E:' or an IMAPI unique id.
        We map a drive letter to its IMAPI recorder by matching volume paths.
        """
        def log(m):
            if on_log:
                on_log(m)
        import comtypes.client
        log("_recorder_for: creating MsftDiscMaster2...")
        master = self._new("IMAPI2.MsftDiscMaster2")
        want = (device or "").rstrip("\\/").upper()
        chosen_id = None
        matched_recorder = None
        try:
            count = master.Count
            log(f"_recorder_for: {count} IMAPI device(s) present")
            for i in range(count):
                uid = master.Item(i)
                # Use a FRESH recorder per device. Reusing one recorder object
                # and calling InitializeDiscRecorder on it more than once can
                # block, which is exactly what hung the burn: the match loop
                # left the recorder initialized to H:, then a second
                # InitializeDiscRecorder on the same object at the end froze.
                probe = self._new("IMAPI2.MsftDiscRecorder2")
                try:
                    log(f"_recorder_for: init recorder for device index {i}...")
                    probe.InitializeDiscRecorder(uid)
                    vols = probe.VolumePathNames
                    vol_list = [str(v).rstrip("\\/").upper() for v in vols]
                    log(f"_recorder_for: device {i} volumes={vol_list} (want {want})")
                    if want in vol_list:
                        chosen_id = uid
                        matched_recorder = probe  # already initialized to it
                        break
                    if i == 0:
                        # Remember the first as a fallback recorder.
                        chosen_id = chosen_id or None
                        first_recorder = probe
                except Exception as e:
                    log(f"_recorder_for: device {i} probe failed: {e}")
                    continue
            if matched_recorder is None and count > 0:
                # No exact volume match: fall back to a fresh recorder on the
                # first device, initialized exactly once.
                log("_recorder_for: no volume match; using first device")
                matched_recorder = self._new("IMAPI2.MsftDiscRecorder2")
                matched_recorder.InitializeDiscRecorder(master.Item(0))
        except Exception as e:
            log(f"_recorder_for: enumeration error: {e}")
        if matched_recorder is None:
            raise RuntimeError("No optical recorder found via IMAPI2")
        # IMPORTANT: matched_recorder is ALREADY initialized to the target drive.
        # Do NOT call InitializeDiscRecorder again (that second call is what
        # hung). Return it as-is.
        log("_recorder_for: recorder ready (reusing initialized recorder)")
        return matched_recorder

    # -- media info / blank / eject ------------------------------------------
    def get_media_info(self, device: str) -> dict:
        info = {"type": "unknown", "rewritable": None, "blank": None, "speeds": None}
        try:
            recorder = self._recorder_for(device)
            data = self._new("IMAPI2.MsftDiscFormat2Data")
            if data.IsCurrentMediaSupported(recorder):
                data.Recorder = recorder
                info["blank"] = bool(data.MediaHeuristicallyBlank)
                # PhysicalMediaType is an enum; we keep it coarse.
                mt = int(data.CurrentPhysicalMediaType)
                # A small subset of IMAPI_MEDIA_PHYSICAL_TYPE values.
                if mt in (1, 2, 3, 4, 5, 6):
                    info["type"] = "CD"
                elif mt in (7, 8, 9, 10, 11, 12, 13):
                    info["type"] = "DVD"
                elif mt in (14, 15, 16, 17, 18, 19):
                    info["type"] = "BD"
                # Rewritable physical media types (IMAPI_MEDIA_PHYSICAL_TYPE):
                # CDRW=3, DVDRAM=4, DVDPLUSRW=6, DVDPLUSRW_DL=8, DVDDASHRW=10,
                # DVDDASHRW_DL=12, BDRE=17. Everything else (CDR=2, DVDR, BDR,
                # etc.) is write-once and must NEVER be erased.
                info["rewritable"] = mt in (3, 4, 6, 8, 10, 12, 17)
        except Exception:
            pass
        return info

    def blank(self, device: str, on_status: OnStatus, on_log: OnLog) -> bool:
        try:
            recorder = self._recorder_for(device)
            eraser = self._new("IMAPI2.MsftDiscFormat2Erase")
            eraser.Recorder = recorder
            # ClientName is REQUIRED; without it EraseMedia throws
            # "The client name is not valid."
            eraser.ClientName = "PyBurn Studio"
            eraser.FullErase = False  # quick erase
            on_status("Erasing rewritable media (IMAPI2)...")
            eraser.EraseMedia()
            on_log("IMAPI2 erase completed")
            return True
        except Exception as e:
            on_log(f"IMAPI2 erase failed: {e!r}")
            return False

    def eject(self, device: str) -> None:
        try:
            recorder = self._recorder_for(device)
            recorder.EjectMedia()
        except Exception:
            pass

    # -- data + ISO burn ------------------------------------------------------
    def burn_image(self, iso_path: Path, device: str, on_status: OnStatus,
                   on_progress: OnProgress, on_log: OnLog, eject_after: bool = True) -> None:
        """Stream an already-prepared image file to disc via IMAPI2.

        Used both for data ISOs we build here and for images handed over from
        the WSL2 authoring step (DVD-Video, BDMV).
        """
        import comtypes.client
        recorder = self._recorder_for(device)
        data = self._new("IMAPI2.MsftDiscFormat2Data")
        data.Recorder = recorder
        data.ClientName = "PyBurn Studio"
        try:
            stream = self._new("IMAPI2FS.MsftFileSystemImage")  # placeholder guard
        except Exception:
            stream = None
        on_status("Burning image to disc (IMAPI2)...")
        # Build an IStream over the ISO file.
        try:
            from comtypes import GUID
            import comtypes.client
            shell = None
        except Exception:
            pass
        # Use SHCreateStreamOnFileEx via ctypes to get an IStream for the ISO.
        istream = self._istream_for_file(iso_path)
        sink = None
        try:
            sink = self._wire_progress(data, on_progress)
        except Exception:
            sink = None
        try:
            data.Write(istream)
            on_progress(100)
            on_status("Image burned successfully (IMAPI2).")
        finally:
            if eject_after:
                try:
                    recorder.EjectMedia()
                except Exception:
                    pass

    def burn_data(self, files: List[Path], device: str, temp_dir: Path, volume: str,
                  on_status: OnStatus, on_progress: OnProgress, on_log: OnLog,
                  auto_blank: bool = True, eject_after: bool = True, speed="Auto",
                  dummy: bool = False) -> None:
        """Build a data image from files/folders and burn it, all via IMAPI2."""
        on_log(f"burn_data: start, device={device}, items={len(files)}")
        on_log("burn_data: resolving recorder...")
        recorder = self._recorder_for(device, on_log=on_log)
        on_log("burn_data: recorder resolved OK")
        info = self.get_media_info(device)
        on_log(f"burn_data: media info blank={info.get('blank')} rewritable={info.get('rewritable')} type={info.get('type')}")
        # Only erase REWRITABLE media that is not blank. MediaHeuristicallyBlank
        # gives false negatives on blank CD-Rs, and CD-R/DVD-R/BD-R are
        # write-once and cannot be erased at all. Erasing (or trying to) a
        # write-once disc fails and then blocks the burn, so gate on rewritable.
        if auto_blank and info.get("rewritable") is True and info.get("blank") is False:
            on_log("burn_data: rewritable media not blank, erasing...")
            self.blank(device, on_status, on_log)
        elif info.get("blank") is False and info.get("rewritable") is not True:
            on_log("burn_data: media reports not-blank but is write-once (CD-R/DVD-R/BD-R); "
                   "not erasing. If this is a used write-once disc the write may fail; "
                   "otherwise MediaHeuristicallyBlank is a false negative and the write will proceed.")
        on_status("Building data image (IMAPI2)...")
        on_log("burn_data: creating MsftFileSystemImage...")
        fsi = self._new("IMAPI2FS.MsftFileSystemImage")
        # ChooseImageDefaults(recorder) MUST run first: it inspects the disc in
        # the drive and configures the image (file-system types, block count,
        # media size) to match. Setting FreeMediaBlocks by hand afterwards is
        # wrong; the previous code set it to -1, which is not "size to media" but
        # a bogus block count, so the image was built for the wrong size and the
        # drive rejected the write with an unrecoverable error. Let
        # ChooseImageDefaults own the sizing.
        try:
            on_log("burn_data: ChooseImageDefaults(recorder)...")
            fsi.ChooseImageDefaults(recorder)
        except Exception as e:
            on_log(f"burn_data: ChooseImageDefaults warning: {e}")
        # Link the filesystem image to the recorder's multisession state BEFORE
        # building it. Create a MsftDiscFormat2Data now, and hand its
        # MultisessionInterfaces to the image so the image is laid out for the
        # actual disc (starting sector, session import). Without this the image
        # is built assuming a layout that need not match the disc, and the write
        # opens a session then fails. For a blank disc this simply starts a new
        # session at 0; for an appendable disc it imports prior content.
        data = self._new("IMAPI2.MsftDiscFormat2Data")
        data.Recorder = recorder
        data.ClientName = "PyBurn Studio"
        try:
            ms = data.MultisessionInterfaces
            fsi.MultisessionInterfaces = ms
            on_log("burn_data: linked MultisessionInterfaces to the image")
        except Exception as e:
            # On some blank-media/driver combinations this property is null and
            # cannot be assigned; that is fine for a fresh single-session burn.
            on_log(f"burn_data: MultisessionInterfaces not set ({e!r}); single-session burn")
        try:
            fsi.VolumeName = (volume or "DATA_DISC")[:32]
        except Exception as e:
            on_log(f"burn_data: VolumeName warning: {e}")
        try:
            # FsiFileSystemISO9660 (1) | FsiFileSystemJoliet (2) | FsiFileSystemUDF (4) = 7
            fsi.FileSystemsToCreate = 7
        except Exception as e:
            on_log(f"burn_data: FileSystemsToCreate warning: {e}")
        root = fsi.Root
        added = self._add_tree(root, files, on_log)
        if added == 0:
            raise RuntimeError("No files could be added to the data image; nothing to burn.")
        on_log(f"burn_data: added {added} item(s); creating result image...")
        try:
            result = fsi.CreateResultImage()
            image_stream = result.ImageStream
        except Exception as e:
            raise RuntimeError(f"Failed to build the data image: {e!r}")
        on_log("burn_data: data formatter ready (created before image for multisession link)")
        # Log what the drive/media supports for diagnostics, but let IMAPI2
        # choose the actual write speed (its default).
        try:
            cur = getattr(data, "CurrentMediaType", None)
            on_log(f"burn_data: current media type = {cur}")
        except Exception as e:
            on_log(f"burn_data: media type query warning: {e}")
        try:
            descriptors = data.SupportedWriteSpeedDescriptors
            speeds = []
            for d in descriptors:
                try:
                    speeds.append((int(d.MediaType), int(d.WriteSpeed), int(d.RotationTypeIsPureCAV)))
                except Exception:
                    pass
            on_log(f"burn_data: supported write-speed descriptors = {speeds}")
        except Exception as e:
            on_log(f"burn_data: write-speed query warning: {e}")
        self._apply_write_speed(data, speed, on_log)
        try:
            self._wire_progress(data, on_progress)
        except Exception:
            pass
        # Diagnostics + correctness around the physical write:
        # - Log the PHYSICAL media type and whether this formatter says the media
        #   is supported. CurrentMediaType came back None, so confirm what the
        #   formatter actually sees.
        # - Acquire exclusive access to the recorder for the duration of the
        #   write. Without it, the Windows shell / autoplay / indexing can hold
        #   the drive and the physical write fails with an unrecoverable drive
        #   error even though the image is valid. The audio TrackAtOnce path
        #   happened to work without this, but data writing is stricter.
        try:
            phys = int(data.CurrentPhysicalMediaType)
            on_log(f"burn_data: CurrentPhysicalMediaType = {phys}")
        except Exception as e:
            on_log(f"burn_data: physical media type warning: {e}")
        try:
            supported = bool(data.IsCurrentMediaSupported(recorder))
            on_log(f"burn_data: IsCurrentMediaSupported = {supported}")
        except Exception as e:
            on_log(f"burn_data: media support query warning: {e}")
        acquired = False
        try:
            recorder.AcquireExclusiveAccess(True, "PyBurn Studio")
            acquired = True
            on_log("burn_data: acquired exclusive access to recorder")
        except Exception as e:
            on_log(f"burn_data: AcquireExclusiveAccess warning (continuing): {e}")
        on_status("Burning data disc (IMAPI2)...")
        if dummy:
            try:
                data.SimulateWrite = True
                on_log("burn_data: DUMMY/TEST burn - SimulateWrite=True, laser off, disc NOT written")
                on_status("Test burn (simulated, disc not written)...")
            except Exception as e:
                on_log(f"burn_data: SimulateWrite not available ({e!r}); cannot simulate, aborting to protect the disc")
                raise RuntimeError("Dummy burn requested but this drive/driver does not support "
                                   "IMAPI2 SimulateWrite; aborting so a disc is not consumed.")
        on_log("burn_data: calling Write(image_stream)...")
        burn_error = None
        try:
            data.Write(image_stream)
            on_log("burn_data: Write() returned OK")
            on_progress(100)
            on_status("Data disc burned successfully (IMAPI2).")
        except Exception as e:
            burn_error = e
            on_log(f"burn_data: FAILED Write: {e!r}")
        finally:
            if acquired:
                try:
                    recorder.ReleaseExclusiveAccess()
                    on_log("burn_data: released exclusive access")
                except Exception:
                    pass
            if eject_after:
                try:
                    on_log("burn_data: ejecting media...")
                    recorder.EjectMedia()
                except Exception:
                    pass
        if burn_error is not None:
            raise RuntimeError(f"Data disc burn failed: {burn_error}")

    def _add_tree(self, dir_item, files: List[Path], on_log: OnLog) -> int:
        """Recursively add files/dirs into an IMAPI file system image directory.
        Returns the number of items successfully added."""
        added = 0
        for p in files:
            try:
                if p.is_dir():
                    on_log(f"burn_data: AddTree {p}")
                    dir_item.AddTree(str(p), False)
                    added += 1
                elif p.is_file():
                    on_log(f"burn_data: AddFile {p.name}")
                    istream = self._istream_for_file(p)
                    dir_item.AddFile(p.name, istream)
                    added += 1
            except Exception as e:
                on_log(f"burn_data: add FAILED for {p}: {e!r}")
        return added

    def burn_audio(self, wav_files: List[Path], device: str,
                   on_status: OnStatus, on_progress: OnProgress, on_log: OnLog,
                   eject_after: bool = True, speed="Auto") -> None:
        """Burn Red Book audio tracks from 44100/16-bit stereo WAV files.

        Uses the TrackAtOnce interface. PrepareMedia() is called EXACTLY ONCE
        before adding any tracks, then every track is added, then ReleaseMedia()
        once at the end. (An earlier version called PrepareMedia() inside the
        per-track loop, which throws a COM error after the first track and
        aborted the burn.)
        """
        on_status("Connecting to burner (IMAPI2)...")
        on_log(f"burn_audio: start, device={device}, tracks={len(wav_files)}")
        on_log("burn_audio: resolving recorder (enumerating IMAPI2 drives)...")
        recorder = self._recorder_for(device, on_log=on_log)
        on_log("burn_audio: recorder resolved OK")
        try:
            on_log("burn_audio: creating TrackAtOnce formatter...")
            tao = self._new("IMAPI2.MsftDiscFormat2TrackAtOnce")
        except Exception as e:
            raise RuntimeError(f"IMAPI2 TrackAtOnce interface unavailable: {e}")
        if tao is None:
            raise RuntimeError("IMAPI2 TrackAtOnce audio interface unavailable on this system")

        on_log("burn_audio: assigning recorder to formatter...")
        tao.Recorder = recorder
        tao.ClientName = "PyBurn Studio"
        self._apply_write_speed(tao, speed, on_log)
        try:
            self._wire_progress(tao, on_progress, audio=True)
        except Exception:
            pass

        n = max(1, len(wav_files))
        on_status("Preparing disc for audio burn (IMAPI2)...")
        on_log("burn_audio: calling PrepareMedia()...")
        tao.PrepareMedia()
        on_log("burn_audio: PrepareMedia() returned; adding tracks...")
        added = 0
        burn_error = None
        try:
            for i, wav in enumerate(wav_files, start=1):
                on_status(f"Writing audio track {i}/{n} (IMAPI2)...")
                on_log(f"burn_audio: track {i}/{n}: opening raw PCM stream for {wav}")
                try:
                    istream = self._audio_istream_for_wav(wav)
                except Exception as e:
                    # Surface the REAL reason instead of silently releasing.
                    on_log(f"burn_audio: track {i}/{n}: FAILED opening stream: {e!r}")
                    raise
                on_log(f"burn_audio: track {i}/{n}: AddAudioTrack()...")
                try:
                    tao.AddAudioTrack(istream)
                except Exception as e:
                    on_log(f"burn_audio: track {i}/{n}: FAILED AddAudioTrack: {e!r}")
                    raise
                added += 1
                on_log(f"burn_audio: track {i}/{n}: added OK")
                on_progress(int((i / n) * 100))
        except Exception as e:
            burn_error = e
        finally:
            # Always release the media, even if a track write raised, so the
            # drive is left in a sane state.
            try:
                on_log("burn_audio: calling ReleaseMedia()...")
                tao.ReleaseMedia()
                on_log("burn_audio: ReleaseMedia() returned")
            except Exception as e:
                on_log(f"IMAPI2 ReleaseMedia warning: {e}")
        if burn_error is not None:
            # Do NOT report success when nothing burned.
            raise RuntimeError(f"Audio burn failed after {added}/{n} tracks: {burn_error}")
        if added == 0:
            raise RuntimeError("Audio burn added no tracks; nothing was written.")
        on_progress(100)
        on_status("Audio CD created successfully (IMAPI2).")
        if eject_after:
            try:
                recorder.EjectMedia()
            except Exception:
                pass

    # -- low level: IStream over files ---------------------------------------
    def _get_istream_type(self):
        """Return the comtypes IStream interface class.

        In a frozen onefile build, IStream is not a plain importable symbol; it
        lives in a generated module. comtypes generates it on demand from
        portabledeviceapi.dll via GetModule. The build bundles comtypes fully
        (--collect-all comtypes) so this generation works at runtime, matching
        how the sibling audioctl onefile handles comtypes. We cache the result.
        """
        if getattr(self, "_istream_type", None) is not None:
            return self._istream_type
        import comtypes.client
        # Generate/import the interface. Try the standard source first.
        last_err = None
        for gen_arg, modname, attr in (
            ("portabledeviceapi.dll", "comtypes.gen.PortableDeviceApiLib", "IStream"),
        ):
            try:
                comtypes.client.GetModule(gen_arg)
                mod = __import__(modname, fromlist=[attr])
                self._istream_type = getattr(mod, attr)
                return self._istream_type
            except Exception as e:
                last_err = e
        raise RuntimeError(f"Could not obtain IStream interface via comtypes: {last_err}")

    def _istream_for_file(self, path: Path):
        """Create an IStream over a file using SHCreateStreamOnFileEx."""
        import ctypes
        from ctypes import wintypes
        IStream = self._get_istream_type()
        STGM_READ = 0x00000000
        shlwapi = ctypes.windll.shlwapi
        ppstm = ctypes.POINTER(IStream)()
        # SHCreateStreamOnFileEx(pszFile, grfMode, dwAttributes, fCreate, pstmTemplate, ppstm)
        hr = shlwapi.SHCreateStreamOnFileEx(
            ctypes.c_wchar_p(str(path)),
            wintypes.DWORD(STGM_READ),
            wintypes.DWORD(0x80),  # FILE_ATTRIBUTE_NORMAL
            wintypes.BOOL(False),
            None,
            ctypes.byref(ppstm),
        )
        if hr != 0:
            raise OSError(f"SHCreateStreamOnFileEx failed: 0x{hr & 0xffffffff:08x}")
        return ppstm

    def _audio_istream_for_wav(self, wav: Path):
        # IMAPI2 AddAudioTrack requires RAW 16-bit little-endian stereo 44100 Hz
        # PCM with NO WAV/RIFF header, AND the total byte length MUST be a whole
        # number of CD audio sectors (2352 bytes each). ffmpeg output is almost
        # never sector-aligned, and an unaligned stream makes AddAudioTrack fail
        # with "The provided audio stream is not valid." So: parse the WAV, take
        # the data chunk, pad the PCM up to the next 2352-byte boundary with
        # silence (zeros), and stream that.
        CD_SECTOR = 2352
        wav = Path(wav)
        pcm_path = wav.with_suffix(".pcm")
        try:
            with open(wav, "rb") as f:
                riff = f.read(12)
                if riff[0:4] != b"RIFF" or riff[8:12] != b"WAVE":
                    # Not a WAV we recognize; stream as-is and hope for the best.
                    return self._istream_for_file(wav)
                data_offset = None
                data_size = None
                while True:
                    hdr = f.read(8)
                    if len(hdr) < 8:
                        break
                    cid = hdr[0:4]
                    csize = int.from_bytes(hdr[4:8], "little")
                    if cid == b"data":
                        data_offset = f.tell()
                        data_size = csize
                        break
                    f.seek(csize, 1)  # skip this chunk's body
                if data_offset is None:
                    return self._istream_for_file(wav)
                f.seek(data_offset)
                pcm = f.read(data_size)
            # Pad to a whole number of CD audio sectors (2352 bytes) with
            # silence, so IMAPI2 accepts the stream.
            remainder = len(pcm) % CD_SECTOR
            if remainder:
                pcm = pcm + (b"\x00" * (CD_SECTOR - remainder))
            with open(pcm_path, "wb") as out:
                out.write(pcm)
            return self._istream_for_file(pcm_path)
        except Exception:
            # On any parsing trouble, fall back to the raw file stream.
            return self._istream_for_file(wav)

    def _apply_write_speed(self, formatter, speed, on_log: OnLog):
        """Honor the user's Setup burn-speed choice on an IMAPI2 formatter.

        `speed` is either "Auto" (let IMAPI2 pick, the default) or a CD-style
        x-multiplier string like "8", "16", "24". One CD "x" is 150 KB/s, so we
        convert and pick the supported descriptor closest to (but not above) the
        requested KB/s. If anything is unavailable we leave IMAPI2 on its
        default rather than fail the burn.
        """
        try:
            if speed is None:
                return
            s = str(speed).strip().lower()
            if s in ("", "auto"):
                on_log("burn: write speed = Auto (IMAPI2 default)")
                return
            mult = int(float(s))
            want_kbps = mult * 150  # 1x CD = 150 KB/s
            # Find the closest supported speed at or below the request.
            best = None
            try:
                for d in formatter.SupportedWriteSpeedDescriptors:
                    ws = int(d.WriteSpeed)
                    if ws <= want_kbps and (best is None or ws > best[0]):
                        best = (ws, d)
                # If none at or below, take the slowest available.
                if best is None:
                    for d in formatter.SupportedWriteSpeedDescriptors:
                        ws = int(d.WriteSpeed)
                        if best is None or ws < best[0]:
                            best = (ws, d)
            except Exception:
                best = None
            if best is not None:
                rot = getattr(best[1], "RotationTypeIsPureCAV", False)
                on_log(f"burn: requested {mult}x (~{want_kbps} KB/s); setting {best[0]} KB/s")
                formatter.SetWriteSpeed(best[0], rot)
            else:
                on_log(f"burn: requested {mult}x (~{want_kbps} KB/s); no descriptors, using default")
        except Exception as e:
            on_log(f"burn: write-speed selection warning: {e}")

    def _wire_progress(self, formatter, on_progress: OnProgress, audio: bool = False):
        """Best-effort connect an IMAPI2 progress event sink.

        comtypes can generate the event interface from the type library; if that
        fails on a given box we simply skip live progress (the write still runs)
        and rely on coarse start/finish updates.
        """
        try:
            import comtypes.client

            class _Sink:
                def Update(self, sender, args):  # noqa: N802
                    try:
                        total = int(args.SectorCount)
                        written = int(args.LastWrittenLba) - int(args.StartLba)
                        if total > 0:
                            on_progress(max(0, min(100, int((written / total) * 100))))
                    except Exception:
                        pass

            comtypes.client.GetEvents(formatter, _Sink())
        except Exception:
            raise
