"""Services tab: browse firewalld's service catalogue and see who uses it."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QListWidget,
                               QListWidgetItem, QSplitter, QVBoxLayout, QWidget)

from .. import analysis
from ..util import service_name_for_port
from . import theme
from .widgets import Badge, Card, FilterTable, KeyValueGrid, SearchBox, toolbar_button


class ServicesTab(QWidget):
    def __init__(self, ctx, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._details: dict[str, dict] = {}
        self._current = ""
        self._catalogue: list[str] = []
        self._build()
        ctx.fw.snapshotChanged.connect(self.on_snapshot)

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 14)
        layout.setSpacing(10)

        splitter = QSplitter(Qt.Horizontal)
        layout.addWidget(splitter, 1)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(8)
        self.search = SearchBox("Search services…")
        self.search.textChanged.connect(self._filter)
        left_layout.addWidget(self.search)

        self.list = QListWidget()
        self.list.currentItemChanged.connect(self._on_selected)
        left_layout.addWidget(self.list, 1)
        self.catalogue_label = QLabel()
        self.catalogue_label.setObjectName("Hint")
        left_layout.addWidget(self.catalogue_label)
        splitter.addWidget(left)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(10)

        header = Card("service")
        self.title = QLabel("—")
        self.title.setObjectName("SectionTitle")
        header.body.addWidget(self.title)
        badges = QHBoxLayout()
        badges.setSpacing(6)
        self.badge_sensitive = Badge("", theme.WARN)
        self.badge_sensitive.hide()
        self.badge_usage = Badge("", theme.NEUTRAL)
        badges.addWidget(self.badge_sensitive)
        badges.addWidget(self.badge_usage)
        badges.addStretch(1)
        header.body.addLayout(badges)
        self.description = QLabel()
        self.description.setWordWrap(True)
        self.description.setObjectName("Hint")
        header.body.addWidget(self.description)
        right_layout.addWidget(header)

        detail = Card("definition")
        self.detail_grid = KeyValueGrid()
        detail.body.addWidget(self.detail_grid)
        right_layout.addWidget(detail)

        ports_card = Card("ports opened by this service")
        self.ports_table = FilterTable(["Port", "Protocol", "Well-known name"])
        self.ports_table.setMinimumHeight(120)
        ports_card.body.addWidget(self.ports_table)
        right_layout.addWidget(ports_card)

        usage_card = Card("zones using this service")
        row = QHBoxLayout()
        row.addWidget(QLabel("Enable in zone"))
        self.zone_combo = QComboBox()
        self.zone_combo.setMinimumWidth(160)
        row.addWidget(self.zone_combo)
        add = toolbar_button("Allow here", accent=True)
        add.clicked.connect(lambda: self._set_in_zone(True))
        row.addWidget(add)
        remove = toolbar_button("Remove from zone", danger=True)
        remove.clicked.connect(lambda: self._set_in_zone(False))
        row.addWidget(remove)
        row.addStretch(1)
        usage_card.body.addLayout(row)
        self.usage_table = FilterTable(["Zone", "Scope", "Zone is reachable"])
        self.usage_table.setMinimumHeight(120)
        usage_card.body.addWidget(self.usage_table)
        right_layout.addWidget(usage_card)
        right_layout.addStretch(1)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        splitter.setSizes([260, 900])

    # -- data ---------------------------------------------------------------
    def on_snapshot(self, snap) -> None:
        if snap.services != self._catalogue:
            self._catalogue = list(snap.services)
            used = self._services_in_use(snap)
            self.list.blockSignals(True)
            self.list.clear()
            for name in snap.services:
                item = QListWidgetItem(name)
                item.setData(Qt.UserRole, name)
                if name in used:
                    item.setForeground(theme.color(theme.ACCENT))
                    item.setText(f"{name}  ●")
                elif name in analysis.SENSITIVE_SERVICES:
                    item.setForeground(theme.color(theme.FG_DIM))
                self.list.addItem(item)
            self.list.blockSignals(False)
            self.catalogue_label.setText(
                f"{len(snap.services)} services · {len(used)} in use")
            if self.list.count() and not self._current:
                self.list.setCurrentRow(0)
            self._filter()

        zones = sorted(snap.zones)
        if [self.zone_combo.itemText(i) for i in range(self.zone_combo.count())] != zones:
            current = self.zone_combo.currentText()
            self.zone_combo.clear()
            self.zone_combo.addItems(zones)
            if current in zones:
                self.zone_combo.setCurrentText(current)
            elif snap.default_zone in zones:
                self.zone_combo.setCurrentText(snap.default_zone)
        self._render_detail()

    def _services_in_use(self, snap) -> set[str]:
        used: set[str] = set()
        for settings in list(snap.zones.values()) + list(snap.perm_zones.values()):
            used.update(settings.get("services") or [])
        return used

    def _filter(self) -> None:
        text = self.search.text().lower()
        for i in range(self.list.count()):
            item = self.list.item(i)
            item.setHidden(bool(text) and text not in item.data(Qt.UserRole).lower())

    def _on_selected(self, current, _previous) -> None:
        if current is None:
            return
        self._current = current.data(Qt.UserRole)
        if self._current not in self._details:
            self.ctx.fw.service_details(self._current, self._on_details)
        self._render_detail()

    def _on_details(self, ok: bool, value) -> None:
        if ok and isinstance(value, dict):
            self._details[self._current] = value
            self._render_detail()

    # -- rendering ----------------------------------------------------------
    def _render_detail(self) -> None:
        name = self._current
        if not name:
            return
        snap = self.ctx.snapshot()
        data = self._details.get(name, {})

        self.title.setText(name)
        self.description.setText(data.get("description")
                                 or "Loading definition from firewalld…")

        note = analysis.SENSITIVE_SERVICES.get(name)
        self.badge_sensitive.setVisible(bool(note))
        if note:
            self.badge_sensitive.set_state(note.upper(), theme.WARN)

        zones_with = [z for z, s in snap.zones.items() if name in (s.get("services") or [])]
        perm_with = [z for z, s in snap.perm_zones.items()
                     if name in (s.get("services") or [])]
        reachable = [z for z in zones_with if z in snap.active_zones]
        if reachable:
            self.badge_usage.set_state(f"REACHABLE VIA {len(reachable)} ZONE(S)", theme.ACCENT)
        elif zones_with or perm_with:
            self.badge_usage.set_state("CONFIGURED, NOT REACHABLE", theme.INFO)
        else:
            self.badge_usage.set_state("NOT ENABLED ANYWHERE", theme.FG_FAINT)

        self.detail_grid.clear()
        self.detail_grid.add("Short name", data.get("short", "") or "—")
        self.detail_grid.add("Version", data.get("version", "") or "—")
        protocols = data.get("protocols") or []
        self.detail_grid.add("Protocols", ", ".join(protocols) or "—")
        modules = data.get("modules") or []
        self.detail_grid.add("Kernel helpers", ", ".join(modules) or "—",
                             color=theme.WARN if modules else None)
        includes = data.get("includes") or []
        self.detail_grid.add("Includes", ", ".join(includes) or "—")
        destinations = data.get("destinations") or {}
        self.detail_grid.add(
            "Destinations",
            ", ".join(f"{k}: {v}" for k, v in destinations.items()) or "any",
            monospace=True)
        source_ports = data.get("source_ports") or []
        self.detail_grid.add(
            "Source ports",
            ", ".join(f"{p}/{proto}" for p, proto in source_ports) or "—")

        self.ports_table.clear_rows()
        for entry in data.get("ports") or []:
            port, proto = (list(entry) + ["", ""])[:2]
            self.ports_table.append(
                [port or "any", proto, service_name_for_port(port, proto) or "—"],
                monospace=[0])
        self.ports_table.resize_columns()

        self.usage_table.clear_rows()
        for zone in sorted(set(zones_with) | set(perm_with)):
            in_runtime, in_perm = zone in zones_with, zone in perm_with
            scope = ("runtime + permanent" if in_runtime and in_perm
                     else ("runtime only" if in_runtime else "permanent only"))
            active = zone in snap.active_zones
            self.usage_table.append(
                [zone, scope, "yes" if active else "no"],
                colors={1: theme.WARN if scope != "runtime + permanent" else theme.FG_DIM,
                        2: theme.GOOD if active else theme.FG_FAINT},
                payload=zone)
        self.usage_table.resize_columns()

    # -- actions ------------------------------------------------------------
    def _set_in_zone(self, enable: bool) -> None:
        name = self._current
        zone = self.zone_combo.currentText()
        if not name or not zone:
            return
        method = "addService" if enable else "removeService"
        verb = "allow" if enable else "remove"
        self.ctx.fw.runtime(method, zone, *( (name, 0) if enable else (name,) ),
                            description=f"{verb} {name} in {zone} (runtime)")
        self.ctx.fw.perm_zone(zone, method, name,
                              description=f"{verb} {name} in {zone} (permanent)")
