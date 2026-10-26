#!/bin/bash
# prepare.sh - populate the rootfs with fake sysfs/dev/json nodes the
# WiFi Pineapple Pager firmware reads at boot. Idempotent. Run with sudo.
set -e
R="${ROOTFS:-/home/kali/pager-qemu/rootfs}"

echo "[prepare] rootfs=$R"

# ---- framebuffer: a plain 426240-byte file is enough (firmware mmaps it) ----
# 480*222*4 = 426240 (firmware only writes the first 480*222*2 = 213120 as RGB565)
if [ ! -f "$R/dev/fb0" ] || [ "$(stat -c%s "$R/dev/fb0" 2>/dev/null)" != "426240" ]; then
    dd if=/dev/zero of="$R/dev/fb0" bs=426240 count=1 2>/dev/null
fi

# ---- input FIFO: the patched qemu reads struct input_event records from
# this host FIFO and feeds them to the firmware's evdev reader. The viewer /
# injector writes events here. (uinput is unavailable: kernel lacks it.) ----
FIFO="${PAGER_INPUT_FIFO:-/tmp/pager_input.fifo}"
if [ ! -p "$FIFO" ]; then
    rm -f "$FIFO" 2>/dev/null || true
    mkfifo "$FIFO"
    chmod 666 "$FIFO"
fi

# ---- backlight ----
mkdir -p "$R/sys/class/backlight/backlight_pwm"
echo 200 > "$R/sys/class/backlight/backlight_pwm/brightness"
echo 255 > "$R/sys/class/backlight/backlight_pwm/max_brightness"

# ---- LEDs (D-pad + buttons) ------------------------------------------------
# LED map (confirmed by RE + strace of the firmware, see docs/LEDS.md):
#   a-button-led          A / SELECT button LED  (on/off; firmware sets 1)
#   b-button-led          B / BACK   button LED  (on/off; firmware sets 1)
#   buzzer                piezo buzzer           (brightness/volume/frequency)
#   {up,down,left,right}-led-{red,green}
#                         D-pad directional LEDs. The REAL Pager D-pad has only
#                         RED + GREEN per position (no blue), so it can show
#                         red / green / yellow(R+G) / off.
#
# CRITICAL (why the D-pad used to stay dark): pineapplepager.SetDpadLED writes
# ALL THREE channels (red, green, AND blue) for every colour, including colours
# with blue=0 (e.g. RED). It builds "<pos>-led-blue/brightness" and opens it
# O_WRONLY|O_TRUNC. If that file does NOT exist in the rootfs, QEMU's -L path
# cache (built once at startup) misses it and the open falls through to the HOST
# /sys (also no such node) -> ENOENT. The firmware treats that open error as a
# fatal step and ABORTS the whole SetDpadLED sequence *after* it already
# O_TRUNC'd the red/green files but *before* writing their brightness values ->
# every D-pad LED ends up truncated-to-empty i.e. dark, for ALL colours.
#
# Faithful fix: materialise the blue-channel files as SINKS. The hardware has no
# blue D-pad LED, so these are write-only sinks that are physically ignored; but
# their presence lets the firmware's 3-channel write complete, so red/green/
# yellow render correctly and blue-containing palette entries (blue/cyan/
# magenta/white) degrade gracefully to just their R+G component (exactly what a
# red+green-only D-pad can show). They MUST exist before qemu starts (prepare.sh
# runs before fw_run.sh) so QEMU's path cache picks them up.
for led in left-led-red left-led-green left-led-blue \
           right-led-red right-led-green right-led-blue \
           up-led-red up-led-green up-led-blue \
           down-led-red down-led-green down-led-blue \
           a-button-led b-button-led buzzer; do
    mkdir -p "$R/sys/class/leds/$led"
    echo 0   > "$R/sys/class/leds/$led/brightness"
    echo 255 > "$R/sys/class/leds/$led/max_brightness"
done

# Default D-pad LED colour: the shipped /etc/config/system had led_color 'blue',
# which is NOT representable on the red+green-only D-pad hardware. Normalise any
# unrepresentable default to a supported colour ('green') so a fresh boot shows
# a lit D-pad instead of a confusing dark one. Only rewrites the stored default;
# the user can still pick any colour from Settings -> D-PAD LED Color.
SYSCFG="$R/etc/config/system"
if [ -f "$SYSCFG" ] && grep -q "option led_color 'blue'" "$SYSCFG" 2>/dev/null; then
    sed -i "s/option led_color 'blue'/option led_color 'green'/" "$SYSCFG"
    echo "[prepare] normalised led_color 'blue' -> 'green' (D-pad has no blue channel)"
fi

