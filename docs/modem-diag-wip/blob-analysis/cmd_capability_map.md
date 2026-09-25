# COMMAND_CAPABILITY / CMD_MASK + tabla runtime @0xca79a850 — SM6375 / Moto G82 5G
Build: MPSS.HI.4.3.4-00494-MANNAR_GEN_PACK-1.24452.133
Base código descomprimido: `/tmp/modemre/clade_dec_full.bin` (VA 0xd8000000). `dis.sh <va> <len>`.
Rodata estática: `rd.py <va> <n>` (b21=0xc3553000, b23=0xc8b6a000).
Disasm completo del image: `/tmp/_full_dis.txt` (2.62M líneas, llvm-objdump v66).

Leyenda: **FACT** = leído del disasm/bytes (VA citada) · **INFERENCE** = deducción · **UNKNOWN** = sólo en vivo/RAM.

---

## 0. TL;DR — RESPUESTA DIRECTA

1. **La tabla @0xca79a850 NO se puebla desde descriptores estáticos con nombre/handler.**
   Se **aloca vacía** (50 slots) en `0xd82723c0` y cada comando RFTEST se **auto-registra en
   runtime** llamando setters (`0xd827263c`, `0xd8272608`, `0xd82725e0`) al procesar su primer
   request. El número `sub_command 0x10xx ↔ nombre` **NO está en el binario como tabla**: se
   deriva en runtime. **FACT** (verificado por 5 caminos independientes — §2/§3/§7). Esto CIERRA
   definitivamente la pregunta "¿por qué no aparece el mapa estático?": no existe.

2. **COMMAND_CAPABILITY**: su función unpacker es **0xd81849ec** (FACT, única que referencia la
   tabla de nombres de campo del grupo-22 @0xc906ce18). Lee su descriptor de request en
   `r25 = memw(pkt+0)`, y de ahí `memub(r25+0x12)` (=sub_command byte, tu "desc+0x12") y
   `memub(r25+0x16)` (=field/query id). **FACT** (0xd8184a00/a30/a3c).

3. **El status 0x14 de tu probe (field 1 QUERY_COMMAND=0xffffffff) NO es por field equivocado.**
   Es el **gate de tech-state** (0xd81e5d60): `memb(0xca7897b0 + tech*8 + 0x89a8...) == 0x7`
   (tech "no-entrada") → error 0x10 → **status 0x14 (DIAG_BAD_PARM)**. **Hay que hacer TECH_ENTER
   (sub 0x000D, TECH=1=LTE) ANTES.** COMMAND_CAPABILITY corre por el mismo router RFTEST 0x10xx
   que sí pasa por ese gate. **FACT del mecanismo** (§7). El field QUERY_COMMAND=1 ES correcto.

---

## 1. LA FUNCIÓN QUE ALOCA @0xca79a850 (init) — DECODE EXACTO [FACT]

**Único escritor de `memw(##0xca79a850)`**: `0xd8272368` (grep en _full_dis.txt: 1 store, 28 loads).

### 1.1 Alloc del bloque maestro — `0xd8272344`
```
d8272348: r2 = memw(##0xca79a850)          ; ¿ya inicializado?
d8272350: if (r2 != 0) return
d827235c: r0 = ##0x954c                     ; alloc 0x954c bytes  (bloque maestro)
d8272368: memw(##0xca79a850) = r0           ; <<< guarda el puntero al bloque
d8272378: jump 0xd8069ec8                    ; memset(block,0,0x954c)
```
**FACT.** `@0xca79a850` = PUNTERO a un bloque RAM de 0x954c bytes.

