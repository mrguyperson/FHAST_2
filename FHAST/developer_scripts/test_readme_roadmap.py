"""Isolated offline roadmap fixtures: no runtime blobs, LFS objects, or network."""
import contextlib
import io
import json
from pathlib import Path
import re
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
import check_readme_roadmap as roadmap


class RoadmapTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='fhast roadmap ')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        # Existence-only predicates need no executable fixture contents.
        for paths in roadmap.FILE_RULES.values():
            for path in paths:
                target = self.root / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.touch()
        for path in ('README.md', roadmap.MANIFEST, roadmap.SOURCES,
                     roadmap.LOCK, 'etc/setup/installed.db'):
            target = self.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(roadmap.ROOT / path, target)

    def replace(self, old, new):
        path = self.root / 'README.md'
        text = path.read_text(encoding='utf-8')
        self.assertIn(old, text)
        path.write_text(text.replace(old, new, 1), encoding='utf-8')

    def mark(self, name, checked):
        path = self.root / 'README.md'
        text, count = re.subn(r'(<!-- roadmap:' + re.escape(name) + r' -->\n- \[)[ x](\])',
                             lambda m: m[1] + ('x' if checked else ' ') + m[2],
                             path.read_text(encoding='utf-8'))
        self.assertEqual(count, 1)
        path.write_text(text, encoding='utf-8')

    def remove_item(self, name):
        path = self.root / 'README.md'
        text = path.read_text(encoding='utf-8')
        pattern = (r'<!-- roadmap:' + re.escape(name)
                   + r' -->\n- \[[ x]\] [^\n]*(?:\n[ \t]+[^\n]+)*\n')
        matches = re.findall(pattern, text)
        self.assertEqual(len(matches), 1)
        path.write_text(text.replace(matches[0], '', 1), encoding='utf-8')
        return matches[0]

    def edit_json(self, path, edit):
        path = self.root / path
        data = json.loads(path.read_text(encoding='utf-8'))
        edit(data)
        path.write_text(json.dumps(data), encoding='utf-8')

    def errors(self):
        with patch('socket.socket.connect', side_effect=AssertionError('network forbidden')):
            return roadmap.check(self.root)

    def assert_error(self, text):
        self.assertIn(text, '\n'.join(self.errors()))

    def test_current_valid_structure(self):
        self.assertEqual(self.errors(), [])
        items, errors = roadmap.parse((self.root / 'README.md').read_text(encoding='utf-8'))
        self.assertFalse(errors)
        self.assertTrue(items)

    def test_duplicate_id(self):
        self.replace('roadmap:docs-ci', 'roadmap:docs-consistency')
        self.assert_error('duplicate roadmap ID docs-consistency')

    def test_malformed_ids(self):
        for name in ('UPPER', 'with_underscore', 'with space', 'café', '-leading', 'double--dash', ''):
            with self.subTest(name=name):
                text = (roadmap.ROOT / 'README.md').read_text(encoding='utf-8')
                items, errors = roadmap.parse(text.replace('roadmap:docs-ci', 'roadmap:' + name))
                self.assertIn('malformed roadmap ID', '\n'.join(errors))

    def test_orphan_id(self):
        self.replace('### R environment', '<!-- roadmap:orphan -->\n\n### R environment')
        self.assert_error('orphan roadmap ID orphan')

    def test_orphan_at_section_end(self):
        self.replace('## Runtime reproducibility metadata',
                     '<!-- roadmap:orphan -->\n## Runtime reproducibility metadata')
        self.assert_error('orphan roadmap ID orphan')

    def test_checkbox_without_id(self):
        self.replace('<!-- roadmap:docs-ci -->\n', '')
        self.assert_error('checkbox without an immediately preceding roadmap ID')

    def test_blank_line_breaks_association(self):
        self.replace('<!-- roadmap:docs-ci -->\n', '<!-- roadmap:docs-ci -->\n\n')
        self.assert_error('orphan roadmap ID docs-ci')
        self.assert_error('checkbox without an immediately preceding roadmap ID')

    def test_two_ids_for_one_item(self):
        self.replace('<!-- roadmap:docs-ci -->', '<!-- roadmap:extra -->\n<!-- roadmap:docs-ci -->')
        self.assert_error('orphan roadmap ID extra')

    def test_inline_or_multiple_ids_rejected(self):
        self.replace('<!-- roadmap:docs-ci -->', '<!-- roadmap:extra --> <!-- roadmap:docs-ci -->')
        self.assert_error('malformed roadmap ID')

    def test_implemented_forbidden(self):
        self.replace('### Documentation and maintenance', '### Implemented')
        self.assert_error('forbidden Implemented heading')

    def test_unexpected_heading(self):
        self.replace('### Documentation and maintenance', '### Miscellaneous')
        self.assert_error('expected topical headings')

    def test_missing_section(self):
        self.replace('## Modernization status', '## Other heading')
        self.assert_error('expected exactly one')

    def test_duplicate_section(self):
        self.replace('## Runtime reproducibility metadata', '## Modernization status')
        self.assert_error('expected exactly one')

    def test_machine_id_cannot_silently_disappear(self):
        self.replace('roadmap:runtime-jdk-source', 'roadmap:different-id')
        self.assert_error('missing registered roadmap ID runtime-jdk-source')

    def test_verified_component_must_be_checked(self):
        for name in ('runtime-qgis-source', 'runtime-netlogo-source', 'runtime-netlogo-jre-source'):
            with self.subTest(name=name):
                self.mark(name, False)
                self.assert_error(f'roadmap:{name} must be [x]')
                self.mark(name, True)

    def test_partial_and_unknown_must_be_unchecked(self):
        for name in ('runtime-jdk-source', 'runtime-pandoc-version'):
            with self.subTest(name=name):
                self.mark(name, True)
                self.assert_error(f'roadmap:{name} must be [ ]')
                self.mark(name, False)

    def test_future_verified_jdk_changes_expected_state(self):
        def promote(data):
            entry = next(e for e in data['components'] if e['name'] == 'fhast-jdk')
            entry['status'] = 'verified'
            entry.pop('unresolved')
        self.edit_json(roadmap.SOURCES, promote)
        self.assert_error('roadmap:runtime-jdk-source must be [x]')
        self.mark('runtime-jdk-source', True)
        self.assertEqual(self.errors(), [])

    def test_known_pandoc_version_does_not_require_verified_acquisition(self):
        def version(data):
            next(e for e in data['components'] if e['name'] == 'pandoc')['version'] = '1.2.3'
        self.edit_json(roadmap.MANIFEST, version)
        self.edit_json(roadmap.SOURCES, version)
        self.assert_error('roadmap:runtime-pandoc-version must be [x]')
        self.mark('runtime-pandoc-version', True)
        self.assertEqual(self.errors(), [])

    def test_jre_requires_netlogo_parent(self):
        def parent(data):
            next(e for e in data['components'] if e['name'] == 'netlogo-jre')['parent'] = 'r'
        self.edit_json(roadmap.SOURCES, parent)
        self.assert_error('roadmap:runtime-netlogo-jre-source must be [ ]')

    def test_present_infrastructure_must_be_checked(self):
        for name in roadmap.FILE_RULES:
            with self.subTest(name=name):
                self.mark(name, False)
                self.assert_error(f'roadmap:{name} must be [x]')
                self.mark(name, True)

    def test_missing_tooling(self):
        (self.root / '.github/workflows/runtime-sources.yml').unlink()
        self.assert_error('roadmap:runtime-source-ci must be [ ]')
        self.mark('runtime-source-ci', False)
        self.assertEqual(self.errors(), [])

    def test_invalid_sources_cannot_be_hidden_by_unchecking(self):
        (self.root / roadmap.SOURCES).write_text('{', encoding='utf-8')
        self.mark('runtime-sources', False)
        self.assert_error('Runtime metadata:')

    def test_invalid_manifest(self):
        self.edit_json(roadmap.MANIFEST, lambda d: d.update(schema_version=99))
        self.assert_error('unsupported runtime manifest')

    def test_invalid_lock_cannot_be_hidden_by_unchecking(self):
        self.edit_json(roadmap.LOCK, lambda d: d['packages'].pop())
        self.mark('osgeo4w-acquisition-lock', False)
        self.assert_error('lock must contain exactly 143 packages')

    def test_wording_is_not_a_predicate(self):
        path = self.root / 'README.md'
        text = path.read_text(encoding='utf-8')
        text = re.sub(r'(<!-- roadmap:runtime-jdk-source -->\n- \[ \] )[^\n]+',
                      r'\1Reworded task without changing its durable identity.', text)
        path.write_text(text, encoding='utf-8')
        self.assertEqual(self.errors(), [])

    def test_manual_states_not_guessed(self):
        items, _ = roadmap.parse((self.root / 'README.md').read_text(encoding='utf-8'))
        for name, (checked, _, _) in items.items():
            if name not in roadmap.FILE_RULES and name not in roadmap.COMPONENT_RULES:
                self.mark(name, not checked)
        self.assertEqual(self.errors(), [])

    def test_unknown_manual_item_rejected(self):
        self.replace('### Longer-term goals',
                     '<!-- roadmap:new-manual-task -->\n- [ ] New task requiring judgment.\n\n### Longer-term goals')
        self.assert_error('unknown roadmap ID new-manual-task')

    def test_deleted_manual_item_rejected(self):
        self.remove_item('linux-builds')
        self.assert_error('missing registered roadmap ID linux-builds')

    def test_renamed_manual_item_rejected(self):
        self.replace('roadmap:linux-builds', 'roadmap:linux-build')
        self.assert_error('missing registered roadmap ID linux-builds')
        self.assert_error('unknown roadmap ID linux-build')

    def test_manual_item_wrong_category(self):
        item = self.remove_item('linux-builds')
        self.replace('### Longer-term goals', item + '\n### Longer-term goals')
        self.assert_error("roadmap:linux-builds belongs under 'Longer-term goals', found 'Testing and releases'")

    def test_manual_item_rewording_preserves_identity(self):
        path = self.root / 'README.md'
        text, count = re.subn(r'(<!-- roadmap:linux-builds -->\n- \[ \] )[^\n]+',
                              r'\1Different prose for this registered manual task.',
                              path.read_text(encoding='utf-8'))
        self.assertEqual(count, 1)
        path.write_text(text, encoding='utf-8')
        self.assertEqual(self.errors(), [])

    def test_manual_item_order_enforced(self):
        first = self.remove_item('r-dependency-inventory')
        second = self.remove_item('r-package-versions')
        self.replace('<!-- roadmap:r-dependency-lock -->',
                     second + first + '<!-- roadmap:r-dependency-lock -->')
        self.assert_error("registered item order for 'R environment'")

    def test_deliberately_registered_new_manual_task(self):
        self.replace('### Longer-term goals',
                     '<!-- roadmap:new-manual-task -->\n- [ ] New task requiring judgment.\n\n### Longer-term goals')
        category = 'Testing and releases'
        with patch.dict(roadmap.ROADMAP, {category: roadmap.ROADMAP[category] + ('new-manual-task',)}):
            self.assertEqual(self.errors(), [])
            self.mark('new-manual-task', True)
            self.assertEqual(self.errors(), [])

    def test_registration_integrity(self):
        ids = [name for names in roadmap.ROADMAP.values() for name in names]
        self.assertEqual(len(ids), 43)
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(roadmap.HEADINGS, tuple(roadmap.ROADMAP))
        self.assertTrue(roadmap.FILE_RULES.keys() | roadmap.COMPONENT_RULES.keys() <= set(ids))
        items, errors = roadmap.parse((self.root / 'README.md').read_text(encoding='utf-8'))
        self.assertFalse(errors)
        self.assertEqual(list(items), ids)
        for category, names in roadmap.ROADMAP.items():
            for name in names:
                self.assertEqual(items[name][2], category)

    def test_missing_heading(self):
        self.replace('### R environment\n', '')
        self.assert_error('expected topical headings')

    def test_reordered_headings(self):
        self.replace('### R environment', '### Placeholder')
        self.replace('### Testing and releases', '### R environment')
        self.replace('### Placeholder', '### Testing and releases')
        self.assert_error('expected topical headings')

    def test_extra_heading(self):
        self.replace('### R environment', '### Unexpected category\n\n### R environment')
        self.assert_error('expected topical headings')

    def test_only_target_section_and_not_fenced_examples(self):
        self.replace('## Modernization status', '- [x] Outside without ID.\n\n## Modernization status')
        self.replace('### R environment', '```markdown\n### Implemented\n<!-- roadmap:INVALID -->\n- [x] Example\n```\n\n### R environment')
        self.assertEqual(self.errors(), [])

    def test_cli_exit_codes(self):
        for errors, code in (([], 0), (['README.md:1: fixture failure'], 1)):
            with patch.object(roadmap, 'check', return_value=errors), \
                    contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(roadmap.main(), code)


if __name__ == '__main__':
    unittest.main()
