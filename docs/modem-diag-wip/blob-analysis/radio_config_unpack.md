# RADIO_CONFIG — Unpacker de TLVs (SM6375 / Moto G82 5G)
Build: MPSS.HI.4.3.4-00494-MANNAR_GEN_PACK-1.24452.133
Código descomprimido: `/tmp/modemre/clade_dec_full.bin` (VA base 0xd8000000, cubre 0xd8000000..0xd8a00000).
Disasm completo: `/tmp/big10.txt`. Rodata estática: `rd.py <va> <n>` (b21 @0xc3553000, b23 @0xc8b6a000).

Leyenda: **FACT** = leído del disasm/bytes con VA · **INFERENCE** = deducción · **UNKNOWN** = sólo runtime/en vivo.

---

## 0. TL;DR — RESPUESTAS DIRECTAS

1. **sub_command @0x04 de RADIO_CONFIG = uno de 0x1002..0x1017 (rango RFTEST 0x10xx).
   El número exacto es UNKNOWN estático** porque el binding slot↔comando se hace en RUNTIME
   (tabla maestra @0xca79a850 poblada por init; el command_id se lee del descriptor con
   `memub(desc+0x0f..0x11)`, NO hay inmediato estático). **INFERENCE fuerte: NO es 0x1002.**
   Ver §1 y §4. Se cierra en vivo con COMMAND_CAPABILITY (§7).

2. **El unpacker de RADIO_CONFIG está IDENTIFICADO y DECODIFICADO:**
   - Función unpacker principal: **0xd8183f30** (FACT).
   - Entry del comando (wrapper): **0xd818327c** → **0xd8182ec0** (FACT).
   - Tabla de field-handlers (dispatch por field_id): **base 0xc37c0290, índice = (field_id-1)**,
     válido **field_id 1..36 (0x01..0x24)**; ≥37 → handler de error (FACT, loop 0xd81844d8/dc/f4).
   - Tabla de NOMBRES de campo: **0xc906c630** (índice = field_id), 60 nombres (FACT, §3).
   - Format-string F3: **[FTM.RFTEST][RADIO_CONFIG][UNPACK]:[%3d][ %12s ][ %12d ]** @0xc37c0320 (FACT).

3. **0x1002 NO crashea por ser RADIO_CONFIG.** El unpacker RADIO_CONFIG (0xd8183f30) trata
   num_tlv=0 de forma LIMPIA: retorna error 0x10 → status 0x14, **no** hace SSR (FACT: bail
   path 0xd818486c pone r24=0x10 y retorna). Por tanto **0x1002 es OTRO comando** (RX_MEASURE /
   IQ_CAPTURE / un comando que deref-ea la memoria compartida/DDR con num_tlv=0). Ver §6.

4. **Tune LTE mínimo que NO crashea:** paquete byte-a-byte en §8 (requiere TECH_ENTER previo).

---

## 1. RUTA COMPLETA sub_command → unpacker RADIO_CONFIG  [FACT con VAs]

```
DIAG 4b 0b → ftm_cmd@0x02=0x27 → jump-table 0xc37bc828[0x27]=0xd814e534 → 0xd8157ec4
  0xd8157ed8: sub_command = u16 @0x04
  router por rango (0xd8157ec4): 0x1000–0x3FFF → RFTEST (0xd8271480/4b4)
  0xd82714fc: byte alto @0x05 == 0x10 → jump-table @0xc37c649c (24 stubs), gate low<=0x17
     stub[N] (0xd8271xxx) → carga r2=##handler, salta 0xd8271630 (callr r2)
        handler 0xd86fxxxx = "entry genérico RFTEST":
          lee command_id del descriptor  memub(desc+0x0f), memub(desc+0x10), memub(desc+0x11)
          bound-check  command_id <= 0x31   (0xd86fd10c: cmp.gtu(r17,#0x31))   [FACT]
          resolver 0xd8272684:  base=memw(##0xca79a850); fld_tbl=memw(base+cmd*4 +0x34)  [FACT]
          → despacha al unpack fn del comando
RADIO_CONFIG unpack fn:  0xd8182ec0 (wrapper) → 0xd818327c → 0xd8183f30 (loop)   [FACT]
  usa tabla de field-handlers @0xc37c0290 (índice field_id-1)                      [FACT]
```

