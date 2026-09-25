# MASTER MODEM MAP — Baseband Qualcomm SM6375 (Moto G82 5G)

**Build:** `MPSS.HI.4.3.4-00494-MANNAR_GEN_PACK-1.24452.133`
**SoC:** SM6375 · Hexagon/QDSP6 v66 (`e_machine=0xa4`) · board = **rhodep** (Motorola)
**Imagen:** `modem.mdt` (ELF32) + `modem.b00..b34` (MBN split). e_entry(paddr)=`0x8b800000`, 36 program headers.
**Objetivo:** puerto a Linux mainline — (a) captura **IQ raw RX** ahora, (b) **TX** a futuro.

**Este documento consolida 25+ reportes de RE estático** en un mapa único. Sustituye la lectura
individual. Cuando dos reportes se contradijeron, se resuelve aquí marcando cuál ganó y por qué.

**Leyenda:** **FACT** = leído byte/instrucción con VA en esta imagen · **INFERENCE** = deducción con
base dura · **UNKNOWN** = sólo resoluble en vivo (RAM/RFC/NV o código ausente del MBN).

**Herramientas de referencia (en `/tmp/modemre/`):** `dis.sh <va> <len>` / `dis36.sh` (llvm-objdump
hexagon v66), `rd.py <va> <n>` (rodata por VA), `find_refs.py`, `reach.py`, `build_fns.py`/`_fns.pkl`,
`clade_extractor_sm6375` (+ `libclade.so`), `mkelf.py`.

**Blobs de trabajo (VA base):**
- `clade_dec_full.bin` — **0xd8000000**, 10 MB, CLADE-descomp CORRECTO (dispatch FTM/DIAG/ML1).
- `clade_ok.bin` / `clade_dec_36m.bin` — **0xd8000000..~0xda440000**, 36 MB CORRECTO (superset del anterior).
- `clade_exc_high.bin` — **0xd0000000..0xd0703000**, 7.3 MB (`.clade.exception_high`).
- `seg27_dec.bin` — **0xce480000**, 6.3 MB, ZLIB-descomp = **rodata RF** (todos los strings RFLTE/RFLM/ML1).
- `modem.b21` — **0xc3553000** (rodata: tablas DIAG/FTM/TLV, enums, nombres UMID).
- `modem.b23` — **0xc8b6a000** (DATA init: master tables DIAG, g-tablas de params).
- ⚠️ `clade_dec.bin` (54 MB) está **CORRUPTO/desalineado** — NO usar (ver §2.4).

---

## 1. RESUMEN EJECUTIVO

**Qué es.** Un baseband Qualcomm SM6375 (familia MANNAR). El firmware del modem es Hexagon v66. El
código está mayormente **comprimido con CLADE** (>80%); sólo el arranque + DIAG core + dlpager + TCM
son planos. El transceiver WWAN sub-6 es un **SDR735** (más SMR526v2 para mmWave, QET6200 para ET/TX).

**Qué logramos entender (FACT).** De punta a punta:
- **DIAG transport** completo (handshake, canales QRTR, routing de respuestas, gate de rsp, SSIDs F3).
- **Framework FTM/RFTEST** completo: jerarquía de dispatch, layout del wire, las DOS tablas de
  comandos (sub_command wrapper vs command_id runtime), todos los unpackers y sus field-tables.
- **IQ_CAPTURE** a nivel de contrato: field-table de 51 slots, las 3 fases (GETCFG/ACQUIRE/FETCH),
  formato de muestra (8/16-bit IQIQ), sample-rate (base WB 1.92 MHz), entrega por memshare (REPACK).
- **La causa raíz del SSR** (crash): cadena causal exacta desde el getter NULL a `err_fatal`.
- **Los dos gates de HW** que hay que abrir (`gp+0x740==2`, `gp+0x7000!=0`) y su VA absoluta.
- **TX** completo a nivel de contrato (TX_CONTROL/TX_MEASURE, tx_playback IQ, DPD/ET, límites SAR).
- **RF chain, MSGR/IPC, operating modes, NV/cal, MCPM clocks, device wakeup, RFC** — todos mapeados
  a nivel de nombres de función / assert / estructura (FACT-de-string).

**Estado del objetivo IQ RX.**
- **FUNCIONA (entendido y accionable):** el path DIAG→FTM→RFTEST IQ_CAPTURE, el wire byte-exact, los
  TLVs, la secuencia de bring-up A→B→C→(IQ), la entrega memshare, el formato de muestra.
- **FALTA (bloqueadores):** tres puntos ciegos que **exigen equipo en vivo**:
  1. El **sub_command/command_id numérico exacto** de FTM_SET_MODE cal, RADIO_CONFIG, IQ_CAPTURE
     (binding table-driven en RAM `@0xca79a850`/`@0xcaad9d00`; se cierra en vivo con COMMAND_CAPABILITY).
  2. El **writer del gate `gp+0x740=2`** (vive en código RF-cal ausente del MBN).
  3. El **código RFLTE/RFLM/SDR735** (cuerpos ausentes del MBN; sólo strings; requiere dump de RAM).
- **Por qué falta:** el driver RF residente (RFLTE ML1 + SDR735 + threads PHY) **no está en ningún
  program header** — reside en RAM de código respaldada por DDR físico `0x2a3xxxxx`, poblada en boot,
  fuera del ELF. Y los enteros de registro (command_id, UMID, gates) se asignan en runtime.

**Conclusión operativa:** el análisis estático llega hasta el borde exacto donde empieza lo que sólo
existe en vivo. El siguiente paso obligatorio es un **dump de RAM en vivo** + un **barrido DIAG en
vivo** con el teléfono en FTM. Todo lo demás (wire, TLVs, secuencia, entrega) está resuelto.

---

## 2. ARQUITECTURA DE MEMORIA / SEGMENTOS

### 2.1 Tabla de segmentos (36 program headers) — FACT
`p_vaddr ≠ p_paddr`: **todo el RE se hace contra p_vaddr** (la VA de runtime). p_paddr es sólo el
layout de carga contiguo desde 0x8b800000.

| idx | bNN | p_vaddr | filesz | memsz | perm | contenido |
|----:|-----|---------|--------|-------|------|-----------|
| 2 | b02 | 0xc0800000 | 0x2930 | 0x3000 | R-E | código arranque/vector |
| 4,5,8,9 | | 0xc09xxxxx | | | RWE | código+datos RW planos |
| 10 | b10 | 0xc0a80000 | 0x29ab14 | 0x29b000 | R-E | **código grande (parte DIAG), q6zip** |
| 12 | b12 | 0xc0d30000 | 0x6000 | 0x6000 | R-E | código |
| 13 | b13 | 0xc0d36000 | 0x31f1b0 | 0x320000 | R-E | **DIAG core plano (-O0), 3.2MB** |
| 14–20 | | 0xc1xxxxxx | | | RW- | datos RW init |
| 20 | b20 | 0xc1a50000 | 0x96e988 | **0x1b03000** | RW- | datos RW + .bss grande (28MB) |
| 21 | b21 | 0xc3553000 | 0xd315f4 | **0x5616000** | R-- | **RODATA: tablas DIAG/FTM/TLV, F3 fmt** |
| 23 | b23 | 0xc8b6a000 | 0x5cdd97 | 0x5ce000 | RW- | **DATA init: master tables, g-tablas** |
| 24 | b24 | 0xc9138000 | **0** | **0x2e17000** | RW- | **.bss RAM RUNTIME (48MB) — TODOS los globals FTM** |
| 25 | b25 | 0xcbf4f000 | 0x19900 | 0x1a000 | RW- | **.sdata/.sbss (gp base) — los gates RF** |
| 26 | b26 | 0xcc000000 | 0x244a040 | — | RW- | **`.clade.comp` + `.clade.dict` (código CLADE, 38MB)** |
| 27 | b27 | 0xce480000 | 0x11922f | — | R-- | **RF ML1 LTE (ZLIB) → seg27_dec.bin, rodata RF** |
| 30–32 | | 0x1e4xxxxx | | | R-E/RW- | código+datos TCM/island |
| 35 | — | 0xa44fd000 | **0** | 0x208c000 | R-- | .bss (34MB) |

