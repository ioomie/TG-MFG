# Open-source preparation review

Prepared on 2026-10-09. Core 1.6.0. MIT license.

This directory is the source candidate for [ioomie/TG-MFG](https://github.com/ioomie/TG-MFG). Do not upload its parent workspace, private deployment directory or historical private packages. Preparation does not mean the code has been pushed.

## Prepared

- Explicit source/build allowlists; binaries, runtimes, configs, logs, data and screenshots remain separate unless intentional public artwork.
- Reserved example domains, blank origin template and configurable loopback HTTP upstream for a trusted HTTPS reverse proxy.
- MIT license, seven dependency licenses and third-party notices.
- English-default documentation/UI, optional Chinese README/interface, English-only errors and fictional public artwork.
- Offline Python/JavaScript tests, pinned GitHub Actions and dependency hash checks.
- Coverage for origin/token protection, login state, counting, fetch pacing, blocked messages, search, proxy handshakes, redaction and persistence.
- Protected opt-in authorization, account/channel snapshot isolation and exact-ID update checks preserving unconfirmed records.

Local verification: 71 Python tests and 13 JavaScript tests pass. Headless browser checks cover the English default, Chinese preference persistence, language switching during sign-in and fetching, preserved filters/sorting, verbatim messages, English errors and narrow layouts. The preview/social artwork uses fictional content.

## Release boundaries

GitHub CI has not run for this code until it is uploaded. Stable installers require native Windows registration/launching/DPAPI and explicit proxy tests with TUN disabled, plus Mac first-install/launch checks. The Mac launcher is ad-hoc signed and not notarized. Offline tests use no real Telegram accounts and do not constitute an independent audit.

The configured website is a trust boundary. Protect HTTPS and downloadable code, and enable private vulnerability reporting. Telegram sessions are account authorization, not read-only tokens. Reading is still subject to Telegram limits.

Source belongs in Git; verified ZIPs/checksums belong in Releases. Private .env, TLS keys, core-settings.json, installation.json, data, sessions and diagnostics must remain excluded.
