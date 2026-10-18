"""Release tests use temporary Git repositories and synthetic runs only."""
import json
from pathlib import Path
import subprocess
import tarfile

import pytest

from tools import export_release as e

KEY = 'private-' + 'test-value-12345'
HEADER_VALUE = 'fake-value'


@pytest.fixture
def repo(tmp_path, monkeypatch):
    def git(*args):
        subprocess.run(['git', '-C', str(tmp_path), *args], check=True, capture_output=True)
    git('init', '-q')
    git('config', 'user.name', 'Example Researcher')
    git('config', 'user.email', 'example@example.invalid')
    (tmp_path / 'README.md').write_text('Committed source\n')
    git('add', 'README.md')
    git('commit', '-qm', 'Fixture')
    monkeypatch.setattr(e, 'load_opencode_key', lambda: KEY)
    return tmp_path, git


@pytest.mark.parametrize('payload', [
    lambda: b'prefix ' + KEY.encode() + b' suffix',
    lambda: ('sk-' + 'fakecredential0123456789').encode(),
    lambda: ('Bearer ' + 'fake-credential').encode(),
    lambda: ('Authorization: ' + 'Basic fake-value').encode(),
    lambda: json.dumps({'x-api-key': HEADER_VALUE}).encode(),
    lambda: json.dumps({'headers': [{'value': 'fake-value', 'name': 'X-API-Key'}]}).encode(),
    lambda: json.dumps([['Authorization', 'fake-value']]).encode(),
    lambda: ('{"value":"' + ''.join('\\u%04x' % ord(c) for c in KEY) + '"}').encode(),
])
def test_secret_scan(payload):
    data = payload()
    with pytest.raises(e.ExportError) as exc:
        e.scan_secrets(data, KEY)
    assert KEY not in str(exc.value) and 'fake-value' not in str(exc.value)


def test_public_credentials_and_redacted_headers():
    e.scan_secrets(json.dumps({'Authorization': e.REDACTED,
        'public': list(e.PUBLIC_CREDENTIALS)}).encode(), KEY)
    with pytest.raises(e.ExportError):
        e.scan_secrets(e.PUBLIC_CREDENTIALS[0].encode(), e.PUBLIC_CREDENTIALS[0])


def test_source_archive_is_head_and_has_no_history_or_owners(repo):
    root, _ = repo
    (root / 'README.md').write_text(KEY)  # Uncommitted content is not exported.
    (root / 'untracked').write_text(KEY)
    (root / 'runs').mkdir()
    (root / 'runs/records.jsonl').write_text(KEY)
    output, count, _ = e.export(root)
    assert count == 1
    with tarfile.open(output) as archive:
        assert archive.getnames() == ['osd-bench/README.md']
        assert archive.extractfile(archive.getmembers()[0]).read() == b'Committed source\n'
        assert not archive.pax_headers
        for member in archive:
            assert member.uid == member.gid == 0
            assert not member.uname and not member.gname and not member.pax_headers


def test_abort_before_output_and_preserve_previous_release(repo):
    root, git = repo
    output, _, _ = e.export(root)
    before = output.read_bytes()
    (root / 'bad.bin').write_bytes(b'\x00' + KEY.encode())
    git('add', 'bad.bin')
    git('commit', '-qm', 'Synthetic credential')
    with pytest.raises(e.ExportError, match='Secret'):
        e.export(root)
    assert output.read_bytes() == before
    assert not list((root / 'release').glob('.export-*'))


