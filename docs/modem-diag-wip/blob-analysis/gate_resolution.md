# GATE RESOLUTION — ¿el gate 0xd8202224 aplica a RADIO_CONFIG? Mapa 0x10xx, crashes, y camino real
Build: **MPSS.HI.4.3.4-00494-MANNAR_GEN_PACK-1.24452.133** (SM6375 / Moto G82 5G)
Imagen: `/tmp/modemre/clade_dec_full.bin` (VA base 0xd8000000, decompresada). Disasm: `dis.sh <va> <len>` (llvm-objdump hexagon v66).
Rodata estática: `modem.b21` @0xc3553000 (`rd.py`). Callers: `find_callers.py` (J2_call PC-rel, validado contra calls conocidos).

Leyenda: **FACT** = verificado byte/instrucción (VA citada) · **INFERENCE** = deducción con base dura · **UNKNOWN** = sólo en vivo / RAM.

---

## 0. TL;DR — RESOLUCIÓN DE LA CONTRADICCIÓN

La contradicción (RADIO_CONFIG detrás del gate que RADIO_CONFIG debe abrir) **es falsa: se apoyaba
en fusionar dos mecanismos que en el binario están en caminos DISTINTOS y disjuntos.** Verificado
por reachability estática (BFS de call-graph, cientos de funciones):

1. **El gate `0xd8202224` (`memb(tech<<3+0xca7897b0)==1`) NO está en el path de los comandos DIAG
   RFTEST 0x10xx.** Vive en una función de **EJECUCIÓN diferida de acción RF** (`0xd8201d3c`), que
   **NINGÚN** dispatcher DIAG alcanza estáticamente (0x10xx/0x20xx/0x30xx/0x70xx/RFDEBUG). **FACT.**
2. **La ruta de RADIO_CONFIG (unpacker `0xd8183f30`) NO toca NI el gate `0xd8202224`, NI el
   post-check `0xd81e5d60` (==0x7), NI el enter-writer `0xd81e5cec`.** BFS desde `0xd8183f30`:
   147 funciones, 0 llegan a esos gates. **FACT.**
3. Por lo tanto: **RADIO_CONFIG NO está gated por 0xd8202224.** El "0x14" que devuelven 0x1000/0x1001
   y (potencialmente) RADIO_CONFIG **no proviene de ese gate**, sino de:
   (a) slots inválidos/no-registrados de la jump-table 0x10xx (0x1000 default, 0x1001 error), o
   (b) el resolver de field-table `0xd8272684` devolviendo 0 (comando aún no registrado en RAM
   `@0xca79a850`) → status de error → 0x14.
4. **Los dos "gates" de los reportes A/B son de subsistemas separados:**
   - `0xd8202224` (`==1`): pertenece al **executor de acción RF diferida** (medición/tune real),
     corre en tarea RF, no en el hilo DIAG. Es el que exige tech "entered".
   - `0xd81e5d60` (`==0x7`) + enter-writer `0xd81e5cec` (`session->0xc==2` → escribe `=1`):
     pertenecen al **path TECH_ENTER per-tech** (RFDEBUG sub 0x0D), invocado por `callr` runtime.
   - `rflte_mc_carrier_activate` (report B): es el **path FTM-LTE** (`ftm_lte_*`), otro módulo.
   Los tres son cajas separadas; el DIAG-unpack de RADIO_CONFIG no entra en ninguna.
5. **Consecuencia práctica:** la circularidad no existe. RADIO_CONFIG se puede mandar sin abrir
   `0xd8202224`. Lo que fallaba en vivo tiene otra causa (§4/§5): (i) el sub_command 0x10NN real
   no estaba resuelto (barrías 0x1002/0x1003 que son otros comandos que **crashean** por deref con
   num_tlv=0), y (ii) el estado de sesión/registro de comandos en RAM.

---

## 1. Q1 — ¿EL GATE 0xd8202224 APLICA A TODOS LOS SUB RFTEST, O A NINGUNO DEL PATH DIAG?

### 1.1 El gate exacto (reconfirmado) — FACT
```
d8202218:  r0 = memuh(r17+#0x0)
d820221c:  p0 = cmp.eq(r0,#0x0); if (p0) jump 0xd820222c
d8202220:  immext(#0xca789780)
d8202224:  r2 = memb(r21<<#0x3+##0xca7897b0)     ; LEE estado[tech]
d8202228:  if (!cmp.eq(r2,#0x1)) jump 0xd8202234 ; GATE: si estado[tech]!=1 -> rama alterna
```
El gate está **dentro de la función que empieza en `0xd8201d3c`** (prólogo
`call 0xd814e6ac ; allocframe(#0xa8)` @0xd8201d40; el byte `0xd8201d38` es en realidad el TAIL
`r0=#0x0; jump 0xc0913ad8` de la función previa `0xd8201b38`, NO el inicio). **FACT.**

