#!/bin/bash
# clean.sh -- remove generated certificates/keys and reset the .cnf templates.
# Ported from the original WiFi Pineapple (/etc/pineape/certs/clean.sh); adds a
# cd to its own dir so it is safe to call by absolute path (the panel does
# `cd /etc/pineape/certs && ./clean.sh`, but callers may invoke it directly).
cd "$(dirname "$0")" || exit 1
echo "[*] Removing certificates and associated files"
rm -fv *.pem
rm -fv *.der
rm -fv *.key
rm -fv *.crt
rm -fv *.csr
rm -fv index.txt*
rm -fv random
rm -fv serial*
rm -fv *.p12
./reset.sh
