# map_modes_nv_power — Operating modes / NV+cal storage / MCPM clocks-power / device wakeup / RFC — SM6375 (MPSS.HI.4.3.4)

**Target:** SM6375 / Moto G82 5G · MPSS.HI.4.3.4-00494-MANNAR_GEN_PACK-1.24452.133 · Hexagon/QDSP6 v66
**Objetivo:** mapear la **infraestructura que rodea al RF** para un driver Linux legítimo — operating modes, almacenamiento NV/cal, MCPM (clocks/power), device wakeup del modem, y RFC (RF card).

**Imágenes / herramientas:**
- `seg27_dec.bin` = `modem.b27` descomprimido (ZLIB), **VA base 0xce480000** — rodata RF (RFLTE/RFLM/RFDEVICE/MCPM + LTE-ML1). **TODOS los strings/asserts de MCPM/NV/RFC/sleep-mgr viven acá.**
- `modem.b21` — rodata, **VA base 0xc3553000** — enums (`eOPRT_MODE_*`), nombres de clientes MCPM, UMIDs.
- `clade_dec_full.bin` (VA 0xd8000000, 10 MB) / `clade_dec.bin` (..0xdb397464) — código FTM/DIAG dispatch (CLADE-descomp). `dis.sh <va> <len>`.
- `find_refs.py` + `_fns.pkl` / `_idx.txt`.

**Leyenda:** **FACT** = string/instrucción verificada byte-a-byte (VA citada) · **INFERENCE** = deducción con base dura · **UNKNOWN** = sólo resoluble en vivo / con blob ausente.

**Cross-ref:** `enter_mode_path.md`, `rf_cal_mode_gates.md`, `map_msgr_ipc.md`, `map_rf_rx.md`, `map_rflte_code.md`, `map_segments.md`.

> **Límite estructural (heredado, confirmado):** el **código** RFLTE/RFLM/RFDEVICE/MCPM **no está en ningún blob descomprimido** (vive en el pool dlpager 0xd4400000..0xd6539000, mapa de páginas ausente del MBN — ver `map_rflte_code.md §0`, `map_segments.md §4.2`). Por eso este mapa es **completo a nivel de contrato (nombres de función/campo/assert/enum, todo FACT-de-string)** pero **los cuerpos de las funciones RF/MCPM/NV/RFC son UNKNOWN-body**. El código disassemblable (0xd8xxxxxx) es el dispatch FTM/DIAG, que **llama** a estos subsistemas por `callr`/MSGR resueltos en runtime.

---

## 0. TL;DR (leer primero)

1. **Operating modes** = enum Qualcomm `sys_oprt_mode_e_type` — verificado en b21 como `eOPRT_MODE_*` (0xc4231805..0xc4231900). Los relevantes: **ONLINE, FTM (factory-test), OFFLINE, LPM (low power), RESET/RESET_MODEM, PWROFF**. Transición: **QMI DMS `set_operating_mode` → CM (`cm_ph_cmd_pref_change_req` / `cmph_send_oprt_mode`) → MMOC** que arranca/para los subsistemas de protocolo y el RF. FTM setea un **flag global de modo (FTM/CAL vs ONLINE)** que particiona el acceso RF (guard `Attempt to access FTM variables in ONLINE MODE` @0xce6d59e0). **FACT (enum+strings) + INFERENCE (cadena canónica).**
2. **NV/cal:** el modem lee NV via **NVMGR** + **`rflte_nv_*`** (items **RFNV_*** con **NV uncompress**, @0xce6eb0b8) y EFS via **`rfdevice_efs_get_*`**. Cal RX crítica = **`RFNV_DATA_TYPE_STAND_ALONE_RX_CAL_OFFSET_V*`** (one-per-RFM-path, @0xce6d4db0) + **LNA gain offsets/switchpoints** (`rflte_dm_rxagc_config_update_switchpoints_and_lna_offsets_in_dm` @0xce6f0960) + **rx static data / freq comp** (`rflte_nv_process_rx_static_data` @0xce6eb430). **FACT.**
3. **MCPM** (Modem Clock and Power Manager): sub-driver completo con clientes por tech (`mcpm_rf`, `mcpm_rf_cal` @b21). Vota clocks/rails via **NPA** (`/clk/cpu`, `/clk/mss/*`, `/icb/arbiter`). El recurso RF crítico para RX es **QLINK (RFFE/transceiver bus PHY)** — `MCPM_RESRC_QLINK` / `MCPM_RESRC_WTR_QLINK` (@0xce767158) sobre el rail **WMSS_CX** + **qLink Ref clk source** (@0xce766928). El gate del enter_mode ML1 (`mcpm_req.return_trans_id != 0` @0xce8bd818) exige una transacción MCPM aceptada. **FACT.**
4. **Device wakeup RX:** `rfdevice_sleep_manager_wakeup_devices_rx` (@0xce6dd840) genera un **script RFFE de writes inmediatos** (`imm_write_script_ptr`) que saca SDR735/LNA/ASM de sleep. Lo dispara `rflte_ftm_mc_wakeup` (@0xce6e08a8, path FTM) o `rflte_mc_rx_wakeup` (@0xce6e3ce0, path mission). Tras el wakeup se espera **Qlink up** (`rflte_qlink_status_timer_cb`, 3ms/6ms @0xce6e3e60). **FACT.**
5. **RFC (RF card):** `rfc_intf` (singleton) construye las tablas de HW RF del **rhodep** (Rhode = board): `rfc_rfm_device_tbl` (device→wrapper), `sig_path` property table, `rfc_get_used_sid_table` (device→bus→USID RFFE @0xce6d49d0), GPIOs (`rfc_common_set_gpio` @0xce6fa048). Define bandas/paths/GPIOs/RFFE del board específico. **FACT.**
6. **Los gates RF `gp+0x740` (=0xcbf4f740, modo RF cal `==2`) y `gp+0x7000` (=0xcbf56000, ctx RF `!=0`)** los setea el **bring-up de modo cal** (que arranca `rfm_init` + MCPM + crea el ctx RF-instance lazy), **NO** el mode/QMI directamente ni el wakeup ni el RFC. Es el disparador **FTM_SET_MODE (cal)** el que enciende el subsistema RF-instance que puebla ambos. → reconcilia `rf_cal_mode_gates.md`. **FACT (gates) + INFERENCE (trigger).**

---

## 1. OPERATING MODES

### 1.1 El enum (FACT — b21 @0xc3553000)

