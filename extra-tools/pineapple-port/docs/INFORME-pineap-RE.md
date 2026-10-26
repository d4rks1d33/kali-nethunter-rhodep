# Informe de Reverse Engineering: `pineap` (CLI del WiFi Pineapple)

Objetivo: reimplementar `pineap` idéntico, nativo para aarch64 (Kali/Motorola G82), portando el
WiFi Pineapple. Este documento describe el parseo de argv, TODOS los comandos, el protocolo de
socket con `pineapd`, el formato de salida a stdout, y la lista de trabajo para `pineap-ng`.

Convención de evidencia:
- `[OBS]` = observado directamente (desensamblado MIPS16 con offsets, o ejecución bajo qemu).
- `[INF]` = inferido a partir de la evidencia.

## 0. Identidad del binario [OBS]
- `/tmp/opencode/pineapple-re/rootfs-base/usr/bin/pineap`, 36901 bytes.
- ELF 32-bit **MSB MIPS32r2**, **ISA MIPS16e** (código de usuario en MIPS16; sólo el startup es MIPS32),
  o32 ABI, dynamically linked, interp `/lib/ld-musl-mips-sf.so.1` (musl), sin section headers.
- C++ (libstdc++.so.6, GLIBCXX). Linkea además `libpcap.so.1`, `libgcc_s.so.1`, `libc.so`.
- Strings de identidad: `PineAP V3.2`, `Copyright 2019 Hak5 LLC`, commit `de3598e095`,
  "Built with legacy command syntax enabled", "Built at: Mon Mar 9 13:04:35 UTC 2020".
- Símbolos exportados conservados (nombres C++ mangled) → el dispatch y las funciones son
  directamente legibles.

Herramientas usadas: `mips-linux-gnu-objdump -m mips:16` (único disassembler con soporte MIPS16;
capstone 5.0.7 y r2 NO decodifican MIPS16), `readelf`, `nm -D`, `strings`, y `qemu-mips-static`
contra un servidor-stub del socket para validar el wire protocol byte a byte.

---

## A. Parseo de argumentos (estructura argv) [OBS]

`main` @ 0x402bf1 (MIPS16). Lógica:

1. Si `argc < 2` → imprime `Usage: <argv0> [command] [options]` + el bloque de ayuda y `exit`. [OBS 0x402bfa]
2. Mira `argv[1]`. Hace `strstr(argv[1], "pineap.conf")`:
   - Si `argv[1]` **contiene** `"pineap.conf"` → **modo con config**: `argv[1]`=ruta del config
     (p.ej. `/tmp/pineap.conf`), `argv[2]`=comando, `argv[3..]`=parámetros.
   - Si **no** lo contiene → **modo legacy**: `argv[1]`=comando, `argv[2..]`=parámetros
     (string "legacy command syntax enabled"). [OBS 0x402c04..0x402c84; validado runtime:
     `pineap list_probes <mac>` funciona igual que `pineap /tmp/pineap.conf list_probes <mac>`].
3. El comando se despacha por una cadena de `strcmp(cmd, "literal")`. `argc` se usa para validar que
   haya suficientes parámetros; si faltan imprime `Missing argument to <cmd> command` o
   `The <cmd> command requires N arguments (M given)` y `exit`.
4. Comando no reconocido → imprime `Unknown command: <cmd>` y sale. [OBS 0x40396c]

Nota importante de reimplementación: **el binario NO lee ni parsea el archivo de config él mismo**
para los comandos; sólo usa `argv[1]` para decidir modo config/legacy. Quien lee `/tmp/pineap.conf`
y lo aplica es el **daemon** `pineapd` (ver init.d, que arranca `pineapd /tmp/pineap.conf`). El CLI
sólo habla por socket. (Excepción: el panel PHP escribe/lee el config directamente cuando el daemon
NO está corriendo — eso es lógica del panel, no de `pineap`.)

---

## B. Lista completa de comandos (nombre + args + efecto)

Cada comando se mapea a una función con símbolo. "Vía" = cómo llega a `pineapd`.
Códigos de comando/opción confirmados por desensamblado y por captura runtime del datagrama.

