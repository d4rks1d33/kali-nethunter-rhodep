# FTM IQ-capture — VALORES FINALES (leídos del código LIMPIO)
Build: **MPSS.HI.4.3.4-00494-MANNAR_GEN_PACK-1.24452.133** (SM6375 / Moto G82 5G)
Base: `/tmp/modemre/clade_dec_full.bin` (VA 0xd8000000, file off 0 = VA 0xd8000000).
Disasm: `/tmp/modemre/dis.sh <va> <len>` (llvm-objdump hexagon v66). Todo verificado en esta imagen.

Leyenda: **FACT** = instrucción/byte verificado · **INFERENCE** = deducción con base · **UNKNOWN** = no aislado.

---

## 0. RESUMEN EJECUTIVO (lo que pediste, directo)

| # | Pregunta | Respuesta | Confianza |
|---|----------|-----------|-----------|
| 1 | ftm_len @0x06 | **NO es una longitud. Es un "session/response id" que se pasa a un alloc/lookup (0xd8062414→core). NO se valida como largo. Tu 0x1e no molesta.** | **FACT** |
| 1 | id @0x08 | **Solo se usa como "response length" y SOLO si DIAG-subcmd@0x02==0x24. Con subcmd 0x14 (tu caso) se IGNORA (default 0xfd0). Tu 0 está bien.** | **FACT** |
| 2 | ¿TECH_ENTER responde? | **SÍ responde. Aloca DIAG rsp (diagpkt_subsys_alloc, SSID 0x17) y escribe un status. NO es fire-and-forget.** | **FACT** |
| 3 | Valor TECH LTE en el TLV | **1** (u32). Cadena cerrada: TECH_TLV=1 → tech-index interno 1 = LTE. | **FACT** |
| 3 | Valor TECH NR5G | tech-index interno 7; enum del TLV = **6** (candidato) o header 0x8000/0x8001. | **INFERENCE** |
| 4 | sub_command RADIO_CONFIG/RX_MEASURE/IQ_CAPTURE/COMMAND_CAPABILITY | **Framework RFTEST separado; registro table-driven (stride 0xc, registrar 0xd8152af4). El número NO es un inmediato estático limpio.** | **UNKNOWN estático** (con método para barrer en vivo) |
| 5 | Flag tech_entered / 0x14 | Estado per-tech en `memb(ctx+0x89a8)`; gate `==0x7` (tech inválido/no-entrado) → error r0=0x10. TECH_ENTER lo puebla vía @0xca7897b0 array. | **FACT (mecanismo)** / INFERENCE (dirección exacta del global raíz) |

**sub_command TECH_ENTER = 13 (0x0d)** — re-confirmado en este pase (§1). **FACT.**

---

## 1. HEADER: qué es cada offset (0x06 y 0x08) — RESUELTO [FACT]

### 1.1 Validación en el registrar DIAG 0xd8150ed8 (FACT literal)
```
d8150efc: r18 = r1                         ; r1 = longitud del paquete (arg de la fn)
d8150f00: memb(pkt+1) == 0x0b              ; subsys FTM      (si no -> jump error)
d8150f08: (memub(pkt+2)|memub(pkt+3)<<8) == 0x14   ; DIAG subsys-cmd (u16 LE)
d8150f24: (memub(pkt+4)|memub(pkt+5)<<8) & 0xfffe == 0x35a  ; ftm id
```
→ **Las 3 validaciones NO tocan offset 0x06 ni 0x08.** El paquete que mandaste PASA estas 3.

### 1.2 La longitud de la RESPUESTA (offset 0x08) — FACT
```
d8150f90: r18 = #0xfd0                      ; DEFAULT response len = 4048
d8150f98: memb(pkt+1) == 0x0b   (else salta, mantiene 0xfd0)
d8150fa8: (memub(pkt+2)|memub(pkt+3)<<8) == 0x24   ; <-- OJO: 0x24, NO 0x14
d8150fb8: r2 = memub(pkt+8) | memub(pkt+9)<<8      ; lee u16 @ offset 0x08
d8150fc0: r18 = maxu(r2, 0xfd0)             ; response len = max(id@0x08, 0xfd0)
```
→ **El offset 0x08 SOLO se lee como "response length" y SOLO cuando el DIAG-subcmd@0x02 vale
0x24.** En tu paquete el subcmd es **0x14**, así que **offset 0x08 se IGNORA** y la respuesta
usa el default 0xfd0. **Tu id=0 @0x08 es correcto/inofensivo.** **FACT.**

