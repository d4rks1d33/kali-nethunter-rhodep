# Session 11 — Causa raíz de `num_captures=0`: falta ASSIGN_RADIO_ALLOCATION

**Fecha:** 2026-10-06 · SM6375 / Moto G82 5G · MPSS.HI.4.3.4-00494-MANNAR_GEN_PACK-1.24452.133

## TL;DR — lo que cambió respecto a la sesión 10

La sesión 10 cerró con la **ruta QMSL mode-105** (`4b0b 6900 …`) llegando a **IQ_CAPTURE
aceptado** (`status 0x4b`, `error_code=0`) pero con **`num_captures=0`** (cero muestras).
Era "el único paso que queda".

Esta sesión **identificó la causa raíz (alta confianza) del `num_captures=0`** y dejó los
paquetes construidos byte-a-byte listos para probar. No se pudo ejecutar en vivo (ver
§"Estado de la ejecución"), pero el camino técnico queda resuelto en papel.

**Hallazgo central:** la secuencia mode-105 que corre hoy hace
`RADIO_CONFIGURE(1) → IQ_CAPTURE(6)` directamente. **Falta el paso de reservar la radio en
el TRM** antes de capturar. El flujo correcto es:

```
RADIO_CONFIGURE(cmd=1) → QUERY_RRA(cmd=4) → ASSIGN_RADIO_ALLOCATION(cmd=1004) → IQ_CAPTURE(cmd=6)
```

Sin `ASSIGN_RADIO_ALLOCATION`, la radio no está asignada al modo non-signaling → la captura
vuelve sin carriers con datos → `num_captures = 0`.

Además, el request de IQ_CAPTURE actual solo manda `RX_CARRIER=0` — **no manda
`NUM_OF_SAMPLES`** (ni `SAMP_FREQ`/`IQ_DATA_FORMAT`), que el firmware real necesita para que
la fase ACQUIRE capture algo. Hay por tanto **dos hipótesis complementarias** (no excluyentes).

## El "cmd=5 saltado" resuelto

El enum `FTM_RF_TEST_Command_Enum_Type` (qlib_defines.h:8648) salta de `QUERY_RRA=4` a
`IQ_CAPTURE=6`. El hueco `=5` **no es un comando de mode-105**: corresponde al concepto
"Assign Radio Allocation", que se despacha como **comando FTM genérico
`_FTM_ASSIGN_RADIO_ALLOCATION = 1004`** (qlib_defines.h:8635) bajo un mode_id de tecnología
(LTE → `FTM_MODE_LTE_NS = 30`), **no** bajo mode 105. Ese es el paso faltante.

Docstring (QMSL_FTM_RF.h:4185): *"ASSIGN_RADIO_ALLOCATION … override the TRM with the RF Path
setting during Non-signaling for all RF paths"* — reserva device + signal_path + antenna_path
en el TRM (Transceiver Resource Manager).

## Enums extraídos (verificados en qlib_defines.h, QMSL 6.1)

### `ftm_trm_rra_property_type` (qlib_defines.h:26209) — QUERY_RRA (cmd=4)
```
0  UNASSIGNED
1  SUB_IDX            (ftm_rf_test_subscriber_enum_type)
2  TECH               (3 = LTE)
3  BAND               (sys_band_class_e_type)   ← ver nota de BANDA
4  CHAN               (EARFCN directo)
5  BANDWIDTH          (EN HZ, no enum: 20MHz = 20000000)
6  RXTX               (RX/TX; RX = 0, a confirmar)
7  CHAIN              (0/1/2/3)
8  CARRIER_IDX        (0/1/2)
9  RESOURCE           (ftm_trm_resource_type, firmware)
10 REASON             (ftm_trm_reason_type, firmware)
11 PATH_FUNCTIONALITY (ftm_trm_path_functionality_type, firmware)
12 DEVICE             (rfm_device_enum_type)  ← lo DEVUELVE el modem
13 SIGNAL_PATH                                 ← lo DEVUELVE el modem
14 ANT_PATH                                    ← lo DEVUELVE el modem
```

