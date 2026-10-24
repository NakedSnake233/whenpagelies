"""Atomic workspace and per-model budgets, with a persistent provider pause."""
import calendar
import math
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import yaml
from tools.providers import provider_name, budget_path

CONFIG_PATH = Path(__file__).resolve().parents[1] / 'config/budget.yaml'
MONTHLY_PAUSE = 'GoUsageLimitError monthly'


class BudgetExceeded(RuntimeError):
    def __init__(self, message, retry_after=60):
        super().__init__(message)
        self.retry_after = retry_after


class Budget:
    def __init__(self, path=None, config=None):
        self.config = yaml.safe_load(CONFIG_PATH.read_text()) if config is None else config
        self.provider = provider_name()
        if self.provider != 'opencode':
            self.config = self.config['providers'][self.provider]
        path = budget_path() if path is None else path
        self.cap = float(self.config['workspace_monthly_usd'])
        self._amount(self.cap)
        anchor = self.config['provider_month_anchor']
        datetime(2000, 1, **anchor, tzinfo=timezone.utc)  # validate UTC anchor
        for fraction in self.config['fractions'].values():
            if not math.isfinite(fraction) or not 0 < fraction <= 1:
                raise ValueError('budget fractions must be in (0, 1]')
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = str(path)
        with self.connect() as db:
            # Serialize schema inspection and migration across worker startup.
            db.execute('BEGIN IMMEDIATE')
            db.execute('''CREATE TABLE IF NOT EXISTS calls (
                id TEXT PRIMARY KEY, model TEXT NOT NULL, episode TEXT NOT NULL,
                timestamp REAL NOT NULL, cost REAL NOT NULL, status TEXT NOT NULL)''')
            if 'provider' not in {r[1] for r in db.execute('PRAGMA table_info(calls)')}:
                db.execute("ALTER TABLE calls ADD COLUMN provider TEXT NOT NULL DEFAULT 'opencode'")
            db.execute('CREATE INDEX IF NOT EXISTS calls_window ON calls(model, timestamp)')
            db.execute('CREATE INDEX IF NOT EXISTS workspace_window ON calls(timestamp)')
            db.execute('''CREATE TABLE IF NOT EXISTS workspace_pause (
                id INTEGER PRIMARY KEY CHECK(id=1), reason TEXT, timestamp REAL)''')

    def connect(self):
        return sqlite3.connect(self.path, timeout=30)

    @staticmethod
    def _amount(cost):
        if not math.isfinite(cost) or cost < 0:
            raise ValueError('cost must be finite and nonnegative')

    def pause(self):
        with self.connect() as db:
            added = db.execute('INSERT OR IGNORE INTO workspace_pause VALUES (1,?,?)',
                               (MONTHLY_PAUSE, time.time())).rowcount
        if added:
            print('Workspace paused: GoUsageLimitError monthly; all sweep workers stop claiming rows. Use sweep resume after allowance is restored.', flush=True)

    def paused(self):
        with self.connect() as db:
            row = db.execute('SELECT reason FROM workspace_pause WHERE id=1').fetchone()
        return row[0] if row else None

    def resume(self):
        with self.connect() as db:
            db.execute('DELETE FROM workspace_pause')

    def month_bounds(self, now, provider=True):
        current = datetime.fromtimestamp(now, timezone.utc)
        anchor = self.config['provider_month_anchor'] if provider else dict(day=1, hour=0, minute=0, second=0)
        def boundary(offset):
            year, month = divmod(current.year * 12 + current.month - 1 + offset, 12)
            fields = dict(anchor)
            fields['day'] = min(fields['day'], calendar.monthrange(year, month + 1)[1])
            return datetime(year, month + 1, **fields, tzinfo=timezone.utc).timestamp()
        offset = 0 if now >= boundary(0) else -1
        return boundary(offset), boundary(offset + 1)

    def windows(self, now, provider=True):
        month, end = self.month_bounds(now, provider)
        fractions = self.config['fractions']
        return ((now - 5*3600, now + 5*3600, fractions['five_hours'], 'five_hours'),
                (now - 7*86400, now + 7*86400, fractions['seven_days'], 'seven_days'),
                (month, end, fractions['month'], 'month'))

    def workspace_spend(self, now=None):
        now = time.time() if now is None else now
        with self.connect() as db:
            return {label: dict(spent_usd=db.execute(
                'SELECT COALESCE(SUM(cost),0) FROM calls WHERE timestamp>=? AND timestamp<=?',
                (start, now)).fetchone()[0], limit_usd=self.cap*fraction, start_utc=
                datetime.fromtimestamp(start, timezone.utc).isoformat())
                for start, end, fraction, label in self.windows(now)}

    def reserve(self, model, cap, estimated_cost, episode='', now=None):
        """Pending/uncertain calls count; serialize both account and model checks."""
        self._amount(estimated_cost)
        cap = self.config.get('per_model_monthly_usd', cap)
        self._amount(cap)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            now = time.time() if now is None else now
            if db.execute('SELECT 1 FROM workspace_pause').fetchone():
                raise BudgetExceeded('workspace paused: ' + MONTHLY_PAUSE)
            for scope, ceiling, provider in (('workspace', self.cap, True), (model, cap, False)):
                for start, end, fraction, label in self.windows(now, provider):
                    where = 'timestamp>=? AND timestamp<=?'
                    params = [start, now]
                    if not provider:
                        where += ' AND model=?'
                        params.append(model)
                    charges = db.execute('SELECT timestamp,cost FROM calls WHERE ' + where +
                                         ' ORDER BY timestamp', params).fetchall()
                    spent = sum(cost for _, cost in charges)
                    limit = ceiling * fraction
                    if spent + estimated_cost > limit and not math.isclose(
                            spent + estimated_cost, limit, rel_tol=0, abs_tol=1e-12):
                        frees = end
                        if label != 'month':
                            window = 5*3600 if label == 'five_hours' else 7*86400
                            remaining = spent
                            for timestamp, cost in charges:
                                remaining -= cost
                                if remaining + estimated_cost <= limit:
                                    frees = timestamp + window
                                    break
                        raise BudgetExceeded(f'{scope}: {label} budget would be exceeded', max(1, frees-now+1))
            call_id = uuid.uuid4().hex
            db.execute('INSERT INTO calls (id,model,episode,timestamp,cost,status,provider) VALUES (?,?,?,?,?,?,?)',
                       (call_id, model, episode, now, estimated_cost, 'reserved', self.provider))
        return call_id

    def settle(self, call_id, cost, status='complete'):
        self._amount(cost)
        with self.connect() as db:
            if db.execute('UPDATE calls SET cost=?, status=? WHERE id=?',
                          (cost, status, call_id)).rowcount != 1:
                raise ValueError('unknown budget reservation')

    def record(self, model, cost, episode='', now=None):
        """Record a known charge (also useful for importing existing accounting)."""
        self._amount(cost)
        with self.connect() as db:
            db.execute('INSERT INTO calls (id,model,episode,timestamp,cost,status,provider) VALUES (?,?,?,?,?,?,?)',
                       (uuid.uuid4().hex, model, episode, time.time() if now is None else now,
                        cost, 'complete', self.provider))
