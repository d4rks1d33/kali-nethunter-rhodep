#!/bin/bash
# =============================================================================
# install-qemu.sh — instala el modo "FIRMWARE REAL (qemu)" del WiFi Pineapple
# Pager en el dispositivo (Motorola G82 / Kali NetHunter aarch64).
#
# Qué hace:
#   1. Reubica el stack a /opt/pineapple-pager/qemu/ (prolijo, junto al nativo):
#        bin/qemu-mipsel, rootfs/, scripts/, viewer/, app/.
#   2. Instala los units systemd (NO los habilita al boot):
#        pager-qemu-firmware.service, pager-qemu-viewer.service, pager-qemu.target
#      + reapunta pineap-bridge.service a /opt
#      + drop-ins Conflicts= bidireccionales (nativo <-> qemu, ambos usan :1471).
#   3. Instala el .desktop + icono en /usr/share/applications (modo Firmware).
#   4. daemon-reload.
#
#   --uninstall revierte 2/3/4 (y, con --purge, borra /opt/pineapple-pager/qemu).
#
# Fuentes (overridable por env):
#   SRC_DIR         este qemu-firmware/ (default: dir del script)
#   QEMU_BIN_SRC    binario qemu-mipsel     (default /home/kali/pager-qemu/qemu-8.2.2/build/qemu-mipsel)
#   ROOTFS_SRC      rootfs del firmware     (default /home/kali/pager-qemu/rootfs)
# Destino:
#   DEST=/opt/pineapple-pager/qemu
# =============================================================================
set -u

SELF="$(cd "$(dirname "$0")" && pwd)"
SRC_DIR="${SRC_DIR:-$SELF}"
QEMU_BIN_SRC="${QEMU_BIN_SRC:-/home/kali/pager-qemu/qemu-8.2.2/build/qemu-mipsel}"
ROOTFS_SRC="${ROOTFS_SRC:-/home/kali/pager-qemu/rootfs}"

DEST="/opt/pineapple-pager/qemu"
UNIT_DIR="/etc/systemd/system"
APP_DEST="/usr/share/applications"
ICON_DEST="/usr/share/icons/hicolor/scalable/apps"

log()  { echo "[install-qemu] $*"; }
die()  { echo "[install-qemu] ERROR: $*" >&2; exit 1; }
need_root() { [ "$(id -u)" -eq 0 ] || die "corré como root (sudo)."; }

