# DIAG LOG PACKETS con IQ en modo NORMAL — ¿hay un path IQ que NO dependa de cal-mode?

**Target:** Qualcomm SM6375 / Moto G82 5G (rhodep) · MPSS.HI.4.3.4-00494-MANNAR_GEN_PACK-1.24452.133 · Hexagon v66
**Método:** 100% estático. Blobs: `clade_dec_36m.bin` (paged pool, VA 0xd8000000, 54MB), `clade_exc_high.bin` (VA 0xd0000000), segmentos nativos `modem.bNN` (rd.py), rodata `seg27_dec.bin`/`modem.b21`, disasm `_dis_b13.txt` (diag core nativo).
**Herramientas:** `dis.sh`, `rd.py`, disasm completo del 36m (`/tmp/_dis36_full.txt`, 9.2M líneas), scans de punteros/immext propios.
**Leyenda:** **FACT** = byte/instrucción/string verificado con VA · **INFERENCE** = deducción con base dura · **UNKNOWN** = sólo runtime.

---

## 0. VEREDICTO EJECUTIVO (directo)

**NO existe, en ESTE firmware, un DIAG log packet que emita muestras IQ raw del receptor por el simple hecho de habilitar su log mask en modo normal.** El mecanismo DIAG de log packets está 100% compilado y funciona (SET_LOG_MASK 0x73 compiled-in, gate de emisión = SÓLO la log mask, sin cal-mode, drena por el mismo canal DATA/QRTR que los F3). Pero **los log codes que existen NO llevan IQ raw**: son métricas, reports de PHY/ML1, y F3 de debug. Las **muestras IQ** en este chip se capturan por facilidades **command-triggered** que:
- (a) el path **FTM IQ_CAPTURE** — ya mapeado, **bloqueado por cal-mode** (gate 0xcbf4f740==2 + carrier ptr 0xca79c494);
- (b) el **async sample capture / sample record** (LTE LL1 / NR ML1) — requiere un **comando/MSGR request explícito** a la FW con timing; su buffer es DDR/LMEM interno, no un log packet; y
- (c) el **NB_EFS sample dump** — se dispara por umbrales de medición durante search NORMAL, pero **vuelca a ficheros EFS** (`iq.bin`, `temp_rx_samples_0.bin`) y necesita un **"MEAS_ONLY debug crash"** para flush → NO es un log stream vivo, y EFS-over-DIAG no está registrado como subsys en este PD.

**Conclusión práctica:** los DIAG log packets NO te dan un atajo al IQ que evite el cal-mode. El único path IQ real sigue siendo el FTM IQ_CAPTURE (bloqueado) o extracción por EFS/ramdump (intrusivo). **Resultado válido y honesto: este vector no rinde IQ raw.**

---

## 1. El mecanismo DIAG de log packets — SÍ está compilado y es mask-only (FACT)

### 1.1 SET_LOG_MASK (cmd 0x73 / DIAG_LOG_CONFIG_F) — compiled-in, byte-exacto (FACT)

Handler inline **`0xc0d3626c`** (dispatch: master `0xc0d55e68` `cmpb.eq(r22,#0x73)` → `jump 0xc0d3626c`). NO es stub, NO es peek/poke compiled-out. Parseo verificado instrucción a instrucción (`_dis_b13.txt`):

```
c0d3626c: r2=memub(r17+6); r3=memub(r17+7)
c0d36274: r4=memub(r17+5); r5=memub(r17+4)
c0d3627c: r5 = pkt[4] | pkt[5]<<8 | (pkt[6]|pkt[7]<<8)<<16   ; = u32 operation @ pkt[4]
c0d36284: if (r5 != 3) jump reject          ; <<< operation==3 = SET_MASK  (FACT)
c0d36290: r3 = u32 @ pkt[8]                  ; = equip_id
c0d362a4: if (cmp.gtu(r3,#0xc)) jump 0xc0d36308  ; <<< equip_id > 0xC -> REJECT  (FACT)
c0d362b0: r7 = memuh(0xc09b5510 + equip*2)   ; per-equip byte-offset en el mask array (runtime)
c0d362bc: r6 = u32 @ pkt[0xc]                ; = last_item / num_codes solicitado
c0d362d8: r5 = memuh(0xc091cda8 + equip*2)   ; per-equip max_code (tabla estática)
c0d362dc: r8 = minu(r6, r5)                  ; clamp last_item <= max_code
c0d362d0: r3 = pkt + 0x10                    ; puntero al bitmap de mask entrante
c0d362e8: r4 = memw(0xcb932088)              ; base del array de masks (global)
c0d362ec: r4 = lsr(r8,#3)                    ; nº de BYTES de mask a copiar (last_item/8)
...copia el bitmap a 0xcb932084[equip]...
```

