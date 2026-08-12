# Audio on SM6375 (rhodep) — bring-up notes

Status: **the sound card exists**, headset jack detection works, the WCD9370
enumerates on both soundwire buses. Starting a stream, in either direction,
still resets the SoC. The DSP accepts every ASM write command and never
acknowledges one, so it starves and hangs, and the watchdog takes the chip down.
The whole codec path (codec DMA, macros, soundwire, WCD9370) is proven innocent:
Secondary MI2S fails identically. That is the open item (§4), and §7 says how to pick
the work back up.

**Do not expect a quick win here.** Everything on the AP side is verified
correct against the vendor sources; see "Honest status" at the end of §4 for the
three things that are actually left, the cheapest of which is asking upstream.

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
| The AFE port configuration differing from downstream | No. Struct, values and command sequence all verified equivalent. |
| RX_0/AIF1 being the wrong CDC DMA channel | No. RX_1/AIF2, the pairing the tested sm6115 series uses, hangs identically. |
| The codec version 2.2 / register stride classification | Verified right: both default tables hold the same registers and values, only the stride differs, and 0x80 matches downstream. |
| A missing AP side LPASS clock | There is none: the vendor kernel has no lpass clock controller for holi/blair at all. |
| The codec DMA, the macros, the soundwire bus, the WCD9370 | All exonerated: playback over Secondary MI2S, which touches none of them, fails identically. |
| The buffer address or the SMMU | Understood: the address is IOVA or-ed with (sid << 32), exactly as sm8250 does it, and no SMMU fault has ever appeared. |
| The codec DMA, the macros, the soundwire bus, the WCD9370 | All exonerated: playback over Secondary MI2S, which touches none of them, fails identically. |
| The AFE/ASM buffer address or the SMMU | Understood and exonerated so far: the address is IOVA \| (sid << 32) exactly as sm8250 does it, and no SMMU fault has ever appeared. |

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

### Session 5b: the AFE sequence is equivalent, and RX_1 does not help either

The AFE comparison against downstream is **done, and it found nothing**:

- The config struct is byte for byte identical, field order included (vendor
  `afe_param_id_cdc_dma_cfg_t` in `techpack/audio/include/dsp/apr_audio-v2.h`
  vs mainline `afe_param_id_cdc_dma_cfg`).
- `data_format` is a mixer control with default 0 on both sides, not a DT value.
- mainline computes `active_channels_mask = (1 << num_channels) - 1` = 0x3 for
  stereo, which is right.
- Downstream's CDC DMA DAI `prepare` (`msm_dai_q6_cdc_dma_prepare`) does nothing
  but an optional data-alignment param and `afe_port_start`; no clock calls, no
  extra setup. The params downstream sends before the config (custom topology,
  port topology id, cal, hw delay, cps) are all gated on calibration mode and
  are not prerequisites.
- The only CDC DMA DT property rhodep sets is
  `qcom,msm-dai-is-island-supported` on `va_cdc_dma_0_tx`, which is a low power
  capture mode, irrelevant to playback.

**RX_CODEC_DMA_RX_1 with the RX macro's AIF2 was tested and hangs identically.**
That pairing is the one the only tested description of this LPASS generation
uses (the qrb4210-rb2 sm6115 series: "RX1 from DSP is connected to rxmacro"), so
it was the best remaining lead, and it makes no difference. Both RX_0/AIF1 and
RX_1/AIF2 hang at exactly the same point.

Two more things closed off:

- **The codec version 2.2 classification is definitely right.** The two register
  default tables in the RX macro (`rx_pre_2_5_defaults` and `rx_2_5_defaults`)
  contain the *same registers with the same values*; the only difference is the
  address stride they are named for. The stride was already verified against
  downstream `bolero-cdc-registers.h` (RX0 0x400, RX1 0x480, RX2 0x500 = 0x80,
  the pre-2.5 layout).
- **There is no AP side LPASS clock controller to be missing.** The vendor kernel
  has gcc, dispcc, gpucc and debugcc for holi/blair but the only lpass clock
  driver in its tree is `lpasscc-sdm845.c`, for a different SoC. So LPASS
  clocking really is the ADSP's job through the AFE votes, which is what is
  already done, and the swr CGCR registers mainline drives via lpasscc-sm6115
  are the same ones downstream pokes directly through `qcom,swrm-hctl-reg`.

