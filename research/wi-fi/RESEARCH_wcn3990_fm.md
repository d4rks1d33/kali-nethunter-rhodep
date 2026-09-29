# WCN3990 FM Receiver — Linux Software Support Research

Date: 2026-09-17
Device: Motorola Moto G82 5G (rhodep), SoC SM6375 (Snapdragon 695),
combo Qualcomm WCN3990 (WiFi + BT + FM).
Scope: research-only (no hardware step), evaluate feasibility of enabling FM
and how far the tuner frequency can be pushed by software.

---

## 0. TL;DR / Executive summary

1. **The FM block on WCN3990 is NOT a separate tuner IC**. It is a soft-FM
   function living inside the same firmware image that runs BT (the ROM/patch
   `qca/crbtfw*.tlv` + NVM `qca/crnv*.bin` you already flash for Bluetooth).
   FM control is done with **Qualcomm vendor HCI commands**, and FM audio comes
   out on a **separate SLIMbus/SoundWire PCM link**, not over HCI.

2. **Two data planes** (this is the key architectural fact):
   - **Control plane (tune / band / seek / RDS)** → HCI command/event packets
     (`0x11` cmd / `0x14` evt) carrying FM opcodes in OGF `0x13/0x14/0x15`.
     On old chips these went over an SMD channel `APPS_FM`; on WCN3990 they go
     over the **same physical UART / HCI transport as Bluetooth** through a
     small FM-HCI shim.
   - **Audio plane (the actual demodulated stereo audio)** → SLIMbus TX ports
     `CHRK_SB_PGD_PORT_TX1_FM` / `TX2_FM` (`btfm_slim_wcn3990.c`). Nothing to do
     with HCI. You need this second path to *hear* anything.

3. **Nothing of this exists in mainline Linux.** Confirmed against
   `torvalds/linux` v6.12 `drivers/media/radio/{Makefile,Kconfig}` — there is
   **no `radio-iris`, no `qcom`, no `wcn` FM entry** whatsoever. The mainline
   `ath10k_snoc` + `hci_qca`/`btqca` stack you're running has **zero FM code**.

4. **Your current rhodep kernel can't even host it**: the config has
   `# CONFIG_MEDIA_RADIO_SUPPORT is not set` (line 5569 of
   `config-motorola-rhodep.aarch64`), so the whole V4L2 radio subsystem is
   compiled out. (`CONFIG_VIDEO_QCOM_IRIS=m` on line 5777 is the **camera**
   codec "Iris", a completely unrelated block — do not be misled by the name.)

5. **Good news for the "how far can I push the tuner" question**: in the
   `radio-iris` driver the tune path does **no frequency clamping in the
   kernel**. `iris_vidioc_s_frequency()` → `iris_set_freq()` →
   `hci_fm_tune_station()` sends a **raw `__u32` kHz value straight to the
   firmware**. Band low/high limits are only *config* fields
   (`recv_conf.band_low_limit`/`band_high_limit`) that the host writes; whether
   they are enforced is a **firmware** decision, not a driver one. So the
   ceiling is set by the WCN3990 FM DSP / PLL, and the only way to find it is to
   actually poke frequencies at it.

---

## 1. The `radio-iris` driver — where it lives, structure, transport

### 1.1 Canonical source locations

`radio-iris` is Qualcomm/CAF code, never upstreamed. Find it in any msm Android
kernel tree, e.g. (verified live):

- `drivers/media/radio/radio-iris.c`  — the V4L2 + FM-HCI protocol engine
  (~5646 lines). Header banner: `"QTI FM Radio Transceiver"`,
  `DRIVER_NAME "radio-iris"`.
- `drivers/media/radio/radio-iris-transport.c` — the *transport* below it.
- `include/media/radio-iris.h` — `struct radio_hci_dev` (looks exactly like
  `hci_core.h` from BlueZ; this driver is a fork of the BT HCI core).
- `include/uapi/media/radio-iris.h` — all opcodes, structs, **band limits**,
  region enums.
