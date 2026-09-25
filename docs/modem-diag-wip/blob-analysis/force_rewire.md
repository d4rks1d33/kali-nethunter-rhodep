# force_rewire.md — Cómo forzar el re-cableo del descriptor COMMAND (`0xcb93f380`) y el gate `connected` (`0xc95089a0`) SIN reiniciar el modem (SM6375, MPSS.HI.4.3.4)

**Build:** MPSS.HI.4.3.4-00494-MANNAR_GEN_PACK (SM6375, Moto G82 5G) · Hexagon v66
**Imágenes:** `_dis_b13.txt` (diag core+transporte, VA 0xc0d36000+), rodata seg21 (VA 0xc3553000, off 0x01a11000 en `modem_full.elf`).
**Verificación:** toda VA con `grep -n "^<VA>:" _dis_b13.txt`; tablas de salto decodificadas desde `modem_full.elf` seg21.
**Leyenda:** **FACT** = leído del disasm/rodata (VA citada) · **INFERENCE** = deducción consistente · **UNKNOWN** = sólo en vivo.

> Este documento **profundiza y corrige** `reply_routing_norestart.md`. La conclusión operativa cambia:
> el re-cableo del descriptor COMMAND **SÍ tiene un segundo camino que NO pasa por el gate `connected`**
> (`0xc0d80ba4 → 0xc0d81af8`), disparado por un **DIAG_CTRL command r6=4** sobre el canal CNTL.
> Eso convierte al menos parte del "UNKNOWN" previo en accionable.

---

## 0. TL;DR — los tres hechos duros nuevos

1. **`connected` (`0xc95089a0`) es un latch de una sola escritura.** En TODO el código diag hay exactamente **dos** referencias: lectura en `0xc0d5f5a8` y escritura en `0xc0d37614`. La escritura **sólo** pone `1` (`if(!p0) memb(0xc95089a0)=r2` con `r2=#0x1`). **No existe ninguna instrucción que lo ponga a 0.** ⇒ No hay ctrl-msg, DEL_CLIENT, timeout ni comando DIAG que resetee `connected`. Sólo lo vuelve a 0 el zero-init de BSS en un boot fresco. **FACT.** (§2)

2. **El descriptor COMMAND se re-cablea por DOS caminos distintos a `0xc0d81af8`:**
   - **Camino A (gateado):** `0xc0d375e4 (full connect-init) → call 0xc0d81af8` en `0xc0d375ec`. Sólo corre si `connected==0` (gate `0xc0d5f5b0`). Es el que usa `--restart-modem`. **FACT.**
   - **Camino B (NO gateado por `connected`):** `0xc0d80ba4 → call 0xc0d81af8` en `0xc0d80bb8`. Se alcanza desde el **parser de DIAG_CTRL** `0xc0d7fcdc`, comando **r6=4**, sub-op **r20=17** (tabla `0xc35ce044`). Este camino **re-abre y re-cablea el descriptor sin mirar `0xc95089a0`**. **FACT (grafo de llamadas + tablas de salto).** (§3)

3. **El destino QRTR COMMAND es el par global único `0xc8c2d870`(node u32)/`0xc8c2d874`(port u8).** Escrito **incondicionalmente** sólo en NEW_SERVER (`0xc0d83018/20`). Ningún path lo pone a 0 en peer-down. `allow_flow` del descriptor vive en `desc+0x84` (`0xcb93f404`), arranca en 1 (`0xc0d8b0e8`), baja por STOP (`0xc0d373ac`), sube por RESUME (`0xc0d565b0`). **FACT.** (§4)

**Consecuencia:** para que la respuesta FTM vuelva a NUESTRO socket sin reboot hay que (i) escribir el par global con nuestro node/port vía NEW_SERVER inst 1 (ya lo hacés) y (ii) **disparar el re-cableo del endpoint por el Camino B** (DIAG_CTRL r6=4 / la secuencia de registro de peripheral-diag), no por el Camino A. El Camino A es inalcanzable sin reboot porque `connected` no se puede bajar.

---

## 1. Mapa de las funciones clave (VA → rol) — FACT

