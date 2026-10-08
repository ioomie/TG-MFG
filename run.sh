#!/bin/sh
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
fi
if ! .venv/bin/python -c "from importlib.metadata import version; assert all(version(n) == v for n, v in [('python-socks', '3.1.1'), ('async-timeout', '5.0.1'), ('Telethon', '1.45.0'), ('PySocks', '1.7.1'), ('pyaes', '1.6.1'), ('rsa', '4.9.1'), ('pyasn1', '0.6.4')])" 2>/dev/null; then
  .venv/bin/python -m pip install --index-url https://pypi.org/simple --require-hashes --timeout 15 --retries 1 -r requirements.txt
fi
exec .venv/bin/python app.py "$@"
