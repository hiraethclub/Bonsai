"""Two level tree model: categories with tri-state ticks, channels with ticks.

A custom model rather than QStandardItemModel so several thousand channels
load instantly: rows are only materialised when a category is expanded.
The search filter is applied inside the model; ticking a category ticks
only its currently visible channels.
"""

from __future__ import annotations

from collections.abc import Iterable

from PySide6.QtCore import QAbstractItemModel, QModelIndex, Qt, Signal
from PySide6.QtGui import QFont

from ..models import Category, Channel

COL_NAME, COL_TICKED, COL_ID = range(3)
HEADERS = ("Name", "Ticked", "Stream ID")


class _CatNode:
    __slots__ = ("cat", "channels", "visible", "row", "ticked", "child_ref")

    def __init__(self, cat: Category, channels: list[Channel]) -> None:
        self.cat = cat
        self.channels = channels
        self.visible = channels
        self.row = 0
        self.ticked = 0  # ticked channels in this category (all, not only visible)
        self.child_ref = _ChildRef(self)


class _ChildRef:
    """internalPointer for channel rows; points back at the parent category."""

    __slots__ = ("node",)

    def __init__(self, node: _CatNode) -> None:
        self.node = node


def _is_checked(value) -> bool:
    if isinstance(value, Qt.CheckState):
        return value == Qt.CheckState.Checked
    return value == Qt.CheckState.Checked.value


