# q6zip / dlpager RX-swap decompression — 2º intento (metódico, con tool leída a fondo)

Build: **MPSS.HI.4.3.4 (SM6375 / Moto G82 5G)**
ELF reensamblado: `/tmp/modemre/modem_full.elf` (87 MB, machine=0xa4 = QDSP6/Hexagon)
Blob crudo (solo lectura, extraído en dir nuevo): `/tmp/modemre2/modem.b00..b34 + modem.mdt`
Herramienta: `nlitsme/qualcomm-q6zip` en `/tmp/q6zip` (q6unzip.py, deltauncomp.py, unclade.py, ELF.py — TODAS leídas).

Leyenda: **FACT** = evidencia estática directa verificada aquí · **INFERENCE** = deducción de arquitectura Qualcomm · **UNKNOWN** = no determinable estáticamente.

---

## 0. RESULTADO (leer primero, honesto)

- **NO se pudo descomprimir el código del dispatcher FTM `0xd8150ed8`.** El motivo, ahora
  probado con más rigor que el pass anterior: la metadata q6zip RX (diccionarios + tabla
  de punteros de página) del *RX swap pool* **no existe en ninguna forma parseable en el
  ELF estático** — se construye/carga en boot en RAM que no está en ningún program header.
  `seg_code_dec.bin` **NO producido** (hacerlo a ciegas daría ruido). **FACT.**
- **Confirmé y CORREGÍ un matiz del pass anterior**: el descriptor de `0xc8d0a338`
  (remap 1:1) que el pass previo citó **es el pool RW/delta**, no el pool RX de código.
  Su ventana destino es **`0xd4400000`**, NO la `0xd8xxxxxx` del dispatcher. El pool RX
  (destino `0xd8xxxxxx`) tiene un descriptor SEPARADO que **no aparece en el ELF** (es
  runtime). Esto refuerza la conclusión, no la debilita. **FACT.**
- **Descubrimiento nuevo relevante**: este firmware usa **CLADE** (descompresión por
  *hardware*, random-access, sin tabla de punteros software) para el código RX residente,
  ADEMÁS del dlpager software (q6zip_sw) para el overlay `0xd8xxxxxx`. Por eso NO hay
  tabla de punteros de página en el ELF: CLADE no la necesita (traduce vaddr→dato
  comprimido por aritmética de registros HW). **FACT** (secciones `.clade.*` + registros
  `CladeCompPDX`/`CladeExcHiPDX` en el XML de QuRT).
- **El código residente b10/b13 SÍ es Hexagon válido y NO está comprimido** — pero el
  dispatcher FTM concreto no está ahí, está en la ventana paginada `0xd8xxxxxx`. **FACT.**

---

## 1. La herramienta q6zip/delta/clade — formato entendido a fondo (FACT, del código)

### 1a. q6unzip.py (RX / código)
`Q6zipSegment.__init__` (q6unzip.py:589) espera, EN EL BOUNDARY del segmento:
```
[npages:u16][version:u16] [dict1: dict1size words][dict2: dict2size words]
[ptrs: npages words (VAddrs de cada página comprimida)] [datos bit-packed]
elfbase = ptrs[0] - datastart_offset      # el tool DERIVA el base del primer puntero
```
- `--dictsize` = dict1size+dict2size en words (default 0x4400 = 0x400+0x4000). El split
  (`splitdictsize`, q6unzip.py:617) exige que dictsize tenga **exactamente 2 bits set**
  (`2^a+2^b`, p.ej. 0x400+0x4000). **FACT.**
- `-o/--offset` = qué página descomprimir (NO es el base). `-O/--baseoffset` = offset al
  segmento. **El pass anterior usó `-o 0xcc000000` que es `--offset`, no el base** — pero
  eso es secundario: el problema real es que seg26 no tiene el header en su boundary.
- Decodificador: 15 opcodes variable-length (DICT1/DICT2_MATCH, NO_MATCH literal, SEQ,
  LOOKBACK, MASK, LOOKBACKMASK). Requisito: **dict1, dict2 y la ptr-list**. Sin ellos la
  herramienta no arranca.

### 1b. deltauncomp.py (RW / datos)
`[npages:u16][version:u16][ptrs: npages words][datos]`. Opcodes 2-bit:
`00`=0, `01 aa`=anchors[aa], `10 aa d10`=anchor+delta, `11 w32`=literal. **FACT.**

### 1c. unclade.py (CLADE / código, HW)
CLADE = 3 diccionarios de 0x2000 bytes (2048 words c/u) al final del segmento clade;
por cada word de salida: 2-bit opcode + 11-bit dict-index + "missing bits" reconstruidos,
o literal 32-bit. **Es word-addressable / random-access (por eso es HW).** No usa tabla de
punteros de página. **FACT** (`CladeDecompressor`, unclade.py:82).

