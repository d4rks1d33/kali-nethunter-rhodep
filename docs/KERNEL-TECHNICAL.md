# postmarketOS port: Motorola Moto G82 5G (motorola-rhodep)

> **OUTDATED — HISTORICAL.** This document (in Spanish) describes an earlier
> state of the port: the v45/v47 images, IPA disabled, audio not yet working,
> and the old patch numbering. It is kept for its technical detail on the build
> internals, but it does **not** reflect the current state. For the current
> build process see [`docs/BUILD.md`](BUILD.md); for what works today see the
> root [`README.md`](../README.md); for the deep history see
> [`docs/interconnect-sm6375-wip/`](interconnect-sm6375-wip/).

Guía de continuación. Escrita para que otra IA o persona retome el port sin el
contexto de las sesiones previas. Todo lo que está acá se verificó en hardware
salvo lo que diga explícitamente "sin probar".

---

## 1. El dispositivo

- **Modelo**: Motorola Moto G82 5G, XT2225-1
- **Codename pmOS**: `motorola-rhodep` (familia Motorola "blair"/"holi")
- **SoC**: Qualcomm Snapdragon 695 5G = **SM6375**
- **CPU**: 2x Cortex-A78 (Kryo 660 Gold) + 6x A55 (Silver), aarch64
- **GPU**: Adreno 619 (variante "holi", GMU wrapper, no GMU real)
- **RAM/almacenamiento**: 6/8 GB, 128 GB UFS
- **Display**: AMOLED 1080x2400, panel Novatek NT37701, DSI+DSC 1.1 modo comando
- **Táctil**: Goodix GT9916S por SPI
- **WiFi/BT**: WCN3990 (NO WCN6750 — el README original del port se equivocaba)
- **Batería**: 5000 mAh. Fuel gauge CellWise CW2217 (I2C 0x64), cargador
  SGMicro SGM41542 (I2C 0x3b, clon del TI bq25601)

El usuario dueño del equipo puede probar y flashear. No es kernel dev: necesita
comandos exactos para copiar/pegar y `.img` listos.

---

## 2. Estado actual (build estable: v45)

| Componente | Estado |
|---|---|
| Arranque kernel mainline (7.2-rc5) | FUNCIONA |
| Almacenamiento UFS | FUNCIONA |
| Display DSI+DSC modo comando 1080x2400@60 | FUNCIONA |
| Táctil Goodix GT9916S | FUNCIONA |
| GPU Adreno 619 (Phosh acelerado) | FUNCIONA |
| WiFi + Bluetooth (WCN3990) | FUNCIONA |
| Módem + ADSP + CDSP (remoteprocs) | FUNCIONAN |
| **Módem: registro, llamadas, SMS** | FUNCIONA (LTE, home; ver HANDOFF-SESSION4 §5 sesión 14) |
| Batería CW2217 + carga SGM41542 (100%, UI ok) | FUNCIONA |
| Throttling térmico (CPU/GPU/carga) por kernel | FUNCIONA |
| USB host / OTG | FUNCIONA |
| USB gadget + SSH | FUNCIONA |
| Botones power/volumen | FUNCIONA |
| **Vibrador** (gpio-vibrator, tlmm 100) | FUNCIONA (§7.3) |
| **Docker** (netfilter/nftables + veth/bridge/overlayfs) | FUNCIONA (§7.9) |
| **Audio** (parlante, auricular, jack, micrófono) | FUNCIONA |
| **Audio Bluetooth** (A2DP) | FUNCIONA (userspace/bluetooth) |
| **Sensor proximidad/luz** | no hecho, va por SSC/ADSP (ver §7.2) |
| **GPS** | el módem publica el servicio de ubicación y acepta sesión; nunca emitió NMEA, sin probar al aire libre (ver §7.4) |
| **NFC** | chip Samsung sec-nfc, sin driver mainline (ver §7.5) |
| **Datos móviles** | FUNCIONAN (~24 Mbit/s) **pero el SoC se reinicia a los 3-10 min con el módem enganchado a LTE**; por eso ipa.ko se envía bloqueado del arranque (ver §7.6) |
| **Audio en llamada** | NO existe: mainline no tiene q6voice (MVM/CVS/CVP) |
| **Monitor mode WiFi** | INVIABLE en mainline: firmware raw 0 (ver §7.7) |
| **Cámara** | inviable en mainline (ver §7.8) |

