# FTM sub_command MAP bajo ftm_cmd 0x27 (LTE) — SM6375 / Moto G82 5G
Build: MPSS.HI.4.3.4-00494-MANNAR_GEN_PACK-1.24452.133
Base código descomprimido: `/tmp/modemre/clade_dec_full.bin` (VA 0xd8000000). `dis.sh <va> <len>`.
Rodata estática: `modem.b21` (VA 0xc3553000), leída con `rd.py <va> <n>`.

Leyenda: **FACT** = leído del disasm/bytes (VA citada) · **INFERENCE** = deducción con base · **UNKNOWN** = sólo en vivo / en RAM.

---

## 0. TL;DR — LA RESPUESTA A TU PREGUNTA CENTRAL (Q1/Q2)

**Bajo ftm_cmd 0x27 hay UN SOLO campo sub_command (u16 @0x04 del paquete DIAG), pero su
espacio está PARTICIONADO POR RANGO. NO son dos tablas indexadas por el mismo número:
es UN número cuyo RANGO selecciona el framework.** **FACT** (router 0xd8157ec4).

```
 sub_command @0x04     framework                          tabla dispatch
 ─────────────────     ──────────────────────────        ─────────────────────
 0x0000 – 0x0FFF       RFDEBUG / LTE-legacy               @0xca65b414 (stride 0xc)   <- TECH_ENTER = 0x000D
 0x1000 – 0x3FFF       RFTEST multi-tech (RADIO_CONFIG,    tablas @0xc37c649c/645c/6440
                       RX_MEASURE, IQ_CAPTURE, CMD_CAP…)
 0x4000 – 0x6FFF       (reservado → error)                —
 0x7000 – 0x702C       RFTEST "grupo 0x70" (otra familia) tabla @0xc37c5a3c
```

→ **TECH_ENTER (RFDEBUG, sub 0x0D)** y **RADIO_CONFIG/IQ_CAPTURE (RFTEST)** NO comparten
tabla: caen en RANGOS distintos del MISMO campo @0x04. TECH_ENTER está en 0x000D
(rango RFDEBUG). RADIO_CONFIG/RX_MEASURE/IQ_CAPTURE/COMMAND_CAPABILITY están en el rango
**0x1000–0x3FFF** (RFTEST), sub-particionado por el BYTE ALTO (@0x05): 0x10xx, 0x20xx, 0x30xx.

**Todo bajo el MISMO ftm_cmd @0x02 = 0x27.** No hace falta otro ftm_cmd. **FACT.**

---

## 1. LAYOUT DEL PAQUETE — CONFIRMADO Y CORREGIDO [FACT]

El router de nivel-1 (0xd814e034) lee del paquete DIAG crudo `r17`:
```
d814e034: r1 = memub(r17+#0x1)                 ; subsys_id  == 0x0b        (gate)
d814e050: r3 = memub(r17+#0x2); r4 = memub(r17+#0x3)  ; ftm_cmd = u16 @0x02   <<<
d814e0b4: r13 = memw(r3<<2 + ##0xc37bc828)      ; jump-table por ftm_cmd
d814e0f8: jumpr r13                             ; -> arm del ftm_cmd (0x27 -> 0xd814e534)
```
El arm 0x27 (0xd814e534) llama al handler LTE `0xd8157ec0/ec4`, que lee:
```
d8157ed8: r2 = memub(r17+#0x4) | memub(r17+#0x5)<<8   ; sub_command = u16 @0x04  <<<
```
**→ El paquete es EXACTAMENTE el que ya te funciona:**
```
4b 0b  <ftm_cmd:u16 @0x02>  <sub_command:u16 @0x04>  <num_tlv:u16 @0x06>  <TLVs...>
```
- @0x00 `4b` = DIAG_SUBSYS_CMD_F. **FACT**
- @0x01 `0b` = subsys FTM. **FACT**
- @0x02 `27 00` = ftm_cmd = LTE. **FACT** (0xd814e050 → jump-table 0xc37bc828[0x27]=0xd814e534)
- @0x04 `<sub_command LE>` = selector RANGO-particionado. **FACT** (0xd8157ed8)
- @0x06 = num_tlv (u16). Cuerpo TLV a continuación.