### 1.2 El dispatcher DIAG RFTEST 0x10xx es `0xd82714b4` (NO 0xd8201b38/0xd8201d3c) — FACT
```
d82714b4:  call 0xd8829688 ; allocframe(#0x170)
d82714bc:  call 0xd8272bb0                       ; parsea el paquete DIAG -> struct (r16)
d82714c8:  r3 = memub(r16+#0x5) ; r2 = memub(r16+#0x4)   ; byte alto / bajo del sub_command
d82714cc:  p0 = cmp.eq(r3,#0x30) ... (0x30xx)
d82714e4:  p0 = cmpb.eq(r3,#0x20) ... (0x20xx)
d82714fc:  p0 = cmp.eq(r3,#0x10); if(!p0) jump 0xd827176c(error)   ; rama 0x10xx
d8271500:  p0 = cmp.gtu(r2,#0x17); if(p0) jump 0xd827176c(error)   ; gate low<=0x17
d8271508:  r2 = ##0xd86fcf84                                        ; handler DEFAULT (pre-carga)
d8271510:  r3 = memw(r2_old<<2 + ##0xc37c649c)                      ; r3 = tabla[slot]
d8271514:  jumpr r3                                                 ; -> stub
```
- La tabla `@0xc37c649c` (24 slots) → stub → `0xd8271630 { callr r2 }`. El handler
  (`0xd86fxxxx`) se ejecuta y retorna. **En NINGÚN punto de este flujo se llama a `0xd8201d3c`
  ni se lee `0xca7897b0`.** **FACT.**

### 1.3 Prueba dura de reachability (BFS de call-graph) — FACT
| Raíz (path)                         | ¿alcanza `0xd8201d3c` / `0xd8202224`? | Funcs escaneadas |
|-------------------------------------|----------------------------------------|------------------|
| Handlers 0x10xx (0xd826d3e4/0xd8292298/0xd82735cc/…) | **NO** | 90 |
| RADIO_CONFIG unpacker `0xd8183f30`  | **NO** (tampoco 0xd81e5d60/0xd81e5cec) | 147 |
| Wrapper RADIO_CONFIG `0xd8182ec0`/`0xd818327c` | **NO** | 65/72 |
| Handler slot2 `0xd86fd0f4` → `0xd8272cd8` | **NO** | 54/73 |
| Dispatcher 0x70xx exec `0xd82218d4`  | **NO** | 26 |
| Handler TECH_ENTER `0xd8174758`      | **NO** (a enter-writer; es `callr` runtime) | 65 |

→ **El gate `0xd8202224` NO protege ningún comando DIAG RFTEST.** Es un gate del **executor de
acción RF** (`0xd8201d3c` y su gemelo `0xd82029a4`), que corre en otro contexto (RF-task), disparado
por `callr` desde tablas de acción pobladas en RAM. **FACT (reachability) + INFERENCE (contexto RF-task).**

### 1.4 ¿Qué es `0xd8201d3c` (la función del gate)? — FACT/INFERENCE
- Sin caller estático ni puntero-dato en los 10 MB (`find_callers`/`xref` = 0 hits). Runtime-bound. **FACT.**
- Entra igual que `0xd82029a4`: ambas hacen `call 0xd8209834` (`memw(idx<<2 + 0xca78a5c0)`, idx<=0x29
  = **lookup de config por carrier**), leen arrays per-carrier (`memw(r19+…)` con stride 0x58/0x3c),
  llaman `0xd81ef068` (chequea modo `==0x8` vía `0xd84ab024`), y tras el gate llaman al executor
  `0xd8201b38` (via `0xd8202684`) que escribe resultados (`memh(r18+0x0)+=r2`). **FACT.**
- **INFERENCE fuerte:** `0xd8201d3c` es el **executor de acción RX_MEASURE/IQ_CAPTURE (medición/tune
  real)** que sí exige tech "entered" (`0xca7897b0[tech]==1`). El DIAG-unpack sólo encola la request;
  la acción diferida es lo gated.

