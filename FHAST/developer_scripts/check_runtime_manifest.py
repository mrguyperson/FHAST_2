"""Validate the current Windows bundle inventory using static metadata only.

Run: python3 FHAST/developer_scripts/check_runtime_manifest.py
Standard library only; no runtime execution, tree hashing, Git, or network calls.
"""

import configparser
import json
from pathlib import Path, PurePosixPath
import re
import sys
import zipfile


ROOT = Path(__file__).resolve().parents[2]
MANIFEST = 'build/windows-runtime-manifest.json'
SOURCE_FIELDS = {
    'unknown': {'kind', 'path'},
    'osgeo4w-package': {'kind', 'path', 'package'},
    'text-field': {'kind', 'path', 'key', 'separator'},
    'ini': {'kind', 'path', 'section', 'key'},
    'jar-manifest': {'kind', 'path', 'key'},
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def nonempty(value):
    return isinstance(value, str) and bool(value.strip()) and value == value.strip()


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'duplicate JSON key: ' + key)
        result[key] = value
    return result


def bundle_path(root, value):
    require(nonempty(value), 'path must be a nonempty string')
    path = PurePosixPath(value)
    require(not path.is_absolute() and '..' not in path.parts
            and '\\' not in value and ':' not in value,
            'path must be bundle-relative with / separators: ' + value)
    target = (root / path).resolve()
    require(target.is_relative_to(root.resolve()), 'path leaves bundle: ' + value)
    return target


def field_value(text, key, separator):
    values = []
    for line in text.splitlines():
        name, found, value = line.partition(separator)
        if found and name.strip() == key:
            value = value.strip()
            if value.startswith('"') and value.endswith('"'):
                value = value[1:-1]
            values.append(value)
    require(len(values) == 1 and nonempty(values[0]),
            'expected exactly one nonempty metadata field: ' + key)
    return values[0]


def source_version(path, source):
    kind = source['kind']
    if kind == 'unknown':
        return None  # Existence only; an LFS pointer is sufficient here.
    with path.open('rb') as stream:
        require(not stream.read(80).startswith(b'version https://git-lfs.github.com/spec/'),
                'version metadata is an LFS pointer, not readable metadata: ' + str(path))
    if kind == 'jar-manifest':
        with zipfile.ZipFile(path) as archive:
            text = archive.read('META-INF/MANIFEST.MF').decode('utf-8')
        # Only the main section; ignore compiler Created-By and per-entry fields.
        text = text.replace('\r\n', '\n').split('\n\n', 1)[0]
        text = text.replace('\n ', '')  # JAR continuation lines.
        return field_value(text, source['key'], ':')
    text = path.read_text(encoding='utf-8')
    if kind == 'text-field':
        require(source['separator'] in (':', '='), 'unsupported metadata separator')
        return field_value(text, source['key'], source['separator'])
    if kind == 'ini':
        # NetLogo's other sections also contain bare JVM option lines.
        parser = configparser.ConfigParser(interpolation=None, allow_no_value=True)
        parser.optionxform = str
        parser.read_string(text)
        value = parser[source['section']][source['key']]
        require(isinstance(value, str) and bool(value.strip()), 'empty INI version field')
        return value.strip()
    package = source['package']
    require(text.splitlines()[0:1] == ['INSTALLED.DB 2'], 'unsupported OSGeo4W database format')
    rows = [line.split() for line in text.splitlines()[1:]
            if line.split() and line.split()[0] == package]
    require(len(rows) == 1 and len(rows[0]) == 3, 'expected one package record: ' + package)
    archive_name = PurePosixPath(rows[0][1]).name
    match = re.fullmatch(re.escape(package) + r'-(.+)\.tar\.bz2', archive_name)
    require(match is not None, 'unsupported package archive: ' + archive_name)
    return match.group(1)  # Retain the OSGeo4W packaging revision.


def validate(root, manifest_path=None):
    """Return diagnostics; also usable with a synthetic bundle in unit tests."""
    manifest_path = manifest_path or root / MANIFEST
    try:
        data = json.loads(manifest_path.read_text(encoding='utf-8'),
                          object_pairs_hook=unique_object)
        require(isinstance(data, dict) and set(data) == {'schema_version', 'platform', 'components'},
                'expected schema_version, platform, and components only')
        require(type(data['schema_version']) is int and data['schema_version'] == 1,
                'unsupported schema_version (expected 1)')
        require(data['platform'] == 'windows', 'platform must be windows')
        require(isinstance(data['components'], list) and data['components'],
                'components must be a nonempty list')
    except (OSError, UnicodeError, ValueError) as error:
        return [str(manifest_path) + ': ' + str(error)]

    errors = []
    names = set()
    for index, component in enumerate(data['components'], 1):
        label = 'component ' + str(index)
        try:
            required = {'name', 'version', 'path', 'version_source'}
            require(isinstance(component, dict) and required <= set(component)
                    and set(component) <= required | {'notes'}, 'missing or unsupported component fields')
            name = component['name']
            require(nonempty(name), 'name must be a nonempty string')
            label = name
            require(name not in names, 'duplicate component name')
            names.add(name)
            if 'notes' in component:
                require(nonempty(component['notes']), 'notes must be a nonempty string')
            source = component['version_source']
            require(isinstance(source, dict) and isinstance(source.get('kind'), str)
                    and source['kind'] in SOURCE_FIELDS, 'unsupported version_source kind')
            require(set(source) == SOURCE_FIELDS[source['kind']], 'missing or unsupported version_source fields')
            require(all(nonempty(value) for value in source.values()), 'version_source fields must be nonempty strings')
            version = component['version']
            if source['kind'] == 'unknown':
                require(version is None and nonempty(component.get('notes')),
                        'unknown version requires null version and explanatory notes')
            else:
                require(nonempty(version), 'verified version must be a nonempty string')
            target = bundle_path(root, component['path'])
            require(target.is_dir(), 'missing runtime directory: ' + component['path'])
            source_path = bundle_path(root, source['path'])
            require(source_path.is_file(), 'missing version-source file: ' + source['path'])
            actual = source_version(source_path, source)
            require(version == actual, 'version mismatch: declared {!r}, metadata {!r}'.format(version, actual))
        except (OSError, UnicodeError, ValueError, KeyError, configparser.Error, zipfile.BadZipFile) as error:
            errors.append(label + ': ' + str(error))
    return errors


def main():
    errors = validate(ROOT)
    if errors:
        print('\n'.join(errors), file=sys.stderr)
        print('Windows runtime manifest check failed.', file=sys.stderr)
        return 1
    print('Windows runtime manifest check passed (static metadata; unknown versions remain unverified).')
    return 0


if __name__ == '__main__':
    sys.exit(main())
