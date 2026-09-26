"""Offline README roadmap structure and explicitly bounded completion rules.

Only the Modernization status section is parsed (ATX headings, standalone IDs
immediately above Markdown checkboxes; fenced examples are ignored). Wording is
not a predicate. All IDs, categories and item order are registered in ROADMAP;
only explicit completion rules constrain checkbox state.

Reuse source/lock validation without reading runtime blobs. The manifest rule
checks recorded metadata consistency and the presence of its validation tooling;
full runtime-content validation remains the separate runtime-manifest CI job.
File-presence rules prove maintained tooling exists, not its semantic correctness.
"""
from pathlib import Path
import re
import sys

sys.dont_write_bytecode = True
from check_runtime_sources import MANIFEST, SOURCES, read_json, validate as validate_sources
from check_osgeo4w_package_lock import LOCK, validate as validate_lock
from fetch_runtime_artifacts import select

ROOT = Path(__file__).resolve().parents[2]
ROADMAP = {
    'Documentation and maintenance': (
        'repository-guidance', 'docs-consistency', 'docs-ci',
    ),
    'Launchers and path handling': (
        'launcher-paths', 'launcher-path-tests', 'r-bootstrap-paths', 'r-bootstrap-tests',
    ),
    'Runtime inventory and provenance': (
        'runtime-manifest', 'runtime-manifest-validation', 'runtime-sources',
        'runtime-source-states', 'runtime-source-ci', 'runtime-qgis-source',
        'runtime-netlogo-source', 'runtime-jdk-source', 'runtime-netlogo-jre-source',
        'runtime-pandoc-version',
    ),
    'Runtime acquisition and reconstruction': (
        'osgeo4w-acquisition-lock', 'runtime-fetcher', 'runtime-fetcher-tests',
        'osgeo4w-package-fetch', 'osgeo4w-dependency-resolution', 'runtime-extraction',
        'runtime-layout', 'runtime-reference-comparison', 'windows-clean-build',
    ),
    'R environment': (
        'r-dependency-inventory', 'r-package-versions', 'r-dependency-lock',
        'r-library-build', 'r-reference-validation',
    ),
    'Testing and releases': (
        'model-regression-baselines', 'windows-startup-tests', 'application-chain-tests',
        'simulation-output-comparison', 'windows-release-artifacts', 'developer-release-workflow',
    ),
    'Longer-term goals': (
        'vendored-runtime-removal', 'repository-size', 'source-build-separation',
        'linux-builds', 'macos-builds', 'distribution-formats',
    ),
}
HEADINGS = tuple(ROADMAP)

ID = re.compile(r'<!-- roadmap:([a-z0-9]+(?:-[a-z0-9]+)*) -->')
CHECKBOX = re.compile(r'^\s*(?:[-+*]|\d+[.)])\s+\[([ xX])\]\s+\S')
HEADING = re.compile(r'^ {0,3}(#{1,6})\s+(.+?)(?:\s+#+)?\s*$')
PREFIX = 'FHAST/developer_scripts/'
FILE_RULES = {
    'runtime-manifest': (MANIFEST,),
    'runtime-manifest-validation': (PREFIX + 'check_runtime_manifest.py',
                                    PREFIX + 'test_runtime_manifest.py'),
    'runtime-sources': (SOURCES,),
    'runtime-source-states': (PREFIX + 'check_runtime_sources.py',
                              PREFIX + 'test_runtime_sources.py'),
    'runtime-source-ci': (PREFIX + 'check_runtime_sources.py',
                          PREFIX + 'test_runtime_sources.py',
                          '.github/workflows/runtime-sources.yml'),
    'osgeo4w-acquisition-lock': (LOCK, PREFIX + 'check_osgeo4w_package_lock.py',
                                 PREFIX + 'test_osgeo4w_package_lock.py',
                                 '.github/workflows/osgeo4w-package-lock.yml'),
    'runtime-fetcher': (PREFIX + 'fetch_runtime_artifacts.py',),
    'runtime-fetcher-tests': (PREFIX + 'test_fetch_runtime_artifacts.py',),
    'osgeo4w-package-fetch': (PREFIX + 'fetch_runtime_artifacts.py', LOCK),
}
COMPONENT_RULES = {
    'runtime-qgis-source': 'qgis',
    'runtime-netlogo-source': 'netlogo',
    'runtime-netlogo-jre-source': 'netlogo-jre',
    'runtime-jdk-source': 'fhast-jdk',
    'runtime-pandoc-version': 'pandoc',
}


def prose_lines(text):
    """Retain physical line numbers and ID comments, but skip fenced examples."""
    fence = None
    for number, line in enumerate(text.splitlines(), 1):
        if fence:
            if re.fullmatch(r' {0,3}' + re.escape(fence[0])
                            + '{' + str(len(fence)) + r',}\s*', line):
                fence = None
            continue
        marker = re.match(r'^ {0,3}(`{3,}|~{3,})', line)
        if marker:
            fence = marker.group(1)
            continue
        yield number, line


