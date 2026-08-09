#!/usr/bin/env bash
#
# Remove everything install.sh put on the system.
#
set -euo pipefail

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }

if [[ $EUID -eq 0 ]]; then
    echo "Run this as your normal user, not root." >&2
    exit 1
fi

say "Stopping the user service"
systemctl --user disable --now fwpanel.service 2>/dev/null || true

if command -v sudo >/dev/null && { sudo -n true 2>/dev/null || [[ -t 0 ]]; }; then
    ELEVATE=(sudo)
elif command -v pkexec >/dev/null; then
    ELEVATE=(pkexec)
else
    echo "Neither sudo nor pkexec is usable; cannot uninstall." >&2
    exit 1
fi

say "Removing system files (one authentication)"
"${ELEVATE[@]}" /usr/bin/env bash "$SOURCE_DIR/data/uninstall-root.sh"

systemctl --user daemon-reload

say "Removed. Your firewalld configuration was not touched."
echo "    Window geometry is remembered in ~/.config/fwpanel/ - delete it if you want a clean slate."
