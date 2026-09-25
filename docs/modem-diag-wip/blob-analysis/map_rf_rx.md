# MAPA COMPLETO DE LA CADENA RX RF — Qualcomm SM6375 (MPSS.HI.4.3.4)
Build: **MPSS.HI.4.3.4-00494-MANNAR_GEN_PACK-1.24452.133** (SM6375 / Moto G82 5G)
Objetivo: habilitar captura de IQ raw (RX) — mapa de punta a punta de la cadena de recepción.

Imágenes:
- `clade_dec_full.bin` (VA 0xd8000000, 10MB) = **código FTM/DIAG dispatch descomprimido** (llvm-objdump hexagon v66 vía `dis.sh`).
- `clade_dec.bin` (VA 0xd8000000..0xdb397464) = extensión del mismo dispatch.
- `seg27_dec.bin` (VA base **0xce480000**) = **modem.b27 descomprimido = rodata RF (RFLTE/RFLM/RFDEVICE + LTE-ML1)**. Contiene TODOS los strings/nombres de función RF.

Leyenda: **FACT** = string/instrucción verificada byte-a-byte (VA citada) · **INFERENCE** = deducción con base dura · **UNKNOWN** = sólo resoluble en vivo o con blob ausente.

---

## 0. LÍMITE ESTRUCTURAL CRÍTICO (leer primero) — dónde vive el código RF

**FACT (verificado en este pase):** el **código** de la cadena RX (RFLTE / RFLM / RFDEVICE) **NO está
presente en ningún blob descomprimido disponible**. Se comprobó buscando referencias (literal-pool y
constant-extender `immext`, decoder validado contra `0xca733d10`/`0xca65b414`) a las VAs de los strings
RF (`0xce6e08a8 rflte_ftm_mc_wakeup`, `0xce6e72c0 rflte_mc_carrier_activate`, `0xce8bd598`, etc.):

| blob | cobertura VA | refs a strings RF |
|------|-------------|-------------------|
| clade_dec_full.bin | 0xd8000000..0xd8a00000 | **0** |
| clade_dec.bin | 0xd8000000..0xdb397464 | **0** |
| clade_all.bin | 0xcc000000..0xcf000000 | **0** |
| modem.b10/b13 (raw, R-E) | 0xc0a80000 / 0xc0d36000 | **0** (siguen q6zip-comprimidos) |

- Lo **presente y disassemblable** es el **dispatch FTM/DIAG** (clade @0xd8xxxxxx): validación de paquete,
  tabla de sub_commands, unpackers de TLV, y el framework RF-TEST multi-tech. Ese código **llama** a la
  cadena RX pero por `callr` a punteros resueltos en runtime; los callees (RFLTE) están en un segmento
  q6zip no extraído.
- Todos los **nombres de función RX, estructuras, asserts y tablas de cal** SÍ están (rodata b27
  @0xce480000). Por eso este mapa es **completo a nivel de bloques/funciones/strings (FACT)**, pero los
  **cuerpos de las funciones RFLTE no se pudieron desensamblar** (marcado UNKNOWN-body donde aplica).

**Consecuencia para IQ capture:** el camino accionable es el **framework FTM-RF-TEST** (código presente,
§6) que dispara la cadena RX; los internos RFLTE se controlan por sus **TLVs** (§6/§7), no hace falta
desensamblar sus cuerpos para capturar IQ con gain conocido.

---

## 1. DIAGRAMA DE LA CADENA RX (bloques → funciones → VAs)

