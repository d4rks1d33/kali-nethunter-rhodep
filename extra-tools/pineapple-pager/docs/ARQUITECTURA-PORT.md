# WiFi Pineapple Pager — Arquitectura del Port nativo a Kali/aarch64 (G82)

Port del **WiFi Pineapple Pager** (firmware 1.1.3 / OpenWrt 24.10.1 / MIPS mt76x8) a
Kali NetHunter mainline aarch64 en el Motorola G82. Vive en **`/opt/pineapple-pager`**
(separado del Pineapple NANO que ya corre en `/opt/pineapple`), para poder usar uno u otro.

El G82 corre Linux real: pantalla táctil, parlante y vibrador funcionan. Por eso el
Pager se replica **100% fiel** como una **app frontend horizontal** (la carcasa amarilla
+ pantalla 480×222 + D-pad/flechas/select táctiles + LEDs + buzzer por el parlante),
y el backend se portea nativo. Solo se descarta el hardware físico del Pager (fb0,
evdev, GPIO, LEDs sysfs, bq27546), reemplazado por el frontend.

---

## 1. Arquitectura del Pager real (de RE, 3 agentes)

```
  ┌─────────────────────────────────────────────────────────┐
  │  golang-product-server  (/pineapple/pineapple, Go MIPS)  │
  │   - sirve el panel web SPA en :1471 + API REST            │
  │   - API local por unix socket /tmp/api.sock              │
  │   - renderiza la UI de pantalla (fb0 480×222 RGBA) y la  │
  │     espeja por WebSocket ("Virtual Pager")               │
  │   - orquesta pineapd (lo arranca/mata por procd/ubus)    │
  └───────────────┬─────────────────────────────────────────┘
                  │ socket unix /tmp/pineap_sock  (msgpack)
  ┌───────────────▼─────────────────────────────────────────┐
  │  pineapd  (/usr/sbin/pineapd, C++ MIPS)                   │
  │   MOTOR WiFi: recon, PineAP/karma/mimic, deauth/CSA,     │
  │   handshakes (libwifi), pcap, wigle, gpsd                │
  │   libs: libpcap, libsqlite3, libnl, libuci, libwifi      │
  └─────────────────────────────────────────────────────────┘
```

- **pineapd NO toca pantalla/LED/D-pad.** Es puro motor WiFi. (el NANO era igual)
- **pineapd NO autoarranca**: lo levanta el Go server.
- IP LAN del Pager real: **172.16.52.1/24** (NANO=42, nuestro NANO-port=84, usb0=42).

## 2. Protocolo del socket /tmp/pineap_sock  (CLAVE)

Framing:
| off | size | campo | endian |
|---|---|---|---|
| 0 | u32 | `cmd` (command code) | **Big-Endian** |
| 4 | u32 | `length` (bytes de body) | **Big-Endian** |
| 8 | length | `body` = **MessagePack** (map) | msgpack |

**El body es MessagePack**, NO un struct binario. El server usa la lib C **mpack**;
el cliente Go usa vmihailenco/msgpack. => en el port usamos **mpack** (C, portable
aarch64) para el body y escribimos a mano solo el header de 8 bytes BE.

Claves msgpack (tags de los structs Go, OBSERVADAS en ambos binarios):
`bssid, count, flags, channel, mac, ifname, enable, hidden, signal, apkeys, size,
bytime, target, sort, ssids, more, seconds, asc, window, clikeys, devicelist,
probemap, autobssid, interface, random_mac, random_order, ssid_search, ssid_icase,
ssid_regex, rstable, autochannel, devs, aps, pcap, recon, wigle, ssidpool, hash,
ssid, freq, page, name, total, speed, ts, message, error, packets, options, lat,
lon, alt, fix, pps, spd, encryption, ssidmap, clihash, respmap, con, ltime, ltimea,
encmap, freqmap, sta, hop, time, bool, payloads, pcap_n, wigle_n`.

### Comandos (rangos OBSERVADOS; mapeo exacto método→código = pendiente captura)
- **interface_tracker**: 100 list_interfaces, 101 mod_interface, 102/103 toggle on/off,
  104 bands, 105/106 set_type, 107 reset_channels, 400/401/402 deauth/deauth-all/CSA,
  500/501/502 examine bssid/channel/reset, 610/611/612 pcap start/stop/control.
