# ftm_set_mode_verified — FTM_COMMON handler, 0x10F, gate 0xcbf4f740=2 writer (verificado en el binario COMPLETO de 36 MB)

**Target:** SM6375 / Moto G82 5G · MPSS.HI.4.3.4-00494-MANNAR_GEN_PACK-1.24452.133
**Binario usado (NUEVO, COMPLETO):** `/tmp/modemre/clade_dec_36m.bin` — VA base `0xd8000000`,
código válido `0xd8000000 .. ~0xda800000` (validado por disasm; `0xdb000000+` = padding `0x70704000`).
**Ventana extra:** `clade_exc_high.bin` (VA base `0xd0000000 .. 0xd0703000`) = re-proyección paginada de
las MISMAS funciones `0xd8xxxxxx` (verificado: 0xd02c2970 == 0xd81bd7b4 idénticos).
**Herramientas nuevas de este pase:** `dis36.sh <va> <len>` (disasm directo del 36 MB),
`/tmp/full36.txt` (disasm autoritativo de 0xd8000000..0xda800000, 9.2 M líneas), `scan_gp_all.py`,
`scan_gate2.py`, `scan_abs_all.py`.

**Leyenda:** **FACT** = byte/instrucción leída con VA · **INFERENCE** = deducción con base dura · **UNKNOWN** = sólo runtime/RAM/fuera del ELF.

---

## 0. TL;DR (respuestas directas)

1. **FTM_COMMON handler `0xd8271290`** — parsea el sub-cmd de **pkt[4..5]** (u16 LE) y despacha
   `0x10F` / `0x4F7` / `0x4F5`. Layout wire y disasm completos en §1. **FACT.**
2. **0x10F handler `0xd8263e2c`** (RF enable/setup) — **NO** setea `0xcbf4f740=2` ni crea el ctx
   `0xcbf56000`. Es un *retriever/enable* de la instancia RF por-tech (tablas @0xca79a850 / @0xca79a8e0).
   Su call-graph no alcanza ningún store de los gates. Disasm + call-graph en §2. **FACT.**
3. **Writer de `0xcbf4f740=2`: NO EXISTE en TODO el binario de 36 MB.** Barrido exhaustivo autoritativo
   (disasm completo, no heurística): los ÚNICOS writers del byte son `0xd81bd494` y `0xd81bd5fc`
   (`memb(gp+0x740)=mux(pred,#1,#0)` → **0 ó 1**) + reset `=0` en `0xd81bd7c8`. **Cero stores del
   literal 2.** El writer del `2` está en código RF-cal fuera del MBN (dlpager/RFLM). VAs y prueba en §3.
4. **Paquete FTM_SET_MODE cal — byte-exact:** ver §4. El **sub-cmd 0x10F NO es el set-cal-mode**
   (es enable RF por-tech). El comando que deja `gp+0x740=2` es el **FTM_SET_MODE canónico
   (FTM_COMMON, phone-mode de cal)** cuyo store vive fuera de la ventana. Doy AMBOS candidatos y por qué.
5. **Secuencia de bring-up completa** verificada por gates en §5.

---

## 1. FTM_COMMON handler `0xd8271290` — DISASM + PARSE DEL SUB-CMD

### 1.1 Header de transporte DIAG-FTM (FACT)
```
pkt[0] = 0x4B          DIAG_SUBSYS_CMD_F
pkt[1] = 0x0B          DIAG_SUBSYS_FTM
pkt[2..3] = u16 LE     ftm_cmd_id  (0x0000 = FTM_COMMON) -> tabla @0xc37bc828 -> handler runtime
pkt[4..5] = u16 LE     sub-command (0x10F / 0x4F7 / 0x4F5)
pkt[6..]  = params
```
El handler recibe `r16 = ctx/subsys`, `r17 = puntero al paquete FTM` (prólogo
`r17:16 = combine(r2,r0)` @0xd8271290). **FACT.**