---

## 2. Layout de segmentos (del .mdt, machine=0xa4 Hexagon) — FACT

`modem.bNN` mapea 1:1 al índice de program header NN. Relevantes:

| seg | vaddr      | filesz    | memsz     | perm | qué es |
|-----|------------|-----------|-----------|------|--------|
| 10  | 0xc0a80000 | 0x029ab14 | 0x029b000 | R E  | **código residente Hexagon (NO comprimido)** ent≈7.0 |
| 13  | 0xc0d36000 | 0x031f1b0 | 0x0320000 | R E  | **código residente Hexagon (NO comprimido)** ent≈6.98 |
| 21  | 0xc3553000 | 0x0d315f4 | 0x5616000 | R    | rodata (strings FTM, tablas dispatch). NO comprimido |
| 24  | 0xc9138000 | **0**     | 0x2e17000 | RW   | **BSS/target de descompresión** (48 MB, filesz=0) |
| 26  | 0xcc000000 | 0x244a040 | 0x244b000 | RW   | **backing comprimido RW/delta** ent=7.734 |
| 27  | 0xce480000 | 0x011922f | 0x011a000 | R    | zlib→QSHRINK4 (ya descomprimido: seg27_dec.bin) |

**El dispatcher `0xd8150ed8` NO está cubierto por NINGÚN program header (ni filesz ni
memsz).** Verificado programáticamente: no hay segmento que solape `0xd8000000..0xd9000000`.
**FACT.**

---

## 3. Evidencia CONCRETA de por qué NO se puede descomprimir estáticamente

### 3.1 seg26 empieza con datos comprimidos crudos, SIN header (FACT)
Hexdump `modem.b26` offset 0 (word LE):
```
+000000: d62015b2 2808298a b7c30b09 e000bb82
+000010: a015b30d 14c00498 860c9404 5dc15be0
```
Interpretado como header q6zip/delta: `npages=0x15b2=5554, ver=0xd620`; los supuestos
"ptrs" (`2808298a b7c30b09 ...`) son alta entropía, **no monótonos, no VAddrs**. => NO es
un header válido en offset 0. **FACT.**

### 3.2 El descriptor real de dlpager en el ELF es del pool RW/delta, ventana 0xd4 (FACT)
En rodata `0xc8d0a338` (leído del ELF):
```
c8d0a338: cc000000   comp_start   (= seg26 base)
c8d0a33c: ce13a000   comp_end     (size 0x213a000)
c8d0a340: d4400000   remap_start  <-- ventana DESTINO = 0xd4400000, NO 0xd8xxxxxx
c8d0a344: d6539000   remap_end    (size 0x2139000)
c8d0a348: 00c00000
```
`comp_size(0x213a000) ≈ remap_size(0x2139000)` → remap 1:1 = **pool RW/delta**. El código
que lo consume (desensamblado en `c0db4024`+, seg13) es init/logging de dlpager
(llamadas a printf `c0992904`); en `c0db41a4` hace `immext(#0xd8000000); r2=add(r2,
##-0x28000000)` (= +0xd8000000) sólo para **formatear un mensaje**, no para paginar.
Barrido de TODO el ELF: **este es el ÚNICO descriptor comp/remap presente**. No hay
descriptor con destino `0xd8xxxxxx` + fuente comprimida. **FACT.** → el descriptor del
pool RX (código, destino 0xd8) se arma en boot en BSS (no en el ELF).

### 3.3 NO existe tabla de punteros de página en ningún segmento (FACT)
- Barrido de tablas monótonas crecientes (≥200 entradas, delta<0x8000) apuntando al rango
  de seg26 (`0xcc..0xce48`), a la ventana `0xd8xxxxxx`, o a la `0xd4xxxxxx`: **0 tablas
  reales** (los únicos "hits" son arrays de punteros a funciones/strings en rodata que
  apuntan a `0xc3xx`, no a datos comprimidos).
- Barrido de tabla de OFFSETS (relativa, deltas 0x100..0x1800, ≥1000 entradas): los hits
  son sólo **runs de ceros** (BSS), sin deltas reales.
- Barrido de header q6zip AUTO-CONSISTENTE (npages 300..40000 + dict1[0]==0 + 9 tamaños de
  dict con 2 bits set + ptrs monótonos con delta<0x1000) sobre seg26: **0 hits**. Sobre
  TODOS los `.bNN`: los hits (b04/b15/b19/b21/b23/b28/b33) tienen `p0` = basura
  (0xffffffff, 0x30303030="0000", 0xbfc3…) y `ver` absurdos → falsos positivos en regiones
  de strings/ceros. **Ninguno es una ptr-list de VAddrs.** **FACT.**

