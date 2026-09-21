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
            operation.assert_called_once_with(self.output, None, False)
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


if __name__ == '__main__':
    unittest.main()
