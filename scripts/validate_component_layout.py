#!/usr/bin/env python3
"""Check an offline layout without importing components or changing files.

Inspect directory and manifest safety, reject duplicate domains, then verify the
requested component's path and version. This does not validate HA compatibility.
"""
import argparse
import json
import os
from pathlib import Path
import re
import stat

MAX_MANIFEST_BYTES = 256 * 1024
MAX_CHILDREN = 1024
DOMAIN = re.compile(r'[a-z][a-z0-9_]{0,127}')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def absolute_path(value):
    path = Path(value)
    require('..' not in path.parts, 'Parent traversal is not allowed')
    return path if path.is_absolute() else Path.cwd() / path


def check_directory_ancestry(path):
    """Require a real directory at every level, including the requested path."""
    # Do not resolve symlinks before checking: resolving would hide them.
    for item in reversed((path, *path.parents)):
        info = item.lstat()
        require(not stat.S_ISLNK(info.st_mode), 'Layout path contains a symlink')
        require(stat.S_ISDIR(info.st_mode), 'Layout path is not a directory')
    return path.lstat()


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'Manifest contains duplicate JSON keys')
        result[key] = value
    return result


def invalid_constant(value):
    raise ValueError('Manifest contains a nonstandard JSON number')


def stat_fingerprint(info):
    """Identity and metadata used to detect changes while reading."""
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


def read_manifest_bytes(directory_fd):
    """Read one bounded, unchanged regular file; absence means no component."""
    try:
        before = os.stat('manifest.json', dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    require(stat.S_ISREG(before.st_mode), 'Manifest must be a regular file, not a symlink')
    require(before.st_size <= MAX_MANIFEST_BYTES, 'Manifest exceeds size limit')
    fd = os.open('manifest.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                 dir_fd=directory_fd)
    with os.fdopen(fd, 'rb') as stream:
        opened = os.fstat(stream.fileno())
        require(stat_fingerprint(opened) == stat_fingerprint(before), 'Manifest changed during validation')
        raw = stream.read(MAX_MANIFEST_BYTES + 1)
        require(len(raw) <= MAX_MANIFEST_BYTES, 'Manifest exceeds size limit')
        final = os.fstat(stream.fileno())
    current = os.stat('manifest.json', dir_fd=directory_fd, follow_symlinks=False)
    require(stat_fingerprint(before) == stat_fingerprint(final) == stat_fingerprint(current),
            'Manifest changed during validation')
    return raw


def parse_manifest(raw):
    """Parse the domain and version only, without executing component code."""
    try:
        manifest = json.loads(raw, object_pairs_hook=unique_object, parse_constant=invalid_constant)
    except (json.JSONDecodeError, UnicodeDecodeError, RecursionError) as exc:
        raise ValueError('Manifest is not valid UTF JSON') from exc
    require(type(manifest) is dict, 'Manifest must be a JSON object')
    domain = manifest.get('domain')
    require(isinstance(domain, str) and DOMAIN.fullmatch(domain), 'Manifest has an invalid domain')
    version = manifest.get('version', '')
    require(isinstance(version, str) and len(version) <= 128 and (not version or version.isprintable()),
            'Manifest has an invalid version')
    return {'domain': domain, 'version': version}


def read_child_manifest(root_fd, child):
    """Open a direct child without following links, then read its manifest."""
    listed = child.stat(follow_symlinks=False)
    require(not stat.S_ISLNK(listed.st_mode), 'Layout child contains a symlink')
    if not stat.S_ISDIR(listed.st_mode):
        return None
    child_fd = os.open(child.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                       dir_fd=root_fd)
    try:
        opened = os.fstat(child_fd)
        require((listed.st_dev, listed.st_ino) == (opened.st_dev, opened.st_ino),
                'Layout child changed during validation')
        raw = read_manifest_bytes(child_fd)
        return None if raw is None else parse_manifest(raw)
    finally:
        os.close(child_fd)


def scan_manifests(root: Path) -> dict:
    """Return domain -> {domain, version, path}; reject duplicates, including hidden copies.

    Only direct child manifests are read. Ordinary files and directories without
    a manifest are ignored. This is a layout check, not HA manifest-schema validation.
    """
    root = absolute_path(root)
    try:
        root_before_open = check_directory_ancestry(root)
        root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            root_opened = os.fstat(root_fd)
            require((root_before_open.st_dev, root_before_open.st_ino) ==
                    (root_opened.st_dev, root_opened.st_ino),
                    'Layout directory changed during validation')
            manifests_by_domain = {}
            with os.scandir(root_fd) as children:
                for count, child in enumerate(children, 1):
                    require(count <= MAX_CHILDREN, 'Layout has too many direct children')
                    # Hidden children count too: HA discovers their manifests.
                    manifest = read_child_manifest(root_fd, child)
                    if manifest is None:
                        continue
                    domain = manifest['domain']
                    require(domain not in manifests_by_domain, f'Duplicate manifest domain: {domain}')
                    manifests_by_domain[domain] = {**manifest, 'path': str(root / child.name)}
            root_after_scan = root.lstat()
            require(stat_fingerprint(root_after_scan) == stat_fingerprint(root_opened),
                    'Layout directory changed during validation')
            return manifests_by_domain
        finally:
            os.close(root_fd)
    except OSError as exc:
        raise ValueError('Layout cannot be read safely') from exc


def validate_layout(root: Path, *, expected_domain: str, expected_version: str,
                    expected_path: Path) -> dict:
    """Require one intended domain at its explicit root/domain path and version."""
    require(isinstance(expected_domain, str) and DOMAIN.fullmatch(expected_domain),
            'Expected domain is invalid')
    require(isinstance(expected_version, str) and 0 < len(expected_version) <= 128
            and expected_version.isprintable(), 'Expected version is invalid')
    root, expected_path = absolute_path(root), absolute_path(expected_path)
    require(expected_path == root / expected_domain,
            'Expected path must be the explicit custom_components/domain directory')
    # Scan the whole directory before choosing the target: any duplicate fails.
    manifests = scan_manifests(root)
    require(expected_domain in manifests, 'Expected integration manifest is missing')
    selected_manifest = manifests[expected_domain]
    require(selected_manifest['path'] == str(expected_path), 'Expected integration is at the wrong path')
    require(selected_manifest['version'] == expected_version, 'Expected integration version does not match')
    return {'ok': True, 'custom_components': str(root), **selected_manifest,
            'manifest_count': len(manifests)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--custom-components', required=True, type=Path,
                        help='Full offline layout to scan, including hidden children')
    parser.add_argument('--domain', required=True, help='Intended integration domain')
    parser.add_argument('--version', required=True, help='Required manifest version')
    parser.add_argument('--expected-path', required=True, type=Path,
                        help='Required component path: the scan directory followed by the domain')
    args = parser.parse_args()
    try:
        result = validate_layout(args.custom_components, expected_domain=args.domain,
                                 expected_version=args.version, expected_path=args.expected_path)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
