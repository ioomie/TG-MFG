# Contributing

Run the Python and JavaScript checks in [README.md](README.md). Tests use fake clients and loopback proxies, never real account credentials. Develop directly from source; there is no npm build or CDN.

Preserve loopback-only binding, strict origin/token checks, text-only message rendering, no direct fallback on proxy failure, and a fixed-action launcher. Storage and login retention must remain opt-in; keep protected authorization separate from unencrypted snapshots and exclude both from builds.

English is the source/default interface language. Add Chinese UI translations in `web/translations.js`; do not translate user content. **Errors, CLI failures and API failures must remain English.** Check language switching without losing filters or active work.

Dependency updates must also update pinned hashes, builder version checks and third-party licenses. Core changes require `diagnostics.py` version and changelog updates plus rebuilt installers. Website updates do not replace visitors’ local cores. Add meaningful offline regression tests for installation, storage, origin protection, proxies or diagnostics.

Build outputs belong in ignored `build/` and `dist/`; release ZIPs and hashes are release attachments, not source. Never commit private `.env`, certificates, installation receipts, core settings, logs, diagnostics, sessions or messages.
