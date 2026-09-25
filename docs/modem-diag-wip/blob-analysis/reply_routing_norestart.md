# reply_routing_norestart.md — Por qué la respuesta del comando FTM NO vuelve al cliente en el path AP-initiated (--kick) y SÍ con --restart-modem (SM6375, MPSS.HI.4.3.4)

**Build:** MPSS.HI.4.3.4-00494-MANNAR_GEN_PACK-1.24452.133 (SM6375, Moto G82 5G) · Hexagon/QDSP6
**Imágenes/refs:** `_dis_b13.txt` (diag core+transporte, VA 0xc0d36000+) · `_dis_b05.txt` (0xc098xxxx) · rodata b21 (VA 0xc3553000)
Todas las VA verificables con `grep -n "^<VA>:" _dis_b13.txt` (o b05).

**Leyenda:** **FACT** = leído del disasm en esta imagen (VA citada) · **INFERENCE** = deducción consistente con la evidencia · **UNKNOWN** = sólo determinable en vivo.

---

## 0. TL;DR — la causa raíz en tres frases

1. La respuesta de un comando FTM se rutea por el **canal COMMAND (bitmask 1)**, cuyo destino QRTR **(node,port) es UN ÚNICO par global** `0xc8c2d870`(node u32)/`0xc8c2d874`(port u8), leído **en el instante del `sendto`** desde ese global. **FACT** (§2, §3).

2. Ese global lo **sobrescribe el último NEW_SERVER** que el modem procesa (`0xc0d83018/20`), **incondicionalmente**, esté "connected" o no. En el CASO B (no-restart) tu `--cmd-peer` SÍ lo escribe, PERO el modem ya estaba `connected` (`0xc95089a0!=0`) de una sesión previa, y **el peer COMMAND efectivo con el que quedó "conectada" la sesión de diag** puede no ser el tuyo por dos motivos independientes: (a) el `0xc0d5f5a4` que corre tras tu NEW_SERVER es un **no-op cuando ya está connected** (no re-cablea nada), y (b) el drenaje de la respuesta lee el global vivo pero el **descriptor COMMAND (`0xcb93f380`) trae `allow_flow` y estado que se fijaron en la 1ª conexión** y no se re-arman con tu kick. Con `--restart-modem` el modem arranca sin `connected`, corre `0xc0d375e4` (full connect-init) y aprende TU par como el peer COMMAND desde cero. **FACT (mecanismo) + INFERENCE (qué queda mal en B).** (§4, §5).

3. Lo que ves en el CASO B — sólo control QRTR `06 00 ..` con node `0x40xx` por DATA y nada por el cmd-peer — es coherente: la respuesta se drenó por COMMAND hacia un (node,port) que **no es tu socket** (o hacia un port cuyo `allow_flow`/estado quedó del peer viejo), y los `0600` son **QRTR ctrl (DEL_CLIENT/RESUME_TX) del router**, no la respuesta. **FACT (ruteo) + INFERENCE (destino).** (§6).

---

## 1. Confirmación del mux de salida COMMAND vs DATA (FACT, corrige "todo va a DATA")

`diagpkt_rsp_send` procesa la cola de respuestas (head `0xc92e4730`, "actual" en `0xc92e4704`), entry `0xc0d55c40`. El drain real:

```
c0d55c64: r21 = memw(0xc92e4704)             ; rsp_entry actual
c0d55c70: r2  = memb(0xc92e43e0)             ; gate feature-recibido (global)
c0d55c74: if (==0) skip call 0xc0d5f5a4      ; (kick drain si feature ya recibido)
...
c0d55d54: r2 = memw(r21+#0x20)               ; <<< campo de ruteo rsp_entry+0x20
c0d55d58: p0 = cmp.eq(r2,#0x0)               ; p0 = (+0x20 == 0)
c0d55d5c: call 0xc0d5d580                     ; DRAIN(r0=rsp_entry, r1=mask)
c0d55d60: r1 = mux(p0,#0x1,#0x2)             ; <<< mask = (+0x20==0)?1(COMMAND):2(DATA)
```
**FACT** (líneas 32559–32562 de `_dis_b13.txt`). El bitmask de canal destino:
- `rsp_entry+0x20 == 0` → **mask 1 = COMMAND**
- `rsp_entry+0x20 != 0` → **mask 2 = DATA**

