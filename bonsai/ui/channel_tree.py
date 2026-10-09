"""Two level tree model: categories with tri-state ticks, channels with ticks.

A custom model rather than QStandardItemModel so several thousand channels
load instantly: rows are only materialised when a category is expanded.

Two kinds of filter are applied inside the model:
- the tier, label and region filters decide which channels are eligible at
  all. Hidden channels are left out of the view, the counts and the export,
  but their ticks are remembered in case they are shown again;
- the search filter only narrows what is on screen. Ticking a category
  ticks only its currently visible channels.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable

from PySide6.QtCore import QAbstractItemModel, QModelIndex, Qt, Signal
from PySide6.QtGui import QFont

from ..filters import DEFAULT_HIDDEN_FLAGS, Flag, channel_flags, flags_from_name, region_of
from ..models import Category, Channel
from ..quality import DEFAULT_HIDDEN_TIERS, Tier, classify, normalise_name, tier_from_name

COL_NAME, COL_TIER, COL_LABELS, COL_TICKED, COL_ID = range(5)
HEADERS = ("Name", "Tier", "Labels", "Ticked", "Stream ID")


class _CatNode:
    __slots__ = ("cat", "key", "tier", "flags", "region", "channels", "shown", "visible", "row", "ticked", "child_ref")

    def __init__(self, cat: Category, channels: list[Channel]) -> None:
        self.cat = cat
        self.key = normalise_name(cat.name).casefold()  # for searching
        self.tier = tier_from_name(cat.name)
        self.flags = frozenset(flags_from_name(cat.name))
        self.region = region_of(cat.name)
        self.channels = channels  # everything the provider lists
        self.shown = channels  # not hidden by the tier/label/region filters
        self.visible = channels  # shown and matching the search
        self.row = 0
        self.ticked = 0  # ticked channels among `shown`
        self.child_ref = _ChildRef(self)


class _ChildRef:
    """internalPointer for channel rows; points back at the parent category."""

    __slots__ = ("node",)

    def __init__(self, node: _CatNode) -> None:
        self.node = node


def _labels(flags) -> str:
    return " ".join(f.short for f in Flag if f in flags and f.short)


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
        self._tier: dict[int, Tier] = {}
        self._inherited: set[int] = set()  # tier came from the category name, not the channel's
        self._key: dict[int, str] = {}  # normalised, casefolded channel names for searching
        self._flags: dict[int, frozenset[Flag]] = {}
        self._italic = QFont()
        self._italic.setItalic(True)
        self._ticked: set[int] = set()
        self._hidden_tiers: frozenset[Tier] = DEFAULT_HIDDEN_TIERS
        self._hidden_flags: frozenset[Flag] = DEFAULT_HIDDEN_FLAGS
        self._hidden_regions: frozenset[str] = frozenset()
        self._filter = ""
        self._bold = QFont()
        self._bold.setBold(True)

    # -- loading and filtering ---------------------------------------------

    def set_groups(self, groups: Iterable[tuple[Category, list[Channel]]]) -> None:
        """Replace the contents. Ticks on channels that still exist are kept."""
        self.beginResetModel()
        self._groups = [_CatNode(cat, list(chans)) for cat, chans in groups if chans]
        self._node_of = {ch.stream_id: n for n in self._groups for ch in n.channels}
        self._tier, self._inherited, self._key, self._flags = {}, set(), {}, {}
        for node in self._groups:
            for ch in node.channels:
                self._flags[ch.stream_id] = channel_flags(
                    ch.name, node.flags, has_epg=bool(ch.epg_channel_id), is_adult=ch.is_adult
                )
                tier, inherited = classify(ch.name, node.tier)
                self._tier[ch.stream_id] = tier
                if inherited:
                    self._inherited.add(ch.stream_id)
                self._key[ch.stream_id] = normalise_name(ch.name).casefold()
        self._ticked &= self._node_of.keys()
        self._refilter()
        self.endResetModel()
        self.tickedChanged.emit()

    def set_hidden_tiers(self, tiers: Iterable[Tier]) -> None:
        self._set_hidden("_hidden_tiers", tiers)

    def set_hidden_flags(self, flags: Iterable[Flag]) -> None:
        self._set_hidden("_hidden_flags", flags)

    def set_hidden_regions(self, regions: Iterable[str]) -> None:
        self._set_hidden("_hidden_regions", regions)

    def _set_hidden(self, attr: str, values) -> None:
        values = frozenset(values)
        if values == getattr(self, attr):
            return
        self.beginResetModel()
        setattr(self, attr, values)
        self._refilter()
        self.endResetModel()
        self.tickedChanged.emit()

    def set_filter(self, text: str) -> None:
        text = normalise_name(text.strip()).casefold()
        if text == self._filter:
            return
        self.beginResetModel()
        self._filter = text
        self._apply_search()
        self.endResetModel()

    def _refilter(self) -> None:
        tiers, flags = self._hidden_tiers, self._hidden_flags
        for node in self._groups:
            if node.region in self._hidden_regions:
                node.shown = []
            elif tiers or flags:
                node.shown = [
                    ch
                    for ch in node.channels
                    if self._tier[ch.stream_id] not in tiers and not (self._flags[ch.stream_id] & flags)
                ]
            else:
                node.shown = node.channels
            node.ticked = sum(1 for ch in node.shown if ch.stream_id in self._ticked)
        self._apply_search()

    def _apply_search(self) -> None:
        f = self._filter
        key = self._key
        for node in self._groups:
            if not f or f in node.key:
                node.visible = node.shown
            else:
                node.visible = [ch for ch in node.shown if f in key[ch.stream_id]]
        self._visible = [n for n in self._groups if n.visible]
        for row, node in enumerate(self._visible):
            node.row = row

    def _eligible(self, stream_id: int) -> bool:
        node = self._node_of.get(stream_id)
        return (
            node is not None
            and node.region not in self._hidden_regions
            and self._tier[stream_id] not in self._hidden_tiers
            and not (self._flags[stream_id] & self._hidden_flags)
        )

    # -- queries -------------------------------------------------------------

    @property
    def is_filtered(self) -> bool:
        return bool(self._filter)

    @property
    def hidden_tiers(self) -> frozenset[Tier]:
        return self._hidden_tiers

    def groups(self) -> list[tuple[Category, list[Channel]]]:
        """All categories and channels, unfiltered, in provider order."""
        return [(n.cat, n.channels) for n in self._groups]

    def ticked_ids(self) -> frozenset[int]:
        """What gets exported: ticked channels that no filter hides."""
        return frozenset(sid for sid in self._ticked if self._eligible(sid))

    def tier_of(self, stream_id: int) -> Tier | None:
        return self._tier.get(stream_id)

    def tier_counts(self) -> Counter[Tier]:
        return Counter(self._tier.values())

    def flags_of(self, stream_id: int) -> frozenset[Flag]:
        return self._flags.get(stream_id, frozenset())

    def flag_counts(self) -> Counter[Flag]:
        counts: Counter[Flag] = Counter()
        for flags in self._flags.values():
            counts.update(flags)
        return counts

    def region_counts(self) -> Counter[str]:
        """Channels per region, largest first when iterated with most_common()."""
        counts: Counter[str] = Counter()
        for node in self._groups:
            counts[node.region] += len(node.channels)
        return counts

    def total_channels(self) -> int:
        return len(self._node_of)

    def visible_channel_count(self) -> int:
        return sum(len(n.visible) for n in self._visible)

    def ticked_summary(self) -> tuple[int, int]:
        """(exportable ticked channels, categories with at least one of them)."""
        return sum(n.ticked for n in self._groups), sum(1 for n in self._groups if n.ticked)

    def hidden_ticked_count(self) -> int:
        """Ticked channels left out of the export because a filter hides them."""
        return sum(1 for sid in self._ticked if not self._eligible(sid))

    # -- ticking -------------------------------------------------------------

    def set_ticked(self, stream_ids: Iterable[int], ticked: bool) -> None:
        changed: set[_CatNode] = set()
        for sid in stream_ids:
            node = self._node_of.get(sid)
            if node is None or (sid in self._ticked) == ticked:
                continue
            if ticked:
                self._ticked.add(sid)
            else:
                self._ticked.discard(sid)
            if self._eligible(sid):
                node.ticked += 1 if ticked else -1
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
        if node.visible is node.shown:
            ticked, total = node.ticked, len(node.shown)
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
                if col == COL_TIER:
                    return "" if node.tier is Tier.UNTAGGED else node.tier.value
                if col == COL_LABELS:
                    return _labels(node.flags)
                if col == COL_TICKED:
                    return f"{node.ticked} / {len(node.shown)}"
            elif role == Qt.ItemDataRole.CheckStateRole and col == COL_NAME:
                return self._cat_state(node)
            elif role == Qt.ItemDataRole.FontRole:
                return self._bold
            elif role == Qt.ItemDataRole.ToolTipRole and col == COL_NAME:
                hidden = len(node.channels) - len(node.shown)
                tip = f"{node.cat.name}\n{len(node.channels)} channels"
                if hidden:
                    tip += f", {hidden} hidden by filters"
                return tip
            return None

        ch = ptr.node.visible[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            if col == COL_NAME:
                return ch.name
            if col == COL_TIER:
                tier = self._tier.get(ch.stream_id)
                return "" if tier in (None, Tier.UNTAGGED) else tier.value
            if col == COL_LABELS:
                return _labels(self._flags.get(ch.stream_id, ()))
            if col == COL_ID:
                return str(ch.stream_id)
        elif role == Qt.ItemDataRole.CheckStateRole and col == COL_NAME:
            return Qt.CheckState.Checked if ch.stream_id in self._ticked else Qt.CheckState.Unchecked
        elif role == Qt.ItemDataRole.FontRole and col == COL_TIER and ch.stream_id in self._inherited:
            return self._italic
        elif role == Qt.ItemDataRole.ToolTipRole and col in (COL_NAME, COL_TIER, COL_LABELS):
            tier = self._tier.get(ch.stream_id)
            if tier is None or tier is Tier.UNTAGGED:
                source = "Untagged: no quality tag in the channel or category name"
            elif ch.stream_id in self._inherited:
                source = f"{tier.label}, from the category name (the channel has no tag of its own)"
            else:
                source = f"{tier.label}, from the channel name"
            labels = ", ".join(f.label for f in Flag if f in self._flags.get(ch.stream_id, ()))
            return f"{ch.name}\nTier: {source}\nLabels: {labels or 'none'}"
        return None

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole) -> bool:
        if role != Qt.ItemDataRole.CheckStateRole or not index.isValid():
            return False
        self.set_ticked_for_indexes([index], _is_checked(value))
        return True
