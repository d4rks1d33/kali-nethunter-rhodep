#!/bin/bash
# Acunetix container entrypoint.
# Starts the Acunetix service (which manages its own embedded PostgreSQL).
# On first boot the DB is already initialized by the installer; we just start.
set -e

ACUNETIX_DIR=/home/acunetix/.acunetix
SERVICE_BIN=$(find /home/acunetix -name "acunetix" -type f 2>/dev/null | head -1)

if [ -z "$SERVICE_BIN" ]; then
    # Fallback: try the typical install path
    SERVICE_BIN=/home/acunetix/.acunetix/acunetix
fi

echo "[acunetix-entrypoint] starting Acunetix..."
echo "[acunetix-entrypoint] binary: $SERVICE_BIN"

# Ensure /etc/hosts has the crack entries (volume mount may overlay /etc).
grep -q "erp.acunetix.com" /etc/hosts 2>/dev/null || \
    printf '127.0.0.1  erp.acunetix.com\n127.0.0.1  erp.acunetix.com.\n' >> /etc/hosts

exec sudo -u acunetix "$SERVICE_BIN"