(Tabla completa en `map_segments.md §1`.)

### 2.2 VA windows y compresión — FACT
| Ventana VA | backing | comprimido | contenido |
|-----------|---------|-----------|-----------|
| 0xc0xxxxxx | b02/b04/b05/b08/b09/b10/b12/b13 | NO (plano) | DIAG core, dlpager, arranque |
| 0xc3xxxxxx | b21 (R--) | NO | **RODATA: dispatch FTM @0xc37bd1e8, ftm_cmd tbl @0xc37bc828, jump-tbls RFTEST @0xc37c649c, F3 fmt** |
| 0xc8b/0xc906 | b23 (RW-) | NO | master DIAG @0xc8dc3b54, g-tablas de nombres TLV @0xc906xxxx |
| **0xca6/0xca7/0xca9/0xcaa** | **b24 (.bss)** | N/A | **RAM RUNTIME: todos los globals FTM/RFTEST (cero al boot)** |
| **0xcbf4f000 (gp)** | b25 | NO | **.sdata/.sbss: los gates RF (gp+0x740, gp+0x7000)** |
| **0xcc0/0xd8xxxxxx** | **b26 CLADE** | **CLADE** | **código FTM/DIAG/ML1 descomprimido** (dispatcher @0xd8150ed8) |
| **0xd0000000** | b26 CLADE | CLADE | `.clade.exception_high` (código de excepciones) |
| **0xce480000** | b27 | **ZLIB** | **RF ML1 LTE: rodata (strings RFLTE/RFLM/SDR735)** |
| **0xd4400000..0xd6539000** | sin PH | (paginado) | **pool dlpager RW/delta — threads PHY, backing DDR 0x2a3xxxxx** |

**Traducción de request CLADE (FACT):** `req_va(comp) = 0xcc000000 + (out_va − 0xd8000000)`.
El window de código CLADE es válido **0xd8000000..~0xda440000** (~36MB); por encima es filler
`00407070`. El comp-stream b26 se agota en ~0xda440000.

### 2.3 Layout de la RAM de datos (.bss b24) — FACT
**TODOS los globals conocidos caen en b24 (0xc9138000, 48MB, filesz=0)**: cero al boot, poblados en
runtime por inicializadores C / registro dinámico de handlers. No tienen valor estático.

| Global | Rol |
|--------|-----|
| 0xca79a850 | tabla RFTEST runtime (command_id → unpacker struct) |
| 0xca7897b0 | tech flags per-tech "ENTERED" (base @0xca789780+0x30, stride 8) |
| 0xca79c494 | **carrier ptr activo (NULL→SSR)** |
| 0xca65b414 | tabla RFDEBUG (dispatch sub_command, stride 0xc) |
| 0xca65d640 | module-0 ctx (RF-TEST commands) |
| 0xca733d10 | tabla callback per-tech (commit FTM) |
| 0xca9ef490 | command_id → group (byte-indexed) |
| 0xcaad9d00 | tabla registro RFTEST sub_command (runtime) |

Los gates RF (`gp+0x740=0xcbf4f740`, `gp+0x7000=0xcbf56000`) están en **b25 (.sbss)**, no en b24.

### 2.4 CLADE / q6zip / lo que FALTA descomprimir — FACT + reconciliación

- **b26 = CLADE** (no q6zip): 3 diccionarios `@0x2444000/6000/8000` (OR `0x3fffffff/0x007fffff/
  0x0001ffff`, firma de `cladetool.cpp`). Descomprime HW-accurate con `clade_extractor_sm6375` +
  `libclade.so` (0.14% invalid). **FACT.**
- **b27 = ZLIB** (`78 9c`) → `seg27_dec.bin`, rodata RF. **FACT.**
- **Ningún otro segmento comprimido** más allá de b26/b27. **FACT.**

**CONTRADICCIÓN RESUELTA (importante):**
- Un pase temprano dijo que `clade_dec.bin` (54MB) era "código válido >0xd8a00000". **FALSO.**
  `locate_rflte.md` verificó byte-a-byte que `clade_dec.bin` está **corrupto/mal-descomprimido**
  (garbage en 0xd8150ed8; no desensambla). El detector de código por conteo de `dealloc_return`
  da falso positivo sobre datos CLADE mal-alineados. **GANA `locate_rflte.md`.** Usar **sólo**
  `clade_dec_full.bin` (10MB) / `clade_ok.bin`=`clade_dec_36m.bin` (36MB, re-extraído correcto) /
  `clade_exc_high.bin`.
- Un pase dijo que el pool dlpager 0xd4400000 "se sirve de b26 CLADE". **CORREGIDO:**
  `locate_rflte.md §4` verificó que el control-block runtime `@0xc8cdf130` apunta a **DDR físico
  0x2a3xxxxx** (threads PHY SYMPROC_IUSS/DEMOD_LITE), no a b26. Es un pool RW/delta de trabajo,
  no backing de código. **GANA `locate_rflte.md`.**

**El código RFLTE/RFLM/SDR735/RFDEVICE NO está en NINGÚN backing estático del MBN** (verificado con
decoder immext correcto: 0 xrefs a los VAs de string RF en 36MB CLADE + 7.3MB exc_high + todo el
plano). Reside en un segmento de código RF residente **fuera de los program headers** (DDR físico
0x2a3xxxxx). **Sólo recuperable con dump de RAM en vivo.** (§12)

---

## 3. DIAG TRANSPORT (resumen)

**Master dispatch** = `diagpkt_master_dispatch @ 0xc0d55df8`, switch por `cmd_code = pkt[0]`:
casos inline (0x80/0x4b/0x73/0x7d/0x82/0x29); resto → master table. **FACT.**

- **SUBSYS_CMD (0x4b):** `subsys=pkt[1]`, `subsys_cmd=memuh(pkt+2)`. Busca el nodo por subsys y el
  rango `[lo,hi]` en su user_table (seg21). **67 nodos → 32 subsys.**
- **FTM = subsys 0x0B**, user_table `@0xc37bd1e8`, count 75, **TODAS → wrapper único `0xd8150ed8`**
  (ftm_common_dispatch). Valida `pkt[1]==0x0B`, `memuh(pkt+2)==0x14`, `id&0xfffe==0x35a`.

**Canales (QRTR sockets), `diagcomm_io_socket_init @ 0xc0d813dc`:**
| canal | gp off | rol |
|-------|--------|-----|
| CNTL | gp+0x6998 | control/handshake |
| **DATA** | gp+0x69a0 | **TX de TODAS las respuestas + logs + eventos** (io_type==2) |
| **CMD** | gp+0x699c | **RX de comandos** |
| DCI | gp+0x69a4 | Diag Command Interface |

**Handshake (ctrl-msgs por CNTL, parser `0xc0d66264`, jumptable @0xc35c9b78):**
- type 8 = FEATURE mask → `memb(0xc92e43e0)=1`
- type 0x21 = DIAGID → `memw(0xc92e4754)` bit0
- type 0x11 = TX MODE (por stream, real-time)
- QRTR NEW_SERVER (type 8) del AP → puebla node/port destino (`0xc0d830b4`→0xc8c2d870/74).

**El GATE de respuestas — `diagpkt_rsp_send @ 0xc0d36c44`:** exige DIAGID bit0 (`0xc92e4754`) **Y**
FEATURE mask (`0xc92e43e0`). Si no → "Attempt to send response before feature mask OR diagID".
**Los F3/log/event NO pasan por este gate** → drenan aunque las respuestas de comando estén bloqueadas
(explica "F3 llegan pero respuestas no"). **FACT.**

**Ser el peer (secuencia mínima):** 1) publicar servicios QRTR (CNTL inst0, **DATA inst2**, DCI inst4,
service 0x1001) ANTES de comandar (genera NEW_SERVER → node/port + `+0x94=0`); 2) FEATURE (type 8);
3) DIAGID (type 0x21); 4) TX MODE stream=1; 5) **comandar por CMD (inst1), leer por DATA (inst2)**.

