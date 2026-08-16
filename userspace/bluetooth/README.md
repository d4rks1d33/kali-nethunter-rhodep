# Bluetooth

	sudo ./install.sh

The WCN3990 controller and its firmware work out of the box. `hci_qca` loads
`qca/crbtfw21.tlv` and `qca/crnv21.bin` over the UART and reports:

	Bluetooth: hci0: QCA setup on UART is completed

Two things still needed doing on this port.

## The service was masked

`bluetooth.service` was found symlinked to `/dev/null`, alongside a drop-in
restricting bluetoothd to `--noplugin=input,avdtp,a2dp,avrcp`. That reads like
it was turned off while the audio stack was being debugged and never turned
back on; nothing in the repo documents it.

Unmasked and verified on the device: `bluetoothd` starts, `hci0` goes
`UP RUNNING PSCAN`, a scan finds devices (7 in 8 seconds), and the audio card
and its PCM list are byte for byte the same before and after — which was the
thing worth checking, since the audio stack is the likely reason it was masked.

The `--noplugin` drop-in is left alone. A2DP over Bluetooth is a separate
feature that has never been validated here, and re-enabling those plugins
belongs in whatever session actually tests it.

## The address was random

The controller does not hold its own address. Mainline's `hci_qca` reads it
from `local-bd-address` in the device tree, and this port's device tree does
not describe the controller at all, so the stack invents one:

	BD Address: 02:00:60:3B:D3:94        <- 02: is locally administered

A new one every boot, so every device the phone has paired with sees a
different phone afterwards and the pairing is dead. The real address is on the
kernel command line, where the bootloader put it:

	androidboot.btmacaddr=E4:26:D5:01:11:0F

`rhodep-bt-address` programs it with `btmgmt public-addr` before `bluetoothd`
starts, which is the only moment the controller accepts it — it has to be
powered down. Verified:

	hci0 Set Public Address complete
	BD Address: E4:26:D5:01:11:0F

Doing it here keeps this a rootfs change, so it needs no new boot image.
**Adding `local-bd-address` to the device tree is the tidier fix** and is worth
doing the next time the boot image is rebuilt for another reason; then this
service becomes a no-op and can be dropped.

## Checking it

	systemctl is-active bluetooth
	hciconfig hci0                       # expect UP RUNNING and the E4:26 address
	bluetoothctl --timeout 8 scan on
