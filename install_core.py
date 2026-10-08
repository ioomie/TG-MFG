#!/usr/bin/env python3
"""Install the script core in a per-user location and register one start action."""
import filecmp
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
import webbrowser
from pathlib import Path
from urllib.request import Request
from launcher import core_state, urlopen

SOURCE = Path(__file__).resolve().parent


def installation_directory(platform=sys.platform, home=None, environ=None):
    home = Path.home() if home is None else Path(home)
    environ = os.environ if environ is None else environ
    if platform == "win32":
        return Path(environ.get("LOCALAPPDATA", home / "AppData" / "Local")) / "TG-MFG"
    if platform == "darwin":
        return home / "Library" / "Application Support" / "TG-MFG"
    raise RuntimeError("Only Windows and Mac are supported.")


def copy_core(source, target):
    # Copy a fixed allowlist. No sessions, browser data or arbitrary working files.
    names = ("app.py", "storage.py", "network_proxy.py", "diagnostics.py", "debug_core.py", "debug.bat", "launcher.py", "install_core.py", "requirements.txt", "run.sh", "run.bat", "run.command",
             "LICENSE", "LICENSES", "THIRD_PARTY_NOTICES.md", "web", "runtime", "mac-launcher", "core-settings.json", "BUILD-INFO.json", "README-Windows.md")
    target.mkdir(parents=True, exist_ok=True)
    for name in names:
        item = source / name
        if not item.exists():
            continue
        files = item.rglob("*") if item.is_dir() else (item,)
        for original in files:
            if not original.is_file() or original.is_symlink() or "__pycache__" in original.parts or original.suffix == ".pyc":
                continue
            destination = target / original.relative_to(source)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if original.resolve() == destination.resolve() or (destination.exists() and filecmp.cmp(original, destination, shallow=False)):
                continue
            shutil.copy2(original, destination)


def windows_protocol_command(target):
    # The URI is passed to Python, never cmd.exe or a shell. launcher.py accepts one exact action.
    return f'"{target / "runtime" / "pythonw.exe"}" -B -X utf8 "{target / "launcher.py"}" "%1"'


def stop_owned_core(target):
    receipt = target / "installation.json"
    if not receipt.exists():
        return
    previous = json.loads(receipt.read_text(encoding="utf-8"))
    state = core_state()
    if not previous.get("id") or not state or (state.get("installation") or {}).get("id") != previous.get("id"):
        return  # Never stop another installation or an unrelated service.
    request = Request("http://127.0.0.1:8765/api/shutdown", data=b"{}", method="POST",
                      headers={"Content-Type": "application/json", "X-App-Token": state["token"]})
    with urlopen(request, timeout=45) as response:
        json.load(response)
    for _ in range(20):
        if core_state() is None:
            return
        time.sleep(0.3)
    raise RuntimeError("The previous core has not stopped. Stop it before reinstalling.")


def register_windows(target):
    import winreg
    path = r"Software\Classes\tg-mfg"
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, path) as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, "URL:TG-MFG local core")
        winreg.SetValueEx(key, "URL Protocol", 0, winreg.REG_SZ, "")
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, path + r"\shell\open\command") as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, windows_protocol_command(target))


def prepare_mac(target):
    if not (target / ".venv" / "bin" / "python").exists():
        subprocess.run([sys.executable, "-m", "venv", str(target / ".venv")], check=True, timeout=120)
    executable = target / ".venv" / "bin" / "python"
    versions = "from importlib.metadata import version; assert all(version(n) == v for n,v in [('python-socks', '3.1.1'), ('async-timeout', '5.0.1'), ('Telethon','1.45.0'),('PySocks','1.7.1'),('pyaes','1.6.1'),('rsa','4.9.1'),('pyasn1','0.6.4')])"
    result = subprocess.run([str(executable), "-c", versions], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
    if result.returncode:
        subprocess.run([str(executable), "-m", "pip", "install", "--disable-pip-version-check", "--index-url", "https://pypi.org/simple",
                        "--require-hashes", "--timeout", "15", "--retries", "1", "-r", str(target / "requirements.txt")], check=True, timeout=240)
    apps = Path.home() / "Applications"
    apps.mkdir(exist_ok=True)
    app = apps / "TG-MFG Launcher.app"
    shutil.copytree(target / "mac-launcher" / "TG-MFG Launcher.app", app, dirs_exist_ok=True)
    subprocess.run(["/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister", "-f", str(app)],
                   check=True, timeout=15)


def main():
    if sys.version_info < (3, 9):
        raise RuntimeError("Python 3.9 or later is required.")
    target = installation_directory()
    print(f"TG-MFG installation directory: {target}", flush=True)
    stop_owned_core(target)
    copy_core(SOURCE, target)
    if sys.platform == "win32":
        if not (target / "runtime" / "pythonw.exe").exists():
            raise RuntimeError("Download the Windows x64 package from the website. This source package does not include a runtime.")
        register_windows(target)
    else:
        prepare_mac(target)
    receipt_path = target / "installation.json"
    previous = {}
    if receipt_path.exists():
        previous = json.loads(receipt_path.read_text(encoding="utf-8"))
    previous_id = previous.get("id")
    receipt = {"managed": True, "version": 1, "platform": "windows" if sys.platform == "win32" else "mac",
               "id": previous_id if isinstance(previous_id, str) and previous_id else str(uuid.uuid4())}
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    settings = target / "core-settings.json"
    origin = json.loads(settings.read_text(encoding="utf-8")).get("portal_origin", "") if settings.exists() else ""
    executable = target / "runtime" / "python.exe" if sys.platform == "win32" else target / ".venv" / "bin" / "python"
    subprocess.run([str(executable), "-B", "-X", "utf8", str(target / "launcher.py"), "tg-mfg://start"], check=True, timeout=45)
    print("Installation complete. Click “Start core” on the website next time, without locating a script.", flush=True)
    webbrowser.open(origin or "http://127.0.0.1:8765")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Installation incomplete: {exc}\nStop the previous core and try again.", file=sys.stderr)
        raise SystemExit(1)
