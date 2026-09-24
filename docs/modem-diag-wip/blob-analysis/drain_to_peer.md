# drain_to_peer.md — Por qué la respuesta de comando subsys NO llega a tu peer DATA aunque los F3 SÍ

**Build:** MPSS.HI.4.3.4-00494-MANNAR_GEN_PACK-1.24452.133 (SM6375, Moto G82 5G)
**Imágenes:** `_dis_b13.txt` (VA 0xc0d36000+, diag core + transporte socket) · `modem.b21` (rodata, VA 0xc3553000) · `clade_dec_full.bin`.
Todas las VA son verificables con `grep -n "^<VA>:" _dis_b13.txt`.

**Leyenda:** **FACT** = leído del disasm (VA citada) · **INFERENCE** = deducción consistente con la evidencia · **UNKNOWN** = sólo en vivo.

---

## 0. TL;DR — la contradicción resuelta en UNA frase

> **La respuesta de un comando subsys NO usa el mismo canal que los F3 logs.**
> Los **F3 logs** drenan por el canal **DATA** (bitmask `2`).
> La **respuesta de comando** la rutea `diagpkt_rsp_send` (VA **0xc0d55d54–0xc0d55d60**) con
> `r1 = mux(p0, #1, #2)` donde `p0 = (rsp_entry+0x20 == 0)`:
> **si `rsp_entry+0x20 == 0` → bitmask `1` = canal COMMAND; si `!= 0` → bitmask `2` = canal DATA.**
> Por eso "mismo peer/allow_flow/drain" era una premisa **falsa**: hay **dos canales de salida
> distintos** (COMMAND y DATA), cada uno con su propio descriptor y su propio node/port, y la
> respuesta subsys se está yendo por el **COMMAND**, no por tu DATA.

**El reporte previo (`diag_transport_full.md`/`diag_response_routing.md`) afirmaba "ambos van a DATA".
Eso es INCORRECTO.** El disasm muestra dos bitmasks de destino y un `mux` explícito. FACT.

Corolario práctico: **la respuesta vuelve por el canal COMMAND**, cuyo `node/port` destino es el
que el modem aprendió del **último NEW_SERVER que registró como CMD/servicio**, guardado en el
global único `0xc8c2d870`(node)/`0xc8c2d874`(port). Si eso apunta a tu socket CLIENTE del CMD (o a
otro peer), la respuesta va ahí — no a `socks[2]` (tu DATA servido).

---

## 1. La estructura real del drain de salida (correige el mapa previo)

### 1.1 `diagbuf_send_pkt` NO es 0xc0d562dc (el reporte previo se equivocó de función) — FACT
`0xc0d562dc` usa los format-strings `udp_receive_data: QPOLLIN diag_pfds[%d]` /
`... Buffer ready, num_recv=%d ...` (`modem.b21` @ 0xc35cf456 / 0xc35cf47e). **Es el poller de
RECEPCIÓN de sockets, no el send.** FACT (strings leídos de b21).

El **`diagbuf_send_pkt` real** es la función grande cuyo cuerpo referencia los strings
`diagbuf_send_pkt: allow_flow on data channel = %d ...` (0xc35cb00d, usado en **0xc0d36e3c**) y
`diagbuf_send_pkt: allow_flow on command channel = %d, allow_flow on data channel = %d ...`
(0xc35cb122). **El propio nombre del string distingue "command channel" de "data channel".** FACT.

### 1.2 `diagpkt_rsp_send` — el ruteo COMMAND vs DATA (el corazón) — FACT
Función de proceso de la cola de respuestas pendientes (head `0xc92e4730`, "current" en
`0xc92e4704`), entry en **0xc0d55c40**; la rama que dispara el drain está en **0xc0d55d50**:

