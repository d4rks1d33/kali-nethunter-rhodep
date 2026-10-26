# wsauth.py -- Recon live-scan WebSocket auth token helper.
# Ported to Python 3 for the Kali/NetHunter aarch64 port (no Python 2 on Kali).
# Py2->3 changes: none needed semantically; file is already 3-clean. Kept as-is
# with explicit UTF-8 and used by reconpp.py / WebSocketsHandler.py.
import random
import string
import os

TOKENLEN = 64
TOKENFILE = '/tmp/reconpp.token'


def gen_token(n):
    return ''.join(
        random.SystemRandom().choice(string.ascii_uppercase + string.digits)
        for _ in range(n)
    )


def write_token():
    if os.path.isfile(TOKENFILE):
        return
    with open(TOKENFILE, 'w') as f:
        f.write(gen_token(TOKENLEN))


def read_token():
    with open(TOKENFILE) as f:
        return f.read()


def remove_token():
    os.unlink(TOKENFILE)


def verify_token(token):
    return token == read_token()
