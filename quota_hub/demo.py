"""Synthetic RPC data; never accesses accounts or credentials."""

from datetime import date
from typing import Any


class DemoRpc:
    def request(self, method: str) -> dict[str, Any]:
        if method == "account/rateLimits/read":
            return {"rateLimitsByLimitId": {"codex": {
                "primary": {"usedPercent": 25, "windowDurationMins": 300},
                "secondary": {"usedPercent": 40, "windowDurationMins": 10080},
                "credits": {"balance": "12.5", "hasCredits": True},
            }}}
        if method == "account/usage/read":
            return {"dailyUsageBuckets": [{"startDate": date.today().isoformat(), "tokens": 12345}]}
        raise ValueError("Unsupported demo method")

    def close(self) -> None:
        pass