- `include/uapi/media/radio-iris-commands.h` — the `V4L2_CID_PRIVATE_IRIS_*`
  control IDs.

Reference trees (any of these has the files at those paths):
- `github.com/LineageOS/android_kernel_google_msm-4.9` (branch `lineage-19.1`)
  — used as the primary source for this report.
- CAF/CodeLinaro FM userspace HAL:
  `git.codelinaro.org/clo/la/platform/vendor/qcom-opensource/fm`
  (243 commits, branch `caf_migration/master`) — this is the **userspace**
  `libfm-hci` / `fmhal` side (`fm_hci/fm_hci.c` etc.), not the kernel driver.
- LineageOS mirror of that HAL:
  `github.com/LineageOS/android_hardware_qcom_fm`.

### 1.2 How it talks to the chip (control plane)

`radio-iris.c` is literally a mini BlueZ. It builds `struct sk_buff`s with a
3-byte HCI-style command header and hands them to a transport `->send()`:

```
radio_hci_send_cmd(hdev, opcode, plen, param)
   opcode = hci_opcode_pack(OGF, OCF)          // 16-bit, same packing as BT HCI
   hdr->opcode = cpu_to_le16(opcode); hdr->plen = plen;
   hci_send_frame(skb) -> hdev->send(skb)      // transport-specific
```

The transport implements `hdev->send`. Two historical transports exist:

- **`radio-iris-transport.c` (SMD variant)** — for WCNSS/riva (older chips,
  e.g. pronto). It opens an SMD channel:
  ```
  smd_named_open_on_edge("APPS_FM", SMD_APPS_WCNSS, ...);
  ```
  i.e. a dedicated shared-memory pipe to the WCNSS/riva coprocessor.
  Firmware = the WCNSS image (`wcnss.mdt`/riva). This is the classic
  "FM over SMD to riva" design.

- **WCN3990 / UART transport** — WCN3990 is not a coprocessor on SMD; it's a
  UART-attached BT chip. Here FM-HCI is multiplexed onto the **same UART/HCI
  link as Bluetooth**. On Android this is done in **userspace** by the
  `fmhal`/`libfm-hci` service (see §4) rather than a dedicated kernel SMD
  transport. The kernel-side `radio-iris.c` protocol engine is identical; only
  the `->send`/`->recv` plumbing under it changes.

So: **FM control = HCI vendor commands. On WCN3990 those HCI packets travel on
the very same hci0/UART that `btqca`/`hci_uart` already drive for Bluetooth.**

### 1.3 What firmware it needs

No separate FM firmware file. The FM DSP is part of the BT firmware you already
install for `btqca`:
- `qca/crbtfw%02x.tlv`  (ROM patch, `%02x` = chip rom version)
- `qca/crnv%02x.bin`    (NVM/calibration)

These are exactly the files referenced by patch
`0009-arm64-dts-qcom-rhodep-enable-wifi-and-bt.patch` in your tree
(`btqca builds the firmware names itself`). FM lights up as a sub-function once
BT firmware is loaded and the chip is powered — no extra blob.

### 1.4 Relationship to Bluetooth

- **Shared firmware**: FM and BT are the same downloaded image.
- **Shared control transport**: on WCN3990 both are HCI over the same UART.
- **Independent audio transport**: FM audio is a SLIMbus TX stream
  (`btfm_slim_wcn3990.c`), BT SCO/A2DP are other SLIMbus ports on the same PGD.
- Consequence: you **cannot** enable FM without the BT/HCI link up. The chip
  must be powered and firmware-loaded (which for you means `hci_uart`+`btqca`
  must have brought hci0 up), then FM enable is a vendor HCI command sequence.

---

## 2. FM transport architecture on WCN3990 specifically

### 2.1 Control: HCI vendor commands (same link as BT)

