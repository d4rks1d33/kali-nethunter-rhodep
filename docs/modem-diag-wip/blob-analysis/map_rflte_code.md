# MAPA DEL CÓDIGO RFLTE/RFLM — SM6375 (MPSS.HI.4.3.4) — intento de disasm de cuerpos RF

Build: **MPSS.HI.4.3.4-00494-MANNAR_GEN_PACK-1.24452.133** (SM6375 / Moto G82 5G).
Objetivo del pase: **desensamblar los CUERPOS** de rflte_ftm_mc_wakeup, rflte_mc_carrier_activate,
rflte_ftm_iq_capture_prop_action_*, rflm_dtr_rx_activate_chain; mapear el path IQ y el cruce de segmentos.

Leyenda: **FACT** = verificado byte-a-byte (VA citada) · **INFERENCE** = deducción con base dura ·
**UNKNOWN** = no resoluble estáticamente en el material disponible.

---

## 0. RESULTADO CRÍTICO (leer primero): seg27 NO contiene código — es RODATA pura

**El encargo asume que "el código RF LTE SÍ está descomprimido" en `seg27_dec.bin`. Eso es INCORRECTO,
y se probó byte-a-byte en este pase.** `seg27_dec.bin` (VA base 0xce480000, 6.3 MB) es **exclusivamente
rodata**: strings + tablas de datos (QSHRINK/diag descriptors, punteros a format-strings). **No hay ni
una sola instrucción Hexagon ejecutable.** Los VAs "de funciones RF" del `map_rf_rx.md`
(0xce6e72c0, 0xce6e08a8, 0xce6ded20, 0xce5f4ccc, …) son las **direcciones de los STRINGS de nombre de
función** (usados en asserts/logging), **NO** las entradas de código.

### Prueba dura (reproducible)

1. **Los VAs "de función" son strings** (FACT):
   ```
   0xce6e08a8 -> "rflte_ftm_mc_wakeup: invalid ant…"
   0xce6e72c0 -> "rflte_mc_carrier_activate : Inva…"
   0xce6ded20 -> "rflte_ftm_iq_capture_prop_action…"
   0xce5f4ccc -> "rflm_dtr_rx_activate_chain() fai…"
   0xce6fae60 -> "sdr735_common_class(): Failed to…"
   0xce6dd840 -> "[rfdevice_sleep_manager] rfdevic…"
   ```

2. **Detector de código calibrado** (cuenta `dealloc_return`=0x961ec01e y `jumpr r31`=0x529fc000,
   las salidas obligatorias de toda función Hexagon):
   | blob | dealloc_return | jumpr r31 | veredicto |
   |------|---------------:|----------:|-----------|
   | `clade_dec_full.bin` (código FTM/DIAG conocido, 2 MB muestreados) | **2530** | **1287** | CÓDIGO ✓ |
   | `modem.b10` (código plano DIAG) | 2872 | — | CÓDIGO ✓ |
   | `modem.b13` (código plano) | 4891 | — | CÓDIGO ✓ |
   | **`seg27_dec.bin` (6.3 MB completos)** | **0** | **0** | **NO-CÓDIGO** ✗ |

3. **Disasm directo de seg27** (cualquier offset) produce basura: packets con `<unknown>`,
   `immext(#0xd580)` repetidos, sin prólogos (`allocframe`) coherentes ni retornos. El "código" que
   ve `llvm-objdump` en seg27 es el decode accidental de **tablas de 14 bytes** (§4).

4. **Las regiones "binarias" de seg27** (0xce49c000..0xce576000, ~872 KB con baja densidad ASCII) son
   **tablas de descriptores QSHRINK/diag** de registro fijo de 14 bytes, no código (§4).

### ¿Dónde está entonces el código RFLTE/RFLM?

**No está en NINGÚN blob descomprimido disponible.** Barrido exhaustivo (FACT):

| blob buscado | contiene strings "rflte_/rflm_/sdr735"? | contiene el CÓDIGO RF? |
|--------------|-----------------------------------------|------------------------|
| `clade_dec.bin` (54 MB, window CLADE 0xd8000000..0xdb397464) | **0** refs (immext exacto) | **NO** |
| `clade_dec_full.bin` (10 MB) | 0 | NO |
| `clade_all.bin` (0xcc region raw) | 0 | NO |
| flat R-E: b04/b05/b08/b09/b10/b13/b30 | 0 | NO |
| CLADE > 0xdb400000 (extraído en este pase) | vacío (0x55 filler) | NO (window termina) |

