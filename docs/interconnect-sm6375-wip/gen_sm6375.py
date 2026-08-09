#!/usr/bin/env python3
# Generate drivers/interconnect/qcom/sm6375.c by translating the vendor holi.c
# (SM6375 == holi) into mainline qcom_icc_node/qcom_icc_desc format.
# Mirrors qcm2290.c structure. QoS offsets converted to qos_port.
import re, sys

HOLI = "/tmp/opencode/dl/moto/drivers/interconnect/qcom/holi.c"
src = open(HOLI).read()

# --- ICBID -> number map ---
icbid = {}
for line in open("/tmp/icbid_map.txt"):
    k, v = line.strip().split("=")
    icbid[k] = int(v)

def rpm(val):
    val = val.strip()
    if val in ("-1", ""): return "-1"
    if val.isdigit() or (val.startswith("-") and val[1:].isdigit()): return val
    if val in icbid: return str(icbid[val])
    return val  # leave as-is (will be a compile error we can spot)

# --- parse qosbox definitions: name -> dict ---
qos = {}
for m in re.finditer(r'static struct qcom_icc_qosbox (\w+) = \{(.*?)\};', src, re.S):
    name, body = m.group(1), m.group(2)
    def g(f, default=None):
        mm = re.search(rf'\.{f}\s*=\s*([^,\n]+)', body)
        return mm.group(1).strip() if mm else default
    offs = re.search(r'\.offsets\s*=\s*\{([^}]*)\}', body)
    off = offs.group(1).strip().rstrip(",").strip() if offs else "0"
    regs = g("regs", "")
    is_bimc = "bimc" in regs.lower()
    qos[name] = {
        "prio": g("prio", "0"),
        "urg": g("urg_fwd", None),
        "offset": off,
        "bimc": is_bimc,
    }

# --- mainline-verified qos_port/prio/mode from qcm2290 (by node id) ---
qcm = {}
for line in open("/tmp/qcm2290_ports.txt"):
    parts = dict(p.split("=") for p in line.strip().split("|")[1:])
    nid = line.split("|")[0]
    qcm[nid] = parts
# qcm2290 uses MASTER_APPSS_PROC / MASTER_SNOC_BIMC ; holi uses MASTER_AMPSS_M0 / SNOC_BIMC_MAS
QCM_ALIAS = {
    "MASTER_AMPSS_M0": "MASTER_APPSS_PROC",
    "SNOC_BIMC_MAS": "MASTER_SNOC_BIMC",
    "MASTER_GRAPHICS_3D": "MASTER_GFX3D",
    "MASTER_TIC": "MASTER_TIC",
    "MASTER_QUP_0": "MASTER_QUP_0",
    "MASTER_PIMEM": "MASTER_PIMEM",
}

# --- convert absolute QoS offset to qos_port (fallback for sm6375-only nodes) ---
def off_to_port(offhex, is_bimc):
    try:
        off = int(offhex, 16)
    except ValueError:
        return 0
    if is_bimc:
        return (off - 0x8300) // 0x4000
    return off // 0x1000

# --- parse node definitions ---
nodes = {}
order = []
for m in re.finditer(r'static struct qcom_icc_node (\w+) = \{(.*?)\n\};', src, re.S):
    name, body = m.group(1), m.group(2)
    def g(f, default=""):
        mm = re.search(rf'\.{f}\s*=\s*([^,\n]+)', body)
        return mm.group(1).strip() if mm else default
    idv = g("id"); ch = g("channels", "1"); bw = g("buswidth", "8")
    mas = g("mas_rpm_id", "-1"); slv = g("slv_rpm_id", "-1"); nlv = g("num_links", "0")
    qref = re.search(r'\.qosbox\s*=\s*&(\w+)', body)
    qname = qref.group(1) if qref else None
    lm = re.search(r'\.links\s*=\s*\{(.*?)\}', body, re.S)
    links = []
    if lm:
        for t in lm.group(1).split(","):
            t = t.strip()
            if t: links.append(t)
    nodes[name] = {"id": idv, "ch": ch, "bw": bw, "mas": mas, "slv": slv,
                   "links": links, "qos": qname}
    order.append(name)

