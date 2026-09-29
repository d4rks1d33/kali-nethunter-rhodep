# 05 — Modem / RF / GNSS on Motorola `rhodep` (Moto G82 5G, XT2225-1, SM6375 "blair"/holi)

Hardware reverse-engineering report for the mainline Linux port.
Target bug: **"enable 4G → modem crashes/dies".**

**Conventions used in this document**

* `FACT:` — literally quoted from a file in the corpus, with `file:line`.
* `INFERENCE:` — my reasoning on top of the facts. May be wrong.
* `NOT FOUND IN SOURCES` — I looked and there is nothing; I did **not** invent it.

**Corpus roots (abbreviated in citations):**

| Short name | Path |
|---|---|
| `DS-DT/` | `/opt/postmarket/research/motorola/devicetree-A12-S1SUS32.73-13-4-3/qcom/` |
| `DS-K/` | `/opt/postmarket/research/motorola/kernel-msm-A12-S1SUS32.73-13-4-3/` |
| `ML/` | `/tmp/v62test/linux-7.2-rc5/` |

---

## 0. Executive summary

The downstream modem is a **TZ/PAS-loaded** (`qcom,pil-tz-generic`, `pas-id = 4`) Hexagon
MSS. There is **no MBA, no MSS clock tree, no MSS resets and no separate `mpss`
carveout** in the downstream device tree — TZ owns all of it. Mainline's
`qcom,sm6375-mpss-pas` uses the *same* mechanism, so the **basic boot path,
carveout, SMP2P bit map, crash-reason SMEM id and PAS id all match exactly.**

That is consistent with the reported symptom: the modem *boots* (WLAN, which lives
on the same SMP2P edge, works), and only dies when the RF/data stack is actually
brought up. The failure is therefore almost certainly **not** in the remoteproc
boot description but in one of the **AP-side services the modem talks to over
QMI/QRTR once it goes online**, or in the **IPA data-path handshake**.

Downstream provides five AP-side services to the running modem that mainline
`rhodep` does **not**:

1. **AP-side PIL load of the IPA/GSI firmware** (`qcom,ipa_fws`, `pas-id = 0xf`) —
   mainline says `qcom,gsi-loader = "modem"`, i.e. the opposite.
2. **`qcom,memshare`** QMI service (GPS / FTM / DIAG DDR loans) — no mainline equivalent exists at all.
3. **QMI thermal client + PA thermistors** (`qmi-tmd-devices`, `qmi-ts-sensors`, `pa_therm1/2`).
4. **`qcom,rmnet-ipa3` / `qcom,msm_gsi`** userspace/kernel data plumbing.
5. A much richer **glinkpkt / DS / DATAxx** channel set.

Top-5 ranked root-cause candidates are in **§8**.

---

## 1. Modem subsystem (MSS / remoteproc)

### 1.1 Downstream node — verbatim

`DS-DT/blair.dtsi:2944-2984`

```dts
pil_modem: qcom,mss@06000000 {
        compatible = "qcom,pil-tz-generic";
        reg = <0x06000000 0x100>;

        clocks = <&rpmcc RPM_SMD_XO_CLK_SRC>;
        clock-names = "xo";
        qcom,proxy-clock-names = "xo";

        vdd_cx-supply = <&S2E_LEVEL>;
        qcom,vdd_cx-uV-uA = <RPMH_REGULATOR_LEVEL_TURBO 100000>;
        qcom,proxy-reg-names = "vdd_cx";

        qcom,firmware-name = "modem";
        memory-region = <&pil_mpss_wlan_mem>;
        qcom,proxy-timeout-ms = <10000>;
        qcom,sysmon-id = <0>;
        qcom,ssctl-instance-id = <0x12>;
        qcom,pas-id = <4>;
        qcom,smem-id = <421>;
        qcom,minidump-id = <3>;
        qcom,aux-minidump-ids = <4>;
        qcom,complete-ramdump;
        /* Inputs from mss */
        interrupts-extended = <&intc GIC_SPI 307 IRQ_TYPE_LEVEL_HIGH>,
                <&modem_smp2p_in 0 0>,
                <&modem_smp2p_in 2 0>,
                <&modem_smp2p_in 1 0>,
                <&modem_smp2p_in 3 0>,
                <&modem_smp2p_in 7 0>;

        interrupt-names = "qcom,wdog",
                "qcom,err-fatal",
                "qcom,proxy-unvote",
                "qcom,err-ready",
                "qcom,stop-ack",
                "qcom,shutdown-ack";

        /* Outputs to mss */
        qcom,smem-states = <&modem_smp2p_out 0>;
        qcom,smem-state-names = "qcom,force-stop";
};
```

**FACT (key negatives).** Searching `blair.dtsi` for `mba`, `q6v5`, `mss-*`,
`snoc_axi`, `mnoc_axi`, `nav` clocks, `resets`, `vdd_mss`, `vdd_pll`, `vdd_mx`
inside the modem node returns nothing:

* No `mba` reserved-memory region — `NOT FOUND IN SOURCES`.
* No `qcom,active-clock-names` on `pil_modem` — only the proxy `xo`.
* No `vdd_mx` / `vdd_mss` / `vdd-pll` supply on `pil_modem` — `NOT FOUND IN SOURCES`.
  (Contrast: `pil_lpass` *does* have two rails, `DS-DT/blair.dtsi:2868-2872`.)
* No `resets` / `reset-names`.

**INFERENCE.** `qcom,pil-tz-generic` means the AP only issues
`qcom_scm_pas_init_image()/auth_and_reset()` SMCs; TZ performs the MBA/PBL
sequence, brings up the MSS clocks, de-asserts resets and votes the MSS-internal
rails. Consequently the *absence* of MX/MSS rails and MSS clocks in the AP DT is
**correct and expected**, and mainline's equally sparse node is **not** a bug.
This removes the "classic missing MX vote" hypothesis for the *boot* path
(see §3 for what remains).

### 1.2 Mainline node — verbatim

`ML/arch/arm64/boot/dts/qcom/sm6375.dtsi:1527-1566`

```dts
remoteproc_mss: remoteproc@6080000 {
        compatible = "qcom,sm6375-mpss-pas";
        reg = <0x0 0x06080000 0x0 0x10000>;

        interrupts-extended = <&intc GIC_SPI 307 IRQ_TYPE_EDGE_RISING>,
                              <&smp2p_modem_in 0 IRQ_TYPE_EDGE_RISING>,
                              <&smp2p_modem_in 1 IRQ_TYPE_EDGE_RISING>,
                              <&smp2p_modem_in 2 IRQ_TYPE_EDGE_RISING>,
                              <&smp2p_modem_in 3 IRQ_TYPE_EDGE_RISING>,
                              <&smp2p_modem_in 7 IRQ_TYPE_EDGE_RISING>;
        interrupt-names = "wdog", "fatal", "ready", "handover",
                          "stop-ack", "shutdown-ack";

        clocks = <&rpmcc RPM_SMD_XO_CLK_SRC>;
        clock-names = "xo";

        power-domains = <&rpmpd SM6375_VDDCX>;
        power-domain-names = "cx";

        memory-region = <&pil_mpss_wlan_mem>;

        qcom,smem-states = <&smp2p_modem_out 0>;
        qcom,smem-state-names = "stop";
        status = "disabled";

        glink-edge { /* IPCC_CLIENT_MPSS / GLINK_QMP, label "modem", remote-pid 1 */ };
};
```

`ML/arch/arm64/boot/dts/qcom/sm6375-motorola-rhodep.dts:380-383`

```dts
&remoteproc_mss {
        firmware-name = "qcom/sm6375/motorola/rhodep/modem.mbn";
        status = "okay";
};
```

`ML/drivers/remoteproc/qcom_q6v5_pas.c:950-963`

```c
static const struct qcom_pas_data sm6375_mpss_resource = {
        .crash_reason_smem = 421,
        .firmware_name = "modem.mdt",
        .pas_id = 4,
        .minidump_id = 3,
        .auto_boot = false,
        .proxy_pd_names = (char*[]){ "cx", NULL },
        .ssr_name = "mpss",
        .sysmon_name = "modem",
        .ssctl_id = 0x12,
};
```

### 1.3 Downstream ↔ mainline equivalence table

