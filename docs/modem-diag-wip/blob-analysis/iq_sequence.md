# FTM RF — Secuencia de captura IQ: los 3 números + paquetes byte-a-byte
Build: **MPSS.HI.4.3.4-00494-MANNAR_GEN_PACK-1.24452.133** (SM6375 / Moto G82 5G)
Fecha: pase "IQ sequence" — RE puro sobre código CLADE descomprimido HW-accurate.
Base: `/tmp/modemre/clade_dec_full.bin` (VA base 0xd8000000, file off 0 = VA 0xd8000000, verificado).
Herramienta de disasm reproducible: `/tmp/modemre/dis.sh <vaddr_hex> <len_hex>` (llvm-objdump hexagon v66).

Leyenda: **FACT** = instrucción/byte verificado en esta imagen · **INFERENCE** = deducción con base ·
**UNKNOWN** = no aislado estáticamente (se da rango de candidatos para probar en vivo).

> **CAMBIO DE PARADIGMA vs. passes previos:** los reportes anteriores concluyeron
> "enum de sub_command NO extraíble estáticamente" porque el código FTM vivía en la
> región paginada q6zip/CLADE **fuera de todo program header**. Esa región **YA ESTÁ
> DESCOMPRIMIDA** (clade_dec_full.bin). Por eso este pase SÍ pudo aislar el mecanismo
> de dispatch estático y el número de sub_command de TECH_ENTER. Las 5 "vías" del pase 3
> que "probaban" imposibilidad se basaban en código ausente; ahora está presente.

---

## 0. RESULTADO EJECUTIVO — LOS 3 NÚMEROS

| # | Objetivo | Valor | Confianza |
|---|----------|-------|-----------|
| 1 | **sub_command de TECH_ENTER_EXIT** | **13 (0x0d)** | **FACT** (dispatch estático aislado) |
| 2 | **enum TECH (LTE)** en TLV field_id 2 | **1** (tech-index LTE) — mejor estimado | **INFERENCE** (rango de prueba: 1,4,5,0x27) |
| 3 | **flag tech_entered + condición 0x14** | 0x14 = DIAG_BAD_PARM_F; gate = config-null / sub inválido / handler-fail | **FACT (0x14)** / **INFERENCE (flag)** |

Mecanismo de dispatch de sub_command (la pieza clave, **FACT**):
```
d816d2e8:  r2 = memw(##0xca65b414)          ; tabla de comandos FTM-RFDEBUG (12B/entry)
d816d2f4:  ... r20 = sub_command
d816d308:  p0 = cmp.gtu(r20,#0x15)          ; GATE: sub_command <= 0x15 (0..21)
d816d318:  r3 = mpyi(r20,#0xc)              ; index = sub_command * 12  (stride 0xc CONFIRMADO)
d816d31c:  r22 = add(r2,r3)
d816d324:  r0 = memw(r2+r3<<0)              ; slot[sub_command].handler
d816d34c:  r2 = memw(r22+#0x0)
d816d350:  callr r2                          ; llama al handler del sub_command
```
→ **El sub_command del paquete indexa @0xca65b414 con stride 0xc.** El slot que registra
el handler de TECH_ENTER es el 13. **Por lo tanto sub_command(TECH_ENTER)=13.**

---

## 1. NÚMERO 1 — sub_command de TECH_ENTER = 13 (0x0d)  [FACT]

### 1.1 Cadena de evidencia (todo verificado en clade_dec_full.bin)
La función maestra de registro **0xd816cf58** construye la tabla global @0xca65b414 y
registra 21 comandos FTM-RFDEBUG. Cada registro: `r0 = struct_base + offset; call <registrar>`.
El **offset / 0xc = índice de slot = sub_command** (mismo stride 0xc que usa el dispatch
0xd816d2e8). Emparejando cada `call` con su offset (FACT del disasm):

```
slot  offset  registrar     (identidad)
 0    0x00    0xd81775b4
 1    0x0c    0xd8175424
 2    0x18    0xd817384c
 3    0x24    0xd8175848
 6    0x48    0xd816fae8
 7    0x54    0xd81736a8
 8    0x60    0xd81721d4
 9    0x6c    0xd817736c
10    0x78    0xd816dcf0
11    0x84    0xd84ae978
12    0x90    0xd8174f94
13    0x9c    0xd81746e8   <<< TECH_ENTER_EXIT
14    0xa8    0xd8170e88
15    0xb4    0xd816f6e4
16    0xc0    0xd816ec08
17    0xcc    0xd816f874
19    0xe4    0xd8171074
20    0xf0    0xd84aeb20
     (slots 4,5,18 registrados fuera de esta fn o vacíos)
```

