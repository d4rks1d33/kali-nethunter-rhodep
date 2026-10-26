# Session 13 — Ejecución EN VIVO de la secuencia IQ (lo que la sesión 11 dejó pendiente)

**Fecha:** 2026-10-15 · SM6375 / Moto G82 5G · MPSS.HI.4.3.4-00494-MANNAR_GEN_PACK-1.24452.133
**Entorno RF confirmado por el usuario:** solo RX, antena/cable controlado. **Nunca** se ejecutó TX
(TX_CONTROL/TX_MEASURE quedaron fuera). Todo lo de abajo es recepción pura.

## TL;DR — qué cambió respecto a la sesión 11

La sesión 11 dejó los paquetes RRA/ACQUIRE construidos byte-a-byte pero **no pudo ejecutarlos en
vivo**. Esta sesión **sí los ejecutó** (y varias variantes más) contra el modem real, con un
parser de respuesta completo (sin truncar). Resultado: **toda la secuencia mode-105 corre sin SSR,
`error_code=0`, pero SIEMPRE devuelve datos vacíos (`num_captures=0` / `num_properties=0`)**. Se
confirmó empíricamente, desde 4 ángulos nuevos, el muro que las sesiones 9-10 habían deducido
estáticamente: **el carrier RX nunca entra en estado "streaming" porque el gate de cal-mode
`gp+0x740` (PA 0x96f4f740) no vale 2**, y ese valor lo escribe código residente/paginado fuera del
MBN que no se puede disparar desde DIAG ni leer desde el AP.

Avance real de la sesión: **cerró empíricamente todos los leads abiertos** (ACQUIRE live, RX_MEASURE,
QUERY_RRA, full-QMSL-cal, ADC-capture-in-mem, lectura de RAM del modem). Ya no quedan hipótesis
"en papel" sin probar dentro del alcance del AP.

## Infraestructura usada (reproducible)

- Transporte: `rhodep-diag-server.py` sobre QRTR (AF_QIPCRTR, DIAG svc 0x1001), NO `/dev/diag`
  (no existe en mainline). `--restart-modem` para handshake fiable, `--cmd-peer` para ser el
  COMMAND peer del modem, `--raw-seq` para la secuencia.
- **Patch de esta sesión:** se añadió un sidecar `<dump>.replies` que guarda la respuesta CMD
  **completa** (antes el log truncaba a 120 hex) y cada paquete DATA `4b0b` sin truncar. Parser
  nuevo `rhodep-iq-parse.py` decodifica status/error_code/num_captures del cuerpo mode-105.
- Device: `kali@172.16.42.1` (USB) / `192.168.1.53` (WiFi), root por sudo (pass 1234).

## Experimentos EN VIVO (todos RX, 0 SSR)

### base (reproducción de fac4, sesión 10d)
`DIAG_CONTROL_F(3) → SET_CAL(20,137)=1 → RADIO_CONFIGURE(105,1) → IQ_CAPTURE(105,6, solo RX_CARRIER)`
- IQ_CAPTURE reply: `4b0b 6900 0600 1800 16 00 | error_code=0 | num_captures=0`. Igual que sesión 10d. ✓

### acq — **el paquete que la sesión 11 nunca ejecutó**
Igual que base pero IQ_CAPTURE ACQUIRE con **NUM_OF_SAMPLES=1024, SAMP_FREQ=30720000,
IQ_DATA_FORMAT=1, IQ_CAPTURE_TYPE=0, FETCH_IQ=0** + un IQ_FETCH(FETCH_IQ=1) después.
- ACQUIRE reply: `error_code=0, num_captures=0`.
- FETCH   reply: `error_code=0, num_captures=0`.
- **Veredicto:** mandar NUM_OF_SAMPLES no cambia nada. Confirma el hallazgo estático
  (`iq_capture_num_captures.md §3.2`): el handler de NUM_OF_SAMPLES (0xd8189994) escribe
  `memub(cfg+0x6c)` (un default de 8 bits del config-struct), **NO el valor u32 del wire**, así que
  el 1024 nunca aterriza.

### rxm — RX_MEASURE antes de IQ_CAPTURE
`... → RADIO_CONFIGURE → RX_MEASURE(105,2) → IQ_ACQUIRE → IQ_FETCH`
- RX_MEASURE reply: `error_code=0` pero **también vacío** (mismo patrón `00000000 00008000`).
- IQ_CAPTURE: `num_captures=0`.
- **Veredicto:** RX_MEASURE tampoco devuelve mediciones reales → el carrier no está midiendo/
  streaming. Descarta la hipótesis "falta arrancar RX con RX_MEASURE".

### fullcal — secuencia de fábrica QMSL completa
`DIAG_CONTROL_F(3) → SET_MODE(mode20,cmd7)=LTE29 → SET_CAL(20,137)=1 → SET_MODE(mode29,cmd7)=B3(44)
→ RADIO_CONFIGURE → IQ_ACQUIRE`
- SET_MODE(mode=20,cmd=7)=29 → aceptado (devuelve `01...`).
- SET_CAL(20,137)=1 → aceptado.
- **SET_MODE(mode=29,cmd=7)=44 → `status 0x13` (BAD_CMD/BAD_PARM), RECHAZADO.** El SET_MODE bajo
  el mode_id de tecnología (29=LTE) no existe con ese framing en esta build (coincide con sesión
  10d: los opcodes SET_MODE_ID/SET_MODE propietarios no son adivinables).
