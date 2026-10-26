#!/bin/sh
# =============================================================================
# pager-kiosk.sh — abre el frontend del WiFi Pineapple Pager en MODO KIOSK
# (pantalla completa, landscape, sin barra de navegador), de modo que el
# telefono (Motorola G82 / Phosh / Wayland) "parezca" el Pager fisico.
#
# Pasos:
#   1. Asegura que pager.target este corriendo (lo arranca si hace falta).
#   2. Healthcheck: espera a que http://127.0.0.1:1471 responda.
#   3. Lanza el mejor navegador kiosk disponible apuntando a la UI.
#
# Orden de preferencia de navegador (de mas liviano/kiosk-nativo a mas pesado):
#   1) cog            — shell WebKit minimalista para kiosks (ideal en movil/Wayland)
#   2) wpe / wpewebkit-* — frontend WPE WebKit (muy liviano, embebido)
#   3) epiphany / GNOME Web (org.gnome.Epiphany) — --application-mode
#   4) chromium / chromium-browser — --app=URL --kiosk --start-fullscreen
#   5) firefox        — --kiosk (fallback)
#
# Uso:  pager-kiosk.sh [URL]
#       URL por defecto: http://127.0.0.1:1471
# =============================================================================
set -u

URL="${1:-http://127.0.0.1:1471}"
HEALTH_URL="${PAGER_HEALTH_URL:-http://127.0.0.1:1471/}"
HEALTH_TIMEOUT="${PAGER_HEALTH_TIMEOUT:-30}"   # segundos de espera maxima
WMCLASS="wifi-pineapple-pager"

log()  { echo "[pager-kiosk] $*" >&2; }
have() { command -v "$1" >/dev/null 2>&1; }

# ---------------------------------------------------------------------------
# 1. Asegurar que el stack del Pager este corriendo
# ---------------------------------------------------------------------------
ensure_stack() {
    if ! have systemctl; then
        log "systemctl no disponible; asumo que el stack ya corre."
        return 0
    fi
    if systemctl is-active --quiet pager.target 2>/dev/null; then
        log "pager.target ya activo."
        return 0
    fi
    log "pager.target no esta activo; intento arrancarlo..."
    if systemctl start pager.target 2>/dev/null; then
        log "pager.target arrancado."
    elif have pkexec && pkexec systemctl start pager.target 2>/dev/null; then
        log "pager.target arrancado (pkexec)."
    elif have sudo && sudo -n systemctl start pager.target 2>/dev/null; then
        log "pager.target arrancado (sudo)."
    else
        log "AVISO: no pude arrancar pager.target automaticamente."
        log "       Arrancalo a mano:  systemctl start pager.target"
        # seguimos igual: el backend podria estar corriendo por otra via.
    fi
}

# ---------------------------------------------------------------------------
# 2. Healthcheck — esperar a que la UI responda
# ---------------------------------------------------------------------------
wait_health() {
    log "esperando a $HEALTH_URL (timeout ${HEALTH_TIMEOUT}s)..."
    i=0
    while [ "$i" -lt "$HEALTH_TIMEOUT" ]; do
        if have curl; then
            if curl -fsS --max-time 2 -o /dev/null "$HEALTH_URL" 2>/dev/null; then
                log "UI responde. OK."
                return 0
            fi
        elif have wget; then
            if wget -q -T 2 -O /dev/null "$HEALTH_URL" 2>/dev/null; then
                log "UI responde. OK."
                return 0
            fi
        else
            log "ni curl ni wget; omito healthcheck y lanzo igual."
            return 0
        fi
        i=$((i + 1))
        sleep 1
    done
    log "AVISO: la UI no respondio en ${HEALTH_TIMEOUT}s; lanzo el navegador igual."
    return 1
}

