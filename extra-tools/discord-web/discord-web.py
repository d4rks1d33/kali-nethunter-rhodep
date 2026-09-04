#!/usr/bin/env python3
# discord-web: Discord's web client in a native aarch64 QtWebEngine (Chromium)
# window. No box64, no Electron -- QtWebEngine IS Chromium, so discord.com/app
# works exactly as in Chrome (login, DMs, text). Phone-style single-panel
# navigation (Android UA + a device scale factor set in the wrapper).
#
# The user's Discord session (cookies/localStorage) lives at
# ~/.local/share/discord-web/ on the device -- per-user runtime data, never in
# the repo.
import sys, os

os.environ["QT_IM_MODULE"] = "none"   # no auto on-screen keyboard

from PyQt6.QtCore import QUrl, Qt, QStandardPaths, QTimer
from PyQt6.QtWidgets import QApplication, QMainWindow
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import (QWebEngineProfile, QWebEnginePage,
                                   QWebEngineDownloadRequest)

DATA = os.path.expanduser("~/.local/share/discord-web")
os.makedirs(DATA, exist_ok=True)

app = QApplication(sys.argv)
app.setApplicationName("Discord")
app.setDesktopFileName("discord-web")

# Named, persistent profile: keeps the session (token in localStorage) + cookies
# across restarts so you do not re-login every launch.
profile = QWebEngineProfile("discord", app)
profile.setPersistentStoragePath(DATA)
profile.setCachePath(os.path.join(DATA, "cache"))
profile.setPersistentCookiesPolicy(
    QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies)
# Android Chrome UA -> Discord serves its mobile web layout (fits a phone),
# and it dodges Discord's recent Linux/VPN block.
profile.setHttpUserAgent(
    "Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36")

# Save downloads to ~/Downloads (created if absent).
DOWNLOADS = QStandardPaths.writableLocation(
    QStandardPaths.StandardLocation.DownloadLocation)
os.makedirs(DOWNLOADS, exist_ok=True)

def on_download(item: QWebEngineDownloadRequest):
    # Keep the filename Discord suggests; put it in ~/Downloads.
    suggested = os.path.basename(item.suggestedFileName())
    dest = os.path.join(DOWNLOADS, suggested or "discord-download")
    # Avoid clobbering existing files.
    base, ext = os.path.splitext(dest)
    n = 1
    while os.path.exists(dest):
        dest = f"{base}_{n}{ext}"
        n += 1
    item.setDownloadDirectory(DOWNLOADS)
    item.setDownloadFileName(os.path.basename(dest))
    item.accept()

profile.downloadRequested.connect(on_download)

# JS injected after every page load.
#
# Discord's image anchors use a stable, build-independent attribute:
#   <a data-role="img" href="https://cdn.discordapp.com/attachments/...">
# This is a literal string in Discord's source (not a hashed class name) and
# has been stable across builds for years.
#
# We watch for these anchors via MutationObserver and overlay a ⬇ button on
# each image.  Tapping the button triggers a custom URL scheme
# ("discord-dl://...") which Python catches in acceptNavigationRequest() and
# converts to a real QWebEnginePage.download() call -> on_download() ->
# ~/Downloads.  stopPropagation() prevents Discord from opening the lightbox.
_INJECT_JS = r"""
(function() {
    if (window.__rhodepDl3) return;
    window.__rhodepDl3 = true;

    var CDN = ['cdn.discordapp.com/attachments', 'media.discordapp.net/attachments'];

    function isCdn(url) {
        return CDN.some(function(h){ return url && url.indexOf(h) !== -1; });
    }

    function triggerDownload(cdnUrl) {
        // Fetch the file and turn it into a blob: URL, then click a temporary
        // <a download> element.  This is the most reliable way to trigger
        // QWebEngineDownloadRequest from injected JS -- it goes through the
        // engine's regular download pipeline regardless of Content-Disposition.
        var fname = cdnUrl.split('?')[0].split('/').pop() || 'discord-file';
        fetch(cdnUrl, {credentials: 'omit'})
            .then(function(r) { return r.blob(); })
            .then(function(blob) {
                var burl = URL.createObjectURL(blob);
                var a = document.createElement('a');
                a.href = burl;
                a.download = fname;
                document.body.appendChild(a);
                a.click();
                setTimeout(function() {
                    document.body.removeChild(a);
                    URL.revokeObjectURL(burl);
                }, 1000);
            })
            .catch(function(err) {
                // Fallback: open in new tab (user can long-press to save).
                window.open(cdnUrl, '_blank');
            });
    }

    function inject(anchor) {
        if (anchor.dataset.dlDone) return;
        anchor.dataset.dlDone = '1';

        var cdnUrl = anchor.href;
        if (!isCdn(cdnUrl)) return;

        var wrapper = anchor.closest('div');
        if (!wrapper) return;

        if (getComputedStyle(wrapper).position === 'static')
            wrapper.style.position = 'relative';

        var btn = document.createElement('button');
        btn.textContent = '\u2b07';
        btn.title = 'Download';
        btn.style.cssText = [
            'position:absolute',
            'top:6px',
            'right:6px',
            'z-index:9999',
            'background:rgba(0,0,0,0.65)',
            'color:#fff',
            'font-size:20px',
            'line-height:1',
            'width:36px',
            'height:36px',
            'display:flex',
            'align-items:center',
            'justify-content:center',
            'border-radius:6px',
            'border:none',
            'cursor:pointer',
            '-webkit-tap-highlight-color:transparent',
            'user-select:none',
            'padding:0',
        ].join(';');
        btn.addEventListener('click', function(e) {
            e.preventDefault();
            e.stopPropagation();
            triggerDownload(cdnUrl);
        });
        wrapper.appendChild(btn);
    }

    function scanNode(node) {
        if (node.nodeType !== 1) return;
        if (node.matches('a[data-role="img"]')) { inject(node); return; }
        node.querySelectorAll('a[data-role="img"]').forEach(inject);
    }

    document.querySelectorAll('a[data-role="img"]').forEach(inject);

    new MutationObserver(function(muts) {
        muts.forEach(function(m) {
            m.addedNodes.forEach(scanNode);
        });
    }).observe(document.body, { childList: true, subtree: true });
})();
"""

class DiscordPage(QWebEnginePage):
    """Custom page: CDN links opened as new tabs are downloaded instead."""

    def newWindowRequested(self, request):
        url = request.requestedUrl()
        if (url.host().endswith('cdn.discordapp.com') or
                url.host().endswith('media.discordapp.net')):
            self.download(url)
        else:
            self.load(url)


win = QMainWindow()
win.setWindowTitle("Discord")

view = QWebEngineView()
page = DiscordPage(profile, view)
view.setPage(page)

def inject_js():
    page.runJavaScript(_INJECT_JS)

view.loadFinished.connect(lambda ok: inject_js())

view.load(QUrl("https://discord.com/app"))

win.setCentralWidget(view)
# Maximised (not fullscreen) so the Plasma Mobile nav bar stays -- you can
# minimise/close and raise the keyboard yourself.
win.showMaximized()

sys.exit(app.exec())
