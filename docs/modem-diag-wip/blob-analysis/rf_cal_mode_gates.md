# rf_cal_mode_gates — Resolución de los DOS gates (gp+0x740 / gp+0x7000): VAs absolutas + quién los setea

**Target:** SM6375 / Moto G82 5G · MPSS.HI.4.3.4-00494-MANNAR_GEN_PACK-1.24452.133
**Imagen fiable:** `/tmp/modemre/clade_dec_full.bin` (VA base `0xd8000000`, 10 MB CLADE-descomp; `dis.sh <va> <len>`).
**Rodata:** b21 @0xc3553000, b23 @0xc8b6a000, b27(desc) seg27_dec.bin @0xce480000.
**RW/.sdata:** b25 @0xcbf4f000 (`0x1a000`), b26 @0xcc000000. **`.bss` RF ctx:** 0xca6exxxx / 0xca79xxxx.
**Herramientas:** `find_refs.py`, `resolve_callr.py`, `_fns.pkl`, `_idx.txt` (disasm de los 10 MB), `_dis_b13.txt`/`_dis_b10_full.txt` (código NATIVO).

**Leyenda:** **FACT** = leído byte/instrucción con VA · **INFERENCE** = deducción con base dura · **UNKNOWN** = sólo runtime/RAM.

> ⚠️ Nota de método: `clade_dec.bin` (54 MB) está **desalineado/mal-decodificado** (verificado: la store
> conocida `0xd81bd494` NO aparece en su offset). Sólo `clade_dec_full.bin` (10 MB) + los segmentos
> NATIVOS (b02..b13) son fiables para byte-scan. Todo lo de abajo se verificó en esa ventana fiable.

---

## 0. TL;DR

1. **gp (r28) = `0xcbf4f000`** (base del segmento b25 `.sdata/.sbss`). **FACT** (triangulación de 140
   offsets gp-relativos que tienen ref absoluta idéntica; cross-check exacto abajo).
2. **Gate (a) `gp+0x740` → VA absoluta `0xcbf4f740`** — byte, enum de **modo RF {0,1,2}**. El gate
   pide `==2`. **FACT.**
3. **Gate (b) `gp+0x7000` → VA absoluta `0xcbf56000`** — word, **puntero al ctx RF (objeto C++)**. El
   gate pide `!=0`. **FACT.**
4. **Gate (b) lo CREA un accessor lazy "get-or-create" `0xd8284eac`** (llamado desde `0xd828626c`):
   si `gp+0x7000==0`, aloca el objeto (`0xd8284cf4` → `memw(gp+0x7000)=obj`, vtable @0xc37c6d80) y lo
   guarda. **Corre bajo demanda la 1ª vez que se toca el subsistema RF-instance, NO en el boot.** **FACT.**
