import importlib.util
from contextlib import contextmanager
import json
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/validate_component_layout.py'
SPEC = importlib.util.spec_from_file_location('validate_component_layout', SCRIPT)
layout = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(layout)


class ComponentLayoutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='component-layout-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / 'custom_components'
        self.root.mkdir()
        self.component = self.write_component(self.root / 'cisco_phone_control')

    @staticmethod
    def write_component(path, *, domain='cisco_phone_control', version='0.5.0'):
        path.mkdir(parents=True)
        manifest = {'domain': domain, 'name': 'Synthetic component'}
        if version is not None:
            manifest['version'] = version
        (path / 'manifest.json').write_text(json.dumps(manifest))
        (path / '__init__.py').write_text('# Synthetic component; must never be imported.\n')
        return path

    def validate(self, **overrides):
        options = {'expected_domain': 'cisco_phone_control', 'expected_version': '0.5.0',
                   'expected_path': self.component}
        options.update(overrides)
        return layout.validate_layout(self.root, **options)

    def command(self):
        return [sys.executable, '-B', str(SCRIPT), '--custom-components', str(self.root),
                '--domain', 'cisco_phone_control', '--version', '0.5.0',
                '--expected-path', str(self.component)]

    def snapshot(self):
        return {str(path.relative_to(self.base)):
                (path.read_bytes(), stat.S_IMODE(path.stat().st_mode), path.stat().st_mtime_ns)
                for path in self.base.rglob('*') if path.is_file()}

    def test_valid_layout_reports_exact_domain_version_and_path(self):
        self.write_component(self.root / 'other_component', domain='other_component', version=None)
        result = self.validate()
        self.assertIs(result['ok'], True)
        self.assertEqual(result['custom_components'], str(self.root))
        self.assertEqual(result['domain'], 'cisco_phone_control')
        self.assertEqual(result['version'], '0.5.0')
        self.assertEqual(result['path'], str(self.component))
        self.assertEqual(result['manifest_count'], 2)

    def test_hidden_backup_with_duplicate_domain_is_rejected(self):
        self.write_component(self.root / '.cisco_phone_control.install-backup-synthetic', version='0.4.0')
        before = self.snapshot()
        with self.assertRaises(ValueError):
            self.validate()
        self.assertEqual(self.snapshot(), before)

    def test_visible_backup_with_duplicate_domain_is_rejected(self):
        self.write_component(self.root / 'previous_component', version='0.4.0')
        with self.assertRaises(ValueError):
            self.validate()

    def test_backups_and_staging_outside_custom_components_leave_valid_layout(self):
        self.write_component(self.base / '.component-backups/previous', version='0.4.0')
        self.write_component(self.base / '.component-staging/candidate')
        result = self.validate()
        self.assertEqual(result['manifest_count'], 1)
        self.assertEqual(result['path'], str(self.component))

    def test_expected_path_must_be_the_canonical_domain_directory(self):
        for path in (self.root / 'different_name', self.base / 'cisco_phone_control'):
            with self.subTest(path=path.name), self.assertRaises(ValueError):
                self.validate(expected_path=path)
        moved = self.root / 'renamed_component'
        self.component.rename(moved)
        with self.assertRaises(ValueError):
            self.validate(expected_path=moved)

    def test_explicit_wrong_version_is_rejected(self):
        with self.assertRaises(ValueError):
            self.validate(expected_version='0.4.0')
        manifest = self.component / 'manifest.json'
        manifest.write_text(json.dumps({'domain': 'cisco_phone_control'}))
        with self.assertRaises(ValueError):
            self.validate()

    def test_malformed_manifest_is_rejected(self):
        manifest = self.component / 'manifest.json'
        for raw in (b'{', b'', b'[]', b'null'):
            manifest.write_bytes(raw)
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                self.validate()

    def test_symlink_manifest_is_rejected_without_modifying_target(self):
        manifest = self.component / 'manifest.json'
        saved = self.base / 'saved-manifest.json'
        manifest.rename(saved)
        original = saved.read_bytes()
        manifest.symlink_to(saved)
        with self.assertRaises(ValueError):
            self.validate()
        self.assertEqual(saved.read_bytes(), original)

    def test_symlink_component_directory_is_rejected(self):
        saved = self.base / 'saved-component'
        self.component.rename(saved)
        self.component.symlink_to(saved, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.validate()

    def test_component_code_is_never_executed_and_all_bytes_are_unchanged(self):
        marker = self.base / 'component-executed'
        (self.component / '__init__.py').write_text(
            'from pathlib import Path\n'
            f'Path({str(marker)!r}).write_text("unexpected execution")\n'
            'raise RuntimeError("component code must not execute")\n')
        before = self.snapshot()
        self.assertTrue(self.validate()['ok'])
        result = subprocess.run(self.command(), capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)['ok'])
        self.assertFalse(marker.exists())
        self.assertFalse((self.component / '__pycache__').exists())
        self.assertEqual(self.snapshot(), before)

    def test_cli_requires_explicit_arguments_and_rejects_duplicate_layout(self):
        missing = subprocess.run([sys.executable, '-B', str(SCRIPT)],
                                 capture_output=True, text=True, timeout=10)
        self.assertNotEqual(missing.returncode, 0)
        self.write_component(self.root / '.retained-backup', version='0.4.0')
        invalid = subprocess.run(self.command(), capture_output=True, text=True, timeout=10)
        self.assertNotEqual(invalid.returncode, 0)
        self.assertNotIn('Traceback', invalid.stderr)

    def test_manifest_size_limit_accepts_boundary_and_rejects_one_extra_byte(self):
        manifest = self.component / 'manifest.json'
        raw = manifest.read_bytes()
        bounded = raw + b' ' * (256 * 1024 - len(raw))
        manifest.write_bytes(bounded)
        self.assertTrue(self.validate()['ok'])
        manifest.write_bytes(bounded + b' ')
        with self.assertRaises(ValueError):
            self.validate()

    def test_child_count_limit_includes_files_without_manifests(self):
        for index in range(1023):
            (self.root / f'ordinary-file-{index}').touch()
        self.assertTrue(self.validate()['ok'])
        (self.root / 'one-too-many').touch()
        with self.assertRaises(ValueError):
            self.validate()

    def test_duplicate_json_keys_are_rejected_at_top_level_and_nested(self):
        manifest = self.component / 'manifest.json'
        for raw in (
            b'{"domain":"cisco_phone_control","domain":"cisco_phone_control","version":"0.5.0"}',
            b'{"domain":"cisco_phone_control","version":"0.5.0","extra":{"key":1,"key":2}}',
        ):
            manifest.write_bytes(raw)
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                self.validate()

    def test_nonstandard_json_numbers_are_rejected_even_in_unused_fields(self):
        manifest = self.component / 'manifest.json'
        for number in ('NaN', 'Infinity', '-Infinity'):
            manifest.write_text('{"domain":"cisco_phone_control","version":"0.5.0","extra":' + number + '}')
            with self.subTest(number=number), self.assertRaises(ValueError):
                self.validate()

    def test_manifest_replaced_between_stat_and_open_is_rejected(self):
        manifest = self.component / 'manifest.json'
        replacement = self.base / 'replacement.json'
        replacement.write_bytes(manifest.read_bytes())
        original_open = layout.os.open
        replacements = []

        def replace_before_open(path, flags, *args, **kwargs):
            if path == 'manifest.json' and not replacements:
                replacement.replace(manifest)
                replacements.append(path)
            return original_open(path, flags, *args, **kwargs)

        with mock.patch.object(layout.os, 'open', side_effect=replace_before_open), self.assertRaises(ValueError):
            self.validate()
        self.assertEqual(len(replacements), 1)
        self.assertFalse(replacement.exists())
        self.assertEqual(json.loads(manifest.read_bytes())['version'], '0.5.0')

    def test_manifest_replaced_after_open_is_rejected(self):
        manifest = self.component / 'manifest.json'
        replacement = self.base / 'replacement.json'
        replacement.write_bytes(manifest.read_bytes())
        original_fstat = layout.os.fstat
        file_observations = []

        def replace_during_read(fd):
            info = original_fstat(fd)
            if stat.S_ISREG(info.st_mode):
                file_observations.append(fd)
                if len(file_observations) == 2:
                    replacement.replace(manifest)
            return info

        with mock.patch.object(layout.os, 'fstat', side_effect=replace_during_read), self.assertRaises(ValueError):
            self.validate()
        self.assertEqual(len(file_observations), 2)
        self.assertFalse(replacement.exists())

    def test_child_added_during_scan_is_rejected(self):
        original_scandir = layout.os.scandir

        @contextmanager
        def changed_scan(path):
            with original_scandir(path) as entries:
                yield entries
            (self.root / 'concurrent-child').touch()

        with mock.patch.object(layout.os, 'scandir', side_effect=changed_scan), self.assertRaises(ValueError):
            self.validate()
        self.assertTrue((self.root / 'concurrent-child').exists())


if __name__ == '__main__':
    unittest.main()
