# Port Kali NetHunter Pro — motorola-rhodep (FUNCIONA)

> **OUTDATED — HISTORICAL.** This document (in Spanish) describes an earlier
> state of the port: the v47 image, IPA disabled, audio still pending. It is
> kept for its detailed walkthrough of the build and install internals, but it
> does **not** reflect the current state, and some paths it references
> (`build/`, `img/`, `../_common/...`) do not exist in a clean clone. For the
> current, verified build process see [`docs/BUILD.md`](BUILD.md); for what
> works today see the root [`README.md`](../README.md).

Kali Linux Rolling 2026.3 (NetHunter Pro / Phosh) sobre kernel mainline
**7.2.0-rc5**, en el Motorola Moto G82 5G (**motorola-rhodep**, SoC **SM6375**).
Instalado en la particion `userdata` con el MISMO esquema que pmOS (un disco con
GPT interno, sector logico 4096). Este README es autocontenido: con esto y los
archivos de `img/` + `build/` se puede reinstalar y continuar sin contexto previo.

Leer TAMBIEN `../_common/README-rhodep-KERNEL.md` = doc tecnica del KERNEL
(los 26 patches, como se compila, gotchas de arranque del bootloader Motorola).
El kernel es COMPARTIDO entre pmOS y Kali; solo cambia el `.config` (Kali agrega
~58 simbolos NetHunter + `MODULE_ALLOW_BTF_MISMATCH`, ver abajo).

================================================================================
## 0. DATOS DUROS DEL DEVICE (no inventar, usar estos)
================================================================================
- Version kernel: `7.2.0-rc5`  (uname). Flavor pmaports: `linux-motorola-rhodep`.
- pmaports (fuente del kernel): 
  `~/.local/var/pmbootstrap/cache_git/pmaports/device/testing/linux-motorola-rhodep`
  (26 patches 0001-0026 + APKBUILD + config-motorola-rhodep.aarch64)
- Particiones del telefono (confirmado con lsblk, UFS):
  - `userdata` = `/dev/sde26` = `/dev/disk/by-partlabel/userdata` (~116 GB) ← aca va TODO Kali
  - `modem_a`  = `/dev/disk/by-partlabel/modem_a` (ext4, firmware vendor en `/image/*.mdt`)
  - `boot_a`   = `/dev/disk/by-partlabel/boot_a` (donde se flashea el boot.img)
  - `super`    = `/dev/sde25` (8G, vendor/system Android; NO se usa)
  - slot activo: `_a`
- Rootfs Kali (dentro del disco GPT en userdata):
  - LABEL=`rootfs`  PARTLABEL=`pmOS_root`
  - **UUID = `d3f420ff-55cd-4e90-aba9-adf089dcf1e4`**  ← lo usa el cmdline del boot
- Disco GPT de Kali (kali-userdata.img), sector logico **4096**:
  - p1 `pmOS_boot` vfat 256 MiB, inicio sector 256  (offset 1048576 bytes) — vacio, no se usa
  - p2 `pmOS_root` ext4 (Kali), inicio sector 65792 (offset **269484032** bytes)
- Usuario Kali por defecto: **kali / 1234**  (root via `sudo su`)
- USB gadget del sistema arrancado: IP `172.16.42.1` (SSH kali@).
- USB gadget del RESCATE: IP `172.16.42.1` (TELNET puerto 23, NO ssh).

================================================================================
## 1. ESTADO: FUNCIONAL Y PERSISTENTE (verificado tras reboot, 2026-08-07)
================================================================================
Todo arranca SOLO en cada boot (sin comandos manuales):
- **Phosh** (GUI) — llega a la pantalla de bloqueo/login
- **Display** panel NT37701 DSI+DSC — `/dev/dri/card0` presente
- **WiFi** WCN3990/ath10k_snoc — `wlan0` conecta; `nmcli device wifi list` escanea OK
- **Bluetooth** WCN3990
- **remoteprocs** modem/adsp/cdsp = `running`
- GPU Adreno 619, tactil Goodix, bateria, USB, botones (mismo kernel que pmOS)
- Docker + modulos NetHunter (WiFi USB inject, BadUSB gadget, CAN, SDR, NFS)
- **USB host/OTG + WiFi USB**: TP-Link RTL8188EUS -> wlan1 con monitor mode
  (el interno WCN3990 no puede; el USB si). Ver §7b. Paquete rhodep-usb-otg.

Limitacion (igual que pmOS): monitor/inject del WiFi INTERNO es inviable
(firmware WCN3990 reporta `raw 0`). Para inject real usar adaptador WiFi USB
externo (drivers rt2800usb/rtl8187/ath9k_htc/etc. YA compilados en el kernel).

