"""Ruleset tab: what firewalld actually compiled into nftables.

Everything here needs root, so the tab is inert until the privileged helper is
running. That is deliberate - the panel asks for elevation once, when you ask
for something that genuinely requires it.
"""

from __future__ import annotations

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QTextCursor, QTextDocument
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QPlainTextEdit, QSplitter,
                               QVBoxLayout, QWidget)

from ..util import human_bytes, human_count
from . import theme
from .charts import BarList, LineChart
from .widgets import Badge, Card, FilterTable, SearchBox, toolbar_button


class RulesetTab(QWidget):
    def __init__(self, ctx, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._previous_counters: dict[str, int] = {}
        self._build()

        ctx.helper.stateChanged.connect(self._on_helper_state)
        ctx.helper.resultReady.connect(self._on_helper_result)

        self._poll = QTimer(self)
        self._poll.setInterval(4000)
        self._poll.timeout.connect(self._request_counters)

    # -- construction -------------------------------------------------------
    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 14)
        layout.setSpacing(10)

        bar = QHBoxLayout()
        bar.setSpacing(9)
        self.elevate_button = toolbar_button(
            "Elevate…", "Start the privileged helper (one authentication)", accent=True)
        self.elevate_button.clicked.connect(self.ctx.helper.start)
        bar.addWidget(self.elevate_button)

        self.refresh_button = toolbar_button("Refresh")
        self.refresh_button.clicked.connect(self._request_all)
        self.refresh_button.setEnabled(False)
        bar.addWidget(self.refresh_button)

        self.search = SearchBox("Search the ruleset…")
        self.search.returnPressed.connect(self._find_next)
        self.search.setEnabled(False)
        bar.addWidget(self.search, 1)

        bar.addStretch(1)
        self.state_badge = Badge("NOT ELEVATED", theme.WARN)
        bar.addWidget(self.state_badge)
        layout.addLayout(bar)

        note = QLabel(
            "firewalld compiles your zones into an nftables ruleset. This is that "
            "ruleset verbatim, with live packet counters — the ground truth when "
            "a rule does not behave the way the zone view suggests.")
        note.setWordWrap(True)
        note.setObjectName("Hint")
        layout.addWidget(note)

        splitter = QSplitter(Qt.Horizontal)
        layout.addWidget(splitter, 1)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(10)

        drops_card = Card("drop rate by chain")
        self.drop_chart = LineChart(formatter=lambda v: f"{v:.1f}/s")
        self.drop_chart.y_min_ceiling = 1
        self.drop_chart.setMinimumHeight(140)
        self.s_drop = self.drop_chart.add_series("dropped packets", theme.DANGER)
        self.s_pass = self.drop_chart.add_series("all matched", theme.ACCENT,
                                                 fill=False, dashed=True)
        drops_card.body.addWidget(self.drop_chart)
        left_layout.addWidget(drops_card)

        counters_card = Card("busiest chains")
        self.counter_bars = BarList(formatter=human_count)
        counters_card.body.addWidget(self.counter_bars)
        left_layout.addWidget(counters_card)

        table_card = Card("chain counters")
        self.table = FilterTable(
            ["Family", "Table", "Chain", "Rules", "Packets", "Bytes", "Dropped"])
        table_card.body.addWidget(self.table)
        left_layout.addWidget(table_card, 1)
        splitter.addWidget(left)

        ruleset_card = Card("nft list ruleset")
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setFont(theme.mono_font(9))
        self.text.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.text.setPlaceholderText(
            "Press Elevate to read the compiled nftables ruleset.")
        ruleset_card.body.addWidget(self.text)
        splitter.addWidget(ruleset_card)

        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)
        splitter.setSizes([520, 700])

    # -- helper plumbing ----------------------------------------------------
    def _on_helper_state(self, elevated: bool, message: str) -> None:
        self.elevate_button.setEnabled(not elevated)
        self.refresh_button.setEnabled(elevated)
        self.search.setEnabled(elevated)
        if elevated:
            self.state_badge.set_state("ELEVATED", theme.ACCENT)
            self._request_all()
            self._poll.start()
        else:
            self.state_badge.set_state((message or "not elevated").upper()[:28],
                                       theme.WARN)
            self._poll.stop()

    def _request_all(self) -> None:
        if not self.ctx.helper.elevated:
            return
        self.ctx.helper.request("nft")
        self.ctx.helper.request("nft_counters")

    def _request_counters(self) -> None:
        if self.ctx.helper.elevated:
            self.ctx.helper.request("nft_counters")

    def _on_helper_result(self, command: str, data) -> None:
        if command == "nft" and isinstance(data, dict):
            self._render_ruleset(data.get("text", ""))
        elif command == "nft_counters" and isinstance(data, dict):
            self._render_counters(data.get("chains") or [])

    # -- rendering ----------------------------------------------------------
    def _render_ruleset(self, text: str) -> None:
        scrollbar = self.text.verticalScrollBar()
        offset = scrollbar.value()
        self.text.setPlainText(text or "# empty ruleset")
        scrollbar.setValue(offset)

    def _render_counters(self, chains: list[dict]) -> None:
        total_drop = 0
        total_packets = 0
        selected = self.table.current_row()
        self.table.clear_rows()
        for chain in chains:
            key = f"{chain['family']}/{chain['table']}/{chain['chain']}"
            total_drop += chain.get("drop_packets", 0)
            total_packets += chain.get("packets", 0)
            drops = chain.get("drop_packets", 0)
            self.table.append(
                [chain.get("family", ""), chain.get("table", ""),
                 chain.get("chain", ""), chain.get("rules", 0),
                 human_count(chain.get("packets", 0)),
                 human_bytes(chain.get("bytes", 0)),
                 human_count(drops)],
                colors={6: theme.DANGER if drops else theme.FG_FAINT},
                sort_keys={3: chain.get("rules", 0),
                           4: chain.get("packets", 0),
                           5: chain.get("bytes", 0),
                           6: drops},
                payload=key, monospace=[2])
        self.table.resize_columns({2: 200})
        if selected >= 0:
            self.table.select_source_row(selected)

        rows = sorted(chains, key=lambda c: -c.get("packets", 0))[:8]
        self.counter_bars.set_rows([
            (f"{c['table']}/{c['chain']}", c.get("packets", 0),
             theme.DANGER if c.get("drop_packets") else theme.ACCENT)
            for c in rows])

        # Counters are cumulative; chart the delta so the line means "per second".
        interval = self._poll.interval() / 1000.0
        previous_drop = self._previous_counters.get("drop")
        previous_total = self._previous_counters.get("total")
        if previous_drop is not None:
            self.s_drop.push(max(0, total_drop - previous_drop) / interval)
            self.s_pass.push(max(0, total_packets - previous_total) / interval)
            self.drop_chart.update()
        self._previous_counters["drop"] = total_drop
        self._previous_counters["total"] = total_packets

    def _find_next(self) -> None:
        needle = self.search.text()
        if not needle:
            return
        if not self.text.find(needle):
            self.text.moveCursor(QTextCursor.Start)
            self.text.find(needle, QTextDocument.FindFlags())
