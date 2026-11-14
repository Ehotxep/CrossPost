"""Run regression tests and publish failure details in the CI job summary."""
import os
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='backslashreplace')
    if hasattr(sys.stderr, 'reconfigure'):
        sys.stderr.reconfigure(encoding='utf-8', errors='backslashreplace')
    suite = unittest.defaultTestLoader.discover(str(ROOT / 'tests'), pattern='test_*.py')
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return report_result(result)


def report_result(result):
    """Expose sanitized failures for regression and browser suites in CI."""
    from app.jobs import mask_secrets
    text = f'## Test results\n\nRun: {result.testsRun}; failures: {len(result.failures)}; errors: {len(result.errors)}; skipped: {len(result.skipped)}.\n'
    summary = os.environ.get('GITHUB_STEP_SUMMARY')
    for case, detail in result.failures + result.errors:
        safe_detail = mask_secrets(detail)[:16000]
        text += f'\n### {case.id()}\n\n```text\n{safe_detail}\n```\n'
        if summary:
            annotation = safe_detail.replace('%', '%25').replace('\r', '%0D').replace('\n', '%0A')
            print(f'::error title={case.id()}::{annotation}', flush=True)
    try:
        if result.failures or result.errors:
            folder = ROOT / 'test-artifacts'
            folder.mkdir(exist_ok=True)
            with open(folder / 'test-failures.log', 'a', encoding='utf-8') as output:
                output.write(text)
        if summary:
            with open(summary, 'a', encoding='utf-8') as output:
                output.write(text)
    except OSError as error:
        print(f'Test report unavailable: {mask_secrets(str(error))}', file=sys.stderr)
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    sys.exit(main())
