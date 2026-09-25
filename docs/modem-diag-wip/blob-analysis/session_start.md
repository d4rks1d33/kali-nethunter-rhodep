# FTM/RFTEST — PASO 0: quién pone `session->0xc = 2` y cómo abrir el gate `@0xca7897b0`
Build: **MPSS.HI.4.3.4-00494-MANNAR_GEN_PACK-1.24452.133** (SM6375 / Moto G82 5G)
Imagen: `/tmp/modemre/clade_dec_full.bin` (VA base 0xd8000000, código paginado descomprimido).
Disasm completo generado: `llvm-objdump-18 -d --triple=hexagon --mcpu=hexagonv66` sobre los 10 MB → `/tmp/_clade_full_dis.txt` (2.62 M líneas). Todo lo marcado FACT está verificado byte-a-byte en esa imagen.

Leyenda: **FACT** = instrucción/byte verificado · **INFERENCE** = deducción con base · **UNKNOWN** = no aislable estáticamente.

---

## 0. RESUMEN EJECUTIVO (leer primero)

1. **NO existe en el código alcanzable ningún store estático `memw/memh/memb(session+0xc) = 2`
   sobre la estructura de sesión** (la que deref-ea el escritor 0xd81e5cec en `*(arg+0)`,
   la de ~0x89b0 bytes con campo `+0x89a8`). Lo verifiqué barriendo los 10 MB completos:
   - Todos los `... (rX+#0xc) = #0x2` inmediatos caen en (a) stores de pila `r29+0xc`,
     (b) descriptores de cola/tarea (0xd811fb18), (c) tablas estáticas de config
     (0xd8843c54), o (d) descriptores de captura HW con `utimerlo` (0xd83c417c/0xd83c40ac).
     **Ninguno es la sesión FTM.** **FACT.**
   - El inicializador real de la sesión (`0xd81df874`, tras el memset de `0xd81df9f8`)
     setea `session+0x4 = 0x15` y `session+0x8 = 0x15` (=21, nº de techs) pero **deja
     `session+0xc = 0`** (queda como lo dejó el memset). **FACT.**

2. **`session->0xc` NO es un "flag booleano de sesión-lista": es el estado/modo operativo
   de la sesión, un enum pequeño (valores observados: 0, 1, 2, 3, 0x13).** El escritor de
   entrada exige `==2` ("modo activo/running"); otras funciones del state-machine lo
   comparan contra `==0x13`, `==0x0`, y hacen switch `>2 / ==1 / ==2 / ==3`. **FACT** (VAs
   en §2). Por eso "TECH=1" nunca lo mueve: TECH sólo fija el **tech-index** (`session+0x12`),
   no el **modo** (`session+0xc`).

3. **El modo `2` lo establece el handshake asíncrono de ENTER-MODE del driver RF vía
   Message Router (MSGR), no un store directo.** Evidencia dura: strings de assertion
   `Assert rfm_inst->wakeup_req.use_enter_mode failed` (seg27 @0x43d598) y
   `Assert msgr_send(&enter_mode_cnf.hdr, sizeof(enter_mode_cnf)) == E_SUCCESS failed`
   (@0x43d848). El commit de tech-enter (`0xd81dfecc`) recorre los módulos per-tech
   registrados (`0xca733d10[tech]`) y dispara sus callbacks; uno de ellos manda el
   `enter_mode` al RF y, **al recibir `enter_mode_cnf`**, la máquina de estados avanza el
   modo de la sesión a `2`. **FACT (strings) + INFERENCE (que ese cnf es lo que pone `0xc=2`).**

4. **Consecuencia para la secuencia:** el "PASO 0" que falta **no es un sub_command DIAG
   separado tipo SESSION_START**; es que **el propio TECH_ENTER debe COMPLETARSE
   (enter-mode confirmado) antes de que el flag `@0xca7897b0[tech]` pase a 1**. La causa
   más probable de que en tu caso no complete: (a) el `enter_mode_cnf` del RF no llega
   (RF/cal no arrancado en tu contexto), o (b) falta un TLV del TECH_ENTER que selecciona
   el modo/scenario correcto. Detalle y bytes en §4–§5.

---

## 1. QUIÉN ESCRIBE EL FLAG `@0xca7897b0` Y EL GATE `session->0xc==2` — FACT (reconfirmado)

