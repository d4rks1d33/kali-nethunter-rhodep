#!/usr/bin/env python3
"""
rhodep-qsr4-decode.py

Decodificador offline COMPLETO de logs DIAG F3 Qshrink-4 de un modem Qualcomm.

Es la fase final del pipeline (ver rhodep-qsr4-extract.py para el deframer HDLC
validado). Este programa:

  1. Carga la tabla id -> (ss_mask, ssid, line, file, format_string) desde el
     QDB string database (qdsp6m.qdb): header 64 bytes + un unico stream zlib
     desde offset 0x40. La seccion <Content>...<\\Content> tiene lineas
     "hash:ss_mask:ssid:line:file:format_string" (split por ':' maxsplit=5).
     El "hash" (decimal) es el message id de 32 bits de los frames 0x99.
     Se cachea en JSON para arranques rapidos.

  2. Deframe DGPK/HDLC (reusa la logica de rhodep-qsr4-extract.py):
       Record DGPK = "DGPK"(4) + u32 length_LE + u32 uptime_ms_LE + payload.
       Los frames HDLC cruzan records -> se concatenan los payloads antes de
       deframear. 0x7d unescape, descarta CRC16, unwrap 0x98 (multi-radio).

  3. Decodifica cada frame:
       - 0x99 DIAG_QSR4_EXT_MSG_TERSE_F:
           ts(u64)@4, msgid(u32)@12, build_tag(u16)@16, args@18+.
           Resuelve el format_string via el QDB y aplica los args binarios con
           un mini motor printf (little-endian).
       - 0x79 DIAG_EXT_MSG_F (texto plano):
           ts_type@1, num_args@2, drop_cnt@3, ts(u64)@4, line(u16)@12,
           ssid(u16)@14, ss_mask(u32)@16, args = num_args*u32 @20,
           luego fmt NUL-terminado y filename NUL-terminado inline.
       - 0x10 log_packet: logcode(u16)@6, ts(u64)@8 (solo cabecera, hex payload).

  4. Emite el log decodificado ordenado por timestamp con, por cada linea:
       [ts] (uptime del record) [ssid/file:line] mensaje_renderizado

Uso:
  rhodep-qsr4-decode.py CAPTURA.bin --qdb qdsp6m.qdb --out decoded.txt
  rhodep-qsr4-decode.py CAPTURA.bin --qdb qdsp6m.qdb --window-ms 200
"""

import argparse
import json
import os
import re
import struct
import sys
import zlib
from collections import Counter

# ---------------------------------------------------------------------------
# Constantes de framing (identicas al extractor validado)
# ---------------------------------------------------------------------------
HDLC_FLAG = 0x7E
HDLC_ESC = 0x7D
HDLC_XOR = 0x20
DGPK_MAGIC = b"DGPK"
QSR4_BUILD_TAG = 0x3A1C

CMD_QSR4 = 0x99   # DIAG_QSR4_EXT_MSG_TERSE_F
CMD_EXT = 0x79    # DIAG_EXT_MSG_F (texto plano)
CMD_LOG = 0x10    # log_packet

# Timestamp Qualcomm: 48-bit ticks de 1.25ms en bits altos + 1/32768 en bajos.
# Formato clasico: valor >> 16 = segundos*... En la practica para ORDENAR basta
# con el entero crudo. Para mostrar ms relativos usamos el tick nativo:
#   ts_tick = raw ; 1 tick ~= 1/32768 s en los 16 bits bajos de la parte baja.
# Qualcomm diag ts: (ts >> 16) son unidades de 1.25ms? Hay varias convenciones.
# Usamos la conversion estandar DIAG: ms = raw / 1000 / 1000? No.
# Convencion verificada para estos devices (56-bit, base 1.25ms):
#   segundos = raw * (1.25e-3 / 32768.0)  -> demasiado pequeno.
# Mantenemos el raw para ordenar y derivamos ms relativos por diferencia de raw
# escalada empiricamente. La relacion raw->ms se calcula al vuelo (ver
# ts_to_ms_factor) comparando el span raw con el span uptime_ms de los records.


