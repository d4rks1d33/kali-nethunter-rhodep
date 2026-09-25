# OS IQ PATH — todo lo que falta del lado del OS para sacar muestras IQ raw del modem
Device: Moto G82 5G (rhodep, SM6375) · Kernel: 7.2.0-rc5 (postmarketOS mainline)
Objetivo: modem escribe IQ raw en memshare (client-id 1, ~5 MiB) → AP las lee.

Leyenda: **FACT** = verificado en fuente/repo/DT de este sistema · **INFERENCE** = deducción
con base dura · **UNKNOWN** = no resoluble sin equipo en vivo.

Fuentes verificadas en esta pasada:
- Kernel 7.2-rc5: `/home/pmos/.local/var/pmbootstrap/chroot_native/tmp/hdrfix/linux-7.2-rc5/`
- Headers/.config: `.../tmp/khdr/usr/src/linux-headers-7.2.0-rc5/`
- Repo: `/opt/postmarket/nethunter-rhodep-repo/` (kernel/, userspace/, scripts/)
- RE: `/tmp/modemre/findings/{map_iq_capture.md,MASTER_MODEM_MAP.md}`

---

## 0. VEREDICTO EJECUTIVO

El path del OS está **~80% construido y correcto**, con **una pieza clave que falta escribir**
(char device / lector desde el AP) y **dos piezas escritas pero NO integradas** (los patches DT
0120/0121 no están en el APKBUILD → el DT vivo no reserva la región). Además hay un
**bug de acceso doble-VMID**: tal como está, cuando el modem sea dueño de la región (VMID_MSS_MSA)
el AP (VMID_HLOS) **no podrá leerla** — hay que asignar a AMBOS VMIDs, no sólo al modem.

Los tres bloqueadores que exigen equipo en vivo (SET_MODE cal, command_id, cuerpos RFLTE) son del
lado del **firmware/protocolo DIAG**, no del OS, y están fuera del alcance de este documento (ver
MASTER_MODEM_MAP.md §12). Este documento cubre exclusivamente la infra del OS.

---

## 1. hyp_assign_phys EN MAINLINE 7.2-rc5 — VIABILIDAD

**FACT — todo lo que rhodep_memassign.c necesita existe y está exportado en 7.2-rc5.**

Verificado en `drivers/firmware/qcom/qcom_scm.c` e includes:

| Símbolo / macro | Ubicación | Estado |
|---|---|---|
| `qcom_scm_assign_mem()` | qcom_scm.c:1355, prototipo qcom_scm.h:109 | **EXPORT_SYMBOL_GPL** (qcom_scm.c:1424) |
| `qcom_scm_is_available()` | qcom_scm.c:2559 | **EXPORT_SYMBOL_GPL** (qcom_scm.c:2564) |
| `struct qcom_scm_vmperm {int vmid; int perm;}` | qcom_scm.h:24 | presente |
| `QCOM_SCM_VMID_HLOS` = 0x3 | dt-bindings/firmware/qcom,scm.h:12 | presente |
| `QCOM_SCM_VMID_MSS_MSA` = 0xF | qcom,scm.h:21 | presente |
| `QCOM_SCM_PERM_READ/WRITE` = 0x4/0x2 | **qcom_scm.h:56-57** | presente |
| `QCOM_SCM_PERM_RW` = READ\|WRITE = 0x6 | **qcom_scm.h:59** | presente |
| `dma_alloc_pages()` | kernel/dma/mapping.c | **EXPORT_SYMBOL_GPL** (mapping.c:736) |
| `__dma_sync_single_for_device()` | kernel/dma/mapping.c | EXPORT_SYMBOL (mapping.c:410) |

Firma exacta (coincide con la llamada del módulo):
```c
int qcom_scm_assign_mem(phys_addr_t mem_addr, size_t mem_sz, u64 *srcvm,
                        const struct qcom_scm_vmperm *newvm, unsigned int dest_cnt);
```
El módulo llama `qcom_scm_assign_mem((phys)base, (size), &srcvm, &dest, 1)` con
`srcvm = BIT(QCOM_SCM_VMID_HLOS)` — **correcto**. `srcvm` se actualiza in-place con el bitmap de
nuevos dueños, y el módulo lo reusa para el revert en `_exit` — **correcto**.