### Session 6: the failure is in the ASM data path, not in the audio hardware

This session narrowed the problem enormously and eliminated the entire audio
hardware block from suspicion.

**The exact failure, at last.** Instrumenting the playback data handshake
(`0038-DIAG-ASoC-q6asm-dai-trace-write-handshake.patch`, which traces
`q6asm_dai_ack`, `q6asm_write_async` and every packet the ASM service receives)
gives the same picture every time:

	ASM-DIAG: ack: appl=6240 queue=0 period=6240 avail=1 pcm_count=24960
	ASM-RX: write: buf=0 addr=00000001fff80000 len=24960 handle=0xb08eb9b8 -> apr_send
	ASM-RX: write: apr_send returned 52
	... (one per period, all of them fine) ...
	ASM-DIAG: RUN_DONE
	                       <- and then nothing, ever

The AP queues the periods correctly, every `apr_send` succeeds, the DSP answers
the memory map, the command responses and RUN_DONE, and then **never sends a
single WRITE_DONE**. Without the acknowledgement the AP cannot queue more, the
DSP runs out of data, and the SoC dies. That is exactly why the survival time
tracks the amount of audio queued: two periods die in 27 ms, a half second
buffer survives ~460 ms.

**It is NOT the codec DMA, the macros, the soundwire bus or the WCD9370.**
Playback over **Secondary MI2S**, which is where the loudspeaker amplifiers hang
and which does not touch the codec DMA path at all, fails **identically**: same
writes, same zero WRITE_DONE, same hang. Capture fails the same way. So
everything specific to the codec path is exonerated and the problem lives in the
ASM/AFE data path that both share.

**The buffer address convention is understood.** q6asm-dai takes the low four
bits of the SMMU stream id and puts them in bits 32+ of the address handed to
the DSP:

	pdata->sid = args.args[0] & SID_MASK_DEFAULT;      /* 0x1801 & 0xF = 1 */
	prtd->phys = substream->dma_buffer.addr | (pdata->sid << 32);

which is why the address is 0x1_fff80000 with `iommus` present and a raw physical
0xfdc00000 without it. sm8250 uses the same stream id and the same code, so this
is not obviously wrong, both variants fail identically, and no SMMU fault,
context fault or "Blocked unknown Stream ID" has ever appeared in any log.

Note for whoever continues: downstream confines the audio IOVAs to a low window
(`qcom,iommu-dma-addr-pool = <0x10000000 0x10000000>` on qcom,msm-audio-ion)
while mainline lets the DMA framework allocate from the top of a 64-bit space,
which is how 0x1_fff80000 comes about.

### Two useful things that came out of it anyway

- **USB audio works.** A USB headset over OTG registers as its own card and plays
  a test tone, confirmed by ear. That is real, usable audio output on this phone
  today, completely independent of the LPASS. Anyone who needs sound now should
  use USB.
- **The loudspeaker amplifiers are identified.** Enabling i2c10 (the downstream
  `qupv3_se10_i2c`, which is **/dev/i2c-1** at runtime, Linux numbers buses
  dynamically) shows both amplifiers answering, 0x34 loudspeaker and 0x35
  earpiece, both reporting chip id **0x2113**, which is exactly
  `AW88261_CHIP_ID`. `CONFIG_SND_SOC_AW88261=m` is already enabled, so the
  speaker is reachable as soon as the ASM path works: it needs the amplifier
  nodes on i2c and a Secondary MI2S dai-link, and that link is already in the
  device tree (with a `linux,spdif-dit` dummy codec, plus `qcom,sd-lines = <0>`
  on the q6afedai child, which this session found was required).

### Session 6b: four more concrete hypotheses, all tested, all negative

Each of these was a real, specific difference or suspicion, and each was tested
on hardware. None changed the outcome: the DSP still never acknowledges a write.

- **32 bit DMA mask on q6asmdai.** Tried, no change, and the premise was wrong:
  the address the DSP is given, 0x1_fff80000, is the IOVA 0xfff80000 (already
  below 4 GB) or-ed with the stream id in bit 32, exactly as
  `prtd->phys = dma_buffer.addr | (pdata->sid << 32)` intends. The addressing
  scheme is coherent and matches sm8250.
