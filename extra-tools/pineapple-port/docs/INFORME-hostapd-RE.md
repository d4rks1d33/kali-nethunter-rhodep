# INFORME RE: hostapd Hak5 (karma + PineAPE Enterprise) y el port a aarch64

Última pieza del core ofensivo del port: el hostapd parcheado de Hak5 que da
(1) el **karma** (evil-twin que responde a probes y asocia clientes) y (2) el
**PineAPE Enterprise** (evil-twin WPA2-EAP que roba credenciales). Este informe
documenta el RE del binario original y la solución construida para el G82/Kali.

---

## 1. RE del `wpad` original (MIPS BE)

Binario: `rootfs-base/usr/sbin/wpad` — ELF 32-bit MSB MIPS, musl, 739 KB.
Es hostapd + 2 juegos de parches (confirmado por los créditos en strings):

```
Original Karma patches by Robin Wood - robin@digininja.org
PineAP and Karma improvements for the WiFi Pineapple by Sebastian Kinne and Hak5
PineAP and Karma patches updated by Marc Egerton - realloc.me
```

### 1.1 Comandos del control-interface (hostapd_cli)

Recuperados de la región de strings `0x4b18c0–0x4b1e00` (tabla de punteros en
`0x48e1d4+`, que es el dispatcher `hostapd_ctrl_iface_receive_process`) y del
disassembly MIPS. Semántica deducida de los mensajes de respuesta adyacentes:

| comando hostapd_cli | arg | respuesta | semántica |
|---|---|---|---|
| `KARMA_ENABLE` | — | `KARMA: ENABLED` | activa karma (responde probes de cualquier SSID) |
| `KARMA_DISABLE` | — | `KARMA: DISABLED` | desactiva karma |
| `KARMA_STATE` | — | `ENABLED`/`DISABLED` (+`SILENT_STATE`) | consulta estado |
| `KARMA_GET_SSID` | — | `<ssid>` | SSID actual del AP |
| `KARMA_CHANGE_SSID ` | `<ssid>` | `CHANGED` / `KARMA: Default SSID changed to %s` | cambia el SSID del AP en caliente |
| `KARMA_WHITE` | — | `KARMA: SSID filter now in white list mode` | filtro SSID = whitelist |
| `KARMA_BLACK` | — | `KARMA: SSID filter now in black list mode` | filtro SSID = blacklist |
| `KARMA_BLACK_WHITE` | — | `BLACK`/`WHITE` | consulta modo de filtro SSID |
| `KARMA_MAC_WHITE` | — | `KARMA: MAC filter now in white list mode` | filtro MAC = whitelist |
| `KARMA_MAC_BLACK` | — | `KARMA: MAC filter now in black list mode` | filtro MAC = blacklist |
| `KARMA_MAC_BLACK_WHITE` | — | `BLACK`/`WHITE` | consulta modo de filtro MAC |
| `KARMA_LOGGING_ENABLE` | — | — | logging de asociaciones karma |
| `PINEAP_RECONNECT` | `<mac>` | — | fuerza la reconexión de un cliente (deauth→reasoc) |
| `PINEAPE_ENABLE` | — | `PineAPE: ENABLED` | activa el subsistema Enterprise (EAP) |
| `PINEAPE_DISABLE` | — | `PineAPE: DISABLED` | desactiva Enterprise |
| `PINEAPE_ENABLE_STATUS` | — | `ENABLED`/`DISABLED` | consulta Enterprise (lo usa el panel) |
| `PINEAPE_AUTH_PASSTHROUGH_ON` | — | `PineAPE: Auth passthrough ON` | acepta CUALQUIER credencial EAP |
| `PINEAPE_AUTH_PASSTHROUGH_OFF` | — | `PineAPE: Auth passthrough OFF` | desactiva passthrough |
| `PINEAPE_AUTH_PASSTHROUGH_STATUS` | — | `ENABLED`/`DISABLED` | consulta passthrough (panel) |

`pineap-ng` (port) invoca: `karma_enable/karma_disable` (hybrid socket+cli),
`karma_black/white`, `karma_mac_black/white`. El panel PHP invoca:
`hostapd_cli -i wlan0-2 pineape_enable_status` y `… pineape_auth_passthrough_status`.

