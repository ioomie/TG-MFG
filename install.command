#!/bin/sh
cd "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)" || exit 1
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1; then
  printf '%s\n' 'Python 3.9+ is needed. Install it from https://www.python.org/downloads/macos/'
  printf '%s' 'Press Enter to close: '
  read -r answer
  exit 1
fi
python3 -B install_core.py
result=$?
printf '%s' 'Press Enter to close: '
read -r answer
exit "$result"