**SSIDs F3:** **FTM/RF = 23 (0x17)** (toda la familia `[FTM.RFTEST]/[FTM.RFDEBUG]/[FTM.CMN]`,
IQ_CAPTURE, RADIO_CONFIG, RX_MEASURE, cal-V3). Vecinos: 14 (NR5G/IRAT), 6004 (cal xPT).
Habilitar sólo RF-test: `7D 03 17 00 17 00 00 00 FF FF FF FF`. Habilitar todo: `7D 04 00 00 FF FF FF FF`.

**NO hay PEEK/POKE/NV/EFS por DIAG en este PD** (0x02–0x07, 0x26/0x27, NV_PEEK ausentes). Para leer RAM
en vivo NO hay comando DIAG genérico → hay que **invocar handlers** (STATUS 0x0C, FEATURE_QUERY 0x2C,
etc.) o volcar RAM por ramdump/QDL. **FACT** (detalle en `map_diag_core.md §6`).

---

## 4. FTM FRAMEWORK (jerarquía completa)

### 4.1 El wire — un modelo, dos ejes de índice — FACT + reconciliación

**El layout del wire es único y consistente** (reconcilia `sub_command_map.md` con `wire_offset_B.md`
y el prompt; ambos describen el MISMO paquete, sólo que se leen bytes distintos en fases distintas):

```
off   size  campo                                    selecciona
0x00   1    0x4B   DIAG_SUBSYS_CMD_F
0x01   1    0x0B   DIAG_SUBSYS_FTM
0x02   2    ftm_cmd_id (u16 LE)  = 0x0027 (LTE)  ───► tabla @0xc37bc828 → arm por tech (0x27→0xd814e534)
0x04   2    sub_command (u16 LE) = 0x10xx        ───► WRAPPER (jump-tbl @0xc37c649c, disp 0xd82714b4)
0x06   2    tlv_count (u16 LE)
0x08   ..   body[0], body[1]
0x0a   1    command_id (≤0x31)                   ───► UNPACKER (resolver 0xd8272684, tbl @0xca79a850)
0x0b   ..   (algunos handlers leen command_id en +0x0b / +0x0c)
0x0f   1    param byte  (bajo del packed 24-bit)
0x10   1    param byte
0x11   1    param byte  (alto)  → r19 = byte15 | (byte16<<16) | (byte17<<24), pasa como r2 al measure path
0x12+  ..   TLVs: { u16 field_id, u16 len, u8 value[len] }  (típico value = u32 LE)
```

**FACT (byte-exact).** El pre-parser `0xd8272c10` copia sólo los 8 bytes de cabecera (wire[0..7]) al
descriptor de respuesta; los bytes 0x0a/0x0f/0x10/0x11 se leen del **buffer crudo** del request
(`r18 = wire ptr, N=0, byte 0 = 0x4B`), por eso están "más allá" de la cabecera copiada.

> **Nota de reconciliación de rutas:** hay DOS entradas al FTM que coexisten:
> - **Ruta A (registrar 0x14):** `diagpkt` subsys 0x0B, subsys-cmd `memuh(pkt+2)==0x14`, id `0x35a`
>   → wrapper `0xd8150ed8`. Aquí el ftm_cmd LTE está en **pkt[0xa]** y sub_command en pkt[0xc].
>   (`iq_final_values.md`, `ftm_set_mode_verified.md`.)
> - **Ruta B (jump-table por ftm_cmd):** router nivel-1 `0xd814e034` lee ftm_cmd en **pkt[0x02]**
>   → tabla `@0xc37bc828[0x27]=0xd814e534` → router por RANGO `0xd8157ec4` del sub_command @0x04.
>   (`sub_command_map.md`.)
> Son dos framings del mismo transporte DIAG-FTM (la Ruta A es el path del subsys registrar; la Ruta B
> es el jump-table interno). **En ambos, el paquete corto `4b 0b 27 00 <sub@4> <ntlv@6> ...` funciona**
> porque el arm 0x27 lo despacha. Para IQ RX se usa el layout de §4.1 (ftm_cmd @0x02 = `27 00`).

### 4.2 La tabla ftm_cmd_id `@0xc37bc828` (por RAT) — FACT
`ftm_cmd_id` (pkt[2..3]) indexa `@0xc37bc828`. **0x00=FTM_COMMON**, **0x27=FTM_LTE**, 0x14=WLAN,
0x22/0x28=otras techs, 0x8000/0x8001=NR5G. El arm 0x27 (`0xd814e534`) llama al handler LTE `0xd8157ec4`.

### 4.3 Router por RANGO del sub_command (bajo ftm_cmd 0x27) — FACT (`0xd8157ec4`)
```
sub_command @0x04:
  0x0000–0x0FFF  → RFDEBUG/legacy   (0xd8157fe4 → tbl @0xca65b414, stride 0xc, gate sub<=0x15)
                                      >>> TECH_ENTER_EXIT = sub 0x000D
  0x1000–0x3FFF  → RFTEST multi-tech (0xd82714b4)  — sub-particionado por byte alto @0x05:
                     0x10xx → jump-tbl @0xc37c649c (24 slots)
                     0x20xx → jump-tbl @0xc37c645c (16 slots)
                     0x30xx → jump-tbl @0xc37c6440 (7 slots, comparte handlers con 0x101x)
  0x4000–0x6FFF  → error (reservado)
  0x7000–0x702C  → familia 0x70xx (@0xc37c5a3c)
```

### 4.4 Los DOS ejes (recordatorio) — FACT
- **sub_command (wire[4..5], 0x10xx)** → **wrapper** vía jump-table `@0xc37c649c` (dispatcher
  `0xd82714b4`, gate hi==0x10 / low≤0x17). Los 24 wrappers son `0xd86fxxxx`/`0xd8271xxx`.
- **command_id (wire byte 10, ≤0x31)** → **unpacker** vía resolver `0xd8272684`:
  `struct = memw(memw(0xca79a850) + id*4 + 0x34)`. **El unpacker que corre lo fija el command_id, NO
  el sub_command.**
- El binding `command_id ↔ nombre ↔ unpacker` se asigna en **registration order en RUNTIME** (tabla
  `@0xca79a850` alloc/memset `0xd8272368`, defaults `0xd82723c0`, 50 slots de 0x28B sin nombres). **El
  entero exacto es UNKNOWN estático.** Se cierra en vivo con COMMAND_CAPABILITY → CMD_MASK.

### 4.5 Semántica de los params packed @0xf/0x10/0x11 — FACT
El handler canónico `0xd86fd0f4` forma un packed 24-bit `r19` y lo pasa como r2 al dispatcher
`0xd8272cd8`. Byte **0x0f = mode/type** de la operación (valores observados: **3** y **5**; 5 =
capture/measure → decode-A `0xd826d3e4`, el que llama al getter y **puede reventar** si carrier NULL).
Bytes **0x10/0x11 = sub-parámetro 16-bit** del mode (handle/índice de captura, semántica fina UNKNOWN).

---

## 5. TABLA DE COMANDOS RFTEST (command_id / registration order → nombre → unpacker)

**Módulo 0 = RF-TEST** (init `0xd8182640`, primero desde el registrar maestro `0xd84aa03c`; group id
0x12, ctx `@0xca65d640`; slots de 12 bytes `{unpack@+0, repack@+4, bufsz@+8}`). slot_index =
registration order = **command_id relativo con base B** (B=0 inferido → command_id absoluto).

