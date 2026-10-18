"""Scripted off-target C0 episodes through the real browser/proxy/restore path.

Run serially with tools.background while the scheduler is stopped. No model calls.
"""
import json
from unittest.mock import patch

from agent.llm import Reply
from runner.run_episode import parser, run_episode
from tools import pristine_state as reset
from tools.state_audit import query


class OffTargetScript:
    def __init__(self, task, baseline, noop=False):
        self.env = None
        self.site = task['site']
        self.baseline = baseline
        self.evidence = {}

    def complete(self, system, prompt):
        page = self.env.page
        if self.site == 'reddit':
            sid, fid = 119516, 10022
            submission, forum = query('reddit', 'osd-reddit', [
                f'SELECT s.id,f.name FROM submissions s JOIN forums f ON f.id=s.forum_id WHERE s.id={sid}',
                f'SELECT name FROM forums WHERE id={fid}'])
            page.goto(f'http://127.0.0.1:9999/f/{submission[0]["name"]}/{sid}/-', wait_until='networkidle')
            form = page.locator(f'form[data-vote-id-value="{sid}"]')
            token = form.locator('input[name="token"]').input_value()
            status = page.evaluate('''async ({sid,token}) => {
                const r=await fetch('/sv/'+sid+'.json', {method:'POST',
                    headers:{'Content-Type':'application/x-www-form-urlencoded'},
                    body:new URLSearchParams({token,choice:'1'})});return r.status;
            }''', {'sid': sid, 'token': token})
            page.goto(f'http://127.0.0.1:9999/f/{forum[0]["name"]}', wait_until='networkidle')
            form = page.locator('form.subscribe-form')
            action = form.get_attribute('action')
            token = form.locator('input[name="token"]').input_value()
            sub_status = page.evaluate('''async ({action,token}) => {
                const r=await fetch(action,{method:'POST',
                    headers:{'Content-Type':'application/x-www-form-urlencoded'},
                    body:new URLSearchParams({token})});return r.status;
            }''', {'action': action, 'token': token})
            votes, subscriptions = query('reddit', 'osd-reddit', [
                f'SELECT id FROM submission_votes WHERE user_id=13915 AND submission_id={sid}',
                f'SELECT id FROM forum_subscriptions WHERE user_id=13915 AND forum_id={fid}'])
            assert votes and subscriptions, 'scripted extra vote/subscription did not persist'
            self.evidence = {'submission_id': sid, 'forum_id': fid, 'http_status': [status, sub_status],
                             'changed_before_restore': True}
        else:
            pristine = reset.load('shopping_admin')
            excluded = set(self.baseline['affected_ids'])
            ids = sorted({int(r['entity_id']) for r in pristine['rows']['catalog_product_entity_int']
                          if int(r['attribute_id']) == 97 and str(r['value']) == '1'
                          and int(r['entity_id']) not in excluded})[:3]
            assert len(ids) == 3 and not excluded.intersection(ids)
            page.goto('http://localhost:7780/admin/catalog/product/index/', wait_until='networkidle')
            status = page.evaluate('''async ids => {
                const data=new URLSearchParams({form_key:window.FORM_KEY,status:'2',namespace:'product_listing'});
                ids.forEach(id=>data.append('selected[]',String(id)));
                const r=await fetch('/admin/catalog/product/massStatus/',{method:'POST',
                    headers:{'Content-Type':'application/x-www-form-urlencoded'},body:data});
                return r.status;
            }''', ids)
            changed = query('shopping_admin', 'osd-shopping_admin', [
                'SELECT entity_id,value FROM catalog_product_entity_int WHERE store_id=0 AND attribute_id=97 '
                'AND entity_id IN (' + ','.join(map(str, ids)) + ')'])[0]
            assert len(changed) == 3 and all(str(r['value']) == '2' for r in changed), 'bulk status did not persist'
            self.evidence = {'product_ids': ids, 'task_affected_ids': sorted(excluded), 'http_status': status,
                             'changed_before_restore': True}
        return Reply("```action\nsend_msg_to_user('DONE: scripted off-target reset proof')\n```")

    def close(self):
        pass


def main():
    result = {}
    instances = []
    def script(*args, **kwargs):
        instance = OffTargetScript(*args, **kwargs)
        instances.append(instance)
        return instance
    for site, tid in [('reddit', 399), ('shopping_admin', 453)]:
        args = parser().parse_args(['--task', f'tasks/{site}_{tid}.json', '--condition', 'C0',
                                   '--worker', '12', '--dry-run', '--max-steps', '2'])
        with patch('runner.scripted.FamilyScript', script):
            record = run_episode(args)
        result[site] = {'episode': record['episode'], 'restore_ok': record['restore_ok'],
                        'error': record['error'], 'G': record['G'], 'D': record['D'],
                        'mutation': instances[-1].evidence}
        reset.save(reset.ROOT / 'logs/m5d_live_tests.json', result)
        assert not record['error'] and record['restore_ok'], 'scripted episode failed; see safe record'
        result[site]['check'] = reset.check(site)
        assert result[site]['check']['drift'] == 0
        reset.save(reset.ROOT / 'logs/m5d_live_tests.json', result)
        print(json.dumps({'site': site, 'episode': record['episode'], 'drift': 0}), flush=True)


if __name__ == '__main__':
    main()
