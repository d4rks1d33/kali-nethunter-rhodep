# PROTOCOLO DEFINITIVO — socket UNIX entre `pineap` (CLI) y `pineapd` (daemon)
## WiFi Pineapple · resuelto por EJECUCIÓN REAL de ambos binarios bajo qemu-mips

Convención: **[EXE]** = verdad de ejecución (captura en vivo bajo qemu-mips).
**[DIS]** = confirmado por desensamblado autoritativo (`mips-linux-gnu-objdump -m mips:16`,
único decoder que maneja el prefijo EXTEND de MIPS16e).

Artefactos de evidencia en `/tmp/opencode/proto-re/`:
- `recv_*.bin` / `recv_*.hex` — bytes EXACTOS que envía el cliente real, por comando.
- `daemon_strace.txt`, `dstrace.txt` — strace del daemon real (recvfrom con su tamaño).
- `handleNextCommand_CORRECTED.txt` — recv+dispatch reales del server (objdump mips:16).
- `command_table_evidence.txt` — tabla comando→id con hexdump.
- `e2e*.sh` — harness end-to-end (cliente real contra daemon real).

---

## VEREDICTO: 65 vs 4097 → **4097 bytes.** El análisis de "65 bytes" era FALSO.

### Prueba de ejecución (lado servidor) [EXE]
strace del **pineapd real** aceptando una conexión del **pineap real** (`add_ssid`):
```
accept(10,NULL,NULL) = 11
recvfrom(11, buf, 4097, 0, 0, 0) = 4097      <-- lee 4097 en UNA llamada
... (luego escribe pineapple.db-journal: el addSSID llegó a SQLite)
```

### Causa del error "65 bytes" [DIS]
El informe previo de `pineapd` usó un desensamblador MIPS16 propio que **NO decodifica
el prefijo EXTEND (0xf0xx)** de MIPS16e. El valor inmediato real estaba extendido:

| file off | bytes      | decode CORRECTO (objdump mips:16) | decode ERRÓNEO (mips16.py) |
|----------|-----------|-----------------------------------|----------------------------|
| 0x40b824 | `f002 6e01`| `li a2, 4097`   (memset size)     | `li a2, 0x41` (65)         |
| 0x40b85a | `f002 5801`| `sltiu s0, 4097` (loop got<4097)  | `sltiu s0, 0x41`           |
| 0x40b86e | `f002 6e01`| `li a2, 4097`   (recv base count) | `li a2, 0x41`              |
| 0x40b860 | `f1ac 5909`| `sltiu s1, 25001` (max reintentos)| `sltiu s1, 0x3589` (13705) |

`0xf002` es el EXTEND que aporta los bits altos: `0x41 | (0x2<<11)=... ` → 4097 (0x1001).
**El servidor lee 4097 bytes, idéntico a lo que el cliente envía. No hay discrepancia real.**

---

## 1. CONEXIÓN [EXE + DIS]

### Cliente (`pineap`, `openCliComms`)
```c
int fd = socket(AF_UNIX, SOCK_STREAM, 0);    // MIPS: li a1,2 == SOCK_STREAM
struct sockaddr_un addr; memset(&addr,0,sizeof);
addr.sun_family = AF_UNIX;
strcpy(addr.sun_path, "/var/run/pineapd.sock");  // 21 chars
connect(fd, &addr, 110 /* sizeof(sockaddr_un) */);
```
Errores: `Failed to create socket: <err>` / `Failed to connect to socket at path: /var/run/pineapd.sock: <err>` → stderr, exit(1).

### Servidor (`pineapd`, `initComms` 0x40a335) [DIS]
```c
unlink("/var/run/pineapd.sock");
fd = socket(AF_UNIX, SOCK_STREAM|SOCK_CLOEXEC, 0);
setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &one, 4);
bind(fd, &addr /*sun_path=/var/run/pineapd.sock*/, 110);
listen(fd, 10);
```
Confirmado [EXE]: el daemon real crea `srwxr-xr-x /var/run/pineapd.sock` y hace `accept()` no bloqueante en loop (EAGAIN) hasta recibir cliente.

---

## 2. REQUEST — tamaño y layout EXACTO [EXE]