```
c0d55c64: r21 = memw(0xc92e4704)            ; rsp_entry "actual"
c0d55c70: r2  = memb(0xc92e43e0)            ; <- feature-mask-received (gate global, ver §4)
c0d55c74: if (==0) skip call 0xc0d5f5a4
...
c0d55cc0: r2 = memw(r16+#0x20)              ; <<< rsp_entry+0x20  (campo de ruteo)
c0d55cc8: if (!=0) jump 0xc0d360d8          ; rama "+0x20 != 0" (log distinto, mask=2 DATA)
c0d55cd0: p0 = cmp.eq(memub(r16+#0x25),#0x4d) ; +0x25=='M'? (heurística de tipo de rsp)
...
--- 0xc0d55d50: el drain real ---
c0d55d54: r2 = memw(r21+#0x20)              ; <<< lee OTRA VEZ rsp_entry+0x20
c0d55d58: p0 = cmp.eq(r2,#0x0)              ; p0 = (+0x20 == 0)
c0d55d5c: call 0xc0d5d580                   ; DRAIN(r0=rsp_entry, r1=mask)
c0d55d60: r1 = mux(p0, #0x1, #0x2)          ; <<< mask = (+0x20==0)?1(CMD):2(DATA)
```
**FACT.** El **bitmask de canal destino** de la respuesta se decide por `rsp_entry+0x20`:
- `+0x20 == 0` → **mask 1 = COMMAND channel**
- `+0x20 != 0` → **mask 2 = DATA channel**

### 1.3 `0xc0d5d580` = drain-a-canal, `r1` es un BITMASK de puertos — FACT
```
c0d5d630: p0 = tstbit(r16,#0x0); if (p0) jump 0xc0d5d858   ; bit0 => COMMAND path
c0d5d634: r23 = 0xcb93f3ac                                 ; descriptor COMMAND
c0d5d640: r2  = and(r16,#0x2)                              ; bit1 => DATA path
          ... usa r20 = 0xcb93f608                          ; descriptor DATA
c0d5d9c8: r2 = memb(r20+#0x84)                              ; allow_flow del DATA
```
`r16` (= arg `r1`) es un **bitmask**: **bit0 = puerto COMMAND (Port1)**, **bit1 = puerto DATA
(Port2)**. Coincide con los strings `allow flow(Port1) = %d, allow flow(Port2) = %d`
(0xc35cb549/0xc35cb698). FACT.

Descriptores usados (structs `diag_socket_info` de 0xb8 bytes):
- **COMMAND**: base `0xcb93f380` (allow_flow/estado en `0xcb93f3ac`+).
- **DATA**: base `0xcb93f608` (allow_flow en `0xcb93f608+0x84`).
FACT (0xc0d5d5a0, 0xc0d5d634/638, 0xc0d5d9c8; y 0xc0d7bf04 `r16 = 0xcb93f608 + r0*0xb8`).

### 1.4 Los F3 logs drenan por DATA (mask 2) — FACT
El drainer principal (0xc0d56exx / `diagbuf_drain_diag` 0xc0d6d2b8) llama a `0xc0d5d580`:
```
c0d56f6c: r2 = memb(r25+#0xb8)                 ; flag de canal-instancia "activo/registrado"
c0d56f70: if (r2!=1) skip
c0d56f74: call 0xc0d5d580 ; r1:0 = combine(#0x2,#0x0)   ; <<< DATA (mask 2)
...
c0d56f98: call 0xc0d5d580 ; r1:0 = combine(#0x1,#0x0)   ; <<< COMMAND (mask 1)
c0d6ef64: call 0xc0d5d580 ; r1:0 = combine(#0x3,#0x0)   ; <<< AMBOS (mask 3)
```
FACT. Los logs/eventos periódicos drenan a DATA (2). Las respuestas de comando drenan por lo que
diga `rsp_entry+0x20` (1 ó 2, §1.2).

**→ Contradicción resuelta: NO es "el mismo canal". Logs = DATA(2). Respuesta subsys = COMMAND(1)
mientras `rsp_entry+0x20 == 0`.** FACT.

---