```
                          ANTENA
                            │
              ┌─────────────▼──────────────┐
              │  RF FRONT-END (RFFE/SPMI)   │   control por bus RFFE/SPMI (SID/USID)
              │  · ASM/switch  · eLNA       │   scripts inmediatos + programados
              │  · LNA (gain states)        │
              └─────────────┬──────────────┘
   RFFE bus  ← rfc_get_used_sid_table()               @0xce6d49d0  (mapea dev→bus/SID)
   scripts   ← rffe_finalize_rf_script()              @0xce6d... (str 24648)
   RxAGC tbl ← rf_rffe_iu_common_prepare_rxagc_table()@0xcea8c0f8 (LNA gain→RFFE writes)
   eLNA      ← [ELNA] config/jdet/wakeup              @0xcea8d770 / str 95443..
   ASM       ← rflte_mc_rx_config_asm_device()        @0xce6e3320
   sleep/wk  ← rfdevice_sleep_manager_wakeup_devices_rx()@0xce6dd840
                            │
              ┌─────────────▼──────────────┐
              │  TRANSCEIVER (SDR735)       │   chip RF principal (RFIC WWAN)
              │  · Mixer / down-converter   │   accedido por RFFE/SPMI
              │  · PLL / LO                 │   + SMR526v2 para mmWave (NR FR2)
              │  · ADC (integrado en RFIC)  │
              └─────────────┬──────────────┘
   TRX class ← sdr735_common_class()                  @0xce6fae60 (power-on script, reg dump)
   TRX LTE   ← rfdevice_trx_lte_rx_get_max_bw_supported()@0xce6e4db0
   TRX cmn   ← rfdevice_trx_cmn_get_common_dev_ptr()   @0xce... (str 24626)
   mmW       ← smr526v2_physical_device() / _nr5g_mmw_class()@0xce6ed880/0xce6fb148
   PA/ET     ← QET6200 (envelope tracker, TX-side)     @0xce... (str 24642) [TX, no RX]
                            │  (IQ digital stream tras el ADC)
              ┌─────────────▼──────────────┐
              │  DTR-RX (Digital Rx front)  │   filtrado/decimación/DC/RSB
              │  · FIRSB, decimación, NB DTR│
              └─────────────┬──────────────┘
   DTR chain ← rflm_dtr_rx_activate_chain()            @0xce5f4ccc
   DTR sett. ← rflm_dtr_rx_get_group_settings()        @0xce5f4d15
   DTR ptrs  ← rflm_dtr_rx_get_settings_type_ag_ptr()  @str 3812/3814 (LPM = low power mode)
   NB masks  ← RFLM_DTR_RX_NB0/NB1_MASK                @str 6150/6151
                            │
              ┌─────────────▼──────────────┐
              │  RxLM (Rx Link Manager)     │   config de la cadena digital, buffers/handles
              │  · rxlm_buf_id_antN         │   1 handle por antena/carrier
              │  · Chain Config/Activate    │
              └─────────────┬──────────────┘
   Alloc     ← RxLm Allocation (rx_client, RF-Device)  @0xce6841c8
   Alloc2    ← Not able to allocate RxLM buffer        @0xce604f28
   Chain     ← RFLM Chain Config HW/SW/Activate        @str 3820..3825
   Frontend  ← Chain … in Frontend Evt1 Thread         @str 3894..3898
   Buf idx   ← LTE_LL1_RXFE_RXLM_BUF_IDX_INVALID       @str 3937/5949/6013
                            │
              ┌─────────────▼──────────────┐
              │  RxFE / RXAGC (LTE LL1)     │   frontend LL1, AGC, LNA state machine
              │  · rxfe_rxlm_params         │
              │  · lna_state / gain ranges  │
              └─────────────┬──────────────┘
   RxFE      ← RXFE_MODE_SWITCH / lte_LL1_rxfe_rxlm_params@str 4044/4857
   RXAGC     ← lte_LL1_rxagc_db_ptr / lna_mode_switch   @str 4010/4015
   LNA LUT   ← lte_LL1_rxagc_lna_lut (LNA_LUT_FULL)     @str 3966
   Gain rng  ← RFFW_LTE_MAX_NUM_RX_GAIN_RANGES          @str 3947/3961
                            │
              ┌─────────────▼──────────────┐
              │  SAMPLE CAPTURE / IQ        │   la salida que buscamos
              │  · async_samp_capture       │   buffer DDR/LMEM, no inline
              │  · samp_rec                 │
              └─────────────────────────────┘
   Async cap ← lte_LL1_async_samp_capture_db            @0xce5aa1a2
   Samp rec  ← DL samp rec start (rxlm chain)           @0xce5b5d0e
   FTM IQ    ← rflte_ftm_iq_capture_prop_action_*       @0xce6ded20/0xce6e0c28
   Rx cap req← rx_capture_req_ptr->p_sample_capture_buffer@str 20790/20791
```

**Todas las VAs de la columna derecha son FACT** (strings verificados en seg27_dec.bin @0xce480000).
Los cuerpos de las funciones RFLTE/RFLM son **UNKNOWN-body** (§0).

---

## 2. LA CADENA RX FÍSICA — bloque por bloque

### 2.1 RFFE / RF Front-End (control SPMI/RFFE de LNA/switches) — FACT
- **Bus RFFE/SPMI:** el mapeo dispositivo→bus→SID se hace en `rfc_get_used_sid_table()` (@0xce6d49d0):
  `physical device index %d with defined bus %d larger than RFFE_SHADOW_REG_MAX_BUS %d`. Hay **shadow
  registers** por bus (`RFFE_SHADOW_REG_MAX_BUS`). El acceso a un device concreto:
  `rfdevice_physical_device::rfdevice_phy_dev_get_bus_idx()` (str 25121):
  `No combination of bus = %d, usid = %d for this device` → **cada device RF se direcciona por (bus, USID)**. **FACT.**
- **Scheduler RFFE** (ejecuta scripts programados por evento): `RFFE scheduler … num_supported_buses %d`
  (str 26096), `RFFE_SCHED_DEBU: Task references unknown bus` (str 26108). Ejecuta tasks RFFE con timing. **FACT.**
- **Finalización de script RFFE:** `rffe_finalize_rf_script(): Unsupported execution type %d` (str 24648)
  → los writes RFFE se agrupan en un "rf_script" con tipos de ejecución (inmediato vs programado). **FACT.**
- **LNA (states/gain) via RFFE:** `rf_rffe_iu_common_prepare_rxagc_table()` (@0xcea8c0f8) construye la
  **tabla RxAGC = por cada LNA gain state, la lista de writes RFFE** que ponen el LNA en ese estado:
  `LNA Device Object query failure` / `invalid LNA format num_gain = %d` / `RFFE LNA Script failure for
  row_idx = %d` / `mismatch in number of writes between format and gain script`. **FACT.**
  Esto es exactamente el mapa "gain state → registros RFFE" que necesitás para saber el gain aplicado.
- **eLNA (LNA externo):** clase `[ELNA]` con métodos `config/sleep/suspend/wakeup/jdet_enable/…`
  (str 95443..95451). `[ELNA] get_phase_comp_data(): num_gain_states` (@0xcea8d770). **FACT.**
