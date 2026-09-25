# map_msgr_ipc — MSGR (message router) / IPC / tasks del modem SM6375 y por qué ciertos comandos son asíncronos

**Target:** SM6375 / Moto G82 5G · MPSS.HI.4.3.4-00494-MANNAR_GEN_PACK-1.24452.133 · Hexagon/QDSP6 v66
**Imágenes:**
- `clade_dec_full.bin` — código paginado, VA base **0xd8000000** (10 MB extraído). Tool `dis.sh <va> <len>`.
- `seg27_dec.bin` = `modem.b27` **descomprimido**, VA base **0xce480000** — rodata + código ML1/RRC/MAC/MCPM/RFMGR (aquí viven TODOS los asserts/format-strings de MSGR).
- `modem.b21` — rodata VA base **0xc3553000** (tablas de nombres de UMID: enum `MSGR_*`/`RFA_*`/`LTE_ML1_*`/`NR5G_ML1_*`, nombres de tasks/mailbox).
- `modem.b23` — RW-data VA base 0xc8b6a000 (nodos DIAG).
- Herramientas: `find_refs.py` (calls+jumps PC-rel), `up_trace.py`, `reach.py`, `_fns.pkl`, `dis.sh`.

**Cross-ref:** `enter_mode_path.md`, `map_ftm_framework.md`, `map_diag_core.md`, `session_start.md`, `tech_state_gate.md`, `diag_transport_full.md`.

**Leyenda:** **FACT** = string/instrucción leída byte-a-byte (VA citada) · **INFERENCE** = deducción con base dura · **UNKNOWN** = sólo resoluble en vivo / con .h ausente.

---

## 0. TL;DR

1. **MSGR es el message-router IPC de QuRT/DSP del modem.** Cada mensaje lleva un **UMID de 32 bits**
   (`msgr_umid_type`, formateado `UMID=0x%08X` @0xce68146b — **FACT**). Los tasks se **suscriben**
   registrando UMIDs con `msgr_register_block(...)` / `register_block_variant_queue(...)` contra un
   **`msgr_client`** que cuelga de una o varias **message-queues (mq)** creadas con
   `msgr_client_create` + `msgr_client_add_mq[_dynamic]`. El envío es `msgr_send()` /
   `msgr_send_direct()`. **FACT (API completa por strings, VAs abajo).**
2. **El UMID se compone `tech / module / id`** (más un bit de "supervisory type"). La macro está
   byte-exacta en un assert: `(((tech) & 0xFF) << 8) | ((module) & 0xFF)` = el **`tech_module`**
   (16 bits altos) que se pasa a `msgr_register_block`; el UMID completo de 32 bits añade el
   `dir/id` en los 16 bajos. Confirmación de rango de módulos por tech = **15** (7 "supervisory" +
   8 normales) en el assert de `msgr_jump_table[tech].max_modules` @0xce5940fc. **FACT.**
3. **Tasks principales identificados por nombre de mailbox/task (b21) + clientes MSGR (seg27):**
   DIAG task (`diag_task_tcb`), FTM task (`FTM TASK RFGSM Mailbox`), RF/RFA task
   (`GSM L1 Common RFA Task Mailbox`, `RF Task Init Status`), LTE ML1 (múltiples clientes
   `lte_ml1_*_msgr_client`), NR5G ML1 (`nr5g_ml1_*_msgr_client`), MCPM (cliente MCPM). **FACT (nombres).**
4. **El path enter_mode FTM↔ML1 es asíncrono porque va por MSGR request→confirm cross-task:** el
   ML1-RFMGR (LTE) manda `LTE_ML1_RFMGR_*_REQ` (0xc404dc..0xc404e1) al task RF/RFA, y el RF responde
   con `RFA_RF_LTE_*_CNF` (0xc404dc..0xc404e1) — p.ej. `RFA_RF_LTE_ENTER_MODE_CNF` @0xc404dce1. La
   respuesta llega **más tarde**, en otro contexto de task, cuando el HW/MCPM completó. En factory-test
   **ese req nunca se emite** (el path FTM hace wakeup directo), por eso el cnf "no vuelve". **FACT (UMID
   names) + INFERENCE (asincronía cross-task).**
5. **QMI DMS set_operating_mode → CM (`cm_ph_cmd_pref_change_req`) → MMOC → arranque/parada de RF.**
   Poner el modem en modo FTM/OFFLINE cambia el flag global de MODO (FTM/CAL vs ONLINE) que
   **particiona** el acceso a variables y al pipeline RF (`Attempt to access FTM variables in ONLINE
   MODE`). Los tasks RF cambian de "servicio ML1 online" a "ejecutor de test (rflte_ftm_*)". **FACT
   (strings) + INFERENCE (secuencia CM→MMOC).**
6. **Callbacks runtime per-tech:** la tabla `@0xca733d10[tech]` (stride 8, índice `session->0xc`) guarda
   `{ctx, fn}`; el commit FTM `0xd81dfecc` hace `callr memw(record+4)`. Y el callback carrier-activate
   `0xd81bd018` (allocframe @**0xd81bd01c**) se **registra en runtime** vía init `0xd81e52c8` (sin puntero
   estático). Esto es exactamente lo que el análisis estático no puede seguir sin ejecución. **FACT.**

---

## 1. MSGR — la API del message router (con VAs)

