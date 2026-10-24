"""Per-model results of the main study (B0, four open-weight models), for the body of the paper.

Inputs: data/episodes_main.jsonl.gz, data/writes.csv and data/readback.csv (released per-model
write and read-back statistics), analysis/review/judge/{standard,skeptical}__gpt-5.6-luna.jsonl (LLM-judge verdicts),
analysis/review/proprietary/proprietary_raw.json (GPT-5.6-Luna, for the table's last column).
Outputs (WPL_TABLES, default analysis/review/per_model): per_model_raw.json, main.tex (the paper's
per-model Table 3) and evaluator.tex (Table 5); markdown on stdout.  WPL_DATA overrides the data directory.

Rates are over exposed episodes.  Paired tests are exact McNemar tests on (task) pairs within a
model, each fault against C0 over tasks exposed under both (robustness.py).
"""
import csv
import gzip
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import robustness as R  # noqa: E402

ROOT = HERE.parents[1]
DATA = Path(os.environ.get('WPL_DATA', ROOT / 'data'))
TABLES = Path(os.environ.get('WPL_TABLES', ROOT / 'analysis' / 'review' / 'per_model'))
MODELS = [('deepseek-v4.1-flash', 'DeepSeek'), ('mimo-v2.6-flash', 'MiMo'), ('minimax-m3', 'MiniMax'), ('qwen3.7-plus', 'Qwen')]
NAMES = {'deepseek-v4.1-flash': 'DeepSeek-V4.1-Flash', 'mimo-v2.6-flash': 'MiMo-V2.6-Flash', 'minimax-m3': 'MiniMax-M3', 'qwen3.7-plus': 'Qwen3.7-Plus'}
CONDS = ['C0', 'F1', 'F2', 'F3', 'F4', 'F5', 'F6']
POST = {'fam-12', 'fam-13', 'fam-15', 'fam-16', 'fam-6100'}
PRICE = 'fam-247'
METRIC = {'CS': lambda r: bool(r['CS']), 'FS': lambda r: bool(r['FS']), 'MS': lambda r: bool(r['MS']),
          'D': lambda r: bool(r['D']), 'RB': lambda r: bool(r['readback_agent']), 'EFP': lambda r: bool(r['EFP']),
          'EFF': lambda r: bool(r['EFF']), 'harm': lambda r: bool(r['harm']), 'W2': lambda r: r['n_matched_writes'] >= 2,
          'DONE': lambda r: r['claim'] == 'DONE', 'DUPDONE': lambda r: r['claim'] == 'DONE' and bool(r['D']) and bool(r['G'])}


def pct(k, n):
    return None if n == 0 else 100.0 * k / n


def r0(x):
    from decimal import Decimal, ROUND_HALF_UP
    return '--' if x is None else str(int(Decimal(str(x)).quantize(Decimal(1), rounding=ROUND_HALF_UP)))


