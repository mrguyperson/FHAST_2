"""Tiny synthetic extraction roots; no downloads, archives or runtime execution."""
import contextlib
import errno
import hashlib
import io
import json
import os
from pathlib import Path
import socket
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
import assemble_runtime_layout as layout

METADATA = ('build/windows-runtime-manifest.json', 'build/windows-runtime-sources.json',
            'build/osgeo4w-v1-package-lock.json', 'etc/setup/installed.db')
NETLOGO = 'PFiles/NetLogo 6.2.2/'
R_TARGET = 'FHAST/FHAST_App/dist/R-Portable/'
NL_TARGET = 'FHAST/FHAST_App/dist/NetLogo 6.2.2/'
PANDOC_TARGET = 'FHAST/FHAST_App/dist/Pandoc/'


def tree_snapshot(root):
    """Independent output oracle, including empty dirs and excluding root metadata."""
    return {p.relative_to(root).as_posix():
            ('directory', None) if p.is_dir() else ('file', p.read_bytes())
            for p in root.rglob('*')}


class LayoutTests(unittest.TestCase):
    def setUp(self):
        for boundary in ('socket.socket.connect', 'socket.create_connection',
                         'urllib.request.urlopen', 'fetch_runtime_artifacts.open_download',
                         'subprocess.Popen', 'os.system'):
            guard = patch(boundary, side_effect=AssertionError('Network/external execution forbidden'))
            guard.start()
            self.addCleanup(guard.stop)
        temporary = tempfile.TemporaryDirectory(prefix='FHAST layout spaces ')
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root = self.base / 'repository'
        for name in METADATA:
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((layout.ROOT / name).read_bytes())
        self.source = self.base / 'extracted inputs'
        self.output = self.base / 'layout output'
        selected = layout.selection(self.root, package_set='osgeo4w-v1')
        for name in ('r', 'netlogo', 'pandoc'):
            selected.extend(layout.selection(self.root, component=name))
        self.selected = {item[1]: item for item in selected}
        # Actual validated metadata membership, but deliberately tiny fake payloads.
        # Demonstrates both the full 146-input contract and schema-1's trust limit.
        for item in reversed(selected):
            (self.source / item[3] / 'payload').mkdir(parents=True)
        self.put('r', 'App/R-Portable/bin/Rscript.exe', b'fake R bytes\x00\xff')
        self.put('r', 'Other/empty', None)
        self.put('r', 'R-Portable.exe', b'portable launcher')
        self.put('r', 'help.html', b'help\r\n')
        self.put('netlogo', NETLOGO + 'app/NetLogo.cfg', b'-Xmx1024m\r\n')
        self.put('netlogo', NETLOGO + 'runtime/lib/rt.jar', b'fake bundled JRE')
        for filename in layout.PANDOC_FILES:
            self.put('pandoc', 'Pandoc/' + filename, filename.encode() + b'\r\n\x00')
        for name in self.selected:
            self.receipt(name)

    def folder(self, name):
        return self.source / self.selected[name][3]

    def put(self, package, name, data=b'x'):
        path = self.folder(package) / 'payload' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if data is None:
            path.mkdir(exist_ok=True)
        else:
            path.write_bytes(data)
        return path

    def receipt(self, package, **changes):
        entry, logical, backend, relative = self.selected[package]
        files = list((self.source / relative / 'payload').rglob('*'))
        value = dict(schema_version=1, name=logical, filename=entry['filename'], backend=backend,
                     checksum={k: entry['checksum'][k] for k in ('algorithm', 'value')},
                     file_count=sum(p.is_file() for p in files),
                     directory_count=sum(p.is_dir() for p in files),
                     file_bytes=sum(p.stat().st_size for p in files if p.is_file()),
                     normalized_timestamp=946684800,
                     tools={key: 'synthetic 1.0' for key in
                            {'tar.bz2': (), 'nsis': ('7z',), 'msi': ('7z', 'msiinfo', 'msiextract')}[backend]})
        value.update(changes)
        path = self.folder(package) / 'receipt.json'
        path.write_text(json.dumps(value, sort_keys=True) + '\n', encoding='utf-8')
        return path

    def run_layout(self, **kwargs):
        return layout.assemble(self.source, self.output, root=self.root, **kwargs)

    def reject(self, message):
        before = set(self.base.iterdir())
        with self.assertRaisesRegex((ValueError, OSError), message):
            self.run_layout()
        self.assertFalse(self.output.exists())
        self.assertEqual(set(self.base.iterdir()), before)

    def add_pair(self, path, providers=('libpng-vc14', 'libpng'), values=(b'earlier', b'later')):
        for name, value in zip(providers, values):
            self.put(name, path, value)
            self.receipt(name)

    def test_disjoint_packages_shared_directories_and_empty_directories(self):
        self.put('shell', 'bin/one.exe', b'\x00\xff\r\n')
        self.put('msvcrt', 'bin/two.exe', b'two')
        self.put('shell', 'share/empty', None)
        self.receipt('shell'); self.receipt('msvcrt')
        before = tree_snapshot(self.source)
        result = self.run_layout()
        self.assertEqual((self.output / 'bin/one.exe').read_bytes(), b'\x00\xff\r\n')
        self.assertEqual((self.output / 'bin/two.exe').read_bytes(), b'two')
        self.assertEqual(list((self.output / 'share/empty').iterdir()), [])
        self.assertEqual(len(result['inputs']), 146)
        self.assertEqual(result['collisions'], [])
        self.assertEqual(tree_snapshot(self.source), before)

    def test_lock_order_not_creation_or_alphabetical_order(self):
        # libpng-vc14 sorts after libpng, but is earlier in the lock.
        self.add_pair('bin/libpng16.dll')
        result = self.run_layout()
        self.assertEqual((self.output / 'bin/libpng16.dll').read_bytes(), b'later')
        self.assertLess(result['package_order'].index('libpng-vc14'), result['package_order'].index('libpng'))
        self.assertEqual(result['collisions'], [dict(path='bin/libpng16.dll',
            providers=['libpng-vc14', 'libpng'], winner='libpng', identical=False)])

    def test_parent_planning_does_not_depend_on_python_recursion_limit(self):
        plan = layout.Layout()
        path = 'bin/' + '/'.join(['nested'] * 1050) + '/file'
        plan.add(path, layout.Node('file', 1, 'a' * 64), 'shell')
        self.assertEqual(len(plan.nodes), 1052)
        self.assertEqual(sum(n.kind == 'directory' for n in plan.nodes.values()), 1051)
        self.assertEqual(plan.nodes[path].kind, 'file')

    def test_runtime_root_files_templates_and_lifecycle_scripts_are_data(self):
        payloads = {'OSGeo4W.bat': b'@echo off\r\n', 'OSGeo4W.ico': b'icon\x00\xff',
                    'cmake/upstream.cmake': b'upstream', 'bin/qgis-ltr.bat.tmpl': b'template\r\n',
                    'etc/postinstall/test.bat': b'exit 99\r\n',
                    'etc/preremove/test.bat': b'exit 99\r\n'}
        for path, data in payloads.items():
            self.put('shell', path, data)
        self.receipt('shell')
        self.run_layout()
        for path, data in payloads.items():
            self.assertEqual((self.output / path).read_bytes(), data)
        for absent in ('bin/qgis-ltr.bat', 'etc/setup/installed.db', 'profile', 'command.txt'):
            self.assertFalse((self.output / absent).exists())

    def test_filesystem_enumeration_does_not_change_receipt(self):
        self.add_pair('bin/libpng16.dll')
        first = self.run_layout(dry_run=True)
        original = Path.iterdir
        with patch.object(Path, 'iterdir', lambda path: iter(reversed(list(original(path))))):
            self.assertEqual(self.run_layout(dry_run=True), first)

    def test_identical_overlap_requires_no_differing_authorization(self):
        self.add_pair('share/proj/null', ('proj-datumgrid', 'proj'), (b'same', b'same'))
        result = self.run_layout()
        self.assertEqual((self.output / 'share/proj/null').read_bytes(), b'same')
        self.assertEqual(result['collisions'][0]['identical'], True)

    def test_all_eight_reviewed_differing_groups(self):
        expected = {
            'include/netcdf.h': ('hdf4', 'netcdf'),
            'apps/Qt5/qsci/api/python/PyQt5.api': ('qscintilla-qt5', 'pyqt5'),
            'bin/libpng16.dll': ('libpng-vc14', 'libpng'),
            **{'apps/Python37/Lib/site-packages/pkg_resources/' + suffix:
               ('python3-core', 'python3-setuptools') for suffix in (
                   '__init__.py', '_vendor/appdirs.py', '_vendor/pyparsing.py',
                   'extern/__init__.py', 'py31compat.py')},
        }
        self.assertEqual(set(layout.DIFFERING_OVERLAPS), set(expected))
        for path, providers in expected.items():
            self.add_pair(path, providers)
        result = self.run_layout()
        self.assertEqual(len(result['collisions']), 8)
        for path in expected:
            self.assertEqual((self.output / path).read_bytes(), b'later')

    def test_undeclared_differing_overlap(self):
        self.add_pair('bin/unreviewed.dll')
        self.reject('Undeclared differing overlap')

    def test_changed_provider_set(self):
        self.add_pair('bin/libpng16.dll')
        self.put('shell', 'bin/libpng16.dll', b'third')
        self.receipt('shell')
        self.reject('provider-set changed')

    def test_missing_approved_provider(self):
        self.put('libpng', 'bin/libpng16.dll', b'only one')
        self.receipt('libpng')
        self.reject('provider-set changed')

    def test_identical_bytes_do_not_hide_changed_approved_providers(self):
        self.add_pair('bin/libpng16.dll', ('shell', 'msvcrt'), (b'same', b'same'))
        self.reject('provider-set changed')

    def test_python_directory_alias_with_differing_file(self):
        suffix = 'site-packages/pkg_resources/__init__.py'
        self.put('python3-core', 'apps/Python37/Lib/' + suffix, b'old')
        self.put('python3-setuptools', 'apps/Python37/lib/' + suffix, b'new\r\n')
        self.receipt('python3-core'); self.receipt('python3-setuptools')
        self.run_layout()
        paths = tree_snapshot(self.output)
        self.assertEqual(paths['apps/Python37/Lib/' + suffix], ('file', b'new\r\n'))
        self.assertNotIn('apps/Python37/lib', paths)

    def test_qt_directory_alias(self):
        self.put('qt5-libs', 'apps/qt5/bin/qt.dll')
        self.put('qca-qt5-libs', 'apps/Qt5/plugins/qca.dll')
        self.receipt('qt5-libs'); self.receipt('qca-qt5-libs')
        self.run_layout()
        paths = tree_snapshot(self.output)
        self.assertIn('apps/Qt5/bin/qt.dll', paths)
        self.assertIn('apps/Qt5/plugins/qca.dll', paths)
        self.assertNotIn('apps/qt5', paths)

    def test_unknown_directory_case_variant(self):
        self.put('shell', 'bin/Folder/a')
        self.put('msvcrt', 'bin/folder/b')
        self.receipt('shell'); self.receipt('msvcrt')
        self.reject('Unrecognized case-insensitive')

    def test_unknown_qt_spelling_even_without_another_provider(self):
        self.put('qt5-libs', 'apps/QT5/bin/a')
        self.receipt('qt5-libs')
        self.reject('Unrecognized directory-case')

    def test_unknown_file_case_variant_even_with_identical_bytes(self):
        self.put('shell', 'bin/Name.dll')
        self.put('msvcrt', 'bin/name.dll')
        self.receipt('shell'); self.receipt('msvcrt')
        self.reject('Unrecognized case-insensitive')

    @unittest.skipIf(os.name == 'nt', 'Case-sensitive input fixture required')
    def test_case_aliases_within_one_input_are_not_valid_extractor_output(self):
        self.put('shell', 'bin/File')
        self.put('shell', 'bin/file')
        self.receipt('shell')
        self.reject('Case-insensitive input collision')

    def test_file_directory_conflict(self):
        self.put('shell', 'bin/conflict')
        self.put('msvcrt', 'bin/conflict/child')
        self.receipt('shell'); self.receipt('msvcrt')
        self.reject('File/directory conflict')

    def test_osgeo_cannot_supply_project_tree_or_jdk(self):
        for forbidden in ('FHAST/jdk-11/release', 'profile/config.ini', 'command.txt', 'layout-receipt.json'):
            with self.subTest(path=forbidden):
                path = self.put('shell', forbidden)
                self.receipt('shell')
                self.reject('Unsupported OSGeo4W root')
                # Remove only this synthetic fixture subtree before the next case.
                top = self.folder('shell') / 'payload' / forbidden.split('/')[0]
                if top.is_dir():
                    path.unlink()
                    for parent in path.parents:
                        parent.rmdir()
                        if parent == top:
                            break
                else:
                    top.unlink()

    def test_r_whole_root_and_all_seven_cluster_tests(self):
        filenames = ['agnes', 'clara', 'daisy', 'diana', 'ellipsoid', 'fanny', 'sweep']
        for name in filenames:
            self.put('r', 'App/R-Portable/library/cluster/tests/' + name + '-ex.R', name.encode())
        self.put('r', 'Other/license.txt', b'keep license')
        self.receipt('r')
        self.run_layout()
        self.assertEqual(tree_snapshot(self.output / R_TARGET), tree_snapshot(self.folder('r') / 'payload'))
        self.assertFalse((self.output / R_TARGET / 'Data').exists())

    def test_r_unexpected_root_state_rejected(self):
        self.put('r', 'Data/settings/R-PortableSettings.ini')
        self.receipt('r')
        self.reject('R Portable payload root shape')

    def test_netlogo_full_payload_and_jre_preserved(self):
        for name in ('app/docs/manual.html', 'app/models/sample.nlogo', 'app/extensions/.bundled/vid/vid.jar'):
            self.put('netlogo', NETLOGO + name, b'upstream')
        self.receipt('netlogo')
        self.run_layout()
        self.assertEqual(tree_snapshot(self.output / NL_TARGET),
                         tree_snapshot(self.folder('netlogo') / 'payload' / NETLOGO))
        self.assertFalse((self.output / 'PFiles').exists())
        self.assertFalse((self.output / NL_TARGET / 'app/extensions/.bundled/pathdir').exists())

    def test_netlogo_wrapper_siblings_rejected_at_both_levels(self):
        for extra in ('other', 'PFiles/other'):
            with self.subTest(extra=extra):
                path = self.put('netlogo', extra, None)
                self.receipt('netlogo')
                self.reject('netlogo wrapper shape/sibling')
                path.rmdir()

    def test_pandoc_exact_relocation(self):
        self.run_layout()
        self.assertEqual(tree_snapshot(self.output / PANDOC_TARGET),
                         tree_snapshot(self.folder('pandoc') / 'payload/Pandoc'))
        self.assertFalse((self.output / 'Pandoc').exists())

    def test_unexpected_pandoc_payload_file_rejected(self):
        self.put('pandoc', 'Pandoc/extra')
        self.receipt('pandoc')
        self.reject('Unexpected Pandoc payload files')

    def test_missing_receipt(self):
        (self.folder('r') / 'receipt.json').unlink()
        self.reject('missing entries')

    def test_malformed_receipt_json_and_duplicate_keys(self):
        for raw in ('{', '[]', '{"schema_version":1,"schema_version":1}', '{"unexpected":1}'):
            with self.subTest(raw=raw):
                (self.folder('r') / 'receipt.json').write_text(raw)
                self.reject('Expecting|Malformed|duplicate')

    def test_wrong_receipt_fields_and_types(self):
        changes = [('schema_version', 2), ('schema_version', True), ('name', 'wrong'),
                   ('filename', '../wrong'), ('backend', 'tar.bz2'),
                   ('normalized_timestamp', 0), ('normalized_timestamp', 946684800.0),
                   ('checksum', {'algorithm': 'sha256', 'value': '0' * 64}),
                   ('file_count', True), ('directory_count', -1), ('file_bytes', '1'),
                   ('tools', {}), ('tools', {'7z': ''})]
        for key, value in changes:
            with self.subTest(key=key, value=value):
                self.receipt('r', **{key: value})
                self.reject('Receipt|Invalid receipt|Malformed receipt')

    def test_count_and_byte_mismatches(self):
        for key in ('file_count', 'directory_count', 'file_bytes'):
            with self.subTest(key=key):
                self.receipt('r', **{key: 999999})
                self.reject('disagrees with payload')

    def test_missing_package_and_unexpected_package(self):
        package = self.folder('shell')
        moved = self.base / 'saved package'
        package.rename(moved)
        self.reject('missing entries')
        moved.rename(package)
        (self.source / 'osgeo4w-v1/unexpected').mkdir()
        self.reject('extra=')

    def test_unexpected_component_including_jdk(self):
        for name in ('fhast-jdk', 'qgis', 'extra.json'):
            with self.subTest(name=name):
                path = self.source / name
                path.mkdir()
                self.reject('extra=')
                path.rmdir()

    def test_unexpected_extraction_component_sibling(self):
        (self.folder('r') / 'extra').mkdir()
        self.reject('extra=')

    def test_input_and_output_must_not_overlap_repository(self):
        for source, output in ((self.root, self.output), (self.source, self.root / 'layout'),
                               (self.base, self.output)):
            with self.subTest(source=source, output=output):
                with self.assertRaisesRegex(ValueError, 'outside the repository'):
                    layout.assemble(source, output, root=self.root)

    def test_input_output_overlap(self):
        for output in (self.source, self.source / 'inside'):
            with self.subTest(output=output):
                with self.assertRaisesRegex(ValueError, 'must not overlap'):
                    layout.assemble(self.source, output, root=self.root)
        parent = self.base / 'input parent'
        parent.mkdir()
        self.source.rename(parent / 'input')
        with self.assertRaisesRegex(ValueError, 'must not overlap'):
            layout.assemble(parent / 'input', parent, root=self.root)

    def test_preexisting_output_preserved_including_empty_directory(self):
        self.output.mkdir()
        with self.assertRaisesRegex(ValueError, 'already exists'):
            self.run_layout()
        (self.output / 'keep').write_bytes(b'keep')
        with self.assertRaisesRegex(ValueError, 'already exists'):
            self.run_layout()
        self.assertEqual((self.output / 'keep').read_bytes(), b'keep')

    def test_preexisting_file_and_dangling_output_symlink(self):
        self.output.write_bytes(b'keep')
        with self.assertRaisesRegex(ValueError, 'already exists'):
            self.run_layout()
        self.assertEqual(self.output.read_bytes(), b'keep')
        self.output.unlink()
        self.make_symlink(self.output, self.base / 'absent')
        with self.assertRaisesRegex(ValueError, 'already exists'):
            self.run_layout()
        self.assertTrue(self.output.is_symlink())

    def make_symlink(self, path, target, directory=False):
        try:
            path.symlink_to(target, target_is_directory=directory)
        except OSError as error:
            self.skipTest('Symlink creation unavailable: ' + str(error))

    def test_symlink_payload_and_receipt(self):
        victim = self.folder('r') / 'payload/help.html'
        victim.unlink()
        self.make_symlink(victim, self.base / 'absent')
        self.reject('Unsupported link')
        victim.unlink(); victim.write_bytes(b'help\r\n')
        receipt = self.folder('r') / 'receipt.json'
        receipt.unlink()
        self.make_symlink(receipt, self.base / 'absent')
        self.reject('Unsupported link')

    def test_symlinked_payload_directory(self):
        path = self.folder('r') / 'payload/Other/empty'
        path.rmdir()
        self.make_symlink(path, self.base, True)
        self.reject('Unsupported link')

    def test_symlinked_root_or_ancestor(self):
        alias = self.base / 'alias'
        self.make_symlink(alias, self.base, True)
        for source, output in ((alias / self.source.name, self.output),
                               (self.source, alias / self.output.name)):
            with self.assertRaisesRegex(ValueError, 'Unsupported link'):
                layout.assemble(source, output, root=self.root)

    def test_hardlinked_payload(self):
        victim = self.folder('r') / 'payload/help.html'
        try:
            os.link(victim, self.base / 'another name')
        except OSError as error:
            self.skipTest('Hardlink creation unavailable: ' + str(error))
        self.reject('hardlink')

    @unittest.skipUnless(hasattr(os, 'mkfifo'), 'FIFO creation unavailable')
    def test_fifo_rejected_without_opening(self):
        os.mkfifo(self.folder('r') / 'payload/fifo')
        self.reject('special node')

    @unittest.skipUnless(hasattr(socket, 'AF_UNIX') and os.name != 'nt', 'Unix socket fixture unavailable')
    def test_socket_rejected(self):
        with socket.socket(socket.AF_UNIX) as server:
            try:
                server.bind(str(self.folder('r') / 'payload/sock'))
            except OSError as error:
                if error.errno in (errno.EPERM, errno.EACCES, errno.EAFNOSUPPORT, errno.EPROTONOSUPPORT):
                    self.skipTest('Unix socket fixture unavailable: ' + str(error))
                raise
            self.reject('special node')

    def test_device_node_type_rejected(self):
        # No privileges needed to exercise block/character device classification.
        for mode in (stat.S_IFCHR, stat.S_IFBLK, stat.S_IFSOCK):
            with self.subTest(mode=mode):
                with self.assertRaisesRegex(ValueError, 'special node'):
                    layout.node_kind(os.stat_result((mode, 0, 0, 1, 0, 0, 0, 0, 0, 0)))

    def test_unsafe_windows_paths_and_traversal(self):
        for name in ('../escape', '/absolute', 'C:/drive', 'a/../b', 'a//b', './a', 'a/',
                     r'a\b', 'CON', 'aux.txt', 'LPT¹', 'NUL', 'name:stream', 'a?b',
                     'a*b', 'a|b', 'a<b', 'a>b', 'a"b', 'a\x01b', 'trailing.', 'trailing '):
            with self.subTest(name=name):
                with self.assertRaisesRegex(ValueError, 'Unsafe'):
                    layout.path_name(name)
        with self.assertRaisesRegex(ValueError, 'Traversal'):
            layout.assemble(self.source / '..' / self.source.name, self.output, root=self.root)

    @unittest.skipIf(os.name == 'nt', 'Windows refuses reserved names at creation')
    def test_actual_reserved_payload_path(self):
        self.put('shell', 'bin/CON.txt')
        self.receipt('shell')
        self.reject('Unsafe')

    def test_normalized_metadata_including_receipt_and_empty_directories(self):
        original = self.folder('r') / 'payload/help.html'
        original.chmod(0o700)
        os.utime(original, (123, 123))
        self.run_layout()
        for path in [self.output] + list(self.output.rglob('*')):
            self.assertEqual(path.stat().st_mtime_ns, 946684800000000000)
            if os.name != 'nt':
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o755 if path.is_dir() else 0o644)
        self.assertEqual(original.stat().st_mtime, 123)

    def test_repeatable_tree_receipt_and_independent_digest(self):
        self.put('shell', 'share/empty', None)
        self.receipt('shell')
        first = self.run_layout()
        before = tree_snapshot(self.output)
        digest = hashlib.sha256()
        files = directories = size = 0
        for path, (kind, data) in sorted(before.items()):
            if path == 'layout-receipt.json':
                continue
            if kind == 'directory':
                directories += 1
                record = [path, kind, 0]
            else:
                files += 1
                size += len(data)
                record = [path, kind, len(data), hashlib.sha256(data).hexdigest()]
            digest.update((json.dumps(record, ensure_ascii=False, separators=(',', ':')) + '\n').encode())
        self.assertEqual(first['tree_sha256'], digest.hexdigest())
        self.assertEqual((first['file_count'], first['directory_count'], first['file_bytes']), (files, directories, size))
        self.assertNotIn(str(self.base), before['layout-receipt.json'][1].decode())
        self.output = self.base / 'another layout'
        self.assertEqual(self.run_layout(), first)
        self.assertEqual(tree_snapshot(self.output), before)

    def test_schema1_same_size_mutation_is_not_claimed_authenticated(self):
        before = self.run_layout(dry_run=True)
        (self.folder('r') / 'payload/help.html').write_bytes(b'HELP\r\n')
        after = self.run_layout(dry_run=True)
        self.assertEqual(before['inputs'], after['inputs'])
        self.assertNotEqual(before['tree_sha256'], after['tree_sha256'])
        self.assertIn('not authenticated', after['input_integrity'])

    def test_input_receipt_hash_records_exact_receipt_bytes(self):
        before = self.run_layout(dry_run=True)
        path = self.folder('r') / 'receipt.json'
        raw = path.read_bytes()
        record = next(r for r in before['inputs'] if r['input'] == 'r')
        self.assertEqual(record['receipt_sha256'], hashlib.sha256(raw).hexdigest())
        path.write_text(json.dumps(json.loads(raw), indent=4) + '\n', encoding='utf-8')
        after = self.run_layout(dry_run=True)
        self.assertEqual(before['tree_sha256'], after['tree_sha256'])
        self.assertNotEqual(before['inputs'], after['inputs'])

    def test_copy_failure_is_atomic_and_cleans_temporary_state(self):
        before = tree_snapshot(self.source)
        def fail(node, target):
            target.write_bytes(b'partial')
            raise OSError('injected copy failure')
        with patch.object(layout, 'copy_file', side_effect=fail):
            self.reject('injected copy failure')
        self.assertEqual(tree_snapshot(self.source), before)

    def test_input_change_between_preflight_and_copy_rejected(self):
        original = layout.copy_file
        def mutate(node, target):
            node.source.write_bytes(bytes(b ^ 0xff for b in node.source.read_bytes()))
            original(node, target)
        with patch.object(layout, 'copy_file', side_effect=mutate):
            self.reject('Input changed since preflight')

    def test_input_replaced_by_symlink_before_copy_rejected(self):
        original = layout.copy_file
        def replace(node, target):
            outside = self.base / 'outside payload'
            outside.write_bytes(node.source.read_bytes())
            node.source.unlink()
            self.make_symlink(node.source, outside)
            original(node, target)
        with patch.object(layout, 'copy_file', side_effect=replace):
            with self.assertRaisesRegex(ValueError, 'Unsupported link'):
                self.run_layout()
        self.assertFalse(self.output.exists())
        self.assertFalse(list(self.base.glob('.*fhast-layout-*')))

    def test_publication_failure_cleans_temporary_state(self):
        def fail(*args):
            raise OSError('injected publication failure')
        with patch.object(layout, 'no_replace_publisher', return_value=fail):
            self.reject('injected publication failure')

    def test_native_publication_refuses_racing_empty_destination(self):
        native = layout.no_replace_publisher()
        def race(source, destination):
            destination.mkdir()
            native(source, destination)
        with patch.object(layout, 'no_replace_publisher', return_value=race):
            with self.assertRaises(FileExistsError):
                self.run_layout()
        self.assertEqual(list(self.output.iterdir()), [])
        self.assertFalse(list(self.base.glob('.*fhast-layout-*')))

    def test_missing_output_parent_is_not_created(self):
        self.output = self.base / 'absent parent/output'
        self.reject('No such file|cannot find')
        self.assertFalse(self.output.parent.exists())

    def test_dry_run_full_validation_without_writes(self):
        self.add_pair('bin/libpng16.dll')
        before = tree_snapshot(self.base)
        with patch.object(layout.tempfile, 'TemporaryDirectory', side_effect=AssertionError('Temporary write')), \
                patch.object(layout, 'copy_file', side_effect=AssertionError('Copy')), \
                patch.object(layout, 'no_replace_publisher', side_effect=AssertionError('Publish')):
            result = self.run_layout(dry_run=True)
        self.assertEqual(result['collisions'][0]['winner'], 'libpng')
        self.assertEqual(tree_snapshot(self.base), before)

    def test_complete_preflight_precedes_writes_in_real_and_dry_runs(self):
        with patch.object(layout.tempfile, 'TemporaryDirectory', side_effect=AssertionError('Early write')):
            self.receipt('pandoc', file_bytes=999)
            for dry_run in (False, True):
                with self.assertRaisesRegex(ValueError, 'disagrees with payload'):
                    self.run_layout(dry_run=dry_run)
            self.receipt('pandoc')
            self.add_pair('bin/unreviewed')
            for dry_run in (False, True):
                with self.assertRaisesRegex(ValueError, 'Undeclared differing'):
                    self.run_layout(dry_run=dry_run)
        self.assertFalse(self.output.exists())

    def test_jdk_unsupported_in_receipt_and_cli_status(self):
        with patch.object(layout, 'assemble', wraps=self.cli_assemble):
            stream = io.StringIO()
            with contextlib.redirect_stdout(stream):
                result = layout.main(['--input-dir', str(self.source), '--output-dir', str(self.output), '--dry-run'])
        self.assertEqual(result, 0)
        self.assertIn('fhast-jdk is unsupported', stream.getvalue())
        receipt = self.run_layout()
        self.assertEqual(receipt['unsupported_required_components'], ['fhast-jdk'])
        self.assertFalse((self.output / 'FHAST/jdk-11').exists())

    def cli_assemble(self, source, output, dry_run):
        # Explicit function reference avoids recursively calling the patched mock.
        return ASSEMBLE(source, output, dry_run, root=self.root)


ASSEMBLE = layout.assemble


if __name__ == '__main__':
    unittest.main()
