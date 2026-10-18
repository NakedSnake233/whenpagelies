"""Kill only browser children created by this episode; retain proxy flow evidence."""
import os
from pathlib import Path
import signal
import time

from runner.metrics import rows


def process_table():
    result = {}
    for path in Path('/proc').glob('[0-9]*/stat'):
        try:
            fields = path.read_text().rsplit(')', 1)[1].split()
            result[int(path.parent.name)] = (int(fields[1]), fields[19], fields[0])
        except (OSError, ValueError, IndexError):
            continue
    return result


def descendants(table, parent):
    found = set()
    frontier = {parent}
    while frontier:
        frontier = {pid for pid, (ppid, _, _) in table.items()
                    if ppid in frontier and pid not in found}
        found.update(frontier)
    return found


class BrowserProcesses:
    """Created immediately before make_env, after the worker proxy is running.

    A fresh episode process owns Playwright and Chromium. Exclude all children
    already present (including the proxy), and check process birth times before
    signalling so PID reuse cannot target another experiment.
    """
    def __init__(self):
        self.parent = os.getpid()
        self.before = descendants(process_table(), self.parent)

    def kill(self, grace=5, settle=5):
        targets = {}
        def discover():
            current = process_table()
            excluded = self.before.copy()
            for old in self.before:
                excluded.update(descendants(current, old))
            for pid in descendants(current, self.parent) - excluded:
                targets.setdefault(pid, current[pid][1])
        discover()
        def alive():
            current = process_table()
            return [pid for pid, born in targets.items() if pid in current
                    and current[pid][1] == born and current[pid][2] != 'Z']
        def send(pids, sig):
            for pid in pids:
                try:
                    os.kill(pid, sig)
                except ProcessLookupError:
                    pass
        send(alive(), signal.SIGTERM)
        until = time.monotonic() + grace
        while alive() and time.monotonic() < until:
            # Retain identities across reparenting and catch any children forked
            # by a browser during its shutdown grace period.
            discover()
            time.sleep(min(.1, max(0, until - time.monotonic())))
        discover()
        send(alive(), signal.SIGKILL)
        time.sleep(settle)
        if alive():
            raise RuntimeError('browser process tree survived SIGKILL')
        return sorted(targets)


def in_flight(directory):
    """A transport error is not evidence the upstream request got a response."""
    responses = {r['flow_id'] for r in rows(directory / 'proxy.jsonl')
                 if r.get('flow_id') and r.get('status') is not None}
    return [r for r in rows(directory / 'proxy_requests.jsonl')
            if r['flow_id'] not in responses]
