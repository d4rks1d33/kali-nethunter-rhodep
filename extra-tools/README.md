# extra-tools

Things that make this phone pleasant to *work on*, as opposed to the rest of the
repository, which makes the hardware work at all.

Nothing here is required to boot, place a call, or use the radio. Everything here
was written because the device was already usable and the next obstacle was no
longer the hardware — it was the software assuming a desktop, a keyboard, or a
paid API key.

	modem-at/             an AT console for the modem, over glink rather than a
	                      serial port — a third window onto the radio, working
	                      when ModemManager is down and independent of the
	                      DIAG debug fuses
	terminal-keyboard/    Esc, Tab, Ctrl, Alt and the arrows on the on-screen
	                      keyboard, in every language layout
	terminal-clipboard/   copy and paste in the terminal, from the shell side,
	                      because the keyboard cannot reach the clipboard
	claude-free/          Claude Code driven by the model providers opencode
	                      already holds keys for
	pwnagotchi/           pwnagotchi capturing on the external TP-Link (wlan1),
	                      never touching the internal wlan0
	nethunter-pro-app/    the NetHunter Pro control panel — a GTK4/libadwaita
	                      app for Phosh that drives the port's tools (pwnagotchi,
	                      wifipumpkin3, CARsenal, nmap, HID attacks, VNC…) from a
	                      touch UI instead of a terminal
	cleanup/              weekly disk-space cleanup (caches, journal, coredumps)
	                      and a permanent cap on the systemd journal

Each directory has its own README with the reasoning, the protocol traces, and
the things that turned out to be impossible. Those are worth reading before
changing anything: the obvious approach usually does not work, and the reason is
never in the documentation.

## Installing

Each has an `install.sh` that is idempotent and safe to re-run:

	cd terminal-keyboard  && sudo ./install.sh   # then log out and back in
	cd terminal-clipboard && sudo ./install.sh   # then open a new terminal
	cd claude-free        && sudo ./install.sh
	cd pwnagotchi         && sudo ./install.sh   # needs /opt/pwnagotchi cloned
	cd nethunter-pro-app  && sudo ./install.sh   # installs the app + dbus helper
	cd cleanup            && sudo ./install.sh   # weekly timer + journal cap

`terminal-keyboard` and `terminal-clipboard` belong together — the keyboard sends
the bytes, the shell turns them into clipboard operations — so install both or
neither if you want `Ctrl+V` to paste.

## Why they are not in `userspace/`

`userspace/` holds what the port needs in order to be a working phone: the modem's
remote file system, the Bluetooth address, the audio routing, the apt holds. If any
of it is missing, something on the device is broken.

These are optional. Removing them costs comfort, not function, and mixing the two
kinds of thing in one directory made it harder to answer "what does this port
actually require".

They are protected the same way as the rest, though: registered with
`rhodep-protect-files`, so they carry the immutable bit and a snapshot, and their
packages are held.

## Per-directory detail

The rest of this file is what used to live in the top-level
README under "Extra tools": the reasoning, protocol traces and
dead-ends per directory. The main README now only carries the
one-line summaries above.

**`terminal-keyboard/`** — Plasma's on-screen keyboard has no Esc, no Tab, no Ctrl
and no arrows, which makes a terminal close to unusable. This adds them as a row on
every page, with Ctrl and Alt as modifiers that arm rather than type, so any
combination is reachable.

The detail worth knowing: modifiers cannot be delivered as modifiers at all.
`plasma-keyboard` sends the protocol's modifier field as a hardcoded `0`
(`src/inputlisteneritem.cpp`) and KWin ignores it regardless, deriving modifiers
from the keymap — which is why an early attempt at Ctrl+C arrived as a capital `C`.
So these keys send **control characters as text**: Ctrl+C on a tty is not a
combination, it is the byte `0x03`. All 52 of Qt's layout files are transformed at
install time and each is verified with `qmllint`, keeping Qt's original whenever a
transform cannot be verified — a layout that will not load is a phone you cannot
type on.

**`terminal-clipboard/`** — `Ctrl+V` pastes and `Ctrl+Shift+C` copies in the
terminal, as zsh widgets.

The detail: this cannot live in the keyboard. The keyboard has no keyboard focus,
so KWin never sends it a clipboard offer — a protocol trace contains not one
`data_offer`, and every read came back empty. The shell *can* reach the clipboard,
and the keyboard can send bytes, so the work happens in the shell. `Ctrl+Shift+C`
has to travel as `Esc` + `C` rather than as a control character, because control
characters ignore case: `Ctrl+C` and `Ctrl+Shift+C` are both `0x03`, and `0x03` has
to keep cancelling.

**`claude-free/`** — Claude Code, running on whatever model providers opencode
already holds keys for, including opencode Zen's free models, which need no key at
all.

The detail: Claude Code speaks exactly one API shape and lets you move it with
`ANTHROPIC_BASE_URL`, so a small proxy translates between that and the OpenAI shape
every other provider speaks, routing by `provider/model` and reading opencode's own
`auth.json`. Anthropic is passed through untranslated, since with an Anthropic key
the request is already the right shape. The real work is the streaming direction:
Claude Code expects Anthropic's event sequence with tool calls as
`input_json_delta` fragments, which is a state machine rather than a field rename.

**`pwnagotchi/`** — the WiFi-auditing agent (jayofelony fork) capturing WPA
handshakes on the phone. Verified in auto mode: bettercap on `wlan1mon` sees
dozens of APs and it captured 11 handshakes in a short run.

The detail worth knowing: pwnagotchi is built for a Raspberry Pi whose only WiFi
does monitor mode, and this phone is the opposite. **wlan0** (internal WCN3990)
is the only real WiFi and cannot do monitor mode at all, so it has to stay a
managed client; **wlan1** (external TP-Link RTL8188EUS) is the one that does
monitor and injection. So everything is pinned to `wlan1mon` and wlan0 is never
touched: the monstart helper works on wlan1 by name only, refuses to run if
pointed at wlan0 or if wlan1 shares a phy with it, and never runs
`airmon-ng check kill` (which would drop wlan0 for nothing). The three services
(`rhodep-pwn-bettercap`, `rhodep-pwngrid-peer`, `rhodep-pwnagotchi`) are disabled
at boot and started on demand, because capture needs `otg on` and the external
adapter anyway. Its face and status are on the web UI at `http://<phone>:8080`.

