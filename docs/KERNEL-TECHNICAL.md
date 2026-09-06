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
| **Sensores** (acelerómetro, giróscopo, magnetómetro, proximidad, luz) | FUNCIONAN (rotación automática incluida) — `userspace/sensors/`, ver §7.2 |
| **GPS / ubicación** | **hay ubicación, no hay satélites.** WiFi y celda funcionan hoy vía gpsd y geoclue (22 m / 250 m medidos, sin SIM). Un fix satelital (`standalone`) **reinicia el SoC en <100 ms** (ver §7.4 y `GPS-USERSPACE.md`) |
| **NFC** | chip Samsung sec-nfc, sin driver mainline (ver §7.5) |
| **Datos móviles** | FUNCIONAN (~24 Mbit/s) **pero el SoC se reinicia a los 3-10 min con el módem enganchado a LTE**; por eso ipa.ko se envía bloqueado del arranque (ver §7.6). Desde 2026-08-26 hay un reproductor de 4 s que no necesita SIM, y el A/B señala a IPA (§7.6) |
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

### 7.2 Sensores — HECHO (ver `userspace/sensors/`)

Esta sección decía "probablemente inviable" y estaba equivocada. Funcionan:
acelerómetro (icm4x6xx), giróscopo, magnetómetro, brújula, proximidad y luz
(stk3a5x), expuestos por `iio-sensor-proxy` en D-Bus, con rotación automática
de pantalla.

Lo que sí era cierto: los sensores van por el **SSC dentro del ADSP** y no por
I2C directo — la IMU está en **I3C**, así que ningún nodo del device tree del AP
puede alcanzarla, y escanear buses I2C no habría encontrado nada.

Lo que faltaba no era un driver sino el **registry**: cada driver del SSC lee su
bus, dirección, interrupción y calibración de un registry que vive en el
application processor, y el ADSP lo pide por FastRPC. Nadie contestaba, así que
el sensor core arrancaba sin ningún sensor.

**No hizo falta ningún cambio de kernel.** El "reverse RPC" que este repo daba
por imposible en mainline no existe como tal: el demonio hace una invocación
normal hacia adelante sobre el handle estático 3 y el DSP la contesta cuando
tiene trabajo. Todo pasa por `FASTRPC_IOCTL_INVOKE`, que mainline ya tenía, y
`FASTRPC_IOCTL_INIT_ATTACH_SNS` existe justo para este caso. La userspace de
Qualcomm (`quic/fastrpc`, BSD-3) ya soporta el driver de mainline y compiló sin
tocar nada.

Detalle completo, trampas incluidas, en `userspace/sensors/README.md`.

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

### 7.4 GPS — hay ubicación, pero no hay satélites

**Esta sección estaba equivocada en las dos mitades y se retracta.** Decía:

> Lo que no se consiguió es que emita: ni NMEA ni información de satélites (...)
> **La prueba que falta es al aire libre**, que es gratis (...) Si al aire libre
> salen sentencias NMEA, GPS es cuestión de un puente a Geoclue y queda andando.

Ninguna de las dos cosas resultó cierta:

1. **Nunca fue un problema de estar bajo techo.** Pedir un fix satelital de
   verdad —modo de operación `standalone`— **reinicia el SoC entero en menos de
   100 ms**, de forma reproducible, sin panic, sin oops, con la consola cortada a
   mitad de línea y `androidboot.bootreason=watchdog` /
   `powerup_reason=0x00008000`. Lo que dispara el reset es que se levante el
   *motor de medición* GNSS del módem, y nada más: no la sesión, no las
   indicaciones, no QMI. En modo `cellid`, que corre la máquina de estados de la
   sesión sin encender ese motor, la sesión vive indefinidamente. La bisección
   completa está en
   [`interconnect-sm6375-wip/GNSS-SM6375.md`](interconnect-sm6375-wip/GNSS-SM6375.md).

   La razón por la que antes esto se veía como "silencio bajo techo" es que
   `qmicli` **no puede** manejar una sesión GNSS: no acepta dos acciones LOC en
   la misma invocación, y sin `qmi-proxy` el cliente QMI muere con el proceso,
   así que toda prueba desde la shell termina o con una sesión sin oyente o con
   un oyente sin sesión. Las dos son **silenciosas**, y eso se lee igual que un
   GPS sin vista al cielo.