`0xc0d5d580` (drain-a-canal) confirma que `r1` es un bitmask:
```
c0d5d630: p0 = tstbit(r16,#0x0); if (p0) jump 0xc0d5d858   ; bit0 => COMMAND
c0d5d634: r23 = 0xcb93f3ac                                  ; descriptor COMMAND (base 0xcb93f380)
c0d5d640: r2  = and(r16,#0x2); if(==0) jump 0xc0d5d904      ; bit1 => DATA
c0d5d5a0: r20 = 0xcb93f608                                  ; descriptor DATA
```
**FACT** (40285–40291, 40249, 40286–40287). Dos descriptores `diag_socket_info` distintos: **COMMAND `0xcb93f380`** y **DATA `0xcb93f608`** (0xb8 bytes cada uno).

### 1.1 Quién fija `rsp_entry+0x20` — un GLOBAL, no un flag del handler FTM (FACT)
En el setup del rsp_entry (`diagbuf`/alloc path, `0xc0d56278`):
```
c0d5626c: r3 = memw(0xc92e477c)             ; <<< "current deferred/stream ctx" (global)
c0d56278: memw(r0+#0x20) = r3 ; p0=cmp.eq(r3,#0)   ; rsp_entry+0x20 = 0xc92e477c
```
**FACT** (32885–32888). => El destino COMMAND-vs-DATA de una respuesta **NO** es una propiedad intrínseca del comando FTM: sale del global `0xc92e477c` vigente cuando se arma el rsp_entry. Para una **respuesta directa/inmediata de comando** (el caso de TECH_ENTER que "corre pero no responde"), `0xc92e477c` está en 0 → **`+0x20==0` → COMMAND (mask 1)**. Sólo las respuestas marcadas como *delayed/stream* (`diagpkt_delay_commit`) llevarían `+0x20!=0` → DATA. **FACT (mecanismo) + INFERENCE (que TECH_ENTER cae en `+0x20==0`)**.

**Conclusión mux:** la respuesta del comando FTM va por **COMMAND (mask 1)**, al descriptor `0xcb93f380`. Por eso **NO** la ves en DATA (inst 2) junto a los F3, y por eso "leer por DATA" (consejo de `handshake_no_restart.md`/`diag_response_routing.md`) era **incorrecto** para la respuesta directa. **FACT.**

---

## 2. El peer COMMAND: un único global node/port, leído en el `sendto` (FACT)

### 2.1 El `sendto` de datos lee node/port del descriptor de mensaje (FACT)
`diagcomm_io_socket_send` (`0xc0d81338`) arma la entrada de ring en el stack y llama al encolador `0xc0d93ed4`:
```
c0d8138c: memw(r29+#0x4)=r18 ; memw(r29+#0x8)=r19     ; campos de la entrada
c0d81390: memw(r29+#0xc)=r17 ; memw(r29+#0x10)=r16    ; r16/r18 = node/port destino
c0d81394: memw(r29+#0x14)=r21; memw(r29+#0x18)=r22
c0d8139c: call 0xc0d93ed4
```
`0xc0d93ed4` copia a la entrada del ring (0x24B) y el `sendto` real `0xc0d95f64` lee:
```
c0d93f08: r8 = memub(r17+#0x1c)   ; node destino
c0d93f0c: r9 = memw (r17+#0x20)   ; port destino
c0d93f38: r2 = memw(r17+#0x14)    ; port_num del servicio (0x1001)
c0d93f4c: call 0xc0d95f64          ; qrtr sendto real
```
**FACT** (76945–76949, 96084–96101). El destino NO viene del source-addr del comando entrante; viene del descriptor de canal / ring.