| VA | Rol | Evidencia |
|---|---|---|
| `0xc0d375e4` | **full connect-init** (Camino A) | `0xc0d375ec: call 0xc0d81af8`; `0xc0d37614: memb(0xc95089a0)=1` |
| `0xc0d5f5a4` | **gate connected** → salta a `0xc0d375e4` sólo si `connected==0`, si no `return` | `0xc0d5f5a8: r2=memub(0xc95089a0)`; `0xc0d5f5b0: if(!gtu(r2,0)) jump 0xc0d375e4`; `0xc0d5f5b4: jumpr r31` |
| `0xc0d81af8` | **open/rewire descriptores** (walker de lista `0xc9508978`, alloc/relink desc) | dos callers: `0xc0d375ec` y `0xc0d80bb8` |
| `0xc0d80ba4` | **registrar/re-cablear canal** (Camino B, NO gateado) | `0xc0d80bb8: call 0xc0d81af8` |
| `0xc0d7fcdc` | **parser DIAG_CTRL** (dispatch r6 vía `0xc35cdfec`; sub r20 vía `0xc35ce044`) | `0xc0d7fd50: r3=memw(r3<<2+0xc35cdfec); jumpr r3`; `0xc0d80080: r2=memw(r2<<2+0xc35ce044); jumpr r2` |
| `0xc0d82fc0` | **NEW_SERVER handler**: escribe global `0xc8c2d870/74` incondicional; `call 0xc0d5f5a4` | `0xc0d83018/20`; `0xc0d83024` |
| `0xc0d83048` | **QRTR ctrl pump por canal** (lee `0xc8c2d86e`, node/port global; ruta a NEW_SERVER/`0xc0d93d80`) | `0xc0d83060/88`, `0xc0d83094: jump 0xc0d82fc0` |
| `0xc0d55c40` | `diagpkt_rsp_send` (mux COMMAND/DATA por `rsp+0x20`) | `0xc0d55d54..60` (1=CMD,2=DATA) |
| `0xc0d5d580` | drain-a-canal: bit0→desc COMMAND `0xcb93f3ac`, bit1→desc DATA `0xcb93f608` | `0xc0d5d634/38`, `0xc0d5d5a0` |
| `0xc0d56520` | `diagcomm_io_transmit`: lee `allow_flow=memb(desc+0x84)` | `0xc0d56558` |
| `0xc0d68168` | kick de signals RESEND/DRAIN → `0xc92e91a0` | via `0xc0d696bc` |

---

## 2. `connected` (`0xc95089a0`): latch de una sola escritura — **NO reseteable sin reboot** (FACT)

Búsqueda exhaustiva en `_dis_b13.txt` (y `_dis_b10.txt`, `_big1.txt`): **`0xc95089a0` aparece 2 veces, en ninguna otra imagen.**

```
; LECTURA (el gate)
c0d5f5a4: r2 = memub(##0xc95089a0)                 ; connected?
c0d5f5b0: if (!cmp.gtu(r2,#0x0)) jump 0xc0d375e4    ; NO connected -> FULL init (Camino A)
c0d5f5b4: jumpr r31                                  ; YA connected  -> RETURN (no-op)

; ÚNICA ESCRITURA (dentro de full connect-init 0xc0d375e4)
c0d375fc: r2 = memb(r0+#0x9)                         ; r0 = desc recién abierto por 0xc0d81af8
c0d37600: memb(##0xc95089a1) = r2.new               ; (byte vecino, diag_id)
c0d37608: p0 = cmp.eq(r2,#0x0)
c0d3760c: if (!p0.new) r2 = #0x1
c0d37614: if (!p0.new) memb(##0xc95089a0) = r2.new  ; connected = 1   (SÓLO 1, nunca 0)
```
**FACT** (líneas 1408–1420, 42293–42297).

**Implicaciones directas (responde tu pregunta 2):**
- No hay **ningún** escritor que ponga `0xc95089a0=0`. Ni STOP, ni BYE, ni DEL_CLIENT, ni DEL_SERVER, ni timeout, ni el propio `0xc0d80ba4`. **FACT.**
- Por lo tanto **el Camino A (`0xc0d375e4/0xc0d81af8`) es irrecuperable sin reboot.** Cualquier estrategia que dependa de "bajar connected" está muerta. **FACT.**
- El `connected` NO gatea el drenaje ni el destino del `sendto`; sólo gatea si `0xc0d5f5a4` re-corre el full-init. El drain (`0xc0d5d580`) y el `sendto` (`0xc0d93ed4/0xc0d95f64`) leen el **global vivo** y el **estado del descriptor**, no `connected`. **FACT.** ⇒ **No necesitamos** bajar `connected`; necesitamos re-cablear el descriptor por otra vía y ganar el global. (§3, §7)

