#!/usr/bin/env python3
"""proxy.py - sirve el VIEWER estatico + hace de puente same-origin al firmware.

PROBLEMA (CORS / cookies):
  El firmware del WiFi Pineapple Pager sirve su API y sus WebSockets en :1471 y
  autentica con la cookie AUTH_<ServerId>. Los navegadores NO permiten setear
  headers (ni, en cross-site, la cookie) en el handshake de un WebSocket, y el
  fetch de login choca con CORS/SameSite si la app se sirve desde otro origen.

SOLUCION (este proxy):
  Un unico origen (el del proxy, p.ej. http://127.0.0.1:8471) sirve:
    - los estaticos del viewer (index.html, app.js, style.css, y las fuentes/
      imagenes de ../../frontend/assets vía ruta relativa) ;
    - TODO lo que empiece con /api/  -> reenviado al firmware (http y ws).
  Asi el navegador habla SIEMPRE con el mismo origen: el login setea la cookie
  AUTH_<sid> same-origin, y los WS screen/keys la mandan solos. El proxy copia
  cookies/headers en ambos sentidos. Cero CORS.

USO:
  pip install websockets        # (requerido para el puente WebSocket)
  python3 proxy.py --firmware 127.0.0.1:1471 --listen 0.0.0.0:8471
  # luego abrir   http://<IP>:8471/   (sin ?host= -> same-origin = el proxy)

Nota: usa solo stdlib + 'websockets'. Un hilo por conexion (aiohttp no hace
falta). Pensado para 1 usuario (el visor del Pager), no para alta carga.
"""

import argparse
import asyncio
import base64
import http.client
import mimetypes
import os
import subprocess
import sys
import urllib.parse

try:
    import websockets  # type: ignore
except Exception:
    websockets = None

HERE = os.path.dirname(os.path.abspath(__file__))
# raiz de estaticos: el viewer; permitimos tambien subir a ../../frontend/assets
VIEWER_DIR = HERE
REPO_FRONTEND = os.path.normpath(os.path.join(HERE, "..", "..", "frontend"))

SCREEN_PATH = "/api/pager/display/screen.ws"
KEYS_PATH = "/api/pager/input/keys.ws"

# Script que enciende/apaga el stack del Pager (menos el viewer). Se invoca via
# sudo (sudoers acotado). El boton de power de la UI pega a /power/{on,off,status}
# en ESTE proxy (no al firmware): por eso debe sobrevivir al apagado.
POWER_SCRIPT = os.environ.get(
    "PAGER_POWER_SCRIPT",
    os.path.normpath(os.path.join(HERE, "..", "scripts", "pager_power.sh")))

# ---------------------------------------------------------------------------
# LED bridge: espejo del estado REAL de los LEDs que controla el firmware.
# ---------------------------------------------------------------------------
# El firmware del Pager (pineapplepager.utils.SetDpadLED / SetDPADLEDColor)
# escribe el color del D-PAD como brillo por-canal en sysfs:
#     /sys/class/leds/<pos>-led-<color>/brightness   (0..255)
# Bajo qemu-user NO hay chroot, pero el qemu PARCHEADO (do_guest_openat)
# redirige los open() de /sys del guest al rootfs, asi que el estado REAL de
# los LEDs vive en  <ROOTFS>/sys/class/leds/<name>/brightness  (archivos
# normales, escritos por el firmware; lo confirmamos: a-button-led/b-button-led
# arrancan en 1 — los puso el firmware, no prepare.sh que los deja en 0).
#
# El hardware del Pager solo tiene LEDs ROJO+VERDE por posicion del D-PAD (no
# hay azul: no existe <pos>-led-blue en el rootfs), mas dos LEDs de boton:
#   a-button-led -> boton A/SELECT (verde)   b-button-led -> boton B/BACK (rojo)
# Este endpoint lee esos brillos y los expone como JSON para que el viewer
# pinte el color REAL (poller en app.js). Ruta configurable por env.
LEDS_DIR = os.environ.get(
    "PAGER_LEDS_DIR",
    os.path.normpath(os.path.join(HERE, "..", "rootfs", "sys", "class", "leds")))

# posiciones del D-PAD con canales rojo/verde (hardware real: sin azul)
_LED_POS = ("up", "down", "left", "right")
_LED_CHANNELS = ("red", "green")


