"""Mail Spoof -- swaks-driven email forgery for authorized pentests.

Inspired by emkei.cz but on-device: instead of relying on a shared
public relay whose IP is blacklisted everywhere by now, this module
drives ``swaks`` from the phone and lets the operator either

* deliver directly to the target domain's MX (works when the target
  has no DMARC or only ``p=none``),
* relay through a custom SMTP server the operator provides
  credentials for (Gmail app-password, SendGrid submission, a
  compromised account in a red-team engagement, etc.),
* pipe through msmtp accounts already configured in ``~/.msmtprc``.

The 5322 vs 5321 distinction is exposed as two entries: the
envelope From (``MAIL FROM``, which SPF validates) and the header
From (the display line the mail client shows). Reply-To spoofing is
a separate row because it's what actually makes reply-based
phishing land in the attacker's inbox even when DMARC blocks the
From forgery.

Preflight recon (Target Recon group) hits DNS for SPF, DMARC, DKIM
selectors and MX records so the operator gets a red/yellow/green
verdict on whether the target domain will accept the spoofed mail
before hitting Send.

Bulk mode reads a CSV of ``email,first_name,last_name,company``
(the same shape Gophish uses) and personalises the subject/body
with ``{{first_name}}``-style placeholders.

Nothing gets sent without a Send press. There is no persistent
scheduler.
"""
from __future__ import annotations

import base64
import csv
import os
import re
import shlex
import time
from pathlib import Path

from gi.repository import Adw, GLib, Gtk

from ..executor import Process, Result, run_async
from ..loot_store import get_loot_store, loot_path
from ..module import NHModule, register
from ..widgets import OutputView, toast


# Curated preset X-Mailer strings the operator can pick from a
# ComboRow. Includes a "Custom" that lets them type their own.
_X_MAILERS = [
    "Microsoft Outlook 16.0",
    "Apple Mail (2.3696.120.41.1.4)",
    "Mozilla Thunderbird 115.0",
    "iPhone Mail (20G81)",
    "Android Gmail 2024.02",
    "Zimbra 9.0.0",
    "Lotus Notes 8.5.3",
    "PHPMailer 6.9.1 (https://github.com/PHPMailer/PHPMailer)",
    "Custom…",
]


# Ephemeral throw-away templates. Placeholders use ``{{name}}``
# style so the personalise pass in bulk mode can substitute them.
_TEMPLATES = {
    "(none)": ("", ""),
    "Office 365 password expiry": (
        "Your Office 365 password expires today",
        "Hi {{first_name}},\n\n"
        "Our records show your Office 365 password expires today. "
        "To keep access to your mailbox, please confirm your "
        "credentials using the link below:\n\n"
        "  {{phish_url}}\n\n"
        "This is an automated notification from the IT team at "
        "{{company}}."),
    "DocuSign envelope": (
        "You have a document to review",
        "Hi {{first_name}},\n\n"
        "You have a new document waiting for signature.\n\n"
        "Please review and sign it here:\n"
        "  {{phish_url}}\n\n"
        "-- DocuSign notifications on behalf of {{company}}"),
    "Wire transfer CEO fraud": (
        "Urgent: wire transfer approval",
        "Hi,\n\n"
        "Please process the attached transfer request as soon as "
        "possible. I'm in a meeting and can't call.\n\n"
        "Regards,\n"
        "{{first_name}} {{last_name}}"),
    "Bank alert": (
        "[SECURITY ALERT] Unusual sign-in detected",
        "We detected a sign-in from a new device on your account.\n"
        "If this was you, no action is required. Otherwise please "
        "secure your account here: {{phish_url}}"),
    "DHL delivery": (
        "Your parcel is on hold",
        "Hi {{first_name}},\n\n"
        "Your parcel could not be delivered. Please arrange "
        "re-delivery here:\n  {{phish_url}}\n\n"
        "-- DHL Express"),
}


