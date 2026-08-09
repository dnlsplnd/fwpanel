"""Hand-drawn chart widgets.

Everything is painted directly so the charts inherit the panel's palette and
stay legible at small sizes: no gridline noise, labelled axes only where a
number is actually readable, and a hover crosshair that reports exact values
instead of forcing the eye to interpolate.
"""

from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (QBrush, QFont, QFontMetrics, QLinearGradient,
                           QPainter, QPainterPath, QPen)
from PySide6.QtWidgets import QSizePolicy, QWidget

from . import theme
from ..util import human_count, human_rate


@dataclass
class Series:
    """One line on a chart plus its rolling history."""

    name: str
    color: str
    capacity: int = 240
    fill: bool = True
    width: float = 1.8
    dashed: bool = False
    values: deque = field(default_factory=deque)
    stamps: deque = field(default_factory=deque)
    visible: bool = True

    def __post_init__(self) -> None:
        self.values = deque(maxlen=self.capacity)
        self.stamps = deque(maxlen=self.capacity)

    def push(self, value: float, stamp: float | None = None) -> None:
        self.values.append(float(value))
        self.stamps.append(stamp if stamp is not None else time.time())

    def clear(self) -> None:
        self.values.clear()
        self.stamps.clear()

    @property
    def last(self) -> float:
        return self.values[-1] if self.values else 0.0

    @property
    def peak(self) -> float:
        return max(self.values) if self.values else 0.0

    @property
    def mean(self) -> float:
        return sum(self.values) / len(self.values) if self.values else 0.0


def _nice_ceiling(value: float) -> float:
    """Round up to a friendly axis maximum (1, 2, 2.5 or 5 x 10^n)."""
    if value <= 0:
        return 1.0
    exponent = math.floor(math.log10(value))
    base = 10 ** exponent
    for step in (1.0, 2.0, 2.5, 5.0, 10.0):
        if value <= step * base:
            return step * base
    return 10.0 * base