### 1.2 PineAPE Enterprise — captura de credenciales

El wpad escribe las credenciales EAP DIRECTAMENTE en SQLite (`/etc/pineapple/pineape.db`).
Strings SQL recuperados (CREATE TABLE y los INSERT con bind params):

```sql
CREATE TABLE IF NOT EXISTS basic (
  id INT PRIMARY KEY,
  log_time TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  type TEXT, identity TEXT, password TEXT,
  verified INT NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS chalresp (
  id INT PRIMARY KEY,
  log_time TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  type TEXT, username TEXT, challenge BLOB, response BLOB,
  verified INT NOT NULL DEFAULT 0);

INSERT INTO basic(type, identity, password) VALUES(?, ?, ?);
INSERT INTO chalresp(type, username, challenge, response) VALUES(?, ?, ?, ?);
```

* `chalresp` → EAP challenge/response (MSCHAPv2 / EAP-TTLS/MSCHAPv2 ⇒ NetNTLM).
  `challenge` y `response` son BLOB (bytes crudos). El panel exporta
  `username:$NETNTLM$hex(challenge)$hex(response)` (JTR) y
  `username::::hex(response):hex(challenge)` (hashcat).
* `basic` → identidad+password en claro (PAP/GTC).

El binario enlaza `libsqlite3.so.0` (`sqlite3_open_v2/prepare_v2/bind_text/step`).
El EAP lo termina hostapd mismo (servidor EAP integrado, sin RADIUS externo):
strings `EAP-MSCHAPV2: Authentication succeeded`, `EAP-PEAP`, `EAP-TTLS`, etc.

### 1.3 El AP Enterprise y sus ficheros (`/etc/pineape`)

* `hostapd-pineape.eap_user` — fichero eap_user de hostapd:
  ```
  *       PEAP,TTLS,TLS,FAST
  "x"     TTLS-PAP,TTLS-CHAP,TTLS-MSCHAP,MSCHAPV2,MD5,GTC,TTLS,TTLS-MSCHAPV2  "x"  [2]
  #"x"    GTC                                                                 "x"  [2]
  #"x"    MSCHAPV2                                                            "x"  [2]
  ```
  La fase-1 (`*`) ofrece PEAP/TTLS/TLS/FAST; la fase-2 comodín (`"x"`) acepta
  cualquier método y cualquier password (`"x"`). Las líneas 5/6 comentadas son
  los "downgrade" que el panel activa/desactiva (toggleComment líneas 4/5/6):
  catch-all / solo-GTC / solo-MSCHAPV2.
* `certs/` — bootstrap de FreeRADIUS/OpenWrt:
  `configure.sh` escribe los subject fields en los `.cnf`; `bootstrap.sh` genera
  con OpenSSL el CA (`ca.pem/ca.key/ca.der`), el server cert firmado
  (`server.key -nodes`, `server.csr`, `server.crt`, `server.p12`, `server.pem`)
  y la DB del CA (`index.txt`, `serial`). `dh` ya viene pregenerado.
  `clean.sh`/`reset.sh` limpian y restauran los `.cnf.master`.

El AP Enterprise original es la 3ª wifi-iface (`@wifi-iface[2]` ⇒ `wlan0-2`),
WPA2-EAP, con `eap_server=1`, `ca_cert/server_cert/private_key` apuntando a
`/etc/pineape/certs` y `eap_user_file=/etc/pineape/hostapd-pineape.eap_user`.

---

## 2. Decisión de arquitectura: hostapd-mana + wrapper

El parche PineAPE de Hak5 **no está publicado**; reconstruirlo byte-exacto del
binario MIPS y forward-portearlo a hostapd moderno es alto riesgo e imposible de
validar sin el teléfono.

`hostapd-mana` (sensepost) es la **continuación open-source del mismo linaje
digininja-karma** y **ya incluye las dos mitades** que daba el binario Hak5:

* **karma/MANA**: responde probes dirigidos y broadcast, asocia cualquier cliente
  (`MANA_ENABLE`, `LOUD_ENABLE`, `MANAACL_*`, `MANA_CHANGE_SSID`, `MANA_STATE`).
