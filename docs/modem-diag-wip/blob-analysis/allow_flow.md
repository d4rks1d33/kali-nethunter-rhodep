# `allow_flow` en el modem SM6375 (Moto G82 5G) — qué lo habilita y por qué la respuesta subsys FTM (TECH_ENTER) no drena al QRTR

Build: **MPSS.HI.4.3.4-00494-MANNAR_GEN_PACK-1.24452.133**
Imagen de código sin comprimir: segmentos crudos `modem.bNN`.
- Código DIAG: **b13** (VA 0xc0d36000, off 0), desasm completo en `/tmp/modemre/_dis_b13.txt`.
- rodata (`__func__`, format-strings, jumptables): **b21** (VA 0xc3553000).
Todas las direcciones de abajo son VA reales y verificables con `grep -n "^<VA>:" _dis_b13.txt`.

Leyenda: **FACT** = leído directo del disasm de esta imagen · **INFERENCE** = deducción sobre el modelo DIAG estándar consistente con la evidencia · **UNKNOWN** = sólo determinable en vivo.

> **TL;DR / corrección de la hipótesis de partida:**
> `allow_flow` **NO** es un global que un control-msg pone en 1. Es un **byte por-canal**:
> **`allow_flow = memb(channel_desc + 0x84)`**, leído por `diagcomm_io_transmit` (0xc0d56520).
> Ese byte se **inicializa a 1** cuando se registra un canal socket con `io_type != 0`
> (0xc0d8b0e8). Lo que un control-msg controla NO es `allow_flow` directamente, sino el
> **Tx-mode/real-time por `stream_id`**, y el control-msg que lo hace es el **type 0x11
> ("Tx Mode = %d for stream_id = %d")**, NO el `DIAGMODE` (type 3) que vos mandás.
> El `DIAGMODE` (type 3) NO toca ni `allow_flow` ni el Tx-mode-por-stream.

================================================================================
## 1. `diagcomm_io_transmit` — qué lee EXACTAMENTE como `allow_flow` (FACT)
================================================================================

Función real: **`0xc0d56520`** (prólogo `allocframe(#0x30)`). Firma efectiva: `r0 = channel_desc`, `r1 = buffer`.

```
c0d56520: r16 = r0                       ; r16 = puntero al DESCRIPTOR DE CANAL
c0d56524: allocframe(#0x30)
c0d56528: r18 = r1                       ; r18 = buffer a transmitir
c0d5652c: r2 = memb(r0+#0x0)             ; r2 = io_type  (campo @ +0x00 del descriptor)
c0d56530: p0 = cmp.eq(r2,#0x2); if (p0) jump 0xc0d56550   ; io_type==2 (SOCKET/DATA) -> ruta real
c0d56538: r17 = #0x0
c0d56540: p0 = cmp.eq(r2,#0x0); if (p0) jump 0xc0d37294   ; io_type==0 -> otra ruta
c0d56544: r0 = r17 ; dealloc_return       ; else return 0

--- ruta io_type==2 (0xc0d56550): ---
c0d56558: r2 = memb(r16+#0x84)           ; <<< r2 = allow_flow  (BYTE @ +0x84 del descriptor)
c0d5655c: (immext)
c0d56560: if (cmp.eq(r2.new,#0x0)) jump 0xc0d372d0   ; <<< SI allow_flow==0 -> LOG de error y return -9
c0d56564: r2 = memw(r18+#0x0)            ; longitud/estado del buffer
c0d5656c: if (==0) jump 0xc0d37300       ; buffer vacío -> log
...                                      ; si allow_flow!=0 y buffer!=0 -> SIGUE y transmite
```

El bloque de error **`0xc0d372d0`** es exactamente el que imprime el format-string
`diagcomm_io_transmit: allow_flow = %d, channel_type = %d, io_type = %d, port_num = %d`
(string @ 0xc35cd525) y devuelve **r17 = -9**:

