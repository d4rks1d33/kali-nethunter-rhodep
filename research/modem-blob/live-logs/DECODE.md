# DECODE.md — Análisis de las respuestas RF-test FTM (0x27) en vivo

Modem: Qualcomm SM6375 (Moto G82 5G).
Build (de findings estáticos): `MPSS.HI.4.3.4-00494-MANNAR_GEN_PACK-1.24452.133`.
Transporte: DIAG sobre QRTR. Comando enviado: `0x4B 0x0B <cmd_id=0x0027> <sub16> <num_tlv> {TLVs}`.
Logs: `d27s0.log` (sub 0), `d27s5.log` (sub 5), `d27s6.log` (sub 6), `one{0,5,6}.log` (mismos subs con `num_tlv=0`).

---

## 0. TL;DR — el resultado más importante (leelo primero)

**NO se pudo extraer CMD_MASK, y la razón es concreta y demostrada, no un fallo de parseo:**

1. **El "REPACK de ~16 KB" NO es la respuesta al comando.** Es tráfico de log F3/telemetría
   de fondo (WLAN/WCN + calibración RF) que el modem emite continuamente por el socket
   DATA. Los sub-paquetes `0x60...` aparecen en el stream **ANTES** de que se enviara el
   comando (desde t=7828.35, el comando fue a t=7837.51) y aparecen **idénticos en los tres
   logs** (s0, s5, s6) al mismo ritmo, sin importar qué sub_command se mandó. **FACT.**

2. **La respuesta real al comando es el `CMD REPLY 4`, que es un ECHO LIMPIO** del request,
   sin TLVs adicionales. El modem aceptó el comando (sin status 0x14) y **no devolvió
   field_id 3 (CMD_MASK) ni ningún otro TLV de respuesta.** **FACT.**

3. **`cmd_id=0x27` bajo subsys FTM(0x0B) es `FTM_LTE`, NO el framework multi-tech
   `ftm_common_rf_test`.** COMMAND_CAPABILITY / RADIO_CONFIG / IQ_CAPTURE viven en el
   dispatcher genérico, que muy probablemente cuelga de **`FTM_RF=0x03`** (aún no sondeado
   correctamente). Ver §6. Esto coincide con el hallazgo estático previo
   (`rftest_subcommand_enum_entermode.md` §3d). **FACT (selector table) + INFERENCE (dónde vive RF-test).**

=> **CMD_MASK: UNKNOWN.** No está en estas capturas. Para obtenerlo hay que (a) sondear el
otro dispatcher, y/o (b) habilitar el ruteo de los F3 `[FTM.RFTEST][*][REPACK]` al socket.

---

## 1. Estructura del framing decodificada (FACT)

### 1a. El hex del log ESTÁ TRUNCADO
La línea `hex:` del REPACK está **truncada a 400 caracteres hex = 200 bytes** de los 16313 B
reales (`DATA REPLY 2: 16313 B`). Confirmado: `awk length` = 400 hex chars exactos.
No hay ninguna otra línea en el log con el hex completo. **Trabajé con los 200 B disponibles**
— pero como se explica abajo, ese contenido no es la respuesta, así que la truncación no
impide la conclusión.

### 1b. Tipos de mensaje DIAG presentes en el stream (todos identificados)
El "datagrama" concatena varios sub-paquetes DIAG, delimitados por `0x7e` (fin HDLC) con
CRC16 de 2 bytes antes. Primer byte = tipo de mensaje DIAG:

| byte | tipo DIAG | contenido observado |
|------|-----------|---------------------|
| `0x10` | `DIAG_LOG_F` | log binario: `[10 00][len:u16][len:u16][code:u16][ts:u64][payload]` |
| `0x79` | `DIAG_EXT_MSG_F` | F3 debug ASCII: `[79 00 00 00][ts:u64][...]` p.ej. `wal_pm_wmac_...`, `state_change_request` |
| `0x92` | `DIAG_QSR_EXT_MSG_TERSE_F` | mensajes QSHRINK (hash + args) |
| `0x99` | QSR/otro terse | tablas binarias |
| `0x60` | **subsys custom (RF/cal telemetry)** | el "REPACK" — ver §1c |
| `0x06` | **QRTR control (no DIAG)** | los "DATA REPLY: 20 B" `06...` — `type=6=DEL_CLIENT`, no son datos |

El `0x60` inicial NO es un "response code / response bit"; es simplemente el opcode del tipo
de mensaje de log de ese sub-sistema. **FACT.**

### 1c. Estructura del sub-paquete `0x60` (el mal-llamado "REPACK")
Cabecera (13 bytes) + N registros + CRC + `0x7e`:

```
60            opcode
9c 00         len u16 LE (0x009c aquí)   [varía por paquete]
f5 6a         marcador fijo (0x6af5)     [constante en TODOS los 0x60]
7f 5b 09 ec   ts_lo u32 LE
bb 99 12 01   ts_hi u32 LE  (=0x011299bb, constante toda la sesión)
<registros...>
21 89         CRC16
7e            fin HDLC
```