- **ASM / antenna switch:** `rflte_mc_rx_config_asm_device()` (@0xce6e3320),
  `rflte_mc_rx_disable_asm_device()` (str 25561). **FACT.**
- **Impacto LNA en la cadena (SAWless etc.):** `rflte_mc_rx_get_lna_impact_params()` (@0xce6e3228),
  `rflte_mc_rx_get_sawless_lna_impact_params()` (str 25557). **FACT.**

### 2.2 Transceiver / Mixer / Down-converter / PLL — FACT
- **Chip = SDR735** (ver §5). Clase de driver `sdr735_common_class()` (@0xce6fae60): construye el
  **power-on script**, hace **register dump** (8-bit y 16-bit) — el mixer/LO/PLL viven dentro del RFIC y
  se programan por RFFE. El down-mix a banda base es HW interno del SDR735; el código lo controla vía
  scripts RFFE y `rfdevice_trx_*`. **FACT (chip + clase) / INFERENCE (mixer interno).**
- **BW soportado RX / tune LTE:** `rfdevice_trx_lte_rx_get_max_bw_supported()` (@0xce6e4db0),
  `rfdevice_trx_cmn_get_common_dev_ptr()` (str 24626). **FACT.**
- **mmWave (NR FR2):** `smr526v2_physical_device()` (@0xce6ed880) con `RX tune failed`, `LUT init`,
  `WWAN initialization`. Es el módulo mmW (SMR526v2 = IF+mmW). Para LTE/sub-6 no interviene. **FACT.**

### 2.3 ADC — INFERENCE (dentro del RFIC)
- No hay una clase "ADC" separada para el path WWAN RX; el ADC es un bloque **interno del SDR735**. La
  única cadena "ADC" con string propia es de sensores/therm (`ADC GetInputProps` str 31862), **no** el
  ADC de recepción RF. **INFERENCE:** el ADC RX es HW del transceiver; el firmware ve el **stream digital
  post-ADC** ya en DTR-RX/RxLM, no controla el ADC directamente salvo por el power-on script del RFIC. **INFERENCE fuerte.**

### 2.4 DTR-RX (Digital Rx front-end: filtrado, decimación, DC/RSB) — FACT
- `rflm_dtr_rx_activate_chain()` (@0xce5f4ccc) — activa la cadena digital DTR de RX.
- `rflm_dtr_rx_get_group_settings()` (@0xce5f4d15) — lee settings (incluye `VSRC_WA_COUNT`).
- Punteros de settings por rxlm_buf_idx: `rflm_dtr_rx_get_settings_type_ag_ptr` y su variante `_lpm_`
  (low power mode) (str 3812/3814/4061/4062). **FACT.**
- **NB (narrowband) DTR:** `RFLM_DTR_RX_NB0_MASK | RFLM_DTR_RX_NB1_MASK` (str 6150/6151) → hasta 2 NB
  paths por chain. **FACT.**
- **Decimación / FIRSB (filtro RSB):** `dtr_rxfe_wb_dp_firsb_coeff_c` (str 3801/3819),
  `decimation_ratio * sync_ratio == wb_ratio` (str 20276), `rx_decimate_factor >= 1` (str 27613). **FACT.**

### 2.5 RxLM (Rx Link Manager) — FACT
- **Config de la cadena digital de RX.** Un **handle (rxlm_buf_id/rxlm_buf_idx) por antena/carrier**.
  `LTE_LL1_RXFE_RXLM_BUF_IDX_INVALID` marca inválido (str 3937/5949/6013).
- **Alloc/dealloc:** `RxLm Allocation failed for rx_client %d RF-Device %d` (@0xce6841c8),
  `Not able to allocate RxLM buffer: status = %d` (@0xce604f28), `Invalid client for RxLM allocation=%d`
  (str 9937). Cada cliente (LTE/WCDMA/GSM/1x) pide su buffer. **FACT.**
- **Ciclo de vida de la chain (los estados que hay que atravesar para que RX esté vivo):**
  `Chain Config HW` → `Chain Config SW` → `Chain Activate` (str 3820/3821/3823), o combinado
  `Chain Combined Config HW + SW + Activate` (str 3825); deactivate/deconfig al bajar (str 3806/3808).
  Hay una variante en thread de frontend: `… in Frontend Evt1 Thread` (str 3894..3898). **FACT.**
- **En términos de enter_mode_path.md:** los `rxlm_buf_id_ant0..3 != …_INVALID` (@0xce8bd5d0) que exige
  el ML1 `enter_mode` son exactamente estos handles: **sin RxLM allocado+configurado, no hay RX.** **FACT.**

### 2.6 RxFE / RXAGC (LTE LL1) — FACT
- `lte_LL1_rxfe_rxlm_params[carrier_idx]` con `rxfe_toggle_info` (str 4857) — el frontend LL1 por carrier.
- `RXFE_MODE_SWITCH` (str 4044/4068/4293) — cambia el modo del RxFE (p.ej. entre CA combos).
- **RXAGC:** `lte_LL1_rxagc_db_ptr->lna_mode_switch_db.applied_lna_power_config_mode`
  (`LTE_LL1_LNA_MISSION_MODE` / `LTE_LL1_LNA_LOW_POWER_MODE`, str 4010/4015),
  `lte_LL1_rxagc_lna_lut` con `LNA_LUT_FULL` (str 3966), `RFFW_LTE_MAX_NUM_RX_GAIN_RANGES` (str 3947/3961).
  Esta es la SM de AGC que **elige el LNA gain state en misión**; para IQ con gain fijo hay que
  **overridearla** (§7). **FACT.**