**RESPUESTA Q1:** El gate `0xd8202224` **NO aplica a RADIO_CONFIG** (ni a ningún unpacker DIAG 0x10xx).
Sólo protege la **fase de acción/medición diferida** (`0xd8201d3c`), no la fase de config. RADIO_CONFIG
corre por un path (`0xd8183f30`) que **no pasa por `0xd8202224`**. **FACT.**

---

## 2. Q2 — MAPA stub_index → handler VA → comando, y sub_command real @0x04

### 2.1 Layout del paquete (reconfirmado) — FACT
```
@0x00 4B  DIAG_SUBSYS_CMD_F      @0x01 0B  subsys FTM
@0x02 u16 ftm_cmd = 0x0027 (LTE) @0x04 u16 sub_command   @0x06 u16 num_tlv   <TLVs>
```
Router por rango del sub_command (`0xd8157ec4`): 0x000–0xFFF=RFDEBUG; **0x1000–0x3FFF=RFTEST**
(`0xd82714b4`); 0x7000+=familia 0x70xx. Dentro de RFTEST, byte alto @0x05 elige tabla:
0x10→`@0xc37c649c`(24), 0x20→`@0xc37c645c`(16), 0x30→`@0xc37c6440`(7). **FACT** (0xd82714cc/e4/fc).

### 2.2 Los 24 stubs de `@0xc37c649c` (leídos de b21) → handler — FACT
El `sub_command` real @0x04 es **`0x1000 + stub_index`** (byte bajo = índice; en vivo lo confirmaste:
0x1000=stub0 … 0x1017=stub0x17). **FACT** (dispatcher usa `r2=memub(+4)` como índice directo, gate<=0x17).

| sub @0x04 | slot | stub VA     | handler VA          | naturaleza (FACT del disasm) |
|-----------|------|-------------|---------------------|------------------------------|
| **0x1000**| 0    | 0xd8271630  | (default `0xd86fcf84` precargado en r2) | slot "común": callr al handler default; bail (`cmp.eq(r2,#0x2a)`) → status err |
| **0x1001**| 1    | 0xd827176c  | ERROR "unsupported" | log F3 + retorno error → **0x14 limpio** |
| **0x1002**| 2    | 0xd8271548  | **0xd86fd0f4**      | → 0xd8272684 + **0xd8272cd8** (decode+action, `callr r5`) |
| **0x1003**| 3    | 0xd8271558  | **0xd86fd230**      | → 0xd8272684 + 0xd827e064 (decode) |
| 0x1004    | 4    | 0xd8271568  | 0xd86fd328          | → 0xd827dfc8 (decode) |
| 0x1005    | 5    | 0xd8271578  | 0xd86fd474          | → 0xd827dfc8 |
| 0x1006    | 6    | 0xd827170c  | 0xd8271c94          | → 0xd829c008 |
| 0x1007    | 7    | 0xd82715b0  | 0xd86fdb88          | → 0xd8272684 + 0xd827dfc8 |
| 0x1008    | 8    | 0xd8271588  | 0xd86fd5c8          | → 0xd827dfc8 (x2) |
| 0x1009    | 9    | 0xd8271598  | 0xd86fd80c          | → 0xd86fd0e8 |
| 0x100a    | 10   | 0xd82715a4  | 0xd86fda68          | → 0xd827dfc8 |
| 0x100b    | 11   | 0xd82715f8  | 0xd86fe120          | → 0xd8272c10 + 0xd8264e4c + 0xd82805f0 |
| 0x100c    | 12   | 0xd8271604  | 0xd86fe1e4          | → 0xd8272c10 + 0xd8264f08 |
| 0x100d    | 13   | 0xd8271610  | 0xd86fde80          | → 0xd8272684 + 0xd82735e8 + 0xd828067c |
| 0x100e    | 14   | 0xd82715bc  | 0xd86fe0a4          | → 0xd8263f98 |
| 0x100f    | 15   | 0xd82715c8  | 0xd86fdf94          | → (bail temprano) |
| 0x1010    | 16   | 0xd8271718  | 0xd82722c4          | → 0xd829c404 (compartido 0x30xx) |
| 0x1011    | 17   | 0xd8271724  | 0xd8271b64          | → 0xd828054c (compartido 0x30xx) |
| 0x1012    | 18   | 0xd8271730  | 0xd8271bcc          | → 0xd82805f0 (compartido 0x30xx) |
| 0x1013    | 19   | 0xd82715d4  | ERROR (`0xd8271774`)| slot inválido → 0x14 |
| 0x1014    | 20   | 0xd82715e0  | 0xd86fe300          | → 0xd8272bb0 + 0xd84b5184 |
| 0x1015    | 21   | 0xd82715ec  | 0xd86fe558          | → 0xd8272bb0 + 0xd81bf0e8 |
| 0x1016    | 22   | 0xd827161c  | 0xd86fe60c          | → 0xd827dfc8 + 0xd827e064 |
| 0x1017    | 23   | 0xd8271628  | 0xd86fe7e0          | → 0xd8263f98 |
(handlers verificados por `r2 = ##<handler>` en cada stub. **FACT**.)

