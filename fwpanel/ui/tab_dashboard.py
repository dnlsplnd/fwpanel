"""Overview tab: live posture, throughput, interfaces, findings."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QGridLayout, QHBoxLayout, QLabel, QScrollArea,
                               QSizePolicy, QVBoxLayout, QWidget)

from .. import analysis
from ..util import human_bytes, human_count, human_rate
from . import theme
from .charts import DonutGauge, LineChart
from .widgets import Badge, Card, FilterTable, StatTile

SEVERITY_COLOR = {
    analysis.CRITICAL: theme.DANGER,
    analysis.HIGH: theme.DANGER,
    analysis.MEDIUM: theme.WARN,
    analysis.LOW: theme.INFO,
    analysis.INFO: theme.NEUTRAL,
}


class FindingRow(QWidget):
    """One posture finding: severity pill, title, detail, optional hint."""

    def __init__(self, finding: analysis.Finding, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        color = SEVERITY_COLOR.get(finding.severity, theme.NEUTRAL)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 5, 0, 5)
        layout.setSpacing(10)

        badge = Badge(finding.severity.upper(), color)
        badge.setFixedWidth(66)
        layout.addWidget(badge, 0, Qt.AlignTop)

        text = QVBoxLayout()
        text.setSpacing(2)
        title = QLabel(finding.title)
        title.setStyleSheet(f"color: {theme.FG}; font-weight: 600; font-size: 9.5pt;")
        title.setWordWrap(True)
        text.addWidget(title)

        detail = QLabel(finding.detail)
        detail.setStyleSheet(f"color: {theme.FG_DIM}; font-size: 9pt;")
        detail.setWordWrap(True)
        text.addWidget(detail)

        if finding.hint:
            hint = QLabel("→ " + finding.hint)
            hint.setStyleSheet(f"color: {theme.FG_FAINT}; font-size: 8.5pt; font-style: italic;")
            hint.setWordWrap(True)
            text.addWidget(hint)
        layout.addLayout(text, 1)


class DashboardTab(QWidget):
    def __init__(self, ctx, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._build()
        ctx.fw.snapshotChanged.connect(self.on_snapshot)
        ctx.net.sampled.connect(self.on_sample)

    # -- construction -------------------------------------------------------
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        outer.addWidget(scroll)

        page = QWidget()
        scroll.setWidget(page)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)

        # --- stat tiles ---
        tiles = QGridLayout()
        tiles.setSpacing(12)
        self.tile_state = StatTile("firewall", caption="daemon state")
        self.tile_zone = StatTile("default zone", caption="unbound interfaces land here")
        self.tile_rx = StatTile("inbound", caption="all interfaces", spark_color=theme.INFO)
        self.tile_tx = StatTile("outbound", caption="all interfaces", spark_color=theme.VIOLET)
        self.tile_conn = StatTile("connections", caption="established", spark_color=theme.ACCENT)
        self.tile_track = StatTile("conntrack", caption="tracked flows")
        for column, tile in enumerate((self.tile_state, self.tile_zone, self.tile_rx,
                                       self.tile_tx, self.tile_conn, self.tile_track)):
            tiles.addWidget(tile, 0, column)
            tiles.setColumnStretch(column, 1)
        layout.addLayout(tiles)

        # --- throughput + posture ---
        middle = QHBoxLayout()
        middle.setSpacing(12)

        chart_card = Card("live throughput")
        self.chart = LineChart(formatter=human_rate)
        self.chart.setMinimumHeight(210)
        self.s_rx = self.chart.add_series("inbound", theme.INFO)
        self.s_tx = self.chart.add_series("outbound", theme.VIOLET)
        self.chart.y_min_ceiling = 64 * 1024
        chart_card.body.addWidget(self.chart)
        self.throughput_note = QLabel()
        self.throughput_note.setObjectName("Hint")
        chart_card.body.addWidget(self.throughput_note)
        middle.addWidget(chart_card, 3)

        posture_card = Card("posture")
        self.gauge = DonutGauge(higher_is_better=True)
        self.gauge.setMinimumHeight(170)
        posture_card.body.addWidget(self.gauge)
        self.posture_note = QLabel("assessing…")
        self.posture_note.setAlignment(Qt.AlignCenter)
        self.posture_note.setObjectName("Hint")
        self.posture_note.setWordWrap(True)
        posture_card.body.addWidget(self.posture_note)
        middle.addWidget(posture_card, 1)
        layout.addLayout(middle)

        # --- interfaces ---
        iface_card = Card("interfaces")
        self.iface_table = FilterTable(
            ["Interface", "Zone", "State", "Addresses", "In", "Out", "Dropped", "MTU"])
        self.iface_table.setMinimumHeight(150)
        self.iface_table.view.setSortingEnabled(False)
        self.iface_table.stretch_column(3)  # addresses absorb the slack
        iface_card.body.addWidget(self.iface_table)
        layout.addWidget(iface_card)

        # --- findings ---
        self.findings_card = Card("what stands out")
        self.findings_box = QVBoxLayout()
        self.findings_box.setSpacing(0)
        holder = QWidget()
        holder.setLayout(self.findings_box)
        holder.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        self.findings_card.body.addWidget(holder)
        layout.addWidget(self.findings_card)
        layout.addStretch(1)

    # -- updates ------------------------------------------------------------
    def on_snapshot(self, snap) -> None:
        if snap.ok:
            state = "RUNNING" if snap.state == "RUNNING" else snap.state or "?"
            color = theme.GOOD if snap.state == "RUNNING" else theme.WARN
            caption = f"firewalld {snap.version}"
            if snap.panic:
                state, color = "PANIC", theme.DANGER
                caption = "all traffic is being dropped"
            self.tile_state.set_value(state, caption=caption, color=color)
        else:
            self.tile_state.set_value("OFFLINE", caption=snap.error[:60], color=theme.DANGER)

        self.tile_zone.set_value(snap.default_zone or "—",
                                 caption=f"{len(snap.active_zones)} zone(s) active")

        findings = analysis.assess(snap, self.ctx.sample())
        score, verdict = analysis.posture_score(findings)
        self.gauge.set_value(score, 100, f"{score}", verdict)
        counts: dict[str, int] = {}
        for finding in findings:
            counts[finding.severity] = counts.get(finding.severity, 0) + 1
        if findings:
            self.posture_note.setText(", ".join(
                f"{n} {sev}" for sev, n in sorted(counts.items(),
                                                  key=lambda kv: analysis._ORDER.get(kv[0], 9))))
        else:
            self.posture_note.setText("nothing worth flagging")

        self._render_findings(findings)
        self._render_interfaces()

    def _render_findings(self, findings) -> None:
        while self.findings_box.count():
            item = self.findings_box.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self.findings_card.set_title(f"what stands out · {len(findings)}")
        if not findings:
            empty = QLabel("No findings. Zones are tight and nothing is drifting.")
            empty.setObjectName("Hint")
            self.findings_box.addWidget(empty)
            return
        for finding in findings[:22]:
            self.findings_box.addWidget(FindingRow(finding))

    def on_sample(self, sample) -> None:
        self.s_rx.push(sample.total_rx_rate)
        self.s_tx.push(sample.total_tx_rate)
        self.chart.update()

        self.tile_rx.set_value(human_rate(sample.total_rx_rate).split(" ")[0],
                               unit=human_rate(sample.total_rx_rate).split(" ")[1])
        self.tile_rx.push(sample.total_rx_rate)
        self.tile_tx.set_value(human_rate(sample.total_tx_rate).split(" ")[0],
                               unit=human_rate(sample.total_tx_rate).split(" ")[1])
        self.tile_tx.push(sample.total_tx_rate)

        self.tile_conn.set_value(str(sample.established),
                                 caption=f"{sample.listening} listening · "
                                         f"{sample.remote_peers} peers")
        self.tile_conn.push(sample.established)

        if sample.conntrack_max:
            ratio = sample.conntrack_count / sample.conntrack_max
            color = theme.DANGER if ratio > 0.85 else (theme.WARN if ratio > 0.6 else theme.FG)
            self.tile_track.set_value(human_count(sample.conntrack_count),
                                      caption=f"{ratio:.1%} of {human_count(sample.conntrack_max)}",
                                      color=color)
        else:
            self.tile_track.set_value("—", caption="conntrack not loaded")

        peak_in = human_rate(self.s_rx.peak)
        peak_out = human_rate(self.s_tx.peak)
        self.throughput_note.setText(
            f"peak in {peak_in} · peak out {peak_out} · "
            f"session total {human_bytes(sum(self.s_rx.values) * sample.interval)} in / "
            f"{human_bytes(sum(self.s_tx.values) * sample.interval)} out"
        )
        self._render_interfaces()

    def _render_interfaces(self) -> None:
        sample = self.ctx.sample()
        snap = self.ctx.snapshot()
        if sample is None:
            return
        selected = self.iface_table.current_row()
        self.iface_table.clear_rows()
        for name, stat in sorted(sample.interfaces.items()):
            if name == "lo" and not stat.rx_rate and not stat.tx_rate:
                continue
            zone = snap.zone_of_interface(name)
            state = "up" if stat.is_up else "down"
            colors = {
                1: theme.ACCENT if zone else theme.FG_FAINT,
                2: theme.GOOD if stat.is_up else theme.FG_FAINT,
                4: theme.INFO, 5: theme.VIOLET,
                6: theme.DANGER if (stat.drop_in + stat.drop_out) else theme.FG_FAINT,
            }
            self.iface_table.append(
                [name, zone or "unmanaged", state,
                 ", ".join(stat.addresses[:3]) or "—",
                 human_rate(stat.rx_rate), human_rate(stat.tx_rate),
                 str(stat.drop_in + stat.drop_out), str(stat.mtu)],
                colors=colors, monospace=[0, 3],
            )
        self.iface_table.select_source_row(selected)
