import hmac
from http.server import BaseHTTPRequestHandler, HTTPServer
import ipaddress
import os
from pathlib import Path
from threading import Thread
import time

from bridge import CodexAppServer, RpcError, encode_frame, snapshot

HOST = os.environ["QUOTA_BIND"]
PORT = int(os.environ.get("QUOTA_PORT", "8765"))
LAN = ipaddress.ip_network(os.environ["QUOTA_LAN"])
KEY = Path(__file__).with_name("lan.key").read_text().strip()
ERROR = encode_frame({"v": 1, "ok": False})
current = (ERROR, 0)


def refresh():
    global current
    server = None
    while True:
        start = time.monotonic()
        try:
            if server is None:
                server = CodexAppServer(str(Path.home() / ".local/bin/codex"))
            current = (encode_frame(snapshot(server)), time.monotonic())
        except (OSError, RpcError) as error:
            current = (ERROR, 0)
            print(f"Codex: {error}", flush=True)
            if server is not None:
                server.close()
                server = None
        time.sleep(max(0, 30 - (time.monotonic() - start)))


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/quota":
            self.send_error(404)
            return
        supplied = self.headers.get("Authorization", "").encode()
        if (ipaddress.ip_address(self.client_address[0]) not in LAN or
                not hmac.compare_digest(supplied, ("Bearer " + KEY).encode())):
            self.send_error(403)
            return
        payload, updated = current
        if time.monotonic() - updated > 90:
            payload = ERROR
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def setup(self):
        self.request.settimeout(3)
        super().setup()

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    if len(KEY) < 32:
        raise SystemExit("Invalid LAN key")
    with HTTPServer((HOST, PORT), Handler) as server:
        Thread(target=refresh, daemon=True).start()
        server.serve_forever()
