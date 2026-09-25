# DIAG handshake SIN reiniciar el modem — re-disparar el feature-mask/DIAGID AP-initiated (SM6375, MPSS.HI.4.3.4)

**Build**: MPSS.HI.4.3.4-00494-MANNAR_GEN_PACK-1.24452.133 · Hexagon/QDSP6 · SM6375 (Moto G82 5G)
**Imágenes**: `_dis_b13.txt` (DIAG core no-comprimido, VA 0xc0d36000+) · `_dis_b10.txt` (0xc0axxxxx) · rodata b21 (VA 0xc3553000+) · `clade_dec_full.bin` (paginado 0xd8xxxxxx via dis.sh).
Todas las VA verificables con `grep -n "^<VA>:" _dis_b13.txt`.

**Leyenda**: **FACT** = leído del disasm (VA citada) · **INFERENCE** = deducción sobre el modelo diagcomm/diagbuf de Qualcomm consistente con la evidencia · **UNKNOWN** = sólo en vivo.

---

## 0. TL;DR — respuesta directa a tu problema

1. **El handshake diag NO va por el CMD (node 0 port 26). Va por un canal CONTROL (CNTL) separado** — otro puerto/servicio QRTR (service DIAG, instancia 0). El modem MANDA su feature mask por CNTL y ESCUCHA la tuya por CNTL. El CMD (inst 1) es sólo RX de comandos; las respuestas salen por DATA (inst 2). **FACT** (§1).

2. **Lo que dispara que el modem MANDE su feature mask / masks / settings NO es el `NEW_SERVER` de QRTR.** El `NEW_SERVER` sólo actualiza node/port destino y, si el modem YA está "connected", **sólo patea el drain** (no re-hace handshake). Por eso tu re-anuncio de `NEW_SERVER` no hizo nada. **FACT** (§2, gate en `diag_send_flush/0xc0d5f5a4` @ 0xc0d5f5b0).

3. **El disparador REAL, AP-initiated, es que el AP MANDE por el canal CNTL el ctrl-msg FEATURE (type 8) y/o DIAGID (type 0x21).** Ambos handlers (0xc0d66f34 y 0xc0d67484) llaman **incondicionalmente** a `0xc0d68168`, que SETEA los signals internos `DIAG_INT_RESEND_CTRL_SIG / RESEND_DATA / DRAIN` (`0xc0d696bc`, bits 0x200000/0x800000/0x8000000 en `0xc92e91a0`). Eso hace que el diag task del modem **re-empuje sus masks/settings hacia TU peer** sin reboot. **FACT** (§3).

4. **Diferencia clave con lo que probaste**: vos empujaste tu feature mask "directo al servicio CMD (node0 port26)". El handler de FEATURE **sólo corre si el ctrl-msg entra por el canal CNTL** (parser `diagpkt_process_ctrl_msg` está enganchado al RX del socket CNTL, NO del CMD). Mandarlo al CMD (port 26) => el modem lo trata como un **comando** (cmd_code=0x08 = DIAG_CTRL... que aquí NO es un cmd válido → BAD_CMD), **no** como ctrl-msg. Por eso no disparó nada. **FACT (parser CNTL) / INFERENCE (que tu envío fue al socket equivocado)**.

5. **La secuencia AP-initiated que SÍ re-hace el handshake sin reboot** (§4): re-mandar por **CNTL** la tripleta FEATURE(8) + DIAGID(0x21) + TX_MODE(0x11). No hace falta que el modem re-mande su feature mask "primero": vos podés setear los gates vos mismo mandando esos ctrl-msgs, porque **el gate de respuestas (0xc0d36c44) sólo mira DOS flags globales** (`0xc92e43e0` feature-recibido y `0xc92e4754`bit0 diagID-recibido) — **ambos los seteás vos** cuando el modem procesa TUS type 8 / type 0x21. **FACT** (§4, §5).

6. **Mínimo para recibir respuestas a comandos FTM sin "re-handshake modem-first"** (§5): (a) FEATURE type 8 por CNTL → set `0xc92e43e0`; (b) DIAGID type 0x21 v1 por CNTL → set `0xc92e4754`bit0; (c) TX_MODE type 0x11 stream_id=1 real-time por CNTL → drain RT; (d) tener publicado tu DATA (inst 2) para que node/port destino estén poblados; (e) leer por DATA. **Las masks (msg/log/event) NO son necesarias para la respuesta directa de un comando FTM** — sólo para F3/logs. **FACT (gate)/INFERENCE (masks no gatean la rsp)**.

