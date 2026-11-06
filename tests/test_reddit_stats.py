import json
import unittest
from unittest.mock import patch

from app.insights import post_stats


class RedditStatsLinks(unittest.TestCase):
    def test_post_ids_are_extracted_only_from_valid_reddit_post_urls(self):
        url = post_stats.reddit_stats_url
        self.assertEqual(url('https://www.reddit.com/r/aiyiff/comments/1wdvx17/title/?context=3'),
                         'https://www.reddit.com/poststats/1wdvx17/')
        self.assertEqual(url('https://reddit.com/r/FurAI/comments/abc123/'),
                         'https://www.reddit.com/poststats/abc123/')
        for bad in ('https://reddit.com.evil.invalid/r/foo/comments/abc/',
                    'https://reddit.com/r/foo/submit/', 'http://reddit.com/r/foo/comments/abc/',
                    'https://reddit.com/r/foo/comments/abc/comment/def/', ''):
            self.assertEqual(url(bad), '')

    def test_each_confirmed_post_keeps_its_own_link_even_if_collection_is_off(self):
        dests = [
            {'key':'reddit:a', 'name':'A', 'platform':'reddit', 'state':'done', 'link':'https://www.reddit.com/r/alpha/comments/abc123/post/'},
            {'key':'reddit:b', 'name':'B', 'platform':'reddit', 'state':'done', 'link':'https://www.reddit.com/r/bravo/comments/xyz456/post/'},
            {'key':'reddit:c', 'name':'C', 'platform':'reddit', 'state':'error', 'link':''},
        ]
        row = {'id':1, 'title':'', 'created':'2026-01-01', 'rating':'sfw', 'files':'[]', 'dests':json.dumps(dests)}
        with patch.object(post_stats, '_pubs', return_value=[row]), patch.object(post_stats.db, 'query', return_value=[]), \
             patch.object(post_stats.M, 'growth_curve', return_value=[]), \
             patch('app.publish.settings.module_on', return_value=False):
            result = post_stats.for_publication(1)['dests']
        self.assertEqual(result[0]['stats_url'], 'https://www.reddit.com/poststats/abc123/')
        self.assertEqual(result[1]['stats_url'], 'https://www.reddit.com/poststats/xyz456/')
        self.assertNotIn('stats_url', result[2])
        self.assertIn('автоматический сбор пока не подключён', result[0]['why'])


if __name__ == '__main__':
    unittest.main()
