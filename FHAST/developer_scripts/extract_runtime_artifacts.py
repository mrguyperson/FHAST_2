"""Offline extraction of verified artifacts into isolated Windows payloads.

No installation, package overlays, postinstall actions or runtime execution.
External adapters intentionally support the demonstrated NSIS and single embedded
CAB MSI containers, not arbitrary installers. Tools must already be installed.
"""
import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading

sys.dont_write_bytecode = True
from fetch_runtime_artifacts import ROOT, WINDOWS_DEVICES, select, verify

NORMALIZED_TIME = 946684800
MAX_ENTRIES = 100_000
MAX_EXPANDED_BYTES = 4 * 1024**3
MAX_FILE_BYTES = 1024**3
TOOL_TIMEOUT = 600


@dataclass(frozen=True)
class Limits:
    entries: int = MAX_ENTRIES
    expanded: int = MAX_EXPANDED_BYTES
    file: int = MAX_FILE_BYTES


def safe_path(raw):
    """Canonical relative Windows-safe name; never repair ambiguous names."""
    if not raw or raw.startswith(('/', '\\')) or '\\' in raw:
        raise ValueError('Unsafe payload path: ' + repr(raw))
    while raw.startswith('./'):
        raw = raw[2:]
    raw = raw.rstrip('/')
    if raw in ('', '.'):
        return ''
    for part in raw.split('/'):
        if (not part or part in ('.', '..') or part.endswith(('.', ' '))
                or part.split('.')[0].upper() in WINDOWS_DEVICES
                or any(ord(c) < 32 or c in '<>:"\\|?*' for c in part)):
            raise ValueError('Unsafe payload path: ' + repr(raw))
    return raw


class Plan:
    """Complete namespace, including implicit parents, before any payload writes."""
    def __init__(self, limits=Limits()):
        self.limits = limits
        self.paths = {}  # relative name -> (directory/file, size)
        self.folded = {}
        self.explicit = set()
        self.total = 0

    def add(self, raw, kind, size=0):
        name = safe_path(raw)
        if not name and kind == 'directory':
            return
        if not name or kind not in ('directory', 'file'):
            raise ValueError('Unsupported payload member: ' + repr(raw))
        if type(size) is not int or size < 0 or size > self.limits.file:
            raise ValueError('Individual-file size limit/invalid size: ' + name)
        if name in self.explicit:
            raise ValueError('Duplicate payload path: ' + name)
        self.explicit.add(name)
        parts = name.split('/')
        for count in range(1, len(parts) + 1):
            path = '/'.join(parts[:count])
            value = (kind, size) if count == len(parts) else ('directory', 0)
            folded = path.casefold()
            if folded in self.folded and self.folded[folded] != path:
                raise ValueError('Case-insensitive payload collision: ' + path)
            self.folded[folded] = path
            if path in self.paths and self.paths[path] != value:
                raise ValueError('File/directory payload conflict: ' + path)
            self.paths[path] = value
        self.total += size if kind == 'file' else 0
        if len(self.paths) > self.limits.entries:
            raise ValueError('Payload entry-count limit exceeded')
        if self.total > self.limits.expanded:
            raise ValueError('Expanded-byte limit exceeded')


def selection(root=ROOT, component=None, package_set=None):
    result = []
    for entry in select(root, component, package_set=package_set):
        if package_set or entry.get('type') == 'osgeo4w-package':
            name = entry['name'] if package_set else entry['package']
            backend, destination = 'tar.bz2', 'osgeo4w-v1/' + name
        elif entry['name'] in ('r', 'netlogo', 'pandoc'):
            name = entry['name']
            backend = 'nsis' if name == 'r' else 'msi'
            destination = name
        else:
            raise ValueError('No supported extraction backend for ' + entry['name'])
        if safe_path(name) != name or '/' in name:
            raise ValueError('Unsafe component/package name: ' + name)
        result.append((entry, name, backend, destination))
    if len({r[3].casefold() for r in result}) != len(result):
        raise ValueError('Duplicate extraction destination')
    return result


def command(args):
    """Only trusted data-extraction tools; no shell, installer or network calls."""
    env = dict(os.environ, LC_ALL='C.UTF-8', TZ='UTC')
    try:
        result = subprocess.run(args, check=True, capture_output=True, text=True,
                                encoding='utf-8', env=env, timeout=TOOL_TIMEOUT)
    except (subprocess.SubprocessError, OSError) as exc:
        raise ValueError('Extraction tool failed: ' + str(exc)) from exc
    return result.stdout


