# Raspberry Pi setup

The Pi reads quota data from Codex App Server and serves only the four display values. The ESP32 polls the relay every 30 seconds.

## Set up the Pi

Put `bridge.py` and `quota_server.py` in the same directory. Install the Codex CLI for the Pi's architecture at `~/.local/bin/codex`. As the Linux user that will run the service, sign in with `~/.local/bin/codex login --device-auth` and verify the session with `python3 bridge.py --check --codex ~/.local/bin/codex`. [Codex authentication](https://learn.chatgpt.com/docs/auth)

Generate a LAN key in the project directory on first installation. Keep it across updates.

```sh
umask 077
openssl rand -hex 32 > lan.key
```

Set `QUOTA_BIND` to the Pi's local IPv4 address, `QUOTA_LAN` to the allowed subnet in CIDR notation, and optionally `QUOTA_PORT` to the desired port (default 8765). Use a stable address. Run `python3 quota_server.py` to check that the relay starts.

To start at boot, adapt this example as `/etc/systemd/system/codex-quota-mini.service`.

```ini
[Unit]
Description=Codex Quota Mini relay
Wants=network-online.target
After=network-online.target

[Service]
User=pi
WorkingDirectory=/home/pi/codex-quota-mini
Environment=QUOTA_BIND=192.168.0.10
Environment=QUOTA_LAN=192.168.0.0/24
ExecStart=/usr/bin/python3 -u /home/pi/codex-quota-mini/quota_server.py
Restart=always
RestartSec=5
NoNewPrivileges=true
UMask=0077

[Install]
WantedBy=multi-user.target
```

```sh
sudo systemctl daemon-reload
sudo systemctl enable --now codex-quota-mini
sudo systemctl status codex-quota-mini
```

## Set up the ESP32

Create `include/lan_secret.h` locally. Replace the URL with your Pi's URL and the placeholder key with the value from `lan.key`.

```cpp
#pragma once
#define QUOTA_URL "http://192.168.0.10:8765/quota"
constexpr char kLanKey[] = "REPLACE_WITH_YOUR_LAN_KEY";
```

Build and upload the firmware. If the board cannot join a known network, connect your phone to the temporary `CodexQuota-Setup` access point, open `http://192.168.4.1`, and select your 2.4 GHz Wi-Fi network. The setup portal is available for three minutes. Once connected, you can power the ESP32 with a USB charger.

Use the HTTP relay on a trusted LAN. The dedicated key travels in cleartext on that network and is embedded in the compiled firmware. Keep `lan.key`, `include/lan_secret.h`, and Wi-Fi firmware binaries out of public repositories. Do not forward the relay port to the internet.