def main():
    rows = [json.loads(l) for l in gzip.open(DATA / 'episodes_main.jsonl.gz', 'rt') if l.strip()]
    rows = [r for r in rows if r['policy'] == 'B0']
    judge, skept = {}, {}
    for name, store in (('standard__gpt-5.6-luna', judge), ('skeptical__gpt-5.6-luna', skept)):
        for line in (ROOT / 'analysis' / 'review' / 'judge' / f'{name}.jsonl').read_text().splitlines():
            rec = json.loads(line)
            if rec.get('verdict') is not None:
                store[rec['episode']] = bool(rec['verdict'])
    writes = {(r['model'], r['condition']): r for r in csv.DictReader(open(DATA / 'writes.csv')) if r['policy'] == 'B0'}
    out = {'models': {}, 'ranges': {}}
    for model, _ in MODELS:
        ep = [r for r in rows if r['model'] == model]
        d = {'by_condition': {}, 'paired': {}}
        for c in CONDS:
            allc = [r for r in ep if r['condition'] == c]
            ex = [r for r in allc if r['exposed']]
            cell = {'n': len(allc), 'n_exposed': len(ex), 'exposure': pct(len(ex), len(allc))}
            for m, fn in METRIC.items():
                cell[m] = pct(sum(fn(r) for r in ex), len(ex))
                cell['n_' + m] = sum(fn(r) for r in ex)
            if c == 'C0':
                nop = [r for r in ex if r['family'] != PRICE]
                cell['FS_noprice'] = pct(sum(r['FS'] for r in nop), len(nop))
                cell['D_noprice'] = pct(sum(bool(r['D']) for r in nop), len(nop))
                cell['n_FS_price'] = sum(r['FS'] for r in ex if r['family'] == PRICE)
            if c in ('F4', 'F5'):
                post = [r for r in ex if r['family'] in POST]
                cell['D_post'] = pct(sum(bool(r['D']) for r in post), len(post))
                cell['n_post'] = len(post)
                cell['CS_post'] = pct(sum(r['CS'] for r in post), len(post))
            w = writes.get((model, c))
            if w:
                cell['mean_writes'] = float(w['mean_n_writes'])
                cell['share_ge2_writes'] = float(w['share_ge2_writes'])
                if w.get('share_done_before_rollback'):
                    cell['claims_before_rollback'] = float(w['share_done_before_rollback'])
                    cell['median_fault_to_claim_s'] = float(w['median_fault_to_claim_seconds'])
            for key, store in (('J', judge), ('S', skept)):
                jd = [r for r in ex if r['episode'] in store]
                cell['n_judged_' + key] = len(jd)
                cell[key + 'FP'] = pct(sum(store[r['episode']] and not (r['G'] and not r['D']) for r in jd), len(jd))
                cell[key + 'FF'] = pct(sum((not store[r['episode']]) and r['G'] and not r['D'] for r in jd), len(jd))
            d['by_condition'][c] = cell
        # silent faults pooled within the model
        sil = [r for r in ep if r['condition'] in ('F1', 'F3') and r['exposed']]
        norb = [r for r in sil if not r['readback_agent']]
        d['silent'] = {'n_exposed': len(sil), 'n_RB': sum(bool(r['readback_agent']) for r in sil),
                       'RB': pct(sum(bool(r['readback_agent']) for r in sil), len(sil)),
                       'FS': pct(sum(r['FS'] for r in sil), len(sil)),
                       'FS_given_noRB': pct(sum(r['FS'] for r in norb), len(norb)),
                       'EFP': pct(sum(bool(r['EFP']) for r in sil), len(sil)),
                       'JFP': pct(sum(judge[r['episode']] and not (r['G'] and not r['D']) for r in sil if r['episode'] in judge),
                                  sum(r['episode'] in judge for r in sil)),
                       'SFP': pct(sum(skept[r['episode']] and not (r['G'] and not r['D']) for r in sil if r['episode'] in skept),
                                  sum(r['episode'] in skept for r in sil))}
        lost = [r for r in ep if r['condition'] in ('F4', 'F5') and r['exposed']]
        d['lost'] = {'n_exposed': len(lost), 'EFF': pct(sum(bool(r['EFF']) for r in lost), len(lost)),
                     'MS': pct(sum(r['MS'] for r in lost), len(lost)), 'D': pct(sum(bool(r['D']) for r in lost), len(lost)),
                     'RB': pct(sum(bool(r['readback_agent']) for r in lost), len(lost)),
                     'JFF': pct(sum((not judge[r['episode']]) and r['G'] and not r['D'] for r in lost if r['episode'] in judge),
                                sum(r['episode'] in judge for r in lost))}
        # paired tests against C0 within the model
        ref = {r['task']: r for r in ep if r['condition'] == 'C0'}
        for c in CONDS[1:]:
            new = {r['task']: r for r in ep if r['condition'] == c}
            tasks = [t for t in new if t in ref and ref[t]['exposed'] and new[t]['exposed']]
            d['paired'][c] = {}
            for m in ('CS', 'FS', 'MS', 'D'):
                pairs = [(t, METRIC[m](ref[t]), METRIC[m](new[t])) for t in tasks]
                if pairs:
                    s = R.paired_summary(pairs)
                    s['p_clustered'] = None  # one pair per task: clustering is moot
                    d['paired'][c][m] = s
        out['models'][model] = d
    # ranges across the four models, for the text
    for c in CONDS:
        for m in ('CS', 'FS', 'MS', 'D', 'RB', 'EFP', 'EFF'):
            vals = [out['models'][k]['by_condition'][c][m] for k, _ in MODELS if out['models'][k]['by_condition'][c][m] is not None]
            out['ranges'][f'{c}:{m}'] = (min(vals), max(vals)) if vals else None
    TABLES.mkdir(parents=True, exist_ok=True)
    (TABLES / 'per_model_raw.json').write_text(json.dumps(out, indent=1, sort_keys=True))
    write_table(out)
    # markdown
    print('# Per-model results (B0, exposed episodes, %)\n')
    print('| model | cond | n | exp | CS | FS | MS | D | RB | EFP | EFF | JFP | JFF | W>=2 |')
    print('|---|---|---|---|---|---|---|---|---|---|---|---|---|---|')
    f = lambda x: '-' if x is None else f'{x:.1f}'
    for model, _ in MODELS:
        for c in CONDS:
            s = out['models'][model]['by_condition'][c]
            print(f"| {model} | {c} | {s['n']} | {s['n_exposed']} | {f(s['CS'])} | {f(s['FS'])} | {f(s['MS'])} | {f(s['D'])} | {f(s['RB'])} | {f(s['EFP'])} | {f(s['EFF'])} | {f(s['JFP'])} | {f(s['JFF'])} | {f(s.get('share_ge2_writes'))} |")
    print('\n## Paired vs C0 within model (exposed in both)')
    for model, _ in MODELS:
        for c, dd in out['models'][model]['paired'].items():
            for m, s in dd.items():
                if m in ('FS', 'CS') and c in ('F1', 'F2', 'F3') or m in ('CS', 'MS') and c in ('F4', 'F5', 'F6'):
                    print(f"  {model} {c} {m}: n={s['n_pairs']} {f(s['ref'])} -> {f(s['new'])} b/c={s['b_ref_only']}/{s['c_new_only']} p={s['p_exact']:.2e}")
    print('\n## Silent faults (F1+F3) and lost acks (F4+F5) per model')
    for model, _ in MODELS:
        print(' ', model, {k: (round(v, 1) if isinstance(v, float) else v) for k, v in out['models'][model]['silent'].items()},
              {k: (round(v, 1) if isinstance(v, float) else v) for k, v in out['models'][model]['lost'].items()})
    print('\n## Extras per model')
    for model, _ in MODELS:
        bc = out['models'][model]['by_condition']
        print(' ', model, 'C0 FS_noprice', f(bc['C0']['FS_noprice']), 'D_noprice', f(bc['C0']['D_noprice']), 'n_FS_price', bc['C0']['n_FS_price'],
              '| F4 D_post', f(bc['F4']['D_post']), 'n_post', bc['F4']['n_post'], 'CS_post', f(bc['F4']['CS_post']), 'DUPDONE', f(bc['F4']['DUPDONE']),
              '| F5 D_post', f(bc['F5']['D_post']), '| C0 writes', bc['C0'].get('mean_writes'), 'F4 writes', bc['F4'].get('mean_writes'), 'F5 ge2', f(bc['F5'].get('share_ge2_writes')), 'F6 ge2', f(bc['F6'].get('share_ge2_writes')),
              '| F3 claims_before_rb', f(bc['F3'].get('claims_before_rollback')), 'median s', bc['F3'].get('median_fault_to_claim_s'),
              '| SFP sil', f(out['models'][model]['silent']['SFP']), 'SFF C0', f(bc['C0']['SFF']), 'JFF C0', f(bc['C0']['JFF']))


