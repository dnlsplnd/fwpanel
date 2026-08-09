"""Reusable building blocks: cards, stat tiles, badges, filtered tables."""

from __future__ import annotations

from typing import Iterable

from PySide6.QtCore import QSortFilterProxyModel, Qt, Signal
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (QAbstractItemView, QFrame, QGridLayout,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QPushButton, QSizePolicy, QTableView,
                               QVBoxLayout, QWidget)

from . import theme
from .charts import Sparkline


class Card(QFrame):
    """A titled surface. Put content into :attr:`body`."""

    def __init__(self, title: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(8)

        self.header = QHBoxLayout()
        self.header.setSpacing(8)
        self.title_label = QLabel(title.upper())
        self.title_label.setObjectName("CardTitle")
        self.header.addWidget(self.title_label)
        self.header.addStretch(1)
        outer.addLayout(self.header)

        self.body = QVBoxLayout()
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(8)
        outer.addLayout(self.body, 1)

    def set_title(self, title: str) -> None:
        self.title_label.setText(title.upper())

    def add_header_widget(self, widget: QWidget) -> None:
        self.header.addWidget(widget)


class Badge(QLabel):
    """Small status pill. Call :meth:`set_state` to recolour it."""

    def __init__(self, text: str = "", color: str = theme.NEUTRAL,
                 parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setAlignment(Qt.AlignCenter)
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Maximum)
        self.set_state(text, color)

    def set_state(self, text: str, color: str) -> None:
        self.setText(text)
        self.setStyleSheet(
            f"background: rgba({QColorTuple(color)}, 0.16);"
            f"color: {color};"
            f"border: 1px solid rgba({QColorTuple(color)}, 0.45);"
            "border-radius: 9px; padding: 2px 9px;"
            "font-size: 8pt; font-weight: 700; letter-spacing: 0.6px;"
        )


def QColorTuple(hex_color: str) -> str:
    """'#rrggbb' -> 'r, g, b' for use inside a Qt stylesheet rgba()."""
    value = hex_color.lstrip("#")
    if len(value) == 3:
        value = "".join(ch * 2 for ch in value)
    try:
        return f"{int(value[0:2], 16)}, {int(value[2:4], 16)}, {int(value[4:6], 16)}"
    except ValueError:
        return "120, 133, 156"


class StatTile(QFrame):
    """Big-number tile with an optional trend strip underneath."""

    def __init__(self, title: str, unit: str = "", caption: str = "",
                 spark_color: str | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 11, 14, 11)
        layout.setSpacing(3)

        self.title_label = QLabel(title.upper())
        self.title_label.setObjectName("CardTitle")
        layout.addWidget(self.title_label)

        row = QHBoxLayout()
        row.setSpacing(5)
        self.value_label = QLabel("—")
        self.value_label.setObjectName("StatValue")
        row.addWidget(self.value_label)
        self.unit_label = QLabel(unit)
        self.unit_label.setObjectName("StatUnit")
        row.addWidget(self.unit_label, 0, Qt.AlignBottom)
        row.addStretch(1)
        layout.addLayout(row)

        self.caption_label = QLabel(caption)
        self.caption_label.setObjectName("StatCaption")
        layout.addWidget(self.caption_label)

        self.spark: Sparkline | None = None
        if spark_color:
            self.spark = Sparkline(spark_color)
            layout.addWidget(self.spark)

        self.setMinimumWidth(150)

    def set_value(self, value: str, unit: str | None = None,
                  caption: str | None = None, color: str | None = None) -> None:
        self.value_label.setText(value)
        if unit is not None:
            self.unit_label.setText(unit)
        if caption is not None:
            self.caption_label.setText(caption)
        if color is not None:
            self.value_label.setStyleSheet(f"font-size: 21pt; font-weight: 300; color: {color};")

    def push(self, value: float) -> None:
        if self.spark is not None:
            self.spark.push(value)


class KeyValueGrid(QWidget):
    """Two-column label grid for detail panes."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(14)
        self._grid.setVerticalSpacing(6)
        self._grid.setColumnStretch(1, 1)
        self._rows = 0
        self._values: dict[str, QLabel] = {}

    def clear(self) -> None:
        while self._grid.count():
            item = self._grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._rows = 0
        self._values.clear()

    def add(self, key: str, value: str, color: str | None = None,
            monospace: bool = False) -> QLabel:
        key_label = QLabel(key)
        key_label.setStyleSheet(f"color: {theme.FG_FAINT}; font-size: 9pt;")
        value_label = QLabel(value)
        value_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        value_label.setWordWrap(True)
        style = f"color: {color or theme.FG}; font-size: 9.5pt;"
        if monospace:
            style += "font-family: 'JetBrains Mono','DejaVu Sans Mono',monospace;"
        value_label.setStyleSheet(style)
        self._grid.addWidget(key_label, self._rows, 0, Qt.AlignTop | Qt.AlignRight)
        self._grid.addWidget(value_label, self._rows, 1, Qt.AlignTop | Qt.AlignLeft)
        self._values[key] = value_label
        self._rows += 1
        return value_label


class SearchBox(QLineEdit):
    def __init__(self, placeholder: str = "Filter…", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setPlaceholderText(placeholder)
        self.setClearButtonEnabled(True)
        self.setMinimumWidth(200)


class FilterTable(QWidget):
    """QTableView + model + case-insensitive all-column filter proxy."""

    doubleClicked = Signal(int)          # source row
    selectionChanged = Signal(int)       # source row, -1 when cleared

    def __init__(self, headers: list[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.model = QStandardItemModel(0, len(headers), self)
        self.model.setHorizontalHeaderLabels(headers)
        self.proxy = QSortFilterProxyModel(self)
        self.proxy.setSourceModel(self.model)
        self.proxy.setFilterKeyColumn(-1)
        self.proxy.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.proxy.setSortRole(Qt.UserRole + 1)

        self.view = QTableView(self)
        self.view.setModel(self.proxy)
        self.view.setSortingEnabled(True)
        self.view.setAlternatingRowColors(True)
        self.view.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.view.setSelectionMode(QAbstractItemView.SingleSelection)
        self.view.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.view.setWordWrap(False)
        self.view.verticalHeader().setVisible(False)
        self.view.verticalHeader().setDefaultSectionSize(26)
        self.view.horizontalHeader().setStretchLastSection(True)
        self.view.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.view.setContextMenuPolicy(Qt.CustomContextMenu)
        layout.addWidget(self.view)

        self.view.doubleClicked.connect(
            lambda index: self.doubleClicked.emit(self.proxy.mapToSource(index).row()))
        self.view.selectionModel().selectionChanged.connect(self._emit_selection)

    def _emit_selection(self, *_args) -> None:
        self.selectionChanged.emit(self.current_row())

    # -- data ---------------------------------------------------------------
    def set_filter(self, text: str) -> None:
        self.proxy.setFilterFixedString(text)

    def clear_rows(self) -> None:
        self.model.removeRows(0, self.model.rowCount())

    def append(self, cells: Iterable, colors: dict[int, str] | None = None,
               sort_keys: dict[int, object] | None = None,
               payload: object = None, monospace: Iterable[int] = ()) -> int:
        colors = colors or {}
        sort_keys = sort_keys or {}
        monospace = set(monospace)
        items = []
        for column, cell in enumerate(cells):
            item = QStandardItem("" if cell is None else str(cell))
            item.setEditable(False)
            item.setData(sort_keys.get(column, str(cell)), Qt.UserRole + 1)
            if column in colors:
                item.setForeground(theme.color(colors[column]))
            if column in monospace:
                item.setFont(theme.mono_font(9))
            if column == 0 and payload is not None:
                item.setData(payload, Qt.UserRole + 2)
            items.append(item)
        self.model.appendRow(items)
        return self.model.rowCount() - 1

    def payload(self, source_row: int) -> object:
        item = self.model.item(source_row, 0)
        return item.data(Qt.UserRole + 2) if item else None

    def current_row(self) -> int:
        indexes = self.view.selectionModel().selectedRows()
        if not indexes:
            return -1
        return self.proxy.mapToSource(indexes[0]).row()

    def current_payload(self) -> object:
        row = self.current_row()
        return self.payload(row) if row >= 0 else None

    def row_at(self, pos) -> int:
        index = self.view.indexAt(pos)
        return self.proxy.mapToSource(index).row() if index.isValid() else -1

    def select_source_row(self, row: int) -> None:
        if row < 0 or row >= self.model.rowCount():
            return
        proxy_index = self.proxy.mapFromSource(self.model.index(row, 0))
        if proxy_index.isValid():
            self.view.selectRow(proxy_index.row())

    def resize_columns(self, widths: dict[int, int] | None = None) -> None:
        self.view.resizeColumnsToContents()
        for column, width in (widths or {}).items():
            self.view.setColumnWidth(column, width)

    def stretch_column(self, column: int) -> None:
        """Let one column absorb the slack instead of the last one."""
        header = self.view.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(column, QHeaderView.Stretch)

    def row_count(self) -> int:
        return self.model.rowCount()


def toolbar_button(text: str, tooltip: str = "", accent: bool = False,
                   danger: bool = False) -> QPushButton:
    button = QPushButton(text)
    if tooltip:
        button.setToolTip(tooltip)
    if accent:
        button.setProperty("accent", "true")
    if danger:
        button.setProperty("danger", "true")
    return button


def hline() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.HLine)
    line.setStyleSheet(f"background: {theme.BORDER_SOFT}; max-height: 1px; border: none;")
    return line


def section_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("SectionTitle")
    return label