**`nethunter-pro-app/`** — the NetHunter Pro control panel, a GTK4/libadwaita
app for Phosh that drives the port's tools from a touch UI instead of a
terminal: pwnagotchi, wifipumpkin3, Phishkin3 (wp3 + evilginx2), driftnet,
Network Discovery, RouterSploit, CARsenal (CAN bus), nmap, HID/BadUSB
attacks, an evil twin, VNC, Docker, and the rest. Each is a module screen.

The Docker screen keeps the engine off until asked. Start and Stop drive
**both** `docker.service` and `docker.socket` -- the socket first on start so the
service can bind it, the service first on stop so nothing socket-activates it
straight back up -- and both are disabled at boot so nothing holds power until
asked. A **Downloaded images** list shows every pulled image with a per-image Run or
Stop button, so several can be started and stopped independently -- start one,
start another, stop one, come back later. Stop removes the container but keeps
the image, so it can be run again; the state is read live from `docker ps`. There
is a **Clean everything** button that wipes all Docker data for the
run-on-demand-then-clean workflow: every container, image, network, the build
cache, and all volumes including named ones (which `system prune --volumes` does
not touch), behind a confirmation. Run is "inteligente" only as far as the
image's own metadata allows: exposed ports come from the pulled image's `EXPOSE`
list (not a README, which is free text), are published with `-p`, and a web port
among them is surfaced as a URL. Tested end to end with `bkimminich/juice-shop`:
pull, EXPOSE 3000 detected and published, container up, HTTP 200 on
`http://127.0.0.1:3000`; and the wipe verified to leave zero containers, images
and volumes.

The **Phishkin3 (evilginx)** screen turns the multi-step wifipumpkin3 +
evilginx2 attack (https://docs.wifipumpkin3.com/blog/tutorials/phishkin3) into
three inputs: phishlet, look-alike domain, interface. The orchestrator lives in
`helper/rhodep-phishkin3-launch`. It configures evilginx by feeding its shell
commands on stdin (evilginx v3 keeps phishlets and lures in a BuntDB store, not
a config file, so pilot-by-stdin is the reliable way), writes the wp3 `.pulp`
with the phishkin3 proxy and DNS spoof, spoofs `/etc/hosts`, and launches both
tools in a `tmux` session named `phishkin3` so they survive and can be attached
for logs. 95 community phishlets ship pre-installed under
`/usr/share/evilginx2/phishlets/` (from Whispergate, jeanlucndato and
hash3liZer), including Instagram, Facebook, Google, GitHub and LinkedIn.

The domain is never the real one -- HSTS preload refuses a spoof of the real
name. The screen suggests look-alikes: a plain one (`instagram-login.com`) and
homoglyphs, Cyrillic and dotless-i characters that read as the original but are
a different domain. The launcher converts these to punycode, which is what
evilginx, the DNS spoof and `/etc/hosts` actually use: `instagrаm.com` with a
Cyrillic a becomes `xn--instgram-46g.com`, verified end to end.

Ten separate bugs had to be worked around, all committed and worth reading
because they name traps the next attacker will hit:

  * The evilginx config directory has to be cleared each run (a stale `data.db`
    accumulates lures and hostnames from previous runs, so `get-url 0` returns
    the wrong lure for the current phishlet -- Instagram tripped over this).
  * The DNS spoof needs every subdomain listed explicitly. `add *.<domain>`
    is written to the zone file but pydns does not match it against a real
    query for `www.<domain>`: the log shows `no local zone found, proxying
    www.<domain>` and the browser gets `ERR_NAME_NOT_RESOLVED`. Every landing
    hostname from the phishlet's `proxy_hosts` gets its own `add` line.
  * phishkin3's gate allows only port 8080 to the AP; the launcher adds an
    `iptables -I FORWARD` for 443 to the AP so the browser can reach the lure
    that `/login` 302s to.
  * evilginx's blacklist runs in `unauth` mode by default. Every victim's
    traffic transits through the AP, so a stray probe or a curl during setup
    lands the AP IP on the list, after which every real visitor gets served
    the `unauth_url` (a Rickroll). The launcher sends `blacklist off` on
    startup and clears `blacklist.txt` alongside the config each run.
  * The stock Instagram phishlet's `sub_filters` are a no-op (search and
    replace are both `https://{hostname}/`), so the HTML served to the
    browser keeps hardcoded links to `www.instagram.com` and the browser
    fetches static assets from the real Instagram through the DNS spoof.
    A fixed phishlet lives under `phishlets/instagram.yaml` and gets copied
    into place.
  * NetworkManager still owns `wlan1` while wp3 puts it in AP mode, so it
    disconnects the interface in a loop -- SSID flashes on and off. `nmcli
    device set <iface> managed no` before start, `managed yes` on Stop.
  * `wifipumpkin3`'s `-iNM` is a *standalone action*, not a flag. Passed with
    `-p`, wp3 ignores the interface and exits before touching the pulp; the
    log shows `The interface wlan1 has been ignored successfully` and then
    nothing, and tmux dies with `no server running`. The nmcli call above is
    enough; `-iNM` must not be added to the wp3 invocation.
  * `pkill -f phishkin3` would match the launcher itself, which is called
    `nethunter-pro-phishkin3-launch`. The reset used narrow patterns instead:
    `pgrep -f 'wifipumpkin3 -p'`, `pgrep -f 'plugins/bin/phishkin3'`, and
    `pkill -x` for evilginx/hostapd/dnsmasq. The `pgrep` variants also filter
    out the launcher's own pid.
  * Half-cleaned state between launches (duplicate FORWARD ACCEPT rules, an
    already-open tmux session) stops wp3 from ever driving phishkin3 to a
    listening state -- the AP comes up but there is no captive portal.
    `full_reset` runs before every Launch, whether Stop was pressed or not.
  * evilginx's `-developer` mode signs certs with a CA called literally
    "Evilginx Super-Evil Root CA". Android will never trust that automatically.
    Installing the CA on the victim (Settings → Security → Install certificate)
    works for browser traffic in a lab, but not for apps: since Android 7,
    Chrome and system apps ignore user-installed CAs by policy, which was
    Google's mitigation for exactly this attack. The realistic path for real
    victim-facing use is a public look-alike domain and Let's Encrypt via
    autocert (without `-developer`) -- the launcher now auto-detects an
    installed cert and switches modes; see below.

