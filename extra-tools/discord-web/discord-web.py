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

from PyQt6.QtCore import QUrl, Qt, QStandardPaths
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

win = QMainWindow()
win.setWindowTitle("Discord")

view = QWebEngineView()
page = QWebEnginePage(profile, view)
view.setPage(page)
# Keep the default context menu so "Save image / Open in new tab / Copy link"
# work. Discord's own long-press handler (for reactions etc.) is unaffected
# because it fires before the browser context menu.
view.load(QUrl("https://discord.com/app"))

win.setCentralWidget(view)
# Maximised (not fullscreen) so the Plasma Mobile nav bar stays -- you can
# minimise/close and raise the keyboard yourself.
win.showMaximized()

sys.exit(app.exec())
