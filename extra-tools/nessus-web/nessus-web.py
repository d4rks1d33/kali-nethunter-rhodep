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

from PyQt6.QtCore import QUrl, QTimer, Qt, pyqtSignal, QObject
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

# --- retry logic: show waiting page until Nessus answers ---
_nessus_up = False
_probe_succeeded = False   # written by bg thread, read by Qt timer
# SSL context that ignores the self-signed cert for the probe request.
_ssl_ctx = ssl.create_default_context()
_ssl_ctx.check_hostname = False
_ssl_ctx.verify_mode = ssl.CERT_NONE


def _probe_nessus() -> None:
    """Background thread: probe Nessus, set flag on success."""
    global _probe_succeeded
    try:
        with urllib.request.urlopen(
            NESSUS_URL + "/server/status", context=_ssl_ctx, timeout=3
        ):
            pass
        _probe_succeeded = True
    except Exception:
        pass


def try_load() -> None:
    """Called by the Qt timer on the main thread every 3 s."""
    global _nessus_up, _probe_succeeded
    if _nessus_up:
        return
    if _probe_succeeded:
        # Probe succeeded in a previous bg thread — load now on main thread.
        _nessus_up = True
        _timer.stop()
        view.load(QUrl(NESSUS_URL))
        return
    # Launch a fresh probe in the background.
    threading.Thread(target=_probe_nessus, daemon=True).start()


def _show_wait() -> None:
    page.setContent(WAIT_HTML.encode(), "text/html;charset=utf-8", QUrl(NESSUS_URL))


# Show the waiting page immediately, then start probing.
_show_wait()
_timer = QTimer()
_timer.setInterval(3000)
_timer.timeout.connect(try_load)
_timer.start()
# Try right away in case Nessus is already up.
try_load()

# Once the real Nessus page loads, stop the timer.
def _on_load_finished(ok: bool) -> None:
    global _nessus_up
    if ok and view.url().toString().startswith(NESSUS_URL):
        _nessus_up = True
        _timer.stop()

view.loadFinished.connect(_on_load_finished)

win.setCentralWidget(view)
win.showMaximized()

sys.exit(app.exec())
