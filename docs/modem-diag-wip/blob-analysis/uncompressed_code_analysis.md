# Análisis del CÓDIGO NO COMPRIMIDO (b02/b04/b05/b08/b09/b10/b12/b13/b30) — DIAG core, dispatch FTM y tech-enter

Build: **MPSS.HI.4.3.4-00494-MANNAR_GEN_PACK-1.24452.133** (SM6375 / Moto G82 5G)
ELF: `/tmp/modemre/modem_full.elf`. Disassembly Hexagon (llvm-objdump-18).
Objetivo del pass: NO depender del q6zip. Buscar en el código R-E no comprimido el DIAG core,
el trampolín dlpager y toda lógica de tech-enter / 0x14 recuperable estáticamente.

Leyenda: **FACT** = verificado en bytes/disasm de esta imagen. **INFERENCE** = deducción de
convención Qualcomm. **UNKNOWN** = no determinable estáticamente en esta imagen.

--------------------------------------------------------------------------------
## 0. RESUMEN EJECUTIVO (honesto, leer primero)
--------------------------------------------------------------------------------
1. **La cadena de RUTEO DIAG→FTM completa está en rodata/data NO comprimida y la decodifiqué
   entera** (secciones 2 y 3). Esto es nuevo respecto al pass q6zip:
   - Master table DIAG (subsys) @**0xc8dc3b54** (seg b23, RW, filesz>0): `subsys=0x0B, cmd=0x4B → tabla 0xc37bd1e8`. **FACT.**
   - Tabla de cmd FTM @**0xc37bd1e8** (seg21 rodata): **76 entradas** `{cmd_lo:u16, cmd_hi:u16, handler:u32}`, TODAS con handler **0xd8150ed8**. Incluye **cmd 0x27 (FTM_LTE)** y **0x8000/0x8001 (FTM_NR5G)**. **FACT** (enum de cmd_id COMPLETO abajo).
   - Tabla de params tech-enter (g16) @**0xc906c8e8**: field_id **1=SUB, 2=TECH, 3=SCENARIO** (índice = field_id del TLV). **FACT.**
2. **El handler 0xd8150ed8 (y 0xd8150e24, 0xd815056c) NO está en ningún program header** →
   vive en la ventana paginada q6zip. Aparece **76 veces como DATO** (puntero en seg21) y
   **0 veces como código** en TODO el disasm no comprimido. **FACT.**
3. **El código de dispatch FTM real, el UNPACK, el tech-enter y el gating "No Tech active"
   NO están en el código no comprimido.** Verifiqué por VAddr (immediato `##` con y sin
   byte-swap/signo) que NINGÚN string clave del RF-test está referenciado en b02..b30
   (sección 4, 12 targets → 0 hits). **FACT (límite duro, ahora con evidencia por-VAddr).**
4. **SÍ hallé el productor de 0x14 (DIAG_BAD_PARM_F)**: función @**0xc0dabf00** (b13,
   compilada -O0) que hace `memw(pkt)=0; memb(pkt)=#0x14` y luego un `callr` (= un
   `diagpkt_err_rsp` que emite BAD_PARM). También la ruta de respuesta `diagpkt_rsp_send`
   @**0xc0d55c40** que valida `byte[+0x24] ∈ {0x13,0x14,0x15}` (BAD_CMD/BAD_PARM/BAD_LEN) y
   `byte[+0x25]==0x4d`. **FACT.** PERO el CALLER que DECIDE emitir 0x14 (el que chequea el
   flag tech_entered) está en el código paginado (sección 5).
5. **El descriptor dlpager @0xc8d0a338 NO cubre 0xd815xxxx**: su remap es `0xd4400000..0xd6539000`
   (pool distinto). El descriptor del window 0xd8xxxxxx (donde vive 0xd8150ed8) no aparece como
   struct limpio en el ELF (consistente con "metadata de páginas ausente" del pass q6zip). **FACT.**

