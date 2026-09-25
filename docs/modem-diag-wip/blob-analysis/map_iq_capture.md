# FTM RF-TEST — IQ CAPTURE: path completo de punta a punta
Build: **MPSS.HI.4.3.4-00494-MANNAR_GEN_PACK-1.24452.133** (SM6375 / Moto G82 5G)
Base disasm: `/tmp/modemre/clade_dec_full.bin` (VA 0xd8000000, file off 0 = VA 0xd8000000).
Herramienta: `/tmp/modemre/dis.sh <va> <len>` (llvm-objdump hexagon v66) · `rd.py <va> <n>` (rodata por VA).
rodata: b21 base 0xc3553000 · name-tables: b23 base 0xc8b6a000.

Leyenda: **FACT** = instrucción/byte verificado en esta imagen · **INFERENCE** = deducción con base ·
**UNKNOWN** = no aislado estáticamente (vive en RFC/NV o pool dlpager paginado; se da método).

---

## 0. RESUMEN EJECUTIVO — LO QUE PEDISTE, DIRECTO

| # | Pregunta | Respuesta | Confianza |
|---|----------|-----------|-----------|
| 1 | Field-table IQ_CAPTURE @0xc37c0828 | **51 entradas (field_id 0..0x32). Jump-table de handlers; 0xd8189b38 = default/reject.** Tabla completa en §2. | **FACT** |
| 2 | ¿Dos fases (capture vs fetch)? | **SÍ. Tres acciones internas: ACTION_GETCFG, ACTION_ACQUIRE (captura→llena buffer), ACTION_FETCH (lee buffer). FETCH_IQ(13) mapea a ACTION_FETCH.** Un IQ_CAPTURE puede hacer ACQUIRE y FETCH en la misma trama o separados. | **FACT (nombres de acción) / INFERENCE (combinabilidad)** |
| 3 | Dónde/cómo salen las muestras | **Buffer DDR asignado dinámicamente, NO inline. Se devuelve por REPACK como {name, size_bytes, address(0x%8x)} por propiedad. 8-bit o 16-bit por componente, IQ interleaved. AP lo lee por memshare/DDR.** | **FACT (contrato) / INFERENCE (layout exacto)** |
| 4 | SAMP_FREQ (field 16) | **u32; internamente se resuelve a `lte_LL1_ue_wb_samp_rate_e` (log2 del ratio WB). Base LTE = 1.92 MHz; rates: 1.92/3.84/7.68/15.36/23.04/30.72 MHz. Enum exacto SAMP_FREQ→Hz mapeado por `WB_DF_BW_SAMP_RATE_LUT[bandwidth]` (RFC/runtime).** | **FACT (enum+base) / UNKNOWN (mapa TLV→Hz exacto)** |
| 5 | IQ_DATA_FORMAT(15) / IQ_CAPTURE_TYPE(39) | **DATA_FORMAT selecciona 8-bit vs 16-bit buffer (prop_action_8bit/16bit). IQ_CAPTURE_TYPE(39) = u24, store en ctx+0x48; selecciona la FUENTE (IQ_SOURCE: raw/post-decimation).** Enums numéricos: UNKNOWN estático (RFC). | **FACT (mecanismo) / UNKNOWN (enums)** |
| 6 | Handoff memshare | **memshare QMI client del modem (`memshare_qmiclient`, task en b21). El AP (servicio memshare QMI) asigna DDR; el modem escribe las IQ ahí y devuelve address+size en el REPACK DIAG. El AP lee el DDR/smem en esa address. No hay copia inline para capturas grandes.** | **FACT (existe cliente QMI) / INFERENCE (secuencia)** |
| 7 | WAIT_TRIGGER (cmd 9) | **Comando RFTEST separado (fn 0xd8187cc0, 8 fields: SRC_DEVICE/SIG_PATH/ANT_PATH/PLL_ID/NB_ID/LANE_ID/SRC_NB_ID/SRC_LANE_ID). Arma el punto de disparo por (device, nanobit/lane). Sincroniza la ACQUIRE a un trigger externo/PLL.** | **FACT (fields) / INFERENCE (rol)** |

---

## 1. ARQUITECTURA DEL UNPACKER (0xd81893a4 / dispatcher 0xd8189818)

### 1.1 Los dos punteros de registro [FACT]
La master-fn IQ_CAPTURE **0xd81893a4** aloca su contexto y guarda en la struct de registro
(0xd8189380, FACT literal):
```
d8189380: memw(r16+#0x0) = ##0xd81893a4     ; UNPACKER  (parsea la trama TLV → contexto)
d8189388: memw(r16+#0x4) = ##0xd8189d38     ; helper alloc/free del contexto (NO el repack real)
```
> **Nota:** el brief cita `unpacker 0xd81893a4` (FACT confirmado) y `dispatcher 0xd8189818`.
> 0xd8189818 es la **entrada del jump-table de handlers de campo** (§1.2). El REPACK real que
> empaqueta las muestras vive en el pool RFLTE (`rflte_ftm_iq_capture_prop_action_*`, §5), paginado.

