"""Deterministic weekly counts, shared by metrics and local browser checks."""
import json
import math
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from app.trends.aggregate import week_of


def write_fixture(data, source='floofgens'):
    folder=Path(data)/'trends'/source
    folder.mkdir(parents=True,exist_ok=True)
    day='2026-11-14'
    end=week_of(day)-2
    categories={'solo':'general','smile':'general','ubiquitous_feature':'general','armor':'general',
                'maid_uniform':'general','desert':'general','wolf':'species','hero_(example)':'character'}
    rows=[]
    site=[]
    for week in range(end-103,end+1):
        site.append({'week':week,'rating':'s','n':1000,'sy':1000*math.log(2)})
        for tag in categories:
            count={'solo':700,'smile':20,'ubiquitous_feature':500,'armor':12,'maid_uniform':10,'desert':9,'wolf':12,'hero_(example)':8}[tag]
            lift=3 if tag in ('solo','smile','ubiquitous_feature') else 1.5
            if tag=='armor' and week>end-4:count,lift=40,2.2
            rows.append({'tag':tag,'week':week,'rating':'s','n':count,'sy':count*(math.log(2)+math.log(lift))})
    pd.DataFrame(rows).to_pickle(folder/'tags_weekly.pkl')
    pd.DataFrame(site).to_pickle(folder/'site_weekly.pkl')
    (folder/'meta.json').write_text(json.dumps({'source':source,'dump_date':day,'built_at':datetime.now(timezone.utc).isoformat(),
                                               'posts':104000,'tags':len(categories),'categories':categories}),encoding='utf-8')
    return folder
