"""Authenticated LAN relay; /quota stays V1, /quota/v2 exposes bounded frames."""

import hmac
from http.server import BaseHTTPRequestHandler, HTTPServer
import ipaddress
import json
import os
from pathlib import Path
from threading import Event, Thread
import time
from urllib.parse import parse_qs, urlsplit

from quota_hub.registry import ProviderRegistry
from quota_hub.runtime import Publication, build_registry, collect, error_frame


class QuotaServer(HTTPServer):
    def __init__(self, address: tuple[str, int], lan: str, key: str) -> None:
        if len(key) < 32:
            raise ValueError("Invalid LAN key")
        self.lan = ipaddress.ip_network(lan)
        self.key = key
        self.current = Publication(error_frame(1), (error_frame(2),), 0)
        super().__init__(address, Handler)


def refresh(server: QuotaServer, registry: ProviderRegistry, stop: Event) -> None:
    try:
        while not stop.is_set():
            start = time.monotonic()
            try:
                server.current = collect(registry)
            except ValueError:
                server.current = Publication(error_frame(1), (error_frame(2),), 0)
            stop.wait(max(0, 30 - (time.monotonic() - start)))
    finally:
        registry.close()


class Handler(BaseHTTPRequestHandler):
    server: QuotaServer

    def do_GET(self) -> None:
        path = urlsplit(self.path)
        if path.path not in ("/quota", "/quota/v2"):
            self.send_error(404)
            return
        supplied = self.headers.get("Authorization", "").encode()
        if (ipaddress.ip_address(self.client_address[0]) not in self.server.lan or
                not hmac.compare_digest(supplied, ("Bearer " + self.server.key).encode())):
            self.send_error(403)
            return
        version = 1 if path.path == "/quota" else 2
        current = self.server.current
        if time.monotonic() - current.updated > 90:
            payload = error_frame(version, "stale")
        elif version == 1:
            payload = current.v1
        else:
            query = parse_qs(path.query, keep_blank_values=True)
            try:
                if set(query) - {"i", "seq"} or any(len(v) != 1 for v in query.values()):
                    raise ValueError
                index = int(query.get("i", ["0"])[0])
                if not 0 <= index < len(current.v2):
                    raise ValueError
                requested_seq = query.get("seq", [None])[0]
                if index and requested_seq is None:
                    raise ValueError
            except ValueError:
                self.send_error(400)
                return
            actual_seq = json.loads(current.v2[0]).get("seq")
            if requested_seq is not None and requested_seq != actual_seq:
                self.send_error(409, "Snapshot changed; restart at i=0")
                return
            payload = current.v2[index]
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def setup(self) -> None:
        self.request.settimeout(3)
        super().setup()

    def log_message(self, *args: object) -> None:
        pass


def main() -> None:
    # Credentials are read only at explicit startup, never on module import.
    key = Path(__file__).with_name("lan.key").read_text().strip()
    registry = build_registry(os.environ.get("QUOTA_CODEX", str(Path.home() / ".local/bin/codex")),
                              demo=os.environ.get("QUOTA_DEMO") == "1")
    stop = Event()
    with QuotaServer((os.environ["QUOTA_BIND"], int(os.environ.get("QUOTA_PORT", "8765"))),
                     os.environ["QUOTA_LAN"], key) as server:
        worker = Thread(target=refresh, args=(server, registry, stop), daemon=True)
        worker.start()
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            stop.set()
            worker.join(timeout=30)


if __name__ == "__main__":
    main()
