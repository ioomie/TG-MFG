#!/usr/bin/env python3
"""Build a Windows x64 portable ZIP using a verified official runtime."""
import argparse
import base64
import hashlib
import importlib.metadata as metadata
import json
import struct
import sys
import zipfile
from pathlib import Path
from common import ROOT, DEPENDENCIES, copy_core, stage_directory, write_archive
PYTHON_SHA256 = '97dae5274cc54867065e8d5a3226e48c35017ed332a0fdb0e27d5b5821961297'
PYTHON_URL = 'https://www.python.org/ftp/python/3.13.16/python-3.13.16-embed-amd64.zip'

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', type=Path, required=True, help='Official python-3.13.16-embed-amd64.zip')
    args = parser.parse_args()
    if hashlib.sha256(args.runtime.read_bytes()).hexdigest() != PYTHON_SHA256:
        raise RuntimeError('Official Python SHA-256 mismatch')
    stage = stage_directory('windows/TG-MFG')
    copy_core(stage)
    runtime = stage / 'runtime'
    runtime.mkdir()
    with zipfile.ZipFile(args.runtime) as archive:
        if archive.testzip() or any((Path(n).is_absolute() or '..' in Path(n).parts or '\\' in n for n in archive.namelist())):
            raise RuntimeError('Invalid Python runtime archive')
        archive.extractall(runtime)
    (runtime / 'python313._pth').write_text('python313.zip\n.\n..\nsite-packages\n', encoding='ascii')
    packages = runtime / 'site-packages'
    packages.mkdir()
    dependencies = []
    for (name, version) in DEPENDENCIES:
        dist = metadata.distribution(name)
        if dist.version != version or 'none-any' not in (dist.read_text('WHEEL') or ''):
            raise RuntimeError(f'Install pinned pure-Python dependency: {name}=={version}')
        for relative in dist.files:
            if '..' in relative.parts or '__pycache__' in relative.parts or relative.suffix == '.pyc':
                continue
            original = Path(dist.locate_file(relative))
            if not original.is_file():
                continue
            data = original.read_bytes()
            if relative.hash:
                actual = base64.urlsafe_b64encode(hashlib.new(relative.hash.mode, data).digest()).rstrip(b'=').decode()
                if actual != relative.hash.value:
                    raise RuntimeError(f'Modified dependency: {relative}')
            target = packages / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        dependencies.append({'name': name, 'version': version, 'source': f'https://pypi.org/project/{name}/{version}/'})
    exe = (runtime / 'python.exe').read_bytes()
    pe = struct.unpack_from('<I', exe, 60)[0]
    if exe[:2] != b'MZ' or exe[pe:pe + 4] != b'PE\x00\x00' or struct.unpack_from('<H', exe, pe + 4)[0] != 34404:
        raise RuntimeError('Runtime is not Windows x64')
    sys.path.insert(0, str(ROOT))
    from diagnostics import CORE_VERSION
    info = {'application': 'TG-MFG', 'core_version': CORE_VERSION, 'target': 'Windows x64', 'python': {'version': '3.13.16', 'source': PYTHON_URL, 'sha256': PYTHON_SHA256}, 'dependencies': dependencies, 'validation': 'Verified runtime and dependency hashes. Building a ZIP does not verify native Windows execution.'}
    (stage / 'BUILD-INFO.json').write_text(json.dumps(info, indent=2) + '\n', encoding='utf-8')
    write_archive(ROOT / 'dist/TG-MFG-Windows-x64.zip', [(p, p.relative_to(stage)) for p in stage.rglob('*') if p.is_file()])
if __name__ == '__main__':
    main()
