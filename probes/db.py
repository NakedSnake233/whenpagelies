"""Bounded container-local DB clients. Credentials never cross stdout/stderr."""

from contextvars import ContextVar
import json
import re
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
TIMINGS = ROOT / "logs/m1b_probe_timings.jsonl"
DEADLINE = ContextVar("osd_probe_deadline", default=None)
PG = r"""
import json, os, re, subprocess, sys
from urllib.parse import urlsplit, unquote
try:
 text=open('/etc/nginx/conf.d/default.conf').read()
 url=re.search(r'DATABASE_URL\s+["\x27]?([^;"\x27\s]+)',text).group(1)
 u=urlsplit(url)
 env=dict(os.environ,PGPASSWORD=unquote(u.password),PGOPTIONS='-c statement_timeout=2000')
 sql=sys.stdin.read()
 p=subprocess.run(['psql','-X','-q','-t','-A','-h',u.hostname,'-U',unquote(u.username),'-d',u.path.lstrip('/'),'-v','ON_ERROR_STOP=1'],input=sql,text=True,capture_output=True,env=env,timeout=2.5)
 if p.returncode:
  error=p.stderr
  for secret in (u.password,unquote(u.password)):
   if secret: error=error.replace(secret,'[REDACTED]')
  print(error,file=sys.stderr);sys.exit(p.returncode)
 print(p.stdout)
except subprocess.TimeoutExpired:
 print('database client timed out after 2.5s',file=sys.stderr);sys.exit(1)
except Exception:
 print('database operation failed',file=sys.stderr);sys.exit(1)
"""
MYSQL = r"""
try {
 $c=(include '/var/www/magento2/app/etc/env.php')['db']['connection']['default'];
 $db=new PDO('mysql:host='.$c['host'].';dbname='.$c['dbname'],$c['username'],$c['password'],[PDO::ATTR_ERRMODE=>PDO::ERRMODE_EXCEPTION]);
 $db->exec('SET SESSION innodb_lock_wait_timeout=1');
 $ops=json_decode(stream_get_contents(STDIN),true); $out=[];
 $db->beginTransaction();
 foreach($ops as $sql) { $s=$db->query($sql); $out[]=$s->columnCount() ? $s->fetchAll(PDO::FETCH_ASSOC) : []; }
 $db->commit(); echo json_encode($out);
} catch(Throwable $e) {fwrite(STDERR,'database operation failed');exit(1);}
"""


def redact_db_error(value):
    """Sanitize diagnostics before truncating; never include the exec command."""
    if isinstance(value, bytes):
        value = value.decode('utf-8', errors='replace')
    text = str(value or '')
    text = re.sub(r'\b(?:oc_|sk-)[A-Za-z0-9_-]+', '[REDACTED]', text)
    text = re.sub(r'(://[^\s/:]+:)[^\s@]+@', r'\1[REDACTED]@', text)
    text = re.sub(r'''(?ix)(\b(?:password|passwd|pwd|pgpassword|api_key|token)\b["']?\s*[:=]\s*)(?:"[^"]*"|'[^']*'|[^\s,;]+)''',
                  r'\1[REDACTED]', text)
    return text


class DatabaseError(RuntimeError):
    def __init__(self, stderr):
        self.stderr = redact_db_error(stderr)[:4096]
        super().__init__('container database operation failed: ' +
                         (self.stderr or 'no database stderr'))


def sql_literal(value):
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return str(value)
    # Standard SQL quotes; MySQL sessions disable backslash interpretation below.
    return "'" + str(value).replace("'", "''") + "'"


def query(site, statements):
    start = time.monotonic()
    if site == "reddit":
        command = ["docker", "exec", "-i", "osd-reddit", "python3", "-c", PG]
        payload = "BEGIN;\n" + ";\n".join(statements) + ";\nCOMMIT;"
    elif site == "shopping_admin":
        command = ["docker", "exec", "-i", "osd-shopping_admin", "php", "-r", MYSQL]
        payload = json.dumps(
            ["SET SESSION sql_mode='NO_BACKSLASH_ESCAPES'", *statements]
        )
    else:
        raise ValueError("unsupported site")
    try:
        remaining = (
            2.8
            if DEADLINE.get() is None
            else min(2.8, DEADLINE.get() - time.monotonic())
        )
        if remaining <= 0:
            raise TimeoutError("probe deadline exceeded")
        try:
            p = subprocess.run(
                command, input=payload, text=True, capture_output=True, timeout=remaining
            )
        except subprocess.TimeoutExpired as exc:
            raise DatabaseError('container exec timed out; ' +
                                redact_db_error(exc.stderr)) from None
        if p.returncode:
            raise DatabaseError(p.stderr)
        if site == "reddit":
            return [json.loads(line) for line in p.stdout.splitlines() if line.strip()]
        return json.loads(p.stdout)[1:]
    finally:
        TIMINGS.parent.mkdir(exist_ok=True)
        with TIMINGS.open("a") as f:
            f.write(json.dumps({"site": site, "db_s": time.monotonic() - start}) + "\n")


def select(site, table, where="TRUE"):
    sql = f"SELECT * FROM {table} WHERE {where}"
    if site == "reddit":
        sql = f"SELECT COALESCE(jsonb_agg(r),'[]'::jsonb) FROM ({sql}) r"
    return sql
