"""The three long-lived data sources, handed to every tab."""

from __future__ import annotations

from dataclasses import dataclass

from .backend.firewalld import FirewalldService
from .backend.helper_client import HelperClient
from .backend.netstats import NetworkMonitor


@dataclass
class AppContext:
    fw: FirewalldService
    net: NetworkMonitor
    helper: HelperClient

    def snapshot(self):
        return self.fw.snapshot

    def sample(self):
        return self.net.latest