### 2.7 Sample Capture / IQ (la salida) — FACT
- **Async sample capture (ML1):** `lte_LL1_async_samp_capture_db` (@0xce5aa1a2), con `lmem_requested`,
  `samp_capture_duration_ustmr`, `Set LNA faild! ErrCode` (str 3926) — **la captura fija/lee el LNA
  antes de capturar** (evidencia directa de gain conocido). **FACT.**
- **DL samp rec:** `DL samp rec start error: carrier %d rxlm chain not requested yet!` (@0xce5b5d0e) →
  **la captura exige la RxLM chain ya requerida/activada.** **FACT.**
- **FTM IQ capture (path de test, el accionable):** `rflte_ftm_iq_capture_prop_action_8bit/16bit_iq_buff`
  (@0xce6ded20/0xce6ded68) y `rflte_ftm_iq_capture_prop_action_get_cfg: rx_path_data_ptr is NULL for
  rx_path %d carrier %d` (@0xce6e0c28). **FACT.** Buffer 8-bit o 16-bit por muestra.
- **El buffer NO viaja inline:** `rx_capture_req_ptr->p_sample_capture_buffer != NULL` /
  `sample_capture_buffer_size_words != 0` (str 20790/20791). Se devuelve un **puntero DDR/memshare** +
  tamaño (consistente con el REPACK `[ 0x%8x ][ %4d ]` documentado en iq_sequence.md). **FACT.**

---

## 3. CÓMO SE CONFIGURA UN RX CARRIER (desde RADIO_CONFIG / carrier-apply)

### 3.1 Framework FTM-RF-TEST (código PRESENTE, disassemblado) — FACT
El dispatch RF-TEST vive en clade @**0xd8182fec / 0xd8183048** (FACT, desensamblado en este pase):
```
d818306c: p0 = cmp.gtu(r16,#0x14)              ; GATE sub_command <= 0x14
d8183084: immext(#0x11b80)
d8183088: r2 += mpyi(r16, ##0x11ba0)           ; struct por sub_command, STRIDE 0x11ba0
d8183078: r1:0 = combine(r16, ##0xf808d788)    ; base tabla config RF-TEST (region 0xf808d780)
d818308c: memw(r2+#0x3f4) = r19                 ; guarda params del carrier en el ctx del sub_command
d8183090: memw(r2+#0x3f0) = r17                 ; (offsets 0x3f0/0x3f4/0x3f8/0x3fc)
```
→ Cada comando RF-TEST (RADIO_CONFIG, RX_MEASURE, IQ_CAPTURE) indexa una estructura de contexto por
sub_command (stride **0x11ba0**) y guarda ahí los parámetros del carrier. El gate `sub<=0x14` y la config
@0xca789780 (32 refs, ver §5 de enter_mode_path) coinciden con el modelo previo. **FACT.**

### 3.2 TLVs de RADIO_CONFIG / RX_TUNE (qué se programa) — FACT
Tabla de nombres de field @**0xc906c630** (seg23, ya documentada). Los que **arman el RX carrier**:

| field_id | nombre | para RX carrier |
|---------|--------|-----------------|
| 1 | **RX_CARRIER** | índice del carrier RX a configurar |
| 3 | **RFM_DEVICE** | device RF/path (qué SDR735 path / antena) |
| 5 | **BAND** | banda LTE/NR (selecciona LNA/ASM/filtro) |
| 6 | **CHANNEL** | **EARFCN** → freq (mapeo interno) |
| 7 | **BANDWIDTH** | ancho (define decimación DTR + RxLM chain) |
| 9 | SIG_PATH | signal path (primary/diversity) |
| 10 | ANT_PATH | antenna path |
| 12/21 | **CENTER_FREQ** | freq central en kHz (override directo de EARFCN) |
| 25 | TECH_MODE | modo tech (LTE/NR) |
| 13 | ENABLE_XO | prende el XO/reloj RF |

**EARFCN→frecuencia:** el ML1 deriva la banda del EARFCN con
`lte_ml1_common_band_get_band_from_dl_earfcn_with_inst` (@0xce8bdc10, FACT en enter_mode_path.md). En el
path FTM, `ftm_lte_tune` (@0xce6dfee8) usa `current_dl_to_ul_map_ptr` para la banda/duplex. **FACT.**

### 3.3 `rflte_mc_carrier_activate` (@0xce6e72c0) — qué hace (FACT string / INFERENCE-body)
Es el **carrier-apply del path RF (multi-carrier controller)**. Strings verificados:
- `Invalid parameters in X2L case … is dl req has wrong va` (@0xce6e72c0)
- `rflte_mc_carrier_activate_v2: Memory Allocation Failed` (@0xce6e7370)
- `get rx_tech_data failed! rx_handle: %d` (@0xce6e73b0)
**INFERENCE (fuerte, por los strings y el nombre):** dada (band, EARFCN/freq, BW, rx_handle/RxLM,
rfm_device), **construye los rf_scripts RFFE**, **configura la RxLM chain** (via
`rflte_mc_rx_generate_config_tables` @0xce6e3108), y **activa** la cadena RX física para ese carrier
(alloca `rx_tech_data`, valida el `rx_handle`). Cuerpo no desensamblado (§0). **UNKNOWN-body.**

