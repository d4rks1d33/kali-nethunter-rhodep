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
work** and is not a small addition -- see the last section. Patch 0111 builds
the full standard-NCI NFC-A listen setup (fixed NFCID1, T2T FRAME mapping, and
the listen mode routing table the mainline core lacked, including a MIFARE 0x80
route); the chip accepts all of it with `status 0x0` but never activates as an
NFC-A tag when a reader is presented. Confirmed dead end from standard NCI: the
NFC-A card-emulation state machine lives in Android's userspace `libnfc-nci`,
and Linux mainline has no host card emulation path. NFC-F (FeliCa) listen does
work.

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

    sudo rhodep-nfc info               # adapter and its protocols
    sudo rhodep-nfc read              # poll, print the first card, exit
    sudo rhodep-nfc watch             # keep printing every card presented
    sudo rhodep-nfc raw [file.pcap]   # capture every NCI frame, hex + pcap
    sudo rhodep-nfc attrs             # dump activation attributes only
    rhodep-nfc --help                 # full usage, and how to add a protocol

Example, a bank card:

    target #1: ISO-DEP (ISO 14443-4) -- e.g. a bank/EMV card or passport
      UID (NFCID1):  0f 58 86 07  (4 bytes)
      ATQA (SENS):   0004
      SAK  (SEL):    20

It names the card from SAK and protocol the way a reader would, and prints the
UID, ATQA, SAK, ATS, and the type-B/F/V specific fields when present.

To analyse a card whose protocol is not yet handled, `raw` is the real tool.
It taps the kernel's `PF_NFC`/`SOCK_RAW` socket, which gets a copy of **every
NCI frame** exchanged with the controller in both directions — the whole wire
conversation with the presented tag, every command, response and APDU. It
prints each frame live (direction, type, hex, and a name for common NCI
messages) and, given a path, writes a pcap:

    sudo rhodep-nfc raw /tmp/card.pcap

The pcap uses link type USER0; in Wireshark use *Decode As* to point the NCI
dissector at it, or read the hex. Each record is `[dev][dir|type<<1]` followed
by the raw NCI frame, exactly as the kernel delivered it.

This is not an RF sniffer: the S3NRN4V only reports what it itself transacts,
so `raw` cannot capture a transaction between two *other* devices (for that you
need a Proxmark). But for a tag you present to the phone it is the complete,
byte-exact conversation.

`attrs` is the lighter, handshake-only view: the parsed activation attributes
as label + hex, including any the tool has no name for (`attr <N>`).

`rhodep-nfc --help` explains exactly where to add a new protocol bit, a new
activation attribute, or a new card name — and when the change belongs in the
driver instead (an unmapped vendor protocol value, patch 0105).

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

## Card emulation -- listen works, tag activation is the open point

Started. `rhodep-nfc listen [uid [sak]]` puts the controller into listen mode.
Two kernel patches back it:

- **0110** makes the s3fwrn5 device advertise `NFC_PROTO_NFC_DEP_MASK`, which is
  the only target protocol the mainline NCI core wires up. Without it
  `nfc_genl_start_poll` rejects any `tm_protocols` and the chip never listens.
- **0111** teaches the NCI core to present an NFC-A tag: it adds the config tags
  `LA_BIT_FRAME_SDD` (0x30), `LA_PLATFORM_CONFIG` (0x31) and `LA_NFCID1` (0x33),
  carries a UID and a SAK from userspace on `START_POLL` (reusing
  `NFC_ATTR_TARGET_NFCID1` and `NFC_ATTR_TARGET_SEL_RES`, no ABI change), maps
  T2T to the FRAME interface in `RF_DISCOVER_MAP` for listen, offers only NFC-A
  passive listen when a UID is set (NFC-F would bring the chip up as NFC-DEP),
  and signals `nfc_tm_activated` for T2T as well as NFC-DEP.

### What is proven on the device

- **The chip enters listen and a reader activates it.** With NFC-A + NFC-F
  offered (no UID), presenting a phone activates the controller -- dmesg shows
  `rf_intf_activated_ntf`, `data_exch_rf_tech_and_mode 0x82`, an ATR_REQ. So the
  hardware can be a listen target; this is not a hardware wall.