| slot (=cmd_id rel) | nombre | UNPACKER VA | field-table | REPACK VA | bufsz | naturaleza |
|-----:|--------|-------------|-------------|-----------|-------|------------|
| **0** | **RADIO_CONFIG** | 0xd8183f30 (wrap 0xd8182ec0→0xd818327c) | @0xc37c0290 (nombres @0xc906c630) | 0xd8182f40 | 0x174810 | tune band/ch/bw |
| **1** | **RX_MEASURE** | 0xd8185654 (disp 0xd8185b1c) | @0xc37c0478 | 0xd81865f4 | 0x540 | measure query |
| 2 | **TX_CONTROL** | 0xd8188428 | @0xc37c078c | 0xd818866c | 0x540 | tx control |
| 3 | TRM_RRA | 0xd86c0fe4 | — | 0xd86c240c | 0x4d740 | TRM resource |
| (4) | *(vacío en mod0)* | — | — | — | — | — |
| **5** | **IQ_CAPTURE** | 0xd81893a4 (disp 0xd8189818) | @0xc37c0828 (fmt @0xc37c08f4) | 0xd8189d38 | 0x100 | sample capture |
| 6 | **TX_MEASURE** | 0xd818ab6c | @0xc37c0a10 | 0xd818bc20 | — | tx measure |
| 7 | MSIM_CFG | 0xd8187fe4 | — | 0xd818837c | — | multi-SIM cfg |
| 8 | IRAT_CONFIG | 0xd84aff68 | — | 0xd84b1514 | 0x64440 | inter-RAT cfg |
| 9 | WAIT_TRIGGER | 0xd81870fc | @0xc37c0644 | 0xd8187a18 | — | trigger wait |
| 10 | tx_measure (RFDEBUG) | 0xd84ad0b8 | — | 0xd84ad1d4 | 0x80 | debug tx meas |
| **11** | **COMMAND_CAPABILITY** | 0xd81849ec | @0xc37c03b4 (nombres @0xc906ce18) | 0xd81851a4 | 0x2a0 | query/CMD_MASK |

**Nombres = byte-exact** (F3 fmt de b21, tabla @0xc3555b80). **command_id relativo = FACT; absoluto
(B=0 → RADIO_CONFIG=0, RX_MEASURE=1, IQ_CAPTURE=5, COMMAND_CAPABILITY=0x0b) = INFERENCE**, se confirma
en vivo con COMMAND_CAPABILITY.

**COMMAND_CAPABILITY fields (grp-22 @0xc906ce18):** QUERY_COMMAND=1, QUERY_PROPERTY=2, **CMD_MASK=3**
(rsp, bit N = command_id N registrado), PROPERTY_MASK_0_63..192_255 = 4..7. Es la llave para cerrar
todos los enteros command_id de una sola respuesta.

Módulos 1 (group 0x13, ctx `@0xca65b5c0`) y 2 (group 0x0b, ctx `@0xcb676300`) ocupan el resto del
espacio 0..0x31; su enumeración exacta es UNKNOWN estático (mismo mecanismo).

---

## 6. LA MÁQUINA DE ESTADOS Y LOS GATES — cadena causal del SSR

### 6.1 Dos estructuras de estado (no confundir) — FACT
- **`session->0xc`** = enum del MODO operativo de la sesión FTM-RF `{0=INACTIVO, 2=ACTIVO, 0x13=...}`.
  Init `0xd81df874` lo deja en 0. Gate del enter-writer `0xd81e5d20` (`if != 2 skip`). **NO existe
  store estático `(session+0xc)=2`** (barrido completo) → el modo 2 lo avanza el **path de activate
  FTM** (rflte_mc_carrier_activate/wakeup) cuando el HW responde. **FACT (ausencia) + INFERENCE (activate).**
- **`@0xca7897b0[tech]`** = flag per-tech "ENTERED" (1 byte, stride 8, base @0xca789780+0x30). Escritor
  ENTER `0xd81e5cec` → `=1` @0xd81e5d54 (gated por `session->0xc==2`). EXIT `0xd81e5e18` → `=0`.
  Gate lector `0xd8202228` (`if !=1 → status 0x14`) en el executor de acción diferida `0xd8201d3c`.

### 6.2 El gate de tech-state (status 0x14) — FACT
Gate `0xd81e5d60`: si `memb(ctx+0x89a8)==0x7` (tech "no-entrada", el índice else de `0xd8169ec0`) →
error 0x10 → status **0x14 (DIAG_BAD_PARM)**. TECH_ENTER (sub 0x0D, TECH=1) puebla ese estado y lo
saca de 0x7. **Un 0x14 en un barrido = "tech no entrada", NO un crash.** El orden importa.

### 6.3 Los DOS gates de HW (`gp = 0xcbf4f000`, b25 .sbss) — FACT
Triangulación de gp (140/423 offsets con cross-check byte-exact). VAs absolutas:
- **Gate (a) `gp+0x740` = `0xcbf4f740`** (byte, enum modo RF `{0,1,2}`, logger `0xd81bde74`). Gate pide
  **`==2`** ("RF cal mode activo"). Reader `0xd81bd160`: `if(memb(gp+0x740)!=2) jump 0xd81bd4b0`.
- **Gate (b) `gp+0x7000` = `0xcbf56000`** (word, puntero al ctx RF-instance C++, vtable `@0xc37c6d80`).
  Gate pide **`!=0`**. Reader `0xd8286240`. Lo CREA el accessor lazy get-or-create `0xd8284eac`
  (creator `0xd8284cf4`, `memw(gp+0x7000)=obj`) la 1ª vez que se toca el subsistema RF-instance.

**Writer del gate (a) = UNKNOWN estático (punto ciego).** Barrido autoritativo del binario de 36MB:
los ÚNICOS writers de `gp+0x740` son `0xd81bd494` y `0xd81bd5fc` (ambos `=mux(pred,#1,#0)` → **0 ó 1**,
rama NO-activate) + reset `=0` en `0xd81bd7c8`. **NO existe NINGÚN store del literal 2** en todo el
código fiable. El writer del `2` vive en código RF-cal **fuera del MBN** (dlpager/RFLM,
`ftm_calibration_v3_*`), disparado por el **FTM_SET_MODE canónico (mode=cal)**. **FACT (ausencia) +
INFERENCE (trigger).** Anclado por strings: `FTM_RF_MODE_CAL`, `lte_LL1_get_ul_ftm_cal_mode(cxn)>0`.

> **Contradicción resuelta:** un pase citó el sub-cmd **0x10F** (handler `0xd8263e2c`) como
> "set RF cal mode". **FALSO** (`ftm_set_mode_verified.md`): el 0x10F es **enable RF por-tech** (setea
> `gp+0x1ed5/0x1ed6`, NO `gp+0x740`/`gp+0x7000`; tablas @0xca79a8e0/@0xca79a850). El FTM_COMMON handler
> `0xd8271290` sólo reconoce 0x10F/0x4F7/0x4F5, ninguno es SET_MODE. El SET_MODE clásico lo maneja el
> dispatcher runtime `0xd8150ed8` (tabla @0xc37bd1e8) no mapeado. **GANA `ftm_set_mode_verified.md`:
> el sub-cmd exacto de SET_MODE cal es UNKNOWN estático.**

### 6.4 La cadena causal completa del SSR — FACT (byte-a-byte)
```
GETTER 0xd827923c:
  r0 = memw(0xca79c494)                 ; active-carrier ptr
  if (r0 != 0) jump 0xd8279260 (return) ; OK
  call 0xd80d8998  →  jump 0xc0d60074   ; NULL → err_fatal → SSR (NO retorna limpio)
```
- **Único writer de `0xca79c494` = SETTER `0xd8279264`** (`memw(0xca79c494)=r0`, valor `0xca6e3b88`).
- SETTER alcanzable SÓLO por: `APPLY 0xd8273b2c ← 0xd81bd2a8 (en 0xd81bd018) ← init 0xd81e52c8`.
- `0xd81bd018` = **carrier-activate handler del módulo FTM-LTE**, registrado en runtime (0 refs
  estáticas → callback MSGR/event). Su APPLY está detrás de los DOS gates: `gp+0x740==2` (0xd81bd160)
  y `gp+0x7000!=0` (0xd8286248, `r0==1` @0xd81bd290).
- **~96 call-sites** llaman al getter (todo el pipeline measure/capture 0xd826xxxx). Reach.py probado:
  `IQ_CAPTURE 0x1002 (0xd86fd0f4) → 0xd8272cd8 → 0xd826d3e4 → getter`; `RADIO_CONFIG 0x1003 → getter`.
