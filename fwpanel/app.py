"""Application wiring, single-instance guard and lifecycle."""

from __future__ import annotations

import argparse
import os
import signal
import sys

from PySide6.QtCore import QTimer
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon

from . import APP_ID, APP_NAME, __version__
from .backend.firewalld import FirewalldService
from .backend.helper_client import HelperClient
from .backend.netstats import NetworkMonitor
from .context import AppContext
from .ui import icons, theme
from .ui.mainwindow import MainWindow
from .ui.tray import Tray

SOCKET_NAME = f"fwpanel-{os.getuid()}"


def _existing_instance() -> bool:
    """Ask a running instance to show itself; True if one answered."""
    socket = QLocalSocket()
    socket.connectToServer(SOCKET_NAME)
    if socket.waitForConnected(400):
        socket.write(b"show")
        socket.flush()
        socket.waitForBytesWritten(400)
        socket.disconnectFromServer()
        return True
    return False


class Application:
    def __init__(self, argv: list[str]) -> None:
        self.args = self._parse_args(argv[1:])

        self.qt = QApplication(argv)
        self.qt.setApplicationName(APP_NAME)
        self.qt.setApplicationDisplayName(APP_NAME)
        self.qt.setApplicationVersion(__version__)
        self.qt.setDesktopFileName(APP_ID)
        self.qt.setOrganizationName("fwpanel")
        self.qt.setQuitOnLastWindowClosed(False)
        theme.apply(self.qt)
        self.qt.setWindowIcon(icons.app_icon())

        self.helper = HelperClient()
        self.fw = FirewalldService()
        self.net = NetworkMonitor(interval_ms=2000,
                                  helper_cache=self.helper.cached)
        self.ctx = AppContext(fw=self.fw, net=self.net, helper=self.helper)

        self.window = MainWindow(self.ctx)
        self.tray = Tray(self.ctx)
        self._wire()

        self.server = QLocalServer()
        QLocalServer.removeServer(SOCKET_NAME)
        self.server.listen(SOCKET_NAME)
        self.server.newConnection.connect(self._on_new_connection)

        # Keep sockets and conntrack fresh once the helper is up.
        self._helper_poll = QTimer()
        self._helper_poll.setInterval(3000)
        self._helper_poll.timeout.connect(self._poll_helper)

    # -- setup --------------------------------------------------------------
    @staticmethod
    def _parse_args(argv: list[str]) -> argparse.Namespace:
        parser = argparse.ArgumentParser(
            prog="fwpanel",
            description="An advanced firewalld control panel for KDE Plasma.")
        parser.add_argument("--hidden", action="store_true",
                            help="start in the system tray without opening the window")
        parser.add_argument("--no-tray", action="store_true",
                            help="run as a plain window with no tray icon")
        parser.add_argument("--version", action="version",
                            version=f"{APP_NAME} {__version__}")
        return parser.parse_args(argv)

    def _wire(self) -> None:
        self.tray.showRequested.connect(self.show_window)
        self.tray.quitRequested.connect(self.quit)
        self.tray.reloadRequested.connect(self.window.reload_firewall)
        self.tray.persistRequested.connect(self.window.persist_runtime)
        self.tray.panicToggled.connect(self._tray_panic)
        self.tray.defaultZoneRequested.connect(self.window.set_default_zone)

        self.helper.stateChanged.connect(self._on_helper_state)
        self.fw.connectionChanged.connect(self._on_connection)
        self.fw.firewallEvent.connect(self._on_firewall_event)

    # -- lifecycle ----------------------------------------------------------
    def run(self) -> int:
        self.fw.start()
        self.net.start()

        if not self.args.no_tray:
            self._install_tray()

        if not self.args.hidden:
            self.window.show()

        # Let Ctrl-C through: Qt's loop otherwise swallows SIGINT entirely.
        signal.signal(signal.SIGINT, lambda *_: self.quit())
        signal.signal(signal.SIGTERM, lambda *_: self.quit())
        wake = QTimer()
        wake.start(400)
        wake.timeout.connect(lambda: None)

        self.qt.aboutToQuit.connect(self._teardown)
        return self.qt.exec()

    def _install_tray(self) -> None:
        """Claim a tray slot, retrying while Plasma's tray host starts up.

        Started as a user service, we routinely win the race against
        plasmashell: there is no StatusNotifierWatcher yet, so a one-shot
        show() would silently do nothing.
        """
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()
            return

        self._tray_attempts = 0
        self._tray_retry = QTimer()
        self._tray_retry.setInterval(2000)

        def attempt() -> None:
            self._tray_attempts += 1
            if QSystemTrayIcon.isSystemTrayAvailable():
                self._tray_retry.stop()
                self.tray.show()
                return
            if self._tray_attempts >= 45:  # ~90 seconds
                self._tray_retry.stop()
                if self.args.hidden:
                    # Hidden with no tray would be an unreachable process.
                    self.show_window()
                    QMessageBox.warning(
                        None, APP_NAME,
                        "No system tray appeared, so the panel opened its window "
                        "instead. In Plasma, check that the System Tray widget is "
                        "present on your panel.")

        self._tray_retry.timeout.connect(attempt)
        self._tray_retry.start()

    def show_window(self) -> None:
        self.window.show()
        self.window.setWindowState(
            self.window.windowState() & ~self.window.windowState().WindowMinimized)
        self.window.raise_()
        self.window.activateWindow()

    def quit(self) -> None:
        self.window.prepare_quit()
        self.qt.quit()

    def _teardown(self) -> None:
        self._helper_poll.stop()
        self.window.logs.shutdown()
        self.tray.hide()
        self.net.stop()
        self.fw.stop()
        self.helper.stop()
        self.server.close()
        QLocalServer.removeServer(SOCKET_NAME)

    # -- reactions ----------------------------------------------------------
    def _on_new_connection(self) -> None:
        socket = self.server.nextPendingConnection()
        if socket is None:
            return
        socket.readyRead.connect(lambda: (socket.readAll(), self.show_window()))
        socket.disconnected.connect(socket.deleteLater)

    def _tray_panic(self, enabled: bool) -> None:
        # Route through the window so the confirmation prompt is identical.
        self.show_window()
        self.window.panic_button.setChecked(enabled)
        self.window.toggle_panic(enabled)

    def _on_helper_state(self, elevated: bool, _message: str) -> None:
        if elevated:
            self._poll_helper()
            self._helper_poll.start()
        else:
            self._helper_poll.stop()

    def _poll_helper(self) -> None:
        if self.helper.elevated:
            self.helper.request("sockets")

    def _on_connection(self, connected: bool, message: str) -> None:
        if not connected:
            self.tray.notify(
                APP_NAME,
                f"Lost contact with firewalld: {message}" if message
                else "firewalld is not available.",
                QSystemTrayIcon.Warning)

    def _on_firewall_event(self, name: str, _args) -> None:
        if name == "panic-mode-enabled":
            self.tray.notify(APP_NAME, "Panic mode enabled — all traffic is blocked.",
                             QSystemTrayIcon.Critical)
        elif name == "panic-mode-disabled":
            self.tray.notify(APP_NAME, "Panic mode disabled — traffic flows again.")
        elif name == "reloaded":
            self.tray.notify(APP_NAME, "Firewall reloaded.")


def main(argv: list[str] | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv)
    if "--version" not in argv and "--help" not in argv and "-h" not in argv:
        if _existing_instance():
            print("fwpanel is already running; raised the existing window.")
            return 0
    return Application(argv).run()
