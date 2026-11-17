"""Bounded in-memory timings. Only identifiers and numbers, never request data."""
from collections import defaultdict, deque
import math
import re
import threading

LIMIT = 2000
VIEWS = {'post', 'history', 'stats', 'trends', 'settings', 'unknown'}
KINDS = {'api', 'server_api', 'serialize', 'render', 'navigation', 'longtask', 'resource'}
_samples = deque(maxlen=LIMIT)
_lock = threading.Lock()


def _api_names():
    from app.api import Api
    return {k for k, v in vars(Api).items() if not k.startswith('_') and k != 'call' and callable(v)}


def sanitize(rows):
    """Treat browser-supplied reports as untrusted; drop all extra fields."""
    if not isinstance(rows, list):
        return []
    names = _api_names()
    out = []
    for row in rows[-LIMIT:]:
        if not isinstance(row, dict):
            continue
        kind, name = row.get('kind'), row.get('name')
        if not isinstance(kind, str) or kind not in KINDS or not isinstance(name, str):
            continue
        if (kind in {'api', 'server_api', 'serialize'} and name not in names) or (kind in {'render', 'navigation', 'longtask'} and name not in VIEWS):
            continue
        if kind == 'resource' and name not in {'thumbnail','preview','static','upload'}:
            continue
        ms = row.get('ms')
        if isinstance(ms, bool) or not isinstance(ms, (float, int)) or not 0 <= ms <= 3600000 or not math.isfinite(ms):
            continue
        item = dict(kind=kind, name=name, ms=round(ms, 2), ok=row.get('ok') is not False)
        if row.get('pending') is True:
            item['pending'] = True
        if isinstance(row.get('view'), str) and row['view'] in VIEWS:
            item['view'] = row['view']
        rid = row.get('id')
        if isinstance(rid, str) and re.fullmatch(r'[a-f0-9]{8}-[0-9]{1,8}', rid):
            item['id'] = rid
        out.append(item)
    return out


def record(kind, name, ms, ok=True, request_id=None):
    # The server supplies a validated API method name. No payload/error text.
    if kind not in {'server_api', 'serialize'} or not re.fullmatch(r'[a-z][a-z0-9_]{0,79}', name):
        return
    item = dict(kind=kind, name=name, ms=round(ms, 2), ok=bool(ok))
    if isinstance(request_id, str) and re.fullmatch(r'[a-f0-9]{8}-[0-9]{1,8}', request_id):
        item['id'] = request_id
    with _lock:
        _samples.append(item)


def summarize(rows):
    groups = defaultdict(list)
    for row in rows:
        groups[(row['kind'], row['name'], row.get('view', 'unknown'))].append(row)
    out = []
    for (kind, name, view), items in groups.items():
        values = sorted(item['ms'] for item in items)
        out.append(dict(kind=kind, name=name, view=view, n=len(values),
                        mean_ms=round(sum(values)/len(values), 2), max_ms=values[-1],
                        p95_ms=values[math.ceil(.95*len(values))-1],
                        errors=sum(not item['ok'] and not item.get('pending') for item in items),
                        pending=sum(bool(item.get('pending')) for item in items)))
    return sorted(out, key=lambda item: -item['max_ms'])


def snapshot(client=None):
    with _lock:
        server = list(_samples)
    server = [r for r in sanitize(server) if r['kind'] in {'server_api', 'serialize'}]
    client = [r for r in sanitize(client) if r['kind'] in {'api', 'render', 'navigation', 'longtask', 'resource'}]
    return dict(limit=LIMIT, scope='current process and browser session',
                server=summarize(server), client=summarize(client),
                recent_server=server, recent_client=client)