### 1.2 El dispatcher por field_id (jump-table @0xc37c0828) [FACT literal]
```
d8189814: immext(#0xc37c0800)
d8189818: r2 = memw(r2<<#0x2 + ##0xc37c0828)   ; slot[field_id] de la jump-table
d818981c: jumpr r2                              ; salta al handler del campo
```
- Cada handler lee la trama TLV, forma un u32 (LE) y lo **guarda en el descriptor / contexto**.
- **Gate primer grupo (field 0..0x19):** `cmp.gtu(field,#0x19)` @0xd81897f0 → si >0x19 va al 2º grupo.
- **Gate segundo grupo (field 0x1a..0x32):** `cmp.gtu(field-1,#0x18)` @0xd818a0cc + tabla @0xc37c0890
  (= 0xc37c0828 + 0x68 = índice 26). El **default reject** es 0xd8189b38 (emite F3 "unknown field").
- **Rango total aceptado: field_id 0..0x32 (0..50).** field_id ≥ 51 → fuera de tabla → se ignora.

### 1.3 El valor en el wire (cómo se escribe) [FACT literal, 0xd81897f4]
```
memb(desc+0) = r20        ; byte0 (LSB)
memb(desc+1) = r20>>8
memb(desc+2) = r20>>16
memb(desc+3) = r20>>24    ; byte3 (MSB)
```
→ **Cada campo acepta hasta u32 LE en el wire.** El "tipo" (8/16/32) determina el *default* leído
del config-struct y la interpretación downstream, pero el encoding TLV es `{u16 field_id, u16 len,
value[len]}` LE estándar Qualcomm. **FACT.**

### 1.4 El log de debug UNPACK (%12s) — tabla de 28 nombres @0xc906c8f8 [FACT]
El UNPACK loguea `[FTM.RFTEST][IQ_CAPTURE][UNPACK]:[%2d][%3d][ %12s ][ %12d ]` (fmt @0xc37c08f4).
El `%12s` NO usa la tabla de field-ids del wire: itera la **estructura interna normalizada de 28
campos** @0xc906c8f8 (loop a 0x1c=28, 0xd8189bbc). Es la vista interna del comando (§4).

---

## 2. FIELD-TABLE IQ_CAPTURE COMPLETA @0xc37c0828 (TODOS los field_ids) [FACT]

51 slots (field_id 0..0x32). Handler `0xd8189b38` = **default/reject** (campo no soportado por
IQ_CAPTURE). Nombre del wire = tabla compartida RX_MEASURE/IQ_CAPTURE @**0xc906c720** (75 nombres,
`rftest_tlv_IQ_CAPTURE_ids.txt`). "cfg_default" = offset en el config-struct (`memw(r25+0)`) de
donde se lee el valor por defecto si el campo NO viene; su ancho de acceso indica el tipo nominal.

