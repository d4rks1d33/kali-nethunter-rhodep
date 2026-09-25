# MAPA COMPLETO DE LA CADENA TX RF — Qualcomm SM6375 (MPSS.HI.4.3.4)
Build: **MPSS.HI.4.3.4-00494-MANNAR_GEN_PACK-1.24452.133** (SM6375 / Moto G82 5G)
Objetivo: mapear la cadena de TRANSMISION del modem de punta a punta (para futura emision RF controlada desde driver Linux).

Imagenes:
- `clade_dec_full.bin` (VA 0xd8000000, 10MB) = **codigo FTM/DIAG dispatch descomprimido** (llvm-objdump hexagon v66 via `dis.sh`).
- `clade_dec.bin` (VA 0xd8000000..0xdb397464) = extension del mismo dispatch.
- `seg27_dec.bin` (VA base **0xce480000**) = **modem.b27 descomprimido = rodata RF (RFLTE/RFLM/RFDEVICE + LTE-ML1 + NR5G LL1)**. Contiene TODOS los strings/nombres de funcion RF.
- Tablas de nombres de TLV/property: rodata b23 @0xc906cxxx.

Leyenda: **FACT** = string/instruccion verificada byte-a-byte (VA citada) · **INFERENCE** = deduccion con base dura · **UNKNOWN** = solo resoluble en vivo o con blob ausente.

---

## 0. LIMITE ESTRUCTURAL (identico a map_rf_rx.md) — leer primero

**FACT:** igual que en RX, el **codigo** de la cadena TX fisica (RFLTE_MC_TX / RFLM / RFDEVICE) **NO esta
en ningun blob descomprimido disponible** — vive en un segmento q6zip no extraido. Lo **presente y
disassemblable** es el **dispatch FTM/DIAG** (clade @0xd8xxxxxx): registro de comandos, unpackers de TLV,
repackers. Ese codigo **registra** los comandos TX y despacha por `callr` a punteros resueltos en runtime;
los cuerpos RFLTE_MC_TX estan en q6zip.

Todos los **nombres de funcion TX, structs, asserts, tablas de property y limites** SI estan (rodata b27
@0xce480000 + b23 @0xc906cxxx). Por eso este mapa es **completo a nivel de bloques/funciones/strings/TLVs
(FACT)**, con **cuerpos RFLTE_MC_TX marcados UNKNOWN-body**.

**Consecuencia:** el camino accionable para TX es el **framework FTM-RF-TEST** (codigo presente):
comandos **TX_CONTROL (slot 2)** y **TX_MEASURE (slot 6)**, mas los overrides. Los internos se controlan
por sus **properties/TLVs**, sin necesidad de desensamblar los cuerpos RFLTE.

---

## 1. DIAGRAMA DE LA CADENA TX (bloques -> funciones -> VAs)

```
                       fuente de datos TX
        (waveform generada / IQ de buffer / tono CW segun tech)
                              │
              ┌───────────────▼────────────────┐
              │  TxFE / TxLM (Tx Link Manager)  │  config de la cadena digital TX
              │  · txlm_buf_idx (1 por carrier) │  handle por carrier/chain
              │  · TXFE config / DAC params     │
              └───────────────┬────────────────┘
   TxLM alloc  ← "Invalid client for TxLM allocation=%d"        @0xce604f93 (str)
   TxLM buffer ← "Not able to allocate TxLM buffer: status=%d"   @0xce604f93-ish
   TxLM cfg    ← wfw_tx_lm_cfg / WFW_TX_TXLM_CFG_*                @0xce5a070b (str txlm)
   DAC params  ← wfw_tx_configure_txlm_dac_params                @0xce5cb4a2-region (str)
   TXFE cfg    ← txlm_carr_cfg_ptr->txfe_config[0] < LTE_LL1_TXFE_INVALID
   MC handle   ← RFLTE_MC_TXLM_INVALID_HANDLE                     (asserts)
                              │
              ┌───────────────▼────────────────┐
              │  BASEBAND DIGITAL TX            │  wb-DPD, scaling, spurs, pre-dist
              │  · DTR-TX (WB DPD rate kHz)     │
              │  · waveform scaling factor      │
              └───────────────┬────────────────┘
   WB-DPD rate ← txlm_stat_ptr->tx_dtr_wb_dpd_rate_kHz != 0       @0xce5cb4a2 (assert)
   WFS scale   ← rflte_dm_update_wfs_scaling_factor               @0xce6f2990
   spurs/notch ← rflte_mc_tx_handle_spurs                         @0xce6e6b60
                              │
              ┌───────────────▼────────────────┐
              │  DAC (dentro del RFIC SDR735)   │  digital->analog IQ
              │  · dac_freq_khz / dtr_ib_freq   │
              └───────────────┬────────────────┘
   DAC rate    ← "Unsupported configuredDacRate %d kHz - Check TXLM spreadsheets" (str)
   DAC freq    ← txlm_stat_ptr->dac_freq_khz != 0 (assert)
                              │
              ┌───────────────▼────────────────┐
              │  UP-CONVERTER / MIXER / TX-PLL  │  HW interno del SDR735, script RFFE
              │  · TxPLL / LO                   │
              └───────────────┬────────────────┘
   TxPLL       ← rflte_mc_tx_get_txpll_script                     @0xce6e7f88 (str)
   TRX scripts ← rflte_mc_tx_config_transceiver_scripts           @0xce6e4090
   tune script ← rflte_mc_tx_build_tune_script                    @0xce6e42f8
   TRX device  ← rflte_mc_tx_config_msm / tx_config_table         @0xce6e7dc8
   AFC/FFH     ← rflte_mc_tx_afc_update / _get_ffh_script          @0xce6e8000/@0xce6e8068
                              │
              ┌───────────────▼────────────────┐
              │  PA (Power Amplifier) + bias    │  externo, control RFFE + ET
              │  · PA_STATE (gain range)        │
              │  · PA_BIAS / PA_CURRENT / RGI   │
              └───────────────┬────────────────┘
   PA on evt   ← rflte_dm_update_pa_on_event                      @0xce6f0b80
   PA swp/Cmax ← rflte_dm_tx_copy_pa_swp_to_p_cmax_data           @0xce6f1780
   PA fixup    ← rf_rffe_iu_common_prepare_txagc_fixup_table      @0xcea8c2e0 (PA gain->RFFE)
   PA states   ← RFIU_NR5G_TX_PA_STATE0..3 (asserts)              @0xce6cdd0b
   ICQ bias    ← rfdevice_efs_get_pa_icq_info                     @0xce6ddb88 (str)
                              │
              ┌───────────────▼────────────────┐
              │  ET / QET6200 (envelope track)  │  alimenta el PA con envolvente
              │  · ETVD_MAX/MIN, DETROUGH       │
              │  · QPOET_VMIN/VMAX              │
              └───────────────┬────────────────┘
   ET/QET6200  ← "Force crash for QET6200 cal ... VPA reading"    @0xce6d4878
   PAPM/QET    ← "[PAPM] Tech %d, bw %d not supported by QET %d"  @0xcea8b8f8
   QPOET thr   ← rflte_nv_query_qpoet_rssi_threshold_data_from_nv @0xce6eb900
   ET ctl NV   ← rflte_core_task_ctl_get_tx_intra_ulca_apt_et_control_nv @0xce6e2e50
   PAPM hub    ← rfdevice_papm_hub_udpdate_vdd_table              @0xce6d4900 (str)
                              │
              ┌───────────────▼────────────────┐
              │  TxAGC (Tx Automatic Gain Ctl)  │  fija la potencia total de salida
              │  · IQ_GAIN, TXFE_GAIN, ENV_SCALE│  reparte gain entre etapas
              │  · MTPL / SAR clamp             │
              └───────────────┬────────────────┘
   TxAGC LTE   ← rflm_lte_txagc_power_estimation_logging          @0xce6ed1f8
   TxAGC cfg   ← rflte_mc_tx_config_common_txagc                  @0xce6e87e8
   TxAGC set   ← rflte_dm_set_txagc_params                        @0xce6f24e0
   txlin data  ← rflte_txagc_populate_txlin_data                  @0xce6f23d8
   FTM en/dis  ← rflte_ftm_mc_enable_tx_agc / _disable_tx_agc     @0xce6e0b68/@0xce6e0ac8
   xpt ovrride ← rflte_ftm_xpt_override_txagc                     @0xce6e0810
                              │
                          ANTENA (ASM/switch RFFE)
   ASM TX      ← rflte_mc_tx_disable_transceiver_device / ASM path (str)
```