**NOTA sobre los reportes viejos:** el modelo "@0x02 = DIAG-subcmd 0x14 fijo y @0x0a =
ftm_cmd" era de un path DISTINTO (el registrar 0xd8150ed8 del subsys). El path REAL de
comando LTE es el jump-table 0xc37bc828 indexado por @0x02 = 0x27. Tu formato corto es el
correcto. **FACT.**

---

## 2. EL ROUTER POR RANGO — DECODE EXACTO (FACT, 0xd8157ec4)

```
d8157ed8: r2 = subcmd (u16 @0x04)
d8157ee4: if (r2 >  0xfff)  continue    else jump 0xd8157f8c  -> RFDEBUG/legacy  (0xd8157fe4)
d8157ef4: if (r2 >  0x3fff) continue    else (0x1000..0x3fff): r3=0xd8271480; jump 0xd8157fb8 callr r3
d8157f08: if (r2 >  0x4fff) continue    else (0x4000..0x4fff): ERROR (0xd8157f5c, "cmd fuera de rango")
d8157f14: if (r2 >  0x5fff) continue    else (0x5000..0x5fff): ERROR (0xd8157f6c)
d8157f20: if (r2 >  0x6fff) continue    else (0x6000..0x6fff): ERROR (0xd8157f7c)
d8157f28: (r2 >= 0x7000):  r3=0xd822176c; jump 0xd8157fb8 callr r3   -> familia 0x70xx
```
- **0x000–0xFFF → 0xd8157fe4** (RFDEBUG). Éste re-decodifica y despacha por `@0xca65b414`
  (stride 0xc, gate sub<=0x15). **AQUÍ vive TECH_ENTER = 0x000D.** **FACT** (0xd816d2e8).
- **0x1000–0x3FFF → 0xd8271480/4b4** (RFTEST LTE). Sub-particiona por byte alto @0x05. **FACT.**
- **0x7000–0x702C → 0xd822176c** (otra familia, gate `&0xff00==0x7000`, low<=0x2c). **FACT.**

---

## 3. RFTEST LTE (0x1000–0x3FFF) — SUB-PARTICIÓN POR BYTE ALTO [FACT, 0xd82714b4]

```
d82714c8: r3 = memub(r16+#0x5)      ; byte alto del sub_command
          r2 = memub(r16+#0x4)      ; byte bajo (índice)
d82714cc: if (r3 == 0x30): gate r2<=0x6 ; jump-table @0xc37c6440  (7 entradas)   -> 0x3000..0x3006
d82714e4: if (r3 == 0x20): gate r2<=0xf ; jump-table @0xc37c645c  (16 entradas)  -> 0x2000..0x200f
d82714fc: if (r3 == 0x10): gate r2<=0x17; jump-table @0xc37c649c  (24 entradas)  -> 0x1000..0x1017
          else -> error 0xd827176c
```
Las 3 jump-tables están en **b21 (rodata estática)** — leídas literalmente (§3.1). Cada
entrada apunta a un STUB (0xd8271xxx) que carga `r2 = ##<handler>` y salta a
`0xd8271630` (`callr r2`). Los handlers reales viven en 0xd86fxxxx (unpackers LTE). **FACT.**

### 3.1 Tabla 0x10xx — @0xc37c649c (24 slots) → stub → handler  [FACT: tabla y handlers]
```
sub_cmd  slot  stub         handler(unpacker)   nota
0x1000    0    0xd8271630   (común/no-op)       slot vacío -> callr directo (probable reservado)
0x1001    1    0xd827176c   (error)             slot inválido (log "unsupported")
0x1002    2    0xd8271548   0xd86fd0f4
0x1003    3    0xd8271558   0xd86fd230
0x1004    4    0xd8271568   0xd86fd328
0x1005    5    0xd8271578   0xd86fd474
0x1006    6    0xd827170c   0xd8271c94
0x1007    7    0xd82715b0   0xd86fdb88
0x1008    8    0xd8271588   0xd86fd5c8
0x1009    9    0xd8271598   0xd86fd80c
0x100a   10    0xd82715a4   0xd86fda68
0x100b   11    0xd82715f8   0xd86fe120
0x100c   12    0xd8271604   0xd86fe1e4
0x100d   13    0xd8271610   0xd86fde80
0x100e   14    0xd82715bc   0xd86fe0a4
0x100f   15    0xd82715c8   0xd86fdf94
0x1010   16    0xd8271718   0xd82722c4
0x1011   17    0xd8271724   0xd8271b64
0x1012   18    0xd8271730   0xd8271bcc
0x1013   19    0xd82715d4   (error 0xd827176c-like)
0x1014   20    0xd82715e0   0xd86fe300
0x1015   21    0xd82715ec   0xd86fe558
0x1016   22    0xd827161c   0xd86fe60c
0x1017   23    0xd8271628   0xd86fe7e0
```
### 3.2 Tabla 0x20xx — @0xc37c645c (16 slots)  [FACT]
slots 0=común, 1=error, 2..15 = handlers 0xd8288xxx / 0xd8289xxx (familia distinta de campos).
### 3.3 Tabla 0x30xx — @0xc37c6440 (7 slots)  [FACT]
`0xd8271718,24,30,0xd8271630,0xd827174c,5c,3c` → COMPARTE handlers con 0x1010/0x1011/0x1012.

