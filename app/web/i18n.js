/* Перевод интерфейса. Тексты в коде — русские; в английском режиме они переводятся по словарю (lang_en.js) перед показом:
   - целиком (точное совпадение) или по шаблону с {имя} вместо подставляемых частей;
   - иначе по кускам: известные фразы заменяются на английские, числа и имена остаются как есть.
   Язык хранится в ui.json (UI_STATE.lang); выбор — в окне первого запуска и в «Настройки» → «Общие», с перезагрузкой окна. */
(function () {
  "use strict";
  const UI = window.UI_STATE && typeof window.UI_STATE === "object" ? window.UI_STATE : {};
  let lang = UI.lang === "en" ? "en" : "ru";
  const CY = /[А-Яа-яЁё]/;
  const api = { lang, loc: lang === "en" ? "en-GB" : "ru-RU" };
  window.LOC = api.loc;
  window.DEC = lang === "en" ? "." : ",";   // десятичный разделитель в числах, которые форматируем руками
  window.I18N = api;
  document.documentElement.lang = lang;
  api.t = (s) => s;
  api.dom = () => {};
  api.html = (s) => s;

  // выбор языка: кнопки с data-lang — в настройках и в окне первого запуска; сохраняем в ui.json и перезагружаем окно
  function chooseLang(code) {
    if (code !== "ru" && code !== "en") return;
    if (code === lang && UI.lang === code) return;
    fetch("/api/ui_set", { method: "POST", headers: { "Content-Type": "application/json", "X-Token": window.API_TOKEN },
      body: JSON.stringify({ changes: { lang: code } }) }).catch(() => {}).finally(() => location.reload());
  }
  document.addEventListener("click", (e) => {
    const b = e.target.closest && e.target.closest("[data-lang]");
    if (b) { e.preventDefault(); chooseLang(b.dataset.lang); }
  });
  // первый запуск: язык ещё не выбран — спрашиваем один раз (двуязычно, пока язык не известен)
  function firstRun() {
    if (UI.lang === "ru" || UI.lang === "en" || document.getElementById("langfirst")) return;
    const guess = /^ru\b|^uk|^be/i.test(navigator.language || "") ? "ru" : "en";
    const d = document.createElement("div");
    d.className = "langfirst"; d.id = "langfirst"; d.setAttribute("role", "dialog"); d.setAttribute("aria-modal", "true"); d.setAttribute("aria-labelledby", "lfh");
    d.innerHTML = '<div class="lfbox"><h2 id="lfh">Выберите язык · Choose your language</h2>' +
      '<p>Язык можно поменять потом в «Настройки» → «Общие».<br>You can change it later in Settings → General.</p>' +
      '<div class="lfbtns"><button class="btn' + (guess === "ru" ? " primary" : "") + '" data-lang="ru">Русский</button>' +
      '<button class="btn' + (guess === "en" ? " primary" : "") + '" data-lang="en">English</button></div></div>';
    document.body.appendChild(d);
    const f = d.querySelector(".primary") || d.querySelector("button"); if (f) f.focus();
    d.addEventListener("keydown", (e) => { if (e.key === "Tab") { const bs = d.querySelectorAll("button"), i = [...bs].indexOf(document.activeElement); e.preventDefault(); bs[(i + (e.shiftKey ? bs.length - 1 : 1)) % bs.length].focus(); } });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", firstRun); else firstRun();

  const D0 = window.EN_DICT;
  if (lang !== "en" || !D0) return;

  // ----- словарь -----
  const UP = /[А-ЯЁ]/, LOW = /[а-яё]/;
  const D = Object.create(null);
  for (const k of Object.keys(D0)) D[k] = D0[k];
  // регистр первой буквы: «Пост» ↔ «пост»
  for (const k of Object.keys(D0)) {
    const v = D0[k], c = k[0];
    let k2 = null, v2 = null;
    if (UP.test(c)) { k2 = c.toLowerCase() + k.slice(1); v2 = /^[A-Z][a-z]/.test(v) ? v[0].toLowerCase() + v.slice(1) : v; }
    else if (LOW.test(c)) { k2 = c.toUpperCase() + k.slice(1); v2 = v[0].toUpperCase() + v.slice(1); }
    if (k2 && !(k2 in D)) D[k2] = v2;
  }
  const tpls = [], plain = [];
  const escRe = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  for (const k of Object.keys(D)) {
    if (/\{\w+\}/.test(k)) {
      const names = [];
      const re = new RegExp("^" + k.split(/(\{\w+\})/).map((p) => { const m = /^\{(\w+)\}$/.exec(p); if (m) { names.push(m[1]); return "(.+?)"; } return escRe(p); }).join("") + "$", "s");
      tpls.push({ re, names, to: D[k] });
    } else plain.push(k);
  }
  plain.sort((a, b) => b.length - a.length);
  const L = "\\p{L}";
  const alt = plain.map((k) => {
    const pre = CY.test(k[0]) ? `(?<!${L})` : "", post = CY.test(k[k.length - 1]) ? `(?!${L})` : "";
    return pre + escRe(k) + post;
  }).join("|");
  const big = new RegExp(alt, "gu");

  const cache = new Map();
  function tr(s) {
    if (!s || !CY.test(s)) return s;
    const hit = cache.get(s); if (hit !== undefined) return hit;
    let out;
    const core = s.trim();
    if (core in D) {
      const i = s.indexOf(core);
      out = s.slice(0, i) + D[core] + s.slice(i + core.length);
    } else {
      out = null;
      for (const t of tpls) {
        const m = t.re.exec(core);
        if (m) { let r = t.to; t.names.forEach((n, i) => { r = r.split("{" + n + "}").join(tr(m[i + 1])); }); const i = s.indexOf(core); out = s.slice(0, i) + r + s.slice(i + core.length); break; }
      }
      if (out === null) out = s.replace(big, (m) => D[m]);
    }
    if (cache.size > 20000) cache.clear();
    cache.set(s, out);
    return out;
  }
  api.t = tr;

  // ----- DOM -----
  const ATTRS = ["title", "placeholder", "aria-label", "data-tip", "alt", "label", "data-title"];
  function node(n) {
    if (n.nodeType === 3) {
      const p = n.parentNode; if (p && (p.nodeName === "TEXTAREA" || p.nodeName === "SCRIPT" || p.nodeName === "STYLE" || p.nodeName === "PRE")) return;
      const v = n.nodeValue, t = tr(v); if (t !== v) n.nodeValue = t;
    } else if (n.nodeType === 1) {
      const nm = n.nodeName;
      if (nm === "SCRIPT" || nm === "STYLE") return;
      if (n.classList && n.classList.contains("notes")) return;   // описание выпуска с GitHub — как есть
      for (const a of ATTRS) if (n.hasAttribute(a)) { const v = n.getAttribute(a), t = tr(v); if (t !== v) n.setAttribute(a, t); }
      if (nm === "INPUT" && (n.type === "button" || n.type === "submit")) { const t = tr(n.value); if (t !== n.value) n.value = t; }
      if (nm === "TEXTAREA" || nm === "PRE") return;   // внутри — текст пользователя
      for (let c = n.firstChild; c; c = c.nextSibling) node(c);
    } else if (n.nodeType === 11) for (let c = n.firstChild; c; c = c.nextSibling) node(c);
  }
  api.dom = node;
  api.html = (s) => { const t = document.createElement("template"); t.innerHTML = s; node(t.content); return t.innerHTML; };

  function start() {
    node(document.documentElement);
    document.title = tr(document.title);
    new MutationObserver((list) => {
      for (const m of list) {
        if (m.type === "childList") m.addedNodes.forEach(node);
        else if (m.type === "characterData") node(m.target);
        else if (m.type === "attributes") { const v = m.target.getAttribute(m.attributeName), t = tr(v); if (t !== v) m.target.setAttribute(m.attributeName, t); }
      }
    }).observe(document.documentElement, { childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ATTRS });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start); else start();

  // ----- прочее: диалоги, подписи на canvas -----
  for (const f of ["alert", "confirm", "prompt"]) { const o = window[f].bind(window); window[f] = (m, ...r) => o(tr(String(m == null ? "" : m)), ...r); }
  const ctx = window.CanvasRenderingContext2D && CanvasRenderingContext2D.prototype;
  if (ctx) for (const f of ["fillText", "strokeText"]) { const o = ctx[f]; ctx[f] = function (t, ...r) { return o.call(this, tr(String(t)), ...r); }; }
})();