### 3.4 `rflte_ftm_mc_wakeup` (@0xce6e08a8) — qué hace para prender RX (FACT string / INFERENCE-body)
- `invalid antpath on sub[%d], cc[%d], path[%d]` (@0xce6e08a8) y variante `sawless path` (@0xce6e08f0).
- **INFERENCE (fuerte):** es el **"encender el RF" del path FTM/CAL**: recorre (sub, cc, antpath) y
  **despierta los devices RX** llamando a `rfdevice_sleep_manager_wakeup_devices_rx` (@0xce6dd840), que
  genera un **script de writes inmediatos RFFE** (`imm_write_script_ptr`) para sacar LNA/ASM/SDR735 de
  sleep. No pasa por el `enter_mode` del ML1 (path distinto, ver enter_mode_path.md). **INFERENCE.**

### 3.5 Secuencia lógica de carrier-apply (INFERENCE con base FACT)
```
RADIO_CONFIG(BAND, CHANNEL/EARFCN, BANDWIDTH, RFM_DEVICE, RX_CARRIER)
   → ftm_lte_tune (@0xce6dfee8)            : resuelve banda/duplex del EARFCN
   → rflte_mc_carrier_activate (@0xce6e72c0): 
        · rflte_mc_rx_generate_config_tables (@0xce6e3108)  → tablas RxAGC/RFFE por LNA state
        · RxLM alloc + Chain Config HW/SW/Activate          → rxlm_buf_idx válido
        · rflm_dtr_rx_activate_chain (@0xce5f4ccc)          → DTR-RX (decimación por BW)
   → rflte_ftm_mc_wakeup (@0xce6e08a8):
        · rfdevice_sleep_manager_wakeup_devices_rx (@0xce6dd840) → LNA/ASM/SDR735 ON (script RFFE)
   ⇒ cadena RX viva; lista para IQ_CAPTURE
```
**FACT (funciones existen y VAs) / INFERENCE (orden exacto de llamada, cuerpos no desensamblados).**

---

## 4. CALIBRACIÓN RX (dónde están las tablas, cómo se cargan de NV)

### 4.1 Parseo de NV RX (FACT)
- **RX cal offset (NV):** `RFNV_DATA_TYPE_STAND_ALONE_RX_CAL_OFFSET_V*` — el NVMGR exige
  **"One and only one instance … per RFM path"** (@0xce6d4db0). → hay **un container de RX cal offset por
  path RFM**. **FACT.**
- **Parser cal RX:** `rflte_nv_parse_rx_cal_data` (@0xce6eba58):
  `rflte_nv_tbl_ptr->valid_cal_rfm_path == NULL`, `More CAL Paths found than memory allocated`. → arma la
  **tabla de paths calibrados** (`valid_cal_rfm_path`). **FACT.**
- **Datos RX estáticos:** `rflte_nv_process_rx_static_data` (@0xce6eb430) y
  `rflte_nv_populate_rx_static_data` (str 26016): incluye **spur lists** por container,
  `rflte_nv_allocate_mem_qpoet_rssi_threshold` (umbrales RSSI/QPOET, str 26008). **FACT.**
- **Aplicación a la SM de AGC:** `rflte_dm_rxagc_config_update_switchpoints_and_lna_offsets_in_dm`
  (@0xce6f0960): `Rx cal data is from wrong tech: %d`, `Rx cal data is NULL for rfm dev %d, sig path %d,
  ant path %d`. → **carga los switchpoints (umbrales de cambio de LNA state) y los LNA gain offsets** en
  el Device Manager. **FACT.**
- **Estados de gain calibrados:** `common_instance_p->cal_data_p->num_gain_states <=
  RFNV_MAX_STATIC_LNA_GAIN_STATES` (@0xce6f6298) — hay **N gain states estáticos calibrados por LNA**,
  cada uno con su offset. `[ELNA] get_phase_comp_data(): num_gain_states` (@0xcea8d770). **FACT.**

### 4.2 Qué cal hace falta para que la IQ tenga sentido (gain conocido) — INFERENCE con base FACT
Para convertir muestras IQ crudas a dBm/nivel absoluto necesitás:
1. **RX cal offset por path** (`RFNV_..._RX_CAL_OFFSET`, @0xce6d4db0): offset absoluto de ganancia del
   path RFM (antena→ADC). **FACT (existe) / crítico.**
2. **LNA gain offsets por gain state** (switchpoints + offsets cargados por
   `rflte_dm_rxagc_config_update_switchpoints_and_lna_offsets_in_dm` @0xce6f0960): el ΔdB de cada LNA
   state. Sabiendo **qué state estaba activo** (leer RX_AGC/LNA_GS, §5) + su offset → gain total. **FACT.**
3. **Freq comp:** los `rx_static_data` incluyen compensación por frecuencia/spur; sin ella el gain varía
   con el EARFCN. **FACT (existe) / INFERENCE (que afecta el nivel absoluto).**
4. **Phase comp (para RSB/IQ balance)** (`get_phase_comp_data` @0xcea8d770): corrige I/Q imbalance; sin
   ella la IQ tiene RSB residual. **FACT.**

**INFERENCE práctica:** con el device **calibrado (NV/RFC presente)** y capturando con **gain fijo
conocido** (override LNA a un state fijo, §7) + leyendo el **RX cal offset del path** y el **offset del
LNA state usado**, la IQ es interpretable en nivel absoluto. Sin NV cargada, el gain es indeterminado
(los asserts `RFM_INIT WAS NEVER CALLED` / `rxlm_buf_idx INVALID` bloquean antes). **INFERENCE fuerte.**