---

## 1. Cómo se transporta el handshake: canal CONTROL (CNTL) vs CMD

### 1.1 Los 4 canales QRTR del modem (FACT)
`diagcomm_io_socket_init @ 0xc0d813dc` crea 4 descriptores (gp-relative), y `diagcomm_io_socket_open @ 0xc0d7d388` abre 4 sockets `diag_socket_info` (0xb8B) con **service id DIAG, port 0x1001, instancia 0/1/2/3**:

| canal | gp off | type@+0x18 | instancia QRTR | rol |
|-------|--------|-----------|----------------|-----|
| **CNTL** | gp+0x6998 | 9 | **inst 0** | **HANDSHAKE: feature mask, DIAGID, masks, tx-mode, diagmode** |
| **DATA** | gp+0x69a0 | 9 (io_type==2) | inst 2 | TX de TODAS las respuestas + logs + eventos |
| **CMD**  | gp+0x699c | 3 | inst 1 | **RX de comandos** (aquí mandás TECH_ENTER/FTM) |
| **DCI**  | gp+0x69a4 | 6 | inst 4 | Diag Command Interface |

**FACT** (tabla en `diag_transport_full.md` §1.1, verificada 0xc0d813dc / 0xc0d7d388).

> **El "canal de control" del handshake es un SERVICIO/PUERTO QRTR APARTE del CMD**: es el socket de la **instancia 0 (CNTL)**, service DIAG. NO es node0/port26. El modem manda su feature mask POR el socket CNTL y escucha la tuya POR el socket CNTL. **FACT (canales separados) / INFERENCE (instancia 0 = CNTL; los nombres SMD-legacy `DIAG_CNTL` confirman el rol)**.

### 1.2 El parser de ctrl-msg está enganchado SÓLO al RX de CNTL (FACT)
`diagpkt_process_ctrl_msg @ 0xc0d66264` es el que procesa feature/diagid/masks/txmode. Su jumptable está en **0xc35c9b78** indexada por `(cmd_type - 3)` (0xc0d662f4-0xc0d66300). Este parser se invoca desde el **read-callback del canal CNTL**, no del CMD. El CMD entra a `diagpkt_master_dispatch @ 0xc0d55df8` (dispatch por cmd_code=pkt[0]). **FACT**.

**Consecuencia práctica (tu "kick" fallido)**: si mandás el feature mask (bytes `08 00 00 00 ...`) al **socket CMD (node0 port26)**, el modem lo mete en `diagpkt_master_dispatch` con cmd_code=0x08. 0x08 no está registrado como comando legacy → responde **DIAG_BAD_CMD (0x13)** o lo descarta; **nunca** llega a `diagpkt_process_ctrl_msg` ni setea `0xc92e43e0`. **Para setear los gates hay que mandar el ctrl-msg por el socket de la instancia CNTL (inst 0).** **FACT (dos parsers distintos) / INFERENCE (destino de tu kick)**.

---

## 2. Qué NO dispara el feature-mask del modem: el NEW_SERVER (por qué tu kick falló)

### 2.1 El handler de NEW_SERVER sólo actualiza node/port + kick de drain (FACT)
QRTR ctrl handler `0xc0d830b4`: `cmp.eq(r1,#0x8)` = NEW_SERVER. Guarda:
```
c0d83018: memw(0xc8c2d870) = r17   ; NODE del AP
c0d83020: memb(0xc8c2d874) = r18   ; PORT del AP
c0d83024: call 0xc0d5f5a4           ; <<< kick/notify (NO re-handshake)
c0d83028: call 0xc0d65ce8           ; log "diag connected"
```
**FACT** (0xc0d83018/0xc0d83020/0xc0d83024).

### 2.2 `0xc0d5f5a4` — el gate que hace que el 2º+ NEW_SERVER sea un no-op de handshake (FACT)
```
c0d5f5a4: r2 = memub(0xc95089a0)                 ; <<< flag "diag YA connected"
c0d5f5b0: if (!cmp.gtu(r2,#0)) jump 0xc0d375e4    ; si NO connected -> full connect-init
c0d5f5b4: jumpr r31                                ; <<< si YA connected -> RETURN casi inmediato
          (rama connected: sólo checa 0xc95089a1 y a lo sumo llama 0xc0910fcc = set-signal drain)
```
**FACT.** `0xc95089a0` lo setea `0xc0d375e4` (la 1ª conexión): `0xc0d37614: memb(0xc95089a0)=1`.