* **WPE (EAP harvesting)**: el linaje hostapd-wpe integrado — `WPE_ENABLE` +
  `EAPSUCCESS_ENABLE` (acepta-cualquier-credencial = el auth passthrough) y
  emite las credenciales MSCHAPv2 (NetNTLM) por el callback WPE.

Está empaquetado en **Kali `main`, arch `any`** (`hostapd-mana`, verificado en
pkg.kali.org: `2.6.5+git…-0kali1`), o sea `apt install`-able en el arm64 del G82.

**Única diferencia**: la nomenclatura de comandos. mana usa `MANA_*`/`WPE_*`;
`pineap-ng` y el panel hablan `karma_*`/`pineape_*` (Hak5). Se resuelve con un
**wrapper `hostapd_cli`** que traduce los verbos Hak5→mana. Así `pineap-ng` y el
panel PHP **no se tocan**. Las credenciales se importan del log WPE de mana a
`pineape.db` con `net/pineape-creds-import`, reproduciendo las tablas exactas.

### 2.1 Mapa de traducción (compat/hostapd_cli)

| Hak5 (lo que piden pineap-ng/panel) | mana (comando real) |
|---|---|
| `karma_enable` | `MANA_ENABLE` (SOLO karma normal — LOUD ya NO se activa aquí) |
| `karma_disable` | `MANA_DISABLE` + `LOUD_DISABLE` |
| `karma_loud_enable` / `karma_loud_disable` | `LOUD_ENABLE` / `LOUD_DISABLE` (opt-in; lo dispara `broadcast_pool on/off`) |
| `karma_state` | `MANA_STATE` (normalizado a `ENABLED`/`DISABLED`) |
| `karma_get_ssid` | `MANA_GET_SSID` |
| `karma_change_ssid <ssid>` | `MANA_CHANGE_SSID <ssid>` |
| `karma_black` / `karma_white` | `MANAACL_DISABLE` / `MANAACL_ENABLE` |
| `karma_mac_black` / `karma_mac_white` | `MANAACL_DISABLE` / `MANAACL_ENABLE` |
| `pineape_enable` / `pineape_disable` | `WPE_ENABLE` / `WPE_DISABLE` |
| `pineape_enable_status` | `WPE_MODE` → `ENABLED`/`DISABLED` |
| `pineape_auth_passthrough_on` / `_off` | `EAPSUCCESS_ENABLE` / `EAPSUCCESS_DISABLE` |
| `pineape_auth_passthrough_status` | `EAPSUCCESS_MODE` → `ENABLED`/`DISABLED` |
| `pineap_reconnect <mac>` | `DEAUTHENTICATE <mac>` |
| (cualquier otro: STATUS, GET_CONFIG, …) | pass-through literal |

Además remapea el iface legacy: `wlan0`/`wlan1` → iface AP real (`wlan1`),
`wlan0-2` (el VAP Enterprise del original) → VAP del port (`wlan1_0`). Así el
hardcode `-i wlan0-2` del panel funciona sin editar el panel.

### 2.2 Formato de credenciales mana→pineape.db

El WPE de mana (RE de `src/eap_server/eap_server.c`) escribe en `mana_credout`:

```
[EAP-MSCHAPV2 JTR]	<user>:$NETNTLM$<chal_hex>$<resp_hex>:::::::
[EAP-MSCHAPV2 HASHCAT]	<user>::::<resp_hex>:<chal_hex>
[EAP-TTLS/CHAP JTR user=<user>]	$chap$<id>*<salt_hex>*<hash_hex>
[EAP-TTLS/PAP]	<user>:<password>
```

`net/pineape-creds-import` tail-ea ese fichero e inserta:
* MSCHAPv2 → `chalresp(type, username, challenge, response)` con los bytes
  crudos (de `bytes.fromhex`), igual que el wpad.
* CHAP → `chalresp` (challenge=salt, response=hash).
* PAP → `basic(type, identity, password)`.

El panel lee EXACTAMENTE esas tablas (`getEnterpriseData`, `downloadJTRHashes`,
`downloadHashcatHashes`), así que su vista Enterprise y los exports JTR/hashcat
funcionan sin cambios.

---

## 3. Archivos del port (nuevos)