**Layout byte-a-byte del comando SET_LOG_MASK (FACT):**
```
offset  campo
+0x00   0x73                       (DIAG_LOG_CONFIG_F)
+0x01   0x00 0x00 0x00             (pad)
+0x04   operation (u32 LE) = 3     (LOG_CONFIG_SET_MASK_OP)
+0x08   equip_id  (u32 LE)         (0..0xC ; ver §1.3)
+0x0c   last_item (u32 LE)         (highest log-code index a togglear; el modem clampa a max_code)
+0x10   mask[ ceil(last_item/8) ]  (bitmap, bit k = code (equip<<12)|k habilitado)
```

### 1.2 El GATE de emisión = SÓLO la log mask; NO hay cal-mode ni carrier ptr (FACT)

`log_alloc @0xc0d4f8e8` → `log_status @0xc0d4fa24`:
```
c0d4fa30: r17 = lsr(log_code,#0xc)          ; equip_id = code>>12
c0d4fa3c: r18 = extractu(log_code,#0xc,#0)  ; code_index = code & 0xFFF
c0d4fa88(helper): r0 = memw(r3<<2 + 0xcb932084)   ; lee la palabra de mask
c0d4fab0: r0 = !cmp.eq(r0,#0)                       ; return TRUE si el bit está set
```
`log_alloc` además exige `memb(0xc091c5e0)==1` (diag-log inicializado). **No hay ninguna referencia a `0xcbf4f740` (cal-mode gate) ni a `0xca79c494` (carrier ptr) en TODO el diag core seg13** (grep negativo). → **Un log packet se emite si y sólo si su bit de mask está activo. No SSR-ea por cal-mode.** **FACT.**

### 1.3 Mapa de equip_id → tech y rango de log codes (FACT)

Tabla estática de max_code `@0xc091cda8` (leída con rd.py):

| equip_id | tech (convención QC) | max_code | rango de log codes | ¿SET_MASK 0x73? |
|---|---|---|---|---|
| 0x1 | 1X / legacy (CDMA1x) | 0xD86 | 0x1000..0x1D86 | SÍ (≤0xC) |
| 0x4 | WCDMA | 0x910 | 0x4000..0x4910 | SÍ |
| 0x5 | GSM | 0xE20 | 0x5000..0x5E20 | SÍ |
| 0x7 | (UMTS/otros) | 0xB60 | 0x7000..0x7B60 | SÍ |
| 0xA | HDR/1xEV | 0x38A | 0xA000..0xA38A | SÍ |
| **0xB** | **LTE** | 0xA01 | **0xB000..0xBA01** | **SÍ** |
| **0xD** | **NR5G** | 0x1FF | **0xD000..0xD1FF** | **NO** (0xD > 0xC → rechazado; requiere subsys 0x12 stream) |

**Nota importante (FACT):** el gate `equip>0xC` del handler 0x73 **rechaza NR5G (equip 0xD)**. Para habilitar log mask de NR hay que usar el path **subsys 0x12 (DIAG_SERV) por stream** (map_diag_core.md §3, handlers 0xc0d65620/0xc0d656f4) o el diag moderno del AP. LTE (0xB) sí se habilita con 0x73.

### 1.4 Transporte de logs = mismo canal DATA/QRTR que los F3 (FACT — responde P5)