---

## 4. IDENTIDAD NOMBRE↔SLOT — LO QUE ES FACT vs UNKNOWN (honesto)

**FACT (nombres de comando RFTEST y sus tablas de campos, en b21):**
Los UNPACKERS multi-tech que referencian las tablas de field-handlers (índice = field_id):
```
  RADIO_CONFIG        unpacker 0xd81828f0   field-tbl @0xc37c0268   fmt-str @0xc37c0320
  COMMAND_CAPABILITY  unpacker 0xd8184fe8   field-tbl @0xc37c03b4   fmt-str @0xc37c03cc
  RX_MEASURE          unpacker 0xd8185b1c   field-tbl @0xc37c0478   fmt-str @0xc37c0590
  WAIT_TRIGGER        unpacker 0xd8187d34   field-tbl @0xc37c0644   fmt-str @0xc37c0664
  TX_CONTROL          unpacker 0xd81889bc   field-tbl @0xc37c078c   fmt-str @0xc37c07d8
  IQ_CAPTURE          unpacker 0xd8189818   field-tbl @0xc37c0828   fmt-str @0xc37c08f4
  TX_MEASURE          unpacker 0xd818a66c   field-tbl @0xc37c0a10   fmt-str @0xc37c0dc4
```
(field-tbl = array de punteros a field-handlers, indexado por field_id — confirma los
field_ids del §6.)

**UNKNOWN estático (el "slot 0x10xx → nombre" exacto):**
El mapa slot→comando se resuelve por el `command_id` interno que cada handler 0xd86fxxxx
pasa a `0xd8272684`, el cual indexa la tabla-maestra **@0xca79a850** (RAM, `memw(##0xca79a850)`,
`addasl(base,id,#2)+0x34` = puntero al field-table del comando). Esa tabla se **puebla en
runtime** (alloc en 0xd8272368) desde descriptores construidos por las init
(0xd850fad8 / 0xd8168xxx), que leen el sub_command del descriptor (`memub(desc+0x12..0x14)`)
— NO hay inmediato estático. **FACT del mecanismo** → **el número exacto slot↔nombre es
UNKNOWN estático** (idéntica conclusión que los 3 pases previos, ahora con la CAUSA precisa:
tabla @0xca79a850 en RAM). Se cierra en vivo con COMMAND_CAPABILITY (§5).

**INFERENCE (orden de declaración, para barrido dirigido):** los slots 0x1002.. siguen el
orden de las tablas de campos consecutivas (g13=RADIO_CONFIG primero). Barrido recomendado:
probar `0x1002, 0x1003, 0x1004, …` con num_tlv=0 y observar cuál pide TECH_MODE/BAND (=RADIO_CONFIG)
y cuál pide RX_CARRIER/FETCH_IQ (=RX_MEASURE/IQ_CAPTURE).

---

## 5. COMMAND_CAPABILITY — cómo cerrar el enum en UNA respuesta [FACT del formato]

