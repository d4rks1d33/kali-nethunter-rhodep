# USB-C on rhodep: the Type-C controller, and why OTG is still manual

This documents what the Type-C port controller on the Moto G82 5G (`rhodep`)
actually does under mainline, what patch 0123 adds, and — more importantly — why
binding it does **not** make host/device switching automatic. It is written from
on-device measurements, not assumptions.

## TL;DR

- The port controller is an **SGM7220**, a register-level clone of the **TI
  TUSB320**, on **i2c bus 1 at address 0x47**. Registers 0x00–0x07 read back the
  ASCII string `TUSB320`; it is a genuine TUSB320-compatible part.
- Patch 0123 binds it with the mainline `extcon-usbc-tusb320` driver and wires a
  `usb-c-connector` to the dwc3 role switch. This works: `/sys/class/typec/port0`
  appears, the chip is bound, and **cable orientation is reported correctly**.
- It does **not** switch the port to host when an OTG adapter is plugged in.
  Automatic OTG remains impossible on this board for two independent reasons:
  1. **CC is not sensed as expected.** In DRP mode the controller reads
     "not attached" (reg 0x09 = 0x30) with an OTG adapter connected, and its
     interrupt line (tlmm 11) never fires — the count in `/proc/interrupts`
     stays at 0.
  2. **VBUS is not the TUSB320's to provide.** In host mode the 5 V that powers
     the attached device comes from the **SGM41542 charger's OTG boost**
     (reg 0x01 bit 0x20), which nothing in the Type-C path toggles. The mainline
     `bq256xx` charger driver only *reads* that state; it exposes no regulator
     for it.

So the manual `otg on|off` control in `packages/rhodep-usb-otg` is still the way
host mode is entered. 0123 is kept for the orientation report and the typec
sysfs infrastructure, not for role switching.

## What patch 0123 adds

A `ti,tusb320` node under the charger i2c bus with the interrupt on tlmm 11
(matching the vendor `sgm7220.dtsi` `intr_gpio`), plus a `usb-c-connector` whose
OF-graph endpoint links to the dwc3 role switch:

```
typec@47 {
    compatible = "ti,tusb320";
    reg = <0x47>;
    interrupts-extended = <&tlmm 11 IRQ_TYPE_EDGE_FALLING>;
    connector {
        compatible = "usb-c-connector";
        label = "USB-C";
        data-role = "dual";
        power-role = "dual";
        port { tusb320_hs_ep: endpoint { remote-endpoint = <&dwc3_hs_ep>; }; };
    };
};
```

The dwc3 node was already prepared for this by patch 0010: `dr_mode = "otg"`,
`usb-role-switch`, `role-switch-default-mode = "peripheral"`. 0123 only adds the
matching `dwc3_hs_ep` endpoint on the dwc3 side. Default role stays peripheral so
the USB gadget (and SSH-over-USB) still comes up on a plain cable.

## What actually happens on the device

With the driver bound:

```
/sys/class/typec/port0 -> .../i2c-1/1-0047/typec/port0
1-0047/driver -> .../extcon-tusb320
extcon_usbc_tusb320 ... typec  (both modules loaded, auto-probed by compatible)
```

Plain cable to a PC (device/sink), the controller reads its state correctly:

```
data_role            : host [device]     # device active
power_role           : source [sink]     # sink active
port_type            : [dual] source sink
orientation          : reverse           # matches reg 0x09 orientation bit
extcon0 (1-0047)     : USB=1  USB-HOST=0
usb_role/4e00000.usb-role-switch/role : device
```

The gadget stays up: `usb0` is `172.16.42.1/16` and SSH-over-USB keeps working.

### Plugging an OTG adapter changes nothing on its own

With an OTG adapter + a USB WiFi dongle physically connected, the state above is
**unchanged**. Investigating the raw registers:

```
reg 0x0a = 0x00   # MODE_SELECT = 00 = "follow the PORT pin" (HW-strapped UFP)
reg 0x09 = 0x50   # attached_state = 01 (attached as UFP/sink)
reg 0x08 = 0x00   # no interrupt pending
/proc/interrupts: 165 ... msmgpio 11 Edge tusb320 -> count 0
```

The driver leaves the chip in `TUSB320_MODE_PORT` (see `tusb320_reset()` in
`drivers/extcon/extcon-usbc-tusb320.c`, "Set mode to default (follow PORT pin)").
On this board the PORT pin is strapped **UFP-only**, so the chip stays device.

Forcing DRP by hand and soft-resetting the CC state machine:

```
i2cset -f -y 1 0x47 0x0a 0x30   # MODE_SELECT = 11 (DRP)
i2cset -f -y 1 0x47 0x0a 0x31   # + I2C_SOFT_RESET, keep DRP
# after ~95 ms:
reg 0x09 = 0x30                 # attached_state = 00 -> NOT ATTACHED
# stays 0x30, IRQ count still 0
```

So even in DRP the controller does not see the OTG adapter on CC — it drops to
"not attached" and never toggles to host. This is the hardware wall: its CC pins
are not sensing the adapter's Rd/Ra here.

### Host mode does work — via the charger, by hand

Doing exactly what `otg on` does — set the dwc3 role to host, then flip the
SGM41542 OTG-boost bit — brings the port up as a host and powers VBUS:

```
echo host > /sys/class/usb_role/4e00000.usb-role-switch/role
# charger SGM41542 at i2c 1 addr 0x3b, reg 0x01, OTG bit 0x20:
cur=$(i2cget -f -y 1 0x3b 0x01)          # e.g. 0x1a
i2cset -f -y 1 0x3b 0x01 $((cur | 0x20)) # -> 0x3a
```

Result (verified):

```
xhci-hcd xhci-hcd.1.auto: xHCI Host Controller ... USB 3.0 SuperSpeed
usb 1-1: new high-speed USB device number 2 using xhci-hcd
lsusb: ID 2357:011e TP-Link AC600 wireless Realtek RTL8811AU [Archer T2U Nano]
rtw_8821au 1-1:1.0: Firmware version 42.4.0
usbcore: registered new interface driver rtw_8821au
```

The TUSB320's reg 0x09 stayed 0x30 throughout — it played no part in bringing
host mode up. VBUS came from the charger; the role came from the sysfs write.

To return to charging: clear the charger bit (`& ~0x20`) and set the role back
to `device`. Leave the TUSB320 in mode PORT (`reg 0x0a = 0x00`) as the driver
had it.

## Why this is a board-design fact, not a driver bug

The vendor stack matches this: the downstream `otg` control lives in the
**charger** path (SGM41542 OTG_CONFIG), not the Type-C controller, and the
`rhodep-usb-otg` package pokes the charger for exactly that reason. The TUSB320
on this SKU is wired for **orientation/CC-flip and current-mode advertisement**,
not for driving the data role of the SoC USB. Mainline has no code path that
turns a `usb-c-connector` role event into a charger boost enable, and even if it
did, the CC here does not raise that event for an OTG adapter.

## Bottom line

- Keep patch 0123: real orientation reporting + the standard typec sysfs, at no
  cost to the default peripheral/gadget path.
- Keep `otg on|off`: it is the only thing that both sets the role and enables the
  charger's VBUS boost, which is what host mode actually needs here.
- A future improvement would be to have `otg` read orientation from
  `/sys/class/typec/port0/orientation` for reporting, but the enable path cannot
  be made automatic without CC sensing the controller does not provide on this
  board.
