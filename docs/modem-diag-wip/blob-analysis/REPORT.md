# FTM/DIAG dispatch extraction — Moto G82 5G (SM6375, Hexagon/QDSP6)

Build: **MPSS.HI.4.3.4-00494-MANNAR_GEN_PACK-1.24452.133**
Blob: modem MBN split (modem.mdt + modem.b00..b34). Reensamblado en `/tmp/modemre/modem_full.elf`.

## TL;DR (lo importante)

- **Desensamblador Hexagon SÍ disponible**: `llvm-objdump 18 --triple=hexagon` funciona perfecto.
- La **tabla de dispatch del subsys DIAG_SUBSYS_FTM (0x0B / 11)** está en:
  - **vaddr = 0xc37bd1e8**, file_off = 0x01c7b1e8, segmento **b21** (rodata R--).
  - **75 entries**, registrada vía record en seg23 @ 0xc8dc3b50 con `subsys=0x0B count=75`.
  - Cada entry = `{u16 selector_lo, u16 selector_hi, u32 dispatch_ptr}` (8 bytes).
  - El `dispatch_ptr` es UNIFORME = `0xd8150ed8` → puntero de **runtime** (0xd815xxxx NO está
    mapeado en el image; el código/handlers FTM se relocalizan a esa región en el arranque).
    Por eso los handlers FTM concretos NO son desensamblables desde el blob estático.
- Los **74 selectores FTM reales** están extraídos abajo y en
  `ftm_subsys_0x0B_selector_table.txt`.
- **Validación cruzada con el sondeo EN VIVO**: los selectores que respondieron en vivo
  (7, 13, 16, 20, 27) están TODOS en esta tabla FTM. El **selector 0x07 aparece SOLO en FTM**
  (no en la tabla OEM de Motorola), lo que confirma que ésta es la tabla FTM correcta.
- OJO (trampa): existe una tabla OEM de Motorola (`mot_diag.c`, subsys **0x00**, 26 entries,
  vaddr 0xc41118d8) cuyos command codes (13,16,20,27,...) SOLAPAN con selectores FTM. El sondeo
  en vivo puede haber tocado ambos. Ver sección "MOT_DIAG".

---

## 1) Program headers (mapeo file-offset ↔ vaddr)

Ver `00_program_headers.txt`. Resumen de segmentos clave:

| idx | b_file | p_vaddr | p_paddr | filesz | flags | rol |
|-----|--------|---------|---------|--------|-------|-----|
| 2  | b02 | 0xc0800000 | 0x8b800000 | 0x2930   | R-E | boot |
| 10 | b10 | 0xc0a80000 | 0x8ba80000 | 0x29ab14 | R-E | **code** |
| 13 | b13 | 0xc0d36000 | 0x8bd36000 | 0x31f1b0 | R-E | **code** (dispatchers) |
| 21 | b21 | 0xc3553000 | 0x8e553000 | 0xd315f4 | R-- | **rodata (todas las tablas de dispatch + strings)** |
| 23 | b23 | 0xc8b6a000 | 0x93b6a000 | 0x5cdd97 | RW- | **data (registros de subsys diag)** |
| 26 | b26 | 0xcc000000 | 0x97000000 | 0x244a040| RW- | data (NO es code; es RW) |

Nota: el `p_vaddr` (0xc0800000…) es la dirección virtual de ejecución; `p_paddr`
(0x8b800000…) es la de carga física. Se usa p_vaddr para seguir punteros.

---

## 2) Master table de subsys DIAG (registros)

Formato de cada record (20 bytes) hallado en seg23:
```
[0x00ff0000] [count<<16 | subsysid] [0x000000ff] [0xffffffff] [tbl_ptr]
```
Se enumeraron **55 registros**. Los relevantes:

| subsys | id | count | tbl_ptr | identificación |
|--------|----|-------|---------|----------------|
| **0x0B** | 11 | **75** | **0xc37bd1e8** | **DIAG_SUBSYS_FTM ← objetivo** |
| 0x0A | 10 | 1 | 0xc37bd440 | ftm_rfnv_dispatch (catch-all 0x0000..0xffff) |
| 0x00 | 0 | 26 | 0xc41118d8 | OEM Motorola (`mot_diag.c`) |
| 0x4B | 75 | 5 | 0xc35abac8 | ADC diag |
| 0x2F | 47 | 15 | 0xc3fb6060 | (otros) |

Registro FTM: `rec@0xc8dc3b50 (seg23): subsys=0x0B count=75 tbl=0xc37bd1e8`.

---

## 3) TABLA FTM (subsys 0x0B) — 74 selectores reales