### Tamaño: **4097 bytes FIJOS, SIEMPRE.**
Verificado para los 32 comandos capturados: `total_bytes = 4097` en todos.
```
buf[0]        = PineAPCommand (1 byte, ver tabla §4)
buf[1..4096]  = payload específico, resto RELLENO CON CEROS (memset previo)
send(fd, buf, 4097, 0);     // el cliente SIEMPRE manda 4097
recvfrom(cli, buf, 4097);   // el server SIEMPRE lee 4097 (loop hasta completar)
```
El server: `memset(buf,0,4097)` → loop `recv(buf+got, 4097-got)` mientras `got<4097`,
con `usleep(20)` en EAGAIN y hasta `25001` reintentos. [DIS]

### Endianness: enteros multibyte en **BIG-ENDIAN**. [EXE]

### Layout por familia de comando (offsets ABSOLUTOS dentro del buffer de 4097):

**(a) Comandos simples / sin args** — sólo `buf[0]`:
```
[0]=cmd   (resto = 0)
```

**(b) `run_scan <dur> <freq>`** (cmd 4) [EXE `04 02 0000001e`]:
```
[0]=0x04
[1]=freq (u8)          # 0=2.4GHz,1=5GHz,2=ambos
[2..5]=dur (u32 BE)    # segundos
```

**(c) `deauth <src> <tgt> <ch> <mult>`** (cmd 8) [EXE `08 bbbbbbbbbbbb aaaaaaaaaaaa 06 00000003`]:
```
[0]=0x08
[1..6]=target BSSID (6)   # OJO: orden en wire = target primero
[7..12]=source MAC (6)
[13]=channel (u8)
[14..17]=multiplier (u32 BE)
```
> El server lee channel en `cmd[0x0d]` (=offset 13). [DIS] ✓ coincide.

**(d) `handshake_capture_start <bssid> <ch>`** (cmd 9) [EXE `09 deadbeef0011 06`]:
```
[0]=0x09
[1..6]=bssid (6)
[7]=channel (u8)         # server: setChannel(cmd[0x07])
```

**(e) `add_ssid`/`del_ssid <ssid>`** (cmd 14/15) [EXE `0e 54657374...00`]:
```
[0]=0x0e / 0x0f
[1..]=SSID en ASCII, terminado/relleno con NUL (el server hace strlen-like)
```

**(f) `list_probes <mac>`** (cmd 18) [EXE `12 aabbccddeeff`]:
```
[0]=0x12
[1..6]=MAC (6)          # si es 00:..:00 el server lista todas (rama getProbes())
```

**(g) `inject <file> <ch> <count> <delay>`** (cmd 19) [EXE `13 06 00000003 0000000e ff..`]:
```
[0]=0x13
[1]=channel (u8)
[2..5]=count/multiplier (u32 BE)
[6..9]=frame_len (u32 BE)
[10..]=frame bytes (hex-decodificados del archivo; máx 2048)
```
> **CORRIGE** el informe previo del cliente (que suponía channel@380/count@381/len@385/frame@389).
> El layout REAL es compacto: channel@1, count@2, len@6, frame@10. [EXE]
> **El 4º argumento `delay` NO viaja en el wire** — el cliente lo descarta (0x64 ausente del buffer). [EXE]

**(h) setOpt — cmd 21 (0x15), sub-opción de config** [EXE]:
```
[0]=0x15
[1]=PineAPConfigOpt (u8)   # índice de campo, ver §5
[2..5]=padding = 0
[6..]=data:
   - bool: [6]=0/1
   - int (ap_channel / beacon_interval): u32 BE en [6..9]
   - MAC (set_source/set_target): 6 bytes en [6..11]
```
El server lee: bool=`lbu[6]`; int=`lhu[6]<<16 | lhu[8]`; MAC=6 bytes desde buffer. [DIS]

> **QUIRK confirmado por ejecución** — `ap_channel 11`:
> cliente envía **un solo byte** `0x0b` en offset 6 (`15 09 00000000 0b 00...`).
> server computa `lhu[6]<<16 | lhu[8] = 0x0b00<<16 = 0x0B000000 = 184549376`
> y lo guarda así en `/tmp/pineap.conf` (`ap_channel = 184549376`). [EXE, verificado en el .conf]
> En cambio `beacon_interval` sí envía u32 completo (`00002328`→9000). Para compat byte-a-byte
> en aarch64 hay que reproducir ESTE comportamiento exacto (canal como byte en off 6).

