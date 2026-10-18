"""Own exactly one local mitmdump; never kill a process merely occupying a port."""
import os
import ctypes
import signal
import socket
import subprocess
import time
from agent.browser import ROOT, browser_environment


def die_with_parent():
    # Linux parent-death signal prevents a killed worker leaving an orphan proxy.
    ctypes.CDLL(None).prctl(1, signal.SIGTERM)


class WorkerProxy:
    def __init__(self, worker, port=None):
        self.worker = worker
        self.port = port or 8300 + worker
        self.process = self.log = None
        self.plan = ROOT / f'runs/plans/worker-{worker}.json'

    def ensure(self):
        if self.process is not None and self.process.poll() is None:
            return
        self.close()
        with socket.socket() as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(('127.0.0.1', self.port))
        self.plan.parent.mkdir(parents=True, exist_ok=True)
        # Clear a stale plan so a restarted proxy cannot fire it.
        self.plan.unlink(missing_ok=True)
        env = browser_environment()
        env.update(OSD_PLAN=str(self.plan), OSD_CATALOG_DIR=str(ROOT / 'catalog'),
                   OSD_RUNS_DIR=str(ROOT / 'runs'), NO_PROXY='localhost,127.0.0.1')
        (ROOT / 'logs').mkdir(exist_ok=True)
        self.log = (ROOT / f'logs/worker-{self.worker}-proxy.log').open('a')
        self.process = subprocess.Popen([
            'nohup', str(ROOT / '.venv/bin/mitmdump'), '-q', '-s', 'proxy/osd_faults.py',
            '--listen-host', '127.0.0.1', '--listen-port', str(self.port),
            '--set', f'confdir={ROOT}/.cache/mitm-worker-{self.worker}'],
            cwd=ROOT, env=env, stdout=self.log, stderr=self.log, start_new_session=True, preexec_fn=die_with_parent)
        (ROOT / f'logs/worker-{self.worker}-proxy.pid').write_text(str(self.process.pid) + '\n')
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError('mitmdump exited at startup')
            with socket.socket() as sock:
                if sock.connect_ex(('127.0.0.1', self.port)) == 0:
                    return
            time.sleep(.1)
        self.close()
        raise TimeoutError('mitmdump startup timeout')

    def close(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self.process = None
        if self.log:
            self.log.close()
            self.log = None