**Registro (por qué el número es runtime) [FACT]:**
- Init RADIO_CONFIG: **0xd8182640** (llamada desde el registrador maestro 0xd84aa03c).
  - aloca descriptor (0x18 bytes), pone `memb(desc+0x10)=0x12` (0xd8182698),
    `desc[0]=unpack=0xd8182ec0`, `desc[4]=repack=0xd8182f40`, `desc[8]=bufsz=0x174810`
    (0xd8182e70..94).
  - lo registra vía 0xd8182e50 → entra en la tabla maestra @0xca79a850.
- El command_id es un CONTADOR asignado en el orden de registro (no un inmediato del sub_command).
- El descriptor 0xd8263324 que los handlers 0x10xx referencian (0xd86fdc2c/0xd86fd14c) es un
  **puntero de código (callback de decode)**, no una tabla estática con el número.
→ **Conclusión (idéntica a 4 pases previos, ahora con el unpacker desensamblado):
   el sub_command 0x10NN exacto de RADIO_CONFIG es UNKNOWN estático. INFERENCE: NO 0x1002
   (ese crashea; RADIO_CONFIG no crashea con num_tlv=0).**

---

## 2. EL UNPACKER 0xd8183f30 — MECÁNICA EXACTA  [FACT]

```
0xd8183f30  call 0xd814e6ac ; allocframe(#0x1a40)         ; ~6.7KB de contexto en pila
0xd8183f34  p0 = cmp.eq(r1,#0)                            ; r1 = num_tlv
0xd8183f3c  if (p0) jump 0xd818486c                       ; num_tlv==0 → BAIL LIMPIO
0xd8183f50  p0 = cmp.eq(r2,#0); if (p0) jump 0xd818486c   ; ptr TLV nulo → BAIL LIMPIO
...init defaults (0xd8183fe4-0xd8184028): r13:12=-1 ; r29+0x24=2 ; bitmap r29+0x68=0 ; etc.
LOOP (0xd8184030):
  p2 = cmp.gtu(r17,r18)                                   ; r17=num_tlv, r18=idx
  field_id  = memub(r20+0)|memub(r20+1)<<8 [|<<16]        ; TLV[idx], stride 0xc
  if (field_id > 0x3b) jump 0xd8184880 (error)            ; gate 0..0x3b (59)  [FACT]
  name = memw(field_id<<2 + 0xc906c630)                   ; nombre F3
  value_lo = bytes[4..7]  (r22)                           ; 32-bit low
  value_hi = bytes[8..11] (r23)                           ; 32-bit high
  r13:12 = combine(r23,r22)                               ; VALOR 64-bit
  call 0xd80d77b8  → F3 [FTM.RFTEST][RADIO_CONFIG][UNPACK]
  → 2º gate/dispatch (0xd81844d8):
     r2 = field_id - 1
     if ((field_id-1) > 0x23) jump 0xd818471c (default)   ; válidos 1..36  [FACT]
     r8 = memw((field_id-1)<<2 + 0xc37c0290)              ; handler del campo  [FACT]
     jumpr r8                                             ; ejecuta handler(value)
  epilogo (0xd8184730): r20 += 0xc ; r18 += 1 ; loop
```

**Puntos clave:**
- **TLV parseado = registro FIJO de 0xc (12) bytes**: `field_id:u16 | pad:u16 | value:u64`.
  El pre-parser de wire (`<field_id:u16><len:u16><value[len]>`) normaliza cada TLV a este
  registro, zero-extendiendo el value a 64-bit según `len`. **FACT (stride 0xc, r20+=0xc).**
