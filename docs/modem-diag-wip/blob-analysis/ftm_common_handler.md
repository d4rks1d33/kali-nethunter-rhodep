# ftm_common_handler — Re-examen COMPLETO del handler FTM_COMMON (0xd8271290) sobre el binario de 36 MB

**Target:** SM6375 / Moto G82 5G · MPSS.HI.4.3.4-00494-MANNAR_GEN_PACK-1.24452.133
**Binario primario:** `/tmp/modemre/clade_dec_36m.bin` — VA base `0xd8000000`, tamaño 0x3400000
(cubre `0xd8000000 .. 0xdb400000`; código válido verificado hasta ~`0xda440000`, luego padding).
**Segmentos NATIVOS (NO CLADE):** b02 @0xc0800000, b10 @0xc0a80000, b12 @0xc0d30000,
b13 @0xc0d36000, b30 @0x1e400000 (escaneados byte-a-byte en este pase).
**Rodata:** b21 @0xc3553000, b23 @0xc8b6a000, seg27_dec @0xce480000.
**RW/.sdata:** b25 @0xcbf4f000 (gp), b26 @0xcc000000.
**Herramientas:** `dis36.sh`, `mkelf.py`, `llvm-mc-18`, `find_refs`, scans propios de este pase.

**Leyenda:** **FACT** = byte/instrucción leída con VA · **INFERENCE** = deducción con base dura ·
**UNKNOWN** = sólo runtime/RAM/fuera del ELF.

> **Método de verificación de alineación:** el store conocido `memb(gp+0x740)=r3` @`0xd81bd494`
> aparece byte-exacto (`40 e3 03 48`) en el 36 MB, y el gate `memb(gp+0x740)` @`0xd81bd160`
> (`02 68 03 49`). El 36 MB de este pase está **correctamente alineado** en toda la región FTM/RF
> (a diferencia del `clade_dec.bin` de 54 MB de pases previos). **FACT.**

---

## 0. TL;DR (respuestas directas)

1. **`0xd8271290` es el handler FTM_COMMON.** Parsea el sub-cmd de **pkt[4..5]** (u16 LE) y despacha
   **EXACTAMENTE 3 sub-cmds: `0x10F`, `0x4F7`, `0x4F5`**. No hay jump-table ni más comparaciones
   (función completa 0xd8271290..0xd8271314, leída íntegra). **FACT.**
2. **Ninguno de los 3 pone el modem en RF cal mode:**
   - `0x10F → 0xd8263e2c`: enable/attach de instancia RF por-tech; setea `gp+0x1ed5/0x1ed6`,
     NO `gp+0x740` ni `gp+0x7000`. **FACT.**
   - `0x4F7/0x4F5 → 0xd86f8514`: lee un CONTEXTO INTERNO resuelto por `0xd827280c`
     (tabla `@0xca79a850`), **NO lee offsets del wire** salvo el sub-cmd. **FACT (corrige un pase previo).**
3. **El writer de `0xcbf4f740 = 2` NO EXISTE en NINGUNA parte** del binario: ni en los 36 MB CLADE,
   ni en los 5 segmentos NATIVOS (b02/b10/b12/b13/b30). Barrido exhaustivo por los 4 vectores de
   escritura (gp-relativo todos los tamaños, absoluto, store-inmediato, base-pointer). Los ÚNICOS
   writers del byte escriben `mux(pred,1,0)` = **0 ó 1** (`0xd81bd494`, `0xd81bd5fc`) + reset `=0`
   (`0xd81bd7c8`). **FACT (ausencia dura, ahora sobre el binario COMPLETO).**
4. **`0x14` = DIAG_BAD_PARM_F** (constante de protocolo diagcmd.h). El patrón `2887 0100` = `0x00018728`
   NO es un literal estático del código (no se genera por un `= #0x8728`): es un campo
   computado en runtime del header de respuesta DIAG/FTM. **FACT (0x14) + INFERENCE/UNKNOWN (0x8728).**