`sys_oprt_mode_e_type` (nombre canónico Qualcomm). Strings verificados byte-a-byte, en orden de layout:

```
0xc4231805  eOPRT_MODE_PWROFF
0xc4231817  eOPRT_MODE_FTM               <<< FACTORY-TEST
0xc4231826  eOPRT_MODE_OFFLINE
0xc4231839  eOPRT_MODE_OFFLINE_AMPS
0xc4231851  eOPRT_MODE_OFFLINE_CDMA
0xc4231869  eOPRT_MODE_ONLINE            <<< ONLINE (servicio normal)
0xc423187b  eOPRT_MODE_LPM               <<< LOW POWER MODE (radio off, modem vivo)
0xc423188a  eOPRT_MODE_RESET
0xc423189b  eOPRT_MODE_NET_TEST_GW
0xc42318b2  eOPRT_MODE_OFFLINE_IF_NOT_FTM
0xc42318d0  eOPRT_MODE_PSEUDO_ONLINE
0xc42318e9  eOPRT_MODE_RESET_MODEM
0xc4231900  eOPRT_MODE_NONE
```
**FACT (13 valores).** Estos son los nombres de tabla de logging/QMI. Los valores numéricos del enum C real (`SYS_OPRT_MODE_PWROFF=0`, `_FTM`, `_OFFLINE`, `_OFFLINE_AMPS`, `_OFFLINE_CDMA`, `_ONLINE`, `_LPM`, `_RESET`, `_NET_TEST_GW`, `_OFFLINE_IF_NOT_FTM`, `_PSEUDO_ONLINE`, `_RESET_MODEM`, `_MAX`) siguen el layout Qualcomm estándar de `sys_oprt_mode_e_type`. **El valor numérico exacto por posición = UNKNOWN estático** (el string-table no lleva el int; es constante compilada), pero el orden de layout coincide con el enum canónico. **FACT (nombres/orden) + INFERENCE (valores por posición canónica).**

Mapeo a los 5 estados que pide la tarea:
| estado pedido | enum |
|---|---|
| **online** | `eOPRT_MODE_ONLINE` |
| **factory-test (FTM)** | `eOPRT_MODE_FTM` |
| **offline** | `eOPRT_MODE_OFFLINE` (+ variantes AMPS/CDMA/IF_NOT_FTM) |
| **LPM** | `eOPRT_MODE_LPM` |
| **reset** | `eOPRT_MODE_RESET` / `eOPRT_MODE_RESET_MODEM` |
| (power off) | `eOPRT_MODE_PWROFF` |

### 1.2 Quién los cambia — la cadena de transición (FACT strings + INFERENCE cadena)

```
QMI DMS set_operating_mode(oprt_mode)
   │   (b21: cmdbg_add_oprt_mode_cmd_to_buffer @0xc37599f8;
   │    ds_qmi_dms.c @0xc3575222; qmi_dmsi_* handlers)
   ▼
CM (Call Manager):  cmph_send_oprt_mode        (b21 @0xc375fbdc)
                    cm_ph_cmd_pref_change_req   (b21 @0xc40cc45e "cm_ph_cmd_pref_change_req failed")
   │                "oprt_mode = %d CMPH error %d" (@0xc3fdda71)
   ▼
MMOC (Multimode Controller):  "=MMOC= Phone should be in online mode" (@0xce64ebb0)
   │   coordina STOP_MODE_REQ / activate-deactivate a NAS/RRC de cada RAT
   │   (=MMOC= LTE-RRC did not respond to DEACTIVATE… @0xce64e9b8, etc.)
   ▼
tasks de protocolo (LTE-RRC/NR-RRC/…) y RF/ML1 arrancan (ONLINE) o paran (OFFLINE/LPM/FTM)
```
**FACT (todos los strings) + INFERENCE (que es la cadena canónica QMI-DMS→CM→MMOC de Qualcomm, consistente con estos strings).** Ver `map_msgr_ipc.md §4`.

- **DMS** (Device Management Service) expone `set_operating_mode`. El error path interno: `QMI_DMS_ERR_FATAL at line:%d` (@0xcea82c8b, seg27). Subsys DIAG candidato para modem-mgr/QMI = **0x5B** (map_diag_core §3). **FACT (strings) + INFERENCE (DMS↔0x5B).**
- **CM→MMOC** es el que realmente arranca/para subsistemas: MMOC manda `CM_STOP_MODE_REQ` a NAS y espera deactivate de cada RRC antes de conmutar. **FACT.**

### 1.3 Qué hace FACTORY-TEST distinto (FACT + INFERENCE)

**(a) El flag global de modo (el guard "Attempt to access FTM variables in ONLINE MODE"):**
```
0xce6d59e0  Attempt to access FTM variables in ONLINE MODE   (x4: +0xce6d5b28/0xce6d5b78/0xce6d64c0)
0xce6cf8c6  Assertion (p_tx_ctx->tx_info.rf_mode == (uint8)NR5G_LL1_CAL_FTM_RF_MODE_CAL) failed
0xce5bd511  Assertion (lte_LL1_get_ul_ftm_cal_mode(cxn_id) > 0) failed
```
Hay un **flag global de modo RF (FTM/CAL vs ONLINE)** que **particiona** el acceso a variables y al pipeline RF: el código FTM aborta si se lo llama en ONLINE y viceversa. En LL1 se lee como `lte_LL1_get_ul_ftm_cal_mode(cxn_id) > 0` (LTE) y `rf_mode == NR5G_LL1_CAL_FTM_RF_MODE_CAL` (NR). **FACT.**

- **Este flag corresponde al gate `gp+0x740` (=0xcbf4f740, enum {0,1,2})** de `rf_cal_mode_gates.md`: el valor **2 = "modo RF cal / non-signaling activo"**. Es el mismo concepto de "FTM/CAL RF mode" a nivel de driver RF. **INFERENCE fuerte (semántica idéntica; el gate 0x740 es un enum de 3 valores con logger mode→string, consistente con off/online/cal).**