```
c0d372d0: r0 = memw(r17+#0x0); r2 = memub(r16+#0x3)   ; r16+0x3 = channel_type
c0d372e0: r3 = memub(r16+#0x1)                         ; r16+0x1 = otro campo (io_type "lógico")
c0d372e4: memw(r29+#0xc) = r3
c0d372e8: memw(r29+#0x8) = #0x2                        ; io_type impreso = 2
c0d372ec: call 0xc0992904                              ; MSG_SPRINTF (F3)
c0d372f0: r17 = #-0x9                                  ; <<< RETORNO = -9 (NO transmite)
c0d372f4: memw(r29+#0x0) = #0x0                        ; allow_flow impreso = 0
c0d372fc: jump 0xc0d56544                              ; return r17(-9)
```

**FACT — respuesta a la pregunta 2:**
- `allow_flow` = **`memb(channel_desc + 0x84)`** (un **byte** en el descriptor de canal).
- No es un global. Es **campo del descriptor de canal**.
- Condición para transmitir: `io_type (== memb+0x0) == 2` **Y** `allow_flow (memb+0x84) != 0`
  **Y** buffer no vacío. Si `allow_flow==0` → log de error y `return -9` (la respuesta se
  queda en el diagbuffer).

Layout del descriptor de canal (FACT, de transmit + init §4):
| offset | campo |
|--------|-------|
| +0x00  | `io_type` (transmit compara ==2 = socket-data; ==0 = otra ruta) |
| +0x01  | io_type "lógico"/instancia |
| +0x03  | `channel_type` |
| +0x10  | flag (=1) |
| +0x14  | `port_num` (= 0x1001 para DIAG) |
| +0x84  | **`allow_flow`** (byte; 1 = drena, 0 = bloqueado) |
| +0x88  | flag de flow-control interno (STOP pendiente) |

================================================================================
## 2. Dónde se INICIALIZA `allow_flow = 1` (FACT — clave)
================================================================================

Registrador/inicializador de descriptor de canal: **`0xc0d8b004`** (parte de
`diagcomm_io_socket_open`/registro de peer). Por cada canal socket:

```
c0d8b0c0: r1 = memub(r16+#0x1)          ; io_type del canal
c0d8b0c4: memb(r16+#0x88) = #0x0        ; flow-ctrl STOP = 0
c0d8b0c8: p0 = cmp.eq(r1,#0x0)          ; io_type == 0 ?
c0d8b0cc: memb(r16+#0x0) = #-0x1        ; channel_type = 0xFF (sin asignar aún)
c0d8b0d0: memb(r16+#0x89) = #0x0
c0d8b0d4: memb(r16+#0x10) = #0x1
c0d8b0dc: memw(r16+#0x14) = ##0x1001    ; <<< port_num = 0x1001  (servicio DIAG QRTR)
c0d8b0e0: if (io_type==0) jump 0xc0d8b120    ; canales io_type==0 (CNTL?) NO ponen allow_flow
c0d8b0e8: memw(r16+#0x84) = #0x1         ; <<< allow_flow = 1   (SÓLO si io_type != 0)
```

**FACT — muy importante:** `allow_flow` **arranca en 1** para todo canal socket con
`io_type != 0` (los canales de DATOS, io_type==2). O sea, por defecto la respuesta
**SÍ** debería drenar. `allow_flow` sólo se pone en 0 por un **flow-control STOP** (§3).

**INFERENCE:** esto significa que si tu respuesta NO drena, lo más probable **NO** es
`allow_flow==0` per se (arranca en 1), sino una de:
(a) el canal DATA para tu peer **nunca se abrió/registró** (no existe descriptor con
    io_type==2 apuntando a tu node/port), o
(b) llegó un **flow-control STOP** que puso `allow_flow=0` y nunca mandaste el "GO", o
(c) la respuesta está en **modo NRT/buffering** por el Tx-mode del `stream_id` y espera
    un **drain trigger** (nunca llega a `transmit`), o
(d) drena por el canal DATA "del sistema" y no por el socket que vos servís.

================================================================================
## 3. Quién pone `allow_flow` en 0 y en 1 en runtime — la máquina de flow-control (FACT)
================================================================================

Dentro del propio `transmit` hay una sub-máquina de estados de flow-control (alcanzada
por `jump 0xc0d37330` desde 0xc0d565f8) que reacciona a bytes de control **0xEF / 0xF4 / 0xF7**:

```
c0d3739c: p0 = cmpb.eq(r2,#0xef); if (p0) jump 0xc0d3736c   ; 0xEF: (sin cambiar flow)
c0d373a4: p0 = cmpb.eq(r2,#0xf4); if (!p0) jump 0xc0d37400
c0d373ac: ; caso 0xF4  (FLOW STOP)
          r2=#1; memb(r16+#0x88)=1        ; marca STOP pendiente
          r1=#0; memb(r16+#0x84)=0        ; <<< allow_flow = 0   (bloquea drenaje)
          LOG (c35cd640) ; return -5
c0d37400: p0 = cmpb.eq(r2,#0xf7); if (!p0) jump 0xc0d374c0
c0d37408: ; caso 0xF7  (FLOW STOP + contador)
          r3=#1; memb(r16+#0x88)=1
          r3=#0; memb(r16+#0x84)=0        ; <<< allow_flow = 0
          incrementa contador global (c9506900) ; return -7
```

Y en la ruta de transmisión exitosa (0xc0d565a4), tras drenar re-arma el flujo:
```
c0d565a4: r2=#1
c0d565b0: memb(r16+#0x84) = r2   ; <<< allow_flow = 1  (re-habilita)
c0d565c0: memb(r16+#0x88) = 0    ; limpia STOP
```

**FACT.** `allow_flow` es un flip-flop: **1 al abrir el canal**, → **0** si llega
flow-control **0xF4/0xF7** (STOP) del peer, → **1** de nuevo cuando el peer permite
seguir (GO) y se completa una transmisión. Los codes 0xEF/0xF4/0xF7 son la señalización
de flow-control DIAG (equivalente a `DIAG_CTRL_FEATURE`/`STOP/GO`) recibida por el canal.

**INFERENCE:** si tu host-side abre el socket DATA y **no** implementa el protocolo de
flow-control (no manda "GO"/"resume" cuando el modem manda STOP, o el kernel diag del AP
lo hace por vos), el modem puede dejar `allow_flow=0` tras el primer STOP y ya no drenar.

================================================================================
## 4. `diagpkt_process_ctrl_msg` — el parser de control-msgs (FACT)
================================================================================

Función: entry en **`0xc0d66264`** (prólogo `allocframe(#0x60)`), dispatch en **`0xc0d662a0`**.

### 4.1 Header del control-msg (wire format) — FACT
Parse en 0xc0d662b0–0xc0d662f0:
```
type     = u32 LE @ +0x00     ; (leído de bytes [0..3])   -> r5
data_len = u32 LE @ +0x04     ; (leído de bytes [4..7])   -> r1  ; debe ser <= total-8
payload  =        @ +0x08     ; r21 = payload+4 (@+0xc), r22 = payload+8 (@+0x10)
```
Range check: `(type - 3) <= 0x62` si no, se ignora. Jumptable en **0xc35c9b78**
(indexada por `type-3`, entradas de 4 bytes).

Header estándar Qualcomm: `struct diag_ctrl_msg { uint32 cmd_type; uint32 data_len; ... }`.

### 4.2 Tabla de tipos → handler (FACT, extraída de la jumptable @0xc35c9b78)
| type (dec / hex) | handler VA | rol (por strings/lógica que referencia) |
|---|---|---|
| **3**  | 0xc0d66348 | **DIAGMODE** (RT/NRT). Refs `RT Mode`/`NRT Mode` (c35c9d1e/c35c9d45). **NO toca allow_flow ni Tx-mode-por-stream.** |
| 8      | 0xc0d66f34 | **FEATURE mask**. Ref `Received APPS Feature mask = 0x%X` (c35c9e8b). Lee `feature_mask = u32 @ payload[0..3]`. |
| 9,10,11,14,15,16 | ... | sub-handlers de máscaras (log/event/msg) |
| 13 (0xd) | 0xc0d670e4 | reg/dereg de comandos |
| **17 (0x11)** | **0xc0d6711c** | **TX MODE por stream_id**. Ref `Tx Mode = %d for stream_id = %d` (c35c9ec7). **ÉSTE fija el Tx-mode/real-time por stream.** |
| 18 (0x12) | 0xc0d67278 | (relacionado a mask ranges) |
| 21 (0x15) | 0xc0d66af4 | |
| 31 (0x1f) | 0xc0d6745c | |
| 33 (0x21) | 0xc0d67484 | (registro de peer / open) |
| 34 (0x22) | 0xc0d67538 | |
| 35 (0x23) | 0xc0d66b50 | escribe stream state @ +0xb0..+0xb3 (por bitmask de stream) |
| 39 (0x27) | 0xc0d675b4 | |
| 40 (0x28) | 0xc0d675f8 | |
| 100 (0x64)| 0xc0d6684c | control grande (feature/mask combinado) |
| 101 (0x65)| 0xc0d66aec | |