- **0 refs por constant-extender** a los VAs de string RF desde los 54 MB de CLADE
  (decoder immext: `imm=((w>>16&0xFFF)<<14)|(w&0x3FFF)`, `ext=imm<<6`; 0 matches).
- Sí hay **70 refs immext** de CLADE hacia el rango de seg27, pero caen en la **zona de TABLAS**
  (0xce49xxxx/0xce4exxxx/0xce4fxxxx), confirmando que **seg27 se consume como DATOS**, no se ejecuta.
- El window CLADE efectivamente **termina ~0xdb397464**; por encima es filler.

**Conclusión (FACT):** el código de `rflte_mc_carrier_activate`, `rflte_ftm_mc_wakeup`,
`rflte_ftm_iq_capture_prop_action_*`, `rflm_dtr_rx_activate_chain` y todo RFLTE/RFLM/RFDEVICE **vive en
un pool paginado NO presente en el ELF** — el candidato es el **pool dlpager 0xd4400000..0xd6539000**
(descriptor @0xc8d0a338, ver `map_segments.md §2/§4.2`), cuyo **mapa de páginas no está en el MBN**.
Por eso **es imposible desensamblar los cuerpos RF con el material actual.** Requiere: (a) dump en
vivo de la RAM de código dlpager, o (b) el mapa de páginas dlpager ausente.

> Por tanto, los "disasm-based" pedidos (tareas 1-4) **no se pueden entregar como disasm**; se entrega
> lo máximo verificable: **el contrato de cada función deducido de sus asserts** (nombres de campo de
> struct, punteros, límites — todo FACT a nivel de string), el path IQ completo, y el mecanismo de cruce
> de segmentos. Todo cuerpo se marca **UNKNOWN-body**.

---

## 1. rflte_ftm_mc_wakeup — encender la cadena RX en FTM  (UNKNOWN-body / contrato FACT)

**Entrada de código: UNKNOWN** (pool dlpager). String de nombre @0xce6e08a8.
Asserts/strings que definen su contrato (todos FACT, VA en seg27):

```
0xce6e08a8  rflte_ftm_mc_wakeup: invalid antpath on sub[%d], cc[%d], path[%d]
0xce6e08f0  rflte_ftm_mc_wakeup: invalid sawless path …          (variante SAWless)
```
- Itera sobre `(sub, cc, antpath)` → valida cada antpath por (subscription, component-carrier, path).
- **INFERENCE (fuerte):** por cada path válido llama a la maquinaria de wakeup de devices RX:
  `rfdevice_sleep_manager_wakeup_devices_rx` (string @0xce6dd840 `[rfdevice_sleep_manager]…`), que
  genera un **script RFFE de writes inmediatos** (`imm_write_script_ptr`) para sacar LNA/ASM/SDR735 de
  sleep. Es el "power-on RF" del path FTM/CAL (no pasa por el `enter_mode` del ML1).
- **Estado que setea:** habilita los devices RX del path; deja la cadena lista para tune/captura.
- **Dispara:** script RFFE inmediato de wakeup por device (SDR735 power-on, LNA/eLNA, ASM switch).

**Registros/scripts concretos: UNKNOWN** (los writes RFFE viven en tablas RFC/NV, no en seg27).

---

## 2. rflte_mc_carrier_activate — programar el carrier  (UNKNOWN-body / contrato FACT)

**Entrada de código: UNKNOWN** (pool dlpager). Strings de nombre y asserts (FACT):

```
0xce6e72c0  rflte_mc_carrier_activate : Invalid parameters in X2L case … is dl req has wrong va
0xce6e7370  rflte_mc_carrier_activate_v2: Memory Allocation Failed
0xce6e73b0  get rx_tech_data failed! rx_handle: %d
0xce59c318  carrier_ptr != NULL) failed, carrier %d ptr_addr 0x%08x   ← el carrier_ptr como puntero runtime
```
- **Contrato (INFERENCE fuerte por strings+nombre):** dado `(band, EARFCN/freq, BW, rx_handle/RxLM,
  rfm_device)` — construye los rf_scripts RFFE, configura la RxLM chain
  (`rflte_mc_rx_generate_config_tables`, string @0xce6e3108) y **activa** la cadena RX física del carrier;
  alloca `rx_tech_data` y valida `rx_handle`. Maneja el caso X2L (inter-RAT desde otra tech).

