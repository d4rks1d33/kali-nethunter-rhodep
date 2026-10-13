#!/usr/bin/env python3
"""
rhodep-qsr4-extract.py

Parser offline para capturas DIAG en contenedor DGPK.

Flujo:
  1. Itera records DGPK: magic "DGPK" + u32 length(LE) + u32 uptime_ms(LE) + payload[length].
  2. Concatena los payloads en un stream continuo y hace deframing HDLC correcto:
       - acumula bytes hasta 0x7e (flag de fin) *sin* des-escapar,
       - luego des-escapa (0x7d -> siguiente XOR 0x20),
       - descarta los 2 bytes finales (CRC16 CCITT) del contenido des-escapado.
     El deframing cruza limites de record (un frame puede partirse entre payloads).
  3. Desenvuelve wrapper 0x98 (DIAG_MULTI_RADIO_CMD_F): si el primer byte del frame
     des-escapado es 0x98, salta 8 bytes de header multi-radio.
  4. Clasifica cada frame por command code (primer byte) -> histograma.
  5. Para frames 0x99 (Qshrink-4): extrae message id, timestamp, build tag y args.
  6. Para frames 0x79 (texto plano): extrae format-strings legibles.

Uso:
  rhodep-qsr4-extract.py FILE [FILE ...] [--json OUT.json] [--expect-f3 N --expect-99 N]
"""

import argparse
import json
import os
import re
import struct
import sys
from collections import Counter, defaultdict

HDLC_FLAG = 0x7E
HDLC_ESC = 0x7D
HDLC_XOR = 0x20

DGPK_MAGIC = b"DGPK"

MSG_TYPE_NAMES = {
    0x79: "DIAG_EXT_MSG_F",
    0x92: "DIAG_QSR_EXT_MSG_TERSE_F",
    0x99: "DIAG_QSR4_EXT_MSG_TERSE_F",
    0x9C: "QSR_variant_9C",
    0x9D: "QSR_variant_9D",
    0x10: "log_packet",
    0x60: "event_60",
    0x0C: "event_0C",
}

# F3 message command codes (los que contamos como "frames F3")
F3_CODES = {0x79, 0x92, 0x99, 0x9C, 0x9D}

QSR4_BUILD_TAG = 0x3A1C

KEYWORDS = [
    "nav", "gnss", "sdd", "adc", "qlink", "stmr", "rtc",
    "clock", "cc reset", "wdog",
]

# Para matching de keywords con limite de palabra (evita falsos positivos por
# substrings dentro de otras palabras, p.ej. "rtc" dentro de "startccs").
_KW_RE = re.compile(
    r"(?<![A-Za-z])(" + "|".join(re.escape(k) for k in KEYWORDS) + r")(?![A-Za-z])",
    re.IGNORECASE,
)

# Normaliza un format-string a un "template" colapsando numeros, para deduplicar
# variantes que solo difieren en valores (p.ej. profiling con distintos contadores).
_NUM_RE = re.compile(r"\d+")


def template_of(s):
    return _NUM_RE.sub("#", s)


# ---------------------------------------------------------------------------
# DGPK container
# ---------------------------------------------------------------------------
def iter_dgpk_payloads(data):
    """Genera (uptime_ms, payload) por cada record DGPK valido."""
    off = 0
    n = len(data)
    while off + 12 <= n:
        if data[off:off + 4] != DGPK_MAGIC:
            # Intenta resync buscando el proximo magic.
            nxt = data.find(DGPK_MAGIC, off + 1)
            if nxt < 0:
                break
            off = nxt
            continue
        length, uptime = struct.unpack_from("<II", data, off + 4)
        start = off + 12
        end = start + length
        if end > n:
            # record truncado al final del archivo: toma lo que haya
            yield uptime, data[start:n]
            break
        yield uptime, data[start:end]
        off = end


# ---------------------------------------------------------------------------
# HDLC deframing
# ---------------------------------------------------------------------------
def hdlc_deframe(stream):
    """
    Deframe HDLC sobre un stream de bytes continuo.

    Regla critica (donde fallan parsers ingenuos): NO partir por 0x7e sin
    tener en cuenta el escape. Un 0x7e real nunca aparece escapado; el 0x7e
    de cierre siempre esta sin escapar. Por eso acumulamos bytes crudos
    hasta un 0x7e *que no sea el segundo byte de una secuencia de escape*,
    y SOLO despues des-escapamos.

    Como 0x7e nunca se produce via escape (seria 7d 5e), basta con:
      - recorrer bytes,
      - cuando veamos 0x7e cerramos el frame crudo acumulado,
      - des-escapamos el contenido crudo,
      - descartamos CRC16 (2 bytes) del contenido des-escapado.

    Devuelve lista de payloads des-escapados (sin CRC).
    """
    frames = []
    raw = bytearray()
    for b in stream:
        if b == HDLC_FLAG:
            if raw:
                frame = _unescape(raw)
                if len(frame) > 2:
                    # quita CRC16 (ultimos 2 bytes)
                    frames.append(bytes(frame[:-2]))
                raw = bytearray()
            # 0x7e repetidos / frame vacio -> ignorar
        else:
            raw.append(b)
    # cualquier residuo sin 0x7e de cierre se descarta (frame incompleto)
    return frames


