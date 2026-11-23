import copy
import unittest
import tempfile
from unittest.mock import patch

from app.publish import settings


class BilingualPosts(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        env = patch.dict('os.environ', CROSSPOST_DATA=folder.name)
        env.start()
        self.addCleanup(env.stop)
        from app import db
        db.reset_for_tests()
        # unittest cleanups run in reverse order: close SQLite before restoring
        # CROSSPOST_DATA and removing its directory (required on Windows).
        self.addCleanup(db.reset_for_tests)
        global runner
        from app.publish import runner
        self.cfg = copy.deepcopy(settings.DEFAULTS)
        self.cfg['modules']['translation'] = True
        self.cfg['text_lang']['reddit'] = 'both'
        self.draft = {'title': 'Закат', 'text': 'Описание',
                      'variants': {'en': {'title': 'Sunset', 'text': 'Description'}}}

    def test_combines_fields_only_for_selected_destination(self):
        self.assertEqual(runner.variant(self.cfg, self.draft, 'reddit'),
                         ('Закат / Sunset', 'Описание\n\nDescription', 'both'))
        self.assertEqual(runner.variant(self.cfg, self.draft, 'telegram'),
                         ('Закат', 'Описание', 'main'))

    def test_empty_translation_and_identical_fields_have_no_extra_separator(self):
        self.draft['variants']['en'] = {'title': 'Закат', 'text': ''}
        self.assertEqual(runner.variant(self.cfg, self.draft, 'reddit')[:2], ('Закат', 'Описание'))
        self.draft['variants'] = {}
        self.assertEqual(runner.variant(self.cfg, self.draft, 'reddit'),
                         ('Закат', 'Описание', 'main'))

    def test_platform_override_retains_priority(self):
        self.cfg['platform_texts_enabled']['reddit'] = True
        self.draft['platform_texts'] = {'reddit': {'enabled': True, 'title': 'Custom', 'text': 'Body'}}
        self.assertEqual(runner.variant(self.cfg, self.draft, 'reddit')[:2], ('Custom', 'Body'))

    def test_disabled_translation_preserves_original(self):
        self.cfg['modules']['translation'] = False
        self.assertEqual(runner.variant(self.cfg, self.draft, 'reddit'),
                         ('Закат', 'Описание', 'main'))