`log_commit @0xc0d4fc68`: toma el buffer alloc'd, retrocede 8B al header de log, chequea el array de canales `0xc9508b60/0xc9508b80` y drena por `0xc0d5665c` → `diagbuf_send_pkt @0xc0d562dc` → canal **DATA (io_type==2)**. **Los logs NO cruzan el gate `diagpkt_rsp_send @0xc0d36c44`** (feature-mask+diagID) — igual que los F3 (diag_transport_full.md §4.3). → **Van por el mismo canal QRTR/DATA que ya tenés funcionando; con sólo habilitar la mask drenan.** **FACT.**

**Comando exacto para habilitar TODA la mask LTE (equip 0xB, codes 0xB000..0xBA01):**
```
73 00 00 00   03 00 00 00   0B 00 00 00   01 0A 00 00   FF FF ... FF
└cmd┘ └pad┘   └─op=SET──┘   └─equip=B──┘  └last=0xA01┘   └── 0x141 bytes de 0xFF ──┘
```
(`last_item=0xA01` → mask de `ceil(0xA01/8)=0x141`=321 bytes; poné todo 0xFF para habilitar todos los codes LTE, o setea sólo los bits que quieras.) El modem clampa `last_item` a `max_code` automáticamente.

---

## 2. ¿Qué log codes existen y llevan IQ/samples? — la búsqueda (FACT/INFERENCE)

### 2.1 El ÚNICO "log packet" con IQ explícito es CDMA1x — y no aplica (FACT)

`LOG_RX_IQ_SAMPLES` (strings @0xc3fa47fa/0xc3fa4855):
```
"Received LOG_RX_IQ_SAMPLES message for ant (%d) cdma1xMemplLogConfig.enabled=%d"
"LOG_RX_IQ_SAMPLES sample copy done - start_rtc:%x end_rtc:%x capture_valid:%x"
```
- Es del **memory-pool logging de CDMA1x** (`cdma1xMemplLogConfig`), equip 0x1.
- Requiere `cdma1xMemplLogConfig.enabled` (config, no sólo la mask) **y** que el device esté operando en 1X.
- **rhodep (Moto G82) es LTE/NR; NO corre CDMA1x** en operación normal → este log jamás se emite. **INFERENCE fuerte (HW/carrier) + FACT (es 1x-only).**
- El código emisor **no está referenciado en el paged image** (ni por immext ni por puntero) → probablemente ni siquiera paginado en un modem no-1x.

### 2.2 LTE/NR ML1 "IQ log" — es capture command-triggered, NO un log pasivo (FACT)

Archivos y state machines presentes (strings b21/seg27, refs por tabla de asserts en seg27):
```
nr5g_ml1_iq_log.c · nr5g_ml1_iq_capture.c · nr5g_ml1_iq_capture_stm.c
lte_LL1_iq_samp_capture.c · lte_LL1_async_samp_capture.c · lte_LL1_sample_record_database.c
NR5G_ML1_IQ_CAPTURE_STM / _IQ_LOG_REQ / _IQ_LOG_CNF / _LOGGING / _START_REQ / _STOP_REQ / _ABORT_REQ
LTE_LL1_ASYNC_SAMP_CAPTURE_CNF · LTE_ML1_MGR_FW_SAMPLE_REC_DONE_IND · LTE_LL1_SYS_SAMPLE_REC_DONE_IND
```
Evidencia dura de que es **command/MSGR-triggered** y va a FW, no un log que "sale solo":
```
"IQ CAPTURE: Failed to send message to FW:TRUE == send_fw_result"          (ce779c08)
"IQ Capture(%d)Failed to schedule IQ Capture Object" (nr5g_ml1_schdlr...)  (ce7798e8)
"Assert ... p_sample_capture_buffer failed: ... Sample capture buffer NULL" (ce779f80)
"ASYNC_SAMP_CAPT: New async samp capture request when current one is under process" (ce5aa1ea)
"Assertion (sampl_rec_params->start_rec_delay_ms == ...gap_rxagc_duration...)" (ce59d5d8)
```
- El "IQ LOG" del ML1 es la **fase de logging del objeto IQ_CAPTURE** (mismo mecanismo que el FTM IQ_CAPTURE analizado en map_iq_capture.md): hay que **armar el objeto, schedulearlo, mandar el comando a FW**, FW llena `p_sample_capture_buffer` (DDR/LMEM interno), y recién ahí se puede loggear/fetchear.
- NO se dispara por habilitar una log mask. Requiere el **request explícito** (por FTM o por un comando ML1). **FACT.**
- El emisor no está referenciado en el paged 36m image (código en el pool FW/LL1, fuera de esta MBN). **FACT (ausencia de refs) / INFERENCE (vive en FW LL1).**

