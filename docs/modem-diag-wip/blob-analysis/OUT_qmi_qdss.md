# OUT_qmi_qdss — QMI / QDSS-STM / NV-EFS interfaces vs. the RF cal-mode gate

**Target:** Qualcomm SM6375 baseband, MPSS.HI.4.3.4-00494-MANNAR_GEN_PACK (Moto G82 rhodep), Hexagon/QDSP6 v66.
**Scope:** Static disassembly / string analysis only, device's own firmware, legitimate interop for mainline Linux port.
**Question:** Is there ANY AP-reachable interface (QMI service, QDSS/STM trace, NV/EFS) that returns **receiver sample data OR per-frequency signal power** WITHOUT the special RF-cal-mode gate (`memb(0xcbf4f740)==2` / carrier ptr `0xca79c494`)?

**Legend:** FACT = byte/string/instruction read in this image (VA cited) · INFERENCE = deduction with hard basis · UNKNOWN = only resolvable live.

**Material used (all extracted, non-.tgz):**
- `/tmp/mre/clade_dec_36m.bin` (VA 0xd8000000, CLADE code), `/tmp/mre/clade_exc_high.bin` (VA 0xd0000000).
- `/tmp/modemre/modem.b21` (rodata, VA base **0xc3553000**) — QMI service handler tables, filenames, enums. **This is where the QMI/QDSS strings live.**
- `/tmp/modemre/seg27_dec.bin` (RF rodata, VA base **0xce480000**) — RF/ML1/searcher strings.
- Prior findings: `MASTER_MODEM_MAP.md`, `map_modes_nv_power.md`, `map_diag_core.md`.

---

## 0. TL;DR / VERDICT

**No interface returns raw receiver IQ samples without the cal-mode gate.** The only raw-IQ path in the
firmware is FTM IQ_CAPTURE, which is squarely inside the cal-gated FTM/RFTEST subsystem already mapped by prior work.

**BUT — per-frequency signal *power* (RSSI/RSRP/RSRQ vs frequency) IS reachable WITHOUT the cal-mode gate**, via the
**QMI NAS service** running in **ONLINE mode**. NAS exposes `perform_network_scan`, `get_cell_location_info`
(neighbor cells with per-EARFCN RSRP/RSRQ), `get_signal_strength`/`get_sig_info`, `get_rf_band_info`, and
`force_lte_scan`. These sit on top of the online ML1 searcher/measurement machinery (LTE BPLMN band-scan `bscan_cnf`,
GSM searcher power-scan `pscan_results_buffer`, `common_rssi_ind` → dBm) which is **architecturally separate from,
and mutually exclusive with, the FTM cal path** (guard `Attempt to access FTM variables in ONLINE MODE`).

- **QMI (raw samples):** NO service delivers receiver samples. FACT.
- **QMI (per-frequency power):** YES — NAS network scan / neighbor-cell / signal-info, ONLINE, NOT cal-gated. FACT (handlers) + INFERENCE (message semantics = standard QMI NAS).
- **QDSS/STM sample-to-trace route:** NO. QDSS here is standard CoreSight software/profiling trace; the "STM" in ML1 is a *software State Machine* framework, not the trace bus. No IQ/ADC→ATB routing exists. FACT.
- **NV/EFS over the AP transport:** No stored raw-sample dumps exist in NV/EFS; no arbitrary RAM/sample read path. Prior "EFS-over-DIAG not registered in this PD" confirmed. FACT.

**Bottom line:** raw *samples* remain gated; **coarse spectrum/power (per-frequency RSSI/RSRP) is fully reachable via QMI-NAS in normal ONLINE operation, no special mode required.**

---

## 1. QMI SERVICES REGISTERED IN THIS MODEM PD

### 1.1 Enumeration (FACT — `qmi_svc_hdlr_ftype` tables in b21)

Distinct QMI service handler prefixes present (count = number of handler strings), from `modem.b21`:

