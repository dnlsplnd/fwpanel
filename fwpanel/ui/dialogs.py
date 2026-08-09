"""Editing dialogs. Every one validates before it lets you press OK."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QFormLayout, QGroupBox, QHBoxLayout, QLabel,
                               QLineEdit, QPlainTextEdit, QSpinBox,
                               QVBoxLayout, QWidget)

from ..backend.firewalld import validate_rich_rule
from ..util import address_family, is_valid_address, parse_port_spec
from . import theme

PROTOCOLS = ("tcp", "udp", "sctp", "dccp")
TARGETS = ("default", "ACCEPT", "%%REJECT%%", "DROP")


class _BaseDialog(QDialog):
    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(460)
        self._outer = QVBoxLayout(self)
        self._outer.setContentsMargins(18, 16, 18, 14)
        self._outer.setSpacing(12)

        self.form = QFormLayout()
        self.form.setSpacing(9)
        self.form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._outer.addLayout(self.form)

        self.error = QLabel()
        self.error.setWordWrap(True)
        self.error.setStyleSheet(f"color: {theme.DANGER}; font-size: 9pt;")
        self.error.hide()
        self._outer.addWidget(self.error)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setProperty("accent", "true")
        self.buttons.accepted.connect(self._try_accept)
        self.buttons.rejected.connect(self.reject)
        self._outer.addWidget(self.buttons)

    def show_error(self, message: str) -> None:
        self.error.setText(message)
        self.error.setVisible(bool(message))

    def validate(self) -> str:
        return ""

    def _try_accept(self) -> None:
        problem = self.validate()
        if problem:
            self.show_error(problem)
            return
        self.accept()


def _permanence_box() -> tuple[QGroupBox, QCheckBox, QCheckBox]:
    box = QGroupBox("Apply to")
    layout = QHBoxLayout(box)
    runtime = QCheckBox("Runtime (immediate)")
    runtime.setChecked(True)
    permanent = QCheckBox("Permanent (survives reload)")
    permanent.setChecked(True)
    layout.addWidget(runtime)
    layout.addWidget(permanent)
    layout.addStretch(1)
    return box, runtime, permanent


class PermanenceMixin:
    """Adds the runtime/permanent choice to a dialog."""

    def add_permanence(self) -> None:
        box, self.runtime_check, self.permanent_check = _permanence_box()
        self._outer.insertWidget(self._outer.count() - 2, box)

    @property
    def apply_runtime(self) -> bool:
        return self.runtime_check.isChecked()

    @property
    def apply_permanent(self) -> bool:
        return self.permanent_check.isChecked()

    def validate_permanence(self) -> str:
        if not (self.apply_runtime or self.apply_permanent):
            return "Choose at least one of runtime or permanent."
        return ""


class PortDialog(_BaseDialog, PermanenceMixin):
    """Open a port or a port range."""

    def __init__(self, zone: str, parent: QWidget | None = None,
                 port: str = "", protocol: str = "tcp", source_port: bool = False) -> None:
        kind = "source port" if source_port else "port"
        super().__init__(f"{'Edit' if port else 'Add'} {kind} — zone '{zone}'", parent)
        self.zone = zone
        self.source_port = source_port

        self.port_edit = QLineEdit(port)
        self.port_edit.setPlaceholderText("8080  or  5000-5100")
        self.form.addRow("Port", self.port_edit)

        self.proto_combo = QComboBox()
        self.proto_combo.addItems(PROTOCOLS)
        self.proto_combo.setCurrentText(protocol)
        self.form.addRow("Protocol", self.proto_combo)

        hint = QLabel("A range uses <code>start-end</code>. Opening a port here "
                      "bypasses service definitions — prefer a service when one exists.")
        hint.setWordWrap(True)
        hint.setObjectName("Hint")
        self.form.addRow("", hint)
        self.add_permanence()

    def validate(self) -> str:
        spec = parse_port_spec(f"{self.port_edit.text().strip()}/{self.proto_combo.currentText()}")
        if spec is None:
            return "Enter a port between 1 and 65535, or a valid start-end range."
        return self.validate_permanence()

    def result_value(self) -> tuple[str, str]:
        return self.port_edit.text().strip(), self.proto_combo.currentText()


class ProtocolDialog(_BaseDialog, PermanenceMixin):
    def __init__(self, zone: str, parent: QWidget | None = None) -> None:
        super().__init__(f"Allow protocol — zone '{zone}'", parent)
        self.proto_edit = QLineEdit()
        self.proto_edit.setPlaceholderText("esp, ah, gre, icmp, igmp…")
        self.form.addRow("Protocol", self.proto_edit)
        self.add_permanence()

    def validate(self) -> str:
        value = self.proto_edit.text().strip()
        if not value or not value.replace("-", "").isalnum():
            return "Enter a protocol name as it appears in /etc/protocols."
        return self.validate_permanence()

    def result_value(self) -> str:
        return self.proto_edit.text().strip()


class ForwardPortDialog(_BaseDialog, PermanenceMixin):
    """Forward a port to another port and/or another host."""

    def __init__(self, zone: str, parent: QWidget | None = None) -> None:
        super().__init__(f"Forward port — zone '{zone}'", parent)

        self.port_edit = QLineEdit()
        self.port_edit.setPlaceholderText("80  or  8000-8010")
        self.form.addRow("Incoming port", self.port_edit)

        self.proto_combo = QComboBox()
        self.proto_combo.addItems(("tcp", "udp"))
        self.form.addRow("Protocol", self.proto_combo)

        self.toport_edit = QLineEdit()
        self.toport_edit.setPlaceholderText("leave empty to keep the same port")
        self.form.addRow("To port", self.toport_edit)

        self.toaddr_edit = QLineEdit()
        self.toaddr_edit.setPlaceholderText("leave empty to forward on this host")
        self.form.addRow("To address", self.toaddr_edit)

        note = QLabel("Forwarding to another host needs masquerading enabled on "
                      "this zone, and IP forwarding enabled in the kernel.")
        note.setWordWrap(True)
        note.setObjectName("Hint")
        self.form.addRow("", note)
        self.add_permanence()

    def validate(self) -> str:
        if parse_port_spec(f"{self.port_edit.text().strip()}/{self.proto_combo.currentText()}") is None:
            return "The incoming port is not valid."
        toport = self.toport_edit.text().strip()
        if toport and parse_port_spec(f"{toport}/{self.proto_combo.currentText()}") is None:
            return "The destination port is not valid."
        toaddr = self.toaddr_edit.text().strip()
        if toaddr and not is_valid_address(toaddr):
            return "The destination address is not a valid IP address."
        if not toport and not toaddr:
            return "Set a destination port, a destination address, or both."
        return self.validate_permanence()

    def result_value(self) -> tuple[str, str, str, str]:
        return (self.port_edit.text().strip(), self.proto_combo.currentText(),
                self.toport_edit.text().strip(), self.toaddr_edit.text().strip())


class SourceDialog(_BaseDialog, PermanenceMixin):
    def __init__(self, zone: str, parent: QWidget | None = None) -> None:
        super().__init__(f"Bind source — zone '{zone}'", parent)
        self.source_edit = QLineEdit()
        self.source_edit.setPlaceholderText("192.168.1.0/24, 2001:db8::/32, or ipset:name")
        self.form.addRow("Source", self.source_edit)
        hint = QLabel("Traffic from this source is handled by this zone regardless "
                      "of which interface it arrives on.")
        hint.setWordWrap(True)
        hint.setObjectName("Hint")
        self.form.addRow("", hint)
        self.add_permanence()

    def validate(self) -> str:
        value = self.source_edit.text().strip()
        if value.startswith("ipset:"):
            return "" if len(value) > 6 else "Name the ipset after 'ipset:'."
        if not is_valid_address(value):
            return "Enter an IP address, a CIDR network, or ipset:name."
        return self.validate_permanence()

    def result_value(self) -> str:
        return self.source_edit.text().strip()


class RichRuleDialog(_BaseDialog, PermanenceMixin):
    """Guided builder that emits (and validates) firewalld rich rule syntax."""

    ELEMENTS = ("(none)", "service", "port", "protocol", "icmp-block",
                "icmp-type", "masquerade", "forward-port", "source-port")
    ACTIONS = ("accept", "reject", "drop", "mark")

    def __init__(self, zone: str, services: list[str], parent: QWidget | None = None,
                 preset_source: str = "", preset_action: str = "drop",
                 existing: str = "") -> None:
        super().__init__(f"{'Edit' if existing else 'New'} rich rule — zone '{zone}'", parent)
        self.setMinimumWidth(620)
        self.services = services

        self.family_combo = QComboBox()
        self.family_combo.addItems(("auto", "ipv4", "ipv6"))
        self.form.addRow("Family", self.family_combo)

        self.priority_spin = QSpinBox()
        self.priority_spin.setRange(-32768, 32767)
        self.priority_spin.setValue(0)
        self.priority_spin.setToolTip("Lower runs earlier. 0 keeps firewalld's default order.")
        self.form.addRow("Priority", self.priority_spin)

        self.source_edit = QLineEdit(preset_source)
        self.source_edit.setPlaceholderText("192.168.1.5, 10.0.0.0/8, ipset:blocklist — empty = any")
        self.form.addRow("Source", self.source_edit)
        self.source_invert = QCheckBox("Invert (match everything except this source)")
        self.form.addRow("", self.source_invert)

        self.dest_edit = QLineEdit()
        self.dest_edit.setPlaceholderText("optional destination address or network")
        self.form.addRow("Destination", self.dest_edit)

        self.element_combo = QComboBox()
        self.element_combo.addItems(self.ELEMENTS)
        self.element_combo.currentTextChanged.connect(self._on_element_changed)
        self.form.addRow("Element", self.element_combo)

        self.element_value = QComboBox()
        self.element_value.setEditable(True)
        self.element_value.setInsertPolicy(QComboBox.NoInsert)
        self.element_value.hide()
        self.form.addRow("Value", self.element_value)
        self.element_row_label = self.form.labelForField(self.element_value)
        self.element_row_label.hide()

        self.element_proto = QComboBox()
        self.element_proto.addItems(PROTOCOLS)
        self.element_proto.hide()
        self.form.addRow("Value protocol", self.element_proto)
        self.element_proto_label = self.form.labelForField(self.element_proto)
        self.element_proto_label.hide()

        self.action_combo = QComboBox()
        self.action_combo.addItems(self.ACTIONS)
        self.action_combo.setCurrentText(preset_action)
        self.action_combo.currentTextChanged.connect(self._refresh_preview)
        self.form.addRow("Action", self.action_combo)

        self.action_extra = QLineEdit()
        self.action_extra.setPlaceholderText("reject type, or mark value like 0x51")
        self.form.addRow("Action detail", self.action_extra)

        self.limit_edit = QLineEdit()
        self.limit_edit.setPlaceholderText("e.g. 10/m — rate limit this rule")
        self.form.addRow("Limit", self.limit_edit)

        self.log_check = QCheckBox("Log matches")
        self.log_prefix = QLineEdit()
        self.log_prefix.setPlaceholderText("log prefix")
        self.log_level = QComboBox()
        self.log_level.addItems(("info", "notice", "warning", "err", "crit",
                                 "alert", "emerg", "debug"))
        self.log_level.setCurrentText("info")
        log_row = QHBoxLayout()
        log_row.addWidget(self.log_check)
        log_row.addWidget(self.log_prefix, 1)
        log_row.addWidget(self.log_level)
        log_holder = QWidget()
        log_holder.setLayout(log_row)
        self.form.addRow("Logging", log_holder)

        self.audit_check = QCheckBox("Send matches to the audit subsystem")
        self.form.addRow("", self.audit_check)

        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(False)
        self.preview.setFixedHeight(64)
        self.preview.setFont(theme.mono_font(9))
        self.preview.setToolTip("The generated rule. You can edit it directly; "
                                "it is validated before saving.")
        self.form.addRow("Rule", self.preview)

        for widget in (self.source_edit, self.dest_edit, self.action_extra,
                       self.limit_edit, self.log_prefix):
            widget.textChanged.connect(self._refresh_preview)
        for widget in (self.family_combo, self.log_level):
            widget.currentTextChanged.connect(self._refresh_preview)
        for widget in (self.source_invert, self.log_check, self.audit_check):
            widget.toggled.connect(self._refresh_preview)
        self.element_value.currentTextChanged.connect(self._refresh_preview)
        self.element_proto.currentTextChanged.connect(self._refresh_preview)
        self.priority_spin.valueChanged.connect(self._refresh_preview)
        self.preview.textChanged.connect(self._on_manual_edit)

        self.add_permanence()
        self._manual = False
        if existing:
            self.preview.setPlainText(existing)
            self._manual = True
        else:
            self._refresh_preview()

    # -- element plumbing ---------------------------------------------------
    def _on_element_changed(self, element: str) -> None:
        needs_value = element in ("service", "port", "protocol", "icmp-block",
                                  "icmp-type", "source-port", "forward-port")
        needs_proto = element in ("port", "source-port", "forward-port")
        self.element_value.setVisible(needs_value)
        self.element_row_label.setVisible(needs_value)
        self.element_proto.setVisible(needs_proto)
        self.element_proto_label.setVisible(needs_proto)

        self.element_value.clear()
        if element == "service":
            self.element_value.addItems(self.services)
        elif element in ("port", "source-port"):
            self.element_value.setEditText("")
        self._refresh_preview()

    def _on_manual_edit(self) -> None:
        if self.preview.hasFocus():
            self._manual = True

    # -- rule assembly ------------------------------------------------------
    def _build_rule(self) -> str:
        parts = ["rule"]
        family = self.family_combo.currentText()
        source = self.source_edit.text().strip()
        if family == "auto":
            reference = source or self.dest_edit.text().strip()
            family = address_family(reference) if reference and not reference.startswith("ipset:") else ""
        if family:
            parts.append(f'family="{family}"')
        if self.priority_spin.value():
            parts.append(f'priority="{self.priority_spin.value()}"')

        if source:
            inner = "source NOT" if self.source_invert.isChecked() else "source"
            if source.startswith("ipset:"):
                parts.append(f'{inner} ipset="{source[6:]}"')
            elif ":" in source and len(source.split(":")) == 6:
                parts.append(f'{inner} mac="{source}"')
            else:
                parts.append(f'{inner} address="{source}"')

        dest = self.dest_edit.text().strip()
        if dest:
            parts.append(f'destination address="{dest}"')

        element = self.element_combo.currentText()
        value = self.element_value.currentText().strip()
        proto = self.element_proto.currentText()
        if element == "service" and value:
            parts.append(f'service name="{value}"')
        elif element == "port" and value:
            parts.append(f'port port="{value}" protocol="{proto}"')
        elif element == "source-port" and value:
            parts.append(f'source-port port="{value}" protocol="{proto}"')
        elif element == "protocol" and value:
            parts.append(f'protocol value="{value}"')
        elif element == "icmp-block" and value:
            parts.append(f'icmp-block name="{value}"')
        elif element == "icmp-type" and value:
            parts.append(f'icmp-type name="{value}"')
        elif element == "masquerade":
            parts.append("masquerade")
        elif element == "forward-port" and value:
            parts.append(f'forward-port port="{value}" protocol="{proto}" to-port="{value}"')

        if self.log_check.isChecked():
            log = "log"
            prefix = self.log_prefix.text().strip()
            if prefix:
                log += f' prefix="{prefix}"'
            log += f' level="{self.log_level.currentText()}"'
            parts.append(log)
        if self.audit_check.isChecked():
            parts.append("audit")

        action = self.action_combo.currentText()
        extra = self.action_extra.text().strip()
        if action == "reject" and extra:
            parts.append(f'reject type="{extra}"')
        elif action == "mark":
            parts.append(f'mark set="{extra or "0x1"}"')
        else:
            parts.append(action)

        limit = self.limit_edit.text().strip()
        if limit:
            parts.append(f'limit value="{limit}"')
        return " ".join(parts)

    def _refresh_preview(self) -> None:
        if self._manual:
            return
        blocked = self.preview.blockSignals(True)
        self.preview.setPlainText(self._build_rule())
        self.preview.blockSignals(blocked)
        self.show_error("")

    def validate(self) -> str:
        problem = validate_rich_rule(self.rule_text())
        if problem:
            return f"firewalld rejects this rule: {problem}"
        return self.validate_permanence()

    def rule_text(self) -> str:
        return self.preview.toPlainText().strip()


class BlockAddressDialog(_BaseDialog, PermanenceMixin):
    """Fast path from the connections table to a drop rule."""

    def __init__(self, address: str, zones: list[str], default_zone: str,
                 parent: QWidget | None = None) -> None:
        super().__init__(f"Block {address}", parent)
        self.address = address

        info = QLabel(f"Add a rich rule that drops all traffic from "
                      f"<b>{address}</b>.")
        info.setWordWrap(True)
        self.form.addRow("", info)

        self.zone_combo = QComboBox()
        self.zone_combo.addItems(zones)
        if default_zone in zones:
            self.zone_combo.setCurrentText(default_zone)
        self.form.addRow("Zone", self.zone_combo)

        self.action_combo = QComboBox()
        self.action_combo.addItems(("drop", "reject"))
        self.form.addRow("Action", self.action_combo)

        self.log_check = QCheckBox("Log every blocked packet")
        self.form.addRow("", self.log_check)

        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(0, 86400)
        self.timeout_spin.setSuffix(" s")
        self.timeout_spin.setSpecialValueText("no expiry")
        self.timeout_spin.setToolTip("Runtime rules can expire automatically.")
        self.form.addRow("Expires after", self.timeout_spin)

        self.add_permanence()
        self.permanent_check.setChecked(False)

    def validate(self) -> str:
        if not is_valid_address(self.address):
            return f"'{self.address}' is not an address that can be blocked."
        if self.timeout_spin.value() and self.apply_permanent:
            return "A timeout only applies to runtime rules. Uncheck Permanent."
        return self.validate_permanence()

    def rule_text(self) -> str:
        family = address_family(self.address)
        parts = [f'rule family="{family}"', f'source address="{self.address}"']
        if self.log_check.isChecked():
            parts.append('log prefix="fwpanel-block" level="info" limit value="5/m"')
        parts.append(self.action_combo.currentText())
        return " ".join(parts)

    @property
    def zone(self) -> str:
        return self.zone_combo.currentText()

    @property
    def timeout(self) -> int:
        return self.timeout_spin.value()


class IPSetEntryDialog(_BaseDialog):
    def __init__(self, ipset: str, parent: QWidget | None = None) -> None:
        super().__init__(f"Add entries to '{ipset}'", parent)
        self.entries_edit = QPlainTextEdit()
        self.entries_edit.setPlaceholderText("One entry per line:\n192.0.2.10\n198.51.100.0/24")
        self.entries_edit.setFont(theme.mono_font(9))
        self.entries_edit.setMinimumHeight(140)
        self.form.addRow("Entries", self.entries_edit)

    def validate(self) -> str:
        if not self.entries():
            return "Enter at least one entry."
        return ""

    def entries(self) -> list[str]:
        return [line.strip() for line in self.entries_edit.toPlainText().splitlines()
                if line.strip()]


class DirectRuleDialog(_BaseDialog):
    """Raw iptables/nftables arguments injected ahead of firewalld's chains."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Add direct rule", parent)
        self.setMinimumWidth(560)

        warning = QLabel(
            "Direct rules bypass the zone model entirely and are not validated "
            "by firewalld. They run before every zone rule.")
        warning.setWordWrap(True)
        warning.setStyleSheet(f"color: {theme.WARN}; font-size: 9pt;")
        self.form.addRow("", warning)

        self.ipv_combo = QComboBox()
        self.ipv_combo.addItems(("ipv4", "ipv6", "eb"))
        self.form.addRow("Family", self.ipv_combo)

        self.table_edit = QLineEdit("filter")
        self.form.addRow("Table", self.table_edit)

        self.chain_edit = QLineEdit("INPUT")
        self.form.addRow("Chain", self.chain_edit)

        self.priority_spin = QSpinBox()
        self.priority_spin.setRange(0, 10000)
        self.form.addRow("Priority", self.priority_spin)

        self.args_edit = QLineEdit()
        self.args_edit.setPlaceholderText("-p tcp --dport 22 -s 10.0.0.0/8 -j ACCEPT")
        self.args_edit.setFont(theme.mono_font(9))
        self.form.addRow("Arguments", self.args_edit)

    def validate(self) -> str:
        if not self.table_edit.text().strip():
            return "Table is required."
        if not self.chain_edit.text().strip():
            return "Chain is required."
        if not self.args_edit.text().strip():
            return "Arguments are required."
        return ""

    def result_value(self) -> tuple[str, str, str, int, list[str]]:
        return (self.ipv_combo.currentText(), self.table_edit.text().strip(),
                self.chain_edit.text().strip(), self.priority_spin.value(),
                self.args_edit.text().split())
