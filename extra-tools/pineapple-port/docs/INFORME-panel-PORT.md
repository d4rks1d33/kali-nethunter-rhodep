# Informe de Port: PANEL web del WiFi Pineapple -> Kali/NetHunter aarch64 (Motorola G82)

Objetivo: correr el panel (nginx + php-fpm + SQLite + AngularJS) NATIVO en Kali,
reemplazando todo lo atado a OpenWrt por una capa de compatibilidad (shims),
tocando el panel SOLO para seguridad (auth) y para portar Python 2 -> 3.

Todo vive bajo `/opt`. En el device se crean DOS symlinks (los crea el instalador,
no se edita el OS):
- `/pineapple`      -> `/opt/pineapple/panel`   (doc-root; el panel hardcodea `/pineapple`)
- `/etc/pineapple`  -> `/opt/pineapple/etc`     (DB, flags, estado; la DB queda en
  `/opt/pineapple/etc/pineapple.db` como exige el port, SIN editar los ~40 paths
  `/etc/pineapple/...` del panel)

Evidencia: todo lo de "VALIDADO" se probó en este host (PHP 8.3.6 aarch64 + SQLite3,
Python 3.12) sirviendo el panel real con `php -S` y symlinks idénticos al device.

---

## 1. Estructura entregada

```
extra-tools/pineapple-port/
├── panel/                 # panel portado (copia de la fuente + ediciones mínimas)
├── compat/                # shims (NO se toca el panel para estos)
│   ├── uci                #   UCI  -> store propio en /opt/pineapple/etc/uci/
│   ├── init.d-dispatcher  #   /etc/init.d/<svc> -> systemctl <unit>
│   ├── uclient-fetch      #   -> curl
│   ├── opkg               #   -> apt
│   ├── wifi               #   /sbin/wifi (netifd) -> hostapd regen + systemctl
│   └── iwinfo             #   -> iw (emula el texto que el panel parsea)
├── net/
│   ├── nginx.conf         # server :1471, root /pineapple, fastcgi a php-fpm
│   └── php-fpm-pineapple.conf  # pool con compat/ primero en PATH + UCI dir
├── etc/
│   ├── uci/               # seed del datamodel UCI (valores por defecto)
│   ├── pineapple.db.seed  # DB seed (auth bcrypt + tablas del panel)
│   └── pineap.conf.seed   # template de /tmp/pineap.conf (daemon config)
├── src/pineapd/ src/pineap/   # daemon y CLI nativos (ya reimplementados)
└── docs/                  # este informe + los RE previos
```

---

## 2. Datamodel UCI que implementa el shim `compat/uci`

Claves grepeadas del panel (lee/escribe). El shim las persiste en un archivo por
package bajo `/opt/pineapple/etc/uci/<package>` (formato `clave=valor`, una por
línea; `@config[0].opt` = la opción de la sección anónima; listas = claves
repetidas, `get` las devuelve con espacios). `commit` es no-op con éxito (el store
escribe al vuelo). `get` de clave inexistente: stdout vacío + exit 1 (como uci real).

### package `pineap`  (config del daemon PineAP; `pineap.@config[0].*`)
| opción | uso | valores |
|---|---|---|
| karma | asociaciones Karma | on/off (uciSet) · 0/1 (seed) |
| autostart | arranque automático de pineapd | 0/1 |
| logging | log de eventos | on/off |
| beacon_responses | responder a probes | on/off |
| broadcast_ssid_pool | emitir el pool de SSIDs | on/off |
| capture_ssids | harvester de SSIDs | on/off |
| connect_notifications | notificar conexiones | on/off |
| disconnect_notifications | notificar desconexiones | on/off |
| beacon_interval | intervalo de beacons | LOW/NORMAL/AGGRESSIVE |
| beacon_response_interval | intervalo de respuestas | LOW/NORMAL/AGGRESSIVE |
| pineap_interface | interfaz del AP malicioso | wlanX |
| pineap_source_interface | interfaz fuente | wlanX |
| pineap_mac | MAC source | aa:bb:.. |
| target_mac | MAC target (deauth) | aa:bb:.. |
| mac_filter | modo filtro de clientes | white/black |
| ssid_filter | modo filtro de SSID | white/black |
| pineape_passthrough | passthrough enterprise | 0/1 |
| ssid_db_path | ruta DB de SSIDs | /opt/pineapple/etc/pineapple.db |
| recon_db_path | ruta DB de recon | idem |
| hostapd_db_path | ruta DB hostapd | idem |