### Confirmación del path al carrier ptr 0xca79c494 (aguas abajo)
- El assert @0xce59c318 (`carrier_ptr != NULL … ptr_addr 0x%08x`) prueba que **carrier_ptr es un puntero
  runtime** que el código imprime como `0x%08x`. En `map_segments.md §5` el global **0xca79c494** está en
  **b24 (.bss, RW-)** = "carrier ptr". → **INFERENCE (fuerte):** `rflte_mc_carrier_activate` (o su
  `_v2`) es quien **rellena/escribe** ese slot de carrier ptr en la RAM de estado RF (0xca79xxxx, b24) al
  activar el carrier. **El path confirmado a nivel de estructura**: activate → alloca/asigna carrier_ptr
  → lo guarda en el array de carriers en b24 (uno de cuyos slots es 0xca79c494). **Cuerpo UNKNOWN**, por
  lo que la escritura literal a 0xca79c494 no se ve como instrucción; se infiere por (a) el string de
  assert que trata carrier_ptr como puntero y (b) la ubicación .bss del global.

**band/EARFCN→freq:** derivado por el ML1 (`lte_ml1_common_band_get_band_from_dl_earfcn_with_inst`,
string en enter_mode_path.md) y en FTM por `ftm_lte_tune` @string 0xce6dfee8
(`ftm_lte_tune: current_dl_to_ul_map_ptr is NULL!`). **FACT (existencia) / UNKNOWN (aritmética exacta).**

---

## 3. rflte_ftm_iq_capture_prop_action_* — el corazón del IQ capture  (UNKNOWN-body / contrato FACT MUY rico)

**Entradas de código: UNKNOWN** (pool dlpager). Pero los asserts revelan la **estructura de captura
completa** (todos FACT, VA en seg27):

### 3.1 Las acciones de captura (8-bit / 16-bit)
```
0xce6ded20  rflte_ftm_iq_capture_prop_action_8bit_iq_buff allocation failure
0xce6ded68  rflte_ftm_iq_capture_prop_action_16bit_iq_buff allocation failure
0xce6e0c28  rflte_ftm_iq_capture_prop_action_get_cfg: rx_path_data_ptr is NULL for rx_path:%d carrier:%d
0xce6deca0  ftm_lte_iq_capture_get_samples_rsp_pkt_ptr allocation failure
0xce6deca8  iq_capture_get_samples_rsp_pkt_ptr allocation failure
```
→ **Formato IQ: 8-bit o 16-bit por componente** (dos buffers/paths separados). Se elige por TLV
`IQ_DATA_FORMAT` (field 15, ver map_rf_rx.md §6.2). El buffer se **alloca dinámicamente** (de ahí los
"allocation failure"). `get_cfg` obtiene el `rx_path_data_ptr` por `(rx_path, carrier)`.

### 3.2 La estructura de request de captura (campos de struct — FACT por assert)
```
0xce779f8f  iq_capture_data_ptr->capture_req.p_sample_capture_buffer failed: IQ Capture(%d) Sample capture buffer is NULL
0xce77a007  iq_capture_data_ptr->iq_capture_req_p == iq_capture_data_ptr->capture_req.p_sample_capture_buffer failed: IQ buffer address mismatch
0xce77a0af  iq_capture_data_ptr->capture_req.p_tti_wp_capture_buffer failed: IQ Capture(%d) TTI capture …
0xce77a127  iq_capture_data_ptr->tti_wp_capture_req_p == …->capture_req.p_tti_wp_capture_buffer failed
0xce6942d2  sample_capture_buffer_size_words != 0) failed
0xce694321  sample_capture_buffer != NULL) failed
0xce69431f  p_sample_capture_buffer != NULL) failed
0xce694459  num_samples_captured <= rx_capture_db_ptr->num_samples_to_capture …
0xce69062f  num_samples_to_alloc != 0) failed
0xce73eeea  num_samples>0 && num_samples<=length failed
```
**Estructura reconstruida (FACT-de-nombres):**
```c
struct iq_capture_data {
    iq_capture_req_p;                 // == capture_req.p_sample_capture_buffer (debe coincidir)
    struct {
        void*  p_sample_capture_buffer;      // puntero al buffer IQ (DDR/memshare) — NO inline
        uint32 sample_capture_buffer_size_words;  // tamaño en WORDS (!=0)
        void*  p_tti_wp_capture_buffer;      // buffer separado para TTI wrap-point capture
        ...
    } capture_req;
    tti_wp_capture_req_p;             // == capture_req.p_tti_wp_capture_buffer
};
struct rx_capture_db {
    num_samples_to_capture;           // límite superior
    num_samples_captured;             // <= num_samples_to_capture
};
```

