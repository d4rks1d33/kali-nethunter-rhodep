# Informe de Ingeniería Inversa — `pineapd` (WiFi Pineapple NANO/TETRA)
## Para reimplementación nativa aarch64 (pineapd-ng)

Binario: `/tmp/opencode/pineapple-re/rootfs-base/usr/sbin/pineapd`
ELF32 MSB (big-endian) **MIPS16e** (flags `0x74001005: mips16, mips32r2, o32`), musl
(`/lib/ld-musl-mips-sf.so.1`), C++ (libstdc++), 143397 bytes, sin section headers,
BIND_NOW. Linkea: `libpcap.so.1`, `libwifi.so` (fork Hak5), `libsqlite3.so.0`,
`libstdc++`, `libgcc_s`, `libc` (musl).

### Nota metodológica (entorno de RE)
- NO había Ghidra/IDA/REA-MCP (`GHIDRA_INSTALL_DIR` vacío, REA no registrado).
- El código de la app es **MIPS16e** (símbolos con dirección impar = ISA bit).
  `capstone 5.0.7` de este host **no expone `CS_MODE_MIPS16`** y el `objdump`/`llvm`
  instalados son toolchains ARM/aarch64 sin backend MIPS16 funcional.
- Solución aplicada: se escribió un **desensamblador MIPS16e propio en Python**
  (`scripts/mips16.py`) + mapa de PLT stubs MIPS16 (`scripts/pltmap.py`) + lectura de
  `.dynsym`/`.dynstr`/`strings`. Las rutinas MIPS32 (startup, PLT) se validaron con capstone.
- Convención: **[OBS]** = visto en el binario (con dirección); **[INF]** = inferencia;
  **unknown** = no determinable.

Dumps y scripts: `/tmp/opencode/pineapd-re/{dumps,scripts}/`.

---

## A. Arranque, argumentos y modos

### A.1 Cómo lo arranca el sistema ([OBS] `etc/init.d/pineapd`)
```
boot():   gen_config();  /usr/sbin/pineapd /tmp/pineap.conf --initdb
          (si autostart==1) start()
start():  mkdir -p /tmp/handshakes
          gen_config()                        # regenera /tmp/pineap.conf desde uci
          ifconfig wlan1mon || airmon-ng start wlan1
          /usr/sbin/pineapd /tmp/pineap.conf &
stop():   killall -9 pineapd; rm /var/run/pineapd.sock; rm /var/run/pineapd.lock
```

### A.2 Parseo de argumentos en `main()` ([OBS] 0x409731)
- Uso: `Usage: %s [path to config] (--initdbs) (--debug)` (string @0x1bf9c).
  **Flags reales: `--initdbs` y `--debug`** (comparados con `std::string::compare`, 0x4097fc).
  El init.d pasa `--initdb` (prefijo); el parse es por `compare`, ver A.5.
- `argc` en `0xb0($sp)`: `if (argc < 2)` → imprime Usage y sale (0x40978e, 0x409d00).
- `argv[1]` = path del config → `PineAPConfig::setConfigPath()` / `reload()`.
- Flags por cada arg extra (bucle 0x4097d4..0x409830):
  - `--initdbs` → setea flag en `0x38($sp)` (**initdbs**).
  - `--debug`   → setea flag en `0x3c($sp)` (**debug**).

### A.3 Checks de arranque ([OBS])
1. **Detección de placa** (`getbrd()` 0x40e90d, llamada 0x409748): ejecuta
   `cat /proc/cmdline | awk ...` (@0x1c098) y `dmesg | head ...` (@0x1c0d4/0x1c128),
   compara con `WIFI-PINEAPPLE-NANO` (@0x1bf60, strncmp len 0x13),
   `PINEAPPLE-TETRA` (@0x1bfe4, len 0xf) y `AR9344 rev` (@0x1bff4).
2. **root**: `geteuid()` (0x409782) debe ser 0; si no → log `PineAP must be run as root`
   (@0x1bf80) y `raise(11)`/`exit(1)` (0x40986a).
3. `daemonInit()` (A.4).

### A.4 `daemonInit()` — instancia única ([OBS] 0x40a831)
- `fopen("/var/run/pineapd.lock")` (@0x1bda8) + `ftrylockfile()`.
- Si ya está bloqueado → `cerr << "Only one instance of pineapd can be started at once"`
  (@0x1bdd8) → `exit(1)`. **No hace fork/setsid** (el init.d lo manda a background con `&`).

### A.5 Modo `--initdbs` ([OBS])
- Tras crear los singletons de config y las 5 bases
  (`PineAPConfig::get`, `ReconDatabase::get`, `LogDatabase::get`, `TrackingDatabase::get`,
  `SSIDDatabase::get`; 0x40995c–0x409974), si el flag initdbs (`0x38($sp)`) está activo:
  `daemonExit(0)` (0x40997a–0x409982). **O sea: `--initdbs` crea el esquema SQLite
  (los constructores corren `CREATE TABLE IF NOT EXISTS`) y termina.**
- `--debug` → `Logger::setEnabled(true)` (0x409928).

### A.6 Secuencia de arranque normal (main, tras checks) ([OBS])
1. `Logger::setEnabled(debug)`.
2. Instanciar singletons: `PineAPConfig`, `ReconDatabase`, `LogDatabase`,
   `TrackingDatabase`, `SSIDDatabase`.
3. Reservar colas/deques: 2× `queue<SniffedFrame*>`, 2× `deque<InjectFrame*>`,
   `deque<ParseResult*>` (0x409984–0x409a8c).
4. `startInterface(iface, sniffQueue, injectQueueHi, injectQueueLo)` (0x4125d5) — hilo captura.
5. `startParser(sniffQueue, parseResults)` (0x413135) — hilo parser.
6. `startResultHandler(parseResults)` (0x419f8d) — hilo escritor DB/handshakes.
7. `initComms()` (0x40a335) — crea socket UNIX (sección C).
8. **Loop principal** (0x409afa–0x409cbc), ver sección H.
9. Al salir: `stopSniffing`, `stopInterface`, `stopParser`, `stopResultHandler`,
   `daemonExit(0)`.