---

## 5. EL TRANSCEIVER CHIP Y SU ACCESO

### 5.1 Identificación (FACT)
| chip | rol | evidencia |
|------|-----|-----------|
| **SDR735** | **Transceiver WWAN principal (RX+TX, sub-6)** — mixer/LO/PLL/ADC internos | `sdr735_common_class()` @0xce6fae60 (power-on script, reg dump 8/16-bit) |
| **SMR526v2** | mmWave (NR FR2) IF+mmW — no aplica a LTE/sub-6 RX | `smr526v2_physical_device()` @0xce6ed880 (RX tune, LUT, WWAN init) |
| **QET6200** | Envelope Tracker / PA power mgmt — **TX**, no RX | str 24642 (`QET6200 cal … VPA reading`) |

**FACT.** El chip relevante para IQ capture RX en LTE es **SDR735** (familia Qualcomm SDR7xx, RFIC WWAN).

### 5.2 Acceso (bus RFFE/SPMI) — FACT
- **Bus:** RFFE/SPMI. Cada device físico se direcciona por **(bus, USID)**:
  `rfdevice_phy_dev_get_bus_idx(): No combination of bus = %d, usid = %d` (str 25121). **FACT.**
- **Tabla SID:** `rfc_get_used_sid_table()` (@0xce6d49d0) mapea `physical device index → bus → SID`, con
  **shadow registers por bus** (`RFFE_SHADOW_REG_MAX_BUS`). **FACT.**
- **Writes agrupados en rf_scripts:** `rffe_finalize_rf_script(): Unsupported execution type` (str 24648)
  — inmediatos (`imm_write_script_ptr`, via sleep_manager @0xce6dd840) o programados (RFFE scheduler
  @0xce... str 26096). **FACT.**
- **Registros clave para RX (INFERENCE):** el SDR735 se controla por **scripts RFFE** cuyos contenidos
  (direcciones de registro, valores) están en las **tablas RFC/NV**, no como constantes en el código
  disassemblado. Los registros concretos (LNA gain, mixer/LO tune, PLL) son **UNKNOWN estático** — viven
  en el blob RFC (RF Card) que no está descomprimido. `sdr735_common_class()` hace `get_reg_dump_addr`
  (8-bit y 16-bit) → **el mapa de registros existe pero se resuelve en runtime/RFC.** **INFERENCE/UNKNOWN.**

### 5.3 Register dump en vivo (accionable) — FACT
`sdr735_common_class()` tiene **register dump** (`get_reg_dump_addr_8bits`, `reg_dump_addr_block_list`).
→ En vivo hay un mecanismo FTM para **volcar los registros del SDR735** (bloques 8-bit y 16-bit), lo que
permite leer el estado real del transceiver (LNA, PLL lock, gain) durante la captura. **FACT (existe) /
UNKNOWN (comando FTM exacto no aislado).**

---

## 6. AGC RX (leer/fijar gain para IQ con gain fijo) + comandos FTM RX_AGC

### 6.1 La SM de AGC (misión) — FACT
- `lte_LL1_rxagc_db_ptr->lna_mode_switch_db.applied_lna_power_config_mode` ∈ {`MISSION_MODE`,
  `LOW_POWER_MODE`} (str 4010/4015). En misión, la AGC **mueve el LNA state automáticamente** según nivel.
- `RFFW_LTE_MAX_NUM_RX_GAIN_RANGES` (str 3947/3961) = número de gain ranges/LNA states.
- `lte_LL1_rxagc_lna_lut` (`LNA_LUT_FULL`, str 3966) = LUT de LNA states.
**FACT.** Para IQ útil con gain conocido hay que **sacar la AGC de automático** y **fijar el LNA state**.

### 6.2 TLVs FTM para RX_MEASURE / IQ_CAPTURE (control de gain) — FACT
Tabla @**0xc906c720** (seg23). Los campos de gain/AGC (FACT):

| field_id | nombre | uso para IQ con gain fijo |
|---------|--------|---------------------------|
| 3 | **EXPECTED_AGC** | AGC esperada (setpoint) |
| 4 | **RX_AGC** | **valor de AGC (leer el gain aplicado / fijarlo)** |
| 5 | **LNA_GS** | **LNA Gain State (fijar el state concreto)** |
| 17 | **OVERRIDE_LNA** | **override del LNA (forzar state, saca la AGC de auto)** ← CLAVE |
| 18 | **RX_GAIN_CTL_TYPE** | **tipo de control de gain (auto/manual/fixed)** ← CLAVE |
| 13 | FETCH_IQ | dispara/recupera la captura |
| 14 | NUM_OF_SAMPLES | número de muestras |
| 15 | IQ_DATA_FORMAT | formato (8/16-bit, ver §2.7) |
| 16 | SAMP_FREQ | freq de muestreo |
| 39 | IQ_CAPTURE_TYPE | tipo de captura |
| 1 | RX_CARRIER | carrier RX |
| 2 | RFM_DEVICE | device/path |
| 27/28/29 | RX_AGC_MIN/MAX/STDDEV | estadística de AGC (respuesta) |

