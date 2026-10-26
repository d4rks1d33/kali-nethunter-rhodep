# WebSocketsHandler.py -- Recon live-scan WebSocket server handler.
# Ported to Python 3 for the Kali/NetHunter aarch64 port.
#
# Py2 -> Py3 changes:
#   - import SocketServer            -> import socketserver
#   - from mimetools import Message  -> email.message_from_string (mimetools gone)
#   - from StringIO import StringIO  -> removed (no longer needed)
#   - print "x"                      -> print("x")
#   - except Exception, e            -> except Exception as e
#   - self.request.send(chr(...))    -> bytes; WebSocket framing must be bytes
#   - sha1(key + magic).hexdigest().decode('hex') -> hashlib + base64 on digest()
#   - message/header strings encoded to bytes on the wire
import struct
import socketserver
import time
from base64 import b64encode
from hashlib import sha1
from email import message_from_string
import wsauth


# This class is taken and modified from https://gist.github.com/jkp/3136208
class WebSocketsHandler(socketserver.StreamRequestHandler):
    magic = '258EAFA5-E914-47DA-95CA-C5AB0DC85B11'

    def setup(self):
        socketserver.StreamRequestHandler.setup(self)
        self.server.websockets.append(self)
        self.handshake_done = False

    def handle(self):
        while self.server.running:
            if not self.handshake_done:
                self.handshake()
            else:
                time.sleep(5)

    def send_message(self, message):
        if isinstance(message, str):
            message = message.encode('utf-8')
        self.request.send(bytes([129]))
        length = len(message)
        if length <= 125:
            self.request.send(bytes([length]))
        elif 126 <= length <= 65535:
            self.request.send(bytes([126]))
            self.request.send(struct.pack(">H", length))
        else:
            self.request.send(bytes([127]))
            self.request.send(struct.pack(">Q", length))
        self.request.send(message)

    def handshake(self):
        data = self.request.recv(1024).strip().decode('utf-8', 'replace')
        headers = message_from_string(data.split('\r\n', 1)[1])
        try:
            uri = data.split('\r\n')[0].split(' ')[1]
            authtoken = dict(
                p.split('=')
                for p in uri.split('/')[-1].split('?')[1].split('&')
            )['authtoken']
            if not wsauth.verify_token(authtoken):
                print("[!] Invalid auth token: {}".format(authtoken))
                return
            else:
                print("[+] Valid auth token: {}".format(authtoken))
        except Exception as e:
            print("Error checking token: {}".format(e))
        if headers.get("Upgrade", None) != "websocket":
            return
        key = headers['Sec-WebSocket-Key']
        digest = b64encode(
            sha1((key + self.magic).encode('utf-8')).digest()
        ).decode('ascii')
        response = 'HTTP/1.1 101 Switching Protocols\r\n'
        response += 'Upgrade: websocket\r\n'
        response += 'Connection: Upgrade\r\n'
        response += 'Sec-WebSocket-Accept: %s\r\n\r\n' % digest
        self.handshake_done = self.request.send(response.encode('utf-8'))
