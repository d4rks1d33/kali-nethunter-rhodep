# map_ftm_framework — Mapa COMPLETO del framework FTM/RFTEST + causa raíz del SSR

**Target:** SM6375 / Moto G82 5G · MPSS.HI.4.3.4-00494-MANNAR_GEN_PACK-1.24452.133
**Base:** `0xd8000000` (`/tmp/modemre/clade_dec_full.bin`, tool `dis.sh <va> <len>`).
**Rodata:** b21 @0xc3553000, b23 @0xc8b6a000, b27(descomp) seg27_dec.bin @0xce480000. `.sdata/.bss` RW: b25 @0xcbf4f000, b26 @0xcc000000.
**Reach:** `reach.py`/`find_refs.py`/`_fns.pkl`. Cross-ref: reg_order_B, command_id_map_B, tech_state_gate, session_start, enter_mode_path, gate_resolution, radio_config_unpack, slot_correlation.

**Leyenda:** **FACT** = leído del disasm/bytes con VA · **INFERENCE** = deducción con evidencia · **UNKNOWN** = sólo runtime/en vivo.

---

## 0. TL;DR — LA PIEZA CLAVE (SSR) EN UNA FRASE

> **El SSR es un `err_fatal` (assert) por NULL en el getter `0xd827923c` (`r0 = memw(0xca79c494)`; si `==0`
> → `call 0xd80d8998` → `jump 0xc0d60074`).** El único escritor de `0xca79c494` es el setter
> `0xd8279264`, alcanzable **SÓLO** por `0xd81bd018 → APPLY 0xd8273b2c → SETTER 0xd8279264`.
> `0xd81bd018` es el **handler de carrier-activate/enter-mode del módulo FTM-LTE**, registrado en
> runtime; **ningún comando DIAG (RADIO_CONFIG incluido) escribe `0xca79c494` por sí solo.** El
> carrier-apply dentro de `0xd81bd018` está detrás de DOS gates de estado de HW/mode
> (`memb(gp+0x740)==2` y `0xd8286240`⇒`memw(gp+0x7000)!=0`). Mientras esos gates no pasen (RF no
> encendido/activado en tu contexto FTM), `0xca79c494` queda NULL y **cualquier** comando de
> measure/capture (IQ_CAPTURE, RX_MEASURE, y la familia RADIO_CONFIG-handler 0x1002/0x1003) que llame
> al getter revienta el modem. **FACT (cadena verificada por reach.py + disasm).**

**Todas las cadenas verificadas con reach.py (`_fns.pkl`):**
```
IQ_CAPTURE handler 0x1002 (0xd86fd0f4) -> 0xd8272cd8 -> 0xd826d3e4 -> GETTER 0xd827923c   [LOAD 0xca79c494]  FACT
RADIO_CONFIG handler 0x1003 (0xd86fd230) -> 0xd826d67c -> GETTER 0xd827923c               [LOAD 0xca79c494]  FACT
callback 0xd81bd018 -> APPLY 0xd8273b2c -> SETTER 0xd8279264                              [STORE 0xca79c494] FACT
init      0xd81e52c8 -> 0xd81bd018 -> 0xd8273b2c -> 0xd8279264                            [STORE]           FACT
RADIO_CONFIG UNPACKER 0xd8183f30 : setter=NO getter=NO  (sólo parsea TLVs a la pila)                        FACT
```

---

## 1. TABLA COMPLETA DE COMANDOS RFTEST (registration order 0..0x11)

### 1.1 Cómo se registran (FACT)
Master registrar **`0xd84aa03c`** llama 3 module-inits en orden:
```
d84aa03c: call 0xd8182640   ; MODULE 0 = RF-TEST commands (group id memb(desc+0x10)=0x12)   ← primero
d84aa040: call 0xd81793b0   ; MODULE 1 (group 0x13, ctx @0xca65b5c0)
d84aa044: call 0xd86c3ee8   ; MODULE 2 (group 0x0b, ctx @0xcb676300)
```
`0xd8182640` (module 0) aloca un descriptor 0x18B en ctx **@0xca65d640**, pone `memb(desc+0x10)=0x12`,
aloca sub-ctx en `desc+0x14`, y escribe cada comando en un **slot de 12 (0xc) bytes**:
`slot[i] = {unpack@+0, repack@+4, bufsz@+8}`, con `slot_index = (add_offset - 0xc)/0xc`.
Pass 1 (unpack) instala en offsets 0xc,0x18,0x24,0x30,0x48,0x60,0x54,0x6c,0x78,0x84,0x90;
pass 2 (repack) reinstala en los MISMOS offsets. **FACT** (`dis.sh 0xd8182640 0x2c0`).

### 1.2 La tabla (slot_index = registration order = módulo-0 base+i) — FACT

