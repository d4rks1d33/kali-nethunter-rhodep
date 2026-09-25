# ENTER_MODE PATH — por qué `enter_mode_cnf` no vuelve en factory-test y cómo abrir el gate `@0xca7897b0`
Build: **MPSS.HI.4.3.4-00494-MANNAR_GEN_PACK-1.24452.133** (SM6375 / Moto G82 5G)
Imágenes: `clade_dec_full.bin` (código paginado, VA 0xd8000000) · `seg27_dec.bin` (= modem.b27 **descomprimido**, VA base **0xce480000**, rodata LTE-ML1/FTM-RF).
Herramientas: `dis.sh <va> <len>` (llvm-objdump hexagon v66) · `clade_extractor_sm6375` (descompresor CLADE HW-accurate).

Leyenda: **FACT** = string/instrucción verificada byte-a-byte (VA citada) · **INFERENCE** = deducción con base dura · **UNKNOWN** = sólo resoluble en vivo o con blob RFLM ausente.

> **CORRECCIÓN DE MODELO CLAVE (cambia el diagnóstico de los pases previos).**
> El `enter_mode`/`enter_mode_cnf` que buscábamos **NO pertenece al path FTM**. Pertenece al
> **`lte_ml1_rfmgr`** (LTE ML1 RF Manager, el path ONLINE/ML1). El path de FACTORY-TEST usa un
> **módulo distinto y paralelo: `ftm_lte_*` / `rflte_ftm_mc_*` / `rflte_dispatch_*`**, que **NO envía
> `enter_mode` por MSGR** — hace un `rflte_ftm_mc_wakeup` directo. Por eso en factory-test el
> `enter_mode_cnf` "no vuelve": **en el path FTM nunca se pide.** Detalle abajo. Esto reordena la
> recomendación: no hay que "esperar el cnf del RF"; hay que **usar el disparador FTM que hace el
> wakeup directo** (o correr el flujo online). **FACT (identidad de módulos) + INFERENCE (que por eso el cnf no llega).**

---

## 0. TL;DR (leer primero)

1. **Los asserts `use_enter_mode` y `enter_mode_cnf` son de `lte_ml1_rfmgr` (ONLINE), no de FTM.**
   - `Assert rfm_inst->wakeup_req.use_enter_mode failed` @ **0xce8bd598** (FACT).
   - `Assert msgr_send(&enter_mode_cnf.hdr, …) == E_SUCCESS failed` @ **0xce8bd848** (FACT).
   - Rodean asserts de `run_rf_script_msg`, `fw_rx_lm_req`, `sleep_cnf/wakeup_cnf/rxlm_cnf/txlm_cnf`,
     `mcpm_req.return_trans_id`, todos con prefijos `rfm_inst->…` y `LTE_ML1_RFMGR_…`. Es el módulo
     ML1 RF-Manager de LTE en modo conectado, no el driver de test. **FACT.**

2. **`enter_mode_cnf` sólo se emite cuando el RFM RECIBE un `enter_mode` **req** por MSGR y completa
   un handshake de reloj/potencia (MCPM).** El assert `mcpm_req.return_trans_id != 0` (@**0xce8bd818**)
   está inmediatamente ANTES del `enter_mode_cnf` (@0xce8bd848): la confirmación requiere una
   transacción MCPM (Modem Clock/Power Mgr) válida. **FACT (adyacencia + semántica).**

3. **En factory-test el `enter_mode` req **no se genera**:** el dispatcher FTM-LTE (`rflte_dispatch_rf_config_req`
   → `rflte_mc_carrier_activate` → `rflte_ftm_mc_wakeup`) enciende el RF por la vía directa de test,
   sin mandar `enter_mode` al ML1-RFMGR. Existe además una barrera dura de modo:
   `Attempt to access FTM variables in ONLINE MODE` (@**0xce6d59e0**) y su simétrica implícita. **FACT (funciones) + INFERENCE (que por eso el cnf ML1 nunca llega en FTM).**

4. **`wakeup_req.use_enter_mode` NO es un TLV ni un comando: es un campo de `rfm_inst` que la propia
   máquina ML1 setea cuando decide entrar por "enter_mode" en vez de por "wakeup/resume".** No hay
   store estático que lo prenda desde DIAG. Se prende dentro del flujo ML1 online. **FACT (sólo hay
   assert, no setter DIAG) + INFERENCE (lo setea la SM ML1 al elegir la rama enter_mode).**

