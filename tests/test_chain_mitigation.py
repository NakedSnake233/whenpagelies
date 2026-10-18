"""Offline handoff checks must never touch live queues, sites, or schedulers."""
from contextlib import nullcontext
import json
import sqlite3

import pytest

from tools import chain_mitigation as chain

WAIT_FOR_MAIN = chain.wait_for_main


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(chain, 'ROOT', tmp_path)
    (tmp_path / 'logs').mkdir()
    (tmp_path / 'runs').mkdir()
    with sqlite3.connect(tmp_path / 'runs/m5_queue.sqlite') as db:
        db.execute('CREATE TABLE queue (name TEXT, status TEXT)')
        db.executemany('INSERT INTO queue VALUES (?,?)',
                       [('m5-main', 'done'), ('m5-mitigation', 'pending')])
    with sqlite3.connect(tmp_path / 'runs/budget.sqlite') as db:
        db.execute('CREATE TABLE workspace_pause (id INTEGER)')
    events = []
    monkeypatch.setattr(chain, 'wait_for_main', lambda: events.append('exited'))
    monkeypatch.setattr(chain.pristine_state, 'site_lock', lambda site: nullcontext())
    monkeypatch.setattr(chain.pristine_state, 'check',
                        lambda site: events.append(site) or {'drift': 0})
    monkeypatch.setattr(chain, 'launch_mitigation', lambda: events.append('launched'))
    return tmp_path, events


def test_completed_main_checks_both_sites_before_launch(sandbox):
    root, events = sandbox
    assert chain.main() == 0
    assert events == ['exited', 'reddit', 'shopping_admin', 'launched']
    assert not (root / 'logs/chain_mitigation.blocked').exists()


@pytest.mark.parametrize('status', chain.UNFINISHED)
def test_exited_main_with_remaining_rows_blocks(sandbox, status):
    root, events = sandbox
    with sqlite3.connect(root / 'runs/m5_queue.sqlite') as db:
        db.execute("UPDATE queue SET status=? WHERE name='m5-main'", (status,))
    assert chain.main() == 1
    assert events == ['exited']
    assert status in json.loads((root / 'logs/chain_mitigation.blocked').read_text())['reason']


@pytest.mark.parametrize('failure', ['dirty', 'drift', 'pause', 'audit_error', 'started', 'blocked'])
def test_preflight_failure_never_launches(sandbox, monkeypatch, failure):
    root, events = sandbox
    if failure == 'dirty':
        (root / 'runs/dirty_reddit').touch()
    elif failure == 'drift':
        monkeypatch.setattr(chain.pristine_state, 'check', lambda site: {'drift': 1})
    elif failure == 'pause':
        with sqlite3.connect(root / 'runs/budget.sqlite') as db:
            db.execute('INSERT INTO workspace_pause VALUES (1)')
    elif failure == 'audit_error':
        def fail(site):
            raise RuntimeError('private transport body')
        monkeypatch.setattr(chain.pristine_state, 'check', fail)
    elif failure == 'started':
        (root / 'logs/chain_mitigation.started.json').write_text('{}')
    else:
        (root / 'logs/chain_mitigation.blocked').write_text('{}')
    assert chain.main() == 1
    assert 'launched' not in events
    assert 'private transport body' not in (root / 'logs/chain_mitigation.blocked').read_text()


def test_wait_requires_exit_before_handoff(sandbox, monkeypatch):
    root, events = sandbox
    # Exercise the real waiter with a fake proc tree: alive twice, then exited.
    (root / 'logs/sweep-m5-main.pid').write_text('123')
    states = iter(['start', 'start', None, None])
    monkeypatch.setattr(chain, 'process_identity', lambda pid: next(states))
    original_read = chain.Path.read_bytes
    original_resolve = chain.Path.resolve
    monkeypatch.setattr(chain.Path, 'read_bytes', lambda p:
                        b'python\0-m\0runner.sweep\0--name\0m5-main\0'
                        if str(p) == '/proc/123/cmdline' else original_read(p))
    monkeypatch.setattr(chain.Path, 'resolve', lambda p:
                        root if str(p) == '/proc/123/cwd' else original_resolve(p))
    monkeypatch.setattr(chain.time, 'sleep', lambda seconds: events.append('wait'))
    WAIT_FOR_MAIN()
    assert events == ['wait']