| field_id | handler | nombre (wire @0xc906c720) | cfg_default (ancho→tipo) | acción / destino | oblig. |
|---:|---|---|---|---|---|
| 0  0x00 | 0xd8189820 | UNASSIGNED           | — (call 0xd84ab200→+0x20) | reservado | no |
| 1  0x01 | 0xd8189864 | **RX_CARRIER**       | memw@0x38 (32b)  | índice carrier RX | **sí(*)** |
| 2  0x02 | 0xd81898bc | RFM_DEVICE           | memw@0x3c (32b)  | device/path RF | rec. |
| 3  0x03 | 0xd8189874 | EXPECTED_AGC         | memw@0x38 (32b)  | AGC setpoint | no |
| 4  0x04 | 0xd81898ec | **RX_AGC**           | memuh@0x28 (16b) | AGC/gain leído-o-fijado | rec. |
| 5..12 | 0xd8189b38 | LNA_GS/SIG_PATH/RX_MODE/RX_SLOT/NUM_OF_BURST/SENSITIVITY/CTON/PEAK_FREQ | — | **NO soportados por IQ_CAPTURE** (son de RX_MEASURE) | — |
| 13 0x0d | 0xd8189964 | **FETCH_IQ**         | memw@0x64 (32b)  | → ACTION_FETCH (lee buffer) | **sí p/leer** |
| 14 0x0e | 0xd8189994 | **NUM_OF_SAMPLES**   | memub@0x6c (8b def) | nº muestras a capturar | **sí** |
| 15 0x0f | 0xd81899d8 | **IQ_DATA_FORMAT**   | memw@0x68 (32b)  | 8-bit vs 16-bit (§6) | **sí** |
| 16 0x10 | 0xd8189934 | **SAMP_FREQ**        | memw@0x60 (32b)  | sample rate (§5) | **sí** |
| 17 0x11 | 0xd8189aa8 | OVERRIDE_LNA         | memw@0x74 (32b)  | fuerza LNA state (gain fijo) | rec.(gain) |
| 18 0x12 | 0xd8189a08 | RX_GAIN_CTL_TYPE     | memw@0x70 (32b); call 0xd8169618 | tipo de control de gain | no |
| 19..22 | 0xd8189b38 | NO_TONE_SENSITIVITY[..SPUR] | — | **NO soportados** | — |
| 23 0x17 | 0xd8189ad8 | BEAM_ID              | memuh@0x8a (16b) | beam (NR) | no |
| 24 0x18 | 0xd8189b38 | TIME_US              | — | **NO soportado** | — |
| 25 0x19 | 0xd8189b08 | SUB_FRAME_CONFIG     | memub@0x8c (8b)  | config subframe | no |
| 26 0x1a | 0xd818a0e4 | NUM_AVERAGES         | call 0xd84ab1d0; memw(ctx+0x20),memb(ctx+2)=1 | nº promedios | no |
| 27 0x1b | 0xd818a0f4 | RX_AGC_MIN           | call 0xd8169618; memw(ctx+0x24),memb(ctx+3)=1 | — | no |
| 28 0x1c | 0xd818a154 | RX_AGC_MAX           | memw(r29+0x18)←val; memb(r29+0x14)=1 | — | no |
| 29 0x1d | 0xd818a158 | RX_AGC_STDDEV        | (mismo bloque 0x1c) | — | no |
| 30 0x1e | 0xd818a104 | CTON_MIN             | memh(ctx+0x28)=v, memh(ctx+0x2a)=v, memb(ctx+6)=1 | — | no |
| 31 0x1f | 0xd818a210 | CTON_MAX             | **default2 (reject-soft)** | — | no |
| 32 0x20 | 0xd818a178 | CTON_STDDEV          | memw(ctx+0x40)=setbit7; memb(ctx+0xf/0x10/0x11/0x12)=1 | flags | no |
| 33 0x21 | 0xd818a190 | SENSITIVITY_MIN      | (bloque flags) | — | no |
| 34 0x22 | 0xd818a198 | SENSITIVITY_MAX      | (bloque flags) | — | no |
| 35..37 | 0xd818a210 | SENSITIVITY_STDDEV / NO_TONE_SENS_MIN/MAX | default2 | — | no |
| 38 0x26 | 0xd818a1c8 | NO_TONE_SENSITIVITY_STDDEV | memb(ctx+?) | — | no |
| 39 0x27 | 0xd818a1a8 | **IQ_CAPTURE_TYPE**  | u24→**memw(ctx+0x48)** | fuente/tipo captura (§6) | rec. |
| 40 0x28 | 0xd818a1c4 | MEAS_FREQUENCY_ERROR | memb(r29+0x1c ptr)+0 | — | no |
| 41..47 | 0xd818a210 | (varios _MIN/_MAX)   | default2 | — | no |
| 48 0x30 | 0xd818a1d4 | SUB_TECH             | u16→memh(ctx+0x52) | sub-tech | no |
| 49 0x31 | 0xd818a1e4 | RSB_FREQ             | memb(ctx+0x19)=1, memb(ctx+0x1b)=1 | flags RSB | no |
| 50 0x32 | 0xd818a1f0 | RSB_PWR_DBC          | memb(ctx+0x5a)=1; u16→memh(ctx+0x5c) | RSB | no |

(*) RX_CARRIER es el carrier a capturar; requerido salvo default de ctx.

**Comando repro:** `python3 /tmp/modemre/rd.py 0xc37c0828 51` · `/tmp/modemre/dis.sh 0xd8189820 0x900`.

### 2.1 Los 7 conocidos, confirmados con su tipo real
| field_id | nombre | tipo wire | notas |
|---:|---|---|---|
| 1  | RX_CARRIER      | u32 | carrier RX |
| 4  | RX_AGC          | u16 (default memuh) | gain/AGC |
| 13 | FETCH_IQ        | u32 (bool 0/1) | dispara ACTION_FETCH |
| 14 | NUM_OF_SAMPLES  | u32 (default 8b) | nº muestras |
| 15 | IQ_DATA_FORMAT  | u32 (enum) | 8/16-bit |
| 16 | SAMP_FREQ       | u32 | sample rate |
| 39 | IQ_CAPTURE_TYPE | u24→u32 | fuente/tipo |

---

## 3. FETCH_IQ (13) vs CAPTURE — LA SECUENCIA EXACTA [FACT de nombres / INFERENCE de orden]

### 3.1 Las TRES acciones internas [FACT — tabla @0xc906c8f8]
La vista interna normalizada del comando (28 campos, la que loguea el UNPACK) contiene **tres flags
de acción consecutivos**:
```
logidx 7  = ACTION_GETCFG    ; consulta config (rx_path_data por carrier) — NO captura
logidx 8  = ACTION_ACQUIRE   ; CAPTURA: arma el request y llena el buffer de muestras
logidx 9  = ACTION_FETCH     ; LEE: devuelve las muestras del buffer al AP (por address+size)
```
Y los campos de la carga de fetch:
```
logidx 13 = IQ_SOURCE        ; de dónde salen las muestras (raw ADC / post-decimation)
logidx 14 = SAMP_SIZE        ; 8-bit vs 16-bit por componente
logidx 15 = DATA_FORMAT      ; layout I/Q
logidx 16 = SAMP_FREQ        ; sample rate
logidx 17 = MAX_DIAG_SIZE    ; tope de bytes inline en la respuesta DIAG
logidx 18 = SAMP_OFFSET      ; offset (en muestras) dentro del buffer para el fetch ventana
logidx 19 = NUM_SAMP_BYTES   ; cuántos bytes de muestra devolver en este fetch
```
**FACT:** `FETCH_IQ (field_id 13 del wire)` se mapea a **ACTION_FETCH (logidx 9)**.

