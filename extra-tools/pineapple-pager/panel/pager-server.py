#!/usr/bin/env python3
# =============================================================================
# pager-server.py — API/UI server del WiFi Pineapple Pager (port nativo aarch64)
# -----------------------------------------------------------------------------
# Sustituye el golang-product-server del Pager real. Hace 3 cosas:
#   1. Sirve el frontend estatico (la app "Pager virtual") en /.
#   2. Expone la API REST /api/* que el frontend consume, traduciendo cada
#      endpoint a un comando del socket unix de pineapd-pager.
#   3. WebSocket /ws/recon (devices en vivo) y /ws/terminal (shell xterm).
#
# Protocolo del socket pineapd (ver docs/PROTOCOLO-SOCKET.md):
#   frame = u32 cmd (BE) + u32 len (BE) + body MessagePack
#   respuesta = mismo framing.  basic response = {error:uint, message:str}
#
# Dependencias: solo stdlib + msgpack. (aiohttp/websockets NO requeridos: se usa
# http.server + un handshake WebSocket minimo en stdlib, para no instalar nada
# pesado en el G82.)
#
# Uso:  pager-server.py [--sock PATH] [--root DIR] [--port N] [--bind IP]
# =============================================================================
import argparse, json, os, socket, struct, sys, threading, http.server, base64, hashlib
import re, math, shutil, subprocess, time, tempfile, posixpath
from urllib.parse import urlparse, parse_qs, unquote

try:
    import msgpack
except ImportError:
    sys.stderr.write("msgpack required: pip3 install msgpack (or vendor it)\n")
    raise

# ---- command codes (de docs/PROTOCOLO-SOCKET.md) ----------------------------
CMD = {
    "list_interfaces": 100, "mod_interface": 101, "toggle_on": 102, "toggle_off": 103,
    "set_type": 105, "reset_channels": 107,
    "count_devices": 200, "send_devices": 201, "new_recon": 204, "stop_recon": 205,
    "recon_freqstats": 207, "recon_stats": 208, "recon_devcounts": 209, "recon_pps": 211,
    "recondb_control": 212, "set_ssidcollect": 255,
    "deauth": 400, "deauth_all": 401, "deauth_csa": 402,
    "examine": 500,
    "fetch_ssid_pool": 300, "clear_ssid_pool": 301, "mod_ssid_pool": 302,
    "config_ssid_pool": 304, "confbcast_ssid_pool": 312, "fetch_new_ssid_pool": 314,
    "pcap_start": 610, "pcap_stop": 611,
    "gpsd_report": 10,
}

SOCK_PATH = "/opt/pineapple-pager/run/pineap_sock"

# -----------------------------------------------------------------------------
# Contenido de comunidad (temas / ringtones / payloads).
# -----------------------------------------------------------------------------
# COMMUNITY_DIR se autodetecta: en el G82 real vive en /opt/pineapple-pager/community,
# pero en el checkout de desarrollo esta junto al panel (../community respecto a
# este archivo). Se puede forzar con PAGER_COMMUNITY_DIR. main() lo recalcula
# tras parsear args, pero lo dejamos resuelto aca para que import-time no falle.
def _detect_community_dir():
    env = os.environ.get("PAGER_COMMUNITY_DIR")
    if env and os.path.isdir(env):
        return os.path.abspath(env)
    here = os.path.dirname(os.path.abspath(__file__))
    for cand in (
        "/opt/pineapple-pager/community",
        os.path.join(here, "..", "community"),
        os.path.join(here, "community"),
    ):
        if os.path.isdir(cand):
            return os.path.abspath(cand)
    # ultimo recurso: la ruta canonica del G82 (puede no existir todavia)
    return "/opt/pineapple-pager/community"

COMMUNITY_DIR = _detect_community_dir()

# Raiz de payloads de comunidad. Es community/payloads por defecto; se mantiene la
# variable historica PAGER_PAYLOADS_DIR como override explicito.
PAYLOADS_DIR = os.environ.get("PAGER_PAYLOADS_DIR") or os.path.join(COMMUNITY_DIR, "payloads")

# Estado persistente (tema activo, logs de payloads). En el G82 => /opt/pineapple-pager/etc.
ETC_DIR = os.environ.get("PAGER_ETC_DIR", "/opt/pineapple-pager/etc")
ACTIVE_THEME_FILE = os.path.join(ETC_DIR, "active_theme")
DEFAULT_THEME = "wargames"
# Logs de ejecucion de payloads.
PAYLOAD_LOG_DIR = os.environ.get("PAGER_PAYLOAD_LOG_DIR", "/opt/pineapple-pager/log/payloads")
# Timeout de ejecucion de un payload (segundos).
PAYLOAD_TIMEOUT = int(os.environ.get("PAGER_PAYLOAD_TIMEOUT", "300"))


def _community_sub(name):
    return os.path.join(COMMUNITY_DIR, name)

def _ensure_dir(p):
    try:
        os.makedirs(p, exist_ok=True)
    except Exception:
        pass
    return p

def _safe_under(root, rel):
    """Resuelve `rel` bajo `root` impidiendo path traversal. Devuelve la ruta
    absoluta normalizada o None si se escapa de root (o root no existe)."""
    root = os.path.realpath(root)
    rel = unquote(rel or "").lstrip("/")
    full = os.path.realpath(os.path.join(root, rel))
    if full == root or full.startswith(root + os.sep):
        return full
    return None


# Ubicacion donde persisten los settings/toggles del Pager. En el G82 real vive en
# /opt/pineapple-pager/etc/settings.json. El backend expone defaults si no existe.
SETTINGS_FILE = os.path.join(ETC_DIR, "settings.json")

# Directorio run (sockets/pid/logs efimeros). Logs de payload van en PAYLOAD_LOG_DIR
# por compat con la spec; mantenemos ambos.
RUN_DIR = os.environ.get("PAGER_RUN_DIR", "/opt/pineapple-pager/run")