---

## 3. El re-cableo NO-gateado: `0xc0d80ba4 → 0xc0d81af8` vía DIAG_CTRL r6=4 (FACT — hallazgo central)

### 3.1 `0xc0d81af8` tiene exactamente dos callers
```
grep "call 0xc0d81af8":
  c0d375ec   ; dentro de 0xc0d375e4 (full init, gateado por connected)
  c0d80bb8   ; dentro de 0xc0d80ba4 (NO gateado)
```
**FACT.**

### 3.2 `0xc0d80ba4` re-cablea el descriptor sin tocar `connected`
```
c0d80ba4: (entry)  ; args r0..r5 = {node/port/flags del canal a registrar}
c0d80bb8: call 0xc0d81af8            ; <<< re-abre/relink el descriptor de canal
c0d80bc0: r20 = r0                    ; r0 = puntero al descriptor (0 si falló)
...
c0d80c14: call 0xc0a0420c            ; log/emit "diag ch registered ..."
```
No hay lectura de `0xc95089a0` en toda la función. **FACT** (76439–76479).

### 3.3 Cadena de disparo: parser DIAG_CTRL `0xc0d7fcdc`
`0xc0d7fcdc` es el **parser de comandos DIAG_CTRL** (usa `0xc0a0420c`, un scanf-like con format `%s/%d`, sobre el payload del ctrl-msg). Tiene **dos niveles de dispatch por tabla**:

- **Nivel 1 (r6, comando):** `cmp.gtu(r6-1,#0x15)`; `r3=memw((r6-1)<<2 + 0xc35cdfec); jumpr r3`.
  Tabla `0xc35cdfec` (22 entradas, decodificada de seg21):
  ```
  r6=1 -> 0xc0d7fd60      r6=2 -> 0xc0d7fdb8     r6=3 -> default(0xc0d804f8)
  r6=4 -> 0xc0d8005c      r6=5 -> 0xc0d802cc     r6=6 -> 0xc0d802fc
  r6=7 -> 0xc0d80328      r6=8 -> 0xc0d80338     r6=9 -> 0xc0d80384
  r6=10-> 0xc0d80390      r6=11-> 0xc0d80318     r6=12-> 0xc0d80374
  r6=13-> default         r6=14-> 0xc0d803a8     r6=15-> 0xc0d8034c
  r6=16-> default         r6=17-> 0xc0d80498     r6=18-> 0xc0d8039c
  r6=19-> 0xc0d804a8      r6=20-> 0xc0d804b4     r6=21-> 0xc0d804c4  r6=22-> 0xc0d804e0
  ```
  **FACT** (tabla leída de `modem_full.elf` seg21 @0xc35cdfec).

- **Nivel 2 (r20, sub-op) — SÓLO para r6=4** (rama `0xc0d8005c`):
  ```
  c0d80070: r2 = add(r20,#-0x1)
  c0d80078: if (cmp.gtu(r2,#0x10)) jump 0xc0d802ac   ; sub-op válido 1..17
  c0d80080: r2 = memw(r2<<2 + 0xc35ce044); jumpr r2
  ```
  Tabla `0xc35ce044` (17 entradas):
  ```
  r20=1 ->0xc0d80088  r20=2 ->0xc0d800e4  r20=3 ->0xc0d80088  r20=4 ->0xc0d80120
  r20=5 ->0xc0d80138  r20=6 ->0xc0d80184  r20=7 ->0xc0d801d4  r20=8 ->0xc0d801e4
  r20=9..15 ->0xc0d802ac (default/return)
  r20=16->0xc0d801f4  r20=17->0xc0d80234  ; <<< r20=17 cae en 0xc0d80290/0xc0d80298
  ```
  **FACT** (tabla @0xc35ce044).

- **La rama r20=17 (`0xc0d80234`) fluye a `0xc0d80290/0xc0d80298`:**
  ```
  c0d80290: call 0xc104f4b8          ; helper (copia/valida params del canal)
  c0d80298: call 0xc0d80ba4          ; <<< re-cablea el descriptor (0xc0d81af8)
  ```
  **FACT** (75857–75860).