- **RADIO_CONFIG unpacker `0xd8183f30` NO escribe el carrier ptr** (reach.py: setter=NO getter=NO;
  sólo parsea TLVs a la pila). Lo que hace es, con BAND+EARFCN, **disparar el activate** que hace pasar
  los gates de `0xd81bd018`, que es quien escribe el ptr.

**Conclusión (cadena causal):** en un FTM "pelado" (sólo TECH_ENTER), `gp+0x740=0`, `gp+0x7000=NULL`,
`0xca79c494=NULL`. Cualquier IQ_CAPTURE/RX_MEASURE (familia measure 0x1002/0x1003 y command_id
correspondiente) llama al getter → NULL → `err_fatal` → **SSR total** (no un status 0x14). Hay que
abrir los gates ANTES (§7 PASO A).

**Mitigación defensiva (para el driver):** NUNCA mandar IQ_CAPTURE/RX_MEASURE hasta confirmar en vivo
`memw(0xca79c494)!=0`. Los comandos QUERY-safe (sub 0x1004,0x1005,0x1007,0x1008,0x1009,0x100a,0x100f,
0x1011,0x1012,0x1014,0x1015,0x1017 con num_tlv=0) NO llegan al getter → seguros para barrer.

---

## 7. SECUENCIA DE BRING-UP RX/IQ (lo más importante) — byte-exact hasta donde se sabe

Estado inicial FTM pelado: `gp+0x740=0`, `gp+0x7000=NULL`, `0xca79c494=NULL`. Orden **obligatorio A→B→C**.

```
PASO 0 — DMS: poner el modem en modo factory-test (FTM/offline)
   QMI DMS set_operating_mode(FTM)  →  CM cm_ph_cmd_pref_change_req  →  MMOC.
   Efecto: el flag global FTM/ONLINE (guard "Attempt to access FTM variables in ONLINE MODE")
           pasa a FTM; la ML1-online se para; corre la FTM task (dispatcher RFTEST/RFDEBUG).
   [subsys/UMID/payload numérico exacto = UNKNOWN; candidato subsys DIAG 0x5B]

PASO A — PONER EN RF CAL MODE  (setea gp+0x740=2 y crea el ctx gp+0x7000)
   Comando: FTM_SET_MODE canónico — DIAG 4B 0B, FTM_COMMON (ftm_cmd 00 00), sub-cmd SET_MODE, mode=2/cal.
   Forma canónica:   4B 0B  00 00  <SET_MODE:u16 LE>  <mode:u16 LE = 00 02>
   >>> sub-cmd numérico EXACTO de SET_MODE en este build = UNKNOWN estático (§6.3, dispatcher runtime).
   >>> NO es el sub-cmd 0x10F (ése es enable RF por-tech, NO set-cal-mode).
   Prereqs HW (FACT strings): rfm_init llamado (@0xce6de1b0), MCPM ON (@0xce748848), NV/cal cargada.
   Efecto: el driver de cal (fuera del MBN) escribe gp+0x740=2 y dispara accessor 0xd8284eac → gp+0x7000!=0.

PASO B — TECH_ENTER (LTE)   [RFDEBUG sub 0x000D — FACT · TECH=1=LTE — FACT · responde con status — FACT]
   4B 0B 27 00 0D 00 03 00
      01 00 04 00 00 00 00 00     ; TLV SUB      (field 1) = 0
      02 00 04 00 01 00 00 00     ; TLV TECH     (field 2) = 1 (LTE)          <-- FACT
      03 00 04 00 00 00 00 00     ; TLV SCENARIO (field 3) = 0
   Concatenado:
   4B 0B 27 00 0D 00 03 00 01 00 04 00 00 00 00 00 02 00 04 00 01 00 00 00 03 00 04 00 00 00 00 00
   Dispara commit 0xd81dfecc → callr módulo LTE per-tech (@0xca733d10[tech].fn).
   Esperar rsp DIAG status byte = 0. (offset 0x06 = handle, no-crítico; offset 0x08 ignorado con subcmd 0x14.)

PASO B' (recomendado) — COMMAND_CAPABILITY para cerrar el enum de sub_command/command_id EN VIVO
   Wrapper query-pure SEGURO = sub 0x100D (0xd86fde80): llama al resolver pero NUNCA derefa el carrier
   (0xca79c494 puede ser NULL). command_id byte10 = 0x0B (COMMAND_CAPABILITY, B=0 inferido).
   4B 0B 27 00  0D 10  01 00  00 00 0B 00 00 00 00 00 00 00  01 00 04 00 FF FF FF FF
                └sub=0x100D┘ └ntlv=1┘ └b8 b9 cmd_id b11..┘  └field1 QUERY_COMMAND=0xFFFFFFFF┘
   Rsp field3 = CMD_MASK: bit N=1 ⇒ command_id N registrado (bit0=RADIO_CONFIG, bit5=IQ_CAPTURE,
   bit0x0b=COMMAND_CAPABILITY confirma B=0). Esto resuelve TODOS los enteros de una vez.

PASO C — RADIO_CONFIG (BAND + EARFCN + BW + RX_CARRIER)   [unpacker 0xd8183f30, field-tbl @0xc37c0290]
   Carrier vía wrapper getter-clean 0x100D, command_id byte10 = 0x00:
   4B 0B 27 00  0D 10  05 00  00 00 00 00 00 00 00 00 00 00
      01 00 04 00 00 00 00 00     ; RX_CARRIER (fid 1)  = 0    (fijar índice PRIMERO)
      19 00 04 00 01 00 00 00     ; TECH_MODE  (fid 25) = 1 (LTE)
      05 00 04 00 03 00 00 00     ; BAND       (fid 5)  = 3 (ej. B3)
      06 00 04 00 27 06 00 00     ; CHANNEL    (fid 6)  = EARFCN 1575
      07 00 04 00 20 4E 00 00     ; BANDWIDTH  (fid 7)  = 20000 kHz
   Orden de TLV: RX_CARRIER → TECH_MODE → BAND → CHANNEL/CENTER_FREQ → BANDWIDTH.
   Efecto: con A ya hecho, aguas abajo (rflte_mc_carrier_activate @0xce6e72c0 / rflte_ftm_mc_wakeup
           @0xce6e08a8) el activate completa → carrier-activate 0xd81bd018 pasa AMBOS gates →
           APPLY 0xd8273b2c → SETTER 0xd8279264 → memw(0xca79c494)=0xca6e3b88.

PASO D — VERIFICAR EN VIVO (obligatorio antes de cualquier measure/capture):
   memb(0xcbf4f740) == 2      (gate a: RF cal mode)
   memw(0xcbf56000) != 0      (gate b: ctx RF creado)
   memw(0xca79c494) != 0      (carrier ptr poblado)   [o memb(0xca7897b0 + tech*8) == 1]

PASO E — IQ_CAPTURE   [command_id (byte10) = 0x05; sub_command wrapper = 0x1002 (measure/capture)]
   4B 0B 27 00  02 10  04 00  00 00 05 00 00 00 00 00 00 00
      0E 00 04 00 00 40 00 00     ; NUM_OF_SAMPLES (fid 14) = 16384
      10 00 04 00 00 C0 D4 01     ; SAMP_FREQ      (fid 16) = 30720000
      0F 00 04 00 00 00 00 00     ; IQ_DATA_FORMAT (fid 15) = 0  (8/16-bit, enum UNKNOWN)
      0D 00 04 00 01 00 00 00     ; FETCH_IQ       (fid 13) = 1
   >>> Sin PASO A/C, este comando revienta (getter NULL → SSR). Con ellos, devuelve el REPACK
       con {name, size, address} del buffer memshare.
```

**Puntos byte-exact confirmados (FACT):** TECH_ENTER=sub 0x0D, TECH=1=LTE, layout wire, field_ids de
RADIO_CONFIG/IQ_CAPTURE, wrapper query-pure 0x100D. **UNKNOWN (cerrar en vivo):** sub-cmd exacto de
SET_MODE cal, command_id/sub_command absolutos (CMD_MASK), enums IQ_DATA_FORMAT/SAMP_FREQ.

