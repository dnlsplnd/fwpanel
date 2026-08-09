"""Unprivileged kernel network sampling.

Runs on its own thread and emits a :class:`Sample` on a fixed cadence: per
interface throughput derived from /proc counters, the socket table, and
conntrack occupancy. When the privileged helper is available its richer socket
view (process names for every user, not just ours) is merged in.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

import psutil
from PySide6.QtCore import (QObject, QThread, QTimer, Signal, Slot, Qt,
                            QMetaObject)

TCP_STATES = ("ESTABLISHED", "SYN_SENT", "SYN_RECV", "FIN_WAIT1", "FIN_WAIT2",
              "TIME_WAIT", "CLOSE", "CLOSE_WAIT", "LAST_ACK", "LISTEN", "CLOSING")


@dataclass
class InterfaceStat:
    name: str
    rx_bytes: int = 0
    tx_bytes: int = 0
    rx_packets: int = 0
    tx_packets: int = 0
    rx_rate: float = 0.0
    tx_rate: float = 0.0
    rx_pps: float = 0.0
    tx_pps: float = 0.0
    drop_in: int = 0
    drop_out: int = 0
    err_in: int = 0
    err_out: int = 0
    drop_rate: float = 0.0
    is_up: bool = False
    speed: int = 0
    mtu: int = 0
    addresses: list[str] = field(default_factory=list)


@dataclass
class Connection:
    proto: str = "tcp"
    family: str = "ipv4"
    laddr: str = ""
    lport: str = ""
    raddr: str = ""
    rport: str = ""
    status: str = ""
    pid: int = 0
    process: str = ""
    recvq: int = 0
    sendq: int = 0

    @property
    def key(self) -> tuple:
        return (self.proto, self.laddr, self.lport, self.raddr, self.rport)


@dataclass
class Sample:
    stamp: float = field(default_factory=time.time)
    interval: float = 1.0
    interfaces: dict[str, InterfaceStat] = field(default_factory=dict)
    connections: list[Connection] = field(default_factory=list)
    tcp_states: dict[str, int] = field(default_factory=dict)
    conntrack_count: int = 0
    conntrack_max: int = 0
    total_rx_rate: float = 0.0
    total_tx_rate: float = 0.0
    total_drop_rate: float = 0.0
    established: int = 0
    listening: int = 0
    remote_peers: int = 0
    privileged: bool = False


def _read_int(path: str) -> int:
    try:
        with open(path) as handle:
            return int(handle.read().strip())
    except (OSError, ValueError):
        return 0


class _Sampler(QObject):
    sampled = Signal(object)

    def __init__(self, interval_ms: int, helper_cache: Callable[[str], object] | None) -> None:
        super().__init__()
        self._interval_ms = interval_ms
        self._helper_cache = helper_cache
        self._timer: QTimer | None = None
        self._prev_counters: dict[str, psutil._common.snetio] = {}
        self._prev_stamp = 0.0
        self._proc_names: dict[int, str] = {}

    @Slot()
    def begin(self) -> None:
        self._timer = QTimer(self)
        self._timer.setInterval(self._interval_ms)
        self._timer.timeout.connect(self._tick)
        self._timer.start()
        self._tick()

    @Slot(int)
    def setInterval(self, interval_ms: int) -> None:
        self._interval_ms = max(500, interval_ms)
        if self._timer:
            self._timer.setInterval(self._interval_ms)

    @Slot()
    def finish(self) -> None:
        if self._timer:
            self._timer.stop()
            self._timer = None

    # -- sampling -----------------------------------------------------------
    @Slot()
    def _tick(self) -> None:
        try:
            sample = self._collect()
        except Exception:
            return
        self.sampled.emit(sample)

    def _collect(self) -> Sample:
        now = time.monotonic()
        elapsed = (now - self._prev_stamp) if self._prev_stamp else 0.0
        sample = Sample(interval=elapsed or self._interval_ms / 1000.0)

        counters = psutil.net_io_counters(pernic=True)
        try:
            if_stats = psutil.net_if_stats()
        except Exception:
            if_stats = {}
        try:
            if_addrs = psutil.net_if_addrs()
        except Exception:
            if_addrs = {}

        for name, cur in counters.items():
            stat = InterfaceStat(
                name=name,
                rx_bytes=cur.bytes_recv, tx_bytes=cur.bytes_sent,
                rx_packets=cur.packets_recv, tx_packets=cur.packets_sent,
                drop_in=cur.dropin, drop_out=cur.dropout,
                err_in=cur.errin, err_out=cur.errout,
            )
            info = if_stats.get(name)
            if info is not None:
                stat.is_up, stat.speed, stat.mtu = info.isup, info.speed, info.mtu
            for addr in if_addrs.get(name, []):
                if addr.family.name in ("AF_INET", "AF_INET6"):
                    stat.addresses.append(addr.address.split("%")[0])

            prev = self._prev_counters.get(name)
            if prev is not None and elapsed > 0:
                stat.rx_rate = max(0.0, (cur.bytes_recv - prev.bytes_recv) / elapsed)
                stat.tx_rate = max(0.0, (cur.bytes_sent - prev.bytes_sent) / elapsed)
                stat.rx_pps = max(0.0, (cur.packets_recv - prev.packets_recv) / elapsed)
                stat.tx_pps = max(0.0, (cur.packets_sent - prev.packets_sent) / elapsed)
                dropped = (cur.dropin - prev.dropin) + (cur.dropout - prev.dropout)
                stat.drop_rate = max(0.0, dropped / elapsed)
            sample.interfaces[name] = stat
            if name != "lo":
                sample.total_rx_rate += stat.rx_rate
                sample.total_tx_rate += stat.tx_rate
                sample.total_drop_rate += stat.drop_rate

        self._prev_counters = counters
        self._prev_stamp = now

        sample.conntrack_count = _read_int("/proc/sys/net/netfilter/nf_conntrack_count")
        sample.conntrack_max = _read_int("/proc/sys/net/netfilter/nf_conntrack_max")

        sample.connections = self._collect_connections(sample)
        states: dict[str, int] = {}
        peers: set[str] = set()
        for conn in sample.connections:
            states[conn.status] = states.get(conn.status, 0) + 1
            if conn.status == "ESTABLISHED":
                sample.established += 1
                if conn.raddr:
                    peers.add(conn.raddr)
            elif conn.status == "LISTEN":
                sample.listening += 1
        sample.tcp_states = states
        sample.remote_peers = len(peers)
        return sample

    def _collect_connections(self, sample: Sample) -> list[Connection]:
        helper_rows = None
        if self._helper_cache is not None:
            cached = self._helper_cache("sockets")
            if isinstance(cached, dict):
                helper_rows = cached.get("sockets")

        if helper_rows:
            sample.privileged = True
            return self._from_helper(helper_rows)
        return self._from_psutil()

    def _from_helper(self, rows: list[dict]) -> list[Connection]:
        out = []
        for row in rows:
            procs = row.get("procs") or []
            state = str(row.get("state", ""))
            proto = str(row.get("proto", ""))
            if proto.startswith("udp"):
                state = "ESTABLISHED" if row.get("raddr") not in ("*", "", "0.0.0.0") else "LISTEN"
            out.append(Connection(
                proto=proto,
                family="ipv6" if ":" in str(row.get("laddr", "")) else "ipv4",
                laddr=str(row.get("laddr", "")), lport=str(row.get("lport", "")),
                raddr=str(row.get("raddr", "")), rport=str(row.get("rport", "")),
                status=state.upper(),
                pid=int(procs[0]["pid"]) if procs else 0,
                process=str(procs[0]["name"]) if procs else "",
                recvq=int(row.get("recvq", 0)), sendq=int(row.get("sendq", 0)),
            ))
        return out

    def _from_psutil(self) -> list[Connection]:
        try:
            raw = psutil.net_connections(kind="inet")
        except (psutil.AccessDenied, PermissionError):
            return []
        out = []
        for item in raw:
            proto = "tcp" if item.type.name == "SOCK_STREAM" else "udp"
            family = "ipv6" if item.family.name == "AF_INET6" else "ipv4"
            if family == "ipv6":
                proto += "6"
            status = item.status if item.status != "NONE" else (
                "ESTABLISHED" if item.raddr else "LISTEN")
            out.append(Connection(
                proto=proto, family=family,
                laddr=item.laddr.ip if item.laddr else "",
                lport=str(item.laddr.port) if item.laddr else "",
                raddr=item.raddr.ip if item.raddr else "",
                rport=str(item.raddr.port) if item.raddr else "",
                status=status,
                pid=item.pid or 0,
                process=self._process_name(item.pid),
            ))
        return out

    def _process_name(self, pid: int | None) -> str:
        if not pid:
            return ""
        cached = self._proc_names.get(pid)
        if cached is not None:
            return cached
        try:
            with open(f"/proc/{pid}/comm") as handle:
                name = handle.read().strip()
        except OSError:
            name = ""
        if len(self._proc_names) > 4096:
            self._proc_names.clear()
        self._proc_names[pid] = name
        return name


class NetworkMonitor(QObject):
    """GUI-thread handle on the sampling thread."""

    sampled = Signal(object)
    _beginRequested = Signal()
    _intervalRequested = Signal(int)

    def __init__(self, interval_ms: int = 2000,
                 helper_cache: Callable[[str], object] | None = None,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._thread = QThread()
        self._thread.setObjectName("fwpanel-netsampler")
        self._sampler = _Sampler(interval_ms, helper_cache)
        self._sampler.moveToThread(self._thread)
        self._sampler.sampled.connect(self.sampled)
        self._beginRequested.connect(self._sampler.begin, Qt.QueuedConnection)
        self._intervalRequested.connect(self._sampler.setInterval, Qt.QueuedConnection)
        self._latest: Sample | None = None
        self.sampled.connect(self._remember)

    @property
    def latest(self) -> Sample | None:
        return self._latest

    @Slot(object)
    def _remember(self, sample: Sample) -> None:
        self._latest = sample

    def start(self) -> None:
        self._thread.start()
        self._beginRequested.emit()

    def set_interval(self, interval_ms: int) -> None:
        self._intervalRequested.emit(int(interval_ms))

    def stop(self) -> None:
        if not self._thread.isRunning():
            return
        QMetaObject.invokeMethod(self._sampler, "finish", Qt.BlockingQueuedConnection)
        self._thread.quit()
        self._thread.wait(3000)