### package `wireless`  (`wireless.@wifi-iface[N].*`, `wireless.radioM.*`)
Índices de iface: **[0]=Open AP, [1]=Management AP, [2]=cliente/wwan**.
| opción | notas |
|---|---|
| @wifi-iface[0].ssid / .disabled / .hidden / .maxassoc | Open AP |
| @wifi-iface[1].ssid / .key / .disabled / .hidden | Management AP (WPA2) |
| @wifi-iface[2].ssid / .disabled / .encryption / .macaddr / .network / .mode / .key | cliente (ClientMode) |
| @wifi-iface[{id}].network/.mode/.ssid/.encryption/.key/.macaddr | id dinámico (ClientMode/Interfaces) |
| radio0.channel | canal del AP |
| radio0.country / radio1.country | country code (Setup) |

### package `firewall`
`allowssh.enabled`, `allowui.enabled`, `allowws.enabled` (0/1).

### package `system`
`@system[0].hostname`, `@system[0].timezone`.

### package `network`
`lan.ipaddr` (usado por Configuration landing-page redirect y hostname).

### package `landingpage`
`@settings[0].autostart` (0/1).

### package `reporting`
`@settings[0].{log,client,survey,tracking,save_report,send_email,clear_log,duration,interval}`
y `@ssmtp[0].{server,port,username,password,from,to,domain,tls,starttls}` (email de reportes).

> Nota: hay un **segundo** store de config, `/tmp/pineap.conf` (formato `key = value`),
> que lee `pineapd-ng` al arrancar y que `PineAPHelper` escribe directamente cuando
> el daemon está caído. El unit systemd de pineapd (FASE 4) debe renderizar
> `/tmp/pineap.conf` desde el store UCI `pineap` en su `ExecStartPre`. Seed/template
> en `etc/pineap.conf.seed`.

---

## 3. Shims (compat/) — qué reemplaza cada uno, SIN tocar el panel

| shim | reemplaza a | mapeo | usos en el panel |
|---|---|---|---|
| `uci` | OpenWrt UCI | store propio en `/opt/pineapple/etc/uci/` | ~44 call sites (uciGet/Set/AddList + exec directos) |
| `init.d-dispatcher` | procd `/etc/init.d/<svc>` | `systemctl <action> <unit>` | pineapd, dnsmasq, firewall, cron, sshd |
| `uclient-fetch` | OpenWrt uclient-fetch | `curl` (mapea -q/-T/-O) | pineapple.php:147, Advanced, ModuleManager |
| `opkg` | OpenWrt opkg | `apt-get` (update/install/remove) | Module.php:118-119 |
| `wifi` | netifd `/sbin/wifi` | regen hostapd + `systemctl restart pineapple-ap` | AccessPoint, ClientMode, Interfaces, PineAP, Setup |
| `iwinfo` | OpenWrt iwinfo | `iw` (emula `freqlist` y `scan` que el panel parsea) | AccessPoint.php:33, ClientMode.php:24 |

### Mapa init.d -> systemd unit (lo documenta el propio dispatcher)
| svc que llama el panel | unit |
|---|---|
| pineapd | `pineapd.service` (daemon nativo pineapd-ng) |
| dnsmasq | `dnsmasq.service` (Kali) |
| firewall | `pineapple-firewall.service` (ruleset del port) |
| cron | `cron.service` (en Debian el unit es `cron`, no `cronie`) |
| sshd | `ssh.service` (en Debian el unit es `ssh`) |

Instalación de los init.d (la hace el instalador):
```
for s in pineapd dnsmasq firewall cron sshd; do
  ln -sf /opt/pineapple/compat/init.d-dispatcher /etc/init.d/$s
done
```

### at / atd (execBackground)
`pineapple.php` usa `echo cmd | /usr/bin/at now`. **Decisión: usar `at`** (existe en
Kali). Es más robusto que `nohup &` porque es exactamente lo que el panel espera
(fire-and-forget con el proceso desacoplado del request), y Setup.php también
encola comandos con `at now`. Dep: `apt install at && systemctl enable --now atd`.
(Si se prefiriera no depender de atd, la alternativa sería editar `execBackground`
a `nohup sh -c "$command" >/dev/null 2>&1 &` — un solo punto de cambio — pero se
optó por NO tocar el panel.)

### Paths OpenWrt hardcodeados (punto 9 del encargo)
- `/tmp/sysinfo/board_name`: lo crea el instalador con un valor (ej `pineapple-g82`).
- `/etc/config/wireless`, `/etc/config/fstab`: los maneja el shim `uci` (store).
  Las líneas `wifi config >> /etc/config/wireless` son no-op inofensivas (el shim
  `wifi config` no emite nada).
