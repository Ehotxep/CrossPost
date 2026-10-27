/* Графики на SVG без внешних библиотек: мини-графики, прогноз, карта ниш. */
(function () {
  const tipEl = () => document.getElementById("tip");
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const fmtX = (v) => "×" + v.toFixed(2);
  const CAT_COLOR = { species: "var(--s1)", general: "var(--s2)", character: "var(--s3)", franchise: "var(--s3)" };

  function showTip(html, x, y) {
    const t = tipEl();
    t.innerHTML = html;
    t.hidden = false;
    const r = t.getBoundingClientRect();
    let left = x + 14, top = y + 14;
    if (left + r.width > window.innerWidth - 8) left = x - r.width - 14;
    if (top + r.height > window.innerHeight - 8) top = y - r.height - 14;
    t.style.left = left + "px";
    t.style.top = top + "px";
  }
  function hideTip() { tipEl().hidden = true; }

  function sparkline(vals) {
    if (!vals || vals.length < 2) return "";
    const lo = Math.min(...vals), hi = Math.max(...vals), span = hi - lo || 1;
    const d = vals.map((v, i) => (i ? "L" : "M") + (i * 100 / (vals.length - 1)).toFixed(1) + " " + (26 - (v - lo) / span * 24).toFixed(1)).join(" ");
    return `<svg width="88" height="26" viewBox="0 0 100 28" preserveAspectRatio="none" aria-hidden="true">
      <path d="${d}" fill="none" stroke="var(--s1)" stroke-width="2" vector-effect="non-scaling-stroke" stroke-linejoin="round"/></svg>`;
  }

  /* Прогноз: история (сплошная), прогноз (пунктир), диапазон, линия «средний пост сайта» */
  function forecastChart(el, fc) {
    const W = 640, H = 260, L = 48, R = 14, T = 12, B = 32;
    const hist = fc.history, f = fc.forecast, n = hist.length + f.length;
    const all = hist.concat(f, fc.lo, fc.hi, [1]);
    let lo = Math.min(...all), hi = Math.max(...all);
    const pad = (hi - lo) * 0.12 || 0.1; lo = Math.max(0, lo - pad); hi += pad;
    const X = (i) => L + i * (W - L - R) / (n - 1);
    const Y = (v) => T + (hi - v) / (hi - lo) * (H - T - B);
    const now = hist.length - 1;
    const ticks = [0, 1, 2, 3].map((k) => lo + (hi - lo) * k / 3);
    let s = "";
    ticks.forEach((v) => {
      s += `<line x1="${L}" x2="${W - R}" y1="${Y(v)}" y2="${Y(v)}" stroke="var(--grid)"/>`;
      s += `<text x="${L - 8}" y="${Y(v) + 4}" text-anchor="end" font-size="11" fill="var(--ink-3)">${fmtX(v)}</text>`;
    });
    s += `<line x1="${L}" x2="${W - R}" y1="${Y(1)}" y2="${Y(1)}" stroke="var(--axis)" stroke-dasharray="4 4"/>`;
    s += `<text x="${L + 4}" y="${Y(1) - 6}" font-size="11" fill="var(--ink-3)" paint-order="stroke" stroke="var(--card)" stroke-width="4" stroke-linejoin="round">средний пост сайта</text>`;
    const up = f.map((v, k) => [X(now + 1 + k), Y(fc.hi[k])]), dn = f.map((v, k) => [X(now + 1 + k), Y(fc.lo[k])]);
    const band = [[X(now), Y(hist[now])], ...up, ...dn.reverse()].map((p, i) => (i ? "L" : "M") + p[0].toFixed(1) + " " + p[1].toFixed(1)).join(" ") + " Z";
    s += `<path d="${band}" fill="var(--s1)" fill-opacity="0.14"/>`;
    s += `<line x1="${X(now)}" x2="${X(now)}" y1="${T}" y2="${H - B}" stroke="var(--axis)" stroke-dasharray="3 3"/>`;
    const hd = hist.map((v, i) => (i ? "L" : "M") + X(i).toFixed(1) + " " + Y(v).toFixed(1)).join(" ");
    const fd = "M" + X(now).toFixed(1) + " " + Y(hist[now]).toFixed(1) + " " + f.map((v, k) => "L" + X(now + 1 + k).toFixed(1) + " " + Y(v).toFixed(1)).join(" ");
    s += `<path d="${hd}" fill="none" stroke="var(--s1)" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>`;
    s += `<path d="${fd}" fill="none" stroke="var(--s1)" stroke-width="2" stroke-dasharray="5 4" stroke-linecap="round"/>`;
    s += `<circle cx="${X(now)}" cy="${Y(hist[now])}" r="4" fill="var(--s1)" stroke="var(--card)" stroke-width="2"/>`;
    s += `<text x="${X(0)}" y="${H - 10}" font-size="11" fill="var(--ink-3)">${esc(fc.history_weeks[0])}</text>`;
    s += `<text x="${X(now)}" y="${H - 10}" text-anchor="middle" font-size="11" fill="var(--ink-3)">сейчас</text>`;
    s += `<text x="${X(n - 1)}" y="${H - 10}" text-anchor="end" font-size="11" fill="var(--ink-3)">+4 недели</text>`;
    s += `<line class="hx" x1="0" x2="0" y1="${T}" y2="${H - B}" stroke="var(--ink-3)" visibility="hidden"/>`;
    s += `<circle class="hd" r="5" fill="var(--s1)" stroke="var(--card)" stroke-width="2" visibility="hidden"/>`;
    s += `<rect class="hit" x="${L}" y="${T}" width="${W - L - R}" height="${H - T - B}" fill="transparent"/>`;
    el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Отклик тега ${esc(fc.tag)} по неделям и прогноз">${s}</svg>`;
    const svg = el.querySelector("svg"), hx = svg.querySelector(".hx"), hdot = svg.querySelector(".hd");
    svg.querySelector(".hit").addEventListener("mousemove", (e) => {
      const r = svg.getBoundingClientRect();
      const vx = (e.clientX - r.left) / r.width * W;
      const i = Math.max(0, Math.min(n - 1, Math.round((vx - L) / (W - L - R) * (n - 1))));
      const isF = i > now, v = isF ? f[i - now - 1] : hist[i];
      hx.setAttribute("x1", X(i)); hx.setAttribute("x2", X(i)); hx.setAttribute("visibility", "visible");
      hdot.setAttribute("cx", X(i)); hdot.setAttribute("cy", Y(v)); hdot.setAttribute("visibility", "visible");
      const label = isF ? `через ${i - now} нед. — прогноз` : `неделя с ${esc(fc.history_weeks[i])}`;
      const extra = isF ? `<br><span class="small">вероятно ${fmtX(fc.lo[i - now - 1])} – ${fmtX(fc.hi[i - now - 1])}</span>` : "";
      showTip(`${label}<br><b>отклик ${fmtX(v)}</b> к среднему посту${extra}`, e.clientX, e.clientY);
    });
    svg.querySelector(".hit").addEventListener("mouseleave", () => {
      hx.setAttribute("visibility", "hidden"); hdot.setAttribute("visibility", "hidden"); hideTip();
    });
  }

  /* Карта ниш: x — постов в месяц (логарифм), y — отклик */
  function nicheChart(el, niches, onPick) {
    const pts = niches.points;
    if (!pts.length) { el.innerHTML = `<p class="muted">Недостаточно данных для карты ниш.</p>`; return; }
    const W = 640, H = 340, L = 52, R = 16, T = 14, B = 42;
    const xs = pts.map((p) => Math.log10(Math.max(1, p.per_month))), ys = pts.map((p) => p.lift);
    let x0 = Math.min(...xs), x1 = Math.max(...xs); const xp = (x1 - x0) * 0.06 || 0.2; x0 -= xp; x1 += xp;
    let y0 = Math.min(0.8, ...ys), y1 = Math.max(1.3, ...ys); const yp = (y1 - y0) * 0.08; y0 -= yp; y1 += yp;
    const X = (p) => L + (Math.log10(Math.max(1, p)) - x0) / (x1 - x0) * (W - L - R);
    const Y = (v) => T + (y1 - v) / (y1 - y0) * (H - T - B);
    const xm = X(niches.x_median), y1line = Y(1);
    let s = `<rect x="${L}" y="${T}" width="${xm - L}" height="${y1line - T}" fill="var(--ok-bg)" fill-opacity="0.75"/>`;
    s += `<text x="${L + 8}" y="${T + 16}" font-size="12" font-weight="600" paint-order="stroke" stroke="var(--card)" stroke-width="4" stroke-linejoin="round" fill="var(--ok-ink)">ниши</text>`;
    s += `<text x="${W - R - 4}" y="${T + 16}" text-anchor="end" font-size="12" font-weight="600" paint-order="stroke" stroke="var(--card)" stroke-width="4" stroke-linejoin="round" fill="var(--ink-2)">хиты с конкуренцией</text>`;
    s += `<text x="${W - R - 4}" y="${H - B - 8}" text-anchor="end" font-size="12" font-weight="600" paint-order="stroke" stroke="var(--card)" stroke-width="4" stroke-linejoin="round" fill="var(--bad)">перенасыщено</text>`;
    s += `<line x1="${L}" x2="${W - R}" y1="${y1line}" y2="${y1line}" stroke="var(--axis)" stroke-dasharray="3 3"/>`;
    s += `<line x1="${xm}" x2="${xm}" y1="${T}" y2="${H - B}" stroke="var(--axis)" stroke-dasharray="3 3"/>`;
    s += `<line x1="${L}" x2="${W - R}" y1="${H - B}" y2="${H - B}" stroke="var(--axis)"/>`;
    s += `<line x1="${L}" x2="${L}" y1="${T}" y2="${H - B}" stroke="var(--axis)"/>`;
    // подписи осей: несколько круглых значений постов в месяц
    [1, 3, 10, 30, 100, 300, 1000, 3000, 10000, 30000].forEach((v) => {
      const lv = Math.log10(v); if (lv < x0 || lv > x1) return;
      s += `<text x="${X(v)}" y="${H - B + 16}" text-anchor="middle" font-size="11" fill="var(--ink-3)">${v >= 1000 ? v / 1000 + "k" : v}</text>`;
    });
    s += `<text x="${(L + W - R) / 2}" y="${H - 6}" text-anchor="middle" font-size="11" fill="var(--ink-3)">новых постов в месяц →</text>`;
    s += `<text x="${L - 8}" y="${y1line + 4}" text-anchor="end" font-size="11" fill="var(--ink-3)">×1</text>`;
    s += `<text x="14" y="${(T + H - B) / 2}" text-anchor="middle" font-size="11" fill="var(--ink-3)" transform="rotate(-90 14 ${(T + H - B) / 2})">отклик →</text>`;
    // подписи точек без наложений: справа, иначе слева, иначе пропускаем
    const boxes = [];
    const hit = (b) => boxes.some((o) => !(b.x2 < o.x1 || b.x1 > o.x2 || b.y2 < o.y1 || b.y1 > o.y2));
    pts.forEach((p) => boxes.push({ x1: X(p.per_month) - 7, x2: X(p.per_month) + 7, y1: Y(p.lift) - 7, y2: Y(p.lift) + 7 }));
    let labels = "";
    pts.filter((p) => p.label).forEach((p) => {
      const w = p.tag.length * 6.6, cx = X(p.per_month), cy = Y(p.lift);
      for (const side of [1, -1]) {
        const b = side > 0 ? { x1: cx + 9, x2: cx + 9 + w, y1: cy - 7, y2: cy + 7 } : { x1: cx - 9 - w, x2: cx - 9, y1: cy - 7, y2: cy + 7 };
        if (b.x1 < L || b.x2 > W - R) continue;
        if (!hit(b)) {
          boxes.push(b);
          labels += `<text x="${side > 0 ? b.x1 : b.x2}" y="${cy + 4}" text-anchor="${side > 0 ? "start" : "end"}" font-size="11" fill="var(--ink)" paint-order="stroke" stroke="var(--card)" stroke-width="4" stroke-linejoin="round">${esc(p.tag)}</text>`;
          break;
        }
      }
    });
    pts.forEach((p, i) => {
      s += `<circle data-i="${i}" cx="${X(p.per_month).toFixed(1)}" cy="${Y(p.lift).toFixed(1)}" r="6" fill="${CAT_COLOR[p.category] || "var(--s2)"}" stroke="var(--card)" stroke-width="2"/>`;
    });
    s += labels;
    s += `<rect class="hit" x="${L}" y="${T}" width="${W - L - R}" height="${H - T - B}" fill="transparent" style="cursor:pointer"/>`;
    el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Карта ниш: по горизонтали число постов в месяц, по вертикали отклик">${s}</svg>`;
    const svg = el.querySelector("svg"), area = svg.querySelector(".hit");
    let cur = null;
    const nearest = (e) => {
      const r = svg.getBoundingClientRect(), k = W / r.width;
      const mx = (e.clientX - r.left) * k, my = (e.clientY - r.top) * k;
      let best = null, bd = 16 * 16;
      pts.forEach((p, i) => { const d = (X(p.per_month) - mx) ** 2 + (Y(p.lift) - my) ** 2; if (d < bd) { bd = d; best = i; } });
      return best;
    };
    area.addEventListener("mousemove", (e) => {
      const i = nearest(e);
      if (cur !== null) svg.querySelector(`circle[data-i="${cur}"]`).setAttribute("r", "6");
      cur = i;
      if (i === null) { hideTip(); return; }
      svg.querySelector(`circle[data-i="${i}"]`).setAttribute("r", "8");
      const p = pts[i];
      showTip(`<b>${esc(p.tag)}</b><br>${Math.round(p.per_month)} постов в месяц<br>отклик ${fmtX(p.lift)} к среднему${p.mine ? "<br>ваш стиль" : ""}`, e.clientX, e.clientY);
    });
    area.addEventListener("mouseleave", () => { if (cur !== null) svg.querySelector(`circle[data-i="${cur}"]`).setAttribute("r", "6"); cur = null; hideTip(); });
    area.addEventListener("click", (e) => { const i = nearest(e); if (i !== null && onPick) onPick(pts[i].tag); });
  }

  /* Рост каналов: несколько линий по месяцам (подписи «ММ.ГГ»), общая ось, подсказка по месяцу */
  function lineChart(el, series) {
    const key = (m) => m.slice(3) + m.slice(0, 2);
    const months = [...new Set(series.flatMap((s) => s.points.map((p) => p.m)))].sort((a, b) => key(a) < key(b) ? -1 : 1);
    if (months.length < 2) { el.innerHTML = `<p class="muted small">Пока мало данных для графика.</p>`; return; }
    const W = Math.max(320, Math.round(el.clientWidth || 640)), H = 240, L = 52, R = 14, T = 12, B = 30, n = months.length;
    const idx = Object.fromEntries(months.map((m, i) => [m, i]));
    const vals = series.flatMap((s) => s.points.map((p) => p.v));
    const raw = Math.max(...vals) / 3 || 1, mag = Math.pow(10, Math.floor(Math.log10(raw)));
    const stepV = [1, 2, 2.5, 5, 10].map((k) => k * mag).find((k) => k >= raw);
    const hi = stepV * 3;   // «круглые» деления оси: 0, 4, 8, 12 и т.п.
    const X = (i) => L + i * (W - L - R) / (n - 1);
    const Y = (v) => T + (hi - v) / hi * (H - T - B);
    const fmtN = (v) => v >= 10000 ? (v / 1000).toFixed(0) + "k" : v >= 1000 ? (v / 1000).toFixed(1) + "k" : String(Math.round(v));
    let s = "";
    [0, 1, 2, 3].forEach((k) => {
      const v = hi * k / 3;
      s += `<line x1="${L}" x2="${W - R}" y1="${Y(v)}" y2="${Y(v)}" stroke="var(--grid)"/>`;
      s += `<text x="${L - 8}" y="${Y(v) + 4}" text-anchor="end" font-size="11" fill="var(--ink-3)">${fmtN(v)}</text>`;
    });
    const step = Math.max(1, Math.ceil(n / Math.max(3, Math.floor(W / 90))));
    months.forEach((m, i) => {
      if (i % step === 0 || i === n - 1) {
        if (i !== n - 1 && n - 1 - i < step / 2) return;
        s += `<text x="${X(i)}" y="${H - 10}" text-anchor="${i === 0 ? "start" : i === n - 1 ? "end" : "middle"}" font-size="11" fill="var(--ink-3)">${esc(m)}</text>`;
      }
    });
    series.forEach((sr) => {
      const d = sr.points.map((p, k) => (k ? "L" : "M") + X(idx[p.m]).toFixed(1) + " " + Y(p.v).toFixed(1)).join(" ");
      s += `<path d="${d}" fill="none" stroke="${sr.color}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>`;
    });
    s += `<line class="hx" x1="0" x2="0" y1="${T}" y2="${H - B}" stroke="var(--ink-3)" visibility="hidden"/>`;
    series.forEach((sr, k) => { s += `<circle class="hd hd${k}" r="4" fill="${sr.color}" stroke="var(--card)" stroke-width="2" visibility="hidden"/>`; });
    s += `<rect class="hit" x="${L}" y="${T}" width="${W - L - R}" height="${H - T - B}" fill="transparent"/>`;
    el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Медиана просмотров по месяцам">${s}</svg>`;
    const svg = el.querySelector("svg"), hx = svg.querySelector(".hx");
    const maps = series.map((sr) => Object.fromEntries(sr.points.map((p) => [p.m, p.v])));
    svg.querySelector(".hit").addEventListener("mousemove", (e) => {
      const r = svg.getBoundingClientRect();
      const i = Math.max(0, Math.min(n - 1, Math.round(((e.clientX - r.left) / r.width * W - L) / (W - L - R) * (n - 1))));
      const m = months[i];
      hx.setAttribute("x1", X(i)); hx.setAttribute("x2", X(i)); hx.setAttribute("visibility", "visible");
      let rows = "";
      series.forEach((sr, k) => {
        const dot = svg.querySelector(".hd" + k), v = maps[k][m];
        if (v === undefined) { dot.setAttribute("visibility", "hidden"); return; }
        dot.setAttribute("cx", X(i)); dot.setAttribute("cy", Y(v)); dot.setAttribute("visibility", "visible");
        rows += `<br><span style="color:${sr.color}">●</span> ${esc(sr.name)}: <b>${v.toLocaleString(LOC)}</b>`;
      });
      showTip(`${esc(m)} — медиана просмотров${rows}`, e.clientX, e.clientY);
    });
    svg.querySelector(".hit").addEventListener("mouseleave", () => {
      hx.setAttribute("visibility", "hidden");
      svg.querySelectorAll(".hd").forEach((d) => d.setAttribute("visibility", "hidden"));
      hideTip();
    });
  }

  window.Charts = { sparkline, forecastChart, nicheChart, lineChart, showTip, hideTip, esc };
})();
