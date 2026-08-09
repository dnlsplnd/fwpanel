#!/usr/bin/env bash
#
# Install fwpanel system-wide and enable it as a per-user systemd service.
#
# Run as your normal user. It elevates exactly once, for the file installation;
# enabling the service afterwards is deliberately unprivileged, because it is a
# user service.
#
set -euo pipefail

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

say()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31mxx\033[0m %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------- checks ----
if [[ $EUID -eq 0 ]]; then
    die "Run this as your normal user, not root. It elevates on its own."
fi

# Dependencies are probed by importing them, not by asking the package
# manager: the import is what actually has to work, and package names differ
# between distributions (Debian even splits PySide6 per Qt module).
install_hint() {
    local id="" like=""
    if [[ -r /etc/os-release ]]; then
        id=$(. /etc/os-release && printf '%s' "${ID:-}")
        like=$(. /etc/os-release && printf '%s' "${ID_LIKE:-}")
    fi
    case " ${id} ${like} " in
        *fedora*|*rhel*|*centos*)
            echo "sudo dnf install python3-pyside6 python3-psutil python3-firewall \\"
            echo "                 python3-dbus python3-gobject-base polkit firewalld" ;;
        *debian*|*ubuntu*)
            echo "sudo apt install python3-pyside6.qtcore python3-pyside6.qtgui \\"
            echo "                 python3-pyside6.qtwidgets python3-pyside6.qtnetwork \\"
            echo "                 python3-psutil python3-firewall python3-dbus \\"
            echo "                 python3-gi pkexec firewalld" ;;
        *arch*)
            echo "sudo pacman -S pyside6 python-psutil python-firewall python-dbus \\"
            echo "               python-gobject polkit firewalld" ;;
        *suse*)
            echo "sudo zypper install python3-pyside6 python3-psutil python3-firewall \\"
            echo "                    python3-dbus-python python3-gobject polkit firewalld" ;;
        *)
            echo "Install: PySide6, psutil, python-firewall, dbus-python, PyGObject," \
                 "polkit and firewalld from your distribution's repositories." ;;
    esac
}

say "Checking dependencies"
missing=()
python3 -c 'import PySide6.QtWidgets' 2>/dev/null || missing+=("PySide6 (Qt 6 bindings)")
python3 -c 'import psutil'            2>/dev/null || missing+=("psutil")
python3 -c 'import firewall.client'   2>/dev/null || missing+=("python firewall bindings")
python3 -c 'import dbus'              2>/dev/null || missing+=("dbus-python")
python3 -c 'import gi'                2>/dev/null || missing+=("PyGObject")
command -v pkexec >/dev/null                      || missing+=("polkit (pkexec)")

if (( ${#missing[@]} )); then
    warn "Missing: ${missing[*]}"
    echo "    On this system, install them with:"
    install_hint | sed 's/^/        /'
    die "Cannot continue until those are installed."
fi

systemctl is-active --quiet firewalld \
    || warn "firewalld is not running. Start it with: sudo systemctl enable --now firewalld"

# --------------------------------------------------------------- elevate ----
# sudo when it can actually prompt (or is already cached); otherwise fall back
# to pkexec, which asks through the desktop's polkit agent. That keeps this
# script usable both from a terminal and from a launcher with no TTY.
if command -v sudo >/dev/null && { sudo -n true 2>/dev/null || [[ -t 0 ]]; }; then
    ELEVATE=(sudo)
elif command -v pkexec >/dev/null; then
    ELEVATE=(pkexec)
    say "No terminal for a sudo prompt - asking through polkit instead"
else
    die "Neither sudo nor pkexec is usable; cannot install."
fi

say "Installing system files (one authentication)"
"${ELEVATE[@]}" /usr/bin/env bash "$SOURCE_DIR/data/install-root.sh" "$SOURCE_DIR"

# --------------------------------------------------------------- service ----
say "Enabling the user service"
systemctl --user daemon-reload
systemctl --user enable --now fwpanel.service

if systemctl --user is-active --quiet fwpanel.service; then
    say "fwpanel is running. Look for the shield icon in your system tray."
else
    warn "The service did not stay active. Inspect it with:"
    echo "        systemctl --user status fwpanel.service"
    echo "        journalctl --user -u fwpanel.service -n 50"
fi

cat <<'EOF'

Installed.

  Launch by hand      fwpanel
  Service control     systemctl --user {status,restart,stop} fwpanel.service
  Service logs        journalctl --user -u fwpanel.service -f
  Remove              ./uninstall.sh

Reading the firewall needs no authentication. Changing it prompts through
polkit, exactly as firewall-config does. The optional "Elevate" button starts
a read-only helper for conntrack, the raw nftables ruleset, and socket
ownership across all users - one prompt per session.
EOF
