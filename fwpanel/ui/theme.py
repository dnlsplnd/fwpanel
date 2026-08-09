"""A single, deliberate dark theme.

fwpanel is a monitoring surface first, so the palette is built for long looks
at dense tables and moving charts: a low-chroma slate ground, one saturated
accent for "this is live", and warm/hot hues reserved exclusively for risk.
Colour never decorates here - if something is amber or coral, it means
something.
"""

from __future__ import annotations

import os

from PySide6.QtCore import QPointF, QStandardPaths, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPalette, QPen, QPixmap

# -- ground -----------------------------------------------------------------
BG_DEEP = "#0e1116"      # window
BG = "#141921"           # panels
BG_RAISED = "#1b2230"    # cards, headers
BG_HOVER = "#232c3d"
BORDER = "#262f3f"
BORDER_SOFT = "#1e2634"

# -- ink --------------------------------------------------------------------
FG = "#dce3ee"
FG_DIM = "#8a97ad"
FG_FAINT = "#5c6980"

# -- meaning ----------------------------------------------------------------
ACCENT = "#2ec4b6"       # live / active / primary action
ACCENT_DEEP = "#1c8c82"
INFO = "#4d9de0"         # inbound traffic
VIOLET = "#a78bfa"       # outbound traffic
GOOD = "#7bc96f"         # allowed, healthy
WARN = "#ffb703"         # attention, permissive setting
DANGER = "#ef476f"       # denied, blocked, dangerous setting
NEUTRAL = "#78859c"

SERIES = (INFO, VIOLET, ACCENT, WARN, DANGER, GOOD, "#e07a5f", "#5eead4")

MONO_FAMILIES = ["JetBrains Mono", "Fira Code", "Hack", "DejaVu Sans Mono",
                 "Liberation Mono", "monospace"]


def mono_font(size: int = 10, bold: bool = False) -> QFont:
    font = QFont()
    font.setFamilies(MONO_FAMILIES)
    font.setStyleHint(QFont.Monospace)
    font.setPointSize(size)
    font.setBold(bold)
    return font


def color(name: str, alpha: int | None = None) -> QColor:
    col = QColor(name)
    if alpha is not None:
        col.setAlpha(alpha)
    return col


def apply_palette(app) -> None:
    pal = QPalette()
    pal.setColor(QPalette.Window, QColor(BG_DEEP))
    pal.setColor(QPalette.WindowText, QColor(FG))
    pal.setColor(QPalette.Base, QColor(BG))
    pal.setColor(QPalette.AlternateBase, QColor(BG_RAISED))
    pal.setColor(QPalette.Text, QColor(FG))
    pal.setColor(QPalette.Button, QColor(BG_RAISED))
    pal.setColor(QPalette.ButtonText, QColor(FG))
    pal.setColor(QPalette.Highlight, QColor(ACCENT_DEEP))
    pal.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    pal.setColor(QPalette.ToolTipBase, QColor(BG_RAISED))
    pal.setColor(QPalette.ToolTipText, QColor(FG))
    pal.setColor(QPalette.PlaceholderText, QColor(FG_FAINT))
    pal.setColor(QPalette.Link, QColor(ACCENT))
    pal.setColor(QPalette.Disabled, QPalette.Text, QColor(FG_FAINT))
    pal.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(FG_FAINT))
    pal.setColor(QPalette.Disabled, QPalette.WindowText, QColor(FG_FAINT))
    app.setPalette(pal)