import re as _re
# Todos los digitos presentes, en orden (cada uno es un write 0/1 del firmware).
_DIGITS = _re.compile(rb"\d")


def _read_brightness(path):
    """Lee un archivo brightness del sysfs del firmware. Devuelve int 0..1 (el
    canal es ON/OFF) o None si el archivo no existe / esta vacio.

    OJO (qemu-user + rootfs REAL, CONFIRMADO por strace + hexdump del firmware):
      - El brightness del kernel real es un atributo sysfs: REEMPLAZA su contenido
        en cada write y el firmware del Pager escribe 0/1 por canal (on/off, NO un
        gradiente 0..255).
      - En el rootfs del qemu el nodo es un archivo REGULAR. El firmware aplica el
        color del D-pad VARIAS veces (hay una transicion/animacion al cambiarlo) y
        esos writes NO truncan de forma visible bajo qemu-user: el archivo queda
        como BYTES NUL de relleno + los digitos ASCII de cada apply, SIN separador.
        Ej. tras pasar de RED a GREEN:
            up-red   = NUL... + "1010"   (1->0->1->0 ; ultimo = 0)
            up-green = NUL... + "0001"   (0->0->0->1 ; ultimo = 1)
            up-blue  = NUL... + "0000"
        El estado REAL (lo que el kernel/hardware mostraria) es el ULTIMO valor
        escrito = el ULTIMO digito. Un strip() de whitespace NO quita los NUL y
        int("1010")=1010 seria erroneo; por eso tomamos el ULTIMO DIGITO.
    """
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except (FileNotFoundError, OSError):
        return None
    digits = _DIGITS.findall(raw)
    if not digits:
        return None
    # Ultimo write gana. El firmware usa 0/1 por canal (on/off).
    try:
        return max(0, min(255, int(digits[-1])))
    except ValueError:
        return None


def read_leds():
    """Devuelve el estado REAL de los LEDs del Pager leido del rootfs.

    Forma:
      {
        "ok": true,
        "dir": "<LEDS_DIR>",
        "dpad": {
          "up":    {"r": 0,  "g": 200, "b": 0, "on": true},
          "down":  {...}, "left": {...}, "right": {...}
        },
        "buttons": {
          "a": {"on": true, "v": 1},   # boton A/SELECT  (verde)
          "b": {"on": true, "v": 1}    # boton B/BACK    (rojo)
        }
      }
    'r'/'g' son el brillo 0..255 de cada canal (b siempre 0: no hay hardware
    azul). 'on' = algun canal > 0. Si un canal no fue escrito por el firmware
    todavia (archivo vacio) se reporta 0 (apagado), que es el estado fisico.
    """
    dpad = {}
    for pos in _LED_POS:
        r = _read_brightness(os.path.join(LEDS_DIR, f"{pos}-led-red", "brightness"))
        g = _read_brightness(os.path.join(LEDS_DIR, f"{pos}-led-green", "brightness"))
        rv = r or 0
        gv = g or 0
        dpad[pos] = {"r": rv, "g": gv, "b": 0, "on": (rv > 0 or gv > 0)}

    buttons = {}
    for name, key in (("a-button-led", "a"), ("b-button-led", "b")):
        v = _read_brightness(os.path.join(LEDS_DIR, name, "brightness")) or 0
        buttons[key] = {"on": v > 0, "v": v}

    return {"ok": True, "dir": LEDS_DIR, "dpad": dpad, "buttons": buttons}


def run_power(action):
    """Ejecuta pager_power.sh {on|off|status}. Devuelve (ok, texto)."""
    if action not in ("on", "off", "status"):
        return False, "accion invalida"
    cmd = ["sudo", "-n", "/bin/bash", POWER_SCRIPT, action]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=40)
        txt = (out.stdout or out.stderr or "").strip()
        return (out.returncode == 0), txt
    except Exception as e:
        return False, "power error: %r" % e


