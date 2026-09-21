"""Validate recorded acquisition evidence offline, not remote availability or truth.

Schema 1: one entry per Windows runtime manifest component. All entries require
name, version (including null), status, type and nonempty evidence (reference/detail).
Optional fields are type-specific; omitted means not established, never guessed.
Verified artifacts/packages require filename, URL and a provenance-labelled hash.
Partial/unresolved entries require nonempty unresolved reasons; verified entries
must omit them. Bundled entries name a parent; verified children need a verified
parent. Package sets remain partial until a future schema can describe full locks.
No downloads, runtime execution, third-party packages, or LFS objects are needed.
"""

import json
from pathlib import Path, PurePosixPath
import re
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = 'build/windows-runtime-manifest.json'
SOURCES = 'build/windows-runtime-sources.json'
INVENTORY = 'etc/setup/installed.db'
REQUIRED = {'name', 'version', 'status', 'type', 'evidence'}
TYPE_FIELDS = {
    'artifact': {'filename', 'url', 'checksum'},
    'osgeo4w-package': {'package', 'filename', 'url', 'checksum'},
    'package-set': {'inventory'},
    'bundled': {'parent'},
    'unknown': set(),
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def string(value):
    return isinstance(value, str) and bool(value.strip()) and value == value.strip()


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'duplicate JSON key: ' + key)
        result[key] = value
    return result


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=unique_object)


def web_url(value):
    require(string(value), 'URL must be a nonempty string')
    parsed = urlsplit(value)
    require(parsed.scheme == 'https' and parsed.hostname and not parsed.username
            and not parsed.password and not any(c.isspace() for c in value),
            'URL must be an absolute HTTPS URL: ' + value)


def reference(value):
    require(string(value), 'evidence reference must be nonempty')
    if value.startswith('https:'):
        web_url(value)
    else:
        path = PurePosixPath(value)
        require(not path.is_absolute() and '..' not in path.parts
                and ':' not in value and '\\' not in value,
                'evidence reference must be a repository path or HTTPS URL')


def installed_packages(root):
    lines = (root / INVENTORY).read_text(encoding='utf-8').splitlines()
    require(lines and lines[0] == 'INSTALLED.DB 2', 'unsupported installed.db format')
    result = {}
    for line in lines[1:]:
        if not line.strip():
            continue
        fields = line.split()
        require(len(fields) == 3, 'malformed installed.db row')
        name, archive, _ = fields
        require(name not in result, 'duplicate installed package: ' + name)
        result[name] = PurePosixPath(archive).name
    require(result, 'empty installed package inventory')
    return result