### A.7 `daemonExit(int)` ([OBS] 0x40a8c1) / `handler(int)` (0x40a8d9)
- `signal(SIGINT=2, handler)` instalado (0x40979e, 0x409906).
- `handler` detecta señales de shutdown repetidas → "Caught multiple shutdown signals, bailing"
  (@0x1be0c). Limpia socket/lock.

---

## B. `/tmp/pineap.conf` — formato y campos

### B.1 Generación ([OBS] init.d `gen_config`, confirmado por los strings del parser)
Formato de texto `clave = valor`, una por línea. Cabecera:
```
# PineAP Config File            (@0x1c53c)
# -- DO NOT EDIT MANUALLY --     (@0x1c554)
```
El panel (`PineAPHelper.php`) lo lee/escribe como texto `clave = valor`.

### B.2 Campos reconocidos por el parser (`PineAPConfig::parseOpt` 0x40f955;
nombres @0x1c2e8..0x1c3e0; serialización @0x1c400..0x1c520) [OBS]

| Campo                      | Tipo        | Getter/efecto en pineapd |
|----------------------------|-------------|--------------------------|
| `autostart`                | 0/1         | solo init.d (no lo usa pineapd) |
| `karma`                    | on/off      | `setKarma` — habilita respuestas karma (vía hostapd, ver F) |
| `beacon_interval`          | LOW/NORMAL/AGGRESSIVE | `setBeacon_interval(InjectInterval)` — cadencia beaconing pool |
| `beacon_response_interval` | LOW/NORMAL/AGGRESSIVE | `setBeacon_response_interval` |
| `beacon_responses`         | on/off      | `isBeacon_responses` — responder beacons dirigidos |
| `capture_ssids`            | on/off      | `isCapture_ssids` — añadir SSIDs sniffados al pool (parseMgmt 0x41418e) |
| `broadcast_ssid_pool`      | on/off      | `isBroadcast_ssid_pool` — beaconing periódico del pool (loop main 0x409ccc) |
| `logging`                  | on/off      | `isLogging` — escribir log.db |
| `mac_filter`               | black/white | FilterMode (DENY/ALLOW); strings `DENY`/`ALLOW`/`INVALID` @0x1c198 |
| `ssid_filter`              | black/white | FilterMode |
| `ap_channel`               | int         | `getAp_channel` — canal AP para beacons/inject |
| `pineap_mac`               | MAC (BSSID origen) | `getPineap_mac` — BSSID de beacons forjados |
| `target_mac`              | MAC          | `getTarget_mac` — destino de beacons/deauth dirigidos |
| `pineap_interface`         | str (wlan1mon) | `getPineapInterface` — iface monitor de captura/inyección |
| `recon_db_path`            | str (/tmp/recon.db) | `getRecon_db_path` |
| `hostapd_db_path`          | str (/tmp/log.db)   | `getHostapd_db_path` (= log_db) |
| `ssid_db_path`             | str (/etc/pineapple/pineapple.db) | `getSsid_db_path` |
| `connect_notifications`    | on/off      | `isConnect_notifications` — `notify "<mac> has connected."` (@0x1bf08) |
| `disconnect_notifications` | on/off      | `isDisconnect_notifications` (@0x1bf28) |
| `rts_enabled`              | on/off      | RTS (init.d fija `off`); relacionado con `rts_scan_id` |
| `rts_scan_id`              | int (-1)    | `rts_scan_id` (comando sel=11) |

Notas:
- `InjectInterval` (enum): LOW/NORMAL/AGGRESSIVE (`injectIntervalToString` 0x40f04d).
  Valor `aggressive` string @0x1c2dc.
- `getFilters_db_path` existe pero no se vio DB de filtros abierta (unknown si se usa).
- También hay `filters_db_path` (getter 0x40fdf1) — **unknown** si se puebla desde config.

---

## C. PROTOCOLO del socket UNIX `/var/run/pineapd.sock` (LO MÁS IMPORTANTE)

### C.1 Setup del servidor (`initComms()` 0x40a335) [OBS]
```c
unlink("/var/run/pineapd.sock");                         // @0x1bd0c
fd = socket(AF_UNIX=1, SOCK_STREAM|SOCK_CLOEXEC=0x82, 0);// 0x40a35c
int one=1; setsockopt(fd, SOL_SOCKET=0xffff, SO_REUSEADDR=4, &one, 4); // 0x40a382
struct sockaddr_un a; memset(&a,0,0x6c);                 // 0x40a388
a.sun_family = AF_UNIX;                                   // sh @0x40a39a
memcpy(a.sun_path, "/var/run/pineapd.sock", 0x15/0x6c);  // 0x40a3a6
bind(fd, &a, 0x6e /*=110 sizeof sockaddr_un*/);          // 0x40a3b2
listen(fd, 10);                                           // 0x40a466
```
Errores (tag `MAIN`): `Failed top open socket: %s` (@0x1bd2c),
`Failed to bind sock fd %d to path %s: %s` (@0x1bd48),
`Failed to listen on sock fd %d at path %s: %s` (@0x1bd74).

### C.2 Formato de mensaje = **estructura binaria fija de 65 (0x41) bytes**
`pineap_command` [OBS: `openCommandComms` 0x40a6a1 y `handleNextCommand` 0x40b819]

El servidor hace `accept()` y luego un **bucle de `recv` hasta completar 0x41 bytes**:
```c
accept(fd, 0, 0);                        // 0x40b83c / 0x40a6b4
memset(cmd, 0, 0x41);                    // 0x40b82e
int got=0, tries=0;
while ((n = recv(cli, cmd+got, 0x41-got, 0)) ) {
    if (n<0) { if (errno==EAGAIN/*0xb*/) { usleep(20); if(++tries<0x3589) continue; }
               break; }
    got += n;
    if (got >= 0x41) break;
    if (tries >= 0x3589/*13705*/) break;
}
```
(`usleep(20)` entre reintentos; máx `0x3589` intentos). **Es binario, de tamaño fijo 65 B.**

