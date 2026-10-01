"""Assemble verified extraction inputs into an incomplete, runtime-only layout.

Offline: no archives, installers, lifecycle scripts, or application execution.
Schema-1 input receipts identify artifacts and aggregate counts, not payload
contents. Use a trusted extraction root; this tool cannot authenticate its bytes.
The required FHAST JDK is unsupported. This is not a runnable FHAST distribution.
"""
import argparse
from contextlib import contextmanager
import ctypes
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import tempfile

sys.dont_write_bytecode = True
from extract_runtime_artifacts import NORMALIZED_TIME, ROOT, normalize, safe_path, selection
from check_osgeo4w_package_lock import unique_object


PROFILE = {'name': 'fhast-verified-runtime-inputs', 'version': 1}
RECEIPT = 'layout-receipt.json'
MAPPINGS = {
    'r': ('', 'FHAST/FHAST_App/dist/R-Portable'),
    'netlogo': ('PFiles/NetLogo 6.2.2', 'FHAST/FHAST_App/dist/NetLogo 6.2.2'),
    'pandoc': ('Pandoc', 'FHAST/FHAST_App/dist/Pandoc'),
}
DIRECTORY_ALIASES = {
    'apps/Python37/lib': 'apps/Python37/Lib',
    'apps/qt5': 'apps/Qt5',
}
# Reviewed against the accepted 146-input corpus. These authorize providers,
# never arbitrary overwrites or conversion to the reference bundle's newlines.
# Winners are selected by the validated lock order, not this dictionary's order.
DIFFERING_OVERLAPS = {
    'include/netcdf.h': frozenset(('hdf4', 'netcdf')),
    'apps/Qt5/qsci/api/python/PyQt5.api': frozenset(('qscintilla-qt5', 'pyqt5')),
    'bin/libpng16.dll': frozenset(('libpng-vc14', 'libpng')),
    **{'apps/Python37/Lib/site-packages/pkg_resources/' + suffix:
       frozenset(('python3-core', 'python3-setuptools')) for suffix in (
           '__init__.py', '_vendor/appdirs.py', '_vendor/pyparsing.py',
           'extern/__init__.py', 'py31compat.py')},
}
OSGEO_TOP = {p: 'directory' for p in ('apps', 'bin', 'cmake', 'etc', 'include', 'lib', 'share')}
OSGEO_TOP.update({'OSGeo4W.bat': 'file', 'OSGeo4W.ico': 'file'})
PANDOC_FILES = {'pandoc.exe', 'COPYING.rtf', 'COPYRIGHT.txt', "Pandoc User's Guide.html"}
RECEIPT_FIELDS = {'schema_version', 'name', 'filename', 'checksum', 'backend',
                  'file_count', 'directory_count', 'file_bytes', 'normalized_timestamp', 'tools'}


@dataclass(frozen=True)
class Node:
    kind: str
    size: int = 0
    sha256: str = ''
    source: Path = None


def require(condition, message):
    if not condition:
        raise ValueError(message)


def path_name(name):
    require(safe_path(name) == name and bool(name), 'Unsafe/noncanonical path: ' + repr(name))
    return name


def node_kind(info):
    # Windows junctions and other reparse points must not bypass symlink checks.
    if getattr(info, 'st_file_attributes', 0) & 0x400:
        raise ValueError('Unsupported reparse point')
    if stat.S_ISDIR(info.st_mode):
        return 'directory'
    if stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
        return 'file'
    raise ValueError('Unsupported link, hardlink, or special node')


def safe_directory(path):
    for parent in list(reversed(path.parents)) + [path]:
        require(node_kind(parent.lstat()) == 'directory', 'Unsafe directory: ' + str(parent))


def absolute_path(value):
    path = Path(value)
    require('..' not in path.parts, 'Traversal in input/output root: ' + str(path))
    return path.absolute()


def overlaps(a, b):
    return a == b or a in b.parents or b in a.parents


def roots(input_dir, output_dir, root):
    source, output, repo = absolute_path(input_dir), absolute_path(output_dir), root.resolve()
    require(not overlaps(source, repo) and not overlaps(output, repo),
            'Input/output must be outside the repository and must not contain it')
    require(not overlaps(source, output), 'Input/output roots must not overlap')
    safe_directory(source)
    # Require an existing parent so failure/dry-run cannot leave created parents.
    safe_directory(output.parent)
    path_name(output.name)
    require(not os.path.lexists(output), 'Output already exists: ' + str(output))
    return source, output


