"""Offline timings on temporary data: python tests/benchmark_views.py 2000 --profile.

Run with the project venv. No accounts, real data or publishers are used.
Compare separate checkouts on the same machine; these are backend timings,
not Windows WebView rendering times. --profile includes profiler overhead.
"""
import argparse
import cProfile
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import pstats
import sys
import tempfile
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('size', type=int, nargs='?', default=2000)
    parser.add_argument('--profile', action='store_true')
    args = parser.parse_args()
    if not 1 <= args.size <= 100000:
        parser.error('size must be between 1 and 100000')
    with tempfile.TemporaryDirectory(prefix='crosspost-benchmark-') as folder, \
            patch.dict(os.environ, CROSSPOST_DATA=folder, CROSSPOST_NO_SCHEDULER='1'):
        from app import db, stats
        from app.publish import runner, schedule, settings  # noqa: F401 (schema)
        from app.insights import calendar, warmup
        try:
            settings.update('modules', {'collect':False, 'news':False})
            with db.tx() as c:
                c.execute("INSERT INTO channels(platform,chat_id,title,selected) VALUES ('telegram','1','Test channel',1)")
                for i in range(args.size):
                    date = (datetime(2024,1,1,tzinfo=timezone.utc) + timedelta(hours=i*12)).isoformat()
                    dest = {'key':'telegram:1', 'platform':'telegram', 'name':'Test channel',
                            'state':'done', 'chat_id':'1', 'message_ids':[i+1]}
                    c.execute('INSERT INTO publications(created,title,text,files,dests,draft) VALUES (?,?,?,?,?,?)',
                              (date, f'Art {i}', 'Body', '[]', json.dumps([dest]), '{}'))
                    pid = c.execute("""INSERT INTO posts(platform,chat_id,msg_id,date_utc,text,media_type,
                                       n_media,views,reactions,updated_at) VALUES ('telegram','1',?,?,?,'photo',1,?,?,?)""",
                                    (i+1,date,f'Art {i}',100+i%50,i%10,date)).lastrowid
                    c.execute("INSERT INTO messages(platform,chat_id,msg_id,post_id) VALUES ('telegram','1',?,?)", (i+1,pid))
            calls = [('history', runner.search_history),
                     ('calendar', lambda: calendar.month('2026-11')),
                     ('summary', warmup.summary_compute),
                     ('stats', lambda: stats.overview('all'))]
            with patch('app.insights.ext_posts.items_between', return_value=[]):
                for name, compute in calls:
                    profile = cProfile.Profile()
                    start = time.perf_counter()
                    if args.profile:
                        profile.enable()
                    compute()
                    profile.disable()
                    print(f'{name}: {time.perf_counter()-start:.3f} s', flush=True)
                    if args.profile:
                        pstats.Stats(profile).sort_stats('cumtime').print_stats(12)
        finally:
            db.reset_for_tests()


if __name__ == '__main__':
    main()