### 1.1 Las funciones de la API (nombres byte-exact, seg27 @0xce480000) — FACT

Todas estas son referencias a la **API MSGR** encontradas en format-strings/asserts de seg27
(`msgr_send x281`, `msgr_client x47`, `msgr_hdr x531`, etc.):

| API | rol | evidencia (VA de string / uso) |
|-----|-----|--------------------------------|
| **`msgr_client_create(&client)`** | crea el cliente MSGR (contenedor de colas+suscripciones) | 0xce7448ae, 0xce76c887, 0xce945a27 |
| **`msgr_client_add_mq(&client, ...)`** | añade una message-queue estática al cliente | 0xce7448d6 |
| **`msgr_client_add_mq_dynamic("NAME", &client, prio1, prio2, buf_size, &mq_id, N)`** | añade una mq dinámica (nombre + tamaños) | 0xce945a77 (`"LTE_NS"`, ret==E_SUCCESS) |
| **`msgr_register_block(tech_module, &client, mq_id, umid_list, umid_cnt)`** | **suscribe** una lista de UMIDs a una cola del cliente | **0xce945b0f** (macro UMID byte-exact) |
| **`register_block_variant_queue(TECH_MODULE, client_ptr, mq_id, umid_list, cnt, instance)`** | variante por-instancia (multi-SIM/multi-carrier) | 0xce8ac06e (`MSGR_LTE_ML1_MSMGR`), 0xce8cbe06 (`MSGR_LTE_ML1_SCHDLR`) |
| **`msgr_register` / `msgr_register_block_variant`** | registro base (wrappers) | 0xce748136, 0xce69731b |
| **`msgr_deregister_block_variant[_queue]`** | des-suscripción | 0xce697396, 0xce8ac067 |
| **`msgr_send(&msg.hdr, sizeof(msg))`** | envía por UMID (routing por tabla, cross-task) | **0xce8bd84f** (enter_mode_cnf), +281 sitios |
| **`msgr_send_direct(&client_or_mq, &msg, ...)`** | envía directo a una cola conocida (sin lookup de routing) | **0xce8c91f7** (`lte_ml1_schdlr_db[instance]->ul_msgr_client`) |
| **`msgr_send_msg(UMID=0x%08X)`** | envío por UMID crudo (log confirma **UMID de 32 bits**) | **0xce68146b** |
| **`msgr_receive_nonblock`** | recepción no bloqueante de una mq | 0xce748185 |
| **`msgr_query_umid`** | consulta metadatos de un UMID | 0xce8cbde3 |
| **`msgr_client_delete`** | destruye el cliente | 0xce8360a7 |

Estructuras de soporte (nombres de campo byte-exact):
- **`msgr_hdr_struct_type`** (cabecera de todo mensaje; el `id` es el UMID) — `msgr_hdr` x531, assert
  `sizeof(msgr_hdr_struct_type) <= msg_len` @0xce734a81. **FACT.**
- **`msgr_client_t`** con `msgr_client_id`, `msgr_client_info`, `msgr_client_nodes[]` — 0xce659969,
  0xce734ac2, 0xce5941e6. **FACT.**
- **`msgr_umid_type`** (el tipo del UMID) — 0xce9aabcb (`(msgr_umid_type)MCS_CXM_GNSS_TECH_STATE_BCAST_IND`). **FACT.**
- Tabla de routing global: **`msgr_table`** (assert `msgr_table != NULL` @0xce734e02),
  **`msgr_jump_table[tech]`** (@0xce5940fc), **`msgr_id_index_tbl`** (@0xce593fba, capacidad 592). **FACT.**

### 1.2 El formato del UMID (con la macro byte-exact) — FACT

Assert byte-exact del registro de MSGR (`msgr_register_block`), seg27 @**0xce945b0f–0xce945bb6**:
```
msgr_register_block(
   ((uint16) (((0x04) & 0xFF) << 8) | ((0x49) & 0xFF)),   <-- 1er arg = MSGR_TECH_MODULE
   &(lte_ns_cb.msgr_client),
   lte_ns_cb.msgr_id,                                      <-- mq_id destino
   lte_ns_driver_init_umid_list,                           <-- lista de UMIDs a suscribir
   lte_ns_driver_init_umid_cnt) == E_SUCCESS               <-- cantidad
```
De aquí sale el **layout canónico Qualcomm del UMID (`msgr_umid_type`, 32 bits, log `UMID=0x%08X`):**

```
 31                      16 15                         0
+---------------------------+---------------------------+
|      tech_module (16b)     |     dir/type + id (16b)   |
+---------------------------+---------------------------+
  tech_module = (tech & 0xFF) << 8 | (module & 0xFF)      <-- FACT (macro @0xce945b3c)
    tech   = 8 bits  (RAT/subsistema: LTE, NR5G, GSM, RFA, MCS_CXM, DS_APPSRV, ...)
    module = 8 bits  (submódulo dentro del tech: RFMGR, DLM, ULM, SCHDLR, MSMGR, ...)
  bajo 16b:
    dir/type = REQ / CNF / IND / RSP  (dirección/clase del mensaje)
    id       = número de mensaje dentro de (tech, module, dir)
```
- El **conteo de módulos por tech** está fijado por la máquina de índices:
  `msgr_jump_table[tech].max_modules * ( ((0x06|0x8)-(0x00|0x8)+1) + (0x07-0x00+1) )` @0xce5940fc/0xce594100.
  Evaluado: `(0xE-0x8+1)=7` "supervisory/high" + `(0x7-0x0+1)=8` normales = **15 clases de módulo por tech**.
  El índice de mensajes usa `msgr_id_index_tbl` de **592** slots (assert @0xce593fba) y término de tamaño
  `3027*(15>>1)` (@0xce594050). **FACT.**