### 3.2 La secuencia para obtener N muestras [INFERENCE fuerte con base FACT]
Confirmado por: (a) las 3 acciones separadas, (b) la existencia de un **paquete de respuesta
independiente** `ftm_lte_iq_capture_get_samples_rsp_pkt_ptr` (seg27 str 25259), (c) SAMP_OFFSET +
NUM_SAMP_BYTES (fetch ventana → múltiples fetch de un buffer grande).

**Modelo de dos fases (recomendado y consistente con el HW):**
```
FASE A — ACQUIRE (capturar):
  IQ_CAPTURE con { RX_CARRIER, IQ_CAPTURE_TYPE/IQ_SOURCE, NUM_OF_SAMPLES=N, SAMP_FREQ,
                   IQ_DATA_FORMAT(8/16b), FETCH_IQ=0 }
  → rflte_ftm_iq_capture_prop_action_{8,16}bit arma capture_req, alloca buffer DDR,
    ML1 ASYNC_SAMP_CAPTURE llena p_sample_capture_buffer (N muestras).
  Respuesta: status OK (+ tal vez address+size del buffer allocado).

FASE B — FETCH (leer):
  IQ_CAPTURE con { RX_CARRIER, FETCH_IQ=1, SAMP_OFFSET=k, NUM_SAMP_BYTES=M, MAX_DIAG_SIZE=... }
  → ACTION_FETCH: REPACK devuelve las muestras [k, k+M) por {name, size, address(0x%8x)}.
  Repetir con SAMP_OFFSET creciente para drenar todo el buffer (capturas > MAX_DIAG_SIZE).
```
**Alternativa de una sola trama (INFERENCE):** un IQ_CAPTURE con `FETCH_IQ=1` y `NUM_OF_SAMPLES=N`
puede hacer ACQUIRE **y** FETCH en la misma trama si `N*bytes_per_sample <= MAX_DIAG_SIZE`. Para
capturas grandes (→5MiB) hay que separar: ACQUIRE una vez, FETCH por ventanas (SAMP_OFFSET).

---

## 4. LA VISTA INTERNA (28 campos) @0xc906c8f8 [FACT — nombres]

Esto es lo más rico de este pase: el comando IQ_CAPTURE se normaliza a una estructura interna cuya
semántica es explícita (a diferencia de los field-ids crudos del wire).

| logidx | nombre | significado |
|---:|---|---|
| 0  | UNASSIGNED     | — |
| 1  | SUBSCRIBER     | subscription (DSDS) |
| 2  | TECHNOLOGY     | tech (LTE/NR) |
| 3  | TX_CARRIER     | carrier TX (no aplica a RX-IQ) |
| 4  | RX_CARRIER     | carrier RX a capturar |
| 5  | RFM_DEVICE     | device/path RF |
| 6  | SIG_PATH       | signal path (primary/div) |
| 7  | **ACTION_GETCFG**  | consulta config, no captura |
| 8  | **ACTION_ACQUIRE** | **captura (llena buffer)** |
| 9  | **ACTION_FETCH**   | **lee/devuelve muestras** (=FETCH_IQ) |
| 10 | ACTION_EST_SENS| estimar sensibilidad |
| 11 | ACTION_CTON    | medir C/N |
| 12 | ACTION_PEAK_FREQ| medir freq pico |
| 13 | **IQ_SOURCE**  | fuente: raw ADC / post-decimation |
| 14 | **SAMP_SIZE**  | 8-bit / 16-bit por componente |
| 15 | **DATA_FORMAT**| layout I/Q |
| 16 | **SAMP_FREQ**  | sample rate |
| 17 | **MAX_DIAG_SIZE**| tope bytes inline DIAG |
| 18 | **SAMP_OFFSET**| offset (muestras) del fetch |
| 19 | **NUM_SAMP_BYTES**| bytes a devolver |
| 20 | EST_SENS       | resultado sensibilidad |
| 21 | CTON           | resultado C/N |
| 22 | PEAK_FREQ      | resultado freq pico |
| 23 | EXPECTED_AGC   | AGC esperada |
| 24 | LNA_GAIN_STATE | LNA state usado (para gain) |
| 25 | OVERRIDE_LNA_GAIN_STATE | fuerza LNA state |
| 26 | GAIN_CONTRL    | modo control de gain |
| 27 | SUB_TECH       | sub-tech |

**Repro:** `python3 /tmp/modemre/rd.py 0xc906c8f8 28` (leer ptrs) → strings en b21 0xc414xxxx.