vaddr 0xc37bd1e8 / off 0x01c7b1e8 / seg b21. Entry = `{u16 sel_lo, u16 sel_hi, u32 disp=0xd8150ed8}`.

Selectores (u16) presentes, ordenados:
```
0x00 0x01 0x02 0x03 0x07 0x08 0x09 0x0a 0x0b 0x0d 0x10 0x11 0x12 0x14 0x15 0x1b
0x1d 0x1e 0x1f 0x20 0x21 0x22 0x23 0x24 0x25 0x27 0x28 0x2f 0x30 0x31 0x32 0x33
0x38 0x39 0x3a 0x3b 0x3c 0x3d 0x3e 0x3f 0x41 0x42 0x43 0x44 0x45 0x46 0x47 0x4a
0x4b 0x4c 0x4d 0x4e 0x4f 0x50 0x51 0x52 0x53 0x54 0x55 0x56 0x57 0x65 0x66 0x67
0x68 0x69 0x79 0x7a 0x7b 0x7c 0x7e 0x80 0x8000 0x8001
```
Lista completa con orden de tabla en `ftm_subsys_0x0B_selector_table.txt`.

### Identificación de tecnología (mapeo canónico Qualcomm FTM — NO verificado por símbolo)

**IMPORTANTE / HONESTIDAD**: los handlers viven en 0xd815xxxx (relocado, ausente del blob),
así que NO pude leer el código que discrimina la tecnología. El mapeo de abajo es la
convención CANÓNICA de Qualcomm FTM (estable entre builds MPSS), presentada como HIPÓTESIS
a verificar en vivo, no como dato extraído del binario:

| selector | tecnología (canónica Qualcomm, HIPÓTESIS) |
|----------|--------------------------------------------|
| 0x00 (0)   | FTM_COMMON |
| 0x01 (1)   | FTM_1X / CDMA |
| 0x02 (2)   | FTM_HDR (EVDO) |
| 0x07 (7)   | FTM_GSM  ← respondió en vivo, exclusivo de FTM |
| 0x08 (8)   | FTM_WCDMA |
| 0x1e (30)  | FTM_LTE (variante) |
| 0x27 (39)  | **FTM_LTE (canónico)** ← respondió en vivo |
| 0x28 (40)  | FTM_TDSCDMA |
| 0x8000/0x8001 | FTM_NR5G (sub6 / mmW) — el bit alto 0x8000 es típico de NR5G |

Selectores que respondieron EN VIVO y su presencia:
| sel viva | en FTM 0x0B | en MOT_DIAG 0x00 |
|----------|-------------|------------------|
| 0x07 (7)  | **sí (solo aquí)** | no |
| 0x0d (13) | sí | sí |
| 0x10 (16) | sí | sí |
| 0x14 (20) | sí | sí |
| 0x1b (27) | sí | sí |

---

## 4) Segundo nivel: comandos FTM_RF_TEST, IQ capture, RX/TX tune

El sub-dispatch por `ftm_cmd_id` ocurre dentro del dispatcher runtime (0xd815xxxx), no
extraíble estáticamente. Pero SÍ se recuperó la **definición de campos/acciones (TLV)** del
FTM RF-test, que es lo que va en el payload:

### 4a) Tabla de nombres de parámetros TLV del RF-test
Cluster rodata ~0xc414b000-0xc414c000 (seg21). Arrays de punteros a nombres en seg23
(0xc906c5c0 y 0xc906c7a0). Incluye acciones/campos de IQ capture y tune. Extracto:

- **IQ capture**: `IQ_CAPTURE` (0xc414b5ae), `IQ_CAPTURE_TYPE` (0xc414baea),
  `FETCH_IQ` (0xc414b959), `NUM_OF_SAMPLES` (0xc414b962), `IQ_DATA_FORMAT` (0xc414b971),
  `SAMP_FREQ` (0xc414b980), `NUM_OF_CAPTURES` (0xc414bb53),
  `IQ_SAMPLE_DEBUG_INFO_0/1/2` (0xc414bb63…).
- **RX**: `RX_AGC`, `LNA_GS`, `RX_MODE`, `RX_SLOT`, `RX_CARRIER`, `RX_GAIN_CTL_TYPE`,
  `RX_IF_BACKOFF`, `RX_AGC_OUTER_LOOP`, `OVERRIDE_LNA`, `SENSITIVITY*`, `CTON*`.
- **TX**: `TX_ACTION`, `TX_CARRIER`, `TX_WAVEFORM`, `TX_SLOT`, `TX_POWER_SWEEP_*`,
  `MODULATION_TYPE`, `TX_ON_DURATION`.
