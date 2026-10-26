# WiFi Pineapple Pager — correr el FIRMWARE REAL bajo qemu-user

Este es el enfoque "Pandora": en vez de reimplementar el display, corremos el
**firmware Go REAL del Pager** (`/pineapple/pineapple`, MIPS) bajo qemu-user, y
le damos el hardware virtual que espera (framebuffer 480x222, netlink/radios,
sysfs). El firmware renderiza SU display real pixel-perfect (fuentes PixFont,
temas, animaciones reales) a un framebuffer que mostramos en la pantalla del G82.

## Estado: FUNCIONA (arranca + dibuja)
El firmware arranca completo bajo el qemu parcheado: pasa netlink, inicializa el
framebuffer, carga el tema wargames desde ROM, y renderiza:
- Boot screen WarGames real (INITIALIZING SYSTEM, mapas, misiles, tic-tac-toe)
- Setup wizard real (WELCOME HACKER, menu Welcome/Notices/PIN/Password/...)
Llega a "UI Startup complete in 503ms" y queda en el event loop.

## Hallazgos clave (del RE)
1. **Framebuffer**: el firmware hace open("/dev/fb0") + mmap DIRECTO de
   **213120 bytes = 480x222x2 (RGB565)**. NO usa ioctl FBIOGET_*. Un archivo
   regular de 426240 bytes alcanza (o 213120; verificar). El panel esta montado
   ROTADO 90 (layout 222x480); hay que rotar -90 para verlo landscape.
2. **Netlink / Go no pasa por libc**: el firmware usa github.com/mdlayher/genetlink
   (nl80211) con socket(PF_NETLINK, NETLINK_GENERIC) directo al kernel (Go runtime,
   NO libc). => LD_PRELOAD es INUTIL. qemu-user ademas RECHAZA NETLINK_GENERIC
   (whitelist en do_socket). SOLUCION: parchear qemu-user (ver patches/).
3. El firmware solo usa netlink para ENUMERAR radios al arrancar (GetFamily
   nl80211, GET_WIPHY/GET_INTERFACE de phy0/phy1). El ataque real lo delega a
   pineapd via /tmp/pineap_sock. => el fake nl80211 solo responde la enumeracion.
4. Device config: pide /tmp/board.json + /etc/device.config + /usb/device.config
   (JSON con model.id = "hak5,wifi-pineapple-pager").
5. /proc/mounts: el firmware espera "overlayfs:/overlay" (chequeo de overlay).
6. Tema desde ROM: carga de /rom/lib/pager (qemu no sigue symlinks absolutos
   encadenados; materializar con hardlinks - ver prepare.sh).

## Patches (patches/)
- `01-syscall-netlink-mounts.patch`: parche a qemu-8.2.2/linux-user/syscall.c:
  - do_socket: para PF_NETLINK/NETLINK_GENERIC crea un socketpair + lanza el
    responder fake (hilo host) en vez de fallar.
  - do_bind/do_getsockname/do_sendrecvmsg/do_sendto/do_recvfrom: cortocircuito
    para los fd fake (bind no-op, sockaddr_nl sintetico).
  - open_pager_mounts: fake /proc/mounts con overlayfs:/overlay.
- `nl80211-fake.c.inc`: el responder genl/nl80211 (CTRL_CMD_GETFAMILY nl80211 +
  NL80211_CMD_GET_WIPHY/GET_INTERFACE -> phy0=wlan0 station, phy1=wlan1 monitor
  con sus MACs). Se incluye desde syscall.c.

## Scripts (scripts/)
- `prepare.sh`: crea /dev/fb0, sysfs falsos (ieee80211 phy0/phy1 macaddress,
  power_supply, backlight, leds, gpio vibrator, hwmon temp), device JSONs, y
  materializa /rom/lib/pager con hardlinks.
- `run.sh`: prepara el rootfs y corre el firmware bajo el qemu parcheado.
  CAPTURE=N vuelca el framebuffer a PNG tras N segundos.
- `fbrender.py`: decodifica el fb RGB565 222x480 y lo rota a 480x222 (PNG).