- **recon_engine**: 200-217 count/send devices + stats, 204 new_recon, 206 monitor_device,
  214 set_handshake, 255 hostap_handshake, 600/601 wigle, 901-904 hostap_*, 905 wigle.
- **ssid_pool**: 300-314 fetch/new/clear/config/confbcast/mod/loaduci.
- **gpsd**: command_gpsd_report.

Respuesta "basic" = `{error:uint, message:str}`.

### Structs models.* reconstruidos (campo → clave msgpack)
- PineapCommand (deauth): bssid(str), count(uint), flags(bitmap64 por canal!),
  channel(uint), target(str mac), packets(uint).
  OJO: deauth codifica canales como **bitmap de 64 bits** (channel>>6, 1<<(channel&63)).
- PineapReconNew (204): enable(bool), ifname(str), hidden(bool), signal(int),
  apkeys(array), size(uint), bytime(bool), target(str).
- PineapDeviceRequest (201/203): mac, sort, asc, hash, ssid, freq, page, ssid_search,
  rstable, ssid_icase, ssid_regex, random_mac, random_order, autochannel, autobssid.
- PineapDeviceResponse: devicelist(array<map>), more(bool).
- PineapSimpleSsid: mac(str), sort, ssids(array<str>), more(bool).
- PineapGpsResponse: pps, lat(double), lon(double), alt(double), fix(int), spd(double), prec.
- PineapConfigInterface: interface, autobssid, random_mac, pcap, recon, wigle, ssidpool,
  ltime, ltimea, pcap_n, wigle_n (+ UCI: bands, channel, hop, rate, pineap_disabled...).

DESCONOCIDO (resolver capturando tráfico real del socket, 3-4 capturas bastan):
mapeo exacto método→código; MAC str vs bin(6); layout interno de un PineapDevice.

## 3. Mapeo de radios: Pager → G82 (reusa lo del NANO-port)

| Rol | Pager real | **G82** |
|---|---|---|
| STA → internet | wlan0cli (interna) | **wlan0** (WCN3990, STA) |
| AP mgmt/karma/evil | wlan0mgmt/open/wpa (interna) | **wlan0** vif AP en canal de la STA |
| Monitor/deauth/inject PRIMARIO | wlan1mon USB (primary+inject) | **wlan1** (TP-Link Archer USB) |
| Monitor en radio interna | wlan0mon | **OMITIR** (WCN3990 no hace monitor sin tumbar la red) |

Es el MISMO reparto que ya definimos para el NANO-port. La regla de oro se mantiene:
wlan0 interna NUNCA en monitor; todo deauth/scan/capture va por la Archer wlan1.

## 4. Plan del port

### Backend nativo (C, aarch64) — en /opt/pineapple-pager
- `src/pineapd/`: reimplementar pineapd-pager (header 8B BE + body mpack). Comandos
  prioritarios: list_interfaces, get/set_config, new_recon, send_devices, deauth(+bitmap),
  ssidpool add/del/clear/list, pcap start/stop, gpsd_report.
- `net/`: br-lan + dnsmasq + nftables NAT (reuso del NANO, IP 172.16.52.1 para no chocar).
- `vendor/`: hostapd-mana (karma) — reuso del NANO.
- API para el frontend: capa que traduce HTTP/WebSocket ↔ socket pineapd (sustituye al Go server).

### Frontend "Pager virtual" fiel (app horizontal)
- Carcasa amarilla + pantalla 480×222 con dashboard (iconos rómbicos: warning, PineAP,
  settings, recon/bat, deauth/broadcast), status bar (vibrate, brillo, mute, señal, reloj).
- D-pad/flechas/select **táctiles** → navegación (reemplaza evdev).
- LEDs virtuales (rojo estado + verdes botones) + buzzer por el parlante G82.
- Terminal xterm (como el Pager real).

### Descartado (hardware físico del Pager, lo reemplaza el front)
fb0, evdev D-pad, GPIO vibrator, LEDs sysfs (a/b-button-led, buzzer), bq27546, eMMC,
gpsd USB, mt7921 GPIO, gpio_switch.

## 5. Librerías a satisfacer en aarch64
libpcap, libsqlite3, libnl-3 + libnl-genl-3, libwifi (compilar/vendorizar), mpack
(para el body msgpack), libuuid. Herramienta externa: hcxpcapngtool (handshakes→hashcat).
Config: reemplazar libuci por un parser propio de las mismas claves.
