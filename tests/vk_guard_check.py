import sys; import os; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from playwright.sync_api import sync_playwright
from app.publish import vk_web as V
WALL='''<div id=wall><div class="Comment_root" data-testid="comment"><span>hi</span><button aria-label="Удалить" onclick="window.del=(window.del||0)+1">Удалить</button><div contenteditable=true id=cf>комм</div></div>
<button role=button onclick="window.del=(window.del||0)+1">Удалить</button><button onclick="window.del=(window.del||0)+1">Закрыть</button></div>'''
COMPOSER='''<div role=dialog><div contenteditable=true id=ed>текст</div><button aria-label="Удалить" id=rm onclick="window.rm=1">x</button><button>Опубликовать</button></div>'''
with sync_playwright() as p:
    b=p.chromium.launch(); pg=b.new_page()
    # A: окно записи есть — вложение убирается, комментарии нетронуты
    pg.set_content(WALL+COMPOSER); ed=pg.locator("#ed")
    V._clear_draft(pg, ed); V._close_draft(pg, ed)
    print("A del=",pg.evaluate("window.del||0"),"rm=",pg.evaluate("window.rm||0"))
    # B: окна записи нет, только просмотр с комментарием в диалоге
    pg.set_content(WALL+'<div role=dialog><div class="comment" contenteditable=true id=cf2>x</div><button aria-label="Удалить" onclick="window.del=(window.del||0)+1">Удалить</button></div>')
    V._clear_draft(pg, pg.locator("#cf2")); V._close_draft(pg, pg.locator("#cf2"))
    print("B del=",pg.evaluate("window.del||0"), "text=",pg.locator("#cf2").inner_text())
    # C: окно записи с полем внутри блока комментариев
    pg.set_content(WALL+'<div role=dialog><div class="comment_box"><div contenteditable=true id=cf3>x</div></div><button>Опубликовать</button><button aria-label="Удалить" onclick="window.del=(window.del||0)+1">Удалить</button></div>')
    V._clear_draft(pg, pg.locator("#cf3")); print("C del=",pg.evaluate("window.del||0"), "text=",pg.locator("#cf3").inner_text())
    b.close()