# ---------------------------------------------------------------------------
# Resolucion segura de rutas estaticas
# ---------------------------------------------------------------------------
def resolve_static(path):
    """Mapea una URL path a un archivo en disco (viewer + ../../frontend)."""
    path = urllib.parse.unquote(path.split("?", 1)[0])
    if path in ("", "/"):
        path = "/index.html"
    rel = path.lstrip("/")

    # Permitir ../../frontend/... explicito (las fuentes/imagenes del chasis)
    # mapeando el prefijo '../../frontend/' a REPO_FRONTEND.
    candidates = []
    norm = os.path.normpath(rel)
    # 1) relativo al viewer
    candidates.append(os.path.normpath(os.path.join(VIEWER_DIR, norm)))
    # 2) si pidieron algo bajo frontend/ (por el href ../../frontend/...)
    #    el navegador normaliza ../../frontend/assets/... a /frontend/assets/...
    #    cuando la pagina se sirve desde la raiz; soportamos ambos.
    if norm.startswith("frontend" + os.sep):
        candidates.append(os.path.normpath(os.path.join(REPO_FRONTEND, norm[len("frontend/"):])))
    candidates.append(os.path.normpath(os.path.join(REPO_FRONTEND, norm)))

    for c in candidates:
        # jail: debe caer dentro de VIEWER_DIR o REPO_FRONTEND
        if (c.startswith(VIEWER_DIR + os.sep) or c == VIEWER_DIR or
                c.startswith(REPO_FRONTEND + os.sep) or c == REPO_FRONTEND):
            if os.path.isfile(c):
                return c
    return None


# ---------------------------------------------------------------------------
# Puente HTTP (reenvia /api/* no-ws al firmware, copiando cookies)
# ---------------------------------------------------------------------------
# Reintentos ante cortes transitorios del firmware (ocupado / reiniciando la
# conexion / token recien expirado mientras levanta de nuevo). El firmware
# ocasionalmente cierra la conexion TCP o tarda un instante tras un reinicio
# interno; sin reintentar, eso se traducia en un 502 al navegar (p.ej. al portal
# de payloads). Reintentamos un par de veces con backoff corto antes de rendirnos.
# Budget: 5 intentos con backoff 0.3,0.6,1.2,2.4 (cap 2.5) -> ~5s total, suficiente
# para aguantar un blip o un reinicio interno corto del firmware sin tirar 502.
# Un reinicio COMPLETO del qemu (varios segundos) lo cubre el reintento del
# cliente (app.js: login con backoff + reconexion infinita con backoff).
HTTP_RETRIES = int(os.environ.get("PAGER_PROXY_HTTP_RETRIES", "5"))
HTTP_BACKOFF = float(os.environ.get("PAGER_PROXY_HTTP_BACKOFF", "0.3"))   # s, se duplica
HTTP_BACKOFF_CAP = float(os.environ.get("PAGER_PROXY_HTTP_BACKOFF_CAP", "2.5"))
HTTP_TIMEOUT = float(os.environ.get("PAGER_PROXY_HTTP_TIMEOUT", "15"))

# Errores que consideramos transitorios (vale la pena reintentar).
import socket as _socket
import time as _time
_TRANSIENT = (
    ConnectionError,                 # incluye ConnectionResetError/Refused/Aborted
    _socket.timeout,
    http.client.BadStatusLine,       # firmware cerro sin responder
    http.client.RemoteDisconnected,  # idem (py3: subclase de ConnectionResetError)
    http.client.CannotSendRequest,
    OSError,                         # errno transitorios de socket
)


def _forward_once(firmware_host, method, path, headers, body):
    conn = http.client.HTTPConnection(firmware_host, timeout=HTTP_TIMEOUT)
    fwd_headers = {}
    for k, v in headers.items():
        lk = k.lower()
        if lk in ("host", "connection", "content-length", "accept-encoding"):
            continue
        fwd_headers[k] = v
    fwd_headers["Host"] = firmware_host
    try:
        conn.request(method, path, body=body, headers=fwd_headers)
        r = conn.getresponse()
        data = r.read()
        resp_headers = []
        for k, v in r.getheaders():
            if k.lower() in ("transfer-encoding", "connection", "content-length"):
                continue
            resp_headers.append((k, v))
        return r.status, r.reason, resp_headers, data
    finally:
        conn.close()