### 2.3 REFLOG (rflm_dtr_reflog) — captura de samples del DTR, en la FW RFLM (FACT)

```
rflm_dtr_reflog.cpp · sdr735v2_dtr_reflog.cpp
reflog_commit_sample_capture / _start_stop_cfg / _pktif_cfg / _reflog_mode_cfg
"private_rflm_reflog_debug_compl_online: Reflog capture done"
```
- Es la facilidad **REFLOG del RFLM/DTR** (Digital Tuner Rx) para capturar samples de la cadena RF.
- Corre en la **FW RFLM** (`rflm_dtr_reflog.cpp`), **fuera de esta imagen de modem** (refs sólo en b21/seg27 rodata, no en código paginado). Se controla por su propio `reflog_mode_cfg` (config), no por DIAG log mask.
- **UNKNOWN** si es alcanzable sin cal desde el AP; su control (`reflog_commit_*`) no está expuesto como comando DIAG registrado en este PD. **INFERENCE (no AP-reachable por DIAG estándar).**

### 2.4 NB_EFS sample dump — SÍ corre en modo NORMAL, pero vuelca a EFS, no a un log (FACT)

Éste es el mecanismo más cercano a "IQ intermitente en modo normal", y merece detalle:
```
"NB_EFS: SRCH type %d | No cells found on ARFCN %d for %d iter, Sample dump %d ms"
"NB_EFS: NB_%d Max Meas result > Threshold for Iter %d!, %d ms sample dump"
"NB_EFS: MEAS_ONLY debug crash, NB sample dump enabled for %d ms!"
"NB_EFS: Intrusive & non-intrusive IQ capture collision on nb_id %d"
"NB_EFS: QxDM EFS trigger is not expected!"
config item EFS: "nbee_meas_enable"
```
- **NB_EFS = NarrowBand-EE, dump de samples disparado por UMBRALES de medición durante el SEARCH/MEAS NORMAL** (no cal-mode). Existe modo **non-intrusive** (corre junto a la operación normal).
- PERO el destino es **ficheros EFS** (`iq_%s.bin`, `iq.bin`, `temp_rx_samples_0.bin`, `swr_samples.bin` @b21) y para flushearlos necesita una **"MEAS_ONLY debug crash"** (o lectura EFS posterior). **NO es un DIAG log stream vivo.**
- Para sacar esos ficheros del AP harías falta **EFS-over-DIAG**, que en este PD **NO está registrado como subsys DIAG** (map_diag_core.md §6: subsys 20/EFS ausente; `LFW_MAX_EFS_DIAG_CMD_PAYLOAD_BYTE` existe pero es del Root-PD/APPS, no de este canal). O ramdump. → **NO AP-reachable limpio por el canal DIAG que tenés.** **FACT.**

### 2.5 Falsos positivos descartados (FACT)

- `IQ_SAMPLES`, `IQ_SAMPLE_DEBUG_INFO_0/1/2`, `IQ_CAPTURE_TYPE`, `FETCH_IQ` @0xc414bxxx = **field names del TLV FTM RFTEST IQ_CAPTURE** (path bloqueado por cal-mode), no log packets.
- `mot_iqi` (Motorola "IQI") = feature que **SNIFEA diag logs/events** existentes (`mot_iqi_booster_metrics_sniff_diag_logs`), no produce IQ raw. "IQI" aquí = métricas Motorola, no I/Q.
- `*_iq_capture` (dlm2_ta/srch/sleep/ulrm) = **claves de config NV/EFS** (agrupadas con `srch_debug`, `sleep_slpc_mode`), enables del facility interno, no log codes.

---

## 3. RESPUESTAS A TUS 5 PREGUNTAS

