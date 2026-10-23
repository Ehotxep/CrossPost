/* Публикация: «Новый пост», ход публикации, «История» и настройки площадок для публикации.
   Работает внутри app.js через общий контекст window.CP (api, состояние, помощники). */
(function () {
  const C = () => window.CP;
  const RATINGS = [
    ["sfw", "SFW", "без обнажённости"],
    ["erotic", "Эротика", "обнажённость и откровенные позы без половых актов"],
    ["explicit", "Откровенное", "всё, что дальше"],
  ];
  const DEST_RATING = [["sfw", "Только SFW"], ["nsfw", "Только 18+"], ["all", "Любой"]];
  const ORIG = [["reply", "Ответом на пост"], ["comments", "В комментарии"], ["none", "Не отправлять"]];
  const ACCEPT = "image/png,image/jpeg,image/webp,image/gif,video/mp4,video/webm,.png,.jpg,.jpeg,.webp,.gif,.mp4,.webm";
  const STATE_ICON = { queued: ["○", "в очереди"], running: ["◐", "идёт"], done: ["✓", "готово"], error: ["✕", "ошибка"] };
  let planTimer = null;

  const S = () => C().S;
  const TEXTP = ["telegram", "discord", "vk", "bluesky", "deviantart"];   // площадки с текстом поста и подписью
  const esc = (s) => C().esc(s == null ? "" : s);
  const mod = (k) => { const st = window.CP && window.CP.S, m = st && st.info && st.info.modules; return !m || m[k] !== false; };   // модуль включён
  const BN = () => {   // куда загружаем: e6ai или e621 (безопасно и до загрузки настроек)
    const st = window.CP && window.CP.S, e6 = st && st.pub && st.pub.e6ai;
    return (e6 && e6.site_name) || "e6ai";
  };
  const mb = (b) => b < 1024 * 1024 ? Math.max(1, Math.round(b / 1024)) + " КБ" : (b / 1024 / 1024).toFixed(b < 10 * 1024 * 1024 ? 1 : 0).replace(".", ",") + " МБ";
  const tok = () => encodeURIComponent(window.API_TOKEN);
  const ratingPill = (r) => r === "sfw" ? `<span class="pill sfw">SFW</span>` : r === "erotic" ? `<span class="pill erotic">Эротика</span>` : r === "explicit" ? `<span class="pill nsfw">18+</span>` : "";

  function draft() {
    const s = S();
    if (!s.draft) s.draft = C().store.get("draft", null) || { files: [], title: "", text: "", rating: null, signature: true, exclude: ["e6ai"], hashtags: null, e6tags: {}, e6meta: {} };
    return s.draft;
  }
  function saveDraft() { C().store.set("draft", S().draft); }
  function apiDraft() {
    const d = draft();
    return { files: d.files.map((f) => f.id), title: d.title, text: d.text, rating: d.rating, signature: d.signature, exclude: d.exclude, e6ai_tags: d.e6tags || {}, e6ai_meta: d.e6meta || {}, hashtags: d.hashtags == null ? null : d.hashtags, tagger: S().taggerOn !== false, extend: d.extend ? d.extend.pub_id : undefined };
  }
  const EMPTY = () => ({ files: [], title: "", text: "", rating: null, signature: true, exclude: ["e6ai"], hashtags: null, e6tags: {}, e6meta: {} });
  // досылка опубликованного поста на другие площадки: черновик этого поста открывается на экране «Новый пост»,
  // а ваш текущий черновик откладывается и возвращается после
  async function openExtend(pubId) {
    const s = S(), r = await C().api("post_extend_draft", { pub_id: pubId });
    const cur = draft();
    if (!cur.extend) C().store.set("draft_saved", cur);
    const x = r.draft || {};
    s.draft = { files: r.files, title: x.title || "", text: x.text || "", rating: x.rating, signature: x.signature !== false,
      exclude: x.exclude || [], hashtags: x.hashtags == null ? null : x.hashtags, e6tags: x.e6ai_tags || {}, e6meta: x.e6ai_meta || {},
      extend: { pub_id: r.pub_id, title: r.title, done: r.done, failed: r.failed, missing: r.missing } };
    saveDraft();
    s.pstatOpen = null; s.postMode = "edit"; s.when = "now"; s.tried = false; s.schedMsg = "";
    s.view = "post"; C().store.set("view", "post");
    await load("post"); s.postMode = "edit"; C().render();
  }
  function closeExtend() {
    const s = S();
    s.draft = C().store.get("draft_saved", null) || EMPTY();
    C().store.set("draft_saved", null); saveDraft();
  }

  // ---------------- загрузка данных ----------------
  async function load(view) {
    const s = S(), api = C().api;
    try {
      if (view === "post") {
        if (!s.pub) s.pub = await api("pub_settings");
        const st = await api("post_state");
        s.pubState = st.state; s.pubJob = st.job;
        if (st.job && st.job.state === "running") { s.postMode = "progress"; C().startPoll(); }
        await refreshPlan(); pollPrep(); loadTags(true);
      } else if (view === "history") {
        s.hist = await api("post_history");
        s.sched = await api("post_scheduled");
        try { s.pipe = await api("pipeline_state"); } catch (e) { s.pipe = null; }
        try { s.histSum = await api("pub_stats_summary"); } catch (e) { s.histSum = {}; }
        await loadCalendar();
      } else {
        s.pub = await api("pub_settings");
        try { s.tagger = await api("tagger_status"); } catch (e) { s.tagger = null; }
        try { s.e6db = await api("e6db_status"); } catch (e) { s.e6db = null; }
        try { s.mediaSt = await api("media_status"); } catch (e) { s.mediaSt = null; }
        try { s.outputs = await api("outputs_get"); } catch (e) { s.outputs = null; }
        try { s.e6learn = await api("e6_learned"); } catch (e) { s.e6learn = null; }
        if (!s.pubKnown) { try { s.pubKnown = await api("pub_tg_known"); } catch (e) { s.pubKnown = { authorized: false, chats: [] }; } }
      }
    } catch (e) { s.pubErr = Object.assign({}, s.pubErr, { top: e.message }); }
  }
  async function refreshPlan() {
    const s = S();
    try { s.plan = await C().api("post_plan", { draft: apiDraft() }); s.planErr = ""; scheduleFc(); }
    catch (e) {
      s.plan = null; s.planErr = e.message;
      if (/не найден/.test(e.message)) {   // файл удалили из папки — убираем его из черновика
        const d = draft(); d.files = []; saveDraft();
        s.planErr = "Файлы прошлого черновика не найдены — добавьте их заново.";
        s.plan = await C().api("post_plan", { draft: apiDraft() }).catch(() => null);
      }
    }
  }
  // ----- прогноз популярности для черновика -----
  let fcTimer = null, fcTries = 0;
  function scheduleFc(delay = 400) {
    clearTimeout(fcTimer);
    fcTimer = setTimeout(loadFc, delay);
  }
  async function loadFc() {
    const s = S(), d = draft();
    if (!d.files.length || !d.rating || !mod("forecast")) { s.fc = null; paintFc(); return; }   // прогноз выключен в «Модулях»
    const runAt = s.when === "later" && s.whenAt ? new Date(s.whenAt).toISOString() : "";
    try { s.fc = await C().api("post_forecast", { draft: apiDraft(), run_at: runAt }); s.fcErr = ""; }
    catch (e) { s.fc = null; s.fcErr = e.message; }
    if (s.when === "later") paintSide(); else paintFc();
    // теггер смотрит картинки — когда закончит, прогноз уточнится содержанием
    if (s.fc && s.fc.tagging && !s.fc.tags_known && fcTries++ < 40) scheduleFc(3000); else if (s.fc && s.fc.tags_known) fcTries = 0;
  }
  function paintFc() {
    const el = document.getElementById("fcbox");
    if (el) el.innerHTML = fcHtml();
  }
  const pct = (m) => (m >= 1 ? "+" : "−") + Math.abs(Math.round((m - 1) * 100)) + "%";
  function fcHtml() {
    const s = S(), f = s.fc, n = C().num;
    if (!f || !f.items || !f.items.length) return s.fcErr ? `<p class="err small">${esc(s.fcErr)}</p>` : "";
    let bestShown = false;
    return f.items.map((x) => {
      if (!x.ok) return `<div class="fc"><div class="fchead"><span>Прогноз · ${esc(x.channel || "")}</span></div><span class="muted small">${esc(x.why)}</span></div>`;
      const q = x.quality || {};
      const fac = (x.factors || []).filter((y) => Math.abs(y.mult - 1) >= 0.03);
      const up = fac.filter((y) => y.mult >= 1).slice(0, 4), down = fac.filter((y) => y.mult < 1).slice(0, 4);
      const bt = bestShown || s.when === "later" ? null : (x.best_times || []).find((b) => b.gain >= 1.05);
      if (bt) bestShown = true;   // время одно на весь пост — подсказываем по первому каналу
      const li = (y) => `<li><span data-tip="${y.kind === "tag" ? "Тег картинки" : y.kind === "time" ? "Время публикации" : "Тип поста"}">${esc(y.name.replace(/^Время: /, ""))}</span><b>${pct(y.mult)}</b></li>`;
      return `<div class="fc">
        <div class="fchead"><span>Прогноз · ${esc(x.channel)}</span>${q.level === "low" ? `<span class="pill" data-tip="${esc(q.text || "")}">слабый</span>` : q.level === "mid" ? `<span class="pill" data-tip="${esc(q.text || "")}">ориентир</span>` : ""}</div>
        <div class="fcbig">≈ ${n(x.expected)}<span>${esc(x.unit)}</span></div>
        <div class="fcrow"><span>обычно ${n(x.low)}–${n(x.high)}</span><span><b>${mult(x.index)}</b> к норме ${n(x.base)}</span><span>хит <b>${Math.round(x.p_hit * 100)}%</b></span>${x.percentile !== null ? `<span>лучше <b>${Math.round(x.percentile * 100)}%</b> постов</span>` : ""}</div>
        ${up.length || down.length ? `<div class="drivers"><div class="u"><h4>Прибавляет</h4><ul>${up.map(li).join("") || `<li><span class="muted">—</span></li>`}</ul></div>
          <div class="d"><h4>Убавляет</h4><ul>${down.map(li).join("") || `<li><span class="muted">—</span></li>`}</ul></div></div>` : ""}
        ${bt ? `<button class="besttime" data-pa="fc-when" data-at="${esc(bt.at)}">${C().ICON.clock}<span class="bt">Лучше опубликовать ${esc(fmtWhen(new Date(bt.at)))}</span><b>${pct(bt.gain)}</b></button>` : ""}
        ${x.tags_known ? "" : `<span class="muted small">${f.tagging ? "Теггер смотрит картинки — прогноз уточнится…" : "Содержание картинки не учтено: теггер не установлен."}</span>`}
      </div>`;
    }).join("");
  }
  function schedulePlan() {
    clearTimeout(planTimer);
    planTimer = setTimeout(async () => { await refreshPlan(); paintSide(); }, 250);
  }
  async function tick() {
    const s = S();
    const j = s.info && s.info.pub_job;
    if (j && (j.state === "running" || (s.pubJob && s.pubJob.state === "running"))) {
      const st = await C().api("post_state");
      const was = s.pubJob && s.pubJob.state === "running";
      s.pubState = st.state; s.pubJob = st.job;
      const dd = (st.state && st.state.dests) || [];
      if (was && st.job && st.job.state !== "running" && dd.some((d) => d.state === "error" && /api\.telegram\.org/.test(d.message || "")))
        C().toast("Публикация в Telegram не прошла: нет связи с api.telegram.org. Включите MTProto или запас прокси в «Настройки» → «Соединение»", "error");
    }
    if (s.view === "settings" && s.tagger && ((s.tagger.job && s.tagger.job.state === "running") || s.tagWaiting)) {
      s.tagger = await C().api("tagger_status");
      if (!(s.tagger.job && s.tagger.job.state === "running")) s.tagWaiting = false;
    }
    if (s.vkStatsWaiting && s.info && s.info.vk_stats_job && s.info.vk_stats_job.state !== "running") {
      s.vkStatsWaiting = false; s.pub = await C().api("pub_settings");
    }
    if (s.vkWaiting && s.info && s.info.vk_job && s.info.vk_job.state !== "running") {
      s.vkWaiting = false; s.pub = await C().api("pub_settings");
    }
  }

  // ---------------- Новый пост ----------------
  const RATING_TIP = { sfw: "Без обнажённости", erotic: "Обнажённость и откровенные позы без половых актов", explicit: "Всё, что дальше" };
  const pfor = (list) => {   // значки площадок; отключённые модули не показываем
    const m = (S().pub && S().pub.modules) || {};
    const l = list.filter((p) => m[p] !== false);
    return l.length ? `<span class="pfor">${l.map((p) => C().pbadge(p, { sm: true })).join("")}</span>` : "";
  };
  const fmtDur = (s) => { s = Math.round(s); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; };
  function galleryHtml() {
    const s = S(), d = draft();
    const prep = s.prep || {};
    const tile = (f, i) => {
      const ar = f.width && f.height ? `aspect-ratio:${f.width}/${f.height};` : "width:160px;";
      const ready = f.kind === "image" && prep[f.id] === "ready";
      return `<figure class="gt" draggable="true" data-gi="${i}" style="${ar}" data-tip="${esc(f.name)}${f.width ? "\n" + f.width + "×" + f.height : ""} · ${mb(f.size)}${d.files.length > 1 ? "\nПеретащите, чтобы поменять порядок" : ""}">
        ${f.kind === "video" ? `<img src="/uthumb/${f.id}?t=${tok()}" alt="${esc(f.name)}" draggable="false" onerror="this.replaceWith(Object.assign(document.createElement('div'),{className:'vid',textContent:'${esc(f.format || "MP4")}'}))">` : `<img src="/uthumb/${f.id}?t=${tok()}" alt="${esc(f.name)}" draggable="false">`}
        ${f.kind === "video" ? `<span class="play" aria-hidden="true">▶</span>` : ""}
        ${f.kind === "video" || f.kind === "gif" ? `<button class="gifbtn" data-pa="ved-open" data-i="${i}" data-tip="Обрезать, повернуть, уменьшить, сделать GIF из видео или видео из GIF">Изменить</button>` : ""}
        <button class="x" data-pa="rmfile" data-i="${i}" aria-label="Убрать ${esc(f.name)}">×</button>
        <span class="gm">${f.kind === "gif" ? "<span>GIF</span>" : f.kind === "video" ? `<span>видео${f.duration ? " · " + fmtDur(f.duration) : ""}</span>` : ""}
          ${f.kind === "image" ? `<span class="${ready ? "ready" : ""}" data-prep="${f.id}">${ready ? "✓ превью" : "готовлю…"}</span>${preBadge(f.id)}` : ""}</span></figure>`;
    };
    return `<div class="gallery">${d.files.map(tile).join("")}
      ${d.files.length < 10 ? `<label class="addtile" id="drop" tabindex="0"><input type="file" id="fileinp" multiple accept="${ACCEPT}" hidden><b>+</b>Добавить<span>${d.files.length} из 10</span></label>` : ""}</div>`;
  }
  function renderPost() {
    const s = S();
    if (s.postMode === "progress" && s.pubState) { C().$main.innerHTML = progressHtml(); return; }
    const d = draft(), p = s.pub;
    const sigOn = p ? TEXTP.filter((k) => {
      const v = (p.signatures.platforms || {})[k] || {};
      return p.signatures.use[k] !== false && (d.rating && d.rating !== "sfw" ? v.nsfw : v.sfw);
    }) : [];
    const titleFor = p ? [["telegram", p.telegram.include_title], ["discord", p.discord.include_title], ["vk", p.vk.include_title]].filter(([, on]) => on).map(([k]) => k) : [];
    const tagsOn = p && ((p.e6ai && p.e6ai.enabled) || (p.hashtags && p.hashtags.enabled));
    const X = d.extend;
    const head = (X ? `<div class="toolbar"><h1>Дослать пост</h1><span class="spacer"></span><button class="btn ghost" data-pa="extend-cancel">${C().ICON.close} Отменить досылку</button></div>
      <div class="card extbanner"><b>${esc(X.title || "Пост без названия")}</b>
        <span class="muted small">Уже вышел: ${esc((X.done || []).join(", ") || "никуда")}${(X.failed || []).length ? ` · с ошибкой: ${esc(X.failed.join(", "))}` : ""}</span>
        <span class="small">Включите справа, куда дослать. В истории и календаре это останется одним постом — у него просто добавятся площадки.
          Текст и теги можно поправить: изменения уйдут только на новые площадки.</span>
        ${(X.missing || []).length ? `<span class="warnline small">Оригиналы уже удалены (хранятся неделю после публикации): ${esc(X.missing.join(", "))}. Добавьте эти файлы заново.</span>` : ""}</div>`
      : `<div class="toolbar"><h1>Новый пост</h1><span class="spacer"></span>${d.files.length || d.text || d.title ? `<button class="btn ghost" data-pa="clear">${C().ICON.trash} Очистить</button>` : ""}</div>`);
    const h = `${head}
      ${s.pubErr && s.pubErr.top ? `<p class="err">${esc(s.pubErr.top)}</p>` : ""}
      <div class="postgrid"><div class="postmain">
        <section class="card"><div class="cardhead"><h2>Файлы</h2>
            ${d.files.some((f) => f.kind !== "image") ? `<span class="optline">GIF и видео уходят как есть ${C().hint("Переделываются только там, где площадка иначе не примет: ужать под лимит Discord или Telegram, перекодировать в H.264 или в WebM для " + BN() + ". Подробности — «Настройки» → «Видео и GIF».")}</span>` :
            `<span class="optline">JPEG-превью для соцсетей, PNG-оригинал в Telegram и на ${BN()} ${C().hint("Оригиналы не меняются. Для постов делается JPEG-превью по рецепту из «Настроек» → «JPEG-превью»; он готовится сразу, пока вы пишете текст. PNG-оригиналы уходят файлами в Telegram и на e6ai.")}</span>`}</div>
          ${d.files.length ? galleryHtml() : `<label class="drop" id="drop" tabindex="0">
            <input type="file" id="fileinp" multiple accept="${ACCEPT}" hidden>
            <b>Перетащите картинки, GIF или MP4</b>
            <span class="muted small">или нажмите, чтобы выбрать. Ctrl+V вставит из буфера. До 10 файлов.</span></label>`}
          ${s.uploading ? `<p class="muted small">Загружаю ${s.uploading} ${C().plural(s.uploading, "файл", "файла", "файлов")}…</p>` : ""}
          ${s.uploadErr ? `<p class="err small">${esc(s.uploadErr)}</p>` : ""}
          <div class="cardhead" style="padding-top:4px"><div class="rseg ${s.tried && !d.rating ? "need" : ""}" role="radiogroup" aria-label="Рейтинг">${RATINGS.map(([k, l]) =>
            `<button role="radio" aria-checked="${d.rating === k}" class="${d.rating === k ? "on" : ""}" data-pa="rating" data-val="${k}" ${X && d.rating !== k ? "disabled" : ""} data-tip="${RATING_TIP[k]}"><span class="dot ${k}"></span>${l}</button>`).join("")}</div>
            ${d.rating ? "" : `<span class="${s.tried ? "err" : "muted"} small">Выберите рейтинг — от него зависит, куда уйдёт пост ${C().hint("18+ работы уходят только туда, где разрешено 18+, и под спойлер. ВК — только SFW.")}</span>`}</div>
        </section>
        <section class="card"><h2>Текст</h2>
          <div class="field"><label for="ptitle">Заголовок ${titleFor.length ? pfor(titleFor) : ""} ${C().hint("Необязательно. Жирным в начале поста на отмеченных площадках.")}</label>
            <input id="ptitle" data-dfield="title" value="${esc(d.title)}" placeholder="Закат над пустыней"></div>
          <div class="field"><label for="ptext">Описание</label>
            <textarea id="ptext" data-dfield="text" rows="5" placeholder="Пара слов о работе">${esc(d.text)}</textarea>
            <div id="pcount">${countHtml()}</div></div>
          <label class="tgl"><input type="checkbox" data-dfield="signature" ${d.signature ? "checked" : ""}>
            <span>Подпись со ссылками ${sigOn.length ? pfor(sigOn) : `<a href="#" data-view-link="platforms" data-tab="signatures">задать</a>`}</span></label>
        </section>
        ${tagsOn ? `<section class="card" id="tagcard"><div class="cardhead"><h2>Теги</h2></div>
          <div id="htbox" class="tagsec">${htHtml()}</div>${e6Html()}</section>` : ""}
      </div>
      <aside class="postside card" id="pubside">${sideHtml()}</aside></div>`;
    C().$main.innerHTML = h;
    if (s.viewFile) C().$main.insertAdjacentHTML("beforeend", viewHtml());
  }
  // ----- правка видео и GIF: само окно — в videditor.js; здесь — подмена файла в посте после правки -----
  async function vedDone(st, job) {
    const s = S(), d = draft(), nf = st.file, r = st.result || {};
    const i = d.files.findIndex((x) => x.id === job.src);
    if (i < 0) { C().api("post_remove_file", { file_id: nf.id }).catch(() => {}); return; }   // файл из поста уже убрали
    d.files.splice(i, 1, nf);
    if (d.e6tags && d.e6tags[job.src]) { d.e6tags[nf.id] = d.e6tags[job.src]; delete d.e6tags[job.src]; }
    if (d.e6meta && d.e6meta[job.src]) { d.e6meta[nf.id] = d.e6meta[job.src]; delete d.e6meta[job.src]; }
    C().api("post_remove_file", { file_id: job.src }).catch(() => {});
    saveDraft(); await refreshPlan();
    C().toast(`${nf.kind === "gif" ? "GIF" : "Видео"} готов${nf.kind === "gif" ? "" : "о"}: ${mb(nf.size)}, ${nf.width}×${nf.height}` + (r.over ? ". Тяжелее заказанного лимита — возьмите отрезок короче" : ""), r.over ? "info" : "ok");
    if (s.view === "post") C().render();
  }
  // ----- хэштеги для соцсетей -----
  function htHtml() {
    const s = S(), d = draft(), p = s.plan;
    if (!p || !p.hashtags_on) return s.pub && s.pub.hashtags && s.pub.hashtags.enabled ? `<span class="muted small">Хэштеги появятся, когда выберете рейтинг.</span>` : "";
    const cur = p.hashtags || [], sug = p.hashtags_suggested || [];
    const all = [...new Set([...cur, ...sug])];
    return `<div class="cardhead"><h3 style="display:flex;align-items:center;gap:8px">Хэштеги для соцсетей ${pfor(TEXTP.filter((k) => (s.pub.hashtags.use || {})[k] !== false))}
        ${C().hint("Для поиска работ в Telegram, Discord и ВК. Собираются из рейтинга, персонажей и видов из тегов " + BN() + " и подсказок теггера, плюс постоянные из «Настроек». Нажмите на хэштег, чтобы убрать или вернуть.")}</h3>
        ${d.hashtags == null ? `<span class="muted small">собраны из тегов</span>` : `<button class="btn small" data-pa="ht-auto">${C().ICON.refresh} Собрать из тегов заново</button>`}</div>
      <div class="chips">${all.map((t) => `<button class="chip ht ${cur.includes(t) ? "on" : ""}" data-pa="ht" data-tag="${esc(t)}">#${esc(t)}</button>`).join("")}
        <input class="taginput" data-htadd="1" placeholder="добавить хэштег" aria-label="Добавить хэштег" style="flex:0 1 180px"></div>`;
  }
  function paintHt() {
    const el = document.getElementById("htbox");
    if (el) el.innerHTML = htHtml();
  }

  // ----- теги e6ai: компактная форма загрузки, как на самом e6ai -----
  const E6ROWS = [
    ["director", "Директор", "директор", "Ваш тег директора (на e621 — художника) — из постоянных тегов в «Настройках»."],
    ["character", "Персонаж", "персонаж", "Тег персонажа: начните вводить имя. Свой OC без тега на сайте — отметьте «Без тега персонажа». Франшизу сайт добавит сам (бледные)."],
    ["sex", "Пол", "пол", "Пол каждого персонажа на картинке."],
    ["count", "Сколько", "сколько", "Сколько персонажей на картинке — один вариант."],
    ["body", "Тип тела", "тип тела", "Anthro — прямоходящий зверь, Feral — как животное, Humanoid — человек с чертами зверя, Taur — тело кентавра."],
    ["species", "Вид", "вид", "Вид персонажа: fox, wolf, dragon… Общие виды (canine, mammal) сайт добавит сам — бледные."],
    ["cont", "Спорное", "спорное", "Есть ли на картинке что-то из этого. Если нет — отметьте «ничего из этого»."],
  ];
  const SEX_L = { male: "Male", female: "Female", andromorph: "Andromorph", gynomorph: "Gynomorph", herm: "Herm", maleherm: "Male-Herm", ambiguous_gender: "Ambiguous" };
  const FIX_L = { solo: "Solo", duo: "Duo", trio: "Trio", group: "Group", zero_pictured: "Zero Pictured", anthro: "Anthro", feral: "Feral", humanoid: "Humanoid", human: "Human", taur: "Taur", my_little_pony: "My Little Pony", not_furry: "Not furry", ...SEX_L };
  const tname = (t) => FIX_L[t] || t.replace(/_/g, " ");
  // по умолчанию — свой OC без тега персонажа и ничего спорного: так чаще всего (снимается одним кликом)
  const e6meta = (id) => { const d = draft(); d.e6meta = d.e6meta || {}; return d.e6meta[id] || (d.e6meta[id] = { nochar: true, nocont: true }); };

  function e6chip(fid, row, o, fixed) {
    const cat = (S().e6cats || {})[o.t] || (fixed ? "general" : "general");
    const cls = ["chip", `c-${cat}`, o.on ? "on" : "", !o.on && o.p != null ? "doubt" : "", o.imp ? "imp" : "", o.lock ? "lock" : "", fixed && !o.on && o.p == null ? "opt" : "",
      o.rare != null && !o.on ? "rare" : "", o.mrem > o.madd && !o.on ? "mremd" : ""].join(" ");
    let tip = o.lock ? "Постоянный тег из «Настроек» → " + BN() : o.imp ? BN() + " добавит сам — отправлять не нужно" :
      (o.p != null ? `Теггер: ${Math.round(o.p * 100)}%` + (o.on ? "" : " — нажмите, чтобы добавить") : o.on ? "Нажмите, чтобы убрать" : "Нажмите, чтобы отметить");
    if (o.rare != null) tip += `\nРедкий тег: ${o.rare} ${C().plural(o.rare, "пост", "поста", "постов")} на ${BN()} — сам не отмечается`;
    if (o.mrem) tip += `\nМодераторы убирали этот тег: ${o.mrem} раз`;
    if (o.madd) tip += `\nМодераторы добавляли этот тег: ${o.madd} раз`;
    if (o.lock || o.imp) return `<span class="${cls}" data-tip="${esc(tip)}">${esc(tname(o.t))}</span>`;
    return `<button class="${cls}" data-pa="e6t" data-id="${fid}" data-row="${row}" data-tag="${esc(o.t)}" data-tip="${esc(tip)}">${esc(tname(o.t))}${!o.on && o.p != null ? `<i>${Math.round(o.p * 100)}</i>` : ""}</button>`;
  }
  function e6input(fid, cat, ph) {
    return `<input class="taginput" data-tagadd="${fid}" data-ac="${cat}" placeholder="${ph}" aria-label="${ph}" autocomplete="off" spellcheck="false">`;
  }
  function e6FormHtml(f) {
    const s = S(), d = draft(), F = (s.e6form || {})[f.id], m = e6meta(f.id);
    const isArtist = BN() === "e621";   // на e621 строка «Директор» — это «Художник»
    if (!F) return `<p class="muted small">Готовлю форму…</p>`;
    const R = F.rows, c = F.check, need = new Set(c.need);
    const lim = (lst, n) => { const on = lst.filter((o) => o.on || o.imp || o.lock); const sug = lst.filter((o) => !(o.on || o.imp || o.lock)).slice(0, n); return [...on, ...sug]; };
    const row = ([k, label, nk, tip], body) => {
      if (k === "director" && isArtist) { label = "Художник"; nk = "художник"; }
      return `<div class="frow ${need.has(nk) ? "need" : ""}"><span class="fl" data-tip="${esc(tip)}">${label}</span><div class="chips">${body}</div></div>`;
    };
    const R0 = Object.fromEntries(E6ROWS.map((r) => [r[0], r]));
    let h = "";
    h += row(R0.director, R.director.length ? R.director.map((o) => e6chip(f.id, "director", o)).join("") :
      `<span class="small muted">нет тега ${isArtist ? "художника" : "директора"} — <a href="#" data-view-link="platforms" data-tab="e6ai">добавить в постоянные</a></span>`);
    h += row(R0.character, lim(R.character, 4).map((o) => e6chip(f.id, "character", o)).join("") + e6input(f.id, "character", "имя персонажа") +
      `<label class="mini"><input type="checkbox" data-pa="e6meta" data-k="nochar" data-id="${f.id}" ${m.nochar ? "checked" : ""}>Без тега персонажа</label>`);
    h += row(R0.sex, R.sex.map((o) => e6chip(f.id, "sex", o, true)).join(""));
    h += row(R0.count, R.count.map((o) => e6chip(f.id, "count", o, true)).join(""));
    h += row(R0.body, R.body.map((o) => e6chip(f.id, "body", o, true)).join(""));
    h += row(R0.species, lim(R.species, 5).map((o) => e6chip(f.id, "species", o)).join("") + e6input(f.id, "species", "вид: fox, wolf…"));
    h += row(R0.cont, R.cont.map((o) => e6chip(f.id, "cont", o, true)).join("") +
      `<label class="mini"><input type="checkbox" data-pa="e6meta" data-k="nocont" data-id="${f.id}" ${m.nocont ? "checked" : ""}>ничего из этого</label>`);
    const rl = { sfw: ["s", "Safe"], erotic: ["q", "Questionable"], explicit: ["e", "Explicit"] }[d.rating];
    h += `<div class="frow"><span class="fl" data-tip="Ставится по рейтингу поста">Рейтинг</span><div class="chips">${rl ? `<span class="rtag r-${rl[0]}">${rl[0]} · ${rl[1]}</span>` : `<span class="small muted">выберите рейтинг поста вверху</span>`}</div></div>`;
    const rest = R.rest, restOn = rest.filter((o) => o.on || o.lock || o.imp), restSug = rest.filter((o) => !(o.on || o.lock || o.imp));
    const open = (s.e6open || {})[f.id];
    h += `<details class="frest" data-fid="${f.id}" ${open ? "open" : ""}><summary><span class="fl">Остальное</span><span class="small muted">${restOn.length} ${C().plural(restOn.length, "тег", "тега", "тегов")}${restSug.length ? ` · ${restSug.length} ${C().plural(restSug.length, "подсказка", "подсказки", "подсказок")}` : ""} — фон, поза, одежда, выражение</span></summary>
      <div class="chips">${restOn.map((o) => e6chip(f.id, "rest", o)).join("")}${e6input(f.id, "", "добавить: smile outside…")}</div>
      ${restSug.length ? `<div class="chips sug">${restSug.map((o) => e6chip(f.id, "rest", o)).join("")}</div>` : ""}</details>`;
    return h + e6CheckHtml(f, F);
  }
  function e6CheckHtml(f, F) {
    const c = F.check, tags = (l) => l.length ? ` <span class="tl">${l.map((t) => esc(t.replace(/_/g, " "))).join(", ")}</span>` : "";
    let h = `<div class="e6check">`;
    const li = [];
    if (c.need.length) li.push(`<li class="bad">Не заполнено: <b>${esc(c.need.join(", "))}</b></li>`);
    c.errors.forEach((x) => li.push(`<li class="bad">${esc(x.text)}${tags(x.tags)}</li>`));
    c.warnings.forEach((x) => li.push(`<li class="warn">${esc(x.text)}${tags(x.tags)}</li>`));
    if (Object.keys(c.renamed || {}).length) li.push(`<li class="info">Переименовано по правилам ${BN()}: ${Object.entries(c.renamed).map(([a, b]) => esc(a) + " → " + esc(b)).join(", ")}</li>`);
    if ((c.trimmed || []).length) li.push(`<li class="info">Убрано лишнее — ${BN()} добавит само: ${c.trimmed.map(esc).join(", ")}</li>`);
    if (li.length) h += `<ul>${li.join("")}</ul>`;
    const blocked = c.need.length || c.errors.length;
    const imgs = draft().files;
    const copy = imgs.length > 1 && imgs[0].id === f.id ? `<button class="btn small ghost" data-pa="e6copy" data-id="${f.id}" data-tip="Персонаж, пол, вид и остальные теги этой картинки — на все остальные картинки поста. Подтвердить нужно будет каждую.">Теги → на остальные</button>` : "";
    h += `<div class="e6sum"><span>Итого <b>${c.total}</b> ${C().plural(c.total, "тег", "тега", "тегов")}${c.implied.length ? ` <span class="muted" data-tip="${esc(c.implied.join(", "))}">(${c.implied.length} добавит сайт)</span>` : ""}</span>
      <span class="e6act">${copy}${c.confirmed && !blocked ? `<button class="btn small okd" data-pa="e6ok" data-id="${f.id}" data-un="1" data-tip="Нажмите, чтобы снять отметку">✓ Проверено</button>` :
        `<button class="btn small ${blocked ? "" : "primary"}" data-pa="e6ok" data-id="${f.id}" ${blocked ? `disabled data-tip="Сначала заполните отмеченное красным"` : `data-tip="Подтвердите, что теги верные — без этого ${BN()} не загрузим"`}>Всё верно</button>`}</span></div>`;
    return h + `</div>`;
  }
  function e6DbHtml() {
    const s = S(), st = s.e6db;
    if (!st) return "";
    const j = st.job;
    if (j && j.state === "running") return `<div class="e6db"><span class="spin"></span><span class="small">${esc(j.message || "Скачиваю базу тегов " + BN() + "…")}</span></div>`;
    if (st.ready) return "";
    return `<div class="e6db"><span class="small">База тегов ${BN()} не скачана: без неё нет подсказок при вводе, и не проверить, существует ли тег.${j && j.state === "error" ? ` <span class="err">${esc(j.error || "")}</span>` : ""}</span>
      <button class="btn small primary" data-pa="e6db-get">Скачать базу</button></div>`;
  }
  function e6on() {   // e6ai настроен и не выключен для этого поста (по умолчанию выключен)
    const s = S(), e6 = s.pub && s.pub.e6ai;
    return !!(e6 && e6.enabled && !(draft().exclude || []).includes("e6ai"));
  }
  function e6Html() {
    const s = S(), d = draft();
    if (!e6on()) return "";
    const imgs = d.files;
    const tg = s.tagger || {};
    const runJob = tg.run_job, running = runJob && runJob.state === "running";
    let h = `<div class="tagsec" id="e6card"><div class="cardhead"><h3 style="display:flex;align-items:center;gap:8px">Теги ${BN()} ${C().pbadge("e6ai", { sm: true })}
        ${C().hint("Как форма загрузки на e6ai. Сплошные — уйдут на " + BN() + ", пунктир — подсказка теггера (число — уверенность): нажмите, чтобы добавить. Бледные " + BN() + " добавит сам. Мелкие детали (пирсинг, украшения, татуировки) теггер путает — их отмечайте сами. Без заполненных строк и «Всё верно» " + BN() + " не загружаем.")}</h3>
        <span class="muted small">${!mod("tagger") ? `теггер выключен — теги вписываются вручную (<a href="#" data-view-link="settings" data-tab="features">Функции</a>)` : tg.ready ? "теггер JTP PILOT²" : `теггер не установлен — <a href="#" data-view-link="platforms" data-tab="tags">установить</a>`}</span></div>`;
    if (s.tagCpu) h += `<div class="callout warn"><p>Теггер не видит видеокарту и подбирал бы теги на процессоре — это медленно и тормозит компьютер, поэтому теги пока не подбираются. <a href="#" data-view-link="settings" data-tab="tags">Как исправить</a></p></div>`;
    h += e6DbHtml();
    if (running) h += `<p class="muted small">${esc(runJob.message || "Подбираю теги…")}</p>`;
    if (!imgs.length) return h + `<p class="muted small">Добавьте картинки — теги для каждой свои: на ${BN()} каждая картинка — отдельный пост.</p></div>`;
    h += imgs.map((f) => {
      const preds = ((s.e6preds || {})[f.id]);
      return `<div class="e6row" data-fid="${f.id}"><img src="/uthumb/${f.id}?t=${tok()}" alt="" onerror="this.replaceWith(Object.assign(document.createElement('div'),{className:'vid sm',textContent:'${f.kind === "video" ? "видео" : "GIF"}'}))">
        <div class="e6form">${e6FormHtml(f)}
          ${preds == null && tg.ready && mod("tagger") ? `<p class="muted small">${running ? "теггер смотрит картинку…" : `<a href="#" data-pa="e6run">Подобрать теги теггером</a>`}</p>` : ""}</div></div>`;
    }).join("");
    return h + `</div>`;
  }
  function e6dbCard() {
    const st = S().e6db || {}, j = st.job, run = j && j.state === "running";
    const when = st.updated ? new Date(st.updated * 1000).toLocaleDateString("ru-RU", { day: "numeric", month: "long" }) : "";
    return `<section class="card setc"><div class="head"><h2>База тегов ${BN()} ${C().hint("Все теги " + BN() + " с категориями, переименования и связи (персонаж → франшиза). По ней: подсказки при вводе, перевод тегов теггера в теги " + BN() + ", проверка, что тег существует. Около 10 МБ, скачивается с e6ai.net. Обновляется сама вместе с трендами e6ai.")}</h2>
        ${st.ready ? `<span class="badge ok">✓ ${C().num(st.tags)} тегов</span>` : `<span class="badge off">не скачана</span>`}</div>
      ${st.ready ? `<p class="muted small">Обновлена ${esc(when)}${st.day ? " · выгрузка за " + esc(st.day) : ""} · ${C().num(st.aliases)} переименований · ${C().num(st.implications)} связей</p>` : ""}
      ${run ? `<div class="callout"><p>${esc(j.message || "Скачиваю…")}</p><div class="progress thin" style="flex:1 1 100%"><div style="width:${Math.round((j.progress || 0) * 100)}%"></div></div></div>` : ""}
      ${j && j.state === "error" ? `<p class="err small">${esc(j.error || "")}</p>` : ""}
      <div class="cardfoot" style="border:0;padding:0"><button class="btn ${st.ready ? "" : "primary"}" data-pa="e6db-get" ${run ? "disabled" : ""}>${st.ready ? "Обновить базу тегов" : "Скачать базу тегов"}</button></div></section>`;
  }
  async function ensureCats(tags) {   // категории для хэштегов и прочих мест
    const s = S(); s.tagCat = s.tagCat || {};
    const miss = [...new Set(tags)].filter((t) => !(t in s.tagCat));
    if (!miss.length) return false;
    try { Object.assign(s.tagCat, await C().api("tag_cats", { tags: miss })); } catch (e) { return false; }
    return true;
  }
  async function refreshE6() {
    const s = S(), d = draft();
    const imgs = d.files;
    if (!imgs.length || !e6on()) { s.e6form = {}; return; }
    d.e6tags = d.e6tags || {}; d.e6meta = d.e6meta || {};
    try {
      const res = await C().api("e6_form", { items: imgs.map((f) => ({ id: f.id, tags: d.e6tags[f.id] || [], meta: e6meta(f.id) })), rating: d.rating || "" });
      s.e6form = res; s.e6cats = s.e6cats || {};
      let ch = false;
      for (const [id, F] of Object.entries(res)) {
        Object.assign(s.e6cats, F.cats);
        if ((Object.keys(F.check.renamed || {}).length || (F.check.trimmed || []).length) && JSON.stringify(F.tags) !== JSON.stringify(d.e6tags[id] || [])) { d.e6tags[id] = F.tags; ch = true; }
      }
      if (ch) saveDraft();
    } catch (e) { s.e6form = {}; }
  }
  async function loadTags(run, force) {
    const s = S(), d = draft(), api = C().api;
    const e6 = e6on();
    // теггер смотрит картинки, пока он включён (переключатель справа вверху); к посту теги привязываются, только если включён e6ai
    if (!mod("tagger") || !force && !(s.taggerOn !== false && s.pub && ((s.pub.e6ai && s.pub.e6ai.enabled) || (s.pub.hashtags && s.pub.hashtags.enabled)))) {
      if (e6) { await refreshE6(); if (s.view === "post" && s.postMode !== "progress") paintE6(); }   // теггер выключен — форма e6ai всё равно нужна
      return;
    }
    const ids = d.files.map((f) => f.id);
    if (!ids.length) return;
    try { s.tagger = await api("tagger_status"); } catch (e) { return; }
    if (e6 && !s.e6db) { try { s.e6db = await api("e6db_status"); } catch (e) { /* без базы тоже работаем */ } }
    const res = await api("post_tags", { ids });
    s.e6preds = Object.assign({}, s.e6preds, res);
    d.e6tags = d.e6tags || {};
    const fresh = ids.filter((id) => res[id] && d.e6tags[id] === undefined && e6);
    if (fresh.length) {   // первый раз — отмечаем только уверенное, без мелких деталей
      const init = await api("e6_initial", { ids: fresh });
      fresh.forEach((id) => { if (init[id]) d.e6tags[id] = init[id]; });
      saveDraft();
    }
    await refreshE6();
    if (fresh.length || ids.some((id) => res[id])) await refreshPlan();
    const missing = ids.filter((id) => !res[id]);
    if (missing.length && s.tagger.ready) {
      if (run) {
        try {
          const r = await api("tagger_run", { ids: missing });
          if (r && r.cpu) {   // видеокарта не используется — без согласия теги не подбираем
            if (!s.tagCpu) C().toast("Теггер не видит видеокарту — теги не подбираются. Подробности: «Настройки» → «Теги»", "error");
            s.tagCpu = true; if (s.view === "post") paintE6(); return;
          }
          s.tagCpu = false;
        } catch (e) { /* покажем в карточке */ }
      }
      clearTimeout(s.tagTimer);
      s.tagTimer = setTimeout(() => loadTags(true), 1500);
    }
    if (s.view === "post" && s.postMode !== "progress") paintE6();
  }
  async function e6changed(force) {
    saveDraft(); await refreshE6(); await refreshPlan(); paintE6(force);
  }
  function paintE6(force) {
    const el = document.getElementById("e6card");
    if (!el) return;
    const ae = document.activeElement;
    if (!force && ae && ae.classList && ae.classList.contains("taginput") && el.contains(ae)) { paintSide(); return; }   // не мешаем печатать
    // открытые «Остальное» не схлопываем
    S().e6open = Object.fromEntries([...el.querySelectorAll("details.frest")].map((x) => [x.dataset.fid, x.open]));
    el.outerHTML = e6Html();
    paintSide();
  }
  function e6toggle(id, row, tag, on) {
    const d = draft(); d.e6tags = d.e6tags || {};
    const list = new Set(d.e6tags[id] || []);
    const add = on == null ? !list.has(tag) : on;
    if (add) {
      if (row === "count") (S().e6form[id] ? S().e6form[id].rows.count : []).forEach((o) => list.delete(o.t));   // один вариант
      list.add(tag);
      if (row === "character") e6meta(id).nochar = false;
      if (row === "cont") e6meta(id).nocont = false;
    } else list.delete(tag);
    d.e6tags[id] = [...list];
  }

  // ----- подсказки при вводе тега (база e6ai) -----
  let acTimer = null, acSeq = 0;
  const acEl = () => document.getElementById("acl") || Object.assign(document.body.appendChild(document.createElement("div")), { id: "acl", className: "acl", hidden: true });
  function acClose() { const el = document.getElementById("acl"); if (el) { el.hidden = true; el.innerHTML = ""; } S().ac = null; }
  async function acQuery(inp) {
    const q = inp.value.trim().split(/[\s,]+/).pop();
    if (!q || q.length < 2) { acClose(); return; }
    const seq = ++acSeq;
    let res = [];
    try { res = await C().api("tag_search", { q, cat: inp.dataset.ac || "", limit: 10 }); } catch (e) { return; }
    if (seq !== acSeq || document.activeElement !== inp) return;
    if (!res.length) { acClose(); return; }
    const el = acEl(), r = inp.getBoundingClientRect();
    S().ac = { inp, res, i: 0 };
    el.style.left = Math.min(r.left, innerWidth - 300) + "px"; el.style.top = r.bottom + 4 + "px"; el.style.minWidth = Math.max(r.width, 240) + "px";
    acPaint(); el.hidden = false;
  }
  function acPaint() {
    const a = S().ac, el = acEl();
    if (!a) return;
    el.innerHTML = a.res.map((x, i) => `<button class="aci ${i === a.i ? "on" : ""}" data-aci="${i}" tabindex="-1"><span class="c-${x.cat}">${esc(x.name.replace(/_/g, " "))}</span>${x.via ? `<em>← ${esc(x.via.replace(/_/g, " "))}</em>` : ""}<small>${C().num(x.n)}</small></button>`).join("");
  }
  async function acPick(inp, name) {
    const id = inp.dataset.tagadd, words = inp.value.trim().split(/[\s,]+/);
    words.pop();
    const row = inp.dataset.ac === "character" ? "character" : inp.dataset.ac === "species" ? "species" : "rest";
    [...words.filter(Boolean).map((t) => t.toLowerCase()), name].forEach((t) => e6toggle(id, row, t, true));
    inp.value = ""; acClose();
    await e6changed(true);
    const f2 = document.querySelector(`#e6card input[data-tagadd="${id}"][data-ac="${inp.dataset.ac}"]`);
    if (f2) f2.focus();
  }
  document.addEventListener("mousedown", (e) => {
    const b = e.target.closest && e.target.closest(".aci");
    if (b && S().ac) { e.preventDefault(); acPick(S().ac.inp, S().ac.res[+b.dataset.aci].name); return; }
    if (!(e.target.closest && e.target.closest("#acl"))) acClose();
  });
  document.addEventListener("input", (e) => {
    if (!(e.target.dataset && e.target.dataset.tagadd)) return;
    clearTimeout(acTimer); acTimer = setTimeout(() => acQuery(e.target), 120);
  });

  // ----- длина текста на каждой площадке -----
  const LIMITS = { telegram: [1024, "подпись к фото"], discord: [2000, "сообщение"], vk: [16384, "запись"], bluesky: [300, "пост — длиннее обрежется"] };
  function countHtml() {
    const s = S(), d = draft(), p = s.plan;
    const will = new Set(p ? p.dests.filter((x) => x.will).map((x) => x.platform) : ["telegram"]);
    const plats = Object.keys(LIMITS).filter((k) => will.has(k));
    if (!plats.length) plats.push("telegram");
    let warn = "";
    const bars = plats.map((k) => {
      const t = p && p.texts ? p.texts[k] : d.text;
      const n = (d.title ? d.title.length + 1 : 0) + (t || "").length, [lim] = LIMITS[k];
      const frac = Math.min(n / lim, 1), cls = n > lim ? "over" : frac > 0.85 ? "near" : "";
      if (k === "telegram" && n > lim) warn = "В Telegram текст уйдёт отдельным сообщением перед артом.";
      return `<span class="lim ${cls}" data-tip="${C().PLAT[k][2]}: ${n} из ${lim} символов (${LIMITS[k][1]}) — с хэштегами и подписью">${C().pbadge(k, { sm: true })}<span class="lb"><i style="width:${(frac * 100).toFixed(0)}%"></i></span>${n}</span>`;
    }).join("");
    return `<div class="limits">${bars}${warn ? `<span class="err">${warn}</span>` : ""}</div>`;
  }

  // ----- правая панель: куда, прогноз, предпросмотр, кнопка -----
  function sideHtml() {
    const s = S(), p = s.plan, d = draft();
    if (!s.pub) return `<p class="muted">Загрузка…</p>`;
    if (!p) return `<h2>Публикация</h2>${s.planErr ? `<p class="err">${esc(s.planErr)}</p>` : `<p class="muted">Считаю…</p>`}`;
    const isNeed = (x) => !x.will && !x.off && x.need && x.need.length;
    const will = p.dests.filter((x) => x.will), off = p.dests.filter((x) => x.off), skip = p.dests.filter((x) => !x.will && !x.off && !isNeed(x) && !x.done);
    const doneL = p.dests.filter((x) => x.done);
    const needs = p.dests.filter(isNeed);
    const row = (x) => `<label class="drow ${x.will ? "" : isNeed(x) ? "needrow" : "dim"}">${C().pbadge(x.platform, { cls: x.will || isNeed(x) ? "" : "off" })}
        <span class="dn"><b>${esc(/^e621 · /.test(x.name) ? x.name : x.name.replace(/^[^·]+· /, ""))}</b><span>${x.will ? esc(x.details.join(", ")) : esc(x.reason)}${isNeed(x) ? ` · <a href="#" data-pa="e6need">к тегам</a>` : ""}</span></span>
        <span class="tgl"><input type="checkbox" ${x.will || isNeed(x) ? "checked" : ""} ${x.can_toggle || x.off ? "" : "disabled"} data-pa="toggle" data-key="${esc(x.key)}" aria-label="${esc(x.name)}"></span></label>`;
    // пока пост только собирается, «выберите рейтинг» и «добавьте картинки» — не ошибки, а подсказки:
    // красным они становятся, только если нажать «Опубликовать» раньше времени
    const soft = (e) => /рейтинг|Добавьте/.test(e);
    const e6wait = (e) => /^(e6ai|e621): теги не готовы/.test(e);   // это уже видно в строке e6ai выше
    const errs = (p.errors || []).filter((e) => (!soft(e) || s.tried) && !e6wait(e));
    let h = `<div class="side-sec"><div class="head"><h2>Публикация</h2>${will.length ? `<span class="muted small">${will.length} ${C().plural(will.length, "место", "места", "мест")}</span>` : ""}</div>`;
    if (!p.dests.length && !p.not_configured.length) h += `<p class="muted">Площадки ещё не настроены.</p>`;
    if (!d.rating) h += `<p class="muted small">Выберите рейтинг — здесь появится список мест.</p>`;
    else {
      h += `<div class="dlist">${will.map(row).join("")}${needs.map(row).join("")}${off.map(row).join("")}</div>`;
      if (doneL.length) h += `<div class="dlist donel"><span class="muted small">Уже опубликовано</span>${doneL.map((x) => `<div class="drow dim">${C().pbadge(x.platform, { cls: "" })}
        <span class="dn"><b>${esc(x.name.replace(/^[^·]+· /, ""))}</b><span>✓ ${esc(x.reason)}</span></span></div>`).join("")}</div>`;
      if (skip.length) h += `<details class="more"><summary>Не уйдёт: ${skip.length}</summary><div class="dlist">${skip.map(row).join("")}</div></details>`;
    }
    if (p.not_configured.length) h += `<span class="muted small">Не подключены: ${esc(p.not_configured.join(", "))}. <a href="#" data-view-link="platforms" data-tab="telegram">Настроить</a></span>`;
    if (errs.length) h += `<ul class="errs">${errs.map((e) => `<li>${esc(e)}</li>`).join("")}</ul>`;
    h += `</div>`;
    if (d.rating && will.some((x) => x.key.startsWith("telegram:") && x.key !== "telegram:group")) h += `<div class="side-sec" id="fcbox">${fcHtml()}</div>`;
    if (d.rating && will.length && p.texts) h += `<div class="side-sec">${previewHtml(will)}</div>`;
    const hard = (p.errors || []).filter((e) => !soft(e));
    const can = !hard.length && (will.length || !d.rating) && !s.publishing;
    h += `<div class="side-sec">`;
    if (d.extend) {
      h += `<button class="btn primary big" style="width:100%" data-pa="publish" ${can && will.length ? "" : "disabled"}>${will.length ? `Дослать: ${will.length} ${C().plural(will.length, "место", "места", "мест")}` : "Выберите, куда дослать"}</button>`;
    } else if (s.when === "later") {
      const at = s.whenAt ? new Date(s.whenAt) : null;
      const ok = can && at && at > new Date();
      h += whenHtml() + `<div class="splitbtn"><button class="btn primary big" data-pa="schedule" ${ok ? "" : "disabled"}>${at ? `Запланировать на ${fmtWhen(at)}` : "Выберите время"}</button>
        <button class="btn big" data-set="when" data-val="now" data-tip="Не планировать — опубликовать сейчас" aria-label="Не планировать">${C().ICON.close}</button></div>`;
    } else {
      h += `<div class="splitbtn"><button class="btn primary big" data-pa="publish" ${can ? "" : "disabled"}>Опубликовать сейчас</button>
        <button class="btn primary big" data-set="when" data-val="later" data-tip="Запланировать на время" aria-label="Запланировать" ${d.rating ? "" : "disabled"}>${C().ICON.clock}</button></div>
        <button class="btn big" style="width:100%;margin-top:8px" data-pa="pipe-add" ${can && will.length && d.rating ? "" : "disabled"} data-tip="Положить пост в очередь: Crosspost сам выберет лучшее свободное время по вашей статистике и опубликует">В конвейер</button>`;
    }
    if (hard.some(e6wait) && hard.length === (p.errors || []).filter(e6wait).length) h += `<p class="small warnline">${BN()} ждёт теги: заполните форму «Теги ${BN()}» или выключите ${BN()} для этого поста.</p>`;
    if (s.schedMsg) h += `<p class="ok small">${esc(s.schedMsg)}. <a href="#" data-view-link="history">Открыть «Историю»</a></p>`;
    return h + `</div>`;
  }
  function previewHtml(will) {
    const s = S(), d = draft(), p = s.plan;
    const plats = TEXTP.filter((k) => will.some((x) => x.platform === k));
    if (!plats.length) return "";
    const cur = plats.includes(s.pvTab) ? s.pvTab : plats[0];
    const t = (p.texts[cur] || "");
    const pub = s.pub, withTitle = d.title && cur !== "deviantart" && (pub[cur] || {}).include_title !== false;
    let body = esc(t);
    if (cur === "telegram") {
      const n = (withTitle ? d.title.length + 1 : 0);
      if (n + t.length > 1024) body = esc(t.slice(0, Math.max(0, 1024 - n))) + `<span class="cut">⋯ дальше отдельным сообщением</span>`;
    }
    body = body.replace(/\[([^\]\n]+)\]\(&lt;?(https?:\/\/[^\s)]+?)(&gt;)?\)/g, '<a href="#">$1</a>');
    return `<details class="more" ${s.pvOpen ? "open" : ""} id="pvbox"><summary>Как будет выглядеть</summary><div class="morebody">
      <div class="seg" role="tablist">${plats.map((k) => `<button role="tab" aria-selected="${k === cur}" class="${k === cur ? "on" : ""}" data-pa="pv" data-val="${k}" data-tip="${C().PLAT[k][2]}">${plats.length > 3 ? C().PLAT[k][0] : C().PLAT[k][2]}</button>`).join("")}</div>
      <div class="preview">${withTitle ? `<b>${esc(d.title)}</b>\n` : ""}${body || `<span class="muted">Без текста</span>`}</div></div></details>`;
  }
  // ----- когда публиковать -----
  const WD = ["вс", "пн", "вт", "ср", "чт", "пт", "сб"], MON = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"];
  function fmtWhen(d) {
    const t = new Date(), tm = new Date(Date.now() + 864e5);
    const same = (a, b) => a.toDateString() === b.toDateString();
    const day = same(d, t) ? "сегодня" : same(d, tm) ? "завтра" : `${WD[d.getDay()]} ${d.getDate()} ${MON[d.getMonth()]}`;
    return `${day}, ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  }
  function localInput(d) {
    const p = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
  }
  function whenHtml() {
    const s = S();
    const bt = s.bestTimes || [];
    let h = `<div class="when"><label class="lbl" for="whenAt">Когда опубликовать</label>
      <input type="datetime-local" id="whenAt" value="${s.whenAt ? localInput(new Date(s.whenAt)) : ""}" min="${localInput(new Date())}">`;
    const fcOk = s.fc && (s.fc.items || []).find((x) => x.ok && x.best_times && x.best_times.length);
    const near = (iso) => s.whenAt && Math.abs(new Date(s.whenAt) - new Date(iso)) < 6e4;
    if (fcOk) h += `<div class="chips">${fcOk.best_times.map((b) => `<button class="chip ${near(b.at) ? "on" : ""}" data-pa="when-pick" data-at="${esc(b.at)}" data-tip="Насколько больше, чем в среднем по всем окнам недели — для этого поста">${esc(fmtWhen(new Date(b.at)))} · ${pct(b.vs_avg)}</button>`).join("")}</div>`;
    else if (bt.length) h += `<div class="chips">${bt.map((b) => `<button class="chip ${near(b.iso) ? "on" : ""}" data-pa="when-pick" data-at="${esc(b.iso)}" data-tip="Окно ${esc(b.window)}, ${b.n} постов в статистике">${esc(b.human)} · ${mult(b.index)}</button>`).join("")}</div>`;
    h += workerHtml();
    return h + `</div>`;
  }

  function paintSide() {
    const el = document.getElementById("pubside");
    if (el) el.innerHTML = sideHtml();
    const ae = document.activeElement;
    if (!(ae && ae.dataset && ae.dataset.htadd)) paintHt();
    const c = document.getElementById("pcount");
    if (c) c.innerHTML = countHtml();
  }

  // ---------------- ход публикации ----------------
  function progressHtml() {
    const s = S(), st = s.pubState, job = s.pubJob;
    const running = job && job.state === "running";
    const ok = st.dests.filter((x) => x.state === "done").length, bad = st.dests.filter((x) => x.state === "error").length;
    const row = (x) => {
      const [ic, lbl] = STATE_ICON[x.state] || ["?", x.state];
      const warn = x.note && /⚠/.test(x.note);
      return `<div class="prow ${x.state}"><span class="pic" title="${lbl}">${ic}</span>
        <div style="flex:1;min-width:0"><b>${esc(x.name)}</b><br>
          <span class="muted small">${esc((x.details || []).join(" · "))}</span>
          ${x.state === "running" ? `<div class="progress thin"><div style="width:${Math.round((x.progress || 0) * 100)}%"></div></div>` : ""}
          ${x.state === "running" && x.message ? `<span class="small">${esc(x.message)} ${Math.round((x.progress || 0) * 100)}%</span>` : ""}
          ${x.state === "error" ? `<br><span class="err small">${esc(x.message)}</span>` : ""}
          ${(() => { const m = /снимок экрана: ([\w.-]+\.png)/.exec((x.message || "") + " " + (x.note || "")); return m ? `<figure class="shot"><img src="/bdebug/${encodeURIComponent(m[1])}?t=${tok()}" alt="Снимок экрана"><figcaption class="muted small">Так выглядела страница в момент ошибки — пришлите этот снимок, если непонятно, что пошло не так.</figcaption></figure>` : ""; })()}
          ${x.note ? `<br><span class="small ${warn ? "warn" : "muted"}">${esc(x.note)}</span>` : ""}</div>
        ${x.link && /^https?:/.test(x.link) ? `<a href="#" class="small" data-open="${esc(x.link)}">Открыть</a>` : ""}</div>`;
    };
    return `<div class="head"><div><h1>${esc(st.title || "Публикация")}</h1>
        <p class="muted">${ratingPill(st.rating)} ${running ? "Публикуется…" : "Готово"} · ${ok} из ${st.dests.length} ${bad ? `· <span class="err">ошибок: ${bad}</span>` : ""}</p></div></div>
      ${running ? `<div class="progress"><div style="width:${Math.round((job.progress || 0) * 100)}%"></div></div>` : ""}
      <section class="card" style="max-width:900px">${st.dests.map(row).join("")}
        ${st.skipped && st.skipped.length ? `<details class="more"><summary>Пропущено: ${st.skipped.length}</summary><div class="dlist">${st.skipped.map((x) => `<div class="small"><b>${esc(x.name)}</b> — <span class="muted">${esc(x.reason)}</span></div>`).join("")}</div></details>` : ""}
      </section>
      ${job && job.state === "error" ? `<p class="err">Публикация прервалась: ${esc(job.error)}</p>` : ""}
      <div style="display:flex;gap:10px;flex-wrap:wrap">
        ${!running && bad ? `<button class="btn primary" data-pa="retry">Повторить неудачные</button>` : ""}
        ${!running && st.pub_id ? `<button class="btn" data-pa="extend" data-id="${st.pub_id}" data-tip="Отправить этот же пост туда, куда он не ушёл — без второго поста в истории">Дослать на другие площадки</button>` : ""}
        ${!running ? `<button class="btn ${bad ? "" : "primary"}" data-pa="newpost">Новый пост</button><button class="btn" data-pa="backedit">Вернуться к посту</button>` : ""}
      </div>
      <p class="note">Повтор отправит пост только туда, где была ошибка — дублей в остальных местах не будет.</p>`;
  }

  // ---------------- История ----------------
  // короткая строка о фоновой отправке — в блоке «Запланировать» нового поста
  function workerHtml() {
    const w = S().worker;
    if (!w) return "";
    if (w.running) return `<span class="muted small">Пост отправит фоновая отправка — окно Crosspost можно закрыть.</span>`;
    return `<div class="wk warn small">Фоновая отправка выключена: пост выйдет, только если окно Crosspost будет открыто.
      <button class="btn small" data-pa="wk-start">Включить</button></div>`;
  }
  const MONS = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"];
  function pipelineHtml() {
    const s = S(), P = s.pipe;
    if (!P) return "";
    const ICON = C().ICON, c = P.cfg, items = P.items || [];
    const hint = C().hint;
    const EXPL = [[0, "Только лучшие окна"], [0.25, "Иногда пробовать новые"], [0.45, "Часто пробовать новые"]];
    const exSel = EXPL.reduce((b, x) => Math.abs(x[0] - c.explore) < Math.abs(b[0] - c.explore) ? x : b);
    const opt = (v, cur, l) => `<option value="${v}" ${String(v) === String(cur) ? "selected" : ""}>${l}</option>`;
    const row = (r) => {
      const img = (r.files || [])[0], at = r.state === "pending" || r.state === "running" || r.state === "missed" ? new Date(r.run_at) : null;
      const when = r.state === "running" ? `<span class="small" style="color:var(--accent)">публикуется…</span>`
        : r.state === "failed" ? `<span class="err small">не вышел: ${esc(r.error || "ошибка")}</span>`
        : r.state === "missed" ? `<span class="err small">пропущен — получит новое время</span>`
        : at ? `<span class="muted small">${esc(r.human)}</span>` : `<span class="muted small">${c.enabled ? "ищу свободное окно…" : "ждёт включения конвейера"}</span>`;
      const mv = s.pipeMove === r.id;
      const box = at ? `<div class="datebox"><b>${String(at.getHours()).padStart(2, "0")}:${String(at.getMinutes()).padStart(2, "0")}</b><span>${at.getDate()} ${MONS[at.getMonth()]}</span></div>` : `<div class="datebox"><b>—</b><span>очередь</span></div>`;
      return `<div class="sitem ${r.state === "missed" || r.state === "failed" ? "missed" : ""}">
        ${box}
        ${img ? `<img src="/uthumb/${esc(img)}?t=${tok()}" alt="" onerror="this.style.visibility='hidden'">` : `<div class="noimg"></div>`}
        <div style="min-width:0;display:flex;flex-direction:column;gap:3px">
          <div style="display:flex;gap:8px;align-items:center;min-width:0"><b class="clip" style="-webkit-line-clamp:1;color:var(--ink)">${esc(r.title || r.text || "Без названия")}</b>${ratingPill(r.rating)}${r.pinned ? `<span class="pill" data-tip="Время закреплено — конвейер его не двигает">📌</span>` : ""}</div>
          <span style="display:flex;gap:10px;flex-wrap:wrap">${when}<span class="muted small">${r.dests} ${C().plural(r.dests, "место", "места", "мест")}</span></span>
          ${r.why && r.state !== "failed" ? `<span class="muted small">${esc(r.why)}</span>` : ""}
          ${mv ? `<div class="row" style="align-items:center;gap:8px;margin-top:4px"><input type="datetime-local" id="pmv-${r.id}" min="${localInput(new Date())}" value="${at ? localInput(at) : ""}"><button class="btn small primary" data-pa="pipe-pin" data-id="${r.id}">Закрепить</button>${r.pinned ? `<button class="btn small" data-pa="pipe-unpin" data-id="${r.id}">Снять закрепление</button>` : ""}<button class="btn small ghost" data-pa="pipe-pin-cancel">Отмена</button></div>` : ""}
        </div>
        ${r.state !== "running" ? `<div class="acts">
          <button class="icon-btn" data-pa="pipe-up" data-id="${r.id}" data-tip="Выше в очереди" aria-label="Выше">↑</button>
          <button class="icon-btn" data-pa="pipe-down" data-id="${r.id}" data-tip="Ниже в очереди" aria-label="Ниже">↓</button>
          <button class="btn small" data-pa="pipe-move" data-id="${r.id}">${ICON.cal} Время</button>
          <button class="icon-btn danger" data-pa="pipe-remove" data-id="${r.id}" data-tip="Убрать из конвейера" aria-label="Убрать">${ICON.trash}</button></div>` : ""}
      </div>`;
    };
    return `<section class="card pipecard"><div class="head"><h2>Конвейер ${hint("Кладёте готовые посты в очередь кнопкой «В конвейер» на экране нового поста — Crosspost сам выбирает время по вашей статистике (окна «день недели × 3 часа») и публикует в фоновой отправке. Время можно закрепить вручную. Если пост сорвался — повторит через 20 минут, до двух раз.")}</h2>
        <span class="spacer"></span><button class="btn small ${c.enabled ? "" : "primary"}" data-pa="pipe-toggle">${c.enabled ? "Поставить на паузу" : "Включить"}</button>
        <button class="btn small ghost" data-pa="pipe-cfg">Настройки</button></div>
      ${!c.enabled ? `<p class="muted small" style="margin:0 0 8px">Конвейер на паузе: посты ждут в очереди без времени.</p>` : ""}
      ${s.pipeCfg ? `<div class="pipecfg">
        <label>Постов в сутки<select data-pipe="per_day">${[1, 2, 3].map((v) => opt(v, c.per_day, v)).join("")}</select></label>
        <label>Не чаще, чем раз в<select data-pipe="min_gap_h">${[3, 4, 6, 8, 12, 24].map((v) => opt(v, c.min_gap_h, v + " ч")).join("")}</select></label>
        <label>Окна<select data-pipe="explore">${EXPL.map((x) => opt(x[0], exSel[0], x[1])).join("")}</select></label>
        <label>Планировать вперёд<select data-pipe="horizon_days">${[7, 14, 30, 60].map((v) => opt(v, c.horizon_days, v + " дн")).join("")}</select></label>
        <p class="muted small" style="margin:6px 0 0">Изучено окон: ${P.windows.solid} из ${P.windows.cells} (3 и больше постов). «Пробовать новые» — иногда ставить пост в окно, где данных мало, чтобы статистика пополнялась. Лимит «в сутки» общий для всех площадок.</p></div>` : ""}
      ${items.length ? `<div class="pinned" style="margin-top:6px">${items.map(row).join("")}</div>` : `<p class="muted small" style="margin:0">Очередь пуста. Подготовьте пост и нажмите «В конвейер».</p>`}
    </section>`;
  }
  function schedHtml() {
    const s = S(), list = (s.sched || []).filter((r) => ["pending", "running", "missed"].includes(r.state) && !r.queue);
    if (!list.length) return "";
    const ICON = C().ICON;
    return `<div class="pinned">${list.map((r) => {
      const img = (r.files || [])[0], at = new Date(r.run_at);
      const time = `${String(at.getHours()).padStart(2, "0")}:${String(at.getMinutes()).padStart(2, "0")}`;
      const state = r.state === "missed" ? `<span class="err small">пропущен — окно было закрыто</span>` : r.state === "running" ? `<span class="small" style="color:var(--accent)">публикуется…</span>` : `<span class="muted small">${esc(r.human)}</span>`;
      return `<div class="sitem ${r.state === "missed" ? "missed" : ""}">
        <div class="datebox"><b>${time}</b><span>${at.getDate()} ${MONS[at.getMonth()]}</span></div>
        ${img ? `<img src="/uthumb/${esc(img)}?t=${tok()}" alt="" onerror="this.style.visibility='hidden'">` : `<div class="noimg"></div>`}
        <div style="min-width:0;display:flex;flex-direction:column;gap:3px">
          <div style="display:flex;gap:8px;align-items:center;min-width:0"><b class="clip" style="-webkit-line-clamp:1;color:var(--ink)">${esc(r.title || r.text || "Без названия")}</b>${ratingPill(r.rating)}</div>
          <span style="display:flex;gap:10px;flex-wrap:wrap">${state}<span class="muted small">${r.dests} ${C().plural(r.dests, "место", "места", "мест")}</span></span>
          ${s.moving === r.id ? `<div class="row" style="align-items:center;gap:8px;margin-top:4px"><input type="datetime-local" id="mv-${r.id}" min="${localInput(new Date())}" value="${localInput(at)}"><button class="btn small primary" data-pa="sched-move" data-id="${r.id}">Сохранить</button><button class="btn small ghost" data-pa="sched-move-cancel">Отмена</button></div>` : ""}
        </div>
        ${r.state !== "running" ? `<div class="acts">
          <button class="btn small primary" data-pa="sched-now" data-id="${r.id}">${ICON.play} Опубликовать сейчас</button>
          ${s.moving === r.id ? "" : `<button class="btn small" data-pa="sched-move" data-id="${r.id}">${ICON.cal} Перенести</button>`}
          <button class="icon-btn danger" data-pa="sched-cancel" data-id="${r.id}" data-tip="Отменить публикацию" aria-label="Отменить публикацию">${ICON.trash}</button></div>` : ""}
      </div>`;
    }).join("")}</div>`;
  }
  const destBadges = (dests) => `<div class="pbs">${dests.map((x) => {
    const st = x.state === "error" ? "err" : x.state === "done" ? "" : "wait";
    const tip = `${x.name}: ${x.state === "done" ? "опубликовано" : x.state === "error" ? "ошибка — " + (x.message || "") : "не дошло"}`;
    return C().pbadge(x.platform || (x.key || "").split(":")[0], { cls: st, tip, link: x.state === "done" && /^https?:/.test(x.link || "") ? x.link : "" });
  }).join("")}</div>`;
  // единая шкала успеха: ×3+ супер-хит, ×2–3 хит, ×1,3–2 выше нормы, ниже ×0,77 — слабее
  const level = (v) => { const L = C().LV(); return v == null ? "" : v >= L.super ? "l4" : v >= L.hit ? "l3" : v >= L.good ? "l2" : v < 0.77 ? "l0" : "l1"; };
  const mk = (v, est, extra = "") => v == null ? "" : `<span class="mk ${level(v)} ${est ? "est" : ""} ${extra}">${est ? "≈" : ""}${mult(v)}</span>`;
  function metricsHtml(r) {
    const s = S(), x = (s.histSum || {})[String(r.id)], n = C().num, ICON = C().ICON;
    if (!x) return `<div class="metrics"></div>`;
    return `<div class="metrics">${x.value != null ? `<span class="metric" data-tip="${esc(x.unit)} в Telegram">${x.unit === "реакций" ? ICON.heart : ICON.eye}${n(x.value)}</span>` : ""}
      ${x.index != null ? `<span data-tip="${x.fresh ? "Оценка по первым часам: " : ""}во сколько раз лучше обычного поста канала">${mk(x.index, x.fresh, "big")}</span>` : ""}</div>`;
  }
  const pubThumb = (r) => {
    const img = (r.files || []).find((f) => f.kind !== "video");
    return `<img src="/pthumb/${r.id}?t=${tok()}" alt="" loading="lazy" ${img ? `onerror="this.onerror=null;this.src='/uthumb/${esc(img.id)}?t=${tok()}'"` : `onerror="this.style.visibility='hidden'"`}>`;
  };
  const pubKind = (r) => { const k = new Set((r.files || []).map((f) => f.kind)); return k.has("video") ? "vid" : k.has("gif") ? "gif" : "img"; };
  // нет превью (в выгрузке не было файла) — вместо пустого квадрата: тип и начало подписи, чтобы пост можно было узнать
  const phKind = (kind, media) => kind === "vid" ? "▶ Видео" : kind === "gif" ? "GIF" : media === "text" ? "Текст" : media === "document" ? "Файл" : "Фото";
  const phHtml = (it) => `<span class="ph"><b>${phKind(it.kind, it.media)}</b><i>${esc((it.text || "").trim().slice(0, 48))}</i></span>`;
  const thumbOf = (it) => it.preview ? `/mthumb/${encodeURIComponent(it.preview)}?t=${tok()}` : it.pub_id ? `/pthumb/${it.pub_id}?t=${tok()}` : "";
  const curYm = () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`; };
  const MONTHS = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"];
  const DOWS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];

  async function loadCalendar(quiet) {
    const s = S(), ym = s.calYm || (s.calYm = curYm());
    s.cal = s.cal || {};
    try {
      const r = await C().api("calendar_month", { ym });
      const prev = s.cal[ym];
      s.cal[ym] = r;
      if (!r._cache || !r._cache.stale || !prev || !prev._cache || prev._cache.at !== r._cache.at) s.calTries = 0;
      if (r._cache && r._cache.stale) {   // данные поменялись — пересчёт идёт в фоне, через 2 с спросим снова
        clearTimeout(s.calPoll);
        s.calTries = (s.calTries || 0) + 1;
        if (s.calTries <= 5) s.calPoll = setTimeout(async () => { if (s.view === "history") { await loadCalendar(true); quietHistory(); } }, 2000 * s.calTries);
      }
    } catch (e) { s.cal[ym] = { error: e.message }; }
    if (!quiet) return;
  }
  function quietHistory() {
    if (S().pstatOpen || S().viewFile) return;   // открытую шторку/просмотр не перерисовываем
    const ae = document.activeElement;
    if (ae && C().$main.contains(ae) && /^(INPUT|TEXTAREA|SELECT)$/.test(ae.tagName)) return;
    if (S().view === "history") C().render();
  }
  // все работы месяца по ключу — для карточки при наведении и шторки
  function calItems() {
    const c = (S().cal || {})[S().calYm];
    const out = {};
    if (c && c.days) Object.values(c.days).flat().forEach((it) => { out[it.key] = it; });
    return out;
  }
  const PLAT = { telegram: ["TG", "Telegram"], vk: ["ВК", "ВКонтакте"], bsky: ["Bs", "Bluesky"], e6ai: ["e6", "e6ai"], e621: ["621", "e621"], da: ["DA", "DeviantArt"] };
  function platChips(it) {
    const set = [...new Set((it.parts || []).map((p) => p.plat))];
    return set.length ? `<span class="cwp">${set.map((p) => `<i class="pl-${p}">${(PLAT[p] || [p])[0]}</i>`).join("")}</span>` : "";
  }
  function suggestHtml(c) {
    const items = calItems(), ICON = C().ICON;
    const list = (c.suggestions || []).filter((x) => items[x.ka] && items[x.kb]);
    if (!list.length) return "";
    const side = (it) => `<span class="sgi"><span class="sgt">${thumbOf(it) ? `<img src="${thumbOf(it)}" alt="" loading="lazy">` : ""}</span><span class="small">${esc(it.channels.join(", "))}<br><span class="muted">${it.day.slice(8)}.${it.day.slice(5, 7)} ${it.time}</span></span></span>`;
    return `<div class="sugg"><h3>Похоже на тот же арт</h3><div class="sgl">${list.map((x, i) => `<div class="sg">${side(items[x.ka])}<span class="sgeq">=</span>${side(items[x.kb])}
        <span class="sga"><button class="btn small" data-pa="cal-sg-yes" data-i="${i}">Объединить</button><button class="btn small ghost" data-pa="cal-sg-no" data-i="${i}">Разные</button></span></div>`).join("")}</div></div>`;
  }
  function selBarHtml() {
    const s = S(), sel = s.calSel || [];
    if (!s.calSelMode && !sel.length) return "";
    const items = calItems();
    const multi = sel.length === 1 && items[sel[0]] && (items[sel[0]].atoms || []).length > 1;
    return `<div class="selbar"><span>${sel.length ? `Выбрано: ${sel.length}` : "Нажимайте на работы, чтобы выбрать"}</span><span class="spacer"></span>
      <button class="btn small" data-pa="cal-merge" ${sel.length < 2 ? "disabled" : ""}>Объединить</button>
      <button class="btn small ghost" data-pa="cal-split" ${multi ? "" : "disabled"}>Разъединить</button>
      <button class="btn small ghost" data-pa="cal-sel-off">Готово</button></div>`;
  }
  function calendarHtml() {
    const s = S(), ym = s.calYm || curYm(), c = (s.cal || {})[ym], ICON = C().ICON;
    const [y, m] = ym.split("-").map(Number);
    let h = `<section class="card calcard"><div class="head"><div class="calnav">
        <button class="icon-btn" data-pa="cal-prev" aria-label="Предыдущий месяц" style="transform:scaleX(-1)">${ICON.chev}</button>
        <button class="calm" data-pa="cal-today" data-tip="Вернуться к текущему месяцу">${MONTHS[m - 1]} ${y}</button>
        <button class="icon-btn" data-pa="cal-next" aria-label="Следующий месяц">${ICON.chev}</button></div>
        <button class="btn small ghost ${s.calSelMode ? "on" : ""}" data-pa="cal-sel" data-tip="Выберите несколько работ (или Ctrl+клик), чтобы объединить их в одну — например, один арт на разных площадках">Объединять</button>
        ${c && c.summary ? `<span class="muted small">${c.summary.posts} ${C().plural(c.summary.posts, "работа", "работы", "работ")}${c.summary.hits ? `, хитов: ${c.summary.hits}` : ""}${c.summary.typical ? `, обычно ${mult(c.summary.typical)}` : ""}</span>` : ""}</div>`;
    if (!c) return h + `<p class="muted">Загрузка…</p></section>`;
    if (c.error) return h + `<p class="err">${esc(c.error)}</p></section>`;
    h += selBarHtml() + suggestHtml(c);
    const start = new Date(c.start + "T00:00:00"), end = new Date(c.end + "T00:00:00");
    const rankOf = {};   // медали лидерам месяца
    (c.leaders || []).slice(0, 3).forEach((it, i) => { if (it.index != null) rankOf[it.key] = i + 1; });
    const sched = {};
    (c.sched || []).forEach((x) => { (sched[x.day] = sched[x.day] || []).push(x); });
    let cells = "";
    for (let d = new Date(start); d < end; d.setDate(d.getDate() + 1)) {
      const iso = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
      const list = c.days[iso] || [], sc = sched[iso] || [];
      const other = d.getMonth() !== m - 1, today = iso === c.today;
      const best = list.length ? list[0].index : null;
      const show = list.slice(0, 4), more = list.length - show.length;
      const n = show.length;
      const works = show.map((it, i) => `<button class="cw ${(s.calSel || []).includes(it.key) ? "picked" : ""}" data-cw="${esc(it.key)}" aria-label="${esc(it.text || it.channels.join(", "))}">
          ${platChips(it)}
          ${thumbOf(it) ? `<img src="${thumbOf(it)}" alt="" loading="lazy" onerror="this.style.visibility='hidden'">` : phHtml(it)}
          ${C().badges({ kind: it.kind, n: it.n_media, kinds: false, rank: rankOf[it.key], scope: "за месяц", index: it.index, est: it.est, sm: true })}
          ${mk(it.index, it.est)}${i === 3 && more > 0 ? `<span class="more">+${more}</span>` : ""}${it.app ? "" : ""}</button>`).join("");
      const plan = sc.map((x) => `<div class="cs" draggable="true" data-sid="${x.sid}" data-tip="Запланировано: ${esc(x.title || "пост")}, ${x.time}. Перетащите на другой день, чтобы перенести.">
          ${x.file ? `<img src="/uthumb/${esc(x.file)}?t=${tok()}" alt="" onerror="this.style.display='none'">` : ""}<span>${ICON.clock}${x.time}</span></div>`).join("");
      cells += `<div class="cc ${other ? "other" : ""} ${today ? "today" : ""} ${s.calDay === iso ? "sel" : ""} ${list.length ? "" : "empty"}" data-day="${iso}">
        <div class="cd"><span class="${best != null && best >= C().LV().hit ? "strong" : ""}">${d.getDate()}</span>${list.length > 1 ? `<span class="muted">${list.length}</span>` : ""}</div>
        ${n ? `<div class="cg n${Math.min(n, 4)}">${works}</div>` : ""}${plan ? `<div class="csl">${plan}</div>` : ""}</div>`;
    }
    h += `<div class="cal"><div class="cdow">${DOWS.map((x) => `<span>${x}</span>`).join("")}</div><div class="cgrid">${cells}</div></div>`;
    if (c.leaders && c.leaders.length) {
      h += `<div class="leaders"><h3>Лидеры месяца</h3><div class="lrow">${c.leaders.map((it, i) => `<button class="ld" data-cw="${esc(it.key)}">
          <span class="lt">${thumbOf(it) ? `<img src="${thumbOf(it)}" alt="" loading="lazy">` : phHtml(it)}${C().badges({ kind: it.kind, n: it.n_media, rank: it.index != null ? i + 1 : 0, scope: "за месяц", index: it.index, est: it.est, sm: true })}${mk(it.index, it.est)}</span>
          <span class="ln"><b>${esc((it.text || "").split("\n")[0].slice(0, 40) || it.channels.join(", "))}</b><span class="muted small">${it.day.slice(8)}.${it.day.slice(5, 7)}, ${esc(it.channels[0] || "")}</span></span></button>`).join("")}</div></div>`;
    }
    return h + `</section>`;
  }
  // список работ выбранного дня (под календарём)
  function dayListHtml() {
    const s = S(), c = (s.cal || {})[s.calYm];
    if (!s.calDay || !c || !c.days) return "";
    const list = c.days[s.calDay] || [], n = C().num, ICON = C().ICON;
    const d = new Date(s.calDay + "T12:00:00");
    return `<div class="toolbar daylist"><h2>${d.toLocaleDateString("ru-RU", { weekday: "long", day: "numeric", month: "long" })}</h2><span class="spacer"></span>
        <button class="btn small ghost" data-pa="cal-day-clear">${ICON.close} Весь месяц</button></div>
      ${list.length ? `<div class="hlist">${list.map((it) => `<div class="hitem" role="button" tabindex="0" data-cw="${esc(it.key)}">
        <span class="hthumb">${thumbOf(it) ? `<img src="${thumbOf(it)}" alt="" loading="lazy">` : phHtml(it)}${C().badges({ kind: it.kind, n: it.n_media, rank: rankOfMonth(it.key), scope: "за месяц", index: it.index, est: it.est, sm: true })}</span>
        <div class="ht"><b>${esc((it.text || "").split("\n")[0].slice(0, 80) || "Без подписи")}</b><span>${it.time} · ${esc(it.channels.join(", "))}${it.app ? ` <span class="pill">Crosspost</span>` : ""}</span></div>
        <div></div><div class="metrics">${it.views != null ? `<span class="metric">${ICON.eye}${n(it.views)}</span>` : ""}${it.index != null ? mk(it.index, it.est, "big") : ""}</div><span class="chev">${ICON.chev}</span></div>`).join("")}</div>`
        : `<p class="muted">В этот день ничего не выходило.</p>`}`;
  }
  function rankOfMonth(key) {
    const c = (S().cal || {})[S().calYm];
    const i = ((c && c.leaders) || []).slice(0, 3).findIndex((x) => x.key === key && x.index != null);
    return i >= 0 ? i + 1 : 0;
  }
  // карточка при наведении на работу
  function hoverCardHtml(it) {
    const n = C().num;
    const views = it.views != null ? n(it.views) : "—";
    return `${thumbOf(it) ? `<div class="hci"><img src="${thumbOf(it).replace("/mthumb/", "/media/")}" alt="">${C().badges({ kind: it.kind, n: it.n_media, rank: rankOfMonth(it.key), scope: "за месяц", index: it.index, est: it.est })}</div>` : ""}
      <div class="hcb"><div class="hch"><b>${esc((it.text || "").split("\n")[0].slice(0, 70) || "Без подписи")}</b>${mk(it.index, it.est, "big")}</div>
        <span class="muted small">${new Date(it.ts * 1000).toLocaleString("ru-RU", { day: "numeric", month: "long", hour: "2-digit", minute: "2-digit" })} · ${esc(it.channels.join(", "))}</span>
        <div class="hcn"><span>${it.by_views ? "Просмотры" : "Реакции"}<b>${it.by_views ? views : n(it.reactions || 0)}</b></span>
          ${it.by_views ? `<span>Реакции<b>${n(it.reactions || 0)}</b></span>` : ""}<span>Пересылки<b>${n(it.forwards || 0)}</b></span></div>
        ${it.index != null ? `<span class="small">${it.est ? `Идёт на ${mult(it.index)} к норме канала${it.proj ? `, ждём ≈${n(it.proj)} за неделю` : ""} — оценка по ${Math.round(it.age_h)} ч` : `${mult(it.index)} к норме канала`}</span>` : `<span class="muted small">Статистики нет: канал не подключён к «Статистике»</span>`}
        ${(it.parts || []).length > 1 ? `<div class="hcparts">${it.parts.map((p) => `<span><i class="pl-${p.plat}">${(PLAT[p.plat] || [p.plat])[0]}</i>${esc(p.label)}<b>${p.index != null ? mult(p.index) : "—"}</b></span>`).join("")}</div>` : ""}
        <span class="muted small">${it.pub_id || it.post_id ? "Нажмите, чтобы открыть подробную статистику" : "Нажмите, чтобы открыть пост на площадке"}</span></div>`;
  }
  let hcTimer = null;
  function showHoverCard(el) {
    const it = calItems()[el.dataset.cw];
    const card = document.getElementById("hcard");
    if (!it || !card) return;
    clearTimeout(hcTimer);
    hcTimer = setTimeout(() => {
      card.innerHTML = hoverCardHtml(it); card.hidden = false;
      const r = el.getBoundingClientRect(), w = card.offsetWidth, hgt = card.offsetHeight;
      let left = r.right + 10;
      if (left + w > window.innerWidth - 8) left = r.left - w - 10;   // у правых колонок — открываем влево
      left = Math.max(8, left);
      let top = Math.min(Math.max(8, r.top + r.height / 2 - hgt / 2), window.innerHeight - hgt - 8);
      card.style.left = left + "px"; card.style.top = Math.max(8, top) + "px";
    }, 250);
  }
  function hideHoverCard() { clearTimeout(hcTimer); const card = document.getElementById("hcard"); if (card) card.hidden = true; }
  document.addEventListener("mouseover", (e) => { const el = e.target.closest && e.target.closest("[data-cw]"); if (el && (el.classList.contains("cw") || el.classList.contains("ld"))) showHoverCard(el); });
  document.addEventListener("mouseout", (e) => { const el = e.target.closest && e.target.closest("[data-cw]"); if (el && !el.contains(e.relatedTarget)) hideHoverCard(); });
  document.addEventListener("focusin", (e) => { const el = e.target.closest && e.target.closest(".cw,.ld"); if (el) showHoverCard(el); });
  document.addEventListener("focusout", hideHoverCard);
  document.addEventListener("click", hideHoverCard, true);
  // перетаскивание запланированного поста на другой день
  document.addEventListener("dragstart", (e) => {
    const x = e.target.closest && e.target.closest(".cs[data-sid]"); if (!x) return;
    S().dragSid = +x.dataset.sid; e.dataTransfer.effectAllowed = "move"; e.dataTransfer.setData("text/plain", "cp-sched");
  });
  document.addEventListener("dragover", (e) => {
    if (S().dragSid == null) return;
    const cc = e.target.closest && e.target.closest(".cc"); if (!cc) return;
    e.preventDefault();
    document.querySelectorAll(".cc.drop").forEach((el) => el.classList.remove("drop")); cc.classList.add("drop");
  });
  document.addEventListener("drop", async (e) => {
    const s = S(); if (s.dragSid == null) return;
    const cc = e.target.closest && e.target.closest(".cc"); const sid = s.dragSid; s.dragSid = null;
    document.querySelectorAll(".cc.drop").forEach((el) => el.classList.remove("drop"));
    if (!cc) return;
    e.preventDefault(); e.stopPropagation();
    const item = ((s.cal || {})[s.calYm].sched || []).find((x) => x.sid === sid);
    if (!item || item.day === cc.dataset.day) return;
    const at = new Date(cc.dataset.day + "T" + item.time + ":00");
    if (at < new Date()) { alert("Этот день уже прошёл — выберите будущий"); return; }
    try { s.sched = await C().api("post_schedule_move", { sid, run_at: at.toISOString() }); } catch (err) { alert(err.message); return; }
    await loadCalendar(); C().render();
  }, true);
  document.addEventListener("dragend", () => { S().dragSid = null; document.querySelectorAll(".cc.drop").forEach((el) => el.classList.remove("drop")); });

  function renderHistory() {
    const s = S(), rows = s.hist;
    const mode = s.histMode || (s.histMode = C().store.get("histMode", "cal"));
    const nSched = (s.sched || []).filter((r) => ["pending", "running", "missed"].includes(r.state)).length;
    let h = `<div class="toolbar"><h1>История</h1><span class="spacer"></span>
      ${mode === "list" && rows && rows.length ? `${C().seg("hf", [["all", `Все ${rows.length}`], ["hits", "Хиты"], ["sched", `Запланированные${nSched ? " " + nSched : ""}`]], s.hf || "all")}
        <select class="sm" id="hsort" aria-label="Сортировка"><option value="date">Сначала новые</option><option value="index" ${s.hsort === "index" ? "selected" : ""}>По индексу успеха</option></select>` : ""}
      ${C().seg("histMode", [["cal", "Календарь"], ["list", "Список"]], mode)}</div>`;
    if (mode === "cal") {
      h += pipelineHtml() + schedHtml() + C().newsStrip() + calendarHtml() + dayListHtml();
    } else {
      if (s.hf !== "hits") h += schedHtml();
      if (!rows) { C().$main.innerHTML = h + `<p class="muted">Загрузка…</p>`; return; }
      if (!rows.length) { C().$main.innerHTML = h + `<section class="card soon"><h2>Здесь появятся ваши публикации</h2><p class="muted">Со статистикой каждого поста и ссылками на все площадки.</p><div><button class="btn primary" data-view-link="post">Новый пост</button></div></section>`; return; }
      const sum = s.histSum || {};
      let list = rows.slice();
      if (s.hf === "hits") list = list.filter((r) => (sum[String(r.id)] || {}).index >= C().LV().hit);
      if (s.hsort === "index") list.sort((a, b) => ((sum[String(b.id)] || {}).index || 0) - ((sum[String(a.id)] || {}).index || 0));
      if (s.hf !== "sched") h += list.length ? `<div class="hlist">${list.map((r) => {
        const title = r.title || (r.text || "").split("\n")[0].slice(0, 80) || "Без названия";
        const dt = new Date(r.created), x = sum[String(r.id)] || {};
        return `<div class="hitem ${x.index >= C().LV().super ? "lead" : ""}" role="button" tabindex="0" data-pa="pstat" data-id="${r.id}" aria-label="Статистика поста ${esc(title)}">
          <span class="hthumb">${pubThumb(r)}${C().badges({ kind: pubKind(r), n: (r.files || []).length, rank: x.rank, scope: "за месяц", index: x.index, est: x.fresh, sm: true })}</span>
          <div class="ht"><b>${esc(title)}</b><span>${dt.toLocaleDateString("ru-RU", { day: "numeric", month: "short" })}, ${dt.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" })} ${ratingPill(r.rating)}</span></div>
          ${destBadges(r.dests)}${metricsHtml(r)}<span class="chev">${C().ICON.chev}</span></div>`;
      }).join("")}</div>` : `<p class="muted">Хитов пока нет — здесь будут посты лучше 90% постов канала (сейчас это ${String(C().LV().hit.toFixed(2)).replace(".", ",")} к норме и выше).</p>`;
    }
    if (s.pstatOpen) h += drawerHtml(s.pstatOpen);
    C().$main.innerHTML = h;
  }
  function drawerHtml(key) {
    const s = S(), [kind, idStr] = String(key).split(":"), id = +idStr;
    let img = "", title = "", sub = "", badges = "", big = "";
    if (kind === "cal") {   // пост с площадки (ВК, Bluesky, e6ai…) или склейка копий — данные берём из самого календаря
      const it = calItems()[String(key).slice(4)] || {};
      title = (it.text || "").split("\n")[0].slice(0, 80);
      if (!title || /^Запись от \d{4}-\d\d-\d\d/.test(title)) title = `${(it.channels || ["Пост"])[0]} · ${it.day ? it.day.slice(8) + "." + it.day.slice(5, 7) : ""}`;
      img = it.preview ? `<img src="/mthumb/${encodeURIComponent(it.preview)}?t=${tok()}" alt="">` : `<span class="hthumb">${phHtml(it)}</span>`;
      big = it.preview ? `/media/${encodeURIComponent(it.preview)}?t=${tok()}` : "";
      sub = it.day ? `${it.day.slice(8)}.${it.day.slice(5, 7)}.${it.day.slice(0, 4)} ${it.time}` : "";
    } else if (kind === "pub") {
      const r = (s.hist || []).find((x) => x.id === id);
      const d = (s.pstat || {})[key];
      title = r ? (r.title || (r.text || "").split("\n")[0].slice(0, 80)) : (d && d.title) || "Публикация";
      const x = (s.histSum || {})[String(id)] || {};
      img = `<span class="hthumb">${pubThumb(r || { id, files: [] })}${r ? C().badges({ kind: pubKind(r), n: (r.files || []).length, rank: x.rank, scope: "за месяц", index: x.index, est: x.fresh, sm: true }) : ""}</span>`;
      big = `/pbig/${id}?t=${tok()}`;
      sub = r ? `${new Date(r.created).toLocaleString("ru-RU")} ${ratingPill(r.rating)}` : "";
      badges = r ? destBadges(r.dests) + `<button class="btn small" style="align-self:flex-start" data-pa="extend" data-id="${id}" data-tip="Отправить этот пост на площадки, куда он не ушёл">Дослать на другие площадки</button>` : "";
    } else {
      const d = (s.pstat || {})[key] || {};
      title = d.title || "Пост";
      img = d.preview ? `<img src="/mthumb/${encodeURIComponent(d.preview)}?t=${tok()}" alt="">` : `<span class="hthumb">${phHtml({ kind: d.media === "video" ? "vid" : /gif|animation/.test(d.media || "") ? "gif" : "img", media: d.media, text: d.text })}</span>`;
      big = d.preview ? `/media/${encodeURIComponent(d.preview)}?t=${tok()}` : "";
      sub = d.created ? new Date(d.created).toLocaleString("ru-RU") : "";
    }
    return `<div class="backdrop" data-pa="pstat-close"></div>
      ${big ? bigHtml(big, false) : ""}
      <aside class="drawer" role="dialog" aria-label="Статистика поста">
        <div class="dh">${img || `<div class="noimg"></div>`}
          <div style="min-width:0;display:flex;flex-direction:column;gap:6px"><b style="font-size:15px">${esc(title || "Без названия")}</b>
            <span class="muted small">${sub}</span>${badges}
            ${(() => { const d = (s.pstat || {})[key] || {}; if (kind === "pub" || kind === "cal") return ""; const t = d.text && d.text.length > (title || "").length ? esc(d.text.slice(0, 140)) : ""; const hint = !d.preview && d.media !== "text" ? "Превью нет: файла не было в выгрузке. Сделайте экспорт канала с включёнными фото и видео и импортируйте снова — превью добавятся." : ""; return t || hint ? `<span class="muted small">${t}${t && hint ? "<br>" : ""}${hint}</span>` : ""; })()}</div>
          <button class="icon-btn" data-pa="pstat-close" aria-label="Закрыть">${C().ICON.close}</button></div>
        <div class="db">${String(key).startsWith("cal:") ? calStatHtml(String(key).slice(4)) : pstatHtml(key)}</div></aside>`;
  }
  function calStatHtml(ik) {
    const it = calItems()[ik];
    if (!it) return `<p class="muted">Пост не найден</p>`;
    const n = C().num, parts = it.parts && it.parts.length ? it.parts : [{ plat: it.ext || it.platform, label: (it.channels || [""])[0], link: it.link, index: it.index, views: it.views, reactions: it.reactions, est: it.est }];
    const ago = (t) => { const h = (Date.now() / 1000 - t) / 3600; return h < 1 ? "только что" : h < 48 ? `${Math.floor(h)} ч назад` : `${Math.floor(h / 24)} дн назад`; };
    return parts.map((p) => {
      const old = p.plat === "vk" && p.stat_at && Date.now() / 1000 - p.stat_at > 2 * 86400;
      const idx = p.index != null ? `<span>Успех: <b>×${Number(p.index).toFixed(2).replace(".", ",")}</b>${p.est ? " (оценка)" : ""}</span>` : `<span class="muted small">Индекс успеха появится через несколько дней, когда на площадке наберётся достаточно постов для сравнения.</span>`;
      return `<div class="pdest"><div class="pdhead">${C().pbadge(p.plat)}<b>${esc(p.label || (PLAT[p.plat] || [, p.plat])[1])}</b>${p.link && /^https?:/.test(p.link) ? `<a href="#" data-open="${esc(p.link)}" class="small">открыть на площадке</a>` : ""}</div>
      <div class="pdm">${p.views != null ? `<span>Просмотры: <b>${n(p.views)}</b></span> ` : ""}<span>Реакции: <b>${n(p.reactions || 0)}</b></span> ${idx}</div>
      ${p.stat_at ? `<p class="${old ? "err" : "muted"} small">Замер: ${ago(p.stat_at)}${old ? ". Цифры давно не обновлялись — проверьте сбор статистики в «Настройки» → ВКонтакте." : ""}</p>` : ""}</div>`;
    }).join("");
  }
  function bigHtml(src, full, extra) {
    return `${full ? `<div class="backdrop"></div>` : ""}<div class="bigprev ${full ? "full" : ""}"><div class="bpstage"><img src="${src}" alt="" draggable="false" onload="window.Pub.bpInit(this)" onerror="this.closest('.bigprev').remove()"></div>${extra || ""}</div>`;
  }
  function viewHtml() {   // просмотр файла на экране нового поста
    const s = S(), d = draft(), i = d.files.findIndex((f) => f.id === s.viewFile);
    if (i < 0) return "";
    const f = d.files[i], n = d.files.length;
    return bigHtml(`/ubig/${f.id}?t=${tok()}`, true, `<button class="bpclose" data-pa="view-close" aria-label="Закрыть">${C().ICON.close}</button>
      <div class="bpcap"><b>${esc(f.name)}</b><span>${f.width ? f.width + "×" + f.height + " · " : ""}${mb(f.size)}${n > 1 ? ` · ${i + 1} из ${n}` : ""}</span></div>
      ${n > 1 ? `<button class="bpnav prev" data-pa="view-prev" aria-label="Предыдущая">‹</button><button class="bpnav next" data-pa="view-next" aria-label="Следующая">›</button>` : ""}`);
  }
  function viewStep(dir) {
    const s = S(), d = draft(), i = d.files.findIndex((f) => f.id === s.viewFile);
    if (i < 0 || d.files.length < 2) return;
    s.viewFile = d.files[(i + dir + d.files.length) % d.files.length].id; C().render();
  }
  // ----- большое превью: вписано в экран, колёсико — масштаб, перетаскивание, миникарта -----
  const bp = { src: "", k: 1, px: 0, py: 0 };
  function bpInit(img) {
    const root = img.closest(".bigprev"), stage = root && root.querySelector(".bpstage");
    if (!root || !stage || root.dataset.ready) return;
    root.dataset.ready = "1";
    if (bp.src !== img.src) Object.assign(bp, { src: img.src, k: 1, px: 0, py: 0 });
    const nw = img.naturalWidth || 1, nh = img.naturalHeight || 1;
    const maxK = () => Math.max(2, 8 / dims().f);   // не крупнее 800% от настоящего размера
    root.insertAdjacentHTML("beforeend", `<div class="bpbar"><button data-bp="out" aria-label="Отдалить">−</button><span class="bpz"></span><button data-bp="in" aria-label="Приблизить">+</button><button data-bp="fit" aria-label="Вписать в экран">Вписать</button></div>
      <div class="bpmini" hidden><img src="${img.src}" alt="" draggable="false"><i></i></div>`);
    const mini = root.querySelector(".bpmini"), mimg = mini.querySelector("img"), rect = mini.querySelector("i"), zl = root.querySelector(".bpz");
    img.style.width = nw + "px"; img.style.height = nh + "px";
    const dims = () => {
      const W = stage.clientWidth, H = stage.clientHeight, f = Math.min(W / nw, H / nh, 6);
      return { W, H, f, iw: nw * f, ih: nh * f };
    };
    const clamp = () => {
      const { W, H, iw, ih } = dims();
      bp.k = Math.min(maxK(), Math.max(1, bp.k));
      const mx = Math.max(0, (iw * bp.k - W) / 2), my = Math.max(0, (ih * bp.k - H) / 2);
      bp.px = Math.min(mx, Math.max(-mx, bp.px)); bp.py = Math.min(my, Math.max(-my, bp.py));
    };
    const apply = () => {
      if (!root.isConnected) { window.removeEventListener("resize", apply); return; }
      clamp();
      const { W, H, f, iw, ih } = dims();
      img.style.left = (W - nw) / 2 + "px"; img.style.top = (H - nh) / 2 + "px";
      img.style.transform = `translate(${bp.px}px, ${bp.py}px) scale(${f * bp.k})`;
      zl.textContent = Math.round(f * bp.k * 100) + "%";
      root.classList.toggle("zoomed", bp.k > 1.001);
      mini.hidden = bp.k <= 1.001;
      if (!mini.hidden) {
        const mw = Math.min(170, 170 * iw / ih), mh = mw * ih / iw;
        mini.style.width = mw + "px"; mini.style.height = mh + "px";
        const vw = Math.min(1, W / (iw * bp.k)), vh = Math.min(1, H / (ih * bp.k));
        const cx = 0.5 - bp.px / (iw * bp.k), cy = 0.5 - bp.py / (ih * bp.k);
        Object.assign(rect.style, { left: (cx - vw / 2) * 100 + "%", top: (cy - vh / 2) * 100 + "%", width: vw * 100 + "%", height: vh * 100 + "%" });
      }
    };
    const zoomAt = (nk, cx, cy) => {   // масштаб вокруг точки экрана (cx, cy — от центра окна просмотра)
      nk = Math.min(maxK(), Math.max(1, nk));
      const r = nk / bp.k;
      bp.px = cx - (cx - bp.px) * r; bp.py = cy - (cy - bp.py) * r; bp.k = nk; apply();
    };
    stage.addEventListener("wheel", (e) => {
      e.preventDefault();
      const b = stage.getBoundingClientRect();
      zoomAt(bp.k * Math.exp(-e.deltaY * 0.002), e.clientX - b.left - b.width / 2, e.clientY - b.top - b.height / 2);
    }, { passive: false });
    let drag = null;
    const onImg = (e) => { const r = img.getBoundingClientRect(); return e.clientX >= r.left && e.clientX <= r.right && e.clientY >= r.top && e.clientY <= r.bottom; };
    const closeAll = () => { S().pstatOpen = null; S().viewFile = null; C().render(); };
    stage.addEventListener("pointerdown", (e) => { if (e.button !== 0) return; drag = { x: e.clientX, y: e.clientY, px: bp.px, py: bp.py, moved: false }; stage.setPointerCapture(e.pointerId); if (onImg(e)) stage.classList.add("grab"); });
    stage.addEventListener("pointermove", (e) => {
      if (!drag) { stage.classList.toggle("over", onImg(e)); return; }
      if (Math.abs(e.clientX - drag.x) + Math.abs(e.clientY - drag.y) > 4) drag.moved = true;
      bp.px = drag.px + e.clientX - drag.x; bp.py = drag.py + e.clientY - drag.y; apply();
    });
    const end = () => { drag = null; stage.classList.remove("grab"); };
    stage.addEventListener("pointerup", (e) => { const d = drag; end(); if (d && !d.moved && !onImg(e)) closeAll(); });   // клик мимо картинки — свернуть
    stage.addEventListener("pointercancel", end);
    root.addEventListener("click", (e) => { if (e.target === root) closeAll(); });
    stage.addEventListener("dblclick", (e) => {
      const b = stage.getBoundingClientRect();
      if (bp.k > 1.001) { bp.k = 1; bp.px = bp.py = 0; apply(); } else zoomAt(3, e.clientX - b.left - b.width / 2, e.clientY - b.top - b.height / 2);
    });
    // миникарта: нажать или тянуть — перейти в это место
    const toMini = (e) => {
      const { iw, ih } = dims(), b = mini.getBoundingClientRect();
      const fx = Math.min(1, Math.max(0, (e.clientX - b.left) / b.width)), fy = Math.min(1, Math.max(0, (e.clientY - b.top) / b.height));
      bp.px = -(fx - 0.5) * iw * bp.k; bp.py = -(fy - 0.5) * ih * bp.k; apply();
    };
    let md = false;
    mini.addEventListener("pointerdown", (e) => { md = true; mini.setPointerCapture(e.pointerId); toMini(e); e.stopPropagation(); });
    mini.addEventListener("pointermove", (e) => { if (md) toMini(e); });
    mini.addEventListener("pointerup", () => { md = false; });
    root.querySelector(".bpbar").addEventListener("click", (e) => {
      const b = e.target.closest("[data-bp]"); if (!b) return;
      if (b.dataset.bp === "fit") { bp.k = 1; bp.px = bp.py = 0; apply(); }
      else zoomAt(bp.k * (b.dataset.bp === "in" ? 1.5 : 1 / 1.5), 0, 0);
    });
    window.addEventListener("resize", apply);
    apply();
    if (!window.__bpEsc) {
      window.__bpEsc = true;
      document.addEventListener("keydown", (e) => {
        if (e.key === "Escape" && (S().pstatOpen || S().viewFile)) { S().pstatOpen = null; S().viewFile = null; C().render(); }
        else if (S().viewFile && (e.key === "ArrowLeft" || e.key === "ArrowRight")) viewStep(e.key === "ArrowLeft" ? -1 : 1);
      });
    }
  }
  async function openDrawer(key) {
    const s = S(), api = C().api, [kind, id] = String(key).split(":");
    hideHoverCard();
    s.pstatOpen = key; s.pstatErr = ""; s.pstat = s.pstat || {}; C().render();
    if (kind === "cal") return;
    try { s.pstat[key] = kind === "pub" ? await api("pub_stats", { pub_id: +id }) : await api("post_detail", { post_id: +id }); }
    catch (err) { s.pstat[key] = { error: err.message }; }
    C().render();
  }

  // ----- статистика поста -----
  const mult = (v) => "×" + Number(v).toFixed(2).replace(".", ",");
  function growthSvg(st, fc, curve) {
    const pts = (st.series || []).filter((p) => p.h <= 192);
    const final = fc && fc.expected ? fc.expected : st.projected || null;
    if (!pts.length && !final) return "";
    const H = 168, W = 600, Hh = 150, X = (h) => 30 + Math.sqrt(Math.min(h, H) / H) * (W - 40);
    const fracAt = (h) => {
      const c = curve.points; if (h <= c[0].h) return c[0].frac * h / c[0].h;
      for (let i = 1; i < c.length; i++) if (h <= c[i].h) { const a = c[i - 1], b = c[i], w = (Math.log(h) - Math.log(a.h)) / (Math.log(b.h) - Math.log(a.h)); return a.frac + w * (b.frac - a.frac); }
      return 1;
    };
    const top = Math.max(final || 0, ...pts.map((p) => p.v), 1) * 1.08;
    const Y = (v) => 10 + (1 - v / top) * Hh;
    let exp = "";
    if (final) {
      const hs = [0.25, 0.5, 1, 2, 3, 6, 9, 12, 18, 24, 36, 48, 72, 96, 120, 144, 168];
      exp = `<path d="${hs.map((h, i) => (i ? "L" : "M") + X(h).toFixed(1) + " " + Y(final * fracAt(h)).toFixed(1)).join(" ")}" fill="none" stroke="var(--ink-3)" stroke-dasharray="4 4" stroke-width="1.5"/>`;
    }
    const line = pts.length ? `<path d="${pts.map((p, i) => (i ? "L" : "M") + X(p.h).toFixed(1) + " " + Y(p.v).toFixed(1)).join(" ")}" fill="none" stroke="var(--s1)" stroke-width="2.5" stroke-linejoin="round"/>
      ${pts.map((p) => `<circle cx="${X(p.h).toFixed(1)}" cy="${Y(p.v).toFixed(1)}" r="3.5" fill="var(--s1)"><title>${p.h < 24 ? p.h.toFixed(1).replace(".", ",") + " ч" : (p.h / 24).toFixed(1).replace(".", ",") + " дн"}: ${C().num(p.v)}</title></circle>`).join("")}` : "";
    const ticks = [[1, "1 ч"], [6, "6 ч"], [24, "1 д"], [72, "3 д"], [168, "7 д"]];
    return `<svg class="gsvg" viewBox="0 0 ${W} ${Hh + 34}" role="img" aria-label="Набор просмотров по часам">
      <line x1="30" x2="${W - 10}" y1="${Y(0)}" y2="${Y(0)}" stroke="var(--line-strong)"/>
      ${ticks.map(([h, l]) => `<line x1="${X(h)}" x2="${X(h)}" y1="10" y2="${Y(0)}" stroke="var(--line)"/><text x="${X(h)}" y="${Y(0) + 18}" text-anchor="middle" font-size="12" fill="var(--ink-3)">${l}</text>`).join("")}
      ${exp}${line}</svg>
      <div class="legend small"><span><span class="sw" style="background:var(--s1)"></span>замеры</span>${final ? `<span><span class="sw dash"></span>${fc && fc.expected ? "путь к прогнозу" : "ожидаемый путь"}</span>` : ""}</div>`;
  }
  function pstatHtml(id) {
    const s = S(), d = (s.pstat || {})[id], n = C().num;
    if (!d) return `<p class="muted">Загрузка…</p>`;
    if (d.error) return `<p class="err">${esc(d.error)}</p>`;
    let h = "";
    // статусная плашка для удачных постов: сразу объясняет, что значат цифры ниже
    const top = (d.dests || []).map((x) => x.stats).filter((st) => st && st.ok && (st.index || st.index_now))
      .sort((a, b) => (b.index || b.index_now) - (a.index || a.index_now))[0];
    if (top) {
      const v = top.index || top.index_now, pc = top.percentile;
      const L = C().LV();
      if (v >= L.hit || (pc != null && pc >= 0.9)) h += `<div class="banner-hit ${v >= L.super ? "super" : ""}">${C().ICON.trophy}<span><b>${v >= L.super ? "Супер-хит" : v >= L.hit ? "Хит канала" : "Сильный пост"}</b>${pc != null ? ` — входит в топ-${Math.max(1, Math.round((1 - pc) * 100))}% публикаций канала` : ""}${top.index ? "" : ", пока по оценке первых часов"}</span>${mk(v, !top.index, "big")}</div>`;
      else if (v < 0.77) h += `<div class="banner-hit low"><span>Пост идёт слабее обычного для канала${pc != null ? ` — лучше ${Math.round(pc * 100)}% публикаций` : ""}</span>${mk(v, !top.index, "big")}</div>`;
    }
    for (const x of d.dests) {
      const plat = x.platform || (x.key || "").split(":")[0];
      h += `<div class="pdest"><div class="pdhead">${C().pbadge(plat)}<b>${esc(x.name)}</b>${x.link && /^https?:/.test(x.link) ? `<a href="#" data-open="${esc(x.link)}" class="small">открыть</a>` : ""}</div>`;
      if (x.stats && x.stats.ok) {
        const st = x.stats, fc = x.forecast;
        if (x.verdict) h += `<p class="verdict">${esc(x.verdict)}</p>`;
        const cell = (l, v, sub) => `<div class="pnum"><span class="muted small">${l}</span><b>${v}</b>${sub ? `<span class="muted small">${sub}</span>` : ""}</div>`;
        h += `<div class="pnums">
          ${st.by_views ? cell("Просмотры", n(st.views), st.projected ? `≈ ${n(st.projected)} за неделю` : "") : ""}
          ${cell("Реакции", n(st.reactions || 0), st.react_rate ? (st.react_rate * 100).toFixed(1).replace(".", ",") + "% от просмотров" : "")}
          ${cell("Пересылки", n(st.forwards || 0), "")}
          ${st.channel_median ? cell("Норма канала", n(st.channel_median), st.percentile !== undefined ? `лучше ${Math.round(st.percentile * 100)}% постов` : "") : ""}
          ${fc && fc.expected ? cell("Прогноз был", "≈ " + n(fc.expected), `${n(fc.low)}–${n(fc.high)}`) : ""}
        </div>`;
        h += growthSvg(st, fc, d.curve);
        h += `<span class="muted small">Возраст поста ${st.age_h < 48 ? Math.round(st.age_h) + " ч" : Math.round(st.age_h / 24) + " дн"}${st.updated ? ", цифры от " + new Date(st.updated).toLocaleString("ru-RU") : ""}${!st.selected ? ". Канал не выбран в «Импорте для статистики» — индекс не считается" : ""}</span>`;
      } else if (x.stats) {
        h += `<p class="muted small">${esc(x.stats.why)}</p>`;
      } else if (x.e6ai) {
        h += x.e6ai.error ? `<p class="muted small">${esc(x.e6ai.error)}</p>` : `<div class="pnums">
          <div class="pnum"><span class="muted small">Score</span><b>${n(x.e6ai.score || 0)}</b></div>
          <div class="pnum"><span class="muted small">В избранном</span><b>${n(x.e6ai.favs || 0)}</b></div>
          <div class="pnum"><span class="muted small">Комментарии</span><b>${n(x.e6ai.comments || 0)}</b></div></div>`;
        h += e6modHtml(x.e6mod);
      } else if (x.nums) {
        h += x.nums.error ? `<p class="muted small">${esc(x.nums.error)}</p>` :
          `<div class="pnums">${x.nums.cells.map(([l, v]) => `<div class="pnum"><span class="muted small">${esc(l)}</span><b>${typeof v === "number" ? n(v) : esc(v == null ? "0" : String(v))}</b></div>`).join("")}</div>${x.nums.note ? `<p class="muted small">${esc(x.nums.note)}</p>` : ""}`;
      } else if (x.why) h += `<p class="muted small">${esc(x.why)}</p>`;
      h += `</div>`;
    }
    const busy = s.pstatBusy === id;
    h += `<div class="pfoot"><button class="btn small" data-pa="pstat-refresh" data-id="${esc(id)}" ${busy ? "disabled" : ""}>${C().ICON.refresh} ${busy ? "Обновляю…" : "Обновить цифры"}</button>
      ${C().hint("Первые 7 дней цифры снимаются сами — через 1, 3, 6, 12 часов, сутки и дальше, пока работает фоновая отправка." + (S().worker && S().worker.track ? " " + S().worker.track : ""))}</div>`;
    if (s.pstatErr) h += `<p class="err small">${esc(s.pstatErr)}</p>`;
    return h;
  }

  function e6modHtml(list) {
    if (!list || !list.length) return "";
    const tl = (arr, cls) => arr.map((t) => `<span class="chip ${cls}">${esc(t.replace(/_/g, " "))}</span>`).join("");
    const checked = list.filter((x) => x.checked);
    if (!checked.length) {
      const due = list.map((x) => x.due).filter(Boolean).sort()[0];
      return `<p class="muted small">Сверка тегов с правками модераторов — ${due ? new Date(due).toLocaleString("ru-RU", { day: "numeric", month: "long", hour: "2-digit", minute: "2-digit" }) : "через сутки после загрузки"}.</p>`;
    }
    const rem = [...new Set(checked.flatMap((x) => x.removed))], add = [...new Set(checked.flatMap((x) => x.added))];
    if (!rem.length && !add.length) return `<p class="ok small">Модераторы теги не правили.</p>`;
    return `<div class="e6mod"><span class="small"><b>Модераторы поправили теги</b> ${C().hint("Убранное больше не отмечается само, добавленное предлагается охотнее. Персонажей и франшизы не запоминаю. Посмотреть и забыть — «Настройки» → " + BN() + ".")}</span>
      ${rem.length ? `<div class="chips"><span class="small muted">убрали</span>${tl(rem, "mrem")}</div>` : ""}
      ${add.length ? `<div class="chips"><span class="small muted">добавили</span>${tl(add, "madd")}</div>` : ""}</div>`;
  }
  function e6learnCard() {
    const L = S().e6learn;
    if (!L) return `<section class="card setc"><div class="head"><h2>Правки модераторов</h2></div><p class="muted">Загрузка…</p></section>`;
    const chip = (t, k) => `<span class="chip ${k}">${esc(t.replace(/_/g, " "))}<button class="cx" data-pa="e6-forget" data-tag="${esc(t)}" aria-label="Забыть ${esc(t)}" data-tip="Забыть">×</button></span>`;
    return `<section class="card setc"><div class="head"><h2>Теги: правки модераторов ${C().hint("Через сутки и через трое после загрузки Crosspost сверяет теги поста на " + BN() + " с отправленными. Что модераторы убрали — больше не отмечается само (подсказка остаётся пунктиром). Что добавили дважды и больше — отмечается уже от 25% уверенности теггера. Персонажей, франшизы и мета-теги не запоминаю.")}</h2>
        <button class="btn small ghost" data-pa="e6-checknow">Сверить сейчас</button></div>
      <div class="field"><span class="lbl">Не отмечаю сам — модераторы убирали</span><div class="chips">${L.removed.map((t) => chip(t, "mrem")).join("") || `<span class="muted small">пока ничего</span>`}</div></div>
      <div class="field"><span class="lbl">Отмечаю охотнее — модераторы добавляли</span><div class="chips">${L.added.map((t) => chip(t, "madd")).join("") || `<span class="muted small">пока ничего</span>`}</div></div>
      <div class="field"><label for="e6never">Не предлагать совсем ${C().hint("Эти теги теггера не показываются даже пунктиром. Через пробел.")}</label>
        <textarea id="e6never" rows="2" placeholder="ring_piercing one_eye_closed">${esc((L.never || []).join(" "))}</textarea></div>
      <div class="cardfoot"><button class="btn primary" data-pa="e6-never-save">Сохранить список</button>${L.removed.length || L.added.length ? `<button class="btn ghost" data-pa="e6-forget" data-tag="">Забыть всё выученное</button>` : ""}<span class="ok small">${esc(S().e6learnOk || "")}</span></div></section>`;
  }

  // ---------------- Площадки → Публикация ----------------
  // статус площадки для боковой панели: [лампочка ok/warn/"", подпись]
  function platStatus(sec) {
    const p = S().pub;
    if (!p) return ["", ""];
    const pl = C().plural;
    switch (sec) {
      case "telegram": { const t = p.telegram, n = t.channels.length;
        if (!t.has_token) return ["", "не подключено"];
        return [n ? "ok" : "warn", (n ? `${n} ${pl(n, "канал", "канала", "каналов")}` : "нет каналов") + (t.transport === "mtproto" ? ", MTProto" : ", Bot API")]; }
      case "discord": { const n = p.discord.webhooks.length; return [n ? "ok" : "", n ? `${n} ${pl(n, "вебхук", "вебхука", "вебхуков")}` : "не подключено"]; }
      case "vk": { const v = p.vk, br = v.method === "browser";
        const logged = br ? v.browser_ok : p.vk_connected;
        if (!logged) return ["", "не подключено"];
        return [v.group_id ? "ok" : "warn", (v.group_name || (v.group_id ? "группа " + v.group_id : "группа не указана")) + (br ? ", через браузер" : ", через API")]; }
      case "bluesky": { const b = p.bluesky_session || {}; return b.ok && p.bluesky.enabled ? ["ok", "@" + b.handle] : b.ok ? ["warn", "выключено"] : ["", "не подключено"]; }
      case "deviantart": { const b = p.deviantart_session || {}; return b.ok && p.deviantart.enabled ? ["ok", b.username || "подключено"] : b.ok ? ["warn", "выключено"] : ["", "не подключено"]; }
      case "e6ai": { const pre = p.e6ai.site_name === "e621" ? "e621 · " : ""; return p.e6ai.enabled && p.e6ai.has_key ? ["ok", pre + (p.e6ai.username || "подключено")] : ["", pre + "не подключено"]; }
      case "hashtags": return p.hashtags.enabled ? ["ok", "включены"] : ["", "выключены"];
      case "signatures": { const on = TEXTP.filter((k) => { const v = (p.signatures.platforms || {})[k] || {}; return v.sfw || v.nsfw; });
        return [on.length ? "ok" : "", on.length ? on.map((k) => C().PLAT[k][0]).join(", ") : "не заданы"]; }
      case "media": { const m = S().mediaSt; return m ? (m.ready ? ["ok", "ffmpeg есть"] : ["", "ffmpeg не установлен"]) : ["", ""]; }
      case "light": return ["ok", `${Math.round(p.light.scale * 100)}%, качество ${p.light.quality}`];
      case "files": { const o = S().outputs; return o ? [o.usage.files ? "ok" : "", o.usage.files ? `${o.usage.files} ${C().plural(o.usage.files, "файл", "файла", "файлов")}, ${mb(o.usage.bytes)}` : (o.custom ? "своя папка" : "в каталоге программы")] : ["", ""]; }
    }
    return ["", ""];
  }
  function settingsHtml(sec) {
    const s = S(), p = s.pub;
    if (!p) return `<p class="muted">Загрузка…</p>`;
    const err = (k) => s.pubErr && s.pubErr[k] ? `<p class="err">${esc(s.pubErr[k])}</p>` : "";
    const ok = (k) => s.pubOk && s.pubOk[k] ? `<p class="ok small">${esc(s.pubOk[k])}</p>` : "";
    const busy = (k) => s.pubBusy === k ? "disabled" : "";
    const hint = C().hint, ICON = C().ICON;
    const segP = (attr, opts, cur) => `<div class="seg" role="radiogroup">${opts.map(([k, l]) =>
      `<button role="radio" aria-checked="${k === cur}" class="${k === cur ? "on" : ""}" ${attr} data-val="${k}">${l}</button>`).join("")}</div>`;
    const sel = (attr, opts, cur, label) => `<select class="sm" ${attr} aria-label="${esc(label)}">${opts.map(([k, l]) => `<option value="${k}" ${k === cur ? "selected" : ""}>${l}</option>`).join("")}</select>`;
    const tgl = (attr, on, label, tip) => `<label class="tgl"><input type="checkbox" ${attr} ${on ? "checked" : ""}><span>${label}${tip ? " " + hint(tip) : ""}</span></label>`;
    const badge = (cls, text) => `<span class="badge ${cls}">${text}</span>`;
    const RATE_OPTS = [["sfw", "Только SFW"], ["nsfw", "Только 18+"], ["all", "SFW и 18+"]];
    const known = (s.pubKnown && s.pubKnown.chats) || [];
    const tg = p.telegram, dc = p.discord, vk = p.vk, sg = p.signatures, li = p.light;
    let h = "";

    if (sec === "telegram") {
      h += `<section class="card setc"><div class="head"><h2>${C().pbadge("telegram")} Telegram</h2></div>${err("tg")}${ok("tg")}`;
      if (!tg.has_token || s.tgNewToken) {
        h += `<details class="how" ${tg.has_token ? "" : "open"}><summary>Как подключить бота</summary><ol class="steps">
            <li>Если бота ещё нет: напишите <a href="#" data-open="https://t.me/BotFather">@BotFather</a> и отправьте /newbot.</li>
            <li>Добавьте бота админом в каналы и в группу, с правом публиковать сообщения.</li>
            <li>Вставьте сюда токен бота вида <code>123456789:AA…</code>. Никому его не показывайте: с ним можно постить от имени бота.</li></ol></details>
          <div class="row" style="align-items:flex-end"><div class="field" style="flex:1 1 360px"><label for="bottok">Токен бота</label><input id="bottok" type="password" autocomplete="off" placeholder="123456789:AAxxxxxxxx"></div>
          <button class="btn primary" data-pa="tg-token" ${busy("tg")}>Проверить и сохранить</button>${tg.has_token ? `<button class="btn ghost" data-pa="tg-newtoken-cancel">Отмена</button>` : ""}</div>`;
      } else {
        h += `<div class="status">${badge("ok", "✓ Подключён")}<span>Публикует бот <b>@${esc(tg.bot_name)}</b></span><span class="grow1"></span>
            <button class="btn small ghost" data-pa="tg-newtoken">Сменить токен</button></div>
          <div class="field"><span class="lbl">Связь с Telegram ${hint(tg.transport === "mtproto" ? "MTProto идёт теми же путями, что Telegram Desktop и импорт: с вашими настройками соединения (zapret, прокси)." : "Bot API ходит на api.telegram.org. Если он у вас не открывается — выберите MTProto.")}</span>
            ${segP(`data-pa="tg-transport"`, [["botapi", "Bot API"], ["mtproto", "MTProto, как Telegram Desktop"]], tg.transport || "botapi")}</div>`;
      }
      h += `</section>`;
      if (tg.has_token && !s.tgNewToken) {
        h += `<section class="card setc"><div class="head"><h2>Каналы ${hint("SFW-канал — «Только SFW». 18+ канал — «Любой»: SFW-работы тоже уходят туда, а 18+ — только туда.")}</h2></div>
          ${tg.channels.length ? `<table class="dtable"><thead><tr><th>Канал</th><th>Что публиковать</th><th>PNG-оригиналы ${hint("«В комментарии» — у канала должна быть группа обсуждения, бот — админ и в ней. Бот ждёт копию поста в обсуждении до минуты.")}</th><th></th></tr></thead><tbody>
            ${tg.channels.map((c, i) => `<tr><td><b>${esc(c.title || c.id)}</b><small>${esc(c.id)}</small></td>
              <td>${sel(`data-pa="tg-chrating" data-i="${i}"`, RATE_OPTS, c.rating, "Что публиковать")}</td>
              <td>${sel(`data-pa="tg-chorig" data-i="${i}"`, ORIG, c.originals, "PNG-оригиналы")}</td>
              <td class="acts"><button class="btn small ghost" data-pa="tg-chcheck" data-i="${i}" ${busy("tg")}>Проверить</button><button class="icon-btn danger" data-pa="tg-chdel" data-i="${i}" data-tip="Убрать канал" aria-label="Убрать канал">${ICON.trash}</button></td></tr>`).join("")}
            </tbody></table>` : `<p class="muted">Каналов пока нет.</p>`}
          <div class="row" style="align-items:flex-end"><div class="field" style="flex:1 1 300px"><label for="tgadd">Добавить канал</label>
            <input id="tgadd" list="tgknown" placeholder="@my_channel, ссылка t.me/… или id -100…">
            <datalist id="tgknown">${known.filter((c) => c.broadcast !== false).map((c) => `<option value="${esc(c.chat_id)}">${esc(c.title)}</option>`).join("")}</datalist></div>
            <button class="btn" data-pa="tg-chadd" ${busy("tg")}>Проверить и добавить</button></div></section>`;
        const g = tg.group || {};
        h += `<section class="card setc"><div class="head"><h2>Группа с темами ${hint("Пост пересылается в тему группы: SFW — из SFW-канала, 18+ — из 18+ канала, под спойлером. Оригиналы в группу не идут.")}</h2><span class="muted small">необязательно</span></div>
          <div class="row" style="align-items:flex-end"><div class="field" style="flex:1 1 300px"><label for="tggroup">Группа</label>
            <input id="tggroup" list="tgknowng" value="${esc(g.id || "")}" placeholder="ссылка t.me/… или id -100…">
            <datalist id="tgknowng">${known.filter((c) => c.broadcast !== true).map((c) => `<option value="${esc(c.chat_id)}">${esc(c.title)}</option>`).join("")}</datalist></div>
            <button class="btn" data-pa="tg-group" ${busy("tg")}>Проверить</button>${g.id ? `<button class="icon-btn danger" data-pa="tg-groupdel" data-tip="Убрать группу" aria-label="Убрать группу">${ICON.trash}</button>` : ""}</div>`;
        if (g.id) {
          const topics = s.topics || [];
          const tsel = (key, cur) => topics.length
            ? `<select data-pa="tg-topic" data-k="${key}"><option value="0">не пересылать</option>${topics.map((t) => `<option value="${t.id}" ${+cur === t.id ? "selected" : ""}>${esc(t.title)}</option>`).join("")}</select>`
            : `<input data-pa="tg-topicin" data-k="${key}" value="${cur || ""}" placeholder="номер или ссылка на тему">`;
          h += `<p><b>${esc(g.title || g.id)}</b></p>
            <div class="row"><div class="field" style="flex:1 1 220px"><label>Тема для SFW</label>${tsel("sfw", g.sfw_topic)}</div>
              <div class="field" style="flex:1 1 220px"><label>Тема для 18+</label>${tsel("nsfw", g.nsfw_topic)}</div></div>
            ${topics.length ? "" : `<div class="cardfoot">${s.pubKnown && s.pubKnown.authorized ? `<button class="btn small" data-pa="tg-topics">Загрузить список тем</button>` : `<span class="muted small">Номер темы — последнее число в ссылке на неё ${hint("«Копировать ссылку» на теме → t.me/c/123456/5, номер — 5. Можно вставить ссылку целиком.")}</span>`}
              <button class="btn small primary" data-pa="tg-topicsave">Сохранить темы</button></div>`}`;
        }
        h += `</section>
          <section class="card setc"><div class="head"><h2>Как публиковать</h2></div><div class="tgls">
            ${tgl(`data-pa="tg-flag" data-k="spoiler_nsfw"`, tg.spoiler_nsfw, "18+ под спойлер")}
            ${tgl(`data-pa="tg-flag" data-k="silent_originals"`, tg.silent_originals, "Оригиналы без уведомления")}
            ${tg.transport === "mtproto" ? tgl(`data-pa="tg-flag" data-k="preupload"`, tg.preupload !== false, `Заливать PNG заранее ${hint("Пока вы пишете пост, PNG-оригиналы уже уходят на сервер Telegram (никуда не публикуясь). После «Опубликовать» они ложатся под пост почти сразу. Если картинку заменить или убрать — залитый файл просто не пригодится, Telegram сам удалит его.")}`) : ""}
            ${tgl(`data-pa="tg-flag" data-k="include_title"`, tg.include_title, "Заголовок жирным")}</div></section>`;
      }
    }

    if (sec === "discord") {
      h += `<section class="card setc"><div class="head"><h2>${C().pbadge("discord")} Discord</h2></div>${err("dc")}${ok("dc")}
        <details class="how" ${dc.webhooks.length ? "" : "open"}><summary>Как получить ссылку вебхука</summary>
          <p>Канал Discord → «Настройки канала» → «Интеграция» → «Вебхуки» → «Новый вебхук» → «Копировать URL». Для 18+ канала включите в Discord «Канал с возрастным ограничением».</p></details>
        ${dc.webhooks.length ? `<table class="dtable"><thead><tr><th>Вебхук</th><th>Что публиковать</th><th></th></tr></thead><tbody>
          ${dc.webhooks.map((w, i) => `<tr><td><b>${esc(w.name || "вебхук")}</b><small>…${esc((w.url || "").slice(-8))}</small></td>
            <td>${sel(`data-pa="dc-rating" data-i="${i}"`, RATE_OPTS, w.rating, "Что публиковать")}</td>
            <td class="acts"><button class="icon-btn danger" data-pa="dc-del" data-i="${i}" data-tip="Убрать вебхук" aria-label="Убрать вебхук">${ICON.trash}</button></td></tr>`).join("")}</tbody></table>` : ""}
        <div class="row" style="align-items:flex-end"><div class="field" style="flex:2 1 320px"><label for="dcurl">Ссылка вебхука</label><input id="dcurl" placeholder="https://discord.com/api/webhooks/…"></div>
          <div class="field" style="flex:1 1 160px"><label for="dcname">Название</label><input id="dcname" placeholder="#арт-18"></div>
          <button class="btn" data-pa="dc-add" ${busy("dc")}>Проверить и добавить</button></div></section>`;
      if (dc.webhooks.length) h += `<section class="card setc"><div class="head"><h2>Как публиковать</h2></div><div class="tgls">
          ${tgl(`data-pa="dc-flag" data-k="blur_nsfw"`, dc.blur_nsfw, "18+ под спойлер", "Картинки размыты до клика.")}
          ${tgl(`data-pa="dc-flag" data-k="no_previews"`, dc.no_previews !== false, "Без превью ссылок", "Discord не будет строить карточки из ссылок в подписи.")}
          ${tgl(`data-pa="dc-flag" data-k="include_title"`, dc.include_title, "Заголовок жирным")}
          ${tgl(`data-pa="dc-flag" data-k="short_links"`, dc.short_links !== false, "Короткие ссылки", "«Больше артов: https://…» превращается в кликабельное «Больше артов».")}</div>
        <div class="field"><span class="lbl">Лимит файла на сервере ${hint("10 МБ — сервер без бустов. С бустами Discord разрешает больше.")}</span>${segP(`data-pa="dc-mb"`, [["10", "10 МБ"], ["50", "50 МБ, буст 2"], ["100", "100 МБ, буст 3"]], String(dc.max_file_mb))}</div></section>`;
    }

    if (sec === "vk") {
      const vkJob = s.info.vk_job, vkRun = vkJob && vkJob.state === "running";
      const viaBrowser = vk.method === "browser";
      h += `<section class="card setc"><div class="head"><h2>${C().pbadge("vk")} ВКонтакте ${hint("Только SFW. Запись на стене группы от имени группы — нужен вход админа.")}</h2>
          ${segP(`data-pa="vk-method"`, [["browser", "Через браузер"], ["api", "Через API"]], viaBrowser ? "browser" : "api")}</div>${err("vk")}${ok("vk")}`;
      if (vkRun) h += `<div class="callout"><p>${esc(vkJob.message || "Работаю…")}</p><div class="progress thin" style="flex:1 1 100%"><div style="width:${Math.round((vkJob.progress || 0) * 100)}%"></div></div></div>`;
      else if (vkJob && vkJob.state === "error") {
        h += `<p class="err">${esc(vkJob.error)}</p>`;
        const m = /снимок экрана: ([\w.-]+\.png)/.exec(vkJob.error || "");
        if (m) h += `<figure class="shot"><img src="/bdebug/${encodeURIComponent(m[1])}?t=${tok()}" alt="Снимок экрана при ошибке">
          <figcaption class="muted small">Так выглядела страница в момент ошибки. <a href="#" data-pa="open-debug">Открыть папку со снимками</a></figcaption></figure>`;
      } else if (vkJob && vkJob.state === "done" && vkJob.message) h += `<p class="ok small">${esc(vkJob.message)}</p>`;
      if (viaBrowser) {
        h += `<div class="status">${vk.browser_ok ? badge("ok", "✓ Вход выполнен") : badge("off", "Вход не выполнен")}
            <span class="muted small">отдельный профиль Edge ${hint("Приложение открывает Edge (или Chrome) с отдельным профилем и выкладывает запись, как вы руками. Ваш основной браузер и его пароли не трогаются. API и бизнес-профиль не нужны.")}</span><span class="grow1"></span>
            <button class="btn small ${vk.browser_ok ? "ghost" : "primary"}" data-pa="vk-blogin" ${vkRun ? "disabled" : ""}>${vk.browser_ok ? "Сменить аккаунт" : "Войти во ВКонтакте"}</button></div>
          <div class="row" style="align-items:flex-end"><div class="field" style="flex:1 1 260px"><label for="vkbgroup">Группа</label><input id="vkbgroup" value="${esc(vk.group_id)}" placeholder="ссылка vk.com/… или номер группы"></div>
            <button class="btn" data-pa="vk-bgroup">Сохранить</button></div>
          <div class="tgls">${tgl(`data-pa="vk-flag" data-k="show_window"`, vk.show_window, "Показывать окно браузера", "Если ВК спросит капчу, её можно будет ввести.")}
            ${tgl(`data-pa="vk-flag" data-k="include_title"`, vk.include_title, "Заголовок первой строкой")}</div>
          <div class="cardfoot"><button class="btn" data-pa="vk-dry" ${vkRun || !vk.browser_ok || !vk.group_id ? "disabled" : ""}>${ICON.play} Пробный прогон</button>
            <button class="btn ghost" data-pa="vk-dry" data-kind="gif" ${vkRun || !vk.browser_ok || !vk.group_id ? "disabled" : ""} data-tip="Проба с GIF: прикрепит документом, «Опубликовать» не нажимает">с GIF</button>
            <button class="btn ghost" data-pa="vk-dry" data-kind="video" ${vkRun || !vk.browser_ok || !vk.group_id ? "disabled" : ""} data-tip="Проба с коротким видео (нужен ffmpeg): «Опубликовать» не нажимает">с видео</button>
            <span class="muted small">Вставит текст и картинку, но «Опубликовать» не нажмёт ${hint("Откроет группу, заполнит запись и сделает снимки экрана двух шагов — чтобы проверить, что всё на месте.")}</span></div>
          <p class="muted small" style="margin-top:12px">Сбор цифр постов ВК — в разделе <a href="#" data-view-link="settings" data-tab="collect">«Статистика» → «Сбор статистики»</a>.</p>
          ${vk.recon_file ? `<div class="callout"><p>${esc(vk.recon_msg || vk.recon_file)}</p>
            <p class="small">Архив лежит в папке снимков браузера. <b>Перед отправкой откройте его и проверьте</b>: ключи и куки вырезаны, но снимки экрана показывают страницу как есть — если там видно что-то личное, скажите, а не пересылайте.</p>
            <button class="btn small" data-pa="open-debug">Открыть папку</button></div>` : ""}
          ${vk.dry_shot ? `<div class="row">${[vk.dry_shot, vk.dry_shot2].filter(Boolean).map((f, i) => `<figure class="shot" style="flex:1 1 300px"><img src="/bdebug/${encodeURIComponent(f)}?t=${tok()}" alt="Снимок пробного прогона"><figcaption class="muted small">${i ? "Шаг 2: здесь была бы кнопка «Опубликовать»" : "Шаг 1: текст и фото в окне записи"}</figcaption></figure>`).join("")}</div>` : ""}`;
      } else if (!p.vk_connected) {
        h += `<details class="how" open><summary>Как подключить через VK ID</summary><ol class="steps"><li>Откройте <a href="#" data-open="https://id.vk.com/about/business/go">кабинет VK ID</a> и создайте приложение типа «Веб».</li>
            <li>Базовый домен: <code>localhost</code>. Доверенный адрес возврата: <code>${esc(vk.redirect_uri.startsWith("http://") ? "https://localhost/callback" : vk.redirect_uri)}</code> — ровно так, без порта.</li>
            <li>Скопируйте сюда ID приложения и нажмите «Войти через VK».</li></ol></details>
          <div class="row" style="align-items:flex-end"><div class="field" style="flex:1 1 200px"><label for="vkapp">ID приложения</label><input id="vkapp" inputmode="numeric" value="${esc(vk.app_id)}" placeholder="51234567"></div>
            <button class="btn ${s.vkStarted ? "" : "primary"}" data-pa="vk-login" ${busy("vk")}>Войти через VK</button></div>
          <details class="more" ${s.vkManual ? "open" : ""}><summary>VK ID требует бизнес-профиль? Вставить готовый ключ доступа</summary><div class="morebody">
            <p class="muted small">Ключ пользователя-админа группы с правами <code>wall</code>, <code>photos</code>, <code>groups</code>, <code>offline</code>. Можно вставить адрес целиком. Ключ хранится только на этом компьютере.</p>
            <div class="row" style="align-items:flex-end"><div class="field" style="flex:1 1 360px"><label for="vktok">Ключ доступа</label><input id="vktok" type="password" placeholder="vk1.a.…"></div>
            <button class="btn" data-pa="vk-token" ${busy("vk")}>Сохранить ключ</button></div></div></details>`;
        if (s.vkStarted) h += `<div class="callout"><p><b>Почти готово.</b> Войдите во ВКонтакте в браузере и разрешите доступ. Браузер покажет «Не удаётся получить доступ к сайту» — так и должно быть. Скопируйте адрес из адресной строки (он начинается с <code>https://localhost/callback?code=…</code>) и вставьте сюда.</p>
            <div class="row" style="align-items:flex-end;flex:1 1 100%"><div class="field" style="flex:1 1 360px"><label for="vkurl">Адрес из браузера</label><input id="vkurl" placeholder="https://localhost/callback?code=…"></div>
            <button class="btn primary" data-pa="vk-finish" ${busy("vk")}>Готово</button></div></div>`;
      } else {
        const sc = (p.vk_scope || "").split(/[\s,]+/).filter(Boolean);
        const miss = sc.length ? ["photos", "wall"].filter((x) => !sc.includes(x)) : [];
        h += `<div class="status">${badge("ok", "✓ Вход выполнен")}${sc.length ? `<span class="muted small">разрешения: ${esc(sc.join(", "))}</span>` : ""}<span class="grow1"></span><button class="btn small ghost" data-pa="vk-logout">Выйти</button></div>
          ${miss.length ? `<div class="callout"><p><b>Не хватает разрешений: ${miss.map((x) => x === "photos" ? "«Фотографии»" : "«Стена»").join(" и ")}.</b> В кабинете VK ID откройте приложение → «Доступы», включите «Фотографии», «Стена» и «Сообщества». Потом выйдите здесь и войдите заново.</p></div>` : ""}
          <div class="row" style="align-items:flex-end"><div class="field" style="flex:1 1 260px"><label for="vkgroup">Группа</label><input id="vkgroup" value="${esc(vk.group_id)}" placeholder="ссылка vk.com/… или id группы"></div>
            <button class="btn" data-pa="vk-group" ${busy("vk")}>Проверить и сохранить</button></div>
          ${vk.group_name ? `<div class="tgls">${tgl(`data-pa="vk-flag" data-k="include_title"`, vk.include_title, "Заголовок первой строкой")}</div>` : ""}`;
      }
      h += `</section>`;
    }

    if (sec === "e6ai") {
      const e6 = p.e6ai;
      h += `<section class="card setc"><div class="head"><h2>${C().pbadge("e6ai")} ${BN()} ${hint("Оригиналы без сжатия; теги — постоянные плюс подобранные теггером; рейтинг s / q / e по рейтингу поста; в источники — ссылки на этот пост в других местах. Публикуется последним.")}</h2>
          ${e6.enabled && e6.has_key ? badge("ok", "✓ " + esc(e6.username)) : badge("off", "не подключено")}</div>${err("e6")}${ok("e6")}
        <div class="row" style="align-items:center;gap:12px"><span class="small">Куда загружать ${hint("Движок у e6ai и e621 один, поэтому форма тегов, теггер, проверка, правки модераторов и цифры работают одинаково. Аккаунт, API-ключ, постоянные теги и база тегов у каждого сайта свои — при переключении подставляются сами.")}</span>
          ${segP(`data-pa="e6-site"`, [["e6ai", "e6ai"], ["e621", "e621"]], BN())}</div>
        ${BN() === "e621" ? `<div class="callout warn"><p><b>На e621 ИИ-арт запрещён правилами сайта</b> (кроме узких исключений вроде фонов) — для ИИ-работ есть e6ai. Такие загрузки удалят, а аккаунт могут заблокировать. Загружайте на e621 только работы, которые подходят под его правила. Проверьте и постоянные теги: <code>ai_generated</code> на e621 сразу пометит работу как ИИ.</p></div>` : ""}
        <div class="row" style="align-items:flex-end"><div class="field" style="flex:1 1 200px"><label for="e6user">Имя пользователя на ${BN()}</label><input id="e6user" value="${esc(e6.username)}" placeholder="AnotherNeko"></div>
          <div class="field" style="flex:2 1 260px"><label for="e6key">API-ключ ${hint("На " + BN() + ": Account → Manage API Access.")}</label><input id="e6key" type="password" placeholder="${e6.has_key ? "сохранён" : ""}"></div>
          <button class="btn" data-pa="e6-save" ${busy("e6")}>Проверить и сохранить</button></div>
</section>
        <section class="card setc"><div class="head"><h2>Теги и описание</h2></div>
          <div class="field"><label for="e6const">Постоянные теги для ${BN()} ${hint(BN() === "e621" ? "Добавляются к каждой загрузке на e621: тег художника (artist), инструмент и т.п. У e6ai свои постоянные теги." : "Добавляются к каждой загрузке: ваш director-тег, инструмент и т.п. У e621 свои постоянные теги.")}</label>
            <input id="e6const" value="${esc((e6.constant_tags || []).join(" "))}" placeholder="director_name stable_diffusion"></div>
          <div class="row" style="align-items:center"><div class="field" style="flex:0 1 220px"><label for="e6pre">Отмечать теги теггера от, % ${hint("Сразу отмечаются теги не ниже 50% уверенности, даже если здесь меньше. Мелкие детали — пирсинг, украшения, татуировки — не отмечаются никогда: теггер их часто путает.")}</label><input id="e6pre" type="number" min="20" max="95" value="${Math.round(e6.preselect * 100)}"></div>
            <div class="tgls" style="flex:1 1 360px"><label class="tgl"><input type="checkbox" id="e6src" ${e6.with_sources ? "checked" : ""}><span>Ссылки на пост — в источники</span></label>
              <label class="tgl"><input type="checkbox" id="e6desc" ${e6.with_description ? "checked" : ""}><span>Текст поста — в описание</span></label></div></div>
          <div class="cardfoot"><button class="btn primary" data-pa="e6-opts">Сохранить</button></div></section>
        <p class="muted small">Теггер, база тегов и правки модераторов — в разделе <a href="#" data-view-link="settings" data-tab="tags">«Теги»</a>.</p>`;
    }


    if (sec === "tags") {   // теггер — для хэштегов и e6ai / e621; база тегов и правки модераторов — для e6ai / e621
      const tgr = s.tagger || {}, tjob = tgr.job, trun = tjob && tjob.state === "running";
      const mods = (s.info && s.info.modules) || {};
      h += mods.tagger === false ? `<section class="card setc"><div class="head"><h2>Подбор тегов выключен</h2></div>
          <p class="muted">Теги к постам вписываются вручную. Включить — в разделе <a href="#" data-view-link="settings" data-tab="features">«Функции»</a>.</p></section>`
        : (() => {
          const ch = tgr.choice || {}, g = ch.gpu, tor = tgr.torch;
          const where = !tgr.ready ? "" : tgr.on_cpu ? `<span class="warnline">процессор — медленно и нагружает компьютер</span>`
            : tor && tor.device ? `видеокарта ${esc(tor.device)}` : tgr.device && tgr.device !== "cpu" ? `видеокарта ${esc(tgr.device)}` : "видеокарта (проверится при первой картинке)";
          const card = g ? `${esc(g.name)}${g.vram_gb ? ` · ${String(Math.round(g.vram_gb)).replace(".", ",")} ГБ` : ""} · драйвер ${esc(g.driver || "?")}` : "видеокарта NVIDIA не найдена";
          return `<section class="card setc"><div class="head"><h2>Теггер JTP PILOT² ${hint("Подбирает теги e621 по картинке на видеокарте NVIDIA. Ставится один раз: PyTorch под вашу видеокарту и драйвер (около 3 ГБ) и модель (около 1,7 ГБ) с HuggingFace. Работает в отдельном процессе и закрывается сам через 2 минуты без дела.")}</h2>
          ${tgr.ready ? badge("ok", "✓ Установлен") : badge("off", "не установлен")}</div>${err("e6")}
          <div class="kvlist"><div><span>Видеокарта</span><span>${card}</span></div>
            ${tgr.ready ? `<div><span>Где считает</span><span>${where}</span></div>` : ch.build ? `<div><span>Поставится</span><span>PyTorch ${esc(ch.build)}</span></div>` : ""}
            ${tor && tor.torch ? `<div><span>PyTorch</span><span>${esc(tor.torch)}${tor.cuda ? " · CUDA " + esc(tor.cuda) : ""}</span></div>` : ""}
            ${tgr.loaded ? `<div><span>Сейчас</span><span>модель загружена — освободится сама через 2 минуты без дела</span></div>` : ""}</div>
          ${tgr.advice || (!tgr.ready && !ch.ok && ch.why) ? `<div class="callout warn"><p>${esc(tgr.advice || (ch.why.charAt(0).toUpperCase() + ch.why.slice(1) + ". " + (ch.advice || "")))}</p></div>` : ""}
          ${tgr.ready ? `<p class="muted small">Теги подбираются сами, как только вы добавите картинку в пост.</p>` : ""}
          ${tgr.ready && tgr.on_cpu ? tgl(`data-pa="tag-cpuok"`, !!(s.info.settings || {}).tagger_cpu_ok, "Всё равно подбирать на процессоре", "Без этого на процессоре теги не подбираются: одна картинка — десятки секунд полной загрузки.") : ""}
          <div class="cardfoot" style="border:0;padding:0;flex-wrap:wrap;gap:10px">
            ${!tgr.ready ? (ch.build === "cpu" ? `<button class="btn" data-pa="tag-install" data-cpu="1" ${trun ? "disabled" : ""}>Установить для процессора</button>`
              : `<button class="btn primary" data-pa="tag-install" ${trun || ch.build === null ? "disabled" : ""}>Установить теггер</button><span class="muted small">10–30 минут, зависит от интернета</span>`) : ""}
            ${tgr.ready && tgr.on_cpu && ch.ok ? `<button class="btn primary" data-pa="tag-install" data-re="1" ${trun ? "disabled" : ""}>Переустановить PyTorch</button>` : ""}
            <button class="btn ghost" data-pa="tag-check" ${trun ? "disabled" : ""}>Проверить видеокарту</button>
            ${tgr.loaded ? `<button class="btn ghost" data-pa="tag-unload">Выгрузить сейчас</button>` : ""}</div>`;
        })() + `
          ${trun ? `<div class="callout"><p>${esc(tjob.message || "Устанавливаю…")}</p><div class="progress thin" style="flex:1 1 100%"><div style="width:${Math.round((tjob.progress || 0) * 100)}%"></div></div></div>` : ""}
          ${tjob && tjob.state === "error" ? `<pre class="err small" style="white-space:pre-wrap">${esc(tjob.error)}</pre>` : ""}</section>`;
      h += mods.e6ai === false ? `<p class="muted small">База тегов и правки модераторов нужны для загрузки на e6ai / e621 — площадка выключена.</p>`
        : `${e6dbCard()}${e6learnCard()}`;
    }

    if (sec === "collect") {   // сбор статистики: что собираем, новости, прогноз, ВК через браузер
      const mods = (s.info && s.info.modules) || {}, on = (k) => mods[k] !== false;
      const vol = (s.info.settings || {}).news_volume || "normal";
      h += `<section class="card setc"><div class="head"><h2>Сбор статистики ${hint("Замеры просмотров свежих постов Telegram, цифры с сайтов (Bluesky, DeviantArt, e6ai / e621, ВК), сверка правок модераторов. Работает и при закрытом окне, если включена фоновая отправка.")}</h2></div>
          <div class="modlist">${tgl(`data-mod="collect"`, on("collect"), "<b>Собирать цифры</b> <span class=\"muted small\">замеры просмотров, цифры с сайтов, сверка правок модераторов</span>")}
            ${tgl(`data-mod="news"`, on("news"), "<b>Новости по свежим артам</b> <span class=\"muted small\">колокольчик вверху: хиты, всплески реакций, комментарии</span>")}
            ${tgl(`data-mod="forecast"`, on("forecast"), "<b>Прогноз</b> <span class=\"muted small\">ожидаемые просмотры и лучшее время на экране поста</span>")}</div>
          ${on("news") ? `<div class="row" style="align-items:center;gap:12px"><span class="small">Сколько новостей</span>
            <span class="seg sm">${[["few", "Мало"], ["normal", "Средне"], ["many", "Много"]].map(([k, l]) => `<button class="${k === vol ? "on" : ""}" data-nvol="${k}" aria-pressed="${k === vol}">${l}</button>`).join("")}</span></div>` : ""}
          <p class="muted small">Цифры Bluesky, DeviantArt и e6ai / e621 берутся по входу, который настроен у площадки, — отдельно здесь ничего не нужно. Каналы Telegram — в разделе <a href="#" data-view-link="settings" data-tab="import">«Статистика» → «Telegram»</a>.</p></section>`;
      if (on("vk")) {
        const vkJob = s.info.vk_stats_job, vkRun = (vkJob && vkJob.state === "running") || (s.info.vk_job && s.info.vk_job.state === "running");
        h += `<section class="card setc"><div class="head"><h2>${C().pbadge("vk")} ВКонтакте</h2></div>${err("vk")}
          ${!vk.browser_ok ? `<p class="muted small">Нужен вход во ВК в браузере Crosspost: <a href="#" data-view-link="settings" data-tab="vk">«Площадки» → ВКонтакте</a>.</p>` : ""}
          ${vkJob && vkJob.state === "running" ? `<div class="callout"><p>${esc(vkJob.message || "Собираю…")}</p><div class="progress thin" style="flex:1 1 100%"><div style="width:${Math.round((vkJob.progress || 0) * 100)}%"></div></div></div>` : ""}
          <p class="small" style="margin:0">Как собираем ${hint("Crosspost открывает «Статистика → Посты → Контент» в своём браузере и читает таблицу: охват, просмотры, лайки, комментарии, «поделились», закладки по каждому посту. Только читает — ничего не нажимает, кроме прокрутки. Нужно, чтобы аккаунт в браузере Crosspost был админом группы.")}</p>
          <div class="row" style="align-items:flex-end"><div class="field" style="flex:1 1 360px"><label for="vkstats">Ссылка на страницу статистики постов</label>
            <input id="vkstats" placeholder="https://vk.ru/groups/dashboard/@группа?sectionId=top_posts&amp;subsectionId=stat_board_content" value="${esc(vk.stats_url || "")}"></div>
            <button class="btn primary" data-pa="vk-stats" ${vkRun || !vk.browser_ok ? "disabled" : ""}>Собрать сейчас</button></div>
          <div class="tgls">${tgl(`data-pa="vk-flag" data-k="stats_auto"`, vk.stats_auto !== false, "Собирать автоматически", "Раз в 3 часа, пока работает фоновая отправка. Браузер открывается без окна.")}</div>
          ${vk.stats_err ? `<p class="err small">Последний сбор (${esc(new Date(vk.stats_at).toLocaleString("ru-RU"))}): ${esc(vk.stats_err)}</p>`
            : vk.stats_at ? `<p class="muted small">Последний сбор: ${esc(new Date(vk.stats_at).toLocaleString("ru-RU"))} · ${esc(vk.stats_msg || "")}</p>` : ""}
          <details class="more"><summary>Если сбор перестал работать</summary><div class="morebody">
            <p class="small">«Снять образец» запишет, как страница статистики получает цифры (ключи и куки вырезаются), — пришлите архив разработчику.</p>
            <button class="btn small" data-pa="vk-recon" ${vkRun || !vk.browser_ok ? "disabled" : ""}>Снять образец</button></div></details></section>`;
      }
    }

    if (sec === "hashtags") {
      const ht = p.hashtags;
      h += `<section class="card setc"><div class="head"><h2>Хэштеги ${hint("Хэштег рейтинга, персонажи и франшизы из тегов " + BN() + ", виды, которые нашёл теггер (#fox, #cat — без общих вроде #mammal), и постоянные. Помогают найти работу поиском в Telegram, Discord и ВК.")}</h2>${ok("ht")}</div>
        ${tgl(`id="htOn"`, ht.enabled, "Добавлять хэштеги к постам")}
        <div class="row" style="align-items:flex-end">
          <div class="field" style="flex:1 1 130px"><label for="htS">Для SFW</label><input id="htS" value="${esc(ht.rating_tags.sfw)}" placeholder="sfw"></div>
          <div class="field" style="flex:1 1 130px"><label for="htQ">Для эротики</label><input id="htQ" value="${esc(ht.rating_tags.erotic)}" placeholder="nsfw"></div>
          <div class="field" style="flex:1 1 130px"><label for="htE">Для откровенного</label><input id="htE" value="${esc(ht.rating_tags.explicit)}" placeholder="nsfw"></div>
          <div class="field" style="flex:1 1 110px"><label for="htN">Видов, не больше</label><input id="htN" type="number" min="0" max="10" value="${ht.max_species}"></div></div>
        <div class="field"><label for="htC">Постоянные хэштеги</label><input id="htC" value="${esc((ht.constant || []).join(" "))}" placeholder="aiart furryart"></div>
        <div class="row" style="align-items:center"><div class="field"><span class="lbl">Где в посте</span>${segP(`data-pa="ht-pos"`, [["start", "Первой строкой"], ["end", "В конце"]], ht.position)}</div>
          <div class="tgls" style="flex:1 1 300px">${[["telegram", "Telegram"], ["discord", "Discord"], ["vk", "ВКонтакте"], ["bluesky", "Bluesky"], ["deviantart", "DeviantArt (в описании)"]].map(([k, l]) => `<label class="tgl"><input type="checkbox" id="htU-${k}" ${ht.use[k] !== false ? "checked" : ""}><span>${l}</span></label>`).join("")}</div></div>
        <div class="cardfoot"><button class="btn primary" data-pa="ht-save">Сохранить</button></div></section>`;
    }

    if (sec === "signatures") {
      const sp = sg.platforms || {};
      const SIGP = [["telegram", "Telegram", "Ссылка становится кликабельным словом."], ["discord", "Discord", "Ссылка становится кликабельным словом, без карточек-превью."], ["vk", "ВКонтакте", "ВК не прячет ссылку под слово — получится «Текст: https://…»."],
        ["bluesky", "Bluesky", "Ссылка становится кликабельным словом. Весь пост Bluesky — до 300 символов, подпись лучше короткая."],
        ["deviantart", "DeviantArt", "Уходит в описание работы как «Текст: https://…»."]];
      h += `<section class="card setc"><div class="head"><h2>Подписи ${hint("Дописываются в конец поста. Ссылка под словом: [Больше артов](https://t.me/канал). Обычная ссылка тоже работает.")}</h2>${ok("sig")}</div>
        ${SIGP.map(([k, l, how]) => { const v = sp[k] || {}; return `<div class="sigblk">
          <div class="sighead">${C().pbadge(k)}<label class="tgl"><input type="checkbox" id="siguse-${k}" ${sg.use[k] !== false ? "checked" : ""}><span><b>${l}</b> ${hint(how)}</span></label>
            ${k !== "telegram" ? `<button class="btn small ghost" data-pa="sig-copy" data-k="${k}">Скопировать из Telegram</button>` : ""}</div>
          <div class="row"><div class="field" style="flex:1 1 280px"><label for="sig-${k}-sfw">SFW-посты</label><textarea id="sig-${k}-sfw" rows="4" placeholder="[Больше артов](https://t.me/…)">${esc(v.sfw || "")}</textarea></div>
            ${k === "vk" ? "" : `<div class="field" style="flex:1 1 280px"><label for="sig-${k}-nsfw">18+ посты</label><textarea id="sig-${k}-nsfw" rows="4">${esc(v.nsfw || "")}</textarea></div>`}</div></div>`; }).join("")}
        <div class="cardfoot"><button class="btn primary" data-pa="sig-save">Сохранить подписи</button></div></section>`;
    }

    if (sec === "light") {
      h += `<section class="card setc"><div class="head"><h2>JPEG-превью ${hint("JPEG из оригинала для Telegram, Discord и ВК. Готовится сразу после добавления файла.")}</h2>${ok("light")}</div>
        <div class="row"><div class="field" style="flex:1 1 150px"><label for="lscale">Размер от оригинала, %</label><input id="lscale" type="number" min="20" max="100" value="${Math.round(li.scale * 100)}"></div>
          <div class="field" style="flex:1 1 150px"><label for="lq">Качество JPEG</label><input id="lq" type="number" min="60" max="100" value="${li.quality}"></div>
          <div class="field" style="flex:0 1 160px"><label for="lbg">Фон вместо прозрачности</label><input id="lbg" type="color" value="${esc(li.background)}"></div></div>
        <div class="tgls"><label class="tgl"><input type="checkbox" id="lsub" ${li.subsampling === "4:4:4" ? "checked" : ""}><span>Чистые цветные края ${hint("Цветовая субдискретизация 4:4:4 — без размытых цветных контуров, файл чуть больше.")}</span></label>
          <label class="tgl"><input type="checkbox" id="lsharp" ${li.sharpen ? "checked" : ""}><span>Лёгкая резкость после уменьшения</span></label></div>
        <div class="cardfoot"><button class="btn primary" data-pa="light-save">Сохранить</button></div></section>
        <p class="muted small">Готовые превью складываются в папку «Готовые файлы» — её можно сменить в «Настройки» → «Пост» → «Готовые файлы».</p>`;
    }
    if (sec === "files") h += filesHtml(err, ok, busy, tgl, hint);
    if (sec === "media") h += mediaHtml();
    if (sec === "bluesky") h += blueskyHtml(err, ok, busy);
    if (sec === "deviantart") h += daHtml(err, ok, busy);
    return h;
  }
  const ratingSel = (id, cur, opts = DEST_RATING) => `<select id="${id}">${opts.map(([k, l]) => `<option value="${k}" ${cur === k ? "selected" : ""}>${l}</option>`).join("")}</select>`;
  function blueskyHtml(err, ok, busy) {
    const p = S().pub, b = p.bluesky, ss = p.bluesky_session || {}, hint = C().hint;
    let h = `<section class="card setc"><div class="head"><h2>${C().pbadge("bluesky")} Bluesky ${hint("Пост от вашего аккаунта. До 4 картинок (JPEG-превью до 1 МБ) или одно видео до 3 минут; GIF уходит видео. Текст — до 300 символов. 18+ помечается: эротика — nudity, откровенное — porn.")}</h2>
      ${ss.ok ? `<span class="badge ok">✓ @${esc(ss.handle)}</span>` : `<span class="badge off">не подключено</span>`}</div>${err("bs")}${ok("bs")}`;
    if (!ss.ok) {
      h += `<ol class="steps"><li>В Bluesky: <b>Settings → Privacy and security → App passwords → Add App Password</b>. Назовите, например, Crosspost.</li>
          <li>Вставьте сюда ник и этот пароль. Основной пароль от аккаунта не нужен — и не вводите его.</li></ol>
        <div class="row" style="align-items:flex-end"><div class="field" style="flex:1 1 200px"><label for="bshandle">Ник</label><input id="bshandle" value="${esc(b.handle || "")}" placeholder="fennec.bsky.social"></div>
          <div class="field" style="flex:1 1 220px"><label for="bspass">Пароль приложения</label><input id="bspass" type="password" placeholder="xxxx-xxxx-xxxx-xxxx" autocomplete="off"></div>
          <button class="btn primary" data-pa="bs-login" ${busy("bs")}>Войти</button></div>
        <p class="note">Пароль приложения не сохраняется — только сессия входа, на этом компьютере. Отозвать её можно там же, в App passwords.</p>`;
    } else {
      h += `<div class="row" style="align-items:flex-end"><div class="field" style="flex:0 1 220px"><label for="bsrating">Какие посты сюда</label>${ratingSel("bsrating", b.rating)}</div>
          <div class="field" style="flex:0 1 160px"><label for="bslang">Язык постов</label><input id="bslang" value="${esc((b.langs || []).join(" "))}" placeholder="ru"></div></div>
        <div class="tgls"><label class="tgl"><input type="checkbox" id="bson" ${b.enabled ? "checked" : ""}><span>Публиковать в Bluesky</span></label>
          <label class="tgl"><input type="checkbox" id="bstitle" ${b.include_title !== false ? "checked" : ""}><span>Заголовок первой строкой</span></label>
          <label class="tgl"><input type="checkbox" id="bsalt" ${b.alt_from_title !== false ? "checked" : ""}><span>Заголовок — в описание картинки (alt)</span></label></div>
        <div class="cardfoot"><button class="btn primary" data-pa="bs-save">Сохранить</button><button class="btn ghost" data-pa="bs-logout">Выйти</button></div>`;
    }
    return h + `</section>`;
  }
  function daHtml(err, ok, busy) {
    const s = S(), p = s.pub, d = p.deviantart, ss = p.deviantart_session || {}, hint = C().hint;
    let h = `<section class="card setc"><div class="head"><h2>${C().pbadge("deviantart")} DeviantArt ${hint("Официальный API DeviantArt. Каждая картинка — отдельная работа. Эротика уходит как Mature (moderate, nudity); откровенное правила DeviantArt запрещают — такие посты сюда не уходят. Видео DeviantArt не принимает.")}</h2>
      ${ss.ok ? `<span class="badge ok">✓ ${esc(ss.username || "подключено")}</span>` : `<span class="badge off">не подключено</span>`}</div>${err("da")}${ok("da")}`;
    if (!ss.ok) {
      h += `<ol class="steps"><li>Откройте <a href="#" data-open="https://www.deviantart.com/developers/apps">deviantart.com/developers/apps</a> → <b>Register Application</b>.</li>
          <li>В <b>OAuth2 Redirect URI Whitelist</b> впишите <code>${esc(d.redirect_uri)}</code>, остальное — как угодно. Сохраните.</li>
          <li>Скопируйте сюда <b>client_id</b> и <b>client_secret</b> и нажмите «Войти» — откроется страница DeviantArt, нажмите Authorize.</li></ol>
        <div class="row" style="align-items:flex-end"><div class="field" style="flex:1 1 140px"><label for="daid">client_id</label><input id="daid" value="${esc(d.client_id || "")}" inputmode="numeric"></div>
          <div class="field" style="flex:2 1 260px"><label for="dasecret">client_secret</label><input id="dasecret" type="password" placeholder="${d.has_secret ? "сохранён" : ""}" autocomplete="off"></div>
          <button class="btn primary" data-pa="da-login" ${busy("da")}>Войти через DeviantArt</button></div>`;
      if (s.daWaiting) h += `<div class="callout"><p>Жду ответ DeviantArt… Если браузер показал ошибку адреса, скопируйте адрес из строки браузера сюда:</p>
          <div class="row" style="align-items:flex-end;flex:1 1 100%"><div class="field" style="flex:1 1 300px"><input id="daurl" placeholder="http://localhost:8765/callback?code=…"></div><button class="btn" data-pa="da-finish">Готово</button></div></div>`;
      h += `<p class="note">client_secret — ключ вашего приложения DeviantArt, а не пароль. Хранится только на этом компьютере.</p>`;
    } else {
      const gal = s.daGal;
      if (ss.need_relogin) h += `<div class="callout warn"><p>Нужно войти заново: при прошлом входе приложение не просило право смотреть папки галереи и цифры работ (просмотры, избранное). Без него не выбрать папку и не собрать статистику DeviantArt.</p>
          <button class="btn small primary" data-pa="da-login" ${busy("da")}>Войти заново</button></div>`;
      if (s.daWaiting) h += `<div class="callout"><p>Жду ответ DeviantArt… Если браузер показал ошибку адреса, скопируйте адрес из строки браузера сюда:</p>
          <div class="row" style="align-items:flex-end;flex:1 1 100%"><div class="field" style="flex:1 1 300px"><input id="daurl" placeholder="http://localhost:8765/callback?code=…"></div><button class="btn" data-pa="da-finish">Готово</button></div></div>`;
      h += `<div class="row" style="align-items:flex-end"><div class="field" style="flex:0 1 220px"><label for="darating">Какие посты сюда</label>${ratingSel("darating", d.rating, [["sfw", "Только SFW"], ["all", "SFW и эротика"]])}</div>
          <div class="field" style="flex:1 1 220px"><label for="dagal">Папка галереи</label>${gal ? `<select id="dagal"><option value="">Без папки (Featured)</option>${gal.map((g) => `<option value="${esc(g.id)}" ${d.gallery_id === g.id ? "selected" : ""}>${esc(g.name)}</option>`).join("")}</select>`
            : `<div style="display:flex;gap:8px;align-items:center"><span class="muted small">${esc(d.gallery_name || "Featured")}</span><button class="btn small" data-pa="da-gal">Выбрать…</button></div>`}</div></div>
        <div class="tgls"><label class="tgl"><input type="checkbox" id="daon" ${d.enabled ? "checked" : ""}><span>Публиковать на DeviantArt</span></label>
          <label class="tgl"><input type="checkbox" id="daai" ${d.is_ai_generated !== false ? "checked" : ""}><span>Отмечать как созданное ИИ ${hint("Правила DeviantArt требуют отмечать ИИ-работы. Без отметки работу могут скрыть.")}</span></label>
          <label class="tgl"><input type="checkbox" id="danoai" ${d.noai !== false ? "checked" : ""}><span>Запретить использовать для обучения ИИ</span></label>
          <label class="tgl"><input type="checkbox" id="daorig" ${d.originals !== false ? "checked" : ""}><span>PNG-оригиналы (до 30 МБ), иначе JPEG-превью</span></label>
          <label class="tgl"><input type="checkbox" id="datags" ${d.tags_from !== "none" ? "checked" : ""}><span>Теги работы — из хэштегов поста</span></label>
          <label class="tgl"><input type="checkbox" id="dadl" ${d.allow_free_download ? "checked" : ""}><span>Разрешить скачивание оригинала</span></label></div>
        <div class="cardfoot"><button class="btn primary" data-pa="da-save">Сохранить</button><button class="btn ghost" data-pa="da-logout">Выйти</button>
          <span class="muted small">Вход действует 3 месяца, потом DeviantArt попросит войти заново.</span></div>`;
    }
    return h + `</section>`;
  }
  function filesHtml(err, ok, busy, tgl, hint) {
    const o = S().outputs;
    if (!o) return `<section class="card setc"><div class="head"><h2>Готовые файлы</h2></div><p class="muted">Загрузка…</p></section>`;
    const u = o.usage, pl = C().plural;
    const HOW = { default: "по умолчанию, в каталоге программы", custom: "ваша папка", "default-fallback": "ваша папка недоступна — пока используется папка по умолчанию",
                  fallback: "в каталог программы писать нельзя — используется запасная папка" };
    const kind = (k, name) => { const x = u.by_kind[k];
      return tgl(`id="f${k}"`, o[k], `${name} <span class="muted small">· папка «${esc(o.folders[k])}», ${x.files ? `${x.files} ${pl(x.files, "файл", "файла", "файлов")}, ${mb(x.bytes)}` : "пока пусто"}</span>`); };
    return `<section class="card setc"><div class="head"><h2>Готовые файлы ${hint("Сюда программа складывает копии того, что сделала сама: JPEG-превью, GIF и видео из редактора, перекодированное и сжатое под площадки видео. Файлы отсюда программа не удаляет — это ваша библиотека. Рабочие копии (кеш) по-прежнему чистятся.")}</h2>${ok("files")}</div>
      ${err("files")}
      <div class="field"><label>Сейчас</label><div class="pathbox"><code>${esc(o.dir)}</code></div><span class="muted small">${esc(HOW[o.how] || "")}</span></div>
      <div class="row" style="align-items:flex-end"><div class="field" style="flex:2 1 320px"><label for="fdir">Своя папка</label>
        <input id="fdir" placeholder="${esc(o.default)}" value="${esc(o.custom ? o.dir : "")}"></div>
        <button class="btn" data-pa="files-pick">Выбрать…</button></div>
      <div class="tgls">${kind("jpeg", "JPEG-превью")}${kind("gif", "GIF")}${kind("video", "Видео")}
        ${tgl(`id="fdate"`, o.by_date, "Раскладывать по датам", "Внутри каждой папки — подпапка с датой сохранения (2026-10-22).")}
        ${tgl(`id="fmove"`, true, "При смене папки перенести уже сохранённые файлы")}</div>
      <div class="cardfoot"><button class="btn primary" data-pa="files-save" ${busy("files")}>Сохранить</button>
        <button class="btn" data-pa="files-open">Открыть папку</button>
        ${o.custom ? `<button class="btn ghost" data-pa="files-default" data-tip="Вернуть папку «Файлы» в каталоге программы">По умолчанию</button>` : ""}
        <span class="muted small">${u.files ? `Всего ${u.files} ${pl(u.files, "файл", "файла", "файлов")}, ${mb(u.bytes)}` : "Файлов пока нет"}</span></div></section>`;
  }
  function mediaHtml() {
    const m = S().mediaSt, j = m && m.job, run = j && j.state === "running";
    const row = (plat, text) => `<div class="mrow">${C().pbadge(plat, { sm: true })}<span>${text}</span></div>`;
    let h = `<section class="card setc"><div class="head"><h2>ffmpeg ${C().hint("Программа для видео. Нужна, только когда файл приходится переделывать под площадку, и для кадра-миниатюры у видео. Ставится в папку приложения, в систему ничего не прописывает.")}</h2>
      ${!m ? "" : m.ready ? `<span class="badge ok">✓ установлен</span>` : `<span class="badge off">не установлен</span>`}</div>`;
    if (!m) h += `<p class="muted">Проверяю…</p>`;
    else if (m.ready) h += `<p class="muted small">${esc(m.version || "")}${m.own ? " · в папке приложения" : " · найден в системе"}</p>`;
    else h += `<p class="muted small">Без ffmpeg GIF и MP4 уходят как есть туда, где их принимают. Если файл нужно ужать или перекодировать, такие площадки будут пропущены с пояснением.</p>`;
    if (run) h += `<div class="callout"><p>${esc(j.message || "Скачиваю…")}</p><div class="progress thin" style="flex:1 1 100%"><div style="width:${Math.round((j.progress || 0) * 100)}%"></div></div></div>`;
    if (j && j.state === "error") h += `<p class="err small">${esc(j.error || "")}</p>`;
    if (m && !m.ready && m.can_install) h += `<div class="cardfoot" style="border:0;padding:0"><button class="btn primary" data-pa="media-install" ${run ? "disabled" : ""}>Установить ffmpeg</button><span class="muted small">около 150 МБ с GitHub, пара минут</span></div>`;
    h += `</section><section class="card setc"><div class="head"><h2>Что уходит на площадки</h2></div>
      <p class="muted small" style="margin-top:0">Файл уходит как есть. Переделывается только то, что площадка иначе не примет.</p>
      <p class="muted small">Правка видео и GIF: в новом посте на плитке файла есть кнопка «Изменить» — обрезка по времени и рамкой, поворот, отражение, разрешение, переключатель GIF. Файл в посте заменяется готовым, исходный не меняется.</p>
      <div class="mlist">
        ${row("telegram", "GIF и MP4 — как есть. Ужимается, если больше 50 МБ (при связи MTProto лимит 2 ГБ). Видео не в H.264 (HEVC, MOV, WebM) перекодируется, иначе Telegram не покажет его в ленте.")}
        ${row("discord", "Как есть, если влезает в лимит сервера из настроек Discord. Иначе ужимается в MP4; тяжёлый GIF тоже становится MP4: в разы легче.")}
        ${row("vk", "Видео — видеозаписью группы, GIF — документом, проигрывается в ленте. Без переделки: ВК перекодирует сам. Только при входе по API.")}
        ${row("e6ai", "GIF, MP4 и WebM — как есть. Если " + BN() + " не примет формат (или это MOV), файл перекодируется в WebM и загрузится снова. Тег animated добавляется сам.")}
      </div></section>`;
    return h;
  }

  // ---------------- файлы ----------------
  async function uploadFiles(list) {
    const s = S(), d = draft();
    const files = Array.from(list || []);
    if (!files.length) return;
    s.uploadErr = ""; s.uploading = (s.uploading || 0) + files.length; C().render();
    let bad = 0;
    for (const f of files) {
      try {
        const r = await fetch("/upload?name=" + encodeURIComponent(f.name || "paste.png"), {
          method: "POST", headers: { "X-Token": window.API_TOKEN, "Content-Type": "application/octet-stream" }, body: f });
        const j = await r.json();
        if (!j.ok) throw new Error(j.error);
        const info = j.result;
        d.files.push({ id: info.id, name: info.name, kind: info.kind, width: info.width, height: info.height, size: info.size, duration: info.duration || null, format: info.format || "" });
      } catch (e) { s.uploadErr = (f.name || "файл") + ": " + e.message; C().toast(s.uploadErr, "error"); bad++; }
      s.uploading -= 1;
    }
    const added = files.length - bad;
    if (added > 0) C().toast(added === 1 ? "Файл добавлен" : `Добавлено файлов: ${added}`, "ok");
    saveDraft(); await refreshPlan(); C().render(); pollPrep(); loadTags(true);
  }
  async function pollPrep() {
    const s = S(), ids = draft().files.filter((f) => f.kind === "image").map((f) => f.id);
    if (!ids.length) return;
    try { s.prep = await C().api("post_prep", { ids }); } catch (e) { return; }
    try { s.pre = await C().api("post_preupload", { ids }); } catch (e) { s.pre = {}; }   // PNG в Telegram заранее (MTProto)
    if (s.view === "post" && s.postMode !== "progress") {
      updateBadges();
    }
    const preBusy = Object.values(s.pre || {}).some((v) => typeof v === "number");
    if (Object.values(s.prep).some((v) => v !== "ready") || preBusy) setTimeout(pollPrep, 1000);
  }
  function preBadge(id) {
    const v = (S().pre || {})[id];
    if (v === undefined) return "";
    if (v === "ready") return `<span class="ready" data-pre="${id}" data-tip="PNG-оригинал уже лежит на сервере Telegram — после «Опубликовать» уйдёт сразу">✓ PNG в TG</span>`;
    if (v === "error") return `<span data-pre="${id}" data-tip="Заранее залить не вышло — PNG уйдёт обычным порядком после поста">PNG позже</span>`;
    return `<span data-pre="${id}" data-tip="PNG-оригинал заранее заливается в Telegram">PNG ${Math.round(v * 100)}%</span>`;
  }
  function updateBadges() {
    const s = S();
    document.querySelectorAll("[data-prep]").forEach((el) => {
      if ((s.prep || {})[el.dataset.prep] === "ready") { el.textContent = "✓ превью"; el.className = "ready"; }
    });
    document.querySelectorAll(".gt").forEach((g) => {
      const id = (g.querySelector("[data-prep]") || {}).dataset; if (!id) return;
      const old = g.querySelector("[data-pre]"), html = preBadge(id.prep);
      if (old) { if (html) old.outerHTML = html; else old.remove(); }
      else if (html) g.querySelector(".gm").insertAdjacentHTML("beforeend", html);
    });
  }

  // ---------------- события ----------------
  async function onClick(e) {
    const ws = e.target.closest('[data-set="when"]');
    if (ws) {
      const s0 = S();
      s0.when = ws.dataset.val;
      if (s0.when === "later") {
        if (!s0.bestTimes) { try { s0.bestTimes = await C().api("post_best_times"); } catch (err) { s0.bestTimes = []; } }
        try { s0.worker = await C().api("worker_status"); } catch (err) { s0.worker = null; }
        if (!s0.whenAt && s0.bestTimes.length) s0.whenAt = s0.bestTimes[0].iso;
      }
      paintSide(); scheduleFc(50); return true;
    }
    const gimg = e.target.closest(".gt img");
    if (gimg && !e.target.closest(".x")) {   // клик по картинке в «Файлах» — крупный просмотр
      const f = draft().files[+gimg.closest(".gt").dataset.gi];
      if (f) { S().viewFile = f.id; C().render(); return true; }
    }
    if (e.target.closest("[data-open]")) return false;   // ссылка внутри строки — открыть ссылку, а не строку
    const mo = e.target.closest(".cw .more");
    if (mo) {   // «+N» на ячейке дня: показать весь список дня и прокрутить к нему
      e.preventDefault();
      const s0 = S(); s0.calDay = mo.closest(".cc").dataset.day;
      C().render();
      requestAnimationFrame(() => { const el = document.querySelector(".daylist"); if (el) el.scrollIntoView({ behavior: "smooth", block: "start" }); });
      return true;
    }
    const cw = e.target.closest("[data-cw]");
    if (cw) {   // работа в календаре → шторка: публикация из Crosspost или пост из импорта
      e.preventDefault();
      const it = calItems()[cw.dataset.cw];
      const s1 = S();
      if (it && (s1.calSelMode || e.ctrlKey || e.metaKey || e.shiftKey)) {   // выбор работ для объединения
        const sel = s1.calSel || (s1.calSel = []), i = sel.indexOf(it.key);
        if (i >= 0) sel.splice(i, 1); else sel.push(it.key);
        C().render(); return true;
      }
      if (it && (it.pub_id || it.post_id)) await openDrawer(it.pub_id ? "pub:" + it.pub_id : "post:" + it.post_id);
      else if (it) await openDrawer("cal:" + it.key);
      return true;
    }
    const hs = e.target.closest('[data-set="hf"],[data-set="histMode"]');
    if (hs) {
      const s0 = S();
      if (hs.dataset.set === "hf") s0.hf = hs.dataset.val;
      else { s0.histMode = hs.dataset.val; C().store.set("histMode", s0.histMode); }
      C().render(); return true;
    }
    const cc = e.target.closest(".cc[data-day]");
    if (cc && !e.target.closest(".cs")) {
      const s0 = S();
      s0.calDay = s0.calDay === cc.dataset.day ? null : cc.dataset.day;
      C().render();
      requestAnimationFrame(() => { const el = document.querySelector(".daylist"); if (el && s0.calDay) el.scrollIntoView({ behavior: "smooth", block: "start" }); });
      return true;
    }
    const a = e.target.closest("[data-pa]");
    if (!a) return false;
    const s = S(), api = C().api, act = a.dataset.pa;
    if (a.tagName === "INPUT" || a.tagName === "SELECT") return act === "toggle" ? false : true;   // их обрабатывает onChange
    e.preventDefault();
    const d = draft();
    const guard = async (key, fn) => {
      s.pubBusy = key; s.pubErr = Object.assign({}, s.pubErr, { [key]: "" }); s.pubOk = Object.assign({}, s.pubOk, { [key]: "" }); C().render();
      try { await fn(); } catch (err) { s.pubErr[key] = err.message; }
      s.pubBusy = ""; C().render();
    };
    // значения полей запоминаем сразу: перерисовка (guard) пересоздаёт поля ввода
    const snap = {};
    document.querySelectorAll("#main input, #main textarea, #main select").forEach((el) => { if (el.id) snap[el.id] = el.type === "checkbox" ? el.checked : el.value; });
    const val = (id) => String(snap[id] == null ? "" : snap[id]).trim();
    const saveSec = async (sec, ch) => { s.pub = await api("pub_save", { section: sec, changes: ch }); };
    switch (act) {
      case "e6t": e6toggle(a.dataset.id, a.dataset.row, a.dataset.tag); await e6changed(true); return true;
      case "e6ok": {
        const F = (s.e6form || {})[a.dataset.id];
        e6meta(a.dataset.id).ok = a.dataset.un ? "" : (F ? F.check.sig : "");
        await e6changed(true); return true;
      }
      case "bs-login": await guard("bs", async () => { s.pub = await api("pub_bluesky_login", { handle: val("bshandle"), app_password: val("bspass") }); s.pubOk.bs = "Вход выполнен"; }); return true;
      case "bs-logout": if (!confirm("Выйти из Bluesky?")) return true; s.pub = await api("pub_bluesky_logout"); C().render(); return true;
      case "bs-save": await guard("bs", async () => {
        s.pub = await api("pub_save", { section: "bluesky", changes: { enabled: !!snap.bson, include_title: !!snap.bstitle, alt_from_title: !!snap.bsalt,
          rating: val("bsrating") || "all", langs: val("bslang").split(/[\s,]+/).filter(Boolean).slice(0, 3) } });
        s.pubOk.bs = "Сохранено"; }); return true;
      case "da-login": await guard("da", async () => {
        await api("pub_da_login", { client_id: val("daid"), client_secret: val("dasecret") });
        s.daWaiting = true;
        const poll = async () => {
          let st; try { st = await api("pub_da_state"); } catch (err) { return; }
          if (st.done) { s.daWaiting = false; s.pub = st.settings; s.pubOk = Object.assign({}, s.pubOk, { da: "Вход выполнен" }); C().render(); return; }
          if (st.error) s.pubErr = Object.assign({}, s.pubErr, { da: st.error });
          if (s.daWaiting) setTimeout(poll, 1500);
        };
        setTimeout(poll, 1500);
      }); return true;
      case "da-finish": await guard("da", async () => { s.pub = await api("pub_da_finish", { url: val("daurl") }); s.daWaiting = false; s.pubOk.da = "Вход выполнен"; }); return true;
      case "da-logout": if (!confirm("Выйти из DeviantArt?")) return true; s.pub = await api("pub_da_logout"); C().render(); return true;
      case "da-gal": await guard("da", async () => { s.daGal = await api("pub_da_galleries"); }); return true;
      case "da-save": await guard("da", async () => {
        const gid = snap.dagal !== undefined ? val("dagal") : s.pub.deviantart.gallery_id;
        const gname = gid && s.daGal ? (s.daGal.find((g) => g.id === gid) || {}).name || "" : (gid ? s.pub.deviantart.gallery_name : "");
        s.pub = await api("pub_save", { section: "deviantart", changes: { enabled: !!snap.daon, is_ai_generated: !!snap.daai, noai: !!snap.danoai,
          originals: !!snap.daorig, tags_from: snap.datags ? "hashtags" : "none", allow_free_download: !!snap.dadl,
          rating: val("darating") || "all", gallery_id: gid || "", gallery_name: gname } });
        s.pubOk.da = "Сохранено"; }); return true;
      case "e6-forget": {
        if (!a.dataset.tag && !confirm("Забыть всё, чему научились по правкам модераторов?")) return true;
        s.e6learn = await api("e6_forget", { tag: a.dataset.tag || "" }); C().render(); return true;
      }
      case "e6-never-save": s.e6learn = await api("e6_never", { tags: val("e6never").split(/[\s,]+/).filter(Boolean) }); s.e6learnOk = "Сохранено"; C().render(); return true;
      case "e6-checknow": { a.disabled = true; try { const r = await api("e6_check_now"); s.e6learnOk = r.checked ? `Сверено постов: ${r.checked}` : "Сверять пока нечего — посты сверяются через сутки после загрузки"; } catch (err) { s.e6learnOk = err.message; }
        s.e6learn = await api("e6_learned"); C().render(); return true; }
      case "media-install": {
        try { await api("media_install"); } catch (err) { alert(err.message); }
        const poll = async () => {
          try { s.mediaSt = await api("media_status"); } catch (err) { return; }
          if (s.view === "settings") C().render();
          if (s.mediaSt.job && s.mediaSt.job.state === "running") setTimeout(poll, 1000);
        };
        poll(); return true;
      }
      case "e6copy": {
        const src = a.dataset.id, m = e6meta(src);
        d.files.filter((x) => x.id !== src).forEach((x) => {
          d.e6tags[x.id] = [...(d.e6tags[src] || [])];
          d.e6meta[x.id] = { nochar: !!m.nochar, nocont: !!m.nocont };
        });
        await e6changed(true); return true;
      }
      case "e6db-get": {
        try { await api("e6db_update"); } catch (err) { alert(err.message); }
        const poll = async () => {
          try { s.e6db = await api("e6db_status"); } catch (err) { return; }
          if (s.view === "post") paintE6(true); else C().render();
          if (s.e6db.job && s.e6db.job.state === "running") setTimeout(poll, 1000);
          else if (s.e6db.ready) { s.e6preds = {}; await loadTags(false); }
        };
        poll(); return true;
      }
      case "e6need": { const el = document.getElementById("e6card"); if (el) el.scrollIntoView({ behavior: "smooth", block: "start" }); return true; }
      case "e6run": await loadTags(true, true); return true;
      case "view-close": s.viewFile = null; C().render(); return true;
      case "view-prev": viewStep(-1); return true;
      case "view-next": viewStep(1); return true;
      case "ht": {
        const cur = (s.plan && s.plan.hashtags) || [];
        const set = new Set(d.hashtags == null ? cur : d.hashtags);
        set.has(a.dataset.tag) ? set.delete(a.dataset.tag) : set.add(a.dataset.tag);
        d.hashtags = [...set]; saveDraft(); await refreshPlan(); paintSide(); return true;
      }
      case "ht-auto": d.hashtags = null; saveDraft(); await refreshPlan(); paintSide(); return true;
      case "extend": try { await openExtend(+a.dataset.id); } catch (err) { C().toast(err.message, "error"); } return true;
      case "extend-cancel": closeExtend(); s.postMode = "edit"; await refreshPlan(); C().render(); loadTags(true); return true;
      case "rating": if (d.extend) return true; s.tried = false; d.rating = a.dataset.val; saveDraft(); await refreshE6(); await refreshPlan(); C().render(); return true;
      case "ved-open": {
        const f = d.files[+a.dataset.i];
        if (!f) return true;
        if (!(s.mediaSt && s.mediaSt.ready)) { try { s.mediaSt = await api("media_status"); } catch (err) { s.mediaSt = null; } }
        if (!(s.mediaSt && s.mediaSt.ready)) { C().toast("Для правки видео нужен ffmpeg: «Настройки» → «Видео и GIF» → «Установить ffmpeg»", "error"); return true; }
        window.VidEditor.open({ file: f, api, tok: window.API_TOKEN, toast: (t, k) => C().toast(t, k), onDone: vedDone });
        return true;
      }
      case "rmfile": { const [f] = d.files.splice(+a.dataset.i, 1); if (f && d.e6tags) delete d.e6tags[f.id]; if (f && d.e6meta) delete d.e6meta[f.id]; saveDraft(); if (f) api("post_remove_file", { file_id: f.id }).catch(() => {}); await refreshPlan(); C().render(); return true; }
      case "clear":
        if (!confirm("Очистить пост?")) return true;
        d.files.forEach((f) => api("post_remove_file", { file_id: f.id }).catch(() => {}));
        s.draft = { files: [], title: "", text: "", rating: null, signature: true, exclude: ["e6ai"], hashtags: null, e6tags: {}, e6meta: {} }; saveDraft(); await refreshPlan(); C().render(); return true;
      case "wk-start": a.disabled = true; try { s.worker = await api("worker_start"); } catch (err) { alert(err.message); } C().loadWorker(); C().render(); return true;
      case "wk-stop": if (!confirm("Выключить фоновую отправку? Запланированные посты будут выходить, только пока открыто окно Crosspost.")) return true;
        s.worker = await api("worker_stop"); setTimeout(async () => { s.worker = await api("worker_status"); C().render(); }, 16000); C().render(); return true;
      case "when-pick": s.whenAt = a.dataset.at; paintSide(); scheduleFc(50); return true;
      case "pv": s.pvTab = a.dataset.val; s.pvOpen = true; paintSide(); return true;
      case "pstat-close": s.pstatOpen = null; C().render(); return true;
      case "sched-move-cancel": s.moving = null; C().render(); return true;
      case "pstat": await openDrawer("pub:" + a.dataset.id); return true;
      case "pstat-refresh": {
        const key = a.dataset.id, [kind, id] = key.split(":");
        s.pstatBusy = key; s.pstatErr = ""; C().render();
        try {
          const jobKey = kind === "pub" ? "pubstats:" + id : "poststats:" + id;
          await api(kind === "pub" ? "pub_stats_refresh" : "post_stats_refresh", kind === "pub" ? { pub_id: +id } : { post_id: +id });
          for (let i = 0; i < 180; i++) {   // ждём окончания (до 3 минут)
            await new Promise((r) => setTimeout(r, 1000));
            const j = await api("job", { key: jobKey });
            if (!j || j.state !== "running") { if (j && j.state === "error") s.pstatErr = j.error; break; }
          }
          s.pstat[key] = kind === "pub" ? await api("pub_stats", { pub_id: +id }) : await api("post_detail", { post_id: +id });
          s.histSum = await api("pub_stats_summary");
          await loadCalendar();
        } catch (err) { s.pstatErr = err.message; }
        s.pstatBusy = null; C().render(); return true;
      }
      case "cal-prev": case "cal-next": case "cal-today": {
        const [y, m] = (s.calYm || curYm()).split("-").map(Number);
        const d = act === "cal-today" ? new Date() : new Date(y, m - 1 + (act === "cal-next" ? 1 : -1), 1);
        s.calYm = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`; s.calDay = null;
        if (!(s.cal || {})[s.calYm]) { C().render(); await loadCalendar(); }
        C().render(); return true;
      }
      case "cal-day-clear": s.calDay = null; C().render(); return true;
      case "cal-sel": s.calSelMode = !s.calSelMode; if (!s.calSelMode) s.calSel = []; C().render(); return true;
      case "cal-sel-off": s.calSelMode = false; s.calSel = []; C().render(); return true;
      case "cal-merge": case "cal-split": {
        const items = calItems(), atoms = [...new Set((s.calSel || []).flatMap((k) => (items[k] || {}).atoms || []))];
        try {
          await api(act === "cal-merge" ? "cal_merge" : "cal_split", { atoms });
          s.calSel = []; s.calSelMode = false; s.cal = {};
          await loadCalendar(); C().render();
        } catch (err) { (C().toast ? C().toast(err.message, "err") : alert(err.message)); }
        return true;
      }
      case "cal-sg-yes": case "cal-sg-no": {
        const c0 = (s.cal || {})[s.calYm], x = ((c0 && c0.suggestions) || [])[+a.dataset.i];
        if (!x) return true;
        try {
          await api("cal_decide", { a: x.a, b: x.b, yes: act === "cal-sg-yes" });
          s.cal = {}; await loadCalendar(); C().render();
        } catch (err) { (C().toast ? C().toast(err.message, "err") : alert(err.message)); }
        return true;
      }
      case "fc-when": {   // «выгоднее опубликовать в …» → запланировать на это время
        s.when = "later"; s.whenAt = a.dataset.at;
        if (!s.bestTimes) { try { s.bestTimes = await api("post_best_times"); } catch (err) { s.bestTimes = []; } }
        try { s.worker = await api("worker_status"); } catch (err) { s.worker = null; }
        paintSide(); scheduleFc(50); return true;
      }
      case "schedule": {
        s.publishing = true; paintSide();
        try {
          const r = await api("post_schedule", { draft: apiDraft(), run_at: new Date(s.whenAt).toISOString() });
          s.schedMsg = `Запланировано: ${r.human}`;
          // новый пост; файлы НЕ удаляем — они нужны запланированной публикации
          s.draft = { files: [], title: "", text: "", rating: null, signature: true, exclude: ["e6ai"], hashtags: null, e6tags: {}, e6meta: {} };
          saveDraft(); s.when = "now"; s.whenAt = null; await refreshPlan();
        } catch (err) { alert(err.message); }
        s.publishing = false; C().render(); return true;
      }
      case "pipe-add": {
        s.publishing = true; paintSide();
        try {
          await api("pipeline_add", { draft: apiDraft() });
          s.schedMsg = "Добавлено в конвейер";
          s.draft = { files: [], title: "", text: "", rating: null, signature: true, exclude: ["e6ai"], hashtags: null, e6tags: {}, e6meta: {} };
          saveDraft(); s.when = "now"; s.whenAt = null; await refreshPlan();
        } catch (err) { alert(err.message); }
        s.publishing = false; C().render(); return true;
      }
      case "pipe-toggle": try { s.pipe = await api("pipeline_set", { changes: { enabled: !s.pipe.cfg.enabled } }); s.sched = await api("post_scheduled"); } catch (err) { alert(err.message); } C().render(); return true;
      case "pipe-cfg": s.pipeCfg = !s.pipeCfg; C().render(); return true;
      case "pipe-up": case "pipe-down": try { s.pipe = await api("pipeline_move", { sid: +a.dataset.id, direction: act === "pipe-down" ? 1 : -1 }); } catch (err) { alert(err.message); } C().render(); return true;
      case "pipe-remove": if (!confirm("Убрать пост из конвейера?")) return true; try { s.pipe = await api("pipeline_remove", { sid: +a.dataset.id }); s.sched = await api("post_scheduled"); } catch (err) { alert(err.message); } C().render(); return true;
      case "pipe-move": s.pipeMove = +a.dataset.id; C().render(); return true;
      case "pipe-pin-cancel": s.pipeMove = null; C().render(); return true;
      case "pipe-pin": case "pipe-unpin": {
        const el = document.getElementById("pmv-" + a.dataset.id);
        try {
          s.pipe = await api("pipeline_pin", { sid: +a.dataset.id, run_at: act === "pipe-pin" && el && el.value ? new Date(el.value).toISOString() : "" });
          s.sched = await api("post_scheduled"); s.pipeMove = null;
        } catch (err) { alert(err.message); }
        C().render(); return true;
      }
      case "sched-cancel": if (!confirm("Отменить запланированный пост?")) return true; s.sched = await api("post_schedule_cancel", { sid: +a.dataset.id }); C().render(); return true;
      case "sched-now": s.sched = await api("post_schedule_now", { sid: +a.dataset.id }); C().render(); setTimeout(async () => { s.sched = await api("post_scheduled"); s.hist = await api("post_history"); C().render(); }, 3000); return true;
      case "sched-move": {
        const el = document.getElementById("mv-" + a.dataset.id);
        if (!el || !el.value) { s.moving = +a.dataset.id; C().render(); return true; }
        try { s.sched = await api("post_schedule_move", { sid: +a.dataset.id, run_at: new Date(el.value).toISOString() }); s.moving = null; } catch (err) { alert(err.message); }
        C().render(); return true;
      }
      case "publish": {
        if (!d.rating || !d.files.length) {   // подсветить, чего не хватает, вместо молча неактивной кнопки
          s.tried = true; C().render();
          const r = document.querySelector(".rseg"); if (r && !d.rating) r.scrollIntoView({ behavior: "smooth", block: "center" });
          return true;
        }
        s.publishing = true; paintSide();
        try {
          await api("post_publish", { draft: apiDraft() });
          if (d.extend) closeExtend();   // досылка ушла — возвращаем ваш прежний черновик
          s.postMode = "progress";
          const st = await api("post_state"); s.pubState = st.state; s.pubJob = st.job;
          await C().loadInfo(); C().startPoll();
        } catch (err) { s.planErr = err.message; alert(err.message); }
        s.publishing = false; C().render(); return true;
      }
      case "retry": await api("post_retry"); { const st = await api("post_state"); s.pubState = st.state; s.pubJob = st.job; } await C().loadInfo(); C().startPoll(); C().render(); return true;
      case "newpost": s.postMode = "edit"; s.draft = { files: [], title: "", text: "", rating: null, signature: true, exclude: ["e6ai"], hashtags: null, e6tags: {}, e6meta: {} }; saveDraft(); await refreshPlan(); C().render(); return true;
      case "backedit": s.postMode = "edit"; await refreshPlan(); C().render(); return true;
      // --- настройки Telegram
      case "tg-token": await guard("tg", async () => { s.pub = await api("pub_tg_bot", { token: val("bottok"), transport: "auto" }); s.tgNewToken = false; s.pubOk.tg = "Бот подключён: @" + s.pub.telegram.bot_name + (s.pub.note ? ". " + s.pub.note : ""); }); return true;
      case "tg-transport": await guard("tg", async () => { s.pub = await api("pub_tg_bot", { token: "", transport: a.dataset.val }); s.pubOk.tg = a.dataset.val === "mtproto" ? "Бот работает через MTProto" : "Бот работает через Bot API"; }); return true;
      case "tg-newtoken": s.tgNewToken = true; C().render(); return true;
      case "nick-save": {
        await api("settings_save", { changes: { nickname: val("nick") } }); await C().loadInfo();
        s.pubOk = Object.assign({}, s.pubOk, { e6: "Ник сохранён" }); C().render(); return true;
      }
      case "tg-newtoken-cancel": s.tgNewToken = false; C().render(); return true;
      case "tg-chcheck": await guard("tg", async () => {
        const c = s.pub.telegram.channels[+a.dataset.i];
        const info = await api("pub_tg_chat", { chat: c.id });
        if (!info.ok) throw new Error(`«${info.title}»: ${info.problems.join(", ")}`);
        s.pubOk.tg = `«${info.title}»: бот на месте, публиковать может.`;
      }); return true;
      case "tg-chadd": await guard("tg", async () => {
        const info = await api("pub_tg_chat", { chat: val("tgadd") });
        if (info.type !== "channel") throw new Error(`«${info.title}» — это группа, а не канал. Группу укажите ниже, в «Группа с темами».`);
        if (!info.ok) throw new Error(`«${info.title}»: ${info.problems.join(", ")} — добавьте бота админом с правом публикации`);
        const chs = s.pub.telegram.channels.filter((c) => c.id !== info.id);
        chs.push({ id: info.id, title: info.title, rating: /18|nsfw/i.test(info.title) ? "all" : "sfw", originals: info.linked_chat_id ? "comments" : "reply" });
        await saveSec("telegram", { channels: chs });
        s.pubOk.tg = `Канал «${info.title}» добавлен. Проверьте, что публиковать и куда класть оригиналы.`;
      }); return true;
      case "tg-chdel": { const chs = s.pub.telegram.channels.slice(); chs.splice(+a.dataset.i, 1); await saveSec("telegram", { channels: chs }); C().render(); return true; }
      case "tg-chrating": case "tg-chorig": {
        const chs = s.pub.telegram.channels.map((c) => Object.assign({}, c));
        chs[+a.dataset.i][act === "tg-chrating" ? "rating" : "originals"] = a.dataset.val;
        if (act === "tg-chorig" && a.dataset.val === "comments") s.pubOk.tg = "Режим «в комментарии»: у канала должна быть группа обсуждения, бот — админ и в ней. Бот ждёт копию поста в обсуждении до минуты.";
        await saveSec("telegram", { channels: chs }); C().render(); return true;
      }
      case "tg-group": await guard("tg", async () => {
        const info = await api("pub_tg_chat", { chat: val("tggroup"), group: true });
        if (info.type === "channel") throw new Error(`«${info.title}» — это канал. Здесь нужна группа (супергруппа с темами).`);
        if (!info.ok) throw new Error(`«${info.title}»: ${info.problems.join(", ")}`);
        await saveSec("telegram", { group: { id: info.id, title: info.title } });
        s.pubOk.tg = info.is_forum ? `Группа «${info.title}» сохранена. Выберите темы.` : `Группа «${info.title}» сохранена, но темы в ней не включены — посты будут пересылаться в общий чат.`;
        if (s.pubKnown && s.pubKnown.authorized) { try { s.topics = await api("pub_tg_topics", { chat_id: info.id }); } catch (e) { s.topics = null; } }
      }); return true;
      case "tg-groupdel": await saveSec("telegram", { group: { id: "", title: "", sfw_topic: 0, nsfw_topic: 0, sfw_topic_title: "", nsfw_topic_title: "" } }); s.topics = null; C().render(); return true;
      case "tg-topics": await guard("tg", async () => { s.topics = await api("pub_tg_topics", { chat_id: s.pub.telegram.group.id }); }); return true;
      case "tg-topicsave": await guard("tg", async () => {
        const g = {};
        document.querySelectorAll('[data-pa="tg-topicin"]').forEach((el) => {
          const v = el.value.trim(), m = v.match(/(\d+)\/?$/);
          if (v && !m) throw new Error("Номер темы — число или ссылка на тему");
          g[el.dataset.k + "_topic"] = m ? +m[1] : 0; g[el.dataset.k + "_topic_title"] = "";
        });
        await saveSec("telegram", { group: g }); s.pubOk.tg = "Темы сохранены";
      }); return true;
      // --- Discord
      case "dc-add": await guard("dc", async () => {
        const url = val("dcurl"); const info = await api("pub_discord_check", { url });
        const hooks = s.pub.discord.webhooks.filter((w) => w.url !== url);
        hooks.push({ url, name: val("dcname") || info.name || "вебхук", rating: /18|nsfw/i.test(val("dcname")) ? "nsfw" : "sfw" });
        await saveSec("discord", { webhooks: hooks, enabled: true });
        s.pubOk.dc = `Вебхук «${val("dcname") || info.name}» добавлен. Проверьте, что в него публиковать.`;
      }); return true;
      case "dc-del": { const hooks = s.pub.discord.webhooks.slice(); hooks.splice(+a.dataset.i, 1); await saveSec("discord", { webhooks: hooks }); C().render(); return true; }
      case "dc-rating": { const hooks = s.pub.discord.webhooks.map((w) => Object.assign({}, w)); hooks[+a.dataset.i].rating = a.dataset.val; await saveSec("discord", { webhooks: hooks }); C().render(); return true; }
      case "dc-mb": await saveSec("discord", { max_file_mb: +a.dataset.val }); C().render(); return true;
      // --- ВК
      case "vk-login": await guard("vk", async () => { await api("pub_vk_login", { app_id: val("vkapp") }); s.pub = await api("pub_settings"); s.vkStarted = true; }); return true;
      case "vk-finish": await guard("vk", async () => { s.pub = await api("pub_vk_finish", { url: val("vkurl") }); s.vkStarted = false; s.pubOk.vk = "Вход выполнен. Теперь укажите группу."; }); return true;
      case "vk-group": await guard("vk", async () => { s.pub = await api("pub_vk_group", { group_id: val("vkgroup").replace(/^.*vk\.com\//, "") }); s.pubOk.vk = `Группа «${s.pub.vk.group_name}» сохранена` + (s.pub.vk_group_check && s.pub.vk_group_check.is_admin === false ? " — но VK говорит, что вы в ней не админ и не редактор: публикация не пройдёт" : ""); }); return true;
      case "vk-logout": s.pub = await api("pub_vk_logout"); C().render(); return true;
      case "e6-save": await guard("e6", async () => {
        s.pub = await api("pub_e6ai", { username: val("e6user"), api_key: val("e6key") });
        const c = s.pub.e6ai_check || {};
        s.pubOk.e6 = `Вход на ${BN()} работает: ${c.name || ""}` + (c.upload_limit != null ? ` · лимит загрузок: ${c.upload_limit}` : "");
      }); return true;
      case "e6-opts": await guard("e6", async () => {
        const pre = Math.min(95, Math.max(20, +val("e6pre") || 35)) / 100;
        s.pub = await api("pub_save", { section: "e6ai", changes: { constant_tags: val("e6const").split(/[\s,]+/).filter(Boolean).map((t) => t.toLowerCase()),
          preselect: pre, with_sources: !!snap.e6src, with_description: !!snap.e6desc } });
        s.pubOk.e6 = "Сохранено";
      }); return true;
      case "tag-check": await guard("e6", async () => { s.tagger = await api("tagger_check"); C().render(); }); return true;
      case "tag-unload": s.tagger = await api("tagger_unload"); C().render(); return true;
      case "tag-install": await guard("e6", async () => { await api("tagger_install", { reinstall: !!a.dataset.re, allow_cpu: !!a.dataset.cpu }); s.tagWaiting = true; s.tagger = await api("tagger_status"); await C().loadInfo(); C().startPoll(); }); return true;
      case "ht-pos": s.pub = await api("pub_save", { section: "hashtags", changes: { position: a.dataset.val } }); C().render(); return true;
      case "ht-save": {
        const use = {}; TEXTP.forEach((k) => { use[k] = !!snap["htU-" + k]; });
        s.pub = await api("pub_save", { section: "hashtags", changes: {
          enabled: !!snap.htOn, max_species: Math.max(0, Math.min(10, +val("htN") || 0)),
          rating_tags: { sfw: val("htS").replace(/^#/, ""), erotic: val("htQ").replace(/^#/, ""), explicit: val("htE").replace(/^#/, "") },
          constant: val("htC").split(/[\s,]+/).map((t) => t.replace(/^#/, "")).filter(Boolean), use } });
        s.pubOk = Object.assign({}, s.pubOk, { ht: "Сохранено" }); C().render(); return true;
      }
      case "open-debug": try { await api("open_debug_folder"); } catch (err) { alert(err.message); } return true;
      case "vk-method": s.pub = await api("pub_vk_method", { method: a.dataset.val }); C().render(); return true;
      case "vk-blogin": await guard("vk", async () => { await api("pub_vk_browser_login"); s.vkWaiting = true; await C().loadInfo(); C().startPoll(); }); return true;
      case "vk-bgroup": await guard("vk", async () => { s.pub = await api("pub_vk_browser_group", { group: val("vkbgroup") }); s.pubOk.vk = "Группа сохранена. Сделайте пробный прогон."; }); return true;
      case "e6-site": {
        s.pub = await api("pub_e6_site", { site: a.dataset.val });
        s.e6db = null; s.e6form = {}; s.e6preds = {};
        try { s.e6db = await api("e6db_status"); } catch (err) { /* без базы тоже работаем */ }
        await refreshPlan(); C().render(); return true;
      }
      case "vk-stats": await guard("vk", async () => { await api("pub_vk_stats", { url: val("vkstats") }); s.vkStatsWaiting = true; await C().loadInfo(); C().startPoll(); }); return true;
      case "vk-recon": await guard("vk", async () => { await api("pub_vk_recon", { url: val("vkstats") }); s.vkWaiting = true; await C().loadInfo(); C().startPoll(); }); return true;
      case "vk-dry": await guard("vk", async () => { await api("pub_vk_dry", { kind: a.dataset.kind || "images" }); s.vkWaiting = true; await C().loadInfo(); C().startPoll(); }); return true;
      case "vk-token": await guard("vk", async () => { s.pub = await api("pub_vk_token", { token: val("vktok") }); s.vkManual = false; s.pubOk.vk = "Ключ сохранён. Теперь укажите группу."; }); return true;
      // --- подпись и лёгкая версия
      case "sig-save": {
        const use = {}, platforms = {};
        TEXTP.forEach((k) => {
          use[k] = document.getElementById("siguse-" + k).checked;
          const n = document.getElementById(`sig-${k}-nsfw`);
          platforms[k] = { sfw: document.getElementById(`sig-${k}-sfw`).value.trim(), nsfw: n ? n.value.trim() : "" };
        });
        await saveSec("signatures", { platforms, use });
        s.pubOk = Object.assign({}, s.pubOk, { sig: "Сохранено" }); C().render(); return true;
      }
      case "sig-copy": {   // без перерисовки — несохранённые правки в других полях не теряются
        const k = a.dataset.k;
        ["sfw", "nsfw"].forEach((r) => { const src = document.getElementById(`sig-telegram-${r}`), dst = document.getElementById(`sig-${k}-${r}`); if (src && dst) dst.value = src.value; });
        return true;
      }
      case "files-pick": {
        const f = await api("pick_folder", { start: val("fdir") || (s.outputs && s.outputs.dir) || "" });
        if (f) document.getElementById("fdir").value = f;
        return true;
      }
      case "files-open": try { await api("outputs_open"); } catch (err) { alert(err.message); } return true;
      case "files-save": case "files-default": await guard("files", async () => {
        const r = await api("outputs_set", { folder: act === "files-default" ? "" : val("fdir"), by_date: !!snap.fdate, jpeg: !!snap.fjpeg, gif: !!snap.fgif, video: !!snap.fvideo, move: !!snap.fmove });
        s.outputs = r;
        s.pubOk.files = r.moved ? `Сохранено. Перенесено файлов: ${r.moved}` : "Сохранено";
      }); return true;
      case "light-save": {
        const sc = Math.min(100, Math.max(20, +val("lscale") || 50)) / 100, q = Math.min(100, Math.max(60, +val("lq") || 88));
        await saveSec("light", { scale: sc, quality: q, background: val("lbg") || "#FFFFFF",
          subsampling: document.getElementById("lsub").checked ? "4:4:4" : "4:2:0", sharpen: document.getElementById("lsharp").checked });
        s.pubOk = Object.assign({}, s.pubOk, { light: "Сохранено" }); C().render(); return true;
      }
    }
    return false;
  }

  async function onChange(e) {
    const t = e.target, s = S(), api = C().api;
    if (t.dataset.pa === "wk-auto") { try { s.worker = await api("worker_autostart", { on: t.checked }); } catch (err) { alert(err.message); } C().render(); return true; }
    if (t.dataset.pipe) { try { s.pipe = await api("pipeline_set", { changes: { [t.dataset.pipe]: +t.value } }); s.sched = await api("post_scheduled"); } catch (err) { alert(err.message); } C().render(); return true; }
    if (t.id === "hsort") { s.hsort = t.value; C().render(); return true; }
    if (t.id === "whenAt") { s.whenAt = t.value ? new Date(t.value).toISOString() : null; paintSide(); scheduleFc(); return true; }
    if (t.id === "fileinp") { await uploadFiles(t.files); t.value = ""; return true; }
    const pa = t.dataset.pa;
    if (pa === "e6meta") { e6meta(t.dataset.id)[t.dataset.k] = t.checked; await e6changed(true); return true; }
    if (pa === "toggle") {
      const d = draft(), k = t.dataset.key;
      d.exclude = d.exclude.filter((x) => x !== k);
      if (!t.checked) d.exclude.push(k);
      saveDraft(); await refreshPlan(); paintSide();
      if (k === "e6ai" && t.checked) { await loadTags(true); if (s.view === "post") C().render(); }   // включили — подбираем теги
      else if (k === "e6ai") { s.e6form = {}; if (s.view === "post") C().render(); }
      return true;
    }
    if (t.dataset.dfield === "signature") { draft().signature = t.checked; saveDraft(); await refreshPlan(); C().render(); return true; }
    if (pa === "tag-cpuok") { s.tagCpu = false; s.tagger = await api("tagger_cpu_ok", { on: t.checked }); await C().loadInfo(); C().render(); return true; }
    if (pa === "tg-flag" || pa === "dc-flag" || pa === "vk-flag") {
      const sec = { "tg-flag": "telegram", "dc-flag": "discord", "vk-flag": "vk" }[pa];
      s.pub = await api("pub_save", { section: sec, changes: { [t.dataset.k]: t.checked } }); return true;
    }
    if (pa === "tg-chrating" || pa === "tg-chorig") {
      const chs = s.pub.telegram.channels.map((c) => Object.assign({}, c));
      chs[+t.dataset.i][pa === "tg-chrating" ? "rating" : "originals"] = t.value;
      s.pub = await api("pub_save", { section: "telegram", changes: { channels: chs } }); C().render(); return true;
    }
    if (pa === "dc-rating") {
      const hooks = s.pub.discord.webhooks.map((w) => Object.assign({}, w)); hooks[+t.dataset.i].rating = t.value;
      s.pub = await api("pub_save", { section: "discord", changes: { webhooks: hooks } }); C().render(); return true;
    }
    if (pa === "tg-topic") {
      const opt = t.options[t.selectedIndex];
      s.pub = await api("pub_save", { section: "telegram", changes: { group: { [t.dataset.k + "_topic"]: +t.value, [t.dataset.k + "_topic_title"]: +t.value ? opt.textContent : "" } } });
      return true;
    }
    return !!pa;
  }

  document.addEventListener("keydown", async (e) => {
    if (e.key === "Escape" && S().pstatOpen) { S().pstatOpen = null; C().render(); return; }
    if (e.target.dataset && e.target.dataset.htadd && e.key === "Enter") {
      e.preventDefault();
      const d = draft(), s = S();
      const set = new Set(d.hashtags == null ? ((s.plan && s.plan.hashtags) || []) : d.hashtags);
      e.target.value.split(/[\s,]+/).map((t) => t.replace(/^#/, "").trim().toLowerCase()).filter(Boolean).forEach((t) => set.add(t));
      d.hashtags = [...set]; e.target.value = ""; e.target.blur(); saveDraft(); await refreshPlan(); paintSide(); return;
    }
    const id = e.target.dataset && e.target.dataset.tagadd;
    if (!id) return;
    const ac = S().ac && S().ac.inp === e.target ? S().ac : null;
    if (ac && (e.key === "ArrowDown" || e.key === "ArrowUp")) {
      e.preventDefault(); ac.i = (ac.i + (e.key === "ArrowDown" ? 1 : ac.res.length - 1)) % ac.res.length; acPaint(); return;
    }
    if (e.key === "Escape") { acClose(); return; }
    if (e.key !== "Enter" && e.key !== "Tab") return;
    if (e.key === "Tab" && !ac) return;
    e.preventDefault();
    if (ac) { acPick(e.target, ac.res[ac.i].name); return; }
    const words = e.target.value.split(/[\s,]+/).map((t) => t.trim().toLowerCase()).filter(Boolean);
    if (!words.length) return;
    const row = e.target.dataset.ac === "character" ? "character" : e.target.dataset.ac === "species" ? "species" : "rest";
    words.forEach((t) => e6toggle(id, row, t, true));
    const sel = `#e6card input[data-tagadd="${id}"][data-ac="${e.target.dataset.ac || ""}"]`;
    e.target.value = ""; acClose(); await e6changed(true);
    const f2 = document.querySelector(sel); if (f2) f2.focus();
  });

  // ввод текста, перетаскивание и вставка файлов
  document.addEventListener("input", (e) => {
    const f = e.target.dataset && e.target.dataset.dfield;
    if (!f || f === "signature") return;
    draft()[f] = e.target.value; saveDraft();
    const c = document.getElementById("pcount"); if (c) c.innerHTML = countHtml();
    schedulePlan();
  });
  const onPost = () => window.CP && S().view === "post" && S().postMode !== "progress";
  // перетаскивание: файлы с компьютера — добавить; миниатюры внутри галереи — поменять порядок
  document.addEventListener("dragstart", (e) => {
    const g = e.target.closest && e.target.closest(".gt");
    if (!g || !onPost()) return;
    S().dragI = +g.dataset.gi; g.classList.add("dragging");
    e.dataTransfer.effectAllowed = "move"; e.dataTransfer.setData("text/plain", "cp-reorder");
  });
  document.addEventListener("dragend", () => {
    S().dragI = null;
    document.querySelectorAll(".gt.dragging,.gt.dropbefore").forEach((el) => el.classList.remove("dragging", "dropbefore"));
  });
  document.addEventListener("dragover", (e) => {
    if (!onPost()) return;
    e.preventDefault();
    if (S().dragI != null) {
      document.querySelectorAll(".gt.dropbefore").forEach((el) => el.classList.remove("dropbefore"));
      const g = e.target.closest && e.target.closest(".gt"); if (g) g.classList.add("dropbefore");
      return;
    }
    const z = document.getElementById("drop"); if (z) z.classList.add("over");
  });
  document.addEventListener("dragleave", (e) => { const z = document.getElementById("drop"); if (z && !e.relatedTarget) z.classList.remove("over"); });
  document.addEventListener("drop", async (e) => {
    if (!onPost()) return;
    e.preventDefault(); const z = document.getElementById("drop"); if (z) z.classList.remove("over");
    const s = S();
    if (s.dragI != null) {
      const from = s.dragI; s.dragI = null;
      const g = e.target.closest && e.target.closest(".gt"), d = draft();
      let to = g ? +g.dataset.gi : d.files.length;
      const [f] = d.files.splice(from, 1);
      if (to > from) to -= 1;
      d.files.splice(to, 0, f); saveDraft(); await refreshPlan(); C().render(); return;
    }
    uploadFiles(e.dataTransfer && e.dataTransfer.files);
  });
  document.addEventListener("paste", (e) => {
    if (!onPost() || !e.clipboardData) return;
    const files = Array.from(e.clipboardData.files || []);
    if (files.length) { e.preventDefault(); uploadFiles(files); }
  });

  window.Pub = { bpInit, drawerHtml, loadTags, load, tick, renderPost, renderHistory, settingsHtml, platStatus, onClick, onChange, openDrawer };
})();
