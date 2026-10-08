#!/usr/bin/env python3
"""Fixed-action local launcher. URLs never select a path, command or arguments."""
import contextlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import build_opener, ProxyHandler

# Loopback health checks must never follow Windows/macOS system proxy settings.
urlopen = build_opener(ProxyHandler({})).open

ROOT = Path(__file__).resolve().parent


def valid_activation(value):
    return value in ("tg-mfg://start", "tg-mfg://start/")


def core_state():
    try:
        with urlopen("http://127.0.0.1:8765/api/bootstrap", timeout=1) as response:
            data = json.load(response)
        return data if data.get("application") == "TG-MFG" and data.get("protocol") == 1 else None
    except (OSError, ValueError):
        return None


def core_python(root=ROOT):
    if sys.platform == "win32":
        return root / "runtime" / "pythonw.exe"
    return root / ".venv" / "bin" / "python"


@contextlib.contextmanager
def launch_lock(root=ROOT):
    # Prevent two clicks from starting two cores. Contains no account information.
    path = root / "launcher.lock"
    with path.open("a+b") as file:
        if file.tell() == 0:
            file.write(b"0")
            file.flush()
        file.seek(0)
        try:
            if sys.platform == "win32":
                import msvcrt
                msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
            return
        try:
            yield True
        finally:
            if sys.platform == "win32":
                file.seek(0)
                msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(file, fcntl.LOCK_UN)


def start_core(root=ROOT):
    executable = core_python(root)
    if not executable.is_file() or not (root / "app.py").is_file():
        raise RuntimeError("The core or runtime is missing. Run the installer again.")
    receipt = json.loads((root / "installation.json").read_text(encoding="utf-8"))
    with launch_lock(root) as acquired:
        if not acquired:
            return
        state = core_state()
        if state:
            if (state.get("installation") or {}).get("id") != receipt["id"]:
                raise RuntimeError("Another TG-MFG core is using port 8765. Stop it first.")
            return
        options = {"start_new_session": True} if sys.platform != "win32" else {"creationflags": subprocess.CREATE_NO_WINDOW}
        # This log is startup output only; the core never logs request bodies or credentials.
        with (root / "startup.log").open("wb") as log:
            process = subprocess.Popen([str(executable), "-u", "-B", "-X", "utf8", str(root / "app.py")],
                                       cwd=root, stdin=subprocess.DEVNULL, stdout=log, stderr=log, **options)
        for _ in range(30):
            state = core_state()
            if state and (state.get("installation") or {}).get("id") == receipt["id"]:
                return
            if process.poll() is not None:
                break
            time.sleep(0.3)
        raise RuntimeError("The core did not start. Check startup.log in the installation directory or check whether the port is in use.")


def main():
    if len(sys.argv) != 2 or not valid_activation(sys.argv[1]):
        return 2
    try:
        start_core()
        return 0
    except Exception as exc:
        if sys.platform == "win32":
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, str(exc), "TG-MFG", 0x10)
        else:
            print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