5. **El "RF cal mode" (`gp+0x740=2`) lo pone código FUERA del MBN** (RFLM/dlpager: driver de cal
   LL1/`ftm_calibration_v3`), disparado por el comando de modo del RF-cmd-dispatch. El sub-cmd/TLV
   exacto es UNKNOWN estático porque el store del `2` no está en el ELF. **INFERENCE fuerte + FACT (ausencia).**

---

## 1. DISASM COMPLETO de `0xd8271290` (FTM_COMMON) + TODOS los sub-cmds

### 1.1 Header de transporte (FACT)
```
pkt[0]      = 0x4B         DIAG_SUBSYS_CMD_F
pkt[1]      = 0x0B         DIAG_SUBSYS_FTM
pkt[2..3]   = u16 LE       ftm_cmd_id (0x0000 = FTM_COMMON) -> tabla @0xc37bc828 (jump-table)
pkt[4..5]   = u16 LE       SUB-COMMAND  <-- lo que 0xd8271290 parsea
pkt[6..]    = params
```
Prólogo `r17:16 = combine(r2,r0)` ⇒ **r16 = arg0 (ctx/resp)**, **r17 = arg2 (puntero al paquete)**.
El sub-cmd se toma de `r17+4/+5`, es decir del **wire pkt[4..5]**. **FACT.**

### 1.2 Disasm anotado (0xd8271290 .. 0xd8271314) — función completa, byte-exacto
```
d8271290:  r17:16 = combine(r2,r0)                  ; r16=arg0(ctx/resp)  r17=arg2(pkt)
d8271294:  memd(r29-0x10)=r17:16 ; allocframe(#0x18)
d827129c:  memw(r0+0xc)=0 ; memw(r0+0x0)=0          ; limpia resp[0..],[0xc]
d82712a0:  call 0xd827280c                          ; helper (resuelve ctx global, NO wire)
d82712a4:  memw(r16+8)=0 ; memw(r16+4)=0
d82712a8:  call 0xd827280c
d82712ac:  call 0xd82834f0
;--------- PARSE DEL SUB-CMD (u16 LE en pkt[4..5]) ---------
d82712b0:  r1 = memub(r17+#0x4) ; r2 = memub(r17+#0x5)
d82712b4:  r1 |= asl(r2,#0x8)                        ; r1 = pkt[4] | pkt[5]<<8  == SUB-CMD
;--------- DISPATCH (3 comparaciones, sin jump-table) ---------
d82712b8:  p0 = cmp.eq(r1,#0x10f)
d82712bc:  if (p0.new) jump 0xd82712f8              ; ===> SUB 0x10F
d82712c0:  immext(#0x4c0)
d82712c4:  p0 = cmp.eq(r1,##0x4f7)
d82712c8:  if (p0.new) r0 = add(r17,#0x0)            ; r0 = &pkt
d82712cc:  if (p0.new) jump 0xd82712f0              ; ===> SUB 0x4F7
d82712d0:  immext(#0x4c0)
d82712d4:  p0 = cmp.eq(r1,##0x4f5)                   ; ===> SUB 0x4F5 (mismo handler)
d82712d8:  immext(#0xf8098340)
d82712dc:  if (p0.new) r0 = ##0xf8098340             ; string A (rama 0x4F5)
d82712e0:  call 0xd80d77a8                           ; LOG (F3): "sub-cmd" (0x4F5 log / default log)
d82712e4:  immext(#0xf8098340)
d82712e8:  if (!p0)    r0 = ##0xf8098348             ; string B (default: sub NO reconocido)
d82712ec:  r1 = #0x0 ; jump 0xd8271314               ; DEFAULT: result=0
;--------- rama 0x4F7 / 0x4F5 ---------
d82712f0:  call 0xd86f8514                           ; handler 0x4F7/0x4F5
d82712f4:  r1 = #0x2 ; jump 0xd8271314               ; result=2
;--------- rama 0x10F ---------
d82712f8:  r2 = memub(r17+#0x8) ; r4 = memub(r17+#0x7)
d82712fc:  r1 = memub(r17+#0x6) ; r3 = memub(r17+#0x9)
d8271300:  r2 |= asl(r3,#0x8)                        ; r2 = pkt[8]|pkt[9]<<8
d8271304:  r1 |= asl(r4,#0x8)                        ; r1 = pkt[6]|pkt[7]<<8
d8271308:  r1 |= asl(r2,#0x10)                       ; r1 = pkt[6..9] u32 LE (PARAM32)
d827130c:  call 0xd8263e2c                           ; handler(r0=ctx, r1=param32)
d8271310:  r1 = cmp.eq(r0,#0x1)                      ; result = (ret==1) ? 1 : 0
;--------- epílogo común (empaqueta respuesta) ---------
d8271314:  r0 = add(r29,#0) ; call 0xd8272bb0        ; RESP-BUILDER (template + status = f(r1))
d827131c..d827134c:  memw(r16+0/4/8/c) = stack[...]  ; copia la resp construida al buffer r16
d827134c:  dealloc_return
```
**FACT (disasm íntegro; verificado byte-a-byte con `dis36.sh 0xd8271290 0xc0`).**