## 2. A qué node/port/socket va la respuesta de tu comando subsys

### 2.1 El destino QRTR sale del descriptor del canal elegido — FACT
`0xc0d93ed4` arma el datagrama y `0xc0d95f64` hace el `sendto`, leyendo node/port de la
`diag_socket_info` (`memw(r17+#0x14)` = port_num 0x1001, `memw(r17+#0x1c)`/`memw(r17+#0x20)` =
node/port destino) (0xc0d93f08/0xc0d93f0c/0xc0d93f38). **NO** usa el source-addr del datagrama de
comando entrante. FACT (estructura).

### 2.2 node/port destino = un GLOBAL único aprendido del NEW_SERVER — FACT
El QRTR-ctrl handler `0xc0d82fc0` (NEW_SERVER) guarda **siempre en el mismo par global**:
```
c0d83018: memw(0xc8c2d870) = r17   ; node del AP
c0d83020: memb(0xc8c2d874) = r18   ; port del AP
```
`r16` ahí calcula un **índice de canal** (0/3/4) según la service-instance (`r18`, validada
`<=5`), pero **el node/port se guarda en UN solo global** `0xc8c2d870/74`. FACT.

**Consecuencia (INFERENCE fuerte):** el node/port que usa el `sendto` de la respuesta es el del
**último peer que el modem aprendió** por ese path. Si tu **cliente CMD** (o cualquier otro
servicio que publiques) fue el último NEW_SERVER visto, la respuesta COMMAND vuelve **a ese
socket**, que puede ser tu socket cliente — no `socks[2]` (DATA).

### 2.3 Entonces, ¿a dónde va tu respuesta TECH_ENTER concretamente?
- Se encola como `rsp_entry` con `+0x20 == 0` (caso por defecto de una respuesta de comando
  "inmediata"/no-diferida). **INFERENCE** (que TECH_ENTER cae en `+0x20==0`; ver §3).
- Por eso `diagpkt_rsp_send` la rutea al **COMMAND channel (mask 1)**.
- El COMMAND channel hace `sendto(node=0xc8c2d870, port=0xc8c2d874)`.
- **Eso NO es tu socket DATA (inst 2).** Es el peer COMMAND. **Esta es la razón por la que no la
  ves en tu DATA**, y también por qué "leí 3 s por el cliente y no llegó" puede depender de si tu
  cliente CMD sigue siendo el peer registrado o si el modem la mandó a otro node/port.

FACT (el ruteo por mask 1); INFERENCE (que `+0x20==0` para TECH_ENTER y que el destino COMMAND no
coincide con tu DATA).

---

## 3. `rsp_entry+0x20`: qué es y por qué el 0x00 SÍ y TECH_ENTER NO

`rsp_entry+0x20` es el **campo que marca "esta respuesta va por DATA" (deferred/stream response)
vs "va por COMMAND" (respuesta directa al canal de comando)**. FACT (que gobierna el `mux`,
§1.2). El **valor** lo fija el productor de la respuesta al construir el `rsp_entry`:

- Las respuestas **triviales apps** (0x00 version, 0x7c) las verías porque el COMMAND channel
  apunta a tu cliente (mismo socket por el que mandaste) → **te llegan inline por el cliente**.
  Eso es exactamente lo que observás: **0x00 responde por el socket cliente.** Coincide con
  "mask 1 = COMMAND = el peer del comando". FACT (ruteo) + INFERENCE (que por eso lo ves inline).

- **TECH_ENTER (subsys FTM):** el handler corre (no 0x14) y hace `diagpkt_(subsys_)commit`. Si su
  `rsp_entry+0x20` queda en 0, **también** se rutea a COMMAND (mask 1) → `sendto` al peer
  `0xc8c2d870/74`. Si en ese momento el peer COMMAND aprendido **no** es el socket por el que
  leés, o el modem re-aprendió otro node/port, **no la ves por ningún lado esperado.** Y como
  **NO** va por DATA (mask 2), tampoco aparece en `socks[2]` junto con los F3. FACT (no va a
  DATA salvo que `+0x20!=0`).