**(b) Qué tasks arranca/para:**
- En **ONLINE:** la tarea **LTE-ML1** corre su SM de servicio; el RF se maneja por el par MSGR `LTE_ML1_RFMGR_*_REQ` ↔ `RFA_RF_LTE_*_CNF` (map_msgr_ipc §3). El activate del carrier lo dispara la **adquisición real de red**.
- En **FTM/CAL:** la ML1-online **no arranca su servicio**; el RF se maneja por el árbol **`rflte_ftm_*` / `rflte_dispatch_*`** con **wakeup/activate directos** (`rflte_ftm_mc_wakeup` @0xce6e08a8, `rflte_mc_carrier_activate` @0xce6e72c0), **sin el par REQ/CNF por MSGR**. Corre la **FTM task** (`FTM TASK RFGSM Mailbox` @b21 0xc37bca3a) como dispatcher de RFTEST/RFDEBUG. **FACT (módulos) + INFERENCE (task↔modo).**
- **Consecuencia:** en FTM el `enter_mode_cnf` del ML1 "no vuelve" (nunca se pide el req); el estado RF lo avanza el **activate FTM**. Ver `enter_mode_path.md`.

**(c) Cómo afecta al RF:** en FTM el RF entra en **modo cal / non-signaling**: gate `gp+0x740→2`, se crea el ctx RF-instance (`gp+0x7000!=0`), y el carrier-apply (`0xd81bd018`) publica el carrier ptr (`0xca79c494`). El pipeline de servicio (RRC/scheduler) está parado; sólo corre el driver RF bajo comando FTM directo. **FACT (gates) + INFERENCE (encadenado).**

---

## 2. NV / CAL STORAGE

### 2.1 Mecanismo de lectura NV (FACT)

**Dos backends:** (1) **NV items numerados** (RFNV) via **NVMGR** con descompresión, y (2) **EFS** (embedded file system, `/nv/item_files/...`).

**(a) NV items (RFNV):**
```
0xce6eb0b8  NV uncompress function failed for NV Item: %d.        <<< items comprimidos en NV
0xce6eb090  MALLOC Failed while copying NV Item: %d
0xce6eb0e8  MALLOC Failed for Local buffer NV Item: %d.
0xce6d4c68  [NVMGR]:Full stop!   / [NVMGR] Out of memory, could not allocate new index row (@0xce6d4d00)
0xce6d4db0  [NVMGR] One and only one instance of RFNV_DATA_TYPE_STAND_ALONE_RX_CAL_OFFSET_V* per RFM path
```
→ **NVMGR** mantiene un **index (rows) de NV items** por path; los items RFNV vienen **comprimidos** y se descomprimen al leerlos ("NV uncompress function"). Cada item se copia a un buffer local (malloc). El identificador es un **NV Item number (int)**. **FACT.**

**(b) EFS (para device/FEM y clock plans):**
```
0xce6dda80  rfdevice_efs_get_split_band_info()      : split_band_info por device
0xce6ddad8  rfdevice_efs_get_gain_comp_info()       : gain compensation (FEM)
0xce6ddb88  rfdevice_efs_get_pa_icq_info()          : PA Icq (TX)
0xce762d60  MCPM_DRV: NULL pointer to the EFS file!  : MCPM lee su clock plan de EFS
0xce762d88  MCPM_EFS: Invalid type format
```
→ Datos de FEM/device y el **clock plan de MCPM** viven en **EFS** (`/nv/item_files/...`). **FACT.**

**(c) MCFG (modem config, carrier-specific):** `MCFG Parsing Error %d` (@0xce760ae8), `mcfg_cleanup_nvefs` — el subsistema MCFG que aplica overlays NV/EFS por operador al boot. **FACT.**

### 2.2 Las funciones de parseo/carga RF NV (FACT — `rflte_nv_*`)

```
0xce6eba58  rflte_nv_parse_rx_cal_data: rflte_nv_tbl_ptr->valid_cal_rfm_path == NULL
0xce6eb430  rflte_nv_process_rx_static_data: rx_nv_parse_ptr == NULL
0xce6eb470  rflte_nv_process_rx_static_data: Parsing helper ptr is NOT NULL
0xce6eb4b0  rflte_nv_find_num_elements_per_type_rx_static
0xce6eb118  rflte_nv_populate_cmn_static_nv_data   (gnss_blanking, endc_pa_switch, …)
0xce6ec3c0  rflte_nv_populate_ca_bw_class_combo_nv: uncompressed_size 0x%X
0xce6ebdb0  rflte_nv_get_snr_sawless_params_from_nv
```
- **`rflte_nv_parse_rx_cal_data`** (@0xce6eba58) arma la tabla de **paths calibrados** (`valid_cal_rfm_path`) desde los containers de cal RX. **FACT.**
- **`rflte_nv_process_rx_static_data`** (@0xce6eb430) procesa datos RX estáticos (spur lists, umbrales RSSI/QPOET). **FACT.**
- Todo cuelga de **`rflte_nv_tbl_ptr`** (el struct global de NV RF LTE) y `common_nv_tbl_ptr` (común). Cuerpos UNKNOWN. **FACT (nombres/campos) / UNKNOWN-body.**

### 2.3 Tablas de cal RF y items críticos para RX (FACT)

**Dónde viven:** en **NV (RFNV items)** y **EFS** — NO como constantes en el código. Los containers están indexados **por RFM path** (device+sig_path+ant_path) y por **band**.

| dato de cal | mecanismo / item | VA | crítico para RX |
|---|---|---|---|
| **RX cal offset (ganancia absoluta del path)** | `RFNV_DATA_TYPE_STAND_ALONE_RX_CAL_OFFSET_V*` — **one-per-RFM-path** | 0xce6d4db0 | **SÍ — el offset absoluto antena→ADC; sin él, gain indeterminado** |
| **LNA gain offsets + switchpoints** | `rflte_dm_rxagc_config_update_switchpoints_and_lna_offsets_in_dm` carga en el Device Manager | 0xce6f0960 | **SÍ — el ΔdB de cada LNA gain state** |
| **N gain states calibrados** | `cal_data_p->num_gain_states <= RFNV_MAX_STATIC_LNA_GAIN_STATES` | 0xce6f6298 | SÍ — cuántos states hay |
| **RX static data / freq comp / spur** | `rflte_nv_process_rx_static_data` | 0xce6eb430 | SÍ — compensación por frecuencia |
| **SNR / SAWless params** | `rflte_nv_get_snr_sawless_params_from_nv` | 0xce6ebdb0 | condicional (SAWless bands) |
| **Phase comp (IQ balance)** | `[ELNA] get_phase_comp_data(): num_gain_states` / `rfdevice_efs_get_gain_comp_info` | 0xcea8d770 / 0xce6ddad8 | SÍ para IQ — corrige RSB residual |
| **split band info (FEM)** | `rfdevice_efs_get_split_band_info` (EFS) | 0xce6dda80 | selección de path por sub-banda |

