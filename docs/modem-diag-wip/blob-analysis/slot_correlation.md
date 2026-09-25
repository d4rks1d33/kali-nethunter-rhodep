# slot_correlation — Correlación slot 0x10xx → unpacker (COMMAND_CAPABILITY / RADIO_CONFIG / IQ_CAPTURE)
SM6375 / Moto G82 5G · MPSS.HI.4.3.4-00494-MANNAR_GEN_PACK · base 0xd8000000 (`clade_dec_full.bin`, `dis.sh`).
Disasm completo: `/tmp/_full_dis.txt`. Cruza con `gate_resolution.md`, `radio_config_unpack.md`, `cmd_capability_map.md`.

Leyenda: **FACT** = leído del disasm con VA · **INFERENCE** = deducción por patrón · **UNKNOWN** = sólo runtime/en vivo.

---

## 0. TL;DR — RESPUESTAS DIRECTAS

1. **El binding slot 0x10xx → unpacker específico (RADIO_CONFIG / COMMAND_CAPABILITY / IQ_CAPTURE) es
   100% RUNTIME (callr), NO estático. PROBADO por 3 vías independientes (§1).** El unpacker NO se
   elige por el número de slot: se elige por `command_id = memub(req + 0xa)` que se lee del PAYLOAD
   del request y se resuelve contra la tabla runtime @0xca79a850. **FACT.**
   - `0xd81849ec` (COMMAND_CAPABILITY): **0 referencias estáticas** en toda la imagen (sólo su prólogo).
   - `0xd8189818` (IQ_CAPTURE disp): **0 referencias estáticas** (sólo su prólogo + jumps internos).
   - `0xd8183f30` (RADIO_CONFIG): **1 call estática** desde su propio wrapper `0xd81833d0`
     (cadena `0xd8182ec0/0xd818327c`), que a su vez sólo se referencia como INMEDIATO guardado en
     el descriptor de registro (`0xd8182e90 immext #0xd8182ec0`) — es decir, se instala en la tabla
     runtime, no se llama desde ningún slot. **FACT (grep §1.1).**

2. Por tanto **el sub_command 0x10xx exacto de cada uno es UNKNOWN estático.** Se cierran en vivo con
   COMMAND_CAPABILITY (barrido §5). **La mejor INFERENCE por el patrón de handler está en §3.**

3. **Slots SEGUROS para barrer con num_tlv=0 (no crashean): §4.**
   - **SEGUROS (query/read, num_tlv=0 → REPACK/bail limpio):** `0x1004, 0x1005, 0x100a, 0x1007,
     0x1008, 0x1009, 0x1011, 0x1012, 0x1014, 0x1015, 0x1017`.
   - **PELIGROSOS (pueden crashear / tocan estado RF / measure-capture): `0x1002, 0x1003` (FACT
     crashean), `0x1006, 0x1010` (init de estado RF), `0x100b, 0x100c, 0x100d, 0x100e, 0x1016`
     (config/measure — escriben estado). **NO barrer con num_tlv=0 sin TLV de carrier.**

---

## 1. PRUEBA DE BINDING RUNTIME (callr) — no hay ruta estática slot→unpacker  [FACT]

### 1.1 Grep de referencias a los 3 unpackers en toda la imagen (`/tmp/_full_dis.txt`)
```
d81849ec  (COMMAND_CAPABILITY)  -> 0 refs (sólo prólogo)                     FACT
d8189818  (IQ_CAPTURE disp)      -> 0 refs (sólo prólogo + jumps internos)    FACT
d8183f30  (RADIO_CONFIG loop)    -> 1 ref:  d81833d0: call 0xd8183f30         FACT
d8182ec0  (RADIO_CONFIG wrapper) -> 1 ref:  d8182e90: immext #0xd8182ec0      FACT  (se GUARDA en descriptor)
d818327c  (RADIO_CONFIG entry)   -> 1 ref:  d8182f24: jump 0xd818327c         FACT  (cadena interna)
```
→ Ningún slot-handler 0x10xx contiene un `call/immext` a estos unpackers. El único camino es la
resolución por command_id.

