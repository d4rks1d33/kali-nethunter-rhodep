# Session 12 — Decodificador Qshrink-4 (QSR4) + decodificación de los F3 GNSS

**Fecha:** 2026-10-06 · SM6375 / Moto G82 5G · MPSS.HI.4.3.4-00494-MANNAR_GEN_PACK-1.24452.133

## TL;DR

Se construyó el **decodificador Qshrink-4 completo** (el paso que faltaba para leer el 51% del
F3 del modem que viene comprimido como frames `0x99`) y se decodificaron las capturas F3 de la
sesión GNSS del 2026-09-06. **Resultado inesperado y de alto valor:**

1. **El QDB es trivial de parsear**: header de 64 bytes + un único stream zlib → 27.3 MB de
   texto ASCII. La sección `<Content>` tiene 157877 líneas `hash:ss_mask:ssid:line:file:format`.
   El `hash` es exactamente el message-id de 32 bits de los frames `0x99`. Resolución id→string
   = lookup en dict. **1058/1058 ids de las capturas resolvieron (100%).**

2. **NO hubo reset del SoC en las capturas existentes.** uptime monótono sin saltos, último
   record DGPK completo (captura detenida limpiamente, no cortada por watchdog), GPS firmware
   corriendo continuo hasta el final.

3. **Los strings de NAV bring-up que la investigación buscaba** ("Failed to enable Nav SDD HW",
   "NavRX ADC cal is failed", "Qlink CSR WDOG timeout", "GNSS RTC not running. Not resetting NAV
   core") **SÍ existen en el QDB** (ids `0x189D8`/`0x18AB7`/`0x18A0D`, ssid 47 = Clock Controller
   GNSS) **pero NUNCA se emitieron** en estas capturas. El NAV core no llegó a fallar por HW aquí.

4. **La cadena de error real decodificada** apunta a **tiempo GPS inválido**, no a un fallo de HW:
```
ss=85 gts.c:2069            FindBestGpsTime: No valid GPS Time          (8x)
ss=49 mgp_pe_api.c:770      PE: Slow Clock Time: INVALID                (7x)
ss=47 mc_msg.c:7742         SLOW CLOCK REQUEST: reason=3, valid=...     (7x)
ss=49 nf_task.c:3736        XTRA clock request: Wk 65535 ...            (3x)  <- week 0xFFFF = sin init
ss=50 tm_xtra_data_handler.c:151  =TM XTRA= GPS time is invalid         (3x)
ss=49 ale_proc.c:3639       ALE: Position-only update, and position invalid, no seed position sent
```
El motor de posición (PE) nunca obtiene una semilla de slow-clock/GPS-time válida
(`Wk 65535` = semana sin inicializar). Sin referencia temporal el correlador no puede alinear
réplicas → **cero measurement reports / cero satélites**. Esto coincide con `gnss-runA.log`:
"SURVIVED 45s, MEASUREMENT REPORTS: 0, SATELLITE INFO: 0".

## Reconciliación con la hipótesis previa (reset del SoC)

**IMPORTANTE — queda pendiente confirmar:** las capturas decodificadas aquí son de un run que
**sobrevivió** (coherente con `gnss-runA.log` = powerMode 3 background, que la investigación
previa ya sabía que NO resetea). El run B que SÍ reseteaba (powerMode 2) puede no estar completo
en estos .bin, o su ventana de reset estar en otra captura. 

Es decir: esta sesión decodificó el run que **sobrevive pero no trackea** (tiempo inválido), NO
el run que resetea. Son dos síntomas posiblemente distintos:
- **Run survive (decodificado):** sin reset, pero cero measurements por `Wk 65535` / tiempo GPS inválido.
- **Run reset (NO decodificado aún):** watchdog-reset del SoC al forzar powerMode no-background
  o inyectar tiempo (ver GNSS-SM6375.md). Los strings NAV de HW quizá SÍ aparezcan ahí.

**Próximo paso lógico:** capturar un run B (que resetea) con F3 habilitado y decodificarlo con
el nuevo `rhodep-qsr4-decode.py`, y/o decodificar `diag-gnss-20260906-124311.bin` (la captura
principal de 7.4 MB que incluía ambos runs) con el decoder, buscando si los strings NAV HW
(0x189D8/0x18AB7/0x18A0D) aparecen en la ventana del reset. OJO: esto implica disparar el
engine en el device (riesgo de reset del SoC), hacerlo vía el flujo de bisect ya documentado.

