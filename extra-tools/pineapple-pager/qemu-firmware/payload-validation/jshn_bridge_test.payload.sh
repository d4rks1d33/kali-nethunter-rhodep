#!/bin/bash
# Title: JSHN Bridge Test
# Author: port-validation
# Description: Verifies jshn (JSON<->shell) resolves and works inside a payload.
# Version: 1.0
LOG green "jshn test: parsing JSON via Hak5 jshn..."
. /lib/hak5/commands.sh
json_init
json_load '{"ip":"10.0.0.9","port":1337}'
json_get_var IPV ip
json_get_var PRT port
if [ "$IPV" = "10.0.0.9" ] && [ "$PRT" = "1337" ]; then
    LOG green "jshn OK: ip=$IPV port=$PRT"
else
    LOG red "jshn FAILED: ip='$IPV' port='$PRT'"
fi
LOG "jshn test done."