class LineChart(QWidget):
    """Multi-series time chart with area fill and a value crosshair."""

    def __init__(self, title: str = "", formatter: Callable[[float], str] = human_rate,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.title = title
        self.formatter = formatter
        self.series: list[Series] = []
        self.show_legend = True
        self.baseline_zero = True
        self.y_min_ceiling = 1.0
        self._display_max = 1.0
        self._hover_index: int | None = None
        self.setMinimumHeight(150)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setAttribute(Qt.WA_OpaquePaintEvent, False)

    # -- data ---------------------------------------------------------------
    def add_series(self, name: str, color: str, **kw) -> Series:
        series = Series(name=name, color=color, **kw)
        self.series.append(series)
        return series

    def clear(self) -> None:
        for series in self.series:
            series.clear()
        self.update()

    # -- interaction --------------------------------------------------------
    def mouseMoveEvent(self, event) -> None:
        plot = self._plot_rect()
        count = self._sample_count()
        if count < 2 or not plot.contains(event.position()):
            if self._hover_index is not None:
                self._hover_index = None
                self.update()
            return
        ratio = (event.position().x() - plot.left()) / max(1.0, plot.width())
        index = int(round(ratio * (count - 1)))
        index = max(0, min(count - 1, index))
        if index != self._hover_index:
            self._hover_index = index
            self.update()

    def leaveEvent(self, event) -> None:
        self._hover_index = None
        self.update()

    # -- geometry -----------------------------------------------------------
    def _sample_count(self) -> int:
        return max((len(s.values) for s in self.series if s.visible), default=0)

    def _plot_rect(self) -> QRectF:
        top = 10.0 + (18.0 if self.title else 0.0)
        bottom = 18.0 + (16.0 if self.show_legend else 0.0)
        left = 68.0  # room for a full "573.7 KB/s" axis label
        return QRectF(left, top, max(10.0, self.width() - left - 14.0),
                      max(10.0, self.height() - top - bottom))

    def _target_max(self) -> float:
        peak = 0.0
        for series in self.series:
            if series.visible and series.values:
                peak = max(peak, max(series.values))
        return _nice_ceiling(max(peak * 1.15, self.y_min_ceiling))

    # -- painting -----------------------------------------------------------
    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.TextAntialiasing, True)

        plot = self._plot_rect()
        if self.title:
            painter.setPen(QPen(theme.color(theme.FG_DIM)))
            font = painter.font()
            font.setPointSizeF(8.0)
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(QRectF(6, 4, self.width() - 12, 16),
                             Qt.AlignLeft | Qt.AlignVCenter, self.title.upper())

        # Ease the axis toward its target so the baseline does not twitch.
        target = self._target_max()
        self._display_max += (target - self._display_max) * (0.35 if target > self._display_max else 0.12)
        ceiling = max(self._display_max, 1e-9)

        self._paint_grid(painter, plot, ceiling)
        count = self._sample_count()
        if count >= 2:
            for series in self.series:
                if series.visible and len(series.values) >= 2:
                    self._paint_series(painter, plot, series, ceiling, count)
            if self._hover_index is not None:
                self._paint_crosshair(painter, plot, ceiling, count)
        else:
            painter.setPen(QPen(theme.color(theme.FG_FAINT)))
            painter.drawText(plot, Qt.AlignCenter, "collecting…")

        if self.show_legend:
            self._paint_legend(painter, plot)
        painter.end()

    def _paint_grid(self, painter: QPainter, plot: QRectF, ceiling: float) -> None:
        painter.setPen(QPen(theme.color(theme.BORDER_SOFT), 1))
        painter.setBrush(Qt.NoBrush)

        font = painter.font()
        font.setPointSizeF(7.5)
        font.setBold(False)
        painter.setFont(font)

        for step in range(5):
            fraction = step / 4.0
            y = plot.bottom() - fraction * plot.height()
            painter.setPen(QPen(theme.color(theme.BORDER_SOFT, 150), 1,
                                Qt.SolidLine if step == 0 else Qt.DotLine))
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            painter.setPen(QPen(theme.color(theme.FG_FAINT)))
            painter.drawText(QRectF(0, y - 8, plot.left() - 8, 16),
                             Qt.AlignRight | Qt.AlignVCenter,
                             self.formatter(ceiling * fraction))

    def _points(self, plot: QRectF, series: Series, ceiling: float,
                count: int) -> list[QPointF]:
        values = list(series.values)
        # Right-align short series so a new line grows in from the right edge.
        offset = count - len(values)
        step = plot.width() / max(1, count - 1)
        return [
            QPointF(plot.left() + (offset + i) * step,
                    plot.bottom() - min(1.0, value / ceiling) * plot.height())
            for i, value in enumerate(values)
        ]

    def _paint_series(self, painter: QPainter, plot: QRectF, series: Series,
                      ceiling: float, count: int) -> None:
        points = self._points(plot, series, ceiling, count)
        if len(points) < 2:
            return

        path = QPainterPath(points[0])
        for point in points[1:]:
            path.lineTo(point)

        if series.fill:
            area = QPainterPath(path)
            area.lineTo(points[-1].x(), plot.bottom())
            area.lineTo(points[0].x(), plot.bottom())
            area.closeSubpath()
            gradient = QLinearGradient(0, plot.top(), 0, plot.bottom())
            gradient.setColorAt(0.0, theme.color(series.color, 90))
            gradient.setColorAt(1.0, theme.color(series.color, 6))
            painter.fillPath(area, QBrush(gradient))

        pen = QPen(theme.color(series.color), series.width)
        pen.setJoinStyle(Qt.RoundJoin)
        pen.setCapStyle(Qt.RoundCap)
        if series.dashed:
            pen.setStyle(Qt.DashLine)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(path)

        # Head dot marks the live sample.
        painter.setBrush(QBrush(theme.color(series.color)))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(points[-1], 2.6, 2.6)

    def _paint_crosshair(self, painter: QPainter, plot: QRectF, ceiling: float,
                         count: int) -> None:
        index = self._hover_index
        if index is None:
            return
        step = plot.width() / max(1, count - 1)
        x = plot.left() + index * step
        painter.setPen(QPen(theme.color(theme.FG_FAINT, 130), 1, Qt.DashLine))
        painter.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))

        rows: list[tuple[str, str, str]] = []
        for series in self.series:
            if not series.visible or not series.values:
                continue
            offset = count - len(series.values)
            local = index - offset
            if 0 <= local < len(series.values):
                value = series.values[local]
                rows.append((series.color, series.name, self.formatter(value)))
                y = plot.bottom() - min(1.0, value / ceiling) * plot.height()
                painter.setBrush(QBrush(theme.color(theme.BG_DEEP)))
                painter.setPen(QPen(theme.color(series.color), 1.6))
                painter.drawEllipse(QPointF(x, y), 3.4, 3.4)
        if not rows:
            return

        font = painter.font()
        font.setPointSizeF(8.0)
        painter.setFont(font)
        metrics = QFontMetrics(font)
        width = max(metrics.horizontalAdvance(f"{n}  {v}") for _, n, v in rows) + 26
        height = len(rows) * 15 + 10
        left = x + 12 if x + 12 + width < plot.right() else x - width - 12
        top = min(plot.top() + 6, plot.bottom() - height - 4)
        box = QRectF(left, top, width, height)

        painter.setBrush(QBrush(theme.color(theme.BG_RAISED, 244)))
        painter.setPen(QPen(theme.color(theme.BORDER)))
        painter.drawRoundedRect(box, 6, 6)
        for row, (col, name, value) in enumerate(rows):
            y = box.top() + 5 + row * 15
            painter.setBrush(QBrush(theme.color(col)))
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(QRectF(box.left() + 7, y + 4.5, 6, 6))
            painter.setPen(QPen(theme.color(theme.FG_DIM)))
            painter.drawText(QRectF(box.left() + 18, y, width - 24, 15),
                             Qt.AlignLeft | Qt.AlignVCenter, name)
            painter.setPen(QPen(theme.color(theme.FG)))
            painter.drawText(QRectF(box.left(), y, width - 8, 15),
                             Qt.AlignRight | Qt.AlignVCenter, value)

    def _paint_legend(self, painter: QPainter, plot: QRectF) -> None:
        font = painter.font()
        font.setPointSizeF(8.0)
        font.setBold(False)
        painter.setFont(font)
        metrics = QFontMetrics(font)
        x = plot.left()
        y = self.height() - 15.0
        for series in self.series:
            label = f"{series.name}  {self.formatter(series.last)}"
            painter.setBrush(QBrush(theme.color(series.color if series.visible else theme.FG_FAINT)))
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(QRectF(x, y + 4, 7, 7))
            painter.setPen(QPen(theme.color(theme.FG_DIM if series.visible else theme.FG_FAINT)))
            painter.drawText(QRectF(x + 12, y, metrics.horizontalAdvance(label) + 6, 14),
                             Qt.AlignLeft | Qt.AlignVCenter, label)
            x += metrics.horizontalAdvance(label) + 30
            if x > self.width() - 60:
                break


