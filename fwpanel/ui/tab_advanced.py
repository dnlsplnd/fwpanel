"""Advanced tab: ipsets, direct rules and policies.

These three all sit outside the ordinary zone model, which is exactly why they
are worth surfacing: they are the parts of a firewalld configuration that a
zone-by-zone review will miss.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QHBoxLayout, QInputDialog, QLabel, QListWidget,
                               QListWidgetItem, QMessageBox, QSplitter,
                               QTabWidget, QVBoxLayout, QWidget)

from ..util import elide
from . import theme
from .dialogs import DirectRuleDialog, IPSetEntryDialog
from .widgets import (Badge, Card, FilterTable, KeyValueGrid, SearchBox,
                      toolbar_button)

IPSET_TYPES = ("hash:ip", "hash:net", "hash:mac", "hash:ip,port",
               "hash:net,port", "hash:ip,port,ip", "hash:net,iface")


class AdvancedTab(QWidget):
    def __init__(self, ctx, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._ipset = ""
        self._build()
        ctx.fw.snapshotChanged.connect(self.on_snapshot)

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 14)
        layout.setSpacing(10)
        self.sub = QTabWidget()
        self.sub.addTab(self._build_ipsets(), "IP sets")
        self.sub.addTab(self._build_direct(), "Direct rules")
        self.sub.addTab(self._build_policies(), "Policies")
        layout.addWidget(self.sub)

    # -- ipsets -------------------------------------------------------------
    def _build_ipsets(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 12, 0, 0)
        layout.setSpacing(10)

        intro = QLabel(
            "An ipset is a named, kernel-side list of addresses. Reference one "
            "from a rich rule with <code>ipset:name</code> to block or allow "
            "thousands of addresses with a single rule.")
        intro.setWordWrap(True)
        intro.setObjectName("Hint")
        layout.addWidget(intro)

        splitter = QSplitter(Qt.Horizontal)
        layout.addWidget(splitter, 1)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(8)
        self.ipset_list = QListWidget()
        self.ipset_list.currentItemChanged.connect(self._on_ipset_selected)
        left_layout.addWidget(self.ipset_list, 1)
        row = QHBoxLayout()
        create = toolbar_button("New set…", accent=True)
        create.clicked.connect(self._create_ipset)
        row.addWidget(create)
        delete = toolbar_button("Delete", danger=True)
        delete.clicked.connect(self._delete_ipset)
        row.addWidget(delete)
        left_layout.addLayout(row)
        splitter.addWidget(left)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(10)

        info = Card("set")
        self.ipset_grid = KeyValueGrid()
        info.body.addWidget(self.ipset_grid)
        right_layout.addWidget(info)

        entries = Card("entries")
        erow = QHBoxLayout()
        add_entry = toolbar_button("Add entries…", accent=True)
        add_entry.clicked.connect(self._add_entries)
        erow.addWidget(add_entry)
        remove_entry = toolbar_button("Remove selected", danger=True)
        remove_entry.clicked.connect(self._remove_entry)
        erow.addWidget(remove_entry)
        erow.addStretch(1)
        self.entry_search = SearchBox("Filter entries…")
        self.entry_search.textChanged.connect(lambda t: self.entries_table.set_filter(t))
        erow.addWidget(self.entry_search)
        entries.body.addLayout(erow)
        self.entries_table = FilterTable(["Entry"])
        entries.body.addWidget(self.entries_table)
        right_layout.addWidget(entries, 1)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        splitter.setSizes([230, 800])
        return page

    def _on_ipset_selected(self, current, _previous) -> None:
        self._ipset = current.data(Qt.UserRole) if current else ""
        self._render_ipsets()

    def _render_ipsets(self) -> None:
        snap = self.ctx.snapshot()
        names = sorted(snap.ipsets)
        existing = [self.ipset_list.item(i).data(Qt.UserRole)
                    for i in range(self.ipset_list.count())]
        if names != existing:
            self.ipset_list.blockSignals(True)
            self.ipset_list.clear()
            for name in names:
                item = QListWidgetItem(f"{name}  ({len(snap.ipsets[name].get('entries') or [])})")
                item.setData(Qt.UserRole, name)
                self.ipset_list.addItem(item)
            self.ipset_list.blockSignals(False)
            if names and self._ipset not in names:
                self._ipset = names[0]
                self.ipset_list.setCurrentRow(0)

        data = snap.ipsets.get(self._ipset, {})
        self.ipset_grid.clear()
        self.ipset_grid.add("Name", self._ipset or "—")
        self.ipset_grid.add("Type", data.get("type", "") or "—")
        self.ipset_grid.add("Description", data.get("description", "") or "—")
        options = data.get("options") or {}
        self.ipset_grid.add("Options",
                            ", ".join(f"{k}={v}" for k, v in options.items()) or "—")
        entries = data.get("entries") or []
        self.ipset_grid.add("Entries", str(len(entries)))

        used_by = []
        for zone, settings in sorted(snap.zones.items()):
            for source in settings.get("sources") or []:
                if source == f"ipset:{self._ipset}":
                    used_by.append(f"{zone} (source)")
            for rule in settings.get("rules_str") or []:
                if f'ipset="{self._ipset}"' in str(rule):
                    used_by.append(f"{zone} (rich rule)")
        self.ipset_grid.add("Referenced by", ", ".join(used_by) or "nothing yet",
                            color=theme.WARN if not used_by else theme.GOOD)

        self.entries_table.clear_rows()
        for entry in entries:
            self.entries_table.append([entry], payload=entry, monospace=[0])

    def _create_ipset(self) -> None:
        name, ok = QInputDialog.getText(self, "New ipset", "Name")
        name = name.strip()
        if not ok or not name:
            return
        kind, ok = QInputDialog.getItem(self, "New ipset", "Type", IPSET_TYPES, 0, False)
        if not ok:
            return
        self.ctx.fw.create_ipset(name, kind, "Created by fwpanel")

    def _delete_ipset(self) -> None:
        if not self._ipset:
            return
        if QMessageBox.warning(
                self, "Delete ipset",
                f"Permanently delete ipset '{self._ipset}' and all its entries?",
                QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        self.ctx.fw.call("config_ipset", self._ipset, "remove",
                         description=f"delete ipset {self._ipset}")

    def _add_entries(self) -> None:
        if not self._ipset:
            return
        dialog = IPSetEntryDialog(self._ipset, self)
        if dialog.exec() != IPSetEntryDialog.Accepted:
            return
        for entry in dialog.entries():
            self.ctx.fw.runtime("addEntry", self._ipset, entry,
                                description=f"add {entry} to {self._ipset}")
            self.ctx.fw.call("config_ipset", self._ipset, "addEntry", entry,
                             description=f"add {entry} to {self._ipset} (permanent)")

    def _remove_entry(self) -> None:
        entry = self.entries_table.current_payload()
        if not entry or not self._ipset:
            return
        self.ctx.fw.runtime("removeEntry", self._ipset, entry,
                            description=f"remove {entry} from {self._ipset}")
        self.ctx.fw.call("config_ipset", self._ipset, "removeEntry", entry,
                         description=f"remove {entry} from {self._ipset} (permanent)")

    # -- direct -------------------------------------------------------------
    def _build_direct(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 12, 0, 0)
        layout.setSpacing(10)

        warning = QLabel(
            "Direct rules are passed to iptables/nftables verbatim and run "
            "<b>before</b> everything firewalld builds from your zones. They are "
            "not validated, not visible in the zone model, and easy to forget.")
        warning.setWordWrap(True)
        warning.setStyleSheet(f"color: {theme.WARN}; font-size: 9pt;")
        layout.addWidget(warning)

        rules = Card("direct rules")
        row = QHBoxLayout()
        add = toolbar_button("Add rule…", accent=True)
        add.clicked.connect(self._add_direct_rule)
        row.addWidget(add)
        remove = toolbar_button("Remove selected", danger=True)
        remove.clicked.connect(self._remove_direct_rule)
        row.addWidget(remove)
        row.addStretch(1)
        self.direct_badge = Badge("NONE", theme.GOOD)
        row.addWidget(self.direct_badge)
        rules.body.addLayout(row)
        self.direct_table = FilterTable(
            ["Family", "Table", "Chain", "Priority", "Arguments"])
        rules.body.addWidget(self.direct_table)
        layout.addWidget(rules, 2)

        extras = Card("chains and passthroughs")
        self.extras_table = FilterTable(["Kind", "Family", "Detail"])
        extras.body.addWidget(self.extras_table)
        layout.addWidget(extras, 1)
        return page

    def _render_direct(self, snap) -> None:
        self.direct_table.clear_rows()
        for entry in snap.direct_rules:
            ipv, table, chain, priority, args = (list(entry) + ["", "", "", 0, []])[:5]
            self.direct_table.append(
                [ipv, table, chain, priority, " ".join(args)],
                colors={4: theme.WARN}, payload=tuple(entry), monospace=[4],
                sort_keys={3: priority})
        self.direct_table.resize_columns({4: 520})
        count = len(snap.direct_rules)
        self.direct_badge.set_state(f"{count} RULE(S)" if count else "NONE",
                                    theme.WARN if count else theme.GOOD)

        self.extras_table.clear_rows()
        for entry in snap.direct_chains:
            ipv, table, chain = (list(entry) + ["", "", ""])[:3]
            self.extras_table.append(["chain", ipv, f"{table}/{chain}"],
                                     payload=("chain", tuple(entry)), monospace=[2])
        for entry in snap.direct_passthroughs:
            ipv, args = (list(entry) + ["", []])[:2]
            self.extras_table.append(
                ["passthrough", ipv, elide(" ".join(args), 200)],
                colors={0: theme.DANGER}, payload=("passthrough", tuple(entry)),
                monospace=[2])
        self.extras_table.resize_columns({2: 520})

    def _add_direct_rule(self) -> None:
        dialog = DirectRuleDialog(self)
        if dialog.exec() != DirectRuleDialog.Accepted:
            return
        ipv, table, chain, priority, args = dialog.result_value()
        self.ctx.fw.runtime("addRule", ipv, table, chain, priority, args,
                            description=f"add direct rule to {table}/{chain}")

    def _remove_direct_rule(self) -> None:
        payload = self.direct_table.current_payload()
        if not payload:
            return
        ipv, table, chain, priority, args = (list(payload) + ["", "", "", 0, []])[:5]
        if QMessageBox.question(
                self, "Remove direct rule",
                f"Remove this rule from {table}/{chain}?\n\n{' '.join(args)}"
                ) != QMessageBox.Yes:
            return
        self.ctx.fw.runtime("removeRule", ipv, table, chain, priority, list(args),
                            description=f"remove direct rule from {table}/{chain}")

    # -- policies -----------------------------------------------------------
    def _build_policies(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 12, 0, 0)
        layout.setSpacing(10)

        intro = QLabel(
            "Policies filter traffic <i>between</i> zones rather than into this "
            "host — forwarded traffic, container egress, and the built-in "
            "host-facing policies firewalld ships.")
        intro.setWordWrap(True)
        intro.setObjectName("Hint")
        layout.addWidget(intro)

        card = Card("policies")
        self.policy_table = FilterTable(
            ["Policy", "Ingress", "Egress", "Target", "Priority", "Services", "Rules"])
        card.body.addWidget(self.policy_table)
        layout.addWidget(card, 1)
        return page

    def _render_policies(self, snap) -> None:
        self.policy_table.clear_rows()
        for name, settings in sorted(snap.policies.items()):
            target = settings.get("target", "")
            self.policy_table.append(
                [name,
                 ", ".join(settings.get("ingress_zones") or []) or "—",
                 ", ".join(settings.get("egress_zones") or []) or "—",
                 target,
                 settings.get("priority", 0),
                 len(settings.get("services") or []),
                 len(settings.get("rules_str") or [])],
                colors={3: theme.DANGER if target == "ACCEPT" else theme.FG_DIM},
                sort_keys={4: settings.get("priority", 0)},
                payload=name)
        self.policy_table.resize_columns({0: 200, 1: 160, 2: 160})

    # -- refresh ------------------------------------------------------------
    def on_snapshot(self, snap) -> None:
        self._render_ipsets()
        self._render_direct(snap)
        self._render_policies(snap)
