"""Имитация страниц ВКонтакте для проверки публикации через браузер (вёрстка условная, логика — как у ВК).

/            — «вход»: через 2 с ставит куку remixsid (как будто человек вошёл)
/club123     — группа: поле «Напишите что-нибудь…», кнопка «Фото», загрузка с индикатором, «Опубликовать»
"""
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PAGE_LOGIN = """<!doctype html><html><head><meta charset=utf-8><title>ВКонтакте</title></head><body>
<h1>Вход</h1><script>setTimeout(()=>{document.cookie='remixsid=abc123; path=/'; location.href='/feed'}, 2000)</script></body></html>"""
PAGE_FEED = """<!doctype html><html><head><meta charset=utf-8><title>Новости | ВКонтакте</title></head><body><h1>Лента</h1></body></html>"""
PAGE_GROUP = """<!doctype html><html><head><meta charset=utf-8><title>Тест-группа | ВКонтакте</title>
<style>.hidden{display:none} [contenteditable]{border:1px solid #ccc;min-height:40px;padding:6px} #dlg{position:fixed;top:60px;left:200px;width:600px;background:#fff;border:1px solid #888;padding:12px} img{width:70px;height:70px}</style></head>
<body><h1>Тест-группа</h1>
<button data-testid="group_publish_create_button" id="create"><span>Создать</span></button>
<div id="menu" class="hidden" role="menu"><div role="menuitem" id="m-post">Пост</div><div role="menuitem">Клип</div><div role="menuitem">История</div></div>
<div id="wall">
  <div data-testid="post"><a data-testid="post_date_block_preview" href="/wall-123_100">закреплённая запись</a>
    <div data-testid="wall_comments_layout_root"><div class="reply_box"><div contenteditable="true" class="reply_field" id="replyfield">комментарий</div><button class="reply_send" id="replysend">Отправить</button></div>
      <button data-testid="comment-attach-photo" aria-label="Фото">фото к комментарию</button></div></div>
</div>
<div id="dlg" class="hidden" role="dialog" aria-modal="true">
  <div id="editor" contenteditable="true" role="textbox"></div>
  <button aria-label="Фото" id="photo">📷</button><input id="file" type="file" accept="image/*" multiple style="display:none">
  <div id="thumbs"></div>
  <button id="next">Далее</button>
</div>
<div id="dlg2" class="hidden" role="dialog" aria-modal="true"><p>Настройки публикации</p><button id="back">Назад</button><button id="pub">Опубликовать</button></div>
<script>
const $=id=>document.getElementById(id);
let clicks=0; $('create').onclick=()=>{ if(++clicks===1) return; $('menu').classList.remove('hidden') };
$('replysend').onclick=()=>{window.__wrong='отправлен комментарий: '+$('replyfield').innerText};
$('m-post').onclick=()=>{$('menu').classList.add('hidden');$('dlg').classList.remove('hidden');
  const d=JSON.parse(localStorage.getItem('draft')||'{"t":"","n":0}'); $('editor').innerText=d.t;
  for(let i=0;i<d.n;i++) addThumb('data:image/gif;base64,R0lGODlhAQABAAAAACw=');
  $('editor').focus()};
function addThumb(src){const w=document.createElement('span');const im=document.createElement('img');im.src=src;const x=document.createElement('button');x.setAttribute('aria-label','Удалить');x.textContent='×';x.onclick=()=>{w.remove();save()};w.appendChild(im);w.appendChild(x);$('thumbs').appendChild(w);save()}
function save(){localStorage.setItem('draft',JSON.stringify({t:$('editor').innerText,n:$('thumbs').querySelectorAll('img').length}))}
setInterval(()=>{if(!$('dlg').classList.contains('hidden'))save()},200);
document.querySelector('[data-testid=comment-attach-photo]').onclick=()=>{window.__wrong='comment photo clicked'};
$('photo').onclick=()=>$('file').click();
$('file').onchange=()=>{[...$('file').files].forEach(f=>{
  const d=document.createElement('div');d.className='upload_progress';d.textContent='загрузка…';$('thumbs').appendChild(d);
  setTimeout(()=>{d.remove();addThumb(URL.createObjectURL(f))},1200)})};
$('next').onclick=()=>{window.__text=$('editor').innerText;save();$('dlg').classList.add('hidden');$('dlg2').classList.remove('hidden')};
$('back').onclick=()=>{$('dlg2').classList.add('hidden');$('dlg').classList.remove('hidden')};
document.addEventListener('keydown',e=>{if(e.key==='Escape'){if(!$('dlg').classList.contains('hidden'))save();$('dlg').classList.add('hidden');$('dlg2').classList.add('hidden')}});
$('pub').onclick=()=>{const n=$('thumbs').querySelectorAll('img').length;const text=window.__text||'';
  const a=document.createElement('a');a.href='/wall-123_777';a.textContent='новая запись: '+text.replace(/\\n/g,' / ')+' ('+n+' фото)';a.id='newpost';
  $('wall').prepend(a);$('editor').innerText='';$('thumbs').innerHTML='';localStorage.removeItem('draft');$('dlg2').classList.add('hidden')};
</script></body></html>"""


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        logged = "remixsid=abc123" in (self.headers.get("Cookie") or "")
        if self.path.startswith("/club123"):
            body = PAGE_GROUP if logged else PAGE_LOGIN
        elif self.path.startswith("/feed"):
            body = PAGE_FEED
        else:
            body = PAGE_FEED if logged else PAGE_LOGIN
        data = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1]) if len(sys.argv) > 1 else 8790), H).serve_forever()