# ---------------------------------------------------------------------------
do_install() {
    need_root
    log "destino: $DEST"

    # --- 1. layout /opt -----------------------------------------------------
    mkdir -p "$DEST/bin" "$DEST/rootfs" "$DEST/scripts" "$DEST/viewer" "$DEST/app"

    [ -x "$QEMU_BIN_SRC" ] || die "no encuentro el binario qemu en $QEMU_BIN_SRC"
    log "copiando qemu-mipsel ($(du -h "$QEMU_BIN_SRC" | cut -f1))..."
    install -m 0755 "$QEMU_BIN_SRC" "$DEST/bin/qemu-mipsel"

    [ -d "$ROOTFS_SRC" ] || die "no encuentro el rootfs en $ROOTFS_SRC"
    log "copiando rootfs ($(du -sh "$ROOTFS_SRC" 2>/dev/null | cut -f1))... (puede tardar)"
    # -a preserva perms/symlinks; --delete para quedar idéntico al origen
    if command -v rsync >/dev/null 2>&1; then
        rsync -a --delete "$ROOTFS_SRC/" "$DEST/rootfs/"
    else
        rm -rf "$DEST/rootfs"; cp -a "$ROOTFS_SRC" "$DEST/rootfs"
    fi

    log "copiando scripts/ viewer/ app/ ..."
    cp -a "$SRC_DIR/scripts/." "$DEST/scripts/"
    cp -a "$SRC_DIR/viewer/."  "$DEST/viewer/"
    cp -a "$SRC_DIR/app/."     "$DEST/app/"
    chmod +x "$DEST/scripts/"*.sh 2>/dev/null || true
    chmod +x "$DEST/app/"*.sh 2>/dev/null || true

    # --- 2. systemd ---------------------------------------------------------
    log "instalando units systemd (no enabled)..."
    install -m 0644 "$SRC_DIR/systemd/pager-qemu-firmware.service" "$UNIT_DIR/"
    install -m 0644 "$SRC_DIR/systemd/pager-qemu-viewer.service"   "$UNIT_DIR/"
    install -m 0644 "$SRC_DIR/systemd/pager-qemu-vibrator.service" "$UNIT_DIR/"
    install -m 0644 "$SRC_DIR/systemd/pager-qemu-bt.service"       "$UNIT_DIR/"
    install -m 0644 "$SRC_DIR/systemd/pager-qemu.target"           "$UNIT_DIR/"

    # Reapuntar pineap-bridge.service a /opt (si existe). Escribimos la unit
    # completa para que el bridge corra desde el árbol reubicado.
    if [ -f "$UNIT_DIR/pineap-bridge.service" ]; then
        log "reapuntando pineap-bridge.service -> $DEST/scripts/pineap_bridge.py"
        cat > "$UNIT_DIR/pineap-bridge.service" <<EOF
[Unit]
Description=PineAP socket bridge (qemu firmware <-> native pineapd-pager, header endianness translation)
After=pineapd-pager.service
Wants=pineapd-pager.service

[Service]
Type=simple
ExecStartPre=-/bin/rm -f /tmp/pineap_sock
ExecStart=/usr/bin/python3 $DEST/scripts/pineap_bridge.py /tmp/pineap_sock /opt/pineapple-pager/run/pineap_sock /tmp/pineap_bridge.log
Restart=on-failure
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF
    fi

    # Drop-in Conflicts= inverso: cuando el usuario arranca el modo NATIVO
    # (pager-server, :1471), que se pare el firmware qemu, y así no chocan en
    # :1471. (El lado qemu->nativo ya está en los units nuevos: firmware y target
    # conflictan con pager-server.service.)
    #
    # IMPORTANTE: NO ponemos Conflicts en pager.target porque pineapd-pager y
    # pineap-bridge son PartOf=pager.target y los COMPARTEN ambos modos; tirar el
    # target abajo mataria el back-end WiFi nativo que el modo qemu necesita.
    # Conflictar solo con pager-server.service alcanza para liberar :1471.
    mkdir -p "$UNIT_DIR/pager-server.service.d"
    cat > "$UNIT_DIR/pager-server.service.d/10-conflict-qemu.conf" <<EOF
# Instalado por install-qemu.sh: pager-server (:1471) vs firmware qemu (:1471).
[Unit]
Conflicts=pager-qemu.target pager-qemu-firmware.service
EOF

    # --- 3. .desktop + icono -----------------------------------------------
    log "instalando .desktop + icono..."
    mkdir -p "$ICON_DEST"
    install -m 0644 "$SRC_DIR/app/pager-qemu-icon.svg" "$ICON_DEST/wifi-pineapple-pager-qemu.svg"
    install -m 0644 "$SRC_DIR/app/pager-qemu.desktop"  "$APP_DEST/wifi-pineapple-pager-qemu.desktop"
    command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$APP_DEST" 2>/dev/null || true
    command -v gtk-update-icon-cache  >/dev/null 2>&1 && gtk-update-icon-cache -f /usr/share/icons/hicolor 2>/dev/null || true

    # --- 4. daemon-reload ---------------------------------------------------
    systemctl daemon-reload
    log "listo. NO se habilitó nada al boot."
    echo
    log "uso:"
    log "   systemctl start pager-qemu.target     # arranca firmware+bridge+viewer (:1471 + :8471)"
    log "   o abrí la app 'WiFi Pineapple Pager (Firmware)'"
    log "   systemctl stop  pager-qemu.target     # para todo"
}

# ---------------------------------------------------------------------------
do_uninstall() {
    need_root
    PURGE=0
    [ "${1:-}" = "--purge" ] && PURGE=1

    log "deteniendo pager-qemu.target (best-effort)..."
    timeout 20 systemctl stop pager-qemu.target 2>/dev/null || true
    timeout 15 systemctl stop pager-qemu-viewer.service pager-qemu-firmware.service 2>/dev/null || true

    log "quitando units + drop-ins..."
    rm -f "$UNIT_DIR/pager-qemu-firmware.service" \
          "$UNIT_DIR/pager-qemu-viewer.service" \
          "$UNIT_DIR/pager-qemu-vibrator.service" \
          "$UNIT_DIR/pager-qemu-bt.service" \
          "$UNIT_DIR/pager-qemu.target"
    rm -f "$UNIT_DIR/pager.target.d/10-conflict-qemu.conf" \
          "$UNIT_DIR/pager-server.service.d/10-conflict-qemu.conf"
    rmdir "$UNIT_DIR/pager.target.d" 2>/dev/null || true
    rmdir "$UNIT_DIR/pager-server.service.d" 2>/dev/null || true

    log "quitando .desktop + icono..."
    rm -f "$APP_DEST/wifi-pineapple-pager-qemu.desktop" \
          "$ICON_DEST/wifi-pineapple-pager-qemu.svg"
    command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$APP_DEST" 2>/dev/null || true

    systemctl daemon-reload

    log "NOTA: pineap-bridge.service quedó reapuntado a /opt (sigue sirviendo al"
    log "      modo nativo igual). No lo revierto para no romperlo."

    if [ "$PURGE" -eq 1 ]; then
        log "--purge: borrando $DEST ..."
        rm -rf "$DEST"
    else
        log "dejé $DEST en disco (usá --purge para borrarlo)."
    fi
    log "desinstalado."
}

# ---------------------------------------------------------------------------
case "${1:-}" in
    --uninstall) shift; do_uninstall "$@" ;;
    -h|--help)
        echo "uso: install-qemu.sh                 # instala a /opt (no enabled)"
        echo "     install-qemu.sh --uninstall      # quita units/.desktop (deja /opt)"
        echo "     install-qemu.sh --uninstall --purge  # + borra /opt/pineapple-pager/qemu"
        ;;
    *) do_install ;;
esac