Verified from `btfm_slim_wcn3990.c` + `fm_hci.c` + the iris opcode header:
- FM **control** is HCI. The FM-HCI packet types are:
  - `RADIO_HCI_COMMAND_PKT = 0x11`
  - `RADIO_HCI_EVENT_PKT   = 0x14`
  (These are Qualcomm's FM-specific HCI packet indicators, distinct from BT's
  `0x01`/`0x04`.) They ride the same UART as BT HCI; the QCA controller
  demuxes by packet-type byte.

### 2.2 Audio: SLIMbus (NOT SoundWire on this chip)

From `btfm_slim_wcn3990.c` (`struct btfmslim_ch wcn3990_txport[]`):
```
{.id=BTFM_FM_SLIM_TX, .name="FM_Tx1", .port=CHRK_SB_PGD_PORT_TX1_FM},
{.id=BTFM_FM_SLIM_TX, .name="FM_Tx2", .port=CHRK_SB_PGD_PORT_TX2_FM},
```
FM audio = two SLIMbus TX channels (L/R) off the WCN3990 SLIMbus PGD
(Ported Generic Device). `btfm_slim.c` does `slim_define_ch` / `slim_connect_src`
/ `slim_control_ch(... SLIM_CH_ACTIVATE ...)` to route them into LPASS. The
"CHRK" prefix = "Cherokee", QCA's internal name for the WCN3990 SLIMbus slave.

- **WCN3990 uses SLIMbus**, not SoundWire, for FM/BT audio. (SoundWire shows up
  on later WCN6xxx/lpass-macro designs.) Your rhodep config indeed enables
  SLIMbus: `CONFIG_SLIMBUS=m`, `CONFIG_SLIM_QCOM_NGD_CTRL=m`
  (lines 10969-10970), plus `CONFIG_QCOM_WCNSS_CTRL=m` (9603).

- Note: to actually *hear* FM you need the SLIMbus FM TX ports wired into an
  ASoC path (LPASS/soundwire-macro → your `rhodep-sndcard`). Patches 32-36 in
  your tree already stand up APR audio / LPASS macros / soundwire — but they do
  **not** define an FM SLIMbus DAI, so today there is no audio route even if
  control worked.

### 2.3 "fm_slim" / "qca6390 fm" search notes

- "fm_slim" → the `btfm_slim*` family above (audio only).
- "qca6390 fm" / "wcn6750 fm" → later chips move the whole thing to a
  `fm_slim_ch`/`btfm_swr` SoundWire variant + a `fm_iris`-like userspace, but
  the *control-is-HCI, audio-is-serial-audio-bus* split is unchanged.
- "FM over UART" is accurate for WCN3990 **control**; "FM over SLIMbus" is
  accurate for WCN3990 **audio**. Both statements are simultaneously true
  because they are different planes.

---

## 3. Mainline / community FM WCN support status

### 3.1 Mainline: none

- `torvalds/linux` v6.12 `drivers/media/radio/Makefile` — no `radio-iris`,
  no `qcom`. Every entry is a legacy ISA/USB/I2C tuner (tea575x, si470x,
  si4713, wl1273, shark, …). **No Qualcomm FM driver has ever been merged.**
- Same file's `Kconfig` — no `RADIO_IRIS`, no `RADIO_QCOM`, nothing WCN.
- `hci_qca`/`btqca` in mainline `drivers/bluetooth/` handle only BT setup
  (firmware download, baud, ROM version). They expose **no** FM function and
  no vendor-command passthrough beyond what `hci_dev` normally allows.

### 3.2 postmarketOS / this workspace

- Your rhodep patch series (`postmarketos/linux-motorola-rhodep`, patches
  0001-0122) covers display, GPU, IPA, audio (APR/LPASS/soundwire), NFC,
  charger, WiFi/BT enablement, and ath10k monitor/injection — **but contains
  no FM patch at all.** No `radio-iris`, no FM DT node, no
  `CONFIG_MEDIA_RADIO_SUPPORT`.
- pmOS wiki/gitlab: no rhodep FM work. In general pmOS has **no** device with
  WCN3990 FM working via V4L2; the whole ecosystem treats WCN FM as unsupported.

### 3.3 linux-media / linux-wireless / Linaro

