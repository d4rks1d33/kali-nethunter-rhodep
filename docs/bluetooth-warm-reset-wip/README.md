# Bluetooth died on a warm reset of the controller (SOLVED)

Turning Bluetooth off from the quick-settings shade and back on failed. It took
four or five rapid presses to get it back, and often it did not come back at
all until the phone was rebooted:

	Bluetooth: hci0: QCA Downloading qca/crbtfw21.tlv
	Bluetooth: hci0: command 0xfc00 tx timeout
	Bluetooth: hci0: QCA Failed to send TLV segment (-110)

Afterwards `hci_dev_open()` returned EOPNOTSUPP with no kernel activity at all
and `btmgmt info` said "Index list with 0 items".

## The cause

`icc_node_add()` gives every interconnect node `init_avg = init_peak = INT_MAX`
when the provider has no `get_bw()`. That floor is what keeps the buses at the
rate the RPM handed over at boot. `icc_sync_state()` drops it once every
provider has probed:

	if (n->init_avg || n->init_peak) {
		n->init_avg = 0;
		n->init_peak = 0;
		aggregate_requests(n);
		p->set(n, n);
	}

From that moment a node gets exactly what its consumers vote for — and on
sm6375 almost nothing votes. The device tree wires up the display and the IPA,
and that is all. Everything else goes to zero, including the QUP the Bluetooth
UART runs on.

**The timing is the tell**, and it is in every log taken while chasing this: the
two setups that run during boot always succeed, because `sync_state` has not
fired yet, and the failures only ever start afterwards.

This was dormant until patch 0065. That patch needed the interconnect provider
built in rather than a blacklisted module, so from then on the provider binds,
`sync_state` runs, and the floors are dropped. Before it, the provider never
bound at all and the RPM's boot vote stood.

## The fix

- **0088-interconnect-sm6375-keep-the-boot-floors.patch** — do not register
  `.sync_state` for this SoC, so the boot floors stay. This is the one that
  fixes it. It gives up bus scaling, which this port never actually had.
- **0086-arm64-dts-qcom-sm6375-uart-interconnect-paths.patch** — give uart1 the
  `qup-core` and `qup-config` paths that sm6115 has and sm6375 never did.
  Correct on its own terms, and required if `.sync_state` is ever restored, but
  **not sufficient**: see below.

Drop 0088 once every consumer that matters declares its paths — and note that
adding them one at a time does not work as an interim step.

## The evidence

Bisected across boot images, same rootfs, same harness, 150 controller resets
per image:

| image | date | result |
| --- | --- | --- |
| `kali-boot-v108-STABLE-camera.img` | 19 Aug | **150/150 pass** (provider never binds) |
| `kali-boot-glinkfix.img` | 25 Aug | fails at cycle 2 |
| `kali-boot-dload.img` | 26 Aug | fails at cycle 1 |
| `kali-boot-estable.img` | 28 Aug | fails within 1-6 |
| `kali-boot-btfix-qup-icc.img` (0086) | — | still fails |
| **`kali-boot-btfix2-icc-floors.img`** (0086+0088) | — | **150/150 pass, 166 setups, 0 timeouts** |

The device-tree diff between the last good image and the first bad one shows
`interconnects` appearing on `display-subsystem@5e00000`, which is 0065 going in.

And the mechanism was measured directly, not inferred:

	before:  qup0_core_master            0            0
	after:   qup0_core_master   2147483647   2147483647

`qxs_imem`, `qhs_qup0` and `ebi` likewise hold INT_MAX with 0088 in.

## What 0086 alone did, and why that mattered

0086 provably works: `qup0_core_master` went from `0 0` to `2 2`, which is
`Bps_to_icc(CORE_2X_50_MHZ)` — the high-speed vote `qcom_geni_serial` asks for
whenever the port runs above 115200:

	avg_bw_core = (baud > 115200) ? Bps_to_icc(CORE_2X_50_MHZ)
				      : GENI_DEFAULT_BW;

It also changed behaviour measurably: for the first time a failed setup
recovered on the driver's own retry instead of all four attempts failing
together. It still did not fix the problem, because repairing one consumer does
not help the others that are also at zero. That negative result is what pointed
at `sync_state` rather than at any single path.

## Dead ends, each killed with a measurement

None of these need testing again.

- **"The chip stops answering over UART."** It does not. On every failed attempt
  and every retry it answered the EDL version query — Product ID `0x0000000a`,
  SOC `0x40020150`, ROM `0x00000201`, controller version `0x01500201`. It is
  powered, out of reset, in ROM mode and talking; only the download fails.
  (It *does* go fully mute eventually, but that is a late state reached after
  the controller has been hammered, not the fault that starts it.)
- **The switch to 3.2 Mbps.** A driver patched to fall back to 115200 after two
  failures was built and installed. The fallback fires and the download then
  fails at 115200 in exactly the same place. Patch dropped.
- **The length of the transfer.** `tlv_seg_size` was made writable
  (0087-DIAGNOSTIC, kept out of `source=`) and the download forced to 32- and
  then 16-byte segments. Identical failure at every size.
- **The power pulse.** Mainline switches this chip's BT core with a one-byte
  pulse on the UART Tx (`0xC0` off, `0xFC` on) decoded by an external circuit,
  not with the rails — `qca_send_power_pulse()` says so — and `qca_power_on()`
  already sends both on every open. The logs confirm it happens.
- **The shared rails.** Three of the four BT rails are shared with WiFi.
  Unloading `ath10k_snoc` to release them and waiting 10 s did not recover a
  wedged controller. (Caveat: these are RPM regulators and unloading only drops
  Linux's votes, so the rails may never have gone low. The practical half stands:
  it is not a recovery path.)
- **`rfkill block`/`unblock`.** Eight cycles, zero kernel activity. Once the
  controller is wedged, nothing short of a reboot re-runs setup.
- **The QUP core vote as measured on a single image.** `qup0_core_master` read
  `0 0` in the healthy state *and* the broken one, which looked like a clean
  negative and was not: the provider is bound in both, so the damage was already
  done at `sync_state` and the reading discriminated nothing.

## Traps for whoever measures this again

- **A wedged controller fails `hciconfig hci0 up` silently** — EOPNOTSUPP, not
  one line in the kernel log. A harness that scores a cycle by *not* finding
  `tx timeout` in `dmesg` counts every cycle on a dead controller as a pass.
  That produced 24 fabricated "successes" here across three conditions. Verify
  positively, in the same cycle: `btmgmt info` lists `hci0` **and**
  `setup on UART is completed` appears.
- **On a wedged controller `hciconfig hci0 up` never reaches the driver at all**,
  so anything you are trying to vary is not being exercised. Force a real setup
  with `unbind`/`bind` on `/sys/bus/serial/drivers/hci_uart_qca` and confirm you
  got one by checking that a timeout appears at all. A run with zero timeouts
  *and* zero setups is a run that did not happen.
- **`pgrep -f <script>` matches your own ssh command line**, so a background job
  can look alive long after it died. Check that its log is growing instead.
- The failure rate on a broken image is erratic — cycle 1, 2 and 6 in different
  runs, but also 15, 34 and 120+ clean in others. Ten presses prove nothing
  either way; use the 150-cycle harness.

## Files

	kernel/patches/0086-arm64-dts-qcom-sm6375-uart-interconnect-paths.patch
	kernel/patches/0088-interconnect-sm6375-keep-the-boot-floors.patch
	kernel/patches/0087-DIAGNOSTIC-btqca-settable-tlv-segment-size.patch   (not in source=)
	docs/bluetooth-warm-reset-wip/README.md                               (this file)
