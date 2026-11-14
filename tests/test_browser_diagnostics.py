"""Diagnostics must retain failures without leaking fixture credentials."""
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from browser import diagnostics
from tools import run_tests


class BrowserDiagnostics(unittest.TestCase):
    def test_failed_capture_does_not_replace_original_failure_and_masks_log(self):
        with tempfile.TemporaryDirectory() as folder:
            page = Mock()
            page.is_closed.return_value = False
            page._crosspost_diagnostics = ['api_key=fixturesecret123456']
            page.screenshot.side_effect = RuntimeError('capture unavailable')
            page.close.side_effect = RuntimeError('close unavailable')
            with patch.object(diagnostics, 'ROOT', Path(folder)), patch('sys.stderr', new=io.StringIO()):
                with self.assertRaisesRegex(AssertionError, 'original failure'):
                    try:
                        raise AssertionError('original failure')
                    finally:
                        diagnostics.close_page(page, 'suite.test')
            text = (Path(folder) / 'test-artifacts/suite.test.log').read_text(encoding='utf-8')
            self.assertNotIn('fixturesecret123456', text)
            self.assertIn('<скрыто>', text)

    def test_report_retains_sanitized_traceback_without_github_environment(self):
        case = unittest.FunctionTestCase(lambda: None)
        result = unittest.TestResult()
        result.testsRun = 1
        result.failures = [(case, 'AssertionError: api_key=fixturesecret123456')]
        with tempfile.TemporaryDirectory() as folder, patch.object(run_tests, 'ROOT', Path(folder)), patch.dict('os.environ', {}, clear=True):
            self.assertEqual(run_tests.report_result(result), 1)
            text = (Path(folder) / 'test-artifacts/test-failures.log').read_text(encoding='utf-8')
            self.assertIn('AssertionError', text)
            self.assertNotIn('fixturesecret123456', text)
            self.assertIn('<скрыто>', text)

    def test_unwritable_report_preserves_failure_exit_status(self):
        result = unittest.TestResult()
        result.errors = [(unittest.FunctionTestCase(lambda: None), 'original error')]
        with patch.object(Path, 'mkdir', side_effect=PermissionError('read only')), patch('sys.stderr', new=io.StringIO()):
            self.assertEqual(run_tests.report_result(result), 1)
