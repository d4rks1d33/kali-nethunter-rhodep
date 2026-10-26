# FASE 4 — Servicios (systemd) + Red nativa + Instalador

Port del WiFi Pineapple a Kali/NetHunter aarch64 (Motorola G82).

Esta fase ensambla las fases 1–3 (binarios nativos `pineapd-ng`/`pineap-ng`,
panel portado, shims de compat) en un stack que **arranca y para a demanda**,
**contenido en `/opt/pineapple`**, **sin tocar el OS ni el boot del teléfono**.

Fuente del diseño de red (RE verificado):
- `docs/INFORME-panel-PORT.md` (datamodel UCI, mapa init.d→systemd).
- `docs/INFORME_pineapd_RE.md` (formato `/tmp/pineap.conf`, esquema SQLite).
- `/tmp/opencode/pineapple-re/wifi-pineapple-cloner/files/common/etc/95-network.sh`
  (LAN br 172.16.42.1/24, gw 172.16.42.42, dns 1.1.1.1/1.0.0.1) y
  `90-firewall.sh` (masquerade hacia uplink usb/wan/wwan + forward lan↔uplink).

---

## 1. systemd units y orquestación

Todas en `systemd/`. Se instalan en `/etc/systemd/system` (copia) y **NO se
`enable`** → el teléfono bootea normal; el Pineapple sólo corre cuando lo pedís.

| unit | tipo | qué hace | reversible |
|---|---|---|---|
| `pineapple.target` | target | paraguas: `Wants=` todas; `systemctl start/stop` levanta/baja todo | — |
| `pineapple-firstboot.service` | oneshot | provisioning 1ª vez (`ConditionPathExists=!/opt/pineapple/.provisioned`): dirs, seed UCI, `pineapd --initdbs`, seed DB panel, `.provisioned` | re-provisiona borrando `.provisioned` |
| `pineapple-net.service` | oneshot (RemainAfterExit) | `pineapple-net.sh up`: br-lan 172.16.84.1/24 (no choca con usb0) + `ip_forward` + nft `inet pineapple` (masq+forward) | `ExecStop`=`down` revierte TODO |
| `pineapple-dnsmasq.service` | simple | dnsmasq DEDICADO en br-lan (`net/dnsmasq.conf`); NO el del sistema | para con el servicio |
| `pineapple-php-fpm.service` | simple | master php-fpm DEDICADO con el pool del panel; NO el php-fpm del sistema | para con el servicio |
| `pineapple-nginx.service` | simple | nginx DEDICADO, panel en `:1471` (`net/nginx.conf`); NO el nginx del sistema | para con el servicio |
| `pineapd.service` | simple | `ExecStartPre`: render `/tmp/pineap.conf` (gen-config) + monitor iface (best-effort); `ExecStart=/usr/sbin/pineapd` | para con el servicio |
| `pineapple-recon-ws.service` | simple | OPCIONAL (:1337). NO está en el target: el **panel** lanza `reconpp.py` por scan con su `scanID`. Sólo para debug manual. | — |

**Orden de arranque** (por `After=`/`Wants=`/`Requires=`):

```
pineapple-firstboot            (una vez)
   └─> pineapple-net           (br-lan + NAT + ip_forward)
         ├─> pineapple-dnsmasq (Requires=net; DHCP/DNS en br-lan)
         ├─> pineapple-php-fpm
         │     └─> pineapple-nginx   (panel :1471)
         └─> pineapd            (After=net; render config + iface + daemon)
```

`PartOf=pineapple.target` en los servicios → parar el target los para a todos.
`pineapple-net` con `ExecStop` revierte la red, así que **parar el target deja
la red del teléfono como estaba** (sin br-lan, sin la tabla nft, `ip_forward`
restaurado a su valor original guardado).

### Reconciliación con los shims del panel (importante)
El panel llama `/etc/init.d/{pineapd,dnsmasq,firewall}` (procd). El shim
`compat/init.d-dispatcher` los mapea a:
- `pineapd`  → `pineapd.service`
- `dnsmasq`  → `pineapple-dnsmasq.service` (NO el del sistema)
- `firewall` → `pineapple-net.service` (re-aplica LAN+NAT)

Así, cuando el panel "reinicia dnsmasq/firewall", sólo cicla lo del Pineapple.

---

## 2. gen-config — UCI store → `/tmp/pineap.conf`

