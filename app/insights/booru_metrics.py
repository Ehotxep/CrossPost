"""Observed booru checkpoints and comparisons within one publisher's history.

Counts are recorded only inside the checkpoint's grace window. Missed checkpoints
are never reconstructed from current totals or estimated from Telegram curves.
"""
import json
import math
import re
import threading
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import numpy as np
from app import db
from app.publish import settings as PS, booru

CHECKPOINTS = {24: 2, 72: 6, 168: 12}  # hours -> allowed delay; never capture early
MIN_BASELINE = 10
BASELINE_DAYS = 180
_lock = threading.Lock()
_schema_connection = None
_last_tick = 0.0


def now_utc():
    return datetime.now(timezone.utc)


def dt(value):
    try:
        value = datetime.fromisoformat(str(value).replace('Z','+00:00'))
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def ensure_schema():
    global _schema_connection
    connection = db.conn()
    if _schema_connection is connection:
        return
    with db.tx() as c:
        c.execute('''CREATE TABLE IF NOT EXISTS booru_items (
            site TEXT, post_id INTEGER, link TEXT, published_at TEXT, owner TEXT, rating TEXT, kind TEXT,
            verified INTEGER DEFAULT 0, last_checked TEXT, last_error TEXT, PRIMARY KEY(site,post_id))''')
        c.execute('''CREATE TABLE IF NOT EXISTS booru_publication_items (
            pub_id INTEGER, site TEXT, post_id INTEGER, position INTEGER,
            PRIMARY KEY(pub_id,site,post_id))''')
        c.execute('''CREATE TABLE IF NOT EXISTS booru_checkpoints (
            site TEXT, post_id INTEGER, checkpoint INTEGER, taken_at TEXT, age_h REAL,
            owner TEXT, rating TEXT, kind TEXT, favs INTEGER, score INTEGER, comments INTEGER,
            PRIMARY KEY(site,post_id,checkpoint))''')
        c.execute('CREATE INDEX IF NOT EXISTS booru_cohort ON booru_checkpoints(site,owner,rating,kind,checkpoint)')
    _schema_connection = connection


def identity(link):
    site = PS.booru_site_for_url(link)
    if not site:
        return None
    match = re.fullmatch(r'/posts/([0-9]+)/?',urlparse(link).path)
    return (site,int(match[1])) if match else None


def references(dest):
    values = dest.get('post_refs') or ([{'url':dest.get('link')}] if dest.get('state') == 'done' and dest.get('link') else [])
    found=[]; seen=set()
    for value in values:
        link=value.get('url') if isinstance(value,dict) else None
        key=identity(link)
        if key and key not in seen:
            found.append((key,link,value));seen.add(key)
    return found


def register(pub, dest):
    """Local registration only; no HTTP requests and no credentials are stored."""
    refs=references(dest)
    if not refs:
        return []
    ensure_schema()
    with db.tx() as c:
        for position,((site,pid),link,ref) in enumerate(refs):
            # A hint helps schedule the first request. The API must verify the real date.
            hint=dt(ref.get('sent_at') or pub.get('created'))
            c.execute('INSERT OR IGNORE INTO booru_items(site,post_id,link,published_at) VALUES (?,?,?,?)',
                      (site,pid,PS.BOORU[site]+f'/posts/{pid}',hint.isoformat() if hint else None))
            c.execute('INSERT OR IGNORE INTO booru_publication_items VALUES (?,?,?,?)',(int(pub['id']),site,pid,position))
    return [key for key,_,_ in refs]


def _count(value, *, negative=False):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):
        return None
    if int(value) != value or (value < 0 and not negative):
        return None
    return int(value)