### 3.3 Dónde y cómo salen las muestras (FACT)
- **El buffer NO viaja inline.** `p_sample_capture_buffer` es un **puntero a DDR/memshare** + tamaño en
  **words** (`sample_capture_buffer_size_words`). Se devuelve puntero+tamaño en la respuesta (consistente
  con el REPACK `[ 0x%8x ][ %4d ]` de iq_sequence.md). **FACT.**
- **Doble buffer:** además del sample buffer hay un **TTI wrap-point capture buffer**
  (`p_tti_wp_capture_buffer`) — segundo stream de metadatos de timing por TTI. **FACT.**
- **Máximo de muestras:** limitado por `num_samples_to_capture` (campo de `rx_capture_db`); el valor
  concreto se fija por TLV `NUM_OF_SAMPLES` (field 14). Valor máximo numérico: **UNKNOWN** (en runtime/RFC).
- **Consistencia forzada:** `iq_capture_req_p == capture_req.p_sample_capture_buffer` (assert) → el
  request y el buffer del db apuntan al MISMO bloque; si no, "IQ buffer address mismatch". **FACT.**

### 3.4 ML1 async sample capture (la fuente real de las muestras) — FACT
```
0xce5aa1b5  async_samp_capture_db.lmem_requested == TRUE) failed
0xce5aa554  async_samp_capture_db.lmem_requested != TRUE) failed
0xce5b0ed4  LTE_LL1_UE_PROC_MODE_ASYNC_SAMP_CAPTURE
0xce5aa253  async_samp_capture_db.po_diff_in_ppp) > LTE_LL1_ODRX_PPP_MIN_SPACE_BETWEEN…
```
→ La captura real la hace el **ML1 en modo `ASYNC_SAMP_CAPTURE`**, con el flag `lmem_requested`
(pide **LMEM** = local memory del DSP como staging) y timing por PPP/ODRX. La cadena FTM
(`rflte_ftm_iq_capture_prop_action_*`) es el **wrapper de test** que arma el request y copia/expone el
buffer a DDR/memshare. **FACT (existencia) / cuerpo UNKNOWN.**

---

## 4. rflm_dtr_rx_activate_chain — cadena RxLM/DTR  (UNKNOWN-body / contrato FACT)

**Entrada de código: UNKNOWN** (pool dlpager). Strings/asserts (FACT):
```
0xce5f4ccc  rflm_dtr_rx_activate_chain() fail…    (nombre)
0xc3f3ada0  (b21) rflm_dtr_rx_activate_chain: RFLM Handle = %d not populated   ← exige RFLM handle poblado
0xce5a8a35  rflm_dtr_rx_get_settings_type_ag_ptr for buf_id:%d
0xce5a8ae8  rflm_dtr_rx_get_lpm_settings_type_ag_ptr for buf_id:%d   (LPM = low power mode)
0xce5ac85d  rflm_dtr_rx_get_lpm_settings_type_ag_ptr for rxlm_buf_idx:%d
0xce5ac8fa  rflm_dtr_rx_get_settings_type_ag_ptr for rxlm_buf_idx:%d
```
- **Precondición dura:** exige un **RFLM Handle poblado** (`not populated`) → la RxLM chain debe estar
  allocada+configurada ANTES de activar el DTR. **FACT.**
- **Settings por `rxlm_buf_idx`/`buf_id`/`handle_id`:** los ptrs de settings se leen por índice de buffer
  RxLM (variante normal y `_lpm_` low-power). **FACT.**