### 2.2 El (node,port) COMMAND destino = el GLOBAL único `0xc8c2d870/74` (FACT)
Los constructores de la dirección de destino QRTR (`0xc0d83128`, `0xc0d83220`) leen el **mismo par global** cada vez que arman un mensaje de salida:
```
c0d83168: r3 = memw (0xc8c2d870)   ; node
c0d83188: r3 = memub(0xc8c2d874)   ; port
c0d832ac: r4 = memw (0xc8c2d870)   ; node  (segundo builder)
c0d832d0: r3 = memub(0xc8c2d874)   ; port
```
que se depositan como `memb(r0+0x8..0xc)` = {node, port} en la estructura sockaddr QRTR de salida. **FACT** (78856/78864, 78937/78946). => **Hay UN solo (node,port) de destino** para el path COMMAND; no hay un par por-canal independiente que sobreviva a un cambio del global.

### 2.3 Quién escribe el global y bajo qué condición (FACT)
El handler QRTR NEW_SERVER (`0xc0d82fc0`, entry de dispatch `0xc0d830b4`) escribe **incondicionalmente**:
```
c0d82fd8: p0 = cmp.gtu(r18,#0x5); if(p0) jump error   ; valida service-instance <= 5
c0d82fdc/ff/0c/10: r16 = índice de canal (0 / 3 / 4) según instancia y node
c0d83018: memw(0xc8c2d870) = r17    ; <<< NODE del AP (siempre)
c0d83020: memb(0xc8c2d874) = r18    ; <<< PORT (u8) del AP (siempre)
c0d83024: call 0xc0d5f5a4            ; kick/connect  (ver §4: no-op si ya connected)
c0d83028: call 0xc0d65ce8            ; log "diag connected"
```
**FACT** (78772–78776). `r17`=node (arg r0), `r18`=service-instance/port (resuelto vía `0xc098e204`, que hace `port=extractu(addr,12,0)`, `node=lsr(addr,12)` — split QRTR estándar, `_dis_b05.txt` 14466–14467).

> **El global se sobrescribe con cada NEW_SERVER que el modem procese, sea CNTL/CMD/DATA/DCI. NO es por-canal.** El índice `r16` selecciona a qué *tabla de canal* re-cablear, pero el **destino de respuesta COMMAND sale del par único** `0xc8c2d870/74`. **FACT.**

**→ Respuesta a la pregunta 1:** el peer COMMAND al que el modem rutea la respuesta = **el (node,port) que esté en `0xc8c2d870/74` en el momento del `sendto`**, que es **el último NEW_SERVER procesado**. Tu `--cmd-peer` (NEW_SERVER inst 1) SÍ los escribe (§2.3 es incondicional). El problema NO es "quién los escribió", sino que en el path B **el estado de la sesión (connected + allow_flow del descriptor COMMAND) no se re-armó para tu peer** (§4–§5).

---

## 3. El mux COMMAND/DATA para una respuesta FTM concreta (FACT/INFERENCE)

- TECH_ENTER (subsys FTM, SSID 0x17) → handler corre (no 0x14) → arma respuesta con `diagpkt_(subsys_)alloc`/commit → rsp_entry con `+0x20 = 0xc92e477c` (§1.1).
- Para una respuesta **directa** (no delayed), `0xc92e477c == 0` → **`+0x20==0` → COMMAND (mask 1)**. **INFERENCE fuerte** (basada en FACT del mux + que el handler FTM hace commit normal, no delay).
- El drain COMMAND hace `sendto(node=0xc8c2d870, port=0xc8c2d874, port_num=0x1001)`. **FACT.**
- **No** aparece en DATA porque DATA es mask 2, y sólo llega ahí si `+0x20!=0`. Por eso en tu socket DATA sólo ves F3 (que drenan por mask 2 explícitamente, `0xc0d56f74`) y el ctrl `0600` (QRTR router), **no** la respuesta. **FACT.**

**→ Respuesta a la pregunta 2:** la respuesta FTM va por **COMMAND (mask 1)**. Si el peer COMMAND (global/descriptor) no es tu socket, ahí se pierde. NO debería aparecer en DATA (a menos que el handler use delay-commit, que no es el caso). Ver §7 para las 4 opciones de solución.

---

## 4. La diferencia REAL A vs B: `0xc95089a0` "connected" gatea el re-cableo (FACT)

