#!/bin/bash
# Acunetix container entrypoint.
# Starts the Acunetix service (which manages its own embedded PostgreSQL).
# On first boot the DB is already initialized by the installer; we just start.
set -e

SUPERVISOR=/home/acunetix/.acunetix/supervisor

echo "[acunetix-entrypoint] starting Acunetix..."

# Redirect Acunetix license validation to localhost (crack requirement).
grep -q "erp.acunetix.com" /etc/hosts 2>/dev/null || \
    printf '127.0.0.1  erp.acunetix.com\n127.0.0.1  erp.acunetix.com.\n::1  erp.acunetix.com\n::1  erp.acunetix.com.\n' >> /etc/hosts

exec sudo -u acunetix "$SUPERVISOR"