### 1.2 Disasm anotado (0xd8271290 .. 0xd8271314)
```
d8271290:  r17:16 = combine(r2,r0)            ; r16=ctx(arg0), r17=pkt(arg2)
d8271294:  memd(r29-0x10)=r17:16 ; allocframe(#0x18)
d827129c:  memw(r0+0xc)=0 ; memw(r0+0x0)=0    ; limpia resp
d82712a0:  call 0xd827280c                    ; (helpers de framing resp)
d82712a8:  call 0xd827280c
d82712ac:  call 0xd82834f0
;---- PARSE DEL SUB-CMD (u16 LE en pkt[4..5]) ----
d82712b0:  r1 = memub(r17+#0x4) ; r2 = memub(r17+#0x5)
d82712b4:  r1 |= asl(r2,#0x8)               ; r1 = pkt[4] | (pkt[5]<<8)   == SUB-CMD
;---- DISPATCH ----
d82712b8:  p0 = cmp.eq(r1,#0x10f)
d82712bc:  if (p0.new) jump 0xd82712f8       ; ---> 0x10F
d82712c4:  p0 = cmp.eq(r1,##0x4f7)
d82712c8:  if (p0.new) r0 = add(r17,#0x0)     ; r0 = &pkt
d82712cc:  if (p0.new) jump 0xd82712f0        ; ---> 0x4F7  (call 0xd86f8514)
d82712d4:  p0 = cmp.eq(r1,##0x4f5)
d82712dc:  if (p0.new) r0 = ##0xf8098340...   ; ---> 0x4F5  (mismo handler, otra rama)
d82712e0:  call 0xd80d77a8                     ; log "unknown sub-cmd" si no matchea
d82712ec:  r1 = #0x0 ; jump 0xd8271314         ; default: status=0
;---- rama 0x4F7/0x4F5 ----
d82712f0:  call 0xd86f8514                     ; handler 0x4F7/0x4F5 (parsea pkt[0xa],[0xc],[0xe]...)
d82712f4:  r1 = #0x2 ; jump 0xd8271314         ; status=2
;---- rama 0x10F ----
d82712f8:  r2 = memub(r17+#0x8) ; r4 = memub(r17+#0x7)
d82712fc:  r1 = memub(r17+#0x6) ; r3 = memub(r17+#0x9)
d8271300:  r2 |= asl(r3,#0x8)               ; r2 = pkt[8] | (pkt[9]<<8)
d8271304:  r1 |= asl(r4,#0x8)               ; r1 = pkt[6] | (pkt[7]<<8)
d8271308:  r1 |= asl(r2,#0x10)             ; r1 = pkt[6..9] u32 LE  (PARAM de 32 bits)
d827130c:  call 0xd8263e2c                   ; 0x10F handler(r0=ctx, r1=param32)
d8271310:  r1 = cmp.eq(r0,#0x1)             ; status = (ret==1)
;---- epílogo común ----
d8271314:  call 0xd8272bb0 ; r0=add(r29,#0)  ; empaqueta respuesta
```
**FACT** (disasm byte-exact del 36 MB, verificado también en /tmp/full36.txt línea 640002+).

### 1.3 Layout wire por sub-cmd

**Sub-cmd 0x10F (RF enable/setup):** param de 32 bits en pkt[6..9]
```
offset:  00  01   02 03    04 05    06 07 08 09
bytes:   4B  0B   00 00    0F 01    PP PP PP PP
                  ^cmd_id  ^sub=0x010F  ^param32 (LE)  -> pasado como r1 a 0xd8263e2c
```
El `param32` = pkt[6] | pkt[7]<<8 | pkt[8]<<16 | pkt[9]<<24. En 0xd8263e2c NO se usa como "modo"
directo: se pasa a helpers de framing (`0xd8055528`/`0xd82726c4` lo indexan como tech/inst id ≤0x31).
**FACT.**

**Sub-cmd 0x4F7 / 0x4F5:** handler `0xd86f8514`. Lee campos del paquete a offsets **pkt[0xa]** (u16,
`memuh(r0+#0xa)`), **pkt[0xc]** (u16, `memuh(r16+#0xc)`) y **pkt[0xe]** (byte, `memub(r16+#0xe)`,
bit0 = flag). Layout:
```
offset:  00 01  02 03  04 05  06 07 08 09  0a 0b  0c 0d  0e ...
bytes:   4B 0B  00 00  F7 04  <resv 4>      AA AA  BB BB  CC
                       ^sub=0x04F7          ^u16   ^u16   ^flag(bit0)
```
(0x4F5 usa el mismo handler con otra rama de log). **FACT (disasm 0xd86f8514 §hasta 0xd86f8560).**