- No radio-iris submission exists on linux-media. Qualcomm never posted it; the
  driver's BlueZ-fork lineage (GPL'd but CAF-only) means it was never cleaned up
  for V4L2 upstream review.
- Konrad Dybcio / Linaro qcom work (msm mainlining) covers SoC, clocks,
  interconnect, WCN WiFi/BT (`ath10k_snoc`, `hci_qca`) — **no FM**. There is no
  Linaro patchset porting WCN3990/6390 FM to V4L2.
- The closest analogues that *were* upstreamed are unrelated chips:
  `radio-wl1273` / `wl128x` (TI) and `si470x`/`si4713` (Silabs). These prove the
  V4L2 target API but share no code with iris.

### 3.4 Conclusion

Nobody has ported WCN3990/6390 FM to mainline V4L2. To use FM on rhodep-mainline
you must either (a) forward-port `radio-iris` + a WCN3990 UART transport, or
(b) drive the chip directly with raw vendor HCI from userspace (see §4/§6).

---

## 4. Control interface: can we drive FM with raw HCI vendor commands?

**Yes — this is the most promising path, because the control protocol is fully
documented in the open-source headers.** The Android userspace confirms the
model: `fm_hci.c` (`LineageOS/android_vendor_qcom_opensource_fm-commonsys`)
opens a serial FD via the BT vendor lib and just `write()`s FM-HCI command
frames and `read()`s events — no kernel V4L2 driver is involved on the wire;
that layer only exists to present `/dev/radioN` to apps.

### 4.1 FM-HCI packet framing

From `uapi/media/radio-iris.h`:
```
struct radio_hci_command_hdr { __le16 opcode; __u8 plen; }  // 3 bytes
Packet indicator byte 0x11 (RADIO_HCI_COMMAND_PKT) precedes it on the wire.
opcode = (OGF << 10) | (OCF & 0x3ff)
```
Events come back with indicator `0x14`, `struct radio_hci_event_hdr {evt; plen;}`,
and the important ones are `HCI_EV_CMD_COMPLETE=0x0F`, `HCI_EV_CMD_STATUS=0x10`,
`HCI_EV_TUNE_COMPLETE=0x11`, `HCI_EV_TUNE_STATUS=0x01`.

### 4.2 Opcode groups (OGF)

```
HCI_OGF_FM_RECV_CTRL_CMD_REQ         0x0013   // receiver control
HCI_OGF_FM_TRANS_CTRL_CMD_REQ        0x0014   // transmitter control
HCI_OGF_FM_COMMON_CTRL_CMD_REQ       0x0015   // tune/reset/calibration/spur
HCI_OGF_FM_STATUS_PARAMETERS_CMD_REQ 0x0016
HCI_OGF_FM_TEST_CMD_REQ              0x0017
HCI_OGF_FM_DIAGNOSTIC_CMD_REQ        0x003F   // peek/poke/ssbi (register access!)
```

### 4.3 The commands that matter for "tune and push the band"

RECV control (OGF 0x13):
```
0x0001 FM_ENABLE_RECV_REQ      (no params)   -> turn on receiver
0x0002 FM_DISABLE_RECV_REQ     (no params)
0x0004 FM_SET_RECV_CONF_REQ    struct hci_fm_recv_conf_req  <-- BAND LIMITS live here
0x0007 FM_SET_ANTENNA          u8
0x000E FM_SEARCH_STATIONS      struct hci_fm_search_station_req
0x0011 FM_CANCEL_SEARCH
```
COMMON control (OGF 0x15):
```
0x0001 FM_TUNE_STATION_REQ     __u32 freq_khz   <-- RAW kHz, this is the tuner poke
0x0004 FM_RESET
0x0005 FM_GET_FEATURE_LIST
0x0006 FM_DO_CALIBRATION
0x0008 FM_SET_SPUR_TABLE
```
DIAGNOSTIC (OGF 0x3F) — direct DSP/analog access:
```
0x0002 FM_PEEK_DATA            struct hci_fm_riva_data
0x0003 FM_POKE_DATA            struct hci_fm_riva_poke
0x0004 FM_SSBI_PEEK_REG        struct hci_fm_ssbi_peek
0x0005 FM_SSBI_POKE_REG        struct hci_fm_ssbi_req   <-- poke analog tuner regs
```

