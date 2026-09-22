"""Offline synthetic fixtures; no runtime contents or downloads required."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
from check_osgeo4w_package_lock import BASE, HEADER, INDIVIDUAL, INVENTORY, LOCK, SOURCES, validate


class PackageLockTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='fhast package lock ')
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        names = list(INDIVIDUAL.values()) + ['example-' + str(i) for i in range(140)]
        self.data = dict(HEADER, packages=[])
        self.rows = ['INSTALLED.DB 2']
        for name in names:
            filename = name + '-1.0-1.tar.bz2'
            archive = 'packages-x86_64/' + filename
            path = 'x86_64/release/family/' + name + '/' + filename
            self.rows.append(name + ' ' + archive + ' 0')
            self.data['packages'].append(dict(name=name, installed_archive=archive,
                source_path=path, url=BASE + path, size=42,
                checksum={'algorithm': 'md5', 'value': 'a' * 32}))
        self.sources = {'schema_version': 1, 'components': []}
        for component, package in INDIVIDUAL.items():
            entry = next(p for p in self.data['packages'] if p['name'] == package)
            self.sources['components'].append(dict(name=component, package=package,
                status='verified', type='osgeo4w-package', filename=Path(entry['source_path']).name,
                url=entry['url'], checksum=dict(entry['checksum'], reference=BASE + 'md5sums')))
        self.entry = self.data['packages'][-1]

    def write(self):
        for name, content in [(LOCK, json.dumps(self.data)), (SOURCES, json.dumps(self.sources)),
                              (INVENTORY, '\n'.join(self.rows) + '\n')]:
            p = self.root / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding='utf-8')

    def check(self):
        self.write()
        return validate(self.root)

    def error(self, text):
        self.assertIn(text, '\n'.join(self.check()))

    def test_valid(self):
        self.assertEqual(self.check(), [])

    def test_missing_package(self):
        self.data['packages'].pop()
        self.error('exactly 143')

    def test_extra_package(self):
        self.data['packages'].append(copy.deepcopy(self.entry))
        self.error('exactly 143')

    def test_duplicate_package(self):
        self.data['packages'][-1] = copy.deepcopy(self.data['packages'][0])
        self.error('duplicate package')

    def test_same_count_wrong_set(self):
        self.entry['name'] = 'unrecorded'
        self.error('unknown package')

    def test_installed_archive(self):
        self.entry['installed_archive'] = 'packages-x86_64/wrong.tar.bz2'
        self.error('installed archive mismatch')

    def test_filename(self):
        self.entry['source_path'] = 'x86_64/release/family/wrong.tar.bz2'
        self.entry['url'] = BASE + self.entry['source_path']
        self.error('filename mismatch')

    def test_header(self):
        for key, value in [('lineage', 'OSGeo4W v2'), ('architecture', 'x86'),
                           ('schema_version', True), ('schema_version', 2),
                           ('artifact_base_url', BASE.replace('v1', 'v2')),
                           ('checksum_inventory_url', 'https://example.org/md5sums'),
                           ('installed_inventory', 'other.db')]:
            with self.subTest(key=key):
                self.data[key] = value
                self.error('invalid ' + key)
                self.data[key] = HEADER[key]

    def test_md5(self):
        for value in ['z' * 32, 'a' * 31, None]:
            self.entry['checksum']['value'] = value
            self.error('invalid MD5')
        self.entry['checksum'] = {'algorithm': 'sha256', 'value': 'a' * 64}
        self.error('invalid MD5')

    def test_size(self):
        for size in [0, -1, True, '42', 1.5, None]:
            with self.subTest(size=size):
                self.entry['size'] = size
                self.error('invalid size')

    def test_paths(self):
        for path in ['/x86_64/release/a.tar.bz2', 'x86/release/a.tar.bz2',
                     'x86_64/release/../a.tar.bz2', 'x86_64/release//a.tar.bz2',
                     'x86_64/release/a%2fb.tar.bz2', 'C:/a.tar.bz2',
                     'x86_64/release/a.tar.bz2?query', 'x86_64/release/a\\b.tar.bz2', None]:
            with self.subTest(path=path):
                self.entry['source_path'] = path
                self.error('invalid archive/source path')

    def test_url(self):
        self.entry['url'] = self.entry['url'].replace('/v1/', '/v2/')
        self.error('URL/source-path disagreement')

    def test_duplicate_path(self):
        self.entry['source_path'] = self.data['packages'][0]['source_path']
        self.error('duplicate source path')

    def test_order(self):
        self.data['packages'].reverse()
        self.error('order differs')

    def test_individual_disagreement(self):
        for source in self.sources['components']:
            original = copy.deepcopy(source)
            for key, value in [('url', 'https://example.org/wrong'),
                               ('checksum', {'algorithm': 'md5', 'value': 'b' * 32}),
                               ('status', 'partial')]:
                source[key] = value
                self.error('disagreement with individually verified source')
                source.clear()
                source.update(copy.deepcopy(original))

    def test_missing_individual(self):
        self.sources['components'].pop()
        self.error('missing/duplicate runtime source')

    def test_required_and_unknown_fields(self):
        del self.entry['checksum']
        self.error('invalid package fields')
        self.data['surprise'] = True
        self.error('invalid lock top-level fields')

    def test_missing_input(self):
        self.write()
        (self.root / INVENTORY).unlink()
        self.assertTrue(validate(self.root))

    def test_bad_inventory(self):
        self.rows[-1] = self.rows[-2]
        self.error('duplicate installed package')
        self.rows[0] = 'INSTALLED.DB 99'
        self.error('unsupported installed.db header')

    def test_bad_json(self):
        self.write()
        for text in ['{', '{"schema_version":1,"schema_version":1}']:
            (self.root / LOCK).write_text(text)
            self.assertTrue(validate(self.root))

    def test_cli(self):
        self.write()
        script = self.root / 'FHAST/developer_scripts/check_osgeo4w_package_lock.py'
        script.parent.mkdir(parents=True)
        script.write_text(Path(__file__).with_name(script.name).read_text())
        for expected in [0, 1]:
            if expected:
                self.entry['size'] = 0
                self.write()
            result = subprocess.run([sys.executable, '-B', str(script)], capture_output=True, text=True)
            self.assertEqual(result.returncode, expected, result.stderr)
            self.assertNotIn('Traceback', result.stderr)


if __name__ == '__main__':
    unittest.main()