### B.1 Comandos que van al socket vía `sendCommandSimple`/`sendCommandComplex` (byte[0] = PineAPCommand)

| Comando CLI | args | función | PineAPCommand (byte0) | payload (bytes 1..) | imprime resp. |
|---|---|---|---|---|---|
| `run_scan <dur> <freq>` | dur(seg), freq(0=2.4,1=5,2=ambos) | runScan | **4** | `[freq:u8][dur:u32 BE]` (5 B) | no |
| `pause_scan` | — | pauseScan | **5** | — | no |
| `unpause_scan` | — | unpauseScan | **6** | — | no |
| `stop_scan` | — | stopScan | **7** | — | no |
| `deauth <target> <bssid> <ch> <mult>` | ver nota | deauth | **8** | `[source:6][target:6][ch:u8][mult:u32 BE]` (17 B) | no |
| `handshake_capture_start <bssid> <ch>` | bssid, canal | handshakeCaptureStart | **9** | `[bssid:6][ch:u8]` (7 B) | no |
| `handshake_capture_stop` | — | handshakeCaptureStop | **10** | — | no |
| `reload_config` | — | reloadConfig | **11** | — | no |
| `pause` | — (channel hopping) | pauseChanHop | **12** | — | no |
| `unpause` | — (channel hopping) | unpauseChanHop | **13** | — | no |
| `add_ssid <ssid>` | ssid | addSSID | **14** | `[ssid:33]` null-padded a 32 | no |
| `del_ssid <ssid>` | ssid | delSSID | **15** | `[ssid:33]` null-padded a 32 | no |
| `clear_ssids` | — | clearSSIDs | **16** | — | no |
| `list_ssids` | — | listSSIDs | **17** | — | **sí** |
| `list_probes <mac>` | mac | listProbesFromMAC | **18** | `[mac:6]` | **sí** |
| `inject <file> <ch> <count> <delay>` | ver nota | inject | **19** | struct de ~2057 B (ver B.4) | no* |
| `get_status` | — | getStatus | **20** | — | **sí** (JSON) |
| (set config option) | — interno | setOpt/setOptBool | **21** | `[opt:u8][pad:4][data...]` | no |

Nota deauth: la CLI declara `deauth [mac] [bssid] [channel] [multiplier]`; el panel lo invoca como
`pineap … deauth $source $target $channel $multiplier` (PineAPHelper::deauth, con channel a 2 dígitos
y multiplier). En el wire el orden serializado es **source(6) luego target(6)** — validado runtime:
`deauth aa:... bb:... 06 3` emite `08 bb×6 aa×6 06 00000003`. channel es el 1er MAC-arg mappeado a
source. Para el port, respetar que el panel pasa `deauth <source_or_client> <sta/bssid> <ch> <mult>`.

Nota inject*: el panel captura stdout; **stdout vacío = éxito** (ver PineAPHelper/inject). Si imprime
algo (ej. "Warning: frame of size …") el panel lo trata como error.

### B.2 Comandos que van al socket vía `setOpt` (PineAPCommand=21, con sub-opción PineAPConfigOpt)

`setOpt(opt, data, len)` / `setOptBool(opt, bool)` construyen el payload
`[opt:u8][4 bytes pad=0][data...]` y lo envían como comando **21**. Validado runtime.

| Comando CLI | args | PineAPConfigOpt (byte tras el 21) | data |
|---|---|---|---|
| `karma <on/off>` | on/off | **0** | bool (1 byte) + además `system(hostapd_cli … karma_enable/disable)` |
| `beacon_responses <on/off>` | on/off | **1** | bool |
| `capture_ssids <on/off>` | on/off | **2** | bool |
| `broadcast_pool <on/off>` | on/off | **3** | bool |
| `logging <on/off>` | on/off | **4** | bool |
| `ap_channel <ch>` | canal | **9** | u8 canal |
| `connect_notifications <on/off>` | on/off | **10** | bool |
| `disconnect_notifications <on/off>` | on/off | **11** | bool |
| `beacon_interval <LOW/NORMAL/AGGRESSIVE>` | intervalo | **12** | u32 BE (ver B.3) |
| `beacon_response_interval <LOW/NORMAL/AGGRESSIVE>` | intervalo | **13** | u32 BE |
| `set_source <mac>` | mac | **15** | mac (6 B) |
| `set_target <mac>` | mac | **16** | mac (6 B) |