class Sparkline(QWidget):
    """A compact trend strip for stat cards."""

    def __init__(self, color: str = theme.ACCENT, capacity: int = 90,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._series = Series("", color, capacity=capacity)
        self.color = color
        self.setFixedHeight(30)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        # Inherit the card behind it rather than painting the window ground.
        self.setStyleSheet("background: transparent;")
        self.setAttribute(Qt.WA_NoSystemBackground, True)

    def push(self, value: float) -> None:
        self._series.push(value)
        self.update()

    def paintEvent(self, event) -> None:
        values = list(self._series.values)
        if len(values) < 2:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        # Scale to the window's own range: a steady value should read as a
        # steady line, not a block filling the whole strip.
        low, high = min(values), max(values)
        span = (high - low) or max(high, 1e-9)
        rect = QRectF(1, 3, self.width() - 2, self.height() - 6)
        step = rect.width() / (len(values) - 1)
        points = [QPointF(rect.left() + i * step,
                          rect.bottom() - ((v - low) / span * 0.82 + 0.09) * rect.height())
                  for i, v in enumerate(values)]
        path = QPainterPath(points[0])
        for point in points[1:]:
            path.lineTo(point)

        area = QPainterPath(path)
        area.lineTo(points[-1].x(), rect.bottom())
        area.lineTo(points[0].x(), rect.bottom())
        area.closeSubpath()
        gradient = QLinearGradient(0, rect.top(), 0, rect.bottom())
        gradient.setColorAt(0.0, theme.color(self.color, 80))
        gradient.setColorAt(1.0, theme.color(self.color, 0))
        painter.fillPath(area, QBrush(gradient))

        painter.setPen(QPen(theme.color(self.color), 1.5))
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(path)
        painter.end()


class DonutGauge(QWidget):
    """Ring gauge for a bounded ratio (conntrack occupancy, and friends)."""

    def __init__(self, caption: str = "", higher_is_better: bool = False,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.caption = caption
        #: Occupancy gauges go red when full; score gauges go red when empty.
        self.higher_is_better = higher_is_better
        self.value = 0.0
        self.maximum = 100.0
        self.center_text = ""
        self.sub_text = ""
        self.setMinimumSize(120, 120)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_value(self, value: float, maximum: float, center: str = "",
                  sub: str = "") -> None:
        self.value = float(value)
        self.maximum = max(1.0, float(maximum))
        self.center_text = center
        self.sub_text = sub
        self.update()

    def _ring_color(self, ratio: float) -> str:
        if self.higher_is_better:
            ratio = 1.0 - ratio
        if ratio >= 0.9:
            return theme.DANGER
        if ratio >= 0.7:
            return theme.WARN
        if ratio >= 0.45:
            return theme.INFO
        return theme.ACCENT

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        side = min(self.width(), self.height() - (16 if self.caption else 0)) - 14
        side = max(40, side)
        rect = QRectF((self.width() - side) / 2.0, 7, side, side)
        thickness = max(7.0, side * 0.11)

        ratio = min(1.0, self.value / self.maximum)
        painter.setPen(QPen(theme.color(theme.BG_RAISED), thickness, Qt.SolidLine, Qt.RoundCap))
        painter.drawArc(rect.adjusted(thickness / 2, thickness / 2,
                                      -thickness / 2, -thickness / 2),
                        90 * 16, -360 * 16)
        # Below a couple of degrees the round cap draws a floating dot that
        # reads as a rendering glitch rather than a tiny value.
        if ratio * 360 >= 2.0:
            painter.setPen(QPen(theme.color(self._ring_color(ratio)), thickness,
                                Qt.SolidLine, Qt.RoundCap))
            painter.drawArc(rect.adjusted(thickness / 2, thickness / 2,
                                          -thickness / 2, -thickness / 2),
                            90 * 16, -int(360 * 16 * ratio))

        font = painter.font()
        font.setPointSizeF(max(10.0, side * 0.16))
        font.setWeight(QFont.Light)
        painter.setFont(font)
        painter.setPen(QPen(theme.color(theme.FG)))
        painter.drawText(rect.adjusted(0, -side * 0.06, 0, -side * 0.06),
                         Qt.AlignCenter, self.center_text)

        if self.sub_text:
            font.setPointSizeF(8.0)
            painter.setFont(font)
            painter.setPen(QPen(theme.color(theme.FG_FAINT)))
            painter.drawText(rect.adjusted(0, side * 0.24, 0, side * 0.24),
                             Qt.AlignCenter, self.sub_text)

        if self.caption:
            font.setPointSizeF(8.0)
            font.setBold(True)
            painter.setFont(font)
            painter.setPen(QPen(theme.color(theme.FG_DIM)))
            painter.drawText(QRectF(0, self.height() - 16, self.width(), 15),
                             Qt.AlignCenter, self.caption.upper())
        painter.end()


class BarList(QWidget):
    """Horizontal ranked bars - socket states, top talkers, chain counters."""

    barClicked = Signal(str)

    def __init__(self, formatter: Callable[[float], str] = human_count,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.formatter = formatter
        self.rows: list[tuple[str, float, str]] = []
        self.row_height = 24
        self._hover = -1
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumHeight(80)

    def set_rows(self, rows: list[tuple[str, float, str]]) -> None:
        self.rows = rows
        self.setMinimumHeight(max(80, len(rows) * self.row_height + 8))
        self.update()

    def mouseMoveEvent(self, event) -> None:
        index = int((event.position().y() - 4) // self.row_height)
        index = index if 0 <= index < len(self.rows) else -1
        if index != self._hover:
            self._hover = index
            self.update()

    def leaveEvent(self, event) -> None:
        self._hover = -1
        self.update()

    def mousePressEvent(self, event) -> None:
        if 0 <= self._hover < len(self.rows):
            self.barClicked.emit(self.rows[self._hover][0])

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        if not self.rows:
            painter.setPen(QPen(theme.color(theme.FG_FAINT)))
            painter.drawText(self.rect(), Qt.AlignCenter, "no data")
            painter.end()
            return

        font = painter.font()
        font.setPointSizeF(8.5)
        painter.setFont(font)
        metrics = QFontMetrics(font)
        label_width = min(190, max(70, max(
            metrics.horizontalAdvance(label) for label, _, _ in self.rows) + 10))
        value_width = max(52, max(
            metrics.horizontalAdvance(self.formatter(v)) for _, v, _ in self.rows) + 10)
        peak = max((value for _, value, _ in self.rows), default=1.0) or 1.0

        for index, (label, value, color) in enumerate(self.rows):
            y = 4 + index * self.row_height
            track = QRectF(label_width + 6, y + 6,
                           max(6.0, self.width() - label_width - value_width - 14),
                           self.row_height - 12)
            if index == self._hover:
                painter.setBrush(QBrush(theme.color(theme.BG_HOVER, 120)))
                painter.setPen(Qt.NoPen)
                painter.drawRoundedRect(QRectF(0, y, self.width(), self.row_height), 5, 5)

            painter.setPen(QPen(theme.color(theme.FG_DIM)))
            painter.drawText(QRectF(4, y, label_width, self.row_height),
                             Qt.AlignLeft | Qt.AlignVCenter,
                             metrics.elidedText(label, Qt.ElideMiddle, int(label_width) - 6))

            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(theme.color(theme.BG_RAISED)))
            painter.drawRoundedRect(track, 3, 3)
            filled = QRectF(track)
            filled.setWidth(max(2.0, track.width() * (value / peak)))
            painter.setBrush(QBrush(theme.color(color)))
            painter.drawRoundedRect(filled, 3, 3)

            painter.setPen(QPen(theme.color(theme.FG)))
            painter.drawText(QRectF(self.width() - value_width - 4, y, value_width,
                                    self.row_height),
                             Qt.AlignRight | Qt.AlignVCenter, self.formatter(value))
        painter.end()


class StackedTimeline(QWidget):
    """Event density strip: one column per bucket, coloured by severity."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.buckets: deque = deque(maxlen=180)
        self.setFixedHeight(46)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def push(self, count: int) -> None:
        self.buckets.append(int(count))
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)
        rect = QRectF(2, 4, self.width() - 4, self.height() - 8)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(theme.color(theme.BG_DEEP)))
        painter.drawRect(rect)

        values = list(self.buckets)
        if not values:
            painter.setPen(QPen(theme.color(theme.FG_FAINT)))
            painter.drawText(self.rect(), Qt.AlignCenter, "no denied packets logged")
            painter.end()
            return

        peak = max(max(values), 1)
        width = rect.width() / self.buckets.maxlen
        for index, value in enumerate(values):
            if value <= 0:
                continue
            height = max(2.0, rect.height() * (value / peak))
            ratio = value / peak
            color = theme.DANGER if ratio > 0.6 else (theme.WARN if ratio > 0.25 else theme.ACCENT)
            painter.setBrush(QBrush(theme.color(color, 220)))
            painter.drawRect(QRectF(rect.left() + index * width,
                                    rect.bottom() - height,
                                    max(1.0, width - 1), height))
        painter.end()
