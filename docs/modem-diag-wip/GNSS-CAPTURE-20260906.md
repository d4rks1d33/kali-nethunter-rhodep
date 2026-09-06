# The first DIAG capture across the GNSS reset

> **CORRECTION, same day, before anyone builds on this.** The heading below used
> to read "the modem said nothing", and that was wrong. The modem was reporting
> about GNSS continuously, in binary log packets, in the very first capture —
> **3831 of them, 250 inside the window that resets, the last at uptime 195.80,
> 120 ms before the rail drops.** The error was mine and it was methodological:
> I looked only at the plain-text F3 channel and at a log-code histogram
> produced by a pattern scanner that this repo already documents as unreliable,
> and I read "no text" as "no data". Everything in the "what the capture shows"
> section about the log-code histogram being unusable is still true of that
> scanner; the fix is to deframe properly, not to distrust the stream. The
> corrected findings are in "What was actually in the capture" below. The rest
> of the document is left as written, because the mask bug it diagnoses was
> real and the fix is real.

**The capture worked.** The modem streamed complete 4096-byte
packets at a steady 60 ms cadence, at a slightly *rising* rate, right up to the
instant the SoC lost power — no error, no assertion, no timeout, no degradation,
and the final record is not even truncated.

That is a real result and it is not the one that was wanted, for a reason this
document is mostly about: **the capture contains no F3 messages at all**, and F3
is where the modem's plain-text diagnostics live. The instrument was pointed at
the right event for the first time, and it turned out to be looking through the
wrong channel.

	bootreason=watchdog   powerup_reason=0x00008000     (the reset)
	bootreason=reboot     powerup_reason=0x00004000     (the calibration)

Capture kept in `backups/diag-gnss-20260906/` (git-ignored). 7 411 165 bytes,
1835 records, uptime 70.91 to 195.92.

---

## What was run

One `rhodep-diag-server.py` covering two runs of the bisector on one boot, so
that the surviving configuration and the failing one land in the same file and
are directly comparable. This is the part worth reusing: the differential is
free, and a single-run capture cannot tell "the modem does X before it dies"
from "the modem always does X".

	uptime  70.9        server publishes, handshake completes 40 ms later
	uptime 106.7-173.1  run A: powermode=3,1000 recurrence=periodic  -- survives
	uptime 187.75       run B: powermode=2,1000 recurrence=periodic  -- resets
	uptime 195.92       power lost

Run A reproduced the recorded result exactly: `SURVIVED 45 s`, 270 indications,
**0 measurement reports, 0 satellite info indications**, and `REQUEST INVENTORY:
1 kind(s)` — the engine asking for time once per fix cycle and never being
answered, which is the check that proves the boot was time-disarmed.

Pre-run state, all verified on the boot used: `rhodep-xtra.timer` masked (see
below), `ExecMainStartTimestamp` empty, `rhodep-gnss.service` stopped, no
injection anywhere in the journal, radio `online`, LOC at node 0 port 108.

**`systemctl disable` is not enough for `rhodep-xtra.timer`.** The holds
machinery registers that unit's enabled state in
`/usr/local/share/rhodep/protect.d/gnss.units` and `rhodep-holds-enforce`
re-enables anything reporting `disabled` on the next boot — which is the whole
point of it, and which would have re-armed the time lever inside the reproducer
window without saying so. `systemctl mask` survives the enforcer, because a
masked unit reports `masked` and not `disabled`. Unmask afterwards.

## What the capture shows

| | idle | A (survives) | idle2 | B (resets) |
| --- | --- | --- | --- | --- |
| records | 505 | 970 | 224 | 136 |
| bytes | 2 026 111 | 3 916 689 | 907 227 | 539 118 |
| span | 35.7 s | 66.4 s | 14.6 s | 8.1 s |
| rate | 56.7 kB/s | 59.0 kB/s | 62.2 kB/s | **66.9 kB/s** |

The stream does not falter, thin out, or stall. It speeds up slightly and then
stops mid-flight. The last record is a full 4096 bytes and its declared length
matches the bytes present, so the modem was not cut off part-way through
writing: it finished a packet, and there was no next one.

29 490 HDLC frames, by command code:

| cmd | frames | what it is |
| --- | --- | --- |
| `0x10` | 19 707 | binary log packet |
| `0x98` | 9 644 | unidentified; 2430 of these in the LTE capture too |
| `0x60` | 134 | event report |
| `0x79` | **0** | F3 message |
| `0x92` | **0** | extended F3 message |