Ten more traps came out of getting Instagram's login form to actually render
end to end -- the "stuck on the logo" bug. Each was hunted with tcpdump, tmux
`capture-pane` on the evilginx window, and `tshark` to read SNI off the client's
`ClientHello` frames; every fix is committed and worth reading because the
default configuration fails silently on all of them.

  * The stock Instagram phishlet only proxies `www.instagram.com` and
    `m.instagram.com`. Instagram's login HTML links every JS/CSS bundle that
    renders the form with absolute URLs at `static.cdninstagram.com` (verified:
    488 references in the served HTML), so with the CDN unproxied the browser
    fetches those bundles from the real CDN under the wrong origin and the
    React SPA never mounts the form -- the page stops at the Instagram logo.
    The phishlet under `phishlets/instagram.yaml` now proxies
    `static.cdninstagram.com`, `scontent.cdninstagram.com` and
    `i.instagram.com` as extra `proxy_hosts` with `auto_filter: true`, and adds
    explicit `sub_filters` for the CDN host so the served bundles reference
    `static.<phishdomain>` and stay on the phishing origin.
  * Evilginx v3.3.0 only generates a phishing vhost and a per-host cert for
    `proxy_hosts` marked `session: true`. With `session: false` the CDN hosts
    get no vhost, so the browser's request to `static.<phishdomain>` hangs
    (`http 000`, TCP connects but no TLS response) and the login bundles never
    load -- same "stuck on the logo" symptom. All CDN hosts in the fixed
    Instagram phishlet are `session: true`; the flag does not widen credential
    capture because `auth_tokens` still scopes what is harvested.
  * With the full 96-phishlet directory shipped in `/usr/share/evilginx2/`,
    `phishlets get-hosts instagram` silently omits the extra CDN hosts -- only
    `www.instagram-login.com` and `m.instagram-login.com` appear. `strace` on
    evilginx confirms every YAML is parsed on start; the omission is not a
    parse error but a downstream collision/limit that only triggers past
    ~60-70 loaded phishlets. Isolating the same YAML in a directory with only
    a handful of phishlets makes all five CDN hosts appear in `get-hosts`
    immediately. The port ships only the four phishlets that are actually
    exposed by the app (`instagram`, `facebook`, `outlook`,
    `google-botguard-bypass`); the rest are backed up to
    `phishlets.all.bak/` in case something else is needed later.
  * The USB Realtek RTL8811AU dongle (`rtw_8821au`, e.g. the TP-Link Archer
    T2U Nano) tears itself down mid-attack when WiFi power-save is on: `dmesg`
    shows repeated `wlan1: entered promiscuous mode` / `left promiscuous
    mode`, then `usb 1-1: reset high-speed USB device` /
    `(unregistering)`, and the AP silently dies (`client has left AP`, SSID
    gone, hostapd left orphaned). `iw dev wlan1 set power_save off` before
    hostapd binds fixes it. The launcher runs this in both
    `release_from_networkmanager` and again in `start_in_tmux` after the AP is
    up, because wp3 re-creates the interface when it switches it to AP mode
    which resets `power_save` to `on`. USB autosuspend is also forced off on
    every USB device (`echo on > /sys/bus/usb/*/power/control`) to keep the
    kernel from parking the dongle.
  * `/etc/resolv.conf` on the port ships with `nameserver 127.0.0.11` (a
    ghost from a Docker install that is not running). Nothing listens there,
    so every DNS resolution the host does -- including every upstream lookup
    evilginx makes to reach Instagram's real backend -- times out on the first
    server before falling back to the second. Symptoms are 5+ second first-byte
    times on every proxied request and CDN bundles that never finish loading
    within the browser's own timeouts. The launcher rewrites `/etc/resolv.conf`
    to `1.1.1.1` + `8.8.8.8` at start and disables IPv6 (`sysctl
    net.ipv6.conf.all.disable_ipv6=1`), because Go's default dialer prefers
    the AAAA record when both are present and the port has no working IPv6
    route to Meta's CDN.
  * evilginx v3.3.0 in `-developer` mode refuses `ClientHello`s with SNI it
    does not know: `WARN: Cannot handshake client static.cdninstagram.com
    remote error: tls: unknown certificate`. What actually reaches evilginx
    (verified by `tshark -Y tls.handshake.type==1` on every interface) is only
    the phishing-domain SNIs the browser was told about; the log line
    identifies the *upstream* host evilginx tried and failed to reach, not the
    incoming SNI. It is emitted from the same code path that later, in
    non-developer mode, becomes the real bug: the browser refuses to trust the
    self-signed cert on any subresource -- there is no click-through UI for
    `<script src>` -- and the login form stays a logo. See the Let's Encrypt
    section below for the fix; installing the developer CA on Android does
    NOT help, because Chrome only trusts user-installed CAs for top-level
    documents, not for JS-loaded subresources.

**Real certs, no developer CA.** The launcher auto-detects a real (Let's
Encrypt-issued or otherwise CA-signed) cert under
`crt/sites/<phishdomain>/fullchain.pem` + `privkey.pem` in evilginx's config
directory. If present, it feeds `config autocert off` on stdin during setup and
starts evilginx *without* `-developer`, so evilginx serves the disk cert
instead of the "Super-Evil" self-signed one -- the browser trusts the whole
`*.<phishdomain>` cert chain, top-level *and* every asset subdomain, and the
login form renders. If no cert is present the launcher falls back to
`-developer` (self-signed) so nothing breaks; see `has_real_cert()` in
`rhodep-phishkin3-launch`.