**Todas las VAs de la columna derecha son FACT** (strings verificados en seg27_dec.bin @0xce480000).
Los cuerpos de las funciones RFLTE_MC_TX/RFLM son **UNKNOWN-body** (§0).

---

## 2. LA CADENA TX FISICA — bloque por bloque

### 2.1 TxLM (Tx Link Manager) — FACT
- **Gestor de la cadena digital TX.** Un **handle (txlm_buf_idx) por carrier/chain**.
  - `Invalid client for TxLM allocation=%d` / `Not able to allocate TxLM buffer: status = %d` (@0xce604f93 region).
  - `RFLTE_MC_TXLM_INVALID_HANDLE` (asserts) — sin TxLM allocado no hay TX.
  - `rflte_mc_data_set_active_txlm_buf_idx` / `_set_txlm_buffer_idx` (str) — fija el buf activo por (tx_path, carrier).
- **Config del TxLM (firmware WFW):**
  - `wfw_tx_lm_cfg ON rcv'ed when TXLM already ON` / `WFW_TX_TXLM_CFG_DISABLED_STATE` (str) — SM de estados del TxLM.
  - `txlm_carr_cfg_ptr->txfe_config[0] < LTE_LL1_TXFE_INVALID` — TXFE (Tx Front-End) config por carrier.
  - `txlm_carr_cfg_ptr->ul_bw < LTE_NUM_BANDWIDTHS`, `txlm_chain_idx < 2` (max 2 chains) — asserts.
- **Multi-tech:** hay TxLM para LTE, NR5G (`NR_TX_IUSS: FED TXLM not cfg`), WCDMA (`rflm_wcdma_txagc_*`),
  GSM (`rfgsm_msm_enable_tx ... txlm_update_static_settings`), 1x (`TXLM buffer allocate error %d for 1X tech`). **FACT.**

### 2.2 DAC / DTR-TX (digital -> analog) — FACT
- `txlm_stat_ptr->dtr_ib_freq_khz != 0 && txlm_stat_ptr->dac_freq_khz != 0` (assert) — freq del DAC y del
  DTR intermedio-band. `Unsupported configuredDacRate %d kHz - Check TXLM spreadsheets` /
  `Unsupported txfe_input_freq_hz %d @ wfw_tx_configure_txlm_dac_params` (str). **FACT.**
- **WB-DPD (predistorsion en banda base):** `txlm_stat_ptr->tx_dtr_wb_dpd_rate_kHz != 0` (@0xce5cb4a2) —
  el DTR-TX corre la DPD wideband a una tasa dada. **FACT.**
- **PDA (predistortion/scaling):** `prach_pda_ideal_val`, `prach_pda_scale_q29`, `txr_pda_ideal_val` (asserts). **FACT.**
- **DAC es HW interno del SDR735** (igual que el ADC en RX): el FW ve el stream digital pre-DAC; el DAC en si
  se programa por el power-on script del RFIC. **INFERENCE fuerte** (misma logica que §2.3 de map_rf_rx.md).

### 2.3 Up-converter / Mixer / TxPLL (dentro del RFIC SDR735) — FACT nombres / INFERENCE HW
- **Scripts de transceiver TX:** `rflte_mc_tx_config_transceiver_scripts` (@0xce6e4090) construye los
  writes RFFE que programan el mixer/LO/PLL del SDR735 para TX. `Script payload buffer overflow detected`,
  `event pointer for trx_trigger is NULL` (str). **FACT.**
- **Tune script:** `rflte_mc_tx_build_tune_script` (@0xce6e42f8): `tx_tune_params`, `tx_config_table`,
  `tx_config_script_pointers`, `policy_check`, `Invalid tx path inded = %d`, `Invalid stx_combo_idx = %d`. **FACT.**
- **TxPLL:** `rflte_mc_tx_get_txpll_script: trx_config_params is NULL for handle %d` (@0xce6e7f88). **FACT.**
- **AFC / FFH (fast freq hop):** `rflte_mc_tx_afc_update` (@0xce6e8000), `rflte_mc_tx_get_ffh_script` (@0xce6e8068). **FACT.**
- **Config MSM/TRX:** `rflte_mc_tx_config_msm: tx tune params is NULL` (@0xce6e7dc8). **FACT.**
- **El mixer/LO/PLL son HW interno del SDR735** controlado por scripts RFFE (registros en RFC/NV, UNKNOWN estatico). **INFERENCE.**

### 2.4 PA (Power Amplifier) — FACT
- **Encendido del PA:** `rflte_dm_update_pa_on_event` (@0xce6f0b80): construye/aplica el "event script" de
  encendido del PA (`rflte_dm_create_event_script`, `Can not clear script ptr`). **FACT.**
- **PA gain range / states:** `RFIU_NR5G_TX_PA_STATE0..STATE3` (@0xce6cdd0b) — el PA tiene N estados de gain
  (rangos de potencia). `tentative_logical_pa_state <= RFIU_NR5G_TX_PA_STATE3`. **FACT.**
- **PA sweep / Cmax:** `rflte_dm_tx_copy_pa_swp_to_p_cmax_data` (@0xce6f1780) — copia la tabla de PA-sweep
  (potencia max por state) a Cmax. `pa_state_nv_ptr is NULL`. **FACT.**
