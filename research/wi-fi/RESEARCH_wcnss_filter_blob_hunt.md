# Hunt for `wcnss_filter` (rhodep / SM6375) + viabilidad de desensamblado

Fecha: 2026-09-17
Objetivo: encontrar el blob `wcnss_filter` (ARM64) del Moto G82 5G (rhodep,
SM6375) para desensamblarlo y extraer la secuencia de activación del canal FM
del WCN3990; o bien una fuente de referencia (btsnoop / kernel downstream).

Método: descarga y análisis ESTÁTICO REAL de los blobs del vendor tree de rhodep
(no especulación). Binarios verificados con `file`/`readelf`/`aarch64-objdump`
en /tmp/opencode/rhodep_blobs/.

---

## 0. TL;DR — el hallazgo que cambia todo

**El Moto G82 (rhodep) NO tiene `wcnss_filter`.** Descargué el `vendor/bin`
completo del vendor tree de rhodep: no existe `wcnss_filter`, `wcnss_service`,
`hci_qcomm_init` ni ningún mux de UART FM. La premisa "la secuencia está dentro
de /vendor/bin/wcnss_filter" **es falsa para este device**.

En su lugar, rhodep usa el stack **HIDL moderno + driver V4L2 de kernel**:

1. **BT** lo maneja `vendor/bin/hw/android.hardware.bluetooth@1.0-service-qti`
   (+ `android.hardware.bluetooth@1.0-impl-qti.so`) — reemplaza a wcnss_filter.
2. **FM** lo maneja `vendor/lib64/hw/vendor.qti.hardware.fm@1.0-impl.so`, cuya
   clase interna es **`FmIoctlHal`**. Desensamblado (abajo): **NO habla por UART
   ni por `fm_sock`**. Abre **`/dev/radio0`** y maneja FM por **ioctls V4L2**
   contra un driver de kernel `radio-iris` + módulo `radio_iris_transport`.

**Consecuencia práctica gigante:** la "secuencia de activación FM" que buscabas
**no es un handshake propietario oculto en un blob de userspace**. En rhodep la
activación es **un solo ioctl V4L2**:

```
VIDIOC_S_CTRL, id = V4L2_CID_PRIVATE_IRIS_STATE (0x08000004), value = 1 (FM_RECV)
```

…contra `/dev/radio0`. Todo el handshake FM-HCI con el WCN3990 lo hace el driver
de kernel `radio-iris`, cuyo **código fuente ES open source** (CLO/CAF). No hay
blob que desensamblar para la "secuencia": la secuencia vive en un driver GPL.

Esto **corrige** la conclusión de RESEARCH_wcnss_filter_fm_activation.md (que
asumía el modelo wcnss_filter/fm_sock/UART-mux). Ese modelo es real en algunos
devices Cherokee, pero **rhodep no es uno de ellos**: usa el modelo iris/V4L2.

---

## 1. Pregunta 1 — vendor.img / firmware stock del G82 (rhodep, XT2225)

### 1.1 Firmware stock Motorola (lolinet)
- Motorola SÍ publica firmware stock, pero lolinet organiza por **codename**, no
  por modelo comercial, y bajo `lenomola`, no `motorola`. El índice raíz vivo es:
  - `https://mirrors.lolinet.com/firmware/lenomola/` (por año: 2022/, 2023/, …)
  - El path histórico `mirrors.lolinet.com/firmware/motorola/<codename>/` da 404
    hoy; usar la estructura `lenomola/<año>/<codename>/`.
- Para rhodep, buscar bajo `lenomola/2022/rhodep/` (año de lanzamiento del G82).
  El firmware Motorola viene como ZIP de RETAIL/carrier con este naming típico:
  `RHODEP_<canal>_<version>_<fecha>_...subsidy-DEFAULT_...ZIP` que contiene
  `super.img_sparsechunk.*` (super = system+vendor+product+odm) + `boot.img` +
  `radio` (NON-HLOS/modem) + `fsg`, etc.
- **Para extraer `/vendor/bin/*` del stock:** el ZIP trae `super.img` (sparse).
  Flujo: `simg2img super.img_sparsechunk.* super.raw` → `lpunpack super.raw` →
  monta `vendor.img` → copia `vendor/lib64/hw/vendor.qti.hardware.fm@1.0-impl.so`.
  Pero **no hace falta** (ver §2: ya está en GitHub, descarga directa).

Otros mirrors de firmware Motorola que suelen tener rhodep/XT2225:
- `https://mirrors.lolinet.com/firmware/lenomola/`  (oficial-ish, el más fiable)
- foros: XDA "Moto G82 firmware" hilos con enlaces a lolinet/mega.
- Servicios de firmware (mirror-o-matic): buscar "XT2225-1 firmware rhodep".