**FACT (tabla verificada).** Para **gain fijo**: setear **RX_GAIN_CTL_TYPE (18)** = manual/fixed,
**OVERRIDE_LNA (17)** + **LNA_GS (5)** al state deseado, y leer **RX_AGC (4)** para conocer el gain
efectivo. La respuesta REPACK incluye RX_AGC_MIN/MAX/STDDEV (27-29).

### 6.3 Cómo leer el gain aplicado (para interpretar la IQ) — INFERENCE con base FACT
1. `RX_AGC` (field 4) en la respuesta = nivel de AGC medido. **FACT (campo).**
2. `LNA_GS` (field 5) = el LNA state activo → con su offset de cal (§4.1.2) das el ΔdB. **FACT.**
3. Gain total = RX_cal_offset(path) + LNA_offset(state) + freq_comp(EARFCN). **INFERENCE (fórmula
   estándar Qualcomm, componentes todos verificados como existentes).**

### 6.4 RX_AGC comando FTM — nota honesta
No hay un string "FTM RX_AGC command" aislado; **RX_AGC es un field_id (4) dentro de RX_MEASURE/
IQ_CAPTURE**, no un comando separado. El comando es **RX_MEASURE / IQ_CAPTURE** (framework RF-TEST,
sub_command UNKNOWN estático, candidatos {1,2,3} en iq_sequence.md), y RX_AGC se lee/fija por TLV. **FACT.**

---

## 7. CÓMO ACTIVAR/CONFIGURAR UN RX CARRIER PARA CAPTURA (accionable)

Combinando este mapa con enter_mode_path.md e iq_sequence.md:

```
Prerrequisitos HW (P1 de enter_mode_path.md):
  · FTM set RF/CAL mode  → arranca rfm_init (evita RFM_INIT WAS NEVER CALLED @0xce6de1b0)
                           + MCPM ON (evita MCPM not turned ON yet @0xce748848)
  · NV/RFC cargada       → RX cal offset + LNA offsets presentes (§4)

1) TECH_ENTER (LTE)                [FTM-RFDEBUG sub_command=13 (FACT), TECH field2 ∈ {1,4,5,0x27}]

2) RADIO_CONFIG / RX_TUNE          [FTM-RF-TEST, dispatch @0xd8182fec, sub<=0x14]
   TLVs (tabla @0xc906c630):
     RX_CARRIER(1)=0, RFM_DEVICE(3)=<path>, BAND(5)=<b>, CHANNEL(6)=<EARFCN>,
     BANDWIDTH(7)=<BW>, [CENTER_FREQ(12) opcional]
   ⇒ dispara ftm_lte_tune → rflte_mc_carrier_activate (@0xce6e72c0)
        → RxLM alloc+ChainActivate → rflm_dtr_rx_activate_chain (@0xce5f4ccc)
     y rflte_ftm_mc_wakeup (@0xce6e08a8) → wakeup_devices_rx (@0xce6dd840): LNA/ASM/SDR735 ON

3) (gain fijo) por TLV en el comando de captura:
     RX_GAIN_CTL_TYPE(18)=manual/fixed, OVERRIDE_LNA(17)=1, LNA_GS(5)=<state>
   ⇒ saca la AGC de auto (evita que el LNA se mueva durante la captura)

4) IQ_CAPTURE / RX_MEASURE         [FTM-RF-TEST, sub_command candidatos {1,2,3}]
   TLVs (tabla @0xc906c720):
     NUM_OF_SAMPLES(14)=<N>, SAMP_FREQ(16)=<fs>, IQ_DATA_FORMAT(15)=<0/1>,
     FETCH_IQ(13)=1, [IQ_CAPTURE_TYPE(39)]
   ⇒ rflte_ftm_iq_capture_prop_action_* (@0xce6ded20) escribe buffer 8/16-bit
   Respuesta REPACK: [ 0x%8x ][ %4d ] = puntero DDR/memshare + tamaño (NO inline)
     + RX_AGC(4)/LNA_GS(5) para el gain efectivo

5) Leer las muestras por memshare/DDR en el puntero devuelto.
   Convertir a nivel abs: gain = RX_cal_offset(path) + LNA_offset(LNA_GS) + freq_comp(EARFCN)
```

**Nota (de enter_mode_path.md):** el gate `@0xca7897b0[LTE]=1` (que destraba RX_MEASURE/IQ_CAPTURE del
0x14) lo abre el **ACTIVATE del path FTM** (paso 2) cuando `rflte_mc_carrier_activate`/`rflte_ftm_mc_wakeup`
completan — no un `enter_mode_cnf`. Verificar en vivo `memb(0xca7897b0 + 1*8)==1` tras el paso 2.

---

## 8. FACT / INFERENCE / UNKNOWN (con VAs)

**FACT (strings/instrucciones verificados):**
- Chip transceiver = **SDR735** (`sdr735_common_class` @0xce6fae60); mmW SMR526v2 @0xce6ed880; ET QET6200.
- RFFE/SPMI: acceso por (bus,USID) `rfdevice_phy_dev_get_bus_idx` str25121; `rfc_get_used_sid_table`
  @0xce6d49d0; `rffe_finalize_rf_script` str24648; RFFE scheduler str26096.