The phishkin3 module in the app surfaces installed-cert domains at the top of
the look-alike domain picker so the default choice is a domain the browser
will actually trust. The GUI runs as the login user (not root) and so it
cannot read the cert store under `/root/.config/nethunter-phishkin3/evilginx/
crt/sites/` directly; instead the launcher mirrors the list of loadable
domains as empty marker files under `/var/lib/nethunter-phishkin3/certs/`
(mode 0644 on a 0755 dir) on every run, via `refresh_cert_index()`. The
picker reads that world-readable directory. Elevating the whole app under
`pkexec` was tried first and rejected: kwin on the Plasma-Mobile port refuses
Wayland connections from a UID that does not own the session, so
`Gdk.Display.get_default()` returns None under root and the app aborts with
`TypeError: Argument 0 does not allow None as a value`. The per-action
`ToolRunner(..., root=True)` path with `allow_active: yes` in the polkit
rule is enough for the privileged work.

The end-to-end flow that actually works (tested with `cdninstagram.dedyn.io`):

  1. Register a free public domain -- **deSEC.io** (`*.dedyn.io`) is the pick:
     wildcard-capable, native DNS API, and gratis. DuckDNS also works and
     avoids deSEC's DNSSEC replication lag but the domain is longer. The
     Let's Encrypt validation is DNS-01, so the port never has to expose any
     port to the internet -- only a TXT record propagates through the
     provider.
  2. Install `acme.sh`:
     `curl https://get.acme.sh | sh -s email=<a-real-email>` (Let's Encrypt
     rejects `example.com` addresses; use `--nocron` if no cron is present).
  3. Issue the wildcard with the deSEC plugin, `--dnssleep 180` or higher --
     deSEC's own devs document their DNSSEC replication lag on their forum
     (`talk.desec.io/t/ns1-desec-io-replication-issues/804`); the default
     `--dnssleep` fails with `DNSSEC: Bogus: validation failure ... covering
     NSEC3 was not opt-out` and the challenge times out. 180-300s consistently
     succeeds:
     ```
     export DEDYN_TOKEN=<token-from-desec.io>
     acme.sh --issue --dns dns_desec \
         -d cdninstagram.dedyn.io -d '*.cdninstagram.dedyn.io' --dnssleep 180
     ```
  4. Install it under evilginx's cert dir with the exact filenames the
     `setUnmanagedSync` loader in `core/certdb.go` looks for (`fullchain.pem`
     + `privkey.pem`; the folder name under `crt/sites/` is a container and
     does not have to match SNI, since certmagic keys off the cert's own
     SANs):
     ```
     acme.sh --install-cert -d cdninstagram.dedyn.io --ecc \
         --fullchain-file <cfg>/crt/sites/cdninstagram.dedyn.io/fullchain.pem \
         --key-file       <cfg>/crt/sites/cdninstagram.dedyn.io/privkey.pem
     ```
  5. Launch: `rhodep-phishkin3-launch --phishlet instagram --domain
     cdninstagram.dedyn.io --interface wlan1 --interface-net wlan0 --start`.
     The launcher logs `cert=letsencrypt/unmanaged` at setup and starts
     evilginx without `-developer`. `openssl s_client -servername
     www.<phishdomain>` should show `issuer=... Let's Encrypt` on every
     subdomain the phishlet uses; that is what the browser gets, no
     click-through warning at any level.

Two things still bite the victim path even with a valid cert, and both are
about how Android treats the AP:

  * **Captive portal WebView.** Android sends `connectivitycheck.gstatic.com`
    over HTTP:80 to sniff for internet on join; the phishkin3 DNAT catches
    that on `--dport 80 -j DNAT --to 172.16.0.1:8080` and the phishkin3 Flask
    app 302s it to the lure URL. Android then opens the lure inside its
    built-in captive-portal WebView, not Chrome, and that WebView is a
    stripped-down runtime: Instagram's SPA loads the HTML+logo and never
    finishes bootstrapping. The victim has to dismiss the captive-portal
    banner ("Use network as is" / "Sin internet") and open the lure in real
    Chrome. This is inherent to how phishkin3 uses the captive-portal
    detection as a delivery vector; documenting it is the fix.
  * **Public A record for the phish domain.** The AP's `dns_spoof` answers
    `www.<phishdomain>` -> `172.16.0.1` for its clients, but Chrome on Android
    routinely does DNS-over-HTTPS to Cloudflare/Google, not to the AP's
    resolver. If the phish domain has no public A record, DoH returns
    `NXDOMAIN` and Chrome shows `DNS_PROBE_FINISHED_NXDOMAIN` -- the AP's own
    resolver is bypassed entirely. The pragmatic fix, since the AP IP is
    private (`172.16.0.1`), is to publish that private IP as the public A
    record for every subdomain the phishlet uses (`apex`, `www`, `static`,
     `scontent`, `i`, `m`). Public resolvers happily serve private IPs; the
     result is the same `172.16.0.1` whether the client asked the AP's spoof
     or Cloudflare's DoH, and Chrome connects to the AP either way. TTL 3600
     (deSEC minimum is 900).

The **driftnet** screen sniffs image bytes off unencrypted HTTP on a capture
interface. `driftnet -a` (adjunct mode) writes each captured image into its
OWN temp directory and announces the filename on stdout; a small in-shell
watchdog reads those announcements and hard-links each file into
`~/Pictures/driftnet-images/` (with a timestamped prefix so runs never collide)
before driftnet's `-m` rotation evicts it, so the permanent collection
survives both driftnet's own housekeeping and the module being stopped.
`~` is expanded against `$SUDO_USER`'s home rather than root's, and each
copied file is `chown`-ed back to that user -- the module runs as root for
pcap and for chown, but the images land under the login user's home. A
second mode swaps the file save-out for driftnet's built-in HTTP viewer on
`http://<phone>:9090`; the "Open web viewer" row is hidden unless that mode
is picked AND driftnet is running, because it 404s otherwise and the first
UX round hit exactly that.