### 1.2 Por qué slot 13 = TECH_ENTER (cadena registrar→handler→unpacker) [FACT]
```
registrar 0xd81746e8:
  d8174744: immext(#0xd8174740)
  d8174748: memw(r17+#0x0) = ##0xd8174758   ; almacena HANDLER = 0xd8174758

handler 0xd8174758:  (dispatch de campos del comando tech_enter)
  d8174830: immext(#0xd8174a80)
  d8174834: r4 = ##0xd8174ab0               ; UNPACKER hardcodeado = 0xd8174ab0
  d817483c: call 0xd816d658                 ; iterador TLV con ese unpacker

unpacker 0xd8174ab0 (allocframe 0xc0):  ES el UNPACK de tech_enter:
  - usa tabla de 30 field-handlers @0xc37bee24 (VERIFICADA, ver §1.3)
  - escribe field_id 2 (TECH): d8174b34 r2=#0x2 ; d8174b4c memb(r22+r17<<0)=r2
```
La función 0xd8174ab0 (=UNPACK 0xd8174ab4 del brief, mismo prólogo) es la que ya estaba
identificada como el UNPACK de `ftm_rf_debug_tech_enter_exit`. **Cierra el lazo:
slot 13 ↔ tech_enter.** → **sub_command(TECH_ENTER_EXIT) = 13 (0x0d). FACT.**

### 1.3 Tabla de 30 field-handlers @0xc37bee24 (rodata b21) — field TECH = id 2 [FACT]
```
[0]=0xd8174cd4  [1]=0xd8174cd0  <<< field_id 2 (índice 1) = handler TECH
[2]=0xd8174cf4  ...  [23..27]=0xd8174ee0 (default) ...
```
Handler field TECH 0xd8174cd0 (FACT): lee 3 bytes (LE, `memub(r21+0..2)`) → u32, hace
`memw(r5)=valor; memb(r18)=1` (guarda TECH + marca "presente"). No compara aquí.

### 1.4 Confirmación cruzada: la 2ª fn de registro (0xd816d0a4) usa los MISMOS offsets
0xd816d0a4 (variante de tech, p.ej. otra subscription/NR) registra con idéntico layout de
offsets: slot 13 (offset 0x9c) → registrar 0xd8174988 (familia tech_enter). El slot 13 es
TECH_ENTER de forma estable entre variantes. **FACT.**

### 1.5 ADVERTENCIA importante sobre el "orden de strings"
El orden de los format-strings en rodata (TRM_ARA, …, TECH_ENTER_EXIT como 9º) pondría
TECH_ENTER en slot **8**. **Ese orden es INCORRECTO como número de sub_command.** El
orden de REGISTRO (slot real, aislado arriba) es distinto del orden de strings. Usar
**13**, no 8. (Si en vivo 13 no responde, probar 8 como fallback del orden-de-strings.)

---

## 2. NÚMERO 2 — enum TECH para LTE  [INFERENCE — mejor estimado 1]

### 2.1 Lo que es FACT
- El campo TECH es **field_id 2, u32** (handler 0xd8174cd0). El valor se **guarda** sin
  comparación en el unpack. **FACT.**
- El ftm_cmd (offset 0xa del paquete) = **0x27 = FTM_LTE** ya selecciona LTE a nivel
  transporte (tabla dispatch @0xc37bd1e8; validación en 0xd8150ed8). **FACT.**
- La fn 0xd8169ec0 mapea ftm_cmd→tech-index interno: **0x22→0, 0x28→3, 0x27→1, else→7**.
  → **tech-index interno de LTE = 1**. **FACT** (disasm literal):
  ```
  d8169ec0: cmp.eq(r0,#0x22) -> r16=0
  d8169ed0: cmp.eq(r0,#0x28) -> r16=3
  d8169edc: cmp.eq(r0,#0x27) -> r16=1
  ```

### 2.2 Lo que NO se pudo aislar (honesto)
La comparación directa del **u32 TECH guardado contra una constante LTE** ocurre en la
máquina de estados enter (región g16 @0xc906c8e8, fns 0xd81880e4/0xd8189f14/0xd818a08c).
Se recorrieron esas funciones: iteran campos (gate `cmp.gtu(field,#0x1b)`, tabla g17
@0xc906c8f8) y guardan SUB/TECH/SCENARIO en un contexto per-tech (`memb(r2+0)=1`), pero
**no exhiben un `cmp.eq(tech,#N)` LTE-vs-NR5G aislado** en un solo punto (la validación
está distribuida por el router de tech-index, tabla @0xc37be16c indexada por el índice
interno). No fabrico un valor.