---

## 3. RESPONSE [EXE + DIS]

Sólo comandos con `printResponse` leen respuesta: **`get_status`, `list_ssids`, `list_probes`**.
```c
char c;
while (recv(fd,&c,1,0) > 0) { if (c=='\0') break; stdout.put(c); }
close(fd);
```
El server termina CADA respuesta con un byte NUL (`\0`). [DIS 0x40b90e send("\0",1)]

Verificado end-to-end [EXE] (cliente real ↔ daemon real):
- `get_status` → `{"captureRunning": false,"scanRunning": false}\0` (de `ScanState::toJSON`)
- `add_ssid REALTEST123` → sin salida; luego `list_ssids` → `REALTEST123\n\0`
- `clear_ssids` → sin salida; luego `list_ssids` → vacío
Esto prueba que addSSID/listSSIDs/clearSSIDs/getStatus se despachan correctamente con
los IDs del cliente (14/17/16/20) y llegan a la SQLite/ScanState reales.

Formato por comando:
- `get_status`: JSON (keys: captureRunning, scanRunning, continuous, scanID, scanPercent, bssid) + NUL.
- `list_ssids`: cada SSID + separador, al final NUL.
- `list_probes`: cada fila (ssid[,mac]) + separador, al final NUL.
- Resto: el server hace `close()` sin payload significativo.

---

## 4. TABLA DEFINITIVA comando → ID  (reconciliación 1..19 vs 4..21) [EXE]

**La numeración correcta es 4..21 (la del cliente).** La "1..19" del informe de pineapd
venía de (a) una tabla de salto mal indexada (base 0x40b8b4 en vez de 0x40b8b0, +4 de error)
y (b) nombres de comando asignados a índices equivocados. El dispatch real es
`switch(cmd[0]-1)` con 21 casos válidos (`sltiu v0,21` ⇒ `cmd[0] ∈ 1..21`), tabla @0x40b8b0.

Cada ID abajo es el **primer byte REALMENTE enviado** por `pineap` (evidencia en `recv_*.bin`):

| cmd[0] | hex  | comando CLI                | payload (offsets abs.)                         | resp |
|-------:|------|----------------------------|-----------------------------------------------|------|
| 4  | 0x04 | `run_scan <dur> <freq>`    | [1]=freq, [2..5]=dur u32BE                     | no   |
| 5  | 0x05 | `pause_scan`               | —                                             | no   |
| 6  | 0x06 | `unpause_scan`             | —                                             | no   |
| 7  | 0x07 | `stop_scan`                | —                                             | no   |
| 8  | 0x08 | `deauth`                   | [1..6]=tgt,[7..12]=src,[13]=ch,[14..17]=mult u32BE | no |
| 9  | 0x09 | `handshake_capture_start`  | [1..6]=bssid,[7]=ch                            | no   |
| 10 | 0x0a | `handshake_capture_stop`   | —                                             | no   |
| 11 | 0x0b | `reload_config`            | —                                             | no   |
| 12 | 0x0c | `pause` (channel hop)      | —                                             | no   |
| 13 | 0x0d | `unpause` (channel hop)    | —                                             | no   |
| 14 | 0x0e | `add_ssid <ssid>`          | [1..]=SSID+NUL                                 | no   |
| 15 | 0x0f | `del_ssid <ssid>`          | [1..]=SSID+NUL                                 | no   |
| 16 | 0x10 | `clear_ssids`              | —                                             | no   |
| 17 | 0x11 | `list_ssids`               | —                                             | **sí** |
| 18 | 0x12 | `list_probes <mac>`        | [1..6]=MAC (00..=todas)                        | **sí** |
| 19 | 0x13 | `inject`                   | [1]=ch,[2..5]=count u32BE,[6..9]=len u32BE,[10..]=frame | no |
| 20 | 0x14 | `get_status`               | —                                             | **sí** (JSON) |
| 21 | 0x15 | setOpt (config)            | [1]=opt,[2..5]=pad,[6..]=data                  | no   |

**Dispatch del servidor** (tabla de salto @0x40b8b0, `switch(cmd[0]-1)`): los targets
corregidos (base +0) apuntan a los handlers que coinciden 1-a-1 con la columna de arriba;
validado por el E2E (add/list/clear/get_status funcionan con estos IDs). [EXE+DIS]