### 4.4 The tune command in detail

```c
static int hci_fm_tune_station_req(hdev, param) {
    __u32 tune_freq = param;                       // kHz, e.g. 100000 = 100.0 MHz
    opcode = hci_opcode_pack(0x0015, 0x0001);      // OGF=COMMON, OCF=TUNE
    return radio_hci_send_cmd(hdev, opcode, sizeof(tune_freq), &tune_freq);
}
```
On the wire (little-endian) to tune 100.0 MHz (100000 kHz = 0x000186A0):
```
11  05 54   04   A0 86 01 00
^   ^        ^    ^-- __u32 freq LE (0x000186A0)
|   |        └── plen = 4
|   └── opcode LE: (0x15<<10)|0x01 = 0x5401  -> bytes 01 54
└── FM-HCI command packet indicator 0x11
```
(byte order shown logically; opcode is `cpu_to_le16(0x5401)` → `01 54`.)

### 4.5 Can BlueZ / hcitool send these?

Caveats:
- These are **FM-HCI** packets (`0x11`/`0x14`), not standard BT HCI
  (`0x01`/`0x04`). Standard `hci0` on mainline expects BT packet types; a raw
  `hcitool cmd` uses the BT command indicator `0x01`. So you can **not** just
  `hcitool cmd 0x15 0x0001 …` and expect the QCA FW to treat it as FM — the
  packet-type byte is wrong.
- On Android the `0x11`/`0x14` framing is injected by the **vendor lib**
  (`libbt-vendor.so`) which owns the UART directly, bypassing the kernel BT
  stack. That's why `fm_hci.c` `write()`s to a raw serial FD returned by
  `BT_VND_OP_FM_USERIAL_OPEN`, not to an `hci` socket.
- **Implication for mainline:** the clean way is a small kernel shim (a
  `radio-iris` transport) that grabs the `hci_uart`/serdev and writes `0x11`
  frames, OR a `hci_vendor_pkt`-style hook. A userspace-only hack would need to
  take the UART away from `hci_qca` (mutually exclusive with BT being up).
  See the plan in §6 for the realistic route.

---

## 5. Software-configurable tuning range (how far can you push it?)

### 5.1 Where band limits live

`struct hci_fm_recv_conf_req` (`uapi/media/radio-iris.h`):
```c
struct hci_fm_recv_conf_req {
    __u8  emphasis;        // FM_RX_EMP75=0 / EMP50=1
    __u8  ch_spacing;      // 0=200kHz 1=100kHz 2=50kHz
    __u8  rds_std;         // RBDS/RDS
    __u8  hlsi;            // high/low side injection: 0 auto,1 low,2 high
    __u32 band_low_limit;  // kHz  <-- YOU set this
    __u32 band_high_limit; // kHz  <-- YOU set this
} __packed;
```
This is sent via `FM_SET_RECV_CONF_REQ` (OGF 0x13, OCF 0x04). It is the ONLY
place the "band" is defined for the receiver.

### 5.2 Region presets (defaults)

```c
enum iris_region_t { IRIS_REGION_US, IRIS_REGION_EU, IRIS_REGION_JAPAN,
                     IRIS_REGION_JAPAN_WIDE, IRIS_REGION_OTHER };

#define REGION_US_EU_BAND_LOW           87500
#define REGION_US_EU_BAND_HIGH         108000
#define REGION_JAPAN_STANDARD_BAND_LOW  76000
#define REGION_JAPAN_STANDARD_BAND_HIGH 90000
#define REGION_JAPAN_WIDE_BAND_LOW      90000
#define REGION_JAPAN_WIDE_BAND_HIGH    108000
```
So the driver already knows how to go **down to 76.0 MHz** (Japan) and up to
108.0 MHz out of the box. `IRIS_REGION_OTHER` lets the host set arbitrary
low/high via `recv_conf` before enable.