def _unescape(raw):
    """Des-escapa HDLC: 0x7d marca que el siguiente byte es (b XOR 0x20)."""
    out = bytearray()
    it = iter(range(len(raw)))
    i = 0
    L = len(raw)
    while i < L:
        b = raw[i]
        if b == HDLC_ESC:
            if i + 1 < L:
                out.append(raw[i + 1] ^ HDLC_XOR)
                i += 2
                continue
            else:
                # escape colgante al final: lo dejamos tal cual (defensivo)
                out.append(b)
                i += 1
        else:
            out.append(b)
            i += 1
    return out


def unwrap_multi_radio(frame):
    """Si el frame empieza con 0x98 (DIAG_MULTI_RADIO_CMD_F), salta 8 bytes."""
    if frame and frame[0] == 0x98:
        if len(frame) >= 8:
            return frame[8:]
        return b""
    return frame


# ---------------------------------------------------------------------------
# Format-string extraction para 0x79
# ---------------------------------------------------------------------------
_PRINTABLE_RE = re.compile(rb"[\x20-\x7e]{4,}")


def extract_strings_from_079(frame):
    """Extrae substrings ASCII imprimibles (>=4 chars) de un frame 0x79."""
    return [m.group().decode("ascii", "replace") for m in _PRINTABLE_RE.finditer(frame)]


# ---------------------------------------------------------------------------
# Procesamiento por archivo
# ---------------------------------------------------------------------------
def process_file(path):
    data = open(path, "rb").read()

    # Concatena todos los payloads (el frame HDLC puede cruzar records).
    stream = bytearray()
    n_records = 0
    for _uptime, payload in iter_dgpk_payloads(data):
        stream.extend(payload)
        n_records += 1

    frames = hdlc_deframe(stream)

    type_hist = Counter()
    f3_total = 0

    qsr4_ids = Counter()
    qsr4_sample = {}          # id -> (timestamp, arg_bytes_hex)
    qsr4_badtag = 0
    qsr4_count = 0
    ts_min = None
    ts_max = None

    f079_strings = Counter()

    for fr in frames:
        fr = unwrap_multi_radio(fr)
        if not fr:
            continue
        cmd = fr[0]
        type_hist[cmd] += 1
        if cmd in F3_CODES:
            f3_total += 1

        if cmd == 0x99:
            qsr4_count += 1
            # byte0=0x99, byte1=0x02, bytes2-3=?, bytes4-11=ts(u64),
            # bytes12-15=msgid(u32), bytes16-17=build tag(u16), 18+=args
            if len(fr) >= 18:
                ts = struct.unpack_from("<Q", fr, 4)[0]
                msgid = struct.unpack_from("<I", fr, 12)[0]
                tag = struct.unpack_from("<H", fr, 16)[0]
                args = fr[18:]
                good = (tag == QSR4_BUILD_TAG)
                if not good:
                    qsr4_badtag += 1
                # Solo los frames con build tag valido entran en la lista de
                # ids a resolver; los de tag malo suelen ser falsos positivos
                # (byte 0 = 0x99 por coincidencia) y contaminarian el rango.
                if good:
                    qsr4_ids[msgid] += 1
                    if msgid not in qsr4_sample:
                        qsr4_sample[msgid] = (ts, args.hex())
                    if ts_min is None or ts < ts_min:
                        ts_min = ts
                    if ts_max is None or ts > ts_max:
                        ts_max = ts

        elif cmd == 0x79:
            for s in extract_strings_from_079(fr):
                f079_strings[s] += 1

    return {
        "path": path,
        "size": len(data),
        "n_records": n_records,
        "n_frames": len(frames),
        "type_hist": type_hist,
        "f3_total": f3_total,
        "qsr4_count": qsr4_count,
        "qsr4_ids": qsr4_ids,
        "qsr4_sample": qsr4_sample,
        "qsr4_badtag": qsr4_badtag,
        "ts_min": ts_min,
        "ts_max": ts_max,
        "f079_strings": f079_strings,
    }


# ---------------------------------------------------------------------------
# Reporte
# ---------------------------------------------------------------------------
def fmt_type(cmd):
    name = MSG_TYPE_NAMES.get(cmd, "other")
    return f"0x{cmd:02X} {name}"