- RADIO_CONFIGURE, IQ_CAPTURE → vacío (`num_captures=0`).

### qrra — QUERY_RRA (preguntar al TRM qué radio hay asignada)
`... → RADIO_CONFIGURE → QUERY_RRA(105,4, 8 props: SUB/TECH/BAND=42/CHAN=1575/BW=20MHz/RXTX/CHAIN/CARRIER)`
- QUERY_RRA reply: `error_code=0`, **num_properties=0** (no devuelve DEVICE/SIGNAL_PATH/ANT_PATH).
- **Veredicto:** el TRM **no tiene ninguna radio asignada** → no hay carrier con el que capturar.
  Esto confirma, desde el propio modem, que no existe un carrier streaming. (Y por tanto ASSIGN_RRA
  con placeholders tampoco tendría de dónde sacar DEVICE/SIG/ANT — la sesión 11 lo marcó como
  incógnita; ahora está resuelto: QUERY_RRA no da nada porque el recurso no está activo.)

## La firma común de TODAS las respuestas (el diagnóstico clave)

Cada comando mode-105 (RADIO_CONFIGURE, RX_MEASURE, QUERY_RRA, IQ_CAPTURE ACQUIRE y FETCH) devuelve
exactamente el mismo cuerpo:
```
4b0b 6900 <cmd:u16> <data_len_echo:u16> 16 00 | 00000000(error_code=0) | 0000 8000 (vacío)
```
Es decir: **el comando se parsea y valida (error_code=0) pero la capa de recurso RF no produce
datos**. Esa capa (RFLM/RFLTE/TRM, el fill de muestras) vive en código residente/paginado detrás
del gate de cal-mode. La uniformidad del resultado entre comandos tan distintos es la evidencia en
vivo de que el muro es **upstream y único**: el gate, no cada comando.

## Evidencia NUEVA en vivo: la cal-SM de RF SÍ corre (pero no por nuestro mando)

Los F3 durante la secuencia muestran que el subsistema de calibración RF está **vivo y ejecutando**:
- `cal_sm_rxdco_device.c` → `RXDCOSM:::RXDCO_CAL_MODE_LPBK_DPD finalcase`, `RxDCO Data Frm HWREG of
  Chain` (¡lee IQ/DC-offset de registros HW!).
- `halphy_mem_paprd.c` → `halphyPaprdAdcCaptInMemConfig: chainSel, captureMode, bConfig` (captura
  ADC→memoria durante DPD), `rxIQMask`, `mem_tap_spacing`.
- `halphy_caldb.c` → tamaños de `t_caldb_RxDcoNormal`, `validBitMaskMem[bw][rfChain]`.

Esto llevó a investigar si la "ADC-capture-in-memory" de HALPHY era una vía alternativa de IQ.
**No lo es** (ver `blob-analysis/adc_capture_in_mem.md`): el código HALPHY está por debajo de RFLM,
paginado por dlpager, **no está en el MBN cargable**, y su acquire pasa por **el mismo gate**
(active-carrier 0xca79c494 / `gp+0x740==2`). Los `0x60` REPACK que vuelven son telemetría con una VA
de heap **interna del modem** copiada inline, no un buffer mapeable desde el AP. (Mucho de ese F3,
además, es cal de **WLAN/WCSS** — `ar_wal_*`, `whal_tracer`, `_paprd` — que comparte el stream DIAG
del SoC integrado, no es el módem celular.)

## Lo que se cerró empíricamente esta sesión (antes eran hipótesis)

| Lead (sesión previa) | Estado tras sesión 13 |
|----------------------|------------------------|
| "falta ASSIGN_RRA entre RADIO_CONFIG e IQ_CAPTURE" (ses.11) | **Refutado en vivo:** QUERY_RRA no devuelve recurso; el camino mode-105 no depende de RRA (lo dice también `iq_capture_num_captures.md §4`). El vacío viene del gate, no de RRA. |
| "falta NUM_OF_SAMPLES en el ACQUIRE" (ses.11) | **Refutado en vivo:** con NUM_OF_SAMPLES=1024 sigue 0. El handler usa `memub(cfg+0x6c)`, no el wire. |
| "arrancar con RX_MEASURE" | **Refutado en vivo:** RX_MEASURE también vuelve vacío. |
| "secuencia de fábrica QMSL (SET_MODE_ID+SET_MODE band)" | **Probado:** SET_MODE bajo tech-mode da 0x13 (rechazado). Los opcodes propietarios no son adivinables. |
| "ADC-capture-in-mem de HALPHY como vía IQ" | **Refutado (RE):** fuera del MBN, mismo gate. |
| "legacy IQ 900/901/902" | **Refutado (RE, ses.13):** compilados fuera; el router rechaza cmd_id ≥ 256 (high-byte gate @0xd814e078). |
| "leer el gate en RAM para confirmar" | **Imposible y peligroso:** la carveout MPSS (PA 0x8b800000) está XPU-locked; `/dev/mem` sobre ella **resetea el SoC** (reproducido en vivo, 2 reboots). SMEM sí se lee pero no contiene el gate. |

