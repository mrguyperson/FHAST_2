"""Offline extraction tests. Installers are generated as data and never run."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
import extract_runtime_artifacts as extraction
import fetch_runtime_artifacts as fetcher
from check_osgeo4w_package_lock import LOCK, SOURCES, INVENTORY, INDIVIDUAL
from check_runtime_sources import MANIFEST

FIXTURES = Path(__file__).parent / 'tests/fixtures/runtime-extraction'


class Fixture(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='FHAST extraction spaces ')
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.root = self.base / 'repository'
        (self.root / 'build').mkdir(parents=True)
        (self.root / 'etc/setup').mkdir(parents=True)
        self.cache = self.base / 'input cache'
        self.cache.mkdir()
        self.output = self.base / 'output tree'
        self.entries = []
        for boundary in ('socket.socket.connect', 'socket.create_connection',
                         'urllib.request.urlopen', 'fetch_runtime_artifacts.open_download'):
            guard = patch(boundary, side_effect=AssertionError('Network forbidden'))
            guard.start()
            self.addCleanup(guard.stop)

    def register(self, data, name='qgis', filename='qgis-ltr-1.tar.bz2'):
        package = name == 'qgis'
        entry = dict(name=name, version='1', status='verified',
                     type='osgeo4w-package' if package else 'artifact', filename=filename,
                     url='https://example.org/' + filename,
                     checksum=dict(algorithm='sha256', value=hashlib.sha256(data).hexdigest(),
                                   reference='https://example.org/checksums'),
                     evidence=[dict(reference='https://example.org/', detail='Synthetic fixture')])
        if package:
            entry['package'] = 'qgis-ltr'
        self.entries.append(entry)
        (self.cache / filename).write_bytes(data)
        self.write_metadata()
        return entry

    def write_metadata(self):
        manifest = []
        installed = ['INSTALLED.DB 2']
        for entry in self.entries:
            row = dict(name=entry['name'], version=entry['version'])
            if 'package' in entry:
                row['version_source'] = dict(kind='osgeo4w-package', package=entry['package'])
                installed.append(entry['package'] + ' packages-x86_64/' + entry['filename'] + ' 0')
            manifest.append(row)
        (self.root / MANIFEST).write_text(json.dumps(dict(schema_version=1, platform='windows', components=manifest)))
        (self.root / SOURCES).write_text(json.dumps(dict(schema_version=1, components=self.entries)))
        (self.root / INVENTORY).write_text('\n'.join(installed) + '\n')

    def tar(self, members=None):
        if members is None:
            members = [('Folder With Spaces/Bytes.txt', tarfile.REGTYPE, b'bytes\r\n\x00\xff')]
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode='w:bz2') as archive:
            for name, kind, data in members:
                member = tarfile.TarInfo(name)
                member.type, member.mode, member.mtime = kind, 0o777, 123
                if kind in (tarfile.SYMTYPE, tarfile.LNKTYPE):
                    member.linkname = '../outside'
                member.size = len(data) if kind == tarfile.REGTYPE else 0
                archive.addfile(member, io.BytesIO(data) if member.isfile() else None)
        return buffer.getvalue()

    def run_extract(self, **kwargs):
        with contextlib.redirect_stdout(io.StringIO()):
            extraction.extract(self.cache, self.output, root=self.root, **kwargs)

    def assert_clean_failure(self):
        self.assertFalse((self.output / 'osgeo4w-v1/qgis-ltr').exists())
        if self.output.exists():
            self.assertFalse([p for p in self.output.rglob('*') if p.name.startswith('.')])

    def need_tools(self, *tools):
        missing = [tool for tool in tools if shutil.which(tool) is None]
        if missing:
            message = 'Missing extraction integration tools: ' + ', '.join(missing)
            if os.environ.get('FHAST_REQUIRE_EXTRACTION_TOOLS') == '1':
                self.fail(message)
            self.skipTest(message)


class TarTests(Fixture):
    def test_normal_bytes_directories_spaces_and_normalization(self):
        self.register(self.tar([('empty/', tarfile.DIRTYPE, b''),
                                ('Folder With Spaces/Bytes.txt', tarfile.REGTYPE, b'bytes\r\n\x00\xff'),
                                ('etc/postinstall/not-executed.bat', tarfile.REGTYPE, b'exit 99')]))
        self.run_extract()
        dest = self.output / 'osgeo4w-v1/qgis-ltr'
        self.assertEqual((dest / 'payload/Folder With Spaces/Bytes.txt').read_bytes(), b'bytes\r\n\x00\xff')
        self.assertTrue((dest / 'payload/empty').is_dir())
        for path in [dest] + list(dest.rglob('*')):
            self.assertEqual(path.stat().st_mtime, extraction.NORMALIZED_TIME)
            if os.name != 'nt':
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o755 if path.is_dir() else 0o644)

    def test_empty_metapackage(self):
        self.register(self.tar([('.', tarfile.DIRTYPE, b'')]))
        self.run_extract()
        self.assertEqual(list((self.output / 'osgeo4w-v1/qgis-ltr/payload').iterdir()), [])

    def test_fully_empty_tar(self):
        self.register(self.tar([]))
        self.run_extract()

    def test_deterministic_receipt_and_tree(self):
        self.register(self.tar())
        self.run_extract()
        first = self.output / 'osgeo4w-v1/qgis-ltr'
        receipt = (first / 'receipt.json').read_bytes()
        self.output = self.base / 'another output'
        self.run_extract()
        second = self.output / 'osgeo4w-v1/qgis-ltr'
        self.assertEqual(receipt, (second / 'receipt.json').read_bytes())
        record = json.loads(receipt)
        self.assertEqual(record['file_count'], 1)
        self.assertEqual(record['directory_count'], 1)
        self.assertEqual(record['file_bytes'], 9)
        self.assertEqual(record['tools'], {})
        self.assertNotIn(str(self.base), receipt.decode())
        for a in first.rglob('*'):
            b = second / a.relative_to(first)
            self.assertEqual(a.stat().st_mtime, b.stat().st_mtime)
            if a.is_file():
                self.assertEqual(a.read_bytes(), b.read_bytes())

    def reject_members(self, members, message):
        self.register(self.tar(members))
        with self.assertRaisesRegex(ValueError, message):
            self.run_extract()
        self.assert_clean_failure()

    def test_unsafe_names(self):
        for name in ('../escape', 'a/../escape', '/absolute', 'C:/drive', '//server/share',
                     r'\\server\share', r'folder\file', 'CON', 'aux.txt', 'COM1.log',
                     'LPT¹', 'CONOUT$', 'a<b', 'a?b', 'a*b', 'a|b', 'a"b', 'a\x01b',
                     'file:stream', 'trailing.', 'trailing ', 'a//b'):
            with self.subTest(name=name):
                self.entries.clear()
                self.reject_members([(name, tarfile.REGTYPE, b'x')], 'Unsafe')

    def test_duplicate(self):
        self.reject_members([('same', tarfile.REGTYPE, b'x')] * 2, 'Duplicate')

    def test_duplicate_after_normalization(self):
        self.reject_members([('./same', tarfile.REGTYPE, b'x'), ('same', tarfile.REGTYPE, b'x')], 'Duplicate')

    def test_case_collision(self):
        self.reject_members([('Name', tarfile.REGTYPE, b'x'), ('name', tarfile.REGTYPE, b'x')], 'Case')

    def test_implied_parent_case_collision(self):
        self.reject_members([('Dir/one', tarfile.REGTYPE, b'x'), ('dir/two', tarfile.REGTYPE, b'x')], 'Case')

    def test_file_directory_conflicts(self):
        for members in ([('a', tarfile.REGTYPE, b'x'), ('a/b', tarfile.REGTYPE, b'x')],
                        [('a/b', tarfile.REGTYPE, b'x'), ('a', tarfile.REGTYPE, b'x')],
                        [('a/', tarfile.DIRTYPE, b''), ('a', tarfile.REGTYPE, b'x')]):
            with self.subTest(members=members):
                self.entries.clear()
                self.reject_members(members, 'conflict|Duplicate')

    def test_links_and_special_nodes(self):
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE, tarfile.CHRTYPE, tarfile.BLKTYPE):
            with self.subTest(kind=kind):
                self.entries.clear()
                self.reject_members([('special', kind, b'')], 'Unsupported tar')

    def test_sparse_rejected(self):
        self.register(self.tar([('sparse', tarfile.GNUTYPE_SPARSE, b'')]))
        with self.assertRaisesRegex(ValueError, 'Unsupported tar'):
            self.run_extract()
        self.assert_clean_failure()

    def test_entry_limit(self):
        self.register(self.tar())
        with self.assertRaisesRegex(ValueError, 'entry-count'):
            self.run_extract(limits=extraction.Limits(entries=1))
        self.assert_clean_failure()

    def test_root_headers_cannot_bypass_entry_limit(self):
        self.register(self.tar([('.', tarfile.DIRTYPE, b'')] * 3))
        with self.assertRaisesRegex(ValueError, 'entry-count'):
            self.run_extract(limits=extraction.Limits(entries=2))

    def test_expanded_byte_limit(self):
        self.register(self.tar())
        with self.assertRaisesRegex(ValueError, 'Expanded-byte'):
            self.run_extract(limits=extraction.Limits(expanded=2))
        self.assert_clean_failure()

    def test_individual_file_limit(self):
        self.register(self.tar())
        with self.assertRaisesRegex(ValueError, 'Individual-file'):
            self.run_extract(limits=extraction.Limits(file=2))
        self.assert_clean_failure()

    def test_format_is_not_trusted_from_filename(self):
        self.register(b'not a bzip2 tar')
        with self.assertRaises(tarfile.TarError):
            self.run_extract()
        self.assert_clean_failure()


class ControllerTests(Fixture):
    def setUp(self):
        super().setUp()
        self.entry = self.register(self.tar())

    def test_missing_artifact(self):
        (self.cache / self.entry['filename']).unlink()
        with self.assertRaisesRegex(ValueError, 'Missing'):
            self.run_extract()
        self.assertFalse(self.output.exists())

    def test_checksum_failure_before_tools_or_output(self):
        (self.cache / self.entry['filename']).write_bytes(b'changed')
        with patch.object(extraction, 'command', side_effect=AssertionError('Tool invoked')):
            with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                self.run_extract()
        self.assertFalse(self.output.exists())

    def test_recorded_size_failure(self):
        selected = extraction.selection(self.root)
        selected[0][0]['size'] = 1
        with patch.object(extraction, 'selection', return_value=selected):
            with self.assertRaisesRegex(ValueError, 'size mismatch'):
                self.run_extract()
        self.assertFalse(self.output.exists())

    def test_preexisting_destination_preserved(self):
        final = self.output / 'osgeo4w-v1/qgis-ltr'
        final.mkdir(parents=True)
        (final / 'keep').write_bytes(b'existing')
        with self.assertRaisesRegex(ValueError, 'already exists'):
            self.run_extract()
        self.assertEqual((final / 'keep').read_bytes(), b'existing')

    def test_active_lock_preserved(self):
        lock = self.output / 'osgeo4w-v1/.qgis-ltr.fhast-extraction.lock'
        lock.parent.mkdir(parents=True)
        lock.write_bytes(b'other owner')
        with self.assertRaisesRegex(ValueError, 'lock already exists'):
            self.run_extract()
        self.assertEqual(lock.read_bytes(), b'other owner')
        self.assertEqual(list(lock.parent.iterdir()), [lock])

    def test_failure_cleanup(self):
        def fail(snapshot, payload, limits):
            payload.mkdir()
            (payload / 'partial').write_bytes(b'partial')
            raise ValueError('Synthetic backend failure')
        with patch.dict(extraction.BACKENDS, {'tar.bz2': fail}):
            with self.assertRaisesRegex(ValueError, 'Synthetic'):
                self.run_extract()
        self.assert_clean_failure()

    def test_snapshot_is_private_and_verified(self):
        original = extraction.extract_tar
        def backend(snapshot, payload, limits):
            self.assertNotEqual(snapshot, self.cache / self.entry['filename'])
            (self.cache / self.entry['filename']).write_bytes(b'changed after snapshot')
            return original(snapshot, payload, limits)
        with patch.dict(extraction.BACKENDS, {'tar.bz2': backend}):
            self.run_extract()

    def test_snapshot_change_fails_before_backend(self):
        def copy(source, destination):
            destination.write_bytes(b'changed during copy')
        with patch.object(extraction.shutil, 'copyfile', side_effect=copy), \
                patch.dict(extraction.BACKENDS, {'tar.bz2': lambda *a: self.fail('Backend invoked')}):
            with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                self.run_extract()
        self.assert_clean_failure()

    def test_symlinked_artifact_rejected(self):
        original = self.cache / self.entry['filename']
        target = self.base / 'target'
        original.rename(target)
        original.symlink_to(target)
        with self.assertRaisesRegex(ValueError, 'symlink artifact'):
            self.run_extract()

    def test_symlinked_output_parent_rejected(self):
        self.output.mkdir()
        outside = self.base / 'outside'
        outside.mkdir()
        (self.output / 'osgeo4w-v1').symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'Unsafe output parent'):
            self.run_extract()
        self.assertEqual(list(outside.iterdir()), [])

    def test_symlinked_destination_rejected(self):
        final = self.output / 'osgeo4w-v1/qgis-ltr'
        final.parent.mkdir(parents=True)
        final.symlink_to(self.base / 'absent', target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'already exists'):
            self.run_extract()
        self.assertTrue(final.is_symlink())

    def test_repository_and_overlapping_roots_rejected(self):
        for cache, output in ((self.cache, self.root / 'out'), (self.root, self.output),
                              (self.cache, self.cache), (self.cache, self.cache / 'out'),
                              (self.cache, self.base)):
            with self.subTest(cache=cache, output=output):
                with self.assertRaisesRegex(ValueError, 'outside|overlap'):
                    extraction.extract(cache, output, root=self.root)

    def test_dry_run_no_writes_tools_or_network(self):
        before = sorted(str(p) for p in self.base.rglob('*'))
        with patch.object(extraction, 'command', side_effect=AssertionError('Tool invoked')):
            self.run_extract(dry_run=True)
        self.assertEqual(before, sorted(str(p) for p in self.base.rglob('*')))
        self.assertFalse(self.output.exists())

    def test_dry_run_verifies_input(self):
        (self.cache / self.entry['filename']).write_bytes(b'bad')
        with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
            self.run_extract(dry_run=True)

    def test_cli_codes_and_mutual_exclusion(self):
        arguments = ['--input-dir', str(self.cache), '--output-dir', str(self.output), '--dry-run']
        with patch.object(extraction, 'ROOT', self.root), \
                patch.object(extraction, 'selection', return_value=extraction.selection(self.root)), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(extraction.main(arguments), 0)
            (self.cache / self.entry['filename']).unlink()
            self.assertEqual(extraction.main(arguments), 1)
            with self.assertRaises(SystemExit) as error:
                extraction.main(arguments + ['--component', 'qgis', '--package-set', 'osgeo4w-v1'])
            self.assertEqual(error.exception.code, 2)

    def test_unexpected_postflight_content_rejected(self):
        original = extraction.extract_tar
        def extra(snapshot, payload, limits):
            result = original(snapshot, payload, limits)
            (payload / 'unexpected').write_bytes(b'x')
            return result
        with patch.dict(extraction.BACKENDS, {'tar.bz2': extra}):
            with self.assertRaisesRegex(ValueError, 'approved paths'):
                self.run_extract()
        self.assert_clean_failure()

    def test_postflight_links_rejected(self):
        original = extraction.extract_tar
        for hard in (False, True):
            with self.subTest(hard=hard):
                def extra(snapshot, payload, limits):
                    result = original(snapshot, payload, limits)
                    source = payload / 'Folder With Spaces/Bytes.txt'
                    if hard:
                        os.link(source, payload / 'link')
                    else:
                        (payload / 'link').symlink_to(source)
                    return result
                with patch.dict(extraction.BACKENDS, {'tar.bz2': extra}):
                    with self.assertRaisesRegex(ValueError, 'link/special'):
                        self.run_extract()
                self.assert_clean_failure()


    def test_later_failure_preserves_completed_package(self):
        entry = self.register(self.tar(), name='r', filename='second.paf.exe')
        with patch.dict(extraction.BACKENDS, {'nsis': lambda *args: (_ for _ in ()).throw(ValueError('later failure'))}):
            with self.assertRaisesRegex(ValueError, 'later failure'):
                self.run_extract()
        self.assertTrue((self.output / 'osgeo4w-v1/qgis-ltr/receipt.json').is_file())
        self.assertFalse((self.output / 'r').exists())
        self.assertFalse([p for p in self.output.rglob('*') if p.name.startswith('.')])

    def test_destination_appearing_during_extraction_preserved(self):
        original = extraction.extract_tar
        def raced(snapshot, payload, limits):
            result = original(snapshot, payload, limits)
            final = self.output / 'osgeo4w-v1/qgis-ltr'
            final.mkdir()
            (final / 'other').write_bytes(b'preserve')
            return result
        with patch.dict(extraction.BACKENDS, {'tar.bz2': raced}):
            with self.assertRaisesRegex(ValueError, 'appeared'):
                self.run_extract()
        self.assertEqual((self.output / 'osgeo4w-v1/qgis-ltr/other').read_bytes(), b'preserve')
        self.assertFalse([p for p in self.output.rglob('*') if p.name.startswith('.')])

    def test_rename_failure_cleanup(self):
        with patch.object(Path, 'rename', side_effect=OSError('rename failed')):
            with self.assertRaisesRegex(OSError, 'rename failed'):
                self.run_extract()
        self.assert_clean_failure()

    def test_dry_run_nsis_and_msi_never_invoke_tools(self):
        self.entries.clear()
        for name, filename in [('r', 'fixture.paf.exe'), ('netlogo', 'fixture.msi')]:
            self.register(b'identity verified; no parser in dry-run', name, filename)
        with patch.object(extraction, 'command', side_effect=AssertionError('Tool invoked')), \
                patch.object(extraction.subprocess, 'Popen', side_effect=AssertionError('Process invoked')):
            self.run_extract(dry_run=True)
        self.assertFalse(self.output.exists())


class SelectionTests(Fixture):
    def test_real_selection_and_aliases(self):
        default = extraction.selection()
        self.assertEqual([s[0]['name'] for s in default], ['qgis', 'qgis-python', 'qt', 'r', 'netlogo', 'pandoc'])
        locked = extraction.selection(package_set='osgeo4w-v1')
        metadata = json.loads((extraction.ROOT / LOCK).read_text())['packages']
        self.assertEqual([s[1] for s in locked], [p['name'] for p in metadata])
        self.assertEqual(len(locked), 143)
        for alias, package in INDIVIDUAL.items():
            selected = extraction.selection(component=alias)
            self.assertEqual(len(selected), 1)
            self.assertEqual(selected[0][3], 'osgeo4w-v1/' + package)
            self.assertEqual(selected[0][3], next(s[3] for s in locked if s[1] == package))
        for blocked in ('fhast-jdk', 'netlogo-jre', 'osgeo4w'):
            with self.assertRaises(ValueError):
                extraction.selection(component=blocked)

    def test_all_143_synthetic_packages_are_isolated_in_order(self):
        lock = json.loads((extraction.ROOT / LOCK).read_text())
        sources = json.loads((extraction.ROOT / SOURCES).read_text())
        data = self.tar([('shared/file', tarfile.REGTYPE, b'synthetic')])
        checksum = dict(algorithm='md5', value=hashlib.md5(data).hexdigest())
        for package in lock['packages']:
            package['size'], package['checksum'] = len(data), checksum
            (self.cache / Path(package['source_path']).name).write_bytes(data)
        for source in sources['components']:
            if source['name'] in INDIVIDUAL:
                source['checksum'].update(checksum)
        (self.root / LOCK).write_text(json.dumps(lock))
        (self.root / SOURCES).write_text(json.dumps(sources))
        shutil.copyfile(extraction.ROOT / INVENTORY, self.root / INVENTORY)
        log = io.StringIO()
        with contextlib.redirect_stdout(log):
            extraction.extract(self.cache, self.output, package_set='osgeo4w-v1', root=self.root)
        self.assertEqual(len(log.getvalue().splitlines()), 143)
        for line, package in zip(log.getvalue().splitlines(), lock['packages']):
            self.assertTrue(line.endswith('/' + package['name']))
            self.assertEqual((self.output / 'osgeo4w-v1' / package['name'] / 'payload/shared/file').read_bytes(), b'synthetic')

    def test_invalid_metadata_fails_without_writes(self):
        (self.root / SOURCES).write_text('{}')
        with self.assertRaisesRegex(ValueError, 'Invalid'):
            self.run_extract()
        self.assertFalse(self.output.exists())


class AdapterTests(Fixture):
    def test_unsafe_nsis_listing_before_extraction(self):
        for name in ('../escape', 'C:/bad', 'Folder/CON', '$UNSUPPORTED/file'):
            listing = 'Type = Nsis\n\n----------\nPath = ' + name + '\nSize = 1\n\n'
            with self.subTest(name=name), patch.object(extraction, 'tool_version', return_value='test'), \
                    patch.object(extraction, 'command', return_value=listing) as run:
                with self.assertRaises(ValueError):
                    extraction.extract_nsis(self.base / 'fixture', self.output, extraction.Limits())
                self.assertEqual(run.call_count, 1)
                self.assertFalse(self.output.exists())

    def test_malformed_and_wrong_container_listings(self):
        for listing in ('Type = Zip\n\n----------\nPath = a\nSize = 1\n',
                        'Type = Nsis\nno separator', 'Type = Nsis\n\n----------\nPath = a\nPath = b\nSize = 1\n'):
            with self.subTest(listing=listing), self.assertRaises(ValueError):
                extraction.seven_listing(listing, 'Nsis')

    def test_external_msi_cabinet_rejected(self):
        with patch.object(extraction, 'tool_version', return_value='test'), \
                patch.object(extraction, 'msi_table', return_value=[dict(Cabinet='external.cab')]), \
                patch.object(extraction, 'command', side_effect=AssertionError('Extraction invoked')):
            with self.assertRaisesRegex(ValueError, 'external cabinets'):
                extraction.extract_msi(self.base / 'fixture', self.output, extraction.Limits())

    def test_unsafe_msi_logical_path_rejected(self):
        dirs = [dict(Directory='ROOT', Directory_Parent='', DefaultDir='../escape')]
        components = [dict(Component='C', Directory_='ROOT')]
        files = [dict(File='F', Component_='C', FileName='name', FileSize='1')]
        with self.assertRaises(ValueError):
            extraction.msi_plan(dirs, components, files, extraction.Limits())

    def test_missing_tool_failure_is_clear(self):
        with patch.object(extraction.subprocess, 'run', side_effect=FileNotFoundError('missing tool')):
            with self.assertRaisesRegex(ValueError, 'Extraction tool failed'):
                extraction.command(['msiextract', '--version'])


    def test_unmapped_duplicate_wrong_size_msi_cabinet_rejected(self):
        tables = {
            'Media': [dict(Cabinet='#fixture.cab')],
            'Directory': [dict(Directory='ROOT', Directory_Parent='', DefaultDir='SourceDir')],
            'Component': [dict(Component='C', Directory_='ROOT')],
            'File': [dict(File='F', Component_='C', FileName='logical', FileSize='1')],
        }
        for records in ('Path = UNKNOWN\nSize = 1\n', 'Path = F\nSize = 2\n',
                        'Path = F\nSize = 1\n\nPath = F\nSize = 1\n'):
            def run(args):
                if args[:2] == ['msiextract', '-l']:
                    return 'logical\n'
                if args[:2] == ['7z', 'l']:
                    return 'Type = Compound\nPath = fixture.cab\nType = Cab\n\n----------\n' + records
                self.fail('Unexpected extraction invocation')
            with self.subTest(records=records), \
                    patch.object(extraction, 'tool_version', return_value='test'), \
                    patch.object(extraction, 'msi_table', side_effect=lambda _, table: tables[table]), \
                    patch.object(extraction, 'command', side_effect=run):
                with self.assertRaisesRegex(ValueError, 'cabinet'):
                    extraction.extract_msi(self.base / 'fixture', self.output, extraction.Limits())
                self.assertFalse(self.output.exists())

    def test_postflight_wrong_size_and_empty_directory_rejected(self):
        plan = extraction.Plan()
        plan.add('file', 'file', 1)
        self.output.mkdir()
        (self.output / 'file').write_bytes(b'too large')
        with self.assertRaisesRegex(ValueError, 'approved paths'):
            extraction.inspect_payload(self.output, plan)
        (self.output / 'file').write_bytes(b'x')
        (self.output / 'unexpected-empty-dir').mkdir()
        with self.assertRaisesRegex(ValueError, 'approved paths'):
            extraction.inspect_payload(self.output, plan)


class IntegrationTests(Fixture):
    def build_directory(self):
        directory = self.base / 'fixture build'
        shutil.copytree(FIXTURES, directory)
        return directory

    def test_real_nsis_helpers_bytes_and_inventory(self):
        self.need_tools('7z', 'makensis')
        directory = self.build_directory()
        subprocess.run(['makensis', '-V2', 'payload.nsi'], cwd=directory, check=True, capture_output=True)
        artifact = directory / 'fixture.exe'
        listing = extraction.command(['7z', 'l', '-slt', str(artifact)])
        self.assertIn('$PLUGINSDIR/helper.txt', listing)
        self.register(artifact.read_bytes(), 'r', 'fixture.paf.exe')
        self.run_extract()
        dest = self.output / 'r'
        self.assertEqual((dest / 'payload/App/Folder With Spaces/Payload.txt').read_bytes(),
                         (directory / 'payload.txt').read_bytes())
        self.assertFalse((dest / 'payload/$PLUGINSDIR').exists())
        self.assertEqual(json.loads((dest / 'receipt.json').read_text())['file_count'], 1)

    def test_real_msi_logical_mapping_both_program_files_properties(self):
        self.need_tools('7z', 'msiextract', 'msiinfo', 'wixl')
        directory = self.build_directory()
        source = (directory / 'payload.wxs').read_text()
        for property_name, prefix in [('ProgramFilesFolder', 'Program Files'), ('ProgramFiles64Folder', 'PFiles')]:
            with self.subTest(property=property_name):
                variant = source.replace('<Directory Id="PAYLOADDIR"',
                    f'<Directory Id="{property_name}" Name="PFiles"><Directory Id="PAYLOADDIR"')
                variant = variant.replace('    </Directory>\n    <Feature', '    </Directory></Directory>\n    <Feature')
                (directory / 'variant.wxs').write_text(variant)
                subprocess.run(['wixl', '-o', 'fixture.msi', 'variant.wxs'], cwd=directory, check=True, capture_output=True)
                self.entries.clear()
                self.register((directory / 'fixture.msi').read_bytes(), 'pandoc', 'fixture.msi')
                self.output = self.base / property_name
                self.run_extract()
                dest = self.output / 'pandoc'
                logical = prefix + '/Folder With Spaces/Logical Name.txt'
                self.assertEqual(extraction.command(['msiextract', '-l', str(directory / 'fixture.msi')]).strip(), logical)
                self.assertEqual((dest / 'payload' / logical).read_bytes(), (directory / 'payload.txt').read_bytes())
                receipt = json.loads((dest / 'receipt.json').read_text())
                self.assertEqual(receipt['file_count'], 1)
                self.assertIn('0.103', receipt['tools']['msiextract'])


    def test_real_nsis_unknown_size_is_bounded(self):
        self.need_tools('7z', 'makensis')
        directory = self.build_directory()
        subprocess.run(['makensis', '-V2', 'payload.nsi'], cwd=directory, check=True, capture_output=True)
        self.register((directory / 'fixture.exe').read_bytes(), 'r', 'fixture.paf.exe')
        with self.assertRaisesRegex(ValueError, 'limit'):
            self.run_extract(limits=extraction.Limits(file=1))
        self.assertFalse((self.output / 'r').exists())
        self.assertFalse([p for p in self.output.rglob('*') if p.name.startswith('.')])


if __name__ == '__main__':
    unittest.main()
