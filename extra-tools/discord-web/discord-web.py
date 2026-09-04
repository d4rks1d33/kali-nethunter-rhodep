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
# Covers all Discord attachment types via stable, build-independent selectors:
#
#  TYPE              SELECTOR                        URL FROM
#  Images            a[data-role="img"]              .href   (cdn.discordapp.com)
#  Video / GIF       [class*="embedVideo"] video     .src    (cdn / media proxy)
#  Audio / files     a[class*="fileNameLink"]        .href   (cdn.discordapp.com)
#
# All three paths share the same toast+fetch+blob download pipeline.
# CSS module hash suffixes change per Discord deploy; only the prefix
# (e.g. "embedVideo", "fileNameLink") is stable and matched with [class*=].
_INJECT_JS = r"""
(function() {
    if (window.__rhodepDl4) return;
    window.__rhodepDl4 = true;

    // Discord CDN domains. /attachments is present for user-uploaded files.
    // Videos / GIFs may also come from media.discordapp.net without /attachments.
    function isCdn(url) {
        if (!url) return false;
        return url.indexOf('cdn.discordapp.com') !== -1 ||
               url.indexOf('media.discordapp.net') !== -1 ||
               url.indexOf('images-ext') !== -1;
    }

    // ── Toast ────────────────────────────────────────────────────────────────
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

    function dismissToast(t, delay) {
        setTimeout(function() {
            t.style.opacity = '0';
            setTimeout(function() { if (t.parentNode) t.parentNode.removeChild(t); }, 350);
        }, delay);
    }

    // ── Core download: fetch → blob → <a download> ────────────────────────
    function triggerDownload(rawUrl) {
        // Strip query params to get the clean filename, but keep the full URL
        // for the actual fetch (Discord's signed URLs need the query string).
        var fname = rawUrl.split('?')[0].split('/').pop() || 'discord-file';
        var short = fname.length > 30 ? fname.slice(0, 28) + '…' : fname;
        var toast = makeToast();
        toast.textContent = '⬇ Descargando ' + short + '…';

        fetch(rawUrl, {credentials: 'omit'})
            .then(function(r) {
                var total = parseInt(r.headers.get('Content-Length') || '0', 10);
                if (!total || !r.body) return r.blob();
                var reader = r.body.getReader(), received = 0, chunks = [];
                function pump() {
                    return reader.read().then(function(res) {
                        if (res.done) return new Blob(chunks);
                        chunks.push(res.value);
                        received += res.value.length;
                        var pct = Math.round(received / total * 100);
                        var kb  = Math.round(received / 1024);
                        var tot = Math.round(total / 1024);
                        toast.textContent = '⬇ ' + short + ' — ' + pct + '% (' + kb + '/' + tot + ' KB)';
                        return pump();
                    });
                }
                return pump();
            })
            .then(function(blob) {
                var burl = URL.createObjectURL(blob);
                var a = document.createElement('a');
                a.href = burl; a.download = fname;
                document.body.appendChild(a); a.click();
                setTimeout(function() { document.body.removeChild(a); URL.revokeObjectURL(burl); }, 1000);
                toast.textContent = '✓ Guardado: ' + short;
                toast.style.background = 'rgba(30,130,70,0.93)';
                dismissToast(toast, 2500);
            })
            .catch(function() {
                toast.textContent = '⚠ Error — abriendo en pestaña';
                toast.style.background = 'rgba(150,40,40,0.93)';
                dismissToast(toast, 3000);
                window.open(rawUrl, '_blank');
            });
    }

    // ── Button factory ───────────────────────────────────────────────────────
    function makeBtn(url) {
        var btn = document.createElement('button');
        btn.textContent = '⬇';
        btn.title = 'Descargar';
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
            e.preventDefault(); e.stopPropagation();
            triggerDownload(url);
        });
        return btn;
    }

    function addBtn(wrapper, url) {
        if (getComputedStyle(wrapper).position === 'static')
            wrapper.style.position = 'relative';
        wrapper.appendChild(makeBtn(url));
    }

    // ── Type handlers ────────────────────────────────────────────────────────

    // 1. IMAGES  →  a[data-role="img"]  (stable literal prop in Discord source)
    function injectImage(anchor) {
        if (anchor.dataset.dlDone) return;
        anchor.dataset.dlDone = '1';
        var url = anchor.href;
        if (!isCdn(url)) return;
        var wrapper = anchor.closest('div');
        if (!wrapper) return;
        addBtn(wrapper, url);
    }

    // 2. VIDEOS & GIFs  →  <video> inside [class*="embedVideo"]
    //    The <video>.src is the CDN URL (direct for uploads, media proxy for GIFs).
    function injectVideo(video) {
        if (video.dataset.dlDone) return;
        video.dataset.dlDone = '1';
        var url = video.src || (video.querySelector('source') || {}).src;
        if (!url || !isCdn(url)) return;
        var wrapper = video.closest('[class*="embedVideo"]') || video.parentElement;
        if (!wrapper) return;
        addBtn(wrapper, url);
    }

    // 3. AUDIO / GENERIC FILES  →  a[class*="fileNameLink"]
    //    Discord renders a filename anchor for audio players and all file cards.
    //    The href is always the direct CDN URL.
    function injectFile(anchor) {
        if (anchor.dataset.dlDone) return;
        anchor.dataset.dlDone = '1';
        var url = anchor.href;
        if (!isCdn(url)) return;
        // Insert a ⬇ button right after the filename link.
        var btn = makeBtn(url);
        btn.style.position = 'relative';  // flow, not absolute, beside the text
        btn.style.top = '0';
        btn.style.right = '0';
        btn.style.display = 'inline-flex';
        btn.style.verticalAlign = 'middle';
        btn.style.marginLeft = '8px';
        btn.style.flexShrink = '0';
        anchor.parentNode.insertBefore(btn, anchor.nextSibling);
    }

    // ── Scanner: run all handlers on a subtree ───────────────────────────────
    function scan(root) {
        if (root.nodeType !== 1) return;
        // Images
        var sel = root.matches ? root : null;
        if (sel && root.matches('a[data-role="img"]')) injectImage(root);
        root.querySelectorAll('a[data-role="img"]').forEach(injectImage);
        // Videos
        if (sel && root.matches('[class*="embedVideo"] video, video[src*="discordapp"]')) injectVideo(root);
        root.querySelectorAll('[class*="embedVideo"] video').forEach(injectVideo);
        // Audio / files
        if (sel && root.matches('a[class*="fileNameLink"]')) injectFile(root);
        root.querySelectorAll('a[class*="fileNameLink"]').forEach(injectFile);
    }

    // Initial scan of whatever is already rendered.
    scan(document.body);

    // Watch for new messages and lazy-loaded content (Discord is a SPA).
    new MutationObserver(function(muts) {
        muts.forEach(function(m) { m.addedNodes.forEach(scan); });
    }).observe(document.body, {childList: true, subtree: true});
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