def forward_http(firmware_host, method, path, headers, body):
    """Reenvia al firmware con reintentos. Devuelve (status, reason, headers, body).

    Si tras HTTP_RETRIES intentos sigue fallando con un error transitorio,
    devuelve un 502 legible (ultimo recurso). Un 5xx DEVUELTO por el firmware
    (no una excepcion) tambien se reintenta para metodos idempotentes-ish de
    navegacion (GET/HEAD) y para el login (POST /api/login), porque el 502/503
    tipico aqui es "todavia levantando".
    """
    delay = HTTP_BACKOFF
    last_err = None
    retriable_status = {502, 503, 504}
    is_login = path.split("?", 1)[0] == "/api/login"
    retry_on_5xx = (method in ("GET", "HEAD")) or is_login
    for attempt in range(HTTP_RETRIES):
        try:
            status, reason, rhdrs, rbody = _forward_once(
                firmware_host, method, path, headers, body)
            if status in retriable_status and retry_on_5xx and attempt < HTTP_RETRIES - 1:
                _time.sleep(delay); delay = min(delay * 2, HTTP_BACKOFF_CAP)
                continue
            return status, reason, rhdrs, rbody
        except _TRANSIENT as e:
            last_err = e
            if attempt < HTTP_RETRIES - 1:
                _time.sleep(delay); delay = min(delay * 2, HTTP_BACKOFF_CAP)
                continue
            break
        except Exception as e:
            # no transitorio: no insistir
            last_err = e
            break
    msg = ("proxy: firmware no respondio tras %d intentos: %r"
           % (HTTP_RETRIES, last_err)).encode()
    return 502, "Bad Gateway", [("Content-Type", "text/plain")], msg


# ---------------------------------------------------------------------------
# Servidor asyncio: un handler que distingue WS de HTTP a mano
# ---------------------------------------------------------------------------
async def read_http_request(reader):
    """Lee request line + headers (+ body si Content-Length). Devuelve dict o None."""
    line = await reader.readline()
    if not line:
        return None
    try:
        method, target, version = line.decode("latin1").rstrip("\r\n").split(" ", 2)
    except ValueError:
        return None
    headers = {}
    while True:
        h = await reader.readline()
        if h in (b"\r\n", b"\n", b""):
            break
        try:
            k, v = h.decode("latin1").rstrip("\r\n").split(":", 1)
            headers[k.strip()] = v.strip()
        except ValueError:
            continue
    body = b""
    clen = headers.get("Content-Length")
    if clen:
        try:
            body = await reader.readexactly(int(clen))
        except asyncio.IncompleteReadError as e:
            body = e.partial
    return {"method": method, "target": target, "version": version,
            "headers": headers, "body": body}


def http_response_bytes(status, reason, headers, body):
    head = [f"HTTP/1.1 {status} {reason}"]
    hdr_has = {k.lower() for k, _ in headers}
    for k, v in headers:
        head.append(f"{k}: {v}")
    if "content-length" not in hdr_has:
        head.append(f"Content-Length: {len(body)}")
    head.append("Connection: close")
    head.append("")
    head.append("")
    return "\r\n".join(head).encode("latin1") + body


async def fw_connect(firmware_host, path, cookie):
    """Cliente WS hacia el firmware (manda la cookie AUTH en el handshake)."""
    uri = f"ws://{firmware_host}{path}"
    extra = {"Cookie": cookie} if cookie else {}
    try:
        return await websockets.connect(uri, additional_headers=extra, max_size=None)
    except TypeError:
        # websockets < 11
        return await websockets.connect(uri, extra_headers=extra, max_size=None)


