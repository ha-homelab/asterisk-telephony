import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('installer', ROOT / 'scripts/stage_integration.py')
installer = importlib.util.module_from_spec(spec); spec.loader.exec_module(installer)

def archive(extra=None, version='1.0.5'):
    stream = io.BytesIO(); prefix = 'fixture/custom_components/asterisk/'
    with zipfile.ZipFile(stream, 'w') as z:
        z.writestr(prefix+'__init__.py', '# Synthetic test fixture. Not upstream code.\n')
        z.writestr(prefix+'manifest.json', json.dumps({'domain':'asterisk','version':version}))
        z.writestr(prefix+'services.yaml', 'send_action: {}\n')
        if extra:
            z.writestr(extra[0], extra[1])
    data = stream.getvalue()
    lock = {'archive_bytes':len(data),'sha256':hashlib.sha256(data).hexdigest(),'source_prefix':prefix,'version':'1.0.5'}
    return data, lock

class InstallerTests(unittest.TestCase):
    def test_verifies_and_stages_without_installing(self):
        data, lock = archive()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve(); config = root/'config'; config.mkdir(); (config/'configuration.yaml').write_text('{}')
            staged = installer.stage(installer.verified_files(data, lock), root/'stage')
            self.assertTrue((staged/'manifest.json').exists())
            self.assertFalse(installer.install(staged, config))
            self.assertFalse((config/'custom_components').exists())
    def test_explicit_apply_installs_and_refuses_overwrite(self):
        data, lock = archive()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve(); config = root/'config'; config.mkdir(); (config/'configuration.yaml').write_text('{}')
            staged = installer.stage(installer.verified_files(data, lock), root/'stage')
            self.assertTrue(installer.install(staged, config, apply=True))
            with self.assertRaises(ValueError): installer.install(staged, config, apply=True)
    def test_wrong_hash_fails(self):
        data, lock = archive(); lock['sha256'] = '0'*64
        with self.assertRaisesRegex(ValueError,'SHA-256'): installer.verified_files(data, lock)
    def test_wrong_version_fails(self):
        data, lock = archive(version='9.9.9')
        with self.assertRaisesRegex(ValueError,'version'): installer.verified_files(data, lock)
    def test_path_traversal_fails(self):
        for path in ('../escape','/absolute','fixture\\evil', 'fixture/custom_components/asterisk/../../evil'):
            data, lock = archive((path, 'bad'))
            with self.assertRaisesRegex(ValueError,'path'): installer.verified_files(data, lock)
    def test_symlink_archive_fails(self):
        link = zipfile.ZipInfo('fixture/custom_components/asterisk/link'); link.create_system = 3; link.external_attr = 0o120777 << 16
        data, lock = archive((link, '/tmp/escape'))
        with self.assertRaisesRegex(ValueError,'Symlinks'): installer.verified_files(data, lock)
    def test_symlink_install_target_fails(self):
        data, lock = archive()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve(); config = root/'config'; config.mkdir(); (config/'configuration.yaml').write_text('{}')
            (config/'custom_components').symlink_to(root/'elsewhere', target_is_directory=True)
            staged = installer.stage(installer.verified_files(data, lock), root/'stage')
            with self.assertRaisesRegex(ValueError,'symlink'): installer.install(staged, config, True)
    def test_untrusted_download_origin_fails_before_network(self):
        with self.assertRaisesRegex(ValueError,'trusted'):
            installer.download({'url':'http://example.com/code.zip'})
    def test_symlink_custom_components_cannot_escape_staging(self):
        data, lock = archive()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve(); stage_root = root/'stage'; stage_root.mkdir()
            outside = root/'outside'; outside.mkdir()
            (stage_root/'custom_components').symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, 'symlink'):
                installer.stage(installer.verified_files(data, lock), stage_root)
            self.assertEqual(list(outside.iterdir()), [])
    def test_missing_config_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError): installer.install(Path(tmp).resolve()/'stage', tmp, True)

if __name__ == '__main__': unittest.main()
