"""Deterministic fake LLM: native form actions through the same BrowserGym loop."""
from agent.llm import Reply
from probes.site_common import mutations


class FamilyScript:
    def __init__(self, task, baseline, noop=False):
        self.env = None
        self.pending = None
        self.actions = iter(self.build(task, baseline, noop))

    @staticmethod
    def build(task, baseline, noop):
        if noop:
            return ["send_msg_to_user('DONE: scripted no-op negative control')"]
        fam = task['intent_template_id']
        origin = 'http://localhost:7780' if task['site'] == 'shopping_admin' else 'http://127.0.0.1:9999'
        out = []
        if fam in (242, 247, 241):
            from probes.shopping_admin import evaluate
            expected = {d['product_id']: d for d in evaluate(task, baseline)['details']}
            for pid in baseline['target_ids']:
                out.append(f"goto('{origin}/admin/catalog/product/edit/id/{pid}/')")
                field = next(iter(next(e['post_data'] for e in mutations(task) if f'/id/{pid}/' in str(e['url']))))
                if fam == 242:
                    out.append(('click', 'label[data-bind*="attr: {for: uid}"]', None, 'status'))
                else:
                    out.append(('fill', f'[name="{field}"]', expected[pid]['expected']))
                out.append(('click', '#save-button', None))
        elif fam == 6:
            out += [f"goto('{origin}/user/MarvelsGrantMan136/edit_biography')",
                    ('fill', '[name="user_biography[biography]"]', task['instantiation_dict']['content']),
                    ('click', 'button:has-text("Save")', None)]
        elif fam == 22:
            out += [f"goto('{origin}/f/{task['instantiation_dict']['forum']}/new')",
                    ('click', f'form[data-vote-id-value="{baseline["submission_id"]}"] button[value="1"]', None)]
        elif fam == 4:
            referer = mutations(task)[0]['headers']['referer'].replace('__REDDIT__', origin)
            out += [f"goto('{referer}')",
                    ('click', 'form.subscribe-form button', None)]
        elif fam == 15:
            forum = task['instantiation_dict']['forum'].removeprefix('f/')
            out.append(f"goto('{origin}/submit/{forum}')")
            fields = mutations(task)[0]['post_data']
            out += [('fill', f'[name="{key}"]', value) for key, value in fields.items() if key != 'submission[forum]']
            out.append(('click', 'button:has-text("Create submission")', None))
        out.append("send_msg_to_user('DONE: scripted native form submission')")
        return out

    def complete(self, system, prompt):
        item = self.pending if self.pending is not None else next(self.actions)
        self.pending = None
        if not isinstance(item, str):
            action, selector, value, *special = item
            if special == ['status']:
                target = self.env.page.locator('[name="product[status]"]').locator('xpath=..').locator('label')
            else:
                target = self.env.page.locator(selector)
            target = target.first
            bid = target.get_attribute('bid')
            if not bid:
                # Magento creates its fields asynchronously after goto's snapshot.
                # A regular hover refreshes BrowserGym's observation and assigns bids.
                self.pending = item
                item = "scroll(0, 0)"
                return Reply(f'```action\n{item}\n```')
            args = [bid] + ([str(value)] if action == 'fill' else [])
            item = action + '(' + ', '.join(repr(v) for v in args) + ')'
        return Reply(f'```action\n{item}\n```')

    def close(self):
        pass
