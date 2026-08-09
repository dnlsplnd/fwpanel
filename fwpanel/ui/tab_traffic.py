"""Traffic tab: throughput, packet rates, losses, and connection makeup."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QGridLayout, QHBoxLayout, QLabel,
                               QVBoxLayout, QWidget)

from ..util import human_count, human_rate
from . import theme
from .charts import BarList, DonutGauge, LineChart
from .widgets import Card

ALL_INTERFACES = "all interfaces"


def _pps(value: float) -> str:
    return f"{human_count(value)} p/s"


class TrafficTab(QWidget):
    def __init__(self, ctx, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._known_interfaces: list[str] = []
        self._build()
        ctx.net.sampled.connect(self.on_sample)

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 14)
        layout.setSpacing(12)

        # --- controls ---
        controls = QHBoxLayout()
        controls.setSpacing(10)
        controls.addWidget(QLabel("Interface"))
        self.iface_combo = QComboBox()
        self.iface_combo.addItem(ALL_INTERFACES)
        self.iface_combo.setMinimumWidth(180)
        self.iface_combo.currentTextChanged.connect(self._reset_series)
        controls.addWidget(self.iface_combo)

        controls.addSpacing(18)
        controls.addWidget(QLabel("Sample every"))
        self.interval_combo = QComboBox()
        for label, ms in (("0.5 s", 500), ("1 s", 1000), ("2 s", 2000),
                          ("5 s", 5000), ("10 s", 10000)):
            self.interval_combo.addItem(label, ms)
        self.interval_combo.setCurrentIndex(2)
        self.interval_combo.currentIndexChanged.connect(
            lambda: self.ctx.net.set_interval(self.interval_combo.currentData()))
        controls.addWidget(self.interval_combo)

        self.summary = QLabel()
        self.summary.setObjectName("Hint")
        controls.addSpacing(18)
        controls.addWidget(self.summary)
        controls.addStretch(1)
        layout.addLayout(controls)

        # --- charts ---
        grid = QGridLayout()
        grid.setSpacing(12)

        throughput = Card("throughput")
        self.chart_bytes = LineChart(formatter=human_rate)
        self.chart_bytes.y_min_ceiling = 64 * 1024
        self.s_rx = self.chart_bytes.add_series("in", theme.INFO)
        self.s_tx = self.chart_bytes.add_series("out", theme.VIOLET)
        throughput.body.addWidget(self.chart_bytes)
        grid.addWidget(throughput, 0, 0)

        packets = Card("packet rate")
        self.chart_packets = LineChart(formatter=_pps)
        self.chart_packets.y_min_ceiling = 50
        self.s_rx_pps = self.chart_packets.add_series("in", theme.ACCENT)
        self.s_tx_pps = self.chart_packets.add_series("out", theme.GOOD)
        packets.body.addWidget(self.chart_packets)
        grid.addWidget(packets, 0, 1)

        losses = Card("errors and drops")
        self.chart_loss = LineChart(formatter=lambda v: f"{v:.1f}/s")
        self.chart_loss.y_min_ceiling = 1
        self.s_drop = self.chart_loss.add_series("dropped", theme.DANGER)
        self.s_err = self.chart_loss.add_series("errors", theme.WARN, dashed=True, fill=False)
        losses.body.addWidget(self.chart_loss)
        grid.addWidget(losses, 1, 0)

        sockets = Card("live connections")
        self.chart_conn = LineChart(formatter=lambda v: f"{v:.0f}")
        self.chart_conn.y_min_ceiling = 10
        self.s_estab = self.chart_conn.add_series("established", theme.ACCENT)
        self.s_track = self.chart_conn.add_series("conntrack", theme.WARN, fill=False)
        sockets.body.addWidget(self.chart_conn)
        grid.addWidget(sockets, 1, 1)

        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        grid.setRowStretch(0, 1)
        grid.setRowStretch(1, 1)
        layout.addLayout(grid, 3)

        # --- breakdown row ---
        bottom = QHBoxLayout()
        bottom.setSpacing(12)

        track_card = Card("conntrack table")
        self.gauge = DonutGauge("occupancy")
        self.gauge.setMinimumHeight(150)
        track_card.body.addWidget(self.gauge)
        bottom.addWidget(track_card, 1)

        states_card = Card("socket states")
        self.states = BarList(formatter=lambda v: f"{v:.0f}")
        states_card.body.addWidget(self.states)
        bottom.addWidget(states_card, 2)

        talkers_card = Card("top remote peers")
        self.talkers = BarList(formatter=lambda v: f"{v:.0f}")
        talkers_card.body.addWidget(self.talkers)
        bottom.addWidget(talkers_card, 2)

        layout.addLayout(bottom, 2)

    def _reset_series(self) -> None:
        for chart in (self.chart_bytes, self.chart_packets, self.chart_loss):
            chart.clear()

    def _sync_interfaces(self, sample) -> None:
        names = sorted(n for n in sample.interfaces if n != "lo")
        if names == self._known_interfaces:
            return
        self._known_interfaces = names
        current = self.iface_combo.currentText()
        self.iface_combo.blockSignals(True)
        self.iface_combo.clear()
        self.iface_combo.addItem(ALL_INTERFACES)
        self.iface_combo.addItems(names)
        index = self.iface_combo.findText(current)
        self.iface_combo.setCurrentIndex(max(0, index))
        self.iface_combo.blockSignals(False)

    def on_sample(self, sample) -> None:
        self._sync_interfaces(sample)
        selection = self.iface_combo.currentText()

        if selection == ALL_INTERFACES:
            stats = [s for name, s in sample.interfaces.items() if name != "lo"]
        else:
            stat = sample.interfaces.get(selection)
            stats = [stat] if stat else []

        rx = sum(s.rx_rate for s in stats)
        tx = sum(s.tx_rate for s in stats)
        rx_pps = sum(s.rx_pps for s in stats)
        tx_pps = sum(s.tx_pps for s in stats)
        drops = sum(s.drop_rate for s in stats)
        errors = sum(s.err_in + s.err_out for s in stats)

        self.s_rx.push(rx)
        self.s_tx.push(tx)
        self.s_rx_pps.push(rx_pps)
        self.s_tx_pps.push(tx_pps)
        self.s_drop.push(drops)
        self.s_err.push(errors)
        self.s_estab.push(sample.established)
        self.s_track.push(sample.conntrack_count)
        for chart in (self.chart_bytes, self.chart_packets, self.chart_loss,
                      self.chart_conn):
            chart.update()

        self.summary.setText(
            f"{human_rate(rx)} in · {human_rate(tx)} out · "
            f"{_pps(rx_pps + tx_pps)} · {len(sample.connections)} sockets"
        )

        if sample.conntrack_max:
            ratio = sample.conntrack_count / sample.conntrack_max
            label = f"{ratio:.0%}" if ratio >= 0.01 else f"{ratio * 100:.2f}%"
            self.gauge.set_value(sample.conntrack_count, sample.conntrack_max,
                                 label,
                                 f"{human_count(sample.conntrack_count)} / "
                                 f"{human_count(sample.conntrack_max)}")
        else:
            self.gauge.set_value(0, 1, "—", "not loaded")

        palette = {
            "ESTABLISHED": theme.ACCENT, "LISTEN": theme.INFO,
            "TIME_WAIT": theme.NEUTRAL, "CLOSE_WAIT": theme.WARN,
            "SYN_SENT": theme.WARN, "SYN_RECV": theme.WARN,
            "FIN_WAIT1": theme.NEUTRAL, "FIN_WAIT2": theme.NEUTRAL,
        }
        rows = sorted(sample.tcp_states.items(), key=lambda kv: -kv[1])[:9]
        self.states.set_rows([(state, count, palette.get(state, theme.FG_FAINT))
                              for state, count in rows])

        peers: dict[str, int] = {}
        for conn in sample.connections:
            if conn.status == "ESTABLISHED" and conn.raddr:
                peers[conn.raddr] = peers.get(conn.raddr, 0) + 1
        top = sorted(peers.items(), key=lambda kv: -kv[1])[:9]
        self.talkers.set_rows([(addr, count, theme.VIOLET) for addr, count in top])