### C.3 Layout del struct `pineap_command` (65 bytes) [OBS, por offsets accedidos]
```
offset  size  campo              uso observado
0x00    1     cmd                selector principal (switch, 0x40b89c: cmd-1, sltiu 0x15)
0x01    1     subcmd / flag      sub-selector (p.ej. config field; 0x40b944 sltiu 0x11)
                                 también usado como bool on/off en varios handlers
0x06    1/2/4 value             valor del campo de config:
                                   - bool:  lbu [0x06]                (0x40c484...)
                                   - int16/int: lhu[0x06]<<16 | lhu[0x08] (0x40c464, canal/intervalo)
0x06..  6     mac/ssid bytes     región de datos (MAC 6B / SSID / payload)
0x0b    1     (usado en sel=1)   byte de control list_probes (0x40bf98)
0x0d    1     channel/mult       deauth: canal/multiplicador (0x40bbdc lbu[0x0d])
...     ...   payload            hasta completar 65B (SSID string, MAC destino, etc.)
```
(El CLI `pineap` rellena este struct; el daemon no valida más allá del rango del switch.)

### C.4 Dispatch principal — `switch(cmd[0]-1)` (tabla @0x40b8b4, 21 entradas) [OBS]
Correlación selector ↔ comando CLI (vocabulario del CLI `pineap`, "PineAP V3.2"):

| cmd[0] | handler  | comando (CLI)                | acción en pineapd |
|-------:|----------|------------------------------|-------------------|
| 1  | 0x40c129 | `list_probes <MAC>`          | `LogDatabase::getProbes(MAC)` → envía filas |
| 2  | 0x40bd7d | `unpause_scan`               | `ScanState::setPaused(false)` |
| 3  | 0x40bd19 | `pause_scan`                 | `stopSniffing()` + `setPaused(true)` |
| 4  | 0x40bcd1 | (start recon/scan interno)   | `startSniffing()`, flags ScanState |
| 5  | 0x40be13 | `stop_scan`                  | limpia flags recon (`[1032]=0`, `[1035]`, `[5]`) |
| 6  | 0x40bb8b | `deauth [mac][bssid][ch][mult]` | `gen_deauth`+`gen_disassoc` → InjectFrame (ver I) |
| 7  | 0x40beb7 | `handshake_capture_start [bssid][ch]` | `setChannel`, `ScanState::setBssid`, setHandshakeRunning |
| 8  | 0x40be6d | `handshake_capture_stop`     | limpia handshake/recon flags |
| 9  | 0x40c421 | (config set + reload/save)   | `PineAPConfig::reload()`+setters+`save()` (sub-switch cmd[1]) |
| 10 | 0x40bd61 | (scan state aux)             | flags ScanState |
| 11 | 0x40c3fb | `rts_scan_id [id]`           | setea rts scan id en ScanState/config |
| 12 | 0x40bdcd | `add_ssid [ssid]`            | `SSIDDatabase::addSSID(str)` |
| 13 | 0x40bae5 | `del_ssid [ssid]`            | `SSIDDatabase::delSSID(str)` |
| 14 | 0x40bad7 | `clear_ssids`                | `SSIDDatabase::clearSSIDs()` |
| 15 | 0x40ba51 | `list_ssids`                 | `SSIDDatabase::getSSIDs(vec)` → envía c/u + sep |
| 16 | 0x40b97f | `list_probes` (todas)        | `LogDatabase::getProbes()` → envía filas |
| 17 | 0x40bb37 | `inject [file][ch][mult][delay]` | `InjectFrame(...)` → cola de inyección |
| 18 | 0x40b8df | `get_status`                 | `ScanState::toJSON()` → envía JSON + NUL |
| 19 | 0x40b947 | (config update por campo)    | sub-switch cmd[1] → setters (ver C.6) |
| 20 | 0x40b017 | (ruta pool/beacon; borde)    | zona de `beaconSSIDPool`; **unknown exacto** |
| 21 | 0x4069c0 | (fuera de .text → default/no-op) | selector inválido |

> Rango válido efectivo: **1..19**. (sel 20/21 caen fuera o en código no-comando.)
> Las variantes `karma on/off`, `logging on/off`, `capture_ssids`, `beacon_responses`,
> `broadcast_pool`, `beacon_interval`, `beacon_response_interval`, `set_source`,
> `set_target`, `ssid_filter`, `mac_filter`, `connect/disconnect_notifications`
> se envían como **cmd[0]=19** con **cmd[1]=índice de campo** y **cmd[6]=valor** (C.6).

### C.5 Protocolo de respuesta [OBS]
- `get_status` (sel 18): `send(cli, json, len, 0)` y luego `send(cli, "\0", 1, 0)`
  (0x40b900, 0x40b90e). JSON (ScanState::toJSON 0x40a9ad), keys:
  ```
  {
    "captureRunning": true|false,
    "scanRunning": true|false,
    "continuous": true|false,
    "scanID": <int>,
    "scanPercent": <int>,
    "bssid": "<mac>"
  }
  ```
  (strings @0x1be50..0x1befc).
- `list_ssids`/`list_probes` (sel 15/16/1): por cada registro `send(record)` +
  `send(sep,1)` y al final `send(term,1)` (NUL). (bucles 0x40b9c0/0x40ba6e).
- Resto de comandos (set/toggle/scan/deauth/inject): `close(cli)` sin payload de
  respuesta significativo (0x40b924).
- Siempre `close(cli)` al terminar de servir (0x40b924).

### C.6 Sub-switch de config — `switch(cmd[1])` (tabla @0x40b95c, 17 entradas) [OBS]
Para el comando de actualización de config. Cada caso hace
`PineAPConfig::get()` → `setXXX(valor)` → (al final) `reload()`?/`save()`:

| cmd[1] | setter                               | valor leído |
|-------:|--------------------------------------|-------------|
| 0  | `setCapture_ssids(bool)`                 | lbu[0x06] |
| 1  | `setBroadcast_ssid_pool(bool)`           | lbu[0x06] |
| 2  | `setLogging(bool)`                       | lbu[0x06] |
| 3  | (save/reload)                            | — |
| 4  | (save/reload)                            | — |
| 5  | (save/reload)                            | — |
| 6  | (save/reload)                            | — |
| 7  | `setAp_channel(int)`                     | lhu[0x06]<<16 \| lhu[0x08] |
| 8  | `setConnect_notifications(bool)`         | lbu[0x06] |
| 9  | `setDisconnect_notifications(bool)`      | lbu[0x06] |
| 10 | `setBeacon_interval(InjectInterval)`     | lhu[0x06]<<16 \| lhu[0x08] |
| 11 | `setBeacon_response_interval(InjectInt)` | lhu[0x06]<<16 \| lhu[0x08] |
| 12 | (save/reload)                            | — |
| 13 | `setKarma(bool)` **o** `setPineap_mac`   | MAC desde buffer scratch (0x114) |
| 14 | `setTarget_mac(MACAddress)`              | 6 bytes MAC |
| 15 | (ruta deauth/inject 0x40bb99)            | — |
| 16 | (fuera de rango / default)               | — |

> Hay también `setBeacon_responses(bool)` (0x410ecc) y `setKarma(bool)` (0x410eac)
> alcanzados por el bloque 0x40c4ac/0x40c4be del handler sel=9; la numeración exacta
> de índices 3–6/12 que sólo hacen save es **[INF]** (son casos que caen a
> `save()` común sin setter específico). Tras cada set → `PineAPConfig::save()` (0x410dd8)
> que reescribe `/tmp/pineap.conf`.

---

## D. Uso de libwifi (fork Hak5, `libwifi.so`) — construcción de frames

### D.1 API exportada por este libwifi (NO es la upstream libwifi/libwifi) [OBS nm -D]
```
gen_beacon, beacon_frame_size, dump_beacon_frame, finalize_beacon, set_ssid,
gen_deauth, gen_disassoc, gen_rts, verify_frame, wifi_get_frame, wifi_get_bss,
wifi_get_client, wifi_get_type/subtype/flags (+set), wifi_get_rsn_info,
is_wpa_handshake, get_wpa_handshake_part, parse_signal,
add_tagged_parameter, easy_add_tagged_parameter, remove_tagged_parameter,
wifi_tag_iterator_init/next, ieee80211_radiotap_iterator_init/next,
calculate_fcs, crc32b, djb2, recalulate_crc, get_epoch, print_ap_info
```
**No existe `create_probe_resp`.** → La respuesta Karma NO la hace pineapd con libwifi
(ver F: la hace hostapd). pineapd usa libwifi para **beacons** del pool, **deauth/disassoc**,
y para **parsear** frames capturados.

### D.2 Beacon del SSID pool (`beaconSSIDPool` 0x40b001) [OBS]
Por cada SSID del pool:
```c
MACAddress src = PineAPConfig::getPineap_mac();   // 0x40b140
uint8_t ch     = PineAPConfig::getAp_channel();   // 0x40b22a
// src/dst/bssid vía MACAddress::getMacBin()       // 0x40b252/5c/64
wifi_beacon b;
gen_beacon(&b, dst, src, bssid /*=src*/, ssid?, ...); // 0x40b278
size_t n = beacon_frame_size(&b);                 // 0x40b27e
uint8_t* buf = new uint8_t[n];                     // 0x40b286
dump_beacon_frame(&b, buf, n);                     // 0x40b292
finalize_beacon(&b/buf);                           // 0x40b298
InjectFrame f(ch, delay, mult, buf, n);            // 0x40b2b6
injectQueue.push_back(f);                          // 0x40b356 (_M_push_back_aux)
delete[] buf;                                       // 0x40b2c0
usleep(...);                                        // 0x40b362 entre SSIDs
```
Llamado desde el loop principal cuando `isBroadcast_ssid_pool()` (main 0x409ccc/0x409cf2),
con `getTarget_mac()` y `getBeacon_interval()`.

### D.3 Deauth / disassoc (sel=6, 0x40bb8b) — ver sección I.

### D.4 Parseo de frames entrantes (`FrameParser::parseMgmt` 0x413619, `parseData` 0x4131dd)
- `verify_frame(pktData, pktSize)` → puntero a `wifi_frame` validado (0x413662).
- `wifi_frame.subtype/type` (`lbu [0x01]`): `== 4` vs `== 8` (0x41366c/70):
  - rama AP (beacon/probe-resp): `wifi_get_bss(&bss, frame)` (0x413676) →
    `AccessPointResult(bss)` (0x4189c0).
  - rama cliente (probe-req/data): `wifi_get_client(&cli, frame)` (0x4136b4) →
    `ClientResult(clientMAC, apMAC)` (0x4136f2); valida SSID con `isUTF8` (0x4136c0).
- parseData usa `is_wpa_handshake` + `get_wpa_handshake_part` (sección E).

---

## E. Captura (libpcap) y handshakes

### E.1 Apertura de captura (`WiFiInterface` ctor 0x411e31) [OBS]
```c
pcap_open_live(pineap_interface, snaplen, promisc=?, to_ms=0x12c/*300*/, errbuf); // 0x411e76
// socket(AF_INET=2, SOCK_DGRAM=1, 0) para ioctl de canal                         // 0x411e84
```
Errores (tag `INTERFACE`): `Failed to create capture handle for %s: %s` (@0x1c5f0),
`Failed to open ioctl socket: %s` (@0x1c61c). **Sin filtro BPF** (captura todo el monitor).