- **PA fixup RFFE (gain state -> registros):** `rf_rffe_iu_common_prepare_txagc_fixup_table` (@0xcea8c2e0):
  `PA Device Object query failure`, `shared reg0, num gain = %d`, `unsupported PA fixup type`. Es el equivalente
  TX de la tabla RxAGC->RFFE: por cada PA gain state, la lista de writes RFFE. **FACT.**
- **ICQ bias:** `rfdevice_efs_get_pa_icq_info` (@0xce6ddb88) — corriente de reposo (bias) del PA desde EFS. **FACT.**
- **PA por tech:** WCDMA `rflm_wcdma_txagc_skip_pa_stm` / `disable_pa_hys`; GSM `rfc_gsm_pa_range0/1_grfc_info`. **FACT.**

### 2.5 ET / Envelope Tracking (QET6200 / QPOET / PAPM) — FACT
- **Chip ET = QET6200** (Qualcomm envelope tracker): `Force crash for QET6200 cal after VPA low/high reading`
  (@0xce6d4878). **FACT.**
- **QPOET (power tracker):** `rflte_nv_query_qpoet_rssi_threshold_data_from_nv` (@0xce6eb900),
  `rflte_nv_allocate_mem_qpoet_rssi_threshold` (@0xce6eb500), `rflte_dm_populate_qpoet_rssi_threshold` (@0xce6eff58). **FACT.**
- **PAPM hub (Power Amplifier Power Management):** `rfdevice_papm_hub_udpdate_vdd_table` (@0xce6d4900),
  `[PAPM] Tech %d, bw %d not supported by QET %d` (@0xcea8b8f8). Programa la tabla VDD del ET por (tech, bw). **FACT.**
- **ET control NV:** `rflte_core_task_ctl_get_tx_intra_ulca_apt_et_control_nv` (@0xce6e2e50) — APT (Average Power
  Tracking) vs ET, config por NV. **FACT.**
- **ET params en TX_CONTROL:** properties `ENV_SCALE_ACTION`(11), `ENV_SCALE`(12), `ENV_GAIN`(23), `ET_VMIN`(39)
  (tabla @0xc906c438, §4). En VDPD/ET table @0xc906c530: `ETVD_MAX`(5), `ETVD_MIN`(6), `DETROUGH`(7),
  `QPOET_VMIN`(8), `QPOET_VMAX`(9), `SCALE_DETROUGH`(11). **FACT.**

### 2.6 TxAGC (fija la potencia total de salida) — FACT
- **La cadena TxAGC reparte el gain** entre IQ digital, TXFE, ENV/ET, PA-state, y RGI (Radio Gain Index):
  - `rflte_mc_tx_config_common_txagc: Failed core txlin config for sig_path:%d, device:%d, ant_path:%d` (@0xce6e87e8).
  - `rflte_dm_set_txagc_params: Pipeline buf ptr is NULL` (@0xce6f24e0).
  - `rflte_txagc_populate_txlin_data: rflte_dm_tx_build_scripts_for_tx_on_template FAILED!` (@0xce6f23d8).
  - `rflm_lte_txagc_power_estimation_logging` (@0xce6ed1f8) — estima potencia por handle. **FACT.**
- **FTM enable/disable TxAGC** (el "prender/apagar el lazo de potencia TX"):
  - `rflte_ftm_mc_enable_tx_agc: unsupported number of tx chain %d` (@0xce6e0b68).
  - `rflte_ftm_mc_disable_tx_agc: unsupported tx chain number %d` (@0xce6e0ac8). **FACT.**
- **Override XPT (crossed-product transmit = DPD+ET):** `rflte_ftm_xpt_override_txagc: cc tx sm ptr is NULL`
  (@0xce6e0810). **FACT.**
- **txlin (TX linearizer)** = la tabla AMAM/AMPM que linealiza el PA (ver DPD §6). **FACT.**

### 2.7 Camino FTM que "prende el RF" para TX — FACT nombres / INFERENCE-body
- `rflte_ftm_mc_wakeup` (@0xce6e08a8) — despierta devices (comun RX/TX, ver map_rf_rx.md).
- `rflte_ftm_mc_override_tx_freq: cc tx sm ptr is NULL for handle ID: %d` (@0xce6e0968) — fuerza la freq TX.
- `rflte_dispatch_tx_on_ind: tx sm ptr is NULL for handle ID: %d` (@0xce6e7490) — indicacion "TX ON".
- `ftm_lte_txagc_preprocess_tx_action` (@0xce6e12e8), `ftm_lte_txagc_set_encoder_input` (@0xce6e11d1),
  `ftm_lte_txagc_tx_rx_non_trx_enqueue` (str) — el path FTM que enque una accion TX (encender potencia). **FACT.**

---

## 3. LOS COMANDOS FTM TX (sub_command / command_id / TLVs)

### 3.1 Registro y command_id (de reg_order_B.md) — FACT
Los comandos TX se registran en el **modulo RF-TEST** (init `0xd8182640`, primer modulo desde el
registrador maestro `0xd84aa03c`). Cada comando ocupa un **slot de 12 bytes**; slot_index = orden de registro.
El command_id absoluto se asigna en RUNTIME (tabla @0xca9ef490 / @0xca79a850), por lo que el **entero exacto es
UNKNOWN estatico** — pero el orden RELATIVO es FACT.

| slot | command | unpacker VA | repacker VA | bufsize | F3 name (byte-exact) |
|-----:|---------|-------------|-------------|---------|----------------------|
| **2** | **TX_CONTROL** | **0xd8188428** | 0xd8188600 | **0x540** | `[FTM.RFTEST][TX_CONTROL][UNPACK]` @0xc3555c10 **FACT** |
| **6** | **TX_MEASURE** | **0xd818ab6c** | 0xd818bb80 | **0x60**  | `[FTM.RFTEST][TX_MEASURE][REPACK]` @0xc3555c70/c80 **FACT** |
| 10 | tx_measure (RFDEBUG) | 0xd84ad0b8 | — | — | `[FTM.RFDEBUG][tx_measure][REPACK]` @0xc3555e00 **FACT** |

**command_id relativo (FACT, estatico):** con B = base del modulo RF-TEST en el espacio 0..0x31:
```
TX_CONTROL = B + 2     TX_MEASURE = B + 6
```
Si B = 0 (INFERENCE, ver reg_order_B.md §4): **TX_CONTROL = command_id 2, TX_MEASURE = command_id 6.**
(Coincide con lo previamente asumido: reg_order_B.md dice "TX_CONTROL=cmd 2, TX_MEASURE=cmd 6".)