Módulo `ftm_rf_test_command_capability.c`. Tabla de campos **@0xc37c03b4** (unpacker 0xd8184fe8).
Campos (field_id = índice; FACT del pass previo, grupo 22 @0xc906ce18):
```
  field 1 = QUERY_COMMAND     (u32)  -> comando a consultar (0xFFFFFFFF = todos)
  field 2 = QUERY_PROPERTY    (u32)
  field 3 = CMD_MASK          (rsp)  -> BITMAP: bit N=1 => sub-slot N soportado
  field 4..7 = PROPERTY_MASK_0..255  (rsp)
```
**sub_command de COMMAND_CAPABILITY: está en el rango 0x10xx (RFTEST), UNKNOWN el slot exacto
(candidatos por orden: uno de 0x1002..0x1017).** Procedimiento en vivo:
```
1) Barrer sub = 0x1002..0x1017 con num_tlv=0. El que responda con un REPACK que trae
   CMD_MASK (0x%8x no-cero) = COMMAND_CAPABILITY.
2) Con COMMAND_CAPABILITY hallado, mandar TLV {field 1 QUERY_COMMAND = 0xFFFFFFFF}.
   La rsp CMD_MASK: cada bit en 1 = un slot 0x10xx válido. Eso te da el enum COMPLETO.
3) Para nombrar cada bit N: QUERY_COMMAND=N -> PROPERTY_MASK (soporta_IQ/TX/necesita_enter).
```
Esto resuelve RADIO_CONFIG/RX_MEASURE/IQ_CAPTURE de forma definitiva sin más RE. **FACT.**

---

## 6. FIELD-IDS OBLIGATORIOS — RADIO_CONFIG e IQ_CAPTURE

Los field-ids son **FACT** (índices en las field-tbl @0xc37c0268 / @0xc37c0828, cada entrada
es un field-handler => el field_id existe). El subconjunto "obligatorio" (el que causa
status 0x14 si falta) lo valida el unpacker en runtime: **NO hay bitmap de required estático**
(FACT: las tablas son sólo arrays de handlers). Por tanto los "obligatorios" son **INFERENCE**
(semántica RF) — confirmar en vivo agregando TLVs hasta que 0x14 pase a OK.

### RADIO_CONFIG (grupo 13) — field-tbl @0xc37c0268 [field_ids FACT]
Mínimos probables (INFERENCE):
```
  TECH_MODE  = 25 (0x19)   val 1 = LTE          (obligatorio; sin tech -> estado 0x7 -> 0x14)
  BAND       =  5
  CHANNEL    =  6          (EARFCN)   ó CENTER_FREQ = 12 (0x0c) / 21 (0x15)
  BANDWIDTH  =  7
  (RX_CARRIER = 1 y/o RFM_DEVICE si el build lo exige)
```

### IQ_CAPTURE / RX_MEASURE (grupo 14) — field-tbl @0xc37c0828 / @0xc37c0478 [field_ids FACT]
Mínimos probables para captura (INFERENCE):
```
  RX_CARRIER      =  1
  RX_AGC          =  4
  RX_MODE         =  7
  IQ_CAPTURE_TYPE = 39 (0x27)
  NUM_OF_SAMPLES  = 14 (0x0e)
  SAMP_FREQ       = 16 (0x10)
  IQ_DATA_FORMAT  = 15 (0x0f)
  FETCH_IQ        = 13 (0x0d)   = 1  (dispara/recupera)
```

---

## 7. FLAG tech_entered / condición 0x14 [FACT del mecanismo]

Estado per-tech en `memb(ctx + tech<<3)` con base **@0xca7897b0** (= tabla RFTEST @0xca789780+0x30).
Gate (0xd81e5d60): `estado == 0x7` (tech "no-entrada", el índice else de 0xd8169ec0) → error 0x10
→ status **0x14 (DIAG_BAD_PARM)**. TECH_ENTER (RFDEBUG sub 0x0D, TECH=1=LTE) puebla ese estado y
lo saca de 0x7 → RADIO_CONFIG/IQ_CAPTURE dejan de dar 0x14. **El orden importa.** **FACT.**

---

## 8. SECUENCIA DE CAPTURA IQ — BYTE A BYTE

Header común: `4b 0b <ftm_cmd@0x02> <sub_command@0x04> <num_tlv@0x06> <TLVs>`.
ftm_cmd = `27 00` (LTE) en todos. TLV = `<field_id:u16> <len:u16> <value...>` (u32 típico).

