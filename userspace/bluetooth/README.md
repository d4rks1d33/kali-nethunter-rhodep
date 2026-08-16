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

The `--noplugin` drop-in **has now been tested and disabled**, with real
earbuds rather than by argument. With all plugins loaded:

	wpctl status
	  Sinks:
	      32. Speaker
	  *   63. AirPods Pro de Mateo            [vol: 0.70]

	wpctl inspect 63
	  api.bluez5.codec   = "sbc"
	  api.bluez5.profile = "a2dp-sink"

and audio played in stereo through the earbuds, confirmed by ear. The phone's
own speaker, its card and its PCM list were unchanged throughout. The old
drop-in is renamed to `noplugin.conf.disabled-by-rhodep` rather than deleted,
so putting it back is one `mv`.

### The other trap: changing the address invalidates every existing pairing

Anything the phone was paired with before this must be **paired again, once**.
There is no way around it, and the obvious workaround does not work.

bluez keeps pairings in `/var/lib/bluetooth/<adapter>/<device>/`, so after the
address change they sit under an adapter the phone no longer has and
`bluetoothctl devices Paired` comes back empty. Copying the directories across
looked like the fix, and three were recovered that way (earbuds, another phone,
a PC) — `devices Paired` listed all three again. Then connecting failed:

	Failed to connect: org.bluez.Error.Failed br-connection-key-missing

The link key is negotiated **against the adapter address**. Moving the file
moves a key that was agreed with `02:00:60:3B:D3:94`, and the remote device
still holds a key for that address too, so neither side can use it. Tried,
measured, and removed from `install.sh` rather than left in as something that
looks like it helps.

Delete the stale record and pair once more and it sticks, because the address
is now stable across boots:

	bluetoothctl remove <MAC>
	bluetoothctl pair <MAC> && bluetoothctl trust <MAC>

### The trap: WirePlumber has to be restarted, once

WirePlumber builds its Bluetooth device list when **it** starts. Unmask
`bluetooth.service` on a running system and the headset will pair, connect, and
then be dropped by the remote end a few seconds later:

	Connection successful
	hci0 ... disconnected with reason 3
	[SIGNAL] Disconnected - org.bluez.Reason.Remote

That is not a pairing failure. WirePlumber came up while Bluetooth was masked,
so it has no bluez monitor, so nothing offers the headset an audio endpoint,
so the headset gives up. `systemctl --user restart wireplumber pipewire
pipewire-pulse` and it connects and stays. `install.sh` does this for every
logged-in session. On a boot where Bluetooth is enabled from the start the
ordering is right and it does not arise.

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