class ChannelTreeModel(QAbstractItemModel):
    tickedChanged = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._groups: list[_CatNode] = []
        self._visible: list[_CatNode] = []
        self._node_of: dict[int, _CatNode] = {}
        self._ticked: set[int] = set()
        self._filter = ""
        self._bold = QFont()
        self._bold.setBold(True)

    # -- loading and filtering ---------------------------------------------

    def set_groups(self, groups: Iterable[tuple[Category, list[Channel]]]) -> None:
        """Replace the contents. Ticks on channels that still exist are kept."""
        self.beginResetModel()
        self._groups = [_CatNode(cat, list(chans)) for cat, chans in groups if chans]
        self._node_of = {ch.stream_id: n for n in self._groups for ch in n.channels}
        self._ticked &= self._node_of.keys()
        self._recount()
        self._apply_filter()
        self.endResetModel()
        self.tickedChanged.emit()

    def set_filter(self, text: str) -> None:
        text = text.strip().casefold()
        if text == self._filter:
            return
        self.beginResetModel()
        self._filter = text
        self._apply_filter()
        self.endResetModel()

    def _apply_filter(self) -> None:
        f = self._filter
        for node in self._groups:
            if not f or f in node.cat.name.casefold():
                node.visible = node.channels
            else:
                node.visible = [ch for ch in node.channels if f in ch.name.casefold()]
        self._visible = [n for n in self._groups if n.visible]
        for row, node in enumerate(self._visible):
            node.row = row

    def _recount(self) -> None:
        for node in self._groups:
            node.ticked = sum(1 for ch in node.channels if ch.stream_id in self._ticked)

    # -- queries -------------------------------------------------------------

    @property
    def is_filtered(self) -> bool:
        return bool(self._filter)

    def groups(self) -> list[tuple[Category, list[Channel]]]:
        """All categories and channels, unfiltered, in provider order."""
        return [(n.cat, n.channels) for n in self._groups]

    def ticked_ids(self) -> frozenset[int]:
        return frozenset(self._ticked)

    def total_channels(self) -> int:
        return len(self._node_of)

    def visible_channel_count(self) -> int:
        return sum(len(n.visible) for n in self._visible)

    def ticked_summary(self) -> tuple[int, int]:
        """(ticked channels, categories with at least one ticked channel)."""
        return len(self._ticked), sum(1 for n in self._groups if n.ticked)

    # -- ticking -------------------------------------------------------------

    def set_ticked(self, stream_ids: Iterable[int], ticked: bool) -> None:
        changed: set[_CatNode] = set()
        for sid in stream_ids:
            node = self._node_of.get(sid)
            if node is None or (sid in self._ticked) == ticked:
                continue
            if ticked:
                self._ticked.add(sid)
                node.ticked += 1
            else:
                self._ticked.discard(sid)
                node.ticked -= 1
            changed.add(node)
        if changed:
            self._emit_changed(changed)
            self.tickedChanged.emit()

    def set_ticked_for_indexes(self, indexes: Iterable[QModelIndex], ticked: bool) -> None:
        """Tick/untick the rows behind the given indexes (categories mean their visible channels)."""
        ids: set[int] = set()
        for idx in indexes:
            ptr = idx.internalPointer() if idx.isValid() else None
            if isinstance(ptr, _CatNode):
                ids.update(ch.stream_id for ch in ptr.visible)
            elif isinstance(ptr, _ChildRef):
                ids.add(ptr.node.visible[idx.row()].stream_id)
        self.set_ticked(ids, ticked)

    def clear_ticked(self) -> None:
        self.set_ticked(list(self._ticked), False)

    def _emit_changed(self, nodes: Iterable[_CatNode]) -> None:
        last_col = len(HEADERS) - 1
        for node in nodes:
            if node.visible and self._visible and self._visible[node.row] is node:
                self.dataChanged.emit(self.index(node.row, 0), self.index(node.row, last_col))
                parent = self.index(node.row, 0)
                self.dataChanged.emit(
                    self.index(0, 0, parent), self.index(len(node.visible) - 1, last_col, parent)
                )

    def _cat_state(self, node: _CatNode) -> Qt.CheckState:
        if node.visible is node.channels:
            ticked, total = node.ticked, len(node.channels)
        else:
            ticked = sum(1 for ch in node.visible if ch.stream_id in self._ticked)
            total = len(node.visible)
        if ticked == 0:
            return Qt.CheckState.Unchecked
        if ticked == total:
            return Qt.CheckState.Checked
        return Qt.CheckState.PartiallyChecked

    # -- QAbstractItemModel ----------------------------------------------------

    def index(self, row: int, column: int, parent=QModelIndex()) -> QModelIndex:
        if not self.hasIndex(row, column, parent):
            return QModelIndex()
        if not parent.isValid():
            return self.createIndex(row, column, self._visible[row])
        node = parent.internalPointer()
        if isinstance(node, _CatNode):
            return self.createIndex(row, column, node.child_ref)
        return QModelIndex()

    def parent(self, index=QModelIndex()) -> QModelIndex:
        if not index.isValid():
            return QModelIndex()
        ptr = index.internalPointer()
        if isinstance(ptr, _ChildRef):
            return self.createIndex(ptr.node.row, 0, ptr.node)
        return QModelIndex()

    def rowCount(self, parent=QModelIndex()) -> int:
        if not parent.isValid():
            return len(self._visible)
        if parent.column() != 0:
            return 0
        ptr = parent.internalPointer()
        return len(ptr.visible) if isinstance(ptr, _CatNode) else 0

    def columnCount(self, parent=QModelIndex()) -> int:
        return len(HEADERS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return HEADERS[section]
        return None

    def flags(self, index):
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        f = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if index.column() == COL_NAME:
            f |= Qt.ItemFlag.ItemIsUserCheckable
        return f

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        ptr = index.internalPointer()
        col = index.column()
        if isinstance(ptr, _CatNode):
            node = ptr
            if role == Qt.ItemDataRole.DisplayRole:
                if col == COL_NAME:
                    return node.cat.name
                if col == COL_TICKED:
                    return f"{node.ticked} / {len(node.channels)}"
            elif role == Qt.ItemDataRole.CheckStateRole and col == COL_NAME:
                return self._cat_state(node)
            elif role == Qt.ItemDataRole.FontRole:
                return self._bold
            elif role == Qt.ItemDataRole.ToolTipRole and col == COL_NAME:
                return f"{node.cat.name} — {len(node.channels)} channels"
            return None

        ch = ptr.node.visible[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            if col == COL_NAME:
                return ch.name
            if col == COL_ID:
                return str(ch.stream_id)
        elif role == Qt.ItemDataRole.CheckStateRole and col == COL_NAME:
            return Qt.CheckState.Checked if ch.stream_id in self._ticked else Qt.CheckState.Unchecked
        elif role == Qt.ItemDataRole.ToolTipRole and col == COL_NAME:
            return ch.name
        return None

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole) -> bool:
        if role != Qt.ItemDataRole.CheckStateRole or not index.isValid():
            return False
        self.set_ticked_for_indexes([index], _is_checked(value))
        return True

