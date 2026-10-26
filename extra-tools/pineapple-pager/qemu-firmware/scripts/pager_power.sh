#!/bin/bash
# pager_power.sh -- enciende/apaga el "Pager" (modo firmware-qemu) SIN matar el
# viewer, para que el boton de power de la app pueda prenderlo de nuevo.
#
# El boton de power fisico del Pager real apaga el dispositivo. Aca replicamos
# esa idea: apagar libera CPU, RAM y -- via el teardown de openap en
# pineapd-pager -- las RADIOS (hostapd/dnsmasq mueren, wlan vuelve a managed),
# asi el hardware queda libre para otros usos. Encender vuelve a levantar todo.
#
# IMPORTANTE: NO tocamos pager-qemu-viewer.service: es el que sirve esta UI y
# debe seguir vivo para poder re-encender. Tampoco tocamos red/wlan a mano
# (airmon/ip link de las interfaces de SSH); el unico efecto sobre radios es el
# teardown limpio que hace pineapd-pager al recibir SIGTERM (openap_shutdown).
#
# Uso:  pager_power.sh {on|off|status}
# Pensado para correr via sudo desde el proxy del viewer (sudoers acotado).
set -u

# Servicios que consumen recursos / ocupan hardware (TODO menos el viewer).
UNITS=(
    pager-qemu-firmware.service
    pineap-bridge.service
    pineapd-pager.service
    pager-qemu-battery.service
    pager-qemu-vibrator.service
    pager-reload-supervisor.service
)

SYSTEMCTL="systemctl"

power_on() {
    # Orden: back-end WiFi primero, luego bridge, luego firmware, extras.
    $SYSTEMCTL reset-failed "${UNITS[@]}" 2>/dev/null || true
    $SYSTEMCTL start pineapd-pager.service 2>/dev/null
    $SYSTEMCTL start pineap-bridge.service 2>/dev/null
    $SYSTEMCTL start pager-qemu-battery.service 2>/dev/null
    $SYSTEMCTL start pager-qemu-firmware.service 2>/dev/null
    $SYSTEMCTL start pager-qemu-vibrator.service 2>/dev/null
    $SYSTEMCTL start pager-reload-supervisor.service 2>/dev/null
    echo "on"
}

power_off() {
    # Parar firmware primero (deja de pedir cosas), luego el resto. pineapd-pager
    # al frenar hace openap_shutdown -> mata hostapd/dnsmasq y libera la radio.
    $SYSTEMCTL stop pager-reload-supervisor.service 2>/dev/null
    $SYSTEMCTL stop pager-qemu-vibrator.service 2>/dev/null
    $SYSTEMCTL stop pager-qemu-firmware.service 2>/dev/null
    $SYSTEMCTL stop pineap-bridge.service 2>/dev/null
    $SYSTEMCTL stop pager-qemu-battery.service 2>/dev/null
    $SYSTEMCTL stop pineapd-pager.service 2>/dev/null
    echo "off"
}

power_status() {
    # "on" si el firmware esta activo; si no, "off".
    if $SYSTEMCTL is-active --quiet pager-qemu-firmware.service; then
        echo "on"
    else
        echo "off"
    fi
}

case "${1:-status}" in
    on)     power_on ;;
    off)    power_off ;;
    status) power_status ;;
    *) echo "uso: $0 {on|off|status}" >&2; exit 2 ;;
esac
