# cal_mode_trigger — QUÉ comando/secuencia DIAG-FTM dispara el flujo que pone `0xcbf4f740=2` (RF cal mode)

**Target:** SM6375 / Moto G82 5G · MPSS.HI.4.3.4-00494-MANNAR_GEN_PACK-1.24452.133 · Hexagon/QDSP6 v66
**Binario primario:** `/tmp/modemre/clade_dec_36m.bin` (VA base `0xd8000000`, código válido `0xd8000000..~0xda800000`).
**Rodata/strings:** `seg27_dec.bin` (VA base `0xce480000`, TODO rodata — verificado, sin código), `modem.b21` (VA `0xc3553000`).
**Disasm autoritativo:** `/tmp/full36.txt` (0xd8000000..0xda800000). Herramientas: `dis36.sh`, `find_refs.py`, `resolve_callr.py`, `_fns.pkl`.

**Leyenda:** **FACT** = byte/instrucción/edge leído con VA · **INFERENCE** = deducción con base dura · **UNKNOWN** = sólo runtime/RAM/fuera del ELF.

---

## 0. TL;DR — RESPUESTA DIRECTA

1. **NO existe en el MBN ningún `msgr_send` que dispare el cal mode, ni ninguna función FTM que mande un
   MSGR de "enter cal / rf_mode" al driver residente.** Razón dura (FACT): la función `msgr_send`
   (y toda la máquina MSGR: `msgr_table`, `E_NO_ROUTE`, el par `LTE_ML1_RFMGR_ENTER_ONLY_REQ` /
   `RFA_RF_LTE_ENTER_MODE_CNF`) vive en la **región PAGINADA `0xce4xxxxx..0xcexxxxxx`**, que está a **~163 MB**
   del código CLADE `0xd8xxxxxx`. El `J2_call`/`J2_jump` de Hexagon tiene alcance PC-relativo de **±8 MB**
   (imm 22-bit `<<2`). ⇒ **el código FTM `0xd8xxxxxx` NO PUEDE llamar a `msgr_send` por PC-rel**, y de hecho
   **CERO refs** a los name-strings de los UMID enter/RFMGR/RFA en todo el disasm de 36 MB. **FACT.**
2. **El disparador REAL desde el código FTM es un `callr` a un puntero de función registrado en RAM**, NO un
   `msgr_send`. El commit FTM per-tech `0xd81dfecc` hace `callr memw(0xca733d10[tech]+4)` (@0xd81e00e0/
   0xd81e0108/0xd81e0138) y el RFDEBUG-0x0d `0xd816d658` hace `callr r17`/`callr r16` (@0xd816d6d8/0xd816d74c).
   Esos punteros apuntan al **driver FTM-LTE residente/paginado** (`ftm_lte_*` / `rflte_ftm_*`), que es quien
   escribe `0xcbf4f740=2`. **FACT (los `callr`) + INFERENCE (destino = driver paginado).**
3. **El comando DIAG origen del flujo es el commit FTM per-tech**, alcanzado por **TECH_ENTER (RFDEBUG sub
   `0x000D`) sobre `ftm_cmd_id=0x27` (FTM_LTE)** → `0xd816d658` → `0xd81dfecc` → `callr` al módulo LTE. **FACT.**
4. **0x10F (0xd8263e2c) NO manda ningún MSGR ni dispara el cal-driver de modo directo.** Es enable/attach de la
   instancia RF por-tech; setea `gp+0x1ed5`/`gp+0x1ed6=1`; su call-graph limpio NO alcanza ni el creator del
   gate(b) (`0xd8284eac`) ni un store del `2`. (La "reachability" positiva de un BFS ingenuo es espuria: pasa por
   `alloc`/`memcpy`/`log` compartidos en cadenas de 100+ saltos, sin semántica de control.) **FACT.**
5. **NO existe un UMID de "enter cal mode" que salga del MBN.** El único par enter/cnf documentado
   (`LTE_ML1_RFMGR_ENTER_ONLY_REQ` ↔ `RFA_RF_LTE_ENTER_MODE_CNF`, b21 @0xc404e0bc/0xc404dce1) es del path
   **ONLINE ML1↔RFA**, y ese código es paginado; **no lo emite el path FTM** (el path FTM va por `callr` directo).
   **FACT (identidad de módulos) + INFERENCE (por eso no aplica en FTM).**