**Cal crítica para que RX/IQ tenga gain conocido (INFERENCE con base FACT):**
```
gain_total(dB) = RX_cal_offset(path)        [RFNV_..._RX_CAL_OFFSET, @0xce6d4db0]
               + LNA_offset(gain_state)      [switchpoints+offsets @0xce6f0960]
               + freq_comp(EARFCN)           [rx_static_data @0xce6eb430]
   (+ phase_comp para I/Q balance del IQ crudo)  [@0xcea8d770]
```
Con el device **calibrado (NV/RFC presente)** + **gain fijo** (override LNA a un state, leyendo RX_AGC/LNA_GS), la IQ es interpretable en nivel absoluto. Sin NV cargada, `RFM_INIT WAS NEVER CALLED` / `rxlm_buf_idx INVALID` bloquean antes. **INFERENCE fuerte (fórmula estándar; componentes todos FACT).** Ver `map_rf_rx.md §4`.

---

## 3. MCPM — Modem Clock and Power Manager

### 3.1 Qué es y su estructura (FACT)

MCPM es el **gestor de reloj y potencia del modem**: vota clocks/rails/buses via **NPA** (Node Power Architecture) por tech, y gestiona los estados de bring-up/sleep del subsistema modem. Máquina de estados verificada:
```
0xcea33770  MCPM_STATE: … in MCPM_INIT_STATE!
0xcea337b0  … MCPM_ACQ_STATE          (acquisition)
0xcea337e8  … MCPM_IDLE_RX_STATE      (idle-RX = página/DRX)
0xcea33828  … MCPM_PAGE_INDICATOR_DECODE_STATE
0xcea33878  … MCPM_SLEEP_STATE
0xcea338b8  … MCPM_LIGHT_SLEEP_STATE
0xcea33938  … MCPM_DEEP_LIGHT_SLEEP_STATE
0xcea33730  … MCPM_POWER_DOWN_STATE
```
Clientes por tech en b21: **`mcpm_rf`** (@0xc3feeb90), **`mcpm_rf_cal`** (@0xc3feebc1), `mcpm_config_tech`, `mcpm_rf_bwp_nr_tech`. Enum de techs: `MCPM_NUM_TECH` / `MCPM_NUM_RAT_TECH`. **FACT.**

Sub-drivers (por nombre de función/assert):
- **`MCPM_Drv_Init`** (@0xce762968) — bring-up: AXI2AXI Qchannel (`TCSR_MSS_AXI2AXI_BRIDGE_QREQ_N`), etc.
- **`MCPM_Config_Modem`** / **`MCPM_MCVSConfig_Modem`** (@0xcea307d0..) — config del modem por tech (recibe MCVS requests).
- **`mcpm_boost`** — boost de CPU/BIMC via NPA (`/clk/cpu`, `/icb/arbiter`).
- **`mcpm_clockplan_*`** (@0xce769xxx) — plan de reloj (sync/async mux votes) leído de EFS.
- **`mcpm_resrc_*`** — los recursos individuales (§3.3).
- **`mcpm_drv_mcvs`** — MCVS (Modem Clock/Voltage Scaling) del Q6/VPE.

### 3.2 Cómo se votan clocks (el mecanismo NPA) (FACT)

MCPM crea **NPA clients** por recurso y por tech, y hace **update/commit** para votar un nivel:
```
0xce763cf0  mcpm_fast_path_npa_config[resrc_id].npa_handle
0xce7643d0  mcpm_npa_bus_ipa_snoc_update_func: client[0x%x] Num keys …
0xce764788  mcpm_npa_bus_modem_bimc_update_func
0xce7650b0  MCPM_RESRC_CPU: /clk/cpu NPA client handle …
0xce764e58  MCPM_Resrc_MCA_ICB_ARB_NPA_Init_CB: async ICB ARB NPA handle …
```
→ El "voto" = un **request NPA** sobre un nodo (`/clk/cpu`, `/clk/mss/config_bus`, `/icb/arbiter`, MPLL, WMSS_CX). Hay **sync** y **async** vectors (`mcpm_clockplan_apply_sync_votes` / `_async_votes`). **FACT.**

### 3.3 El recurso RF crítico para RX: QLINK + WMSS_CX (FACT)

**El "MCPM on" que menciona `enter_mode_path.md` como prerequisito del cnf es esto:** el ML1 enter_mode exige `mcpm_req.return_trans_id != 0` (@0xce8bd818) — una **transacción MCPM aceptada** que garantiza que los clocks/rails del RF están votados. Los recursos que la cadena RX necesita:

```
0xce767158  MCPM_RESRC_WTR_QLINK: MCPM_RF_Notify_Extended_Qlink_time invalid QlinkWakeTime input
0xce7671b8  MCPM_RESRC_QLINK: MCPM_Send_RF_Part_Update invalid Qlink wakeup time input
0xce7672b0  MCPM_RESRC_QLINK: Qlink0 PHY PC failed(MSS_QLINK0_BRIDGE_CNTRL) QLB_VDDA_FEW_ACK…
0xce767a28  MCPM_RESRC_QLINK: mcpm_qlink_0_wmss_cx_1_gps_required_handle client handle null
0xce767a78  MCPM_RESRC_QLINK: Invalid Qlink RFLM enum mapping
0xce766928  mcpm_resrc_modem_pll: Commit failed to enable qLink Ref clk source
0xce7634c0  MCPM_MCVS: mcvs_update_info indicates both WMSS_CX rails
0xce765ac0  MCPM_MODEM_BLK: MSS_CC_XO_CX_CBCR register status failed validation
```

**QLINK = el enlace RFFE/SPMI hacia el transceiver (WTR/SDR735).** Es un PHY con power-collapse (`Qlink0 PHY PC`, registro `MSS_QLINK0_BRIDGE_CNTRL`) y **rail VDDA** (`QLB_VDDA_FEW_ACK`/`QLB_VDDA_REST_ACK`). Requiere:
1. **qLink Ref clk source** habilitado (`mcpm_resrc_modem_pll` commit, @0xce766928).
2. **Rail WMSS_CX** (`mcpm_qlink_0_wmss_cx_1_*`, @0xce767a28; `both WMSS_CX rails` @0xce7634c0).
3. **XO_CX** (`MSS_CC_XO_CX_CBCR`, @0xce765ac0) — el crystal oscillator para el modem.
4. **QLINK PHY power-collapse exit** (sacar el PHY de PC).