# ---------------------------------------------------------------------------
# DGPK container
# ---------------------------------------------------------------------------
def iter_dgpk_payloads(data):
    """Genera (uptime_ms, payload) por cada record DGPK valido."""
    off = 0
    n = len(data)
    while off + 12 <= n:
        if data[off:off + 4] != DGPK_MAGIC:
            nxt = data.find(DGPK_MAGIC, off + 1)
            if nxt < 0:
                break
            off = nxt
            continue
        length, uptime = struct.unpack_from("<II", data, off + 4)
        start = off + 12
        end = start + length
        if end > n:
            yield uptime, data[start:n]
            break
        yield uptime, data[start:end]
        off = end


# ---------------------------------------------------------------------------
# HDLC deframing (del extractor validado) - con mapeo a uptime del record
# ---------------------------------------------------------------------------
def _unescape(raw):
    out = bytearray()
    i = 0
    L = len(raw)
    while i < L:
        b = raw[i]
        if b == HDLC_ESC and i + 1 < L:
            out.append(raw[i + 1] ^ HDLC_XOR)
            i += 2
        else:
            out.append(b)
            i += 1
    return out


def deframe_with_uptime(data):
    """
    Deframe HDLC sobre el stream concatenado, pero recordando el uptime_ms del
    record DGPK en el que terminó cada frame (aproxima el tiempo de pared).
    Devuelve lista de (uptime_ms, frame_desescapado_sin_crc).
    """
    # Construye stream con marcas de uptime por offset de record.
    stream = bytearray()
    marks = []  # (offset_fin_de_record, uptime)
    for uptime, payload in iter_dgpk_payloads(data):
        stream.extend(payload)
        marks.append((len(stream), uptime))

    def uptime_at(pos):
        # primer record cuyo fin >= pos
        lo, hi = 0, len(marks)
        while lo < hi:
            mid = (lo + hi) // 2
            if marks[mid][0] < pos:
                lo = mid + 1
            else:
                hi = mid
        if lo < len(marks):
            return marks[lo][1]
        return marks[-1][1] if marks else 0

    frames = []
    raw = bytearray()
    start_pos = 0
    for pos, b in enumerate(stream):
        if b == HDLC_FLAG:
            if raw:
                fr = _unescape(raw)
                if len(fr) > 2:
                    frames.append((uptime_at(pos), bytes(fr[:-2])))
                raw = bytearray()
            start_pos = pos + 1
        else:
            raw.append(b)
    return frames


def unwrap_multi_radio(frame):
    if frame and frame[0] == 0x98:
        return frame[8:] if len(frame) >= 8 else b""
    return frame


# ---------------------------------------------------------------------------
# QDB string database
# ---------------------------------------------------------------------------
def load_qdb(qdb_path, cache_path=None):
    """
    Carga la tabla {id:int -> dict(ss_mask, ssid, line, file, fmt)} desde el qdb.
    Usa cache JSON si existe y es mas nuevo que el qdb.
    """
    if cache_path and os.path.exists(cache_path) and \
       os.path.getmtime(cache_path) >= os.path.getmtime(qdb_path):
        with open(cache_path) as f:
            raw = json.load(f)
        return {int(k): v for k, v in raw.items()}

    data = open(qdb_path, "rb").read()
    txt = zlib.decompress(data[0x40:])
    i = txt.find(b"<Content>")
    j = txt.find(b"<\\Content>")
    if i < 0 or j < 0:
        raise RuntimeError("seccion <Content> no encontrada en el qdb")
    body = txt[i + len(b"<Content>"):j]
    table = {}
    for line in body.split(b"\n"):
        line = line.strip(b"\r")
        if not line:
            continue
        parts = line.split(b":", 5)
        if len(parts) < 6:
            continue
        h, ssm, ssid, ln, fn, fmt = parts
        try:
            hid = int(h)
        except ValueError:
            continue
        table[hid] = {
            "ss_mask": ssm.decode("ascii", "replace"),
            "ssid": ssid.decode("ascii", "replace"),
            "line": ln.decode("ascii", "replace"),
            "file": fn.decode("ascii", "replace"),
            "fmt": fmt.decode("ascii", "replace"),
        }
    if cache_path:
        try:
            with open(cache_path, "w") as f:
                json.dump({str(k): v for k, v in table.items()}, f)
        except OSError:
            pass
    return table


