#!/bin/sh
cd "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)" || exit 1
if ! command -v python3 >/dev/null 2>&1; then
  printf '%s\n' 'TG-MFG needs Python 3.9 or newer. Install it from https://www.python.org/downloads/macos/'
  printf '%s' 'Press Enter to close: '
  read -r answer
  exit 1
fi
sh run.sh --open-browser "$@"
result=$?
if [ "$result" -ne 0 ]; then
  printf '%s\n' 'TG-MFG could not start. See the error above.'
  printf '%s' 'Press Enter to close: '
  read -r answer
fi
exit "$result"