### 3.2 Mecanica del unpacker TX_CONTROL (0xd8188428) — FACT (disasm)
```
d8188428: call 0xd8829924 ; allocframe(#0x38)
d818842c: p0 = cmp.eq(r0,#0)               ; valida ctx no-null
d8188434: r16=ctx; r2 = memw(r0+#0x4)      ; r2 = property array ptr
d818848c: r0 = memub(r22+#0x12)            ; r20 = property_id (leido del descriptor)
d8188490: p0 = cmp.gtu(r20,#0x14)          ; GATE: property_id <= 0x14 (20)   [FACT]
d81884a4: r17 = addasl(r18,r20,#0x6)       ; slot = base + property_id*0x40 (STRIDE 0x40)  [FACT]
d81884a8: r0 = memw(r17+#0xc)              ; handler/flags del property
d81884b4: if(!p0) call 0xd81886d0          ; procesa el property
d8188518: r2=memw(r17+0); r1=memw(r17+0x38); callr r2  ; callr al setter del property
```
-> TX_CONTROL indexa una **tabla de contexto por property (stride 0x40)**, gate property_id <= 0x14, y
   despacha por `callr` al setter de cada property. **FACT.**

### 3.3 TLVs de TX_CONTROL (tabla de nombres @0xc906c438) — FACT
TX_CONTROL fija los parametros de CADA ETAPA de la cadena TX (gain, PA, ET, DPD). Es el comando de override
TX de bajo nivel. Los 40 properties (indice = property_id):

| id | nombre | etapa / uso |
|---:|--------|-------------|
| 1 | **TX_ENABLE** | **prende/apaga TX** |
| 2 | PATH_INDEX | tx path/chain |
| 3 | SIG_PATH | signal path |
| 4 | RFM_DEVICE | device RF (que SDR735 path) |
| 5 | ANT_PATH | antenna path (que ASM/PA) |
| 6 | XPT_MODE | modo XPT (APT / EPT / ET) |
| 7/8 | IQ_GAIN_ACTION / **IQ_GAIN** | gain digital IQ (baseband) |
| 9/10 | TXFE_GAIN_ACTION / **TXFE_GAIN** | gain del Tx Front-End |
| 11/12 | ENV_SCALE_ACTION / **ENV_SCALE** | escala de envolvente (ET) |
| 13 | **RGI** | Radio Gain Index (indice de gain del PA/RFFE) |
| 14 | **PA_BIAS** | bias del PA |
| 15 | **PA_STATE** | PA gain state (rango de potencia) |
| 16 | **PA_CURRENT** | corriente del PA |
| 17/18 | DELAY_ACTION / DELAY | delay de timing |
| 19 | DATA_TYPE | tipo de dato TX |
| 20 | SLOT_NUMBER | slot |
| 21 | **DATA_SOURCE** | **fuente del waveform (tono/pattern/IQ) — CLAVE §5** |
| 22 | MOD_TYPE | tipo de modulacion |
| 23 | **ENV_GAIN** | gain de envolvente (ET) |
| 24 | BEAM_ID | beam (NR5G) |
| 25/27 | PRE_COMB_IQ_GAIN_ACTION / PRE_COMB_IQ_GAIN | gain IQ pre-combinador |
| 26 | CARR_IDX | carrier index |
| 28 | RF_TRX_IDX | indice transceiver |
| 29 | PLL_ID | PLL |
| 30 | SPR_ACTION | spur action |
| 31 | **TX_MOD_TYPE** | modulacion TX |
| 32 | MIXER_GAIN | gain del mixer (up-converter) |
| 33 | PHASE_SHIFTER | phase shifter |
| 34/35 | DCOC_I_COMP / DCOC_Q_COMP | DC offset comp I/Q |
| 36/37 | IQ_COMP_GAIN / IQ_COMP_CROSS | IQ imbalance comp |
| 39 | **ET_VMIN** | Vmin del envelope tracker |

**FACT (tabla verificada).** El gate del unpacker es property_id <= 0x14 (20), pero la tabla tiene 40 nombres;
los indices > 0x14 (RGI..ET_VMIN, 13..39) se despachan por un segundo path/estructura (offset 0x28 del slot,
visto en el disasm 0xd81884b8 `r18=add(r17,#0x28)`). **FACT/INFERENCE.**

### 3.4 TLVs de TX_MEASURE (tabla de nombres @0xc906c850) — FACT
TX_MEASURE es el comando de **medir/fijar potencia TX y forma de onda** (el equivalente TX de RX_MEASURE):

| id | nombre | uso |
|---:|--------|-----|
| 1 | **TX_CARRIER** | carrier TX |
| 2 | RFM_DEVICE | device/path |
| 3 | **TX_ACTION** | accion (medir / arrancar TX / parar) |
| 4 | **TX_POWER** | **potencia TX objetivo (dBm) — CLAVE** |
| 5 | RB_CONFIG | config de resource blocks |
| 6 | SIG_PATH | signal path |
| 7 | NUM_OF_RB | numero de RBs |
| 8 | **TX_WAVEFORM** | **forma de onda TX (tipo) — CLAVE §5** |
| 9 | NETWORK_SIGNAL | NS (network signalling -> band restrictions §7) |
| 10 | TX_SLOT | slot |
| 11 | **MODULATION_TYPE** | modulacion (QPSK/16QAM/...) |
| 12 | TX_ON_DURATION | duracion del burst |
| 13 | UL_BURST_PATTRN | patron de burst UL |
| 14 | BEAM_ID | beam |
| 15 | **UE_POWER_CLASS** | clase de potencia (limita Pmax §7) |
| 16 | TIME_US | timing |
| 17 | SUB_FRAME_CONFIG | config de subframe |
| 18/19 | TX_POWER_SWEEP_STOP / _STEP | barrido de potencia |
| 20 | SUB_TECH | sub-tech |
| 21/22 | NUM_POWER_BLOCK_TR / TX_PWR_PER_PWR_BLK | potencia por bloque |
| 23/24 | START_SYMBOL / STOP_SYMBOL | ventana de simbolos |
| 25 | TIMING_ADVANCE | timing advance |
| 29 | WAVEFORM_IMMEDIATE_TRIGGER | trigger inmediato del waveform |
| 30 | MIMO_ENABLED | MIMO |
| 31 | IS_RA_TYPE_0 | resource allocation type |
| 32 | IF_BACKOFF | back-off |
| 33-36 | C1/C2_RATYPE0_START_RB / NRB_ALLOC | asignacion de RBs por cluster |

**FACT (tabla verificada).** Repack (respuesta): `[FTM.RFTEST][TX_MEASURE][REPACK]` @0xc3555c70 devuelve
medidas (formato con 4x u16 -> IQ/valor de 64 bits, o `[%4d][0x%8x]` = tamano + puntero, ver §5). **FACT.**