### PASO 0 (recomendado) — COMMAND_CAPABILITY para fijar los sub_command reales
```
4B 0B 27 00 <SUB_CAP:u16> 01 00  01 00 04 00 FF FF FF FF
                                  └field1 QUERY_COMMAND = 0xFFFFFFFF┘
```
`<SUB_CAP>` = barrer 02 10 .. 17 10 (LE de 0x1002..0x1017). Rsp con CMD_MASK ⇒ ese es CAP.
Del CMD_MASK obtenés los slots válidos y fijás RADIO_CONFIG/RX_MEASURE/IQ_CAPTURE reales.

### PASO 1 — TECH_ENTER (LTE)  [sub = 0x000D FACT · TECH=1 FACT · responde con status FACT]
sub_command 0x000D cae en el rango RFDEBUG (0x000–0xFFF). TLVs: SUB=1, TECH=2(u32)=1, SCENARIO=3.
```
4B 0B 27 00 0D 00 03 00 \
   01 00 04 00 00 00 00 00 \      ; TLV SUB      (field 1, len 4, val 0)
   02 00 04 00 01 00 00 00 \      ; TLV TECH=1   (field 2, len 4, val 1 = LTE)   <-- FACT
   03 00 04 00 00 00 00 00        ; TLV SCENARIO (field 3, len 4, val 0)
```
Concatenado:
```
4B 0B 27 00 0D 00 03 00 01 00 04 00 00 00 00 00 02 00 04 00 01 00 00 00 03 00 04 00 00 00 00 00
```
Esperar rsp DIAG con status byte = 0 (éxito). Si 0x14 → revisar TECH.

### PASO 2 — RADIO_CONFIG (tune LTE)  [sub RFTEST rango 0x10xx; fijar con CMD_MASK del paso 0]
Ejemplo con `<SUB_RC>` = LE de 0x10NN (barrer 0x1002.. hasta que pida TECH_MODE/BAND):
LTE B3, EARFCN 1575, CENTER_FREQ 1842500 kHz (0x001C1F84), BW 20000 kHz. num_tlv=5.
```
4B 0B 27 00 <SUB_RC:u16LE> 05 00 \
   19 00 04 00 01 00 00 00 \        ; TECH_MODE=25  val 1 (LTE)
   05 00 04 00 03 00 00 00 \        ; BAND=5        val 3 (B3)
   06 00 04 00 27 06 00 00 \        ; CHANNEL=6     EARFCN 1575
   0C 00 04 00 84 1F 1C 00 \        ; CENTER_FREQ=12  1842500 kHz
   07 00 04 00 20 4E 00 00          ; BANDWIDTH=7   20000 kHz
```

### PASO 3 — IQ_CAPTURE  [sub RFTEST rango 0x10xx; fijar con CMD_MASK del paso 0]
```
4B 0B 27 00 <SUB_IQ:u16LE> 04 00 \
   0E 00 04 00 00 40 00 00 \        ; NUM_OF_SAMPLES=14  16384
   10 00 04 00 00 C0 D4 01 \        ; SAMP_FREQ=16       30720000
   0F 00 04 00 00 00 00 00 \        ; IQ_DATA_FORMAT=15  0
   0D 00 04 00 01 00 00 00          ; FETCH_IQ=13        1
```
Rsp IQ_CAPTURE (REPACK) trae un puntero DDR/memshare + tamaño; las muestras NO viajan inline.

---

## 9. TABLA sub_command (@0x04) → destino, bajo ftm_cmd 0x27  [resumen]

| sub_command @0x04 | destino / framework | comando |
|---|---|---|
| 0x0000–0x0FFF | RFDEBUG (@0xca65b414, stride 0xc, gate<=0x15) | legacy/RF-debug |
| **0x000D** | RFDEBUG slot 13 | **TECH_ENTER_EXIT** (FACT) |
| 0x1000 | RFTEST 0x10xx slot0 | reservado/no-op (FACT estructura) |
| 0x1001 | RFTEST 0x10xx slot1 | inválido/error (FACT) |
| 0x1002–0x1017 | RFTEST 0x10xx (handlers 0xd86fxxxx) | RADIO_CONFIG / RX_MEASURE / IQ_CAPTURE / COMMAND_CAPABILITY / … (nombre↔slot = UNKNOWN estático → COMMAND_CAPABILITY) |
| 0x2000–0x200f | RFTEST 0x20xx (handlers 0xd8288/9xxx) | 2ª familia de campos |
| 0x3000–0x3006 | RFTEST 0x30xx (comparte con 0x101x) | 3ª familia |
| 0x4000–0x6FFF | — | ERROR (reservado) |
| 0x7000–0x702C | familia 0x70xx (@0xc37c5a3c) | otra familia |