2. **El puente a Geoclue ya existe, funciona, y no arregla lo de arriba.** El
   paquete `rhodep-gnss` (versión 3, instalado y verificado) obtiene la posición
   de fuentes que no necesitan el motor de medición y la publica como NMEA
   común:

   - `--source=wifi` — trilateración por puntos de acceso WiFi. No toca el
     módem ni ningún servicio QMI.
   - `--source=cell` — identidad de las celdas que el módem escucha, leída por
     QMI **NAS** (`--nas-get-cell-location-info`), que es de sólo lectura y no
     puede arrancar el motor.
   - `--source=qmi-cellid` — el servicio LOC del módem fijado en modo `cellid`.
     Necesita un módem **registrado**, así que hoy en este equipo no da nada.
   - `--source=auto` — cadena: `qmi-cellid` si el módem está registrado, si no
     `wifi`, si no `cell`.

   Publica `$GPGGA`/`$GPRMC`/`$GPGSA`/`$GPGST` sintetizados y con checksum en un
   socket UNIX `/run/gnss-share.sock` (modo 0660, grupo `geoclue`) y en
   `tcp://127.0.0.1:2948` —el TCP existe porque **gpsd no sabe leer sockets
   UNIX**—, y trae los drop-ins que enganchan gpsd (`DEVICES="tcp://127.0.0.1:2948"`,
   `GPSD_OPTIONS="-n"`) y geoclue
   (`/etc/geoclue/conf.d/20-rhodep-wifi.conf`).

   Verificado en vivo, con el SIM **sin provisionar** y el módem en
   `registration: searching` / `imsi-unknown-in-hlr`:

	$ gpspipe -w | grep TPV
	{"class":"TPV","mode":2,"lat":-32.954171667,"lon":-60.644348333,"eph":142.5}

	$ cgps -s
	2D FIX

	geoclue informa la misma ubicación por D-Bus.

   Medido: **22 m** por WiFi, **250 m** por celda.

**Dato nuevo y útil, que no estaba anotado en ningún lado:** la identidad de las
celdas se obtiene **sin registro de red**. Con el SIM sin provisionar y el módem
en servicio limitado, `qmicli -p -d qrtr://0 --nas-get-cell-location-info`
devuelve igual `Cell ID: '60858'`, `PLMN: '72234'`, `Location Area Code: '420'`.
El módem acampa para servicio limitado y decodifica el BCCH sin adjuntarse; la
identidad de celda es información **difundida**, no específica del abonado.
ModemManager no lo muestra (`--location-get` vuelve vacío) porque MM sólo
completa LAC/CI desde una celda servidora **registrada** — es política de MM, no
una capacidad que falte. Es intermitente (~2 de cada 10 sondeos) porque el módem
está en bucle de búsqueda.

**Lo que sigue faltando** es exclusivamente el fix **satelital**: no hay
velocidad, no hay rumbo, no hay precisión por debajo de 10 m, y no hay posición
donde no haya ni WiFi relevado ni celda conocida. Eso está bloqueado por el
reset del SoC, que es un bug de hardware/firmware del módem y no se arregla
desde el espacio de usuario.

Detalle completo del lado userspace —las tres compuertas de seguridad antes de
`QMI_LOC_START`, la cadena de proveedores, la trampa de `considerIp`, y por qué
la precisión no se puede convertir ingenuamente a HDOP— en
[`GPS-USERSPACE.md`](GPS-USERSPACE.md).


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

**Actualizado 2026-08-26.** Dos cosas de arriba quedaron viejas.

El "experimento que decide" de esta sección **no es ejecutable**: sin `ipa.ko`
el módem busca 96 segundos y nunca registra en LTE, con o sin el APN de attach
puesto a mano. Quedó descartado como camino.