**Imagen estable pmOS**: `boot-v45-DOCKER.img` + `modules-v45.tar.gz` + `linux-motorola-rhodep-v45.apk` + device apk r0.
Contiene v40 + vibrador + Docker. IPA presente pero `status=disabled`.
**Rollback**: `boot-v41-VIBRATOR.img` (vibrador estable) o `boot-v40-SCOPE.img`.

**Kernel v47 (para Kali NetHunter)**: = v45 + `CONFIG_MODULE_ALLOW_BTF_MISMATCH=y`
+ ~58 simbolos NetHunter (WiFi USB inject, BT, CAN, SDR, gadget HID, NFS, binder).
El fix BTF es CRITICO para Kali/Debian (sin el, TODOS los modulos fallan a cargar:
`failed to validate module [X] BTF: -22` — Alpine/pmOS no valida BTF, Debian si).
Es INOFENSIVO para pmOS -> se puede unificar en un solo kernel. Ver
`../kali-nethunter/README.md` (§4.4) para el detalle. Kali corre estable y
persistente (Phosh+WiFi+modem) desde 2026-08-07.

IMPORTANTE (aprendido a la mala esta sesión): este device tiene
`deviceinfo_flash_kernel_on_update="true"`. El DTB que corre viene del
**kernel-apk instalado en el rootfs** (`/boot/dtbs/...`), NO del boot.img que
flashees a mano. boot-deploy reflashea boot_a al instalar el apk. Por eso la
forma correcta de actualizar es **instalar el kernel apk** (`apk add`), no
`fastboot flash boot`. Flashear boot.img a mano solo sirve para rescate
puntual y lo pisa el próximo `apk add`.

---

## 3. Entorno de trabajo (dónde está cada cosa)

Todo corre dentro de un container Docker Linux con pmbootstrap. El teléfono se
accede por SSH: por USB gadget (`172.16.42.1`) o por WiFi (IP de la LAN).

| Ruta | Qué es |
|---|---|
| `~/.local/var/pmbootstrap/cache_git/pmaports/device/testing/linux-motorola-rhodep/` | **APKBUILD, config, 24 patches**. Fuente de verdad del kernel. |
| `.../device-motorola-rhodep/` | APKBUILD, deviceinfo, units systemd, servicio JEITA, etc. |
| `.../firmware-motorola-rhodep/` | APKBUILD + blobs del vendor (NO va al MR, ver §6) |
| `/tmp/opencode/dl/moto/` | **Árbol del kernel del vendor** (git sparse checkout de `Motorola-SM6375-Devs/android_kernel_motorola_sm6375`). Fuente de verdad de direcciones/registros. `arch/.../vendor/qcom/*.dtsi` y `drivers/`. |
| `/tmp/opencode/kernel/linux-7.2-rc5/` | Kernel mainline de referencia, para leer drivers y DTS de otros SoCs (sm6115, sm6350, agatti). Sin objetos de build. |
| `/tmp/gpuwork/linux-7.2-rc5/` | Árbol con la serie de patches aplicada, para editar DTS/drivers y compilar DTBs rápido con `make ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu-`. Tiene toolchain host incompleta (falla el build completo, sirve para `.dtb` y `.o` sueltos). |
| `/opt/postmarket/` | Bind mount de la Mac del usuario. Se cuelga con reinicios de Docker Desktop. Ahí van los backups y los `.img` de salida. |
| `/opt/postmarket/repo/scripts/` | `make-boot-from-apk.sh` y helpers para armar boot.img |

### Reconstruir el entorno si se perdió
- El vendor tree es git sparse. Para traer más subdirectorios:
  `cd /tmp/opencode/dl/moto && git sparse-checkout add drivers/xxx`
- Backups del código en `/opt/postmarket/backup-rhodep-CODE-*.tar.gz` (los 24
  patches + paquetes + este README).

---

## 4. Cómo se construye (CRÍTICO — leer antes de tocar nada)