| slot | reg-init fn | UNPACKER VA (real) | field-table | REPACK VA | bufsz | comando (F3 byte-exact) | naturaleza |
|-----:|-------------|--------------------|-------------|-----------|-------|-------------------------|------------|
| **0** | 0xd8182e50 | 0xd8182ec0→0xd818327c→**0xd8183f30** | @0xc37c0290 (nombres @0xc906c630) | 0xd8182f40 | 0x174810 | **RADIO_CONFIG** (F3 @0xc37c0320) | tune band/ch/bw |
| **1** | 0xd818552c | **0xd8185654** (disp 0xd8185b1c) | @0xc37c0478 | 0xd81865f4 | 0x540 | **RX_MEASURE** (F3 @0xc37c0590) | measure query |
| 2 | 0xd81883c0 | **0xd8188428** | @0xc37c078c | 0xd818866c | 0x540 | **TX_CONTROL** (F3 @0xc37c07d8) | tx control |
| 3 | 0xd86c2324 | **0xd86c0fe4** | — | 0xd86c240c | 0x4d740 | **TRM_RRA** (F3 @0xc3561a10/c4168270) | TRM resource |
| (4) | — | *(slot 0x3c NO registrado en mod0)* | — | — | — | *(vacío en mod0)* | — |
| **5** | 0xd818933c | **0xd81893a4** (disp 0xd8189818) | @0xc37c0828 (fmt @0xc37c08f4) | 0xd8189d38 | 0x100 | **IQ_CAPTURE** (F3 @0xc37c08f4) | sample capture |
| 6 | 0xd818ab08 | **0xd818ab6c** | @0xc37c0a10 | 0xd818bc20 | — | **TX_MEASURE** (F3 @0xc37c0dc4) | tx measure |
| 7 | 0xd8187f80 | **0xd8187fe4** | — | 0xd818837c | — | **MSIM_CFG** (F3 @0xc37c073c) | multi-SIM cfg |
| 8 | 0xd84afe9c | **0xd84aff68** | — | 0xd84b1514 | 0x64440 | **IRAT_CONFIG** (F3 @0xc391a3f8) | inter-RAT cfg |
| 9 | 0xd8187098 | **0xd81870fc** | @0xc37c0644 | 0xd8187a18 | — | **WAIT_TRIGGER** (F3 @0xc37c0664) | trigger wait |
| 10 | 0xd84ad048 | **0xd84ad0b8** | — | 0xd84ad1d4 | 0x80 | **tx_measure** (RFDEBUG) (F3 @0xc391a2f8) | debug tx meas |
| **11** | 0xd8184980 | 0xd81849e4→**0xd81849ec** | @0xc37c03b4 (nombres @0xc906ce18) | 0xd81851a4 | 0x2a0 | **COMMAND_CAPABILITY** (F3 @0xc37c03cc) | query/CMD_MASK |

Los 11 nombres son **byte-exact** (F3 fmt resuelto de b21 — ver §Reproducir). **FACT.**

### 1.3 command_id 0..0x31 — FACT vs UNKNOWN (sin cambios respecto a reg_order_B/command_id_map_B)
- **slot_index dentro de mod0 = registration order = FACT** (tabla §1.2).
- El **entero absoluto command_id (byte 10 del wire, ≤0x31)** se asigna en RUNTIME: la tabla maestra
  `@0xca79a850` se aloca/memset (0xd8272368) y se rellena con defaults (0xd82723c0, 50 slots 0x28B,
  sin nombres); el binding `command_id→unpacker` se escribe al registrar en RAM; y el
  `command_id→group` es la tabla byte `@0xca9ef490` (memb-indexed). **Ningún unpacker aparece como
  inmediato estático fuera de su propio cuerpo.** **FACT (verificado).**
- Módulos 1 (group 0x13, @0xca65b5c0) y 2 (group 0x0b, @0xcb676300) ocupan el resto del espacio
  0..0x31 con sus propios comandos; su enumeración exacta es **UNKNOWN estático** (mismo mecanismo).
- **RELATIVO (FACT):** con base mod0 = B: RADIO_CONFIG=B+0, RX_MEASURE=B+1, TX_CONTROL=B+2, TRM_RRA=B+3,
  IQ_CAPTURE=B+5, TX_MEASURE=B+6, MSIM_CFG=B+7, IRAT_CONFIG=B+8, WAIT_TRIGGER=B+9, tx_measure=B+10,
  COMMAND_CAPABILITY=B+11.
- **Cierre en vivo:** COMMAND_CAPABILITY → CMD_MASK (field 3): bit N = command_id N registrado;
  QUERY_COMMAND=N + PROPERTY_MASK_* nombra cada uno (command_id_map_B §6).

### 1.4 Dos ejes en el wire (recordatorio, FACT)
- `sub_command` = wire[4..5] LE (0x10xx) → jump-table `@0xc37c649c` (24 stubs) → handler wrapper.
  Dispatcher RFTEST = **0xd82714b4** (gate hi==0x10, low≤0x17).
