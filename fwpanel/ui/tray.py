"""System tray presence.

Plasma exposes Qt's QSystemTrayIcon through StatusNotifierItem, so the icon
lands in the panel's system tray and the menu is a real Plasma menu. The icon
colour tracks firewall state, and the tooltip carries live throughput so the
common question - "is anything moving?" - is answered without opening a window.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from ..util import human_rate
from . import icons


class Tray(QObject):
    showRequested = Signal()
    quitRequested = Signal()
    reloadRequested = Signal()
    persistRequested = Signal()
    panicToggled = Signal(bool)
    defaultZoneRequested = Signal(str)

    def __init__(self, ctx, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._state = ""
        self._zone_actions: dict[str, QAction] = {}

        self.icon = QSystemTrayIcon(icons.tray_icon("offline"))
        self.icon.setToolTip("Firewall Panel")
        self.icon.activated.connect(self._on_activated)

        self.menu = QMenu()
        self._build_menu()
        self.icon.setContextMenu(self.menu)

        ctx.fw.snapshotChanged.connect(self.on_snapshot)
        ctx.fw.connectionChanged.connect(self._on_connection)
        ctx.net.sampled.connect(self.on_sample)

    # -- menu ---------------------------------------------------------------
    def _build_menu(self) -> None:
        self.status_action = QAction("Connecting to firewalld…", self.menu)
        self.status_action.setEnabled(False)
        self.menu.addAction(self.status_action)
        self.menu.addSeparator()

        show = QAction("Open Firewall Panel", self.menu)
        show.triggered.connect(self.showRequested)
        self.menu.addAction(show)

        self.zone_menu = QMenu("Default zone", self.menu)
        self.zone_group = QActionGroup(self.zone_menu)
        self.zone_group.setExclusive(True)
        self.menu.addMenu(self.zone_menu)
        self.menu.addSeparator()

        reload_action = QAction("Reload firewall", self.menu)
        reload_action.setToolTip("Re-apply the permanent configuration")
        reload_action.triggered.connect(self.reloadRequested)
        self.menu.addAction(reload_action)

        persist_action = QAction("Save runtime to permanent", self.menu)
        persist_action.triggered.connect(self.persistRequested)
        self.menu.addAction(persist_action)

        self.panic_action = QAction("Panic mode (block all traffic)", self.menu)
        self.panic_action.setCheckable(True)
        self.panic_action.toggled.connect(self.panicToggled)
        self.menu.addAction(self.panic_action)
        self.menu.addSeparator()

        quit_action = QAction("Quit", self.menu)
        quit_action.triggered.connect(self.quitRequested)
        self.menu.addAction(quit_action)

    def _on_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.showRequested.emit()

    # -- state --------------------------------------------------------------
    def show(self) -> None:
        self.icon.show()

    def hide(self) -> None:
        self.icon.hide()

    def _set_state(self, state: str) -> None:
        if state == self._state:
            return
        self._state = state
        self.icon.setIcon(icons.tray_icon(state))

    def _on_connection(self, connected: bool, message: str) -> None:
        if not connected:
            self._set_state("offline")
            self.status_action.setText(f"firewalld unavailable — {message}"
                                       if message else "firewalld unavailable")

    def on_snapshot(self, snap) -> None:
        if not snap.ok:
            self._set_state("offline")
            self.status_action.setText("firewalld unavailable")
            return

        self._set_state("panic" if snap.panic else "running")
        active = ", ".join(sorted(snap.active_zones)) or "no active zone"
        self.status_action.setText(
            f"{'PANIC MODE' if snap.panic else 'Protected'} · "
            f"default {snap.default_zone} · {active}")

        self.panic_action.blockSignals(True)
        self.panic_action.setChecked(snap.panic)
        self.panic_action.blockSignals(False)

        zones = sorted(snap.zones)
        if list(self._zone_actions) != zones:
            for action in list(self._zone_actions.values()):
                self.zone_group.removeAction(action)
                self.zone_menu.removeAction(action)
            self._zone_actions.clear()
            for zone in zones:
                action = QAction(zone, self.zone_menu)
                action.setCheckable(True)
                action.triggered.connect(
                    lambda _checked, z=zone: self.defaultZoneRequested.emit(z))
                self.zone_group.addAction(action)
                self.zone_menu.addAction(action)
                self._zone_actions[zone] = action
        for zone, action in self._zone_actions.items():
            action.blockSignals(True)
            action.setChecked(zone == snap.default_zone)
            action.blockSignals(False)

    def on_sample(self, sample) -> None:
        snap = self.ctx.snapshot()
        lines = ["Firewall Panel"]
        if snap.ok:
            lines.append("PANIC MODE — all traffic blocked" if snap.panic
                         else f"default zone: {snap.default_zone}")
        else:
            lines.append("firewalld unavailable")
        lines.append(f"↓ {human_rate(sample.total_rx_rate)}   "
                     f"↑ {human_rate(sample.total_tx_rate)}")
        lines.append(f"{sample.established} established · {sample.listening} listening")
        self.icon.setToolTip("\n".join(lines))

    def notify(self, title: str, message: str,
               level=QSystemTrayIcon.Information) -> None:
        if self.icon.isVisible():
            self.icon.showMessage(title, message, level, 5000)

    @staticmethod
    def available() -> bool:
        return QSystemTrayIcon.isSystemTrayAvailable()