Nota: el nombre exacto del archivo cambia por región/canal; el codename en el
paquete es **RHODEP** y el modelo **XT2225-1 / XT2225-2**.

### 1.2 ¿Custom ROM/LineageOS de rhodep con el blob?
Sí, y es la vía fácil (§2). Los device trees de rhodep referencian los blobs
desde repos "proprietary_vendor" que **contienen los .so directamente**.

---

## 2. Pregunta 2 — repos GitHub con los blobs de rhodep (URLs concretas)

Búsqueda vía GitHub API (`/search/repositories?q=vendor motorola rhodep`),
9 repos. Los relevantes, verificados:

| Repo | Branch | Contiene |
|---|---|---|
| `TheMuppets/proprietary_vendor_motorola_rhodep` | `lineage-23.2` | tree canónico LOS (parcial en esa branch) |
| **`zetas-lab/vendor_motorola_rhodep`** | `lineage-21` | **tree COMPLETO con los blobs FM/BT** ✅ |
| `Keret-Playground/vendor_motorola_rhodep` | — | unified G82 |
| `sevenrock/proprietary_vendor_motorola_rhodep` | HEAD | libfm-hci + fm@1.0 |
| `moto-common/android_vendor_motorola_rhodep` | `14` | parcial |
| `Beregina-bengal/android_vendor_motorola_rhodep` | HEAD | full (3370 paths) |

**No existe `wcnss_filter` en NINGUNO** (grep sobre el tree completo = 0).
Lo que SÍ existe (todo descargable como raw, sin LFS, sin auth), base:
`https://raw.githubusercontent.com/zetas-lab/vendor_motorola_rhodep/lineage-21/proprietary/`

Blobs FM/BT clave (con tamaños reales medidos):
```
vendor/bin/hw/android.hardware.bluetooth@1.0-service-qti        20488  B  (ELF aarch64, stripped)
vendor/lib64/hw/android.hardware.bluetooth@1.0-impl-qti.so     549592  B
vendor/lib64/hw/vendor.qti.hardware.fm@1.0-impl.so             83384  B  ← TARGET FM (FmIoctlHal)
vendor/lib64/vendor.qti.hardware.fm@1.0.so                    145344  B  (proxy HIDL autogenerado)
system_ext/lib64/fm_helium.so                                  49392  B  (helium HAL, símbolos C)
system_ext/lib64/libfm-hci.so                                  45952  B  (cliente HIDL fino)
```

Descarga directa (ejemplo del target):
```
curl -LO https://raw.githubusercontent.com/zetas-lab/vendor_motorola_rhodep/lineage-21/proprietary/vendor/lib64/hw/vendor.qti.hardware.fm@1.0-impl.so
```

Otros SM6375 con blobs equivalentes (por si querés comparar):
`bengal`/`khaje` device trees (moto g stylus 5g milanf, etc.) publican los mismos
`vendor.qti.hardware.fm@1.0-impl.so` en sus `proprietary_vendor_*` — misma
familia CLO, mismo `FmIoctlHal`.

---

## 3. Pregunta 3 — viabilidad del desensamblado (HECHO, no estimado)

Ya lo desensamblé. Resumen fáctico:

### 3.1 Naturaleza de los binarios
- `file`: **ELF 64-bit LSB shared object, ARM aarch64, dynamically linked,
  stripped**. TODOS los blobs FM/BT son aarch64.
- **Stripped** = sin símbolos locales/debug, PERO al ser `.so` conservan la
  **tabla de símbolos dinámicos** (exportados + importados). En C++ HIDL los
  símbolos exportados están **mangleados y son riquísimos**: te dan la clase, el
  método y la firma. Ej. reales extraídos:
  ```
  FmIoctlHal::FmTurnOn()
  FmIoctlHal::fmDeviceNodeInit()
  FmIoctlHal::fmEnableEvent()
  FmIoctlHal::FmSetConfiguration()
  FmIoctlHal::sendHciCommand(hidl_vec<unsigned char> const&)
  FmIoctlsInterface::set_control(unsigned int, unsigned int, int)
  FmIoctlsInterface::set_ext_control(unsigned int, v4l2_ext_controls*)
  ```
  `fm_helium.so` va más allá: exporta símbolos **C sin manglear** (`hci_fm_enable_recv_req`,
  `hci_fm_tune_station_req`, `hci_fm_enable_slimbus`, `helium_set_antenna_req`…),
  porque es literalmente el `radio_helium_hal*.c` open source compilado.

