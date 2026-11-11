"""Send AI Quota Hub data through USB, or inspect V1/V2 without a device."""

import argparse
from datetime import date
import os
from pathlib import Path
import shutil
import time
from typing import Any

from quota_hub.codex import CodexAdapter, RpcClient, credit_metric, quota_metric, token_metric
from quota_hub.codex_rpc import CodexAppServer, RpcError
from quota_hub.protocol import display_page, encode_frame, v1_snapshot
from quota_hub.runtime import build_registry, collect, error_frame


# Preserve the original helpers for callers importing bridge.py.
def quota_page(window: Any, expected_minutes: int) -> dict[str, Any]:
    return display_page(quota_metric(window, "5h" if expected_minutes == 300 else "weekly", int(time.time())))


def credit_page(credits: Any) -> dict[str, Any]:
    return display_page(credit_metric(credits, int(time.time())))


def tokens_page(usage: Any, day: date) -> dict[str, Any]:
    return display_page(token_metric(usage, day, int(time.time())))


def snapshot(server: RpcClient) -> dict[str, Any]:
    return v1_snapshot((CodexAdapter(lambda: server).fetch(),))


def bridge(executable: str, port: str, demo: bool = False) -> None:
    import serial

    interval = 30
    print(f"AI Quota Hub USB bridge on {port}, refreshing every {interval} s. Press Ctrl+C to stop.", flush=True)
    registry = build_registry(executable, demo)
    try:
        while True:
            try:
                with serial.Serial(port, 115200, write_timeout=2) as device:
                    time.sleep(1)
                    while True:
                        start = time.monotonic()
                        try:
                            data = collect(registry).v1
                        except ValueError:
                            data = error_frame(1)
                        device.write(data)
                        time.sleep(max(0, interval - (time.monotonic() - start)))
            except serial.SerialException:
                print(f"{port} unavailable. Retrying in 3 s.", flush=True)
            finally:
                registry.close()
            time.sleep(3)
    finally:
        registry.close()


def find_codex(override: str | None = None) -> str | None:
    if override:
        return shutil.which(override)
    executable = shutil.which("codex")
    if executable:
        return executable
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        return None
    bin_dir = Path(local_app_data) / "OpenAI" / "Codex" / "bin"
    candidates = (path for path in bin_dir.glob("*/codex.exe") if path.is_file())
    newest = max(candidates, key=lambda path: path.stat().st_mtime, default=None)
    return str(newest) if newest else None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--port", help="ESP32 USB port, for example COM11.")
    mode.add_argument("--check", action="store_true", help="Inspect data without opening USB.")
    parser.add_argument("--codex", metavar="EXE", help="Optional path to the Codex executable.")
    parser.add_argument("--protocol", type=int, choices=(1, 2), default=1, help="Inspection protocol (default: 1).")
    parser.add_argument("--demo", action="store_true", help="Use synthetic data without an account.")
    args = parser.parse_args()
    if args.port and args.protocol != 1:
        parser.error("The current ESP32 firmware accepts V1 only. Use --check for V2.")
    executable = "" if args.demo else find_codex(args.codex)
    if executable is None:
        parser.error("Codex was not found. Specify the executable with --codex.")
    try:
        if args.check:
            registry = build_registry(executable, args.demo)
            try:
                publication = collect(registry)
                frames = (publication.v1,) if args.protocol == 1 else publication.v2
                for frame in frames:
                    print(frame.decode("ascii"), end="")
                # Keep --check useful in scripts when collection failed.
                if publication.v1 == error_frame(1) and args.protocol == 1:
                    parser.exit(1)
            finally:
                registry.close()
        else:
            bridge(executable, args.port, args.demo)
    except KeyboardInterrupt:
        return
    except (OSError, RpcError, ImportError, ValueError) as error:
        parser.exit(1, f"AI Quota Hub failed ({type(error).__name__}).\n")


if __name__ == "__main__":
    main()