| Item | Downstream (`blair.dtsi:2944+`) | Mainline (`sm6375.dtsi:1527+` / `qcom_q6v5_pas.c:950+`) | Verdict |
|---|---|---|---|
| Load mechanism | `qcom,pil-tz-generic` (SCM PAS) | `qcom,sm6375-mpss-pas` (SCM PAS) | **same** |
| `pas-id` | `4` | `.pas_id = 4` | **same** |
| crash-reason SMEM id | `qcom,smem-id = <421>` | `.crash_reason_smem = 421` | **same** |
| minidump id | `3` (+aux `4`) | `.minidump_id = 3` (no aux) | minor diff |
| sysmon / ssctl | `sysmon-id=0`, `ssctl-instance-id=0x12` | `.sysmon_name="modem"`, `.ssctl_id=0x12` | **same** |
| watchdog IRQ | `GIC_SPI 307` | `GIC_SPI 307` | **same** (level vs edge, see note) |
| SMP2P in bits | 0=err-fatal, 1=err-ready, 2=proxy-unvote, 3=stop-ack, 7=shutdown-ack | 0=fatal, 1=ready, 2=handover, 3=stop-ack, 7=shutdown-ack | **same bit map** |
| SMP2P out bit | `<&modem_smp2p_out 0>` "force-stop" | `<&smp2p_modem_out 0>` "stop" | **same** |
| `reg` | `0x06000000 0x100` | `0x06080000 0x10000` | different, but `qcom_pas_probe()` never ioremaps `reg` — cosmetic |
| memory-region | `&pil_mpss_wlan_mem` | `&pil_mpss_wlan_mem` | **same** |
| Proxy rails | `vdd_cx` = `S2E_LEVEL` @ `TURBO`, `100000 uA` | `rpmpd SM6375_VDDCX`, set to `INT_MAX` perf state | equivalent-ish, see §3 |
| Proxy clock | `xo` | `xo` | **same** |
| Proxy hold | `qcom,proxy-timeout-ms = <10000>` | released on `handover` IRQ, no timer | **different**, see §3.3 |

**Note on IRQ trigger type.** Downstream declares the MSS watchdog as
`IRQ_TYPE_LEVEL_HIGH` (`blair.dtsi:2967`), mainline as `IRQ_TYPE_EDGE_RISING`
(`sm6375.dtsi:1531`). `INFERENCE:` mainline is consistent with every other
`*-mpss-pas` platform and the GIC config is taken from the driver's expectations;
this is unlikely to matter, but an edge-configured watchdog line that is actually
level-triggered would only cause a *missed* crash notification, not a crash.

---

## 2. Power / clock / transport topology (ASCII)

```
                                   ┌──────────────────────────────────────┐
                                   │            RPM  (SMD/glink)          │
                                   │  rpmcc  |  rpmpd (rwcx/rwmx/…)       │
                                   └───▲───────────────▲──────────────────┘
   AP proxy votes (boot window only)   │               │  modem's OWN votes
   ─────────────────────────────────── │ ───────────── │ ─────────────────
                                       │               │
   ┌───────────────────────────────────┴───┐   ┌───────┴────────────────────┐
   │  APSS / Linux                          │   │  MSS Hexagon (mpss)       │
   │                                        │   │   + WCSS (WLAN) on same   │
   │  remoteproc_mss (qcom_q6v5_pas)        │   │     SMP2P edge            │
   │    clocks:  rpmcc RPM_SMD_XO_CLK_SRC   │   └───┬───────────────────────┘
   │    pd:      rpmpd SM6375_VDDCX ──┐     │       │
   │             (downstream also     │     │       │
   │              TURBO + 100 mA load)│     │       │
   └──────────────┬───────────────────┼─────┘       │
                  │                   │             │
     SCM/TZ PAS   │                   │             │
     pas_id = 4   │                   │             │
     ┌────────────▼──────────┐        │             │
     │ TZ / QSEE             │  MX(S1E) / CX(S2E)   │
     │  - loads MBA           │  pmr735a SMPS       │
     │  - MSS clocks/resets   │                     │
     │  - MSS-internal rails  │                     │
     └────────────┬──────────┘                      │
                  │ auth+reset                      │
                  ▼                                 │
     pil_mpss_wlan_mem @ 0x8B80_0000, 0x1000_0000 (256 MiB)
                                                    │
  ── Transport ─────────────────────────────────────┴───────────────────────
     IPCC @ 0x208000   ─ IPCC_CLIENT_MPSS(1)
        ├─ IPCC_MPROC_SIGNAL_SMP2P      →  smp2p-modem (smem 435 / 428)
        │      master-kernel (out, bit0 = force-stop)
        │      slave-kernel  (in,  bits 0,1,2,3,7)
        │      "ipa"  out/in  (IPA clock handshake + setup-ready)
        │      "wlan" in      (WCSS force-fatal / early-crash)
        └─ IPCC_MPROC_SIGNAL_GLINK_QMP  →  glink "mpss" over SMEM
               channels: IPCRTR (QRTR/QMI), DS, glink_ssr,
                         DATA1/DATA4/DATA11/DATA40_CNTL (downstream only)

     SMEM @ 0x8090_0000 (2 MiB), hwlock = tcsr_mutex 3
     rmtfs  : 2.5 MiB shared buffer, client-id 1, VMIDs {MSS_MSA, NAV}
     memshare: QMI svc 0x34 inst 1, clients GPS(0)/FTM(1)/DIAG(2)  [downstream only]
     IPA v4.11 @ 0x5840000 + GSI @ 0x5804000, smp2p "ipa" edge on the MPSS edge
```

---

## 3. Power detail

### 3.1 Rail identification (FACT)

| Rail | Regulator | Evidence |
|---|---|---|
| `VDD_CX` | PMR735A **S2** (`S2E_LEVEL`, RPM resource `rwcx`) | `DS-DT/holi-regulators-pm6125.dtsi:751-757, 759-773` |
| `VDD_MX` | PMR735A **S1** (`S1E_LEVEL`, RPM resource `rwmx`) | `DS-DT/holi-regulators-pm6125.dtsi:694-716` |
| `VDD_GFX` | PM6125 **S8** (`rwgx`) | `DS-DT/holi-regulators-pm6125.dtsi:406-410` |
| `VDD_LPI_CX` | PM6125 **L1** (`rwlc`) | `DS-DT/holi-regulators-pm6125.dtsi:434-440` |
| `VDD_LPI_MX` | PM6125 **L17** (`rwlm`) | `DS-DT/holi-regulators-pm6125.dtsi:607-613` |

Mainline agrees (comments in `ML/.../sm6375-motorola-rhodep.dts:404-407, 485, 527-530`).

### 3.2 Downstream boot-time holds on MX and CX (FACT)

`DS-DT/holi-regulators-pm6125.dtsi:697-716` (MX) and `:754-773` (CX):

```dts
proxy-supply = <&pmr735a_s1_level>;                  /* MX */
    qcom,init-voltage-level = <RPM_SMD_REGULATOR_LEVEL_TURBO>;
    qcom,proxy-consumer-enable;
    qcom,proxy-consumer-voltage =
        <RPM_SMD_REGULATOR_LEVEL_TURBO RPM_SMD_REGULATOR_LEVEL_BINNING>;

proxy-supply = <&pmr735a_s2_level>;                  /* CX */
    qcom,init-voltage-level = <RPM_SMD_REGULATOR_LEVEL_TURBO>;
    qcom,proxy-consumer-enable;
    qcom,proxy-consumer-voltage =
        <RPM_SMD_REGULATOR_LEVEL_TURBO RPM_SMD_REGULATOR_LEVEL_BINNING>;
```

Also proxied at boot: `pmr735a_l1` @ 62 mA (`:806-817`), `pm6125_l8` @ 857 mA
(`:505-517`), `pm6125_l13` @ 62 mA (`:557-569`).

`INFERENCE:` mainline has **no** `qcom,proxy-consumer-*` concept, so MX and CX
fall back to whatever real AP consumers request. Because the modem places its own
RPM votes for MX/CX once it is running, this is *probably* harmless — but it means
there is a window (kernel boot → modem RPM votes) where the AP is not holding
MX/CX at TURBO, unlike downstream.

### 3.3 Proxy-vote release timing (FACT + INFERENCE)

* Downstream `subsys-pil-tz.c` reads `qcom,proxy-timeout-ms`
  (`DS-K/drivers/soc/qcom/subsys-pil-tz.c:1404-1407`) and applies
  `regulator_set_voltage(reg, uV, INT_MAX)` + `regulator_set_load(reg, uA)`
  (`DS-K/drivers/soc/qcom/subsys-pil-tz.c:445-470`). `pil_modem` asks for
  **10000 ms** and **100000 µA** on CX.
* Mainline votes `dev_pm_genpd_set_performance_state(pds[i], INT_MAX)` +
  `pm_runtime_get_sync()` (`ML/drivers/remoteproc/qcom_q6v5_pas.c:158-159`) and
  drops it as soon as the `handover` SMP2P bit fires.

`INFERENCE:` functionally similar. The 100 mA load hint has no mainline analogue
(rpmpd has no load concept) — on RPM platforms the load hint only selects
PWM/AUTO mode for the SMPS, and CX is a shared always-PWM rail, so this is very
unlikely to be the crash cause.

### 3.4 Are RF LDOs at risk of being turned off by Linux? — **No** (FACT)