### 2.3 Correlación comando↔handler: honestidad sobre lo verificable

**FACT (unpackers correctos, corrigiendo reportes previos):**
Los VAs de unpacker de los reportes viejos (`0xd81828f0`, `0xd8184fe8`) caen **a mitad de instrucción**
(disasm = basura `<unknown>`/`jumpr r2`). Los **reales** (prólogo `call 0xd814e6ac ; allocframe`) son:
```
RADIO_CONFIG       unpacker = 0xd8183f30   (FACT: prólogo válido; usa nombres @0xc906c600, str @0xf808d800)
COMMAND_CAPABILITY unpacker = 0xd81849ec   (FACT: prólogo válido; QUERY_COMMAND/CMD_MASK)
```
→ **Los VAs del prompt (0xd8183f30, 0xd81849ec) son los correctos.** Los del `sub_command_map.md`
   (0xd81828f0/0xd8184fe8) estaban desalineados. **FACT.**

**UNKNOWN estático — el slot 0x10NN ↔ nombre exacto** (idéntica conclusión que 5 pases previos,
con la CAUSA precisa aquí verificada): el handler 0x10xx **no** llama al unpacker por inmediato.
Llama al resolver `0xd8272684`:
```
d8272684:  p0 = cmph.gtu(command_id,#0x31)              ; command_id = memub(request+0xa)
d8272694:  r3 = memw(##0xca79a850)                       ; tabla-maestra (RAM)
d8272698:  r3 = addasl(r3, command_id, #2)               ; base + cmd*4
d827269c:  r3 = memw(r3+#0x34)                            ; fld_tbl = *(slot+0x34)
d82726a0:  if (fld_tbl != 0) jump success else -> error, return 0
```
- El `command_id` (byte @0x0a del request parseado) **NO es el sub_command 0x10NN**: es un contador de
  registro asignado en runtime (init 0xd8182640/0xd850fxxx pobla `@0xca79a850`). El binding
  `sub_command 0x10NN → command_id → unpacker` se cierra **sólo en RAM**. **FACT del mecanismo.**
- Por eso **RADIO_CONFIG/RX_MEASURE/IQ_CAPTURE/COMMAND_CAPABILITY = 0x10NN con N∈{2..17}, UNKNOWN
  estático**. Se resuelve con **COMMAND_CAPABILITY→CMD_MASK** (§6).

**INFERENCE (orden de declaración, F3-table @0xc3555b80):** RADIO_CONFIG, COMMAND_CAPABILITY,
RX_MEASURE, WAIT_TRIGGER, MSIM_CFG, TX_CONTROL, IQ_CAPTURE, TX_MEASURE. Como 0x1000=default y
0x1001=error, el primer comando REAL cae en **0x1002**. Pero (§3) **0x1002/0x1003 crashean**, lo que
indica que esos slots son comandos de **acción/medición** (deref sin estado), no config → el orden
"RADIO_CONFIG=0x1002" del reporte viejo es **dudoso**. Barrer con COMMAND_CAPABILITY es obligatorio.

**Field-tables por comando (FACT, de reportes previos re-usados; los field_ids sí son FACT):**
```
RADIO_CONFIG @0xc37c0268 · COMMAND_CAPABILITY @0xc37c03b4 · RX_MEASURE @0xc37c0478
IQ_CAPTURE @0xc37c0828 · TX_MEASURE @0xc37c0a10 · WAIT_TRIGGER @0xc37c0644 · TX_CONTROL @0xc37c078c
```

---

## 3. Q3 — ¿POR QUÉ 0x1002/0x1003 CRASHEAN (SSR) PERO 0x1000/0x1001 DAN 0x14 LIMPIO?

### 3.1 0x1000 y 0x1001 son "sin cuerpo real" — FACT
- **0x1001 (slot1) = `0xd827176c`**: handler de ERROR explícito. Loguea F3 ("unsupported cmd")
  y retorna código de error → el dispatch traduce a **status 0x14**. No toca punteros. **FACT.**
