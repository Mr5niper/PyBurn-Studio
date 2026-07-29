from __future__ import annotations
import os
from pathlib import Path
from typing import Iterable, List, Optional
from PyQt6.QtWidgets import (
    QListWidget, QListWidgetItem, QWidget, QVBoxLayout, QProgressBar, QLabel,
    QTableWidget, QTableWidgetItem, QHBoxLayout, QPushButton, QMessageBox, QHeaderView, QFileDialog
)
from PyQt6.QtCore import QMimeData, pyqtSignal, Qt, QTimer, QUrl
from PyQt6.QtGui import QDragEnterEvent, QDropEvent, QDesktopServices
from ..core.history import HistoryStore, HistoryEntry
from datetime import datetime
from PyQt6.QtWidgets import QTreeWidget, QTreeWidgetItem, QAbstractItemView, QMenu


# Roles stored on each disc-tree item.
_ROLE_IS_DIR = Qt.ItemDataRole.UserRole + 1
_ROLE_SRC = Qt.ItemDataRole.UserRole + 2


class DiscTreeWidget(QTreeWidget):
    """A folder-tree view of the disc being composed.

    Each item is either a folder (may hold children, no source) or a file (a leaf
    with a source path on disk). Item text is the ON-DISC name. Supports:
      - dropping files/folders from the OS file manager into the root or a folder,
      - moving items between folders by dragging inside the tree,
      - exporting a description consumable by ISOBuilder.build_tree().
    This widget only composes a layout; it never burns anything.
    """
    changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHeaderHidden(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        # We manage internal moves ourselves (see startDrag/dropEvent), so use
        # DragDrop (not InternalMove) and never let Qt's model perform the move,
        # which was removing the source row and making dragged items vanish.
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDropIndicatorShown(True)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._context_menu)
        self._clipboard = []      # copied disc-node descriptions for paste
        self._dragging = []       # items currently being dragged (internal move)
        self._sorting = False     # guard so re-sort does not recurse via signals
        # Re-sort when an item is renamed via the inline editor.
        self.itemChanged.connect(self._on_item_changed)
        # Give rows enough height that the inline rename editor is not clipped.
        self.setUniformRowHeights(True)
        self._row_height = 24

    # -- item helpers ---------------------------------------------------------
    @staticmethod
    def _is_dir(item: QTreeWidgetItem) -> bool:
        return bool(item.data(0, _ROLE_IS_DIR))

    def _make_item(self, name: str, is_dir: bool, src: str | None) -> QTreeWidgetItem:
        it = QTreeWidgetItem([name])
        it.setData(0, _ROLE_IS_DIR, is_dir)
        it.setData(0, _ROLE_SRC, src)
        flags = Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsDragEnabled | Qt.ItemFlag.ItemIsEditable
        if is_dir:
            flags |= Qt.ItemFlag.ItemIsDropEnabled
        it.setFlags(flags)
        from PyQt6.QtCore import QSize
        it.setSizeHint(0, QSize(0, self._row_height))
        return it

    def _existing_names(self, parent: QTreeWidgetItem | None) -> set:
        names = set()
        if parent is None:
            for i in range(self.topLevelItemCount()):
                names.add(self.topLevelItem(i).text(0).lower())
        else:
            for i in range(parent.childCount()):
                names.add(parent.child(i).text(0).lower())
        return names

    def _unique_name(self, base: str, parent: QTreeWidgetItem | None) -> str:
        existing = self._existing_names(parent)
        if base.lower() not in existing:
            return base
        stem, dot, ext = base.partition(".")
        n = 1
        while True:
            cand = f"{stem} ({n}){dot}{ext}" if dot else f"{stem} ({n})"
            if cand.lower() not in existing:
                return cand
            n += 1

    def _add_disk_path(self, parent: QTreeWidgetItem | None, path: Path):
        """Add a real file or folder (recursively) under parent (or root)."""
        name = self._unique_name(path.name or str(path), parent)
        if path.is_dir():
            folder = self._make_item(name, True, None)
            self._append(parent, folder)
            try:
                for child in sorted(path.iterdir(), key=lambda x: x.name.lower()):
                    self._add_disk_path(folder, child)
            except Exception:
                pass
        elif path.is_file():
            self._append(parent, self._make_item(name, False, str(path)))

    def _append(self, parent: QTreeWidgetItem | None, item: QTreeWidgetItem):
        if parent is None:
            self.addTopLevelItem(item)
        else:
            parent.addChild(item)
        # Do NOT auto-expand; let the user open folders themselves.

    def _sort_key(self, item: QTreeWidgetItem):
        # Folders first (0), then files (1); then case-insensitive name.
        return (0 if self._is_dir(item) else 1, item.text(0).lower())

    def _sort_container(self, parent: QTreeWidgetItem | None):
        """Re-sort the direct children of parent (or the top level) in place:
        folders first, then files, alphabetical within each group. Recurses into
        subfolders. Preserves expansion state and the current selection."""
        if parent is None:
            items = [self.takeTopLevelItem(0) for _ in range(self.topLevelItemCount())]
            items.sort(key=self._sort_key)
            for it in items:
                self.addTopLevelItem(it)
            for it in items:
                if self._is_dir(it):
                    self._sort_container(it)
        else:
            expanded = parent.isExpanded()
            children = [parent.takeChild(0) for _ in range(parent.childCount())]
            children.sort(key=self._sort_key)
            for c in children:
                parent.addChild(c)
            for c in children:
                if self._is_dir(c):
                    self._sort_container(c)
            parent.setExpanded(expanded)

    def _resort(self):
        """Re-sort the entire tree, preserving expansion and selection."""
        if self._sorting:
            return
        self._sorting = True
        try:
            cur = self.currentItem()
            self._sort_container(None)
            if cur is not None:
                self.setCurrentItem(cur)
        finally:
            self._sorting = False

    def _on_item_changed(self, item, column):
        # A rename (inline edit) changed the text; keep ordering correct.
        if self._sorting:
            return
        self._resort()
        self.changed.emit()

    # -- public composition API ----------------------------------------------
    def add_files(self, paths: list[str], parent: QTreeWidgetItem | None = None):
        for p in paths:
            self._add_disk_path(parent, Path(p))
        self._resort()
        self.changed.emit()

    def add_folder_as_folder(self, path: str, parent: QTreeWidgetItem | None = None):
        self._add_disk_path(parent, Path(path))
        self._resort()
        self.changed.emit()

    def add_folder_contents(self, path: str, parent: QTreeWidgetItem | None = None):
        folder = Path(path)
        try:
            for child in sorted(folder.iterdir(), key=lambda x: x.name.lower()):
                self._add_disk_path(parent, child)
        except Exception:
            pass
        self._resort()
        self.changed.emit()

    def new_folder(self, parent: QTreeWidgetItem | None = None) -> QTreeWidgetItem:
        name = self._unique_name("New Folder", parent)
        it = self._make_item(name, True, None)
        self._append(parent, it)
        self._resort()
        self.changed.emit()
        return it

    def remove_selected(self):
        for it in list(self.selectedItems()):
            parent = it.parent()
            if parent is None:
                idx = self.indexOfTopLevelItem(it)
                if idx >= 0:
                    self.takeTopLevelItem(idx)
            else:
                parent.removeChild(it)
        self.changed.emit()

    def clear_all(self):
        self.clear()
        self.changed.emit()

    def export_tree(self) -> list:
        """Serialize to the ISOBuilder.build_tree() description."""
        def node(item: QTreeWidgetItem):
            if self._is_dir(item):
                return {"name": item.text(0),
                        "children": [node(item.child(i)) for i in range(item.childCount())]}
            return {"name": item.text(0), "src": item.data(0, _ROLE_SRC)}
        return [node(self.topLevelItem(i)) for i in range(self.topLevelItemCount())]

    def total_size(self) -> int:
        """Sum of source file sizes currently in the tree (bytes)."""
        total = 0
        def walk(item: QTreeWidgetItem):
            nonlocal total
            if self._is_dir(item):
                for i in range(item.childCount()):
                    walk(item.child(i))
            else:
                src = item.data(0, _ROLE_SRC)
                try:
                    if src:
                        total += Path(src).stat().st_size
                except Exception:
                    pass
        for i in range(self.topLevelItemCount()):
            walk(self.topLevelItem(i))
        return total

    def is_empty(self) -> bool:
        return self.topLevelItemCount() == 0

    # -- context menu / clipboard --------------------------------------------
    def _context_menu(self, point):
        item = self.itemAt(point)
        menu = QMenu(self)
        act_newfolder = menu.addAction("New Folder")
        act_rename = menu.addAction("Rename")
        act_remove = menu.addAction("Remove")
        menu.addSeparator()
        act_copy = menu.addAction("Copy")
        act_paste = menu.addAction("Paste")
        menu.addSeparator()
        act_open = menu.addAction("Open in Explorer")

        has_sel = len(self.selectedItems()) > 0
        act_rename.setEnabled(item is not None)
        act_remove.setEnabled(has_sel)
        act_copy.setEnabled(has_sel)
        act_paste.setEnabled(bool(self._clipboard))
        # Open in Explorer only makes sense for a node that maps to a real disk
        # path (a file's source, or a folder that came from disk).
        open_path = self._explorer_path(item) if item is not None else None
        act_open.setEnabled(open_path is not None)

        chosen = menu.exec(self.viewport().mapToGlobal(point))
        if chosen is None:
            return
        if chosen is act_newfolder:
            dest = item if (item is not None and self._is_dir(item)) else (item.parent() if item is not None else None)
            it = self.new_folder(dest)
            self.setCurrentItem(it)
            self.editItem(it, 0)
        elif chosen is act_rename and item is not None:
            self.editItem(item, 0)
        elif chosen is act_remove:
            self.remove_selected()
        elif chosen is act_copy:
            self._copy_selected()
        elif chosen is act_paste:
            dest = item if (item is not None and self._is_dir(item)) else (item.parent() if item is not None else None)
            self._paste(dest)
        elif chosen is act_open and open_path is not None:
            self._open_in_explorer(open_path)

    def _explorer_path(self, item: QTreeWidgetItem):
        """Return a real disk path to reveal for this item, or None. Files reveal
        their source; disk-derived folders reveal their source if we can infer
        it from a child file. GUI-created folders have no disk path."""
        if item is None:
            return None
        if not self._is_dir(item):
            src = item.data(0, _ROLE_SRC)
            return src if src else None
        # Folder: try to find a descendant file's source and reveal its folder.
        def first_src(node):
            for i in range(node.childCount()):
                c = node.child(i)
                if self._is_dir(c):
                    r = first_src(c)
                    if r:
                        return r
                else:
                    s = c.data(0, _ROLE_SRC)
                    if s:
                        return s
            return None
        s = first_src(item)
        if s:
            return str(Path(s).parent)
        return None

    def _open_in_explorer(self, path: str):
        import subprocess, sys, os as _os
        p = Path(path)
        try:
            if sys.platform.startswith("win"):
                if p.is_dir():
                    _os.startfile(str(p))  # type: ignore[attr-defined]
                else:
                    subprocess.Popen(["explorer", "/select,", str(p)])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", "-R", str(p)] if p.exists() else ["open", str(p.parent)])
            else:
                target = str(p if p.is_dir() else p.parent)
                subprocess.Popen(["xdg-open", target])
        except Exception:
            pass

    def _node_desc(self, item: QTreeWidgetItem):
        if self._is_dir(item):
            return {"name": item.text(0),
                    "children": [self._node_desc(item.child(i)) for i in range(item.childCount())]}
        return {"name": item.text(0), "src": item.data(0, _ROLE_SRC), "is_dir": False}

    def _copy_selected(self):
        # Copy only top-most selected items (avoid duplicating a child whose
        # parent is also selected). QTreeWidgetItem is not hashable, so compare
        # by identity against the selection list.
        sel = self.selectedItems()
        tops = []
        for it in sel:
            p = it.parent()
            skip = False
            while p is not None:
                if any(p is s for s in sel):
                    skip = True
                    break
                p = p.parent()
            if not skip:
                tops.append(it)
        self._clipboard = [self._node_desc(it) for it in tops]

    def _paste(self, dest: QTreeWidgetItem | None):
        def add_desc(parent, desc):
            name = self._unique_name(desc["name"], parent)
            if desc.get("children") is not None or ("src" not in desc):
                node = self._make_item(name, True, None)
                self._append(parent, node)
                for ch in desc.get("children", []):
                    add_desc(node, ch)
            else:
                node = self._make_item(name, False, desc.get("src"))
                self._append(parent, node)
        for desc in self._clipboard:
            add_desc(dest, desc)
        self._resort()
        self.changed.emit()

    # -- drag & drop ----------------------------------------------------------
    def startDrag(self, supportedActions):
        # Record what is being dragged and run the drag as a Copy action so Qt's
        # view does NOT remove the source rows itself (we do the move in
        # dropEvent). This is what stops dragged items from disappearing.
        self._dragging = list(self.selectedItems())
        from PyQt6.QtGui import QDrag
        from PyQt6.QtCore import QMimeData
        drag = QDrag(self)
        mime = QMimeData()
        mime.setData("application/x-pyburn-disc-node", b"1")
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.MoveAction)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() or e.source() is self:
            e.acceptProposedAction()
        else:
            super().dragEnterEvent(e)

    def dragMoveEvent(self, e):
        if e.mimeData().hasUrls() or e.source() is self:
            # Let the base view compute and paint the drop indicator, then accept
            # so the drop is allowed. Without calling super the indicator line
            # never appears.
            super().dragMoveEvent(e)
            e.acceptProposedAction()
        else:
            super().dragMoveEvent(e)

    def _drop_dest(self, pos):
        """Resolve the destination folder for a drop at pos: a folder under the
        cursor is the destination; a file targets its parent; empty space targets
        the root (None)."""
        target = self.itemAt(pos)
        if target is not None and not self._is_dir(target):
            return target.parent()
        return target

    def dropEvent(self, e):
        md = e.mimeData()
        pos = e.position().toPoint() if hasattr(e, "position") else e.pos()
        dest = self._drop_dest(pos)

        # External drop from the OS file manager.
        if md.hasUrls():
            for url in md.urls():
                p = url.toLocalFile()
                if p:
                    self._add_disk_path(dest, Path(p))
            self._resort()
            self.changed.emit()
            e.acceptProposedAction()
            return

        # Internal move of the recorded dragged items.
        moving = self._dragging or list(self.selectedItems())
        self._dragging = []
        if not moving:
            e.ignore()
            return

        def is_descendant(node, maybe_ancestor):
            p = node.parent()
            while p is not None:
                if p is maybe_ancestor:
                    return True
                p = p.parent()
            return False

        # Reject illegal moves (into self or own descendant); keep the rest.
        valid = []
        for it in moving:
            if dest is it or (dest is not None and is_descendant(dest, it)):
                continue
            valid.append(it)
        if not valid:
            e.ignore()
            return

        last = None
        for it in valid:
            parent = it.parent()
            if parent is None:
                taken = self.takeTopLevelItem(self.indexOfTopLevelItem(it))
            else:
                taken = parent.takeChild(parent.indexOfChild(it))
            taken.setText(0, self._unique_name(taken.text(0), dest))
            if dest is None:
                self.addTopLevelItem(taken)
            else:
                dest.addChild(taken)
                dest.setExpanded(True)
            last = taken
        if last is not None:
            self.setCurrentItem(last)
        self._resort()
        self.changed.emit()
        e.acceptProposedAction()



