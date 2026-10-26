# pineapd Pager — Protocolo del socket /tmp/pineap_sock (DEFINITIVO)

Extraido por RE estatico (disasm MIPS16e del binario `usr/sbin/pineapd` del firmware
Pager 1.1.3) + verificado que arranca bajo qemu-mipsel y crea el socket. Framing
confirmado empiricamente.

## Framing
```
[ u32 cmd  BE ][ u32 len BE ][ body: MessagePack (len bytes) ]
```
- Body = MessagePack map (lib `mpack` en el server). Respuesta = mismo framing.
- `add_command(uint32 code, handler)` registra cada handler. 60 handlers, 59 codes
  (el 106 duplicado), gap intencional en 306.

## TABLA code -> handler (todos OBSERVADOS del disasm)

### interface_tracker::register_socket_functions() @ 0x46b5bc
| code | handler | que hace |
|---|---|---|
| 100 | command_list_interfaces | lista interfaces WiFi |
| 101 | command_mod_interface | modifica interfaz (canal) |
| 102 | command_toggle_interface | enable/disable (A) |
| 103 | command_toggle_interface | enable/disable (B) |
| 104 | command_mod_interface | modifica (2) |
| 105 | command_set_interface_type | set tipo monitor/AP/sta |
| 106 | command_set_interface_type | set type (2 y 3 - registrado 2 veces) |
| 107 | command_interface_reset_channels | reset canales |
| 400 | command_send_deauth | deauth (A) |
| 401 | command_send_deauth | deauth (B) = deauth-all |
| 402 | command_send_deauth | deauth (C) = deauth-CSA |
| 500 | command_examine | examine bssid |
| 501 | command_examine | examine channel |
| 502 | command_examine | examine reset |
| 610 | command_pcap_control | pcap start |
| 611 | command_pcap_control | pcap stop |
| 612 | command_pcap_control | pcap control |

### recon_engine::register_socket_functions() @ 0x49c99c
| code | handler | que hace |
|---|---|---|
| 200 | command_count_devices | cuenta devices |
| 201 | command_send_devices | vuelca lista devices (variante) |
| 202 | command_count_devices | count (2) |
| 203 | command_send_devices | send devices |
| 204 | command_new_recon | inicia recon |
| 205 | command_send_devices | send devices |
| 206 | command_monitor_device | monitorea un device |
| 207 | command_recon_freqstats | stats por frecuencia |
| 208 | command_recon_stats | stats generales |
| 209 | command_recon_devcounts | conteos devices |
| 210 | command_recon_cryptstats | stats cifrado |
| 211 | command_recon_pps | paquetes/seg |
| 212 | command_recondb_control | control recon.db |
| 213 | command_waitfor | espera evento |
| 214 | command_set_handshake | modo captura handshake |
| 215 | command_send_devices | send devices |
| 216 | command_send_devices | send devices |
| 217 | command_send_devices | send devices |
| 255 | command_set_ssidcollect | activa recoleccion SSIDs |
| 600 | command_wigle_control | wigle (A) |
| 601 | command_wigle_control | wigle (B) |
| 602 | command_wigle_control | wigle (C) |
| 901 | command_hostap_handshake | hostapd handshake |
| 902 | command_hostap_challenge | hostapd challenge |
| 903 | command_hostap_basic | hostapd auth basica |
| 904 | command_hostap_client_connect | cliente conectado |
| 905 | command_hostap_client_disconnect | cliente desconectado |

### ssid_pool::setup() @ 0x4e9b60
| code | handler | que hace |
|---|---|---|
| 300 | command_fetch_ssid_pool | devuelve pool actual |
| 301 | command_clear_ssid_pool | limpia pool |
| 302 | command_mod_ssid_pool | modifica pool (A) |
| 303 | command_mod_ssid_pool | modifica pool (B) |
| 304-305 | command_config_ssid_pool | configura pool |
| 306 | (AUSENTE - gap intencional) | - |
| 307-310 | command_config_ssid_pool | configura pool |
| 311 | command_loaduci_config | carga config UCI |
| 312 | command_confbcast_ssid_pool | config broadcast pool |
| 313 | command_loaduci_ssid_pool | carga pool UCI |
| 314 | command_fetch_new_ssid_pool | pool "nuevo" |

### gpsd::setup() @ 0x4ee670
| code | handler | que hace |
|---|---|---|
| 10 | command_gpsd_report | reporta GPS lat/lon/alt |

## Notas de implementacion para el port
- Los codes "variantes" (ej send_devices 201/203/205/215/216/217) usan el MISMO handler;
  el comportamiento se diferencia por el `code` (pasado como uint al handler) dentro.
- gpsd=10 (no rango alto), set_ssidcollect=255 (no 315).
- Para el port: header 8B BE hecho a mano + body con mpack (C, portable aarch64).
- Claves msgpack por comando: ver ARQUITECTURA-PORT.md seccion 2.
- Semantica exacta de cada variante: leer el cuerpo de cada command_* (pendiente, se
  resuelve al implementar/capturar trafico real con el frontend conectado).

## Estado de la captura qemu
- pineapd arranca bajo `qemu-mipsel-static -L <rootfs>` y crea /tmp/pineap_sock. OK.
- En qemu los handlers no responden a bodies vacios (dependen de nl80211/interfaces
  reales ausentes). El mapeo code->handler se fijo ESTATICAMENTE (tabla arriba).
  La semantica fina de payloads se validara con el frontend real conectado.