---

## 2. 0x10F handler `0xd8263e2c` — DISASM + CALL-GRAPH (¿setea los gates? NO)

### 2.1 Disasm (0xd8263e2c .. 0xd8263ea0, cuerpo real)
```
d8263e2c:  call 0xd8829688 ; allocframe(#0x10)
d8263e34:  call 0xd8055528                   ; r17:16 = combine(r0,r1)  -> GET RF-inst (tabla @0xca79a8e0[tech])
d8263e3c:  call 0xd82726c4 ; r18=r0 ; r0=r17  ; GET per-tech obj (tabla @0xca79a850[tech], +0x34)
d8263e44:  p0 = cmp.eq(r18,#0)
d8263e48:  r0 = ##0xf8039a38 ; r2 = r0        ; (string/desc de subsistema)
d8263e50:  if (p0) jump 0xd8263e6c
d8263e54:  p0 = cmp.eq(r2,#0) ; if(p0) jump 0xd8263e6c
d8263e60:  r18 = #1 ; r2 = memb(r18+#0x1fe)   ; lee estado (+0x1fe) del obj
d8263e68:  p0 = cmp.eq(r2,#7) ; if(p0.new) jump 0xd8263e74
d8263e6c:  call 0xd80d77a8 ; r18=#0 ; r1=r17   ; (log)
d8263e74:  call 0xd8471498 ; r0=#1             ; SET memb(gp+0x1ed5)=1   (flag FTM, NO es 0x740)
d8263e7c:  call 0xd84714a0 ; r0=#1             ; SET memb(gp+0x1ed6)=1   (flag FTM, NO es 0x740)
d8263e84:  call 0xd81578f0
d8263e8c:  r0 = ##0xf8098300
d8263e90:  p0 = cmp.eq(r18,#0) ; if(p0.new) jump 0xd8263ea4
d8263e94:  call 0xd829df1c ; r1:0=combine(r0,r16)
d8263e9c:  r0 = and(r0,r18)                    ; ret = ok & inst
d8263ea0:  jump 0xd8829698 (epílogo)           ; return r0
```
**FACT.**

### 2.2 Call-graph inmediato del 0x10F
```
0xd8263e2c
 ├─ 0xd8055528  = get_rf_inst(tech):  r0=memw(##0xca79a8e0 + tech<<2)   [tabla de instancias]
 ├─ 0xd82726c4  = get_tech_obj(id):   r2=memw(##0xca79a850); r0=memw(addasl(r2,id,2)+0x34)
 ├─ 0xd8471498  = set flag: memb(gp+#0x1ed5)=r0   (0xcbf50ed5)   <-- NO es 0x740
 ├─ 0xd84714a0  = set flag: memb(gp+#0x1ed6)=r0   (0xcbf50ed6)   <-- NO es 0x740
 ├─ 0xd81578f0  = (init/notify)
 └─ 0xd829df1c  = (activate helper; llama 0xd81bf0e8)
```
**Ninguna** de estas escribe `0xcbf4f740` ni `0xcbf56000`. Los flags que sí toca son
`gp+0x1ed5/0x1ed6` (cluster distinto). **FACT.**

**Conclusión (FACT):** el 0x10F **NO es** el "set RF cal mode". Es un **enable/attach de la instancia
RF por-tech** (equivalente a `ftm_rf_*_enable`). No abre los gates de cal.

---

## 3. WRITER de `0xcbf4f740 = 2` EN EL BINARIO COMPLETO (36 MB) — BÚSQUEDA EXHAUSTIVA

### 3.1 gp = 0xcbf4f000 (FACT, re-confirmado en 36 MB)
Cross-check byte-exact en el 36 MB: `0xd81bd160 r2=memb(gp+#0x740)` y `0xd81bd5b8 immext(#0xcbf4f740)`
apuntan al MISMO global. `memb(gp+#0x740)=r3` = word `0x4803e340` (S2_storerbgp, reg en bits[12:8]);
`r2=memb(gp+#0x740)` = `0x4903e802`. **FACT.**

