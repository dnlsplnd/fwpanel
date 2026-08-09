"""Icon generation.

The tray icon is drawn at runtime rather than shipped as a set of PNGs: the
shield is tinted by firewall state, so its colour carries information at a
glance even when the panel window is closed.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QIcon, QPainter, QPainterPath, QPen, QPixmap

from . import theme

STATE_COLORS = {
    "running": theme.ACCENT,
    "panic": theme.DANGER,
    "offline": theme.FG_FAINT,
    "warning": theme.WARN,
}


def _shield_path(rect: QRectF) -> QPainterPath:
    width, height = rect.width(), rect.height()
    left, top = rect.left(), rect.top()
    path = QPainterPath()
    path.moveTo(left + width * 0.5, top + height * 0.03)
    path.lineTo(left + width * 0.93, top + height * 0.20)
    path.lineTo(left + width * 0.93, top + height * 0.52)
    path.cubicTo(QPointF(left + width * 0.93, top + height * 0.80),
                 QPointF(left + width * 0.72, top + height * 0.95),
                 QPointF(left + width * 0.5, top + height * 0.99))
    path.cubicTo(QPointF(left + width * 0.28, top + height * 0.95),
                 QPointF(left + width * 0.07, top + height * 0.80),
                 QPointF(left + width * 0.07, top + height * 0.52))
    path.lineTo(left + width * 0.07, top + height * 0.20)
    path.closeSubpath()
    return path


def render_icon(state: str = "running", size: int = 128,
                monochrome: bool = False) -> QIcon:
    """A shield tinted by firewall state, with a bar-chart glyph inside."""
    color = STATE_COLORS.get(state, theme.ACCENT)
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    margin = size * 0.07
    rect = QRectF(margin, margin, size - 2 * margin, size - 2 * margin)
    path = _shield_path(rect)

    if monochrome:
        painter.setBrush(QBrush(theme.color("#ffffff")))
        painter.setPen(Qt.NoPen)
        painter.drawPath(path)
        painter.setCompositionMode(QPainter.CompositionMode_Clear)
    else:
        painter.setBrush(QBrush(theme.color(color, 55)))
        painter.setPen(QPen(theme.color(color), max(2.0, size * 0.055)))
        painter.drawPath(path)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(theme.color(color)))

    # Three ascending bars: this is a monitor, not just a switch.
    base = rect.top() + rect.height() * 0.72
    bar_width = rect.width() * 0.13
    gap = rect.width() * 0.07
    start = rect.left() + rect.width() * 0.5 - (bar_width * 1.5 + gap)
    for index, factor in enumerate((0.20, 0.38, 0.29)):
        height = rect.height() * factor
        painter.drawRoundedRect(
            QRectF(start + index * (bar_width + gap), base - height,
                   bar_width, height),
            bar_width * 0.35, bar_width * 0.35)
    painter.end()
    return QIcon(pixmap)


def app_icon() -> QIcon:
    """Prefer an installed theme icon so KDE keeps it crisp at every size."""
    themed = QIcon.fromTheme("fwpanel")
    if not themed.isNull():
        return themed
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(render_icon("running", size).pixmap(size, size))
    return icon


def tray_icon(state: str) -> QIcon:
    icon = QIcon()
    for size in (22, 32, 48, 64):
        icon.addPixmap(render_icon(state, size).pixmap(size, size))
    return icon


SVG_TEMPLATE = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128">
  <path d="M64 6 L119 27 L119 68 C119 104 91 122 64 127 C37 122 9 104 9 68 L9 27 Z"
        fill="{accent}33" stroke="{accent}" stroke-width="7" stroke-linejoin="round"/>
  <g fill="{accent}">
    <rect x="41" y="72" width="13" height="22" rx="4"/>
    <rect x="58" y="58" width="13" height="36" rx="4"/>
    <rect x="75" y="66" width="13" height="28" rx="4"/>
  </g>
</svg>
"""


def write_svg(path: str, accent: str = theme.ACCENT) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(SVG_TEMPLATE.format(accent=accent))
