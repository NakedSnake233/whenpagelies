"""Run with .venv/bin/python -m runner.run_episode --help."""
import argparse
import fcntl
import importlib
import inspect
import json
from pathlib import Path
import re
import time
import uuid
import warnings

from agent.agent import run_agent
from agent.browser import ROOT, scrub_har
from agent.env import make_env
from agent.llm import CONFIG, ScriptedLLM, ZERO_USAGE, redact
from runner.llm import LLMClient, RateLimitExceeded
from runner.browser_recovery import BrowserProcesses, in_flight

POLICIES = ('B0', 'B1', 'B2', 'VRV', 'VRV-modeltrigger', 'VRV-samepage', 'VRV-nocbr', 'Oracle')


def oracle_hook(policy, module, task, baseline):
    if policy != 'Oracle':
        return None
    if module is None or baseline is None:
        raise ValueError('Oracle requires a backend probe and baseline')
    def evaluate():
        result = module.evaluate(task, baseline)
        return {key: result[key] for key in ('G', 'D')}
    return evaluate


def probe(task, phase):
    name = f"probes.{task['site']}"
    try:
        module = importlib.import_module(name)
    except ModuleNotFoundError as exc:
        if exc.name in ('probes', name):
            return None
        raise
    hook = getattr(module, 'evaluate', None)
    if not callable(hook):
        return None
    # Later milestones also define evaluate(task, baseline); that is not the
    # optional M2 phase hook and must not receive a phase string as a snapshot.
    if 'phase' not in inspect.signature(hook).parameters:
        warnings.warn(f'{name}.evaluate is not a phase hook; skipping in M2')
        return None
    return hook(task, phase=phase)


def identifier(value):
    value = re.sub(r'[^A-Za-z0-9_.-]+', '-', str(value)).strip('.-')
    if not value:
        raise ValueError('empty identifier')
    return value


def load_task(path):
    task = json.loads(Path(path).read_text())
    for field in ('id', 'site', 'start_url', 'intent'):
        if field not in task:
            raise ValueError(f'Missing task field: {field}')
    if not re.fullmatch(r'[A-Za-z0-9_]+', task['site']):
        raise ValueError('Invalid site identifier')
    tid = task.get('webarena_task_id')
    if tid is not None:
        from probes.site_common import task_data
        joined = task_data(tid)
        task = {**joined, **task, 'task_id': tid,
                'family': f"fam-{joined['intent_template_id']}",
                'instantiation': joined['instantiation_dict']}
    return task


def same_rows(before, after):
    def normalized(b):
        return {k: sorted(json.dumps(r, sort_keys=True) for r in v)
                for k, v in b['rows'].items()}
    return normalized(before) == normalized(after)


def restore_verified(module, task, baseline, episode_dir=None):
    try:
        module.restore(task, baseline)
        if not same_rows(baseline, module.baseline(task)):
            raise RuntimeError('restore pre-image mismatch')
        # The task-scoped pre-image cannot cover off-target agent actions. Keep
        # F3's module.restore unchanged; only episode cleanup uses pristine undo.
        site = task.get('site') or baseline.get('site')
        if site in ('reddit', 'shopping_admin'):
            from tools.pristine_state import reset
            reset(site, episode_dir, baseline.get('episode_started_at'))
    except Exception as exc:
        if episode_dir is not None:
            from probes.db import redact_db_error
            diagnostic = redact_db_error(getattr(exc, 'stderr', str(exc)))[:4096]
            (Path(episode_dir) / 'restore_error.log').write_text(diagnostic + '\n')
        raise


def previous_baseline(task, directory):
    candidates = []
    for path in (ROOT / 'runs').glob('*/baseline.json'):
        if path.parent == directory:
            continue
        b = json.loads(path.read_text())
        if b.get('task_id') == task['webarena_task_id'] and b.get('site') == task['site']:
            candidates.append(path)
    return max(candidates, key=lambda p: p.stat().st_mtime) if candidates else None


