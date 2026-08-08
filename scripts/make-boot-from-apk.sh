#!/bin/sh
# Arma un boot.img nuevo a partir del APK del kernel que acaba de construir
# pmbootstrap, reusando el initramfs de un boot.img anterior (no cambia cuando
# solo se toca el kernel).
#
# Uso:  sh make-boot-from-apk.sh <boot-viejo.img> <boot-nuevo.img>
# Ej:   sh make-boot-from-apk.sh /opt/postmarket/out-phosh/boot-v11.img \
#                               /opt/postmarket/out-phosh/boot-v12.img
set -e

OLD="${1:?falta el boot.img viejo (de donde saco el initramfs)}"
NEW="${2:?falta el nombre del boot.img nuevo}"

PMB="${PMB:-$HOME/.local/var/pmbootstrap}"
PKGDIR="$PMB/packages/edge/aarch64"
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

APK=$(ls -t "$PKGDIR"/linux-motorola-rhodep-*.apk 2>/dev/null | head -1)
[ -n "$APK" ] || { echo "ERROR: no encuentro el apk del kernel en $PKGDIR" >&2; exit 1; }
echo "apk:        $APK"

mkdir -p "$WORK/apk" && tar xzf "$APK" -C "$WORK/apk" 2>/dev/null || true
KERNEL=$(find "$WORK/apk" -name vmlinuz | head -1)
DTB=$(find "$WORK/apk" -name 'sm6375-motorola-rhodep.dtb' | head -1)
[ -n "$KERNEL" ] && [ -n "$DTB" ] || { echo "ERROR: falta vmlinuz o el dtb en el apk" >&2; exit 1; }
echo "vmlinuz:    $(stat -c%s "$KERNEL") bytes"
echo "dtb:        $(stat -c%s "$DTB") bytes"

# Sacar el initramfs y el cmdline del boot.img viejo (header Android v2)
python3 - "$OLD" "$WORK/ramdisk" "$WORK/cmdline" <<'EOF'
import struct, sys
old, rd_out, cmd_out = sys.argv[1], sys.argv[2], sys.argv[3]
d = open(old, "rb").read()
assert d[:8] == b"ANDROID!", "no es un boot image Android"
PS = 4096
def pad(n):
    r = n % PS
    return n + (PS - r) if r else n
ksz = struct.unpack_from("<I", d, 8)[0]
rsz = struct.unpack_from("<I", d, 16)[0]
off = PS + pad(ksz)
open(rd_out, "wb").write(d[off:off + rsz])
cmd = d[64:64 + 512].split(b"\0")[0].decode()
extra = d[608:608 + 1024].split(b"\0")[0].decode()
open(cmd_out, "w").write(cmd + extra)
print("initramfs:  %d bytes" % rsz)
print("cmdline:    %s" % (cmd + extra))
EOF

CMDLINE=$(cat "$WORK/cmdline")

# Permitir editar el cmdline via entorno, para imagenes de diagnostico:
#   CMDLINE_DROP="quiet"  quita esas palabras
#   CMDLINE_ADD="..."     agrega al final
if [ -n "$CMDLINE_DROP" ]; then
	for w in $CMDLINE_DROP; do
		CMDLINE=$(printf '%s' "$CMDLINE" | tr ' ' '\n' | grep -vx -- "$w" | tr '\n' ' ')
	done
	echo "cmdline:    (quitado: $CMDLINE_DROP)"
fi
if [ -n "$CMDLINE_ADD" ]; then
	CMDLINE="$CMDLINE $CMDLINE_ADD"
	echo "cmdline:    (agregado: $CMDLINE_ADD)"
fi

# Asegurar clk_scale=100 en el cmdline
case "$CMDLINE" in
	*panel_novatek_nt37701.clk_scale=*) ;;
	*) CMDLINE="$CMDLINE panel_novatek_nt37701.clk_scale=100"
	   echo "cmdline:    (agregado clk_scale=100)" ;;
esac

python3 /opt/postmarket/repo/scripts/mkbootv2b.py \
	"$KERNEL" "$DTB" "$WORK/ramdisk" "$CMDLINE" "$NEW"

echo
echo "listo: $NEW"
md5sum "$NEW"
echo
echo "Flashear con:"
echo "  fastboot flash boot_a $(basename "$NEW")"
echo "  fastboot --set-active=a && fastboot reboot"