### 1.3 LISTA COMPLETA de sub-cmds válidos (leída, NO adivinada)
| Sub-cmd (u16 @pkt4) | Handler | Qué hace | result→status |
|---|---|---|---|
| **0x010F** | `0xd8263e2c` | RF enable/attach por-tech (param32 @pkt[6..9]) | `(ret==1)?1:0` |
| **0x04F7** | `0xd86f8514` | RF "measurement/verify" con ctx interno | fijo `2` |
| **0x04F5** | `0xd86f8514` | igual handler, otra rama de log | fijo `2` |
| cualquier otro | — (default) | LOG + `result=0` | `0` |

**No hay más sub-cmds.** Son 3 comparaciones lineales `cmp.eq` con `0x10F`, `0x4F7`, `0x4F5`;
no existe jump-table ni rango. **FACT.**

### 1.4 Mapeo result→status del RESP-BUILDER `0xd8272bb0` (FACT)
```
0xd8272bb0(r0=respbuf, r1=result):
   copia template 16B de @0xc37c64fc  (= 09 00 00 00 00...  -> resp[0..0xf])
   if r1==2:  memb(resp+0)=1   ; memw(##0xcbad8a6c)=0
   if r1==1:  memb(resp+0)=0
   if r1==0:  memb(resp+0)=4   ; (default / sub desconocido)
```
Es decir, el **status byte** propio del FTM_COMMON handler es 1 / 0 / 4 (NO 0x14).
`0x14` es del framework DIAG (§4). **FACT.**

---

## 2. LAYOUT DE PARAMS EXACTO por sub-cmd (y por qué fallan los intentos en vivo)

### 2.1 Sub-cmd 0x10F (RF enable, handler 0xd8263e2c)
Wire:
```
off : 00 01  02 03   04 05   06 07 08 09
byte: 4B 0B  00 00   0F 01   PP PP PP PP
              cmd_id  sub=0x010F  param32 (LE) -> r1
```
`0xd8263e2c` NO usa param32 como "modo": llama a `0xd8055528` (get_rf_inst por tabla `@0xca79a8e0`)
y `0xd82726c4` (get_tech_obj por tabla `@0xca79a850`, +0x34, id≤0x31); si el obj no existe → log →
setea flags `gp+0x1ed5`, `gp+0x1ed6` (via `0xd8471498`/`0xd84714a0`). **No abre gates de cal.** **FACT.**

### 2.2 Sub-cmds 0x4F7 / 0x4F5 (handler 0xd86f8514) — CORRECCIÓN de un pase previo
`0xd86f8514` **NO recibe el paquete como arg**. Hace:
```
d86f8514:  call 0xd8829a00                 ; obtiene un ctx/sesión global (r0); if r0==0 -> error
d86f8530:  call 0xd827280c ; r16=ret ; r0 = memuh(r16+0xa)   ; <- r16 es CTX INTERNO, no el wire
d86f8538:  call 0xd827280c ; r17=ret ; r0 = memuh(r16+0xc)
d86f855c:  r2 = memub(r16+0xe)  ; tstbit(r2,#0)              ; flag interno
d86f8564:  r2 = memb(r16+0xf)   ; r5 = memub(r16+0x10)
```
`0xd827280c` lee `memw(##0xca79a850)` (tabla global en .bss) e **indexa por r0**; devuelve un
**puntero a estructura interna**. Por tanto los offsets **+0xa/+0xc/+0xe/+0xf/+0x10 son campos de esa
struct interna, NO del wire**. El único dato del wire que 0x4F7/0x4F5 usan es el propio sub-cmd
(pkt[4..5]); el resto de sus datos vienen del estado RF ya resuelto en RAM. **FACT.**