def write_table(out):
    prop = json.loads((ROOT / 'analysis' / 'review' / 'proprietary' / 'proprietary_raw.json').read_text())
    pb = prop['by_condition']
    sig = {}
    for model, _ in MODELS:
        for c, dd in out['models'][model]['paired'].items():
            for m, s in dd.items():
                sig[(model, c, m)] = s['p_exact'] < 0.05

    def v(model, c, m, bold=False):
        s = out['models'][model]['by_condition'][c]
        x = r0(s[m])
        if bold and sig.get((model, c, m)) and x != '--':
            x = r'\textbf{' + x + '}'
        return x

    def vp(c, m, bold=False):
        if c not in pb:
            return '--'
        x = r0(pb[c][m])
        pp = prop.get('paired', {}).get(c, {}).get(m)
        if bold and pp and pp['p_exact'] < 0.05 and x != '--':
            x = r'\textbf{' + x + '}'
        return x

    rows = [('C0', 'none', [('CS', 'CS', False), ('FS', 'FS', False), ('\\D', 'D', False)]),
            ('F1', 'fake ack', [('FS', 'FS', True), ('RB', 'RB', False)]),
            ('F2', 'partial', [('FS', 'FS', True), ('RB', 'RB', False)]),
            ('F3', 'rollback', [('FS', 'FS', True), ('RB', 'RB', False)]),
            ('F4', 'lost ack', [('CS', 'CS', True), ('MS', 'MS', True), ('\\D', 'D', True), ('RB', 'RB', False)]),
            ('F5', 'misl.\\ error', [('CS', 'CS', True), ('MS', 'MS', True), ('\\D', 'D', True)]),
            ('F6', 'stale read', [('CS', 'CS', True), ('\\D', 'D', True)])]
    ns = [out['models'][k]['by_condition'][c]['n_exposed'] for k, _ in MODELS for c in CONDS]
    ns = [out['models'][k]['by_condition'][c]['n_exposed'] for k, _ in MODELS for c in CONDS]
    L = [r'\begin{table}[t]',
         r'\caption{Outcomes of exposed B0 episodes by model (\%). CS clean success, FS false success, MS missed success, \D excess or misdirected effect (e.g., a duplicate post), RB agent read the state back after the write (under \fault{F2}, by opening the post). Bold: differs from \fault{C0} on the same tasks (paired exact McNemar, $p<0.05$). Exposed episodes per cell: ' + str(min(ns)) + '--' + str(max(ns)) + r' (\Cref{app:models}). $^{\dagger}$Proprietary model, run under four conditions.}',
         r'\label{tab:main}', r'\footnotesize', r'\setlength{\tabcolsep}{2.6pt}', r'\renewcommand{\arraystretch}{0.92}',
         r'\begin{tabular}{@{}llrrrrr@{}}', r'\toprule',
         r'Fault & Outcome & DeepSeek & MiMo & MiniMax & Qwen & Luna$^{\dagger}$ \\', r'\midrule']
    for i, (c, name, metrics) in enumerate(rows):
        for j, (lab, m, bold) in enumerate(metrics):
            head = f'\\fault{{{c}}} {name}' if j == 0 else ''
            cells = [v(model, c, m, bold) if m != 'n_exposed' else str(out['models'][model]['by_condition'][c]['n_exposed']) for model, _ in MODELS]
            if m == 'n_exposed':
                cells.append(str(pb[c]['n_exposed']) if c in pb else '--')
            else:
                cells.append(vp(c, m, bold))
            L.append(f"{head} & {lab} & " + ' & '.join(cells) + r' \\')
        if i < len(rows) - 1:
            L.append(r'\addlinespace[1.5pt]')
    L += [r'\bottomrule', r'\end{tabular}', r'\end{table}']
    (TABLES / 'main.tex').write_text('\n'.join(L) + '\n')
    write_evaluator_table(out, prop)