| prefix | service | # handlers | RF-relevant? |
|--------|---------|-----------:|--------------|
| `qmi_nasi_` | **NAS (Network Access Service)** | 401 | **YES — signal/scan/RF measurements** |
| `qmi_wdsi_` | WDS (Wireless Data) | 138 | no |
| `qmi_uimi_` | UIM (SIM) | 75 | no |
| `qmi_voicei_` | VOICE | 62 | no |
| `qmi_wmsi_` | WMS (SMS) | 44 | no (`raw_read/send` = SMS PDUs, not RF) |
| `qmi_ctli_` | CTL (control) | 33 | no |
| `qmi_qosi_` | QOS | 25 | no |
| `qmi_pbmi_` | PBM (phonebook) | 23 | no |
| `qmi_cati_` | CAT | 18 | no |
| `qmi_wdai_` | WDA (data agent) | 17 | no |
| `qmi_dsdi_` | DSD (dual-SIM data) | 12 | no |
| `qmi_ati_` | AT | 6 | no |
| `qmi_dfci_` | DFC | 3 | no |
| `qmi_csi_`/`qmi_cci_` | QMI framework (server/client infra) | 2/1 | infra |
| `qmi_dmsi_` | DMS (device mgmt) | 1 string only | operating-mode (partial in this PD) |

**Service IDs (INFERENCE — standard Qualcomm QMUX service IDs; numeric enum is a compiled constant, not in the string table):**
NAS = **0x03**, WDS = 0x01, DMS = 0x02, WMS = 0x05, PBM = 0x09, LOC = 0x10, UIM = 0x0B, VOICE = 0x09→? (voice), QOS = 0x04, CTL = 0x00. The exact int per service in *this* build is UNKNOWN-static (same reason FTM command IDs are — registration-order/compiled). But the well-known assignment is stable across Qualcomm MPSS.

**No dedicated SAR / COEX / RFRPE / TEST QMI *service handler table* is registered in this PD** (searched `qmi_(sar|coex|rfrpe|test|csvt)i_` — zero hits). See §1.4 for RFRPE/COEX which exist as *tasks/filenames*, not as QMI services hosted here.

### 1.2 The RF-measurement surface = QMI NAS (FACT — b21 handler strings)

All are `(qmi_svc_hdlr_ftype) qmi_nasi_*` dispatch entries in b21. The RF-relevant subset:

| NAS handler | what it returns | per-freq power? | cal-gated? |
|-------------|-----------------|-----------------|-----------|
| `qmi_nasi_get_signal_strength` @b21 0x2182be | serving RSSI/RSRP/RSRQ/SINR/ECIO | serving only | **NO** |
| `qmi_nasi_get_sig_info` @b21 0x2189b3 | signal info (multi-RAT dBm) | serving only | **NO** |
| `qmi_nasi_config_sig_info` / `_sig_info2` @0x2189de/0x218d7a | threshold-triggered signal reporting | serving | **NO** |
| `qmi_nasi_perform_network_scan` @0x2182f0 | **detected cells across bands: RAT + MCC/MNC + per-cell signal** | **YES (per detected cell/freq)** | **NO** |
| `qmi_nasi_perform_incremental_network_scan` @0x219212 | incremental variant | **YES** | **NO** |
| `qmi_nasi_abort_scan` @0x219aba | abort ongoing scan | — | NO |
| `qmi_nasi_force_lte_scan` @0x219e53 | force an LTE scan | **YES (LTE)** | **NO** |
| `qmi_nasi_get_cell_location_info` @ (b21 handler present) | **serving + neighbor cells: EARFCN + RSRP/RSRQ per neighbor** | **YES (per-EARFCN)** | **NO** |
| `qmi_nasi_get_lte_cphy_ca_info` | CPHY carrier-aggregation info (per-carrier) | per-CC | **NO** |
| `qmi_nasi_get_rf_band_info` @0x218613 | active band + channel (serving) | serving band | **NO** |
| `qmi_nasi_get_rf_availability` @0x219c65 | RF available/unavailable indication | boolean | **NO** |
| `qmi_nasi_get_arfcn_list` | ARFCN list (GERAN) | freq list | **NO** |
| `qmi_nasi_get_serving_cell_sib` @ (resp 0x217df0) | serving cell SIB | serving | **NO** |

Response generators also present (FACT, b21): `qmi_nasi_generate_perform_network_scan_resp` (0x217baf),
`_resp_pci_scan` (0x217b7a), `qmi_nasi_generate_perform_incremental_network_scan_ind/resp` (0x217b5.., 0x21b5a4),
`qmi_nas_send_rf_band_info_ind`, `qmi_nasi_generate_rf_availability_ind` (0x21b74e).

**Richest per-frequency power result = `get_cell_location_info` and `perform_network_scan`:** the underlying data
(`4g_cell_neighbor_info`, `neighbors_count` — FACT strings @b21) is the set of {EARFCN, PCI, RSRP, RSRQ} for every
cell the receiver can currently detect — i.e. a **per-frequency, per-cell power map**, produced by the online searcher.