`0xc0d5f5a4` (llamada tras CADA NEW_SERVER, `0xc0d83024`):
```
c0d5f5a4: r2 = memub(0xc95089a0)                    ; <<< flag "diag YA connected"
c0d5f5b0: if (!cmp.gtu(r2,#0)) jump 0xc0d375e4       ; si NO connected -> FULL connect-init
c0d5f5b4: jumpr r31                                   ; <<< si YA connected -> RETURN inmediato
```
**FACT** (42293–42297). `0xc95089a0` se pone a 1 en la primera conexión (`0xc0d37614`, dentro de `0xc0d375e4`). **FACT** (1420).

`0xc0d375e4` (full connect-init, corre **sólo cuando NOT connected**):
```
c0d375e4: call 0xc0d677f8                 ; diagID/init helper
c0d375ec: call 0xc0d81af8                 ; <<< abre/cablea los descriptores de canal
c0d375f4: if (r0==0) jump ...
c0d37614: memb(0xc95089a0) = 1            ; marca connected
```
**FACT** (1408–1420). Es aquí (`0xc0d81af8`) donde los descriptores COMMAND/DATA se **abren y cablean** (io_type, allow_flow inicial=1, node/port destino desde el global recién aprendido, port_num 0x1001).

### 4.1 Qué implica para A vs B
- **CASO A (--restart-modem):** el modem arranca con `0xc95089a0==0`. Publicás CNTL/DATA/(DCI) y el modem, en su boot de diag, ve el primer NEW_SERVER → `0xc0d5f5a4` → **`0xc0d375e4` FULL init** → `0xc0d81af8` cablea los descriptores con TU (node,port). Justo antes del comando mandás NEW_SERVER inst 1 → `0xc8c2d870/74` = TU cmd-peer → la respuesta COMMAND te llega. **FACT (mecanismo) + INFERENCE (por eso funciona)**.

- **CASO B (--kick, sin restart):** `0xc95089a0` **ya vale 1** (de una sesión previa: el ModemManager/diag del sistema, o un boot anterior). Tu NEW_SERVER inst 1:
  - **SÍ** escribe `0xc8c2d870/74` con tu (node,port) (§2.3 incondicional), y
  - llama `0xc0d5f5a4` que, al estar connected, hace **RETURN inmediato** (`0xc0d5f5b4`): **NO** re-corre `0xc0d81af8`, **NO** re-cablea el descriptor COMMAND, **NO** re-abre el canal, **NO** re-arma allow_flow para tu peer.

  Resultado: el `sendto` de la respuesta COMMAND lee el global (que puede ser el tuyo) **pero el descriptor COMMAND `0xcb93f380`** conserva estado de la sesión vieja (allow_flow/`+0x94`/socket handle internos), y/o el envío se hace por un handle de socket que no está entregando a tu bind. La respuesta "corre pero no llega". **FACT (que 0xc0d5f5a4 es no-op) + INFERENCE (qué queda desincronizado en el descriptor)**.

**→ Respuesta a la pregunta 3 (el global que falta setear):** no es que falte *escribir* `0xc8c2d870/74` (tu kick lo escribe). Lo que falta es **forzar el re-cableo del canal COMMAND**, que sólo ocurre en `0xc0d81af8` bajo el gate `0xc95089a0==0`. Con `0xc95089a0!=0` ese re-cableo NO se dispara. Enumeración de globals del routing:

| Global (VA) | Rol | Quién lo setea | Estado en B |
|---|---|---|---|
| `0xc8c2d870` (u32) | **node destino** (COMMAND/todo) | NEW_SERVER `0xc0d83018` (incondicional) | **lo escribe tu kick** (OK) |
| `0xc8c2d874` (u8)  | **port destino** | NEW_SERVER `0xc0d83020` (incondicional) | **lo escribe tu kick** (OK) |
| `0xc95089a0` (u8)  | **"diag connected"** — gatea full-init `0xc0d375e4`/`0xc0d81af8` | 1ª conexión `0xc0d37614` | **ya ==1** → bloquea re-cableo ← **ESTE es el que "falta" (en realidad SOBRA)** |
| `0xc92e43e0` (u8)  | feature-recibido (gate rsp) | FEATURE type 8 `0xc0d66fa8` | tu kick lo setea (OK) |
| `0xc92e4754` bit0  | diagID-recibido (gate rsp) | DIAGID type 0x21 path | tu kick lo setea (OK) |
| `0xc92e477c` (u32) | ctx deferred → `rsp_entry+0x20` (mux COMMAND/DATA) | productor de rsp | ==0 → COMMAND |
| `0xc92e91a0` (u32) | signals internos (RESEND/DRAIN) | `0xc0d696bc` | tu kick lo patea vía `0xc0d68168` |
| `0xcb93f380`+0x84  | allow_flow del descriptor COMMAND | init `0xc0d81af8`; STOP `0xc0d373ac` | **no re-armado por kick** |

