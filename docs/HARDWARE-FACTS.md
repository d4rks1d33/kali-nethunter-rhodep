# Hardware facts (motorola-rhodep) — exact values, do not guess

- Device: Motorola Moto G82 5G, XT2225-1, codename `rhodep` (Motorola "blair"/"holi")
- SoC: Qualcomm SM6375 (Snapdragon 695 5G). CPU: 2x A78 + 6x A55, aarch64
- GPU: Adreno 619 (holi variant, GMU wrapper — not a real GMU)
- Display: AMOLED 1080x2400, Novatek NT37701, DSI + DSC 1.1 command mode
- Touch: Goodix GT9916S over SPI
- WiFi/BT: WCN3990 (NOT WCN6750)
- Battery: 5000 mAh. Gauge CellWise CW2217 (I2C 0x64). Charger SGMicro SGM41542
  (I2C bus 0, addr 0x3b; bq25601 register clone)

## Kernel / OS
- Kernel version string: `7.2.0-rc5`
- Boot image: Android boot v2, FLAT Image + appended DTB. offsets: base 0,
  kernel 0x8000, ramdisk 0x1000000, tags 0x100, pagesize 4096.
- DTB name expected by tooling: `qcom/sm6375-motorola-rhodep.dtb`

## Partitions (UFS)
- `userdata` = /dev/sde26 = /dev/disk/by-partlabel/userdata (~116 GB) — the whole
  OS (Kali) lives here as a sector-4096 GPT disk image (pmOS_boot + pmOS_root).
- `modem_a` = /dev/disk/by-partlabel/modem_a (ext4) — vendor firmware in /image/
  (modem/adsp/cdsp as .mdt + .bNN). Mounted ro at /readonly/firmware at runtime.
- `boot_a`  = where the boot.img is flashed. A/B device, active slot `_a`.
- `super`   = /dev/sde25 (8 GB, Android vendor/system; unused by the port).

## Kali root filesystem (inside the userdata GPT disk)
- LABEL=`rootfs`  PARTLABEL=`pmOS_root`
- The boot cmdline references it by `pmos_root_uuid=<UUID>` (regenerate the UUID
  per build with `blkid`, and rebuild the boot image accordingly).
- GPT (sector 4096): p1 pmOS_boot vfat 256 MiB @sector 256 (byte 1048576),
  p2 pmOS_root ext4 @sector 65792 (byte 269484032).

## USB / OTG / charger (single USB-C port)
- USB role switch: `/sys/class/usb_role/4e00000.usb-role-switch/role` (device|host)
- No mainline Type-C driver → VBUS in host mode is NOT auto-enabled. The SGM41542
  charger provides VBUS in OTG boost mode; the mainline bq256xx driver does NOT
  enable it. Enable by I2C: reg 0x01 (CHRG_CTRL_1) bit5 (OTG_CONFIG): 0x1a -> 0x3a.
  reg 0x02 bit7 BOOST_LIM and reg 0x06 BOOSTV (5.15V) already set by defaults.
- In OTG/boost the charger cannot sense an incoming charger (reg 0x08 VBUS_STAT
  reads OTG), so OTG->charge is not automatic. Default to charging; OTG on demand.