### 1.3 Is any of the above cal-gated? — NO (FACT + INFERENCE)

**Mutual exclusion (FACT):** `Attempt to access FTM variables in ONLINE MODE` (seg27 @0xce6d59e0, ×4). NAS and the
whole online protocol stack run in **eOPRT_MODE_ONLINE**; the FTM cal subsystem (which owns `gp+0x740==2` /
`0xca79c494`) runs in **eOPRT_MODE_FTM**. They are the two arms of `sys_oprt_mode_e_type` (see `map_modes_nv_power.md`
§1) and the firmware asserts if you cross them.

**No shared gate (FACT/INFERENCE):**
- The cal gate `0xcbf4f740` and carrier ptr `0xca79c494` are referenced only by the FTM/RFTEST code at 0xd82xxxxx
  (prior work: getter 0xd827923c, ~96 FTM measure/capture call-sites). None of that is the NAS/online path.
- The online RSSI→dBm producers are different code: `nr5g_ml1_common_rssi_ind.c`, `lte_ml1_common_rssi_ind.c`,
  `rfmgr_wb_rssi_report_config`, and the searcher (`RxD: ... TRUE_RSSI_PRx:%d (= %d dBm)` @b21). These feed the NAS
  QMI responses via the online measurement DB, not via the FTM carrier ptr. **FACT (distinct code/strings) + INFERENCE (no edge to the cal gate).**
- NAS scan uses the online searcher (§3), which programs the RX chain through the *mission* wakeup path
  (`rflte_mc_rx_wakeup` @0xce6e3ce0), NOT the FTM wakeup (`rflte_ftm_mc_wakeup` @0xce6e08a8). The mission path does
  not require `gp+0x740==2`. **FACT (two distinct wakeup fns) + INFERENCE.**

### 1.4 SAR / COEX / RFRPE / "test" — present but NOT a usable RF-readback QMI here (FACT)

- **RFRPE** (`rf_qmi_rfrpe_svc.c` @b21 0x1680f; `rfrpe`@0x2570a next to `QMI_TASK_PRI_ORDER`; `RFRPE task info not
  found` seg27 @0x255278): a real RF-QMI service **task** exists in the RF subsystem. Its message set is compiled
  (not in strings) → **UNKNOWN-static**. It is adjacent to `rfmeas_mc.c` (RF measurement MC). It is plausibly an
  RF-measurement QMI, but (a) its handlers are not in the QMI dispatch table of this PD's rodata, (b) whether it
  reads/needs the cal gate is UNKNOWN-static. **Not a confirmed ungated readback.** Candidate for live probing only.
- **COEX** (`COEX_SERVICE` @b21 0xa587cc, `coex_interface.c`, `COEX_POWER_IND_CMD` @0x2449a6): coexistence manager,
  exchanges Tx power/priority with WLAN/BT — an *indication of own Tx activity*, not a receiver spectrum readback. Not useful for RX power/spectrum.
- **SAR**: only NV item files (`/nv/item_files/mcs/lmtsmgr/sar/*` @b21 0xa59a06+) — a Tx power-limit table, no RX read.
- **No `qmi_testi_` / CSVT test service** hosted in this PD (zero handler hits).

---

## 2. QDSS / STM TRACE — is any DSP/PHY sample stream routed to the trace bus?

### 2.1 Two different "STM" — do not conflate (FACT)

- **CoreSight STM (hardware trace bus):** `stm.c` @b21 0x19a69; QDSS topology present in full
  (`qdss`, `qdss_control`, `qdss_ctrl_q6b_i`, `QDSS_APSS_DL_TPDM0/1...`, `TPDA`, `CXATBFUNNEL`, `CSCTI`,
  `qdss_etrbytecnt_irq` @0x363e1, `QDSS_SOC_DBG` @0x3542d). This is the standard on-SoC debug/profiling fabric.
- **ML1 "STM" = software State Machine framework:** `stm_instance_activate(...)==STM_SUCCESS`,
  `NR5G_ML1_MGR_SM`, `NR5G_ML1_PHYCHAN_BPLMN_STM`, `NR5G_ML1_IQ_CAPTURE_STM` (seg27) — these are *state machines*
  (states + inputs + STM_SUCCESS return), **not** trace. `NR5G_ML1_IQ_CAPTURE_STM` is the IQ-capture *state machine*,
  part of the cal-gated FTM path — not a trace sink.