**FACT** (todas las VA verificadas). El diferencial es **`0xc95089a0`**: en A vale 0 y deja re-cablear; en B vale 1 y NO. Tu kick no lo toca (ni puede fácil por DIAG). **FACT + INFERENCE.**

---

## 5. Por qué el `--cmd-peer` (NEW_SERVER inst 1) "no se usa" aunque escribe el global (FACT/INFERENCE)

- El global `0xc8c2d870/74` **sí** queda con tu (node,port) tras tu NEW_SERVER (`0xc0d83018/20` es incondicional). **FACT.**
- Pero el `sendto` de la respuesta usa, además del (node,port), el **socket-handle/estado del descriptor COMMAND** (`0xcb93f380`) — el file/endpoint QRTR interno con el que el modem abrió el canal en `0xc0d81af8` en la 1ª conexión. Ese endpoint apunta a la **sesión vieja**. Cambiar sólo el global node/port **no re-crea el endpoint** ni re-valida `allow_flow` para tu bind. **INFERENCE fuerte** (consistente con: `0xc0d5f5a4` no-op cuando connected, `0xc0d81af8` sólo en full-init).
- Además, si el **peer viejo (ModemManager / diag del kernel)** sigue vivo y con su servicio 0x1001 publicado, cualquier NEW_SERVER re-emitido por ese peer (o por el propio kernel al re-anunciar) **vuelve a pisar** `0xc8c2d870/74`. Como es un global único, hay una **carrera**: quien mande el último NEW_SERVER gana. **FACT (global único) + INFERENCE (competencia)**.

**→ Por eso "no somos nosotros" el peer COMMAND efectivo:** o el endpoint del descriptor quedó del peer viejo (no re-cableado por el gate `connected`), o el global fue re-pisado por el peer preexistente después de tu kick. **INFERENCE fuerte.**

---

## 6. El "0600.. node 0x40xx" en DATA (FACT/INFERENCE)

- Formato `06 00 00 00 ...` = **QRTR control message, cmd=0x06**. En el protocolo QRTR del router, `0x06 = QRTR_TYPE_DEL_CLIENT` (y `0x09 = RESUME_TX`). Llega al socket porque el **router QRTR** notifica cambios de topología (un cliente/nodo se fue o hay resume de flujo). **FACT (es ctrl QRTR, no payload diag) + INFERENCE (tipo exacto DEL_CLIENT)**.
- El `node 0x40xx` es un **QRTR node-id** (0x40xx = rango típico de nodos remotos/servicios en este SoC). Apunta al **peer que cambió de estado** — con alta probabilidad el **peer viejo** (ModemManager / el diag preexistente) que se conectó/desconectó, no tu socket. **INFERENCE**.
- **No es la respuesta del comando.** Es ruido de control del router. Que sólo veas esto por DATA (y no la respuesta) es la firma exacta de "la respuesta se fue por COMMAND a otro endpoint". **FACT (que no es diag rsp) + INFERENCE (destino)**.

**→ Respuesta a la pregunta 5:** `0600` = QRTR ctrl DEL_CLIENT/RESUME_TX del router; `0x40xx` = node del peer que cambió (probablemente el peer viejo/ModemManager). No confundir con la respuesta del comando.

---

## 7. SOLUCIÓN ACCIONABLE — hacer que la respuesta vuelva a NUESTRO socket sin restart

