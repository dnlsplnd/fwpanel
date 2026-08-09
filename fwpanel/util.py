"""Small formatting and parsing helpers shared across the UI."""

from __future__ import annotations

import ipaddress
import socket

_UNITS = ("B", "KB", "MB", "GB", "TB", "PB")
_RATE_UNITS = ("B/s", "KB/s", "MB/s", "GB/s", "TB/s")


def human_bytes(value: float, units=_UNITS) -> str:
    value = float(value)
    idx = 0
    while value >= 1024.0 and idx < len(units) - 1:
        value /= 1024.0
        idx += 1
    if idx == 0:
        return f"{value:.0f} {units[idx]}"
    return f"{value:.1f} {units[idx]}"


def human_rate(value: float) -> str:
    return human_bytes(value, _RATE_UNITS)


def human_count(value: float) -> str:
    for suffix, div in (("G", 1e9), ("M", 1e6), ("k", 1e3)):
        if value >= div:
            return f"{value / div:.1f}{suffix}"
    return f"{value:.0f}"


def human_duration(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60}s"
    if seconds < 86400:
        return f"{seconds // 3600}h {(seconds % 3600) // 60}m"
    return f"{seconds // 86400}d {(seconds % 86400) // 3600}h"


def port_label(port: str, proto: str) -> str:
    """Render a firewalld (port, protocol) pair, expanding well-known names."""
    name = service_name_for_port(port, proto)
    return f"{port}/{proto}" + (f"  ({name})" if name else "")


_SERVICE_CACHE: dict[tuple[str, str], str] = {}


def service_name_for_port(port: str, proto: str) -> str:
    """Best-effort /etc/services lookup; ranges and unknown ports return ''."""
    if "-" in str(port):
        return ""
    key = (str(port), proto)
    if key in _SERVICE_CACHE:
        return _SERVICE_CACHE[key]
    try:
        name = socket.getservbyport(int(port), proto)
    except (OSError, ValueError, TypeError):
        name = ""
    _SERVICE_CACHE[key] = name
    return name


def is_valid_address(text: str) -> bool:
    """Accept a bare address or a CIDR network, v4 or v6."""
    text = text.strip()
    if not text:
        return False
    try:
        ipaddress.ip_network(text, strict=False)
        return True
    except ValueError:
        return False


def address_family(text: str) -> str:
    try:
        net = ipaddress.ip_network(text.split("/")[0].strip(), strict=False)
    except ValueError:
        return "ipv4"
    return "ipv6" if net.version == 6 else "ipv4"


def is_private_address(text: str) -> bool:
    try:
        addr = ipaddress.ip_address(text.strip())
    except ValueError:
        return False
    return addr.is_private or addr.is_loopback or addr.is_link_local


def parse_port_spec(text: str) -> tuple[str, str] | None:
    """Parse '8080/tcp' or '5000-5100/udp' into (port, protocol)."""
    text = text.strip()
    if "/" not in text:
        return None
    port, _, proto = text.rpartition("/")
    proto = proto.lower()
    if proto not in ("tcp", "udp", "sctp", "dccp"):
        return None
    port = port.strip()
    parts = port.split("-")
    if len(parts) > 2:
        return None
    for part in parts:
        if not part.isdigit() or not 0 < int(part) <= 65535:
            return None
    if len(parts) == 2 and int(parts[0]) > int(parts[1]):
        return None
    return port, proto


def elide(text: str, limit: int) -> str:
    text = str(text)
    return text if len(text) <= limit else text[: limit - 1] + "…"