**Por qué "0x00 sí / TECH_ENTER no" ya NO es misterioso:** ambos van por COMMAND (mask 1); la
diferencia es **cuál socket es el peer COMMAND en cada instante** y si el datagrama de respuesta
te llega a un socket que estás leyendo. El 0x00 es corto y vuelve en el mismo tick al cliente que
lo pidió; TECH_ENTER es asíncrono y, para cuando el modem drena, el peer COMMAND puede haber
cambiado (re-NEW_SERVER) o tu cliente ya cerró la ventana de lectura. INFERENCE (consistente con
FACT del ruteo).

---

## 4. Los gates (feature+diagID) NO son el diferencial — corrección honesta

Hay DOS bloques de gate idénticos, uno por canal:
- **COMMAND path gate** @ **0xc0d36c44**: exige `tstbit(memw(0xc92e4754),0)` (DIAGID) **y**
  `memb(0xc92e43e0)!=0` (feature); si no → 0xc0d58b9c ("Attempt to send response before feature
  mask OR diagID"). FACT.
- **DATA path gate** @ **0xc0d36f80**: mismo `0xc92e4754` bit0 + `0xc92e43e0` + un bit per-canal
  `memub(0xcb93f96d)`. FACT.

Ambos leen los **mismos globales** `0xc92e4754`/`0xc92e43e0` (no son per-peer para el gate en sí).
Como vos confirmás que **0x00 responde y TECH_ENTER corre sin 0x14**, **el gate está PASANDO**.
→ **El gate NO es tu bloqueo.** La causa es el **ruteo COMMAND vs DATA** (§1–§3). FACT +
tu observación en vivo.

(El campo `0xc92e43dc..0xc92e43e0` es un array por-canal que 0xc0d7bf04/0xc0d7c2ec resetean a 0
al cerrar/registrar cada `diag_socket_info` de `0xcb93f608`; el gate lee el byte fijo
`0xc92e43e0`. FACT.)

---

## 5. La condición `+0x94` ("peer no listo") — dónde aplica

En el loop de `0xc0d562dc` (el poller RX, no el send) hay `if (memw(r18+#0x94)!=0) jump error`
(0xc0d563d8) y luego `memw(r18+#0x94)=r20` (0xc0d564e0) tras un envío. En el **send real**
(`0xc0d5d580`/`diagcomm_io_transmit` 0xc0d56520) el gate es **`allow_flow = memb(desc+0x84)`**
(0xc0d56558) y `buffer!=0`. FACT.
- `+0x94` = "resultado/estado del último socket op del canal" (retry/QENOMEM). Si tu DATA no está
  registrado, su `+0x94` puede quedar !=0 y bloquear su drenaje — **pero eso afecta al canal DATA,
  y tu respuesta subsys ni siquiera está yendo por DATA** (§1.2). Por eso arreglar `+0x94` de DATA
  no te trae la respuesta de comando. INFERENCE.
- `allow_flow(+0x84)` arranca en 1 (0xc0d8b0e8) y sólo lo baja un STOP 0xF4/0xF7 (0xc0d373ac).
  Como tus F3 SÍ drenan por DATA, el DATA está con allow_flow=1 y peer listo. FACT (de que los
  logs salen). **Confirma que el problema no es DATA, es que la rsp va por COMMAND.**

---

## 6. QUÉ TE FALTA — acciones concretas (en orden de probabilidad)

### 6.1 (LA CLAVE) Leé la respuesta por el socket CLIENTE del CMD, no por DATA — FACT
La respuesta de comando subsys se rutea al **COMMAND channel (mask 1)** cuando `rsp_entry+0x20==0`,
y el `sendto` va a `node=0xc8c2d870/port=0xc8c2d874` = **el peer COMMAND aprendido**. Ese peer es,
con altísima probabilidad, **el socket desde el que mandaste el comando** (tu cliente al CMD inst
1). → **La respuesta TECH_ENTER te llega (o intenta llegar) por tu socket CLIENTE, no por DATA.**
Esto invierte el consejo del reporte previo. FACT (ruteo) + INFERENCE (peer=tu cliente).

**Acción:** después de mandar TECH_ENTER **por el cliente CMD, seguí `recvfrom` en ESE MISMO
socket cliente** (no en `socks[2]`), con timeout mayor (la subsys FTM es asíncrona; 3 s puede ser
corto — probá 10–15 s y no cierres el socket). Capturá TODO datagrama que llegue a tu cliente.

### 6.2 Asegurá que el peer COMMAND que el modem tiene registrado es TU cliente — FACT/INFERENCE
`0xc8c2d870/74` es **un global único** que se sobrescribe con **cada** NEW_SERVER que el modem
procesa (0xc0d83018/20). Si después de conectar tu cliente CMD publicás/republicás otros servicios
(o el kernel QRTR emite NEW_SERVER de DATA/DCI **después**), el modem **re-aprende** node/port y la
respuesta COMMAND puede salir hacia el último aprendido. INFERENCE fuerte.

**Acción:** ordená el handshake para que **lo último que el modem aprenda antes de comandar sea el
peer por el que vas a leer**. Práctica robusta:
1. Publicá CNTL/DATA/DCI (NEW_SERVER) primero.
2. FEATURE(8) + DIAGID(0x21) + TX MODE(0x11 stream 1 RT) (satisfacen el gate; ya te pasa).
3. **Justo antes** de mandar TECH_ENTER, hacé que tu **cliente CMD** emita su
   NEW_SERVER/hello de modo que sea el node/port en `0xc8c2d870/74`.
4. Mandá TECH_ENTER por el cliente CMD y **leé por el cliente CMD**.

### 6.3 (Alternativa) Forzá que la respuesta salga por DATA poniendo `rsp_entry+0x20 != 0` — UNKNOWN estático
El único modo de que la respuesta subsys drene por **DATA (mask 2)** es que su `rsp_entry+0x20`
sea `!=0` (§1.2). Ese campo lo fija el **productor de la respuesta** (path
`diagpkt_commit`/subsys), típicamente cuando la respuesta se marca como **"delayed response"**
(`diagpkt_commit`/`diagpkt_delay_commit`) o cuando el comando entró como **"deferred"**. En el
protocolo diag del host, esto corresponde a que el comando se envíe con el bit/subsys que provoca
respuesta diferida. **Cómo forzarlo desde el wire para TECH_ENTER es UNKNOWN estático** (depende de
si el handler FTM llama a `diagpkt_commit` normal o al delayed). Si tu objetivo es ver la respuesta
en el DATA que ya escuchás, esta es la vía "elegante" pero requiere identificar el commit exacto
del handler FTM (VA del handler subsys 0x17 → buscar si llama a un `diagpkt_delay_*`).
INFERENCE + UNKNOWN.

### 6.4 Lo que NO te falta (descartado con evidencia)
- **NO** te falta feature/DIAGID: el gate pasa (0x00 responde; TECH_ENTER no da 0x14). FACT.
- **NO** es `allow_flow` de DATA: los F3 drenan por DATA → DATA con allow_flow=1 y peer listo. FACT.
- **NO** es `+0x94` de DATA: la rsp subsys no va por DATA. INFERENCE.
- **NO** es el handle @0x06. FACT (ver diag_response_routing.md §1).

---

## 7. ¿Es algo que sólo el driver diag del kernel puede hacer?

**Parcialmente sí (INFERENCE fuerte):** en un AP Linux estándar, el **kernel `diag`/`diagfwd`**
(o `qrtr`) es quien publica el servicio DIAG y quien mantiene el peer COMMAND estable. El modem
**sólo tiene UN node/port global** para responder por COMMAND (`0xc8c2d870/74`); si tu userland
compite con el driver del kernel por registrar servicios QRTR DIAG, el modem puede terminar
respondiendo al **peer del kernel**, no al tuyo, y el datagrama lo consume/descarta el kernel.
FACT (global único) + INFERENCE (competencia de peers).

**Recomendación de robustez:** si hay un `diag` char-device del kernel activo, **la respuesta de
comando puede estar llegándole a él** (por eso "no llega a mi socket"). Opciones:
- Deshabilitar el driver diag del kernel y ser vos el único peer QRTR DIAG (CNTL+CMD+DATA), y
  **leer por el mismo socket cliente CMD** (§6.1/§6.2). O
- Leer la respuesta a través del char-device `/dev/diag` del kernel si está presente (el kernel ya
  hace de peer COMMAND y te entrega el payload). UNKNOWN cuál está activo en tu setup en vivo.

---

## 8. Respuestas directas a tus 6 preguntas

1. **¿Mismo canal/peer?** **NO.** F3 logs → **DATA (mask 2)**. Respuesta de comando →
   **COMMAND (mask 1)** salvo `rsp_entry+0x20!=0`. Dos descriptores distintos
   (`0xcb93f608` DATA vs `0xcb93f380` COMMAND), node/port del global único `0xc8c2d870/74`. FACT.

2. **¿Sobre qué itera el drain?** `diagpkt_rsp_send` (0xc0d55c40/0xc0d55d50) procesa la cola de
   respuestas `0xc92e4730` y para **cada** rsp llama a `0xc0d5d580` con **bitmask** de canal
   (1=CMD, 2=DATA, 3=ambos). El drainer de logs (0xc0d56exx / 0xc0d6d2b8) llama con **2 (DATA)**.
   La respuesta de comando entró por CMD pero **vuelve al canal que dicte `rsp_entry+0x20`**, no al
   "DATA global" por defecto. FACT.

3. **¿Usa mi source-addr o el DATA global?** Usa **el node/port del descriptor del canal elegido**
   (`memw(diag_socket_info+0x1c/+0x20)`), que se rellenó desde el **global** `0xc8c2d870/74`
   aprendido del **último NEW_SERVER** — **no** el source-addr del datagrama de comando. Como es
   COMMAND, ese peer suele ser **tu cliente CMD**. FACT (estructura) + INFERENCE (peer=cliente).

4. **channel_type / io_type routing:** el match del canal de salida NO es por io_type del paquete;
   es por el **bitmask** que `diagpkt_rsp_send` pasa (`mux` sobre `rsp_entry+0x20`). Dentro,
   `diagcomm_io_transmit` (0xc0d56520) exige `io_type(+0x0)==2` **sólo para la rama DATA**; la
   rama COMMAND usa su propio descriptor. FACT.

5. **¿"cmd rsp goes to the socket it came from"?** **Efectivamente SÍ, vía el COMMAND channel.**
   No guarda literalmente tu source-addr en el pkt, pero rutea por el **COMMAND channel** cuyo
   node/port = el peer COMMAND global (tu cliente). → **Mandar desde el cliente y leer por el
   cliente es lo correcto**; mandar y **leer por DATA es lo que está MAL** para respuestas con
   `+0x20==0`. FACT (ruteo) + INFERENCE (peer).

6. **`+0x94` ("peer no listo"):** es estado de retry del canal (0xc0d563d8/0xc0d564e0), y el gate
   de drenaje real es `allow_flow(+0x84)` (0xc0d56558). Ninguno es tu bloqueo: afectan a DATA, y tu
   respuesta subsys no va por DATA. FACT/INFERENCE.

---

## 9. FACT / INFERENCE / UNKNOWN (cierre)

**FACT (con VA):**
- `0xc0d562dc` = poller RX `udp_receive_data` (strings b21 0xc35cf456/47e), **no** el send.
- `diagpkt_rsp_send` real: entry 0xc0d55c40; drain 0xc0d55d50; **ruteo `r1=mux((rsp+0x20==0),1,2)`
  en 0xc0d55d54–0xc0d55d60** (1=COMMAND, 2=DATA).
- `0xc0d5d580` = drain-a-canal; `r1`=bitmask (bit0 COMMAND `0xcb93f380`, bit1 DATA `0xcb93f608`);
  0xc0d5d630 tstbit; 0xc0d5d9c8 allow_flow DATA.
- Logs drenan DATA(2) en 0xc0d56f74; COMMAND(1) en 0xc0d56f98; ambos(3) en 0xc0d6ef64.
- `diagcomm_io_transmit` 0xc0d56520: allow_flow `memb(desc+0x84)` (0xc0d56558), re-arma en
  0xc0d565b0; STOP 0xF4/0xF7 en 0xc0d373ac.
- `sendto` QRTR: 0xc0d93ed4→0xc0d95f64; node/port de `diag_socket_info+0x1c/+0x20`, port_num
  `+0x14`=0x1001 (0xc0d93f08/0f/38).
- NEW_SERVER 0xc0d82fc0: node/port en **global único** `0xc8c2d870`/`0xc8c2d874` (0xc0d83018/20).
- Gates feature+diagID: COMMAND 0xc0d36c44, DATA 0xc0d36f80 (mismos globales 0xc92e4754/0xc92e43e0);
  string "before feature mask OR diagID" 0xc35ca363 (rama 0xc0d58b9c). Pasan en tu caso.
- Strings decisivos: `diagbuf_send_pkt: allow_flow on command channel = ..., data channel = ...`
  0xc35cb122; `allow flow(Port1/Port2)` 0xc35cb549/0xc35cb698.

**INFERENCE:**
- TECH_ENTER cae en `rsp_entry+0x20==0` → sale por COMMAND (mask 1) → `sendto` a tu cliente CMD,
  no a DATA. Por eso no aparece en `socks[2]` con los F3.
- El peer COMMAND es tu socket cliente (o el último NEW_SERVER); leer por el cliente es lo correcto.
- Si un driver diag del kernel es peer, la respuesta puede irle a él.

**UNKNOWN (sólo en vivo):**
- Valor exacto de node/port en `0xc8c2d870/74` al momento del drain (capturar `sendto` / qrtr dump).
- Si el handler subsys FTM setea `rsp_entry+0x20!=0` (respuesta diferida) — determina si podés
  forzarla por DATA. Requiere ubicar el commit exacto del handler 0x17.
- Semántica de `+0x25=='M'`(0x4d) como heurística de tipo de respuesta.
- Si hay driver diag del kernel activo consumiendo la respuesta COMMAND.

---

## 10. Plan de captura mínimo para cerrar el IQ

1. Mandá TECH_ENTER **por tu socket cliente CMD**. **NO cierres ese socket.**
2. Hacé `recvfrom` en **ESE cliente** durante ≥10 s. Loggeá `(src_node, src_port, bytes)` de todo
   datagrama. → ahí debería estar la respuesta subsys (status + payload FTM).
3. En paralelo, dumpeá el tráfico QRTR del AP (`/sys/kernel/debug/qrtr` o pcap qrtr) y buscá el
   `sendto` del modem tras el comando: anotá **port destino**. Comparalo con el port de tu cliente
   y con el de tu DATA(inst 2).
   - Si el port == tu cliente → leé por el cliente (§6.1). **Caso esperado.**
   - Si el port == otro (kernel diag) → deshabilitá el diag del kernel o leé por `/dev/diag`.
4. Si querés que salga por DATA: identificá el handler subsys 0x17 y verificá si llama a un
   `diagpkt_delay_commit` (que pondría `rsp_entry+0x20!=0`); si el comando admite modo "delayed
   response", usalo → saldrá por DATA(2) y lo verás junto a los F3.