### 5.3 Does anything clamp the tune value?

**Critically: the kernel driver does NOT clamp.** Full path:
```
iris_vidioc_s_frequency(): f = freq->frequency / TUNE_PARAM;   // scale only
                           iris_set_freq(radio, f);
iris_set_freq():           hci_fm_tune_station(&freq, hdev);   // no min/max check
hci_fm_tune_station_req():  sends raw __u32 kHz to firmware
```
There is **no `if (f < band_low || f > band_high) return -EINVAL;`** anywhere in
`radio-iris.c`. `band_low_limit`/`band_high_limit` are used only to:
- report `rangelow`/`rangehigh` in `VIDIOC_G_TUNER` (`* TUNE_PARAM`), and
- compute relative-frequency offsets for search-list events
  (`rel_freq = abs_freq - recv_conf.band_low_limit`).

Therefore **the enforcement, if any, is entirely in the WCN3990 FM firmware /
PLL**. Whether the firmware:
- (a) silently clamps to its internal min/max,
- (b) returns an error status in `HCI_EV_CMD_STATUS`,
- (c) accepts and actually tunes the synthesizer,
…can only be discovered empirically by poking values and reading the
`HCI_EV_TUNE_STATUS` (`station_freq`, `rssi`, `sinr`, `serv_avble`).

### 5.4 Extra low-level control if firmware clamps

If `FM_TUNE_STATION_REQ` clamps, you still have:
- `FM_SSBI_POKE_REG` (OGF 0x3F, OCF 0x05): write the analog tuner's SSBI
  registers directly (`struct hci_fm_ssbi_req {__u16 start_addr; __u8 data;}`,
  addr range gated in the driver to `0x280..0x37F`).
- `FM_POKE_DATA` (OGF 0x3F, OCF 0x03): poke RIVA/DSP memory
  (`start_addr` gated `0x3180000..0x31E0004` in the driver's V4L2 control, but
  the HCI command itself takes an arbitrary `__u32 start_addr`).
These are the "escape hatches" to reprogram the synthesizer below/above the
firmware's nominal FM band — subject to what the analog front-end PLL can lock.
This is exactly the kind of poke you'd use to test pushing beyond 108 MHz or
below 76 MHz. **No guarantee the RF front-end (SAW filter/LNA) passes anything
useful outside ~76-108 MHz**, but the digital path will attempt the tune.

### 5.5 Channel spacing / other tunables

- `ch_spacing`: 200/100/50 kHz (`enum channel_space_type`).
- `hlsi`: force high/low-side LO injection — can matter at band edges.
- `FM_SET_SPUR_TABLE`: spur-cancellation table; `COMPUTE_SPUR(val) =
  ((val-76000)/50)` reveals the firmware's internal frequency grid is
  referenced to **76000 kHz base, 50 kHz step** — another hint the DSP natively
  reasons from 76 MHz upward.

---

## 6. Concrete next-step software plan (rhodep, mainline)

Goal: get *any* two-way FM-HCI conversation with the WCN3990, read
`TUNE_STATUS`, then sweep frequency to find the real min/max the firmware/PLL
will accept.

### Phase A — reconnaissance (no code, uses existing BT link)
1. Confirm BT is up: `hciconfig -a` / `dmesg | grep -i qca`. You need
   `qca/crbtfw*.tlv` + `crnv*.bin` loaded (they already are for BT). FM shares
   this firmware.
2. Dump the BT firmware ROM version from dmesg (`btqca` prints it). Note it —
   FM feature availability can vary by ROM patch level.
3. Check whether the QCA controller advertises FM: after BT init, some QCA FW
   accept a vendor "FM enable" only when a control bit is set at download time.
   Grep the vendor NVM (`crnv*.bin`) offsets is out of scope, but record the
   exact filenames/versions you're on.

