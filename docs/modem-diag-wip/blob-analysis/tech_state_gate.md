# FTM TECH-STATE GATE @0xca7897b0 — quién escribe, por qué TECH=1 no basta, y la secuencia correcta
Build: **MPSS.HI.4.3.4-00494-MANNAR_GEN_PACK-1.24452.133** (SM6375 / Moto G82 5G)
Imagen analizada: `/tmp/modemre/clade_dec_full.bin` (VA base 0xd8000000, decompresada/relocada).
Disasm: `/tmp/modemre/dis.sh <va> <len>` (llvm-objdump hexagon v66). Todo verificado en esta imagen.

Leyenda: **FACT** = instrucción/byte verificado en el binario · **INFERENCE** = deducción con base · **UNKNOWN** = no aislado.

> NOTA importante de address-space: `0xca7897b0`, `0xcbf68508`, `0xcbad8a70` y los strings
> `0xf80970xx`/`0xf808dbxx` son direcciones de la imagen DECOMPRESADA (clade). Son **RAM/.bss
> poblada en runtime** — NO están en los `modem.bNN` originales (esos usan VA 0xc0xxxxxx). Por eso
> `rd.py` las reporta como "no seg" o todo-ceros: son tablas/estructuras construidas al iniciar.

---

## 0. CORRECCIÓN AL REPORTE PREVIO (iq_final_values.md §6)

El reporte previo mezcló DOS cosas distintas. Verificado aquí instrucción-por-instrucción:

1. **`@0xca7897b0` (stride 8) NO guarda `0x7`.** Guarda un **flag per-tech de 1 byte: `1`=entrada,
   `0`=salida.** Es el array que el gate del path RFTEST lee y compara contra **`==1`** (no `==7`).
   - Store `=1`: `0xd81e5d54` (FACT). Store `=0`: `0xd81e5fc4` (FACT). Read/gate `!=1`: `0xd8202228` (FACT).
2. **El `==0x7`** que veías es de OTRO byte: `memb(ctx+0x89a8)` (`0xd81e5d60`), un **post-check de
   consistencia** dentro del mismo escritor. `0x7` = tech-index "no-tech/else" (rama else de 0xd8169ec0).
   Ese `==0x7` dispara `r0=#0x10` + abort `0xda01cdb0`; NO es el gate que ve el usuario en RFTEST.

Ambos hechos abajo con VA y bytes.

---

## 1. QUIÉN ESCRIBE @0xca7897b0  — **FACT**