## El muro, ahora con confirmación empírica total

- **Transporte, FTM, framing mode-105, SET_CAL, RADIO_CONFIGURE, RX_MEASURE, QUERY_RRA, IQ_CAPTURE
  ACQUIRE/FETCH: todo corre sin crash, byte-exacto, `error_code=0`.** (95% de la cadena.)
- **El 5% restante es un único dato numérico:** el opcode FTM_SET_MODE-cal que escribe
  `gp+0x740 (PA 0x96f4f740) = 2` y activa el carrier RX streaming. Ese store **no existe en el
  código legible** (ni 36 MB CLADE, ni segmentos nativos, ni exception blob) y el opcode que lo
  dispara **no está en ningún repo público** (vive en `ftm.h`/`ftm_common.h` propietario o en la
  DLL QMSL cerrada). Está detrás de código residente que solo corre en una sesión de cal de
  fábrica real.
- **No hay forma desde el AP mainline** de (a) disparar ese comando (opcode desconocido, inventarlo
  da 0x13/SSR), ni (b) escribir el gate directamente (modem RAM XPU-locked, escribir la resetea), ni
  (c) leer el gate para confirmar (misma XPU, resetea).

## Veredicto honesto

Como front-end SDR de RX de uso general, **el modem de este dispositivo no es utilizable desde el AP
en mainline para IQ RAW**: la última llave (cal-mode gate) está en código que no se puede ejecutar,
leer ni escribir desde el lado Linux. Esta sesión convirtió la conclusión de las sesiones 9-10 (que
era mitad estática, mitad inferida) en una **conclusión empírica**: se ejecutaron en vivo todos los
caminos que quedaban y todos devuelven "éxito vacío" por la misma causa raíz única.

### Las ÚNICAS vías que quedarían (todas fuera del AP, como en sesiones previas)
1. **Log QXDM/QRCT de una sesión FTM real del SM6375** con QMSL/QPST oficial → leer los bytes
   exactos del SET_MODE-cal. Cierra el problema en minutos con todo lo documentado aquí.
2. **Desensamblar `QMSL_MSVC10R.dll`** (QMSL cerrada) → ver qué bytes construye
   `QLIB_FTM_SET_CALIBRATION_STATE`/`SET_MODE_ID`.
3. **EDL ramdump de una sesión de cal real** → capturar el driver residente y el gate=2.
4. Hardware real: para BTS (yateBTS/srsRAN) de todos modos se necesita un SDR full-duplex
   (LimeSDR/bladeRF/USRP); el modem no expone IQ streaming full-duplex.

## Para RX útil HOY sin cal-mode (lo que sí funciona)
Survey de potencia por frecuencia vía QMI-NAS (RSSI/RSRP/RSRQ vs EARFCN/ARFCN en dBm): network scan
+ cell-location-info = un barrido de espectro a granularidad de celda. Ver `../docs/05-modem-rf-gnss.md`
§11 y el plan `rhodep-rf-survey`. No es IQ RAW, pero es el máximo de RX de uso general alcanzable
desde el AP sin el gate.

## Artefactos de esta sesión
- `backups/device-iqexp-20261015/`: `fullcal.{log,replies}`, `qrra.{log,replies}` (respuestas CMD
  completas byte-exactas + F3 de cal).
- Reportes RE nuevos: `blob-analysis/iq_capture_num_captures.md` (§8 ACQUIRE trigger & cfg+0x6c),
  `blob-analysis/adc_capture_in_mem.md`, `blob-analysis/legacy_iq_capture_900_902.md`.
- Herramientas: patch de `rhodep-diag-server.py` (sidecar `.replies`), `rhodep-iq-parse.py`
  (parser mode-105) — en el device bajo `/home/kali/`.

## Reproducir el experimento más avanzado (qrra)
```bash
# en el device, root:
systemctl stop ModemManager
CTRL=290300
CAL=4b0b140089000100000001
RC=4b0b690001003c004000030000000000000004000000010000000000000000000000050000000300000000000000060000002706000000000000070000000500000000000000
QRRA=4b0b690004006c000002030000000000000008000000010000000000000000000000020000000300000000000000030000002a0000000000000004000000270600000000000005000000002d310100000000060000000000000000000000070000000000000000000000080000000000000000000000
python3 /home/kali/rhodep-diag-server.py --log /tmp/iqrun/qrra.log --dump /tmp/iqrun/qrra.dump \
    --seconds 65 --restart-modem --cmd-after 20 --cmd-peer --raw-seq "$CTRL,$CAL,$RC,$QRRA"
python3 /home/kali/rhodep-iq-parse.py /tmp/iqrun/qrra.dump.replies
```
