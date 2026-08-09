"""Rules tab: every rule in the firewall, flattened into one auditable list.

The Zones tab is for working on one zone at a time. This is the opposite view -
everything that opens or blocks traffic anywhere, so you can answer "what is
actually exposed on this machine?" without clicking through fourteen zones.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QGuiApplication
from PySide6.QtWidgets import (QCheckBox, QComboBox, QHBoxLayout, QLabel, QMenu,
                               QMessageBox, QVBoxLayout, QWidget)

from ..util import elide, service_name_for_port
from . import theme
from .dialogs import RichRuleDialog
from .widgets import Card, FilterTable, SearchBox, toolbar_button

KIND_COLOR = {
    "service": theme.INFO,
    "port": theme.WARN,
    "source port": theme.WARN,
    "protocol": theme.VIOLET,
    "forward": theme.VIOLET,
    "rich rule": theme.ACCENT,
    "icmp block": theme.NEUTRAL,
    "masquerade": theme.VIOLET,
}


@dataclass
class RuleRow:
    zone: str
    kind: str
    detail: str
    effect: str
    scope: str
    active: bool
    #: (method, runtime_args, permanent_args) for removal, or None if built in
    remove: tuple | None = None
    raw: str = ""


class RulesTab(QWidget):
    COLUMNS = ["Zone", "Kind", "Rule", "Effect", "Scope", "Reachable"]

    def __init__(self, ctx, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._rows: list[RuleRow] = []
        self._build()
        ctx.fw.snapshotChanged.connect(lambda _snap: self._render())

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 14)
        layout.setSpacing(10)

        bar = QHBoxLayout()
        bar.setSpacing(9)
        self.search = SearchBox("Filter rules…")
        self.search.textChanged.connect(lambda t: self.table.set_filter(t))
        bar.addWidget(self.search, 2)

        self.zone_combo = QComboBox()
        self.zone_combo.addItem("all zones")
        self.zone_combo.currentIndexChanged.connect(self._render)
        bar.addWidget(self.zone_combo)

        self.kind_combo = QComboBox()
        self.kind_combo.addItems(["all kinds"] + sorted(KIND_COLOR))
        self.kind_combo.currentIndexChanged.connect(self._render)
        bar.addWidget(self.kind_combo)

        self.active_only = QCheckBox("Only reachable zones")
        self.active_only.setToolTip(
            "Hide rules in zones that no interface or source is bound to.")
        self.active_only.setChecked(True)
        self.active_only.toggled.connect(self._render)
        bar.addWidget(self.active_only)

        bar.addStretch(1)
        add_rule = toolbar_button("New rich rule…", accent=True)
        add_rule.clicked.connect(self._add_rich_rule)
        bar.addWidget(add_rule)
        remove = toolbar_button("Remove selected", danger=True)
        remove.clicked.connect(self._remove_selected)
        bar.addWidget(remove)
        layout.addLayout(bar)

        card = Card("all rules")
        self.count_label = QLabel()
        self.count_label.setObjectName("Hint")
        card.add_header_widget(self.count_label)
        self.table = FilterTable(self.COLUMNS)
        self.table.view.customContextMenuRequested.connect(self._context_menu)
        self.table.stretch_column(2)  # the rule text is what deserves the room
        card.body.addWidget(self.table)
        layout.addWidget(card, 1)

        footer = QLabel(
            "“Reachable” means the rule's zone is bound to a live interface or "
            "source right now. Rules in unbound zones are inert until something "
            "is assigned to them.")
        footer.setObjectName("Hint")
        footer.setWordWrap(True)
        layout.addWidget(footer)

    # -- collection ---------------------------------------------------------
    def _collect(self) -> list[RuleRow]:
        snap = self.ctx.snapshot()
        rows: list[RuleRow] = []
        zones = sorted(set(snap.zones) | set(snap.perm_zones))

        for zone in zones:
            runtime = snap.zones.get(zone, {})
            permanent = snap.perm_zones.get(zone, {})
            active = zone in snap.active_zones

            def scope_of(value, key) -> str:
                in_runtime = value in (runtime.get(key) or [])
                in_permanent = value in (permanent.get(key) or [])
                if in_runtime and in_permanent:
                    return "runtime + permanent"
                return "runtime only" if in_runtime else "permanent only"

            def union(key):
                out = list(runtime.get(key) or [])
                for item in permanent.get(key) or []:
                    if item not in out:
                        out.append(item)
                return out

            for service in union("services"):
                rows.append(RuleRow(
                    zone, "service", service, "allow",
                    scope_of(service, "services"), active,
                    ("removeService", (service,), (service,))))

            for entry in union("ports"):
                port, proto = (list(entry) + ["", ""])[:2]
                name = service_name_for_port(port, proto)
                rows.append(RuleRow(
                    zone, "port", f"{port}/{proto}" + (f" ({name})" if name else ""),
                    "allow", scope_of(entry, "ports"), active,
                    ("removePort", (port, proto), (port, proto))))

            for entry in union("source_ports"):
                port, proto = (list(entry) + ["", ""])[:2]
                rows.append(RuleRow(
                    zone, "source port", f"{port}/{proto}", "allow",
                    scope_of(entry, "source_ports"), active,
                    ("removeSourcePort", (port, proto), (port, proto))))

            for proto in union("protocols"):
                rows.append(RuleRow(
                    zone, "protocol", proto, "allow",
                    scope_of(proto, "protocols"), active,
                    ("removeProtocol", (proto,), (proto,))))

            for entry in union("forward_ports"):
                port, proto, toport, toaddr = (list(entry) + ["", "", "", ""])[:4]
                target = f"{toaddr or 'this host'}:{toport or port}"
                rows.append(RuleRow(
                    zone, "forward", f"{port}/{proto} → {target}", "forward",
                    scope_of(entry, "forward_ports"), active,
                    ("removeForwardPort", (port, proto, toport, toaddr),
                     (port, proto, toport, toaddr))))

            for name in union("icmp_blocks"):
                rows.append(RuleRow(
                    zone, "icmp block", name, "block",
                    scope_of(name, "icmp_blocks"), active,
                    ("removeIcmpBlock", (name,), (name,))))

            if runtime.get("masquerade") or permanent.get("masquerade"):
                scope = ("runtime + permanent"
                         if runtime.get("masquerade") and permanent.get("masquerade")
                         else ("runtime only" if runtime.get("masquerade")
                               else "permanent only"))
                rows.append(RuleRow(zone, "masquerade", "NAT outbound traffic",
                                    "translate", scope, active,
                                    ("removeMasquerade", (), ())))

            for rule in union("rules_str"):
                text = str(rule)
                effect = next((word for word in ("accept", "reject", "drop", "mark")
                               if f" {word}" in f" {text} "), "custom")
                rows.append(RuleRow(
                    zone, "rich rule", elide(text, 170), effect,
                    scope_of(rule, "rules_str"), active,
                    ("removeRichRule", (text,), (text,)), raw=text))

        return rows

    # -- rendering ----------------------------------------------------------
    def _render(self) -> None:
        snap = self.ctx.snapshot()
        zones = sorted(set(snap.zones) | set(snap.perm_zones))
        if [self.zone_combo.itemText(i) for i in range(1, self.zone_combo.count())] != zones:
            current = self.zone_combo.currentText()
            self.zone_combo.blockSignals(True)
            self.zone_combo.clear()
            self.zone_combo.addItem("all zones")
            self.zone_combo.addItems(zones)
            index = self.zone_combo.findText(current)
            self.zone_combo.setCurrentIndex(max(0, index))
            self.zone_combo.blockSignals(False)

        zone_filter = self.zone_combo.currentText()
        kind_filter = self.kind_combo.currentText()

        self._rows = []
        self.table.clear_rows()
        for row in self._collect():
            if zone_filter != "all zones" and row.zone != zone_filter:
                continue
            if kind_filter != "all kinds" and row.kind != kind_filter:
                continue
            if self.active_only.isChecked() and not row.active:
                continue
            effect_color = {"allow": theme.GOOD, "drop": theme.DANGER,
                            "reject": theme.WARN, "accept": theme.GOOD,
                            "block": theme.DANGER, "forward": theme.VIOLET,
                            "translate": theme.VIOLET}.get(row.effect, theme.FG_DIM)
            scope_color = theme.WARN if row.scope == "runtime only" else (
                theme.INFO if row.scope == "permanent only" else theme.FG_DIM)
            index = self.table.append(
                [row.zone, row.kind, row.detail, row.effect, row.scope,
                 "yes" if row.active else "no"],
                colors={1: KIND_COLOR.get(row.kind, theme.FG_DIM),
                        3: effect_color, 5: theme.GOOD if row.active else theme.FG_FAINT,
                        4: scope_color},
                payload=len(self._rows), monospace=[2])
            self._rows.append(row)

        self.table.resize_columns({0: 130, 1: 110, 3: 90, 4: 180, 5: 100})
        exposed = sum(1 for r in self._rows if r.active and r.effect in ("allow", "accept"))
        self.count_label.setText(
            f"{len(self._rows)} rules · {exposed} actively allowing traffic")

    # -- actions ------------------------------------------------------------
    def _selected(self) -> RuleRow | None:
        payload = self.table.current_payload()
        if isinstance(payload, int) and 0 <= payload < len(self._rows):
            return self._rows[payload]
        return None

    def _remove_selected(self) -> None:
        row = self._selected()
        if row is None or row.remove is None:
            return
        method, runtime_args, permanent_args = row.remove
        question = (f"Remove this {row.kind} from zone '{row.zone}'?\n\n"
                    f"{row.raw or row.detail}\n\nScope: {row.scope}")
        if QMessageBox.question(self, "Remove rule", question) != QMessageBox.Yes:
            return
        if "runtime" in row.scope:
            self.ctx.fw.runtime(method, row.zone, *runtime_args,
                                description=f"remove {row.kind} from {row.zone}")
        if "permanent" in row.scope:
            self.ctx.fw.perm_zone(row.zone, method, *permanent_args,
                                  description=f"remove {row.kind} from {row.zone}")

    def _add_rich_rule(self) -> None:
        snap = self.ctx.snapshot()
        zone = (self.zone_combo.currentText() if self.zone_combo.currentIndex() > 0
                else snap.default_zone)
        dialog = RichRuleDialog(zone, snap.services, self)
        if dialog.exec() != RichRuleDialog.Accepted:
            return
        rule = dialog.rule_text()
        if dialog.apply_runtime:
            self.ctx.fw.runtime("addRichRule", zone, rule, 0,
                                description=f"add rich rule to {zone}")
        if dialog.apply_permanent:
            self.ctx.fw.perm_zone(zone, "addRichRule", rule,
                                  description=f"add rich rule to {zone} (permanent)")

    def _context_menu(self, pos) -> None:
        source_row = self.table.row_at(pos)
        if source_row < 0:
            return
        self.table.select_source_row(source_row)
        row = self._selected()
        if row is None:
            return
        menu = QMenu(self)
        remove = QAction(f"Remove this {row.kind}…", self)
        remove.triggered.connect(self._remove_selected)
        menu.addAction(remove)
        if row.kind == "rich rule" and row.raw:
            copy = QAction("Copy rule text", self)
            copy.triggered.connect(lambda: QGuiApplication.clipboard().setText(row.raw))
            menu.addAction(copy)
        menu.exec(self.table.view.viewport().mapToGlobal(pos))