### Decimación / sample rate / dónde salen las IQ (FACT por asserts)
```
0xce68c507  decimation_ratio) == wb_ratio) failed
0xce6ff310  decimation factor = 0x%x
0xce700a24  decimate_factor >= 1) failed
0xce6ff214  decimate_in_len <= sample_buf->rx_len) failed        ← sample_buf->rx_len = long del buffer RX
0xce6ff33e  decimation_factor >= in_len) failed
0xce5ac371  sample_rate_change_numr) == (int32)(vsrc_to_mstmr_…)
0xce5ac29c  sample_rate_log2 != LTE_LL1_UE_WB_SAMP_RATE_DUMMY) failed
0xce59ac91  LTE_LL1_UE_WB_SAMP_MAX_NUM_RATE          ← enum de sample rates WB
0xce59ad81  LTE_LL1_UE_IRAT_SAMP_RATE_1_92           ← 1.92 MHz (rate IRAT conocido)
0xce6c56a5  samp_rate_khz %d                         ← el rate se maneja en kHz
```
- **Decimación:** `decimation_ratio == wb_ratio`, `decimate_factor >= 1`; el factor se aplica al stream
  post-ADC. La salida decimada va a `sample_buf->rx_len` muestras. **FACT.**
- **Sample rate:** enum `LTE_LL1_UE_WB_SAMP_*` (log2), con `SAMP_RATE_1_92` (1.92 MHz) visible; el rate
  efectivo se maneja en kHz (`samp_rate_khz`). El WB ratio deriva del BW del carrier. **FACT.**
- **Dónde salen las IQ:** DTR-RX escribe el stream decimado en `sample_buf` (`rx_len` muestras); ese
  buffer alimenta la ML1 async capture (§3.4), que lo expone a DDR/memshare vía FTM. **FACT/INFERENCE.**

**Registros DTR concretos: UNKNOWN** (tablas de coeficientes `dtr_rxfe_wb_dp_firsb_coeff_c` viven en
RFC/NV, no como instrucciones en seg27).

---

## 5. Cruce de segmentos 0xd8 (FTM/CLADE) ↔ 0xce4/pool-RF (RFLTE)

**Mecanismo: MSGR (message router) + REX dispatch con param-structs allocados — NO call directo.** (FACT)

### 5.1 Evidencia MSGR en el lado RF (seg27, FACT)
```
0xce59a89c  msgr_send_return == E_SUCCESS) failed
0xce5b1ee3  msgr_receive_return == E_SUCCESS) failed
0xce5940fc  msgr_jump_table[tech].max_modules * …          ← tabla de salto MSGR por tech
0xce594598  msgr_jump_table[tech].max_modules failed
0xce5d591a  MSGR API: expected 0x%08x, got 0x%08x          ← IDs de mensaje MSGR
0xce5cd152  MSGR_ID_VAL((int)LTE_LL1_DL_REQ_AP_CSF_SCHED)) …
```
→ El RF LTE ML1 se comunica por **MSGR**: hay un `msgr_jump_table[tech]` (indexado por tecnología) con
`max_modules`; los mensajes llevan un **MSGR_ID** (`MSGR_ID_VAL`). `msgr_send`/`msgr_receive` cruzan
tareas/segmentos. **FACT.**