Pero ya no hace falta, porque apareció un reproductor mucho mejor. **El SoC no
muere cuando el módem se cae: muere cuando el módem vuelve a levantarse**, y
alcanza con una escritura a debugfs:

    echo 1 > /sys/kernel/debug/remoteproc/remoteproc0/crash

Unos tres segundos y medio después, reinicio: `bootreason=watchdog`, pstore
vacío, PON `088d=01 08c2=02 08c4=40`, que es la firma de todas las muertes de
este port. **Sin SIM, sin radio, sin cobertura y sin attach**, contra los 3 a
10 minutos que pedía el camino de LTE.

Con ese reproductor se pudo hacer el A/B que antes era imposible:

| | reinicios / levantadas del módem |
|---|---|
| `ipa.ko` cargado en el arranque | **4 de 5** |
| `ipa.ko` descargado | **0 de 6** |

(La quinta con IPA no tuvo levantada a la que sobrevivir: la recuperación se
colgó dentro de `ipa_modem_stop`, el módem nunca volvió y el SoC tampoco se
reinició.)

Ojo con lo que esto dice y lo que no. **No dice que IPA sea la causa**: sacar
`ipa.ko` saca el camino de datos entero, y el módem podría comportarse distinto
por no tener a dónde mandar tráfico. El próximo corte hay que hacerlo *adentro*
de IPA, en `ipa_modem_start` y el armado de canales GSI. Lo que sí explica es
por qué todas las rondas de eliminación volvían al LTE: un attach es lo único
que hace que el módem levante el camino de datos, y el camino de datos es IPA.

Apareció además un bug aparte que conviene reportar solo: cuando el módem
muere, `ipa_modem_stop` espera en `__gsi_channel_stop` una completion que un
módem muerto nunca manda, **sin timeout**, y el worker queda en estado D para
siempre. Es lo que hace que el apagado tarde quince minutos.

Detalle completo, evidencia y herramientas: `docs/watchdog-ipa-lte-wip/HANDOFF.md`
(§ "The SoC dies when the modem comes back" y § "IPA is required for the reset"),
`userspace/debug-tools/rhodep-modem-restart-watch.py`.

> **Estado del teléfono de desarrollo:** el `rhodep-ipa-hold.conf` está
> **desactivado a propósito** (movido a `/root/ipa-hold.off`) porque el
> reproductor necesita IPA cargado. Con una SIM puesta, ese teléfono se
> reinicia: es esperado, no un fallo nuevo. Cómo revertirlo está en
> `/etc/modprobe.d/README-rhodep-ipa-hold-is-OFF`.


### 7.7 Monitor mode WiFi — RX PASIVO FUNCIONA, inyección no (actualizado)

**El análisis anterior estaba equivocado**: decía "captura 0 paquetes", pero
el test anterior usaba `frame_mode=1 cryptmode=1` (que mata el probe). El path
del monitor vdev es completamente independiente y no necesita esos params.

**Lo que funciona hoy, sin parche:**
```
sudo iw dev wlan0 interface add mon0 type monitor
sudo ip link set mon0 up
sudo tcpdump -i mon0 --immediate-mode -e
```
dmesg confirma: `ath10k_snoc c800000.wifi mon0: entered promiscuous mode`.
Frames con radiotap headers llegan. Verificado en el dispositivo real.

**Análisis de fuente (ath10k mainline 7.2-rc5):**
- `ATH10K_FW_FEATURE_RAW_MODE_SUPPORT` (`raw 0`) solo gatea TX (encapsulación
  raw, SW-crypto). NUNCA se verifica en el path de creación del monitor vdev
  (`ath10k_add_interface`, `mac.c:5646-5647`) ni en el RX.
- `WMI_VDEV_TYPE_MONITOR=4` se crea incondicionalmente para
  `NL80211_IFTYPE_MONITOR`. No se requiere ningún bit de servicio WMI.
- WCN3990 usa `ATH10K_DEV_TYPE_LL` (no HL), así que el path LL de RX no
  descarta frames de peers desconocidos (el HL sí lo haría, haciendo inútil
  el monitor).