### 1.1 Escritor de ENTRADA (pone el byte = 1): `0xd81e5cec`
```
d81e5cec  <ENTER-COMMIT>
d81e5d08:  r16 = r0 ; r2 = memw(r0+#0x0)         ; session = *(arg+0)
d81e5d0c:  if (r2 != 0) jump 0xd81e5d1c          ; Puerta A: session != NULL
d81e5d1c:  r3 = memw(r2+#0xc)
d81e5d20:  if (r3 != 2) jump 0xd81e5d70          ; <<< Puerta B: session->0xc == 2 (bytes 2a e2 42 24)  FACT
d81e5d2c:  r17 = memub(r2+#0x12)                 ; r17 = tech-index (del TLV TECH)
d81e5d40:  r0 = memw(r17<<2 + ##0xcbf68508)      ; ctx per-tech (RAM)
d81e5d44:  r3 = #0x1
d81e5d54:  memb(r17<<3 + ##0xca7897b0) = r3      ; <<< STORE flag[tech] = 1  (ENTERED)   FACT
d81e5d5c:  r2 = memb(ctx + ##0x89a8)             ; post-check sobre el ctx per-tech (NO la sesión)
d81e5d60:  p0 = cmp.eq(r2,#0x7) ; if(p0) r0=#0x10 ; call 0xda01cdb0   ; else-tech => err 0x10
```
- Store `flag[tech]=1` en **0xd81e5d54** (`ad11e3f0`), condicionado a **`session->0xc==2`**
  en **0xd81e5d20**. Base flag-array = `0xca7897b0`, stride 8, índice = tech-index. **FACT.**
- **El `0x89a8` es del ctx per-tech `0xcbf68508[tech]`, no de la sesión `*(arg+0)`.** (Corrige
  una ambigüedad del reporte previo: hay DOS punteros distintos en juego.) **FACT.**

### 1.2 Escritor de SALIDA (pone el byte = 0): `0xd81e5e18`
```
d81e5e24:  r18 = memub(r0+#0x1c)                 ; tech-index
d81e5e50:  r16 = memw(r18<<3 + ##0xcbad8a70)     ; tabla ctx per-tech (RAM)
d81e5fc4:  memb(r18<<3 + ##0xca7897b0) = 0       ; STORE flag[tech] = 0  (EXITED)   FACT
```

### 1.3 Gate RFTEST que devuelve 0x14 (lee el flag, compara ==1): `0xd8202224`
```
d8202220:  immext(#0xca789780)
d8202224:  r2 = memb(r21<<3 + ##0xca7897b0)      ; LEE flag[tech]
d8202228:  if (r2 != 1) jump 0xd8202234          ; si != 1 => rama alterna => status 0x14   FACT
```

### 1.4 Sin xref estático a los commits — se registran en runtime  (FACT)
Búsqueda de puntero crudo a `0xd81e5cec` y `0xd81dfecc` en los 10 MB: **0 hits** (verificado).
Se invocan por **callr** sobre records per-tech poblados en boot:
- `0xd816d658` (rama tech!=6) hace `call 0xd814e6ac` (obtiene la sesión desde las tablas
  RAM `0xcbad8a20/40`) y luego `callr r17` (el commit per-tech). **FACT.**
- `0xd81dfecc` recorre `0xca733d10[tech]` y hace `callr memw(record+4)` por cada módulo
  registrado (0xd81e00e0/0xd81e0108/0xd81e0138). **FACT.**
=> La tabla de módulos per-tech (records en `0xca733c00`/`0xca733d10`, `0xcbf68508`) se
llena al arrancar cada driver de tech. **Si el módulo LTE no se registró/inicializó en tu
contexto, el commit no corre y `session->0xc` nunca llega a 2.** **INFERENCE fuerte.**

---

## 2. QUÉ ES `session->0xc` REALMENTE — es el MODO, no un "listo" — FACT

Reconstruido de todos los sitios que leen/comparan `*(session)+0xc` en el state-machine
(archivo `_clade_full_dis.txt`):

| VA | Instrucción | Interpretación |
|----|-------------|----------------|
| `d81e5d20` | `if (memw(session+0xc) != 2) skip store` | enter exige **modo==2** |
| `d81e01ac` | `p0 = cmp.eq(memw(session+0xc), 0x13)` | compara contra modo 0x13 |
| `d81e023c` | `if (memw(session+0xc) != 0x13) skip` | idem |
| `d81e0a60` | `if (memw(session+0xc) == 0) skip` | modo 0 = inactivo |
| `d81e0360` | switch sobre el valor: `cmp.gt(v,2)` / `==1` / `==2` / `==3` | **enum de estados 0..3+** |
| `d81dff14` | `r16 = memw(session+0xc)` → `r16-1 <= 0x13` → indexa `0xca733d10` | usado como índice 1..0x14 |