I checked this explicitly because it is a classic port bug.
`ML/drivers/regulator/qcom_smd-regulator.c:99-104` returns the driver's own
`vreg->is_enabled`, which is `0` after the zeroed allocation at probe.
`regulator_late_cleanup()` (`ML/drivers/regulator/core.c:6822-6824`) bails out
when `_regulator_is_enabled(rdev) <= 0`. **Linux therefore never disables an
RPM regulator it did not itself enable**, so the modem's RF LDOs
(`pmr735a_l1..l7`, `pm6125_l*`) cannot be yanked out from under the modem by the
AP regulator core. Rule this hypothesis out.

One constraint mismatch worth noting anyway (FACT):

| LDO | Downstream range / init | Mainline range | Note |
|---|---|---|---|
| `pmr735a_l2` | `360000..752000`, init `704000` (`holi-regulators-pm6125.dtsi:820-831`) | `640000..640000` (`rhodep.dts:537-540`) | mainline pins it to the WLAN `vdd-cx-mx` value (`icnss` wants `640000 640000`, `blair.dtsi:3733`). Harmless for the modem, but it *clamps AP-side requests* only. |
| `pmr735a_l3` | `900000..1200000` | `1000000..1200000` | narrower in mainline |
| `pmr735a_l7` | `2700000..3300000`, init `3080000` | `2700000..3544000` | wider in mainline |
| `pm6125_l5` | `1650000..3050000` | `1650000..2960000` | |
| `pm6125_l7` | `788000..1050000` | `880000..880000` | mainline pins it |
| `pm6125_l19/l20/l21/l23` | max `3300000`/`3400000` | max `3304000`/`3312000` | rounding |

---

## 4. Reserved memory — downstream vs mainline

### 4.1 Downstream (`DS-DT/blair.dtsi:282-442`)

| Label | Address | Size | Line |
|---|---|---|---|
| `hyp_mem` | `0x8000_0000` | `0x60_0000` | 287 |
| `xbl_aop_mem` | `0x8070_0000` | `0x10_0000` | 292 |
| `reserved_xbl_uefi` | `0x8088_0000` | `0x1_4000` | 297 |
| `smem_mem` | `0x8090_0000` | `0x20_0000` | 302 |
| `fw_mem` | `0x80b0_0000` | `0x10_0000` | 307 |
| `cdsp_secure_heap_mem` | `0x80c0_0000` | `0x1e0_0000` | 312 |
| `pil_wlan_mem` | `0x8650_0000` | `0x20_0000` | 317 |
| `pil_adsp_mem` | `0x8670_0000` | `0x200_0000` | 322 |
| `pil_cdsp_mem` | `0x8870_0000` | `0x1e0_0000` | 327 |
| `pil_video_mem` | `0x8a50_0000` | `0x50_0000` | 332 |
| **`pil_ipa_fw_mem`** | **`0x8aa0_0000`** | **`0x1_0000`** | **337** |
| **`pil_ipa_gsi_mem`** | **`0x8aa1_0000`** | **`0xa000`** | **342** |
| `pil_gpu_micro_code_mem` | `0x8aa1_a000` | `0x2000` | 347 |
| **`pil_mpss_wlan_mem`** | **`0x8b80_0000`** | **`0x1000_0000`** (256 MiB) | **352** |
| `removed_mem` | `0xc000_0000` | `0x510_0000` | 357 |
| `ramoops` | `0xd000_0000` | `0x20_0000` | 387 |
| `memshare_mem` (dynamic pool) | *alloc-ranges 0..4 GiB* | `0x80_0000` (8 MiB), align 1 MiB | 436 |
| rmtfs (dynamic) | *allocated by driver* | `0x28_0000` (2.5 MiB) | 2436 |

### 4.2 Mainline (`ML/.../sm6375.dtsi:533-643`, `rhodep.dts:103-133`)

Every entry above through `removed_mem` is present with **identical address and
size**. Additional mainline-only entries:

```dts
rmtfs_mem: rmtfs@f3900000 {                       /* sm6375.dtsi:620-627 */
        compatible = "qcom,rmtfs-mem";
        reg = <0 0xf3900000 0 0x280000>;
        no-map;
        qcom,client-id = <1>;
        qcom,vmid = <QCOM_SCM_VMID_MSS_MSA QCOM_SCM_VMID_NAV>;
};
debug_mem@ffb00000 (0xc0000)      /* sm6375.dtsi:629 */
last_log_mem@ffbc0000 (0x80000)   /* sm6375.dtsi:634 */
cmdline_region@ffd00000 (0x1000)  /* sm6375.dtsi:639 */
```

**Verdict on carveouts: no mismatch.** `pil_mpss_wlan_mem` is byte-identical
(`blair.dtsi:352-355` vs `sm6375.dtsi:610-613`). This eliminates "wrong/missing
mpss carveout" as a cause.

### 4.3 Two real memory-map concerns

**(a) `rmtfs_mem@f3900000` is not a vendor address.** `grep -rn "f3900000"`
over the whole of `DS-DT/` returns **nothing**. Downstream instead allocates the
rmtfs buffer dynamically:

`DS-DT/blair.dtsi:2436-2442`
```dts
qcom,rmtfs_sharedmem@0 {
        compatible = "qcom,sharedmem-uio";
        reg = <0x0 0x280000>;        /* address 0 = allocate at runtime */
        reg-names = "rmtfs";
        qcom,client-id = <0x00000001>;
        qcom,vm-nav-path;
};
```
Size (`0x280000`), client-id (`1`) and the NAV VMID (`qcom,vm-nav-path`
↔ `QCOM_SCM_VMID_NAV`) all match mainline. `INFERENCE:` the mainline static
address is inherited from the SoC .dtsi (Sony murray shares it) and is inside the
declared RAM with no overlap, so it is *probably* fine — but it has never been
validated against Motorola's XBL/hyp map. Worth confirming against `/proc/iomem`
and `dmesg | grep rmtfs`.

**(b) Only 2 GiB of RAM is declared.** `ML/.../sm6375-motorola-rhodep.dts:103-106`

```dts
memory@80000000 {
        device_type = "memory";
        reg = <0x0 0x80000000 0x0 0x80000000>;
};
```

Downstream expects more (`DS-DT/blair.dtsi:37-43`):

```dts
mem-offline {
        compatible = "qcom,mem-offline";
        offline-sizes = <0x1 0x40000000 0x0 0x40000000>,
                        <0x1 0xc0000000 0x0 0x80000000>,
                        <0x2 0xc0000000 0x1 0x40000000>;
        granule = <512>;
};
```

`INFERENCE:` banks above `0x1_0000_0000` exist and are not declared. Not a modem
bug by itself, but it means any modem-requested allocation (memshare, rmtfs,
IPA DMA) competes for the low 2 GiB only.

---

## 5. Transport: SMP2P, GLINK/QRTR, IPCC, SMEM, IPA, rmtfs, memshare

### 5.1 SMP2P (FACT — essentially identical)

Downstream `DS-DT/blair.dtsi:2132-2170`:

```dts
qcom,smp2p-modem {
        compatible = "qcom,smp2p";
        qcom,smem = <435>, <428>;
        interrupt-parent = <&ipcc_mproc>;
        interrupts = <IPCC_CLIENT_MPSS IPCC_MPROC_SIGNAL_SMP2P IRQ_TYPE_EDGE_RISING>;
        mboxes = <&ipcc_mproc IPCC_CLIENT_MPSS IPCC_MPROC_SIGNAL_SMP2P>;
        qcom,local-pid = <0>;
        qcom,remote-pid = <1>;

        modem_smp2p_out: master-kernel { qcom,entry-name = "master-kernel"; … };
        modem_smp2p_in:  slave-kernel  { qcom,entry-name = "slave-kernel"; … };
        smp2p_ipa_1_out: qcom,smp2p-ipa-1-out  { qcom,entry-name = "ipa"; … };
        smp2p_ipa_1_in:  qcom,smp2p-ipa-1-in   { qcom,entry-name = "ipa"; … };
        smp2p_wlan_1_in: qcom,smp2p-wlan-1-in  { qcom,entry-name = "wlan"; … };
};
```

Mainline `ML/.../sm6375.dtsi:764-803` has the same `qcom,smem = <435>, <428>`,
the same pids, the same three entry names (`master-kernel`, `slave-kernel`,
`ipa`, `wlan`). **No difference.**

**Important structural fact:** the **WLAN** subsystem's `force-fatal` /
`early-crash` signals ride the **modem** SMP2P edge
(`blair.dtsi:2165`, `sm6375.dtsi:798-802`; consumer `blair.dtsi:3739-3744`).
`INFERENCE:` WLAN and the modem are coupled; if WiFi works on the port, the MSS
is up and the edge is functional, which is strong evidence that the crash is a
*post-boot service* problem rather than a boot/transport problem.

### 5.2 IPCC / SMEM / hwlock (FACT — identical)

