"""Portable data backups without login credentials. Restore is staged for next startup."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import tempfile
import uuid
import zipfile
from datetime import datetime, timezone
from contextlib import contextmanager

from app.paths import data_dir
from core.fsutil import atomic_write, file_lock

FILES = {'posts.db', 'publish.json', 'settings.json', 'ui.json', 'templates.json'}
DIRS = {'thumbs', 'previews', 'uploads'}
MAX_BYTES = 8 * 1024**3


def folder():
    p = data_dir() / 'backups'
    p.mkdir(exist_ok=True)
    return p


def archive_path(bid):
    if not re.fullmatch(r'[0-9a-f]{32}', str(bid)):
        raise ValueError('Неверный номер резервной копии')
    return folder() / (bid + '.zip')


def pending():
    return (data_dir() / 'pending-restore.json').exists()


def restore_error():
    if not pending():
        return ''
    return json.loads((data_dir() / 'pending-restore.json').read_text(encoding='utf-8')).get('error', '')


def cancel_restore():
    with file_lock(data_dir() / 'restore'):
        if (data_dir() / '.restore-work' / 'transaction.json').exists():
            raise ValueError('Откат не завершён — не удаляйте файлы восстановления')
        (data_dir() / 'pending-restore.json').unlink(missing_ok=True)
        shutil.rmtree(data_dir() / '.restore-work', ignore_errors=True)


@contextmanager
def _without_worker():
    from app import worker
    lock = worker._single_instance()
    if lock is None:
        raise ValueError('Сначала полностью выключите фоновую отправку')
    try:
        yield
    finally:
        lock.close()


def _allowed(name):
    p = PurePosixPath(name)
    return (not p.is_absolute() and '\\' not in name and ':' not in name
            and all(x not in ('', '.', '..') for x in name.split('/'))
            and (name in FILES or (len(p.parts) > 1 and p.parts[0] in DIRS)))


def _hash(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def _portable_publish(cfg):
    from app.publish.templates import clean_profile
    out = clean_profile(cfg)
    tg = cfg.get('telegram') or {}
    out.setdefault('telegram', {})['channels'] = [
        {k: ch[k] for k in ('id', 'title', 'rating', 'username', 'originals') if k in ch}
        for ch in tg.get('channels', [])]
    out['telegram']['group'] = {k: v for k, v in tg.get('group', {}).items()
                               if k in ('id', 'title', 'sfw_topic', 'nsfw_topic', 'sfw_topic_title', 'nsfw_topic_title')}
    for platform in ('vk', 'e6ai', 'bluesky', 'deviantart'):
        src = cfg.get(platform) or {}
        out.setdefault(platform, {}).update({k: src[k] for k in ('group_id', 'group_name', 'username', 'handle', 'constant_tags', 'rating') if k in src})
    return out


def create(include_originals=False, report=lambda **kw: None):
    from app import db, settings, ui_state
    from app.publish import settings as ps, templates
    bid = uuid.uuid4().hex
    dest = archive_path(bid)
    with tempfile.TemporaryDirectory(dir=folder()) as tmp:
        root = Path(tmp)
        # SQLite's backup API includes committed WAL data without copying a live WAL file.
        with db._lock:
            target = sqlite3.connect(root / 'posts.db')
            try:
                db.conn().backup(target)
            finally:
                target.close()
        for name, value in (
            ('publish.json', _portable_publish(ps.load(raw=True))),
            ('settings.json', {k: v for k, v in settings.load().items() if k in settings.DEFAULTS}),
            ('ui.json', {k: v for k, v in ui_state.load().items() if k in ('draft', 'draft_saved', 'lang', 'theme', 'view', 'platSec')}),
            ('templates.json', {'version': 1, 'items': templates.items()}),
        ):
            (root / name).write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
        sources = [(p.name, p) for p in root.iterdir()]
        for name in sorted(DIRS if include_originals else DIRS - {'uploads'}):
            base = data_dir() / name
            if base.exists():
                sources += [(p.relative_to(data_dir()).as_posix(), p) for p in base.rglob('*') if p.is_file() and not p.is_symlink()]
        manifest = {'version': 1, 'created': datetime.now(timezone.utc).isoformat(timespec='seconds'),
                    'originals': bool(include_originals), 'credentials': False,
                    'directories': sorted(DIRS if include_originals else DIRS - {'uploads'}), 'files': {}}
        total = sum(p.stat().st_size for _, p in sources)
        if total > MAX_BYTES:
            raise ValueError('Данные копии превышают 8 ГБ')
        part = dest.with_suffix('.part')
        try:
            with zipfile.ZipFile(part, 'w', compression=zipfile.ZIP_DEFLATED) as z:
                for i, (name, path) in enumerate(sources):
                    report(progress=i / max(1, len(sources)), message='Создаю резервную копию…')
                    # Snapshot each media file once so the checksum describes the exact archived bytes.
                    h = hashlib.sha256()
                    with path.open('rb') as src, z.open(name, 'w', force_zip64=True) as out:
                        for chunk in iter(lambda: src.read(1024 * 1024), b''):
                            h.update(chunk)
                            out.write(chunk)
                    manifest['files'][name] = h.hexdigest()
                z.writestr('manifest.json', json.dumps(manifest))
            part.replace(dest)
            _metadata(bid, manifest)
        finally:
            part.unlink(missing_ok=True)
    report(progress=1.0, message='Резервная копия создана')
    return bid


def _metadata(bid, manifest):
    info = dict(id=bid, created=manifest['created'], originals=manifest.get('originals', False),
                size=archive_path(bid).stat().st_size)
    atomic_write(folder() / (bid + '.json'), json.dumps(info), backup=False)
    return info


def items():
    rows = []
    for p in folder().glob('*.json'):
        try:
            row = json.loads(p.read_text(encoding='utf-8'))
            if archive_path(row['id']).is_file():
                rows.append(row)
        except (ValueError, OSError, KeyError):
            continue
    return sorted(rows, key=lambda r: r['created'], reverse=True)


def extract_verified(path, dest):
    with zipfile.ZipFile(path) as z:
        entries = z.infolist()
        if len(entries) > 50000 or sum(i.file_size for i in entries) > MAX_BYTES:
            raise ValueError('Резервная копия слишком большая')
        names = [i.filename for i in entries]
        if len(set(names)) != len(names) or 'manifest.json' not in names:
            raise ValueError('Неверный состав резервной копии')
        if z.getinfo('manifest.json').file_size > 5_000_000:
            raise ValueError('Слишком большой манифест копии')
        manifest = json.loads(z.read('manifest.json'))
        if not isinstance(manifest, dict) or manifest.get('version') != 1 or not isinstance(manifest.get('files'), dict) or set(manifest['files']) != set(names) - {'manifest.json'}:
            raise ValueError('Неподдерживаемый формат резервной копии')
        if 'posts.db' not in manifest['files']:
            raise ValueError('В копии отсутствует база данных')
        for name, expected in manifest['files'].items():
            if not _allowed(name):
                raise ValueError('Недопустимый путь в резервной копии')
            if not isinstance(expected, str) or not re.fullmatch(r'[0-9a-f]{64}', expected):
                raise ValueError('Неверная контрольная сумма в манифесте')
            target = dest / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(name) as src, target.open('wb') as out:
                shutil.copyfileobj(src, out, 1024 * 1024)
            if _hash(target) != expected:
                raise ValueError('Контрольная сумма резервной копии не совпала')
            if name in FILES - {'posts.db'}:
                value = json.loads(target.read_text(encoding='utf-8'))
                if not isinstance(value, dict):
                    raise ValueError('Неверный формат настроек в резервной копии')
                if name == 'templates.json':
                    rows = value.get('items', [])
                    if not isinstance(rows, list) or len(rows) > 100 or any(
                        not isinstance(r, dict) or not isinstance(r.get('name'), str)
                        or not isinstance(r.get('draft'), dict)
                        or not re.fullmatch(r'[0-9a-f]{16}', str(r.get('id', ''))) for r in rows
                    ):
                        raise ValueError('Неверный формат шаблонов в резервной копии')
        if not isinstance(manifest.get('created'), str) or not isinstance(manifest.get('originals', False), bool):
            raise ValueError('Неверные сведения о резервной копии')
        datetime.fromisoformat(manifest['created'].replace('Z', '+00:00'))
    for name in manifest.get('directories', sorted(DIRS if manifest.get('originals') else DIRS - {'uploads'})):
        if name not in DIRS:
            raise ValueError('Недопустимая папка в резервной копии')
        (dest / name).mkdir(exist_ok=True)
    c = sqlite3.connect((dest / 'posts.db').as_uri() + '?mode=ro', uri=True)
    try:
        if c.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('База данных в резервной копии повреждена')
    finally:
        c.close()
    return manifest


def receive(src, length):
    if length <= 0 or length > MAX_BYTES:
        raise ValueError('Размер копии должен быть от 1 байта до 8 ГБ')
    bid = uuid.uuid4().hex
    dest = archive_path(bid)
    try:
        with dest.open('wb') as out:
            left = length
            while left:
                chunk = src.read(min(left, 1024 * 1024))
                if not chunk:
                    raise ValueError('Загрузка копии оборвалась')
                out.write(chunk)
                left -= len(chunk)
        with tempfile.TemporaryDirectory(dir=folder()) as tmp:
            manifest = extract_verified(dest, Path(tmp))
        return _metadata(bid, manifest)
    except BaseException:
        dest.unlink(missing_ok=True)
        raise


def stage_restore(bid, idle_check=lambda: True):
    from app import worker
    if worker.status()['running']:
        raise ValueError('Сначала выключите фоновую отправку в настройках')
    with file_lock(data_dir() / 'restore'), _without_worker():
        if pending():
            raise ValueError('Восстановление уже назначено — перезапустите приложение')
        with tempfile.TemporaryDirectory(dir=folder()) as tmp:
            extract_verified(archive_path(bid), Path(tmp))
        # Keep a usable snapshot of the current data before replacing it on next startup.
        marker = data_dir() / 'pending-restore.json'
        with file_lock(data_dir() / 'publication-start'):
            if not idle_check():
                raise ValueError('Дождитесь завершения фоновых задач перед восстановлением')
            atomic_write(marker, json.dumps({'id': bid, 'rollback_id': None}), backup=False)
        try:
            rollback_id = create(include_originals=True)
            worker.set_off(True)
            atomic_write(marker, json.dumps({'id': bid, 'rollback_id': rollback_id}), backup=False)
        except BaseException:
            marker.unlink(missing_ok=True)
            raise
    return {'restart': True, 'rollback_id': rollback_id}


def apply_pending():
    try:
        return _apply_pending()
    except Exception as e:
        # A completed rollback is safe to open, but keep sending disabled until
        # the user dismisses the failed restore. Never hide an incomplete rollback.
        if (data_dir() / '.restore-work' / 'transaction.json').exists():
            raise
        from app.jobs import mask_secrets
        p = data_dir() / 'pending-restore.json'
        request = json.loads(p.read_text(encoding='utf-8'))
        request['error'] = mask_secrets(str(e))
        atomic_write(p, json.dumps(request), backup=False)
        return False


def _apply_pending():
    """Called before importing Api/db at application startup; roll back any interrupted apply."""
    if not pending():
        return False
    if restore_error():
        return False
    with file_lock(data_dir() / 'restore'), _without_worker():
        root = data_dir()
        stage = root / '.restore-work'
        transaction = stage / 'transaction.json'
        rollback = stage / 'previous'

        def undo():
            info = json.loads(transaction.read_text(encoding='utf-8'))
            for name, existed in reversed(list(info.items())):
                old, target = rollback / name, root / name
                if old.exists():
                    if target.is_dir():
                        shutil.rmtree(target)
                    else:
                        target.unlink(missing_ok=True)
                    old.replace(target)
                elif not existed and target.exists():
                    shutil.rmtree(target) if target.is_dir() else target.unlink()
            transaction.unlink(missing_ok=True)

        if transaction.exists():
            undo()
        shutil.rmtree(stage, ignore_errors=True)
        stage.mkdir()
        request = json.loads((root / 'pending-restore.json').read_text(encoding='utf-8'))
        if not request.get('rollback_id'):
            (root / 'pending-restore.json').unlink()
            return False
        new = stage / 'new'
        new.mkdir()
        extract_verified(archive_path(request['id']), new)
        if (new / 'ui.json').exists():
            ui = json.loads((new / 'ui.json').read_text(encoding='utf-8'))
            for key in ('draft', 'draft_saved'):
                if isinstance(ui.get(key), dict):
                    ui[key].pop('editing_schedule', None)
            (new / 'ui.json').write_text(json.dumps(ui, ensure_ascii=False), encoding='utf-8')
        # Apply only portable settings and preserve this machine's existing credential bindings.
        from app.publish import settings as ps
        current = ps.load(raw=True)
        imported = json.loads((new / 'publish.json').read_text(encoding='utf-8')) if (new / 'publish.json').exists() else {}
        cfg = ps._merge(current, _portable_publish(imported))
        cfg['pipeline']['enabled'] = False
        (new / 'publish.json').write_text(json.dumps(cfg, ensure_ascii=False), encoding='utf-8')
        c = sqlite3.connect(new / 'posts.db')
        try:
            tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'scheduled' in tables:
                c.execute("UPDATE scheduled SET state='missed', error='восстановлено из копии — проверьте время и файлы' WHERE state IN ('pending','running','queued','editing')")
                cols = {r[1] for r in c.execute('PRAGMA table_info(scheduled)')}
                for col in ('edit_token', 'edit_prev'):
                    if col in cols:
                        c.execute(f'UPDATE scheduled SET {col}=NULL')
            if 'publications' in tables:
                for pid, text in c.execute('SELECT id,dests FROM publications').fetchall():
                    dests = json.loads(text or '[]')
                    for d in dests:
                        if d.get('state') in ('running', 'queued'):
                            d.update(state='error', outcome='unknown', message='Восстановлено из копии — проверьте площадку')
                    c.execute('UPDATE publications SET dests=? WHERE id=?', (json.dumps(dests, ensure_ascii=False), pid))
            c.commit()
        finally:
            c.close()
        names = [p.name for p in new.iterdir()]
        names += ['posts.db-wal', 'posts.db-shm']
        rollback.mkdir()
        atomic_write(transaction, json.dumps({n: (root / n).exists() for n in names}), backup=False)
        try:
            for name in names:
                if (root / name).exists():
                    (root / name).replace(rollback / name)
                if (new / name).exists():
                    (new / name).replace(root / name)
        except BaseException:
            undo()
            raise
        (root / 'pending-restore.json').unlink()
        shutil.rmtree(stage)
    return True
