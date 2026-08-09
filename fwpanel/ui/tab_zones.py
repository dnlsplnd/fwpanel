"""Zones tab: the full zone editor, runtime and permanent side by side."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QHBoxLayout,
                               QInputDialog, QLabel, QListWidget,
                               QListWidgetItem, QMenu, QMessageBox,
                               QPushButton, QSplitter, QTabWidget, QVBoxLayout,
                               QWidget)

from .. import analysis
from ..util import elide, service_name_for_port
from . import theme
from .dialogs import (ForwardPortDialog, PortDialog, ProtocolDialog,
                      RichRuleDialog, SourceDialog, TARGETS)
from .widgets import (Badge, Card, FilterTable, KeyValueGrid, SearchBox,
                      toolbar_button)

RUNTIME, PERMANENT, BOTH = "runtime", "permanent", "both"


class ZonesTab(QWidget):
    zoneSelected = Signal(str)

    def __init__(self, ctx, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.mode = BOTH
        self._zone = ""
        self._service_signature: tuple = ()
        self._loading = False
        self._build()
        ctx.fw.snapshotChanged.connect(self.on_snapshot)

    # -- construction -------------------------------------------------------
    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 14)
        layout.setSpacing(10)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        bar.addWidget(QLabel("Editing"))
        self.mode_group = QButtonGroup(self)
        self.mode_group.setExclusive(True)
        for label, mode, tip in (
            ("Runtime", RUNTIME, "Takes effect immediately, lost on reload"),
            ("Permanent", PERMANENT, "Written to disk, applied on reload"),
            ("Both", BOTH, "Apply immediately and write to disk"),
        ):
            button = QPushButton(label)
            button.setCheckable(True)
            button.setToolTip(tip)
            button.setChecked(mode == self.mode)
            button.clicked.connect(lambda _checked, m=mode: self._set_mode(m))
            self.mode_group.addButton(button)
            bar.addWidget(button)

        bar.addStretch(1)
        self.drift_badge = Badge("", theme.WARN)
        self.drift_badge.hide()
        bar.addWidget(self.drift_badge)

        self.default_button = toolbar_button("Set as default zone")
        self.default_button.clicked.connect(self._set_default_zone)
        bar.addWidget(self.default_button)

        self.reset_button = toolbar_button("Reset to defaults", danger=True)
        self.reset_button.setToolTip("Discard permanent customisations for this zone")
        self.reset_button.clicked.connect(self._reset_zone)
        bar.addWidget(self.reset_button)
        layout.addLayout(bar)

        splitter = QSplitter(Qt.Horizontal)
        layout.addWidget(splitter, 1)

        # --- zone list ---
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(8)
        self.zone_search = SearchBox("Find a zone…")
        self.zone_search.textChanged.connect(self._filter_zones)
        left_layout.addWidget(self.zone_search)

        self.zone_list = QListWidget()
        self.zone_list.currentItemChanged.connect(self._on_zone_changed)
        self.zone_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.zone_list.customContextMenuRequested.connect(self._zone_menu)
        left_layout.addWidget(self.zone_list, 1)

        new_zone = toolbar_button("New zone…")
        new_zone.clicked.connect(self._create_zone)
        left_layout.addWidget(new_zone)
        splitter.addWidget(left)

        # --- detail ---
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(10)

        header = Card("zone")
        self.zone_title = QLabel("—")
        self.zone_title.setObjectName("SectionTitle")
        header.body.addWidget(self.zone_title)
        badge_row = QHBoxLayout()
        badge_row.setSpacing(6)
        self.badge_active = Badge("INACTIVE", theme.FG_FAINT)
        self.badge_default = Badge("", theme.ACCENT)
        self.badge_target = Badge("", theme.NEUTRAL)
        self.badge_drift = Badge("UNSAVED CHANGES", theme.WARN)
        for badge in (self.badge_active, self.badge_default, self.badge_target,
                      self.badge_drift):
            badge_row.addWidget(badge)
        badge_row.addStretch(1)
        header.body.addLayout(badge_row)
        self.zone_description = QLabel()
        self.zone_description.setWordWrap(True)
        self.zone_description.setObjectName("Hint")
        header.body.addWidget(self.zone_description)
        right_layout.addWidget(header)

        self.sub = QTabWidget()
        self.sub.addTab(self._build_services(), "Services")
        self.sub.addTab(self._build_ports(), "Ports")
        self.sub.addTab(self._build_rules(), "Rich rules")
        self.sub.addTab(self._build_bindings(), "Bindings")
        self.sub.addTab(self._build_options(), "Options")
        right_layout.addWidget(self.sub, 1)
        splitter.addWidget(right)

        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 4)
        splitter.setSizes([230, 900])

    def _build_services(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 10, 0, 0)
        layout.setSpacing(8)

        row = QHBoxLayout()
        self.service_search = SearchBox("Find a service…")
        self.service_search.textChanged.connect(self._filter_services)
        row.addWidget(self.service_search, 1)
        self.only_enabled = QCheckBox("Only enabled")
        self.only_enabled.toggled.connect(self._filter_services)
        row.addWidget(self.only_enabled)
        self.service_count = QLabel()
        self.service_count.setObjectName("Hint")
        row.addWidget(self.service_count)
        layout.addLayout(row)

        self.service_list = QListWidget()
        self.service_list.itemChanged.connect(self._on_service_toggled)
        self.service_list.setAlternatingRowColors(True)
        layout.addWidget(self.service_list, 1)

        hint = QLabel("A service bundles the ports, protocols and helpers an "
                      "application needs. Prefer these over raw ports.")
        hint.setObjectName("Hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        return page

    def _build_ports(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 10, 0, 0)
        layout.setSpacing(10)

        ports_card = Card("open ports")
        row = QHBoxLayout()
        add_port = toolbar_button("Add port…", accent=True)
        add_port.clicked.connect(self._add_port)
        row.addWidget(add_port)
        remove_port = toolbar_button("Remove", danger=True)
        remove_port.clicked.connect(self._remove_port)
        row.addWidget(remove_port)
        row.addStretch(1)
        ports_card.body.addLayout(row)
        self.ports_table = FilterTable(["Port", "Protocol", "Well-known service", "Scope"])
        self.ports_table.setMinimumHeight(120)
        ports_card.body.addWidget(self.ports_table)
        layout.addWidget(ports_card)

        forwards_card = Card("port forwarding")
        frow = QHBoxLayout()
        add_forward = toolbar_button("Add forward…", accent=True)
        add_forward.clicked.connect(self._add_forward)
        frow.addWidget(add_forward)
        remove_forward = toolbar_button("Remove", danger=True)
        remove_forward.clicked.connect(self._remove_forward)
        frow.addWidget(remove_forward)
        frow.addStretch(1)
        forwards_card.body.addLayout(frow)
        self.forwards_table = FilterTable(["Port", "Protocol", "To port", "To address", "Scope"])
        self.forwards_table.setMinimumHeight(100)
        forwards_card.body.addWidget(self.forwards_table)
        layout.addWidget(forwards_card)

        proto_card = Card("protocols and source ports")
        prow = QHBoxLayout()
        add_proto = toolbar_button("Add protocol…")
        add_proto.clicked.connect(self._add_protocol)
        prow.addWidget(add_proto)
        add_source_port = toolbar_button("Add source port…")
        add_source_port.clicked.connect(self._add_source_port)
        prow.addWidget(add_source_port)
        remove_extra = toolbar_button("Remove", danger=True)
        remove_extra.clicked.connect(self._remove_extra)
        prow.addWidget(remove_extra)
        prow.addStretch(1)
        proto_card.body.addLayout(prow)
        self.extras_table = FilterTable(["Kind", "Value", "Scope"])
        self.extras_table.setMinimumHeight(100)
        proto_card.body.addWidget(self.extras_table)
        layout.addWidget(proto_card)
        return page

    def _build_rules(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 10, 0, 0)
        layout.setSpacing(8)

        row = QHBoxLayout()
        add_rule = toolbar_button("New rule…", accent=True)
        add_rule.clicked.connect(self._add_rich_rule)
        row.addWidget(add_rule)
        edit_rule = toolbar_button("Edit…")
        edit_rule.clicked.connect(self._edit_rich_rule)
        row.addWidget(edit_rule)
        remove_rule = toolbar_button("Remove", danger=True)
        remove_rule.clicked.connect(self._remove_rich_rule)
        row.addWidget(remove_rule)
        row.addStretch(1)
        self.rules_search = SearchBox("Filter rules…")
        self.rules_search.textChanged.connect(lambda t: self.rules_table.set_filter(t))
        row.addWidget(self.rules_search)
        layout.addLayout(row)

        self.rules_table = FilterTable(["Action", "Source", "Match", "Scope", "Rule"])
        layout.addWidget(self.rules_table, 1)

        hint = QLabel("Rich rules are evaluated before the zone's service and "
                      "port lists. Double-click a rule to edit it.")
        hint.setObjectName("Hint")
        layout.addWidget(hint)
        self.rules_table.doubleClicked.connect(lambda _row: self._edit_rich_rule())
        return page

    def _build_bindings(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 10, 0, 0)
        layout.setSpacing(10)

        iface_card = Card("interfaces")
        row = QHBoxLayout()
        self.iface_combo = QComboBox()
        self.iface_combo.setEditable(True)
        self.iface_combo.setMinimumWidth(160)
        row.addWidget(self.iface_combo)
        bind = toolbar_button("Bind interface", accent=True)
        bind.clicked.connect(self._bind_interface)
        row.addWidget(bind)
        unbind = toolbar_button("Unbind selected", danger=True)
        unbind.clicked.connect(self._unbind_interface)
        row.addWidget(unbind)
        row.addStretch(1)
        iface_card.body.addLayout(row)
        self.iface_table = FilterTable(["Interface", "Scope", "Currently in zone"])
        self.iface_table.setMinimumHeight(110)
        iface_card.body.addWidget(self.iface_table)
        layout.addWidget(iface_card)

        source_card = Card("sources")
        srow = QHBoxLayout()
        add_source = toolbar_button("Add source…", accent=True)
        add_source.clicked.connect(self._add_source)
        srow.addWidget(add_source)
        remove_source = toolbar_button("Remove", danger=True)
        remove_source.clicked.connect(self._remove_source)
        srow.addWidget(remove_source)
        srow.addStretch(1)
        source_card.body.addLayout(srow)
        self.sources_table = FilterTable(["Source", "Scope"])
        self.sources_table.setMinimumHeight(110)
        source_card.body.addWidget(self.sources_table)
        layout.addWidget(source_card)
        layout.addStretch(1)
        return page

    def _build_options(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 10, 0, 0)
        layout.setSpacing(10)

        behaviour = Card("behaviour")
        target_row = QHBoxLayout()
        target_row.addWidget(QLabel("Target for unmatched packets"))
        self.target_combo = QComboBox()
        self.target_combo.addItems(TARGETS)
        self.target_combo.activated.connect(self._set_target)
        target_row.addWidget(self.target_combo)
        target_row.addStretch(1)
        behaviour.body.addLayout(target_row)

        self.masquerade_check = QCheckBox(
            "Masquerade — rewrite the source address of forwarded traffic (NAT)")
        self.masquerade_check.clicked.connect(self._toggle_masquerade)
        behaviour.body.addWidget(self.masquerade_check)

        self.forward_check = QCheckBox(
            "Intra-zone forwarding — let interfaces in this zone talk to each other")
        self.forward_check.clicked.connect(self._toggle_forward)
        behaviour.body.addWidget(self.forward_check)

        self.inversion_check = QCheckBox(
            "Invert the ICMP block list — block everything except the listed types")
        self.inversion_check.clicked.connect(self._toggle_inversion)
        behaviour.body.addWidget(self.inversion_check)
        layout.addWidget(behaviour)

        icmp_card = Card("blocked ICMP types")
        irow = QHBoxLayout()
        self.icmp_combo = QComboBox()
        self.icmp_combo.setMinimumWidth(220)
        irow.addWidget(self.icmp_combo)
        block_icmp = toolbar_button("Block type", accent=True)
        block_icmp.clicked.connect(self._add_icmp_block)
        irow.addWidget(block_icmp)
        unblock_icmp = toolbar_button("Unblock selected", danger=True)
        unblock_icmp.clicked.connect(self._remove_icmp_block)
        irow.addWidget(unblock_icmp)
        irow.addStretch(1)
        icmp_card.body.addLayout(irow)
        self.icmp_table = FilterTable(["ICMP type", "Scope"])
        self.icmp_table.setMinimumHeight(110)
        icmp_card.body.addWidget(self.icmp_table)
        layout.addWidget(icmp_card)

        summary_card = Card("summary")
        self.summary_grid = KeyValueGrid()
        summary_card.body.addWidget(self.summary_grid)
        layout.addWidget(summary_card)
        layout.addStretch(1)
        return page

    # -- mode ---------------------------------------------------------------
    def _set_mode(self, mode: str) -> None:
        self.mode = mode
        self._service_signature = ()
        self._render_zone()

    @property
    def _edit_runtime(self) -> bool:
        return self.mode in (RUNTIME, BOTH)

    @property
    def _edit_permanent(self) -> bool:
        return self.mode in (PERMANENT, BOTH)

    def _view_settings(self, zone: str = "") -> dict:
        snap = self.ctx.snapshot()
        zone = zone or self._zone
        if self.mode == PERMANENT:
            return snap.perm_zones.get(zone, {})
        return snap.zones.get(zone, {})

    # -- snapshot -----------------------------------------------------------
    def on_snapshot(self, snap) -> None:
        self._sync_zone_list(snap)
        self._render_zone()

        drift = analysis.zone_drift(snap)
        if drift:
            self.drift_badge.set_state(f"{len(drift)} ZONE(S) UNSAVED", theme.WARN)
            self.drift_badge.setToolTip(
                "Runtime differs from the permanent configuration in: "
                + ", ".join(sorted(drift)))
            self.drift_badge.show()
        else:
            self.drift_badge.hide()

    def _sync_zone_list(self, snap) -> None:
        names = sorted(set(snap.zones) | set(snap.perm_zones))
        existing = [self.zone_list.item(i).data(Qt.UserRole)
                    for i in range(self.zone_list.count())]
        drift = analysis.zone_drift(snap)
        if names != existing:
            current = self._zone
            self.zone_list.blockSignals(True)
            self.zone_list.clear()
            for name in names:
                item = QListWidgetItem(name)
                item.setData(Qt.UserRole, name)
                self.zone_list.addItem(item)
            self.zone_list.blockSignals(False)
            index = names.index(current) if current in names else (
                names.index(snap.default_zone) if snap.default_zone in names else 0)
            if names:
                self.zone_list.setCurrentRow(index)

        for i in range(self.zone_list.count()):
            item = self.zone_list.item(i)
            name = item.data(Qt.UserRole)
            marks = []
            if name in snap.active_zones:
                marks.append("●")
            if name == snap.default_zone:
                marks.append("default")
            if name in drift:
                marks.append("*")
            item.setText(f"{name}  {' '.join(marks)}".rstrip())
            if name in snap.active_zones:
                item.setForeground(theme.color(theme.ACCENT))
            elif name in drift:
                item.setForeground(theme.color(theme.WARN))
            else:
                item.setForeground(theme.color(theme.FG_DIM))
        self._sync_interface_combo(snap)

    def _sync_interface_combo(self, snap) -> None:
        sample = self.ctx.sample()
        names = sorted(sample.interfaces) if sample else []
        current = self.iface_combo.currentText()
        if [self.iface_combo.itemText(i) for i in range(self.iface_combo.count())] != names:
            self.iface_combo.blockSignals(True)
            self.iface_combo.clear()
            self.iface_combo.addItems(names)
            if current:
                self.iface_combo.setCurrentText(current)
            self.iface_combo.blockSignals(False)

        icmp_types = snap.icmptypes
        if [self.icmp_combo.itemText(i) for i in range(self.icmp_combo.count())] != icmp_types:
            self.icmp_combo.clear()
            self.icmp_combo.addItems(icmp_types)

    def _on_zone_changed(self, current, _previous) -> None:
        self._zone = current.data(Qt.UserRole) if current else ""
        self._service_signature = ()
        self._render_zone()
        self.zoneSelected.emit(self._zone)

    def _filter_zones(self, text: str) -> None:
        text = text.lower()
        for i in range(self.zone_list.count()):
            item = self.zone_list.item(i)
            item.setHidden(bool(text) and text not in item.data(Qt.UserRole).lower())

    # -- rendering ----------------------------------------------------------
    def _render_zone(self) -> None:
        snap = self.ctx.snapshot()
        zone = self._zone
        settings = self._view_settings()
        permanent = snap.perm_zones.get(zone, {})
        runtime = snap.zones.get(zone, {})

        self.zone_title.setText(zone or "—")
        self.zone_description.setText(settings.get("description", "") or
                                      "No description in the zone definition.")

        active = zone in snap.active_zones
        bound = snap.active_zones.get(zone, {})
        where = ", ".join(bound.get("interfaces", []) + bound.get("sources", []))
        self.badge_active.set_state(f"ACTIVE · {where}" if active else "INACTIVE",
                                    theme.ACCENT if active else theme.FG_FAINT)
        self.badge_default.setVisible(zone == snap.default_zone)
        self.badge_default.set_state("DEFAULT ZONE", theme.INFO)

        target = settings.get("target", "default")
        target_color = theme.DANGER if target == "ACCEPT" else (
            theme.GOOD if target in ("DROP", "%%REJECT%%") else theme.NEUTRAL)
        self.badge_target.set_state(f"TARGET {target}", target_color)

        drift = analysis.zone_drift(snap)
        self.badge_drift.setVisible(zone in drift)
        self.default_button.setEnabled(bool(zone) and zone != snap.default_zone)
        self.reset_button.setEnabled(bool(zone))

        self._render_services(snap, settings)
        self._render_ports(runtime, permanent, settings)
        self._render_rules(runtime, permanent, settings)
        self._render_bindings(snap, runtime, permanent, settings)
        self._render_options(snap, settings, runtime, permanent)

    def _scope_of(self, value, runtime_list, permanent_list) -> tuple[str, str]:
        """Label an entry with where it lives, so drift is visible in place."""
        in_runtime = value in (runtime_list or [])
        in_permanent = value in (permanent_list or [])
        if in_runtime and in_permanent:
            return "runtime + permanent", theme.FG_DIM
        if in_runtime:
            return "runtime only", theme.WARN
        return "permanent only", theme.INFO

    def _render_services(self, snap, settings) -> None:
        enabled = set(settings.get("services") or [])
        signature = (self._zone, self.mode, tuple(sorted(enabled)), len(snap.services))
        if signature == self._service_signature:
            return
        self._service_signature = signature

        self._loading = True
        self.service_list.clear()
        runtime_services = set(snap.zones.get(self._zone, {}).get("services") or [])
        perm_services = set(snap.perm_zones.get(self._zone, {}).get("services") or [])
        for name in snap.services:
            item = QListWidgetItem(name)
            item.setData(Qt.UserRole, name)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if name in enabled else Qt.Unchecked)
            if name in analysis.SENSITIVE_SERVICES and name in enabled:
                item.setForeground(theme.color(theme.WARN))
                item.setToolTip(f"{analysis.SENSITIVE_SERVICES[name]} — review who can reach it")
            elif name in runtime_services and name not in perm_services:
                item.setForeground(theme.color(theme.WARN))
                item.setToolTip("Enabled at runtime only — lost on reload")
            elif name in perm_services and name not in runtime_services:
                item.setForeground(theme.color(theme.INFO))
                item.setToolTip("Permanent only — not active until reload")
            self.service_list.addItem(item)
        self._loading = False
        self.service_count.setText(f"{len(enabled)} of {len(snap.services)} enabled")
        self._filter_services()

    def _filter_services(self) -> None:
        text = self.service_search.text().lower()
        only_enabled = self.only_enabled.isChecked()
        for i in range(self.service_list.count()):
            item = self.service_list.item(i)
            hidden = bool(text) and text not in item.data(Qt.UserRole).lower()
            if only_enabled and item.checkState() != Qt.Checked:
                hidden = True
            item.setHidden(hidden)

    def _render_ports(self, runtime, permanent, settings) -> None:
        self.ports_table.clear_rows()
        for entry in settings.get("ports") or []:
            port, proto = (list(entry) + ["", ""])[:2]
            scope, color = self._scope_of([port, proto], runtime.get("ports"),
                                          permanent.get("ports"))
            self.ports_table.append(
                [port, proto, service_name_for_port(port, proto) or "—", scope],
                colors={3: color}, payload=(port, proto), monospace=[0])
        self.ports_table.resize_columns()

        self.forwards_table.clear_rows()
        for entry in settings.get("forward_ports") or []:
            port, proto, toport, toaddr = (list(entry) + ["", "", "", ""])[:4]
            scope, color = self._scope_of(list(entry), runtime.get("forward_ports"),
                                          permanent.get("forward_ports"))
            self.forwards_table.append(
                [port, proto, toport or port, toaddr or "this host", scope],
                colors={4: color}, payload=(port, proto, toport, toaddr), monospace=[0, 2, 3])
        self.forwards_table.resize_columns()

        self.extras_table.clear_rows()
        for proto in settings.get("protocols") or []:
            scope, color = self._scope_of(proto, runtime.get("protocols"),
                                          permanent.get("protocols"))
            self.extras_table.append(["protocol", proto, scope], colors={2: color},
                                     payload=("protocol", proto))
        for entry in settings.get("source_ports") or []:
            port, proto = (list(entry) + ["", ""])[:2]
            scope, color = self._scope_of([port, proto], runtime.get("source_ports"),
                                          permanent.get("source_ports"))
            self.extras_table.append(["source port", f"{port}/{proto}", scope],
                                     colors={2: color}, payload=("source-port", port, proto))
        self.extras_table.resize_columns()

    def _render_rules(self, runtime, permanent, settings) -> None:
        self.rules_table.clear_rows()
        for rule in settings.get("rules_str") or []:
            text = str(rule)
            action = next((word for word in ("accept", "reject", "drop", "mark")
                           if f" {word}" in f" {text}"), "?")
            source = ""
            if 'source address="' in text:
                source = text.split('source address="', 1)[1].split('"', 1)[0]
            elif 'source ipset="' in text:
                source = "ipset:" + text.split('source ipset="', 1)[1].split('"', 1)[0]
            match = ""
            for token in ("service name=", "port port=", "protocol value=",
                          "icmp-block name=", "icmp-type name="):
                if token in text:
                    match = token.rstrip("=").replace(" ", ":") + " " + \
                        text.split(token, 1)[1].split('"')[1]
                    break
            scope, color = self._scope_of(rule, runtime.get("rules_str"),
                                          permanent.get("rules_str"))
            action_color = {"accept": theme.GOOD, "reject": theme.WARN,
                            "drop": theme.DANGER}.get(action, theme.FG_DIM)
            self.rules_table.append(
                [action, source or "any", match or "everything", scope, elide(text, 200)],
                colors={0: action_color, 3: color}, payload=rule, monospace=[4])
        self.rules_table.resize_columns({0: 80, 1: 160, 2: 190, 3: 130})

    def _render_bindings(self, snap, runtime, permanent, settings) -> None:
        self.iface_table.clear_rows()
        for iface in settings.get("interfaces") or []:
            scope, color = self._scope_of(iface, runtime.get("interfaces"),
                                          permanent.get("interfaces"))
            self.iface_table.append([iface, scope, snap.zone_of_interface(iface) or "—"],
                                    colors={1: color}, payload=iface, monospace=[0])
        self.iface_table.resize_columns()

        self.sources_table.clear_rows()
        for source in settings.get("sources") or []:
            scope, color = self._scope_of(source, runtime.get("sources"),
                                          permanent.get("sources"))
            self.sources_table.append([source, scope], colors={1: color},
                                      payload=source, monospace=[0])
        self.sources_table.resize_columns()

    def _render_options(self, snap, settings, runtime, permanent) -> None:
        self._loading = True
        self.target_combo.setCurrentText(settings.get("target", "default") or "default")
        self.masquerade_check.setChecked(bool(settings.get("masquerade")))
        self.forward_check.setChecked(bool(settings.get("forward")))
        self.inversion_check.setChecked(bool(settings.get("icmp_block_inversion")))
        self._loading = False

        self.icmp_table.clear_rows()
        for name in settings.get("icmp_blocks") or []:
            scope, color = self._scope_of(name, runtime.get("icmp_blocks"),
                                          permanent.get("icmp_blocks"))
            self.icmp_table.append([name, scope], colors={1: color}, payload=name)
        self.icmp_table.resize_columns()

        self.summary_grid.clear()
        self.summary_grid.add("Zone", self._zone or "—")
        self.summary_grid.add("Short name", settings.get("short", "") or "—")
        self.summary_grid.add("Target", settings.get("target", "default") or "default")
        self.summary_grid.add("Services", str(len(settings.get("services") or [])))
        self.summary_grid.add("Ports", str(len(settings.get("ports") or [])))
        self.summary_grid.add("Rich rules", str(len(settings.get("rules_str") or [])))
        self.summary_grid.add("Forward ports", str(len(settings.get("forward_ports") or [])))
        self.summary_grid.add("Interfaces", ", ".join(settings.get("interfaces") or []) or "—",
                              monospace=True)
        self.summary_grid.add("Sources", ", ".join(settings.get("sources") or []) or "—",
                              monospace=True)
        drift = analysis.zone_drift(snap).get(self._zone)
        self.summary_grid.add("Unsaved changes", ", ".join(drift) if drift else "none",
                              color=theme.WARN if drift else theme.GOOD)

    # -- editing helpers ----------------------------------------------------
    def _require_zone(self) -> bool:
        if self._zone:
            return True
        QMessageBox.information(self, "No zone selected", "Pick a zone first.")
        return False

    def _apply(self, description: str, method: str, runtime_args: tuple,
               permanent_args: tuple, force_runtime: bool | None = None,
               force_permanent: bool | None = None) -> None:
        do_runtime = self._edit_runtime if force_runtime is None else force_runtime
        do_permanent = self._edit_permanent if force_permanent is None else force_permanent
        if do_runtime:
            self.ctx.fw.runtime(method, self._zone, *runtime_args,
                                description=f"{description} (runtime)")
        if do_permanent:
            self.ctx.fw.perm_zone(self._zone, method, *permanent_args,
                                  description=f"{description} (permanent)")

    # -- services -----------------------------------------------------------
    def _on_service_toggled(self, item: QListWidgetItem) -> None:
        if self._loading or not self._zone:
            return
        name = item.data(Qt.UserRole)
        enable = item.checkState() == Qt.Checked
        method = "addService" if enable else "removeService"
        verb = "allow" if enable else "remove"
        self._apply(f"{verb} service {name} in {self._zone}", method,
                    (name, 0) if enable else (name,), (name,))

    # -- ports --------------------------------------------------------------
    def _add_port(self) -> None:
        if not self._require_zone():
            return
        dialog = PortDialog(self._zone, self)
        dialog.runtime_check.setChecked(self._edit_runtime)
        dialog.permanent_check.setChecked(self._edit_permanent)
        if dialog.exec() != PortDialog.Accepted:
            return
        port, proto = dialog.result_value()
        self._apply(f"open {port}/{proto} in {self._zone}", "addPort",
                    (port, proto, 0), (port, proto),
                    dialog.apply_runtime, dialog.apply_permanent)

    def _remove_port(self) -> None:
        payload = self.ports_table.current_payload()
        if not payload:
            return
        port, proto = payload
        self._apply(f"close {port}/{proto} in {self._zone}", "removePort",
                    (port, proto), (port, proto))

    def _add_source_port(self) -> None:
        if not self._require_zone():
            return
        dialog = PortDialog(self._zone, self, source_port=True)
        if dialog.exec() != PortDialog.Accepted:
            return
        port, proto = dialog.result_value()
        self._apply(f"allow source port {port}/{proto}", "addSourcePort",
                    (port, proto, 0), (port, proto),
                    dialog.apply_runtime, dialog.apply_permanent)

    def _add_protocol(self) -> None:
        if not self._require_zone():
            return
        dialog = ProtocolDialog(self._zone, self)
        if dialog.exec() != ProtocolDialog.Accepted:
            return
        proto = dialog.result_value()
        self._apply(f"allow protocol {proto}", "addProtocol", (proto, 0), (proto,),
                    dialog.apply_runtime, dialog.apply_permanent)

    def _remove_extra(self) -> None:
        payload = self.extras_table.current_payload()
        if not payload:
            return
        if payload[0] == "protocol":
            self._apply(f"remove protocol {payload[1]}", "removeProtocol",
                        (payload[1],), (payload[1],))
        else:
            _, port, proto = payload
            self._apply(f"remove source port {port}/{proto}", "removeSourcePort",
                        (port, proto), (port, proto))

    def _add_forward(self) -> None:
        if not self._require_zone():
            return
        dialog = ForwardPortDialog(self._zone, self)
        if dialog.exec() != ForwardPortDialog.Accepted:
            return
        port, proto, toport, toaddr = dialog.result_value()
        self._apply(f"forward {port}/{proto}", "addForwardPort",
                    (port, proto, toport, toaddr, 0), (port, proto, toport, toaddr),
                    dialog.apply_runtime, dialog.apply_permanent)

    def _remove_forward(self) -> None:
        payload = self.forwards_table.current_payload()
        if not payload:
            return
        port, proto, toport, toaddr = payload
        self._apply(f"remove forward {port}/{proto}", "removeForwardPort",
                    (port, proto, toport, toaddr), (port, proto, toport, toaddr))

    # -- rich rules ---------------------------------------------------------
    def _add_rich_rule(self) -> None:
        if not self._require_zone():
            return
        snap = self.ctx.snapshot()
        dialog = RichRuleDialog(self._zone, snap.services, self)
        dialog.runtime_check.setChecked(self._edit_runtime)
        dialog.permanent_check.setChecked(self._edit_permanent)
        if dialog.exec() != RichRuleDialog.Accepted:
            return
        rule = dialog.rule_text()
        self._apply(f"add rich rule to {self._zone}", "addRichRule",
                    (rule, 0), (rule,), dialog.apply_runtime, dialog.apply_permanent)

    def _edit_rich_rule(self) -> None:
        original = self.rules_table.current_payload()
        if not original:
            return
        snap = self.ctx.snapshot()
        dialog = RichRuleDialog(self._zone, snap.services, self, existing=str(original))
        if dialog.exec() != RichRuleDialog.Accepted:
            return
        updated = dialog.rule_text()
        if updated == original:
            return
        # Replace = remove then add, in that order, on each selected scope.
        if dialog.apply_runtime:
            self.ctx.fw.runtime("removeRichRule", self._zone, str(original),
                                description="replace rich rule (runtime)")
            self.ctx.fw.runtime("addRichRule", self._zone, updated, 0,
                                description="replace rich rule (runtime)")
        if dialog.apply_permanent:
            self.ctx.fw.perm_zone(self._zone, "removeRichRule", str(original),
                                  description="replace rich rule (permanent)")
            self.ctx.fw.perm_zone(self._zone, "addRichRule", updated,
                                  description="replace rich rule (permanent)")

    def _remove_rich_rule(self) -> None:
        rule = self.rules_table.current_payload()
        if not rule:
            return
        if QMessageBox.question(
                self, "Remove rich rule",
                f"Remove this rule from '{self._zone}'?\n\n{rule}") != QMessageBox.Yes:
            return
        self._apply("remove rich rule", "removeRichRule", (str(rule),), (str(rule),))

    # -- bindings -----------------------------------------------------------
    def _bind_interface(self) -> None:
        if not self._require_zone():
            return
        iface = self.iface_combo.currentText().strip()
        if not iface:
            return
        snap = self.ctx.snapshot()
        current = snap.zone_of_interface(iface)
        if current and current != self._zone:
            if QMessageBox.question(
                    self, "Move interface",
                    f"'{iface}' is currently in zone '{current}'.\n"
                    f"Move it to '{self._zone}'?") != QMessageBox.Yes:
                return
            if self._edit_runtime:
                self.ctx.fw.runtime("changeZoneOfInterface", self._zone, iface,
                                    description=f"move {iface} to {self._zone}")
            if self._edit_permanent:
                self.ctx.fw.perm_zone(current, "removeInterface", iface,
                                      description=f"unbind {iface} from {current}")
                self.ctx.fw.perm_zone(self._zone, "addInterface", iface,
                                      description=f"bind {iface} to {self._zone}")
            return
        self._apply(f"bind {iface} to {self._zone}", "addInterface", (iface,), (iface,))

    def _unbind_interface(self) -> None:
        iface = self.iface_table.current_payload()
        if not iface:
            return
        self._apply(f"unbind {iface}", "removeInterface", (iface,), (iface,))

    def _add_source(self) -> None:
        if not self._require_zone():
            return
        dialog = SourceDialog(self._zone, self)
        if dialog.exec() != SourceDialog.Accepted:
            return
        source = dialog.result_value()
        self._apply(f"bind source {source}", "addSource", (source,), (source,),
                    dialog.apply_runtime, dialog.apply_permanent)

    def _remove_source(self) -> None:
        source = self.sources_table.current_payload()
        if not source:
            return
        self._apply(f"unbind source {source}", "removeSource", (source,), (source,))

    # -- options ------------------------------------------------------------
    def _set_target(self) -> None:
        if self._loading or not self._zone:
            return
        target = self.target_combo.currentText()
        if target == "ACCEPT" and QMessageBox.warning(
                self, "Accept everything?",
                f"Setting the target of '{self._zone}' to ACCEPT makes every port "
                "reachable unless a rule blocks it explicitly.\n\nContinue?",
                QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            self._render_zone()
            return
        # Target only exists in the permanent settings object; runtime needs a
        # full settings write, so route both through the permanent zone.
        self.ctx.fw.perm_zone(self._zone, "setTarget", target,
                              description=f"set target of {self._zone} to {target}")
        if self._edit_runtime:
            QMessageBox.information(
                self, "Reload required",
                "The zone target is a permanent-only setting. Press Reload in the "
                "toolbar to apply it to the running firewall.")

    def _toggle_masquerade(self, checked: bool) -> None:
        if self._loading or not self._zone:
            return
        method = "addMasquerade" if checked else "removeMasquerade"
        self._apply(f"{'enable' if checked else 'disable'} masquerade", method,
                    (0,) if checked else (), ())

    def _toggle_forward(self, checked: bool) -> None:
        if self._loading or not self._zone:
            return
        method = "addForward" if checked else "removeForward"
        self._apply(f"{'enable' if checked else 'disable'} intra-zone forwarding",
                    method, (0,) if checked else (), ())

    def _toggle_inversion(self, checked: bool) -> None:
        if self._loading or not self._zone:
            return
        method = "addIcmpBlockInversion" if checked else "removeIcmpBlockInversion"
        self._apply(f"{'enable' if checked else 'disable'} ICMP block inversion",
                    method, (), ())

    def _add_icmp_block(self) -> None:
        if not self._require_zone():
            return
        name = self.icmp_combo.currentText()
        if not name:
            return
        self._apply(f"block ICMP {name}", "addIcmpBlock", (name, 0), (name,))

    def _remove_icmp_block(self) -> None:
        name = self.icmp_table.current_payload()
        if not name:
            return
        self._apply(f"unblock ICMP {name}", "removeIcmpBlock", (name,), (name,))

    # -- zone lifecycle -----------------------------------------------------
    def _set_default_zone(self) -> None:
        if not self._require_zone():
            return
        self.ctx.fw.runtime("setDefaultZone", self._zone,
                            description=f"set default zone to {self._zone}")

    def _reset_zone(self) -> None:
        if not self._require_zone():
            return
        if QMessageBox.warning(
                self, "Reset zone",
                f"Discard all permanent customisations of '{self._zone}' and "
                "restore the shipped defaults?",
                QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        self.ctx.fw.perm_zone(self._zone, "loadDefaults",
                              description=f"reset {self._zone} to defaults")

    def _create_zone(self) -> None:
        name, ok = QInputDialog.getText(self, "New zone", "Zone name")
        name = name.strip()
        if not ok or not name:
            return
        if not name.replace("-", "").replace("_", "").isalnum():
            QMessageBox.warning(self, "Invalid name",
                                "Use letters, digits, dashes and underscores only.")
            return
        self.ctx.fw.create_zone(name, short=name, description="Created by fwpanel")

    def _zone_menu(self, pos) -> None:
        item = self.zone_list.itemAt(pos)
        if item is None:
            return
        name = item.data(Qt.UserRole)
        menu = QMenu(self)
        default_action = QAction("Set as default zone", self)
        default_action.triggered.connect(
            lambda: self.ctx.fw.runtime("setDefaultZone", name,
                                        description=f"set default zone to {name}"))
        menu.addAction(default_action)
        remove_action = QAction("Delete zone…", self)
        remove_action.triggered.connect(lambda: self._delete_zone(name))
        menu.addAction(remove_action)
        menu.exec(self.zone_list.viewport().mapToGlobal(pos))

    def _delete_zone(self, name: str) -> None:
        if QMessageBox.warning(
                self, "Delete zone",
                f"Permanently delete zone '{name}'?\n\nBuilt-in zones cannot be "
                "deleted; this only works for zones you created.",
                QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        self.ctx.fw.perm_zone(name, "remove", description=f"delete zone {name}")
