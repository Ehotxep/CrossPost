import hashlib
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import zipfile

from app import updates, selfupdate


def release(version, experimental=False):
    name = f'Crosspost-{version}.zip'
    return {'tag_name': 'experimental-123-1' if experimental else 'v'+version,
            'prerelease': experimental, 'draft': False,
            'assets': [{'name': name, 'browser_download_url': selfupdate.DL_PREFIX+'tag/'+name},
                       {'name': name+'.sha256', 'browser_download_url': selfupdate.DL_PREFIX+'tag/'+name+'.sha256'}]}


class UpdateChannels(unittest.TestCase):
    def test_selects_compatible_channel_and_compares_builds(self):
        self.assertTrue(updates.eligible('0.2.15-experimental.3', 'experimental', '0.2.15-experimental.2'))
        self.assertFalse(updates.eligible('0.2.15-experimental.2', 'experimental', '0.2.15-experimental.3'))
        self.assertTrue(updates.eligible('0.2.15-experimental.2', 'experimental', '0.2.15'))
        self.assertTrue(updates.eligible('0.2.15', 'stable', '0.2.16-experimental.2'))
        self.assertFalse(updates.eligible('0.2.15-experimental.2', 'stable', '0.2.14'))
        self.assertEqual(updates.release_version(release('0.2.15-experimental.2', True), 'experimental'), '0.2.15-experimental.2')
        with self.assertRaises(ValueError):
            updates.release_version(release('0.2.15-experimental.2', True), 'stable')

    def test_new_experimental_base_upgrades_old_build_and_allows_stable_return(self):
        self.assertTrue(updates.eligible('0.2.17-experimental.13', 'experimental', '0.2.15-experimental.12'))
        self.assertFalse(updates.eligible('0.2.15-experimental.99', 'experimental', '0.2.17-experimental.13'))
        self.assertTrue(updates.eligible('0.2.17-experimental.13', 'experimental', '0.2.16'))
        self.assertTrue(updates.eligible('0.2.16', 'stable', '0.2.17-experimental.13'))

    def test_fetch_ignores_other_prereleases_and_chooses_newest_build(self):
        older = release('0.2.15-experimental.2', True)
        newer = release('0.2.15-experimental.10', True)
        other = release('0.2.15-experimental.99', True)
        other['tag_name'] = 'random-preview'
        with patch('requests.get', return_value=Mock(status_code=200, json=lambda: [older, other, newer])):
            self.assertIs(updates.fetch_release('experimental'), newer)

    def test_switch_clears_cached_release_and_rejects_invalid_channel(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, CROSSPOST_DATA=folder):
            updates._save({'check': True, 'channel': 'stable', 'latest': {'version':'9.0'}, 'skip':'9.0'})
            state = updates.set_channel('experimental')
            self.assertEqual(state['channel'], 'experimental')
            self.assertIsNone(state['latest'])
            self.assertFalse(state['newer'])
            with self.assertRaises(ValueError): updates.set_channel('other')

    def test_prepare_and_ready_enforce_selected_channel(self):
        version='0.2.15-experimental.3'
        content=io.BytesIO()
        with zipfile.ZipFile(content, 'w') as archive:
            archive.writestr('Crosspost/app/__init__.py', f'__version__ = "{version}"\n')
            for name in ('app/main.py','app/api.py','app/apply_update.py','app/selfupdate.py','Crosspost.bat','requirements-app.txt'):
                archive.writestr('Crosspost/'+name, '')
        data=content.getvalue()
        def download(url, path, report, label, limit):
            path.write_bytes((hashlib.sha256(data).hexdigest()+'  file.zip\n').encode() if url.endswith('.sha256') else data)
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, CROSSPOST_DATA=folder):
            updates.set_channel('experimental')
            with patch.object(selfupdate, '__version__', '0.2.15'), patch.object(updates, '__version__', '0.2.15'), patch.object(selfupdate, 'can_update', return_value=(True,'')), patch.object(selfupdate, '_download', side_effect=download):
                selfupdate.prepare(lambda *args: None, release(version, True))
                self.assertEqual(selfupdate.ready()['version'], version)
                updates.set_channel('stable')
                self.assertIsNone(selfupdate.ready())


if __name__ == '__main__':
    unittest.main()
