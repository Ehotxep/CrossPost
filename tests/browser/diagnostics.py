"""Failure artifacts for isolated browser fixtures, never live account sessions."""
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]


def watch_page(page):
    messages = []

    def record(kind, message):
        messages.append(f'{kind}: {str(message)[:2000]}')
        del messages[:-100]

    page.on('console', lambda message: record(f'console/{message.type}', message.text))
    page.on('pageerror', lambda error: record('pageerror', error))
    page._crosspost_diagnostics = messages
    return page


def capture_failure(page, test_id):
    """Capture before finally closes the page; preserve the original exception."""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from app.jobs import mask_secrets

    try:
        folder = ROOT / 'test-artifacts'
        folder.mkdir(exist_ok=True)
        name = re.sub(r'[^\w.-]', '_', test_id)
        # A test may exercise several fixture pages; keep each failure separately.
        base = folder / name
        counter = 1
        log = Path(str(base) + '.log')
        while log.exists():
            base = folder / f'{name}-{counter}'
            log = Path(str(base) + '.log')
            counter += 1
        messages = getattr(page, '_crosspost_diagnostics', [])
        log.write_text(mask_secrets('\n'.join(messages) or 'No browser console messages.'), encoding='utf-8')
        if not page.is_closed():
            page.screenshot(path=str(base) + '.png', full_page=True, timeout=5000)
    except Exception as error:
        print(f'Browser diagnostics unavailable: {mask_secrets(str(error))}', file=sys.stderr)


def close_page(page, test_id):
    failing = sys.exc_info()[0] is not None
    if failing:
        capture_failure(page, test_id)
    try:
        page.close()
    except Exception:
        if not failing:
            raise
