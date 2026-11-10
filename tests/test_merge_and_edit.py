import importlib
import json
import os
import tempfile
import unittest
from unittest.mock import patch


class MergeAndEdit(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = patch.dict(os.environ, CROSSPOST_DATA=self.tmp.name)
        self.env.start()
        self.addCleanup(self.env.stop)
        from app import db
        db.reset_for_tests()
        self.addCleanup(db.reset_for_tests)
        from app.publish import runner
        self.runner = importlib.reload(runner)
        from app.publish import schedule
        importlib.reload(schedule)
        from app.insights import calendar
        self.calendar = importlib.reload(calendar)
        from app.insights import ext_posts
        importlib.reload(ext_posts)
        self.db = db

    def pub(self, date, title, dests=None):
        with self.db.tx() as c:
            return c.execute("INSERT INTO publications(created,title,text,rating,files,dests,draft) VALUES (?,?,?,?,?,?,?)",
                             (date, title, 'Body', 'sfw', '[]', json.dumps(dests or []), '{}')).lastrowid

    def test_arbitrary_dates_and_platforms_merge_in_calendar_list_and_stats(self):
        from app.insights import post_stats
        a = self.pub('2025-01-03T12:00:00+00:00', 'First', [{'key':'discord:1','platform':'discord','state':'done','name':'DS'}])
        b = self.pub('2026-09-11T12:00:00+00:00', 'Second', [{'key':'vk:2','platform':'vk','state':'done','name':'VK'}])
        self.calendar.merge([f'u{a}', f'u{b}'])
        with patch('app.insights.ext_posts.items_between', return_value=[]):
            january = self.calendar.month('2025-01')
            september = self.calendar.month('2026-09')
            self.assertEqual(january['summary']['posts'], 0)
            self.assertEqual(september['summary']['posts'], 1)
            row = self.runner.search_history()['items'][0]
            self.assertEqual(row['merge_ids'], [a,b])
            self.assertEqual(len(row['dests']), 2)
            self.assertEqual(self.runner.search_history(q='Second')['total'], 1)
        with patch('app.publish.vk_stats.nums_for', return_value={}):
            stats = post_stats.for_publication(a)
        self.assertEqual({d['source_pub_id'] for d in stats['dests']}, {a,b})
        self.calendar.split([f'u{a}', f'u{b}'])
        with self.assertRaisesRegex(ValueError, 'принудительное'):
            self.calendar.merge([f'u{a}', f'u{b}'])
        self.calendar.merge([f'u{a}', f'u{b}'], force=True)
        self.assertEqual(self.calendar.publication_groups()[a], [a,b])

    def test_merged_statistics_use_newer_post_instead_of_older_larger_values(self):
        from app.insights import post_stats
        old=self.pub('2026-01-01T12:00:00+00:00','Old')
        new=self.pub('2026-02-01T12:00:00+00:00','New')
        self.calendar.merge([f'u{old}',f'u{new}'])
        def details(pid):
            return {"id":pid,"dests":[{"key":"telegram:1","views":1000 if pid==old else 10}]}
        with patch.object(post_stats,'_for_single_publication',side_effect=details):
            result=post_stats.for_publication(old)
        self.assertEqual(result["id"],new)
        self.assertEqual(len(result["dests"]),1)
        self.assertEqual(result["dests"][0]["views"],10)
        self.assertEqual(result["dests"][0]["source_pub_id"],new)

    def test_later_mirror_of_same_publication_does_not_hide_available_index(self):
        def atom(key, ts, index):
            return dict(key=key,ts=ts,index=index,pub_id=1,post_id=None,day='2026-01-01',time='12:00',
                        preview=None,text='Post',link='',channels=[key],app=True,parts=[],views=10,reactions=1,forwards=0)
        work=self.calendar._collapse([atom('telegram',1,.9),atom('bsky',2,None)])
        self.assertEqual(work['index'],.9)
        self.assertFalse(work['manual'])

    def test_imported_post_and_crosspost_merge_in_list_with_combined_metrics(self):
        pid=self.pub('2026-01-01T12:00:00+00:00','Own post')
        with self.db.tx() as c:
            c.execute("INSERT INTO ext_posts(key,platform,date,text,link,kind,views,likes,reposts,replies) VALUES (?,?,?,?,?,?,?,?,?,?)",
                      ('vk:external','vk',1769000000,'External','https://vk.com/wall-1_2','img',120,5,1,0))
        self.calendar.merge([f'u{pid}','vk:external'], force=True)
        history=self.runner.search_history()
        self.assertEqual(history['total'],1)
        self.assertEqual(self.runner.search_history(q='External')['total'],1)
        self.assertEqual(self.runner.search_history(platform='vk')['total'],1)
        self.assertEqual(self.runner.search_history(q='Missing')['total'],0)
        self.assertEqual(self.runner.search_history(platform='discord')['total'],0)
        work=history['items'][0]['work']
        self.assertEqual(set(work['atoms']),{f'u{pid}','vk:external'})
        self.assertEqual(work['views'],120)
        self.assertEqual(len(work['parts']),2)
        self.calendar.split(work['atoms'])
        self.assertEqual(self.runner.search_history()['total'],2)

    def tg(self, **extra):
        dest = dict(key='telegram:1', platform='telegram', name='Channel', state='done',
                    chat_id='-100123', message_ids=[42,43], edit_message_id=42,
                    edit_kind='caption', edit_text='<b>Original</b>', edit_bot_id='123')
        dest.update(extra)
        return self.pub('2026-11-01T12:00:00+00:00','Title',[dest])

    def test_bot_edit_uses_recorded_message_and_preserves_originals(self):
        from app.publish import settings, edit_post
        settings.update('telegram', {'bot_token':'123:secret', 'transport':'botapi'})
        pid=self.tg()
        before=edit_post.prepare(pid,'telegram:1')
        with patch('app.publish.tg_bot.call', return_value=True) as call:
            saved=edit_post.apply(pid,'telegram:1','New caption',before['revision'])
        self.assertEqual(call.call_args.args[1], 'editMessageCaption')
        self.assertEqual(call.call_args.args[2]['message_id'],42)
        self.assertEqual(saved['text'],'New caption')
        self.assertEqual(json.loads(self.db.query('SELECT dests FROM publications WHERE id=?',(pid,))[0]['dests'])[0]['message_ids'],[42,43])
        with patch('app.publish.tg_bot.call') as call:
            with self.assertRaisesRegex(ValueError,'изменилась'):
                edit_post.apply(pid,'telegram:1','Old editor',before['revision'])
            call.assert_not_called()

    def test_failed_edit_does_not_change_history_or_send_with_other_bot(self):
        from app.publish import settings, edit_post
        settings.update('telegram', {'bot_token':'123:secret', 'transport':'botapi'})
        pid=self.tg(edit_kind='text', edit_message_id=41)
        before=edit_post.prepare(pid,'telegram:1')
        with patch('app.publish.tg_bot.call', side_effect=RuntimeError('Denied')) as call:
            with self.assertRaises(RuntimeError):
                edit_post.apply(pid,'telegram:1','New',before['revision'])
            self.assertEqual(call.call_args.args[1], 'editMessageText')
            self.assertEqual(call.call_args.args[2]['message_id'],41)
        self.assertEqual(edit_post.prepare(pid,'telegram:1')['text'],before['text'])
        settings.update('telegram', {'bot_token':'999:other'})
        with patch('app.publish.tg_bot.call') as call:
            with self.assertRaisesRegex(ValueError,'другим ботом'):
                edit_post.apply(pid,'telegram:1','New',before['revision'])
            call.assert_not_called()

    def test_new_long_bot_post_keeps_separate_text_message_for_editing(self):
        from app.publish import tg_bot
        text_message={"chat":{"id":-100123},"message_id":40}
        photo={"chat":{"id":-100123},"message_id":41}
        with patch.object(tg_bot,"call",return_value=text_message), patch.object(tg_bot.T,"_send_photos",return_value=[photo]):
            result=tg_bot.send_to_channel('123:secret',{},{"id":"-100123"},
                                         {"kind":"images","photos":[],"originals":[]},
                                         'a'*1100,'',False,lambda message:None)
        self.assertEqual(result["edit_message_id"],40)
        self.assertEqual(result["edit_kind"],"text")
        self.assertEqual(result["edit_text"],'a'*1100)
        self.assertEqual(result["message_ids"],[41])

    def test_mtproto_edit_and_caption_limit(self):
        from app.publish import settings, edit_post
        settings.update('telegram', {'bot_token':'123:secret', 'transport':'mtproto'})
        pid=self.tg()
        prepared=edit_post.prepare(pid,'telegram:1')
        with patch('app.publish.tg_bot._mt') as mt:
            with self.assertRaisesRegex(ValueError,'1024'):
                edit_post.apply(pid,'telegram:1','a'*1025,prepared['revision'])
            mt.assert_not_called()
            edit_post.apply(pid,'telegram:1','New',prepared['revision'])
            mt.return_value.edit_text.assert_called_once_with('-100123',42,'New')


if __name__ == '__main__':
    unittest.main()
