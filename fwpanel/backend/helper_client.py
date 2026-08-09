"""Client for the optional privileged helper.

Some things the panel wants to show are root-only: ``/proc/net/nf_conntrack``,
the raw nftables ruleset, and the process behind another user's socket. Rather
than prompt for every read, we launch one long-lived helper through ``pkexec``
and speak newline-delimited JSON to it over pipes. One authentication, and the
helper dies with the panel.

The helper accepts a fixed set of no-argument commands; nothing the GUI types
is ever forwarded to it as a command line.
"""

from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import threading
from typing import Any

from PySide6.QtCore import QObject, Signal

HELPER_PATHS = (
    "/usr/libexec/fwpanel-helper",
    "/usr/local/libexec/fwpanel-helper",
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), "helper", "fwpanel-helper"),
)

#: Commands the helper understands. Kept here so the GUI cannot invent one.
COMMANDS = ("ping", "conntrack", "sockets", "nft", "nft_counters", "denied")


def helper_path() -> str:
    for path in HELPER_PATHS:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return ""


class HelperClient(QObject):
    """Owns the privileged subprocess and caches its most recent replies."""

    stateChanged = Signal(bool, str)      # elevated, message
    resultReady = Signal(str, object)     # command, data
    failed = Signal(str, str)             # command, error

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._proc: subprocess.Popen | None = None
        self._reader: threading.Thread | None = None
        self._writer: threading.Thread | None = None
        self._outbox: queue.Queue = queue.Queue()
        self._lock = threading.Lock()
        self._cache: dict[str, Any] = {}
        self._next_id = 1
        self._inflight: dict[int, str] = {}
        self._elevated = False
        self._stopping = False

    # -- state --------------------------------------------------------------
    @property
    def elevated(self) -> bool:
        return self._elevated and self._proc is not None and self._proc.poll() is None

    def cached(self, command: str, default: Any = None) -> Any:
        with self._lock:
            return self._cache.get(command, default)

    # -- lifecycle ----------------------------------------------------------
    def start(self) -> None:
        if self.elevated:
            return
        path = helper_path()
        if not path:
            self.stateChanged.emit(False, "fwpanel-helper is not installed")
            return
        if not shutil.which("pkexec"):
            self.stateChanged.emit(False, "pkexec not found")
            return

        env = dict(os.environ)
        # Give polkit the session context it needs to find the KDE agent.
        try:
            self._proc = subprocess.Popen(
                ["pkexec", path],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, env=env, bufsize=1,
                universal_newlines=True, close_fds=True,
            )
        except OSError as exc:
            self.stateChanged.emit(False, f"could not launch helper: {exc}")
            return

        self._stopping = False
        self._reader = threading.Thread(target=self._read_loop, daemon=True,
                                        name="fwpanel-helper-read")
        self._writer = threading.Thread(target=self._write_loop, daemon=True,
                                        name="fwpanel-helper-write")
        self._reader.start()
        self._writer.start()
        self.request("ping")

    def stop(self) -> None:
        self._stopping = True
        self._elevated = False
        proc, self._proc = self._proc, None
        self._outbox.put(None)
        if proc and proc.poll() is None:
            try:
                proc.stdin.close()
            except Exception:
                pass
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

    # -- requests -----------------------------------------------------------
    def request(self, command: str) -> None:
        if command not in COMMANDS:
            raise ValueError(f"unknown helper command {command!r}")
        if self._proc is None or self._proc.poll() is not None:
            return
        with self._lock:
            rid = self._next_id
            self._next_id += 1
            self._inflight[rid] = command
        self._outbox.put(json.dumps({"id": rid, "cmd": command}))

    # -- plumbing -----------------------------------------------------------
    def _write_loop(self) -> None:
        proc = self._proc
        while proc is not None and not self._stopping:
            item = self._outbox.get()
            if item is None:
                break
            try:
                proc.stdin.write(item + "\n")
                proc.stdin.flush()
            except (BrokenPipeError, ValueError, AttributeError):
                break

    def _read_loop(self) -> None:
        proc = self._proc
        if proc is None:
            return
        try:
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                self._dispatch(msg)
        except Exception:
            pass

        code = proc.wait()
        if self._stopping:
            return
        self._elevated = False
        self.stateChanged.emit(False, self._exit_reason(proc, code))

    def _dispatch(self, msg: dict) -> None:
        rid = msg.get("id")
        with self._lock:
            command = self._inflight.pop(rid, msg.get("cmd", ""))
        if msg.get("ok"):
            data = msg.get("data")
            with self._lock:
                self._cache[command] = data
            if command == "ping" and not self._elevated:
                self._elevated = True
                self.stateChanged.emit(True, "elevated access granted")
            self.resultReady.emit(command, data)
        else:
            self.failed.emit(command, str(msg.get("error", "unknown error")))

    def _exit_reason(self, proc: subprocess.Popen, code: int) -> str:
        if code == 126:
            return "authentication dismissed"
        if code == 127:
            return "helper could not be executed"
        try:
            err = (proc.stderr.read() or "").strip()
        except Exception:
            err = ""
        if err:
            return err.splitlines()[-1][:200]
        return f"helper exited with status {code}"
