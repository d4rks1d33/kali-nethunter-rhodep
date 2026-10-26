#!/usr/bin/env python3
# reconpp.py -- Recon live-scan WebSocket server entrypoint (port :1337).
# Ported to Python 3 for the Kali/NetHunter aarch64 port.
#
# Py2 -> Py3 changes:
#   - import SocketServer            -> import socketserver
#   - SocketServer.ThreadingTCPServer-> socketserver.ThreadingTCPServer
#   - dbHandler.setDaemon(True)      -> dbHandler.daemon = True (setDaemon deprecated)
#   - added explicit python3 shebang
import WebSocketsHandler
import dbhandler
import socketserver
import fcntl
import sys
import os
import wsauth

LOCKFILE = '/tmp/reconpp.lock'

# Keep a module-level reference to the lock file handle so the advisory lock
# is held for the lifetime of the process (Py2 original dropped it immediately,
# which released the flock at function return -- fixed here).
_lock_fh = None


def acquire_lockfile():
    global _lock_fh
    _lock_fh = open(LOCKFILE, 'w')
    try:
        fcntl.flock(_lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except Exception:
        return False
    return True


def release_lockfile():
    try:
        os.unlink(LOCKFILE)
    except OSError:
        pass


def main():
    if len(sys.argv) != 3:
        print("Usage: reconpp.py <database> <scanID>")
        sys.exit(1)

    if not acquire_lockfile():
        print("Failed to acquire lock file. Is reconpp already running?")
        sys.exit(1)

    wsauth.write_token()

    websockets = []
    running = True

    database = sys.argv[1]
    scanID = sys.argv[2]

    dbHandler = dbhandler.DBHandler(args=(websockets, database, scanID))
    dbHandler.daemon = True

    socketserver.ThreadingTCPServer.allow_reuse_address = 1
    server = socketserver.ThreadingTCPServer(
        ("", 1337), WebSocketsHandler.WebSocketsHandler
    )
    server.running = running
    server.websockets = websockets

    try:
        dbHandler.start()
        server.serve_forever()
        dbHandler.join()
    except KeyboardInterrupt:
        server.running = False
        server.server_close()
    finally:
        release_lockfile()
        wsauth.remove_token()


if __name__ == '__main__':
    main()