### 5.2 El puente FTM → RF LTE (los entry points FTM, seg27, FACT)
Familia `ftm_lte_*` (strings; cuerpos UNKNOWN en pool RF):
```
0xce6e0025  ftm_lte_rex_dispatch: FTM_LTE_DISABLE_SCELL not supported   ← DISPATCHER REX del FTM-LTE
0xce6dfee8  ftm_lte_tune: current_dl_to_ul_map_ptr is NULL!
0xce6e0188  ftm_lte_rflm_lte_ard_cmd: Input parameters are NULL !!
0xce6e0e20  ftm_lte_populate_config_data
0xce6dfc82  ftm_lte_common_db_ptr != NULL failed
0xce6dfdfa  ftm_lte_db[sub_id] != NULL failed
0xce6e0190  rflm_lte_ard_cmd: Input parameters are NULL !!
0xce6e01c8  rflm_lte_ard_cmd: ftm_lte_rx_toggle_in_ptr memory allocation …
0xce6e0218  rflm_lte_ard_cmd: ftm_rx_toggle_return_ptr memory allocation …
```
**Path del cruce (FACT-de-estructura / INFERENCE-de-flujo):**
```
[seg 0xd8, CÓDIGO CLADE]  FTM-RF-TEST dispatch @0xd8182fec (gate sub<=0x14, stride 0x11ba0)
   │  (comando RADIO_CONFIG con TLVs BAND/CHANNEL/BW/RFM_DEVICE/RX_CARRIER)
   │  → resuelve tech=LTE → entra al dispatcher de tech LTE
   ▼
[pool RF]  ftm_lte_rex_dispatch  (@string 0xce6e0025)   ← REX dispatch por comando FTM-LTE
   │  · ftm_lte_populate_config_data  (arma el config del carrier desde los TLVs)
   │  · ftm_lte_tune                  (banda/duplex del EARFCN, current_dl_to_ul_map_ptr)
   │  · ftm_lte_rflm_lte_ard_cmd → rflm_lte_ard_cmd   (alloca param-structs: rx_toggle_in_ptr, …)
   ▼
[pool RF]  rflte_mc_carrier_activate  (@string 0xce6e72c0)
   │  → RxLM alloc + Chain Config/Activate → rflm_dtr_rx_activate_chain (@string 0xce5f4ccc)
   │  → escribe carrier_ptr en b24/.bss (uno de cuyos slots = 0xca79c494)   [INFERENCE §2]
   └  rflte_ftm_mc_wakeup → rfdevice_sleep_manager_wakeup_devices_rx (LNA/ASM/SDR735 ON)
```
- **RADIO_CONFIG unpacker 0xd8183f30 (seg 0xd8) → rflte_mc_carrier_activate (pool RF):** el unpacker
  0xd8 **NO llama directo** a 0xce4/RF. Deposita los params del carrier en el **ctx del sub_command**
  (stride 0x11ba0, offsets 0x3f0/0x3f4/0x3f8/0x3fc, ver map_rf_rx.md §3.1) y **encola/despacha por REX**
  hacia `ftm_lte_rex_dispatch`, que aguas abajo llama `ftm_lte_tune`→`rflm_lte_ard_cmd`→
  `rflte_mc_carrier_activate`. El acoplamiento fino entre tareas es por **MSGR**
  (`msgr_send`/`msgr_jump_table[tech]`). **INFERENCE fuerte (cadena de nombres+asserts verificada; el
  `callr` exacto no es visible porque el código RF no está desensamblado).**

**Resumen del cruce (FACT):** los dos segmentos NO se llaman por `call` absoluto entre 0xd8 y 0xce4/pool.
Cruzan por (1) **depósito de params en ctx compartido** (b24/.bss, 0xca79xxxx) + (2) **REX dispatch**
(`ftm_lte_rex_dispatch`) + (3) **MSGR** (`msgr_send`/`msgr_receive`, `msgr_jump_table[tech]`,
`MSGR_ID_VAL`). El puntero de carrier resultante se publica en b24/.bss (0xca79c494).

---

## 6. Registros del transceiver SDR735 para RX

**UNKNOWN estático.** (FACT de por qué)
- `sdr735_common_class()` (string @0xce6fae60) construye el **power-on script** y hace **register dump**
  (8-bit y 16-bit: strings `…8bits returned FALSE!` @0xce6faec1, `…8bit memory allocation failed!`
  @0xce6faf3f, `…16bits is NULL!` @0xce6fafd0). El **mapa de registros existe** pero:
- Los **valores/direcciones de registro RFFE/SPMI** (LNA on, mixer, ADC enable, PLL/LO tune) **NO están
  en seg27 ni en ningún blob descomprimido**: viven en el **blob RFC (RF Card) / NV**, que no está en
  este material, y se resuelven en runtime. El acceso es por **(bus, USID)** vía RFFE/SPMI
  (`rfdevice_phy_dev_get_bus_idx: No combination of bus=%d, usid=%d`, y `rfc_get_used_sid_table` con
  `RFFE_SHADOW_REG_MAX_BUS`), pero los **números de registro concretos: UNKNOWN**.
- **Único mecanismo accionable para leerlos en vivo:** el **register dump** de `sdr735_common_class`
  (bloques 8/16-bit), disparable por FTM — el selector DIAG exacto no se aisló. **FACT (existe) / UNKNOWN
  (selector + direcciones)**.

---

## 7. PATH IQ CAPTURE COMPLETO (trigger → RxLM → buffer → formato)

