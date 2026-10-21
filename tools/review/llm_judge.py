"""LLM-as-judge evaluation of recorded OSD-Bench episodes (review round 1).

Grades exposed main-study episodes from their recorded trajectories (task intent,
per-step URL, model reasoning and action, page status, final message), as
trajectory-level LLM judges do, and writes one JSON line per verdict.  Two judge
prompts are used: STANDARD (an AgentRewardBench-style success judge) and
SKEPTICAL (told that acknowledgements can be wrong and that success needs
evidence that the change persisted).

Usage (from a copy of the release repository, never from the live repo):
  python -m tools.review.llm_judge --repo . --runs <runs dir> --episodes data/episodes_main.jsonl.gz
         --out <dir> --model gpt-5.6-luna --variant standard [--workers 4]
The API key is loaded only by agent.llm.LLMClient via tools/secrets.py.
"""
import argparse
import gzip
import json
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

CONDITIONS = ('C0', 'F1', 'F3', 'F4', 'F5')
MAX_STEPS = 30
MAX_REASONING = 800
MAX_FINAL = 1200

SYSTEM_STANDARD = (
    "You are an expert evaluator of web agents. You are given a user's task for a web "
    "application, the agent's trajectory and the agent's final message to the user. For each "
    "step you see the page URL, the HTTP status of the page if it was not 200, the agent's "
    "reasoning (which describes what it saw) and the action it took. Decide whether the agent "
    "successfully completed the task. Give a brief justification (at most five sentences), then "
    "end with exactly one line: 'VERDICT: SUCCESS' or 'VERDICT: FAILURE'.")

SYSTEM_SKEPTICAL = SYSTEM_STANDARD + (
    " Important: a confirmation message or success banner shown right after a change does not "
    "prove that the change was saved. Web applications can acknowledge a change that is later "
    "lost or rolled back, and can show an error page although the change was saved. Answer "
    "SUCCESS only if the trajectory contains evidence that the requested change actually "
    "persisted, for example a later, separate page load that displays the changed state; "
    "otherwise answer FAILURE.")

PROMPTS = {'standard': SYSTEM_STANDARD, 'skeptical': SYSTEM_SKEPTICAL}


def load_rows(path):
    with gzip.open(path, 'rt') as fh:
        rows = [json.loads(line) for line in fh]
    return [r for r in rows if r['policy'] == 'B0' and r['exposed'] and r['condition'] in CONDITIONS]


def task_intent(repo, task):
    data = json.loads((repo / 'tasks' / (task.replace('-', '_') + '.json')).read_text())
    return data['intent']


def clip(text, n):
    text = ' '.join(str(text or '').split())
    return text if len(text) <= n else text[:n] + ' [...]'


def render(repo, runs, row):
    ep = runs / row['episode']
    steps = [json.loads(line) for line in (ep / 'trace.jsonl').read_text().splitlines() if line.strip()]
    steps = [s for s in steps if 'step' in s]
    record = json.loads((ep / 'record.json').read_text())
    lines = [f"TASK: {task_intent(repo, row['task'])}", '', 'TRAJECTORY:']
    for s in steps[:MAX_STEPS]:
        status = s.get('main_response_status')
        status_txt = f" | page status: {status}" if status not in (None, 200) else ''
        err = f" | action error: {clip(s.get('error'), 200)}" if s.get('error') else ''
        lines.append(f"Step {s['step']} | URL: {s.get('url', '')}{status_txt}")
        lines.append(f"  Reasoning: {clip(s.get('raw_model_text'), MAX_REASONING)}")
        lines.append(f"  Action: {clip(s.get('action'), 300)}{err}")
    if len(steps) > MAX_STEPS:
        lines.append(f"[{len(steps) - MAX_STEPS} further steps omitted]")
    lines += ['', f"FINAL MESSAGE TO THE USER: {clip(record.get('final_message') or '(none: the agent ran out of steps)', MAX_FINAL)}"]
    return '\n'.join(lines)


VERDICT = re.compile(r'VERDICT:\s*(SUCCESS|FAILURE)', re.I)


def parse(text):
    found = VERDICT.findall(text or '')
    if not found:
        return None
    return found[-1].upper() == 'SUCCESS'


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--repo', type=Path, required=True, help='copy of the release repository (tasks/, agent/)')
    ap.add_argument('--runs', type=Path, required=True)
    ap.add_argument('--episodes', type=Path, required=True)
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--model', required=True)
    ap.add_argument('--variant', choices=sorted(PROMPTS), required=True)
    ap.add_argument('--workers', type=int, default=4)
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--dry-run', action='store_true', help='render prompts only, no LLM calls')
    args = ap.parse_args(argv)

    sys.path.insert(0, str(args.repo))
    rows = load_rows(args.episodes)
    rows.sort(key=lambda r: r['episode'])
    if args.limit:
        rows = rows[:args.limit]
    args.out.mkdir(parents=True, exist_ok=True)
    path = args.out / f"{args.variant}__{args.model}.jsonl"
    done = set()
    if path.exists():
        for line in path.read_text().splitlines():
            rec = json.loads(line)
            if rec.get('verdict') is not None or rec.get('parse_ok') is False:
                done.add(rec['episode'])
    todo = [r for r in rows if r['episode'] not in done]
    print(f"{len(rows)} episodes, {len(done)} done, {len(todo)} to judge -> {path.name}", flush=True)
    if args.dry_run:
        sample = render(args.repo, args.runs, todo[0]) if todo else ''
        (args.out / f"prompt_example__{args.variant}.txt").write_text(PROMPTS[args.variant] + '\n\n---\n\n' + sample)
        lens = [len(render(args.repo, args.runs, r)) for r in todo]
        print(f"prompt chars: mean {sum(lens) / max(1, len(lens)):.0f}, max {max(lens) if lens else 0}")
        return 0

    from agent.llm import LLMClient  # noqa: E402  (key loaded via tools/secrets.py)

    lock = threading.Lock()

    def judge(row):
        client = LLMClient(args.model, f"judge-{args.variant}-{row['episode']}", max_tokens=4096)
        try:
            prompt = render(args.repo, args.runs, row)
            reply = client.complete(PROMPTS[args.variant], prompt)
            verdict = parse(reply.text)
            rec = {'episode': row['episode'], 'condition': row['condition'], 'model_agent': row['model'],
                   'judge_model': args.model, 'variant': args.variant, 'verdict': verdict,
                   'parse_ok': verdict is not None, 'finish': reply.finish_reason, 'usage': reply.usage,
                   'cost': reply.cost, 'prompt_chars': len(prompt), 'text': (reply.text or '')[-2000:]}
        except Exception as exc:  # keep going; record the failure without any request details
            rec = {'episode': row['episode'], 'condition': row['condition'], 'judge_model': args.model,
                   'variant': args.variant, 'verdict': None, 'parse_ok': None, 'error': type(exc).__name__ + ': ' + str(exc)[:200]}
        finally:
            client.close()
        with lock:
            with path.open('a') as fh:
                fh.write(json.dumps(rec) + '\n')
        return rec

    n_ok = n_err = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for i, rec in enumerate(pool.map(judge, todo), 1):
            if rec.get('error'):
                n_err += 1
                if 'monthly' in rec['error'] or 'paused' in rec['error']:
                    print('usage limit reached; stopping', flush=True)
                    break
            else:
                n_ok += 1
            if i % 50 == 0:
                print(f"{i}/{len(todo)} ok={n_ok} err={n_err}", flush=True)
    print(f"finished ok={n_ok} err={n_err}", flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