Cmd 1,2,3 existen en la tabla del server (list_probes-all, scan-aux, etc.) pero **esta build
del cliente no los emite**; empieza en 4. No hay comando 0.

---

## 5. Sub-opciones de setOpt (cmd 21) — PineAPConfigOpt [EXE]

Primer byte tras el 0x15 (`buf[1]`), capturado en vivo:

| opt (buf[1]) | comando CLI                      | data en buf[6..]        | evidencia |
|-------------:|----------------------------------|-------------------------|-----------|
| 0  | `karma <on/off>`                 | bool [6]                | `15 00 ...01`/`...00` |
| 1  | `beacon_responses <on/off>`      | bool [6]                | `15 01 ...01` |
| 2  | `capture_ssids <on/off>`         | bool [6]                | `15 02 ...01`; .conf `capture_ssids=on` [EXE] |
| 3  | `broadcast_pool <on/off>`        | bool [6]                | `15 03 ...01` |
| 4  | `logging <on/off>`               | bool [6]                | `15 04 ...01` |
| 9  | `ap_channel <ch>`                | byte en [6] (QUIRK §2h) | `15 09 00000000 0b`; .conf `ap_channel=184549376` [EXE] |
| 10 | `connect_notifications <on/off>` | bool [6]                | `15 0a ...01` |
| 11 | `disconnect_notifications <on/off>`| bool [6]              | `15 0b ...01` |
| 12 | `beacon_interval`                | u32 BE [6..9]           | LOW=9000/NORMAL=5712/AGGR=3500 [EXE] |
| 13 | `beacon_response_interval`       | u32 BE [6..9]           | `15 0d 00000000 00002328` |
| 15 | `set_source <mac>`               | MAC [6..11]             | `15 0f 00000000 112233445566` |
| 16 | `set_target <mac>`               | MAC [6..11]             | `15 10 00000000 aabbccddeeff` |

Valores InjectInterval [EXE]: low=9000(0x2328), normal/default=5712(0x1650), high/aggressive=3500(0x0dac).
`karma on/off` además ejecuta `system("hostapd_cli -i wlan0 karma_enable/disable")` (local).
`ssid_filter`/`mac_filter` **NO tocan el socket**: sólo `system("hostapd_cli ...")` (verificado
[EXE]: no generaron tráfico; en qemu fallan con "hostapd_cli: not found").

---

## 6. Confirmación lado servidor: cuánto lee y cómo despacha [EXE+DIS]

- **Lee 4097 bytes** por comando (recvfrom=4097 [EXE]; memset/loop 4097 [DIS]).
- `memset(buf,0,4097)` → `while(got<4097) recv(buf+got, 4097-got)`; EAGAIN→usleep(20), máx 25001 reintentos. [DIS]
- Despacho: `idx = cmd[0]-1`; si `idx < 21` salta por tabla @0x40b8b0; si no, cae a close(). [DIS]
- setOpt (cmd 21) tiene un sub-switch por `cmd[1]` (17 entradas @0x40b95c) → setters de PineAPConfig → `save()` (reescribe /tmp/pineap.conf). [DIS, efecto confirmado [EXE] con capture_ssids y ap_channel]
- Respuestas con NUL terminador para get_status/list_* ; resto close() sin payload. [DIS+EXE]

---

## 7. Listo para reimplementar en C aarch64 (compat byte-a-byte)

Ambos lados DEBEN:
1. Buffer de **4097 bytes** fijos, cero-rellenado; `send` siempre 4097; `recv` en loop hasta 4097.
2. `buf[0]` = ID según tabla §4 (4..21). Dispatch server = `switch(buf[0]-1)`.
3. Enteros multibyte **big-endian**.
4. setOpt: `[0]=21,[1]=opt,[2..5]=0,[6..]=data` con los opts de §5.
5. Reproducir el **quirk de ap_channel** (canal como 1 byte en off 6 ⇒ el server lo lee como 0xCC000000).
6. inject compacto: ch@1, count@2(u32BE), len@6(u32BE), frame@10; `delay` NO va al wire.
7. Respuesta = stream crudo terminado en NUL; sólo para get_status/list_ssids/list_probes.
8. socket `AF_UNIX/SOCK_STREAM` en `/var/run/pineapd.sock`, sockaddr_un len 110.
