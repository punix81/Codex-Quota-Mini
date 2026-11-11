"""Bounded JSON lines: legacy four-page V1 and paginated V2 snapshots."""

from datetime import date, datetime
import json
from typing import Any
from uuid import uuid4

from .models import Kind, Metric, ProviderSnapshot, State
from .registry import ProviderRegistry

MAX_FRAME_BYTES = 512  # Includes newline; safe for both serial and HTTP.
MAX_FRAMES = 256


def encode_frame(data: dict[str, Any]) -> bytes:
    frame = (json.dumps(data, ensure_ascii=True, allow_nan=False, separators=(",", ":")) + "\n").encode("ascii")
    if len(frame) > MAX_FRAME_BYTES:
        raise ValueError("Frame exceeds the 512-byte transport budget")
    return frame


def display_page(metric: Metric) -> dict[str, Any]:
    if metric.state in (State.UNAVAILABLE, State.ERROR):
        return {"value": "--", "detail": "Data unavailable"}
    detail = ""
    if metric.kind == Kind.REMAINING:
        detail = "Remaining quota"
        expected = {"5h": 300, "weekly": 10080}.get(metric.id)
        if metric.window_minutes not in (None, expected):
            detail = f"Window: {metric.window_minutes} min"
        if metric.reset_at is not None:
            try:
                reset = datetime.fromtimestamp(metric.reset_at)
                fmt = "%H:%M" if reset.date() == date.today() else "%d/%m %H:%M"
                detail = f"Reset {reset:{fmt}}"
                if metric.window_minutes not in (None, expected):
                    detail = f"{metric.window_minutes}m  {reset:{fmt}}"
            except (ValueError, OSError, OverflowError):
                pass
    elif metric.kind == Kind.CREDITS:
        detail = "Available credits"
    elif metric.kind == Kind.TOKENS:
        detail = f"Total for {date.fromisoformat(metric.period):%d/%m}" if metric.period else "Tokens used"
    else:
        detail = metric.unit
    value = "--"
    if metric.unlimited:
        value = "Unlimited"
    elif metric.value is not None:
        if metric.unit == "percent":
            value = f"{metric.value:g}%"
        elif metric.kind == Kind.TOKENS:
            value = f"{metric.value:,}".replace(",", " ")
        else:
            value = str(metric.value)
        if len(value) > 23:
            value = f"{metric.value:.6g}"
    page: dict[str, Any] = {"value": value[:23], "detail": detail[:23]}
    if metric.unit == "percent" and metric.value is not None:
        page["percent"] = round(metric.value)
    return page


def v1_snapshot(snapshots: tuple[ProviderSnapshot, ...], account_id: str = "default") -> dict[str, Any]:
    source = next((s for s in snapshots if s.provider_id == "codex" and s.account_id == account_id), None)
    if source is None or source.state != State.OK:
        return {"v": 1, "ok": False}
    metrics = {m.id: m for m in source.metrics if m.model_id is None}
    if not any(metrics.get(k) and metrics[k].state == State.OK for k in ("5h", "weekly")):
        return {"v": 1, "ok": False}
    pages = [display_page(metrics[k]) if k in metrics else
             {"value": "--", "detail": "Data unavailable"}
             for k in ("5h", "weekly", "credits", "tokens")]
    return {"v": 1, "ok": True, "pages": pages}


def v2_frames(snapshots: tuple[ProviderSnapshot, ...], registry: ProviderRegistry,
              timestamp: int, sequence: str | None = None) -> tuple[bytes, ...]:
    """One manifest or metric per frame; never silently drop a metric."""
    records: list[dict[str, Any]] = []
    for snapshot in snapshots:
        identity = {"p": snapshot.provider_id, "a": snapshot.account_id}
        adapter = registry.adapters[(snapshot.provider_id, snapshot.account_id)]
        manifest: dict[str, Any] = {
            **identity, "s": snapshot.state.value, "at": snapshot.updated_at,
            "caps": [[c.category.value, c.kind.value] for c in adapter.capabilities],
        }
        if snapshot.error is not None:
            manifest["err"] = snapshot.error.value
        records.append({"provider": manifest})
        for metric in snapshot.metrics:
            page: dict[str, Any] = {
                **identity, "id": metric.id, "cat": metric.category.value,
                "kind": metric.kind.value, "unit": metric.unit,
                "s": metric.state.value, "at": metric.updated_at,
                "fmt": "gauge" if metric.unit == "percent" else "number",
            }
            for key, value in (("val", metric.value), ("m", metric.model_id),
                               ("reset", metric.reset_at), ("win", metric.window_minutes),
                               ("period", metric.period)):
                if value is not None:
                    page[key] = value
            if metric.unlimited:
                page["unlimited"] = True
            if metric.error:
                page["err"] = metric.error.value
            records.append({"pages": [page]})
    if not records:
        records.append({"providers": [], "pages": []})
    if len(records) > MAX_FRAMES:
        raise ValueError("Snapshot exceeds 256 frames")
    seq = sequence or uuid4().hex[:12]
    return tuple(encode_frame({"v": 2, "seq": seq, "at": timestamp, "i": index,
                               "n": len(records), **record})
                 for index, record in enumerate(records))