@contextmanager
def regular_file(path):
    """Refuse special nodes before opening, including a replacement at open()."""
    safe_directory(path.parent)
    before = path.lstat()
    require(node_kind(before) == 'file', 'Expected regular file: ' + str(path))
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
    flags |= getattr(os, 'O_BINARY', 0)
    with os.fdopen(os.open(path, flags), 'rb') as stream:
        opened = os.fstat(stream.fileno())
        require(node_kind(opened) == 'file' and
                (opened.st_dev, opened.st_ino) == (before.st_dev, before.st_ino),
                'File changed while opening: ' + str(path))
        yield stream
        after = os.fstat(stream.fileno())
        require(node_kind(after) == 'file' and
                (opened.st_size, opened.st_mtime_ns) == (after.st_size, after.st_mtime_ns),
                'File changed while reading: ' + str(path))


def fingerprint(path, destination=None):
    digest, size = hashlib.sha256(), 0
    with regular_file(path) as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
            size += len(block)
            if destination is not None:
                destination.write(block)
    return size, digest.hexdigest()


def children(path, expected):
    safe_directory(path)
    actual = {p.name for p in path.iterdir()}
    require(actual == set(expected),
            f'Unexpected/missing entries in {path}: missing={sorted(set(expected) - actual)}, '
            f'extra={sorted(actual - set(expected))}')
    for name, kind in expected.items():
        require(node_kind((path / name).lstat()) == kind, 'Wrong node type: ' + str(path / name))


def inspect_payload(payload):
    safe_directory(payload)
    tree, folded, pending = {}, {}, [payload]
    while pending:
        parent = pending.pop()
        safe_directory(parent)
        for path in sorted(parent.iterdir()):
            name = path_name(path.relative_to(payload).as_posix())
            require(name.casefold() not in folded, 'Case-insensitive input collision: ' + name)
            folded[name.casefold()] = name
            kind = node_kind(path.lstat())
            if kind == 'directory':
                tree[name] = Node(kind)
                pending.append(path)
            else:
                size, digest = fingerprint(path)
                tree[name] = Node(kind, size, digest, path)
    return tree


def read_input(source, selected):
    entry, name, backend, relative = selected
    folder = source / relative
    children(folder, {'receipt.json': 'file', 'payload': 'directory'})
    with regular_file(folder / 'receipt.json') as stream:
        raw = stream.read(65537)
    require(len(raw) <= 65536, 'Oversized extraction receipt: ' + relative)
    receipt = json.loads(raw.decode('utf-8'), object_pairs_hook=unique_object)
    require(isinstance(receipt, dict) and set(receipt) == RECEIPT_FIELDS,
            'Malformed extraction receipt fields: ' + relative)
    expected = dict(schema_version=1, name=name, filename=entry['filename'], backend=backend,
                    checksum={k: entry['checksum'][k] for k in ('algorithm', 'value')},
                    normalized_timestamp=NORMALIZED_TIME)
    for key, value in expected.items():
        require(type(receipt[key]) is type(value) and receipt[key] == value,
                f'Receipt {key} mismatch: {relative}')
    for key in ('file_count', 'directory_count', 'file_bytes'):
        require(type(receipt[key]) is int and receipt[key] >= 0,
                f'Invalid receipt {key}: {relative}')
    tools = receipt['tools']
    expected_tools = {'tar.bz2': set(), 'nsis': {'7z'}, 'msi': {'7z', 'msiextract', 'msiinfo'}}[backend]
    require(isinstance(tools, dict) and set(tools) == expected_tools and
            all(isinstance(v, str) and v.strip() for v in tools.values()),
            'Malformed receipt tool identities: ' + relative)
    tree = inspect_payload(folder / 'payload')
    counts = dict(file_count=sum(n.kind == 'file' for n in tree.values()),
                  directory_count=sum(n.kind == 'directory' for n in tree.values()),
                  file_bytes=sum(n.size for n in tree.values()))
    for key, value in counts.items():
        require(receipt[key] == value, f'Receipt {key} disagrees with payload: {relative}')
    identity = dict(input=relative, receipt_sha256=hashlib.sha256(raw).hexdigest(),
                    filename=entry['filename'], checksum=expected['checksum'], backend=backend)
    return tree, identity


