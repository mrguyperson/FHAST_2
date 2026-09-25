"""Download verified standalone artifacts or an explicit validated package set."""
import argparse
import hashlib
from http.client import HTTPException
import os
from pathlib import Path, PurePosixPath
import sys
import tempfile
from urllib.request import HTTPRedirectHandler, Request, build_opener

sys.dont_write_bytecode = True
from check_runtime_sources import ROOT, SOURCES, read_json, validate, web_url
from check_osgeo4w_package_lock import LOCK, validate as validate_package_lock


USER_AGENT = 'FHAST-runtime-fetcher/1.0 (+https://github.com/mrguyperson/FHAST_2)'


WINDOWS_DEVICES = {'CON', 'PRN', 'AUX', 'NUL', 'CONIN$', 'CONOUT$'} | {
    prefix + suffix for prefix in ('COM', 'LPT') for suffix in '123456789¹²³'
}


class HTTPSRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        web_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def open_download(url):
    """The only network boundary; fixtures can replace this callable in tests."""
    web_url(url)
    request = Request(url, headers={'User-Agent': USER_AGENT})
    return build_opener(HTTPSRedirects()).open(request, timeout=60)


def eligible(entry):
    return entry['status'] == 'verified' and entry['type'] in {'artifact', 'osgeo4w-package'}


def select(root, component=None, *, package_set=None):
    if package_set is not None:
        if component is not None:
            raise ValueError('component and package_set are mutually exclusive')
        if package_set != 'osgeo4w-v1':
            raise ValueError('Unknown package set: ' + package_set)
        errors = validate_package_lock(root)
        if errors:
            raise ValueError('Invalid OSGeo4W package lock: ' + '; '.join(errors))
        selected = [dict(name=p['name'], filename=PurePosixPath(p['source_path']).name,
                         url=p['url'], checksum=p['checksum'], size=p['size'])
                    for p in read_json(root / LOCK)['packages']]
    else:
        selected = select_components(root, component)
    check_filenames(selected)
    return selected


def select_components(root, component):
    errors = validate(root)
    if errors:
        raise ValueError('Invalid runtime source metadata: ' + '; '.join(errors))
    entries = read_json(root / SOURCES)['components']
    if component is not None:
        entries = [entry for entry in entries if entry['name'] == component]
        if not entries:
            raise ValueError('Unknown component: ' + component)
        entry = entries[0]
        if not eligible(entry):
            reason = '; '.join(entry.get('unresolved', ['Not a standalone artifact/package.']))
            raise ValueError(f"{component}: status={entry['status']}, type={entry['type']}; cannot fetch: {reason}")
    return [entry for entry in entries if eligible(entry)]


def check_filenames(selected):
    names = set()
    for entry in selected:
        filename = entry['filename']
        # Windows aliases/devices must not escape or collide in the output directory.
        if (filename.split('.')[0].upper() in WINDOWS_DEVICES or filename.endswith(('.', ' '))
                or any(ord(c) < 32 or c in '<>"|?*' for c in filename)):
            raise ValueError('Unsafe artifact filename: ' + filename)
        if filename.casefold() in names:
            raise ValueError('Duplicate artifact filename: ' + filename)
        names.add(filename.casefold())


def digest_file(path, algorithm):
    digest = hashlib.new(algorithm)
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def verify(path, entry):
    if 'size' in entry and path.stat().st_size != entry['size']:
        raise ValueError(f"{entry['name']}: size mismatch for {path.name}: "
                         f"expected {entry['size']}, got {path.stat().st_size}; file not accepted")
    checksum = entry['checksum']
    actual = digest_file(path, checksum['algorithm'])
    if actual.lower() != checksum['value'].lower():
        raise ValueError(f"{entry['name']}: checksum mismatch for {path.name}: "
                         f"expected {checksum['value']}, got {actual}; file not accepted")


def fetch(output_dir, component=None, dry_run=False, *, package_set=None, root=ROOT, opener=open_download):
    entries = select(root, component, package_set=package_set)
    output_dir = Path(output_dir).resolve()
    # Acquisition output must stay separate from the checked-in bundle.
    if output_dir == root.resolve() or root.resolve() in output_dir.parents:
        raise ValueError('Output directory must be outside the repository/runtime bundle')
    if dry_run:
        for entry in entries:
            print(f"Would fetch {entry['name']}: {entry['url']} -> {output_dir / entry['filename']}")
        return
    output_dir.mkdir(parents=True, exist_ok=True)
    for entry in entries:
        target = output_dir / entry['filename']
        if target.is_symlink():
            raise ValueError('Refusing artifact symlink: ' + str(target))
        if target.exists():
            verify(target, entry)
            print(f"Reused {entry['name']}: {target}")
            continue
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=output_dir, prefix='.fhast-', suffix='.part', delete=False) as stream:
                temporary = Path(stream.name)
                with opener(entry['url']) as response:
                    for chunk in iter(lambda: response.read(1024 * 1024), b''):
                        stream.write(chunk)
            verify(temporary, entry)
            # Hard-link publication is atomic and refuses to overwrite a file created
            # concurrently. Both paths are on the same filesystem; fail if unsupported.
            os.link(temporary, target)
            print(f"Fetched {entry['name']}: {target}")
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', required=True, type=Path,
                        help='artifact cache outside the repository (required)')
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument('--component', help='one verified standalone component; default: all eligible')
    selection.add_argument('--package-set', choices=['osgeo4w-v1'],
                           help='explicitly fetch the validated 143-package OSGeo4W v1 lock')
    parser.add_argument('--dry-run', action='store_true', help='validate and show selection without writing or downloading')
    args = parser.parse_args(argv)
    try:
        fetch(args.output_dir, args.component, args.dry_run, package_set=args.package_set)
    except (OSError, ValueError, HTTPException) as exc:
        print('Runtime fetch failed: ' + str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
