"""Connections tab: the live socket table, with a route straight to blocking."""

from __future__ import annotations

import socket

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal, Slot
from PySide6.QtGui import QAction, QGuiApplication
from PySide6.QtWidgets import (QCheckBox, QComboBox, QHBoxLayout, QLabel, QMenu,
                               QMessageBox, QVBoxLayout, QWidget)

from ..util import human_count, is_private_address, service_name_for_port
from . import theme
from .dialogs import BlockAddressDialog, RichRuleDialog
from .widgets import Badge, Card, FilterTable, SearchBox, toolbar_button

STATE_COLOR = {
    "ESTABLISHED": theme.GOOD,
    "LISTEN": theme.INFO,
    "SYN_SENT": theme.WARN,
    "SYN_RECV": theme.WARN,
    "TIME_WAIT": theme.FG_FAINT,
    "CLOSE_WAIT": theme.WARN,
    "FIN_WAIT1": theme.FG_FAINT,
    "FIN_WAIT2": theme.FG_FAINT,
    "CLOSING": theme.FG_FAINT,
    "LAST_ACK": theme.FG_FAINT,
    "UNCONN": theme.FG_FAINT,
}


class _ResolveSignals(QObject):
    done = Signal(str, str)


class _ResolveTask(QRunnable):
    """One reverse lookup, off the GUI thread."""

    def __init__(self, address: str, signals: _ResolveSignals) -> None:
        super().__init__()
        self.address = address
        self.signals = signals
        self.setAutoDelete(True)

    @Slot()
    def run(self) -> None:
        try:
            name = socket.gethostbyaddr(self.address)[0]
        except (OSError, socket.herror, socket.gaierror):
            name = ""
        self.signals.done.emit(self.address, name)