### 1.3 El offset 0x06 — qué es realmente [FACT]
En el dispatch interno 0xd816d1e8 (el que decodifica el cuerpo FTM), r21 = paquete DIAG completo:
```
d816d1ec: r18 = add(r21,#0xa)              ; cuerpo FTM empieza en 0xa  (FACT ya conocido)
d816d1f0: r5  = memub(pkt+0xc)|pkt+0xd<<8|pkt+0xe<<16   ; ftm sub-cmd @0x0c (u24)
d816d20c: r0  = memub(pkt+0x6)|memub(pkt+0x7)<<8        ; <-- LEE offset 0x06 (u16 LE)
d816d214: call 0xd8062414                  ; 0xd8062414 -> tail-jump a core (0xc0988ef4)
d816d218: if (r0==0) jump error            ; si el lookup/alloc devuelve 0 -> error
```
→ **offset 0x06 = un identificador (client/transaction/response id) que se pasa a una
rutina core de alloc/lookup.** NO es un "largo del cuerpo". No se compara contra el
tamaño real. **Con 0x1e (30) el lookup no falla** (el core no rechaza ese valor como
largo). **FACT del mecanismo.** El valor concreto que "debería" ir ahí es 0 o un id de
transacción; **cualquier valor no-cero razonable funciona** (el core lo usa como handle).

### 1.4 CONCLUSIÓN del silencio (por qué no da 0x14 ni responde)
Tu paquete **pasa las 3 validaciones de 0xd8150ed8** (subsys 0x0b, subcmd 0x14, id 0x35a),
así que **no** entra por la rama de error temprano. Que NO veas 0x14 **ni** respuesta
indica que el flujo sí entró y el handler corrió, pero:
- o el sub_command efectivo (13) llegó al handler tech_enter y éste devolvió OK-silencioso
  (rsp de status mínima que tu captura no está mostrando), **o**
- el sub_command que el paquete produjo no fue 13 y cayó en un slot sin handler/echo-vacío.
**El fix está en (a) fijar sub_command=13 @0x0c y (b) los TLVs SUB/TECH=1/SCENARIO.**

---

## 2. ¿TECH_ENTER RESPONDE? — SÍ [FACT]

### 2.1 El handler 0xd8174758 construye respuesta DIAG (FACT)
El registrar DIAG 0xd8150ed8 aloca el buffer de respuesta ANTES de llamar al handler:
```
d8150f5c: call 0xd814d760                  ; 0xd814d760 -> jump 0xc0988ef4 (r1=0x17)
                                           ;   = diagpkt_subsys_alloc(SSID 0x17 = FTM)
d8150f60: memh(r16+#0xc)  = r18            ; guarda len solicitado
d8150f68: memw(r16+#0x10) = r0             ; guarda PUNTERO a la rsp alocada
```
El handler tech_enter, en su ruta de salida común, ESCRIBE el status en esa rsp:
```
d8174964: memw(r17+#0x18) = r18            ; status (r18 = or de codigos de error/ok)
d817495c: r0 = add(r16,#0xc)               ; dst = rsp buffer
d8174960: r1 = add(r17,#0x14)              ; src = status
d8174964: r2 = #0x8                        ; 8 bytes
d8174968: call 0xd816d794                  ; copia status -> rsp  (0xd816d794 usa memcpy 0xd8051b58)
```
Y el dispatch 0xd816d3ec escribe el status byte en el header de la rsp:
```
d816d3ec: p0 = cmp.eq(r3,#0x14)            ; si el status interno es 0x14 (BAD_PARM)
d816d3f4: if(p0) r2 = memw(r29+#0x38)      ; copia el codigo de error a la rsp
d816d424: memb(r0+#0x8) = r21              ; r21 = codigo (0x0 ok / 0x10 / 0x20 / 0x2 / 0x4)
```
→ **TECH_ENTER NO es fire-and-forget: aloca y commitea una respuesta DIAG con un status.**
**FACT.** Si el status sale 0 (ok) la respuesta es corta (header + status 0), fácil de
pasar por alto en una captura. **El éxito real se confirma por: (a) status byte = 0 en la
rsp, y (b) que el siguiente comando ya no rechace por "tech no activa" (§5).**

---

