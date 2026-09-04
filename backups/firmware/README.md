# Firmware backups (blobs NOT pushed)

This folder holds local backups of vendor firmware blobs taken before
on-device experiments. The blobs themselves are **git-ignored** (not
redistributable, and large); only this note is tracked. Keep the files here so
they are not lost, but do not push them.

## wlanmdsp.mbn.rhodep-orig

The **pristine** WCN3990 WLAN firmware as shipped on this device.

- Source on device: `/readonly/firmware/image/wlanmdsp.mbn`
  (served to the modem/Q6 by `tqftpserv-rhodep`; the modem authenticates and
  loads it — this is the executable Hexagon QDSP6 image, not `firmware-5.bin`).
- `md5sum`: `f377d18cae70e74610ce386713ecde5c`
- size: 3981160 bytes
- version string inside: `WLAN.HL.3.3.2-00472-QCAHLSWMTPLZ-1.24092.12`
- format: ELF32, `QUALCOMM DSP6 Processor`, entry `0xb0000000`, 7 program
  headers; code segment at file `0x40000` / vaddr `0xb0000000` (size `0x30d06c`,
  `R E`); a hash/signature segment at file `0x1000` / vaddr `0xb07cc000`.

### Why this backup exists

During the Wi-Fi injection investigation (see README item 13) we established
empirically that **this device does NOT enforce secure boot on the WLAN
firmware**: a single byte flipped inside a LOAD (code) segment still loaded and
ran (Wi-Fi associated normally, no PIL/auth failure in dmesg). That means the
image is **patchable** — the realistic path to host-driven TX/injection on the
internal radio is reverse-engineering this Hexagon image and patching the TX
gate, since every driver-side avenue was exhausted (see item 13).

If an experiment ever bricks Wi-Fi, restore with:

```sh
sudo mount -o remount,rw /readonly/firmware
sudo cp wlanmdsp.mbn.rhodep-orig /readonly/firmware/image/wlanmdsp.mbn
sudo sync && sudo reboot
# verify md5 == f377d18cae70e74610ce386713ecde5c
```

A copy of this same pristine blob also lives on the device at
`/root/wlanmdsp.mbn.rhodepbak` (created during the session).
