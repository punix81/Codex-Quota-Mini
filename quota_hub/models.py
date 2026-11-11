"""Normalized data. Absence is explicit; no provider-specific payloads escape here."""

from dataclasses import dataclass
from datetime import date
from enum import Enum
from math import isfinite
import re


class Category(str, Enum):
    SUBSCRIPTION = "subscription"
    API = "api"
    FINANCIAL = "financial"
    RATE = "rate"
    LOCAL = "local"


class Kind(str, Enum):
    REMAINING = "remaining"
    CONSUMPTION = "consumption"
    CREDITS = "credits"
    REQUESTS = "requests"
    TOKENS = "tokens"


class State(str, Enum):
    OK = "ok"
    UNAVAILABLE = "unavailable"
    ERROR = "error"
    STALE = "stale"


class ErrorCode(str, Enum):
    AUTH = "auth"
    TIMEOUT = "timeout"
    TRANSPORT = "transport"
    UPSTREAM = "upstream"
    INVALID_DATA = "invalid_data"
    INTERNAL = "internal"


def identifier(value: str) -> None:
    if not re.fullmatch(r"[a-zA-Z0-9_.-]{1,32}", value):
        raise ValueError("IDs must be 1-32 ASCII letters, digits, dots, dashes or underscores")


def finite_number(value: object) -> bool:
    try:
        return type(value) in (int, float) and isfinite(value)
    except OverflowError:
        return False


@dataclass(frozen=True)
class Capability:
    category: Category
    kind: Kind


@dataclass(frozen=True)
class Metric:
    id: str
    category: Category
    kind: Kind
    unit: str
    state: State
    updated_at: int
    value: int | float | None = None
    model_id: str | None = None
    reset_at: int | None = None
    window_minutes: int | None = None
    period: str | None = None
    unlimited: bool = False
    error: ErrorCode | None = None

    def __post_init__(self) -> None:
        identifier(self.id)
        if self.model_id is not None:
            identifier(self.model_id)
        identifier(self.unit)
        if self.value is not None:
            if not finite_number(self.value) or self.value < 0:
                raise ValueError("Metric values must be finite non-negative numbers")
            if self.unit == "percent" and self.value > 100:
                raise ValueError("Percent outside 0..100")
        if self.state in (State.UNAVAILABLE, State.ERROR) and self.value is not None:
            raise ValueError("Unavailable metrics cannot carry numeric values")
        if self.unlimited and self.value is not None:
            raise ValueError("Unlimited is not a numeric balance")
        if self.unlimited and self.state not in (State.OK, State.STALE):
            raise ValueError("Unlimited requires known data")
        for value in (self.updated_at, self.reset_at, self.window_minutes):
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError("Timestamps and windows must be non-negative integers")
        if self.period is not None:
            if len(self.period) != 10 or date.fromisoformat(self.period).isoformat() != self.period:
                raise ValueError("Period must be an ISO calendar date")


@dataclass(frozen=True)
class ProviderSnapshot:
    provider_id: str
    account_id: str
    updated_at: int
    state: State
    metrics: tuple[Metric, ...] = ()
    error: ErrorCode | None = None

    def __post_init__(self) -> None:
        identifier(self.provider_id)
        identifier(self.account_id)
        keys = [(m.id, m.model_id) for m in self.metrics]
        if len(set(keys)) != len(keys):
            raise ValueError("Duplicate metric/model identity")