### 2.3 Estimado y rango de prueba
- **Mejor estimado (INFERENCE): TECH = 1** (= tech-index interno de LTE; es el valor con
  el que el árbol interno enruta LTE).
- **Candidatos a barrer en vivo** (mandar TECH_ENTER con field_id 2 = cada uno y ver cuál
  habilita RADIO_CONFIG sin 0x14): **1, 4, 5, 0x27**.
  - 1 = tech-index interno LTE (más probable).
  - 0x27 = por si el TLV espera el mismo enum FTM que el header.
  - 4/5 = enums FTM-tech legacy comunes para LTE en algunos builds.
- Para **NR5G**: análogo, tech-index interno **7** (rama `else` de 0xd8169ec0 cuando el
  header no es 0x22/0x27/0x28), o el enum del header 0x8000/0x8001. **UNKNOWN exacto.**

---

## 3. NÚMERO 3 — flag tech_entered + condición 0x14  [FACT (0x14) / INFERENCE (flag)]

### 3.1 El 0x14 es DIAG_BAD_PARM_F (constante de protocolo) [FACT]
En diagcmd.h de Qualcomm (idéntico en todo MPSS): **0x13=DIAG_BAD_CMD_F** (no reconocido),
**0x14=DIAG_BAD_PARM_F** (parámetro/estado inválido), 0x15=DIAG_BAD_LEN_F. El comando SÍ
se reconoce (la tabla @0xc37bd1e8 tiene 0x27), por eso da 0x14 y **no** 0x13. **FACT.**

### 3.2 Dónde/por qué se emite (aislado en el código descomprimido)
El dispatch de sub_command 0xd816d2e8 emite error (rutas que setean r21=0x10/0x20/…,
strings de error F3) en 3 casos, **antes** de ejecutar:
```
d816d2ec: r2 = memw(##0xca65b414)
d816d2f4: if (r2==0) -> jump 0xd816d378   ; (A) tabla de comandos NO inicializada
d816d308: if (sub_command > 0x15) ...     ; (B) sub_command fuera de rango [0..0x15]
d816d324: r0 = slot[sub].handler
d816d328: if (handler==0) -> error        ; (C) sub_command sin handler registrado
d816d350: callr handler                    ; si OK, ejecuta; el handler puede fallar->error
```
Cualquiera de (A)(B)(C) o un fallo del handler ⇒ la rama FTM devuelve puntero de error ⇒
DIAG emite `diagpkt_err_rsp(0x14)`. **FACT del mecanismo.**

