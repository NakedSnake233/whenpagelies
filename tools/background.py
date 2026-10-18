"""Launch a repository command detached with nohup and required log/PID artifacts."""
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def launch(name, command):
    if not name or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in name):
        raise ValueError('unsafe log name')
    logs = ROOT / 'logs'
    logs.mkdir(exist_ok=True)
    env = dict(os.environ, TMPDIR=str(ROOT / '.tmp'), XDG_CACHE_HOME=str(ROOT / '.cache'),
               PYTHONUNBUFFERED='1', PLAYWRIGHT_BROWSERS_PATH=str(ROOT / '.cache/ms-playwright'))
    with (logs / f'{name}.log').open('a') as log:
        process = subprocess.Popen(['nohup', *command], cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                                   stdout=log, stderr=log, start_new_session=True)
    (logs / f'{name}.pid').write_text(str(process.pid) + '\n')
    return process.pid


if __name__ == '__main__':
    print(launch(sys.argv[1], sys.argv[2:]))
