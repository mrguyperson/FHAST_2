"""Synthetic, standard-library tests; no bundled runtime is launched or needed.

Run: python3 FHAST/developer_scripts/test_runtime_manifest.py
"""

import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

sys.dont_write_bytecode = True
from check_runtime_manifest import MANIFEST, validate


class RuntimeManifestTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='fhast-manifest-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / 'runtime with spaces').mkdir()
        (self.root / 'metadata.txt').write_text('Version: 1.2.3\n', encoding='utf-8')
        self.manifest = self.root / MANIFEST
        self.manifest.parent.mkdir()
        self.component = {
            'name': 'example', 'version': '1.2.3', 'path': 'runtime with spaces',
            'version_source': {'kind': 'text-field', 'path': 'metadata.txt',
                               'key': 'Version', 'separator': ':'},
        }
        self.data = {'schema_version': 1, 'platform': 'windows',
                     'components': [self.component]}

    def check(self):
        self.manifest.write_text(json.dumps(self.data), encoding='utf-8')
        return validate(self.root)

    def assert_error(self, fragment):
        self.assertIn(fragment, '\n'.join(self.check()))

    def test_valid_manifest(self):
        self.assertEqual(self.check(), [])

    def test_missing_runtime_directory(self):
        self.component['path'] = 'missing'
        self.assert_error('missing runtime directory: missing')

    def test_file_is_not_runtime_directory(self):
        self.component['path'] = 'metadata.txt'
        self.assert_error('missing runtime directory')

    def test_missing_source(self):
        self.component['version_source']['path'] = 'missing.txt'
        self.assert_error('missing version-source file')

    def test_version_mismatch(self):
        self.component['version'] = '9.9'
        self.assert_error("declared '9.9', metadata '1.2.3'")

    def test_unknown_version_and_lfs_pointer(self):
        (self.root / 'metadata.txt').write_text('version https://git-lfs.github.com/spec/v1\n')
        self.component.update(version=None, notes='No readable version metadata.',
                              version_source={'kind': 'unknown', 'path': 'metadata.txt'})
        self.assertEqual(self.check(), [])
        self.component['version'] = '1.2.3'
        self.assert_error('unknown version requires null')
        self.component['version'] = None
        del self.component['notes']
        self.assert_error('explanatory notes')

    def test_lfs_pointer_cannot_verify_version(self):
        (self.root / 'metadata.txt').write_text('version https://git-lfs.github.com/spec/v1\n')
        self.assert_error('version metadata is an LFS pointer')

    def test_directory_name_is_not_version_evidence(self):
        (self.root / 'runtime-9.9').mkdir()
        self.component.update(path='runtime-9.9', version='9.9')
        self.assert_error('version mismatch')
        self.component['version_source']['kind'] = 'filename'
        self.assert_error('unsupported version_source kind')

    def test_osgeo4w_package_revision(self):
        (self.root / 'metadata.txt').write_text(
            'INSTALLED.DB 2\npython3-core packages-x86_64/python3-core-3.7.0-4.tar.bz2 0\n')
        self.component.update(version='3.7.0-4', version_source={
            'kind': 'osgeo4w-package', 'path': 'metadata.txt', 'package': 'python3-core'})
        self.assertEqual(self.check(), [])
        self.component['version_source']['package'] = 'missing-package'
        self.assert_error('expected one package record')

    def test_quoted_java_release_field(self):
        (self.root / 'metadata.txt').write_text('JAVA_RUNTIME_VERSION="11.0.20+9-LTS-256"\n')
        self.component.update(version='11.0.20+9-LTS-256', version_source={
            'kind': 'text-field', 'path': 'metadata.txt', 'key': 'JAVA_RUNTIME_VERSION', 'separator': '='})
        self.assertEqual(self.check(), [])

    def test_netlogo_ini(self):
        (self.root / 'metadata.txt').write_text(
            '[Application]\napp.version=6.2.2\napp.runtime=$APPDIR\\runtime\n'
            '[JVMOptions]\n-Xmx8192m\n-XX:+UseParallelGC\n-Dfile.encoding=UTF-8\n')
        self.component.update(version='6.2.2', version_source={
            'kind': 'ini', 'path': 'metadata.txt', 'section': 'Application', 'key': 'app.version'})
        self.assertEqual(self.check(), [])

    def test_jar_main_section_and_continuation(self):
        with zipfile.ZipFile(self.root / 'runtime.jar', 'w') as archive:
            archive.writestr('META-INF/MANIFEST.MF',
                            'Manifest-Version: 1.0\r\nCreated-By: 99.0\r\n'
                            'Implementation-Version: 1.8.0_\r\n 275\r\n\r\n'
                            'Name: other.class\r\nImplementation-Version: 88.0\r\n')
        self.component.update(version='1.8.0_275', version_source={
            'kind': 'jar-manifest', 'path': 'runtime.jar', 'key': 'Implementation-Version'})
        self.assertEqual(self.check(), [])

    def test_bad_archive(self):
        self.component['version_source'] = {
            'kind': 'jar-manifest', 'path': 'metadata.txt', 'key': 'Implementation-Version'}
        self.assert_error('not a zip file')

    def test_ambiguous_metadata_field(self):
        (self.root / 'metadata.txt').write_text('Version: 1.2.3\nVersion: 9.9\n')
        self.assert_error('expected exactly one')

    def test_malformed_json_and_duplicate_keys(self):
        for content in ('{', '{"schema_version": 1, "schema_version": 1}'):
            with self.subTest(content=content):
                self.manifest.write_text(content)
                self.assertTrue(validate(self.root))

    def test_missing_and_unsupported_fields(self):
        original = copy.deepcopy(self.data)
        variants = [None, [], {}, {**original, 'schema_version': 2},
                    {**original, 'schema_version': True}, {**original, 'platform': 'linux'},
                    {**original, 'components': []}, {**original, 'extra': 1}]
        for field in self.component:
            component = dict(self.component)
            del component[field]
            variants.append({**original, 'components': [component]})
        variants.extend({**original, 'components': [component]} for component in (
            None, {**self.component, 'version': None}, {**self.component, 'name': 1},
            {**self.component, 'version_source': {'kind': []}},
            {**self.component, 'unexpected': True},
        ))
        for data in variants:
            with self.subTest(data=data):
                self.data = data
                self.assertTrue(self.check())

    def test_duplicate_components(self):
        self.data['components'].append(dict(self.component))
        self.assert_error('duplicate component name')

    def test_unsafe_paths(self):
        for path in ('../escape', '/absolute', 'C:/Windows', r'runtime\file'):
            for target in (self.component, self.component['version_source']):
                with self.subTest(path=path, target=target):
                    original = target['path']
                    target['path'] = path
                    self.assert_error('bundle-relative')
                    target['path'] = original

    def test_cli_exit_codes(self):
        checker = self.root / 'FHAST/developer_scripts/check_runtime_manifest.py'
        checker.parent.mkdir(parents=True)
        checker.write_bytes(Path(__file__).with_name('check_runtime_manifest.py').read_bytes())
        for version, expected in (('1.2.3', 0), ('wrong', 1)):
            self.component['version'] = version
            self.check()
            result = subprocess.run([sys.executable, str(checker)], cwd=self.root,
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
            if expected:
                self.assertIn('example: version mismatch', result.stderr)


if __name__ == '__main__':
    unittest.main(verbosity=2)