### 3.5 Overrides TX de bajo nivel (RFDEBUG / MC) — FACT
- `rflte_ftm_mc_override_tx_freq` (@0xce6e0968) — override de la freq TX (independiente del EARFCN).
- `rflte_ftm_xpt_override_txagc` (@0xce6e0810) — override del lazo TxAGC/XPT (fija gain manual).
- `rflte_ftm_mc_enable_tx_agc` / `_disable_tx_agc` (@0xce6e0b68/@0xce6e0ac8) — prende/apaga el lazo de potencia.
- `[FTM.RFDEBUG][RX_OVERRIDE]` @0xce6d3a48 (existe un override analogo; el de TX se enruta por TX_CONTROL). **FACT.**

### 3.6 Como se prende el PA y se fija la potencia — secuencia (INFERENCE con base FACT)
```
1) TX_MEASURE: TX_CARRIER, RFM_DEVICE, BAND(via RADIO_CONFIG previo), TX_POWER=<dBm>,
   TX_WAVEFORM=<tipo>, MODULATION_TYPE, NUM_OF_RB, TX_ACTION=<start>
      -> ftm_lte_txagc_preprocess_tx_action (@0xce6e12e8)
      -> rflte_dispatch_tx_on_ind (@0xce6e7490)  [TX ON]
      -> rflte_dm_update_pa_on_event (@0xce6f0b80): construye script RFFE que ENCIENDE EL PA
      -> rflte_mc_tx_config_common_txagc (@0xce6e87e8) + rflte_dm_set_txagc_params (@0xce6f24e0):
           fija IQ_GAIN + TXFE_GAIN + ENV_SCALE + PA_STATE para dar TX_POWER   [FIJA LA POTENCIA]
      -> rf_rffe_iu_common_prepare_txagc_fixup_table (@0xcea8c2e0): PA gain state -> writes RFFE
2) (fine control) TX_CONTROL: TX_ENABLE=1, PA_STATE, IQ_GAIN, TXFE_GAIN, RGI, PA_BIAS, ENV_SCALE
      -> override manual de cada etapa (saca el TxAGC de auto)
```
**FACT (funciones + VAs) / INFERENCE (orden exacto de llamada; cuerpos UNKNOWN-body).**

---

## 4. GENERACION DE WAVEFORM TX — tono / pattern / IQ arbitrario

### 4.1 Fuentes de waveform disponibles (FACT)
La property **DATA_SOURCE** (TX_CONTROL id 21) y **TX_WAVEFORM** (TX_MEASURE id 8) seleccionan que transmite el
modem. Evidencia por tech:

**GSM — tono CW y patrones (FACT):**
- `ftm_gsm_do_tx_cont_tone()` (@0xce6d6748) — **transmite un tono continuo (CW)**.
- `ftm_gsm_do_tx_cont_rnd()` (@0xce6d66b8) — **transmite datos random continuos (patron)**.
- `ftm_gsm_do_tx_stop_cont()` (@0xce6d67d8) — para la TX continua.
-> GSM tiene CW y pattern nativos. **FACT.**

**LTE — waveform modulada / multi-cluster (FACT):**
- `ftm_lte_set_multi_cluster_tx_waveform_handler` (@0xce6e00a0) — configura la forma de onda TX LTE
  (multi-cluster: varias asignaciones de RB). El waveform se define por RB_CONFIG/NUM_OF_RB/MODULATION_TYPE
  (TX_MEASURE) — es un **UL LTE modulado** (PUSCH-like), no IQ arbitrario por defecto. **FACT.**
- `rflte_dm_update_wfs_scaling_factor: tx_waveform_scaling_info` (@0xce6f2990) — escala del waveform. **FACT.**

**NR5G — IQ PLAYBACK arbitrario (FACT, ver §4.2):** SI existe inyeccion de IQ arbitrario.

### 4.2 ¿Se puede inyectar IQ arbitrario para TX? — SI (NR5G), via TX IQ PLAYBACK — FACT

**FACT (fuerte):** el path NR5G-MFTE tiene un **TX IQ playback** que transmite muestras IQ desde un buffer en
memoria, equivalente TX del IQ_CAPTURE en RX:
- `tx_playback_req_p->num_tx_stream_vectors <= 2` (@0xce68b99a) — hasta 2 stream vectors (buffers) por TX.
- `tx_playback_req_p->stream_vector_addr[tx_buffer_idx]` (@0xce68b9d0) — **puntero al buffer de IQ** (alineado a 0x40).
- `tx_playback_req_p->stream_vector_bytes[tx_buffer_idx] <= nr5g_mfte_nr_tx_sample_buffer_max_size_bytes`
  (@0xce68ba91) — **tamano del buffer IQ**, limitado por un maximo.
- `nr5g_mfte_nr_tx_sample_addr[tx_buffer_idx]` (@0xce68bb0a) — direccion del sample buffer (alineado 0x40).
- Estado: `NR5GFW_CAL_CTX_TX_IQ_PLAYBACK_RUNNING` vs `NR5GFW_CAL_CTX_TX_MODULATED_RUNNING` (@0xce6cfb3b) —
  **dos modos: IQ playback (arbitrario) vs modulado (generado por FW).** **FACT.**
- Disparo: mensaje `rx_tx_ctrl_req.data.tx_playback_params` (@0xce6cfbae/@0xce6d0000) con op-mask
  `NR5G_LL1_CAL_FTM_OP_TX_CFG / _TX_DECFG / _TX_START / _TX_STOP` (@0xce6cfdf2/@0xce6cfea6). **FACT.**

**Como (INFERENCE con base FACT):**
```
NR5G-MFTE (Modem Factory Test Environment):
  1) Cargar las muestras IQ en un buffer alineado a 64B (memshare/DDR),
     tamano <= nr5g_mfte_nr_tx_sample_buffer_max_size_bytes.
  2) Enviar rx_tx_ctrl_req con tx_playback_params:
       stream_vector_addr[0..1] = punteros a los buffers IQ
       stream_vector_bytes[0..1] = tamanos
       num_tx_stream_vectors <= 2
       rx_tx_op_mask con NR5G_LL1_CAL_FTM_OP_TX_CFG | _TX_START
  3) El FW pone tx_info.state = NR5GFW_CAL_CTX_TX_IQ_PLAYBACK_RUNNING y reproduce el IQ por el DAC->PA->antena.
  4) NR5G_LL1_CAL_FTM_OP_TX_STOP para detener.
```
- **El comando FTM concreto** que lleva `tx_playback_params` es un mensaje MSGR interno del path
  NR5G-CAL/MFTE (variante `nr5g_mfte_fr_state.variant_ids`). El **selector DIAG/FTM exacto** (subsys/cmd) que
  lo dispara **no se aislo estatico** (UNKNOWN — es un mensaje MSGR, no un TLV RF-TEST directo). El equivalente
  RF-TEST publico es TX_CONTROL con **DATA_SOURCE** (id 21) apuntando a un buffer. **INFERENCE/UNKNOWN-selector.**