### Phase B — get an FM-HCI transport (pick ONE)
- **B1 (cleanest): forward-port `radio-iris` + a serdev transport.**
  - Pull `radio-iris.c`, `include/media/radio-iris.h`,
    `include/uapi/media/radio-iris*.h` from `LineageOS/android_kernel_google_msm-4.9`.
  - Replace the SMD transport with a tiny serdev/`hci_uart`-cooperating shim
    that emits `0x11`-prefixed frames and parses `0x14` events. The hard part is
    **UART arbitration with `hci_qca`** — FM and BT share the UART. Two options:
    - piggy-back: register FM-HCI as another packet type consumer inside the
      existing `hci_qca` serdev instance (send `0x11`, receive `0x14`), i.e. add
      an FM demux in the QCA line discipline; or
    - a `hci_vendor`/`hdev`-level hook that lets you inject vendor packets and
      receive vendor events, feeding them to a `radio-iris` `hdev->send`.
  - Enable `CONFIG_MEDIA_RADIO_SUPPORT`, `CONFIG_RADIO_ADAPTERS`, and the ported
    `CONFIG_RADIO_IRIS`. You get `/dev/radio0` + can use `v4l2-ctl`.
- **B2 (fastest to first light): userspace over the raw UART.**
  - Temporarily unbind `hci_qca` from the serdev (or boot with BT disabled),
    open the tty at the QCA baud, do the QCA BT firmware download yourself
    (or let btqca do it, then steal the port), and `write()` FM-HCI frames à la
    `fm_hci.c`. This is a throwaway probe rig, not a daily driver, and it
    **takes BT offline** while active.
  - Minimal frame builder (Python/pyserial): implement `pack(ogf,ocf,plen,payload)`
    with indicator `0x11`, send ENABLE_RECV → SET_RECV_CONF(region OTHER,
    low=76000, high=108000) → TUNE(freq) → read `0x14` events.

### Phase C — bring-up sequence (the command order that works)
Mirror what `radio-iris.c` does on RX open:
1. `FM_ENABLE_RECV_REQ`   (OGF 0x13/OCF 0x01)   → wait `CMD_COMPLETE`/enable evt.
2. `FM_SET_RECV_CONF_REQ` (0x13/0x04) with `emphasis=0, ch_spacing=0, rds_std,
   hlsi=0, band_low_limit=76000, band_high_limit=108000`.
3. `FM_SET_ANTENNA`       (0x13/0x07) = 0 (internal) or 1 (headset) — WCN3990
   FM antenna is usually the headset cable; note this affects sensitivity.
4. `FM_TUNE_STATION_REQ`  (0x15/0x01) = target kHz.
5. Read `HCI_EV_TUNE_STATUS` (0x01): `station_freq, rssi, sinr, serv_avble`.
   `serv_avble` + reasonable rssi/sinr = real lock.

### Phase D — the actual experiment (how far can it go)
1. Set `band_low_limit=1000, band_high_limit=200000` (absurdly wide) via
   SET_RECV_CONF to remove the host-side hint entirely.
2. Sweep `FM_TUNE_STATION_REQ` from low to high in your chosen step; for each,
   read `TUNE_STATUS` and log `station_freq` reported back vs requested:
   - if `station_freq` == requested → firmware accepted/locked there.
   - if it snaps to a boundary (e.g. always 76000 or 108000) → firmware clamps;
     go to Phase E.
   - if `CMD_STATUS` returns non-zero → firmware rejected; note the error code
     (`radio_hci_err()` maps: 0x12=EINVAL, 0x11=EOPNOTSUPP, etc.).
3. Record the true min and max where you still get `serv_avble`/lock.

### Phase E — bypass firmware clamp (if needed)
1. Use `FM_SSBI_POKE_REG` (0x3F/0x05) to write the tuner PLL/synth registers
   directly (addr 0x280-0x37F). You'll need to reverse the register map from a
   `FM_SSBI_PEEK_REG` sweep first (read current values while tuned to a known
   station, then correlate).
