#!/bin/sh
# fix-community-modules.sh -- idempotent repairs for panel COMMUNITY modules.
#
# Part of: extra-tools/pineapple-port/net/
# Invoked by: install.sh (after the panel overlay) and safe to run by hand.
#
# Community modules (EvilPortal, PortalAuth, DNSMasqSpoof, HTTPProxy, ...) are
# installed by the user from the panel's Module Manager, NOT shipped by this
# port, so install.sh deliberately PRESERVES them. But those modules ship with
# the original Pineapple's hard-coded LAN IP (172.16.42.1) and, for EvilPortal,
# a couple of bugs that break them on this port:
#
#   1. LAN IP drift: the port moved the Pineapple LAN from 172.16.42.1 to
#      172.16.84.1 (so it never clashes with usb0 = the SSH lifeline). Modules
#      with 172.16.42.1 baked into iptables DNAT rules / iframes point at the
#      wrong host, so the captive portal and previews don't work.
#   2. EvilPortal "-pine" portals shipped with a mis-named .ep descriptor
#      (<base>.ep instead of <dir>.ep), so the panel couldn't identify them.
#   3. EvilPortal Live Preview called refreshLivePreview(), which was missing
#      from module.js, and its iframe pointed at the old IP.
#
# Everything here is idempotent: it only changes what is still wrong, and never
# touches captured data (.logs, credentials, client lists).

set -u

PREFIX="${PINEAPPLE_PREFIX:-/opt/pineapple}"
PANEL="${PANEL_DIR:-$PREFIX/panel}"
PORTALS_DIR="${PORTALS_DIR:-/root/portals}"
OLD_IP="172.16.42"
NEW_IP="172.16.84"

log() { echo "[fix-community] $*"; }

[ -d "$PANEL/modules" ] || { log "no panel modules dir ($PANEL/modules); nothing to do"; exit 0; }

# --- 1. Rewrite the old LAN IP in community modules ------------------------- #
# Only touch MODULE code/config; never touch captured data files. We scan the
# modules tree and replace 172.16.42.x -> 172.16.84.x in text files that are
# part of the module (php/html/js/sh/txt/conf/executable), skipping logs.
log "rewriting ${OLD_IP}.x -> ${NEW_IP}.x in panel community modules"
for f in $(grep -rlI "${OLD_IP}\." "$PANEL/modules" 2>/dev/null); do
    case "$f" in
        *.log|*/.logs|*CLIENTS*|*clients*|*.bak*) continue ;;   # never touch data/backups
    esac
    sed -i "s/${OLD_IP}\./${NEW_IP}./g" "$f" 2>/dev/null \
        && log "  fixed IP in ${f#$PANEL/modules/}"
done

# --- 2. EvilPortal: fix mis-named .ep descriptors -------------------------- #
# The panel lists a portal only if <dir>/<dir>.ep exists. Some bundled portals
# (notably the "-pine" variants) ship <dir>/<base>.ep. Rename to match the dir
# and fix the "name" field inside the JSON.
if [ -d "$PORTALS_DIR" ]; then
    log "checking EvilPortal portal descriptors in $PORTALS_DIR"
    for dir in "$PORTALS_DIR"/*; do
        [ -d "$dir" ] || continue
        name="$(basename "$dir")"
        want="$dir/$name.ep"
        [ -f "$want" ] && continue          # already correct
        # find any other .ep in this portal and rename it
        have="$(find "$dir" -maxdepth 1 -name '*.ep' 2>/dev/null | head -n1)"
        if [ -n "$have" ]; then
            mv "$have" "$want" 2>/dev/null || continue
            sed -i "s/\"name\":\"[^\"]*\"/\"name\":\"$name\"/" "$want" 2>/dev/null || true
            log "  fixed descriptor: $name/$(basename "$have") -> $name.ep"
        fi
    done
fi

# --- 3. EvilPortal: add the missing refreshLivePreview() handler ----------- #
EP_JS="$PANEL/modules/EvilPortal/js/module.js"
if [ -f "$EP_JS" ] && ! grep -q 'refreshLivePreview' "$EP_JS"; then
    log "adding missing refreshLivePreview() to EvilPortal module.js"
    # Insert the function right after the loadLog function's closing "    };".
    # We append before the final "}]);" to be robust to version differences.
    tmp="$EP_JS.fixtmp.$$"
    awk -v ip="$NEW_IP" '
        BEGIN { done=0 }
        /^}\]\);[[:space:]]*$/ && !done {
            print "    $scope.refreshLivePreview = function() {";
            print "        var f = document.getElementById(\"livePreviewIframe\");";
            print "        if (f) { f.src = \"http://" ip ".1/?_=\" + Date.now(); }";
            print "    };";
            print "";
            done=1
        }
        { print }
    ' "$EP_JS" > "$tmp" && mv "$tmp" "$EP_JS"
fi

log "community module fixups complete."
exit 0