**LTE:** no se hallo un "tx_playback" LTE explicito; el waveform LTE se genera con
`ftm_lte_set_multi_cluster_tx_waveform_handler` (RB/mod, no IQ arbitrario). Para IQ arbitrario en sub-6, el
path claro es **NR5G-MFTE tx_playback**. **FACT (ausencia LTE) / FACT (presencia NR5G).**

### 4.3 Resumen fuentes de waveform (FACT)
| tech | tono CW | pattern | modulado (RB) | IQ arbitrario |
|------|:-------:|:-------:|:-------------:|:-------------:|
| GSM  | SI (`do_tx_cont_tone`) | SI (`do_tx_cont_rnd`) | — | — |
| LTE  | via TX_WAVEFORM/CONT_MODE | via BURST_PATTERN | SI (`multi_cluster_tx_waveform`) | no visto |
| NR5G | via TX_WAVEFORM | via UL_BURST_PATTRN | SI (`TX_MODULATED_RUNNING`) | **SI (`TX_IQ_PLAYBACK`)** |

---

## 5. DPD (Digital Predistortion) y ET (Envelope Tracking) — config

### 5.1 DPD — FACT
- **Modelo DPD = txlin (Tx Linearizer)** con LUTs AMAM/AMPM y kernels (memory polynomial). Properties (tabla DPD @0xc906c530/560/588):
  - `KERNEL_MASK`, `QFACTOR`, `NUM_KERNEL_WEIGHTS`, `KERNEL_WEIGHT_I`, `KERNEL_WEIGHT_Q` — coeficientes del
    modelo polinomial (kernels de memoria). Tambien en TX_CONTROL (ids 46-51 seccion 2). **FACT.**
  - `NUM_LUT_VALUES`, `AMAM`, `AMPM`, `GAIN` — LUTs de la curva AM/AM y AM/PM. **FACT.**
- **WB-DPD (banda base):** `tx_dtr_wb_dpd_rate_kHz` (@0xce5cb4a2) — corre en el DTR-TX a tasa dada. **FACT.**
- **Comandos FTM RFDEBUG de DPD:**
  - `[FTM.RFDEBUG][LOAD_UNITY_DPD]` (@0xce6d3778) — **carga una DPD unidad (identidad, sin predist)**.
  - `[FTM.RFDEBUG][VDPD_CONVERSION]` (@0xce6d3958) — conversion VDPD (voltage-DPD, DPD acoplada al ET).
  - `[FTM.RFDEBUG][VDPD_CAL]` (@0xce6d3990) — calibracion VDPD. **FACT.**
- **Autopin (auto-DPD cal):** `fw_psamp_autopin_cal_gen_dpd_lut` (@0xce6ff851) genera la LUT DPD;
  `fw_psamp_autopin_step1/2_cal/online` (@0xce6ff796.., @0xce701e3e..). Requiere NV/QCN. **FACT.**
- **MultiLin (multi-linearizer):** `MultiLin V3 ... Tx Muti-Lin Autopin table, pa_state=%u, waveform_type=%u,
  frequency=%lu` (@0xce6ee720) — tabla de linearizacion por (pa_state, waveform, freq). **FACT.**

**INFERENCE:** para TX limpio (ACLR/EVM ok), cargar la DPD del RFC/NV o correr autopin; para TX crudo sin
predist, `LOAD_UNITY_DPD` deja el PA sin linealizar (util para caracterizar). **INFERENCE con base FACT.**

### 5.2 ET (Envelope Tracking) — FACT
- **Chip = QET6200** (§2.5). Modo se elige por `XPT_MODE` (TX_CONTROL id 6): APT / EPT / ET.
- **Config ET:**
  - `rflte_core_task_ctl_get_tx_intra_ulca_apt_et_control_nv` (@0xce6e2e50) — control APT/ET desde NV.
  - `rfdevice_papm_hub_udpdate_vdd_table` (@0xce6d4900) — tabla VDD del ET (voltaje vs envolvente).
  - `[PAPM] Tech %d, bw %d not supported by QET %d` (@0xcea8b8f8) — soporte por (tech, bw).
  - QPOET thresholds: `rflte_nv_query_qpoet_rssi_threshold_data_from_nv` (@0xce6eb900). **FACT.**
- **Params ET en FTM (tabla @0xc906c530):** `ETVD_MAX`(5), `ETVD_MIN`(6), `DETROUGH`(7), `QPOET_VMIN`(8),
  `QPOET_VMAX`(9), `SCALE_DETROUGH`(11). En TX_CONTROL: `ENV_SCALE`(12), `ENV_GAIN`(23), `ET_VMIN`(39). **FACT.**
- **VDPD = DPD acoplada a ET** (voltage-domain DPD): los comandos VDPD_CONVERSION/VDPD_CAL (§5.1) linkean
  la curva DPD con la trayectoria de voltaje del ET. **FACT/INFERENCE.**

**INFERENCE:** para TX limpio con ET, XPT_MODE=ET + VDPD cargada + QET6200 con su VDD table (RFC/NV). Para TX
simple (APT o sin ET), XPT_MODE=APT y ENV_SCALE fijo. **INFERENCE con base FACT.**

---

## 6. SAFETY / LIMITES DE POTENCIA — que impide TX arbitrario

**Hay multiples capas de clamp de potencia. Documentadas (FACT):**

### 6.1 MTPL (Maximum Transmit Power Limit) — FACT
- `rflte_core_txpl_get_max_tx_power_nv` (@0xce6e1c70) y `_based_on_ue_power_class` (@0xce6e1b60) — **Pmax por NV
  y por UE power class**. `rflte_core_compute_mtpl_per_sf` (@0xce6e2668) computa el MTPL por subframe. **FACT.**
- `rflte_core_txpl_set_mtpl_for_sg` (@0xce6e1f80). El TxAGC clampea al MTPL. **FACT.**
- **UE_POWER_CLASS** es un TLV (TX_MEASURE id 15) — define el limite; no es libre. **FACT.**

### 6.2 SAR / RT-SAR (Specific Absorption Rate) — FACT (limite duro)
- **LMTSMGR (Limits Manager)** aplica RT-SAR: `Assertion tx_power < LMTSMGR_RTSAR_MAX_TX_PWR_DBM_10` (@0xcea91b72)
  — **clamp duro de potencia TX por SAR** (en dBm*10). **FACT.**
- `rflte_core_txpl_set_sar_power_limit` (@0xce6e1fd0), `rflte_core_txpl_get_sar_pcmax_offset` (str @0xce6e20b0). **FACT.**
- RT-SAR config: `stx_config->tech_records`, `pd_char_tbls` (power-density char tables), `ant_grps` — tablas de
  backoff por antena/tech/banda (@0xce72f048.., @0xce731610..). Es RUNTIME-driven (DSI/proximity sensor). **FACT.**
- `RT-SAR: No SAR record for antenna %d active tech %d band %d` (@0xce72ea20) — si no hay record SAR, se
  aplica el default (conservador). **FACT.**