Todo FACT a nivel de estructura/string; cuerpos UNKNOWN:
```
TRIGGER
  FTM-RF-TEST IQ_CAPTURE (seg 0xd8, sub_command RF-TEST candidatos {1,2,3})
    TLVs: NUM_OF_SAMPLES(14), SAMP_FREQ(16), IQ_DATA_FORMAT(15)=8/16-bit, FETCH_IQ(13)
        │  REX dispatch + MSGR
        ▼
  ftm_lte_rex_dispatch → rflte_ftm_iq_capture_prop_action_{8,16}bit  (@0xce6ded20/0xce6ded68)
        │  get_cfg: rx_path_data_ptr por (rx_path, carrier)  (@0xce6e0c28)
        ▼
RxLM / DTR (la fuente digital)
  precondición: RxLM chain allocada+activada (rflm_dtr_rx_activate_chain, RFLM Handle poblado)
  DTR-RX: decimación (decimate_factor>=1, decimation_ratio==wb_ratio), sample_rate WB (kHz, log2)
  ML1 async capture: LTE_LL1_UE_PROC_MODE_ASYNC_SAMP_CAPTURE, async_samp_capture_db.lmem_requested=TRUE
        │  DTR escribe sample_buf (rx_len muestras) → staging en LMEM
        ▼
BUFFER (salida)
  iq_capture_data.capture_req.p_sample_capture_buffer  = puntero DDR/memshare (NO inline)
  iq_capture_data.capture_req.sample_capture_buffer_size_words = tamaño en WORDS
  (+ p_tti_wp_capture_buffer = stream de metadatos de timing por TTI)
  invariante: iq_capture_req_p == p_sample_capture_buffer  (si no → "IQ buffer address mismatch")
  límite: num_samples_captured <= rx_capture_db.num_samples_to_capture (fijado por TLV 14)
        ▼
FORMATO
  8-bit por componente  → prop_action_8bit_iq_buff   (I8,Q8 intercalado, INFERENCE)
  16-bit por componente → prop_action_16bit_iq_buff  (I16,Q16 intercalado, INFERENCE)
  seleccionado por TLV IQ_DATA_FORMAT(15)
        ▼
RESPUESTA
  REPACK [ 0x%8x ][ %4d ] = puntero + tamaño; se lee por memshare/DDR
  + RX_AGC(4)/LNA_GS(5) para el gain efectivo (ver map_rf_rx.md §6)
```

---

## 8. FACT / INFERENCE / UNKNOWN (con VAs)

**FACT (verificado byte/estructura en este pase):**
- **seg27_dec.bin es RODATA pura, 0 instrucciones** (0 dealloc_return / 0 jumpr r31 en 6.3 MB;
  calibrado contra clade/b10/b13 que sí son código). Los VAs "de función" RF son STRINGS.
- El **código RFLTE/RFLM NO está en ningún blob descomprimido** (clade 54 MB: 0 refs immext exactas a los
  string-VAs RF; flat b04..b30: 0 strings RF; CLADE>0xdb400000: vacío).
- Regiones binarias de seg27 (0xce49c000..0xce576000) = **tablas QSHRINK/diag de 14 bytes**
  {ptr-fmt-string en b21 0xc356xxxx, id/hash, high-half 0xcf97} (§4 desglosado abajo).
- Estructura IQ capture (nombres de campo por assert): `iq_capture_data.capture_req.
  {p_sample_capture_buffer, sample_capture_buffer_size_words, p_tti_wp_capture_buffer}`, invariante
  `iq_capture_req_p == p_sample_capture_buffer`, límite `num_samples_captured <= num_samples_to_capture`.
  VAs: 0xce779f8f/0xce77a007/0xce77a0af/0xce6942d2/0xce694459.
- Formato IQ 8-bit o 16-bit (0xce6ded20/0xce6ded68); get_cfg por (rx_path,carrier) 0xce6e0c28.
- ML1 async capture: `LTE_LL1_UE_PROC_MODE_ASYNC_SAMP_CAPTURE` 0xce5b0ed4, `async_samp_capture_db.
  lmem_requested` 0xce5aa1b5.
- DTR: `decimate_factor>=1` 0xce700a24, `decimation_ratio==wb_ratio` 0xce68c507, `samp_rate_khz` 0xce6c56a5,
  `SAMP_RATE_1_92` 0xce59ad81, RFLM Handle poblado 0xc3f3ada0(b21).
- Cruce por MSGR/REX: `msgr_send`/`msgr_receive` 0xce59a89c/0xce5b1ee3, `msgr_jump_table[tech]` 0xce5940fc,
  `ftm_lte_rex_dispatch` 0xce6e0025, `rflm_lte_ard_cmd` 0xce6e0190, `ftm_lte_tune` 0xce6dfee8.
- carrier_ptr como puntero runtime (`ptr_addr 0x%08x`) 0xce59c318; global carrier ptr 0xca79c494 en b24/.bss.
- SDR735 reg-dump 8/16-bit existe (`sdr735_common_class` 0xce6fae60, dumps 0xce6faec1/0xce6fafd0).

