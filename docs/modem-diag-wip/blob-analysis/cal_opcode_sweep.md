# cal_opcode_sweep — Empirical sweep for the FTM command that opens the RF cal-mode gate (gp+0x740 = 2)

**Target:** SM6375 / Moto G82 5G · MPSS.HI.4.3.4-00494-MANNAR_GEN_PACK-1.24452.133
**Goal:** find the FTM mode-set / set-calibration-state command that flips the modem global
`gp+0x740` (VA `0xcbf4f740`, PA `0x96f4f740`) to `2` ("RF cal mode active"), which populates the
active-carrier pointer `0xca79c494` and makes `IQ_CAPTURE`/`RX_MEASURE`/`QUERY_RRA` return real data
instead of the baseline `error_code=0, num_captures=0`.

**Safety:** RX-only / controlled antenna. This sweep sends ONLY mode-set / calibration-state / query
commands (control, non-radiating). NO TX_CONTROL, NO TX_MEASURE, NO RADIO_CONFIGURE-with-real-tuning
loops. Batches ≤6 packets; device liveness checked between batches; SSR noted as a data point and
allowed to recover (~60s).

**Tooling:** `/home/kali/rhodep-diag-server.py --raw-seq ... --cmd-peer --dump ... ` → CMD replies +
F3 in `.log`, full untruncated replies in `<dump>.replies`; parsed by `rhodep-iq-parse.py`.

**Framing (verified):** `4b 0b <mode_id u16 LE> <cmd_id u16 LE> <data_len u16> <rsp_pkt_size u16> <body>`.
Reply: same echo + `<status u16 @off10>` + `<error_code u32 @off14>` ... (then 2-byte CRC + `7e` HDLC).
A rejected cmd prefixes the reply with a status byte `0x13` (BAD_CMD). `DIAG_CONTROL_F` FTM = `290300`.

---

## Baseline (established from prior runs fullcal/qrra, re-confirmed this session)

| packet | reply status | note |
|--------|-------------|------|
| `290300` (DIAG_CONTROL_F = FTM mode 3) | `290300 81` | OK, enters FTM phone mode |
| `4b0b1400 0700 0200 4000 1d00` SET_MODE mode=20 cmd=7 body=u16 0x1d (LTE) | status word `0x0001` | **accepted, non-error, distinct** |
| `4b0b1400 8900 0100 0000 01` SET_CAL mode=20 cmd=137 state=1(u8) | status `0x0000` | OK |
| `4b0b1d00 0700 0200 4000 2c00` SET_MODE mode=29(LTE) cmd=7 | prefix `0x13` | **BAD_CMD rejected** |
| IQ_CAPTURE mode=105 cmd=6 (full TLV body) | `...00000000 00008000` | **error_code=0, num_captures=0** (baseline failure) |

Interpretation of the mode-105 reply tail `00000000 00008000`: `error_code=0` (u32 @14),
`num_captures=0`, `0x80` = rsp_pkt_size echo (NOT a capture count). This is the signal we need to flip.

---

## Results table

(status legend: 0x4b=accepted/no-effect, 0x13=BAD_CMD/BAD_PARM, 0x03+SSR=crash, 0x14=bad per-tech gate,
0x00xx=structured OK reply with status word. "capN" = num_captures after a probe IQ_CAPTURE.)

| # | candidate packet (hex) | meaning | reply status | new cal F3? | IQ cap after | notes |
|---|------------------------|---------|--------------|-------------|--------------|-------|
| B1 | `4b0b140089000100000002` | SET_CAL mode=20 cmd=137 state=2 (u8) | 0x4b accepted | no (only WLAN `cal_sm_mem_dpd`; no RXDCO) | 0 | same as state=1; no effect |
| B2 | `4b0b000089000100000002` | SET_CAL mode=0 cmd=137 state=2 | 0x14 (bad per-tech gate) | **YES — RXDCO (`cal_sm_rxdco_device.c`, "RxDCO Data Frm HWREG/CaldB/Restored")** when sent in the 5-mode batch | 0 | **cal state machine engaged** (never happens in control). See below. |
| B2 | `4b0b1d0089000100000002` | SET_CAL mode=29(LTE) cmd=137 state=2 | 0x13 BAD_CMD | (part of RXDCO-triggering batch) | 0 | |
| B2 | `4b0b1e0089000100000002` | SET_CAL mode=30(LTE_NS) cmd=137 state=2 | 0x13 BAD_CMD | (part of batch) | 0 | |
| B2 | `4b0b690089000100000002` | SET_CAL mode=105(RF_TEST) cmd=137 state=2 | 0x4b accepted | no (alone) | 0 | |
| B2 | `4b0b010089000100000002` | SET_CAL mode=1 cmd=137 state=2 | 0x14 | (part of batch) | 0 | |

**KEY FINDING (B2): `SET_CALIBRATION_STATE (cmd=137) state=2` engages the modem RXDCO cal state
machine.** Control runs (bare modem restart + DIAG_CONTROL_F only, even over a 90 s window covering
full boot) produce **ZERO** RXDCO F3. The 5-mode SET_CAL-state=2 batch reliably produces
`cal_sm_rxdco_device.c` F3 ("RxDCO Data Frm HWREG/CaldB of Chain", "RxDCO Restored Data", and the enum
dump `rxdco_cal_mode(0: Normal, 1: DTIM, 2: Comb, 7: DPD)`). The exact step it fires on varies between
runs (async cal state machine) — mode=0 and the mode=29→30 region both observed — confirming it's an
*asynchronous* kick, not a synchronous per-mode write. **This is the strongest "cal path engaged"
signal found; but IQ_CAPTURE still returns num_captures=0** (RXDCO cal running ≠ gp+0x740==2 + active
streaming carrier).

**Note on F3:** the cal-F3 that appears on every modem restart is **WLAN-side** (wcn39x0, qca61x0,
whal_wmac, wal_coex, `cal_sm_mem_dpd_device.c` in WCN context). The MODEM cal signal we must watch for
is specifically **RXDCO / `cal_sm_rxdco_device.c` / "RxDCO Data Frm HWREG"** and modem
`ftm_*`/`rflte_*`/`RFTEST` F3 — none of which appeared for B1.