--------------------------------------------------------------------------------
## 1. QUÉ SE BUSCÓ Y CÓMO (metodología de grep por VAddr)
--------------------------------------------------------------------------------
Hexagon carga direcciones de 32 bits con `immext(#hi)` + `Rd=##addr`; **llvm-objdump resuelve
y muestra el valor combinado**, pero lo imprime en **forma con signo** cuando el bit 31 está
puesto (p.ej. 0xc35b6784 → `r0 = ##-0x3ca4987c`). Por eso el grep correcto usa AMBAS formas:
`0x<va>` y `-0x<(2^32 - va)>`. Verificado con un load real:
```
c0cb683c: immext(#0xc35b6780)
c0cb6840: r0 = ##-0x3ca4987c        ; = 0xc35b6784  (string "/therm/mitigate/modem_bw_backoff")
```
El `immext` que muestra objdump es el HIGH con los 6 bits bajos en 0; el valor exacto está en
el `##`. Grep por el `##` (ambas formas) = fiable. `add(pc,##off)`: sólo 2 casos en b13, 0 en
b10 → esta imagen casi no usa PC-relative para strings; casi todo es `immext+##`. **FACT.**

--------------------------------------------------------------------------------
## 2. CADENA DE RUTEO DIAG → FTM (TODO en código/datos NO comprimidos) — FACT
--------------------------------------------------------------------------------

### 2.1 Master table DIAG (registro de subsistemas) — @0xc8dc3b40 (seg b23, RW, mapeado)
Array de `diagpkt_master_table_t` de **20 bytes** por entrada:
`{ u32 (cmd<<16 | subsys_id), u32 mask_lo, u32 mask_hi, u32 sub_table_ptr, u32 range }`.
Entradas limpias (FACT, dump directo):
```
 @0xc8dc3b40: subsys=0x000B cmd=0x0008  mask=000000ff/ffffffff  tbl=0xc37bd1a8  range=0x00ff0000
 @0xc8dc3b54: subsys=0x000B cmd=0x004B  mask=000000ff/ffffffff  tbl=0xc37bd1e8  range=0x00ff0000   <-- FTM
 @0xc8dc3b68: subsys=0x000A cmd=0x0001  mask=000000ff/ffffffff  tbl=0xc37bd440  range=0x00000003
```
=> **DIAG cmd 0x4B + subsys 0x0B ⇒ tabla FTM @0xc37bd1e8.** **FACT.**
(El único xref a 0xc37bd1e8 como puntero en toda la imagen está aquí, en 0xc8dc3b60.)

### 2.2 Tabla de cmd_id FTM — @0xc37bd1e8 (seg21 rodata, mapeado) — 76 entradas
Formato por entrada = **8 bytes**: `{ cmd_lo:u16, cmd_hi:u16 } , { handler:u32 }`.
En esta tabla cmd_lo==cmd_hi (un cmd por fila) y **TODOS los handler = 0xd8150ed8**.
Enum COMPLETO de cmd_id que cuelga de subsys 0x0B / cmd 0x4B (FACT, dump):
```
0x00 0x01 0x02 0x03 0x07 0x08 0x09 0x0a 0x0b 0x0d 0x10 0x11 0x12 0x14 0x15 0x1b 0x1d 0x1e
0x1f 0x20 0x21 0x22 0x23 0x24 0x25 0x27 0x28 0x2f 0x30 0x31 0x32 0x33 0x38 0x39 0x3a 0x3b
0x3c 0x3d 0x3e 0x3f 0x41 0x42 0x43 0x44 0x45 0x46 0x47 0x4a 0x4b 0x4c 0x4d 0x4e 0x4f 0x50
0x51 0x52 0x53 0x54 0x55 0x56 0x57 0x65 0x66 0x67 0x68 0x69 0x79 0x7a 0x7b 0x7c 0x7e 0x80
0x8000 0x8001
```
- **0x27 = FTM_LTE** presente (entry [8] de la tabla). **FACT.**
- **0x8000 / 0x8001 = FTM_NR5G** presentes (últimas 2). **FACT.**
- Terminador tras 76 filas @0xc37bd448 = `{0x0003,0x0000, handler=0x00000000}`. **FACT.**