### 2.2 What QDSS actually traces here (FACT)

- All modem-side QDSS use is **software event / profiling trace**: `*_stm_if.c` / `*_stm_profile.c`
  (e.g. `nr5g_ml1_srch_activity_stm_if.c`, `lte_ml1_common_stm_profile.c`) = ML1 state-machine activity profiling;
  `tracer_simple` @0x25c7b, `QDSS tracer test data %d`, `TRACER PACKET Data RX on channel %s` — software tracer packets.
- **QDSS QMI control service** exists: `qdss_qmi_ctrl_svc.c` / `_q6b.c` @b21 0xfd33/0xfd47, task `qdssc_svc_task`,
  `QDSS_PMON_ENABLED`, `Assertion (qdssEnabled <= 1)`. This is a **control** service (enable/route the trace fabric),
  **not** a data-return service — it does not hand receiver samples to the AP.

### 2.3 Is there an RF/PHY-sample → trace route? — NO (FACT)

- Searched b21 + seg27 for any IQ/sample/ADC ↔ QDSS/STM/ATB coupling: **none**. The only near-hits are the RIF-MMAP
  register-block name table: `SYMPROC_TRACE_TRACE_MODEM_TRACE_AGGREGATOR_RIF_MMAP`, `DEMOD_LITE_TRACE_...`,
  `DEMBACK_NR/LTE_TRACE_AGGR_...MODEM_TRACE_AGGREGATOR_RIF_MMAP`, terminating at
  `TRACE_MODEM_TRACE_ENDPOINT_RIF_MMAP` (all @b21 ~0x3d9xx–0x3e1xx). These are the **modem's internal trace-aggregator
  hardware block names** (a debug fabric that aggregates *software/event* traces from PHY blocks). There is **no
  string or route indicating raw IQ/ADC samples are pushed onto this aggregator or onto the QDSS ATB toward the AP.**
  The modem trace aggregator is an on-modem endpoint, not an AP-readable IQ tap.
- Consequently, **the AP cannot read receiver samples from the QDSS/CoreSight side** — there is no sample-to-trace route to read.

**QDSS verdict: no sample-trace route exists. QDSS carries software event/profiling trace only. FACT.**

---

## 3. PER-FREQUENCY POWER / BAND SCAN WITHOUT CAL-MODE — yes, via the online searcher

Even without raw IQ, the firmware has a full **online (mission-mode) band/power scan** that returns power vs frequency,
driven by CM/NAS, **not** by FTM cal:

### 3.1 The online scan machinery (FACT — seg27 strings)

- **LTE band/PLMN scan (CPHY):** `LTE_ML1_BPLMN_ONLY_SCAN`, `bscan_cnf`/`sscan_cnf` callbacks
  (`lte_ml1_sm_callbacks.plmn.bscan_cnf`), `LTE_CPHY_BANDSCAN_NUM_CANDIDATES`, `acq_stage1_req.freq_num`,
  `parallel_acq_fscan_efs_ptr`. Returns candidate cells {EARFCN, PCI, power} across a band.
- **LTE FSCAN / frequency scan:** `LTE_LL1_FSCAN_IDLE`, `lfs_req`/`ffs_req` with `center_freq`, `ffs_bw`/`lfs_bw`,
  `capture_time_per_spectrum` (`FSCAN_CAPTURE_TIME_MIN/MAX`) — an actual per-frequency spectrum-capture-window scan
  in the online LL1. (`lfs`/`ffs` = LTE/final frequency scan.)
- **GSM searcher power scan:** `srchcrgsm_pwr_scan_cmd`, `SRCHCM:PwrScan ReqWinSz ... for RSSI`,
  `pscan_results_buffer_ptr`, `temp_pscan_db_ptr`; cached in NV (`/nv/item_files/modem/geran/pscan_results_reuse_time_secs`).
  This is a literal **per-ARFCN RSSI sweep**.
- **RSSI → dBm producers:** `RxD: ... RAW_RSSI_PRx / TRUE_RSSI_PRx:%d (= %d dBm)` (@b21),
  `nr5g_ml1_common_rssi_ind.c`, `lte_ml1_common_rssi_ind.c`, `rfmgr_wb_rssi_report_config`,
  per-carrier `Total RSSI` / `InbandRSSI` (NR RX IUSS, seg27).
- **CM network survey:** `=CM= GET_NET: frequency scan already in progress!` (seg27 @0x1cd470) — the Call-Manager
  manual-network-search path (the thing behind `perform_network_scan`).