- **The S3NRN4V accepts a fixed 7-byte NFCID1.** `CORE_SET_CONFIG` with
  `LA_NFCID1` = the card's UID returns `status 0x0`. That was the open hardware
  question -- the chip does not force a random UID. All nine listen SET_CONFIGs
  (BIT_FRAME_SDD, PLATFORM_CONFIG, NFCID1, SEL_INFO, ...) return `status 0x0`.
- Target card measured for emulation: MIFARE Classic 1K, **ATQA 0x0044, SAK
  0x08, UID 04:35:3d:6a:f7:54:80** (7 bytes, NXP 0x04 prefix).

### The open point (narrowed down)

The chip **emulates in listen mode -- confirmed with a Flipper Zero** -- but it
only comes up over **NFC-F**, never as an NFC-A tag. What was measured, all with
the clean NFC-A config (patch 0111: `LA_BIT_FRAME_SDD`, `LA_PLATFORM_CONFIG`,
`LA_NFCID1`, `LA_SEL_INFO`, no NFC-DEP `LF_*` parameters):

- **NFC-A + NFC-F offered:** the Flipper detects the phone -- **as a FeliCa
  (Type 3 / NFC-F)**. So the hardware does answer a reader in listen; it just
  answers on F, not A.
- **NFC-A only offered:** nothing. No reader (Flipper or NFC Tools) sees it, and
  there is no `rf_intf_activated_ntf`. The chip accepts every SET_CONFIG and the
  RF_DISCOVER (`status 0x0` on all), then sits silent -- it does **not** answer
  the NFC-A anticollision as a tag.
- Removing the NFC-DEP `LF_*` parameters (the clean config) does not stop the F
  activation; the F path still works and shows up as FeliCa.

So the S3NRN4V, driven by the plain NCI listen sequence, **does NFC-F listen but
not NFC-A tag listen**. The hardware is capable (Android emulated on it); what
was missing turned out to be the whole userspace card-emulation stack, not a
single NCI command -- the LMRT below was implemented and tested and did not
unblock it. See "Conclusion" at the end of this section for the full result.

### The LMRT -- implemented (patch 0111)

`RF_SET_LISTEN_MODE_ROUTING` is now built and sent. Patch 0111 adds the opcode
(`GID 0x1 OID 0x1`), the command/response structs and the RSP handler to the
mainline NCI core (mainline had none of it -- it only read
`max_routing_table_size` from `CORE_INIT_RSP` and never programmed a table).
When emulating, `nci_start_poll` sends the table just before `RF_DISCOVER`,
routing three entries to the host NFCEE (id 0x00) for all power states (0x3f):

- a **technology** entry for NFC-A (tech 0x00),
- a **protocol** entry for T2T (0x02), and
- a **protocol** entry for MIFARE Classic (the Samsung/NXP proprietary 0x80,
  from index 5 of the `NFA_PROPRIETARY_CFG` remap -- MIFARE Classic is presented
  under 0x80, not plain T2T).

The MIFARE 0x80 protocol is deliberately **not** added to `RF_DISCOVER_MAP`: the
controller rejects that command with `status 0x1` if 0x80 is listed there, so
only T2T is mapped to the FRAME interface, while MIFARE is routed via the LMRT.

**Measured on the phone -- every command is accepted, but it still does not
activate.** With `nci` dynamic debug on and a MIFARE Classic 1K target
(`rhodep-nfc listen 04:35:3d:6a:f7:54:80 0x08`):

```
NCI TX: GID=0x1, OID=0x0, plen=19   RF_DISCOVER_MAP           -> status 0x0
NCI TX: GID=0x1, OID=0x1, plen=17   RF_SET_LISTEN_MODE_ROUTING -> status 0x0  (3 entries, incl. MIFARE 0x80)
NCI TX: GID=0x1, OID=0x3, plen=3    RF_DISCOVER                -> status 0x0
... then 30 s of total silence while a Flipper Zero (read mode) is on the
    antenna: no nci_recv_frame, no RF_INTF_ACTIVATED_NTF ...
NCI TX: GID=0x1, OID=0x6            RF_DEACTIVATE (timeout)    -> status 0x0
```

