"""Presentation filters for useful trend topics; original tag data stays intact."""
import re

# Structural attributes rarely help choose the next subject. They remain searchable
# and can be restored in the overview with 'show common tags'.
COMMON = set('''solo duo trio group zero_pictured male female ambiguous_gender andromorph gynomorph herm maleherm
anthro feral humanoid human taur clothed nude fully_clothed looking_at_viewer smile standing sitting
fur body_fur tail ears animal_ears hands feet fingers toes claws teeth tongue hair eyes nose mouth
breasts nipples penis pussy anus genitals balls butt hi_res absurd_res low_res ai_generated tagme
simple_background white_background transparent_background english_text signature watermark artist_name'''.split())
BASIC_DETAIL = re.compile(r'^(?:(?:blue|green|brown|red|yellow|orange|purple|pink|white|black|grey|gray)_eyes|(?:one|two|three|four|five|six|1|2|3|4|5|6)_(?:boy|boys|girl|girls|tails|ears))$')
CLOTHING = set('''armor clothing uniform military_uniform maid_uniform dress skirt shirt t-shirt pants shorts
jacket coat hoodie sweater scarf hat cap boots shoes gloves socks stockings underwear lingerie swimsuit
bikini kimono robe cloak cape vest suit spacesuit helmet jewelry necklace earrings glasses sunglasses camouflage'''.split())
SETTING = set('''desert underwater beach forest city town village mountains mountain river lake ocean sea
space spaceship ruins castle bedroom bathroom kitchen indoors outdoors inside outside nature garden
snow rain night sunset sunrise winter spring summer autumn fall snowing raining camping cyberpunk'''.split())


def group(tag, category):
    if category != 'general':
        return category
    if tag in CLOTHING or tag.endswith(('_uniform','_dress','_jacket','_armor','_clothing')):
        return 'clothing'
    if tag in SETTING or tag.endswith(('_background','_scenery','_landscape')):
        return 'setting'
    return 'general'


def common(tag, category, share=0.0):
    return category == 'general' and (tag in COMMON or bool(BASIC_DETAIL.fullmatch(tag)) or share >= .4)


def mask(model, category='all', style=(), mine_only=False, hidden=(), show_common=False):
    import numpy as np
    recent = model.cols(13)
    denominator = max(float(model.site_n[recent].sum()), 1)
    shares = model.N[:,recent].sum(1) / denominator
    hidden, style = set(hidden), set(style)
    selected = np.array([t not in hidden for t in model.tags], dtype=bool)
    if category in ('clothing','setting'):
        selected &= np.array([group(t,c) == category for t,c in zip(model.tags,model.cat)],dtype=bool)
    elif category != 'all':
        selected &= model.cat == category
    if mine_only:
        selected &= np.array([t in style for t in model.tags],dtype=bool)
    suppressed = np.array([common(t,c,float(s)) for t,c,s in zip(model.tags,model.cat,shares)],dtype=bool)
    count = int((selected & suppressed).sum()) if not show_common else 0
    if not show_common:
        selected &= ~suppressed
    return selected, count