# ---- cliente del socket pineapd ---------------------------------------------
class PineapClient:
    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()

    def call(self, cmd, body=None, timeout=4.0):
        """Envia un comando al socket pineapd y devuelve el body de la respuesta
        (dict msgpack). Si el socket no esta, devuelve un basic error."""
        if body is None:
            body = {}
        packed = msgpack.packb(body, use_bin_type=True)
        frame = struct.pack(">II", cmd, len(packed)) + packed
        try:
            with self.lock:
                s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                s.settimeout(timeout)
                s.connect(self.path)
                s.sendall(frame)
                hdr = self._recvn(s, 8)
                if not hdr:
                    s.close()
                    return {"error": 1, "message": "no response from pineapd"}
                rcmd, rlen = struct.unpack(">II", hdr)
                rbody = self._recvn(s, rlen) if rlen else b""
                s.close()
            if not rbody:
                return {"error": 0, "message": "ok"}
            try:
                return msgpack.unpackb(rbody, raw=False)
            except Exception:
                return {"error": 0, "message": "ok", "_raw_len": len(rbody)}
        except (FileNotFoundError, ConnectionRefusedError):
            return {"error": 1, "message": "pineapd not running"}
        except socket.timeout:
            return {"error": 1, "message": "pineapd timeout"}
        except Exception as e:
            return {"error": 1, "message": f"socket error: {e}"}

    @staticmethod
    def _recvn(s, n):
        buf = b""
        while len(buf) < n:
            try:
                chunk = s.recv(n - len(buf))
            except socket.timeout:
                break
            if not chunk:
                break
            buf += chunk
        return buf


PINEAP = None  # set in main


# ---- traduccion REST -> comando pineapd -------------------------------------
def api_status():
    gps = PINEAP.call(CMD["gpsd_report"])
    cnt = PINEAP.call(CMD["count_devices"])
    return {
        "time": _clock(),
        "vibrate": True, "brightness": 3, "mute": False,
        "wifi_signal": 3, "battery": _battery(),
        "aps": cnt.get("aps", cnt.get("total", 0)),
        "clients": cnt.get("clients", cnt.get("devs", 0)),
        "gps_fix": gps.get("fix", 0),
    }

def api_interfaces():
    r = PINEAP.call(CMD["list_interfaces"])
    # pineapd devuelve {interfaces:[...]} o similar; normalizamos
    ifs = r.get("interfaces") or r.get("interface") or []
    if isinstance(ifs, dict):
        ifs = [ifs]
    return {"interfaces": ifs}

def api_recon_devices(q):
    body = {}
    if "sort" in q: body["sort"] = q["sort"][0]
    if "asc" in q: body["asc"] = q["asc"][0] in ("1", "true", "True")
    if "page" in q: body["page"] = int(q["page"][0])
    if "ssid_search" in q: body["ssid_search"] = q["ssid_search"][0]
    r = PINEAP.call(CMD["send_devices"], body)
    return {"devicelist": r.get("devicelist", []), "more": r.get("more", False),
            "total": r.get("total", len(r.get("devicelist", [])))}

def api_recon_start():
    return PINEAP.call(CMD["new_recon"], {"enable": True, "ifname": "wlan1mon"})

def api_recon_stop():
    return PINEAP.call(CMD["stop_recon"], {"enable": False})

def api_recon_stats():
    s = PINEAP.call(CMD["recon_stats"])
    pps = PINEAP.call(CMD["recon_pps"])
    return {"aps": s.get("aps", 0), "clients": s.get("devs", 0),
            "packets": s.get("total", 0), "pps": pps.get("pps", 0)}

def api_pineap_get():
    return PINEAP.call(CMD["fetch_ssid_pool"])

def api_pineap_set(body):
    # enable/disable karma / mimic -> set_ssidcollect / config_ssid_pool
    if "karma" in body:
        return PINEAP.call(CMD["set_ssidcollect"], {"bool": bool(body["karma"])})
    return PINEAP.call(CMD["config_ssid_pool"], body)

def api_ssidpool_get():
    r = PINEAP.call(CMD["fetch_ssid_pool"])
    return {"ssids": r.get("ssids", []), "more": r.get("more", False)}

def api_ssidpool_post(body):
    act = body.get("action")
    if act == "add":
        return PINEAP.call(CMD["mod_ssid_pool"], {"ssids": [body["ssid"]], "add": True})
    if act == "del":
        return PINEAP.call(CMD["mod_ssid_pool"], {"ssids": [body["ssid"]], "add": False})
    if act == "clear":
        return PINEAP.call(CMD["clear_ssid_pool"])
    return {"error": 1, "message": "unknown action"}

def api_deauth(body):
    code = CMD["deauth"]
    if body.get("all"): code = CMD["deauth_all"]
    elif body.get("csa"): code = CMD["deauth_csa"]
    return PINEAP.call(code, {
        "bssid": body.get("bssid", ""), "target": body.get("target", "ff:ff:ff:ff:ff:ff"),
        "channel": int(body.get("channel", 1)), "count": int(body.get("count", 5)),
    })

def api_gps():
    return PINEAP.call(CMD["gpsd_report"])

def api_settings_get():
    return {"hostname": "pager", "lan_ip": "172.16.52.1", "vibrate": True,
            "buzzer": True, "led": True, "channel_hop": True, "brightness": 3}

def api_settings_set(body):
    return {"error": 0, "message": "settings are read-only in this build"}


def api_themes():
    """Catalogo de temas de la pantalla escaneando community/themes/<id>/theme.json.

    Cada tema de comunidad es una carpeta con theme.json + components/ + assets/.
    Devuelve una lista [{id, name, author, version}] (segun pide la spec). El
    campo `default` va para compat con el frontend viejo (apunta al tema activo).
    """
    tdir = _community_sub("themes")
    themes = []
    try:
        entries = sorted(os.listdir(tdir))
    except FileNotFoundError:
        entries = []
    for tid in entries:
        tjson = os.path.join(tdir, tid, "theme.json")
        if not os.path.isfile(tjson):
            continue
        meta = {"id": tid, "name": tid, "author": "", "version": ""}
        try:
            with open(tjson, encoding="utf-8") as f:
                t = json.load(f)
            if isinstance(t, dict):
                meta["name"] = t.get("name") or t.get("theme_name") or tid
                meta["author"] = t.get("author", "")
                meta["version"] = (t.get("theme_version")
                                   or t.get("version") or "")
        except Exception:
            pass
        themes.append(meta)
    active = _read_active_theme()
    if not any(t["id"] == active for t in themes) and themes:
        active = themes[0]["id"]
    # devolvemos la lista como contrato principal + metadata extra para el front.
    return {"default": active, "active": active, "themes": themes}


def _read_active_theme():
    """Tema activo persistido en ACTIVE_THEME_FILE; default DEFAULT_THEME."""
    try:
        with open(ACTIVE_THEME_FILE, encoding="utf-8") as f:
            v = f.read().strip()
        if v:
            return v
    except Exception:
        pass
    return DEFAULT_THEME


def api_theme_get():
    """Tema activo (lee ACTIVE_THEME_FILE, default DEFAULT_THEME)."""
    tid = _read_active_theme()
    base = os.path.join(_community_sub("themes"), tid)
    exists = os.path.isfile(os.path.join(base, "theme.json"))
    return {"id": tid, "path": "/community/themes/%s/" % tid, "exists": exists}