**Qué clock/rail necesita la cadena RX para capturar (INFERENCE fuerte con base FACT):**
- **QLINK PHY up + WMSS_CX rail on** — para que el bus RFFE pueda escribir/leer registros del SDR735 (tune, LNA, ADC). Sin QLINK, no hay tráfico RFFE → no se puede programar el transceiver.
- **qLink Ref clk + XO_CX** — la referencia de reloj del transceiver.
- El "wakeup time" del QLINK (`MCPM_RF_Notify_Extended_Qlink_time`, `MCPM_Send_RF_Part_Update`) es el **budget de tiempo** que el RF le notifica a MCPM para tener el QLINK listo antes del wakeup RX. **FACT (recursos) + INFERENCE (que RX los necesita).**

### 3.4 Cómo votar clocks RF (la secuencia — INFERENCE con base FACT)

```
1. MCPM_Drv_Init          (boot: AXI2AXI Qchannel, DAL device handles)          @0xce762968
2. MCPM_Config_Modem(tech) (config por tech: MCVS + sync/async clock vectors)    @0xcea30878
3. mcpm_resrc_modem_pll: enable qLink Ref clk source                            @0xce766928
4. MCPM_RESRC_QLINK: Qlink0 PHY PC exit + WMSS_CX rail + VDDA ACK               @0xce7672b0/0xce767a28
5. MCPM_RF_Notify_Extended_Qlink_time / MCPM_Send_RF_Part_Update (RF budget)    @0xce767158/0xce7671b8
   → mcpm_req.return_trans_id != 0  (transacción aceptada)                       @0xce8bd818
```
Para un **driver Linux**, "votar clocks RF" = poner el modem en un estado MCPM que tenga **QLINK+WMSS_CX+XO_CX** activos ANTES de tocar el transceiver. En la práctica esto lo hace el **bring-up de modo (ONLINE o FTM/CAL)**: MCPM se enciende como parte de ese bring-up. El error `MCPM not turned ON yet` (@0xce748848) es el guard de que se intentó tocar RF sin ese voto. **FACT (recursos+guard) + INFERENCE (orden exacto, cuerpos UNKNOWN).**

**Nota MCPM en FTM:** `rflte_ftm_mc_get_mcpm_bw` (@0xce6e07d8) y `MCPM_Config_Modem` con `MCVS_FAST_CAP_REQUEST` (@0xcea30878) — el path FTM **también** hace su propio config MCPM (con BW derivado del carrier). Por eso el activate FTM necesita MCPM encendido. **FACT.**

---

## 4. DEVICE WAKEUP / SLEEP — la secuencia RX

### 4.1 El sleep manager (FACT)

`rfdevice_sleep_manager` maneja el sleep/wakeup de los **devices RF físicos** (SDR735, LNA/eLNA, ASM, GRFC). Funciones verificadas:
```
0xce6dd6d0  rfdevice_sleep_manager_sleep_devices_rx: Config structure not initialized!
0xce6dd840  rfdevice_sleep_manager_wakeup_devices_rx: Config structure not initialized!  <<< WAKEUP RX
0xce6dd8d8  rfdevice_sleep_manager_wakeup_devices_rx(): imm_write_script_ptr memory allocation failed!
0xce6dd950  rfdevice_sleep_manager_wakeup_device_rx(): Invalid device type %d!
0xce6dd9b0  rfdevice_sleep_manager_wakeup_grfc_device: Unsupported GRFC device_type %d
```
- **`rfdevice_sleep_manager_wakeup_devices_rx` (@0xce6dd840, string @string 0xce6dd840):** despierta **todos** los devices RX de un path. Genera un **`imm_write_script_ptr`** = un **script RFFE de writes inmediatos** que se ejecutan de una para sacar cada device de sleep. **FACT.**
- **`rfdevice_sleep_manager_wakeup_device_rx`** (singular) — despierta **un** device por tipo (SDR735, LNA, ASM, GRFC). Valida `device type`. **FACT.**
- El script necesita una **config structure inicializada** (viene de la RFC/NV) y un **buffer** (malloc del `imm_write_script_ptr`). **FACT.**

### 4.2 Quién dispara el wakeup RX (FACT)

**Dos caminos, según modo:**
- **FTM/CAL:** `rflte_ftm_mc_wakeup` (@0xce6e08a8) itera `(sub, cc, antpath)` y por cada antpath válido llama a `rfdevice_sleep_manager_wakeup_devices_rx`. Valida antpath (normal y SAWless). Es el **"encender el RF" del path de test**. **FACT.**
- **Mission (online):** `rflte_mc_rx_wakeup` (@0xce6e3ce0) — obtiene RxAGC data por `(rx_handle, path_idx, carrier_idx)` y despierta. **FACT.**
- **Otros techs:** `rfm_1x_prep_wakeup_rx`/`exec_wakeup_rx` (1x @0xce684328), `rfgsm_dispatch_rx_wakeup_req` (GSM @0xce6d7dd0), WCDMA WTR wakeup (@0xce6da648). Cada RAT tiene su wakeup. **FACT.**

### 4.3 La secuencia de wakeup RX (INFERENCE con base FACT)

```
0. PRECOND: MCPM on (QLINK+WMSS_CX+XO_CX votados, §3.3), RFC config structure init, NV cargada.

1. rflte_ftm_mc_wakeup (FTM)  |  rflte_mc_rx_wakeup (mission)
      · valida (sub, cc, antpath) — cada path por subscription/carrier
      · WTR scouting update (rx wakeup script building)     @0xce6d6f58
      ▼
2. rfdevice_sleep_manager_wakeup_devices_rx  (@0xce6dd840)
      · alloca imm_write_script_ptr
      · por cada device del path: wakeup_device_rx (SDR735 power-on, LNA/eLNA, ASM switch)
      · genera el script RFFE de writes inmediatos
      ▼
3. Ejecuta el script RFFE (por bus/USID, RFFE scheduler o inmediato)
      ▼
4. Espera QLINK up:  rflte_qlink_status_timer_cb
      · "Qlink is not up in 3ms post RF wakeup call"   @0xce6e3ec0
      · "Qlink is not up in 6ms post RF wakeup call"   @0xce6e3e60
      · qlink_buf != NULL                              @0xce6e09d8
      ▼
5. Cadena RX viva → tune/RxLM chain activate → captura
```
- El **timing** lo gestiona el sleep manager del ML1: `sleepmgr_inst->rf_info.rf_wakeup_start_ustmr` (@0xce7e5fc8), `sleep_adj_tl_cfg[...].rf_wakeup_time > 0` (@0xce842480) y `mcpm_wakeup_time > 0` (@0xce842530) — hay un **budget de tiempo de RF wakeup** y uno de **MCPM wakeup** que deben ser >0, y un guard `RF wakeup time exceeded OTA time` (@0xce7e8090). **FACT.**
- **La pieza clave del wakeup: el script RFFE inmediato + espera de QLINK up (3-6ms).** El QLINK debe estar levantado (por MCPM, §3.3) para que el script llegue al transceiver. **FACT (strings) + INFERENCE (encadenado; cuerpos UNKNOWN).**