- **Cómo se leen los nombres:** el enum `MSGR_ID_VAL(...)` (asserts @0xce5cd152, 0xce6976ba) confirma que el
  UMID es una constante compilada `MSGR_ID_VAL(NAME)`. Los **nombres legibles** de cada UMID están en la
  tabla de strings de **b21** (§3): p.ej. `LTE_ML1_RFMGR_ENTER_ONLY_REQ`, `RFA_RF_LTE_ENTER_MODE_CNF`,
  `NR5G_ML1_RFMGR_WAKEUP_REQ`. El **entero** UMID de cada uno es una constante del .h (no aparece como
  dato) → **UNKNOWN numérico estático**; se resuelve en vivo (`msgr_query_umid`) o loggeando `UMID=0x%08X`.

### 1.3 Cómo un task envía y cómo se suscribe (secuencia) — FACT

**Suscripción (arranque del task):**
```
msgr_client_create(&client)                              ; crea el cliente
   → msgr_client_add_mq_dynamic("NAME",&client,p1,p2, buf, &mq_id, N)   ; crea la cola (prioridad p1/p2)
   → msgr_register_block( TECH_MODULE, &client, mq_id, umid_list[], cnt )   ; suscribe UMIDs a esa cola
```
Ejemplo real (LTE ML1 MSMGR, seg27 @0xce8ac06e):
`register_block_variant_queue( MSGR_LTE_ML1_MSMGR, lte_ml1_msmgr[instance]->client_ptr, ul_mq_id,
lte_ml1_msmgr_ul_umid_list, ARR_SIZE(...), instance )`. Hay **varias colas por cliente** con distinta
prioridad: `ul_mq_id` (uplink), `ll_mq_id` (low-latency/low-priority), `hi_prio_mq_id`
(`hi_pri_msgr_client`) — visible en el scheduler LTE (@0xce8c91f7..0xce8cab2f). **FACT.**

**Envío:**
- `msgr_send(&msg.hdr, sizeof(msg))` — el router mira `msg.hdr.id` (UMID), busca en `msgr_table` /
  `msgr_jump_table[tech]` qué cola(s) están suscritas y **encola** el mensaje ahí (posible cross-task ⇒
  **asíncrono**). Devuelve `E_SUCCESS`/`E_NO_ROUTE` (`msgr_send failed with cause E_NO_ROUTE` @0xce669906).
- `msgr_send_direct(&mq_client, &msg, ...)` — salta el lookup: entrega directo a una cola ya conocida
  (usado en el fast-path del scheduler LTE, @0xce8c91f7). Más barato, aún cross-task. **FACT.**

**VA del `msgr_send` que produce el `enter_mode_cnf` (el que "no vuelve" en FTM):** el sitio de emisión
está en el handler ML1-RFMGR cuyo assert es **`msgr_send(&enter_mode_cnf.hdr, sizeof(enter_mode_cnf)) ==
E_SUCCESS` @0xce8bd84f** (código ML1 en la región seg27/paged ML1). Los 8 `*_cnf` consecutivos
(enter/exit/sleep/cdrx_sleep/wakeup/cdrx_wakeup/rxlm/txlm) @0xce8bd84f–0xce8bda9f son la tabla de
respuestas de esa SM. **FACT.**

---

## 2. Los tasks del modem, sus colas y prioridades

### 2.1 Nombres de task / mailbox (b21 @0xc3553000) — FACT

| task | nombre string (VA) | rol | cómo se crea |
|------|--------------------|-----|--------------|
| **DIAG** | `diag_task_tcb` (0xce585bf9, seg27) | dispatcher DIAG (§map_diag_core: `diagpkt_master_dispatch`) | rex/rcinit (`rcinit_lookup_rextask`, 0xce586380) |
| **FTM** | **`FTM TASK RFGSM Mailbox`** (0xc37bca3a) | ejecutor factory-test RF (RFTEST/RFDEBUG) | rex task + mailbox; recibe DIAG subsys 0x0B |
| **RF / RFA** | **`GSM L1 Common RFA Task Mailbox`** (0xc3fb6b86), **`RF Task Init Status`** (0xc3928806), `RFA Task Mailbox` | task RF-driver (RFA), procesa `*_REQ` de ML1 y emite `RFA_*_CNF` | rex task; `RF Task Init` + `RFM Init` (0xc3928829) |
| **LTE ML1** | clientes `lte_ml1_mgr_msgr_client`, `lte_ml1_schdlr` (`ul/ll/hi_pri`), `lte_ml1_msmgr`, `lte_ml1_offload_msgr_client[instance]`, `lte_ml1_common_diag_msgr_client` (seg27) | L1 LTE (RFMGR/DLM/ULM/SCHDLR/GM/SM) | `msgr_client_create` + `add_mq` + `register_block_variant_queue` |
| **NR5G ML1** | `nr5g_ml1_mgr_msgr_client`, `nr5g_ml1_tick_msgr_client`, `nr5g_ml1_offload_msgr_client[instance]`, `nr5g_ml1_common_diag_msgr_client`, `nr5g_mac ... ctrl_msgr_client_id` (seg27) | L1 NR5G | idem |
| **MCPM** | cliente MCPM (`MCPM command %d` 0xce5d0e55; asserts `MCPM_NUM_TECH`) | Modem Clock & Power Mgr — handshake de reloj/potencia (`mcpm_req.return_trans_id`) | rex task |
| **(QuRT threads)** | `qurt_thread_create` (0xce5d1f2d, 0xce67c980) | threads QuRT de bajo nivel (FW/uImage) | `qurt_thread_create` |

