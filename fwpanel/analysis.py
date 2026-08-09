"""Posture analysis.

Turns a firewalld snapshot into a ranked list of findings. This is deliberately
opinionated but never alarmist: a finding says what is configured and why it
matters, and severity reflects exposure (is the zone actually bound to a live
interface?) rather than the mere presence of a setting.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import util

CRITICAL, HIGH, MEDIUM, LOW, INFO = "critical", "high", "medium", "low", "info"

_ORDER = {CRITICAL: 0, HIGH: 1, MEDIUM: 2, LOW: 3, INFO: 4}

#: Services worth calling out when they are reachable from an untrusted zone.
SENSITIVE_SERVICES = {
    "ssh": "remote shell",
    "telnet": "remote shell, unencrypted",
    "vnc-server": "remote desktop",
    "rdp": "remote desktop",
    "samba": "file sharing",
    "nfs": "file sharing",
    "nfs3": "file sharing",
    "mysql": "database",
    "postgresql": "database",
    "mongodb": "database",
    "redis": "datastore, often unauthenticated",
    "memcached": "datastore, often unauthenticated",
    "docker-registry": "container registry",
    "cockpit": "system administration",
}

#: Zones whose whole purpose is to be permissive - not a finding on their own.
INTENTIONALLY_OPEN = {"trusted", "internal", "home", "work",
                      "libvirt", "libvirt-routed", "nm-shared"}


@dataclass
class Finding:
    severity: str
    title: str
    detail: str
    zone: str = ""
    hint: str = ""

    @property
    def rank(self) -> int:
        return _ORDER.get(self.severity, 9)


def _normalise(settings: dict) -> dict:
    """Reduce a zone settings dict to the fields worth diffing."""
    keys = ("services", "ports", "protocols", "source_ports", "icmp_blocks",
            "forward_ports", "rules_str", "sources", "interfaces",
            "masquerade", "forward", "target", "icmp_block_inversion")
    out = {}
    for key in keys:
        value = settings.get(key)
        if isinstance(value, list):
            out[key] = sorted(tuple(v) if isinstance(v, list) else v for v in value)
        else:
            out[key] = value
    return out


def zone_drift(snapshot) -> dict[str, list[str]]:
    """Zones whose runtime state differs from what is on disk."""
    drift: dict[str, list[str]] = {}
    for zone, runtime in snapshot.zones.items():
        permanent = snapshot.perm_zones.get(zone)
        if permanent is None or "_error" in runtime or "_error" in permanent:
            continue
        left, right = _normalise(runtime), _normalise(permanent)
        changed = [key for key in left if left[key] != right[key]]
        # Interfaces are routinely assigned at runtime by NetworkManager.
        changed = [key for key in changed if key != "interfaces"]
        if changed:
            drift[zone] = changed
    return drift


def assess(snapshot, sample=None) -> list[Finding]:
    findings: list[Finding] = []
    if not snapshot.ok:
        return [Finding(HIGH, "firewalld unavailable",
                        snapshot.error or "no connection to the firewalld daemon")]

    active = set(snapshot.active_zones)
    default_zone = snapshot.default_zone

    if snapshot.panic:
        findings.append(Finding(
            CRITICAL, "Panic mode is active",
            "All incoming and outgoing packets are dropped. Nothing on this "
            "machine can reach the network until panic mode is turned off.",
            hint="Disable it from the toolbar or the tray menu."))

    for zone, settings in sorted(snapshot.zones.items()):
        if "_error" in settings:
            continue
        is_active = zone in active
        interfaces = snapshot.active_zones.get(zone, {}).get("interfaces", [])
        sources = snapshot.active_zones.get(zone, {}).get("sources", [])
        where = ", ".join(interfaces + sources) or "no bound interface"
        target = settings.get("target", "default")

        if target in ("ACCEPT",) and is_active and zone not in INTENTIONALLY_OPEN:
            findings.append(Finding(
                HIGH, f"Zone '{zone}' accepts everything by default",
                f"Target is ACCEPT and the zone is bound to {where}. Any port "
                "not explicitly blocked is reachable.",
                zone=zone, hint="Set the target to 'default' or '%%REJECT%%'."))

        # Open ports.
        ports = settings.get("ports") or []
        if ports and is_active:
            listed = ", ".join(util.port_label(p, proto) for p, proto in ports[:8])
            more = f" (+{len(ports) - 8} more)" if len(ports) > 8 else ""
            severity = MEDIUM if zone in INTENTIONALLY_OPEN else HIGH
            findings.append(Finding(
                severity, f"{len(ports)} port(s) open in '{zone}'",
                f"{listed}{more} — reachable via {where}.", zone=zone))

        # Sensitive services.
        for service in settings.get("services") or []:
            if service in SENSITIVE_SERVICES and is_active:
                severity = MEDIUM if zone in INTENTIONALLY_OPEN else HIGH
                findings.append(Finding(
                    severity, f"'{service}' is allowed in zone '{zone}'",
                    f"{SENSITIVE_SERVICES[service]} — reachable via {where}.",
                    zone=zone,
                    hint="Restrict it with a rich rule limited to known sources."))

        if settings.get("masquerade") and is_active:
            findings.append(Finding(
                LOW, f"Masquerading is on for '{zone}'",
                f"This host NATs traffic leaving {where}. Expected on a router "
                "or a VM host, unexpected on a workstation.", zone=zone))

        forward_ports = settings.get("forward_ports") or []
        for entry in forward_ports:
            port, proto, toport, toaddr = (list(entry) + ["", "", "", ""])[:4]
            findings.append(Finding(
                MEDIUM if zone in active else LOW,
                f"Port forward in '{zone}': {port}/{proto}",
                f"Forwarded to {toaddr or 'this host'}:{toport or port}.",
                zone=zone))

        for rule in settings.get("rules_str") or []:
            text = str(rule)
            if "accept" in text and "source" not in text and zone in active:
                findings.append(Finding(
                    MEDIUM, f"Unscoped accept rule in '{zone}'",
                    util.elide(text, 160), zone=zone,
                    hint="Add a source address so the rule is not open to the world."))

        if settings.get("icmp_block_inversion") and is_active:
            findings.append(Finding(
                LOW, f"ICMP block inversion in '{zone}'",
                "The ICMP block list is inverted: everything not listed is "
                "blocked. Easy to misread when auditing.", zone=zone))

    if default_zone in ("trusted",):
        findings.append(Finding(
            HIGH, "Default zone is 'trusted'",
            "Any new interface without an explicit zone accepts all traffic.",
            zone=default_zone))

    if snapshot.direct_rules:
        findings.append(Finding(
            MEDIUM, f"{len(snapshot.direct_rules)} direct rule(s) in effect",
            "Direct rules are injected ahead of firewalld's own chains and are "
            "invisible to the zone model. Audit them by hand.",
            hint="See the Direct tab."))

    if snapshot.direct_passthroughs:
        findings.append(Finding(
            MEDIUM, f"{len(snapshot.direct_passthroughs)} passthrough rule(s)",
            "Raw iptables/nftables arguments are being passed straight to the "
            "kernel, bypassing firewalld's validation."))

    if snapshot.log_denied == "off":
        findings.append(Finding(
            LOW, "Denied packets are not logged",
            "LogDenied is off, so blocked traffic leaves no audit trail and the "
            "Denied timeline stays empty.",
            hint="Set LogDenied to 'unicast' or 'all' from the toolbar."))

    drift = zone_drift(snapshot)
    if drift:
        names = ", ".join(f"{z} ({', '.join(k)})" for z, k in sorted(drift.items())[:4])
        findings.append(Finding(
            MEDIUM, f"{len(drift)} zone(s) have unsaved runtime changes",
            f"Runtime differs from the permanent configuration: {names}. These "
            "changes are lost on reload or reboot.",
            hint="Use 'Runtime → Permanent' to persist them."))

    if not snapshot.ipv6:
        findings.append(Finding(
            INFO, "IPv6 support is disabled in firewalld",
            "ip6tables/nftables IPv6 handling is off; IPv6 traffic is not "
            "filtered by these rules."))

    if sample is not None and sample.conntrack_max:
        ratio = sample.conntrack_count / sample.conntrack_max
        if ratio > 0.85:
            findings.append(Finding(
                HIGH, "Connection tracking table is nearly full",
                f"{sample.conntrack_count} of {sample.conntrack_max} slots used "
                f"({ratio:.0%}). New connections will be dropped when it fills.",
                hint="Raise net.netfilter.nf_conntrack_max."))
        elif ratio > 0.6:
            findings.append(Finding(
                LOW, "Connection tracking table is filling up",
                f"{sample.conntrack_count} of {sample.conntrack_max} slots used "
                f"({ratio:.0%})."))

    findings.sort(key=lambda f: (f.rank, f.title))
    return findings


def posture_score(findings: list[Finding]) -> tuple[int, str]:
    """A 0-100 score with a one-word verdict, for the dashboard gauge."""
    weights = {CRITICAL: 34, HIGH: 13, MEDIUM: 6, LOW: 2, INFO: 0}
    score = 100 - sum(weights.get(f.severity, 0) for f in findings)
    score = max(0, min(100, score))
    if score >= 88:
        return score, "solid"
    if score >= 70:
        return score, "fair"
    if score >= 45:
        return score, "loose"
    return score, "exposed"