### 3.4 seg26 no tiene la firma q6zip "0xff en inicio de página" (FACT)
q6zip RX marca ~la mitad de las páginas con byte inicial 0xff (README + opcode END_BLOCK
`0xff`). En seg26: **0 de 255 páginas** (0x1000) empiezan con 0xff. => seg26 no es el RX
q6zip; es el pool RW/delta (coincide con 3.2). **FACT.**

### 3.5 Este firmware usa CLADE (HW) para el RX — no q6zip-SW — lo que EXPLICA la ausencia
Strings del XML de config QuRT embebido en el ELF (file 0x27c4xxx):
```
<section ... name=".clade.dict"  physpool="CLADE_DICT" .../>
<section clade_register="clade_comp" ... name=".clade.comp" .../>
<section clade_register="clade_exception_high" name=".clade.exception_high"/>
<section ... name=".clade.metadata" .../>
<clade_base_paddr value="0x400000000"/>
<section mapping="rx" name=".candidate_compress_section"/>       # RX q6zip pool
<section mapping="rx" name=".rw_candidate_compress_section"/>     # RW delta pool
```
+ símbolos `dlpager_q6zip_iface.c`, `q6zip_sw.c`, `DL_PAGER_RX_SWAP_POOL`.
CLADE descomprime por HW con registros `CladeCompPDX`/`CladeExcHiPDX`/`CladeRegion`
(paddr base `0x400000000`) — **traducción aritmética vaddr→comprimido, sin ptr-list
software**. Por eso NINGÚN barrido encuentra tabla de páginas: no hay ninguna que
encontrar. **FACT.** El overlay demand-paged `0xd8xxxxxx` (dispatcher FTM) lo sirve
dlpager-SW cuya metadata (dict+ptrtable) reside en RAM de boot, ausente del ELF estático.

### 3.6 b10/b13 SÍ son código Hexagon válido pero NO contienen el dispatcher (FACT)
`llvm-objdump --triple=hexagon` (vía el ELF reensamblado) desensambla b10/b13 limpiamente
(inmediatos, packets, jumps coherentes; p.ej. c0d36000: `r2=add(r20,#-0x7fff)` …). Pero
b10/b13 sólo REFERENCIAN direcciones `0xd8xxxxxx` (llamadas/loads al overlay paginado);
el cuerpo del dispatcher no está en ellos.

---

## 4. Confirmación del camino FTM en lo que SÍ es estático (FACT)

### 4.1 Tabla de dispatch FTM (rodata seg21, `0xc37bd1e8`) — leída del ELF
Formato por entrada: `{cmd_id:u16, cmd_id:u16, handler:u32}`. **77 entradas, TODAS con
handler = `0xd8150ed8`** (dispatcher único paginado). Extracto:
```
c37bd1ec: d8150ed8   (cmd 0x0000)
c37bd204: d8150ed8   (cmd 0x0028)
c37bd208: 00270027 -> c37bd20c: d8150ed8   (cmd 0x0027 = FTM_LTE)   <-- LTE aquí
c37bd218: 007a007a ; c37bd220: 007b007b ; c37bd228: 007e007e ...
```
**FACT**: `ftm_cmd_id 0x27` (LTE) existe en la tabla y va al dispatcher `0xd8150ed8`. Por
eso 0x27 NO devuelve 0x13 (BAD_CMD) sino 0x14 (BAD_PARM) cuando el sub/estado no valida.

### 4.2 La string-DB QSHRINK confirma el dispatcher paginado
Tabla de mensajes en `0xc35558f8`: `{hash, nargs=4, fmt="ftm_common_print_msg: %s",
file="ftm_common_dispatch.c"}`. Sólo hay **1** referencia estática a `ftm_common_dispatch.c`
(va `0xc37bda8d`), y es este registro de mensaje — el CÓDIGO que lo emite es paginado.
Existen además `ftm_lte_common_dispatch.c` (va c3569e11) y el módulo tech-enter
`ftm_rf_debug_tech_enter_exit.c` (pass previo). **FACT.**

---

## 5. Lo pedido sobre el dispatch FTM: estado honesto

Objetivo: leer en `ftm_common_dispatch`/`ftm_lte_rex_dispatch` (target `0xd8150ed8`):
qué sub_command hace el tech-enter bajo 0x27, el enum numérico de TECH (LTE), y la
condición que valida antes de devolver 0x14.

- **NO obtenible estáticamente de esta imagen.** Toda esa lógica (comparación del enum
  TECH, selección de sub_command handler, gate que dispara BAD_PARM 0x14) vive en el
  cuerpo del dispatcher `0xd8150ed8`, en la ventana paginada `0xd8xxxxxx`, cuyo backing
  comprimido y metadata (dict + ptr-table) **no están en el ELF** (secciones 3.2–3.5).
  **UNKNOWN estático.**