- **tune/mode**: `MODE` (0xc414b64c), `CONT_MODE`, `CENTER_FREQ`, `TECH_MODE`, `SUB_TECH`,
  `BWP_*`, `BAND`, `CHANNEL`, `BANDWIDTH`.
Lista completa (327 nombres): `ftm_rftest_tlv_param_names.txt`.

### 4b) Módulos fuente confirmados (filenames en rodata)
`ftm_rf_test_iq_capture.c`, `ftm_rf_test_rx_measure.c`, `ftm_rf_test_tx_measure.c`,
`ftm_rf_test_control.c`, `ftm_rf_test_radio_config.c`, `ftm_rf_test_tx_control.c`,
`ftm_rf_test_command_capability.c`, `ftm_rf_test_wait_trigger.c`,
`ftm_multi_tech_rf_test.c`, `ftm_nr5g_rf_test.c`, `ftm_lte_rf_test.c`,
`ftm_common_dispatch.c`, `ftm_lte_common_dispatch.c`, `rflte_dispatch.c`,
`ftm_rfnv_dispatch.c`, `nr_rx_ctl_iq_capt.c`, `nr5g_ml1_iq_capture.c`.

### 4c) IQ capture logs / state machine (NR5G ML1)
Enum de estados y requests (rodata 0xc3ffd0xx):
`NR5G_ML1_IQ_CAPTURE_START_REQ / STOP_REQ / ABORT_REQ / DIAG_REQ / IQ_LOG_REQ /
INTERNAL_TRIGGER_REQ / MAC_METRICS_REQ / TTI_WP_LOG_REQ / NB_LOG_REQ`, con CNF/IND paralelos.
También `[FTM.RFTEST][IQ_CAPTURE][UNPACK/REPACK]` (0xc37c08f4…), `nrfw_rx_iq_capture`.

### 4d) RX/TX tune (contexto, NO comando DIAG directo)
`RX_TUNE_CMD`, `RX_TUNE_COMP_CMD`, `RX_TUNE_COMP_DELAY_CMD` (0xc3797…) son mensajes internos
del searcher/AFC 1X (state-machine), no command-ids DIAG-FTM. `rflm_c2k_rx_rf_tune`,
`rflm_dtr_alloc_tune*`, `rx_tune_table_array`, `lte_ml1_tunemgr*` son rutinas RFLM internas.
Detalle en `iq_capture_rx_tune_strings.txt`.

---

## 5) MOT_DIAG (subsys 0x00) — tabla trampa

vaddr 0xc41118d8, 26 entries, `mot_diag.c`. Command codes solapan con selectores FTM:
- 0x01 → version string `4.3.4-00494-MANNAR_GEN_PACK-1.24452.133`
- 0x15(21) → `mot_diag_provision_imei`
- 0x1a(26) → `mot_diag_provision_sl_db`
- 0x1b(27) → `mot_diag_get_rsu_key`
- 0x1c(28) → `mot_diag_simlock_relock`
- 0xf9(249) → `mot_diag_tf_rsu_test`
- 0xfc(252) → `mot_diag_validate_sl_db`
Detalle en `ftm_selector_table.txt`.

---

## 6) Limitaciones honestas

1. **Handlers FTM no desensamblables**: todos apuntan a 0xd8150ed8 (0xd815xxxx no está en
   ningún PT_LOAD → relocado en runtime). No pude leer el código que mapea
   selector+ftm_cmd → función, ni confirmar por símbolo la tecnología de cada selector.
2. Los **ftm_cmd_id de segundo nivel** (set-mode / tune / IQ) por tecnología NO están como
   tabla de punteros estática legible; el sub-dispatch es interno al dispatcher runtime.
   Lo que SÍ se tiene: los nombres TLV de campos/acciones (sección 4a) y los módulos (4b).
3. El mapeo selector→tecnología (sección 3) es la convención canónica Qualcomm (hipótesis),
   no extraído por símbolo. Verificar en vivo enviando a cada selector.

## Archivos en /tmp/modemre/findings/
- `00_program_headers.txt` — mapeo de segmentos
- `ftm_subsys_0x0B_selector_table.txt` — **74 selectores FTM reales**
- `ftm_selector_table.txt` — tabla OEM Motorola (subsys 0x00)
- `ftm_rftest_tlv_param_names.txt` — 327 nombres de campos/acciones RF-test (IQ/RX/TX/tune)
- `iq_capture_rx_tune_strings.txt` — strings IQ capture / rx tune
- `01_strings_ftm.txt` — dump de strings FTM/diag con vaddr
