#!/bin/sh
# uninstall.sh -- convenience wrapper for `install.sh --uninstall`.
#
# Part of: extra-tools/pineapple-port/   (FASE 4)
#
# Reverts the entire port (units, binaries, symlinks, /opt/pineapple) without
# touching the phone's OS/network. apt packages are left installed.
exec "$(cd "$(dirname "$0")" && pwd)/install.sh" --uninstall "$@"