# ---- power supply ----
mkdir -p "$R/sys/class/power_supply/battery"
echo 87       > "$R/sys/class/power_supply/battery/capacity"
echo Charging > "$R/sys/class/power_supply/battery/status"
# The firmware reads bq27546-0/{uevent,capacity,status}. Seed it so it exists
# before pager-qemu-battery.service starts mirroring the REAL G82 battery into
# it; otherwise the firmware spams "[POWER] Unable to open power monitor" and
# the UI can hang (e.g. stuck "Reloading UI" after a theme change).
mkdir -p "$R/sys/class/power_supply/bq27546-0"
echo 92          > "$R/sys/class/power_supply/bq27546-0/capacity"
echo Discharging > "$R/sys/class/power_supply/bq27546-0/status"
echo Good        > "$R/sys/class/power_supply/bq27546-0/health"
printf 'POWER_SUPPLY_NAME=bq27546-0\nPOWER_SUPPLY_TYPE=Battery\nPOWER_SUPPLY_STATUS=Discharging\nPOWER_SUPPLY_CAPACITY=92\nPOWER_SUPPLY_PRESENT=1\n' \
    > "$R/sys/class/power_supply/bq27546-0/uevent"

# ---- hwmon / mt7921 temperature ----
mkdir -p "$R/sys/class/hwmon/hwmon0"
echo 42000 > "$R/sys/class/hwmon/hwmon0/temp1_input"

# ---- ieee80211 phy macaddresses (match the fake nl80211 wiphys in qemu) ----
mkdir -p "$R/sys/class/ieee80211/phy0" "$R/sys/class/ieee80211/phy1"
echo "00:c0:ca:11:22:33" > "$R/sys/class/ieee80211/phy0/macaddress"
echo "00:c0:ca:44:55:66" > "$R/sys/class/ieee80211/phy1/macaddress"

# ---- gpio vibrator (fake node; bridged to real G82 vibrator by
#      pager-qemu-vibrator.service / vibrator_bridge.py) ----
mkdir -p "$R/sys/class/gpio/vibrator"
echo 0 > "$R/sys/class/gpio/vibrator/value"

# ---- bluetooth: the firmware toggles BT by exec'ing `hciconfig <if> up|down`.
# The rootfs hciconfig is MIPS and cannot reach the G82's real controller under
# qemu-user (the HCI ioctls are not emulated: it fails cleanly with ENOTTY /
# "Not a tty"). The firmware treats that as a handled error ("Error setting
# bluetooth status"), so the BT toggle does NOT crash -- it just has no effect.
#
# A native host helper (scripts/bt_bridge.py, service pager-qemu-bt.service) is
# provided that DOES drive the real hci0 (verified), and a shell shim
# (hciconfig.shim) that would route the firmware's call to it. BUT qemu-user
# cannot exec a shebang script from inside the guest (nested execve of a #! file
# returns ENOENT), and there is no MIPS cross-compiler on the device to build a
# native front-end, so the shim cannot currently be wired in. We therefore keep
# the original MIPS hciconfig (graceful failure, no crash). See README for the
# full analysis and the remaining step (a native MIPS front-end) to finish it.
if [ -e "$R/usr/bin/hciconfig.real" ] && head -c4 "$R/usr/bin/hciconfig" 2>/dev/null | grep -q "ELF"; then
    : # already the real ELF binary; nothing to do
elif [ -e "$R/usr/bin/hciconfig.real" ]; then
    # a previous run installed the (non-working) shim; restore the real binary
    cp -a "$R/usr/bin/hciconfig.real" "$R/usr/bin/hciconfig"
    echo "[prepare] restored real MIPS hciconfig (shim not viable under qemu-user)"
fi

# ---- /rom squashfs lower layer: on the real device this mirrors the rootfs.
# The firmware falls back to /rom/lib/pager/themes/... for theme/ROM assets.
# qemu-user does not reliably follow chained absolute symlinks under -L, so we
# materialize the needed trees under /rom with real files (hardlink copy). ----
mkdir -p "$R/rom/lib"
if [ ! -e "$R/rom/lib/pager" ]; then
    cp -al "$R/lib/pager" "$R/rom/lib/pager" 2>/dev/null || cp -a "$R/lib/pager" "$R/rom/lib/pager"
fi
# some ROM fallbacks also look under /rom/lib/hak5 and /rom/pineapple
[ -e "$R/lib/hak5" ] && [ ! -e "$R/rom/lib/hak5" ] && { cp -al "$R/lib/hak5" "$R/rom/lib/hak5" 2>/dev/null || cp -a "$R/lib/hak5" "$R/rom/lib/hak5"; }
if [ ! -e "$R/rom/pineapple" ]; then
    cp -al "$R/pineapple" "$R/rom/pineapple" 2>/dev/null || true
fi

# ---- device identity jsons (already present, (re)assert) ----
for p in "$R/tmp/board.json" "$R/etc/device.config" "$R/usb/device.config"; do
    mkdir -p "$(dirname "$p")"
    if [ ! -f "$p" ]; then
        echo '{"model":{"id":"hak5,wifi-pineapple-pager","name":"WiFi Pineapple Pager"}}' > "$p"
    fi