**P1 — ¿Qué DIAG log codes emiten IQ/samples RX? Log code (hex) + layout.**
- **Ninguno emite IQ raw por mask-enable en este target.** El único log packet con IQ explícito es **`LOG_RX_IQ_SAMPLES`** (equip 0x1 = CDMA1x, code 0x1xxx), gated por `cdma1xMemplLogConfig.enabled` + operación 1X; **rhodep no corre 1X** → no aplica. Layout (por el string): `{ start_rtc(u32), end_rtc(u32), capture_valid(u8), ant(u8), samples[...] }` — **UNKNOWN el layout exacto** (emisor no paginado). Los "IQ log" LTE/NR son fases de un capture command-triggered (§2.2), no log codes pasivos. **FACT.**

**P2 — ¿Se emiten en modo normal con sólo la mask, o requieren cal/FTM?**
- El **mecanismo** de log (mask → log_status → commit) es modo-normal puro, sin cal (§1.2). PERO **los datos IQ no existen como log**: para tenerlos hay que **disparar un capture** (FTM = cal-mode, o async-samp-capture = comando ML1/MSGR con timing, o NB_EFS = umbral+EFS+crash). **Ningún log de IQ "sale solo" al habilitar la mask.** **FACT/INFERENCE.**

**P3 — SET_LOG_MASK (0x73): ¿compilado en ESTE firmware? Comando byte-a-byte.**
- **SÍ, compiled-in y byte-exacto** (§1.1, handler 0xc0d3626c, NO es stub, NO es peek/poke). Comando para LTE-all:
  `73 00 00 00 | 03 00 00 00 | 0B 00 00 00 | 01 0A 00 00 | FF×0x141`. **FACT.**
- Caveat: NR5G (equip 0xD) lo **rechaza** el 0x73 (gate >0xC); usar subsys 0x12 stream. **FACT.**

**P4 — ¿Logs de ML1/PHY con IQ o sample dumps habilitables para IQ intermitente?**
- **Los logs ML1/PHY que SÍ existen y se habilitan con la mask son métricas/reports (RSRP, AGC, SINR, srch results, dlm2, schdlr…), NO IQ raw.** El único "sample dump" que corre en normal es **NB_EFS** (§2.4), pero vuelca a **EFS + crash**, no a un log vivo, y EFS-over-DIAG no está en este PD → **no te da IQ intermitente por el canal DIAG.** **FACT.**

**P5 — Transporte: ¿mismo canal DIAG (QRTR, userspace client)?**
- **SÍ.** Logs drenan por `log_commit @0xc0d4fc68 → diagbuf_send_pkt @0xc0d562dc → canal DATA (io_type==2, QRTR)`, **sin** cruzar el gate feature+diagID de las respuestas. Mismo canal que ya usás para F3. **FACT.**

---

## 4. ¿AP-reachable sin cal-mode? — SÍ/NO

| Path | ¿corre en normal (sin cal)? | ¿lleva IQ raw? | ¿AP-reachable por DIAG? | Veredicto |
|---|---|---|---|---|
| DIAG log mask (0x73) + log packets | **SÍ** (mask-only, sin cal) | **NO** (métricas/reports) | SÍ (canal DATA) | inútil para IQ |
| `LOG_RX_IQ_SAMPLES` (CDMA1x) | sólo en 1X (rhodep no) | sí | sí (si 1X) | **N/A** (no 1X) |
| ML1/LL1 async_samp_capture / IQ_CAPTURE_STM | requiere comando+FW | sí (buffer DDR) | sólo por FTM (cal) o comando ML1 | mismo blocker cal |
| FTM RFTEST IQ_CAPTURE | **NO** (cal-mode 0xcbf4f740==2) | sí | sí pero SSR-ea | **BLOQUEADO** |
| NB_EFS sample dump | **SÍ** (umbral, normal) | sí (a EFS) | **NO** (EFS+crash, EFS-over-DIAG ausente) | no por DIAG |
| REFLOG (rflm_dtr) | UNKNOWN | sí | **NO** (no expuesto en DIAG) | no por DIAG |

**Respuesta neta: NO hay un path IQ por DIAG log packets que sea AP-reachable sin cal-mode.**

---

## 5. ¿Depende del gate cal-mode / carrier ptr? (lo que pediste explícito)

