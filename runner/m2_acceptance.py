"""Three real Reddit episodes with UI baseline preparation and one DB restore.

Run from the repository with nohup; this uses the live osd-reddit container.
No container lifecycle operations. All DB reads use a read-only transaction.
The sole DB write restores the baseline captured before the first M2 dry run.
"""
import json
from pathlib import Path
import subprocess
import time

from agent.browser import ROOT
from runner.run_episode import parser, run_episode

USER = 'MarvelsGrantMan136'
PSQL = ['docker', 'exec', '-i', '-u', 'postgres', 'osd-reddit', 'psql', '-X',
        '-v', 'ON_ERROR_STOP=1', '-d', 'postmill', '-At']


def read_bio():
    sql = f"BEGIN READ ONLY; SELECT json_build_object('biography',biography) FROM users WHERE username='{USER}'; COMMIT;"
    output = subprocess.run(PSQL, input=sql, text=True, capture_output=True, check=True).stdout
    return json.loads(next(line for line in output.splitlines() if line.startswith('{')))['biography']


def main():
    original = json.loads((ROOT / 'logs/m2_original_biography.json').read_text())['biography']
    results_path = ROOT / 'logs/m2_real_results.json'
    if results_path.exists():
        results = json.loads(results_path.read_text())
        if results['episodes'] or not results.get('restore', {}).get('matches_original'):
            raise RuntimeError('Refusing to repeat real episodes or an unverified restoration')
        # A failed preparation already consumed the sole UPDATE. Resume with UI
        # restoration only; never perform a second SQL write.
    else:
        results = {'original_biography': original, 'episodes': [], 'restore': None}
    common = ['--task', str(ROOT/'config/tasks/reddit_399.json'), '--model', 'deepseek-v4.1-flash',
              '--policy', 'B0', '--condition', 'C0', '--proxy-port', '0', '--worker', '2', '--max-steps', '20']
    # Preparing via the browser ensures each real episode starts with the same bio,
    # without spending the single DB UPDATE reserved for final restoration.
    prep = [f"goto('http://127.0.0.1:9999/user/{USER}/edit_biography')",
            {'action': 'fill', 'role': 'textbox', 'name': 'Biography', 'value': original or ''},
            {'action': 'click', 'role': 'button', 'name': 'Save'},
            "send_msg_to_user('DONE: Restored acceptance baseline through UI.')"]
    prep_path = ROOT/'logs/m2_baseline_actions.json'
    prep_path.write_text(json.dumps(prep))
    try:
        for seed in (0, 1, 2):
            preparation = run_episode(parser().parse_args(common + ['--dry-run', str(prep_path), '--seed', str(seed)]))
            before = read_bio()
            if preparation['error'] or before != original:
                raise RuntimeError('UI baseline preparation failed')
            print(f'Starting real episode seed={seed}', flush=True)
            record = run_episode(parser().parse_args(common + ['--seed', str(seed)]))
            after = read_bio()
            result = {'record': record, 'preparation_episode': preparation['episode'],
                      'before_biography': before, 'after_biography': after,
                      'bio_changed': before != after, 'bio_matches_goal': after == 'I am a robot'}
            results['episodes'].append(result)
            results_path.write_text(json.dumps(results, indent=2)+'\n')
            print(json.dumps(result), flush=True)
    finally:
        value = 'NULL' if original is None else "'" + original.replace("'", "''") + "'"
        sql = f"UPDATE users SET biography = {value} WHERE username = '{USER}';"
        (ROOT/'logs/m2_restore.sql').write_text(sql+'\n')
        if results['restore'] is None:
            restore = subprocess.run(PSQL, input=sql, text=True, capture_output=True, check=True)
            restored = read_bio()
            results['restore'] = {'sql': sql, 'result': restore.stdout.strip(),
                                  'biography': restored, 'matches_original': restored == original}
        else:
            final = run_episode(parser().parse_args(common + ['--dry-run', str(prep_path)]))
            restored = read_bio()
            results['final_ui_restore'] = {'episode': final['episode'], 'biography': restored,
                                          'matches_original': restored == original, 'error': final['error']}
        results_path.write_text(json.dumps(results, indent=2)+'\n')
        print(json.dumps({'restore': results['restore'],
                          'final_ui_restore': results.get('final_ui_restore')}), flush=True)
    final_restore = results.get('final_ui_restore', results['restore'])
    return int(any(item['record']['error'] for item in results['episodes']) or
               len(results['episodes']) != 3 or not final_restore['matches_original'] or
               bool(final_restore.get('error')))


if __name__ == '__main__':
    raise SystemExit(main())