`rhodep-diag-decode.py` finds **0 extended messages** in the whole file, which
agrees.

**Nothing in the log-code histogram distinguishes B from A.** Every code present
in B is present in A. One code appears once in B and nowhere else
(`0x0010da`, n=1) and one appears 35 times in A and not in B (`0x0014d8`), and
neither should be believed: `scan_logs` is a byte-pattern scanner, the repo
already records that it undercounts and "invents alignment", and the codes it
reports here (`0x508f`, `0x5a95`, `0x18e8`) are outside Qualcomm's normal
ranges. Treat the histogram as unusable rather than as a negative.

The only readable strings in 7.4 MB are `Small pool` (204), `Large pool` (192),
`time.xtracloud.net` (38), a WiFi SSID, and a handful of QMI field names. All of
them are text embedded *inside* binary log packets, not F3 messages.

`time.xtracloud.net` appears 38 times, first at 128.02 — inside run A, at
roughly 0.47/s, which is the once-per-fix-cycle time request — and **zero times
in run B**. That is consistent with run B dying about 100 ms after
`QMI_LOC_START`, before the first fix cycle completes, and it is not evidence of
anything else. Window B is 8.1 s long but the engine only ran for the last
fraction of a second of it; the rest is the bisector doing configuration and
read-back.

## The finding that matters: F3 is not being captured

The three mask packets are accepted and the modem plainly acts on the log and
event masks — 19 707 log frames and 134 events arrive. The F3 mask produces
nothing, on either the legacy or the extended message type, in any window,
including the idle ones.

Everything the GNSS investigation wants from DIAG is F3. The strings that
`NAV-CORE-RESET.md` extracted from `modem.b21` and that would say why the NAV
core does not come up —

	Failed to enable Nav SDD HW. Clock Status: Flag %d, BCR 0x%x, QDSCR 0x%x,
	    PllMode 0x%x, NavClkStat 0x%x
	Md %u Fifo 0x%x RTC 0x%x STMR 0x%x SpCtl 0x%x GDSC 0x%x Retry %d
	NavRX ADC cal is failed. Request CC reset!
	Qlink CSR WDOG timeout in command process
	GNSS RTC not running. Not resetting NAV core

— are F3 messages, and `modem.b21` is the diagnostic message table. None of them
can appear in a capture that carries no F3.

**So the honest reading of "the modem said nothing" is "we did not ask it in the
language it answers in".** The capture is not a negative result about the
firmware. It is a positive result about the instrument: DIAG carries logs and
events on this port today, and does not yet carry messages.

### One hypothesis tested and eliminated

`msg_mask_all()` sent `ssid_first = ssid_last = 0`, i.e. the range [0, 0] —
ssid 0 alone — while its docstring claimed "whole ssid range". `diag_masks.c`
uses `ALL_SSID` (`-1`) as the sentinel there. That looked like the whole
explanation.

It is not. Changed to `0xFFFF`/`0xFFFF`, verified on the wire
(`0b0000000b000000010200ffffffff00000000`), and re-captured: 2.0 MB, 35 s, 4236
frames of `0x10`, 29 of `0x60`, **still zero of `0x79` or `0x92`**. The change is
kept because it is the more faithful thing to send, and it is written up here so
nobody re-derives the same dead end.

### SOLVED, same day: the F3 mask packet was the wrong shape

`diag_send_msg_mask_update()` does **not** use the size-0 shortcut for
messages. For `DIAG_CTRL_MASK_ALL_ENABLED` it sets `mask_size = 1`, multiplies
by `sizeof(u32)`, memcpy's four bytes of body, sets `data_len` to 15, and then
**loops over `msg_mask_tbl` sending one packet per ssid range**. The log mask
and the event mask *do* use the size-0 shortcut — which is exactly why those
two always worked here and this one never did. This server had copied their
shape onto a path that does not have it, and sent one 19-byte packet where the
driver sends N packets of 23 bytes each.

Three changes, all in `rhodep-diag-server.py`:

* `msg_mask_range()` replaces `msg_mask_all()`: one packet per range,
  `msg_mask_size = 1`, four bytes of body, the range's real ssids.