### 4.3 Handler DIAGMODE (type 3, 0xc0d66348) — qué hace (FACT)
```
c0d66350: r2 = u24 @ payload[0..2]      ; version/subtype
c0d66364: if (r2==2) jump 0xc0d6661c    ; version 2
c0d66368: if (r2!=1) return             ; version 1
c0d66370: if (data_len != 0x24) return  ; v1 exige data_len == 36
c0d663c4: r5 = u32 @ payload[+0x0c..0f] ; campo de modo
c0d663e0: if (r5 != 1) jump ... ; branch "NRT Mode with diag_real_time_mode = %d" (c35c9d1e)
                                ; else "RT  Mode ..." (c35c9d45)
          -> llama a c0d6f2cc/c0d6f2e4/c0d6a6b4/c0d7ad68/c0d7ae48/c0d6f2f0/c0d6f360/c0d6f3c4
```
**FACT:** DIAGMODE setea parámetros de **buffering global** (sleep vote, límites de
tamaño, timers de drain) — NO escribe `allow_flow (+0x84)` ni el Tx-mode-por-stream.
Es exactamente lo que vos mandás, y **por eso no cambia el drenaje de respuestas**.

### 4.4 Handler TX MODE (type 0x11, 0xc0d6711c) — el que importa (FACT)
```
c0d67124..67134: r1 = u24 @ payload[0..2]     ; NUM_STREAMS (1 o 2)
c0d67150: if (num_streams==2) jump 0xc0d671f0
c0d67154: if (num_streams!=1) error
--- caso 1 stream (0xc0d67158): ---
c0d67158: r16 = payload[4]                     ; (base loop)
   loop sobre lista de sesiones (head @ c92e4734):
c0d671a4: r1 = memub(r20+#0xd) = payload[5]    ; TX_MODE value
c0d671b0: call 0xc0d7dc78(stream_id=r22, tx_mode=payload[5])   ; <<< SETTER
c0d671c8: LOG "Tx Mode = %d for stream_id = %d"  (payload[5], stream_id)
--- caso 2 streams (0xc0d671f0): ---
c0d671f0: r16 = payload[5]                      ; stream_id[0]
c0d67220: r1  = payload[6]                       ; tx_mode[0]
c0d6722c: call 0xc0d7dc78(stream_id=payload[4], tx_mode=payload[6])
   (repite para el 2do stream)
```

El **setter `0xc0d7dc78`** (FACT):
- valida `stream_id` (arg r1): `if (stream_id==1) ...` — sólo procesa stream 1/2.
- camina la lista de streams (head @ `0xcb93fe40`, `.next` @ +0x0), matcheando
  `memb(stream_obj+0xae) == stream_id`.
- escribe **`memb(stream_obj + 0xaf) = tx_mode`** (0xc0d7dd18) → el Tx-mode QUEDA POR STREAM.
- también toca el array por-stream @ `0xcb93fbc0` (entradas de 0xe0 bytes, index = stream_id-1):
  copia timers `+0x34→+0x40`, `+0x38→+0x44`, `+0x3c` etc. (RT vs NRT rates).
- cuando el modo pasa a "real-time" llama a **`0xc0d696bc`** = drain/flush del diagbuffer
  (byte-stuffing HDLC 0x7d/0x7e), que es lo que finalmente empuja al `transmit`.