**Creación:** el modem usa **rex tasks orquestados por rcinit** (`rcinit_lookup_rextask`, `rcinit_worker
trigger` @0xce57e0d8; eventos `rcevt_*` @0xce74449f) para los tasks de alto nivel (DIAG, FTM, RF, ML1,
MCPM), y **QuRT threads** (`qurt_thread_create`) para FW/uImage. **FACT (mecanismos por string) +
INFERENCE (asignación task↔mecanismo).**

### 2.2 Las colas (message queues) por cliente — FACT

Cada task tiene **1 `msgr_client`** con **N colas** de distinta prioridad. Ejemplo LTE-ML1-SCHDLR
(seg27 @0xce8cbe06–0xce8cc05a) tiene 3 colas: **`ul_mq_id`**, **`ll_mq_id`**, **`hi_prio_mq_id`**, cada
una con su `umid_list` propio (`lte_ml1_schdlr_ul_umid_list_cnt`, `..._ll_..._cnt`, `..._hi_pri_..._cnt`).
El fast-path usa `msgr_send_direct(&...->hi_pri_msgr_client, ...)`. **La prioridad de la cola** (arg 3/4
de `msgr_client_add_mq_dynamic`) determina el orden de servicio dentro del task. **FACT (colas + fast/slow
path) + INFERENCE (semántica de prioridad).**

> **Prioridad numérica exacta de cada task rex/QuRT = UNKNOWN estático** (se fija en la tabla rcinit /
> `qurt_thread_attr`, no expuesta como string legible en estos segmentos).

---

## 3. Path enter_mode online vs FTM — FTM↔ML1 por MSGR y por qué es asíncrono

### 3.1 Los UMIDs enter_mode/enter_mode_cnf (nombres byte-exact, b21) — FACT

El par request/confirm entre **ML1-RFMGR (LTE)** y el **task RF/RFA** vive en el bloque
0xc404dc..0xc404e1 de b21:

**Requests que el ML1 manda al RF (`LTE_ML1_RFMGR_*_REQ`):**
```
0xc404dcc9  LTE_ML1_RFMGR_START_REQ
0xc404e0bc  LTE_ML1_RFMGR_ENTER_ONLY_REQ        <<< el "enter_mode" del path ONLINE
0xc404dfff  LTE_ML1_RFMGR_WAKEUP_REQ
0xc404ddeb  LTE_ML1_RFMGR_SLEEP_REQ
0xc404dcfb  LTE_ML1_RFMGR_SEND_TUNE_SCRIPT_REQ
0xc404e0f5  LTE_ML1_RFMGR_SCRIPT_EXEC_REQ
0xc404e113  LTE_ML1_RFMGR_SEND_TXRX_LM_REQ      (rxlm/txlm req)
... (config/div/resume/tx_sleep/dmcs_wakeup/cgi/freeze_fw/...)
```
**Confirmaciones que el RF devuelve al ML1 (`RFA_RF_LTE_*_CNF`):**
```
0xc404dce1  RFA_RF_LTE_ENTER_MODE_CNF           <<< el "enter_mode_cnf" (msgr_send @0xce8bd84f)
0xc404ddd2  RFA_RF_LTE_EXIT_MODE_CNF
0xc404de20  RFA_RF_LTE_SLEEP_CNF
0xc404de35  RFA_RF_LTE_CDRX_SLEEP_CNF
0xc404df57  RFA_RF_LTE_WAKEUP_CNF
0xc404e0a1  RFA_RF_LTE_CDRX_WAKEUP_CNF
0xc404def4  RFA_RF_LTE_RF_CHECK_CONFIG_CNF
0xc404e031  RFA_RF_LTE_RF_CONFIG_CNF / 0xc404e154 RFA_RF_LTE_RF_CONFIG_COMPLETE_CNF
0xc404dd74  RFA_RF_LTE_L2L_BUILD_SCRIPTS_CNF
```
NR5G tiene el par simétrico `NR5G_ML1_RFMGR_*_REQ` (0xc400dbc8..) ↔ `RFA_RF_NR5G_*_CNF` (0xc400dbe1..).
**Todos FACT (nombres byte-exact en b21).** El **entero UMID** de cada uno es constante compilada →
**UNKNOWN numérico estático** (§1.2).

### 3.2 Diagrama del handshake y de dónde vive cada actor (FACT + INFERENCE)

