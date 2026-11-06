import importlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from app.publish import browser_targets as BT
from core.common import NotPublishedError, PublicationUnknown, PlatformError


class BrowserTargets(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, CROSSPOST_DATA=self.tmp.name)
        self.env.start()
        from app import db
        from app.publish import runner, settings
        self.db, self.settings = db, settings
        db.reset_for_tests()
        self.runner = importlib.reload(runner)
        self.uid = '0123456789abcdef'
        path = Path(self.tmp.name) / 'uploads' / self.uid / 'image.png'
        path.parent.mkdir(parents=True)
        Image.new('RGB', (80, 80), 'blue').save(path)
        self.draft = dict(files=[self.uid], title='Art', text='Description', rating='sfw',
                          signature=False, hashtags=[], e6ai_tags={self.uid: ['male/male', 'male']})

    def tearDown(self):
        self.db.reset_for_tests()
        self.env.stop()
        self.tmp.cleanup()

    def reddit(self):
        self.settings.update('reddit', {'enabled': True, 'browser_ok': True})

    def test_defaults_do_not_add_publication_destinations(self):
        self.assertFalse(self.settings.load()['reddit']['enabled'])
        self.assertFalse(self.settings.load()['joyreactor']['enabled'])
        self.assertFalse(any(d['platform'] in ('reddit', 'joyreactor') for d in self.runner.plan(self.draft)['dests']))

    def test_separate_subreddit_destinations_flairs_and_rating(self):
        self.reddit()
        dests = self.runner.plan(self.draft)['dests']
        self.assertEqual([d['key'] for d in dests], ['reddit:FurAI', 'reddit:aiyiff'])
        self.assertEqual([d['flair'] for d in dests], ['SFW', 'Gay'])
        self.assertTrue(all(d['will'] for d in dests))
        self.draft.update(rating='explicit', exclude=['reddit:FurAI'])
        dests = self.runner.plan(self.draft)['dests']
        self.assertFalse(dests[0]['will'])
        self.assertEqual(dests[0]['flair'], 'NSFW')
        self.assertTrue(dests[1]['will'])

    def test_disabled_module_overrides_saved_connection(self):
        self.reddit()
        self.settings.update('modules', {'reddit': False})
        self.assertFalse(any(d['platform'] == 'reddit' for d in self.runner.plan(self.draft)['dests']))

    def test_manual_flair_survives_tag_changes_named_drafts_and_templates(self):
        from app.publish import drafts, templates
        self.reddit()
        self.draft['reddit_flairs'] = {'aiyiff': 'Other'}
        self.draft['e6ai_tags'][self.uid] = ['female/female']
        saved = drafts.save('Reddit', self.draft)
        loaded = drafts.get(saved['id'])['draft']
        self.assertEqual(loaded['reddit_flairs'], {'aiyiff': 'Other'})
        self.assertEqual(BT.reddit_flair(self.settings.load(), loaded, 'aiyiff', [self.uid]), 'Other')
        item = templates.save('Reddit', self.draft)
        self.assertEqual(templates.get(item['id'])['draft']['reddit_flairs'], {'aiyiff': 'Other'})

    def test_edited_empty_tags_override_predictions_and_conflicts_need_manual_choice(self):
        cfg = self.settings.load()
        self.draft['e6ai_tags'][self.uid] = []
        with patch('app.publish.hashtags._preds', return_value=[['male/male', .99]]):
            self.assertEqual(BT.reddit_flair(cfg, self.draft, 'aiyiff', [self.uid]), '')
        cfg['reddit']['flair_rules'] = [dict(all=['male'], flair='Male', priority=1), dict(all=['male'], flair='Gay', priority=1)]
        self.draft['e6ai_tags'][self.uid] = ['male']
        with self.assertRaises(NotPublishedError):
            BT.reddit_flair(cfg, self.draft, 'aiyiff', [self.uid])

    def test_predictions_only_use_selected_files_and_confidence(self):
        self.draft['e6ai_tags'] = {}
        with patch('app.publish.hashtags._preds', side_effect=lambda uid: [['female', .9], ['male', .1]] if uid == self.uid else [['male/male', .99]]):
            self.assertEqual(BT.reddit_flair(self.settings.load(), self.draft, 'aiyiff', [self.uid]), 'Female')

    def test_joy_tags_are_explicit_and_preserve_spaces(self):
        cfg = self.settings.load()
        with self.assertRaises(NotPublishedError):
            BT.joy_tags(cfg, self.draft, [self.uid])
        cfg['joyreactor'].update(ai_tags=['AI Art'], constant_tags=['furry'], tag_aliases={'male': 'мужской персонаж'}, rating_tags={'sfw': ['без обнажения']})
        self.assertEqual(BT.joy_tags(cfg, self.draft, [self.uid]), ['AI Art', 'furry', 'без обнажения', 'мужской персонаж'])
        self.assertNotIn('male/male', BT.joy_tags(cfg, self.draft, [self.uid]))

    def test_settings_cannot_forge_login_or_inject_unlisted_communities(self):
        for changes in ({'browser_ok': True}, {'base': 'https://example.invalid'}, {'communities': {'unexpected': True}}, {'flair_rules': [{'all': ['male'], 'flair': 'invented'}]}):
            with self.assertRaises(ValueError):
                BT.validate_changes('reddit', changes)
        with self.assertRaises(ValueError):
            BT.validate_changes('joyreactor', {'ai_tags': ['one,two']})

    def test_new_text_variants_are_retained_and_previewed(self):
        self.reddit()
        self.settings.update('platform_texts_enabled', {'reddit': True})
        self.draft['platform_texts'] = {'reddit': {'enabled': True, 'title': 'Reddit title', 'text': 'Reddit body'}}
        plan = self.runner.plan(self.draft)
        self.assertEqual(plan['titles']['reddit'], 'Reddit title')
        self.assertEqual(plan['texts']['reddit'], 'Reddit body')

    def test_unknown_browser_result_blocks_retry_of_only_that_subreddit(self):
        self.reddit()
        calls = []
        def send(platform, files, title, text, **kwargs):
            calls.append(kwargs['community'])
            if kwargs['community'] == 'aiyiff':
                raise PublicationUnknown('Lost confirmation')
            return {'link': 'https://www.reddit.com/r/FurAI/comments/abc/art/'}
        with patch('app.publish.social_web.post', side_effect=send), patch('app.insights.forecast.for_draft', return_value={'items': []}):
            self.runner.start(self.draft, lambda **kw: None)
        state = self.runner.state()
        by_key = {d['key']: d for d in state['dests']}
        self.assertEqual(by_key['reddit:FurAI']['state'], 'done')
        self.assertEqual(by_key['reddit:aiyiff']['outcome'], 'unknown')
        with patch('app.publish.social_web.post') as again:
            with self.assertRaises(PlatformError):
                self.runner.retry(lambda **kw: None)
            again.assert_not_called()
        self.assertCountEqual(calls, ['FurAI', 'aiyiff'])


if __name__ == '__main__':
    unittest.main()