2. Or `FM_POKE_DATA` (0x3F/0x03) into the FM DSP frequency variable.
3. Reality check: even if the digital synth locks, the **RF front end** (SAW
   filter, matching network, LNA on the rhodep board) is tuned for 76-108 MHz.
   Expect rapidly falling sensitivity outside that; "pushing the tuner" is
   mostly a digital/PLL exercise, the analog path is the true limiter.

### Phase F — audio (only if you want to actually hear it)
- Route the two SLIMbus FM TX ports (`CHRK_SB_PGD_PORT_TX1_FM/TX2_FM`) into an
  ASoC path. This needs an FM SLIMbus DAI added to your `rhodep-sndcard` +
  LPASS routing (your patches 32-36 set up LPASS/soundwire but define no FM
  DAI). For the *frequency-limit experiment* you don't need audio at all —
  `TUNE_STATUS` rssi/sinr already tells you if the tuner locked.

---

## 7. Key file/URL reference

Kernel driver (CAF, not mainline):
- `drivers/media/radio/radio-iris.c`  — protocol engine (V4L2 + FM-HCI)
- `drivers/media/radio/radio-iris-transport.c` — SMD transport (riva/WCNSS)
- `include/media/radio-iris.h`         — `struct radio_hci_dev`, timeouts
- `include/uapi/media/radio-iris.h`    — opcodes, band limits, region enums
  (all quoted above)
Source tree used: `github.com/LineageOS/android_kernel_google_msm-4.9`@`lineage-19.1`

FM audio (SLIMbus) driver:
- `drivers/bluetooth/btfm_slim.c`
- `drivers/bluetooth/btfm_slim_wcn3990.c` — WCN3990 FM TX ports (Cherokee)

Userspace HAL (control plane model, proves raw-HCI approach):
- `git.codelinaro.org/clo/la/platform/vendor/qcom-opensource/fm`@`caf_migration/master`
- `github.com/LineageOS/android_vendor_qcom_opensource_fm-commonsys`
  (`fm_hci/fm_hci.c` — `write()` FM-HCI frames to a raw serial FD)

Mainline (proof of absence):
- `github.com/torvalds/linux`@`v6.12` `drivers/media/radio/Makefile` + `Kconfig`
  — no iris/qcom/wcn FM entry.

This device (rhodep) config confirmations:
- `nethunter-rhodep-repo/postmarketos/linux-motorola-rhodep/config-motorola-rhodep.aarch64`
  - L5569 `# CONFIG_MEDIA_RADIO_SUPPORT is not set`  (V4L radio compiled out)
  - L5777 `CONFIG_VIDEO_QCOM_IRIS=m`  (camera codec, NOT FM — do not confuse)
  - L10969-10970 `CONFIG_SLIMBUS=m`, `CONFIG_SLIM_QCOM_NGD_CTRL=m`
  - L9603 `CONFIG_QCOM_WCNSS_CTRL=m`
- `.../0009-arm64-dts-qcom-rhodep-enable-wifi-and-bt.patch` — WCN3990 BT on
  UART1, firmware `qca/crbtfw*.tlv`+`crnv*.bin` (shared with FM).

---

## 8. Bottom line for the user's objective

- **Feasible?** Control side yes, via vendor FM-HCI commands that are fully
  open-sourced in the iris UAPI header. Audio side needs extra ASoC work but is
  not required to test tuning.
- **Fastest first contact:** Phase B2 — steal the QCA UART, replay the
  `fm_hci.c` enable→conf→tune sequence from a Python script, read `TUNE_STATUS`.
- **How far can you push the frequency?** The kernel imposes NO limit; the tune
  command is a raw `__u32` kHz to firmware. The real ceiling/floor is whatever
  the WCN3990 FM DSP/PLL + rhodep RF front-end will lock. The firmware's own
  math is referenced from 76000 kHz / 50 kHz steps, and region presets already
  span 76-108 MHz. To probe beyond that, widen `band_*_limit`, sweep TUNE, and
  if clamped, drop to `SSBI_POKE_REG`/`POKE_DATA` — accepting that the analog
  front-end is the ultimate limiter.