# --- parse the 6 bus descriptors: bus -> [(id, nodevar), ...] ---
buses = {}
for m in re.finditer(r'static struct qcom_icc_node \*(\w+)_nodes\[\] = \{(.*?)\};', src, re.S):
    bus, body = m.group(1), m.group(2)
    entries = re.findall(r'\[(\w+)\]\s*=\s*&(\w+)', body)
    buses[bus] = entries

# ---------------- emit ----------------
out = []
w = out.append
w("// SPDX-License-Identifier: GPL-2.0")
w("/*")
w(" * Qualcomm SM6375 interconnect driver")
w(" *")
w(" * Tables ported from the downstream 'holi' (SM6375) interconnect driver.")
w(" * Mirrors the mainline qcm2290 driver (same SMD-RPM architecture).")
w(" */")
w("")
w("#include <linux/device.h>")
w("#include <linux/interconnect-provider.h>")
w("#include <linux/mod_devicetable.h>")
w("#include <linux/module.h>")
w("#include <linux/platform_device.h>")
w("#include <linux/regmap.h>")
w("")
w("#include <dt-bindings/interconnect/qcom,sm6375.h>")
w("")
w('#include "icc-rpm.h"')
w("")

# node emit order: follow file order
def emit_links(name, links):
    if not links:
        return None
    var = f"{name}_links"
    w(f"static const u16 {var}[] = {{")
    w("\t" + ", ".join(links) + ",")
    w("};")
    w("")
    return var

for name in order:
    n = nodes[name]
    lvar = emit_links(name, n["links"])
    w(f"static struct qcom_icc_node {name} = {{")
    w(f'\t.name = "{name}",')
    w(f"\t.id = {n['id']},")
    w(f"\t.channels = {n['ch']},")
    w(f"\t.buswidth = {n['bw']},")
    if n["qos"] and n["qos"] in qos:
        q = qos[n["qos"]]
        nid = n["id"]
        qid = QCM_ALIAS.get(nid, nid)
        w("\t.qos.ap_owned = true,")
        if qid in qcm:
            # mainline-verified values from qcm2290
            v = qcm[qid]
            w(f"\t.qos.qos_port = {v['port']},")
            w(f"\t.qos.qos_mode = {v['mode']},")
            w(f"\t.qos.areq_prio = {v['prio']},")
        else:
            # sm6375-only node: convert from the vendor offset
            port = off_to_port(q["offset"], q["bimc"])
            w(f"\t.qos.qos_port = {port},")
            w("\t.qos.qos_mode = NOC_QOS_MODE_FIXED,")
            w(f"\t.qos.areq_prio = {q['prio']},")
        if q["urg"] not in (None, "0", ""):
            w("\t.qos.urg_fwd_en = true,")
    w(f"\t.mas_rpm_id = {rpm(n['mas'])},")
    w(f"\t.slv_rpm_id = {rpm(n['slv'])},")
    if lvar:
        w(f"\t.num_links = ARRAY_SIZE({lvar}),")
        w(f"\t.links = {lvar},")
    else:
        w("\t.num_links = 0,")
    w("};")
    w("")

# Mainline RPM bus clock descriptors (extern in icc-rpm.h / icc-rpm-clocks.c).
# Mapping verified against qcm2290.c (identical SMD-RPM NoC layout):
#   mmnrt_virt -> mmaxi_0_clk, mmrt_virt -> mmaxi_1_clk
CLKDESC = {
    "bimc": "bimc_clk", "sys_noc": "bus_2_clk", "config_noc": "bus_1_clk",
    "clk_virt": "qup_clk", "mmrt_virt": "mmaxi_1_clk", "mmnrt_virt": "mmaxi_0_clk",
}
# emit bus node arrays + descs. Values mirror mainline sm6115.c (SM6115 == same
# NoC family as SM6375): types, qos_offset, ab_coeff, regmap, intf_clocks.
# BUS_META: bus -> (type, qos_offset, ab_coeff, regmap_name, intf_clocks_name)
BUS_META = {
    "bimc":        ("QCOM_ICC_BIMC", "0x8000",  "153", "bimc_regmap_config",     None),
    "sys_noc":     ("QCOM_ICC_QNOC", "0x15000", None,  "sys_noc_regmap_config",  "snoc_intf_clocks"),
    "config_noc":  ("QCOM_ICC_QNOC", "0x15000", None,  "cnoc_regmap_config",     "cnoc_intf_clocks"),
    "clk_virt":    ("QCOM_ICC_QNOC", None,      None,  "sys_noc_regmap_config",  None),
    "mmrt_virt":   ("QCOM_ICC_QNOC", "0x15000", "139", "sys_noc_regmap_config",  None),
    "mmnrt_virt":  ("QCOM_ICC_QNOC", "0x15000", "142", "sys_noc_regmap_config",  None),
}

