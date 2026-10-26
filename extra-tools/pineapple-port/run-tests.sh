#!/bin/bash
# WiFi Pineapple port - automated end-to-end test runner (Motorola G82 / Kali)
#
# Conecta la TP-Link Archer por USB y corre:   sudo ./run-tests.sh
# Deja un log completo en /home/kali/pineapple-port/test-results.log
# Despues me avisas y me conecto a leer el log.
#
# Es SEGURO: arranca el stack, corre pruebas, y AL FINAL para todo (revierte la
# red) salvo que pases --keep para dejarlo corriendo.

set -u
LOG=/home/kali/pineapple-port/test-results.log
KEEP=0
[ "${1:-}" = "--keep" ] && KEEP=1

# --- helpers --------------------------------------------------------------
exec > >(tee "$LOG") 2>&1
hr(){ echo "============================================================"; }
sec(){ hr; echo "## $*"; hr; }
run(){ echo "\$ $*"; "$@" 2>&1; echo "[exit $?]"; echo; }
ok(){ echo "  -> $*"; }

if [ "$(id -u)" -ne 0 ]; then echo "Corré con sudo: sudo ./run-tests.sh"; exit 1; fi

echo "WiFi Pineapple port - test run $(date -Is)"
echo "host: $(uname -a)"
echo

# =========================================================================
sec "0. PRE-CHEQUEO DE HARDWARE"
run iw dev
echo "--- interface combinations (necesario para AP+VAP en el adaptador AP) ---"
run sh -c 'iw list | sed -n "/valid interface combinations/,/HT Capability/p" | head -40'
echo "--- supported interface modes ---"
run sh -c 'iw list | sed -n "/Supported interface modes/,/band/p" | head -30'
echo "--- adaptadores USB ---"
run sh -c 'lsusb | grep -iE "realtek|ralink|mediatek|tp-link|atheros|802.11|wire" || lsusb'
echo "NOTA: wlan0 = interno (monitor wlan0mon), wlan1 = Archer USB (AP)."
echo

# =========================================================================
sec "1. ARRANCAR EL STACK (systemctl start pineapple.target)"
run systemctl start pineapple.target
sleep 4
for u in pineapple-firstboot pineapple-net pineapple-dnsmasq pineapple-php-fpm \
         pineapple-nginx pineapd pineapple-ap; do
  printf "  %-26s " "$u:"
  systemctl is-active "$u.service" 2>/dev/null || echo "inactive"
done
echo
echo "--- units que fallaron (journal) ---"
for u in pineapple-net pineapple-dnsmasq pineapple-php-fpm pineapple-nginx pineapd pineapple-ap; do
  if [ "$(systemctl is-active "$u.service" 2>/dev/null)" != "active" ]; then
    echo ">>> $u NO activo:"; journalctl -u "$u.service" --no-pager 2>/dev/null | tail -20; echo
  fi
done

# =========================================================================
sec "2. RED DEL PINEAPPLE"
run ip addr show br-lan
run nft list table inet pineapple
run sh -c 'ps aux | grep -E "[d]nsmasq.*br-lan" || echo "dnsmasq br-lan no visible"'
run sh -c 'ss -ltnp | grep -E ":1471|:1337" || echo "nginx/ws no escuchando aun"'

# =========================================================================
sec "3. PANEL WEB (http://172.16.84.1:1471)"
run sh -c 'curl -s -o /dev/null -w "HTTP %{http_code}\n" http://172.16.84.1:1471/ || echo "curl fallo"'
echo "--- login API (root/pineapplesareyummy) ---"
run sh -c 'curl -s -c /tmp/pc.jar -X POST http://172.16.84.1:1471/api/ \
  -H "Content-Type: application/json" \
  -d "{\"system\":\"authentication\",\"action\":\"login\",\"username\":\"root\",\"password\":\"pineapplesareyummy\"}" | head -c 400; echo'
echo "--- getModuleList (con X-XSRF-TOKEN de la cookie, como hace el navegador) ---"
run sh -c 'XT=$(awk "/XSRF-TOKEN/{print \$7}" /tmp/pc.jar | tail -1); \
  curl -s -b /tmp/pc.jar -X POST http://172.16.84.1:1471/api/ \
  -H "Content-Type: application/json" -H "X-XSRF-TOKEN: $XT" \
  -d "{\"system\":\"modules\",\"action\":\"getModuleList\"}" | head -c 500; echo'

# =========================================================================
sec "4. CORE pineapd / pineap (socket)"
run sh -c 'pineap /tmp/pineap.conf get_status'
run sh -c 'pineap /tmp/pineap.conf add_ssid PruebaNet'
run sh -c 'pineap /tmp/pineap.conf list_ssids'
run sh -c 'pineap /tmp/pineap.conf clear_ssids'
run sh -c 'pineap /tmp/pineap.conf list_ssids'