def observe(link, values):
    """Use the HTTP observation time, including cached responses, not the time of viewing."""
    key=identity(link)
    if not key:
        return False
    ensure_schema()
    site,pid=key
    if not db.query('SELECT 1 FROM booru_items WHERE site=? AND post_id=?',key):
        return False
    if not values or values.get('error'):
        with db.tx() as c:
            c.execute('UPDATE booru_items SET last_checked=?,last_error=? WHERE site=? AND post_id=?',
                      (now_utc().isoformat(),(values or {}).get('error') or 'Нет ответа',site,pid))
        return False
    observed=dt(values.get('observed_at'))
    published=dt(values.get('created_at'))
    owner=values.get('owner')
    rating=values.get('rating')
    kind=values.get('kind')
    favs=_count(values.get('favs'))
    if not observed or not published or observed < published or not owner or rating not in ('s','q','e') or kind not in ('image','gif','video') or favs is None or values.get('deleted'):
        with db.tx() as c:
            c.execute('UPDATE booru_items SET last_checked=?,last_error=? WHERE site=? AND post_id=?',
                      ((observed or now_utc()).isoformat(),'На площадке нет полных данных для сравнения',site,pid))
        return False
    age=(observed-published).total_seconds()/3600
    with db.tx() as c:
        c.execute('UPDATE booru_items SET published_at=?,owner=?,rating=?,kind=?,verified=1,last_checked=?,last_error=NULL WHERE site=? AND post_id=?',
                  (published.isoformat(),str(owner),rating,kind,observed.isoformat(),site,pid))
        for checkpoint,grace in CHECKPOINTS.items():
            if checkpoint <= age <= checkpoint+grace:
                c.execute('INSERT OR IGNORE INTO booru_checkpoints VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                          (site,pid,checkpoint,observed.isoformat(),age,str(owner),rating,kind,favs,
                           _count(values.get('score'),negative=True),_count(values.get('comments'))))
    return True


def _comparison(item,sample):
    # One typical result per earlier publication: albums must not inflate the sample size.
    earliest=(dt(item['published_at'])-timedelta(days=BASELINE_DAYS)).isoformat()
    rows=db.query('''SELECT s.post_id,s.favs,MIN(a.pub_id) AS pub_id
        FROM booru_checkpoints s JOIN booru_items b USING(site,post_id)
        JOIN booru_publication_items a USING(site,post_id)
        WHERE s.site=? AND s.owner=? AND s.rating=? AND s.kind=? AND s.checkpoint=?
          AND b.verified=1 AND b.published_at>=? AND b.published_at<? AND s.taken_at<=?
          AND NOT EXISTS (SELECT 1 FROM booru_publication_items own
              JOIN booru_publication_items target ON own.pub_id=target.pub_id
              WHERE own.site=s.site AND own.post_id=s.post_id AND target.site=? AND target.post_id=?)
        GROUP BY s.site,s.post_id ORDER BY b.published_at DESC,s.post_id DESC LIMIT 1000''',
        (item['site'],sample['owner'],sample['rating'],sample['kind'],sample['checkpoint'],earliest,
         item['published_at'],sample['taken_at'],item['site'],item['post_id']))
    grouped={}
    for row in rows:
        grouped.setdefault(row['pub_id'],[]).append(row['favs'])
    # Keep the most recent 100 independent publications; several copies of one remote post count once.
    values=np.array([np.median(v) for v in list(grouped.values())[:100]],dtype=float)
    result={'n':len(values),'minimum':MIN_BASELINE,'window_days':BASELINE_DAYS,'ready':len(values)>=MIN_BASELINE,
            'method':'Та же площадка, автор, рейтинг, тип файла и возраст; для альбомов берётся медиана, публикации имеют одинаковый вес'}
    if not result['ready']:
        return result
    median=float(np.median(values))
    # Bootstrap uncertainty of the typical result, not a prediction interval for the new artwork.
    draws=np.random.default_rng(0).choice(values,size=(500,len(values)),replace=True)
    medians=np.median(draws,axis=1)
    low,high=np.percentile(medians,[2.5,97.5])
    q1,q3=np.percentile(values,[25,75])
    result.update(median=round(median,2),median_low=round(float(low),2),median_high=round(float(high),2),
                  typical_low=round(float(q1),2),typical_high=round(float(q3),2),
                  index=round(sample['favs']/median,2) if median>0 else None,
                  lower=int((values<sample['favs']).sum()),equal=int((values==sample['favs']).sum()),
                  preliminary=len(values)<30)
    return result