```
vendor/hostapd/
  build-mana.sh                 apt | prebuilt | source (aarch64)
  mana.config                   .config de hostapd (karma+EAP server+WPE+ctrl)
  hostapd-karma.conf.in         plantilla AP karma (wlan1)
  hostapd-enterprise.conf.in    plantilla VAP Enterprise (WPA2-EAP+WPE)
  prebuilt/aarch64/             hostapd-mana + hostapd_cli-mana (cross-build validado)
compat/
  hostapd_cli                   wrapper Hak5->mana (traducción + iface remap)
  wifi-apply                    render de los hostapd.conf desde el UCI store
net/
  pineape-creds-import          importador WPE log -> pineape.db (chalresp/basic)
etc/pineape/
  hostapd-pineape.eap_user      eap_user (portado del original)
  certs/{configure,bootstrap,clean,reset}.sh  + *.cnf/*.master/xpextensions/dh
systemd/
  pineapple-ap.service          hostapd-mana (ExecStartPre=wifi-apply)
  pineape-creds.service         importador de credenciales
```

`install.sh`: instala hostapd-mana (apt→prebuilt→source), los certs+eap_user,
symlinks `/etc/pineape` y `/usr/local/sbin/hostapd_cli` (gana sobre el stock),
semilla `pineape.db`, y añade las 2 units al `pineapple.target`.

---

## 4. Validación realizada (sin WiFi real)

* **Cross-build aarch64**: `hostapd-mana` y `hostapd_cli-mana` compilados con
  `aarch64-linux-gnu-gcc` desde fuente (mana.config). `file` ⇒ ELF aarch64.
  Strings del binario confirman `MANA_ENABLE/WPE_ENABLE/EAPSUCCESS_*` + claves
  `enable_mana/mana_wpe/mana_eapsuccess/mana_credout` + formatos `$NETNTLM$`.
* **Kali apt**: `hostapd-mana` confirmado en Kali main arch any (pkg.kali.org).
* **Certs**: `configure.sh`+`bootstrap.sh` en /tmp ⇒ CA + server cert válidos,
  `openssl verify -CAfile ca.pem server.crt` ⇒ OK, `ca.pem` con `CA:TRUE`.
* **Wrapper**: contra un stub y contra el `hostapd_cli-mana` aarch64 REAL (qemu):
  `karma_enable`→`MANA_ENABLE`+`LOUD_ENABLE`, `pineape_*`→`WPE_*`/`EAPSUCCESS_*`,
  remap `wlan0-2`→`wlan1_0`, status normalizado a `ENABLED`/`DISABLED`.
* **Importador**: log mana de prueba (NetNTLM/CHAP/PAP) ⇒ filas correctas en
  `chalresp`/`basic`; export JTR/hashcat del panel reproduce el hash NetNTLM.
* **Coherencia**: `pineap-ng` hace `hostapd_cli -i wlan1 karma_*` ⇒ lo soporta el
  stack (wrapper→mana). Schema `pineape.db` idéntico al del wpad original.
* **Sintaxis**: `sh -n`/`bash -n` de todos los scripts, `py_compile` del
  importador, `install.sh --dry-run` completo OK.

## 5. Qué requiere el G82 + Archer para probar en vivo

Lo validado aquí es compilación/sintaxis/certs/traducción/schema. El karma y el
EAP harvesting REALES requieren hardware:

* **wlan1 = TP-Link Archer USB** con driver nl80211 en modo AP (el `enable_mana`
  y los VAP `bss=` necesitan un adaptador que soporte AP+múltiples BSS; no todos
  los chipsets lo hacen — verificar `iw list` → "valid interface combinations").
* **wlan0 = wifi interno** en monitor (lo maneja pineapd, no hostapd).
* Un cliente víctima que haga probe (karma) o que intente asociarse al SSID
  Enterprise con un supplicant mal configurado (que no valide el server cert) →
  es ahí donde se capturan las credenciales NetNTLM/MSCHAPv2.
* El passthrough (`mana_eapsuccess`) hace que el cliente "conecte" aunque la
  password sea incorrecta; sin él, mana igual captura el challenge/response
  (suficiente para crackear offline), solo que el cliente ve un fallo de auth.

En resumen: el stack queda cableado y validado en software; la eficacia real del
evil-twin depende del chipset del Archer (soporte AP/VAP) y del comportamiento
del supplicant de la víctima.