> **Por eso tu re-anuncio de NEW_SERVER no re-disparó el feature mask**: cuando el modem ya está "connected" (`0xc95089a0!=0`, que quedó en 1 desde el boot/handshake original), un nuevo NEW_SERVER **sólo re-escribe node/port y patea el drain** (`0xc0910fcc`), pero **no re-ejecuta la secuencia de open ni re-envía el feature mask**. El path modem-first (`0xc0d375e4`) sólo corre UNA vez por sesión de diag. **FACT**.

### 2.3 El feature-mask modem-first es reactivo, no espontáneo (FACT)
No hay una string "Sending Feature mask" en el modem: el modem **no manda su feature mask "porque sí" al ver un server**. En el flujo modem-first del boot, el modem manda su feature mask cuando **el AP-diag abre el control channel y el modem procesa el primer intercambio**; en la práctica de esta build el envío de masks/settings del modem se dispara desde `0xc0d68168` (§3), que es llamado por los handlers de FEATURE/DIAGID **que el AP manda**. Es decir: **incluso el "modem-first" está encadenado a que el AP mande su feature mask primero por CNTL**. **FACT (0xc0d68168 llamado desde 0xc0d670dc y 0xc0d674d4) / INFERENCE (que en boot el AP-diag lo manda y por eso "parece" modem-first)**.

---

## 3. El disparador AP-initiated real: FEATURE(8)/DIAGID(0x21) → `0xc0d68168` → RESEND signals

### 3.1 Handler FEATURE (ctrl type 8) @ 0xc0d66f34 (FACT)
```
c0d66f5c..: log "Received APPS Feature mask = 0x%X" (str 0xc35c9e8b)
c0d66fa8:  memb(0xc92e43e0) = 1        ; <<< GATE #2 feature-recibido = 1
c0d66fb0:  call 0xc0d677f8             ; diagID/init helper (thunk d80e7210)
...
c0d670c4:  r2 = memb(0xc92e43e0)
c0d670cc:  if (r2==0) jump 0xc0d670dc  ; (1ª vez) -> directo a resend
c0d670d0:  call 0xc0d5f5a4             ; (ya connected) kick drain
c0d670dc:  call 0xc0d68168             ; <<< SIEMPRE: dispara RESEND de masks/settings
```
**FACT.** => Mandar FEATURE(8) por CNTL **siempre** llama `0xc0d68168`, esté o no ya connected.

### 3.2 Handler DIAGID (ctrl type 0x21) @ 0xc0d67484 (FACT)
```
c0d67494: valida version == 1 (memub payload+0x8..)   ; cmp.eq(r5,#1)
c0d674d4: call 0xc0d68168             ; <<< dispara RESEND de masks/settings
c0d674d8: call 0xc0d82100             ; registra el diagID/proceso
```
**FACT.** El flag de gate #1 (`0xc92e4754` bit0) lo setea el path de DIAGID (setter 0xc0d36058 / lectura del gate 0xc0d36c48). => Mandar DIAGID(0x21) por CNTL setea el gate #1 y dispara resend.

### 3.3 `0xc0d68168` — la función que re-empuja el estado del modem al peer (FACT)
```
c0d68168: call 0xc0d696bc            ; set-signal bit(0x1) (drain)
c0d68198: call 0xc0d696bc  con 0x200000   ; DIAG_INT_RESEND_CTRL_SIG
c0d6819c: call 0xc0d696bc  con 0x800000   ; RESEND_DATA
c0d681e0: call 0xc0d696bc  con 0x8000000  ; DRAIN / flush
```
`0xc0d696bc` hace `memw(0xc92e91a0) |= sig` y despierta el diag task (0xc09875d4). Los bits corresponden a los signals de `diag_handle_internal_sigs` (strings 0xc35caad8 `DIAG_INT_RESEND_CTRL_SIG`, 0xc35cabaa `RESEND_DATA_SIG`, 0xc35cac22 `RESEND_CMD_SIG`). **FACT** (0xc0d696bc @ 52600; bits en 0xc0d68168).