Tablas hermanas (mismo formato):
```
 @0xc37bd1a8 (cmd 0x08 / subsys 0x0B): cmds 0x69,0x7a,0x7b,0x7c,0x7e,0x14,0x7f → handler 0xd8150e24
                                        cmd 0x8003 → handler 0xd815056c
 @0xc37bd440 (cmd 0x01 / subsys 0x0A): [0]=0x0000..0xffff → handler 0xd8150ed8 (wildcard)
```
=> subsys 0x0B tiene DOS grupos de comandos: cmd 0x08 (RF cal/otros, handler 0xd8150e24) y
cmd 0x4B (FTM RF-test, handler 0xd8150ed8). **FACT.**

### 2.3 Confirmación de que el handler está en el q6zip window (no estático)
`0xd8150ed8`, `0xd8150e24`, `0xd815056c` → **inseg()=None** (ningún PH los cubre).
`0xd8150ed8` aparece **76 veces, todas en seg21 rodata** (como puntero de tabla), **0 en código**.
=> el salto al dispatcher se resuelve por **fallo de página dlpager** (MMU exception sobre la
ventana virtual), no por un `call`/`##` estático. **FACT.**

--------------------------------------------------------------------------------
## 3. TABLA DE PARAMS DEL TECH-ENTER (g16 @0xc906c8e8) — FACT, mapeada
--------------------------------------------------------------------------------
Array de punteros a nombre indexado por **field_id** (dump directo, strings resueltos):
```
 field_id 0 -> UNASSIGNED
 field_id 1 -> "SUB"
 field_id 2 -> "TECH"
 field_id 3 -> "SCENARIO"
 field_id 4 -> "SUBSCRIBER"   (empieza el bloque de otra tabla adyacente)
 ...
```
=> El TLV de **TECH_ENTER_EXIT** usa **field_id 1=SUB, 2=TECH, 3=SCENARIO**. **FACT.**
(Concuerda con el layout del format-string `[FTM.RFDEBUG][TECH_ENTER_EXIT][UNPACK]:[%3d][%12s][%12d]`
que imprime `[field_id][name][value]`.)

--------------------------------------------------------------------------------
## 4. LO QUE NO ESTÁ EN EL CÓDIGO NO COMPRIMIDO (con evidencia por-VAddr) — FACT
--------------------------------------------------------------------------------
Grep por VAddr (ambas formas de signo) de 12 targets en TODOS los disasm no comprimidos
(b02,b04,b05,b08,b09,b10,b12,b13,b30) → **0 hits** en cada uno:
```
  TECH_ENTER_EXIT fmt       0xc37beef0   0 hits
  ftm_rf_debug_tech_enter_exit.c 0xc37bef2d 0 hits
  [FTM.RFTEST][RADIO_CONFIG] 0xc37c0320   0 hits
  [FTM.RFTEST][COMMAND_CAP.] 0xc37c03cc   0 hits
  [FTM.RFTEST][RX_MEASURE]   0xc37c0590   0 hits
  [FTM.RFTEST][IQ_CAPTURE]   0xc37c08f4   0 hits
  ftm_common_dispatch.c      0xc356945d   0 hits
  ftm_common_dispatch.c(2)   0xc37bda8d   0 hits
  ftm_lte_common_dispatch.c  0xc3569e11   0 hits
  "No Tech is active..."     0xc35b6fd2   0 hits
  tabla dispatch FTM         0xc37bd1e8   0 hits (como immediato de código)
  handler dispatcher         0xd8150ed8   0 hits (como immediato de código)
```
También: **el string `ftm_lte_rex_dispatch` NO EXISTE en esta imagen** (grep binario del ELF:
0 ocurrencias). El nombre real de la fuente es **`ftm_lte_common_dispatch.c`** (@0xc3569e11)
y **`ftm_common_dispatch.c`** (@0xc356945d / 0xc37bda8d). El string `not supported - %d`
tampoco existe literal. **FACT.** (Corrijo la premisa del enunciado: esos strings específicos
no están; los equivalentes reales sí, pero su CÓDIGO está en el q6zip.)

CONCLUSIÓN sección 4: **el dispatch FTM, el sub-decode de 0x27, el UNPACK de TLVs, el
tech-enter y el gating por "tech activa" están 100% dentro de la ventana q6zip.** El código
no comprimido sólo contiene el DIAG **core/ruteo** (que llega hasta el puntero 0xd8150ed8) y
las **tablas** (rodata) — no el cuerpo del handler. **FACT.**