# ---------------------------------------------------------------------------
# 2b. Forzar orientacion HORIZONTAL (landscape) para que el Pager aparezca
#     apaisado sin que el usuario tenga que girar la pantalla.
#     El G82 arranca en portrait; rotamos el output del compositor a landscape.
#     Mejor-esfuerzo: probamos las herramientas de rotacion disponibles. Si
#     ninguna existe, el frontend igual fuerza el layout landscape por CSS.
#     Se guarda la orientacion previa para restaurarla al cerrar (trap).
# ---------------------------------------------------------------------------
ROTATED=""
restore_orientation() {
    [ -z "$ROTATED" ] && return 0
    case "$ROTATED" in
        wlr-randr) wlr-randr --output "$WLR_OUT" --transform normal 2>/dev/null || true ;;
        monitor)  gdbus call --session --dest org.gnome.Mutter.DisplayConfig \
                    --object-path /org/gnome/Mutter/DisplayConfig 2>/dev/null >/dev/null || true ;;
        xrandr)   xrandr -o normal 2>/dev/null || true ;;
    esac
}
trap restore_orientation EXIT INT TERM

force_landscape() {
    # Wayland (Phosh/wlroots): wlr-randr
    if have wlr-randr; then
        WLR_OUT="$(wlr-randr 2>/dev/null | awk 'NR==1{print $1}')"
        if [ -n "$WLR_OUT" ]; then
            # elegir landscape: transform 90 (apaisado). Si ya es landscape, no molesta.
            if wlr-randr --output "$WLR_OUT" --transform 90 2>/dev/null; then
                ROTATED="wlr-randr"; log "orientacion -> landscape (wlr-randr, $WLR_OUT)"; return 0
            fi
        fi
    fi
    # X11 fallback
    if have xrandr && [ -n "${DISPLAY:-}" ]; then
        if xrandr -o right 2>/dev/null; then
            ROTATED="xrandr"; log "orientacion -> landscape (xrandr)"; return 0
        fi
    fi
    log "no pude rotar el compositor; el frontend forzara landscape por CSS."
    return 1
}

# ---------------------------------------------------------------------------
# 3. Lanzar el navegador kiosk (primero que exista en el orden de preferencia)
# ---------------------------------------------------------------------------
launch_browser() {
    # cog: shell WebKit para kiosk (lo mejor en Wayland/movil)
    if have cog; then
        log "lanzando con cog (WebKit kiosk)."
        exec cog --platform=wl -F "$URL"
    fi

    # WPE WebKit launchers comunes
    for wpe in wpe wpewebkit wpe-webkit cog-wpe; do
        if have "$wpe"; then
            log "lanzando con $wpe (WPE WebKit)."
            exec "$wpe" "$URL"
        fi
    done

    # GNOME Web / Epiphany en modo aplicacion
    if have epiphany; then
        log "lanzando con epiphany --application-mode."
        exec epiphany --application-mode "$URL"
    fi
    if have org.gnome.Epiphany; then
        log "lanzando con org.gnome.Epiphany --application-mode."
        exec org.gnome.Epiphany --application-mode "$URL"
    fi
    if have flatpak && flatpak info org.gnome.Epiphany >/dev/null 2>&1; then
        log "lanzando con flatpak org.gnome.Epiphany."
        exec flatpak run org.gnome.Epiphany --application-mode "$URL"
    fi

    # Chromium / Chrome en modo app + kiosk
    for c in chromium chromium-browser chrome google-chrome google-chrome-stable; do
        if have "$c"; then
            log "lanzando con $c (--app --kiosk)."
            exec "$c" \
                --app="$URL" \
                --kiosk \
                --start-fullscreen \
                --window-position=0,0 \
                --no-first-run \
                --disable-infobars \
                --overscroll-history-navigation=0 \
                --class="$WMCLASS" \
                --user-data-dir="${XDG_CACHE_HOME:-$HOME/.cache}/pager-kiosk"
        fi
    done

    # Firefox kiosk (ultimo recurso)
    if have firefox; then
        log "lanzando con firefox --kiosk."
        exec firefox --kiosk "$URL"
    fi

    log "ERROR: no encontre ningun navegador soportado."
    log "       Instala uno de: cog, wpewebkit, epiphany (GNOME Web), chromium o firefox."
    exit 1
}

# ---------------------------------------------------------------------------
main() {
    ensure_stack
    wait_health
    force_landscape
    launch_browser
}
main "$@"
