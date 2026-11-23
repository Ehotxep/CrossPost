"""Lost upload responses must not cause duplicate POSTs or expose credentials."""
import hashlib
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import Mock, patch

import requests
from urllib3 import exceptions as U3
from core import retry
from core.common import PublicationUnknown
from app.publish import e6ai


class UploadRecovery(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'original.png'
        self.path.write_bytes(b'exact original bytes')
        self.digest = hashlib.md5(self.path.read_bytes()).hexdigest()
        self.cfg = {'base':'https://floofgens.net','site_name':'floofgens',
                    'username':'fixture-user','api_key':'fixture-key-not-real'}

    def post(self, **changes):
        value = {'id':42,'file':{'md5':self.digest},'flags':{'deleted':False}}
        value.update(changes)
        return value

    def test_response_loss_checks_exact_hash_once_without_resending(self):
        response = Mock(status_code=200, json=lambda: {'posts':[self.post()]})
        def lose_response(method, url, **kwargs):
            self.assertEqual(method, 'POST')
            self.assertEqual(kwargs['files']['upload[file]'][1].read(), self.path.read_bytes())
            raise requests.ConnectionError(U3.ProtocolError('reset', ConnectionResetError(10054, 'private data')))
        with patch.object(retry.requests, 'request', side_effect=lose_response) as post, patch.object(e6ai.requests, 'get', return_value=response) as get:
            result = e6ai.upload(self.cfg, self.path, ['artist:fixture'], 'sfw')
        self.assertEqual(post.call_count, 1)
        self.assertTrue(result['duplicate'])
        self.assertTrue(result['recovered'])
        self.assertEqual(result['url'], 'https://floofgens.net/posts/42')
        get.assert_called_once()
        self.assertEqual(get.call_args.args[0], 'https://floofgens.net/posts.json')
        self.assertEqual(get.call_args.kwargs['params']['tags'], 'md5:' + self.digest)
        self.assertEqual(get.call_args.kwargs['auth'], ('fixture-user','fixture-key-not-real'))
        self.assertFalse(get.call_args.kwargs['allow_redirects'])

    def test_recovery_stays_on_each_original_site_and_uses_its_credentials(self):
        for site, host in [('e6ai','blep.art'), ('e621','e621.net'), ('floofgens','floofgens.net')]:
            cfg = {'base':'https://' + host, 'site_name':site,
                   'username':site + '-fixture', 'api_key':site + '-key-not-real'}
            for found in (True, False):
                response = Mock(status_code=200, json=lambda: {'posts':[self.post()] if found else []})
                with self.subTest(site=site, found=found), patch('core.retry.post', side_effect=PublicationUnknown('lost response')) as post, patch.object(e6ai.requests, 'get', return_value=response) as get:
                    if found:
                        result = e6ai.upload(cfg, self.path, [], 'sfw')
                        self.assertEqual(result['url'], 'https://' + host + '/posts/42')
                    else:
                        with self.assertRaises(PublicationUnknown):
                            e6ai.upload(cfg, self.path, [], 'sfw')
                    post.assert_called_once()
                    self.assertEqual(post.call_args.args[0], 'https://' + host + '/uploads.json')
                    self.assertEqual(post.call_args.kwargs['auth'], (cfg['username'],cfg['api_key']))
                    self.assertFalse(post.call_args.kwargs['idempotent'])
                    get.assert_called_once()
                    self.assertEqual(get.call_args.args[0], 'https://' + host + '/posts.json')
                    self.assertEqual(get.call_args.kwargs['auth'], (cfg['username'],cfg['api_key']))
                    self.assertFalse(get.call_args.kwargs['allow_redirects'])

    def test_no_match_wrong_hash_deleted_and_malformed_results_remain_unknown(self):
        for value in ({'posts':[]}, {'posts':[self.post(file={'md5':'wrong'})]},
                      {'posts':[self.post(flags={'deleted':True})]}, {'posts':[self.post(id=True)]},
                      {'posts':[self.post(id='https://evil.invalid')]}, {'posts':[None]}, {'posts':None}):
            with self.subTest(value=value), patch('core.retry.post', side_effect=PublicationUnknown('lost response')) as post, patch.object(e6ai.requests, 'get', return_value=Mock(status_code=200, json=lambda:value)):
                with self.assertRaisesRegex(PublicationUnknown, 'не подтвердила'):
                    e6ai.upload(self.cfg, self.path, ['artist:fixture'], 'sfw')
                self.assertEqual(post.call_count, 1)

    def test_failed_reconciliation_cannot_turn_into_success_or_retry_upload(self):
        for error in (requests.ReadTimeout('read only lookup failed'), ValueError('not JSON')):
            with self.subTest(error=error), patch('core.retry.post', side_effect=PublicationUnknown('original failure')) as post, patch.object(e6ai.requests, 'get', side_effect=error):
                with self.assertRaisesRegex(PublicationUnknown, 'original failure'):
                    e6ai.upload(self.cfg, self.path, [], 'sfw')
                post.assert_called_once()
        with patch('core.retry.post', side_effect=PublicationUnknown('lost')) as post, patch.object(e6ai.requests, 'get', return_value=Mock(status_code=302)):
            with self.assertRaises(PublicationUnknown):
                e6ai.upload(self.cfg, self.path, [], 'sfw')
            post.assert_called_once()

    def test_normal_success_does_not_query_history(self):
        with patch('core.retry.post', return_value=Mock(status_code=200, json=lambda:{'success':True,'post_id':42})), patch.object(e6ai.requests, 'get') as get:
            self.assertFalse(e6ai.upload(self.cfg, self.path, [], 'sfw')['duplicate'])
        get.assert_not_called()

    def test_nested_transport_cause_and_code_are_visible_without_raw_exception(self):
        private = 'https://user:password@example.invalid/?api_key=secret-and-private'
        error = requests.ConnectionError(U3.ProtocolError(private, ConnectionResetError(10054, private)))
        with patch.object(retry.requests, 'request', side_effect=error) as request:
            with self.assertRaises(PublicationUnknown) as raised:
                retry.post('https://example.invalid/create', idempotent=False)
        text = str(raised.exception)
        self.assertIn('ConnectionResetError', text)
        self.assertIn('10054', text)
        self.assertNotIn(private, text)
        self.assertNotIn('secret-and-private', text)
        request.assert_called_once()

    def test_tls_proxy_and_dns_causes_are_distinguishable(self):
        cases = [(requests.exceptions.SSLError('private details'), 'TLS'),
                 (requests.exceptions.ProxyError('private details'), 'прокси'),
                 (requests.ConnectionError(socket.gaierror(-2, 'private details')), 'DNS')]
        for error, expected in cases:
            with self.subTest(expected=expected):
                text = retry.network_reason(error)
                self.assertIn(expected, text)
                self.assertNotIn('private details', text)
