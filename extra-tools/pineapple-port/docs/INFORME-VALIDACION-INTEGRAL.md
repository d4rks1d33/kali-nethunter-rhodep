# INFORME DE VALIDACIÓN INTEGRAL — WiFi Pineapple port (Kali/aarch64)

Validación de coherencia entre TODAS las fases antes de instalar en el Motorola
G82 real. Ejecutada de verdad con gcc/make/php/sqlite3/python3/systemd-analyze
sobre un host aarch64 (native gcc = aarch64, qemu no necesario).

## Resultado: COHERENTE Y LISTO PARA INSTALAR (ver "Pendiente de hardware").

---

## 1. Compilación aarch64
- `make` en `src/pineapd` y `src/pineap`: 0 warnings, 0 errors.
- `pineapd-ng` y `pineap-ng`: ELF 64-bit LSB PIE, ARM aarch64. OK.

## 2. Protocolo socket (lo más crítico)
- `PINEAP_CMD_LEN = 4097` en AMBOS headers (pineapd.h, pineap.h). NO hay "65"
  residual en ningún lado (grep de 65/0x41 en código de socket = vacío).
- Comandos 4..21 idénticos en ambos; dispatch server = `switch(cmd)` con CMD_*.
- Byte-oracle (`validate_bytes.sh`): los 33 comandos que emite pineap-ng son
  BYTE-IDÉNTICOS a las capturas del cliente ORIGINAL (`/tmp/opencode/proto-re/
  recv_*.bin`). PASS=33 FAIL=0.
- End-to-end REAL pineap-ng <-> pineapd-ng (`run_e2e.sh`): 13/13 PASS.
  - `get_status` JSON byte-exacto vs captura del daemon ORIGINAL.
  - add/list/clear ssids, setOpt capture_ssids, quirk ap_channel (184549376),
    beacon_interval LOW, set_source: todos OK.

## 3. Schema SQLite
- `--initdbs` crea aps/clients/scan_ids (recon), log/tracking (log),
  ssids (pineapple) con columnas BYTE-IDÉNTICAS a las del binario original
  (strings `CREATE TABLE ...`).
- Todas las columnas que consultan los módulos del panel (Recon, PineAP,
  Clients, Logging, Tracking, Reporting) existen en el schema. Coherente.

## 4. Cadena panel -> pineap-ng -> pineapd-ng -> DB
- PineAPHelper invoca `pineap /tmp/pineap.conf <cmd>`; pineap-ng soporta TODOS
  los comandos que el panel manda (karma, logging, broadcast_pool,
  beacon_responses, capture_ssids, connect/disconnect_notifications,
  beacon_interval, beacon_response_interval, set_source, set_target, deauth).
- Config-mode (`strstr "pineap.conf"`) correcto.
- Panel lee las DBs vía UCI (recon/hostapd/ssid_db_path) = mismo fichero que
  escribe pineapd-ng (`/opt/pineapple/etc/pineapple.db`). Coherente.

## 5. Ciclo shim uci (panel -> gen-config -> pineapd)
- Probado end-to-end: `uci set` (shim) -> `gen-pineap-config.sh` lee el store
  -> genera `/tmp/pineap.conf` -> pineapd-ng lo parsea y hace round-trip con
  config_save() conservando los valores. OK.

## 6. Auth
- bcrypt (`password_hash`/`password_verify`, PASSWORD_DEFAULT). Test PHP real
  contra DatabaseConnection: login correcto PASS, password incorrecto/usuario
  inexistente RECHAZADOS, changePassword invalida la vieja, quoting a prueba de
  inyección. OK.

## 7. Python3 Recon
- Los 4 .py compilan en py3 (py_compile). WebSocketsHandler bindea y acepta en
  :1337 (connect_ex=0). Token/lock OK. Branch JSON de scan-activo verificado.

## 8. systemd + install
- `systemd-analyze verify` de las 8 units: sintaxis válida (solo avisos de
  binarios inexistentes pre-instalación, que se resuelven tras install.sh).
- `bash -n`/`sh -n` de todos los scripts: OK.
- `install.sh --dry-run` coherente; uninstall reversible; paths coherentes
  (pineapd /usr/sbin, pineap /usr/bin, panel /opt/pineapple/panel, symlinks
  /pineapple y /etc/pineapple, init.d shims).

## 9. Residuos OpenWrt
- Cada comando OpenWrt que invoca el panel tiene shim en compat/ y el pool
  php-fpm pone compat/ PRIMERO en el PATH.
- Dispatcher init.d cubre los 5 servicios (pineapd, dnsmasq, firewall, cron,
  sshd) -> units systemd.

## 10. Coherencia de interfaz
- Monitor/captura: `pineap_interface` (UCI=wlan1 -> gen-config -> pineapd;
  pineapd-iface.sh conmuta wlan1 a monitor). Coherente.
- AP/hostapd: pineap-ng usa `hostapd_cli -i $PINEAP_IFACE` (default wlan0).

---

## BUGS/INCONSISTENCIAS ENCONTRADAS Y REPARADAS

1. **Shims OpenWrt faltantes** (`led`, `logread`, `resetssids`). El panel los
   invocaba sin cobertura:
   - `compat/resetssids`: reimplementación fiel del ELF original
     (`UPDATE ssids SET new_ssid = 0 WHERE new_ssid = 1`), con sqlite3. Probado.
   - `compat/led`: no-op de éxito (G82 no tiene el LED de OpenWrt). `led reset`→0.
   - `compat/logread`: mapea a journalctl (fallback /var/log/syslog). Probado.

2. **Invocaciones por ruta ABSOLUTA que saltaban el PATH de compat**:
   - `exec('/usr/sbin/resetssids')` (PineAP) y `exec('/sbin/wifi ...')` (Setup).
     Añadidos symlinks en `install.sh` (`/usr/sbin/resetssids`->compat/resetssids,
     `/sbin/wifi`->compat/wifi) y su reversión en el uninstall.

3. **Interfaz AP no cableada end-to-end**: pineap-ng soportaba `$PINEAP_IFACE`
   pero el pool php-fpm no lo exportaba. Añadido `env[PINEAP_IFACE]=wlan0` al
   pool, coherente con el default de compilación de pineap-ng, con un único
   punto de edición documentado (la iface del Archer).

---

## PENDIENTE DE HARDWARE REAL (no es bug del port)

- **WiFi**: monitor mode, inyección, hostapd/karma — requieren la radio real
  (Archer como AP en wlan0, wifi interno como monitor wlan1). pineapd-iface.sh
  y las units son best-effort y no bloquean sin radio.
- **hostapd_cli karma_*/pineape_***: requieren el hostapd PARCHEADO de Hak5; el
  hostapd stock de Kali no trae esos subcomandos. Es dependiente de hardware/
  firmware, no un shim faltante.
- **PineAPe Enterprise** (`/etc/pineape/certs/*`, EAP evil-twin): subsistema de
  certificados + hostapd-enterprise NO portado. Feature avanzada fuera del core.
