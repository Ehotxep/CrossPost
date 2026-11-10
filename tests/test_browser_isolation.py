import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from app.publish import browser as B


class BrowserIsolation(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        env = patch.dict(os.environ, CROSSPOST_DATA=folder.name, CROSSPOST_BROWSER_EXE='/fake/chrome')
        env.start()
        self.addCleanup(env.stop)

    def cookie(self, domain, path="/"):
        return {"name":"session","value":"test","domain":domain,"path":path,"expires":-1}

    def test_each_site_launches_a_distinct_clean_profile(self):
        pw=Mock()
        for platform in B.PLATFORMS:
            B._launch(pw,False,platform=platform)
        paths=[call.kwargs["user_data_dir"] for call in pw.chromium.launch_persistent_context.call_args_list]
        self.assertEqual(len(set(paths)),4)
        self.assertTrue(all(Path(path).parent==self.root/'browser_profiles' for path in paths))
        self.assertNotIn(str(self.root/'browser'),paths)
        with self.assertRaises(ValueError):
            B.profile_dir('../browser')

    def test_legacy_cookie_migration_never_imports_another_site(self):
        legacy=self.root/'browser'
        legacy.mkdir()
        cookies=[self.cookie('.reddit.com'),self.cookie('.vk.com'),self.cookie('evilreddit.com'),self.cookie('.reddit.com.evil')]
        (legacy/'crosspost_cookies.json').write_text(json.dumps(cookies))
        for platform,expected in [('reddit','.reddit.com'),('vk','.vk.com')]:
            ctx=Mock()
            ctx._crosspost_platform=platform
            ctx.cookies.return_value=[]
            B._restore_cookies(ctx)
            self.assertEqual(ctx.add_cookies.call_args.args[0],[self.cookie(expected)])
        ctx=Mock()
        ctx._crosspost_platform='x'
        ctx.cookies.return_value=[]
        B._restore_cookies(ctx)
        ctx.add_cookies.assert_not_called()

    def test_save_and_restore_are_scoped_to_platform_and_cookie_path(self):
        ctx=Mock()
        ctx._crosspost_platform='reddit'
        ctx.cookies.return_value=[self.cookie('.reddit.com'),self.cookie('.reddit.com','/r'),self.cookie('.vk.com')]
        B.save_cookies(ctx)
        saved=json.loads(B._cookie_file('reddit').read_text())
        self.assertEqual(len(saved),2)
        self.assertFalse(B._cookie_file('vk').exists())
        ctx.cookies.return_value=[self.cookie('.reddit.com')]
        B._restore_cookies(ctx)
        self.assertEqual(ctx.add_cookies.call_args.args[0],[self.cookie('.reddit.com','/r')])
