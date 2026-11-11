"""Existing Codex App Server stdio integration; no authentication mutations."""

import json
from queue import Empty, Queue
import subprocess
from threading import Thread
import time
from typing import Any

from .models import ErrorCode


class RpcError(RuntimeError):
    def __init__(self, message: str, code: ErrorCode = ErrorCode.UPSTREAM) -> None:
        super().__init__(message)
        self.code = code


class CodexAppServer:
    def __init__(self, executable: str) -> None:
        self.process = subprocess.Popen(
            [executable, "app-server", "--listen", "stdio://"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
        )
        self.responses: Queue[Any] = Queue()
        self.request_id = 0
        self.reader = Thread(target=self._read, daemon=True)
        self.reader.start()
        try:
            self.request("initialize", {
                "clientInfo": {"name": "codex-quota-mini", "version": "2.0.0"},
            })
            self._send({"method": "initialized"})
        except BaseException:
            self.close()
            raise

    def _read(self) -> None:
        assert self.process.stdout is not None
        try:
            for line in self.process.stdout:
                try:
                    self.responses.put(json.loads(line))
                except ValueError:
                    continue
        finally:
            self.responses.put(None)

    def _send(self, message: dict[str, Any]) -> None:
        assert self.process.stdin is not None
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()

    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.request_id += 1
        message: dict[str, Any] = {"method": method, "id": self.request_id}
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
                raise RpcError("The Codex process stopped.", ErrorCode.TRANSPORT)
            if not isinstance(response, dict) or response.get("id") != self.request_id:
                continue
            if "error" in response:
                error = response["error"]
                code = error.get("code") if isinstance(error, dict) else None
                normalized = ErrorCode.AUTH if code in (401, 403) else ErrorCode.UPSTREAM
                raise RpcError(f"{method}: RPC error {code}.", normalized)
            result = response.get("result")
            if not isinstance(result, dict):
                raise RpcError("Invalid RPC result.", ErrorCode.INVALID_DATA)
            return result
        raise RpcError(f"{method}: request timed out.", ErrorCode.TIMEOUT)

    def close(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
        try:
            self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
        self.reader.join(timeout=1)
        assert self.process.stdin is not None and self.process.stdout is not None
        self.process.stdin.close()
        self.process.stdout.close()
