"""Send Codex account usage to the ESP32 through its USB serial port."""

import argparse
from datetime import date, datetime
import json
from math import isfinite
import os
from pathlib import Path
from queue import Empty, Queue
import shutil
import subprocess
from threading import Thread
import time


class RpcError(RuntimeError):
    pass


class CodexAppServer:
    def __init__(self, executable):
        self.process = subprocess.Popen(
            [executable, "app-server", "--listen", "stdio://"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
        )
        self.responses = Queue()
        self.request_id = 0
        self.reader = Thread(target=self._read, daemon=True)
        self.reader.start()
        try:
            self.request("initialize", {
                "clientInfo": {"name": "codex-quota-mini", "version": "1.0.0"},
            })
            self._send({"method": "initialized"})
        except BaseException:
            self.close()
            raise

    def _read(self):
        try:
            for line in self.process.stdout:
                try:
                    self.responses.put(json.loads(line))
                except ValueError:
                    continue
        finally:
            self.responses.put(None)

    def _send(self, message):
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()

    def request(self, method, params=None):
        self.request_id += 1
        message = {"method": method, "id": self.request_id}
        if params is not None:
            message["params"] = params
        self._send(message)
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            try:
                response = self.responses.get(timeout=max(0, deadline - time.monotonic()))
            except Empty:
                break
            if response is None:
                raise RpcError("The Codex process stopped.")
            if not isinstance(response, dict) or response.get("id") != self.request_id:
                continue
            if "error" in response:
                raise RpcError(f"{method}: RPC error {response['error'].get('code')}.")
            return response["result"]
        raise RpcError(f"{method}: request timed out.")

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
        try:
            self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
        self.reader.join(timeout=1)
        self.process.stdin.close()
        self.process.stdout.close()


def quota_page(window, expected_minutes):
    window = window or {}
    used = window.get("usedPercent")
    minutes = window.get("windowDurationMins")
    if minutes not in (None, expected_minutes):
        detail = f"Window: {minutes} min"
    else:
        detail = "Remaining quota"
    if not isinstance(used, (int, float)) or not isfinite(used):
        return {"value": "--", "detail": "Quota unavailable"}

    remaining = round(100 - max(0, min(100, used)))
    resets_at = window.get("resetsAt")
    if isinstance(resets_at, (int, float)):
        try:
            reset = datetime.fromtimestamp(resets_at)
            fmt = "%H:%M" if reset.date() == date.today() else "%d/%m %H:%M"
            detail = f"Reset {reset:{fmt}}"
            if minutes not in (None, expected_minutes):
                detail = f"{minutes}m  {reset:{fmt}}"
        except (ValueError, OSError, OverflowError):
            pass
    return {"value": f"{remaining}%", "detail": detail, "percent": remaining}


def credit_page(credits):
    credits = credits or {}
    if credits.get("unlimited") is True:
        value = "Unlimited"
    elif credits.get("balance") is not None:
        value = str(credits["balance"])[:20]
    elif credits.get("hasCredits") is False:
        value = "0"
    else:
        value = "--"
    return {"value": value, "detail": "Available credits"}


def tokens_page(usage, day):
    latest_day = date.min
    latest_count = None
    for bucket in usage.get("dailyUsageBuckets") or []:
        count = bucket.get("tokens")
        try:
            bucket_day = date.fromisoformat(bucket.get("startDate") or "")
        except (TypeError, ValueError):
            continue
        if latest_day < bucket_day <= day and isinstance(count, int) and count >= 0:
            latest_day = bucket_day
            latest_count = count
    if latest_count is None:
        return {"value": "--", "detail": "Waiting for data"}
    return {"value": f"{latest_count:,}".replace(",", " "),
            "detail": f"Total for {latest_day:%d/%m}"}


def snapshot(server):
    result = server.request("account/rateLimits/read")
    legacy = result.get("rateLimits") or {}
    limit = (result.get("rateLimitsByLimitId") or {}).get("codex")
    if not limit and legacy.get("limitId") in (None, "codex"):
        limit = legacy
    if not limit or (not limit.get("primary") and not limit.get("secondary")):
        raise RpcError("Codex quotas unavailable. Check your ChatGPT sign-in in Codex.")

    try:
        usage = server.request("account/usage/read")
    except RpcError:
        usage = {}
    return {
        "v": 1, "ok": True,
        "pages": [
            quota_page(limit.get("primary"), 300),
            quota_page(limit.get("secondary"), 10080),
            credit_page(limit.get("credits")),
            tokens_page(usage, date.today()),
        ],
    }


def encode_frame(data):
    return (json.dumps(data, ensure_ascii=True, separators=(",", ":")) + "\n").encode("ascii")


def bridge(executable, port):
    import serial

    interval = 30
    print(f"Codex USB bridge on {port}, refreshing every {interval} s. Press Ctrl+C to stop.", flush=True)
    while True:
        server = None
        try:
            with serial.Serial(port, 115200, write_timeout=2) as device:
                time.sleep(1)
                while True:
                    start = time.monotonic()
                    try:
                        if server is None:
                            server = CodexAppServer(executable)
                        data = snapshot(server)
                    except (OSError, RpcError) as error:
                        print(f"Codex: {error}", flush=True)
                        data = {"v": 1, "ok": False}
                        if server is not None:
                            server.close()
                            server = None

                    device.write(encode_frame(data))
                    time.sleep(max(0, interval - (time.monotonic() - start)))
        except serial.SerialException:
            print(f"{port} unavailable. Retrying in 3 s.", flush=True)
        finally:
            if server is not None:
                server.close()
        time.sleep(3)


def find_codex(override=None):
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--port", help="ESP32 USB port, for example COM11.")
    mode.add_argument("--check", action="store_true", help="Check Codex without opening the USB port.")
    parser.add_argument("--codex", metavar="EXE", help="Optional path to the Codex executable.")
    args = parser.parse_args()
    executable = find_codex(args.codex)
    if not executable:
        parser.error("Codex was not found. Specify the executable with --codex.")

    try:
        if args.check:
            server = CodexAppServer(executable)
            try:
                print(json.dumps(snapshot(server), indent=2))
            finally:
                server.close()
        else:
            bridge(executable, args.port)
    except KeyboardInterrupt:
        return
    except (OSError, RpcError, ImportError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