STYLESHEET = f"""
QWidget {{
    background: {BG_DEEP};
    color: {FG};
    font-size: 10pt;
}}
QMainWindow, QDialog {{ background: {BG_DEEP}; }}
/* Labels must not paint the window ground over the card they sit on. */
QLabel {{ background: transparent; }}

/* ---- tabs ---- */
QTabWidget::pane {{
    border: none;
    background: {BG_DEEP};
    top: -1px;
}}
QTabBar {{ background: {BG_DEEP}; qproperty-drawBase: 0; }}
QTabBar::tab {{
    background: transparent;
    color: {FG_DIM};
    padding: 9px 16px;
    margin-right: 2px;
    border: none;
    border-bottom: 2px solid transparent;
    font-weight: 500;
}}
QTabBar::tab:hover {{ color: {FG}; background: {BG}; }}
QTabBar::tab:selected {{
    color: {ACCENT};
    border-bottom: 2px solid {ACCENT};
    background: {BG};
}}

/* ---- cards ---- */
QFrame#Card {{
    background: {BG};
    border: 1px solid {BORDER_SOFT};
    border-radius: 10px;
}}
QFrame#CardHeader {{ background: transparent; border: none; }}
QLabel#CardTitle {{
    color: {FG_DIM};
    font-size: 8pt;
    font-weight: 700;
    letter-spacing: 1.4px;
    text-transform: uppercase;
}}
QLabel#StatValue {{ font-size: 21pt; font-weight: 300; color: {FG}; }}
QLabel#StatUnit {{ font-size: 9pt; color: {FG_DIM}; }}
QLabel#StatCaption {{ font-size: 8.5pt; color: {FG_FAINT}; }}
QLabel#SectionTitle {{ font-size: 12pt; font-weight: 600; color: {FG}; }}
QLabel#Hint {{ color: {FG_FAINT}; font-size: 9pt; }}

/* ---- buttons ---- */
QPushButton {{
    background: {BG_RAISED};
    color: {FG};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 6px 14px;
    font-weight: 500;
}}
QPushButton:hover {{ background: {BG_HOVER}; border-color: {ACCENT_DEEP}; }}
QPushButton:pressed {{ background: {BG}; }}
QPushButton:disabled {{ color: {FG_FAINT}; border-color: {BORDER_SOFT}; background: {BG}; }}
QPushButton[accent="true"] {{
    background: {ACCENT_DEEP}; border-color: {ACCENT}; color: #eafffb;
}}
QPushButton[accent="true"]:hover {{ background: {ACCENT}; color: #04201d; }}
QPushButton[danger="true"] {{ border-color: {DANGER}; color: {DANGER}; }}
QPushButton[danger="true"]:hover {{ background: {DANGER}; color: #1a0009; }}
QPushButton:checked {{ background: {ACCENT_DEEP}; border-color: {ACCENT}; color: #eafffb; }}
QToolButton {{
    background: transparent; border: 1px solid transparent;
    border-radius: 6px; padding: 5px;
}}
QToolButton:hover {{ background: {BG_HOVER}; border-color: {BORDER}; }}
QToolButton:checked {{ background: {ACCENT_DEEP}; }}

/* ---- inputs ---- */
QLineEdit, QComboBox, QSpinBox, QPlainTextEdit, QTextEdit, QAbstractSpinBox {{
    background: {BG_DEEP};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 5px 8px;
    selection-background-color: {ACCENT_DEEP};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QPlainTextEdit:focus {{
    border-color: {ACCENT};
}}
QComboBox::drop-down {{ border: none; width: 20px; }}
QComboBox QAbstractItemView {{
    background: {BG_RAISED};
    border: 1px solid {BORDER};
    selection-background-color: {ACCENT_DEEP};
    outline: none;
    padding: 4px;
}}
QCheckBox, QRadioButton {{ spacing: 8px; background: transparent; }}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 15px; height: 15px;
    border: 1px solid {BORDER}; border-radius: 4px; background: {BG_DEEP};
}}
QRadioButton::indicator {{ border-radius: 8px; }}
QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
    background: {ACCENT}; border-color: {ACCENT};
}}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{ border-color: {ACCENT}; }}

/* ---- tables ---- */
QTableView, QTreeView, QListView {{
    background: {BG};
    alternate-background-color: {BG_DEEP};
    border: 1px solid {BORDER_SOFT};
    border-radius: 8px;
    gridline-color: {BORDER_SOFT};
    selection-background-color: {ACCENT_DEEP};
    selection-color: #ffffff;
    outline: none;
}}
QTableView::item, QTreeView::item {{ padding: 3px 6px; border: none; }}
QTableView::item:hover, QTreeView::item:hover {{ background: {BG_HOVER}; }}
QHeaderView {{ background: transparent; }}
QHeaderView::section {{
    background: {BG_RAISED};
    color: {FG_DIM};
    border: none;
    border-right: 1px solid {BORDER_SOFT};
    border-bottom: 1px solid {BORDER};
    padding: 7px 8px;
    font-size: 8.5pt;
    font-weight: 700;
    letter-spacing: 0.6px;
}}
QHeaderView::section:hover {{ color: {FG}; }}
QTableCornerButton::section {{ background: {BG_RAISED}; border: none; }}

/* ---- scrollbars ---- */
QScrollBar:vertical {{ background: transparent; width: 11px; margin: 2px; }}
QScrollBar:horizontal {{ background: transparent; height: 11px; margin: 2px; }}
QScrollBar::handle {{ background: {BORDER}; border-radius: 5px; min-height: 28px; min-width: 28px; }}
QScrollBar::handle:hover {{ background: {FG_FAINT}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* ---- misc ---- */
QGroupBox {{
    border: 1px solid {BORDER_SOFT};
    border-radius: 8px;
    margin-top: 14px;
    padding-top: 10px;
    background: {BG};
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 12px; top: 2px;
    padding: 0 6px;
    color: {FG_DIM};
    font-size: 8pt;
    font-weight: 700;
    letter-spacing: 1.2px;
}}
QSplitter::handle {{ background: transparent; }}
QSplitter::handle:hover {{ background: {BORDER}; }}
QStatusBar {{ background: {BG}; border-top: 1px solid {BORDER_SOFT}; color: {FG_DIM}; }}
QStatusBar::item {{ border: none; }}
QToolTip {{
    background: {BG_RAISED}; color: {FG};
    border: 1px solid {BORDER}; border-radius: 6px; padding: 6px 8px;
}}
QMenu {{
    background: {BG_RAISED}; border: 1px solid {BORDER};
    border-radius: 8px; padding: 6px;
}}
QMenu::item {{ padding: 6px 26px 6px 22px; border-radius: 5px; }}
QMenu::item:selected {{ background: {ACCENT_DEEP}; }}
QMenu::item:disabled {{ color: {FG_FAINT}; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 5px 8px; }}
QProgressBar {{
    background: {BG_DEEP}; border: 1px solid {BORDER};
    border-radius: 5px; text-align: center; height: 8px;
}}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 4px; }}
"""