- **0x1000 (slot0) = `0xd8271630`** directo (la tabla[0] apunta al propio `callr`): entra con
  `r2 = 0xd86fcf84` (handler default precargado en `0xd8271508`). `0xd86fcf84` hace
  `cmp.eq(r2,#0x2a)` y por lo general **bail limpio** (dealloc_return con status err). No deref de
  buffers no inicializados. → **0x14/err limpio**, no SSR. **FACT (estructura) / INFERENCE (0x14).**

### 3.2 0x1002 y 0x1003 SÍ ejecutan cuerpo de comando → deref con num_tlv=0 — FACT del path + INFERENCE del crash
Ambos handlers hacen la secuencia completa:
```
0xd86fd0f4 (0x1002):  call 0xd86fd0e8 (=0xd8272c10 parse TLV) ; command_id=memub(req+0xa) ;
                      bound cmd<=0x31 ; call 0xd8272684 (resolver fld_tbl) ;
                      call 0xd8272cd8 (DECODE+ACTION)
0xd86fd230 (0x1003):  call 0xd86fd0e8 ; command_id=memub(req+0xa) ; call 0xd8272684 ;
                      call 0xd827e064 (DECODE)
```
El deref peligroso está en `0xd8272cd8`:
```
d8272d08:  p1 = cmp.eq(r18,#0x3)                 ; r18 = parámetro de tipo (del request)
d8272d0c:  p0 = cmp.eq(r18,#0x5)
d8272d40:  immext(#0xd826d3c0)
d8272d44:  if (p0) r5 = ##0xd826d3e4             ; decode-A
d8272d48:  immext(#0xd8292280)
d8272d4c:  if(!p0) r5 = ##0xd8292298             ; decode-B
d8272d50:  callr r5                              ; <<< entra al pipeline de decode/measure
```
- Con **num_tlv=0** el request queda vacío: `0xd8272c10` no popula carrier/agc/tipo. Los decode
  `0xd826d3e4`/`0xd8292298` (y aguas abajo `0xd82735cc`, arrays per-carrier con gates `idx<=0x29`,
  `memd(r4+0x1240)` bitmap) **indexan/deref-ean punteros que quedaron nulos o en default inválido**
  → **acceso inválido → SSR**. **INFERENCE (con base FACT del path de deref).**
- **Diferencia clave vs 0x1000/0x1001:** 0x1002/0x1003 **entran al cuerpo real del comando** (decode +
  pipeline de acción/medición) que asume estado previo; 0x1000/0x1001 **no ejecutan cuerpo** (default/error).
- **Lo que esto revela:** 0x1002/0x1003 son comandos de **acción/medición** (RX_MEASURE / IQ_CAPTURE /
  algún measure), NO config. Un comando de **config** (RADIO_CONFIG, unpacker `0xd8183f30`) hace
  **bail LIMPIO** con num_tlv=0 (`0xd818486c`: r24=0x10 → 0x14), **no crashea**. → **INFERENCE fuerte:
  RADIO_CONFIG NO es 0x1002 ni 0x1003.** (Coincide con radio_config_unpack.md §6.)

### 3.3 Probe seguro para 0x1002/0x1003 (evita el SSR) — INFERENCE accionable
Mandar ≥1 TLV que fije carrier antes de que el decode toque arrays:
```
4B 0B 27 00 02 10 01 00  01 00 04 00 00 00 00 00     ; RX_CARRIER(fid1,len4,val0) sobre 0x1002
4B 0B 27 00 03 10 01 00  01 00 04 00 00 00 00 00     ; idem sobre 0x1003
```

---

## 4. Q4 — CAMINO REAL PARA ABRIR EL GATE Y LLEGAR A RADIO_CONFIG

### 4.1 Reconciliación de los tres "gates" (cajas separadas) — FACT
| Gate | VA | Qué lee | Dónde vive (path) | ¿Bloquea DIAG-RADIO_CONFIG? |
|------|----|---------|--------------------|------------------------------|
| **G1** enter-writer | `0xd81e5d20` (`session->0xc==2`) → store `=1` @0xd81e5d54 | escribe `0xca7897b0[tech]` | **TECH_ENTER per-tech** (RFDEBUG 0x0D → `callr` runtime) | NO (otro path) |
| **G2** post-check | `0xd81e5d60` (`ctx->0x89a8==0x7`) | ctx del enter-writer | dentro de G1 | NO |
| **G3** action-gate | `0xd8202224` (`0xca7897b0[tech]==1`) | array per-tech | **executor de acción RF diferida** `0xd8201d3c` (RF-task) | NO (RADIO_CONFIG no lo alcanza) |
| **G4** carrier-activate | `rflte_mc_carrier_activate` (report B) | — | **módulo FTM-LTE** `ftm_lte_*` | NO (otro módulo) |