def seven_listing(text, expected):
    header, separator, body = text.replace('\r\n', '\n').partition('\n----------\n')
    if not separator or f'Type = {expected}\n' not in header:
        raise ValueError('Unexpected 7-Zip container; expected ' + expected)
    records = []
    for block in body.strip().split('\n\n'):
        if not block:
            continue
        record = {}
        for line in block.splitlines():
            key, sep, value = line.partition(' = ')
            if not sep or key in record:
                raise ValueError('Malformed 7-Zip listing')
            record[key] = value
        if 'Path' not in record or 'Size' not in record:
            raise ValueError('Incomplete 7-Zip listing')
        if any(k in record for k in ('Symbolic Link', 'Hard Link')):
            raise ValueError('Links are unsupported')
        records.append(record)
    return header, records


def tool_version(tool):
    output = command([tool] if tool == '7z' else [tool, '--version'])
    pattern = r'7-Zip[^\n]*' if tool == '7z' else r'[^\n]*\b\d+\.\d+[^\n]*'
    match = re.search(pattern, output)
    if not match:
        raise ValueError('Cannot identify extraction tool version: ' + tool)
    return match.group().strip()


def extract_tar(snapshot, payload, limits):
    plan = Plan(limits)
    with tarfile.open(snapshot, 'r:bz2') as archive:
        members = []
        for member in archive:
            if member.sparse is not None or not (member.isfile() or member.isdir()):
                raise ValueError('Unsupported tar member: ' + member.name)
            plan.add(member.name, 'directory' if member.isdir() else 'file', member.size)
            members.append(member)
            # Bound root-only or otherwise non-payload headers too.
            if len(members) > limits.entries:
                raise ValueError('Archive entry-count limit exceeded')
        payload.mkdir()
        for name, (kind, _) in sorted(plan.paths.items()):
            if kind == 'directory':
                (payload / name).mkdir(parents=True, exist_ok=True)
        for member in members:
            if member.isfile():
                target = payload / safe_path(member.name)
                with archive.extractfile(member) as source, target.open('xb') as dest:
                    shutil.copyfileobj(source, dest, 1024 * 1024)
    return plan, {}


def nsis_member_size(snapshot, name, limit):
    """Older 7-Zip leaves terminal/shared solid-stream sizes blank in listings.

    Measure only those members through stdout, without filesystem extraction.
    Bound both bytes and elapsed time; never retain the member in memory.
    """
    args = ['7z', 'x', '-so', '-spd', str(snapshot), '--', name]
    with tempfile.TemporaryFile() as errors:
        try:
            process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=errors,
                                       env=dict(os.environ, LC_ALL='C.UTF-8', TZ='UTC'))
        except OSError as exc:
            raise ValueError('Extraction tool failed: ' + str(exc)) from exc
        timer = threading.Timer(TOOL_TIMEOUT, process.kill)
        timer.daemon = True
        timer.start()
        size = 0
        try:
            while True:
                chunk = process.stdout.read(min(1024 * 1024, max(1, limit - size + 1)))
                if not chunk:
                    break
                size += len(chunk)
                if size > limit:
                    raise ValueError('NSIS member exceeds file/expanded-byte limit: ' + name)
            if process.wait() != 0:
                raise ValueError('NSIS size preflight failed or timed out: ' + name)
            return size
        finally:
            timer.cancel()
            if process.poll() is None:
                process.kill()
            process.wait()
            process.stdout.close()


def extract_nsis(snapshot, payload, limits):
    version = tool_version('7z')
    _, records = seven_listing(command(['7z', 'l', '-slt', str(snapshot)]), 'Nsis')
    plan = Plan(limits)
    if len(records) > limits.entries:
        raise ValueError('Archive entry-count limit exceeded')
    for record in records:
        path = record['Path']
        if path == '$PLUGINSDIR' or path.startswith('$PLUGINSDIR/'):
            continue  # Installer helpers, including legitimate duplicate helper names.
        if path.startswith('$'):
            raise ValueError('Unsupported NSIS installer variable: ' + path)
        kind = 'directory' if record.get('Folder') == '+' or 'D' in record.get('Attributes', '') else 'file'
        safe_path(path)
        size = (int(record['Size']) if record['Size'] else
                nsis_member_size(snapshot, path, min(limits.file, limits.expanded - plan.total)))
        plan.add(path, kind, size)
    payload.mkdir()
    command(['7z', 'x', '-y', '-o' + str(payload), str(snapshot), '-xr!$PLUGINSDIR'])
    return plan, {'7z': version}


