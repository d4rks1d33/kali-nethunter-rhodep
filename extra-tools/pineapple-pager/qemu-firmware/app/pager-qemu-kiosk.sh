#!/bin/sh
# =============================================================================
# pager-qemu-kiosk.sh — abre el VIEWER del WiFi Pineapple Pager en modo
# "FIRMWARE REAL (qemu)" a pantalla completa (kiosk, landscape), de modo que el
# Motorola G82 (Phosh / Wayland) muestre el display del firmware real navegable.
#
# A diferencia de pager-kiosk.sh (modo NATIVO, UI propia en :1471), este:
#   1. Arranca pager-qemu.target  (firmware bajo qemu + bridge + viewer).
#      Al hacerlo, el Conflicts= PARA automaticamente el modo nativo (pager.target).
#   2. Espera a que el VIEWER (proxy same-origin) responda en :8471.
#   3. Lanza el navegador kiosk apuntando a http://127.0.0.1:8471/.
#
# Comparte la deteccion de navegador (cog/epiphany/chromium/firefox) y la
# rotacion a landscape del pager-kiosk.sh del modo nativo.
#
# Uso:  pager-qemu-kiosk.sh [URL]
#       URL por defecto: http://127.0.0.1:8471
# =============================================================================
set -u

URL="${1:-http://127.0.0.1:8471}"
HEALTH_URL="${PAGER_HEALTH_URL:-http://127.0.0.1:8471/}"
HEALTH_TIMEOUT="${PAGER_HEALTH_TIMEOUT:-45}"   # qemu tarda mas en levantar
WMCLASS="wifi-pineapple-pager-qemu"
TARGET="pager-qemu.target"

log()  { echo "[pager-qemu-kiosk] $*" >&2; }
have() { command -v "$1" >/dev/null 2>&1; }

# ---------------------------------------------------------------------------
# 1. Asegurar el stack del firmware-qemu (arranca el target; para el nativo)
# ---------------------------------------------------------------------------
ensure_stack() {
    if ! have systemctl; then
        log "systemctl no disponible; asumo que el stack ya corre."
        return 0
    fi
    if systemctl is-active --quiet "$TARGET" 2>/dev/null; then
        log "$TARGET ya activo."
        return 0
    fi
    log "$TARGET no esta activo; intento arrancarlo (para el modo nativo si estaba)..."
    if systemctl start "$TARGET" 2>/dev/null; then
        log "$TARGET arrancado."
    elif have pkexec && pkexec systemctl start "$TARGET" 2>/dev/null; then
        log "$TARGET arrancado (pkexec)."
    elif have sudo && sudo -n systemctl start "$TARGET" 2>/dev/null; then
        log "$TARGET arrancado (sudo)."
    else
        log "AVISO: no pude arrancar $TARGET automaticamente."
        log "       Arrancalo a mano:  systemctl start $TARGET"
    fi
}

# ---------------------------------------------------------------------------
# 2. Healthcheck — esperar a que el VIEWER (:8471) responda
# ---------------------------------------------------------------------------
wait_health() {
    log "esperando a $HEALTH_URL (timeout ${HEALTH_TIMEOUT}s)..."
    i=0
    while [ "$i" -lt "$HEALTH_TIMEOUT" ]; do
        if have curl; then
            curl -fsS --max-time 2 -o /dev/null "$HEALTH_URL" 2>/dev/null && { log "viewer responde. OK."; return 0; }
        elif have wget; then
            wget -q -T 2 -O /dev/null "$HEALTH_URL" 2>/dev/null && { log "viewer responde. OK."; return 0; }
        else
            log "ni curl ni wget; omito healthcheck."; return 0
        fi
        i=$((i + 1)); sleep 1
    done
    log "AVISO: el viewer no respondio en ${HEALTH_TIMEOUT}s; lanzo el navegador igual."
    return 1
}

# ---------------------------------------------------------------------------
# 2b. Forzar orientacion landscape (misma logica que el modo nativo)
# ---------------------------------------------------------------------------
ROTATED=""
restore_orientation() {
    [ -z "$ROTATED" ] && return 0
    case "$ROTATED" in
        wlr-randr) wlr-randr --output "$WLR_OUT" --transform normal 2>/dev/null || true ;;
        xrandr)    xrandr -o normal 2>/dev/null || true ;;
    esac
}
trap restore_orientation EXIT INT TERM

force_landscape() {
    if have wlr-randr; then
        WLR_OUT="$(wlr-randr 2>/dev/null | awk 'NR==1{print $1}')"
        if [ -n "$WLR_OUT" ] && wlr-randr --output "$WLR_OUT" --transform 90 2>/dev/null; then
            ROTATED="wlr-randr"; log "orientacion -> landscape (wlr-randr, $WLR_OUT)"; return 0
        fi
    fi
    if have xrandr && [ -n "${DISPLAY:-}" ] && xrandr -o right 2>/dev/null; then
        ROTATED="xrandr"; log "orientacion -> landscape (xrandr)"; return 0
    fi
    log "no pude rotar el compositor; el frontend forzara landscape por CSS."
    return 1
}

# ---------------------------------------------------------------------------
# 3. Lanzar el navegador kiosk (primero que exista)
# ---------------------------------------------------------------------------
launch_browser() {
    if have cog; then
        log "lanzando con cog (WebKit kiosk)."
        exec cog --platform=wl -F "$URL"
    fi
    for wpe in wpe wpewebkit wpe-webkit cog-wpe; do
        have "$wpe" && { log "lanzando con $wpe."; exec "$wpe" "$URL"; }
    done
    if have epiphany; then
        log "lanzando con epiphany --application-mode."
        exec epiphany --application-mode "$URL"
    fi
    if have flatpak && flatpak info org.gnome.Epiphany >/dev/null 2>&1; then
        log "lanzando con flatpak org.gnome.Epiphany."
        exec flatpak run org.gnome.Epiphany --application-mode "$URL"
    fi
    for c in chromium chromium-browser chrome google-chrome google-chrome-stable; do
        if have "$c"; then
            log "lanzando con $c (--app --kiosk)."
            exec "$c" --app="$URL" --kiosk --start-fullscreen --window-position=0,0 \
                --no-first-run --disable-infobars --overscroll-history-navigation=0 \
                --class="$WMCLASS" \
                --user-data-dir="${XDG_CACHE_HOME:-$HOME/.cache}/pager-qemu-kiosk"
        fi
    done
    if have firefox; then
        log "lanzando con firefox --kiosk."
        exec firefox --kiosk "$URL"
    fi
    log "ERROR: no encontre ningun navegador soportado (cog/epiphany/chromium/firefox)."
    exit 1
}

main() {
    ensure_stack
    wait_health
    force_landscape
    launch_browser
}
main "$@"