def compute_total_size(paths: List[str], max_files: int = 50000) -> int:
    total = 0
    file_count = 0
    for p in paths:
        pp = Path(p)
        if pp.is_file():
            try:
                total += pp.stat().st_size
                file_count += 1
            except Exception:
                pass
        elif pp.is_dir():
            for root, _, files in os.walk(pp, followlinks=False):
                for fn in files:
                    if file_count >= max_files:
                        return total
                    fp = Path(root) / fn
                    try:
                        total += fp.stat().st_size
                        file_count += 1
                    except Exception:
                        pass
    return total


def _ffprobe_duration_seconds(ffprobe: str, path: str) -> float:
    """Return the playback duration of one media file in seconds via ffprobe.

    Audio CD capacity is measured in PLAYBACK TIME (about 80 minutes), not file
    bytes. MP3/FLAC/etc. compress the audio, so their byte size is far smaller
    than the uncompressed CD-audio they become, which made a bytes-based gauge
    read far too low. Duration is the correct measure.
    """
    import subprocess
    try:
        creo: dict = {}
        if os.name == "nt":
            creoy = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
            creo = {"creationflags": creoy}
        out = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=30, **creo,
        )
        val = (out.stdout or "").strip()
        return float(val) if val else 0.0
    except Exception:
        return 0.0