Ver `map_rf_rx.md §2.1/§3.4` para la parte de scripts RFFE.

---

## 5. RFC — RF Card / config del HW RF del rhodep

### 5.1 Qué es RFC (FACT)

`rfc_intf` es el **singleton que describe el hardware RF de la placa (board = "rhodep"/rhodium-dep)**: qué devices RF hay, en qué bus/USID RFFE están, qué bandas/paths soportan, y qué GPIOs los controlan. Se construye al boot desde datos de RFC + EFS.
```
0xce6de230  RFC ERROR Re-creating the Common rf_card Object which is not allowed   (singleton)
0xce6de278  rfc_intf::rfc_forced_crash_check()
0xce6de3a0  Populated RFM Device in rfc_rfm_device_tbl wrapper %d is more than maximum
0xce6de480  rfc_create_sig_path_prop_tbl - Failed to allocate memory for sig_path property table
0xce6de400  rfc API called before SIG_PATH property table constructed
0xce6eec08  rfc_intf::create_rfm_path_info_tbl() rfm_mode %u NOT SUPPORTED
```
**FACT.** También hay clases por-tech: `rfc_gsm()` (@0xce6d8f58), `rfc_wcdma_data` (@0xce6da4a0, singleton), `rfc_nr5g_data` (@0xce6f6e48, singleton), `rfc_lte_data`/`rfc_lte_path` (@0xce6ec640), `rfc_mmw` (@0xce6fa800). **FACT.**

### 5.2 Qué define y cómo se accede (FACT)

| qué define | función RFC | VA |
|---|---|---|
| **device → wrapper (RFM device table)** | `rfc_rfm_device_tbl` | 0xce6de3a0 |
| **device → bus → USID (RFFE addressing)** | `rfc_get_used_sid_table()` — `physical device index → bus → SID`, con `RFFE_SHADOW_REG_MAX_BUS` | 0xce6d49d0 |
| **acceso device por (bus, USID)** | `rfdevice_phy_dev_get_bus_idx(): No combination of bus=%d, usid=%d` | 0xce6dc3b0 |
| **sig_path property table (bandas/paths)** | `rfc_create_sig_path_prop_tbl` (concurrency policy) | 0xce6de480 |
| **rfm_path info (rfm_mode→paths)** | `create_rfm_path_info_tbl` | 0xce6eec08 |
| **GPIOs (control de FEM/switch)** | `rfc_common_set_gpio - DalTlmm_GpioIdOut` | 0xce6fa048 |
| **wakeup GPIO config** | `rfc_cmw_wakeup - DalTlmm_ConfigGpioId` | 0xce6fa090 |
| **device info por LTE path** | `rfc_lte_path_get_device_info` | 0xce6ec640 |
| **FEM port por (ant_path, band, tech)** | `fem_get_port(): rfc_data_override_ptr` | 0xce6dc4d0 |
| **radio options / SR groups (de EFS)** | `rfc_radio_resource_get_sr_groups_from_efs` | 0xce6fa1b8 |
| **PLL select LUT (CA)** | `rfc_ca_pll_sel_entries_binary_search` / `rfc_search_mc_efs_pll_sel_lut` | 0xce6fa218/0xce6fa260 |

**Cómo se accede:** vía el **singleton `rfc_intf`** (get-instance); las APIs `rfc_*` consultan las tablas construidas al boot. El acceso al HW concreto es por **RFFE/SPMI (bus, USID)** para los devices seriales (SDR735, LNA, ASM) y por **GPIO (DalTlmm)** para los controles paralelos (switch enables, FEM). **FACT.**

### 5.3 Qué define el HW RF del rhodep específicamente (FACT + INFERENCE)

La RFC del board **rhodep** (Moto G82 5G) define, en sus tablas:
- **Qué transceiver:** SDR735 (sub-6) + SMR526v2 (mmW, si aplica). **FACT (map_rf_rx §5).**
- **Bandas soportadas por sig_path:** la `sig_path property table` mapea cada `(device, sig_path, band)` a sus propiedades (LNA, ASM, filtro, SAWless o no). **FACT.**
- **Bus/USID RFFE de cada device:** `rfc_get_used_sid_table` — qué bus RFFE y qué USID tiene el SDR735, cada LNA, cada ASM. **FACT (mecanismo) / los valores concretos = UNKNOWN estático (viven en el blob RFC/NV, no en seg27).**
- **GPIOs:** qué GPIO DalTlmm controla cada switch/FEM enable del board. **FACT (mecanismo) / números concretos UNKNOWN.**
- **PLL/SR groups:** los combos de CA y PLL select del board (de EFS). **FACT.**

**Los valores concretos (USID, GPIO num, band lists) NO están en el material** — viven en el **blob RFC (RF Card driver + NV/EFS)** que no está descomprimido (es el mismo pool dlpager). La RFC es lo que un port a un board distinto tendría que re-derivar. **FACT (qué define) / UNKNOWN (los valores).**

---

## 6. QUÉ SETEA LOS GATES RF (gp+0x740 / gp+0x7000) — reconciliación

De `rf_cal_mode_gates.md` (todo FACT ahí):
- **Gate (a) `gp+0x740` = 0xcbf4f740** (byte, enum modo RF {0,1,2}, gate pide `==2`).
- **Gate (b) `gp+0x7000` = 0xcbf56000** (word, puntero al ctx RF-instance C++, gate pide `!=0`).

**Cuál subsistema (mode / MCPM / wakeup / rfc) los setea — respuesta:**

