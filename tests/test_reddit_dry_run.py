import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from app.api import Api


class RedditDryRun(unittest.TestCase):
    def test_x_dry_run_uses_sample_and_requires_browser_login(self):
        from app.publish import settings
        with tempfile.TemporaryDirectory() as directory, patch.dict('os.environ', {'CROSSPOST_DATA': directory}):
            api = Api.__new__(Api)
            api.jobs = Mock()
            api.jobs.start.side_effect = lambda key, fn: fn(lambda **kw: None)
            with self.assertRaisesRegex(ValueError, 'войдите'):
                api.pub_browser_dry('x')
            settings.update('x', {'browser_ok': True})
            def check(files, **kwargs):
                self.assertEqual(len(files), 1)
                self.assertTrue(Path(files[0]).is_file())
                return {'shot': 'x-test.png'}
            with patch('app.publish.social_web.x_dry', side_effect=check):
                api.pub_browser_dry('x')

    def test_empty_draft_uses_sample_without_sending(self):
        from app.publish import settings
        with tempfile.TemporaryDirectory() as directory, patch.dict('os.environ', {'CROSSPOST_DATA': directory}):
            settings.update('reddit', {'browser_ok': True})
            api = Api.__new__(Api)
            api.jobs = Mock()
            api.jobs.start.side_effect = lambda key, fn: fn(lambda **kw: None)
            def check(platform, files, title, text, **kwargs):
                self.assertEqual(platform, 'reddit')
                self.assertTrue(Path(files[0]).is_file())
                self.assertEqual(title, 'Crosspost test')
                self.assertEqual(kwargs['flair'], 'SFW')
                self.assertFalse(kwargs['nsfw'])
                self.assertTrue(kwargs['dry_run'])
                return {'shot': 'test.png'}
            with patch('app.publish.social_web.post', side_effect=check):
                api.pub_browser_dry('reddit', {}, 'FurAI')

    def test_joyreactor_uses_sample_without_draft_or_configured_tags(self):
        from app.publish import settings, browser_targets
        with tempfile.TemporaryDirectory() as directory, patch.dict('os.environ', {'CROSSPOST_DATA': directory}):
            settings.update('joyreactor', {'browser_ok': True})
            api = Api.__new__(Api)
            api.jobs = Mock()
            api.jobs.start.side_effect = lambda key, fn: fn(lambda **kw: None)
            def check(platform, files, title, text, **kwargs):
                self.assertEqual(platform, 'joyreactor')
                self.assertTrue(Path(files[0]).is_file())
                self.assertEqual(kwargs['tags'], ['crosspost_test'])
                self.assertFalse(kwargs['nsfw'])
                self.assertTrue(kwargs['dry_run'])
                return {'shot': 'test.png'}
            with patch('app.publish.social_web.post', side_effect=check):
                api.pub_browser_dry('joyreactor')
            with self.assertRaisesRegex(Exception, 'теги ИИ'):
                browser_targets.joy_tags(settings.load(), {'rating': 'sfw'}, [])

    def test_reddit_rejects_unknown_community(self):
        from app.publish import settings
        with tempfile.TemporaryDirectory() as directory, patch.dict('os.environ', {'CROSSPOST_DATA': directory}):
            settings.update('reddit', {'browser_ok': True})
            api = Api.__new__(Api)
            with self.assertRaisesRegex(ValueError, 'сообщество'):
                api.pub_browser_dry('reddit', community='unknown')


if __name__ == '__main__':
    unittest.main()
