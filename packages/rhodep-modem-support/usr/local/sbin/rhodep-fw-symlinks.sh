#!/bin/sh
# Expone firmware modem/adsp/cdsp desde modem_a y arranca los remoteprocs.
FW=/lib/firmware/qcom/sm6375/motorola/rhodep
IMG=/readonly/firmware/image
mkdir -p "$FW"
[ -d "$IMG" ] || exit 0
for base in modem adsp cdsp wpss; do
    [ -e "$IMG/$base.mdt" ] || continue
    ln -sf "$IMG/$base.mdt" "$FW/$base.mbn"
    ln -sf "$IMG/$base.mdt" "$FW/$base.mdt"
    for seg in "$IMG/$base".b*; do
        [ -e "$seg" ] && ln -sf "$seg" "$FW/$(basename "$seg")"
    done
done
# arrancar remoteprocs (adsp, luego modem, luego cdsp). Ya con firmware presente.
for name_want in adsp modem cdsp; do
    for r in /sys/class/remoteproc/remoteproc*; do
        [ "$(cat "$r/name" 2>/dev/null)" = "$name_want" ] || continue
        state=$(cat "$r/state" 2>/dev/null)
        if [ "$state" = "offline" ]; then
            echo start > "$r/state" 2>/dev/null || true
            sleep 3
        fi
    done
done
exit 0