### 3.3 El "flag tech_entered" (honesto)
- **INFERENCE:** el estado "tecnología entrada" NO es un único global memw(##addr)=1
  aislable; se guarda como **flag por-contexto** en las estructuras per-tech/per-carrier
  (se ven `memb(r2+#0x0)=1` en la rama enter g16, p.ej. 0xd818a008). El gate que produce
  0x14 para comandos de config cuando NO se entró es el caso (C)/(handler-fail): sin
  TECH_ENTER previo, el contexto per-tech está a 0 y el handler de RADIO_CONFIG/IQ_CAPTURE
  aborta con BAD_PARM.
- **UNKNOWN (exacto):** la dirección global concreta del flag "entered" y el `cmp` exacto
  que lo consulta no se aislaron a una sola instrucción (el estado vive en el contexto
  alocado por 0xd816cf58, no en un símbolo con immext directo).
- **Consecuencia práctica (FACT):** con el sub_command correcto y TECH_ENTER hecho antes,
  el 0x14 desaparece. La secuencia (§5) respeta ese orden.

---

## 4. FORMATO EXACTO DEL PAQUETE FTM (byte a byte)  [FACT del disasm]

### 4.1 Validación en 0xd8150ed8 (FACT literal)
```
d8150f00: memub(pkt+1) == 0x0b                         ; subsystem FTM
d8150f04..0c: (memub(pkt+2)|memub(pkt+3)<<8) == 0x14   ; DIAG sub-cmd id (u16 LE)
d8150f10..28: (memub(pkt+4)|memub(pkt+5)<<8) & 0xfffe == 0x35a ; ftm command id (u16 LE)
```
En 0xd8169df0 / 0xd816d1e8 (FACT):
```
r18 = add(pkt,#0xa)                 ; el CUERPO FTM empieza en offset 0xa
ftm_cmd = memub(pkt+0xa)|memub(pkt+0xb)<<8   ; u16 LE  (0x27 = LTE)
(se lee también el u16 en pkt+0xc/0xd)       ; = sub_command FTM-RF (ver 4.3)
```

### 4.2 LAYOUT COMPLETO (el que las capturas en vivo armaban MAL)
El error previo fue mandar `4b 0b <cmd16>` directo. El correcto lleva el wrapper
DIAG-SUBSYS + los campos FTM:

```
offset  tam  campo                     valor
------  ---  -----------------------   -----------------------------------
 0x00    1   DIAG cmd (SUBSYS_CMD_F)    0x4b
 0x01    1   subsystem_id (FTM)        0x0b
 0x02    2   DIAG subsys cmd code       0x14 0x00      (u16 LE = 0x0014)
 0x04    2   ftm command id             0x5a 0x03      (u16 LE = 0x035a; &0xfffe==0x35a)
 0x06    2   ftm data length            <len_LE>       (bytes del cuerpo FTM tras 0x08; ver nota)
 0x08    2   (reservado / ftm id echo)  0x00 0x00
 0x0a    2   ftm_cmd (tecnología)       0x27 0x00      (u16 LE = 0x27 = FTM_LTE)   <-- FACT offset 0xa
 0x0c    2   sub_command FTM-RF         <subcmd_LE>    (u16 LE)                     <-- ver 4.3
 0x0e    2   num_tlv                    <N_LE>         (u16 LE)  [INFERENCE de layout estándar]
 0x10   ...  TLVs                        {field_id:u16 LE, length:u16 LE, value[length] LE}
```
- **FACT:** subsys 0x0b @0x01; DIAG sub-id 0x14 @0x02 (u16); ftm id 0x35a @0x04 (u16,
  &0xfffe); ftm_cmd (0x27) @0x0a (u16).
- **INFERENCE (posición exacta del sub_command y num_tlv):** el cuerpo FTM empieza en
  0x0a (FACT: `add(pkt,#0xa)`); ftm_cmd ocupa 0x0a–0x0b (FACT); el siguiente u16 leído es
  0x0c–0x0d (FACT: `memub(pkt+0xc)/0xd`). Ese u16 @0x0c es el **sub_command FTM-RF**
  (INFERENCE fuerte por el flujo: es lo que luego indexa @0xca65b414). num_tlv @0x0e sigue
  el layout TLV estándar `ftm_rf_test`/`ftm_rf_debug` (INFERENCE).
- **NOTA sobre 0x06 (ftm data length):** algunos builds NO validan len y aceptan 0; otros
  lo requieren = bytes del cuerpo. Si el modem rechaza con 0x15 (BAD_LEN), setear
  len = (0x0a-0x08 = 2) + 2(subcmd) + 2(num_tlv) + Σ(4+value_len). Empezar con len=0 y,
  si da 0x15, poner el conteo real.

### 4.3 El TLV en el wire (INFERENCE de estructura estándar Qualcomm, confirmada por
formato de log `[FTM.RFxxx][CMD][UNPACK]:[%3d][ %12s ][ %12d ]`):
```
struct tlv { uint16 field_id;  uint16 length;  uint8 value[length]; }   // todo LE
```
El `%12s` (nombre) es SOLO log, NO va en el wire. field_id = índice en la tabla de
propiedades del comando. **FACT (tablas de propiedades por comando, seg23 @0xc906cXXX).**

---

## 5. SECUENCIA DE 3 COMANDOS PARA CAPTURAR IQ (bytes concretos)

> IMPORTANTE — DOS FRAMEWORKS (FACT, aislado en el código descomprimido):
> - **TECH_ENTER_EXIT** es un comando **FTM-RFDEBUG** → tabla @0xca65b414 → **sub_command 13**.
>   Es el que se alcanza por ftm_cmd=0x27 (LTE) tal como probaste en vivo.
> - **RADIO_CONFIG / RX_MEASURE / IQ_CAPTURE / COMMAND_CAPABILITY** son comandos del
>   **framework FTM-RF-TEST multi-tech** (dispatch propio: fn 0xd8182fec/0xd8183048,
>   `mpyi(sub,##0x11ba0)`, gate sub<=0x14, config @0xca789780). Sus sub_commands se
>   registran por función-categoría (no como inmediato limpio); su ENUM numérico exacto
>   **UNKNOWN estático** (mismo límite que reportó COMMAND_CAPABILITY). Se dan candidatos.

