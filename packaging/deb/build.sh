#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PACKAGE_NAME="multi-folder-dashboard"
VERSION="${VERSION:-0.1.0}"
REVISION="${REVISION:-1}"
ARCH="${ARCH:-all}"
DEB_VERSION="${VERSION}-${REVISION}"
OUTPUT_DIR="${ROOT_DIR}/packaging/deb/output"
STAGING_DIR="${ROOT_DIR}/packaging/deb/${PACKAGE_NAME}_${DEB_VERSION}_${ARCH}"
DEB_PATH="${OUTPUT_DIR}/${PACKAGE_NAME}_${DEB_VERSION}_${ARCH}.deb"
INSTALL_DIR="/usr/lib/${PACKAGE_NAME}"
ICON_NAME="io.github.rafael.MultiFolderDashboard"

rm -rf "${STAGING_DIR}" "${OUTPUT_DIR}"
mkdir -p \
  "${OUTPUT_DIR}" \
  "${STAGING_DIR}/DEBIAN" \
  "${STAGING_DIR}/usr/bin" \
  "${STAGING_DIR}${INSTALL_DIR}" \
  "${STAGING_DIR}/usr/share/applications" \
  "${STAGING_DIR}/usr/share/icons/hicolor/1024x1024/apps"

install -m 0644 "${ROOT_DIR}/multi_folder_dashboard.py" "${STAGING_DIR}${INSTALL_DIR}/"
install -m 0644 "${ROOT_DIR}/app_config.py" "${STAGING_DIR}${INSTALL_DIR}/"
install -m 0644 "${ROOT_DIR}/gtk_compat.py" "${STAGING_DIR}${INSTALL_DIR}/"
install -m 0644 "${ROOT_DIR}/system_utils.py" "${STAGING_DIR}${INSTALL_DIR}/"
install -m 0644 "${ROOT_DIR}/icon.png" "${STAGING_DIR}${INSTALL_DIR}/icon.png"
install -m 0644 "${ROOT_DIR}/io.github.rafael.MultiFolderDashboard.desktop" \
  "${STAGING_DIR}/usr/share/applications/${ICON_NAME}.desktop"
install -m 0644 "${ROOT_DIR}/icon.png" \
  "${STAGING_DIR}/usr/share/icons/hicolor/1024x1024/apps/${ICON_NAME}.png"

cat > "${STAGING_DIR}/usr/bin/${PACKAGE_NAME}" <<EOF
#!/bin/sh
exec /usr/bin/python3 ${INSTALL_DIR}/multi_folder_dashboard.py "\$@"
EOF
chmod 0755 "${STAGING_DIR}/usr/bin/${PACKAGE_NAME}"

cat > "${STAGING_DIR}/DEBIAN/control" <<EOF
Package: ${PACKAGE_NAME}
Version: ${DEB_VERSION}
Section: utils
Priority: optional
Architecture: ${ARCH}
Maintainer: Rafael <rafaelshelena@gmail.com>
Depends: python3, python3-gi, gir1.2-gtk-3.0, gir1.2-vte-2.91, python3-psutil
Description: Dashboard de terminais por pasta
 Aplicacao de desktop para abrir terminais organizados por pasta e monitorar portas.
EOF

cat > "${STAGING_DIR}/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e

if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -q -t /usr/share/icons/hicolor || true
fi

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database -q /usr/share/applications || true
fi

exit 0
EOF
chmod 0755 "${STAGING_DIR}/DEBIAN/postinst"

dpkg-deb --build "${STAGING_DIR}" "${DEB_PATH}"
printf 'Created %s\n' "${DEB_PATH}"
