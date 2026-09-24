# DIAG response routing en el modem SM6375 (Moto G82 5G) — por qué la respuesta de TECH_ENTER (subsys FTM) no llega al socket QRTR

Build: **MPSS.HI.4.3.4-00494-MANNAR_GEN_PACK-1.24452.133**
Imagen: `/tmp/modemre/clade_dec_full.bin` (código paginado 0xd8xxxxxx) + segmentos crudos `modem.bNN` (código no comprimido en 0xc0xxxxxx/0xc1xxxxxx, rodata b21 en 0xc3553000).
Disasm de referencia: `/tmp/modemre/_dis_b13.txt` (= modem_full.elf completo, cubre las funciones DIAG en 0xc0d3xxxx–0xc10xxxxx).

Leyenda:
- **FACT** = evidencia estática directa (bytes/desensamblado en esta imagen).
- **INFERENCE** = deducción sobre arquitectura DIAG de Qualcomm (modelo estándar diagbuf/diagcomm), consistente con la evidencia.
- **UNKNOWN** = no determinable estático sin sondeo en vivo.

================================================================================
## 0. RESUMEN EJECUTIVO (leer primero)
================================================================================

**El handle @0x06 NO es "por dónde responder". Es una red herring.** La ruta
de la respuesta DIAG **no depende del handle @0x06 ni de un vtable de sesión**.
Mi re-análisis del disasm corrige la hipótesis de partida:

1. **`0xd8062414` NO es "el lookup de la tabla de sesiones/canales".** Es un
   **thunk de tail-call** (`immext; jump 0xc0988ef4`) a un **asignador de
   memoria/handle genérico** (usa `framekey`/canary y `memw_locked`). El "handle
   @0x06" es el `subsys_cmd_code`/id interno del paquete FTM, no un stream_id ni
   un puerto QRTR. **FACT** (disasm §1).

2. **`0xd816d794` (el `r2=memw(r0+#4); callr r2`) NO pertenece al mismo camino
   que `0xd816d790`.** `0xd816d790` es `r0=r22 ; jump 0xc0913ad8`, y
   `0xc0913ad8` es el **epílogo compartido de restauración de registros +
   return** (una fila de `memd(r30+#-…)=…; dealloc_return`). O sea, ese `jump`
   es simplemente el RETURN de la función. `0xd816d794` es **el prólogo de la
   FUNCIÓN SIGUIENTE** (empieza con `allocframe`). El "dispatch por vtable"
   observado no es el routing de la respuesta DIAG. **FACT** (disasm §2).

3. **El routing REAL de la respuesta DIAG es el pipeline estándar de Qualcomm**:
   `diagpkt_(subsys_)alloc` → `diagpkt_commit` → **diagbuffer/diag drain** →
   `diagcomm_io_transmit` → `diagcomm_io_socket_send` → **QRTR sendto**. La
   respuesta se **DRENA al canal DIAG_DATA**, no al socket cliente que envió el
   comando, y **sólo si `allow_flow` está habilitado para ese canal** (lo cual
   requiere que el AP complete el handshake de control DIAG). **FACT** de los
   nombres/estructuras (§3–§5); el "va a DATA, no al cliente" es **INFERENCE
   fuerte** por el modelo diagbuf + evidencia de `diagcomm_io_transmit`.

4. **Por qué 0x00/0x7c SÍ y TECH_ENTER NO**: no es que uno vuelva al cliente y
   el otro no — **ambos** drenan por DIAG_DATA. La diferencia está en **cuándo**
   se genera y drena la respuesta y **si el canal DATA está drenando en ese
   momento** (buffering/real-time mode + allow_flow). Los apps 0x00/0x7c
   responden inmediato dentro del mismo contexto de comando; el subsys FTM
   `diagpkt_subsys_alloc` deja la respuesta en el **diagbuffer** que se drena
   asíncronamente por el thread de DATA, y si DATA no está "abierto/con
   allow_flow" para tu peer QRTR, **la respuesta se queda en el buffer y nunca
   sale** (o sale por un canal que no estás escuchando). **INFERENCE fuerte**
   (§6), con la evidencia de `diagpkt_process_ctrl_msg: Tx Mode=%d stream_id=%d`
   y `diagcomm_io_transmit: allow_flow=%d ...`.

