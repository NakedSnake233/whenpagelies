"""Load credentials and inspect format without exposing credential values."""
import os
import re
from pathlib import Path


def _credential_path():
    return Path.home() / '.config/osd-bench/opencode.env'


def _read_file(path):
    try:
        with Path(path).open(encoding='utf-8', newline='') as stream:
            return stream.read()
    except (OSError, UnicodeError):
        raise RuntimeError('OpenCode credential file is unavailable; set OPENCODE_API_KEY.') from None


def _parse_value(raw):
    value = raw.strip()
    if value.startswith(('"', "'")):
        quote = value[0]
        end = value.find(quote, 1)
        if end < 0:
            return ''
        suffix = value[end + 1:].strip()
        if suffix and not suffix.startswith('#'):
            return ''
        return value[1:end]
    return re.split(r'\s+#', value, maxsplit=1)[0].rstrip()


def load_opencode_key():
    """Prefer OPENCODE_API_KEY; accept dotenv assignments or one bare key."""
    if os.environ.get('OPENCODE_API_KEY'):
        return os.environ['OPENCODE_API_KEY']
    lines = [line.strip() for line in _read_file(_credential_path()).splitlines()
             if line.strip() and not line.lstrip().startswith('#')]
    for line in lines:
        assignment = line[7:].lstrip() if line.startswith('export ') else line
        name, separator, raw = assignment.partition('=')
        if separator and name.strip() == 'OPENCODE_API_KEY':
            value = _parse_value(raw)
            if value:
                return value
    if len(lines) == 1 and '=' not in lines[0]:
        value = _parse_value(lines[0])
        if value and not any(char.isspace() for char in value):
            return value
    raise RuntimeError('OPENCODE_API_KEY is missing or empty in the environment and credential file.')


def diagnose_key_file():
    """Return only line metadata; never include any characters from a value.

    Lengths exclude LF but retain CR. Whitespace/quotes/comment flags describe
    the right-hand side; export/CR flags describe the whole physical line.
    """
    text = _read_file(_credential_path())
    lines = text.split('\n')
    if lines and lines[-1] == '':
        lines.pop()
    result = {'number_of_lines': len(lines), 'lines': []}
    for line in lines:
        if '=' not in line:
            result['lines'].append({'length': len(line),
                                   'starts_oc_or_sk': line.startswith(('oc_', 'sk-'))})
            continue
        left, right = line.split('=', 1)
        stripped = right.strip()
        if stripped.startswith(('"', "'")):
            end = stripped.find(stripped[0], 1)
            quoted = end > 0
            comment = quoted and stripped[end + 1:].lstrip().startswith('#')
            content = stripped[1:end] if quoted else stripped
        else:
            quoted = False
            comment = re.search(r'\s+#', right) is not None
            content = re.split(r'\s+#', stripped, maxsplit=1)[0].rstrip()
        # Only dotenv variable names are safe to expose as left-hand text.
        candidate = left.strip()
        name = candidate[7:].strip() if candidate.startswith('export ') else candidate
        safe_left = left.replace(' ', '<sp>') if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', name) and not name.startswith(('oc_', 'sk-')) else '[non-variable left side withheld]'
        result['lines'].append({'length': len(line), 'contains_equals': True,
            'left': safe_left, 'right_length': len(right),
            'starts_export': line.startswith('export '), 'has_cr': '\r' in line,
            'surrounding_quotes': quoted,
            'internal_whitespace': any(char.isspace() for char in content),
            'trailing_comment': bool(comment)})
    return result


if __name__ == '__main__':
    import json
    print(json.dumps(diagnose_key_file(), indent=2))


class EnvValues:
    """Parsed credentials: values are available only through explicit get()."""
    __slots__ = ('__values',)

    def __init__(self, values):
        self.__values = values

    def __repr__(self):
        return '<EnvValues [REDACTED]>'

    __str__ = __repr__

    def get(self, name, default=None):
        return self.__values.get(name, default)


def parse_env_file(path):
    try:
        text = Path(path).read_text(encoding='utf-8')
    except (OSError, UnicodeError):
        raise RuntimeError('Provider credential file is unavailable') from None
    values = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('export '):
            line = line[7:].lstrip()
        name, sep, raw = line.partition('=')
        if not sep or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', name.strip()):
            raise ValueError('Invalid credential assignment')
        value = _parse_value(raw)
        if raw.strip().startswith(('"', "'")) and not re.fullmatch(
                r'''(?:"[^"]*"|'[^']*')\s*(?:#.*)?''', raw.strip()):
            raise ValueError('Invalid credential value')
        values[name.strip()] = value
    return EnvValues(values)


def _lmuai_path():
    return _credential_path().with_name('lmuai.env')


def load_lmuai_settings(key_name):
    path = _lmuai_path()
    values = EnvValues({})
    if path.exists():
        values = parse_env_file(path)
    elif _credential_path().exists():
        try:
            values = parse_env_file(_credential_path())
        except ValueError:
            # The shared file may hold a bare OpenCode key, not assignments.
            # Only a fully valid named file can supply lmuai settings.
            pass
    key = os.environ.get(key_name) or values.get(key_name)
    if not key:
        raise RuntimeError('Required model credential is missing')
    base = os.environ.get('LMUAI_BASE_URL') or values.get('LMUAI_BASE_URL') or 'https://api.lmuai.ai/v1'
    return EnvValues({'key': key, 'base_url': base.rstrip('/')})