> **Nota tablas solapadas [FACT]:** @0xc906c8f8 = @0xc906c8c0 + 0x38 (14 entradas). La tabla
> @0xc906c8c0 es un **superset** con 14 entradas líder (RESERVED1, WAVEFORM_IMMEDIATE_TRIGGER,
> MIMO_ENABLED, IS_RA_TYPE_0, IF_BACKOFF, C1/C2_RATYPE0_START_RB/NRB_ALLOC — campos de waveform/RB
> para TX/wave). El UNPACK log de IQ_CAPTURE itera el **slice de 28** @0xc906c8f8 (0xd8189bbc,
> loop a 0x1c). El 2º grupo de handlers (field 0x1a+) indexa el log con gate `field<=0x1b`
> (0xd818a068) sobre @0xc906c8f8.

---

## 5. SAMP_FREQ (field 16) — sample rate real [FACT enum / UNKNOWN mapa exacto]

### 5.1 Cómo se resuelve internamente [FACT — seg27 asserts]
```
str 4039  ((lte_LL1_ue_wb_samp_rate_e) sample_rate_log2 != LTE_LL1_UE_WB_SAMP_RATE_DUMMY)
str 2923  log2_mstmr_wb_ratio < LTE_LL1_UE_WB_SAMP_MAX_NUM_RATE
str 2790  (uint8)bandwidth < WB_DF_BW_SAMP_RATE_LUT_LEN
str 2927  log2_mstmr_wb_ratio == LTE_LL1_UE_IRAT_SAMP_RATE_1_92   ; 1.92 MHz visible
str 23734 samp_rate_khz %d                                        ; se maneja en kHz
```
- El sample rate es un **enum log2 del ratio WB** (`lte_LL1_ue_wb_samp_rate_e`), NO Hz crudo.
- Base LTE (WB) = **1.92 MHz**. Rates estándar (1.92 × 2^k, y ×1.5, ×2.5): **1.92 / 3.84 / 7.68 /
  15.36 / 23.04 / 30.72 MHz**. El rate nativo se deriva del **bandwidth del carrier** vía
  `WB_DF_BW_SAMP_RATE_LUT[bandwidth]`.

### 5.2 Valores prácticos [INFERENCE con base FACT]
- Para un carrier LTE de **20 MHz**, el rate nativo del RxFE = **30.72 MHz** (ADC/decimado full-BW).
- SAMP_FREQ (TLV) es el rate deseado; el modem lo cuantiza al enum WB más cercano soportado por el BW.
- Rates típicos por BW LTE: 1.4MHz→1.92 · 3MHz→3.84 · 5MHz→7.68 · 10MHz→15.36 · 15MHz→23.04 ·
  20MHz→30.72 (MHz). **INFERENCE (tabla LTE estándar; el LUT exacto vive en RFC).**
- **UNKNOWN estático:** el mapa exacto `SAMP_FREQ(TLV u32) → enum WB` (si el TLV es Hz, kHz, o el
  enum directo). El código lo resuelve por `WB_DF_BW_SAMP_RATE_LUT` (runtime). **Método:** mandar
  SAMP_FREQ=30720000 (Hz) y 30720 (kHz) y ver cuál acepta; o leer el default del config-struct
  (`memw(cfg+0x60)`) en vivo.

---

## 6. IQ_DATA_FORMAT (15) e IQ_CAPTURE_TYPE (39) [FACT mecanismo / UNKNOWN enums]

### 6.1 SAMP_SIZE / DATA_FORMAT → 8-bit vs 16-bit [FACT]
Dos acciones de captura separadas por ancho de muestra (seg27 str 25261/25262):
```
rflte_ftm_iq_capture_prop_action_8bit_iq_buff   ; muestras I8/Q8
rflte_ftm_iq_capture_prop_action_16bit_iq_buff  ; muestras I16/Q16
```
- **IQ_DATA_FORMAT (field 15)** / interno **SAMP_SIZE (logidx 14) + DATA_FORMAT (logidx 15)**
  seleccionan cuál de los dos buffers se usa. **FACT.**
- **Layout (INFERENCE fuerte, convención Qualcomm FTM IQ):** **interleaved IQIQIQ**, **signed
  two's-complement**, **little-endian**. 8-bit: `int8 I, int8 Q, ...`. 16-bit: `int16 I, int16 Q, ...`.
- **Escala:** muestras del ADC/post-decimación; el nivel absoluto (dBm) requiere RX cal offset del
  path + LNA gain offset del state usado (leer RX_AGC/LNA_GAIN_STATE). Ver map_rf_rx.md §4.

### 6.2 IQ_CAPTURE_TYPE (39) / IQ_SOURCE (logidx 13) [FACT handler / UNKNOWN enum]
- Handler 0xd818a1a8 lee **u24** y hace `memw(ctx+0x48)=valor`. **FACT.**
- Semántica: selecciona la **FUENTE** de las muestras — **raw ADC (pre-decimación)** vs **stream
  decimado (post-DTR, a SAMP_FREQ)** vs otros taps del RxFE. Los nombres internos:
  `IQ_SOURCE`. **FACT (existe) / los valores enum concretos = UNKNOWN estático** (definidos en RFC/
  ftm_rf_test_iq_capture.c, no como inmediatos limpios). **Método:** barrer 0,1,2,... y ver el
  `samp_freq`/decimación resultante en el REPACK.

---

## 7. EL BUFFER DE MUESTRAS Y EL HANDOFF MEMSHARE [FACT contrato / INFERENCE secuencia]