### 3.2 EL GATE (a) — reader, byte-exact (FACT, 36 MB)
```
0xd81bd160:  r2 = memb(gp+#0x740)                     ; r2 = *(0xcbf4f740)
0xd81bd168:  if (!cmp.eq(r2,#0x2)) jump 0xd81bd4b0    ; si != 2 -> SALTA el APPLY (no-cal)
```
Enum {0,1,2}; logger mode->string `0xd81bde74` (==2 / ==1 / else). **Valor 2 = "RF cal mode activo".**
**FACT.**

### 3.3 GATE (b) — reader, byte-exact (FACT, 36 MB)
```
0xd8286240:  r2 = memw(gp+#0x7000)                    ; r2 = *(0xcbf56000)  (ptr ctx RF C++)
0xd8286248:  p0=cmp.eq(r2,#0); if(!p0.new) jump 0xd8286260 (r16=1) ; else r16=0 + log ##0xf8048318
```
Lo CREA el accessor lazy get-or-create `0xd8284eac`:
```
0xd8284eb4:  r17 = memw(gp+#0x7000)
0xd8284eb8:  if (r17!=0) jump 0xd8284ef0               ; ya existe
0xd8284ed4:  call 0xd8284cf4                            ; CREATOR (alloc + vtable ##0xc37c6d80)
0xd8284ee4:  memw(gp+#0x7000) = r17                     ; <<< puebla 0xcbf56000
```
**FACT.**

### 3.4 BARRIDO AUTORITATIVO del writer de 0x740 (todo el disasm, no heurística)
Sobre `/tmp/full36.txt` (disasm completo 0xd8000000..0xda800000) + `hi36.txt` (0xda800000..0xdb000000)
+ `clade_exc_high` (0xd0000000..0xd0703000):

**TODAS las escrituras a `gp+0x740` / `0xcbf4f740` en 36 MB (FACT):**
```
0xd81bd494   memb(gp+#0x740) = r3   ; r3 = mux(p0,#0x1,#0x0)  -> 0 ó 1   (rama NO-activate)
0xd81bd5fc   memb(gp+#0x740) = r3   ; r3 = mux(p2,#0x1,#0x0)  -> 0 ó 1   (rama NO-activate)
0xd81bd7c8   memb(r2)=#0 ; r2=##0xcbf4f740                    -> RESET a 0 (teardown)
```
**TODAS las lecturas (FACT):** 0xd81bd160 (==2, EL GATE) · 0xd81bd644 · 0xd81bd708 (==1) ·
0xd81bde74 (logger) · 0xd81bdea0 · 0xd81bf300 (==1) · 0xd81bf34c · 0xd81bf39c · 0xd81bf40c.
**USOS-COMO-BASE** (cargan `0xcbf4f740+N` para tocar campos vecinos del struct 0x73x–0x77x,
NUNCA el byte 0x740): 0xd8209200/0c, 0xd820bca8, 0xd82c23c4, 0xd82dd7b4, 0xd82f0d78 (mux #3/#2 pero a
0xcbf4f776), 0xd833e548, 0xd83553a8, 0xd8356330, 0xd835ba04/0xd835c04c (memb=r2 a **0xcbf4f766**),
0xd83606a4, 0xd838e9a8, 0xd8395008, 0xd839d3f0.
En exc_high: 0xd063b748 escribe **#3 a 0xcbf4f71b** (flag vecino), 0xd02c297c retorna `&0xcbf4f740`.

**RESULTADO (FACT): en 36 MB NO existe NINGÚN store del literal 2 a `0xcbf4f740`.**
Métodos aplicados (los 3 vectores + verificación por disasm completo):
- **Vector gp-relativo** (`memb(gp+#0x740)=Rt`, base `0x4803e040`, todos los registros):
  `scan_gp_all.py` → SÓLO 0xd81bd494 y 0xd81bd5fc (ambos r3=mux 0/1).
- **Vector absoluto** (`immext(#0xcbf4f740)` = word `0x0cbf53dd`, 21 sitios + bases vecinas 3dc/3c0):
  `scan_gate2.py` + `scan_abs_all.py` → único store a offset 0 es `memb(##0xcbf4f740)=#0` @0xd81bd7c8.
- **Vector base-pointer** (`rX=##0xcbf4f740; memb(rX+off)=…`): sólo tocan offsets vecinos, nunca +0.
- **Verificación cruzada**: grep de `gp+#0x740)` y `0xcbf4f740` en el disasm autoritativo de 36 MB.