The Flipper detects **nothing**. This was tested with NFC-A only, with
NFC-A + NFC-F, with and without the MIFARE 0x80 route, and with the corrected
`LA_PLATFORM_CONFIG` cascade byte -- the result is the same every time: the chip
accepts the full standard-NCI listen setup with `status 0x0` and then never
answers the NFC-A anticollision. (With NFC-F offered it comes up as FeliCa, so
the RF front-end and the listen path themselves work -- see above.)

### Conclusion: NFC-A tag listen is not reachable from standard NCI on this chip

The LMRT was necessary to even attempt it, but it is **not sufficient**, and
disassembly of the vendor HAL (`nfc_nci_sec.so`, extracted from the LineageOS
`vendor.img`) shows there is no missing magic command to add:

- The vendor `hwreg`/`swreg` blobs are **RF analog register images** (they end in
  the magic `"DEF\0"`), not NCI config -- patch 0104 already loads them via the
  proprietary `2F 2A` dual-rfreg opcode, which is why NFC-F listen and reader
  mode work. They contain no `LA_*` listen TLVs.
- The HAL's `nfc_hal_pre_discover` sends nothing; `hal_nci_send_clearLmrt` only
  builds a *clear* (`21 01 02 00 00`), identical in format to ours; and every
  proprietary opcode (`2F 25/26/27/28/2A`) is rfreg / clock / trace during init,
  never a "card emulation enable".
- In Android the entire NFC-A card-emulation state machine lives in
  **`libnfc-nci.so` in userspace** (the system partition), driven by the AOSP
  NFA stack and HCE app routing -- the kernel `sec-nfc` driver is a pure
  transport. Linux mainline's `net/nfc/` has never implemented host card
  emulation for T2T/ISO-DEP; it only wires up NFC-DEP (P2P) listen. So there is
  no in-kernel path that makes this controller arm NFC-A tag listen, and no
  single vendor command that would.

What is upstreamable and kept in 0111: the fixed-NFCID1 NFC-A listen config, the
T2T FRAME mapping, and the `RF_SET_LISTEN_MODE_ROUTING` implementation the
mainline core lacked. What is **not** achievable without porting a full
userspace NCI/HCE stack (à la libnfc-nci) talking raw NCI over the driver: the
actual NFC-A tag activation and the tag data path. That is the real remaining
gap, and it is a large piece of new software, not a missing register or command.

### The plan by layers (the goal is extensible protocols)

In Android the .apk did the tag protocol; the chip only did the RF/listen. The
same split is the target here: the kernel does listen + a generic data path,
userspace implements each tag protocol as a handler. So a new protocol later is
a userspace handler, not a kernel change.

- **Layer 1 -- NFC-A listen + fixed UID.** For UID-only readers (most building
  doors): the UID is delivered in the hardware anticollision, the reader takes
  it and sends nothing more, so no data path is needed. This is where 0110+0111
  land -- once the NFC-A tag activation above is solved, a UID-only door should
  work with just `listen <uid> <sak>`.
- **Layer 2 -- Type 2 READ.** Readers that send `30 <blk>`. Needs a listen data
  path kernel<->userspace: route `nfc_tm_data_received` (today hardcoded to
  LLCP, `net/nfc/core.c`) to a target `PF_NFC`/`SOCK_SEQPACKET`, and a
  `Type2Handler` in userspace. This is the one kernel change that unlocks every
  higher layer.
- **Layer 3 -- MIFARE Classic (Crypto1).** Only if the door authenticates a
  sector. Needs the sector key (crack offline from captured nonces with
  mfkey32/mfoc), and the RF timing of Crypto1 is tight enough that it likely
  needs a hardware CE mode in the firmware -- high risk.
- **Layer 4 -- ISO-DEP / Type 4 (HCE).** APDUs, like Android HCE. Reuses the
  Layer 2 data path; `Type4Handler` dispatches by AID.

### First thing to do when resuming

Capture, with `rhodep-nfc raw`, what a reader actually sends when it activates a
real NFC-A tag, and confirm whether the target door is UID-only (does it stop at
the anticollision, or send `30 00` / `60 xx`?). That decides whether Layer 1 is
enough or Layer 2 is needed.
