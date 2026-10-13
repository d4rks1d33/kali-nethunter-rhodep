# Fixes pendientes de aplicar (notas de sesión)

> Documento de trabajo para no re-investigar. Lista de cambios **confirmados
> funcionando en RAM** durante la sesión pero que todavía **no son persistentes**.
> Aplicar todo junto al final (un solo rebuild + reflash del boot.img, o rebuild
> de kernel si hace falta).

Dispositivo: Motorola Moto G82 5G (rhodep, SM6375, panel Novatek NT37701, DSC 1.1
command mode). Kernel `7.2.0-rc5` mainline port (`linux-image-7.2.0-rc5` pkg
`rhodep7`).

Acceso: `ssh kali@172.16.42.1` (USB) o `ssh kali@192.168.1.53` (WiFi), pass `1234`
(root también `1234`). `sudo` pide la misma pass.

---

## FIX #1 — Línea de color fija en el panel (DSC desync por PPS doble)  [FIX INSUFICIENTE — ver actualización]

> **UPDATE 2026-10-13:** Se aplicó `send_pps=N` persistente vía cmdline (confirmado en
> `/proc/cmdline` y módulo). **La línea fija sigue apareciendo igual.** El PPS doble
> NO era la causa raíz (o no la única). Diagnóstico nuevo abajo.

### Hallazgo nuevo: `dsi_err_worker: status=45` (no 44)
En el boot actual, además del `status=44` conocido (1 vez al boot), aparecen **2 ocurrencias
de `status=45`** cuando se reinicia GDM. Decodificación bit a bit:
- `0x45 = 0x40 | 0x04 | 0x01`
- `0x40` = `DSI_ERR_STATE_LANE_FIFO_UNDERFLOW` (conocido)
- `0x04` = `DSI_ERR_STATE_FIFO`
- **`0x01` = `DSI_ERR_STATE_TIMEOUT`** ← bit NUEVO que no veíamos antes

Fuente: `drivers/gpu/drm/msm/dsi/dsi_host.c` upstream mainline:
```c
#define DSI_ERR_STATE_ACK          0x0000
#define DSI_ERR_STATE_TIMEOUT      0x0001
#define DSI_ERR_STATE_DLN0_PHY     0x0002
#define DSI_ERR_STATE_FIFO         0x0004
```

El `TIMEOUT` significa que un comando DSI al panel (lectura/escritura) **no recibió ACK**
dentro del tiempo esperado. Esto es DIFERENTE del underflow puro — es "pedí algo al panel
y no me respondió", lo cual puede dejar al DSC desincronizado permanentemente de forma
que el modeset del parche 0098 **no recupera** (porque el panel sigue respondiendo pero
con el DSC roto).

Diagnóstico: la línea fija **NO está en el framebuffer** (captura de `/dev/fb0` sale en
blanco, confirmado 2 veces). Se genera abajo del DPU, en el DSI/panel.

### Lo que ya se probó y NO funciona (persistente)
- `send_pps=N` solo (fix original de este FIX): **línea sigue igual**.
- `send_pps=N` + `clk_scale=150`: **línea sigue igual**.
- Reinicio GDM + modeset vía kscreen-doctor (cambio 180Hz→90Hz): **línea sigue igual**.

### Hipótesis actualizadas (ninguna confirmada aún)
1. El bug raíz es el `TIMEOUT` (bit 0x01) que ocurre durante el modeset de recovery,
   dejando el DSC en estado inconsistente que ningún nuevo modeset recupera.
2. Puede ser timing crítico de los PHY timings del nt37701 (que están hardcodeados
   en el DT por refresh rate — ver 04-display.md).
3. Puede ser hardware defect del panel (dead pixel column) — descartable solo probando
   mostrar un patron de prueba de pantalla completa en varios colores.

### Nota del fix persistente ya aplicado
El cmdline tiene `panel_novatek_nt37701.send_pps=N` aplicado y funcionando. Mantenerlo
porque **puede ser necesario** combinado con otros fixes futuros aunque solo no alcance.

---

## FIX #1 ORIGINAL (histórico, documentación de lo probado) — Línea de color fija en el panel (DSC desync por PPS doble)  [CONFIRMADO EN RAM, INSUFICIENTE EN PERSISTENTE]

### Síntoma
Una línea/banda de color fija (verde/violeta/rosa según el frame) cruzando el
panel. Desaparece al bloquear con el botón físico (DPMS off real) y **reaparece**
al encender. Persiste aunque el contenido en pantalla sea negro. Se recupera sola
a veces (gracias al parche 0098 de recuperación ya instalado) pero vuelve.

