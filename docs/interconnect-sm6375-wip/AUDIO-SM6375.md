# Audio on SM6375 (rhodep) — bring-up notes

Status: **the sound card exists**, headset jack detection works, the WCD9370
enumerates on both soundwire buses. Starting a stream, in either direction,
still resets the SoC about half a second later: the ADSP faults and its watchdog
bite takes the chip down. That is the open item (§4), and §7 says how to pick
the work back up.

**State of the device right now:** the audio patches ARE back in `source=`
(together with `CONFIG_SM_LPASSCC_6115=m`), and the phone was left on
`kali-boot-v82-smmubypass.img`. That image is only for the SMMU experiment (it
adds `arm-smmu.disable_bypass=0`, which lowers memory protection); for normal use
flash **`kali-boot-v81-audio.img`** (same kernel, clean cmdline, audio device
tree present but `apr.ko` held back in `/root/audio-hold/` so nothing loads) or
**`kali-boot-v80-STABLE.img`** (no audio at all).

	0 [MotorolaMotoG82]: sm6375 - Motorola-Moto-G82-5G
	input: Motorola-Moto-G82-5G Headset Jack as .../card0/input5

--------------------------------------------------------------------------------
## 1. The hardware
--------------------------------------------------------------------------------
From the downstream device tree (rhodep pulls
`rhodep-audio-wcd9370-aw882xx-acf-dual-overlay.dtsi` via
`blair-rhodep-common-overlay.dtsi`):

- **WCD9370** codec, split over two soundwire buses: RX side at phy address
  0x01170224 (device 4 on the RX master), TX side at 0x01170223 (device 3 on
  the VA/TX master). Reset on TLMM gpio83.
  Supplies: rxtx and px from L11A, buck from L14A (both 1.8 V), mic bias from
  BOB, which mainline's pm6125 RPM regulator driver does not expose (VPH is
  used instead; the bias voltage itself comes from the
  `qcom,micbias*-microvolt` properties).
- **4 digital microphones** on LPI gpio6-gpio9.
- **Loudspeaker and earpiece: two Awinic smart amplifiers on i2c**
  (`qupv3_se10_i2c`, 0x34 speaker and 0x35 earpiece, i2c bus 2 in Android
  numbering), driven with a vendor specific ADSP topology
  (`aw-rx-topo-id = <0x1000ff01>` etc). Mainline has no driver for this
  family, so **there is no speaker or earpiece audio yet**. See §5.
- LPASS: VA and RX macros only, no TX macro instance in use and no WSA.

--------------------------------------------------------------------------------
## 2. What mainline already had, and the template
--------------------------------------------------------------------------------
`sm6375.dtsi` had **no audio nodes at all**, but the kernel config already had
everything compiled: `QCOM_APR`, the whole QDSP6 stack, the LPASS VA/RX/TX
macros, `WCD937X` + `WCD937X_SDW`, `SOUNDWIRE_QCOM`, `PINCTRL_SM6115_LPASS_LPI`
and `SND_SOC_SM8250` (the generic `qcom,*-sndcard` machine driver). Only
`SM_LPASSCC_6115` had to be enabled, for the soundwire resets.

The template is **sm6115 (bengal)**, the same LPASS generation. Two upstream
series are the reference:

- `[PATCH v3 00/12] qrb4210-rb2: add wsa audio playback and capture support`
  (Alexey Klimov), in particular `arm64: dts: qcom: sm6115: add LPASS devices`
  and `qrb4210-rb2: add wcd937x codec support`.
- `qcm6490-idp.dts` for the shape of a wcd9370 based sound card.

**Every LPASS address on sm6375 is identical to sm6115**, confirmed against
downstream `holi-audio.dtsi`:

| block | address | note |
| --- | --- | --- |
| rx macro | 0xa600000 | downstream rx-macro@A600000 |
| RX soundwire | 0xa610000 | GIC_SPI 297, same as sm6115 |
| tx macro | 0xa620000 | |
| lpass audiocc | 0xa6a9000 | downstream RX swr hctl reg 0x0A6A9098 |
| va macro | 0xa730000 | downstream va-macro@A730000 |
| VA/TX soundwire | 0xa740000 | downstream swrm-io-base 0xA740000 |
| LPI pinctrl | 0xa7c0000 | 19 GPIOs, same offset table |
| lpasscc | 0xa7ec000 | downstream VA swr hctl reg 0x0A7EC100 |

LPI pins, from downstream `holi-lpi.dtsi`: swr_tx_clk gpio0, swr_tx_data
gpio1/gpio2 (downstream also uses gpio18 for a third lane, which the sm6115
pin controller does not describe as swr_tx_data; two lanes are enough),
swr_rx_clk gpio3, swr_rx_data gpio4/gpio5, dmic01 gpio6/gpio7, dmic23
gpio8/gpio9.

