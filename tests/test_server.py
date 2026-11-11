from dataclasses import replace
from http.client import HTTPConnection
import json
from threading import Thread
import time
import unittest

from quota_hub.runtime import build_registry, collect
from quota_server import QuotaServer


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.key = "synthetic-test-key-" * 3
        self.server = QuotaServer(("127.0.0.1", 0), "127.0.0.0/8", self.key)
        registry = build_registry("unused", demo=True)
        self.server.current = collect(registry)
        registry.close()
        self.worker = Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01})
        self.worker.start()

    def tearDown(self):
        self.server.shutdown()
        self.worker.join(timeout=2)
        self.server.server_close()

    def get(self, path, key=None):
        connection = HTTPConnection(*self.server.server_address, timeout=2)
        try:
            headers = {"Authorization": "Bearer " + (self.key if key is None else key)}
            connection.request("GET", path, headers=headers)
            response = connection.getresponse()
            body = response.read()
            return response.status, body, dict(response.getheaders())
        finally:
            connection.close()

    def test_v1_and_bounded_v2_pagination(self):
        status, body, headers = self.get("/quota")
        self.assertEqual(status, 200)
        self.assertEqual(len(json.loads(body)["pages"]), 4)
        self.assertEqual(int(headers["Content-Length"]), len(body))
        status, body, _ = self.get("/quota/v2")
        first = json.loads(body)
        self.assertEqual(first["n"], 5)
        for index in range(first["n"]):
            status, body, _ = self.get(f"/quota/v2?i={index}&seq={first['seq']}")
            self.assertEqual(status, 200)
            self.assertLessEqual(len(body), 512)
            self.assertEqual(json.loads(body)["i"], index)

    def test_authentication_for_both_versions(self):
        for path in ("/quota", "/quota/v2"):
            self.assertEqual(self.get(path, "wrong")[0], 403)

    def test_lan_filter(self):
        import ipaddress
        self.server.lan = ipaddress.ip_network("192.0.2.0/24")
        self.assertEqual(self.get("/quota")[0], 403)

    def test_stale_cache(self):
        self.server.current = replace(self.server.current, updated=time.monotonic() - 91)
        self.assertFalse(json.loads(self.get("/quota")[1])["ok"])
        self.assertEqual(json.loads(self.get("/quota/v2")[1])["s"], "stale")

    def test_invalid_indices_and_snapshot_change(self):
        for query in ("i=-1", "i=999", "i=x", "i=1", "i=0&i=1", "unknown=1"):
            self.assertEqual(self.get("/quota/v2?" + query)[0], 400)
        first = json.loads(self.get("/quota/v2")[1])
        self.server.current = collect(build_registry("unused", demo=True))
        self.assertEqual(self.get(f"/quota/v2?i=1&seq={first['seq']}")[0], 409)
        self.assertEqual(self.get("/missing")[0], 404)


if __name__ == "__main__":
    unittest.main()