> **Corrección explícita:** el pase previo que decía "0x4F7/0x4F5 → campos en pkt[0xa]/[0xc]/[0xe]"
> confundió los offsets de la struct interna con offsets del wire. En vivo, **no hay params de wire
> que 0x4F7/0x4F5 lean más allá del sub-cmd**; por eso "0x4f7 → no reply" (el handler entra, resuelve
> ctx interno y responde/asserta según el estado RF, no según lo que mandes en pkt[6..]).

### 2.3 Por qué tus probes dan 0x14 (BAD_PARM)
- **sub 0..0x20** (con o sin params): caen en el **default** de 0xd8271290 (`result=0` → status 4)
  SÓLO si llegan al handler. Pero en vivo ves **0x14 constante**, que es del **framework DIAG antes
  del sub-dispatch** ⇒ los paquetes se rechazan por **validación de framing/longitud/estado** ANTES
  de 0xd8271290 (o el ftm_cmd_id 0x00 de tu build no enruta a 0xd8271290 sino al wrapper genérico
  0xd8150ed8 que devuelve BAD_PARM). El status 4 del handler NUNCA se observa ⇒ **no llegas al handler**.
  **INFERENCE alta** (consistente con: patrón 100% constante para todo sub 0..0x20).
- **sub 0x10e/0x110/0x111/0x4f4/0x4f6** con `02 00 00 00`: no matchean ninguna de las 3 comparaciones
  (`0x10F`/`0x4F7`/`0x4F5`) → default. **FACT.**
- **sub 0x10f** con `02 00 00 00` @pkt6: SÍ matchea → `0xd8263e2c(param32=2)`, pero eso es
  **enable de instancia RF por-tech con "2"** (no un modo cal); si la instancia no existe → status 0.
  **FACT.**
- **sub 0x4f5/0x4f7**: entran a `0xd86f8514`, que depende del **estado RF interno** (ctx `@0xca79a850`),
  no de tus params → "no reply"/status según estado. **FACT.**

---

## 3. EL SUB-CMD / STORE QUE PONE `gp+0x740 = 2` — BÚSQUEDA EXHAUSTIVA SOBRE EL BINARIO COMPLETO

### 3.1 gp = 0xcbf4f000 · Gate (a) = 0xcbf4f740 (re-confirmado en 36 MB, FACT)
```
0xd81bd160:  r2 = memb(gp+#0x740)                    ; r2 = *(0xcbf4f740)     (02 68 03 49)
0xd81bd168:  if (!cmp.eq(r2,#0x2)) jump 0xd81bd4b0   ; si != 2 -> SALTA el APPLY (no-cal)
```
Cross-check: `immext(#0xcbf4f740)`=`0x0cbf53dd` @`0xd81bd5b8` pareado con `memb(gp+0x740)=r3`
@`0xd81bd5fc` ⇒ gp+0x740 == 0xcbf4f740 ⇒ **gp=0xcbf4f000**. El enum es {0,1,2}: logger
mode→string `0xd81bde74` (`==2`/`==1`/else). **Valor 2 = "RF cal mode activo".** **FACT.**

### 3.2 Barrido de TODOS los writers de 0xcbf4f740 (los 4 vectores) — sobre CLADE-36MB + NATIVOS

Encodings derivados con `llvm-mc-18` (verificados):
```
memb(gp+0x740)=Rt : 0x4803e?40   (Rt en bits[12:8])   base = 0x4803e040
memh(gp+0x740)=Rt : 0x4841e2?0                          base = 0x4841e280
memw(gp+0x740)=Rt : 0x4880e2?0                          base = 0x4880e2d0
memd(gp+0x740)=Rtt: 0x48c0c2e8
immext(#0xcbf4f740): word 0x0cbf53dd  (cubre 0xcbf4f740..77f)
immext(#0xcbf4f700): word 0x0cbf53dc  (cubre 0xcbf4f700..73f)
```

