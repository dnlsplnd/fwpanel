"""The main window: a global control strip above the tab stack."""

from __future__ import annotations

from PySide6.QtCore import QSettings, Qt, Signal
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QMainWindow, QMessageBox,
                               QStatusBar, QTabWidget, QVBoxLayout, QWidget)

from .. import APP_NAME, __version__
from . import icons, theme
from .tab_advanced import AdvancedTab
from .tab_connections import ConnectionsTab
from .tab_dashboard import DashboardTab
from .tab_logs import LogsTab
from .tab_rules import RulesTab
from .tab_ruleset import RulesetTab
from .tab_services import ServicesTab
from .tab_traffic import TrafficTab
from .tab_zones import ZonesTab
from .widgets import Badge, toolbar_button


class MainWindow(QMainWindow):
    closedToTray = Signal()

    def __init__(self, ctx, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._quitting = False
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(icons.app_icon())
        self.resize(1440, 900)
        self.setMinimumSize(1040, 680)

        self._build()
        ctx.fw.connectionChanged.connect(self._on_connection)
        ctx.fw.snapshotChanged.connect(self._on_snapshot)
        ctx.fw.operationFinished.connect(self._on_operation)
        ctx.helper.stateChanged.connect(self._on_helper_state)
        ctx.helper.failed.connect(
            lambda cmd, err: self.status.showMessage(f"helper: {cmd}: {err}", 8000))
        self._restore_geometry()

    # -- construction -------------------------------------------------------
    def _build(self) -> None:
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        layout.addWidget(self._build_control_strip())

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.dashboard = DashboardTab(self.ctx)
        self.traffic = TrafficTab(self.ctx)
        self.connections = ConnectionsTab(self.ctx)
        self.zones = ZonesTab(self.ctx)
        self.rules = RulesTab(self.ctx)
        self.services = ServicesTab(self.ctx)
        self.advanced = AdvancedTab(self.ctx)
        self.logs = LogsTab(self.ctx)
        self.ruleset = RulesetTab(self.ctx)

        for widget, label in (
            (self.dashboard, "Overview"),
            (self.traffic, "Traffic"),
            (self.connections, "Connections"),
            (self.zones, "Zones"),
            (self.rules, "Rules"),
            (self.services, "Services"),
            (self.advanced, "Advanced"),
            (self.logs, "Logs"),
            (self.ruleset, "Ruleset"),
        ):
            self.tabs.addTab(widget, label)
        layout.addWidget(self.tabs, 1)
        self.setCentralWidget(central)

        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.status_left = QLabel(f"{APP_NAME} {__version__}")
        self.status.addWidget(self.status_left)
        self.status_right = QLabel()
        self.status.addPermanentWidget(self.status_right)

    def _build_control_strip(self) -> QWidget:
        strip = QWidget()
        strip.setStyleSheet(
            f"background: {theme.BG}; border-bottom: 1px solid {theme.BORDER_SOFT};")
        row = QHBoxLayout(strip)
        row.setContentsMargins(14, 9, 14, 9)
        row.setSpacing(9)

        title = QLabel(APP_NAME)
        title.setStyleSheet(
            f"color: {theme.FG}; font-size: 11pt; font-weight: 600; letter-spacing: 0.3px;")
        row.addWidget(title)

        self.connection_badge = Badge("CONNECTING", theme.NEUTRAL)
        row.addWidget(self.connection_badge)
        self.zone_badge = Badge("", theme.INFO)
        self.zone_badge.hide()
        row.addWidget(self.zone_badge)
        self.drift_badge = Badge("", theme.WARN)
        self.drift_badge.hide()
        row.addWidget(self.drift_badge)
        row.addStretch(1)

        self.reload_button = toolbar_button(
            "Reload", "Discard runtime changes and re-apply the permanent configuration")
        self.reload_button.clicked.connect(self.reload_firewall)
        row.addWidget(self.reload_button)

        self.persist_button = toolbar_button(
            "Runtime → Permanent", "Write the current runtime configuration to disk")
        self.persist_button.clicked.connect(self.persist_runtime)
        row.addWidget(self.persist_button)

        self.elevate_button = toolbar_button(
            "Elevate", "Start the privileged helper for conntrack, full socket "
                       "ownership and the raw nftables ruleset", accent=True)
        self.elevate_button.clicked.connect(self.ctx.helper.start)
        row.addWidget(self.elevate_button)

        self.panic_button = toolbar_button(
            "Panic mode", "Drop every packet, in and out", danger=True)
        self.panic_button.setCheckable(True)
        self.panic_button.clicked.connect(self.toggle_panic)
        row.addWidget(self.panic_button)
        return strip

    # -- global actions -----------------------------------------------------
    def reload_firewall(self) -> None:
        if QMessageBox.question(
                self, "Reload firewall",
                "Re-apply the permanent configuration.\n\nAny runtime-only change "
                "that has not been saved will be lost.") != QMessageBox.Yes:
            return
        self.ctx.fw.runtime("reload", description="reload firewall")

    def persist_runtime(self) -> None:
        if QMessageBox.question(
                self, "Save runtime configuration",
                "Write the entire current runtime configuration to disk?\n\n"
                "This overwrites the permanent configuration of every zone."
                ) != QMessageBox.Yes:
            return
        self.ctx.fw.runtime("runtimeToPermanent",
                            description="save runtime configuration")

    def toggle_panic(self, checked: bool) -> None:
        if checked:
            confirmed = QMessageBox.warning(
                self, "Enable panic mode",
                "Panic mode drops every packet in both directions. You will lose "
                "all network access, including remote sessions to this machine, "
                "until you turn it off.\n\nEnable it?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if confirmed != QMessageBox.Yes:
                self.panic_button.setChecked(False)
                return
            self.ctx.fw.runtime("enablePanicMode", description="enable panic mode")
        else:
            self.ctx.fw.runtime("disablePanicMode", description="disable panic mode")

    def set_default_zone(self, zone: str) -> None:
        self.ctx.fw.runtime("setDefaultZone", zone,
                            description=f"set default zone to {zone}")

    # -- reactions ----------------------------------------------------------
    def _on_connection(self, connected: bool, message: str) -> None:
        if connected:
            self.connection_badge.set_state("CONNECTED", theme.ACCENT)
        else:
            self.connection_badge.set_state("DISCONNECTED", theme.DANGER)
            self.status.showMessage(message or "firewalld is unavailable", 0)
        for button in (self.reload_button, self.persist_button, self.panic_button):
            button.setEnabled(connected)

    def _on_snapshot(self, snap) -> None:
        from .. import analysis

        if snap.ok:
            self.connection_badge.set_state(
                "PANIC MODE" if snap.panic else "PROTECTED",
                theme.DANGER if snap.panic else theme.ACCENT)
            self.zone_badge.set_state(f"DEFAULT · {snap.default_zone}", theme.INFO)
            self.zone_badge.show()

            self.panic_button.blockSignals(True)
            self.panic_button.setChecked(snap.panic)
            self.panic_button.blockSignals(False)

            drift = analysis.zone_drift(snap)
            if drift:
                self.drift_badge.set_state(f"{len(drift)} UNSAVED", theme.WARN)
                self.drift_badge.setToolTip(
                    "Runtime differs from the saved configuration in: "
                    + ", ".join(sorted(drift))
                    + "\nUse Runtime → Permanent to keep these changes.")
                self.drift_badge.show()
            else:
                self.drift_badge.hide()

            active = ", ".join(f"{z} ({', '.join(v.get('interfaces', []) or ['—'])})"
                               for z, v in sorted(snap.active_zones.items()))
            self.status_right.setText(
                f"firewalld {snap.version} · {len(snap.zones)} zones · "
                f"log denied: {snap.log_denied} · {active}")
        else:
            self.connection_badge.set_state("UNAVAILABLE", theme.DANGER)
            self.status_right.setText(snap.error[:120])

    def _on_operation(self, description: str, ok: bool, message: str) -> None:
        if ok:
            self.status.showMessage(f"✓ {description}", 4000)
        else:
            self.status.showMessage(f"✗ {description}: {message}", 9000)

    def _on_helper_state(self, elevated: bool, message: str) -> None:
        self.elevate_button.setEnabled(not elevated)
        self.elevate_button.setText("Elevated" if elevated else "Elevate")
        self.status.showMessage(
            "Privileged helper running — full socket ownership, conntrack and "
            "the raw ruleset are available." if elevated
            else f"Elevation unavailable: {message}", 7000)

    # -- window state -------------------------------------------------------
    def _settings(self) -> QSettings:
        return QSettings("fwpanel", "fwpanel")

    def _restore_geometry(self) -> None:
        geometry = self._settings().value("window/geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)

    def prepare_quit(self) -> None:
        self._quitting = True

    def closeEvent(self, event) -> None:
        self._settings().setValue("window/geometry", self.saveGeometry())
        if self._quitting:
            self.logs.shutdown()
            event.accept()
            return
        event.ignore()
        self.hide()
        self.closedToTray.emit()