* The CNTL receive path now **walks the datagram**. The modem concatenates
  several control packets into one read — the 4020, 3692 and 2888 byte reads
  were all multi-packet — and this parsed only the first, so everything after
  it was dropped unread. That is why the modem's own reports had never been
  seen.
* Those reports are now parsed and acted on. The modem has been sending them
  all along, because it advertises mask centralization (feature bit 11 of
  `f7fe1b`), which means it does not own its masks and expects the master to
  send them down after reading its tables up.

The modem's tables, read for the first time on 2026-09-06:

	modem log ranges: last_equip=13 num_ranges=14
	modem ssid ranges (25): 0-134 500-506 1000-1007 2000-2008 3000-3014
	  4000-4010 4500-4584 4600-4616 5000-5036 5500-5517 6000-6081 6500-6521
	  7000-7003 7100-7111 7200-7201 8000-8000 8500-8532 9000-9008 9500-9521
	  10200-10210 10251-10255 10300-10300 10350-10377 10400-10416 10500-10505
	modem build mask 0-134: 0x1f 0x1e 0x1f 0x18 0x1f 0x1e 0x1e 0x1c 0x18 ...

`0x1f` is LOW|MED|HIGH|ERROR|FATAL. The strings are compiled in, from the
modem's own mouth.

Result, measured immediately after, 45 s on an idle phone:

	before:  30692 frames, F3 0
	after:   30692 frames, F3 24782   (0x99 16908, 0x79 5740, 0x92 2134)

	NEW_DEBUG:nBursts 0                                  gfw_urxfe_drv.cc
	qlss0: qsleep policy for type=0 found, cycle_1_time=44 pll_sd=0
	                                                     qsf_qsleep_policy.c
	VADC conv result for XO_THERM: eStatus 0, nPhysical 31289, uPercent
	  16193, uMicrovolts 463300, uCode 7141               VAdcLog.c

Note `0x99` — QSR4, the hash-compressed form — is the commonest by far, so
anything counting only `0x79` and `0x92` will report "no F3" for a modem that
is emitting plenty. Count `0x79, 0x92, 0x99, 0x9c, 0x9d`. And deframe with
proper HDLC unescaping: `0x7d` escapes the next byte with `^ 0x20`, so
splitting naively on `0x7e` breaks packets. That is also the answer to the
`0x98` frames this repo has carried as unidentified since the LTE work — they
are `DIAG_MULTI_RADIO_CMD_F`, an 8-byte multi-SIM wrapper around ordinary
`0x10` log packets.

### And the capture was re-run with F3 working. The engine still says nothing.

Second capture, same two-run method, `bootreason=watchdog`, 5.2 MB, uptime
58.16 to 150.27, run B fired at 142.10. **43 173 F3 frames, 12 139 of them
carrying text — and not one of them from the positioning engine.**

By source file, the whole capture:

	x5541  gfw_urxfe_drv.cc      GERAN receive front-end
	x2639  gfw_rf_triggers.cc    GERAN RF trigger scheduling
	x1739  gfw_acq_task.cc       GERAN acquisition
	x148   platform_helium_init.c
	x109   ar_wal_phy_dev.c      WLAN
	 ...   cal_sm_*, halphy_cal_sm.c, wal_*, resmgr_ocs.c

It is the 2G search loop and the WLAN firmware, at full volume, to the last
millisecond. The final messages before the rail drops are `Urxfe Miss Timeline
profiling` and `WBEE_DEBUG : wbPwrEstRaw ...` — a cellular receiver doing what
it was already doing. Nothing named `nav`, `gnss`, `sdd`, `adc cal`, `qlink`
or `stmr` appears anywhere in 12 139 messages.

So the instrument now works and the answer is still silence, which relocates
the question rather than answering it. The most economical explanation, and it
is testable: **the modem announced exactly two protection domains**, `0x21`
`msm/modem/root_pd` and `0x21` `msm/modem/wlan_pd`. Masks are only delivered
to a peripheral after its `diag_id` has been sent, so a positioning engine
living in a third PD that never announced itself would never be enabled — and
`gfw_*` and `wal_*` are precisely root_pd and wlan_pd. Finding out whether a
GNSS PD exists, and whether it can be made to announce, is the next step.

The other possibility is that the GNSS ssids sit outside the 25 ranges the
modem reported. The ranges top out at 10505 and are dense; that can be checked
offline against the ssid numbering in any public Qualcomm header.

### What to try next, in order