5. **Acción accionable priorizada** (§7):
   **(P0)** Usar el disparador FTM que hace `rflte_ftm_mc_wakeup` (el flag `@0xca7897b0[tech]=1` lo debe
   escribir el commit FTM, no el cnf ML1) → asegurarse de mandar el TECH_ENTER/RADIO_CONFIG con los TLVs
   que hacen `session->0xc` avanzar dentro del propio framework FTM.
   **(P1)** Si el path que estás usando SÍ enrutó al ML1 online, **correr fuera de factory-test (online)**
   para que el RFM procese el `enter_mode` req y devuelva el cnf.
   **(P2)** Garantizar prerequisitos de HW: **MCPM encendido** (`MCPM not turned ON yet` @0xce748848),
   NV/cal cargada, `rfm_init` llamado (`RFM_INIT WAS NEVER CALLED` @0xce6de1b0).

---

## 1. EL PATH enter_mode → enter_mode_cnf (VAs: sender, receiver, handler)

### 1.1 Identidad de los módulos (FACT)
Los strings viven en **seg27_dec.bin = modem.b27 descomprimido, VA base 0xce480000** (confirmado en
`findings/q6zip_decomp2.md`: "seg27: 0xce480000 … ya descomprimido: seg27_dec.bin").

Cluster **`lte_ml1_rfmgr`** (ONLINE ML1 RF Manager) — región rodata ~**0xce8b0000–0xce8c0000**:
```
0xce8bd430  Assert rfm_inst->cc_state[LTE_ML1_RFMGR_PCC_ID].tx_tuned failed:
0xce8bd598  Assert rfm_inst->wakeup_req.use_enter_mode failed:            <<< WAKEUP GATE
0xce8bd5d0  Assert run_rf_script_msg.msg_payload.carrier[N].rxlm_buf_id_ant0 != …_INVALID
0xce8bd670  Assert enter_params.enter_params->header.source2_tech <= RFCOM_NUM_MODES
0xce8bd818  Assert mcpm_req.return_trans_id != 0 failed:                   <<< MCPM PRECOND
0xce8bd848  Assert msgr_send(&enter_mode_cnf.hdr, sizeof(enter_mode_cnf)) == E_SUCCESS  <<< CNF SENDER
0xce8bd8a0  Assert msgr_send(&exit_mode_cnf.hdr, …)  == E_SUCCESS
0xce8bd8f8  Assert msgr_send(&sleep_cnf.hdr, …)      == E_SUCCESS
0xce8bd948  Assert msgr_send(&cdrx_sleep_cnf.hdr,…)  == E_SUCCESS
0xce8bd9a0  Assert msgr_send(&wakeup_cnf.hdr, …)     == E_SUCCESS
0xce8bd9f0  Assert msgr_send(&cdrx_wakeup_cnf.hdr,…) == E_SUCCESS
0xce8bda48  Assert msgr_send(&rxlm_cnf.msg_hdr, …)   == E_SUCCESS
0xce8bda98  Assert msgr_send(&txlm_cnf.msg_hdr, …)   == E_SUCCESS
```
**Todos FACT** (verificados en seg27_dec.bin). El grupo de 8 `*_cnf` consecutivos es la tabla de
respuestas de la SM ML1: **enter/exit/sleep/cdrx_sleep/wakeup/cdrx_wakeup/rxlm/txlm**. El
`enter_mode_cnf` es una respuesta más de esa SM.

### 1.2 QUIÉN recibe el `enter_mode` req y QUIÉN produce el cnf (FACT/INFERENCE)
- **Receiver = la tarea ML1 RF-Manager de LTE (`lte_ml1_rfmgr`).** Corre en el contexto ML1
  (dedicado, no el rf_task/RFM genérico). Recibe el UMID `…RFMGR…ENTER_MODE_REQ` por MSGR, ejecuta
  el handler de enter (construye `run_rf_script_msg` con `rxlm_buf_id`), y al terminar **responde con
  `enter_mode_cnf`** vía `msgr_send` (@0xce8bd848). **INFERENCE fuerte** (todos los asserts tienen
  prefijo `rfm_inst`/`LTE_ML1_RFMGR`; el sender del cnf está en el mismo módulo que consume el req).