### 1.2 Envelope común de TODOS los handlers 0x10xx  [FACT]
Cada handler (0xd86fxxxx / 0xd8271xxx) hace exactamente:
```
call 0xd86fd0e8            ; parse header del request -> r0=desc parseado, r1=raw pkt
                           ;   (0xd86fd0e8 -> 0xd8272c10 = wire-parser, copia bytes del TLV header)
r_cmd = memub(pkt + 0xa)   ; <<< COMMAND_ID (byte del payload)   [FACT: 18/21 handlers usan +0xa]
p0 = cmp.gtu(r_cmd, #0x31) ; bound-check command_id <= 0x31      [FACT: 0xd86fd10c y clones]
if (p0) jump <error>
... decode específico del slot ...
```
Offsets de command_id por handler (FACT, grep `memub` del prólogo):
```
+0xa : 0x1002,0x1003,0x1004,0x1005,0x1007,0x100a,0x100b,0x100c,0x100d,0x100e,0x100f,
       0x1011,0x1012,0x1014,0x1016  (mayoría)
+0xb : 0x1008 (0xd86fd5c8), 0x1017 (0xd86fe7e0)
+0xc : 0x1006 (0xd8271c94), 0x1010 (0xd82722c4)   (variante compartida con 0x30xx)
+0x19: 0x1009 (0xd86fd80c)   (además de +0xa)
```
→ El comando REAL (qué unpacker corre) lo fija ese byte del payload, resuelto por:
```
0xd8272684: p0=cmph.gtu(id,#0x31); base=memw(##0xca79a850); struct=memw(base+id*4+0x34); return struct
```
El struct-comando (0x28 bytes) contiene los punteros unpack/repack, instalados en runtime por el
registrador (`0xd8182640` para RADIO_CONFIG, análogos para CC/IQ). **FACT (cmd_capability_map.md §1).**

### 1.3 Ruta measure/capture (callr r5) — el crash de 0x1002/0x1003  [FACT]
Handler 0x1002 (0xd86fd0f4) → `0xd86fd158: call 0xd8272cd8`. En 0xd8272cd8:
```
r18 = mode ; call 0xd827dfc8 ; call 0xd827e064
if (mode==5) ... ; r5 = mode? 0xd826d3e4 : 0xd8292298 ; callr r5     [FACT 0xd8272d50]
```
`0xd826d3e4`/`0xd8292298` (gemelos: measure vs non-measure) dentro llaman:
```
d826d4bc: call 0xd8273c84         ; gate de estado (valida tech/carrier)   [FACT]
d826d514: callr r2 (=memw(r29+0x18)); r2 = callback repack pasado por el slot  [FACT]
```
Con num_tlv=0 no se fija carrier/agc y este path deref-ea punteros no inicializados → SSR.
**FACT del path; INFERENCE del deref exacto (coincide con radio_config_unpack.md §6).**

---

## 2. RESPUESTAS 1/2/3 — sub_command @0x04 de cada unpacker

Porque el binding es runtime (§1), el número estático NO existe en el binario. Doy VA del unpacker
(FACT) + la INFERENCE del slot + cómo cerrarlo.

### 2.1 COMMAND_CAPABILITY
- **Unpacker: `0xd81849ec`** (FACT — única fn que usa la name-table grupo-22 @0xc906ce18; lee
  desc+0x12, desc+0x16, num_tlv desc+0x1a; permite num_tlv=0). **field-tbl @0xc37c03b4.**
- **sub_command @0x04 = UNKNOWN estático.** **INFERENCE: ∈ {0x1004, 0x1005, 0x100a}** — son los
  handlers puros-query que sólo llaman `0xd827dfc8` y hacen REPACK con num_tlv=0 (§3), el patrón
  exacto de COMMAND_CAPABILITY (request vacío → REPACK de defaults + CMD_MASK). Por orden de
  declaración F3 (RADIO_CONFIG, **COMMAND_CAPABILITY**, RX_MEASURE…) es de los primeros query-slots.
  **Candidato más fuerte: 0x1004.**
- **Cierre (FACT del mecanismo):** barrer 0x1004/0x1005/0x100a con `{field1 QUERY_COMMAND=0xFFFFFFFF}`
  tras TECH_ENTER; el que devuelva REPACK con field 3 CMD_MASK != 0 es COMMAND_CAPABILITY (§5).