Conclusión (FACT del uso): `session->0xc` es un **enum de estado/modo de la sesión FTM-RF**
(0=inactivo, 1=…, 2=activo/running, 3=…, 0x13=otro). El path RFTEST sólo habilita el flag
per-tech cuando la sesión ya está en **modo 2**. **TECH=1 fija `session->0x12` (tech-index),
que es un campo DISTINTO** (`memub(session+0x12)`, leído en d81e5d2c/d81e0230/d81e0250).
Por eso mandar TECH=1 no cambia `session->0xc`. **FACT.**

---

## 3. RASTREO HACIA ATRÁS AL "handler/sub_command" — resultado: NO es un sub_command DIAG

Rastreé desde el store `session->0xc=2` hacia atrás y **no hay un handler de sub_command
que lo escriba**. La cadena real es:

```
DIAG 0x4b/0x0b, subsys_cmd_code @0x02 = 0x27 (LTE)  ── §ftm_subsys_activate.md
   → handler común 0xd8150ed8 (path f3c, cmd!=0x14)  [FACT]
   → dispatch RFDEBUG 0xd816d2d4: sub_command @pkt+4 (u16), tabla 0xca65b414 stride 0xc,
     gate sub<=0x15, callr slot[sub].handler   [FACT]
   → slot 0x0d = TECH_ENTER_EXIT → handler 0xd8174758   [FACT]
   → 0xd8174758: r18=memub(request+0x12)=tech ; map 0xd8169618 → tech-index ;
     tabla per-tech stride 0x1c ; para tech!=6 → call 0xd816d658   [FACT]
   → 0xd816d658: call 0xd814e6ac (get session) ; callr r17 (commit per-tech)   [FACT]
   → commit = 0xd81dfecc / 0xd81e5cec (registrados en runtime)   [FACT]
   → 0xd81dfecc recorre módulos 0xca733d10[tech] y dispara sus callbacks (callr)   [FACT]
   → un callback manda ENTER_MODE al RF (msgr_send enter_mode_cnf)   [FACT strings]
   → al confirmarse enter_mode, la SM sube session->0xc a 2   [INFERENCE]
   → recién entonces 0xd81e5cec ve session->0xc==2 y escribe flag[tech]=1   [FACT del gate]
```

**No hay comando "SESSION_START"/"RF_INIT" separado que ponga `session->0xc=2`.** El modo 2
es un **side-effect del propio TECH_ENTER** cuando su handshake enter-mode con el RF se
completa. **FACT (ausencia de store por sub_command) + INFERENCE (side-effect del cnf).**

### 3.1 Strings/format que anclan la interpretación (FACT)
- TECH_ENTER_EXIT UNPACK: `[FTM.RFDEBUG][TECH_ENTER_EXIT][UNPACK]:[%3d][ %12s ][ %12d ]`
  (@0xc37beef0) → TLVs `{field_id, name, value}`; params g16 = {SUB, TECH, SCENARIO}.
- `Assert rfm_inst->wakeup_req.use_enter_mode failed` (seg27 @0x43d598).
- `Assert msgr_send(&enter_mode_cnf.hdr,…) == E_SUCCESS failed` (seg27 @0x43d848).
Estos dos últimos son la prueba de que la entrada de modo RF es un **request/cnf por MSGR**,
i.e. asíncrono, y no un simple `memw(session+0xc)=2`.

---

## 4. TASK #3 — TLVs de TECH_ENTER: ¿falta alguno? ¿importan SUB/SCENARIO?

Del handler 0xd8174758 y el mapeo, lo que efectivamente se consume del request:
- `request+0x12` = **TECH** (byte) → `0xd8169618` → tech-index → selecciona el módulo
  per-tech y termina siendo `session->0x12`. **Es el que ya mandás (TECH=1).** **FACT.**
- `request+0x16..0x19` y `request+0x1a` = leídos SÓLO en la rama **tech==6** (d81747c8..d8174838).
  Para LTE (tech!=6) **no** entran por ahí. **FACT.**