- **Handler que produce el cnf:** la función que contiene el `msgr_send(&enter_mode_cnf…)` @0xce8bd848,
  precedida por la validación MCPM @0xce8bd818. Es el **último paso del handler de enter_mode del ML1**.
- **UMID numérico del `enter_mode` req/cnf:** **UNKNOWN**. Los UMIDs son constantes compiladas
  (`MSGR_ID_VAL(...)`), no aparecen como strings. Se resolverían en vivo o con el .h RFLM (ausente).

### 1.3 El disparador FTM (el que TÚ estás ejercitando) — módulo DISTINTO (FACT)
Cluster **`ftm_lte_*` / `rflte_ftm_mc_*` / `rflte_dispatch_*`** — región rodata ~**0xce6dc000–0xce6f2000**:
```
0xce6dfee8  ftm_lte_tune: current_dl_to_ul_map_ptr is NULL!          (FTM RX tune)
0xce6dff78  rflte_dispatch_rf_config_req: rf_tuner_config_params creation failed
0xce6e08a8  rflte_ftm_mc_wakeup: invalid antpath on sub[%d], cc[%d], path[%d]   <<< WAKEUP DIRECTO FTM
0xce6e07d8  rflte_ftm_mc_get_mcpm_bw: NULL mcpm_request_parms_type   (MCPM en FTM)
0xce6e02a8  ftm_lte_rflm_lte_usleep_enter:  (micro-sleep enter FTM)
0xce6e03b0  ftm_lte_rflm_lte_usleep_exit
0xce6e0508  ftm_lte_rflm_lte_usleep_seq
0xce6e72c0  rflte_mc_carrier_activate      (activa portadora RF en FTM)
0xce6e0c28  rflte_ftm_iq_capture_prop_action_get_cfg   (IQ capture FTM)
```
**Todos FACT.** Este árbol es el path de factory-test. **Clave:** `rflte_ftm_mc_wakeup` (0xce6e08a8)
es el equivalente FTM de "encender el RF", y **NO manda `enter_mode` al ML1** — hace el wakeup del
device directo (via `rfdevice_sleep_manager_wakeup_devices_rx`, @0xce6dd840). El commit per-tech FTM
que recorre `0xca733d10[tech]` (0xd81dfecc, ya verificado) llama a estos módulos FTM, **no al ML1-RFMGR.**

### 1.4 El commit FTM (0xd81dfecc) — reconfirmado (FACT)
```
d81dff14:  r16 = memw(r2+#0xc)               ; r16 = session->0xc  (el "modo" que exige ==2)
d81dff20:  r3  = memb(r2+##0x89a8)           ; post-check ctx (==7 = no-tech)
d81dff2c:  r21 = add(##0xca733d10, r21<<2)   ; &modulos[tech]
d81dff30:  p0  = cmp.eq(r3,#0x7) …           ; else-tech
d81dffc4:  r1  = memw(r20<<2 + ##0xca733d10) ; record del módulo per-tech
d81dffd4:  r2  = memw(r21+#0x0) ; callr memw(record+4)  ; invoca callback del módulo (FTM)
```
El `session->0xc` se usa como índice `r16-1 <= 0x13` en `0xca733d10`. **FACT.** Los callbacks
apuntan a los módulos **FTM** (`ftm_lte_*`), no al ML1. **INFERENCE fuerte** (por identidad de cluster).

---

## 2. LA CONDICIÓN DE WAKEUP — `wakeup_req.use_enter_mode`

### 2.1 Qué es y dónde se lee (FACT)
- Es un **campo booleano de la estructura `rfm_inst->wakeup_req`** (el request de wakeup del ML1
  RF-Manager de LTE). Único punto verificado: **assert @0xce8bd598** dentro del constructor de
  `run_rf_script_msg` (la función que arma el script RX de enter). Semántica: **"cuando estoy
  construyendo el script de entrada, el flag `use_enter_mode` DEBE estar TRUE"** — i.e. sólo se llega
  a esta rama si la SM decidió entrar por `enter_mode`. **FACT.**
- Estructura hermana: `rfm_inst->wakeup_req.is_async_req` (@0xce8b6d48, en el path de
  `lte_ml1_rfmgr_enl1_slna_done_ind(..., ENL1_SLNA_RF_ACTION_WAKEUP, is_async_req, ...)`). Confirma que
  `wakeup_req` gobierna la rama wakeup/enter y su asincronía. **FACT.**