Two things worth naming here because they are true of driftnet in 2026 in
general and are why the screen frames what it does the way it does:

  * **HTTPS is not sniffable by design.** TLS negotiates a per-session key
    from the server's certificate; a passive tap sees random-looking bytes
    and cannot recover images from them. Google Images, Instagram, every
    social network, every bank -- HTTPS everywhere means driftnet's actual
    hit rate is close to zero on modern browsers hitting modern sites. The
    module's description says so up-front. Where it does still work: HTTP
    fallbacks (`http://neverssl.com`, old CDNs, IoT devices' plain HTTP),
    the captive-portal probe traffic Android generates on join
    (`connectivitycheck.gstatic.com`), and any HTTP that transits the AP.
  * **SSL stripping is dead too.** sslstrip (2009) turned HTTPS into HTTP
    inside a MITM by rewriting `https://` to `http://` in served responses,
    which is exactly the kind of "downgrade HTTPS to HTTP so driftnet can
    read it" that comes up first when discussing this. Three defenses
    landed since then and killed it against anything worth stripping: HSTS
    (a site returning `Strict-Transport-Security` locks the browser onto
    HTTPS for a year), the HSTS preload list (Chrome/Firefox/Safari ship
    ~150 000 hardcoded HTTPS-only hosts including every big site and every
    `.dev`/`.app`/`.new` TLD), and browser HTTPS-first / HTTPS-only modes.
    The realistic path for capturing plaintext of a real target's traffic
    in 2026 is not stripping but the phishkin3 flow above -- a look-alike
    domain with a real Let's Encrypt cert, so the browser trusts the MITM
    proxy on its own without ever attempting downgrade.

**Cloudflare Quick Tunnels do not replace the deSEC domain either.** The
obvious question after the deSEC+LE flow works is whether a free
`cloudflared tunnel --url ...` (which hands out a random
`*.trycloudflare.com` subdomain with a Cloudflare cert) could skip the
domain-registration step entirely. Four blockers, one architectural, one
per-site:

  * **Evilginx v3.3 has no HTTP-only listener.** `core/http_proxy.go:1633`
    goes `net.Listen("tcp", …)` → `vhost.TLS(c)` on every accepted
    connection; a plain HTTP byte sequence fails the TLS ClientHello parse
    and the goroutine returns silently. `cloudflared tunnel --url
    http://…` -- the natural HTTP-origin config -- therefore never gets a
    single request through. `--url https://…` + `--no-tls-verify` +
    `--origin-server-name <hostname>` works, but the tunnel and evilginx
    still both terminate TLS, meaning three back-to-back TLS terminations
    (browser↔CF, CF↔evilginx, evilginx↔real site) with the SNI game
    played twice, and Cloudflare has to be told the exact SNI evilginx is
    now listening for. There is a chicken-and-egg for the SNI value:
    trycloudflare only assigns the random hostname *after* cloudflared
    starts, so the phishlet has to be re-hostnamed and cloudflared
    relaunched with `--origin-server-name <that hostname>` -- doable but
    fragile.
  * **No wildcards under trycloudflare.com, no config-file support.** Quick
    tunnels give exactly one flat random hostname; the docs are explicit
    that config-file ingress is unsupported here. The Instagram phishlet
    proxies five sibling hostnames under one phish domain (`www`, `m`,
    `static`, `scontent`, `i`); collapsing them all to the one
    trycloudflare hostname means the CDN and API subdomains cannot be
    rewritten separately, which is the exact bug that puts the login
    "stuck on the logo". Wildcards only exist under Named Tunnels on a
    user-owned zone -- so a real free-of-cost solution still needs a real
    domain.
  * **Brotli silently breaks sub_filters.** Cloudflare rewrites the
    outbound `Accept-Encoding` to `br, gzip` on the way to the origin. Go's
    `net/http` transport transparently decodes `gzip` responses but NOT
    brotli, so when the real origin (Instagram, and every modern site)
    responds `Content-Encoding: br`, evilginx sees raw compressed bytes
    and its `sub_filter`/`patchUrls` regexes fail without a warning. The
    victim gets the original, unmodified HTML/JS pointing at
    `instagram.com`. Evilginx v3 has no header-rewrite knob for this;
    you'd need to patch it to strip `br` from `Accept-Encoding` before
    proxying. Same failure mode as "stuck on the logo" but from a
    completely different cause than the one above -- one that only shows
    up in production, once Cloudflare is in the path.
  * **200 concurrent in-flight cap, no SSE, and CF Trust & Safety.** A
    single-victim demo probably works. Two victims fanning out ~40
    subresource fetches each will hit HTTP 429s during login. Cloudflare
    also propagates `CF-Ray`, `CF-Connecting-IP`, `CF-IPCountry` and the
    `__cf_bm` bot-management cookie to the origin -- fingerprinting the
    tunnel is trivial from the target's side, and Trust & Safety can (and
    routinely does) kill trycloudflare tunnels used for credential
    phishing mid-op.

Verdict: quick tunnels are architecturally viable only for single-hostname
phishlets against sites that do not use brotli and expect small load. For
the Instagram target we already work with, they are strictly worse than the
deSEC+LE setup that exists today.

