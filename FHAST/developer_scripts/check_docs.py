"""Offline checks for an explicit set of FHAST documentation (stdlib only).

Run from the repository root: python3 FHAST/developer_scripts/check_docs.py
No recursive discovery: dependency documentation must not enter this scope.
This is a small link/path checker, not a full Markdown or semantic validator.
"""

from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[2]
DOCUMENTS = (
    "AGENTS.md",                      # Current contributor guidance.
    "FHAST/README.md",                # Project overview.
    "FHAST/FHAST_App/README.md",       # Mixed project/historical deployment notes.
)
MAP_HEADING = "## Verified repository map"
# Simple inline/image links and reference definitions; spaces use <destination>.
# Nested parentheses in destinations should be percent-encoded or angle-wrapped.
DESTINATION = r'<([^>\n]*)>|([^\s<>]+?)'
INLINE_LINK = re.compile(
    r'!?\[[^\]\n]*\]\(\s*(?:' + DESTINATION
    + r')(?:\s+"[^"\n]*"|\s+\'[^\'\n]*\')?\s*\)'
)
REFERENCE = re.compile(
    r'^ {0,3}\[[^\]\n]+\]:\s*(?:<([^>\n]*)>|(\S+))'
)
INLINE_CODE = re.compile(r'(`+).*?\1')


def sparse_paths(root):
    """Return index paths omitted by sparse checkout, without reading blobs.

    Only skip-worktree entries qualify: ordinary unstaged deletions must fail.
    Git does not track directories, so infer them from qualifying descendants.
    """
    def git(*arguments):
        return subprocess.run(
            ['git', '-C', str(root), *arguments],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
        ).stdout

    try:
        top = git('rev-parse', '--show-toplevel').decode('utf-8').strip()
        if Path(top).resolve() != root:
            raise RuntimeError('checker must be inside the FHAST Git worktree root')
        enabled = git('config', '--bool', '--default', 'false',
                      '--get', 'core.sparseCheckout').strip() == b'true'
        files = set()
        directories = set()
        if enabled:
            for entry in git('ls-files', '-t', '-z').split(b'\0'):
                if entry.startswith(b'S '):
                    path = entry[2:].decode('utf-8')
                    files.add(path)
                    parent = Path(path).parent
                    while parent != Path('.'):
                        directories.add(parent.as_posix())
                        parent = parent.parent
        return files, directories
    except (OSError, subprocess.CalledProcessError, UnicodeError) as error:
        raise RuntimeError(
            'documentation checker requires Git and a readable FHAST Git '
            'worktree/index; run it inside a Git checkout'
        ) from error


def prose_lines(text):
    """Keep line numbers, excluding fenced/indented code and HTML comments."""
    text = re.sub(r'<!--[\s\S]*?-->',
                  lambda match: '\n' * match.group().count('\n'), text)
    fence = None
    for number, line in enumerate(text.splitlines(), 1):
        marker = re.match(r'^ {0,3}(`{3,}|~{3,})', line)
        if fence:
            if re.fullmatch(r' {0,3}' + re.escape(fence[0])
                            + '{' + str(len(fence)) + r',}\s*', line):
                fence = None
            continue
        if marker:
            fence = marker.group(1)
            continue
        if not line.startswith(('    ', '\t')):
            yield number, line


def check(root):
    root = root.resolve()
    omitted_files, omitted_directories = sparse_paths(root)
    errors = []
    count = 0

    def check_path(document, line, value, base, directory=False):
        nonlocal count
        count += 1
        target = (base / value).resolve()
        if not target.is_relative_to(root):
            errors.append(f'{document}:{line}: path leaves repository: {value}')
        elif not target.exists():
            relative = target.relative_to(root).as_posix()
            if relative in omitted_directories:
                return
            if relative in omitted_files:
                if directory:
                    errors.append(f'{document}:{line}: expected directory: {value}')
                return
            errors.append(f'{document}:{line}: missing path: {value}')
        elif directory and not target.is_dir():
            errors.append(f'{document}:{line}: expected directory: {value}')

    for document in DOCUMENTS:
        source = root / document
        if not source.is_file():
            errors.append(f'{document}: missing documentation file')
            continue
        lines = list(prose_lines(source.read_text(encoding='utf-8')))
        if document == 'AGENTS.md':
            in_map = False
            rows = 0
            for number, line in lines:
                if line.startswith('## '):
                    in_map = line == MAP_HEADING
                if not in_map or not line.startswith('|'):
                    continue
                cell = line.split('|')[1].strip()
                if cell == 'Path' or re.fullmatch(r'[-: ]+', cell):
                    continue
                rows += 1
                if not re.fullmatch(r'`[^`]+`(?:,\s*`[^`]+`)*', cell):
                    errors.append(f'{document}:{number}: map Path cell must '
                                  'contain comma-separated backtick paths')
                    continue
                for value in re.findall(r'`([^`]+)`', cell):
                    if '\\' in value or ':' in value or value.startswith('/'):
                        errors.append(f'{document}:{number}: map path must be '
                                      f'repository-relative with / separators: {value}')
                        continue
                    check_path(document, number, value, root, value.endswith('/'))
            if not rows:
                errors.append(f'{document}: missing or empty {MAP_HEADING!r} table')

        for number, line in lines:
            line = INLINE_CODE.sub('', line)
            definition = REFERENCE.match(line)
            links = [definition] if definition else INLINE_LINK.finditer(line)
            for link in links:
                value = link.group(1) if link.group(1) is not None else link.group(2)
                # Windows drive/UNC paths are historical machine-local examples.
                if re.match(r'^[A-Za-z]:[\\/]', value) or value.startswith('\\\\'):
                    continue
                try:
                    url = urlsplit(value)
                except ValueError:
                    errors.append(f'{document}:{number}: invalid link: {value}')
                    continue
                if url.scheme or url.netloc or not url.path:
                    continue  # External URLs and fragment-only links: no network.
                path = unquote(url.path)
                if path.startswith('/') or '\\' in path:
                    errors.append(f'{document}:{number}: use a relative Markdown '
                                  f'link with / separators: {value}')
                    continue
                check_path(document, number, path, source.parent, path.endswith('/'))
    return errors, count


def main():
    try:
        errors, count = check(ROOT)
    except RuntimeError as error:
        print(f'Documentation check failed: {error}', file=sys.stderr)
        return 1
    if errors:
        print('\n'.join(errors), file=sys.stderr)
        print(f'Documentation check failed: {len(errors)} error(s).', file=sys.stderr)
        return 1
    print(f'Documentation check passed: {len(DOCUMENTS)} documents, '
          f'{count} local path/link targets checked.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