**Grafo confirmado:**
```
DIAG_CTRL (r6=4, sub-op r20=17)  →  0xc0d7fcdc  →  0xc0d8005c  →  0xc0d80078(jumpr)
   →  0xc0d80234  →  0xc0d80290/98  →  0xc0d80ba4  →  0xc0d80bb8: call 0xc0d81af8  (RE-CABLEO)
```
**Ninguna etapa lee `0xc95089a0`.** **FACT.**

### 3.4 Qué es `r6=4` en el wire (INFERENCE + parcialmente UNKNOWN)
`0xc0d7fcdc` es el handler de la familia de **DIAG_CTRL peripheral control** (registro de canales/PD del propio diag: "diag: register/deregister ch", DIAG_ID/PD assoc). El `r6`/`r20` no son bytes crudos del datagrama: los arma el llamador (indirecto, no hay puntero estático a `0xc0d7fcdc` en las imágenes → se registra en runtime). El texto lo parsea `0xc0a0420c` con formato tipo `"diag: ... %d ..."`.

- **INFERENCE fuerte:** `r6=4 / r20=17` = la operación de **(re)apertura/asociación del canal COMMAND a un peer** (la misma que en boot hace el full-init, pero invocable por control sin `connected`). Es lo que ModemManager/`/dev/diag` del kernel dispara al conectarse, y por eso una vez, tras parar MM, TECH_ENTER respondió: el sistema re-corrió esta secuencia con un peer nuevo.
- **UNKNOWN (sólo en vivo):** el **encoding exacto del payload** (`r6`/`r20`/params) tal como debe salir por el socket CNTL. Requiere capturar en vivo un registro de canal que dispare `0xc0d80ba4` (p.ej. trazar el ctrl que emite el kernel diag al abrir `/dev/diag`, o al re-registrar un peripheral) y replicar esos bytes. La tabla y el grafo son FACT; el mapeo byte↔(r6,r20) es UNKNOWN estático.

---

## 4. El global peer y `allow_flow` — FACT (confirmación y offsets)

### 4.1 Global peer COMMAND: único, escrito sólo por NEW_SERVER, nunca puesto a 0
```
c0d83018: memw(##0xc8c2d870) = r17   ; node (u32) del AP  — INCONDICIONAL
c0d83020: memb(##0xc8c2d874) = r18   ; port (u8)  del AP  — INCONDICIONAL
```
Únicos escritores. Lecturas: `0xc0d82fa8` (dedup NEW_SERVER), `0xc0d83060/88` (ctrl pump), builders `0xc0d83168/88` y `0xc0d832ac/d0`. **Ningún path lo escribe 0 en peer-down.** **FACT** (grep exhaustivo de `c8c2d870/74`).

⇒ **Responde tu pregunta 4 (DEL_CLIENT):** cuando MM cierra su socket y QRTR emite DEL_CLIENT (`06 00 ..`), el diag del modem **NO** resetea `connected` (§2) **ni** el global peer (§4.1) **ni** re-cablea el descriptor. El handler QRTR ctrl (`0xc0d83048`) sólo re-lee el global/`0xc8c2d86e` y puede rutar a NEW_SERVER dedup, pero **no libera el endpoint viejo por DEL_CLIENT**. Matar MM **no** basta por sí solo para que el modem "suelte" el peer viejo; sí ayuda para que MM no re-pise el global ni consuma la respuesta, pero el re-cableo hay que forzarlo (Camino B). **FACT (no-reset) + INFERENCE (competencia MM).**

### 4.2 Layout del descriptor COMMAND `0xcb93f380` (0xb8 bytes)
```
+0x00  io_type            (==2 para socket QRTR de salida; transmit exige ==2)
+0x14  port_num           = 0x1001 (servicio DIAG)      (0xc0d8b0dc)
+0x84  allow_flow (byte)  1=drena, 0=bloqueado          (0xc0d56558 lee; 0xc0d8b0e8 init=1)
       (VA absoluta allow_flow COMMAND = 0xcb93f404)
       (allow_flow DATA  = 0xcb93f68c;  cf. c0d6edd4/c0d79830)
+node/port destino → el descriptor NO guarda su propio node/port fijo; el sendto los toma
       del GLOBAL 0xc8c2d870/74 vía builders 0xc0d83128/0xc0d83220 en cada envío.
```
**FACT** (`allow_flow.md` §1–§2; `0xc0d56558`, `0xc0d8b0e8`, `0xc0d8b0dc`; drain `0xc0d5d580`).