def compute_total_duration(paths: List[str], ffprobe: Optional[str]) -> float:
    """Sum playback duration (seconds) across audio files using ffprobe.

    Returns 0.0 if ffprobe is unavailable; the caller can fall back to a
    conservative estimate in that case.
    """
    if not ffprobe:
        return 0.0
    total = 0.0
    for p in paths:
        pp = Path(p)
        if pp.is_file():
            total += _ffprobe_duration_seconds(ffprobe, str(pp))
    return total


# --- DVD-Video fit-to-disc bitrate model ------------------------------------
# A single-layer DVD holds a fixed number of bits; how many MINUTES fit depends
# on the video bitrate. Real DVD authoring picks a bitrate that fills the disc
# for the given runtime (lower bitrate = longer runtime), rather than a fixed
# rate. These constants and helpers are the single source of truth shared by
# the capacity gauge (UI) and the WSL transcode (bitrate actually used).
DVD5_USABLE_BYTES = 4_700_000_000   # single-layer DVD-5
DVD_AUDIO_KBPS = 224                # MP2/AC3 DVD audio
DVD_MUX_OVERHEAD = 0.98             # ~2% for VOB/nav-pack overhead
DVD_VIDEO_MAX_KBPS = 9000           # headroom under the ~9.8 Mbps DVD ceiling
DVD_VIDEO_MIN_KBPS = 2000           # quality floor; sets the practical max runtime


