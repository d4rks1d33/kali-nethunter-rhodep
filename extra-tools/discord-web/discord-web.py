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

# Save downloads to ~/Downloads/Discord files (created if absent).
DOWNLOADS = os.path.join(
    QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DownloadLocation),
    "Discord files")
os.makedirs(DOWNLOADS, exist_ok=True)

def on_download(item: QWebEngineDownloadRequest):
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

    // --- Toast notification ---
    function makeToast() {
        var t = document.createElement('div');
        t.style.cssText = [
            'position:fixed',
            'bottom:24px',
            'left:50%',
            'transform:translateX(-50%)',
            'z-index:99999',
            'background:rgba(20,20,30,0.93)',
            'color:#fff',
            'font-family:sans-serif',
            'font-size:15px',
            'padding:12px 20px',
            'border-radius:14px',
            'box-shadow:0 4px 24px rgba(0,0,0,0.5)',
            'max-width:85vw',
            'text-align:center',
            'pointer-events:none',
            'transition:opacity 0.3s',
            'white-space:nowrap',
            'overflow:hidden',
            'text-overflow:ellipsis',
        ].join(';');
        document.body.appendChild(t);
        return t;
    }

    function showToast(msg, duration) {
        var t = makeToast();
        t.textContent = msg;
        if (duration) {
            setTimeout(function() {
                t.style.opacity = '0';
                setTimeout(function() {
                    if (t.parentNode) t.parentNode.removeChild(t);
                }, 350);
            }, duration);
        }
        return t;
    }

    function triggerDownload(cdnUrl) {
        var fname = cdnUrl.split('?')[0].split('/').pop() || 'discord-file';
        var shortName = fname.length > 30 ? fname.substring(0,28) + '…' : fname;

        // Show "downloading" toast with live progress.
        var toast = makeToast();
        toast.textContent = '⬇ Descargando ' + shortName + '…';

        fetch(cdnUrl, {credentials: 'omit'})
            .then(function(r) {
                // Show progress if Content-Length is known.
                var total = parseInt(r.headers.get('Content-Length') || '0', 10);
                if (!total || !r.body) return r.blob();

                var reader = r.body.getReader();
                var received = 0;
                var chunks = [];
                function pump() {
                    return reader.read().then(function(result) {
                        if (result.done) {
                            return new Blob(chunks);
                        }
                        chunks.push(result.value);
                        received += result.value.length;
                        var pct = Math.round(received / total * 100);
                        var kb  = Math.round(received / 1024);
                        var tot = Math.round(total / 1024);
                        toast.textContent = '⬇ ' + shortName + ' — ' + pct + '% (' + kb + '/' + tot + ' KB)';
                        return pump();
                    });
                }
                return pump();
            })
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
                // Replace toast with success.
                toast.textContent = '✓ Guardado: ' + shortName;
                toast.style.background = 'rgba(30,130,70,0.93)';
                setTimeout(function() {
                    toast.style.opacity = '0';
                    setTimeout(function() {
                        if (toast.parentNode) toast.parentNode.removeChild(toast);
                    }, 400);
                }, 2500);
            })
            .catch(function(err) {
                toast.textContent = '⚠ Error — abriendo en pestaña';
                toast.style.background = 'rgba(150,40,40,0.93)';
                setTimeout(function() {
                    if (toast.parentNode) toast.parentNode.removeChild(toast);
                }, 3000);
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
            'font-size:24px',
            'line-height:1',
            'width:48px',
            'height:48px',
            'display:flex',
            'align-items:center',
            'justify-content:center',
            'border-radius:8px',
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
