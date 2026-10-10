"""A layout that places widgets left to right and wraps onto new lines (after Qt's FlowLayout example)."""

from __future__ import annotations

from PySide6.QtCore import QMargins, QPoint, QRect, QSize, Qt
from PySide6.QtWidgets import QLayout, QLayoutItem


class FlowLayout(QLayout):
    def __init__(self, parent=None, h_spacing: int = 14, v_spacing: int = 4) -> None:
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self._h = h_spacing
        self._v = v_spacing
        self.setContentsMargins(QMargins(0, 0, 0, 0))

    def addItem(self, item: QLayoutItem) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self) -> Qt.Orientation:
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._do_layout(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        self._do_layout(rect, apply=True)

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        return size + QSize(m.left() + m.right(), m.top() + m.bottom())

    def _do_layout(self, rect: QRect, apply: bool) -> int:
        m = self.contentsMargins()
        area = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x, y, line_height = area.x(), area.y(), 0
        for item in self._items:
            if item.widget() is not None and not item.widget().isVisible() and apply:
                continue
            hint = item.sizeHint()
            next_x = x + hint.width() + self._h
            if next_x - self._h > area.right() + 1 and line_height > 0:
                x = area.x()
                y += line_height + self._v
                next_x = x + hint.width() + self._h
                line_height = 0
            if apply:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x = next_x
            line_height = max(line_height, hint.height())
        return y + line_height - rect.y() + m.bottom()