### 4.1 Compilar el kernel
```
cd ~/.local/var/pmbootstrap/cache_git/pmaports/device/testing/linux-motorola-rhodep
pmbootstrap checksum linux-motorola-rhodep      # SIEMPRE tras cambiar patches/config
pmbootstrap build --force linux-motorola-rhodep
```
El apk queda en `~/.local/var/pmbootstrap/packages/edge/aarch64/linux-motorola-rhodep-7.2_rc5-r0.apk`.
Build ~4-8 min (más si cambió el .config y recompila todo).

### 4.2 Compilar el paquete del device
```
pmbootstrap checksum device-motorola-rhodep
pmbootstrap build --force device-motorola-rhodep
```

### 4.3 Armar el boot.img
El kernel se instala como **Image plano** (no EFI-zboot: el bootloader de
Motorola no ejecuta la imagen autodescomprimible y resetea). El script reusa el
initramfs de un boot.img previo:
```
cd /opt/postmarket/repo
sh scripts/make-boot-from-apk.sh <boot-base.img> /tmp/boot-nuevo.img
```
Variables útiles del script: `CMDLINE_ADD="..."`, `CMDLINE_DROP="quiet"`.

### 4.4 Extraer los módulos
```
cd /tmp && mkdir m && cd m
tar --ignore-zeros -xzf ~/.local/var/pmbootstrap/packages/edge/aarch64/linux-motorola-rhodep-7.2_rc5-r0.apk usr/lib/modules
tar -czf /tmp/modules-nuevo.tar.gz usr/lib/modules
# VERIFICAR SIEMPRE:
tar -tzvf /tmp/modules-nuevo.tar.gz | awk '$3==0 && /\.ko$/'   # ninguno vacío
tar -tzvf /tmp/modules-nuevo.tar.gz | grep '\.ko\.zst'          # NINGUNO (ver §5)
```

### 4.5 Instalar en el teléfono (ORDEN IMPORTA)
```
# en la Mac:
docker cp <container>:/ruta/boot-nuevo.img .
scp modules-nuevo.tar.gz device-....apk user@IP:/tmp/

# en el teléfono, SIEMPRE borrar módulos viejos antes (ver §5):
sudo rm -rf /usr/lib/modules/7.2.0-rc5
sudo tar -xzf /tmp/modules-nuevo.tar.gz -C /
sudo depmod -a 7.2.0-rc5
find /usr/lib/modules/7.2.0-rc5 -name "*.ko" -size 0 | wc -l    # 0
find /usr/lib/modules/7.2.0-rc5 -name "*.ko.zst" | wc -l         # 0

# device package ANTES de flashear (dispara boot-deploy que re-flashea boot_a):
sudo apk add --allow-untrusted /tmp/device-....apk

# flashear:
fastboot flash boot_a boot-nuevo.img
fastboot --set-active=a && fastboot reboot
```

---

## 5. GOTCHAS que costaron horas (LEER)

1. **`CONFIG_MODULE_COMPRESS` CUELGA EL ARRANQUE.** El CI de pmaports exige ~18
   opciones de kconfig; una es `MODULE_COMPRESS_ZSTD`, que instala módulos como
   `.ko.zst`. Este device se cuelga en el arranque (pantalla congelada
   post-logo, sin SSH) cargando módulos comprimidos, y `modprobe` da
   `Exec format error`. **Debe quedar deshabilitado** en el config. El CI no lo
   bloquea.

2. **`tar -x` NO borra lo viejo.** Al reinstalar módulos hay que
   `rm -rf /usr/lib/modules/<ver>` primero, o quedan `.ko` y `.ko.zst`
   mezclados de builds distintos y `modprobe` falla.

3. **No mezclar cambios de .config y de lógica en el mismo build.** Si algo
   cuelga, no sabés cuál fue. Partir SIEMPRE de un boot.img que arranca y
   aplicar un cambio a la vez, verificando entre cada uno.

4. **Siempre verificar `uname -r` (7.2.0-rc5) y `/proc/cmdline`** antes de
   diagnosticar. Hubo imágenes de rescate con `modprobe.blacklist=` que dan un
   falso "medio sistema roto".

5. **Los módulos viven en el rootfs, no en el boot.img.** Cada cambio de kernel
   necesita reinstalar módulos + `depmod -a 7.2.0-rc5`.

