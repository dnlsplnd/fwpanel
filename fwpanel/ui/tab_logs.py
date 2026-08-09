"""Logs tab: a live tail of firewalld and kernel netfilter denials."""

from __future__ import annotations

import re
from collections import deque

from PySide6.QtCore import QProcess, QTimer, Qt
from PySide6.QtGui import QAction, QGuiApplication, QTextCursor
from PySide6.QtWidgets import (QCheckBox, QComboBox, QHBoxLayout, QLabel, QMenu,
                               QPlainTextEdit, QSplitter, QVBoxLayout, QWidget)

from ..util import is_valid_address
from . import theme
from .charts import StackedTimeline
from .dialogs import BlockAddressDialog
from .widgets import Badge, Card, FilterTable, SearchBox, toolbar_button

#: Prefixes firewalld/netfilter attach to dropped or rejected packets.
DENIED_RE = re.compile(
    r"(?P<prefix>[A-Za-z0-9_\-]*(?:_DROP|_REJECT|_DENY|REJECT|DROP))\s*:")
FIELD_RE = re.compile(r"\b([A-Z]+)=(\S*)")
LOG_DENIED_CHOICES = ("off", "all", "unicast", "broadcast", "multicast")


class LogsTab(QWidget):
    def __init__(self, ctx, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._processes: list[QProcess] = []
        self._failures = 0
        self._bucket = 0
        self._denied_total = 0
        self._recent_sources: deque = deque(maxlen=500)
        self._paused = False
        self._build()

        self._tick = QTimer(self)
        self._tick.setInterval(1000)
        self._tick.timeout.connect(self._flush_bucket)
        self._tick.start()

        ctx.fw.snapshotChanged.connect(self._on_snapshot)
        self.start_tail()

    # -- construction -------------------------------------------------------
    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 14)
        layout.setSpacing(10)

        bar = QHBoxLayout()
        bar.setSpacing(9)
        bar.addWidget(QLabel("Log denied packets"))
        self.log_denied_combo = QComboBox()
        self.log_denied_combo.addItems(LOG_DENIED_CHOICES)
        self.log_denied_combo.setToolTip(
            "firewalld's LogDenied setting. Without it the kernel never records "
            "blocked packets and this tab stays quiet.")
        self.log_denied_combo.activated.connect(self._set_log_denied)
        bar.addWidget(self.log_denied_combo)

        self.search = SearchBox("Filter denied events…")
        self.search.textChanged.connect(lambda t: self.table.set_filter(t))
        bar.addWidget(self.search, 1)

        self.pause_button = toolbar_button("Pause")
        self.pause_button.setCheckable(True)
        self.pause_button.toggled.connect(self._on_pause)
        bar.addWidget(self.pause_button)

        clear = toolbar_button("Clear")
        clear.clicked.connect(self._clear)
        bar.addWidget(clear)

        self.status_badge = Badge("STARTING", theme.NEUTRAL)
        bar.addWidget(self.status_badge)
        layout.addLayout(bar)

        timeline_card = Card("denied packets per second")
        self.timeline = StackedTimeline()
        timeline_card.body.addWidget(self.timeline)
        self.timeline_note = QLabel()
        self.timeline_note.setObjectName("Hint")
        timeline_card.body.addWidget(self.timeline_note)
        layout.addWidget(timeline_card)

        splitter = QSplitter(Qt.Vertical)
        layout.addWidget(splitter, 1)

        denied_card = Card("denied packets")
        self.table = FilterTable(
            ["Time", "Chain", "Interface", "Source", "Src port", "Destination",
             "Dst port", "Proto", "Packet"])
        self.table.view.customContextMenuRequested.connect(self._context_menu)
        self.table.stretch_column(8)  # packet detail earns the leftover width
        # Fixed once here: denials arrive continuously, and resizing to
        # contents per row would be O(rows) on every packet.
        for column, width in ((0, 85), (1, 190), (2, 90), (3, 140), (4, 80),
                              (5, 140), (6, 80), (7, 65)):
            self.table.view.setColumnWidth(column, width)
        denied_card.body.addWidget(self.table)
        splitter.addWidget(denied_card)

        raw_card = Card("journal")
        controls = QHBoxLayout()
        self.follow_check = QCheckBox("Follow")
        self.follow_check.setChecked(True)
        controls.addWidget(self.follow_check)
        self.only_denied = QCheckBox("Only denied packets")
        self.only_denied.toggled.connect(self._rebuild_raw)
        controls.addWidget(self.only_denied)
        controls.addStretch(1)
        copy_button = toolbar_button("Copy visible")
        copy_button.clicked.connect(
            lambda: QGuiApplication.clipboard().setText(self.raw.toPlainText()))
        controls.addWidget(copy_button)
        raw_card.body.addLayout(controls)

        self.raw = QPlainTextEdit()
        self.raw.setReadOnly(True)
        self.raw.setMaximumBlockCount(4000)
        self.raw.setFont(theme.mono_font(9))
        self.raw.setLineWrapMode(QPlainTextEdit.NoWrap)
        raw_card.body.addWidget(self.raw)
        splitter.addWidget(raw_card)
        splitter.setSizes([320, 260])

        self._all_lines: deque = deque(maxlen=4000)

    # -- journal tail -------------------------------------------------------
    #: journalctl ANDs unlike matchers, so "-k -u firewalld" selects nothing.
    #: Two tails, merged here, is the only way to watch both.
    TAILS = (
        ["-f", "-n", "300", "-o", "short-iso", "--no-pager", "-q", "-k"],
        ["-f", "-n", "60", "-o", "short-iso", "--no-pager", "-q", "-u", "firewalld"],
    )

    def start_tail(self) -> None:
        if self._processes:
            return
        for args in self.TAILS:
            process = QProcess(self)
            process.setProcessChannelMode(QProcess.MergedChannels)
            process.readyReadStandardOutput.connect(
                lambda p=process: self._on_output(p))
            process.finished.connect(lambda *_a: self._on_finished())
            process.errorOccurred.connect(lambda *_a: self._on_error())
            process.start("journalctl", args)
            self._processes.append(process)
        self.status_badge.set_state("TAILING", theme.ACCENT)

    def stop_tail(self) -> None:
        for process in self._processes:
            try:
                process.readyReadStandardOutput.disconnect()
                process.finished.disconnect()
                process.errorOccurred.disconnect()
            except (RuntimeError, TypeError):
                pass
            process.terminate()
            if not process.waitForFinished(1500):
                process.kill()
        self._processes.clear()

    def _on_error(self) -> None:
        self._failures += 1
        if self._failures >= len(self.TAILS):
            self.status_badge.set_state("NO JOURNAL ACCESS", theme.DANGER)
            self.timeline_note.setText(
                "Could not read the journal. Add your user to the "
                "'systemd-journal' group: sudo usermod -aG systemd-journal $USER")

    def _on_finished(self) -> None:
        if all(p.state() == QProcess.NotRunning for p in self._processes):
            self.status_badge.set_state("STOPPED", theme.WARN)

    def _on_output(self, process: QProcess) -> None:
        chunk = bytes(process.readAllStandardOutput()).decode("utf-8", "replace")
        for line in chunk.splitlines():
            if line.strip():
                self._ingest(line)
        # journalctl delivers its whole backlog in one chunk, and the per-line
        # scroll reset can lose that race; settle it once the batch is in.
        if self.follow_check.isChecked():
            self.raw.horizontalScrollBar().setValue(0)

    def _ingest(self, line: str) -> None:
        if self._paused:
            return
        self._all_lines.append(line)
        match = DENIED_RE.search(line)
        if match:
            self._bucket += 1
            self._denied_total += 1
            self._add_denied(line, match.group("prefix"))
        if not self.only_denied.isChecked() or match:
            self._append_raw(line, bool(match))

    def _append_raw(self, line: str, denied: bool) -> None:
        scroll = self.raw.verticalScrollBar()
        at_bottom = scroll.value() >= scroll.maximum() - 4
        if denied:
            self.raw.appendHtml(
                f'<span style="color:{theme.DANGER}">{_escape(line)}</span>')
        else:
            self.raw.appendPlainText(line)
        if self.follow_check.isChecked() and at_bottom:
            # Appending parks the text cursor at the end of the line, and Qt
            # keeps that cursor visible on every repaint - which drags the
            # view right and hides the timestamp on these long kernel lines.
            # Anchor the cursor to the start of the last line instead, then
            # follow by scrolling the viewport.
            cursor = self.raw.textCursor()
            cursor.movePosition(QTextCursor.End)
            cursor.movePosition(QTextCursor.StartOfLine)
            self.raw.setTextCursor(cursor)
            scroll.setValue(scroll.maximum())
            self.raw.horizontalScrollBar().setValue(0)

    def _rebuild_raw(self) -> None:
        self.raw.clear()
        for line in self._all_lines:
            denied = bool(DENIED_RE.search(line))
            if self.only_denied.isChecked() and not denied:
                continue
            self._append_raw(line, denied)

    def _add_denied(self, line: str, prefix: str) -> None:
        fields = dict(FIELD_RE.findall(line))
        stamp = line.split(" ", 1)[0]
        source = fields.get("SRC", "")
        if source:
            self._recent_sources.append(source)

        # TCP flags are bare words in the kernel's format, not KEY=VALUE.
        flags = [flag for flag in ("SYN", "ACK", "RST", "FIN", "PSH", "URG")
                 if f" {flag} " in f" {line} "]
        detail = " ".join(filter(None, [
            f"len {fields['LEN']}" if "LEN" in fields else "",
            f"ttl {fields['TTL']}" if "TTL" in fields else "",
            " ".join(flags).lower(),
        ])) or "—"

        self.table.append(
            [stamp.split("T")[-1][:8] or stamp, prefix,
             fields.get("IN", "") or fields.get("OUT", "") or "—",
             source or "—", fields.get("SPT", "") or "—",
             fields.get("DST", "") or "—", fields.get("DPT", "") or "—",
             fields.get("PROTO", "") or "—", detail],
            colors={1: theme.DANGER, 3: theme.WARN, 8: theme.FG_FAINT},
            sort_keys={4: int(fields["SPT"]) if fields.get("SPT", "").isdigit() else 0,
                       6: int(fields["DPT"]) if fields.get("DPT", "").isdigit() else 0},
            payload=source, monospace=[0, 3, 5])
        # Keep the table bounded; the journal pane keeps the full history.
        while self.table.row_count() > 2000:
            self.table.model.removeRow(0)

    def _flush_bucket(self) -> None:
        self.timeline.push(self._bucket)
        self._bucket = 0
        top = ""
        if self._recent_sources:
            counts: dict[str, int] = {}
            for address in self._recent_sources:
                counts[address] = counts.get(address, 0) + 1
            address, hits = max(counts.items(), key=lambda kv: kv[1])
            top = f" · most blocked source: {address} ({hits})"
        self.timeline_note.setText(
            f"{self._denied_total} denied packets logged this session{top}")

    # -- controls -----------------------------------------------------------
    def _on_pause(self, paused: bool) -> None:
        self._paused = paused
        self.pause_button.setText("Resume" if paused else "Pause")
        self.status_badge.set_state("PAUSED" if paused else "TAILING",
                                    theme.WARN if paused else theme.ACCENT)

    def _clear(self) -> None:
        self.table.clear_rows()
        self.raw.clear()
        self._all_lines.clear()
        self._recent_sources.clear()
        self._denied_total = 0

    def _set_log_denied(self) -> None:
        value = self.log_denied_combo.currentText()
        self.ctx.fw.runtime("setLogDenied", value,
                            description=f"set LogDenied to {value}")

    def _on_snapshot(self, snap) -> None:
        if snap.log_denied and snap.log_denied != self.log_denied_combo.currentText():
            self.log_denied_combo.blockSignals(True)
            self.log_denied_combo.setCurrentText(snap.log_denied)
            self.log_denied_combo.blockSignals(False)

    # -- actions ------------------------------------------------------------
    def _context_menu(self, pos) -> None:
        row = self.table.row_at(pos)
        if row < 0:
            return
        self.table.select_source_row(row)
        address = self.table.payload(row)
        if not address or not is_valid_address(str(address)):
            return
        menu = QMenu(self)
        block = QAction(f"Block {address} permanently…", self)
        block.triggered.connect(lambda: self._block(str(address)))
        menu.addAction(block)
        copy = QAction("Copy source address", self)
        copy.triggered.connect(lambda: QGuiApplication.clipboard().setText(str(address)))
        menu.addAction(copy)
        menu.exec(self.table.view.viewport().mapToGlobal(pos))

    def _block(self, address: str) -> None:
        snap = self.ctx.snapshot()
        dialog = BlockAddressDialog(address, sorted(snap.zones) or ["public"],
                                    snap.default_zone, self)
        if dialog.exec() != BlockAddressDialog.Accepted:
            return
        rule = dialog.rule_text()
        if dialog.apply_runtime:
            self.ctx.fw.runtime("addRichRule", dialog.zone, rule, dialog.timeout,
                                description=f"block {address}")
        if dialog.apply_permanent:
            self.ctx.fw.perm_zone(dialog.zone, "addRichRule", rule,
                                  description=f"block {address} (permanent)")

    def shutdown(self) -> None:
        self.stop_tail()


def _escape(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
