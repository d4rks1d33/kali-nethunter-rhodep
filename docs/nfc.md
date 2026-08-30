# NFC on the Moto G82 (rhodep) under mainline Linux

The phone's NFC controller is a **Samsung S3NRN4V**, driven by the in-tree
`s3fwrn5` driver with the port's patches (0101-0105). It sits on **i2c-2**
(`4c84000.i2c`, the vendor's `qupv3_se7_i2c`). This document is for writing
your own software against it; for the story of how it was made to work see the
`nfc:` commits and the patch headers.

## What works

Reading every passive tag technology the chip advertises:

- **NFC-A** -- MIFARE Classic/Ultralight/NTAG (Type 2), and ISO-DEP Type 4
  (bank/EMV cards, passports, transit)
- **NFC-B** -- ISO 14443-B
- **NFC-F** -- FeliCa (Type 3)
- **NFC-V** -- ISO 15693 (Type 5)

Verified on hardware with a MIFARE Classic 1K (UID, ATQA 0x0044, SAK 0x08) and
an EMV card (ISO-DEP, SAK 0x20).

**Card emulation (acting as a tag for another reader, e.g. a Flipper) does not
work yet** and is not a small addition -- see the last section.

## The one thing that must be set

The chip needs `rfreg_dual=1` on the `s3fwrn5` module, or it comes up on the
older register-transfer path, talks fine over the bus, and reads nothing
because its antenna is never configured. The port installs
`/etc/modprobe.d/s3fwrn5-rhodep.conf` to set it at boot. If you build the
module yourself, remember this or you will chase a "polls but sees no card"
ghost. The RF register blobs must also be present:

    /lib/firmware/sec_s3fwrn5_rfreg.bin   (3232 bytes, hwreg)
    /lib/firmware/sec_s3fwrn5_swreg.bin   (336 bytes, swreg)

You can confirm the load in `dmesg`:

    nfc nfc0: rfreg configuration update: success (checksum 0x5b9a)

## Two ways to talk to it

### 1. neard over D-Bus -- easy, read-only, NDEF-oriented

`neard` is installed and exposes the adapter on the system bus as
`org.neard` at `/org/neard/nfc0`. It is the simplest path if all you want is
to poll and read NDEF content, and it is what a GTK/Qt app would sit on.

    # power on and poll
    busctl --system set-property org.neard /org/neard/nfc0 \
        org.neard.Adapter Powered b true
    busctl --system call org.neard /org/neard/nfc0 \
        org.neard.Adapter StartPollLoop s Initiator

    # what it found (tags appear as /org/neard/nfc0/tagN)
    busctl --system call org.neard / \
        org.freedesktop.DBus.ObjectManager GetManagedObjects

The `org.neard.Adapter` interface has `StartPollLoop`, `StopPollLoop`,
`Powered`, `Polling`, `Mode` and `Protocols`. Tags expose `org.neard.Tag`;
NDEF records appear as `org.neard.Record`. neard does **not** give you the
raw UID/ATQA/SAK of a non-NDEF card, and it will not let you send arbitrary
APDUs -- for that, use netlink.

Only one program can drive the adapter at a time. If you use netlink
directly, stop neard first: `sudo systemctl stop neard`.

### 2. Kernel NFC netlink -- full control

This is what `neard`, `nfcpy` and the port's own `rhodep-nfc` tool use. It is
a generic-netlink family called `nfc` (resolve its id at runtime with
`CTRL_CMD_GETFAMILY`; it is not fixed). The commands and attributes are in
`include/uapi/linux/nfc.h`. The essentials:

- `NFC_CMD_GET_DEVICE` (dump) -- list adapters, their index, name, protocol
  mask and powered state.
- `NFC_CMD_DEV_UP` -- power the adapter on. Attribute:
  `NFC_ATTR_DEVICE_INDEX`.
- `NFC_CMD_START_POLL` -- begin discovery. Attributes: `NFC_ATTR_DEVICE_INDEX`,
  and a protocol bitmask in `NFC_ATTR_PROTOCOLS` (and `NFC_ATTR_IM_PROTOCOLS`
  for the initiator-mode set). The bit positions are `enum nfc_prot`:
  1=Jewel/NFC-A, 2=MIFARE, 3=FeliCa, 4=ISO-DEP, 5=NFC-DEP, 6=ISO 15693.
- `NFC_EVENT_TARGETS_FOUND` -- multicast event on the `events` group when a
  card is activated. Join the group to receive it.
- `NFC_CMD_GET_TARGET` (dump) -- read the activated targets. Attributes come
  back per technology: `NFC_ATTR_TARGET_NFCID1` (UID),
  `NFC_ATTR_TARGET_SENS_RES` (ATQA), `NFC_ATTR_TARGET_SEL_RES` (SAK),
  `NFC_ATTR_TARGET_ATS`, `NFC_ATTR_TARGET_SENSB_RES`,
  `NFC_ATTR_TARGET_SENSF_RES`, `NFC_ATTR_TARGET_ISO15693_UID`,
  `NFC_ATTR_TARGET_ISO15693_DSFID`.
- `NFC_CMD_STOP_POLL` -- stop. The chip stops on its own once it activates and
  holds a target, so to keep scanning you re-poll after `NFC_EVENT_TARGET_LOST`.

All of these are privileged: you must be root.

For raw APDU exchange with an activated ISO-DEP card, the transport is a
`PF_NFC` `SOCK_SEQPACKET` socket connected to the target's logical connection
(this is how `nfcpy` does tag transceive). `rhodep-nfc` currently reads the
activation data but does not yet open that socket; the disassembly of the
vendor HAL and the `nci_transceive` traces in `dmesg` show it working, so the
next step for an APDU tool is well mapped.

## The ready-made tool: `rhodep-nfc`

Installed at `/usr/local/bin/rhodep-nfc`. Pure Python, stdlib only, speaks
netlink directly. Run as root, with neard stopped.

    sudo systemctl stop neard

    sudo rhodep-nfc info     # adapter and its protocols
    sudo rhodep-nfc read     # poll, print the first card, exit
    sudo rhodep-nfc watch    # keep printing every card presented

Example, a bank card:

    target #1: ISO-DEP (ISO 14443-4) -- e.g. a bank/EMV card or passport
      UID (NFCID1):  0f 58 86 07  (4 bytes)
      ATQA (SENS):   0004
      SAK  (SEL):    20

It names the card from SAK and protocol the way a reader would, and prints the
UID, ATQA, SAK, ATS, and the type-B/F/V specific fields when present.

## A note on the hardware layout

If you write something lower level, these are the board facts established
during the port:

- I2C: `i2c-2` (bus number can move between boots; bind by driver, the NFC
  node is `samsung,s3fwrn5-i2c` at address `0x27`).
- GPIOs on the TLMM (`gpiochip3`): enable/VEN = tlmm48 **active low**
  (`SEC_NFC_PW_ON` asserted = 0), firmware/wake = tlmm8, IRQ = tlmm9,
  clock-request = tlmm7.
- The wake line (tlmm8) must stay high for the whole time NCI is up; the
  driver raises it in `open` and only drops it in `close`. Do not toggle it
  under the driver's feet.

## Card emulation (host card emulation) -- not yet

Emulating a tag so another reader sees the phone is a separate, larger job:

- The mainline NCI core only signals listen-mode activation for **NFC-DEP**
  (peer-to-peer). It has no path to present as a **Type 2/Type 4 tag**, which
  is what emulating a MIFARE card means.
- The `s3fwrn5` driver allocates its device with zero listen protocols and
  sends no proprietary listen-configuration commands.

So emulating even a harmless MIFARE against a Flipper needs new work in both
the NCI core's listen path and the driver, plus confirming from the vendor HAL
that the S3NRN4V exposes card emulation at all. It is on the list as research,
not as a quick follow-up.