# ---------------------------------------------------------------------------
# Mini motor printf para aplicar args binarios little-endian
# ---------------------------------------------------------------------------
# Captura especificadores printf: %[flags][width][.prec][length]conv
_SPEC_RE = re.compile(
    r"%"
    r"([-+ 0#]*)"          # flags
    r"(\d+|\*)?"           # width
    r"(\.\d+|\.\*)?"       # precision
    r"(hh|h|ll|l|L|z|j|t|q)?"  # length
    r"([diouxXeEfFgGaAcspn%])"  # conv
)


def render_format(fmt, args_bytes):
    """
    Renderiza fmt estilo printf consumiendo args_bytes (little-endian) en orden.
      %d/%i/%u/%x/%X/%o/%p         -> u32 (4 bytes)  [%p a veces 4]
      %ld/%lu/%lx ... (l)          -> u32 (4 bytes en este ABI de 32-bit)
      %lld/%llu/%llx (ll) y %...q  -> u64 (8 bytes)
      %c                           -> 1 byte
      %s                           -> string NUL-terminado inline
      %f/%e/%g (sin length)        -> doble? El FW suele pasar u32 raw; se
                                      muestra como u32 si no hay 8 bytes.
    Devuelve (texto_renderizado, ok_bool). Si faltan bytes, para y marca ok=False
    dejando el resto del fmt literal + volcado hex de args restantes.
    """
    out = []
    pos = 0
    off = 0
    n = len(args_bytes)
    ok = True

    def take(sz):
        nonlocal off
        if off + sz > n:
            return None
        v = args_bytes[off:off + sz]
        off += sz
        return v

    for m in _SPEC_RE.finditer(fmt):
        out.append(fmt[pos:m.start()])
        pos = m.end()
        flags, width, prec, length, conv = m.groups()

        if conv == "%":
            out.append("%")
            continue

        # width/prec dinamicos (*) consumen un u32 cada uno
        dyn_width = None
        dyn_prec = None
        if width == "*":
            b = take(4)
            if b is None:
                ok = False
                break
            dyn_width = struct.unpack("<i", b)[0]
        if prec == ".*":
            b = take(4)
            if b is None:
                ok = False
                break
            dyn_prec = struct.unpack("<i", b)[0]

        is64 = length in ("ll", "q", "j")
        # 'l' en ABI de 32 bits = 32 bits salvo doble ll

        if conv in ("d", "i"):
            b = take(8 if is64 else 4)
            if b is None:
                ok = False
                break
            val = struct.unpack("<q" if is64 else "<i", b)[0]
            spec = "%" + flags + (str(dyn_width) if dyn_width is not None else (width or "")) \
                + (("." + str(dyn_prec)) if dyn_prec is not None else (prec or "")) + "d"
            out.append(spec % val)
        elif conv in ("u", "o", "x", "X"):
            b = take(8 if is64 else 4)
            if b is None:
                ok = False
                break
            val = struct.unpack("<Q" if is64 else "<I", b)[0]
            spec = "%" + flags + (str(dyn_width) if dyn_width is not None else (width or "")) \
                + (("." + str(dyn_prec)) if dyn_prec is not None else (prec or "")) + conv
            out.append(spec % val)
        elif conv == "p":
            b = take(4)
            if b is None:
                ok = False
                break
            val = struct.unpack("<I", b)[0]
            out.append("0x%x" % val)
        elif conv == "c":
            b = take(4)  # char promovido a int (4 bytes) en varargs
            if b is None:
                ok = False
                break
            val = struct.unpack("<I", b)[0] & 0xFF
            out.append(chr(val) if 32 <= val < 127 else "\\x%02x" % val)
        elif conv == "s":
            # string NUL-terminado inline desde el offset actual
            end = args_bytes.find(b"\x00", off)
            if end < 0:
                s = args_bytes[off:]
                off = n
            else:
                s = args_bytes[off:end]
                off = end + 1
            out.append(s.decode("ascii", "replace"))
        elif conv in ("f", "F", "e", "E", "g", "G", "a", "A"):
            # doble de 8 bytes (varargs promueve float->double)
            b = take(8)
            if b is None:
                ok = False
                break
            val = struct.unpack("<d", b)[0]
            spec = "%" + flags + (width or "") + (prec or "") + conv
            try:
                out.append(spec % val)
            except Exception:
                out.append(repr(val))
        elif conv == "n":
            pass  # ignora
        else:
            out.append(m.group(0))

    out.append(fmt[pos:])
    text = "".join(out)
    if not ok:
        text += "  [ARGS INCOMPLETOS restantes=%s]" % args_bytes[off:].hex()
    elif off < n:
        extra = args_bytes[off:]
        # args sobrantes: util para detectar mismatch, pero no ruidoso si son 0s
        if extra.strip(b"\x00"):
            text += "  [args_extra=%s]" % extra.hex()
    return text, ok