- LNA/RxAGC RFFE table: `rf_rffe_iu_common_prepare_rxagc_table` @0xcea8c0f8; eLNA @0xcea8d770.
- ASM/LNA impact: `rflte_mc_rx_config_asm_device` @0xce6e3320; `rflte_mc_rx_get_lna_impact_params` @0xce6e3228.
- DTR-RX: `rflm_dtr_rx_activate_chain` @0xce5f4ccc; `rflm_dtr_rx_get_group_settings` @0xce5f4d15; NB masks str6150.
- RxLM: alloc @0xce6841c8/@0xce604f28; Chain Config/Activate str3820-3825; buf_idx invalid str3937.
- RxFE/RXAGC LL1: `lte_LL1_rxagc_db_ptr` str4010; `RFFW_LTE_MAX_NUM_RX_GAIN_RANGES` str3947; LNA LUT str3966.
- Sample capture: async db @0xce5aa1a2; DL samp rec @0xce5b5d0e; FTM IQ @0xce6ded20/@0xce6e0c28;
  buffer no-inline str20790/20791.
- Carrier activate: `rflte_mc_carrier_activate` @0xce6e72c0; `rflte_ftm_mc_wakeup` @0xce6e08a8;
  `ftm_lte_tune` @0xce6dfee8; wakeup devices rx @0xce6dd840; generate_config_tables @0xce6e3108.
- Cal RX: `RFNV_..._RX_CAL_OFFSET` one-per-path @0xce6d4db0; `rflte_nv_parse_rx_cal_data` @0xce6eba58;
  `rflte_nv_process_rx_static_data` @0xce6eb430; `rflte_dm_rxagc_config_update_switchpoints_and_lna_offsets_in_dm`
  @0xce6f0960; `num_gain_states <= RFNV_MAX_STATIC_LNA_GAIN_STATES` @0xce6f6298.
- AGC TLVs (RX_MEASURE/IQ_CAPTURE @0xc906c720): RX_AGC=4, LNA_GS=5, EXPECTED_AGC=3, OVERRIDE_LNA=17,
  RX_GAIN_CTL_TYPE=18, FETCH_IQ=13, NUM_OF_SAMPLES=14, SAMP_FREQ=16, IQ_DATA_FORMAT=15.
- RADIO_CONFIG TLVs (@0xc906c630): RX_CARRIER=1, RFM_DEVICE=3, BAND=5, CHANNEL=6, BANDWIDTH=7, CENTER_FREQ=12/21.
- RF-TEST dispatch @0xd8182fec/0xd8183048: gate sub<=0x14, stride 0x11ba0, ctx base 0xf808d780,
  offsets 0x3f0/0x3f4/0x3f8/0x3fc.

**INFERENCE:**
- El ADC RX es interno del SDR735 (no hay clase ADC RX propia); el FW ve el stream post-ADC en DTR-RX/RxLM.
- Orden de carrier-apply: RADIO_CONFIG → carrier_activate → RxLM chain → DTR activate → ftm_mc_wakeup (device RX ON).
- Fórmula de gain absoluto = RX_cal_offset(path) + LNA_offset(state) + freq_comp(EARFCN).
- Para gain fijo: RX_GAIN_CTL_TYPE=manual + OVERRIDE_LNA + LNA_GS.
- El mixer/LO/PLL son HW interno del SDR735 controlado por scripts RFFE.

**UNKNOWN (sólo en vivo o con blob ausente):**
- **Cuerpos de las funciones RFLTE/RFLM/RFDEVICE** (código en q6zip no descomprimido; §0). Sólo strings/VAs de rodata.
- **Registros concretos del SDR735** (LNA gain, mixer/LO, PLL): viven en el blob RFC/NV, no en el código disassemblado.
- **sub_command numérico exacto de RADIO_CONFIG/RX_MEASURE/IQ_CAPTURE** en el framework RF-TEST (registro
  por función-categoría; candidatos {1,2,3}, mismo límite que iq_sequence.md).
- **Comando FTM exacto para reg-dump del SDR735** (el mecanismo existe en `sdr735_common_class`, el
  selector DIAG no se aisló).
- **Valores enum exactos** de RX_GAIN_CTL_TYPE (auto/manual/fixed) y de LNA_GS por banda (en RFC/NV).

---

## 9. REPRODUCIR
```bash
# Strings de la cadena RX (seg27_dec, VA base 0xce480000):
python3 - <<'PY'
d=open('/tmp/modemre/seg27_dec.bin','rb').read(); B=0xce480000
for va in (0xce6fae60,0xce6ed880,0xce6d49d0,0xcea8c0f8,0xce6dd840,0xce6e72c0,
           0xce6e08a8,0xce6dfee8,0xce5f4ccc,0xce6841c8,0xce6ded20,0xce6e0c28,
           0xce6eba58,0xce6f0960,0xce6f6298,0xce6d4db0):
    print(hex(va), d[va-B:va-B+80].split(b'\x00')[0].decode('latin1'))
PY
# Confirmar que el código RFLTE NO está en los blobs descomprimidos (0 refs):
python3 /tmp/hexref.py /tmp/modemre/clade_dec_full.bin 0xd8000000 0xce6e72c0   # -> 0 hits
# (decoder immext validado: refs a 0xca733d10 -> 16 hits, a 0xca65b414 -> 37 hits)
# Framework RF-TEST dispatch (presente en clade):
/tmp/modemre/dis.sh 0xd8182fec 0x110
# Tablas de TLV (seg23) ya documentadas en findings/rftest_tlv_*_ids.txt
```
(hexref.py = decoder de constant-extender Hexagon:
 val = ((w>>16&0xFFF)<<20)|((w&0x3FFF)<<6), sólo si (w>>28)==0.)