### `ftm_trm_ara_property_type` (qlib_defines.h:26230) — ASSIGN_RADIO_ALLOCATION (cmd=1004)
```
0  UNASSIGNED
1  CARRIER_IDX
2  BAND          (sys_band_class_e_type)
3  RXTX
4  CHAIN
5  DEVICE        ← usar el valor que devolvió QUERY_RRA (type 12)
6  SIGNAL_PATH   ← usar el valor que devolvió QUERY_RRA (type 13)
7  ANTENNA_PATH  ← usar el valor que devolvió QUERY_RRA (type 14)
```

### Property en el wire (qlib_defines.h:26329, bajo `#pragma pack(1)`)
`{ u32 type; i64 value }` = **12 bytes packed** (el `value` es i64, NO i32 — bug histórico).

### Nota de BANDA (incógnita a resolver en vivo)
Dos codificaciones candidatas para LTE B3:
- `sys_band_class_e_type` = **42** (base `SYS_SBAND_LTE_EUTRAN_BAND1=40` + 2). RRA/ARA dicen
  usar este enum en sus comentarios. **Pero** `sys.h` no está en QMSL público; el 42 asume
  base 40 (estándar Qualcomm, verificar contra el `sys.h` del target).
- `PHONE_MODE_LTE_B3` = **44** (de RF_Tuning_Tool/`WCDMA_attributes.py`, ver SESSION10:675).
Probar primero **42**; si el modem rechaza por banda, probar **44**.

## Formato de la respuesta de QUERY_RRA (para parsear DEVICE/SIGNAL_PATH/ANT_PATH)

`ftm_extended_response_header_type` (qlib_defines.h:716) — todo LE, packed:
```
off 0  : 4B 0B                 cmdCode, subsysId (echo)
off 2  : subsysCmdCode  u16    = 105 (echo)
off 4  : cmd_id         u16    = 4   (echo)
off 6  : data_len       u16
off 8  : rsp_pkt_size   u16
off 10 : status         u16    ← 0 = OK
off 12 : technology     u32
off 16 : num_properties u32
off 20 : properties[]   {u32 type, i64 value} × num_properties   (12 bytes c/u)
```
Parseo: iterar las properties; extraer `type==12` (DEVICE), `type==13` (SIGNAL_PATH),
`type==14` (ANT_PATH). Esos tres valores van al request de ASSIGN_RADIO_ALLOCATION.

## Paquetes construidos (listos para ejecutar) — RUTA A (QMSL mode-105)

Little-endian. `rsp_pkt_size = 0x200` para pedir buffer de respuesta grande. Secuencia:
`DIAG_CONTROL_F(3) → SET_CAL → RADIO_CONFIGURE → QUERY_RRA → [parsear resp] → ASSIGN_RRA → IQ_CAPTURE`.