---

## 8. IQ CAPTURE (field-table, fases, formato, entrega)

### 8.1 Unpacker y field-table — FACT
UNPACKER = `0xd81893a4`; dispatcher jump-table de campo `0xd8189818`: `r2 = memw(field_id<<2 +
0xc37c0828); jumpr r2`. **51 slots (field_id 0..0x32)**; default/reject `0xd8189b38`. Gates:
`field>0x19` (0xd81897f0) y `field-1>0x18` con tabla @0xc37c0890 (0xd818a0cc). Encoding TLV =
`{u16 field_id, u16 len, value[len]}` LE, valor hasta u32.

**Field-table IQ_CAPTURE (los que importan, nombres @0xc906c720) — FACT:**
| fid | nombre | tipo | uso |
|----:|--------|------|-----|
| 1 | RX_CARRIER | u32 | carrier RX (requerido) |
| 2 | RFM_DEVICE | u32 | device/path RF |
| 4 | RX_AGC | u16 | AGC/gain leído-o-fijado |
| 5 | LNA_GS | — | LNA gain state (fijar) |
| 13 | **FETCH_IQ** | u32 (bool) | → ACTION_FETCH (lee buffer) |
| 14 | **NUM_OF_SAMPLES** | u32 | nº muestras a capturar |
| 15 | **IQ_DATA_FORMAT** | u32 enum | 8-bit vs 16-bit |
| 16 | **SAMP_FREQ** | u32 | sample rate |
| 17 | OVERRIDE_LNA | u32 | fuerza LNA state (gain fijo) |
| 18 | RX_GAIN_CTL_TYPE | u32 | auto/manual/fixed |
| 39 | **IQ_CAPTURE_TYPE** | u24 | fuente: raw ADC / post-decimación |

### 8.2 Las TRES fases (vista interna normalizada @0xc906c8f8, 28 campos) — FACT
```
logidx 7  = ACTION_GETCFG    ; consulta config (rx_path_data por carrier), NO captura
logidx 8  = ACTION_ACQUIRE   ; CAPTURA: arma request, llena el buffer de muestras
logidx 9  = ACTION_FETCH     ; LEE: devuelve muestras al AP (= FETCH_IQ field 13)
logidx 13 = IQ_SOURCE ; 14 = SAMP_SIZE (8/16b) ; 15 = DATA_FORMAT ; 16 = SAMP_FREQ
logidx 17 = MAX_DIAG_SIZE ; 18 = SAMP_OFFSET (ventana) ; 19 = NUM_SAMP_BYTES
```
**Modelo de dos fases (recomendado):**
- **ACQUIRE:** IQ_CAPTURE con {RX_CARRIER, NUM_OF_SAMPLES=N, SAMP_FREQ, IQ_DATA_FORMAT, FETCH_IQ=0} →
  `rflte_ftm_iq_capture_prop_action_{8,16}bit` → alloca buffer DDR, ML1 ASYNC_SAMP_CAPTURE llena N muestras.
- **FETCH:** IQ_CAPTURE con {RX_CARRIER, FETCH_IQ=1, SAMP_OFFSET=k, NUM_SAMP_BYTES=M, MAX_DIAG_SIZE} →
  REPACK devuelve [k, k+M). Repetir con SAMP_OFFSET creciente para drenar >MAX_DIAG_SIZE.
- **Una-trama:** FETCH_IQ=1 + N pequeño hace ACQUIRE+FETCH junto si `N*bytes ≤ MAX_DIAG_SIZE`.

### 8.3 Formato de muestra — FACT (mecanismo) / INFERENCE (layout)
- **8-bit vs 16-bit** por `rflte_ftm_iq_capture_prop_action_8bit/16bit_iq_buff` (@0xce6ded20/68),
  seleccionado por IQ_DATA_FORMAT(15) / SAMP_SIZE. **FACT.**
- **Layout (INFERENCE fuerte, convención QC FTM):** **IQIQ interleaved, signed two's-complement, LE**.
  8-bit: `int8 I, int8 Q, ...`. 16-bit: `int16 I, int16 Q, ...`.
- **Escala a dBm:** `gain = RX_cal_offset(path) + LNA_offset(state) + freq_comp(EARFCN) (+phase_comp)`.

### 8.4 Sample rate — FACT (enum) / UNKNOWN (mapa exacto)
Interno = enum log2 del ratio WB (`lte_LL1_ue_wb_samp_rate_e`), **base LTE WB = 1.92 MHz**. Rates:
1.92/3.84/7.68/15.36/23.04/30.72 MHz, derivado del BW por `WB_DF_BW_SAMP_RATE_LUT[bandwidth]`. Para
20MHz LTE = 30.72 MHz nativo. **UNKNOWN:** si el TLV SAMP_FREQ es Hz/kHz/enum (probar en vivo).

### 8.5 Entrega memshare (REPACK) — FACT (contrato) / INFERENCE (secuencia)
- Buffer **NO inline**: `p_sample_capture_buffer` (DDR) + `sample_capture_buffer_size_words` (asserts:
  `!= NULL`, `!= 0`, `iq_capture_req_p == p_sample_capture_buffer`, `num_captured <= num_to_capture`).
- Fuente: ML1 `ASYNC_SAMP_CAPTURE` (lmem_requested), DTR-RX decima a SAMP_FREQ, escribe `sample_buf`.
- REPACK (fmt @0xc37c0949): `[%2d][%3d][ %12s ][ %4d ][ 0x%8x ]` → por propiedad: **name, size_bytes,
  address**. Array reordenable ("Swapped location"), omite lo que no cabe ("NOT PACKED").
- **memshare QMI client** del modem (`memshare_qmiclient` @0xc3577c60): el AP (daemon, client-id 1,
  5MiB) mantiene viva la región DDR; el modem escribe las IQ ahí; el REPACK notifica address+size; el
  AP lee `base_5MiB + (address − region_phys_base)`. No hay copia inline para capturas grandes.
- **Máx muestras (5MiB):** ~1.31M (16-bit, 4B/muestra) / ~2.62M (8-bit, 2B/muestra), menos overhead.
  A 30.72 Msps, 5MiB 16-bit ≈ 42.7 ms; 8-bit ≈ 85 ms.

### 8.6 WAIT_TRIGGER (command_id 9) — FACT (fields)
Fn `0xd8187cc0`, 8 fields (SRC_DEVICE/SIG_PATH/ANT_PATH/PLL_ID/NB_ID/LANE_ID/SRC_NB_ID/SRC_LANE_ID).
Arma un punto de disparo por (device, nanobit/lane) para sincronizar la ACQUIRE a un evento de timing.
Secuencia: `RADIO_CONFIG → WAIT_TRIGGER(src) → IQ_CAPTURE(ACQUIRE)`.

---

## 9. TX (futuro)

**Comandos FTM TX (módulo RF-TEST):** TX_CONTROL (slot/cmd_id rel 2, unpacker `0xd8188428`, gate
property_id≤0x14, stride 0x40, callr setter) y TX_MEASURE (slot 6, unpacker `0xd818ab6c`).

**TX_CONTROL (fine control, TLV @0xc906c438):** TX_ENABLE=1, PATH_INDEX=2, RFM_DEVICE=4, ANT_PATH=5,
XPT_MODE=6 (APT/EPT/ET), IQ_GAIN=8, TXFE_GAIN=10, ENV_SCALE=12, RGI=13, PA_BIAS=14, PA_STATE=15,
PA_CURRENT=16, DATA_SOURCE=21 (tono/pattern/IQ), ENV_GAIN=23, ET_VMIN=39, + kernels DPD AMAM/AMPM.

**TX_MEASURE (potencia+waveform, TLV @0xc906c850):** TX_CARRIER=1, TX_ACTION=3, TX_POWER=4,
TX_WAVEFORM=8, NETWORK_SIGNAL=9, MODULATION_TYPE=11, UE_POWER_CLASS=15, TX_POWER_SWEEP_STOP/STEP=18/19.