> **Conclusión §3**: existe un mecanismo AP-initiated para que el modem re-procese/re-empuje su estado de control **sin reboot**: **mandar FEATURE(8) y/o DIAGID(0x21) por el socket CNTL (inst 0)**. Eso llama `0xc0d68168` → setea RESEND_CTRL/DATA/DRAIN → el diag task re-emite masks/settings y drena hacia el node/port que aprendió de tu NEW_SERVER. **FACT.**

---

## 4. "diag hello" / open explícito — ¿hay un ctrl-msg que fuerce al modem a re-emitir feature+DIAGID?

### 4.1 No hay un "feature mask request" del AP hacia el modem (FACT)
No existe en esta build un ctrl-msg tipo "dame tu feature mask" que fuerce al modem a **enviar su propia** feature mask on demand. El jumptable de `diagpkt_process_ctrl_msg` (@0xc35c9b78, index type-3) cubre: 3=DIAGMODE, 8=FEATURE, 0x11=TX_MODE, 0x12=msg-mask, 0x21=DIAGID, APPS_BUFFERING_MODE, DBUF_TX_PERM, CLIENT_SETTINGS, PASSTHRU_CFG. **Ninguno es "request feature mask".** **FACT (revisión de la jumptable/strings 0xc35c9d04..0xc35ca0xx)**.

### 4.2 Pero NO lo necesitás — el "diag hello" efectivo es que VOS mandes FEATURE+DIAGID (FACT)
El gate de respuestas (`diagpkt_rsp_send @ 0xc0d36c44`) sólo exige **dos flags globales** que **vos seteás** cuando el modem procesa TUS ctrl-msgs:
```
c0d36c48: r2 = memw(0xc92e4754); if(!tstbit(r2,#0)) skip   ; DIAGID-recibido (lo seteás con type 0x21)
c0d36c64: r2 = memb(0xc92e43e0)                             ; FEATURE-recibido (lo seteás con type 8)
c0d36c6c: if (r2==0) jump 0xc0d58b9c                        ; "before feature mask OR diagID" (no encola)
```
**FACT.** El modem **no** necesita re-mandarte SU feature mask para que las respuestas fluyan: **el gate mira flags que ponés vos**. El "handshake" que te falta re-hacer es que el modem **re-registre tu peer y ponga los gates**; eso lo lográs re-mandando FEATURE+DIAGID+TX_MODE por CNTL (que además llama `0xc0d68168` para re-empujar masks). **FACT + INFERENCE.**

### 4.3 Canal-open / DIAG_COMM_OPEN_SIG (FACT, contexto)
`diag_handle_sigs @ 0xc0d56920` despacha por bits de signal: bit 0xc → open/reset de canal (`0xc0d7d2d0`+`0xc0d7bd98`+`0xc0d7c064`), bit 0xb → DCI open. Estos NO son AP-triggerables directamente (son internos, se levantan cuando el socket QRTR del modem detecta open); pero el efecto de "re-open" lo conseguís con el resend de §3. **FACT (bits) / INFERENCE (no AP-triggerables salvo vía NEW_SERVER que ya vimos que sólo kickea)**.

---

## 5. SECUENCIA EXACTA de bytes AP-initiated (sin reboot)

Todos los ctrl-msg van por el **socket de la instancia CNTL (service DIAG, inst 0)** — NO por CMD (node0 port26). Header LE `{u32 cmd_type; u32 data_len; payload}`. Parser 0xc0d66264, jumptable 0xc35c9b78 index=type-3. **FACT**.

**Precondición**: tener publicados por QRTR tus servers DIAG **CNTL(inst0)**, **DATA(inst2)**, **DCI(inst4)** (service 0x1001). Esto ya lo hace tu tool; el modem aprende node/port en `0xc8c2d870/74` del NEW_SERVER. Si ya estaban publicados y el modem sigue "connected", basta con re-mandar la tripleta de abajo por CNTL.

### Paso 1 — FEATURE mask (type 8) — set `0xc92e43e0`, llama 0xc0d68168
```
08 00 00 00  04 00 00 00  1B FE F7 00
| type=8   | len=4     | feature_mask LE (0x00F7FE1B) |
```
El handler lee `u32 @ payload` (0xc0d66f3c) y loguea "Received APPS Feature mask = 0x%X". Usá **la misma mask que mandás en el handshake que SÍ funciona tras reboot** (vos observaste `f7fe1b` del modem; mandá la tuya de vuelta con el bit de diagID-support activo). **FACT (formato) / INFERENCE (valor)**.