- `command_id` = wire byte 10 (`memub(req+0xa)`, ≤0x31) → resolver **0xd8272684**
  (`memw(memw(0xca79a850)+id*4+0x34)`) → struct-comando (unpack/repack). **El unpacker que corre lo
  fija command_id, NO el sub_command.**

---

## 2. STATE MACHINE COMPLETA DEL FTM

Hay DOS estructuras de estado distintas; no confundirlas.

### 2.1 `session->0xc` = MODO operativo de la sesión FTM-RF (enum) — FACT
Inicializador de sesión **0xd81df874** deja `session+0xc = 0` (sólo pone `+0x4=+0x8=0x15`=21 techs).
`session->0xc` es un enum leído/comparado en toda la SM:

| VA | Instrucción | Estado |
|----|-------------|--------|
| 0xd81df874 | init: `session+0xc = 0` (memset) | **0 = INACTIVO** (arranque) |
| 0xd81e0a60 | `if (session->0xc == 0) skip` | 0 = inactivo |
| 0xd81e0360 | switch `cmp.gt(v,2)` / `==1` / `==2` / `==3` | enum 0..3 |
| 0xd81dff14 | `r16 = session->0xc`; `r16-1 ≤ 0x13` → indexa `@0xca733d10` | usado como índice 1..0x14 |
| **0xd81e5d20** | **`if (session->0xc != 2) skip store flag`** | **2 = ACTIVO/RUNNING** (gate del enter-writer) |
| 0xd81e01ac / 0xd81e023c | `cmp.eq(session->0xc, 0x13)` | 0x13 = otro modo |

**Transiciones (FACT del uso, INFERENCE del disparador):**
```
[0 INACTIVO] --(init 0xd81df874)--> queda 0
[0] --(commit FTM 0xd81dfecc corre callback del módulo per-tech y el activate/wakeup completa)--> [2 ACTIVO]
[2] --(enter-writer 0xd81e5cec ve session->0xc==2)--> escribe flag[tech] @0xca7897b0 = 1
[2/otros] --(exit 0xd81e5e18)--> flag[tech]=0
```
**NO existe store estático `(session+0xc)=2`** (barrido completo 10MB). El modo 2 lo avanza el
**path de activate FTM** (módulo `ftm_lte_*`/`rflte_mc_carrier_activate`/`rflte_ftm_mc_wakeup`,
cluster @0xce6exxxx) cuando la portadora se configura y el HW responde. **FACT (ausencia de store) +
INFERENCE (side-effect del activate).**