| | Downstream | Mainline |
|---|---|---|
| IPCC | `qcom,ipcc@208000`, `GIC_SPI 334` (`blair.dtsi:999-1006`) | `mailbox@208000`, `GIC_SPI 334` (`sm6375.dtsi:812-819`) |
| SMEM | `memory-region=<&smem_mem>`, `hwlocks=<&tcsr_mutex 3>` (`blair.dtsi:1606-1611`) | `compatible="qcom,smem"` on `smem@80900000`, `hwlocks=<&tcsr_mutex 3>` (`sm6375.dtsi:553-558`) |
| TCSR mutex | `syscon@340000` + `qcom,tcsr-mutex` (`blair.dtsi:1595-1604`) | `hwlock@340000` `0x40000` (`sm6375.dtsi:821-825`) |

### 5.3 GLINK channels (FACT — mainline is much thinner)

Downstream `DS-DT/blair.dtsi:2237-2268`:

```dts
glink_modem: modem {
        qcom,remote-pid = <1>;
        transport = "smem";
        mboxes = <&ipcc_mproc IPCC_CLIENT_MPSS IPCC_MPROC_SIGNAL_GLINK_QMP>;
        label = "modem";
        qcom,glink-label = "mpss";

        qcom,modem_qrtr {
                qcom,glink-channels = "IPCRTR";
                qcom,low-latency;
                qcom,intents = <0x800 5   0x2000 3   0x4400 2>;
        };
        qcom,modem_ds       { qcom,glink-channels = "DS";  qcom,intents = <0x4000 0x2>; };
        qcom,modem_glink_ssr{ qcom,glink-channels = "glink_ssr"; qcom,notify-edges = <&glink_adsp>; };
};
```

Plus `qcom,glinkpkt` (`blair.dtsi:2349-2388`) exposing on edge `"mpss"`:
`DS→at_mdm0`, `DATA40_CNTL→smdcntl8`, `DATA1→smd7`, `DATA4→smd8`, `DATA11→smd11`.

Mainline only has the bare `glink-edge` child of `remoteproc_mss`
(`sm6375.dtsi:1557-1565`). `INFERENCE:` that is normal — upstream `qcom_glink_smem`
+ `qrtr-smd` bind `IPCRTR` dynamically and `rpmsg_char` can expose the rest, and the
pre-declared **intent sizes** (`0x800/0x2000/0x4400`) are a downstream-only
optimisation. Upstream GLINK negotiates intents on demand. Not a likely crash cause,
but note that a modem that expects large pre-posted RX intents on `IPCRTR` will
stall (not crash) if the AP cannot satisfy them.

### 5.4 IPA — **the biggest functional divergence**

#### Downstream (FACT)

`DS-DT/blair.dtsi:3166-3172`
```dts
qcom,ipa_fws {
        compatible = "qcom,pil-tz-generic";
        qcom,pas-id = <0xf>;
        qcom,firmware-name = "ipa_fws";
        qcom,pil-force-shutdown;
        memory-region = <&pil_ipa_fw_mem>;
};
```

`DS-DT/blair.dtsi:3174-3196` (excerpt)
```dts
ipa_hw: qcom,ipa@0x5800000 {
        compatible = "qcom,ipa";
        reg = <0x5800000 0x84000>, <0x5804000 0x23000>;
        reg-names = "ipa-base", "gsi-base";
        interrupts = <GIC_SPI 257 IRQ_TYPE_LEVEL_HIGH>,
                     <GIC_SPI 259 IRQ_TYPE_LEVEL_HIGH>;
        qcom,ipa-hw-ver = <20>;          /* IPAv4.11 */
        qcom,ipa-hw-mode = <0>;
        qcom,platform-type = <1>;
        qcom,ee = <0>;
        qcom,modem-cfg-emb-pipe-flt;
        qcom,ipa-wdi2; qcom,ipa-wdi2_over_gsi;
        qcom,arm-smmu; qcom,use-64-bit-dma-mask;
        clocks = <&rpmcc RPM_SMD_IPA_CLK>;
        …
        ipa_smmu_ap  { iommus = <&apps_smmu 0x04A0 0x0>;
                       qcom,additional-mapping = <0x0C123000 0x0C123000 0x2000>;   /* modem tables in IMEM */
                       qcom,ipa-q6-smem-size = <36864>; };   /* 0x9000 */
        ipa_smmu_wlan{ iommus = <&apps_smmu 0x04A1 0x0>; };
        ipa_smmu_uc  { iommus = <&apps_smmu 0x04A2 0x0>; };
        ipa_smmu_11ad{ iommus = <&apps_smmu 0x04A3 0x0>; };
};
```
plus `qcom,msm_gsi` (`blair.dtsi:3154`), `qcom,rmnet-ipa3` (`blair.dtsi:3158-3164`),
and the reserved regions `pil_ipa_fw_mem` **and** `pil_ipa_gsi_mem`
(`blair.dtsi:337-345`).

#### Mainline (FACT)

`ML/.../sm6375-motorola-rhodep.dts:1155-1193`
```dts
ipa: ipa@5840000 {
        compatible = "qcom,sm6375-ipa";
        iommus = <&apps_smmu 0x4a0 0x0>;
        reg = <0x0 0x05840000 0x0 0x7000>,
              <0x0 0x05847000 0x0 0x3000>,
              <0x0 0x05804000 0x0 0x2c000>;
        reg-names = "ipa-reg", "ipa-shared", "gsi";
        …
        qcom,gsi-loader = "modem";
        status = "okay";
};
```
`ML/drivers/net/ipa/data/ipa_data-v4.11.c:418-429` — `ipa_data_v4_11_sm6375`
is `ipa_data_v4_11` (the QCM2290 data) with `interconnect_count = 0`.

#### Analysis

1. **`qcom,gsi-loader = "modem"` contradicts the vendor design.**
   `FACT:` downstream carries `qcom,ipa_fws` with `qcom,pas-id = <0xf>` and a
   dedicated `pil_ipa_fw_mem` carveout, i.e. **the AP loads the IPA/GSI firmware
   via TZ PAS.** `FACT:` mainline's `"modem"` value makes `ipa_probe()` skip
   `ipa_firmware_load()` and defer `ipa_setup()` until the modem raises
   `ipa-setup-ready` (`ML/drivers/net/ipa/ipa_main.c:881, 896-907`).
   `FACT:` every Android-phone port upstream uses `"self"`
   (sdm845 phones, `sm7225-fairphone-fp4.dts:714`,
   `sm7325-motorola-dubai.dts:732`, `sm8350-hdk.dts:926`, …); only the ChromeOS
   trogdor/herobrine LTE SKUs use `"modem"`
   (`sc7180-trogdor-lte-sku.dtsi:27`, `sc7280-herobrine-lte-sku.dtsi:25`).
   `INFERENCE:` **this is very likely wrong for rhodep.** If the rhodep modem
   firmware does not contain the GSI loader (because the vendor design has the AP
   do it), then either IPA never gets set up, or the modem asserts the first time
   its IPA/GSI client runs — which is exactly at data-path bring-up.
   The correct configuration should be:
   ```dts
   memory-region = <&pil_ipa_fw_mem>;
   firmware-name = "qcom/sm6375/motorola/rhodep/ipa_fws.mbn";
   qcom,gsi-loader = "self";
   ```

2. **The IPA local memory map is borrowed from QCM2290, unverified for blair.**
   `FACT:` `ipa_data_v4_11_sm6375` reuses `ipa_mem_local_data` verbatim
   (`ipa_data-v4.11.c:418-429`, table at `:~230-320`), whose `IPA_MEM_END_MARKER`
   sits at offset `0x3000`, and `.smem_size = 0x00009000`.
   `FACT:` downstream declares `qcom,ipa-q6-smem-size = <36864>` = `0x9000`
   (`blair.dtsi:3252`) — **this matches**, which is a good sign.
   `FACT:` the user sized `"ipa-shared"` as `0x3000` while QCM2290 uses `0x2000`
   (`ML/.../agatti.dtsi:1661`); `ipa_mem_size_valid()`
   (`ML/drivers/net/ipa/ipa_mem.c:294-313`) rejects any region ending past
   `ipa->mem_size`, so `0x3000` is required for the v4.11 table. Correct.
   `INFERENCE:` the *sizes* line up, but the per-region offsets used by the
   rhodep modem cannot be verified from this corpus — the downstream IPA3
   (`dataipa` techpack) is **not** in the corpus (only `drivers/platform/msm/ipa_fmwk`
   exists, `DS-K/drivers/platform/msm/ipa_fmwk`). If the offsets differ, the modem
   rejects `IPA_INIT_DRIVER` over QMI and **asserts**.