**FACT — respuesta a preguntas 1, 3 y 5:**
- El control-msg que fija el **Tx-mode/real-time por stream** es el **type 0x11**
  ("Tx Mode = %d for stream_id = %d"), NO el DIAGMODE (type 3).
- El Tx-mode se guarda **por `stream_id`** en `stream_obj+0xaf`; el drain se dispara por
  stream cuando el modo es real-time (`0xc0d696bc`).
- `allow_flow (+0x84)` del canal NO lo escribe este handler directamente; el handler
  habilita el **drain** del stream, que llama a `transmit`, que lee `allow_flow`.

================================================================================
## 5. `stream_id`: cuál activar (FACT + INFERENCE)
================================================================================

- Rango válido de `stream_id` (validado en 0xc0d7dc78/0xc0d7de30): **1 o 2**
  (`index = stream_id-1 ∈ {0,1}`; `if (idx > 1) error`). → **DIAG_STREAM_1 = 1**,
  **DIAG_STREAM_2 = 2**. **FACT.**
- Los F3 logs que **SÍ** recibís y las respuestas de comando comparten el transporte
  DATA, pero el **buffering/drain es por stream**. En el modelo estándar Qualcomm:
  **DIAG_STREAM_1 (=1)** es el stream de **comandos + respuestas + F3 en tiempo real**
  (el que consume el diag host normal). **INFERENCE fuerte.**
- Que los F3 lleguen pero las respuestas subsys no, es consistente con que el stream de
  la RESPUESTA quedó en **NRT/buffering** (no real-time) para **stream_id=1**, mientras
  que los F3 salen por otra config. → **hay que poner Tx-mode = real-time para stream_id 1**
  (y si querés cubrir todo, también para el 2). **INFERENCE.**

================================================================================
## 6. Formato de bytes EXACTO de los control-msgs a mandar por CNTL (FACT del parser)
================================================================================

Header común (§4.1), little-endian:
```
offset 0: u32  cmd_type
offset 4: u32  data_len      ; = número de bytes DESPUÉS del header (payload)
offset 8: payload[...]
```

### 6.1 TX MODE (type 0x11) — 1 stream, real-time (ESTE es el que te falta)
Layout que parsea el handler para num_streams==1:
```
u32  cmd_type   = 0x00000011      ; 17
u32  data_len   = 0x00000006      ; 6 bytes de payload (num_streams(4) + stream_id(1) + tx_mode(1))
u32  num_streams= 0x00000001      ; payload[0..3] = 1
u8   stream_id  = 0x01            ; payload[4]  = DIAG_STREAM_1
u8   tx_mode    = 0x01            ; payload[5]  = 1  (real-time / drain-on)   <-- ver nota
```
Bytes en la línea (LE):
```
11 00 00 00  06 00 00 00  01 00 00 00  01 01
```

> **Nota sobre el valor de `tx_mode`:** el handler NO hardcodea el semantic; pasa
> `payload[5]` tal cual a `0xc0d7dc78` que lo guarda en `stream_obj+0xaf`. El valor que
> dispara el **drain real-time** es el que hace que `0xc0d696bc` (flush) se invoque. En el
> stack Qualcomm estándar **`DIAG_STREAM_TX_MODE`: 1 = real-time (drenar), 0/otros =
> buffered/NRT**. Probá **`tx_mode = 1`**. Si tu build espera el enum invertido, probá 0.
> (Valor semántico exacto: **UNKNOWN** estático; el código sólo lo propaga.)

### 6.2 TX MODE (type 0x11) — 2 streams (opcional, cubre ambos)
```
u32  cmd_type   = 0x00000011
u32  data_len   = 0x00000008      ; num_streams(4) + [id,mode]*2
u32  num_streams= 0x00000002
u8   stream_id0 = 0x01
u8   tx_mode0   = 0x01
u8   stream_id1 = 0x02
u8   tx_mode1   = 0x01
```
Bytes: `11 00 00 00  08 00 00 00  02 00 00 00  01 01 02 01`
(el parser de 2-streams lee payload[4]=id, payload[5]=id_base, payload[6]=mode — el
orden exacto de 2-streams es más enredado en el disasm; **usá 1-stream x2 mensajes**
en vez del combinado si querés estar seguro.)