**Importante:** esto es un bug **distinto** del "glitch al mover el brillo"
(ese sigue sin resolver, ver FIX pendiente más abajo). La línea fija y el glitch
de brillo son dos cosas separadas.

### Causa raíz (confirmada empíricamente esta sesión)
El panel NT37701 **se auto-configura su decodificador DSC** mediante registros
propietarios Novatek (`0x90` / `0x91`, 18 bytes) **embebidos dentro de la
secuencia de power-on** (`nt37701_on()`).

Pero el driver mainline **además** envía por separado un **PPS MIPI estándar de
128 bytes** (`mipi_dsi_picture_parameter_set()`, vía `drm_dsc_pps_payload_pack()`)
en `nt37701_send_dsc_cfg()`. Como el driver mainline **no setea `initial_xmit_delay`**
(lo deja en 0 y DRM lo recalcula), los valores del PPS estándar **difieren** del
bloque `0x91` que el panel ya aplicó → el **decodificador DSC queda
desincronizado** → línea de color persistente.

El propio autor ya lo había sospechado: comentario en
`kernel/patches/0003-drm-panel-add-novatek-nt37701.patch:44-46`:
> *"The panel configures its DSC decoder through its own registers (0x90/0x91)
> during the power on sequence, so the standard PPS and the compression mode
> command may be redundant or even get in the way."*

### Fix
**Desactivar el envío del PPS estándar del host** (`send_pps=0`). Dejar que el
panel use solo su `0x91` propietario.

Probado en RAM:
```sh
echo N > /sys/module/panel_novatek_nt37701/parameters/send_pps
# luego re-init del panel (restart gdm o DPMS cycle)
```
Resultado: **la línea fija NO reapareció** tras forzar el glitch de brillo
(el glitch de brillo seguía, pero ya no "levantaba" la línea fija).

Notas de lo que NO funcionó (ya descartado esta sesión, no reintentar):
- `send_cmode=N` solo → la línea siguió.
- `clk_scale` 100→230 → el autor ya lo había descartado (tinte carmesí, no
  arregla). Revertido a 100.
- `pps_first=Y` → no cambió. Revertido a N.
- `bl_lpm` on/off → sin efecto (ya medido por el autor: 64 underflows con y sin).

El estado final en RAM que quedó funcionando es: **solo `send_pps=N`**, todo lo
demás en default (`clk_scale=100`, `send_cmode=Y`, `pps_first=N`, `bl_lpm=Y`,
`refresh=0`).

### Cómo persistirlo — OPCIÓN A: cmdline (recomendada, sin recompilar kernel)

Mismo mecanismo que ya usa el proyecto para `clk_scale`.

1. Editar el cmdline y agregar el token `panel_novatek_nt37701.send_pps=N`:

   Archivo: `boot-artifacts/v47_cmdline.txt`

   Actual:
   ```
   earlycon pmos_root_uuid=d3f420ff-55cd-4e90-aba9-adf089dcf1e4 pmos_rootfsopts=defaults panel_novatek_nt37701.clk_scale=100 ignore_loglevel watchdog_thresh=5
   ```
   Nuevo (agregar `panel_novatek_nt37701.send_pps=N` junto al `clk_scale`):
   ```
   earlycon pmos_root_uuid=d3f420ff-55cd-4e90-aba9-adf089dcf1e4 pmos_rootfsopts=defaults panel_novatek_nt37701.clk_scale=100 panel_novatek_nt37701.send_pps=N ignore_loglevel watchdog_thresh=5
   ```
   > Nota: el módulo es builtin, el parámetro se pasa como
   > `panel_novatek_nt37701.send_pps=N` (bool acepta `N`/`0`).

2. Reconstruir el boot.img (ver sección "Rebuild boot.img" al final).

3. Flashear:
   ```sh
   fastboot flash boot_a out/kali-boot-rhodep-clean.img
   fastboot --set-active=a
   fastboot reboot
   ```

### Cómo persistirlo — OPCIÓN B: cambiar el default del driver (requiere recompilar kernel)

Más "correcto" pero lento. Editar **ambas** copias del parche (están sincronizadas):
- `kernel/patches/0003-drm-panel-add-novatek-nt37701.patch:50`
- `postmarketos/linux-motorola-rhodep/0003-drm-panel-add-novatek-nt37701.patch` (misma línea)

Cambiar:
```c
static bool send_pps = true;
```
por:
```c
static bool send_pps = false;
```
Luego recompilar el kernel y reflashear. (Verificar sync con
`scripts/check-patch-sync.sh`.)