6. **`dmesg | grep -c` engaña**: el ring buffer da la vuelta y el contador puede
   bajar mientras los errores siguen. Mirar timestamps, no cuentas.

7. **`echo $VAR` en un shell `sudo su` no refleja profile.d** (no es login
   shell). Para ver el entorno de un proceso: `tr '\0' '\n' < /proc/PID/environ`.

8. **`find /proc/device-tree` da vacío** (es symlink). Usar
   `/sys/firmware/devicetree/base`.

9. **ramoops para bootloops**: `/sys/fs/pstore/` (o
   `/var/lib/systemd/pstore/` si systemd ya lo copió). Sobrevive reboot en
   caliente, NO corte de energía. Agregar `ignore_loglevel` al cmdline para ver
   todo (el cmdline lleva `quiet` que mete el bootloader).

10. **Los registros de un `Call trace` suelen traer el mensaje en ASCII** en
    x6/x7. Ej: `x6=0x702d726570206f4e` = "No per-p".

---

## 6. Firmware del vendor (NO va al MR)

Los blobs (WiFi, BT, GPU zap, módem) NO son redistribuibles ni están en
`linux-firmware` de Alpine (se verificó: WCN3990, a630_sqe, wlanmdsp no están).
Se extraen de la partición `vendor` de la ROM stock. **No etiquetar MIT** — el
usuario insistió pero es incorrecto y el mantenedor lo rechazaría.

Están en `vendor` (partición lógica en `super`, offset `0xcf300000`), bajo
`/firmware/` y `/bt_firmware/`. El paquete `firmware-motorola-rhodep` local los
tiene para desarrollo, pero se documenta cómo extraerlos en la descripción del MR.

Rutas donde el kernel los busca:
- GPU: `qcom/a630_sqe.fw`, `qcom/a619_gmu.bin`, `qcom/sm6375/motorola/rhodep/a615_zap.mdt`+`.b00..b02`
- BT: `qca/crbtfw21.tlv`, `qca/crnv21.bin`
- WiFi: `ath10k/WCN3990/hw1.0/firmware-5.bin`, `board-2.bin`
- WLAN PD: `qcom/sm6375/motorola/rhodep/wlanmdsp.mbn` + 6 `.jsn`
- Audio amp (cuando aplique): `aw88261_acf.bin` = vendor `aw882xx_pid_2113_acf.bin`

---

## 7. LO QUE FALTA — punto de partida de cada uno

Método general que funcionó 4 veces: **leer el driver del vendor** en
`/tmp/opencode/dl/moto/` para el mapa de registros/direcciones exacto, comparar
con lo que soporta mainline, y si el chip es un clon de uno soportado, agregar
el compatible en vez de escribir driver nuevo.

### 7.1 Audio (lo más avanzado, casi listo)
Patches existentes PERO FUERA del `source=` (no se incluyen porque cuelgan):
`0026-adsp-audio-services`, `0027-sm8250-add-sm6375-sndcard`, `0028-speaker-audio`.
Estado: los amplificadores **AW88261** (I2C 0x34 y 0x35, chip id 0x2113 =
`AW88261_CHIP_ID`, driver `snd-soc-aw88261` en mainline) probaron OK, los
servicios del ADSP (q6afe/q6asm/q6adm/q6routing vía APR sobre GLINK) levantan,
la tarjeta de sonido se crea (`card 0: M5G`), y el ACF del vendor
(`aw882xx_pid_2113_acf.bin` → `aw88261_acf.bin`) parsea sin cambios.

**BLOQUEANTE**: los parlantes van por MI2S secundario, cuyos pines son **LPASS
LPI GPIOs** (`lpi_i2s2_*`, `qcom,lpi-gpios`). Mainline **no tiene** pinctrl LPI
(`pinctrl-sm6375-lpass-lpi.c`) ni LPASS clock controller (`lpasscc-sm6375`) para
SM6375. Existen los de sm6115 y sm6350 como molde. Habilitar el mixer
(`SEC_MI2S_RX Audio Mixer MultiMedia1`) y reproducir **RESETEA el SoC** sin esos
drivers. Por eso quedó fuera del MR.
Plan: portar `pinctrl-sm6115-lpass-lpi.c` → sm6375 comparando tablas de pines
contra el vendor holi, y el `lpasscc`. Auriculares/mic son otro camino (WCD9370
por SoundWire, también sin soporte LPASS/SWR para sm6375).