def msi_table(snapshot, table):
    lines = command(['msiinfo', 'export', str(snapshot), table]).splitlines()
    if len(lines) < 3:
        raise ValueError('Malformed MSI table: ' + table)
    columns = lines[0].split('\t')
    rows = []
    for line in lines[3:]:
        fields = line.split('\t')
        if len(fields) != len(columns):
            raise ValueError('Malformed MSI table row: ' + table)
        rows.append(dict(zip(columns, fields)))
    return rows


def unique_rows(rows, key):
    result = {}
    for row in rows:
        if row[key] in result:
            raise ValueError('Duplicate MSI table key: ' + row[key])
        result[row[key]] = row
    return result


def msi_plan(directories, components, files, limits):
    """Resolve the narrow logical path contract used by msiextract 0.103."""
    dirs = unique_rows(directories, 'Directory')
    comps = unique_rows(components, 'Component')
    unique_rows(files, 'File')

    def directory(key):
        parts, visited = [], set()
        while key:
            if key in visited:
                raise ValueError('MSI directory cycle')
            visited.add(key)
            row = dirs[key]
            name = row['DefaultDir'].split('|')[-1]
            # msiextract 0.103 special-cases only the 32-bit property.
            # ProgramFiles64Folder retains DefaultDir (e.g. PFiles), as tested.
            if key == 'ProgramFilesFolder':
                name = 'Program Files'
            if name not in ('.', 'SourceDir'):
                # Source:target directory forms are not needed by these inputs.
                if '/' in name or not safe_path(name):
                    raise ValueError('Unsupported MSI directory name')
                parts.append(name)
            key = row['Directory_Parent']
        return list(reversed(parts))

    plan = Plan(limits)
    for row in files:
        filename = row['FileName'].split('|')[-1]
        if '/' in filename or not safe_path(filename):
            raise ValueError('Unsupported MSI filename')
        path = '/'.join(directory(comps[row['Component_']]['Directory_']) + [filename])
        plan.add(path, 'file', int(row['FileSize']))
    return plan


def extract_msi(snapshot, payload, limits):
    versions = {tool: tool_version(tool) for tool in ('msiextract', 'msiinfo', '7z')}
    media = msi_table(snapshot, 'Media')
    if len(media) != 1 or not media[0]['Cabinet'].startswith('#'):
        raise ValueError('Only a single embedded MSI cabinet is supported; external cabinets rejected')
    cabinet = media[0]['Cabinet'][1:]
    if not cabinet or safe_path(cabinet) != cabinet or '/' in cabinet:
        raise ValueError('Unsafe MSI cabinet name')
    files = msi_table(snapshot, 'File')
    plan = msi_plan(msi_table(snapshot, 'Directory'), msi_table(snapshot, 'Component'), files, limits)
    listed = command(['msiextract', '-l', str(snapshot)]).splitlines()
    if len(listed) != len(set(listed)) or set(listed) != {p for p, (k, _) in plan.paths.items() if k == 'file'}:
        raise ValueError('MSI logical listing disagrees with File/Directory tables')
    header, records = seven_listing(command(['7z', 'l', '-slt', str(snapshot)]), 'Cab')
    if 'Type = Compound\n' not in header or f'Path = {cabinet}\n' not in header:
        raise ValueError('MSI embedded cabinet identity disagrees with Media table')
    actual = {}
    for record in records:
        identifier = record['Path']
        if identifier in actual or record.get('Folder') == '+':
            raise ValueError('Duplicate/unsupported MSI cabinet member')
        actual[identifier] = int(record['Size'])
    expected = {row['File']: int(row['FileSize']) for row in files}
    if actual != expected:
        raise ValueError('Incomplete or unmapped MSI cabinet payload')
    payload.mkdir()
    command(['msiextract', '-C', str(payload), str(snapshot)])
    return plan, versions


BACKENDS = {'tar.bz2': extract_tar, 'nsis': extract_nsis, 'msi': extract_msi}


def inspect_payload(payload, plan):
    if payload.is_symlink() or not payload.is_dir():
        raise ValueError('Unsafe payload root')
    actual = {}
    for parent, dirs, files in os.walk(payload, followlinks=False):
        for name in dirs + files:
            path = Path(parent) / name
            info = path.lstat()
            if stat.S_ISDIR(info.st_mode):
                value = ('directory', 0)
            elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                value = ('file', info.st_size)
            else:
                raise ValueError('Extracted link/special node: ' + str(path))
            actual[path.relative_to(payload).as_posix()] = value
    if actual != plan.paths:
        raise ValueError('Extracted payload does not exactly match approved paths/types/sizes')


