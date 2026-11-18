import unittest
from unittest.mock import Mock, patch
from app.api import Api


class LocalChannelSuggestions(unittest.TestCase):
    def make_api(self):
        api=Api.__new__(Api)
        api.tg=Mock()
        api.tg.channels.return_value=[{'chat_id':'42','title':'Channel','username':'test'}]
        return api

    def test_opening_settings_uses_only_saved_status_and_local_channels(self):
        api=self.make_api()
        with patch('app.cache.read',return_value={'data':{'authorized':True}}):
            result=api.pub_tg_known()
        self.assertTrue(result['authorized'])
        api.tg.channels.assert_called_once_with(refresh=False)
        api.tg.status.assert_not_called()
        api.tg.backend.assert_not_called()
        api.tg.keys.assert_not_called()

    def test_missing_status_does_not_start_network_login_check(self):
        api=self.make_api()
        with patch('app.cache.read',return_value=None):
            self.assertFalse(api.pub_tg_known()['authorized'])
        api.tg.channels.assert_called_once_with(refresh=False)
        api.tg.status.assert_not_called()

    def test_explicit_refresh_checks_authorization_and_refreshes_channels(self):
        api=self.make_api()
        with patch.object(api,'tg_status',return_value={'authorized':True}) as status:
            self.assertTrue(api.pub_tg_known(refresh=True)['authorized'])
        status.assert_called_once_with()
        api.tg.channels.assert_called_once_with(refresh=True)

    def test_explicit_refresh_reports_connection_error_and_preserves_local_list(self):
        api=self.make_api()
        with patch.object(api,'tg_status',return_value={'authorized':False,'error':'Offline'}):
            with self.assertRaisesRegex(ValueError,'Offline'):
                api.pub_tg_known(refresh=True)
        api.tg.channels.assert_not_called()