**INFERENCE:**
- `rflte_mc_carrier_activate` publica el carrier_ptr en b24/.bss (0xca79xxxx, slot 0xca79c494).
- El unpacker 0xd8183f30 deposita params en ctx (stride 0x11ba0) y despacha por REX/MSGR; no llama RF directo.
- rflte_ftm_mc_wakeup dispara wakeup_devices_rx (script RFFE inmediato) por (sub,cc,antpath).
- IQ 8/16-bit = I/Q intercalado; el buffer real lo produce ML1 async_samp_capture en LMEM y FTM lo copia a DDR.
- El pool dlpager 0xd4400000..0xd6539000 es el candidato donde vive el código RFLTE/RFLM.

**UNKNOWN (no resoluble con el material actual):**
- **Cuerpos desensamblados de rflte_mc_carrier_activate / rflte_ftm_mc_wakeup /
  rflte_ftm_iq_capture_prop_action_* / rflm_dtr_rx_activate_chain** — el código no está en ningún blob;
  vive en el pool dlpager (mapa de páginas ausente del ELF). Requiere dump en vivo.
- Registros concretos RFFE/SPMI del SDR735 para RX (LNA/mixer/ADC/PLL) — en RFC/NV, no en el material.
- sub_command numérico exacto de RADIO_CONFIG/IQ_CAPTURE (candidatos {1,2,3}); MSGR_ID exacto LTE.
- Máximo numérico de num_samples_to_capture; layout exacto de intercalado I/Q; selector DIAG del reg-dump.

---

## 9. REPRODUCIR

```bash
cd /tmp/modemre
# (1) Probar que seg27 NO es código (0 salidas de función):
python3 - <<'PY'
import struct,collections
for f in ('seg27_dec.bin','clade_dec_full.bin','modem.b10'):
    d=open(f,'rb').read(); c=collections.Counter()
    for i in range(0,len(d)-4,4):
        w=struct.unpack_from('<I',d,i)[0]
        if w==0x961ec01e: c['dealloc_return']+=1
        if (w & 0xffffe01f)==0x529fc000: c['jumpr_r31']+=1
    print(f, dict(c))
PY
# (2) 0 refs al código RF desde CLADE (54 MB):
python3 - <<'PY'
d=open('clade_dec.bin','rb').read()
for s in (b'rflte_mc_carrier_activate',b'rflm_dtr_rx_activate_chain',b'sdr735_common_class'):
    print(s, d.find(s))   # -> -1 (no está)
PY
# (3) Los VAs "de función" son strings:
python3 -c "d=open('seg27_dec.bin','rb').read();B=0xce480000
for va in (0xce6e72c0,0xce6e08a8,0xce6ded20,0xce5f4ccc):
    print(hex(va), d[va-B:va-B+40].split(b'\x00')[0])"
# (4) El código RF (si se dumpea en vivo el pool dlpager 0xd4400000) se desensambla con:
#   python3 mkelf.py <dump.bin> <va> out.elf ; llvm-objdump-18 -d --triple=hexagon --mcpu=hexagonv66 out.elf
```

### Detalle §4 — estructura de la tabla QSHRINK/diag en seg27 (lo que llvm-objdump confunde con código)
Región 0xce49c000..0xce576000, **registros de 14 bytes (0xE)**, entropía 6.84:
```
  offset  campo
  +0x0   u16  low16 de un ptr a format-string en b21   (high16 fijo = 0xc356 → VA 0xc356_63XX)
  +0x2   0xc356  (high16 del ptr a b21 rodata)
  +0x4   u32  = 0x00000000  (padding / high de line-num)
  +0x8   u16  id/line-number (0x0c42, 0x0d92, … monótono)
  +0xA   u16  hash/file-id  (0x59f6, 0x5a24, … monótono)
  +0xC   0xcf97  (high16 de un segundo puntero 0xcf97_xxxx)
```
Es la **tabla de descriptores de mensajes comprimidos (QSHRINK)**: mapea id→format-string. **NO es código.**
```
Ejemplo (0xce49c000): 53 63 | 56 c3 | 00 00 00 00 | 42 0c | f6 59 | 97 cf
 → fmt_ptr=0xc3566353, id=0x0c42, hash=0x59f6, ptr2_hi=0xcf97
```
