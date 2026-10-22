"""Публикация из приложения. Использует модули площадок из папки platforms/ (те же, что у crosspost.py)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
