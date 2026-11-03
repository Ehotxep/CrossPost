"""Advisory comparison against available historical previews, never a proof of identity."""
import hashlib
import json
from pathlib import Path
import sqlite3
from app import db
from app.paths import sub
from core.imagehash import difference_hash

DISTANCE = 6


def _hash(key, path):
    from app.paths import data_dir
    if not path.resolve().is_relative_to(data_dir().resolve()):
        return None
    rows = db.query('SELECT * FROM image_fingerprints WHERE key=?', (key,))
    old = rows[0] if rows else None
    try:
        stat = path.stat()
        if old and old['path'] == str(path) and old['mtime'] == stat.st_mtime_ns and old['size'] == stat.st_size:
            return int(old['hash'], 16)
        value = difference_hash(path)
        with db.tx() as c:
            c.execute('INSERT OR REPLACE INTO image_fingerprints(key,path,mtime,size,hash) VALUES (?,?,?,?,?)',
                      (key, str(path), stat.st_mtime_ns, stat.st_size, f'{value:016x}'))
        return value
    except (OSError, ValueError):
        return int(old['hash'], 16) if old else None


def find(draft):
    from app.publish import uploads
    from app.publish.drafts import content
    draft = dict(content(draft), **({'extend': draft['extend']} if draft.get('extend') else {}))
    current, unreadable = [], 0
    for uid in draft.get('files', []):
        try:
            path = uploads.still(uid)
            value = _hash('upload:' + uid, path) if path else None
        except (OSError, ValueError):
            value = None
        if value is not None:
            current.append((uid, value))
        else:
            unreadable += 1
    candidates, skip_links = [], set()
    pubs = db.query('SELECT id,created,title,dests,files FROM publications')
    for pub in pubs:
        if pub['id'] == draft.get('extend'):
            skip_links.update(d.get('link') for d in json.loads(pub['dests'] or '[]'))
    for pub in pubs:
        if pub['id'] == draft.get('extend'):
            continue
        dests = [d for d in json.loads(pub['dests'] or '[]') if d.get('state') in ('done', 'running')
                 or (d.get('state') == 'error' and d.get('outcome') != 'not_sent')]
        if not dests:
            continue
        files = json.loads(pub['files'] or '[]')
        for i, f in enumerate(files):
            uid = f.get('id') if isinstance(f, dict) else f
            if not isinstance(uid, str):
                continue
            # A thumbnail per original survives cleanup; the publication thumbnail is a fallback for its first image.
            thumb = sub('uploads') / (uid + '.thumb.jpg')
            if not thumb.exists():
                try:
                    thumb = uploads.still(uid)
                except (ValueError, OSError):
                    thumb = None
            if thumb is None or not thumb.exists():
                thumb = sub('thumbs') / f"pub_{pub['id']}.jpg" if i == 0 else sub('uploads') / (uid + '.thumb.jpg')
            value = _hash(f"pub:{pub['id']}:{uid}", thumb)
            if value is None:
                continue
            for d in dests:
                candidates.append(dict(source=f"pub:{pub['id']}:{uid}:{d['key']}", hash=value,
                                       pub_id=pub['id'], created=pub['created'], title=pub['title'] or '',
                                       destination=d.get('name') or d['key'], link=d.get('link') or ''))
    for row in db.query("SELECT p.id,p.preview,p.date_utc,p.text,p.link,c.title FROM posts p LEFT JOIN channels c ON c.platform=p.platform AND c.chat_id=p.chat_id WHERE p.preview IS NOT NULL"):
        if row['link'] and row['link'] in skip_links:
            continue
        value = _hash(f"post:{row['id']}", sub('previews') / row['preview'])
        if value is not None:
            candidates.append(dict(source=f"post:{row['id']}", hash=value, pub_id=None,
                                   created=row['date_utc'], title=(row['text'] or '')[:80],
                                   destination='Telegram · ' + (row['title'] or ''), link=row['link'] or ''))
    try:
        external = db.query('SELECT key,platform,preview,date,text,link FROM ext_posts WHERE preview IS NOT NULL')
    except sqlite3.OperationalError as e:
        if 'no such table' not in str(e):
            raise
        external = []
    for row in external:
        if row['link'] in skip_links:
            continue
        value = _hash('ext:' + row['key'], sub('previews') / row['preview'])
        if value is not None:
            from datetime import datetime, timezone
            candidates.append(dict(source='ext:' + row['key'], hash=value, pub_id=None,
                                   created=datetime.fromtimestamp(row['date'], timezone.utc).isoformat(),
                                   title=(row['text'] or '')[:80], destination=row['platform'], link=row['link'] or ''))
    matches, seen = [], set()
    for uid, value in current:
        for candidate in candidates:
            distance = (value ^ candidate['hash']).bit_count()
            key = (uid, candidate['link'] or candidate['source'])
            if distance <= DISTANCE and key not in seen:
                seen.add(key)
                matches.append(dict({k: v for k, v in candidate.items() if k != 'hash'}, file_id=uid, distance=distance))
    matches.sort(key=lambda m: (m['distance'], m['source'], m['file_id']))
    # Changes to content or the matching history invalidate an old confirmation.
    signature = json.dumps([draft, [(m['source'], m['file_id'], m['distance']) for m in matches]], sort_keys=True, ensure_ascii=False)
    token = hashlib.sha256(signature.encode()).hexdigest()
    return dict(matches=matches[:12], total=len(matches), checked=len(current), unreadable=unreadable, token=token)


def require_confirmation(draft, token):
    result = find(draft)
    if result['total'] and token != result['token']:
        raise ValueError('В истории есть похожие работы. Проверьте предупреждение и подтвердите повторную публикацию.')
    return result
