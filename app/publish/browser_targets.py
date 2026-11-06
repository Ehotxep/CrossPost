"""Local preparation rules for experimental browser destinations; no network calls."""
from core.common import NotPublishedError

SUBREDDITS = ('FurAI', 'aiyiff')
FLAIRS = {
    'FurAI': ('SFW', 'NSFW', 'Request', 'Discussion', 'Guide/Advice', 'Animation'),
    'aiyiff': ('Discussion', 'News', 'Female', 'Male', 'Straight', 'Gay', 'Lesbian',
              'Threesome', 'Orgy', 'Intersex', 'Other'),
}
DEFAULT_RULES = [
    {'all': ['intersex'], 'flair': 'Intersex', 'priority': 30},
    {'all': ['male/male'], 'flair': 'Gay', 'priority': 20},
    {'all': ['female/female'], 'flair': 'Lesbian', 'priority': 20},
    {'all': ['male/female'], 'flair': 'Straight', 'priority': 20},
    {'all': ['female'], 'none': ['male', 'intersex'], 'flair': 'Female', 'priority': 10},
    {'all': ['male'], 'none': ['female', 'intersex'], 'flair': 'Male', 'priority': 10},
]


def validate_changes(platform, changes):
    """Do not let client settings forge login status or inject arbitrary site URLs."""
    import copy
    allowed = {'enabled', 'show_window'}
    allowed |= ({'communities', 'flair_rules'} if platform == 'reddit' else
                {'constant_tags', 'ai_tags', 'rating_tags', 'tag_aliases'})
    if not isinstance(changes, dict) or set(changes) - allowed:
        raise ValueError('Неизвестные настройки браузерной площадки')
    out = copy.deepcopy(changes)
    for key in ('enabled', 'show_window'):
        if key in out and not isinstance(out[key], bool):
            raise ValueError('Переключатель должен быть логическим значением')
    if 'communities' in out:
        if not isinstance(out['communities'], dict) or set(out['communities']) - set(SUBREDDITS):
            raise ValueError('Поддерживаются только r/FurAI и r/aiyiff')
        if any(not isinstance(on, bool) for on in out['communities'].values()):
            raise ValueError('Неверный выбор сообщества')
    if 'flair_rules' in out:
        rules = out['flair_rules']
        if not isinstance(rules, list) or len(rules) > 100:
            raise ValueError('До 100 правил flair')
        for rule in rules:
            if (not isinstance(rule, dict) or set(rule) - {'all', 'none', 'flair', 'priority'}
                    or rule.get('flair') not in FLAIRS['aiyiff']
                    or not isinstance(rule.get('priority', 0), int)
                    or not rule.get('all')):
                raise ValueError('Правило flair: all, необязательные none и priority, известный flair')
            for key in ('all', 'none'):
                _tags(rule.get(key, []), key)
    for key in ('constant_tags', 'ai_tags'):
        if key in out:
            _tags(out[key], key)
    if 'rating_tags' in out:
        if not isinstance(out['rating_tags'], dict) or set(out['rating_tags']) - {'sfw', 'erotic', 'explicit'}:
            raise ValueError('Неизвестный рейтинг JoyReactor')
        for tags in out['rating_tags'].values():
            _tags(tags, 'rating_tags')
    if 'tag_aliases' in out:
        if not isinstance(out['tag_aliases'], dict) or len(out['tag_aliases']) > 500:
            raise ValueError('Неверная таблица тегов JoyReactor')
        for source, target in out['tag_aliases'].items():
            _tags([source], 'tag_aliases')
            _tags([target], 'tag_aliases')
    return out


def _tags(value, label):
    if (not isinstance(value, list) or len(value) > 500 or
            any(not isinstance(t, str) or not t.strip() or len(t) > 100 or
                any(c in t for c in ',\n\r') for t in value)):
        raise ValueError(f'{label}: список непустых тегов без запятых и переносов строк')


def image_tags(draft, file_ids):
    """Edited booru tags override predictions, including an explicitly empty list."""
    from app.publish.hashtags import _preds
    chosen = draft.get('e6ai_tags') or {}
    tags = set()
    for uid in file_ids:
        if uid in chosen:
            values = chosen[uid]
        elif draft.get('tagger', True):
            values = [t for t, probability in _preds(uid) if probability >= .5]
        else:
            values = []
        tags.update(str(t).strip().lower().replace(' ', '_') for t in values or [])
    return tags


def reddit_flair(cfg, draft, community, file_ids):
    if community not in SUBREDDITS:
        raise NotPublishedError('Неизвестное сообщество Reddit')
    overrides = draft.get('reddit_flairs') or {}
    if community in overrides:
        flair = overrides[community]
        if flair not in FLAIRS[community] and not (community == 'aiyiff' and flair == ''):
            raise NotPublishedError('Неверный flair Reddit')
        return flair
    if community == 'FurAI':
        return 'SFW' if draft.get('rating') == 'sfw' else 'NSFW'
    tags = image_tags(draft, file_ids)
    matches = [r for r in cfg['reddit']['flair_rules']
               if {t.lower().replace(' ', '_') for t in r['all']} <= tags
               and not ({t.lower().replace(' ', '_') for t in r.get('none', [])} & tags)]
    if not matches:
        return ''
    priority = max(r.get('priority', 0) for r in matches)
    winners = {r['flair'] for r in matches if r.get('priority', 0) == priority}
    if len(winners) != 1:
        raise NotPublishedError('Несколько подходящих flair r/aiyiff — выберите вручную')
    return winners.pop()


def joy_tags(cfg, draft, file_ids):
    joy = cfg['joyreactor']
    if not joy.get('ai_tags'):
        raise NotPublishedError('Укажите обязательные теги ИИ для JoyReactor в настройках')
    if draft.get('rating') != 'sfw' and not (joy.get('rating_tags') or {}).get(draft.get('rating')):
        raise NotPublishedError('Укажите теги выбранного рейтинга JoyReactor в настройках')
    aliases = joy.get('tag_aliases') or {}
    result = list(joy.get('ai_tags') or []) + list(joy.get('constant_tags') or [])
    result += (joy.get('rating_tags') or {}).get(draft.get('rating'), [])
    result += [aliases[t] for t in sorted(image_tags(draft, file_ids)) if t in aliases]
    # Unknown booru tags are not blindly sent to a site with its own vocabulary.
    return list(dict.fromkeys(t.strip() for t in result if t.strip()))
