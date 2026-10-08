#!/usr/bin/env python3
"""Package source, a built Mac core, or the static website with desktop downloads."""
import argparse
import shutil
from common import ROOT, selected_files, copy_core, stage_directory, write_archive

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('target', choices=('source', 'mac', 'site'))
    args = parser.parse_args()
    if args.target == 'source':
        write_archive(ROOT / 'dist/TG-MFG-source.zip', [(p, p.relative_to(ROOT)) for p in selected_files(source=True)])
        return
    if args.target == 'mac':
        launcher = ROOT / 'build/mac-launcher/TG-MFG Launcher.app'
        if not (launcher / 'Contents/MacOS/TG-MFG-Launcher').is_file():
            raise SystemExit('First run python scripts/build_mac_launcher.py on macOS.')
        stage = stage_directory('mac/TG-MFG')
        copy_core(stage)
        shutil.copytree(launcher, stage / 'mac-launcher/TG-MFG Launcher.app')
    else:
        stage = stage_directory('site/TG-MFG')
        for name in ('portal.py', 'Dockerfile', 'compose.yaml', '.env.example', '.dockerignore', 'DEPLOYMENT.md', 'LICENSE', 'THIRD_PARTY_NOTICES.md'):
            shutil.copy2(ROOT / name, stage / name)
        for name in ('web', 'LICENSES', 'docs'):
            shutil.copytree(ROOT / name, stage / name, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        (stage / 'downloads').mkdir()
        for name in ('TG-MFG-Windows-x64.zip', 'TG-MFG-Mac.zip'):
            package = ROOT / 'dist' / name
            if not package.is_file():
                raise SystemExit(f'Build {name} first.')
            shutil.copy2(package, stage / 'downloads' / name)
    name = 'TG-MFG-Mac.zip' if args.target == 'mac' else 'TG-MFG-Site.zip'
    write_archive(ROOT / 'dist' / name, [(p, p.relative_to(stage)) for p in stage.rglob('*') if p.is_file()])
if __name__ == '__main__':
    main()