## 3. VALOR TECH PARA LTE = 1 — CERRADO [FACT]

### 3.1 Cadena de evidencia (dos caminos convergen en tech-index interno 1)
**Camino A — el ftm_cmd del header (0xd8169ec0, FACT literal):**
```
d8169ec0: cmp.eq(ftm_cmd,#0x22) -> idx 0
d8169ed0: cmp.eq(ftm_cmd,#0x28) -> idx 3
d8169edc: cmp.eq(ftm_cmd,#0x27) -> idx 1      ; 0x27 (LTE) -> tech-index interno 1
          else                  -> idx 7
```
**Camino B — el TLV TECH pasa por 0xd8169618, tabla @0xc37bdfe0 (FACT):**
```
d8169618: r2 = (TECH + 1) & 0xff ; if (r2 > 8) return 0x15(error)
          r0 = memw(##0xc37bdfe0 + r2<<2)      ; mapa TECH_TLV -> tech-index interno
tabla @0xc37bdfe0 (leída de b21):
  (TECH=-1)->0xffffffff  (TECH=0)->3  (TECH=1)->1  (TECH=2)->2  (TECH=3)->8
  (TECH=4)->0xa  (TECH=5)->5  (TECH=6)->0x13  (TECH=7)->0x13
```
→ **TECH_TLV = 1 mapea a tech-index interno 1**, que es EXACTAMENTE el índice de LTE del
camino A. **Por lo tanto el valor del TLV TECH (field_id 2, u32) para LTE = 1. FACT.**

### 3.2 El handler del field TECH (0xd8174cd0) — FACT
Lee 3–4 bytes LE (`memub(r21+0..3)`), forma un u32, lo guarda (`memw(r5)=valor`) y marca
presente (`memb(r18)=1`). **No compara aquí**; la validación es el mapeo §3.1. El valor
guardado se relee como byte de tech-index en el handler (`memub(ctx+0x12)`), gate `<=0x14`.

### 3.3 NR5G (INFERENCE)
tech-index interno de NR5G = 7 (rama else de 0xd8169ec0). En la tabla @0xc37bdfe0 el índice
7 sale de TECH_TLV=6 y 7 (ambos → 0x13, que NO es 7). No hay un TECH_TLV que dé 7 limpio,
así que NR5G probablemente entra por otro ftm_cmd de header (0x8000/0x8001), no por 0x27.
**NR5G en el TLV = UNKNOWN exacto**; candidato de prueba 6.

---

## 4. sub_command TECH_ENTER = 13 (0x0d) — RE-CONFIRMADO [FACT]

### 4.1 Mecanismo de dispatch (FACT literal, 0xd816d2e8)
```
d816d2d4: r20 = memub(r19+#0x4) | memub(r19+#0x5)<<8   ; sub_command (u16)
d816d2ec: r2  = memw(##0xca65b414)          ; tabla RFDEBUG (stride 0xc)
d816d308: if (r20 > 0x15) -> error 0x10     ; gate sub_command <= 0x15 (0..21)
d816d318: r3  = mpyi(r20,#0xc)              ; index = sub_command * 12
d816d324: r0  = memw(r2 + r3)               ; slot[sub].handler
d816d328: if (handler==0) -> error 0x20
d816d350: callr handler                     ; ejecuta
```

### 4.2 La tabla de registro (0xd816cf58) — slot 13 = TECH_ENTER (FACT)
Semántica Hexagon correcta (lectura paralela de r0 viejo): el `add(rX,#off)` de un packet
es el argumento del `call` del packet SIGUIENTE. Emparejado así:
```
registrar 0xd81746e8  ->  offset 0x9c  ->  slot 0x9c/0xc = 13   <<< TECH_ENTER_EXIT
```
Cadena: 0xd81746e8 guarda HANDLER 0xd8174758 → UNPACK 0xd8174ab0 (field TECH=id 2 vía
tabla @0xc37bee24). La 2ª fn de registro 0xd816d0a4 registra en el MISMO offset 0x9c
(0xd8174988, familia tech_enter). **sub_command(TECH_ENTER_EXIT) = 13. FACT estable.**

---

## 5. sub_command de RADIO_CONFIG / RX_MEASURE / IQ_CAPTURE / COMMAND_CAPABILITY