5. **ACCIÓN CONCRETA** (§8): la respuesta **va al canal DIAG_DATA (channel_type
   DATA, socket QRTR de DATA)**, no a tu socket cliente ni al CMD. Debes:
   **(b) escuchar/servir el canal DATA correctamente Y hacer que el modem lo
   marque `allow_flow`** completando el handshake de control DIAG (features +
   set real-time/tx-mode) por el canal **DIAG_CNTL**. La respuesta llega por el
   **socket de DATA (tu servicio inst 2)**, NO por el socket cliente del CMD.
   El handle @0x06 debe ser el que devolvió la fase previa (o **0** si es la
   primera sesión); no cambia el destino de la respuesta.

================================================================================
## 1. El "handle @0x06" y `0xd8062414` — qué es realmente (FACT)
================================================================================

### 1.1 El cuerpo del comando (0xd816d1e8) lee handle @0x06 y llama al thunk
```
d816d1e8: memw(r29+#0x0) = #0x0
d816d1ec: r18 = add(r21,#0xa)                  ; r21 = base del paquete
d816d1f0: r2 = memub(r21+#0xc); r3 = memub(r21+#0xd)
d816d1f4: r2 |= asl(r3,#0x8)
d816d1f8: r4 = memub(r21+#0xb); r5 = memub(r21+#0xa)
d816d1fc: r5 |= asl(r4,#0x8)
d816d200: r5 |= asl(r2,#0x10)
d816d204: if (cmp.eq(r5.new,#0x0)) jump 0xd816d2c8   ; valida campo @0x0a..0x0d
d816d208: r1 = #0x17                                 ; SSID = 0x17 (FTM)
d816d20c: r0 = memub(r21+#0x6); r2 = memub(r21+#0x7) ; <<< handle = u16 LE @0x06
d816d210: r0 |= asl(r2,#0x8)
d816d214: call 0xd8062414                            ; lookup(handle)   r0=handle,r1=0x17
d816d218: p0 = cmp.eq(r0,#0x0); if (p0.new) jump 0xd816d2b0   ; if ret==0 -> error
d816d21c: r19 = r0; r2 = memb(r0+#0x0)               ; r19 = objeto devuelto
```
**FACT.** El handle es el **u16 LE en el offset 6** del paquete FTM. En tu
frame `4b 0b 14 00 5a 03 <handle@0x06> 00 00 27 00 0d 00 …`, ese campo es lo que
va a `0xd8062414`.

### 1.2 `0xd8062414` es un THUNK, no el lookup real
```
d8062410: r0 = #0x2a
d8062414: immext(#0xe8926ac0); jump 0xc0988ef4    ; tail-call lejano (needs immext)
d806241c: nop
```
`offset = 0xc0988ef4 - 0xd8062418 = -0x176d9524` → far jump; el `immext` lo
resuelve. **FACT.**

### 1.3 `0xc0988ef4` es un asignador/hash genérico (NO tabla de sesiones DIAG)
Desde el segmento crudo `modem.b05` (VA 0xc0980000, off 0x8ef4):
```
c0988ef4: p0 = cmp.eq(r0,#0x0); r16 = #0x0; allocframe(#0x18)
c0988f14: call 0xc0988ee0                       ; helper que lee gp/config byte
c0988f20: p0 = cmp.gtu(r17,##0x3fff)            ; límite 0x3fff (índice/máscara)
c0988f3c: call 0xc0989590
c0988f4c: r3 = framekey                          ; <<< canary/stack-key => hash
c0988f5c: r18 = xor(r2,r3)
c0988fa8: r3 = memw_locked(r2)                   ; <<< incremento atómico de refcount
c0988fb0: memw_locked(r2,p0) = r3
c0988fb8: r2 = extractu(r3,#0xa,#0x0)            ; 10-bit index (tabla de 0x400)
c0988fc4: r3 = +mpyi(r2,#0xc)                    ; entradas de 0xc bytes
c0988fcc: r17 = add(r3,##...)                    ; base de una tabla de descriptores
c0988fd4: memw(r17+#0x4) = r18                   ; guarda hash en la entrada
```
Esto es un **registro/tabla de handles genérica** (con `memw_locked` para
refcount, `framekey` para hashing y una tabla de 0xc-byte-entries indexada por
10 bits). **NO** contiene node/port QRTR ni un "send-response del canal".
Devuelve un **objeto opaco** cuyo byte @0x0 el cuerpo compara con `#-0x80`
(`0x80`, un tag de tipo), no un vtable de transporte. **FACT.**

