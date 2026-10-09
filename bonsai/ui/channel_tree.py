"""Two level tree model: categories with tri-state ticks, channels with ticks.

A custom model rather than QStandardItemModel so several thousand channels
load instantly: rows are only materialised when a category is expanded.

Two kinds of filter are applied inside the model:
- the tier, label and region filters decide which channels are eligible at
  all. Hidden channels are left out of the view, the counts and the export,
  but their ticks are remembered in case they are shown again;
- the search filter only narrows what is on screen. Ticking a category
  ticks only its currently visible channels.

Channels marked as new (since a profile was saved) are highlighted.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable

from PySide6.QtCore import QAbstractItemModel, QModelIndex, Qt, Signal
from PySide6.QtGui import QColor, QFont

from ..filters import ChannelTags, FilterState, Flag, flags_from_name, region_of, tag_channels
from ..models import Category, Channel
from ..quality import Tier, normalise_name, tier_from_name

COL_NAME, COL_TIER, COL_LABELS, COL_TICKED, COL_ID = range(5)
HEADERS = ("Name", "Tier", "Labels", "Ticked", "Stream ID")

# Translucent so it reads on both light and dark themes.
NEW_BACKGROUND = QColor(76, 175, 80, 70)


class _CatNode:
    __slots__ = ("cat", "key", "tier", "flags", "region", "channels", "shown", "visible", "row",
                 "ticked", "new", "child_ref")

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
        self.new = 0  # new channels among `shown`
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
        self._tags: dict[int, ChannelTags] = {}
        self._key: dict[int, str] = {}  # normalised, casefolded channel names for searching
        self._ticked: set[int] = set()
        self._new: set[int] = set()
        self._state = FilterState()
        self._filter = ""
        self._bold = QFont()
        self._bold.setBold(True)
        self._italic = QFont()
        self._italic.setItalic(True)

    # -- loading and filtering ---------------------------------------------

    def set_groups(self, groups: Iterable[tuple[Category, list[Channel]]]) -> None:
        """Replace the contents. Ticks on channels that still exist are kept."""
        self.beginResetModel()
        self._groups = [_CatNode(cat, list(chans)) for cat, chans in groups if chans]
        self._node_of = {ch.stream_id: n for n in self._groups for ch in n.channels}
        self._tags = tag_channels([(n.cat, n.channels) for n in self._groups])
        self._key = {ch.stream_id: normalise_name(ch.name).casefold() for n in self._groups for ch in n.channels}
        self._ticked &= self._node_of.keys()
        self._new &= self._node_of.keys()
        self._refilter()
        self.endResetModel()
        self.tickedChanged.emit()

    def set_filter_state(self, state: FilterState) -> None:
        if state == self._state:
            return
        self.beginResetModel()
        self._state = state
        self._refilter()
        self.endResetModel()
        self.tickedChanged.emit()

    def set_hidden_tiers(self, tiers: Iterable[Tier]) -> None:
        s = self._state
        self.set_filter_state(FilterState(frozenset(tiers), s.hidden_flags, s.hidden_regions))

    def set_hidden_flags(self, flags: Iterable[Flag]) -> None:
        s = self._state
        self.set_filter_state(FilterState(s.hidden_tiers, frozenset(flags), s.hidden_regions))

    def set_hidden_regions(self, regions: Iterable[str]) -> None:
        s = self._state
        self.set_filter_state(FilterState(s.hidden_tiers, s.hidden_flags, frozenset(regions)))

    def set_filter(self, text: str) -> None:
        text = normalise_name(text.strip()).casefold()
        if text == self._filter:
            return
        self.beginResetModel()
        self._filter = text
        self._apply_search()
        self.endResetModel()

    def _refilter(self) -> None:
        allows, tags = self._state.allows, self._tags
        hidden_regions = self._state.hidden_regions
        for node in self._groups:
            if node.region in hidden_regions:
                node.shown = []
            else:
                node.shown = [ch for ch in node.channels if allows(tags[ch.stream_id])]
            node.ticked = sum(1 for ch in node.shown if ch.stream_id in self._ticked)
            node.new = sum(1 for ch in node.shown if ch.stream_id in self._new)
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
        return self._state.allows(self._tags.get(stream_id))

    # -- queries -------------------------------------------------------------

    @property
    def is_filtered(self) -> bool:
        return bool(self._filter)

    @property
    def filter_state(self) -> FilterState:
        return self._state

    @property
    def hidden_tiers(self) -> frozenset[Tier]:
        return self._state.hidden_tiers

    def has_data(self) -> bool:
        return bool(self._groups)

    def groups(self) -> list[tuple[Category, list[Channel]]]:
        """All categories and channels, unfiltered, in provider order."""
        return [(n.cat, n.channels) for n in self._groups]

    def ticked_ids(self) -> frozenset[int]:
        """What gets exported: ticked channels that no filter hides."""
        return frozenset(sid for sid in self._ticked if self._eligible(sid))

    def all_ticked_ids(self) -> frozenset[int]:
        """Every tick, including channels currently hidden by filters."""
        return frozenset(self._ticked)

    def full_category_ids(self) -> set[str]:
        """Categories where every shown channel is ticked."""
        return {n.cat.id for n in self._groups if n.shown and n.ticked == len(n.shown)}

    def tier_of(self, stream_id: int) -> Tier | None:
        tags = self._tags.get(stream_id)
        return tags.tier if tags else None

    def flags_of(self, stream_id: int) -> frozenset[Flag]:
        tags = self._tags.get(stream_id)
        return tags.flags if tags else frozenset()

    def tier_counts(self) -> Counter[Tier]:
        return Counter(t.tier for t in self._tags.values())

    def flag_counts(self) -> Counter[Flag]:
        counts: Counter[Flag] = Counter()
        for t in self._tags.values():
            counts.update(t.flags)
        return counts

    def region_counts(self) -> Counter[str]:
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

    def new_shown_count(self) -> int:
        return sum(n.new for n in self._groups)

    def rows_with_new(self) -> list[int]:
        """Top-level rows of visible categories that contain new channels."""
        return [n.row for n in self._visible if any(ch.stream_id in self._new for ch in n.visible)]

    # -- ticking and marking -------------------------------------------------

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

    def replace_ticked(self, stream_ids: Iterable[int], new_ids: Iterable[int] = ()) -> None:
        """Set exactly these ticks (and new-channel marks), e.g. when a profile is loaded."""
        self.beginResetModel()
        self._ticked = set(stream_ids) & self._node_of.keys()
        self._new = set(new_ids) & self._node_of.keys()
        self._refilter()
        self.endResetModel()
        self.tickedChanged.emit()

    def clear_new(self) -> None:
        if self._new:
            self.beginResetModel()
            self._new.clear()
            self._refilter()
            self.endResetModel()

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
            return self._category_data(ptr, col, role)

        ch = ptr.node.visible[index.row()]
        sid = ch.stream_id
        tags = self._tags.get(sid)
        is_new = sid in self._new
        if role == Qt.ItemDataRole.DisplayRole:
            if col == COL_NAME:
                return ch.name
            if col == COL_TIER:
                return "" if tags is None or tags.tier is Tier.UNTAGGED else tags.tier.value
            if col == COL_LABELS:
                text = _labels(tags.flags) if tags else ""
                return f"NEW {text}".strip() if is_new else text
            if col == COL_ID:
                return str(sid)
        elif role == Qt.ItemDataRole.CheckStateRole and col == COL_NAME:
            return Qt.CheckState.Checked if sid in self._ticked else Qt.CheckState.Unchecked
        elif role == Qt.ItemDataRole.BackgroundRole and is_new:
            return NEW_BACKGROUND
        elif role == Qt.ItemDataRole.FontRole and col == COL_TIER and tags and tags.tier_inherited:
            return self._italic
        elif role == Qt.ItemDataRole.ToolTipRole and col in (COL_NAME, COL_TIER, COL_LABELS):
            return self._channel_tooltip(ch, tags, is_new)
        return None

    def _category_data(self, node: _CatNode, col: int, role):
        if role == Qt.ItemDataRole.DisplayRole:
            if col == COL_NAME:
                return node.cat.name
            if col == COL_TIER:
                return "" if node.tier is Tier.UNTAGGED else node.tier.value
            if col == COL_LABELS:
                text = _labels(node.flags)
                return f"{node.new} NEW {text}".strip() if node.new else text
            if col == COL_TICKED:
                return f"{node.ticked} / {len(node.shown)}"
        elif role == Qt.ItemDataRole.CheckStateRole and col == COL_NAME:
            return self._cat_state(node)
        elif role == Qt.ItemDataRole.FontRole:
            return self._bold
        elif role == Qt.ItemDataRole.BackgroundRole and node.new:
            return NEW_BACKGROUND
        elif role == Qt.ItemDataRole.ToolTipRole and col == COL_NAME:
            hidden = len(node.channels) - len(node.shown)
            tip = f"{node.cat.name}\n{len(node.channels)} channels"
            if hidden:
                tip += f", {hidden} hidden by filters"
            if node.new:
                tip += f"\n{node.new} new since the profile was saved"
            return tip
        return None

    @staticmethod
    def _channel_tooltip(ch: Channel, tags: ChannelTags | None, is_new: bool) -> str:
        if tags is None or tags.tier is Tier.UNTAGGED:
            source = "Untagged: no quality tag in the channel or category name"
        elif tags.tier_inherited:
            source = f"{tags.tier.label}, from the category name (the channel has no tag of its own)"
        else:
            source = f"{tags.tier.label}, from the channel name"
        labels = ", ".join(f.label for f in Flag if tags and f in tags.flags)
        tip = f"{ch.name}\nTier: {source}\nLabels: {labels or 'none'}"
        if is_new:
            tip += "\nNew since the profile was saved"
        return tip

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole) -> bool:
        if role != Qt.ItemDataRole.CheckStateRole or not index.isValid():
            return False
        self.set_ticked_for_indexes([index], _is_checked(value))
        return True