**Ninguno de G1–G4 está en la ruta del unpacker DIAG de RADIO_CONFIG (`0xd8183f30`).** BFS: 147
funciones desde `0xd8183f30`, 0 llegan a G1/G2/G3. **FACT.** → **No hay circularidad.**

### 4.2 ¿Entonces qué produce el 0x14 que veías con RADIO_CONFIG? — FACT/INFERENCE
El 0x14 NO es G3. Candidatos reales, en orden:
1. **Sub_command equivocado**: barrías 0x1002/0x1003 (que crashean) o 0x1000/0x1001 (0x14 estructural).
   El RADIO_CONFIG real es otro 0x10NN (N∈{4..17}, o el que CMD_MASK indique). **FACT (0x1002/3 no son config).**
2. **`0xd8272684` devuelve 0** porque `@0xca79a850[command_id]+0x34 == 0` (el comando aún no fue
   registrado en la tabla-maestra RAM en tu instante) → error → 0x14. Esto depende del init runtime.
   **FACT (mecanismo)** / **UNKNOWN (estado RAM)**.
3. **G3 sólo mordería en la fase de ACCIÓN diferida** (RX_MEASURE/IQ_CAPTURE reales), no en el
   unpack ni en RADIO_CONFIG. La captura IQ real (executor `0xd8201d3c`→`0xd8201b38`) sí exige
   `0xca7897b0[tech]==1`. **FACT.**

### 4.3 Secuencia REAL recomendada (sin crashear) — FACT del formato + INFERENCE de orden

```
PASO 0  COMMAND_CAPABILITY (resolver los 0x10NN reales)  [OBLIGATORIO — cierra el UNKNOWN]
        Barrer SUB=0x1002..0x1017 con num_tlv=0 (o field1=0xFFFFFFFF). El que devuelva REPACK con
        CMD_MASK!=0 es COMMAND_CAPABILITY (unpacker 0xd81849ec, no crashea con num_tlv=0).
        4B 0B 27 00 <SUB:LE> 00 00
        Con CMD_MASK: bit N = slot 0x10(N) válido. QUERY_COMMAND=N -> PROPERTY_MASK nombra cada uno.
        >>> Esto te da RADIO_CONFIG / RX_MEASURE / IQ_CAPTURE reales de una sola vez.

PASO 1  TECH_ENTER (LTE)   [sub 0x000D, RFDEBUG — FACT]
        4B 0B 27 00 0D 00 03 00 \
           01 00 04 00 00 00 00 00 \    ; SUB=0
           02 00 04 00 01 00 00 00 \    ; TECH=1 (LTE)   <-- FACT
           03 00 04 00 00 00 00 00      ; SCENARIO=0
        (Necesario para que la fase de ACCIÓN/medición pase G3 después. Para RADIO_CONFIG en sí no
         hace falta G3, pero SÍ para RX_MEASURE/IQ_CAPTURE. Manda TECH_ENTER igual.)

PASO 2  RADIO_CONFIG (sub = el 0x10NN que CMD_MASK marcó como RADIO_CONFIG)  [NO gated por 0xd8202224]
        Unpacker 0xd8183f30. Mínimo LTE (INFERENCE, field_ids FACT @0xc37c0268):
        4B 0B 27 00 <SUB_RC:LE> 05 00 \
           01 00 04 00 00 00 00 00 \    ; RX_CARRIER=1  val 0   (fija índice de carrier PRIMERO)
           19 00 04 00 01 00 00 00 \    ; TECH_MODE=25  val 1 (LTE)
           05 00 04 00 03 00 00 00 \    ; BAND=5        val 3 (B3)
           06 00 04 00 27 06 00 00 \    ; CHANNEL=6     EARFCN 1575
           07 00 04 00 20 4E 00 00      ; BANDWIDTH=7   20000 kHz
        (Con RX_CARRIER primero NO crashea aunque fuera un slot de acción; para RADIO_CONFIG real el
         bail es limpio.)

PASO 3  IQ_CAPTURE (sub = el 0x10NN de IQ_CAPTURE, unpacker 0xd8189818, field-tbl @0xc37c0828)
        4B 0B 27 00 <SUB_IQ:LE> 04 00 \
           0E 00 04 00 00 40 00 00 \    ; NUM_OF_SAMPLES=14  16384
           10 00 04 00 00 C0 D4 01 \    ; SAMP_FREQ=16       30720000
           0F 00 04 00 00 00 00 00 \    ; IQ_DATA_FORMAT=15  0
           0D 00 04 00 01 00 00 00      ; FETCH_IQ=13        1
        En la FASE DE ACCIÓN de IQ_CAPTURE es donde G3 (0xd8202224) exige tech==1; por eso el PASO 1.
        Verificación en vivo: leer memb(0xca7897b0 + 1*8) debe ser 1 tras TECH_ENTER+activate.
```