def normalize(tree):
    for parent, dirs, files in os.walk(tree, topdown=False):
        for name in files:
            path = Path(parent) / name
            path.chmod(0o644)
            os.utime(path, (NORMALIZED_TIME, NORMALIZED_TIME))
        Path(parent).chmod(0o755)
        os.utime(parent, (NORMALIZED_TIME, NORMALIZED_TIME))


def ensure_parent(output, parent, create=False):
    path = output
    for part in ('',) + parent.relative_to(output).parts:
        path = path / part
        if path.is_symlink() or (path.exists() and not path.is_dir()):
            raise ValueError('Unsafe output parent: ' + str(path))
        if create:
            path.mkdir(parents=True, exist_ok=True)


def extract_one(cache, output, selected, limits):
    entry, name, backend, relative = selected
    final = output / relative
    ensure_parent(output, final.parent, create=True)
    lock = final.parent / ('.' + final.name + '.fhast-extraction.lock')
    try:
        fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise ValueError('Extraction lock already exists: ' + str(lock)) from exc
    os.close(fd)
    try:
        if os.path.lexists(final):
            raise ValueError('Extraction destination already exists: ' + str(final))
        with tempfile.TemporaryDirectory(prefix='.' + final.name + '-', dir=final.parent) as temporary:
            work = Path(temporary)
            snapshot = work / entry['filename']
            shutil.copyfile(cache / entry['filename'], snapshot)
            verify(snapshot, entry)  # Verify the private bytes actually supplied to the backend.
            completed = work / 'completed'
            completed.mkdir()
            plan, versions = BACKENDS[backend](snapshot, completed / 'payload', limits)
            inspect_payload(completed / 'payload', plan)
            receipt = dict(schema_version=1, name=name, filename=entry['filename'],
                           checksum={k: entry['checksum'][k] for k in ('algorithm', 'value')},
                           backend=backend, file_count=sum(k == 'file' for k, _ in plan.paths.values()),
                           directory_count=sum(k == 'directory' for k, _ in plan.paths.values()),
                           file_bytes=plan.total, normalized_timestamp=NORMALIZED_TIME, tools=versions)
            (completed / 'receipt.json').write_bytes((json.dumps(receipt, indent=2, sort_keys=True) + '\n').encode('utf-8'))
            normalize(completed)
            ensure_parent(output, final.parent)
            if os.path.lexists(final):
                raise ValueError('Extraction destination appeared during extraction: ' + str(final))
            # All cooperating writers hold this exclusive lock. Same-filesystem rename.
            completed.rename(final)
    finally:
        lock.unlink()


def extract(input_dir, output_dir, component=None, dry_run=False, *, package_set=None, root=ROOT, limits=Limits()):
    selected = selection(root, component, package_set)
    cache, output, repo = Path(input_dir).resolve(), Path(output_dir).resolve(), root.resolve()
    if any(p == repo or repo in p.parents for p in (cache, output)):
        raise ValueError('Input/output directories must be outside the repository/runtime bundle')
    if cache == output or cache in output.parents or output in cache.parents:
        raise ValueError('Input/output roots must not overlap')
    if not cache.is_dir():
        raise ValueError('Input cache does not exist: ' + str(cache))
    # Preflight the entire selection before creating output or invoking tools.
    for entry, _, backend, relative in selected:
        source = cache / entry['filename']
        if source.is_symlink() or not source.is_file():
            raise ValueError('Missing/nonregular/symlink artifact: ' + str(source))
        verify(source, entry)
        final = output / relative
        ensure_parent(output, final.parent)
        if os.path.lexists(final):
            raise ValueError('Extraction destination already exists: ' + str(final))
    for item in selected:
        if dry_run:
            print(f'Would extract {item[0]["filename"]} using {item[2]} -> {output / item[3]}')
        else:
            extract_one(cache, output, item, limits)
            print('Extracted ' + str(output / item[3]))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-dir', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--component')
    group.add_argument('--package-set', choices=['osgeo4w-v1'])
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args(argv)
    try:
        extract(args.input_dir, args.output_dir, args.component, args.dry_run, package_set=args.package_set)
    except (OSError, ValueError, KeyError, tarfile.TarError) as exc:
        print('Runtime extraction failed: ' + str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