### E.2 Cambio de canal (`setChannel` 0x4126ed / `WiFiInterface::setChannel` 0x412109) [OBS]
```c
struct iwreq w; strncpy(w.ifr_name, pineap_interface, 0x10);  // 0x4127dc
w.u.freq.channel = ch; ...                                    
ioctl(sock, SIOCSIWFREQ /*~0x8B24*/, &w);                     // 0x4127ee (reintenta con usleep)
```
(El inmediato mostrado `0x6224` es artefacto de decode MIPS16; el request es el de
frecuencia inalámbrica. Mecanismo: iwreq + ioctl con reintento.) [OBS mecanismo; nº exacto INF]

### E.3 Hilo de captura (`interface_run` 0x412979) [OBS]
Loop:
```
setChannel(...)          // channel hopping (0x412a48/0x412b6c)
WiFiInterface::nextFrame()// pcap_next_ex envuelto (0x412c56)
 -> SniffedFrame(pkthdr, data, hasRadiotap)   // push a sniffQueue (0x412dc2)
pcap_inject(...)         // drena cola de inyección (0x412a7a/0x412b9e)
usleep(...)              // 0x412a3a/0x412cd0
```
`SniffedFrame` guarda: radiotap header+size, packet data+size, timestamp (µs),
signal strength (`parse_signal`), flag "complete".

### E.4 Detección/captura de handshake WPA (`parseData` 0x4131dd) [OBS]
```c
if (ScanState::isHandshakeRunning()) {                 // 0x41340e
  if (is_wpa_handshake(frame)) {                        // 0x413418
    int part = get_wpa_handshake_part(frame);           // 0x413422  (M1..M4)
    // localizar/crear HandshakeResult por (apMAC, clientMAC)
    hr->addPart(sniffedFrame);  // guarda copia del frame M1..M4 (0x4134c4/0x41351c/0x413542)
    if (hr->isCrackable()) ...                           // 0x41352a
  }
}
```
`HandshakeResult` (ctor 0x418391): campos `apMAC`, `clientMAC`, `ssid`, `scanID`,
frames `M1..M4` + `SSIDFrame` (`getM1..getM4` 0x41891d.., `getSSIDFrame` 0x41892c,
`getPartsMask` 0x418919, `hasPart(i)` 0x418705, `isCrackable` 0x418715,
`isComplete` 0x418751, `isPartial` 0x418769).

### E.5 Escritura de .pcap (`handleHandshakeResult` 0x41a119) [OBS]
```c
mkdir("/tmp/handshakes");                               // 0x41a130
name = "/tmp/handshakes/" + apMAC.getMacStr() +
       (isPartial()? "_partial.pcap" : "_full.pcap");   // @0x1d15c / @0x1d16c
stat(name);                                             // 0x41a2c4 (no sobreescribir)
// junta M1,M2,M3,M4,SSIDFrame en un deque                (0x41a334..0x41a37e)
FILE* f = fopen(name, ...);                             // 0x41a386
pcap_t* pd = pcap_open_dead(DLT_IEEE802_11_RADIO, snaplen); // 0x41a392
pcap_dumper_t* du = pcap_dump_fopen(pd, f);             // 0x41a39c
for each frame:
   buf = new[radiotapSize + packetSize]; memset;         // 0x41a3e8
   memcpy(buf, radiotapHeader, radiotapSize);            // 0x41a404
   memcpy(buf+radiotapSize, packetData, packetSize);     // 0x41a41a
   pcap_dump(du, &pkthdr, buf); delete[] buf;            // 0x41a426/0x41a42c
pcap_dump_close(du); pcap_close(pd);                     // 0x41a440/0x41a446
```
Tag `RESULTS` (@0x1d118).

---

## F. Lógica KARMA completa

### F.1 Reparto de responsabilidades (clave para el port) [OBS]
- **Las respuestas Karma reales (probe-response forjada) las hace `hostapd`**, no pineapd.
  El CLI `pineap` ejecuta: `hostapd_cli -i wlan0 karma_enable/karma_disable/
  karma_black/karma_white/karma_mac_black/karma_mac_white` (strings en `/usr/bin/pineap`).
  libwifi de pineapd no tiene `create_probe_resp`.
- **pineapd** se encarga de: (a) beaconing periódico del **SSID pool**
  (`broadcast_ssid_pool`, D.2); (b) **cosecha de SSIDs** desde probe-requests/beacons
  (`capture_ssids`) para engordar el pool y el log; (c) filtros; (d) recon/handshake/deauth.

### F.2 SSID pool y beaconing [OBS]
- Loop principal: si `isBroadcast_ssid_pool()` → `beaconSSIDPool(target_mac, beacon_interval)`
  (main 0x409ccc..0x409cf2). Cadencia por `InjectInterval` (LOW/NORMAL/AGGRESSIVE).
- `SSIDDatabase::getSSIDs()` provee el pool (tabla `ssids`, ver G).

### F.3 Cosecha de SSIDs (`capture_ssids`) [OBS parseMgmt 0x41418e]
```c
if (PineAPConfig::isCapture_ssids()) {                  // 0x41418e
   ssid = AccessPointResult::getSsid();                  // 0x4141a0
   if (SSIDDatabase no lo tiene) SSIDDatabase::addSSID(ssid);
}
```
Además todo probe/assoc/deassoc se registra en `log.db` (ver G, LogResult).

### F.4 beacon_responses [OBS]
`isBeacon_responses()` (0x40fd11) condiciona respuestas de beacon dirigidas;
usado junto con `beacon_response_interval`. (La emisión concreta se enruta por la
misma maquinaria de InjectFrame/inject queue.)

### F.5 Filtros (`mac_filter`/`ssid_filter`, FilterMode) [OBS]
`FilterMode` → strings `DENY`/`ALLOW`/`INVALID` (`filterModeToStr` 0x40efed).
`TrackingDatabase::isTracked(MAC)` (0x416641, tabla `tracking`) implementa whitelist/
blacklist por MAC. La aplicación del filtro ocurre en el flujo de resultados/respuesta.
(La integración fina black/white con hostapd la gobierna el CLI, F.1.)

