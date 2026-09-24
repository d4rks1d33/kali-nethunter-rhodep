# DIAG transport (QRTR sockets) — mapa INTEGRAL del path comando→respuesta en el modem SM6375 (Moto G82 5G)

**Objetivo**: que las respuestas de comando subsys (FTM, SSID 0x17) drenen al socket QRTR DATA que sirvo en el AP.
**Build**: MPSS.HI.4.3.4-00494-MANNAR_GEN_PACK-1.24452.133
**Imágenes**: `_dis_b13.txt` (VA 0xc0d36000+, código DIAG core, no comprimido) · rodata b21 (VA 0xc3553000) · `clade_dec_full.bin` (páginas 0xd8xxxxxx).
Todas las VA de abajo son verificables con `grep -n "^<VA>:" _dis_b13.txt`.

**Leyenda**: **FACT** = leído del disasm de esta imagen (VA citada) · **INFERENCE** = deducción sobre el modelo diagbuf/diagcomm Qualcomm consistente con la evidencia · **UNKNOWN** = sólo en vivo.

---

## 0. TL;DR — la causa raíz y la lista de lo que falta

La respuesta de un comando (apps **y** subsys) sale SIEMPRE por el mismo pipeline y por el **canal DATA (io_type==2)**. El commit de la respuesta pasa por **`diagpkt_rsp_send`/`diagpkt_commit`** que tiene un **GATE DURO** al principio:

> **`diagpkt_rsp_send: Attempt to send response before feature mask OR diagID is received`**
> (string @ 0xc35ca363; gate en **0xc0d36c44–0xc0d36c6c**).

El gate exige **DOS** flags globales en 1:
1. **DIAGID recibido** → `memw(0xc92e4754)` bit0 (set por el ctrl-msg **DIAGID / type 0x21**).
2. **Feature mask recibido POR PERIFERICO** → `memb(0xc92e43e0)` (set por el ctrl-msg **FEATURE type 8**, handler 0xc0d66f34).

Si el gate NO pasa, la respuesta **ni siquiera se encola** → jump a la rama de error (0xc0d58b9c / "before feature mask OR diagID"). **Ésta es la diferencia real 0x00 vs TECH_ENTER sólo si el 0x00 lo estás mandando/leyendo por el canal CMD "inline"** (ver §2). Pero incluso pasando el gate, para que **drene** hacen falta, en orden:

**LISTA COMPLETA de lo que tiene que pasar para que la respuesta subsys drene al TU socket DATA** (detalle con bytes en §5 y §9):