**Limitación real:** mon0 comparte phy0 con wlan0. Canal fijo al de wlan0
mientras esté asociada (`-EBUSY` al intentar cambiar). Para captura en otro
canal: `nmcli dev disconnect wlan0` primero.

**Inyección:** sigue siendo imposible. `raw 0` bloquea `ATH10K_HW_TXRX_RAW`.
El firmware es signed/closed, no hay ruta a habilitarlo.

**Próximo paso (opcional, ~10 líneas de parche):** `set_promisc_mode_cmdid`
está mapeado a `WMI_PDEV_PARAM_UNSUPPORTED` en mainline (`wmi-tlv.c:4412`).
qcacld-3.0 lo envía explícitamente. Si el filtro RX del firmware no abre
para frames de otras BSSes (solo ve la propia), el fix es identificar el
enum `WMI_TLV_PDEV_PARAM_*` correcto y agregar un `ath10k_wmi_pdev_set_param`
en `ath10k_monitor_vdev_start`. Pendiente de verificar con captura cross-BSS.

**NO usar `ath10k_core frame_mode=1 cryptmode=1`** — sigue siendo cierto que
eso mata el probe con -EINVAL y te deja sin wlan0.

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

### 9.x El mdss nunca votaba ancho de banda (0065)

`msm_mdss_parse_data_bus_icc_path()` pide `devm_of_icc_get(dev, "mdp0-mem")`. El
nodo `mdss` del rhodep no declaraba `interconnects`, así que devolvía NULL,
`num_mdp_paths` quedaba en 0 y **todos** los `icc_set_bw()` de `msm_mdss.c`
iteraban sobre un array vacío. El driver de display corría sin pedir un solo
byte de ancho de banda, y los valores que el catálogo del SM6375 calcula para
eso (`min_dram_ib = 1600000`, `min_core_ib = 2500000`) se computaban y se
tiraban.

Arreglado con las mismas rutas que usa el sm6115, que es el hermano SMD-RPM más
cercano: `mmrt_virt MASTER_MDP_PORT0 → bimc SLAVE_EBI` y
`bimc MASTER_AMPSS_M0 → config_noc SLAVE_DISPLAY_CFG`.

Obliga a compilar el proveedor dentro del kernel
(`CONFIG_INTERCONNECT_QCOM_SM6375=y`, antes `=m`): al nombrar un interconnect,
el probe del mdss difiere hasta que el proveedor exista, y el proveedor era un
módulo en blacklist desde v96. De paso quedó verificado que ese blacklist ya no
hace falta — cargado a mano bindea los seis buses sin colgarse, así que el fix
0027 aguanta.

Ojo con el efecto medido: el voteo pasa a estar vivo (`avg 903260, peak
1600000`), pero el agregado sobre `ebi` ya figuraba en INT_MAX por decisión del
RPM. O sea que el bug era real y el parche es correcto, pero **no está
demostrado** que esto cambie el comportamiento observable.

### 9.y El cpuidle profundo cuesta frames (0061)

Medido con `scripts/rhodep-repaint-bench`, 3 corridas de 20 s por condición:

| | frames tardíos | peor frame |
| --- | ---: | ---: |
| `cpu-sleep-0-1` activo | 5 | 33.6 ms |
| desactivado | 0 | 16.9 ms |

33 ms son dos períodos de refresco: un frame perdido entero. El estado declara
1617 us de latencia de salida, casi el 10% del presupuesto a 60 Hz.

0061 sigue en el build porque el otro lado de la balanza nunca se midió bien: el
A/B/A de batería dio 67.5 / 71.1 / 72.1 mA con un ruido de ±30-43 mA que se
traga cualquier diferencia. Antes de tocarlo hay que medir el consumo en serio.

No tiene nada que ver con las líneas glitcheadas: 20 errores con el estado
activo y 20 con él desactivado.


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
firmware DSP → `rmtfs -P -s` (da EFS al módem) → `pd-mapper` (con los 6 .jsn
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
