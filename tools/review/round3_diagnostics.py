"""Round-3 diagnostics from released episode lists and read-only run artifacts.

Full traces are not part of this release. Supply both full run directories,
with one directory per episode as written by the harness. Run from the
repository root; the episode-list default is repository-relative data.
Output is Markdown on stdout; no run artifacts are written or imported.
Requires PyYAML. Raw reply excerpts are deliberately omitted.
"""
import argparse
import ast
import collections
from datetime import datetime
import gzip
import json
from pathlib import Path
import re
from urllib.parse import urlsplit, urljoin

import yaml

REPO = Path(__file__).resolve().parents[2]
ERRORS = [
    'Reply with exactly one action per turn.',
    'Use exactly one ```action fenced block containing one valid action call.',
    'Elements must be referenced by the bracketed numeric bid shown in the observation.',
]

def jl(p):
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]

def run_directory(study, row):
    ep = row['episode']
    assert Path(ep).name == ep and ep not in ('.', '..')
    return ROOTS[study]/ep

def table(headers, rows):
    return ['| ' + ' | '.join(headers) + ' |', '| ' + ' | '.join(['---']*len(headers)) + ' |'] + ['| ' + ' | '.join(str(x).replace('|', '\\|').replace('\n', ' ') for x in r) + ' |' for r in rows]

GROUPS = [('main B0', 'main', 'B0', None),
          ('mitigation VRV', 'mitigation', 'VRV', None),
          ('r3 B0 repeat', 'r3', 'B0', {'C0', 'F1', 'F3', 'F4'}),
          ('r3 B0 delay', 'r3', 'B0', {'F3s2', 'F3s30'}),
          ('r3 VRV', 'r3', 'VRV', {'F3s2', 'F3', 'F3s30'})]
CATEGORIES = ['Empty/whitespace', 'DSML', '≥2 action blocks',
              'Fence; no valid single action', 'No fence (prose)', 'Other']


def classify(raw):
    if not raw.strip(): return 0
    if 'DSML' in raw: return 1
    if raw.count('```action') >= 2: return 2
    if '```' in raw: return 3
    if '```' not in raw: return 4
    return 5