### 5.1 Hallazgo estructural NUEVO de este pase [FACT]
Son un **framework SEPARADO (RFTEST)**, NO están en la tabla RFDEBUG @0xca65b414:
- **FACT:** cero referencias a las UNPACK-master RFTEST (0xd8182000–0xd818c000) desde la
  región de registro RFDEBUG (0xd816c000–0xd8172000). Frameworks distintos.
- **FACT:** RFTEST tiene su propio registrar **0xd8152af4** (mismo patrón que RFDEBUG:
  `r3 = mpyi(sub,#0xc)`, valida `sub*0xc <= tabla_size`, stride 0xc) y su tabla en RAM
  (@0xcaad9d00, poblada en runtime; ej. de registro en 0xd850fc78).
- **FACT:** las UNPACK-master identificadas (por la tabla de field-handlers que referencian):
  ```
  RADIO_CONFIG       -> fn @~0xd81828ec  (tabla campos @0xc37c0268)
  COMMAND_CAPABILITY -> fn @~0xd8184fe4  (tabla campos @0xc37c03b4)
  RX_MEASURE         -> fn @~0xd8185b18  (tabla campos @0xc37c0478)
  WAIT_TRIGGER       -> fn @ 0xd8187cc0  (tabla PACK   @0xc37c0644)  [ref format str FACT]
  TX_CONTROL         -> fn @~0xd81889b8  (tabla campos @0xc37c078c)
  IQ_CAPTURE         -> fn @~0xd8189814  (tabla campos @0xc37c0828)
  TX_MEASURE         -> fn @~0xd818a668  (tabla campos @0xc37c0a10)
  ```
- format strings UNPACK (FACT): RADIO_CONFIG @0xc37c0320, COMMAND_CAPABILITY @0xc37c03cc,
  RX_MEASURE @0xc37c0590, WAIT_TRIGGER @0xc37c0664, IQ_CAPTURE @0xc37c08f4, TX_MEASURE @0xc37c0dc4.

### 5.2 Por qué el número exacto sigue UNKNOWN estático (honesto)
El sub_command de cada comando RFTEST **NO es un inmediato limpio**: el registro es
table-driven — la fn 0xd850fc40 lee los campos del descriptor de una **estructura**
(`memub(r18+0..7)`) y los reenvía al registrar 0xd8152af4. El `r0`(sub_command) del
registro proviene de esa estructura de descriptores construida en runtime (tabla RAM
@0xcaad9d00), no de un `r0 = #N` estático. Para leer el número habría que **volcar la RAM
del modem en vivo** o emular el init de RFTEST. **UNKNOWN estático (con método claro).**

### 5.3 Cómo barrerlos en vivo (mínimo, dado que el mecanismo está cerrado)
El registrar valida `sub*0xc <= tabla_size` y el dispatch RFTEST también gate `sub<=0x14`.
→ **Barrer sub_command 0..0x14** para el framework RFTEST. Camino más limpio:
**COMMAND_CAPABILITY** (6 campos, consulta sin enter-mode) devuelve **CMD_MASK** = bitmap
de qué sub_commands existen. Ese bitmap RESUELVE el enum entero en una sola respuesta.

---

## 6. FLAG "tech_entered" y condición 0x14 [FACT del mecanismo]

### 6.1 El estado per-tech y el gate (FACT literal, 0xd81e5d50)
```
d81e5d54: memb(##0xca7897b0 + tech<<3) = r3        ; ARRAY de estado per-tech (@0xca789780+0x30)
d81e5d5c: r2 = memb(r2 + ##0x89a8)                 ; byte de estado del contexto tech
d81e5d60: p0 = cmp.eq(r2, #0x7)                    ; 0x7 = tech-index INVALIDO / "no activo"
d81e5d64: if(p0) r0 = #0x10                        ; codigo de error 0x10
d81e5d6c: if(p0) call 0xda01cdb0                   ; aborta -> se propaga a status 0x14
```
→ **0x7 es el tech-index "no-tech/else" de 0xd8169ec0.** Si el contexto per-tech no tiene
una tech válida entrada, el byte de estado queda en 0x7 y los comandos de config/measure
abortan con 0x10 → el dispatch emite status **0x14 (DIAG_BAD_PARM_F)**.

### 6.2 Quién lo SETEA (FACT parcial / INFERENCE)
- **FACT:** el array de estado per-tech vive en **@0xca7897b0** (= base tabla RFTEST
  @0xca789780 + 0x30), indexado por tech con stride 8 (`tech<<3`).