---

## G. ESQUEMA SQLite EXACTO (CREATE TABLE) y escrituras

Todas las sentencias están como strings literales [OBS, offsets citados]. Los
`CREATE TABLE IF NOT EXISTS` corren en los **constructores** de cada *Database*
(`Database::exec`), por eso `--initdbs` crea el esquema completo.

### G.1 recon_db (`/tmp/recon.db`) — `ReconDatabase` (ctor 0x415939) [OBS]
```sql
-- @0x1c6dc
CREATE TABLE IF NOT EXISTS aps ( scan_id INT, ssid TEXT, bssid TEXT,
  encryption INT, hidden INT, channel INT, signal INT, wps INT,
  last_seen INT NOT NULL, UNIQUE(scan_id, ssid, bssid) ON CONFLICT REPLACE );
-- @0x1c7c8
CREATE TABLE IF NOT EXISTS clients ( scan_id INT, mac TEXT, bssid TEXT,
  last_seen INT NOT NULL, UNIQUE(scan_id, mac) ON CONFLICT REPLACE );
-- @0x1c874
CREATE TABLE IF NOT EXISTS scan_ids ( scan_id INT UNIQUE NOT NULL,
  date TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP );
```
Escrituras:
- `writeAP(AccessPointResult*)` (0x415c6d): `INSERT OR REPLACE INTO aps
  (scan_id, ssid, bssid, encryption, hidden, channel, signal, wps, last_seen)
  VALUES (%d,%s,%s,%d,%d,%d,%d,%d,%d)` (@0x1c90c).
- `writeClient(ClientResult*)` (0x415d55): `INSERT OR REPLACE INTO clients
  (scan_id, mac, bssid, last_seen) VALUES (%d,%s,%s,%d)` (@0x1c9a4).
- `nextScanID()` (0x415e05): `SELECT scan_id FROM scan_ids ORDER BY scan_id DESC LIMIT 1`
  (@0x1ca00) y `INSERT INTO scan_ids (scan_id) VALUES (%d)` (@0x1ca3c).

### G.2 log_db / hostapd_db (`/tmp/log.db`) — `LogDatabase` (ctor 0x415f89) [OBS]
```sql
-- @0x1ca68
CREATE TABLE IF NOT EXISTS log( log_type INT NOT NULL, mac TEXT NOT NULL,
  ssid TEXT, dups INT NOT NULL DEFAULT 0,
  created_at INT NOT NULL, updated_at INT NOT NULL );
```
Escrituras (`writeEntry(LogResult*)` 0x4161e9):
- `INSERT INTO log(log_type, mac, ssid, dups, created_at, updated_at)
   VALUES (%d,%s,%s,%d,%d,%d)` (@0x1cb2c)
- Dedup: `SELECT count(*) FROM log WHERE mac=%s AND ssid=%s` (@0x1cb90) +
  `UPDATE log SET dups=%d WHERE mac=%s AND ssid=%s` (@0x1cbc8).
- `getProbes()` (0x416c41): `SELECT DISTINCT ssid,mac FROM log
   WHERE ssid != "" AND log_type = 0` (@0x1ceb0).
- `getProbes(MAC)` (0x416ef5): `SELECT DISTINCT ssid FROM log
   WHERE mac=%s AND ssid != "" AND log_type = 0` (@0x1cef4).
- `LogType` (LogResult::LogType): **0 = PROBE** (@0x1cf98), ASSOCIATION (@0x1cf8c),
  DEAUTH (@0x1cfa0). (En la tabla `log_type` 0=probe, 1=assoc, 2=deassoc [INF por orden
  y strings]).

### G.3 tracking (misma log_db o recon) — `TrackingDatabase` (ctor 0x4163d5) [OBS]
```sql
-- @0x1cc00
CREATE TABLE IF NOT EXISTS tracking ( id INT PRIMARY KEY, mac TEXT UNIQUE NOT NULL );
```
- `isTracked(MAC)` (0x416641): `SELECT mac FROM tracking WHERE mac=%s` (@0x1cc7c).
- Script de tracking externo: `/etc/pineapple/tracking_script` (@0x1d0f8) con env
  `TRACKING_MAC`, `TRACKING_ACTION`, `TRACKING_SSID` (@0x1d0c8..0x1d0e8).

### G.4 ssid_db (`/etc/pineapple/pineapple.db`) — `SSIDDatabase` (ctor 0x4166c9) [OBS]
```sql
-- @0x1cca4
CREATE TABLE IF NOT EXISTS ssids ( ssid TEXT UNIQUE NOT NULL,
  new_ssid INTEGER NOT NULL DEFAULT 1,
  created_at TIMESTAMP NOT NULL,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  user_created INT NOT NULL DEFAULT 0 );
```
Operaciones:
- `hasSSID` (0x416ab1): `SELECT ssid FROM ssids WHERE ssid=%s` (@0x1cda8).
- `addSSID` (0x416979): si existe → `UPDATE ssids SET updated_at=CURRENT_TIMESTAMP,
   new_ssid=1 WHERE ssid=%s` (@0x1cdd0); si no → `INSERT INTO ssids(ssid, created_at,
   updated_at) VALUES(%s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)` (@0x1ce18).
- `delSSID` (0x416b31): `DELETE FROM ssids WHERE ssid=%s` (@0x1ce7c).
- `clearSSIDs` (0x416ba9): `DELETE FROM ssids;` (@0x1ce9c).
- `getSSIDs(vec)` (0x417055): `SELECT ssid FROM ssids` (@0x1cf44).

### G.5 Infra `Database` (0x415351) [OBS]
`sqlite3_open_v2` (error `Failed to open sqlite3 database at %s: %s` @0x1c67c),
`begin_tx`=`BEGIN TRANSACTION;` (@0x1c6a8), `rollback_tx`=`ROLLBACK;` (@0x1c6bc),
`end_tx`=`END TRANSACTION;` (@0x1c6c8). API prepared statements:
`beginQuery/step/endQuery` con `sqlite3_prepare_v2/bind_*/step/finalize`.

