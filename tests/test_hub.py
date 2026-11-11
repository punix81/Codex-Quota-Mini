from dataclasses import replace
from datetime import date
import io
import json
import sys
import types
from queue import Queue
import unittest
from unittest.mock import Mock, patch

import bridge
from quota_hub.codex import CodexAdapter, credit_metric, quota_metric, token_metric
from quota_hub.codex_rpc import CodexAppServer, RpcError
from quota_hub.demo import DemoRpc
from quota_hub.models import Capability, Category, ErrorCode, Kind, Metric, ProviderSnapshot, State
from quota_hub.protocol import display_page, encode_frame, v1_snapshot, v2_frames
from quota_hub.registry import ProviderAdapter, ProviderRegistry
from quota_hub.runtime import build_registry, collect

NOW = 1794391200
DAY = date(2026, 11, 11)


class FakeRpc(DemoRpc):
    def __init__(self, limits=None, usage=None, error=None):
        self.limits = limits
        self.usage = usage
        self.error = error
        self.closed = False
        self.calls = []

    def request(self, method):
        self.calls.append(method)
        if self.error and method == self.error[0]:
            raise self.error[1]
        if method == "account/rateLimits/read" and self.limits is not None:
            return self.limits
        if method == "account/usage/read" and self.usage is not None:
            return self.usage
        return super().request(method)

    def close(self):
        self.closed = True


def adapter(rpc=None, account="default"):
    return CodexAdapter(lambda: rpc or DemoRpc(), account, lambda: NOW, lambda: DAY)


class MetricTests(unittest.TestCase):
    def test_invalid_percent_never_becomes_zero(self):
        for value in (None, True, "5", -1, 101, float("nan"), float("inf"), 10 ** 400):
            with self.subTest(value=value):
                metric = quota_metric({"usedPercent": value}, "5h", NOW)
                self.assertEqual(metric.state, State.UNAVAILABLE)
                self.assertIsNone(metric.value)
                self.assertNotIn("percent", display_page(metric))

    def test_zero_quota_is_available(self):
        metric = quota_metric({"usedPercent": 100, "resetsAt": NOW + 60,
                               "windowDurationMins": 300}, "5h", NOW)
        self.assertEqual(metric.value, 0)
        self.assertEqual(metric.reset_at, NOW + 60)
        self.assertEqual(display_page(metric)["percent"], 0)

    def test_unexpected_window_preserved(self):
        metric = quota_metric({"usedPercent": 20, "windowDurationMins": 15}, "5h", NOW)
        self.assertEqual(metric.window_minutes, 15)
        self.assertIn("15", display_page(metric)["detail"])

    def test_credit_states(self):
        self.assertIsNone(credit_metric({}, NOW).value)
        self.assertEqual(credit_metric({"hasCredits": False}, NOW).value, 0)
        self.assertEqual(credit_metric({"balance": "12.50"}, NOW).value, 12.5)
        unlimited = credit_metric({"unlimited": True, "balance": "12"}, NOW)
        self.assertTrue(unlimited.unlimited)
        self.assertIsNone(unlimited.value)
        for value in (True, "NaN", "Infinity", "-3", "bad", [], {}):
            self.assertIsNone(credit_metric({"balance": value}, NOW).value)

    def test_latest_valid_daily_tokens(self):
        metric = token_metric({"dailyUsageBuckets": [
            {"startDate": "2026-11-09", "tokens": 10},
            {"startDate": "2026-11-12", "tokens": 999},
            {"startDate": "2026-11-10", "tokens": 0},
            {"startDate": "2026-11-11", "tokens": True},
            {"startDate": "bad", "tokens": 30}, None,
        ]}, DAY, NOW)
        self.assertEqual(metric.value, 0)
        self.assertEqual(metric.period, "2026-11-10")
        self.assertEqual(metric.category, Category.SUBSCRIPTION)

    def test_bad_collections_are_unavailable(self):
        for value in (None, [], "bad", {"dailyUsageBuckets": "bad"}):
            self.assertEqual(token_metric(value, DAY, NOW).state, State.UNAVAILABLE)
        self.assertEqual(quota_metric([], "5h", NOW).state, State.UNAVAILABLE)

    def test_model_rejects_fabricated_values_and_bad_ids(self):
        good = quota_metric({"usedPercent": 25}, "5h", NOW)
        for changes in ({"value": float("nan")}, {"value": True}, {"value": -1},
                        {"state": State.UNAVAILABLE}, {"id": "a" * 33},
                        {"model_id": "model/unsafe"}, {"unlimited": True}):
            with self.assertRaises(ValueError):
                replace(good, **changes)