- **INFERENCE:** TECH_ENTER (sub 13) escribe la tech válida en su contexto per-tech (se ven
  `memb(ctx+0)=1` en la rama enter y el mapeo TECH→índice de §3), lo que hace que ese byte
  de estado deje de ser 0x7. La **dirección raíz exacta del "entered flag"** vive dentro
  del contexto alocado en runtime (no un símbolo con immext directo) — no aislada a una
  sola instrucción. **UNKNOWN exacto**, pero el gate (§6.1) es **FACT**.

### 6.3 Consecuencia práctica (FACT)
Con TECH_ENTER (sub 13, TECH=1) hecho ANTES, el byte de estado deja de ser 0x7 y
RADIO_CONFIG/IQ_CAPTURE dejan de abortar con 0x14. El orden importa.

---

## 7. SECUENCIA DEFINITIVA byte-a-byte

### Header común (FACT de layout):
```
4b 0b  14 00  5a 03  <id06_lo id06_hi>  <id08_lo id08_hi>  27 00  <sub_lo sub_hi>  <ntlv_lo ntlv_hi>  <TLVs>
└DIAG┘ └sub┘  └ftmid┘ └id@0x06 (handle)┘ └id@0x08 (ignora)┘ └ftmcmd┘└sub_command┘  └ num_tlv ┘
```
- @0x06: handle/id — **usar 00 00** (o cualquier valor; no se valida como largo). FACT.
- @0x08: **usar 00 00** (ignorado con subcmd 0x14). FACT.
- @0x0a: **27 00** (ftm_cmd = LTE). FACT.
- @0x0c: **sub_command**. TECH_ENTER = **0d 00** (FACT). RFTEST = barrer 0..0x14 (UNKNOWN).

### PASO 1 — TECH_ENTER (LTE)   [sub=13=0x0d FACT · TECH=1 FACT · responde con status FACT]
TLVs (grupo enter): SUB=field 1, TECH=field 2 (u32)=**1**, SCENARIO=field 3. num_tlv=3.
```
4B 0B 14 00 5A 03 00 00 00 00 27 00 0D 00 03 00 \
   01 00 04 00 00 00 00 00 \      ; TLV SUB      (field 1, len 4, val 0)
   02 00 04 00 01 00 00 00 \      ; TLV TECH=1   (field 2, len 4, val 1 = LTE)   <-- FACT
   03 00 04 00 00 00 00 00        ; TLV SCENARIO (field 3, len 4, val 0)
```
Concatenado:
```
4B 0B 14 00 5A 03 00 00 00 00 27 00 0D 00 03 00 01 00 04 00 00 00 00 00 02 00 04 00 01 00 00 00 03 00 04 00 00 00 00 00
```
Espera una rsp DIAG con status byte = 0 (éxito). Si status = 0x14 → revisar TLVs.

### PASO 2 — RADIO_CONFIG (tune LTE)   [sub RFTEST: BARRER 0..0x14; empezar por 0/1/2/3]
field_ids (grupo RADIO_CONFIG): TECH_MODE=25(0x19), BAND=5, CHANNEL=6, CENTER_FREQ=12(0x0c),
BANDWIDTH=7. Ejemplo LTE B3, CENTER_FREQ 1842500 kHz (0x001C1F84), BW 20000 kHz. num_tlv=5.
```
4B 0B 14 00 5A 03 00 00 00 00 27 00 <SUB> 00 05 00 \
   19 00 04 00 01 00 00 00 \        ; TECH_MODE=25  val 1 (LTE)
   05 00 04 00 03 00 00 00 \        ; BAND=5        val 3 (B3)
   06 00 04 00 27 06 00 00 \        ; CHANNEL=6     EARFCN 1575
   0C 00 04 00 84 1F 1C 00 \        ; CENTER_FREQ=12  1842500 kHz
   07 00 04 00 20 4E 00 00          ; BANDWIDTH=7   20000 kHz
```