**Secuencia de encendido TX (INFERENCE):** TECH_ENTER → RADIO_CONFIG(UL_EARFCN) → TX_MEASURE(start,
TX_POWER, TX_WAVEFORM) → `dispatch_tx_on_ind` → `update_pa_on_event` (PA ON script RFFE) →
`config_common_txagc`/`set_txagc_params` (reparte gain IQ+TXFE+ENV+PA_STATE). Fine: TX_CONTROL manual
+ `rflte_ftm_xpt_override_txagc`.

**tx_playback (IQ arbitrario) — FACT (NR5G-MFTE):** transmite muestras IQ de un buffer:
`tx_playback_req_p->stream_vector_addr[0..1]` (alineado 0x40), `stream_vector_bytes[]` (≤
`nr5g_mfte_nr_tx_sample_buffer_max_size_bytes`), `num_tx_stream_vectors≤2`. Estado
`NR5GFW_CAL_CTX_TX_IQ_PLAYBACK_RUNNING`. Disparo: `rx_tx_ctrl_req.tx_playback_params` con op-mask
`NR5G_LL1_CAL_FTM_OP_TX_CFG|_TX_START|_TX_STOP`. **El selector DIAG/FTM exacto = UNKNOWN (mensaje MSGR
interno).** LTE no tiene tx_playback (sólo waveform modulado `multi_cluster_tx_waveform`).

**DPD/ET — FACT:** DPD = txlin (AMAM/AMPM + kernels); WB-DPD (`tx_dtr_wb_dpd_rate_kHz`). Comandos
RFDEBUG: `LOAD_UNITY_DPD` (PA crudo), `VDPD_CONVERSION`/`VDPD_CAL` (DPD acoplada a ET), autopin
(`autopin_cal_gen_dpd_lut`). ET = QET6200; XPT_MODE=ET + VDD table (PAPM hub) + QPOET thresholds.

**Límites de safety (SIEMPRE presentes, no bypasseables por TLV) — FACT:**
- **SAR clamp (LMTSMGR):** assert `tx_power < LMTSMGR_RTSAR_MAX_TX_PWR_DBM_10` (@0xcea91b72) — clamp
  duro por SAR (dBm*10), runtime-driven (proximity). Si se excede → assert → crash.
- **MTPL** (Pmax por NV + UE power class), **NS** (band restriction), **PA_STATE max**, `WFW_MAX_POWER_OFFSET`.
- No hay TLV "disable_all_limits". Límite último = Pmax de cal del RFC + PA_STATE max.

---

## 10. RF CHAIN / HARDWARE

**Cadena RX (bloques → funciones → VA, strings de seg27 = FACT; cuerpos = UNKNOWN):**
```
ANTENA → RFFE/RF-FE (ASM/eLNA/LNA, bus RFFE/SPMI por (bus,USID))
       → SDR735 (transceiver WWAN sub-6: mixer/LO/PLL/ADC internos)
       → DTR-RX (filtrado/decimación/DC/RSB) → RxLM (link mgr, handle por antena/carrier)
       → RxFE/RXAGC (LTE LL1, LNA state machine) → SAMPLE CAPTURE / IQ (buffer DDR)
```
- **Chip:** SDR735 (`sdr735_common_class` @0xce6fae60, power-on script + reg dump 8/16-bit).
  SMR526v2 = mmWave (no LTE). QET6200 = ET (TX).
- **RFFE/SPMI:** `rfc_get_used_sid_table` @0xce6d49d0 (device→bus→SID); acceso por (bus,USID)
  `rfdevice_phy_dev_get_bus_idx`; scripts `rffe_finalize_rf_script`. LNA gain→RFFE table
  `rf_rffe_iu_common_prepare_rxagc_table` @0xcea8c0f8.
- **RxLM/DTR:** `rflm_dtr_rx_activate_chain` @0xce5f4ccc; Chain Config HW/SW/Activate; `rxlm_buf_idx`
  por antena. Captura exige RxLM chain requerida ("DL samp rec start ... rxlm chain not requested").
- **Cal RX (NV):** `RFNV_..._RX_CAL_OFFSET` one-per-path (@0xce6d4db0), `rflte_nv_parse_rx_cal_data`
  (@0xce6eba58), switchpoints+LNA offsets (`..._update_switchpoints_and_lna_offsets_in_dm` @0xce6f0960),
  freq comp (`process_rx_static_data`), phase comp (`get_phase_comp_data` @0xcea8d770).
- **MCPM clocks:** el "MCPM on" = votar **QLINK (bus RFFE al transceiver) + WMSS_CX rail + XO_CX +
  qLink Ref clk** vía NPA. Gate del activate: `mcpm_req.return_trans_id != 0` (@0xce8bd818).
- **Device wakeup:** `rfdevice_sleep_manager_wakeup_devices_rx` @0xce6dd840 (genera script RFFE
  inmediato), disparado por `rflte_ftm_mc_wakeup` (FTM). Espera QLINK up (3-6ms,
  `rflte_qlink_status_timer_cb`).
- **RFC (rhodep):** `rfc_intf` singleton — device tables, sig_path (bandas), USID/bus RFFE, GPIOs
  (DalTlmm). Los valores concretos (USID, GPIO, band lists) viven en el blob RFC/NV/EFS (UNKNOWN,
  ausente del MBN — lo que un port a otro board debe re-derivar).

**Para IQ con gain fijo:** RX_GAIN_CTL_TYPE(18)=manual/fixed + OVERRIDE_LNA(17)=1 + LNA_GS(5)=state;
leer RX_AGC(4)/LNA_GS(5) para el gain efectivo.

---

## 11. MSGR / IPC (por qué RFLTE es asíncrono y está fuera del MBN)

**MSGR** = message-router IPC de QuRT. Cada mensaje lleva un **UMID de 32 bits**:
`tech_module = (tech&0xFF)<<8 | (module&0xFF)` (16 altos) + `dir/type + id` (16 bajos). API:
`msgr_client_create` + `msgr_client_add_mq[_dynamic]` + `msgr_register_block(tech_module, client, mq,
umid_list, cnt)`; envío `msgr_send()` / `msgr_send_direct()`. Los **enteros UMID son constantes
compiladas** (`MSGR_ID_VAL(NAME)`) → UNKNOWN estático; sólo los nombres están en b21.

**Tasks:** DIAG (`diag_task_tcb`), **FTM** (`FTM TASK RFGSM Mailbox`), **RF/RFA** (`GSM L1 Common RFA
Task Mailbox`), LTE ML1 (clientes `lte_ml1_*_msgr_client`), NR5G ML1, MCPM.

**El path enter_mode es asíncrono cross-task:** ML1-RFMGR manda `LTE_ML1_RFMGR_ENTER_ONLY_REQ`
(@0xc404e0bc) → task RF/RFA → responde `RFA_RF_LTE_ENTER_MODE_CNF` (@0xc404dce1, `msgr_send`
@0xce8bd84f). `msgr_send` sólo **encola** en la cola de otro task; el resultado vuelve MÁS TARDE como
otro mensaje (`*_CNF`). **En FTM ese REQ nunca se emite** (el path FTM usa `rflte_ftm_mc_wakeup`
directo, sin REQ/CNF) → los comandos que esperan el CNF "cuelgan"; el estado RF lo avanza el activate.

**Por qué el código RFLTE está fuera del MBN:** el receptor MSGR del task RF (`RFA_RF_LTE_*`,
carrier-activate) es código RF residente que **no está en ningún window CLADE ni plano**. El
acoplamiento FTM→RF es por UMID + `msgr_table` (RAM) + cola REX; el estático ve el `msgr_send` y el
handler FTM, pero **no el edge** send→handler (se resuelve en RAM). Igual con `@0xca733d10[tech].fn`
(commit per-tech, callr runtime) y `0xd81bd018` (carrier-activate, registrado por `0xd81e52c8`). Estos
edges y cuerpos son **el binding que el estático no puede seguir** → dump en vivo.

---

## 12. PUNTOS CIEGOS RESTANTES Y CÓMO RESOLVERLOS