### 3.5 ¿Puede r3 valer 2? NO (FACT)
Ambos writers: `r3 = mux(pred,#0x1,#0x0)` (0xd81bd48c, 0xd81bd5f4). Es matemáticamente 0 ó 1.
No hay path que meta 2 en r3. **FACT.**

### 3.6 Conclusión dura sobre el writer del 2 (FACT + INFERENCE)
- **FACT:** el binario "completo" de 36 MB cubre `0xd8000000..~0xda800000` de código VÁLIDO (mucho más
  que los 10 MB del pase anterior), y AUN ASÍ no contiene el store `0xcbf4f740=2`. Se descarta que
  estuviera "en el 26 % mal-decodificado del pase de 10 MB": ahora el disasm es autoritativo y sigue
  sin aparecer.
- **INFERENCE (base dura):** el writer del `2` vive en el **código RF-cal fuera de este MBN**
  (pool dlpager / blob RFLM secundario: `rflte_ftm_*` / `ftm_calibration_v3_*` / setter de `rf_mode`).
  Lo confirma la semántica: el getter de instancia (`0xd8055528`/`0xd82726c4`) resuelve la instancia
  por tablas en `.bss` (0xca79a8e0/0xca79a850) cuyos MÉTODOS (que setean el modo) no están mapeados.
  El enum `NR5G_LL1_CAL_FTM_RF_MODE_CAL` y `lte_LL1_get_ul_ftm_cal_mode(cxn)>0` (seg27) anclan que el
  `2` = FTM_RF_MODE_CAL, seteado por el driver de cal. **INFERENCE fuerte.**

**El VA del store `0xcbf4f740=2` es UNKNOWN estático** (código ausente del ELF).

---

## 4. PAQUETE FTM_SET_MODE — CAL MODE (byte-exact) + los dos candidatos

### 4.1 Candidato A — sub-cmd 0x10F (con param) — **DESCARTADO como set-cal-mode** (FACT)
```
4B 0B  00 00  0F 01  PP PP PP PP
```
Disasm de su handler (§2) prueba que **NO** escribe `gp+0x740` ni `gp+0x7000`; solo hace attach/enable
de la instancia RF por-tech y setea flags gp+0x1ed5/0x1ed6. **No pone cal mode.** **FACT.**

### 4.2 Candidato B — FTM_SET_MODE canónico (FTM_COMMON) — **el correcto** (INFERENCE)
El "poner el modem en RF cal mode" (`gp+0x740=2`) lo hace el **FTM_SET_MODE clásico de Qualcomm**
(subsys FTM 0x0B, FTM_COMMON, sub-cmd SET_MODE que conmuta el phone-mode a `FTM_PHONE_MODE_*` de cal),
procesado por el RF cmd-dispatch → driver de cal (fuera del MBN, §3.6). Forma canónica del comando
(FTM_SET_MODE, mode=2):
```
DIAG:  4B 0B  00 00                 ; SUBSYS_CMD_F, SUBSYS_FTM, ftm_cmd_id=FTM_COMMON(0x0000)
sub:   <SET_MODE:u16 LE>  <mode:u16 LE=0x0002>
```
- El sub-cmd numérico EXACTO de SET_MODE en ESTE build es **UNKNOWN estático** (el binding vive en el
  dispatcher runtime `0xd8150ed8`, no mapeado; y el handler 0xd8271290 sólo reconoce 0x10F/0x4F7/0x4F5,
  que NO son SET_MODE). El `mode=2` = FTM_RF_MODE_CAL (anclado por strings seg27). **INFERENCE.**
- **Recomendación operativa:** en muchos stacks el "modo cal" se entra por **QMI-DMS
  set_operating_mode → FTM/offline** + el activate FTM de la portadora; ese activate es quien completa
  `gp+0x740=2` y `gp+0x7000!=0` vía el driver de cal. Verificar SIEMPRE en vivo (§5, PASO D).

