"""Codex subscription quotas, credits and latest daily token activity."""

from collections.abc import Callable
from datetime import date
import time
from typing import Any, Protocol

from .codex_rpc import RpcError
from .models import Capability, Category, ErrorCode, Kind, Metric, ProviderSnapshot, State, finite_number
from .registry import ProviderAdapter


class RpcClient(Protocol):
    def request(self, method: str) -> dict[str, Any]: ...
    def close(self) -> None: ...


def mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def number(value: Any) -> int | float | None:
    if finite_number(value) and value >= 0:
        return value
    return None


def quota_metric(window: Any, metric_id: str, now: int) -> Metric:
    window = mapping(window)
    used = number(window.get("usedPercent"))
    valid = used is not None and used <= 100
    reset = number(window.get("resetsAt"))
    minutes = number(window.get("windowDurationMins"))
    return Metric(metric_id, Category.SUBSCRIPTION, Kind.REMAINING, "percent",
                  State.OK if valid else State.UNAVAILABLE, now,
                  value=round(100 - used) if valid else None,
                  reset_at=int(reset) if reset is not None else None,
                  window_minutes=int(minutes) if minutes is not None else None)


def credit_metric(credits: Any, now: int) -> Metric:
    credits = mapping(credits)
    unlimited = credits.get("unlimited") is True
    balance = credits.get("balance")
    if isinstance(balance, str):
        try:
            balance = float(balance)
        except ValueError:
            balance = None
    value = number(balance)
    if value is None and credits.get("hasCredits") is False:
        value = 0
    return Metric("credits", Category.FINANCIAL, Kind.CREDITS, "credits",
                  State.OK if unlimited or value is not None else State.UNAVAILABLE,
                  now, value=None if unlimited else value, unlimited=unlimited)


def token_metric(usage: Any, day: date, now: int) -> Metric:
    latest_day = date.min
    latest_count = None
    buckets = mapping(usage).get("dailyUsageBuckets")
    for bucket in buckets if isinstance(buckets, list) else []:
        bucket = mapping(bucket)
        count = bucket.get("tokens")
        try:
            bucket_day = date.fromisoformat(bucket.get("startDate") or "")
        except (TypeError, ValueError):
            continue
        if latest_day < bucket_day <= day and type(count) is int and count >= 0:
            latest_day, latest_count = bucket_day, count
    return Metric("tokens", Category.SUBSCRIPTION, Kind.TOKENS, "tokens",
                  State.OK if latest_count is not None else State.UNAVAILABLE,
                  now, value=latest_count,
                  period=latest_day.isoformat() if latest_count is not None else None)


class CodexAdapter(ProviderAdapter):
    provider_id = "codex"
    capabilities = (
        Capability(Category.SUBSCRIPTION, Kind.REMAINING),
        Capability(Category.FINANCIAL, Kind.CREDITS),
        Capability(Category.SUBSCRIPTION, Kind.TOKENS),
    )

    def __init__(self, factory: Callable[[], RpcClient], account_id: str = "default",
                 clock: Callable[[], float] = time.time,
                 today: Callable[[], date] = date.today) -> None:
        self.account_id = account_id
        self.factory = factory
        self.clock = clock
        self.today = today
        self.server: RpcClient | None = None

    def close(self) -> None:
        if self.server is not None:
            server, self.server = self.server, None
            server.close()

    def fetch(self) -> ProviderSnapshot:
        now = int(self.clock())
        try:
            if self.server is None:
                self.server = self.factory()
            result = mapping(self.server.request("account/rateLimits/read"))
            legacy = mapping(result.get("rateLimits"))
            limit = mapping(mapping(result.get("rateLimitsByLimitId")).get("codex"))
            if not limit and legacy.get("limitId") in (None, "codex"):
                limit = legacy
            quotas = (quota_metric(limit.get("primary"), "5h", now),
                      quota_metric(limit.get("secondary"), "weekly", now))
            credits = credit_metric(limit.get("credits"), now)
            try:
                tokens = token_metric(self.server.request("account/usage/read"), self.today(), now)
            except (RpcError, OSError) as error:
                code = error.code if isinstance(error, RpcError) else ErrorCode.TRANSPORT
                tokens = Metric("tokens", Category.SUBSCRIPTION, Kind.TOKENS, "tokens",
                                State.ERROR, now, error=code)
                # Retry on the next poll, while preserving the successful quota read.
                self.close()
            metrics = (*quotas, credits, tokens)
            state = State.OK if any(m.state == State.OK for m in metrics) else State.UNAVAILABLE
            return ProviderSnapshot(self.provider_id, self.account_id, now, state, metrics)
        except (RpcError, OSError) as error:
            self.close()
            code = error.code if isinstance(error, RpcError) else ErrorCode.TRANSPORT
            return ProviderSnapshot(self.provider_id, self.account_id, now, State.ERROR, error=code)