### 3.2 Does it need cal-mode? — NO (FACT/INFERENCE)

All of the above runs in **ONLINE** mode under CM/NAS. It uses the *mission* RX wakeup (`rflte_mc_rx_wakeup`
@0xce6e3ce0), not the FTM cal wakeup, and never touches `gp+0x740`/`0xca79c494`. It is the exact opposite arm of the
FTM/ONLINE guard. So a per-frequency RSSI/power scan is available **in the normal operating state, no special mode.**
**FACT (strings + mode separation) + INFERENCE (no edge to cal gate).**

**Resolution/limits (INFERENCE):** this yields **processed power** (RSSI/RSRP/RSRQ per cell/EARFCN, dBm), at
cell-search granularity — not a fine dense spectrogram and not raw IQ. Good enough for a "which frequencies have
energy / how strong" survey; not enough to see arbitrary non-cellular emitters at arbitrary resolution.

---

## 4. EFS / NV OVER THE AP-REACHABLE TRANSPORT

### 4.1 EFS/NV over DIAG — not registered in this PD (CONFIRMED, FACT)

Prior work verified (re-confirmed from `map_diag_core.md`):
- PEEK/POKE (0x02–0x07) and NV/EFS peek **not registered**; **EFS2 (subsys 20) absent** from the 67 DIAG nodes.
- A string `LFW_MAX_EFS_DIAG_CMD_PAYLOAD_BYTE` exists → an EFS-over-DIAG path exists in the *firmware family*, but is
  **not registered as a DIAG subsys in this modem PD** (it belongs to Root-PD/APPS). Not reachable from this channel.
- No arbitrary RAM/NV/EFS read command in this PD. FACT.

### 4.2 EFS/NV over QMI — no stored raw-sample dump path (FACT)

