# AI Quota Hub — Codex Quota Mini

A 240 × 240 GC9A01 round display shows Codex usage on an ESP32-C3 Super Mini. Tap the capacitive button to cycle through four pages.

The Python host now has a typed multi-provider architecture. Codex is the first
and only production adapter. The existing device continues to receive its four
V1 pages; a bounded, paginated V2 protocol is available for future clients.
No firmware update is required for this migration.

| Page | What it shows |
| --- | --- |
| 5 hours | Remaining percentage and reset time |
| Weekly | Remaining percentage and next reset |
| Credits | Balance reported by Codex |
| Tokens used | Usage for the latest day reported by Codex |

The adapter converts Codex's used percentages into remaining percentages and
reads token activity separately using the existing official App Server calls.
[Codex App Server documentation](https://learn.chatgpt.com/docs/app-server)

## Architecture and offline checks

Requires Python 3.10 or later. The hub and HTTP relay use only the standard
library; USB retains its existing `pyserial` dependency.

```sh
python3 bridge.py --check --demo
python3 bridge.py --check --demo --protocol 2
python3 -m unittest discover -s tests -v
```

Demo mode uses synthetic RPC replies and never launches Codex. Tests need no
account, credentials, connected ESP32, or third-party Python package. HTTP
tests temporarily bind a loopback port.

See [the V2 architecture and protocol](docs/architecture-v2.md) for the model,
adapter contract, pagination, compatibility boundaries, and extension example.
The roadmap targets OpenAI/Codex, Claude, Gemini, DeepSeek, Qwen, Mistral, Grok,
Perplexity, OpenRouter, and Ollama/local models. The other nine adapters and a
configuration web interface are outside this iteration.

## Wiring

| Component | ESP32-C3 Super Mini |
| --- | --- |
| Display VCC / GND | 3V3 / GND |
| Display SCL / SDA | GPIO2 / GPIO4 |
| Display DC / CS / RST | GPIO1 / GPIO5 / GPIO0 |
| Touch button VCC / GND / I/O | 3V3 / GND / GPIO6 |

## Build and upload

Install PlatformIO, connect the ESP32 via USB, and identify its port with `pio device list`.

```sh
pio run -e supermini
pio run -e supermini -t upload --upload-port COM11
```

`COM11` is a Windows example. Replace it with your device's port. Without a local Wi-Fi configuration, the firmware uses USB.

## Usage

- **Standalone Wi-Fi**. A Raspberry Pi reads the quotas and the ESP32 retrieves them over the local network. You can turn off your PC. [Raspberry Pi setup](docs/raspberry-pi.md)
- **USB**. A Python bridge reads Codex on your PC and sends data every 30 seconds. [USB setup](docs/usb.md)
