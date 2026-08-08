#!/bin/sh
# Expose modem/adsp/cdsp firmware from the modem_a partition and start the
# remoteprocs. The kernel asks for <base>.mbn but the vendor partition ships
# <base>.mdt + .bNN segments, so symlink .mbn -> .mdt.
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
# Start the remoteprocs (adsp, then modem, then cdsp) now that firmware is present.
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