### 7.2 Sensor de proximidad/luz (y acelerómetro)
**Malas noticias**: en el vendor los sensores van por el **SSC (Sensor hub
dentro del ADSP)**, no por I2C directo (`qcom,fastrpc-adsp-sensors-pdr` en
holi.dtsi). Eso en mainline es muy difícil (no hay soporte de sensor hub SSC
genérico). Primer paso: escanear los buses I2C que aún NO habilitamos (sólo van
i2c8/i2c10 de carga; el SoC tiene i2c0,1,2,6,7,9) por si algún sensor está en
I2C directo. Método: `i2cdetect` tras habilitar cada bus, comparar con el DTS
del vendor. Si están en SSC, es un proyecto grande y probablemente inviable.

### 7.3 Vibrador — HECHO (v41), patch 0025 (config) + nodo DT
`gpio-vibrator` sobre **GPIO tlmm 100** (igual que el vendor
`moto,vibrator-ldo`, blair-rhodep-common-overlay.dtsi:571). Nodo agregado al
root del .dts del device (patch 0001):
```
vibrator {
	compatible = "gpio-vibrator";
	enable-gpios = <&tlmm 100 GPIO_ACTIVE_HIGH>;
	vcc-supply = <&vph_pwr>;
};
```
Config: `CONFIG_INPUT_GPIO_VIBRA=m` (arrastra `INPUT_FF_MEMLESS`).
GOTCHA del driver mainline `gpio-vibra.c`: EXIGE un `vcc-supply` (falla el
probe si falta). Se usó `vph_pwr` (regulador fijo always-on = línea de
batería). Verificado en HW: registra `input4 = gpio-vibrator`, event4, EV_FF
+ FF_RUMBLE, y vibra con un efecto FF_RUMBLE por evdev.
NOTA test evdev: `struct ff_effect` en arm64 mide 48 bytes (la union tiene un
puntero en `ff_periodic_effect.custom_data`), NO 32. El EVIOCSFF hay que
armarlo con size 48 o da EFAULT (Bad address). El id es `__s16` (usar -1
signed, no 0xFFFF).

### 7.4 GPS — más cerca de lo que decía esta sección

El módem **ya publica el servicio de ubicación** por QRTR y `qmicli` lo habla
sin necesidad del IPA ni de ModemManager:

	qrtr-lookup            ->  16  2  0  0  107  Location service (~ PDS v2)
	qmicli --loc-start     ->  Successfully started location tracking
	qmicli --loc-set-nmea-types=all
	                       ->  gga, rmc, gsv, gsa, vtg, pqxfi, pstis

O sea que la sesión GNSS arranca y acepta configuración. Lo que no se consiguió
es que emita: ni NMEA ni información de satélites, ni con la radio apagada ni
encendida, en unos 3 minutos de escucha. **La prueba que falta es al aire
libre**, que es gratis: adentro y en arranque en frío sin almanaque, no emitir
es esperable. Si al aire libre salen sentencias NMEA, GPS es cuestión de un
puente a Geoclue y queda andando.


### 7.5 NFC — DIFÍCIL (chip Samsung sin driver mainline)
Chip identificado: **`sec-nfc`** (Samsung NFC controller, S3NRN) en I2C
`qupv3_se7` addr **0x27** (blair-rhodep-common-overlay.dtsi:271). GPIOs:
ven=tlmm48, firm=tlmm8, irq=tlmm9, clk_req=tlmm7.
Problema: mainline NO tiene driver para `sec-nfc` (tiene `nxp-nci`, `st-nci`,
`pn544`, etc., pero no el de Samsung). El char device del vendor expone NCI
crudo a un HAL propietario. Portarlo = escribir driver nuevo. En el mismo bus
SE7 hay también un SAR sensor `Semtech,sx937x` @0x2c (tampoco tiene driver
mainline útil). NO es "fácil" como se pensaba.

### 7.6 Datos móviles — FUNCIONAN, pero el SoC se reinicia en LTE

Esta sección decía "bloqueado por interconnect" y estaba equivocada en las dos
mitades. Los datos móviles funcionan: ~24 Mbit/s medidos, IP pública, IPv6.
Lo que falta es estabilidad.

