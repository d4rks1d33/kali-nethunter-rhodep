#!/bin/bash
systemctl stop ModemManager 2>/dev/null
ARM=4b0b27000210000000000000000000000000
TE=4b0b27000d000300010004000000000002000400010000000300040000000000
RC=4b0b27000010050001000400000000001900040001000000050004000300000006000400270600000700040003000000
python3 /home/kali/rhodep-diag-server.py --log /tmp/iqexp/exp14.log --seconds 75 --restart-modem --set-ftm-after --cmd-after 25 --cmd-peer --raw-seq "$ARM,$TE,$RC"
echo "DONE $(wc -l < /tmp/iqexp/exp14.log)"