- **Bitmap de campos recibidos**: `memw(r29+0x68)` — RX_CARRIER (fid 1) hace
  `r3 |= asl(1, value)` (0xd8184518): marca el bit del CARRIER recibido. La fase de repack
  (0xd81847a0..) recorre estos bitmaps por-carrier (`memd(r4+0x1240)`) para decidir qué
  responder. **FACT.**
- **Índices per-carrier**: RX_CARRIER (fid1) guarda el índice en `memw(r29+0x20)`. CHANNEL,
  BANDWIDTH y varios usan ese índice como offset en arrays per-carrier, gate `idx<=2`
  (0xd81845b0: `cmp.gtu(idx,#0x2)`→error) o `idx<=0x29/0x41` en otros. **FACT.**

---

## 3. TABLA COMPLETA field_id → tipo → obligatorio? → nombre  [FACT nombres+dispatch]

Nombre: b23 @0xc906c630[field_id] (FACT). Handler: @0xc37c0290[field_id-1] (FACT).
Tipo: deducido del handler (INFERENCE salvo donde se cita el store). "Oblig?": ver §5 (INFERENCE
semántica — NO hay bitmap de required estático; validación runtime).

| fid | 0x  | nombre           | handler VA  | tipo/tamaño (value) | oblig? | nota (handler) |
|----:|-----|------------------|-------------|---------------------|--------|----------------|
| 1 | 0x01 | RX_CARRIER       | 0xd8184510  | u32 (índice carrier)| **SÍ** | set bit en bitmap r29+0x68; fija idx r29+0x20; **selector de carrier** |
| 2 | 0x02 | TX_CARRIER       | 0xd818450c  | u32 (índice carrier)| TX     | análogo, cadena TX |
| 3 | 0x03 | RFM_DEVICE       | 0xd818452c  | u16 (zxth), mask 0xff| cond  | per-carrier; bitsclr(r19,#0xff) |
| 4 | 0x04 | DEPRECATED       | 0xd818452c  | (=fid3)             | no     | obsoleto |
| 5 | 0x05 | BAND             | 0xd8184594  | u32 → mem r29+0x60  | **SÍ** | store directo del band |
| 6 | 0x06 | CHANNEL (EARFCN) | 0xd81845ac  | u32, per-carrier    | **SÍ** | array r29+0x19e8[idx], gate idx<=2 |
| 7 | 0x07 | BANDWIDTH        | 0xd81845a0  | u32 (enum), per-carr| **SÍ** | array r29+0x19e8[idx], gate idx<=2 |
| 8 | 0x08 | CONT_MODE        | 0xd81845d8  | u32, mask 0xfe      | no     | store r29+0x58 |
| 9 | 0x09 | SIG_PATH         | 0xd8184638  | u16 (zxth), idx<=0x29| no    | per-index r29+0x24 |
|10 | 0x0a | ANT_PATH         | 0xd818462c  | u32, per-index      | no     | array r29+0x1868[idx] |
|11 | 0x0b | USER_ADJ         | 0xd81846a4  | u32/s, per-index    | no     | idx<=0x29 |
|12 | 0x0c | CENTER_FREQ      | 0xd81846cc  | **u64** (memd)      | alt‡   | array r29+0x1080[idx*0x28]+0x8; **64-bit** |
|13 | 0x0d | ENABLE_XO        | 0xd81846f4  | u32, per-index      | no     | idx<=0x29 |
|14 | 0x0e | TOTAL_ADJ        | 0xd818471c  | u32 (default store) | no     | handler genérico (log+store) |
|15 | 0x0f | BURST_PATTERN    | 0xd8184600  | u64 (memd r29+0x10) | no     | mask 0xfe |
|16 | 0x10 | BEAM_ID          | 0xd818471c  | u32 default         | no     | genérico |
|17 | 0x11 | SUB_FRAME_CONFIG | 0xd818471c  | u32 default         | no     | genérico |
|18 | 0x12 | PLL_ID           | 0xd8184658  | u32, per-index      | no     | array r29+0x17c0[idx] |
|19 | 0x13 | TIME_US          | 0xd818471c  | u32 default         | no     | genérico |
|20 | 0x14 | INTER_FREQ       | 0xd818471c  | u32 default         | no     | genérico |
|21 | 0x15 | CENTER_FREQ(alt) | 0xd818471c  | u32 default         | alt‡   | 2º alias CENTER_FREQ (32-bit) |
|22 | 0x16 | SUB_TECH         | 0xd818471c  | u32 default         | INF    | genérico |
|23 | 0x17 | BWP_START_LOC    | 0xd818471c  | u32 default         | no     | genérico |
|24 | 0x18 | PATH_FILTER_TYPE | 0xd818471c  | u32 default         | no     | genérico |
|25 | 0x19 | **TECH_MODE**    | 0xd818471c  | u32 default         | **SÍ** | genérico (log+store); LTE=1 |
|26 | 0x1a | SCS              | 0xd818471c  | u32 default         | NR     | genérico (NR subcarrier spacing) |
|27 | 0x1b | LOAD_CODEBOOK    | 0xd818471c  | u32 default         | no     | genérico |
|28 | 0x1c | NDR_STATE        | 0xd818471c  | u32 default         | no     | genérico |
|29 | 0x1d | TECHNOLOGY       | 0xd818471c  | u32 default         | alt†   | alias de TECH_MODE (genérico) |
|30 | 0x1e | NETWORK_SIGNAL   | 0xd818471c  | u32 default         | no     | genérico |
|31 | 0x1f | RESERVED2        | 0xd818471c  | u32 default         | no     | genérico |
|32 | 0x20 | SELFTEST_TYPE    | 0xd818471c  | u32 default         | no     | genérico |
|33 | 0x21 | UE_POWER_CLASS   | 0xd818471c  | u32 default         | no     | genérico |
|34 | 0x22 | RESERVED3        | 0xd818471c  | u32 default         | no     | genérico |
|35 | 0x23 | NB_ID            | 0xd8184674  | u16/u8 (asl idx#1)  | no     | array r29+0x1710[idx*2] |
|36 | 0x24 | LANE_ID          | 0xd8184668  | u32/s, per-index    | no     | array r29+0x1710[idx] |
|37+| 0x25+| (TX_GAIN_ADJ…LANE)| ERROR      | —                   | —      | field_id>=37 → jump 0xd818471c-error / 0xd8184880 |

‡ CENTER_FREQ existe como fid 12 (u64, per-carrier) y alias fid 21 (u32). Es **alternativo** a
  CHANNEL (podés especificar EARFCN por CHANNEL o Hz por CENTER_FREQ).
† TECH_MODE (25) y TECHNOLOGY (29) son alias del selector de tecnología. LTE=1 (FACT del pass
  TECH_ENTER: TLV TECH=2, val=1=LTE).

**Nombres 37..59 (existen en la name-table 0xc906c630 pero NO tienen handler de valor en
0xc37c0290 → caen en el error handler si se mandan):** 37=TX_GAIN_ADJUSTMENT, 38=SRS_CS_TYPE,
39=SRS_SOURCE_CARRIER, 40=IF_PLL_UNLOCK_STATUS, 41=RF_PLL_UNLOCK_STATUS, 42=SUBSCRIPTION_INDEX,
43=TX_SHARING, 44=TX_PRORITY(sic), 45=BWP_ID, 46=BWP_BW, 47=TARGET_BWP_ID, 48=BWP_CENTER_FREQ,
49=BWP_PRIORITY, 50=BWP_CUSTOM, 51=SUB_CFG_ID, 52=TARGET_SUB_CFG, 53=ANT_NUM,
54=UE_COMBO_POWER_CLASS, 55=UL_TX_SWITCH_TYPE, 56=TX_SWITCH_SOURCE_CARRIE(R),
57=TUNE_BUILD_SCRIPT_TIME, 58=TX_CELL_ID, 59=NS_VAL_TYPE. **FACT (b23 @0xc906c630).**

> NOTA IMPORTANTE sobre numeración: este mapa (nombre @0xc906c630, handler @0xc37c0290) es el
> ESQUEMA REAL del unpacker. Difiere de reportes previos que listaban field_ids del grupo TLV
> g13 (@0xc906c8e8). El unpacker realmente usa **field_id 1..36** con estos nombres. **FACT.**

---

## 4. ¿CUÁL 0x10xx ES RADIO_CONFIG?  [INFERENCE + método de cierre]

- **FACT:** el unpacker RADIO_CONFIG es 0xd8183f30 (usa @0xc37c0290 / nombres @0xc906c630 /
  fmt @0xc37c0320). Se registra desde init 0xd8182640 con unpack=0xd8182ec0.
- **FACT:** el binding a un slot 0x10NN concreto ocurre en runtime (tabla @0xca79a850,
  command_id = contador de registro, leído de `memub(desc+0x0f..0x11)`).
- **FACT en vivo:** 0x1000→0x14, 0x1001→0x14 (RFTEST válido, piden TLVs), **0x1002→SSR sin TLVs**.
- **INFERENCE:** dado que RADIO_CONFIG **no** crashea con num_tlv=0 (bail limpio 0xd818486c),
  **RADIO_CONFIG ≠ 0x1002**. RADIO_CONFIG es otro slot 0x1003..0x1017 (el que, con num_tlv=0,
  responde 0x14 y con TECH_MODE/BAND/CHANNEL empieza a emitir [FTM.RFTEST][RADIO_CONFIG][UNPACK]).
- **CIERRE en vivo (definitivo):** COMMAND_CAPABILITY (§7) devuelve CMD_MASK y por
  QUERY_COMMAND/PROPERTY el nombre de cada slot. Alternativa: barrer 0x1003..0x1017 con
  num_tlv=1 {field 25 TECH_MODE=1} tras TECH_ENTER y ver cuál emite el F3 RADIO_CONFIG.

**sub_command @0x04 de RADIO_CONFIG = 0x10NN, N∈{3..17}. UNKNOWN estático · NO 0x1002 (INFERENCE).**

---

## 5. OBLIGATORIOS / DEPENDENCIAS  [INFERENCE semántica + FACT de gates]

**No hay bitmap de required estático** (FACT: las tablas son arrays de handlers). Pero el código
IMPONE dependencias por índice (FACT de gates), que definen el mínimo real:

1. **RX_CARRIER (fid 1) es de-facto obligatorio y PRIMERO.** Fija el índice de carrier
   (`r29+0x20`) que CHANNEL/BANDWIDTH usan como offset. Sin RX_CARRIER el índice queda en su
   default (init `r29+0x20 = 2` a 0xd8184018) → CHANNEL/BANDWIDTH escriben en el slot 2. Si el
   default no es válido para el resto del pipeline puede fallar aguas abajo. **INFERENCE alta.**
2. **Orden: RX_CARRIER → BAND → CHANNEL/CENTER_FREQ → BANDWIDTH.** CHANNEL y BANDWIDTH indexan
   arrays per-carrier con gate `idx<=2` (0xd81845b0). BAND se guarda global (r29+0x60). **FACT gates.**
3. **TECH_MODE (fid 25) = 1 (LTE)** para seleccionar la tecnología del tune. **INFERENCE
   (LTE=1 es FACT del TECH_ENTER).**
4. **Estado tech "entered"**: RADIO_CONFIG exige TECH_ENTER previo (LTE). Gate 0xd81e5d60:
   estado per-tech `memb(0xca7897b0 + tech<<3) == 0x7` → error 0x10 → status **0x14**. **FACT.**
   → Sin TECH_ENTER, RADIO_CONFIG da 0x14 aunque los TLVs sean correctos.

**Mínimo LTE (INFERENCE, a confirmar quitando de a uno en vivo):**
`RX_CARRIER(1), TECH_MODE(25), BAND(5), CHANNEL(6) ó CENTER_FREQ(12), BANDWIDTH(7)`.

---

## 6. POR QUÉ 0x1002 CRASHEA (SSR) SIN TLVs  [INFERENCE con base FACT]

**FACT:** el unpacker RADIO_CONFIG (0xd8183f30) NO crashea con num_tlv=0: hace bail limpio
en 0xd818486c (r24=0x10, retorno de error → status 0x14). Lo mismo el gate genérico de los
handlers 0x10xx (0xd86fd10c) hace bound-check `command_id<=0x31` antes de tocar nada.

**Entonces 0x1002 es un comando distinto cuyo path SÍ deref-ea con num_tlv=0.** Candidatos y
mecanismo (INFERENCE):
- **RX_MEASURE / IQ_CAPTURE**: su handler (p.ej. 0xd86fd0f4 → 0xd8272684 → 0xd8272cd8 →
  callr 0xd826d3e4 / 0xd8292298) resuelve un **field-table por command_id vía @0xca79a850**
  y luego pasa a un decode que, para captura, **espera un buffer de resultados/memshare**.
  Con num_tlv=0 no se selecciona carrier/agc y el path de measure/capture puede:
  (a) indexar `memw(0xca79a850)+cmd*4+0x34` cuando ese slot aún es 0 → **deref nulo**, o
  (b) llamar al pipeline de captura que deref-ea el puntero DDR/memshare no inicializado.
  → SSR. **INFERENCE (el crash es un deref nulo en el path de measure/capture, no en el TLV loop).**
- **FACT de soporte:** la fase de repack de estos comandos recorre `memd(r4+0x1240)` (bitmap
  per-carrier) y arrays per-index con gates `idx<=0x29`; con num_tlv=0 el índice/bitmap queda
  en default y algunos punteros intermedios quedan nulos.

**TLV mínimo que EVITA el crash en 0x1002 (INFERENCE accionable):** mandar **al menos 1 TLV
válido que fije el carrier**: `RX_CARRIER(fid=1, len=4, val=0)`. Eso inicializa el índice y el
bitmap per-carrier antes de que el path toque los arrays/punteros. Si 0x1002 resulta ser
IQ_CAPTURE, agregar además el selector de captura (FETCH/type) según iq_final_values.md.

```
; probe seguro para 0x1002 (fija carrier 0, no dispara captura):
4B 0B 27 00 02 10 01 00  01 00 04 00 00 00 00 00
                          └ RX_CARRIER=0 (fid1,len4,val0)
```

---

## 7. CERRAR EL sub_command EN VIVO — COMMAND_CAPABILITY  [FACT del formato]

Campos @0xc37c03b4 (6 handlers). field 1=QUERY_COMMAND(u32), 2=QUERY_PROPERTY(u32),
3=CMD_MASK(rsp), 4..7=PROPERTY_MASK_*(rsp). **FACT.**
```
1) Barrer SUB = 0x1002..0x1017, num_tlv=1, TLV {field1 QUERY_COMMAND = 0xFFFFFFFF}:
   4B 0B 27 00 <SUB:LE> 01 00  01 00 04 00 FF FF FF FF
   El que devuelva un REPACK con CMD_MASK != 0 = COMMAND_CAPABILITY.
2) Su CMD_MASK: bit N = slot 0x10(N) soportado → enum completo.
3) QUERY_COMMAND=N → PROPERTY_MASK identifica RADIO_CONFIG (trae props BAND/CHANNEL/BW).
```

---

## 8. PAQUETE RADIO_CONFIG — TUNE LTE MÍNIMO, BYTE A BYTE  [que NO crashea]

Header: `4B 0B <ftm_cmd@0x02=27 00> <sub_command@0x04> <num_tlv@0x06> <TLVs>`.
Wire TLV (variable): `<field_id:u16 LE> <len:u16 LE> <value[len] LE>`. El unpacker zero-extiende
el value a 64-bit. Usar len=4 (u32) para todos salvo CENTER_FREQ (usar len=8 si se manda por Hz).

### PASO 1 (OBLIGATORIO) — TECH_ENTER LTE  [FACT: sub 0x000D, TECH=1]
```
4B 0B 27 00 0D 00 03 00 \
   01 00 04 00 00 00 00 00 \    ; SUB      (field 1) = 0
   02 00 04 00 01 00 00 00 \    ; TECH     (field 2) = 1 (LTE)
   03 00 04 00 00 00 00 00      ; SCENARIO (field 3) = 0
```
Esperar status 0 (éxito). Saca a LTE del estado 0x7 → RADIO_CONFIG deja de dar 0x14.

### PASO 2 — RADIO_CONFIG (LTE B3, EARFCN 1575, BW 20 MHz)
`<SUB_RC>` = LE de 0x10NN (fijar por §4/§7; probar 0x1003.. o el que CMD_MASK indique).
Orden RX_CARRIER → TECH_MODE → BAND → CHANNEL → BANDWIDTH. num_tlv = 5.
```
4B 0B 27 00 <SUB_RC:u16LE> 05 00 \
   01 00 04 00 00 00 00 00 \    ; RX_CARRIER = 0        (fid 1)  <- fija carrier, evita crash
   19 00 04 00 01 00 00 00 \    ; TECH_MODE  = 1 (LTE)  (fid 25=0x19)
   05 00 04 00 03 00 00 00 \    ; BAND       = 3 (B3)   (fid 5)
   06 00 04 00 27 06 00 00 \    ; CHANNEL    = 1575     (fid 6, EARFCN B3 DL)
   07 00 04 00 03 00 00 00      ; BANDWIDTH  = 3 (enum 20MHz) (fid 7)
```
Concatenado (SUB_RC = ejemplo 0x1003 = `03 10`; ajustar):
```
4B 0B 27 00 03 10 05 00 01 00 04 00 00 00 00 00 19 00 04 00 01 00 00 00 05 00 04 00 03 00 00 00 06 00 04 00 27 06 00 00 07 00 04 00 03 00 00 00
```

### VARIANTE por CENTER_FREQ (Hz) en vez de EARFCN
CENTER_FREQ (fid 12) es u64. B3 DL EARFCN 1575 → 1842.5 MHz = 1842500000 Hz = 0x6DD5F600.
Reemplazar el TLV de CHANNEL por:
```
   0C 00 08 00 00 F6 D5 6D 00 00 00 00   ; CENTER_FREQ (fid 12, len 8) = 1842500000 Hz
```

**Valores LTE concretos (INFERENCE de convención 3GPP/Qualcomm; validar en vivo):**
| campo       | fid | LTE B3            | LTE B7            |
|-------------|-----|-------------------|-------------------|
| TECH_MODE   | 25  | 1 (LTE)           | 1 (LTE)           |
| BAND        | 5   | 3                 | 7                 |
| CHANNEL(EARFCN DL) | 6 | 1575 (0x0627)  | 3100 (0x0C1C)     |
| CENTER_FREQ(Hz) | 12 | 1842500000 (0x6DD5F600) | 2680000000 (0x9FBB2700) |
| BANDWIDTH(enum) | 7 | 3 (=20 MHz, típico) | 3 (=20 MHz)     |

> BANDWIDTH es un ENUM (no kHz). El valor exacto del enum (0=1.4,1=3,2=5,3=10,4=15,5=20 MHz o
> similar) es **UNKNOWN estático** — no hay tabla de strings del enum en rodata. Barrer 0..6 en
> vivo y ver cuál pasa la validación / reporta el BW correcto. EARFCN/Hz son valores 3GPP FACT.

---

## 9. FACT / INFERENCE / UNKNOWN — CIERRE CON VAs

**FACT:**
- Unpacker RADIO_CONFIG = **0xd8183f30**; wrapper 0xd818327c/0xd8182ec0; init/registro 0xd8182640
  (unpack=0xd8182ec0, repack=0xd8182f40, bufsz=0x174810, memb(desc+0x10)=0x12).
- Dispatch de field: **r8 = memw((field_id-1)<<2 + 0xc37c0290); jumpr r8**; válido field_id 1..36
  (gate `(field_id-1)>0x23` → 0xd818471c). Gate externo del loop: `field_id>0x3b` → 0xd8184880.
  (0xd81844d8/dc/f4, 0xd8184054/58).
- Name-table @0xc906c630 (b23), 60 nombres (índice=field_id). Lista completa §3.
- TLV normalizado = registro FIJO 0xc bytes {field_id:u16, pad, value:u64}; loop r20+=0xc.
- num_tlv==0 (o ptr nulo) → bail LIMPIO 0xd818486c (r24=0x10 → status 0x14). **RADIO_CONFIG NO crashea.**
- CENTER_FREQ (fid12) = u64 (memd, 0xd81846f0). RX_CARRIER (fid1) set bit en bitmap r29+0x68.
- Gate tech-state @0xca7897b0 (tech<<3), ==0x7 → status 0x14 (0xd81e5d60). LTE=1.
- Resolver command_id→field_table: base=memw(0xca79a850); memw(base+cmd*4+0x34); cmd<=0x31
  (0xd8272684/98/9c; 0xd86fd10c).
- Router/dispatch RFTEST 0x10xx: jump-table @0xc37c649c (24 stubs), byte alto @0x05==0x10
  (0xd82714fc). Format string RADIO_CONFIG @0xc37c0320. Handler-array @0xc37c0268 (46 = 10 tipo
  + 36 valor).

**INFERENCE:**
- sub_command 0x10NN de RADIO_CONFIG = N∈{3..17}, **NO 0x1002** (0x1002 crashea; RADIO_CONFIG no).
- 0x1002 = RX_MEASURE / IQ_CAPTURE u otro comando de measure/capture: crash = deref nulo del
  field-table/memshare no-inicializado con num_tlv=0. Evitarlo con ≥1 TLV RX_CARRIER=0.
- Obligatorios LTE: RX_CARRIER, TECH_MODE=1, BAND, CHANNEL|CENTER_FREQ, BANDWIDTH; orden importa.
- Valores 3GPP B3/B7 (EARFCN/Hz) FACT; enum BANDWIDTH a barrer.

**UNKNOWN (sólo runtime/en vivo):**
- Número exacto sub_command 0x10NN de RADIO_CONFIG (tabla @0xca79a850 poblada en runtime;
  command_id = contador de registro). Cerrar con COMMAND_CAPABILITY (§7).
- Valor exacto del enum BANDWIDTH y el subconjunto exacto de obligatorios (validación runtime).
- Identidad precisa de 0x1002 (probablemente IQ_CAPTURE/RX_MEASURE) — cerrar con COMMAND_CAPABILITY.

---

## 10. REPRODUCIR
```
dis.sh 0xd8183f30 0x120   # unpacker RADIO_CONFIG: gate num_tlv, loop, dispatch
dis.sh 0xd8184030 0x90    # loop TLV: field_id, value 64-bit, name-table
dis.sh 0xd81844d8 0x40    # 2º gate (field_id-1)>0x23 + dispatch @0xc37c0290
dis.sh 0xd818486c 0x20    # bail limpio num_tlv=0 (r24=0x10 -> status 0x14)
dis.sh 0xd8182640 0x140   # init/registro RADIO_CONFIG (unpack=0xd8182ec0, memb desc+0x10=0x12)
dis.sh 0xd8272684 0x40    # resolver command_id -> field-table @0xca79a850
rd.py  0xc37c0290 36 w    # tabla field-handlers (índice field_id-1)
python3 - <<'PY'          # name-table (índice = field_id)
import struct
b23=open('/tmp/modemre/modem.b23','rb').read();B=0xc8b6a000
for f in range(1,37):
  e=struct.unpack('<I',b23[(0xc906c630+f*4)-B:(0xc906c630+f*4)-B+4])[0]
  o=e-B; print(f, b23[o:b23.find(b'\x00',o)].decode())
PY
```
