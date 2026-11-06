"""Reusable post content and presentation settings; never account credentials or files."""
import copy
import json
import secrets
import hashlib
from datetime import datetime, timezone
from app.paths import data_dir
from core.fsutil import file_lock, read_json, atomic_write

FIELDS = ('title', 'text', 'rating', 'signature', 'hashtags', 'variants', 'multi', 'exclude', 'platform_texts', 'reddit_flairs')
PROFILE = ('light', 'signatures', 'hashtags', 'versions', 'text_lang', 'platform_order')
OPTIONS = {
    'telegram': ('include_title', 'spoiler_nsfw'),
    'discord': ('include_title', 'no_previews', 'blur_nsfw', 'max_file_mb', 'short_links'),
    'reddit': (), 'joyreactor': (),
    'vk': ('include_title',), 'bluesky': ('include_title', 'alt_from_title', 'langs'),
    'deviantart': ('is_ai_generated', 'noai', 'originals', 'tags_from'),
    'e6ai': ('constant_tags', 'with_description', 'with_sources'),
}


def clean_profile(profile):
    if not isinstance(profile, dict):
        return {}
    out = {k: copy.deepcopy(profile[k]) for k in PROFILE if k in profile}
    if isinstance(profile.get('platforms'), list):
        out['platforms'] = [p for p in profile['platforms'] if p in OPTIONS]
    for platform, fields in OPTIONS.items():
        if isinstance(profile.get(platform), dict):
            out[platform] = {k: copy.deepcopy(profile[platform][k]) for k in fields if k in profile[platform]}
    return out


def effective(cfg, draft):
    from app.publish.settings import _merge
    profile = clean_profile(draft.get('profile'))
    selected = profile.pop('platforms', None)
    result = _merge(cfg, profile)
    if selected is not None:
        for platform in OPTIONS:
            if platform not in selected:
                result[platform]['enabled'] = False
    return result


def _path():
    return data_dir() / 'templates.json'


def _targets(cfg):
    out = {k: k for k in ('vk', 'bluesky', 'deviantart', 'e6ai', 'joyreactor')}
    for i, ch in enumerate(cfg['telegram']['channels']):
        out[f'telegram:{i}'] = 'telegram:' + str(ch['id'])
    out['telegram:group'] = 'telegram:group:' + str(cfg['telegram']['group'].get('id', ''))
    for i, hook in enumerate(cfg['discord']['webhooks']):
        out[f'discord:{i}'] = 'discord:' + hashlib.sha256(hook['url'].encode()).hexdigest()
    out.update({f'reddit:{name}': f'reddit:{name}' for name in ('FurAI', 'aiyiff')})
    return out


def items():
    with file_lock(_path()):
        return read_json(_path()).get('items', [])


def save(name, draft):
    from app.publish import settings
    name = str(name).strip()
    if not name or len(name) > 80:
        raise ValueError('Название шаблона: от 1 до 80 символов')
    cfg = effective(settings.load(), draft)
    profile = clean_profile(cfg)
    profile['platforms'] = [k for k in OPTIONS if cfg[k].get('enabled')]
    content = {k: copy.deepcopy(draft[k]) for k in FIELDS if k in draft}
    targets = _targets(cfg)
    excluded = [targets[key] for key in draft.get('exclude', []) if key in targets]
    content['exclude'] = []   # индексы каналов могут поменяться до следующего применения
    content['profile'] = profile
    if len(json.dumps(content).encode()) > 100_000:
        raise ValueError('Шаблон слишком большой')
    with file_lock(_path()):
        rows = items()
        if len(rows) >= 100:
            raise ValueError('Уже сохранено 100 шаблонов — удалите ненужные')
        item = dict(id=secrets.token_hex(8), name=name, draft=content, excluded_targets=excluded,
                    updated=datetime.now(timezone.utc).isoformat(timespec='seconds'))
        rows.append(item)
        atomic_write(_path(), json.dumps({'version': 1, 'items': rows}, ensure_ascii=False))
    return item


def get(tid):
    row = next((r for r in items() if r['id'] == tid), None)
    if row is None:
        raise ValueError('Шаблон не найден')
    if 'excluded_targets' in row:
        from app.publish import settings
        row = copy.deepcopy(row)
        row['draft']['exclude'] = [key for key, target in _targets(settings.load(raw=True)).items()
                                   if target in row['excluded_targets']]
    return row


def remove(tid):
    with file_lock(_path()):
        rows = [r for r in items() if r['id'] != tid]
        atomic_write(_path(), json.dumps({'version': 1, 'items': rows}, ensure_ascii=False))