---

## 10. FACT / INFERENCE / UNKNOWN — cierre con VAs

**FACT:**
- ftm_cmd = u16 @0x02; jump-table `0xc37bc828[ftm_cmd]` (0xd814e050/0b4/0f8). 0x27→0xd814e534→0xd8157ec4.
- sub_command = u16 @0x04 (0xd8157ed8).
- Router por RANGO en 0xd8157ec4: 0x000–0xFFF=RFDEBUG (0xd8157fe4); 0x1000–0x3FFF=RFTEST (0xd8271480/4b4);
  0x4000–0x6FFF=error; 0x7000+=0xd822176c.
- RFDEBUG despacha por `@0xca65b414` stride 0xc, gate sub<=0x15 (0xd816d2e8). TECH_ENTER=0x0D.
- RFTEST LTE sub-particiona por byte alto @0x05: 0x10→@0xc37c649c(24), 0x20→@0xc37c645c(16),
  0x30→@0xc37c6440(7) (0xd82714cc/e4/fc). Tablas leídas literal de b21.
- Stubs 0x10xx → handlers 0xd86fxxxx (extraídos, §3.1).
- Field-tables por comando (RADIO_CONFIG @0xc37c0268 … IQ_CAPTURE @0xc37c0828), unpackers 0xd818xxxx (§4).
- Resolución de field-table en runtime vía tabla-maestra @0xca79a850 (`addasl+0x34`), alloc 0xd8272368.
- Gate tech-state @0xca7897b0 (tech<<3); estado==0x7 → 0x10 → status 0x14 (0xd81e5d60).
- COMMAND_CAPABILITY: campos @0xc37c03b4; CMD_MASK bit N = slot válido; QUERY_COMMAND field 1.

**INFERENCE:**
- Orden de slots 0x1002.. sigue orden de declaración (RADIO_CONFIG primero) → barrido dirigido.
- Field-ids obligatorios de RADIO_CONFIG/IQ_CAPTURE (§6) por semántica RF (no hay bitmap estático).
- COMMAND_CAPABILITY es uno de 0x1002..0x1017.

**UNKNOWN (sólo en vivo / RAM):**
- slot 0x10xx EXACTO ↔ nombre (RADIO_CONFIG=0x10??, RX_MEASURE=0x10??, IQ_CAPTURE=0x10??,
  COMMAND_CAPABILITY=0x10??): tabla @0xca79a850 poblada en runtime desde descriptores con
  sub_command en `memub(desc+0x12..0x14)` (0xd850fc40 / 0xd8168080). Cerrar con COMMAND_CAPABILITY.
- Subconjunto exacto de field-ids obligatorios (validado por unpacker en runtime).
- Valor TECH exacto de NR5G (LTE=1 es FACT).

## 11. Reproducir
```
dis.sh 0xd814e034 0x90     # router nivel-1: ftm_cmd @0x02 -> jump-table 0xc37bc828
rd.py  0xc37bc828 0x30     # jump-table ftm_cmd (0x27 -> 0xd814e534)
dis.sh 0xd8157ec4 0x90     # router por RANGO del sub_command @0x04
dis.sh 0xd8271444 0x120    # dispatch RFTEST LTE (byte alto @0x05 -> 0x10/0x20/0x30)
dis.sh 0xd82714b4 0x60     # gates de rango + jump-tables
rd.py  0xc37c649c 0x18     # tabla 0x10xx (24 stubs)
rd.py  0xc37c645c 0x10     # tabla 0x20xx (16 stubs)
rd.py  0xc37c6440 7        # tabla 0x30xx (7 stubs)
dis.sh 0xd8272684 0x50     # resolver field-table por command_id (@0xca79a850)
dis.sh 0xd816d2e8 0x90     # RFDEBUG dispatch (@0xca65b414, TECH_ENTER=0x0D)
dis.sh 0xd81e5d40 0x40     # gate estado tech (==0x7 -> 0x14)
rd.py  0xc37c0268 30       # field-table RADIO_CONFIG
rd.py  0xc37c0828 30       # field-table IQ_CAPTURE
```