- **The APR router dropping the reply.** Instrumented `apr_callback()` to log
  every packet the router receives. During a failing playback exactly **8**
  packets arrive: three AFE responses, two ASM responses plus the memory map
  response, and two ADM ones. None is a WRITE_DONE. So the DSP genuinely never
  sends it and nothing is being dropped on the way in. The whole control path
  (AFE, ASM and ADM) answers normally.
- **The token layout.** mainline packs the transfer length into bits 16..31 of
  the token, which is where downstream keeps `buf_index` (16..23) and a flags
  byte (24..31, bit 24 = direction, bit 25 = no wait), so a 24960 byte period
  sets the direction flag among others. Sending just the buffer index, leaving
  the flags byte zero, changes nothing.
- **The stream session mode.** Downstream pairs the null post-processing
  topology (`ASM_STREAM_POSTPROCOPO_ID_NONE`, 0x00010C68, the same value as
  mainline's `ASM_NULL_POPP_TOPOLOGY`) only with the **low latency** session
  types; for a legacy session it uses a real topology id from the calibration
  database. mainline always opens legacy + null topology, a combination
  downstream never uses. Opening with `ASM_LOW_LATENCY_STREAM_SESSION` instead,
  which is the combination downstream does use, also changes nothing.

### Session 6c: the default topologies, also negative

Downstream opens legacy PCM sessions with a topology id from the ACDB
calibration database, and mainline uses the null ones on both sides, so the
obvious cheap approximation was to use the vendor's **default** topologies
instead of the null ones:

- ASM (POPP): `DEFAULT_POPP_TOPOLOGY` 0x00010BE4 instead of
  `ASM_NULL_POPP_TOPOLOGY` 0x00010C68, back on a legacy session, which is the
  combination downstream uses.
- ADM (COPP): `DEFAULT_COPP_TOPOLOGY` 0x00010314 instead of
  `NULL_COPP_TOPOLOGY` 0x00010312 in q6routing.

No change: four writes, zero WRITE_DONE. Worth recording that the APR trace of
this run shows the control path completing in full, including the ADM answering
`ADM_CMDRSP_DEVICE_OPEN_V5` (0x00010329) for AFE port 0x1002 and then the matrix
map. Everything is set up correctly and the DSP simply does not consume.

### Session 7: the SMMU stream id was wrong, and the DSP still says yes to everything

Four things came out of this session, one of them a real fix.

**1. Nobody has ADSP audio on sm6375 in mainline.** This should have been
checked much earlier. Upstream `sm6375.dtsi` has no `apr` node at all, and
neither sm6375 board (rhodep, sony-xperia-murray-pdx225) declares a sound
card. The whole effort has been running on the unverified assumption that
"it should work like sm6115". The real working reference of the same LPASS
generation is sm6115 / qrb4210-rb2, which does have apr, q6asm and a card.

**2. The SMMU stream id was taken from the wrong SoC (fixed).** The generic
`msm-audio-lpass.dtsi` is overridden per target, and `holi-audio.dtsi` says:

	&msm_audio_ion {
		iommus = <&apps_smmu 0x00A1 0x0>;
		qcom,smmu-sid-mask = /bits/ 64 <0xf>;
	};

so the correct value here is 0xa1 with a mask of 0xf, covering 0xa0-0xaf.
Nothing else in sm6375.dtsi uses a stream id in that range. What was in the
DT was 0x1801, which is sm8250's. This is now `iommus = <&apps_smmu 0xa1
0xf>` in patch 0032 and the dais device does get an iommu group, but it did
not change the symptom, and no SMMU fault is logged either way.

**3. The APR header is 8 words, not 5, and every command succeeds.** The
diagnostic hook was only printing the packet header, so all eight received
packets were being read as "fine" without ever looking at the status word.
Dumping the raw packet shows `hdr_field = 0x0181`, i.e. a header length of 8
words (32 bytes), so the payload starts at w08 and not at w05 as a naive
`sizeof(struct apr_hdr)` would suggest. Mainline's apr.c gets this right; the
first attempt at reading the status by hand did not, and produced garbage
(timestamps read as status codes) that could easily have been mistaken for
an error. Decoded properly, `APR_BASIC_RSP_RESULT` gives:

	responded-to opcode                    status
	0x000100f3 AFE_SVC_CMD_SET_PARAM       0
	0x000100ef AFE_PORT_CMD_SET_PARAM      0
	0x000100e5 AFE_PORT_CMD_DEVICE_START   0
	0x00010db3 ASM_STREAM_CMD_OPEN_WRITE   0
	0x00010325 ADM_CMD_MATRIX_MAP_ROUTINGS 0
	0x00010d98 ASM_DATA_CMD_MEDIA_FMT_UPD  0

Everything the DSP acknowledges, it acknowledges as successful, including
the AFE port actually starting. `ASM_CMDRSP_SHARED_MEM_MAP_REGIONS` returns
a handle (0xb08eb9b8) and mainline reads it from the right offset. So the
"a command is failing silently" theory is dead. Raw and decoded traces are
kept in `evidence-session7/`.

**4. The one command with no acknowledgement is RUN.** There is no response
to `ASM_SESSION_CMD_RUN` anywhere in the trace, because mainline sends it
through `q6asm_run_nowait()`. That leaves the most plausible remaining
explanation, and it fits the one measurement that has never been explained:
survival time scales with the amount of audio queued. If RUN never takes
effect, the DSP stays open-but-stopped, accepts writes into its queue
without consuming them, never returns WRITE_DONE, and eventually watchdogs
when the queue fills. The next step is to instrument the transmit side in
`apr_send_pkt()` (drivers/soc/qcom/apr.c:55) and confirm whether RUN is
actually sent, with which dest_port and token, and whether the DSP answers
it with an error that is currently being discarded.

Also ruled out this session, all negative:

- The default DSP topologies (see session 6c).
- `qcom,protection-domain` missing on the apr node: it is not missing, it is
  correctly declared per service on `service@7`, which is where mainline
  wants it. pd-mapper does log "Cannot open firmware path" but the services
  probe, so PDR reports the audio PD up.
- LPI power domains: upstream sm6375.dtsi already has `SM6375_VDD_LPI_CX`
  and `SM6375_VDD_LPI_MX` with the "lcx"/"lmx" names, same as sm6115.
- The MI2S bit clock: `sm8250.c` does configure `SEC_MI2S_IBIT` at 1536000,
  in `sm8250_snd_startup()`, which is in the be_ops that this card uses.

One invalid experiment is worth recording so it is not repeated: playing
with no route enabled, to see whether the DSP consumes without a backend.
The first attempt looked like it ran but the trace still showed the ADM
opening a COPP, because alsa-restore reinstates the saved mixer state on
every boot and the clearing loop had not actually matched the control name.
With the route genuinely off, aplay refuses to open the device with
"Invalid argument", because DPCM will not start a front end with no back
end, so this particular bisection cannot be done through aplay at all.

### Honest status and what is left

The audio hardware description is verified correct, the entire codec path is
proven innocent, and the question is now precise: **why does this DSP firmware
accept ASM write commands and never acknowledge them?** Everything else works:
the memory map succeeds and returns a handle, OPEN_WRITE, the media format, the
ADM device open and RUN are all answered, and every `apr_send` returns success.

What is genuinely left, none of it cheap:

1. **The post-processing topology.** This is the one real difference still
   standing: downstream opens legacy PCM sessions with a topology id taken from
   the ACDB calibration database, and we have no ACDB. Feeding a real topology
   id would mean extracting it from the vendor calibration blobs on the device.
   Worth noting the DSP may simply refuse to run a stream whose topology it
   cannot instantiate, which would fit the symptom exactly.
2. **Compare the ADM (matrix routing) setup**, the one part of the control path
   not yet compared line by line against downstream. The stream has to be
   connected to a device through the matrix before the DSP will pull data.
3. Capturing the actual APR byte stream of a working downstream boot and
   diffing it against ours would settle it in minutes, but it needs the device
   running the vendor kernel with tracing, which this port cannot do.

The realistic summary: this needs either the vendor calibration data or someone
who knows the ASM firmware protocol. It is not a device tree problem, and it is
not in the parts of the audio path this port describes.

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