================================================================================
## 2. IMAGENES FINALES (img/) — QUE FLASHEAR
================================================================================
| Archivo | Que es | Como se usa |
|---|---|---|
| `rescue-boot.img` | Boot RESCATE: kernel+initramfs pmOS + `pmos.debug-shell`. Levanta telnet 172.16.42.1 SIN montar root. IMPRESCINDIBLE para escribir userdata y salir de cualquier bootloop. | `fastboot flash boot_a rescue-boot.img` |
| `kali-userdata.img` (5.1 GB raw) | Disco GPT (p1 pmOS_boot + p2 pmOS_root=Kali), sector 4096. Contiene TODO Kali. | Se escribe a userdata por **dd** (fastboot NO puede, ver §3). |
| `kali-boot-v47.img` | boot.img de Kali: kernel v47 (Image PLANO + DTB appended) + initramfs pmOS. cmdline monta el rootfs Kali por UUID. | `fastboot flash boot_a kali-boot-v47.img` |
| `linux-image-7.2.0-rc5_7.2~rc5-rhodep2_arm64.deb` | Kernel v47 (con BTF-mismatch permitido). YA instalado en el rootfs. Para actualizar kernel dentro de Kali. | `dpkg -i` dentro de Kali |
| `rhodep-modem-support_1_arm64.deb` | Servicios modem/WiFi (mount modem_a, symlinks fw, arranque remoteprocs, ath10k-late, rmtfs flags). YA instalado. | `dpkg -i` dentro de Kali |

`_intentos-viejos/` = imagenes de iteraciones fallidas previas. IGNORAR.
CHECKSUMS.txt tiene los md5 de las imagenes finales.