5. **Gate (a) `gp+0x740` — NO existe ningún store estático del literal `2` en TODO el código fiable**
   (10 MB CLADE + nativo). Los ÚNICOS writers byte son `0xd81bd494` y `0xd81bd5fc`, ambos
   `memb(gp+0x740)=mux(pred,#1,#0)` (**0 ó 1**), y están en la **rama NO-activate** del propio
   `0xd81bd018`. El literal `2` lo pone código **fuera de la ventana decodificada de forma fiable**
   (RF-driver de cal / seg mal-decomp) o por store computado en runtime. **FACT (ausencia de store #2)
   + INFERENCE (lo pone el comando de "enter cal mode" del RF driver).**
6. **Consecuencia:** en FTM "pelado" `gp+0x740` queda en 0 y `gp+0x7000` en NULL → gate(a) y gate(b)
   fallan → `0xd81bd018` salta el APPLY → `memw(0xca79c494)` sigue NULL → IQ_CAPTURE/RX_MEASURE llaman
   al getter `0xd827923c` → `err_fatal` → **SSR**. **FACT (gates) + FACT (cadena del SSR ya verificada
   en map_ftm_framework.md).**

---

## 1. RESOLUCIÓN DE gp Y VAs ABSOLUTAS DE LOS GATES

### 1.1 gp = 0xcbf4f000 — cómo se resolvió (FACT)
`gp` (r28) es el *global pointer* de Hexagon: se fija UNA vez al boot (por el loader QuRT) a la base
del área de small-data. El disassembler muestra `gp+#imm` sin resolver el valor. Se resolvió por
**triangulación**: hay 423 offsets gp-relativos distintos en los 10 MB; para la base candidata
`0xcbf4f000`, **140** de esos offsets tienen una referencia **absoluta idéntica** (`##imm`) en otra
instrucción — ningún otro candidato pasa de 14. **FACT.**

Cross-checks byte-exact (mismo global, dos formas de direccionarlo):
```
gp+0x732 :  0xd8150100  memb(gp+#0x732)=r0        <->  0xd8150230  r2 = ##0xcbf4f732 ; memb(r2)=#0xa
gp+0x733 :  0xd8167a20  memb(gp+#0x733)=r0        <->  0xd8167a4c  ##0xcbf4f733
gp+0x737 :  0xd81bd250  memub(gp+#0x737)          <->  0xd81bd1b0  ##0xcbf4f737
gp+0x740 :  0xd81bd160  memb(gp+#0x740)           <->  0xd81bd5b8  ##0xcbf4f740 (immext)
gp+0x750 :  0xd82c23c0  memb(gp+#0x750)=r17       <->  0xd82c23c8  ##0xcbf4f758
```
`0xcbf4f732 = -0x340b08ce`, `0xcbf4f740 = -0x340b08c0`, etc. (los ##imm negativos del disasm). **FACT.**

### 1.2 VAs absolutas
```
gp = 0xcbf4f000                     (b25 .sdata/.sbss ; PH: p_vaddr=0xcbf4f000, filesz=0x19900)
GATE (a) gp+0x740  =>  0xcbf4f740    (byte  : RF-mode enum {0,1,2})
GATE (b) gp+0x7000 =>  0xcbf56000    (word  : puntero al ctx/objeto RF-instance)
```
**FACT.**

### 1.3 Instrucciones exactas de cada gate (dentro de `0xd81bd018`) — FACT
```
GATE (a)  0xd81bd160:  r2 = memb(gp+#0x740)                       ; r2 = *(0xcbf4f740)
          0xd81bd168:  if (!cmp.eq(r2.new,#0x2)) jump 0xd81bd4b0  ; si !=2 -> salta el APPLY
GATE (b)  0xd8286240:  r2 = memw(gp+#0x7000)                      ; r2 = *(0xcbf56000)
          0xd8286248:  if (r2!=0) jump 0xd8286260 (r16=1)  else  r16=0 + log err(##0xf8048318)
          (wrapper tail  0xd82836c0: jump 0xd8286240)
          0xd81bd290:  p0=cmp.eq(r0,#1); if(!p0) jump 0xd81bd2e4  ; sólo r0==1 cae en APPLY 0xd81bd2a8
```
`gp+0x740` es un **enum de modo** (no un flag booleano): la función `0xd81bde74` lo mapea a 3 strings
distintas según valga `2` / `1` / `0` (`if(==2) str_A ; if(==1) str_B ; else str_C`) → es un
**mode-to-string logger**. **FACT.** Por eso "modo RF cal activo" = valor **2**.

---

## 2. GATE (a) gp+0x740 == 2 : QUIÉN LO ESCRIBE

### 2.1 Barrido EXHAUSTIVO de writers (FACT)
Accesos a `gp+0x740` / `##0xcbf4f740` en la ventana fiable (10 MB CLADE + nativo b13):
```
STORES (byte):
  0xd81bd494  memb(gp+0x740) = r3   ; r3 = mux(p0,#1,#0)   -> 0 ó 1     (rama NO-activate)
  0xd81bd5fc  memb(gp+0x740) = r3   ; r3 = mux(p2,#1,#0)   -> 0 ó 1     (rama NO-activate)
  0xd81bd7c8  memb(r2)=#0 ; r2=##0xcbf4f740                -> RESET a 0  (path de teardown)
LOADS (lectura):
  0xd81bd160 (GATE ==2) · 0xd81bd644 · 0xd81bd708 (==1) · 0xd81bde74 (logger 0/1/2) ·
  0xd81bf300 (==1) · 0xd81bf34c · 0xd81bf39c · 0xd81bf40c · 0xd81bdea0 ·
  0xc0db81b4 · 0xc0db81d0  (código NATIVO b13, sólo lecturas)
USOS-COMO-BASE (no tocan el byte 0x740):
  0xd8209200/0c, 0xd820bca8, 0xd82c23c4, 0xd82dd7b4  -> cargan 0xcbf4f740+N como puntero a
  campos adyacentes del struct (0x743,0x746,0x758...); no escriben el byte 0x740.
```
**Resultado (FACT):** **NO existe un solo store del literal `2` a `gp+0x740` en todo el código
decodificado de forma fiable.** Los únicos writers escriben `0`/`1` y están en la **rama
NO-activate** de `0xd81bd018` (el bloque a partir de `0xd81bd4b0`, al que se salta cuando el gate
`!=2` — o sea, cuando NO estamos en modo cal). Ese bloque gestiona el estado por-cadena RF
(gp+0x736/0x737/0x738/0x73c) y deja `gp+0x740` en 0/1. **FACT.**

Método de verificación (además del grep del disasm): se decodificó el campo del opcode
`S2_storerbgp` (`memb(gp+#off)=Rt`, Rt=bits[12:8]) y se confirmó que **no hay store-registro ni
store-inmediato a offset 0x740 con valor 2**. **FACT.**

### 2.2 ¿Entonces quién pone el 2? — INFERENCE (con base dura)
El literal `2` NO está en la ventana fiable ⇒ lo pone código en:
- (i) la porción del window CLADE que el decodificador Python no reconstruye bien (~26 % de words;
  sólo `libclade.so` es exacto), **o**
- (ii) un store **computado en runtime** (`memb(rX)=rY` con rX=puntero al struct y rY=valor de
  entrada), cuyo inmediato no es 2 estáticamente.

Semánticamente (INFERENCE fuerte, respaldado por strings y por el logger 0/1/2):
`gp+0x740` es el **modo RF del RF-driver de FTM** (equivalente a `rfm_mode`/estado cal). El valor `2`
= "modo RF cal / non-signaling activo". Lo enciende el **comando FTM que pone el device en cal mode**
(no un RFTEST 0x10xx): el `FTM_SET_MODE`/enter-cal del RF cmd-dispatch. Prueba de contexto (FACT):
- `ftm_calibration_v3_*` (seg21: `ftm_calibration_v3_seq_class.cpp` @0xc3919342, `..._data.c`
  @0xc4166134, etc.) — el subsistema de cal por secuencias.
- `rf_cmd_dispatch_register_tech` (seg27 @str) — el dispatcher RF por-tech/modulo.
- `lte_LL1_get_ul_ftm_cal_mode(cxn_id) > 0` (seg27, assert @str) — existe un "ftm_cal_mode" que
  debe ser > 0 para operar; consistente con `gp+0x740` como enum de cal-mode.
- selector FTM `0x0000` (FTM_COMMON) de la tabla `@0xc37bd1e8` contiene el clásico `FTM_SET_MODE`
  (Qualcomm FTM common) que conmuta el phone-mode a `FTM_PHONE_MODE_*`.

**Trigger exacto (INFERENCE):** el comando **FTM_SET_MODE / "set RF (cal) mode"** (DIAG subsys FTM
0x0B, selector FTM_COMMON=0x0000, sub-cmd SET_MODE con el phone-mode de cal), procesado por el
RF cmd-dispatch, es lo que deja `gp+0x740 = 2`. **No es un side-effect de TECH_ENTER ni de
RADIO_CONFIG**: es un comando de modo previo. **UNKNOWN**: el store byte-exact (vive fuera de la
ventana fiable) y el opcode/TLV exacto de SET_MODE en este build.

---

## 3. GATE (b) gp+0x7000 != 0 : QUIÉN LO INICIALIZA

### 3.1 El accessor lazy "get-or-create" (FACT)
```
0xd8284eac  (accessor / singleton getter):
   d8284eb4:  r17 = memw(gp+#0x7000)
   d8284eb8:  if (r17!=0) jump 0xd8284ef0            ; ya existe -> return r17
   d8284ebc:  call 0xd8284e60                         ; prepara args
   d8284ed4:  call 0xd8284cf4                         ; CREATOR (ver abajo)
   d8284ee4:  memw(gp+#0x7000) = r17                  ; guarda el ctx recién creado
   d8284ef0:  r0 = r17 ; jump 0xc0913b74              ; return ptr

0xd8284cf4  (CREATOR del ctx):
   d8284cfc:  call 0xd84c4b80                         ; ALLOC del objeto (r16 = obj)
   d8284d10:  memw(r16+#0x0) = ##0xc37c6d80           ; instala VTABLE C++ (obj es un C++ instance)
   d8284d14:  memw(r16+#0x84) = r19 ; +0x88 = r18     ; init campos
   d8284d18:  call 0xd8284778
   d8284d20:  memw(gp+#0x7000) = r16                  ; <<< STORE que puebla gp+0x7000 (0xcbf56000)
```
`gp+0x7000` = **puntero a un objeto C++ (RF-instance/carrier-mgr)** con vtable @`0xc37c6d80`
(métodos 0xd8285ce0..0xd8285d78). **FACT** (`find_refs 0xd8284cf4` = 1 caller: 0xd8284ed4;
`find_refs 0xd8284eac` = 1 caller: 0xd8286290, dentro de `0xd828626c`).

### 3.2 Cuándo corre (FACT del patrón + INFERENCE del disparo)
Es **lazy init bajo demanda**: la 1ª llamada al accessor crea el objeto; llamadas posteriores lo
devuelven. El accessor lo invoca `0xd828626c` (una función de verify/ensure del ctx RF). **NO hay
puntero estático ni caller estático a `0xd828626c`** (`find_refs`=0; `up_trace`=runtime-bound) → es
un **handler registrado en runtime** (msgr/event del subsistema RF). **FACT (ausencia de ref
estática) + INFERENCE (handler msgr).**

→ **Gate (b) NO se satisface en el boot "pelado".** `gp+0x7000` sólo se puebla cuando algo dispara el
path RF-instance (el mismo bring-up de cal/activate que toca gp+0x740). Hasta entonces vale NULL y el
gate(b) (`0xd8286240`) devuelve 0 → `0xd81bd018` no llega a APPLY. **FACT.**

### 3.3 No es parte del arranque incondicional
El init del módulo `0xd81e52c8` (que **registra** el carrier-activate `0xd81bd018` vía `0xd81e53dc`)
NO crea `gp+0x7000`; sólo prepara handlers y pone OTRO flag (`memb(gp+0x1dfb)=2` @0xd81e53fc — cluster
distinto, NO es gp+0x740). El ctx de gp+0x7000 se crea recién cuando corre el path RF-instance.
**FACT.**

---

## 4. ¿HAY UN COMANDO FTM "SET RF CAL MODE"?

### 4.1 En la tabla RFTEST 0x10xx — NO (FACT)
Barrido de la tabla RFTEST (`@0xc37c649c`, 24 stubs) y de los command_id 0..0x31: **ningún handler
0x10xx ni ningún unpacker (RADIO_CONFIG 0xd8183f30, TX_CONTROL 0xd8188428, cmd_id 3/4) escribe
`gp+0x740` ni `gp+0x7000`.** Verificado: los unpackers sólo parsean TLVs a la pila; el store del
modo/ctx no está en su call-graph. **FACT** (reach.py + barrido de writers §2.1/§3.1).
- **TX_CONTROL (slot 2 / command_id B+2)**: unpacker 0xd8188428 — controla TX, no toca gp+0x740/0x7000.
- **command_id 3, 4** (no mapeados): el 3 es **TRM_RRA** (slot 3, unpacker 0xd86c0fe4); el 4 está
  **vacío en el módulo 0** (no registrado). Ninguno setea los gates. **FACT.**

### 4.2 El comando de modo vive en el subsistema FTM-COMMON / RF-cmd-dispatch — INFERENCE
El "set RF cal mode" es un comando **DIAG subsys FTM (0x0B)**, **NO** un RFTEST 0x10xx:
- **Selector FTM_COMMON = 0x0000** (tabla `@0xc37bd1e8`, idx 0) → dispatcher común `0xd8150ed8`.
  Ahí vive `FTM_SET_MODE` (conmuta phone-mode a `FTM_PHONE_MODE_<tech>` / cal).
- El RF-cmd-dispatch por-tech (`rf_cmd_dispatch_register_tech`, seg27) enruta a los handlers RF que
  encienden el modo cal (`ftm_calibration_v3_*`).
**Sub_command/command_id/TLV exactos: UNKNOWN estático** en este build (el binding vive en RAM y el
store del literal 2 está fuera de la ventana fiable). Strings de apoyo (FACT): `FTM_MODE`
@0xc37bd180, `ftm_calibration_v3_seq_class.cpp` @0xc3919342, `lte_LL1_get_ul_ftm_cal_mode` (seg27).

### 4.3 Búsqueda de strings (FACT de existencia)
`cal_mode` → `lte_LL1_get_ul_ftm_cal_mode(cxn_id) > 0` (seg27) ; `rf_mode`/`rfm_mode` →
`rfm_mode_to_rfm_tech`, `rfc_convert_rfm_mode_to_front_end_tech` (seg27) ; `FTM_MODE` @0xc37bd180 ;
`set_mode` → `afc_set_mode_and_gain` (seg21, no aplica). `device_config`/`MODE_SET`/`FTM_SET`
literales: no en las tablas de strings extraídas. **FACT (lo listado existe; no hay literal
"set_cal_mode" directo).**

---

## 5. SECUENCIA COMPLETA DE BRING-UP RF-TEST (hasta `memw(0xca79c494) != NULL`)

Estado inicial FTM "pelado" (sólo TECH_ENTER): `gp+0x740=0`, `gp+0x7000=NULL`, `0xca79c494=NULL`.

```
PASO A — PONER EL MODEM EN RF CAL MODE  (setea gp+0x740 -> 2 y dispara el path RF-instance)
   Comando: FTM DIAG subsys 0x0B, selector FTM_COMMON (0x0000), sub-cmd = SET_MODE
            (phone-mode de cal / FTM_PHONE_MODE para LTE).
   Efecto (FACT del gate; INFERENCE del trigger): arranca el RF-driver de cal → escribe gp+0x740=2
            y, al tocar el subsistema RF-instance, dispara el accessor 0xd8284eac que CREA el ctx y
            puebla gp+0x7000 (!=0).  >>> Sin esto, gate(a) y gate(b) fallan y APPLY nunca corre.
   Prereqs HW: rfm_init (@0xce6de1b0 assert "RFM_INIT WAS NEVER CALLED"), MCPM ON
            (@0xce748848 "MCPM not turned ON yet"), NV/cal cargada.
   [Si en tu stack este "modo cal" se entra por QMI/DMS en vez de DIAG-FTM, es un modo especial de
    servicio; en la mayoría de FTM es el FTM_SET_MODE por DIAG. — UNKNOWN cuál aplica en tu HW.]

PASO B — TECH_ENTER (LTE)   [RFDEBUG sub 0x000D — FACT]
   4B 0B 27 00 0D 00 03 00
      01 00 04 00 00 00 00 00     ; SUB=0
      02 00 04 00 01 00 00 00     ; TECH=1 (LTE)
      03 00 04 00 00 00 00 00     ; SCENARIO=0
   Dispara commit 0xd81dfecc -> callr módulo LTE per-tech.

PASO C — RADIO_CONFIG (BAND + EARFCN/CHANNEL + BW + RX_CARRIER + RFM_DEVICE)
   Unpacker 0xd8183f30 (field-tbl @0xc37c0290). Orden: RX_CARRIER -> TECH_MODE -> BAND ->
   CHANNEL/CENTER_FREQ -> BANDWIDTH. Ej. LTE:
   4B 0B 27 00 <SUB_RC:LE> 05 00
      01 00 04 00 00 00 00 00     ; RX_CARRIER (fid 1) = 0
      19 00 04 00 01 00 00 00     ; TECH_MODE  (fid 25)= 1 (LTE)
      05 00 04 00 03 00 00 00     ; BAND       (fid 5) = 3 (ej B3)
      06 00 04 00 27 06 00 00     ; CHANNEL    (fid 6) = EARFCN 1575
      07 00 04 00 20 4E 00 00     ; BANDWIDTH  (fid 7) = 20000 kHz
   Efecto: aguas abajo (módulo FTM-LTE, rflte_mc_carrier_activate @0xce6e72c0 /
   rflte_ftm_mc_wakeup @0xce6e08a8) completa el activate de la portadora. Con gp+0x740==2 y
   gp+0x7000!=0 YA seteados en A, el carrier-activate 0xd81bd018 pasa AMBOS gates ->
   APPLY 0xd8273b2c -> SETTER 0xd8279264 -> **memw(0xca79c494) = 0xca6e3b88**.

PASO D — VERIFICAR EN VIVO  (obligatorio antes de cualquier measure):
   leer memw(0xcbf56000) != 0      (gate b: ctx RF creado)     [peek DIAG memoria]
   leer memb(0xcbf4f740) == 2      (gate a: RF cal mode)       [peek DIAG memoria]
   leer memw(0xca79c494) != 0      (carrier ptr poblado)       [peek DIAG memoria]
   (equivalente: memb(0xca7897b0 + tech*8) == 1 tras el activate)

PASO E — RECIÉN AHORA: IQ_CAPTURE / RX_MEASURE
   El getter 0xd827923c devuelve el ptr (no NULL) -> NO asserta -> SIN SSR.
```

**El orden A→B→C es obligatorio.** RADIO_CONFIG **no** escribe `0xca79c494` (su unpacker sólo parsea
a la pila); lo que hace es **disparar el activate** con BAND+EARFCN. Pero el activate sólo aplica si
gp+0x740==2 y gp+0x7000!=0, que se encienden en el **PASO A (set cal mode)**. Sin A, C corre pero el
APPLY se salta y `0xca79c494` sigue NULL. **FACT (gates) + INFERENCE (A = FTM_SET_MODE).**

---

## 6. FACT / INFERENCE / UNKNOWN (con VAs)

**FACT**
- `gp = 0xcbf4f000` (b25 .sdata/.sbss). Triangulación 140/423 + cross-check byte-exact
  (gp+0x732↔##0xcbf4f732 @0xd8150100/0xd8150230; gp+0x740↔##0xcbf4f740 @0xd81bd160/0xd81bd5b8).
- Gate (a) VA = **0xcbf4f740** (byte). Read/gate `0xd81bd160`→`0xd81bd168 if(!=2) jump 0xd81bd4b0`.
- Gate (b) VA = **0xcbf56000** (word). Read/gate `0xd8286240` (wrapper tail 0xd82836c0);
  `if(memw(gp+0x7000)!=0) r16=1 else 0+log(##0xf8048318)`; APPLY sólo si r0==1 (0xd81bd290).
- gp+0x7000 lo crea el accessor lazy **0xd8284eac** (get-or-create) → creator **0xd8284cf4**
  (alloc 0xd84c4b80, vtable ##0xc37c6d80, `memw(gp+0x7000)=obj` @0xd8284d20 y @0xd8284ee4).
  Callers estáticos: 0xd8284eac←0xd8286290(en 0xd828626c); 0xd8284cf4←0xd8284ed4. 0xd828626c =
  runtime-bound (handler msgr).
- gp+0x740: ÚNICOS writers byte = **0xd81bd494** y **0xd81bd5fc**, ambos `=mux(pred,#1,#0)` (0/1),
  en la rama NO-activate (post-0xd81bd4b0); reset a 0 @0xd81bd7c8. **NINGÚN store del literal 2** en
  10 MB CLADE ni en nativo (b13 sólo lee: 0xc0db81b4/0xc0db81d0).
- gp+0x740 es enum {0,1,2}: logger mode→string `0xd81bde74` (`==2`/`==1`/else, strings
  0xf8032980/0xf80329c0/…).
- Init del módulo 0xd81e52c8 registra 0xd81bd018 (0xd81e53dc); pone gp+0x1dfb=2 (0xd81e53fc,
  cluster distinto), NO gp+0x740.
- Cadena del SSR (ya verificada): getter 0xd827923c `memw(0xca79c494)` NULL→err_fatal 0xd80d8998→
  0xc0d60074; setter único 0xd8279264 sólo desde APPLY 0xd8273b2c←0xd81bd2a8 (en 0xd81bd018);
  valor guardado 0xca6e3b88.
- clade_dec.bin (54 MB) desalineado (store conocida no aparece en su offset) → sólo fiable el 10 MB.

**INFERENCE (base dura)**
- `gp+0x740 == 2` = "modo RF cal / non-signaling activo"; lo pone el comando **FTM_SET_MODE**
  (DIAG subsys FTM 0x0B, selector FTM_COMMON=0x0000), procesado por el RF cmd-dispatch/
  ftm_calibration_v3, cuyo store byte vive fuera de la ventana decodificada de forma fiable.
- `gp+0x7000` se puebla en el mismo bring-up de cal (accessor lazy disparado por el path RF-instance),
  NO en el boot.
- Ningún RFTEST 0x10xx (RADIO_CONFIG/TX_CONTROL/cmd_id 3=TRM_RRA/4=vacío) setea los gates.
- Secuencia A(SET_MODE cal)→B(TECH_ENTER LTE)→C(RADIO_CONFIG BAND+EARFCN) deja 0xca79c494!=NULL.

**UNKNOWN (sólo en vivo / RAM / fuera de ventana)**
- El store byte-exact de `gp+0x740=2` (VA del writer) y el sub_command/command_id/TLV EXACTO del
  comando "set RF cal mode" en este build (FTM_SET_MODE vs. un modo QMI/DMS especial).
- Valor de gp+0x740 / gp+0x7000 en tu instante (estado RF).
- Si el "modo cal" de tu stack se entra por DIAG-FTM o por un servicio QMI/DMS especial.

---

## 7. REPRODUCIR
```bash
# gp resolution (triangulación + cross-check)
python3 - <<'PY'
import re
L=open('/tmp/modemre/_idx.txt').read().splitlines()
gp=set(); ab={}
for ln in L:
  for m in re.finditer(r'gp\+#(0x[0-9a-f]+)',ln): gp.add(int(m.group(1),16))
  for m in re.finditer(r'##(-?0x[0-9a-f]+)',ln):
    v=int(m.group(1),16); v&=0xffffffff; ab[v]=1
best=max(range(0xcbf40000,0xcc010000,0x1000), key=lambda b:sum(1 for o in gp if b+o in ab))
print("gp =",hex(best))  # -> 0xcbf4f000
PY
dis.sh 0xd81bd160 0xc      # GATE(a): memb(gp+0x740) ; if(!=2) jump 0xd81bd4b0
dis.sh 0xd8286240 0x30     # GATE(b): memw(gp+0x7000)!=0 ? 1 : 0
dis.sh 0xd8284eac 0x50     # accessor get-or-create gp+0x7000
dis.sh 0xd8284cf4 0x40     # creator: memw(gp+0x7000)=obj (vtable ##0xc37c6d80)
dis.sh 0xd81bd478 0x90     # writers gp+0x740 = mux(pred,#1,#0)  (0/1, rama no-activate)
dis.sh 0xd81bde74 0x40     # logger mode->string (==2/==1/else) => gp+0x740 es enum {0,1,2}
python3 find_refs.py 0xd8284cf4   # 1 caller 0xd8284ed4
python3 find_refs.py 0xd8284eac   # 1 caller 0xd8286290 (en 0xd828626c)
python3 find_refs.py 0xd828626c   # (vacío -> runtime/msgr-bound)
# Prueba de ausencia de store #2 a gp+0x740:
grep -nE "memb\(gp\+#0x740\) = |0xcbf4f740" /tmp/modemre/_idx.txt   # sólo mux(0/1) + usos-base
```