**Conclusión §1:** el handle @0x06 identifica un **objeto interno de la sesión
FTM/subsys** (contexto del comando), no "por dónde responder". Su valor
correcto es **el que la fase previa te haya dado**, o **0** si el path acepta
handle 0 (tú observaste que con 0 o 1 no da error). **No cambia el destino QRTR
de la respuesta.** **FACT + INFERENCE.**

================================================================================
## 2. `0xd816d790`/`0xd816d794` — NO es un vtable de routing (FACT)
================================================================================

Contexto completo (loop d816d6bc..d816d778, luego commit):
```
d816d748: r1:0 = combine(r21,r24)
d816d74c: callr r16                       ; llamada indirecta (handler del sub)
d816d754: r22 = r0                        ; r22 = resultado del handler
...
d816d77c: p0 = cmp.eq(r22,#0x0); if (p0.new) jump 0xd816d690
d816d780: call 0xd80d77a8
d816d788: r1:0 = combine(r22, ##...)
d816d78c: immext(#0xe87a6340)
d816d790: r0 = r22 ; jump 0xc0913ad8       ; <<< tail-call: RETURN por epílogo compartido
--------- (fin de la función) ---------
d816d794: p0 = cmp.eq(r0,#0x0); if (p0.new) jump 0xd816d7a4
          memd(r29+#-0x10)=r17:16; allocframe(#0x8)   ; <<< PRÓLOGO de OTRA función
d816d7b0: r16 = r2; r2 = memw(r0+#0x4)
d816d7b4: r1 = zxth(r16)
d816d7b8: callr r2                          ; vtable de OTRO objeto (no la rsp DIAG)
```
`0xc0913ad8` (desde `modem.b04`) es una batería de:
```
c0913ad8: r27:26 = memd(r30+#-0x30)
c0913adc: r25:24 = memd(r30+#-0x28)
...
c0913af0: dealloc_return
```
= **epílogo/trampolín compartido de restauración de registros** (usado como
tail-call de RETURN por MUCHAS funciones: también aparece en d8151364,
d81514c8, d8152c40…). **FACT.**

Por lo tanto:
- `d816d790` = **el return** del handler FTM (devuelve r22).
- `d816d794` = **función distinta**; su `callr memw(r0+4)` es un vtable de otro
  objeto (no es el "send response del canal de la sesión del handle"). La
  premisa "la respuesta se rutea según un objeto que depende del handle @0x06"
  **no se sostiene** en el disasm. **FACT.**

================================================================================
## 3. El transporte DIAG real del modem (FACT — nombres y funciones)
================================================================================

En rodata b21 (`__func__`/`__FILE__`) y en el código b13 aparece el stack DIAG
estándar de Qualcomm:

| Símbolo (rodata b21)                    | VA string   | Rol |
|-----------------------------------------|-------------|-----|
| `diagpkt.c / diagbuf.c`                  | 0xc3563888  | core |
| `diagpkt_alloc`                          | 0xc35ca... (0x770ee) | alloc rsp apps |
| `diagpkt_subsys_alloc / _v2`             | 0xc35ab705 / 0x771..| alloc rsp subsys (SSID) |
| `diagpkt_commit`                         | 0xc35ca274  | commit al buffer |
| `diagpkt_rsp_send`                       | 0xc35ca363  | envío de respuesta |
| `diagbuf_send_pkt / diagbuf_drain`       | 0xc35cb00d / 0x782fe | drenaje |
| `diagcomm_io_transmit`                   | 0x7a50f     | transmit por canal |
| `diagcomm_io_socket_open/close/send/init`| 0xc35ce4d2… | **transporte SOCKET/QRTR** |
| `diagcomm_status`                        | 0xc35cb6dc  | estado de canal |

**FACT** (todos presentes como strings + referenciados desde código b13).

### 3.1 `diagcomm_io_socket_*` — modelo de canal (FACT)
Format-strings decisivos:
```
0xc35ce4d2  diagcomm_io_socket_open:  Opening Socket - channel_type = %d, io_type = %d, port_num = %d
0xc35ce52b  diagcomm_io_socket_close: closing Socket - channel_type = %d, io_type = %d, port_num = %d
0xc35ce585  diagcomm_io_socket_send:  diag_socket_error = %d
0xc35ce5b5  diagcomm_io_socket_init:  - isSocketsThreadInitialized: %d, channel_type = %d, io_type = %d, port_num = %d
0xc35cd50f  diagcomm_io_transmit: allow_flow = %d, channel_type = %d, io_type = %d, port_num = %d
```
**FACT.** El canal socket se identifica por la tripleta **(channel_type,
io_type, port_num)**. `diagcomm_io_transmit` **está gateado por `allow_flow`**.