El objetivo es que el path COMMAND (mask 1) drene a TU (node,port) con un endpoint válido. Hay cuatro vías; **la (a)+(c) combinadas es la robusta**; la (d) es el fallback elegante si podés tolerar delay-commit.

### (a) Ganar la carrera del global COMMAND: NEW_SERVER inst 1 como ÚLTIMA acción antes del comando — necesario, no suficiente solo
- Publicá el NEW_SERVER inst 1 (CMD) desde el socket por el que vas a **leer**, y mandá el comando **desde ese mismo socket**, con el NEW_SERVER lo más pegado posible al `sendto` del comando, para que `0xc8c2d870/74` = tu (node,port) en el `sendto` de la respuesta.
- Bytes del NEW_SERVER (ya los manda tu `--cmd-peer`; QRTR ctrl, no diag):
  ```
  04 00 00 00  01 10 00 00  01 00 00 00  <node u32 LE>  <port u32 LE>
  | type=4    | svc=0x1001 | inst=1     | node          | port
  ```
  (enviado a `(node, 0xFFFFFFFE)` = QRTR_PORT_CTRL, como ya hacés). **FACT (formato) + tu código**.
- **Por qué no basta solo:** `0xc0d5f5a4` es no-op con `connected==1`, así que el **descriptor COMMAND no se re-cablea**. Necesitás además (c) o forzar el re-cableo. **FACT.**