- Lo que SÍ queda FIJO (FACT, secciones 4 y pass previo tech_enter_decoded.md):
  - `ftm_cmd_id LTE = 0x27` (en tabla dispatch).
  - Tech-enter = módulo `ftm_rf_debug_tech_enter_exit.c`, TLVs `{SUB, TECH, SCENARIO}`
    (format-string UNPACK `[%3d][ %12s ][ %12d ]`).
  - `0x14 = DIAG_BAD_PARM_F` (el cmd se reconoce pero el sub/estado no valida → BAD_PARM
    antes del UNPACK).
- **Valor numérico de TECH (LTE) y número exacto del sub_command tech-enter: UNKNOWN
  estático.** Vías para cerrarlo: (a) EN VIVO vía COMMAND_CAPABILITY/CMD_MASK
  (procedimiento en `tech_enter_decoded.md` §7); (b) un **dump de RAM en vivo** que
  capture la ventana `0xd8xxxxxx` ya descomprimida (el `mssdump.elf` presente NO sirve:
  usa direcciones FÍSICAS 0x8b.., es la MISMA imagen estática, sin páginas 0xd8 — verificado).

---

## 6. ¿b10/b13 (código no comprimido) tienen algo útil? (FACT)

Sí, son Hexagon desensamblable, pero **NO contienen el dispatcher FTM** (§3.6). Contienen
código residente que LLAMA al overlay `0xd8xxxxxx` (p.ej. 70+ referencias a
`0xd8910588`, `0xd8b84a00`, etc.). El init de dlpager está en seg13 (`c0db4024`+) y sólo
logea los límites del pool RW/delta. No hay ruta estática desde b10/b13 hacia el cuerpo
del dispatcher LTE.

---

## 7. Qué se intentó en este pass (reproducible)

Scripts en `/tmp/modemre/`:
- `scan_ptrtable_v2.py` — tablas monótonas de VAddrs en 5 rangos destino → sólo arrays de
  punteros a rodata, ninguno a comprimido.
- `scan_meta.py` — tablas de offsets de página (deltas pequeños) → sólo runs de ceros.
- `scan_q6_selfconsistent.py` — header q6zip auto-consistente en seg26 y en todos los .bNN
  → 0 reales (sólo falsos positivos en strings/ceros).
- Delta de seg26 page0 (`deltauncomp`): produce salida estructurada (valores pequeños,
  patrones `0x1262xxxx`/`0x0c94xxxx`) → consistente con que seg26 es el pool RW/delta,
  pero SIN ptr-table no se puede indexar a páginas concretas ni sirve para el código RX.

---

## 8. Conclusión FACT / INFERENCE / UNKNOWN

**FACT (probado en esta imagen):**
- La herramienta q6zip/delta/clade y su formato están entendidos a fondo (dict+ptrlist+
  bit-packed; clade = 3 dicts + random-access HW).
- El dispatcher FTM `0xd8150ed8` NO está en ningún program header (ni file ni mem).
- El ÚNICO descriptor comp/remap del ELF (`0xc8d0a338`) es el pool RW/delta con destino
  `0xd4400000` (remap 1:1), NO el pool RX de código (`0xd8xxxxxx`).
- No existe tabla de punteros de página / header q6zip auto-consistente en NINGÚN segmento
  (barridos exhaustivos, 0 resultados reales).
- seg26 es el backing RW/delta (ent 7.734, sin firma 0xff de q6zip RX).
- Firmware usa CLADE (HW) + dlpager-SW; la metadata del RX swap se arma en boot (BSS),
  ausente del ELF → **descompresión estática del código RX imposible con esta imagen**.
- b10/b13 = código Hexagon residente válido, pero sin el dispatcher FTM.
- Tabla dispatch FTM: 77 entradas → `0xd8150ed8`; cmd 0x27 (LTE) presente.

**INFERENCE:**
- El pool RX (código, destino 0xd8) probablemente usa q6zip-SW (`q6zip_sw.c`,
  `dlpager_q6zip_iface.c`) con dict+ptrtable construidos en boot; y/o CLADE HW para el
  código residente. Ambos dejan la ventana 0xd8 fuera del ELF estático.

**UNKNOWN (sólo en vivo o con dump de RAM real de la ventana 0xd8xxxxxx):**
- Cuerpo del dispatcher `0xd8150ed8` (lógica sub_command, comparación del enum TECH, gate
  del 0x14).
- Valor numérico de TECH para LTE (y NR5G).
- Número exacto del sub_command de tech-enter bajo 0x27.

**Entregable de código descomprimido (`/tmp/modemre/seg_code_dec.bin`): NO producido**, con
justificación probada arriba. Producirlo a ciegas daría ruido no desensamblable.