6. **Secuencia en vivo más probable** para dejar `0xcbf4f740=2`: **(A)** poner el módem en modo servicio-FTM
   (QMI-DMS `set_operating_mode`→OFFLINE/FTM **o** arranque en factory-test) para que `rfm_init`+MCPM+el
   driver de cal residente estén activos; **(B)** `TECH_ENTER(LTE)` (dispara el `callr` al módulo FTM-LTE);
   **(C)** `RADIO_CONFIG` con BAND+EARFCN+BW (completa el activate). El `2` lo escribe el driver residente al
   correr el `callr` de B/C. **INFERENCE fuerte + FACT (gates y `callr`).** **Verificar SIEMPRE por peek DIAG.**

---

## 1. POR QUÉ NO HAY `msgr_send` DE CAL EN EL CÓDIGO FTM (evidencia dura)

### 1.1 Arquitectura de segmentos: dos mundos que no se llaman por PC-rel (FACT)
Program headers (`findings/00_program_headers.txt`):
```
b13  0xc0d36000  R-E   (código nativo DIAG/kernel)
b21  0xc3553000  R--   (rodata: name-tables MSGR/UMID)
b25  0xcbf4f000  RW-   (gp/.sdata → gate(a) 0xcbf4f740, gate(b) 0xcbf56000)
b26  0xcc000000  RW-
b27  0xce480000  R--   (rodata PAGINADA: TODOS los asserts/strings msgr/ftm_lte/rf_mode)
```
- **b27/seg27 es `R--` (NO ejecutable)** y su descompresión `seg27_dec.bin` es **100 % rodata** (verificado:
  `0xce6cf8e3='rf_mode == (uint8)NR5G_L...'`, `0xce6e08a8='rflte_ftm_mc_wakeup: inv...'` — todo ASCII, sin
  `allocframe` en 4 MB). **FACT.**
- El código CLADE mapeado está en `0xd8000000..~0xda800000`. La distancia `0xd8000000 → 0xce480000` es
  **~0x9B80000 = 163 MB**. `J2_call` codifica un desplazamiento de **22 bits con signo `<<2` = ±8 MB**.
  ⇒ **imposible** que el código `0xd8` llame por PC-rel a nada en `0xce`. **FACT (aritmética del ISA).**

### 1.2 Ausencia de refs a UMIDs/strings MSGR desde el código FTM (FACT)
Barrido de `/tmp/full36.txt` (disasm completo 36 MB):
```
refs a ##0xce6699.. / ##0xce734.. (msgr E_NO_ROUTE / msgr_table strings) ..... 0
refs a ##0xc404e0bc (LTE_ML1_RFMGR_ENTER_ONLY_REQ name) ......................... 0
refs a ##0xc404dce1 (RFA_RF_LTE_ENTER_MODE_CNF name) ............................ 0
refs a ##0xc404dcc9/dfff (RFMGR_START_REQ / WAKEUP_REQ names) ................... 0
```
Los ~6172 refs a `##0xc40xxxxx` que SÍ existen son **format-strings de log/assert** de OTRO subsistema
(cluster `0xd8b9xxxx`, p.ej. `r1=##0xc403a925; r0=add(r29,#0x1c)` alimentando un logger), **no** sends MSGR.
**FACT.** ⇒ **El código FTM del MBN no construye ni envía ningún UMID de enter/cal/rf-mode.**

### 1.3 Conclusión (FACT)
`msgr_send` y el par enter/cnf son **exclusivos del path ONLINE ML1↔RFA, en código PAGINADO**. El MBN FTM
**no manda MSGR de cal**. Descarta la hipótesis "una función FTM manda un MSGR al driver de cal". El edge
real hacia el driver residente es por **`callr` de puntero registrado en RAM** (§2), no por MSGR.

---