@register
class MailSpoof(NHModule):
    title = "Mail Spoof"
    icon = "mail-send-symbolic"
    description = ("swaks-driven SMTP spoofing / email forgery for "
                   "authorized red-team engagements. Recon, single "
                   "message and CSV bulk on one page.")
    required_tools = ["swaks", "dig"]

    def __init__(self, app_window):
        super().__init__(app_window)
        # Attachments list -- editable via the file picker button.
        self._attachments: list[str] = []
        # Bulk target rows.
        self._bulk_targets: list[dict] = []
        # Rendered attachment chip rows so we can clear them.
        self._attach_rows: list[Adw.ActionRow] = []
        # Current subprocess (Send button state machine).
        self._proc: Process | None = None

    # ---------------------------------------------------------- build
    def build(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        for m in ("top", "bottom", "start", "end"):
            getattr(box, "set_margin_" + m)(12)

        # ---- legal banner ----
        legal = Adw.PreferencesGroup(
            title="Authorized use only",
            description="Sending mail with a forged From: to a "
                        "domain you do not own or have written "
                        "permission to test is a crime in most "
                        "jurisdictions (CAN-SPAM, Ley 26.388, "
                        "NIS2). Every message this module sends "
                        "logs the recipient into the loot store; "
                        "keep your engagement scope handy.")
        box.append(legal)

        # ---- Compose group ----
        compose = Adw.PreferencesGroup(
            title="Compose",
            description="Envelope-From (MAIL FROM) is what SPF "
                        "checks; header From is what the recipient "
                        "sees. Different values = classic "
                        "misalignment used for spoofing.")

        self.env_from = Adw.EntryRow(
            title="Envelope From (MAIL FROM)")
        compose.add(self.env_from)

        self.hdr_from = Adw.EntryRow(
            title="Header From (display)")
        compose.add(self.hdr_from)

        self.to = Adw.EntryRow(title="To (comma-separated)")
        compose.add(self.to)

        self.reply_to = Adw.EntryRow(title="Reply-To (optional)")
        compose.add(self.reply_to)

        # Optional Cc / Bcc under an expander so the form stays short.
        cc_exp = Adw.ExpanderRow(title="Cc / Bcc / Return-Path")
        self.cc = Adw.EntryRow(title="Cc")
        cc_exp.add_row(self.cc)
        self.bcc = Adw.EntryRow(title="Bcc")
        cc_exp.add_row(self.bcc)
        self.return_path = Adw.EntryRow(
            title="Return-Path (Errors-To)")
        cc_exp.add_row(self.return_path)
        compose.add(cc_exp)

        self.subject = Adw.EntryRow(title="Subject")
        compose.add(self.subject)

        self.html_mode = Adw.SwitchRow(
            title="HTML body",
            subtitle="Off: text/plain; on: text/html + inline HTML")
        compose.add(self.html_mode)

        # Body: TextView inside a small ScrolledWindow so it doesn't
        # eat the whole page.
        body_row = Adw.ActionRow(title="Body")
        compose.add(body_row)
        body_scroll = Gtk.ScrolledWindow()
        body_scroll.set_min_content_height(160)
        body_scroll.set_hexpand(True)
        self.body_view = Gtk.TextView()
        self.body_view.set_wrap_mode(Gtk.WrapMode.WORD)
        self.body_view.set_monospace(False)
        body_scroll.set_child(self.body_view)
        box.append(compose)
        box.append(body_scroll)

        # ---- Advanced headers group ----
        adv = Adw.PreferencesGroup(
            title="Advanced headers",
            description="Everything here is optional. Leave blank "
                        "to let swaks/RFC defaults kick in.")

        self.mailer = Adw.ComboRow(title="X-Mailer")
        self.mailer.set_model(Gtk.StringList.new(_X_MAILERS))
        adv.add(self.mailer)

        self.mailer_custom = Adw.EntryRow(
            title="Custom X-Mailer (used when Custom… is picked)")
        adv.add(self.mailer_custom)

        self.msg_id = Adw.EntryRow(
            title="Message-ID (blank = auto-generated)")
        adv.add(self.msg_id)

        self.date_hdr = Adw.EntryRow(
            title="Date (blank = now)")
        adv.add(self.date_hdr)

        self.priority = Adw.ComboRow(title="Priority")
        self.priority.set_model(Gtk.StringList.new(
            ["Normal (3)", "High (1)", "Low (5)"]))
        adv.add(self.priority)

        self.ehlo = Adw.EntryRow(
            title="HELO / EHLO (banner sent to the MX)")
        adv.add(self.ehlo)

        # Free-form headers: one "Key: value" per line.
        extra_row = Adw.ActionRow(
            title="Extra headers (one per line: Key: value)")
        adv.add(extra_row)
        extra_scroll = Gtk.ScrolledWindow()
        extra_scroll.set_min_content_height(80)
        extra_scroll.set_hexpand(True)
        self.extra_view = Gtk.TextView()
        self.extra_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.extra_view.set_monospace(True)
        extra_scroll.set_child(self.extra_view)
        box.append(adv)
        box.append(extra_scroll)

        # ---- Attachments ----
        self._attach_group = Adw.PreferencesGroup(
            title="Attachments",
            description="MIME type is guessed from the extension. "
                        "Sizes over a few MB may hit the recipient "
                        "MX's message limit.")
        add_row = Adw.ActionRow(
            title="Add file", subtitle="Opens a file picker")
        add_btn = Gtk.Button(label="Add",
                             valign=Gtk.Align.CENTER)
        add_btn.connect("clicked",
                        lambda _b: self._pick_attachment())
        add_row.add_suffix(add_btn)
        self._attach_group.add(add_row)
        box.append(self._attach_group)

        # ---- Delivery group ----
        deliv = Adw.PreferencesGroup(
            title="Delivery",
            description="Direct-to-MX works only when the target "
                        "domain has no DMARC or p=none. Everything "
                        "else needs a relay you can authenticate to.")

        self.deliv_mode = Adw.ComboRow(title="Method")
        self.deliv_mode.set_model(Gtk.StringList.new([
            "Direct to target's MX (--copy-routing)",
            "Custom SMTP server",
            "msmtp account (~/.msmtprc)",
        ]))
        deliv.add(self.deliv_mode)

        self.smtp_server = Adw.EntryRow(title="SMTP server")
        deliv.add(self.smtp_server)
        self.smtp_port = Adw.SpinRow.new_with_range(1, 65535, 1)
        self.smtp_port.set_title("Port")
        self.smtp_port.set_value(587)
        deliv.add(self.smtp_port)

        self.smtp_tls = Adw.ComboRow(title="TLS")
        self.smtp_tls.set_model(Gtk.StringList.new([
            "STARTTLS (587)", "TLS on connect (465)", "None (25)",
        ]))
        deliv.add(self.smtp_tls)

        self.smtp_auth = Adw.SwitchRow(title="Authentication")
        deliv.add(self.smtp_auth)
        self.smtp_user = Adw.EntryRow(title="User")
        deliv.add(self.smtp_user)
        self.smtp_pass = Adw.PasswordEntryRow(title="Password")
        deliv.add(self.smtp_pass)
        self.smtp_authtype = Adw.ComboRow(title="Auth mechanism")
        self.smtp_authtype.set_model(Gtk.StringList.new([
            "LOGIN", "PLAIN", "CRAM-MD5", "NTLM",
        ]))
        deliv.add(self.smtp_authtype)

        self.msmtp_account = Adw.EntryRow(
            title="msmtp account name (blank = default)")
        deliv.add(self.msmtp_account)
        box.append(deliv)

        # ---- Templates ----
        tmpl = Adw.PreferencesGroup(
            title="Templates",
            description="Prefills Subject + Body. Placeholders like "
                        "{{first_name}} get substituted from CSV in "
                        "bulk mode.")
        self.template = Adw.ComboRow(title="Template")
        self.template.set_model(Gtk.StringList.new(
            list(_TEMPLATES.keys())))
        self.template.connect(
            "notify::selected",
            lambda *_: self._apply_template())
        tmpl.add(self.template)

        self.phish_url = Adw.EntryRow(
            title="Phish URL (substituted into {{phish_url}})")
        tmpl.add(self.phish_url)
        box.append(tmpl)

        # ---- Target Recon (SPF/DMARC/DKIM/MX) ----
        recon = Adw.PreferencesGroup(
            title="Target recon",
            description="Runs dig for SPF, DMARC, DKIM guess, MX. "
                        "Green = spoof-friendly; red = will bounce.")
        self.recon_domain = Adw.EntryRow(
            title="Target domain (e.g. victim.com)")
        recon.add(self.recon_domain)
        recon_row = Adw.ActionRow(
            title="Check",
            subtitle="MX + SPF + DMARC + DKIM (default._domainkey)")
        recon_btn = Gtk.Button(label="Check",
                               valign=Gtk.Align.CENTER)
        recon_btn.connect("clicked",
                          lambda _b: self._do_recon())
        recon_row.add_suffix(recon_btn)
        recon.add(recon_row)

        # Rows we fill after dig comes back.
        self.recon_mx = Adw.ActionRow(title="MX", subtitle="—")
        recon.add(self.recon_mx)
        self.recon_spf = Adw.ActionRow(title="SPF", subtitle="—")
        recon.add(self.recon_spf)
        self.recon_dmarc = Adw.ActionRow(title="DMARC",
                                         subtitle="—")
        recon.add(self.recon_dmarc)
        self.recon_dkim = Adw.ActionRow(title="DKIM",
                                        subtitle="—")
        recon.add(self.recon_dkim)
        self.recon_verdict = Adw.ActionRow(title="Verdict",
                                           subtitle="—")
        recon.add(self.recon_verdict)
        box.append(recon)

        # ---- Bulk CSV ----
        bulk = Adw.PreferencesGroup(
            title="Bulk (CSV)",
            description="CSV: email,first_name,last_name,company. "
                        "Placeholders in subject/body get "
                        "substituted per row.")
        self.csv_path = Adw.EntryRow(title="CSV path")
        bulk.add(self.csv_path)
        load_row = Adw.ActionRow(
            title="Load targets",
            subtitle="—")
        load_btn = Gtk.Button(label="Load",
                              valign=Gtk.Align.CENTER)
        load_btn.connect("clicked",
                         lambda _b: self._load_csv(load_row))
        load_row.add_suffix(load_btn)
        bulk.add(load_row)
        self.bulk_delay = Adw.SpinRow.new_with_range(0, 300, 1)
        self.bulk_delay.set_title("Delay between sends (s)")
        self.bulk_delay.set_value(3)
        bulk.add(self.bulk_delay)
        box.append(bulk)

        # ---- Send / dry-run buttons ----
        actions = Adw.PreferencesGroup(title="Send")
        send_row = Adw.ActionRow(
            title="Send once",
            subtitle="Uses To/Cc/Bcc as-is; ignores CSV")
        self.send_btn = Gtk.Button(label="Send",
                                   valign=Gtk.Align.CENTER)
        self.send_btn.add_css_class("destructive-action")
        self.send_btn.connect("clicked",
                              lambda _b: self._send_single())
        send_row.add_suffix(self.send_btn)
        dry_btn = Gtk.Button(label="Dry-run",
                             valign=Gtk.Align.CENTER)
        dry_btn.connect("clicked",
                        lambda _b: self._dry_run())
        send_row.add_suffix(dry_btn)
        show_btn = Gtk.Button(label="Show cmd",
                              valign=Gtk.Align.CENTER)
        show_btn.connect("clicked",
                         lambda _b: self._show_cmd())
        send_row.add_suffix(show_btn)
        actions.add(send_row)

        bulk_row = Adw.ActionRow(
            title="Send to every row in CSV",
            subtitle="Per-row personalisation of placeholders")
        self.bulk_btn = Gtk.Button(label="Send bulk",
                                   valign=Gtk.Align.CENTER)
        self.bulk_btn.add_css_class("destructive-action")
        self.bulk_btn.connect("clicked",
                              lambda _b: self._send_bulk())
        bulk_row.add_suffix(self.bulk_btn)
        self.stop_btn = Gtk.Button(label="Stop",
                                   valign=Gtk.Align.CENTER)
        self.stop_btn.set_sensitive(False)
        self.stop_btn.connect("clicked",
                              lambda _b: self._stop())
        bulk_row.add_suffix(self.stop_btn)
        actions.add(bulk_row)
        box.append(actions)

        # ---- output log ----
        self.output = OutputView()
        box.append(self.output)

        return box

    # ---------------------------------------------------------- helpers
    def _apply_template(self) -> None:
        idx = self.template.get_selected()
        name = list(_TEMPLATES.keys())[idx]
        subject, body = _TEMPLATES[name]
        if subject or body:
            self.subject.set_text(subject)
            self.body_view.get_buffer().set_text(body, len(body))

    def _pick_attachment(self) -> None:
        # Use a native file chooser dialog if we can; fall back to
        # asking via a toast if the display doesn't support it.
        dlg = Gtk.FileDialog()
        dlg.set_title("Attach file")

        def cb(source, result):
            try:
                gfile = source.open_finish(result)
            except Exception:
                return
            if not gfile:
                return
            path = gfile.get_path()
            self._attachments.append(path)
            self._render_attachments()

        dlg.open(self.app_window, None, cb)

    def _render_attachments(self) -> None:
        for r in self._attach_rows:
            self._attach_group.remove(r)
        self._attach_rows = []
        for i, p in enumerate(self._attachments):
            row = Adw.ActionRow(
                title=os.path.basename(p), subtitle=p)
            rm = Gtk.Button(label="Remove",
                            valign=Gtk.Align.CENTER)
            rm.add_css_class("destructive-action")
            rm.connect(
                "clicked",
                lambda _b, idx=i: self._remove_attachment(idx))
            row.add_suffix(rm)
            self._attach_group.add(row)
            self._attach_rows.append(row)

    def _remove_attachment(self, idx: int) -> None:
        if 0 <= idx < len(self._attachments):
            del self._attachments[idx]
            self._render_attachments()

    def _body_text(self) -> str:
        buf = self.body_view.get_buffer()
        start, end = buf.get_start_iter(), buf.get_end_iter()
        return buf.get_text(start, end, False) or ""

    def _extra_headers(self) -> list[str]:
        buf = self.extra_view.get_buffer()
        start, end = buf.get_start_iter(), buf.get_end_iter()
        text = buf.get_text(start, end, False) or ""
        out = []
        for line in text.splitlines():
            line = line.strip()
            if not line or ":" not in line:
                continue
            out.append(line)
        return out

    # -------------------------------------------------- swaks builder
    def _build_swaks_cmd(
            self, to: str, subject: str, body: str,
            personalise: dict | None = None) -> list[str]:
        """Return the argv for one swaks invocation. Attachments are
        added; MIME is guessed by swaks based on the extension.

        If ``personalise`` is set the subject / body get
        ``{{key}}`` substitution."""
        if personalise:
            for k, v in personalise.items():
                token = "{{" + k + "}}"
                subject = subject.replace(token, v)
                body = body.replace(token, v)
        if self.phish_url.get_text().strip():
            phish = self.phish_url.get_text().strip()
            subject = subject.replace("{{phish_url}}", phish)
            body = body.replace("{{phish_url}}", phish)

        argv: list[str] = ["swaks"]
        # Recipients
        argv += ["--to", to]
        for name, entry in (
                ("Cc", self.cc.get_text()),
                ("Bcc", self.bcc.get_text()),
        ):
            entry = entry.strip()
            if entry:
                for addr in [a.strip() for a in entry.split(",")
                              if a.strip()]:
                    argv += ["--" + name.lower(), addr]

        # Envelope From (5321) and header From (5322).
        env_from = self.env_from.get_text().strip()
        hdr_from = self.hdr_from.get_text().strip()
        if env_from:
            argv += ["--from", env_from]
        if hdr_from:
            argv += ["--h-From", hdr_from]

        rt = self.reply_to.get_text().strip()
        if rt:
            argv += ["--h-Reply-To", rt]

        rp = self.return_path.get_text().strip()
        if rp:
            argv += ["--h-Return-Path", rp]

        argv += ["--h-Subject", subject]

        # X-Mailer (preset or custom).
        idx = self.mailer.get_selected()
        picked = _X_MAILERS[idx]
        if picked == "Custom…":
            xm = self.mailer_custom.get_text().strip()
        else:
            xm = picked
        if xm:
            argv += ["--add-header", "X-Mailer: " + xm]

        # Message-ID and Date if the operator overrode them.
        mid = self.msg_id.get_text().strip()
        if mid:
            argv += ["--h-Message-ID", mid]
        dh = self.date_hdr.get_text().strip()
        if dh:
            argv += ["--h-Date", dh]

        # Priority: X-Priority header.
        pmap = {0: "3 (Normal)", 1: "1 (High)", 2: "5 (Low)"}
        prio = pmap[self.priority.get_selected()]
        argv += ["--add-header", "X-Priority: " + prio]

        # HELO / EHLO
        ehlo = self.ehlo.get_text().strip()
        if ehlo:
            argv += ["--ehlo", ehlo]

        # Extra headers.
        for h in self._extra_headers():
            argv += ["--add-header", h]

        # Attachments.
        for a in self._attachments:
            argv += ["--attach", "@" + a]

        # Body: write to a temp file so we can control Content-Type.
        # For HTML mode we want ``text/html; charset=UTF-8``.
        stamp = time.strftime("%Y%m%d-%H%M%S")
        body_tmp = "/tmp/nhp-mailspoof-body-%s.txt" % stamp
        with open(body_tmp, "w", encoding="utf-8") as fp:
            fp.write(body)
        if self.html_mode.get_active():
            argv += ["--add-header",
                     "Content-Type: text/html; charset=UTF-8"]
        argv += ["--body", "@" + body_tmp]

        # Delivery method.
        mode = self.deliv_mode.get_selected()
        if mode == 0:
            # Direct to MX -- use the target domain (from To).
            first_to = to.split(",", 1)[0].strip()
            if "@" in first_to:
                argv += ["--copy-routing",
                         first_to.split("@", 1)[1]]
        elif mode == 1:
            srv = self.smtp_server.get_text().strip()
            port = int(self.smtp_port.get_value())
            if srv:
                argv += ["--server", "%s:%d" % (srv, port)]
            tls = self.smtp_tls.get_selected()
            if tls == 0:
                argv += ["-tls"]
            elif tls == 1:
                argv += ["-tlsc"]
            if self.smtp_auth.get_active():
                mech = ["LOGIN", "PLAIN", "CRAM-MD5", "NTLM"][
                    self.smtp_authtype.get_selected()]
                argv += ["--auth", mech,
                         "--auth-user",
                         self.smtp_user.get_text().strip(),
                         "--auth-password",
                         self.smtp_pass.get_text()]
        elif mode == 2:
            acct = self.msmtp_account.get_text().strip()
            argv = ["msmtp"]
            if acct:
                argv += ["-a", acct]
            argv += ["--"] + [to]
            # msmtp reads from stdin; we handle that in _send_single.

        return argv

    # ------------------------------------------------- recon (DNS)
    def _do_recon(self) -> None:
        dom = self.recon_domain.get_text().strip()
        if not dom:
            toast(self.app_window,
                  "Set the target domain first")
            return
        # Four dig queries in parallel; each updates its own row.
        script = (
            "echo === MX ===; dig +short MX %s; "
            "echo === SPF ===; dig +short TXT %s | "
            "  grep -i 'v=spf'; "
            "echo === DMARC ===; dig +short TXT _dmarc.%s; "
            "echo === DKIM ===; dig +short TXT "
            "  default._domainkey.%s; "
            "echo"
        ) % (dom, dom, dom, dom)

        def done(r: Result) -> None:
            out = r.stdout or ""
            self.output.append(out)
            self._parse_recon(out)

        run_async(["sh", "-c", script], done,
                  root=False, timeout=15)

    def _parse_recon(self, text: str) -> None:
        blocks: dict[str, list[str]] = {}
        current = None
        for line in text.splitlines():
            m = re.match(r"^=== (\w+) ===$", line)
            if m:
                current = m.group(1)
                blocks[current] = []
                continue
            if current and line.strip():
                blocks[current].append(line.strip())

        mx = ", ".join(blocks.get("MX", []))[:100] or "(none)"
        self.recon_mx.set_subtitle(mx)

        spf_lines = blocks.get("SPF", [])
        spf = " ".join(spf_lines) if spf_lines else "(no SPF)"
        self.recon_spf.set_subtitle(spf[:120])

        dmarc_lines = blocks.get("DMARC", [])
        dmarc = " ".join(dmarc_lines) if dmarc_lines \
                 else "(no DMARC)"
        self.recon_dmarc.set_subtitle(dmarc[:120])

        dkim_lines = blocks.get("DKIM", [])
        dkim = " ".join(dkim_lines) if dkim_lines else \
               "(none at default._domainkey)"
        self.recon_dkim.set_subtitle(dkim[:120])

        # Verdict: red if DMARC p=reject with strict alignment,
        # yellow if p=quarantine, green otherwise. This is a rough
        # heuristic; the operator should still verify manually.
        low = dmarc.lower()
        if "p=reject" in low:
            self.recon_verdict.set_subtitle(
                "🔴 DMARC p=reject -- spoofed From will bounce")
        elif "p=quarantine" in low:
            self.recon_verdict.set_subtitle(
                "🟡 DMARC p=quarantine -- likely spam folder")
        elif "p=none" in low or "(no DMARC)" in dmarc:
            self.recon_verdict.set_subtitle(
                "🟢 No enforced DMARC -- spoof likely lands in inbox")
        else:
            self.recon_verdict.set_subtitle(
                "? DMARC record present but policy unclear")

    # ----------------------------------------------- bulk targets
    def _load_csv(self, row: Adw.ActionRow) -> None:
        path = self.csv_path.get_text().strip()
        if not path:
            toast(self.app_window,
                  "Set a CSV path first")
            return
        try:
            targets: list[dict] = []
            with open(path) as fp:
                reader = csv.DictReader(fp)
                for r in reader:
                    if not r.get("email"):
                        continue
                    targets.append(r)
        except OSError as e:
            row.set_subtitle("failed: " + str(e))
            return
        self._bulk_targets = targets
        row.set_subtitle("loaded %d targets" % len(targets))

    # ---------------------------------------------- send / dry-run
    def _send_single(self) -> None:
        to = self.to.get_text().strip()
        if not to:
            toast(self.app_window, "Set To first")
            return
        subject = self.subject.get_text()
        body = self._body_text()
        argv = self._build_swaks_cmd(to, subject, body)
        self._fire_argv(argv, to)

    def _dry_run(self) -> None:
        to = self.to.get_text().strip()
        if not to:
            toast(self.app_window, "Set To first")
            return
        argv = self._build_swaks_cmd(
            to, self.subject.get_text(), self._body_text())
        # --suppress-data prints the SMTP conversation but skips DATA
        argv += ["--suppress-data"]
        self._fire_argv(argv, to, dry=True)

    def _show_cmd(self) -> None:
        to = self.to.get_text().strip() or "recipient@example.com"
        argv = self._build_swaks_cmd(
            to, self.subject.get_text(), self._body_text())
        self.output.append(
            "# " + " ".join(shlex.quote(a) for a in argv)
            + "\n")

    def _send_bulk(self) -> None:
        if not self._bulk_targets:
            toast(self.app_window,
                  "Load a CSV first")
            return
        self.bulk_btn.set_sensitive(False)
        self.stop_btn.set_sensitive(True)
        self._bulk_running = True
        # Chained fire: one target per delay tick. GLib timer
        # runs off the main loop so the UI stays responsive.
        idx = [0]

        def one() -> bool:
            if not self._bulk_running:
                self.output.append("[bulk stopped]\n")
                self.bulk_btn.set_sensitive(True)
                self.stop_btn.set_sensitive(False)
                return False
            if idx[0] >= len(self._bulk_targets):
                self.output.append(
                    "[bulk done: %d sent]\n"
                    % len(self._bulk_targets))
                self.bulk_btn.set_sensitive(True)
                self.stop_btn.set_sensitive(False)
                return False
            row = self._bulk_targets[idx[0]]
            idx[0] += 1
            personalise = {
                "first_name": row.get("first_name", ""),
                "last_name": row.get("last_name", ""),
                "company": row.get("company", ""),
                "email": row.get("email", ""),
            }
            argv = self._build_swaks_cmd(
                row["email"],
                self.subject.get_text(),
                self._body_text(),
                personalise=personalise)
            self._fire_argv(argv, row["email"])
            # Keep the loop alive until the queue drains; delay
            # between sends per operator preference.
            return True

        # Fire the first one immediately, then use a repeating timer.
        one()
        GLib.timeout_add_seconds(
            int(self.bulk_delay.get_value()) or 1, one)

    def _stop(self) -> None:
        self._bulk_running = False
        if self._proc is not None:
            try:
                self._proc.stop()
            except Exception:
                pass

    def _fire_argv(self, argv: list[str], to: str,
                   dry: bool = False) -> None:
        self.output.append(
            "$ " + " ".join(shlex.quote(a) for a in argv) + "\n")

        stamp = time.strftime("%Y%m%d-%H%M%S")
        log_path = loot_path(
            "mail_spoof",
            "send-%s-%s.log" % (
                re.sub(r"[^A-Za-z0-9_.-]+", "_", to)[:40],
                stamp))
        loot_id = get_loot_store().record(
            module="mail_spoof",
            type="dry_run" if dry else "sent_message",
            target=to, path=log_path,
            notes="dry-run" if dry else "attempted delivery")

        def on_line(text: str) -> None:
            self.output.append(text)
            try:
                with open(log_path, "a") as fp:
                    fp.write(text)
            except OSError:
                pass

        def on_done(code: int) -> None:
            self.output.append("[swaks exit %d]\n\n" % code)
            self._proc = None
            get_loot_store().refresh_size(loot_id)
            if code == 0:
                get_loot_store().append_notes(
                    loot_id, "delivered")
            else:
                get_loot_store().append_notes(
                    loot_id, "failed (exit %d)" % code)

        # swaks doesn't need root; msmtp doesn't either. Everything
        # runs as the login user, which keeps the audit trail clean.
        self._proc = Process(
            argv, on_line, on_done, root=False)
        self._proc.start()

    def set_target(self, target: str) -> None:
        """Simple deep-link: accept an email address and pre-fill
        the To field. Callers from phishkin3 / karma may want to
        immediately compose to a captured victim."""
        if target:
            self.to.set_text(target.strip())