def dvd_video_kbps_for_seconds(seconds: float) -> int:
    """Video bitrate (kbps) that fills a DVD-5 for the given runtime, clamped
    between the quality floor and the DVD ceiling."""
    if seconds <= 0:
        return DVD_VIDEO_MAX_KBPS
    total_kbits = DVD5_USABLE_BYTES * 8 * DVD_MUX_OVERHEAD / 1000.0
    avail_video_kbits = total_kbits - (DVD_AUDIO_KBPS * seconds)
    kbps = avail_video_kbits / seconds if seconds > 0 else DVD_VIDEO_MAX_KBPS
    return max(DVD_VIDEO_MIN_KBPS, min(DVD_VIDEO_MAX_KBPS, int(kbps)))


def dvd_max_minutes() -> float:
    """Runtime at which the video bitrate hits the quality floor. Past this, the
    content will not fit a single-layer DVD at acceptable quality."""
    total_kbits = DVD5_USABLE_BYTES * 8 * DVD_MUX_OVERHEAD / 1000.0
    seconds = total_kbits / (DVD_VIDEO_MIN_KBPS + DVD_AUDIO_KBPS)
    return seconds / 60.0


# --- Blu-ray (BD-25) fit-to-disc bitrate model ------------------------------
# Same idea as DVD, sized for a single-layer 25 GB Blu-ray with H.264 video and
# AC3 audio. The BD path formerly used a fixed CRF (quality-based, so output
# size varied with content and could not map to a time budget); switching to a
# fit-to-disc bitrate makes capacity predictable and lets long content fit by
# lowering the bitrate, exactly like the DVD path.
BD25_USABLE_BYTES = 23_500_000_000   # usable BD-25 (~23.3 GiB), conservative
BD_AUDIO_KBPS = 192                  # AC3 audio
BD_MUX_OVERHEAD = 0.97               # BDMV/m2ts overhead
BD_VIDEO_MAX_KBPS = 35000            # headroom under the ~40 Mbps BD ceiling
BD_VIDEO_MIN_KBPS = 6000             # H.264 1080p quality floor; sets max runtime