### Paso 2 — DIAGID (type 0x21=33), version=1 — set `0xc92e4754`bit0, llama 0xc0d68168
```
21 00 00 00  <len u32 LE>  01 00 00 00  <diag_id u32>  <process_name[] NUL-term>
| type=0x21|              | version=1 | diag_id     | "root_pd"/"wlan_pd"\0 ...
```
Handler 0xc0d67484 valida `version==1` (memub payload+0x8). **Mandá el MISMO DIAGID (root_pd y wlan_pd) que mandás en el flujo post-reboot que funciona** — el layout exacto que valida esta build es el que ya usás con éxito. Sin este mensaje, gate #1 bloquea toda respuesta de comando. **FACT (gate + version) / UNKNOWN (layout byte-exacto: reusá el que ya te funciona)**.

### Paso 3 — TX_MODE (type 0x11=17), stream_id=1, real-time — drain RT
```
11 00 00 00  06 00 00 00  01 00 00 00  01 01
| type=0x11| len=6     | num_streams=1 | id=1 | mode=1(RT)
```
Handler 0xc0d6711c → setter 0xc0d7dc78 (escribe `stream_obj+0xaf=mode`, match `+0xae==stream_id`, dispara flush 0xc0d696bc). String de verificación 0xc35c9ec7 "Tx Mode = %d for stream_id = %d". **FACT (mecanismo) / UNKNOWN (1 vs 0; probá 1)**.

### Paso 4 (opcional) — masks msg/log/event (SÓLO si querés F3, NO para la rsp de comando)
Como ya hacés. No gatean la respuesta directa de comando (§6).

### Paso 5 — Comandar por CMD (inst 1) y LEER por DATA (inst 2)
Tu TECH_ENTER/FTM por CMD; la respuesta drena por DATA al node/port aprendido. **FACT**.

**Verificación en vivo** (capturando CNTL/DATA en el AP):
- Tras Paso 1 → F3 "Received APPS Feature mask = 0x%X" (0xc35c9e8b) => gate #2 OK.
- Tras Paso 3 → F3 "Tx Mode = %d for stream_id = %d" (0xc35c9ec7) => stream RT.
- Si al comandar aparece "Attempt to send response before feature mask OR diagID" (0xc35ca363) => faltó gate #1 (DIAGID) o #2 (feature) — revisá que fueron por CNTL, no por CMD.
- Si "diagcomm_io_transmit: allow_flow = 0" (0xc35cd50f) => hubo STOP (0xF4/0xF7); mandá RESUME.

---

## 6. Alternativa: mínimo para recibir la respuesta a un comando FTM SIN handshake completo de masks

### 6.1 ¿La respuesta a comando FTM (subsys 0x4b/SSID 0x17) depende de las masks? — NO (FACT/INFERENCE)
- La respuesta de un comando (apps y subsys) sale por `diagpkt_(subsys_)alloc → diagpkt_commit/diagpkt_rsp_send → diagbuf_send_pkt → diagcomm_io_transmit → sendto`. **FACT** (`diag_transport_full.md` §2, `map_diag_core.md` §4).
- El **único gate** de `diagpkt_rsp_send` (0xc0d36c44) es **feature-recibido (`0xc92e43e0`) + diagID-recibido (`0xc92e4754`bit0)**. **No mira msg/log/event mask.** **FACT**.
- El drain (`diagcomm_io_transmit` 0xc0d56520) exige: canal DATA con **io_type==2**, **allow_flow(+0x84)!=0**, buffer!=0, y **node/port destino poblados** (+0x8c/+0x90, del NEW_SERVER). **FACT**.

