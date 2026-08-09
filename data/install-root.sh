#!/usr/bin/env bash
#
# The privileged half of install.sh. Not meant to be run directly - install.sh
# invokes it once through sudo or pkexec so there is a single authentication
# rather than one per file.
#
# Argument 1: the absolute path of the fwpanel source tree.
#
set -euo pipefail

SOURCE_DIR="${1:?source directory required}"

PREFIX=/usr
LIB_DIR="$PREFIX/lib/fwpanel"

if [[ ! -f "$SOURCE_DIR/fwpanel/app.py" ]]; then
    echo "xx $SOURCE_DIR does not look like the fwpanel source tree" >&2
    exit 1
fi

echo "==> Installing the application to $LIB_DIR"
rm -rf "$LIB_DIR"
install -d -m 0755 "$LIB_DIR/fwpanel"
cp -r "$SOURCE_DIR/fwpanel/." "$LIB_DIR/fwpanel/"
find "$LIB_DIR" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
find "$LIB_DIR" -type d -exec chmod 0755 {} +
find "$LIB_DIR" -type f -exec chmod 0644 {} +

echo "==> Installing the launcher, helper and polkit policy"
install -D -m 0755 "$SOURCE_DIR/data/fwpanel-launcher"        "$PREFIX/bin/fwpanel"
install -D -m 0755 "$SOURCE_DIR/helper/fwpanel-helper"        "$PREFIX/libexec/fwpanel-helper"
install -D -m 0644 "$SOURCE_DIR/data/org.fwpanel.helper.policy" \
    "$PREFIX/share/polkit-1/actions/org.fwpanel.helper.policy"

echo "==> Installing the desktop entry, icon and systemd user unit"
install -D -m 0644 "$SOURCE_DIR/data/org.fwpanel.Panel.desktop" \
    "$PREFIX/share/applications/org.fwpanel.Panel.desktop"
install -D -m 0644 "$SOURCE_DIR/data/icons/fwpanel.svg" \
    "$PREFIX/share/icons/hicolor/scalable/apps/fwpanel.svg"
install -D -m 0644 "$SOURCE_DIR/data/fwpanel.service" \
    "$PREFIX/lib/systemd/user/fwpanel.service"

echo "==> Refreshing desktop caches"
update-desktop-database "$PREFIX/share/applications" 2>/dev/null || true
gtk-update-icon-cache -f -t "$PREFIX/share/icons/hicolor" 2>/dev/null || true

echo "==> System files installed"