def seed_runs(root):
    runs = root / 'runs'
    runs.mkdir()
    records = [{'episode': name, 'G': True} for name in ['first', 'second', 'old']]
    (runs / 'records.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in records))
    (runs / 'superseded.jsonl').write_text('{"episode_id":"old"}\n')
    har = {'log': {'entries': [{'request': {'url': 'https://name:password@localhost/save?token=private',
        'headers': [{'name': 'Authorization', 'value': 'Bearer ' + KEY}],
        'cookies': [{'value': KEY}], 'postData': {'text': KEY}},
        'response': {'headers': [{'name': 'Set-Cookie', 'value': KEY}],
            'content': {'text': KEY}}}]}}
    for record in records:
        directory = runs / record['episode']
        directory.mkdir()
        (directory / 'record.json').write_text(json.dumps(record))
        (directory / 'trace.jsonl').write_text('{"step":1}\n')
        (directory / 'network.har').write_text(json.dumps(har))
    return {str(p.relative_to(root)): p.read_bytes() for p in runs.rglob('*') if p.is_file()}


def test_optional_runs_and_scrubbed_hars_are_read_only(repo):
    root, _ = repo
    before = seed_runs(root)
    output, _, _ = e.export(root, with_runs=True)
    with tarfile.open(output) as archive:
        assert not any(n.endswith('.har') or '/old/' in n for n in archive.getnames())
        assert 'osd-bench/runs/second/record.json' in archive.getnames()
    output, _, _ = e.export(root, with_runs=True, episodes=['first'], with_har=True)
    with tarfile.open(output) as archive:
        assert not any('/second/' in n for n in archive.getnames())
        data = archive.extractfile('osd-bench/runs/first/network.har').read()
        assert KEY.encode() not in data and b'name:password' not in data
        har = json.loads(data)['log']['entries'][0]
        assert har['request']['headers'] == [] and 'postData' not in har['request']
        assert 'text' not in har['response']['content']
        assert archive.extractfile('osd-bench/runs/records.jsonl').read() == before['runs/records.jsonl']
    assert before == {name: (root / name).read_bytes() for name in before}


def test_run_secret_scan_includes_unselected_aggregate(repo):
    root, _ = repo
    seed_runs(root)
    with (root / 'runs/records.jsonl').open('a') as stream:
        stream.write(json.dumps({'episode': 'other', 'message': KEY}) + '\n')
    with pytest.raises(e.ExportError, match='Secret'):
        e.export(root, with_runs=True, episodes=['first'])
    assert not (root / 'release').exists()


@pytest.mark.parametrize('episode', ['../escape', 'missing', 'old'])
def test_invalid_selection(repo, episode):
    root, _ = repo
    seed_runs(root)
    with pytest.raises(e.ExportError):
        e.export(root, with_runs=True, episodes=[episode])


def test_changed_or_incomplete_run_input(repo, monkeypatch):
    root, _ = repo
    seed_runs(root)
    original = e.read_run
    def racing(*args, **kwargs):
        result = original(*args, **kwargs)
        if args[1].endswith('trace.jsonl'):
            with (root / 'runs/records.jsonl').open('a') as stream:
                stream.write('\n')
        return result
    monkeypatch.setattr(e, 'read_run', racing)
    with pytest.raises(e.ExportError, match='changed'):
        e.export(root, with_runs=True)
    monkeypatch.setattr(e, 'read_run', original)
    (root / 'runs/first/trace.jsonl').write_text('{')
    with pytest.raises(e.ExportError, match='Incomplete'):
        e.export(root, with_runs=True)


def test_missing_key_fails_closed(repo, monkeypatch):
    root, _ = repo
    def fail():
        raise RuntimeError(KEY)
    monkeypatch.setattr(e, 'load_opencode_key', fail)
    with pytest.raises(e.ExportError) as exc:
        e.export(root)
    assert KEY not in str(exc.value)
    assert not (root / 'release').exists()


def test_privacy_reports_locations_not_values(repo):
    root, git = repo
    text = str(Path.home()) + '/work Example Researcher example@example.invalid https://private-host.invalid\n'
    (root / 'private.md').write_text(text)
    git('add', 'private.md')
    git('commit', '-qm', 'Identity fixture')
    output, _, count = e.export(root)
    report = output.with_suffix('').with_suffix('.privacy.json').read_text()
    assert count >= 4
    assert 'Example Researcher' not in report and 'example@example.invalid' not in report
    assert {r['category'] for r in json.loads(report)['findings']} == {'home-path', 'hostname', 'email', 'personal-name'}


def test_symlinks_rejected(repo):
    root, git = repo
    (root / 'link').symlink_to('README.md')
    git('add', 'link')
    git('commit', '-qm', 'Link fixture')
    with pytest.raises(e.ExportError, match='Links'):
        e.export(root)


def test_run_symlink_and_flag_dependencies(repo):
    root, _ = repo
    seed_runs(root)
    with pytest.raises(e.ExportError):
        e.export(root, with_har=True)
    path = root / 'runs/first/trace.jsonl'
    path.unlink()
    path.symlink_to('../../README.md')
    with pytest.raises(e.ExportError, match='Linked'):
        e.export(root, with_runs=True)


def test_json_escaping_cannot_hide_key():
    key = 'private-"quoted\\key'
    with pytest.raises(e.ExportError, match='Secret'):
        e.scan_secrets(json.dumps({'nested': [key]}).encode(), key)


def test_missing_optional_superseded_and_record_mismatch(repo):
    root, _ = repo
    seed_runs(root)
    (root / 'runs/superseded.jsonl').unlink()
    output, _, _ = e.export(root, with_runs=True)
    with tarfile.open(output) as archive:
        assert archive.extractfile('osd-bench/runs/superseded.jsonl').read() == b''
        assert 'osd-bench/runs/old/record.json' in archive.getnames()
    (root / 'runs/first/record.json').write_text('{}')
    with pytest.raises(e.ExportError, match='disagree'):
        e.export(root, with_runs=True)
