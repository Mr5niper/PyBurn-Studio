from __future__ import annotations
import os
import struct
import time
from pathlib import Path
from typing import Callable, List, Optional, Tuple

# Pure-Python ISO9660 + Joliet image builder. No COM, no IMAPI2, no external
# tools. Produces a single-session, non-bootable data image with both an
# ISO9660 (Primary Volume Descriptor) directory tree and a Joliet (Supplementary
# Volume Descriptor, UCS-2) directory tree that share the same file extents,
# which is exactly what mkisofs/genisoimage and IMAPI2 produce for a normal data
# disc. The output is a plain .iso file that our SPTI writer burns.
#
# Structure written, in sector (2048-byte) order:
#   sector 0..15  : System Area (zeros)
#   sector 16     : Primary Volume Descriptor (ISO9660)
#   sector 17     : Supplementary Volume Descriptor (Joliet)
#   sector 18     : Volume Descriptor Set Terminator
#   then          : L-path table (LE) and M-path table (BE) for ISO9660
#                   L-path table (LE) and M-path table (BE) for Joliet
#                   directory extents for ISO9660
#                   directory extents for Joliet
#                   file data extents (shared by both trees)
#
# References: ECMA-119 / ISO 9660 (via the OSDev ISO 9660 page for exact field
# offsets) and the Joliet specification (SVD escape sequence %/E for UCS-2
# Level 3, SVD type code 2, UCS-2 big-endian names).

OnStatus = Callable[[str], None]
OnLog = Callable[[str], None]

SECTOR = 2048


def _ceil_sectors(nbytes: int) -> int:
    return (nbytes + SECTOR - 1) // SECTOR


def _pad_to_sector(data: bytes) -> bytes:
    r = len(data) % SECTOR
    if r:
        data = data + b"\x00" * (SECTOR - r)
    return data


def _both_endian_16(v: int) -> bytes:
    # ISO9660 "both-byte" 16-bit: LSB-first then MSB-first.
    return struct.pack("<H", v) + struct.pack(">H", v)


def _both_endian_32(v: int) -> bytes:
    return struct.pack("<I", v) + struct.pack(">I", v)


def _dec_datetime(t: Optional[time.struct_time] = None) -> bytes:
    # 17-byte dec-datetime: 'YYYYMMDDHHMMSScc' + 1-byte GMT offset.
    if t is None:
        t = time.gmtime()
    s = "%04d%02d%02d%02d%02d%02d%02d" % (
        t.tm_year, t.tm_mon, t.tm_mday, t.tm_hour, t.tm_min, t.tm_sec, 0)
    return s.encode("ascii") + bytes([0])


def _dir_datetime(t: Optional[time.struct_time] = None) -> bytes:
    # 7-byte directory-record timestamp: years-since-1900, month, day, hour,
    # minute, second, GMT offset (in 15-min units, signed).
    if t is None:
        t = time.gmtime()
    return bytes([max(0, t.tm_year - 1900), t.tm_mon, t.tm_mday,
                  t.tm_hour, t.tm_min, t.tm_sec, 0])


class _Node:
    """A file or directory in the tree."""
    def __init__(self, name: str, is_dir: bool, path: Optional[Path] = None):
        self.name = name
        self.is_dir = is_dir
        self.path = path
        self.size = 0 if is_dir else (path.stat().st_size if path else 0)
        self.children: List["_Node"] = []
        # Assigned during layout:
        self.iso_name = ""          # 8.3 identifier for ISO9660
        self.joliet_name = ""       # UCS-2 name for Joliet
        self.extent_lba = 0         # start sector of data (file) or records (dir)
        self.data_len = 0           # byte length of data/records
        self.parent: Optional["_Node"] = None
        self.path_index_iso = 0     # 1-based path table index (ISO)
        self.path_index_joliet = 0