### (c) Forzar el re-cableo / drain del canal COMMAND por CNTL (el kick que falta)
Tras (a), dispará por **CNTL (inst 0)** los ctrl-msgs que llaman `0xc0d68168` → setea signals RESEND_CTRL/RESEND_DATA/**DRAIN** en `0xc92e91a0` y despierta el diag task, que re-drena la cola de respuestas hacia el (node,port) global vigente:
1. **FEATURE (type 8)** — set `0xc92e43e0`, llama `0xc0d68168`:
   ```
   08 00 00 00  04 00 00 00  1B FE F7 00
   ```
2. **DIAGID (type 0x21) v1** — set `0xc92e4754`bit0, llama `0xc0d68168`:
   ```
   21 00 00 00  <len LE>  01 00 00 00  <diag_id u32>  <name NUL>
   ```
   (reusá el layout root_pd/wlan_pd que ya te funciona post-reboot).
3. **TX_MODE (type 0x11) stream 1 RT** — drain RT:
   ```
   11 00 00 00  06 00 00 00  01 00 00 00  01 01
   ```
- Estos **NO** re-arman `0xc0d81af8` (el gate `connected` lo impide), pero **sí** pateen el DRAIN. Si el descriptor COMMAND aún tiene `allow_flow=1` (arranca en 1, `0xc0d8b0e8; sólo un STOP 0xF4/0xF7 lo baja `0xc0d373ac`) y el endpoint sigue vivo, el DRAIN empuja la respuesta al **global actualizado (tu peer)**. **FACT (mecanismo) + INFERENCE (que el drain usa el global vivo)**.
- **Orden exacto (sin restart):**
  1. (Ya publicado) CNTL(0), DATA(2), DCI(4).
  2. FEATURE + DIAGID + TX_MODE por **CNTL**.
  3. **NEW_SERVER inst 1 desde el socket-lector** (gana el global COMMAND).
  4. `sendto(comando FTM)` **desde ese mismo socket**.
  5. `recvfrom` en **ese socket** ≥10–15 s (subsys FTM es asíncrona). **No cerrarlo.**

### (b) Mandar el comando desde un socket específico — insuficiente por sí solo
El source-addr del comando **no** se usa para rutear la respuesta (§2.1: el destino sale del global/descriptor, no del datagrama entrante). Mandar "desde DATA" (`--from-data`) **no** cambia el destino de la respuesta COMMAND. Sirve sólo si combinás con (a) para que el bind del socket sea el peer COMMAND global. **FACT.**

### (d) Leer por DATA forzando `rsp_entry+0x20 != 0` — elegante, requiere delay-commit
La única forma de que la respuesta salga por **DATA (mask 2)** es que `0xc92e477c != 0` cuando se arma el rsp_entry (→ `+0x20!=0`). Eso ocurre si el handler usa **delayed/stream commit** (`diagpkt_delay_commit`). Para TECH_ENTER/FTM en este build, **si** el handler admite respuesta diferida, la verías por DATA junto a los F3 (que ya escuchás). **Determinar si el handler 0x17 usa delay-commit es UNKNOWN estático** (hay que ubicar el commit exacto del subsys 0x17). Si lo usa, esta es la vía sin tocar el peer COMMAND. **INFERENCE + UNKNOWN.**

### Recomendación operativa
1. **Preferí (a)+(c)** con lectura por el socket del cmd-peer, timeout largo, socket persistente.
2. Si tras (a)+(c) sigue sin llegar: el bloqueo remanente es **el endpoint del descriptor COMMAND anclado a la sesión vieja** (gate `0xc95089a0`) o un **peer competidor (ModemManager/diag del kernel) re-pisando el global**. Mitigaciones en vivo:
   - **Silenciar/parar el peer preexistente** (ModemManager / `/dev/diag` del kernel) para que no re-emita NEW_SERVER 0x1001 y no consuma la respuesta COMMAND. **INFERENCE**.
   - O, si hay `/dev/diag` del kernel activo, **la respuesta puede estarle llegando a él** (es el peer COMMAND estable del sistema): leerla por `/dev/diag` en vez de por QRTR crudo. **INFERENCE**.
3. Captura para cerrar el IQ (en vivo): dump QRTR del AP tras el comando; anotá el **port destino del `sendto`** del modem y compará con: (i) tu cmd-peer, (ii) tu DATA, (iii) el peer viejo. Eso decide entre (a)+(c) vs "matar el peer viejo" vs "leer por /dev/diag". **UNKNOWN estático (requiere vivo).**

---

## 8. Respuestas directas a las 5 preguntas

1. **Peer COMMAND en no-restart:** el (node,port) en `0xc8c2d870`/`0xc8c2d874`, leído en el `sendto` (§2). Tu `--cmd-peer` **sí** los escribe (`0xc0d83018/20` es incondicional). **No sos vos** el peer efectivo porque: (i) `0xc0d5f5a4` es **no-op con `0xc95089a0!=0`** → el **descriptor COMMAND no se re-cablea** (endpoint/allow_flow de la sesión vieja), y/o (ii) un **peer preexistente re-pisa el global único**. **FACT + INFERENCE.**

2. **Mux COMMAND/DATA:** decidido por `rsp_entry+0x20` (`c0d55d54..d60`), que se copia del global `0xc92e477c` (`c0d56278`). Respuesta FTM directa → `+0x20==0` → **COMMAND (mask 1)**, descriptor `0xcb93f380`. **No** va por DATA salvo delay-commit. Por eso en DATA sólo ves F3 + ctrl `0600`. **FACT.**

3. **Global que falta:** no falta *escribir* node/port (tu kick los escribe). Lo que bloquea es **`0xc95089a0` (VA 0xc95089a0) == 1** ("connected"), que impide el re-cableo del canal (`0xc0d81af8`, gateado en `0xc0d5f5b0`). Con restart vale 0 y re-cablea a tu peer; sin restart vale 1 y no. **FACT.**

4. **Solución byte-exacta:** ver §7. Núcleo: **(a)** NEW_SERVER inst 1 (`04 00 00 00 01 10 00 00 01 00 00 00 <node> <port>`) desde el socket-lector, **como última acción antes del comando**; **(c)** por CNTL: FEATURE `08 00 00 00 04 00 00 00 1B FE F7 00`, DIAGID `21 00 00 00 <len> 01 00 00 00 <id> <name\0>`, TX_MODE `11 00 00 00 06 00 00 00 01 00 00 00 01 01` → patean DRAIN (`0xc0d68168`→`0xc92e91a0`); luego `sendto(FTM)` y `recvfrom` ≥10 s en el MISMO socket. Fallback **(d)**: sólo si el handler 0x17 usa delay-commit (UNKNOWN), leer por DATA. Mitigación: matar el peer preexistente / leer por `/dev/diag`.

5. **`0600.. node 0x40xx`:** QRTR **ctrl** cmd=0x06 (**DEL_CLIENT**/RESUME_TX) del router; `0x40xx` = node del peer que cambió (probablemente el **peer viejo/ModemManager**). No es la respuesta. **FACT (ctrl QRTR) + INFERENCE (tipo/peer).**

