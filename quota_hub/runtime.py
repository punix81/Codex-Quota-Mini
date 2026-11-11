"""Application composition and atomic, encoded snapshot publication."""

from dataclasses import dataclass
import time
from typing import Any

from .codex import CodexAdapter
from .codex_rpc import CodexAppServer
from .demo import DemoRpc
from .protocol import encode_frame, v1_snapshot, v2_frames
from .registry import ProviderRegistry


def build_registry(executable: str, demo: bool = False) -> ProviderRegistry:
    registry = ProviderRegistry()
    registry.register(CodexAdapter(DemoRpc if demo else lambda: CodexAppServer(executable)))
    return registry


def error_frame(version: int, state: str = "error") -> bytes:
    data: dict[str, Any] = {"v": version, "ok": False}
    if version == 2:
        data.update(s=state, at=int(time.time()), pages=[])
    return encode_frame(data)


@dataclass(frozen=True)
class Publication:
    v1: bytes
    v2: tuple[bytes, ...]
    updated: float


def collect(registry: ProviderRegistry) -> Publication:
    snapshots = registry.collect()
    legacy = encode_frame(v1_snapshot(snapshots))
    try:
        frames = v2_frames(snapshots, registry, int(time.time()))
    except ValueError:
        # A future adapter exceeding V2 limits must not break the existing display.
        frames = (encode_frame({"v": 2, "ok": False, "s": "error",
                                "err": "invalid_data", "at": int(time.time()), "pages": []}),)
    return Publication(legacy, frames, time.monotonic())