# ---------------------------------------------------------------------------
# Decodificacion de frames individuales
# ---------------------------------------------------------------------------
def decode_99(fr, table):
    if len(fr) < 18:
        return None
    ts = struct.unpack_from("<Q", fr, 4)[0]
    msgid = struct.unpack_from("<I", fr, 12)[0]
    tag = struct.unpack_from("<H", fr, 16)[0]
    args = fr[18:]
    meta = table.get(msgid)
    if meta is None:
        return {
            "ts": ts, "cmd": 0x99, "msgid": msgid, "tag": tag,
            "ssid": "?", "loc": "<id 0x%08X no en qdb>" % msgid,
            "text": "<unresolved 0x%08X> args=%s" % (msgid, args.hex()),
            "resolved": False, "argok": False,
        }
    text, argok = render_format(meta["fmt"], args)
    return {
        "ts": ts, "cmd": 0x99, "msgid": msgid, "tag": tag,
        "ssid": meta["ssid"], "loc": "%s:%s" % (meta["file"], meta["line"]),
        "text": text, "resolved": True, "argok": argok,
        "fmt": meta["fmt"],
    }


def decode_79(fr):
    if len(fr) < 20:
        return None
    nargs = fr[2]
    ts = struct.unpack_from("<Q", fr, 4)[0]
    line = struct.unpack_from("<H", fr, 12)[0]
    ssid = struct.unpack_from("<H", fr, 14)[0]
    off = 20
    args = []
    for _ in range(nargs):
        if off + 4 > len(fr):
            break
        args.append(struct.unpack_from("<I", fr, off)[0])
        off += 4
    rest = fr[off:]
    parts = rest.split(b"\x00")
    fmt = parts[0].decode("ascii", "replace") if parts else ""
    fn = parts[1].decode("ascii", "replace") if len(parts) > 1 else ""
    # Aplica args u32 al fmt (el FW de 0x79 usa solo u32)
    text = apply_u32_args(fmt, args)
    loc = "%s:%d" % (fn, line) if fn else "line:%d" % line
    return {
        "ts": ts, "cmd": 0x79, "msgid": None, "tag": None,
        "ssid": str(ssid), "loc": loc, "text": text,
        "resolved": True, "argok": True, "fmt": fmt,
    }


def apply_u32_args(fmt, args):
    """printf simple solo con args u32 ya desempaquetados (para 0x79)."""
    out = []
    pos = 0
    ai = 0
    for m in _SPEC_RE.finditer(fmt):
        out.append(fmt[pos:m.start()])
        pos = m.end()
        flags, width, prec, length, conv = m.groups()
        if conv == "%":
            out.append("%")
            continue
        if ai >= len(args):
            out.append(m.group(0))
            continue
        v = args[ai]
        ai += 1
        try:
            if conv in ("d", "i"):
                sv = v - (1 << 32) if v & 0x80000000 else v
                out.append(("%" + flags + (width or "") + (prec or "") + "d") % sv)
            elif conv in ("u", "o", "x", "X"):
                out.append(("%" + flags + (width or "") + (prec or "") + conv) % v)
            elif conv == "p":
                out.append("0x%x" % v)
            elif conv == "c":
                vb = v & 0xFF
                out.append(chr(vb) if 32 <= vb < 127 else "\\x%02x" % vb)
            else:
                out.append(m.group(0))
        except Exception:
            out.append(m.group(0))
    out.append(fmt[pos:])
    return "".join(out)


