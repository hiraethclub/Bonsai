"""Filter controls: resolution tiers, content labels and regions."""

from __future__ import annotations

from collections import Counter

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from ..filters import Flag, flag_tooltip
from ..quality import Tier
from .flow_layout import FlowLayout


class _RegionList(QWidget):
    """Checkable list of regions with All / None buttons, shown in a drop-down."""

    changed = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        buttons = QHBoxLayout()
        all_btn = QPushButton("All")
        none_btn = QPushButton("None")
        all_btn.clicked.connect(lambda: self._set_all(True))
        none_btn.clicked.connect(lambda: self._set_all(False))
        buttons.addWidget(all_btn)
        buttons.addWidget(none_btn)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        self.list = QListWidget()
        self.list.setMinimumSize(220, 360)
        self.list.itemChanged.connect(lambda _item: self.changed.emit())
        layout.addWidget(self.list)
        self._hidden: set[str] = set()

    def set_regions(self, counts: Counter[str], hidden: set[str]) -> None:
        self._hidden = set(hidden)
        self.list.blockSignals(True)
        self.list.clear()
        for region, n in counts.most_common():
            item = QListWidgetItem(f"{region}  ({n})")
            item.setData(Qt.ItemDataRole.UserRole, region)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked if region in hidden else Qt.CheckState.Checked)
            self.list.addItem(item)
        self.list.blockSignals(False)

    def hidden(self) -> set[str]:
        # Regions saved as hidden but absent from the current list stay hidden.
        listed = set()
        hidden = set()
        for i in range(self.list.count()):
            item = self.list.item(i)
            region = item.data(Qt.ItemDataRole.UserRole)
            listed.add(region)
            if item.checkState() != Qt.CheckState.Checked:
                hidden.add(region)
        return hidden | (self._hidden - listed)

    def shown(self) -> list[str]:
        return [
            self.list.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self.list.count())
            if self.list.item(i).checkState() == Qt.CheckState.Checked
        ]

    def _set_all(self, on: bool) -> None:
        state = Qt.CheckState.Checked if on else Qt.CheckState.Unchecked
        self.list.blockSignals(True)
        for i in range(self.list.count()):
            self.list.item(i).setCheckState(state)
        self.list.blockSignals(False)
        self.changed.emit()


class FilterPanel(QGroupBox):
    """Emits `changed` whenever any filter is toggled."""

    changed = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__("Filters, from tags in channel and category names", parent)
        grid = QGridLayout(self)
        grid.setColumnStretch(1, 1)

        tiers_box = QWidget()
        tiers_flow = FlowLayout(tiers_box)
        self.tier_checks: dict[Tier, QCheckBox] = {}
        for tier in Tier:
            box = QCheckBox(tier.label)
            box.toggled.connect(self.changed)
            self.tier_checks[tier] = box
            tiers_flow.addWidget(box)
        grid.addWidget(QLabel("Resolution:"), 0, 0, Qt.AlignmentFlag.AlignTop)
        grid.addWidget(tiers_box, 0, 1)

        content_box = QWidget()
        content_flow = FlowLayout(content_box)
        self.flag_checks: dict[Flag, QCheckBox] = {}
        for flag in Flag:
            box = QCheckBox(flag.label)
            box.setToolTip(flag_tooltip(flag))
            box.toggled.connect(self.changed)
            self.flag_checks[flag] = box
            content_flow.addWidget(box)
        grid.addWidget(QLabel("Content:"), 1, 0, Qt.AlignmentFlag.AlignTop)
        grid.addWidget(content_box, 1, 1)

        self.regions = _RegionList()
        self.regions.changed.connect(self._regions_changed)
        menu = QMenu(self)
        action = QWidgetAction(menu)
        action.setDefaultWidget(self.regions)
        menu.addAction(action)
        self.region_btn = QToolButton()
        self.region_btn.setText("All")
        self.region_btn.setToolTip("Category prefixes such as US| or UK: — untick regions you never watch.")
        self.region_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.region_btn.setMenu(menu)
        region_row = QHBoxLayout()
        region_row.addWidget(self.region_btn)
        region_row.addStretch(1)
        grid.addWidget(QLabel("Regions:"), 2, 0)
        grid.addLayout(region_row, 2, 1)

        note = QLabel(
            "Ticked boxes are shown. Hidden channels are left out of the export even if ticked. "
            "\u201cHD\u201d is the provider's label and is not guaranteed to be 720p; "
            "some HD channels are 1080i. Hover a box for what it matches."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: palette(placeholder-text);")
        grid.addWidget(note, 3, 0, 1, 2)

    # -- state ---------------------------------------------------------------

    def set_hidden(self, tiers: set[Tier], flags: set[Flag], regions: set[str]) -> None:
        for tier, box in self.tier_checks.items():
            box.blockSignals(True)
            box.setChecked(tier not in tiers)
            box.blockSignals(False)
        for flag, box in self.flag_checks.items():
            box.blockSignals(True)
            box.setChecked(flag not in flags)
            box.blockSignals(False)
        self.regions.set_regions(Counter(), regions)
        self._update_region_label()

    def hidden_tiers(self) -> set[Tier]:
        return {t for t, box in self.tier_checks.items() if not box.isChecked()}

    def hidden_flags(self) -> set[Flag]:
        return {f for f, box in self.flag_checks.items() if not box.isChecked()}

    def hidden_regions(self) -> set[str]:
        return self.regions.hidden()

    def update_counts(self, tiers: Counter, flags: Counter, regions: Counter) -> None:
        for tier, box in self.tier_checks.items():
            box.setText(f"{tier.label} ({tiers[tier]})" if tiers else tier.label)
        for flag, box in self.flag_checks.items():
            box.setText(f"{flag.label} ({flags[flag]})" if tiers else flag.label)
        for box in (*self.tier_checks.values(), *self.flag_checks.values()):
            box.updateGeometry()
        self.regions.set_regions(regions, self.regions.hidden())
        self._update_region_label()

    def _regions_changed(self) -> None:
        self._update_region_label()
        self.changed.emit()

    def _update_region_label(self) -> None:
        total = self.regions.list.count()
        shown = self.regions.shown()
        if not total:
            hidden = len(self.regions.hidden())
            text = f"{hidden} hidden" if hidden else "All"
        elif len(shown) == total:
            text = f"All ({total})"
        elif not shown:
            text = "None"
        elif len(shown) <= 5:
            text = ", ".join(shown)
        else:
            text = f"{len(shown)} of {total}"
        self.region_btn.setText(text)
