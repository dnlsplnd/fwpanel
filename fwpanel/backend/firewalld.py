"""Threaded firewalld D-Bus backend.

All D-Bus traffic is confined to a single worker thread. The worker owns the
``FirewallClient`` and drives GLib's default main context with a Qt timer, so
firewalld's async signals arrive without a second event loop fighting Qt's.

The GUI talks to :class:`FirewalldService`, which marshals every request onto
the worker via queued connections and hands results back as Qt signals.
"""

from __future__ import annotations

import itertools
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from PySide6.QtCore import QObject, QThread, QTimer, Signal, Slot, Qt, QMetaObject, Q_ARG

# --- Object kinds addressable through :meth:`FirewalldService.call` ----------
RUNTIME = "runtime"          # methods on FirewallClient itself
CONFIG = "config"            # methods on FirewallClient.config()
CONFIG_ZONE = "config_zone"  # methods on a permanent zone object
CONFIG_SERVICE = "config_service"
CONFIG_IPSET = "config_ipset"
CONFIG_POLICY = "config_policy"


@dataclass
class Snapshot:
    """One consistent read of firewalld's state."""

    ok: bool = False
    error: str = ""
    stamp: float = field(default_factory=time.time)

    version: str = ""
    state: str = ""
    ipv4: bool = True
    ipv6: bool = True
    default_zone: str = ""
    log_denied: str = "off"
    panic: bool = False
    nf_conntrack_helpers: bool = False

    # zone name -> settings dict (getSettingsDict shape)
    zones: dict[str, dict] = field(default_factory=dict)
    perm_zones: dict[str, dict] = field(default_factory=dict)
    # zone name -> {"interfaces": [...], "sources": [...]}
    active_zones: dict[str, dict] = field(default_factory=dict)

    services: list[str] = field(default_factory=list)
    service_details: dict[str, dict] = field(default_factory=dict)
    icmptypes: list[str] = field(default_factory=list)
    helpers: list[str] = field(default_factory=list)
    policies: dict[str, dict] = field(default_factory=dict)
    ipsets: dict[str, dict] = field(default_factory=dict)

    direct_chains: list = field(default_factory=list)
    direct_rules: list = field(default_factory=list)
    direct_passthroughs: list = field(default_factory=list)

    def zone_of_interface(self, iface: str) -> str:
        for zone, info in self.active_zones.items():
            if iface in info.get("interfaces", []):
                return zone
        return ""

    def interfaces(self) -> list[tuple[str, str]]:
        out = []
        for zone, info in sorted(self.active_zones.items()):
            for iface in info.get("interfaces", []):
                out.append((iface, zone))
        return sorted(out)

    def settings_for(self, zone: str, permanent: bool) -> dict:
        table = self.perm_zones if permanent else self.zones
        return table.get(zone, {})