## 2. EL DISPARADOR REAL: `callr` per-tech desde el commit FTM (FACT)

### 2.1 El comando DIAG y su cadena (FACT)
```
DIAG:  4B 0B  27 00  0D 00 ...        ; SUBSYS_CMD_F, SUBSYS_FTM, ftm_cmd_id=0x27 (FTM_LTE), RFDEBUG sub=0x000D (TECH_ENTER)
   → ftm_cmd table @0xc37bc828 [0x27] = stub 0xd814e534  → handler FTM-LTE runtime
   → RFDEBUG-0x0d handler  0xd816d658
        d816d6d8:  callr r17            ; (parse/prep del sub-cmd, puntero runtime)
        d816d74c:  callr r16            ; (idem)
   → COMMIT per-tech  0xd81dfecc
        d81dff14:  r16 = memw(r2+#0xc)                 ; session->0xc  (MODO de sesión; exige ==2 aguas abajo)
        d81dff2c:  r21 = add(##0xca733d10, r21<<2)     ; &tabla_modulos[tech]
        d81dffc4:  r1  = memw(r20<<2 + ##0xca733d10)   ; record = tabla[tech]
        d81e00d0:  r2  = memw(r3+#0x4)                 ; fn = record->+4
        d81e00e0:  callr r2                            ; <<< INVOCA EL MÓDULO FTM-LTE (driver residente)
        (ramas gemelas: callr @0xd81e0108, @0xd81e0138)
```
**FACT (disasm byte-exact 36 MB).** El `callr memw(record+4)` es **el único edge desde el MBN hacia el
driver FTM-LTE**. `tabla[tech].fn` se **puebla en runtime** al init del módulo per-tech (binding dinámico;
no hay store estático legible del puntero). **FACT (lectura+`callr`) + INFERENCE (destino=driver paginado
que setea `rf_mode`).**

### 2.2 El driver invocado escribe `0xcbf4f740=2` (INFERENCE con anclas FACT)
El módulo FTM-LTE (`ftm_lte_*`, `rflte_ftm_mc_wakeup` @0xce6e08a8, `rflte_mc_carrier_activate` @0xce6e72c0)
corre en el pool paginado y setea el `rf_mode` de LL1. Anclas (FACT, seg27):
```
0xce6cf8e3  Assertion (p_tx_ctx->tx_info.rf_mode == (uint8)NR5G_LL1_CAL_FTM_RF_MODE_CAL) failed
0xce6cf902  FTM_RF_MODE_CAL) failed
0xce5bd52b  Assertion (lte_LL1_get_ul_ftm_cal_mode(cxn_id) > 0) failed
```
El literal `2` (=`FTM_RF_MODE_CAL`) **no está en ningún store del MBN** (barrido exhaustivo previo re-verificado:
únicos writers de `0xcbf4f740` = `0xd81bd494`/`0xd81bd5fc` con `mux(pred,1,0)`=0/1, reset `=0` @0xd81bd7c8).
⇒ lo escribe el driver residente al correr el `callr`. **FACT (ausencia del store 2 en MBN) + INFERENCE.**

---

## 3. RE-ANÁLISIS DE 0x10F (0xd8263e2c) — ¿manda un MSGR de cal? NO (FACT)

Disasm/call-graph (re-verificado en 36 MB):
```
0xd8263e2c
 ├─ 0xd8055528  get_rf_inst(tech)  : r0=memw(##0xca79a8e0 + tech<<2)
 ├─ 0xd82726c4  get_tech_obj(id)   : r0=memw(memw(##0xca79a850) addasl id,2 +0x34)
 ├─ 0xd8471498  memb(gp+#0x1ed5)=1  (0xcbf50ed5)      <-- flag FTM enable, NO 0x740
 ├─ 0xd84714a0  memb(gp+#0x1ed6)=1  (0xcbf50ed6)      <-- flag FTM enable, NO 0x740
 ├─ 0xd81578f0  (init/notify)
 └─ 0xd829df1c → 0xd81bf0e8  (toca arrays RF 0xca6e3ab8; NO gate, NO msgr)
```
- **NO hay `msgr_send`** en su subtree (imposible por §1) ni `callr` a un puntero de cal. **FACT.**
- **NO escribe `0xcbf4f740` ni `0xcbf56000`.** Sólo `gp+0x1ed5/0x1ed6` (cluster distinto). **FACT.**
- El BFS ingenuo lo marca "alcanza 0xd8284eac", pero el path es **espurio**: 130+ saltos por
  `0xd8083c78`/`0xd8058c70`/alloc/log compartidos (span de scan que desborda límites de función). El commit
  `0xd81dfecc` **NO** alcanza el creator del gate(b) ni siquiera con ese scan permisivo. **FACT.**