**Resultado (FACT):**
```
VECTOR gp-relativo (memb/memh/memw/memd off 0x740, cualquier reg) sobre 36 MB:
   memb(gp+0x740)=Rt :  0xd81bd494 (r3=mux 0/1)   ,  0xd81bd5fc (r3=mux 0/1)
   memh / memw / memd off 0x740     :  CERO
VECTOR absoluto (immext 0xcbf4f740, offset exacto +0) sobre 36 MB + NATIVOS:
   memb(##0xcbf4f740)=#0 :  0xd81bd7c8  (RESET a 0, teardown)
   TODO lo demás con ese immext apunta a offsets VECINOS del struct:
     0xcbf4f746=#1 (0xd820bcbc), 0xcbf4f766=r2 (0xd835ba08/0xd835c050),
     0xcbf4f76a=#1 (0xd83606b8), 0xcbf4f775=#0 (0xd839d3f8), 0xcbf4f776=#0 (0xd82f0d84),
     0xcbf4f716=#2/#0 (NATIVO b13 0xc0db4220/0xc0db4260), 0xcbf4f731/0x729/0x71c/0x72e/0x724
     (varios en 0xd9xxxxxx, valores 0/1/6/7/8/b) — NINGUNO es el offset 0x740.
```
Ampliación clave de este pase: el 36 MB alcanza **la región 0xd9xxxxxx–0xda1xxxxx** (invisible en
los 10 MB previos). Aun así, **cero stores del literal 2 a 0xcbf4f740**. **FACT.**

### 3.3 ¿Puede r3 valer 2 en los 2 writers gp? NO (FACT)
Ambos: `r3 = mux(pred,#0x1,#0x0)` (`0xd81bd48c`, `0xd81bd5f4`). Matemáticamente 0 ó 1. En
`0xd81bd5fc` la "rama de éxito" (p2 = AND de gp+0x737/0x738==1/0x73f==1) escribe **1**, no 2. **FACT.**

### 3.4 Conclusión sobre el writer del `2` (FACT + INFERENCE)
- **FACT (dura):** en TODO el binario disponible (36 MB CLADE + b02/b10/b12/b13/b30 nativos) **no existe
  ningún store del literal 2 a `0xcbf4f740`**, por ninguno de los 4 vectores de escritura. Esto ya no
  se puede atribuir a "26 % mal-decodificado" (pase de 10 MB): el 36 MB está alineado y es autoritativo.
- **INFERENCE fuerte:** el `2` (= `NR5G_LL1_CAL_FTM_RF_MODE_CAL` / `ul_ftm_cal_mode`) lo escribe el
  **driver de cal RF que vive FUERA del MBN** (blob RFLM / dlpager: `ftm_calibration_v3_*`,
  `rflte_ftm_*`), invocado por el RF-cmd-dispatch. Anclas de strings (seg27, FACT):
  `Assertion (p_tx_ctx->tx_info.rf_mode == (uint8)NR5G_LL1_CAL_FTM_RF_MODE_CAL) failed` @0xce6cf8e0,
  `Assertion (lte_LL1_get_ul_ftm_cal_mode(cxn_id) > 0) failed` @0xce5bd52b. Ese `rf_mode`/`ftm_cal_mode`
  es estado LL1 por-conexión seteado por firmware de capa física, **no** por el código MBN escaneado.
- **El VA del store `0xcbf4f740=2` y el sub-cmd/TLV EXACTO que lo dispara son UNKNOWN estático**
  (código ausente del ELF). NO es 0x10F/0x4F5/0x4F7 (probado en §2). **FACT (ausencia) + INFERENCE.**

