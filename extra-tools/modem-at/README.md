# modem-at — an AT console for the modem

The Moto G82's modem answers AT commands, and nothing in this port used them
until now. It is worth having on its own terms: it is a third window onto the
radio, independent of the two that already existed.

```
              ┌── AT    modem state, over glink
              │
   Linux ─────┼── QMI   modem services, over qrtr
              │
              └── IPA   the data path
```

That independence is the point. The AT path keeps working when ModemManager is
not running, when the IPA driver is held out of the boot, and when QMI is busy
or wedged. It also does not depend on the CoreSight debug fuses, which on this
handset are **closed** — see `docs/watchdog-ipa-lte-wip/HANDOFF.md`.

## Install

On the phone, as root:

```sh
./install.sh
```

## Use

```sh
sudo rhodep-modem-at              # interactive console
sudo rhodep-modem-at ATI          # one command
sudo rhodep-modem-at -c DATA1 ATI # the other channel that answers
sudo rhodep-modem-at -l           # what the modem advertises
```

In the console, `?` prints a crib sheet and Ctrl-D exits. Everything else is
sent verbatim — the tool is a pipe, not a wrapper, so any command the modem
understands works whether or not this tool has heard of it.

```
at> ATI
Manufacturer: Motorola Mobility
Model: 334
Revision: MPSS.HI.4.3.4-00494-MANNAR_GEN_PACK-1.24452.133  1  [Sep 13 2024]
IMEI: 351287971159510
OK

at> AT$QCSIMSTAT?
$QCSIMSTAT: 0,SIM INIT COMPLETED
OK
```

## How it works, and why that matters

There is no serial port here. The modem's glink edge advertises channels —

```
6080000.remoteproc:glink-edge.DS.-1.-1
6080000.remoteproc:glink-edge.DATA1.-1.-1
6080000.remoteproc:glink-edge.DATA2.-1.-1 … DATA4, DATA11
```

— and no driver binds any of them. `rpmsg_ctrl` can create an endpoint on a
named channel with `RPMSG_CREATE_EPT_IOCTL`, which produces an ordinary
`/dev/rpmsgN` you can read and write. `DS` and `DATA1` turn out to speak AT.

Two things will confuse you if nobody says them:

* **A write can fail with `EBUSY`.** That is glink saying the modem has not
  posted a receive buffer yet, not the port being dead. Open the endpoint
  *blocking* and bound the wait; this tool uses `SIGALRM` so it can never hang.
* **The port echoes.** Send `AT` and the first line back is `AT`. The tool
  drains before each command and strips the echo, so what you see is the
  answer.

`DIAG` is a different story: it is **not** advertised, and creating an endpoint
for it succeeds while `open()` then fails with `EINVAL`, because
`qcom_glink_create_local()` never receives an `OPEN_ACK` from the modem. Nor
will the modem switch an AT port into DIAG mode: `AT$QCDMG` answers `ERROR`.
So the modem's own F3 log is not reachable on this firmware, and this console
is the closest thing to it.

## What it is good for

Poking at the radio without ModemManager in the way:

* `AT+CESQ` for RSRP and RSRQ, which is the honest signal reading
* `AT+CEREG?` and `AT+COPS?` for registration and access technology
* `AT+CEER` for a cause when something fails
* `AT+CGDCONT?` to see the APNs the modem actually has

It was also used to establish something about the LTE reset that nothing else
could: polled continuously across an attach, **the modem answers every command
until 200 ms before the SoC disappears**, reporting itself registered with no
cause to report. Both processors look healthy right up to the instant of the
reset.

## Careful

This is the real modem of a working phone. Reads are harmless; writes are not
necessarily. `AT+CFUN=0` will drop the radio, and some vendor commands persist
across reboots. If the phone is your only way in, remember that WiFi does not
depend on the modem but mobile data does.
