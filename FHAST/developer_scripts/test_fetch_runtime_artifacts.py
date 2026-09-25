"""Offline fetch tests with in-memory responses; no runtime downloads."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.dont_write_bytecode = True
import fetch_runtime_artifacts as fetcher
from check_runtime_sources import MANIFEST, SOURCES
from check_osgeo4w_package_lock import BASE, HEADER, INDIVIDUAL, INVENTORY, LOCK


class FetchTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='fhast fetch spaces ')
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.root = self.base / 'repo'
        (self.root / 'build').mkdir(parents=True)
        self.output = self.base / 'output with spaces'
        self.payload = b'synthetic artifact\x00\xff'
        self.entries = []
        for name, algorithm in [('one', 'sha256'), ('two', 'md5')]:
            self.entries.append(dict(name=name, version='1', status='verified', type='artifact',
                filename=name + '.zip', url='https://example.org/' + name,
                checksum=dict(algorithm=algorithm, value=hashlib.new(algorithm, self.payload).hexdigest(),
                              reference='https://example.org/checksums'),
                evidence=[dict(reference='https://example.org/releases', detail='Synthetic fixture')]))
        self.opener = Mock(side_effect=lambda url: io.BytesIO(self.payload))
        self.write()

    def write(self):
        (self.root / MANIFEST).write_text(json.dumps(dict(schema_version=1, platform='windows',
            components=[dict(name=e['name'], version=e['version']) for e in self.entries])))
        (self.root / SOURCES).write_text(json.dumps(dict(schema_version=1, components=self.entries)))

    def run_fetch(self, **kwargs):
        with contextlib.redirect_stdout(io.StringIO()):
            fetcher.fetch(self.output, root=self.root, opener=self.opener, **kwargs)

    def test_all_sha256_md5_and_spaces(self):
        self.run_fetch()
        self.assertEqual(self.opener.call_count, 2)
        self.assertEqual({p.name for p in self.output.iterdir()}, {'one.zip', 'two.zip'})
        self.assertEqual((self.output / 'one.zip').read_bytes(), self.payload)

    def test_network_boundary_user_agent(self):
        url = self.entries[0]['url']
        with patch.object(fetcher, 'build_opener') as build:
            response = fetcher.open_download(url)
            self.assertIs(response, build.return_value.open.return_value)
            self.assertIsInstance(build.call_args.args[0], fetcher.HTTPSRedirects)
            request = build.return_value.open.call_args.args[0]
            self.assertEqual(request.full_url, url)
            self.assertEqual(request.get_header('User-agent'),
                'FHAST-runtime-fetcher/1.0 (+https://github.com/mrguyperson/FHAST_2)')
            self.assertEqual(build.return_value.open.call_args.kwargs, {'timeout': 60})
            with self.assertRaises(ValueError):
                fetcher.open_download('http://example.org/artifact')
            build.assert_called_once()

    def test_repository_netlogo_selection_and_dry_run(self):
        selected = fetcher.select(fetcher.ROOT, 'netlogo')
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]['filename'], 'NetLogo-6.2.2-64.msi')
        names = {entry['name'] for entry in fetcher.select(fetcher.ROOT)}
        self.assertIn('netlogo', names)
        self.assertNotIn('netlogo-jre', names)
        with self.assertRaisesRegex(ValueError, 'Not a standalone'):
            fetcher.select(fetcher.ROOT, 'netlogo-jre')
        with contextlib.redirect_stdout(io.StringIO()):
            fetcher.fetch(self.output, 'netlogo', dry_run=True, opener=self.opener)
        self.opener.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_selection(self):
        self.run_fetch(component='two')
        self.assertEqual(self.opener.call_count, 1)
        self.assertFalse((self.output / 'one.zip').exists())

    def test_reuse(self):
        self.run_fetch()
        self.opener.reset_mock()
        self.run_fetch()
        self.opener.assert_not_called()

    def test_invalid_existing_preserved(self):
        self.output.mkdir()
        target = self.output / 'one.zip'
        target.write_bytes(b'wrong')
        with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
            self.run_fetch()
        self.assertEqual(target.read_bytes(), b'wrong')
        self.opener.assert_not_called()

    def test_mismatch_cleanup(self):
        self.opener.side_effect = lambda url: io.BytesIO(b'wrong')
        with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
            self.run_fetch()
        self.assertEqual(list(self.output.iterdir()), [])

    def test_network_failure_cleanup(self):
        self.opener.side_effect = OSError('connection failed')
        with self.assertRaisesRegex(OSError, 'connection failed'):
            self.run_fetch()
        self.assertEqual(list(self.output.iterdir()), [])

    def test_interrupted_stream_cleanup(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.side_effect = [b'partial', OSError('interrupted')]
        self.opener.side_effect = None
        self.opener.return_value = response
        with self.assertRaisesRegex(OSError, 'interrupted'):
            self.run_fetch()
        self.assertEqual(list(self.output.iterdir()), [])

    def test_partial_and_unresolved_blocked(self):
        for status in ['partial', 'unresolved']:
            self.entries[0].update(status=status, unresolved=['Evidence incomplete.'])
            self.write()
            with self.assertRaisesRegex(ValueError, 'status=' + status):
                self.run_fetch(component='one')
            self.opener.assert_not_called()
        self.run_fetch()
        self.assertEqual(self.opener.call_count, 1)
        self.assertFalse((self.output / 'one.zip').exists())

    def test_missing_fields(self):
        for field in ['url', 'checksum']:
            value = self.entries[0].pop(field)
            self.write()
            with self.assertRaisesRegex(ValueError, 'Invalid runtime source metadata'):
                self.run_fetch()
            self.entries[0][field] = value
        self.opener.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_malformed(self):
        (self.root / SOURCES).write_text('{')
        with self.assertRaisesRegex(ValueError, 'Invalid runtime source metadata'):
            self.run_fetch()
        self.opener.assert_not_called()

    def test_unknown(self):
        with self.assertRaisesRegex(ValueError, 'Unknown component'):
            self.run_fetch(component='absent')

    def test_dry_run(self):
        self.run_fetch(dry_run=True)
        self.assertFalse(self.output.exists())
        self.opener.assert_not_called()

    def test_repository_output_blocked(self):
        self.output = self.root / 'cache'
        with self.assertRaisesRegex(ValueError, 'outside the repository'):
            self.run_fetch()
        self.assertFalse(self.output.exists())

    def test_filename_collision(self):
        self.entries[1]['filename'] = 'ONE.ZIP'
        self.write()
        with self.assertRaisesRegex(ValueError, 'Duplicate artifact filename'):
            self.run_fetch()

    def test_bundled_child_blocked(self):
        self.entries[1] = {k: v for k, v in self.entries[1].items() if k not in ['filename', 'url', 'checksum']}
        self.entries[1].update(type='bundled', parent='one')
        self.write()
        with self.assertRaisesRegex(ValueError, 'Not a standalone'):
            self.run_fetch(component='two')

    def test_cli_exit_codes(self):
        with patch.object(fetcher, 'fetch') as operation:
            self.assertEqual(fetcher.main(['--output-dir', str(self.output)]), 0)
            operation.assert_called_once_with(self.output, None, False, package_set=None)
            operation.side_effect = ValueError('checksum mismatch')
            with contextlib.redirect_stderr(io.StringIO()) as error:
                self.assertEqual(fetcher.main(['--output-dir', str(self.output)]), 1)
            self.assertIn('checksum mismatch', error.getvalue())

    def test_concurrent_file_not_overwritten(self):
        def response(url):
            (self.output / 'one.zip').write_bytes(b'concurrent file')
            return io.BytesIO(self.payload)
        self.opener.side_effect = response
        with self.assertRaises(FileExistsError):
            self.run_fetch(component='one')
        self.assertEqual((self.output / 'one.zip').read_bytes(), b'concurrent file')
        self.assertEqual(len(list(self.output.iterdir())), 1)



class PackageSetTest(unittest.TestCase):
    write = FetchTest.write
    run_fetch = FetchTest.run_fetch

    def setUp(self):
        FetchTest.setUp(self)
        self.packages = []
        names = list(INDIVIDUAL.values()) + ['sample-' + str(i) for i in range(140)]
        for name in names:
            filename = name + '-1.tar.bz2'
            path = 'x86_64/release/family/' + name + '/' + filename
            self.packages.append(dict(name=name, installed_archive='packages-x86_64/' + filename,
                source_path=path, url=BASE + path, size=len(self.payload),
                checksum=dict(algorithm='md5', value=hashlib.md5(self.payload).hexdigest())))
        for component, package in INDIVIDUAL.items():
            p = next(p for p in self.packages if p['name'] == package)
            self.entries.append(dict(name=component, version='1', status='verified',
                type='osgeo4w-package', package=package, filename=Path(p['source_path']).name,
                url=p['url'], checksum=dict(p['checksum'], reference=BASE + 'md5sums'),
                evidence=[dict(reference=BASE + 'md5sums', detail='Synthetic fixture')]))
        self.write()
        manifest = json.loads((self.root / MANIFEST).read_text())
        for c in manifest['components']:
            if c['name'] in INDIVIDUAL:
                c['version_source'] = dict(kind='osgeo4w-package', package=INDIVIDUAL[c['name']])
        (self.root / MANIFEST).write_text(json.dumps(manifest))
        self.save_lock()

    def save_lock(self):
        (self.root / LOCK).write_text(json.dumps(dict(HEADER, packages=self.packages)))
        inventory = self.root / INVENTORY
        inventory.parent.mkdir(parents=True, exist_ok=True)
        inventory.write_text('INSTALLED.DB 2\n' + ''.join(
            p['name'] + ' ' + p['installed_archive'] + ' 0\n' for p in self.packages))

    def test_selection_and_download_all(self):
        selected = fetcher.select(self.root, package_set='osgeo4w-v1')
        self.assertEqual(len(selected), 143)
        self.assertEqual([p['name'] for p in selected], [p['name'] for p in self.packages])
        self.run_fetch(package_set='osgeo4w-v1')
        self.assertEqual([c.args[0] for c in self.opener.call_args_list], [p['url'] for p in self.packages])
        self.assertEqual({p.name for p in self.output.iterdir()}, {p['filename'] for p in selected})
        self.assertTrue(all(p.read_bytes() == self.payload for p in self.output.iterdir()))
        self.opener.reset_mock()
        self.run_fetch(package_set='osgeo4w-v1')
        self.opener.assert_not_called()

    def test_lock_checksum_used(self):
        self.opener.side_effect = lambda url: io.BytesIO(b'x' * len(self.payload))
        with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
            self.run_fetch(package_set='osgeo4w-v1')
        self.assertEqual(list(self.output.iterdir()), [])

    def test_dry_run(self):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            fetcher.fetch(self.output, package_set='osgeo4w-v1', dry_run=True,
                          root=self.root, opener=self.opener)
        self.assertEqual(len(out.getvalue().splitlines()), 143)
        self.assertFalse(self.output.exists())
        self.opener.assert_not_called()

    def test_invalid_lock_blocks_activity(self):
        self.packages.pop()
        self.save_lock()
        for dry_run in [False, True]:
            with self.assertRaisesRegex(ValueError, 'Invalid OSGeo4W package lock'):
                self.run_fetch(package_set='osgeo4w-v1', dry_run=dry_run)
        self.opener.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_new_size_mismatch_cleanup(self):
        self.opener.side_effect = lambda url: io.BytesIO(b'short')
        with self.assertRaisesRegex(ValueError, 'size mismatch'):
            self.run_fetch(package_set='osgeo4w-v1')
        self.assertEqual(list(self.output.iterdir()), [])

    def test_existing_size_mismatch_preserved(self):
        self.output.mkdir()
        target = self.output / Path(self.packages[0]['source_path']).name
        target.write_bytes(b'short')
        with self.assertRaisesRegex(ValueError, 'size mismatch'):
            self.run_fetch(package_set='osgeo4w-v1')
        self.assertEqual(target.read_bytes(), b'short')
        self.opener.assert_not_called()

    def test_flat_filename_collisions(self):
        first = Path(self.packages[-2]['source_path']).name
        for filename in [first, first.upper()]:
            p = self.packages[-1]
            # Keep the extension lowercase to satisfy the lock's archive suffix rule.
            filename = filename[:-8] + '.tar.bz2'
            p.update(installed_archive='packages-x86_64/' + filename,
                     source_path='x86_64/release/other/' + filename)
            p['url'] = BASE + p['source_path']
            self.save_lock()
            with self.assertRaisesRegex(ValueError, 'Duplicate artifact filename'):
                self.run_fetch(package_set='osgeo4w-v1')
        self.assertFalse(self.output.exists())
        self.opener.assert_not_called()

    def test_windows_reserved_filename(self):
        p = self.packages[-1]
        p.update(installed_archive='packages-x86_64/CON.tar.bz2',
                 source_path='x86_64/release/other/CON.tar.bz2')
        p['url'] = BASE + p['source_path']
        self.save_lock()
        with self.assertRaisesRegex(ValueError, 'Unsafe artifact filename'):
            self.run_fetch(package_set='osgeo4w-v1')
        self.opener.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_default_does_not_select_lock(self):
        # Standalone selection remains independent even when the lock is malformed.
        (self.root / LOCK).write_text('{')
        selected = fetcher.select(self.root)
        self.assertEqual([p['name'] for p in selected], ['one', 'two', 'qgis', 'qgis-python', 'qt'])
        self.assertEqual(len(fetcher.select(self.root, 'qgis')), 1)

    def test_cli_mutual_exclusion(self):
        with patch.object(fetcher, 'fetch') as operation, contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                fetcher.main(['--output-dir', str(self.output), '--component', 'qgis',
                              '--package-set', 'osgeo4w-v1'])
            self.assertEqual(error.exception.code, 2)
            operation.assert_not_called()
        with self.assertRaisesRegex(ValueError, 'mutually exclusive'):
            self.run_fetch(component='qgis', package_set='osgeo4w-v1')

    def test_cli_package_set_forwarded(self):
        with patch.object(fetcher, 'fetch') as operation:
            self.assertEqual(fetcher.main(['--output-dir', str(self.output),
                '--package-set', 'osgeo4w-v1', '--dry-run']), 0)
            operation.assert_called_once_with(self.output, None, True, package_set='osgeo4w-v1')

if __name__ == '__main__':
    unittest.main()