### 2.2 `@0xca7897b0[tech]` = flag per-tech "ENTERED" (1 byte, stride 8) — FACT
- Escritor ENTER: **0xd81e5cec** → store `=1` @**0xd81e5d54** (r3=#1 @0xd81e5d44), gated por
  `session->0xc==2` (0xd81e5d20). Post-check `ctx->0x89a8==0x7` (0xd81e5d60) → err 0x10 → abort.
- Escritor EXIT: **0xd81e5e18** → store `=0` @**0xd81e5fc4**.
- Gate lector (RFTEST action executor): **0xd8202224** `memb(tech<<3+0xca7897b0)`, **0xd8202228**
  `if(!=1) → rama alterna → status 0x14`. Vive en **0xd8201d3c** (executor de acción diferida,
  RF-task), NO en el path DIAG del unpacker (gate_resolution §1). **FACT.**

### 2.3 Diagrama de estados (VAs)
```
                         ┌──────────────────────────────────────────────────────────────────┐
   DIAG 4B 0B / 0x27     │  DISPATCH                                                          │
        │                │  0xd8150ed8 → RFDEBUG 0xd816d2d4 (tbl @0xca65b414, sub≤0x15)       │
        │                │             → RFTEST  0xd82714b4 (tbl @0xc37c649c, 0x10xx)         │
        ▼                └──────────────────────────────────────────────────────────────────┘
  TECH_ENTER (RFDEBUG sub 0x0d) 0xd8174758
        │  TLV TECH → tech-index (map @0xc37bdfe0) → tabla per-tech (stride 0x1c)
        │  → 0xd816d658 → callr commit per-tech  ─────────────┐
        ▼                                                     │ (callr runtime)
  commit 0xd81dfecc  ── indexa @0xca733d10[ session->0xc ] ──▶ callback del módulo LTE (RAM)
        │  r16=session->0xc ; r20=memw(sess+0)                │
        │  callr memw(record+4)                               ▼
        │                                        ┌─────────────────────────────────────────┐
        │                                        │  MÓDULO FTM-LTE (ftm_lte_*, @0xce6exxxx) │
        │                                        │  rflte_mc_carrier_activate /            │
        │                                        │  rflte_ftm_mc_wakeup  → enciende RF     │
        │                                        └─────────────────────────────────────────┘
        │                                                     │  (al completar HW/MCPM)
        ▼                                                     ▼
  session->0xc  0 ───────────────────────────────────────▶  2  (ACTIVO)
        │                                                     │
        │  (con session->0xc==2)                              │
        ▼                                                     ▼
  enter-writer 0xd81e5cec:  @0xca7897b0[tech] = 1  ◀──────────┘
        │
        ▼
  gate action 0xd8202228 (==1) abre  →  executor RX_MEASURE/IQ_CAPTURE 0xd8201d3c corre

  ── EN PARALELO (carrier-apply, la pieza del SSR) ──
  callback carrier-activate 0xd81bd018   (registrado por init 0xd81e52c8; msgr/event)
        │  gate1: memb(gp+0x740)==2          (0xd81bd160)
        │  gate2: 0xd8286240 ⇒ memw(gp+0x7000)!=0   (0xd82836c0 → 0xd8286240)
        ▼  (ambos gates OK)
  APPLY 0xd8273b2c → SETTER 0xd8279264 :  memw(0xca79c494) = 0xca6e3b88 (ctx carrier)
```

---

## 3. EL SSR — CAUSA RAÍZ DEFINITIVA

### 3.1 El punto de fallo exacto (FACT, byte-a-byte)
```
GETTER 0xd827923c:
  d827923c: immext(#0xca79c480)
  d8279240: r0 = memw(##0xca79c494)                 ; r0 = active-carrier ptr
  d8279244: if (!cmp.eq(r0.new,#0)) jump 0xd8279260 ; si != NULL → return r0
  d8279248: call 0xd80d8998                         ; <<< NULL → ASSERT
  d8279250: r0 = ##0xf803b090 ; allocframe          ;     (string arg)
  ...
0xd80d8998: immext(#0xe8c876c0); jump 0xc0d60074    ; err_fatal / exception → SSR   FACT
```
**Cuando `0xca79c494 == NULL`, el getter llama `err_fatal` → SSR.** El getter NO devuelve; aborta.
**FACT.**

### 3.2 Quién llama al getter (todos los comandos de measure/capture) — FACT
`find_refs 0xd827923c` = **~96 call-sites**, todos en el pipeline RF (0xd826xxxx measure/capture,
0xd827exxxx, 0xd829xxxx). Los relevantes al SSR:
```
0xd826d40c  dentro de 0xd826d3e4  = executor decode-A (IQ_CAPTURE/measure, mode==5). callr desde 0xd8272cd8.
0xd826d694  dentro de 0xd826d67c  = executor de la familia RADIO_CONFIG-handler (slot 0x1003).
0xd8292298                        = executor decode-B (measure, mode!=5).
```
reach.py (probado): `0xd86fd0f4 (0x1002) → 0xd8272cd8 → 0xd826d3e4 → 0xd827923c`. **FACT.**

### 3.3 El ÚNICO escritor de `0xca79c494` — FACT
```
SETTER 0xd8279264:
  d827926c: memw(##0xca79c494) = r0                 ; STORE (incondicional en el 1er packet)
  ...luego escrituras extra por-command_id en 0xca79a8e0[id]+0x24/+0x10 con gates id≤0x31
APPLY 0xd8273b2c:
  d8273b34: call 0xd81bd7ec                          ; r0 = ##0xca6e3b88  (ctx carrier fijo en .bss)
  d8273b3c: r19:18 = combine(r0,r1)
  d8273b40: call 0xd8279264                          ; SETTER( r0 = 0xca6e3b88 )
```
`find_refs 0xd8279264` = **1 caller: 0xd8273b40** (dentro de APPLY).
`find_refs 0xd8273b2c` = **1 caller: 0xd81bd2a8** (dentro de 0xd81bd018).
`find_refs 0xd81bd018` = **1 caller estático: 0xd81e53dc** (dentro de init 0xd81e52c8). Sin puntero
crudo en RAM (runtime-registered como msgr/event handler). **FACT.**

→ **El setter escribe `0xca79c494 = 0xca6e3b88` SÓLO cuando corre `0xd81bd018`** (el
carrier-activate/enter-mode del módulo FTM-LTE). **Ningún handler DIAG lo alcanza:**
`reach.py 0xd82714b4` (dispatcher), `0xd8174758` (TECH_ENTER), `0xd8183f30` (RADIO_CONFIG unpacker)
= **setter=NO**. **FACT.**

### 3.4 Por qué `0xca79c494` sigue NULL después de TECH_ENTER — los DOS gates dentro de 0xd81bd018
`0xd81bd018` (carrier-activate handler) sólo llega a APPLY si pasa:

| gate | VA | condición | qué es (INFERENCE) |
|------|----|-----------|--------------------|
| **G-mode** | 0xd81bd160 | `memb(gp+0x740) != 2` → jump 0xd81bd4b0 (salta el bloque de activate) | flag global de MODO RF (== 2 significa "RF cal/activo"). Antes: cluster ce6exxxx `FTM_RF_MODE_CAL`. |
| **G-ctx** | 0xd81bd28c→0xd8286240 | `0xd82836c0` (→0xd8286240) devuelve 1 ⇔ `memw(gp+0x7000) != 0` | contexto/instancia RF (enter-mode) presente. Si `gp+0x7000==NULL` → log err (`0xf8048318`) → return 0 → **NO APPLY**. |

```
0xd8286240: r2 = memw(gp+#0x7000)
0xd8286248: if (r2 != 0) jump 0xd8286260   ; r2!=0 → r16=1 (OK) ; else r16=0 + log err
0xd8286260: r0 = r16 ; dealloc_return
0xd81bd290: p0 = cmp.eq(r0,#1); if (!p0) jump 0xd81bd2e4   ; sólo r0==1 → cae en APPLY 0xd81bd2a8
```
**Conclusión (la respuesta exacta):** TECH_ENTER LTE dispara el commit `0xd81dfecc` que hace `callr`
al módulo per-tech; ese módulo debe ejecutar el carrier-activate (`0xd81bd018`), pero el APPLY interno
está **detrás de `gp+0x740==2` y `gp+0x7000!=NULL`**. En un FTM "pelado" (RF no encendido / MCPM off /
`rfm_init` no llamado / sin config de portadora), **esos gates fallan**, APPLY no corre, el setter no
se ejecuta, y **`0xca79c494` queda en 0 (NULL) de fábrica**. El siguiente IQ_CAPTURE/RX_MEASURE llama
al getter → NULL → `err_fatal` → **SSR**. **FACT de los gates + INFERENCE de por qué no se cumplen.**

### 3.5 ¿MSGR que no llega / timer / HW? — respuesta
- **NO es un timer.** El APPLY no depende de un timeout; depende de dos flags de estado (`gp+0x740`,
  `gp+0x7000`).
- **NO es directamente el `enter_mode_cnf` del ML1** (enter_mode_path §3: en FTM ese cnf no se pide;
  el path FTM usa `rflte_ftm_mc_wakeup` directo).
- **Es una CONDICIÓN DE ESTADO/HW:** `gp+0x740` (modo RF==2) y `gp+0x7000` (instancia/ctx RF no-nulo)
  los enciende el **arranque del subsistema RF en modo cal** + el **activate de portadora** (que exige
  BAND+EARFCN válidos: assert `band_get_band_from_dl_earfcn` @0xce8bdc10, `ftm_lte_tune:
  current_dl_to_ul_map` @0xce6dfee8). Prereqs de HW: `rfm_init` (@0xce6de1b0), MCPM ON (@0xce748848),
  NV/RxLM. **FACT (gates + strings) + INFERENCE (qué los enciende).**

### 3.6 La secuencia EXACTA que deja `0xca79c494 != NULL` (desbloquea IQ)
```
1. FTM: poner el modem en RF CAL MODE  (arranca rfm_init + MCPM ; setea gp+0x740 → 2 y crea el ctx
   RF que puebla gp+0x7000). Es el comando FTM de set_mode/cal, NO un RFTEST 0x10xx.
   >>> Sin esto, G-mode/G-ctx fallan y APPLY nunca corre.
2. TECH_ENTER LTE (RFDEBUG sub 0x0d, TECH=1) → dispara commit 0xd81dfecc → callr módulo LTE.
3. RADIO_CONFIG (BAND + EARFCN/CHANNEL + BANDWIDTH + RX_CARRIER + RFM_DEVICE) → aguas abajo, en el
   módulo FTM-LTE, corre rflte_mc_carrier_activate/wakeup → completa el activate → el carrier-activate
   handler 0xd81bd018 pasa G-mode(gp+0x740==2) y G-ctx(gp+0x7000!=0) → APPLY 0xd8273b2c → SETTER
   0xd8279264 → **memw(0xca79c494) = 0xca6e3b88**.
4. VERIFICAR EN VIVO: leer memw(0xca79c494) != 0  (o: leer memb(0xca7897b0 + tech*8) == 1).
5. RECIÉN AHORA: IQ_CAPTURE / RX_MEASURE → el getter 0xd827923c devuelve el ptr, no asserta → sin SSR.
```
**El orden es obligatorio.** RADIO_CONFIG **no** escribe `0xca79c494` (su unpacker 0xd8183f30 sólo
parsea TLVs a la pila — reach.py: setter=NO). Lo que sí hace RADIO_CONFIG es, con BAND+EARFCN,
**disparar el activate** que hace pasar los gates de `0xd81bd018`, que es quien escribe el ptr.
**FACT (unpacker no escribe) + INFERENCE (activate como disparador, cluster FTM-LTE verificado).**

### 3.7 Mitigación defensiva (para el driver Linux / no crashear al barrer)
- **Nunca** mandar IQ_CAPTURE/RX_MEASURE (ni la familia measure 0x1002/0x1003) hasta confirmar
  `memw(0xca79c494)!=0`. Un `err_fatal` aquí = SSR total, no un status 0x14.
- Los comandos QUERY-safe (§tabla slot_correlation: 0x1004,0x1005,0x1007,0x1008,0x1009,0x100a,0x100f,
  0x1011,0x1012,0x1014,0x1015,0x1017) NO llegan al getter con num_tlv=0 → seguros para barrer.

---

## 4. RADIO_CONFIG A FONDO — carrier-apply path completo

### 4.1 El unpacker (parser puro, NO escribe el carrier ptr) — FACT
- Unpacker: **0xd8183f30** (wrap 0xd8182ec0→0xd818327c). field-handlers `@0xc37c0290` (idx=field_id-1,
  válido 1..36); nombres `@0xc906c630`.
- `num_tlv==0` o ptr TLV nulo → **bail LIMPIO** 0xd818486c (r24=0x10 → status 0x14). **NO SSR.**
- TLV normalizado = registro fijo 0xc bytes `{field_id:u16, pad:u16, value:u64}`; loop r20+=0xc.
- Campos clave (FACT, nombres byte-exact): **1=RX_CARRIER** (fija idx carrier r29+0x20, set bit
  bitmap r29+0x68), 2=TX_CARRIER, 3=RFM_DEVICE, **5=BAND** (store r29+0x60), **6=CHANNEL/EARFCN**
  (array per-carrier, gate idx≤2), **7=BANDWIDTH** (enum, per-carrier), 12=CENTER_FREQ (u64),
  **25=TECH_MODE** (LTE=1).
- **reach.py 0xd8183f30: setter=NO, getter=NO, resolver=NO, region=NO** → el unpacker **sólo escribe
  la pila** (contexto de ~6.7KB), no toca `0xca79c494` ni la región RF. **FACT.**

### 4.2 El path real hasta el store en 0xca79c494 (FACT + INFERENCE)
RADIO_CONFIG **no** llama al setter. La cadena es indirecta:
```
RADIO_CONFIG (unpack 0xd8183f30 → repack 0xd8182f40) parsea BAND/EARFCN/BW/RX_CARRIER a un ctx
      │  (efecto: encola/parametriza la config de portadora)
      ▼  (aguas abajo, en el commit/activate FTM-LTE)
rflte_mc_carrier_activate (@0xce6e72c0) / rflte_ftm_mc_wakeup (@0xce6e08a8)  [módulo FTM-LTE, seg27]
      │  completa el encendido de la portadora (usa BAND+EARFCN: assert @0xce8bdc10)
      ▼  (setea gp+0x740==2 y crea el ctx que puebla gp+0x7000)
carrier-activate handler 0xd81bd018  →  G-mode(0xd81bd160) OK  →  G-ctx(0xd8286240) OK
      ▼
APPLY 0xd8273b2c → SETTER 0xd8279264 :  memw(0xca79c494) = 0xca6e3b88
```
- El puntero almacenado (`0xca6e3b88`) lo devuelve `0xd81bd7ec` (`r0 = ##0xca6e3b88`, constante). Es el
  **ctx de carrier** en .bss (region @0xca6e3xxx, cerca de `0xca6e3200`/`0xca6e3228` que el executor de
  measure lee). **FACT.**
- El setter, además del store principal, escribe por-command_id en `0xca79a8e0[id]+0x24`/`+0x10` con
  gates `id≤0x31` y `r1≤5` (0xd827928c..0xd82792e4): registra el carrier activo por-comando. **FACT.**

### 4.3 TLVs mínimos REALES que hacen correr el activate (INFERENCE fuerte, gates FACT)
Del unpacker (gates FACT) + los asserts del activate (band_get_band_from_dl_earfcn @0xce8bdc10,
current_dl_to_ul_map @0xce6dfee8):
```
RX_CARRIER (fid 1)   = 0     ; fija el índice de carrier PRIMERO (evita default inválido)
TECH_MODE  (fid 25)  = 1     ; LTE
BAND       (fid 5)   = <band>; OBLIGATORIO para el activate
CHANNEL    (fid 6)   = <EARFCN>  (ó CENTER_FREQ fid 12 en Hz) ; OBLIGATORIO (deriva la banda)
BANDWIDTH  (fid 7)   = <enum>
(RFM_DEVICE fid 3     = <dev>  ; recomendado para seleccionar la cadena RF)
```
**Sin BAND+EARFCN el activate NO corre, `gp+0x7000`/`gp+0x740` no se completan, y `0xca79c494` sigue
NULL.** Ésta es la razón mecánica de que "RADIO_CONFIG con BAND+EARFCN" sea el que destraba el IQ.
Orden: RX_CARRIER → TECH_MODE → BAND → CHANNEL/CENTER_FREQ → BANDWIDTH.

---

## 5. LOS PARAMS @0xf/0x10/0x11 DEL WIRE — QUÉ SON Y VALORES

### 5.1 Lectura exacta (handler canónico 0xd86fd0f4) — FACT
```
d86fd108: r17 = memub(req+0x0a)        ; command_id (índice de tabla, ≤0x31)
d86fd120: r20 = memub(req+0x11)        ; byte 17
d86fd12c: r19 = memub(req+0x0f)        ; byte 15
d86fd134: r18 = memub(req+0x10)        ; byte 16
d86fd138: r18 |= asl(r20,#0x8)         ; r18 = byte16 | (byte17<<8)
d86fd148: r19 |= asl(r18,#0x10)        ; r19 = byte15 | (byte16<<16) | (byte17<<24)... (24-bit packed)
d86fd158: call 0xd8272cd8 ; r2 = r19   ; pasa el packed al dispatcher measure/capture
```
→ Los bytes **0x0f, 0x10, 0x11** forman un **campo empaquetado de 24 bits** (little-endian) que se
pasa como 2º parámetro (`r2`) al dispatcher `0xd8272cd8`. **FACT.**

### 5.2 Semántica (FACT del dispatcher 0xd8272cd8)
```
d8272d08: p1 = cmp.eq(r18,#0x3)        ; r18 = low del packed (el "mode/type")
d8272d0c: p0 = cmp.eq(r18,#0x5)
d8272d44: if (p0)  r5 = ##0xd826d3e4   ; mode==5 → decode-A (measure/CAPTURA con timing 0x51eb851f)
d8272d4c: if (!p0) r5 = ##0xd8292298   ; else    → decode-B (measure non-capture)
d8272d50: callr r5
```
- **byte 0x0f (15)** = **mode/type de la operación** (selector de sub-acción). Valores observados:
  `3` y `5` (5 = capture/measure con timing → decode-A `0xd826d3e4`, el que llama al getter y **puede
  reventar** si `0xca79c494==NULL`). **FACT de los valores 3/5.**
- **bytes 0x10/0x11 (16/17)** = **parámetro de 16 bits** asociado al mode (p.ej. sub-índice/handle de
  captura). Se combinan en el packed de 24-bit junto con el mode. **FACT del empaquetado; INFERENCE de
  la semántica exacta del sub-parámetro.**
- **byte 0x0a (10)** = **command_id** (el que resuelve el unpacker). **FACT.**

> Nota: el pre-parser `0xd8272c10` copia sólo los **8 bytes de cabecera** (wire[0..7]) al descriptor;
> los bytes 0x0a/0x0f/0x10/0x11 se leen del **buffer crudo** del request (`r18` = raw wire ptr que
> devuelve 0xd86fd0e8), no del descriptor parseado. Por eso están "más allá" de la cabecera copiada.
> **FACT (wire_offset_B §).**

### 5.3 Valores válidos (resumen)
| offset | campo | valores | fuente |
|--------|-------|---------|--------|
| 0x0a | command_id | 0..0x31 (el del comando a ejecutar; bound `cmp.gtu #0x31`) | FACT |
| 0x0f | mode/type | 3, 5 (5 = capture/measure decode-A) | FACT (3/5) |
| 0x10 | param lo | 0..0xff (byte bajo del sub-param 16-bit) | FACT layout |
| 0x11 | param hi | 0..0xff (byte alto) | FACT layout |

---

## 6. FACT / INFERENCE / UNKNOWN — con VAs

**FACT (verificado en clade_dec_full.bin / b21 / b23 / seg27_dec.bin):**
- Master registrar 0xd84aa03c → module-inits 0xd8182640 (RF-TEST, group 0x12), 0xd81793b0 (0x13),
  0xd86c3ee8 (0x0b).
- Module 0 (0xd8182640): ctx @0xca65d640, 12-byte slots; 11 comandos slots 0..11 (§1.2), nombres F3
  byte-exact.
- Wire dos-ejes: sub_command→jump-tbl @0xc37c649c (disp 0xd82714b4); command_id byte10→resolver
  0xd8272684 (@0xca79a850). Params 0x0f/0x10/0x11 = packed 24-bit (mode+param) a 0xd8272cd8 (§5).
- SM: session->0xc enum (0=init,2=activo,...); init 0xd81df874 deja 0; gate enter-writer 0xd81e5d20
  (==2); flag @0xca7897b0[tech] escrito =1 @0xd81e5d54 / =0 @0xd81e5fc4; gate action 0xd8202228 (==1);
  commit per-tech 0xd81dfecc indexa @0xca733d10.
- **SSR:** getter 0xd827923c lee memw(0xca79c494); NULL → call 0xd80d8998 → jump 0xc0d60074
  (err_fatal). Setter 0xd8279264 (único writer, memw(0xca79c494)=r0) llamado sólo desde APPLY
  0xd8273b2c (0xd8273b40), llamado sólo desde 0xd81bd018 (0xd81bd2a8), registrado por init 0xd81e52c8
  (0xd81e53dc). Valor almacenado = 0xca6e3b88 (de 0xd81bd7ec).
- Gates dentro de 0xd81bd018: G-mode 0xd81bd160 (memb(gp+0x740)==2); G-ctx 0xd8286240
  (memw(gp+0x7000)!=0, vía 0xd82836c0); APPLY sólo si ambos OK (0xd81bd290 if r0==1).
- reach.py (probado): 0x1002→getter(LOAD); 0x1003→getter(LOAD); 0xd81bd018→setter(STORE);
  0xd81e52c8→setter(STORE); RADIO_CONFIG unpacker 0xd8183f30 → setter=NO getter=NO.
- RADIO_CONFIG unpacker 0xd8183f30: parser puro (pila), field-tbl @0xc37c0290, nombres @0xc906c630,
  bail limpio num_tlv=0 @0xd818486c.

**INFERENCE:**
- `session->0xc → 2` = side-effect del activate FTM (rflte_mc_carrier_activate/wakeup, cluster
  ce6exxxx), no de un store directo ni del enter_mode_cnf ML1.
- G-mode (gp+0x740==2) = flag global "RF cal/activo"; G-ctx (gp+0x7000) = instancia/ctx RF creada al
  arrancar RF cal. Se encienden con set_mode cal + activate de portadora (BAND+EARFCN).
- Los bytes 0x10/0x11 = sub-parámetro 16-bit del mode (handle/índice de captura).
- command_id absoluto de cada comando (base mod0 = B): probablemente RADIO_CONFIG=0, RX_MEASURE=1, …,
  COMMAND_CAPABILITY=0x0b si B=0 (primer módulo). No probado.

**UNKNOWN (sólo en vivo / RAM):**
- Valor exacto de gp+0x740 / gp+0x7000 en tu instante (estado RF); command_id absoluto por comando y
  el mapeo command_id↔sub_command 0x10xx (cerrar con COMMAND_CAPABILITY/CMD_MASK).
- Enumeración de módulos 1 (0x13) y 2 (0x0b) del espacio 0..0x31.
- El comando FTM exacto de "set RF cal mode" (paso 1 de §3.6) y si algún TLV de RADIO_CONFIG adicional
  es requerido por el activate en tu HW.
- Enum exacto de BANDWIDTH y semántica fina de los bytes 0x10/0x11.

---

## 7. REPRODUCIR
```bash
# --- SSR core ---
dis.sh 0xd827923c 0x30     # GETTER: memw(0xca79c494); NULL → 0xd80d8998 → err_fatal
dis.sh 0xd8279264 0xa0     # SETTER: memw(0xca79c494)=r0 (único writer)
dis.sh 0xd80d8998 0x10     # err_fatal (jump 0xc0d60074)
dis.sh 0xd8273b2c 0x60     # APPLY: 0xd81bd7ec (r0=0xca6e3b88) → SETTER
dis.sh 0xd81bd7ec 0x10     # devuelve r0 = ##0xca6e3b88 (ctx carrier)
dis.sh 0xd81bd018 0x2b0    # carrier-activate handler: G-mode 0xd81bd160, G-ctx 0xd81bd28c, APPLY 0xd81bd2a8
dis.sh 0xd8286240 0x30     # G-ctx: memw(gp+0x7000)!=0 ? return 1 : return 0
dis.sh 0xd81e53dc 0x10     # init 0xd81e52c8 llama 0xd81bd018 (registro runtime)
python3 find_refs.py 0xd8279264   # 1 caller: 0xd8273b40
python3 find_refs.py 0xd8273b2c   # 1 caller: 0xd81bd2a8
python3 find_refs.py 0xd81bd018   # 1 caller estático: 0xd81e53dc
python3 reach.py 0xd86fd0f4 -v    # 0x1002 → getter (SSR)
python3 reach.py 0xd81bd018 -v    # → setter (apply)
python3 reach.py 0xd8183f30       # RADIO_CONFIG unpacker: setter=NO getter=NO

# --- command table ---
dis.sh 0xd84aa03c 0x14            # master registrar (3 module-inits)
dis.sh 0xd8182640 0x2c0           # module 0: 11 slots (2 passes)
dis.sh 0xd86fd0f4 0x80            # handler canónico: command_id + params 0x0f/0x10/0x11
dis.sh 0xd8272cd8 0x60            # dispatcher measure/capture (mode==5 → 0xd826d3e4)

# --- F3 names (byte-exact) ---
python3 - <<'PY'
import struct
SEGS=[(0xc3553000,0xd315f4,'modem.b21'),(0xc8b6a000,0x5cdd97,'modem.b23')]
def rb(va,n):
  for b,s,f in SEGS:
    if b<=va<b+s: d=open('/tmp/modemre/'+f,'rb').read();o=va-b;return d[o:o+n]
  return b''
def cs(va):
  x=rb(va,160);i=x.find(b'\x00');return x[:i].decode('latin1','replace')
for a in (0xc3555b70,0xc3555bc0,0xc3555c10,0xc3561a10,0xc3555c30,0xc3555c70,
          0xc3555c00,0xc3555e20,0xc3555bd0,0xc3555e00,0xc3555b90):
  print(hex(a),'->',cs(struct.unpack('<4I',rb(a,16))[2]))
PY
```
