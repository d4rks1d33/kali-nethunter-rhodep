# hostapd for the WiFi Pineapple port (karma + PineAPE Enterprise), aarch64

This directory provides the **patched hostapd** that the port's rogue-AP core
needs on the Motorola G82 / Kali-NetHunter (aarch64). It is the last offensive
piece: the karma "evil twin" (responds to probes and associates clients) and
the **PineAPE Enterprise** WPA2-EAP evil-twin that harvests credentials.

## Why hostapd-mana (and not a hand-rebuilt Hak5 patch)

The original WiFi Pineapple ships Hak5's `wpad` (hostapd) carrying two patch
sets (reverse-engineered in `docs/INFORME-hostapd-RE.md`):

1. **digininja KARMA** — the "answer every probe" patch by Robin Wood. Adds the
   `KARMA_*` control-interface commands.
2. **PineAPE** (Sebastian Kinne / Hak5, later Marc Egerton) — a WPA-Enterprise
   evil-twin on top of karma that accepts any EAP identity, downgrades to
   MSCHAPv2/GTC, and writes captured credentials straight into
   `/etc/pineapple/pineape.db` (`chalresp` / `basic` tables). Adds the
   `PINEAPE_*` control-interface commands.

The Hak5 PineAPE patch is **not published**; reconstructing it byte-exact from
the MIPS binary and forward-porting it to a current hostapd is high-risk and
hard to validate without the phone.

`hostapd-mana` (github.com/sensepost/hostapd-mana) is the **open-source
continuation of the same digininja-karma lineage**. It already implements, in
maintained C against a modern hostapd:

* **karma / MANA** — respond to directed *and* broadcast probes, associate any
  client (`MANA_ENABLE`, `LOUD_ENABLE`, `MANAACL_*`, `MANA_CHANGE_SSID`, ...).
* **WPE EAP credential harvesting** — the hostapd-wpe lineage is folded in as
  `WPE_ENABLE` + `EAPSUCCESS_ENABLE` (accept-any-credential) + `MANA_EAPTLS`.
  MSCHAPv2 username/challenge/response are emitted through the WPE credential
  callback (`eap_server_mschap_rx_callback`).

So mana gives us **both** halves (karma AND EAP harvest) that the Hak5 binary
gave us, in one maintained, aarch64-buildable tree. It is packaged in **Kali
`main`, arch `any`** (`hostapd-mana` — verified on pkg.kali.org), i.e. it is
directly `apt install`-able on the G82's Kali/NetHunter arm64 userland.

The only gap is **nomenclature**: mana's control commands are named
`MANA_*`/`WPE_*`, while `pineap-ng` and the panel speak Hak5's
`karma_*`/`pineape_*`. We bridge that with a thin **`hostapd_cli` wrapper**
(`compat/hostapd_cli`) that translates the Hak5 verbs to mana verbs, so
`pineap-ng` and the PHP panel are **not modified at all**. Captured creds are
imported from mana's WPE log into `pineape.db` by `net/pineape-creds-import`,
reproducing the exact tables the panel reads.

See `docs/INFORME-hostapd-RE.md` for the full RE and command mapping.

## How it is installed

`install.sh` runs `vendor/hostapd/build-mana.sh`, which:

1. **Kali/apt fast path** (preferred on the G82): if `apt-get` can install
   `hostapd-mana`, it does, and symlinks the mana binaries into place. No
   compilation on the phone.
2. **Source fallback** (this/any host, cross or native): clones
   `hostapd-mana`, drops in `vendor/hostapd/mana.config`, and builds
   `hostapd` + `hostapd_cli` for the target arch (`CC=aarch64-linux-gnu-gcc`
   when cross-compiling). Produces `hostapd-mana` and `hostapd_cli-mana`.

The real `hostapd_cli` from mana is installed as `hostapd_cli-mana`; our
translating wrapper takes the name `hostapd_cli` on `$PATH`.

## Files

| file | purpose |
|------|---------|
| `build-mana.sh` | apt-or-source installer/builder for aarch64 |
| `mana.config`   | hostapd `.config` enabling karma/MANA + EAP server + WPE + SQLite ctrl |
| `hostapd-karma.conf.in` | template for the karma AP on `wlan1` (open evil-twin) |
| `hostapd-enterprise.conf.in` | template for the PineAPE Enterprise VAP (WPA2-EAP) |
| `README.md` | this file |

The `.in` templates are rendered by `compat/wifi-apply` from the UCI store (the
same store the panel writes), mirroring how the original init.d produced the
hostapd config.