def bd_video_kbps_for_seconds(seconds: float) -> int:
    """Video bitrate (kbps) that fills a BD-25 for the given runtime, clamped
    between the quality floor and the BD ceiling."""
    if seconds <= 0:
        return BD_VIDEO_MAX_KBPS
    total_kbits = BD25_USABLE_BYTES * 8 * BD_MUX_OVERHEAD / 1000.0
    avail_video_kbits = total_kbits - (BD_AUDIO_KBPS * seconds)
    kbps = avail_video_kbits / seconds if seconds > 0 else BD_VIDEO_MAX_KBPS
    return max(BD_VIDEO_MIN_KBPS, min(BD_VIDEO_MAX_KBPS, int(kbps)))


def bd_max_minutes() -> float:
    """Runtime at which the BD video bitrate hits the quality floor."""
    total_kbits = BD25_USABLE_BYTES * 8 * BD_MUX_OVERHEAD / 1000.0
    seconds = total_kbits / (BD_VIDEO_MIN_KBPS + BD_AUDIO_KBPS)
    return seconds / 60.0


class FileListWidget(QListWidget):
    files_changed = pyqtSignal(list)

    def __init__(self, allow_dirs: bool = True, exts: Iterable[str] | None = None):
        super().__init__()
        self.setAcceptDrops(True)
        self.exts = set(e.lower() for e in (exts or []))
        self.allow_dirs = allow_dirs
        self._paths_set = set()

    def add_path(self, p: str):
        try:
            normalized = str(Path(p).resolve())
        except Exception:
            normalized = p
        if normalized in self._paths_set:
            return
        if os.path.isdir(p):
            if not self.allow_dirs:
                return
        else:
            if self.exts:
                ext = Path(p).suffix.lower().lstrip(".")
                if ext and ext not in self.exts:
                    return
        self.addItem(QListWidgetItem(p))
        self._paths_set.add(normalized)
        self.files_changed.emit(self.get_file_list())

    def dragEnterEvent(self, e: QDragEnterEvent):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
        else:
            e.ignore()

    def dropEvent(self, e: QDropEvent):
        md: QMimeData = e.mimeData()
        if md.hasUrls():
            for url in md.urls():
                p = url.toLocalFile()
                if os.path.exists(p):
                    self.add_path(p)
        e.acceptProposedAction()

    def takeItem(self, row):
        it = self.item(row)
        if it:
            try:
                normalized = str(Path(it.text()).resolve())
                self._paths_set.discard(normalized)
            except Exception:
                pass
        res = super().takeItem(row)
        self.files_changed.emit(self.get_file_list())
        return res

    def clear(self):
        super().clear()
        self._paths_set.clear()
        self.files_changed.emit(self.get_file_list())

    def get_file_list(self) -> List[str]:
        return [self.item(i).text() for i in range(self.count())]


