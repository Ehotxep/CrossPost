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


def resource_link(platform, url):
    """Derive a channel/profile/community from a public post URL without HTTP."""
    import re
    host, path = url.hostname.lower(), url.path
    if platform == 'telegram' and host in ('t.me','www.t.me','telegram.me','www.telegram.me'):
        match = re.fullmatch(r'/(?:s/)?([A-Za-z][A-Za-z0-9_]{2,63})/\d+(?:/\d+)?/?', path)
        return 'https://t.me/' + match[1] if match else None
    if platform == 'vk' and host in ('vk.com','www.vk.com','m.vk.com','vk.ru','www.vk.ru','m.vk.ru'):
        match = re.match(r'^/(?:wall|photo|video|clip)(-?\d+)_\d+', path)
        if match:
            owner = int(match[1])
            return 'https://vk.com/' + ('club' + str(-owner) if owner < 0 else 'id' + str(owner))
        if re.fullmatch(r'/[A-Za-z][A-Za-z0-9_.]{1,63}/?', path):
            return 'https://vk.com' + path.rstrip('/')
        return 'https://vk.com/'
    if platform == 'bluesky' and host in ('bsky.app','www.bsky.app'):
        match = re.match(r'^/profile/([^/]+)(?:/|$)', path)
        return 'https://bsky.app/profile/' + match[1] if match else 'https://bsky.app/'
    if platform == 'deviantart':
        if host in ('deviantart.com','www.deviantart.com'):
            match = re.match(r'^/([^/]+)/art/', path)
            return 'https://www.deviantart.com/' + match[1] if match else 'https://www.deviantart.com/'
        if re.fullmatch(r'[a-z0-9-]+\.deviantart\.com', host):
            return 'https://' + host + '/'
    if platform == 'reddit' and host in ('reddit.com','www.reddit.com','old.reddit.com','new.reddit.com'):
        match = re.match(r'^/(r|u|user)/([A-Za-z0-9_-]+)(?:/|$)', path)
        if match:
            prefix = 'r' if match[1] == 'r' else 'user'
            return f'https://www.reddit.com/{prefix}/{match[2]}/'
        return 'https://www.reddit.com/'
    if platform == 'joyreactor' and host in ('joyreactor.cc','www.joyreactor.cc'):
        return 'https://joyreactor.cc/'
    if platform in PLATFORMS:
        from urllib.parse import urlsplit
        base = settings.BOORU[platform]
        if host == urlsplit(base).hostname:
            return base + '/'
    if platform == 'discord' and host in ('discord.com','discordapp.com','www.discord.com'):
        match = re.match(r'^/channels/(\d+)/(\d+)(?:/|$)', path)
        return f'https://discord.com/channels/{match[1]}/{match[2]}' if match else None
    return None


def sources_for(cfg, site, rows):
    """Public links from selected, completed destinations, independent per site."""
    from urllib.parse import urlsplit
    options = cfg.get(site) or {}
    if not options.get('with_sources', True):
        return []
    selected = options.get('source_platforms', {})
    if not isinstance(selected, dict):
        return []
    found, seen = [], set()
    for dest in destinations(rows):
        platform = dest.get('platform') or str(dest.get('key') or '').split(':', 1)[0]
        if platform not in settings.PLATFORM_MODULES or platform == site or selected.get(platform, True) is not True:
            continue
        link = dest.get('link')
        if dest.get('state') != 'done' or not isinstance(link, str):
            continue
        link = link.strip()
        try:
            url = urlsplit(link)
            if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password:
                continue
            if url.hostname.lower() in ('t.me','telegram.me','www.t.me','www.telegram.me') and url.path.startswith('/c/'):
                continue  # private Telegram post links cannot serve as public sources
        except ValueError:
            continue
        if options.get('source_link_mode', 'post') == 'resource':
            link = resource_link(platform, url)
            if not link:
                continue
        if link not in seen:
            found.append(link)
            seen.add(link)
        if len(found) == 10:
            break
    return found