Los tres bloqueadores de IQ RX — todos requieren **equipo en vivo**:

### (a) Sub-cmd/command_id exacto de FTM_SET_MODE cal, RADIO_CONFIG, IQ_CAPTURE
- **Por qué falta:** binding table-driven en RAM (`@0xca79a850` command_id, `@0xcaad9d00` sub_command,
  dispatcher SET_MODE runtime `0xd8150ed8`). No hay inmediato estático.
- **Cómo resolver EN VIVO:** mandar **COMMAND_CAPABILITY** (wrapper query-pure 0x100D, seguro con
  carrier NULL) con QUERY_COMMAND=0xFFFFFFFF → leer **CMD_MASK** (field 3): bit N ⇒ command_id N
  registrado. Luego QUERY_COMMAND=N + PROPERTY_MASK nombra cada uno. Para SET_MODE: barrer los sub-cmd
  de FTM_COMMON con mode=2 y verificar en vivo que `memb(0xcbf4f740)` pasa a 2.

### (b) Writer del gate `gp+0x740=2` (y el disparo de gp+0x7000)
- **Por qué falta:** el store del literal 2 vive en código RF-cal fuera del MBN (verificado: 0 stores
  del 2 en 36MB fiables). Lo dispara FTM_SET_MODE cal + el activate.
- **Cómo resolver EN VIVO:** tras el candidato de SET_MODE, **peek de los gates** — pero como no hay
  PEEK DIAG, hay que: (i) dump de RAM en vivo leyendo `0xcbf4f740`/`0xcbf56000`/`0xca79c494`, o (ii)
  inferir por comportamiento (si IQ_CAPTURE deja de dar SSR, los gates se abrieron). Barrido: probar
  cada sub-cmd de FTM_COMMON hasta que `0xca79c494 != 0` (o el IQ funcione).

### (c) Código RFLTE en DDR / dlpager
- **Por qué falta:** el segmento de código RF residente (RFLTE ML1 + SDR735 + threads PHY
  SYMPROC/DEMOD_LITE) no está en ningún program header; backing DDR físico `0x2a3xxxxx`, poblado en boot.
- **Cómo resolver EN VIVO:** **dump de RAM** (ramdump SDI post-crash, QDL modo dump, o `/dev/mem`
  restringido). Anclar por los VAs de string seg27: buscar en el dump las funciones que hacen `immext`
  a `0xce6e72c0` (rflte_mc_carrier_activate), `0xce6e08a8` (ftm_mc_wakeup), `0xce6ded20/68`
  (iq_capture_prop_action). Además volcar el pool DDR `0x2a300000..0x2a539000`. Firmware RFC/NV aparte
  para los registros concretos SDR735/RFFE.

### Plan concreto de captura/prueba EN VIVO (cuando el teléfono esté disponible)
1. **Ser el peer DIAG:** publicar QRTR (CNTL0/DATA2/DCI4, svc 0x1001) → FEATURE → DIAGID → TX MODE.
   Habilitar F3 SSID 23 (`7D 03 17 00 17 00 00 00 FF FF FF FF`) para ver `[FTM.RFTEST]` logs.
2. **Barrer capacidades:** TECH_ENTER(LTE) → COMMAND_CAPABILITY (barrer sub 0x1004..0x1017 num_tlv=0;
   el que devuelve CMD_MASK ≠0 es CAP) → QUERY_COMMAND=0xFFFFFFFF → **fijar todos los command_id**.
3. **Barrer SET_MODE:** probar sub-cmds de FTM_COMMON (mode=2) monitoreando SSR / logs de cal.
   Confirmar con dump: `0xcbf4f740==2`, `0xcbf56000!=0`.
4. **RADIO_CONFIG (BAND+EARFCN+BW)** → confirmar `0xca79c494!=0` (dump o "IQ ya no SSR").
5. **IQ_CAPTURE:** ACQUIRE (FETCH_IQ=0, N muestras) → FETCH (FETCH_IQ=1) → leer memshare (client-id 1,
   5MiB) en address+size del REPACK. Probar IQ_DATA_FORMAT=0/1 y SAMP_FREQ en Hz vs kHz.
6. **Dump de RAM** con RF cargado para extraer los cuerpos RFLTE (anclar por VAs seg27) y el pool DDR.
7. **Validar formato:** capturar un tono conocido, confirmar IQIQ signed LE 8/16-bit y la escala con
   RX_cal_offset + LNA_offset.

---

## 13. ROADMAP ACTUALIZADO (cerrar IQ RX, priorizado)

**P0 — Habilitar el canal en vivo (sin esto nada avanza):**
1. Implementar el peer DIAG/QRTR (canales CMD/DATA, handshake FEATURE/DIAGID/TX-MODE). Verificar que
   llegan respuestas por DATA y F3 SSID 23. *(Todo el contrato está en §3 — FACT.)*

**P1 — Cerrar los enteros de registro en vivo:**
2. TECH_ENTER(LTE) + COMMAND_CAPABILITY → CMD_MASK. Fijar command_id de RADIO_CONFIG/RX_MEASURE/
   IQ_CAPTURE/COMMAND_CAPABILITY. *(Wire byte-exact en §7 PASO B'.)*
3. Identificar el sub-cmd de FTM_SET_MODE cal barriendo FTM_COMMON + monitoreo de gates/SSR. *(§12a/b.)*

**P2 — Abrir los gates y aplicar carrier:**
4. FTM_SET_MODE cal → verificar `gp+0x740==2`, `gp+0x7000!=0` (dump o comportamiento).
5. RADIO_CONFIG(BAND+EARFCN+BW+RX_CARRIER) → verificar `0xca79c494!=0`. *(§7 PASO C/D.)*

**P3 — Capturar IQ:**
6. IQ_CAPTURE ACQUIRE→FETCH, leer memshare. Resolver enums IQ_DATA_FORMAT / SAMP_FREQ empíricamente.
7. Validar formato de muestra (tono conocido) y la escala a dBm (cal RX). *(§8.)*

**P4 — Recuperar el código RFLTE (para entender internals / portar):**
8. Dump de RAM en vivo con RF cargado; extraer cuerpos RFLTE (anclar por VAs seg27) y pool DDR
   0x2a3xxxxx. Obtener el blob RFC/NV para los registros SDR735/RFFE. *(§12c.)*

**P5 — TX (futuro):**
9. Reusar la infraestructura (P0-P2) → TX_MEASURE/TX_CONTROL; probar tx_playback NR5G-MFTE; respetar
   los clamps SAR/MTPL (no bypasseables). *(§9.)*

---

### Índice de VAs clave (referencia rápida)
```
DIAG:      master 0xc0d55df8 · FTM wrapper 0xd8150ed8 · rsp gate 0xc0d36c44 · DATA canal gp+0x69a0
FTM wire:  ftm_cmd tbl 0xc37bc828 · router rango 0xd8157ec4 · RFTEST disp 0xd82714b4 · jump-tbl 0xc37c649c
Resolver:  command_id 0xd8272684 → 0xca79a850 · sub_command reg 0xcaad9d00
Unpackers: RADIO_CONFIG 0xd8183f30 · IQ_CAPTURE 0xd81893a4/0xd8189818 · COMMAND_CAPABILITY 0xd81849ec
IQ tablas: field-tbl 0xc37c0828 · vista interna 0xc906c8f8 · REPACK fmt 0xc37c0949
Estado:    session->0xc · tech flag 0xca7897b0 · commit 0xd81dfecc · callback 0xca733d10
Gates:     gp=0xcbf4f000 · gate(a) 0xcbf4f740 (==2) · gate(b) 0xcbf56000 (!=0) · accessor 0xd8284eac
SSR:       carrier ptr 0xca79c494 · getter 0xd827923c · setter 0xd8279264 · apply 0xd8273b2c · activate 0xd81bd018
RF strings:carrier_activate 0xce6e72c0 · ftm_mc_wakeup 0xce6e08a8 · iq_prop_action 0xce6ded20/68 · SDR735 0xce6fae60
```