### 2.2 RADIO_CONFIG
- **Unpacker: `0xd8183f30`** (wrappers `0xd818327c` / `0xd8182ec0`) (FACT — usa field-handlers
  @0xc37c0290 y nombres @0xc906c630; init/registro 0xd8182640). **field-tbl @0xc37c0268.**
- **sub_command @0x04 = UNKNOWN estático · NO 0x1002 (INFERENCE, FACT-backed).** RADIO_CONFIG hace
  bail LIMPIO con num_tlv=0 (0xd818486c → status 0x14, sin SSR); 0x1002 CRASHEA → 0x1002 ≠ RADIO_CONFIG.
- **INFERENCE del slot: ∈ {0x1003, 0x1007, 0x1016}** — handlers que combinan `0xd8272684`
  (resolver command_id → field-table, requerido para el dispatch RADIO_CONFIG) con decode de valores.
  0x1003 lo hace (`0xd8272684 + 0xd827e064`, y crashea con num_tlv=0 lo que es consistente con un
  tune que exige TLVs), 0x1007 (`0xd8272684 + 0xd827dfc8`) y 0x1016 (`0xd827dfc8 + 0xd827e064`).
  **Candidato más fuerte: 0x1003** (usa 0xd8272684 = resolución de field-table, y su crash con
  num_tlv=0 encaja con “RADIO_CONFIG necesita TECH_ENTER + ≥1 TLV”). El bail limpio del unpacker
  (0xd818486c) sólo se alcanza si el path llegó al loop TLV; si el handler deref-ea antes (measure)
  crashea igual. INFERENCE media-alta.
- **Cierre:** tras TECH_ENTER LTE, barrer con `{field25 TECH_MODE=1, field5 BAND, field6 CHANNEL}`;
  el slot que emite el F3 `[FTM.RFTEST][RADIO_CONFIG][UNPACK]` es RADIO_CONFIG (radio_config_unpack.md §8).

### 2.3 IQ_CAPTURE
- **Unpacker (dispatcher): `0xd8189818`** (FACT — única fn que usa la field-table @0xc37c0828;
  0 referencias estáticas → dispatch runtime puro). **field-tbl @0xc37c0828, fmt @0xc37c08f4.**
- **sub_command @0x04 = UNKNOWN estático.** **INFERENCE: 0x1002** (o 0x1008). Razón (FACT-backed):
  - 0x1002 y 0x1008 son los ÚNICOS que pasan el callback genérico `0xd8263324` y enrutan por
    `0xd8272cd8` → callr `0xd826d3e4/0xd8292298` (path **measure/capture** con timing 0x51eb851f).
  - 0x1002 **CRASHEA con num_tlv=0** deref-eando un buffer/memshare no inicializado — firma exacta
    de una CAPTURA que espera buffer DDR de resultados. IQ_CAPTURE (field-table @0xc37c0828) es el
    comando de captura por excelencia.
  - **Candidato más fuerte para IQ_CAPTURE: 0x1002.** 0x1008 (mismo cb 0xd8263324, pero query-safe
    con num_tlv=0 → llama 0xd827dfc8 x2 y hace REPACK) es más probablemente RX_MEASURE/TX_MEASURE.
- **Cierre:** IQ_CAPTURE requiere secuencia (ver iq_sequence.md / iq_final_values.md): TECH_ENTER →
  RADIO_CONFIG(tune) → IQ_CAPTURE con selector de captura. NO barrer 0x1002 con num_tlv=0 (SSR).

> NOTA: field-table @0xc37c0828 (IQ_CAPTURE) referenciada SÓLO en 0xd8189818 (FACT grep §6). Confirma
> que ningún slot la llama por inmediato; el enlace es por command_id runtime.

---

## 3. INFERENCIA POR PATRÓN DE HANDLER — decode-fn → semántica  [FACT de las fns + INFERENCE clase]

Decode functions comunes decodificadas (VA = FACT):