Códigos 5,6,7,8,14 de PineAPConfigOpt no se usan desde esta versión de la CLI (gap en el enum).

### B.3 Valores de intervalos (InjectInterval) [OBS]
`strToInjectInterval` (case-insensitive vía transformLower). Validado runtime (u32 BE enviado):
- `low`  → **9000** (0x2328)
- `normal` (o cualquier string que no matchee) → **5712** (0x1650)  ← valor por defecto
- `high` / `aggressive` → **3500** (0x0DAC)

(Son milisegundos entre beacons: menor = más agresivo.)

### B.4 inject (comando 19) — detalle del payload [OBS parcial]
- Lee el frame desde `<file>` (o desde stdin si file es `-` o `/dev/stdin`), lo **hex-decodifica**
  (`hexDecode`). Máximo de frame = 2048 bytes; si excede imprime
  `Warning: frame of size N bytes exceeds 2048 byte max. It will be truncated.`
- Construye un buffer (base del payload) con:
  - `channel`  en offset 380 (u8)
  - `count`/multiplier en offset 381..384 (u32 BE)
  - `frame_len` en offset 385..388 (u32 BE)
  - bytes del frame a partir de offset 389
- Mensaje final: `msg[0]=19`, `memcpy(msg+1, payload, 2057)`, `send(fd, msg, 4097, 0)`.
- (El 4º arg `delay` se pasa en la línea de comandos; su offset exacto en el struct no se
  confirmó al 100% — ver "unknowns". Los tres primeros campos sí están confirmados.)

### B.5 Comandos que NO van al socket (resueltos localmente) [OBS]
- `help` → imprime el bloque de ayuda y sale.
- `version` → imprime versión/commit/build (ver salida en §0), sin tocar socket.
- `ssid_filter <black/white>` → **sólo** `system("hostapd_cli -i wlan0 karma_white|karma_black")`.
  (strToFilterMode: "deny"/"black" → modo DENY/black; otro → ALLOW/white.)
- `mac_filter <black/white>` → **sólo** `system("hostapd_cli -i wlan0 karma_mac_white|karma_mac_black")`.
- `karma <on/off>` → híbrido: `system(hostapd_cli … karma_enable/disable)` **y** `setOptBool(0, …)`
  por socket.

### B.6 Comandos listados en el help pero SIN handler en el dispatch [OBS]
- `rts_attack [on/off]` y `rts_scan_id [scan id]` aparecen en el texto de ayuda (y en el config
  `rts_enabled`, `rts_scan_id`) pero **no** tienen `strcmp` en el dispatch de esta build → caerían en
  `Unknown command:`. (El soporte RTS vive del lado del daemon / config, no en esta CLI.)

---

## C. Protocolo con `pineapd` (socket `/var/run/pineapd.sock`)

### C.1 Conexión [OBS: openCliComms @ 0x4041c9; validado por qemu -strace]
```c
int fd = socket(AF_UNIX /*1*/, SOCK_STREAM /*2 en MIPS*/, 0);   // ¡STREAM, no DGRAM!
struct sockaddr_un addr; memset(&addr, 0, 88);
addr.sun_family = AF_UNIX;                       // = 1
memcpy(addr.sun_path, "/var/run/pineapd.sock", 21);
connect(fd, (sockaddr*)&addr, 110);              // 110 = sizeof(sockaddr_un)
// error de socket():  "Failed to create socket: <strerror>"  -> cerr, exit(1)
// error de connect(): "Failed to connect to socket at path: /var/run/pineapd.sock: <strerror>" -> cerr, exit(1)
```
Aclaración MIPS: en el ABI de Linux/MIPS `SOCK_STREAM=2` y `SOCK_DGRAM=1` (invertidos respecto a la
mayoría de arquitecturas). El `li a1,2` del binario = **SOCK_STREAM** (confirmado por qemu-strace:
`socket(PF_UNIX,SOCK_STREAM,…)`). En aarch64 usar simplemente `SOCK_STREAM`.

