"""e621ng contract tests. No live requests or real API keys."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from app.publish import e6ai, settings
from app.insights import post_stats


class Floofgens(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = patch.dict(os.environ, CROSSPOST_DATA=self.tmp.name)
        self.env.start()
        self.addCleanup(self.env.stop)
        settings.update('e6ai', {'site': 'floofgens', 'username': 'blep-user', 'blep_api_key': 'blep-key',
                                'accounts': {'floofgens': {'username': 'floof-user', 'api_key': 'floof-key'},
                                             'e621': {'username': 'e621-user', 'api_key': 'e621-key'}}})
        post_stats._e6_cache.clear()
        self.addCleanup(post_stats._e6_cache.clear)

    def test_check_is_read_only_and_uses_floofgens_credentials(self):
        response = Mock(status_code=200, json=lambda: {'name': 'floof-user', 'id': 7})
        with patch('requests.get', return_value=response) as get, patch('requests.post') as post:
            self.assertEqual(e6ai.check(settings.load()['e6ai'])['id'], 7)
        post.assert_not_called()
        self.assertEqual(get.call_args_list[0].args[0], 'https://floofgens.net/favorites.json')
        self.assertEqual(get.call_args_list[0].kwargs['auth'], ('floof-user', 'floof-key'))
        self.assertFalse(get.call_args_list[0].kwargs['allow_redirects'])
        self.assertIn('floofgens.net', get.call_args_list[0].kwargs['headers']['User-Agent'])

    def test_upload_original_ratings_sources_and_parent(self):
        path = Path(self.tmp.name) / 'original.png'
        path.write_bytes(b'original-png')
        def send(url, **kwargs):
            self.assertEqual(url, 'https://floofgens.net/uploads.json')
            self.assertEqual(kwargs['auth'], ('floof-user', 'floof-key'))
            self.assertFalse(kwargs['idempotent'])
            self.assertFalse(kwargs['allow_redirects'])
            self.assertEqual(kwargs['files']['upload[file]'][1].read(), b'original-png')
            self.assertEqual(kwargs['data']['upload[parent_id]'], '8')
            self.assertEqual(kwargs['data']['upload[tag_string]'], 'artist:test blue_eyes')
            self.assertEqual(kwargs['data']['upload[source]'], 'https://example.org/art')
            self.assertEqual(kwargs['data']['upload[description]'], 'Description')
            return Mock(status_code=200, json=lambda: {'success': True, 'post_id': 42})
        for rating, expected in [('sfw', 's'), ('erotic', 'q'), ('explicit', 'e')]:
            with self.subTest(rating=rating), patch('core.retry.post', side_effect=send) as post:
                result = e6ai.upload(settings.load()['e6ai'], path, ['artist:test', 'blue eyes', 'blue eyes', 'rating:s'],
                                     rating, ['https://example.org/art'], 'Description', 8)
                self.assertEqual(post.call_args.kwargs['data']['upload[rating]'], expected)
                self.assertEqual(result, {'post_id': 42, 'url': 'https://floofgens.net/posts/42', 'duplicate': False})
                post.assert_called_once()

    def test_e621_uses_its_own_account_and_upload_endpoint(self):
        settings.update('e6ai', {'site': 'e621'})
        cfg = settings.load()['e6ai']
        self.assertEqual(e6ai.name(cfg), 'e621')
        self.assertEqual(e6ai.history_site(cfg), 'e621')
        path = Path(self.tmp.name) / 'drawing.png'
        path.write_bytes(b'original-drawing')
        with patch('core.retry.post', return_value=Mock(status_code=200, json=lambda: {'success': True, 'post_id': 17})) as post:
            result = e6ai.upload(cfg, path, ['artist:example', 'drawing'], 'sfw')
        self.assertEqual(post.call_args.args[0], 'https://e621.net/uploads.json')
        self.assertEqual(post.call_args.kwargs['auth'], ('e621-user', 'e621-key'))
        self.assertEqual(result['url'], 'https://e621.net/posts/17')
        self.assertNotIn('ai_generated', post.call_args.kwargs['data']['upload[tag_string]'])

    def test_removing_platform_keeps_account_and_blocks_publication(self):
        from app.api import Api
        api = Api.__new__(Api)
        settings.update('e6ai', {'enabled': True})
        api.modules_set('e6ai', False)
        self.assertFalse(settings.load()['e6ai']['enabled'])
        self.assertEqual(settings.load()['e6ai']['api_key'], 'floof-key')
        api.modules_set('e6ai', True)
        self.assertTrue(settings.load()['e6ai']['enabled'])
        self.assertEqual(settings.load()['e6ai']['api_key'], 'floof-key')

    def test_duplicate_and_auth_failure(self):
        from core.common import PlatformError
        path = Path(self.tmp.name) / 'image.png'
        path.write_bytes(b'image')
        with patch('core.retry.post', return_value=Mock(status_code=412, json=lambda: {'reason': 'duplicate', 'post_id': 42})):
            self.assertTrue(e6ai.upload(settings.load()['e6ai'], path, [], 'sfw')['duplicate'])
        with patch('core.retry.post', return_value=Mock(status_code=401, json=lambda: {})):
            with self.assertRaisesRegex(PlatformError, 'floofgens.net не принял'):
                e6ai.upload(settings.load()['e6ai'], path, [], 'sfw')

    def test_statistics_and_moderation_follow_original_site(self):
        from app import db
        db.reset_for_tests()
        self.addCleanup(db.reset_for_tests)
        from app.insights import e6learn
        response = Mock(status_code=200, json=lambda: {'post': {'score': {'total': 12}, 'tags': {'general': ['fluffy']}}})
        settings.update('e6ai', {'site': 'e621'})
        with patch('requests.get', return_value=response) as get:
            for host, account in [('floofgens.net', 'floof'), ('e621.net', 'e621'), ('blep.art', 'blep')]:
                stats = post_stats.e6ai_stats(f'https://{host}/posts/42')
                self.assertEqual(stats['score'], 12)
                self.assertEqual(get.call_args.args[0], f'https://{host}/posts/42.json')
                self.assertEqual(get.call_args.kwargs['auth'], (account + '-user', account + '-key'))
            self.assertEqual(get.call_count, 3)
            self.assertEqual(e6learn._fetch(42, 'floofgens'), {'general': ['fluffy']})
            self.assertEqual(get.call_args.kwargs['auth'], ('floof-user', 'floof-key'))
            self.assertIsNone(e6learn._fetch(42, 'unknown'))
            self.assertEqual(get.call_count, 4)
        self.assertEqual(e6ai.history_site({'site_name': 'floofgens'}), 'floofgens')

    def test_unknown_urls_never_receive_credentials(self):
        with patch('requests.get') as get:
            for url in ['https://e6ai.net/posts/42', 'https://blep.art.evil.test/posts/42',
                        'https://floofgens.net@evil.test/posts/42', 'https://[bad',
                        'http://floofgens.net/posts/42', 'https://floofgens.net:8443/posts/42']:
                self.assertIsNone(post_stats.e6ai_stats(url))
            get.assert_not_called()

    def test_tag_exports_use_selected_site(self):
        from app import e6tags
        self.assertNotEqual(e6tags._prefix('floofgens'), e6tags._prefix('e621'))
        self.assertNotEqual(e6tags._prefix('floofgens'), e6tags._prefix('e6ai'))
        kinds = ('tags', 'tag_aliases', 'tag_implications')
        response = Mock(ok=True, json=lambda: [{'name': k, 'url': f'https://floofgens.net/db_export/{k}.csv.gz'} for k in kinds])
        with patch('requests.get', return_value=response) as get:
            result = e6tags.find_exports('Crosspost/test', 'floofgens')
            self.assertEqual(get.call_args.args[0], 'https://floofgens.net/db_exports.json')
            self.assertTrue(all(result[k].startswith('https://floofgens.net/') for k in kinds))


if __name__ == '__main__':
    unittest.main()