def make_handler(firmware_host):
    async def handle(reader, writer):
        peer = writer.get_extra_info("peername")
        try:
            req = await read_http_request(reader)
            if not req:
                writer.close(); return

            target = req["target"]
            headers = req["headers"]
            path_only = target.split("?", 1)[0]
            is_upgrade = headers.get("Upgrade", "").lower() == "websocket"

            # ---- WebSocket upgrade: solo para los dos endpoints del pager ----
            if is_upgrade and path_only in (SCREEN_PATH, KEYS_PATH):
                if websockets is None:
                    writer.write(http_response_bytes(
                        500, "No websockets", [("Content-Type", "text/plain")],
                        b"instala el modulo 'websockets' (pip install websockets)"))
                    await writer.drain(); writer.close(); return
                cookie = headers.get("Cookie", "")
                await serve_ws_upgrade(reader, writer, req, firmware_host, target, cookie)
                return

            # ---- Power local del proxy: /power/{on,off,status} ----
            # Enciende/apaga el stack del Pager sin matar este viewer. No se
            # reenvia al firmware (de hecho puede estar apagado).
            if path_only.startswith("/power/"):
                action = path_only[len("/power/"):].strip("/")
                ok, txt = await asyncio.get_event_loop().run_in_executor(
                    None, run_power, action)
                import json as _json
                payload = _json.dumps({"ok": ok, "state": txt}).encode()
                writer.write(http_response_bytes(
                    200 if ok else 500, "OK" if ok else "Error",
                    [("Content-Type", "application/json"),
                     ("Cache-Control", "no-cache")], payload))
                await writer.drain(); writer.close(); return

            # ---- LED bridge local: /leds (GET) ----
            # Espejo del estado REAL de los LEDs que el firmware escribe en
            # sysfs (redirigido al rootfs por el qemu parcheado). No toca el
            # firmware: lee archivos locales. El poller de app.js lo consulta
            # para pintar el color real del D-PAD.
            if path_only == "/leds":
                import json as _json
                try:
                    payload = _json.dumps(read_leds()).encode()
                    writer.write(http_response_bytes(
                        200, "OK",
                        [("Content-Type", "application/json"),
                         ("Cache-Control", "no-store"),
                         ("Access-Control-Allow-Origin", "*")], payload))
                except Exception as e:
                    payload = _json.dumps({"ok": False, "error": str(e)}).encode()
                    writer.write(http_response_bytes(
                        500, "Error",
                        [("Content-Type", "application/json")], payload))
                await writer.drain(); writer.close(); return

            # ---- HTTP: /api/* -> firmware ; resto -> estatico ----
            if path_only.startswith("/api/"):
                status, reason, rhdrs, rbody = await asyncio.get_event_loop().run_in_executor(
                    None, forward_http, firmware_host, req["method"], target,
                    headers, req["body"])
                writer.write(http_response_bytes(status, reason, rhdrs, rbody))
                await writer.drain(); writer.close(); return

            # estatico
            fpath = resolve_static(path_only)
            if not fpath:
                writer.write(http_response_bytes(
                    404, "Not Found", [("Content-Type", "text/plain")], b"404"))
                await writer.drain(); writer.close(); return
            ctype = mimetypes.guess_type(fpath)[0] or "application/octet-stream"
            with open(fpath, "rb") as f:
                data = f.read()
            writer.write(http_response_bytes(
                200, "OK", [("Content-Type", ctype),
                            ("Cache-Control", "no-cache")], data))
            await writer.drain(); writer.close()
        except Exception as e:
            try:
                writer.write(http_response_bytes(
                    502, "Bad Gateway", [("Content-Type", "text/plain")],
                    f"proxy error: {e}".encode()))
                await writer.drain()
            except Exception:
                pass
            try:
                writer.close()
            except Exception:
                pass
    return handle


# ---------------------------------------------------------------------------
# Puente WebSocket browser <-> firmware.
#
# Para el lado del NAVEGADOR implementamos el framing RFC6455 a mano (robusto
# y sin depender de internals de 'websockets'): leemos del StreamReader ya
# upgradeado y escribimos frames sin mascara (server role). Para el lado del
# FIRMWARE usamos el cliente de la libreria 'websockets', que si manda la
# cookie AUTH en el handshake (additional_headers).
# ---------------------------------------------------------------------------
import hashlib
import struct

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

OP_CONT, OP_TEXT, OP_BIN, OP_CLOSE, OP_PING, OP_PONG = 0x0, 0x1, 0x2, 0x8, 0x9, 0xA


def ws_accept(key):
    return base64.b64encode(
        hashlib.sha1((key + WS_GUID).encode()).digest()).decode()


