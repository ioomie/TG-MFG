"""Enable sanitized logs in the installed core. All requests go directly to loopback."""
import json
import sys
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request
from launcher import core_state, urlopen, start_core


def main():
    root = Path(__file__).resolve().parent
    if not (root / "installation.json").exists():
        raise RuntimeError("Please run install.bat first, then run debug.bat again.")
    start_core(root)
    state = core_state()
    if not state:
        raise RuntimeError("Core did not respond. Check startup.log in the installation folder.")
    headers = {"Content-Type": "application/json", "X-App-Token": state["token"]}
    try:
        with urlopen(Request("http://127.0.0.1:8765/api/debug", data=b'{"enabled":true}', headers=headers), timeout=5) as response:
            json.load(response)
    except HTTPError as exc:
        if exc.code == 404:
            raise RuntimeError("Installed core is too old. Download the latest package and run install.bat.") from None
        raise
    print("Debug mode is on. Reproduce the problem in your browser.")
    print("Sanitized core log:", root / "debug.log")
    print("Startup log:", root / "startup.log")
    print("Local UI: http://127.0.0.1:8765 (type this address directly if website access fails)")
    print("Core version:", state.get("core_version", "unknown"))
    print("Use Connection diagnostics -> Test current proxy settings to verify SOCKS5/HTTP.")
    print("The log records proxy backend, handshake, and Telegram connection separately.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # No inputs or account requests are used by this helper.
        print("Debug could not start:", type(exc).__name__, str(exc), file=sys.stderr)
        raise SystemExit(1)