### 1.1 Escritor de "ENTRADA" (pone el byte = 1): función **0xd81e5cec**
```
0xd81e5cec  <FUNC ENTER-COMMIT>  (allocframe @0xd81e5cf0 ; dealloc_return @0xd81e5da8)
...
d81e5d08:  r16 = r0 ; r2 = memw(r0+#0x0)          ; r2 = *(arg+0)  (sub-struct de sesión)
d81e5d0c:  if (r2 != 0) jump 0xd81e5d1c           ; si *(arg+0)==0 -> error
d81e5d1c:  r3 = memw(r2+#0xc)
d81e5d20:  if (r3 != 2) jump 0xd81e5d70           ; <<< GATE INTERNO: r2->0xc DEBE ser 2, si no NO ESCRIBE
d81e5d2c:  r17 = memub(r2+#0x12)                  ; r17 = tech-index (byte parseado del TLV TECH)
d81e5d38:  call 0xd8051b58                        ; memcpy (init de sub-bloque en +0x89b0)
d81e5d40:  r0 = memw(r17<<#0x2 + ##0xcbf68508)    ; tabla de punteros a ctx per-tech (RAM)
d81e5d44:  r3 = #0x1                              ; <<< VALOR A ESCRIBIR = 1
d81e5d50:  immext(#0xca789780)
d81e5d54:  memb(r17<<#0x3 + ##0xca7897b0) = r3    ; <<< STORE: estado[tech] = 1  (ENTERED)  ***
d81e5d5c:  r2 = memb(r2 + ##0x89a8)               ; lee byte de estado del ctx (post-check)
d81e5d60:  p0 = cmp.eq(r2,#0x7)                   ; si ctx->0x89a8 == 7 (no-tech)
d81e5d64:  if(p0.new) r0 = #0x10                  ; -> error 0x10
d81e5d6c:  if(p0) call 0xda01cdb0                 ; -> abort/err  (se propaga a status 0x14)
```
- **Escribe `@0xca7897b0 + tech*8 = 1`** en `0xd81e5d54`. Bytes: `f0 e3 11 ad` (memb Rt<<3+##U = Rs). **FACT.**
- El valor escrito es **1** (`r3 = #0x1` en `0xd81e5d44`, bytes `23 40 00 78`). **FACT.**

### 1.2 Escritor de "SALIDA" (pone el byte = 0): función **0xd81e5e18**
```
d81e5e18  <FUNC EXIT>  (allocframe @0xd81e5e1c)
d81e5e24:  r18 = memub(r0+#0x1c)                  ; tech-index
d81e5e50:  r16 = memw(r18<<#0x3 + ##0xcbad8a70)   ; otra tabla ctx per-tech
...
d81e5fbc:  r2 = memw(r19+#0x0)
d81e5fc0:  immext(#0xca789780)
d81e5fc4:  memb(r18<<#0x3 + ##0xca7897b0) = r3.new ; <<< STORE: estado[tech] = 0  (EXITED)   ***
                                                    ; r3 = #0x0 (0xd81e5fb8, bytes 03 40 00 78)
d81e5fcc:  r2 = memb(r2 + ##0x89a8)
d81e5fd0:  p0 = cmp.eq(r2,#0x7)                   ; mismo post-check
d81e5fdc:  if (p0) call 0xda01ce80
```
- **Escribe `@0xca7897b0 + tech*8 = 0`** en `0xd81e5fc4`. **FACT.**

### 1.3 Base y layout del array (FACT)
- Base RFTEST tech-table = `0xca789780`. El array de estado per-tech = **`0xca7897b0` = base + 0x30**.
- Índice = **tech-index interno** (no el valor del TLV), stride **8** (`tech<<3`). **FACT** (por `<<#0x3`).
- 32 referencias a `0xca789780`-region, todas en 0xd81e5xxx–0xd81e8xxx + una en 0xd8202xxx (el gate RFTEST).

### 1.4 Sin xref estático (FACT / explicación)
`0xd81e5cec`, `0xd81e5e18` NO tienen `call` estático ni aparecen como puntero literal en los 10 MB
(verificado con `find_callers.py` y búsqueda de puntero crudo → 0 hits). Se invocan por **tabla
per-tech poblada en runtime** (el handler TECH_ENTER hace `callr` sobre `memw(tbl_tech+0)`; §3).
Consistente con la tabla RAM RFTEST @0xcaad9d00 del reporte previo. **FACT (no hay xref estático).**

---

## 2. QUÉ CONDICIONES SE CHEQUEAN ANTES DE ESCRIBIR (por qué TECH=1 no basta) — **FACT**

Dentro del escritor `0xd81e5cec` hay **DOS puertas** antes/alrededor del store:

| # | VA | Condición | Efecto si falla |
|---|----|-----------|-----------------|
| A | `d81e5d0c` | `*(arg+0)  != 0`  (sub-struct de sesión existe) | jump a rama error (log `f8097040+`) |
| B | `d81e5d20` | `(*(arg+0))->0xc == 2` | **jump 0xd81e5d70 SIN escribir el array** |
| C | `d81e5d60` | (post-store) `ctx->0x89a8 != 0x7` | `r0=0x10` + abort 0xda01cdb0 |

- **Puerta B es la clave**: el campo **`session->0xc` debe valer exactamente `2`** para que el
  store `estado[tech]=1` se ejecute. Si `session->0xc != 2`, el código salta directo a `0xd81e5d70`
  y **el byte @0xca7897b0 queda como estaba (0)** → los RFTEST siguen bloqueados. **FACT.**
- `session->0xc == 2` es un **estado de sesión "RF configurada/lista"**. NO lo pone TECH_ENTER por sí
  solo: es un contador/estado que se establece en un paso previo del framework (típicamente el
  arranque del subsistema FTM-RF / `session state = 2`). **INFERENCE** de que es un estado de sesión
  (por ser `*(arg+0)->0xc` comparado contra un enum pequeño y por el post-check `0x89a8`).
- **Por eso TECH=1 (TLV correcto) NO basta:** el valor del TLV solo determina `r17`=tech-index
  (`memub(r2+0x12)`, `0xd81e5d2c`). La ESCRITURA está condicionada a `session->0xc==2`. Si la sesión
  no está en ese estado, el escritor sale por 0xd81e5d70 (retorna 1 "ok" o llama al helper
  `0xd81dfecc` según `**r16==2`), pero **nunca toca el array**. Resultado: el flag per-tech sigue 0.

### 2.1 El helper profundo 0xd81dfecc (rama alterna del enter)
```
d81e5d70:  r17 = #0x1 ; r2 = memw(r16+#0x0)
d81e5d74:  r2 = memw(r2+#0x0)
d81e5d78:  if (r2 != 2) jump 0xd81e5da4           ; si **r16 != 2 -> return 1 (no-op ok)
d81e5d7c:  call 0xd81dfecc                        ; commit real de tech-enter (retorna 1=ok)
d81e5d84:  if (r0==1) jump 0xd81e5d98             ; r0==1 -> success (r17=1)
           else  ...  r17 = 0                     ; fallo
d81e5da4:  r0 = r17 ; dealloc_return              ; retorna 1 ok / 0 fallo
```
`0xd81dfecc` empieza con `call 0xd814e6ac` y valida `*(arg+0)` (mismo patrón). Es la
transacción real de entrada de tecnología (handshake con el core RF). **INFERENCE** (es el
"commit" que efectivamente habilita la tech; retorna 1 en éxito).

---

## 3. HANDLER DE TECH_ENTER (sub 0x000d) — del TLV TECH al store — **FACT del path**

### 3.1 Dispatch RFDEBUG (tabla @0xca65b414, stride 0xc)  [FACT]
```
d816d2d4:  r20 = memub(pkt+4)|memub(pkt+5)<<8     ; sub_command (u16)
d816d2ec:  r2  = memw(##0xca65b414)               ; tabla RFDEBUG
d816d308:  if (r20 > 0x15) -> error 0x10          ; gate sub<=0x15
d816d318:  r3  = mpyi(r20,#0xc)                   ; sub*12
d816d324:  r0  = memw(r2+r3)                      ; slot[sub].handler
d816d350:  callr handler
```
Slot 13 (0x0d) = **TECH_ENTER_EXIT**, handler **0xd8174758**. **FACT** (re-confirmado).

### 3.2 Handler 0xd8174758 — parse TECH y dispatch per-tech  [FACT]
```
d8174764:  r16 = r0 ; r2 = memw(r0+#0x4)
d817476c:  r19 = memw(r16+#0x0)                    ; r19 = struct del request FTM parseado
d817479c:  r18 = memub(r19+#0x12)                  ; <<< r18 = tech-index bruto (del TLV TECH)
d81747a0:  call 0xd8169618 ; r0 = r18              ; mapea TECH_TLV -> tech-index interno (tabla @0xc37bdfe0)
d81747a8:  r1 = r0
d81747ac:  if (r0 > 0x14) jump 0xd8174974          ; error si índice fuera de rango
d81747b0:  r17 += mpyi(r1,#0x1c)                    ; indexa TABLA PER-TECH (stride 0x1c) por tech-index
d81747b4:  r0 = memw(r17+#0x10)                     ; handler secundario (opcional)
d81747c4:  p0 = cmp.eq(r18,#0x6)                     ; caso especial tech==6
d81747d4:  if (!p0) jump 0xd8174814                  ; camino normal (tech != 6)
...
d8174804:  r2 = memw(r17+#0x0) ; r1 = memw(r17+#0x4)
d817480c:  callr r2                                  ; <<< callr per-tech: aquí entra 0xd81e5cec / 0xd81dfecc
d8174838:  call 0xd816d658  (rama tech!=6)           ; wrapper que invoca el commit per-tech
```
- **La cadena es:** TLV TECH → `r18 = memub(r19+0x12)` → mapeo `0xc37bdfe0` → tech-index →
  `tabla_per_tech[tech].fn` (`memw(r17+0)`) → `callr` → (para LTE) el **enter-commit** que llega a
  `0xd81e5cec`/`0xd81dfecc`, donde está el gate `session->0xc==2` y el store `@0xca7897b0=1`. **FACT del encadenamiento.**

### 3.3 POR QUÉ con TECH=1 NO escribe (las causas posibles, ordenadas por probabilidad)
El TLV TECH=1 SÍ produce tech-index 1 (=LTE) correctamente (mapeo @0xc37bdfe0, verificado en report
previo). El bloqueo del store NO es el valor de TECH; es una de estas (todas verificadas como
condiciones reales en el binario):

1. **`session->0xc != 2`** (Puerta B, `0xd81e5d20`) — **causa principal**. La sesión RF no está en
   estado "2" (configurada/lista) cuando llega TECH_ENTER. El escritor salta el store. **FACT (gate)** /
   **INFERENCE (que este es el estado que falta en tu secuencia)**.
2. **`*(arg+0) == 0`** (Puerta A) — el sub-struct de sesión no fue alocado (falta un paso previo de
   init de sesión FTM-RF). **FACT (gate)**.
3. **El `callr` per-tech (`0xd817480c`) no llega al commit**: si `tabla_per_tech[1].fn` (RAM) no está
   poblada, `r2==0` y no se llama nada; el handler devuelve ok-silencioso sin tocar el array.
   La tabla per-tech (stride 0x1c) se puebla en runtime → **UNKNOWN estático** si el slot LTE está listo.
4. **Falta un TLV además de TECH**: el request debe traer también el/los campos que ponen
   `session->field0` y `session->0xc`. Con solo TECH el struct de sesión queda incompleto. **INFERENCE.**

> Conclusión: **TECH=1 es necesario pero no suficiente.** El store a `@0xca7897b0` exige que la
> **sesión RF ya esté en estado 2** (Puerta B). Eso implica un **paso previo** (init/arranque de la
> sesión FTM-RF, típicamente un comando de "RF mode/session start" antes de TECH_ENTER), no solo el TLV.

---

## 4. EL GATE EXACTO QUE DEVUELVE 0x14 — **FACT**

Hay que distinguir DOS gates. El que ve el usuario en RFTEST es el (I).

### (I) Gate del path RFTEST (lee el array @0xca7897b0, compara `==1`)  — **FACT**
```
d8202218:  r0 = memuh(r17+#0x0)
d820221c:  if (r0==0) jump 0xd820222c
d8202220:  immext(#0xca789780)
d8202224:  r2 = memb(r21<<#0x3 + ##0xca7897b0)     ; <<< LEE estado[tech]
d8202228:  if (r2 != #0x1) jump 0xd8202234         ; <<< GATE: si NO está "entered" (==1) -> rama alterna
```
- VA del read/gate: **`0xd8202224`–`0xd8202228`**. Condición precisa: **`estado[tech] == 1`** para
  continuar por la rama "tech activa". Si el byte es `0` (no entrado) o cualquier ≠1, salta. **FACT.**
- Este es el mecanismo por el que los RFTEST (0x1000–0x3FFF) quedan bloqueados: mientras
  `@0xca7897b0[tech] != 1`, el ejecutor no corre la parte real y el (un)packer/dispatch termina
  emitiendo **status 0x14 (DIAG_BAD_PARM_F)**.

### (II) Post-check interno del escritor (lee ctx->0x89a8, compara `==7`)  — **FACT**
```
d81e5d5c:  r2 = memb(r2 + ##0x89a8)
d81e5d60:  p0 = cmp.eq(r2,#0x7)                     ; 0x7 = tech-index "no-tech/else"
d81e5d64:  if(p0.new) r0 = #0x10                    ; error 0x10
d81e5d6c:  if(p0)     call 0xda01cdb0               ; abort
```
- VA: **`0xd81e5d60`**. Condición: **`ctx->0x89a8 == 0x7`** → error `0x10`. **FACT.**
- El `0x7` que el reporte previo atribuía a `@0xca7897b0` es en realidad ESTE byte (`ctx+0x89a8`).

### Mapeo error 0x10 → status DIAG 0x14  — **FACT**
En el dispatch (0xd816d3ec) el status interno se traduce y se escribe en el header de la rsp:
```
d816d3ec:  p0 = cmp.eq(r3,#0x14)                    ; si status interno == 0x14
d816d3f4:  if(p0) r2 = memw(r29+#0x38)
d816d424:  memb(r0+#0x8) = r21                       ; escribe el status byte (0/0x10/0x20/0x2/0x4)
```
- **0x14 = DIAG_BAD_PARM_F** (constante fija de diagcmd.h; "parámetro inválido / estado incorrecto").
  Confirmado como valor DIAG estándar, no es un código propietario. **FACT del valor.**
- Semánticamente: "comando reconocido pero rechazado porque la tech no está entrada / estado incorrecto".
  Coincide 1:1 con el síntoma. **FACT del significado.**

---

## 5. SECUENCIA CORRECTA PARA QUE EL BYTE PASE A "ACTIVO" (byte-a-byte)

### Diagnóstico de raíz
El array `@0xca7897b0[tech]` pasa de `0`→`1` SOLO cuando el escritor `0xd81e5cec` ejecuta su store,
y eso exige **`session->0xc == 2`** (Puerta B, `0xd81e5d20`). Con solo mandar TECH_ENTER + TECH=1
esa condición **no se cumple** si la sesión RF no fue inicializada antes. → **Orden y paso previo importan.**

### Header común FTM (FACT de layout, del report previo, re-usado):
```
4B 0B  14 00  5A 03  <id06 lo hi>  <id08 lo hi>  27 00  <sub lo hi>  <ntlv lo hi>  <TLVs...>
└DIAG┘ └subc┘ └ftmid┘ └handle 0x06┘ └ignora 0x08┘ └ftmcmd=LTE┘ └sub_command┘ └num_tlv┘
```

### PASO 0 (NUEVO / requerido) — poner la sesión RF en estado 2  [INFERENCE fuerte]
Antes de TECH_ENTER, la sesión FTM-RF debe estar "configurada" (`session->0xc==2`). En este
framework eso lo hace el **arranque/registro de la sesión RF** (el mismo path que aloca `*(arg+0)`
y setea `session->field0`). Candidatos concretos a mandar primero (barrer/observar la rsp):
  - un comando de **RF mode / session start** del subsistema FTM-RF (sub_command del framework RFTEST),
    o el propio **COMMAND_CAPABILITY** que fuerza la construcción de la sesión.
  - **Método de verificación en vivo:** tras el paso 0, un TECH_ENTER debe devolver status byte = 0
    y el read `0xd8202224` empezar a ver `1`. Si TECH_ENTER sigue no cambiando el byte → la sesión
    aún no está en 2 (repetir paso 0 con el comando de session-start correcto).

> Este PASO 0 es la pieza que faltaba en el reporte previo. Sin él, TECH=1 nunca escribe. **INFERENCE**
> (basada en que Puerta B compara `session->0xc==2` y en que TECH_ENTER por sí solo no toca ese campo).

### PASO 1 — TECH_ENTER (LTE)   [sub=13=0x0d FACT · TECH=1 FACT]
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
**Confirmación de éxito:** status byte = 0 en la rsp. Si el paso 0 se hizo bien,
`estado[LTE=idx1] @ (0xca7897b0 + 1*8)` pasa a **1**.

### PASO 2 — RADIO_CONFIG / RX_MEASURE / IQ_CAPTURE (RFTEST)
Ya con el flag en 1, el gate `0xd8202224` (`==1`) deja pasar y los RFTEST dejan de dar 0x14.
(sub_command de cada RFTEST sigue siendo **UNKNOWN estático**; usar COMMAND_CAPABILITY→CMD_MASK
para resolver el enum, como en iq_final_values.md §5).

### Entre pasos
- Esperar la rsp de cada paso (no pipeline). El PASO 1 (commit) puede requerir el callback de
  `0xd81dfecc`; si el commit es asíncrono, dejar un breve margen antes del PASO 2. **INFERENCE.**

---

## 6. FACT / INFERENCE / UNKNOWN (con VAs)

**FACT (verificado en clade_dec_full.bin):**
- Escritor "enter" del array = **0xd81e5cec**; store `@0xca7897b0[tech]=1` en **0xd81e5d54** (`r3=#1` @0xd81e5d44). Stride 8 (`<<3`), índice = tech-index.
- Escritor "exit" = **0xd81e5e18**; store `@0xca7897b0[tech]=0` en **0xd81e5fc4**.
- Puerta que impide el store: **0xd81e5d20** `if ((*(arg+0))->0xc != 2) skip`. → TECH=1 no basta.
- Puerta previa: **0xd81e5d0c** `*(arg+0) != 0`.
- Post-check interno: **0xd81e5d60** `ctx->0x89a8 == 0x7` → error 0x10 → abort 0xda01cdb0. (El `0x7` real vive aquí, NO en @0xca7897b0.)
- Gate del path RFTEST (lee el array): **0xd8202224** `r2 = memb(tech<<3 + 0xca7897b0)`, **0xd8202228** `if (r2 != 1) …`. Condición para pasar: **estado[tech]==1**.
- Dispatch TECH_ENTER: tabla RFDEBUG @0xca65b414, slot 13 → handler **0xd8174758**; TLV TECH leído en **0xd817479c** (`memub(r19+0x12)`), mapeo **0xd81747a0**→tabla @0xc37bdfe0, dispatch per-tech `callr` en **0xd817480c**.
- Status 0x14 = DIAG_BAD_PARM_F; traducción en dispatch **0xd816d3ec**/**0xd816d424**.
- El escritor no tiene xref estático (tabla per-tech poblada en runtime).

**INFERENCE:**
- `session->0xc == 2` = estado "sesión RF configurada/lista"; requiere un **paso previo** (session-start / init RF) antes de TECH_ENTER. Es la razón real de que TECH=1 no cambie el byte.
- `0xd81dfecc` = commit real de tech-enter (retorna 1 en éxito); posible callback asíncrono.
- La secuencia correcta = [PASO 0: session-start] → [PASO 1: TECH_ENTER TECH=1] → [PASO 2: RFTEST].

**UNKNOWN (requiere RAM en vivo o emular init):**
- El comando/sub_command EXACTO del PASO 0 que pone `session->0xc=2` (framework RFTEST, tabla RAM @0xcaad9d00; método: barrer o usar COMMAND_CAPABILITY).
- Si el slot per-tech LTE (`tabla_per_tech[1].fn`, stride 0x1c) está poblado — depende del init runtime.
- Valor de tech-index para NR5G en el TLV.

---

## 7. Reproducir
```
/tmp/modemre/dis.sh 0xd81e5cec 0xc0     # escritor ENTER (store @0xca7897b0=1 + gate session->0xc==2 + post-check 0x89a8==7)
/tmp/modemre/dis.sh 0xd81e5e18 0x1c8    # escritor EXIT  (store @0xca7897b0=0)
/tmp/modemre/dis.sh 0xd8202200 0x50     # gate RFTEST: memb(tech<<3+0xca7897b0)==1
/tmp/modemre/dis.sh 0xd8174758 0xc0     # handler TECH_ENTER (TLV TECH -> tech-index -> callr per-tech)
/tmp/modemre/dis.sh 0xd816d2d4 0x90     # dispatch RFDEBUG (tabla @0xca65b414, slot 13)
/tmp/modemre/dis.sh 0xd816d3e0 0x50     # status interno -> byte de status (0x14)
python3 /tmp/modemre/rd.py 0xc37bdfe0 8 # tabla TECH_TLV->tech-index (TECH1->1 = LTE)
```