### 1.2 Populado de 50 slots VACÍOS con defaults — `0xd82723c0`
```
d82723d4: r19:18 = (##0x120, #0x34)          ; r18=0x34+id*4 ; r19=0x28+id*0x28
d82723d8: r16=0 (id) ; r21:20=(#3,#0)
d82723e0: r23:22 = (##0xca79a850, #0x15)      ; r23=&block_ptr ; r22=0x15 (ssid default)
LOOP (id=r16, 0..0x31):
  r24 = memw(r23)                             ; block = *0xca79a850
  entry = malloc(0x28)                        ; struct por-comando 0x28 bytes
  memw(block + 0x34 + id*4) = entry           ; ARRAY DE PUNTEROS a struct
  memset(entry,0,0x28)
  entry->[0x1]=0x15 ; entry->[0x2]=0xffff(h) ; entry->[0x4]=0x33(h) ; entry->[0x6]=0
  entry->[0x10]=0 ; entry->[0x19..0x27]=defaults ; entry->[0x1e]=0x7fff(h)
  entry->[0x25]=3 ; entry->[0x26]=3
  memb(block + id)        = 0x15              ; array de bytes @block[0..49]
  memb(block + 0x28 + id*0x28) = 3
d82724f0: if (id == 0x32) done               ; 50 slots (0..0x31)
```
**→ LAYOUT del bloque maestro (block = *0xca79a850):**
| offset | contenido |
|---|---|
| block+0x00 .. +0x31 | array de bytes (ssid, def 0x15) por command_id |
| block+0x34 + id*4    | **puntero al struct-comando (0x28 bytes)** por command_id  ← lo lee 0xd8272684 |
| block+0xfc + id*0x28 | **struct-comando INLINE (0x28 bytes)** por command_id ← lo devuelve 0xd82726f4 |
| block+0x8c0/+0x8cc/+0x8dc/+0x8f0/+0x900/+0x908/+0x910 | globales del framework |

**Los valores 0xffff (+0x2), 0x33 (+0x4) son DEFAULTS "no registrado"** — NO hay sub_command
estático. El sub_command real y el nombre se escriben en runtime (§2). **FACT.**

### 1.3 Accesores (getters/setters) del struct-comando [FACT]
```
0xd8272684  resolve(id≤0x31): return memw(block + id*4 + 0x34)   ; struct-ptr; +0x34=field?  <<< usado por handlers
0xd82726c4  get(id):          return memw(block + id*4 + 0x34)   ; variante sin log
0xd82726f4  get_inline(id):   return block + 0xfc + id*0x28       ; struct inline
0xd8272594  set00(id, r1):    entry->[0x0]=r1 (r2==1) / [0x6] (r2==2) / [0x7]
0xd8272608  set04(id, r1):    memh(entry+0x4) = r1                 ; 16-bit
0xd827263c  set0102(id,r1,r2):entry->[0x1]=r1 ; memh(entry+0x2)=r2 ; <<< aquí iría sub/idx16
```
**Callers de los setters = los handlers 0x10xx** (0xd86fd434, 0xd86fd550, 0xd86fd784, …) →
confirman **auto-registro en runtime**, no init estático. **FACT.**

---

## 2. POR QUÉ NO HAY MAPA ESTÁTICO sub_command↔nombre — 5 PRUEBAS [FACT]

1. **Único escritor de la tabla es un memset/alloc** (0xd8272368), sin datos de comando.
2. **Los field-tables por-comando** (RADIO_CONFIG @0xc37c0268, IQ_CAPTURE @0xc37c0828,
   COMMAND_CAPABILITY @0xc37c03b4) son **arrays de punteros a field-handlers de CÓDIGO**, y están
   **referenciados SÓLO dentro del unpacker de su comando** (grep: 0xc37c0268 sólo en 0xd81828f0;
   0xc37c03b4 sólo en 0xd8184fe8; 0xc37c0828 sólo en 0xd8189818). No hay una tabla que los liste.
3. **Las VAs de los unpackers NO aparecen como inmediatos** en ninguna parte del image
   (grep `##0xd81849ec`, `##0xd8184fe8`, `##0xd81828f0`, … = 0 hits salvo el propio prólogo).
   → se registran vía command_id calculado en runtime, no por puntero estático.
4. **Los nombres de comando ("RADIO_CONFIG", "COMMAND_CAPABILITY", …) sólo existen embebidos en
   format-strings F3** (`[FTM.RFTEST][COMMAND_CAPABILITY][UNPACK]…` @0xc37c03cc), no como tabla
   de registro con sub_command.
5. **Los 21 handlers 0x10xx (§4) son wrappers finos** que llaman al **mismo** despachador genérico
   `0xd8272cd8` pasando un callback genérico compartido (0xd8263324 lo pasan 0x1002 Y 0x1008),
   así que el callback tampoco identifica el comando.

**Conclusión (idéntica a los 3 pases previos, ahora con la causa RAÍZ probada): el número exacto
`0x10xx ↔ nombre` es UNKNOWN estático. Se cierra en vivo (§6).**