**Conclusión:** 0x10F **no es** el disparador del cal mode y **no manda MSGR**. Es enable de instancia RF
por-tech. (Sí puede ser un pre-requisito de "habilitar" la tech, pero no abre el gate.) **FACT.**

---

## 4. ¿EXISTE UN UMID/REQ DE "ENTER CAL MODE"? (FACT + INFERENCE)

- **Par enter/cnf que SÍ existe (b21, FACT):** `LTE_ML1_RFMGR_ENTER_ONLY_REQ` @0xc404e0bc ↔
  `RFA_RF_LTE_ENTER_MODE_CNF` @0xc404dce1 (más START/WAKEUP/SLEEP/SCRIPT_EXEC/…). **Es el handshake ONLINE
  ML1→RFA**, no un "enter CAL". Su emisor/receptor son tasks paginados. **FACT (names) + INFERENCE (roles).**
- **NO hay un UMID `*_CAL_*` / `*_FTM_*` / `*_ENTER_CAL_*` legible** que el MBN FTM emita (§1.2: cero refs).
  El "enter cal" del path FTM **no es un mensaje**: es el `callr` per-tech (§2). **FACT (ausencia) + INFERENCE.**
- **Valor numérico de cualquier UMID = UNKNOWN estático** (constante compilada `MSGR_ID_VAL(NAME)`; sólo
  resoluble en vivo con `msgr_query_umid` o loggeando `UMID=0x%08X` @0xce68146b). **UNKNOWN.**

⇒ **No hay forma de "mandar el req de enter-cal" desde DIAG** porque tal req no existe como comando; el modo
cal se enciende como **side-effect del `callr` FTM-LTE** cuando el driver residente corre (bajo TECH_ENTER +
RADIO_CONFIG con el HW/MCPM listos). **INFERENCE fuerte.**

---

## 5. ¿EL GATE SE SETEA POR ARRANQUE FACTORY-TEST / QMI-DMS? (INFERENCE con base FACT)

- **Barrera de modo dura (FACT, seg27):** `Attempt to access FTM variables in ONLINE MODE` (0xce6d59e0 x3),
  `=MMOC= Phone should be in online mode` (0xce64ebb0), `FTM_RF_MODE_CAL` (0xce6cf902). Existe un flag global
  FTM/CAL vs ONLINE que particiona el pipeline RF. **FACT.**
- **Cadena de cambio de modo (FACT strings + INFERENCE de secuencia):**
  QMI-DMS `set_operating_mode(oprt_mode)` (`operating_mode` @0xc3789c68, `oprt_mode_req` @0xc37700f9) →
  `cm_ph_cmd_pref_change_req` (@0xc40cc45e) → **MMOC** → arranca/para tasks RF/ML1 y fija el flag FTM/ONLINE.
  El subsys DIAG **0x5B (91)** (modem-mgr/QMI, handlers 0xda019xxx) es el candidato de dispatch DMS interno.
  **FACT (strings) + INFERENCE (cadena canónica Qualcomm).**
- **Efecto sobre el gate:** poner el módem en **servicio-FTM/CAL** arranca `rfm_init` (evita
  `RFM_INIT WAS NEVER CALLED` @0xce6de1b0) y **MCPM** (evita `MCPM not turned ON yet` @0xce748848), y hace que
  el `callr` FTM-LTE del commit (§2) corra por la rama que setea `rf_mode=CAL`. **INFERENCE fuerte.**
