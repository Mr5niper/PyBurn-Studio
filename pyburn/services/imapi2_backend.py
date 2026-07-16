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
        log("_recorder_for: creating MsftDiscRecorder2...")
        recorder = self._new("IMAPI2.MsftDiscRecorder2")
        want = (device or "").rstrip("\\/").upper()
        chosen_id = None
        try:
            count = master.Count
            log(f"_recorder_for: {count} IMAPI device(s) present")
            for i in range(count):
                uid = master.Item(i)
                try:
                    log(f"_recorder_for: init recorder for device index {i}...")
                    recorder.InitializeDiscRecorder(uid)
                    vols = recorder.VolumePathNames
                    vol_list = [str(v).rstrip("\\/").upper() for v in vols]
                    log(f"_recorder_for: device {i} volumes={vol_list} (want {want})")
                    for v in vol_list:
                        if v == want:
                            chosen_id = uid
                            break
                except Exception as e:
                    log(f"_recorder_for: device {i} probe failed: {e}")
                    continue
                if chosen_id:
                    break
            if chosen_id is None and count > 0:
                log("_recorder_for: no volume match; falling back to first device")
                chosen_id = master.Item(0)
        except Exception as e:
            log(f"_recorder_for: enumeration error: {e}")
        if chosen_id is None:
            raise RuntimeError("No optical recorder found via IMAPI2")
        log("_recorder_for: final InitializeDiscRecorder on chosen device...")
        recorder.InitializeDiscRecorder(chosen_id)
        log("_recorder_for: recorder ready")
        return recorder

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
        except Exception:
            pass
        return info

    def blank(self, device: str, on_status: OnStatus, on_log: OnLog) -> bool:
        try:
            recorder = self._recorder_for(device)
            eraser = self._new("IMAPI2.MsftDiscFormat2Erase")
            eraser.Recorder = recorder
            eraser.FullErase = False  # quick erase
            on_status("Erasing rewritable media (IMAPI2)...")
            eraser.EraseMedia()
            return True
        except Exception as e:
            on_log(f"IMAPI2 erase failed: {e}")
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
                  auto_blank: bool = True, eject_after: bool = True) -> None:
        """Build a data image from files/folders and burn it, all via IMAPI2."""
        recorder = self._recorder_for(device)
        info = self.get_media_info(device)
        if auto_blank and info.get("blank") is False:
            self.blank(device, on_status, on_log)
        on_status("Building data image (IMAPI2)...")
        fsi = self._new("IMAPI2FS.MsftFileSystemImage")
        try:
            fsi.FreeMediaBlocks = -1  # let IMAPI size to media
        except Exception:
            pass
        try:
            fsi.VolumeName = (volume or "DATA_DISC")[:32]
        except Exception:
            pass
        # Choose Joliet + ISO9660 + UDF for broad compatibility.
        try:
            fsi.ChooseImageDefaults(recorder)
        except Exception:
            pass
        root = fsi.Root
        self._add_tree(root, files, on_log)
        result = fsi.CreateResultImage()
        image_stream = result.ImageStream
        data = self._new("IMAPI2.MsftDiscFormat2Data")
        data.Recorder = recorder
        data.ClientName = "PyBurn Studio"
        try:
            self._wire_progress(data, on_progress)
        except Exception:
            pass
        on_status("Burning data disc (IMAPI2)...")
        try:
            data.Write(image_stream)
            on_progress(100)
            on_status("Data disc burned successfully (IMAPI2).")
        finally:
            if eject_after:
                try:
                    recorder.EjectMedia()
                except Exception:
                    pass

    def _add_tree(self, dir_item, files: List[Path], on_log: OnLog):
        """Recursively add files/dirs into an IMAPI file system image directory."""
        for p in files:
            try:
                if p.is_dir():
                    # AddTree adds the directory contents under a named subdir.
                    dir_item.AddTree(str(p), False)
                elif p.is_file():
                    with open(p, "rb"):
                        pass
                    # AddFile takes a path relative in the image and an IStream.
                    istream = self._istream_for_file(p)
                    dir_item.AddFile(p.name, istream)
            except Exception as e:
                on_log(f"IMAPI2 add failed for {p}: {e}")

    def burn_audio(self, wav_files: List[Path], device: str,
                   on_status: OnStatus, on_progress: OnProgress, on_log: OnLog,
                   eject_after: bool = True) -> None:
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
        try:
            self._wire_progress(tao, on_progress, audio=True)
        except Exception:
            pass

        n = max(1, len(wav_files))
        on_status("Preparing disc for audio burn (IMAPI2)...")
        on_log("burn_audio: calling PrepareMedia()...")
        tao.PrepareMedia()
        on_log("burn_audio: PrepareMedia() returned; adding tracks...")
        try:
            for i, wav in enumerate(wav_files, start=1):
                on_status(f"Writing audio track {i}/{n} (IMAPI2)...")
                on_log(f"burn_audio: track {i}/{n}: opening stream for {wav}")
                istream = self._audio_istream_for_wav(wav)
                on_log(f"burn_audio: track {i}/{n}: AddAudioTrack()...")
                tao.AddAudioTrack(istream)
                on_log(f"burn_audio: track {i}/{n}: added OK")
                on_progress(int((i / n) * 100))
        finally:
            # Always release the media, even if a track write raised, so the
            # drive is left in a sane state.
            try:
                on_log("burn_audio: calling ReleaseMedia()...")
                tao.ReleaseMedia()
                on_log("burn_audio: ReleaseMedia() returned")
            except Exception as e:
                on_log(f"IMAPI2 ReleaseMedia warning: {e}")
        on_progress(100)
        on_status("Audio CD created successfully (IMAPI2).")
        if eject_after:
            try:
                recorder.EjectMedia()
            except Exception:
                pass

    # -- low level: IStream over files ---------------------------------------
    def _istream_for_file(self, path: Path):
        """Create an IStream over a file using SHCreateStreamOnFileEx."""
        import ctypes
        from ctypes import wintypes
        import comtypes
        STGM_READ = 0x00000000
        shlwapi = ctypes.windll.shlwapi
        ppstm = ctypes.POINTER(comtypes.IUnknown)()
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
        from comtypes.stream import IStream  # type: ignore
        return ppstm.QueryInterface(IStream)

    def _audio_istream_for_wav(self, wav: Path):
        # Audio tracks want raw 44100/16/stereo PCM; IMAPI accepts a WAV IStream
        # through the same file stream mechanism.
        return self._istream_for_file(wav)

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
