#!/usr/bin/env python3
# nessus-web: Nessus Professional web UI in a native aarch64 QtWebEngine
# (Chromium) window.  No browser, no typing URLs -- just tap the icon.
#
# Nessus uses a self-signed TLS certificate on https://127.0.0.1:8834.
# QtWebEngine rejects it by default; we accept it via a custom
# QWebEngineCertificateError handler (equivalent to clicking "Advanced →
# Proceed" in Chrome, scoped to 127.0.0.1 only).
#
# The session (cookies / localStorage) lives at
# ~/.local/share/nessus-web/ and persists across restarts so you do not
# have to log in every time.
#
# If Nessus is not yet up (docker container stopped), the window shows a
# friendly "waiting" page and retries every 3 seconds until it responds.
import sys, os, ssl, urllib.request, threading

os.environ["QT_IM_MODULE"] = "none"

from PyQt6.QtCore import QUrl, QTimer, Qt, pyqtSignal, QObject, QMetaObject, Q_ARG
from PyQt6.QtWidgets import QApplication, QMainWindow
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import (
    QWebEngineProfile, QWebEnginePage,
    QWebEngineCertificateError, QWebEngineDownloadRequest,
)

NESSUS_URL = "https://127.0.0.1:8834"
DATA = os.path.expanduser("~/.local/share/nessus-web")
os.makedirs(DATA, exist_ok=True)

DOWNLOADS = os.path.join(os.path.expanduser("~/Downloads"), "Nessus")
os.makedirs(DOWNLOADS, exist_ok=True)

# Waiting page shown while Nessus is still starting up.
WAIT_HTML = """\
<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
  body { background:#1a1a2e; color:#e0e0e0; font-family:sans-serif;
         display:flex; flex-direction:column; align-items:center;
         justify-content:center; height:100vh; margin:0; }
  .logo { font-size:48px; margin-bottom:16px; }
  h1 { font-size:22px; margin:0 0 8px; }
  p  { font-size:15px; color:#aaa; margin:0; }
  .dot { animation: blink 1.2s infinite; }
  .dot:nth-child(2) { animation-delay:.2s; }
  .dot:nth-child(3) { animation-delay:.4s; }
  @keyframes blink { 0%,80%,100%{opacity:0} 40%{opacity:1} }
</style></head>
<body>
  <div class="logo">N</div>
  <h1>Nessus Professional</h1>
  <p>Waiting for the scanner to start
    <span class="dot">.</span><span class="dot">.</span><span class="dot">.</span>
  </p>
</body></html>"""


def on_download(item: QWebEngineDownloadRequest) -> None:
    suggested = os.path.basename(item.suggestedFileName())
    dest = os.path.join(DOWNLOADS, suggested or "nessus-download")
    base, ext = os.path.splitext(dest)
    n = 1
    while os.path.exists(dest):
        dest = f"{base}_{n}{ext}"
        n += 1
    item.setDownloadDirectory(DOWNLOADS)
    item.setDownloadFileName(os.path.basename(dest))
    item.accept()


class NessusPage(QWebEnginePage):
    """Accept the self-signed cert from 127.0.0.1 automatically."""

    def certificateError(self, error: QWebEngineCertificateError) -> bool:
        host = error.url().host()
        if host in ("127.0.0.1", "localhost"):
            error.acceptCertificate()
            return True
        return False

    def newWindowRequested(self, request) -> None:
        # Open any link that would spawn a new tab inside the same view.
        self.load(request.requestedUrl())


app = QApplication(sys.argv)
app.setApplicationName("Nessus")
app.setDesktopFileName("nessus-web")

profile = QWebEngineProfile("nessus", app)
profile.setPersistentStoragePath(DATA)
profile.setCachePath(os.path.join(DATA, "cache"))
profile.setPersistentCookiesPolicy(
    QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies)
profile.setHttpUserAgent(
    "Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
profile.downloadRequested.connect(on_download)

win = QMainWindow()
win.setWindowTitle("Nessus Professional")

view = QWebEngineView()
page = NessusPage(profile, view)
view.setPage(page)

# SSL context that ignores the self-signed cert for the probe request.
_ssl_ctx = ssl.create_default_context()
_ssl_ctx.check_hostname = False
_ssl_ctx.verify_mode = ssl.CERT_NONE


class _Notifier(QObject):
    """Bridge between the background probe thread and the Qt main thread."""
    ready = pyqtSignal()


_notifier = _Notifier()


def _probe_loop() -> None:
    """Background thread: keep probing until Nessus answers, then signal."""
    while True:
        try:
            urllib.request.urlopen(
                NESSUS_URL + "/server/status", context=_ssl_ctx, timeout=3)
            _notifier.ready.emit()   # safe: pyqtSignal is thread-safe
            return
        except Exception:
            pass
        threading.Event().wait(3)


def _show_wait() -> None:
    page.setContent(WAIT_HTML.encode(), "text/html;charset=utf-8", QUrl(NESSUS_URL))


def _on_ready() -> None:
    view.load(QUrl(NESSUS_URL))


_notifier.ready.connect(_on_ready)
_show_wait()
threading.Thread(target=_probe_loop, daemon=True).start()

win.setCentralWidget(view)
win.showMaximized()

sys.exit(app.exec())