1. **Send one control packet per ssid range**, walking `msg_mask_tbl`, which is
   what `diag_send_msg_mask_update()` actually does. This sends a single packet
   for all ranges. Cheapest remaining candidate.
2. **Use `DIAG_CTRL_MASK_VALID` with a real per-ssid bitmap** instead of leaning
   on the `ALL_ENABLED` size-0 shortcut. That shortcut is only *known* to work
   for events here; it is assumed to work for messages.
3. **Accept that this firmware may not route F3 to the DATA socket at all**, in
   which case no mask fixes it and the strings have to be recovered by decoding
   the `0x10` log packets — which is what SCAT is for, and which is also the
   answer to the unidentified `0x98` frames.

Until one of those lands, a GNSS capture costs a reset and buys a byte rate.

## What is now known that was not before

* The modem is **busy and healthy at full output rate at the instant power
  goes**, with no fault of its own on the channel it does emit. This is the same
  shape as the LTE reset, where DIAG showed continuous physical-layer logging
  with the SoC gone milliseconds later and no error, no assertion, no exception.
  Two different triggers, one signature.
* The stream **stops on a record boundary**. Whatever takes the rail down does
  not interrupt the modem mid-write; it removes power between packets. That is
  consistent with the PMIC `POFF sequence ran, status 0002 -> PS_HOLD` reading
  and inconsistent with the modem crashing and something else noticing.
* The dump survives, reproducibly, and the two-runs-one-server method works. The
  procedure is cheap now: about four minutes per attempt, one reset.

## Reproducing

	# the timer must be MASKED, not disabled -- rhodep-holds-enforce re-enables
	# anything merely disabled on the next boot
	sudo systemctl stop rhodep-gnss.service
	sudo systemctl mask --now rhodep-xtra.timer rhodep-xtra.service
	sudo systemctl reboot                       # clears time from the modem

	# on the new boot:
	systemctl show rhodep-xtra.service -p ExecMainStartTimestamp   # must be empty
	sudo systemctl stop rhodep-gnss.service     # it comes back enabled
	md5sum ~/rhodep-gnss-bisect.py              # 507cbb2ce6fea20b8ea7437daaea1df9
	sync; sudo sh -c 'echo 3 > /proc/sys/vm/drop_caches'; md5sum ~/rhodep-gnss-bisect.py
	qrtr-lookup | awk '$1==16'
	sudo qmicli -p -d qrtr://0 --dms-set-operating-mode=online

	sudo setsid nohup python3 ~/rhodep-diag-server.py --seconds 420 \
	    --dump /var/log/diag-gnss-$(date +%s).bin \
	    --log  /var/log/diag-gnss-$(date +%s).log --cmd 00 --cmd-after 10 &
	# wait ~15 s and confirm the handshake in the log before firing anything

	sudo ~/rhodep-gnss-bisect.py 45 opmode=standalone powermode=3,1000 \
	    recurrence=periodic events=min,engine,inject,meas,svpoly     # survives
	sudo ~/rhodep-gnss-bisect.py 20 opmode=standalone powermode=2,1000 \
	    recurrence=periodic events=min,engine,inject,meas,svpoly     # RESETS

	# after the reboot:
	sudo systemctl unmask rhodep-xtra.timer rhodep-xtra.service
	sudo systemctl enable --now rhodep-xtra.timer
	sudo systemctl start rhodep-gnss.service

Do not put anything in `/tmp`; it does not survive. `--dump` and `--log`
truncate rather than append despite the help text, so timestamp the filenames.
Detect the reset by uptime, never by ssh dropping.

---

# What was actually in the capture

Three mistakes turned a stream full of GNSS data into "the modem said nothing".
All three are in the reader, none in the capture.

* **HDLC unescaping.** `0x7d` escapes the following byte with `^ 0x20`. Splitting
  on `0x7e` without unescaping corrupts frames and manufactures log codes. The
  out-of-range codes this document dismissed as "unusable" — `0x508f`, `0x5a95`,
  `0x18e8` — were that corruption, and dismissing them was the right call for
  the wrong reason. Deframe properly and they resolve.
* **The `0x98` wrapper.** `DIAG_MULTI_RADIO_CMD_F`, 8 bytes, around an ordinary
  `0x10` log packet. Strip it and re-dispatch on the inner code, or a third of
  the stream stays invisible.
* **Log-packet layout.** The log code is a `u16` at offset 6, and
  `equip_id = code >> 12`. GNSS lives under equip_id 1.

