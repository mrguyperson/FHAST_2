"""Synthetic acquisition metadata tests; no network or runtime dependencies."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
from check_runtime_sources import INVENTORY, MANIFEST, SOURCES, validate


class RuntimeSourcesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='fhast sources ')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'build').mkdir()
        self.manifest = {'schema_version': 1, 'platform': 'windows',
                         'components': [{'name': 'example', 'version': '1.2'}]}
        self.entry = {'name': 'example', 'version': '1.2', 'status': 'verified',
                      'type': 'artifact', 'filename': 'example.zip',
                      'url': 'https://example.org/example.zip',
                      'checksum': {'algorithm': 'sha256', 'value': 'a' * 64,
                                   'reference': 'https://example.org/checksums'},
                      'evidence': [{'reference': 'https://example.org/releases',
                                    'detail': 'Synthetic official release record.'}]}
        self.data = {'schema_version': 1, 'components': [self.entry]}

    def check(self):
        (self.root / MANIFEST).write_text(json.dumps(self.manifest), encoding='utf-8')
        (self.root / SOURCES).write_text(json.dumps(self.data), encoding='utf-8')
        return validate(self.root)

    def error(self, message):
        self.assertIn(message, '\n'.join(self.check()))

    def test_verified(self):
        self.assertEqual(self.check(), [])

    def test_partial(self):
        self.entry.update(status='partial', unresolved=['Checksum unavailable.'])
        del self.entry['checksum']
        self.assertEqual(self.check(), [])

    def test_unresolved_unknown_version(self):
        self.manifest['components'][0]['version'] = None
        self.entry.update(version=None, status='unresolved', type='unknown',
                          unresolved=['Installed version unknown.'])
        for key in ['filename', 'url', 'checksum']:
            del self.entry[key]
        self.assertEqual(self.check(), [])

    def test_unknown_component(self):
        self.entry['name'] = 'missing'
        self.error('unknown component')

    def test_duplicate_component(self):
        self.data['components'].append(copy.deepcopy(self.entry))
        self.error('duplicate component')

    def test_missing_component(self):
        self.manifest['components'].append({'name': 'other', 'version': None})
        self.error('cover every')

    def test_version_disagreement(self):
        self.entry['version'] = '2.0'
        self.error('version disagrees')

    def test_malformed_checksum(self):
        for value in ['z' * 64, 'a' * 63, None]:
            with self.subTest(value=value):
                self.entry['checksum']['value'] = value
                self.error('malformed checksum')

    def test_unsupported_checksum(self):
        self.entry['checksum']['algorithm'] = 'lfs-oid'
        self.error('unsupported checksum')

    def test_checksum_provenance_required(self):
        del self.entry['checksum']['reference']
        self.error('malformed checksum')

    def test_missing_acquisition_fields(self):
        original = copy.deepcopy(self.entry)
        for field in ['filename', 'url', 'checksum']:
            with self.subTest(field=field):
                self.entry.clear()
                self.entry.update(copy.deepcopy(original))
                del self.entry[field]
                self.assertTrue(self.check())

    def test_incomplete_requires_reasons(self):
        for status in ['partial', 'unresolved']:
            self.entry['status'] = status
            self.error('explicit unresolved reasons')

    def test_verified_cannot_have_gaps(self):
        self.entry['unresolved'] = ['Not yet established.']
        self.error('no unresolved gaps')

    def test_status_and_type(self):
        self.entry['status'] = 'maybe'
        self.error('unsupported status')
        self.entry['status'] = 'verified'
        self.entry['type'] = 'installer-magic'
        self.error('unsupported acquisition type')

    def child(self):
        self.manifest['components'].append({'name': 'child', 'version': '3'})
        child = {'name': 'child', 'version': '3', 'status': 'verified',
                 'type': 'bundled', 'parent': 'example', 'evidence': self.entry['evidence']}
        self.data['components'].append(child)
        return child

    def test_valid_bundled(self):
        self.child()
        self.assertEqual(self.check(), [])

    def test_invalid_parent(self):
        child = self.child()
        for parent in ['missing', 'child']:
            child['parent'] = parent
            self.error('invalid bundled parent')

    def test_bundled_cycle(self):
        child = self.child()
        for key in ['filename', 'url', 'checksum']:
            del self.entry[key]
        self.entry.update(type='bundled', parent='child')
        self.error('cycle')

    def test_verified_child_requires_verified_parent(self):
        self.child()
        self.entry.update(status='partial', unresolved=['Not yet matched.'])
        self.error('requires verified parent')

    def test_package_inventory(self):
        path = self.root / INVENTORY
        path.parent.mkdir(parents=True)
        path.write_text('INSTALLED.DB 2\nexample packages-x86_64/example-1.2.tar.bz2 0\n')
        self.entry.update(type='osgeo4w-package', package='example', filename='example-1.2.tar.bz2')
        self.manifest['components'][0]['version_source'] = {
            'kind': 'osgeo4w-package', 'package': 'example'}
        self.assertEqual(self.check(), [])
        self.entry['filename'] = 'example-2.0.tar.bz2'
        self.error('disagrees with installed.db')
        path.unlink()
        self.assertTrue(self.check())

    def test_package_set(self):
        path = self.root / INVENTORY
        path.parent.mkdir(parents=True)
        path.write_text('INSTALLED.DB 2\nexample packages-x86_64/example-1.2.tar.bz2 0\n')
        for key in ['filename', 'url', 'checksum']:
            del self.entry[key]
        self.entry.update(type='package-set', inventory=INVENTORY, status='partial',
                          unresolved=['Historical dependency index missing.'])
        self.assertEqual(self.check(), [])
        self.entry['inventory'] = '../elsewhere'
        self.error('unsupported package inventory')

    def test_bad_schema_or_fields(self):
        for version in [2, True, '1']:
            self.data['schema_version'] = version
            self.error('unsupported source schema')
        self.data['schema_version'] = 1
        self.entry['typo'] = 'value'
        self.error('unsupported fields')

    def test_missing_required_field(self):
        del self.entry['evidence']
        self.error('missing required fields')

    def test_bad_url_and_evidence(self):
        self.entry['url'] = 'file:///tmp/example'
        self.error('HTTPS URL')
        self.entry['url'] = 'https://example.org/example.zip'
        self.entry['evidence'] = []
        self.error('evidence must be nonempty')

    def test_duplicate_json_key_and_malformed_json(self):
        self.check()
        for text in ['{', '{"schema_version": 1, "schema_version": 1, "components": []}']:
            (self.root / SOURCES).write_text(text)
            self.assertTrue(validate(self.root))

    def test_cli_failure(self):
        self.check()
        script = self.root / 'FHAST/developer_scripts/check_runtime_sources.py'
        script.parent.mkdir(parents=True)
        script.write_text(Path(__file__).with_name('check_runtime_sources.py').read_text())
        self.entry['version'] = '9'
        self.check()
        result = subprocess.run([sys.executable, str(script)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn('version disagrees', result.stderr)
        self.assertNotIn('Traceback', result.stderr)


if __name__ == '__main__':
    unittest.main()
