"""Validate the recorded OSGeo4W v1 acquisition set offline (Python stdlib only).

Checks recorded consistency, not remote availability, authenticity, dependency
closure, or installed-tree equivalence. No runtimes or network calls are used.
"""
import json
from pathlib import Path, PurePosixPath
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
LOCK = 'build/osgeo4w-v1-package-lock.json'
SOURCES = 'build/windows-runtime-sources.json'
INVENTORY = 'etc/setup/installed.db'
BASE = 'https://download.osgeo.org/osgeo4w/v1/'
HEADER = {'schema_version': 1, 'lineage': 'OSGeo4W v1', 'architecture': 'x86_64',
          'installed_inventory': INVENTORY, 'checksum_inventory_url': BASE + 'md5sums',
          'artifact_base_url': BASE}
FIELDS = {'name', 'installed_archive', 'source_path', 'url', 'size', 'checksum'}
INDIVIDUAL = {'qgis': 'qgis-ltr', 'qgis-python': 'python3-core', 'qt': 'qt5-libs'}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'duplicate JSON key: ' + key)
        result[key] = value
    return result


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=unique_object)


def safe_path(value, prefix):
    require(isinstance(value, str) and value.startswith(prefix)
            and re.fullmatch(r'[A-Za-z0-9_./+\-]+', value)
            and all(part not in {'', '.', '..'} for part in value.split('/'))
            and value.endswith('.tar.bz2'), 'invalid archive/source path: ' + str(value))


def validate(root=ROOT):
    try:
        lock = read_json(root / LOCK)
        require(isinstance(lock, dict) and set(lock) == set(HEADER) | {'packages'},
                'invalid lock top-level fields')
        for key, expected in HEADER.items():
            require(type(lock[key]) is type(expected) and lock[key] == expected,
                    'invalid ' + key)
        packages = lock['packages']
        require(isinstance(packages, list) and len(packages) == 143,
                'lock must contain exactly 143 packages')
        lines = (root / INVENTORY).read_text(encoding='utf-8').splitlines()
        require(lines and lines[0] == 'INSTALLED.DB 2', 'unsupported installed.db header')
        installed = {}
        for line in lines[1:]:
            if not line.strip():
                continue
            fields = line.split()
            require(len(fields) == 3, 'malformed installed.db row')
            name, archive, _ = fields
            require(name not in installed, 'duplicate installed package: ' + name)
            safe_path(archive, 'packages-x86_64/')
            installed[name] = archive
        require(len(installed) == 143, 'installed.db must contain exactly 143 packages')
        by_name, paths, urls = {}, set(), set()
        for entry in packages:
            require(isinstance(entry, dict) and set(entry) == FIELDS, 'invalid package fields')
            name = entry['name']
            require(isinstance(name, str) and name in installed, 'unknown package: ' + str(name))
            require(name not in by_name, 'duplicate package: ' + name)
            by_name[name] = entry
            require(entry['installed_archive'] == installed[name], name + ': installed archive mismatch')
            path = entry['source_path']
            safe_path(path, 'x86_64/release/')
            require(path not in paths, name + ': duplicate source path')
            paths.add(path)
            require(PurePosixPath(path).name == PurePosixPath(installed[name]).name,
                    name + ': archive filename mismatch')
            require(entry['url'] == BASE + path, name + ': URL/source-path disagreement')
            require(entry['url'] not in urls, name + ': duplicate artifact URL')
            urls.add(entry['url'])
            require(type(entry['size']) is int and entry['size'] > 0, name + ': invalid size')
            checksum = entry['checksum']
            require(isinstance(checksum, dict) and set(checksum) == {'algorithm', 'value'}
                    and checksum['algorithm'] == 'md5' and isinstance(checksum['value'], str)
                    and re.fullmatch(r'[0-9a-fA-F]{32}', checksum['value']), name + ': invalid MD5 checksum')
        require(set(by_name) == set(installed), 'package set differs from installed.db')
        require(list(by_name) == list(installed), 'package order differs from installed.db')
        sources = read_json(root / SOURCES)
        require(isinstance(sources, dict) and type(sources.get('schema_version')) is int
                and sources['schema_version'] == 1 and isinstance(sources.get('components'), list),
                'unsupported runtime source metadata')
        for component, package in INDIVIDUAL.items():
            matches = [c for c in sources['components'] if isinstance(c, dict) and c.get('name') == component]
            require(len(matches) == 1, 'missing/duplicate runtime source: ' + component)
            source, locked = matches[0], by_name[package]
            checksum = source.get('checksum')
            require(source.get('status') == 'verified' and source.get('type') == 'osgeo4w-package'
                    and source.get('package') == package and source.get('filename') == PurePosixPath(locked['source_path']).name
                    and source.get('url') == locked['url'] and isinstance(checksum, dict)
                    and checksum.get('algorithm') == 'md5' and isinstance(checksum.get('value'), str)
                    and checksum['value'].lower() == locked['checksum']['value'].lower(),
                    component + ': disagreement with individually verified source')
        return []
    except (ValueError, OSError, TypeError, KeyError) as exc:
        return [str(exc)]


def main():
    errors = validate()
    if errors:
        for error in errors:
            print('OSGeo4W package lock: ' + error, file=sys.stderr)
        return 1
    print('OSGeo4W package lock passed: 143 packages (offline recorded consistency only).')
    return 0


if __name__ == '__main__':
    sys.exit(main())