### 7.1 Estructura de captura (FACT por asserts, seg27)
```c
struct iq_capture_data {
    void* iq_capture_req_p;                    // == capture_req.p_sample_capture_buffer (assert)
    struct rx_capture_req {
        void*  p_sample_capture_buffer;        // buffer IQ en DDR (NO inline)
        uint32 sample_capture_buffer_size_words;// tamaño en WORDS (!=0)
        void*  p_tti_wp_capture_buffer;        // buffer TTI wrap-point (metadatos timing)
        uint32 tti_wp_capture_buffer_size_words;
        ...
    } capture_req;
    void* tti_wp_capture_req_p;                // == capture_req.p_tti_wp_capture_buffer (assert)
};
struct rx_capture_db {
    uint32 num_samples_to_capture;             // = NUM_OF_SAMPLES (field 14)
    uint32 num_samples_captured;               // <= num_samples_to_capture (assert)
};
```
Asserts que lo fijan (FACT): `p_sample_capture_buffer != NULL`,
`sample_capture_buffer_size_words != 0`, `iq_capture_req_p == p_sample_capture_buffer`
("IQ buffer address mismatch"), `num_samples_captured <= num_samples_to_capture`,
`num_samples>0 && num_samples<=length`.

### 7.2 Fuente real de las muestras (FACT)
```
LTE_LL1_UE_PROC_MODE_ASYNC_SAMP_CAPTURE   (ML1 async capture)
async_samp_capture_db.lmem_requested==TRUE (staging en LMEM del DSP)
DTR-RX: decimate_factor>=1, decimation_ratio==wb_ratio → sample_buf->rx_len muestras
```
El DTR-RX escribe el stream (decimado a SAMP_FREQ) en `sample_buf`; ML1 lo copia/expone al
`p_sample_capture_buffer` (DDR). **FACT (estructura) / cuerpos en pool dlpager (UNKNOWN-body).**

### 7.3 Cómo el modem le dice al AP dónde está el buffer [FACT REPACK / INFERENCE memshare]
El REPACK (fmt @0xc37c0949) emite **por propiedad**:
```
[FTM.RFTEST][IQ_CAPTURE][REPACK]: [%2d][%3d][ %12s ][ %4d ][ 0x%8x ]
                                    idx  ?    name    size    ADDRESS
```
→ el `0x%8x` es la **dirección** del buffer y `%4d` el **tamaño en bytes** de esa propiedad.
El REPACK arma un **array de propiedades** (`Could not allocate %d bytes for payload_size array`,
`num props %d, total size %d`), reordena elementos ("Swapped location of elements") y omite los que
no caben ("NOT PACKED"). **FACT.**

**El handoff memshare (INFERENCE fuerte, consistente con la arquitectura Qualcomm):**
1. El modem corre un **memshare QMI client** (`memshare_qmiclient`, task registrada en b21 @0xc3577c60).
   **FACT (existe la task).**
2. Ese cliente pide al **servicio memshare QMI del AP** una región DDR (ALLOC_GENERIC). El AP
   asigna DDR física (SMEM/carveout), la mapea y devuelve la dirección al modem.
3. El modem escribe las IQ en esa región (`p_sample_capture_buffer` apunta ahí).
4. La respuesta DIAG del IQ_CAPTURE REPACK devuelve **address (0x%8x) + size**. El AP **lee el
   DDR/smem directamente** en esa address (ya la conoce del ALLOC). No hay copia inline para
   capturas grandes.
5. Para el daemon (memshare-daemon.c, client-id 1, 5MiB): el daemon **es** el lado AP del ALLOC —
   registra la región de 5MiB con client-id 1; el modem la usa como `p_sample_capture_buffer`.
   La `address` del REPACK cae dentro de esa región (offset relativo a la base de 5MiB).

> **Confirmación del contrato para el daemon:** el modem NO copia inline; escribe en la región
> memshare que el daemon expone y **notifica al AP via la respuesta DIAG del FETCH** (address+size).
> El daemon debe: (a) mantener viva la región de 5MiB con client-id 1, (b) tras el FETCH, leer las
> muestras en `base_5MiB + (address - region_phys_base)` con `size` bytes. **INFERENCE fuerte.**

### 7.4 Máximo de muestras (relación con 5MiB) [INFERENCE con base FACT]
- Límite duro: `num_samples_captured <= num_samples_to_capture` (NUM_OF_SAMPLES field 14) y el buffer
  DDR (`sample_capture_buffer_size_words`). **FACT.**
- Con región de **5 MiB = 5,242,880 bytes**:
  - **16-bit I/Q** (4 bytes/muestra: I16+Q16): **máx ≈ 1,310,720 muestras** (5MiB/4).
  - **8-bit I/Q** (2 bytes/muestra: I8+Q8): **máx ≈ 2,621,440 muestras** (5MiB/2).
  - Menos el overhead del TTI-wp buffer y headers → dejar margen (~10%). **INFERENCE.**