## Herramientas creadas (en el repo)

- **`userspace/debug-tools/rhodep-qsr4-extract.py`** — deframer HDLC validado. Lee contenedor
  DGPK cross-record, hace unescape 0x7d correcto, descarta CRC16, desenvuelve wrapper 0x98.
  Extrae frames 0x99 con message-id/timestamp/args. Histograma de tipos. Validado: 28506 frames
  0x99 en gnss-f3.bin (99.86% del esperado).
- **`userspace/debug-tools/rhodep-qsr4-decode.py`** — decodificador completo. Carga la tabla
  id→format_string del QDB, resuelve cada frame 0x99, parsea especificadores printf y aplica
  los args binarios (LE: %d/%u/%x=u32, %lld/%llu=u64, %s=NUL-term, %c=byte). También decodifica
  0x79 (texto inline). CLI: acepta un .bin DGPK + `--qdb qdsp6m.qdb` → log decodificado.

## Formato del QDB (verificado byte-exacto)

```
[0x00] Header 64 bytes:
   0x00: 7f 51 44 42 5b           magic \x7f"QDB["
   0x05: <16 bytes GUID>          GUID del build (5beaec3a-e649-5cf9-58f9-e6ac49e23a1c)
   0x12: 3a 1c                    build tag 0x3A1C (= 2 ultimos bytes del GUID)
   0x15: ceros hasta 0x3F
[0x40] stream zlib (78 da) hasta EOF → zlib.decompress → 27271932 bytes de texto ASCII:
   <GUID>/<Version>/<Baseline> ...
   <Content> ...157877 lineas "hash:ss_mask:ssid:line:file:format"... <\Content>   ← F3 (objetivo)
   <MtraceContent> ...                                                            (ids rango alto, NO mezclar)
   <QtraceStrContent> ...
```
Resolver id→string: `zlib.decompress(file[0x40:])`, aislar `<Content>`, por línea
`split(b':', 5)` (maxsplit=5 porque el format-string puede contener `:`), dict `{int(hash): fmt}`.

## Formato del frame 0x99 (Qshrink-4) en el wire

```
byte 0      : 0x99  (DIAG_QSR4_EXT_MSG_TERSE_F)
byte 1      : 0x02  (constante)
bytes 2-3   : (ssid/count)
bytes 4-11  : timestamp u64 LE
bytes 12-15 : message id u32 LE   ← resuelve contra <Content> del QDB
bytes 16-17 : build tag 0x3A1C    ← valida que el QDB corresponde al build
bytes 18+   : args binarios (según los especificadores del format-string)
```
Deframing: contenedor DGPK (`"DGPK"` + u32 len + u32 uptime_ms + payload). Los frames HDLC
CRUZAN límites de records → concatenar payloads antes de deframear. HDLC: acumular hasta 0x7e
sin escapar, luego unescape (0x7d → byte^0x20), descartar CRC16 (2 bytes). Si el frame empieza
con 0x98 (DIAG_MULTI_RADIO_CMD_F), saltar 8 bytes de header multi-SIM.

## Artefactos respaldados (todos en el repo, NO en /tmp)

En `backups/diag-gnss-20260906/`:
- `qdsp6m.qdb` (6042676 B) — la string DB original.
- `qdb_decompressed.txt.gz` — los 27.3 MB de texto descomprimido (gzip).
- `qdb_content_id2fmt.json` (9.9 MB) — tabla {id: format_string}, 157877 entradas.
- `qdb_full.json` (22.9 MB) — tabla {id: {ss_mask, ssid, line, file, fmt}}.
- `qdb_resolve.py` — CLI standalone para resolver ids contra el qdb.
- `qsr4_ids.json` — los 1058 ids de las capturas con count/timestamp/args.
- `decoded-gnss-f3.txt` (5.9 MB, 40612 líneas) — gnss-f3.bin decodificado.
- `decoded-diag-f3b.txt` (3.4 MB, 22644 líneas) — diag-f3b.bin decodificado.
- capturas originales: `gnss-f3.bin`, `diag-f3b.bin`, `diag-gnss-20260906-124311.bin`,
  `diag-probe.bin`, `gnss-runA.log`.