def _asset_dir() -> str:
    base = QStandardPaths.writableLocation(QStandardPaths.CacheLocation) or "/tmp"
    path = os.path.join(base, "assets")
    os.makedirs(path, exist_ok=True)
    return path


def _chevron(name: str, ink: str, direction: str = "down") -> str:
    """Draw an arrow to a cached PNG.

    Qt stops drawing a combo box's native arrow as soon as the widget is
    styled at all, and the border-triangle CSS trick renders as a solid block
    on Qt 6. A generated image is the only reliable way to get an arrow that
    matches the palette.
    """
    path = os.path.join(_asset_dir(), f"chevron-{name}.png")
    pixmap = QPixmap(24, 24)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    pen = QPen(QColor(ink), 2.6)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    painter.setPen(pen)
    if direction == "down":
        points = [QPointF(6, 9.5), QPointF(12, 15.5), QPointF(18, 9.5)]
    else:
        points = [QPointF(6, 15.5), QPointF(12, 9.5), QPointF(18, 15.5)]
    painter.drawPolyline(points)
    painter.end()
    pixmap.save(path, "PNG")
    return path


def _arrow_stylesheet() -> str:
    down = _chevron("down", FG_DIM)
    down_hot = _chevron("down-hot", ACCENT)
    up = _chevron("up", FG_DIM, "up")
    return f"""
QComboBox::down-arrow {{
    image: url("{down}");
    width: 11px; height: 11px; margin-right: 7px;
}}
QComboBox::down-arrow:hover, QComboBox::down-arrow:on {{ image: url("{down_hot}"); }}
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{
    background: transparent; border: none; width: 17px;
}}
QAbstractSpinBox::up-arrow {{ image: url("{up}"); width: 10px; height: 10px; }}
QAbstractSpinBox::down-arrow {{ image: url("{down}"); width: 10px; height: 10px; }}
QAbstractSpinBox::up-arrow:hover, QAbstractSpinBox::down-arrow:hover {{
    image: url("{down_hot}");
}}
"""


def apply(app) -> None:
    app.setStyle("Fusion")
    apply_palette(app)
    # Arrow assets need a QGuiApplication, so they are built here, not at import.
    app.setStyleSheet(STYLESHEET + _arrow_stylesheet())
