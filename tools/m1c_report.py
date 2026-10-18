"""Summarize local M1c evidence; reads artifacts only and never calls a model."""
from difflib import SequenceMatcher
from pathlib import Path
import yaml
from tools.m1b_report import normalize

ROOT = Path(__file__).resolve().parents[1]


def silence():
    lines = ['# M1c immediate-page similarity', '',
             'Native body.inner_text compared to C0 after whitespace, relative-time and numeric URL-ID normalization. Ratios are measurements, not a guarantee of semantic silence. F4/F5 are intentionally visible. Raw pages: runs/silence/<site>/<family>/<condition>.txt.', '',
             '| Site | Family | Condition | Ratio to C0 |', '|---|---|---|---:|']
    for site in ('reddit', 'shopping_admin'):
        catalog = yaml.safe_load((ROOT / f'catalog/{site}.yaml').read_text())
        for entry in catalog['entries']:
            if not entry.get('implemented'):
                continue
            path = ROOT / 'runs/silence' / site / entry['id']
            if not (path / 'C0.txt').exists():
                continue
            c0 = normalize((path / 'C0.txt').read_text())
            for p in sorted(path.glob('*.txt')):
                ratio = SequenceMatcher(None, c0, normalize(p.read_text()), autojunk=False).ratio()
                lines.append(f"| {site} | {entry['id']} | {p.stem} | {ratio:.4f} |")
    (ROOT / 'logs/m1c_silence.md').write_text('\n'.join(lines) + '\n')


if __name__ == '__main__':
    silence()