En `backups/device-iqexp-20261005/`: dumps/logs de la sesión RF/FACT (sesión 10/11) traídos
del `/tmp/iqexp` del device (exp14-18, fac1-4, ctrl, nvread) para que no se pierdan al apagar.

## Estado del subsistema GPS (recordatorio)

- **GPS por red: FUNCIONA** (paquete `rhodep-gnss`, WiFi 22m / celda 250m, NMEA → gpsd/geoclue).
- **Fix por satélite: NO funciona.** Dos síntomas ahora distinguidos:
  1. Tiempo GPS inválido (`Wk 65535`) → cero measurements (decodificado esta sesión).
  2. Watchdog-reset del SoC al forzar el engine (pendiente de decodificar su ventana).
- La AP ya fue exonerada (instrumentación v100). El bug/recurso vive en MPSS/secure world.

## Continuación (Session 13, 2026-10-06 — misma fecha, segunda tanda)

Se decodificó la captura principal y se cerró el análisis de las existentes:

1. **`diag-gnss-20260906-124311.bin` resultó ser modo LOG (0x10), sin F3.** Es un run que
   sobrevive (uptime monótono, fin limpio). NO contiene el run B que resetea, ni ningún frame
   0x99. Los strings NAV de HW (0x189D8/0x18AB7/0x18A0D) NO aparecen aquí (y solo se emiten como
   F3, nunca en modo log). Log decodificado: `decoded-gnss-main.txt`.

2. **Los strings de "reset" de las capturas F3 eran una atribución errónea de ssid:** son
   WiFi/WCSS/RF (ssid 4534/4515/14), bring-up normal, NO el NAV core. Ninguna captura existente
   tiene un ERR_FATAL/ASSERT/CC-reset: las tres son runs que sobreviven.

3. **Red/XTRA descartados como causa:** verificado en vivo — internet OK, DNS OK, NTP sincronizado,
   XTRA descarga bien, y `rhodep-xtra` inyectó tiempo+posición con QMI status=0. El modem igual
   reporta `Wk 65535`. El atasco está DENTRO del modem, aguas abajo de la inyección.

4. **CAUSA RAÍZ UNIFICADA (alta confianza): el RTC de hardware del NAV core no está corriendo
   (`cc_IsGnssRTCRunning() == 0`).** Explica los dos síntomas con un solo mecanismo:
   - Sin RTC corriendo, el modem DESCARTA el tiempo inyectado (lo acepta por QMI pero lo rechaza
     en `mc_clock.c:6685` "received while GNSS RTC is not running. Not accepting time") → slow-clock
     INVALID → `Wk 65535` → MGP OFF → cero satélites. (= el run que sobrevive)
   - Con tiempo inyectado, el engine intenta encender el HW del NAV → bring-up/recovery → muere en
     ~100ms. (= el run que resetea)
   - El MISMO predicado (`GNSS RTC not running`) guarda tanto el descarte de tiempo como el reset.
   - Detalle completo en `docs/interconnect-sm6375-wip/NAV-CORE-RESET.md` (addendum Session 12).

### Próximo paso (sin riesgo de reset)
Capturar un cold-start con la **máscara F3 de ssid 47 (Clock Controller GNSS) a nivel debug
(ss_mask 8 y 128)** habilitada, y leer renderizados:
- `mc_clock.c:1346` → `u_GpsRtcRunning %u cc_IsGnssRTCRunning() %u` (2º %u=0 confirma RTC OFF)
- `mc_clock.c:6685` → confirma el gate de descarte del tiempo
- `navbb_qft.c:221` → `ECO Mode %u ... RTC 0x%x STMR 0x%x ... GDSC 0x%x` (registros crudos)
- `cc_hwconfig.c:641` → `Failed to enable Nav SDD HW. Clock Status: ...`
- `cc_slicer.c:9915/9922`, `cc_task.c:4001/4012` → qué rama de recovery se toma
Esto requiere ampliar la máscara F3 en `rhodep-diag-server.py` para incluir ssid 47 a nivel
debug (las capturas previas no lo tenían). Es una captura pasiva (no fuerza el reset), solo
observa un cold-start normal.