## Como construir el qemu parcheado (VALIDADO en aarch64 / G82)
```
# bajar qemu 8.2.2, aplicar los patches, compilar solo el target mipsel-user:
tar xf qemu-8.2.2.tar.xz
cd qemu-8.2.2
cp ../patches/nl80211-fake.c.inc linux-user/
patch -p1 < ../patches/01-syscall-netlink-mounts.patch
# FIX glibc >=2.41/2.43 (gcc nuevo de Kali): qemu 8.2.2 redefine 'struct
# sched_attr' que glibc nuevo ya trae -> envolver esa definicion en linux-user/
# syscall.c con:  #ifndef SCHED_ATTR_SIZE_VER0  ...struct sched_attr{...}... #endif
./configure --target-list=mipsel-linux-user --disable-system --disable-tools \
            --disable-docs --disable-werror
ninja -C build qemu-mipsel -j4
# -> build/qemu-mipsel  (ELF aarch64 nativo, ~16MB). Compila en ~5min en el G82.
```
VALIDADO: con este qemu parcheado el firmware arranca en el G82 (pasa netlink +
mounts), inicializa el framebuffer y renderiza el setup wizard real
("WELCOME, HACKER" + menu Welcome/Notices/PIN/Password/Timezone/License).

## ESTADO: COMPLETO Y FUNCIONAL en el G82 (todo validado)
1. **Build aarch64**: HECHO. qemu-8.2.2 parcheado compila en el G82 (fix glibc
   2.43 struct sched_attr). ~16MB, ~5min.
2. **Visor + Input**: RESUELTO via el VIRTUAL PAGER NATIVO del firmware (no evdev).
   El firmware expone: ws :1471/api/pager/display/screen.ws (frames RGBA 480x222)
   + ws :1471/api/pager/input/keys.ws (ArrowUp/Down/Left/Right/Enter(A)/Escape(B)).
   La app `viewer/` muestra el display real en la carcasa amarilla + botones
   tactiles -> keys.ws. Validado: el display navega de verdad.
   (El intento evdev/uinput quedo documentado en INPUT_VISOR_README.md pero NO se
   usa: el kernel del G82 no tiene uinput, y el Go epoll edge-triggered no navegaba.
   El Virtual Pager nativo es la solucion correcta.)
3. **pineapd**: RESUELTO via `scripts/pineap_bridge.py`. El firmware (MIPSel)
   escribe el header del socket LITTLE-endian; nuestro pineapd-pager nativo lo lee
   BIG-endian. El bridge byteswapea el header 8B y reenvia. El firmware pasa de
   "Deferring PineAP" a "Starting PineAP" y conecta al pineapd-pager nativo
   (round-trip msgpack OK). Setup wizard saltado: option setup_complete '1' + eula
   '1' en rootfs/etc/config/system.
4. **Integracion**: HECHA. `pager-qemu.target` (on-demand) orquesta
   pineapd-pager + pineap-bridge + pager-qemu-firmware + pager-qemu-viewer.
   Conflicts con pager-server (modo nativo) para no chocar en :1471. App .desktop
   "WiFi Pineapple Pager (Firmware)". Installer: install-qemu.sh.

## Como usar (modo firmware real)
```
sudo sh install-qemu.sh              # instala a /opt/pineapple-pager/qemu + units + .desktop
sudo systemctl start pager-qemu.target
#   firmware real en :1471, viewer en :8471
#   abrir http://127.0.0.1:8471/  (o la app "WiFi Pineapple Pager (Firmware)")
sudo systemctl stop pager-qemu.target
```
Arrancar el modo qemu para el nativo (pager-server) y viceversa (Conflicts).

## DOS MODOS del Pager (el usuario elige)
- **Nativo (liviano)**: pager.target -> renderer JS + pineapd-pager. :1471.
- **Firmware real (qemu, 100% fiel)**: pager-qemu.target -> firmware Go autentico
  bajo qemu + Virtual Pager + pineapd-pager. :1471 (firmware) + :8471 (viewer).

## Lo unico pendiente (necesita hardware)
Conectar la TP-Link Archer (wlan1) y probar deauth/scan/AP reales. Sin la Archer
el firmware arranca y PineAP conecta, pero el ataque WiFi real no se puede probar.
El canal de control firmware<->pineapd-pager esta vivo (validado con list_interfaces).
Reparto: wlan0 interna = STA+AP (karma/evil, nunca monitor), wlan1 Archer = monitor/deauth/scan.

## Reparto de radios (igual que todo el port)
El firmware delega WiFi a pineapd. Nuestro pineapd-pager: wlan0 interna = STA+AP
(karma/evil, nunca monitor), wlan1 Archer = monitor/deauth/scan.
