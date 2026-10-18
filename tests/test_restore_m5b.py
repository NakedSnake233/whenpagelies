"""Restore comment trees without touching baseline posts; retain safe diagnostics."""
import sqlite3
import subprocess
from types import SimpleNamespace

import pytest

from probes import db, reddit
from runner.run_episode import restore_verified


def test_restore_removes_comment_tree_and_preserves_preexisting_rows(monkeypatch):
    connection = sqlite3.connect(':memory:')
    connection.executescript('''
        PRAGMA foreign_keys=ON;
        CREATE TABLE submissions(id INTEGER PRIMARY KEY,user_id INTEGER,forum_id INTEGER);
        CREATE TABLE comments(id INTEGER PRIMARY KEY,submission_id INTEGER REFERENCES submissions(id),
                              parent_id INTEGER REFERENCES comments(id));
        CREATE TABLE comment_votes(id INTEGER PRIMARY KEY,comment_id INTEGER REFERENCES comments(id));
        CREATE TABLE notifications(id INTEGER PRIMARY KEY,submission_id INTEGER REFERENCES submissions(id),
                                   comment_id INTEGER REFERENCES comments(id));
        CREATE TABLE submission_votes(id INTEGER PRIMARY KEY,submission_id INTEGER REFERENCES submissions(id));
        CREATE TABLE submissions_flairs(id INTEGER PRIMARY KEY,submission_id INTEGER REFERENCES submissions(id));
        INSERT INTO submissions VALUES(900,13915,10075),(12,13915,10075),(13,7,10075),(14,13915,10076);
        INSERT INTO comments VALUES(1,900,NULL),(2,12,NULL),(3,12,2),(4,13,NULL),(5,14,NULL);
        INSERT INTO comment_votes VALUES(1,1),(2,2),(3,3),(4,4),(5,5);
        INSERT INTO notifications VALUES(1,900,1),(2,NULL,2),(3,12,3),(4,13,4),(5,14,5);
        INSERT INTO submission_votes VALUES(1,900),(2,12),(4,13),(5,14);
        INSERT INTO submissions_flairs VALUES(1,900),(2,12),(4,13),(5,14);
    ''')
    tables = ['submissions', 'comments', 'comment_votes', 'notifications',
              'submission_votes', 'submissions_flairs']
    def snapshot():
        return {t: connection.execute(f'SELECT * FROM {t} ORDER BY id').fetchall() for t in tables}
    expected = snapshot()
    expected['submissions'] = [r for r in expected['submissions'] if r[0] != 12]
    for table in tables[1:]:
        expected[table] = [r for r in expected[table] if r[0] not in (2, 3)]
    def query(site, statements):
        assert site == 'reddit'
        with connection:
            for statement in statements:
                connection.execute(statement)
    monkeypatch.setattr(reddit, 'query', query)
    monkeypatch.setattr(reddit, 'write_guard', lambda: None)
    baseline = dict(task_id=624, user_id=13915, rows={'submissions': [{'id': 900}]},
                    scopes={'submissions': 'user_id=13915 AND forum_id=10075'})
    reddit.restore.__wrapped__(624, baseline)
    assert snapshot() == expected
    reddit.restore.__wrapped__(624, baseline)
    assert snapshot() == expected  # interrupted recovery can safely repeat restore


@pytest.mark.parametrize('timeout', [False, True])
def test_db_failure_retains_safe_stderr_without_command(tmp_path, monkeypatch, timeout):
    monkeypatch.setattr(db, 'TIMINGS', tmp_path / 'timings.jsonl')
    diagnostic = 'ERROR: foreign key comments password="hidden password" postgresql://u:hidden@host/db sk-secret'
    def run(*args, **kwargs):
        if timeout:
            raise subprocess.TimeoutExpired(['unsafe-command-secret'], 1, stderr=diagnostic.encode())
        return SimpleNamespace(returncode=3, stderr=diagnostic, stdout='')
    monkeypatch.setattr(db.subprocess, 'run', run)
    with pytest.raises(db.DatabaseError) as error:
        db.query('reddit', ['DELETE FROM submissions'])
    assert 'foreign key comments' in error.value.stderr
    assert 'hidden' not in str(error.value)
    assert 'sk-secret' not in str(error.value)
    assert 'unsafe-command-secret' not in str(error.value)


def test_container_psql_stderr_redacts_actual_password(monkeypatch, capsys):
    import io
    monkeypatch.setattr('builtins.open', lambda *a: io.StringIO(
        'DATABASE_URL postgres://user:private%21@database/postmill;'))
    monkeypatch.setattr('sys.stdin', io.StringIO('BEGIN; ROLLBACK;'))
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: SimpleNamespace(
        returncode=3, stderr='ERROR: comments FK; private! private%21', stdout=''))
    with pytest.raises(SystemExit) as error:
        exec(db.PG, {})
    assert error.value.code == 3
    stderr = capsys.readouterr().err
    assert 'ERROR: comments FK' in stderr
    assert 'private' not in stderr


@pytest.mark.parametrize('phase', ['restore', 'verify'])
def test_runner_writes_bounded_redacted_restore_error(tmp_path, phase):
    def fail(*args):
        raise db.DatabaseError('ERROR: FK comments password=private\n' + 'x' * 5000)
    module = SimpleNamespace(restore=fail if phase == 'restore' else lambda *a: None,
                             baseline=fail)
    with pytest.raises(db.DatabaseError):
        restore_verified(module, {}, {'rows': {}}, tmp_path)
    diagnostic = (tmp_path / 'restore_error.log').read_text()
    assert diagnostic.startswith('ERROR: FK comments password=[REDACTED]')
    assert len(diagnostic) <= 4097
    assert 'private' not in diagnostic