```
 TASK: LTE ML1 (lte_ml1_rfmgr, seg27 región 0xce8bxxxx, "ONLINE")
   │  al adquirir LTE, la SM ML1 decide entrar por enter_mode
   │  (rfm_inst->wakeup_req.use_enter_mode==TRUE, assert @0xce8bd598)
   ▼  msgr_send( LTE_ML1_RFMGR_ENTER_ONLY_REQ )    ── ASÍNCRONO, cross-task ──►
 TASK: RF / RFA  (RFA Task, procesa el REQ, mueve reloj/potencia vía MCPM)
   │  precond: mcpm_req.return_trans_id != 0  (assert @0xce8bd818, MCPM ON)
   │  ejecuta el script de entrada (run_rf_script_msg, rxlm_buf_id válidos)
   ▼  msgr_send( RFA_RF_LTE_ENTER_MODE_CNF )        ◄── vuelve MÁS TARDE, otro contexto ──
 TASK: LTE ML1  (recibe el CNF en su cola; la SM avanza de estado)
```

- **Emisor del REQ:** task LTE-ML1 (módulo RFMGR). **Receptor:** task RF/RFA. **Emisor del CNF:** RF/RFA
  (`msgr_send(&enter_mode_cnf...)` @0xce8bd84f, precedido por la validación MCPM @0xce8bd818).
  **Receptor del CNF:** LTE-ML1. **FACT (asserts) + INFERENCE (identidad de emisor/receptor por prefijo
  `rfm_inst`/`LTE_ML1_RFMGR`/`RFA_RF_LTE`).**
- **Por qué es asíncrono:** `msgr_send` sólo **encola** el mensaje en la cola del task destino; el
  handler corre en el **scheduler de ESE task** cuando le toca (y sólo tras el handshake MCPM de
  reloj/potencia, que a su vez espera al HW). No hay retorno síncrono del resultado del RF: el resultado
  vuelve como **otro mensaje (`*_CNF`)** en la cola del emisor original. Los `*_IND` (p.ej.
  `LTE_ML1_RFMGR_WAKEUP_RX_TUNED_IND` @0xc405ae4f) son notificaciones asíncronas puras. **INFERENCE
  fuerte (modelo MSGR estándar) + FACT (existencia de REQ/CNF/IND separados).**

### 3.3 Por qué en factory-test ciertos comandos "cuelgan" (los CNF que no llegan)