class ConnectionsTab(QWidget):
    COLUMNS = ["Proto", "State", "Local address", "Port", "Remote address",
               "Port", "Service", "Host", "Process", "PID", "Queue"]

    def __init__(self, ctx, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._dns_cache: dict[str, str] = {}
        self._dns_pending: set[str] = set()
        self._pool = QThreadPool()
        self._pool.setMaxThreadCount(4)
        self._resolve_signals = _ResolveSignals()
        self._resolve_signals.done.connect(self._on_resolved)
        self._paused = False

        self._build()
        ctx.net.sampled.connect(self.on_sample)
        ctx.helper.stateChanged.connect(lambda *_: self._update_privilege_badge())

    # -- construction -------------------------------------------------------
    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 14)
        layout.setSpacing(10)

        bar = QHBoxLayout()
        bar.setSpacing(9)

        self.search = SearchBox("Filter by address, port, process…")
        self.search.textChanged.connect(lambda text: self.table.set_filter(text))
        bar.addWidget(self.search, 2)

        self.proto_combo = QComboBox()
        self.proto_combo.addItems(("all protocols", "tcp", "udp"))
        self.proto_combo.currentIndexChanged.connect(self._rerender)
        bar.addWidget(self.proto_combo)

        self.state_combo = QComboBox()
        self.state_combo.addItems(("all states", "ESTABLISHED", "LISTEN", "TIME_WAIT"))
        self.state_combo.currentIndexChanged.connect(self._rerender)
        bar.addWidget(self.state_combo)

        self.hide_local = QCheckBox("Hide loopback")
        self.hide_local.setChecked(True)
        self.hide_local.toggled.connect(self._rerender)
        bar.addWidget(self.hide_local)

        self.resolve_check = QCheckBox("Resolve names")
        self.resolve_check.setToolTip("Reverse-DNS remote addresses in the background.")
        self.resolve_check.toggled.connect(self._rerender)
        bar.addWidget(self.resolve_check)

        self.pause_button = toolbar_button("Pause", "Freeze the table while you work through it")
        self.pause_button.setCheckable(True)
        self.pause_button.toggled.connect(self._on_pause)
        bar.addWidget(self.pause_button)

        bar.addStretch(1)
        self.privilege_badge = Badge("OWN PROCESSES", theme.WARN)
        bar.addWidget(self.privilege_badge)
        layout.addLayout(bar)

        card = Card("live sockets")
        self.count_label = QLabel()
        self.count_label.setObjectName("Hint")
        card.add_header_widget(self.count_label)

        self.table = FilterTable(self.COLUMNS)
        self.table.view.customContextMenuRequested.connect(self._context_menu)
        self.table.view.setSortingEnabled(True)
        self.table.stretch_column(7)  # resolved host name absorbs the slack
        card.body.addWidget(self.table)
        layout.addWidget(card, 1)

        footer = QLabel(
            "Double-click a row to block that remote address. Without elevated "
            "access only your own processes can be named — use Elevate in the toolbar.")
        footer.setObjectName("Hint")
        footer.setWordWrap(True)
        layout.addWidget(footer)

        self.table.doubleClicked.connect(self._block_selected_row)
        self._update_privilege_badge()

    # -- state --------------------------------------------------------------
    def _on_pause(self, paused: bool) -> None:
        self._paused = paused
        self.pause_button.setText("Resume" if paused else "Pause")

    def _update_privilege_badge(self) -> None:
        if self.ctx.helper.elevated:
            self.privilege_badge.set_state("ALL PROCESSES", theme.ACCENT)
        else:
            self.privilege_badge.set_state("OWN PROCESSES", theme.WARN)

    # -- rendering ----------------------------------------------------------
    def on_sample(self, sample) -> None:
        if self._paused:
            return
        self._render(sample)

    def _rerender(self) -> None:
        sample = self.ctx.sample()
        if sample is not None:
            self._render(sample)

    def _filtered(self, sample) -> list:
        proto = self.proto_combo.currentText()
        state = self.state_combo.currentText()
        rows = []
        for conn in sample.connections:
            if proto != "all protocols" and not conn.proto.startswith(proto):
                continue
            if state != "all states" and conn.status != state:
                continue
            if self.hide_local.isChecked():
                if conn.laddr.startswith("127.") or conn.laddr == "::1":
                    continue
                if conn.raddr.startswith("127.") or conn.raddr == "::1":
                    continue
            rows.append(conn)
        return rows

    def _render(self, sample) -> None:
        selected_key = None
        payload = self.table.current_payload()
        if isinstance(payload, tuple):
            selected_key = payload

        scrollbar = self.table.view.verticalScrollBar()
        offset = scrollbar.value()

        rows = self._filtered(sample)
        self.table.clear_rows()
        restore_row = -1

        for conn in rows:
            host = ""
            if self.resolve_check.isChecked() and conn.raddr:
                host = self._hostname(conn.raddr)
            service = service_name_for_port(conn.rport or conn.lport, "tcp"
                                            if conn.proto.startswith("tcp") else "udp")
            colors = {
                1: STATE_COLOR.get(conn.status, theme.FG_DIM),
                4: theme.FG if conn.raddr and not is_private_address(conn.raddr) else theme.FG_DIM,
                8: theme.ACCENT if conn.process else theme.FG_FAINT,
            }
            queue = f"{conn.recvq}/{conn.sendq}" if (conn.recvq or conn.sendq) else ""
            key = conn.key
            row = self.table.append(
                [conn.proto, conn.status, conn.laddr or "*", conn.lport or "*",
                 conn.raddr or "*", conn.rport or "*", service or "",
                 host, conn.process or "—", conn.pid or "", queue],
                colors=colors,
                sort_keys={3: int(conn.lport) if conn.lport.isdigit() else 0,
                           5: int(conn.rport) if conn.rport.isdigit() else 0,
                           9: conn.pid or 0},
                payload=key,
                monospace=[2, 3, 4, 5],
            )
            if key == selected_key:
                restore_row = row

        established = sum(1 for c in rows if c.status == "ESTABLISHED")
        listening = sum(1 for c in rows if c.status == "LISTEN")
        self.count_label.setText(
            f"{len(rows)} shown · {established} established · {listening} listening"
            + (f" · {human_count(sample.conntrack_count)} tracked flows"
               if sample.conntrack_count else ""))

        self.table.resize_columns({2: 150, 4: 150, 8: 130})
        if restore_row >= 0:
            self.table.select_source_row(restore_row)
        scrollbar.setValue(offset)

    # -- dns ----------------------------------------------------------------
    def _hostname(self, address: str) -> str:
        cached = self._dns_cache.get(address)
        if cached is not None:
            return cached
        if address not in self._dns_pending and len(self._dns_pending) < 24:
            self._dns_pending.add(address)
            self._pool.start(_ResolveTask(address, self._resolve_signals))
        return "…"

    @Slot(str, str)
    def _on_resolved(self, address: str, name: str) -> None:
        self._dns_pending.discard(address)
        self._dns_cache[address] = name or "—"

    # -- actions ------------------------------------------------------------
    def _selected_connection(self):
        row = self.table.current_row()
        if row < 0:
            return None
        key = self.table.payload(row)
        sample = self.ctx.sample()
        if sample is None or not isinstance(key, tuple):
            return None
        for conn in sample.connections:
            if conn.key == key:
                return conn
        return None

    def _context_menu(self, pos) -> None:
        row = self.table.row_at(pos)
        if row < 0:
            return
        self.table.select_source_row(row)
        conn = self._selected_connection()
        if conn is None:
            return

        menu = QMenu(self)
        if conn.raddr and conn.raddr not in ("*", ""):
            block = QAction(f"Block {conn.raddr}…", self)
            block.triggered.connect(lambda: self._block_address(conn.raddr))
            menu.addAction(block)

            allow = QAction(f"Allow {conn.raddr} explicitly…", self)
            allow.triggered.connect(lambda: self._rich_rule_for(conn.raddr, "accept"))
            menu.addAction(allow)
            menu.addSeparator()

            copy_addr = QAction("Copy remote address", self)
            copy_addr.triggered.connect(
                lambda: QGuiApplication.clipboard().setText(conn.raddr))
            menu.addAction(copy_addr)

        if conn.status == "LISTEN" and conn.lport:
            close_port = QAction(f"Review zone rules for port {conn.lport}…", self)
            close_port.triggered.connect(lambda: self._explain_listener(conn))
            menu.addAction(close_port)

        copy_row = QAction("Copy row", self)
        copy_row.triggered.connect(lambda: QGuiApplication.clipboard().setText(
            f"{conn.proto} {conn.laddr}:{conn.lport} -> {conn.raddr}:{conn.rport} "
            f"{conn.status} {conn.process}"))
        menu.addAction(copy_row)
        menu.exec(self.table.view.viewport().mapToGlobal(pos))

    def _block_selected_row(self, row: int) -> None:
        conn = self._selected_connection()
        if conn and conn.raddr and conn.raddr not in ("*", ""):
            self._block_address(conn.raddr)

    def _block_address(self, address: str) -> None:
        snap = self.ctx.snapshot()
        zones = sorted(snap.zones) or ["public"]
        dialog = BlockAddressDialog(address, zones, snap.default_zone, self)
        if dialog.exec() != BlockAddressDialog.Accepted:
            return
        rule = dialog.rule_text()
        if dialog.apply_runtime:
            self.ctx.fw.runtime("addRichRule", dialog.zone, rule, dialog.timeout,
                                description=f"block {address} in {dialog.zone}")
        if dialog.apply_permanent:
            self.ctx.fw.perm_zone(dialog.zone, "addRichRule", rule,
                                  description=f"block {address} permanently")

    def _rich_rule_for(self, address: str, action: str) -> None:
        snap = self.ctx.snapshot()
        dialog = RichRuleDialog(snap.default_zone, snap.services, self,
                                preset_source=address, preset_action=action)
        if dialog.exec() != RichRuleDialog.Accepted:
            return
        zone = snap.default_zone
        if dialog.apply_runtime:
            self.ctx.fw.runtime("addRichRule", zone, dialog.rule_text(), 0,
                                description=f"rich rule in {zone}")
        if dialog.apply_permanent:
            self.ctx.fw.perm_zone(zone, "addRichRule", dialog.rule_text(),
                                  description=f"rich rule in {zone} (permanent)")

    def _explain_listener(self, conn) -> None:
        snap = self.ctx.snapshot()
        port = conn.lport
        proto = "tcp" if conn.proto.startswith("tcp") else "udp"
        hits = []
        for zone, settings in sorted(snap.zones.items()):
            for entry in settings.get("ports") or []:
                if str(entry[0]) == str(port) and entry[1] == proto:
                    hits.append(f"• zone '{zone}': port {port}/{proto} is explicitly open")
            for rule in settings.get("rules_str") or []:
                if f'port="{port}"' in str(rule):
                    hits.append(f"• zone '{zone}': matched by a rich rule")
            if settings.get("target") == "ACCEPT":
                hits.append(f"• zone '{zone}': target is ACCEPT, so every port is reachable")
        active = ", ".join(snap.active_zones) or "none"

        text = [f"<b>{conn.process or 'A process'}</b> is listening on "
                f"{conn.laddr}:{port}/{proto}.", ""]
        if hits:
            text.append("Firewall rules that expose it:")
            text.extend(hits)
        else:
            text.append("No zone opens this port explicitly. It may still be "
                        "reachable through a service definition, a rich rule, or "
                        "because the zone target is ACCEPT.")
        text.append("")
        text.append(f"Active zones: {active}")
        QMessageBox.information(self, f"Port {port}/{proto}", "<br>".join(text))
