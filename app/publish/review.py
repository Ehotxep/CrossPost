"""The same draft/configuration used by the sender, presented without sending anything."""
from datetime import datetime, timezone
import html
import re
import requests
from app.jobs import mask_secrets
from app.publish import runner, settings, templates


def composition(draft):
    cfg = templates.effective(settings.load(), draft)
    plan = runner.plan(draft)
    files = runner._files(draft)
    result = []
    for platform in settings.platform_order(cfg):
        dests = [d for d in plan['dests'] if d['platform'] == platform and d['will']]
        if not dests:
            continue
        ids = runner.select_ids(cfg, draft, [f['id'] for f in files], platform)
        selected = [f for f in files if f['id'] in ids]
        omitted = []
        if platform == 'bluesky' and all(f['kind'] == 'image' for f in selected):
            omitted = [f['name'] for f in selected[4:]]
            selected = selected[:4]
        title, _, lang = runner.variant(cfg, draft, platform)
        text = runner.signed_text(cfg, draft, platform)
        include = title and cfg.get(platform, {}).get('include_title', True)
        warnings = []
        fmt = 'text'
        if platform == 'telegram':
            caption, mode = runner.tg_bot._caption(text, title, cfg[platform].get('include_title', True))
            text = caption
            fmt = mode or 'text'
            if len(html.unescape(re.sub(r'<[^>]+>', '', caption))) > 1024:
                warnings.append('Текст уйдёт отдельным сообщением перед медиа')
                if len(caption) > 4096:
                    warnings.append('Отдельное сообщение будет обрезано до 4096 символов')
                    text = caption[:4096]
        elif platform == 'discord':
            from platforms import discord
            text = discord.short_links(text) if cfg[platform].get('short_links', True) else text
            text = (f'**{title}**\n{text}' if include else text).strip()
            fmt = 'Markdown'
            if len(text) > 2000:
                warnings.append('Текст Discord будет обрезан до 2000 символов')
                text = text[:2000]
        elif platform == 'bluesky':
            from platforms import bluesky
            text = (f'{title}\n\n{text}' if include else text).strip()
            full = bluesky.build_text(text, 10**6)[0]
            text = bluesky.build_text(text)[0]
            if text != full:
                warnings.append('Текст Bluesky будет сокращён до лимита площадки')
        elif platform in ('vk', 'joyreactor'):
            text = (f'{title}\n\n{text}' if include else text).strip()
        elif platform == 'e6ai':
            text = ((f'[b]{title}[/b]\n' if title else '') + runner.variant(cfg, draft, platform)[1]).strip() if cfg['e6ai'].get('with_description', True) else ''
            fmt = 'DText'
            if cfg['e6ai'].get('with_sources', True):
                warnings.append('Ссылки на опубликованные копии добавятся после отправки на другие площадки')
        if platform in ('reddit', 'joyreactor'):
            warnings += ['Браузерная интеграция экспериментальная; выполните пробный прогон в настройках']
            warnings += [d['name'] + ': ' + '; '.join(d['details']) for d in dests]
        previews = []
        for f in selected:
            tags = runner.e6_tags(cfg, draft, f['id']) if platform == 'e6ai' else []
            original = platform == 'e6ai'
            if platform == 'deviantart':
                from platforms import deviantart
                original = cfg['deviantart'].get('originals', True) and f['size'] <= deviantart.FILE_MAX_BYTES
            transform = ('Оригинал' if original else
                         f"JPEG-превью: масштаб {cfg['light']['scale']}, качество {cfg['light']['quality']}") if f['kind'] == 'image' else 'Исходный файл; перекодирование при необходимости по лимитам площадки'
            previews.append({k: f.get(k) for k in ('id', 'name', 'kind', 'size', 'width', 'height')})
            previews[-1].update(transform=transform, tags=tags)
            if platform == 'bluesky':
                previews[-1]['alt'] = (draft.get('alts') or {}).get(f['id']) or (title if cfg['bluesky'].get('alt_from_title', True) else '')
        if omitted:
            warnings.append('Не поместятся в пост: ' + ', '.join(omitted))
        result.append(dict(platform=platform, destinations=[d['name'] for d in dests],
                           title=title, text=text, language=lang, format=fmt, files=previews,
                           details=list(dict.fromkeys(t for d in dests for t in d.get('details', []))), warnings=warnings))
    return {'platforms': result, 'errors': plan['errors'], 'notes': plan.get('notes', [])}


def readiness(draft, remote=False):
    cfg = templates.effective(settings.load(), draft)
    try:
        plan = runner.plan(draft)
        selected = [d for d in plan['dests'] if d['will']]
        checks = [dict(name='Состав поста и ограничения', state='error' if plan['errors'] else 'ok',
                       message='; '.join(plan['errors']) or 'Локальные проверки пройдены')]
        if not selected:
            checks.append(dict(name='Получатели', state='error', message='Не выбрано ни одного места публикации'))
    except (ValueError, OSError) as e:
        return {'ready': False, 'checked_at': datetime.now(timezone.utc).isoformat(),
                'checks': [dict(name='Файлы поста', state='error', message=mask_secrets(str(e)))]}
    for d in selected:
        check = dict(name=d['name'], state='unchecked', message='Доступ к площадке не проверен; проверка не публикует пост')
        if remote:
            try:
                platform = d['platform']
                if platform == 'telegram' and d['key'] != 'telegram:group' and cfg['telegram'].get('transport') != 'mtproto':
                    ch = cfg['telegram']['channels'][int(d['key'].split(':')[1])]
                    info = runner.tg_bot.check_chat(cfg['telegram']['bot_token'], ch['id'], timeout=15)
                    check.update(state='ok' if info['ok'] else 'error', message='; '.join(info['problems']) or 'Бот имеет право публиковать в канале')
                elif platform == 'discord':
                    hook = cfg['discord']['webhooks'][int(d['key'].split(':')[1])]['url']
                    response = requests.get(hook, timeout=(5, 15))
                    check.update(state='ok' if response.status_code == 200 else 'error',
                                 message='Вебхук доступен' if response.status_code == 200 else f'Вебхук ответил HTTP {response.status_code}')
                elif platform == 'bluesky':
                    from platforms import bluesky
                    session = runner.core_common.load_token('bluesky') or {}
                    response = requests.get(bluesky._home(cfg['bluesky'], session) + '/xrpc/com.atproto.server.getSession',
                                            headers={'Authorization': 'Bearer ' + session.get('accessJwt', '')}, timeout=(5, 15))
                    check.update(state='ok' if response.status_code == 200 else 'error', message='Сессия действительна' if response.status_code == 200 else f'Проверка сессии: HTTP {response.status_code}')
                else:
                    check['message'] = 'Вход и права нужно проверить в настройках площадки; автоматическая проверка здесь недоступна'
            except Exception as e:
                check.update(state='error', message=mask_secrets(str(e)))
        checks.append(check)
    checks.append(dict(name='Перед отправкой', state='warning', message='Права и доступность могут измениться после проверки. Эта проверка ничего не отправляет.'))
    return {'ready': not any(c['state'] == 'error' for c in checks),
            'access_verified': bool(selected) and all(c['state'] == 'ok' for c in checks[1:-1]),
            'checked_at': datetime.now(timezone.utc).isoformat(timespec='seconds'), 'checks': checks}
