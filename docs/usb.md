# USB setup

Install Python 3.10+, the Codex CLI, and `pyserial`. Keep `bridge.py` and the
complete `quota_hub/` package together. Sign in to Codex with the ChatGPT account
you want to monitor.

```powershell
python -m pip install pyserial
python .\bridge.py --check
python .\bridge.py --port COM11
```

`--check` verifies the Codex session. `--port` keeps the bridge running. Replace `COM11` with your board's port and close any serial monitor before starting the bridge. On Windows, the bridge can detect Codex Desktop outside your PATH. Use `--codex PATH` to select an executable explicitly.

The default protocol remains V1, with exactly four pages and a 512-byte maximum
including the newline. `--check` emits compact JSON; unavailable Codex quotas
produce `{"v":1,"ok":false}` and a nonzero exit status.

```powershell
python .\bridge.py --check --demo
python .\bridge.py --check --demo --protocol 2
python .\bridge.py --port COM11 --demo
```

`--demo` uses synthetic data without a Codex account. `--check --protocol 2`
emits one compact JSON object per line (a provider manifest followed by metric
pages). It can also inspect real data when `--demo` is omitted. V2 inspection
reports per-provider errors in the JSON, so clients must inspect states.
`--port --protocol 2` is deliberately rejected: the current firmware understands
only V1. See [V2 compatibility](architecture-v2.md).