### 6.3 NS (Network Signalling) / band restrictions — FACT
- `rflte_core_pa_swp_update_based_ns_type` (@0xce6e2b50) — **el PA sweep (Pmax) depende del NS_type** (network
  signalling value), que codifica restricciones de banda/emision (additional spectrum emission). **FACT.**
- TLV **NETWORK_SIGNAL** (TX_MEASURE id 9), **NS_VAL_TYPE** (RADIO_CONFIG id 59). **FACT.**
- Band restrictions: `lte_ml1_common_band_get_band_from_ul_earfcn` (@0xcea0d5f0) — la banda se deriva del UL
  EARFCN; canales fuera de banda -> `tx_freq != LTE_ML1_COMMON_INVALID_VALUE_FREQ` (@0xce936b10) rechaza. **FACT.**

### 6.4 PA state / power class caps — FACT
- `RFIU_NR5G_POWER_CLASS3 / CLASS2` (@0xce6ce918) — el firmware valida la power class.
- `pa_state <= RFIU_NR5G_TX_PA_STATE3` (@0xce6cdd0b) — el PA state esta acotado (max state = max power range). **FACT.**
- `WFW_MAX_POWER_OFFSET` (@0xce5ec68f) — offset de potencia acotado en firmware WFW. **FACT.**

### 6.5 ¿Que es posible? (INFERENCE con base FACT)
- **Dentro de los limites de cal:** con NV/RFC cargada, TX_MEASURE/TX_CONTROL permiten fijar potencia y
  waveform hasta el min(MTPL_nv, SAR_clamp, power_class, NS_restriction). Esto es TX controlado legitimo.
- **TX arbitrario sin limite:** los clamps SAR/MTPL son **asserts + clamps en el TxAGC** que corren siempre en
  el path de mision; en el path FTM (RF/CAL mode) muchos se relajan pero **el SAR clamp del LMTSMGR y el
  Pmax por power class siguen** (son asserts que crashean, no bypasseables por TLV). El limite ULTIMO es
  **fisico**: PA state max + Pmax de cal del RFC. **INFERENCE fuerte.**
- **No hay** un TLV "disable_all_limits". El override mas fuerte es `rflte_ftm_xpt_override_txagc` (@0xce6e0810)
  + TX_CONTROL (gain manual por etapa), que **fija el gain crudo saltando el lazo AGC** — pero sigue acotado
  por PA_STATE y por el clamp SAR del LMTSMGR. **INFERENCE.**

---

## 7. FACT / INFERENCE / UNKNOWN (con VAs)

**FACT (strings/instrucciones verificados):**
- Comandos FTM TX (RF-TEST module init 0xd8182640): **TX_CONTROL slot2 unpacker 0xd8188428** (repack
  0xd8188600, bufsz 0x540, F3 @0xc3555c10); **TX_MEASURE slot6 unpacker 0xd818ab6c** (repack 0xd818bb80,
  bufsz 0x60, F3 @0xc3555c70). command_id relativo: TX_CONTROL=B+2, TX_MEASURE=B+6.
- TX_CONTROL unpacker: gate property_id <= 0x14, stride 0x40 (`addasl(r18,r20,#6)` @0xd81884a4), callr setter.
- Tabla TLV TX_CONTROL @0xc906c438 (TX_ENABLE=1, PA_STATE=15, PA_BIAS=14, RGI=13, IQ_GAIN=8, TXFE_GAIN=10,
  ENV_SCALE=12, DATA_SOURCE=21, ENV_GAIN=23, ET_VMIN=39, KERNEL_WEIGHT_I/Q, AMAM/AMPM en tablas hermanas).
- Tabla TLV TX_MEASURE @0xc906c850 (TX_CARRIER=1, TX_ACTION=3, TX_POWER=4, TX_WAVEFORM=8, MODULATION_TYPE=11,
  NETWORK_SIGNAL=9, UE_POWER_CLASS=15, TX_POWER_SWEEP_STOP/STEP=18/19).
- TxLM: alloc/dealloc asserts @0xce604f93; wfw_tx_lm_cfg SM; txfe_config/ul_bw asserts; RFLTE_MC_TXLM_INVALID_HANDLE.
- DAC/DTR-TX: dac_freq_khz/dtr_ib_freq_khz asserts @0xce5cb4a2; "Unsupported configuredDacRate".
- TRX/tune scripts: config_transceiver_scripts @0xce6e4090; build_tune_script @0xce6e42f8; get_txpll_script
  @0xce6e7f88; config_msm @0xce6e7dc8; afc_update @0xce6e8000; get_ffh_script @0xce6e8068.
- PA: update_pa_on_event @0xce6f0b80; copy_pa_swp_to_p_cmax @0xce6f1780; RFFE txagc_fixup_table @0xcea8c2e0;
  pa_icq_info @0xce6ddb88; PA_STATE0..3 @0xce6cdd0b.
- ET/QET6200: QET6200 cal @0xce6d4878; PAPM VDD table @0xce6d4900; [PAPM] not supported by QET @0xcea8b8f8;
  qpoet threshold @0xce6eb900; apt_et_control_nv @0xce6e2e50.
- TxAGC: config_common_txagc @0xce6e87e8; set_txagc_params @0xce6f24e0; populate_txlin_data @0xce6f23d8;
  power_estimation_logging @0xce6ed1f8; FTM enable/disable @0xce6e0b68/@0xce6e0ac8; xpt_override @0xce6e0810.
- Waveform: GSM cont_tone @0xce6d6748 (CW), cont_rnd @0xce6d66b8 (pattern), stop_cont @0xce6d67d8;
  LTE multi_cluster_tx_waveform @0xce6e00a0; wfs_scaling_factor @0xce6f2990.
- **NR5G TX IQ PLAYBACK (IQ arbitrario):** tx_playback_req_p->stream_vector_addr/bytes @0xce68b99a-ba91;
  num_tx_stream_vectors <= 2; NR5GFW_CAL_CTX_TX_IQ_PLAYBACK_RUNNING @0xce6cfb3b; rx_tx_ctrl_req op-mask
  NR5G_LL1_CAL_FTM_OP_TX_CFG/START/STOP @0xce6cfdf2/ea6; nr5g_mfte_nr_tx_sample_addr @0xce68bb0a.
- DPD: LOAD_UNITY_DPD @0xce6d3778; VDPD_CONVERSION @0xce6d3958; VDPD_CAL @0xce6d3990; autopin_cal_gen_dpd_lut
  @0xce6ff851; MultiLin V3 @0xce6ee720; kernel/AMAM/AMPM tablas @0xc906c530/560/588.
- Safety: MTPL get_max_tx_power_nv @0xce6e1c70/@0xce6e1b60; SAR clamp tx_power<LMTSMGR_RTSAR_MAX_TX_PWR_DBM_10
  @0xcea91b72; set_sar_power_limit @0xce6e1fd0; NS pa_swp @0xce6e2b50; power_class @0xce6ce918;
  WFW_MAX_POWER_OFFSET @0xce5ec68f.

