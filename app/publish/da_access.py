"""Validation for DeviantArt paid downloads and subscription access."""
import re

FIELDS = ('premium_download', 'premium_price_usd', 'subscriber_only', 'subscriber_tiers')



def validate_changes(changes):
    out = dict(changes)
    for field in ('premium_download', 'subscriber_only'):
        if field in out and type(out[field]) is not bool:
            raise ValueError('Платный режим DeviantArt должен быть переключателем')
    if 'premium_price_usd' in out:
        value = out['premium_price_usd']
        if not isinstance(value, str):
            raise ValueError('Цена DeviantArt должна быть строкой в USD')
        value = value.strip().replace(',', '.')
        if value:
            if not re.fullmatch(r'\d{1,7}(?:\.\d{1,2})?', value) or float(value) <= 0:
                raise ValueError('Укажите положительную цену в USD, не более двух знаков после запятой')
            from decimal import Decimal
            value = str(Decimal(value).quantize(Decimal('.01')))
        out['premium_price_usd'] = value
    if 'subscriber_tiers' in out:
        tiers = out['subscriber_tiers']
        if not isinstance(tiers, list) or len(tiers) > 50 or any(not isinstance(tier,str) or not tier.strip() or len(tier)>100 for tier in tiers):
            raise ValueError('Укажите названия тиров DeviantArt списком, до 100 символов каждое')
        out['subscriber_tiers'] = list(dict.fromkeys(tier.strip() for tier in tiers))
    return out


def requested(cfg):
    return bool(cfg.get('premium_download') or cfg.get('subscriber_only'))


def blocked_reason(cfg):
    if not requested(cfg):
        return ''
    try:
        cfg=validate_changes(cfg)
    except ValueError as error:
        return str(error)
    if cfg.get('premium_download') and not cfg.get('premium_price_usd'):
        return 'Укажите цену платного скачивания DeviantArt в USD'
    if cfg.get('subscriber_only') and not cfg.get('subscriber_tiers'):
        return 'Выберите хотя бы один тир DeviantArt'
    if not cfg.get('browser_ok'):
        return 'Для платной публикации войдите в DeviantArt через браузер'
    return ''