### 3.2 La cola/sendto QRTR (FACT)
`diagcomm_io_socket_send` (código en c0d81xxx) construye un mensaje leyendo una
estructura `diag_socket_info` (r16): campos en `+0`, `+1`, `+3`, `+8`, `+0x8c`
y llama a `0xc0d93ed4` (encolado en ring-buffer; head/tail en `+0x10/+0x14`).
El descriptor del mensaje lleva **node** y **port** de destino en `+0x1c`/`+0x20`:
```
c0d93f34: r1:0 = combine(r16,#-0x1)
c0d93f38: r2 = memw(r17+#0x14); r3 = memw(r17+#0x0)
c0d93f3c: r5 = memw(r17+#0x4); r4 = memw(r17+#0x10)
c0d93f40: r7 = memw(r17+#0x18); r6 = memw(r17+#0x8)
c0d93f4c: call 0xc0d95f64                 ; qrtr sendto real
```
**FACT.** El **destino QRTR (node/port) sale de la estructura del canal**, que
el modem rellenó cuando el peer (el AP) se registró/abrió ese canal — **no del
paquete de comando entrante**. **FACT (estructura) + INFERENCE (que node/port
provienen del registro del peer).**

================================================================================
## 4. La tabla de canales DIAG del modem (FACT)
================================================================================

`diagcomm_io_socket_open` (c0d813dc+) recorre e inicializa arrays de descriptores
de canal de **0x28 bytes** cada uno, guardados en globales relativos a `gp`:
```
c0d8145c..c0d81528  loop sobre memw(r16+#0x10)->array  -> gp+0x6998   (canal A: CNTL)
c0d81560..c0d8162c  loop sobre memw(r16+#0x10)->array  -> gp+0x69a0   (canal B: DATA)
c0d81630..           loop sobre memw(r16+#0x8)         -> (canal C: CMD/DCI)
```
Cada entrada guarda punteros de callback (`+0x8`, `+0xc`, `+0x10`, `+0x14`),
un tipo (`memw+0x18 = #9`), tamaño (`+0x1c = #0x84`), etc. **FACT.** Esto es la
**tabla de canales/peers** que el modem crea; cada canal apunta a su peer QRTR.

Los nombres de canal (SMD-legacy y equivalentes socket) en b21:
`DS, DS_CNTL, DIAG, DIAG_CNTL, DIAG_2, DIAG_CNTL_2, DATA1, DATA1_CNTL, DATA2…`,
más `DIAG_CMD`, `DIAG_CMD_BUF`, `DIAG_DCI_CMD_BUF`. **FACT.**

**Modelo (INFERENCE estándar Qualcomm, consistente con lo anterior):**
- **CNTL** (control): handshake de features/máscaras/modo (bidireccional de
  control). Tu servicio "CNTL inst 0".
- **DATA**: **por aquí salen TODAS las respuestas de comando, logs y eventos**.
  Tu servicio "DATA inst 2".
- **CMD**: canal por el que **entran** los comandos (tu cliente al CMD service
  0x1001 inst 1). El CMD es (casi) sólo RX de comandos en el modem.
- **DCI** (inst 4): canal aparte para clientes DCI.

================================================================================
## 5. El gating de la respuesta: `allow_flow` + Tx mode + stream_id (FACT)
================================================================================

Evidencia del handshake de control que habilita el drenaje:
```
0xc35c9e8b  diagpkt_process_ctrl_msg: Received APPS Feature mask = 0x%X
0xc35c9ec7  diagpkt_process_ctrl_msg: Tx Mode = %d for stream_id = %d
0xc35c9d04  diagpkt_process_ctrl_msg: NRT Mode with diag_real_time_mode = %d
0xc35c9d45  diagpkt_process_ctrl_msg: RT  Mode with diag_real_time_mode = %d
0xc35cb6cc  ...diagcomm_status = %d, allow flow(Port1) = %d, allow flow(Port2) = %d, send state = %d...
0xc35cf2ff  Sent drain complete notification stream_id %d, mode %d
0xc35cd50f  diagcomm_io_transmit: allow_flow = %d, channel_type = %d, io_type = %d, port_num = %d
```
**FACT.** El drenaje del diagbuffer al socket **sólo ocurre si**:
1. el canal (DATA) está **abierto** (peer QRTR registrado), y
2. **`allow_flow`** está activo para ese canal/port, lo que el modem fija tras
   recibir por **CNTL** los control-msgs de **feature mask** y **Tx mode /
   real-time mode** que envía el AP-diag cuando "se conecta" como consumidor.