================================================================================
## 3. INSTALAR DESDE CERO (procedimiento EXACTO, probado)
================================================================================
IMPORTANTE: `fastboot flash userdata` da SIEMPRE "permission denied" en este
bootloader (no hay fastbootd: `fastboot reboot fastboot` -> "Failed to boot into
userspace fastboot"). userdata SOLO se escribe por **dd desde un Linux corriendo
en el telefono** (pmOS, o el propio Kali, o un rescate con red+sshd/nc).

### Metodo A — desde un pmOS que arranque (el que se uso esta sesion)
Si el telefono todavia tiene pmOS booteable:
```
# desde la Mac (kali-userdata.img esta en img/):
ssh -t user@172.16.42.1 'sudo dd of=/dev/disk/by-partlabel/userdata bs=4M conv=fsync' < kali-userdata.img
#   -> escribe el disco GPT de Kali al INICIO de userdata (pisa pmOS, es lo buscado)
#   -> el 'ssh -t' da terminal para el sudo. Tarda ~1-2 min (5.1 GB).
# luego:
fastboot flash boot_a kali-boot-v47.img
fastboot --set-active=a && fastboot reboot
```

### Metodo B — desde el RESCATE (cuando no hay ningun OS booteable)
El rescate (rescue-boot.img) da TELNET sin sshd. Para meter los 5.1GB por red:
```
fastboot flash boot_a rescue-boot.img
fastboot --set-active=a && fastboot reboot     # esperar ~30s
telnet 172.16.42.1 23                          # entrar al debug-shell (root)
# en el telefono (rescate), levantar un receptor con nc y escribir a userdata:
#   (el rescate de pmOS trae busybox nc)
nc -l -p 5555 | dd of=/dev/disk/by-partlabel/userdata bs=4M
```
```
# desde la Mac, mandar el .img:
nc 172.16.42.1 5555 < kali-userdata.img
```
Si el rescate no tiene `nc`, alternativa: usar el boot de pmOS normal para
arrancar pmOS y usar Metodo A. (pmOS reinstalable desde
`../postmarketos/` + la imagen completa, ver §7.)
Cuando termine el dd:
```
fastboot flash boot_a kali-boot-v47.img
fastboot --set-active=a && fastboot reboot
```

### Primer arranque
Tarda: el initramfs pmOS hace `resize2fs` del rootfs Kali a ~107G + primer
systemd + Phosh. Pantalla negra/logo varios minutos = NORMAL. Vibra antes del
switch_root (initramfs pmOS). Luego levanta `172.16.42.1`, SSH kali/1234.

### Ajustes post-install (YA aplicados en el rootfs actual; si se reinstala desde
### el rootfs base limpio, hacerlos):
```
sudo su
systemctl mask droid-juicer.service        # se cuelga (disenado para dual-boot Android)
systemctl mask systemd-repart.service      # falla, innecesario
dpkg -i /tmp/rhodep-modem-support_1_arm64.deb
systemctl daemon-reload
```

================================================================================
## 4. LOS 6 BLOQUEOS QUE SE RESOLVIERON (causa -> fix). NO repetir.
================================================================================
1. **Esquema de userdata**: NO es ext4 plano. Es un **disco con GPT interno,
   sector logico 4096**, 2 particiones (pmOS_boot + pmOS_root). El initramfs hace
   `losetup -Pf --sector-size 4096 <userdata>` y monta la p2 por UUID. Si haces
   un ext4 plano NO arranca.

2. **`fastboot flash userdata` = permission denied** (bootloader Motorola, sin
   fastbootd). Confirmado que ni con vbmeta AVB-disable deja. userdata SOLO por dd.

3. **`rootwait` faltaba** en el cmdline -> kernel panic si userdata/loop tarda.
   (En el boot final usamos el initramfs de pmOS que ya espera; si armas uno con
   initramfs-tools de Debian, agregar `rootwait`.)

4. **BTF mismatch (EL bloqueo grande)**: el kernel se compilaba con
   `CONFIG_DEBUG_INFO_BTF_MODULES=y` SIN `CONFIG_MODULE_ALLOW_BTF_MISMATCH`.
   Alpine/pmOS NO valida BTF de modulos; Kali/Debian SI -> TODOS los modulos
   fallaban al cargar: `failed to validate module [X] BTF: -22`. Rompia refgen
   (=> DSI => sin display) y qrtr (=> sin modem/servicios/WiFi).
   **FIX: `CONFIG_MODULE_ALLOW_BTF_MISMATCH=y`** en el config del kernel (v47).
   (linea ~11868 de config-motorola-rhodep.aarch64, junto a DEBUG_INFO_BTF*).

5. **droid-juicer colgaba el boot**: servicio de NetHunter que extrae firmware de
   particiones Android; sin Android se queda "running" para siempre -> bloquea
   plymouth-quit-wait -> graphical.target nunca llega -> Phosh no arranca.
   **FIX: `systemctl mask droid-juicer.service`**.

6. **Firmware modem/WiFi**: el kernel pide `qcom/sm6375/motorola/rhodep/modem.mbn`
   (+ adsp.mbn, cdsp.mbn) pero la particion `modem_a` los tiene como `.mdt` +
   segmentos `.bNN` en `/image/`. El WiFi WCN3990 vive DENTRO del modem: sin el
   remoteproc modem `running`, NO hay WiFi. **FIX** (paquete rhodep-modem-support):
   - montar `modem_a` en `/readonly/firmware` (ro)
   - symlink `.mbn -> .mdt` y los `.bNN` en el path que busca el kernel
   - arrancar remoteprocs (adsp, luego modem, luego cdsp) con firmware presente
   - `rmtfs` con flags `-P -s` (SIN `-r`: en rmtfs `-r` significa read-ONLY y
     descarta las escrituras EFS a un shadow en RAM; ver
     docs/interconnect-sm6375-wip/EFS-AND-XTRA.md) ; `ath10k-late` carga
     ath10k_snoc DESPUES de que
     aparece QMI 69 (wlfw). ath10k_snoc va BLACKLISTED para no autocargar temprano.

================================================================================
## 5. COMO SE RECONSTRUYE CADA ARTEFACTO (si se pierde algo)
================================================================================

### 5.1 Kernel .deb (linux-image) desde el apk de pmbootstrap
El kernel se compila con pmbootstrap (ver README-rhodep-KERNEL.md §4). Con
`MODULE_ALLOW_BTF_MISMATCH=y` en el config. El apk queda en:
`~/.local/var/pmbootstrap/packages/edge/aarch64/linux-motorola-rhodep-7.2_rc5-r0.apk`
Convertir apk -> .deb linux-image (layout que espera NetHunter/bootloader.sh):
```
KVER=7.2.0-rc5 ; SOC=sm6375 VENDOR=motorola MODEL=rhodep
mkdir -p PKG/DEBIAN PKG/boot PKG/usr/lib/linux-image-$KVER/qcom PKG/usr/lib/modules
tar xzf <apk> -C /tmp/apkx
cp /tmp/apkx/boot/vmlinuz PKG/boot/vmlinuz-$KVER            # Image PLANO (no gz!)
cp /tmp/apkx/boot/dtbs/qcom/sm6375-motorola-rhodep.dtb \
   PKG/usr/lib/linux-image-$KVER/qcom/$SOC-$VENDOR-$MODEL.dtb
cp -r /tmp/apkx/usr/lib/modules/$KVER PKG/usr/lib/modules/$KVER
rm -f PKG/usr/lib/modules/$KVER/build PKG/usr/lib/modules/$KVER/source
# DEBIAN/control: Package: linux-image-7.2.0-rc5 ; Depends: kmod, linux-base
# DEBIAN/postinst: depmod -a $KVER ; update-initramfs -u -k $KVER
fakeroot dpkg-deb --build -Zxz PKG linux-image-...deb
```

### 5.2 firmware .deb (WCN3990/GPU + .jsn del modem, los blobs chicos)
Origen: `pmaports/device/testing/firmware-motorola-rhodep/fw/` -> a `/lib/firmware/`.
```
mkdir -p PKG/lib/firmware ; cp -r fw/. PKG/lib/firmware/
# control: Package: firmware-motorola-rhodep ; Section: non-free/kernel
fakeroot dpkg-deb --build -Zxz PKG firmware-...deb
```
OJO: los blobs GRANDES del modem/adsp/cdsp NO van aca; vienen de la particion
`modem_a` en runtime (ver bloqueo #6 / paquete rhodep-modem-support).

### 5.3 rhodep-modem-support .deb (servicios modem/WiFi)
Fuentes en `build/persist/`: rhodep-fw-symlinks.sh, rhodep-modem-fw.service,
readonly-firmware.mount, ath10k-late.service, ath10k-late.conf, rmtfs-rhodep.conf.
Estructura del .deb:
- `/usr/local/sbin/rhodep-fw-symlinks.sh` (symlinks .mbn->.mdt + arranca remoteprocs)
- `/usr/lib/systemd/system/{readonly-firmware.mount,rhodep-modem-fw.service,ath10k-late.service}`
- `/usr/lib/systemd/system/rmtfs.service.d/10-rhodep.conf` (ExecStart= ; =/usr/bin/rmtfs -P -s)
  NOTA: el drop-in de `/etc/systemd/system/` que instala userspace/modem/install.sh
  tiene el MISMO nombre de archivo y por lo tanto REEMPLAZA a este, no se fusiona.
- `/usr/lib/modprobe.d/ath10k-late.conf` (blacklist ath10k_snoc)
- `/usr/lib/tmpfiles.d/rhodep-modem.conf` (d /readwrite, /readwrite/datablock, /readonly/firmware)
- symlinks en multi-user.target.wants/ para habilitar los 3 units
- postinst: systemctl daemon-reload + enable de los units + rmtfs/pd-mapper/tqftpserv/qrtr-ns
Rearmar: `fakeroot dpkg-deb --build PKG rhodep-modem-support_1_arm64.deb`

### 5.4 kali-userdata.img (disco GPT con Kali) desde cero
Requiere el rootfs Kali (ver §6). Pasos (sector 4096!):
```
# 1. rootfs Kali ext4 (ya con kernel.deb + firmware.deb + qcom-support-common +
#    rhodep-modem-support instalados, ver §6) -> archivo ext4 'rootfs.ext4'
# 2. crear disco GPT sector-4096 con losetup -b 4096:
LD=$(losetup -f --show -b 4096 disco.img)
sgdisk --zap-all $LD
sgdisk -n 1:256:65791     -c 1:pmOS_boot -t 1:8300 \    # 256MiB, sectores 4k
       -n 2:65792:0       -c 2:pmOS_root -t 2:8300 $LD
losetup -d $LD
# 3. dd del ext4 en el offset de p2 (sector 65792 * 4096 = 269484032):
dd if=rootfs.ext4 of=disco.img bs=1M seek=$((269484032/1024/1024)) conv=notrunc
# 4. vfat vacio en p1 (offset 1048576) opcional
```
El UUID del ext4 debe ser el del cmdline (d3f420ff...) o regenerar boot con el
UUID nuevo (`blkid` del ext4).

### 5.5 kali-boot-v47.img (boot que arranca Kali)
Kernel v47 (Image PLANO) + DTB appended + **initramfs de pmOS** (NO el de Debian).
El initramfs de pmOS hace mount_subpartitions (losetup -Pf --sector-size 4096) y
busca el rootfs por `pmos_root_uuid`. cmdline EXACTO:
```
earlycon pmos_root_uuid=d3f420ff-55cd-4e90-aba9-adf089dcf1e4 pmos_rootfsopts=defaults \
panel_novatek_nt37701.clk_scale=100 ignore_loglevel watchdog_thresh=5
```
Armar con `../_common/scripts/mkbootv2b.py`:
```
# kernel = vmlinuz + dtb (concatenado). initramfs = el de pmOS (v45_ramdisk /
# del chroot_rootfs). mkbootv2b.py hace el append del dtb solo.
python3 ../_common/scripts/mkbootv2b.py vmlinuz DTB initramfs "<cmdline>" kali-boot-v47.img
```
CRITICO: Image PLANO, NO Image.gz-dtb/EFI_ZBOOT (el bootloader Motorola resetea
con imagen autodescomprimible — ver README-rhodep-KERNEL.md §4.3).

### 5.6 rescue-boot.img
= kernel + initramfs de pmOS (del boot pmOS que arranca, ej boot-v45) con cmdline
del v45 + `pmos.debug-shell` agregado. Levanta telnet sin montar root.

================================================================================
## 6. COMO SE CONSTRUYE EL ROOTFS KALI (debos) — reproducir
================================================================================
Entorno: container Ubuntu 24.04 arm64 (NATIVO arm64, sin qemu), con sudo, SIN KVM.

### 6.1 Toolchain (una vez)
```
sudo apt install -y pkg-config libglib2.0-dev libostree-dev debootstrap \
     android-sdk-libsparse-utils mkbootimg bmap-tools jq squashfs-tools-ng \
     fakeroot systemd-container parted gdisk fdisk
# Go (para debos):
wget https://go.dev/dl/go1.23.4.linux-arm64.tar.gz ; sudo tar -C /usr/local -xzf go*.tar.gz
export PATH=/usr/local/go/bin:$HOME/go/bin:$PATH
go install github.com/go-debos/debos/cmd/debos@latest
sudo pip3 install --break-system-packages yq    # trae tomlq (a nivel sistema, root lo usa)
```
### 6.2 Repo
```
git clone https://gitlab.com/kalilinux/nethunter/build-scripts/kali-nethunter-pro /tmp/knh-pro
# copiar nuestros archivos:
cp build/wip.toml           /tmp/knh-pro/devices/qcom/configs/wip.toml
cp build/packages/*.deb     /tmp/knh-pro/devices/qcom/packages/   # kernel + firmware
```
`wip.toml` define rhodep: chipset=sm6375, vendor=motorola, model=rhodep, offsets
bootimg (base0, kernel@0x8000, ramdisk@0x1000000, tags@0x100, pagesize4096, v2).
### 6.3 Correr debos (nspawn en container necesita estos flags)
```
# wrapper que agrega --disable-fakemachine (no hay KVM):  build/binwrap/debos
export PATH="$PWD/build/binwrap:/usr/local/go/bin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
cd /tmp/knh-pro
sudo mkdir -p /dev/disk   # nspawn lo bind-mountea
sudo env PATH="$PATH" HOME=/root SYSTEMD_NSPAWN_UNIFIED_HIERARCHY=1 ./build.sh -t qcom-wip
#   -> rootfs.yaml corre COMPLETO -> rootfs-arm64-phosh-nonfree.tar.xz (~920M, en build/)
#   -> image.yaml FALLA en el particionado (container tiene loop max_part=0)
```
### 6.4 Terminar a mano (porque image.yaml no particiona en el container)
```
sudo tar xJf rootfs-arm64-phosh-nonfree.tar.xz -C /tmp/rootfs-rhodep
# montar proc/sys/dev, arreglar DNS (echo 'nameserver 127.0.0.11' > etc/resolv.conf
#   -- el DNS interno de Docker; el /etc/resolv.conf del rootfs es symlink roto),
# chroot (arm64 nativo, sin qemu):
chroot /tmp/rootfs-rhodep apt-get install -y --no-install-recommends \
    initramfs-tools qcom-support-common
chroot ... dpkg -i /srv/linux-image-...deb /srv/firmware-...deb /srv/rhodep-modem-support...deb
chroot ... systemctl enable qrtr-ns rmtfs pd-mapper tqftpserv
chroot ... systemctl mask droid-juicer systemd-repart
# empaquetar el dir a ext4 (mkfs.ext4 + cp -a + resize2fs -M) -> rootfs.ext4
# luego §5.4 (disco GPT) y §5.5 (boot).
```

Artefactos guardados en `build/`:
- `packages/*.deb` (kernel v47, firmware, rhodep-modem-support)
- `persist/*` (fuentes de los units/scripts de rhodep-modem-support)
- `rootfs-arm64-phosh-nonfree.tar.xz` (rootfs Kali base de debos, 920M)
- `wip.toml`, `binwrap/` (wrappers debos + systemd-nspawn)

================================================================================
## 7. RECUPERACION / ROLLBACK
================================================================================
- Cualquier bootloop: `fastboot flash boot_a rescue-boot.img` -> telnet 172.16.42.1
  (root, sin montar root). Desde ahi dd a userdata lo que quieras.
- Volver a pmOS: escribir la imagen pmOS completa a userdata. La imagen esta en
  `~/.local/var/pmbootstrap/chroot_native/home/pmos/rootfs/motorola-rhodep.img`
  (5.06G, disco GPT pmOS). dd por el mismo metodo (§3) y flashear
  `../postmarketos/img/boot-v45-DOCKER.img`.
- Verificar el disco Kali sin arrancarlo (desde rescate o pmOS):
  `losetup -Pf --sector-size 4096 /dev/disk/by-partlabel/userdata`
  `blkid /dev/loop0p2`  (debe dar UUID d3f420ff... LABEL rootfs)
  `mount /dev/loop0p2 /mnt` (ver el rootfs Kali).

================================================================================
================================================================================
## 7b. USB HOST / OTG + WiFi USB (TP-Link RTL8188EUS) — FUNCIONA
================================================================================
Objetivo: usar un adaptador WiFi USB (monitor+inject, que el WCN3990 interno no
puede). LOGRADO. Detalle del descubrimiento (para no repetir):

- El puerto USB-C esta en modo `device` (gadget/SSH) por defecto. Cambiar a host:
  `echo host > /sys/class/usb_role/4e00000.usb-role-switch/role`
  -> arranca el xHCI, aparecen los root hubs. Pero el TP-Link NO se enumeraba.
- CAUSA: en modo host el kernel NO activa el **VBUS** (no hay type-c/tcpm driver
  en mainline para este device; `/sys/class/typec` vacio). Sin 5V el adaptador
  no prende. En Android el PMIC/charger da VBUS solo.
- El VBUS lo genera el **charger SGM41542** (I2C bus 0, addr 0x3b) en modo OTG
  boost. El driver mainline `bq256xx` (que usamos para el SGM41542) SOLO LEE el
  estado OTG, NO lo activa. Hay que setear el bit por I2C:
  - reg `0x01` (CHRG_CTRL_1) bit5 = OTG_CONFIG. `0x1a` -> `0x3a` (| 0x20).
  - reg `0x02` bit7 BOOST_LIM ya = 1 (0x94). reg `0x06` BOOSTV ya = 5.15V (0xe6).
  ```
  i2cset -f -y 0 0x3b 0x01 0x3a     # activa OTG boost -> VBUS 5V -> TP-Link prende
  ```
  (el bit SE MANTIENE, el driver bq256xx no lo pisa — verificado.)
- Con VBUS, el RTL8188EUS enumera. Driver `rtl8xxxu` (compilado) + firmware
  `rtlwifi/rtl8188eufw.bin` (paquete `firmware-realtek`) -> aparece `wlan1`.
  `airmon-ng` lo lista; `iw list` muestra monitor. Monitor/inject REAL por USB.

### Persistencia: paquete `rhodep-usb-otg_1_arm64.deb` (en img/ y build/packages/)
Instala:
- `/usr/local/sbin/usb-otg-host.sh on|off` (host+OTG / device)
- `usb-otg-host.service` (al boot: modo host + OTG on) — habilitado
- `usb-otg-keepalive.timer` (cada 30s re-asegura el bit OTG, por si acaso) — habilitado
- Depends: i2c-tools. Fuentes en `build/persist-usb/`.
```
dpkg -i rhodep-usb-otg_1_arm64.deb    # + firmware-realtek para el rtl8188eufw.bin
```
OJO: modo host permanente = **se pierde SSH por USB gadget** (usar SSH por WiFi).
Para volver a gadget: `usb-otg-host.sh off` o `systemctl disable usb-otg-host`.

PERSISTENCIA USB/OTG CONFIRMADA (reboot 2026-08-07): tras reiniciar, arranca solo
`role=host`, `reg0x01=0x3a` (OTG), `wlan1` rtl8xxxu (airmon-ng lo lista). Sin
comandos manuales.

NOTA inject: `rtl8xxxu` (mainline) hace monitor OK; si la INYECCION falla, usar
el driver `8188eu` (DKMS aircrack-ng/Realtek) que es mejor para ese chip.

================================================================================
## 7c. ENERGIA: carga / OTG (puerto USB-C unico) + proteccion bateria
================================================================================
### Carga vs OTG (paquete rhodep-usb-otg v3)
El puerto USB-C es UNICO (carga + datos + OTG). Sin driver type-c en mainline el
kernel NO detecta que conectaste. En modo OTG el charger entra en boost y NO
sensa el cargador entrante (reg0x08 VBUS_STAT=OTG), asi que OTG->carga NO puede
ser automatico. Estrategia: **DEFAULT = carga**, OTG on-demand con `otg`:
```
otg on      # host + VBUS boost (para TP-Link/BT/adaptadores USB). Deja de cargar.
otg off     # device + carga (DEFAULT; tambien al boot via otg-default-charge.service)
otg status  # role, OTG, charger status, %bat, regs
```
`otg off` = reg0x01 bit5(OTG_CONFIG) a 0 -> carga. `otg on` = bit5 a 1 -> VBUS 5V.
Verificado: cargando da `role=device OTG=no charger=Charging reg01=0x1a reg08=0xd4`.

### Proteccion de bateria/temperatura — DOS CAPAS
1. **Lado caliente + throttle CPU/GPU: en el KERNEL** (DTS, patches 0020/0021/0024).
   thermal zones + CW2217 como sensor OF + SGM41542 como cooling device + battery
   thermal zone: throttle carga a 45C, corte a 50C. Mismo kernel v47 -> YA activo
   en Kali (todas las thermal_zone* se leen, ~22-24C en reposo, PMIC 37C).
2. **Lado frio: userspace** (paquete `rhodep-battery-jeita`). El thermal framework
   solo actua en subida; cargar <0C daña la celda (plating de litio). El script
   vigila cw2217-battery/temp cada 30s: <0C corta carga (charge_type=N/A),
   re-habilita a 3C (histeresis). Servicio `rhodep-battery-jeita.service` enabled.
   Script identico al de pmOS (fuente: pmaports device-motorola-rhodep).

Instalar ambos en Kali: `dpkg -i rhodep-usb-otg_3_arm64.deb rhodep-battery-jeita_1_arm64.deb`
(+ i2c-tools). Estado sano verificado: bat 98% Charging, Good, temps normales.

================================================================================
## 7d. ACTUALIZAR KALI + INSTALAR TODO EL TOOLSET (uso diario)
================================================================================
El rootfs se instalo MINIMAL (minbase + phosh). Para tener Kali completo y
actualizado, seguir ESTE procedimiento (apt estandar PERO protegiendo lo custom).

### Contexto de seguridad (por que no un apt upgrade "a lo bruto")
- Nuestro kernel `linux-image-7.2.0-rc5` (7.2~rc5-rhodep2) y los paquetes
  `firmware-motorola-rhodep`, `rhodep-modem-support`, `rhodep-usb-otg`,
  `rhodep-battery-jeita` son CUSTOM (dpkg -i, no de repo). apt NO los actualiza,
  pero hay que ponerlos en HOLD para que un metapaquete no instale otro kernel.
- El boot.img flasheado en boot_a tiene el initramfs de pmOS EMBEBIDO (es lo que
  arranca). `apt`/`update-initramfs` regenera /boot/initrd.img-* en el rootfs
  pero NO re-flashea boot_a (Kali no tiene flash_kernel_on_update). => actualizar
  NO cambia el arranque. Seguro.
- droid-juicer y systemd-repart estan MASKED (si no, cuelgan el boot). Un upgrade
  podria tocarlos: re-verificar el mask al final.

### Procedimiento (por SSH, con cargador conectado, va a tardar/bajar varios GB)
```
sudo su
# 1. HOLD de lo custom (que apt nunca los toque)
apt-mark hold linux-image-7.2.0-rc5 firmware-motorola-rhodep \
              rhodep-modem-support rhodep-usb-otg rhodep-battery-jeita

# 2. actualizar indices y el sistema
apt update
apt full-upgrade -y

# 3. instalar el toolset de Kali (elegir UNO):
apt install -y kali-linux-default          # set estandar (recomendado en movil)
#   o kali-linux-large  (mucho mas grande)  / kali-linux-headless (sin GUI extra)
# tools sueltas utiles que faltaban:
apt install -y python3-requests hcxdumptool hcxtools

# 4. re-asegurar los servicios que deben quedar OFF/ON
systemctl mask droid-juicer.service systemd-repart.service
systemctl enable rhodep-battery-jeita.service otg-default-charge.service \
                 usb-otg-... 2>/dev/null   # (ya vienen enabled; por las dudas)
systemctl daemon-reload

# 5. limpiar
apt autoremove -y ; apt clean
```
NO reinicies con el rescate a mano; un reboot normal alcanza. Igual, TENER a mano
`rescue-boot.img` por si algo del upgrade rompiera userspace (recuperas por telnet).

### Verificar despues del upgrade (antes de confiar)
```
uname -r                                   # 7.2.0-rc5 (nuestro kernel intacto)
apt-mark showhold                          # los 5 custom en hold
systemctl is-enabled rhodep-battery-jeita otg-default-charge
otg status                                 # charger=Charging, OTG=no
iw dev | grep wlan0                        # wifi interno OK
# si conectas TP-Link: otg on ; airmon-ng
```
Si el `df -h /` se queda sin espacio con kali-linux-large, usar kali-linux-default.
El rootfs es de ~107G (resize2fs al primer boot), espacio de sobra normalmente.

### OJO espacio: el rootfs crecio a 107G en el primer boot (resize2fs). Confirmar:
`df -h /`  -> deberia ser ~107G. Si dice 4.8G, el resize no corrio; forzar:
`resize2fs /dev/loop0p2` (con el fs montado se agranda online).

### UPGRADE VERIFICADO (2026-08-07)
`apt full-upgrade` + `apt install kali-linux-default` corrido OK. Post-upgrade:
uname 7.2.0-rc5 (kernel intacto), apt-mark showhold = los 5 custom, otg status
`charger=Full bat=100%`, wifi OK, JEITA enabled. Toolset completo funcionando
(BlueDucky, etc.). El hold protegio el kernel/paquetes custom perfectamente.

### NOTAS de uso (userspace, NO del port)
- **Firefox**: la ventana abre pero las paginas no cargan -> era la VERSION de
  firefox-esr de ese momento (bug de red del navegador, no del port). Workaround:
  usar **chromium** (anda perfecto) o actualizar/downgrade firefox. NO es GPU ni DNS
  del sistema (CLI navega bien).
- Chromium: funciona OK.

================================================================================
## 8. PENDIENTES / TODO
================================================================================
- Datos moviles: **el motivo anotado aca es viejo y se corrige.** No es que
  falte el driver de interconnect: ese driver existe y anda (parches 0027/0028/
  0046). Los datos moviles funcionan —~24 Mbit/s medidos, IP publica, IPv6— y lo
  que falta es **estabilidad**: con `ipa.ko` cargado *y* el modem enganchado a
  LTE, el SoC se reinicia por watchdog cada 3 a 10 minutos, en silencio. Por eso
  el port envia `ipa.ko` fuera del arranque. Ver KERNEL-TECHNICAL.md §7.6 y
  `docs/watchdog-ipa-lte-wip/HANDOFF.md`.
- Empaquetar como imagen unica flasheable estilo NetHunter Pro oficial (correr
  debos image.yaml completo en un host con KVM/Docker, con los .deb de build/).
- Monitor/inject WiFi interno: inviable (firmware WCN3990 raw 0). Usar USB.
- Audio, sensores, GPS, NFC, camara: **esta linea quedo vieja y se corrige.**
  Estado real (ver KERNEL-TECHNICAL.md §7 y el README raiz):
  - **Audio: FUNCIONA.** Parlante, auricular, jack de 3.5 mm con deteccion de
    conector y microfono (AMIC3), via PipeWire. Falta audio *en llamada*, que es
    un driver que no existe en mainline (q6voice), no un bug.
  - **Sensores: FUNCIONAN.** Acelerometro, giroscopo, magnetometro, proximidad y
    luz ambiente por SSC/ADSP sobre FastRPC, con `iio-sensor-proxy`, asi que la
    rotacion automatica de pantalla anda. No necesito ningun cambio de kernel.
  - **NFC: lee tarjetas.** Samsung S3NRN4V con el `s3fwrn5` de mainline mas los
    parches 0101-0105. La emulacion de tarjeta no anda (esa maquina de estados
    vive en la libnfc-nci de Android).
  - **GPS: hay ubicacion, no hay satelites.** El paquete `rhodep-gnss` da
    posicion por WiFi y por celda como NMEA hacia gpsd y geoclue -- 22 m y 250 m
    medidos, con el SIM sin provisionar. Lo que **no** hay es fix satelital:
    pedirlo (`standalone`) reinicia el SoC en menos de 100 ms. Ver
    `docs/GPS-USERSPACE.md` y `docs/interconnect-sm6375-wip/GNSS-SM6375.md`.
  - **Camara: sigue pendiente.** Solo estan las alimentaciones (el driver del
    PMIC FAN53870 anda y registra sus 7 LDOs); no hay camino de imagen todavia.
  - **Huella: sigue pendiente**, HAL propietario de Focaltech.
- Verificar/pulir arranque de otros perifericos en Kali (bateria UI, audio).