### 4.4 Sobre G3 y quién pone `0xca7897b0[tech]=1` — FACT del writer, UNKNOWN del trigger runtime
El flag lo escribe `0xd81e5cec` (`0xd81e5d54: memb(tech<<3+0xca7897b0)=1`) SÓLO si `session->0xc==2`
(`0xd81e5d20`). Este writer **no** está en el path DIAG de RADIO_CONFIG ni en el de TECH_ENTER
estáticamente (es `callr` desde la tabla per-tech `@0xca733d10`/RAM). El disparador real que sube
`session->0xc→2` es el **commit FTM** (`0xd81dfecc` → módulo LTE FTM → `rflte_mc_carrier_activate`/
`rflte_ftm_mc_wakeup`, cluster `ce6exxxx`), que corre cuando la **portadora se configura con BAND+
EARFCN** (assert `band_get_band_from_dl_earfcn` @0xce8bdc10). → **Es RADIO_CONFIG (con BAND+EARFCN)
lo que, aguas abajo en el módulo FTM, dispara el activate que sube `session->0xc` y habilita el
writer** — pero eso **no** es el gate `0xd8202224` bloqueando a RADIO_CONFIG: RADIO_CONFIG se ejecuta
libre; su EFECTO (activate) es lo que luego abre G3 para IQ_CAPTURE. **INFERENCE fuerte (cadena de
módulos FTM verificada en enter_mode_path.md) — NO circular.**

**RESPUESTA Q4:** No hay que "abrir un gate para poder mandar RADIO_CONFIG". RADIO_CONFIG no está gated.
El orden correcto es: **[0] COMMAND_CAPABILITY para fijar los 0x10NN → [1] TECH_ENTER LTE →
[2] RADIO_CONFIG (BAND+EARFCN+BW+RX_CARRIER, que dispara el activate y sube session->0xc→2, poniendo
`0xca7897b0[LTE]=1`) → [3] IQ_CAPTURE (ya con G3 abierto).** No barrer 0x1002/0x1003 a ciegas (crashean).

---

## 5. FACT / INFERENCE / UNKNOWN (con VAs)

**FACT (verificado en clade_dec_full.bin / b21):**
- Gate G3: `0xd8202224` `memb(r21<<3+0xca7897b0)`, `0xd8202228` `if(!=1)`. Vive en función `0xd8201d3c`
  (allocframe @0xd8201d40). `0xd8201d38` es TAIL de la función previa `0xd8201b38`, no un inicio.
- `0xd8201d3c` y su gemela `0xd82029a4`: sin caller/dato estático → runtime-bound (executor de acción RF).
- Dispatcher DIAG RFTEST 0x10xx = `0xd82714b4`: parse `0xd8272bb0`; índice `r2=memub(+4)<=0x17`;
  `r3=tabla[slot] @0xc37c649c`; `jumpr`; stub → `0xd8271630 {callr r2}`. **No toca 0xd8201d3c ni 0xca7897b0.**
- Reachability (BFS): 0x10xx/RADIO_CONFIG(0xd8183f30, 147 fn)/handlers/0x70xx **NO** alcanzan
  `0xd8201d3c`, `0xd8202224`, `0xd81e5d60`, `0xd81e5cec`.
- 24 stubs `@0xc37c649c` → handlers (tabla §2.2). sub_command real @0x04 = `0x1000+slot`.
- 0x1000=slot0 (default `0xd86fcf84`, bail); 0x1001=slot1 (`0xd827176c` error→0x14); 0x1013=error.
- 0x1002=`0xd86fd0f4`→`0xd8272684`+`0xd8272cd8`(`callr r5`=0xd826d3e4/0xd8292298);
  0x1003=`0xd86fd230`→`0xd8272684`+`0xd827e064`. Ambos ejecutan cuerpo de comando (decode/action).