3. **Missing SMMU context banks.** `FACT:` downstream declares four IPA SMMU CBs
   (AP `0x4A0`, WLAN `0x4A1`, uC `0x4A2`, 11ad `0x4A3`) plus the explicit IMEM
   mapping `0x0C123000 → 0x0C123000, 0x2000` ("modem tables in IMEM",
   `blair.dtsi:3248-3250`). Mainline maps only `0x4a0`. `INFERENCE:` upstream IPA
   handles IMEM via `ipa_mem_data.imem_addr = 0x146a8000 / size 0x2000`
   (`ipa_data-v4.11.c:~365`) — note this is a **different IMEM address** than the
   `0x0C123000` the vendor maps. That discrepancy deserves a hard look: an IPA
   that DMAs modem routing/filter tables to the wrong IMEM window will fault, and
   the modem is the peer of those tables.

4. `qcom,modem-cfg-emb-pipe-flt` (`blair.dtsi:3187`) — modem configures the
   embedded pipe filters. Upstream IPA has `modem_route_count = 8` in
   `ipa_data_v4_11*`; no DT knob. Informational.

### 5.5 rmtfs (FACT)

Downstream: `qcom,sharedmem-uio`, dynamic 2.5 MiB, client-id 1, `qcom,vm-nav-path`
(`blair.dtsi:2436-2442`). Mainline: `qcom,rmtfs-mem`, static `0xf3900000`,
2.5 MiB, client-id 1, VMIDs `MSS_MSA` + `NAV` (`sm6375.dtsi:620-627`).
Structurally equivalent.

`INFERENCE:` the DT side is fine; the **userspace `rmtfs` daemon and its backing
partitions** are the risk. The modem reads/writes its EFS (including all RF
calibration / RFNV items) through this path, and it only does so in earnest when
the RF stack comes online. If `rmtfs` is not running, cannot open
`/dev/disk/by-partlabel/modemst1|modemst2|fsg|fsc`, or the SCM VMID assignment
fails, the modem will assert precisely at "enable 4G".

### 5.6 memshare — **completely absent from mainline** (FACT)

`DS-DT/blair.dtsi:1080-1106`
```dts
qcom,memshare {
        compatible = "qcom,memshare";

        qcom,client_1 {   /* GPS */
                compatible = "qcom,memshare-peripheral";
                qcom,peripheral-size = <0x0>;
                qcom,client-id = <0>;
                qcom,allocate-boot-time;
                label = "modem";
        };
        qcom,client_2 {   /* DIAG */
                qcom,peripheral-size = <0x0>;
                qcom,client-id = <2>;
                label = "modem";
        };
        qcom,client_3 {   /* FTM */
                qcom,peripheral-size = <0x500000>;
                memory-region = <&memshare_mem>;
                qcom,client-id = <1>;
                qcom,allocate-on-request;
                label = "modem";
        };
};
```
Backing pool `memshare_mem`: 8 MiB, 1 MiB aligned, `no-map`, anywhere in 32-bit
space (`blair.dtsi:436-442`).

Client-ID decode is authoritative from the vendor driver
(`DS-K/drivers/soc/qcom/memshare/msm_memshare.c:65-78` and `:213-231`):

```c
case 0: clnt = "GPS";  break;
case 1: clnt = "FTM";  break;
case 2: clnt = "DIAG"; break;
```
QMI service: `MEM_SHARE_SERVICE_SVC_ID 0x34`, `INS_ID 1`
(`DS-K/drivers/soc/qcom/memshare/msm_memshare.h:8-10`).
Memory is hyp-assigned `VMID_HLOS ↔ VMID_MSS_MSA`
(`msm_memshare.c:207-209`).

`FACT:` `grep -rn memshare ML/drivers ML/net` → **no results**. Mainline Linux
has no memshare implementation whatsoever.

`INFERENCE:` this is the **direct explanation for "GNSS does not work via the
modem"** (§7), and a plausible secondary cause for modem asserts: the modem's GPS
task requests DDR at boot (`qcom,allocate-boot-time`) and gets no QMI responder.
Whether that is fatal depends on the modem build.

### 5.7 QMI thermal — absent from mainline (FACT)

`DS-DT/holi-thermal-modem.dtsi:2-104` declares `qmi-tmd-devices` (cooling devices
`pa`, `pa_fr1`, `modem`, `modem_bw_backoff`, `modem_current`, `modem_skin`,
`vbatt_low`, `charge_state`, `cpuv_restriction_cold`, `wlan`, `wlan_bw`, …) and
`:105-142` `qmi-ts-sensors` with the modem sensor list
(`"pa"`, `"pa_1"`, `"qfe_wtr0"`, `"modem_tsens"`, `"xo_therm"`,
`"qfe_wtr_pa0..3"`, `"modem_tsens1"`, `"qfe_wtr0_fr1"`, … — the `mmw*`/`beamer*`
entries are the generic holi list and are mmWave-only).
`QMI_MODEM_INST_ID = 0x0` (`DS-K/include/dt-bindings/thermal/thermal_qti.h:76`).

These are consumed by `DS-DT/holi-thermal.dtsi:139-310` (`mdm-core0/1-step`,
`tsens1` ch.6/7 → `modem_tj` 1/2/3) and by `blair.dtsi:3806`
(`q6-hvx-cx-cdev1` → `modem_pa 3 3`).

Mainline has the same **sensors** (`sm6375.dtsi:2547-2593`, `mdm-core0/1-thermal`
on `tsens1 6/7`) but **no cooling device at all** — only `passive` alerts at
90/95 °C and a **`critical` trip at 110 °C** (`sm6375.dtsi:2563-2567`).

`INFERENCE:` with no `modem_tj` / `modem_pa` cooling device, the AP cannot ask the
modem to back off. A sustained LTE TX session will heat the MSS with zero
mitigation; at 110 °C the thermal core triggers an **emergency shutdown**, not a
modem crash — so this is unlikely to be *the* bug, but it is a real
safety/regression risk once 4G does work.

---

## 6. RF / front-end

### 6.1 Where the RF control lines are (FACT, important negative result)

An exhaustive search of the downstream device tree for AP-visible RF control
returns essentially nothing for blair/holi:

```
grep -rn -i -E "grfc|rffe|qcom,mss-|wtr[0-9]|ant_sw|pa_en|fem_|qcom,rf-" DS-DT/
  → only:  qcx6490-cnss.dtsi (different SoC)
           holi-thermal-modem.dtsi:113,139  "qfe_wtr0", "qfe_wtr0_fr1"  (QMI sensor names)
```

* `qcom,mss-*` properties: **NOT FOUND IN SOURCES**
* `grfc` / `rffe` / `qlink` *pinctrl states*: **NOT FOUND IN SOURCES**
* Antenna-tuner / antenna-switch / PA-enable GPIOs: **NOT FOUND IN SOURCES**
* Any `rfc` (RF-card) node: **NOT FOUND IN SOURCES** (the only `rfc` string is
  the thermal zone `rfc_camera_therm`, `blair-rhodep-common-overlay.dtsi:470`)
* Transceiver (WTR/WCD-RF) part number: **NOT FOUND IN SOURCES**

The TLMM *function* list does confirm which RF-adjacent signals can appear on AP
pads (`DS-K/drivers/pinctrl/qcom/pinctrl-blair.c:1500-1546`, mirrored exactly in
`ML/drivers/pinctrl/qcom/pinctrl-sm6375.c:1409-1427`):

| GPIO | function | meaning |
|---|---|---|
| 49 | `vfr_1` | Voice Frame Reference (modem ↔ audio) |
| 75-78 | `uim2_data/clk/reset/present` | SIM slot 2 |
| 79-82 | `uim1_data/clk/reset/present` | SIM slot 1 |
| 101, 102 | `nav_gpio`, `NAV_PPS`, `GPS_TX` | GNSS 1PPS / GPS blanking |
| 103 | `QLINK0_WMSS` | QLINK0 WMSS |
| 104, 105 | `qlink0_request`, `qlink0_enable` | QLINK0 |
| 106 | `QLINK1_WMSS` | QLINK1 WMSS |
| 107, 108 | `qlink1_request`, `qlink1_enable` (alt `GPS_TX`) | QLINK1 |
| 118 | `pa_indicator` | PA activity indicator (coexistence) |

**INFERENCE (RF topology).** There is no `grfc`/`rffe` mux function in the
SM6375 TLMM at all. On this SoC the **RFFE bus, GRFCs and the transceiver
interface are on dedicated MSS pads outside the TLMM**, fully owned by the modem
DSP and invisible to Linux. This is why the vendor DT contains zero RF GPIO
description, and it means **mainline cannot be "missing an RF GPIO"** — there is
nothing to miss. RF front-end configuration lives entirely in the modem's
RFNV/RFC blobs in EFS, reached through **rmtfs** (§5.5).

### 6.2 ASCII RF chain (reconstructed)