--------------------------------------------------------------------------------
## 5. EL 0x14 (DIAG_BAD_PARM_F) — qué SÍ se ve en código no comprimido
--------------------------------------------------------------------------------
### 5.1 Productor de 0x14 — `diagpkt_err_rsp`-like @0xc0dabf00 (b13, -O0) — FACT
Función que construye un paquete de error con código **0x14** hardcodeado:
```
c0dabf00: allocframe(#0x20)
...                                   ; guarda args r0..r3 en frame
c0dabfa4: r2 = memw(r30+#-0xc)
c0dabfa8: memw(r2+#0x0) = #0x0        ; limpia el dword
c0dabfb0: memb(r2+#0x0) = #0x14       ; <-- escribe 0x14 (DIAG_BAD_PARM_F) en byte[0] del pkt
c0dabfb4: ...
c0dabfe4: callr r4                    ; callback (send/commit del pkt)
c0dac000: r2 = memb(r30+#-0x11); p0 = cmp.eq(r2,#0x3f)  ; 0x3f='?' marcador BAD_CMD
```
=> Este es un emisor de respuesta de error DIAG con BAD_PARM=0x14 (y también maneja '?'=0x3f).
El caller que lo invoca (y que decide 0x14 por estado inválido) NO está en el código no
comprimido → es el dispatch FTM paginado. **FACT (productor) / UNKNOWN (caller/condición exacta).**