### 4.3 Por qué el disasm NO da el byte-exact de SET_MODE
El handler de SET_MODE no es 0x10F. El único `FTM_COMMON` estático que despacha por sub-cmd
(0xd8271290) reconoce 0x10F/0x4F7/0x4F5. El SET_MODE clásico lo maneja el dispatcher runtime
`0xd8150ed8` (tabla @0xc37bd1e8, ptr 0xd8150ed8 para las 75 entradas) que **no está mapeado** en el
MBN. Por eso el opcode/sub-cmd exacto y el store del `2` son **UNKNOWN estático**. **FACT (ausencia).**

---

## 5. SECUENCIA DE BRING-UP COMPLETA (verificada por gates)

Estado FTM "pelado": `0xcbf4f740=0`, `0xcbf56000=NULL`, `0xca79c494=NULL`.
Cadena de APPLY (FACT, 36 MB): gate(a)==2 (0xd81bd168) Y gate(b)!=0 (0xd8286248, r0==1 @0xd81bd290)
→ APPLY 0xd8273b2c ← 0xd81bd2a8 → setter 0xd8279264 → `memw(0xca79c494)=0xca6e3b88`.
Sin gates, el getter 0xd827923c ve NULL → err_fatal → SSR.

```
PASO A — PONER EN RF CAL MODE   (setea 0xcbf4f740=2 y dispara la creación de 0xcbf56000)
   Comando: FTM_SET_MODE canónico (DIAG 4B 0B, FTM_COMMON 00 00, sub-cmd SET_MODE, mode=2/cal).
   [sub-cmd numérico exacto = UNKNOWN estático; ver §4.2/§4.3]
   Prereqs HW (FACT strings seg27): rfm_init llamado (@0xce6de1b0), MCPM ON (@0xce748848),
                                    NV/cal cargada.
   Efecto: driver de cal (fuera del MBN) escribe 0xcbf4f740=2 y, al tocar el path RF-instance,
           dispara accessor 0xd8284eac -> crea ctx -> 0xcbf56000 != 0.

PASO B — TECH_ENTER (LTE)   [FTM RFDEBUG sub 0x000D — FACT]
   4B 0B 27 00 0D 00 03 00
      01 00 04 00 00 00 00 00     ; SUB=0
      02 00 04 00 01 00 00 00     ; TECH=1 (LTE)
      03 00 04 00 00 00 00 00     ; SCENARIO=0

PASO C — RADIO_CONFIG (BAND + EARFCN + BW + RX_CARRIER)   [unpacker 0xd8183f30, field-tbl @0xc37c0290]
   4B 0B 27 00 <SUB_RC:u16 LE> 05 00
      01 00 04 00 00 00 00 00     ; RX_CARRIER (fid 1)  = 0
      19 00 04 00 01 00 00 00     ; TECH_MODE  (fid 25) = 1 (LTE)
      05 00 04 00 03 00 00 00     ; BAND       (fid 5)  = 3 (ej. B3)
      06 00 04 00 27 06 00 00     ; CHANNEL    (fid 6)  = EARFCN 1575
      07 00 04 00 20 4E 00 00     ; BANDWIDTH  (fid 7)  = 20000 kHz
   Efecto: con A ya hecho, el carrier-activate 0xd81bd018 pasa AMBOS gates -> APPLY ->
           memw(0xca79c494)=0xca6e3b88.

PASO D — VERIFICAR EN VIVO (obligatorio, peek DIAG de memoria):
   memb(0xcbf4f740) == 2      (gate a: RF cal mode)         [FACT gate]
   memw(0xcbf56000) != 0      (gate b: ctx RF creado)        [FACT gate]
   memw(0xca79c494) != 0      (carrier ptr poblado)          [FACT setter]

PASO E — IQ_CAPTURE / RX_MEASURE   (recién aquí; el getter 0xd827923c devuelve !=NULL, sin SSR)
```
El orden **A→B→C** es obligatorio. RADIO_CONFIG NO escribe 0xca79c494 (su unpacker sólo parsea a la
pila); dispara el activate, que sólo aplica con los gates de A abiertos. **FACT (gates) + INFERENCE (A).**

---

## 6. FACT / INFERENCE / UNKNOWN (con VAs)