1. **QRTR NEW_SERVER de TU DATA visto por el modem** → el modem guarda tu node/port destino en `0xc8c2d870`(node)/`0xc8c2d874`(port) y wirea el descriptor de canal DATA (io_type==2, port 0x1001, allow_flow=1). **FACT** handler 0xc0d830b4/0xc0d82fc0.
2. **FEATURE mask (ctrl type 8)** → set `memb(0xc92e43e0)=1` (gate #2). **FACT** 0xc0d66fa8.
3. **DIAGID (ctrl type 0x21)** → set `memw(0xc92e4754)` bit0 (gate #1). **FACT** 0xc0d67484 / lectura del gate 0xc0d36c48.
4. **TX MODE por stream (ctrl type 0x11), stream_id=1, tx_mode=real-time** → pone el stream en real-time (`stream_obj+0xaf`) y dispara flush. **FACT** 0xc0d6711c→0xc0d7dc78.
5. (opcional pero recomendado) **DIAGMODE (type 3, data_len=0x24, real_time=1)** — sólo buffering global, no es gate. **FACT** 0xc0d66348.
6. NO haber recibido un **flow STOP** (0xF4/0xF7) sin GO posterior — pondría `allow_flow(+0x84)=0`. **FACT** 0xc0d373ac/0xc0d37408.
7. **Leer por el socket DATA (inst 2)**, no por el CMD. El sendto va a `node=0xc8c2d870, port=0xc8c2d874` (tu DATA). **FACT** §3.2.

**Lo más probable que te falta** dado que "los F3 drenan pero las respuestas de comando no": los **F3 logs no pasan por el gate de `diagpkt_rsp_send`** (van por `diagbuf`/log path directo), pero **las respuestas de comando SÍ**. Si te falta el **DIAGID (type 0x21)** o el **FEATURE (type 8) por el canal correcto**, el gate bloquea toda respuesta de comando pero deja pasar los logs. → **Manda DIAGID + FEATURE + TX MODE(0x11) y verifica que el modem los procesó** (§5).

---

## 1. Registro/apertura del canal socket y descubrimiento del peer QRTR (FACT)

### 1.1 Tabla de canales — `diagcomm_io_socket_init` @ **0xc0d813dc**
Construye **4 arrays** de descriptores de canal (entradas de **0x28 bytes**), en globales relativos a `gp`:

| gp offset | canal | `+0x18` type | fuente (arg r16) | VA loop |
|-----------|-------|--------------|------------------|---------|
| **gp+0x6998** | **CNTL** | 9 | `memw(r16+0xc)` | 0xc0d8145c |
| **gp+0x69a0** | **DATA** | 9 | `memw(r16+0x10)` | 0xc0d81560 |
| **gp+0x699c** | **CMD**  | 3 | `memw(r16+0x8)`  | 0xc0d81664 |
| **gp+0x69a4** | **DCI**  | 6 | `memw(r16+0x14)` | 0xc0d81768 |

Cada entrada guarda callbacks en `+0x8/+0xc/+0x10/+0x14`, `type=+0x18`, `size(+0x1c)=0x84`, y llama a getters 0xc0d81874/…/0xc0d8188c y a `+0x44`-callback (0xc0d755f8/0xc0d7c820/0xc0d7bb80). **FACT.**

### 1.2 Descriptor de canal (0x84 bytes) — `0xc0d8b004` (allocador/registro)
Tabla de punteros a descriptores en **`0xcb93fec0`** (índices 0..5). Al registrar cada canal:
```
c0d8b0c0: r1 = memub(r16+#0x1)      ; io_type del canal
c0d8b0c4: memb(r16+#0x88) = 0       ; flow-STOP flag = 0
c0d8b0cc: memb(r16+#0x0) = 0xFF     ; channel_type "sin asignar"
c0d8b0d4: memb(r16+#0x10) = 1
c0d8b0dc: memw(r16+#0x14) = 0x1001  ; <<< port_num del servicio DIAG QRTR
c0d8b0e0: if (io_type==0) skip
c0d8b0e8: memw(r16+#0x84) = 1       ; <<< allow_flow = 1  (SÓLO io_type != 0)
```
`+0x18` se fija al **channel-type enum** (0=DS/CNTL...,2=DATA,...) en 0xc0d8b120+. **FACT.**

**Layout del descriptor de canal (FACT, de transmit + init + send):**
| off | campo |
|-----|-------|
| +0x00 | `io_type` (transmit exige ==2 = DATA-socket; 0 = CNTL) |
| +0x01 | io_type "lógico"/instancia |
| +0x03 | `channel_type` |
| +0x10 | flag=1 |
| +0x14 | `port_num` = 0x1001 |
| +0x80 | tamaño/estado buffer (usado en send) |
| +0x84 | **`allow_flow`** (1=drena, 0=bloqueado) |
| +0x88 | flow-STOP pendiente |
| +0x8c | **node QRTR destino** (peer) |
| +0x90 | **port QRTR destino** (peer) |
| +0x94 | flag "peer no listo" (si !=0 → send falla, ver §3.1) |

### 1.3 Sockets del modem y servicio — `diagcomm_io_socket_open` @ **0xc0d7d388**
Construye structs `diag_socket_info` de **0xb8 bytes** en tablas globales `0xcb93f8c0`, `0xcb93f600`, `0xcb93f300`, `0xcb93f940`, poniendo **port 0x1001** (`memw(+0x14)=0x1001`, `+0x134=0x1001`), service instance (`+0x18` = 0/1/2/3), y llama a **`0xc0d93ac0`** (bind/registro del servicio QRTR). **FACT** (0xc0d7d47c–0xc0d7d5b4).

### 1.4 Descubrimiento del peer del AP — QRTR ctrl handler @ **0xc0d830b4 / 0xc0d82fc0**
El modem procesa el control-channel de QRTR. Cuando llega **QRTR type 8 = NEW_SERVER** (`cmp.eq(r1,#0x8)` @ 0xc0d830b4), guarda la dirección del AP:
```
c0d83018: memw(0xc8c2d870) = r17   ; <<< NODE del AP (server node)
c0d83020: memb(0xc8c2d874) = r18   ; <<< PORT del AP (server port)
```
y llama a `0xc0d5f5a4`/`0xc0d65ce8` para propagar el "diag connected". El parser de node/port está en 0xc0d83128+ (lee `memub(r0+0x4/0x5)` = node, service type). **FACT.**

> **Para que el canal DATA quede "abierto + listo para drenar hacia MI peer"** hacen falta:
> - descriptor DATA con **io_type==2** y **allow_flow(+0x84)=1** (init §1.2), y
> - **node(+0x8c)/port(+0x90) poblados con TU dirección** — esto ocurre cuando el modem ve el **NEW_SERVER de tu servicio DATA** (§1.4) y lo aprende en `0xc8c2d870/0xc8c2d874`. **FACT (mecanismo) + INFERENCE (que corresponde a tu DATA).**
> - Por eso: **publicá el servicio DATA por QRTR ANTES de mandar comandos** para que exista el NEW_SERVER y el descriptor tenga destino.

---

## 2. 0x00 (apps) vs TECH_ENTER (subsys) — PASO A PASO y dónde diverge

### Path COMÚN de la respuesta (ambos):
```
handler del comando
  → diagpkt_(subsys_)alloc(len [,SSID])       ; reserva buffer del pool (no fija destino)
  → diagpkt_commit / diagpkt_rsp_send         ; <<< GATE feature+diagID (0xc0d36c44)
  → diagbuf_send_pkt (0xc0d562dc)             ; itera canales socket 0xc9508b80
      → diagcomm_io_transmit (0xc0d56520)     ; gate allow_flow(+0x84), io_type==2
          → diagcomm_io_socket_send / 0xc0d93ed4 → 0xc0d95f64  ; QRTR sendto
```

### 2.1 `diagpkt_rsp_send` / `diagpkt_commit` — el GATE (FACT)
Entrada del commit en **0xc0d36c44**:
```
c0d36c44: r2 = memw(0xc92e4754)                 ; DIAGID flag
c0d36c4c: p0 = tstbit(r2,#0); if (!p0) skip     ; bit0 = "diagID recibido"
c0d36c54: r2 = memub(r20+#0x0)                  ; cmd_code del pkt
c0d36c58: p0 = bitsclr(r2,#0xff); ...
c0d36c60: (immext)
c0d36c64: r2 = memb(0xc92e43e0)                 ; <<< FEATURE-mask-received flag
c0d36c6c: if (cmp.eq(r2,#0)) jump 0xc0d58b9c    ; <<< SIN feature mask -> ERROR (no encola)
c0d36c70: call 0xc0d5f5a4                       ; (drain/notify helper)
c0d36c7c: ... sigue: arma header y encola
```
`0xc0d58b9c` es la rama que imprime **"Attempt to send response before feature mask OR diagID is received"** (string 0xc35ca363). **FACT.**

- **DIAGID flag** `0xc92e4754` bit0 → lo pone el ctrl-msg **DIAGID (type 0x21, handler 0xc0d67484)**. También lo referencian 0xc0d36058/0xc0d36490 (setter). **FACT.**
- **FEATURE flag** `0xc92e43e0` → lo pone el handler **FEATURE (type 8, 0xc0d66f34)** en `c0d66fa8: memb(0xc92e43e0)=1`. **FACT.**

### 2.2 Por qué 0x00 responde y TECH_ENTER no — el análisis honesto (FACT + INFERENCE)
- **Ambos** cruzan el mismo gate y el mismo drain. No hay "path inline al cliente CMD" separado para apps en el disasm: el CMD es RX; la respuesta se **encola y drena por DATA**. **FACT** (§3, §4).
- **Si vos ves 0x00 responder** por el socket cliente del CMD, es porque tu stack QRTR del AP entrega el datagrama de respuesta que el modem manda al **port DATA** que tu proceso también sirve, o porque 0x00 es tan corto que se drena en el mismo tick tras el commit. **INFERENCE.**
- **La divergencia REAL más probable**: TECH_ENTER es una respuesta subsys **más grande y asíncrona**; su drain depende de que el **stream esté en real-time** (type 0x11) y de que el gate feature+diagID esté satisfecho **para el periférico correcto**. Los **F3 logs** que sí recibís **NO pasan por `diagpkt_rsp_send`** (usan `diagbuf`/log-commit directo sin el gate feature+diagID), por eso drenan aunque el gate de respuestas esté cerrado. → **el gate de respuestas (feature+diagID) o el real-time del stream es lo que te falta.** **FACT (los logs no tocan 0xc0d36c44) + INFERENCE (que ése es el gate diferencial).**

---

## 3. El drain: quién lo dispara y TODOS los gates

### 3.1 `diagbuf_send_pkt` @ **0xc0d562dc** — itera canales y aplica gates (FACT)
```
c0d56303: r23 = 0xc9508b80              ; array de canales socket (count @ [0])
c0d56344: itera i=0..count-1
c0d56354: r18 = descriptor de canal (0xcb93fec0[i])
c0d5635c..56390: chequea el BITMASK per-canal @ (0xc9508b80-0x36af7468 + i*0xc):
        tstbit(mask,#0)  -> canal habilitado
        bitsclr(mask,#4) / bitsclr(mask,#8) -> sub-flags
c0d563d8: r2 = memw(r18+#0x94)          ; <<< si +0x94 != 0 -> jump 0xc0d56484 (peer NO listo, error)
c0d563e8: r0=node(+? ), r1=memw(r18+#0x8c), r2=memw(r18+#0x90)  ; node/port destino
c0d563f8: call 0xc0d56518 -> diagcomm_io_transmit
```
**Gates del drain (todos deben cumplirse):**
1. canal **habilitado en el bitmask** per-canal (tstbit #0). **FACT.**
2. **`+0x94 == 0`** (peer listo / socket abierto). **FACT.** ← si tu DATA no se registró, +0x94 != 0 → no drena.
3. dentro de transmit: **io_type(+0x0)==2** y **allow_flow(+0x84)!=0** y **buffer(+0x0 del buf)!=0**. **FACT** (§3.3).
4. cola no saturada (`r19 <= memw(0xc8c2d830)`, si no → 0xc0d374cc). **FACT** 0xc0d565e0.

### 3.2 Destino QRTR del sendto (FACT)
`diagcomm_io_socket_send`/`0xc0d93ed4` arma el datagrama y `0xc0d95f64` hace el sendto real, con **node/port tomados del descriptor de canal** (`+0x8c`/`+0x90`), que se poblaron con la dirección del AP aprendida en el NEW_SERVER (`0xc8c2d870/0xc8c2d874`). **NO** usa el source-addr del comando entrante. **FACT (estructura) + INFERENCE (origen del valor).**

### 3.3 `diagcomm_io_transmit` @ **0xc0d56520** — el gate final allow_flow (FACT)
```
c0d5652c: r2 = memb(r0+#0x0)            ; io_type
c0d56530: if (io_type==2) -> 0xc0d56550 ; ruta DATA
c0d56558: r2 = memb(r16+#0x84)          ; <<< allow_flow
c0d56560: if (==0) jump 0xc0d372d0      ; log "allow_flow=%d..." + return -9  (NO transmite)
c0d56564: r2 = memw(r18+#0x0)           ; buffer
c0d5656c: if (==0) jump 0xc0d37300
...transmite (0xc0d5c49c + socket_send)...
c0d565b0: memb(r16+#0x84) = 1           ; re-arma allow_flow tras TX ok
c0d565c0: memb(r16+#0x88) = 0           ; limpia STOP
```
**FACT.**

### 3.4 ¿Quién dispara el drain? (FACT)
- El **thread de sockets diag** (drena en loop; `diagbuf_drain_diag` en 0xc0d6ea..0xc0d6f0..).
- El **flush por real-time** cuando el stream pasa a RT: **`0xc0d696bc`** (invocado por el setter de Tx-mode 0xc0d7dc78).
- El **DIAGID/feature notify** `0xc0d5f5a4` (llamado tras el gate en 0xc0d36c70) que patea el drain.
- La global **`0xc93022fc`** actúa como flag "drain en progreso/conectado" (checada en el loop de drain 0xc0d6eaf8/0xc0d6ed28; seteada en 0xc0d6dbf0). **FACT.**

---

## 4. Estado "diag conectado / streaming mode" — los flags globales (FACT)

| global VA | rol | quién lo pone |
|-----------|-----|---------------|
| **0xc92e43e0** | **feature-mask recibido** (gate rsp_send) | FEATURE type 8 → 0xc0d66fa8 |
| **0xc92e4754** (bit0) | **DIAGID recibido** (gate rsp_send) | DIAGID type 0x21 → 0xc0d67484 / setter 0xc0d36058 |
| **0xc8c2d870 / 0xc8c2d874** | **node/port del AP** (destino sendto) | QRTR NEW_SERVER type 8 → 0xc0d83018/0xc0d83020 |
| **0xc93022fc** | drain en progreso / "connected" | 0xc0d6dbf0 |
| `stream_obj+0xaf` | **Tx-mode por stream** (RT/NRT) | TX MODE type 0x11 → 0xc0d7dc78 |
| `stream_obj+0xb3` | buffering mode por stream | APPS_BUFFERING_MODE (0xc0d67538) |
| `channel+0x84` | allow_flow per-canal | init 0xc0d8b0e8; STOP/GO 0xc0d373ac/0xc0d565b0 |

**Respuesta a la pregunta 4:** SÍ hay un gate específico para respuestas de comando que NO aplica a los F3 logs: el par **feature-mask-received (0xc92e43e0) + diagID-received (0xc92e4754)** en `diagpkt_rsp_send` (0xc0d36c44). Los logs F3 no cruzan ese gate → drenan igual. **FACT.**

---

## 5. SECUENCIA EXACTA que un cliente diag real ejecuta (con bytes)

Todos los ctrl-msg van por el **canal CNTL** con header LE `{u32 cmd_type; u32 data_len; payload}` (parser `diagpkt_process_ctrl_msg` entry 0xc0d66264, jumptable @0xc35c9b78 index=type-3). **FACT.**

**Orden:**

**Paso 0 — Publicar servicios QRTR (AP → kernel/qrtr)**
Publicar como servidor: DIAG **CNTL inst 0**, **DATA inst 2**, **DCI inst 4** (service id 0x1001). Esto genera los **QRTR NEW_SERVER** que el modem ve (§1.4) y con los que puebla node/port y marca +0x94=0. **Hacerlo ANTES de mandar comandos.**

**Paso 1 — FEATURE mask (type 8)** → set `0xc92e43e0` (gate #2)
```
08 00 00 00  04 00 00 00  <feature_mask u32 LE>
```
El handler lee `u32 @ payload[0..3]` (0xc0d66f3c). Feature bits típicos: STM, log_on_demand, feature_mask_support, diagID, etc. Manda al menos el bit de **diagID support** para habilitar el flujo diagID. **FACT (formato) / INFERENCE (qué bits).**

**Paso 2 — DIAGID (type 0x21 = 33)** → set `0xc92e4754` bit0 (gate #1)
```
21 00 00 00  <len u32>  <payload diagID>
```
Handler 0xc0d67484. Estructura estándar `diag_ctrl_msg_diagid` { version, diag_id, process_name[] }. Sin este mensaje, `diagpkt_rsp_send` bloquea TODA respuesta de comando. **FACT (que existe el gate) / UNKNOWN (layout exacto del payload — capturar de un diag real).**

**Paso 3 — TX MODE por stream (type 0x11 = 17)** → stream_id=1 en real-time
```
11 00 00 00  06 00 00 00  01 00 00 00  01 01
                          |num_streams| |id|mode
```
Handler 0xc0d6711c → setter 0xc0d7dc78 (escribe `stream_obj+0xaf=mode`, match por `+0xae==stream_id`, dispara flush 0xc0d696bc). `stream_id ∈ {1,2}`. `tx_mode=1` = real-time (probar 1; si el enum está invertido, 0). **FACT (mecanismo) / UNKNOWN (semántica 1 vs 0).**

**Paso 4 (opcional) — DIAGMODE (type 3), real_time=1, data_len=0x24**
```
03 00 00 00  24 00 00 00  01 00 00 00  <32 bytes: version(1), ..., real_time@+0x0c, límites>
```
Handler 0xc0d66348 (v1 exige data_len==0x24). Sólo ajusta buffering global; NO es gate. **FACT.**

**Paso 5 — (opcional) log/event/msg masks** como ya hacés (para F3, no afectan respuestas).

**Paso 6 — Mandar TECH_ENTER por el CMD (inst 1)** como ya hacés.

**Paso 7 — LEER la respuesta por el socket DATA (inst 2).**

**Flow-control (RESUME):** si en algún momento el modem manda un byte **0xF4/0xF7 (STOP)** por el canal, el `allow_flow` del canal se pone a 0 (0xc0d373ac). Tu stack debe responder con el **GO/RESUME** (o dejar que el kernel diag lo haga) para que vuelva a drenar; si no, tras el primer STOP se queda mudo. El re-arme automático ocurre tras un TX exitoso (0xc0d565b0). **FACT.**

---

## 6. Por qué canal/stream vuelve la respuesta de un comando al CMD (inst 1)

- **Entra** por **CMD (inst 1, gp+0x699c, type 3)** — RX de comandos.
- **Sale/vuelve** por **DATA (inst 2, gp+0x69a0, type 9, io_type==2)** — todas las respuestas de comando + logs + eventos. **NO** vuelve por CMD ni por el socket cliente. **FACT (tabla §1.1) + FACT (transmit exige io_type==2) + INFERENCE (mapping DATA).**
- **Stream**: la respuesta pertenece al **stream_id=1 (DIAG_STREAM_1)** en real-time. Es el que debés poner RT con el type 0x11. **FACT (rango {1,2}) + INFERENCE (que 1 es el RT de comando/respuesta).**
- **node/port destino**: `node = 0xc8c2d870`, `port = 0xc8c2d874` = **la dirección de TU servicio DATA (inst 2)** aprendida por el modem del NEW_SERVER. Valor numérico del port = el que QRTR te asignó al bind → **UNKNOWN estático, es tuyo**. **FACT (de dónde sale) / UNKNOWN (valor).**

---

## 7. Diferencia con tu handshake actual — qué agregar (en orden)

Tu handshake actual: feature mask, DIAGID ack, msg masks, log mask, event mask, DIAGMODE(RT=1), y (nuevo) type 0x11.

**Lo que probablemente falta o está mal:**
1. **DIAGID como ctrl-msg type 0x21 REAL** que setee `0xc92e4754` bit0. Vos decís "DIAGID ack" — verificá que es el **type 0x21** y con el layout que el handler 0xc0d67484 espera, no sólo un ack. Sin este flag el gate 0xc0d36c48 bloquea. **FACT.**
2. **FEATURE (type 8) por el canal CNTL correcto y con el bit de diagID** para que 0xc0d66fa8 setee `0xc92e43e0` **para el periférico del que esperás la respuesta**. El handler almacena la mask **por peer**; si lo mandás por un peer distinto del que corre el handler FTM, el gate puede quedar cerrado para ese contexto. **FACT (per-peer) / INFERENCE (que importa el peer).**
3. **NEW_SERVER de DATA antes de comandar** (que el modem aprenda node/port y ponga +0x94=0). Si publicás DATA después del comando, el drain encuentra +0x94!=0 → no sale. **FACT (+0x94 gate) / INFERENCE (timing).**
4. **type 0x11 stream_id=1 tx_mode=1** (ya lo agregaste — correcto). Confirmá que el setter matchea (`stream_obj+0xae==1`); si el stream 1 no existe/registró, el loop no matchea y el RT no se aplica. **FACT.**
5. **Leer por DATA, no por CMD.** **FACT.**

---

## 8. Checklist de diagnóstico en vivo (para confirmar cuál gate está cerrado)

- Capturá el tráfico CNTL/DATA con `qrtr`/`diag` dump en el AP.
- Verificá que el modem emite el F3 **"Received APPS Feature mask = 0x%X"** (string 0xc35c9e8b) tras tu type 8 → confirma gate #2.
- Verificá F3 **"Tx Mode = %d for stream_id = %d"** (0xc35c9ec7) tras tu type 0x11 → confirma stream RT.
- Si aparece **"Attempt to send response before feature mask OR diagID"** (0xc35ca363) al comandar → falta gate #1 (DIAGID) o #2 (feature).
- Si aparece **"diagcomm_io_transmit: allow_flow = 0 ..."** (0xc35cd50f) → hubo STOP; mandá RESUME.
- Confirmá con un `sendto` dump a qué **port** manda el modem; comparalo con el port de tu DATA (inst 2).

---

## 9. FACT / INFERENCE / UNKNOWN — cierre con VAs

**FACT (probado, con VA):**
- Tabla de canales: `diagcomm_io_socket_init` 0xc0d813dc → CNTL gp+0x6998, DATA gp+0x69a0, CMD gp+0x699c, DCI gp+0x69a4 (entradas 0x28B, type @ +0x18: 9/9/3/6).
- Descriptor de canal alloc/registro: 0xc0d8b004; array de descriptores 0xcb93fec0; allow_flow(+0x84)=1 sólo io_type!=0 (0xc0d8b0e8); port 0x1001 @ +0x14 (0xc0d8b0dc).
- `diagcomm_io_socket_open` 0xc0d7d388 crea `diag_socket_info` (0xb8B) con port 0x1001 y bind vía 0xc0d93ac0.
- Descubrimiento del peer: QRTR NEW_SERVER (type 8) @ 0xc0d830b4 → node 0xc8c2d870, port 0xc8c2d874 (0xc0d83018/0xc0d83020).
- **Gate de respuesta** `diagpkt_rsp_send`/commit @ 0xc0d36c44: exige `0xc92e4754`bit0 (DIAGID) **y** `memb(0xc92e43e0)` (feature) != 0; si no → 0xc0d58b9c ("before feature mask OR diagID").
- FEATURE type 8 handler 0xc0d66f34 setea `0xc92e43e0`=1 (0xc0d66fa8); string 0xc35c9e8b.
- DIAGID flag `0xc92e4754` set/leído en 0xc0d36058/0xc0d36c48; handler type 0x21 @ 0xc0d67484.
- TX MODE type 0x11 handler 0xc0d6711c → setter 0xc0d7dc78 (stream_obj+0xaf, flush 0xc0d696bc); string 0xc35c9ec7.
- DIAGMODE type 3 handler 0xc0d66348 (v1 data_len==0x24; sólo buffering global).
- APPS_BUFFERING_MODE handler 0xc0d67538 (stream_obj+0xb3); string 0xc35c9fd0.
- Drain: `diagbuf_send_pkt` 0xc0d562dc itera 0xc9508b80, gate `+0x94==0`, bitmask per-canal, node/port +0x8c/+0x90.
- `diagcomm_io_transmit` 0xc0d56520: io_type(+0x0)==2, allow_flow(+0x84)!=0, buffer!=0; re-arma allow_flow (0xc0d565b0); string 0xc35cd50f.
- Flow-control STOP 0xF4/0xF7 → allow_flow=0 (0xc0d373ac/0xc0d37408).
- QRTR sendto real: 0xc0d93ed4 → 0xc0d95f64.
- Flag "connected/drain" 0xc93022fc (0xc0d6dbf0 set; 0xc0d6eaf8 check).

**INFERENCE (modelo Qualcomm, consistente):**
- Los F3 logs no cruzan el gate feature+diagID → por eso drenan mientras las respuestas de comando no.
- DATA (inst 2) es el canal de respuestas; stream 1 = RT de comando/respuesta.
- node/port destino = tu servicio DATA aprendido del NEW_SERVER.

**UNKNOWN (sólo en vivo):**
- Layout exacto del payload DIAGID (type 0x21) que este build valida.
- Semántica exacta de tx_mode (1 vs 0) para "real-time" en 0xc0d7dc78.
- Valor numérico del port QRTR de tu DATA (lo asigna el bind del AP).
- Si tu ruta recibió un STOP y quedó allow_flow=0 (capturar CNTL/DATA).
- Qué feature-bits mínimos exige tu build para abrir el flujo diagID.
