"""Export committed source, optionally completed episodes, after a fail-closed scan.

No queue/database access, source edits, history rewriting, or Docker operations.
Run with python -m tools.export_release. All diagnostics omit matched values.
"""
import argparse
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import socket
import subprocess
import tarfile
import tempfile
from urllib.parse import parse_qsl, urlencode, urlsplit

from tools.secrets import load_opencode_key

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_CREDENTIALS = ('MarvelsGrantMan136:test1234', 'admin:admin1234')
HEADER_NAMES = {'authorization', 'proxy-authorization', 'x-api-key'}
REDACTED = '[REDACTED]'
KEY_PATTERN = re.compile(r'\bsk-[A-Za-z0-9_-]{4,}')
BEARER_PATTERN = re.compile(r'\bBearer\s+([A-Za-z0-9_./+=-]{4,})', re.I)
LITERAL_HEADER = re.compile(
    r'''["'](?:authorization|proxy-authorization|x-api-key)["']\s*:\s*["']([^"'\r\n]+)["'](?!\s*\+)''', re.I)
RAW_HEADER = re.compile(r'(?im)^\s*(?:authorization|proxy-authorization|x-api-key)\s*:\s*([^\r\n]+)')


class ExportError(RuntimeError):
    """Only fixed, non-secret messages may cross this boundary."""


def git(root, *args):
    result = subprocess.run(['git', '-C', str(root), *args], capture_output=True)
    if result.returncode:
        raise ExportError('Git operation failed; export aborted.')
    return result.stdout


def header_secret(value):
    return isinstance(value, str) and value.strip() not in ('', REDACTED)


def structured_secret(value):
    if isinstance(value, dict):
        if str(value.get('name', '')).lower() in HEADER_NAMES and header_secret(value.get('value')):
            return True
        for name, child in value.items():
            if name.lower() in HEADER_NAMES and header_secret(child):
                return True
            if structured_secret(child):
                return True
    elif isinstance(value, list):
        # Also recognize serialized (header-name, header-value) pairs.
        if len(value) == 2 and isinstance(value[0], str) and value[0].lower() in HEADER_NAMES:
            if header_secret(value[1]):
                return True
        return any(structured_secret(child) for child in value)
    return False


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for name, child in value.items():
            yield name
            yield from strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from strings(child)


def scan_secrets(data, key):
    """Scan bytes as well as decoded JSON/JSONL; never include a match in errors."""
    if not key:
        raise ExportError('Credential unavailable; export aborted without a complete scan.')
    if key.encode() in data:
        raise ExportError('Secret detected; export aborted.')
    text = data.decode('utf-8', errors='replace')
    # These exact public benchmark credentials are deliberately permitted.
    # The loaded key check above always takes precedence over this exception.
    for credential in PUBLIC_CREDENTIALS:
        text = text.replace(credential, REDACTED)
    if KEY_PATTERN.search(text) or BEARER_PATTERN.search(text):
        raise ExportError('Secret pattern detected; export aborted.')
    if any(header_secret(m[1]) for pattern in (LITERAL_HEADER, RAW_HEADER) for m in pattern.finditer(text)):
        raise ExportError('Credential header detected; export aborted.')
    candidates = [text] if text.lstrip().startswith(('{', '[')) else []
    candidates.extend(line for line in text.splitlines() if line.lstrip().startswith(('{', '[')))
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except (ValueError, TypeError):
            continue
        if structured_secret(value):
            raise ExportError('Credential header detected; export aborted.')
        # Detect JSON-escaped keys, including unicode escapes, in nested strings.
        for decoded in strings(value):
            if key in decoded or KEY_PATTERN.search(decoded) or BEARER_PATTERN.search(decoded):
                raise ExportError('Secret detected; export aborted.')


def safe_name(name):
    path = PurePosixPath(name)
    if path.is_absolute() or any(p in ('.', '..', '.git') for p in path.parts) or '\\' in name:
        raise ExportError('Unsafe archive path; export aborted.')
    return name