def _plain(value: Any) -> Any:
    """Strip dbus.* wrapper types so the GUI only ever sees builtins."""
    if isinstance(value, dict):
        return {_plain(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, bool):
        return bool(value)
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        return float(value)
    if isinstance(value, str):
        return str(value)
    return value


def _error_text(exc: BaseException) -> str:
    getter = getattr(exc, "get_dbus_message", None)
    if callable(getter):
        try:
            msg = getter()
            if msg:
                return str(msg)
        except Exception:
            pass
    text = str(exc)
    # firewalld wraps polkit denials in a long D-Bus name; make it readable.
    if "NotAuthorizedException" in text or "not authorized" in text.lower():
        return "Not authorized - the polkit prompt was dismissed or denied."
    return text or exc.__class__.__name__


class FirewalldWorker(QObject):
    """Lives on the D-Bus thread. Never touch its attributes from the GUI."""

    connectionChanged = Signal(bool, str)
    snapshotReady = Signal(object)
    callFinished = Signal(int, bool, object)
    firewallEvent = Signal(str, object)

    _EVENTS = (
        "connection-established", "connection-lost", "reloaded",
        "default-zone-changed", "log-denied-changed",
        "panic-mode-enabled", "panic-mode-disabled",
        "service-added", "service-removed",
        "port-added", "port-removed",
        "source-port-added", "source-port-removed",
        "protocol-added", "protocol-removed",
        "masquerade-added", "masquerade-removed",
        "forward-port-added", "forward-port-removed",
        "icmp-block-added", "icmp-block-removed",
        "icmp-block-inversion-added", "icmp-block-inversion-removed",
        "richrule-added", "richrule-removed",
        "interface-added", "interface-removed",
        "source-added", "source-removed",
        "zone-changed", "zone-of-interface-changed", "zone-of-source-changed",
        "ipset-entry-added", "ipset-entry-removed",
    )

    def __init__(self) -> None:
        super().__init__()
        self._client = None
        self._pump: QTimer | None = None
        self._connected = False

    # -- lifecycle ----------------------------------------------------------
    @Slot()
    def bootstrap(self) -> None:
        try:
            from firewall.client import FirewallClient
        except ImportError as exc:  # pragma: no cover - packaging problem
            self.connectionChanged.emit(False, f"python firewall module missing: {exc}")
            return

        try:
            self._client = FirewallClient(wait=0, quiet=True)
        except Exception as exc:
            self._client = None
            self.connectionChanged.emit(False, _error_text(exc))
            return

        for name in self._EVENTS:
            try:
                self._client.connect(name, self._make_event_relay(name))
            except ValueError:
                pass  # firewalld version without this signal

        # Pump GLib's default context so D-Bus signal callbacks actually fire.
        self._pump = QTimer(self)
        self._pump.setInterval(60)
        self._pump.timeout.connect(self._pump_glib)
        self._pump.start()

        self._connected = bool(getattr(self._client, "connected", True))
        self.connectionChanged.emit(self._connected, "" if self._connected else "firewalld is not running")
        self.buildSnapshot()

    def _make_event_relay(self, name: str) -> Callable[..., None]:
        def relay(*args):
            if name == "connection-established":
                self._connected = True
                self.connectionChanged.emit(True, "")
                # The proxies only exist now, so the first real read happens here.
                self.buildSnapshot()
            elif name == "connection-lost":
                self._connected = False
                self.connectionChanged.emit(False, "lost connection to firewalld")
            self.firewallEvent.emit(name, _plain(list(args)))
        return relay

    @Slot()
    def _pump_glib(self) -> None:
        try:
            from gi.repository import GLib
        except ImportError:
            if self._pump:
                self._pump.stop()
            return
        ctx = GLib.MainContext.default()
        # Bounded so a signal storm can never starve this thread's Qt loop.
        for _ in range(64):
            if not ctx.pending():
                break
            ctx.iteration(False)

    @Slot()
    def shutdown(self) -> None:
        if self._pump:
            self._pump.stop()
            self._pump = None
        self._client = None

    # -- reads --------------------------------------------------------------
    @Slot()
    def buildSnapshot(self) -> None:
        snap = Snapshot()
        fc = self._client
        if fc is None:
            snap.error = "not connected to firewalld"
            self.snapshotReady.emit(snap)
            return
        if not getattr(fc, "connected", False):
            # The D-Bus proxies are not wired up yet; a read now would only
            # produce a confusing AttributeError from inside the client.
            snap.error = "connecting to firewalld…"
            self.snapshotReady.emit(snap)
            return
        try:
            for prop, attr in (("version", "version"), ("state", "state"),
                               ("IPv4", "ipv4"), ("IPv6", "ipv6")):
                try:
                    setattr(snap, attr, _plain(fc.get_property(prop)))
                except Exception:
                    pass
            try:
                snap.nf_conntrack_helpers = bool(fc.get_property("nf_conntrack_helper_setting"))
            except Exception:
                pass

            snap.default_zone = str(fc.getDefaultZone())
            snap.log_denied = str(fc.getLogDenied())
            snap.panic = bool(fc.queryPanicMode())
            snap.active_zones = _plain(fc.getActiveZones())

            for zone in fc.getZones():
                zone = str(zone)
                try:
                    snap.zones[zone] = _plain(fc.getZoneSettings(zone).getSettingsDict())
                except Exception as exc:
                    snap.zones[zone] = {"_error": _error_text(exc)}

            cfg = fc.config()
            for zone in cfg.getZoneNames():
                zone = str(zone)
                try:
                    obj = cfg.getZoneByName(zone)
                    snap.perm_zones[zone] = _plain(obj.getSettings().getSettingsDict())
                except Exception as exc:
                    snap.perm_zones[zone] = {"_error": _error_text(exc)}

            snap.services = sorted(str(s) for s in fc.listServices())
            snap.icmptypes = sorted(str(s) for s in fc.listIcmpTypes())
            try:
                snap.helpers = sorted(str(s) for s in fc.getHelpers())
            except Exception:
                snap.helpers = []

            try:
                for pol in fc.getPolicies():
                    pol = str(pol)
                    snap.policies[pol] = _plain(fc.getPolicySettings(pol).getSettingsDict())
            except Exception:
                pass

            try:
                for name in fc.getIPSets():
                    name = str(name)
                    entry = _plain(fc.getIPSetSettings(name).getSettingsDict())
                    try:
                        entry["entries"] = _plain(fc.getEntries(name))
                    except Exception:
                        entry.setdefault("entries", [])
                    snap.ipsets[name] = entry
            except Exception:
                pass

            for attr, getter in (("direct_chains", "getAllChains"),
                                 ("direct_rules", "getAllRules"),
                                 ("direct_passthroughs", "getAllPassthroughs")):
                try:
                    setattr(snap, attr, _plain(getattr(fc, getter)()))
                except Exception:
                    setattr(snap, attr, [])

            snap.ok = True
        except Exception as exc:
            snap.ok = False
            snap.error = _error_text(exc)
        self.snapshotReady.emit(snap)

    @Slot(int, str)
    def fetchServiceDetails(self, token: int, name: str) -> None:
        fc = self._client
        if fc is None:
            self.callFinished.emit(token, False, "not connected")
            return
        try:
            data = _plain(fc.getServiceSettings(name).getSettingsDict())
            self.callFinished.emit(token, True, data)
        except Exception as exc:
            self.callFinished.emit(token, False, _error_text(exc))

    @Slot(int, str, str, str)
    def createZone(self, token: int, name: str, short: str, description: str) -> None:
        """Build a real settings object rather than hand-marshalling a dict."""
        fc = self._client
        if fc is None:
            self.callFinished.emit(token, False, "not connected")
            return
        try:
            from firewall.client import FirewallClientZoneSettings
            settings = FirewallClientZoneSettings()
            settings.setShort(short or name)
            settings.setDescription(description)
            settings.setTarget("default")
            fc.config().addZone(name, settings)
            self.callFinished.emit(token, True, name)
        except Exception as exc:
            self.callFinished.emit(token, False, _error_text(exc))

    @Slot(int, str, str, str)
    def createIPSet(self, token: int, name: str, ipset_type: str, description: str) -> None:
        fc = self._client
        if fc is None:
            self.callFinished.emit(token, False, "not connected")
            return
        try:
            from firewall.client import FirewallClientIPSetSettings
            settings = FirewallClientIPSetSettings()
            settings.setType(ipset_type)
            settings.setShort(name)
            settings.setDescription(description)
            fc.config().addIPSet(name, settings)
            self.callFinished.emit(token, True, name)
        except Exception as exc:
            self.callFinished.emit(token, False, _error_text(exc))

    # -- writes -------------------------------------------------------------
    @Slot(int, str, str, str, object)
    def invoke(self, token: int, kind: str, target: str, method: str, args: object) -> None:
        fc = self._client
        if fc is None:
            self.callFinished.emit(token, False, "not connected to firewalld")
            return
        args = list(args or [])
        try:
            obj = self._resolve(kind, target)
            result = getattr(obj, method)(*args)
            self.callFinished.emit(token, True, _plain(result))
        except Exception as exc:
            self.callFinished.emit(token, False, _error_text(exc))

    def _resolve(self, kind: str, target: str):
        fc = self._client
        if kind == RUNTIME:
            return fc
        cfg = fc.config()
        if kind == CONFIG:
            return cfg
        if kind == CONFIG_ZONE:
            return cfg.getZoneByName(target)
        if kind == CONFIG_SERVICE:
            return cfg.getServiceByName(target)
        if kind == CONFIG_IPSET:
            return cfg.getIPSetByName(target)
        if kind == CONFIG_POLICY:
            return cfg.getPolicyByName(target)
        raise ValueError(f"unknown object kind {kind!r}")


class FirewalldService(QObject):
    """GUI-thread facade over :class:`FirewalldWorker`."""

    connectionChanged = Signal(bool, str)
    snapshotChanged = Signal(object)
    firewallEvent = Signal(str, object)
    #: description, ok, message - for the status bar / notifications
    operationFinished = Signal(str, bool, str)

    # Requests are handed to the worker as queued signals rather than
    # QMetaObject.invokeMethod: Q_ARG cannot marshal Python containers.
    _bootstrapRequested = Signal()
    _snapshotRequested = Signal()
    _invokeRequested = Signal(int, str, str, str, object)
    _serviceDetailsRequested = Signal(int, str)
    _createZoneRequested = Signal(int, str, str, str)
    _createIPSetRequested = Signal(int, str, str, str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._thread = QThread()
        self._thread.setObjectName("firewalld-dbus")
        self._worker = FirewalldWorker()
        self._worker.moveToThread(self._thread)

        self._worker.connectionChanged.connect(self._on_connection)
        self._worker.snapshotReady.connect(self._on_snapshot)
        self._worker.callFinished.connect(self._on_call_finished)
        self._worker.firewallEvent.connect(self._on_event)

        self._bootstrapRequested.connect(self._worker.bootstrap, Qt.QueuedConnection)
        self._snapshotRequested.connect(self._worker.buildSnapshot, Qt.QueuedConnection)
        self._invokeRequested.connect(self._worker.invoke, Qt.QueuedConnection)
        self._serviceDetailsRequested.connect(self._worker.fetchServiceDetails,
                                              Qt.QueuedConnection)
        self._createZoneRequested.connect(self._worker.createZone, Qt.QueuedConnection)
        self._createIPSetRequested.connect(self._worker.createIPSet, Qt.QueuedConnection)

        self._tokens = itertools.count(1)
        self._pending: dict[int, tuple[str, Callable | None]] = {}
        self._snapshot = Snapshot()
        self._connected = False

        # firewalld emits a burst of signals per change; coalesce the refreshes.
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(250)
        self._refresh_timer.timeout.connect(self.refresh)

    # -- properties ---------------------------------------------------------
    @property
    def snapshot(self) -> Snapshot:
        return self._snapshot

    @property
    def connected(self) -> bool:
        return self._connected

    # -- lifecycle ----------------------------------------------------------
    def start(self) -> None:
        self._thread.start()
        self._bootstrapRequested.emit()

    def stop(self) -> None:
        if not self._thread.isRunning():
            return
        QMetaObject.invokeMethod(self._worker, "shutdown", Qt.BlockingQueuedConnection)
        self._thread.quit()
        self._thread.wait(3000)

    def refresh(self) -> None:
        self._snapshotRequested.emit()

    def refresh_soon(self) -> None:
        self._refresh_timer.start()

    # -- calls --------------------------------------------------------------
    def call(self, kind: str, target: str, method: str, *args,
             description: str = "", then: Callable[[bool, Any], None] | None = None) -> int:
        token = next(self._tokens)
        self._pending[token] = (description or method, then)
        self._invokeRequested.emit(token, kind, target, method, list(args))
        return token

    def runtime(self, method: str, *args, **kw) -> int:
        return self.call(RUNTIME, "", method, *args, **kw)

    def config(self, method: str, *args, **kw) -> int:
        return self.call(CONFIG, "", method, *args, **kw)

    def perm_zone(self, zone: str, method: str, *args, **kw) -> int:
        return self.call(CONFIG_ZONE, zone, method, *args, **kw)

    def create_zone(self, name: str, short: str = "", description: str = "") -> int:
        token = next(self._tokens)
        self._pending[token] = (f"create zone {name}", None)
        self._createZoneRequested.emit(token, name, short, description)
        return token

    def create_ipset(self, name: str, ipset_type: str = "hash:ip",
                     description: str = "") -> int:
        token = next(self._tokens)
        self._pending[token] = (f"create ipset {name}", None)
        self._createIPSetRequested.emit(token, name, ipset_type, description)
        return token

    def service_details(self, name: str, then: Callable[[bool, Any], None]) -> int:
        token = next(self._tokens)
        self._pending[token] = (f"service {name}", then)
        self._serviceDetailsRequested.emit(token, name)
        return token

    # -- slots --------------------------------------------------------------
    @Slot(bool, str)
    def _on_connection(self, ok: bool, message: str) -> None:
        self._connected = ok
        self.connectionChanged.emit(ok, message)

    @Slot(object)
    def _on_snapshot(self, snap: Snapshot) -> None:
        self._snapshot = snap
        self.snapshotChanged.emit(snap)

    @Slot(int, bool, object)
    def _on_call_finished(self, token: int, ok: bool, value: object) -> None:
        description, then = self._pending.pop(token, ("operation", None))
        if then is not None:
            try:
                then(ok, value)
            except Exception as exc:  # a broken callback must not kill the app
                self.operationFinished.emit(description, False, f"callback error: {exc}")
                return
        message = "" if ok else str(value)
        self.operationFinished.emit(description, ok, message)
        if ok:
            self.refresh_soon()

    @Slot(str, object)
    def _on_event(self, name: str, args: object) -> None:
        self.firewallEvent.emit(name, args)
        self.refresh_soon()


def validate_rich_rule(text: str) -> str:
    """Return '' if firewalld would accept this rich rule, else the error."""
    text = text.strip()
    if not text:
        return "rule is empty"
    try:
        from firewall.core.rich import Rich_Rule
    except ImportError:
        return ""  # cannot validate locally; let the daemon decide
    try:
        Rich_Rule(rule_str=text)
    except Exception as exc:
        return _error_text(exc)
    return ""