### 3.2 Tamaño / dificultad
- Target FM (`vendor.qti.hardware.fm@1.0-impl.so`) = **~83 KB**. Pequeño.
- Herramientas: **`aarch64-linux-gnu-objdump -d` basta**; **Ghidra** ideal para
  seguir C++ (vtables HIDL, structs). Dificultad: **BAJA**. El código es un
  wrapper delgado sobre ioctls; no hay ofuscación, no hay packing.

### 3.3 Qué encontré desensamblando (la secuencia real)
`FmIoctlHal::fmDeviceNodeInit()` (cuerpo en 0xc908):
```
open("/dev/radio0", ...)                       ; abre el nodo V4L2
ioctl(fd, 0x80685600, &cap)                    ; VIDIOC_QUERYCAP (_IOR('V',0,v4l2_capability))
```
`FmIoctlHal::FmTurnOn()` (cuerpo en 0xd780):
```
FmIoctlsInterface::set_control(fd, 0x08000004, 1)   ; ← ACTIVACIÓN FM
    ; id = V4L2_CID_PRIVATE_BASE(0x08000000)+4 = V4L2_CID_PRIVATE_IRIS_STATE
    ; value 1 = FM_RECV (encender receptor)
pthread_create(fmReaderThread)                       ; hilo lector de eventos
property_get("persist.vendor.qcom.bluetooth.soc")    ; detecta SoC (cherokee/…)
[opcional] FmIoctlsInterface::set_calibration(fd)    ; Riva cal
```
Strings corroborantes en el blob:
```
/dev/radio0
/sys/module/radio_iris_transport/parameters/fmsmd_set
"inserting the radio transport module"
"failed to intialize radio device node"
persist.vendor.qcom.bluetooth.soc
/data/vendor/fm/Riva_fm_cal
```

**Conclusión pregunta 3:** el desensamblado es trivialmente viable Y YA REVELÓ
la secuencia. Pero la revelación es que **la activación NO es un frame 0x11 que
haya que anteponer, ni un vendor HCI 0xFC** por packet 0x01**: es un **ioctl
V4L2 `S_CTRL(IRIS_STATE=FM_RECV)`** contra un driver de kernel. El HAL nunca
toca el UART; se lo delega íntegro al driver `radio-iris`.

---

## 4. Pregunta 4 — fuentes de la secuencia (kernel downstream / btsnoop)

Como la activación vive en el **driver de kernel `radio-iris`** (GPL, open
source), NO necesitás btsnoop ni el blob para conocerla. Fuentes de la secuencia:

### 4.1 El driver de kernel `radio-iris` (donde ocurre el handshake real)
En árboles CLO/CAF SM6375 el FM NO está en `drivers/media/radio/` del kernel
principal (lo confirmé: los kernels Motorola SM6375 —`Motorola-SM6375-Devs`,
`LineageOS/android_kernel_motorola_sm6375`— sólo traen tuners legacy + `rtc6226`
en esa ruta). El `radio-iris.c`/`radio-iris-transport.c` se distribuye como
**módulo aparte** (techpack/vendor kernel module) que produce el `.ko`
`radio_iris_transport` que el HAL inserta. Copias canónicas del fuente:
- `github.com/LineageOS/android_kernel_google_msm-4.9` (branch `lineage-19.1`):
  `drivers/media/radio/radio-iris.c`, `radio-iris-transport.c`,
  `include/media/radio-iris.h`, `include/uapi/media/radio-iris*.h`
  (ya usado en RESEARCH_wcn3990_fm.md — tiene TODA la máquina de estados FM-HCI).
- Userspace de referencia (helium, ya presente como blob y como fuente):
  `github.com/LineageOS/android_vendor_qcom_opensource_fm-commonsys`
  `git.codelinaro.org/clo/la/platform/vendor/qcom-opensource/fm`

Lo que hace `radio-iris` al recibir `S_CTRL(IRIS_STATE=FM_RECV)`: arma y envía la
secuencia FM-HCI de encendido (ENABLE_RECV → default-data/cal → SET_RECV_CONF →
SET_ANTENNA …) por su transport, y bombea los eventos 0x14 de vuelta. **Esa es la
"secuencia de activación" completa, en C GPL, legible.**

### 4.2 ¿Repo MotorolaMobilityLLC con kernel rhodep/FM?
- `github.com/MotorolaMobilityLLC` publica kernels por SoC. Para SM6375 los
  mirrors activos con más contenido son `Motorola-SM6375-Devs/android_kernel_motorola_sm6375`
  (branch `sixteen`) y `LineageOS/android_kernel_motorola_sm6375`.
