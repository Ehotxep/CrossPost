"""Independent choices of public post-source platforms for e621ng sites."""
import copy
import os
import tempfile
import unittest
from unittest.mock import patch

from app.publish import booru, settings as S, templates


class BooruSources(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = patch.dict(os.environ, CROSSPOST_DATA=self.tmp.name)
        env.start()
        self.addCleanup(env.stop)
        self.cfg = copy.deepcopy(S.DEFAULTS)

    def dest(self, platform, link, **values):
        return dict(key=platform, platform=platform.split(':')[0], state='done', link=link, **values)

    def test_existing_settings_keep_all_public_sources_and_off_switch(self):
        rows = [self.dest('vk','https://vk.com/wall-1_2'), self.dest('telegram:0','https://t.me/public/2')]
        for site in booru.PLATFORMS:
            self.assertEqual(booru.sources_for(self.cfg, site, rows), [r['link'] for r in rows])
            self.cfg[site]['with_sources'] = False
            self.assertEqual(booru.sources_for(self.cfg, site, rows), [])

    def test_sites_can_choose_different_platforms_and_no_sources(self):
        rows = [self.dest('vk','https://vk.com/wall-1_2'), self.dest('bluesky','https://bsky.app/profile/test/post/a')]
        self.cfg['e6ai']['source_platforms'] = {'vk':False}
        self.cfg['e621']['source_platforms'] = {'bluesky':False}
        self.cfg['floofgens']['source_platforms'] = {'vk':False,'bluesky':False}
        self.assertEqual(booru.sources_for(self.cfg,'e6ai',rows), [rows[1]['link']])
        self.assertEqual(booru.sources_for(self.cfg,'e621',rows), [rows[0]['link']])
        self.assertEqual(booru.sources_for(self.cfg,'floofgens',rows), [])

    def test_old_settings_keep_all_sources_and_master_switch_can_disable_them(self):
        rows = [{'key':'vk','state':'done','link':'https://vk.com/wall-1_2'},
                {'key':'reddit:FurAI','state':'done','link':'https://www.reddit.com/r/FurAI/comments/abc/test/'}]
        cfg = {'floofgens':{'with_sources':True}}
        self.assertEqual(booru.sources_for(cfg,'floofgens',rows), [r['link'] for r in rows])
        cfg['floofgens']['with_sources'] = False
        self.assertEqual(booru.sources_for(cfg,'floofgens',rows), [])

    def test_private_failed_self_and_invalid_links_are_not_sources(self):
        rows = [self.dest('telegram:0','https://t.me/c/123/4'),
                self.dest('telegram:1','https://t.me/public/4'),
                self.dest('vk','https://vk.com/wall-1_2'),
                self.dest('bluesky','https://user:private@example.org/post'),
                self.dest('reddit:furai','javascript:bad'),
                self.dest('joyreactor','https://[broken'),
                self.dest('floofgens','https://floofgens.net/posts/2'),
                self.dest('e621','https://e621.net/posts/2')]
        rows += [dict(self.dest('vk','https://vk.com/wall-1_3'),state='error'),
                 self.dest('telegram:2','https://t.me/public/4')]
        self.assertEqual(booru.sources_for(self.cfg,'floofgens',rows),
                         ['https://t.me/public/4','https://vk.com/wall-1_2','https://e621.net/posts/2'])
        # /c/ is private only for Telegram; it is a valid public path elsewhere.
        self.assertEqual(booru.sources_for(self.cfg,'floofgens',[self.dest('deviantart','https://www.deviantart.com/c/work')]),
                         ['https://www.deviantart.com/c/work'])

    def test_selected_sources_are_filtered_before_ten_link_limit(self):
        self.cfg['floofgens']['source_platforms'] = {'vk':False}
        rows = [self.dest('vk',f'https://vk.com/wall-1_{i}') for i in range(15)]
        rows += [self.dest('telegram:0',f'https://t.me/public/{i}') for i in range(15)]
        self.assertEqual(booru.sources_for(self.cfg,'floofgens',rows), [r['link'] for r in rows[15:25]])

    def test_sparse_autosave_patches_merge_without_affecting_other_sites(self):
        S.update('floofgens', {'source_platforms':{'vk':False}})
        S.update('floofgens', {'source_platforms':{'telegram':False}})
        cfg = S.load()
        self.assertEqual(cfg['floofgens']['source_platforms'], {'vk':False,'telegram':False})
        self.assertEqual(cfg['e621']['source_platforms'], {})
        self.assertEqual(cfg['e6ai']['source_platforms'], {})
        for value in ([], {'unknown':False}, {'vk':'false'}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                S.update('floofgens', {'source_platforms':value})

    def test_template_profiles_keep_independent_source_selections(self):
        draft = {'booru_posts':{}, 'profile':{'floofgens':{'source_platforms':{'vk':False}},
                                            'e621':{'source_platforms':{'telegram':False}}}}
        cfg = templates.effective(self.cfg, draft)
        self.assertFalse(cfg['floofgens']['source_platforms']['vk'])
        self.assertFalse(cfg['e621']['source_platforms']['telegram'])
        self.assertEqual(cfg['e6ai']['source_platforms'], {})

    def test_saved_default_selection_is_not_changed_by_later_global_edits(self):
        profile = templates.clean_profile(self.cfg)
        self.cfg['floofgens']['source_platforms'] = {'vk':False}
        result = templates.effective(self.cfg, {'booru_posts':{}, 'profile':profile})
        self.assertTrue(result['floofgens']['source_platforms']['vk'])
        self.assertFalse(self.cfg['floofgens']['source_platforms']['vk'])

    def test_resource_mode_maps_public_post_links_to_their_publishers(self):
        self.cfg['floofgens']['source_link_mode'] = 'resource'
        cases = [
            ('telegram:0','https://t.me/my_channel/123','https://t.me/my_channel'),
            ('telegram:0','https://t.me/s/my_channel/123?single','https://t.me/my_channel'),
            ('telegram:group','https://t.me/my_group/12/123','https://t.me/my_group'),
            ('vk','https://vk.com/wall-123_456','https://vk.com/club123'),
            ('vk','https://vk.com/wall123_456','https://vk.com/id123'),
            ('vk','https://vk.com/mygroup?w=wall-123_456','https://vk.com/mygroup'),
            ('bluesky','https://bsky.app/profile/name.test/post/abc?ref=x#top','https://bsky.app/profile/name.test'),
            ('bluesky','https://bsky.app/profile/did:plc:abc/post/def','https://bsky.app/profile/did:plc:abc'),
            ('deviantart','https://www.deviantart.com/artist/art/work-123','https://www.deviantart.com/artist'),
            ('deviantart','https://artist.deviantart.com/art/work-123','https://artist.deviantart.com/'),
            ('reddit:FurAI','https://www.reddit.com/r/FurAI/comments/abc/work/','https://www.reddit.com/r/FurAI/'),
            ('joyreactor','https://joyreactor.cc/post/123','https://joyreactor.cc/'),
            ('e621','https://e621.net/posts/123','https://e621.net/'),
            ('e6ai','https://blep.art/posts/123','https://blep.art/'),
        ]
        for platform, link, expected in cases:
            with self.subTest(platform=platform, link=link):
                self.assertEqual(booru.sources_for(self.cfg,'floofgens',[self.dest(platform,link)]),[expected])
        self.cfg['e621']['source_link_mode'] = 'resource'
        self.assertEqual(booru.sources_for(self.cfg,'e621',[self.dest('floofgens','https://floofgens.net/posts/123')]),['https://floofgens.net/'])

    def test_resource_mode_deduplicates_after_conversion_without_revealing_private_links(self):
        self.cfg['floofgens']['source_link_mode'] = 'resource'
        rows = [self.dest('telegram:0',f'https://t.me/public/{i}') for i in range(15)]
        rows += [self.dest('vk','https://vk.com/wall-1_2'),
                 self.dest('telegram:1','https://t.me/c/123/456'),
                 self.dest('telegram:2','https://t.me/+private-invite'),
                 self.dest('telegram:3','https://example.invalid/owner/123')]
        self.assertEqual(booru.sources_for(self.cfg,'floofgens',rows), ['https://t.me/public','https://vk.com/club1'])
        self.cfg['floofgens']['source_platforms'] = {'vk':False}
        self.assertEqual(booru.sources_for(self.cfg,'floofgens',rows), ['https://t.me/public'])
        self.cfg['floofgens']['with_sources'] = False
        self.assertEqual(booru.sources_for(self.cfg,'floofgens',rows), [])

    def test_link_mode_is_independent_validated_and_frozen_in_templates(self):
        S.update('e621', {'source_link_mode':'resource'})
        cfg = S.load()
        self.assertEqual(cfg['e621']['source_link_mode'],'resource')
        self.assertEqual(cfg['e6ai']['source_link_mode'],'post')
        self.assertEqual(cfg['floofgens']['source_link_mode'],'post')
        profile = templates.clean_profile(cfg)
        cfg['e621']['source_link_mode'] = 'post'
        effective = templates.effective(cfg,{'booru_posts':{},'profile':profile})
        self.assertEqual(effective['e621']['source_link_mode'],'resource')
        for mode in ('unknown',True,None):
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                S.update('floofgens', {'source_link_mode':mode})