### 6.3 FEATURE mask (type 8) — probablemente ya lo mandás; confirmá el formato
```
u32  cmd_type   = 0x00000008
u32  data_len   = 0x00000004      ; (al menos 4; el handler lee u32 @ payload[0..3])
u32  feature_mask = <tu máscara>  ; payload[0..3]
```
Bytes: `08 00 00 00  04 00 00 00  <mask LE>`

### 6.4 DIAGMODE (type 3) — lo que VOS mandás; correcto pero insuficiente
El handler exige **version==1 y data_len==0x24 (36)** (o version==2 por otra rama):
```
u32  cmd_type = 0x00000003
u32  data_len = 0x00000024        ; 36 bytes EXACTOS para v1
u32  version  = 0x00000001        ; payload[0..3]
... (36 bytes de payload: sleep_vote, real_time flag @ payload[+0x0c..0f], límites, etc.)
```
**Comprobá tu `data_len`:** si mandás menos de 0x24 con version=1, el handler hace
`return` sin procesar (0xc0d66370). Pero aunque lo proceses bien, **DIAGMODE NO habilita
el drenaje de respuestas** — sólo ajusta buffering global. **Ésta es tu confusión de raíz.**

================================================================================
## 7. Qué te falta vs tu handshake actual (respuesta directa)
================================================================================

Tu handshake actual: feature mask, DIAGID ack, msg mask, log mask, event mask, y
**DIAG_CTRL_MSG_DIAGMODE (type 3) con real_time=1**.

**Lo que te falta / está mal (FACT/INFERENCE):**

1. **Mandás el control-msg equivocado para el drenaje.** El que gatilla el Tx-mode/
   real-time **por stream** (y por ende el drain de respuestas) es el **type 0x11
   ("Tx Mode for stream_id")**, NO el DIAGMODE type 3. **Agregá el type 0x11 con
   stream_id=1, tx_mode=1** (§6.1). **FACT** (el disasm muestra que sólo el 0x11 llama
   al setter por-stream 0xc0d7dc78; el type 3 no).

2. **Tu DIAGMODE (type 3) puede estar siendo descartado** si `data_len != 0x24`
   (version 1). Verificá que mandás **exactamente 36 bytes** de payload. **FACT.**

3. **`allow_flow` NO es tu bloqueo principal**: arranca en 1 al abrir el canal DATA
   (§2). Si tu respuesta no sale, revisá **en orden**:
   - (a) que **exista y esté abierto** el canal socket con **io_type==2** para tu peer
     (que el AP haya hecho `new_server`/bind del servicio DATA **antes** de mandar el
     comando; el modem crea el descriptor con port 0x1001 y allow_flow=1). **INFERENCE.**
   - (b) que **no** haya un **flow-control STOP** (byte 0xF4/0xF7) que puso allow_flow=0
     sin "GO" posterior (§3). Si tu stack QRTR no implementa resume, el modem se queda
     bloqueado tras el primer STOP. **FACT del mecanismo.**
   - (c) que el **stream_id=1 esté en real-time** (paso 1). En NRT la respuesta se
     acumula y `transmit` ni se llama. **INFERENCE.**
   - (d) que estés **leyendo por el socket del servicio DATA**, no por el socket cliente
     del CMD (el CMD es RX). El modem hace `sendto(node=AP, port=DATA)` con node/port del
     descriptor. **INFERENCE (ver diag_response_routing.md §7).**

4. **Orden recomendado del handshake por CNTL:**
   1. Registrar/abrir servicios QRTR (CNTL, **DATA con io_type==2**, DCI) **antes** de
      tocar CMD → el modem inicializa el descriptor DATA con `allow_flow=1`.
   2. FEATURE mask (type 8).
   3. **TX MODE (type 0x11) stream_id=1 tx_mode=1** (§6.1)  ← **lo nuevo/faltante**.
      (opcional stream_id=2 tx_mode=1).
   4. (opcional) DIAGMODE (type 3, data_len=0x24, real_time=1) para buffering global.
   5. Mandar TECH_ENTER por CMD.
   6. **Leer la respuesta por el socket DATA.**