### 4.3 `allow_flow`: STOP baja, RESUME sube
```
init (open):   c0d8b0e8: memw(desc+#0x84) = #0x1
STOP (0xF4/F7): c0d373ac path: memb(desc+#0x84) = 0
RESUME/GO:      c0d565b0: memb(desc+#0x84) = r2 (=1)   ; re-habilita drenaje
```
**FACT.** Si la sesión vieja dejó `allow_flow=0` por un STOP, un **RESUME/TX_MODE GO** lo re-sube sin reboot.

---

## 5. Por qué "una vez funcionó sin reboot" (INFERENCE cerrada con FACT)

La vez que TECH_ENTER devolvió 63 bytes tras parar MM: al morir MM y re-aparecer un cliente (o el kernel diag re-registrando canal), se disparó el **Camino B** (`0xc0d80ba4→0xc0d81af8`) con un peer nuevo, re-cableando el endpoint COMMAND al socket que estaba leyendo, **sin** tocar `connected`. Fue no-reproducible porque tu `--kick` **no** emite el DIAG_CTRL r6=4/r20=17 que dispara ese camino; sólo mandás FEATURE/DIAGID/TX_MODE + NEW_SERVER, que patean DRAIN y ganan el global pero **no re-abren el endpoint**. **INFERENCE fuerte, consistente con todos los FACT anteriores.**

---

## 6. Respuestas directas

1. **Re-cablear `0xcb93f380` sin reboot — VA + método:**
   - **Método (único viable sin reboot):** Camino B = `0xc0d80ba4 → 0xc0d81af8`, alcanzado por **DIAG_CTRL comando r6=4, sub-op r20=17** parseado en `0xc0d7fcdc`. Ese camino re-abre/relink el descriptor **sin** el gate `connected`. Tablas: `0xc35cdfec` (r6), `0xc35ce044` (r20). **FACT (grafo/tablas).** El **payload exacto** del ctrl-msg que produce (r6=4,r20=17) es **UNKNOWN estático** (armado por caller indirecto + scanf `0xc0a0420c`); capturarlo en vivo trazando el registro de canal del kernel diag al abrir `/dev/diag`.
   - **NO** intentes el Camino A (`0xc0d375e4`): requiere `connected==0`, imposible sin reboot.

2. **Resetear/evitar el gate `connected` (`0xc95089a0`):**
   - **No se puede resetear**: latch de una escritura, sin escritor a 0 (§2). **FACT.**
   - **No hace falta**: el drain y el `sendto` no consultan `connected`. Sólo hay que (a) ganar el global peer y (b) re-cablear el endpoint por el Camino B, que **ignora** `connected`. **FACT + INFERENCE.**

3. **¿Matar el socket del peer viejo (MM) vía QRTR DEL_CLIENT lo resuelve?**
   - **Por sí solo, NO.** DEL_CLIENT no resetea `connected` ni el global ni libera el endpoint COMMAND en el diag del modem (§4.1). **FACT (no hay handler que lo haga).**
   - **Es necesario pero no suficiente:** matar MM evita que re-pise `0xc8c2d870/74` y que consuma la respuesta COMMAND, y su desconexión puede **inducir** que el kernel diag re-registre el canal (disparando el Camino B). Pero para robustez hay que emitir el DIAG_CTRL de re-registro nosotros. **INFERENCE.**

4. **Secuencia byte-exacta y confiable:** ver §7.

5. **`0600.. node 0x40xx`** = QRTR ctrl `cmd=0x06` (DEL_CLIENT) / `0x09` (RESUME_TX) del router; `0x40xx` = node del peer que cambió (probablemente MM). No es la respuesta del comando. **FACT (es ctrl QRTR) + INFERENCE (peer).**

---

## 7. Secuencia accionable definitiva (sin reboot)

Objetivo: descriptor COMMAND `0xcb93f380` re-cableado a NUESTRO socket, `allow_flow=1`, global peer = nuestro node/port, en el instante del `sendto` de la respuesta.