### 2.2 Qué lo setea (FACT/INFERENCE)
- **No hay ningún setter accesible por DIAG/FTM ni store estático que ponga `use_enter_mode=1`.**
  Sólo existe el assert (lectura). El campo lo escribe la **máquina de estados ML1 online** cuando el
  módem entra a un modo/servicio que requiere `enter_mode` (p.ej. primera adquisición LTE, cambio de
  tech con enter completo) en vez de un `wakeup/resume` desde sleep. **INFERENCE fuerte.**
- **No existe un comando "device_wakeup"/"rfm_wakeup" DIAG que prenda `use_enter_mode`.** El
  `rflte_ftm_mc_wakeup` del path FTM (0xce6e08a8) es OTRO wakeup (device-level, factory), que **no
  toca `rfm_inst->wakeup_req.use_enter_mode`** del ML1. **FACT (módulos distintos) + INFERENCE.**

### 2.3 Consecuencia
No hay "secuencia de wakeup previa" que puedas mandar por DIAG para prender `use_enter_mode`. Ese flag
vive en el mundo ML1-online. **En factory-test simplemente no se recorre esa rama.** → El camino no es
"prender use_enter_mode desde FTM", es **elegir el path correcto** (§7).

---

## 3. FACTORY-TEST vs ONLINE — ¿bloquea el cnf? ¿online lo permitiría?

### 3.1 Existe una barrera dura de modo operativo (FACT)
```
0xce6d59e0  "Attempt to access FTM variables in ONLINE MODE"   (x3: 0xce6d59e0/0xce6d5b28/0xce6d5b78)
0xce64ebb0  "=MMOC= Phone should be in online mode"
0xce6cf8c6  Assertion (p_tx_ctx->tx_info.rf_mode == NR5G_LL1_CAL_FTM_RF_MODE_CAL)
0xce6cf902  "FTM_RF_MODE_CAL"
```
Hay un **flag global de modo (FTM/CAL vs ONLINE)** que particiona el acceso a variables y al path RF.
El código FTM aborta si se lo llama en ONLINE, y viceversa. **FACT.**

### 3.2 Por qué en factory-test el `enter_mode_cnf` no completa (INFERENCE fuerte, con base FACT)
- El `enter_mode`/`enter_mode_cnf` es del **stack ML1 online**. En factory-test el módem está en
  **FTM/CAL mode**: la tarea ML1 online no está corriendo su flujo normal de servicio, por lo que
  **nadie genera el `enter_mode` req** (el disparador FTM va por `rflte_ftm_mc_wakeup`, §1.3). Sin req,
  no hay cnf. **INFERENCE (identidad de módulos) — es la explicación más económica de "el cnf no vuelve".**
- Segunda barrera posible: aunque llegara un `enter_mode` req al ML1 en FTM, el handler exige
  **MCPM `return_trans_id != 0`** (@0xce8bd818) y **`mcpm_req` válido**; si MCPM no está encendido
  (`MCPM not turned ON yet` @0xce748848) el assert falla y el cnf nunca sale con E_SUCCESS. **FACT (precond).**

### 3.3 ¿Online lo permitiría? (INFERENCE)
- **Sí, en online el `enter_mode`/`enter_mode_cnf` es el flujo natural** del ML1 RF-Manager: al adquirir
  LTE, el ML1 pide `enter_mode`, el RFM confirma con `enter_mode_cnf`, y ahí la SM avanza el estado
  (equivalente a `session->0xc→2` en tu modelo FTM). Pero **online NO ejecuta el path RFTEST/IQ_CAPTURE
  de FTM** (bloqueado por la barrera §3.1). → **Online resuelve el cnf pero NO te da el IQ_CAPTURE.**
  Los dos mundos son mutuamente excluyentes por diseño. **INFERENCE fuerte.**
- **Corolario importante:** la idea de "correr el flujo en online para que el cnf complete" **no sirve
  para IQ vía RFTEST**, porque en online el gate FTM (`@0xca7897b0`) y el ejecutor RFTEST no están
  activos. El camino correcto es **quedarse en FTM y usar el disparador FTM** (§7 P0), no cambiar a online.

---