def report(pub_id,site,now=None):
    ensure_schema()
    now=now or now_utc()
    items=db.query('''SELECT b.* FROM booru_items b JOIN booru_publication_items a USING(site,post_id)
                      WHERE a.pub_id=? AND a.site=? ORDER BY a.position,a.post_id''',(int(pub_id),site))
    output=[]
    for item in items:
        samples={r['checkpoint']:r for r in db.query('SELECT * FROM booru_checkpoints WHERE site=? AND post_id=?',(site,item['post_id']))}
        published=dt(item['published_at'])
        age=(now-published).total_seconds()/3600 if published else None
        points=[]
        for checkpoint,grace in CHECKPOINTS.items():
            if checkpoint in samples:
                sample=samples[checkpoint]
                points.append(dict(state='measured',hours=checkpoint,favs=sample['favs'],score=sample['score'],
                                   comments=sample['comments'],taken_at=sample['taken_at'],age_h=round(sample['age_h'],2),
                                   comparison=_comparison(item,sample)))
            else:
                state='unknown' if not item['verified'] else 'waiting' if age is not None and age<checkpoint else 'missed' if age is not None and age>checkpoint+grace else 'due'
                points.append(dict(hours=checkpoint,state=state))
        output.append(dict(post_id=item['post_id'],link=item['link'],points=points,error=item['last_error'],verified=bool(item['verified'])))
    return {'items':output,'minimum':MIN_BASELINE}


def tick(now=None,limit=8,min_gap=60):
    """Bounded background collection, independent of Telegram authorization."""
    global _last_tick
    now=now or now_utc()
    if not _lock.acquire(blocking=False):
        return {'skipped':'busy'}
    try:
        if (now.timestamp()-_last_tick)<min_gap:
            return {'skipped':'recent'}
        _last_tick=now.timestamp()
        cfg=PS.load()
        active={site for site in booru.PLATFORMS if cfg['modules'].get(site,True)}
        if not cfg['modules'].get('collect',True) or not active:
            return {'skipped':'disabled'}
        from app.insights import post_stats
        ensure_schema()
        for pub in post_stats._pubs(days=9):
            for dest in booru.destinations(pub['dests']):
                register(pub,dest)
        todo=[]
        for item in db.query('SELECT * FROM booru_items ORDER BY published_at,site,post_id'):
            if item['site'] not in active:
                continue
            published=dt(item['published_at'])
            if not published:
                continue
            age=(now-published).total_seconds()/3600
            last=dt(item['last_checked'])
            if last and (now-last).total_seconds()<300:
                continue
            if item['verified'] and age>180:
                continue
            samples={r['checkpoint'] for r in db.query('SELECT checkpoint FROM booru_checkpoints WHERE site=? AND post_id=?',(item['site'],item['post_id']))}
            due=[h for h,grace in CHECKPOINTS.items() if h not in samples and h<=age<=h+grace]
            if due:
                todo.append((min(h+CHECKPOINTS[h]-age for h in due),item))
            elif not item['verified'] and last is None and 0<=age<=216:
                todo.append((48,item))
        checked=updated=0
        for _,item in sorted(todo,key=lambda pair:pair[0])[:limit]:
            # Recheck immediately before HTTP, including a user disabling collection mid-run.
            if not PS.module_on('collect') or not PS.module_on(item['site']):
                continue
            checked+=1
            values=post_stats.e6ai_stats(item['link'],force=True)
            updated+=int(observe(item['link'],values))
        return {'checked':checked,'updated':updated}
    finally:
        _lock.release()
