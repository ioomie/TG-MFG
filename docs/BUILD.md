# Build installers

Source, docs and licenses belong in Git. Outputs go to ignored `build/` and `dist/`. Builders locate the repository relative to their own files and contain no developer paths or private website origins. Archive ordering and timestamps are deterministic; compiled launcher binaries may differ across toolchains.

## Dependencies

```sh
python -m venv .venv
.venv/bin/python -m pip install --require-hashes -r requirements.txt
```

On Windows substitute `.venv\Scripts\python.exe`. Windows builds require all seven pinned application dependencies. Builders use the standard library and installed dependencies, with no hidden build service.

## Windows x64

Download the [official Python embedded runtime](https://www.python.org/ftp/python/3.13.16/python-3.13.16-embed-amd64.zip). Expected SHA-256:

```text
97dae5274cc54867065e8d5a3226e48c35017ed332a0fdb0e27d5b5821961297
```

```sh
.venv/bin/python scripts/build_windows.py --runtime /path/to/python-3.13.16-embed-amd64.zip
```

The builder checks runtime hash, PE x64 architecture, pinned versions, pure-Python wheel tags and installed dependency RECORD hashes. It copies dependencies/licenses and writes `dist/TG-MFG-Windows-x64.zip` plus `.sha256`. `_pth` isolates global modules/PYTHONPATH; `BUILD-INFO.json` records provenance. Build verification does not replace native Windows install/login/proxy/DPAPI checks.

## Mac universal launcher

Requires macOS and Xcode command-line tools. Builds Intel / Apple Silicon for macOS 11+:

```sh
python scripts/build_mac_launcher.py
python scripts/package.py mac
```

Output: `dist/TG-MFG-Mac.zip` and checksum. The builder compiles and ad-hoc signs only; it does not register, install or launch the helper. It is not notarized. First installation still requires Python 3.9+ and PyPI access. Running from source does not require this compilation.

## Source and website packages

```sh
python scripts/package.py source
# After building both desktop packages:
python scripts/package.py site
```

`dist/TG-MFG-source.zip` excludes binaries, account state and deployment secrets. `dist/TG-MFG-Site.zip` includes the static website and both installers. At download time, the website injects its allowed origin, so the resulting download differs from the original release ZIP/checksum.

Upload only the clean source tree to GitHub. Verified ZIPs/checksums can be release attachments. Never upload runtime/, build/, dist/, tls/, real core-settings.json, installation.json, .env, logs, diagnostics, sessions or messages.

## GitHub upload

See [GITHUB.md](GITHUB.md) for an initial upload to a repository that already has a README/license commit. Do not force-push over its existing history.