Two deviations from sm6115 were needed, both reported by the hardware itself:
- The **VA/TX soundwire has 4 input ports** (sm6115 declares 3).
- The **RX soundwire has 1 input and 6 output ports** (sm6115 declares 0 and 5).

--------------------------------------------------------------------------------
## 3. The LPASS codec version 2.2
--------------------------------------------------------------------------------
The VA macro on this SoC reports major 2, minor 2, which no mainline driver
knows about:

	va_macro a730000.codec: VA Macro v2.2.0 is not supported

and since the VA macro provides the `fsgen` clock to the RX and TX macros,
nothing else probes. `LPASS_CODEC_VERSION_2_2` was added in its ordinal place
between 2.1 and 2.5 (so the WSA macro's `>= LPASS_CODEC_VERSION_2_5`
comparisons keep their meaning) and grouped with the **pre-2.5** branch in
every switch, i.e. RXn register stride 0x80, the pre-2.5 register defaults, the
pre-2.5 readable/writeable tables and the pre-2.5 controls and widgets.

Missing any one of those switches is not harmless: falling through to
`default: return false` in the register tables makes every version dependent
register unwriteable and the card refuses to instantiate:

	rx_macro a600000.codec: ASoC error (-5): at snd_soc_component_update_bits() ... [0x000004b4]
	snd-sm8250 sound: ASoC: failed to instantiate card -22

The stride choice is confirmed by downstream `bolero-cdc-registers.h`:
`RX_RX0_RX_PATH_CTL` 0x0400, `RX_RX1` 0x0480, `RX_RX2` 0x0500, and the two
registers the driver complained about are real: `RX_RX1_RX_PATH_CFG3` 0x0490
and `RX_RX1_RX_PATH_SEC7` 0x04B4.

--------------------------------------------------------------------------------
## 4. OPEN: the ADSP dies ~500 ms after a stream starts
--------------------------------------------------------------------------------
Everything up to and including the whole PCM prepare works. Then, about half a
second after the stream is triggered, the SoC resets. The bootloader reports the
reason on the next boot:

	androidboot.bootreason=watchdog        (powerup_reason 0x8000, vs 0x4000 normally)

### It is NOT the application processor

A userspace heartbeat (`echo hb > /dev/kmsg` every 50 ms) proves the AP is alive
and healthy well past the trigger:

	[113.712645] PCM-DIAG: MultiMedia1: prepare done
	[113.716337] HB 20
	...
	[114.178651] HB 29          <- ~460 ms later, then everything stops at once

and `CONFIG_HARDLOCKUP_DETECTOR` (buddy) is enabled and active
(`nmi_watchdog=1`) yet never fires, so no single CPU is stuck either.

So the AP runs normally while the ADSP starts pushing data, and then the whole
SoC goes down together. That is the signature of the **ADSP faulting and its
watchdog bite resetting the chip**: the AP is simply taken down with it, which
is also why nothing is ever printed.

### Ruled out, with the evidence

| candidate | verdict |
| --- | --- |
| The RX/headphone path specifically | No. **Capture dies too**, once the VA DMIC path is fully routed. It is the CDC DMA datapath in general, both directions. (The earlier capture attempt that "survived" was not actually routed and never started the DMA.) |
| Analog headphone PA current / PMIC brownout | No. Playing with the HPH PA switches left off still resets. |
| RX macro register layout for codec version 2.2 | No. Confirmed against downstream `bolero-cdc-registers.h`: RX0 0x400, RX1 0x480, RX2 0x500, so stride 0x80 (pre-2.5) is right, and 0x490/0x4B4 are real registers. |
| Missing LPASS_HW_MACRO_VOTE | Impossible here. The ADSP answers the vote request with "unknown command" and the VA macro then fails to probe with -110. The dcodec vote (block 4) is accepted. |
| Wrong ADSP SMMU stream id | No. Tried 0x1c1 (sm6115), 0x1801 (what downstream declares for this SoC) and no `iommus` at all: identical reset in all three cases. |
| A CPU stalled on an unclocked register | No, see the heartbeat and the hardlockup detector above. |
| The soundwire port counts | No. It reset the same way before and after correcting them. |
| The soundwire CGCR reset offsets | Correct: the driver uses 0x98 and 0x100, which match the downstream hctl registers 0x0A6A9098 and 0x0A7EC100. |
| The SMMU aborting the DSP's DMA | No. `arm-smmu.disable_bypass=0` changes nothing and no fault or "Blocked unknown Stream ID" message has ever appeared. |
| A missing LPASS clock vote | No. Downstream votes the same DCODEC block the macros already request; the MACRO vote it uses is for a different clock. |
| An ADSP software fault we could read | There is none to read: the SFR says "wdog or kernel error suspected", so the DSP hung rather than faulted. |