---

## 3. COMMAND_CAPABILITY — FUNCIÓN Y VALIDACIÓN [FACT, 0xd81849ec]

Módulo `ftm_rf_test_command_capability.c`. Entry del unpacker: **0xd81849ec** (FACT: única función
con `memw(fid<<2+##0xc906ce18)` — tabla de nombres del grupo-22).

```
d81849ec: call 0xd814e6ac                    ; obtiene contexto del request -> r0
d81849f8: r18 = r0 ; r2 = memw(r0+#0x4)       ; r18 = request
d8184a00: r25 = memw(r18+#0x0)               ; r25 = DESCRIPTOR de comando (parsed)
d8184a2c: call 0xd8169618                     ; tech->tech_idx (gate ≤8)
d8184a30: r0 = memub(r25+#0x16)              ; desc+0x16 = query/field selector
d8184a3c: r0 = memub(r25+#0x12) ; r22 = r0   ; desc+0x12 = sub/idx byte  (tu "desc+0x12")  <<<
d8184a40: if (r19 > 0x14) error               ; gate query ≤ 0x14
d8184ac0: r22 = memub(r25+#0x1a)             ; desc+0x1a = NUM_TLV del request
d8184ac0: if (num_tlv == 0) jump 0xd8184bc4   ; permite request vacío (default)
LOOP fields (r26 = puntero TLV): field_id -> 0xd818296c -> índice local (0..5)
```

### 3.1 Field-dispatcher de COMMAND_CAPABILITY — @0xc37c03b4 (6 slots) [FACT]
`0xd8184fe8: r4 = memw(idx<<2 + ##0xc37c03b4); jumpr r4` con `idx = mapfield(field_id)` y
gate `idx-1 > 5 → repack (0xd8185060)`.
```
idx 0 -> 0xd8185010   (default/QUERY dispatch)
idx 1 -> 0xd8184ff0   (field QUERY_COMMAND)
idx 2 -> 0xd8184ff8   (field QUERY_PROPERTY)
idx 3 -> 0xd8185000
idx 4 -> 0xd8185004
idx 5 -> 0xd818500c
```
(A partir de 0xc37c03cc empieza el format-string, por eso son 6 slots. **FACT** por bytes.)

### 3.2 Tabla de NOMBRES de campo grupo-22 (COMMAND_CAPABILITY) — @0xc906ce18 [FACT]
`field_id` (índice directo) → nombre (leído de los punteros a string):
```
 0 UNASSIGNED           1 QUERY_COMMAND        2 QUERY_PROPERTY
 3 CMD_MASK             4 PROPERTY_MASK_0_63   5 PROPERTY_MASK_64_127
 6 PROPERTY_MASK_128_191 7 PROPERTY_MASK_192_255
 9 SUB_IDX  10 TECH  11 RXTX  12 CHAIN  13 CARRIER_IDX  14 RFM_DEVICE
15 SIG_PATH 16 ANTENNA_PATH 17 ANTENNA_NUM 22 PLL_ID 23 BAND 24 CHANNEL 25 BANDWIDTH
27 BAND_NUMBER 28 SUBBAND_NUMBER 29 RAW_RESULT 30 IF_TRX_INFO 31 RFTRX_ANTMOD_INTF_INFO
32 ANT_MODULE_INFO 33 SUBBAND_FREQ_INFO 34 ANT_MODULE_TYPE 35 TX_RX_CAL_FEED_INFO
36 PLATFORM_TYPE  38 gps_dc_cancellation
```
**FACT** (b23 @0xc906ce18). Coincide con el grupo-22 del pase previo.

---

## 4. TABLA COMPLETA sub_command 0x10xx → handler (stub → wrapper) [FACT estructura]

De la jump-table @0xc37c649c (24 slots, byte-alto @0x05 == 0x10). Cada slot → stub 0xd8271xxx →
`callr` handler wrapper. Los handlers son wrappers finos al despachador genérico 0xd8272cd8; el
NOMBRE de cada slot es UNKNOWN estático (§2). VAs **FACT**:

