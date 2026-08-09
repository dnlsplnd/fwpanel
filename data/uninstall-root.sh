#!/usr/bin/env bash
#
# The privileged half of uninstall.sh.
#
set -euo pipefail

echo "==> Removing installed files"
rm -rf /usr/lib/fwpanel
rm -f /usr/bin/fwpanel \
      /usr/libexec/fwpanel-helper \
      /usr/share/polkit-1/actions/org.fwpanel.helper.policy \
      /usr/share/applications/org.fwpanel.Panel.desktop \
      /usr/share/icons/hicolor/scalable/apps/fwpanel.svg \
      /usr/lib/systemd/user/fwpanel.service

echo "==> Refreshing desktop caches"
update-desktop-database /usr/share/applications 2>/dev/null || true
gtk-update-icon-cache -f -t /usr/share/icons/hicolor 2>/dev/null || true

echo "==> System files removed"