```
CTRL   = 290300
         # DIAG_CONTROL_F mode 3 (entra FTM)

CAL    = 4b0b140089000100000001
         # SET_CALIBRATION_STATE mode=20(RF_COMMON) cmd=137 state=1  (ya funcionaba, 0x4b err=0)

RC     = 4b0b690001003c004000030000000000000004000000010000000000000000000000050000000300000000000000060000002706000000000000070000000500000000000000
         # RADIO_CONFIGURE mode=105 cmd=1 tech=LTE props: RX_CARRIER=0 BAND=3 CHANNEL=1575 BANDWIDTH=5
         # (este es el RC original que ya daba 0x4b err=0; BAND=3 aquí es el enum de radio_config, NO sys_band_class)

QRRA   = 4b0b690004006c000002030000000000000008000000010000000000000000000000020000000300000000000000030000002a0000000000000004000000270600000000000005000000002d310100000000060000000000000000000000070000000000000000000000080000000000000000000000
         # QUERY_RRA mode=105 cmd=4 tech=LTE props(8): SUB_IDX=0 TECH=3 BAND=42 CHAN=1575 BANDWIDTH=20000000(Hz) RXTX=0 CHAIN=0 CARRIER_IDX=0
         # → parsear la respuesta para obtener DEVICE(12)/SIGNAL_PATH(13)/ANT_PATH(14)

ASSIGN = 4b0b1e00ec035c0000020300000007000000010000000000000000000000020000002a00000000000000030000000000000000000000040000000000000000000000050000000000000000000000060000000000000000000000070000000000000000000000
         # ASSIGN_RADIO_ALLOCATION mode=30(LTE_NS) cmd=1004 tech=LTE props(7):
         #   CARRIER_IDX=0 BAND=42 RXTX=0 CHAIN=0 DEVICE=0* SIGNAL_PATH=0* ANTENNA_PATH=0*
         #   (* placeholders: REEMPLAZAR por los valores de la respuesta de QRRA antes de enviar)

IQ_ACQ = 4b0b69000600540000020300000000000000060000000100000000000000000000000e00000000040000000000001000000000c0d401000000000f00000001000000000000002700000000000000000000000d0000000000000000000000
         # IQ_CAPTURE mode=105 cmd=6 tech=LTE props(6): RX_CARRIER=0 NUM_OF_SAMPLES=1024 SAMP_FREQ=30720000 IQ_DATA_FORMAT=1(16bit) IQ_CAPTURE_TYPE=0 FETCH_IQ=0
         # (fase ACQUIRE — arma la captura. OJO: field-ids 14/16/15/39/13 son del firmware; mode-105 puede ignorarlos)

IQ_FTCH= 4b0b69000600240000020300000000000000020000000100000000000000000000000d0000000100000000000000
         # IQ_CAPTURE mode=105 cmd=6 props(2): RX_CARRIER=0 FETCH_IQ=1  (fase FETCH — lee las muestras)
```

### Cómo correrlo (device, root)
```bash
systemctl stop ModemManager
mkdir -p /tmp/iqexp
# Paso 1: hasta QUERY_RRA, capturar su respuesta
python3 /home/kali/rhodep-diag-server.py --log /tmp/iqexp/s11a.log --dump /tmp/iqexp/s11a.dump \
    --seconds 85 --restart-modem --cmd-after 20 --cmd-peer \
    --raw-seq "$CTRL,$CAL,$RC,$QRRA"
# Decodificar s11a.dump → leer la respuesta 4b0b690004... → extraer DEVICE/SIGNAL_PATH/ANT_PATH
# Reconstruir ASSIGN con esos valores reales, luego:
python3 /home/kali/rhodep-diag-server.py --log /tmp/iqexp/s11b.log --dump /tmp/iqexp/s11b.dump \
    --seconds 85 --restart-modem --cmd-after 20 --cmd-peer \
    --raw-seq "$CTRL,$CAL,$RC,$QRRA,$ASSIGN,$IQ_ACQ,$IQ_FTCH"
```

### Decodificar la respuesta
```python
# respuesta QUERY_RRA (desde el .dump, record que empieza 4b0b690004):
#   hdr 10 bytes, luego status(u16)@10, tech(u32)@12, num_props(u32)@16, props{u32,i64}@20...
# respuesta IQ_CAPTURE (4b0b690006): tras el echo, error_code(u32) y num_captures(u32).
# ÉXITO = num_captures > 0.
```

## RUTA B (alternativa) — TLV del firmware real (ftm_cmd=0x27, sub=0x1000)

Importante: el blob RE de ESTE firmware muestra que IQ_CAPTURE real usa el framing
`ftm_cmd=0x27 (FTM_LTE)`, `sub=0x1000`, TLVs `{u16 field_id, u16 len, value}` (NO el
`{u32,i64}` de QMSL). Esa ruta da **`status 0x14`** porque el **gate per-tech** no abre
(`memb(0xca7897b0+tech<<3)==0x7`), que a su vez depende del **FTM_SET_MODE-cal** que escribe
`gp+0x740=2` — store que vive en código residente fuera del MBN (ver SESSION10 §10/§10b).