done

# ---- USER DATA TREES the firmware writes to -------------------------------
# On the real device these live on the eMMC (/mmc) + /root. The firmware MOVES
# downloaded payloads from /root/.cache/payloads -> /root/payloads/<cat>/<sub>/,
# writes loot, reads themes/ringtones. If ANY of these dirs is missing the
# firmware fails with "rename ...: no such file or directory" (payload install),
# or empty theme/ringtone lists. Create the WHOLE tree so nothing is missing.
#
# payload categories + their known subcategories (from the firmware strings).
for d in \
    payloads payloads/user payloads/recon payloads/alerts \
    payloads/user/games payloads/user/general payloads/user/exfiltration \
    payloads/recon/ap payloads/recon/client payloads/recon/deauth \
    payloads/alerts/deauth payloads/alerts/handshake payloads/alerts/pineapple \
    themes ringtones \
    loot loot/handshakes loot/pcap loot/wigle loot/changelog \
    recon upgrade \
    .cache .cache/payloads .cache/payloads/downloading .cache/payloadportal ; do
    mkdir -p "$R/root/$d"
done
# the eMMC mirror the firmware also probes
for d in root/pager root/payloads root/loot root/uidebug root/upgrade \
         bin sbin usr/bin usr/sbin ; do
    mkdir -p "$R/mmc/$d"
done
chmod -R 0777 "$R/root/payloads" "$R/root/loot" "$R/root/themes" \
    "$R/root/ringtones" "$R/root/.cache" "$R/mmc" 2>/dev/null || true

# ---- POBLAR con el contenido de comunidad (themes / ringtones / payloads) --
# Si existe el arbol de comunidad del port, lo materializamos en el rootfs del
# firmware para que /root/themes, /root/ringtones y /root/payloads tengan
# contenido real (54 temas, 101 ringtones, 199 payloads). Idempotente.
COMM="${PAGER_COMMUNITY:-/opt/pineapple-pager/community}"
if [ -d "$COMM/themes" ]; then
    # los temas de pantalla van a /lib/pager/themes (+ /rom) y /root/themes
    for t in "$COMM"/themes/*; do
        [ -d "$t" ] || continue
        n="$(basename "$t")"
        [ -e "$R/lib/pager/themes/$n" ] || cp -a "$t" "$R/lib/pager/themes/$n" 2>/dev/null || true
        [ -e "$R/root/themes/$n" ]      || cp -a "$t" "$R/root/themes/$n" 2>/dev/null || true
    done
    # refrescar el mirror /rom
    rm -rf "$R/rom/lib/pager/themes" 2>/dev/null
    cp -al "$R/lib/pager/themes" "$R/rom/lib/pager/themes" 2>/dev/null || \
        cp -a "$R/lib/pager/themes" "$R/rom/lib/pager/themes" 2>/dev/null || true
fi
if [ -d "$COMM/ringtones" ]; then
    for rt in "$COMM"/ringtones/*.rtttl; do
        [ -f "$rt" ] || continue
        bn="$(basename "$rt")"
        [ -e "$R/root/ringtones/$bn" ]     || cp -a "$rt" "$R/root/ringtones/$bn" 2>/dev/null || true
        [ -e "$R/lib/pager/ringtones/$bn" ]|| cp -a "$rt" "$R/lib/pager/ringtones/$bn" 2>/dev/null || true
    done
fi
if [ -d "$COMM/payloads" ]; then
    # community payloads estan en recon/alerts/user -> mapear a /root/payloads/<cat>
    for cat in recon alerts user; do
        [ -d "$COMM/payloads/$cat" ] || continue
        cp -a "$COMM/payloads/$cat/." "$R/root/payloads/$cat/" 2>/dev/null || true
    done
fi
chmod -R 0777 "$R/root/payloads" "$R/root/themes" "$R/root/ringtones" 2>/dev/null || true

# ---- DUCKYSCRIPT PAYLOAD RUNTIME (host-side bridge) ------------------------
# Make launched payloads actually RUN: expose /lib/pineapple, build the host
# DuckyScript command dir (LOG, LED, ALERT, PROMPT, WAIT_FOR_*, the *_PICKER
# family, SPINNER, jshn, ...) and mirror it onto the payload's real PATH
# (/mmc/usr/bin). The firmware runs payloads with the HOST bash + an EMPTY
# inherited PATH, so these commands (which POST to /tmp/api.sock, exactly like
# on the device) must live where the firmware's injected header searches.
# fw_run.sh also runs this on every boot; doing it here keeps a manual
# prepare.sh self-sufficient. See scripts/payload_env.sh and docs/PAYLOADS.md.
PENV="$(dirname "$0")/payload_env.sh"
if [ -x "$PENV" ]; then
    ROOTFS="$R" "$PENV" || echo "[prepare] WARNING: payload_env.sh failed"
fi

echo "[prepare] done"