# intf_clocks (GCC clocks consumed from DT clock-names); "ipa" required by qxm_ipa
w("static const char * const snoc_intf_clocks[] = {")
w('\t"cpu_axi",')
w('\t"ufs_axi",')
w('\t"usb_axi",')
w('\t"ipa", /* Required by qxm_ipa */')
w("};")
w("")
w("static const char * const cnoc_intf_clocks[] = {")
w('\t"usb_axi",')
w("};")
w("")

for bus, entries in buses.items():
    w(f"static struct qcom_icc_node * const {bus}_nodes[] = {{")
    for idv, var in entries:
        w(f"\t[{idv}] = &{var},")
    w("};")
    w("")

# regmap configs (per bus, like sm6115.c)
for name, maxreg in (("bimc_regmap_config", "0x80000"),
                     ("cnoc_regmap_config", "0x6200"),
                     ("sys_noc_regmap_config", "0x5f080")):
    w(f"static const struct regmap_config {name} = {{")
    w("\t.reg_bits = 32,")
    w("\t.reg_stride = 4,")
    w("\t.val_bits = 32,")
    w(f"\t.max_register = {maxreg},")
    w("\t.fast_io = true,")
    w("};")
    w("")

for bus, entries in buses.items():
    typ, qoff, ab, regmap_name, intf = BUS_META[bus]
    w(f"static const struct qcom_icc_desc sm6375_{bus} = {{")
    w(f"\t.type = {typ},")
    w(f"\t.nodes = {bus}_nodes,")
    w(f"\t.num_nodes = ARRAY_SIZE({bus}_nodes),")
    w(f"\t.regmap_cfg = &{regmap_name},")
    if intf:
        w(f"\t.intf_clocks = {intf},")
        w(f"\t.num_intf_clocks = ARRAY_SIZE({intf}),")
    w(f"\t.bus_clk_desc = &{CLKDESC[bus]},")
    if qoff:
        w(f"\t.qos_offset = {qoff},")
    if ab:
        w(f"\t.ab_coeff = {ab},")
    w("\t.keep_alive = true,")
    w("};")
    w("")

# of_match + driver
w("static const struct of_device_id sm6375_noc_of_match[] = {")
COMPAT = {
 "bimc": "qcom,sm6375-bimc", "sys_noc": "qcom,sm6375-sys-noc",
 "config_noc": "qcom,sm6375-config-noc", "clk_virt": "qcom,sm6375-clk-virt",
 "mmrt_virt": "qcom,sm6375-mmrt-virt", "mmnrt_virt": "qcom,sm6375-mmnrt-virt",
}
for bus in buses:
    w(f'\t{{ .compatible = "{COMPAT[bus]}", .data = &sm6375_{bus} }},')
w("\t{ }")
w("};")
w("MODULE_DEVICE_TABLE(of, sm6375_noc_of_match);")
w("")
w("static struct platform_driver sm6375_noc_driver = {")
w("\t.probe = qnoc_probe,")
w("\t.remove = qnoc_remove,")
w("\t.driver = {")
w('\t\t.name = "qnoc-sm6375",')
w("\t\t.of_match_table = sm6375_noc_of_match,")
w("\t\t.sync_state = icc_sync_state,")
w("\t},")
w("};")
w("module_platform_driver(sm6375_noc_driver);")
w("")
w('MODULE_DESCRIPTION("Qualcomm SM6375 NoC driver");')
w('MODULE_LICENSE("GPL");')

open("/tmp/sm6375.c", "w").write("\n".join(out) + "\n")
print("generated /tmp/sm6375.c :", len(out), "lines")
print("buses:", list(buses.keys()))
print("nodes:", len(nodes))