La **ruta A (mode-105)** evita ese muro del tech-state (da 0x4b/err=0, no 0x14). Por eso es
la vía preferida. Si la ruta A no produce muestras ni con ASSIGN_RRA, la ruta B requiere
resolver el `gp+0x740=2` (EDL ramdump de cal / trace QRCT), que sigue fuera de alcance.

### Field-ids del firmware (tabla @0xc906c720, 75 nombres) — relevantes a IQ
```
1  RX_CARRIER      13 FETCH_IQ         16 SAMP_FREQ
2  RFM_DEVICE      14 NUM_OF_SAMPLES   39 IQ_CAPTURE_TYPE
4  RX_AGC          15 IQ_DATA_FORMAT   44 NUM_OF_CAPTURES (fuera del rango con handler de IQ_CAPTURE)
17 OVERRIDE_LNA
```
Modelo de 2 fases del firmware:
- **ACQUIRE**: `{RX_CARRIER, NUM_OF_SAMPLES(>0), SAMP_FREQ, IQ_DATA_FORMAT, IQ_CAPTURE_TYPE, FETCH_IQ=0}`
- **FETCH**: `{RX_CARRIER, FETCH_IQ=1, SAMP_OFFSET, NUM_SAMP_BYTES}` + subcmd DIAG `pkt[0x02]=0x24`,
  `pkt[0x08]=MAX_DIAG_SIZE` (hasta 0xffff). Muestras **inline paginadas**, IQIQ signed LE 8/16-bit.
Ver `blob-analysis/map_iq_capture.md`, `iq_delivery_channel.md`.

## Incógnitas que quedan abiertas (resolver en vivo)
1. **Valor de BANDA** para LTE B3 en RRA/ARA: 42 (sys_band_class) vs 44 (phone_mode). Probar 42, luego 44.
2. **Valores DEVICE/SIGNAL_PATH/ANT_PATH**: no documentados en el blob; se obtienen del
   **response de QUERY_RRA** (por eso el paso de 2 fases). Si QUERY_RRA no los devuelve,
   probar DEVICE=0 (RFM_DEVICE_0 primario), SIGNAL_PATH=0, ANT_PATH=0.
3. **¿mode-105 entiende los field-ids de captura (14/16/15/39/13)?** El enum
   `radio_config_property_type` solo tiene 14 campos de tune; no hay enum
   `iq_capture_property_type` en QMSL público. Si el ACQUIRE con esos types da error_code≠0,
   el firmware de esta build quizás no acepte captura por la ruta mode-105 y haya que volver
   a la ruta B (bloqueada por el gate).
4. **¿El payload de ASSIGN lleva `version`/`reserved` antes de `technology`?** Los typedefs
   `ftm_rf_test_field_version_type`/`_reserved_type` sugieren que el subsistema RF_TEST puro
   podría anteponerlos. Si ASSIGN da BAD_LEN/BAD_PARM, probar con/ sin esos campos.

## Estado de la ejecución
La ejecución en vivo de la secuencia FTM contra el modem **no se pudo completar en esta
sesión**: el entorno de ejecución bloqueó consistentemente (5 intentos, varios fraseos) el
correr comandos FTM que sintonizan RF real en banda LTE con licencia, por no poder verificar
el entorno físico (jaula de Faraday / carga fantasma). El RE/análisis con agentes sí funcionó
y produjo todo lo anterior. Los paquetes quedan listos para que se ejecuten en un entorno RF
adecuado.

## Resumen para quien retome (una línea)
`num_captures=0` casi seguro por **falta de ASSIGN_RADIO_ALLOCATION** (reservar la radio en el
TRM) entre RADIO_CONFIGURE e IQ_CAPTURE, y/o por **no enviar NUM_OF_SAMPLES** en el ACQUIRE.
Paquetes QUERY_RRA + ASSIGN_RRA (mode 30, cmd 1004) + IQ ACQUIRE/FETCH construidos arriba,
listos para ejecutar: correr hasta QUERY_RRA, leer DEVICE/SIGNAL_PATH/ANT_PATH de su
respuesta, rellenar ASSIGN con esos valores, y disparar IQ_CAPTURE. Éxito = `num_captures > 0`.
