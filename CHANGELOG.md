# Changelog

## 1.6.0

- English-default UI with a persistent Simplified Chinese option; switching preserves current filters and user content.
- English-only core, API, launcher, installer and diagnostic errors.
- English documentation, linked Chinese README, fictional UI artwork and GitHub upload guide.

## 1.5.0

- Fetch reports explain observed skipped records and early stops.
- Opt-in per-account/channel local snapshots and blocked records, offline search and restart restoration.
- macOS Keychain / Windows current-user DPAPI login retention, preserving proxy configuration.
- Update checks by exact message IDs; retain previous text and unconfirmed snapshots without appending new messages.
- Stop preserves opted-in data; disconnect clears saved data and attempts logout. Builds exclude runtime data.

## 1.4.0

- Batch intervals of 0.5, 1, 2, 5, 10 and 30 seconds, default 1; changes during fetching validate task IDs.
- Reload restores active speed; old cores show an upgrade prompt.
- Waiting respects cancellation/time budgets; FloodWait stops and retains partial results.
- Offline tests exercise real Telethon 100-record batching and pacing.

## 1.3.0

- Block/restore individual messages; blocked messages are excluded from normal sorting, text and hashtag search.
- Channel/message ID isolation and retention across re-fetches in the same session.
- The blocked box spans channels independently of normal filters. Restoring preserves active sort/filter settings.

## 1.2.1

- Asynchronous python-socks handshakes fix the Windows proxy path.
- Proxy tunnel tests and no fallback to direct access.
- Diagnostics include proxy backend, OS/Python version and error stage.

## 1.2.0

- Bounded, redacted diagnostics and Windows debug.bat.
- Allow top-level navigation to the local page without weakening cross-site API checks.
- Verify authenticated API initialization before reporting core availability; distinguish Telegram login state.

## Open-source preparation

- Clean source separated from private deployment/runtime files.
- Portable builders, third-party licenses, security documentation and cross-platform CI.
- Configurable origins; no private domains, sessions, logs or compiled runtime in source.