- Resolver `0xd8272684`: `fld_tbl=memw(memw(0xca79a850)+cmd*4+0x34)`; si 0 → error/0x14 (no crash).
- Unpackers REALES: RADIO_CONFIG=`0xd8183f30`, COMMAND_CAPABILITY=`0xd81849ec` (prólogos válidos).
  Los del reporte viejo (0xd81828f0/0xd8184fe8) están desalineados (basura). IQ_CAPTURE=`0xd8189818`.
- Enter-writer `0xd81e5cec`: store `=1` @0xd81e5d54 gated por `session->0xc==2` @0xd81e5d20.

**INFERENCE:**
- `0xd8201d3c` = executor de RX_MEASURE/IQ_CAPTURE (acción/medición diferida), gated por tech==1.
- 0x1002/0x1003 crashean por **deref de punteros/arrays no inicializados** en el decode/pipeline de
  acción cuando num_tlv=0 (son comandos de acción, no config). RADIO_CONFIG hace bail limpio → **RADIO_CONFIG ≠ 0x1002/0x1003**.
- Orden de comandos (F3-table): RADIO_CONFIG primero, pero como 0x1002/3 crashean, el 0x10NN de
  RADIO_CONFIG es ≥0x1004 (o el que CMD_MASK diga). Cerrar con COMMAND_CAPABILITY.
- RADIO_CONFIG con BAND+EARFCN dispara (aguas abajo, módulo FTM) el activate que sube `session->0xc→2`
  y pone `0xca7897b0[LTE]=1`, abriendo G3 para la captura. No es circular.

**UNKNOWN (sólo en vivo / RAM):**
- sub_command 0x10NN EXACTO de RADIO_CONFIG / RX_MEASURE / IQ_CAPTURE / COMMAND_CAPABILITY
  (binding `command_id→unpacker` en `@0xca79a850`, poblado en runtime). → COMMAND_CAPABILITY/CMD_MASK.
- Estado runtime de `@0xca79a850[cmd]+0x34` (registrado o no) en el instante de tu prueba.
- Contexto/tabla RAM que hace `callr 0xd8201d3c` (executor de acción) y `callr 0xd81e5cec` (per-tech).

---

## 6. Reproducir
```bash
dis.sh 0xd8202200 0x60          # gate G3: memb(tech<<3+0xca7897b0)==1  (0xd8202224/28)
dis.sh 0xd8201d3c 0x20          # prólogo real de la funcion del gate (executor de accion)
dis.sh 0xd8201d34 0x10          # ver que 0xd8201d38 es TAIL de 0xd8201b38, no inicio
dis.sh 0xd82714b4 0x70          # dispatcher DIAG RFTEST 0x10xx (jumpr, NO llama al gate)
rd.py  0xc37c649c 24            # tabla 0x10xx (24 stubs)
dis.sh 0xd8271540 0x240         # los 24 stubs -> handlers
dis.sh 0xd86fd0f4 0x60          # handler 0x1002 (crash path): 0xd8272684 + 0xd8272cd8
dis.sh 0xd8272cd8 0x80          # callr r5 = 0xd826d3e4/0xd8292298 (decode/action, deref con num_tlv=0)
dis.sh 0xd8272684 0x40          # resolver fld_tbl @0xca79a850 (0->error/0x14, no crash)
dis.sh 0xd8183f30 0x30          # unpacker RADIO_CONFIG REAL (prologo valido)
dis.sh 0xd81849ec 0x30          # unpacker COMMAND_CAPABILITY REAL (prologo valido)
# BFS reachability (reproduce §1.3):
python3 - <<'PY'
import subprocess,re
def calls(v,l=0x140):
    o=subprocess.run(['/tmp/modemre/dis.sh',hex(v),hex(l)],capture_output=True,text=True).stdout
    return set(int(x,16) for x in re.findall(r'call (0x[0-9a-f]+)',o))
T={0xd8201d3c,0xd81e5cec,0xd81e5d60}
for r in [0xd8183f30,0xd86fd0f4]:
    seen=set();fr={r};d=0;f=set()
    while fr and d<6:
        n=set()
        for x in fr:
            if x in seen:continue
            seen.add(x);c=calls(x);f|=c&T;n|={y for y in c if 0xd8100000<=y<0xd8300000}
        fr=n-seen;d+=1
    print(hex(r),'reaches',[hex(x) for x in f],'scanned',len(seen))
PY
```