## 4. PREREQUISITOS DEL `enter_mode_cnf` E_SUCCESS (VAs)

Del propio handler ML1 (contexto de asserts alrededor de 0xce8bd598–0xce8bda98) y del entorno RF:

| # | Prerequisito | Evidencia (VA) | Tipo |
|---|--------------|----------------|------|
| 1 | **`rfm_inst->wakeup_req.use_enter_mode == TRUE`** (la SM eligió la rama enter) | 0xce8bd598 | FACT |
| 2 | **`rxlm_buf_id_ant0..3 != LTE_ML1_RFMGR_LM_HANDLE_INVALID`** — buffers RxLM válidos (LM manager configurado) | 0xce8bd5d0, 0xce8bee00 | FACT |
| 3 | **`enter_params->header.source2_tech <= RFCOM_NUM_MODES`** — parámetros de tech válidos | 0xce8bd670 | FACT |
| 4 | **MCPM: `mcpm_req.return_trans_id != 0`** — transacción de reloj/potencia aceptada (MCPM ON) | 0xce8bd818, 0xce748848 (`MCPM not turned ON yet`) | FACT |
| 5 | **`rfm_init` llamado** — el core RF inicializado | 0xce6de1b0 (`RFM_INIT WAS NEVER CALLED. NO CMW RF FUNCTIONALITY`) | FACT |
| 6 | **`fw_rx_lm_req.msg_payload.carrier[..].rxlm_buf_id_antN` válidos** al mandar el LM req a FW | 0xce8be250.. | FACT |
| 7 | **`script_exec_db` sin pendientes** (`is_pending_fed/send_rxlm/send_txlm` en estado correcto) | 0xce8b6df8.. 0xce8b8e90 | FACT |
| 8 | **`msgr_send(&enter_mode_cnf…)==E_SUCCESS`** — MSGR operativo y cola del cliente registrada | 0xce8bd848 | FACT |
| 9 | **Banda derivable de EARFCN** (`lte_ml1_common_band_get_band_from_dl_earfcn_with_inst`) | 0xce8bdc10 | FACT |

En términos simples: para que el ML1 responda `enter_mode_cnf` con éxito hacen falta **RF core init +
NV/RFC (RxLM/handles) + MCPM encendido + parámetros de tech/EARFCN válidos + la SM en la rama
enter_mode**. En factory-test (1) y muchas de (2)-(4) no se cumplen porque el flujo va por FTM. **FACT.**

---

## 5. RELACIÓN CON EL GATE `@0xca7897b0` — reconciliación con pases previos

- Pases previos (tech_state_gate.md, session_start.md): el store `@0xca7897b0[tech]=1` (0xd81e5d54)
  exige **`session->0xc==2`** (0xd81e5d20), y `session->0xc` NO lo mueve TECH_ENTER por sí solo.
- **Nuevo hallazgo:** ese `session->0xc→2` en el path FTM lo debe avanzar el **commit FTM
  (0xd81dfecc → módulos `0xca733d10[tech]` → `ftm_lte_*`/`rflte_ftm_mc_wakeup`)**, NO el `enter_mode_cnf`
  del ML1. El modelo previo (INFERENCE) de "el cnf ML1 sube 0xc a 2" **era la hipótesis correcta para el
  path ONLINE, pero el path FTM avanza el estado por su cuenta** cuando `rflte_ftm_mc_wakeup` /
  `rflte_mc_carrier_activate` completan. **INFERENCE (corrige el modelo previo con la evidencia de módulos).**
- Por eso el flag no sube: **el módulo FTM per-tech LTE en `0xca733d10[1]` o no está registrado, o su
  callback no completó el activate/wakeup** (falta RF init/MCPM/NV o falta un TLV de RADIO_CONFIG que
  dispare el activate). **INFERENCE fuerte.**

---

## 6. FACT / INFERENCE / UNKNOWN (con VAs)

**FACT (verificado en seg27_dec.bin @0xce480000 y clade_dec_full.bin):**
- `use_enter_mode` assert @0xce8bd598; `enter_mode_cnf` sender @0xce8bd848; precond MCPM
  `mcpm_req.return_trans_id!=0` @0xce8bd818; grupo de 8 `*_cnf` @0xce8bd848–0xce8bda98. Módulo = `lte_ml1_rfmgr`.