**Con `ipa.ko` cargado y el módem enganchado a LTE el SoC se reinicia cada 3 a
10 minutos**, en silencio, sin fallo ni panic ni una línea de consola. Con la
radio encendida pero sin enganchar aguanta 22 minutos sin problemas, y con
`ipa.ko` descargado también. El disparador es que el módem **se enganche**, que
es cuando instala sus tablas de filtrado y ruteo.

Por eso las imágenes envían `/etc/modprobe.d/rhodep-ipa-hold.conf`, que
mantiene el IPA fuera del arranque. Borrar ese archivo y reiniciar devuelve los
datos móviles y ModemManager, al precio de los reinicios.

Tres hipótesis probadas y descartadas con evidencia (HANDOFF-SESSION4 §5,
sesión 15): el camino a IMEM sin votar en el interconnect, el voto por demanda
en vez de un piso permanente, y el mapa de SRAM del IPA heredado de qcm2290.
Los tres patches se conservan porque son correcciones verificadas contra el
árbol del fabricante, pero ninguno es la causa.

El experimento que decide y que todavía no se pudo correr: enganchar a LTE con
`ipa.ko` **nunca cargado**. Requiere configurar a mano el APN de attach por
`qmicli --wds-set-lte-attach-pdn-list`, porque sin ModemManager el módem no
engancha y MM necesita el puerto de red del IPA para existir.


### 7.7 Monitor mode WiFi — INVIABLE en mainline (cerrado)
Probado a fondo esta sesión. Conclusión definitiva, NO es cosa de kernel:
- `iw list` SÍ muestra monitor y se puede crear `mon0` (cfg80211 lo permite).
- Pero **captura = 0 paquetes** siempre (con wlan0 up o down, canal fijo o no).
- **Inyección = imposible**: el firmware WCN3990 reporta `raw 0` (dmesg
  `ath10k_snoc ... raw 0 hwcrypto 1`). Raw mode es requisito para aircrack-ng.
  Intentar `ath10k_core frame_mode=1 cryptmode=1` hace FALLAR el probe:
  "cryptmode > 0 requires raw mode support from firmware" → fw features -22 →
  te deja sin wlan0. NO usar esos params.
- Por qué en LineageOS anda: usa el driver propietario `qcacld-3.0` de
  Qualcomm, que implementa monitor/spectral fuera de la ruta ath10k mainline.
  No hay equivalente en mainline y ningún CONFIG lo habilita.
Veredicto: descartado salvo que aparezca un firmware ath10k con raw mode para
WCN3990 (no conocido). La creación de la interfaz monitor funciona pero es
inútil (no captura ni inyecta).

### 7.8 Cámara
CAMSS + drivers de sensores. Prácticamente inviable en ports mainline
comunitarios. No priorizar.

### 7.9 Docker — HECHO (v45), solo config de kernel
Docker corre (probado `docker run hello-world` OK). Fue solo habilitar
opciones de netfilter/contenedores en el config; no toca DT ni firmware.
Símbolos añadidos (todos resueltos con `make olddefconfig` tras editarlos):
```
CONFIG_NF_TABLES_INET=y
CONFIG_NF_TABLES_IPV4=y
CONFIG_NF_NAT=m
CONFIG_NFT_NAT=m
CONFIG_NFT_MASQ=m
CONFIG_NFT_COMPAT=m
CONFIG_NETFILTER_XT_NAT=m               <- CLAVE
CONFIG_NETFILTER_XT_TARGET_MASQUERADE=m <- CLAVE
CONFIG_NETFILTER_XT_TARGET_REDIRECT=m
CONFIG_VETH=y  CONFIG_BRIDGE=y  CONFIG_BRIDGE_NETFILTER=y  CONFIG_OVERLAY_FS=y
```
GOTCHA que costó un build: Docker usa `iptables-nft`, y `-j MASQUERADE` se
traduce al target **xtables** (`xt_MASQUERADE`), NO al `nft_masq` nativo. Sin
`NETFILTER_XT_TARGET_MASQUERADE` dockerd falla al crear la red bridge:
"MASQUERADE revision 0 not supported, missing kernel module". Habilitar los
`NFT_*` NO alcanza; hacen falta los `NETFILTER_XT_*`.
OJO al editar config a mano: poné los símbolos en su sección correcta o
`olddefconfig` los revierte a `n` en silencio. Verificar SIEMPRE el `.config`
resultante (`grep` de cada símbolo) antes de compilar. `MODULE_COMPRESS` debe
seguir en "is not set" tras cualquier olddefconfig (gotcha §5.1).