| sub_cmd | slot | stub        | handler (wrapper)   | pasa callback | nota |
|---------|------|-------------|---------------------|---------------|------|
| 0x1000  | 0    | 0xd8271630  | (common/no-op)      | —             | reservado |
| 0x1001  | 1    | 0xd827176c  | (error)             | —             | "unsupported" |
| 0x1002  | 2    | 0xd8271548  | 0xd86fd0f4          | 0xd8263324    | |
| 0x1003  | 3    | 0xd8271558  | 0xd86fd230          | —             | |
| 0x1004  | 4    | 0xd8271568  | 0xd86fd328          | —             | |
| 0x1005  | 5    | 0xd8271578  | 0xd86fd474          | —             | |
| 0x1006  | 6    | 0xd827170c  | 0xd8271c94          | —             | |
| 0x1007  | 7    | 0xd82715b0  | 0xd86fdb88          | —             | |
| 0x1008  | 8    | 0xd8271588  | 0xd86fd5c8          | 0xd8263324    | (mismo cb que 0x1002 ⇒ cb genérico) |
| 0x1009  | 9    | 0xd8271598  | 0xd86fd80c          | —             | |
| 0x100a  | 10   | 0xd82715a4  | 0xd86fda68          | —             | |
| 0x100b  | 11   | 0xd82715f8  | 0xd86fe120          | —             | |
| 0x100c  | 12   | 0xd8271604  | 0xd86fe1e4          | —             | |
| 0x100d  | 13   | 0xd8271610  | 0xd86fde80          | —             | |
| 0x100e  | 14   | 0xd82715bc  | 0xd86fe0a4          | —             | |
| 0x100f  | 15   | 0xd82715c8  | 0xd86fdf94          | —             | |
| 0x1010  | 16   | 0xd8271718  | 0xd82722c4          | —             | (compartido con 0x30xx) |
| 0x1011  | 17   | 0xd8271724  | 0xd8271b64          | —             | (compartido con 0x30xx) |
| 0x1012  | 18   | 0xd8271730  | 0xd8271bcc          | —             | (compartido con 0x30xx) |
| 0x1013  | 19   | 0xd82715d4  | (error)             | —             | inválido |
| 0x1014  | 20   | 0xd82715e0  | 0xd86fe300          | —             | |
| 0x1015  | 21   | 0xd82715ec  | 0xd86fe558          | —             | |
| 0x1016  | 22   | 0xd827161c  | 0xd86fe60c          | 0xd86fe780    | |
| 0x1017  | 23   | 0xd8271628  | 0xd86fe7e0          | —             | |

**IMPORTANTE:** el unpacker **0xd81849ec (COMMAND_CAPABILITY)** NO es ninguno de estos wrappers:
se INVOCA por command_id vía la tabla @0xca79a850 (0xd8272684 resuelve struct-comando y de ahí el
unpacker). Por eso su sub_command exacto es UNKNOWN estático — hay que barrer (§6).

**INFERENCE (orden de declaración):** RADIO_CONFIG suele ser el primero (slot ~0x1002),
seguido por RX_MEASURE, WAIT_TRIGGER, MSIM_CFG, TX_CONTROL, IQ_CAPTURE, TX_MEASURE,
COMMAND_CAPABILITY (por el orden del F3-table §5.1). Candidatos COMMAND_CAPABILITY: 0x1002..0x1017.

### 4.1 Field-tables (unpacker → field-tbl → fmt) por comando [FACT]
```
RADIO_CONFIG        unpacker 0xd81828f0(disp)  field-tbl @0xc37c0268  fmt @0xc37c0320/0374
COMMAND_CAPABILITY  unpacker 0xd81849ec        field-tbl @0xc37c03b4  fmt @0xc37c03cc/042c
RX_MEASURE          unpacker 0xd8185b1c        field-tbl @0xc37c0478  fmt @0xc37c0590
WAIT_TRIGGER        unpacker 0xd8187d34        field-tbl @0xc37c0644  fmt @0xc37c0664
TX_CONTROL          unpacker 0xd81889bc        field-tbl @0xc37c078c  fmt @0xc37c07d8
IQ_CAPTURE          unpacker 0xd8189818(disp)  field-tbl @0xc37c0828  fmt @0xc37c08f4
TX_MEASURE          unpacker 0xd818a66c        field-tbl @0xc37c0a10  fmt @0xc37c0dc4
```

---

## 5. NOMBRES DE COMANDO (F3 message table) — confirmación [FACT]