```
  ┌───────────────────────────────────────────────────────────────────┐
  │ SM6375 MSS (Hexagon + RF SW)                                      │
  │                                                                   │
  │  RF task ── reads RFNV / RFC / calibration ──► [ rmtfs / EFS ]     │
  │      │                                          (AP userspace!)   │
  │      ├── RFFE bus ─────────┐  (dedicated MSS pads, NOT TLMM)      │
  │      ├── GRFC lines ───────┤                                      │
  │      └── QLINK0/1 ─────────┼──► TLMM 103..108 (companion/WMSS)    │
  │          pa_indicator ─────┼──► TLMM 118                          │
  │          nav_gpio/PPS ─────┼──► TLMM 101,102                      │
  └───────────────────────────┬┴──────────────────────────────────────┘
                              │
             ┌────────────────▼──────────────────┐
             │ Transceiver (WTR) + PA/FEM modules │  part numbers NOT IN SOURCES
             │  monitored by: qfe_wtr0, qfe_wtr_pa0..3, pa, pa_1  (QMI TS)
             │  board NTCs:   pa_therm1 = PM6125 ADC5_GPIO4_100K_PU
             │                pa_therm2 = PM6125 ADC5_AMUX_THM1_100K_PU
             └────────────────┬──────────────────┘
                              │
             ┌────────────────▼──────────────────────────────────────┐
             │ Antenna array — decoded from the SX9375 SAR map below │
             └───────────────────────────────────────────────────────┘
```

### 6.3 PA thermistors (FACT)

`DS-DT/blair-rhodep-common-overlay.dtsi:500-529`
```dts
pa_therm1 { thermal-sensors = <&pm6125_adc_tm ADC5_GPIO4_100K_PU>;  thermal-governor = "user_space"; };
pa_therm2 { thermal-sensors = <&pm6125_adc_tm ADC5_AMUX_THM1_100K_PU>; thermal-governor = "user_space"; };
```
The generic holi zones `pa-therm0-usr` / `pa-therm1-usr` are explicitly
**disabled** for rhodep (`:415-420`) and replaced by the two above.
Base channel definition: `DS-DT/holi-pmic-overlay-pm6125.dtsi:79-85, 171-175`
(`pa_therm1` = `ADC5_AMUX_THM1_100K_PU`, ratiometric, 200 µs settle).

`INFERENCE:` two board NTCs ⇒ **two PA/FEM clusters** on the rhodep PCB
(consistent with the 2-cluster antenna layout decoded below). These are
`user_space` zones: the Motorola thermal HAL reads them and pushes limits to the
modem through `modem_pa` / `modem_skin` QMI TMD. **Mainline declares neither the
PM6125 VADC channels nor the zones nor the QMI cooling device** — searching
`rhodep.dts` for `pa_therm` / `pm6125_vadc` returns nothing.

### 6.4 SAR sensor → antenna map (FACT + decode)

`DS-DT/blair-rhodep-common-overlay.dtsi:287-362` — Semtech **SX937x** on
`qupv3_se7_i2c` @ `0x2c`, IRQ `tlmm 24`, `cap_vdd-supply = <&pm6125_l9>`,
`Semtech,ref-phases-a = <5>`, `ref-phases-b = <6>`, `ref-phases-c = <0xff>`,
`Semtech,button-flag = <0x1f>`.

Register block `blair-rhodep-common-overlay.dtsi:319-326` with the vendor's own
antenna comments:

| Phase reg | Value | CS pin | Antenna | Vendor comment |
|---|---|---|---|---|
| `0x8030` PH0 | `0xFEF9FF` | CS5 | **ANT0** | *Bottom center* |
| `0x803C` PH1 | `0xFFF9FD` | CS0 | **ANT1 / ANT6** | *Top right* |
| `0x8048` PH2 | `0xFFD9FF` | CS4 | **ANT2** | *Top center* |
| `0x8054` PH3 | `0xF7F9FF` | CS6 | **ANT5** | *Bottom Right* |
| `0x8060` PH4 | `0xBFF9FF` | CS7 | **ANT8** | *Top Left* |
| `0x806C` PH5 | `0xFFF97F` | CS2 | **ANT0 / ANT5 ref** | *Bottom_Ref* |
| `0x8078` PH6 | `0xFFF9EF` | CS1 | **ANT8 ref** | *Middle* |
| `0x8084` PH7 | `0xFFF9FF` | — | — | *NOT USE* |

Also `/* set CS3 to hiz, because it used as IRQ pin */` (`:318`).

**Decode / INFERENCE.**

```
                 ┌──────────── phone front, portrait ────────────┐
  Top-Left  ANT8 ●                  ● ANT2 (Top center)        ● ANT1/ANT6 (Top right)
            (PH4/CS7)                 (PH2/CS4)                  (PH1/CS0)
                       ● ANT8_REF (PH6/CS1, "Middle")
  Bottom            ● ANT0 (PH0/CS5, bottom center)     ● ANT5 (PH3/CS6, bottom right)
                    ● ANT0/ANT5_REF (PH5/CS2, "Bottom_Ref")
                 └───────────────────────────────────────────────┘
```

* **Five sensed antennas**: ANT0, ANT1(+ANT6 shared pad), ANT2, ANT5, ANT8.
* **Two reference phases**: PH5 (bottom, references ANT0+ANT5) and PH6 (middle,
  references ANT8). `Semtech,ref-phases-a = <5>` / `-b = <6>` confirms PH5/PH6
  are references, PH7 unused.
* `button-flag = 0x1f` = 5 active "buttons"/phases (PH0..PH4), matching the five
  sensed antennas.
* Two spatial clusters (top group ANT1/2/8, bottom group ANT0/5) map cleanly onto
  the two PA thermistors of §6.3.
* ANT3, ANT4, ANT7 are **not** SAR-sensed. `INFERENCE:` these are likely the
  Wi-Fi/BT/GNSS/diversity antennas that do not transmit at SAR-relevant power.
  **This is inference, not a source statement.**
* Note `&sx937x { status = "disabled"; }` at `:253-255` disables the *generic*
  holi SAR node; the rhodep instance `sx937x_se7` is a new node on SE7 with
  `status = "ok"` (`:361`).