- **SUB** (field 1) y **SCENARIO** (field 3): el UNPACK los imprime, pero en el path LTE
  del commit **no vi que ninguno escriba `session->0xc`**. SUB selecciona sub-instancia
  (single/dual SIM); SCENARIO parametriza el modo. **INFERENCE:** su valor no cambia el
  hecho de que `0xc` lo pone el enter_mode_cnf; a lo sumo SCENARIO influye en QUÉ enter_mode
  se pide. **No hay un 4º TLV oculto que "prenda" el estado**; el estado lo prende el RF.

=> **Con SUB=0, TECH=1, SCENARIO=0 tus TLVs están completos para el UNPACK.** El problema
no es un TLV faltante sino que el **commit enter-mode no completa** (§0.3, §5). **INFERENCE fuerte.**

> Único matiz verificable en vivo: probá **SCENARIO distinto** (p.ej. 1) y **SUB** acorde a
> tu nº de SIM; si el módulo LTE exige un scenario concreto para pedir el enter_mode, eso
> podría ser lo que falta. Es la única variable de TLV que razonablemente afecta el estado.

---

## 5. SECUENCIA BYTE-A-BYTE PARA ABRIR EL GATE

Header FTM (subsys_cmd_code @0x02 = 0x27 = LTE; ver ftm_subsys_activate.md). Tu layout
(`4b 0b <ftm_cmd@0x02> <sub@0x04> <ntlv@0x06> <TLVs>`) es correcto para caer en el path real.

### PASO 1 — TECH_ENTER (LTE), sub_command RFDEBUG = 0x000d, TECH=1
```
4B 0B  27 00  0D 00  03 00 \
   01 00 04 00 00 00 00 00 \      ; TLV SUB       (field 1, len 4, val 0)
   02 00 04 00 01 00 00 00 \      ; TLV TECH=1    (field 2, len 4, val 1 = LTE index)   FACT
   03 00 04 00 00 00 00 00        ; TLV SCENARIO  (field 3, len 4, val 0)
```
Concatenado:
```
4B 0B 27 00 0D 00 03 00 01 00 04 00 00 00 00 00 02 00 04 00 01 00 00 00 03 00 04 00 00 00 00 00
```
Efecto esperado: dispara el commit per-tech → pide ENTER_MODE al RF. **Si el RF confirma
(`enter_mode_cnf`), `session->0xc` pasa a 2 y el mismo commit escribe `@0xca7897b0[LTE]=1`.**

### VERIFICACIÓN (imprescindible, en vivo)
- Status byte de la rsp de TECH_ENTER = **0** ⇒ commit ok (enter-mode confirmado).
- Si status ≠ 0 o RFTEST sigue en 0x14 ⇒ el enter-mode **no** completó (RF no listo).

### PASO 2 — si TECH_ENTER no completa (session->0xc no llega a 2)
El estado 2 depende del RF. Acciones (barrer/observar rsp; **no hay un único sub_command
mágico**, es dependencia de estado del driver):
1. **Reintentá TECH_ENTER con SCENARIO=1** (y SUB acorde a tu SIM). El scenario puede
   condicionar el enter_mode que se pide. (Única variable de TLV con chance de mover el estado.)
2. **Asegurá que el subsistema RF/cal esté arrancado** antes (FTM mode online). Si el modem
   está en un estado donde el RF driver no procesa `enter_mode_cnf`, `session->0xc` nunca
   sube a 2 por diseño. Esto es lo que el reporte previo llamaba "PASO 0": **no es un
   comando DIAG, es que el RF debe estar en condiciones de confirmar el enter-mode.**
3. Usá **COMMAND_CAPABILITY** (bajo 0x27, sub UNKNOWN de {0,5,6}) para resolver el enum de
   sub_command y confirmar en vivo cuál es TECH_ENTER si 0x0d del RFDEBUG no aplicara al
   path 0x27 (RFDEBUG y RFTEST son dispatchers distintos; ver §nota).

### PASO 3 — RFTEST (RADIO_CONFIG / RX_MEASURE / IQ_CAPTURE)
Con `@0xca7897b0[LTE]==1`, el gate `0xd8202228 (==1)` deja pasar y los RFTEST (0x1000–0x3FFF)
dejan de dar 0x14. sub_command numérico de cada RFTEST = UNKNOWN estático → COMMAND_CAPABILITY.

> **Nota de dispatcher (importante):** el TECH_ENTER que llega al gate `session->0xc==2`
> está en el dispatcher **RFDEBUG** (tabla `0xca65b414`, slot 0x0d). Verificá en vivo que tu
> ruta 0x27→RFTEST use ese mismo commit. Si tu path RFTEST tiene su PROPIO enter, el enum de
> su sub_command "ENTER" sale de COMMAND_CAPABILITY (P1 de tech_enter_decoded.md §7).