def canonical_osgeo(name):
    for alias, canonical in DIRECTORY_ALIASES.items():
        prefix = '/'.join(name.split('/')[:len(alias.split('/'))])
        if prefix.casefold() == canonical.casefold():
            require(prefix in (alias, canonical), 'Unrecognized directory-case alias: ' + prefix)
            return canonical + name[len(prefix):]
    return name


def mapped_tree(name, tree):
    if name not in MAPPINGS:
        for path, node in tree.items():
            top = path.split('/')[0]
            require(top in OSGEO_TOP, 'Unsupported OSGeo4W root path: ' + path)
            if path == top:
                require(node.kind == OSGEO_TOP[top], 'Wrong OSGeo4W root type: ' + path)
            mapped = canonical_osgeo(path)
            if mapped in DIRECTORY_ALIASES.values():
                require(node.kind == 'directory', 'Directory alias used for a file: ' + path)
            yield mapped, node
        return
    wrapper, target = MAPPINGS[name]
    tops = {p: n.kind for p, n in tree.items() if '/' not in p}
    if name == 'r':
        require(tops == {'App': 'directory', 'Other': 'directory',
                         'R-Portable.exe': 'file', 'help.html': 'file'},
                'Unexpected R Portable payload root shape')
    else:
        parts = wrapper.split('/')
        for depth in range(len(parts)):
            prefix = '/'.join(parts[:depth])
            siblings = {p: n.kind for p, n in tree.items()
                        if p.rpartition('/')[0] == prefix}
            require(siblings == {'/'.join(parts[:depth + 1]): 'directory'},
                    'Unexpected ' + name + ' wrapper shape/sibling')
        if name == 'pandoc':
            require({p: n.kind for p, n in tree.items()} ==
                    {'Pandoc': 'directory', **{'Pandoc/' + f: 'file' for f in PANDOC_FILES}},
                    'Unexpected Pandoc payload files')
    yield target, Node('directory')
    for path, node in tree.items():
        if wrapper:
            if not path.startswith(wrapper + '/'):
                continue  # Only the explicitly validated wrapper directories.
            path = path[len(wrapper) + 1:]
        yield target + '/' + path, node


class Layout:
    def __init__(self):
        self.nodes, self.folded, self.providers = {}, {}, {}

    def add(self, path, node, provider):
        path_name(path)
        parts = path.split('/')
        require(parts[0].casefold() != RECEIPT.casefold(), 'Reserved layout receipt path')
        # Explicit and implicit parents use the same checks without recursion.
        for depth in range(1, len(parts) + 1):
            member = '/'.join(parts[:depth])
            current = node if depth == len(parts) else Node('directory')
            key = member.casefold()
            require(key not in self.folded or self.folded[key] == member,
                    'Unrecognized case-insensitive output alias: ' + member)
            self.folded[key] = member
            if member in self.nodes:
                require(self.nodes[member].kind == current.kind, 'File/directory conflict: ' + member)
            self.nodes[member] = current  # Files: last provider in the validated lock order.
        if node.kind == 'file':
            self.providers.setdefault(path, []).append((provider, node))

    def collisions(self):
        decisions = []
        for path, candidates in sorted(self.providers.items()):
            providers = [provider for provider, _ in candidates]
            allowed = DIFFERING_OVERLAPS.get(path)
            if allowed is not None:
                require(set(providers) == allowed and len(providers) == len(allowed),
                        'Approved overlap provider-set changed: ' + path)
            if len(candidates) < 2:
                continue
            identical = len({(n.size, n.sha256) for _, n in candidates}) == 1
            require(identical or allowed is not None, 'Undeclared differing overlap: ' + path)
            decisions.append(dict(path=path, providers=providers, winner=providers[-1],
                                  identical=identical))
        return decisions


def tree_digest(nodes):
    """SHA-256 of sorted compact UTF-8 JSON arrays, one LF-terminated record each.

    Directory: [path,"directory",0]. File: [path,"file",size,sha256].
    Sorting is by the canonical, case-preserving relative path (Python str order).
    """
    digest = hashlib.sha256()
    for path, node in sorted(nodes.items()):
        row = [path, node.kind, node.size]
        if node.kind == 'file':
            row.append(node.sha256)
        digest.update((json.dumps(row, ensure_ascii=False, separators=(',', ':')) + '\n').encode('utf-8'))
    return digest.hexdigest()