- The QMI services in this PD (§1.1) have no generic NV/EFS *file read* handler that would surface sample dumps
  (`qmi_dmsi_` is essentially absent here — only `qmi_dmsi_set_ap_version`; the NV-read DMS handlers are not in this
  PD's rodata). No PDC/persistent-data file-read service is hosted here for arbitrary EFS reads of a sample dump.
- **What NV/EFS *does* store is calibration + config, not samples:** `RFNV_..._RX_CAL_OFFSET`, LNA offsets/switchpoints,
  `rfdevice_efs_get_*` (FEM/gain-comp), MCPM clock plan, `pscan_results` cache, coex/SAR tables
  (`/nv/item_files/mcs/...`). None of these are receiver sample data. There is **no NV/EFS item that is a raw IQ or
  spectrum dump** — the firmware writes captured IQ only to a runtime DDR memshare region (FTM path), never to NV/EFS.
- Therefore **no file/NV read path (DIAG or QMI) surfaces stored receiver samples.** FACT.

---

## 5. DID ANYTHING READ THE GATE POINTER 0xca79c494 / MODE BYTE 0xcbf4f740?

- Direct byte search for the literals `0xca79c494`, `0xcbf4f740`, `0xcbf4f000`, `0xca79c480` in the code blobs =
  **0 hits** — expected, because Hexagon encodes these via `immext` (split), not raw 32-bit words (prior work resolved
  them with `scan_ptr_access.py`). All confirmed readers of these live in the FTM/RFTEST code (0xd82xxxxx), which is
  the cal path, per prior findings.
- **None of the QMI/NAS/QDSS/searcher code identified here is in that FTM code region.** The QMI services and online
  searcher are the online protocol stack (b21 rodata + out-of-MBN ML1 code), a different subsystem that by design
  runs in the ONLINE mode which is *mutually exclusive* with the FTM cal mode that owns those gates.
- So: **the QMI/NAS/QDSS interfaces do NOT read the gated pointer or the mode byte.** FACT (region separation) + INFERENCE (mode exclusion).

---

## 6. VERDICT

**Is any receiver data reachable via QMI/QDSS/NV without the special cal mode (byte==2)?**

| capability | reachable w/o cal-mode? | via | evidence tag |
|-----------|------------------------|-----|--------------|
| **Raw receiver IQ samples** | **NO** | only FTM IQ_CAPTURE, which is cal-gated | FACT |
| **Per-frequency signal power (RSSI/RSRP/RSRQ vs EARFCN/ARFCN)** | **YES** | QMI **NAS**: `perform_network_scan`, `get_cell_location_info`, `force_lte_scan`, `get_sig_info`/`get_signal_strength`, `get_arfcn_list` — ONLINE mode | FACT (handlers) + INFERENCE (msg semantics) |
| **Serving-cell signal (dBm)** | **YES** | QMI NAS `get_signal_strength`/`get_sig_info`/`get_rf_band_info` | FACT + INFERENCE |
| **Spectrum via QDSS/STM trace** | **NO** | no sample→trace route exists; QDSS = SW/profiling trace only | FACT |
| **Sample dump via NV/EFS** | **NO** | NV/EFS store cal/config, not samples; no file-read path in this PD | FACT |
| **RFRPE / test QMI lower-level RF readback** | **UNKNOWN** | `rf_qmi_rfrpe_svc` task exists but message set + gating are compiled/out-of-MBN | UNKNOWN (live probe only) |

**Summary for the port:**
1. **Receiver *samples* stay locked behind the cal-mode gate** — nothing in QMI, QDSS, or NV/EFS bypasses it. The
   only IQ producer is FTM IQ_CAPTURE (cal-gated). QDSS carries no samples; NV/EFS store no samples.
2. **Coarse spectrum / per-frequency power IS available in normal operation, no special mode**, through the standard
   **QMI NAS network-scan / neighbor-cell / signal-info** messages, which are backed by the online searcher's band/
   power-scan machinery (LTE BPLMN/FSCAN, GSM pscan, common RSSI→dBm). This is the honest "win": if the goal only
   needs *where is there energy and how strong* (a cell-search-grade survey per EARFCN/ARFCN in dBm), it is reachable
   today via QMI on an ONLINE modem, with no cal-mode, no gate, and no FTM.
3. The gap between the two is fundamental: **NAS gives processed power at cell-search granularity; raw IQ requires the
   FTM cal mode.** There is no third path (QDSS/NV) that closes it.
4. **One residual live-only lead:** the **RFRPE** RF-QMI service task (`rf_qmi_rfrpe_svc.c`, adjacent to `rfmeas_mc.c`).
   Its message set and whether it needs the cal gate are compiled constants / out-of-MBN → only a live QMI probe (or a
   RAM dump of the RF PD) can determine if it offers ungated lower-level RF readback. Do not assume it does.

**Honest negative where it matters:** *No ungated raw-sample interface exists.* The positive result is limited to
per-frequency **power** (not samples), via QMI-NAS.

---

## 7. REPRODUCE

```bash
# QMI service handler tables (b21, VA base 0xc3553000)
strings -a /tmp/modemre/modem.b21 | grep -oE "qmi_[a-z0-9]+i_" | sort | uniq -c | sort -rn
strings -a /tmp/modemre/modem.b21 | grep -E "\(qmi_svc_hdlr_ftype\) *qmi_nasi_" | sed 's/.*qmi_nasi_/nas_/' | sort -u \
  | grep -iE "sig|scan|rf|meas|cell|rssi|arfcn|band|serving|cphy"
# RFRPE / COEX / rfmeas
strings -t x /tmp/modemre/modem.b21 | grep -iE "rfrpe|coex_service|rfmeas_mc"
# QDSS / STM (b21) — control svc + coresight topology, and the SW state-machine STM
strings -t x /tmp/modemre/modem.b21 | grep -iE "qdss|coresight|\bstm\b|tpdm|tpda|funnel|tracer|trace_aggregator|endpoint"
strings -a /tmp/modemre/seg27_dec.bin | grep -iE "stm_instance_(activate|deactivate)|STM_SUCCESS|IQ_CAPTURE_STM"
# online per-freq power scan (seg27, VA base 0xce480000)
strings -t x /tmp/modemre/seg27_dec.bin | grep -iE "fscan|lfs_req|ffs_req|capture_time_per_spectrum|bplmn.*scan|pwr_scan|pscan|TRUE_RSSI|common_rssi_ind"
# FTM/ONLINE mutual exclusion guard
strings -a /tmp/modemre/seg27_dec.bin | grep -iE "Attempt to access FTM variables in ONLINE MODE|Phone should be in online mode"
# confirm cal-gate literals are NOT in the plain QMI/searcher region (immext-encoded in FTM only)
python3 -c "
for f in ['/tmp/mre/clade_dec_36m.bin','/tmp/mre/clade_exc_high.bin']:
    d=open(f,'rb').read()
    for lit in [0xcbf4f740,0xca79c494]:
        print(f, hex(lit), 'raw-literal count=', d.count(lit.to_bytes(4,'little')))
"
```