class CapacityGauge(QWidget):
    def __init__(self, max_capacity_bytes: int, mode: str = "bytes", max_minutes: float = 80.0):
        super().__init__()
        self.max_capacity = max_capacity_bytes
        self.current_size = 0
        # mode "bytes" -> data/video discs measured by size.
        # mode "minutes" -> audio CD measured by playback time.
        self.mode = mode
        self.max_minutes = max_minutes
        self.current_seconds = 0.0
        lay = QVBoxLayout(self)
        self.lbl = QLabel("")
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setTextVisible(True)
        lay.addWidget(self.lbl)
        lay.addWidget(self.bar)
        if self.mode == "minutes":
            self.update_duration(0.0)
        else:
            self.update_size(0)

    def _human(self, n: int) -> str:
        units = ["B", "KB", "MB", "GB", "TB"]
        v = float(n)
        i = 0
        while v >= 1024 and i < len(units) - 1:
            v /= 1024.0
            i += 1
        return f"{v:.2f} {units[i]}"

    def _fmt_mmss(self, seconds: float) -> str:
        s = int(round(seconds))
        return f"{s // 60}:{s % 60:02d}"

    def update_size(self, size_bytes: int):
        self.current_size = size_bytes
        pct = int((size_bytes / self.max_capacity) * 100) if self.max_capacity > 0 else 0
        pct = max(0, min(100, pct))
        self.bar.setValue(pct)
        self.lbl.setText(f"{self._human(size_bytes)} / {self._human(self.max_capacity)} ({pct}%)")
        color = "#E74C3C" if size_bytes > self.max_capacity else "#2ECC71"
        self.bar.setStyleSheet(f"""
            QProgressBar {{ border: 1px solid #5e81ac; border-radius: 4px; background:#3b4252; color: white; }}
            QProgressBar::chunk {{ background-color:{color}; }}
        """)

    def update_duration(self, seconds: float):
        """Audio CD gauge: show total playback time against the disc's minutes."""
        self.current_seconds = seconds
        total_cap_seconds = self.max_minutes * 60.0
        pct = int((seconds / total_cap_seconds) * 100) if total_cap_seconds > 0 else 0
        pct = max(0, min(100, pct))
        self.bar.setValue(pct)
        over = seconds > total_cap_seconds
        self.lbl.setText(
            f"{self._fmt_mmss(seconds)} / {int(self.max_minutes)}:00 "
            f"minutes ({pct}%)" + ("  OVER CAPACITY" if over else "")
        )
        color = "#E74C3C" if over else "#2ECC71"
        self.bar.setStyleSheet(f"""
            QProgressBar {{ border: 1px solid #5e81ac; border-radius: 4px; background:#3b4252; color: white; }}
            QProgressBar::chunk {{ background-color:{color}; }}
        """)