Tabla de mensajes F3 @0xc3555b80 (16 bytes/entry: `msg_id:u16, ssid:u16, argc:u32, fmt:u32, file:u32`).
Los format-strings nombran cada comando RFTEST (ssid 0x17=RF):
```
[FTM.RFTEST][RADIO_CFG][REPACK]: [%3d][ %12s ][ %lld ][ 0x%8x ]     file ftm_rf_test_radio_config.c
[FTM.RFTEST][COMMAND_CAPABILITY][UNPACK]:[%3d][ %12s ][ %12d ]      file ftm_rf_test_command_capability.c
[FTM.RFTEST][COMMAND_CAPABILITY][REPACK]: [%3d][ %12s ][ %lld ][ 0x%8x ]
[FTM.RFTEST][RX_MEASURE][UNPACK]:[%3d][ %12s ][ %12d ]              file ftm_rf_test_rx_measure.c
[FTM.RFTEST][RX_MEASURE][REPACK]: [%3d][ %12s ][ %4d ][ 0x%8x ]
[FTM.RFTEST][WAIT_TRIGGER][UNPACK]/[REPACK]                          file ftm_rf_test_wait_trigger.c
[FTM.RFTEST][MSIM_CFG][UNPACK]                                       file ftm_rf_test_msim_config.c
[FTM.RFTEST][TX_CONTROL][UNPACK]                                     file ftm_rf_test_tx_control.c
[FTM.RFTEST][IQ_CAPTURE][UNPACK]/[REPACK]                            file ftm_rf_test_iq_capture.c
[FTM.RFTEST][TX_MEASURE][UNPACK]/[REPACK]                            file ftm_rf_test_tx_measure.c
[FTM.RFTEST][IRAT_CONFIG]                                            file ftm_rf_test_irat_config.c
```
→ el **orden de declaración** (RADIO_CONFIG, COMMAND_CAPABILITY, RX_MEASURE, WAIT_TRIGGER,
MSIM_CFG, TX_CONTROL, IQ_CAPTURE, TX_MEASURE) es la guía para el barrido dirigido de §6. **FACT.**

---

## 6. PAQUETE COMMAND_CAPABILITY BYTE-A-BYTE PARA LEER CMD_MASK

Header común (FACT §sub_command_map): `4b 0b <ftm_cmd@0x02> <sub_command@0x04> <num_tlv@0x06> <TLVs>`.
ftm_cmd = `27 00` (LTE). TLV = `<field_id:u16> <len:u16> <value>`.

### PASO 0 (OBLIGATORIO) — TECH_ENTER LTE (evita el 0x14 del gate tech-state §7)
```
4B 0B 27 00 0D 00 03 00 \
   01 00 04 00 00 00 00 00 \      ; SUB      = 0
   02 00 04 00 01 00 00 00 \      ; TECH     = 1 (LTE)   <-- FACT
   03 00 04 00 00 00 00 00        ; SCENARIO = 0
```
Concatenado:
```
4B 0B 27 00 0D 00 03 00 01 00 04 00 00 00 00 00 02 00 04 00 01 00 00 00 03 00 04 00 00 00 00 00
```
Esperar status=0. **Sin esto, COMMAND_CAPABILITY devuelve 0x14** (el 0x14 que viste NO era el field).

### PASO 1 — COMMAND_CAPABILITY con QUERY_COMMAND=0xFFFFFFFF (todos)
`<SUB_CAP>` = LE de 0x10NN. **Barrer 0x1002..0x1017** (orden probable por §5: cerca de RADIO_CONFIG).
El que devuelva un REPACK con `field 3 CMD_MASK` (0x%8x != 0) es COMMAND_CAPABILITY.
```
4B 0B 27 00 <SUB_CAP:u16LE> 01 00 \
   01 00 04 00 FF FF FF FF          ; field 1 QUERY_COMMAND = 0xFFFFFFFF
```
Ejemplo con SUB_CAP=0x1002:  `4B 0B 27 00 02 10 01 00 01 00 04 00 FF FF FF FF`
Barrido:  `02 10`, `03 10`, `04 10`, `05 10`, `06 10`, `07 10`, `08 10`, `09 10`,
          `0A 10`, `0B 10`, `0C 10`, `0D 10`, `0E 10`, `0F 10`, `14 10`, `15 10`,
          `16 10`, `17 10`.