def api_theme_set(body):
    """Setea el tema activo y lo persiste en ACTIVE_THEME_FILE."""
    tid = (body or {}).get("id", "").strip()
    if not tid:
        return {"error": 1, "message": "missing id"}
    # validar que exista y que el id no haga traversal.
    safe = _safe_under(_community_sub("themes"), tid)
    if not safe or not os.path.isfile(os.path.join(safe, "theme.json")):
        return {"error": 1, "message": "unknown theme: %s" % tid}
    _ensure_dir(ETC_DIR)
    try:
        with open(ACTIVE_THEME_FILE, "w", encoding="utf-8") as f:
            f.write(tid + "\n")
    except Exception as e:
        return {"error": 1, "message": "persist failed: %s" % e}
    return {"error": 0, "message": "ok", "id": tid}


def api_alerts(q):
    """Lista de alertas/eventos del Pager.

    No existe un bus de alertas nativo en pineapd, asi que sintetizamos eventos
    utiles a partir del estado actual (recon activo, karma, deauth-capable ifaces,
    conteos). Cada alerta: {ts, level, message, payload}. level in info|warn|crit.
    El frontend solo necesita esta forma; cuando exista un log real del daemon se
    reemplaza la fuente sin tocar el contrato.
    """
    import time
    now = int(time.time() * 1000)
    alerts = []

    def add(level, message, payload=None, ago=0):
        alerts.append({"ts": now - ago, "level": level,
                       "message": message, "payload": payload or {}})

    stats = api_recon_stats()
    cnt = PINEAP.call(CMD["count_devices"])
    daemon_down = isinstance(cnt, dict) and cnt.get("error")
    if daemon_down:
        add("crit", "pineapd no responde — motor WiFi caido",
            {"error": cnt.get("error"), "detail": cnt.get("message")})
    aps = stats.get("aps", cnt.get("aps", cnt.get("total", 0)))
    clients = stats.get("clients", cnt.get("clients", cnt.get("devs", 0)))

    if stats.get("pps", 0) or stats.get("packets", 0):
        add("info", f"Recon activo — {stats.get('pps', 0)} pps",
            {"pps": stats.get("pps", 0), "packets": stats.get("packets", 0)})
    if aps:
        add("info", f"{aps} APs visibles ({clients} clientes)",
            {"aps": aps, "clients": clients}, ago=1500)

    pineap = PINEAP.call(CMD["fetch_ssid_pool"])
    if isinstance(pineap, dict) and pineap.get("enable"):
        add("warn", "PineAP/KARMA habilitado", {"karma": True}, ago=3000)

    # iface monitor presente => capacidad ofensiva (aviso)
    ifs = api_interfaces().get("interfaces", [])
    if any((i.get("type") == "monitor") for i in ifs if isinstance(i, dict)):
        add("warn", "Interfaz monitor activa (deauth disponible)",
            {"ifaces": [i.get("ifname") for i in ifs if isinstance(i, dict)]}, ago=5000)

    if not alerts:
        add("info", "Sin eventos — sistema nominal", {}, ago=0)

    alerts.sort(key=lambda a: a["ts"], reverse=True)
    return {"alerts": alerts}


def _clock():
    import time
    return time.strftime("%I:%M %p").lstrip("0")

def _battery():
    # el G82 tiene bateria real; leer sysfs si existe
    pct, _ = _battery_scan()
    return pct if pct is not None else 100

def _uplink_signal():
    return 3


# =============================================================================
# Helpers de bajo nivel para resolver variables $_ (sistema/sysfs/pineapd/gps).
# =============================================================================
def _read_first(*paths):
    """Devuelve el contenido .strip() del primer path legible, o None."""
    for p in paths:
        try:
            with open(p, encoding="utf-8", errors="ignore") as f:
                return f.read().strip()
        except Exception:
            continue
    return None


def _battery_scan():
    """Escanea /sys/class/power_supply/*/ buscando una bateria (capacity+status).

    El G82 expone la bateria via bq27546 (p.ej. .../battery/ o .../bq27546-0/).
    Devuelve (capacity:int|None, status:str). En el checkout de dev no hay sysfs
    de bateria real -> (None, "")."""
    base = "/sys/class/power_supply"
    try:
        names = sorted(os.listdir(base))
    except Exception:
        return (None, "")
    # preferir supplies tipo Battery
    for name in names:
        d = os.path.join(base, name)
        typ = _read_first(os.path.join(d, "type")) or ""
        cap = _read_first(os.path.join(d, "capacity"))
        if cap is None:
            continue
        if typ.lower() != "battery" and "bat" not in name.lower() and "bq" not in name.lower():
            # si no parece bateria pero es el unico con capacity, igual sirve
            pass
        try:
            pct = int(cap)
        except ValueError:
            continue
        status = _read_first(os.path.join(d, "status")) or ""
        return (pct, status)
    return (None, "")


def _brightness():
    """Lee /sys/class/backlight/*/brightness (0..max_brightness) -> 0..100 o ''."""
    base = "/sys/class/backlight"
    try:
        names = sorted(os.listdir(base))
    except Exception:
        return ""
    for name in names:
        d = os.path.join(base, name)
        cur = _read_first(os.path.join(d, "brightness"))
        mx = _read_first(os.path.join(d, "max_brightness"))
        if cur is None:
            continue
        try:
            cur = int(cur)
            mx = int(mx) if mx else 0
        except ValueError:
            continue
        if mx > 0:
            return int(round(cur * 100.0 / mx))
        return cur
    return ""


def _uptime_str():
    """Uptime legible (Nd HH:MM:SS) desde /proc/uptime, o ''."""
    try:
        with open("/proc/uptime") as f:
            secs = int(float(f.read().split()[0]))
    except Exception:
        return ""
    d, rem = divmod(secs, 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    if d:
        return "%dd %02d:%02d:%02d" % (d, h, m, s)
    return "%02d:%02d:%02d" % (h, m, s)


def _hostname():
    hn = _read_first("/proc/sys/kernel/hostname", "/etc/hostname")
    if hn:
        return hn
    try:
        return socket.gethostname()
    except Exception:
        return "pager"


def _firmware_version():
    """Version de firmware del Pager (best-effort). En el G82 vive en un archivo
    de /etc o en uci; aca probamos rutas conocidas y caemos a ''."""
    v = _read_first(
        "/opt/pineapple-pager/etc/version",
        "/etc/pineapple-pager-version",
        "/etc/pineapple_version",
    )
    return v or ""


def _load_settings():
    """Lee SETTINGS_FILE (toggles persistidos). Devuelve dict (vacio si no hay)."""
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict):
            return d
    except Exception:
        pass
    return {}