- **UNKNOWN:** el subsys/UMID/payload EXACTO de `set_operating_mode` en este build; si el "cal mode" de tu HW
  se entra por DMS o por el mismo arranque en factory-test. El store `0xcbf4f740=2` no está en el ELF, así que
  el instante exacto sólo se confirma por peek. **UNKNOWN.**

---

## 6. SECUENCIA EN VIVO MÁS PROBABLE (accionable)

Estado FTM "pelado": `0xcbf4f740=0`, `0xcbf56000=NULL`, `0xca79c494=NULL` → gate(a)!=2 ∧ gate(b)==NULL →
`0xd81bd018` salta el APPLY (@0xd81bd168) → `0xca79c494` NULL → getter `0xd827923c` → `err_fatal` → SSR. **FACT.**

```
PASO A — MODO SERVICIO-FTM/CAL  (arranca rfm_init + MCPM + el driver de cal residente)
   Camino más probable: QMI-DMS set_operating_mode -> OFFLINE/FTM   (subsys DIAG 0x5B / QMI-DMS)
   [o el arranque del device directamente en factory-test]
   Efecto: fija el flag FTM/CAL, arranca los tasks RF/ML1 en modo test y deja listo el driver residente
           (el mismo que, al correr el callr FTM-LTE de B/C, escribe 0xcbf4f740=2).
   Prereqs (FACT): RFM_INIT (@0xce6de1b0), MCPM ON (@0xce748848), NV/cal cargada.

PASO B — TECH_ENTER (LTE)   [ftm_cmd 0x27, RFDEBUG sub 0x000D]  -> commit 0xd81dfecc -> callr modulo FTM-LTE
   4B 0B 27 00 0D 00 03 00
      01 00 04 00 00 00 00 00     ; SUB=0
      02 00 04 00 01 00 00 00     ; TECH=1 (LTE)
      03 00 04 00 00 00 00 00     ; SCENARIO=0
   (El callr @0xd81e00e0 entra al driver residente; ahí se setea rf_mode/gate si el HW esta listo.)

PASO C — RADIO_CONFIG (BAND + EARFCN + BW + RX_CARRIER)   [unpacker 0xd8183f30, field-tbl @0xc37c0290]
   4B 0B 27 00 <SUB_RC:u16 LE> 05 00
      01 00 04 00 00 00 00 00     ; RX_CARRIER (fid 1)  = 0
      19 00 04 00 01 00 00 00     ; TECH_MODE  (fid 25) = 1 (LTE)
      05 00 04 00 03 00 00 00     ; BAND       (fid 5)  = 3 (ej B3)
      06 00 04 00 27 06 00 00     ; CHANNEL    (fid 6)  = EARFCN 1575
      07 00 04 00 20 4E 00 00     ; BANDWIDTH  (fid 7)  = 20000 kHz
   Con A hecho, el carrier-activate 0xd81bd018 pasa gate(a)==2 y gate(b)!=0 -> APPLY 0xd8273b2c ->
   setter 0xd8279264 -> memw(0xca79c494)=0xca6e3b88.

PASO D — VERIFICAR EN VIVO (peek DIAG de memoria; ÚNICA fuente de verdad, el store del 2 no esta en el ELF):
   memb(0xcbf4f740) == 2      (gate a: RF cal mode)
   memw(0xcbf56000) != 0      (gate b: ctx RF creado)
   memw(0xca79c494) != 0      (carrier ptr poblado)

PASO E — IQ_CAPTURE / RX_MEASURE   (recien aqui; getter 0xd827923c != NULL, sin SSR)
```
Orden **A→B→C obligatorio**. **FACT (gates/setter) + INFERENCE (A = set-mode servicio-FTM; el `callr` de B/C
es quien materializa el 2 vía el driver residente).**

---

## 7. ¿ES POSIBLE DESDE DIAG-FTM "PURO"? — VEREDICTO

- **No hay un comando DIAG-FTM que escriba `0xcbf4f740=2` directamente** (ni 0x10F, ni 0x4F5/0x4F7, ni ningún
  stub de la tabla `@0xc37bc828`). El store del `2` está **fuera del MBN** (driver residente/paginado). **FACT.**