> **Decisión tomada en sesión: usar OPCIÓN A (cmdline + reflash boot.img).**
> Si igual hay que recompilar el kernel por otros fixes, conviene pasar a
> OPCIÓN B para no depender del cmdline.

---

## FIX PENDIENTE (sin resolver) — Glitch de líneas al mover el brillo (FIFO underflow)

Este es el **otro** bug, independiente del FIX #1. NO está resuelto.

- Síntoma: banda de líneas corruptas al mover el brillo mientras el compositor
  repinta. `dsi_err_worker: status=44` (= `DSI_ERR_STATE_LANE_FIFO_UNDERFLOW 0x40`
  | `DSI_ERR_STATE_FIFO 0x04`).
- Causa: los 4 lanes HS del DSI corren a oferta==demanda exacta (clk_scale=100,
  mínimo teórico). Una escritura DCS de brillo cae sobre el stream de píxeles y
  los FIFO se vacían.
- Estado: solo hay **recuperación** (parche 0098, ya instalado: marca el connector
  LINK_STATUS_BAD y KWin rehace el modeset). La **prevención** sigue abierta.
- El camino de prevención documentado por el autor (programar la ventana de
  command-DMA `DSI_DMA_SCHEDULE_CTRL2 @ 0x100`) está en
  `docs/display-cmd-dma-window-wip/README.md` — 4 intentos (v122–v125), todos
  dead-end. NO reintentar sin leer ese doc entero.
- Mitigación práctica real-world: desactivar el brillo automático (el disparador
  casi siempre es la escritura de brillo + repintado simultáneo).

Datos útiles ya recabados para atacar la prevención (de los agentes de research):
- Modo booteado: CSOT NT37701 v1, timing@1, 60 Hz, DSC 1.1, 1080×2400, slice
  12×1080, 10bpc/8bpp.
- v_active = 2400 (0x960); vtotal = 2420 (v-bp 4 + 2400 + v-fp 12 + v-pw 4).
- Vendor fallback para la ventana: `line_no = 2400 + 5 = 2405`, `window = 2400`.
  El `5` corrobora con `qcom,mdss-dsi-dma-schedule-line = <0x05>` que el vendor
  usa en sus paneles cmd-DSC de referencia (Sharp) en el DTBO
  (`backups/vendor-dt-rhodep-20260906/dtbo_b_2.dts:2635,2771`).
- Registro (deducido mainline): `DSI_DMA_SCHEDULE_CTRL2 @ 0x100`, valor
  `line_no | (window << 16)`; `DSI_TRIG_CTRL` nibble bajo = `0xc`.
- **`mdp-transfer-time-us` NO existe en ningún lado** del repo ni del vendor DT —
  habría que calcularlo/derivarlo.
- El panel NT37701 **no** define `dma-schedule-line/window/transfer-time` en su
  nodo DT (confirmado), así que los valores fallback son los correctos; el
  problema (según el autor) es el **cuándo** se escribe el registro, no el qué.

---

## Rebuild boot.img (referencia)

Packer: `scripts/mkbootv2b.py` (header Android boot v2). Flashea a `boot_a`.

Comando (del README del repo):
```sh
python3 scripts/mkbootv2b.py \
  /tmp/apkx/boot/vmlinuz \
  /tmp/apkx/boot/dtbs/qcom/sm6375-motorola-rhodep.dtb \
  _common/boot-artifacts/v47_ramdisk \
  "$(cat boot-artifacts/v47_cmdline.txt)" \
  out/kali-boot-rhodep-clean.img
```
> Ojo: `make-boot-from-apk.sh:71-76` fuerza a re-agregar `clk_scale=100` si el
> cmdline heredado no tiene ningún token `clk_scale=`. Como nuestro edit mantiene
> ese token, se preserva. El `send_pps=N` que agregamos NO lo toca ese script.

Secuencia de flasheo completa:
```sh
fastboot flash boot_a out/kali-boot-rhodep-clean.img
fastboot --set-active=a
fastboot reboot
```
(En este bootloader Motorola, `fastboot flash userdata` siempre da permission
denied; userdata se escribe por `dd` desde un rescue boot.)

---

## Verificación post-reflash

Tras reflashear y reiniciar, confirmar que el parámetro quedó aplicado:
```sh
cat /proc/cmdline | tr ' ' '\n' | grep send_pps
# debe mostrar: panel_novatek_nt37701.send_pps=N

cat /sys/module/panel_novatek_nt37701/parameters/send_pps
# debe mostrar: N
```
Y verificar visualmente que la línea fija ya no aparece tras un ciclo de
bloqueo/desbloqueo de pantalla.