- A 30.72 Msps, 5MiB de 16-bit IQ ≈ **~42.7 ms** de captura continua; 8-bit ≈ **~85 ms**. **INFERENCE.**
- `MAX_DIAG_SIZE` (logidx 17) limita cuánto sale por FETCH inline; para drenar 5MiB hay que iterar
  FETCH con SAMP_OFFSET. **FACT (campos existen) / INFERENCE (iteración).**

---

## 8. WAIT_TRIGGER (command_id 9) — trigger/timing [FACT fields / INFERENCE rol]

Comando RFTEST separado. Fn **0xd8187cc0**, jump-table @**0xc37c0644** (8 handlers), name-table
@**0xc906cdc0**, fmt `[FTM.RFTEST][WAIT_TRIGGER][UNPACK]` @0xc37c0664.

| field_id | nombre | uso |
|---:|---|---|
| 0 | SRC_DEVICE   | device fuente del trigger |
| 1 | SRC_SIG_PATH | signal path fuente |
| 2 | SRC_ANT_PATH | antenna path fuente |
| 3 | SRC_PLL_ID   | PLL de referencia del trigger |
| 4 | NB_ID        | nanobit id (timing engine) |
| 5 | LANE_ID      | lane (RFFE/CSI) |
| 6 | SRC_NB_ID    | nanobit fuente |
| 7 | SRC_LANE_ID  | lane fuente |

**Rol (INFERENCE):** WAIT_TRIGGER arma un **punto de disparo** ligado a un device/PLL/nanobit-lane.
Se usa para **sincronizar la ACQUIRE** a un evento de timing (edge de PLL, marca de subframe, o
trigger externo) en vez de capturar "ya". Secuencia: `RADIO_CONFIG → WAIT_TRIGGER(src) →
IQ_CAPTURE(ACQUIRE)` → la captura arranca en el trigger. Coherente con `WAVEFORM_IMMEDIATE_TRIGGER`
(name-table @0xc906c8c0 idx1): si se pide immediate, no espera trigger. **INFERENCE (fields=FACT).**

---

## 9. PATH COMPLETO (de punta a punta)

```
[AP/DIAG] SUBSYS_CMD 0x4b/0x0b, ftm id 0x35a, ftm_cmd 0x27(LTE), sub_command RFTEST
          (barrer 0..0x14; usar COMMAND_CAPABILITY→CMD_MASK para el enum de IQ_CAPTURE)
   │
   ▼  registrar DIAG 0xd8150ed8 → dispatch RFTEST 0xd8182fec (gate sub<=0x14, stride 0x11ba0)
[UNPACK] 0xd81893a4  (FACT)
   · itera TLVs {u16 field_id, u16 len, value[len]} LE
   · jump-table @0xc37c0828 (51 slots) → handler por field_id (§2)
   · normaliza a la struct interna de 28 campos (§4): ACTION_GETCFG/ACQUIRE/FETCH, IQ_SOURCE,
     SAMP_SIZE, DATA_FORMAT, SAMP_FREQ, MAX_DIAG_SIZE, SAMP_OFFSET, NUM_SAMP_BYTES
   │  REX dispatch + MSGR (cruce seg 0xd8 → pool RFLTE 0xce4/seg27)
   ▼
[RFLTE FTM] rflte_ftm_iq_capture_prop_action_{8,16}bit  (@0xce6ded20/0xce6ded68)  (FACT strings)
   · get_cfg: rx_path_data_ptr por (rx_path, carrier)  (@0xce6e0c28)
   · arma capture_req: p_sample_capture_buffer (DDR memshare), size_words, num_samples_to_capture
   │  precondición: RxLM chain activada (rflm_dtr_rx_activate_chain, RFLM handle poblado)
   ▼
[RxLM/DTR] decimación por BW (decimate_factor>=1, decimation_ratio==wb_ratio) → SAMP_FREQ (WB log2)
[ML1]      ASYNC_SAMP_CAPTURE (lmem_requested=TRUE) → llena p_sample_capture_buffer con N muestras
   │  (WAIT_TRIGGER opcional sincroniza el arranque)
   ▼
[BUFFER]   DDR memshare: IQ interleaved (I,Q), 8-bit o 16-bit signed LE, N muestras
           límite: num_samples_captured <= NUM_OF_SAMPLES; <=5MiB/bytes_per_sample
   ▼
[REPACK]   FETCH_IQ=1 (ACTION_FETCH) → [ name ][ size_bytes ][ 0x address ] por propiedad
           (fmt @0xc37c0949). Ventaneable por SAMP_OFFSET/NUM_SAMP_BYTES/MAX_DIAG_SIZE.
   ▼
[AP]       lee las muestras en la región memshare (client-id 1, 5MiB) en address+size.
```

---

## 10. FACT / INFERENCE / UNKNOWN — honesto (con VAs)

**FACT (verificado en clade_dec_full.bin / b21 / b23):**
- UNPACKER IQ_CAPTURE = 0xd81893a4; jump-table de campo @0xc37c0828 (0xd8189818 = entrada). VAs §1.
- Field-table completa: **51 slots (field_id 0..0x32)**; default/reject = 0xd8189b38. Gates
  `field>0x19` (0xd81897f0) y `field-1>0x18` con tabla @0xc37c0890 (0xd818a0cc). §2.
