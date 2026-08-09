# Interconnect SM6375 driver — research / work-in-progress

Mobile data on this device is blocked because mainline lacks an SM6375
**interconnect** driver. The IPA (data path) must vote its NoC bandwidth; without
the provider, enabling IPA hangs the SoC.

This directory holds the completed research to write that driver. It is NOT the
driver yet — it is everything needed to write it.

## Status: RESEARCH COMPLETE, driver not yet written
- Feasibility: confirmed. Template = mainline `qcm2290.c` (same SMD-RPM arch).
  Source of truth for tables = vendor `holi.c` (holi == SM6375), 6 buses, 94 nodes.
- `qcom,sm6375.h` — the dt-bindings header (DONE, ready to use).
- `PROGRESS.md` — full plan, API mapping (vendor icc-rpm vs mainline), and the
  CRITICAL QoS offset->qos_port conversion (a wrong port hangs the SoC; cross-check
  shared nodes against qcm2290.c which is mainline-verified).
- `DT-NODES.md` — the 6 NoC provider DT nodes (reg + clocks) for sm6375.dtsi, and
  the exact `interconnects=` to put on the IPA node.
- `holi_nodes_dump.txt` / `holi_qos_dump.txt` — parsed node/QoS tables (derived
  data from the vendor driver, for translation reference).

## What's left (the actual coding + debugging)
1. Write `drivers/interconnect/qcom/sm6375.c`: translate the 94 nodes + 6 buses
   from holi.c into mainline `qcom_icc_node`/`qcom_icc_desc` format. Use qcm2290.c
   as the structural template. For QoS: prefer qcm2290's qos_port for shared
   nodes; convert vendor offsets only for sm6375-only nodes.
2. Kconfig + Makefile + `CONFIG_INTERCONNECT_QCOM_SM6375`.
3. DTS: add the 6 NoC provider nodes to sm6375.dtsi (see DT-NODES.md).
4. DTS: add `interconnects=` to the IPA node + `status = "okay"`; restore the real
   `interconnect_count` in `ipa_data_v4_11_sm6375` (revert the count=0 hack in
   kernel patch 0025) now that a provider exists.
5. Build, flash, test mobile data. Iterate carefully — a wrong QoS port or RPM id
   hangs the SoC, and each test is a build + flash + boot cycle.

## Note on the vendor holi.c
The vendor driver (proprietary Qualcomm/Motorola source) is the reference for the
tables but is NOT redistributed here. Obtain it from the vendor kernel tree
(Motorola-SM6375-Devs) if you need the raw QoS offsets and RPM ids.