Registro (37–38 bytes, layout FACT):
```
20 01         field_id u16 LE   (0x0120 = 288)
04 02         campo A2 (varía: 0x0204 / 0x0104 / 0x0401)
00 01         campo A3
04 01         campo A4
00            byte
8a c2 27      tag/const de 3 bytes  (0x27c28a) [firma del registro, constante]
a1 19 d0 00   ptr1 = 0x00d019a1   (puntero DDR)
00 d0 00 00   size = 0x0000d000 = 53248
a1 19 d0 00   ptr2 = 0x00d019a1   (mismo puntero)
00 00 00 00 00 00 00 00 00 00 00   padding
f4 ea / f5 ea  ...  (fragmento CRC/escape que precede al siguiente registro)
```

Esto **SÍ** coincide con el formato REPACK `{field_id, size, pointer_DDR}` — o sea, el
mecanismo REPACK es real y así se ve — **pero los field_id que aparecen son 0x0120/0x0121,
no los del grupo 22 (CMD_MASK=3)**, y el puntero/size son constantes (`0x00d019a1` / `0xd000`)
en las tres capturas. Es un dump periódico de un buffer de calibración/telemetría, no la
respuesta al comando.

Interpretación del `2001 / 2101`:
- `2001` = field_id 0x0120 (288), `2101` = field_id 0x0121 (289). **FACT (decodificado).**
- A qué corresponden 288/289: **UNKNOWN**. No están en las tablas de field-ids extraídas
  (IQ_CAPTURE, RX_TUNE, groups_raw sólo llegan hasta ~222 campos en g18). Los grupos
  RF-test usan field_ids chicos (1..~222); 288/289 pertenecen a OTRO namespace (probable
  log/QSR de RxDCO — los strings de fondo incluyen `RXDCO 0_1::: rxdco_cal_m`,
  `RxDCO Restored Data of`). **INFERENCE: es telemetría de RxDCO cal, no TLV RF-test.**

### 1d. El patrón `a119d000` que se repite
`a119d000` LE = `0x00d019a1`. Es un puntero DDR **constante** que se repite porque cada
registro apunta al **mismo** buffer (`ptr1==ptr2`, size 0xd000=52 KiB). Es una lista de N
entradas homogéneas (todas al mismo buffer de 52 KiB) — coherente con un "snapshot handle"
periódico, no con una lista de comandos. **FACT.**

---

## 2. CMD_MASK — objetivo principal

**NO ENCONTRADO. UNKNOWN.**

- field_id 3 (CMD_MASK) **no aparece en ninguna respuesta** de las tres capturas.
- Verificación exhaustiva: los únicos paquetes `4b0b2700` en los logs son:
  - request: `4b0b2700 0500 0100 01000400 ffffffff`
  - reply:   `4b0b2700 0500 0100 01000400 ffffffff f461 7e`  ← **echo idéntico + CRC + 0x7e**
- El reply **repite el TLV de entrada** (`num_tlv=1`, fid=1/QUERY_COMMAND, len=4,
  val=0xffffffff) y **no agrega field_id 3**. Es decir: `sub_command=5` bajo `cmd_id=0x27`
  **no es COMMAND_CAPABILITY** (o al menos no responde con CMD_MASK). **FACT.**

Lista de sub_commands válidos del CMD_MASK: **no obtenible de estos datos.**

---

## 3. Qué es sub 5 vs sub 6 vs sub 0

Comportamiento observado (FACT, de los echoes):

| sub | num_tlv=1 (d27sN)                     | num_tlv=0 (oneN)                  | interpretación |
|-----|--------------------------------------|----------------------------------|----------------|
| 0   | echo limpio `4b0b2700 0000 0100 ...` | echo limpio                      | comando sin-arg aceptado |
| 5   | echo limpio `4b0b2700 0500 0100 ...` | `4b0b2700 0500 0000 8ac0 7e` OK  | comando sin-arg aceptado |
| 6   | echo limpio `4b0b2700 0600 0100 ...` | echo limpio                      | comando sin-arg aceptado |

- **Los tres (0/5/6) aceptan tanto num_tlv=0 como num_tlv=1** y devuelven echo sin error
  (sin status 0x14). No exigen TLV. **FACT.**
- **s5 vs s6 NO difieren en la respuesta al comando** — ambos echo limpio. La "diferencia"
  de tamaño (16313 vs 16117 B en `DATA REPLY 2`) es **ruido de fondo** (cuánta telemetría
  cayó en esa ventana de tiempo), no una diferencia de respuesta. Prueba: s0 dio 582 B en
  esa misma posición, y los registros `0x60` con `8ac227` aparecen ~9 veces en cada log
  independientemente del sub. **FACT.**