**=> El MÍNIMO para que una respuesta directa a comando FTM llegue a tu socket DATA es:**
1. **FEATURE(8)** por CNTL → `0xc92e43e0=1`. **(gate #2)** — **FACT**
2. **DIAGID(0x21) v1** por CNTL → `0xc92e4754`bit0. **(gate #1)** — **FACT**
3. **TX_MODE(0x11) stream_id=1 real-time** por CNTL → drain RT del stream de comando/respuesta. **FACT (mecanismo) / INFERENCE (que stream 1 = RT de rsp)**
4. **DATA (inst 2) publicado** (node/port destino ya aprendidos del NEW_SERVER; +0x94==0). **FACT**
5. **No estar en STOP** (allow_flow!=0). **FACT**
6. **Leer por DATA, no por CMD.** **FACT**

**Las masks (msg/log/event) NO hacen falta** para la respuesta directa del comando FTM — sólo para los F3/logs (que además no cruzan el gate y drenan igual). **FACT (gate no mira masks) / INFERENCE (masks sólo afectan F3)**.

### 6.2 DIAGMODE (type 3) — opcional
Sólo ajusta buffering global (0xc0d66348, exige data_len=0x24, real_time@+0xc); NO es gate. Recomendado real_time=1 para no acumular. **FACT**.

### 6.3 ¿Se puede "attach" a un diag ya abierto y leer las masks que el modem ya tiene? (respuesta honesta)
- **No hay un comando DIAG "dump current masks"** registrado en este PD (§ `map_diag_core.md` §6: sin peek/NV/EFS). No podés leer las masks vivas del modem por DIAG. **FACT**.
- Pero **no lo necesitás**: para recibir la respuesta de un comando FTM sólo hacen falta los 6 puntos de §6.1, que **vos seteás/re-seteás** mandando ctrl-msgs por CNTL. El estado RF vivo del modem (lo que querés preservar) **no se toca** con esto: FEATURE/DIAGID/TX_MODE sólo afectan el pipeline de transporte diag, **no** re-inicializan RF. **FACT (los handlers 0xc0d66f34/67484/6711c no tocan RF) / INFERENCE (preserva RF)**.

---

## 7. Por qué tu "kick" no funcionó y qué cambiar (resumen accionable)

| Lo que hiciste | Por qué falló (FACT) | Qué hacer |
|----------------|----------------------|-----------|
| Re-anunciar **NEW_SERVER** por CNTL de cada instancia | El modem ya estaba `connected` (`0xc95089a0!=0`) → `0xc0d5f5a4` sólo re-escribe node/port y kickea drain; **no re-hace handshake ni re-emite feature mask** (0xc0d5f5b0) | El NEW_SERVER es necesario para poblar node/port, pero **no** dispara el handshake. Mantenelo, pero además mandá §7-fila-2 |
| Empujar tu feature mask **al servicio CMD (node0 port26)** | El CMD entra a `diagpkt_master_dispatch` (cmd_code=0x08 → BAD_CMD), **NO** a `diagpkt_process_ctrl_msg`; **nunca setea `0xc92e43e0`** | Mandá FEATURE(8) por el socket de la **instancia CNTL (inst 0)**, no por CMD |
| — | Falta el disparo de `0xc0d68168` (resend) | Mandá **FEATURE(8) + DIAGID(0x21) + TX_MODE(0x11) por CNTL** — ambos handlers llaman `0xc0d68168` y setean los gates (§3, §5) |

**Regla de oro**: el handshake diag (feature/diagID/tx-mode/masks) **SIEMPRE por el socket CNTL (inst 0)**; los comandos por CMD (inst 1); leer respuestas por DATA (inst 2). Re-mandar FEATURE+DIAGID+TX_MODE por CNTL re-arma los gates y patea el drain **sin reboot**, preservando el estado RF. **FACT + INFERENCE.**

---

## 8. FACT / INFERENCE / UNKNOWN — cierre con VAs

### FACT (probado en esta imagen)
- 4 canales QRTR: `diagcomm_io_socket_init @ 0xc0d813dc`, `diagcomm_io_socket_open @ 0xc0d7d388`; CNTL=gp+0x6998(inst0), DATA=gp+0x69a0(inst2,io_type2), CMD=gp+0x699c(inst1), DCI=gp+0x69a4(inst4); service DIAG port 0x1001.
- Ctrl-msg parser (handshake) `diagpkt_process_ctrl_msg @ 0xc0d66264`, jumptable 0xc35c9b78 index=(type-3). Enganchado al RX de **CNTL**, distinto del `diagpkt_master_dispatch @ 0xc0d55df8` (RX de **CMD**, dispatch por cmd_code).
- NEW_SERVER handler `0xc0d830b4` → node 0xc8c2d870 / port 0xc8c2d874 (0xc0d83018/20) + call 0xc0d5f5a4 + 0xc0d65ce8.
- `0xc0d5f5a4`: si `memub(0xc95089a0)==0` → full connect-init `0xc0d375e4`; si !=0 (ya connected) → RETURN casi inmediato (sólo kick drain). `0xc95089a0` se setea a 1 en 0xc0d37614.
- FEATURE(type 8) handler `0xc0d66f34`: set `0xc92e43e0=1` (0xc0d66fa8); log 0xc35c9e8b; **call `0xc0d68168`** en 0xc0d670dc (siempre).
- DIAGID(type 0x21) handler `0xc0d67484`: valida version==1; **call `0xc0d68168`** en 0xc0d674d4; registra proceso (0xc0d82100).
- `0xc0d68168`: llama `0xc0d696bc` con bits 0x200000/0x800000/0x8000000 (RESEND_CTRL/RESEND_DATA/DRAIN). `0xc0d696bc @ 0xc0d696bc`: `memw(0xc92e91a0) |= sig` + wake (0xc09875d4).
- TX_MODE(type 0x11) handler `0xc0d6711c` → setter 0xc0d7dc78 (stream_obj+0xaf, flush 0xc0d696bc); string 0xc35c9ec7.
- Gate de respuestas `diagpkt_rsp_send @ 0xc0d36c44`: exige `0xc92e4754`bit0 (DIAGID) + `memb(0xc92e43e0)` (feature); si no → 0xc0d58b9c "before feature mask OR diagID" (str 0xc35ca363). **No mira msg/log/event mask.**
- Drain: `diagbuf_send_pkt @ 0xc0d562dc` (gate +0x94==0) → `diagcomm_io_transmit @ 0xc0d56520` (io_type==2, allow_flow+0x84!=0) → sendto 0xc0d95f64 (node/port +0x8c/+0x90). STOP 0xF4/0xF7 → allow_flow=0 (0xc0d373ac).
- No hay ctrl-msg "request feature mask" en la jumptable; no hay comando DIAG de dump de masks/peek/NV en este PD (`map_diag_core.md` §6).
- `diag_handle_sigs @ 0xc0d56920`: bit 0xc = channel open/reset, bit 0xb = DCI open.

### INFERENCE (modelo Qualcomm, consistente)
- Instancia 0 = CNTL (nombres SMD-legacy `DIAG_CNTL`); stream 1 = RT de comando/respuesta.
- El "modem-first" del boot está encadenado a que el AP-diag mande su feature mask por CNTL (por eso `0xc0d68168` lo llaman los handlers del AP), y "parece" espontáneo.
- FEATURE/DIAGID/TX_MODE no re-inicializan RF → preservan el estado RF vivo.
- Tu kick al CMD (port26) fue interpretado como comando 0x08, no como ctrl-msg.

### UNKNOWN (sólo en vivo / reusar lo que ya funciona)
- Layout byte-exacto del payload DIAGID (type 0x21) que valida esta build — **reusá el que ya te funciona post-reboot** (root_pd/wlan_pd).
- Semántica tx_mode 1 vs 0 en 0xc0d7dc78 (probá 1).
- Valor numérico del port QRTR de tu DATA (lo asigna el bind del AP; es tuyo).
- Feature-bits mínimos que exige el build para abrir el flujo diagID (usá tu mask f7fe1b/la que ya te funciona).
- Si al reintentar sin reboot el modem quedó con allow_flow=0 por un STOP previo (capturar CNTL/DATA; mandar RESUME).

---

## 9. Plan de prueba (sin reboot, preservando RF)

1. **Sin republicar/borrar nada**: dejá tus servers CNTL(0)/DATA(2)/DCI(4) publicados (node/port ya aprendidos por el modem).
2. Por el socket **CNTL (inst 0)** mandá, en orden: **FEATURE(8)** → **DIAGID(0x21) v1 (root_pd y wlan_pd, el layout que ya usás)** → **TX_MODE(0x11) stream1 RT**. (Bytes en §5.) Esto llama `0xc0d68168` (resend) y setea los dos gates.
3. (Opcional) msg-mask SSID 0x17 sólo si querés F3.
4. Mandá TECH_ENTER/FTM por **CMD (inst 1)**.
5. **Leé la respuesta por DATA (inst 2)**.
6. Si no llega: capturá CNTL/DATA en el AP; confirmá F3 0xc35c9e8b (feature) y 0xc35c9ec7 (txmode); si ves 0xc35ca363 revisá que los ctrl-msg fueron por CNTL (no CMD); si ves allow_flow=0 mandá RESUME.

Si tras esto **igual** no drena y sólo funciona con reboot, la causa remanente sería que el modem quedó con `allow_flow=0` (STOP sin GO) o que el descriptor DATA perdió node/port — ambos capturables en vivo con dump QRTR (no determinables estático). **UNKNOWN (en vivo)**.
