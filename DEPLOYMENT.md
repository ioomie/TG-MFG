# Deploy the private website

The container runs `portal.py`, serving only the UI and installers. It does not install Telethon or receive account API requests. Each visitor’s local core connects to Telegram independently. Running from source on one computer requires no website server.

## Setup

1. Build matching Mac and Windows packages using [BUILD.md](docs/BUILD.md), or use verified packages of the same version.
2. Copy `TG-MFG-Mac.zip` and `TG-MFG-Windows-x64.zip` into `downloads/`. Do not commit them as source.
3. Copy `.env.example` to `.env` and set `TG_MFG_ORIGIN` to the full HTTPS origin, without a path, for example `https://tg.example.com`. Examples use reserved domains; the template is intentionally blank.

```sh
cp .env.example .env
# Edit .env before starting.
docker compose up -d --build
docker compose ps
```

Compose binds its HTTP upstream to server loopback `127.0.0.1:7333`. Put a trusted HTTPS reverse proxy in front, preserving the Host header:

```nginx
location / {
    proxy_set_header Host $http_host;
    proxy_pass http://127.0.0.1:7333;
}
```

Manage certificates and listener ports in your proxy. For a proxy in another container or machine, adjust `TG_MFG_BIND` and the upstream network, restricting upstream access. The website has no separate user authentication and is intended for a controlled private network, not a public multi-user service. A Linux server/container may host the static website; **Linux clients are unsupported**.

HTTPS-to-loopback access may require browser local network permission. Use **Open local page** if permission is unavailable or unsupported. A changed website origin requires a new download and reinstall. The downloaded `core-settings.json` contains that exact origin; wildcard origins are never enabled.

## Direct TLS

`portal.py` also accepts a certificate and key together:

```sh
python portal.py --origin https://tg.example.com --packages downloads \
  --tls-cert tls/server.crt --tls-key tls/server.key
```

Containers need read-only certificate mounts, a command override and an HTTPS health check. Keep private keys out of source and downloads; `tls/` is ignored. Browsers must trust the certificate. An untrusted certificate may block local network access.

## Install and update

The UI defaults to English; users can choose Simplified Chinese. All errors are English. Use `/#install` to reopen installation/downloads.

Windows installs to `%LOCALAPPDATA%\TG-MFG`; Mac installs to `~/Library/Application Support/TG-MFG`, with `~/Applications/TG-MFG Launcher.app` as the launcher. No silent installation, tray service or boot auto-start is configured. Login retention is opt-in.

When changing the UI, rebuild the website container. When changing core code or its errors, build both installers, replace `downloads/` and rebuild the container. Visitors must reinstall to update their local Python code; refreshing the website alone does not update it. Core 1.6.0 adds English errors and bilingual UI; local storage/update checks require 1.5.0, speed control 1.4.0 and the blocked box 1.3.0. Older versions display feature upgrade prompts.

```sh
docker compose logs --tail 50
docker compose down
```

Stopping the website does not stop visitors’ local cores. Saved data lives on each visitor’s computer, never in the website container. Never commit or package actual `.env`, TLS keys, `core-settings.json`, `installation.json`, `data/`, SQLite files, login ciphertext, sessions, logs or diagnostics.