================================================================================
## 8. ¿Depende de que el peer QRTR esté registrado de cierta forma? (FACT + INFERENCE)
================================================================================

- **SÍ.** `allow_flow=1` sólo se inicializa para descriptores con **io_type != 0**
  (0xc0d8b0e0/0xc0d8b0e8), que es la clase de canal **socket-data (io_type==2)**. Si el
  canal por el que quieren drenar no está registrado como io_type==2 (porque el peer
  DATA no se abrió/registró aún), no hay descriptor con allow_flow=1 y `transmit` nunca
  encuentra a quién drenar. **FACT (init) + INFERENCE (que corresponde al peer DATA).**
- El descriptor guarda **port_num = 0x1001** (servicio DIAG) y su node/port de destino se
  rellena al abrir/registrar el peer (ver diag_response_routing.md §3.2/§7). El modem NO
  usa tu source-addr del CMD como destino. **INFERENCE fuerte.**
- El **stream** (donde se guarda el Tx-mode, `stream_obj+0xaf`) se crea/matchea por
  `stream_id` (`+0xae`); si el AP no "abrió" el stream 1 (registro), el setter 0xc0d7dc78
  camina la lista y no matchea → el Tx-mode no se aplica a nada. **FACT (el loop matchea
  por +0xae) + INFERENCE (que el stream se crea al registrar el peer).**

================================================================================
## 9. FACT / INFERENCE / UNKNOWN (cierre)
================================================================================

**FACT (probado en esta imagen, con VA):**
- `diagcomm_io_transmit` = **0xc0d56520**. Lee `io_type` @ `memb(desc+0x0)` (exige ==2) y
  **`allow_flow` = `memb(desc+0x84)`**; si es 0 → log (string @0xc35cd525) y `return -9`.
- `allow_flow (+0x84)` se **inicializa a 1** en el registro de canal **0xc0d8b0e8**, sólo
  para io_type!=0; port_num=0x1001 @ +0x14.
- Se pone a **0** con flow-control STOP (0xF4/0xF7) en 0xc0d373c0/0xc0d3741c; se re-arma a
  **1** tras transmisión exitosa en 0xc0d565b0.
- `diagpkt_process_ctrl_msg` entry 0xc0d66264, dispatch 0xc0d662a0, jumptable @0xc35c9b78.
  Header: `{u32 cmd_type; u32 data_len; payload}`.
- **type 3 = DIAGMODE** (0xc0d66348, RT/NRT, buffering global; NO toca allow_flow ni
  Tx-mode-por-stream; v1 exige data_len==0x24).
- **type 8 = FEATURE** (0xc0d66f34; lee u32 @payload[0..3]).
- **type 0x11 = TX MODE por stream_id** (0xc0d6711c; parsea num_streams @payload[0..3],
  stream_id, tx_mode; llama al setter 0xc0d7dc78).
- setter **0xc0d7dc78** guarda tx_mode en `stream_obj+0xaf` (por stream_id, match @+0xae) y
  dispara drain (0xc0d696bc) en real-time. stream_id válido ∈ {1,2}.

**INFERENCE (modelo DIAG Qualcomm, consistente):**
- El drenaje de la respuesta subsys FTM ocurre por el canal DATA (io_type==2) sólo si el
  stream (1) está en real-time y no hay STOP pendiente; el type 0x11 es el disparador.
- DIAG_STREAM_1=1 es el stream de comando/respuesta en tiempo real.
- El peer DATA debe estar registrado/abierto antes para que exista el descriptor con
  allow_flow=1 y el stream con `+0xae`.

**UNKNOWN (sólo en vivo):**
- Valor semántico exacto de `tx_mode` (1 vs 0) que este build interpreta como
  "real-time/drenar" (el código sólo lo propaga a `+0xaf`; probar 1 primero, luego 0).
- Si tu ruta ya recibió un flow-control STOP (0xF4/0xF7) y quedó allow_flow=0 (requiere
  capturar el tráfico CNTL/DATA en vivo).
- El node/port QRTR concreto del servicio DATA (lo asigna el bind del AP).
- Orden de bytes exacto del caso **2-streams** del type 0x11 (usar dos mensajes de
  1-stream para evitar ambigüedad).