def preflight(source, root):
    selected = selection(root, package_set='osgeo4w-v1')
    packages = [name for _, name, _, _ in selected]
    require(len(packages) == 143, 'Profile requires exactly 143 locked packages')
    for component in MAPPINGS:
        standalone = selection(root, component=component)
        backend = 'nsis' if component == 'r' else 'msi'
        require(len(standalone) == 1 and standalone[0][1:] == (component, backend, component),
                'Unsupported standalone extraction identity: ' + component)
        selected.extend(standalone)
    children(source, {p: 'directory' for p in ('osgeo4w-v1', *MAPPINGS)})
    children(source / 'osgeo4w-v1', {p: 'directory' for p in packages})
    layout, identities = Layout(), []
    for item in selected:
        tree, identity = read_input(source, item)
        identities.append(identity)
        for path, node in mapped_tree(item[1], tree):
            layout.add(path, node, item[1])
    decisions = layout.collisions()  # Complete namespace validated before any writes.
    receipt = dict(schema_version=1, profile=PROFILE, normalized_timestamp=NORMALIZED_TIME,
                   package_order=packages,
                   mappings={n: {'source': n + '/payload' + ('/' + w if w else ''), 'target': t}
                             for n, (w, t) in MAPPINGS.items()},
                   directory_aliases=DIRECTORY_ALIASES, inputs=identities, collisions=decisions,
                   unsupported_required_components=['fhast-jdk'],
                   input_integrity='schema-1 artifact identities and aggregate counts; payloads not authenticated',
                   file_count=sum(n.kind == 'file' for n in layout.nodes.values()),
                   directory_count=sum(n.kind == 'directory' for n in layout.nodes.values()),
                   file_bytes=sum(n.size for n in layout.nodes.values()),
                   tree_sha256=tree_digest(layout.nodes))
    return layout, receipt


def no_replace_publisher():
    """Native atomic directory publication, never check-then-overwrite rename.

    Linux requires libc renameat2 and filesystem RENAME_NOREPLACE support.
    Windows os.rename refuses an existing destination. No unsafe fallback.
    """
    if os.name == 'nt':
        return os.rename
    require(sys.platform.startswith('linux'), 'Atomic no-replace publication supports Linux and Windows only')
    libc = ctypes.CDLL(None, use_errno=True)
    require(hasattr(libc, 'renameat2'), 'Atomic publication requires libc renameat2')
    rename = libc.renameat2
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int

    def publish(source, destination):
        if rename(-100, os.fsencode(source), -100, os.fsencode(destination), 1) != 0:
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error), str(destination))
    return publish


def copy_file(node, destination):
    with destination.open('xb') as stream:
        actual = fingerprint(node.source, stream)
    require(actual == (node.size, node.sha256), 'Input changed since preflight: ' + str(node.source))


def assemble(input_dir, output_dir, dry_run=False, *, root=ROOT):
    source, output = roots(input_dir, output_dir, root)
    layout, receipt = preflight(source, root)
    if dry_run:
        return receipt
    publish = no_replace_publisher()
    roots(source, output, root)
    with tempfile.TemporaryDirectory(prefix='.' + output.name + '.fhast-layout-', dir=output.parent) as temporary:
        completed = Path(temporary) / 'completed'
        completed.mkdir()
        for path, node in sorted(layout.nodes.items()):
            target = completed / path
            if node.kind == 'directory':
                target.mkdir()
            else:
                copy_file(node, target)
        (completed / RECEIPT).write_bytes((json.dumps(receipt, indent=2, sort_keys=True) + '\n').encode('utf-8'))
        normalize(completed)
        roots(source, output, root)
        publish(completed, output)
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-dir', type=Path, required=True, help='trusted, complete extraction root outside repository')
    parser.add_argument('--output-dir', type=Path, required=True, help='absent staging root with an existing parent')
    parser.add_argument('--dry-run', action='store_true', help='validate and plan without any writes')
    args = parser.parse_args(argv)
    try:
        receipt = assemble(args.input_dir, args.output_dir, args.dry_run)
    except (OSError, ValueError, KeyError) as exc:
        print('Runtime layout failed: ' + str(exc), file=sys.stderr)
        return 1
    print(('Validated' if args.dry_run else 'Assembled') +
          f" {len(receipt['inputs'])} inputs: {receipt['file_count']} files, "
          f"{receipt['directory_count']} directories, {receipt['file_bytes']} bytes.")
    print('fhast-jdk is unsupported; this is not a complete runnable FHAST runtime.')
    print('Schema-1 extraction receipts do not authenticate payload contents.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