---

## H. Arquitectura de hilos y loop principal

### H.1 Hilos (todos `pthread_create`, con mutex `pthread_mutex_*`) [OBS]
1. **Captura** — `interface_run` (0x412979), lanzado por `startInterface` (0x4125d5).
   Hace channel hopping, `pcap_next_ex`, push a `sniffQueue`, drena `injectQueue`.
2. **Parser** — `parser_run` (0x414491), lanzado por `startParser` (0x413135).
   Saca de `sniffQueue`, `FrameParser::parse` → parseMgmt/parseData → `addResult`.
3. **Result handler / escritor DB** — `results_run` (0x41aa9d), lanzado por
   `startResultHandler` (0x419f8d). Saca de `parseResults`, `handleResult` →
   `handleAPResult`(→writeAP), `handleClientResult`(→writeClient),
   `handleLogResult`(→writeEntry), `handleHandshakeResult`(→pcap).
4. **Main thread** — loop de comandos + temporización (H.2).

Sincronización: `PSemaphore` (0x41bb51: request/tryRequest/release/get/setTimeout),
`PThread mutex`, `__sync_synchronize` (barreras para los guards estáticos/singletons).

### H.2 Loop principal (`main` 0x409afa–0x409cbc) [OBS]
```c
clock_gettime(CLOCK_REALTIME, &t0);
while (ScanState::running /*[0x5]/[1032]*/) {
   if (ScanState recon/paused flags) ...                // gating
   handleNextCommand();                                 // 0x40b818  (accept+recv+dispatch, bloqueante por cliente)
   if (time - lastHop > umbral) chanHop();              // 0x40a735 channel hopping recon
   if (isBroadcast_ssid_pool())
        beaconSSIDPool(target_mac, beacon_interval);    // 0x40b001
   // control de duración de scan: time()/clock_gettime, nanosleep
   nanosleep(...);                                      // 0x409ba6
}
// shutdown: stopSniffing/stopInterface/stopParser/stopResultHandler; daemonExit(0)
```
`ScanState` (singleton 0x40dbd8) campos observados: `[0x5]` paused/running flag,
`[0x2c]` start/duration, `[0x408=1032]` reconRunning, `[0x409=1033]` hopping,
`[0x40b=1035]` (flag aux scan). Métodos: isHopping/setHopping, isPaused/setPaused,
getElapsedSec, get/setDurationSec, isHandshakeRunning/set, get/setBssid,
isReconRunning/set, get/setReconScanID, getPercentComplete, get/setChannelMask,
scanFiveGHz/scanTwoPointFourGHz, isReconComplete, get/setStartTime, setStartTimeToNow.

---

## I. DEAUTH — implementación (sel=6, 0x40bb8b) [OBS]

Invocación CLI: `pineap /tmp/pineap.conf deauth <source> <target> <channel> <multiplier>`
(PineAPHelper::deauth, PineAPHelper.php:258; channel con `str_pad(…,2,"0")`).
Flujo en pineapd:
```c
// MACs origen/destino desde el struct (región de datos del cmd)
// 1) Deauth frame
wifi_deauth d;  gen_deauth(&d, dst, src, bssid, reason, ...);   // 0x40bbba
// 2) Disassoc frame
wifi_disassoc u; gen_disassoc(&u, dst, src, bssid, reason, ...);// 0x40bbca
// canal/multiplicador:  ch = cmd[0x0d]  (0x40bbdc / 0x40bc00 / 0x40bc42 / 0x40bc66)
// Empaqueta en InjectFrame y lo encola:
InjectFrame f1(ch, delay=0xc850, mult, deauthBuf, len);  // 0x40bbee  (delay 0xc850≈51280µs)
InjectFrame f2(ch, delay=0xc850, mult, disassocBuf, len);
//   li $v0,0x1e(=30)  y  li $v0,0xfa(=250) → conteos/intervalos de repetición
injectQueue.push(f1); injectQueue.push(f2);
```
El **hilo de captura** drena la cola y hace `pcap_inject(handle, buf, len)` en el
canal indicado (`WiFiInterface::inject`, 0x4120c1/0x412371; `interface_run` 0x412a7a).
`InjectFrame` (ctor 0x411ae1/0x411b65) guarda: channel, delay (µs), multiplier,
payload (copiado), len. Getters: getChannel/getDelay/getMultiplier/getPayload.

El comando **`inject`** (sel=17, 0x40bb37) construye un `InjectFrame` a partir de un
payload binario recibido/leído (channel/mult/delay desde el struct) y lo encola igual.

---

## J. Checklist para reimplementar `pineapd-ng` (aarch64, C nativo)

### J.1 Estructuras de datos a portar
- [ ] `MACAddress` (6 bytes; getMacStr/getMacBin; is{Broadcast,Zeroed,Multicast,
      GloballyAssigned}; operadores ==,!=,<,>,<=,>=).
- [ ] `PineAPConfig` (singleton) con los ~24 campos de la tabla B.2; `parseOpt`,
      `serialize`, `save/saveToFile`, `reload`, todos los get/set. Formato de archivo
      `clave = valor` con cabecera `# PineAP Config File`.
- [ ] `ScanState` (singleton) con flags/campos de H.2 y `toJSON` (formato C.5).
- [ ] `SniffedFrame` (radiotap+packet+ts+signal+complete).
- [ ] `InjectFrame` (channel, delay, multiplier, payload, len).
- [ ] `ParseResult` + subclases `AccessPointResult`, `ClientResult`, `LogResult`
      (LogType 0=probe/1=assoc/2=deassoc), `HandshakeResult` (M1..M4+SSIDFrame,
      isCrackable/isComplete/isPartial/partsMask).
