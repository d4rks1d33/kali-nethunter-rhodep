#!/bin/sh
# Build the native core, install the Python app, and wire up desktop
# integration. Run as root on the phone (Kali ARM64 + Plasma Mobile).
#
# System dependencies (install first with apt):
#   gtk4, libadwaita, python3-gi, gir1.2-gtk-4.0, gir1.2-adw-1,
#   gcc, make, hackrf (the hackrf_transfer / hackrf_info CLI tools)
#
#   apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 \
#               libgtk-4-1 libadwaita-1-0 gcc make hackrf
set -e
here=$(dirname "$0")

native="$here/src/pandora_emu/native"

# 1. Compile the emulator core into libpandora.so, right next to the Python
#    module so core.py finds it (it searches the module dir first).
echo "building libpandora.so ..."
make -C "$native"
cp -f "$native/libpandora.so" "$here/src/pandora_emu/libpandora.so"

# 2. Install the Python package (package-data pulls in libpandora.so + css).
pip install --break-system-packages "$here"

# 3. Desktop entry + polkit action.
install -Dm644 "$here/data/org.kali.PandoraEmu.desktop" \
    /usr/share/applications/org.kali.PandoraEmu.desktop
install -Dm644 "$here/data/org.kali.PandoraEmu.policy" \
    /usr/share/polkit-1/actions/org.kali.PandoraEmu.policy

# 3b. App icon in a few sizes so Plasma Mobile picks a crisp one.
# The source PNG is 192x192; resize to the hicolor theme sizes.
for size in 48 64 128 192; do
    if command -v convert >/dev/null 2>&1; then
        convert "$here/data/icons/org.kali.PandoraEmu.png" \
            -resize ${size}x${size} \
            "/tmp/pandora-emu-icon-${size}.png"
        install -Dm644 "/tmp/pandora-emu-icon-${size}.png" \
            "/usr/share/icons/hicolor/${size}x${size}/apps/org.kali.PandoraEmu.png"
        rm -f "/tmp/pandora-emu-icon-${size}.png"
    fi
done
# Always install the source PNG at its native size as a fallback.
install -Dm644 "$here/data/icons/org.kali.PandoraEmu.png" \
    /usr/share/icons/hicolor/192x192/apps/org.kali.PandoraEmu.png

# 4. HackRF udev rule so the login user can reach the SDR without root.
#    (Skip silently if /etc/udev is not present.)
if [ -d /etc/udev/rules.d ]; then
    cat > /etc/udev/rules.d/53-pandora-hackrf.rules <<'EOF'
# HackRF One / Jawbreaker: grant the console user access.
ATTR{idVendor}=="1d50", ATTR{idProduct}=="6089", MODE="0660", TAG+="uaccess"
ATTR{idVendor}=="1d50", ATTR{idProduct}=="604b", MODE="0660", TAG+="uaccess"
EOF
    udevadm control --reload-rules 2>/dev/null || true
    udevadm trigger 2>/dev/null || true
fi

# 5. Refresh caches so Plasma Mobile picks up the entry.
update-desktop-database /usr/share/applications 2>/dev/null || true
gtk-update-icon-cache -f /usr/share/icons/hicolor 2>/dev/null || true

echo "installed; look for 'Pandora Emu' in the app grid"