---

## 8. Los 26 patches (orden del source=)

DTS del device y del SoC + fixes de drivers compartidos. Los que tocan código
compartido (no-DTS) son candidatos a upstream y lo dicen en su commit message.

```
0001 add-Motorola-Moto-G82-5G           DTS base del device (+ nodo vibrator gpio)
0002 dsi-fix-pclk-for-dsc-without-widebus    [upstream] pclk DSI
0003 drm-panel-add-novatek-nt37701       driver del panel
0004 dpu-cmd-mode-tearcheck-intf-config2 [upstream] DPU cmd mode
0005 dpu-ctl-top-group-id-from-dpu-5     [upstream] CTL group id
0006 dsi-fix-dsc-range-max-qp-8bpp-10bpc [upstream] DSC range qp
0007 rhodep-enable-adreno-619-gpu        DTS GPU (VDDGX, no VDDCX!)
0008 rhodep-add-ramoops                  DTS pstore
0009 rhodep-enable-wifi-and-bt           DTS WCN3990 (serial0 alias!)
0010 rhodep-enable-usb-host-otg          DTS usb-role-switch
0011 rhodep-enable-charger-i2c-buses     DTS i2c8/i2c10
0012 add-cellwise-cw2217-fuel-gauge      driver nuevo (con ui-full-soc)
0013 rhodep-add-battery                  DTS gauge + simple-battery
0014 a6xx-sm6375-use-gmu-wrapper-funcs   [upstream] catálogo a6xx
0015 sm6375-gpu-smmu-adreno              [upstream] adreno-smmu
0016 bq256xx-add-sgm41542                [upstream] charger clon bq25601
0017 rhodep-add-charger                  DTS charger (4450mV/1250mA)
0018 bq256xx-reset-part-before-configuring [upstream] reset caché regmap
0019 bq256xx-honour-battery-charge-limits  [upstream] usar bat_info
0020 sm6375-thermal-cooling-maps         DTS throttling CPU
0021 rhodep-throttle-gpu                 DTS throttling GPU
0022 bq256xx-charge-current-cooling-device [upstream] cooling device
0023 bq256xx-report-device-scope         [upstream] scope=DEVICE (fix UI 48%)
0024 rhodep-battery-thermal-zone         DTS zona térmica batería
0025 net-ipa-add-sm6375-without-interconnects  driver IPA: compat qcom,sm6375-ipa + ipa_data_v4_11_sm6375 (icc_count=0)
0026 rhodep-enable-ipa                   DTS nodo ipa@5840000 (status=disabled, ver §7.6)
```
Nota: además del source=, esta tanda tocó `config-motorola-rhodep.aarch64`
(CONFIG_INPUT_GPIO_VIBRA=m para el vibrador §7.3, y el set netfilter/Docker
§7.9). El config no va en el source= pero es parte de la build v45.
Regla: tras editar/renumerar patches, correr `pmbootstrap checksum` y verificar
que la serie aplica **sin fuzz** sobre un árbol pristino (aplicar 0007+ sobre
`/tmp/opencode/kernel/linux-7.2-rc5`, que ya tiene 0001-0006).

---

## 9. Los bugs de mainline que este port destapó (contexto técnico)

Todos con el mismo patrón: código correcto bajo una suposición que este
hardware no cumple. Upstream nunca habilitó estos periféricos en sm6375, así que
nunca se ejecutaron.

**GPU (3):**
- Catálogo a6xx daba `a6xx_gpu_funcs` (GMU real) a sm6375, pero el DT declara
  GMU wrapper → se inicializa wrapper y se despierta como GMU real → cuelgue.
  Fix: `a6xx_gmuwrapper_funcs` (patch 0014).