# =========================================================================
sec "5. RECON (STA scan nativo sobre wlan0 -- SIN monitor; wlan0 sigue conectada)"
# El recon ya NO usa wlan0mon: scan_sta hace 'iw dev wlan0 scan' sobre la STA
# conectada y ve todos los APs sin romper el internet del telefono. wlan0mon NO
# debe existir (solo se crea con un adaptador DEDICADO para handshake/deauth).
run sh -c 'iw dev wlan0 info | grep -q "type managed" && echo "OK: wlan0 sigue en managed (internet intacto)" || echo "ALERTA: wlan0 NO esta en managed!"'
run sh -c 'iw dev | grep -q wlan0mon && echo "AVISO: wlan0mon existe (no deberia en single-radio)" || echo "OK: sin wlan0mon (recon via STA scan)"'
# run_scan requiere 2 args: <duracion_seg> <scan_type>  (RE: cmd 4, [freq:u8][dur:u32BE])
#   scan_type/freq: 0=2.4GHz, 1=5GHz, 2=ambas bandas  (asi lo llama el panel:
#   Recon/module.php "run_scan {duration} {scanType}" y Help/debug "run_scan 15 $scan_type")
#   Aca: 15 segundos, ambas bandas. (dur=0 = scan continuo.)
run sh -c 'pineap /tmp/pineap.conf run_scan 15 2'
echo "  esperando 15s de scan..."; sleep 15
run sh -c 'pineap /tmp/pineap.conf get_status'
run sh -c 'pineap /tmp/pineap.conf stop_scan'
echo "--- APs detectados (tabla aps) ---"
run sh -c 'RDB=$(grep -oP "recon_db_path\s*=\s*\K\S+" /tmp/pineap.conf 2>/dev/null); RDB=${RDB:-/tmp/recon.db}; sqlite3 "$RDB" "SELECT ssid,bssid,channel FROM aps LIMIT 15;" 2>&1 || echo "sin recon.db"'
run sh -c 'RDB=$(grep -oP "recon_db_path\s*=\s*\K\S+" /tmp/pineap.conf 2>/dev/null); RDB=${RDB:-/tmp/recon.db}; echo "clients:"; sqlite3 "$RDB" "SELECT mac,bssid FROM clients LIMIT 15;" 2>&1'

# =========================================================================
sec "6. KARMA / EVIL TWIN (AP en wlan1 = Archer)"
run sh -c 'iw dev | grep -A3 wlan1 || echo "wlan1 (Archer) no visible"'
run sh -c 'systemctl is-active pineapple-ap.service'
run sh -c 'pgrep -a hostapd || echo "hostapd no corriendo"'
run sh -c 'pineap /tmp/pineap.conf karma on'
sleep 2
run sh -c 'pineap /tmp/pineap.conf get_status'
echo "NOTA: para probar karma real conectá un dispositivo de prueba al AP y mirá"
echo "      si asocia + recibe IP (172.16.84.x). Eso es manual."
echo "--- clientes asociados al AP (iw dev wlan1 station dump) ---"
run sh -c 'iw dev wlan1 station dump 2>&1 | head -20 || echo "sin stations"'

# =========================================================================
sec "7. HANDSHAKE CAPTURE"
run sh -c 'ls -la /tmp/handshakes/ 2>&1 || echo "sin /tmp/handshakes aun (necesita trafico real)"'

# =========================================================================
sec "8. PineAPe ENTERPRISE (EAP certs + DB)"
run sh -c 'ls -la /etc/pineape/certs/ 2>&1 || echo "sin certs - corré /etc/pineape/certs/configure.sh"'
run sh -c 'sqlite3 /etc/pineapple/pineape.db ".tables" 2>&1 || echo "sin pineape.db"'
run sh -c 'sqlite3 /etc/pineapple/pineape.db "SELECT count(*) AS creds_chalresp FROM chalresp;" 2>&1'

# =========================================================================
sec "9. LOGS DE LOS DAEMONS (para diagnostico)"
echo "--- pineapd ---";       journalctl -u pineapd.service --no-pager 2>/dev/null | tail -25
echo "--- hostapd (ap) ---";  journalctl -u pineapple-ap.service --no-pager 2>/dev/null | tail -25
echo "--- nginx ---";         journalctl -u pineapple-nginx.service --no-pager 2>/dev/null | tail -10

# =========================================================================
if [ "$KEEP" -eq 1 ]; then
  sec "FIN - stack SIGUE CORRIENDO (--keep). Pará con: sudo systemctl stop pineapple.target"
else
  sec "10. PARAR TODO (revierte la red, deja el telefono como estaba)"
  run systemctl stop pineapple.target
  sleep 2
  run sh -c 'ip addr show br-lan 2>&1 | head -2 || echo "br-lan eliminado OK"'
  run sh -c 'nft list table inet pineapple 2>&1 | head -2 || echo "nft pineapple revertido OK"'
  echo "  conexion normal del telefono restaurada."
fi

hr
echo "LOG COMPLETO EN: $LOG"
echo "run terminado $(date -Is)"
hr