**Config del kernel (verificado en .config):** `CONFIG_QCOM_SCM=y`, `CONFIG_CMA=y`,
`CONFIG_DMA_CMA=y`, `CONFIG_ARCH_FORCE_MAX_ORDER=10`, `CONFIG_MODULE_ALLOW_BTF_MISMATCH=y`.
Todo lo que el módulo asume en sus comentarios se sostiene, **con una corrección**:

> **INEXACTITUD en el comentario del módulo (no bloquea, corregir):** el header dice "a 40 MiB
> default CMA area". El .config real tiene **`CONFIG_CMA_SIZE_MBYTES=32`**, no 40. 5 MiB de un
> área CMA de 32 MiB sigue cabiendo holgado, así que `alloc=1` funciona; sólo el número del
> comentario está mal. FACT.

### 1.1 ¿Compila/linkea el módulo out-of-tree?
**FACT: sí.** Todos los símbolos que usa son EXPORT_SYMBOL(_GPL) y el módulo declara
`MODULE_LICENSE("GPL")` (necesario para linkear contra los _GPL). El Makefile
(`kernel/diag-modules/Makefile`) compila con `clang` (el kernel se buildeó con LLVM=1) contra
`/lib/modules/$(uname -r)/build`; el .config va en el paquete de headers y
`CONFIG_MODULE_ALLOW_BTF_MISMATCH=y` permite cargar módulos de otro toolchain.

### 1.2 Correctitud del módulo — revisión
**FACT: rhodep_memassign.c es correcto y cuidadoso.** Puntos fuertes verificados:
- Guarda `region_is_reserved()` que **camina el /reserved-memory vivo** y exige `no-map` antes de
  asignar una región del DT (memassign_init: no procede sin reserva salvo `force=1`).
- `alloc=1`: usa `dma_alloc_pages` (CMA vía dma-direct) porque 5 MiB es order-11 y MAX_ORDER=10;
  verifica `dma_addr == phys` y **rechaza** si difieren (evita entregar al modem una addr que no
  es la que TZ autorizó).
- Limpia caché (`dma_sync_single_for_device(..., DMA_TO_DEVICE)`) antes del assign para que un
  write-back sucio no caiga después del cambio de dueño (sería XPU violation).
- Publica `/sys/kernel/rhodep_memshare/{base,size,assigned,dest_vmid,source}` **sólo tras** el SCM
  OK; `assigned=1` es el guard que lee el daemon.
- En `_exit` revierte con `revert=1` (default) y, si el revert falla, **NO libera** las páginas
  (correcto: nunca devolver al buddy allocator páginas que el modem aún posee).

**PROBLEMA DE DISEÑO (clave, ver §4): asigna sólo a UN destino.** `dest.vmid = QCOM_SCM_VMID_MSS_MSA`,
`dest_cnt=1`, y **HLOS no está en la lista de destino**. `qcom_scm_assign_mem` reprograma el XPU
para que los dueños sean EXACTAMENTE el conjunto de destino → tras la llamada **HLOS pierde el
acceso**. El AP no podrá leer la región. Esto hay que arreglarlo (§4).

**Riesgo residual reconocido por el propio módulo (FACT, no handleable desde módulo):** en `alloc=1`
sobre CMA queda un alias lineal cacheable; `set_direct_map_invalid_noflush()` no está exportado, así
que una lectura especulativa por ese alias podría tocar el XPU. Por eso **la reserva no-map del DT
(patch 0120) es el estado final correcto**, y `alloc=1` es sólo el modo "sin reflashear".

---

## 2. LA RESERVA DT (patch 0120) — VALIDEZ DE 0x8ab00000 / 0x800000

**FACT: la dirección es válida y NO pisa ninguna reserva.** Verificado contra
`arch/arm64/boot/dts/qcom/sm6375.dtsi` (reserved-memory del SoC):