### Session 5: the ADSP hangs, it does not fault

The ADSP crash reason has been read, and it closes off a whole line of enquiry.

`drivers/remoteproc/qcom_q6v5_pas.c` gives sm6375 the sm6350 data, whose
`crash_reason_smem` is **423**. That SMEM item can be read after the reset,
because SMEM is a nomap reserved region in DDR and survives a warm reset.
`/dev/mem` is useless for it (CONFIG_STRICT_DEVMEM, the read returns 0 bytes),
so `tools/smemdump/` in this repo is a small module that calls
`qcom_smem_get()`; it is meant to be **built on the device** with the installed
headers, so it costs no build and no reflash. It lives in `/root/smemdump/` on
the phone.

After a crash, item 423 contains:

	smemdump: item 423 text: "SFR Init: wdog or kernel error suspected."

That string is what the ADSP writes at its next init when it finds the previous
shutdown was not clean. There is **no file and no line**, which means the ADSP
never reached a fault handler: it **hung**, and its watchdog bit. So no software
fault description is ever going to come out of the DSP, and the failure is the
same class as the IPA bug, an access that never completes, only on the DSP side.

Also consistent: the AP heartbeat survives ~460 ms past the trigger and then
everything stops together, which is what a wedged bus looks like from the AP.

Two more things ruled out this session:

- **The LPASS clock description is right.** Downstream's `lpass_audio_hw_vote`
  (the clock the bolero parent node votes) is
  `afe_vote_lpass_core_hw(AFE_LPASS_CORE_HW_DCODEC_BLOCK, ...)` in
  `techpack/audio/asoc/codecs/audio-ext-clk-up.c`, i.e. exactly mainline's
  `LPASS_HW_DCODEC_VOTE`, which is what the macros already request. Downstream
  uses the MACRO block vote for a *different* clock
  (`lpass_core_hw_vote`), so its absence here is correct, not a gap.
- **The SMMU is not aborting the DSP's DMA.** With
  `CONFIG_ARM_SMMU_DISABLE_BYPASS_BY_DEFAULT=y` an unregistered stream id is
  aborted, which was a good candidate for hanging a DMA master. Booting with
  `arm-smmu.disable_bypass=0` (a cmdline change only, no rebuild) changes
  nothing at all, and no log in any of the crashes has ever contained
  "Blocked unknown Stream ID", a context fault or a global fault. Combined with
  the earlier result that 0x1c1, 0x1801 and no `iommus` all behave identically,
  the SMMU can be dropped as a suspect.

### Where to look next: diff the AFE command sequence against downstream

This is desk work, no device time, and it is the last systematic avenue before
guessing again. The question is what mainline tells the ADSP that downstream does
not, for the same port.

Already done: the config **struct** is byte for byte identical, field order
included. Vendor `afe_param_id_cdc_dma_cfg_t`
(`techpack/audio/include/dsp/apr_audio-v2.h`) and mainline
`afe_param_id_cdc_dma_cfg` (`sound/soc/qcom/qdsp6/q6afe.c`) are both:

	u32 cdc_dma_cfg_minor_version; u32 sample_rate;
	u16 bit_width; u16 data_format; u16 num_channels; u16 active_channels_mask;

So it is not a layout mismatch. What has NOT been compared yet:

1. **The values**: `cdc_dma_cfg_minor_version` (mainline uses
   AFE_API_VERSION_CODEC_DMA_CONFIG == 1, confirm downstream agrees),
   `data_format`, and how `active_channels_mask` is computed for 2 channels.
2. **The command sequence around it.** Mainline does param-set then
   `AFE_PORT_CMD_DEVICE_START`. Downstream `techpack/audio/dsp/q6afe.c`
   (see `SIZEOF_CFG_CMD(afe_param_id_cdc_dma_cfg_t)` near line 1614 and work
   outwards to `afe_port_start`) may send **extra params first**. Candidates
   worth grepping for: island mode (the rhodep DT sets
   `qcom,msm-dai-is-island-supported = <1>` on `va_cdc_dma_0_tx`), a topology
   or `AFE_PARAM_ID_LPASS_CORE_SHARED_CLOCK_CONFIG`, and anything CDC-DMA
   specific that mainline has no equivalent for. A missing prerequisite param
   is a very plausible way to make the DSP walk into an unclocked block and
   hang.
3. If that comes up empty: check whether the **AP** is supposed to enable LPASS
   clocks that mainline never touches. `lpasscc-sm6115.c` only provides
   *resets*, while for example `lpassaudiocc-sc7280.c` provides real clocks. If
   SM6375's LPASS AUDIO CC has gates the CDC DMA path needs, nobody is enabling
   them.