### 7.1 Pre-condición: silenciar al competidor
`stop ModemManager` (y cualquier cliente `/dev/diag` del sistema). Motivo: (i) que no re-pise `0xc8c2d870/74` con un NEW_SERVER propio; (ii) que no consuma la respuesta COMMAND si el endpoint quedara apuntándole; (iii) su BYE/close puede inducir re-registro de canal en el kernel diag. **INFERENCE (necesario, no suficiente).**

### 7.2 Handshake CNTL (ya lo hacés) — deja los gates de rsp listos y patea DRAIN
Por el canal **CNTL (inst 0)**, en orden:
1. **FEATURE (type 8)** → set `0xc92e43e0`, llama `0xc0d68168`:
   ```
   08 00 00 00  04 00 00 00  <feature_mask u32 LE>
   ```
2. **DIAGID (type 0x21) v1** → set `0xc92e4754` bit0:
   ```
   21 00 00 00  <len LE>  01 00 00 00  <diag_id u32>  <name NUL>
   ```
3. **TX_MODE (type 0x11) stream 1 RT** → drain RT + (si hubo STOP) RESUME `allow_flow=1` (`0xc0d565b0`):
   ```
   11 00 00 00  06 00 00 00  01 00 00 00  01 01
   ```

### 7.3 **El paso nuevo y decisivo: forzar el Camino B (re-cableo del endpoint)**
Emitir por **CNTL** el/los **DIAG_CTRL de registro de canal** que el parser `0xc0d7fcdc` decodifique como **r6=4 / r20=17**, con params = nuestro (node,port) y port_num 0x1001. Eso ejecuta `0xc0d80ba4 → 0xc0d81af8` y re-abre el descriptor COMMAND apuntando al peer vivo, `allow_flow=1`, **sin** tocar `connected`.

- **Estado del encoding:** grafo y tablas = **FACT**; bytes exactos = **UNKNOWN estático**. Para cerrarlo en vivo (una sola captura):
  - `qrtr`-dump del CNTL mientras (a) arranca MM o (b) se abre `/dev/diag` del kernel; localizar el ctrl-msg que precede a un re-cableo (correlacionar con que TECH_ENTER vuelva a responder). Esos bytes son el `r6=4/r20=17`. Replicarlos con node/port nuestros.
  - Alternativa de fuerza bruta segura: la familia de DIAG_CTRL de este parser usa payload textual (scanf `0xc0a0420c`); probar los ctrl-msgs de "register diag channel/PD" del protocolo diag userspace estándar (los que emite libdiag al hacer `diag_register`), que es exactamente lo que este handler consume.

### 7.4 Ganar el global COMMAND como última acción
Inmediatamente antes del comando, desde el **socket-lector**:
```
NEW_SERVER inst 1 (a (node,0xFFFFFFFE)=QRTR_PORT_CTRL):
04 00 00 00  01 10 00 00  01 00 00 00  <node u32 LE>  <port u32 LE>
```
Esto ejecuta `0xc0d83018/20` → `0xc8c2d870/74` = nuestro par. **FACT.**

### 7.5 Enviar el comando y leer en el MISMO socket
```
sendto(FTM TECH_ENTER)  desde el socket-lector
recvfrom  ≥10–15 s (FTM es asíncrona). No cerrar el socket.
```

### 7.6 Orden exacto resumido
```
0) stop ModemManager / cerrar /dev/diag del sistema
1) (publicado) CNTL(0), DATA(2), DCI(4)
2) CNTL: FEATURE(8)  -> gate 0xc92e43e0, kick 0xc0d68168
3) CNTL: DIAGID(0x21)-> gate 0xc92e4754 bit0
4) CNTL: TX_MODE(0x11 str1 RT) -> DRAIN + RESUME allow_flow
5) CNTL: DIAG_CTRL re-registro de canal (r6=4/r20=17, node/port nuestros)  <<< RE-CABLEO (Camino B)
6) NEW_SERVER inst 1 desde socket-lector -> gana global 0xc8c2d870/74
7) sendto(TECH_ENTER) desde socket-lector
8) recvfrom >=10-15 s en el mismo socket
```
Confiabilidad: pasos 2–4 y 6 son FACT y ya te funcionan post-reboot; el **paso 5** es el que faltaba y es lo que vuelve reproducible el "una vez funcionó". Sin el paso 5, dependés de que MM/kernel disparen el Camino B por casualidad (lo no-reproducible).