```
pil-wlan@86500000        0x86500000 + 0x200000
pil-adsp@86700000        0x86700000 + 0x2000000
pil-cdsp@88700000        0x88700000 + 0x1e00000
pil-video@8a500000       0x8a500000 + 0x500000   -> ends 0x8aa00000
pil-ipa-fw@8aa00000      0x8aa00000 + 0x10000    -> ends 0x8aa10000
pil-ipa-gsi@8aa10000     0x8aa10000 + 0xa000     -> ends 0x8aa1a000
pil-gpu-ucode@8aa1a000   0x8aa1a000 + 0x2000     -> ends 0x8aa1c000   <-- vecino inferior
   ...........  HUECO LIBRE: 0x8aa1c000 .. 0x8b800000  (~13.9 MiB) ...........
pil-mpss-wlan@8b800000   0x8b800000 + 0x10000000 (256 MiB)           <-- vecino superior
```
La región propuesta **memshare@8ab00000 + 0x800000 → 0x8b300000** cae entera dentro del hueco:
- 1 MiB por encima del final de pil-gpu-ucode (0x8aa1c000 → 0x8ab00000): margen OK.
- Termina en 0x8b300000, **5 MiB por debajo** de pil-mpss-wlan (0x8b800000): margen OK.
- 1 MiB aligned (0x8ab00000): cumple el `alignment = <0 0x100000>` que stock exige. **FACT.**

Esto coincide byte-a-byte con la medición de /proc/iomem citada en el patch 0120
(`8aa1c000-8b7fffff : System RAM`). El único otro no-map cercano es `removed@c0000000` (muy arriba,
sin conflicto).

**¿`no-map` es correcto para hyp_assign?** **FACT: sí, es lo correcto y necesario.** hyp_assign
mueve páginas fuera del dominio de HLOS; si el kernel las tuviera mapeadas (reserved plano o
`reusable`) podría accederlas por el linear map y tocar el XPU. `no-map` garantiza que Linux nunca
las mapea. El propio módulo exige `no-map` en `region_is_reserved()`. **Correcto.**

### 2.1 ⛔ ESTADO: EL PATCH NO ESTÁ APLICADO — BLOQUEADOR
**FACT (verificado en `kernel/APKBUILD`):** el `source=` salta de `0118-...` directo a `0122-...`.
`grep -c` de `0119`, `0120`, `0121` en el APKBUILD = **0**. Es decir:
- **0119** (crypto-engine) — no aplicado
- **0120** (reserve memshare region) — **NO APLICADO** → el DT vivo **no** reserva 0x8ab00000
- **0121** (keep pmr735a L1 on for the modem) — no aplicado

Consecuencia: `find_reserved_region()` del daemon no encuentra nada → el daemon responde 0. Sin
0120, la única vía es `rhodep_memassign.ko alloc=1` (CMA), con el riesgo especulativo residual.

**Además: el .dts base (que entrega el patch 0001) NO contiene las reservas pil-\*** — esas viven en
`sm6375.dtsi`. El patch 0120 inserta el nodo tras `wdog_cpuctx@aefd2000` (en el .dts de rhodep), lo
cual es correcto: agrega un hijo más a `reserved-memory`. Pero el ANCLA del hunk
(`reg = <0x0 0xaefd2000 0x0 0x2e000>;` + `no-map;`) debe existir en el .dts en el momento en que se
aplica 0120; **FACT: existe** (rhodep.dts:183-185, `wdog_cpuctx@aefd2000`). El patch aplica limpio.

---

## 3. FLUJO DE ESTABLECIMIENTO Y ORDEN DE ARRANQUE

Orden correcto (a→d), y quién lo hace hoy:

```
(a) DT reserva la región no-map            <- patch 0120 (FALTA aplicar)  o  alloc=1 (CMA)
(b) rhodep_memassign hyp_assign HLOS->MSS  <- módulo (existe; bug: falta HLOS en destino, §4)
     publica /sys/kernel/rhodep_memshare/assigned=1
(c) daemon responde QMI 52 con la addr     <- memshare-daemon (existe; hoy responde 0 sin región)
(d) modem escribe                          <- firmware (ya entendido por RE)
```