| decode fn   | qué hace (FACT)                                                        | clase (INFERENCE) |
|-------------|-----------------------------------------------------------------------|-------------------|
| 0xd827dfc8  | param-validate + status-string (r0==0 → log err; devuelve 0/1/2). **NO** toca RF | helper de status → **query/read seguro** |
| 0xd827e064  | valida ptr, memcpy 0x16 bytes a buffer (0xd8069ec8). Copia payload    | copia de campo → benigno |
| 0xd8272c10  | wire-parser: copia el TLV-header del request a un descriptor (memb)   | parser → benigno |
| 0xd8272684  | resolver command_id → struct-comando @0xca79a850 (memw base+id*4+0x34)| resolución field-table → precede dispatch real |
| 0xd8272cd8  | measure/capture dispatcher → callr 0xd826d3e4/0xd8292298 (timing)     | **measure/capture → PELIGROSO con num_tlv=0** |
| 0xd829c008  | call 0xd8391e88; **escribe estado en +0xe0** (aloca/instala contexto) | init estado RF → **ACTION** |
| 0xd829c404  | call 0xd8391e88; **escribe estado en +0xdc**                          | init estado RF → **ACTION** |
| 0xd828054c  | 0xd8279154 (state getter id≤0x31) + check memb+0x33                   | **query de estado (seguro)** |
| 0xd82805f0  | 0xd8279154 + jump 0xd827ffe0 (light)                                  | **query/dispatch (seguro)** |
| 0xd8263f98  | 0xd81bf0e8 (state getter @0xca6e3ab8) + gate; ruta de config          | config-read → semi (ACTION si escribe) |
| 0xd8272bb0  | copia tabla estática de propiedades @0xc37c64fc a struct             | **capability/property read (seguro)** |
| 0xd8279154  | getter: memw(id<<2 + @0xca79a8e0) (estado por-comando)                | getter puro (seguro) |
| 0xd81bf0e8  | getter: memw(id<<2 + @0xca6e3ab8) (estado por-comando)               | getter puro (seguro) |
| 0xd82735e8  | minu(r0,#0x14) clamp + subrutina                                     | clamp/normalize (benigno) |

**Conclusión de clase por slot (INFERENCE, base FACT de las decode-fns):**
- `0xd827dfc8`-solo = **query/read** (devuelve REPACK, num_tlv=0 seguro).
- `0xd8272684` presente = resuelve field-table → puede llegar a un unpacker con estado (RADIO_CONFIG/
  measure); **más riesgoso** si además entra a 0xd8272cd8.
- `0xd8272cd8`/callback 0xd8263324 = **measure/capture** → **PELIGROSO** con num_tlv=0.
- `0xd829c008`/`0xd829c404` = **escriben estado RF** → **ACTION** (idempotente-ish pero muta estado).
- `0xd8272bb0`/`0xd8279154`/`0xd81bf0e8` = **getters de capability/estado** → **seguros**.

---

## 4. CLASIFICACIÓN FINAL POR SLOT — SEGURO vs PELIGROSO (num_tlv=0)

| sub    | slot | handler VA  | decode(s)                        | clase                       | num_tlv=0 |
|--------|------|-------------|----------------------------------|-----------------------------|-----------|
| 0x1002 | 2    | 0xd86fd0f4  | 0xd8272684 + 0xd8272cd8 (callr)  | **measure/capture (IQ?)**   | ❌ CRASHEA (FACT) |
| 0x1003 | 3    | 0xd86fd230  | 0xd8272684 + 0xd827e064          | tune/config (RADIO_CFG?)    | ❌ CRASHEA (FACT) |
| 0x1004 | 4    | 0xd86fd328  | 0xd827dfc8                       | **query/read**              | ✅ SEGURO |
| 0x1005 | 5    | 0xd86fd474  | 0xd827dfc8                       | **query/read**              | ✅ SEGURO |
| 0x1006 | 6    | 0xd8271c94  | 0xd829c008                       | init estado RF (ACTION)     | ⚠️ evitar (muta estado) |
| 0x1007 | 7    | 0xd86fdb88  | 0xd8272684 + 0xd827dfc8          | query c/ resolución         | ✅ prob. seguro |
| 0x1008 | 8    | 0xd86fd5c8  | 0xd827dfc8 (x2)                  | **query/read** (RX_MEASURE?)| ✅ SEGURO |
| 0x1009 | 9    | 0xd86fd80c  | 0xd86fd0e8                       | query (sólo parse+REPACK)   | ✅ SEGURO |
| 0x100a | 10   | 0xd86fda68  | 0xd827dfc8                       | **query/read**              | ✅ SEGURO |
| 0x100b | 11   | 0xd86fe120  | 0xd8272c10+0xd8264e4c+0xd82805f0 | config (escribe)            | ⚠️ evitar |
| 0x100c | 12   | 0xd86fe1e4  | 0xd8272c10 + 0xd8264f08          | config (escribe)            | ⚠️ evitar |
| 0x100d | 13   | 0xd86fde80  | 0xd8272684+0xd82735e8+0xd828067c | config/list (escribe)       | ⚠️ evitar |
| 0x100e | 14   | 0xd86fe0a4  | 0xd8263f98                       | config-read (0xd81bf0e8)    | ⚠️ semi |
| 0x100f | 15   | 0xd86fdf94  | bail temprano                    | reservado                   | ✅ (bail) |
| 0x1010 | 16   | 0xd82722c4  | 0xd829c404                       | init estado RF (ACTION)     | ⚠️ evitar |
| 0x1011 | 17   | 0xd8271b64  | 0xd828054c                       | **query de estado**         | ✅ SEGURO |
| 0x1012 | 18   | 0xd8271bcc  | 0xd82805f0                       | **query/dispatch**          | ✅ SEGURO |
| 0x1014 | 20   | 0xd86fe300  | 0xd8272bb0 + 0xd84b5184          | **capability read**         | ✅ prob. seguro |
| 0x1015 | 21   | 0xd86fe558  | 0xd8272bb0 + 0xd81bf0e8          | **capability/state read**   | ✅ prob. seguro |
| 0x1016 | 22   | 0xd86fe60c  | 0xd827dfc8 + 0xd827e064          | decode+copia                | ⚠️ semi (0xd8263324 cb) |
| 0x1017 | 23   | 0xd86fe7e0  | 0xd8263f98                       | config-read (0xd81bf0e8)    | ✅ prob. seguro |

**LISTA DE SLOTS SEGUROS PARA BARRER CON num_tlv=0 (no crashean):**
```
0x1004, 0x1005, 0x1007, 0x1008, 0x1009, 0x100a, 0x100f, 0x1011, 0x1012, 0x1014, 0x1015, 0x1017
```
**NO barrer con num_tlv=0 (crash o muta estado):** `0x1002, 0x1003` (CRASHEA-FACT), `0x1006, 0x1010`
(init estado RF), `0x100b, 0x100c, 0x100d, 0x100e, 0x1016` (config write / semi).

> Precaución: TODOS los slots requieren TECH_ENTER previo (gate tech-state 0xd81e5d60 → status 0x14
> si no). Un 0x14 en el barrido NO es crash: es "tech no entrada". El barrido seguro real es:
> TECH_ENTER LTE → probar cada slot seguro con num_tlv=0 → leer status/REPACK. **FACT del gate.**

---

## 5. CIERRE EN VIVO (definitivo) — barrido COMMAND_CAPABILITY  [FACT del mecanismo]

```
PASO 0 (obligatorio): TECH_ENTER LTE
  4B 0B 27 00 0D 00 03 00 01 00 04 00 00 00 00 00 02 00 04 00 01 00 00 00 03 00 04 00 00 00 00 00

PASO 1: identificar COMMAND_CAPABILITY (barrer sólo slots SEGUROS de §4)
  Por cada SUB en {1004,1005,1007,1008,1009,100a,1011,1012,1014,1015,1017}:
    4B 0B 27 00 <SUB:u16LE> 01 00  01 00 04 00 FF FF FF FF      ; QUERY_COMMAND=0xFFFFFFFF
  El que devuelva REPACK con field 3 CMD_MASK != 0  =>  COMMAND_CAPABILITY.  (su @0x04 queda FIJADO)

PASO 2: leer CMD_MASK -> enum de comandos soportados (bit N = command_id N).
PASO 3: para RADIO_CONFIG: tras TECH_ENTER, probar {f25 TECH_MODE=1, f5 BAND, f6 CHANNEL} en
        {1003,1007,1016}; el que emite F3 [RADIO_CONFIG][UNPACK] es RADIO_CONFIG (@0x04 fijado).
PASO 4: para IQ_CAPTURE: seguir iq_sequence.md; el slot measure/capture (0x1002, cb 0xd8263324) es
        el candidato — NUNCA con num_tlv=0.
```

---

## 6. FACT / INFERENCE / UNKNOWN — cierre con VAs

**FACT:**
- Binding slot→unpacker = RUNTIME (callr). `command_id = memub(req+0xa)` (mayoría; +0xb/+0xc en
  0x1008/0x1017/0x1006/0x1010; +0x19 extra en 0x1009). Bound-check ≤0x31 (0xd86fd10c y clones).
- Resolver: 0xd8272684 → `memw(memw(##0xca79a850) + id*4 + 0x34)`.
- Unpackers: COMMAND_CAPABILITY=**0xd81849ec** (0 refs), IQ_CAPTURE disp=**0xd8189818** (0 refs,
  field-tbl @0xc37c0828), RADIO_CONFIG=**0xd8183f30** (wrapper 0xd818327c/0xd8182ec0, field-tbl
  @0xc37c0290/@0xc37c0268; 1 call desde 0xd81833d0; se INSTALA por inmediato 0xd8182e90).
- Path measure/capture (crash 0x1002/0x1003): 0xd86fd158→0xd8272cd8→callr r5 (0xd826d3e4 measure /
  0xd8292298 non-measure) @0xd8272d50; timing magic 0x51eb851f; gate 0xd8273c84; callr repack 0xd826d514.
- Clases de decode-fn: §3 (todas con VA verificada).
- Gate tech-state 0xd81e5d60 (==0x7 → status 0x14) precede a TODO slot 0x10xx.

**INFERENCE (con evidencia, a cerrar en vivo):**
- COMMAND_CAPABILITY @0x04 ∈ {0x1004, 0x1005, 0x100a} — **más fuerte 0x1004** (query puro
  0xd827dfc8, REPACK con num_tlv=0, primero por orden F3).
- RADIO_CONFIG @0x04 ∈ {0x1003, 0x1007, 0x1016} — **más fuerte 0x1003** (usa 0xd8272684 +
  crashea con num_tlv=0 = necesita TLVs, consistente con tune). NO 0x1002.
- IQ_CAPTURE @0x04 = **0x1002** (o 0x1008) — measure/capture (cb 0xd8263324, path 0xd8272cd8,
  crash por buffer no init con num_tlv=0). 0x1008 más probable RX/TX_MEASURE.
- Slots seguros para barrer: §4 lista.

**UNKNOWN (sólo runtime/en vivo):**
- Número EXACTO sub_command 0x10xx de cada unpacker (tabla @0xca79a850 poblada en runtime; los
  unpackers no se referencian por inmediato). Cerrar con §5 (CMD_MASK).
- Mapeo command_id(0..0x31) ↔ sub_command 0x10xx (se resuelve leyendo el descriptor en runtime).

---

## 7. REPRODUCIR
```
grep -n "0xd81849ec\|0xd8189818\|0xd8183f30\|0xd8182ec0\|0xd818327c" /tmp/_full_dis.txt   # refs (§1.1)
dis.sh 0xd86fd0e8 0x40     # envelope: parse header -> 0xd8272c10 wire-parser
dis.sh 0xd86fd0f4 0x40     # handler 0x1002: memub(req+0xa)=command_id, bound<=0x31
dis.sh 0xd8272684 0x40     # resolver command_id -> struct @0xca79a850
dis.sh 0xd8272cd8 0x80     # measure/capture dispatcher -> callr 0xd826d3e4/0xd8292298 (0x51eb851f)
dis.sh 0xd826d3e4 0x140    # measure executor: 0xd8273c84 gate + callr repack 0xd826d514
dis.sh 0xd829c008 0x40     # decode 0x1006: escribe estado RF +0xe0 (ACTION)
dis.sh 0xd8272bb0 0x40     # decode 0x1014/0x1015: copia props @0xc37c64fc (capability read)
dis.sh 0xd8279154 0x30     # getter estado @0xca79a8e0 (query 0x1011/0x1012)
dis.sh 0xd81bf0e8 0x20     # getter estado @0xca6e3ab8 (query 0x100e/0x1017/0x1015)
```