Otros productores de 0x14 como código de error (memb(pkt)=#0x14): @0xc0dadb30, y variantes
`r0/r1/r2 = #0x14` en múltiples funciones DIAG de b13 (c0d9d518, c0daa574, c0dde024, ...).
(Los c0dd0ab4/c0dd2444 NO son error-codes: ahí 0x14 es un contador/offset junto a floats.)

### 5.2 Ruta de respuesta — `diagpkt_rsp_send` @0xc0d55c40 (b13) — FACT
Valida el paquete de respuesta antes de enviarlo:
```
c0d55ccc: r3 = memub(r16+#0x25)
c0d55cd0: p0 = cmp.eq(r3,#0x4d)            ; byte[+0x25]==0x4d ('M', marcador de rsp válido)
c0d55cd8: r4 = memub(r16+#0x24)            ; (rama !p0) byte[+0x24] = código
c0d55ce0: r2 = add(r4,#-0x13)              ; r2 = code - 0x13
c0d55ce8: r2 = and(r2,#0xff)
c0d55cec: p0 = cmp.gtu(r2,#0x2)            ; si (code-0x13) > 2  => NO es {0x13,0x14,0x15}
          ...                              ; => trata 0x13/0x14/0x15 como "Invalid command response"
c0d55d00: immext(#0xc35ca4c0)
c0d55d04: r2 = ##-0x3ca35b34               ; = 0xc35ca4cc "diagpkt_rsp_send: Invalid command response - ..."
c0d55d0c: call 0xc0992904                  ; log F3
```
=> El error-response se codifica con **byte[+0x24] ∈ {0x13=BAD_CMD, 0x14=BAD_PARM, 0x15=BAD_LEN}**
y marcador `0x4d` en +0x25. Confirma que **0x14 = DIAG_BAD_PARM_F** en esta build. **FACT.**
(Es el ÚNICO xref al string 0xc35ca4cc en todo el código no comprimido — b13 línea 32539.)

### 5.3 Ruteo por command-code — dentro de diagpkt_rsp_send @0xc0d55e00+ — FACT
El dispatcher de respuesta clasifica por el primer byte del pkt:
```
c0d55e20: p0 = cmpb.eq(r22,#0x80)  → jump 0xc0d36424
c0d55e30: p1 = cmpb.eq(r22,#0x4b)  → jump 0xc0d36424   ; 0x4B = DIAG_SUBSYS_CMD_F (FTM llega por aquí)
c0d55e5c: cmpb.eq(r22,#0x29) ; 0x73 ; 0x7d ; 0x82      ; otros cmd-codes DIAG
```
Para 0x4B lee subsys id en `memub(r17+#0x1)` (c0d55f30). **FACT.** (Ruteo core; el sub-decode
FTM en sí es el 0xd8150ed8 paginado.)

--------------------------------------------------------------------------------
## 6. dlpager / trampolín — lo que se ve en código no comprimido — FACT
--------------------------------------------------------------------------------
- **Función de init/log dlpager @0xc0db4014+ (b13)**: lee el descriptor en **0xc8d0a338** campo
  a campo (`memw(##0xc8d0a338/33c/340/344/348)`) y lo pasa a la fn de log 0xc0992904. Confirma
  el layout del descriptor:
  ```
  0xc8d0a338: comp_start  = 0xcc000000  (= seg26)
  0xc8d0a33c: comp_end    = 0xce13a000
  0xc8d0a340: remap_start = 0xd4400000
  0xc8d0a344: remap_end   = 0xd6539000
  0xc8d0a348: field       = 0x00c00000
  ```
- **PERO 0xd8150ed8 (dispatcher FTM) NO cae en [0xd4400000,0xd6539000).** Ese descriptor es de
  OTRO pool/window. El descriptor del window 0xd8xxxxxx (que contiene 0xd8150ed8) **no aparece
  como struct {cs,ce,rs,re} limpio** en el ELF: al barrer pares (rs<=0xd8150ed8<re) sólo salen
  falsos positivos en seg21 (rangos de funciones C++/EH dentro del window, no descriptores de
  compresión). **FACT.** Consistente con el hallazgo previo (metadata de páginas q6zip ausente
  de esta imagen reensamblada).
- Hay **168 immext(#0xd8xxxxxx) en b10 y 146 en b13** (referencias a la ventana paginada) y
  **4477 refs al remap 0xd4/0xd6 en b10** → el código no comprimido SÍ referencia direcciones
  del window (datos/funciones remapeadas), pero **ninguna** es 0xd815xxxx (el dispatcher).
  El salto al dispatcher NO es un `##0xd8150ed8` en código; es indirecto vía el puntero de la
  tabla rodata + fallo de página. **FACT.**

--------------------------------------------------------------------------------
## 7. ENUM TECH (valor numérico de LTE/NR5G) — lo hallado — UNKNOWN numérico
--------------------------------------------------------------------------------
- Tabla de nombres de RAT (display) @**0xc37857dc** (seg21, mapeada): tokens null-separados
  ` GSM`, ` TDS`, ` LTE`, ` NR5G` (en ese orden). Es un array de display/log, **no** el enum
  FTM `rfcom_mode_enum`; su orden local (GSM/TDS/LTE/NR5G) no es prueba del valor numérico FTM.
  **FACT (existe) / no usable como enum FTM.**
- En `seg27_dec.bin` (QSHRINK descomprimido) los símbolos de enum aparecen SÓLO como nombres en
  strings de assert (sin valor): `CXM_TECH_LTE`, `CXM_TECH_LTE2`, `RFCOMMON_SBE_TECH_NR5G_SUB6`,
  `RFCOMMON_SBE_TECH_NR5G_MMW`, `RFCOM_NUM_MODES`, `NR5G_LL1_CAL_FTM_RF_MODE_CAL`. **FACT (nombres).**
- El "TECH" @0xc40d24f5 pertenece a una lista de propiedades de DATA-CALL/OPRT, no a FTM.
- **VALOR NUMÉRICO de TECH para LTE y NR5G = UNKNOWN estático.** El enum se compila a
  constantes dentro del código FTM paginado (q6zip). No hay tabla `tech_value` numerada en
  rodata/seg27 mapeados. **UNKNOWN.**

--------------------------------------------------------------------------------
## 8. MAPEO sub_command → nombre/handler del RF-test — UNKNOWN (no en rodata)
--------------------------------------------------------------------------------
- **NO existe** en el código/rodata no comprimido una tabla maestra `sub_command → handler` ni
  `sub_command → nombre` para el RF-test bajo cmd 0x4B. Las cadenas de nombre (RADIO_CONFIG,
  COMMAND_CAPABILITY, RX_MEASURE, ...) están **embebidas dentro de los format-strings**
  `[FTM.RFTEST][<NAME>][UNPACK]` (seg21), no como array de punteros indexable, y esos
  format-strings **no se referencian** en b02..b30 (sección 4). El array se arma en el código
  paginado. **FACT (ausencia) / UNKNOWN (los números).**
- Lo que SÍ está mapeado y es útil: las tablas de **propiedades por comando** (g13..g24 en
  0xc906c630..0xc906ce18), indexadas por field_id (como g16 en §3). Dan los field_id/nombres de
  cada propiedad, pero NO el índice numérico del sub_command dentro de 0x4B. (Ver pass previo
  `tech_enter_decoded.md` §6 para el detalle de cada g-tabla.)

--------------------------------------------------------------------------------
## 9. CIERRE FACT / INFERENCE / UNKNOWN
--------------------------------------------------------------------------------
**FACT (probado en esta imagen, código/datos no comprimidos):**
- Ruteo DIAG→FTM completo y estático: master @0xc8dc3b54 (subsys 0x0B, cmd 0x4B) → tabla
  @0xc37bd1e8 (76 cmds, cmd 0x27=LTE, 0x8000/1=NR5G) → handler **0xd8150ed8** (paginado).
- Segundo grupo subsys 0x0B/cmd 0x08 → tabla @0xc37bd1a8 → handlers 0xd8150e24 / 0xd815056c.
- g16 tech-enter: field_id **1=SUB, 2=TECH, 3=SCENARIO**.
- 0x14 = DIAG_BAD_PARM_F: productor @0xc0dabf00 (`memb(pkt)=#0x14`), validador
  @0xc0d55c40 (byte[+0x24]∈{0x13,0x14,0x15}, marcador 0x4d en +0x25), string
  @0xc35ca4cc referenciado sólo en b13.
- Ruteo por command-code 0x80/0x4b/0x29/0x73/0x7d/0x82 en diagpkt_rsp_send (@0xc0d55e20+).
- Descriptor dlpager @0xc8d0a338 (remap 0xd44–0xd65, NO cubre 0xd815xxxx).
- El handler 0xd8150ed8 aparece 76× como dato en seg21, 0× como código.
- Los 12 strings clave del RF-test/tech-enter/dispatch NO están referenciados en NINGÚN
  segmento de código no comprimido.

**INFERENCE:**
- El caller que emite 0x14 bajo 0x27 es el sub-dispatch FTM paginado, que rechaza sub-comandos
  de config/measure si falta el estado tech-enter (mecanismo "sub reconocido, estado/param
  inválido" → BAD_PARM). Consistente con el probe en vivo (sub 1–4 → 0x14; 0/5/6 → echo).
- sub_command de tech-enter ∈ {0,5,6} bajo 0x27 (control sin TLV obligatorio).

**UNKNOWN (sólo en vivo o descomprimiendo el q6zip de código, ausente de esta imagen):**
- Número exacto de cada sub_command del RF-test (RADIO_CONFIG/RX_MEASURE/IQ_CAPTURE/
  COMMAND_CAPABILITY/TECH_ENTER) bajo cmd 0x4B.
- **Valor numérico de TECH para LTE y NR5G** (el enum rfcom_mode está compilado en el paginado).
- Nombre/dirección exactos del flag `tech_entered` que dispara el 0x14 (su chequeo está en
  0xd8150ed8, paginado).

--------------------------------------------------------------------------------
## 10. RECOMENDACIÓN PARA CERRAR (accionable)
--------------------------------------------------------------------------------
El límite estático es DURO y ahora está probado por-VAddr, no sólo por metadata. Vías reales:
- **En vivo (única que cierra los números)**: usar COMMAND_CAPABILITY (QUERY_COMMAND=1,
  CMD_MASK=3, ver `tech_enter_decoded.md` §7) para leer el bitmask de sub_commands soportados
  bajo 0x4B/0x0B/0x27, y barrer TECH=0..15. El ruteo DIAG↔FTM confirmado aquí garantiza que el
  paquete `4B 0B 27 00 SS 00 NT 00 {TLVs}` llega al dispatcher.
- **Descompresión q6zip real**: requiere volcado EN VIVO del window virtual 0xd8xxxxxx (o de la
  tabla de páginas dlpager que NO está en el ELF). Con la página que contiene 0xd8150ed8
  desensamblada, el sub-decode de 0x27 y el chequeo de tech_entered serían recuperables.
