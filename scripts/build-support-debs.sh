#!/bin/sh
# Build the support .deb packages from the sources in packages/.
# Requires: fakeroot, dpkg-deb. Run from the repo root.
set -e
cd "$(dirname "$0")/../packages"
for pkg in rhodep-modem-support rhodep-usb-otg rhodep-battery-jeita \
           rhodep-phosh-wifi-guard rhodep-gpu-opencl rhodep-gpu-vulkan \
           rhodep-gnss; do
    ver=$(grep '^Version:' "$pkg/DEBIAN/control" | awk '{print $2}')
    out="../${pkg}_${ver}_arm64.deb"
    echo "Building $out"
    # A D-Bus policy file that does not parse is not a policy file. dbus-daemon
    # logs "not well-formed" and then ignores the whole file, so a security
    # deny silently stops existing while the file sits there looking correct.
    # (XML comments may not contain two consecutive hyphens; that is how it
    # happened.) Refuse to build rather than ship one.
    for conf in "$pkg"/usr/share/dbus-1/system.d/*.conf; do
        [ -e "$conf" ] || continue
        python3 -c 'import sys,xml.dom.minidom; xml.dom.minidom.parse(sys.argv[1])' \
            "$conf" || { echo "  $conf is not well-formed XML" >&2; exit 1; }
    done
    find "$pkg" -type d -exec chmod 0755 {} \;
    fakeroot dpkg-deb --build -Zxz "$pkg" "$out"
done
echo ""
echo "firmware-motorola-rhodep is NOT built here (needs vendor blobs)."
echo "See packages/firmware-motorola-rhodep/README.md"