`net/gen-pineap-config.sh` lee `pineap.@config[0].*` del store UCI (vía el shim
`compat/uci`, que persiste en `/opt/pineapple/etc/uci/pineap`) y renderiza
`/tmp/pineap.conf` en el formato EXACTO que `pineapd-ng` parsea y que el panel
(`PineAPHelper`) lee/escribe:

```
# PineAP Config File
# -- DO NOT EDIT MANUALLY --
autostart = 0
karma = off | on
beacon_interval = LOW | NORMAL | AGGRESSIVE
...
pineap_interface = wlan1mon
ssid_db_path = /opt/pineapple/etc/pineapple.db
...
```

Normaliza `0/1` → `on/off`, intervalos a mayúsculas, filtros a `white/black`.
Lo usa `pineapd.service` en `ExecStartPre` (equivale al `gen_config()` del
init.d original). **Validado**: el output es aceptado por el parser de
`pineapd-ng` (`config_reload`) y `--initdbs` crea el esquema correcto.

---

## 3. Red nativa (`net/pineapple-net.sh up|down`)

- **LAN**: bridge `br-lan`, `172.16.84.1/24` (movido de 172.16.42.1 para no
  chocar con usb0=172.16.42.1/24, el lifeline SSH). (El AP —Archer USB con hostapd—
  se enslava a `br-lan` por la capa AP; este script sólo arma bridge+IP+NAT.)
- **NAT/forwarding**: `sysctl ip_forward=1` (guarda el valor previo) + tabla nft
  DEDICADA `inet pineapple` con masquerade en el uplink y forward lan↔uplink.
  Usar una tabla propia significa que `down` borra SÓLO esa tabla y **nunca
  toca el firewall del teléfono**.
- **Uplink G82**: autodetección por `ip route get 1.1.1.1` (sirve rmnet*, wlan
  cliente, usb0). Override con `UPLINK=<iface>` o `/opt/pineapple/etc/uplink`.
  Se registra en `/opt/pineapple/run/uplink` para revertir el mismo iface.
- **Reversible**: `down` borra la tabla nft, restaura `ip_forward`, borra la IP
  y el bridge. Cero residuo.
- **dnsmasq** (`net/dnsmasq.conf`): instancia dedicada `interface=br-lan`,
  `bind-interfaces`, pool `172.16.84.100–250`, gateway/DNS `172.16.84.1`,
  leases/pid bajo `/opt/pineapple/run`. No lee resolv.conf/hosts del host.

---

## 4. Instalador (`install.sh`) + `uninstall.sh`

`install.sh` (idempotente, reversible, no rompe el OS):
1. **apt deps** (detecta versión de php-fpm para el master dedicado).
2. **compila** `src/pineapd` + `src/pineap` (`make`) e instala
   `pineapd-ng → /usr/sbin/pineapd`, `pineap-ng → /usr/bin/pineap`.
   (pineapd compilado con `-DPINEAPD_*_DB=/opt/pineapple/etc/pineapple.db`.)
3. **copia** `panel/ compat/ net/` → `/opt/pineapple/…`; seed UCI (sólo si falta
   el package, no pisa un store configurado), seed DB (no pisa una DB viva).
4. **symlinks** que el panel/daemon hardcodean:
   `/pineapple → /opt/pineapple/panel`, `/etc/pineapple → /opt/pineapple/etc`,
   y `/etc/init.d/{pineapd,dnsmasq,firewall,cron,sshd} → init.d-dispatcher`.
5. **units** copiadas a `/etc/systemd/system`, `daemon-reload`, drop-in con el
   binario php-fpm detectado. **NO enable** (a demanda).

Flags: `--dry-run` (no cambia nada), `--skip-apt`, `--skip-build`, `--uninstall`.

`uninstall.sh` / `install.sh --uninstall`: para y quita units+drop-ins, quita
los symlinks (sólo si apuntan a nuestro prefijo), quita `/usr/sbin/pineapd` y
`/usr/bin/pineap`, borra `/opt/pineapple`, quita los shims init.d (sólo si
apuntan a nuestro dispatcher). **No toca OS/red/DNS/firewall del teléfono; no
desinstala apt deps** (lo sugiere, no lo fuerza).

---

## 5. Flujo de uso