- Verificado: **ninguno trae `radio-iris`/`radio-helium` en `drivers/media/radio`**
  (sólo `rtc6226` discreto + tuners legacy). El módulo FM iris llega por techpack
  separado. `btfm_slim*` (audio SLIMbus) sí puede estar en `drivers/bluetooth`.
- Por tanto, para el CÓDIGO de la secuencia, usá el `radio-iris.c` de msm-4.9
  (§4.1); para el ENGANCHE exacto de este device (nodo, módulo, cal path) usá el
  desensamblado de `FmIoctlHal` (§3.3), que ya te lo dio.

### 4.3 btsnoop de referencia
No necesario (tenés el fuente del driver). Si igual querés validación dinámica:
un btsnoop clásico NO capturaría esto, porque el FM va por `/dev/radio0` (V4L2),
no por hci0. La captura útil sería un **strace del proceso HAL** mostrando
`ioctl(fd, VIDIOC_S_CTRL/S_EXT_CTRLS, …)`, o un trace del driver (ftrace sobre
`radio-iris`). Pero el fuente ya lo hace innecesario.

---

## 5. Pregunta 5 — libfm-hci + HIDL: ¿cuál blob es el correcto?

Ya identificado y desensamblado. Jerarquía real en rhodep:
```
app/framework
   │  V4L2 (/dev/radioN)  ó  JNI
   ▼
[HIDL server]  vendor.qti.hardware.fm@1.0-impl.so   =  clase FmIoctlHal   ← ESTE
   │   FmTurnOn/FmTune/... => FmIoctlsInterface::set_control/set_ext_control
   ▼   ioctl() a
/dev/radio0   ── driver de kernel radio-iris  (open source, GPL)
   │  FM-HCI (0x11/0x14) por su transport interno
   ▼
WCN3990 firmware
```
- `libfm-hci.so` (system_ext): cliente HIDL fino. Sólo `fm_hci_init` →
  `IFmHci::getService` → `fm_hci_transmit`. **No contiene la activación.** (Es la
  ruta que usa el path fm_hci.cpp/helium; en rhodep coexiste pero el camino que
  ABRE el device y ACTIVA es `FmIoctlHal` vía V4L2.)
- `vendor.qti.hardware.fm@1.0.so`: proxy/stub HIDL autogenerado (BnHwFmHci/
  BpHwFmHci). No lógica.
- **`vendor.qti.hardware.fm@1.0-impl.so` = el blob correcto** si querés
  desensamblar. Pero (importante) **su lógica de activación es sólo el ioctl
  `IRIS_STATE`**; el trabajo pesado está en el driver de kernel GPL, no en él.

---

## 6. Respuestas directas a lo que pediste

**¿Dónde consigo el blob wcnss_filter de rhodep?**
No existe en rhodep. Lo que buscás (la activación FM) NO está en un blob de
userspace en este device; está en el driver de kernel `radio-iris` (GPL). El
blob HIDL equivalente (`vendor.qti.hardware.fm@1.0-impl.so`) sí lo tenés en:
`https://raw.githubusercontent.com/zetas-lab/vendor_motorola_rhodep/lineage-21/proprietary/vendor/lib64/hw/vendor.qti.hardware.fm@1.0-impl.so`

**¿El firmware Motorola es descargable?**
Sí: `https://mirrors.lolinet.com/firmware/lenomola/` (por año→codename `rhodep`).
Pero no hace falta para el objetivo; los blobs ya están en GitHub raw.

**¿Es viable el desensamblado?**
Sí, trivial. ELF aarch64 stripped de ~83 KB, con símbolos dinámicos C++
mangleados muy informativos. Herramienta: `aarch64-linux-gnu-objdump -d` o Ghidra.
Ya lo hice: la activación es `ioctl(VIDIOC_S_CTRL, IRIS_STATE=FM_RECV)`.

**¿Qué buscábamos y qué encontramos?**
Buscábamos "qué antepone al 0x11 / qué comando previo manda". Respuesta: en
rhodep **el HAL no antepone nada ni manda 0xFC** por 0x01**. Delega a
`/dev/radio0`. El 0x11 y toda la secuencia de encendido los genera el driver de
kernel `radio-iris` (open source), no el blob.

**Fuente de la secuencia (sin blob):**
`radio-iris.c` + `radio-iris-transport.c` +
`include/uapi/media/radio-iris*.h` de
`github.com/LineageOS/android_kernel_google_msm-4.9@lineage-19.1`.

---