def _mangle_iso(name: str, is_dir: bool, used: set) -> str:
    """Produce a unique ISO9660 level-1-ish 8.3 uppercase identifier."""
    up = "".join(c if (c.isalnum() or c == "_") else "_" for c in name.upper())
    if is_dir:
        base = up[:8] or "DIR"
        cand = base
        n = 1
        while cand in used:
            suff = str(n)
            cand = (base[:8 - len(suff)] + suff)
            n += 1
        used.add(cand)
        return cand
    # File: split name/extension.
    if "." in name:
        stem, ext = name.rsplit(".", 1)
    else:
        stem, ext = name, ""
    stem_u = "".join(c if (c.isalnum() or c == "_") else "_" for c in stem.upper())[:8] or "FILE"
    ext_u = "".join(c if (c.isalnum() or c == "_") else "_" for c in ext.upper())[:3]
    cand = stem_u + ("." + ext_u if ext_u else "")
    n = 1
    while (cand + ";1") in used:
        suff = str(n)
        stem_try = stem_u[:8 - len(suff)] + suff
        cand = stem_try + ("." + ext_u if ext_u else "")
        n += 1
    used.add(cand + ";1")
    return cand + ";1"


def _dir_record(name_bytes: bytes, extent_lba: int, data_len: int,
                is_dir: bool, dt: bytes, special: Optional[int] = None) -> bytes:
    """Build one ISO9660 directory record.

    special: None for a normal named record; 0 for the '.' record; 1 for '..'.
    """
    if special == 0:
        ident = b"\x00"
    elif special == 1:
        ident = b"\x01"
    else:
        ident = name_bytes
    id_len = len(ident)
    rec_len = 33 + id_len
    pad = rec_len % 2
    rec_len += pad
    flags = 0x02 if is_dir else 0x00
    out = bytearray()
    out += bytes([rec_len])            # 0: length of record
    out += bytes([0])                  # 1: extended attribute length
    out += _both_endian_32(extent_lba) # 2-9: extent LBA (both-endian)
    out += _both_endian_32(data_len)   # 10-17: data length (both-endian)
    out += dt                          # 18-24: recording date/time (7 bytes)
    out += bytes([flags])              # 25: file flags
    out += bytes([0])                  # 26: file unit size (non-interleaved)
    out += bytes([0])                  # 27: interleave gap size
    out += _both_endian_16(1)          # 28-31: volume sequence number
    out += bytes([id_len])             # 32: length of identifier
    out += ident                       # 33..: identifier
    if pad:
        out += b"\x00"
    return bytes(out)


