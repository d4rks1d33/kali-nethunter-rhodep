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

# JS injected after every page load. Watches for Discord's image lightbox
# (the full-screen overlay that opens when you tap an image) and injects a
# prominent download button.  Uses a MutationObserver so it catches lightboxes
# that open after the initial load (Discord is a SPA).
#
# How it works:
#   - Watches <body> for any new element that contains a full-size CDN image
#     inside a role="dialog" or a known Discord lightbox container.
#   - When found, appends a fixed-position "⬇ Save" button that navigates to
#     the raw CDN URL with the `download` attribute, which triggers
#     QWebEngineDownloadRequest -> on_download() -> ~/Downloads.
#   - The button is styled to be large enough for a finger tap (56px tall).
_INJECT_JS = r"""
(function() {
    if (window.__rhodepDlInjected) return;
    window.__rhodepDlInjected = true;

    var btn = null;

    function removeBtn() {
        if (btn && btn.parentNode) { btn.parentNode.removeChild(btn); }
        btn = null;
    }

    function addBtn(imgUrl) {
        removeBtn();
        // Strip Discord's size/format query params to get the raw file.
        var rawUrl = imgUrl.split('?')[0];
        // Guess a filename from the URL path.
        var fname = rawUrl.split('/').pop() || 'discord-image';
        if (!/\.\w{2,5}$/.test(fname)) fname += '.jpg';

        btn = document.createElement('a');
        btn.href = rawUrl;
        btn.download = fname;
        btn.textContent = '\u2b07 Save';
        btn.style.cssText = [
            'position:fixed',
            'bottom:80px',
            'right:16px',
            'z-index:99999',
            'background:#5865f2',
            'color:#fff',
            'font-size:18px',
            'font-weight:bold',
            'padding:12px 22px',
            'border-radius:28px',
            'box-shadow:0 4px 16px rgba(0,0,0,0.5)',
            'text-decoration:none',
            'user-select:none',
            '-webkit-tap-highlight-color:transparent',
        ].join(';');
        document.body.appendChild(btn);
    }

    // Find the largest <img> inside a lightbox-like container.
    function findLightboxImg(node) {
        // Discord wraps the lightbox in a div with role=dialog or a known
        // layerContainer. Look for a big CDN image inside any overlay.
        var imgs = node.querySelectorAll('img[src*="cdn.discordapp.com"], img[src*="media.discordapp.net"], img[src*="images-ext"]');
        var best = null, bestArea = 0;
        imgs.forEach(function(img) {
            var r = img.getBoundingClientRect();
            var area = r.width * r.height;
            if (area > bestArea) { bestArea = area; best = img; }
        });
        // Only show the button if the image is large (lightbox, not thumbnail).
        if (best && bestArea > 40000) return best;
        return null;
    }

    var observer = new MutationObserver(function(mutations) {
        mutations.forEach(function(m) {
            m.addedNodes.forEach(function(node) {
                if (node.nodeType !== 1) return;
                var img = findLightboxImg(node);
                if (img) { addBtn(img.src); return; }
                // Also check if a lightbox was removed (closed).
                if (btn) {
                    var still = document.querySelector(
                        'img[src="' + btn.href.split('?')[0] + '"]');
                    if (!still) removeBtn();
                }
            });
            m.removedNodes.forEach(function(node) {
                if (btn && node.nodeType === 1 && node.contains &&
                    node.querySelector && findLightboxImg(node)) {
                    removeBtn();
                }
            });
        });
    });

    observer.observe(document.body, { childList: true, subtree: true });
})();
"""

win = QMainWindow()
win.setWindowTitle("Discord")

view = QWebEngineView()
page = QWebEnginePage(profile, view)
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