Si `allow_flow == 0` (AP no completó el handshake, o está en modo buffering/NRT
sin drenar), `diagcomm_io_transmit` **no transmite**: la respuesta queda en el
diagbuffer. **INFERENCE fuerte** (es exactamente lo que describen esos strings).

================================================================================
## 6. Por qué 0x00/0x7c responden y TECH_ENTER no (INFERENCE fuerte)
================================================================================

- **Ambos** tipos de respuesta usan el **mismo transporte** (diagbuf → DATA →
  QRTR). No hay un "path directo al socket cliente" en el disasm; el CMD es RX.
- **La diferencia práctica** que produce tu síntoma:
  - `0x00 (version)` y `0x7c (build id)` son **apps-cmd triviales**: el handler
    genera una respuesta **corta e inmediata** y hace `diagpkt_commit`
    típicamente en el **mismo tick**; si tu tooling drena el DATA justo después,
    la ves. Además muchas stacks host-side (y algunas rutas del modem) mandan la
    respuesta de comando "en línea" al **mismo canal por el que llegó** cuando
    es el path apps simple — por eso "vuelve por el socket cliente".
  - `TECH_ENTER` es **subsys FTM**: usa `diagpkt_subsys_alloc(SSID 0x17)`, corre
    lógica RF más pesada y hace el `commit` que **encola en el diagbuffer para
    drenaje ASÍNCRONO por el thread de DATA**. Si el canal **DATA** no está
    "abierto + allow_flow" **para tu peer**, la respuesta **no se drena** (o se
    drena por el DATA "real" del sistema, no por tu socket). Resultado: el
    handler corre (no hay 0x14), pero **no ves respuesta**. **INFERENCE fuerte**,
    apoyada en §5.

- Confirmación de que `diagpkt_subsys_alloc` **no fija destino**: en su código
  (c1048b0c+, con `__func__ = diagpkt_subsys_alloc` @0xc35ab722, y
  `combine(#0x1f,#0x4b)` = longitud/valor 0x4b='K') sólo **reserva buffer del
  pool** y valida args; **no** escribe node/port ni canal. El destino se decide
  **en el drain**, por el estado global de canales. **FACT.**

================================================================================
## 7. A qué node/port QRTR va la respuesta (FACT estructura / UNKNOWN valor)
================================================================================

- La respuesta va al **node/port QRTR guardado en el descriptor del canal
  DIAG_DATA** (campos `+0x1c`/`+0x20` del mensaje, poblados desde
  `diag_socket_info` de DATA). **FACT (mecanismo).**
- **No** va al puerto del cliente que envió el comando (CMD). El CMD sólo se usa
  para **recibir** el comando; el modem **no** guarda tu source-addr del CMD
  como destino de respuestas de subsys. **INFERENCE fuerte.**
- El **node** destino es el del **AP** (node QRTR del apps processor). El
  **port** es el que el AP **publicó/bind-eó para el servicio DATA** cuando
  registró el servicio DIAG DATA por QRTR (tu "DATA inst 2"). El valor numérico
  exacto del port lo asigna QRTR dinámicamente al bind del AP → **UNKNOWN
  estático**, pero es **tu propio port de DATA**, que tú controlas.

================================================================================
## 8. CONCLUSIÓN ACCIONABLE (respuesta directa a a/b/c/d)
================================================================================

**Qué es el handle @0x06:** el `subsys id`/handle interno del contexto FTM
(u16 LE @off 6), resuelto por un **asignador de handles genérico**
(`0xd8062414`→`0xc0988ef4`), **no** un stream_id ni un puerto. Handle **0**
(o el que te devolvió la fase previa) es válido; **no** afecta el destino de la
respuesta. → tu opción **(a) NO es la solución**: cambiar @0x06 no te va a hacer
llegar la respuesta.

**A qué canal va la respuesta:** al **DIAG_DATA** (channel_type=DATA), por el
**socket QRTR del servicio DATA que TÚ sirves (inst 2)**, drenado
asíncronamente. NO al socket cliente del CMD, NO al CNTL. → esto es tu opción
**(d): la respuesta va a un puerto QRTR específico = el port de tu servicio
DATA (inst 2)**, no al socket cliente. **Debes escuchar la respuesta en el
socket del servicio DATA**, no en el socket cliente que usaste para mandar el
comando.

