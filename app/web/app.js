/* Интерфейс Crosspost. Без сборки и фреймворков: состояние → HTML. */
(function () {
  const { sparkline, forecastChart, nicheChart, esc } = window.Charts;
  const $main = document.getElementById("main");

  // ---------------- обратная связь на действия ----------------
  // Любая кнопка или переключатель: пока идёт — индикатор на кнопке и полоса вверху; потом «✓ …» или ошибка.
  // Делается здесь, в одном месте, по самим вызовам приложения — каждую кнопку отдельно трогать не нужно.
  const FB = { el: null, at: 0, pending: 0 };
  // чтение данных (открыть экран, обновить список) — без «✓ готово», только индикатор
  const READ = new Set(["activity", "calendar_month", "e6_form", "e6_initial", "e6_learned", "e6db_status", "info", "insights_overview",
    "job", "media_status", "pick_files", "pick_folder", "post_best_times", "post_detail", "post_forecast", "post_history", "post_plan",
    "post_prep", "post_preupload", "post_scheduled", "post_state", "post_tags", "pub_da_galleries", "pub_da_state", "pub_settings",
    "pub_stats", "pub_stats_summary", "pub_tg_known", "pub_tg_topics", "storage_info", "tag_cats", "tag_search", "tagger_status",
    "tg_channels", "tg_status", "tg_status_quick", "trends_forecast", "trends_overview", "worker_status", "stats_overview", "open_url",
    "news", "news_read", "modules"]);
  const OK_TEXT = {
    e6_check_now: "Сверка тегов запущена", pub_e6_site: "Сайт для загрузки переключён", e6_forget: "Забыто", e6_never: "Больше не буду предлагать этот тег",
    e6db_update: "Скачивание базы тегов запущено", hide_tag: "Тег скрыт из трендов", insights_tag_history: "Разметка истории запущена",
    outputs_set: "Сохранено", media_install: "Установка ffmpeg запущена", modules_set: "Сохранено", create_shortcuts: "Ярлыки созданы: на рабочем столе и в меню «Пуск»", modules_set_many: "Готово", tagger_unload: "Теггер выгружен — память освобождена", tagger_check: "Проверено", tagger_cpu_ok: "Сохранено", perf_report: "Отчёт сохранён — папка открыта",
    tg_forget_proxy: "Прокси убран из запаса", open_debug_folder: "Папка открыта", open_light_folder: "Папка открыта", post_publish: "Публикация началась",
    post_remove_file: "Файл убран из поста", post_retry: "Повторяю неудачные", post_schedule: "Пост запланирован",
    post_schedule_cancel: "Запланированный пост отменён", post_schedule_move: "Время перенесено", post_schedule_now: "Публикую сейчас",
    pub_bluesky_login: "Вход в Bluesky выполнен", pub_bluesky_logout: "Вы вышли из Bluesky",
    pub_da_finish: "Вход в DeviantArt выполнен", pub_da_login: "Открыла DeviantArt — подтвердите доступ в браузере",
    pub_da_logout: "Вы вышли из DeviantArt", pub_discord_check: "Вебхук Discord работает", pub_e6ai: "Вход работает",
    pub_save: "Сохранено", pub_tg_bot: "Бот подключён", pub_tg_chat: "Канал проверен", pub_vk_browser_group: "Группа сохранена",
    pub_vk_browser_login: "Открываю окно входа во ВКонтакте", pub_vk_dry: "Пробный прогон запущен", pub_vk_finish: "Вход во ВКонтакте выполнен",
    pub_vk_group: "Группа сохранена", pub_vk_login: "Открываю вход через VK ID", pub_vk_logout: "Вы вышли из ВКонтакте",
    pub_vk_method: "Сохранено", pub_vk_recon: "Разведка запущена — ход виден в панели процессов", pub_vk_token: "Ключ сохранён",
    settings_save: "Сохранено", stats_exclude: "Готово", storage_free: "Место освобождено", tagger_install: "Установка теггера запущена",
    tagger_run: "Теггер подбирает теги", tg_check_net: "Проверка соединения завершена", tg_import_export: "Импорт выгрузки запущен",
    tg_log_out: "Вы вышли из Telegram", tg_password: "Вход в Telegram выполнен", tg_select: "Сохранено",
    tg_send_code: "Код отправлен — он придёт в чат «Telegram» на телефоне или компьютере", tg_set_keys: "Ключи сохранены",
    tg_set_net: "Настройки соединения сохранены", tg_sync: "Обновление из Telegram запущено", trends_import: "Импорт выгрузки запущен",
    trends_update: "Обновление трендов запущено", worker_autostart: "Сохранено", worker_start: "Фоновая отправка включена",
    worker_stop: "Фоновая отправка выключается",
  };
  function okText(name, res) {
    if (res && typeof res === "object" && res.cancelled) return ["", "ok"];   // окно выбора закрыли — сообщать нечего
    if (res && typeof res === "object" && res.notice) return [res.notice, "info"];
    if (name === "tg_sign_in") return res && res.need_password ? ["Нужен облачный пароль от Telegram", "info"] : ["Вход в Telegram выполнен", "ok"];
    return [OK_TEXT[name] || "Готово", "ok"];
  }
  function markAction(el) {
    if (!el || el.disabled || el.closest("#toasts")) return;
    FB.el = el; FB.at = performance.now(); FB.toasted = false;
  }
  document.addEventListener("click", (e) => markAction(e.target.closest("button, [role=button], a, [data-pa], [data-action], [data-set]")), true);
  document.addEventListener("change", (e) => { if (e.target.matches && e.target.matches("input, select, textarea")) markAction(e.target); }, true);
  document.addEventListener("keydown", (e) => { if (e.key === "Enter" && e.target.matches && e.target.matches("input")) markAction(e.target); }, true);

  // частые сетевые ошибки Python — понятными словами (подробности в панели процессов остаются как есть)
  function humanize(t) {
    const host = (t.match(/host='([^']+)'/) || [])[1];
    const pre = (t.match(/^([^:]{2,40}):\s/) || [])[1];   // «База тегов e6ai: …»
    const lead = pre && !/^https?$/i.test(pre) && !/Pool|Error/.test(pre) ? pre + ": " : "";
    if (/ConnectionPool|Max retries|NewConnectionError|getaddrinfo|Name or service not known|Failed to establish/i.test(t))
      return `${lead}нет связи с ${host || "сайтом"} — проверьте интернет или VPN`;
    if (/timed? ?out/i.test(t)) return `${lead}${host || "сайт"} не ответил вовремя — попробуйте ещё раз`;
    if (/SSLError|CERTIFICATE/i.test(t)) return `${lead}не удалось установить защищённое соединение с ${host || "сайтом"}`;
    return t;
  }
  let toastSeen = {};
  function toast(text, kind = "ok") {
    text = String(text || "").trim();
    if (kind === "error") text = humanize(text);
    if (!text) return;
    const now = Date.now();
    if (toastSeen[text] && now - toastSeen[text] < 3000) return;   // одно и то же сообщение дважды подряд не показываем
    toastSeen[text] = now;
    let box = document.getElementById("toasts");
    if (!box) { box = document.createElement("div"); box.id = "toasts"; box.setAttribute("aria-live", "polite"); document.body.appendChild(box); }
    const t = document.createElement("div");
    t.className = "toast " + kind;
    t.setAttribute("role", kind === "error" ? "alert" : "status");
    t.innerHTML = `<span class="ti">${kind === "error" ? "!" : kind === "info" ? "i" : "✓"}</span><span class="tt"></span><button class="tx" aria-label="Закрыть">×</button>`;
    t.querySelector(".tt").textContent = text;
    t.querySelector(".tx").onclick = () => t.remove();
    box.appendChild(t);
    while (box.children.length > 5) box.firstChild.remove();
    setTimeout(() => { t.classList.add("out"); setTimeout(() => t.remove(), 300); }, kind === "error" ? 12000 : kind === "info" ? 7000 : 2600);
  }
  window.alert = (m) => toast(m, "error");   // все прежние alert — тоже в этот вид
  function busy(on) {
    let bar = document.getElementById("fbbar");
    if (!bar) { bar = document.createElement("div"); bar.id = "fbbar"; document.body.appendChild(bar); }
    bar.classList.toggle("on", on);
    document.body.classList.toggle("working", on);
  }

  async function api(name, args = {}) {
    // вызов от действия человека: в течение полсекунды после клика или пока идут вызовы этого же действия
    const user = name !== "activity" && FB.el && (performance.now() - FB.at < 600 || FB.pending > 0);
    const el = user ? FB.el : null;
    if (user) {
      FB.pending++; busy(true);
      if (el && el.isConnected && el.matches("button, [role=button], a")) { el.classList.add("busy"); el.setAttribute("aria-busy", "true"); }
    }
    try {
      const r = await fetch("/api/" + name, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Token": window.API_TOKEN },
        body: JSON.stringify(args),
      });
      let j;
      try { j = await r.json(); } catch (e) { throw new Error("Приложение не ответило — попробуйте ещё раз"); }
      if (!j.ok) throw new Error(j.error || "Ошибка");
      if (/install|update|publish|_run|sync|import|preupload|tag_history|refresh|e6db_update|_dry|schedule|recon/.test(name) && name !== "activity")
        setTimeout(() => { if (window.CP && window.CP.pollActs) window.CP.pollActs(); }, 250);   // панель процессов — сразу, без ожидания
      if (user && !READ.has(name) && !FB.toasted) {
        const [text, kind] = okText(name, j.result);
        FB.toasted = kind === "ok";   // одно «✓» на одно действие, пояснения — всегда
        toast(text, kind);
      }
      return j.result;
    } catch (e) {
      if (user) toast(e.message === "Failed to fetch" ? "Нет связи с приложением — оно запущено?" : e.message, "error");
      throw e;
    } finally {
      if (user) {
        FB.pending = Math.max(0, FB.pending - 1);
        if (!FB.pending) { busy(false); FB.el = null; }
        if (el) { el.classList.remove("busy"); el.removeAttribute("aria-busy"); }
      }
    }
  }
  // что помнит окно (экран, черновик, фильтры, тема) — в файле ui.json в папке данных, а не в памяти окна:
  // окно каждый запуск открывается на новом адресе, и его собственная память начиналась бы с нуля
  const UI = window.UI_STATE && typeof window.UI_STATE === "object" ? window.UI_STATE : {};
  let uiPend = {}, uiT = null;
  function uiFlush(keepalive) {
    const ch = uiPend; uiPend = {}; clearTimeout(uiT); uiT = null;
    if (!Object.keys(ch).length) return;
    fetch("/api/ui_set", { method: "POST", keepalive: !!keepalive, headers: { "Content-Type": "application/json", "X-Token": window.API_TOKEN },
      body: JSON.stringify({ changes: ch }) }).catch(() => { Object.assign(uiPend, ch); });
  }
  window.addEventListener("beforeunload", () => uiFlush(true));
  const store = {
    get(k, d) {
      if (Object.prototype.hasOwnProperty.call(UI, k)) return UI[k] === null ? d : UI[k];
      try { const v = localStorage.getItem("cp:" + k); if (v !== null) { const x = JSON.parse(v); store.set(k, x); return x; } } catch (e) { /* нет — ну и ладно */ }
      return d;
    },
    set(k, v) { UI[k] = v; uiPend[k] = v; clearTimeout(uiT); uiT = setTimeout(uiFlush, 400); },
  };

  const S = {
    view: store.get("view", "trends"),
    src: store.get("src", "e6ai"),
    rating: store.get("rating", "all"), cat: store.get("cat", "all"), mine: store.get("mine", false),
    sel: null, info: null, data: null, fc: null, loading: false, error: null, flash: "",
    manual: false,
    tg: null, tgCh: [], tgErr: "", tgBusy: false, tgJson: "", netDraft: null, netRes: null, netOpen: false,
    platSec: store.get("platSec", "telegram"),
    stats: null, sp: store.get("sp", "90"), sc: store.get("sc", ""), statsLoading: false,
  };
  // переключатель «Теггер» в шапке — это та же настройка, что «Подбор тегов» в «Функциях»
  Object.defineProperty(S, "taggerOn", { get: () => !(S.info && S.info.modules && S.info.modules.tagger === false) });
  let poll = null;

  const CAT_LABEL = { all: "Все", species: "Виды", character: "Персонажи", franchise: "Франшизы", general: "Темы" };
  const pct = (v) => (v >= 0 ? "+" : "−") + Math.abs(Math.round(v * 100)) + "%";
  const mult = (v) => "×" + Number(v).toFixed(2).replace(".", ",");
  const plural = (n, a, b, c) => { n = Math.abs(n) % 100; const k = n % 10; return n > 10 && n < 20 ? c : k === 1 ? a : k >= 2 && k <= 4 ? b : c; };
  const num = (n) => Number(n).toLocaleString("ru-RU");
  const seg = (name, opts, cur) => `<div class="seg" role="radiogroup">${opts.map(([k, l]) =>
    `<button role="radio" aria-checked="${k === cur}" class="${k === cur ? "on" : ""}" data-set="${name}" data-val="${k}">${l}</button>`).join("")}</div>`;
  const LV = () => (S.info && S.info.levels) || { good: 1.3, hit: 2, super: 3 };   // ступени индекса — из вашей истории
  const xf = (v) => "×" + Number(v).toFixed(2).replace(/0$/, "").replace(".", ",");
  const hideBtn = (tag) => `<button class="hide" data-hide="${esc(tag)}" title="Скрыть тег из трендов" aria-label="Скрыть тег ${esc(tag)}">×</button>`;
  const catDot = (c) => `<span class="dot" style="background:${{ species: "var(--s1)", general: "var(--s2)" }[c] || "var(--s3)"}"></span>`;
  // подсказка «?»: пояснение показывается при наведении или фокусе, а не занимает место на экране
  const hint = (text) => `<span class="hint" tabindex="0" role="img" aria-label="${esc(text)}" data-tip="${esc(text)}">?</span>`;
  const svg = (d, s = 16) => `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${d}</svg>`;
  const ICON = {
    eye: svg('<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>', 14),
    flame: svg('<path d="M12 22c4 0 7-2.7 7-6.6 0-3.1-2-5.3-3.5-6.9-.4 1.6-1.3 2.7-2.5 3.2.3-3.5-1.4-6.6-4.5-8.7.2 3-1.5 5.2-3 7C4.4 11.5 5 13 5 15.4 5 19.3 8 22 12 22z"/>', 14),
    trophy: svg('<path d="M8 21h8M12 17v4M7 4h10v5a5 5 0 0 1-10 0V4z"/><path d="M17 5h3v2a3 3 0 0 1-3 3M7 5H4v2a3 3 0 0 0 3 3"/>'),
    image: svg('<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="10" r="2"/><path d="M21 17l-5-5-9 8"/>'),
    users: svg('<path d="M16 20v-1.5a3.5 3.5 0 0 0-3.5-3.5h-5A3.5 3.5 0 0 0 4 18.5V20"/><circle cx="10" cy="8" r="3.5"/><path d="M20 20v-1.5a3.5 3.5 0 0 0-2.5-3.4M15 4.6a3.5 3.5 0 0 1 0 6.8"/>'),
    heart: svg('<path d="M12 20s-7-4.4-9-8.6C1.6 8.3 3.6 5 7 5c2 0 3.3 1.1 5 3 1.7-1.9 3-3 5-3 3.4 0 5.4 3.3 4 6.4-2 4.2-9 8.6-9 8.6z"/>', 14),
    star: svg('<path d="M12 3l2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1L3.2 9.5l6.1-.9L12 3z"/>', 14),
    clock: svg('<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>'),
    cal: svg('<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/>', 15),
    trash: svg('<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>', 15),
    chev: svg('<path d="M9 6l6 6-6 6"/>'),
    bell: svg('<path d="M6 16V11a6 6 0 0 1 12 0v5l1.5 2h-15z"/><path d="M10 20a2 2 0 0 0 4 0"/>', 16),
    chat: svg('<path d="M4 5h16v11H9l-5 4z"/>', 14),
    share: svg('<path d="M14 5l6 6-6 6M20 11H9a5 5 0 0 0-5 5v2"/>', 14),
    trend: svg('<path d="M3 17l6-6 4 4 8-8M15 7h6v6"/>', 14),
    tag: svg('<path d="M3 12V4h8l10 10-8 8z"/><circle cx="7.5" cy="8.5" r="1.3"/>', 14),
    close: svg('<path d="M6 6l12 12M18 6L6 18"/>', 18),
    refresh: svg('<path d="M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7"/>', 15),
    eyeoff: svg('<path d="M3 3l18 18M10.6 6.1A9.6 9.6 0 0 1 12 6c6.4 0 10 6 10 6a17 17 0 0 1-3.1 3.7M6.6 6.6C3.9 8.3 2 12 2 12s3.6 6 10 6c1.5 0 2.9-.3 4.1-.9"/>', 14),
    plus: svg('<path d="M12 5v14M5 12h14"/>', 18),
    play: svg('<path d="M7 4l13 8-13 8V4z"/>', 14),
  };
  // площадки: буквы на цветном квадрате (свои значки, без чужих логотипов)
  const PLAT = { telegram: ["TG", "#2A9FD8", "Telegram"], discord: ["DS", "#5865F2", "Discord"], vk: ["VK", "#2D7BE5", "ВКонтакте"],
    e6ai: ["e6", "#24508F", "e6ai"], reddit: ["R", "#F0541E", "Reddit"], deviantart: ["DA", "#14A85A", "DeviantArt"], bluesky: ["BS", "#1185FE", "Bluesky"], joyreactor: ["JR", "#C2410C", "Joyreactor"], x: ["X", "#3A3F4B", "X"] };
  const pbadge = (platform, { cls = "", tip = "", link = "", sm = false } = {}) => {
    const [t, c, name] = PLAT[platform] || [String(platform || "?").slice(0, 2).toUpperCase(), "#555", platform];
    const attrs = `class="pb ${sm ? "sm" : ""} ${cls}" style="--pc:${c}" data-tip="${esc(tip || name)}" aria-label="${esc(tip || name)}"`;
    return link ? `<a href="#" ${attrs} data-open="${esc(link)}">${t}</a>` : `<span ${attrs}>${t}</span>`;
  };
  const delta = (cur, prev, kind = "rel") => {
    if (cur == null || prev == null || (kind === "rel" && !prev)) return "";
    const d = kind === "rel" ? cur / prev - 1 : cur - prev;   // rel — в процентах, pp — в процентных пунктах
    const v = kind === "rel" ? Math.round(d * 100) : Math.round(d * 100);
    const cls = Math.abs(v) < 2 ? "flat" : v > 0 ? "up" : "down";
    return `<span class="delta ${cls}">${v > 0 ? "+" : v < 0 ? "−" : "±"}${Math.abs(v)}${kind === "rel" ? "%" : " п.п."}</span>`;
  };
  // общий контекст для модуля публикации (publish.js)
  // ----- значки на миниатюрах: тип содержимого, медали лидеров, «горячий» -----
  const KIND = { img: ["IMG", "Картинка"], gif: ["GIF", "GIF-анимация"], vid: ["VID", "Видео"] };
  function kindBadge(kind, n) {
    const k = KIND[kind] ? kind : "img", many = k === "img" && n > 1;
    return `<span class="kb k-${k}" data-tip="${many ? `Альбом: ${n} ${plural(n, "картинка", "картинки", "картинок")}` : KIND[k][1]}">${KIND[k][0]}${many ? `<i>${n}</i>` : ""}</span>`;
  }
  function medal(rank, scope) {
    if (!rank || rank > 3) return "";
    return `<span class="medal m${rank}" data-tip="${rank}-е место ${esc(scope || "")}"><svg viewBox="0 0 24 24" aria-hidden="true">
      <path class="rib" d="M7.5 1.5h4l1.6 6.2-3.6 1.7zM16.5 1.5h-4l-1.6 6.2 3.6 1.7z"/>
      <circle class="disc" cx="12" cy="15" r="7.2"/><circle class="ring" cx="12" cy="15" r="5.3"/>
      <text x="12" y="18.3" text-anchor="middle">${rank}</text></svg></span>`;
  }
  function hotBadge(index, est) {
    if (index == null || index < LV().hit) return "";
    const sup = index >= LV().super, x = String(index.toFixed(1)).replace(".", ",");
    const tip = est ? `Горячий прямо сейчас: свежий пост идёт на ×${x} к норме канала` : sup ? `Супер-хит: ×${x} к норме канала` : `Хит: ×${x} к норме канала`;
    return `<span class="hot ${sup ? "super" : ""} ${est ? "live" : ""}" data-tip="${tip}">${ICON.flame}</span>`;
  }
  // o: {kind, n, rank, scope, index, est, kinds: false — не показывать IMG (только GIF/VID)}
  function badges(o) {
    const k = o.kind === "gif" || o.kind === "vid" ? kindBadge(o.kind) : "";   // обычные картинки без значка
    const top = medal(o.rank, o.scope) + hotBadge(o.index, o.est);
    return top || k ? `<span class="bdg ${o.sm ? "sm" : ""}">${top ? `<span class="br">${top}</span>` : ""}${k}</span>` : "";
  }
  window.CP = { toast, LV, api, S, store, esc, num, plural, seg, hint, ICON, pbadge, PLAT, badges, newsStrip: () => window.CP_newsStrip ? window.CP_newsStrip() : "", pollActs: () => pollActs(), render: () => render(), $main, loadInfo: () => loadInfo(),
    loadWorker: () => loadWorker(), startPoll: () => { if (!poll) poll = setInterval(tick, 1000); } };

  // ---------------- данные ----------------
  async function loadInfo() {
    S.info = await api("info");
    navBadge(); updBadge(); updModal();
    const running = Object.values(S.info.trends).some((t) => t.job && t.job.state === "running") ||
      (S.info.tg_job && S.info.tg_job.state === "running") ||
      (S.info.pub_job && S.info.pub_job.state === "running") || (S.info.vk_job && S.info.vk_job.state === "running") ||
      (S.info.ins_job && S.info.ins_job.state === "running") || (S.info.update && S.info.update.job && S.info.update.job.state === "running");
    if (running && !poll) poll = setInterval(tick, 1000);
    if (!running && poll) { clearInterval(poll); poll = null; }
  }
  async function tick() {
    const before = S.info && S.info.trends[S.src].job;
    const tgBefore = S.info && S.info.tg_job;
    await loadInfo();
    if (window.Pub) await window.Pub.tick();
    const tgj = S.info.tg_job;
    if (tgBefore && tgBefore.state === "running" && tgj && tgj.state !== "running") {
      await loadChannels(); if (S.view === "stats") await loadStats();
    }
    const ij = S.info.ins_job;
    if (S.insJobWas && (!ij || ij.state !== "running")) { S.insJobWas = false; if (S.view === "stats") await loadStats(); }
    if (ij && ij.state === "running") {
      S.insJobWas = true;
      const el = document.getElementById("insprog");
      if (S.view === "stats" && el) { el.querySelector("[data-bar]").style.width = Math.round(ij.progress * 100) + "%"; el.querySelector("[data-msg]").textContent = ij.message || ""; return; }
    }
    const after = S.info.trends[S.src].job;
    if (before && before.state === "running" && after && after.state !== "running") {
      await loadOverview();
    }
    // Во время загрузки из Telegram статистику не перерисовываем каждую секунду —
    // обновляем только полосу прогресса, а сами цифры пересчитываем раз в 15 секунд.
    // открыт крупный просмотр картинки — экран не перерисовываем, иначе сбиваются масштаб и перетаскивание
    if (document.querySelector(".bigprev")) { if (S.view === "stats" && tgj && tgj.state === "running") updateProgress(tgj); return; }
    if (S.view === "stats" && tgj && tgj.state === "running" && document.getElementById("tgprog")) {
      updateProgress(tgj);
      if (Date.now() - (S.statsAt || 0) > 15000) { S.statsAt = Date.now(); await loadStats(); render(); }
      return;
    }
    // не перерисовываем экран, пока человек печатает — иначе слетит курсор и набранный текст
    const ae = document.activeElement;
    if (ae && $main.contains(ae) && /^(INPUT|TEXTAREA|SELECT)$/.test(ae.tagName) && ae.type !== "checkbox") return;
    render();
  }
  async function loadOverview() {
    S.data = null; S.fc = null; S.error = null;
    if (!S.info.trends[S.src].ready) return;
    S.loading = true; render();
    try {
      S.data = await api("trends_overview", { source: S.src, rating: S.rating, category: S.cat, mine_only: S.mine });
      const all = (S.data.rising || []).concat(S.data.popular || []);
      if (!S.sel || !all.some((x) => x.tag === S.sel)) S.sel = all.length ? all[0].tag : null;
      if (S.sel) S.fc = await api("trends_forecast", { source: S.src, tag: S.sel, rating: S.rating });
    } catch (e) { S.error = e.message; }
    S.loading = false;
  }
  // список каналов — из базы, мгновенно (без обращения к Telegram)
  async function loadChannels() {
    try { S.tgCh = await api("tg_channels", { refresh: false }); } catch (e) { /* покажем позже */ }
  }
  // статус входа: сначала последний известный (сразу), настоящая проверка — следом, без ожидания экрана
  async function loadTg(refresh) {
    try {
      if (!S.tg) S.tg = await api("tg_status_quick");
      await loadChannels();
    } catch (e) { S.tgErr = e.message; }
    const check = (async () => {
      try {
        S.tg = await api("tg_status");
        S.tgErr = S.tg.error || "";
        if (refresh && S.tg.authorized) S.tgCh = await api("tg_channels", { refresh: true });
      } catch (e) { S.tgErr = e.message; }
      if (S.view === "settings") quietRender();
    })();
    if (refresh) await check;
  }
  function quietRender() {
    const ae = document.activeElement;
    if (ae && $main.contains(ae) && /^(INPUT|TEXTAREA|SELECT)$/.test(ae.tagName) && ae.type !== "checkbox") return;
    render();
  }
  function progressHtml(job) {
    return `<section class="card prog" id="tgprog"><div class="head"><h2>Идёт загрузка из Telegram</h2><span class="muted small" data-pct>${Math.round(job.progress * 100)}%</span></div>
      <div class="progress"><div data-bar style="width:${Math.round(job.progress * 100)}%"></div></div>
      <p class="muted small" data-msg>${esc(job.message || "")}</p>
      <p class="note">Статистика ниже уже считается по загруженной части и обновляется по ходу (раз в 15 секунд).</p></section>`;
  }
  function updateProgress(job) {
    const el = document.getElementById("tgprog"); if (!el) return;
    const pct = Math.round(job.progress * 100) + "%";
    el.querySelector("[data-bar]").style.width = pct;
    el.querySelector("[data-pct]").textContent = pct;
    el.querySelector("[data-msg]").textContent = job.message || "";
  }
  // ----- окно «вышла новая версия» при запуске: установить / позже / пропустить -----
  function updModal() {
    const u = S.info && S.info.update, old = document.getElementById("updmodal");
    const j = u && u.job, rd = u && u.ready && u.latest && u.ready.version === u.latest.version;
    if (u && u.show && !S.updPrompted && !(j && j.state === "running")) { S.updPrompted = true; S.updOpen = true; }   // один раз за запуск
    if (!u || !u.latest || !(S.updAuto || S.updOpen) || (!u.show && !S.updAuto)) { if (old) old.remove(); return; }
    if (rd && S.updAuto && !S.updApplying && u.can) {   // скачано и проверено — человек уже нажал «Установить»
      S.updApplying = true;
      api("update_apply").catch((err) => { S.updApplying = false; S.updAuto = false; toast(err.message, "error"); updModal(); });
    }
    const lt = u.latest, e = window.CP.esc;
    let body, foot;
    if (S.updApplying) { body = `<p>Устанавливаю версию ${e(lt.version)}… Crosspost закроется и сам откроется заново — это займёт около минуты.</p>`; foot = ""; }
    else if (j && j.state === "running") { body = `<div class="progress thin"><div style="width:${Math.round((j.progress || 0) * 100)}%"></div></div><p class="muted small" style="margin:8px 0 0">${e(j.message || "Скачиваю…")}</p>`; foot = ""; }
    else {
      const err = S.updAuto && j && j.state === "error" ? `<p class="err">${e(j.error)}</p>` : "";
      body = `<p>У вас версия ${e(u.current)}, вышла ${e(lt.version)}${lt.date ? ` (${e(lt.date)})` : ""}.</p>${lt.notes ? `<div class="notes">${e(lt.notes)}</div>` : ""}${err}
        <p class="muted small" style="margin:10px 0 0">${u.can ? "Установка займёт около минуты: Crosspost закроется и откроется заново. Данные и ключи не затрагиваются, прежняя версия сохраняется на случай отката." : "Обновление по кнопке здесь недоступно: " + e(u.why) + "."}</p>`;
      foot = `${u.can ? `<button class="btn primary" data-upd="install">Установить сейчас</button>` : `<button class="btn primary" data-upd="page">Страница выпуска</button>`}
        <button class="btn" data-upd="later">Напомнить позже</button><button class="btn ghost" data-upd="skip">Пропустить эту версию</button>`;
    }
    const html = `<div class="backdrop" style="z-index:60"></div><div class="updbox" role="dialog" aria-modal="true" aria-label="Вышла новая версия"><h2>Вышла новая версия Crosspost</h2>${body}${foot ? `<div class="cardfoot">${foot}</div>` : ""}</div>`;
    if (old) { if (old.dataset.sig === html) return; old.innerHTML = html; old.dataset.sig = html; return; }
    const el = document.createElement("div"); el.id = "updmodal"; el.dataset.sig = html; el.innerHTML = html; document.body.appendChild(el);
    el.addEventListener("click", async (ev) => {
      const b = ev.target.closest("[data-upd]"); if (!b) return;
      const a = b.dataset.upd;
      try {
        if (a === "later") { S.updOpen = false; S.updAuto = false; updModal(); }
        else if (a === "page") await api("update_open");
        else if (a === "skip") { S.updOpen = false; S.info.update = await api("update_skip", { version: S.info.update.latest.version }); updBadge(); updModal(); if (S.view === "settings") render(); }
        else if (a === "install") { S.updAuto = true; await api("update_prepare"); await loadInfo(); }
      } catch (err) { S.updAuto = false; toast(err.message, "error"); updModal(); }
    });
  }
  function updBadge() {
    const b = document.getElementById("updbtn"), u = S.info && S.info.update; if (!b) return;
    b.hidden = !(u && u.show);
    if (u && u.show) b.querySelector(".wt").textContent = "Вышла версия " + u.latest.version;
  }
  // кнопки обновления: скачать → установить и перезапустить
  function updAct(u) {
    const j = u.job, rd = u.ready && u.ready.version === (u.latest || {}).version ? u.ready : null;
    const skip = u.show ? `<button class="btn ghost" data-action="upd-skip">Пропустить эту версию</button>` : `<span class="muted small">Уведомление скрыто — версия пропущена.</span>`;
    const page = `<button class="btn ghost" data-action="upd-open">Страница выпуска</button>`;
    if (j && j.state === "running") return `<div class="progress thin"><div style="width:${Math.round((j.progress || 0) * 100)}%"></div></div><p class="muted small" style="margin:0">${esc(j.message || "Скачиваю…")}</p>`;
    const err = j && j.state === "error" ? `<p class="err">${esc(j.error)}</p>` : "";
    if (S.updApplying) return `<p>Устанавливаю… Окно закроется и откроется заново — это займёт около минуты.</p>`;
    if (rd) return `${err}<div class="callout"><p><b>Версия ${esc(rd.version)} скачана и проверена.</b> При установке Crosspost закроется на минуту и откроется заново. Фоновая отправка остановится и включится снова. Ваши данные, ключи и папка «Готовые файлы» не затрагиваются; прежняя версия сохраняется на случай отката.</p></div>
      <div class="cardfoot"><button class="btn primary" data-action="upd-apply">Установить и перезапустить</button>${page}${skip}</div>`;
    if (!u.can) return `<p class="muted">Обновление по кнопке здесь недоступно: ${esc(u.why)}.</p><div class="cardfoot">${page}${skip}</div>`;
    return `${err}<div class="cardfoot"><button class="btn primary" data-action="upd-get">Скачать обновление</button>${page}${skip}</div>
      <p class="muted small" style="margin:0">Файл качается с GitHub и проверяется по контрольной сумме. Сначала только скачивание — ничего не меняется, пока вы не нажмёте «Установить».</p>`;
  }
  function updHtml() {
    const u = S.info.update, hint = window.CP.hint;
    if (!u) return "";
    const lt = u.latest, ago = u.last ? new Date(u.last * 1000).toLocaleString("ru-RU", { day: "numeric", month: "long", hour: "2-digit", minute: "2-digit" }) : "";
    let st;
    if (u.newer) st = `<p><b>Вышла версия ${esc(lt.version)}</b>${lt.date ? ` <span class="muted">от ${esc(lt.date)}</span>` : ""}. У вас ${esc(u.current)}.</p>
      ${lt.notes ? `<div class="notes">${esc(lt.notes)}</div>` : ""}
      ${updAct(u)}`;
    else if (u.error && !lt) st = `<p class="muted">Не удалось проверить: ${esc(u.error)}.</p>`;
    else st = `<p class="muted">У вас последняя версия (${esc(u.current)}).${u.error ? ` Последняя проверка не удалась: ${esc(u.error)}.` : ""}</p>`;
    return `<section class="card setc"><div class="head"><h2>Обновления ${hint("Программа один раз при запуске спрашивает GitHub, вышла ли новая версия, и только сообщает об этом — сама ничего не скачивает и не ставит. Проверку можно отключить. Передаётся только запрос на страницу выпусков, без ваших данных.")}</h2></div>
      ${st}
      <div class="cardfoot"><button class="btn" data-action="upd-check" ${S.updBusy || u.checking ? "disabled" : ""}>${S.updBusy ? "Проверяю…" : "Проверить сейчас"}</button>
        <label class="tgl"><input type="checkbox" data-action="upd-auto" ${u.check ? "checked" : ""}><span>Проверять при запуске</span></label>
        <span class="muted small">${ago ? `Проверено ${esc(ago)}` : "Ещё не проверялось"}</span></div></section>`;
  }
  function navBadge() {
    const b = document.querySelector('#nav button[data-view="settings"]'); if (!b) return;
    const j = S.info && S.info.tg_job, run = j && j.state === "running";
    b.innerHTML = "Настройки" + (run ? ` <span class="navbadge">${Math.round(j.progress * 100)}%</span>` : "");
  }
  // ----- индикатор фоновой отправки в шапке -----
  async function loadWorker() {
    try { S.worker = await api("worker_status"); } catch (e) { S.worker = null; }
    paintWorker();
    return S.worker;
  }
  function paintTagger() {
    const b = document.getElementById("tgind"); if (!b) return;
    const on = S.taggerOn !== false;
    b.className = "wkind tgind " + (on ? "on" : "off"); b.setAttribute("aria-pressed", on ? "true" : "false");
    b.dataset.tip = on ? "Подбор тегов включён: теггер подбирает теги картинкам в фоне. К посту они привязываются, только когда включена отправка на e6ai. Нажмите, чтобы выключить (это та же настройка, что «Подбор тегов» в «Настройки» → «Функции»)."
                       : "Подбор тегов выключен — теги картинкам не подбираются. Нажмите, чтобы включить.";
  }
  function paintWorker() {
    const b = document.getElementById("wkind"), w = S.worker;
    if (!b) return;
    b.hidden = !w;
    if (!w) return;
    b.className = "wkind " + (w.running ? "on" : "off");
    b.dataset.tip = w.running ? (w.next || "Фоновая отправка работает") : "Фоновая отправка выключена — нажмите, чтобы включить";
    b.querySelector(".wt").textContent = w.running ? "Фоновая отправка" : "Отправка выключена";
    const pop = document.getElementById("wkpop");
    if (pop && !pop.hidden) pop.innerHTML = workerPopHtml();
  }
  function workerPopHtml() {
    const w = S.worker || {};
    return `<div class="head"><h2>Фоновая отправка</h2><span class="badge ${w.running ? "ok" : "warn"}" style="padding:2px 8px;border-radius:999px;font-size:12px">${w.running ? "работает" : "выключена"}</span></div>
      <p class="small muted">${w.running ? "Публикует запланированные посты и снимает цифры свежих постов, даже когда окно Crosspost закрыто." : "Запланированные посты выйдут, только пока открыто окно Crosspost."}</p>
      ${w.next ? `<div class="kvlist"><div><span>Следующий пост</span><b>${esc(w.next.replace(/^Следующий пост: /, ""))}</b></div>${w.track ? `<div><span>Замеры</span><span class="small">${esc(w.track)}</span></div>` : ""}</div>` : ""}
      <div style="display:flex;gap:8px;flex-wrap:wrap">${w.running ? `<button class="btn small danger" data-wk="stop">Выключить</button>` : `<button class="btn small primary" data-wk="start">Включить</button>`}
        <button class="btn small ghost" data-wk="settings">Автозапуск с Windows…</button></div>`;
  }
  document.getElementById("wkind").addEventListener("click", () => {
    const pop = document.getElementById("wkpop");
    pop.hidden = !pop.hidden;
    if (!pop.hidden) { pop.innerHTML = workerPopHtml(); loadWorker(); }
  });
  document.getElementById("wkpop").addEventListener("click", async (e) => {
    const b = e.target.closest("[data-wk]"); if (!b) return;
    const pop = document.getElementById("wkpop");
    if (b.dataset.wk === "settings") { pop.hidden = true; S.view = "settings"; store.set("view", "settings"); render(); return; }
    b.disabled = true;
    try {
      if (b.dataset.wk === "start") S.worker = await api("worker_start");
      else { S.worker = await api("worker_stop"); setTimeout(loadWorker, 16000); }
    } catch (err) { alert(err.message); }
    paintWorker(); if (window.Pub) render();
  });
  document.addEventListener("click", (e) => {
    const pop = document.getElementById("wkpop");
    if (pop && !pop.hidden && !pop.contains(e.target) && !e.target.closest("#wkind")) pop.hidden = true;
  });
  // ----- панель процессов: всё, что идёт в фоне, с полосками -----
  let actT = null;
  const actGone = {};   // закрытые вручную ошибки
  const actWas = {};   // что шло в прошлый раз — чтобы сказать «готово» или «ошибка», когда закончится
  const ACT_QUIET = new Set(["prep", "preupload", "tagger-load"]);   // фоновая мелочь — без сообщений
  async function pollActs() {
    let list = [];
    if (!document.hidden) { try { list = await api("activity"); } catch (e) { list = S.acts || []; } }
    for (const x of list) {
      if (actWas[x.key] === "running" && x.state !== "running" && !ACT_QUIET.has(x.key)) {
        if (x.state === "error") toast(`${x.label}: ${x.message || "ошибка"}`, "error");
        else toast(`${x.label} — готово${x.message && !/^готово/i.test(x.message) ? ": " + x.message : ""}`, "ok");
      }
      actWas[x.key] = x.state;
    }
    for (const k of Object.keys(actWas)) if (!list.some((x) => x.key === k)) delete actWas[k];
    S.acts = list;
    paintActs(list);
    clearTimeout(actT);
    actT = setTimeout(pollActs, list.some((x) => x.state === "running") ? 1000 : 4000);
  }
  function actHtml(x) {
    const pct = x.progress == null ? null : Math.round(x.progress * 100);
    const subs = (x.subs || []).map((d) => `<div class="asub">${pbadge(d.platform, { sm: true })}<span class="an">${esc(d.name)}</span>
        <span class="ab"><i class="${d.state === "error" ? "bad" : d.state === "done" ? "ok" : ""}" style="width:${Math.round((d.progress || 0) * 100)}%"></i></span>
        <span class="ap">${d.state === "done" ? "✓" : d.state === "error" ? "✕" : Math.round((d.progress || 0) * 100) + "%"}</span></div>`).join("");
    return `<div class="act ${x.state}" data-act="${esc(x.key)}" ${x.go ? `data-go="${esc(x.go)}" role="button" tabindex="0"` : ""}>
      <div class="ah"><b>${esc(x.label)}</b><span class="ap">${x.state === "done" ? "✓ готово" : x.state === "error" ? "ошибка" : pct == null ? "" : pct + "%"}</span>
        ${x.state === "error" ? `<button class="ax" data-actx="${esc(x.key)}" aria-label="Скрыть">×</button>` : ""}</div>
      <div class="abar ${pct == null && x.state === "running" ? "indet" : ""}"><i style="width:${x.state === "done" ? 100 : pct || 0}%"></i></div>
      ${x.message ? `<span class="am">${esc(x.state === "error" ? humanize(x.message) : x.message)}</span>` : ""}${subs ? `<div class="asubs">${subs}</div>` : ""}</div>`;
  }
  function paintActs(list) {
    const box = document.getElementById("acts");
    if (!box) return;
    const vis = list.filter((x) => !(x.state === "error" && actGone[x.key] === x.started));
    box.innerHTML = vis.map(actHtml).join("");
    box.classList.toggle("on", vis.length > 0);
  }
  document.getElementById("acts").addEventListener("click", async (e) => {
    const x = e.target.closest("[data-actx]");
    if (x) { const it = (S.acts || []).find((a) => a.key === x.dataset.actx); if (it) actGone[it.key] = it.started; paintActs(S.acts || []); return; }
    const g = e.target.closest("[data-go]"); if (!g) return;
    const [view, sec] = g.dataset.go.split(":");
    if (sec) { S.platSec = sec; store.set("platSec", sec); }
    if (view === "post" && g.dataset.act === "publish") S.postMode = "progress";
    S.view = view; store.set("view", view); render();
    if (["post", "history", "settings"].includes(view)) { await window.Pub.load(view); render(); }
    if (view === "stats") { await loadStats(); render(); }
  });

  // ----- новости по свежим артам -----
  const NEWS_ICON = { good: "trend", views: "eye", hit: "flame", react: "heart", fav: "star", comment: "chat", share: "share", score: "trend", mod: "tag" };
  async function loadNews(markRead) {
    if (!(modOn("news") && modOn("collect"))) { S.news = { items: [], unread: 0 }; paintNewsBtn(); return; }
    try { S.news = await api(markRead ? "news_read" : "news"); } catch (e) { return; }
    paintNewsBtn();
    const pop = document.getElementById("newspop");
    if (pop && !pop.hidden) pop.innerHTML = newsPopHtml();
    if (S.view === "history" && document.getElementById("newsstrip")) document.getElementById("newsstrip").outerHTML = newsStripHtml();
  }
  function paintNewsBtn() {
    const b = document.getElementById("newsbtn"); if (!b) return;
    b.querySelector(".nbi").innerHTML = ICON.bell;
    const c = b.querySelector(".nbc"), n = (S.news && S.news.unread) || 0;
    c.hidden = !n; c.textContent = n > 99 ? "99+" : n;
    b.classList.toggle("has", !!n);
  }
  const ago = (iso) => {
    const m = Math.round((Date.now() - new Date(iso)) / 60000);
    return m < 1 ? "только что" : m < 60 ? `${m} мин назад` : m < 1440 ? `${Math.round(m / 60)} ч назад` : `${Math.round(m / 1440)} дн назад`;
  };
  function newsItemHtml(x) {
    const tok = encodeURIComponent(window.API_TOKEN);
    return `<button class="nitem ${x.read ? "" : "unread"} k-${esc(x.kind)} l${x.level || 1}" data-news-pub="${x.pub_id || ""}">
      <span class="nth">${x.pub_id ? `<img src="/pthumb/${x.pub_id}?t=${tok}" alt="" loading="lazy" onerror="this.style.visibility='hidden'">` : ""}<span class="nic">${ICON[NEWS_ICON[x.kind]] || ICON.bell}</span></span>
      <span class="ntx"><span>${esc(x.text)}</span><span class="muted small">${pbadge(x.platform, { sm: true })} ${ago(x.at)}</span></span></button>`;
  }
  function newsPopHtml() {
    const n = S.news;
    if (!n) return `<p class="muted">Загрузка…</p>`;
    const vol = n.volume || "normal";
    return `<div class="nhead"><b>Новости по свежим артам</b>${hint("Раз в полторы минуты смотрю публикации последних 7 дней. Что считать новостью — по вашей же истории: хит — пост лучше 90% постов канала, «много реакций» — больше, чем у 90% постов к этому же часу. «Мало» — только хиты и супер-хиты, «много» — ещё и «лучше обычного» (лучше 75%).")}
        <span style="margin-left:auto" class="seg sm">${[["few", "Мало"], ["normal", "Средне"], ["many", "Много"]].map(([k, l]) => `<button class="${k === vol ? "on" : ""}" data-nvol="${k}" aria-pressed="${k === vol}">${l}</button>`).join("")}</span></div>
      ${n.items.length ? `<div class="nlist">${n.items.map(newsItemHtml).join("")}</div>` : `<p class="muted small" style="padding:6px 4px">Пока тихо. Здесь появятся хиты, всплески реакций и новые комментарии к свежим постам.</p>`}`;
  }
  function newsStripHtml() {
    const items = ((S.news && S.news.items) || []).filter((x) => Date.now() - new Date(x.at) < 3 * 864e5).slice(0, 4);
    if (!items.length) return `<div id="newsstrip"></div>`;
    return `<section class="card newsstrip" id="newsstrip"><div class="head"><h2>${ICON.bell} Новости по свежим артам</h2><button class="btn small ghost" data-news-all="1">Все новости</button></div>
      <div class="nrow">${items.map(newsItemHtml).join("")}</div></section>`;
  }
  window.CP_newsStrip = newsStripHtml;
  paintTagger();
  document.getElementById("tgind").addEventListener("click", async () => {
    try { S.info.modules = await api("modules_set", { key: "tagger", on: !S.taggerOn }); } catch (e) { return; }
    applyModules(); paintTagger();
    if (S.view === "settings") render();
    if (S.taggerOn && S.view === "post" && window.Pub) { await window.Pub.loadTags(true); render(); }
  });
  document.getElementById("newsbtn").addEventListener("click", async () => {
    const pop = document.getElementById("newspop");
    pop.hidden = !pop.hidden;
    if (!pop.hidden) { pop.innerHTML = newsPopHtml(); if (S.news && S.news.unread) setTimeout(() => loadNews(true), 1500); }
  });
  document.addEventListener("click", async (e) => {
    const pop = document.getElementById("newspop");
    const all = e.target.closest("[data-news-all]");
    if (all) { pop.hidden = false; pop.innerHTML = newsPopHtml(); return; }
    const nv = e.target.closest("[data-nvol]");
    if (nv) {
      try { S.info.settings = await api("settings_save", { changes: { news_volume: nv.dataset.nvol } }); } catch (err) { alert(err.message); return; }
      if (S.news) S.news.volume = nv.dataset.nvol;
      pop.innerHTML = newsPopHtml(); if (S.view === "settings") render(); return;
    }
    const it = e.target.closest("[data-news-pub]");
    if (it) {
      pop.hidden = true;
      if (!it.dataset.newsPub) return;
      S.view = "history"; store.set("view", "history"); render();
      await window.Pub.load("history"); render();
      await window.Pub.openDrawer("pub:" + it.dataset.newsPub);
      return;
    }
    if (pop && !pop.hidden && !pop.contains(e.target) && !e.target.closest("#newsbtn")) pop.hidden = true;
  });

  // ----- всплывающие подсказки для всего, у чего есть data-tip -----
  const tipEl = document.getElementById("tip");
  function showTipFor(el) {
    const text = el.dataset.tip; if (!text) return;
    tipEl.textContent = text; tipEl.hidden = false;
    const r = el.getBoundingClientRect(), t = tipEl.getBoundingClientRect();
    let left = Math.min(Math.max(8, r.left + r.width / 2 - t.width / 2), window.innerWidth - t.width - 8);
    let top = r.bottom + 8;
    if (top + t.height > window.innerHeight - 8) top = r.top - t.height - 8;
    tipEl.style.left = left + "px"; tipEl.style.top = top + "px";
  }
  document.addEventListener("mouseover", (e) => { const el = e.target.closest("[data-tip]"); if (el) showTipFor(el); });
  document.addEventListener("mouseout", (e) => { const el = e.target.closest("[data-tip]"); if (el && !el.contains(e.relatedTarget)) tipEl.hidden = true; });
  document.addEventListener("focusin", (e) => { const el = e.target.closest && e.target.closest("[data-tip]"); if (el) showTipFor(el); });
  document.addEventListener("focusout", () => { tipEl.hidden = true; });
  document.addEventListener("scroll", () => { tipEl.hidden = true; }, true);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") { tipEl.hidden = true; const pop = document.getElementById("wkpop"); if (pop) pop.hidden = true; } });

  // «Статистика»: сразу сохранённый результат, а если данные поменялись — тихо подтягиваем пересчитанный
  let statsPoll = null;
  async function fetchStats() {
    const [st, ins] = await Promise.all([
      api("stats_overview", { period: S.sp, chat_id: S.sc || null }).catch((e) => ({ error: e.message })),
      api("insights_overview").catch((e) => ({ error: e.message })),
    ]);
    S.stats = st; S.ins = ins;
    return (st && st._cache && st._cache.stale) || (ins && ins._cache && ins._cache.stale);
  }
  async function loadStats() {
    clearTimeout(statsPoll);
    S.statsLoading = true;
    if (!S.stats) render();
    const stale = await fetchStats();
    S.statsLoading = false;
    if (stale) pollStats(0);
  }
  function pollStats(n) {
    clearTimeout(statsPoll);
    if (n > 15) return;
    statsPoll = setTimeout(async () => {
      if (S.view !== "stats") return;
      const sp = S.sp, sc = S.sc;
      const stale = await fetchStats();
      if (S.view === "stats" && sp === S.sp && sc === S.sc) quietRender();
      if (stale) pollStats(n + 1);
    }, 2000);
  }
  async function pick(tag) {
    S.sel = tag;
    S.fc = await api("trends_forecast", { source: S.src, tag, rating: S.rating }).catch(() => null);
    render();
    const el = document.getElementById("forecast");
    if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  // ---------------- отрисовка ----------------
  function render() {
    renderMain();
    if (S.view === "stats" && S.pstatOpen && window.Pub) $main.insertAdjacentHTML("beforeend", window.Pub.drawerHtml(S.pstatOpen));
  }
  function renderMain() {
    applyModules();
    document.querySelectorAll("#nav button").forEach((b) => b.classList.toggle("on", b.dataset.view === S.view));
    if (!S.info) { $main.innerHTML = `<p class="muted">Загрузка…</p>`; return; }
    if (S.view === "trends") renderTrends();
    else if (S.view === "settings") renderPlatforms();
    else if (S.view === "stats") renderStats();
    else if (S.view === "post") window.Pub.renderPost();
    else if (S.view === "history") window.Pub.renderHistory();
    else renderSoon();
    if (S.view === "stats" && S.stats && S.stats.channels) {
      const el = document.getElementById("growth-chart");
      if (el) window.Charts.lineChart(el, S.stats.channels.map((c, i) => ({ name: c.title, color: ["var(--s1)", "var(--s2)", "var(--s3)"][i % 3], points: c.monthly })));
    }
    if (S.view === "trends" && S.data && S.fc) {
      const fcEl = document.getElementById("fc-chart");
      if (fcEl) forecastChart(fcEl, S.fc);
    }
    if (S.view === "trends" && S.data) {
      const nEl = document.getElementById("niche-chart");
      if (nEl) nicheChart(nEl, S.data.niches, pick);
    }
  }

  function renderTrends() {
    const st = S.info.trends[S.src];
    const job = st.job;
    const running = job && job.state === "running";
    const about = S.src === "e621"
      ? "e621 — крупнейший фурри-архив. ИИ-работы туда загружать нельзя, но это лучший индикатор вкусов фандома: тренды часто приходят оттуда."
      : "Ваша площадка для ИИ-работ. Популярность — избранное на пост: просмотров e6ai не отдаёт.";
    const dump = st.meta ? `выгрузка от ${st.meta.dump_date.split("-").reverse().join(".")} · ${num(st.meta.posts)} ${plural(st.meta.posts, "пост", "поста", "постов")} за 2 года` : "данных пока нет";
    let h = `
    <div class="head" style="align-items:flex-end">
      <div style="display:flex;flex-direction:column;gap:6px;max-width:760px">
        <h1>Тренды · ${S.src}</h1>
        <p class="muted">${about}</p>
      </div>
      <div style="display:flex;flex-direction:column;align-items:flex-end;gap:8px">
        ${seg("src", [["e6ai", "e6ai"], ["e621", "e621"]], S.src)}
        <div style="display:flex;align-items:center;gap:10px">
          <span class="muted small">${dump}</span>
          <button class="btn" data-action="update" ${running ? "disabled" : ""}>${st.ready ? "Обновить" : "Скачать"}</button>
          <button class="icon-btn" data-action="trends-settings" data-tip="Настройки трендов: мой стиль, скрытые теги, частота обновления" aria-label="Настройки трендов">${svg('<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>', 18)}</button>
        </div>
      </div>
    </div>`;
    if (S.trSet) h += trendsSettingsHtml();
    if (running) {
      h += `<div class="card"><div class="head"><h3>Готовлю данные ${S.src}</h3><span class="muted small">${esc(job.message)}</span></div>
        <div class="progress"><div style="width:${Math.round(job.progress * 100)}%"></div></div>
        <p class="note">Можно пользоваться приложением дальше — данные обновятся сами.</p></div>`;
    } else if (job && job.state === "error") {
      h += `<div class="card"><p class="err">Не получилось: ${esc(job.error)}</p>
        <p class="note">Проверьте интернет и ник в настройках. Если сайт недоступен, скачайте файлы вручную и подключите их ниже.</p></div>`;
    }
    if (!st.ready) {
      h += emptyState(running);
      $main.innerHTML = h;
      return;
    }
    if (S.loading && !S.data) { $main.innerHTML = h + `<p class="muted">Считаю…</p>`; return; }
    if (S.error) { $main.innerHTML = h + `<div class="card"><p class="err">${esc(S.error)}</p></div>`; return; }
    const d = S.data;
    if (!d) {   // данных ещё не спрашивали — спросим сейчас, а не оставим пустой экран
      $main.innerHTML = h + `<p class="muted">Считаю…</p>`;
      if (!S.loading) loadOverview().then(() => { if (S.view === "trends") render(); });
      return;
    }

    // опережающие сигналы
    if (d.leads === null) {
      const other = S.src === "e621" ? "e6ai" : "e621";
      h += `<div class="card"><h2>Опережающие сигналы</h2><p class="muted">Скачайте выгрузку ${other}, чтобы сравнивать, что растёт на e621 и ещё не дошло до e6ai.</p></div>`;
    } else {
      const card = (l) => `<div class="wrap">
        <button class="lead" data-pick="${esc(l.tag)}" data-src="e6ai" title="Открыть прогноз на e6ai">
          <span style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;padding-right:22px"><span class="tag" style="font-weight:600">${esc(l.tag)}</span>${l.mine ? `<span class="pill mine">ваш стиль</span>` : ""}</span>
          <span class="kv"><span>e621</span><b style="color:var(--good)">${num(l.e621_before)} → ${num(l.e621_now)}</b></span>
          <span class="kv"><span>e6ai</span><b>${num(l.e6ai_before)} → ${num(l.e6ai_now)}</b></span>
        </button>${hideBtn(l.tag)}</div>`;
      const grp = (title, note, list) => `<div style="display:flex;flex-direction:column;gap:8px">
        <div class="head"><h3>${title}</h3><span class="muted small">${note}</span></div>
        ${list.length ? `<div class="leads">${list.map(card).join("")}</div>` : `<p class="muted small">Сейчас ничего заметного.</p>`}</div>`;
      h += `<section class="card"><div class="head"><h2>Опережающие сигналы: растёт на e621, на e6ai ещё нет</h2>
        <span class="muted small">работ в месяц: 3 месяца назад → сейчас</span></div>
        ${grp("Растёт на e621", "теги, которые давно есть, но набирают обороты", d.leads.growing)}
        ${grp("Новое на e621", "полгода назад тега почти не было: новый персонаж, игра или мем", d.leads.new)}`;
      h += `</section>`;
    }

    // фильтры
    h += `<div class="bar">
      <span class="lbl">Рейтинг</span>${seg("rating", [["all", "Все"], ["s", "Safe"], ["q", "Questionable"], ["e", "Explicit"]], S.rating)}
      <span class="vsep"></span>
      <span class="lbl">Категория</span>${seg("cat", Object.entries(CAT_LABEL), S.cat)}
      <label class="check" style="margin-left:auto"><input type="checkbox" data-action="mine" ${S.mine ? "checked" : ""}> Только мой стиль</label>
    </div>`;
    if (S.mine && !S.info.settings.style_tags.length) {
      h += `<div class="card"><p class="muted">Список «мой стиль» пуст — добавьте теги в <a href="#" data-view-link="settings" data-tab="trends">настройках трендов</a>.</p></div>`;
    }

    // сезон
    if (d.seasonal && d.seasonal.length) {
      const s0 = d.seasonal[0], rest = d.seasonal.slice(1).map((s) => s.tag);
      h += `<section class="banner">
        <svg width="28" height="28" viewBox="0 0 28 28" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><rect x="4" y="6" width="20" height="18" rx="3"/><path d="M4 11h20M9 3v5M19 3v5"/></svg>
        <div style="flex:1;min-width:260px;display:flex;flex-direction:column;gap:2px">
          <b>${s0.now ? "Сезон идёт сейчас" : "Скоро сезон"}: <span class="tag">${esc(s0.tag)}</span>${s0.now ? "" : " — пик около " + s0.peak}</b>
          <span class="small">В прошлом году в эти недели работ с этим тегом было в ${s0.ratio.toFixed(1)} раза больше обычного.${rest.length ? " Рядом растут: " + rest.map(esc).join(", ") + "." : ""} ${s0.now ? "Если есть подходящая работа — самое время." : "Загружать лучше за 5–10 дней до пика."}</span>
        </div>
        <button class="btn" data-pick="${esc(s0.tag)}">Прогноз</button>
      </section>`;
    }

    // популярное и растущее
    const row = (x, i, kind) => {
      const val = kind === "pop" ? `<span class="val">${mult(x.lift)}</span>`
        : `<span class="val ${x.response >= 0 ? "up" : "down"}">${pct(x.response)}</span>`;
      const badge = x.badge ? `<span class="pill ${{ "сезон": "season", "ранний сигнал": "early", "ваш стиль": "mine" }[x.badge]}">${x.badge}</span>`
        : (kind === "pop" && x.mine ? `<span class="pill mine">ваш стиль</span>` : "");
      const title = kind === "pop"
        ? `${x.tag}: пост с тегом набирает ${mult(x.lift)} избранного к среднему посту; ${x.per_month} в месяц`
        : `${x.tag}: отклик ${pct(x.response)}, число работ ${pct(x.supply)} за 3 месяца`;
      return `<div class="wrap"><button class="li ${S.sel === x.tag ? "sel" : ""}" data-pick="${esc(x.tag)}" title="${esc(title)}">
        ${kind === "pop" ? `<span class="rank">${i + 1}</span>` : ""}
        <span class="name">${catDot(x.category)}<span class="tag">${esc(x.tag)}</span>${badge}</span>
        ${sparkline(x.spark)}${val}<span class="meta">${num(x.per_month)}/мес</span></button>${hideBtn(x.tag)}</div>`;
    };
    h += `<div class="row">
      <section class="card grow"><div class="head"><h2>Популярно сейчас</h2><span class="muted small">отклик за 4 недели</span></div>
        <div class="list">${d.popular.length ? d.popular.map((x, i) => row(x, i, "pop")).join("") : `<p class="muted">Нет тегов с достаточным числом постов.</p>`}</div></section>
      <section class="card grow"><div class="head"><h2>Набирает популярность</h2><span class="muted small">3 месяца к предыдущим 3</span></div>
        <div class="list">${d.rising.length ? d.rising.map((x, i) => row(x, i, "rise")).join("") : `<p class="muted">Заметного роста сейчас нет.</p>`}</div></section>
    </div>`;

    // прогноз
    if (S.fc) {
      const f = S.fc, ch = f.change;
      const verdict = ch > 0.08 ? "Рост " + pct(ch) : ch < -0.08 ? "Спад " + pct(ch) : "Без изменений";
      const last = f.forecast[f.forecast.length - 1];
      const bt = f.backtest && f.backtest.total ? `Проверка на истории: направление угадано в ${Math.round(f.backtest.accuracy * 100)}% случаев (${f.backtest.hits} из ${f.backtest.total}).` : "";
      h += `<section class="card" id="forecast"><div class="head"><h2>Прогноз на 4 недели · <span class="tag" style="font-size:16px">${esc(f.tag)}</span></h2>
        <span class="muted small">нажмите на любой тег выше, чтобы посмотреть его прогноз</span></div>
        <div class="row">
          <div style="flex:999 1 520px;min-width:0"><div class="chart" id="fc-chart"></div>
            <div class="legend" style="margin-top:8px">
              <span><svg width="22" height="8" aria-hidden="true"><line x1="0" x2="22" y1="4" y2="4" stroke="var(--s1)" stroke-width="2"/></svg>отклик по неделям</span>
              <span><svg width="22" height="8" aria-hidden="true"><line x1="0" x2="22" y1="4" y2="4" stroke="var(--s1)" stroke-width="2" stroke-dasharray="5 4"/></svg>прогноз</span>
              <span><span class="sw" style="background:color-mix(in srgb, var(--s1) 16%, transparent)"></span>вероятный диапазон</span>
            </div></div>
          <div style="flex:1 1 260px;min-width:0;display:flex;flex-direction:column;gap:12px">
            <div class="kpi"><span class="t">Через 4 недели</span><span class="v">${verdict}</span>
              <span class="small">отклик ${mult(last)} к среднему посту, вероятно ${mult(f.lo[3])} – ${mult(f.hi[3])}</span></div>
            <div class="kvlist">
              <div><span>Уверенность</span><b>${f.confidence}</b></div>
              <div><span>Новых работ в неделю</span><b>${num(f.weekly_posts)}</b></div>
              <div><span>За 3 месяца</span><b>${num(f.posts_90d)}</b></div>
              <div><span>Ваш стиль</span><b>${f.mine ? "да" : "нет"}</b></div>
            </div>
            <p class="note">${bt} Внезапные всплески (новый персонаж, мем) предсказать нельзя — их ловят «ранние сигналы».</p>
          </div>
        </div></section>`;
    }

    // ниши
    h += `<section class="card"><div class="head"><h2>Ниши: спрос и конкуренция</h2>
      <span class="muted small">слева вверху — высокий отклик при небольшом числе работ</span></div>
      <div class="row">
        <div style="flex:999 1 520px;min-width:0"><div class="chart" id="niche-chart"></div>
          <div class="legend" style="margin-top:8px">
            <span><span class="dot" style="background:var(--s1)"></span>виды</span>
            <span><span class="dot" style="background:var(--s2)"></span>темы</span>
            <span><span class="dot" style="background:var(--s3)"></span>персонажи и франшизы</span>
          </div></div>
        <div style="flex:1 1 260px;min-width:0;display:flex;flex-direction:column;gap:10px">
          <h3>Лучшие ниши${S.info.settings.style_tags.length ? " (ваш стиль — первыми)" : ""}</h3>
          ${d.niche_top.length ? d.niche_top.map((t) => `<button class="lead" data-pick="${esc(t.tag)}"><span class="tag" style="font-weight:600">${esc(t.tag)}</span><span class="small">${esc(t.text)}</span></button>`).join("")
            : `<p class="muted small">Явных ниш при текущих фильтрах нет.</p>`}
        </div>
      </div></section>`;

    h += `<p class="note">Отклик — во сколько раз пост с тегом набирает больше избранного, чем средний пост сайта той же недели и того же рейтинга. Так старые посты, успевшие собрать больше избранного, не перевешивают новые. Посты младше 7 дней не учитываются. Последняя учтённая неделя — с ${d.week_end}.</p>`;
    $main.innerHTML = h;
  }

  function emptyState(running) {
    const big = S.src === "e621";
    return `<section class="card empty">
      <h2>Данных ${S.src} ещё нет</h2>
      <p class="muted">Приложение скачает ежедневную выгрузку всей базы ${S.src} (${big ? "это большой файл, порядка гигабайта — займёт время" : "несколько десятков мегабайт"}) и посчитает тренды у вас на компьютере.</p>
      <div style="display:flex;flex-wrap:wrap;gap:10px">
        <button class="btn primary" data-action="update" ${running ? "disabled" : ""}>Скачать выгрузку ${S.src}</button>
        <button class="btn" data-action="manual">Подключить скачанные файлы</button>
      </div>
      ${S.manual ? manualForm() : ""}
    </section>`;
  }
  function manualForm() {
    const base = S.src === "e621" ? "https://e621.net/db_export/" : "https://e6ai.net/db_export/";
    const pickBtn = S.info.can_pick_files ? `<button class="btn" data-action="pickfiles">Выбрать файлы…</button>` : "";
    return `<div style="display:flex;flex-direction:column;gap:10px;width:100%;max-width:720px;margin-top:6px">
      <p class="note">Скачайте с ${base} два файла с одной датой: <span class="tag">posts-ГГГГ-ММ-ДД.csv.gz</span> и <span class="tag">tags-ГГГГ-ММ-ДД.csv.gz</span>.</p>
      ${pickBtn}
      <div class="field"><label for="mp">Файл постов</label><input id="mp" placeholder="C:\\Users\\…\\Downloads\\posts-2026-10-09.csv.gz"></div>
      <div class="field"><label for="mt">Файл тегов</label><input id="mt" placeholder="C:\\Users\\…\\Downloads\\tags-2026-10-09.csv.gz"></div>
      <div><button class="btn primary" data-action="import">Подключить</button></div>
    </div>`;
  }

  const fmtBytes = (b) => b >= 1e9 ? (b / 1e9).toFixed(1).replace(".", ",") + " ГБ" : b >= 1e6 ? Math.round(b / 1e6) + " МБ" : Math.max(0, Math.round(b / 1e3)) + " КБ";
  function trendsSettingsHtml() {
    const s = S.info.settings;
    return `<section class="card"><div class="head"><h2>Настройки трендов</h2><button class="icon-btn" data-action="trends-settings" aria-label="Закрыть">${ICON.close}</button></div>
        <div class="field"><label for="style">Мой стиль ${hint("Теги, которые встречаются в ваших работах. Через пробел или запятую, как на e621.")}</label>
          <textarea id="style" rows="3" class="code" placeholder="fennec_fox military desert camouflage">${esc((s.style_tags || []).join(" "))}</textarea></div>
        <div class="field"><label for="hidden">Скрытые теги ${hint("Не показываются в трендах. Добавляются кнопкой × в списках трендов; удалите тег отсюда, чтобы вернуть его.")}</label>
          <textarea id="hidden" rows="2" class="code" placeholder="collarbone nails">${esc((s.hidden_tags || []).join(" "))}</textarea></div>
        <div class="row">
          <div class="field" style="flex:1 1 200px"><label for="r6">Обновлять e6ai, раз в … дней</label><input id="r6" type="number" min="1" value="${s.trends.e6ai.refresh_days}"></div>
          <div class="field" style="flex:1 1 200px"><label for="r621">Обновлять e621, раз в … дней</label><input id="r621" type="number" min="1" value="${s.trends.e621.refresh_days}"></div>
        </div>
        <div class="row" style="align-items:flex-end"><div class="field" style="flex:1 1 260px"><label for="nick">Ник для запросов к e621 и e6ai ${hint("Сайты просят указывать ник в запросах программ — так они понимают, кто к ним обращается. Пароль не нужен.")}</label>
            <input id="nick" value="${esc(s.nickname || "")}" placeholder="например, fennec_artist"></div>
          <button class="btn" data-pa="nick-save">Сохранить ник</button></div>
        <div class="cardfoot"><button class="btn primary" data-action="save-settings">Сохранить</button><span class="ok small">${esc(S.flash)}</span></div></section>`;
  }
  function appSettingsHtml() {
    const w = S.worker;
    if (!S.storage && !S.storageLoading) { S.storageLoading = true; api("storage_info").then((r) => { S.storage = r; S.storageLoading = false; if (S.view === "settings") render(); }).catch(() => { S.storageLoading = false; }); }
    return `<section class="card setc"><div class="head"><h2>Фоновая отправка ${hint("Отдельный маленький процесс со значком у часов. Публикует запланированные посты и снимает цифры свежих постов, даже когда окно Crosspost закрыто.")}</h2>
          ${w ? `<span class="badge ${w.running ? "ok" : "warn"}" style="padding:2px 9px;border-radius:999px;font-size:12px;font-weight:600">${w.running ? "работает" : "выключена"}</span>` : ""}</div>
        ${w ? `<div class="status"><span>${w.running ? esc(w.next || "Работает") : "Запланированные посты выйдут, только пока открыто окно Crosspost."}</span><span class="grow1"></span>
            ${w.running ? `<button class="btn small danger" data-wk="stop">Выключить</button>` : `<button class="btn small primary" data-wk="start">Включить</button>`}</div>
          <label class="tgl"><input type="checkbox" data-pa="wk-auto" ${w.autostart ? "checked" : ""} ${w.platform === "win32" ? "" : "disabled"}><span>Запускать вместе с Windows</span></label>` : `<p class="muted">Проверяю…</p>`}</section>
      ${updHtml()}
      <section class="card setc"><div class="head"><h2>Оформление</h2></div>
        <div class="field" style="max-width:260px"><label for="themesel">Тема</label><select id="themesel"><option value="dark" ${store.get("theme", "dark") !== "light" ? "selected" : ""}>Тёмная</option><option value="light" ${store.get("theme", "dark") === "light" ? "selected" : ""}>Светлая</option></select></div></section>
      <section class="card setc"><div class="head"><h2>Значок и ярлык ${hint("Ярлык запускает Crosspost напрямую, без файла Crosspost.bat — поэтому Windows не показывает окно «Не удаётся проверить издателя», а в панели задач виден значок Crosspost. Закрепить на панели задач: правой кнопкой по ярлыку → «Закрепить на панели задач».")}</h2></div>
        <div style="display:flex;gap:14px;align-items:center;flex-wrap:wrap"><img src="/appicon.ico" alt="Значок приложения" width="40" height="40" style="border-radius:10px">
          <button class="btn" data-action="mk-shortcut">Создать ярлык на рабочем столе и в «Пуске»</button></div>
        <p class="muted small" style="margin:0">Если значок у ярлыка старый — создайте ярлык заново.</p></section>
      <section class="card setc"><div class="head"><h2>Место на диске ${hint("Файлы, добавленные в пост, копируются в папку приложения — поэтому перемещение исходников ничего не ломает. Тяжёлые копии удаляются через 7 дней после публикации; миниатюры и JPEG-превью остаются.")}</h2></div>
        ${S.storage ? `<div class="kvlist"><div><span>Копии оригиналов</span><b>${fmtBytes(S.storage.originals)}</b></div>
          <div><span>JPEG-превью</span><b>${fmtBytes(S.storage.light)}</b></div>
          <div><span>Миниатюры и превью</span><b>${fmtBytes(S.storage.thumbs + S.storage.previews)}</b></div>
          <div><span>Готовые файлы (не удаляются) ${hint("Папка «Готовые файлы» в «Настройки» → «Пост»: JPEG-превью, GIF и видео, которые сделала программа.")}</span><b>${fmtBytes(S.storage.outputs || 0)}</b></div></div>
          <div class="cardfoot"><button class="btn" data-action="storage-free">Освободить сейчас</button><span class="muted small">${esc(S.flash2 || "Удалит копии оригиналов всех опубликованных постов. Запланированные не трогаются.")}</span></div>` : `<p class="muted">Считаю…</p>`}</section>
      ${perfHtml()}
      <p class="note">Версия ${esc(S.info.version)}. Данные хранятся только на вашем компьютере.</p>`;
  }
  // ----- производительность: память и процессор частей Crosspost, медленные фоновые задачи -----
  function loadPerf() {
    if (S.perfLoading) return;
    S.perfLoading = true;
    api("perf_status").then((r) => { S.perf = r; S.perfLoading = false; if (S.view === "settings" && S.platSec === "app") render(); })
      .catch(() => { S.perfLoading = false; });
  }
  function perfHtml() {
    if (!S.perf) { loadPerf(); return `<section class="card setc"><div class="head"><h2>Производительность</h2></div><p class="muted">Считаю…</p></section>`; }
    const n = S.perf.now || {}, sm = S.perf.summary || {};
    const roleName = { window: "Окно", worker: "Фоновая отправка" };
    let h = `<section class="card setc"><div class="head"><h2>Производительность ${hint("Сколько памяти и процессора берёт каждая часть Crosspost прямо сейчас, и какие фоновые задачи шли дольше всего за сутки. Если компьютер тормозит — нажмите «Сохранить отчёт» и пришлите файл: в нём нет ключей, токенов и текстов постов.")}</h2>
      <button class="btn small ghost" data-action="perf-refresh" ${S.perfLoading ? "disabled" : ""}>Обновить</button></div>`;
    if (!n.psutil) h += `<p class="muted small">${esc(n.note || "")}</p>`;
    else {
      h += `<div class="kvlist">${n.groups.map((g) => `<div><span>${esc(g.name)}${g.n > 1 ? ` <span class="muted small">×${g.n}</span>` : ""}</span><span>${num(g.mb)} МБ · процессор ${String(g.cpu).replace(".", ",")}%</span></div>`).join("")}
        <div><span><b>Всего Crosspost</b></span><b>${num(n.total_mb)} МБ</b></div></div>
        <p class="muted small">Память компьютера занята на ${n.system.ram_used}% из ${String(n.system.ram_gb).replace(".", ",")} ГБ.</p>`;
      if (n.groups.some((g) => /Браузер/.test(g.name))) h += `<div class="callout"><p>Открыт браузер Crosspost (сбор статистики ВК или публикация). Если сейчас ничего не идёт — его можно закрыть.</p><button class="btn small" data-action="perf-kill">Закрыть браузер</button></div>`;
    }
    const mem = (sm.memory || []).filter((m) => m.hours >= 0.5);
    if (mem.length) h += `<h3>Память за сутки</h3><div class="kvlist">${mem.map((m) => `<div><span>${roleName[m.role] || m.role}</span><span>было ${num(m.first)} МБ → сейчас ${num(m.last)} МБ, максимум ${num(m.max)} МБ · процессор в среднем ${String(m.cpu_avg).replace(".", ",")}%</span></div>`).join("")}</div>`;
    const slow = (sm.tasks || []).slice(0, 6);
    if (slow.length) h += `<h3>Фоновые задачи за сутки</h3><div class="kvlist">${slow.map((t) => `<div><span>${esc(t.name)} <span class="muted small">${roleName[t.role] || t.role}</span></span><span>${t.n} раз · всего ${String(t.s).replace(".", ",")} с · дольше всего ${String(t.max_s).replace(".", ",")} с${t.errors ? ` · <span class="err">ошибок ${t.errors}</span>` : ""}</span></div>`).join("")}</div>`;
    h += `<div class="cardfoot"><button class="btn" data-action="perf-report">Сохранить отчёт</button>
      ${(n.groups || []).some((g) => g.name === "Теггер") ? `<button class="btn ghost" data-action="perf-tagger">Выгрузить теггер</button>` : ""}
      <span class="muted small">Чтобы поймать тормоза, оставьте Crosspost открытым на час, потом сохраните отчёт.</span></div></section>`;
    return h;
  }
  function trendsSecHtml() {
    return trendsSettingsHtml().replace(/<button class="icon-btn" data-action="trends-settings"[^>]*>[\s\S]*?<\/button>/, "");
  }

  // меню настроек: группа, пункты, когда группа видна (по включённым функциям)
  const PSECS = [["Приложение", [["app", "Общие"], ["features", "Функции"], ["net", "Соединение"]], () => true],
    ["Площадки", [["telegram", "Telegram"], ["discord", "Discord"], ["vk", "ВКонтакте"], ["bluesky", "Bluesky"], ["deviantart", "DeviantArt"], ["e6ai", "e6ai / e621"]], () => true],
    ["Текст поста", [["signatures", "Подписи"], ["hashtags", "Хэштеги"]], () => modOn("crosspost")],
    ["Файлы поста", [["light", "JPEG-превью"], ["media", "Видео и GIF"], ["files", "Готовые файлы"]], () => modOn("crosspost")],
    ["Теги", [["tags", "Теггер и база тегов"]], () => modOn("tagger") || modOn("e6ai")],
    ["Статистика", [["collect", "Сбор статистики"], ["import", "Импорт из Telegram"]], () => statsOn()],
    ["Тренды", [["trends", "Тренды"]], () => modOn("trends")]];
  // четыре функции приложения; у каждой — модули, которые она включает и выключает разом
  const FEATURES = [
    ["crosspost", "Кросспостинг", "Новый пост, публикация на площадки, расписание и «История».", ["crosspost", "history"]],
    ["tags", "Подбор тегов", "Теггер подбирает теги по картинке — для хэштегов и e6ai / e621. Без него теги вписываются вручную.", ["tagger"]],
    ["stats", "Статистика", "Замеры просмотров, цифры с площадок, индекс успеха, новости и прогноз.", ["stats", "collect", "news", "forecast"]],
    ["trends", "Тренды", "Что набирает популярность на e6ai и e621, ниши и «мой стиль».", ["trends"]]];
  const featOn = (f) => f[3].some((k) => modOn(k));
  const statsOn = () => modOn("stats") || modOn("collect");
  const MODS = { platforms: [["telegram", "Telegram", "публикация в каналы и группу"], ["discord", "Discord", "вебхуки"], ["vk", "ВКонтакте", "группа"], ["bluesky", "Bluesky", ""], ["deviantart", "DeviantArt", ""], ["e6ai", "e6ai / e621", "загрузка, теги, теггер и база тегов"]],
    extras: [["tagger", "Теггер", "подбирает теги по картинке для e6ai / e621 и хэштегов; без него теги вписываются вручную"],
      ["collect", "Сбор статистики", "замеры просмотров, цифры с сайтов, сверка правок модераторов, досылка превью"],
      ["news", "Новости по свежим артам", "колокольчик вверху: хиты, всплески реакций, комментарии"],
      ["forecast", "Прогноз", "ожидаемые просмотры и лучшее время на экране поста"]],
    screens: [["history", "История", "календарь и список публикаций"], ["stats", "Статистика", "просмотры и индекс успеха каналов"], ["trends", "Тренды", "e6ai и e621, обновление выгрузок"]] };
  const modOn = (k) => !(S.info && S.info.modules) || S.info.modules[k] !== false;
  // какие экраны видны: «Новый пост» и «История» — кросспостинг, «Статистика» и «Тренды» — свои функции, «Настройки» — всегда
  const viewOn = (v) => v === "settings" ? true : v === "post" ? modOn("crosspost") : v === "history" ? modOn("crosspost") && modOn("history") : modOn(v);
  function applyModules() {   // скрыть кнопки отключённых экранов; если открыт отключённый — перейти к первому включённому
    document.querySelectorAll("#nav button[data-view]").forEach((b) => { b.hidden = !viewOn(b.dataset.view); });
    const nb = document.getElementById("newsbtn"); if (nb) nb.hidden = !(modOn("news") && modOn("collect"));
    paintTagger();
    if (!(modOn("news") && modOn("collect"))) { const np = document.getElementById("newspop"); if (np) np.hidden = true; }
    if (!viewOn(S.view)) { S.view = ["post", "history", "stats", "trends", "settings"].find(viewOn); store.set("view", S.view); return true; }
    return false;
  }
  function featuresHtml() {
    const row = ([k, l, sub]) => `<label class="tgl modrow"><input type="checkbox" data-mod="${k}" ${modOn(k) ? "checked" : ""}><span><b>${l}</b>${sub ? ` <span class="muted small">${esc(sub)}</span>` : ""}</span></label>`;
    return `<section class="card setc"><div class="head"><h2>Функции ${hint("Выключенная функция не работает ни в окне, ни в фоновой отправке и не обращается к сайтам; её экраны и настройки скрываются. Накопленные данные и ключи не удаляются — включите обратно, и всё на месте.")}</h2></div>
        <div class="featlist">${FEATURES.map((f) => { const on = featOn(f); return `<label class="feat ${on ? "on" : ""}">
          <span class="tgl"><input type="checkbox" data-feat="${f[0]}" ${on ? "checked" : ""} aria-label="${f[1]}"></span>
          <span><b>${f[1]}</b><span class="muted small">${esc(f[2])}</span></span></label>`; }).join("")}</div></section>
      <section class="card setc"><div class="head"><h2>Площадки ${hint("Выключенная площадка пропадает из списка «Публикация», в неё ничего не публикуется (и фоновой отправкой тоже), цифры с неё не собираются. Ключи и настройки не стираются.")}</h2></div>
        <div class="modlist">${MODS.platforms.map(row).join("")}</div></section>`;
  }
  function netSecHtml() {
    const t = S.tg;
    if (!t || t.unknown) return `<section class="card setc"><p class="muted">Проверяю…</p></section>`;
    if (!t.configured) return `<section class="card setc"><div class="head"><h2>Соединение с Telegram</h2></div>
      <p class="muted">Соединение настраивается после того, как сохранены ключи API Telegram: ${statsOn() ? `<a href="#" data-view-link="settings" data-tab="import">«Статистика» → «Импорт из Telegram»</a>` : `включите «Статистика» в <a href="#" data-view-link="settings" data-tab="features">«Функциях»</a> — ключи вводятся там`}. Боту по Bot API оно не нужно.</p></section>`;
    return `<section class="card setc"><div class="head"><h2>Соединение с Telegram ${hint("Для входа в аккаунт Telegram (статистика) и для бота, который работает через MTProto. zapret, прокси и ссылки на прокси из Telegram Desktop.")}</h2></div>
      ${S.tgErr ? `<p class="err">${esc(S.tgErr)}</p>` : ""}${netHtml(t, true)}</section>`;
  }
  function platNav() {
    const st = (k) => {
      if (k === "app") { const w = S.worker; return [w && w.running ? "ok" : "", w ? (w.running ? "фоновая отправка работает" : "фоновая отправка выключена") : ""]; }
      if (k === "trends") return ["", "мой стиль, частота обновления"];
      if (k === "features") return ["", FEATURES.filter(featOn).map((f) => f[1].toLowerCase()).join(", ") || "всё выключено"];
      if (k === "net") { const n = S.tg && S.tg.net, p = n && n.proxy; return ["", p && p.type ? (p.type === "mtproxy" ? "MTProxy" : p.type.toUpperCase()) + " " + (p.host || "") : "напрямую"]; }
      if (k === "collect") return [modOn("collect") ? "ok" : "", modOn("collect") ? "собираем цифры" : "сбор выключен"];
      if (k === "tags") { const tg = window.Pub && S.tagger; return [tg && tg.ready && modOn("tagger") ? "ok" : "", !modOn("tagger") ? "подбор выключен" : tg && tg.ready ? "теггер установлен" : "теггер не установлен"]; }
      if (k === "media") return window.Pub.platStatus(k);
      if (k === "import") { const n = S.tgCh.filter((c) => c.selected && c.posts).length; return [n ? "ok" : "", n ? `${n} ${plural(n, "канал", "канала", "каналов")} в статистике` : "только чтение каналов"]; }
      return window.Pub.platStatus(k);
    };
    const isPlat = (k) => MODS.platforms.some((m) => m[0] === k);
    return `<nav class="platnav" aria-label="Разделы настроек">${PSECS.filter((g) => g[2]()).map(([g, items]) => `${g ? `<h3>${g}</h3>` : ""}${items.map(([k, l]) => {
      const off = isPlat(k) && !modOn(k);
      const [lamp, sub] = off ? ["", "выключена"] : st(k);
      const btn = `<button class="pn ${S.platSec === k ? "on" : ""} ${off ? "off" : ""} ${isPlat(k) ? "hassw" : ""}" data-psec="${k}" aria-current="${S.platSec === k}"><span class="lamp ${lamp}"></span><span><b>${l}</b>${sub ? `<span>${esc(sub)}</span>` : ""}</span></button>`;
      return isPlat(k) ? `<div class="pnwrap">${btn}<span class="tgl pnsw" data-tip="${off ? "Включить" : "Выключить"} площадку «${l}»"><input type="checkbox" data-mod="${k}" ${off ? "" : "checked"} aria-label="Площадка ${l}"></span></div>` : btn;
    }).join("")}`).join("")}</nav>`;
  }
  function renderPlatforms() {
    const head = `<div class="toolbar"><h1>Настройки</h1><span class="muted small">Ключи и токены хранятся только на этом компьютере</span></div>`;
    if (S.platSec === "modules") S.platSec = "features";   // раздел переименован
    const visible = PSECS.filter((g) => g[2]()).flatMap((g) => g[1].map((x) => x[0]));
    if (!visible.includes(S.platSec)) S.platSec = "features";   // раздел скрыт выключенной функцией
    const offPlat = MODS.platforms.find((m) => m[0] === S.platSec && !modOn(m[0]));
    const body = offPlat ? `<section class="card setc"><div class="head"><h2>${offPlat[1]} выключена</h2></div>
        <p class="muted">Площадка скрыта из публикации, фоновой отправки и сбора цифр. Ключи и настройки сохранены.</p>
        <div class="cardfoot" style="border:0;padding:0"><button class="btn primary" data-modon="${offPlat[0]}">Включить ${esc(offPlat[1])}</button></div></section>`
      : S.platSec === "features" ? featuresHtml() : S.platSec === "app" ? appSettingsHtml() : S.platSec === "trends" ? trendsSecHtml()
      : S.platSec === "net" ? netSecHtml() : S.platSec !== "import" ? window.Pub.settingsHtml(S.platSec) : null;
    if (body !== null) {
      $main.innerHTML = head + `<div class="platlayout">${platNav()}<div class="platbody">${body}</div></div>`;
      return;
    }
    const t = S.tg, job = S.info.tg_job, running = job && job.state === "running";
    let h = `<section class="card" style="max-width:900px"><div class="head"><h2>${pbadge("telegram")} Telegram — статистика каналов ${hint("Здесь приложение только читает ваши каналы — ничего не публикует и не пишет от вашего имени.")}</h2>
      ${S.tgCh.some((c) => c.posts) ? `<span class="badge ok" style="padding:2px 9px;border-radius:999px;font-size:12px;font-weight:600">есть данные</span>` : ""}</div>`;
    // 1. Импорт выгрузки Telegram Desktop — основной способ, без ключей
    const pick = S.info.can_pick_files ? `<button class="btn" data-action="tg-pickjson">Выбрать файл…</button>` : "";
    h += `<h3>Выгрузка из Telegram Desktop</h3>
      <details class="how" ${S.tgCh.some((c) => c.posts) ? "" : "open"}><summary>Как сделать выгрузку</summary><ol class="steps">
        <li>В Telegram Desktop откройте канал → <b>⋮</b> → <b>«Экспорт истории чата»</b>.</li>
        <li>Галочку оставьте только у <b>«Фото»</b> (для превью), формат — <b>«JSON (машиночитаемый)»</b>, период — за всё время.</li>
        <li>Укажите здесь файл <code>result.json</code> из созданной папки (или саму папку). Так для каждого канала.</li></ol></details>
      <div class="row" style="align-items:flex-end"><div class="field" style="flex:1 1 360px"><label for="tgjson">Файл выгрузки</label>
        <input id="tgjson" value="${esc(S.tgJson || "")}" placeholder="C:\\Users\\…\\Downloads\\Telegram Desktop\\ChatExport_2026-10-10\\result.json"></div>
        <div style="display:flex;gap:10px">${pick}<button class="btn primary" data-action="tg-import" ${running ? "disabled" : ""}>Импортировать</button></div></div>
      <p class="muted small">В выгрузке нет просмотров, только реакции ${hint("Поэтому успех таких каналов считается по реакциям. Чтобы обновить цифры, сделайте выгрузку заново и импортируйте её: посты не задвоятся.")}</p>`;
    if (running) h += `<div class="progress"><div style="width:${Math.round(job.progress * 100)}%"></div></div><p class="muted small">${esc(job.message)}</p>`;
    else if (job && job.state === "error") h += `<p class="err">Импорт не удался: ${esc(job.error)}</p>`;
    else if (job && job.state === "done") h += `<p class="ok small">Готово: ${esc(job.message)}</p>`;

    // 2. Каналы с данными
    if (S.tgCh.length) {
      h += `<div class="head" style="margin-top:8px"><h3>Ваши каналы</h3>${t && t.authorized ? `<button class="btn" data-action="tg-refresh-ch" ${S.tgBusy ? "disabled" : ""}>Обновить список</button>` : ""}</div>
        <div class="chlist">${S.tgCh.map((c) => `<div class="chrow">
          <label class="tgl" style="flex:1;min-width:200px" data-tip="Учитывать в статистике"><input type="checkbox" data-chsel="${esc(c.chat_id)}" ${c.selected ? "checked" : ""}>
            <span><b>${esc(c.title || c.chat_id)}</b>${c.username ? ` <span class="muted small">@${esc(c.username)}</span>` : ""}<br>
            <span class="muted small">${c.posts ? num(c.posts) + " " + plural(c.posts, "пост", "поста", "постов") : "ещё не импортирован"}${c.synced_at ? " · обновлён " + new Date(c.synced_at).toLocaleString("ru-RU") : ""}</span></span></label>
          <div class="seg" role="radiogroup" aria-label="Что публикуется в канале">${[["sfw", "SFW"], ["nsfw", "18+"], ["all", "Любой"]].map(([k, l]) =>
            `<button role="radio" aria-checked="${c.rating === k}" class="${c.rating === k ? "on" : ""}" data-chrating="${esc(c.chat_id)}" data-val="${k}">${l}</button>`).join("")}</div>
        </div>`).join("")}</div>`;
      if (S.tgCh.some((c) => c.posts)) h += `<div><button class="btn" data-view-link="stats">Открыть статистику</button></div>`;
    }

    // 3. Подключение по API — по желанию, даёт просмотры и автообновление
    h += `<details class="more" id="apibox" ${S.apiOpen || (t && t.configured) ? "open" : ""}><summary>Подключение через API Telegram (необязательно) — даёт просмотры и обновление одной кнопкой</summary><div class="morebody">`;
    if (S.tgErr) h += `<p class="err">${esc(S.tgErr)}</p>`;
    if (S.tgBusy) h += `<p class="muted small">Подождите, связываюсь с Telegram…</p>`;
    if (!t || t.unknown) h += `<p class="muted">Проверяю…</p>`;
    else if (!t.configured) {
      h += `<ol class="steps">
          <li>Откройте <a href="#" data-open="https://my.telegram.org/apps">my.telegram.org/apps</a> и войдите по номеру телефона (код придёт в Telegram).</li>
          <li>Создайте приложение: название латиницей (например, Crosspost Stats), платформа — Desktop.</li>
          <li>Скопируйте сюда <b>App api_id</b> и <b>App api_hash</b>.</li></ol>
        <div class="row"><div class="field" style="flex:1 1 180px"><label for="apiid">api_id</label><input id="apiid" inputmode="numeric" placeholder="1234567"></div>
          <div class="field" style="flex:3 1 320px"><label for="apihash">api_hash</label><input id="apihash" placeholder="0123456789abcdef0123456789abcdef"></div></div>
        <div><button class="btn primary" data-action="tg-keys" ${S.tgBusy ? "disabled" : ""}>Сохранить ключи</button></div>
        <p class="note">Эти ключи — пропуск программы в Telegram, а не пароль от аккаунта. Не публикуйте их.</p>`;
    } else if (!t.authorized) {
      h += `<p class="muted small">Не подключается? Настройте zapret или прокси: <a href="#" data-view-link="settings" data-tab="net">«Приложение» → «Соединение»</a>.</p>`;
      if (t.need_password) {
        h += `<div class="field" style="max-width:360px"><label for="tgpw">Облачный пароль (двухэтапная проверка)</label><input id="tgpw" type="password"></div>
          <div><button class="btn primary" data-action="tg-password" ${S.tgBusy ? "disabled" : ""}>Войти</button></div>`;
      } else if (t.code_sent) {
        h += `<div class="field" style="max-width:360px"><label for="tgcode">Код из Telegram</label><input id="tgcode" inputmode="numeric" placeholder="12345"></div>
          <div style="display:flex;gap:10px"><button class="btn primary" data-action="tg-code" ${S.tgBusy ? "disabled" : ""}>Войти</button>
          <button class="btn" data-action="tg-resend">Другой номер</button></div>
          ${t.notice ? `<div class="callout warn"><p>${esc(t.notice)}</p></div>` : `<p class="note">Код придёт в само приложение Telegram (в чат «Telegram»), а не в SMS.</p>`}`;
      } else {
        h += `<div class="field" style="max-width:360px"><label for="tgphone">Номер телефона</label><input id="tgphone" inputmode="tel" placeholder="+7 999 123-45-67"></div>
          <div style="display:flex;gap:10px"><button class="btn primary" data-action="tg-phone" ${S.tgBusy ? "disabled" : ""}>Получить код</button>
          <button class="btn" data-action="tg-rekeys">Изменить ключи</button></div>`;
      }
    } else {
      const me = t.me || {};
      const any = S.tgCh.some((c) => c.selected);
      h += `<div style="display:flex;flex-wrap:wrap;align-items:center;gap:12px"><span>Вход выполнен: <b>${esc(me.name || "")}</b>${me.username ? " · @" + esc(me.username) : ""}</span>
          <button class="btn" data-action="tg-refresh-ch" ${S.tgBusy ? "disabled" : ""}>Обновить список каналов</button>
          <button class="btn" data-action="tg-logout">Выйти</button></div>
        <div style="display:flex;flex-wrap:wrap;gap:10px;align-items:center"><button class="btn primary" data-action="tg-sync" ${running || !any ? "disabled" : ""}>Загрузить посты и просмотры</button>
        <span class="muted small">${any ? "Первый раз загрузится вся история — это может занять несколько минут." : "Отметьте каналы выше."}</span></div>`;
    }
    h += `</div></details></section>`;
    $main.innerHTML = head + `<div class="platlayout">${platNav()}<div class="platbody">${h}</div></div>`;
  }


  // ----- настройки соединения: zapret / прокси -----
  function netHtml(t, bare) {
    if (!S.netDraft) S.netDraft = JSON.parse(JSON.stringify(t.net || { transport: "obfuscated", proxy: {} }));
    const n = S.netDraft, p = n.proxy || {}, pt = p.type || "";
    const open = S.netOpen || (t.error && !t.authorized);
    const segN = (key, opts, cur) => `<div class="seg" role="radiogroup">${opts.map(([k, l]) =>
      `<button role="radio" aria-checked="${cur === k}" class="${cur === k ? "on" : ""}" data-net="${key}" data-val="${k}">${l}</button>`).join("")}</div>`;
    let h = (bare ? `<div id="netbox">` : `<details class="more" id="netbox" ${open ? "open" : ""}><summary>Настройки соединения — zapret, прокси</summary><div class="morebody">`) + `
      <p class="note">Если Telegram Desktop у вас работает через zapret <b>без прокси</b> — оставьте «Напрямую» и нажмите «Проверить связь».
        Если в Telegram Desktop указан прокси — проще всего вставить ссылку на него (ниже), вписывать вручную не нужно.</p>
      <div class="field"><label for="nlinks">Ссылки на прокси из Telegram</label>
        <textarea id="nlinks" rows="3" placeholder="tg://proxy?server=…&port=443&secret=…  или  https://t.me/proxy?…">${esc(S.netLinks || "")}</textarea>
        <span class="muted small">Где взять: Telegram Desktop → Настройки → Продвинутые → Тип соединения → у прокси «⋮» → «Поделиться» (ссылка копируется).
          Можно вставить сразу несколько — или ссылки из канала с прокси. Рабочие станут запасными: если текущий прокси перестанет отвечать, приложение само переключится.</span></div>
      <div style="display:flex;gap:10px;align-items:center;margin-bottom:10px"><button class="btn primary" data-action="net-links" ${S.tgBusy ? "disabled" : ""}>Добавить и проверить</button></div>
      <div class="row" style="align-items:center"><span class="lbl" style="min-width:90px">Прокси</span>${segN("ptype", [["", "Напрямую"], ["socks5", "SOCKS5"], ["http", "HTTP"], ["mtproxy", "MTProxy"]], pt)}</div>`;
    if (!pt) {
      h += `<div class="row" style="align-items:center"><span class="lbl" style="min-width:90px">Протокол</span>${segN("transport", [["obfuscated", "Как Telegram Desktop"], ["full", "Обычный"]], n.transport || "obfuscated")}</div>`;
    } else {
      h += `<div class="row"><div class="field" style="flex:2 1 200px"><label for="nhost">Адрес</label><input id="nhost" value="${esc(p.host || "")}" placeholder="127.0.0.1"></div>
        <div class="field" style="flex:1 1 100px"><label for="nport">Порт</label><input id="nport" inputmode="numeric" value="${esc(p.port || "")}" placeholder="${pt === "mtproxy" ? "443" : "1080"}"></div></div>`;
      h += pt === "mtproxy"
        ? `<div class="field"><label for="nsecret">Секрет (secret)</label><input id="nsecret" value="${esc(p.secret || "")}" placeholder="dd0123456789abcdef0123456789abcdef"></div>`
        : `<div class="row"><div class="field" style="flex:1 1 160px"><label for="nuser">Логин (если есть)</label><input id="nuser" value="${esc(p.user || "")}"></div>
           <div class="field" style="flex:1 1 160px"><label for="npass">Пароль</label><input id="npass" type="password" placeholder="${p.has_password ? "сохранён" : ""}"></div></div>`;
    }
    const bk = (t.net && t.net.proxies) || [];
    if (bk.length) {
      const cur = (t.net && t.net.proxy) || {};
      h += `<div class="lbl" style="margin-top:6px">Прокси в запасе (${bk.length})</div><div class="kvlist">${bk.map((x) => {
        const on = x.host === cur.host && String(x.port) === String(cur.port);
        const ftls = x.type === "mtproxy" && String(x.secret || "").toLowerCase().startsWith("ee");
        return `<div><span>${x.type === "mtproxy" ? "MTProxy" : "SOCKS5"} · ${esc(x.host)}:${esc(x.port)}${ftls ? " · FakeTLS" : ""}${on ? " · <b>сейчас</b>" : ""}</span>
          <button class="btn small" data-proxyforget="${esc(x.host)}|${esc(x.port)}" ${S.tgBusy ? "disabled" : ""}>Забыть</button></div>`; }).join("")}</div>`;
      if (t.net.faketls === false && bk.some((x) => String(x.secret || "").toLowerCase().startsWith("ee")))
        h += `<p class="err small">Для прокси FakeTLS (секрет начинается с ee) нужна библиотека TelethonFakeTLS — запустите Install.bat ещё раз.</p>`;
    }
    h += `<div style="display:flex;flex-wrap:wrap;gap:10px;align-items:center">
        <button class="btn primary" data-action="net-check" ${S.tgBusy ? "disabled" : ""}>Проверить связь</button>
        <button class="btn" data-action="net-save" ${S.tgBusy ? "disabled" : ""}>Сохранить</button>
        <span class="muted small">Проверка пробует ваш вариант, затем «напрямую» двумя способами, и сохраняет первый рабочий.</span></div>`;
    if (S.netRes) h += `<div class="kvlist">${S.netRes.map((r) => `<div><span>${esc(r.label)}</span>${r.ok
        ? `<b class="ok">работает · ${r.ms} мс</b>` : `<span class="err">${esc(r.error)}</span>`}</div>`).join("")}</div>
        ${S.netRes.some((r) => r.ok) ? `<p class="ok small">Рабочий вариант сохранён — можно входить.</p>` : `<p class="small">Ни один вариант не прошёл. Проверьте, что zapret запущен и Telegram Desktop работает, и пришлите мне эти строки.</p>`}`;
    return h + (bare ? `</div>` : `</div></details>`);
  }
  function syncNet() {
    const n = S.netDraft; if (!n) return;
    n.proxy = n.proxy || {};
    const v = (id) => { const el = document.getElementById(id); return el ? el.value.trim() : undefined; };
    [["nhost", "host"], ["nport", "port"], ["nsecret", "secret"], ["nuser", "user"], ["npass", "password"]].forEach(([id, k]) => {
      const x = v(id); if (x !== undefined) n.proxy[k] = x;
    });
  }

  function freshHtml(st) {
    const c = st && st._cache;
    if (!c) return "";
    const mins = Math.max(0, Math.round((Date.now() / 1000 - c.at) / 60));
    const ago = mins < 1 ? "только что" : mins < 60 ? `${mins} мин назад` : `${Math.round(mins / 60)} ч назад`;
    return `<span class="fresh ${c.stale ? "upd" : ""}" data-tip="${c.stale ? "Данные поменялись — пересчитываю в фоне, экран обновится сам" : "Посчитано заранее, в фоне"}">${c.stale ? `<span class="spin"></span>обновляю…` : `обновлено ${ago}`}</span>`;
  }
  function renderStats() {
    const st = S.stats;
    const metricWord = st && st.metric === "reactions" ? "реакций" : st && st.metric === "mixed" ? "просмотров (или реакций, где просмотров нет)" : "просмотров";
    const idxHint = hint(`Индекс успеха — во сколько раз пост набрал больше ${metricWord}, чем обычный пост того же канала в те же недели. ×1 — обычный, ×2 — вдвое лучше.`);
    if (!S.tgCh.some((c) => c.selected && c.posts)) {
      $main.innerHTML = `<div class="toolbar"><h1>Статистика</h1></div>
        <section class="card soon"><h2>Пока нет данных</h2><p class="muted">Импортируйте посты Telegram — из выгрузки Telegram Desktop или через API.</p>
        <div><button class="btn primary" data-view-link="platforms" data-tab="import">Импортировать посты</button></div></section>`;
      return;
    }
    const chans = S.tgCh.filter((c) => c.selected);
    let h = `<div class="toolbar"><h1>Статистика</h1>
      ${st && st.excluded ? `<button class="chipbtn" data-unexclude-all data-tip="Анонсы и другие посты, которые вы отметили «не учитывать». Нажмите, чтобы вернуть все.">${ICON.eyeoff} Не учитываются: ${num(st.excluded)}</button>` : ""}
      <span class="spacer"></span>${freshHtml(st)}
      ${seg("sp", [["7", "7 дн"], ["30", "30 дн"], ["90", "90 дн"], ["365", "Год"], ["all", "Всё"]], S.sp)}
      <select class="sm" id="statch" aria-label="Канал"><option value="">Все каналы</option>${chans.map((c) => `<option value="${esc(c.chat_id)}" ${S.sc === c.chat_id ? "selected" : ""}>${esc(c.title || c.chat_id)}</option>`).join("")}</select></div>`;
    const tj = S.info.tg_job;
    if (tj && tj.state === "running") h += progressHtml(tj);
    if (S.statsLoading && !st) { $main.innerHTML = h + `<p class="muted">Считаю…</p>`; return; }
    if (!st) { $main.innerHTML = h; return; }
    if (st.error) { $main.innerHTML = h + `<div class="card"><p class="err">${esc(st.error)}</p></div>`; return; }
    if (st.empty_period) { $main.innerHTML = h + `<div class="card"><p class="muted">${tj && tj.state === "running" ? "Посты за этот период ещё не загружены — история загружается от старых постов к новым. Можно выбрать период «Всё»." : "За этот период постов нет."}</p></div>`; return; }
    const pv = st.prev, per = { "7": "7 дням", "30": "30 дням", "90": "90 дням", "365": "году" }[st.period];
    const vs = per ? `<span>к прошлым ${per}</span>` : "";
    const tile = (icon, label, value, foot, tip) => `<div class="tile"><span class="ico">${icon}</span>
      <span class="tl">${label}${tip ? " " + tip : ""}</span><span class="tv">${value}</span><span class="tf">${foot}</span></div>`;
    const byViews = st.metric !== "reactions";
    h += `<div class="tiles">
      ${tile(ICON.image, "Постов с артом", num(st.posts), pv ? delta(st.posts, pv.posts) + vs : "", hint("Пересланные и текстовые посты не считаются."))}
      ${byViews ? tile(ICON.eye, st.week ? "Ожидаемый охват" : "Обычный охват", st.median_views !== null ? num(st.median_views) : "—", pv ? delta(st.median_views, pv.median_views) + vs : "медиана просмотров", hint(st.week ? "Медиана ожидаемых просмотров за неделю: свежие посты ещё набирают, поэтому итог оценивается по кривой набора просмотров." : "Медиана просмотров на пост: половина постов набирает больше, половина — меньше."))
        : tile(ICON.heart, "Обычный отклик", num(st.median_reactions), pv ? delta(st.median_reactions, pv.median_reactions) + vs : "медиана реакций", hint("Медиана реакций на пост."))}
      ${tile(ICON.trophy, "Лучший пост", st.best ? mult(st.best.index) : "—", st.best ? `${esc(st.best.channel)}, ${st.best.date.slice(0, 10)}` : "", idxHint)}
      ${tile(ICON.flame, "Хиты", Math.round(st.hits_share * 100) + "%", (pv && pv.hits_share != null ? delta(st.hits_share, pv.hits_share, "pp") + vs : "") + `<span>${num(st.hits)} ${plural(st.hits, "пост", "поста", "постов")} ${xf((st.levels || LV()).hit)} и выше</span>`, hint(((st.levels || {}).own || 0) > 0
        ? `Хит — пост лучше 90% постов этого канала за полгода. Для вашего канала это ${xf(st.levels.hit)} к норме и выше; порог пересчитывается сам, когда канал растёт.`
        : "Хит — пост ×2 к норме и выше. Когда наберётся 20+ постов с цифрами, порог станет вашим: лучше 90% постов канала."))}
    </div>`;
    const tok = encodeURIComponent(window.API_TOKEN);
    const art = (p, i, rankable) => `<div class="art" role="button" tabindex="0" data-artpost="${p.id}" data-tip="${esc(p.channel)} · ${esc(p.date)}${p.views !== null ? " · " + num(p.views) + " просм." : ""} · ${num(p.reactions)} ${plural(p.reactions, "реакция", "реакции", "реакций")}${p.text ? "\n" + esc(p.text.slice(0, 120)) : ""}">
        ${p.preview ? `<img src="/media/${encodeURIComponent(p.preview)}?t=${tok}" alt="" loading="lazy">` : ""}
        ${badges({ kind: p.kind, n: p.n_media, rank: rankable && p.index != null ? i + 1 : 0, scope: "за период", index: p.index, est: p.est })}
        <span class="ab ${p.index >= LV().hit ? "hit" : ""} ${p.est ? "est" : ""}" ${p.est ? `data-tip="Оценка: пост ещё набирает просмотры"` : ""}>${p.est ? "≈" : ""}${mult(p.index)}</span>
        <span class="am"><b>${esc(p.channel)}</b><span>${esc(p.date.slice(0, 10))}${p.views !== null ? " · " + num(p.views) : ""}</span></span>
        <button class="ax" data-exclude="${p.id}" data-tip="Не арт (анонс, новость) — не учитывать в статистике" aria-label="Не учитывать в статистике">${ICON.eyeoff}</button></div>`;
    h += `<div class="split">
      <section class="card"><div class="head"><h2>Лучшие работы ${idxHint}</h2><span class="muted small">по индексу успеха</span></div>
        ${st.top.length ? `<div class="artgrid">${st.top.slice(0, 8).map((p, i) => art(p, i, true)).join("")}</div>` : `<p class="muted">Нет постов старше недели за этот период.</p>`}
      </section>
      ${st.week ? weekHtml(st) : `<section class="card"><div class="head"><h2>Когда постить ${hint("Средний индекс постов в каждом окне по 3 часа. Окна, где меньше 2 постов, — серые; где постов мало, цвет приглушён поправкой, чтобы одна удача не выглядела закономерностью.")}</h2><span class="muted small">время ${esc(st.tz)}</span></div>
        <div class="heatwrap">${heatHtml(st.heat, st.coverage)}
          <div class="top3"><h3>Лучшие окна</h3>
            ${st.best_times.length ? st.best_times.map((b, i) => `<div class="slot"><span class="n">${i + 1}</span><span><b>${esc(b.label)}</b><br><span class="muted small">${b.n} ${plural(b.n, "пост", "поста", "постов")}</span></span><span class="v">${mult(b.index)}</span></div>`).join("")
              : `<p class="muted small">Пока мало постов, чтобы уверенно назвать лучшее время.</p>`}</div></div>
      </section>`}</div>`;
    if (st.groups && st.groups.length) h += groupsHtml(st);
    const ratingPill = (r) => r === "nsfw" ? `<span class="pill nsfw">18+</span>` : r === "sfw" ? `<span class="pill sfw">SFW</span>` : "";
    h += `<section class="card"><div class="head"><h2>Рост каналов ${hint(`Медиана ${metricWord} на пост по месяцам. Посты моложе недели не учитываются — они ещё набирают.`)}</h2></div>
      <div class="chart" id="growth-chart"></div>
      <div class="legend">${st.channels.map((c, i) => `<span><span class="sw" style="background:${["var(--s1)", "var(--s2)", "var(--s3)"][i % 3]}"></span>${esc(c.title)} ${ratingPill(c.rating)}</span>`).join("")}</div>
      <div class="kvlist">${st.channels.map((c) => `<div><span>${esc(c.title)}</span><span class="muted">${num(c.posts)} ${plural(c.posts, "пост", "поста", "постов")}, ${c.by_views ? `обычно ${num(c.median_views || 0)} просм., реакций ${c.react_rate !== null ? (c.react_rate * 100).toFixed(1).replace(".", ",") + "% от просмотров" : "—"}` : `обычно ${num(c.median_reactions)} ${plural(c.median_reactions, "реакция", "реакции", "реакций")}, без просмотров`}</span></div>`).join("")}</div>
    </section>`;
    h += insightsHtml(st, S.ins);
    $main.innerHTML = h;
  }
  // неделя: разбивка по дням вместо тепловой карты (за 7 дней в каждой клетке 0–1 пост)
  function weekHtml(st) {
    const tok = encodeURIComponent(window.API_TOKEN);
    const lvl = (v) => { const L = LV(); return v == null ? "" : v >= L.super ? "l4" : v >= L.hit ? "l3" : v >= L.good ? "l2" : v < 0.77 ? "l0" : "l1"; };
    return `<section class="card"><div class="head"><h2>Неделя по дням ${hint("Множители свежих постов — оценка итога по кривой набора просмотров; через 7 дней они становятся окончательными.")}</h2><span class="muted small">лучшее окно недели — по полугоду истории</span></div>
      <div class="wdays">${st.days.map((d) => `<div class="wday ${d.n ? "" : "empty"}"><span class="wd">${d.dow}<b>${d.date.slice(8)}.${d.date.slice(5, 7)}</b></span>
        <div class="wthumbs">${d.top.map((p) => p.preview ? `<img src="/media/${encodeURIComponent(p.preview)}?t=${tok}" alt="" loading="lazy" data-tip="${esc(p.channel)}: ≈${mult(p.index)}">` : "").join("")}</div>
        <span class="wn">${d.n ? `${d.n} ${plural(d.n, "пост", "поста", "постов")}` : "—"}</span>${d.best != null ? `<span class="mk ${lvl(d.best)} est">≈${mult(d.best)}</span>` : ""}</div>`).join("")}</div>
      ${st.best_times.length ? `<div class="top3" style="flex:none;flex-direction:row;flex-wrap:wrap">${st.best_times.map((b, i) => `<div class="slot" style="flex:1 1 180px"><span class="n">${i + 1}</span><span><b>${esc(b.label)}</b></span><span class="v">${mult(b.index)}</span></div>`).join("")}</div>` : ""}</section>`;
  }
  // Выводы по содержанию (теги от теггера), кривая набора просмотров, точность прогноза
  function dbar(v) {
    const LIM = Math.log(2), w = Math.min(Math.abs(Math.log(v)) / LIM, 1) * 50;
    return `<div class="dbar" aria-hidden="true"><span class="mid"></span><span class="fill ${v >= 1 ? "up" : "down"}" style="${v >= 1 ? "left:50%" : "right:50%"};width:${w.toFixed(1)}%"></span></div>`;
  }
  function insightsHtml(st, ins) {
    ins = ins || {};
    const tg = ins.tagging || {}, job = S.info.ins_job, run = job && job.state === "running";
    const pl = (n) => `${num(n)} ${plural(n, "пост", "поста", "постов")}`;
    const tagRow = (t) => `<div class="grow-row"><span class="gname">${esc(t.label)}<br><span class="muted small">${pl(t.n)}${t.days ? `, последний ${num(t.days)} ${plural(t.days, "день", "дня", "дней")} назад` : ""}</span></span>${dbar(t.mult)}<b class="gval">${mult(t.mult)}</b></div>`;
    let h = `<section class="card"><div class="head"><h2>Что на картинке заходит лучше ${hint("По тегам, которые теггер нашёл на превью постов. ×1,4 — посты с этим тегом в среднем на 40% лучше вашего обычного поста при прочих равных: то же время, тот же тип поста. Редкие теги подтянуты к ×1.")}</h2></div>`;
    if (run) h += `<div id="insprog"><div class="progress"><div data-bar style="width:${Math.round(job.progress * 100)}%"></div></div><p class="muted small" data-msg>${esc(job.message || "")}</p></div>`;
    else if (job && job.state === "error") h += `<p class="err">${esc(job.error || "")}</p>`;
    if (!run && tg.todo) {
      h += tg.tagger_ready
        ? `<div class="callout"><span>${tg.tagged ? `Новых постов без тегов: <b>${num(tg.todo)}</b>.` : `Разметьте ${pl(tg.todo)} теггером — станет видно, какое содержание заходит, и прогноз начнёт его учитывать.`}
            ${hint("Теггер смотрит превью и находит вид, сюжет, одежду, фон. На видеокарте — пара минут на тысячу постов.")}</span><button class="btn primary" data-ins-tag>Разметить теггером</button></div>`
        : `<p class="muted">Чтобы разобрать содержание работ, <a href="#" data-view-link="platforms" data-tab="tags">установите теггер</a>.</p>`;
    }
    if (!tg.with_preview && !tg.tagged) h += `<p class="muted small">У импортированных постов нет превью. Переимпортируйте канал из выгрузки Telegram Desktop с фото или через API.</p>`;
    if (ins.too_few) h += `<p class="muted">Для выводов нужно хотя бы 30 постов старше недели (сейчас ${num(ins.n)}).</p>`;
    if (ins.tags_up && ins.tags_up.length) {
      h += `<div class="groups"><div class="grp"><h3>Поднимает охват</h3>${ins.tags_up.slice(0, 10).map(tagRow).join("")}</div>
        <div class="grp"><h3>Заходит слабее</h3>${ins.tags_down.length ? ins.tags_down.map(tagRow).join("") : `<p class="muted small">Явно проседающих тем нет.</p>`}</div>
        ${ins.forgotten && ins.forgotten.length ? `<div class="grp"><h3>Заходило, но давно не было ${hint("Темы, которые давали больше обычного, а вы их не публиковали месяц и дольше. Кандидаты на следующий арт.")}</h3>${ins.forgotten.map(tagRow).join("")}</div>` : ""}</div>`;
    } else if (st.hashtags.length) {
      h += `<p class="muted small">Пока по хэштегам из текста постов:</p><div class="kvlist">${st.hashtags.map((t) => `<div><span class="tag">#${esc(t.tag)}</span><b>${mult(t.index)} <span class="muted small" style="font-weight:400">${pl(t.n)}</span></b></div>`).join("")}</div>`;
    }
    h += `</section>`;
    if (ins.curve || ins.quality) {
      const c = ins.curve, q = ins.quality, acc = ins.accuracy;
      const hl = (x) => x < 24 ? x + " ч" : (x / 24) + " д";
      h += `<div class="split">`;
      if (c) h += `<section class="card"><div class="head"><h2>Как пост набирает ${st.metric === "reactions" ? "реакции" : "просмотры"} ${hint((c.own ? "По вашим замерам. " : "Светлые столбики — примерная кривая Telegram: своих замеров пока мало. Они копятся сами, пока работает фоновая отправка. ") + "По этой кривой карточка поста в «Истории» оценивает итог уже через пару часов.")}</h2><span class="muted small">доля от итога за неделю</span></div>
        <div class="curve">${c.points.map((p) => `<div class="cv"><div class="cvb"><span style="height:${Math.round(p.frac * 100)}%" class="${p.own ? "" : "est"}"></span></div><b>${Math.round(p.frac * 100)}%</b><span class="muted small">${hl(p.h)}</span></div>`).join("")}</div></section>`;
      if (q) h += `<section class="card"><div class="head"><h2>Прогноз популярности ${hint("Проверка честная: модель угадывает посты, которых не видела при обучении. Чем больше постов и тегов, тем точнее. Сам прогноз — в «Новом посте».")}</h2><span class="muted small">учится на ${pl(q.n)}</span></div>
        <p><span class="badge ${q.level === "good" ? "ok" : q.level === "mid" ? "warn" : "off"}" style="padding:2px 8px;border-radius:999px;font-size:12px;font-weight:600">${q.level === "good" ? "Работает" : q.level === "mid" ? "Грубый ориентир" : "Пока слабый"}</span> <span class="muted">${esc(q.text)}</span></p>
        <div class="kvlist"><div><span>Угадывает «лучше или хуже обычного»</span><b>${Math.round(q.direction * 100)}% постов</b></div>
          <div><span>Обычная ошибка в цифре</span><b>в ${q.typical_err.toFixed(1).replace(".", ",")} раза</b></div>
          <div><span>Постов с тегами картинки</span><b>${num(q.n_tagged)} из ${num(q.n)}</b></div>
          ${acc && acc.n ? `<div><span>Прогнозы при публикации попали в диапазон</span><b>${acc.inside} из ${acc.n}</b></div>` : ""}</div>
        ${acc && acc.items && acc.items.length ? `<h3>Прогноз и факт</h3><div class="kvlist">${acc.items.slice(0, 6).map((i) => `<div><span class="clip">${esc(i.title || "без названия")} <span class="muted small">${esc(i.channel || "")}</span></span><span>${num(i.expected)} → <b>${num(i.actual)}</b>${i.mature ? "" : ` <span class="muted small">набирает</span>`}</span></div>`).join("")}</div>` : ""}</section>`;
      h += `</div>`;
    }
    if (ins.error) h += `<p class="err small">${esc(ins.error)}</p>`;
    return h;
  }
  // «Что заходит лучше»: полосы вправо — лучше вашего среднего поста, влево — хуже
  function groupsHtml(st) {
    const LIM = Math.log(2);
    const bar = (v) => {
      const w = Math.min(Math.abs(Math.log(v)) / LIM, 1) * 50;
      return `<div class="dbar" aria-hidden="true"><span class="mid"></span><span class="fill ${v >= 1 ? "up" : "down"}" style="${v >= 1 ? "left:50%" : "right:50%"};width:${w.toFixed(1)}%"></span></div>`;
    };
    const unit = st.metric === "reactions" ? "реакц." : "просм.";
    return `<section class="card"><div class="head"><h2>Что заходит лучше ${hint("×1 — ваш средний пост. Группы, где мало постов, подтянуты к ×1. Если пост пересылали в другой канал или группу, Telegram засчитывает оригиналу и просмотры копий — у пересылаемых постов (например, SFW-работ из 18+ канала) просмотры завышены.")}</h2></div>
      <div class="groups">${st.groups.map((g) => `<div class="grp"><h3>${esc(g.title)}</h3>${g.rows.map((r) => `<div class="grow-row">
          <span class="gname">${esc(r.name)}<br><span class="muted small">${num(r.n)} ${plural(r.n, "пост", "поста", "постов")}, обычно ${r.views !== null && st.metric !== "reactions" ? num(r.views) + " " + unit : num(r.reactions) + " " + plural(r.reactions, "реакция", "реакции", "реакций")}</span></span>
          ${bar(r.index)}<b class="gval">${mult(r.index)}</b></div>`).join("")}</div>`).join("")}</div>
      </section>`;
  }
  const RAMP = ["var(--h0)", "var(--h1)", "var(--h2)", "var(--h3)", "var(--h4)", "var(--h5)"];
  function heatHtml(heat, cov) {
    const vals = heat.flatMap((r) => r.cells.map((c) => c.v)).filter((v) => v !== null);
    // шкала не уже ×0.7…×1.4, чтобы разница в 5% не выглядела как «тёмное против светлого»
    const lo = Math.min(...vals, 1 / 1.4), hi = Math.max(...vals, 1.4);
    const color = (v) => RAMP[Math.max(0, Math.min(5, Math.floor((Math.log(v) - Math.log(lo)) / ((Math.log(hi) - Math.log(lo)) || 1) * 5.999)))];
    const hours = ["00", "03", "06", "09", "12", "15", "18", "21"];
    return `<div style="display:flex;flex-direction:column;gap:10px"><div class="heat" role="img" aria-label="Тепловая карта: день недели и время"><span></span>${hours.map((x) => `<span class="hh">${x}</span>`).join("")}
      ${heat.map((r) => `<span class="hd">${r.day}</span>${r.cells.map((c, i) => c.v === null
        ? `<span class="hc empty ${c.n ? "one" : ""}" data-tip="${r.day} ${hours[i]}:00 — ${c.n ? c.n + " пост, мало для вывода" : "не постили"}"></span>`
        : `<span class="hc" style="background:${color(c.v)}" data-tip="${r.day}, ${hours[i]}:00–${String((i * 3 + 3) % 24).padStart(2, "0")}:00 — ${mult(c.v)} (без поправки ${mult(c.raw)}), ${c.n} ${plural(c.n, "пост", "поста", "постов")}"></span>`).join("")}`).join("")}</div>
      <div class="hramp"><span>хуже</span>${RAMP.map((c) => `<i style="background:${c}"></i>`).join("")}<span>лучше</span></div>
      ${cov ? `<div class="hcov"><b>За всю историю изучено ${cov.solid} из ${cov.cells} окон</b> (3 и больше постов). Мало данных — ${cov.weak}, без данных — ${cov.none}.
        <span class="muted">Карта выше — только выбранный период, поэтому серых окон на ней может быть больше. Серое с точкой — один пост, пустое — ни одного.</span></div>` : ""}</div>`;
  }

  const SOON = {};
  function renderSoon() {
    const [t, d] = SOON[S.view] || ["Скоро", ""];
    $main.innerHTML = `<h1>${t}</h1><section class="card soon"><p>${d}</p><p class="muted small">Раздел в работе. Сейчас готовы <a href="#" data-view-link="trends">тренды e6ai и e621</a>.</p></section>`;
  }

  // ---------------- события ----------------
  document.getElementById("nav").addEventListener("click", async (e) => {
    const b = e.target.closest("button[data-view]"); if (!b) return;
    S.pstatOpen = null;
    S.view = b.dataset.view; store.set("view", S.view); S.flash = ""; render();
    if (S.view === "post" || S.view === "history" || S.view === "settings") { await window.Pub.load(S.view); render(); }
    if (S.view === "settings") { await loadTg(false); render(); }
    if (S.view === "stats") { if (!S.tgCh.length) await loadChannels(); await loadStats(); render(); }
    if (S.view === "trends" && !S.data && !S.loading) { await loadOverview(); render(); }   // данные трендов — при первом заходе
  });
  $main.addEventListener("click", async (e) => {
    if (window.Pub && await window.Pub.onClick(e)) return;
    const wkb = e.target.closest("[data-wk]");
    if (wkb) {
      wkb.disabled = true;
      try { if (wkb.dataset.wk === "start") S.worker = await api("worker_start"); else { S.worker = await api("worker_stop"); setTimeout(async () => { await loadWorker(); render(); }, 16000); } }
      catch (err) { alert(err.message); }
      paintWorker(); render(); return;
    }
    const pfb = e.target.closest('[data-action="perf-refresh"],[data-action="perf-report"],[data-action="perf-kill"],[data-action="perf-tagger"]');
    if (pfb) {
      const a = pfb.dataset.action;
      try {
        if (a === "perf-report") await api("perf_report");
        if (a === "perf-kill") { const r = await api("perf_kill_browsers"); toast(r.killed ? `Закрыто процессов браузера: ${r.killed}` : "Лишних процессов браузера нет", "ok"); }
        if (a === "perf-tagger") await api("tagger_unload");
      } catch (err) { /* ошибка уже показана */ }
      S.perf = null; loadPerf(); render(); return;
    }
    if (e.target.closest('[data-action="mk-shortcut"]')) { try { await api("create_shortcuts"); } catch (err) { /* ошибка уже показана */ } return; }

    const mon = e.target.closest("[data-modon]");
    if (mon) { S.info.modules = await api("modules_set", { key: mon.dataset.modon, on: true }); S.plan = null; await window.Pub.load("settings"); render(); return; }
    const ps = e.target.closest("[data-psec]");
    if (ps) { S.platSec = ps.dataset.psec; store.set("platSec", S.platSec); render(); window.scrollTo(0, 0); return; }
    const link = e.target.closest("[data-view-link]");
    if (link) { e.preventDefault(); S.view = link.dataset.viewLink === "platforms" ? "settings" : link.dataset.viewLink; store.set("view", S.view);
      if (link.dataset.tab) { S.platSec = link.dataset.tab; store.set("platSec", S.platSec); }
      else if (link.dataset.viewLink === "settings" && S.view === "settings" && !link.dataset.keep) { /* раздел остаётся прежним */ }
      if (S.view === "post" || S.view === "history" || S.view === "settings") await window.Pub.load(S.view);
      render();
      if (S.view === "settings") { await loadTg(false); render(); }
      if (S.view === "stats") { await loadChannels(); await loadStats(); render(); }
      if (S.view === "trends" && !S.data && !S.loading) { await loadOverview(); render(); }
      return; }
    const ex = e.target.closest("[data-exclude],[data-unexclude-all]");
    if (ex) {
      e.preventDefault();
      if (ex.dataset.exclude) await api("stats_exclude", { post_id: +ex.dataset.exclude, excluded: true });
      else await api("stats_exclude", { post_id: null, excluded: false });
      await loadStats(); render(); return;
    }
    const ap = e.target.closest("[data-artpost]");
    if (ap) { e.preventDefault(); await window.Pub.openDrawer("post:" + ap.dataset.artpost); return; }
    const ol = e.target.closest("[data-open]");
    if (ol) { e.preventDefault(); api("open_url", { url: ol.dataset.open }).catch((err) => alert(err.message)); return; }
    if (e.target.closest("[data-ins-tag]")) {
      try { await api("insights_tag_history"); } catch (err) { alert(err.message); }
      S.insJobWas = true; await loadInfo(); render(); return;
    }
    const nb = e.target.closest("[data-net]");
    if (nb) {
      syncNet(); S.netOpen = true;
      if (nb.dataset.net === "transport") S.netDraft.transport = nb.dataset.val;
      else S.netDraft.proxy = Object.assign({}, S.netDraft.proxy, { type: nb.dataset.val });
      render(); return;
    }
    if (e.target.closest('[data-action="net-links"]')) {
      const ta = document.getElementById("nlinks"); S.netLinks = ta ? ta.value : "";
      if (!S.netLinks.trim()) { toast("Вставьте ссылку на прокси", "error"); return; }
      S.netOpen = true; S.tgBusy = true; S.tgErr = ""; S.netRes = null; render();
      try {
        const r = await api("tg_proxy_links", { text: S.netLinks });
        S.netRes = r.results; S.tg = r.status; S.netDraft = null;
        if (!r.added) toast("Ссылок на прокси не нашлось — нужны вида tg://proxy?… или t.me/proxy?…", "error");
        else { S.netLinks = ""; toast(`Добавлено прокси: ${r.added}`, "ok"); }
      } catch (err) { S.tgErr = err.message; }
      S.tgBusy = false; render(); return;
    }
    const pf = e.target.closest("[data-proxyforget]");
    if (pf) {
      const [host, port] = pf.dataset.proxyforget.split("|");
      try { S.tg = await api("tg_forget_proxy", { host, port }); S.netDraft = null; } catch (err) { S.tgErr = err.message; }
      render(); return;
    }
    const nact = e.target.closest('[data-action="net-check"],[data-action="net-save"]');
    if (nact) {
      syncNet(); S.netOpen = true; S.tgBusy = true; S.tgErr = ""; S.netRes = null; render();
      const draft = JSON.parse(JSON.stringify(S.netDraft));
      if (!draft.proxy || !draft.proxy.type) draft.proxy = {};
      try {
        if (nact.dataset.action === "net-save") { S.tg = await api("tg_set_net", { net: draft }); }
        else { S.netRes = await api("tg_check_net", { net: draft }); S.tg = await api("tg_status"); }
        S.netDraft = null;
      } catch (err) { S.tgErr = err.message; }
      S.tgBusy = false; render(); return;
    }
    const chr = e.target.closest("[data-chrating]");
    if (chr) { const c = S.tgCh.find((x) => x.chat_id === chr.dataset.chrating);
      S.tgCh = await api("tg_select", { chat_id: c.chat_id, selected: !!c.selected, rating: chr.dataset.val }); S.stats = null; render(); return; }
    const setb = e.target.closest("[data-set]");
    if (setb) {
      const k = setb.dataset.set, v = setb.dataset.val;
      if (S[k] === v) return;
      S[k] = v;
      if (k === "src") { store.set("src", v); S.sel = null; S.manual = false; }
      if (k === "rating" || k === "cat") store.set(k, v);
      if (k === "sp" || k === "sc") { store.set(k, v); await loadStats(); render(); return; }
      await loadOverview(); render(); return;
    }
    const hb = e.target.closest("[data-hide]");
    if (hb) {
      await api("hide_tag", { tag: hb.dataset.hide, hide: true });
      await loadInfo(); if (S.sel === hb.dataset.hide) S.sel = null;
      await loadOverview(); render(); return;
    }
    const p = e.target.closest("[data-pick]");
    if (p) {
      if (p.dataset.src && p.dataset.src !== S.src) { S.src = p.dataset.src; store.set("src", S.src); S.sel = p.dataset.pick; await loadOverview(); render(); }
      await pick(p.dataset.pick); return;
    }
    const a = e.target.closest("[data-action]"); if (!a) return;
    const act = a.dataset.action;
    if (act === "tg-pickjson") {
      const f = await api("pick_files", { title: "Выгрузка Telegram (result.json)", kind: "tgjson" });
      if (f && f[0]) { S.tgJson = f[0]; document.getElementById("tgjson").value = f[0]; }
      return;
    }
    if (act === "tg-import") {
      const path = document.getElementById("tgjson").value.trim();
      if (!path) { alert("Укажите файл result.json или папку выгрузки"); return; }
      S.tgJson = path; S.tgErr = "";
      try { await api("tg_import_export", { path }); await loadInfo(); if (!poll) poll = setInterval(tick, 1000); }
      catch (err) { alert(err.message); }
      render(); return;
    }
    if (act.startsWith("upd-")) {
      try {
        if (act === "upd-check") { S.updBusy = true; render(); S.info.update = await api("update_check"); S.updBusy = false; }
        else if (act === "upd-open") await api("update_open");
        else if (act === "upd-get") { await api("update_prepare"); await loadInfo(); }
        else if (act === "upd-apply") {
          if (!confirm("Установить обновление? Crosspost закроется и сам откроется заново. Несохранённый черновик поста может пропасть.")) return;
          S.updApplying = true; render(); await api("update_apply"); return;
        }
        else if (act === "upd-skip") S.info.update = await api("update_skip", { version: (S.info.update.latest || {}).version });
        else if (act === "upd-auto") S.info.update = await api("update_set", { check: a.checked });
      } catch (err) { S.updBusy = false; S.updApplying = false; toast(err.message, "error"); }
      updBadge(); render(); return;
    }
    const tgAct = { "tg-keys": 1, "tg-phone": 1, "tg-code": 1, "tg-password": 1, "tg-resend": 1, "tg-rekeys": 1, "tg-logout": 1, "tg-refresh-ch": 1, "tg-sync": 1 };
    if (tgAct[act]) {
      // значения полей читаем ДО перерисовки — она пересоздаёт поля ввода
      const vals = {};
      ["apiid", "apihash", "tgphone", "tgcode", "tgpw"].forEach((id) => { const el = document.getElementById(id); if (el) vals[id] = el.value.trim(); });
      const val = (id) => vals[id] || "";
      S.tgBusy = true; S.tgErr = ""; S.apiOpen = true; render();
      try {
        if (act === "tg-keys") S.tg = await api("tg_set_keys", { api_id: val("apiid"), api_hash: val("apihash") });
        if (act === "tg-phone") S.tg = await api("tg_send_code", { phone: val("tgphone") });
        if (act === "tg-code") S.tg = await api("tg_sign_in", { code: val("tgcode") });
        if (act === "tg-password") S.tg = await api("tg_password", { password: val("tgpw") });
        if (act === "tg-resend") S.tg = Object.assign({}, S.tg, { code_sent: false });
        if (act === "tg-rekeys") S.tg = Object.assign({}, S.tg, { configured: false });
        if (act === "tg-logout") { if (!confirm("Выйти из Telegram в приложении? Импортированные посты останутся.")) { S.tgBusy = false; render(); return; } S.tg = await api("tg_log_out"); }
        if (act === "tg-sync") { await api("tg_sync"); await loadInfo(); if (!poll) poll = setInterval(tick, 1000); }
        if (S.tg && S.tg.authorized) S.tgCh = await api("tg_channels", { refresh: act === "tg-refresh-ch" || act === "tg-password" || act === "tg-code" });
      } catch (err) { S.tgErr = err.message; }
      S.tgBusy = false; render(); return;
    }
    try {
      if (act === "update") { await api("trends_update", { source: S.src }); await loadInfo(); if (!poll) poll = setInterval(tick, 1000); render(); }
      if (act === "manual") { S.manual = !S.manual; render(); }
      if (act === "trends-settings") { S.trSet = !S.trSet; S.flash = ""; render(); }
      if (act === "storage-free") {
        if (!confirm("Удалить копии оригиналов всех опубликованных постов? Миниатюры, JPEG-превью и статистика останутся.")) return;
        const r = await api("storage_free"); S.storage = r.usage; S.flash2 = `Освобождено ${fmtBytes(r.bytes)}`; render();
      }
      if (act === "pickfiles") {
        const files = await api("pick_files");
        const posts = files.find((f) => /posts-/.test(f)), tags = files.find((f) => /tags-/.test(f));
        if (posts) document.getElementById("mp").value = posts;
        if (tags) document.getElementById("mt").value = tags;
      }
      if (act === "import") {
        const mp = document.getElementById("mp").value.trim(), mt = document.getElementById("mt").value.trim();
        if (!mp || !mt) { alert("Укажите оба файла"); return; }
        await api("trends_import", { source: S.src, posts_path: mp, tags_path: mt });
        S.manual = false; await loadInfo(); if (!poll) poll = setInterval(tick, 1000); render();
      }
      if (act === "save-settings") {
        const v = (id) => { const el = document.getElementById(id); return el ? el.value : null; };
        const ch = {};
        if (v("nick") !== null) ch.nickname = v("nick").trim();
        if (v("style") !== null) ch.style_tags = v("style").split(/[\s,]+/).filter(Boolean);
        if (v("hidden") !== null) ch.hidden_tags = v("hidden").split(/[\s,]+/).filter(Boolean);
        if (v("r6") !== null) ch.trends = { e6ai: { refresh_days: +v("r6") || 1 }, e621: { refresh_days: +v("r621") || 7 } };
        await api("settings_save", { changes: ch });
        await loadInfo(); S.flash = "Сохранено"; render();
      }
    } catch (err) { alert(err.message); }
  });
  $main.addEventListener("toggle", (e) => {
    if (e.target.id === "apibox") S.apiOpen = e.target.open;
    if (e.target.id === "netbox") S.netOpen = e.target.open;
    if (e.target.id === "pvbox") S.pvOpen = e.target.open;
  }, true);
  $main.addEventListener("change", async (e) => {
    if (e.target.id === "statch") { S.sc = e.target.value; store.set("sc", S.sc); await loadStats(); render(); return; }
    if (e.target.id === "themesel") { store.set("theme", e.target.value); if (e.target.value === "light") document.documentElement.dataset.theme = "light"; else delete document.documentElement.dataset.theme; render(); return; }
    if (e.target.dataset.feat) {   // функция целиком: все её модули разом
      const f = FEATURES.find((x) => x[0] === e.target.dataset.feat), on = e.target.checked;
      if (!on && f[0] === "crosspost" && (S.sched || []).some((r) => r.state === "pending") &&
          !confirm("Есть запланированные посты — фоновая отправка всё равно их опубликует. Скрыть «Новый пост» и «Историю»?")) { e.target.checked = true; return; }
      try { S.info.modules = await api("modules_set_many", { changes: Object.fromEntries(f[3].map((k) => [k, on])) }); }
      catch (err) { e.target.checked = !on; return; }
      applyModules(); S.plan = null; await window.Pub.load("settings"); render(); return;
    }
    if (e.target.dataset.mod) {   // переключатель модуля
      const k = e.target.dataset.mod;
      try { S.info.modules = await api("modules_set", { key: k, on: e.target.checked }); } catch (err) { alert(err.message); e.target.checked = !e.target.checked; return; }
      applyModules();
      S.plan = null; await window.Pub.load("settings"); render(); return;
    }
    if (window.Pub && await window.Pub.onChange(e)) return;
    if (e.target.dataset.chsel) {
      const c = S.tgCh.find((x) => x.chat_id === e.target.dataset.chsel);
      S.tgCh = await api("tg_select", { chat_id: c.chat_id, selected: e.target.checked, rating: c.rating || "all" }); S.stats = null; render(); return;
    }
    if (e.target.dataset.action === "mine") { S.mine = e.target.checked; store.set("mine", S.mine); await loadOverview(); render(); }
  });

  // Enter/пробел на плитках-ссылках (role="button")
  $main.addEventListener("keydown", (e) => {
    if ((e.key === "Enter" || e.key === " ") && e.target.matches && e.target.matches('[role="button"]')) { e.preventDefault(); e.target.click(); }
  });

  (async function init() {
    document.getElementById("updbtn").addEventListener("click", () => { S.view = "settings"; S.platSec = "app"; store.set("view", S.view); loadInfo().then(() => render()); });
    setTimeout(() => loadInfo().catch(() => {}), 8000);   // к этому моменту фоновая проверка версии уже ответила
    loadWorker(); setInterval(loadWorker, 20000);
    pollActs(); loadNews(false); setInterval(() => loadNews(false), 60000);
    try { await loadInfo(); } catch (e) { $main.innerHTML = `<p class="err">Не удалось связаться с приложением: ${esc(e.message)}</p>`; return; }
    if (applyModules()) render();
    const ur = S.info.update && S.info.update.result;
    if (ur) { toast(ur.ok ? `Crosspost обновлён до версии ${ur.version}` : `Обновление не удалось, осталась прежняя версия: ${ur.error}`, ur.ok ? "ok" : "error"); api("update_ack").catch(() => {}); }
    if (S.view === "trends") await loadOverview();
    if (S.view === "platforms") { S.view = "settings"; store.set("view", S.view); }   // раздел «Площадки» теперь внутри «Настроек»
    if (S.view === "settings") await loadTg(false);
    if (S.view === "stats") { await loadChannels(); await loadStats(); }
    if (S.view === "post" || S.view === "history" || S.view === "settings") await window.Pub.load(S.view);
    render();
  let resizeT = null;
  window.addEventListener("resize", () => {
    clearTimeout(resizeT);
    resizeT = setTimeout(() => { if (S.view === "stats") render(); }, 200);
  });
  })();
})();
