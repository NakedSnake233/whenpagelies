"""Dump post-write navigation of exposed sample episodes for a manual read-back audit."""
import ast, json, random, sys
from pathlib import Path
S = Path(sys.argv[1]); repo = Path(sys.argv[2]); out = Path(sys.argv[3])

def lst(v):
    if isinstance(v, list):
        return v
    if isinstance(v, str) and v.startswith('['):
        try:
            return ast.literal_eval(v)
        except Exception:
            return []
    return []

rows = []
for ep in sorted(p.name for p in (S / 'sample').iterdir()):
    d = S / 'sample' / ep
    rec = json.loads((d / 'record.json').read_text())
    if not rec.get('n_matched_writes'):
        continue
    if rec['condition'] != 'C0' and not rec.get('fault_fired'):
        continue
    steps = [json.loads(l) for l in (d / 'trace.jsonl').read_text().splitlines() if l.strip()]
    steps = [s for s in steps if 'step' in s]
    proxy = [json.loads(l) for l in (d / 'proxy.jsonl').read_text().splitlines() if l.strip()]
    matched = {(p['method'], p['url']) for p in proxy if p.get('matched_entry')}
    first = None
    for s in steps:
        if any((m.get('method'), m.get('url')) in matched for m in lst(s.get('mutating_requests'))):
            first = int(s['step']); break
    intent = json.loads((repo / 'tasks' / (rec['task'].replace('-', '_') + '.json')).read_text())['intent']
    nav = []
    for s in steps:
        if first is None or int(s['step']) <= first:
            continue
        docs = [x for x in lst(s.get('document_responses')) if x.get('document')]
        muts = lst(s.get('mutating_requests'))
        act = str(s.get('action'))
        if docs or 'report_verification' in act or 'send_msg' in act:
            kind = ' [after a write in the same step: site redirect]' if muts and docs else ''
            nav.append(f"  s{s['step']} {act[:80]} -> " + ', '.join(f"{x.get('method')} {x.get('url','').split('127.0.0.1:')[-1][4:]} {x.get('status')}" for x in docs) + kind)
    rows.append({'episode': ep, 'condition': rec['condition'], 'policy': rec['policy'], 'RB': bool(rec.get('readback_agent')),
                 'n_rb': rec.get('n_readbacks_agent'), 'intent': intent, 'first_write_step': first, 'nav': nav,
                 'final': (rec.get('final_message') or '')[:140], 'G': rec.get('G'), 'claim': rec.get('claim')})
random.seed(7)
rb_t = [r for r in rows if r['RB']]; rb_f = [r for r in rows if not r['RB']]
pick = random.sample(rb_t, min(30, len(rb_t))) + random.sample(rb_f, min(30, len(rb_f)))
pick.sort(key=lambda r: r['episode'])
with out.open('w') as fh:
    for i, r in enumerate(pick, 1):
        fh.write(f"#{i} {r['episode']} | {r['policy']} {r['condition']} | auto RB={r['RB']} (n={r['n_rb']}) | first write step {r['first_write_step']}\n  TASK: {r['intent']}\n")
        fh.write('\n'.join(r['nav'][:16]) + ('\n  ...' if len(r['nav']) > 16 else '') + '\n\n')
json.dump([{k: r[k] for k in ('episode', 'policy', 'condition', 'RB', 'n_rb')} for r in pick], open(str(out) + '.json', 'w'), indent=0)
print(len(rows), 'exposed;', len(rb_t), 'RB true;', len(rb_f), 'RB false; audited', len(pick), '; no write step found:', sum(r['first_write_step'] is None for r in pick))
