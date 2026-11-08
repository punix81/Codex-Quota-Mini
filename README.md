# Codex Quota Mini

A 240 × 240 GC9A01 round display shows Codex usage on an ESP32-C3 Super Mini. Tap the capacitive button to cycle through four pages.

| Page | What it shows |
| --- | --- |
| 5 hours | Remaining percentage and reset time |
| Weekly | Remaining percentage and next reset |
| Credits | Balance reported by Codex |
| Tokens used | Usage for the latest day reported by Codex |

Codex reports remaining quotas as percentages and publishes token usage separately. [Codex App Server documentation](https://learn.chatgpt.com/docs/app-server)

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
