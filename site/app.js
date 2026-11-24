/* Tokenized Stock Observatory: renders the files in site/data/. No build step; Chart.js is vendored. */
"use strict";
(() => {
  const SESS = ["RTH", "PRE", "POST", "OVERNIGHT", "CLOSED"];
  const LABEL = { RTH: "Regular hours", PRE: "Pre-market", POST: "After-hours", OVERNIGHT: "Overnight", CLOSED: "Closed" };
  const HOURS = { RTH: "09:30 to 16:00", PRE: "04:00 to 09:30", POST: "16:00 to 20:00", OVERNIGHT: "20:00 to 04:00", CLOSED: "weekends and holidays" };
  const VAR = { RTH: "--rth", PRE: "--pre", POST: "--post", OVERNIGHT: "--ovn", CLOSED: "--closed" };
  const FAMILY = {
    xstocks: "xStocks", ondo: "Ondo Global Markets", bstocks: "bStocks (Binance)", robinhood: "Robinhood",
    coinbase: "Coinbase", dinari: "Dinari", backpack: "Backpack", other: "Other issuers",
  };
  const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const WEEKDAY = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
  const PALETTE = {
    light: ["#2F5F8F", "#3E8E7E", "#8F82C2", "#B5523B", "#7FA6C6", "#4C3D8C", "#6B7F2A", "#9C6B98", "#5B6B78", "#2E7D9A", "#A3A9B0"],
    dark: ["#5C8FC4", "#5FB3A1", "#ABA0DA", "#D9826C", "#9DBCD6", "#7A6CC4", "#9DB35A", "#C495C0", "#8D9DAB", "#5BAAC6", "#6F7A85"],
  };
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => Array.from(el.querySelectorAll(s));
  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const darkMode = () => {
    const forced = document.documentElement.dataset.theme;
    return forced ? forced === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
  };
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fin = (v) => typeof v === "number" && Number.isFinite(v);
  const sum = (arr) => arr.reduce((a, b) => a + (fin(b) ? b : 0), 0);

  function usd(v) {
    if (!fin(v)) return "n/a";
    const a = Math.abs(v);
    const f = (x, d) => x.toFixed(d).replace(/\.0+$/, "");
    if (a >= 1e12) return `$${f(v / 1e12, 2)}T`;
    if (a >= 1e9) return `$${f(v / 1e9, a >= 1e10 ? 1 : 2)}B`;
    if (a >= 1e6) return `$${f(v / 1e6, a >= 1e8 ? 0 : 1)}M`;
    if (a >= 1e3) return `$${f(v / 1e3, a >= 1e5 ? 0 : 1)}K`;
    return `$${v.toFixed(0)}`;
  }
  const pct = (v, d = 0) => (fin(v) ? `${(v * 100).toFixed(d)}%` : "n/a");
  const bp = (v) => (fin(v) ? `${v >= 10 ? v.toFixed(0) : v >= 1 ? v.toFixed(1) : v.toFixed(2)} bp` : "n/a");
  const fix = (v, d = 2) => (fin(v) ? v.toFixed(d) : "n/a");
  const parts = (iso) => iso.split("-").map(Number);
  const shortDate = (iso) => { const [, m, d] = parts(iso); return `${d} ${MONTHS[m - 1]}`; };
  const longDate = (iso) => { const [y, m, d] = parts(iso); return `${d} ${MONTHS[m - 1]} ${y}`; };
  const weekday = (iso) => WEEKDAY[new Date(`${iso}T12:00:00Z`).getUTCDay()];
  const slotTime = (s) => `${String(Math.floor(s / 2) % 24).padStart(2, "0")}:${s % 2 ? "30" : "00"}`;
  function rgba(hex, a) {
    let h = hex.replace("#", "");
    if (h.length === 3) h = h.split("").map((c) => c + c).join("");
    const n = parseInt(h, 16);
    return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${a})`;
  }

  const D = {};
  const ui = { week: "token", daily: "usd", range: "30", venue: "venues", query: "", sort: { key: "token", dir: -1 }, ticker: null, tickerLimit: "10", closureLimit: "10" };
  const charts = {};

  async function load(name) {
    const r = await fetch(`data/${name}`, { cache: "no-cache" });
    if (!r.ok) throw new Error(`${name}: HTTP ${r.status}`);
    return r.json();
  }

  /* ---------- Chart.js ---------- */
  const markers = {
    id: "markers",
    afterDatasetsDraw(chart, _args, opts) {
      const items = (opts && opts.items) || [];
      const x = chart.scales.x;
      if (!items.length || !x) return;
      const { ctx, chartArea } = chart;
      const labels = chart.data.labels || [];
      ctx.save();
      for (const m of items) {
        const i = labels.indexOf(m.date);
        if (i < 0) continue;
        const step = labels.length > 1 ? x.getPixelForValue(1) - x.getPixelForValue(0) : 0;
        const px = x.getPixelForValue(i) - step / 2;
        ctx.strokeStyle = css("--ink");
        ctx.lineWidth = 1;
        ctx.setLineDash([3, 3]);
        ctx.beginPath();
        ctx.moveTo(px, chartArea.top);
        ctx.lineTo(px, chartArea.bottom);
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.fillStyle = css("--ink");
        ctx.font = `500 11px ${css("--font")}`;
        const fits = px + 6 + ctx.measureText(m.label).width < chartArea.right;
        ctx.textAlign = fits ? "left" : "right";
        ctx.fillText(m.label, fits ? px + 6 : px - 6, chartArea.top + 12);
      }
      ctx.restore();
    },
  };

  function chartDefaults() {
    Chart.defaults.font.family = css("--font");
    Chart.defaults.font.size = 12;
    Chart.defaults.color = css("--muted");
    Chart.defaults.borderColor = css("--faint");
    // Controls rebuild charts in place. Animating that replacement can leave a queued
    // Chart.js frame pointing at the chart that was just destroyed, producing a blank
    // canvas on fast (or load-adjacent) clicks. Immediate redraws are clearer here.
    Chart.defaults.animation = false;
    Chart.defaults.maintainAspectRatio = false;
    Chart.defaults.plugins.legend.display = false;
    Object.assign(Chart.defaults.plugins.tooltip, {
      backgroundColor: css("--ink"), titleColor: css("--bg"), bodyColor: css("--bg"), footerColor: css("--bg"),
      padding: 10, cornerRadius: 4, boxPadding: 4,
    });
  }

  function make(id, config) {
    if (charts[id]) {
      charts[id].stop();
      charts[id].destroy();
    }
    charts[id] = new Chart($(`#${id}`), config);
  }

  const dateAxis = () => ({
    stacked: true,
    grid: { display: false },
    ticks: { autoSkip: true, maxTicksLimit: 9, maxRotation: 0, callback(v) { return shortDate(this.getLabelForValue(v)); } },
  });
  const dateTitle = (items) => `${weekday(items[0].label)} ${longDate(items[0].label)}`;
  const topDown = (a, b) => b.datasetIndex - a.datasetIndex;

  function pressed(group, value) {
    $$(`[data-${group}]`).forEach((b) => b.setAttribute("aria-pressed", String(b.dataset[group] === value)));
  }

  function rangeStart(dates) {
    if (ui.range === "all") return 0;
    return Math.max(0, dates.length - Number(ui.range || 30));
  }

  function rangeLabel(dates) {
    if (!dates.length) return "No dates";
    const i = rangeStart(dates);
    return `${longDate(dates[i])} to ${longDate(dates[dates.length - 1])}`;
  }

  /* ---------- Header, week grid, summary ---------- */
  function renderStamp() {
    const m = D.meta;
    $("#demo-banner").hidden = !m.demo;
    const t = new Date(m.generated_at);
    const two = (n) => String(n).padStart(2, "0");
    const when = `${t.getUTCDate()} ${MONTHS[t.getUTCMonth()]} ${t.getUTCFullYear()}, ${two(t.getUTCHours())}:${two(t.getUTCMinutes())} UTC`;
    const span = m.first_date ? `, covering ${longDate(m.first_date)} to ${longDate(m.last_date)}` : "";
    $("#stamp").textContent = m.demo ? `Synthetic demo data built ${when}${span}.` : `Updated ${when}${span}.`;
  }

  function renderLegend() {
    $("#legend").innerHTML = SESS.map((s) =>
      `<li><span class="sw" style="background:var(${VAR[s]})"></span><b>${LABEL[s]}</b><span class="h">${HOURS[s]}</span></li>`).join("");
  }

  function renderOverview() {
    const m = D.meta;
    const k = m.kpis || {};
    const span = m.first_date && m.last_date
      ? `${Math.floor((new Date(`${m.last_date}T12:00:00Z`) - new Date(`${m.first_date}T12:00:00Z`)) / 86400000) + 1} days`
      : "No history";
    const items = [
      [span, "Historical span"],
      [usd(k.token_total || 0), `Token volume in the current ${k.days || 0}-day summary window`],
      [pct(k.outside_rth_share), "Volume outside regular US hours"],
      [String((m.markets || {}).total || 0), `Token markets in the historical registry`],
    ];
    $("#kpi-grid").innerHTML = items.map(([value, label]) =>
      `<div class="kpi"><span class="value">${esc(value)}</span><span class="label">${esc(label)}</span></div>`).join("");
    const history = m.history || {};
    const tokenDates = history.token_dates || 0;
    const stockDates = history.stock_dates || 0;
    const latestStockDate = D.daily.dates.filter((_, i) =>
      SESS.some((session) => D.daily.stock[session][i] > 0)).pop();
    const source = (m.sources || []).find((s) => s.id === "stocks");
    const notes = [`Token trades appear on ${tokenDates} calendar dates.`];
    if (stockDates && latestStockDate) {
      notes.push(`US stock volume appears on ${stockDates} dates. Latest recorded trading date: ${longDate(latestStockDate)}.`);
    } else {
      notes.push("The current dataset contains no recorded US stock volume.");
    }
    if (source?.ok === false) {
      notes.push(source.error?.includes("ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY not set")
        ? "The latest refresh skipped US stocks because the collection job lacked Alpaca credentials."
        : "The latest Alpaca refresh failed. See Sources for details.");
    }
    $("#overview-note").textContent = notes.join(" ");
  }

  function renderWeek() {
    const w = D.week;
    if (!w) return;
    const stockAvailable = (D.meta.kpis || {}).stock_total > 0;
    const stockButton = $('[data-week="stock"]');
    stockButton.disabled = !stockAvailable;
    stockButton.title = stockAvailable ? "" : "Requires Alpaca stock data";
    if (!stockAvailable && ui.week === "stock") {
      ui.week = "token";
      pressed("week", ui.week);
    }
    const grid = w[ui.week] || [];
    const cell = 14, gap = 2, left = 34, top = 20, pitch = cell + gap;
    let max = 0;
    for (const row of grid) for (const v of row) if (v > max) max = v;
    const color = Object.fromEntries(SESS.map((s) => [s, css(VAR[s])]));
    const faint = css("--faint");
    const out = [];
    for (const s of [0, 8, 19, 32, 40]) out.push(`<text x="${left + s * pitch}" y="${top - 7}">${slotTime(s)}</text>`);
    for (let d = 0; d < 7; d++) {
      const y = top + d * pitch;
      out.push(`<text x="0" y="${y + cell - 3}">${DAYS[d]}</text>`);
      for (let s = 0; s < 48; s++) {
        const v = (grid[d] || [])[s] || 0;
        const sess = SESS[w.template[d][s]];
        const a = max > 0 && v > 0 ? 0.14 + 0.86 * Math.sqrt(v / max) : 0;
        const fill = a ? `fill="${color[sess]}" fill-opacity="${a.toFixed(3)}"` : `fill="${faint}"`;
        out.push(`<rect x="${left + s * pitch}" y="${y}" width="${cell}" height="${cell}" rx="2" ${fill}>` +
          `<title>${DAYS[d]} ${slotTime(s)} to ${slotTime(s + 1)} ET, ${LABEL[sess].toLowerCase()}: ${usd(v)} on average</title></rect>`);
      }
    }
    out.push(`<rect class="open-box" x="${left + 19 * pitch - 1.5}" y="${top - 1.5}" width="${13 * pitch + 1}" height="${5 * pitch + 1}" rx="3"/>`);
    const svg = $("#week-grid");
    svg.setAttribute("viewBox", `0 0 ${left + 48 * pitch} ${top + 7 * pitch}`);
    svg.innerHTML = out.join("");
    $("#week-note").textContent = ui.week === "token"
      ? `Average dollar volume of tokenized stocks in each half hour of the week, New York time, over the last ${w.days} days. Darker means more trading. The outlined block is the regular session.`
      : "The same stocks on the US consolidated tape. Before 6 December 2026 the tape does not comprehensively cover overnight trading, although occasional eligible or late prints can appear; nothing trades at weekends.";
  }

  function renderSummary() {
    const k = D.meta.kpis;
    if (!k || !fin(k.token_total) || k.token_total <= 0) {
      $("#summary").textContent = "No token volume recorded yet.";
      return;
    }
    const out = [`Current ${k.days}-day summary window (${longDate(k.start)} to ${longDate(k.end)}): <strong>${usd(k.token_total)}</strong> token volume; <strong>${pct(k.outside_rth_share)}</strong> outside regular hours; <strong>${pct(k.closed_share)}</strong> while US venues were closed.`];
    if (fin(k.ratio_bp) && k.stock_total > 0) {
      const r = k.ratio_bp_by_session || {};
      out.push(`Token volume as a share of the same stocks' dollar volume: <strong>${bp(k.ratio_bp)}</strong> overall, ${bp(r.RTH)} regular hours, ${bp(r.PRE)} pre-market and ${bp(r.POST)} after-hours.`);
    } else {
      out.push("This summary window has no comparable US stock volume.");
    }
    if (k.token_other > 0) out.push(`DEX pools pairing stock tokens with memecoins traded another ${usd(k.token_other)}, not counted above.`);
    $("#summary").innerHTML = out.join(" ");
  }

  /* ---------- Daily, mix, venues ---------- */
  function renderDaily() {
    const d = D.daily;
    const start = rangeStart(d.dates);
    const dates = d.dates.slice(start);
    const share = ui.daily === "share";
    const totals = dates.map((_, j) => SESS.reduce((a, s) => a + (d.token[s][start + j] || 0), 0));
    const datasets = SESS.map((s) => ({
      label: LABEL[s], backgroundColor: css(VAR[s]), borderWidth: 0, barPercentage: 0.9, categoryPercentage: 0.94,
      data: d.token[s].slice(start).map((v, i) => (share ? (totals[i] ? (v / totals[i]) * 100 : 0) : v)),
    }));
    make("daily-chart", {
      type: "bar",
      data: { labels: dates, datasets },
      options: {
        interaction: { mode: "index", intersect: false },
        scales: { x: dateAxis(), y: { stacked: true, max: share ? 100 : undefined, ticks: { callback: (v) => (share ? `${v}%` : usd(v)) } } },
        plugins: {
          markers: { items: D.meta.events || [] },
          tooltip: {
            itemSort: topDown,
            callbacks: {
              title: dateTitle,
              label: (c) => ` ${c.dataset.label}: ${share ? `${c.parsed.y.toFixed(1)}%` : usd(c.parsed.y)}`,
              footer: (items) => (share ? "" : `Total ${usd(totals[items[0].dataIndex])}`),
            },
          },
        },
      },
    });
    $("#daily-note").textContent = `${rangeLabel(d.dates)}. Overnight trading from 20:00 ET counts toward the next trading date; weekends and holidays retain their own dates.`;
  }

  function renderMix() {
    const k = D.meta.kpis || {};
    const st = k.share_token || {}, ss = k.share_stock || {}, rb = k.ratio_bp_by_session || {};
    const hasStock = k.stock_total > 0;
    const datasets = SESS.map((s) => ({
      label: LABEL[s], backgroundColor: css(VAR[s]), borderWidth: 0, barPercentage: 0.72,
      data: [(st[s] || 0) * 100].concat(hasStock ? [(ss[s] || 0) * 100] : []),
    }));
    make("mix-chart", {
      type: "bar",
      data: { labels: hasStock ? ["Tokens", "The stocks"] : ["Tokens"], datasets },
      options: {
        indexAxis: "y",
        scales: { x: { stacked: true, max: 100, ticks: { callback: (v) => `${v}%` } }, y: { stacked: true, grid: { display: false } } },
        plugins: { tooltip: { callbacks: { label: (c) => ` ${c.dataset.label}: ${c.parsed.x.toFixed(1)}%` } } },
      },
    });
    const rows = SESS.map((s) => {
      const tok = (st[s] || 0) * (k.token_total || 0);
      const stk = (ss[s] || 0) * (k.stock_total || 0);
      const none = "no stock volume recorded";
      const ratio = fin(rb[s]) ? bp(rb[s]) : hasStock && stk === 0 ? none : "n/a";
      return `<tr><td><span class="sw-inline" style="background:var(${VAR[s]})"></span>${LABEL[s]}</td><td>${usd(tok)}</td>` +
        `<td>${hasStock ? (stk > 0 ? usd(stk) : "none recorded") : "n/a"}</td><td>${ratio}</td></tr>`;
    });
    $("#mix-table").innerHTML = `<thead><tr><th>Session</th><th>Tokens</th><th>The stocks</th><th title="Token volume as basis points of the stocks' own dollar volume">Tokens as a share</th></tr></thead><tbody>${rows.join("")}</tbody>`;
    $("#mix-note").textContent = `Last ${k.days} days. A basis point (bp) is a hundredth of a percent of the stocks' own dollar volume in that session, counting only tokens whose stock is in the data.`;
  }

  function renderVenues() {
    const d = D.daily;
    const start = rangeStart(d.dates);
    const dates = d.dates.slice(start);
    const groups = d[ui.venue] || {};
    const keys = Object.keys(groups).sort((a, b) => sum(groups[b].slice(start)) - sum(groups[a].slice(start)));
    const pal = PALETTE[darkMode() ? "dark" : "light"];
    const names = D.meta.venue_labels || {};
    const datasets = keys.map((k, i) => ({
      label: ui.venue === "venues" ? names[k] || k : FAMILY[k] || k,
      data: groups[k].slice(start), backgroundColor: pal[i % pal.length], borderWidth: 0, barPercentage: 0.9, categoryPercentage: 0.94,
    }));
    make("venue-chart", {
      type: "bar",
      data: { labels: dates, datasets },
      options: {
        interaction: { mode: "index", intersect: false },
        scales: { x: dateAxis(), y: { stacked: true, ticks: { callback: (v) => usd(v) } } },
        plugins: {
          legend: { display: true, position: "bottom", labels: { boxWidth: 10, boxHeight: 10, padding: 14 } },
          markers: { items: D.meta.events || [] },
          tooltip: { itemSort: topDown, filter: (c) => c.parsed.y > 0, callbacks: { title: dateTitle, label: (c) => ` ${c.dataset.label}: ${usd(c.parsed.y)}` } },
        },
      },
    });
    const other = sum(d.token_other.slice(start));
    $("#venue-note").textContent = `${rangeLabel(d.dates)}. Volume includes exchange order books and eligible on-chain trading pools.` +
      (other > 0 ? ` Pools with other counterpart assets contributed a further ${usd(other)}, excluded here.` : "");
    const issuers = ui.venue === "families";
    $("#issuer-detail").hidden = !issuers;
    if (issuers) {
      const rows = keys.map(key => {
        const values = groups[key].slice(start);
        const observed = dates.filter((_, i) => values[i] > 0);
        return `<tr><td>${esc(FAMILY[key] || key)}</td><td>${usd(sum(values))}</td><td>${observed.length}</td><td>${observed.length ? longDate(observed[0]) : "—"}</td><td>${observed.length ? longDate(observed[observed.length-1]) : "—"}</td></tr>`;
      });
      $("#issuer-totals").innerHTML = "<thead><tr><th>Issuer</th><th>Recorded volume</th><th>Dates with volume</th><th>First in range</th><th>Latest in range</th></tr></thead><tbody>" + rows.join("") + "</tbody>";
    }
  }

  /* ---------- Stocks table and detail ---------- */
  const offShare = (r) => (r.token ? 1 - r.rth / r.token : 0);
  const closedShare = (r) => (r.token ? r.closed / r.token : 0);
  const COLS = [
    { key: "t", label: "Stock", get: (r) => r.t, cell: (r) => `<td class="t">${esc(r.t)}</td>` },
    { key: "token", label: "Token volume", get: (r) => r.token, cell: (r) => `<td>${usd(r.token)}</td>` },
    { key: "off", label: "Outside regular hours", get: offShare, cell: (r) => `<td><span class="bar off" style="width:${(offShare(r) * 3).toFixed(2)}rem"></span>${pct(offShare(r))}</td>` },
    { key: "closed", label: "While closed", get: closedShare, cell: (r) => `<td><span class="bar" style="width:${(closedShare(r) * 3).toFixed(2)}rem"></span>${pct(closedShare(r))}</td>` },
    { key: "stock", label: "Stock volume", get: (r) => (fin(r.stock) ? r.stock : -1), cell: (r) => `<td>${fin(r.stock) ? usd(r.stock) : '<span class="sub">n/a</span>'}</td>` },
    { key: "ratio", label: "Tokens as a share", get: (r) => (fin(r.ratio_bp) ? r.ratio_bp : -1), cell: (r) => `<td>${fin(r.ratio_bp) ? bp(r.ratio_bp) : '<span class="sub">n/a</span>'}</td>` },
    { key: "venues", label: "Venues", get: (r) => r.venues.length, cell: (r) => `<td title="${esc(r.venues.map((v) => (D.meta.venue_labels || {})[v] || v).join(", "))}">${r.venues.length}</td>` },
  ];

  function renderTickers(focusKey) {
    const T = D.tickers;
    const allRows = (T.rows || []).slice();
    const q = ui.query.trim().toUpperCase();
    const rows = q ? allRows.filter((r) => r.t.toUpperCase().includes(q)) : allRows;
    const col = COLS.find((c) => c.key === ui.sort.key) || COLS[1];
    rows.sort((a, b) => {
      const x = col.get(a), y = col.get(b);
      return (typeof x === "string" ? x.localeCompare(y) : x - y) * ui.sort.dir;
    });
    const limit = ui.tickerLimit === "all" ? rows.length : Number(ui.tickerLimit);
    const shown = rows.slice(0, limit);
    const head = COLS.map((c) => {
      const sort = c.key === ui.sort.key ? (ui.sort.dir < 0 ? "descending" : "ascending") : "none";
      return `<th scope="col" aria-sort="${sort}"><button type="button" data-sort="${c.key}">${c.label}</button></th>`;
    }).join("");
    const body = shown.map((r) => `<tr class="pick" tabindex="0" data-t="${esc(r.t)}" aria-selected="${r.t === ui.ticker}">${COLS.map((c) => c.cell(r)).join("")}</tr>`).join("");
    $("#ticker-table").innerHTML = rows.length
      ? `<thead><tr>${head}</tr></thead><tbody>${body}</tbody>`
      : "<tbody><tr><td>No stock-level volume yet.</td></tr></tbody>";
    if (T.start) $("#stocks-note").textContent = `${longDate(T.start)} to ${longDate(T.end)} (${T.window_days} day${T.window_days === 1 ? "" : "s"}). Select a row to see its daily series.`;
    const scope = q ? `${rows.length} of ${allRows.length} stocks match “${ui.query.trim()}”` : `${allRows.length} stocks in the current summary window`;
    const shownLabel = shown.length === rows.length ? `all ${rows.length} stocks` : `the top ${shown.length}`;
    $("#ticker-count").textContent = `${scope}. Showing ${shownLabel} by ${col.label.toLowerCase()}.`;
    if (focusKey) { const b = $(`[data-sort="${focusKey}"]`); if (b) b.focus(); }
  }

  async function selectTicker(t) {
    ui.ticker = t;
    $$("#ticker-table tr[data-t]").forEach((tr) => tr.setAttribute("aria-selected", String(tr.dataset.t === t)));
    const row = (D.tickers.rows || []).find((r) => r.t === t);
    const box = $("#ticker-detail");
    box.hidden = false;
    $("#ticker-h").textContent = `${t}: token volume by session`;
    if (charts["ticker-chart"]) { charts["ticker-chart"].destroy(); delete charts["ticker-chart"]; }
    D.series = null;
    if (!row || !row.series) {
      $("#ticker-note").textContent = "Day-by-day detail is published for the 100 most traded stocks.";
      return;
    }
    try {
      const series = await load(`t/${encodeURIComponent(t)}.json`);
      if (ui.ticker !== t) return;
      D.series = series;
    } catch (e) {
      if (ui.ticker !== t) return;
      $("#ticker-note").textContent = `Could not load the detail for ${t} (${e.message}).`;
      return;
    }
    renderTickerChart(row);
    box.scrollIntoView({ behavior: reduced ? "auto" : "smooth", block: "nearest" });
  }

  function renderTickerChart(row) {
    const s = D.series;
    if (!s || s.t !== row.t) return;
    const start = rangeStart(s.dates);
    const dates = s.dates.slice(start);
    const datasets = SESS.map((k) => ({
      type: "bar", label: LABEL[k], data: s.token[k].slice(start), backgroundColor: css(VAR[k]), borderWidth: 0,
      stack: "tokens", yAxisID: "y", barPercentage: 0.9, categoryPercentage: 0.94,
    }));
    if (s.stock) {
      const stock = dates.map((_, j) => SESS.reduce((a, k) => a + (s.stock[k][start + j] || 0), 0));
      datasets.push({
        type: "line", label: "The stock's own dollar volume (right axis)", data: stock.map((v) => (v > 0 ? v : null)),
        borderColor: css("--ink"), backgroundColor: css("--ink"), borderWidth: 1.25, pointRadius: 0, spanGaps: false, yAxisID: "y2", order: -1,
      });
    }
    make("ticker-chart", {
      data: { labels: dates, datasets },
      options: {
        interaction: { mode: "index", intersect: false },
        scales: {
          x: dateAxis(),
          y: { stacked: true, ticks: { callback: (v) => usd(v) } },
          y2: { display: Boolean(s.stock), position: "right", grid: { display: false }, ticks: { callback: (v) => usd(v) } },
        },
        plugins: {
          markers: { items: D.meta.events || [] },
          tooltip: { itemSort: topDown, filter: (c) => c.parsed.y > 0, callbacks: { title: dateTitle, label: (c) => ` ${c.dataset.label}: ${usd(c.parsed.y)}` } },
        },
      },
    });
    const n = row.venues.length;
    const out = [`Summary window (${D.tickers.window_days} days): ${usd(row.token)} of ${row.t} tokens on ${n} venue${n === 1 ? "" : "s"}, ${pct(offShare(row))} outside regular hours and ${pct(closedShare(row))} while closed. Chart: ${rangeLabel(s.dates)}.`];
    if (fin(row.ratio_bp)) out.push(`That is ${bp(row.ratio_bp)} of ${row.t}'s own dollar volume.`);
    const c = ((D.closures || {}).by_ticker || []).find((x) => x.underlying === row.t);
    if (c && fin(c.beta)) out.push(`Weekend test over ${c.windows} closures: slope ${fix(c.beta)} (standard error ${fix(c.se)}), R² ${fix(c.r2)}.`);
    $("#ticker-note").textContent = out.join(" ");
  }

  /* ---------- Weekend price test ---------- */
  function renderClosures() {
    const C = D.closures || {};
    const pts = C.points || [];
    const p = C.pooled || {};
    const amber = css("--closed");
    const lim = Math.max(2, ...pts.map((q) => Math.abs(q.x) * 100), ...pts.map((q) => Math.abs(q.y) * 100)) * 1.05;
    const datasets = [
      {
        label: "One stock over one closure", pointRadius: 3.5, pointHoverRadius: 5, borderWidth: 0.5,
        backgroundColor: rgba(amber, 0.5), borderColor: rgba(amber, 0.95),
        data: pts.map((q) => ({ x: q.x * 100, y: q.y * 100, t: q.t, w: q.w, v: q.v })),
      },
      { label: "Token move equals the gap", data: [{ x: -lim, y: -lim }, { x: lim, y: lim }], showLine: true, borderColor: css("--muted"), borderDash: [4, 4], borderWidth: 1, pointRadius: 0 },
    ];
    if (fin(p.beta)) {
      datasets.push({ label: "Fitted line", showLine: true, borderColor: css("--ink"), borderWidth: 1.5, pointRadius: 0,
        data: [{ x: -lim, y: p.alpha * 100 - p.beta * lim }, { x: lim, y: p.alpha * 100 + p.beta * lim }] });
    }
    const tick = (v) => `${+v.toFixed(1)}%`;
    make("scatter", {
      type: "scatter",
      data: { datasets },
      options: {
        scales: {
          x: { min: -lim, max: lim, title: { display: true, text: "Token move while US venues were shut" }, ticks: { callback: tick } },
          y: { min: -lim, max: lim, title: { display: true, text: "Stock move to the next open" }, ticks: { callback: tick } },
        },
        plugins: {
          legend: { display: true, position: "bottom", labels: { boxWidth: 10, boxHeight: 10, padding: 14 } },
          tooltip: {
            filter: (c) => c.datasetIndex === 0,
            callbacks: {
              title: (items) => `${items[0].raw.t}, closure from ${longDate(items[0].raw.w)}`,
              label: (c) => ` Token ${c.raw.x.toFixed(2)}%, stock ${c.raw.y.toFixed(2)}%, ${usd(c.raw.v)} traded`,
            },
          },
        },
      },
    });
    const stats = [];
    if (fin(p.beta)) {
      stats.push(["Slope", `${fix(p.beta)} <small>standard error ${fix(p.se)}</small>`]);
      stats.push(["R²", fix(p.r2)]);
      stats.push(["Gap already priced", pct(p.anticipated)]);
      stats.push(["Same direction", `${pct(p.hit_rate)} <small>of ${p.n_hit} gaps over 0.5%</small>`]);
    }
    stats.push(["Observations", `${p.n || 0} <small>over ${p.windows || 0} closures</small>`]);
    $("#stats").innerHTML = stats.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join("");
    $("#stats-note").textContent = fin(p.beta)
      ? `Metrics calculated from ${p.n} stock-closure observations over ${p.windows} closures.`
      : "No comparable closure series yet. This panel requires overlapping token and underlying-stock history.";
    const bt = C.by_ticker || [];
    const closureLimit = ui.closureLimit === "all" ? bt.length : Number(ui.closureLimit);
    const shown = bt.slice(0, closureLimit);
    $("#closure-table").innerHTML = bt.length
      ? `<thead><tr><th>Stock</th><th>Closures</th><th>Slope</th><th>R²</th><th>Gap already priced</th><th>Same direction</th><th>Token volume</th></tr></thead><tbody>${shown.map((r) =>
        `<tr><td class="t">${esc(r.underlying)}</td><td>${r.windows}</td><td>${fix(r.beta)}</td><td>${fix(r.r2)}</td><td>${pct(r.anticipated)}</td><td>${pct(r.hit_rate)}</td><td>${usd(r.token_vol)}</td></tr>`).join("")}</tbody>`
      : "<tbody><tr><td>A stock appears here once it has six qualifying closures.</td></tr></tbody>";
    $("#closure-count").textContent = bt.length
      ? `${bt.length} stocks meet the minimum-history rule. Showing ${shown.length === bt.length ? `all ${bt.length} stocks` : `the top ${shown.length}`} by token volume.`
      : "No stocks meet the minimum-history rule yet.";
  }

  /* ---------- Method ---------- */
  function renderMethod() {
    const m = D.meta;
    $("#session-table").innerHTML = `<thead><tr><th>Session</th><th class="left">Hours, New York time</th></tr></thead><tbody>${(m.sessions || []).map((s) =>
      `<tr><td><span class="sw-inline" style="background:var(${VAR[s.id]})"></span>${esc(s.label)}</td><td class="left">${esc(s.hours)}</td></tr>`).join("")}</tbody>`;
    const u = m.universe || {};
    const cov = m.coverage;
    const bits = [];
    if (u.tokens) {
      bits.push(`The token list comes from CoinGecko's tokenized-stock categories: ${u.tokens} tokens, ${u.mapped} of them matched to a US ticker` +
        (u.tickers_checked ? ". The token-list refresh checked ticker mappings against Alpaca listings." : ". The token-list refresh assigned these symbols without an Alpaca listing check."));
    }
    if (cov && cov.coingecko_24h > 0) {
      bits.push(`In the 24 hours to ${esc(cov.as_of.slice(0, 16).replace("T", " "))} UTC these sources measured ${usd(cov.measured_24h)}, against ${usd(cov.coingecko_24h)} that CoinGecko reports for the same tokens on every venue it tracks (${pct(cov.measured_24h / cov.coingecko_24h)}). The two count slightly different things, so read it as a rough coverage check.`);
    }
    $("#coverage").textContent = bits.join(" ") || "Coverage figures appear after the first full run.";
    const src = m.sources || [];
    $("#source-table").innerHTML = src.length
      ? `<thead><tr><th>Source</th><th>Markets</th><th class="left">Observed market dates</th><th class="left">Latest collection</th><th>Last run</th></tr></thead><tbody>${src.map((s) => {
        const status = s.ok && (s.errors || s.discovery_errors) ? '<span class="sub">Partial</span>' : s.ok === true ? '<span class="status-ok">Working</span>'
          : s.ok === false ? '<span class="status-bad">Failing</span>' : '<span class="sub">No recorded run</span>';
        const detail = s.error ? ` <span class="sub">${esc(String(s.error).slice(0, 160))}</span>` : "";
        const errs = (s.errors ? ` <span class="sub">(${s.errors} market errors)</span>` : "") +
          (s.discovery_errors ? ` <span class="sub">(${s.discovery_errors} token pool lookups failed)</span>` : "");
        const runAt = s.finished || s.started;
        const when = runAt ? `${esc(runAt.slice(0, 16).replace("T", " "))} UTC` : "n/a";
        return `<tr><td>${esc(s.label)}</td><td>${s.markets ?? "—"}</td><td class="left">${s.first_date ? longDate(s.first_date) + " to " + longDate(s.last_date) : "—"}</td><td class="left">${status}${errs}${detail}</td><td>${when}</td></tr>`;
      }).join("")}</tbody>`
      : "<tbody><tr><td>No runs recorded yet.</td></tr></tbody>";
    const links = ['<a href="data/token_daily.csv">Token volume by day, session, venue, issuer and stock</a>'];
    if (m.history?.stock_dates > 0) links.push('<a href="data/stock_daily.csv">Stock volume by day and session</a>');
    if (((D.closures || {}).points || []).length) links.push('<a href="data/closures.csv">Closure observations</a>');
    $("#downloads").innerHTML = links.join(", ") + ".";
  }

  /* ---------- Boot ---------- */
  function showEmpty(err) {
    $("#stamp").textContent = "";
    const local = location.protocol === "file:";
    if (local) {
      $("#main").innerHTML = '<div class="empty"><h2>Open the dashboard online</h2><p>Your browser could not load the dashboard data from this local file. <a href="https://tianhaoyin1120.github.io/offhours/">View the published dashboard</a> or serve the site folder over HTTP.</p></div>';
      return;
    }
    $("#main").innerHTML = `<div class="empty"><h2>Dashboard could not load</h2><p>Reload the page to retry.</p>${err ? `<p class="sub">${esc(err.message)}</p>` : ""}</div>`;
  }

  function historicalComparison(history, start, end, mode) {
    const totals = new Map();
    for (const [day, records] of Object.entries(history)) {
      if (day < start || day > end) continue;
      const tickers = new Map();
      for (const [ticker, session, token, stock] of records) {
        if (mode !== "calendar" && mode !== "matched" && mode !== session) continue;
        if (!tickers.has(ticker)) tickers.set(ticker, [0, 0]);
        const pair = tickers.get(ticker);
        pair[0] += token;
        pair[1] += stock;
      }
      for (const [ticker, [token, stock]] of tickers) {
        const both = token > 0 && stock > 0;
        if (!both && mode !== "calendar") continue;
        if (!totals.has(ticker)) totals.set(ticker, { t: ticker, token: 0, stock: 0, days: 0, above: 0 });
        const row = totals.get(ticker);
        row.token += token;
        row.stock += stock;
        if (both) {
          row.days++;
          if (token > stock) row.above++;
        }
      }
    }
    return Array.from(totals.values()).filter(row => row.days).map(row => ({
      ...row, token: row.token / 100, stock: row.stock / 100
    }));
  }

  function setComparisonRange() {
    const dates = D.daily.dates;
    const range = $("#volume-range").value;
    if (range !== "custom" && dates.length) {
      $("#volume-start").value = dates[range === "all" ? 0 : Math.max(0, dates.length - Number(range))];
      $("#volume-end").value = dates[dates.length - 1];
    }
    renderComparisons();
  }

  function renderComparisons() {
    const mode = $("#volume-mode").value;
    const limit = $("#volume-limit").value;
    const start = $("#volume-start").value, end = $("#volume-end").value;
    const dates = D.daily.dates;
    const message = (value) => {
      $("#volume-summary").textContent = value;
      $("#volume-comparison").replaceChildren();
    };
    if (!dates.length) return message("No recorded history is available yet.");
    if (!start || !end || start > end || start < dates[0] || end > dates[dates.length - 1]) {
      return message(`Choose a start date on or before the end date. Both dates must fall between ${longDate(dates[0])} and ${longDate(dates[dates.length - 1])}.`);
    }
    let rows;
    if (start === D.tickers.start && end === D.tickers.end) {
      rows = (D.tickers.comparisons?.[mode] || []).slice();
    } else if (D.volumeHistory) {
      rows = historicalComparison(D.volumeHistory.days, start, end, mode);
    } else {
      message("Loading historical volume…");
      if (!D.loadingVolume) {
        D.loadingVolume = load("volume_history.json").then(history => {
          D.volumeHistory = history;
          renderComparisons();
        }).catch(() => {
          if ($("#volume-start").value !== D.tickers.start || $("#volume-end").value !== D.tickers.end)
            message("Historical data could not load. Change the range to retry.");
        }).finally(() => { D.loadingVolume = null; });
      }
      return;
    }
    rows.sort((a,b) => b.token/b.stock - a.token/a.stock || a.t.localeCompare(b.t));
    const description = mode === "calendar" ? "Each total includes all recorded volume in the selected dates. A ticker needs at least one date with trading on both sides." :
      mode === "matched" ? "Both markets traded on each included date." : "Both markets traded in the selected session on each included date.";
    $("#volume-summary").textContent = `${longDate(start)} to ${longDate(end)}. ${description} ${rows.filter(r => r.token > r.stock).length} of ${rows.length} comparable tickers exceeded 100% over the window.`;
    $("#volume-comparison").innerHTML = "<thead><tr><th>Stock</th><th>Token dollars</th><th>Stock dollars</th><th>Token / stock</th><th>Days above 100% / matched</th></tr></thead><tbody>" +
      rows.slice(0, limit === "all" ? rows.length : Number(limit)).map(r =>
        `<tr><td>${esc(r.t)}</td><td>${usd(r.token)}</td><td>${usd(r.stock)}</td><td>${pct(r.token/r.stock,2)}</td><td>${r.above} / ${r.days}</td></tr>`).join("") +
      (rows.length ? "" : '<tr><td colspan="5">No comparable recorded volume.</td></tr>') + "</tbody>";
  }

  function renderLead() {
    const t = D.tickers;
    $("#lead-window").textContent = `This price comparison uses the latest ${t.window_days} calendar days, ${longDate(t.start)} to ${longDate(t.end)}.`;
    const select = $("#lead-symbol");
    const results = t.lead_lag?.[$("#lead-session").value] || {};
    const names = Object.keys(results).sort();
    const previous = select.value;
    select.replaceChildren(...names.map(name => new Option(name, name)));
    if (names.includes(previous)) select.value = previous;
    else if (names.includes("BNC")) select.value = "BNC";
    const r = results[select.value];
    $("#lead-result").textContent = r ? `${select.value}, ${longDate(t.start)} to ${longDate(t.end)}. Token first: ${fin(r.token_first) ? fix(r.token_first) : "n/a"}. Stock first: ${fin(r.stock_first) ? fix(r.stock_first) : "n/a"}. Based on ${r.n} consecutive half-hour pairs.` :
      "A stock needs 30 comparable half-hour pairs to appear here.";
  }

  function renderAll() {
    chartDefaults();
    renderStamp();
    renderOverview();
    renderLegend();
    renderWeek();
    renderSummary();
    renderDaily();
    renderMix();
    renderVenues();
    renderTickers();
    renderComparisons();
    renderLead();
    renderClosures();
    renderMethod();
    const row = ui.ticker && (D.tickers.rows || []).find((r) => r.t === ui.ticker);
    if (row) renderTickerChart(row);
  }

  function bind() {
    const dates = D.daily.dates;
    const dex = D.meta.sources?.find(source => source.id === "dex");
    if (dates.length) {
      $("#volume-archive").textContent = `Archive: ${longDate(dates[0])} to ${longDate(dates[dates.length - 1])}.` +
        (dex?.first_date ? ` On-chain pool records begin ${longDate(dex.first_date)}.` : "");
    }
    for (const input of [$("#volume-start"), $("#volume-end")]) {
      input.min = dates[0] || "";
      input.max = dates[dates.length - 1] || "";
      input.addEventListener("change", () => {
        $("#volume-range").value = "custom";
        renderComparisons();
      });
    }
    $("#volume-range").addEventListener("change", setComparisonRange);
    setComparisonRange();
    $("#volume-mode").addEventListener("change", renderComparisons);
    $("#volume-limit").addEventListener("change", renderComparisons);
    $("#lead-symbol").addEventListener("change", renderLead);
    $("#lead-session").addEventListener("change", renderLead);
    $$("[data-week]").forEach((b) => b.addEventListener("click", () => { ui.week = b.dataset.week; pressed("week", ui.week); renderWeek(); }));
    $$("[data-daily]").forEach((b) => b.addEventListener("click", () => { ui.daily = b.dataset.daily; pressed("daily", ui.daily); renderDaily(); }));
    $$("[data-range]").forEach((b) => b.addEventListener("click", () => {
      ui.range = b.dataset.range;
      pressed("range", ui.range);
      renderDaily();
      renderVenues();
      const row = ui.ticker && (D.tickers.rows || []).find((r) => r.t === ui.ticker);
      if (row && D.series) renderTickerChart(row);
    }));
    $$("[data-venue]").forEach((b) => b.addEventListener("click", () => { ui.venue = b.dataset.venue; pressed("venue", ui.venue); renderVenues(); }));
    $$("[data-limit]").forEach((b) => b.addEventListener("click", () => {
      ui.tickerLimit = b.dataset.limit;
      pressed("limit", ui.tickerLimit);
      renderTickers();
    }));
    $$("[data-closure-limit]").forEach((b) => b.addEventListener("click", () => {
      ui.closureLimit = b.dataset.closureLimit;
      pressed("closure-limit", ui.closureLimit);
      renderClosures();
    }));
    const table = $("#ticker-table");
    table.addEventListener("click", (e) => {
      const sort = e.target.closest("[data-sort]");
      if (sort) {
        const key = sort.dataset.sort;
        ui.sort = { key, dir: ui.sort.key === key ? -ui.sort.dir : key === "t" ? 1 : -1 };
        renderTickers(key);
        return;
      }
      const tr = e.target.closest("tr[data-t]");
      if (tr) selectTicker(tr.dataset.t);
    });
    table.addEventListener("keydown", (e) => {
      if ((e.key === "Enter" || e.key === " ") && e.target.matches("tr[data-t]")) {
        e.preventDefault();
        selectTicker(e.target.dataset.t);
      }
    });
    $("#ticker-search").addEventListener("input", (e) => {
      ui.query = e.target.value;
      renderTickers();
    });
    matchMedia("(prefers-color-scheme: dark)").addEventListener("change", renderAll);
  }

  async function init() {
    try {
      const [meta, daily, week, tickers, closures] = await Promise.all(
        ["meta.json", "daily.json", "week.json", "tickers.json", "closures.json"].map(load));
      Object.assign(D, { meta, daily, week, tickers, closures });
    } catch (e) {
      showEmpty(e);
      return;
    }
    if (typeof Chart === "undefined") {
      showEmpty(new Error("Chart.js did not load from site/vendor/chart.umd.js."));
      return;
    }
    Chart.register(markers);
    bind();
    renderAll();
  }

  init();
})();