class CodexTests(unittest.TestCase):
    def test_official_methods_and_four_metrics(self):
        rpc = FakeRpc(usage={"dailyUsageBuckets": [{"startDate": DAY.isoformat(), "tokens": 12345}]})
        result = adapter(rpc).fetch()
        self.assertEqual(rpc.calls, ["account/rateLimits/read", "account/usage/read"])
        self.assertEqual([m.id for m in result.metrics], ["5h", "weekly", "credits", "tokens"])
        self.assertEqual([m.value for m in result.metrics], [75, 60, 12.5, 12345])
        self.assertTrue(all(m.model_id is None for m in result.metrics))

    def test_legacy_and_codex_bucket_selection(self):
        primary = {"primary": {"usedPercent": 10}}
        for raw in ({"rateLimits": primary}, {"rateLimits": {**primary, "limitId": "codex"}},
                    {"rateLimits": {"limitId": "other"}, "rateLimitsByLimitId": {"codex": primary}}):
            self.assertEqual(adapter(FakeRpc(limits=raw)).fetch().metrics[0].value, 90)
        wrong = adapter(FakeRpc(limits={"rateLimits": {**primary, "limitId": "other"}})).fetch()
        self.assertIsNone(wrong.metrics[0].value)

    def test_usage_error_does_not_lose_quotas(self):
        rpc = FakeRpc(error=("account/usage/read", RpcError("sensitive", ErrorCode.AUTH)))
        result = adapter(rpc).fetch()
        self.assertEqual(result.state, State.OK)
        self.assertEqual(result.metrics[-1].error, ErrorCode.AUTH)
        self.assertTrue(v1_snapshot((result,))["ok"])
        self.assertTrue(rpc.closed)

    def test_transport_error_and_recovery(self):
        rpc = FakeRpc(error=("account/rateLimits/read", RpcError("secret", ErrorCode.TIMEOUT)))
        factory = Mock(side_effect=[rpc, DemoRpc()])
        source = CodexAdapter(factory)
        result = source.fetch()
        self.assertEqual(result.error, ErrorCode.TIMEOUT)
        self.assertEqual(v1_snapshot((result,)), {"v": 1, "ok": False})
        self.assertTrue(rpc.closed)
        self.assertEqual(source.fetch().state, State.OK)
        source.close()

    def test_process_start_failure(self):
        source = CodexAdapter(Mock(side_effect=OSError("secret")))
        self.assertEqual(source.fetch().error, ErrorCode.TRANSPORT)


class StaticAdapter(ProviderAdapter):
    capabilities = (Capability(Category.LOCAL, Kind.TOKENS),)

    def __init__(self, provider="test", account="default", broken=False):
        self.provider_id, self.account_id, self.broken = provider, account, broken

    def fetch(self):
        if self.broken:
            raise RuntimeError("secret credential")
        metrics = tuple(Metric("tokens", Category.LOCAL, Kind.TOKENS, "tokens", State.OK,
                               NOW, value=10, model_id=model) for model in ("model-a", "model-b"))
        return ProviderSnapshot(self.provider_id, self.account_id, NOW, State.OK, metrics)


