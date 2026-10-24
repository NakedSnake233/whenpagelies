"""Keep all test temporaries, including those from dependencies, in the workspace."""
import os
from pathlib import Path
import tempfile
import pytest

ROOT = Path(__file__).resolve().parents[1]
(ROOT / ".tmp").mkdir(exist_ok=True)
os.environ["TMPDIR"] = str(ROOT / ".tmp")
os.environ["XDG_CACHE_HOME"] = str(ROOT / ".cache")
tempfile.tempdir = str(ROOT / ".tmp")


@pytest.fixture
def legacy_budget_config(monkeypatch):
    """Historical budget tests own their limits, independently of the live plan."""
    config = ROOT / 'tests/fixtures/legacy_budget.yaml'
    monkeypatch.setattr('runner.budget.CONFIG_PATH', config)


@pytest.fixture(autouse=True)
def isolate_live_budget(tmp_path, monkeypatch):
    """Queue unit tests must never open the running sweep's budget database."""
    from runner.budget import Budget
    original = Budget.__init__

    def isolated(self, path=None, config=None):
        from tools.providers import budget_path
        path = budget_path() if path is None else path
        if Path(path).resolve() in (ROOT / 'runs/budget.sqlite', ROOT / 'runs/budget_lmuai.sqlite'):
            path = tmp_path / 'runs' / Path(path).name
        original(self, path, config)

    monkeypatch.setattr(Budget, '__init__', isolated)