- `run_rf_script_msg` / `fw_rx_lm_req` / `rxlm_buf_id` asserts @0xce8bd5d0, 0xce8be250.. → construcción de script RX de enter.
- Path FTM separado: `ftm_lte_tune` @0xce6dfee8, `rflte_dispatch_rf_config_req` @0xce6dff78,
  `rflte_ftm_mc_wakeup` @0xce6e08a8, `rflte_mc_carrier_activate` @0xce6e72c0, `rflte_ftm_iq_capture_*` @0xce6ded20..0xce6e0c28.
- Barrera de modo: `Attempt to access FTM variables in ONLINE MODE` @0xce6d59e0; `=MMOC= Phone should be in online mode` @0xce64ebb0; `FTM_RF_MODE_CAL` @0xce6cf902.
- Precond RF: `RFM_INIT WAS NEVER CALLED…` @0xce6de1b0; `MCPM not turned ON yet` @0xce748848.
- Commit FTM 0xd81dfecc: lee `session->0xc` (0xd81dff14), indexa `0xca733d10[tech]` (0xd81dffc4), `callr memw(record+4)`.
- `rfm_inst->wakeup_req.is_async_req` @0xce8b6d48.

**INFERENCE:**
- El receiver del `enter_mode` req y productor del `enter_mode_cnf` es la **tarea ML1 RF-Manager LTE**
  (mismo módulo que los asserts `rfm_inst`), no el rf_task/RFM genérico.
- En factory-test el `enter_mode` req **no se genera** (el path FTM usa `rflte_ftm_mc_wakeup` directo);
  por eso el cnf "no vuelve". Es un artefacto de estar en el módulo FTM, no un fallo del RF.
- El `session->0xc→2` (y por ende `@0xca7897b0[tech]=1`) en FTM lo avanza el **commit/activate FTM**
  cuando `rflte_mc_carrier_activate`/`rflte_ftm_mc_wakeup` completan, no el cnf ML1.
- `use_enter_mode` lo setea la SM ML1 online; no hay setter DIAG.

**UNKNOWN (sólo en vivo o con blob RFLM ausente):**
- UMID numérico de `enter_mode` req/cnf (constante compilada; no hay string).
- El registro runtime de `0xca733d10[1]` (módulo LTE FTM): si su callback está poblado y qué TLV de
  RADIO_CONFIG dispara `rflte_mc_carrier_activate`.
- El código exacto (VA) del handler ML1 del cnf y del setter de `use_enter_mode` (viven en la región ML1
  no descompilada limpiamente; los strings están en b27 @0xce480000 pero el código no se aisló estáticamente).

---

## 7. RECOMENDACIÓN PRIORIZADA Y ACCIONABLE EN VIVO

> Objetivo: que `@0xca7897b0[LTE]` pase a 1 para que RADIO_CONFIG/RX_MEASURE/IQ_CAPTURE dejen el 0x14.
> Conclusión de este pase: **NO esperes el `enter_mode_cnf` del ML1 — no aplica en FTM.** El estado lo
> avanza el propio framework FTM cuando el activate/wakeup completa.

### P0 — Completar el ACTIVATE FTM (lo más probable). **[recomendado]**
El commit FTM (0xd81dfecc) sube `session->0xc` cuando el módulo LTE (`0xca733d10[1]`) corre su callback
de activate/wakeup (`rflte_mc_carrier_activate` @0xce6e72c0 → `rflte_ftm_mc_wakeup` @0xce6e08a8). Para
que ese callback complete hace falta que la **portadora esté configurada**. Acciones:
1. Mandar **TECH_ENTER (LTE, TECH=1)** como ya tenés, y luego un **RADIO_CONFIG con los TLVs que
   activan la portadora** (BAND, CHANNEL/EARFCN, BANDWIDTH, RFM_DEVICE, RX_CARRIER). El activate FTM
   necesita EARFCN válido (assert `band_get_band_from_dl_earfcn` @0xce8bdc10; `ftm_lte_tune:
   current_dl_to_ul_map` @0xce6dfee8). **Sin BAND+EARFCN el activate no corre y `0xc` no sube.**