def decode_10(fr):
    if len(fr) < 16:
        return None
    logcode = struct.unpack_from("<H", fr, 6)[0]
    ts = struct.unpack_from("<Q", fr, 8)[0]
    payload = fr[16:]
    return {
        "ts": ts, "cmd": 0x10, "msgid": None, "tag": None,
        "ssid": "LOG", "loc": "logcode=0x%04X" % logcode,
        "text": "log_packet code=0x%04X len=%d payload=%s" % (
            logcode, len(payload), payload[:48].hex() + ("..." if len(payload) > 48 else "")),
        "resolved": True, "argok": True,
    }


# ---------------------------------------------------------------------------
# Procesamiento de captura completa
# ---------------------------------------------------------------------------
def _sane_ts_bounds(frames):
    """
    Estima el rango valido de timestamps raw usando la mediana robusta de los
    frames 0x99 con build tag valido (los mas fiables). Devuelve (lo, hi) con
    margen amplio para descartar outliers de frames mal-reencuadrados (byte0
    0x99/0x79 por coincidencia).
    """
    tss = []
    for _u, raw in frames:
        fr = unwrap_multi_radio(raw)
        if fr and fr[0] == CMD_QSR4 and len(fr) >= 18:
            if struct.unpack_from("<H", fr, 16)[0] == QSR4_BUILD_TAG:
                tss.append(struct.unpack_from("<Q", fr, 4)[0])
    if not tss:
        return 0, (1 << 64)
    tss.sort()
    med = tss[len(tss) // 2]
    # ventana de +-1e12 ticks alrededor de la mediana (mas que suficiente:
    # la captura entera abarca ~4.3e9 ticks)
    span = 1_000_000_000_000
    return med - span, med + span


def decode_capture(path, table, include_log=False):
    data = open(path, "rb").read()
    frames = deframe_with_uptime(data)
    ts_lo, ts_hi = _sane_ts_bounds(frames)
    events = []
    dropped = 0
    for uptime, raw in frames:
        fr = unwrap_multi_radio(raw)
        if not fr:
            continue
        cmd = fr[0]
        ev = None
        if cmd == CMD_QSR4:
            # solo frames con build tag valido (filtra falsos positivos)
            if len(fr) < 18 or struct.unpack_from("<H", fr, 16)[0] != QSR4_BUILD_TAG:
                dropped += 1
                continue
            ev = decode_99(fr, table)
        elif cmd == CMD_EXT:
            ev = decode_79(fr)
        elif cmd == CMD_LOG and include_log:
            ev = decode_10(fr)
        if ev is None:
            continue
        # descarta frames con timestamp fuera del rango sano (mal encuadrados)
        if not (ts_lo <= ev["ts"] <= ts_hi):
            dropped += 1
            continue
        ev["uptime"] = uptime
        events.append(ev)
    # ordena por timestamp del frame; fallback a uptime
    events.sort(key=lambda e: (e["ts"], e["uptime"]))
    events_dropped[path] = dropped
    return events


events_dropped = {}


def ts_ms(ts, ts_base, factor):
    """Convierte raw ts a ms relativos usando el factor estimado."""
    return (ts - ts_base) * factor


def estimate_factor(events):
    """
    Estima raw_ts->ms correlacionando el span de ts con el span de uptime_ms de
    los records. Devuelve (ts_base, ms_per_tick).
    """
    if not events:
        return 0, 0.0
    ts_lo = min(e["ts"] for e in events)
    ts_hi = max(e["ts"] for e in events)
    up_lo = min(e["uptime"] for e in events)
    up_hi = max(e["uptime"] for e in events)
    dts = ts_hi - ts_lo
    dup = up_hi - up_lo
    factor = (dup / dts) if dts else 0.0
    return ts_lo, factor


# ---------------------------------------------------------------------------
# Escritura del log decodificado
# ---------------------------------------------------------------------------
def write_decoded(events, out_path):
    ts_base, factor = estimate_factor(events)
    with open(out_path, "w") as f:
        f.write("# Log decodificado QSR4/DIAG-F3 (offline)\n")
        f.write("# columnas: [ms_rel] [uptime_ms] [cmd ssid file:line] mensaje\n")
        f.write("# eventos: %d  ts_base=%d  ms_per_tick=%.9g\n\n" %
                (len(events), ts_base, factor))
        for e in events:
            ms = ts_ms(e["ts"], ts_base, factor)
            cmdname = {0x99: "99", 0x79: "79", 0x10: "10"}.get(e["cmd"], "%02X" % e["cmd"])
            f.write("[%10.3f ms][up=%d][%s ss=%s %s] %s\n" % (
                ms, e["uptime"], cmdname, e["ssid"], e["loc"], e["text"]))
    return ts_base, factor


def report_window(events, window_ms, label):
    """Reporta la ventana de ~window_ms antes del ultimo frame."""
    if not events:
        print("  (sin eventos)")
        return
    ts_base, factor = estimate_factor(events)
    last_ts = max(e["ts"] for e in events)
    if factor > 0:
        cutoff_ts = last_ts - (window_ms / factor)
    else:
        cutoff_ts = last_ts  # sin escala: toma el ultimo bloque por ts
    win = [e for e in events if e["ts"] >= cutoff_ts]
    win.sort(key=lambda e: e["ts"])
    print("\n" + "=" * 90)
    print("VENTANA DEL RESET (%s): ultimos ~%d ms antes del corte" % (label, window_ms))
    print("  ultimo ts raw = %d" % last_ts)
    print("  uptime max    = %d ms" % max(e["uptime"] for e in events))
    print("  factor raw->ms= %.9g   eventos en ventana = %d" % (factor, len(win)))
    print("-" * 90)
    for e in win:
        ms = ts_ms(e["ts"], ts_base, factor)
        cmdname = {0x99: "99", 0x79: "79", 0x10: "10"}.get(e["cmd"], "%02X" % e["cmd"])
        print("[%9.3f ms][%s ss=%s %s] %s" % (ms, cmdname, e["ssid"], e["loc"], e["text"]))


def main():
    ap = argparse.ArgumentParser(description="Decodificador Qshrink-4 / DIAG F3.")
    ap.add_argument("files", nargs="+", help="capturas .bin DGPK")
    ap.add_argument("--qdb", required=True, help="qdsp6m.qdb")
    ap.add_argument("--cache", default="/tmp/opencode/qdb_full.json",
                    help="cache JSON de la tabla qdb")
    ap.add_argument("--out", default=None, help="salida del log decodificado (1 archivo)")
    ap.add_argument("--window-ms", type=int, default=200,
                    help="ventana (ms) antes del corte a reportar")
    ap.add_argument("--include-log", action="store_true",
                    help="incluye frames 0x10 log_packet (solo cabecera)")
    args = ap.parse_args()

    table = load_qdb(args.qdb, args.cache)
    print("[+] qdb cargado: %d format strings" % len(table))

    for path in args.files:
        if not os.path.exists(path):
            print("[!] no existe:", path, file=sys.stderr)
            continue
        events = decode_capture(path, table, include_log=args.include_log)
        hist = Counter(e["cmd"] for e in events)
        print("\n[+] %s: %d eventos decodificados  %s" %
              (path, len(events), {hex(k): v for k, v in hist.items()}))
        out = args.out
        if out is None:
            base = os.path.basename(path)
            base = re.sub(r"\.bin$", "", base)
            out = os.path.join(os.path.dirname(path), "decoded-%s.txt" % base)
        ts_base, factor = write_decoded(events, out)
        print("    log escrito: %s" % out)
        report_window(events, args.window_ms, os.path.basename(path))


if __name__ == "__main__":
    main()
