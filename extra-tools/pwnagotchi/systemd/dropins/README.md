# Systemd drop-ins for pwnagotchi radio selection

The pwnagotchi units ship configured for the **external** TP-Link radio
(`wlan1mon`, `rtw_8821au`/`RTL8811AU`). To switch to the **internal** WCN3990
radio (`wlan0`/`mon0`) — where wlan0 STA is dropped and mon0 does channel-
hopping capture, and deauth/associate are routed through
`rhodep-inject-lab` — install the `20-radio-internal.conf` drop-in to BOTH the
bettercap and pwngrid units:

```sh
sudo install -Dm0644 20-radio-internal.conf \
    /etc/systemd/system/rhodep-pwn-bettercap.service.d/20-radio.conf
sudo install -Dm0644 20-radio-internal.conf \
    /etc/systemd/system/rhodep-pwngrid-peer.service.d/20-radio.conf
sudo systemctl daemon-reload
```

Also edit `/etc/pwnagotchi/config.toml`:

```toml
main.iface = "mon0"

[main.plugins.rhodep_internal_inject]
enabled = true
```

Then start pwnagotchi as usual (same unit name — the NetHunter Pro toggle
works unchanged):

```sh
sudo systemctl start rhodep-pwnagotchi.service
```

To go back to the external adapter: remove both drop-ins, revert `main.iface`
to `wlan1mon`, and disable the plugin. `systemctl daemon-reload` after.

Automating this from the NetHunter Pro app is the same pattern already used
for the manual/auto mode drop-in (`10-mode.conf`).