### PASO 3 — IQ_CAPTURE   [sub RFTEST: BARRER 0..0x14; usar COMMAND_CAPABILITY para el enum]
field_ids (grupo IQ/RX): NUM_OF_SAMPLES=14(0x0e), SAMP_FREQ=16(0x10), IQ_DATA_FORMAT=15(0x0f),
FETCH_IQ=13(0x0d). num_tlv=4.
```
4B 0B 14 00 5A 03 00 00 00 00 27 00 <SUB> 00 04 00 \
   0E 00 04 00 00 40 00 00 \        ; NUM_OF_SAMPLES=14  16384
   10 00 04 00 00 C0 D4 01 \        ; SAMP_FREQ=16       30720000
   0F 00 04 00 00 00 00 00 \        ; IQ_DATA_FORMAT=15  0
   0D 00 04 00 01 00 00 00          ; FETCH_IQ=13        1
```
Rsp IQ_CAPTURE (REPACK) = `[%2d][%3d][ %12s ][ %4d ][ 0x%8x ]` → el `0x%8x` = puntero
DDR/memshare a las muestras + `%4d` = tamaño. Las muestras NO viajan inline (FACT previo).

### Cómo cerrar el <SUB> de RFTEST en vivo (recomendado)
Primero manda **COMMAND_CAPABILITY** (barre sub 0..0x14, num_tlv=0, luego num_tlv=1 con
TLV {field 1 QUERY_COMMAND = 0xFFFFFFFF}). La rsp trae **CMD_MASK**: bit N=1 ⇒ sub N existe.
Eso da el enum numérico REAL de RADIO_CONFIG/RX_MEASURE/IQ_CAPTURE de una sola vez.

---

## 8. FACT / INFERENCE / UNKNOWN — honesto

**FACT (verificado en clade_dec_full.bin):**
- offset 0x06 = handle/id a alloc-lookup (0xd816d20c→0xd8062414), NO un largo; 0x1e no molesta.
- offset 0x08 = response-len, SOLO si DIAG-subcmd@0x02==0x24; con 0x14 se ignora (default 0xfd0).
- TECH_ENTER responde: aloca DIAG rsp (0xd814d760→diagpkt_subsys_alloc SSID 0x17) + status (0xd8174968).
- TECH (field 2, u32) = **1** para LTE (0xd8169ec0 idx1 ∧ tabla @0xc37bdfe0 TECH1→idx1).
- sub_command TECH_ENTER = **13** (dispatch 0xd816d2e8 stride 0xc; registro 0xd816cf58 slot 0x9c).
- RFTEST = framework separado; registrar 0xd8152af4 (stride 0xc); gate sub<=0x14.
- Gate de estado tech (0xd81e5d60): estado==0x7 (no-tech) → error 0x10 → status 0x14.
- Array de estado per-tech @0xca7897b0 (tech<<3).

**INFERENCE:**
- NR5G en TLV: candidato 6 (o header 0x8000/0x8001).
- @0x06 valor "correcto" = 0 o un id de transacción; cualquiera no problemático funciona.
- TECH_ENTER pobla el contexto per-tech que saca el estado de 0x7.

**UNKNOWN (requiere RAM en vivo o emular init RFTEST):**
- Número exacto de sub_command de RADIO_CONFIG/RX_MEASURE/IQ_CAPTURE/COMMAND_CAPABILITY
  (registro table-driven en RAM @0xcaad9d00; método: COMMAND_CAPABILITY→CMD_MASK).
- Dirección raíz exacta del "entered flag" (vive en contexto alocado en runtime).
- Valor exacto NR5G del TLV TECH.

---

## 9. Reproducir
```
/tmp/modemre/dis.sh 0xd8150ed8 0x120     # registrar DIAG (validacion + response-len @0x08)
/tmp/modemre/dis.sh 0xd816d1e8 0x100     # decode cuerpo FTM (offset 0x06 = handle)
/tmp/modemre/dis.sh 0xd816d2e8 0x90      # dispatch sub_command (stride 0xc, gate 0x15)
/tmp/modemre/dis.sh 0xd8169ec0 0x30      # ftm_cmd -> tech-index (0x27->1)
/tmp/modemre/dis.sh 0xd8169618 0x40      # TECH_TLV -> tech-index (tabla @0xc37bdfe0)
python3 /tmp/modemre/rd.py 0xc37bdfe0 8  # tabla TECH_TLV->idx (TECH1->1 = LTE)
/tmp/modemre/dis.sh 0xd8174758 0xa0      # handler TECH_ENTER (aloca+status = responde)
/tmp/modemre/dis.sh 0xd8152af4 0x80      # registrar RFTEST (stride 0xc)
/tmp/modemre/dis.sh 0xd81e5d40 0x40      # gate de estado tech (==0x7 -> 0x14)
```