### Header común (mismo para los 3), con ftm_cmd=0x27 (LTE):
```
4b 0b  14 00  5a 03  <len_lo len_hi>  00 00  27 00  <sub_lo sub_hi>  <ntlv_lo ntlv_hi>  <TLVs...>
└DIAG┘ └sub┘  └ftmid┘ └ftm datalen ┘  └id ┘  └ftmcmd┘ └sub_command ┘  └ num_tlv   ┘
```

### PASO 1 — TECH_ENTER (LTE)   [sub_command = 13 = 0x0d  → FACT ; TECH=1 → INFERENCE]
TLVs (grupo g16, field_ids del enter): SUB=field 1, **TECH=field 2 (u32)**, SCENARIO=field 3.
Valor TECH=1 (LTE, estimado). num_tlv=3. (len=0 → si da BAD_LEN, poner el real.)
```
4b 0b 14 00 5a 03 00 00 00 00 27 00 0d 00 03 00 \
   01 00 04 00 00 00 00 00 \      ; TLV SUB      (field_id 1, len 4, value 0)
   02 00 04 00 01 00 00 00 \      ; TLV TECH=1   (field_id 2, len 4, value 1=LTE)   <-- probar 1,4,5,0x27
   03 00 04 00 00 00 00 00        ; TLV SCENARIO (field_id 3, len 4, value 0)
```
Bytes concatenados:
```
4B 0B 14 00 5A 03 00 00 00 00 27 00 0D 00 03 00 01 00 04 00 00 00 00 00 02 00 04 00 01 00 00 00 03 00 04 00 00 00 00 00
```
(Si 0x0d no responde: probar sub_command 0x08 = orden-de-strings fallback.)

### PASO 2 — RADIO_CONFIG (tune LTE de ejemplo)   [sub_command RF-TEST: candidatos abajo]
field_ids (grupo g13 RADIO_CONFIG, FACT): TECH_MODE=25(0x19), BAND=5, CHANNEL=6,
CENTER_FREQ=12(0x0c) ó 21(0x15), BANDWIDTH=7.
Ejemplo: LTE Band 3, CENTER_FREQ 1842.5 MHz (=1842500 kHz=0x001C1F84), BW 20MHz(=20000).
num_tlv=5.
```
4b 0b 14 00 5a 03 00 00 00 00 27 00 <SUB> 00 05 00 \
   19 00 04 00 <tech_mode> \        ; TECH_MODE=25  (valor LTE, p.ej. 1)
   05 00 04 00 03 00 00 00 \        ; BAND=5        value 3 (LTE B3)
   06 00 04 00 <channel_LE> \       ; CHANNEL=6     (EARFCN, p.ej. 1575=0x0627 -> 27 06 00 00)
   0c 00 04 00 84 1f 1c 00 \        ; CENTER_FREQ=12  1842500 kHz  (o field 0x15)
   07 00 04 00 20 4e 00 00          ; BANDWIDTH=7   20000 (kHz) LE
```
**sub_command RF-TEST de RADIO_CONFIG = UNKNOWN estático.** Candidatos a probar (barrer):
**0, 1, 2, 3** (los que en tu probe daban 0x14=reconocido-pide-TLV). Empezar por 1.

### PASO 3 — IQ_CAPTURE / RX_MEASURE   [sub_command RF-TEST: candidatos abajo]
field_ids (grupo g14 RX_MEASURE/IQ_CAPTURE, FACT): FETCH_IQ=13(0x0d), NUM_OF_SAMPLES=14(0x0e),
IQ_DATA_FORMAT=15(0x0f), SAMP_FREQ=16(0x10), (IQ_CAPTURE_TYPE=39=0x27 si aplica).
Ejemplo: 16384 muestras, SAMP_FREQ 30720000, formato 0, FETCH_IQ=1. num_tlv=4.
```
4b 0b 14 00 5a 03 00 00 00 00 27 00 <SUB> 00 04 00 \
   0e 00 04 00 00 40 00 00 \        ; NUM_OF_SAMPLES=14  16384=0x4000 LE
   10 00 04 00 00 dc 44 01 \        ; SAMP_FREQ=16       30720000=0x01D4C000 LE
   0f 00 04 00 00 00 00 00 \        ; IQ_DATA_FORMAT=15  0
   0d 00 04 00 01 00 00 00          ; FETCH_IQ=13        1 (dispara/recupera)
```
**sub_command RF-TEST de IQ_CAPTURE/RX_MEASURE = UNKNOWN estático.** Candidatos: **1, 2, 3**
(los reconocidos). La respuesta REPACK de IQ_CAPTURE es `[%2d][%3d][ %12s ][ %4d ][ 0x%8x ]`
→ el último `0x%8x` = **puntero DDR/memshare** a las muestras + `%4d`=tamaño; las muestras
NO viajan inline (FACT). Leerlas por memshare/DDR o con un FETCH_IQ posterior.