def report(res, expect_f3=None, expect_99=None):
    print("=" * 72)
    print(f"ARCHIVO: {res['path']}")
    print(f"  tamano          : {res['size']} bytes")
    print(f"  records DGPK    : {res['n_records']}")
    print(f"  frames HDLC     : {res['n_frames']}")
    print(f"  frames F3 total : {res['f3_total']}  (0x79/0x92/0x99/0x9C/0x9D)")
    print(f"  frames 0x99     : {res['qsr4_count']}")

    print("\n  --- Histograma de tipos de mensaje (command code) ---")
    th = res["type_hist"]
    known = sorted(MSG_TYPE_NAMES.keys())
    shown = set()
    for cmd in known:
        if th.get(cmd):
            print(f"    {fmt_type(cmd):<40} : {th[cmd]}")
            shown.add(cmd)
    others = sorted((c for c in th if c not in shown), key=lambda c: -th[c])
    other_total = sum(th[c] for c in others)
    if others:
        print(f"    {'(otros command codes)':<40} : {other_total}")
        for cmd in others[:20]:
            print(f"        0x{cmd:02X} : {th[cmd]}")

    # Validacion
    if expect_f3 is not None:
        ok = "OK" if res["f3_total"] == expect_f3 else \
             f"~ (esperado {expect_f3}, delta {res['f3_total']-expect_f3:+d})"
        print(f"\n  Validacion frames F3 : {res['f3_total']} vs esperado {expect_f3} -> {ok}")
    if expect_99 is not None:
        ok = "OK" if res["qsr4_count"] == expect_99 else \
             f"~ (esperado {expect_99}, delta {res['qsr4_count']-expect_99:+d})"
        print(f"  Validacion frames 99 : {res['qsr4_count']} vs esperado {expect_99} -> {ok}")

    # 0x99 ids
    ids = res["qsr4_ids"]
    if ids:
        print(f"\n  --- Message IDs 0x99 (solo frames con build tag valido) ---")
        print(f"    build tag malo (excluidos): {res['qsr4_badtag']} frames (tag != 0x{QSR4_BUILD_TAG:04X})")
        print(f"    frames 0x99 validos        : {res['qsr4_count'] - res['qsr4_badtag']}")
        print(f"    ids distintos : {len(ids)}")
        print(f"    rango id      : min=0x{min(ids):08X}  max=0x{max(ids):08X}")
        if res["ts_min"] is not None:
            print(f"    rango ts      : min={res['ts_min']}  max={res['ts_max']}")
        print(f"\n    Top 30 message ids:")
        print(f"      {'id':<12} {'count':>8}")
        for mid, cnt in ids.most_common(30):
            print(f"      0x{mid:08X} {cnt:>8}")

    # 0x79 strings
    strs = res["f079_strings"]
    if strs:
        print(f"\n  --- Format-strings 0x79 (texto plano) ---")
        print(f"    strings distintos: {len(strs)}")
        # muestra general ~40
        print(f"\n    Muestra (hasta 40 strings distintos mas frecuentes):")
        for s, cnt in strs.most_common(40):
            print(f"      [{cnt:>5}] {s}")
        # keyword hits (con limite de palabra, deduplicados por template)
        kw_templates = {}   # template -> (ejemplo, count total)
        for s, cnt in strs.items():
            if _KW_RE.search(s):
                t = template_of(s)
                if t not in kw_templates:
                    kw_templates[t] = [s, 0]
                kw_templates[t][1] += cnt
        ordered = sorted(kw_templates.values(), key=lambda x: -x[1])
        print(f"\n    Strings que mencionan {KEYWORDS}")
        print(f"      (deduplicados por template; {len(kw_templates)} plantillas distintas)")
        for example, cnt in ordered[:80]:
            print(f"      [{cnt:>5}] {example}")


def write_json(results, out_path):
    """Escribe JSON con ids 0x99 distintos agregados de todos los archivos."""
    agg_count = Counter()
    agg_sample = {}
    for res in results:
        for mid, cnt in res["qsr4_ids"].items():
            agg_count[mid] += cnt
            if mid not in agg_sample and mid in res["qsr4_sample"]:
                agg_sample[mid] = res["qsr4_sample"][mid]

    records = []
    for mid in sorted(agg_count):
        ts, argh = agg_sample.get(mid, (None, ""))
        records.append({
            "message_id": mid,
            "message_id_hex": f"0x{mid:08X}",
            "count": agg_count[mid],
            "sample_timestamp": ts,
            "sample_arg_bytes_hex": argh,
        })

    doc = {
        "source_files": [r["path"] for r in results],
        "total_distinct_ids": len(records),
        "id_min_hex": f"0x{min(agg_count):08X}" if agg_count else None,
        "id_max_hex": f"0x{max(agg_count):08X}" if agg_count else None,
        "ids": records,
    }
    with open(out_path, "w") as f:
        json.dump(doc, f, indent=2)
    print(f"\n[+] JSON escrito: {out_path}  ({len(records)} ids distintos)")


def main():
    ap = argparse.ArgumentParser(description="Extractor Qshrink-4 / DIAG F3 desde DGPK.")
    ap.add_argument("files", nargs="+", help="archivos .bin DGPK")
    ap.add_argument("--json", default=None, help="salida JSON con ids 0x99")
    ap.add_argument("--expect-f3", type=int, default=None)
    ap.add_argument("--expect-99", type=int, default=None)
    args = ap.parse_args()

    results = []
    for path in args.files:
        if not os.path.exists(path):
            print(f"[!] no existe: {path}", file=sys.stderr)
            continue
        res = process_file(path)
        results.append(res)
        report(res, expect_f3=args.expect_f3, expect_99=args.expect_99)

    if args.json and results:
        write_json(results, args.json)


if __name__ == "__main__":
    main()