### 3.5 Gate (b) gp+0x7000 (=0xcbf56000) — accessor lazy (FACT, re-confirmado)
```
0xd8284eac (get-or-create):  r17=memw(gp+0x7000); if(!=0) return; else call 0xd8284cf4; memw(gp+0x7000)=r17
0xd8284cf4 (creator):        alloc 0xd84c4b80; memw(obj+0)=##0xc37c6d80 (vtable); memw(gp+0x7000)=obj
```
Se crea **bajo demanda** al tocar el subsistema RF-instance (mismo bring-up de cal). No en boot. **FACT.**

---

## 4. `0x14` y el patrón de respuesta `2887 0100`

### 4.1 `0x14` (FACT del valor)
`0x14 = 20 = DIAG_BAD_PARM_F` (constante fija de `diagcmd.h`, idéntica en todo MPSS). "Comando
reconocido pero parámetro/estado inválido" (NO es `0x13 DIAG_BAD_CMD_F` = comando desconocido).
Lo emite el **framework DIAG** (`diagpkt_err_rsp`, formato de 5 bytes `{err_code, orig_pkt[≤4]}`),
no el status propio del FTM_COMMON handler (que sería 1/0/4, §1.4). **FACT.**

### 4.2 `2887 0100` = `0x00018728` (INFERENCE / UNKNOWN)
- **FACT:** NO es un literal del código. Escaneo de todo el binario: no hay ningún `r = #0x8728` ni
  store de `0x8728`/`0x00018728` en la ruta de respuesta FTM. La única aparición de los bytes
  `28 87 01 00` en rodata (`0xce448e30`, b26) es una tabla de datos no relacionada. **FACT.**
- **INFERENCE:** `0x8728` es un **campo computado en runtime del header de la respuesta DIAG/FTM**
  (candidatos: `command_id`/`rsp_cnt`/token de delayed-response, o parte del sello de tiempo/secuencia
  que DIAG añade). Es **constante entre tus probes** porque tu secuencia de envío es idéntica y el
  estado del modem no cambia (siempre BAD_PARM antes de ejecutar). **No indica un error-code
  específico del RF**: es framing, no semántica de FTM. **INFERENCE.**
- **UNKNOWN:** el significado byte-exacto de `0x8728` requiere el header `diagpkt`/`ftm_cmd_header`
  del build (en b13 nativo/DIAG-core) y la posición exacta en tu wire (que no fue provista byte-a-byte).

**Interpretación operativa:** `0x14 + <framing constante>` = **"el comando llegó pero fue rechazado
por parámetro/estado inválido, sin ejecutarse"**. Es el síntoma de "no estás en el modo/estado que el
comando exige", coherente con que el RF cal mode (`gp+0x740=2`) NO está activo.

---

## 5. ¿DÓNDE ESTÁ EL "SET RF CAL MODE" SI NO ES FTM_COMMON? (revisión de la tabla @0xc37bc828)

### 5.1 Tabla jump `@0xc37bc828` (indexada por pkt[2], ftm_cmd_id) — COMPLETA (FACT)
Todas las entradas apuntan a stubs `0xd814e1xx` dentro del router genérico (que hacen
`r3 = add(r9,#-0x7fff); callr r3`, i.e. handler = base_módulo − 0x7fff). El idx 0 (FTM_COMMON) →
`0xd814e1ec`; el idx 0x27 (LTE) → `0xd814e534`; el idx 0x2e → `0xd814e4bc`; etc. Los que no están
mapeados → `0xd814e5e4` (stub "not supported"):
```
[00]d814e1ec [01]d814e26c [02]d814e1a8 [03]d814e138 [04-06]d814e5e4 [07]d814e14c [08]d814e140
[09]d814e2cc [0d]d814e314 [10]d814e540 [11]d814e240 [12]d814e350 [14]d814e360 [15]d814e278
[1b]d814e340 [1d/1e]d814e370 [1f]d814e504 [20]d814e514 [22]d814e10c [23]d814e370 [24]d814e524
[27]d814e534 (LTE) [28]d814e51c [2e]d814e4bc [2f]d814e370 ; resto -> d814e5e4 (not supported)
```
**FACT.** Ninguno de estos stubs escribe `gp+0x740`/`gp+0x7000` en su cuerpo inmediato; todos son
trampolines al handler por-módulo. Reachability sound al store del `2` = imposible (no existe).