def write_evaluator_table(out, prop):
    """Evaluator and LLM-judge errors per model."""
    pb = prop['by_condition']
    L = [r'\begin{table}[t]',
         r'\caption{Evaluator and LLM-judge errors by model (\% of exposed B0 episodes). False pass: graded success although the backend state is wrong; false fail: graded failure although it is right. The judges read the trajectory, not the pages (\Cref{app:judge}). $^{\dagger}$Not judged; run under four conditions.}',
         r'\label{tab:evaluator}', r'\footnotesize', r'\setlength{\tabcolsep}{2.4pt}',
         r'\begin{tabular}{@{}lrrrrrrrr@{}}', r'\toprule',
         r' & \multicolumn{4}{c}{Trace evaluator} & \multicolumn{2}{c}{LLM judge} & \multicolumn{2}{c}{Sceptical judge} \\',
         r'\cmidrule(lr){2-5}\cmidrule(lr){6-7}\cmidrule(lr){8-9}',
         r' & \multicolumn{2}{c}{false pass} & \multicolumn{2}{c}{false fail} & f.\ pass & f.\ fail & f.\ pass & f.\ fail \\',
         r'Model & \fault{F1} & \fault{F3} & \fault{F4} & \fault{F5} & \fault{F1}+\fault{F3} & \fault{F4}+\fault{F5} & \fault{F1}+\fault{F3} & \fault{C0} \\', r'\midrule']
    for model, _ in MODELS:
        d = out['models'][model]
        bc = d['by_condition']
        cells = [bc['F1']['EFP'], bc['F3']['EFP'], bc['F4']['EFF'], bc['F5']['EFF'], d['silent']['JFP'], d['lost']['JFF'], d['silent']['SFP'], bc['C0']['SFF']]
        L.append(NAMES[model] + ' & ' + ' & '.join(r0(c) for c in cells) + r' \\')
    L += [r'\midrule', r'GPT-5.6-Luna$^{\dagger}$ & ' + ' & '.join([r0(pb['F1']['EFP']), r0(pb['F3']['EFP']), r0(pb['F4']['EFF']), '--', '--', '--', '--', '--']) + r' \\']
    L += [r'\bottomrule', r'\end{tabular}', r'\end{table}']
    (TABLES / 'evaluator.tex').write_text('\n'.join(L) + '\n')


if __name__ == '__main__':
    main()