**Qué falta para recibirla — combinación de (b)+(c)+(d):**

1. **(c/CNTL) Completar el handshake de control DIAG por el canal CNTL** para
   que el modem ponga `allow_flow=1` y `Tx Mode`/real-time en el canal DATA:
   - Publicar/registrar bien tu servicio **DIAG_CNTL (inst 0)** y, desde el AP,
     enviar los control-msgs que el modem espera (Feature mask + set real-time
     mode / Tx mode). Sin esto, `diagcomm_io_transmit` ve `allow_flow=0` y **no
     drena** las respuestas subsys. (Evidencia: §5.) Es la causa más probable
     de que TECH_ENTER "corra pero no responda".
   - Fija **real-time mode (RT)**, no NRT/buffering; en NRT el modem acumula y
     no drena hasta "drain".

2. **(d/DATA) Escuchar la respuesta en el socket del servicio DATA (inst 2)**,
   no en el socket cliente del CMD. El modem hace `sendto(node=AP, port=DATA)`.
   Asegúrate de que el **node/port** con que registraste DATA es el que el modem
   aprendió (que el AP haya hecho el `new_server`/bind de DATA **antes** de que
   el modem intente drenar, para que el descriptor de canal tenga node/port
   correctos).

3. **(b) Alternativa/robustez:** dado que el CMD es sólo RX, **no** esperes la
   respuesta por el socket cliente. Si tu stack lo permite, unifica: usa el
   mismo endpoint QRTR para servir DATA y para leer, y manda el comando por CMD
   pero **lee por DATA**. Mandar el comando "desde el socket que sirve DATA" por
   sí solo **no** basta si `allow_flow` no está activo (paso 1).

**Orden de prueba recomendado (accionable):**
1. Registrar servicios QRTR: CNTL(inst0), DATA(inst2), (DCI inst4) **antes** de
   tocar CMD.
2. Enviar por CNTL el control handshake (features + RT/tx-mode) → provoca
   `allow_flow=1` en DATA.
3. Mandar TECH_ENTER por el CMD (inst1) como ya haces (handle @0x06 = 0 está
   bien).
4. **Leer la respuesta en el socket de DATA (inst2)** — ahí saldrá el REPACK/echo.
5. Si sigue sin salir: capturar en el AP un `qrtr` dump para ver si el modem
   hace `sendto` a un port que no estás bindeando (compararlo con el port real
   de tu DATA).

================================================================================
## 9. FACT / INFERENCE / UNKNOWN (cierre honesto)
================================================================================

**FACT (probado en esta imagen):**
- Handle @0x06 = u16 LE off 6; `0xd8062414` es thunk→`0xc0988ef4` = asignador de
  handles genérico (framekey/memw_locked/tabla 0xc-byte), no lookup de canal.
- `0xd816d790`(`jump 0xc0913ad8`) = RETURN por epílogo compartido; `0xd816d794`
  = otra función. No hay "vtable de send-response de sesión" ligado al handle.
- Stack DIAG estándar presente: `diagpkt_(subsys_)alloc`, `diagpkt_commit`,
  `diagbuf_drain`, `diagcomm_io_transmit`, `diagcomm_io_socket_{open,send,…}`.
- Transporte socket identifica canal por (channel_type, io_type, port_num);
  transmit gateado por **allow_flow**; sendto QRTR con node/port del descriptor
  de canal.
- Handshake de control: feature mask + Tx/real-time mode por CNTL controla el
  drenaje (strings `diagpkt_process_ctrl_msg`, `allow flow(Port1/2)`).
- `diagpkt_subsys_alloc` sólo reserva buffer; no fija destino.

**INFERENCE (arquitectura DIAG Qualcomm, consistente con la evidencia):**
- Respuestas de comando (apps y subsys) drenan por **DIAG_DATA**, no por CMD ni
  por el socket cliente.
- El destino node = node QRTR del AP; port = el que el AP bindeó para DATA.
- TECH_ENTER "corre pero no responde" porque el drain de DATA no está habilitado
  (allow_flow / modo) para tu peer, o no estás leyendo por el socket de DATA.

**UNKNOWN (sólo en vivo):**
- Valor numérico del port QRTR de DATA (lo asigna QRTR al bind del AP; es tuyo).
- Secuencia exacta de control-msgs (bytes) que el modem exige por CNTL para
  fijar allow_flow en este build (capturar del diag-router del AP o del kernel
  diagfwd para replicarla).
- Si en NRT hay un "drain" explícito requerido tras cada respuesta.