**Mainline:** no SX937x node, no SAR driver, no `pm6125_l9` consumer for it.
`INFERENCE:` on mainline, **SAR back-off will never be applied**. That does not
crash the modem (the modem's default table applies), but it is a regulatory issue
once TX works.

---

## 7. GNSS

### 7.1 Where GNSS lives (FACT)

An exhaustive search of `blair.dtsi`, `holi.dtsi`,
`blair-rhodep-common-overlay.dtsi`, `blair-pinctrl.dtsi`, `holi-pinctrl.dtsi`
for `gps|gnss|nav|lna` produces exactly **two** hits, both the same property:

```
DS-DT/blair.dtsi:2441   qcom,vm-nav-path;
DS-DT/holi.dtsi:2480    qcom,vm-nav-path;
```

Therefore:

* **No separate GNSS chip.** No `gnss` node, no `compatible` for any GNSS
  receiver: **NOT FOUND IN SOURCES**.
* **No GPS_EN / LNA-enable GPIO** anywhere in the device tree: **NOT FOUND IN SOURCES**.
* **No GNSS regulator** (`vdd-gnss`, `vdd-lna`, …): **NOT FOUND IN SOURCES**.
* **No `nav` clock** on any node: **NOT FOUND IN SOURCES**.

The only silicon-level GNSS footprint is the TLMM mux
(`pinctrl-blair.c:1520-1523`, identical in `pinctrl-sm6375.c:1410-1411`):

```
[101] = PINGROUP(101, nav_gpio, NAV_PPS, NAV_PPS, GPS_TX, …)
[102] = PINGROUP(102, nav_gpio, NAV_PPS, NAV_PPS, GPS_TX, …)
[107] = PINGROUP(107, qlink1_request, GPS_TX, …)
[108] = PINGROUP(108, qlink1_enable,  GPS_TX, …)
```

**Conclusion (well-supported INFERENCE):** GNSS on rhodep is **entirely inside
the modem DSP** ("Qualcomm Location / GNSS engine on MPSS"). The AP reaches it
only through **QMI service `loc` over QRTR**. `nav_gpio`/`NAV_PPS` on TLMM 101/102
are the 1PPS/blanking outputs, owned by the modem, requiring no AP driver.

### 7.2 Why GNSS does not work on the mainline port

Three AP-side dependencies of the modem-resident GNSS engine, all present
downstream and all missing in mainline:

1. **`memshare` client 0 = GPS**, `qcom,allocate-boot-time`
   (`blair.dtsi:1082-1088`; ID decode `msm_memshare.c:65-68`). The GNSS engine
   borrows DDR from HLOS over QMI `0x34`. **No mainline implementation exists.**
2. **rmtfs with the NAV VMID.** `qcom,vm-nav-path` (`blair.dtsi:2441`) /
   `QCOM_SCM_VMID_NAV` (`sm6375.dtsi:626`) exists specifically so the **NAV**
   processor can reach the EFS buffer (almanac, ephemeris, XTRA, NV). Present in
   mainline DT, but useless without the rmtfs daemon.
3. **Userspace `loc` QMI client.** No mainline/pmOS equivalent of Qualcomm's
   `loc_launcher`/`izat`; `gpsd` cannot speak QMI-LOC.

`INFERENCE:` consistent with the user's observation that only Wi-Fi/network
positioning works. Fixing 4G will **not** by itself give GNSS; memshare and a
QMI-LOC client are additionally required.

---

## 8. Mainline diff — ranked "present downstream, MISSING/DIFFERENT in mainline"

### TOP 5 root-cause candidates for "enable 4G → modem dies"

---

**#1 — IPA/GSI firmware loader is configured the *opposite* way from the vendor design.**

*Evidence:*
- Downstream: `qcom,ipa_fws { compatible = "qcom,pil-tz-generic"; qcom,pas-id = <0xf>; qcom,firmware-name = "ipa_fws"; memory-region = <&pil_ipa_fw_mem>; }` — `DS-DT/blair.dtsi:3166-3172`
- Downstream reserves both `pil_ipa_fw_mem@8aa00000` and `pil_ipa_gsi_mem@8aa10000` — `DS-DT/blair.dtsi:337-345`
- Mainline: `qcom,gsi-loader = "modem";` — `ML/.../sm6375-motorola-rhodep.dts:1183`
- Mainline `ipa` node has **no** `memory-region` and **no** `firmware-name`
- Driver behaviour: `if (loader == IPA_LOADER_MODEM) goto done;` — `ML/drivers/net/ipa/ipa_main.c:899-900`; `IPA_LOADER_SELF` → `ipa_firmware_load()` at `:902-906`
- Every upstream Android-phone port uses `"self"`, e.g. `ML/.../sm7325-motorola-dubai.dts:732`, `ML/.../sm7225-fairphone-fp4.dts:714`

*Why it fits the symptom:* the IPA/GSI handshake is exercised when the data path
is brought up. If the modem is not the GSI loader in this product, it will either
never assert `ipa-setup-ready` (data never works) or assert/crash when its IPA
client runs.

*Test:* switch to
`memory-region = <&pil_ipa_fw_mem>; firmware-name = "…/ipa_fws.mbn"; qcom,gsi-loader = "self";`
and supply `ipa_fws.mbn` from the vendor `/vendor/firmware_mnt`.

---

**#2 — rmtfs (EFS/RFNV) path not proven end-to-end.**

*Evidence:*
- Downstream: `qcom,rmtfs_sharedmem@0 { compatible = "qcom,sharedmem-uio"; reg = <0x0 0x280000>; qcom,client-id = <1>; qcom,vm-nav-path; }` — `DS-DT/blair.dtsi:2436-2442` (address **0** = runtime allocation)
- Mainline: static `rmtfs@f3900000`, `0x280000`, `qcom,vmid = <QCOM_SCM_VMID_MSS_MSA QCOM_SCM_VMID_NAV>` — `ML/.../sm6375.dtsi:620-627`
- `grep -rn "f3900000" DS-DT/` → **no match**: this address is not from Motorola's map.

*Why it fits the symptom:* RF calibration and all RFNV items live in EFS. The
modem boots from its cached image but must reach EFS the moment the RF stack goes
online (`DMS Set Operating Mode = ONLINE`), i.e. **exactly at "enable 4G"**. A
missing/failed `rmtfs` daemon, a failed `qcom_scm_assign_mem()` for the VMIDs, or
missing `modemst1/modemst2/fsg/fsc` partition nodes produce an immediate assert.

*Test:* `dmesg | grep -i rmtfs`; confirm `/dev/rmtfs*` or the qrtr rmtfs service is
serving; confirm the four EFS partitions are reachable; verify `0xf3900000` really
is free in `/proc/iomem`.

---

**#3 — `qcom,memshare` QMI service (0x34) does not exist in mainline at all.**

*Evidence:*
- Downstream: `qcom,memshare` with GPS(0, boot-time), DIAG(2), FTM(1, 5 MiB on request) — `DS-DT/blair.dtsi:1080-1106`; pool `memshare_mem` 8 MiB — `DS-DT/blair.dtsi:436-442`
- Client-ID decode: `case 0: "GPS"; case 1: "FTM"; case 2: "DIAG";` — `DS-K/drivers/soc/qcom/memshare/msm_memshare.c:65-78`
- Service ids: `MEM_SHARE_SERVICE_SVC_ID 0x34`, `INS_ID 1` — `DS-K/drivers/soc/qcom/memshare/msm_memshare.h:8-10`
- Hyp assign `VMID_MSS_MSA ↔ VMID_HLOS` — `msm_memshare.c:207-209`
- `grep -rn memshare ML/drivers ML/net` → **nothing**

*Why it fits the symptom:* a modem task that requests a memory loan and receives
no QMI response can time out into an assert. This is also the **primary
explanation for GNSS being dead** (§7.2).

---

**#4 — IPA local-memory / IMEM map is a straight copy of QCM2290 and unverified for blair.**

*Evidence:*
- `ipa_data_v4_11_sm6375` = `ipa_data_v4_11` minus interconnects — `ML/drivers/net/ipa/data/ipa_data-v4.11.c:418-429`
- Upstream IMEM window: `.imem_addr = 0x146a8000, .imem_size = 0x2000` — `ML/.../ipa_data-v4.11.c` (`ipa_mem_data`)
- Vendor maps a **different** IMEM window into the IPA AP context bank:
  `qcom,additional-mapping = <0x0C123000 0x0C123000 0x2000>;  /* modem tables in IMEM */` — `DS-DT/blair.dtsi:3248-3250`
- Vendor SMEM size `qcom,ipa-q6-smem-size = <36864>` (`0x9000`) **does** match upstream `.smem_size = 0x00009000` — good
- Vendor declares four IPA SMMU CBs `0x4A0/0x4A1/0x4A2/0x4A3` (`blair.dtsi:3244-3269`); mainline maps only `0x4a0` (`rhodep.dts:1158`)

*Why it fits the symptom:* the IPA memory-region table is negotiated with the
modem over IPA-QMI (`INIT_DRIVER`). A mismatch makes the modem reject the config
and assert — and this happens at data-path bring-up, not at modem boot.

*Test:* `dmesg | grep -i "ipa"` for `region %u ends beyond memory limit`
(`ML/drivers/net/ipa/ipa_mem.c:306`) or a QMI error; compare `0x0C123000` vs
`0x146a8000` against the SM6375 IMEM map.

---

**#5 — No QMI thermal mitigation client + no PA thermistors ⇒ no back-off path, and a 110 °C critical trip.**

*Evidence:*
- Downstream cooling devices `modem_pa`, `modem_tj`, `modem_skin`, `modem_current`, `modem_bw_backoff`, … — `DS-DT/holi-thermal-modem.dtsi:2-104`
- Downstream QMI sensors `"pa"`, `"pa_1"`, `"qfe_wtr0"`, `"modem_tsens"`, … — `DS-DT/holi-thermal-modem.dtsi:105-142`
- Downstream maps `tsens1 6/7 → modem_tj 1/2/3` at 95/105/115 °C — `DS-DT/holi-thermal.dtsi:139-232`
- Downstream board NTCs `pa_therm1`/`pa_therm2` — `DS-DT/blair-rhodep-common-overlay.dtsi:500-529`
- Mainline `mdm-core0-thermal` has passive 90/95 °C alerts with **no cooling-maps** and `mdm_core0_crit` `type = "critical"` at **110 °C** — `ML/.../sm6375.dtsi:2547-2569`
- Mainline `rhodep.dts` declares no `pa_therm*` channel and no `pm6125_vadc` node

*Why it fits the symptom:* an LTE TX session with no mitigation heats the MSS
fast. The observable would be "works for a while, then dies" (or a hard
thermal shutdown at 110 °C). If the failure is *immediate*, this is not the cause;
if it happens after tens of seconds of TX, it is a strong suspect.

---

### 8.2 Full "present downstream / missing-or-different in mainline" list

| # | Item | Downstream evidence | Mainline state | Severity for the bug |
|---|---|---|---|---|
| 1 | AP-side PIL of `ipa_fws` (pas-id 15) | `blair.dtsi:3166-3172` | `qcom,gsi-loader="modem"`, `rhodep.dts:1183` | **HIGH** |
| 2 | rmtfs daemon + EFS partitions (DT ok) | `blair.dtsi:2436-2442` | `sm6375.dtsi:620-627` (DT only) | **HIGH** (userspace) |
| 3 | `qcom,memshare` QMI 0x34 (GPS/FTM/DIAG) + 8 MiB pool | `blair.dtsi:1080-1106, 436-442` | **absent everywhere** | **HIGH** (fatal for GNSS) |
| 4 | IPA IMEM window `0x0C123000` + 4 SMMU CBs | `blair.dtsi:3244-3269` | only CB `0x4a0`; IMEM `0x146a8000` from QCM2290 data | **HIGH** |
| 5 | QMI TMD cooling devices + QMI TS sensors | `holi-thermal-modem.dtsi:2-142` | absent; critical trip at 110 °C | MEDIUM |
| 6 | `pa_therm1/2` PM6125 VADC channels + zones | `blair-rhodep-common-overlay.dtsi:500-529` | absent | MEDIUM |
| 7 | `qcom,rmnet-ipa3` + `qcom,msm_gsi` | `blair.dtsi:3154-3164` | upstream `ipa_modem.c` covers rmnet; no `msm_gsi` needed | LOW |
| 8 | MX/CX `qcom,proxy-consumer-enable` @ TURBO held past boot | `holi-regulators-pm6125.dtsi:707-716, 767-773` | no proxy-consumer concept | MEDIUM-LOW |
| 9 | `qcom,vdd_cx-uV-uA = <TURBO 100000>` load hint | `blair.dtsi:2953` | rpmpd has no load concept | LOW |
| 10 | `qcom,proxy-timeout-ms = <10000>` | `blair.dtsi:2958` | released on `handover` IRQ | LOW |
| 11 | GLINK pre-declared intents (`0x800 5 / 0x2000 3 / 0x4400 2`) | `blair.dtsi:2251-2257` | negotiated dynamically | LOW |
| 12 | `glinkpkt` DS / DATA1 / DATA4 / DATA11 / DATA40_CNTL | `blair.dtsi:2349-2388` | `rpmsg_char` can expose on demand | LOW |
| 13 | `qcom,aux-minidump-ids = <4>` | `blair.dtsi:2964` | not in `sm6375_mpss_resource` | LOW (debug only) |
| 14 | SX9375 SAR sensor (5 antennas) | `blair-rhodep-common-overlay.dtsi:287-362` | absent | LOW for crash, **HIGH for compliance** |
| 15 | Watchdog IRQ `LEVEL_HIGH` vs `EDGE_RISING` | `blair.dtsi:2967` vs `sm6375.dtsi:1531` | different | LOW |
| 16 | `mss@06000000 0x100` vs `remoteproc@6080000 0x10000` | `blair.dtsi:2946` vs `sm6375.dtsi:1529` | different, `reg` unused by driver | NONE |
| 17 | RAM: 2 GiB declared vs `mem-offline` banks above 4 GiB | `blair.dtsi:37-43` | `rhodep.dts:103-106` | LOW (indirect) |
| 18 | `rmtfs_mem` at a non-vendor static address | *not in vendor tree* | `sm6375.dtsi:620-627` | needs verification |

### 8.3 Explicitly **verified as NOT a problem**

* `pil_mpss_wlan_mem` carveout: **byte-identical** (`blair.dtsi:352-355` vs `sm6375.dtsi:610-613`).
* `pas-id`, crash-reason SMEM id, sysmon/ssctl ids, SMP2P bit map, SMP2P SMEM ids: all identical (§1.3).
* IPCC / SMEM / TCSR-mutex: identical (§5.2).
* TLMM reserved GPIOs: downstream `13, 14, 15, 16` for the `QUP0_SE2_AP` build
  (`DS-K/drivers/pinctrl/qcom/pinctrl-blair.c:1595-1600`) — mainline
  `gpio-reserved-ranges = <13 4>` (`rhodep.dts:696`). **Exact match.**
  (rhodep must be the `QUP0_SE2_AP` variant, since it uses gpio46 for panel VCI.)
* Linux disabling the RF LDOs: impossible with `qcom_smd-regulator` (§3.4).
* Missing MX / MSS / PLL supplies on the modem node: not a bug — TZ owns them
  under `pil-tz-generic`/PAS on both sides (§1.1).
* Missing RF GPIOs: there are none to declare; RFFE/GRFC are off-TLMM (§6.1).

---

## 9. Suggested debug order

1. **Get the modem's own assert string.** The crash reason is written to SMEM id
   `421` and surfaced by `qcom_q6v5` as
   `"fatal error received: <file>:<line>"`. `dmesg -w` while toggling 4G. That
   single line will discriminate between candidates #1–#5 immediately
   (`ipa_*.c` → #1/#4; `rfnv`/`efs`/`fs_` → #2; `memshare`/`mem_heap` → #3;
   `therm`/`tmd` → #5).
2. Flip `qcom,gsi-loader` to `"self"` with `pil_ipa_fw_mem` + `ipa_fws.mbn`
   (candidate #1). This is a one-line DT change and the cheapest test.
3. Confirm `rmtfs` is running and bound to the four EFS partitions; check
   `/proc/iomem` for `0xf3900000` (candidate #2).
4. If the assert mentions memory/heap, a minimal in-kernel or userspace memshare
   QMI responder is needed (candidate #3). This is also the prerequisite for GNSS.
5. Add the PM6125 VADC `pa_therm1/2` channels and a `user_space` zone; even
   without QMI TMD it gives visibility into whether #5 (thermal) is in play.

## 10. Open questions the corpus cannot answer

* The downstream **IPA3 driver** (`dataipa` techpack) is **not** in the corpus —
  only `DS-K/drivers/platform/msm/ipa_fmwk`. The authoritative per-region IPA
  local-memory offsets for blair therefore could not be cross-checked against
  `ipa_mem_local_data`.
* Transceiver (WTR), PA and FEM **part numbers**: not present in any source file.
* Which physical antennas ANT3, ANT4 and ANT7 are: not stated; only ANT0, ANT1,
  ANT2, ANT5, ANT6, ANT8 appear in the SX9375 comments.
* Whether the rhodep **modem.mbn actually contains a GSI loader**: determinable
  only by experiment (step 2 above).

---

## 11. Modem-as-radio-sensor: per-frequency power survey via QMI-NAS (session 9, 2026-09-28)

Context: the goal was raw-IQ capture (toward an SDR / eventually a BTS). Three
parallel RE passes proved **raw IQ is structurally unreachable from the AP** on
mainline — see the port repo `docs/modem-diag-wip/NEXT-STEPS-IQ.md` (session-9
update) and `docs/modem-diag-wip/blob-analysis/OUT_{diag_log_iq,rx_measure,qmi_qdss}.md`.
The only IQ producer is FTM `IQ_CAPTURE`, gated behind an RF cal-mode set by
resident code outside the MBN; DIAG log packets carry no raw IQ; no QMI/QDSS route
bypasses the gate. The modem also cannot be an SDR front-end for yateBTS/srsRAN
(no full-duplex IQ streaming, no sub-µs TX/RX timing, RF-chain code not in the MBN).

**What IS reachable today, with no cal-mode:** a per-frequency signal-power survey
over **QMI-NAS** in normal ONLINE operation (evidence in `OUT_qmi_qdss.md`; the
NAS handlers live in `modem.b21`, none touch the cal gate `0xcbf4f740` /
carrier ptr `0xca79c494`):

- `perform_network_scan` / `perform_incremental_network_scan` / `force_lte_scan`
  → detected cells across bands, per-cell signal.
- `get_cell_location_info` → serving + neighbour cells with per-EARFCN RSRP/RSRQ
  (`4g_cell_neighbor_info`, `neighbors_count`).
- `get_signal_strength` / `get_sig_info` / `get_rf_band_info` → serving dBm.
- `get_arfcn_list`, `get_lte_cphy_ca_info`; GSM power scan + `common_rssi_ind`
  (`TRUE_RSSI (=%d dBm)`).

This is a **cell-search-granularity spectrum survey** (power vs EARFCN/ARFCN in
dBm), NOT a dense spectrogram and NOT IQ. It is the realistic "SDR-lite"
capability the closed modem offers.

### NEXT STEP (planned) — implement the power-survey with qmicli

Build a small tool/script on top of `qmicli` over QRTR (the same transport already
working; `qmicli -d qrtr://0 ...`), to drive NAS network-scan + cell-info and emit
a power-vs-frequency table (a coarse cellular-band spectrum survey). Sketch:

1. Stop ModemManager (it holds the QMI clients), bring the modem online.
2. `qmicli -d qrtr://0 --nas-network-scan` (all RATs / a band list) → parse the
   returned cells (RAT, band, EARFCN/ARFCN, cell id, RSRP/RSRQ/RSSI dBm).
3. `qmicli -d qrtr://0 --nas-get-cell-location-info` → serving + neighbours with
   per-EARFCN RSRP/RSRQ; loop/aggregate for a survey.
4. `--nas-get-signal-info` / `--nas-get-rf-band-info` for the serving-cell dBm.
5. Emit CSV/JSON: freq (from EARFCN→Hz), band, power dBm, cell id — a scan table.
6. Restore: ModemManager back up (or leave the tool holding the QMI client).

Deliverable location when built: `nethunter-rhodep-repo/userspace/modem/` +
`scripts/modem/` (a `rhodep-rf-survey` script), documented in the port repo.
No kernel change needed — it's pure userspace over the existing QMI/QRTR path.

**Modem closed for now** (this session). Raw-IQ/SDR path is documented as
structurally blocked; the QMI-NAS power survey is the queued, achievable radio
feature to implement next.
