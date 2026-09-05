#!/bin/bash
# Nessus entrypoint. The crack is already baked into the image.
# On every start just write the plugin_feed_info (stays fresh)
# and exec nessusd as PID 1.
set -euo pipefail

# Keep plugin_feed_info.inc in place (Nessus tries to overwrite it).
FEED=/opt/nessus/var/nessus/plugin_feed_info.inc
if [ ! -s "$FEED" ]; then
    printf 'PLUGIN_SET = "202609041258";\nPLUGIN_FEED = "ProfessionalFeed (Direct)";\nPLUGIN_FEED_TRANSPORT = "Tenable Network Security Lightning";\n' \
        > "$FEED"
fi
cp "$FEED" /opt/nessus/lib/nessus/plugins/plugin_feed_info.inc 2>/dev/null || true

echo "[nessus-entrypoint] starting nessusd (PID 1)"
exec /opt/nessus/sbin/nessus-service -q