- SMMU de GPU sin `"qcom,adreno-smmu"` → sin pagetables por proceso ("No
  per-process page tables"). Fix: agregar el compatible (0015).
- Voltaje sobre VDDCX en vez de **VDDGX** → subalimentado a 650 MHz, cuelga bajo
  carga. sm6115 usa VDDCX porque su A610 se alimenta de CX y no tiene riel GX;
  sm6375 sí (SM6375_VDDGX). Fix en 0007.

**Batería/cargador (4):**
- SGM41542 es clon de registros del bq25601 → agregar compatible (0016).
- Caché del regmap de bq256xx sembrada con defaults de fábrica → si el
  bootloader dejó otro estado, `regmap_update_bits` omite escrituras como no-op
  y la carga nunca se habilita. Fix: resetear el chip en hw_init (0018).
- Límites del DT usados sólo como techo del clamp, no como target → nunca subía
  el voltaje. Fix: usar bat_info (0019).
- Batería fantasma del bq256xx (type=Battery, sin capacity) → UPower la promedia
  con el gauge real y muestra la mitad (48% en vez de 96). udev UPOWER_IGNORE no
  alcanza (UPower lee scope del sysfs). Fix: `SCOPE_DEVICE` en el driver (0023).

Detalles largos de cada uno en los commit messages de los patches.

---

## 10. Datos móviles / cadena QRTR (ya funciona, para referencia)

El WLAN vive DENTRO del módem (protection domain). Cadena de arranque:
firmware DSP → `rmtfs -r -P -s` (da EFS al módem) → `pd-mapper` (con los 6 .jsn
junto al firmware) → servicio QMI 69 (wlfw) → `ath10k_snoc`. Todo automatizado
en el device package (units systemd, `readonly-firmware.mount`, tmpfiles).
`rmtfs`/`pd-mapper`/`tqftpserv` los provee Alpine; nuestro paquete sólo agrega
un drop-in para los flags de rmtfs (no pisar las units, causa conflicto apk).

---

## 11. Tareas de empaquetado / MR (pendientes del lado del usuario)

- MR: `postmarketOS/pmaports!9234`, fork `d4rks1d33/pmaports` rama
  `motorola-rhodep`. Commit único `motorola-rhodep: new device`. Requiere
  force-push. Autor: `d4rks1d3 <teogorqui1@hotmail.es>`.
- El MR está desactualizado respecto de los últimos cambios de batería. Rehacer
  commit único y push.
- **Wiki**: la página necesita `booting = yes` en el infobox o el job `wiki` del
  CI falla. Sólo lo puede hacer el usuario con su cuenta.
- Feedback del mantenedor (JustSoup321) ya aplicado en la última versión:
  orden de campos, `android-tools-mkbootimg`, `_flavor="${pkgname#linux-}"`,
  `kernel-cmdline.d`, `kconfig migrate`, quoting, sin `DTC_FLAGS=-@`.

---

## 12. Rollback de emergencia
Rescate rápido por fastboot (arranca YA, pero el rootfs sigue con el kernel
viejo instalado; ver nota abajo):
```
fastboot flash boot_a boot-v41-VIBRATOR.img   # estable con vibrador
fastboot --set-active=a && fastboot reboot
```
Para dejarlo CONSISTENTE de verdad (por `flash_kernel_on_update=true`, §2),
una vez adentro reinstalar el kernel-apk de esa versión, que reflashea boot
via boot-deploy con el DTB correcto:
```
sudo rm -rf /usr/lib/modules/7.2.0-rc5
sudo tar -xzf /tmp/modules-vNN.tar.gz -C /
sudo depmod -a 7.2.0-rc5
sudo apk add --allow-untrusted /tmp/linux-motorola-rhodep-vNN.apk
```
Si NO reinstalás el apk, el próximo `apk add` de cualquier cosa dispara
boot-deploy y reflashea boot_a con el kernel/DTB que haya en el rootfs
(posible bootloop si ese era el malo).

Imágenes de rescate disponibles (en /opt/postmarket y en la Mac):
`boot-v45-DOCKER.img` (actual), `boot-v41-VIBRATOR.img`, `boot-v40-SCOPE.img`.

Para volver a Android: imágenes stock en el backup (`stock-restore`),
`fastboot flash` de boot/dtbo/vbmeta/vendor_boot.
