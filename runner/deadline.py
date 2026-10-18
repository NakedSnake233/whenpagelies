"""Bound browser/CDP operations, whose own protocol calls can lack timeouts."""
from contextlib import contextmanager
import signal
import threading


class BrowserDeadline(BaseException):
    """Bypass BrowserGym's action-error handler so a stalled CDP call aborts safely."""


@contextmanager
def browser_deadline(seconds=90):
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError('browser operations require a worker main thread')
    def expire(signum, frame):
        raise BrowserDeadline(f'browser operation exceeded {seconds}s')
    previous = signal.signal(signal.SIGALRM, expire)
    old_timer = signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, *old_timer)
        signal.signal(signal.SIGALRM, previous)
