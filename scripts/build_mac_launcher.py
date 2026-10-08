#!/usr/bin/env python3
"""Build a universal thin URL adapter on macOS; never install or launch it."""
import plistlib
import subprocess
import sys
from common import ROOT, stage_directory

def main():
    if sys.platform != 'darwin':
        raise SystemExit('This build requires macOS and Xcode command-line tools.')
    stage = stage_directory('mac-launcher')
    app = stage / 'TG-MFG Launcher.app'
    macos = app / 'Contents/MacOS'
    macos.mkdir(parents=True)
    for arch in ('arm64', 'x86_64'):
        subprocess.run(['xcrun', 'swiftc', str(ROOT / 'launcher-mac.swift'), '-O', '-module-cache-path', str(stage / 'ModuleCache'), '-target', f'{arch}-apple-macos11.0', '-o', str(stage / arch)], check=True, timeout=180)
    subprocess.run(['xcrun', 'lipo', '-create', str(stage / 'arm64'), str(stage / 'x86_64'), '-output', str(macos / 'TG-MFG-Launcher')], check=True, timeout=30)
    info = {'CFBundleExecutable': 'TG-MFG-Launcher', 'CFBundleIdentifier': 'local.tg-mfg.launcher', 'CFBundleName': 'TG-MFG Launcher', 'CFBundlePackageType': 'APPL', 'CFBundleVersion': '1', 'CFBundleShortVersionString': '1.0', 'LSUIElement': True, 'LSMinimumSystemVersion': '11.0', 'CFBundleURLTypes': [{'CFBundleURLName': 'TG-MFG core start', 'CFBundleURLSchemes': ['tg-mfg'], 'CFBundleTypeRole': 'Editor'}]}
    (app / 'Contents/Info.plist').write_bytes(plistlib.dumps(info))
    subprocess.run(['codesign', '--force', '--sign', '-', str(app)], check=True, timeout=30)
    subprocess.run(['codesign', '--verify', '--strict', str(app)], check=True, timeout=30)
    print(f'Built {app}; ad-hoc signed, not notarized, not registered or launched.')
if __name__ == '__main__':
    main()
