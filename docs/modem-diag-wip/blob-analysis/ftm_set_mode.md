# ftm_set_mode — Comando FTM que pone el modem en "RF calibration mode" (gp+0x740=2) + tabla ftm_cmd_id + creación del ctx RF (gp+0x7000)

**Target:** SM6375 / Moto G82 5G · MPSS.HI.4.3.4-00494-MANNAR_GEN_PACK-1.24452.133
**Imagen fiable:** `/tmp/modemre/clade_dec_full.bin` (VA base `0xd8000000`, 10 MB CLADE-descomp, `dis.sh <va> <len>`).
**Rodata:** b21 @0xc3553000, b23 @0xc8b6a000, b27(desc) seg27_dec.bin @0xce480000. **RW:** b25 @0xcbf4f000 (gp), b26 @0xcc000000.
**Herramientas:** `find_refs.py`, `resolve_callr.py`, `_fns.pkl`, `_idx.txt`, `_dis_b*.txt` (nativo), `clade_extractor_sm6375`+`libclade.so`.

**Leyenda:** **FACT** = leído byte/instrucción con VA · **INFERENCE** = deducción con base dura · **UNKNOWN** = sólo runtime/RAM/fuera del ELF.

---

## 0. TL;DR — respuestas directas

1. **El store del literal `2` a `gp+0x740` NO existe en NINGÚN código estáticamente decodificable** —
   ni en los 10 MB CLADE fiables, ni en clade_dec.bin (54 MB, confirmado mal-alineado), ni en el código
   NATIVO (b02–b13, b30). Se rastreó de forma exhaustiva por 3 vías (gp-store, abs-store, base-pointer).
   **La razón dura (FACT, ya probada en `map_rflte_code.md`): el driver que lo escribe —`rflte_ftm_*`,
   `ftm_cal_*`, el setter de `rf_mode`— vive en el pool PAGINADO dlpager `0xd4400000..0xd6539000`, cuyo
   mapa de páginas NO está en el MBN.** Por eso su store computado (`memb(ctx_or_gp+0x740)=<mode_enum>`)
   es físicamente inalcanzable para el análisis estático con este material. **FACT (ausencia total de store #2) + FACT (código RF paginado ausente).**

2. **El comando que lo dispara** (INFERENCE fuerte, con VAs FACT del path): el **FTM_COMMON**
   (DIAG `4B 0B`, **ftm_cmd `00 00` @pkt[2]**) — su handler es **`0xd8271290`** (FACT), que ejecuta el
   comando de RF-enable/set-mode (`0xd8263e2c`, FACT) → llama al árbol `rflte_ftm_*` (paginado) que pone
   `rf_mode = FTM_RF_MODE_CAL` (= **2**) y crea el ctx RF. **En la práctica se entra vía QMI-DMS
   `set_operating_mode` a un modo de servicio FTM/CAL, o vía el propio FTM_COMMON set-mode.**

3. **Enum `gp+0x740` = {0,1,2}** (mode-to-string logger `0xd81bde74`, `==2`/`==1`/else). El valor **2** =
   "RF cal / non-signaling activo". Confirmado por el enum del firmware **`NR5G_LL1_CAL_FTM_RF_MODE_CAL`**
   (assert @0xce6cf8c6, `rf_mode` es `uint8`) y por `RFLM_WARNING_..._ON_NON_CAL_MODE` (@0xc37f24d2). **FACT.**

4. **El ctx RF `gp+0x7000` (0xcbf56000)** lo crea el accessor lazy get-or-create **`0xd8284eac`** →
   creator **`0xd8284cf4`** (`memw(gp+0x7000)=obj`, vtable @0xc37c6d80). Se dispara la 1ª vez que corre
   el path RF-instance del **mismo bring-up de cal** (no en boot). **FACT.**

5. **`factory-test` QMI (DMS set_operating_mode=FTM):** por sí solo **cambia el flag de modo del sistema
   (ONLINE↔FTM) y reenruta el RF a `rflte_ftm_*`, pero NO garantiza `gp+0x740==2` ni `gp+0x7000!=0`**:
   esos dos gates los completa el **activate FTM de la portadora** (SET_MODE cal + BAND+EARFCN). **FACT
   (partición de modo) + INFERENCE (que factory-test es prerequisito, no suficiente).**

---

## 1. QUIÉN ESCRIBE `gp+0x740 = 2` — BÚSQUEDA EXHAUSTIVA (los 3 vectores) [FACT]

`gp = 0xcbf4f000` (ya triangulado; ver `rf_cal_mode_gates.md §1`). Gate: `0xd81bd160 r2=memb(gp+0x740);
0xd81bd168 if(!=2) jump 0xd81bd4b0`. **FACT.**

### 1.1 Vector A — store gp-relativo `memb(gp+#0x740)=Rt` (opcode `S2_storerbgp`)
Decodé el opcode exacto (`0x4803e340` = `memb(gp+0x740)=r3`; cross-check con `memb(gp+0x738)=r2`=`0x4803e238`
y `memb(gp+0x766)=r2`=`0x4803e266`) y barrí AMBOS binarios por `byte0==0x40 && byte2==0x03 && byte3==0x48 && (byte1&0xe0)==0xe0`:
```
clade_dec_full.bin : SÓLO  0xd81bd494 (r3)  y  0xd81bd5fc (r3)
clade_dec.bin      : NINGUNO
```
Ambos writers computan `r3 = mux(pred,#1,#0)` → **0 ó 1**, y están en la **rama NO-activate** (post
`0xd81bd4b0`). Además reset a 0 en `0xd81bd7c8` (`r2=##0xcbf4f740; memb(r2)=#0`). **NINGÚN store de 2.** **FACT.**

### 1.2 Vector B — store absoluto extendido `memb(##0xcbf4f740)=...`
Barrí las **20** instancias de `immext` que cubren `0xcbf4f740..0x77f` (word `0x0cbf53dd`) en el 10 MB
y las 7 en clade_dec.bin, decodificando la instrucción siguiente. Resultado (FACT):
- Ningún store absoluto apunta a offset 0 del window (=0x740). Los stores `af..` que sí existen apuntan a
  **0x766** (`0xd835ba08`, `0xd835c050`), no a 0x740.
- `0xd82f0d80` tiene `r2 = mux(p0,#0x3,#0x2)` (¡genera 2 ó 3!) **pero** el destino es un logger
  (`call 0xd802c020` + formatters), NO un store a 0x740; el `memb(r16)=#0/#1` de esa función escribe
  **0xcbf4f776**, no 0x740. **FACT (analizado 0xd82f0d00–e3c).**

### 1.3 Vector C — base-pointer al struct (`rX=##0xcbf4f740; memb(rX+off)=imm`)
Los usos de `0xcbf4f740` como puntero-base (`0xd8209200/0c`, `0xd820bca8`, `0xd82c23c4`, `0xd82dd7b4`,
`0xd839d3f0`, `0xd8395008`) escriben **campos adyacentes** (0x743=#1, 0x74b=#0, 0x750=puntero, 0x766),
**nunca el byte 0x740 con 2**. **FACT.**

### 1.4 Código NATIVO (b02–b13, b30)
Grep de `gp+#0x740` y `0xcbf4f740` en TODOS los dumps nativos: **sólo 2 LECTURAS** en b13
(`0xc0db81b4`, `0xc0db81d0`, dentro de un dumper de estado que compara con `#1`). **Cero stores.** **FACT.**

### 1.5 Conclusión dura sobre el writer
`clade_dec.bin` (54 MB) verificado mal-alineado (la store conocida `0xd81bd494`/`0x4803e340` **no aparece**
en su offset ni en ningún lado → count 0). El extractor `clade_extractor_sm6375` sólo reconstruye
`0xd8000000..~0xd8a00000` (más allá `clade_read error=3`) — y ahí YA barrí todo. **El writer de `gp+0x740=2`
está en el código RF paginado ausente del ELF** (`rflte_ftm_*`/`ftm_cal_*`/setter de `rf_mode`, pool
dlpager `0xd4400000..0xd6539000`, mapa de páginas no presente — FACT de `map_rflte_code.md §0`). Es un
**store computado** `memb(<ctx>+0x740) = <phone_mode_enum>` donde el valor 2 llega como argumento del
comando set-mode; por eso jamás aparece como inmediato estático. **FACT (ausencia) + FACT (código ausente)
+ INFERENCE (store computado con el enum de entrada).**

---

## 2. LA TABLA ftm_cmd_id (u16 @pkt+0x02) — TODOS, no sólo 0x27 [FACT]

**Corrección de método:** hay DOS niveles y DOS tablas; no confundir.

### 2.1 Nivel-1 REAL de comando: jump-table `@0xc37bc828` indexada por `pkt[2]` (byte) [FACT]
Router **`0xd814e034`**: `r1=pkt[1]` (subsys, gate `==0x0b`); `r3=pkt[2]` (ftm_cmd);
`r13 = memw(pkt[2]<<2 + ##0xc37bc828); jumpr r13` (0xd814e0b4/0f8). Cada arm precarga un handler en un
registro y salta a un tail común (`0xd814e1ec`: `callr r3`). Tabla leída literal de b21:

| ftm_cmd (pkt[2]) | arm VA | handler (callr) | identidad |
|---|---|---|---|
| **0x00** | 0xd814e1ec | **0xd8271290** | **FTM_COMMON** ← aquí vive el set-mode / phone-mode |
| 0x01 | 0xd814e26c | (r?) | FTM_1X / CDMA |
| 0x02 | 0xd814e1a8 | 0xd81f571b-based | FTM_HDR/EVDO |
| 0x03 | 0xd814e138 | — | FTM_PROGRAMMING? |
| 0x04–0x06 | 0xd814e5e4 | **ERROR** | inválidos |
| 0x07 | 0xd814e14c | 0xd8288640-based | FTM_GSM |
| 0x08 | 0xd814e140 | 0xd8288640-based | FTM_WCDMA |
| 0x09 | 0xd814e2cc | — | FTM_UMTS? |
| 0x0d | 0xd814e314 | — | (13) |
| 0x11 | 0xd814e240 | 0xd81edd6c-based | FTM_AUDIO? |
| 0x22 | 0xd814e10c | 0xd8687218-based | (34) |
| **0x27** | 0xd814e534 | **0xd8157ec4** | **FTM_LTE** (TECH_ENTER, RADIO_CONFIG, IQ_CAPTURE) |
| 0x2e | 0xd814e4bc | — | (46) |
| 0x31 | 0xd814e1c0 | — | (49) |
| 0x1d/0x1e/0x23/0x2f/0x30 | 0xd814e370 | (compartido) | familia -tech |
| resto (0x0a,0x0b,0x0c,0x0e,0x0f,0x13,0x16..1a,0x1c,0x21,0x25,0x26,0x29..2d,0x34..37) | 0xd814e5e4 | **ERROR** | no registrados |

Índices válidos (arm ≠ 0xd814e5e4): **0x00,0x01,0x02,0x03,0x07,0x08,0x09,0x0d,0x10,0x11,0x12,0x14,0x15,
0x1b,0x1d,0x1e,0x1f,0x20,0x22,0x23,0x24,0x27,0x28,0x2e,0x2f,0x30,0x31,0x32,0x33,0x38,0x39,0x3a,0x3b,0x3c,
0x3d,0x3e,0x3f**. **FACT** (dump byte-exact de `0xc37bc828[0..0x3f]`).

### 2.2 La OTRA tabla (`@0xc37bd1e8`, 75 entradas, subsys DIAG 0x0B) es la del REGISTRO de subsistema DIAG
Registro `@0xc8dc3b50`: subsys=0x0B, count=75, tbl=`0xc37bd1e8`, formato `{cmd_lo:u16, cmd_hi:u16, disp:u32}`
(rango de códigos DIAG; en todas las entradas cmd_lo==cmd_hi = un valor único). **Todas las 75 entradas →
`0xd8150ed8`** (pre-validador/encolador que empaqueta el paquete FTM y lo postea al FTM-task con
`combine(#1,#0xb)`). Es la **capa de transporte DIAG→FTM-task**, no la dispatch de comando. El dispatch
real es §2.1. **FACT.**

### 2.3 FTM_COMMON (ftm_cmd 0x00) — handler `0xd8271290` — sub-comandos reconocidos [FACT]
`r1 = pkt[4] | pkt[5]<<8` (`0xd82712b0`), compara:
```
0xd82712b8:  cmp.eq(r1, #0x10f)  -> 0xd82712f8 -> call 0xd8263e2c   (RF enable/setup; toma pkt[6..9] packed)
0xd82712c4:  cmp.eq(r1, #0x4f7)  -> 0xd82712f0 -> call 0xd86f8514   (usa el resolver command_id 0xd8272684)
0xd82712d4:  cmp.eq(r1, #0x4f5)  -> path de log/estado
```
→ **Comando FTM_COMMON `0x10F` (271) = el "RF set-mode / radio-enable" de FTM_COMMON** (INFERENCE fuerte:
es el único que llama el setup RF `0xd8263e2c`, que valida un estado `==7` y llama `0xd8471498/a0` +
`0xd81578f0` = habilitar RF). Los códigos exactos y sus nombres byte-exact viven en el pool QSR
(`0xf80983xx`) no presente. **FACT (códigos 0x10F/0x4F5/0x4F7 + call-graph) · UNKNOWN (nombre string).**

### 2.4 Sub-espacio bajo ftm_cmd 0x27 (FTM_LTE) — recordatorio [FACT, de sub_command_map.md]
`sub_command = pkt[4..5]` particionado por RANGO (router `0xd8157ec4`): 0x000–0xFFF=**RFDEBUG**
(`@0xca65b414`, gate `<=0x15`, **TECH_ENTER=0x000D**); 0x1000–0x3FFF=**RFTEST** (RADIO_CONFIG/RX_MEASURE/
IQ_CAPTURE/COMMAND_CAPABILITY, tablas `@0xc37c649c/645c/6440`); 0x7000+=familia 0x70xx. **FACT.**

---

## 3. EL PAQUETE FTM_SET_MODE / "SET RF CAL MODE" — byte-exact

> **HONESTO:** el store del `2` está en código paginado ausente, así que **no puedo confirmar byte-a-byte
> el opcode ni el número de comando "canónico" de SET_MODE en ESTE build**. Doy las 3 formas concretas
> que el análisis estático respalda, en orden de probabilidad, para probar en vivo.

### 3.1 (Recomendada) FTM_COMMON radio-enable/set-mode — ftm_cmd 0x00, cmd 0x10F [call-graph FACT]
```
4B 0B 00 00 0F 01 <p0> <p1> <p2> <p3> ...
│  │  └┬─┘ └┬─┘ └──────────┬──────────┘
│  │   │    │              └ pkt[6..9] = arg packed 32-bit a 0xd8263e2c (mode/tech/carrier)
│  │   │    └ ftm_common_cmd = 0x010F (LE)  (el RF set-mode/enable de FTM_COMMON)
│  │   └ ftm_cmd = 0x0000 (FTM_COMMON)   @pkt[2..3]
│  └ subsys FTM (0x0B)                    @pkt[1]
└ DIAG_SUBSYS_CMD_F (0x4B)                @pkt[0]
```
El handler `0xd8263e2c` lee `pkt[6]|pkt[7]<<8|(pkt[8]|pkt[9]<<8)<<16` (24-bit packed) y ejecuta el
enable RF. **Los 4 bytes de payload son el selector de modo/tech** (semántica exacta UNKNOWN estático).
**FACT (layout+call) · INFERENCE (que este es el que enciende cal mode) · UNKNOWN (valores payload).**

### 3.2 (Alternativa canónica QC) FTM_SET_PHONE_MODE clásico — ftm_cmd 0x00, cmd 0x0001, mode=cal
En la mayoría de stacks QC, FTM_COMMON cmd **0x0001** = `FTM_SET_MODE(mode)` con `mode` = phone-mode enum;
para cal se usa un phone-mode de test. El tail-handler alterno de FTM_COMMON (`0xd8271400`, cmd 0/1/2/4 →
handlers `0xd86f8600/8280/8340/8400`) es candidato. Formato tentativo:
```
4B 0B 00 00 01 00 <mode:u16 LE>            ; FTM_SET_MODE(mode)
```
**INFERENCE (patrón QC) · UNKNOWN (que aplique en este build; probar 0x0000/0x0001/0x0004).**

### 3.3 (La que realmente usa el stack) QMI-DMS set_operating_mode → FTM  [ver §5]
En Moto/QC de producción el "entrar a cal" se hace por **QMI-DMS `set_operating_mode(oprt_mode)`** a un
modo de servicio FTM, NO por un solo DIAG. Cadena FACT (strings): DMS → `cm_ph_cmd_pref_change_req`
(0xc40cc45e) → MMOC (`=MMOC= Phone should be in online mode` 0xce64ebb0) → arranque del árbol `rflte_ftm_*`.
**FACT (cadena por strings) · INFERENCE (que es el vector operativo).**

### 3.4 Valor de "mode"
El enum `gp+0x740` es **{0,1,2}** con **2 = FTM_RF_MODE_CAL** (mode-to-string `0xd81bde74`; enum firmware
`NR5G_LL1_CAL_FTM_RF_MODE_CAL`, uint8, @0xce6cf8c6). El comando set-mode debe seleccionar el phone-mode
cuyo mapeo interno deja `rf_mode = 2`. **FACT (que 2=CAL) · UNKNOWN (qué valor de payload lo produce).**

---

## 4. CÓMO SE CREA EL CTX RF `gp+0x7000` (0xcbf56000) [FACT]

```
0xd8284eac  accessor get-or-create:
   d8284eb4:  r17 = memw(gp+0x7000)
   d8284eb8:  if (r17!=0) jump 0xd8284ef0        ; ya existe -> return
   d8284ed4:  call 0xd8284cf4                     ; CREATOR
   d8284ee4:  memw(gp+0x7000) = r17               ; guarda
0xd8284cf4  creator:
   d8284cfc:  call 0xd84c4b80                      ; ALLOC
   d8284d10:  memw(r16+0) = ##0xc37c6d80           ; VTABLE C++
   d8284d20:  memw(gp+0x7000) = r16                ; <<< store que puebla 0xcbf56000
```
Callers estáticos: `0xd8284eac ← 0xd8286290` (dentro de **`0xd828626c`**, verify/ensure ctx RF);
`0xd8284cf4 ← 0xd8284ed4`. `find_refs 0xd828626c` = **vacío** → `0xd828626c` es **handler registrado en
runtime (msgr/event del subsistema RF)**, se dispara en el **mismo bring-up del path RF-instance** que
enciende cal. **No corre en boot; corre bajo demanda.** **FACT.**

**Respuesta a "¿es el mismo SET_MODE o aparte?":** es **el mismo bring-up de cal** — al entrar a
cal/activate, el subsistema RF-instance toca `0xd828626c` → accessor → crea el ctx. **No es un RFTEST
aparte ni el primer RADIO_CONFIG por sí solo**; RADIO_CONFIG sólo lo desencadena *si* ya se está en el
modo cal (gp+0x740==2) y el activate corre. **FACT (call-graph) + INFERENCE (co-disparo con set-mode).**

---

## 5. ¿factory-test QMI YA setea `gp+0x740`? [FACT partición + INFERENCE suficiencia]

**No de forma garantizada.** Lo que hace `set_operating_mode(FTM)` (FACT por strings):
1. **Particiona el modo del sistema (ONLINE↔FTM/CAL).** Barrera dura: `Attempt to access FTM variables
   in ONLINE MODE` (0xce6d59e0 x3), `MMOC= Phone should be in online mode` (0xce64ebb0). El código FTM
   sólo corre en modo FTM. **FACT.**
2. **Reenruta el RF** de `lte_ml1_rfmgr` (ONLINE, par MSGR REQ/CNF) a **`rflte_ftm_*`** (wakeup/activate
   directos, sin enter_mode_cnf). Por eso "esperar el CNF" no destraba nada en FTM. **FACT (módulos)
   `enter_mode_path.md`.**
3. **PERO** `gp+0x740==2` y `gp+0x7000!=0` los completa el **activate FTM de la portadora**, que exige
   el **SET_MODE cal + BAND+EARFCN válidos** (assert `band_get_band_from_dl_earfcn` @0xce8bdc10). Sólo
   entonces el carrier-apply `0xd81bd018` pasa ambos gates → escribe `0xca79c494`. **FACT (gates) +
   INFERENCE (factory-test = prerequisito necesario, no suficiente).**

→ **Regla operativa:** `factory-test` mode es **necesario pero no suficiente**. Tras entrar a FTM hay que
ejecutar el **set-mode cal (§3)** y el **activate (TECH_ENTER LTE → RADIO_CONFIG BAND+EARFCN)** para que
los dos gates queden en 2 / no-NULL. Verificar SIEMPRE en vivo antes de IQ:
`memb(0xcbf4f740)==2`, `memw(0xcbf56000)!=0`, `memw(0xca79c494)!=0`.

---

## 6. SECUENCIA COMPLETA (integrada)

```
PASO A — ENTRAR A FTM/CAL
   (i)  QMI-DMS set_operating_mode -> modo servicio FTM     [vector operativo, §5]  y/o
   (ii) DIAG FTM_COMMON set-mode:  4B 0B 00 00 0F 01 <p0..p3>  (cmd 0x10F, handler 0xd8263e2c)  [§3.1]
   Efecto: arranca rflte_ftm_* (paginado) -> escribe rf_mode(gp+0x740)=2 (FTM_RF_MODE_CAL)
           y toca 0xd828626c -> accessor 0xd8284eac -> crea ctx -> gp+0x7000 != 0.
   >>> Sin A, gate(a)/gate(b) de 0xd81bd018 fallan y APPLY nunca corre (=> SSR en IQ).

PASO B — TECH_ENTER (LTE)   [RFDEBUG sub 0x000D bajo ftm_cmd 0x27 — FACT]
   4B 0B 27 00 0D 00 03 00
      01 00 04 00 00 00 00 00       ; SUB=0
      02 00 04 00 01 00 00 00       ; TECH=1 (LTE)
      03 00 04 00 00 00 00 00       ; SCENARIO=0

PASO C — RADIO_CONFIG (BAND + EARFCN + BW + RX_CARRIER)   [RFTEST 0x10xx bajo ftm_cmd 0x27]
   4B 0B 27 00 <SUB_RC:LE> 05 00
      01 00 04 00 00 00 00 00       ; RX_CARRIER (fid 1) = 0
      19 00 04 00 01 00 00 00       ; TECH_MODE  (fid 25)= 1 (LTE)
      05 00 04 00 03 00 00 00       ; BAND       (fid 5) = 3
      06 00 04 00 27 06 00 00       ; CHANNEL    (fid 6) = EARFCN 1575
      07 00 04 00 20 4E 00 00       ; BANDWIDTH  (fid 7) = 20000 kHz
   Con A hecho, el activate pasa gate(a)+gate(b) de 0xd81bd018 -> APPLY 0xd8273b2c -> SETTER 0xd8279264
   -> memw(0xca79c494)=0xca6e3b88.

PASO D — VERIFICAR EN VIVO (peek memoria DIAG):
   memb(0xcbf4f740)==2 ; memw(0xcbf56000)!=0 ; memw(0xca79c494)!=0

PASO E — IQ_CAPTURE / RX_MEASURE  (getter 0xd827923c ya no asserta -> sin SSR)
```
**Orden A→B→C obligatorio.** **FACT (gates + B/C) + INFERENCE (A = set-mode cal).**

---

## 7. FACT / INFERENCE / UNKNOWN — con VAs

**FACT**
- Router nivel-1 `0xd814e034`: ftm_cmd=pkt[2]; jump-table `0xc37bc828[ftm_cmd]` (0xd814e0b4/0f8); tail
  `0xd814e1ec` (`callr r3`). Índices válidos §2.1; arm de error = `0xd814e5e4`.
- **ftm_cmd 0x00 (FTM_COMMON) → handler `0xd8271290`** (r3 precargado @0xd814e0d4). Sub-cmds reconocidos:
  **0x10F** (→0xd8263e2c, RF enable/setup), **0x4F7** (→0xd86f8514), **0x4F5** (log). Dump byte-exact.
- ftm_cmd 0x27 (FTM_LTE) → `0xd8157ec4`; sub_command=pkt[4..5] por rango (RFDEBUG/RFTEST/0x70xx).
- Tabla DIAG subsys 0x0B `@0xc37bd1e8` (75 entradas) → todas a `0xd8150ed8` (encolador transporte).
- Gate(a) `0xd81bd160` `memb(gp+0x740)==2`; único enum-logger `0xd81bde74` (2/1/0). Enum firmware
  `NR5G_LL1_CAL_FTM_RF_MODE_CAL` (uint8, @0xce6cf8c6); `RFLM_..._ON_NON_CAL_MODE` @0xc37f24d2.
- **Store de `gp+0x740=2`: ausente en 10 MB CLADE + clade_dec.bin(54MB) + todo el nativo (b02–b13,b30).**
  Vectores A/B/C barridos. Únicos writers = `0xd81bd494`/`0xd81bd5fc` (=mux 0/1) + reset `0xd81bd7c8`.
- Código RF (`rflte_ftm_*`, `ftm_cal_*`, setter rf_mode) NO está en el ELF: vive en dlpager
  `0xd4400000..0xd6539000` (mapa de páginas ausente) — `map_rflte_code.md §0`. seg27 es rodata pura.
- gp+0x7000: accessor `0xd8284eac` → creator `0xd8284cf4` (`memw(gp+0x7000)=obj`@0xd8284d20, vtable
  ##0xc37c6d80). Callers: 0xd8284eac←0xd8286290(en 0xd828626c); 0xd828626c=runtime-bound (msgr).
- SSR: getter `0xd827923c` (memw 0xca79c494 NULL→err_fatal 0xd80d8998→0xc0d60074); setter único
  `0xd8279264` sólo desde APPLY `0xd8273b2c`←`0xd81bd2a8` (en `0xd81bd018`); valor 0xca6e3b88.
- QMI-DMS set_operating_mode: cadena por strings DMS→`cm_ph_cmd_pref_change_req`(0xc40cc45e)→MMOC
  (0xce64ebb0). Partición ONLINE/FTM: `Attempt to access FTM variables in ONLINE MODE` (0xce6d59e0).

**INFERENCE (base dura)**
- `gp+0x740=2` lo escribe (store computado `memb(<ctx>+0x740)=<mode>`) el driver `rflte_ftm_*`/`ftm_cal_*`
  paginado, disparado por el set-mode cal. El valor 2 llega como argumento (phone-mode/tech) del comando.
- El comando disparador es **FTM_COMMON (ftm_cmd 0x00), muy probablemente cmd 0x10F** (único que llama el
  RF-enable 0xd8263e2c), o el **FTM_SET_MODE clásico (cmd 0x0001)**; en producción vía **QMI-DMS FTM**.
- gp+0x7000 se puebla en el mismo bring-up (co-disparo con set-mode), no en boot, ni por un RFTEST aislado.
- factory-test QMI es **prerequisito necesario pero no suficiente** para los gates.

**UNKNOWN (sólo en vivo / RAM / fuera del ELF)**
- El store byte-exact de `gp+0x740=2` (VA del writer) — en código dlpager ausente.
- El número de comando y payload EXACTOS de SET_MODE en ESTE build (0x10F vs 0x0001 vs phone-mode QMI) y
  qué valor de payload deja `rf_mode=2`. Probar §3.1 → §3.2 en vivo, verificando `memb(0xcbf4f740)`.
- Nombres string de FTM_COMMON cmds (pool QSR `0xf80983xx` no presente).
- Slots 0x10xx ↔ nombre (tabla @0xca79a850 en RAM; cerrar con COMMAND_CAPABILITY).

---

## 8. REPRODUCIR
```bash
# writer gp+0x740 (ausencia de #2) — barrido de los 3 vectores
python3 - <<'PY'
d=open('/tmp/modemre/clade_dec_full.bin','rb').read()
h=[(0xd8000000+o) for o in range(0,len(d)-4,4)
   if d[o]==0x40 and d[o+2]==0x03 and d[o+3]==0x48 and (d[o+1]&0xe0)==0xe0]
print("gp-stores a 0x740:",[hex(x) for x in h])   # solo 0xd81bd494, 0xd81bd5fc (=mux 0/1)
PY
grep -nE "0xcbf4f740|gp\+#0x740" /tmp/modemre/_dis_b*.txt   # nativo: solo lecturas en b13

# tabla ftm_cmd (nivel-1)
dis.sh 0xd814e034 0xd0            # router: ftm_cmd=pkt[2] -> 0xc37bc828[ftm_cmd]
python3 - <<'PY'                  # dump jump-table
import struct;b=open('/tmp/modemre/modem.b21','rb').read();o=0xc37bc828-0xc3553000
for i in range(0x40):
  v=struct.unpack('<I',b[o+i*4:o+i*4+4])[0]
  print(hex(i),hex(v),'ERROR' if v==0xd814e5e4 else '')
PY
dis.sh 0xd8271290 0x90           # FTM_COMMON handler: cmp pkt[4..5] == 0x10f / 0x4f7 / 0x4f5
dis.sh 0xd8263e2c 0xa0           # cmd 0x10F -> RF enable/setup
dis.sh 0xd8157ec4 0x90           # FTM_LTE (ftm_cmd 0x27) router por rango

# ctx RF gp+0x7000
dis.sh 0xd8284eac 0x50 ; dis.sh 0xd8284cf4 0x40
python3 /tmp/modemre/find_refs.py 0xd8284eac   # <- 0xd8286290 (en 0xd828626c)
python3 /tmp/modemre/find_refs.py 0xd828626c   # vacio -> runtime/msgr-bound

# enum CAL=2
dis.sh 0xd81bde74 0x40           # mode->string (==2/==1/else)
```
