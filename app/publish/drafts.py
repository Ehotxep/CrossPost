"""Named drafts with optimistic revisions; shared by the window and file retention."""
import copy
import json
from datetime import datetime, timezone
from app import db

FIELDS = ('title', 'text', 'rating', 'signature', 'exclude', 'variants', 'hashtags',
          'e6ai_tags', 'e6ai_meta', 'multi', 'marks', 'tagger', 'profile', 'platform_texts', 'alts', 'reddit_flairs')


def content(draft):
    if not isinstance(draft, dict):
        raise ValueError('Ожидался черновик поста')
    result = {k: copy.deepcopy(draft[k]) for k in FIELDS if k in draft}
    files = draft.get('files') or []
    if not isinstance(files, list) or len(files) > 40:
        raise ValueError('В черновике допускается до 40 файлов')
    from app.publish import uploads, templates
    import re
    ids = [f.get('id') if isinstance(f, dict) else f for f in files]
    if any(not isinstance(uid, str) or not re.fullmatch('[0-9a-f]{16}', uid) for uid in ids):
        raise ValueError('Неверный номер файла в черновике')
    result['files'] = list(dict.fromkeys(ids))
    if result.get('profile'):
        result['profile'] = templates.clean_profile(result['profile'])
    alts = result.get('alts') or {}
    if not isinstance(alts, dict) or any(not isinstance(v, str) or len(v) > 1000 for v in alts.values()):
        raise ValueError('Описание картинки: до 1000 символов')
    result['alts'] = {uid: alts[uid] for uid in ids if uid in alts}
    from app.publish.browser_targets import valid_community
    flairs = result.get('reddit_flairs') or {}
    if not isinstance(flairs, dict) or any(not valid_community(k) or not isinstance(v, str) or len(v) > 100 or '\n' in v or '\r' in v for k, v in flairs.items()):
        raise ValueError('Неизвестный flair Reddit')
    result['reddit_flairs'] = flairs
    overrides = result.get('platform_texts') or {}
    if not isinstance(overrides, dict):
        raise ValueError('Неверный формат текстов площадок')
    clean = {}
    for platform, fields in overrides.items():
        if platform not in templates.OPTIONS or not isinstance(fields, dict):
            raise ValueError('Неизвестная площадка в вариантах текста')
        if any(k in fields and not isinstance(fields[k], str) for k in ('title', 'text')):
            raise ValueError('Заголовок и описание должны быть текстом')
        clean[platform] = {k: fields[k] for k in ('title', 'text') if k in fields}
        clean[platform]['enabled'] = fields.get('enabled') is True
    result['platform_texts'] = clean
    if len(json.dumps(result, ensure_ascii=False).encode()) > 2_000_000:
        raise ValueError('Черновик слишком большой')
    return result


def hydrate(draft):
    from app.publish import uploads
    files, missing = [], []
    for uid in draft.get('files', []):
        try:
            files.append(dict(uploads.describe(uploads.path_of(uid)), id=uid))
        except (ValueError, OSError):
            missing.append(uid)
            files.append(dict(id=uid, name='Недоступный файл ' + uid, kind='image', missing=True, size=0, width=0, height=0))
    return dict(draft, files=files), missing


def save(name, draft, status='working', did=None, revision=None):
    name = str(name).strip()
    if not name or len(name) > 120 or status not in ('working', 'ready'):
        raise ValueError('Укажите название до 120 символов и статус черновика')
    payload = json.dumps(content(draft), ensure_ascii=False)
    now = datetime.now(timezone.utc).isoformat(timespec='microseconds')
    with db.tx() as c:
        if did is None:
            did = c.execute('INSERT INTO saved_drafts(name,status,created,updated,draft) VALUES (?,?,?,?,?)',
                            (name, status, now, now, payload)).lastrowid
            revision = 1
        else:
            if revision is None or c.execute('UPDATE saved_drafts SET name=?,status=?,updated=?,draft=?,revision=revision+1 WHERE id=? AND revision=?',
                                            (name, status, now, payload, int(did), int(revision))).rowcount != 1:
                raise ValueError('Черновик уже изменён в другом окне. Откройте сохранённую версию заново.')
            revision = int(revision) + 1
    return dict(id=int(did), revision=revision, name=name, status=status, updated=now)


def get(did):
    rows = db.query('SELECT * FROM saved_drafts WHERE id=?', (int(did),))
    if not rows:
        raise ValueError('Черновик не найден')
    row = rows[0]
    row['draft'], row['missing'] = hydrate(json.loads(row['draft']))
    return row


def items(q='', status=''):
    rows = db.query('SELECT id,name,status,updated,revision,draft FROM saved_drafts ORDER BY updated DESC')
    result = []
    for row in rows:
        d = json.loads(row.pop('draft'))
        if status and row['status'] != status:
            continue
        if q and q.casefold() not in (row['name'] + ' ' + d.get('title', '') + ' ' + d.get('text', '')).casefold():
            continue
        result.append(dict(row, files=d.get('files', []), title=d.get('title', '')))
    return result


def remove(did, revision):
    with db.tx() as c:
        if c.execute('DELETE FROM saved_drafts WHERE id=? AND revision=?', (int(did), int(revision))).rowcount != 1:
            raise ValueError('Черновик изменился — обновите список перед удалением')


def protected_ids():
    from app import ui_state
    out = set()
    for row in db.query('SELECT draft FROM saved_drafts'):
        out.update(json.loads(row['draft']).get('files', []))
    state = ui_state.load()
    for key in ('draft', 'draft_saved'):
        d = state.get(key) or {}
        if isinstance(d, dict):
            out.update(f.get('id') if isinstance(f, dict) else f for f in d.get('files', []))
    return out