With those fixed, counted from the two captures already on disk:

| | first capture | with F3 working |
| --- | --- | --- |
| GNSS log packets | **3831** | 1455 |
| in the window that resets | **250** | 158 |
| last GNSS packet | t=195.80, 120 ms before the end | t=150.27, the last record |

Named codes present in both: `LOG_CGPS_IPC_DATA_C` (0x1375), the richest of them
at 257 records; `LOG_GNSS_GTS_INPUT_REPORT_C` (0x18ab);
`LOG_GNSS_PDSM_PD_EVENT_CALLBACK_C` (0x1490); `LOG_GNSS_SLIM_*` (the modem's
sensor-listener acting as a QMI client of the sensor DSP); and
`LOG_GNSS_TECH_SEL_*`. The event stream carries 39 each of
`EVENT_GPS_PD_FIX_START`/`_END`, `EVENT_GPS_LM_SESSION_START`/`_END` and
`EVENT_GPS_LM_FIX_REQUEST_START`/`_END` — 39 fix cycles, which is exactly the
45-second run A at 1 Hz. Three plain-text F3 messages did arrive, from ssid 86
`MSG_SSID_GNSS_LOCMW`, out of `loc_client.c` and `loc_qmi_shim.c`.

**So the engine is observable, it was observed, and it keeps reporting to within
120 ms of the rail dropping.** What is still missing is a message saying *why*.

## The protection-domain lead is dead, and so is the missing-ssid one

Both hypotheses at the end of the previous section are disproven from source.

* **There is no third protection domain and there cannot be one.**
  `diag_query_pd()` in the driver knows ten names — `modem/root_pd`,
  `adsp/root_pd`, `slpi/root_pd`, `cdsp/root_pd`, `npu/root_pd`, `wpss/root_pd`,
  `wlan_pd`, `audio_pd`, `sensor_pd`, `charger_pd` — and none is GNSS. An
  announcement with an unknown name is dropped without a reply. Upstream's
  `qcom_pd_mapper.c` declares exactly two modem domains and puts
  `gps/gps_service` **on `msm/modem/root_pd`**. And it would not matter anyway:
  masks are per *peripheral*, not per domain. `diag_id_sent[]` is indexed by
  peripheral, and one mask packet covers every PD on it.
* **The 25 ssid ranges are the complete standard table.** Every one maps
  exactly, both endpoints, onto a block of Qualcomm's `msgcfg.h`. All the
  positioning ssids are inside range 1 (`MSG_SSID_GEN`, 0-134): `MSG_SSID_GPS`
  19, `MSG_SSID_MGP` 46, `MSG_SSID_GPSSM` 50, `MSG_SSID_TLE` 81,
  `MSG_SSID_GNSS_LOCMW` 86, `MSG_SSID_SLIM` 113. That range is enabled and
  delivering — ssid 86 arrived.
* **equip_id 6 (`LOG_EQUIP_ID_LBS`) is empty**, `LAST_CODE_DEFAULT = 0`. Every
  real GNSS log code is under equip_id 1, which the blanket log mask already
  enables. A per-equip-6 mask would buy nothing.

## Where the missing text actually went: Qshrink 4

Of 55 604 frames in the F3 capture, **28 546 are `0x99`** —
`DIAG_QSR4_EXT_MSG_TERSE_F`, hash-compressed. 51% of this modem's F3 output,
and it carries no ssid and no text: byte 1 is `0x02`, bytes 4-11 a timestamp,
bytes 12-15 a 32-bit message index (670 distinct values, up to `0x7a125`), bytes
16-17 a constant build tag `0x3A1C`. Rendering it needs the build's string
database.

**That database is already on this phone and already extracted.**
`/readonly/firmware/image/strait/qdsp6m.qdb`, 5.8 MiB, `QDB` magic —
`rhodep-rfs-populate` copies it out of the `modem` partition, and
`modem-rfs-namespace-wip/HANDOFF.md` records it. `qdsp6m` is QDSP6-modem, this
exact MPSS build. Checking whether its embedded tag is `0x3A1C` is an offline
five-minute job, and if it matches, half the modem's diagnostic text becomes
readable — very likely including the NAV bring-up strings this whole
investigation is chasing.

## What to do next, revised