def _payload_categories():
    """Lista de categorias top-level de community/payloads (dirs)."""
    try:
        return sorted(d for d in os.listdir(PAYLOADS_DIR)
                      if os.path.isdir(os.path.join(PAYLOADS_DIR, d)))
    except Exception:
        return []


def _installed_payloads_list():
    """Nombres de payloads instalados (carpeta que contiene payload.sh)."""
    out = []
    for p in _iter_payload_dirs():
        out.append(os.path.basename(p))
    return sorted(out)


def _iter_payload_dirs():
    """Itera rutas absolutas de carpetas con payload.sh bajo PAYLOADS_DIR."""
    for dirpath, _dirs, files in os.walk(PAYLOADS_DIR):
        if "payload.sh" in files:
            yield dirpath


def api_variables():
    """Resuelve TODAS las variables $_ del motor de render a valores reales.

    Fuentes (ver docs/DISPLAY-ENGINE.md):
      - Sistema: reloj, bateria (sysfs G82), hostname, brightness, uptime, fw, fecha.
      - PineAP:  config del socket pineapd (fetch_ssid_pool / config).
      - Recon:   count_devices (el detalle de filas lo da /api/recon/devices).
      - GPS:     gpsd_report.
      - Payloads: categorias + instalados (community/payloads).
      - Toggles: settings.json persistido (defaults si no existe).
    Las que no se puedan resolver => "" o false. Devuelve dict plano."""
    import time as _t
    now = _t.localtime()
    v = {}

    # ---- Sistema / fecha-hora -------------------------------------------------
    v["$_CLOCK_"] = _t.strftime("%H:%M", now)
    v["$_DATETIME"] = _t.strftime("%Y-%m-%d %H:%M:%S", now)
    v["$_YEAR"] = _t.strftime("%Y", now)
    v["$_MONTH"] = _t.strftime("%m", now)
    v["$_DAY"] = _t.strftime("%d", now)
    v["$_HOUR"] = _t.strftime("%H", now)
    v["$_MINUTE"] = _t.strftime("%M", now)

    pct, status = _battery_scan()
    v["$_BATTERY"] = pct if pct is not None else ""
    if pct is not None:
        info = "%d%%" % pct
        if status:
            info += " " + status
        v["$_BATTERY_INFO"] = info
    else:
        v["$_BATTERY_INFO"] = ""

    v["$_HOSTNAME"] = _hostname()
    v["$_BRIGHTNESS"] = _brightness()
    v["$_UPTIME"] = _uptime_str()
    v["$_FIRMWARE_VERSION"] = _firmware_version()

    # ---- Toggles / settings persistidos (defaults si no hay archivo) ----------
    st = _load_settings()
    v["$_VOLUME"] = st.get("volume", "")
    v["$_VIBRATE"] = bool(st.get("vibrate", False))
    v["$_BLUETOOTH_ENABLED"] = bool(st.get("bluetooth", False))
    v["$_DPAD_LED_COLOR"] = st.get("dpad_led_color", "")
    v["$_THEME"] = _read_active_theme()

    # ---- PineAP (socket pineapd) ---------------------------------------------
    pineap = PINEAP.call(CMD["fetch_ssid_pool"]) if PINEAP else {}
    if not isinstance(pineap, dict):
        pineap = {}
    pdown = bool(pineap.get("error"))

    def _pb(key, *names):
        """bool desde la config pineap (varias claves posibles)."""
        for n in names:
            if n in pineap:
                return bool(pineap[n])
        return False

    v["$_PINEAP_IMPERSONATE_NETWORKS"] = _pb("impersonate", "impersonate", "beacon_responses")
    v["$_PINEAP_COLLECT_NETWORKS"] = _pb("collect", "capture_ssids", "pineap_capture_ssids")
    v["$_PINEAP_ADVERTISE_NETWORKS"] = _pb("advertise", "beacon", "broadcast_ssid_pool")
    v["$_PINEAP_RANDOMIZE_MAC_ADDRESS"] = _pb("randomize_mac", "mac_randomize")
    v["$_PINEAP_COLLECT_HANDSHAKES"] = _pb("capture_handshakes", "collect_handshakes")

    pool = pineap.get("ssids") or pineap.get("ssid_pool") or []
    if isinstance(pool, list):
        v["$_PINEAP_SSID_POOL_LIST"] = "\n".join(str(s) for s in pool)
    else:
        v["$_PINEAP_SSID_POOL_LIST"] = ""

    v["$_OPEN_AP_ENABLED"] = _pb("open_ap_enabled", "open_ap")
    v["$_OPEN_AP_SSID"] = pineap.get("open_ap_ssid", "") or ""
    v["$_EVIL_WPA_ENABLED"] = _pb("evil_wpa_enabled", "evilwpa_enabled")
    v["$_EVIL_WPA_SSID"] = pineap.get("evil_wpa_ssid", "") or ""
    v["$_EVIL_WPA_PASSPHRASE"] = pineap.get("evil_wpa_passphrase", "") or ""

    # ---- Recon ----------------------------------------------------------------
    cnt = PINEAP.call(CMD["count_devices"]) if PINEAP else {}
    if not isinstance(cnt, dict):
        cnt = {}
    list_size = cnt.get("total", cnt.get("aps", cnt.get("devs", 0)))
    try:
        list_size = int(list_size)
    except (TypeError, ValueError):
        list_size = 0
    v["$_RECON_LIST_SIZE"] = list_size
    # Las filas ($_RECON_RESULT_*) las mapea el frontend desde /api/recon/devices.

    # ---- GPS ------------------------------------------------------------------
    gps = PINEAP.call(CMD["gpsd_report"]) if PINEAP else {}
    if not isinstance(gps, dict):
        gps = {}
    lat = gps.get("lat", gps.get("latitude", ""))
    lon = gps.get("lon", gps.get("longitude", ""))
    v["$_GPS_LAT"] = lat if lat not in (None, "") else ""
    v["$_GPS_LON"] = lon if lon not in (None, "") else ""

    # ---- Payloads -------------------------------------------------------------
    cats = _payload_categories()
    v["$_PAYLOAD_CATEGORIES"] = "\n".join(cats)
    v["$_INSTALLED_PAYLOADS_LIST"] = "\n".join(_installed_payloads_list())

    return v


# =============================================================================
# Ringtones (community/ringtones/*.rtttl) -> sintesis y reproduccion en el G82.
# =============================================================================
def _ringtones_dir():
    return _community_sub("ringtones")


def api_ringtones():
    """Lista los .rtttl de community/ringtones/ -> [{name, file}]."""
    d = _ringtones_dir()
    out = []
    try:
        for fn in sorted(os.listdir(d)):
            if fn.endswith(".rtttl"):
                out.append({"name": fn[:-6], "file": fn})
    except FileNotFoundError:
        pass
    return {"ringtones": out, "count": len(out)}