--------------------------------------------------------------------------------
## 5. Speaker and earpiece: the Awinic amplifiers
--------------------------------------------------------------------------------
Not started. `CONFIG_SND_SOC_AW88261=m` is already enabled, and the Awinic
family the vendor driver covers includes chips that mainline supports, so the
first thing to do is read the chip id over i2c: enable `qupv3_se10_i2c` (i2c
bus with the amps at 0x34 and 0x35) in the device tree and use i2cdetect/i2cget
from userspace. If the part is an AW88261 (or another one mainline handles),
the remaining work is a DAI link from a MI2S/TDM port to the amps. If not, it
needs a new codec driver.

Note that downstream drives these amps as "smart PAs" with a proprietary ADSP
topology (`aw-rx-topo-id`, `aw-tx-topo-id`, calibration through `aw_attr`),
which mainline cannot reproduce; a plain i2s amplifier driver without the
vendor DSP algorithms is the realistic target.

--------------------------------------------------------------------------------
## 6. Manual routing used for testing (no UCM yet)
--------------------------------------------------------------------------------
There is no UCM profile, so `MultiMedia1` is not routed to any backend by
default ("no backend DAIs enabled for MultiMedia1"). For a headphone test:

	amixer -c 0 cset name='RX_CODEC_DMA_RX_0 Audio Mixer MultiMedia1' 1
	amixer -c 0 cset name='RX_MACRO RX0 MUX' AIF1_PB
	amixer -c 0 cset name='RX INT0_1 MIX1 INP0' RX0
	amixer -c 0 cset name='RX_MACRO RX1 MUX' AIF1_PB
	amixer -c 0 cset name='RX INT1_1 MIX1 INP0' RX1
	amixer -c 0 cset name='HPHL_RDAC Switch' 1
	amixer -c 0 cset name='HPHR_RDAC Switch' 1
	amixer -c 0 cset name='HPHL Switch' 1
	amixer -c 0 cset name='HPHR Switch' 1
	amixer -c 0 cset name='RX_RX0 Digital Volume' 100
	amixer -c 0 cset name='RX_RX1 Digital Volume' 100
	speaker-test -D hw:0,0 -c 2 -t sine -f 440 -l 1

A UCM profile will be needed for PipeWire/Phosh to use the card at all.

--------------------------------------------------------------------------------
## 7. How to pick this up again
--------------------------------------------------------------------------------
The audio patches are **not** in the kernel `source=` right now, so the shipped
kernel (v80-STABLE) has no audio device tree at all. To resume:

1. Add 0032-0036 back to `source=` in the linux-motorola-rhodep APKBUILD, add
   their sha512sums, and set `CONFIG_SM_LPASSCC_6115=m` in the config (the
   soundwire controllers need it for their resets). Update the config's sha too.
   0037 (the PCM prepare DIAG) is optional and useful.
2. Rebuild, build a boot image, flash.
3. Keep the safety net that made this iterable: move `apr.ko` out of the module
   tree and run depmod, so the device boots with the whole audio device tree
   present but nothing ever touches an LPASS register:

	mkdir -p /root/audio-hold
	mv /lib/modules/7.2.0-rc5/kernel/drivers/soc/qcom/apr.ko /root/audio-hold/
	depmod -a 7.2.0-rc5

   Then load it by hand when you want to experiment:

	modprobe pdr_interface && insmod /root/audio-hold/apr.ko

   Everything else cascades from there by modalias: q6afe, the clocks, the LPI
   pin controller, the macros, the soundwire controllers, the WCD9370 and the
   card.

Other things worth knowing:

- Anything that is only a driver change needs **no reflash**: rebuild, pull the
  modules out of the apk and scp them. Only device tree changes need a new boot
  image. That turns a ~10 minute cycle into a ~5 minute one.
- Always decompile the DTB and check the property you just changed before
  flashing. Two patches touch sm6375.dtsi in sequence, so editing only the first
  one's base tree silently leaves the old value in place; this was caught that
  way once.
- Crash logs: `/var/lib/systemd/pstore/`, not `/sys/fs/pstore/`.
- `CONFIG_DYNAMIC_DEBUG` is enabled in the shipped kernel, so the ASoC core,
  macro, soundwire and codec debug messages can be turned on at runtime:

	echo 'module snd_soc_core +p' > /sys/kernel/debug/dynamic_debug/control

  Use `module <name> +p`; `file <glob> +p` patterns did not match anything here.
- The buddy hardlockup detector is NOT enabled in the shipped kernel (it panics
  on detection, which is not wanted in a daily image). Re-enable
  `CONFIG_HARDLOCKUP_DETECTOR` + `CONFIG_HARDLOCKUP_DETECTOR_BUDDY` if needed;
  note it never fired for this bug, which is itself what proved the AP was not
  the one hanging.