---

## 9. FACT / INFERENCE / UNKNOWN (cierre con VAs)

### FACT
- Mux respuesta: `diagpkt_rsp_send` `0xc0d55c40`; `r2=memw(r21+0x20)`; `r1=mux((+0x20==0),1,2)` en `0xc0d55d54–0xc0d55d60` (1=COMMAND, 2=DATA).
- Drain-a-canal `0xc0d5d580`: bit0=COMMAND desc `0xcb93f380`(`0xcb93f3ac`), bit1=DATA desc `0xcb93f608`; `0xc0d5d630`/`0xc0d5d640`.
- `rsp_entry+0x20` = global `0xc92e477c` en `0xc0d56278` (`r3=memw(0xc92e477c)`).
- `sendto` datos: `0xc0d93ed4`→`0xc0d95f64`; node/port de `ring+0x1c/0x20`, port_num `+0x14`=0x1001; alimentados por `diagcomm_io_socket_send` `0xc0d81338` (`c0d8138c..9c`).
- Destino COMMAND = global único `0xc8c2d870`(node)/`0xc8c2d874`(port); builders `0xc0d83128`/`0xc0d83220` (`c0d83168/88`, `c0d832ac/d0`).
- NEW_SERVER handler `0xc0d82fc0` (dispatch `0xc0d830b4`): escribe global **incondicional** `0xc0d83018/20`; valida inst<=5; índice canal `r16`∈{0,3,4}; split addr por `0xc098e204` (`_dis_b05.txt` 14466: `port=extractu(a,12,0)`, `node=lsr(a,12)`).
- `0xc0d5f5a4`: `if(0xc95089a0==0) jump 0xc0d375e4 (full init); else return` (`c0d5f5a4..b4`). `0xc95089a0` se pone 1 en `0xc0d37614`.
- Full connect-init `0xc0d375e4` → `0xc0d81af8` (open/cablea descriptores) → set connected.
- Gates rsp: feature `0xc92e43e0` (`0xc0d66fa8`), diagID `0xc92e4754`bit0; kick por CNTL: FEATURE `0xc0d66f34`, DIAGID `0xc0d67484`, TX_MODE `0xc0d6711c`, todos → `0xc0d68168` → `0xc0d696bc` (`0xc92e91a0` |= sig).
- allow_flow del descriptor arranca 1 (`0xc0d8b0e8`), baja sólo con STOP 0xF4/0xF7 (`0xc0d373ac`).
- QRTR ctrl parser `0xc0d675xx` (dentro de `0xc0d67xxx`): distingue tipos por campos del pkt; NEW_SERVER-path → `call 0xc0d830b4` (`0xc0d676e4`).

### INFERENCE
- TECH_ENTER/FTM directa → `0xc92e477c==0` → `+0x20==0` → COMMAND (mask 1).
- El fallo B se debe a que `0xc0d5f5a4` no re-cablea (connected==1): el **descriptor/endpoint COMMAND queda anclado a la sesión vieja**, y/o un peer preexistente re-pisa el global único. Restart evita ambos (connected==0 → full init a tu peer).
- `0600 node 0x40xx` = QRTR DEL_CLIENT/RESUME_TX del router para el peer viejo.

### UNKNOWN (sólo en vivo)
- Valor exacto de `0xc8c2d870/74` en el instante del drain (dump QRTR / trazar el `sendto`).
- Si el handler subsys 0x17 usa `diagpkt_delay_commit` (habilitaría la vía (d) por DATA).
- Si hay un peer preexistente (ModemManager / `/dev/diag`) compitiendo por el servicio 0x1001 y consumiendo la respuesta COMMAND.
- Si `allow_flow` del descriptor COMMAND quedó en 1 o fue bajado por un STOP previo en la sesión vieja.
- Si existe un camino DIAG (no restart) para poner `0xc95089a0=0` y forzar `0xc0d81af8` sin reboot — no hallado estáticamente; el DRAIN por `0xc0d68168` es lo más cercano.