def ws_encode(data, opcode):
    """Frame server->browser (sin mascara, un solo fragmento)."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    n = len(data)
    hdr = bytearray([0x80 | opcode])  # FIN + opcode
    if n < 126:
        hdr.append(n)
    elif n < 65536:
        hdr.append(126); hdr += struct.pack("!H", n)
    else:
        hdr.append(127); hdr += struct.pack("!Q", n)
    return bytes(hdr) + data


async def ws_read_frame(reader):
    """Lee un frame del navegador (viene enmascarado). Devuelve (opcode, bytes) o None."""
    hdr = await reader.readexactly(2)
    b0, b1 = hdr[0], hdr[1]
    opcode = b0 & 0x0F
    masked = b1 & 0x80
    ln = b1 & 0x7F
    if ln == 126:
        ln = struct.unpack("!H", await reader.readexactly(2))[0]
    elif ln == 127:
        ln = struct.unpack("!Q", await reader.readexactly(8))[0]
    mask = await reader.readexactly(4) if masked else b"\x00\x00\x00\x00"
    payload = bytearray(await reader.readexactly(ln))
    if masked:
        for i in range(ln):
            payload[i] ^= mask[i & 3]
    return opcode, bytes(payload)


async def browser_to_fw(reader, up):
    """Navegador -> firmware (texto = teclas; binario improbable)."""
    try:
        while True:
            frame = await ws_read_frame(reader)
            if frame is None:
                break
            opcode, payload = frame
            if opcode == OP_CLOSE:
                break
            elif opcode == OP_PING:
                continue  # el cliente no deberia pingear; ignoramos
            elif opcode in (OP_TEXT, OP_BIN, OP_CONT):
                if opcode == OP_TEXT:
                    await up.send(payload.decode("utf-8", "replace"))
                else:
                    await up.send(payload)
    except (asyncio.IncompleteReadError, ConnectionError):
        pass
    except Exception:
        pass
    finally:
        try:
            await up.close()
        except Exception:
            pass


async def fw_to_browser(up, writer):
    """Firmware -> navegador (frames binarios del screen / texto)."""
    try:
        async for msg in up:
            if isinstance(msg, (bytes, bytearray)):
                writer.write(ws_encode(bytes(msg), OP_BIN))
            else:
                writer.write(ws_encode(msg, OP_TEXT))
            await writer.drain()
    except Exception:
        pass
    finally:
        try:
            writer.write(ws_encode(b"", OP_CLOSE))
            await writer.drain()
        except Exception:
            pass
        try:
            writer.close()
        except Exception:
            pass


async def serve_ws_upgrade(reader, writer, req, firmware_host, target, cookie):
    """Handshake WS con el navegador y puente bidireccional al firmware."""
    key = req["headers"].get("Sec-WebSocket-Key")
    if not key:
        writer.write(http_response_bytes(400, "Bad Request", [], b"no ws key"))
        await writer.drain(); writer.close(); return

    # conectar primero al firmware, con reintentos ante cortes transitorios
    # (firmware ocupado / reiniciando). Solo si agotamos los intentos devolvemos
    # un error HTTP legible en vez de tirar un 502 al primer fallo.
    up = None
    delay = HTTP_BACKOFF
    last_err = None
    for attempt in range(HTTP_RETRIES):
        try:
            up = await fw_connect(firmware_host, target, cookie)
            break
        except Exception as e:
            last_err = e
            if attempt < HTTP_RETRIES - 1:
                await asyncio.sleep(delay); delay = min(delay * 2, HTTP_BACKOFF_CAP)
                continue
    if up is None:
        writer.write(http_response_bytes(
            502, "Bad Gateway", [("Content-Type", "text/plain")],
            f"no se pudo abrir WS al firmware tras {HTTP_RETRIES} intentos: {last_err}".encode()))
        await writer.drain(); writer.close(); return

    accept = ws_accept(key)
    resp = (
        "HTTP/1.1 101 Switching Protocols\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Accept: {accept}\r\n\r\n"
    )
    writer.write(resp.encode()); await writer.drain()

    try:
        await asyncio.gather(
            browser_to_fw(reader, up),
            fw_to_browser(up, writer),
        )
    finally:
        try:
            await up.close()
        except Exception:
            pass


async def amain():
    ap = argparse.ArgumentParser(description="Viewer proxy (static + same-origin bridge a :1471)")
    ap.add_argument("--firmware", default="127.0.0.1:1471",
                    help="host:puerto del firmware (default 127.0.0.1:1471)")
    ap.add_argument("--listen", default="0.0.0.0:8471",
                    help="host:puerto donde servir el viewer (default 0.0.0.0:8471)")
    a = ap.parse_args()

    if websockets is None:
        print("[WARN] modulo 'websockets' no instalado: el puente WS NO funcionara.",
              file=sys.stderr)
        print("       instalalo con: pip install websockets", file=sys.stderr)

    lhost, lport = a.listen.rsplit(":", 1)
    server = await asyncio.start_server(make_handler(a.firmware), lhost, int(lport))
    addr = ", ".join(str(s.getsockname()) for s in server.sockets)
    print(f"[proxy] viewer en http://{a.listen}/  ->  firmware {a.firmware}")
    print(f"[proxy] escuchando en {addr}")
    print(f"[proxy] abri:  http://{lhost if lhost!='0.0.0.0' else '127.0.0.1'}:{lport}/")
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        pass
