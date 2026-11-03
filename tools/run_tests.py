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
    summary = os.environ.get('GITHUB_STEP_SUMMARY')
    if summary:
        from app.jobs import mask_secrets
        text = f'## Regression tests\n\nRun: {result.testsRun}; failures: {len(result.failures)}; errors: {len(result.errors)}; skipped: {len(result.skipped)}.\n'
        for case, detail in result.failures + result.errors:
            text += f'\n### {case.id()}\n\n```text\n{mask_secrets(detail)[:16000]}\n```\n'
        with open(summary, 'a', encoding='utf-8') as output:
            output.write(text)
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    sys.exit(main())