## 7. Implicación para tu port mainline (lo accionable)

Tu problema real deja de ser "reversear un blob". Se convierte en **portar el
driver `radio-iris` (+ su transport) a tu kernel mainline rhodep**, que es
código GPL disponible. Dos sub-tareas:

1. **radio-iris.c**: la máquina de estados FM-HCI (genera ENABLE_RECV, conf,
   antenna, tune, y parsea 0x14). Portable casi tal cual; expone `/dev/radioN`.
2. **transport**: reemplazar el `radio-iris-transport` (SMD, era riva) por un
   shim que meta los frames `0x11` en el UART del WCN3990 y lea `0x14`. Éste es
   el único trabajo nuevo, y coincide EXACTA­mente con el inyector 0x11 que ya
   tenés. Es decir: ya construiste medio transport; falta acoplarlo bajo
   radio-iris para que el driver haga la secuencia por vos.

Duda abierta que el desensamblado NO resuelve (igual que antes): si el `crnv21`
de rhodep trae FM deshabilitado en NVM, el firmware ignorará la secuencia aunque
la mandes perfecta (ver RESEARCH_wcnss_filter_fm_activation.md §5.2). Eso se
prueba flasheando un crnv "con FM" y viendo si aparece el 0x14.

---

## 8. Comandos de reproducción (todo verificado)

```bash
# Descargar el target y compañía
B=https://raw.githubusercontent.com/zetas-lab/vendor_motorola_rhodep/lineage-21/proprietary
curl -LO $B/vendor/lib64/hw/vendor.qti.hardware.fm@1.0-impl.so
curl -LO $B/system_ext/lib64/fm_helium.so

# Confirmar arquitectura
file vendor.qti.hardware.fm@1.0-impl.so   # ELF aarch64, stripped

# Símbolos (mangleados C++) => arquitectura FmIoctlHal
readelf -sW vendor.qti.hardware.fm@1.0-impl.so | c++filt | grep FmIoctl

# Strings clave
strings -a vendor.qti.hardware.fm@1.0-impl.so | grep -E '/dev/radio|radio_iris_transport|bluetooth.soc'

# Desensamblar la activación
aarch64-linux-gnu-objdump -d vendor.qti.hardware.fm@1.0-impl.so \
  | sed -n '/FmTurnOn/,/ret/p'      # (o abrir en Ghidra y ver set_control(0x08000004,1))
```

---

## 9. Enlaces (todos concretos)

Blobs rhodep (raw, descarga directa):
- https://raw.githubusercontent.com/zetas-lab/vendor_motorola_rhodep/lineage-21/proprietary/vendor/lib64/hw/vendor.qti.hardware.fm@1.0-impl.so
- https://raw.githubusercontent.com/zetas-lab/vendor_motorola_rhodep/lineage-21/proprietary/system_ext/lib64/fm_helium.so
- https://raw.githubusercontent.com/zetas-lab/vendor_motorola_rhodep/lineage-21/proprietary/system_ext/lib64/libfm-hci.so
- https://raw.githubusercontent.com/zetas-lab/vendor_motorola_rhodep/lineage-21/proprietary/vendor/bin/hw/android.hardware.bluetooth@1.0-service-qti

Repos vendor rhodep:
- https://github.com/zetas-lab/vendor_motorola_rhodep (branch lineage-21) ← completo
- https://github.com/TheMuppets/proprietary_vendor_motorola_rhodep (lineage-23.2)
- https://github.com/Beregina-bengal/android_vendor_motorola_rhodep
- https://github.com/sevenrock/proprietary_vendor_motorola_rhodep

Fuente kernel de la secuencia (radio-iris, GPL):
- https://github.com/LineageOS/android_kernel_google_msm-4.9 (lineage-19.1)
  drivers/media/radio/radio-iris.c
  drivers/media/radio/radio-iris-transport.c
  include/uapi/media/radio-iris.h  /  radio-iris-commands.h

Kernels Motorola SM6375 (BT/btfm_slim, sin radio-iris en media/radio):
- https://github.com/Motorola-SM6375-Devs/android_kernel_motorola_sm6375 (sixteen)
- https://github.com/LineageOS/android_kernel_motorola_sm6375 (lineage-23.2)

Userspace HAL FM (helium/fm_hci, fuente):
- https://github.com/LineageOS/android_vendor_qcom_opensource_fm-commonsys
- https://git.codelinaro.org/clo/la/platform/vendor/qcom-opensource/fm

Firmware stock Motorola:
- https://mirrors.lolinet.com/firmware/lenomola/   (año → codename rhodep, XT2225)