- En **factory-test** el task ML1-online **no corre su flujo de servicio**, así que **`LTE_ML1_RFMGR_ENTER_ONLY_REQ` nunca se emite**. El path FTM (`rflte_ftm_mc_wakeup` @0xce6e08a8,
  `rflte_mc_carrier_activate` @0xce6e72c0) hace el wakeup del device **directo**, sin el par REQ/CNF por
  MSGR. Como el driver espera un CNF que no se pidió, el comando parece "colgado". **FACT (módulos
  distintos) + INFERENCE (por eso no llega el CNF).** (Ver `enter_mode_path.md §1.3/§3.2.)**
- Segunda causa de "cuelgue/SSR" en FTM: aunque el activate FTM corra, el **carrier-apply**
  (`0xd81bd018`, allocframe @**0xd81bd01c**) sólo escribe el `active-carrier ptr` (`0xca79c494`) si pasan
  los gates `gp+0x740==2` (modo RF cal) y `gp+0x7000!=NULL` (ctx RF). Si el RF no está encendido/activado,
  `0xca79c494` queda NULL y el siguiente measure/capture hace `err_fatal` (getter `0xd827923c`). Esto NO
  es un mensaje MSGR que falta: es **estado de HW** que el activate no completó. **FACT
  (`map_ftm_framework.md §3`).**
- **Mensajes MSGR que en FTM se esperan pero no llegan** (los que explican el hang):
  `RFA_RF_LTE_ENTER_MODE_CNF`, `RFA_RF_LTE_RF_CONFIG_CNF`, `RFA_RF_LTE_WAKEUP_CNF` — todos son
  respuestas del par ML1↔RFA del path **ONLINE**; en FTM no hay quien las genere. **INFERENCE fuerte.**

---

## 4. QMI interno — DMS set_operating_mode y cómo factory-test altera los tasks RF

### 4.1 El path del cambio de modo (FACT strings + INFERENCE de la cadena)

DIAG/QMI DMS `set_operating_mode` (ONLINE / OFFLINE / LOW_POWER / FTM) **no toca el RF directamente**;
entra al **Call Manager (CM)** que emite un **phone-command de cambio de preferencia**:

```
QMI DMS set_operating_mode(oprt_mode)      (oprt_mode strings @0xc3789c68 "operating_mode",
   │                                        0xc375fbe6 "oprt_mode", 0xc37700f9 "oprt_mode_req")
   ▼
cm_ph_cmd_pref_change_req(...)             (0xc40cc45e "cm_ph_cmd_pref_change_req failed";
   │                                        "oprt_mode = %d CMPH error %d" @0xc3fddaa5)
   ▼
MMOC (Multimode Controller)                ("=MMOC= Phone should be in online mode" @0xce64ebb0)
   │   decide arrancar/parar los subsistemas de protocolo y el RF
   ▼
tasks RF/ML1 arrancan (ONLINE) o entran a modo test (FTM/CAL)
```
Estados de `oprt_mode` visibles: `MODE_ONLINE` (0xc423186f), `MODE_OFFLINE` / `MODE_OFFLINE_CDMA` /
`MODE_OFFLINE_AMPS`, **`MODE_OFFLINE_IF_NOT_FTM`** (0xc42318b8), y el flag **`FTM_MODE`** (0xc37bd180) /
`FACTORY TEST MODE (FTM)` (0xc3928ff0). **FACT (strings) + INFERENCE (que la cadena QMI→CM→MMOC es la
canónica de Qualcomm y consistente con estos strings).**

### 4.2 El QMI service que maneja el modo

- **DMS (Device Management Service)** es el que expone `set_operating_mode`. En este PD del modem el
  handler de mode-change se ve por CM (`cm_ph_cmd_pref_change_req`) y el nivel QMI-DMS por el string
  `QMI_DMS_ERR_FATAL at line:%d` (0xcea82c8b, seg27). El **subsys DIAG 0x5B (91)** (map_diag_core §3,
  73 entradas, handlers 0xda019xxx) es el candidato de **Modem-mgr/QMI dispatch** interno. **FACT
  (strings) + INFERENCE (identidad DMS↔0x5B).**
- El QMI viaja sobre **QMUX/QRTR**; para factory-test el driver típicamente usa el DIAG-FTM (subsys 0x0B)
  **o** QMI-DMS `set_operating_mode(FTM)`. Ambos terminan cambiando el mismo **flag global de modo**. **INFERENCE.**

### 4.3 Cómo factory-test mode cambia el comportamiento de los tasks RF (FACT + INFERENCE)

1. **Barrera dura de modo:** existe un flag global (FTM/CAL vs ONLINE) que **particiona** el código RF.
   Strings: `Attempt to access FTM variables in ONLINE MODE` (0xce6d59e0 x3),
   `FTM_RF_MODE_CAL` (0xce6cf902), `MMOC= Phone should be in online mode` (0xce64ebb0). El código FTM
   aborta si se lo llama en ONLINE y viceversa. **FACT.**
2. **En ONLINE:** el task LTE-ML1 corre su SM de servicio; el RF se maneja por el par MSGR
   `LTE_ML1_RFMGR_*_REQ` ↔ `RFA_RF_LTE_*_CNF` (§3). El activate del carrier lo dispara la adquisición
   real de red.
3. **En FTM/CAL:** el task ML1-online **no arranca su servicio**; el RF se maneja por el árbol
   **`rflte_ftm_*` / `rflte_dispatch_*`** (wakeup/activate directos, sin el par REQ/CNF por MSGR). El
   flag global `gp+0x740` pasa a **2** (modo RF cal) y se crea el ctx que puebla `gp+0x7000`; sólo
   entonces el carrier-apply `0xd81bd018` escribe `0xca79c494`. **FACT (módulos) + INFERENCE (side-effect
   del set_mode cal sobre gp+0x740/gp+0x7000).**
4. **Consecuencia operativa:** para el driver, **entrar a FTM cambia qué código atiende cada comando RF**
   (de ML1-online a rflte_ftm_*), y **desactiva** el handshake enter_mode/CNF. Por eso "esperar el CNF"
   no destraba nada en FTM; hay que completar el **activate FTM** (BAND+EARFCN) para que `gp+0x740==2` y
   `gp+0x7000!=NULL`. **INFERENCE fuerte (base FACT).**

---

## 5. Callbacks runtime (callr) — el binding que el estático no puede seguir

### 5.1 La tabla per-tech `@0xca733d10` (commit FTM) — FACT

Del commit FTM **`0xd81dfecc`** (verificado byte-a-byte):
```
d81dff14:  r16 = memw(r2+#0xc)              ; r16 = session->0xc   (MODO de la sesión FTM, enum)
d81dff20:  r3  = memb(r2+##0x89a8)          ; post-check ctx (==7 = "no-tech")
d81dff24:  r17 = memw(r2+#0x4)              ; r17 = arg
d81dff28:  immext(#0xca733d00)
d81dff2c:  r21 = add(##0xca733d10, asl(r21,#0x2))   ; &tabla[tech]   (stride 4 en este acceso)
d81dff34:  r2  = add(r16,#-0x1)             ; tech = session->0xc - 1
d81dff38:  if (!cmp.gtu(r2,#0x13)) ...      ; validez: tech-1 <= 0x13  (1..0x14 = 20 techs)
d81dff48:  r2  = memw(r21+#0x0)             ; record = tabla[tech]
d81dff4c:  r2  = memw(r2+#0x4)              ; fn = record->+4          <<< PUNTERO A CALLBACK
d81dff50:  if (cmp.eq(r2,#0)) jump ...      ; si fn==NULL → no-op (rama else)
   ... (rama con fn!=NULL corre el callback del módulo FTM per-tech)
```
- **`@0xca733d10`** es un array **per-tech** de records `{ctx@+0, fn@+4}`; el commit hace
  **`callr` a `memw(record+4)`** (el callback del módulo FTM de esa tech: `ftm_lte_*` para tech LTE).
  Índice = `session->0xc - 1` (rango 1..0x14 = **20 techs**). **FACT.**
- **Cuándo se dispara:** cada TECH_ENTER/commit FTM (RFDEBUG sub 0x0d → 0xd816d658 → commit 0xd81dfecc).
  El callback per-tech es el que realmente enciende/configura el RF de esa tech en FTM. **FACT.**
- **Cuándo/quién lo registra:** el record `tabla[tech].fn` se **puebla en runtime** al inicializar cada
  módulo FTM per-tech (no hay store estático del puntero legible; es binding dinámico). **INFERENCE
  fuerte** (el estático sólo ve la lectura+`callr`, no el escritor del slot).

### 5.2 El callback carrier-activate `0xd81bd018` (allocframe @0xd81bd01c) — FACT

```
d81bd018:  call 0xd814e6ac                  ; prólogo/log
d81bd01c:  allocframe(#0x158)               ; <<< inicio real de la función (map_ftm_framework)
   ... G-mode (memb(gp+0x740)==2) @0xd81bd160
   ... G-ctx  (0xd8286240 ⇒ memw(gp+0x7000)!=0) @0xd81bd28c
   ... si ambos OK → APPLY 0xd8273b2c → SETTER 0xd8279264 : memw(0xca79c494)=0xca6e3b88
```
- **`find_refs 0xd81bd018` = 1 solo caller estático: `0xd81e53dc`** (dentro del init **`0xd81e52c8`**).
  No hay puntero crudo en RAM → se **registra en runtime como handler MSGR/event**. Por eso el estático
  no puede seguir "quién lo dispara realmente": lo dispara el router MSGR cuando llega el mensaje de
  carrier-activate/enter, no una llamada directa. **FACT (find_refs) + INFERENCE (registro MSGR/event).**
- El init `0xd81e52c8` arma una tabla de descriptores en la pila (`memw(r29+0x28/0x2c/0x48)` con
  constantes `##0xc37c2f..`, `##0x63e66dd8`, `##0xd81e5400`) y registra el callback: es el **punto de
  binding runtime** del carrier-activate. **FACT (disasm) + INFERENCE (semántica de registro).**

### 5.3 Por qué esto explica el binding que el estático no ve

- `msgr_send` enruta por UMID a una **cola** cuyo **handler fue registrado en runtime** (`msgr_register_block`). El estático ve el `msgr_send` y ve la función handler, pero **no el edge**
  `send→handler` (lo resuelve `msgr_table` en RAM). Igual con `@0xca733d10[tech]` y `0xd81bd018`: el
  puntero de destino se escribe en runtime. **Para cerrar estos edges hay que:** (a) leer `msgr_table` /
  `@0xca733d10` en vivo, o (b) loggear `UMID=0x%08X` y correlacionar. **FACT (mecanismo) + INFERENCE.**

---

## 6. FACT / INFERENCE / UNKNOWN — con VAs

### FACT
- **API MSGR** (strings seg27): `msgr_client_create` (0xce7448ae/0xce945a27), `msgr_client_add_mq_dynamic`
  (0xce945a77), `msgr_register_block` (0xce945b0f), `register_block_variant_queue` (0xce8ac06e/0xce8cbe06),
  `msgr_send` (0xce8bd84f + 281), `msgr_send_direct` (0xce8c91f7 + 19), `msgr_send_msg(UMID=0x%08X)`
  (0xce68146b), `msgr_receive_nonblock` (0xce748185), `msgr_query_umid` (0xce8cbde3),
  `msgr_deregister_block_variant[_queue]` (0xce697396/0xce8ac067).
- **UMID:** 32 bits (`UMID=0x%08X`); `tech_module = (tech&0xFF)<<8 | (module&0xFF)` byte-exact @0xce945b3c;
  15 clases de módulo/tech (assert @0xce5940fc); `msgr_id_index_tbl` 592 slots (@0xce593fba);
  `MSGR_ID_VAL(NAME)` (@0xce5cd152). `msgr_hdr_struct_type.id` = UMID (@0xce734a81).
- **Colas por cliente:** LTE-ML1-SCHDLR con `ul/ll/hi_pri` mq (@0xce8cbe06–0xce8cc05a); fast-path
  `msgr_send_direct(hi_pri_msgr_client)`.
- **Tasks (nombres):** `diag_task_tcb` (0xce585bf9), `FTM TASK RFGSM Mailbox` (0xc37bca3a),
  `GSM L1 Common RFA Task Mailbox` (0xc3fb6b86), `RF Task Init Status` (0xc3928806), clientes
  `lte_ml1_*_msgr_client` / `nr5g_ml1_*_msgr_client`, `qurt_thread_create` (0xce5d1f2d),
  `rcinit_lookup_rextask` (0xce586380), `rcevt_*` (0xce74449f).
- **UMIDs enter/cnf (b21):** `LTE_ML1_RFMGR_ENTER_ONLY_REQ` (0xc404e0bc), `RFA_RF_LTE_ENTER_MODE_CNF`
  (0xc404dce1), tabla `LTE_ML1_RFMGR_*_REQ` (0xc404dc..0xc404e1) ↔ `RFA_RF_LTE_*_CNF`; simétrico NR5G
  (0xc400dbc8.. / 0xc400dbe1..). `msgr_send(&enter_mode_cnf...)` @0xce8bd84f, precond MCPM @0xce8bd818.
- **Modo/QMI:** `operating_mode`/`oprt_mode` (0xc3789c68/0xc375fbe6/0xc37700f9),
  `cm_ph_cmd_pref_change_req` (0xc40cc45e), `oprt_mode = %d CMPH error %d` (0xc3fddaa5),
  `MODE_ONLINE`/`MODE_OFFLINE_IF_NOT_FTM`/`FTM_MODE` (0xc423186f/0xc42318b8/0xc37bd180),
  `Attempt to access FTM variables in ONLINE MODE` (0xce6d59e0), `FTM_RF_MODE_CAL` (0xce6cf902),
  `QMI_DMS_ERR_FATAL` (0xcea82c8b). Subsys DIAG 0x5B (modem-mgr/QMI) — map_diag_core §3.
- **callr per-tech:** commit `0xd81dfecc` lee `session->0xc` (@0xd81dff14), indexa `@0xca733d10`
  (@0xd81dff2c), `fn = memw(record+4)` (@0xd81dff4c) y `callr`. Callback carrier-activate `0xd81bd018`
  (allocframe @0xd81bd01c), único caller estático `0xd81e53dc` dentro del init `0xd81e52c8`; escribe
  `0xca79c494=0xca6e3b88` tras gates `gp+0x740==2` y `gp+0x7000!=0`.

### INFERENCE
- Emisor `ENTER_ONLY_REQ` = task LTE-ML1; receptor/emisor del `ENTER_MODE_CNF` = task RF/RFA; receptor
  del CNF = LTE-ML1 (por prefijos `rfm_inst`/`RFA_RF_LTE`). La asincronía es intrínseca a `msgr_send`
  (encola en cola de otro task; el resultado vuelve como `*_CNF`).
- En FTM el `ENTER_ONLY_REQ` no se emite (path `rflte_ftm_*` directo) ⇒ el CNF no vuelve ⇒ comandos
  que esperan el CNF "cuelgan".
- QMI DMS `set_operating_mode` → `cm_ph_cmd_pref_change_req` → MMOC → arranque/parada de tasks RF/ML1;
  el flag global FTM/ONLINE particiona el pipeline RF y decide qué código atiende cada comando RF.
- `@0xca733d10[tech].fn` y `0xd81bd018` se registran/pueblan en runtime (binding dinámico); el estático
  ve la lectura+`callr`/`msgr_send` pero no el edge de destino.
- Prioridades de cola: `hi_pri` > `ul` > `ll` (por nombre/uso fast-path). Task-priorities rex/QuRT no
  legibles como string.

### UNKNOWN (sólo en vivo / con .h ausente)
- **Entero numérico de cada UMID** (`MSGR_ID_VAL(NAME)`): constante compilada; resolver con
  `msgr_query_umid` o loggeando `UMID=0x%08X`.
- Contenido runtime de `msgr_table` / `msgr_jump_table[tech]` (qué cola está suscrita a qué UMID).
- Contenido runtime de `@0xca733d10[LTE]` (si `fn` está poblado) y del handler registrado por
  `0xd81e52c8`.
- Prioridad numérica exacta y stack-size de cada task rex/QuRT (tabla rcinit no expuesta).
- El subsys/UMID QMI-DMS numérico exacto que este build usa para `set_operating_mode`; layout del payload.
- Valor en vivo de `gp+0x740` / `gp+0x7000` (estado RF) y de `0xca79c494`.

---

## 7. Reproducir
```bash
cd /tmp/modemre
# --- API MSGR + macro UMID (seg27 @0xce480000) ---
python3 - <<'PY'
d=open('seg27_dec.bin','rb').read(); B=0xce480000
def cs(va,n=200):
    o=va-B; s=d[o:o+n]; e=s.find(b'\x00'); return s[:e].decode('latin1','replace')
for va in (0xce945b0f,0xce945a77,0xce8c91f7,0xce68146b,0xce5940fc,0xce593fba,0xce8bd84f,0xce8ac06e):
    print(hex(va), '=>', cs(va))
PY
# --- UMID names enter/cnf (b21 @0xc3553000) ---
python3 - <<'PY'
import re
d=open('modem.b21','rb').read(); B=0xc3553000
for kw in (b'LTE_ML1_RFMGR_ENTER_ONLY_REQ',b'RFA_RF_LTE_ENTER_MODE_CNF',b'RFA_RF_LTE_WAKEUP_CNF'):
    m=re.search(re.escape(kw),d); print(hex(B+m.start()), kw.decode())
PY
# --- task/mailbox names ---
python3 - <<'PY'
import re
d=open('modem.b21','rb').read(); B=0xc3553000
for kw in (b'FTM TASK RFGSM Mailbox',b'GSM L1 Common RFA Task Mailbox',b'RF Task Init Status'):
    m=re.search(re.escape(kw),d); print(hex(B+m.start()), kw.decode())
PY
# --- callr per-tech + carrier-activate binding ---
./dis.sh 0xd81dfecc 0x120      # commit: session->0xc, @0xca733d10[tech], callr memw(record+4)
./dis.sh 0xd81bd018 0x40       # carrier-activate handler (allocframe @0xd81bd01c)
python3 find_refs.py 0xd81bd018   # 1 caller estático: 0xd81e53dc (init 0xd81e52c8) => runtime-registered
# --- modo/QMI ---
python3 - <<'PY'
import re
d=open('modem.b21','rb').read(); B=0xc3553000
for kw in (b'oprt_mode_req',b'cm_ph_cmd_pref_change_req failed',b'MODE_OFFLINE_IF_NOT_FTM',b'FTM_MODE'):
    m=re.search(re.escape(kw),d); print(hex(B+m.start()), kw.decode())
PY
```
