import os
import tempfile
import unittest
from unittest.mock import patch


class BlepMigration(unittest.TestCase):
    def test_post_statistics_do_not_mix_old_and_new_site_ids(self):
        from app.insights import post_stats
        from app.publish import settings
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, CROSSPOST_DATA=folder):
            settings.update('e6ai', {'username': 'artist', 'api_key': 'old-key', 'blep_api_key': 'new-key'})
            post_stats._e6_cache.clear()
            def response(url, **kwargs):
                score = 10 if 'blep.art' in url else 20
                return Mock(status_code=200, json=lambda: {'post': {'score': {'total': score}}})
            with patch('requests.get', side_effect=response) as get:
                old = post_stats.e6ai_stats('https://e6ai.net/posts/42')
                new = post_stats.e6ai_stats('https://blep.art/posts/42')
            self.assertIsNone(old)
            self.assertEqual(new['score'], 10)
            self.assertEqual(get.call_count, 1)
            self.assertEqual(get.call_args.kwargs['auth'], ('artist', 'new-key'))
            post_stats._e6_cache.clear()

    def test_old_key_never_used_for_blep_and_new_key_is_isolated(self):
        from app.publish import settings
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, CROSSPOST_DATA=folder):
            settings.update('e6ai', {'enabled': True, 'username': 'artist', 'api_key': 'old-secret', 'base': 'https://e6ai.net', 'constant_tags': ['artist_tag']})
            active = settings.load()['e6ai']
            self.assertEqual(active['base'], 'https://blep.art')
            self.assertEqual(active['api_key'], '')
            self.assertEqual(active['constant_tags'], ['artist_tag'])
            self.assertFalse(settings.public(settings.load())['e6ai']['has_key'])
            settings.update('e6ai', settings.site_changes({'api_key': 'blep-secret'}))
            self.assertEqual(settings.load()['e6ai']['api_key'], 'blep-secret')
            self.assertEqual(settings.load(raw=True)['e6ai']['api_key'], 'old-secret')
            public = settings.public(settings.load())['e6ai']
            self.assertTrue(public['has_key'])
            self.assertNotIn('blep-secret', str(public))
            settings.update('e6ai', {'site': 'e621'})
            self.assertEqual(settings.load()['e6ai']['api_key'], '')
            settings.update('e6ai', settings.site_changes({'api_key': 'e621-secret'}))
            self.assertEqual(settings.load()['e6ai']['api_key'], 'e621-secret')
            settings.update('e6ai', {'site': 'e6ai'})
            self.assertEqual(settings.load()['e6ai']['api_key'], 'blep-secret')


if __name__ == '__main__':
    unittest.main()