### C.2 Formato de REQUEST (el mismo para todos los comandos) [OBS; validado runtime]
Un único buffer de **4097 bytes fijos**, siempre:
```
byte[0]      = PineAPCommand (ver tabla B)
byte[1..4096]= payload específico del comando, el resto en ceros (memset previo)
send(fd, buf, 4097, 0);   // SIEMPRE 4097 bytes exactos
```
Para el comando 21 (setOpt): `byte[1]=PineAPConfigOpt`, `byte[2..5]=0 (pad)`, `byte[6..]=data`.

Capturas runtime (hex de los primeros bytes del datagrama de 4097):
```
get_status                : 14 00…                         (cmd 20)
list_ssids                : 11 00…                         (cmd 17)
add_ssid "TestNet"        : 0e 54 65 73 74 4e 65 74 00…    (cmd 14, "TestNet"+pad)
list_probes aa:bb:…:ff    : 12 aabbccddeeff 00…           (cmd 18, MAC)
run_scan 30 2             : 04 02 0000001e 00…            (cmd 4, freq=2, dur=30 BE)
deauth aa… bb… 06 3       : 08 bbbbbbbbbbbb aaaaaaaaaaaa 06 00000003   (cmd 8)
handshake_capture_start … : 09 deadbeef0011 06 00…        (cmd 9, bssid, ch)
set_source 11:22:…:66     : 15 0f 00000000 112233445566   (cmd 21, opt 15, MAC)
set_target aa:bb:…:ff     : 15 10 00000000 aabbccddeeff   (cmd 21, opt 16, MAC)
capture_ssids on          : 15 02 00000000 01             (cmd 21, opt 2, bool=1)
beacon_interval LOW       : 15 0c 00000000 00002328       (cmd 21, opt 12, 9000 BE)
beacon_interval NORMAL    : 15 0c 00000000 00001650       (cmd 21, opt 12, 5712 BE)
beacon_interval AGGRESSIVE: 15 0c 00000000 00000dac       (cmd 21, opt 12, 3500 BE)
```

### C.3 Formato de RESPONSE [OBS: loop de recv en sendCommandSimple/Complex; validado runtime]
Sólo se lee respuesta si el flag `printResponse` está activo (comandos `list_ssids`, `list_probes`,
`get_status`). El cliente:
```c
char c;
while (recv(fd, &c, 1, 0) > 0) {   // lee de a 1 byte
    if (c == '\0') break;          // termina en NUL
    cout.write(&c, 1);             // reenvía crudo a stdout
}
close(fd);
```
Es decir: **la respuesta es un stream de texto terminado en NUL**; `pineap` lo vuelca tal cual a
stdout, sin transformarlo. El `pineapd` debe terminar su respuesta con `\0`.

Los comandos sin `printResponse` igualmente hacen `send(4097)` y `close(fd)` inmediato (no leen).

---

## D. Salida a stdout por comando (lo que el panel parsea)

- `get_status` → **JSON** terminado en NUL. Campos que el panel consume (PineAP/Recon):
  `running`, `scanRunning`, `scanID`, `scanPercent`, `continuous`, `captureRunning`, `bssid`.
  Caso daemon caído: el panel compara `== "PineAP is not running"` (ese texto lo produce el daemon;
  si el daemon está apagado, `pineap` imprimiría en su lugar el error de connect a stderr).
  El panel hace `json_decode(implode("\n", stdout))`.
- `list_ssids` → una SSID por línea (texto terminado en NUL).
- `list_probes <mac>` → una SSID sondeada por línea; el panel hace `array_unique` (loadProbes).
- `inject …` → **stdout vacío en éxito**; cualquier salida = error para el panel.
- `version` → 5 líneas (ver §0). `help`/`Usage` → bloque de ayuda.
- Resto de comandos (set*, deauth, run_scan, pause/stop, reload_config, clear_ssids,
  handshake_capture_*) → sin salida (el panel no parsea su stdout; usa exec/execBackground).