- **No hay un `msgr_send` de cal en el MBN** (imposible por rango PC-rel + cero refs). Descartada la vía "mandar
  un mensaje MSGR al driver". **FACT.**
- **SÍ hay un disparador DIAG efectivo**: el **`callr` per-tech del commit FTM (0xd81dfecc)** que corre bajo
  **TECH_ENTER(LTE)+RADIO_CONFIG**, **siempre que el módem esté en servicio-FTM/CAL con RFM+MCPM arrancados**
  (PASO A). Es decir: **el gate se enciende como side-effect del bring-up FTM completo, no por un comando
  aislado.** **FACT (callr) + INFERENCE (side-effect).**
- **Conclusión operativa:** desde DIAG-FTM puro (sin poner antes el módem en servicio-FTM/CAL) **el gate NO se
  abre** → SSR. Con el PASO A previo (DMS set_operating_mode→FTM o arranque factory-test) + B + C, **el driver
  residente lo abre**. Verificación **obligatoria** por peek. **INFERENCE fuerte respaldada por los gates FACT.**

---

## 8. FACT / INFERENCE / UNKNOWN (con VAs)

**FACT**
- `msgr_send`/`msgr_table`/`E_NO_ROUTE` y el par `LTE_ML1_RFMGR_ENTER_ONLY_REQ`(0xc404e0bc)/
  `RFA_RF_LTE_ENTER_MODE_CNF`(0xc404dce1) están en región PAGINADA `0xce4xxxxx`; `J2_call` alcanza ±8 MB;
  `0xd8`→`0xce` = 163 MB ⇒ inalcanzable. Cero refs a esos name-strings en 36 MB de disasm.
- Commit FTM `0xd81dfecc`: `session->0xc` @0xd81dff14; `&0xca733d10[tech]` @0xd81dff2c; `fn=memw(record+4)`
  @0xd81e00d0; `callr r2` @0xd81e00e0 (+ gemelos @0xd81e0108/0xd81e0138). RFDEBUG-0x0d `0xd816d658`:
  `callr r17`@0xd816d6d8, `callr r16`@0xd816d74c. ftm_cmd table `@0xc37bc828[0x27]=0xd814e534` (FTM_LTE).
- 0x10F handler `0xd8263e2c`: setea `gp+0x1ed5`/`gp+0x1ed6=1` (0xcbf50ed5/ed6); NO toca `0xcbf4f740`/`0xcbf56000`;
  sin `msgr_send`/`callr` de cal. Callers del commit: 0xd81df7c0/0xd81e5d7c/0xd8223868/0xd828a150/0xd82fc858/
  0xd850858c/0xd86f4be0/0xd86f76f4.
- Gate(a) `0xcbf4f740` (byte): reader `0xd81bd160`+`if(!=2) jump 0xd81bd4b0` @0xd81bd168. Únicos writers:
  `0xd81bd494`/`0xd81bd5fc` (`mux 0/1`), reset `0xd81bd7c8` (=0). **Cero stores del literal 2 en 36 MB.**