class JobQueueWidget(QWidget):
    def __init__(self, service):
        super().__init__()
        self.service = service
        lay = QVBoxLayout(self)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Job", "Device", "Progress", "Status"])
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        lay.addWidget(self.table)
        btn_row = QHBoxLayout()
        self.btn_cancel = QPushButton("Cancel Current")
        self.btn_cancel.clicked.connect(self.service.cancel_current)
        self.btn_remove = QPushButton("Remove Selected (Queued)")
        self.btn_remove.clicked.connect(self._remove_selected)
        btn_row.addWidget(self.btn_cancel)
        btn_row.addWidget(self.btn_remove)
        btn_row.addStretch()
        lay.addLayout(btn_row)
        self.service.sig_queue_updated.connect(self.refresh)
        self.service.sig_status_update.connect(self._status_update)
        self.service.sig_job_started.connect(lambda _id: self.refresh())
        self.service.sig_job_finished.connect(lambda _id, ok, msg: self.refresh())
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(400)
        self.refresh()

    def refresh(self):
        jobs = self.service.get_list()
        self.table.setRowCount(len(jobs))
        for i, job in enumerate(jobs):
            self.table.setItem(i, 0, QTableWidgetItem(job.display_name))
            self.table.setItem(i, 1, QTableWidgetItem(job.device))
            pb = QProgressBar()
            pb.setValue(job.progress)
            pb.setStyleSheet("QProgressBar { background:#4c566a; border:none; } QProgressBar::chunk { background:#a3be8c; }")
            self.table.setCellWidget(i, 2, pb)
            self.table.setItem(i, 3, QTableWidgetItem(job.status))
        self.btn_cancel.setEnabled(bool(len(jobs)) and jobs[0].status == "RUNNING")

    def _status_update(self, job_id: str, status: str, progress: int):
        jobs = self.service.get_list()
        for i, job in enumerate(jobs):
            if job.id == job_id:
                self.table.item(i, 3).setText(status)
                w = self.table.cellWidget(i, 2)
                if isinstance(w, QProgressBar):
                    w.setValue(progress)

    def _remove_selected(self):
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.information(self, "Remove", "Select a queued job to remove.")
            return
        jobs = self.service.get_list()
        if row >= len(jobs):
            return
        job = jobs[row]
        if job.status == "RUNNING":
            QMessageBox.warning(self, "Remove", "Cannot remove the currently running job.")
            return
        self.service.remove(job.id)

    def _tick(self):
        jobs = self.service.get_list()
        self.btn_cancel.setEnabled(bool(len(jobs)) and jobs[0].status == "RUNNING")
        for i, job in enumerate(jobs):
            w = self.table.cellWidget(i, 2)
            if isinstance(w, QProgressBar):
                w.setValue(job.progress)
            item = self.table.item(i, 3)
            if item:
                item.setText(job.status)


