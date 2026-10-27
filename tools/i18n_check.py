"""Проверка перевода: какие русские тексты в коде ещё не попали в app/web/lang_en.js.

Запуск из папки проекта:  python tools/i18n_check.py
Новые тексты дописывайте в lang_en.js (ключ — русский текст как в коде, значение — перевод).
Ключи — без лишних пробелов по краям; куски фраз (между тегами и подстановками ${…}) переводятся по отдельности.
"""
import ast
import html
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CY = re.compile(r"[А-Яа-яЁё]")
RUN = re.compile(r"[^<>\"'`${}\n\\]*[А-Яа-яЁё][^<>\"'`${}\n\\]*")
NOISE = re.compile(r"\|.*\||=>|\.test\(|^/|\\\\|^try:|^\[|^\^|^esc\(|^const |CREATE TABLE|-- |INTEGER|TEXT|^UPDATE|^s\.|\.vbs|^https?://|^tg://|^posts-|^tags-|^<токен|^\w+\.\w+\(")


def norm(s):
    return re.sub(r"\s+", " ", s).strip()


def main():
    dic = open(os.path.join(ROOT, "app/web/lang_en.js"), encoding="utf-8").read()
    dic = json.loads(dic[dic.index("{"):dic.rindex("}") + 1])
    keys = set(dic)
    words = set(w.lower() for k in keys for w in re.findall(r"[А-Яа-яЁё]+", k))
    found = {}
    for f in ("app/web/app.js", "app/web/publish.js", "app/web/charts.js", "app/web/videditor.js", "app/web/index.html"):
        src = open(os.path.join(ROOT, f), encoding="utf-8").read()
        src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
        src = re.sub(r"<!--.*?-->", "", src, flags=re.S) if f.endswith("html") else re.sub(r"(?m)(^|\s)//[^\n]*", "", src)
        src = src.replace("\\n", " ").replace("\\t", " ")
        for m in RUN.finditer(src):
            found.setdefault(norm(html.unescape(m.group(0))), f)
    for d in ("app", "core", "platforms"):
        for dp, _, fn in os.walk(os.path.join(ROOT, d)):
            if "__pycache__" in dp or os.sep + "tests" in dp:
                continue
            for x in fn:
                if not x.endswith(".py"):
                    continue
                p = os.path.join(dp, x)
                tree = ast.parse(open(p, encoding="utf-8").read())
                doc = {id(n.body[0].value) for n in ast.walk(tree) if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef))
                       and n.body and isinstance(n.body[0], ast.Expr) and isinstance(getattr(n.body[0], "value", None), ast.Constant)}
                for n in ast.walk(tree):
                    if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in doc and CY.search(n.value):
                        for line in n.value.split("\n"):
                            found.setdefault(norm(line), os.path.relpath(p, ROOT))
    miss = sorted((t, f) for t, f in found.items() if CY.search(t) and not NOISE.search(t) and t not in keys
                  and any(w.lower() not in words for w in re.findall(r"[А-Яа-яЁё]+", t)) or False)
    # непереведённой считаем строку, в которой есть слово, которого нет ни в одном ключе словаря
    for t, f in miss:
        print(f"{f}: {t}")
    print(f"\nНе хватает слов для перевода: {len(miss)} строк", file=sys.stderr)
    return 1 if miss else 0


if __name__ == "__main__":
    sys.exit(main())