class ISOBuilder:
    def __init__(self):
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def build(self, files: List[Path], out_iso: Path, volume: str,
              on_status: OnStatus, on_log: OnLog) -> Path:
        on_status("Building ISO image (ISO9660 + Joliet)...")
        vol_id = (volume or "DATA_DISC").upper()[:32]

        # 1) Build the in-memory tree from the input paths. Top-level inputs are
        #    added under the root; a directory input is added recursively.
        root = _Node("", True)

        def add_path(parent: _Node, p: Path):
            if p.is_dir():
                node = _Node(p.name, True, p)
                parent.children.append(node)
                for child in sorted(p.iterdir(), key=lambda x: x.name.lower()):
                    add_path(node, child)
            elif p.is_file():
                parent.children.append(_Node(p.name, False, p))

        for p in files:
            add_path(root, p)

        # 2) Assign names (ISO 8.3 + Joliet UCS-2), collecting all directories in
        #    breadth-first order (root first) for the path tables.
        all_dirs: List[_Node] = [root]
        root.iso_name = ""
        root.joliet_name = ""

        def assign_names(node: _Node):
            used_iso = set()
            for child in node.children:
                child.parent = node
                child.iso_name = _mangle_iso(child.name, child.is_dir, used_iso)
                # Joliet name: UCS-2 big-endian, max 64 chars; drop nothing.
                jname = child.name[:64]
                child.joliet_name = jname
            for child in node.children:
                if child.is_dir:
                    all_dirs.append(child)
            for child in node.children:
                if child.is_dir:
                    assign_names(child)

        assign_names(root)

        # Assign path-table indices (1-based, root = 1), in BFS order.
        for i, d in enumerate(all_dirs, start=1):
            d.path_index_iso = i
            d.path_index_joliet = i

        # 3) Compute directory extent sizes for BOTH trees. A directory's data is
        #    the concatenation of its records: '.', '..', then each child.
        def dir_records_len(node: _Node, joliet: bool) -> int:
            dt = _dir_datetime()
            total = len(_dir_record(b"", 0, 0, True, dt, special=0))   # '.'
            total += len(_dir_record(b"", 0, 0, True, dt, special=1))  # '..'
            # Records cannot span a sector boundary; account for padding.
            cur = total
            def add(rec_len):
                nonlocal cur, total
                if (cur % SECTOR) + rec_len > SECTOR:
                    pad = SECTOR - (cur % SECTOR)
                    cur += pad
                    total += pad
                cur += rec_len
                total += rec_len
            # reset and recompute with the '.' and '..' already counted
            cur = total
            for child in node.children:
                if joliet:
                    nb = child.joliet_name.encode("utf-16-be")
                else:
                    nb = child.iso_name.encode("ascii")
                rec_len = 33 + len(nb)
                rec_len += rec_len % 2
                add(rec_len)
            return total

        # 4) Lay out sectors. Fixed prelude first.
        #    0-15 system area, 16 PVD, 17 SVD, 18 terminator = next free 19.
        lba = 19

        # Path tables. Compute their sizes.
        def path_table_size(joliet: bool) -> int:
            size = 0
            for d in all_dirs:
                if d is root:
                    id_bytes = b"\x00"  # root dir id is a single 0 byte
                else:
                    id_bytes = (d.joliet_name.encode("utf-16-be") if joliet
                                else d.iso_name.encode("ascii"))
                di_len = len(id_bytes)
                rec = 8 + di_len + (di_len % 2)
                size += rec
            return size

        iso_pt_size = path_table_size(False)
        jol_pt_size = path_table_size(True)

        iso_l_pt_lba = lba; lba += _ceil_sectors(iso_pt_size)
        iso_m_pt_lba = lba; lba += _ceil_sectors(iso_pt_size)
        jol_l_pt_lba = lba; lba += _ceil_sectors(jol_pt_size)
        jol_m_pt_lba = lba; lba += _ceil_sectors(jol_pt_size)

        # Directory extents: ISO tree, then Joliet tree.
        for d in all_dirs:
            d._iso_dir_len = dir_records_len(d, joliet=False)
            d.extent_lba = lba
            d.data_len = d._iso_dir_len
            lba += _ceil_sectors(d._iso_dir_len)
        for d in all_dirs:
            d._jol_dir_len = dir_records_len(d, joliet=True)
            d._jol_extent_lba = lba
            lba += _ceil_sectors(d._jol_dir_len)

        # File data extents (shared by both trees).
        def all_files(node: _Node) -> List[_Node]:
            out = []
            for c in node.children:
                if c.is_dir:
                    out += all_files(c)
                else:
                    out.append(c)
            return out

        files_list = all_files(root)
        for f in files_list:
            f.extent_lba = lba
            f.data_len = f.size
            # A file occupies ceil(size/2048) sectors; a 0-byte file occupies
            # ZERO data sectors (its extent length is 0). Reserving a sector for
            # an empty file would desync every following extent.
            lba += _ceil_sectors(f.size)
        total_sectors = lba

        on_log(f"iso: {len(all_dirs)} dirs, {len(files_list)} files, {total_sectors} sectors "
               f"({total_sectors * SECTOR} bytes)")

        # 5) Now WRITE everything to the file in order.
        out_iso = Path(out_iso)
        out_iso.parent.mkdir(parents=True, exist_ok=True)
        with open(out_iso, "wb") as fout:
            # System area
            fout.write(b"\x00" * (16 * SECTOR))
            # PVD (sector 16)
            fout.write(self._volume_descriptor(
                vd_type=1, vol_id=vol_id, total_sectors=total_sectors,
                pt_size=iso_pt_size, l_pt_lba=iso_l_pt_lba, m_pt_lba=iso_m_pt_lba,
                root=root, joliet=False))
            # SVD Joliet (sector 17)
            fout.write(self._volume_descriptor(
                vd_type=2, vol_id=vol_id, total_sectors=total_sectors,
                pt_size=jol_pt_size, l_pt_lba=jol_l_pt_lba, m_pt_lba=jol_m_pt_lba,
                root=root, joliet=True))
            # Terminator (sector 18)
            term = bytearray(SECTOR)
            term[0] = 0xFF
            term[1:6] = b"CD001"
            term[6] = 0x01
            fout.write(bytes(term))

            # Path tables
            fout.write(_pad_to_sector(self._path_table(all_dirs, root, joliet=False, big_endian=False)))
            fout.write(_pad_to_sector(self._path_table(all_dirs, root, joliet=False, big_endian=True)))
            fout.write(_pad_to_sector(self._path_table(all_dirs, root, joliet=True, big_endian=False)))
            fout.write(_pad_to_sector(self._path_table(all_dirs, root, joliet=True, big_endian=True)))

            # ISO directory extents
            for d in all_dirs:
                fout.write(_pad_to_sector(self._directory_extent(d, joliet=False)))
            # Joliet directory extents
            for d in all_dirs:
                fout.write(_pad_to_sector(self._directory_extent(d, joliet=True)))

            # File data
            for f in files_list:
                if self._cancelled:
                    raise RuntimeError("ISO build cancelled")
                if f.size > 0 and f.path is not None:
                    written = 0
                    with open(f.path, "rb") as fin:
                        while True:
                            chunk = fin.read(SECTOR * 64)
                            if not chunk:
                                break
                            fout.write(chunk)
                            written += len(chunk)
                    pad = written % SECTOR
                    if pad:
                        fout.write(b"\x00" * (SECTOR - pad))
                else:
                    # zero-length file: no data sectors written (extent_lba points
                    # at next area but data_len 0 is valid).
                    pass

        actual = out_iso.stat().st_size
        on_log(f"iso: wrote {actual} bytes to {out_iso}")
        return out_iso

    # -- volume descriptor ----------------------------------------------------
    def _volume_descriptor(self, vd_type: int, vol_id: str, total_sectors: int,
                           pt_size: int, l_pt_lba: int, m_pt_lba: int,
                           root: _Node, joliet: bool) -> bytes:
        b = bytearray(SECTOR)
        b[0] = vd_type                 # 0: type (1=PVD, 2=SVD)
        b[1:6] = b"CD001"              # 1-5: standard identifier
        b[6] = 0x01                    # 6: version

        def put_str(off, s, length, ucs2=False):
            if ucs2:
                enc = s.encode("utf-16-be")[:length]
                enc = enc + b"\x00" * (length - len(enc))
            else:
                enc = s.encode("ascii")[:length]
                enc = enc + b" " * (length - len(enc))
            b[off:off + length] = enc

        # System identifier (8-39) and Volume identifier (40-71).
        if joliet:
            put_str(8, "", 32, ucs2=True)
            put_str(40, vol_id, 32, ucs2=True)
        else:
            put_str(8, "", 32)
            put_str(40, vol_id, 32)

        # 80-87: volume space size (both-endian) in logical blocks.
        b[80:88] = _both_endian_32(total_sectors)

        if joliet:
            # 88-119: escape sequences. %/E = UCS-2 Level 3.
            esc = b"%/E"
            b[88:88 + len(esc)] = esc

        b[120:124] = _both_endian_16(1)   # volume set size
        b[124:128] = _both_endian_16(1)   # volume sequence number
        b[128:132] = _both_endian_16(SECTOR)  # logical block size (both-endian)
        b[132:140] = _both_endian_32(pt_size) # path table size (both-endian)

        # Path table locations.
        b[140:144] = struct.pack("<I", l_pt_lba)   # L-path table LBA (LE)
        b[144:148] = struct.pack("<I", 0)          # optional L-path table = 0
        b[148:152] = struct.pack(">I", m_pt_lba)   # M-path table LBA (BE)
        b[152:156] = struct.pack(">I", 0)          # optional M-path table = 0

        # 156-189: root directory record (34 bytes).
        dt = _dir_datetime()
        if joliet:
            root_lba = root._jol_extent_lba
            root_len = root._jol_dir_len
        else:
            root_lba = root.extent_lba
            root_len = root._iso_dir_len
        root_rec = _dir_record(b"", root_lba, root_len, True, dt, special=0)
        # Root record identifier is a single 0 byte, length 34.
        b[156:156 + len(root_rec)] = root_rec

        # Volume set / publisher / preparer / application identifiers (fill spaces
        # or UCS-2 spaces). Offsets 190-318 area.
        def fill_id(off, length):
            if joliet:
                b[off:off + length] = (" " * (length // 2)).encode("utf-16-be")
            else:
                b[off:off + length] = b" " * length
        fill_id(190, 128)   # volume set identifier
        fill_id(318, 128)   # publisher identifier
        fill_id(446, 128)   # data preparer identifier
        fill_id(574, 128)   # application identifier

        # Dates (813-829 etc.): creation, modification = now; expiration,
        # effective = "0000...". dec-datetime is 17 bytes each.
        now = _dec_datetime()
        zero_dt = b"0" * 16 + bytes([0])
        b[813:830] = now         # volume creation date/time
        b[830:847] = now         # volume modification date/time
        b[847:864] = zero_dt     # volume expiration date/time
        b[864:881] = zero_dt     # volume effective date/time
        b[881] = 0x01            # file structure version
        return bytes(b)

    # -- path table -----------------------------------------------------------
    def _path_table(self, all_dirs: List[_Node], root: _Node, joliet: bool,
                    big_endian: bool) -> bytes:
        out = bytearray()
        for d in all_dirs:
            if d is root:
                id_bytes = b"\x00"
            else:
                id_bytes = (d.joliet_name.encode("utf-16-be") if joliet
                            else d.iso_name.encode("ascii"))
            di_len = len(id_bytes)
            if joliet:
                ext_lba = d._jol_extent_lba
            else:
                ext_lba = d.extent_lba
            parent_index = 1 if d is root else (
                d.parent.path_index_joliet if joliet else d.parent.path_index_iso)
            if big_endian:
                rec = bytes([di_len, 0]) + struct.pack(">I", ext_lba) + struct.pack(">H", parent_index)
            else:
                rec = bytes([di_len, 0]) + struct.pack("<I", ext_lba) + struct.pack("<H", parent_index)
            rec += id_bytes
            if di_len % 2:
                rec += b"\x00"
            out += rec
        return bytes(out)

    # -- directory extent -----------------------------------------------------
    def _directory_extent(self, node: _Node, joliet: bool) -> bytes:
        dt = _dir_datetime()
        if joliet:
            self_lba = node._jol_extent_lba
            self_len = node._jol_dir_len
        else:
            self_lba = node.extent_lba
            self_len = node._iso_dir_len
        parent = node.parent or node
        if joliet:
            par_lba = parent._jol_extent_lba
            par_len = parent._jol_dir_len
        else:
            par_lba = parent.extent_lba
            par_len = parent._iso_dir_len

        out = bytearray()
        out += _dir_record(b"", self_lba, self_len, True, dt, special=0)   # '.'
        out += _dir_record(b"", par_lba, par_len, True, dt, special=1)     # '..'
        for child in node.children:
            if joliet:
                nb = child.joliet_name.encode("utf-16-be")
                ext_lba = child._jol_extent_lba if child.is_dir else child.extent_lba
                dlen = child._jol_dir_len if child.is_dir else child.data_len
            else:
                nb = child.iso_name.encode("ascii")
                ext_lba = child.extent_lba
                dlen = child._iso_dir_len if child.is_dir else child.data_len
            rec = _dir_record(nb, ext_lba, dlen, child.is_dir, dt)
            # A directory record may not cross a sector boundary; pad if needed.
            if (len(out) % SECTOR) + len(rec) > SECTOR:
                out += b"\x00" * (SECTOR - (len(out) % SECTOR))
            out += rec
        return bytes(out)