- [ ] `FrameParser`, `WiFiInterface`.
- [ ] `Database` base + `ReconDatabase`, `LogDatabase`, `TrackingDatabase`, `SSIDDatabase`.
- [ ] `Logger`, `PSemaphore`.

### J.2 Arranque
- [ ] CLI: `pineapd <config> [--initdbs] [--debug]`.
- [ ] Check root (`geteuid()==0`) y (opcional) detección de placa.
- [ ] Lock de instancia única (`/var/run/pineapd.lock` + flock).
- [ ] `--initdbs`: abrir las 4 DBs (crea esquema) y salir.
- [ ] Crear colas; lanzar hilos captura/parser/results/comms; loop principal.

### J.3 Socket UNIX `/var/run/pineapd.sock` (sección C — crítico)
- [ ] `AF_UNIX`/`SOCK_STREAM` (+CLOEXEC), `SO_REUSEADDR`, `bind` (sizeof sockaddr_un),
      `listen(10)`, `unlink` previo.
- [ ] Protocolo: `accept`; `recv` hasta **65 bytes** (`pineap_command`), con reintentos
      `usleep(20)` en EAGAIN.
- [ ] Dispatch `switch(cmd[0])` 1..19 (tabla C.4) + sub-switch `cmd[1]` de config (C.6).
- [ ] Respuestas: `get_status`→JSON+NUL; `list_*`→registros+sep+NUL; resto→close.
- [ ] Mantener **idéntico** el layout del struct y los IDs de comando (el CLI y el panel
      dependen de ello). **El layout exacto de offsets >0x0d (payload SSID/MAC) debe
      confirmarse contra el CLI `pineap`** (lo analiza el otro agente) — marcar como
      punto de sincronización.

### J.4 WiFi
- [ ] Reimplementar/portar la **API del libwifi fork** (D.1): gen_beacon,
      beacon_frame_size, dump_beacon_frame, finalize_beacon, set_ssid, gen_deauth,
      gen_disassoc, gen_rts, verify_frame, wifi_get_frame/bss/client/type/subtype/
      flags/rsn_info, is_wpa_handshake, get_wpa_handshake_part, parse_signal,
      tagged-param helpers, radiotap iterator, FCS/CRC. (Es Apache-2.0 upstream pero
      ESTE es un fork con nombres propios; conviene portar el .so o reescribir con
      la MISMA firma.)
- [ ] Captura: `pcap_open_live(iface, snaplen, promisc, 300ms)`, sin BPF.
- [ ] Canal: ioctl `SIOCSIWFREQ` sobre `iwreq` (en aarch64/Kali probablemente usar
      `nl80211`/`iw` en vez de WEXT — **cambio de plataforma a decidir**).
- [ ] Inyección: `pcap_inject` desde la cola.

### J.5 Karma / pool / filtros (sección F)
- [ ] Beaconing periódico del SSID pool (`broadcast_ssid_pool`, cadencia por InjectInterval).
- [ ] Cosecha de SSIDs (`capture_ssids`) hacia tabla `ssids` + `log`.
- [ ] Karma "real" (probe-resp): **depende de hostapd-karma** (en NetHunter usar
      `hostapd-wpe`/`hostapd` con patch karma, invocado como en el CLI). Decidir si el
      port lo integra o delega.
- [ ] Filtros MAC/SSID (FilterMode DENY/ALLOW) + `tracking` + `tracking_script`.

### J.6 Handshakes (sección E)
- [ ] Detección WPA (is_wpa_handshake/get_wpa_handshake_part) y `HandshakeResult`.
- [ ] Salida: `/tmp/handshakes/<apmac>_full.pcap` / `_partial.pcap` con radiotap,
      vía `pcap_open_dead(DLT_IEEE802_11_RADIO)` + `pcap_dump`.

### J.7 SQLite (sección G — esquema EXACTO, el panel lo lee)
- [ ] Crear tablas `aps`, `clients`, `scan_ids` (recon.db); `log` (log.db);
      `tracking`; `ssids` (pineapple.db) con las sentencias literales de G.
- [ ] Reproducir INSERT/UPDATE/SELECT idénticos (dedup de log por mac+ssid, scan_id, etc.).

### J.8 Deauth (sección I)
- [ ] `deauth src target channel multiplier` → gen_deauth+gen_disassoc →
      InjectFrame(channel, delay≈51280µs, multiplier) → cola → pcap_inject,
      repetido según multiplier/intervalos (30/250).

### J.9 Puntos "unknown" a resolver antes/durante el port
- Offsets exactos del payload del struct para SSID/MAC/canal más allá de 0x0,0x1,0x6,0xd
  → confirmar contra el CLI `pineap` (otro agente).
- Número exacto del ioctl de canal en el binario (mecanismo iwreq confirmado; valor INF).
- Uso real de `filters_db_path`/`getFilters_db_path` (no se vio DB de filtros abierta).
- Semántica fina de `rts_scan_id`/`rts_enabled` (comando sel=11) — sólo parcialmente visto.
- Índices 3–6/12 del sub-switch de config que caen a `save()` común (numeración INF).
- Relación precisa cmd[0]=9 (0x40c421) vs cmd[0]=19 (0x40b947): ambos tocan config;
  parecen dos puntos de entrada (uno "set+reload", otro "set por campo") — confirmar
  con el emisor (CLI).

---

### Apéndice: archivos generados
- `dumps/nm_dynamic.txt`, `dumps/functions_demangled.txt` — símbolos.
- `dumps/data_strings.txt`, `dumps/strings_off.txt` — strings con offset.
- `dumps/pltmap.txt` — 161 PLT stubs MIPS16 → import.
- `dumps/dis_*.txt` — desensamblados MIPS16 (main, initComms, openCommandComms,
  handleNextCommand, beaconSSIDPool, parseMgmt, parseData, handleHandshake,
  interface_run, WiFiInterface_ctor, setChannel).
- `scripts/mips16.py` — desensamblador MIPS16e propio.
- `scripts/pltmap.py`, `scripts/build_elf.py`, `scripts/pltmap.py`.