- `led`/`blink`/`jffs2reset`/`sysupgrade`: NO aplican al G82. Stubear como no-ops
  seguros en `/opt/pineapple/compat` si alguna vez se invocan (Setup.php:200-201
  `killall blink` / `led reset` ya fallan silenciosamente vía exec sin romper flujo).

---

## 4. Ediciones en el panel (SOLO auth + python2->3)

### 4.1 AUTH (seguridad) — se edita a propósito
Antes: validaba contra `/etc/shadow` asumiendo `crypt $1$` (MD5). En Kali root usa
`$6$` o está deshabilitado -> inservible. Ahora: credenciales propias en la SQLite
(`/etc/pineapple/pineapple.db` -> `/opt/pineapple/etc/pineapple.db`) con
`password_hash`/`password_verify` (bcrypt `$2y$` por `PASSWORD_DEFAULT`).

- `api/Authentication.php`
  - crea tabla `auth(username UNIQUE, password_hash)`.
  - `ensureDefaultCredentials()`: siembra `root` / `pineapplesareyummy` si la tabla
    está vacía (configurable en la instalación / se cambia en setup).
  - `verifyPassword()`: `password_verify` contra la DB (ya no lee `/etc/shadow`).
- `api/SystemModule.php` `changePassword($current,$new)`: verifica el actual con
  `password_verify` y actualiza el hash en la DB (ya no reescribe `/etc/shadow`).
- `api/Setup.php` `changePassword()`: `INSERT OR REPLACE` del hash nuevo en `auth`
  (ya no reescribe `/etc/shadow`).

**Credenciales por defecto:** usuario `root`, password `pineapplesareyummy`
(cambiar en el instalador o en el Setup wizard). Constantes en Authentication.php.

### 4.2 Python 2 -> 3 (obligatorio; no hay python2 en Kali)
`modules/Recon/api/` (WebSocket de recon en vivo, puerto 1337):
- `wsauth.py`: ya era 3-clean; normalizado.
- `WebSocketsHandler.py`: `SocketServer`->`socketserver`; `mimetools.Message`+
  `StringIO`->`email.message_from_string`; `print x`->`print(x)`;
  `except E, e`->`except E as e`; framing WebSocket a **bytes**;
  `sha1(...).hexdigest().decode('hex')`->`sha1(...).digest()`+`b64encode`.
- `dbhandler.py`: `import thread`->`_thread`; `.iteritems()`->`.items()`;
  `except E, e`->`as e`; `check_output` ahora devuelve bytes -> `.decode()` antes
  del `in`.
- `reconpp.py`: `SocketServer`->`socketserver`; `setDaemon(True)`->`.daemon=True`;
  shebang `#!/usr/bin/env python3`; además se corrigió un bug del original (el
  handle del lockfile se perdía y liberaba el flock al retornar).
- `modules/Recon/api/module.php`: `const CLI = 'python'` -> `'python3'` (único
  cambio PHP por el port py3; el panel invoca `python3 .../reconpp.py <db> <scanID>`).

> Todo lo demás del panel (UCI, init.d, fetch, opkg, wifi, iwinfo, at) queda vía
> shim, SIN editar el panel.

---

## 5. Acoplamiento con el CLI nativo `pineap` (pineap-ng)

El panel invoca `pineap /tmp/pineap.conf <cmd>` (desde PATH) y `/usr/bin/pineap`
(PineAPHelper, Recon module.php). El daemon se referencia como `/usr/sbin/pineapd`
(PineAPHelper `CLI_PINEAPD`, `pgrep /usr/sbin/pineapd`). Por lo tanto el instalador
debe colocar:
- `src/pineap/pineap-ng`   -> `/usr/bin/pineap` (y en PATH)
- `src/pineapd/pineapd-ng` -> `/usr/sbin/pineapd`

Verificado: `pineap-ng` es ELF aarch64 nativo, `version` imprime `PineAP version 3.2`
/ commit `de3598e095` idéntico al original. El path del binario que usa el panel es
correcto (no requiere cambios en el panel).

---

## 6. nginx + php-fpm

- `net/nginx.conf`: `listen 1471`, `root /pineapple`, `/api` y `/api/` -> 
  `api/index.php` (fastcgi), `.php` genérico a php-fpm, estáticos + fallback SPA.
  El prefijo anti-hijacking `)]}',\n` lo emite el PHP (API.php finalize), nginx no
  hace nada especial. `fastcgi_pass` por unix socket (alternativa TCP comentada).