---

## 6. FACT / INFERENCE / UNKNOWN (con VAs)

**FACT (verificado en clade_dec_full.bin / _clade_full_dis.txt):**
- Gate de entrada: `0xd81e5d20` `if (memw(session+0xc) != 2) skip` (bytes `2a e2 42 24`).
- Store flag: `0xd81e5d54` `memb(tech<<3 + 0xca7897b0) = 1` (r3=#1 @0xd81e5d44). Exit: `0xd81e5fc4` =0.
- Gate RFTEST: `0xd8202224` lee `memb(tech<<3 + 0xca7897b0)`; `0xd8202228` `if(!=1) → 0x14`.
- **NO existe store estático `(session+0xc)=2`** sobre la sesión (~0x89b0, con `+0x89a8`):
  barrido completo de los 10 MB; todos los `(rX+0xc)=#2` son pila / colas / tablas / HW-capture.
- Inicializador de sesión `0xd81df874` deja `session+0xc = 0` (sólo pone `+0x4=+0x8=0x15`).
- `session->0xc` es un enum de estado (comparado ==2/==0x13/==0/switch 1..3): VAs d81e5d20,
  d81e01ac, d81e023c, d81e0a60, d81e0360, d81dff14.
- Commits de tech-enter (0xd81e5cec, 0xd81dfecc) **sin xref/puntero estático** (0 hits): se
  registran en runtime; se invocan por `callr` desde 0xd816d658 / 0xd81e00e0.
- Dispatch: DIAG 0x27 → 0xd8150ed8 (path f3c) → RFDEBUG 0xd816d2d4 (tabla 0xca65b414,
  gate sub<=0x15, callr) → slot 0x0d handler 0xd8174758 → 0xd816d658 → commit.
- Strings RF enter-mode (seg27): `use_enter_mode` @0x43d598; `msgr_send(&enter_mode_cnf…)`
  @0x43d848 ⇒ entrada de modo RF = request/cnf por MSGR (asíncrono).

**INFERENCE:**
- `session->0xc = 2` se alcanza como **side-effect del ENTER_MODE confirmado (enter_mode_cnf)**
  disparado por el commit de TECH_ENTER, no por un store directo ni por un sub_command aparte.
- El "PASO 0" no es un comando DIAG: es que el **driver RF esté en condiciones de confirmar
  el enter-mode** (FTM/RF online). Si no, el commit no sube `0xc` a 2 y el flag queda en 0.
- SUB/SCENARIO no "prenden" el estado; SCENARIO a lo sumo elige qué enter_mode se pide.

**UNKNOWN (requiere RAM en vivo o emular el arranque RF):**
- El código exacto del callback que envía `enter_mode` y la máquina que setea `session->0xc=2`
  (vive en un módulo per-tech registrado en runtime; no hay store estático que fijarlo).
- Si algún TLV (SCENARIO/SUB con cierto valor) es necesario para que el enter_mode se solicite.
- El enum numérico de sub_command de cada RFTEST y si el path 0x27 usa su propio "ENTER"
  (resolver con COMMAND_CAPABILITY en vivo).
- El valor de tech-index para NR5G.

---

## 7. Reproducir
```
/tmp/modemre/dis.sh 0xd81e5cec 0xc0     # enter-commit: gate session->0xc==2 + store flag=1
/tmp/modemre/dis.sh 0xd81df874 0x90     # init de sesión: deja session->0xc = 0
/tmp/modemre/dis.sh 0xd81dfecc 0x120    # commit que recorre modulos per-tech (0xca733d10)
/tmp/modemre/dis.sh 0xd816d658 0xa0     # get session (0xd814e6ac) + callr commit per-tech
/tmp/modemre/dis.sh 0xd81e0360 0x90     # switch sobre session->0xc (enum de estados 0..3)
/tmp/modemre/dis.sh 0xd8202200 0x50     # gate RFTEST: memb(tech<<3+0xca7897b0)==1
# Barrido que prueba la ausencia de store (session+0xc)=2:
grep -E 'mem[whb]\(r[0-9]+\+#0xc\) *= *#0x2' /tmp/_clade_full_dis.txt
# Strings enter-mode:
python3 - <<'PY'
d=open('/tmp/modemre/seg27_dec.bin','rb').read()
for o in (0x43d598,0x43d848): print(hex(o), d[o:o+90].split(b'\x00')[0])
PY
```