- Tipos por cfg-default: RX_AGC=16b(memuh@0x28), NUM_OF_SAMPLES=8b-def(memub@0x6c),
  SAMP_FREQ=32b(@0x60), IQ_DATA_FORMAT=32b(@0x68), FETCH_IQ=32b(@0x64), IQ_CAPTURE_TYPE=u24→ctx+0x48.
- Wire encoding: `{u16 field_id, u16 len, value[len]}` LE; valor hasta u32 (0xd81897f4).
- **Tres acciones internas** GETCFG/ACQUIRE/FETCH + IQ_SOURCE/SAMP_SIZE/DATA_FORMAT/SAMP_FREQ/
  MAX_DIAG_SIZE/SAMP_OFFSET/NUM_SAMP_BYTES (tabla 28 nombres @0xc906c8f8). §3/§4.
- Buffer NO inline: `p_sample_capture_buffer` (DDR) + size_words; asserts de consistencia (seg27). §7.
- 8-bit / 16-bit por prop_action_{8,16}bit (@0xce6ded20/0xce6ded68). Fuente ML1 ASYNC_SAMP_CAPTURE.
- SAMP_FREQ interno = enum WB log2 (`lte_LL1_ue_wb_samp_rate_e`), base 1.92 MHz, LUT por BW.
- REPACK devuelve {name, size, address} por propiedad (fmt @0xc37c0949); array reordenable.
- memshare QMI client existe (`memshare_qmiclient` task @0xc3577c60).
- WAIT_TRIGGER = fn 0xd8187cc0, 8 fields (SRC_DEVICE..SRC_LANE_ID), name-table @0xc906cdc0. §8.
- Response-len DIAG por default = 0xfd0 (4048B) salvo subcmd 0x24 (iq_final_values.md §1.2).

**INFERENCE:**
- Layout muestras: **IQIQ interleaved, signed two's-complement, little-endian** (convención QC FTM).
- Secuencia dos fases ACQUIRE→FETCH(ventanas) para capturas > MAX_DIAG_SIZE; una-trama si N pequeño.
- Handoff: modem escribe en la región memshare del daemon (client-id 1, 5MiB) y notifica address+size
  en el REPACK DIAG del FETCH; el AP lee DDR directo (no copia inline).
- Máx muestras: ~1.31M (16-bit) / ~2.62M (8-bit) para 5MiB, menos overhead.
- SAMP_FREQ para 20MHz LTE = 30.72 MHz nativo; rates por BW = LTE estándar.
- WAIT_TRIGGER sincroniza el arranque de la ACQUIRE a device/PLL/nanobit-lane.

**UNKNOWN (RFC/NV o pool dlpager paginado; con método):**
- sub_command RFTEST de IQ_CAPTURE (enum numérico): registro table-driven en RAM @0xcaad9d00 →
  usar COMMAND_CAPABILITY→CMD_MASK en vivo (iq_final_values.md §5). 
- Enums exactos: IQ_DATA_FORMAT (valores 8/16-bit), IQ_CAPTURE_TYPE/IQ_SOURCE (raw vs post-dec),
  SAMP_FREQ TLV→Hz/kHz/enum (definidos en ftm_rf_test_iq_capture.c / WB_DF_BW_SAMP_RATE_LUT, RFC).
- Cuerpos de `rflte_ftm_iq_capture_prop_action_*` y el REPACK real (pool dlpager, no desensamblado).
- Dirección física exacta de la región memshare y su base (runtime, la fija el ALLOC del AP).
- Valor default del config-struct (`memw(cfg+0x60/0x64/0x68/0x6c)`) = runtime NV.

---

## 11. Reproducir
```
python3 /tmp/modemre/rd.py 0xc37c0828 51       # field-table IQ_CAPTURE (51 slots)
/tmp/modemre/dis.sh 0xd8189814 0x30            # dispatcher jump-table (field_id -> handler)
/tmp/modemre/dis.sh 0xd8189820 0x900           # todos los handlers de campo
/tmp/modemre/dis.sh 0xd81893a4 0x400           # master UNPACK (aloca ctx, itera TLVs, REX dispatch)
python3 /tmp/modemre/rd.py 0xc906c8f8 28       # struct interna 28 campos (ACTION_GETCFG/ACQUIRE/FETCH)
python3 /tmp/modemre/rd.py 0xc906c720 75       # name-table wire compartida RX_MEASURE/IQ_CAPTURE
/tmp/modemre/dis.sh 0xd8187cc0 0x60            # WAIT_TRIGGER
python3 /tmp/modemre/rd.py 0xc906cdc0 8        # WAIT_TRIGGER field names
# strings de contrato (seg27):
#   25259 ftm_lte_iq_capture_get_samples_rsp_pkt_ptr    (fase FETCH = rsp separada)
#   25261/25262 rflte_ftm_iq_capture_prop_action_{8,16}bit_iq_buff
#   36190/36191 p_sample_capture_buffer / iq_capture_req_p == ...  (buffer DDR, no inline)
#   24612/24613 payload_size array / num props, total size  (REPACK multi-prop)
```