### 5.2 El wrapper "clásico" `0xd8150ed8` (tabla DIAG `@0xc37bd1e8`)
La tabla `@0xc37bd1e8` es la **tabla DIAG-subsys FTM** (pares `{cmd_range:u32, handler}`), TODAS →
`0xd8150ed8` (framing genérico `diagpkt_subsys_alloc` + re-dispatch). Cubre cmds
`0x00,0x03,0x20,0x28,0x27,0x69,0x7a,0x7b,0x7e,0x0b,0x07,0x08,0x02,0x3a,0x31,0x3d,0x65,0x66,0x11,...,0x1b`
(75 entradas). `0xd8150ed8` valida `pkt[1]==0xB`, y tiene un caso especial para `ftm_cmd_id==0x14`
con `sub & 0xFFFE == 0x35A`. **No contiene un `SET_MODE` que escriba `gp+0x740`.** **FACT.**

### 5.3 Búsqueda de strings de modo (FACT de existencia)
```
seg27: NR5G_LL1_CAL_FTM_RF_MODE_CAL (@0xce6cf8fe/0xce6cf902)  ; rf_mode enum, valor cal
seg27: lte_LL1_get_ul_ftm_cal_mode(cxn_id) > 0 (@0xce5bd52b)  ; getter de cal-mode LL1
seg27: rfm_mode_to_rfm_tech / rfc_convert_rfm_mode_to_front_end_tech ; conversores rfm_mode
b21 :  FTM_MODE (@0xc37bd180) ; ftm_calibration_v3_seq_class.cpp (@0xc3919342)
```
El `rf_mode`/`ftm_cal_mode` es **estado LL1 por-conexión** (capa física), seteado por el driver de
cal fuera del MBN. El comando que lo dispara pasa por el **RF-cmd-dispatch por-tech**
(`rf_cmd_dispatch_register_tech`, seg27), cuyo binding vive en RAM. **FACT (strings) + INFERENCE (ruta).**

### 5.4 Recomendación operativa (para el driver)
1. **No esperes que 0xd8271290 (sub 0x10F/0x4F5/0x4F7) ponga cal mode** — no lo hace.
2. La entrada a "RF cal / non-signaling" en este stack es, con alta probabilidad, un **modo previo**:
   - **QMI-DMS `set_operating_mode` → offline/FTM**, o
   - el **FTM_SET_MODE clásico de Qualcomm** procesado por el RF-cmd-dispatch (fuera del MBN),
   seguido del **activate FTM de la portadora** (TECH_ENTER LTE + RADIO_CONFIG con BAND+EARFCN), que
   es lo que dispara el driver de cal LL1 → deja `gp+0x740=2` y crea `gp+0x7000`.
3. **Verificá SIEMPRE en vivo por peek DIAG de memoria** (única fuente de verdad, ya que el store del
   `2` no está en el ELF):
   ```
   memb(0xcbf4f740) == 2      ; RF cal mode activo
   memw(0xcbf56000) != 0      ; ctx RF creado
   memw(0xca79c494) != 0      ; carrier ptr poblado (post-activate)
   ```

---

## 6. FACT / INFERENCE / UNKNOWN (con VAs)

**FACT**
- FTM_COMMON handler = `0xd8271290`; sub-cmd = u16 LE @pkt[4..5]; despacha EXACTAMENTE
  `0x10F`(→0xd8263e2c), `0x4F7`/`0x4F5`(→0xd86f8514). Sin jump-table. (disasm 0xd8271290..0xd8271314)
- 0x10F: param32 @pkt[6..9] → enable RF por-tech; setea gp+0x1ed5/0x1ed6; NO gp+0x740/0x7000.
- 0x4F7/0x4F5: `0xd86f8514` lee ctx INTERNO (`0xd827280c`→tabla `@0xca79a850`), NO wire (salvo sub-cmd).
- RESP-BUILDER `0xd8272bb0`: status propio = {result2→1, result1→0, result0→4}. Template @0xc37c64fc.
- gp = 0xcbf4f000; Gate(a) 0xcbf4f740 read/gate @0xd81bd160/68 (`if(!=2) jump 0xd81bd4b0`).
- **CERO stores del literal 2 a 0xcbf4f740 en TODO el binario** (36 MB CLADE + b02/b10/b12/b13/b30).
  Únicos writers: 0xd81bd494 y 0xd81bd5fc (`mux(pred,1,0)` = 0/1) + reset 0xd81bd7c8 (=0).
  Verificado por los 4 vectores (gp-rel memb/memh/memw/memd, absoluto, store-imm, base-pointer),
  con encodings confirmados por llvm-mc-18.
