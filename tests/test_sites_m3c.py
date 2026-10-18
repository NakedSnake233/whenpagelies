"""The task-599 spelling must resolve to the seeded MachineLearning forum."""
import fcntl
import pytest
from agent.browser import ROOT
from probes import reddit
from runner.run_episode import restore_verified

pytestmark = pytest.mark.sites


def test_reddit_599_baseline_and_restore():
    with (ROOT / 'runs/locks/m1b-reddit.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        baseline = reddit.baseline(599)
        assert baseline['forum_id'] == 10018
        assert reddit.evaluate(599, baseline)['D'] == 0
        restore_verified(reddit, 599, baseline)