- Gate(b) `0xcbf56000` (word): reader `0xd8286240`; creator `0xd8284cf4` (vtable ##0xc37c6d80); accessor
  `0xd8284eac`. `0xd8284eac←0xd8286290` (en `0xd828626c`); `0xd828626c`/`0xd8286290` **sin callers estáticos**
  (runtime/event-bound).
- APPLY `0xd8273b2c`←`0xd81bd2a8`; setter `0xca79c494`=`0xd8279264` (valor 0xca6e3b88); getter SSR `0xd827923c`.
- seg27_dec = 100% rodata (no ejecutable, b27 `R--`). Anclas cal: `rf_mode==NR5G_LL1_CAL_FTM_RF_MODE_CAL`
  @0xce6cf8e3, `FTM_RF_MODE_CAL` @0xce6cf902, `lte_LL1_get_ul_ftm_cal_mode(cxn)>0` @0xce5bd52b,
  `rflte_ftm_mc_wakeup` @0xce6e08a8, `rflte_mc_carrier_activate` @0xce6e72c0, `RFM_INIT WAS NEVER CALLED`
  @0xce6de1b0, `MCPM not turned ON yet` @0xce748848, `Attempt to access FTM variables in ONLINE MODE` @0xce6d59e0.
- QMI/modo: `operating_mode` @0xc3789c68, `oprt_mode_req` @0xc37700f9, `cm_ph_cmd_pref_change_req` @0xc40cc45e,
  `MODE_OFFLINE_IF_NOT_FTM` @0xc42318b8, `FTM_MODE` @0xc37bd180. Subsys DIAG 0x5B handlers 0xda019xxx.

**INFERENCE (base dura)**
- El disparador desde el MBN es el **`callr` per-tech del commit FTM** hacia el driver FTM-LTE residente/paginado,
  NO un `msgr_send`. Ese driver escribe `0xcbf4f740=2` (FTM_RF_MODE_CAL) y, al tocar el subsistema RF-instance,
  hace que el accessor `0xd8284eac` cree el ctx que puebla `0xcbf56000`.
- No existe UMID/req de "enter cal" emitible desde DIAG; el modo cal es side-effect del bring-up FTM completo.
- El modo servicio-FTM/CAL se entra por QMI-DMS `set_operating_mode`→FTM (subsys 0x5B / QMI-DMS) o por arranque
  factory-test; eso habilita RFM+MCPM+driver de cal.
- Secuencia A(set-mode FTM)→B(TECH_ENTER LTE, dispara callr)→C(RADIO_CONFIG BAND+EARFCN) deja `0xca79c494`!=NULL.

**UNKNOWN (sólo runtime / RAM / fuera del ELF)**
- VA del store `0xcbf4f740=2` (código del driver residente, ausente del MBN).
- Valor de `0xca733d10[LTE].fn` (si el callback está poblado) y la VA exacta del handler FTM-LTE en el pool paginado.
- UMID numérico de cualquier `*_REQ/_CNF` (`MSGR_ID_VAL`), incl. si hubiera alguno de cal.
- Subsys/UMID/payload EXACTO de `set_operating_mode` en este build; si el cal mode se entra por DMS o por boot factory.
- Valor en vivo de `0xcbf4f740`/`0xcbf56000`/`0xca79c494` (sólo por peek DIAG).

---

## 9. REPRODUCIR
```bash
cd /tmp/modemre
# 1) Ausencia de msgr_send/UMID de cal en el MBN (rango PC-rel + cero refs):
grep -cE "##0xce6699|##0xce734" /tmp/full36.txt            # 0  (msgr strings inalcanzables)
for u in c404e0bc c404dce1 c404dcc9 c404dfff; do grep -cE "##0x$u" /tmp/full36.txt; done  # todos 0
# 2) El callr per-tech (disparador real) del commit FTM:
./dis36.sh 0xd81dfecc 0x140 | grep -E "0xca733d10|memw\(r.*#0x4\)|callr"
./dis36.sh 0xd81e0020 0x180 | grep -E "callr"
./dis36.sh 0xd816d658 0x140 | grep -E "callr"
# 3) ftm_cmd table [0x27]=FTM_LTE:
python3 - <<'PY'
import struct;d=open('modem.b21','rb').read();B=0xc3553000;o=0xc37bc828-B
print('[0x27]',hex(struct.unpack('<I',d[o+0x27*4:o+0x27*4+4])[0]))  # 0xd814e534
PY
# 4) 0x10F no toca los gates ni manda cal:
./dis36.sh 0xd8263e2c 0x90 | grep -E "gp\+#0x1ed|callr|call 0x"
# 5) Gate readers + writers (2 no existe):
./dis36.sh 0xd81bd160 0x10       # gate(a) ==2
./dis36.sh 0xd8286240 0x30       # gate(b) !=0
grep -nE "gp\+#0x740\) = |0xcbf4f740" /tmp/full36.txt   # solo mux(0/1) + reset
# 6) creator gate(b) sin callers estaticos:
python3 find_refs.py 0xd828626c   # vacio -> runtime/event-bound
```