### 3.1 ¿Cuándo pide el modem? QUERY_SIZE al boot, ALLOC on-request
**FACT (medido, MASTER_MODEM_MAP.md + memshare-probe):** el modem manda **MEM_QUERY_SIZE (0x0024)
para client_id=1 EXACTAMENTE UNA VEZ durante el bring-up** (`00 01 0024 000e00 01 0400 01000000
10 0400 00000000`). Stock declara `qcom,allocate-on-request`, así que:
- Si QUERY_SIZE devuelve **0** → el modem **NO** hace follow-up (FACT: "told there is nothing, the
  modem does not follow up").
- Si QUERY_SIZE devuelve **5 MiB** → el modem responde inmediato con **MEM_ALLOC_GENERIC**
  (`num_bytes=0x500000, client_id=1, alloc_contiguous=1`) (FACT, patch 0120 texto).

`allocate-on-request` sólo difiere la *asignación física*, NO la *talla anunciada*: en stock,
`init_size` se toma de `qcom,peripheral-size` en probe y `handle_query_size_req` responde eso. Por
eso el daemon debe anunciar 5 MiB en QUERY_SIZE (no 0) para que el modem pida.

### 3.2 El daemon DEBE estar corriendo ANTES del bring-up del modem
**FACT + INFERENCE:** el modem pregunta una sola vez, temprano. Si nadie responde, se queda
esperando (o continúa sin memshare y IQ_CAPTURE no tendrá buffer). El service ya está diseñado para
esto (`rhodep-memshare.service`): `After=qrtr-ns.service`, **`Before=rmtfs.service`**,
`Wants=qrtr-ns.service`. El comentario del service es correcto: "el modem pregunta una vez durante
bring-up, antes de que rmtfs lo libere, así que hay que publicar primero".

**PROBLEMA DE ORDEN NO RESUELTO en el repo:** el módulo `rhodep_memassign` debe estar **cargado y
con `assigned=1` ANTES de que el daemon responda QUERY_SIZE con 5 MiB** (el daemon lee
`/sys/kernel/rhodep_memshare/assigned`). El header del módulo lo dice
(`ExecStartPre=/sbin/modprobe`), pero **el .service actual NO tiene ese ExecStartPre** ni
`modules-load.d`. Con el DT patch 0120 aplicado esto no importa (la región viene del DT, el módulo
no es necesario para la reserva — aunque sí para el hyp_assign, ver §4). Con `alloc=1` es
obligatorio ordenarlo. **FALTA: integrar el modprobe/orden en el service (§6).**

### 3.3 Si el modem ya booteó sin memshare, ¿re-pregunta?
**INFERENCE fuerte: NO re-pregunta espontáneamente.** El QUERY_SIZE es one-shot en bring-up. Para
que vuelva a preguntar hay que **reiniciar el subsistema del modem** (remoteproc MSS stop/start:
`echo stop > /sys/class/remoteproc/remoteprocN/state; echo start > ...`, o SSR). Tras el restart,
el modem re-ejecuta bring-up y re-emite QUERY_SIZE — para entonces el daemon+módulo deben estar
listos. **UNKNOWN:** si existe un re-query periódico o gatillado por un comando FTM concreto (no
observado; requiere prueba en vivo).

---

## 4. LECTURA DE LAS MUESTRAS DESDE EL AP — LA PIEZA CLAVE

### 4.1 ⛔ hyp_assign a MSS_MSA solo QUITA el acceso a HLOS
**FACT (semántica de qcom_scm_assign_mem):** la llamada reprograma el XPU para que los dueños sean
**exactamente** el `newvm[]` pasado. El módulo hoy pasa `dest={vmid=MSS_MSA, perm=RW}`, `dest_cnt=1`
y **NO incluye HLOS**. Resultado: tras el assign, **HLOS pierde acceso** a la región. Si el AP
intenta leerla (mmap /dev/mem o char device), toma **XPU violation** — que en este SoC es el mismo
tipo de fallo silencioso que sufre el modem al revés.

**→ Para que el AP LEA las muestras hay que asignar a AMBOS VMIDs.** Esto es lo que hace el driver
vendor para buffers compartidos AP↔modem. La corrección concreta en el módulo:

```c
/* En vez de un solo destino: */
struct qcom_scm_vmperm dest[2] = {
    { .vmid = QCOM_SCM_VMID_HLOS,    .perm = QCOM_SCM_PERM_RW },  /* AP puede leer */
    { .vmid = QCOM_SCM_VMID_MSS_MSA, .perm = QCOM_SCM_PERM_RW },  /* modem escribe */
};
srcvm = BIT(QCOM_SCM_VMID_HLOS);
ret = qcom_scm_assign_mem(base, size, &srcvm, dest, 2);
```

> **INFERENCE (alta confianza):** dual-owner HLOS+MSS_MSA es el patrón estándar para SMEM/memshare
> compartida y es lo que permite que el AP lea el buffer que el modem llena. Downstream, memshare
> asigna con permisos que dejan al AP acceso de lectura. **UNKNOWN (verificar en vivo):** si el XPU
> de este SoC acepta el shared-ownership con MSS_MSA específicamente (algunos setups exigen
> `VMID_MSS_MSA` exclusivo para MSA "modem self-authenticated area"). Plan B si TZ rechaza dual:
> usar un VMID compartido no-MSA, o mapear la región vía el nodo memshare del smem (SMEM item) en
> vez de carveout crudo. Probar `qcom_scm_assign_mem` dual y ver si retorna 0.

**Con `no-map` (patch 0120):** OJO — una región `no-map` **no está en el linear map de HLOS**, así
que aunque el XPU permita a HLOS el acceso, el AP necesita **mapearla explícitamente**
(`memremap`/`ioremap` en un char device, o `/dev/mem` que en no-map RAM funciona con
`ioremap`-style). No se puede leer por el linear map porque no-map lo excluye. Con `alloc=1` (CMA)
sí hay alias lineal, pero es cacheable (coherencia manual necesaria). **La forma limpia y única para
ambos modos es un char device del módulo que exponga la región (§4.2).**

### 4.2 ⛔ FALTA: el char device / mecanismo de lectura desde el AP
**Esta es la única pieza del OS que NO existe en el repo.** Hoy `rhodep_memassign` sólo publica
`base`/`size` en sysfs (números), pero **no da forma de LEER los bytes**. Opciones:

| Opción | Cómo | Pros / contras |
|---|---|---|
| **A. char device en rhodep_memassign** (recomendado) | agregar `/dev/rhodep_memshare` con `.mmap` (`remap_pfn_range` de la phys, `pgprot_writecombine` o non-cached) y/o `.read` (`memremap` + copy_to_user) | Único punto, respeta no-map, controla coherencia. **FALTA escribir.** |
| B. `/dev/mem` + mmap de la phys desde userspace | userspace mmapea `base` de sysfs | Requiere `CONFIG_DEVMEM` + `CONFIG_STRICT_DEVMEM` permisivo; no-map RAM puede estar bloqueada por STRICT_DEVMEM. Frágil. |
| C. dma-buf exportado por el módulo | exporta un dma-buf de las páginas | Más código; útil si se quiere zero-copy a otro subsistema. Overkill para volcar a fichero. |

**Recomendación: Opción A.** Extender `rhodep_memassign.c` con un misc/char device que haga `mmap`
por `remap_pfn_range(vma, ..., base>>PAGE_SHIFT, size, prot)` con prot **non-cached / write-combine**
(la región es DDR compartida con el modem; leer cacheado da datos rancios). Para no-map, `mmap` de
la phys es la vía correcta (no hay struct page usable en no-map). Para `alloc=1` (CMA) hay struct
page; igual sirve `remap_pfn_range` con coherencia manual (`dma_sync_single_for_cpu` antes de leer).

**Coherencia de caché al leer (INFERENCE):** el modem escribe por su lado; el AP debe **invalidar**
antes de leer (o mapear non-cached). En `alloc=1` el módulo ya limpia antes del assign; para lectura
post-captura el lector debe `dma_sync_single_for_cpu(DMA_FROM_DEVICE)` o mapear WC/non-cached.

---

## 5. COORDINACIÓN IQ_CAPTURE ↔ memshare: ¿cómo sabe el AP offset/size?

**FACT (contrato del firmware, map_iq_capture.md §7.3):** tras el FETCH, el modem reporta
`{name, size_bytes, address}` en el **REPACK de la respuesta DIAG** del IQ_CAPTURE
(fmt `[%2d][%3d][ %12s ][ %4d ][ 0x%8x ]` @0xc37c0949). El `0x%8x` es la dirección (física, dentro
de la región memshare) y `%4d` el tamaño en bytes de esa propiedad. El AP calcula el offset como:
```
offset_en_region = address_REPACK − region_phys_base   (region_phys_base = 0x8ab00000)
puntero_AP       = mmap_base + offset_en_region
leer             = size_bytes
```

### 5.1 ⛔ El problema de re-cableo DIAG que menciona el prompt
El REPACK vuelve por la **respuesta de comando DIAG**, que pasa por el gate
`diagpkt_rsp_send @0xc0d36c44` (exige DIAGID bit0 + FEATURE mask). **FACT (MASTER §3):** los
F3/log/event NO pasan por ese gate y drenan siempre; las **respuestas de comando SÍ** requieren el
handshake completo. Entonces:

- **Si el peer DIAG está bien establecido** (FEATURE + DIAGID + TX-MODE, MASTER §3 "ser el peer"),
  la respuesta REPACK **sí vuelve** y el AP lee address+size de ahí. **Ésta es la vía primaria.**
- **Si la respuesta de comando NO vuelve** (handshake incompleto, que es "nuestro problema de
  re-cableo diag"): hay que obtener address/size por otra vía. Opciones:

  **(i) INFERENCE fuerte — el AP ya conoce la base y la talla:** el daemon **entregó** la región
  (base 0x8ab00000, 5 MiB) en el MEM_ALLOC_GENERIC. Si se hace un IQ_CAPTURE **ACQUIRE de N
  muestras conocido** con formato conocido (8/16-bit), el AP sabe `size = N * bytes_per_sample` y
  el buffer arranca en la **base de la región** (el modem usa `p_sample_capture_buffer` = la región
  memshare del client-id 1). Entonces el AP puede leer `[base, base + N*bps)` sin depender del
  REPACK. **UNKNOWN:** si el modem pone el buffer exactamente en la base o a un offset fijo dentro
  de la región (header/TTI-wp buffer delante). Verificar en vivo volcando la región tras un ACQUIRE
  con patrón conocido.

  **(ii) El TTI-wp buffer / metadatos:** la struct de captura tiene `p_tti_wp_capture_buffer`
  (timing). No ayuda a ubicar las muestras por sí solo. UNKNOWN si hay un header de layout al inicio
  de la región.

  **(iii) Escaneo:** volcar los 5 MiB y buscar el patrón (para bring-up/validación con tono
  conocido). Viable como diagnóstico, no como mecanismo de producción.

**Conclusión (§5):** la vía correcta es **arreglar el peer DIAG** para que la respuesta REPACK
vuelva (todo el contrato está en MASTER §3 y es trabajo de userspace DIAG, no del path memshare).
Como respaldo, para capturas ACQUIRE de N conocido, el AP puede leer desde la base con
`size=N*bps`. El path memshare (este documento) entrega la memoria; **saber el offset exacto de una
captura arbitraria depende del REPACK DIAG**, que es un subsistema separado.

---

## 6. LISTA COMPLETA DE PIEZAS OS — ESTADO Y QUÉ ESCRIBIR

| # | Pieza | Estado | Ubicación / qué falta |
|---|---|---|---|
| 1 | **DT: reserva no-map 0x8ab00000/0x800000** | **ESCRITO, NO APLICADO** | `kernel/patches/0120-...patch`. Correcto y válido (§2). **FALTA: agregarlo al `source=` del `kernel/APKBUILD`** (hoy salta 0119/0120/0121). Rebuild + reflash boot. |
| 2 | **Módulo hyp_assign (rhodep_memassign.ko)** | **ESCRITO, con BUG** | `kernel/diag-modules/rhodep_memassign.c`. Compila/linkea contra 7.2-rc5 (§1). **FALTA: asignar a AMBOS VMIDs (HLOS+MSS_MSA)** para que el AP pueda leer (§4.1). Corregir comentario "40 MiB CMA" (es 32). |
| 3 | **Char device / lector desde el AP** | **NO EXISTE — FALTA ESCRIBIR** | Pieza clave (§4.2). Extender rhodep_memassign con `/dev/rhodep_memshare` (mmap `remap_pfn_range` non-cached/WC, opcional `.read`). Alternativa frágil: /dev/mem. |
| 4 | **Daemon QMI 52 (memshare-daemon)** | **ESCRITO, correcto** | `userspace/modem/memshare-daemon.c`. build_alloc_response OK; guard reserved/module OK. **Para entregar 5 MiB necesita** región presente (item 1 o `alloc=1`) y arrancar con el drop-in de 5 MiB (hoy default = 0). |
| 5 | **systemd service del daemon** | **ESCRITO, incompleto** | `userspace/modem/systemd/rhodep-memshare.service`. Orden qrtr→antes-de-rmtfs OK. **FALTA: `ExecStartPre=/sbin/modprobe rhodep_memassign` (modo alloc=1) y/o dependencia de que el módulo esté cargado con assigned=1** antes de ofrecer 5 MiB (§3.2). |
| 6 | **Drop-in "ofrecer 5 MiB"** | **ESCRITO, .disabled** | `install.sh` ship `...service.d/20-offer-5mib.conf.disabled`. **FALTA: activarlo** (copiar a /etc como .conf) una vez la región exista. |
| 7 | **modules-load / build del módulo** | **PARCIAL** | Makefile compila en el teléfono. **FALTA: empaquetar rhodep_memassign.ko en el rootfs / modules-load.d, o el ExecStartPre del item 5**, para carga automática al boot. |
| 8 | **Reinicio del modem para re-query** | **NO AUTOMATIZADO** | Si el modem booteó sin memshare, hay que reiniciar MSS (remoteproc stop/start) tras tener daemon+módulo listos (§3.3). Script/orden de arranque que garantice daemon+módulo ANTES del primer bring-up MSS evita esto. |
| 9 | **Peer DIAG (para el REPACK / address+size)** | **FUERA DE ESTE PATH** | Necesario para leer address+size de una captura arbitraria (§5). Contrato completo en MASTER_MODEM_MAP.md §3. Es trabajo DIAG/QRTR de userspace, separado del path memshare. |
| 10 | **Patch 0121 (pmr735a L1 on)** | **ESCRITO, NO APLICADO** | `kernel/patches/0121-...`. Mantiene un rail del PMIC para el modem. No es parte estricta del path IQ/memshare, pero está en el mismo hueco de patches no integrados; revisar si el modem lo necesita. |

### 6.1 Ruta mínima para cerrar el path IQ (orden de trabajo)
1. **Aplicar 0120** al APKBUILD (`source=` + checksum), rebuild kernel/boot, reflash. → DT reserva
   la región no-map. (Evita el riesgo especulativo de `alloc=1`.)
2. **Corregir rhodep_memassign** para dual-VMID (HLOS+MSS_MSA) — item 2 (§4.1).
3. **Escribir el char device** `/dev/rhodep_memshare` (mmap non-cached) — item 3 (§4.2). *(Con el
   DT no-map, el módulo carga con la reserva del DT — no `alloc=1` — hace el hyp_assign dual, y
   expone el char device.)*
4. **Ajustar el service**: cargar el módulo antes del daemon; activar el drop-in de 5 MiB — items
   5/6/7 (§3.2).
5. **Garantizar orden de boot**: daemon+módulo listos antes del bring-up MSS, o reiniciar MSS una
   vez — item 8 (§3.3).
6. **Arreglar el peer DIAG** para recuperar address+size del REPACK (o usar la vía "N conocido desde
   la base") — items 9 / §5.

---

## 7. FACT / INFERENCE / UNKNOWN — resumen honesto

**FACT (verificado en fuente/repo/DT de este sistema):**
- `qcom_scm_assign_mem` y `qcom_scm_is_available` son EXPORT_SYMBOL_GPL en 7.2-rc5; VMIDs HLOS=0x3,
  MSS_MSA=0xF, PERM_RW=0x6, struct qcom_scm_vmperm, dma_alloc_pages exportado. El módulo compila.
- .config: QCOM_SCM=y, CMA=y, DMA_CMA=y, **CMA_SIZE_MBYTES=32** (no 40), MAX_ORDER=10,
  MODULE_ALLOW_BTF_MISMATCH=y.
- 0x8ab00000+0x800000 cae en el hueco libre 0x8aa1c000..0x8b800000 del reserved-memory (sm6375.dtsi);
  1 MiB aligned; no pisa pil-*. no-map es correcto y necesario.
- **Patches 0119/0120/0121 NO están en el `source=` del APKBUILD** → DT vivo sin la reserva.
- El módulo asigna a un solo VMID (MSS_MSA) → **HLOS pierde acceso** tras el assign.
- El daemon, service, drop-in de 5 MiB y guards existen y son correctos en su lógica.
- El modem manda MEM_QUERY_SIZE client_id=1 una vez en bring-up; con 0 no sigue, con 5 MiB pide
  MEM_ALLOC_GENERIC.
- El REPACK DIAG del FETCH lleva {name, size, address}; vuelve por la respuesta de comando (gate
  DIAGID+FEATURE), no por F3.

**INFERENCE (base dura):**
- Dual-owner HLOS+MSS_MSA es el patrón para leer el buffer desde el AP (patrón memshare/SMEM).
- Char device con mmap non-cached (`remap_pfn_range`) es la vía limpia para leer no-map desde AP.
- El modem no re-pregunta QUERY_SIZE espontáneamente; hay que reiniciar MSS.
- Para un ACQUIRE de N muestras conocido, el AP puede leer desde la base sin el REPACK.

**UNKNOWN (requiere equipo en vivo):**
- Si el XPU de este SoC acepta ownership compartido HLOS+MSS_MSA (vs MSA exclusivo). Probar el
  assign dual y ver el retorno de TZ.
- Si el buffer de muestras arranca exactamente en la base de la región o a un offset fijo
  (header/TTI-wp delante). Volcar tras ACQUIRE con patrón conocido.
- Si hay re-query de memshare gatillado por algún comando FTM.
- Los enums IQ_DATA_FORMAT/SAMP_FREQ, el SET_MODE cal, los command_id — bloqueadores del lado
  firmware/DIAG, no del OS (MASTER §12).

---

## 8. Reproducir las verificaciones de este documento
```
SRC=/home/pmos/.local/var/pmbootstrap/chroot_native/tmp/hdrfix/linux-7.2-rc5
# exports y VMIDs
grep -n "EXPORT_SYMBOL.*assign_mem\|EXPORT_SYMBOL.*is_available" $SRC/drivers/firmware/qcom/qcom_scm.c
grep -n "QCOM_SCM_VMID_HLOS\|QCOM_SCM_VMID_MSS_MSA" $SRC/include/dt-bindings/firmware/qcom,scm.h
grep -n "QCOM_SCM_PERM_RW" $SRC/include/linux/firmware/qcom/qcom_scm.h
# mapa de memoria
grep -nE "reg = <0 0x8[a-f]" $SRC/arch/arm64/boot/dts/qcom/sm6375.dtsi
# patch NO aplicado
for p in 0119 0120 0121; do echo -n "$p: "; grep -c "$p-" /opt/postmarket/nethunter-rhodep-repo/kernel/APKBUILD; done
# .config
grep -E "CMA_SIZE_MBYTES|ARCH_FORCE_MAX_ORDER|QCOM_SCM=" \
  /home/pmos/.local/var/pmbootstrap/chroot_native/tmp/khdr/usr/src/linux-headers-7.2.0-rc5/.config
```
