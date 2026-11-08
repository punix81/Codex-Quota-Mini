# USB setup

Install Python, the Codex CLI, and `pyserial`. Sign in to Codex with the ChatGPT account you want to monitor.

```powershell
python -m pip install pyserial
python .\bridge.py --check
python .\bridge.py --port COM11
```

`--check` verifies the Codex session. `--port` keeps the bridge running. Replace `COM11` with your board's port and close any serial monitor before starting the bridge. On Windows, the bridge can detect Codex Desktop outside your PATH. Use `--codex PATH` to select an executable explicitly.