class ProtocolTests(unittest.TestCase):
    def test_v1_is_four_pages_and_fits_firmware(self):
        result = adapter().fetch()
        payload = v1_snapshot((result,))
        self.assertEqual(len(payload["pages"]), 4)
        self.assertEqual(payload["pages"][0]["value"], "75%")
        self.assertLessEqual(len(encode_frame(payload)), 512)
        self.assertEqual(bridge.snapshot(DemoRpc())["v"], 1)

    def test_multiple_providers_accounts_models_and_failure_isolation(self):
        registry = ProviderRegistry(lambda: NOW)
        for source in (adapter(), StaticAdapter(), StaticAdapter(account="work"),
                       StaticAdapter(provider="broken", broken=True)):
            registry.register(source)
        results = registry.collect()
        self.assertEqual(results[-1].error, ErrorCode.INTERNAL)
        frames = v2_frames(results, registry, NOW, "demo")
        self.assertEqual(len(frames), 12)
        pages = [page for frame in frames for page in json.loads(frame).get("pages", [])]
        self.assertEqual(len(pages), 8)
        self.assertEqual(len({(p["p"], p["a"], p.get("m"), p["id"]) for p in pages}), 8)
        for index, frame in enumerate(frames):
            data = json.loads(frame)
            self.assertEqual((data["i"], data["n"], data["seq"]), (index, 12, "demo"))
            self.assertLessEqual(len(frame), 512)
            self.assertNotIn(b"secret", frame)
        self.assertTrue(v1_snapshot(results)["ok"])

    def test_duplicate_registration_rejected(self):
        registry = ProviderRegistry()
        registry.register(adapter())
        with self.assertRaises(ValueError):
            registry.register(adapter())

    def test_missing_numeric_field_and_capabilities(self):
        registry = ProviderRegistry()
        registry.register(adapter(FakeRpc(limits={})))
        frames = [json.loads(f) for f in v2_frames(registry.collect(), registry, NOW)]
        self.assertIn(["financial", "credits"], frames[0]["provider"]["caps"])
        self.assertEqual(frames[1]["pages"][0]["s"], "unavailable")
        self.assertNotIn("val", frames[1]["pages"][0])

    def test_oversize_and_nonfinite_json_rejected(self):
        for data in ({"x": "x" * 512}, {"x": "é" * 100}, {"x": float("nan")}):
            with self.assertRaises(ValueError):
                encode_frame(data)

    def test_runtime_demo_without_process(self):
        with patch("quota_hub.runtime.CodexAppServer", side_effect=AssertionError("must not run")):
            registry = build_registry("missing", demo=True)
            publication = collect(registry)
            self.assertTrue(json.loads(publication.v1)["ok"])
            self.assertEqual(len(publication.v2), 5)
            registry.close()

    def test_cli_demo_v2(self):
        with patch("sys.argv", ["bridge.py", "--check", "--demo", "--protocol", "2"]), \
                patch("sys.stdout", new_callable=io.StringIO) as output:
            bridge.main()
        self.assertEqual(len(output.getvalue().splitlines()), 5)
        self.assertTrue(all(json.loads(line)["v"] == 2 for line in output.getvalue().splitlines()))

    def test_cli_rejects_v2_on_legacy_device(self):
        with patch("sys.argv", ["bridge.py", "--port", "fake", "--demo", "--protocol", "2"]), \
                patch("sys.stderr", new_callable=io.StringIO), self.assertRaises(SystemExit) as error:
            bridge.main()
        self.assertEqual(error.exception.code, 2)

    def test_usb_writes_legacy_frame_and_closes(self):
        device = Mock()
        device.__enter__ = Mock(return_value=device)
        device.__exit__ = Mock(return_value=False)
        serial = types.SimpleNamespace(Serial=Mock(return_value=device), SerialException=OSError)
        with patch.dict(sys.modules, {"serial": serial}), \
                patch("bridge.time.sleep", side_effect=[None, KeyboardInterrupt]), \
                patch("sys.stdout", new_callable=io.StringIO), self.assertRaises(KeyboardInterrupt):
            bridge.bridge("unused", "fake", demo=True)
        frame = device.write.call_args.args[0]
        self.assertLessEqual(len(frame), 512)
        self.assertEqual(json.loads(frame)["v"], 1)
        self.assertEqual(len(json.loads(frame)["pages"]), 4)
        device.__exit__.assert_called_once()

    def test_large_v2_snapshot_cannot_break_v1(self):
        registry = build_registry("unused", demo=True)
        for i in range(85):
            registry.register(StaticAdapter(account=f"account-{i}"))
        publication = collect(registry)
        self.assertTrue(json.loads(publication.v1)["ok"])
        self.assertEqual(json.loads(publication.v2[0])["err"], "invalid_data")

    def test_all_categories_and_kinds_are_transmitted(self):
        registry = ProviderRegistry()
        source = StaticAdapter()
        registry.register(source)
        metrics = tuple(Metric(f"metric-{i}", category, kind, "count", State.OK, NOW, value=0)
                        for i, (category, kind) in enumerate(zip(Category, Kind)))
        result = ProviderSnapshot("test", "default", NOW, State.OK, metrics)
        frames = [json.loads(f) for f in v2_frames((result,), registry, NOW)]
        self.assertEqual({f["pages"][0]["cat"] for f in frames[1:]}, {c.value for c in Category})
        self.assertEqual({f["pages"][0]["kind"] for f in frames[1:]}, {k.value for k in Kind})


class RpcTests(unittest.TestCase):
    def client(self, responses):
        server = CodexAppServer.__new__(CodexAppServer)
        server.process = Mock()
        server.process.stdin = io.StringIO()
        server.request_id = 0
        server.responses = Queue()
        for response in responses:
            server.responses.put(response)
        return server

    def test_notifications_and_old_responses_are_ignored(self):
        server = self.client([{"method": "notice"}, {"id": 99}, {"id": 1, "result": {"ok": True}}])
        self.assertEqual(server.request("account/rateLimits/read"), {"ok": True})
        self.assertEqual(json.loads(server.process.stdin.getvalue())["id"], 1)

    def test_eof_errors_and_bad_results(self):
        cases = [(None, ErrorCode.TRANSPORT), ({"id": 1, "error": {"code": 401}}, ErrorCode.AUTH),
                 ({"id": 1, "error": "bad"}, ErrorCode.UPSTREAM),
                 ({"id": 1, "result": []}, ErrorCode.INVALID_DATA)]
        for response, code in cases:
            with self.subTest(response=response), self.assertRaises(RpcError) as error:
                self.client([response]).request("read")
            self.assertEqual(error.exception.code, code)


if __name__ == "__main__":
    unittest.main()