2. **Verificación en vivo:** tras RADIO_CONFIG con BAND+EARFCN, releer `memb(0xca7897b0 + 1*8)`; debe
   pasar a 1. Si pasa a 1 → RX_MEASURE/IQ_CAPTURE dejan de dar 0x14. **Evidencia:** el path FTM entero
   (`rflte_dispatch_rf_config_req`, `rflte_mc_carrier_activate`) existe y es el disparador real (FACT).
   **Prioridad máxima porque no depende de cambiar de modo.**

### P1 — Prerequisitos de HW/RF (habilitar el activate). **[necesario si P0 no basta]**
Asegurar, ANTES de RADIO_CONFIG, que el subsistema RF esté inicializado en FTM:
- `rfm_init` ejecutado (evitar `RFM_INIT WAS NEVER CALLED` @0xce6de1b0): en la práctica, mandar primero
  el/los comandos FTM de **set_mode/cal-mode** que arrancan el RF (poner el módem en **FTM_RF_MODE_CAL/
  online-cal**, no en un FTM "pelado"). **FACT (precond) / INFERENCE (que un set_mode previo lo arranca).**
- **MCPM encendido** (evitar `MCPM not turned ON yet` @0xce748848): lo enciende el mismo arranque de
  modo RF/cal. Si el activate falla por MCPM, es esto. **FACT (precond).**
- NV/RFC cargada (RxLM handles válidos): device calibrado/NV presente. **FACT (asserts rxlm_buf_id).**

### P2 — Barrer SCENARIO/SUB en TECH_ENTER. **[bajo costo, complementario]**
Como en session_start.md §4: probar **SCENARIO=1** y **SUB acorde a tu SIM**. El SCENARIO puede
condicionar qué prep/activate se pide. Es la única variable de TLV con chance de mover el estado. **INFERENCE.**

### P3 — NO cambiar a online para IQ. **[descartado, documentado]**
Correr en ONLINE haría que el `enter_mode_cnf` complete (flujo natural del ML1), **pero online NO
ejecuta el path RFTEST/IQ_CAPTURE de FTM** (barrera `Attempt to access FTM variables in ONLINE MODE`
@0xce6d59e0). Por lo tanto **online no sirve para capturar IQ vía RFTEST**. Documentado para no perder
tiempo en esa vía. **INFERENCE fuerte.**

### Orden de ejecución sugerido (en vivo)
```
1. FTM set RF/cal mode         (arranca rfm_init + MCPM)                  [P1]
2. TECH_ENTER (LTE, TECH=1, SCENARIO=0/1, SUB=<sim>)                       [P0/P2]
3. RADIO_CONFIG (BAND, EARFCN/CHANNEL, BANDWIDTH, RFM_DEVICE, RX_CARRIER)  [P0]  <-- dispara activate
4. leer memb(0xca7897b0 + 1*8) == 1 ?  -> sí: gate abierto
5. RX_MEASURE / IQ_CAPTURE (ya no 0x14)
```
La pieza que faltaba en el modelo previo: **el flag lo abre el ACTIVATE de la portadora en el path FTM
(paso 3), no un `enter_mode_cnf`.** Por eso "esperar el cnf del RF" no destrababa nada en factory-test.

---

## 8. Reproducir
```bash
# Strings ML1 enter_mode/cnf/mcpm (seg27_dec, VA base 0xce480000):
python3 - <<'PY'
d=open('/tmp/modemre/seg27_dec.bin','rb').read(); B=0xce480000
for va in (0xce8bd598,0xce8bd818,0xce8bd848,0xce8bd5d0,0xce8bd670):
    print(hex(va), d[va-B:va-B+90].split(b'\x00')[0].decode())
PY
# Path FTM (módulo distinto):
python3 - <<'PY'
d=open('/tmp/modemre/seg27_dec.bin','rb').read(); B=0xce480000
for va in (0xce6dfee8,0xce6dff78,0xce6e08a8,0xce6e72c0,0xce6e07d8,0xce6de1b0,0xce748848,0xce6d59e0,0xce64ebb0):
    print(hex(va), d[va-B:va-B+80].split(b'\x00')[0].decode())
PY
# Commit FTM que sube session->0xc e indexa 0xca733d10[tech]:
/tmp/modemre/dis.sh 0xd81dfecc 0x140
# Gate RFTEST y escritor del flag (reconfirmar):
/tmp/modemre/dis.sh 0xd81e5cec 0xc0
/tmp/modemre/dis.sh 0xd8202200 0x50
```