# notas -> semitono relativo a A (para calcular frecuencia). RTTTL usa p=silencio.
_RTTTL_NOTES = {"c": -9, "c#": -8, "d": -7, "d#": -6, "e": -5, "f": -4,
                "f#": -3, "g": -2, "g#": -1, "a": 0, "a#": 1, "b": 2,
                "p": None}


def _rtttl_parse(text):
    """Parser RTTTL minimo. Devuelve (name, [(freq_hz, dur_ms), ...]).

    Formato: name:d=<dur>,o=<oct>,b=<bpm>:nota,nota,...
    nota = [duracion][nombre][#][octava][.]  (. = dotted, +50%).
    freq_hz = 0 para silencio (p)."""
    parts = text.strip().split(":")
    if len(parts) < 3:
        raise ValueError("RTTTL invalido (faltan secciones)")
    name = parts[0].strip()
    defaults = parts[1]
    notes_s = parts[2]

    d_def, o_def, b_def = 4, 5, 63
    for tok in defaults.split(","):
        tok = tok.strip().lower()
        if "=" not in tok:
            continue
        k, val = tok.split("=", 1)
        try:
            n = int(val)
        except ValueError:
            continue
        if k == "d":
            d_def = n
        elif k == "o":
            o_def = n
        elif k == "b":
            b_def = n

    # ms por nota entera = 4 beats; beat = 60000/bpm ms -> whole = 4*beat
    whole_ms = 4.0 * 60000.0 / max(1, b_def)
    events = []
    for raw in notes_s.split(","):
        tok = raw.strip().lower()
        if not tok:
            continue
        m = re.match(r"^(\d+)?([a-gp]#?)(\d+)?(\.)?$", tok)
        if not m:
            continue
        dur = int(m.group(1)) if m.group(1) else d_def
        note = m.group(2)
        octv = int(m.group(3)) if m.group(3) else o_def
        dotted = bool(m.group(4))

        dur_ms = whole_ms / max(1, dur)
        if dotted:
            dur_ms *= 1.5

        semitone = _RTTTL_NOTES.get(note)
        if semitone is None:  # pausa
            freq = 0.0
        else:
            # A4 = 440Hz. n = semitonos respecto a A4.
            n = semitone + (octv - 4) * 12
            freq = 440.0 * (2.0 ** (n / 12.0))
        events.append((freq, dur_ms))
    return name, events


def _rtttl_to_wav(events, path, rate=22050, amp=0.35):
    """Sintetiza los eventos a un WAV PCM 16-bit mono usando el modulo stdlib wave.
    Pequeno fade por nota para evitar clicks."""
    import wave, array
    samples = array.array("h")
    for freq, dur_ms in events:
        n = int(rate * dur_ms / 1000.0)
        if freq <= 0:
            samples.extend([0] * n)
            continue
        fade = max(1, int(rate * 0.005))  # 5ms fade in/out
        for i in range(n):
            s = math.sin(2.0 * math.pi * freq * (i / rate))
            env = 1.0
            if i < fade:
                env = i / fade
            elif i > n - fade:
                env = max(0.0, (n - i) / fade)
            samples.append(int(max(-1.0, min(1.0, s * amp * env)) * 32767))
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(samples.tobytes())