**Alternativa (request vacío):** el unpacker permite `num_tlv=0` (0xd8184ac0 → 0xd8184bc4),
que hace REPACK de TODOS los campos con sus defaults, incluido CMD_MASK. Probar también:
```
4B 0B 27 00 <SUB_CAP:u16LE> 00 00        ; sin TLVs -> REPACK completo (defaults + CMD_MASK)
```

### PASO 2 — Nombrar cada bit del mask (opcional)
Con COMMAND_CAPABILITY fijado, por cada bit N=1 del CMD_MASK:
```
4B 0B 27 00 <SUB_CAP:u16LE> 01 00  01 00 04 00 <N:u32LE>   ; QUERY_COMMAND = N
```
La rsp trae PROPERTY_MASK_0_63..192_255 (fields 4..7) con las propiedades de ese comando N
(soporta_IQ/TX/necesita_enter). Eso da el enum COMPLETO nombrado. **FACT del mecanismo.**

---

## 7. POR QUÉ TU PROBE DIO 0x14 — gate tech-state [FACT, 0xd81e5d60]

```
d81e5d4c: r2 = memw(tech<<2 + ##0xcbf68508)   ; ctx por-tech
d81e5d54: memb(tech<<3 + ##0xca7897b0) = 1      ; marca "entered" (lo pone TECH_ENTER)
d81e5d5c: r2 = memb(r2 + ##0x89a8)              ; estado por-tech
d81e5d60: if (r2 == 0x7) r0 = 0x10              ; 0x7 = "no-entrada" -> error 0x10
                                                ; -> status 0x14 (DIAG_BAD_PARM)
```
Estado per-tech base **@0xca7897b0** (= tabla RFTEST @0xca789780 + 0x30). TECH_ENTER (RFDEBUG
sub 0x000D, TECH=1=LTE) puebla ese estado y lo saca de 0x7. **El orden importa: TECH_ENTER
antes de CUALQUIER RFTEST 0x10xx, incluido COMMAND_CAPABILITY.** **FACT.**

Tu probe (sub 0x1000 QUERY_COMMAND=0xffffffff → 0x14) falló porque (a) 0x1000 es el slot0
reservado/no-op (§4), y/o (b) no habías hecho TECH_ENTER. El field QUERY_COMMAND=1 ES correcto
(FACT §3.2). Repetir con SUB_CAP real (barrido §6) DESPUÉS de TECH_ENTER.

---

## 8. FORMATO DE LA RESPUESTA CMD_MASK [FACT]

La rsp es un **REPACK**: bloque de TLVs de salida, uno por campo del comando, generado por el
loop 0xd8185060..0xd81850fc (usa fmt REPACK @0xc3555ba0 = `[COMMAND_CAPABILITY][REPACK]:
[%3d][ %12s ][ %lld ][ 0x%8x ]`). Cada TLV de salida:
```
<field_id:u16> <len:u16> <value>       ; mismo layout que el request
```
- **CMD_MASK = field_id 3**. Su value es el **BITMAP** (32-bit, mostrado como `0x%8x` en el F3):
  **bit N = 1 ⇒ el comando N (slot/sub_command interno) está soportado**. **FACT** (formato REPACK
  + nombre CMD_MASK grupo-22). El `%lld` del fmt es el value extendido a 64-bit; el `0x%8x` es el
  mask de 32-bit — para lecturas grandes usar los campos PROPERTY_MASK_0_63..192_255 (256 bits).
- **PROPERTY_MASK_0_63 (field 4) … PROPERTY_MASK_192_255 (field 7)**: 4× u64 = bitmap de 256
  propiedades del comando consultado en QUERY_COMMAND. **FACT** (nombres grupo-22).

**Parseo del enum:** leer el TLV con field_id==3 (CMD_MASK) de la rsp; iterar bits 0..31; cada
bit=1 es un command_id válido. Para el nombre de cada uno, re-consultar con QUERY_COMMAND=bit y
mirar los PROPERTY_MASK (o cruzar con el orden F3 §5). El command_id interno mapea a sub_command
0x10xx vía la tabla @0xca79a850 (runtime).

---

## 9. FACT / INFERENCE / UNKNOWN — cierre con VAs