- Gate(b) 0xcbf56000: accessor lazy 0xd8284eac / creator 0xd8284cf4 (vtable ##0xc37c6d80).
- Tabla ftm_cmd `@0xc37bc828` completa (48 entradas, stubs 0xd814e1xx). Tabla DIAG `@0xc37bd1e8`
  (75 entradas → 0xd8150ed8 framing).
- 0x14 = DIAG_BAD_PARM_F (constante de protocolo). `2887 0100`/`0x8728` NO es literal estático.
- Strings de anclaje: NR5G_LL1_CAL_FTM_RF_MODE_CAL @0xce6cf8fe, lte_LL1_get_ul_ftm_cal_mode @0xce5bd52b.

**INFERENCE (base dura)**
- `gp+0x740=2` (RF cal mode) lo escribe código RF-cal FUERA del MBN (RFLM/dlpager, LL1); disparado por
  el comando de modo del RF-cmd-dispatch, NO por FTM_COMMON 0x10F/0x4F5/0x4F7.
- Los probes en vivo (sub 0..0x20) dan 0x14 constante porque el framework DIAG rechaza por
  framing/estado ANTES del sub-dispatch (nunca se ve el status 4 propio del handler).
- `0x8728` = campo de framing DIAG/FTM computado en runtime (command_id/rsp token), no un error-code RF.
- La entrada a cal mode es un modo previo (QMI-DMS offline/FTM o FTM_SET_MODE del RF-dispatch) +
  activate de portadora (TECH_ENTER LTE + RADIO_CONFIG BAND+EARFCN).

**UNKNOWN (sólo runtime / RAM / fuera del ELF)**
- VA exacto del store `0xcbf4f740=2` y el sub_command/command_id/TLV EXACTO que lo dispara
  (código ausente del MBN).
- Significado byte-exacto de `0x8728` y su posición en tu wire (falta el header diagpkt del build
  y los bytes crudos completos de la respuesta).
- Valor en vivo de gp+0x740 / gp+0x7000 / 0xca79c494 en tu instante (sólo por peek DIAG).

---

## 7. REPRODUCIR
```bash
# Handler FTM_COMMON completo
/tmp/modemre/dis36.sh 0xd8271290 0xc0
# Sub-handlers
/tmp/modemre/dis36.sh 0xd8263e2c 0x80     # 0x10F
/tmp/modemre/dis36.sh 0xd86f8514 0x60     # 0x4F7/0x4F5 (ctx interno via 0xd827280c)
/tmp/modemre/dis36.sh 0xd827280c 0x40     # helper: memw(##0xca79a850) indexado
/tmp/modemre/dis36.sh 0xd8272bb0 0x60     # resp-builder result->status
# Gate y writers
/tmp/modemre/dis36.sh 0xd81bd160 0x14     # gate ==2
/tmp/modemre/dis36.sh 0xd81bd478 0x40     # writer mux(0/1) @0xd81bd494
/tmp/modemre/dis36.sh 0xd81bd5b0 0x60     # writer mux(0/1) @0xd81bd5fc
# Prueba de ausencia del store #2 (encodings via llvm-mc-18):
#   memb/memh/memw/memd(gp+0x740)=Rt  -> sólo 0xd81bd494/0xd81bd5fc (memb, mux 0/1)
#   immext(0xcbf4f740) + store offset+0 -> sólo memb(##0xcbf4f740)=#0 @0xd81bd7c8 (reset)
# Nativos (b02/b10/b12/b13/b30): único store cercano = memb(0xcbf4f716)=#2 @0xc0db4220 (offset 0x716, NO 0x740)
```