| subsistema | ¿setea los gates? | evidencia |
|---|---|---|
| **Operating mode (QMI/CM/MMOC)** | **NO directamente.** MMOC arranca/para tasks, pero no escribe `gp+0x740`/`gp+0x7000`. | El flag global de modo FTM/ONLINE (§1.3) es **el mismo concepto** que `gp+0x740`, pero lo escribe el driver RF, no MMOC. **INFERENCE.** |
| **MCPM (clocks/power)** | **NO.** MCPM vota clocks; es **precondición** del activate pero no escribe los gates. | `mcpm_req.return_trans_id != 0` es un gate *distinto* (del enter_mode ML1). **FACT.** |
| **Device wakeup (sleep_mgr)** | **NO.** El wakeup genera scripts RFFE; no escribe `gp+0x740`/`gp+0x7000`. | `rf_cal_mode_gates.md §4.1`: ningún RFTEST/wakeup escribe los gates. **FACT.** |
| **RFC** | **NO.** La RFC construye tablas de HW; no escribe los gates de modo. | — |
| **Bring-up de MODO CAL (FTM_SET_MODE cal)** | **SÍ (el trigger real).** | §6 abajo. **FACT (gates) + INFERENCE (trigger).** |

**Conclusión (reconcilia rf_cal_mode_gates.md):**
- **`gp+0x740 = 2`** lo escribe el **comando de bring-up de modo RF cal** (FTM_SET_MODE → cal, el que también arranca `rfm_init` + MCPM). Es un **comando de modo previo**, NO un side-effect de TECH_ENTER/RADIO_CONFIG ni del wakeup ni de la RFC. El store del literal `2` vive **fuera de la ventana decodificada de forma fiable** (código RF en dlpager). **FACT (ausencia de store #2 en 10MB CLADE) + INFERENCE (trigger = set-cal-mode).**
- **`gp+0x7000 != 0`** lo puebla el **accessor lazy get-or-create `0xd8284eac`** (crea el ctx C++ vtable @0xc37c6d80, store `memw(gp+0x7000)=obj` @0xd8284d20) la **1ª vez que se toca el subsistema RF-instance** — que es parte del mismo bring-up de cal. **FACT.**

→ **El mismo evento (entrar a modo RF cal/FTM) enciende ambos gates:** `gp+0x740→2` (el flag de modo) y, al tocar el path RF-instance, dispara el accessor que puebla `gp+0x7000`. Este evento es un **cambio de operating mode a FTM/CAL** (§1), no una acción de MCPM/wakeup/RFC aisladas. Es la **intersección de "operating mode" (§1) con el driver RF**. **INFERENCE fuerte (base FACT).**

**Relación con `@0xca7897b0[tech]=1`** (tech flag): lo abre el **activate del carrier** (`0xd81bd018` APPLY) cuando pasan ambos gates — es decir, tras (1) estar en modo cal (`gp+0x740==2`), (2) ctx creado (`gp+0x7000!=0`), (3) RADIO_CONFIG con BAND+EARFCN que dispara el activate. Ver `enter_mode_path.md §5` y `rf_cal_mode_gates.md §5`.

---

## 7. FACT / INFERENCE / UNKNOWN (con VAs)

### FACT (verificado byte-a-byte)
- **Operating mode enum** (b21): `eOPRT_MODE_{PWROFF,FTM,OFFLINE,OFFLINE_AMPS,OFFLINE_CDMA,ONLINE,LPM,RESET,NET_TEST_GW,OFFLINE_IF_NOT_FTM,PSEUDO_ONLINE,RESET_MODEM,NONE}` @0xc4231805..0xc4231900.
- **Cadena de modo:** `cmph_send_oprt_mode` (b21 0xc375fbdc), `cm_ph_cmd_pref_change_req failed` (0xc40cc45e), `oprt_mode = %d CMPH error %d` (0xc3fdda71), `=MMOC= Phone should be in online mode` (0xce64ebb0), `ds_qmi_dms.c` (0xc3575222), `QMI_DMS_ERR_FATAL` (0xcea82c8b).
- **Guard FTM/ONLINE:** `Attempt to access FTM variables in ONLINE MODE` @0xce6d59e0 (x4); `lte_LL1_get_ul_ftm_cal_mode(cxn_id) > 0` @0xce5bd511; `rf_mode == NR5G_LL1_CAL_FTM_RF_MODE_CAL` @0xce6cf8c6.
- **NV read:** NVMGR (`NV uncompress function failed for NV Item: %d` @0xce6eb0b8; `[NVMGR] … RX_CAL_OFFSET_V* per RFM path` @0xce6d4db0); `rflte_nv_parse_rx_cal_data` @0xce6eba58; `rflte_nv_process_rx_static_data` @0xce6eb430; EFS (`rfdevice_efs_get_split_band_info` @0xce6dda80, `..._gain_comp_info` @0xce6ddad8, `MCPM_DRV: NULL pointer to the EFS file` @0xce762d60); MCFG (`MCFG Parsing Error` @0xce760ae8).
- **Cal RX crítica:** RX_CAL_OFFSET one-per-path @0xce6d4db0; `rflte_dm_rxagc_config_update_switchpoints_and_lna_offsets_in_dm` @0xce6f0960; `num_gain_states <= RFNV_MAX_STATIC_LNA_GAIN_STATES` @0xce6f6298; phase comp @0xcea8d770.
- **MCPM estados:** MCPM_{INIT,ACQ,IDLE_RX,PAGE_INDICATOR_DECODE,SLEEP,LIGHT_SLEEP,DEEP_LIGHT_SLEEP,POWER_DOWN}_STATE @0xcea33730..0xcea33938; clientes `mcpm_rf`/`mcpm_rf_cal` (b21 0xc3feeb90/0xc3feebc1); `MCPM not turned ON yet` @0xce748848; `mcpm_req.return_trans_id != 0` @0xce8bd818.
- **MCPM RF clocks:** `MCPM_RESRC_WTR_QLINK: MCPM_RF_Notify_Extended_Qlink_time` @0xce767158; `MCPM_RESRC_QLINK: Qlink0 PHY PC failed(MSS_QLINK0_BRIDGE_CNTRL) QLB_VDDA_*` @0xce7672b0; `mcpm_qlink_0_wmss_cx_1_*` @0xce767a28; `enable qLink Ref clk source` @0xce766928; `both WMSS_CX rails` @0xce7634c0; `MSS_CC_XO_CX_CBCR` @0xce765ac0; NPA (`/clk/cpu`, `/clk/mss/config_bus`, `/icb/arbiter`).
- **Device wakeup:** `rfdevice_sleep_manager_wakeup_devices_rx` @0xce6dd840 (imm_write_script_ptr); `_wakeup_device_rx` @0xce6dd950; `rflte_ftm_mc_wakeup` @0xce6e08a8; `rflte_mc_rx_wakeup` @0xce6e3ce0; `rflte_qlink_status_timer_cb` 3/6ms @0xce6e3ec0/0xce6e3e60; `rf_wakeup_time > 0` @0xce842480, `mcpm_wakeup_time > 0` @0xce842530, `RF wakeup time exceeded OTA time` @0xce7e8090.
- **RFC:** `rfc_intf` singleton @0xce6de230; `rfc_get_used_sid_table` @0xce6d49d0; `rfc_rfm_device_tbl` @0xce6de3a0; `rfc_create_sig_path_prop_tbl` @0xce6de480; `create_rfm_path_info_tbl` @0xce6eec08; `rfc_common_set_gpio` @0xce6fa048; `rfc_cmw_wakeup` @0xce6fa090; `rfdevice_phy_dev_get_bus_idx (bus,usid)` @0xce6dc3b0; `rfc_lte_path_get_device_info` @0xce6ec640.
- **Gates RF:** gp+0x740=0xcbf4f740 (`==2`), gp+0x7000=0xcbf56000 (`!=0`); accessor lazy 0xd8284eac / creator 0xd8284cf4 (`memw(gp+0x7000)=obj` @0xd8284d20); único writers de gp+0x740 = 0/1 (rama no-activate) — **ningún store del literal 2 en 10MB CLADE**. (rf_cal_mode_gates.md)

### INFERENCE (base dura)
- Valores numéricos del enum `sys_oprt_mode_e_type` por posición canónica (nombres/orden FACT).
- Cadena QMI-DMS→CM(cm_ph_cmd_pref_change)→MMOC→arranque/parada de tasks (strings FACT, cadena canónica Qualcomm).
- El flag global FTM/ONLINE (§1.3) es el mismo concepto que gp+0x740 (enum de 3 valores; cal=2).
- RX necesita QLINK PHY up + WMSS_CX + XO_CX + qLink Ref clk votados por MCPM antes de tocar el transceiver.
- Secuencia de wakeup RX (script RFFE inmediato + espera QLINK 3-6ms); orden exacto no disassemblado.
- Fórmula gain absoluto = RX_cal_offset(path) + LNA_offset(state) + freq_comp(EARFCN) (+ phase_comp).
- Los gates gp+0x740/gp+0x7000 los enciende el **bring-up de modo RF cal (FTM_SET_MODE cal)**, que es la intersección de "operating mode→FTM/CAL" con el driver RF; NO los enciende MCPM/wakeup/RFC por separado.

### UNKNOWN (sólo en vivo / con blob ausente)
- **Valores numéricos** del enum operating mode por posición; sub_command/UMID/TLV numérico exacto de `set_operating_mode` (DMS) y de FTM_SET_MODE en este build.
- **Cuerpos de código** de MCPM/`rflte_nv_*`/`rfdevice_sleep_manager_*`/`rfc_*` (pool dlpager 0xd4400000, mapa de páginas ausente). Todo lo anterior es contrato-de-strings, no disasm de cuerpo.
- **Valores concretos de la RFC del rhodep:** USID/bus RFFE de cada device, GPIO numbers, band lists por sig_path, PLL select LUTs (viven en blob RFC/NV/EFS ausente).
- **NV Item numbers concretos** de cada RFNV_* (los strings dan el *tipo* `RFNV_DATA_TYPE_*`, no el int).
- **Estado runtime** de los gates (gp+0x740/gp+0x7000), del flag de modo, y de los votos MCPM (sólo en vivo).
- Store byte-exact de `gp+0x740=2` (VA del writer, fuera de ventana fiable).

---

## 8. REPRODUCIR

```bash
cd /tmp/modemre
# --- Operating mode enum (b21 @0xc3553000) ---
python3 - <<'PY'
import re
d=open('modem.b21','rb').read(); B=0xc3553000
for m in re.finditer(rb'eOPRT_MODE_[A-Z_]+',d):
    print(hex(B+m.start()), m.group().decode())
PY
# --- Cadena de modo (b21 + seg27) ---
python3 - <<'PY'
import re
d=open('modem.b21','rb').read(); B=0xc3553000
for kw in (b'cmph_send_oprt_mode',b'cm_ph_cmd_pref_change_req failed',b'oprt_mode = %d CMPH'):
    m=re.search(re.escape(kw),d); print(hex(B+m.start()), kw.decode())
PY
# --- MCPM RF clocks / QLINK / rails (seg27 @0xce480000) ---
python3 - <<'PY'
d=open('seg27_dec.bin','rb').read(); B=0xce480000
for va in (0xce767158,0xce7672b0,0xce767a28,0xce766928,0xce7634c0,0xce765ac0,0xce748848,0xce8bd818):
    print(hex(va), d[va-B:va-B+110].split(b'\x00')[0].decode('latin1'))
PY
# --- NV read + cal RX (seg27) ---
python3 - <<'PY'
d=open('seg27_dec.bin','rb').read(); B=0xce480000
for va in (0xce6eb0b8,0xce6d4db0,0xce6eba58,0xce6eb430,0xce6f0960,0xce6f6298,0xce6dda80,0xce762d60):
    print(hex(va), d[va-B:va-B+120].split(b'\x00')[0].decode('latin1'))
PY
# --- Device wakeup RX (seg27) ---
python3 - <<'PY'
d=open('seg27_dec.bin','rb').read(); B=0xce480000
for va in (0xce6dd840,0xce6dd950,0xce6e08a8,0xce6e3ce0,0xce6e3e60,0xce6e3ec0,0xce842480,0xce842530):
    print(hex(va), d[va-B:va-B+110].split(b'\x00')[0].decode('latin1'))
PY
# --- RFC (seg27) ---
python3 - <<'PY'
d=open('seg27_dec.bin','rb').read(); B=0xce480000
for va in (0xce6de230,0xce6d49d0,0xce6de3a0,0xce6de480,0xce6eec08,0xce6fa048,0xce6fa090,0xce6dc3b0,0xce6ec640):
    print(hex(va), d[va-B:va-B+120].split(b'\x00')[0].decode('latin1'))
PY
# --- Gates RF (reconfirmar; rf_cal_mode_gates.md) ---
./dis.sh 0xd81bd160 0xc      # GATE(a) memb(gp+0x740); if(!=2) jump
./dis.sh 0xd8286240 0x30     # GATE(b) memw(gp+0x7000)!=0
./dis.sh 0xd8284cf4 0x40     # creator: memw(gp+0x7000)=obj
```