- `net/php-fpm-pineapple.conf`: pool `pineapple`, socket
  `/run/php/php-fpm-pineapple.sock`, y lo CLAVE:
  `env[PATH] = /opt/pineapple/compat:...` (shims primero) y
  `env[PINEAPPLE_UCI_DIR] = /opt/pineapple/etc/uci`. `disable_functions` vacío
  (el panel usa `exec()`). Sesiones en `/opt/pineapple/etc/sessions`.

---

## 7. VALIDACIÓN (ejecutada en este host)

1. **php -l**: los 33 `.php` del panel sin errores de sintaxis (incluye los 3 de
   auth editados).
2. **Shim uci**: `set`/`get`/`commit`/`add_list`(dedup)/`del`/`show`, valores con
   espacios, listas space-joined, `get` inexistente -> rc=1 sin stdout, persistencia.
3. **py_compile**: los 4 `.py` de Recon compilan en Python 3.12. Sin py2-isms
   fuera de comentarios.
4. **Auth nueva** (unit test PHP): login default OK, password incorrecto rechazado,
   usuario inexistente rechazado, changePassword invalida la vieja y exige la actual.
   Hash = bcrypt `$2y$`.
5. **Panel en vivo** (`php -S` + symlinks `/pineapple`, `/etc/pineapple`, PATH con
   shims):
   - `GET /` -> HTTP 200, carga index.html (AngularJS `ng-app="pineapple"`).
   - `POST /api/ checkAuth` -> `)]}',\n{"error":"Not Authenticated"}` (prefijo OK).
   - `login` wrong -> `{"logged_in":false}`; `login` default -> `{"logged_in":true}`.
   - `modules getModuleList` -> lista los 13 módulos.
   - `PineAP getPineAPSettings` -> lee TODO el datamodel `pineap.@config[0].*` vía
     el shim uci y devuelve los valores del seed.
   - `PineAP setPineAPSettings` -> `{"success":true}`; el shim persiste
     `karma=on/logging=on/beacon_interval=LOW` y PineAPHelper escribe `/tmp/pineap.conf`
     (ruta correcta con daemon caído).

---

## 8. Dependencias apt para el G82

```
apt install \
  nginx-light \
  php-fpm php-cli php-sqlite3 \
  sqlite3 \
  python3 \
  at \
  curl \
  iw \
  hostapd \
  dnsmasq
systemctl enable --now atd
```
Notas:
- El port se probó con **php8.3** (`php8.3-fpm`, `php8.3-sqlite3`). Ajustar el pool
  path (`/etc/php/8.3/fpm/pool.d/`) a la versión que traiga NetHunter.
- `iw`, `hostapd`, `dnsmasq`, `at` son los que consumen los shims (iwinfo/wifi, at).
- `hostapd_cli` (de `hostapd`) lo usan los side-effects de karma/filtros del CLI.

---

## 9. Honestidad sobre lo que requiere el G82 para probar EN VIVO

Lo validado aquí (lint, shim uci, py_compile, auth, y el panel sirviendo login +
API + lectura/escritura UCI vía HTTP) es **independiente del hardware**: confirma
que el panel y la capa de compat funcionan sobre PHP/SQLite/Python aarch64.

Lo que NO se puede probar fuera del G82 y requiere el device real:
- **Radio/WiFi real**: `iwinfo scan`/`freqlist` dependen de `iw` + un adaptador en
  modo apropiado; aquí sólo se validó el *parser* del shim (fallback sin `iw`).
- **AP malicioso / Karma / deauth / inject**: requieren `pineapd-ng` corriendo con
  acceso a un adaptador WiFi con soporte de inyección/monitor y `hostapd` real.
- **init.d-dispatcher -> systemctl**: requiere las units de FASE 4 instaladas
  (`pineapd.service`, `pineapple-ap.service`, `pineapple-firewall.service`). Aquí se
  validó el *mapeo de comandos* (con `SYSTEMCTL=echo`), no la activación real.
- **`wifi` apply**: depende de `/opt/pineapple/compat/wifi-apply` (regenerador de
  hostapd de FASE 4) y del servicio AP; aquí degrada a no-op seguro.
- **Recon en vivo (WebSocket :1337)**: `reconpp.py` compila y el framing es correcto,
  pero el flujo end-to-end necesita `pineapd-ng get_status` devolviendo `scanID` y la
  DB poblándose durante un scan real.
- **Adaptador WiFi**: el G82 interno no inyecta; se asume un adaptador USB externo
  (p.ej. basado en MT7612U/RT2800) con soporte monitor/injection, mapeado como
  `wlan1` (coincide con el seed `pineap_interface=wlan1`).