1. **Fix the deframer** in `rhodep-diag-decode.py`: unescape, unwrap `0x98`,
   read the code at offset 6. Zero device time. Regression target: 3831 GNSS
   packets and equip_ids `{1,4,5,7,11}` out of the first capture.
2. **Match `qdsp6m.qdb` against tag `0x3A1C`.** Zero device time, highest
   payoff of anything left.
3. **Decode `LOG_CGPS_IPC_DATA_C` (0x1375)** against the 39 fix cycles, for
   which run A is ground truth.
4. **Watch for `LOG_GNSS_MC_F3_GENERIC_C` (0x1950)**, the GNSS mission
   controller's own F3-inside-a-log-packet wrapper, and for
   `LOG_GNSS_CC_CCP_STATUS_C` / `_DCP_STATUS_C` (0x1c67/0x1c68), the correlator
   controller. If "Failed to enable Nav SDD HW" is emitted at all, 0x1950 is a
   likelier carrier than 0x79.
5. **Print the whole build-mask report**, not just its first range —
   `handle_cntl()` currently reads one `<HH` and stops. The levels for ssids 19,
   46, 50, 81, 86 and 113 say directly whether each positioning component's F3
   was compiled in.

---

# The PS_HOLD control, finally run

`rhodep-pon-reason` and the three-byte comparison had never been calibrated
against a clean reboot. Run on a boot whose `bootreason` is `reboot` and whose
`powerup_reason` is `0x00004000` — a deliberate `systemctl reboot`:

	0x8C5 POFF_REASON1     = 02    bit 1 = PS_HOLD
	0x8C7 PON_OFF_REASON   = 80    POFF sequence ran; FAULT and S3 did not
	0x8C0 PON_REASON1      = 41    hard reset + cable
	0x88D DVDD_RB_SPARE    = 00
	0x8C2 WARM_RESET_REASON1 = 00
	0x8C4                  = 80
	0x85A PS_HOLD_RST_CTL  = 01    warm reset armed for next time
	0x85B                  = 80    enable

**`0x8C5 = 02` and `0x8C7 = 80` are identical to the values recorded after the
GNSS event.** A deliberate reboot produces the same reading. So "the PMIC
recorded PS_HOLD, therefore something inside the SoC asked to be shut down"
distinguishes nothing: it is equally true of a user typing `reboot`. Every
sentence in this repo of that shape must be read down to **"the shutdown went
through the normal mechanism, and the PMIC's own protection hardware did not
initiate it"**. That second half is a real negative and it still stands — bits 6
and 5 of `0x8C7` are clear, so no UVLO, OVLO, over-temperature, MBG fault,
FAULT_N, PBS watchdog or S3 sequence — but it is a statement about the PMIC, not
an attribution to anything inside the chip.

The three bytes that *do* differ between a clean reboot and the event
(`0x88D`, `0x8C2`, `0x8C4`) are all plausibly one measurement, not three:
`0x8C2` is `WARM_RESET_REASON1` and non-zero means the PMIC calls the boot warm;
`0x88D` is `PON_DVDD_RB_SPARE`, a scratch byte in the DVDD reset domain, which a
hard reset clears and a warm reset preserves; `0x8C4` is unidentified in every
public register map but its one-hot `0x80`/`0x40` matches the only published
bit-field of that shape, `PON_PBL_STATUS` DVDD/XVDD. Reset *type* is programmed
by Linux's own reboot notifier into `0x85A` before the pin drops — so the
signature most likely reads "Linux's reboot notifier did not run", which
`bootreason=watchdog` and the empty pstore already said. The run that would
settle it costs one reboot: boot once with `reboot=w` and then reboot cleanly.
If that reproduces `00/02/40`, the signature is about reset type and nothing
else.

## The PBS event log does not exist on this build

Qualcomm's PMICs can keep a PON event FIFO written by the PBS sequencer, with
reset trigger, reset type, fault reasons, the PBS program counter, and a warm
reset count — the only source on the device that names *who* asked. On this
phone it is PMK8350 at SID 6, SDAM 5, and it is reachable without any new code:
`/sys/kernel/debug/regmap/0-06/registers`.

	0x7446 push pointer = 4b
	0x744b-0x74bf FIFO  = all zero

The push pointer sits at `FIFO_DATA_START`. **Nothing has ever been written.**
Consistent with the vendor device tree, which disables `pon_pbs@800` on this
PMIC. Closed, for two minutes of work and no risk.