**FACT (verificado byte-exact en clade_dec_36m.bin / full36.txt)**
- FTM_COMMON handler = **0xd8271290**; parsea sub-cmd = pkt[4]|pkt[5]<<8; despacha 0x10F/0x4F7/0x4F5.
- 0x10F handler = **0xd8263e2c**; param32 = pkt[6..9] LE; hace enable RF por-tech (tablas
  0xca79a8e0/0xca79a850); setea gp+0x1ed5/0x1ed6=1; **NO** toca 0xcbf4f740 ni 0xcbf56000.
- 0x4F7/0x4F5 handler = **0xd86f8514**; campos en pkt[0xa]/[0xc]/[0xe].
- Gate(a) reader **0xd81bd160** `memb(gp+0x740)`; `0xd81bd168 if(!=2) jump 0xd81bd4b0`. Enum {0,1,2}.
- Gate(b) reader **0xd8286240** `memw(gp+0x7000)`; `0xd8286248 if(!=0)…`. Creator **0xd8284cf4**;
  accessor **0xd8284eac** (`memw(gp+0x7000)=obj` @0xd8284ee4; vtable ##0xc37c6d80).
- gp = **0xcbf4f000** → gate(a)=**0xcbf4f740** (byte), gate(b)=**0xcbf56000** (word).
- **Writers de 0xcbf4f740 en 36 MB (los únicos):** 0xd81bd494 (=mux 0/1), 0xd81bd5fc (=mux 0/1),
  0xd81bd7c8 (=0 reset). **NINGÚN store del literal 2.**
- Struct de flags RF-cal vecino: 0xcbf4f732/33/37/38/3f (0/1), 0xcbf4f766 (memb=r2 @0xd835ba08/c050),
  0xcbf4f776 (@0xd82f0d84), 0xcbf4f71b=#3 (@0xd063b748 exc_high).
- APPLY 0xd8273b2c ← 0xd81bd2a8; setter 0xca79c494 = 0xd8279264 (valor 0xca6e3b88); getter 0xd827923c.
- Código válido del 36 MB llega a ~0xda800000; 0xdb000000+ = padding.

**INFERENCE (base dura)**
- El writer de `0xcbf4f740=2` vive FUERA del MBN (dlpager/RFLM: `rflte_ftm_*`/`ftm_calibration_v3_*`),
  disparado por el **FTM_SET_MODE canónico** (FTM_COMMON, mode=2/cal), NO por el 0x10F.
- El sub-cmd 0x10F es enable RF por-tech, no set-cal-mode.
- Secuencia A(SET_MODE cal)→B(TECH_ENTER)→C(RADIO_CONFIG) deja 0xca79c494 != NULL.

**UNKNOWN (sólo runtime / fuera del ELF)**
- VA del store `0xcbf4f740=2` (código RF-cal ausente).
- Sub-cmd/command_id/TLV EXACTO de FTM_SET_MODE en este build (dispatcher runtime 0xd8150ed8 no mapeado).
- Si el "modo cal" en tu HW se entra por DIAG-FTM SET_MODE o por servicio QMI/DMS especial.
- Valor de los gates en tu instante (estado RF en RAM).

---

## 7. REPRODUCIR
```bash
cd /tmp/modemre
# handlers (36 MB, autoritativo)
./dis36.sh 0xd8271290 0x90    # FTM_COMMON: parse pkt[4..5]; dispatch 0x10F/0x4F7/0x4F5
./dis36.sh 0xd8263e2c 0x90    # 0x10F: enable RF por-tech (NO setea gates)
./dis36.sh 0xd86f8514 0x50    # 0x4F7/0x4F5
./dis36.sh 0xd81bd160 0x10    # GATE(a): memb(gp+0x740); if(!=2) jump
./dis36.sh 0xd8286240 0x30    # GATE(b): memw(gp+0x7000)!=0
./dis36.sh 0xd8284eac 0x50    # accessor get-or-create gp+0x7000
# barrido exhaustivo del writer de 0x740 (los 3 vectores)
python3 scan_gp_all.py        # gp-relativo -> solo 0xd81bd494/0xd81bd5fc (mux 0/1)
python3 scan_gate2.py         # immext(0xcbf4f740) sites
python3 scan_abs_all.py       # abs store a 0xcbf4f740 -> solo =0 @0xd81bd7c8
grep -n "gp+#0x740)\|0xcbf4f740" /tmp/full36.txt   # disasm completo, verificación cruzada
```
