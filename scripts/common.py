"""Explicit distribution allowlists; never sweep a developer's working directory."""
import hashlib
import shutil
import zipfile
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
DEPENDENCIES = [('python-socks', '3.1.1'), ('async-timeout', '5.0.1'), ('Telethon', '1.45.0'), ('PySocks', '1.7.1'), ('pyaes', '1.6.1'), ('rsa', '4.9.1'), ('pyasn1', '0.6.4')]
CORE_FILES = ('app.py', 'storage.py', 'network_proxy.py', 'diagnostics.py', 'launcher.py', 'install_core.py', 'debug_core.py', 'debug.bat', 'install.bat', 'install.command', 'run.bat', 'run.command', 'run.sh', 'requirements.txt', 'README.md', 'README.zh-CN.md', 'README-Windows.md', 'LICENSE', 'THIRD_PARTY_NOTICES.md')
SOURCE_FILES = CORE_FILES + ('portal.py', 'launcher-mac.swift', 'Dockerfile', 'compose.yaml', '.dockerignore', '.gitignore', '.gitattributes', '.env.example', 'DEPLOYMENT.md', 'SECURITY.md', 'CONTRIBUTING.md', 'CHANGELOG.md')

def selected_files(source=False):
    for name in SOURCE_FILES if source else CORE_FILES:
        yield (ROOT / name)
    for folder in ('web', 'LICENSES', 'docs', 'tests', 'scripts', '.github') if source else ('web', 'LICENSES'):
        for path in sorted((ROOT / folder).rglob('*')):
            if path.is_file() and (not path.is_symlink()) and (path.suffix in ('.py', '.js', '.mjs', '.css', '.html', '.md', '.txt', '.rst', '.yml', '.yaml', '.svg', '.png', '')) and ('__pycache__' not in path.parts):
                yield path
    if source:
        yield (ROOT / 'downloads/.gitkeep')
    else:
        for path in sorted((ROOT / 'docs/assets').glob('*')):
            if path.is_file() and not path.is_symlink() and path.suffix in ('.png', '.svg'):
                yield path

def stage_directory(name):
    path = ROOT / 'build' / name
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)
    return path

def copy_core(stage):
    for path in selected_files():
        target = stage / path.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)

def write_archive(output, entries, prefix='TG-MFG'):
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for (path, relative) in sorted(entries, key=lambda pair: str(pair[1])):
            if path.is_symlink():
                raise ValueError('Symlinks are not accepted in distributions')
            data = path.read_bytes()
            if path.suffix == '.bat':
                data = data.decode('ascii').replace('\r\n', '\n').replace('\n', '\r\n').encode('ascii')
            info = zipfile.ZipInfo(f'{prefix}/{relative.as_posix()}', date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            mode = 493 if path.suffix in ('.sh', '.command') or path.name == 'TG-MFG-Launcher' else 420
            info.external_attr = (32768 | mode) << 16
            archive.writestr(info, data)
    with zipfile.ZipFile(output) as archive:
        if archive.testzip():
            raise RuntimeError('Corrupt archive')
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix(output.suffix + '.sha256').write_text(f'{digest}  {output.name}\n', encoding='ascii')
    print(f'{output.name}: {output.stat().st_size:,} bytes; SHA-256 {digest}')