class HistoryWidget(QWidget):
    def __init__(self, history: HistoryStore, queue):
        super().__init__()
        self.history = history
        self.queue = queue
        lay = QVBoxLayout(self)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Finished", "Job", "Device", "Success", "Message", "Log"])
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        lay.addWidget(self.table)
        btn_row = QHBoxLayout()
        self.btn_show = QPushButton("Show Log")
        self.btn_show.clicked.connect(self._show_log)
        self.btn_export = QPushButton("Export Log...")
        self.btn_export.clicked.connect(self._export_log)
        self.btn_retry = QPushButton("Retry Selected")
        self.btn_retry.clicked.connect(self._retry)
        btn_row.addWidget(self.btn_show)
        btn_row.addWidget(self.btn_export)
        btn_row.addWidget(self.btn_retry)
        btn_row.addStretch()
        lay.addLayout(btn_row)
        # Keep the history view in sync as jobs complete.
        self.queue.sig_job_finished.connect(lambda _id, ok, msg: self.refresh())
        self.refresh()

    def _parse_dt(self, s: str):
        try:
            return datetime.fromisoformat(s)
        except Exception:
            return datetime.min

    def refresh(self):
        entries = sorted(self.history.all(), key=lambda e: self._parse_dt(e.finished_at), reverse=True)
        self.table.setRowCount(len(entries))
        for i, e in enumerate(entries):
            self.table.setItem(i, 0, QTableWidgetItem(e.finished_at))
            self.table.setItem(i, 1, QTableWidgetItem(e.job_type))
            self.table.setItem(i, 2, QTableWidgetItem(e.device))
            self.table.setItem(i, 3, QTableWidgetItem("Yes" if e.success else "No"))
            self.table.setItem(i, 4, QTableWidgetItem(e.message))
            self.table.setItem(i, 5, QTableWidgetItem(e.log_file or ""))

    def _selected_entry(self) -> Optional[HistoryEntry]:
        row = self.table.currentRow()
        if row < 0:
            return None
        entries = sorted(self.history.all(), key=lambda e: self._parse_dt(e.finished_at), reverse=True)
        if row >= len(entries):
            return None
        return entries[row]

    def _show_log(self):
        e = self._selected_entry()
        if not e or not e.log_file or not Path(e.log_file).exists():
            QMessageBox.information(self, "Show Log", "No log available.")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(e.log_file))

    def _export_log(self):
        e = self._selected_entry()
        if not e or not e.log_file or not Path(e.log_file).exists():
            QMessageBox.information(self, "Export Log", "No log available.")
            return
        dest, _ = QFileDialog.getSaveFileName(self, "Save Log As", Path.home().as_posix() + "/pyburn.log", "Log Files (*.log);;All Files (*)")
        if dest:
            try:
                Path(dest).write_text(Path(e.log_file).read_text(encoding="utf-8"), encoding="utf-8")
                QMessageBox.information(self, "Export Log", f"Log saved to {dest}")
            except Exception as ex:
                QMessageBox.warning(self, "Export Log", f"Failed to save: {ex}")

    def _retry(self):
        e = self._selected_entry()
        if not e:
            QMessageBox.information(self, "Retry", "Select a job to retry.")
            return
        self.queue.retry(e)
        QMessageBox.information(self, "Retry", "Job re-enqueued.")