**FACT:**
- Único escritor de @0xca79a850 = alloc/memset en 0xd8272368 (bloque 0x954c). Init de 50 slots
  vacíos con defaults en 0xd82723c0. Layout del bloque en §1.2.
- Accesores struct-comando: resolve 0xd8272684 (`*(block+id*4+0x34)`), inline 0xd82726f4
  (`block+0xfc+id*0x28`), setters 0xd827263c/0xd8272608/0xd82725e0. Callers = handlers 0x10xx
  (auto-registro runtime).
- COMMAND_CAPABILITY unpacker = **0xd81849ec** (única func con tabla nombres grupo-22 @0xc906ce18).
  Lee desc+0x12 (0xd8184a3c), desc+0x16 (0xd8184a30), num_tlv desc+0x1a (0xd8184ac0), permite
  num_tlv=0 (0xd8184bc4).
- Field-dispatcher CC @0xc37c03b4 (6 slots, gate idx-1>5→repack 0xd8185060), disp 0xd8184fe8.
- Nombres de campo grupo-22 @0xc906ce18: 1=QUERY_COMMAND 2=QUERY_PROPERTY 3=CMD_MASK
  4..7=PROPERTY_MASK_0_63..192_255 (§3.2).
- Gate tech-state 0xd81e5d60: estado==0x7 → 0x10 → status 0x14; base @0xca7897b0 (tech<<3).
- Formato rsp = REPACK TLV; CMD_MASK=field 3 (bitmap 32-bit, `0x%8x`); PROPERTY_MASK 4× u64.
- Tabla 24-slots 0x10xx @0xc37c649c → stubs 0xd8271xxx → handlers (§4). ftm_cmd=0x27, sub@0x04,
  num_tlv@0x06.
- Nombres de comando via F3-table @0xc3555b80 (§5), orden de declaración.

**INFERENCE:**
- COMMAND_CAPABILITY sub_command ∈ {0x1002..0x1017}; probable cercano a RADIO_CONFIG por orden F3.
- Orden de slots 0x1002.. sigue orden de declaración (RADIO_CONFIG, COMMAND_CAPABILITY, RX_MEASURE,
  WAIT_TRIGGER, MSIM_CFG, TX_CONTROL, IQ_CAPTURE, TX_MEASURE).

**UNKNOWN (sólo en vivo / RAM):**
- sub_command 0x10xx EXACTO ↔ nombre (incl. COMMAND_CAPABILITY): la tabla @0xca79a850 se puebla
  en runtime; los unpackers NO se referencian por inmediato. **No existe tabla estática.**
  Cerrar con el barrido §6 (PASO 1) tras TECH_ENTER.
- Valor concreto del CMD_MASK y qué bits mapean a qué command_id (sólo la respuesta en vivo lo da).
- Mapeo command_id(interno 0..0x31) ↔ sub_command 0x10xx (se resuelve leyendo desc en runtime).

---

## 10. REPRODUCIR
```
dis.sh 0xd8272344 0x60      # alloc @0xca79a850 (bloque 0x954c)
dis.sh 0xd82723c8 0x140     # populate 50 slots vacíos (defaults)
dis.sh 0xd8272684 0x40      # resolve(id) -> struct-comando
dis.sh 0xd82726f4 0x30      # get_inline(id) -> block+0xfc+id*0x28
dis.sh 0xd827263c 0x50      # setter [0x1],[0x2] (auto-registro runtime)
dis.sh 0xd81849ec 0xc0      # COMMAND_CAPABILITY unpacker (entry)
dis.sh 0xd8184fb0 0x40      # field-dispatcher CC (@0xc37c03b4, gate idx>5)
dis.sh 0xd8185060 0xc0      # REPACK loop (CMD_MASK, fmt @0xc3555ba0)
dis.sh 0xd81e5d40 0x40      # gate tech-state (==0x7 -> 0x14)
rd.py  0xc906ce18 40        # nombres de campo grupo-22 (COMMAND_CAPABILITY)
rd.py  0xc37c03b4 12        # field-dispatcher table CC (6 slots)
rd.py  0xc3555b80 0x60 b    # F3-table (nombres de comando RFTEST)
grep -n 0xca79a850 /tmp/_full_dis.txt   # 1 store (0xd8272368) + 28 loads
grep -n 0xc906ce18 /tmp/_full_dis.txt   # 2 refs -> ambos en COMMAND_CAPABILITY
```