def parse(text):
    """Return ID -> (checked, checkbox line, category), and structural diagnostics."""
    sections = []
    section = None
    for number, line in prose_lines(text):
        heading = HEADING.fullmatch(line)
        if heading and len(heading[1]) <= 2:
            section = None
            if heading[1] == '##' and heading[2] == 'Modernization status':
                section = []
                sections.append(section)
        elif section is not None:
            section.append((number, line))
    if len(sections) != 1:
        return {}, ['README.md: expected exactly one ## Modernization status section']
    errors, items, headings = [], {}, []
    pending = None
    category = None
    for number, line in sections[0]:
        checkbox = CHECKBOX.match(line)
        if pending and (not checkbox or number != pending[1] + 1):
            errors.append(f'README.md:{pending[1]}: orphan roadmap ID {pending[0]}; '
                          'place it immediately above one checkbox')
            pending = None
        if re.search(r'<!--\s*roadmap\b', line, re.I):
            match = ID.fullmatch(line.strip())
            if not match:
                errors.append(f'README.md:{number}: malformed roadmap ID; '
                              'use <!-- roadmap:lowercase-id --> on its own line')
            else:
                pending = (match[1], number)
        if checkbox:
            if pending is None or pending[1] != number - 1:
                errors.append(f'README.md:{number}: checkbox without an immediately preceding roadmap ID')
            else:
                name = pending[0]
                if name in items:
                    errors.append(f'README.md:{number}: duplicate roadmap ID {name}')
                else:
                    items[name] = (checkbox[1].lower() == 'x', number, category)
                pending = None
        heading = HEADING.fullmatch(line)
        if heading:
            category = heading[2] if heading[1] == '###' else None
            if heading[2].casefold() == 'implemented':
                errors.append(f'README.md:{number}: forbidden Implemented heading; use topical headings')
            headings.append(heading[2] if heading[1] == '###' else line.strip())
    if pending:
        errors.append(f'README.md:{pending[1]}: orphan roadmap ID {pending[0]}')
    if tuple(headings) != HEADINGS:
        errors.append('README.md: expected topical headings in this order: ' + '; '.join(HEADINGS))
    registered = {name: category for category, names in ROADMAP.items() for name in names}
    for name, category in registered.items():
        if name not in items:
            errors.append(f'README.md: missing registered roadmap ID {name} in {category!r}')
    for name, (_, line, category) in items.items():
        if name not in registered:
            errors.append(f'README.md:{line}: unknown roadmap ID {name}; register new tasks in ROADMAP')
        elif category != registered[name]:
            errors.append(f'README.md:{line}: roadmap:{name} belongs under '
                          f'{registered[name]!r}, found {category!r}')
    for category, expected in ROADMAP.items():
        actual = tuple(name for name, item in items.items() if item[2] == category)
        if actual != expected:
            errors.append(f'README.md: registered item order for {category!r} must be: '
                          + ', '.join(expected))
    return items, errors


def expected_states(root):
    """Derive only explicit rules, leaving all other task judgments to reviewers."""
    states, errors = {}, []
    for name, paths in FILE_RULES.items():
        missing = [path for path in paths if not (root / path).is_file()]
        states[name] = (not missing, 'missing files: ' + ', '.join(missing)
                        if missing else 'maintained files present: ' + ', '.join(paths))

    source_errors = validate_sources(root)
    lock_errors = validate_lock(root)
    errors.extend('Runtime metadata: ' + e for e in source_errors)
    errors.extend('OSGeo4W lock: ' + e for e in lock_errors)
    # Invalid authoritative inputs fail even if somebody unchecks an item.
    if not source_errors:
        entries = {e['name']: e for e in read_json(root / SOURCES)['components']}
        for name, component in COMPONENT_RULES.items():
            if component not in entries:
                errors.append(f'{SOURCES}: missing roadmap component {component}')
                continue
            entry = entries[component]
            if component == 'pandoc':
                # The task is exact version identification, not full acquisition.
                done = entry['version'] is not None
                reason = f"{component} recorded version={entry['version']!r}"
            else:
                done = entry['status'] == 'verified'
                reason = f"{component} status={entry['status']}"
                if component == 'netlogo-jre':
                    done = done and entry['type'] == 'bundled' and entry.get('parent') == 'netlogo'
                    reason += f", type={entry['type']}, parent={entry.get('parent')!r}"
            states[name] = (done, reason)
    if not lock_errors:
        try:
            # Pure selection: no download, output directory, or dependency solving.
            selected = select(root, package_set='osgeo4w-v1')
            states['osgeo4w-package-fetch'] = (states['osgeo4w-package-fetch'][0] and len(selected) == 143,
                                             'validated lock selection must contain 143 packages')
        except (ValueError, OSError) as error:
            errors.append('OSGeo4W fetch selection: ' + str(error))
    return states, errors


def check(root=ROOT):
    try:
        items, errors = parse((root / 'README.md').read_text(encoding='utf-8'))
        states, state_errors = expected_states(root)
        errors.extend(state_errors)
        for name in sorted(FILE_RULES.keys() | COMPONENT_RULES.keys()):
            if name in items and name in states:
                expected, reason = states[name]
                checked, line, _ = items[name]
                if checked != expected:
                    mark = 'x' if expected else ' '
                    errors.append(f'README.md:{line}: roadmap:{name} must be [{mark}]: {reason}')
        return errors
    except (OSError, UnicodeError, ValueError) as error:
        return ['Roadmap input: ' + str(error)]


def main():
    errors = check()
    if errors:
        print('\n'.join(errors), file=sys.stderr)
        return 1
    print('README roadmap passed: structure and explicit completion rules only; other judgments remain manual.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
