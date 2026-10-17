#!/usr/bin/env python3
"""Verify a pinned upstream ZIP, stage it, and install only with explicit --apply."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import tempfile
import urllib.request
import urllib.parse
import zipfile

ROOT = Path(__file__).resolve().parents[1]
MAX_ARCHIVE = 5 * 1024 * 1024
MAX_EXPANDED = 20 * 1024 * 1024
ALLOWED_HOSTS = {'github.com', 'codeload.github.com'}

def require(condition, message):
    if not condition:
        raise ValueError(message)

class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        target = urllib.parse.urlparse(newurl)
        require(target.scheme == 'https' and target.hostname in ALLOWED_HOSTS, 'Untrusted download redirect')
        return super().redirect_request(request, fp, code, msg, headers, newurl)

def download(lock):
    target = urllib.parse.urlparse(lock['url'])
    require(target.scheme == 'https' and target.hostname in ALLOWED_HOSTS, 'Download requires trusted GitHub HTTPS origin')
    request = urllib.request.Request(lock['url'], headers={'User-Agent': 'asterisk-telephony-pinned-installer/1'})
    with urllib.request.build_opener(SafeRedirect()).open(request, timeout=30) as response:
        data = response.read(MAX_ARCHIVE + 1)
    require(len(data) <= MAX_ARCHIVE, 'Archive exceeds size limit')
    return data

def verified_files(data, lock):
    require(0 < len(data) <= MAX_ARCHIVE, 'Invalid archive size')
    require(len(data) == lock['archive_bytes'], 'Archive byte count mismatch')
    require(hashlib.sha256(data).hexdigest() == lock['sha256'], 'Archive SHA-256 mismatch')
    prefix = lock['source_prefix']
    require(prefix.endswith('/custom_components/asterisk/') and '..' not in prefix and not prefix.startswith('/'), 'Invalid source prefix')
    files = {}; total = 0
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        require(len(archive.infolist()) <= 2000, 'Too many archive entries')
        seen = set()
        for info in archive.infolist():
            name = info.filename
            parts = PurePosixPath(name).parts
            require(name not in seen, 'Duplicate ZIP entry')
            seen.add(name)
            require(not name.startswith('/') and '\\' not in name and ':' not in name and '..' not in parts, 'Unsafe ZIP path')
            mode = (info.external_attr >> 16) & 0o170000
            require(mode != stat.S_IFLNK, 'Symlinks are not allowed in the archive')
            total += info.file_size
            require(total <= MAX_EXPANDED, 'Expanded archive exceeds size limit')
            if name.startswith(prefix) and not info.is_dir():
                relative = name[len(prefix):]
                require(relative and all(x not in ('', '.', '..') for x in relative.split('/')), 'Unsafe component path')
                files[relative] = archive.read(info)
    require({'__init__.py', 'manifest.json', 'services.yaml'} <= files.keys(), 'Incomplete integration payload')
    manifest = json.loads(files['manifest.json'])
    require(manifest.get('domain') == 'asterisk' and manifest.get('version') == lock['version'], 'Integration domain/version mismatch')
    return files

def no_symlink_path(path):
    for item in [path, *path.parents]:
        require(not item.is_symlink(), 'Target path contains a symlink')

def stage(files, stage_root):
    stage_root = Path(stage_root)
    no_symlink_path(stage_root)
    target = stage_root / 'custom_components' / 'asterisk'
    no_symlink_path(target)
    require(not target.exists(), 'Staging target exists; use a fresh stage directory')
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = Path(tempfile.mkdtemp(prefix='.asterisk-', dir=target.parent))
    try:
        for relative, data in files.items():
            file = temporary / relative
            file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with file.open('xb') as stream:
                os.chmod(file, 0o600)
                stream.write(data)
        temporary.rename(target)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return target

def install(staged, config_dir, apply=False):
    config_dir = Path(config_dir)
    no_symlink_path(config_dir)
    require(config_dir.is_dir() and (config_dir / 'configuration.yaml').is_file(), 'Choose the real existing HA config directory')
    target = config_dir / 'custom_components' / 'asterisk'
    no_symlink_path(target)
    require(not target.exists(), 'Integration already exists; back it up and move it aside, or use HACS updates')
    if not apply:
        return False
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix='.asterisk-install-', dir=target.parent))
    try:
        shutil.copytree(staged, temporary, dirs_exist_ok=True)
        temporary.rename(target)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return True

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lock', type=Path, default=ROOT / 'upstream.lock.json')
    parser.add_argument('--archive', type=Path, help='Use an already downloaded archive, still checked against the lock')
    parser.add_argument('--stage', type=Path, default=Path('.local/upstream-stage'))
    parser.add_argument('--install-to', type=Path, help='Existing HA config directory')
    parser.add_argument('--apply', action='store_true', help='Explicitly copy to --install-to; never restart HA')
    args = parser.parse_args()
    if args.apply and not args.install_to:
        parser.error('--apply requires --install-to')
    try:
        lock = json.loads(args.lock.read_text())
        if args.archive:
            require(args.archive.stat().st_size <= MAX_ARCHIVE, 'Archive exceeds size limit')
            data = args.archive.read_bytes()
        else:
            data = download(lock)
        files = verified_files(data, lock)
        staged = stage(files, args.stage)
        applied = install(staged, args.install_to, args.apply) if args.install_to else False
    except (ValueError, OSError, KeyError, zipfile.BadZipFile) as exc:
        parser.error(str(exc))
    print('Pinned integration verified and ' + ('installed. Restart HA only after config validation.' if applied else 'staged only. HA was not changed or restarted.'))

if __name__ == '__main__':
    main()