Patrón de invocación del panel (confirmado en PineAPHelper.php / module.php):
`pineap /tmp/pineap.conf <comando> [args]` vía `exec()`/`proc_open()`. Cuando el daemon NO corre,
el panel edita `/tmp/pineap.conf` directamente en vez de llamar a `pineap`.

---

## E. Qué reimplementar para un `pineap-ng` nativo aarch64 idéntico

1. **CLI / parseo argv** (§A): modo config (`argv[1]` contiene `pineap.conf`) vs legacy; validación de
   argc con los mismos mensajes de error (`Missing argument to <cmd> command`,
   `The <cmd> command requires N arguments (M given)`, `Unknown command: <cmd>`).
2. **Help/version idénticos** (§0): mismos textos, incluido commit `de3598e095` o el que corresponda.
3. **Capa de socket** (§C.1): `socket(AF_UNIX, SOCK_STREAM, 0)` + `connect` a
   `/var/run/pineapd.sock` con `sockaddr_un` (len 110); mismos mensajes de error a stderr.
4. **Serializador de request** (§C.2): buffer de 4097 bytes, `buf[0]=cmd`, payload según comando,
   `send(fd, buf, 4097, 0)`. Respeta big-endian para enteros multibyte (u32: run_scan dur, deauth
   mult, beacon intervals, inject count/len; canal/bool/opt son 1 byte).
5. **Tabla de comandos y códigos** (tablas B.1/B.2): enum `PineAPCommand` (4..21) y
   `PineAPConfigOpt` (0,1,2,3,4,9,10,11,12,13,15,16). Sub-protocolo setOpt (cmd 21,
   `[opt][4 pad][data]`).
6. **Mapas de valores** (§B.3): InjectInterval LOW=9000 / NORMAL=5712 / HIGH=AGGRESSIVE=3500;
   FilterMode black/deny vs white/allow; `on`/`off` case-insensitive (isOptionOn).
7. **Parseo de MAC** a 6 bytes (`MACAddress` desde string "aa:bb:…"), usado por set_source/target,
   deauth, list_probes, handshake_capture_start.
8. **inject** (§B.4): lectura de archivo o stdin, hexdecode, límite 2048, armado del struct
   (channel@380, count@381 u32BE, len@385 u32BE, frame@389), cmd 19, send 4097. (Confirmar offset de
   `delay` contra el daemon.)
9. **Lector de response** (§C.3): loop `recv` de a 1 byte hasta NUL, volcado crudo a stdout, para
   `list_ssids`, `list_probes`, `get_status`.
10. **Side-effects locales** (§B.5): `system("hostapd_cli -i wlan0 …")` para karma / ssid_filter /
    mac_filter. En aarch64/Kali ajustar la interfaz (wlan0) al adaptador real.
11. **Compatibilidad con el daemon**: el formato de 4097 bytes, los códigos de comando/opción y el
    terminador NUL de la respuesta DEBEN coincidir con lo que documente el agente de `pineapd`.
    (Cross-check pendiente con ese informe.)

### Unknowns / a verificar contra el daemon
- Offset exacto del `delay` de `inject` dentro del struct (campos channel/count/len confirmados).
- Esquema JSON completo de `get_status` del lado servidor (campos observados vía panel; confirmar con
  el RE de `pineapd`).
- Semántica exacta de los códigos PineAPConfigOpt no usados por esta CLI (5,6,7,8,14) — definidos por
  el daemon.

---

## Artefactos generados (en /tmp/opencode/pineap-re/)
- `objdump-full.txt`, `main_dispatch.txt` — desensamblado.
- `demangled-funcs.txt`, `stub_map.json` — símbolos y mapa PLT→import.
- `strings-all.txt`, `nm-D.txt`, `readelf-*.txt` — metadatos.
- `dis16.py` — desensamblador MIPS16 anotado (jal→nombre, pools→strings).
- `stub_server.py` + `stub_*.txt` / `client_*.txt` — capturas runtime del wire protocol.