def _play_wav(path):
    """Reproduce un WAV por el parlante usando el primer player disponible."""
    for tool, argv in (
        ("aplay", ["aplay", "-q", path]),
        ("paplay", ["paplay", path]),
        ("play", ["play", "-q", path]),
        ("ffplay", ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", path]),
    ):
        if shutil.which(tool):
            try:
                subprocess.run(argv, timeout=60,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return tool
            except Exception:
                continue
    return None


def api_ringtone_play(body):
    """Reproduce un ringtone por el PARLANTE del G82.

    Estrategia:
      1. Si existe 'play' (sox) => play -n synth por nota (sin archivo temporal).
      2. Si no, sintetizamos WAV PCM con el modulo stdlib 'wave' y lo tocamos con
         aplay/paplay/ffplay.
      3. Si no hay ninguna herramienta de audio => degradamos con mensaje claro.
    """
    name = (body or {}).get("name", "").strip()
    if not name:
        return {"error": 1, "message": "missing name"}
    if not name.endswith(".rtttl"):
        name += ".rtttl"
    full = _safe_under(_ringtones_dir(), name)
    if not full or not os.path.isfile(full):
        return {"error": 1, "message": "ringtone not found: %s" % name}

    try:
        with open(full, encoding="utf-8") as f:
            text = f.read()
        tune, events = _rtttl_parse(text)
    except Exception as e:
        return {"error": 1, "message": "rtttl parse error: %s" % e}

    # --- 1) sox 'play': synth directo nota por nota (mejor calidad, sin temp) ---
    if shutil.which("play"):
        argv = ["play", "-q", "-n"]
        first = True
        emitted = False
        for freq, dur_ms in events:
            seg = ["synth", "%.4f" % (dur_ms / 1000.0)]
            if freq <= 0:
                seg += ["sine", "0"]       # silencio
            else:
                seg += ["sine", "%.2f" % freq]
            if first:
                argv += seg
                first = False
            else:
                argv += [":", "newfile", ":"] if False else seg  # concat simple
            emitted = True
        # sox concatena multiples 'synth' en un stream; ejecutar best-effort.
        try:
            subprocess.run(argv, timeout=60,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return {"error": 0, "message": "played via sox(play)",
                    "tool": "play", "tune": tune, "notes": len(events)}
        except Exception:
            pass  # cae al fallback WAV

    # --- 2) fallback: WAV PCM + aplay/paplay/ffplay ---------------------------
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(suffix=".wav", prefix="ringtone_")
        os.close(fd)
        _rtttl_to_wav(events, tmp)
        tool = _play_wav(tmp)
        if tool:
            return {"error": 0, "message": "played via %s (stdlib wave synth)" % tool,
                    "tool": tool, "tune": tune, "notes": len(events)}
        return {"error": 1, "tool": None,
                "message": ("no audio tool available (need sox/play, aplay, "
                            "paplay or ffplay on the G82). WAV synth OK but "
                            "nothing to play it."),
                "tune": tune, "notes": len(events)}
    except Exception as e:
        return {"error": 1, "message": "playback error: %s" % e}
    finally:
        if tmp:
            try: os.remove(tmp)
            except Exception: pass


def api_ringtone_raw(name):
    """Sirve el .rtttl crudo (para que el front use Web Audio). Devuelve
    (bytes, ctype) o (None, msg)."""
    if not name.endswith(".rtttl"):
        name += ".rtttl"
    full = _safe_under(_ringtones_dir(), name)
    if not full or not os.path.isfile(full):
        return None, "not found"
    try:
        with open(full, "rb") as f:
            return f.read(), "text/plain; charset=utf-8"
    except Exception as e:
        return None, str(e)


# =============================================================================
# Payloads (community/payloads) -> listado + ejecucion sandbox con ENV $_ + LOG.
# =============================================================================
_META_RE = re.compile(r"^#\s*(Title|Author|Description|Version|Category)\s*:\s*(.*)$",
                      re.IGNORECASE)


def _parse_payload_meta(path):
    """Parsea la metadata del payload.sh (# Title/# Author/# Description/# Version).
    Lee solo las primeras lineas de comentario del header."""
    meta = {"title": "", "author": "", "description": "", "version": "", "category": ""}
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            for i, line in enumerate(f):
                if i > 40:
                    break
                m = _META_RE.match(line.strip())
                if m:
                    meta[m.group(1).lower()] = m.group(2).strip()
    except Exception:
        pass
    return meta


def api_payloads():
    """Arbol de community/payloads/ (categoria/sub/payload) con metadata.

    Estructura real: payloads/<category>/<sub>/<name>/payload.sh. Devolvemos un
    arbol anidado + una lista plana con `path` relativo a PAYLOADS_DIR (lo que
    espera POST /api/payload/run)."""
    tree = {}
    flat = []
    for d in sorted(_iter_payload_dirs()):
        rel = os.path.relpath(d, PAYLOADS_DIR)
        comps = rel.split(os.sep)
        # category / sub / name   (toleramos profundidades distintas)
        category = comps[0] if len(comps) >= 1 else ""
        sub = comps[1] if len(comps) >= 3 else ""
        name = comps[-1]
        meta = _parse_payload_meta(os.path.join(d, "payload.sh"))
        entry = {
            "name": name,
            "title": meta["title"] or name,
            "author": meta["author"],
            "description": meta["description"],
            "version": meta["version"],
            "category": category,
            "sub": sub,
            "path": os.path.join(rel, "payload.sh"),
        }
        flat.append(entry)
        tree.setdefault(category, {}).setdefault(sub, []).append(entry)
    return {"tree": tree, "payloads": flat, "count": len(flat),
            "categories": _payload_categories()}


def _payload_log_path(rel_dir):
    """Ruta del log de un payload (uno por payload, por su dir relativo)."""
    _ensure_dir(PAYLOAD_LOG_DIR)
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", rel_dir.strip("/"))
    return os.path.join(PAYLOAD_LOG_DIR, safe + ".log")


def _write_log_wrapper():
    """Crea un wrapper 'LOG' (y helpers ALERT/PROMPT/etc.) en un bindir temporal
    y devuelve (bindir, env-extra). El wrapper escribe a $PAYLOAD_LOG_FILE.

    Los payloads usan comandos del firmware (LOG, ALERT, PROMPT, *_DIALOG,
    *_PICKER, PAYLOAD_GET/SET_CONFIG...). No existen fuera del G82; proveemos
    shims que loguean y devuelven vacio para que el payload no explote."""
    bindir = tempfile.mkdtemp(prefix="pager-payload-bin-")
    # LOG: escribe al log (soporta 'LOG color msg' o 'LOG msg')
    log_sh = os.path.join(bindir, "LOG")
    with open(log_sh, "w") as f:
        f.write(
            "#!/bin/sh\n"
            '# shim de LOG para payloads fuera del G82\n'
            'case "$1" in green|red|blue|yellow|white|cyan|magenta) shift;; esac\n'
            'printf "%s\\n" "$*" >> "${PAYLOAD_LOG_FILE:-/dev/stderr}"\n'
        )
    os.chmod(log_sh, 0o755)
    # shims no-interactivos: devuelven vacio/exito, loguean la invocacion.
    for cmd in ("ALERT", "PROMPT", "CONFIRMATION_DIALOG", "ERROR_DIALOG",
                "TEXT_PICKER", "NUMBER_PICKER", "PAYLOAD_GET_CONFIG",
                "PAYLOAD_SET_CONFIG"):
        p = os.path.join(bindir, cmd)
        with open(p, "w") as f:
            f.write(
                "#!/bin/sh\n"
                'printf "[%s] %s\\n" "%s" "$*" >> "${PAYLOAD_LOG_FILE:-/dev/stderr}"\n'
                'exit 0\n' % ("SHIM", "%s", cmd)
            )
        os.chmod(p, 0o755)
    return bindir


def api_payload_run(body):
    """Ejecuta un payload.sh (bash) con las variables $_ inyectadas como ENV y un
    comando LOG disponible. Timeout + sandbox de path (dentro de PAYLOADS_DIR).

    body: {path: "<cat>/<sub>/<name>/payload.sh"}  (relativo a PAYLOADS_DIR)."""
    rel = (body or {}).get("path", "").strip()
    if not rel:
        return {"error": 1, "message": "missing path"}
    # aceptar que venga con prefijo payloads/ o community/payloads/
    for pre in ("community/payloads/", "payloads/"):
        if rel.startswith(pre):
            rel = rel[len(pre):]
    full = _safe_under(PAYLOADS_DIR, rel)
    if not full:
        return {"error": 1, "message": "path escapes payloads dir (traversal)"}
    if os.path.isdir(full):
        full = os.path.join(full, "payload.sh")
    if not os.path.isfile(full):
        return {"error": 1, "message": "payload.sh not found: %s" % rel}

    # ENV: variables $_ reales. El motor expone $_RECON_SELECTED_AP_*; aqui
    # inyectamos lo que tengamos (payloads leen env sin el prefijo '$').
    env = dict(os.environ)
    try:
        for k, val in api_variables().items():
            env_key = k.lstrip("$")             # $_CLOCK_ -> _CLOCK_
            env[env_key] = "" if val is None else (
                "1" if val is True else "0" if val is False else str(val))
    except Exception:
        pass

    # log por payload + wrapper LOG/shims
    rel_dir = os.path.dirname(rel) or os.path.basename(os.path.dirname(full))
    log_file = _payload_log_path(rel_dir)
    env["PAYLOAD_LOG_FILE"] = log_file
    try:
        bindir = _write_log_wrapper()
        env["PATH"] = bindir + os.pathsep + env.get("PATH", "")
    except Exception:
        bindir = None

    _ensure_dir(os.path.dirname(log_file))
    header = "=== run %s @ %s ===\n" % (rel, time.strftime("%Y-%m-%d %H:%M:%S"))
    try:
        with open(log_file, "w", encoding="utf-8") as lf:
            lf.write(header)
    except Exception:
        pass

    workdir = os.path.dirname(full)
    try:
        proc = subprocess.run(
            ["bash", full], cwd=workdir, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            timeout=PAYLOAD_TIMEOUT, close_fds=True,
        )
        out = proc.stdout.decode("utf-8", "replace") if proc.stdout else ""
        rc = proc.returncode
        timed_out = False
    except subprocess.TimeoutExpired as e:
        out = (e.stdout.decode("utf-8", "replace") if e.stdout else "")
        rc = -1
        timed_out = True
    except FileNotFoundError:
        return {"error": 1, "message": "bash not available on this host"}
    except Exception as e:
        return {"error": 1, "message": "exec error: %s" % e}
    finally:
        if bindir:
            try: shutil.rmtree(bindir, ignore_errors=True)
            except Exception: pass

    # anexar la salida capturada al log
    try:
        with open(log_file, "a", encoding="utf-8") as lf:
            lf.write(out)
            lf.write("\n=== exit %s%s ===\n" % (rc, " (timeout)" if timed_out else ""))
    except Exception:
        pass

    return {"error": 0 if rc == 0 and not timed_out else 1,
            "returncode": rc, "timeout": timed_out,
            "output": out[-8000:], "log": log_file, "path": rel}


def api_payload_log(q):
    """Devuelve el ultimo log de un payload. q: path=<rel dir o payload.sh>."""
    rel = (q.get("path", [""])[0] if isinstance(q, dict) else "").strip()
    if not rel:
        return {"error": 1, "message": "missing path"}
    for pre in ("community/payloads/", "payloads/"):
        if rel.startswith(pre):
            rel = rel[len(pre):]
    if rel.endswith("/payload.sh"):
        rel = rel[:-len("/payload.sh")]
    rel = rel.rstrip("/")
    log_file = _payload_log_path(rel)
    try:
        with open(log_file, encoding="utf-8", errors="replace") as f:
            return {"error": 0, "log": log_file, "content": f.read()}
    except FileNotFoundError:
        return {"error": 1, "message": "no log yet", "log": log_file, "content": ""}
    except Exception as e:
        return {"error": 1, "message": str(e)}


ROUTES = {
    ("GET", "/api/status"): lambda q, b: api_status(),
    ("GET", "/api/interfaces"): lambda q, b: api_interfaces(),
    ("GET", "/api/recon/devices"): lambda q, b: api_recon_devices(q),
    ("POST", "/api/recon/start"): lambda q, b: api_recon_start(),
    ("POST", "/api/recon/stop"): lambda q, b: api_recon_stop(),
    ("GET", "/api/recon/stats"): lambda q, b: api_recon_stats(),
    ("GET", "/api/pineap"): lambda q, b: api_pineap_get(),
    ("POST", "/api/pineap"): lambda q, b: api_pineap_set(b),
    ("GET", "/api/ssidpool"): lambda q, b: api_ssidpool_get(),
    ("POST", "/api/ssidpool"): lambda q, b: api_ssidpool_post(b),
    ("POST", "/api/deauth"): lambda q, b: api_deauth(b),
    ("GET", "/api/gps"): lambda q, b: api_gps(),
    ("GET", "/api/alerts"): lambda q, b: api_alerts(q),
    ("GET", "/api/settings"): lambda q, b: api_settings_get(),
    ("POST", "/api/settings"): lambda q, b: api_settings_set(b),
    ("GET", "/api/themes"): lambda q, b: api_themes(),
    ("GET", "/api/theme"): lambda q, b: api_theme_get(),
    ("POST", "/api/theme"): lambda q, b: api_theme_set(b),
    ("GET", "/api/variables"): lambda q, b: api_variables(),
    ("GET", "/api/ringtones"): lambda q, b: api_ringtones(),
    ("POST", "/api/ringtone/play"): lambda q, b: api_ringtone_play(b),
    ("GET", "/api/payloads"): lambda q, b: api_payloads(),
    ("POST", "/api/payload/run"): lambda q, b: api_payload_run(b),
    ("GET", "/api/payload/log"): lambda q, b: api_payload_log(q),
}


# ---- WebSocket (RFC6455) minimo en stdlib -----------------------------------
WS_MAGIC = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

def ws_accept_key(key):
    return base64.b64encode(hashlib.sha1((key + WS_MAGIC).encode()).digest()).decode()

def ws_send(conn, data, opcode=0x1):
    """Envia un frame WS (text=0x1, binary=0x2, close=0x8). data: bytes o str."""
    if isinstance(data, str):
        data = data.encode()
    hdr = bytearray([0x80 | opcode])
    n = len(data)
    if n < 126:
        hdr.append(n)
    elif n < 65536:
        hdr.append(126); hdr += struct.pack(">H", n)
    else:
        hdr.append(127); hdr += struct.pack(">Q", n)
    try:
        conn.sendall(bytes(hdr) + data)
        return True
    except Exception:
        return False

def ws_recv(conn):
    """Lee un frame WS del cliente (desenmascara). Devuelve (opcode, payload) o None."""
    try:
        h = _recv_exact(conn, 2)
        if not h: return None
        b1, b2 = h[0], h[1]
        opcode = b1 & 0x0f
        masked = b2 & 0x80
        ln = b2 & 0x7f
        if ln == 126:
            ln = struct.unpack(">H", _recv_exact(conn, 2))[0]
        elif ln == 127:
            ln = struct.unpack(">Q", _recv_exact(conn, 8))[0]
        mask = _recv_exact(conn, 4) if masked else b"\0\0\0\0"
        payload = _recv_exact(conn, ln) if ln else b""
        if masked and payload:
            payload = bytes(payload[i] ^ mask[i % 4] for i in range(len(payload)))
        return (opcode, payload)
    except Exception:
        return None

def _recv_exact(conn, n):
    buf = b""
    while len(buf) < n:
        chunk = conn.recv(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return buf


def _ws_pty_resize(fd, rows, cols):
    import termios, fcntl, struct as _st
    try:
        fcntl.ioctl(fd, termios.TIOCSWINSZ, _st.pack("HHHH", int(rows), int(cols), 0, 0))
    except Exception:
        pass


class Handler(http.server.SimpleHTTPRequestHandler):
    server_version = "PagerServer/1.0"

    def __init__(self, *a, **kw):
        super().__init__(*a, directory=WEBROOT, **kw)

    def log_message(self, fmt, *args):
        pass  # quiet

    # ---- WebSocket upgrade ---------------------------------------------------
    def _is_ws(self):
        return (self.headers.get("Upgrade", "").lower() == "websocket" and
                "upgrade" in self.headers.get("Connection", "").lower())

    def _ws_handshake(self):
        key = self.headers.get("Sec-WebSocket-Key")
        if not key:
            self.send_error(400); return False
        self.send_response(101)
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", ws_accept_key(key))
        self.end_headers()
        return True

    def _ws_terminal(self):
        """PTY-backed interactive shell over WebSocket (xterm.js)."""
        import pty, termios, fcntl, select as _sel, signal
        if not self._ws_handshake():
            return
        conn = self.connection
        pid, fd = pty.fork()
        if pid == 0:
            # child: exec a login shell
            os.environ["TERM"] = "xterm-256color"
            try:
                os.execvp("/bin/bash", ["/bin/bash", "-l"])
            except Exception:
                os.execvp("/bin/sh", ["/bin/sh"])
            os._exit(1)
        # parent: bridge PTY <-> WS
        try:
            while True:
                r, _, _ = _sel.select([conn, fd], [], [], 0.2)
                if conn in r:
                    frame = ws_recv(conn)
                    if frame is None:
                        break
                    op, data = frame
                    if op == 0x8:  # close
                        break
                    if op in (0x1, 0x2) and data:
                        try:
                            msg = json.loads(data.decode("utf-8", "ignore"))
                            if isinstance(msg, dict) and msg.get("t") == "resize":
                                _ws_pty_resize(fd, msg.get("rows", 24), msg.get("cols", 80))
                                continue
                        except Exception:
                            pass
                        try: os.write(fd, data)
                        except OSError: break
                if fd in r:
                    try:
                        out = os.read(fd, 4096)
                    except OSError:
                        break
                    if not out:
                        break
                    if not ws_send(conn, out, opcode=0x1):
                        break
        finally:
            try: os.close(fd)
            except Exception: pass
            try:
                os.kill(pid, signal.SIGKILL); os.waitpid(pid, 0)
            except Exception: pass

    def _ws_recon(self):
        """Pushes recon device deltas while a scan is active."""
        import time as _t
        if not self._ws_handshake():
            return
        conn = self.connection
        last = -1
        try:
            while True:
                frame = None
                # non-blocking check for client close
                conn.setblocking(False)
                try:
                    import select as _sel
                    r, _, _ = _sel.select([conn], [], [], 0)
                    if r:
                        frame = ws_recv(conn)
                        if frame and frame[0] == 0x8:
                            break
                except Exception:
                    pass
                conn.setblocking(True)
                # poll devices + stats and push
                dev = PINEAP.call(CMD["send_devices"], {"page": 0})
                stats = api_recon_stats()
                payload = json.dumps({"devicelist": dev.get("devicelist", []),
                                      "stats": stats})
                if not ws_send(conn, payload, opcode=0x1):
                    break
                _t.sleep(1.0)
        except Exception:
            pass

    def _json(self, obj, code=200):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _route(self, method):
        parsed = urlparse(self.path)
        key = (method, parsed.path)
        if key in ROUTES:
            q = parse_qs(parsed.query)
            body = {}
            if method == "POST":
                ln = int(self.headers.get("Content-Length", 0) or 0)
                if ln:
                    try: body = json.loads(self.rfile.read(ln) or b"{}")
                    except Exception: body = {}
            try:
                self._json(ROUTES[key](q, body))
            except Exception as e:
                self._json({"error": 1, "message": f"server error: {e}"}, 500)
            return True
        return False

    # ---- segundo root estatico: /community/<...> -> COMMUNITY_DIR ------------
    def _serve_community(self):
        """Sirve el arbol community/ (temas, assets PNG, components JSON, ringtones).

        El frontend hace fetch de /community/themes/<id>/theme.json, .../assets/x.png
        y components/*.json. Normalizamos la ruta con _safe_under() para que NUNCA se
        pueda salir de COMMUNITY_DIR (anti path-traversal)."""
        rel = urlparse(self.path).path[len("/community/"):]
        full = _safe_under(COMMUNITY_DIR, rel)
        if not full or not os.path.isfile(full):
            self._json({"error": 1, "message": "not found"}, 404)
            return
        self._send_file(full)

    def _send_file(self, full, ctype=None):
        """Envia un archivo del disco con Content-Type adivinado por extension."""
        if ctype is None:
            import mimetypes
            ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
            # .rtttl / .json sin tipo claro
            if full.endswith(".rtttl"):
                ctype = "text/plain; charset=utf-8"
            elif full.endswith(".json"):
                ctype = "application/json"
        try:
            with open(full, "rb") as f:
                data = f.read()
        except Exception as e:
            self._json({"error": 1, "message": f"read error: {e}"}, 500)
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(data)
        except Exception:
            pass

    def do_GET(self):
        # WebSocket endpoints (xterm terminal / live recon)
        if self._is_ws():
            path = urlparse(self.path).path
            if path == "/ws/terminal":
                self._ws_terminal(); return
            if path == "/ws/recon":
                self._ws_recon(); return
            self.send_error(404); return
        # GET /api/ringtone/<name>.rtttl -> raw del tono (para Web Audio en el front)
        rpath = urlparse(self.path).path
        if rpath.startswith("/api/ringtone/") and rpath.endswith(".rtttl"):
            name = unquote(rpath[len("/api/ringtone/"):])
            data, ctype = api_ringtone_raw(name)
            if data is None:
                self._json({"error": 1, "message": "ringtone not found"}, 404)
            else:
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                try: self.wfile.write(data)
                except Exception: pass
            return
        if self.path.startswith("/api/"):
            if not self._route("GET"):
                self._json({"error": 1, "message": "not found"}, 404)
            return
        # segundo root estatico: /community/* -> COMMUNITY_DIR (temas/assets/ringtones)
        if urlparse(self.path).path.startswith("/community/"):
            self._serve_community()
            return
        # static files (frontend)
        super().do_GET()

    def do_POST(self):
        if not self._route("POST"):
            self._json({"error": 1, "message": "not found"}, 404)


class ThreadingHTTP(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


WEBROOT = "/opt/pineapple-pager/frontend"


def main():
    global PINEAP, WEBROOT
    ap = argparse.ArgumentParser()
    ap.add_argument("--sock", default=SOCK_PATH)
    ap.add_argument("--root", default=WEBROOT)
    ap.add_argument("--port", type=int, default=1471)
    ap.add_argument("--bind", default="0.0.0.0")
    args = ap.parse_args()
    WEBROOT = args.root
    PINEAP = PineapClient(args.sock)
    srv = ThreadingHTTP((args.bind, args.port), Handler)
    print(f"[pager-server] UI+API on http://{args.bind}:{args.port}  "
          f"root={WEBROOT}  sock={args.sock}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