```sh
# Instalar (en el G82, como root):
cd extra-tools/pineapple-port
sudo ./install.sh                 # compila, instala, NO arranca nada

# Arrancar TODO el stack a demanda:
sudo systemctl start pineapple.target

# Acceder al panel:
#   http://172.16.84.1:1471   (login por defecto root / pineapplesareyummy)

# Ver estado de la red del Pineapple:
/opt/pineapple/net/pineapple-net.sh status

# Parar TODO (revierte br-lan/NAT/ip_forward; el teléfono queda intacto):
sudo systemctl stop pineapple.target

# Desinstalar (revierte todo lo del port):
sudo ./install.sh --uninstall
```

El uplink se autodetecta; si no, forzalo:
```sh
echo rmnet_data0 | sudo tee /opt/pineapple/etc/uplink
# o:  UPLINK=wlan0 sudo systemctl start pineapple.target   (vía override)
```

---

## 6. Dependencias apt finales (G82)

```
apt install \
  nginx-light php-fpm php-cli php-sqlite3 sqlite3 \
  python3 at curl iw hostapd dnsmasq \
  libpcap0.8 libsqlite3-0 libnl-3-200 libnl-genl-3-200
# build (lo instala install.sh): build-essential libpcap-dev libsqlite3-dev
#                                 libnl-3-dev libnl-genl-3-dev pkg-config
systemctl enable --now atd
```
`install.sh` detecta la versión de php-fpm (`php-fpm8.x`) y escribe el
`ExecStart` del master dedicado vía drop-in.

---

## 7. Validación ejecutada en este host (aarch64)

| qué | cómo | resultado |
|---|---|---|
| sintaxis scripts | `sh -n` en los 6 scripts + dispatcher | OK todos |
| units systemd | `systemd-analyze verify` | sin errores de sintaxis/directiva; los únicos mensajes son "binary no presente" (porque no están instalados en ESTE host). Con los ejecutables resueltos (stubs): **CLEAN** las 8 |
| compilación | `make` en src/pineapd y src/pineap | OK, ELF aarch64 |
| gen-config | poblar store con el shim uci (0/1 y on/off) → generar conf | formato correcto; `0→off`, `LOW` respetado; aceptado por el parser de `pineapd-ng` |
| `--initdbs` | `pineapd-ng conf --initdbs` sobre la conf generada | crea el esquema EXACTO (aps, clients, scan_ids, log, tracking, ssids) |
| firstboot e2e | correr `pineapple-firstboot.sh` en prefijo temporal | render conf + initdbs + DB compartida (tablas panel + daemon) + `.provisioned` |
| install dry-run | `install.sh --dry-run` y `--uninstall --dry-run` | lógica correcta, idempotente, reversible |
| iface best-effort | `pineapd-iface.sh` sin WiFi/iw | degrada a exit 0 (no bloquea pineapd) |
| nginx.conf | balance de llaves/directivas + include files estándar presentes | estructuralmente OK |

---

## 8. Honestidad: qué requiere el G82 (o root) para probar EN VIVO

- **`nginx -t`**: nginx no está instalado en este host; se validó la estructura.
  El chequeo real corre device-side en `pineapple-nginx.service` (`ExecStartPre
  nginx -t`). Igual con `php-fpm` y `dnsmasq` (no instalados aquí).
- **Daemon loop + socket `/var/run/pineapd.sock` + protocolo 65B**: `pineapd`
  exige **root** (raw pcap + lock + socket). Aquí se validó el camino sin root
  (`--initdbs` + parseo de config). El loop completo y el handshake del socket
  con el CLI/panel se prueban con la suite `src/pineapd/run_tests.sh` como root
  o en el device.
- **Red real (br-lan/NAT/dnsmasq en vivo)**: `ip`/`nft`/`dnsmasq` modifican la
  red; NO se aplicaron en este host. Se validó sintaxis+lógica. En el G82 el
  `up/down` es seguro porque usa bridge+tabla nft DEDICADOS.
- **WiFi (AP karma/monitor/recon/handshake/deauth)**: requieren el G82 con un
  adaptador USB con monitor/inyección (ej. Archer MT7612U) como AP y el wifi
  interno como monitor, `hostapd` real para karma (el daemon delega karma a
  hostapd — ver INFORME_pineapd_RE §F). Imposible de probar sin hardware.
- **Recon WS (:1337)**: `reconpp.py` lo lanza el panel por scan con su `scanID`;
  el flujo e2e necesita un scan real poblando la DB.
