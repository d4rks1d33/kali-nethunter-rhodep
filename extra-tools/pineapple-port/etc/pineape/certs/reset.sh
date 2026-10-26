#!/bin/bash
# reset.sh -- restore the .cnf files from their .master templates.
# Ported verbatim from the original WiFi Pineapple (/etc/pineape/certs/reset.sh).
cd "$(dirname "$0")" || exit 1
echo "[*] Restoring config defaults"
ls *.master | while read f; do
	name="$(echo "$f" | sed -e 's/\.master$//')"
	cmp "$f" "$name" > /dev/null 2>&1 || cp -v "$f" "$name"
done
