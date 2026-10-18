"""Crash-safe publication for baselines, recovery journals, and episode records."""
import json
import os
from pathlib import Path
import uuid


def write_json(path, data):
    path = Path(path)
    temporary = path.with_name('.' + path.name + '-' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('x') as output:
            temporary.chmod(0o600)
            json.dump(data, output, ensure_ascii=False, indent=2)
            output.write('\n')
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(path)
        fd = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        temporary.unlink(missing_ok=True)