- El **path de log packets en sí NO depende** de `0xcbf4f740==2` ni de `0xca79c494` — el diag core (seg13) no los referencia (grep negativo). Si hubiera un log de IQ, drenaría sin cal. **FACT.**
- **Pero los DATOS IQ sí dependen** del capture engine, que en el path usable (FTM IQ_CAPTURE) **sí** requiere `0xcbf4f740==2` + `0xca79c494` poblado (ya verificado en rf_cal_mode_gates.md / map_ftm_framework.md → SSR). El async-samp-capture del ML1 requiere que el RxFE/carrier esté armado (equivalente al carrier ptr). → **el blocker cal-mode/carrier NO se evita por la vía de logs.** **FACT/INFERENCE.**

---

## 6. Honestidad sobre la evidencia negativa (límite del blob)

- El emisor de los logs de IQ (CDMA1x mempool, ML1 IQ log, REFLOG, NB_EFS) **NO está referenciado en el paged image `clade_dec_36m.bin`** (verificado: sin refs por immext ni por puntero directo). Los **strings y las tablas de asserts SÍ están** (b21/seg27), lo que prueba que el código existe en el árbol, pero su cuerpo vive en el **pool FW/LL1/RFLM** (Lower-Layer-1 / RF firmware que corre en otro contexto) o en páginas dlpager no incluidas en este snapshot. Por eso **no puedo darte el log code numérico exacto** de un IQ log ML1 con VA de la instrucción de `log_alloc(code,len)` — no está en la ventana desensamblable.
- Lo que **SÍ es FACT duro**: (1) el mecanismo DIAG log (0x73 + log_status + commit + drain) está compilado y es mask-only sin cal; (2) el equip map y el clamp; (3) que **ningún log packet de IQ raw se emite por mask-enable** en operación normal de este device (el único con IQ explícito es 1X, que no aplica); (4) que las facilidades de sample capture son command-triggered o EFS+crash.

---

## 7. VEREDICTO FINAL

**NO hay un path IQ vía DIAG log packets en este firmware.** El sistema de log packets funciona perfecto en modo normal sin tocar cal-mode y drena por tu canal DIAG/QRTR actual — pero **los log codes existentes no transportan muestras IQ raw**. Las muestras IQ sólo salen de un *capture engine* que hay que **disparar** (FTM IQ_CAPTURE = cal-mode bloqueado; async-samp-capture ML1 = comando+FW+carrier armado; NB_EFS = EFS+crash). Ninguna de esas vías se abre con `SET_LOG_MASK`.

**Recomendación:** este vector queda descartado como atajo. Los caminos que siguen vivos son los ya identificados: (a) desbloquear el cal-mode (gate 0xcbf4f740==2, cuyo trigger está en código residente fuera de la MBN), o (b) extracción por EFS/QDL-ramdump (intrusiva). Si querés perseguir NB_EFS, el ángulo sería habilitar `nbee_meas_enable` por EFS y leer los `iq*.bin` — pero eso necesita EFS-over-DIAG (ausente en este PD) o acceso al FS del modem, no el canal de logs.

---

## 8. Reproducir
```bash
# SET_LOG_MASK handler (0x73) - compiled-in, byte-exacto
grep -A80 "^c0d3626c:" /tmp/modemre/_dis_b13.txt
# log emission gate (mask-only, sin cal)
grep -A18 "^c0d4faa8:" /tmp/modemre/_dis_b13.txt      # log_status core
grep -A2  "^c0d4f8e8:" /tmp/modemre/_dis_b13.txt      # log_alloc start
grep -A16 "^c0d4fc68:" /tmp/modemre/_dis_b13.txt      # log_commit -> drain
# equip map
python3 -c "import sys;sys.path.insert(0,'/tmp/modemre');from rd import readbytes;import struct;b,_=readbytes(0xc091cda8,32);print([hex(x) for x in struct.unpack('<16H',b)])"
# IQ/log strings (todas las evidencias)
strings /tmp/modemre/modem_full.elf | grep -iE "LOG_RX_IQ_SAMPLES|NB_EFS|reflog|async_samp|iq_log|sample_rec"
# confirmar ausencia de cal-gate en diag core
grep -i "cbf4f740\|ca79c494" /tmp/modemre/_dis_b13.txt   # (vacío = sin gate cal)
```