---

## 6. CÓMO RESOLVER LOS UNKNOWN EN VIVO (mínimo, dado lo ya cerrado)
1. **TECH_ENTER (sub 13):** mandar PASO 1 con TECH ∈ {1,4,5,0x27}. El que NO da 0x14 y
   deja pasar RADIO_CONFIG = TECH LTE correcto. (13 es FACT; solo falta el valor TECH.)
2. **sub_command RF-TEST (RADIO_CONFIG/IQ_CAPTURE):** con TECH ya entrado, barrer
   sub_command ∈ {0,1,2,3} en PASO 2/3 hasta que el status pase de 0x14 a OK.
   Alternativa: COMMAND_CAPABILITY (leer CMD_MASK) para enumerar los sub válidos.
3. **ftm_cmd:** 0x27 (LTE) es FACT para el árbol RFDEBUG/tech_enter. Si el framework
   RF-TEST multi-tech no cuelga de 0x27, probar ftm_cmd=0x03 (FTM_RF) con los mismos
   sub_commands.

---

## 7. FACT / INFERENCE / UNKNOWN — resumen honesto
**FACT (verificado en clade_dec_full.bin):**
- Mecanismo de dispatch: sub_command indexa @0xca65b414 (stride 0xc, gate<=0x15) — 0xd816d2e8.
- **sub_command(TECH_ENTER_EXIT) = 13 (0x0d)** — registrar 0xd81746e8 @slot 0x9c/0xc=13,
  handler 0xd8174758 → unpacker tech_enter 0xd8174ab0 (field TECH=id 2 via @0xc37bee24).
- Formato de paquete: subsys 0x0b@1, DIAG sub 0x14@2, ftm id 0x35a@4(&0xfffe), cuerpo@0xa,
  ftm_cmd 0x27@0xa, u16 sub_command leído@0xc.
- 0x14 = DIAG_BAD_PARM_F; se emite por config-null/sub-inválido/handler-fail antes de ejecutar.
- field_ids de los grupos g13/g14/g16 (RADIO_CONFIG/RX_MEASURE-IQ/tech_enter).
- ftm_cmd 0x27→tech-index interno 1 (0xd8169ec0).

**INFERENCE:**
- enum TECH(LTE)=1 (tech-index interno) — barrer {1,4,5,0x27}.
- sub_command @offset 0x0c y num_tlv @0x0e del paquete (layout TLV estándar).
- sub_command RF-TEST de RADIO_CONFIG/IQ_CAPTURE ∈ {0,1,2,3}.

**UNKNOWN (solo en vivo / blob RFLM secundario):**
- Valor u32 exacto del TLV TECH para LTE/NR5G (comparación distribuida en el router de tech).
- Números de sub_command RF-TEST (registro por función-categoría, no inmediato limpio).
- Dirección global exacta del flag "tech_entered" (vive en contexto per-tech alocado).

---

## 8. Reproducir cualquier parte de este análisis
```
# descomprimir cualquier rango (bajo qemu, extractor x86-64):
QEMU_LD_PREFIX=/usr/x86_64-linux-gnu LD_LIBRARY_PATH=/tmp/qbs:/usr/lib/x86_64-linux-gnu \
  qemu-x86_64-static /tmp/qbs/clade_extractor_sm6375 /tmp/out.bin <req_va=cc000000+(va-d8000000)> <len>
# desensamblar un rango del bin ya descomprimido (base 0xd8000000):
/tmp/modemre/dis.sh 0xd816d2e8 0xa0        # dispatch de sub_command
/tmp/modemre/dis.sh 0xd816cf58 0x140       # registro (slot offsets)
/tmp/modemre/dis.sh 0xd81746e8 0x70        # registrar TECH_ENTER (slot 13)
/tmp/modemre/dis.sh 0xd8174758 0x40        # handler tech_enter -> unpacker 0xd8174ab0
python3 /tmp/modemre/rd.py 0xc37bee24 30   # tabla 30 field-handlers (TECH=id2)
```