**Instagram itself is the hard part, and it is not solved.** With the AP,
the wildcard cert, the CDN proxying and the `session: true` fix in place,
the login page is served correctly and the browser reaches it without any
TLS warning. And yet the login `<form>` never mounts: the page stops at
the Instagram logo splash. The debug trail below is what has been
established so far, so the next person poking at this does not repeat the
dead-ends:

  * **Served HTML is byte-identical to the real one** modulo the URL
    rewrites -- 419 KB vs 416 KB, same 67 vs 69 `<script>` count, same
    `data-btmanifest` version, same `<script type="application/json">`
    Bootloader payloads, same rsrcMap listing 358-360 bundle URLs. The
    reverse proxy is doing its job on the HTML.
  * **All bundles that ARE fetched come through byte-perfect.** The 298 KB
    tier-one JS bundle at `rsrc.php/v4/yo/r/XrOVaBLe-P9.js` is delivered
    identically from `static.cdninstagram.com` and from
    `static.cdninstagram.dedyn.io` (checked with `wc -c` and `head -c 40`).
    No corruption, no half-decoded brotli, no truncation.
  * **26 subresources return HTTP 200, zero fail, zero console errors,
    zero JS exceptions.** Verified with headless Chromium driven by CDP
    (`Network.enable` + `Runtime.consoleAPICalled` +
    `Runtime.exceptionThrown`). And yet `body.children.length` stays at 2
    (the `id=splash-screen` div plus the trailing `<script>`s), no `<form>`,
    no `<input type=password>`, `window.Bootloader === undefined`,
    `window.PolarisAppID === undefined`. React hydration never completes.
  * **Same code path works fine against the real domain.** Same headless
    Chromium, same UA, same network -- loading `www.instagram.com/accounts/
    login/` directly gives `divs: 119, pw: 1`, i.e. the form is there. So
    the browser and the network are not at fault; the proxy is doing
    something the SPA does not like.
  * **The Bootloader silently stops fetching partway through the graph.**
    The real load fetches 25 JS bundles; the proxied load fetches only 13.
    The 12 missing bundles are exactly the ones that carry the
    `PolarisLoginForm` and its dependencies (`v4/y4/r/HUrPRMPbjNh.js`,
    `v4iQvT4/yE/l/en_US/M-LBWHhsGRV.js`, etc). The bootloader gets far
    enough to know they exist (they are in the served rsrcMap) but never
    requires them.
  * **Bootloader is designed to fail silently.** Reading the SSR payload,
    `BootloaderConfig` is initialised with `silentDups: true`,
    `jsRetryAbortNum: 2`, `timeout: 60000`. Any lazy-load failure gets
    swallowed with no console output. Adding a `Runtime.enable` + full
    console + exception capture over CDP confirms: nothing is thrown,
    nothing is logged, the SPA just parks.
  * **The classic anti-proxy checks are not the cause.**
      - `location.hostname` cannot be redefined -- Chromium marks
        `Location.prototype.hostname`'s descriptor non-configurable and
        the `Object.defineProperty` call throws `Cannot redefine property:
        hostname`. But leaving it as the phishing hostname does NOT change
        anything: there is no visible hostname allowlist in the Instagram
        bundles that gates mount.
      - `document.domain` CAN be overridden and was, no effect.
      - CSP is not the killer: evilginx strips
        `Content-Security-Policy`/`Report-Only`/`X-Frame-Options` from
        responses (`rm_headers` in `OnResponse`), so no nonce mismatch.
        The proxy's response headers were dumped and neither CSP header is
        present.
      - `edge-chat.facebook.com` WebSocket and `www.facebook.com/
        ig_xsite_user_info/` fetch are NOT the killer either. Adding a
        `js_inject` that stubs `window.WebSocket` for `edge-chat` and
        short-circuits `fetch` for `ig_xsite_user_info` does execute (the
        console log fires), but the bundle never even reaches the point
        of calling either -- the failure happens earlier in hydration.
      - Cookie seeding is not the killer. Evilginx's own debug log shows
        it captures `csrftoken`, `datr`, `ig_did`, `ps_l`, `ps_n` into the
        session store, but only forwards `csrftoken` to the browser (the
        `Set-Cookie` headers of the proxy's response are the evidence).
        Seeding `datr`, `mid`, `ig_did`, `ig_nrcb` into `document.cookie`
        from the js_inject also does not unblock hydration.
  * **What has NOT been ruled out (candidates for the next attempt):**
      - The `data-btmanifest="1046380673_main"` hash embedded in the HTML
        is a bundle-manifest version; the SPA may be checking it against
        something the proxy alters (though HTMLs are byte-identical, so
        this seems unlikely).
      - The `brsid` inside the `envjson` element and the `hsi` in
        `SiteData` are session correlators; if the proxy's response
        includes a value that has already been "consumed" against
        `www.instagram.com` and the client verifies against a rotating
        token, that could hang.
      - The `charlesbel`/`tijme` phishlet lineage uses
        `sub_filters` with `search: '{hostname_regexp}'` +
        `replace: '{hostname_regexp}'` over `text/html`, `text/javascript`,
        `application/json`, `application/javascript`,
        `application/x-javascript`. In evilginx v3 that pattern is a no-op
        (self-replacement), but its purpose in v2 was to force
        re-evaluation of hostname rewrites through the specific mime
        types. There may be an alternate `{hostname_regexp}` filter that
        does catch a hostname the current phishlet's `auto_filter` misses.
      - No public working Instagram phishlet exists as of late 2024/early
        2025. The lineage repo (`An0nUD4Y/Evilginx2-Phishlets`) is taken
        down by GitHub for ToS. `simplerhacking/Evilginx3-Phishlets`, the
        most-starred public catalog, explicitly does NOT ship an Instagram
        phishlet and gates a working one behind their paid Masterclass.
        The comment thread at
        `github.com/simplerhacking/Evilginx3-Phishlets/pull/15` documents
        multiple people hitting the exact same "stops on splash screen /
        blank page" symptom and nobody publishing a fix -- so if this ends
        up unfixable without paying, it is because it is unfixable
        without paying.

The `phishlets/instagram.yaml` in the port currently ships with the CDN
proxying + `session: true` fix, extra `sub_filters` for `static`, `scontent`
and `i`, and a defensive `js_inject` that (a) waits for the login form via
`MutationObserver` before hooking the submit, (b) attempts to fake
`document.domain` (works) and `location.hostname` (silently rejected), (c)
stubs the `edge-chat.facebook.com` WebSocket and the
`www.facebook.com/ig_xsite_user_info/` fetch, and (d) seeds
`datr`/`mid`/`ig_did`/`ig_nrcb` into `document.cookie`.

None of that was enough on its own. The actual gate turned out to be an
anti-MITM byte-integrity check that Instagram ships inside the tier-one
bundle, in the module called `ServerJSPayloadListener`:

```js
function e(el){
  if (el instanceof HTMLScriptElement) {
    var t = el.dataset.contentLen;
    if (!(el.dataset.processed
          || el.textContent.length.toString() !== t)) {
      // parse and dispatch payload
    }
    // otherwise: silently skip. No log, no throw.
  }
}
```

Every `<script type="application/json" data-sjs>` block in the served HTML
carries a `data-content-len="N"` attribute -- the byte length its
`textContent` had at the origin. `ServerJSPayloadListener.process()`
compares that against the current `textContent.length` and, if they
disagree, **drops the entire payload without a log entry, without an
exception, and without any signal in DevTools**. The design is explicitly
there to defeat MITM byte modification. It is one of the few genuinely
undetectable-from-inside failures a browser can have.

The evilginx `patchUrls` pass rewrites `www.instagram.com` (14 chars) ->
`www.cdninstagram.dedyn.io` (24 chars) everywhere in the response body,
including inside each `<script data-sjs>` payload. Every rewritten URL
adds 10 bytes. Across a `Bootloader.handlePayload` payload with hundreds
of rsrcMap entries, the drift is +1000-2000 bytes. Evilginx does NOT
update `data-content-len` after the mutation, so every touched payload
gets silently discarded by Instagram's listener. Empirically: a curl
capture of the served HTML shows 30 `data-sjs` scripts and 6 of them
mismatch (deltas from +28 to +1630 bytes), including two
`Bootloader.handlePayload` payloads and the tier-two `ScheduledServerJS`
payload. Those six carry `SiteData`, `BootloaderConfig`, `DTSGInitData`,
`LSD`, `ServerNonce`, `CurrentUserInitialData`, `SprinkleConfig` and the
CSS-loader / retry-config modules. With them silently dropped,
`Bootloader.require("SiteData")` returns `undefined` via
`ErrorGuard.applyWithGuard`'s swallow-and-log, the tier-two JS chunk
carrying `PolarisLoginForm` is never demanded, and the splash sticks.

The fix is a `sub_filter` that inserts an inline `<script>` right after
the opening `<head>` -- so it executes before ANY of Instagram's inline
bundles -- that monkey-patches `Element.prototype.getAttribute` and
overrides the `dataset.contentLen` getter to return the CURRENT
`textContent.length` on every read, regardless of what the attribute
actually says. The listener's compare then becomes `x === x`, the guard
passes, every payload is dispatched, and the login form mounts. A second
sweep in `js_inject` before `</body>` also directly rewrites the
attribute as belt-and-braces, and a `MutationObserver` catches
late-inserted payloads. Verified end-to-end: headless Chromium against
the proxied lure produces `divs: 119, pw: 1` (identical to real IG),
console log shows `[nhp-inject] fixed N data-content-len mismatches` on
each visit, and the login form renders in real Chrome on the AP.

Two follow-ups that are not addressed and are known to be worth chasing:

  * **Slow first paint on Chrome.** The head-injected patch runs
    synchronously, which is exactly what makes it win the race against
    the listener, but the form still takes a couple of seconds longer to
    appear than on the real site. Some of that is the AP's uplink and
    evilginx re-encrypting each subresource; some may be the patched
    `getAttribute` running on every element in the DOM. Worth measuring
    later with `--enable-tracing`.
  * **Credentials come in blank; only cookies are captured.** With 2FA
    enabled on the victim account, the classic
    `unenc_password` interception via `sendPass()` does not fire before
    the browser sends the real POST, so evilginx's session has empty
    `username`/`password` fields. The captured `tokens` (session cookies)
    are still usable -- they include the post-2FA `sessionid`, so
    replaying them into a browser lands directly inside the account.
    That is what the "Captured sessions" strip in the Phishkin3 module
    exposes.

**Captured sessions in the app.** The Phishkin3 screen tails evilginx's
`data.db` every 5 s and lists every captured session: username (or "(no
credentials captured)" for the 2FA case), phishlet, cookie count, remote
IP, session id fragment. Each row has "Show cookies" (opens a scrollable
Netscape-format dump ready to paste into a Cookie Editor extension --
domain, path, HttpOnly flag, name, value, one per line) and "Delete"
(`sed -i` on the BuntDB backing file, keyed on the exact `session_id`
substring so it removes just the one record). The read goes through the
DBus helper's `RunCommand` path because the store is 0600 root-owned;
the delete goes the same way for the same reason. No polkit prompt storm
-- the helper is authorised once at app start and the calls piggyback on
that.

The **Network Discovery** screen is the first thing you reach in the Recon
section, because knowing what else is on the Wi-Fi with you is where every
LAN pentest starts. It runs an arp-scan sweep of the local subnet in
~2 seconds and then enriches every discovered device through a parallel
lookup chain -- gateway PTR first (dnsmasq on the router usually
auto-registers DHCP client hostnames, so a reverse query at the router
returns whatever name the device announced when it joined), then mDNS/
Bonjour via a primed avahi-browse cache (Apple TVs, printers, HomePods,
Chromecasts, most consumer IoT), then NetBIOS via nbtscan (Windows,
Android with SMB on), then a system PTR fallback, then unicast SSDP
M-SEARCH with a `<friendlyName>` scrape (Sonos, Samsung/LG TVs, Xbox,
Windows with UPnP), then a Roku ECP probe on 8060 for the `user-device-name`
field, and finally a TCP connect on 62078 which is the Apple lockdownd
port -- open on iPhones and iPads even when they refuse everything else,
so an otherwise-silent device gets tagged as "Apple mobile device".

The whole chain is one `bash -c` pipeline, backgrounded per host, with a
1-2 s per-source timeout so the wall clock stays around 5-8 s for a /24.
`bash` is explicit and not `sh` because the script uses `IFS=$'\t'` ANSI-C
quoting to read arp-scan's tab-separated output, which dash treats as five
literal characters instead of a tab -- that silently splits the vendor
"(Unknown: locally administered)" into "(Unknown: locally adminis" +
"ered)" and the app rendered "ered)" as the title of every
MAC-randomised device until the switch to bash. Row rendering prefers the
hostname if it's a real one, falls back to a cleaned-up vendor string
("Huawei" instead of "HUAWEI TECHNOLOGIES CO.,LTD"), and only shows the
IP inside the expanded view -- the title stays human-first. Placeholder
hostnames from certain resolvers (`_gateway`, `dev.opt` on Huawei,
`openwrt.lan`, etc.) are treated as "no useful name" so the vendor wins.

The one thing this screen doesn't do: name devices with MAC
randomisation + mDNS off + LAN discovery permission denied (modern
iPhones on Private Wi-Fi Address, Android 12+ with LAN access off). Those
devices are silent by design -- they don't answer SSDP, mDNS, ICMP,
lockdownd, or anything else on any port -- and the only way to identify
them is to scrape the router's admin UI for its DHCP lease table, which
requires per-vendor credentials. That path (Huawei mesh JSON API, TP-Link
LuCI, ASUS appGet.cgi, Netgear SOAP, Fritz!Box TR-064, Ubiquiti UniFi) is
in the follow-up list because it needs a credentials dialog and per-brand
scrapers.

Each row expands to show IP, MAC, the full vendor string and the hostname
source, plus three action buttons: **Copy IP**, **Send to nmap** and
**Send to RouterSploit**. The last two go through a small cross-module
deep-link contract on the main window (`activate_module(module_id,
target)`) plus a `set_target(str)` method on the destination module; the
sidebar switches, the highlight moves, and the target field is prefilled,
so you never have to type an IP twice.

The **RouterSploit** screen drives the framework at `/opt/routersploit`
without ever leaving the GUI. RouterSploit ships 360 modules: 143 exploits
grouped per vendor (2wire, cisco, dlink, huawei, linksys, mikrotik,
netgear, tplink, ubiquiti, zte and ~15 more), 171 default-credential
brute-forcers per protocol (SSH, FTP, Telnet, HTTP, SNMP), 32 payloads, 6
encoders and 4 scanners (`autopwn`, `router_scan`, `camera_scan`,
`misc_scan`). Each exploit declares its runtime options with `Opt*`
class attributes -- `OptIP`, `OptPort`, `OptBool`, `OptString`,
`OptWordlist` -- and the screen parses those out of the source files
with a regex reader so the form fields match the module. Bool options
become libadwaita `SwitchRow`s; everything else becomes `EntryRow`s
with the module's default pre-filled. There are four sections in a
combo at the top: `Scanners`, `Exploits by vendor`, `Default-credential
brute-force`, and `Search` (substring match across path/name/description
of all 360 modules).

Every run goes through `rsf.py`'s non-interactive shape:

    python3 -u /opt/routersploit/rsf.py -m <module> \
        -s "<opt1> <val1>" -s "<opt2> <val2>" ...

The `-u` matters: without it RouterSploit buffers all output through its
`printer_queue` and only flushes on clean exit -- so anything killed by
the Stop button drops its output on the floor. With `-u` every line the
framework prints streams live to the OutputView through the DBus
helper's `StartStream` path. `Check` uses a different shape (the
framework's `-m` mode does not expose the `check` verb): it drives the
interactive REPL from a heredoc (`use <mod>; set ...; check; exit`) so
the safe-probe path stays a single click. All runs are as root because
the modules do raw sockets, SSH bind, SNMP, ARP scans, and paramiko
key crypto.

**Paramiko 4.0 fix.** RouterSploit's `core/ssh/ssh_client.py` still
imports `paramiko.DSSKey`, which the paramiko 4.0 release removed
because DSA has been cryptographically broken since ~2015 and OpenSSH
>= 7.0 does not accept it either. The stock module crashes with
`AttributeError: module 'paramiko' has no attribute 'DSSKey'` the first
time any SSH-touching module (which is 60% of the exploit and cred
tree) tries to load a key. The port patches `login_pkey` to auto-detect
the key type via `PKey.from_path()` -- writing the PEM blob to a
short-lived temp file, letting paramiko pick RSAKey/ECDSAKey/Ed25519Key
by inspecting the header, and rejecting DSA cleanly via
`paramiko.UnknownKeyType`. Side benefit: the port now also picks up
ECDSA and Ed25519 keys, which the original type-sniffing if/elif
skipped entirely. Backup lives at `ssh_client.py.bak-paramiko4`.

Also worth documenting because it is not obvious: nethunter-pro-app also
carries `rhodep-make-captiveportal`, a generator that turns a git repo of a
static login page into a wifipumpkin3 captive portal (clones, folds
`index.html` into `templates/login.html`, moves css/js/images under `static/`
and rewrites paths, forces the first `<form>` to POST with `name=login` /
`name=password` so captiveflask captures, writes the plugin `.py`, installs
into place and deletes the clone). It is invoked from the wifipumpkin3 screen
and reports honestly when the source has no `<form>` and only submits via
JavaScript, since a portal that silently captures nothing is worse than one
that admits it cannot.

The detail worth knowing: anything needing root goes through a persistent DBus
helper (`org.kali.NetHunterPro.Helper`) so the password is asked once, falling
back to `pkexec` if the helper is absent — the UI thread never runs privileged
code itself. Three module icons are shipped by the app because no installed icon
theme has them (a car for CARsenal, a pumpkin for wifipumpkin3, the pwnagotchi
face), symbolic so they recolour with the theme. The pwnagotchi screen edits the
plugin config live with `tomlkit` to keep comments and formatting, reads the
wpa-sec upload state from the plugin's own SQLite db so its "upload now" and
"free space" buttons agree with the plugin, and only ever deletes captures
wpa-sec has finished with. Note the app is installed with `pip`, so it lands in
the current `python3`'s site-packages: a Python minor-version bump (3.13 → 3.14
happened once) leaves the module behind and the launcher fails with
`ModuleNotFoundError` until it is reinstalled for the new version.

**`cleanup/`** — a weekly disk-space cleanup and a permanent cap on the systemd
journal. The disk had drifted to 50G used, ~2G of it junk that comes straight
back with use, and the journal alone was 633M.

The detail worth knowing: it deletes only regenerable things — caches (GPU
shaders, thumbnails, browser, npm), apt archives, coredumps, fwupd metadata,
rotated logs, `/tmp` — so **no apt hold is affected**, because dpkg owns none of
those files and the holds protect packages. It never touches data (the `/opt`
toolset, libpostal, the databases, the clamav signatures, the AI tools' state).
A journald drop-in sets `SystemMaxUse=100M` so the journal cannot grow back
between runs; the weekly `rhodep-cleanup.timer` reclaims the rest, catching up on
the next boot if the phone was off (`Persistent=true`). Run it by hand with
`sudo rhodep-cleanup`, or `--dry-run` to see what it would free.