**INFERENCE:**
- DAC/mixer/LO/TxPLL son HW interno del SDR735 controlado por scripts RFFE (registros en RFC/NV).
- Secuencia de encendido TX: TX_MEASURE(start) -> preprocess_tx_action -> dispatch_tx_on_ind -> update_pa_on_event
  (PA ON, script RFFE) -> config_common_txagc/set_txagc_params (fija potencia repartiendo gain).
- IQ arbitrario TX = NR5G-MFTE tx_playback (buffers 64B-alineados, <= max_size, hasta 2 vectors).
- TX limpio: XPT_MODE=ET + VDPD cargada (o autopin) para linealizar el PA con QET6200.
- Los clamps SAR (LMTSMGR) y power-class son asserts que no se bypassean por TLV; limite ultimo = PA_STATE max + Pmax de cal.

**UNKNOWN (solo en vivo o con blob ausente):**
- **Cuerpos de las funciones RFLTE_MC_TX/RFLM/RFDEVICE** (codigo en q6zip no descomprimido; §0).
- **command_id absoluto** de TX_CONTROL/TX_MEASURE (base B runtime; INFERENCE B=0 -> cmd 2 y 6). Cerrar en vivo
  con COMMAND_CAPABILITY (CMD_MASK/QUERY_COMMAND).
- **Selector DIAG/FTM exacto** que dispara el NR5G tx_playback (es un mensaje MSGR interno, no un TLV RF-TEST directo).
- **Registros concretos del SDR735** para TX (mixer/LO/TxPLL, DAC) — viven en el blob RFC/NV.
- **Valores enum** de DATA_SOURCE (tono/pattern/IQ), TX_WAVEFORM, XPT_MODE (APT/EPT/ET), TX_ACTION.
- **Valores numericos** de los clamps SAR/MTPL (LMTSMGR_RTSAR_MAX_TX_PWR_DBM_10, Pmax por power class) — en NV/RFC.
- **nr5g_mfte_nr_tx_sample_buffer_max_size_bytes** (tamano max del buffer IQ TX) — runtime/NV.

---

## 8. RESUMEN ACCIONABLE (para TX futuro)

```
Prerrequisitos (igual que RX, de enter_mode_path.md):
  · FTM set RF/CAL mode  -> rfm_init + MCPM ON
  · NV/RFC cargada       -> Tx cal (txlin/DPD), PA sweep, SAR/MTPL tables, QET6200 VDD

Encender TX y fijar potencia (LTE, path RF-TEST):
  1) TECH_ENTER(LTE)                         [FTM-RFDEBUG sub_command=13]
  2) RADIO_CONFIG: TX_CARRIER(2), RFM_DEVICE(3), BAND(5), CHANNEL(6)=UL_EARFCN, BANDWIDTH(7)
        -> ftm_lte_tune -> rflte_mc_tx_build_tune_script -> config_transceiver_scripts (TxPLL/mixer ON)
        -> TxLM alloc + wfw_tx_lm_cfg (DAC/TXFE config)
  3) TX_MEASURE(cmd ~6): TX_ACTION=start, TX_POWER=<dBm>, TX_WAVEFORM=<tipo>, MODULATION_TYPE,
        NUM_OF_RB, UE_POWER_CLASS(15)
        -> dispatch_tx_on_ind -> update_pa_on_event (PA ON) -> config_common_txagc/set_txagc_params
           (reparte gain: IQ_GAIN+TXFE_GAIN+ENV_SCALE+PA_STATE) hasta min(TX_POWER, SAR, MTPL, power_class)
  4) (gain manual crudo) TX_CONTROL(cmd ~2): TX_ENABLE(1)=1, PA_STATE(15), IQ_GAIN(8), TXFE_GAIN(10),
        RGI(13), PA_BIAS(14), ENV_SCALE(12), XPT_MODE(6)
        + rflte_ftm_xpt_override_txagc para saltar el lazo AGC (sigue acotado por SAR/PA_STATE)

Inyectar IQ arbitrario (NR5G-MFTE):
  · buffer IQ alineado 64B, <= nr5g_mfte_nr_tx_sample_buffer_max_size_bytes, hasta 2 vectors
  · rx_tx_ctrl_req.tx_playback_params{stream_vector_addr[], stream_vector_bytes[], num_tx_stream_vectors}
  · op-mask NR5G_LL1_CAL_FTM_OP_TX_CFG | _TX_START  -> TX_IQ_PLAYBACK_RUNNING; _TX_STOP para parar

TX limpio:
  · XPT_MODE=ET (QET6200), VDPD cargada (VDPD_CAL/CONVERSION) o autopin; o LOAD_UNITY_DPD para PA crudo

Limites que quedan SIEMPRE:
  · SAR clamp (LMTSMGR, assert tx_power<MAX_TX_PWR_DBM_10), MTPL(NV), UE power class, NS band restriction,
    PA_STATE max. No hay TLV para desactivarlos; limite ultimo = Pmax de cal del RFC.
```

---

## 9. REPRODUCIR
```bash
# Strings de la cadena TX (seg27_dec, VA base 0xce480000):
python3 - <<'PY'
d=open('/tmp/modemre/seg27_dec.bin','rb').read(); B=0xce480000
def va(s):
  i=d.find(s.encode()); return hex(B+i) if i>=0 else None
for s in ("rflte_ftm_mc_enable_tx_agc","rflte_dm_update_pa_on_event","QET6200",
          "tx_playback_req_p->num_tx_stream_vectors","NR5GFW_CAL_CTX_TX_IQ_PLAYBACK",
          "ftm_gsm_do_tx_cont_tone","rflte_mc_tx_build_tune_script","rf_rffe_iu_common_prepare_txagc_fixup_table",
          "[FTM.RFDEBUG][VDPD_CAL]","tx_power < LMTSMGR_RTSAR_MAX_TX_PWR_DBM_10"):
    print(va(s), s)
PY
# Comandos FTM TX (registro y unpackers, base 0xd8000000):
/tmp/modemre/dis.sh 0xd8182640 0x2a0   # RF-TEST module init: slot2=TX_CONTROL, slot6=TX_MEASURE
/tmp/modemre/dis.sh 0xd8188428 0x100   # TX_CONTROL unpacker (gate prop<=0x14, stride 0x40)
/tmp/modemre/dis.sh 0xd818ab08 0x70    # TX_MEASURE reg (unpack 0xd818ab6c, repack 0xd818bb80)
# Tablas de TLV (b23): TX_CONTROL @0xc906c438, TX_MEASURE @0xc906c850, DPD/ET @0xc906c530
# F3 names (word[2]=fmt ptr): 0xc3555c10=TX_CONTROL, 0xc3555c70/c80=TX_MEASURE
```
