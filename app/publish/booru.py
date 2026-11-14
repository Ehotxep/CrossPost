"""Independent e621ng destinations, with compatibility for pre-split drafts."""
from contextlib import contextmanager

from app.publish import settings

PLATFORMS = settings.BOORU_PLATFORMS


def fields(cfg, draft, platform):
    slots = draft.get('booru_posts')
    if isinstance(slots, dict):
        return slots.get(platform) or {'tags': {}, 'meta': {}}
    legacy = cfg.get('legacy_booru_site', 'e6ai')
    if platform == legacy:
        return {'tags': draft.get('e6ai_tags') or {}, 'meta': draft.get('e6ai_meta') or {}}
    return {'tags': {}, 'meta': {}}


def excludes(cfg, draft):
    out = set(draft.get('exclude') or [])
    legacy = cfg.get('legacy_booru_site', 'e6ai')
    if not isinstance(draft.get('booru_posts'), dict) and legacy != 'e6ai':
        if 'e6ai' in out:
            out.remove('e6ai')
            out.add(legacy)
    return out


@contextmanager
def tag_context(platform):
    from app import e6tags
    with e6tags.for_site(platform):
        yield


def tag_settings(platform=None):
    from app import e6tags
    return settings.load()[platform or e6tags.site()]


def destinations(value):
    """Read old single-slot history without treating an e621/floofgens post as blep."""
    import json
    rows = json.loads(value or '[]') if isinstance(value, str) or value is None else value
    out = []
    for original in rows:
        row = dict(original)
        if row.get('key') == 'e6ai':
            site = settings.booru_site_for_url(row.get('link') or '')
            if site is None:
                label = str(row.get('name') or '').lower()
                name = label.split(' · ', 1)[0].strip()
                site = 'floofgens' if name == 'floofgens.net' else 'e621' if name == 'e621' else 'e6ai'
            row['key'] = row['platform'] = site
        out.append(row)
    return out


def legacy_profile(cfg, profile):
    import copy
    out = copy.deepcopy(profile)
    site = cfg.get('legacy_booru_site', 'e6ai')
    if site == 'e6ai':
        return out
    if 'e6ai' in out:
        out[site] = out.pop('e6ai')
    for field in ('text_lang', 'versions'):
        if isinstance(out.get(field), dict) and 'e6ai' in out[field]:
            out[field][site] = out[field].pop('e6ai')
    if isinstance(out.get('platforms'), list):
        out['platforms'] = [site if key == 'e6ai' else key for key in out['platforms']]
    return out