def stamp(t):
    return 'NA' if t is None else f'{t:.6f}'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--episodes-dir', type=Path, default=Path('data'))
    parser.add_argument('--runs-round3', type=Path, required=True)
    parser.add_argument('--runs-original', type=Path, required=True)
    args = parser.parse_args()
    global ROOTS
    ROOTS = {'r3': args.runs_round3, 'mitigation': args.runs_original,
             'main': args.runs_original}
    data = {}
    for study in ROOTS:
        with gzip.open(args.episodes_dir / f'episodes_{study}.jsonl.gz', 'rt') as stream:
            data[study] = [json.loads(line) for line in stream if line.strip()]
    # Check the parser-error definition without importing runtime packages.
    source = ast.parse((REPO / 'tools/export_r2.py').read_text())
    assert set(ERRORS) == next(ast.literal_eval(node.value) for node in source.body
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name)
        and target.id == 'PARSE_ERRORS' for target in node.targets))
    lines = ['# Round-3 diagnostics', '', 'Inputs opened read-only; no network, Docker, queue mutation, or credential files. Released episode lists define the population; directories are runs/<episode>, exactly as export_r2.validate and analyze.capture resolve them. No replacement attempts are selected.', '', '### Missing directories across entire input lists']
    lines += table(['list','episodes','missing run directories'], [(s,len(rs),sum(not run_directory(s,r).is_dir() for r in rs)) for s,rs in data.items()])
    lines += ['', '## 1. Parse errors and composition', 'E1/E2/E3 are, respectively:'] + [f'- E{i+1}: {e}' for i,e in enumerate(ERRORS)]
    lines += ['', 'Count exactly action == "noop()" and error in the above set. Steps are trace entries, not API calls. Means divide by episodes with available traces; missing traces are excluded, never treated as zero. Step shares divide by observed trace steps. All episodes, including unexposed episodes, enter these groups.']
    groups = []
    for s,m,p,conds in [
     ('mitigation','deepseek-v4.1-flash','VRV',['C0','F1','F3']),
     ('main','deepseek-v4.1-flash','B0',['C0','F3']),
     ('r3','deepseek-v4.1-flash','VRV',['F3s2','F3','F3s30']),
     ('r3','deepseek-v4.1-flash','B0',['C0','F1','F3','F4','F3s2','F3s30']),
     ('r3','mimo-v2.6-flash','B0',sorted({r['condition'] for r in data['r3'] if r['model']=='mimo-v2.6-flash'})),
     ('r3','mimo-v2.6-flash','VRV',sorted({r['condition'] for r in data['r3'] if r['model']=='mimo-v2.6-flash'})),
     ('main','mimo-v2.6-flash','B0',['C0','F1','F3','F4'])]:
     for c in conds: groups.append((s,m,p,c))
    parse_rows, checks = [], collections.Counter()
    for s,m,p,c in groups:
        rs = [r for r in data[s] if (r['model'],r['policy'],r['condition'])==(m,p,c)]
        count=collections.Counter(); total=found=missing=0
        for r in rs:
            d=run_directory(s,r)
            if not d.is_dir(): missing+=1; continue
            if not (d/'trace.jsonl').exists(): continue
            tr=jl(d/'trace.jsonl'); found+=1; total+=len(tr)
            checks['trace vs released step mismatches'] += len(tr)!=r['steps']
            errs=[t for t in tr if t.get('action')=='noop()' and t.get('error') in ERRORS]
            if 'n_parse_error_steps' in r: checks['released parse-count mismatches']+=len(errs)!=r['n_parse_error_steps']
            count.update(t['error'] for t in errs)
        n=sum(count.values())
        parse_rows.append([s,m,p,c,len(rs),missing,len(rs)-missing-found,total,n,f'{n/total:.6%}' if total else 'NA',f'{n/found:.6f}' if found else 'NA',*[count[e] for e in ERRORS]])
    lines += table(['list','model','policy','condition','episodes','missing dirs','missing traces','steps','parse errors','share','mean/episode','E1','E2','E3'],parse_rows)
    composition_rows = []
    for label, study, policy, conditions in GROUPS:
        episodes = [r for r in data[study] if r['model'] == 'deepseek-v4.1-flash'
                    and r['policy'] == policy and (conditions is None or r['condition'] in conditions)]
        assert episodes
        counts = collections.Counter()
        proxy = 0
        for row in episodes:
            episode = row['episode']
            assert Path(episode).name == episode and episode not in ('.', '..')
            directory = ROOTS[study] / episode
            assert directory.is_dir()
            # Inventory confirms no separate LLM response log in selected runs.
            assert not any('llm' in p.name.lower() for p in directory.rglob('*') if p.is_file())
            with (directory / 'trace.jsonl').open() as f:
                trace = [json.loads(line) for line in f if line.strip()]
            assert len(trace) == row['steps']
            parse_count = 0
            for step in trace:
                if step.get('action') != 'noop()' or step.get('error') not in ERRORS: continue
                parse_count += 1
                assert not {'content', 'reasoning', 'reasoning_content', 'response', 'llm_response'} & step.keys()
                raw = step['raw_model_text']
                assert isinstance(raw, str)
                category = classify(raw)
                counts[category] += 1
                call = step['action_llm_call']
                assert set(call) == {'usage', 'cost'}
                proxy += raw == '' and call['usage']['reasoning'] > 0
            if 'n_parse_error_steps' in row:
                assert parse_count == row['n_parse_error_steps']
        total = sum(counts.values())
        conds = ', '.join(sorted({r['condition'] for r in episodes}))
        composition_rows.append([f'{label} ({conds})', total, *[counts[i] for i in range(6)],
                     'N/A: fields not stored', f'{proxy}/{total} ({proxy/total:.1%})'])
    lines += ['', '### Parse-error composition (DeepSeek-V4.1-Flash)',
              'Categories use first-match order: empty/whitespace, literal DSML, at least two opening action fences, remaining fenced replies, prose without a fence, other. No separate response content or reasoning text is stored; raw_model_text is normalized content. The observable proxy uses action_llm_call.usage.reasoning, not aggregate usage.',
              *table(['Group', 'Parse errors', *CATEGORIES, 'Empty content + nonempty reasoning', 'Proxy: empty raw + reasoning tokens >0'], composition_rows)]
    results = []
    for baseline in parse_rows:
        study, model, policy, condition = baseline[:4]
        episodes = [r for r in data[study]
                    if (r['model'], r['policy'], r['condition']) == (model, policy, condition)]
        steps = parse = dsml = other = multi = all_dsml = 0
        missing_dirs = missing_traces = 0
        for row in episodes:
            episode = row['episode']
            assert Path(episode).name == episode and episode not in ('.', '..')
            directory = ROOTS[study] / episode
            if not directory.is_dir():
                missing_dirs += 1
                continue
            path = directory / 'trace.jsonl'
            if not path.exists():
                missing_traces += 1
                continue
            with path.open() as f:
                trace = [json.loads(line) for line in f if line.strip()]
            assert len(trace) == row['steps']
            episode_parse = 0
            for step in trace:
                raw = step.get('raw_model_text') or ''
                assert isinstance(raw, str)
                steps += 1
                has_dsml = 'DSML' in raw
                all_dsml += has_dsml
                if step.get('action') == 'noop()' and step.get('error') in ERRORS:
                    parse += 1
                    episode_parse += 1
                    dsml += has_dsml
                    other += any(marker in raw for marker in ('<tool_call', '<function_calls', '<invoke'))
                    multi += raw.count('```action') >= 2
            if 'n_parse_error_steps' in row:
                assert episode_parse == row['n_parse_error_steps']
        assert [len(episodes), missing_dirs, missing_traces, steps, parse] == list(map(int, baseline[4:9])), baseline[:4]
        share = f'{all_dsml / steps:.6%}' if steps else 'NA'
        results.append([study, model, policy, condition, steps, parse, dsml, other, multi, share])
    headers = ['list', 'model', 'policy', 'condition', 'steps', 'parse errors',
               'DSML among parse errors', 'other markup among parse errors',
               'multiple blocks among parse errors', 'share of all steps with DSML']
    lines += ['', '## 2. DSML share', 'Markup tests are case-sensitive literal substrings of raw_model_text. Other markup means <tool_call, <function_calls or <invoke; multiple blocks means at least two opening action fences. These categories can overlap. Absent/null replies count as empty; all trace entries enter the share denominator.', *table(headers, results)]
    # Execute only pure metric/readback functions extracted from the released source.
    # This preserves document-navigation, same-origin, URL, redirect, and step rules
    # without importing any runner, browser, secrets, or proxy initialization code.
    spaces={}
    for study in ROOTS:
        root = REPO
        ns={'ast':ast,'datetime':datetime,'json':json,'re':re,'urlsplit':urlsplit,'urljoin':urljoin,'F3_DELAYS':{'F3s2':2,'F3s30':30,'F3':8}}
        for rel in ['runner/readback.py','runner/metrics.py']:
            tree=ast.parse((root/rel).read_text())
            nodes=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name!='wait_rollbacks' or isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='NAV_ACTIONS' for t in n.targets)]
            exec(compile(ast.Module(body=nodes,type_ignores=[]),str(root/rel),'exec'),ns)
        spaces[study]=ns

    cats=['claim before rollback','claim after rollback; last read-back before rollback','last read-back after rollback; report holds','last read-back after rollback; report does not hold','undeterminable']
    counts=collections.Counter(); details=[]; cross=[]
    for study,conds in [('r3',['F3s2','F3','F3s30']),('mitigation',['F3'])]:
        root=REPO; ns=spaces[study]
        catalogs={site:yaml.safe_load((root/f'catalog/{site}.yaml').read_text())['entries'] for site in ['reddit','shopping_admin']}
        tasks={}
        for c in conds:
          rs=[r for r in data[study] if r['model']=='deepseek-v4.1-flash' and r['policy']=='VRV' and r['condition']==c and r['exposed'] and r['claim']=='DONE']
          for r in rs:
            d=run_directory(study,r); cat=cats[-1]; rollback=readtime=reporttime=claim=None; result='NA'; notes=[]; readstep=reportstep='NA'; undos=[]
            if not all((d/f).exists() for f in ['record.json','trace.jsonl','proxy.jsonl','proxy_events.jsonl']):
                notes.append('missing directory/artifact')
            else:
                rec=json.loads((d/'record.json').read_text()); tr=jl(d/'trace.jsonl'); ev=jl(d/'proxy_events.jsonl')
                claim=rec.get('final_message_at')
                undos=sorted(e['ts'] for e in ev if e['event']=='rollback_done')
                rollback=undos[-1] if undos else None
                if len(undos)!=1: notes.append(f'{len(undos)} rollback_done events; classification requires exactly one')
                taskid=r['task']
                if taskid not in tasks:
                    tasks[taskid]=json.loads((root/'tasks'/(taskid.replace('-','_')+'.json')).read_text())
                task=tasks[taskid]; entry=next(e for e in catalogs[r['site']] if e['id']==r['family'])
                captured=[]
                original=ns['agent_readbacks']
                def capture(*args):
                    v=original(*args); captured.extend(v); return v
                ns['agent_readbacks']=capture
                evidence=ns['evidence'](d,task,entry,claim,c)
                ns['agent_readbacks']=original
                checks['readback count vs record mismatches']+=evidence['n_readbacks_agent']!=rec['n_readbacks_agent']
                checks['readback count vs released mismatches']+=evidence['n_readbacks_agent']!=r['n_readbacks_agent']
                if captured:
                    readtime=max(x.get('request_ts',x['ts']) for x in captured)
                    readstep=ns['action_step'](tr,readtime)['step']
                reports=[t for t in tr if ns['action_name'](t)=='report_verification' and claim is not None and t.get('action_started_at',t['started_at'])<claim]
                if reports:
                    t=reports[-1]; reportstep=t['step']; reporttime=t.get('action_started_at',t['started_at'])
                    result=(t.get('verification_report') or {}).get('result','NA')
                    if result=='NA':
                        call=ast.parse(t['action']).body[0].value
                        val=call.args[0] if call.args else next((k.value for k in call.keywords if k.arg=='result'),None)
                        if val is not None: result=ast.literal_eval(val)
                    if t.get('error'): notes.append('last report rejected by policy')
                finals=[t for t in tr if ns['action_name'](t)=='send_msg_to_user' and not t.get('error') and not 'done_refused' in t.get('policy_event',[])]
                checks['final claim timestamp not matched to trace']+=not any(t.get('action_started_at')==claim for t in finals)
                if claim is not None and rollback is not None and len(undos)==1:
                    if claim<rollback: cat=cats[0]
                    elif claim>rollback and readtime is not None and readtime<rollback: cat=cats[1]
                    elif readtime is not None and readtime>rollback and reporttime is not None and readtime<=reporttime<claim and not notes:
                        if result=='holds': cat=cats[2]
                        elif result=='does not hold': cat=cats[3]
                if cat==cats[-1] and not notes: notes.append('missing/ambiguous timing, result, or report precedes last read-back')
            outcome='FS' if r['FS'] else 'CS/other'
            counts[study,c,cat,outcome]+=1
            details.append([study,c,r['episode'],outcome,','.join(stamp(t) for t in undos) or 'NA',stamp(readtime),readstep,stamp(reporttime),reportstep,result,stamp(claim),cat,'; '.join(notes)])
            if study=='mitigation' and r['FS']: cross.append((readtime is not None and rollback is not None and readtime>rollback,result))
    lines += ['', '## 3. VRV check timing relative to rollback', 'Timestamps below are Unix seconds (UTC). Rollback time is proxy_events.jsonl rollback_done, never fired or rollback_scheduled. Read-back time is request_ts (fallback ts), using runner.metrics.evidence and agent_readbacks unchanged, including HAR document/redirect matching and runner.readback URL eligibility. The source functions are extracted by AST without importing runtime packages. The released metric implementation is used for both populations; its read-back logic matches both original implementations.', '', 'Final DONE time is record.final_message_at, checked against the successful final send_msg_to_user trace action_started_at. Report time is the last report_verification action_started_at before DONE; result comes from verification_report.result or the literal action argument. A post-rollback report category requires the report at/after the last read-back and before DONE, with no rejected report. Multiple or missing rollback_done events are undeterminable. The five categories are disjoint. FS uses the released FS flag; CS/other is its complement among exposed DONE episodes. No inference from configured rollback delays or null rollback_before_claim is used.']
    summary=[]
    for study,conds in [('r3',['F3s2','F3','F3s30']),('mitigation',['F3'])]:
     for c in conds:
      for cat in cats: summary.append([study,c,cat,counts[study,c,cat,'FS'],counts[study,c,cat,'CS/other']])
    lines += table(['list','condition','classification','FS','CS/other'],summary)
    lines += ['',f'Original mitigation F3 cross-check: {len(cross)} exposed DONE false successes; {sum(x[0] for x in cross)} have last agent read-back after rollback; {sum(x[1]=="does not hold" for x in cross)} have last report “does not hold”; {sum(x[0] and x[1]=="does not hold" for x in cross)} satisfy both. Paper expectation: 18, 18, 16.', '', '### Per-episode evidence',*table(['list','condition','episode','outcome','rollback_done timestamp(s)','last read request','read step','last report time','report step','report result','DONE time','classification','notes'],details),'','### Validation',*table(['check','mismatches'],sorted(checks.items()))]
    print('\n'.join(lines))


if __name__ == '__main__':
    main()