=> Ni sub 5 ni sub 6 son COMMAND_CAPABILITY (ninguno devuelve mask). Bajo `cmd_id=0x27`
(=FTM_LTE), son sub-comandos **sin argumento obligatorio** — candidatos a
ENTER_MODE / EXIT_MODE / GET_STATE / control de estado del path LTE legado.
**INFERENCE (media)**, consistente con el análisis estático `rftest_subcommand_enum_entermode.md §3d`.

---

## 4. Pistas sobre RADIO_CONFIG / RX_MEASURE / IQ_CAPTURE

- **NO están bajo `cmd_id=0x27`.** `0x27` = selector `FTM_LTE` en la tabla de dispatch
  subsys 0x0B (`ftm_subsys_0x0B_selector_table.txt` idx[4] = 0x0027 = FTM_LTE). El framework
  multi-tech `ftm_common_rf_test` (que define RADIO_CONFIG/RX_MEASURE/IQ_CAPTURE/
  COMMAND_CAPABILITY) es un dispatcher aparte. **FACT (selector table).**
- Candidato para el dispatcher RF-test genérico: **`FTM_RF=0x03`** (idx[1]=0x0003 en la
  tabla; y `iq_capture_rx_tune_strings.txt` refiere `ftm_rf_test_command_capability.c
  @0xc37c040b`). **INFERENCE fuerte — falta sondearlo en vivo.**
- Los field_ids IQ_CAPTURE/RX_TUNE ya extraídos (findings) usan índices chicos (1..~222) y
  **no coinciden** con los 0x0120/0x0121 vistos en el stream `0x60`, confirmando que el
  stream `0x60` no es RF-test. **FACT.**

---

## 5. Recomendación para armar la secuencia de captura IQ

1. **Sondear `cmd_id=0x03` (FTM_RF)** con el mismo patrón: barrer `sub_command 0..15` con
   `num_tlv=0`, y con `QUERY_COMMAND=0xFFFFFFFF` (fid=1,len=4). Ahí debería aparecer
   COMMAND_CAPABILITY y devolver **field_id 3 = CMD_MASK** como TLV en el `CMD REPLY`
   (no en el stream DATA de fondo). Ese es el objetivo real.
   - Request de prueba: `4b0b 0300 <sub16> 0100 01000400 ffffffff`
2. **Leer el CMD_MASK del CMD REPLY** (no del socket DATA). Cada bit N=1 => sub_command N
   existe. Luego `QUERY_COMMAND=N` + leer PROPERTY_MASK para nombre/propiedades.
3. Si `0x03` tampoco devuelve TLVs de respuesta, el problema es de **ruteo de logs**:
   habilitar el ruteo de mensajes F3 del subsistema FTM/RFTEST al socket, para capturar los
   `[FTM.RFTEST][COMMAND_CAPABILITY][REPACK]` reales (que sí llevan CMD_MASK/pointer).
   Actualmente **no hay ni un solo string `[FTM.RFTEST]` / `REPACK` en las capturas** — la
   telemetría FTM/RFTEST no está llegando a este canal. **FACT.**

---

## 6. Etiquetado honesto FACT / INFERENCE / UNKNOWN

**FACT (decodificado con certeza):**
- El hex del REPACK en el log está truncado a 200 B de 16313 B.
- Los sub-paquetes `0x60` son telemetría de fondo (aparecen antes del comando e idénticos en s0/s5/s6).
- Framing DIAG multiplexado por `0x7e`; tipos 0x10/0x79/0x92/0x99/0x60 identificados; `0x06`=QRTR ctrl.
- Estructura del registro `0x60`: `{field_id, A2, A3, A4, tag 8ac227, ptr1=0x00d019a1, size=0xd000, ptr2}`.
- field_id observados en `0x60`: 0x0120 (288), 0x0121 (289). ptr/size constantes.
- La respuesta real al comando es un **echo limpio sin TLVs de respuesta**; no hay field_id 3.
- sub 0/5/6 aceptan num_tlv 0 y 1 sin error; no difieren entre sí en la respuesta.
- `cmd_id=0x27` = selector FTM_LTE (no el framework multi-tech RF-test).

**INFERENCE:**
- Los `0x60` son telemetría de RxDCO/calibración (por los strings de fondo). (media)
- sub 5/6 bajo 0x27 son control/estado del path LTE legado. (media)
- RADIO_CONFIG/RX_MEASURE/IQ_CAPTURE/COMMAND_CAPABILITY viven bajo FTM_RF=0x03. (fuerte)

**UNKNOWN:**
- CMD_MASK (lista de sub_commands válidos del dispatcher RF-test genérico).
- Números de enum de RADIO_CONFIG / RX_MEASURE / IQ_CAPTURE.
- Significado exacto de field_id 288/289 en el stream de telemetría.
- Si FTM_RF=0x03 responde con TLVs por el canal CMD (hay que probarlo).