def run_episode(args):
    from runner.storage import write_json
    from runner.deadline import BrowserDeadline, browser_deadline
    from runner.evaluation import evaluate as benchmark_evaluate
    from runner.metrics import evidence, wait_rollbacks
    from runner.worker_proxy import WorkerProxy
    from proxy.plan import Plan, write_plan
    from proxy.catalog import load_catalog
    task = load_task(args.task)
    episode = '-'.join(map(identifier, [task['id'], args.model, args.policy, args.condition,
                                        f's{args.seed}', uuid.uuid4().hex[:8]]))
    directory = ROOT / 'runs' / episode
    directory.mkdir(parents=True)
    directory.chmod(0o700)
    har = directory / 'network.har'
    real_task = 'webarena_task_id' in task
    state = dict(episode=episode, task=task['id'], site=task['site'], family=task.get('family'),
                 model=args.model, policy=args.policy, condition=args.condition, seed=args.seed,
                 worker=args.worker, proxy_port=args.proxy_port, dry_run=bool(args.dry_run),
                 steps=0, claim='NONE', final_message='', final_message_at=None,
                 tokens=ZERO_USAGE.copy(), cost=0.0, seconds=0, har=str(har.relative_to(ROOT)),
                 error='', error_kind=None, G=None, D=None, details=None,
                 fault_fired=False, fired_step=None, fired_time=None, n_matched_writes=0,
                 rollback_before_claim=None, readback_after_write=False, readback_any=False,
                 readback_agent=False, n_readbacks=0, n_readbacks_agent=0,
                 eval_pass=None, eval_detail='', restore_ok=None, browser_killed=False)
    start = time.monotonic()
    episode_started_at = time.time()
    state['started_at'] = episode_started_at
    env = client = lock = site_lock = owned_proxy = module = baseline = entry = None
    browser_processes = None
    browser_timed_out = False
    active = ROOT / f"runs/active_{task['site']}.json"
    dirty = ROOT / f"runs/dirty_{task['site']}"
    def failure(exc, kind='infrastructure'):
        message = redact(f'{type(exc).__name__}: {exc}', getattr(client, 'key', ''))
        state['error'] = (state['error'] + '; ' + message).strip('; ')
        state['error_kind'] = kind
    try:
        plans = ROOT / 'runs/plans'
        plans.mkdir(exist_ok=True)
        lock = (plans / f'worker-{args.worker}.lock').open('a')
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        locks = ROOT / 'runs/locks'
        locks.mkdir(exist_ok=True)
        site_lock = (locks / f"m1b-{task['site']}.lock").open('a')
        fcntl.flock(site_lock, fcntl.LOCK_EX)
        if dirty.exists():
            raise RuntimeError('dirty_site')
        if real_task:
            module = importlib.import_module(f"probes.{task['site']}")
            catalog = load_catalog(ROOT / f"catalog/{task['site']}.yaml")
            entry = next(e for e in catalog.entries if e['id'] == task['family'])
            if not entry.get('implemented'):
                raise ValueError('unsupported family')
            # A previous worker may have died after a write and before cleanup.
            if active.exists():
                interrupted = Path(json.loads(active.read_text())['baseline_path'])
                try:
                    wait_rollbacks(interrupted.parent)
                except (TimeoutError, RuntimeError):
                    # The former worker no longer holds the site lock. Its proxy
                    # is parent-bound; canceled timers are superseded by verified undo.
                    state['recovered_interrupted_rollback'] = True
                restore_verified(module, task if json.loads(interrupted.read_text())['task_id'] == task['task_id']
                                 else __import__('probes.site_common', fromlist=['task_data']).task_data(
                                     json.loads(interrupted.read_text())['task_id']),
                                 json.loads(interrupted.read_text()), interrupted.parent)
                active.unlink()
                state['recovered_episode'] = interrupted.parent.name
            baseline = module.baseline(task)
            if module.evaluate(task, baseline)['G']:
                previous = previous_baseline(task, directory)
                if previous is None:
                    baseline = None
                    raise RuntimeError('dirty_state')
                baseline = json.loads(previous.read_text())
                restore_verified(module, task, baseline, directory)
                baseline = module.baseline(task)
                if module.evaluate(task, baseline)['G']:
                    baseline = None
                    raise RuntimeError('dirty_state')
                state['recovered_baseline'] = str(previous.relative_to(ROOT))
            baseline['episode_started_at'] = episode_started_at
            write_json(directory / 'baseline.json', baseline)
            write_json(active, {'baseline_path': str(directory / 'baseline.json')})
            if not args.proxy_port:
                owned_proxy = WorkerProxy(args.worker)
                owned_proxy.ensure()
                state['proxy_port'] = owned_proxy.port
            write_plan(plans / f'worker-{args.worker}.json', Plan(
                episode, task['site'], args.condition, args.seed, [task['family']],
                {'task_id': task['webarena_task_id'], 'baseline_path': str(directory / 'baseline.json'),
                 'fire_count': 1}))
        if args.dry_run:
            if args.dry_run in ('default', 'noop') and real_task:
                from runner.scripted import FamilyScript
                client = FamilyScript(task, baseline, noop=args.dry_run == 'noop')
            else:
                script = ROOT / 'config/reddit_399_actions.json' if args.dry_run == 'default' else Path(args.dry_run)
                client = ScriptedLLM(json.loads(script.read_text()))
        else:
            client = LLMClient(args.model, episode)
        browser_processes = BrowserProcesses()
        with browser_deadline(90):
            env = make_env(task, directory, state['proxy_port'])
        with (directory / 'trace.jsonl').open('w') as trace:
            hook = oracle_hook(args.policy, module, task, baseline)
            run_agent(env, client, args.policy, args.seed, args.max_steps, trace, state, oracle_fn=hook)
        if (getattr(args, 'proxy_health', lambda: True)() is False or
                (owned_proxy and owned_proxy.process.poll() is not None)):
            raise RuntimeError('mitmdump died during episode')
        if not real_task:
            raise ValueError('scoring requires webarena_task_id')
    except (Exception, BrowserDeadline) as exc:
        from runner.budget import BudgetExceeded
        browser_timed_out = isinstance(exc, BrowserDeadline)
        failure(exc, 'budget' if isinstance(exc, BudgetExceeded) else
                'rate_limit' if isinstance(exc, RateLimitExceeded) else 'infrastructure')
        if isinstance(exc, (BudgetExceeded, RateLimitExceeded)):
            state['retry_after'] = exc.retry_after
        if active.exists() and baseline is None and module is not None:
            dirty.write_text(episode + ': interrupted episode recovery failed\n')
    finally:
        # Close the browser first: flush HAR, cease new writes, then drain undo timers.
        if env is not None and not browser_timed_out:
            try:
                with browser_deadline(15):
                    env.close()
            except (Exception, BrowserDeadline) as exc:
                failure(exc)
                browser_timed_out = True
        if browser_timed_out and browser_processes is not None:
            state['browser_killed'] = True
            # Close may itself hang or fail after a deadline. Kill this episode's
            # entire Playwright/browser tree before examining outstanding flows.
            try:
                state['browser_pids'] = browser_processes.kill()
                pending = in_flight(directory)
                state['in_flight_requests'] = pending
                state['in_flight_count'] = len(pending)
            except Exception as exc:
                failure(exc)
            state['error_kind'] = 'infrastructure'
        if baseline is not None:
            try:
                wait_rollbacks(directory)
                state.update(module.evaluate(task, baseline))
                if not (directory / 'proxy.jsonl').exists() and state['steps']:
                    raise RuntimeError('proxy produced no flow log')
            except Exception as exc:
                failure(exc)
                if 'rollback_timeout' in str(exc) and not state['browser_killed']:
                    dirty.write_text(episode + ': rollback_timeout\n')
        try:
            response_keys = {k for e in task.get('eval', [])
                             for k in (e.get('expected', {}).get('response_content') or {})}
            scrub_har(har, response_keys=response_keys)
            if real_task and har.exists():
                state['eval_pass'], state['eval_detail'] = benchmark_evaluate(task, har, state['claim'])
            if entry:
                state.update(evidence(directory, task, entry, state['final_message_at'], args.condition))
        except Exception as exc:
            failure(exc)
        if owned_proxy:
            owned_proxy.close()
        if baseline is not None:
            try:
                restore_verified(module, task, baseline, directory)
                state['restore_ok'] = True
                active.unlink(missing_ok=True)
            except Exception as exc:
                state['restore_ok'] = False
                failure(exc, 'dirty_site')
                dirty.write_text(episode + ': restore failed\n')
        if client:
            client.close()
        (directory / 'trace.jsonl').touch(exist_ok=True)
        state['seconds'] = round(time.monotonic() - start, 3)
        text = redact(json.dumps(state, ensure_ascii=False), getattr(client, 'key', ''))
        write_json(directory / 'record.json', json.loads(text))
        with (ROOT / 'runs/records.jsonl').open('a') as output:
            fcntl.flock(output, fcntl.LOCK_EX)
            output.write(text + '\n')
            output.flush()
        if site_lock:
            site_lock.close()
        if lock:
            lock.close()
    return state


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--task', required=True)
    p.add_argument('--model', choices=list(CONFIG['models']), default='deepseek-v4.1-flash')
    p.add_argument('--policy', choices=POLICIES, default='B0')
    p.add_argument('--condition', choices=['C0', *[f'F{i}' for i in range(1, 7)]], default='C0')
    p.add_argument('--seed', type=int, default=0)
    p.add_argument('--worker', type=int, default=0)
    p.add_argument('--proxy-port', type=int, default=0)
    p.add_argument('--max-steps', type=int, default=30)
    p.add_argument('--dry-run', nargs='?', const='default', metavar='ACTIONS_JSON')
    return p


def main():
    p = parser()
    args = p.parse_args()
    if args.max_steps < 1 or args.worker < 0 or not 0 <= args.proxy_port <= 65535:
        p.error('require max-steps > 0, worker >= 0, and a valid proxy port')
    record = run_episode(args)
    print(json.dumps(record, ensure_ascii=False))
    return int(bool(record['error']))


if __name__ == '__main__':
    raise SystemExit(main())
