/* Редактор видео и GIF для нового поста: рамка обрезки, лента кадров с ползунками начала и конца,
   поворот, отражение, разрешение, переключатель GIF. Исходный файл не меняется: «Готово» делает новый файл
   (ffmpeg на сервере), а publish.js подставляет его в пост на место прежнего.

   window.VidEditor.open({ file, api, tok, toast, onDone(status) })
   Окно живёт отдельно от экранов приложения (в <body>) — перерисовка страницы не сбрасывает ему видео. */
(() => {
  "use strict";
  const STOPS = [[360, "360p"], [480, "480p"], [720, "720p"], [0, "Исходное"]];
  const FPS = [8, 10, 12, 15, 20, 24], MBS = [0, 8, 15, 30, 50];
  const MIN_SEL = 0.5, MIN_CROP = 0.08;
  const LOOPS = [["none", "Нет"], ["cross", "Кроссфейд"], ["pingpong", "Пинг-понг"], ["fade", "Из чёрного и в чёрное"]];
  const XF = [["fade", "Плавно"], ["dissolve", "Растворение"], ["fadeblack", "Через чёрное"], ["slideleft", "Сдвиг"], ["wipeleft", "Шторка"]];
  const DS = [0.3, 0.5, 1, 1.5, 2, 3];
  const fnum = (v) => String(v).replace(".", ",");
  const esc = (t) => String(t).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const even = (x) => Math.max(2, Math.floor(x / 2) * 2);
  const fmtT = (s) => { s = Math.max(0, s); const m = Math.floor(s / 60), r = s - m * 60; return String(m).padStart(2, "0") + ":" + (r < 10 ? "0" : "") + r.toFixed(1); };
  const mbf = (b) => b < 1048576 ? Math.max(1, Math.round(b / 1024)) + " КБ" : (b / 1048576).toFixed(b < 10485760 ? 1 : 0).replace(".", ",") + " МБ";

  const ICON = {
    rot: `<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M20 12a8 8 0 1 1-2.6-5.9"/><path d="M20 4v5h-5"/></svg>`,
    flip: `<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v18" stroke-dasharray="2.5 2.5"/><path d="M9 7 3.5 17H9z"/><path d="M15 7l5.5 10H15z"/></svg>`,
  };

  let E = null;     // открытое окно
  let job = null;   // идущая правка (продолжается, даже если окно закрыли)

  // ---------- открыть / закрыть ----------
  function open(o) {
    if (E) return;
    const f = o.file;
    E = { o, f, tok: o.tok, meta: null, start: 0, end: 0, cur: 0, rot: 0, flip: false, crop: { x: 0, y: 0, w: 1, h: 1 }, short: 0,
          gif: f.kind === "gif", fps: 15, maxmb: 15, busy: false, playing: false, err: "", drag: null, el: {},
          loop: { mode: "none", d: 1, xf: "fade", color: "black" }, lpBusy: false, lpShow: false };
    const root = document.createElement("div");
    root.className = "ved"; root.setAttribute("role", "dialog"); root.setAttribute("aria-label", "Редактор видео");
    root.innerHTML = `<div class="ved-load"><span>Готовлю кадры…</span><button class="btn ghost" data-v="close">Отмена</button></div>`;
    document.body.appendChild(root);
    E.root = root;
    root.addEventListener("click", onClick);
    root.addEventListener("pointerdown", onDown);
    root.addEventListener("pointermove", onMove);
    root.addEventListener("pointerup", onUp);
    root.addEventListener("pointercancel", onUp);
    document.addEventListener("keydown", onKey);
    window.addEventListener("resize", layout);
    o.api("media_editor_open", { file_id: f.id }).then((m) => {
      if (!E || E.root !== root) return;
      E.meta = m; E.end = m.duration; build();
    }).catch((err) => { o.toast(err.message, "error"); if (E && E.root === root) close(); });
  }

  function close() {
    if (!E) return;
    cancelAnimationFrame(E.raf);
    try { if (E.el.vid) E.el.vid.pause(); } catch (e) { /* ignore */ }
    document.removeEventListener("keydown", onKey);
    window.removeEventListener("resize", layout);
    E.root.remove();
    E = null;
  }

  // ---------- разметка ----------
  function build() {
    const m = E.meta, id = E.f.id, t = encodeURIComponent(E.tok);
    E.root.innerHTML = `
      <div class="ved-stage">
        <div class="ved-m"><video muted playsinline preload="auto" src="/uvideo/${id}?t=${t}" poster="/ubig/${id}?t=${t}"></video></div>
        <div class="ved-crop" data-d="move">
          <i class="ved-h nw" data-d="nw"></i><i class="ved-h ne" data-d="ne"></i><i class="ved-h sw" data-d="sw"></i><i class="ved-h se" data-d="se"></i>
          <i class="ved-e n" data-d="n"></i><i class="ved-e s" data-d="s"></i><i class="ved-e w" data-d="w"></i><i class="ved-e e" data-d="e"></i>
        </div>
        <button class="ved-play" data-v="play" aria-label="Играть">▶</button>
        <video class="ved-lp" muted loop playsinline hidden></video>
        <button class="ved-lpback" data-v="lpback" hidden>← К правке <span>просмотр петли: 360p, без звука</span></button>
        <div class="ved-busy" hidden><b></b><div class="progress"><div></div></div></div>
      </div>
      <div class="ved-info"><span class="cur"></span><b class="dur"></b><span class="size"></span></div>
      <div class="ved-tl" data-tl>
        <div class="ved-strip" style="background-image:url(/ustrip/${id}?t=${t})"></div>
        <i class="ved-dim l"></i><i class="ved-dim r"></i>
        <div class="ved-sel"><i class="ved-th l" data-d="ts"></i><i class="ved-th r" data-d="te"></i></div>
        <i class="ved-ph" data-d="ph"></i>
      </div>
      <div class="ved-res" role="radiogroup" aria-label="Разрешение">${STOPS.map(([v, l]) => `<button role="radio" class="ved-stop" data-v="short" data-val="${v}"><i></i><span>${l}</span></button>`).join("")}</div>
      <div class="ved-loop">
        <div class="ved-row">
          <div class="seg" role="radiogroup" aria-label="Зацикливание"><span class="seglbl">Петля</span>${LOOPS.map(([k, l]) => `<button role="radio" data-v="loop" data-val="${k}">${l}</button>`).join("")}</div>
          <button class="btn ghost sm" data-v="lpview" hidden>▶ Посмотреть петлю</button>
        </div>
        <div class="ved-row ved-lopt" hidden>
          <div class="seg" data-g="xf">${XF.map(([k, l]) => `<button data-v="lpxf" data-val="${k}">${l}</button>`).join("")}</div>
          <div class="seg" data-g="color"><button data-v="lpcol" data-val="black">Чёрный</button><button data-v="lpcol" data-val="white">Белый</button></div>
          <div class="seg" data-g="d"><span class="seglbl">длительность, с</span>${DS.map((v) => `<button data-v="lpd" data-val="${v}">${fnum(v)}</button>`).join("")}</div>
        </div>
      </div>
      <div class="ved-gif" hidden>
        <div class="seg" aria-label="Кадров в секунду"><span class="seglbl">к/с</span>${FPS.map((v) => `<button data-v="fps" data-val="${v}">${v}</button>`).join("")}</div>
        <div class="seg" aria-label="Потолок по весу">${MBS.map((v) => `<button data-v="mb" data-val="${v}">${v ? "до " + v + " МБ" : "без лимита"}</button>`).join("")}</div>
      </div>
      <p class="ved-msg"></p>
      <div class="ved-bar">
        <button class="vb" data-v="close">Отмена</button>
        <button class="vb ic" data-v="rot" aria-label="Повернуть" title="Повернуть на 90°">${ICON.rot}</button>
        <button class="vb ic" data-v="flip" aria-label="Отразить" title="Отразить по горизонтали">${ICON.flip}</button>
        <button class="vb gifb" data-v="gif" aria-pressed="false" title="Сделать из ролика GIF (без звука). Выключено — получится видео MP4">GIF</button>
        <button class="vb ok" data-v="done">Готово</button>
      </div>`;
    const q = (s) => E.root.querySelector(s);
    Object.assign(E.el, { stage: q(".ved-stage"), m: q(".ved-m"), vid: q("video"), crop: q(".ved-crop"), play: q(".ved-play"), busy: q(".ved-busy"),
      tl: q(".ved-tl"), dimL: q(".ved-dim.l"), dimR: q(".ved-dim.r"), sel: q(".ved-sel"), ph: q(".ved-ph"), cur: q(".ved-info .cur"),
      dur: q(".ved-info .dur"), size: q(".ved-info .size"), gifrow: q(".ved-gif"), msg: q(".ved-msg"), gifb: q(".vb.gifb"), done: q(".vb.ok"),
      lp: q(".ved-lp"), lpback: q(".ved-lpback"), lopt: q(".ved-lopt"), lpview: q('[data-v="lpview"]') });
    const v = E.el.vid;
    v.addEventListener("loadedmetadata", () => { v.currentTime = 0; });
    v.addEventListener("error", () => { if (E) { E.note = "Видео не удалось показать в окне, но править и сохранять его можно: кадры внизу — из самого файла."; paint(); } });
    v.addEventListener("pause", () => { if (E) { E.playing = false; paint(); } });
    layout(); paint();
  }

  // ---------- размеры окна просмотра и преобразования ----------
  function layout() {
    if (!E || !E.meta) return;
    const m = E.meta, odd = E.rot % 180 !== 0;
    const sw = odd ? m.height : m.width, sh = odd ? m.width : m.height, ar = sw / sh;
    const maxW = Math.min(window.innerWidth - 48, 900), maxH = Math.max(180, window.innerHeight - (E.gif ? 372 : 310) - (E.loop.mode === "none" ? 46 : 90));   // место под ленту, разрешение, кнопки (и строку GIF)
    const w = Math.min(maxW, maxH * ar), h = w / ar;
    const st = E.el.stage.style;
    st.width = w + "px"; st.height = h + "px";
    const mw = odd ? h : w, mh = odd ? w : h;
    const ms = E.el.m.style;
    ms.width = mw + "px"; ms.height = mh + "px";
    ms.transform = `translate(-50%,-50%) scaleX(${E.flip ? -1 : 1}) rotate(${E.rot}deg)`;
    const tw = Math.min(640, window.innerWidth - 48);
    E.el.tl.style.width = tw + "px";
    E.root.style.setProperty("--vw", tw + "px");
  }

  function dims() {
    const m = E.meta;
    let w = m.width, h = m.height;
    if (E.rot % 180) [w, h] = [h, w];
    const cw = even(w * E.crop.w), ch = even(h * E.crop.h);
    let ow = cw, oh = ch;
    if (E.short && Math.min(ow, oh) > E.short) { const k = E.short / Math.min(ow, oh); ow = even(ow * k); oh = even(oh * k); }
    return { cw, ch, ow, oh };
  }

  const fullCrop = () => E.crop.x < 0.002 && E.crop.y < 0.002 && E.crop.w > 0.998 && E.crop.h > 0.998;
  const sel = () => E.end - E.start;
  const dEff = () => Math.max(0.2, Math.min(E.loop.d, 3, sel() / 3));
  const effLen = () => E.loop.mode === "cross" ? sel() - dEff() : E.loop.mode === "pingpong" ? sel() * 2 - 2 / (E.gif ? E.fps : (E.meta.fps || 30)) : sel();
  const changed = () => E.loop.mode !== "none" || E.start > 0.05 || E.end < E.meta.duration - 0.05 || E.rot || E.flip || !fullCrop() || E.short || E.gif !== (E.meta.kind === "gif");

  function estimate() {   // грубая прикидка веса результата
    const m = E.meta, sl = effLen(), d = dims();
    if (E.gif) { const raw = d.ow * d.oh * sl * E.fps * 0.08; return E.maxmb ? Math.min(raw, E.maxmb * 1048576 * 0.97) : raw; }
    if (m.kind === "gif") return d.ow * d.oh * sl * 0.15;
    return m.size * (sl / m.duration) * Math.pow((d.ow * d.oh) / (m.width * m.height), 0.85);
  }

  // ---------- отрисовка ----------
  function paint() {
    if (!E || !E.meta) return;
    const m = E.meta, c = E.crop, el = E.el, dur = m.duration;
    el.crop.style.cssText = `left:${c.x * 100}%;top:${c.y * 100}%;width:${c.w * 100}%;height:${c.h * 100}%`;
    el.dimL.style.width = (E.start / dur * 100) + "%";
    el.dimR.style.width = ((dur - E.end) / dur * 100) + "%";
    el.sel.style.cssText = `left:${E.start / dur * 100}%;right:${(dur - E.end) / dur * 100}%`;
    paintPh();
    el.dur.textContent = fmtT(effLen());
    el.dur.title = E.loop.mode === "none" ? "" : `Длина с учётом зацикливания (выбрано ${fmtT(sel())})`;
    const est = estimate();
    el.size.textContent = (changed() ? "≈ " : "") + mbf(est);
    const d = dims(), shortSide = Math.min(d.cw, d.ch);
    E.root.querySelectorAll('[data-v="short"]').forEach((b) => {
      const v = +b.dataset.val, off = v && v >= shortSide;
      b.classList.toggle("on", v === E.short || (v === 0 && !E.short));
      b.disabled = E.busy || off;
      b.setAttribute("aria-checked", String(v === E.short));
    });
    el.gifrow.hidden = !E.gif;
    E.root.querySelectorAll('[data-v="fps"]').forEach((b) => { b.classList.toggle("on", +b.dataset.val === E.fps); b.disabled = E.busy; });
    E.root.querySelectorAll('[data-v="mb"]').forEach((b) => { b.classList.toggle("on", +b.dataset.val === E.maxmb); b.disabled = E.busy; });
    el.gifb.classList.toggle("on", E.gif);
    el.gifb.setAttribute("aria-pressed", String(E.gif));
    el.play.hidden = E.playing || E.busy || E.lpBusy || E.lpShow;
    el.busy.hidden = !(E.busy || E.lpBusy);
    el.crop.classList.toggle("off", E.busy);
    E.root.querySelectorAll(".vb").forEach((b) => { if (b.dataset.v !== "close") b.disabled = E.busy; });
    E.root.querySelector('.vb[data-v="close"]').textContent = E.busy ? "Свернуть" : "Отмена";
    el.done.textContent = E.busy ? "Делаю…" : "Готово";
    const lp = E.loop, s3 = sel();
    E.root.querySelectorAll('[data-v="loop"]').forEach((b) => {
      const k = b.dataset.val;
      b.classList.toggle("on", k === lp.mode); b.setAttribute("aria-checked", String(k === lp.mode));
      b.disabled = E.busy || E.lpBusy || (k === "pingpong" && s3 < 0.4) || ((k === "cross" || k === "fade") && s3 < 0.9);
    });
    el.lopt.hidden = !(lp.mode === "cross" || lp.mode === "fade");
    el.lopt.querySelector('[data-g="xf"]').hidden = lp.mode !== "cross";
    el.lopt.querySelector('[data-g="color"]').hidden = lp.mode !== "fade";
    el.lpview.hidden = lp.mode === "none";
    el.lpview.disabled = E.busy || E.lpBusy;
    E.root.querySelectorAll('[data-v="lpxf"]').forEach((b) => b.classList.toggle("on", b.dataset.val === lp.xf));
    E.root.querySelectorAll('[data-v="lpcol"]').forEach((b) => b.classList.toggle("on", b.dataset.val === lp.color));
    E.root.querySelectorAll('[data-v="lpd"]').forEach((b) => { const v = +b.dataset.val; b.classList.toggle("on", v === lp.d || (v === 3 && lp.d > 3)); b.disabled = v > s3 / 3 + 1e-6 || E.busy; });
    el.lp.hidden = !E.lpShow; el.lpback.hidden = !E.lpShow;
    el.crop.hidden = E.lpShow;
    let msg = E.err || E.note || "";
    if (!msg && lp.mode === "pingpong" && E.meta.has_audio && !E.gif) msg = "В пинг-понге звук не сохраняется: хвост играл бы задом наперёд.";
    if (!msg && E.gif && E.end - E.start > 15) msg = E.end - E.start > 60 ? "В GIF войдут только первые 60 секунд." : "GIF длиннее 15 секунд получается очень тяжёлым — лучше взять отрезок покороче или оставить видео.";
    el.msg.textContent = msg;
    el.msg.classList.toggle("err", !!E.err);
    paintBusy();
  }
  function paintPh() {
    const el = E.el, dur = E.meta.duration;
    el.ph.style.left = (E.cur / dur * 100) + "%";
    el.cur.textContent = fmtT(E.cur);
  }
  function paintBusy() {
    if (!E || !E.el.busy) return;
    E.el.busy.querySelector("b").textContent = E.lpBusy && !E.busy ? "Готовлю просмотр петли…" : (E.msg || "Запуск…");
    E.el.busy.querySelector(".progress > div").style.width = Math.round((E.prog || 0) * 100) + "%";
  }

  // ---------- проигрывание ----------
  function seek(t) { E.cur = clamp(t, 0, E.meta.duration); try { E.el.vid.currentTime = E.cur; } catch (e) { /* ignore */ } paintPh(); }
  function pause() { if (E.playing) { E.playing = false; try { E.el.vid.pause(); } catch (e) { /* ignore */ } cancelAnimationFrame(E.raf); } }
  function togglePlay() {
    if (E.busy) return;
    if (E.playing) { pause(); paint(); return; }
    const v = E.el.vid;
    if (v.currentTime < E.start - 0.05 || v.currentTime >= E.end - 0.05) v.currentTime = E.start;
    const p = v.play();
    if (p && p.catch) p.catch(() => {});
    E.playing = true; paint(); tick();
  }
  function tick() {
    if (!E || !E.playing) return;
    const v = E.el.vid;
    if (v.currentTime >= E.end - 0.03) v.currentTime = E.start;   // повтор внутри выбранного отрезка
    E.cur = v.currentTime; paintPh();
    E.raf = requestAnimationFrame(tick);
  }

  // ---------- мышь и касание ----------
  function onDown(e) {
    if (!E || !E.meta || E.busy || e.button > 0) return;
    if (e.target.closest("button")) return;
    if (E.lpShow) exitPrev();
    const dd = e.target.closest("[data-d]");
    let mode = dd ? dd.dataset.d : null;
    if (!mode && e.target.closest("[data-tl]")) mode = "seek";
    if (!mode) return;
    e.preventDefault();
    pause();
    E.drag = { mode, x0: e.clientX, y0: e.clientY, tl: E.el.tl.getBoundingClientRect(), st: E.el.stage.getBoundingClientRect(),
               crop: { ...E.crop }, start: E.start, end: E.end };
    try { E.root.setPointerCapture(e.pointerId); } catch (err) { /* ignore */ }
    onMove(e);
  }
  function onMove(e) {
    const g = E && E.drag;
    if (!g) return;
    const dur = E.meta.duration;
    const dx = e.clientX - g.x0, dy = e.clientY - g.y0;
    switch (g.mode) {
      case "ts": E.start = clamp(g.start + dx / g.tl.width * dur, 0, E.end - MIN_SEL); seek(E.start); break;
      case "te": E.end = clamp(g.end + dx / g.tl.width * dur, E.start + MIN_SEL, dur); seek(Math.max(E.start, E.end - 0.05)); break;
      case "seek": case "ph": seek(clamp((e.clientX - g.tl.left) / g.tl.width * dur, E.start, E.end)); break;
      case "move": {
        const c = g.crop;
        E.crop.x = clamp(c.x + dx / g.st.width, 0, 1 - c.w);
        E.crop.y = clamp(c.y + dy / g.st.height, 0, 1 - c.h);
        break;
      }
      default: {   // углы и стороны рамки
        let l = g.crop.x, r = g.crop.x + g.crop.w, t = g.crop.y, b = g.crop.y + g.crop.h;
        const fx = dx / g.st.width, fy = dy / g.st.height;
        if (g.mode.includes("w")) l = clamp(l + fx, 0, r - MIN_CROP);
        if (g.mode.includes("e")) r = clamp(r + fx, l + MIN_CROP, 1);
        if (g.mode.includes("n")) t = clamp(t + fy, 0, b - MIN_CROP);
        if (g.mode.includes("s")) b = clamp(b + fy, t + MIN_CROP, 1);
        E.crop = { x: l, y: t, w: r - l, h: b - t };
      }
    }
    paint();
  }
  function onUp(e) {
    if (!E || !E.drag) return;
    try { E.root.releasePointerCapture(e.pointerId); } catch (err) { /* ignore */ }
    E.drag = null;
  }
  function onKey(e) {
    if (!E) return;
    if (e.key === "Escape" && !E.busy) { e.preventDefault(); close(); }
    else if (e.key === " " && E.meta && !/INPUT|TEXTAREA|BUTTON/.test((e.target || {}).tagName || "")) { e.preventDefault(); togglePlay(); }
  }

  // ---------- кнопки ----------
  function onClick(e) {
    const b = e.target.closest("[data-v]");
    if (!b || !E || b.disabled) return;
    const v = b.dataset.v, val = b.dataset.val;
    if (E.lpShow && !["lpview", "lpback", "done", "close", "play"].includes(v)) exitPrev();
    switch (v) {
      case "close": close(); break;
      case "play": togglePlay(); break;
      case "rot": {   // поворот по часовой; рамка обрезки поворачивается вместе с картинкой
        pause();
        const c = E.crop;
        E.crop = { x: 1 - c.y - c.h, y: c.x, w: c.h, h: c.w };
        E.rot = (E.rot + (E.flip ? -90 : 90) + 360) % 360;
        layout(); paint(); break;
      }
      case "flip": pause(); E.crop = { ...E.crop, x: 1 - E.crop.x - E.crop.w }; E.flip = !E.flip; layout(); paint(); break;
      case "gif": E.gif = !E.gif; layout(); paint(); break;
      case "short": E.short = +val; paint(); break;
      case "fps": E.fps = +val; paint(); break;
      case "mb": E.maxmb = +val; paint(); break;
      case "loop": E.loop.mode = val; layout(); paint(); break;
      case "lpxf": E.loop.xf = val; paint(); break;
      case "lpcol": E.loop.color = val; paint(); break;
      case "lpd": E.loop.d = +val; paint(); break;
      case "lpview": lpView(); break;
      case "lpback": exitPrev(); break;
      case "done": finish(); break;
      default: break;
    }
  }

  function params() {
    const c = E.crop, m = E.meta, lp = E.loop;
    return { file_id: E.f.id, start: Math.round(E.start * 100) / 100, end: E.end >= m.duration - 0.05 ? 0 : Math.round(E.end * 100) / 100,
             rotate: E.rot, flip: E.flip, crop: fullCrop() ? null : { x: c.x, y: c.y, w: c.w, h: c.h }, short: E.short, gif: E.gif, fps: E.fps,
             loop: lp.mode === "none" ? null : { mode: lp.mode, d: lp.d, xf: lp.xf, color: lp.color } };
  }

  async function finish() {
    if (E.busy || E.lpBusy) return;
    if (!changed()) { close(); return; }
    pause();
    const p = params();
    p.max_mb = E.gif ? E.maxmb : 0;
    E.busy = true; E.err = ""; E.prog = 0; E.msg = "Запуск…"; paint();
    try { await E.o.api("media_edit_start", p); }
    catch (err) { E.busy = false; E.err = err.message; paint(); return; }
    job = { src: E.f.id, o: E.o, gif: E.gif };
    poll();
  }

  // ---------- просмотр петли: быстрый беззвучный рендер 360p с теми же правками ----------
  async function lpView() {
    if (E.busy || E.lpBusy) return;
    pause(); E.err = ""; E.lpBusy = true; E.prog = 0; paint();
    const p = params();
    delete p.short;
    try { await E.o.api("media_edit_preview", p); }
    catch (err) { E.lpBusy = false; E.err = err.message; paint(); return; }
    pollPrev(E.root);
  }
  async function pollPrev(root) {
    if (!E || E.root !== root || !E.lpBusy) return;
    let st;
    try { st = await E.o.api("media_edit_preview_status"); } catch (e) { setTimeout(() => pollPrev(root), 800); return; }
    const j = st.job;
    if (j && j.state === "running") { setTimeout(() => pollPrev(root), 300); return; }
    E.lpBusy = false;
    if (j && j.state === "done") {
      const v = E.el.lp;
      v.src = `/uloop/${E.f.id}?t=${encodeURIComponent(E.tok)}&n=${Date.now()}`;
      E.lpShow = true; paint();
      const pr = v.play(); if (pr && pr.catch) pr.catch(() => {});
    } else { E.err = (j && j.error) || "Не получилось подготовить просмотр"; paint(); }
  }
  function exitPrev() {
    if (!E.lpShow) return;
    E.lpShow = false;
    try { E.el.lp.pause(); E.el.lp.removeAttribute("src"); E.el.lp.load(); } catch (e) { /* ignore */ }
    paint();
  }

  async function poll() {   // не зависит от окна: правка дойдёт до поста, даже если окно свернули
    if (!job) return;
    let st;
    try { st = await job.o.api("media_edit_status"); } catch (e) { setTimeout(poll, 1500); return; }
    const j = st.job;
    if (j && j.state === "running") {
      if (E && E.busy) { E.prog = j.progress; E.msg = j.message; paintBusy(); }
      setTimeout(poll, 500); return;
    }
    const jb = job; job = null;
    if (j && j.state === "done" && st.file) { if (E) close(); jb.o.onDone(st, jb); return; }
    const msg = (j && j.error) || "Не получилось обработать файл";
    if (E) { E.busy = false; E.err = msg; paint(); } else jb.o.toast(msg, "error");
  }

  window.VidEditor = { open, busy: () => !!job };
})();