def validate(root):
    """Return actionable errors; root also allows synthetic, isolated test bundles."""
    try:
        manifest = read_json(root / MANIFEST)
        require(isinstance(manifest, dict) and type(manifest.get('schema_version')) is int
                and manifest['schema_version'] == 1 and manifest.get('platform') == 'windows'
                and isinstance(manifest.get('components'), list), 'unsupported runtime manifest')
        versions = {}
        for component in manifest['components']:
            require(isinstance(component, dict) and string(component.get('name'))
                    and 'version' in component and (component['version'] is None
                    or string(component['version'])), 'malformed runtime manifest component')
            name = component['name']
            require(name not in versions, 'duplicate runtime manifest component: ' + name)
            versions[name] = component['version']
        data = read_json(root / SOURCES)
        require(isinstance(data, dict) and set(data) == {'schema_version', 'components'}
                and type(data['schema_version']) is int and data['schema_version'] == 1,
                'unsupported source schema or top-level fields')
        require(isinstance(data['components'], list) and data['components'],
                'components must be a nonempty list')
        entries = {}
        packages = None
        for entry in data['components']:
            require(isinstance(entry, dict) and REQUIRED <= set(entry),
                    'component missing required fields')
            name = entry['name']
            require(string(name) and name in versions, 'unknown component: ' + str(name))
            require(name not in entries, 'duplicate component: ' + name)
            entries[name] = entry
            require(entry['version'] == versions[name], name + ': version disagrees with runtime manifest')
            status, kind = entry['status'], entry['type']
            require(isinstance(status, str) and status in {'verified', 'partial', 'unresolved'},
                    name + ': unsupported status')
            require(isinstance(kind, str) and kind in TYPE_FIELDS, name + ': unsupported acquisition type')
            require(set(entry) <= REQUIRED | TYPE_FIELDS[kind] | {'unresolved'},
                    name + ': unsupported fields for acquisition type')
            if status == 'verified':
                require(entry['version'] is not None and 'unresolved' not in entry,
                        name + ': verified requires known version and no unresolved gaps')
                require(kind not in {'unknown', 'package-set'}, name + ': type cannot be verified')
            else:
                gaps = entry.get('unresolved')
                require(isinstance(gaps, list) and gaps and all(string(x) for x in gaps),
                        name + ': incomplete acquisition requires explicit unresolved reasons')
            require(kind != 'unknown' or status == 'unresolved', name + ': unknown must be unresolved')
            ev = entry['evidence']
            require(isinstance(ev, list) and ev, name + ': evidence must be nonempty')
            for item in ev:
                require(isinstance(item, dict) and set(item) == {'reference', 'detail'}
                        and string(item['detail']), name + ': malformed evidence')
                reference(item['reference'])
            if 'filename' in entry:
                filename = entry['filename']
                require(string(filename) and '/' not in filename and '\\' not in filename
                        and ':' not in filename and filename not in {'.', '..'}, name + ': invalid filename')
            if 'url' in entry:
                web_url(entry['url'])
                require('filename' in entry, name + ': URL requires filename')
            if 'checksum' in entry:
                checksum = entry['checksum']
                require(isinstance(checksum, dict) and set(checksum) == {'algorithm', 'value', 'reference'},
                        name + ': malformed checksum')
                sizes = {'md5': 32, 'sha256': 64, 'sha512': 128}
                algorithm = checksum['algorithm']
                require(isinstance(algorithm, str) and algorithm in sizes, name + ': unsupported checksum algorithm')
                require(isinstance(checksum['value'], str) and
                        re.fullmatch('[0-9a-fA-F]{%d}' % sizes[algorithm], checksum['value']),
                        name + ': malformed checksum value')
                web_url(checksum['reference'])
                require('filename' in entry, name + ': checksum requires filename')
            if status == 'verified' and kind in {'artifact', 'osgeo4w-package'}:
                require({'filename', 'url', 'checksum'} <= set(entry),
                        name + ': verified acquisition requires filename, URL and checksum')
            if kind == 'package-set':
                require(entry.get('inventory') == INVENTORY, name + ': unsupported package inventory')
                packages = installed_packages(root)
            if kind == 'osgeo4w-package':
                require(string(entry.get('package')) and 'filename' in entry,
                        name + ': package acquisition requires package and filename')
                if packages is None:
                    packages = installed_packages(root)
                require(packages.get(entry['package']) == entry['filename'],
                        name + ': package/archive disagrees with installed.db')
                source = next(c for c in manifest['components'] if c['name'] == name).get('version_source', {})
                require(source.get('kind') == 'osgeo4w-package' and source.get('package') == entry['package'],
                        name + ': package disagrees with runtime manifest')
            if kind == 'bundled':
                require(string(entry.get('parent')), name + ': bundled acquisition requires parent')
        require(set(entries) == set(versions), 'source components must cover every runtime manifest component')
        for name, entry in entries.items():
            seen = {name}
            current = entry
            while current['type'] == 'bundled':
                parent = current['parent']
                require(parent in entries and parent not in seen, name + ': invalid bundled parent or cycle')
                seen.add(parent)
                current = entries[parent]
                require(entry['status'] != 'verified' or current['status'] == 'verified',
                        name + ': verified bundled acquisition requires verified parent')
        return []
    except (ValueError, OSError, TypeError) as exc:
        return [str(exc)]


def main():
    errors = validate(ROOT)
    if errors:
        for error in errors:
            print('Runtime sources: ' + error, file=sys.stderr)
        return 1
    print('Runtime source metadata is consistent (offline; remote availability and bundle equivalence not checked).')
    return 0


if __name__ == '__main__':
    sys.exit(main())