def source_files(root):
    """Repack archive members so tar owners and Git PAX commit metadata disappear."""
    files = {}
    with tarfile.open(fileobj=io.BytesIO(git(root, 'archive', '--format=tar', 'HEAD'))) as archive:
        for member in archive:
            safe_name(member.name)
            if member.isdir():
                continue
            if not member.isfile():
                raise ExportError('Links or special files in source archive; export aborted.')
            # Never silently include accidentally tracked runtime artifacts.
            if PurePosixPath(member.name).parts[0] in {'runs', 'release', '.venv', '.cache', '.tmp'}:
                raise ExportError('Runtime artifacts tracked in source; export aborted.')
            files[member.name] = (archive.extractfile(member).read(), member.mode & 0o755)
    return files


def read_run(root, relative, snapshots, optional=False):
    path = root / relative
    # Reject symlinked parents as well as files; never follow them outside runs/.
    if any(p.is_symlink() for p in [path, *path.parents] if p != root and p.is_relative_to(root)):
        raise ExportError('Linked run input; export aborted.')
    if optional and not path.exists():
        snapshots[path] = None
        return b''
    try:
        with path.open('rb') as stream:
            before = os.fstat(stream.fileno())
            data = stream.read(before.st_size)
            after = os.fstat(stream.fileno())
        signature = lambda s: (s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        if signature(before) != signature(after) or len(data) != before.st_size:
            raise ExportError('Run input changed; retry export after writers finish.')
        snapshots[path] = signature(after)
        return data
    except OSError:
        raise ExportError('Run input unavailable; export aborted.') from None


def json_lines(data):
    if data and not data.endswith(b'\n'):
        raise ExportError('Incomplete run input; export aborted.')
    try:
        rows = [json.loads(line) for line in data.splitlines() if line.strip()]
        if not all(isinstance(row, dict) for row in rows):
            raise ValueError
        return rows
    except (ValueError, TypeError):
        raise ExportError('Invalid run input; export aborted.') from None


def scrub_har(data):
    """Produce a metadata-only HAR copy; never write back to the episode."""
    try:
        har = json.loads(data)
        for entry in har['log']['entries']:
            for side in ('request', 'response'):
                obj = entry.get(side, {})
                obj['headers'] = []
                obj['cookies'] = []
                obj.pop('postData', None)
                obj.get('content', {}).pop('text', None)
                for param in obj.get('queryString', []):
                    param['value'] = REDACTED
                for field in ('url', 'redirectURL'):
                    if obj.get(field):
                        url = urlsplit(obj[field])
                        query = urlencode([(k, REDACTED) for k, _ in parse_qsl(url.query, keep_blank_values=True)])
                        obj[field] = url._replace(netloc=url.netloc.rsplit('@', 1)[-1], query=query, fragment='').geturl()
        return (json.dumps(har, ensure_ascii=False) + '\n').encode()
    except (ValueError, KeyError, TypeError, AttributeError):
        raise ExportError('Invalid HAR input; export aborted.') from None


def add_runs(root, files, episodes, with_har):
    snapshots = {}
    aggregate = read_run(root, 'runs/records.jsonl', snapshots)
    superseded = read_run(root, 'runs/superseded.jsonl', snapshots, optional=True)
    records = json_lines(aggregate)
    exclusions = json_lines(superseded)
    excluded = {row['episode_id'] for row in exclusions}
    by_id = {row['episode']: row for row in records}
    selected = list(dict.fromkeys(episodes)) if episodes else sorted(set(by_id) - excluded)
    if any(not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', item) or item not in by_id or item in excluded for item in selected):
        raise ExportError('Invalid, missing, or superseded episode selection; export aborted.')
    # Retain complete aggregate/exclusion snapshots for audit, even for a subset.
    files['runs/records.jsonl'] = (aggregate, 0o600)
    files['runs/superseded.jsonl'] = (superseded, 0o600)
    for episode in selected:
        prefix = f'runs/{episode}/'
        record = read_run(root, prefix + 'record.json', snapshots)
        if json.loads(record) != by_id[episode]:
            raise ExportError('Episode and aggregate disagree; export aborted.')
        trace = read_run(root, prefix + 'trace.jsonl', snapshots)
        json_lines(trace)
        files[prefix + 'record.json'] = (record, 0o600)
        files[prefix + 'trace.jsonl'] = (trace, 0o600)
        if with_har:
            files[prefix + 'network.har'] = (scrub_har(read_run(root, prefix + 'network.har', snapshots)), 0o600)
    for path, before in snapshots.items():
        if before is None:
            if path.exists():
                raise ExportError('Run input changed; retry export after writers finish.')
        else:
            s = path.stat()
            if before != (s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns):
                raise ExportError('Run input changed; retry export after writers finish.')


def privacy_findings(files, root):
    """Report locations/categories only, never excerpts or identity values.

    Names come from this repository's commit identities; arbitrary personal names
    cannot reliably be inferred. Generic email, hostname and home-path patterns
    supplement these exact matches. Findings need human review, not auto-removal.
    """
    identities = git(root, 'log', '--format=%an%n%ae%n%cn%n%ce').decode(errors='replace').splitlines()
    names = {v for v in identities if v and '@' not in v}
    home = str(Path.home())
    host = socket.gethostname()
    patterns = {
        'home-path': re.compile(r'/home/[^/\s"\']+|/Users/[^/\s"\']+|' + re.escape(home)),
        'hostname': re.compile(r'\b(?:localhost|(?:\d{1,3}\.){3}\d{1,3}|[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)\b|' + re.escape(host)),
        'email': re.compile(r'\b[A-Za-z0-9_.+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b'),
    }
    if names:
        patterns['personal-name'] = re.compile('|'.join(re.escape(v) for v in sorted(names)))
    findings = []
    for filename, (data, _) in sorted(files.items()):
        for number, line in enumerate(data.decode(errors='replace').splitlines(), 1):
            for category, pattern in patterns.items():
                if pattern.search(line):
                    findings.append(dict(file=filename, line=number, category=category))
    return findings


def export(root=ROOT, *, with_runs=False, episodes=(), with_har=False):
    root = Path(root).resolve()
    if (episodes or with_har) and not with_runs:
        raise ExportError('Episode selection and HAR export require --with-runs.')
    try:
        key = load_opencode_key()
    except Exception:
        raise ExportError('Credential unavailable; export aborted without a complete scan.') from None
    files = source_files(root)
    tracked = dict(files)
    if with_runs:
        add_runs(root, files, episodes, with_har)
    for name, (data, _) in files.items():
        scan_secrets(name.encode(), key)
        scan_secrets(data, key)
    findings = privacy_findings(tracked, root)
    report = (json.dumps({'scope': 'tracked HEAD files', 'findings': findings}, indent=2) + '\n').encode()
    scan_secrets(report, key)
    directory = root / 'release'
    if directory.is_symlink():
        raise ExportError('Linked release directory; export aborted.')
    directory.mkdir(exist_ok=True)
    output = directory / ('osd-bench-' + datetime.now(timezone.utc).strftime('%Y-%m-%d') + '.tar.gz')
    report_path = output.with_suffix('').with_suffix('.privacy.json')
    if report_path.is_symlink():
        raise ExportError('Linked privacy report; export aborted.')
    # Scan every byte destined for the archive before creating any output file.
    fd, temporary = tempfile.mkstemp(prefix='.export-', suffix='.tmp', dir=directory)
    try:
        with os.fdopen(fd, 'wb') as stream:
            with tarfile.open(fileobj=stream, mode='w:gz', format=tarfile.PAX_FORMAT) as archive:
                for name, (data, mode) in sorted(files.items()):
                    member = tarfile.TarInfo('osd-bench/' + safe_name(name))
                    member.size, member.mode = len(data), mode
                    member.uid = member.gid = member.mtime = 0
                    member.uname = member.gname = ''
                    archive.addfile(member, io.BytesIO(data))
        os.replace(temporary, output)
    finally:
        Path(temporary).unlink(missing_ok=True)
    report_path.write_bytes(report)
    return output, len(files), len(findings)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--with-runs', action='store_true', help='Include aggregate and non-superseded published episodes')
    parser.add_argument('--episodes', nargs='+', default=[], metavar='ID', help='Select published episode IDs (requires --with-runs)')
    parser.add_argument('--with-har', action='store_true', help='Include metadata-only scrubbed HARs (requires --with-runs)')
    args = parser.parse_args()
    try:
        output, count, findings = export(with_runs=args.with_runs, episodes=args.episodes, with_har=args.with_har)
    except ExportError as exc:
        print(str(exc))
        return 1
    except Exception:
        print('Export failed; input or output unavailable/invalid. No diagnostic values disclosed.')
        return 1
    print(f'Release written: {output.relative_to(ROOT)} ({output.stat().st_size} bytes; {count} files).')
    print(f'Privacy review: {findings} locations; see the adjacent .privacy.json report (values withheld).')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