### 7.7 Verificación en vivo (para cerrar el UNKNOWN del paso 5)
- Trazar/dumpear el `sendto` del modem tras el comando y comparar el **port destino** con: (i) tu cmd-peer, (ii) tu DATA, (iii) MM. Si es tu cmd-peer → el Camino B funcionó.
- Si tras 2–4+6 (sin 5) no llega, y tras 5 sí llega → confirma que el re-cableo es el paso 5 (Camino B). 
- Si aún no llega con 5, capturar el ctrl-msg de registro que emite el kernel diag y ajustar bytes de r6=4/r20=17.

---

## 8. FACT / INFERENCE / UNKNOWN (cierre con VAs)

### FACT
- `connected 0xc95089a0`: **2 refs totales** — lectura `0xc0d5f5a8`, escritura **sólo a 1** `0xc0d37614`. Sin escritor a 0 en ninguna imagen. Gate `0xc0d5f5b0`.
- `0xc0d81af8` con **2 callers**: `0xc0d375ec` (gateado por connected) y `0xc0d80bb8` (NO gateado).
- Camino B: `0xc0d7fcdc` (parser DIAG_CTRL) → r6=4 (`0xc35cdfec[3]=0xc0d8005c`) → r20=17 (`0xc35ce044[16]=0xc0d80234`) → `0xc0d80290/98` → `0xc0d80ba4` → `0xc0d80bb8: call 0xc0d81af8`. Tablas decodificadas de `modem_full.elf` seg21 (va 0xc3553000, off 0x01a11000).
- Global peer COMMAND `0xc8c2d870(node)/0xc8c2d874(port)`: escrito **sólo** e **incondicional** en NEW_SERVER `0xc0d83018/20`; sin path que lo ponga 0.
- Descriptor COMMAND `0xcb93f380`: `io_type +0x0`, `port_num +0x14`=0x1001, `allow_flow +0x84`=`0xcb93f404` (init 1 `0xc0d8b0e8`, STOP→0 `0xc0d373ac`, RESUME→1 `0xc0d565b0`). Transmit exige io_type==2 && allow_flow!=0 (`0xc0d56558`).
- Mux respuesta: `rsp+0x20==0`→COMMAND(1), `!=0`→DATA(2) (`0xc0d55d54..60`); `rsp+0x20` = global `0xc92e477c` (`0xc0d56278`).
- NEW_SERVER handler `0xc0d82fc0`: valida inst<=5 (`0xc0d82fd8`); llama `0xc0d5f5a4` (no-op si connected).
- QRTR ctrl pump `0xc0d83048`: lee `0xc8c2d86e`, node/port global; ruta a `0xc0d82fc0`/`0xc0d93d80`. **No** resetea connected ni global ante DEL_CLIENT.
- Kick DRAIN: FEATURE/DIAGID/TX_MODE → `0xc0d68168` → `0xc0d696bc` (`0xc92e91a0 |= sig`).

### INFERENCE
- `r6=4/r20=17` = operación de (re)apertura/asociación de canal COMMAND a un peer (equivalente al re-cableo de boot, invocable por DIAG_CTRL). Es lo que dispara el kernel/MM al conectarse; explica el éxito único no-reproducible.
- Matar MM es necesario (no re-pisa global / no consume rsp / puede inducir re-registro) pero no suficiente; el re-cableo hay que forzarlo (paso 5).
- TECH_ENTER directa → `0xc92e477c==0` → respuesta por COMMAND (mask 1).

### UNKNOWN (sólo en vivo)
- **Encoding byte-exacto del DIAG_CTRL que el parser `0xc0d7fcdc` decodifica como r6=4/r20=17** (armado por caller indirecto + scanf `0xc0a0420c`; sin puntero estático). Capturar trazando el registro de canal del kernel diag / libdiag `diag_register`.
- Valor de `0xc8c2d870/74` en el instante del drain (dump QRTR / trazar el `sendto`).
- Si `allow_flow` COMMAND quedó en 0 por un STOP de la sesión vieja (si sí, el TX_MODE/RESUME del paso 4 lo re-sube).
- Si el kernel `/dev/diag` es el peer COMMAND estable del sistema (en cuyo caso la respuesta le llega a él y conviene leer por `/dev/diag`).
