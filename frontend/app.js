/* -- Trading Dashboard - frontend logic ------------------------------- */
const API = "";
let currentView = "intraday";
let refreshTimer = null;

const DASH = "–", UP = "▲", DN = "▼", X = "✕", GE = "≥", MID = "·";

const VIEWS = {
  intraday:  { title: "Intraday",  sub: "Live momentum on your day-trading watchlist", wl: "intraday" },
  swing:     { title: "Swing",     sub: "Multi-day setups scored by trend, pullback & breakout", wl: "swing" },
  longterm:  { title: "Long-term", sub: "5-framework fundamental ranking", wl: "longterm" },
  sector:    { title: "Sector",    sub: "Sector rotation heat — where money is flowing", wl: null },
  ideas:     { title: "Ideas",     sub: "Top trade ideas in the hottest sectors, by role", wl: null },
  analyzer:  { title: "Analyzer",  sub: "5-Floor institutional scorecard for any ticker", wl: null },
  backtest:  { title: "Backtest",  sub: "How the bullish signal has played out historically", wl: null },
  journal:   { title: "Journal",   sub: "Your IBKR trades, performance & review", wl: null },
  settings:  { title: "Settings",  sub: "Appearance, connections & preferences", wl: null },
};

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const fmt = (n, d = 2) => (n === null || n === undefined || isNaN(n)) ? DASH : Number(n).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
const fmtBig = (n) => {
  if (n === null || n === undefined) return DASH;
  const a = Math.abs(n);
  if (a >= 1e12) return (n / 1e12).toFixed(2) + "T";
  if (a >= 1e9) return (n / 1e9).toFixed(2) + "B";
  if (a >= 1e6) return (n / 1e6).toFixed(2) + "M";
  if (a >= 1e3) return (n / 1e3).toFixed(1) + "K";
  return fmt(n, 0);
};
const sign = (n) => n > 0 ? "pos" : n < 0 ? "neg" : "";
const arrow = (n) => n > 0 ? UP : n < 0 ? DN : "";

async function api(path, opts = {}) {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), opts.timeout || 30000);
  try {
    const r = await fetch(API + path, { ...opts, signal: ctrl.signal });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || r.statusText);
    return await r.json();
  } finally { clearTimeout(t); }
}

function errorState(msg) {
  return `<div class="card"><div class="empty"><div class="big">&#9888;</div>
    <strong>Couldn't load data</strong>
    <p style="margin-top:6px;max-width:420px">${msg || 'The data source did not respond.'}</p>
    <button class="btn primary" style="margin-top:14px" onclick="render(false)">Retry</button></div></div>`;
}

function toast(msg) {
  const t = $("#toast"); t.textContent = msg; t.classList.add("show");
  setTimeout(() => t.classList.remove("show"), 2600);
}

function sparkline(data, color) {
  if (!data || data.length < 2) return "";
  const w = 78, h = 26, p = 2;
  const min = Math.min(...data), max = Math.max(...data), rng = max - min || 1;
  const pts = data.map((v, i) => `${(p + (i / (data.length - 1)) * (w - 2 * p)).toFixed(1)},${(h - p - ((v - min) / rng) * (h - 2 * p)).toFixed(1)}`).join(" ");
  const c = color || (data[data.length - 1] >= data[0] ? "var(--green)" : "var(--red)");
  return `<svg class="spark" viewBox="0 0 ${w} ${h}"><polyline points="${pts}" fill="none" stroke="${c}" stroke-width="1.6" stroke-linejoin="round" stroke-linecap="round"/></svg>`;
}

/* -- theme ------------------------------------------------------------ */
function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  try { localStorage.setItem("theme", theme); } catch (e) {}
  const lbl = $("#theme-label"), ico = $("#theme-ico");
  if (lbl) lbl.textContent = theme === "dark" ? "Light mode" : "Dark mode";
  if (ico) ico.innerHTML = theme === "dark" ? "&#9728;" : "&#9790;";
}
function initTheme() {
  let t = "dark";
  try { t = localStorage.getItem("theme") || "dark"; } catch (e) {}
  applyTheme(t);
  const btn = $("#theme-toggle");
  if (btn) btn.addEventListener("click", () => applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark"));
}

/* -- navigation ------------------------------------------------------- */
$$(".nav-item").forEach(btn => btn.addEventListener("click", () => {
  $$(".nav-item").forEach(b => b.classList.remove("active"));
  btn.classList.add("active");
  switchView(btn.dataset.view);
}));

function switchView(view) {
  currentView = view;
  const v = VIEWS[view];
  $("#view-title").textContent = v.title;
  $("#view-sub").textContent = v.sub;
  $(".watchlist-add").style.display = v.wl ? "flex" : "none";
  render(false);
}

$("#add-btn").addEventListener("click", addSymbol);
$("#symbol-input").addEventListener("keydown", e => { if (e.key === "Enter") addSymbol(); });
$("#refresh-btn").addEventListener("click", () => render(true));

async function addSymbol() {
  const inp = $("#symbol-input"); const sym = inp.value.trim().toUpperCase(); const wl = VIEWS[currentView].wl;
  if (!sym || !wl) return;
  await api(`/api/watchlist/${wl}/add`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ symbol: sym }) });
  inp.value = ""; toast(`${sym} added`); render(false);
}
async function removeSymbol(sym) {
  const wl = VIEWS[currentView].wl; if (!wl) return;
  await api(`/api/watchlist/${wl}/remove`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ symbol: sym }) });
  toast(`${sym} removed`); render(false);
}

async function pollStatus() {
  try {
    const s = await api("/api/status");
    const lo = $("#logout-link"); if (lo) lo.style.display = s.loginRequired ? "block" : "none";
    const el = $("#ibkr-status");
    if (s.ibkrConnected) { el.className = "status-pill on"; el.querySelector(".label").textContent = `IBKR live ${MID} :${s.ibkrPort}`; }
    else { el.className = "status-pill off"; el.querySelector(".label").textContent = "IBKR offline"; }
  } catch (e) {}
}

function loading() { $("#content").innerHTML = `<div class="skeleton"><span class="loader"></span> Loading...</div>`; }

/* background=true -> refresh data in place WITHOUT blanking the page */
async function render(background) {
  if (refreshTimer) { clearTimeout(refreshTimer); refreshTimer = null; }
  const view = currentView;
  if (!background) loading();
  let success = false;
  try {
    if (view === "intraday") await renderIntraday();
    else if (view === "swing") await renderSwing();
    else if (view === "longterm") await renderLongterm();
    else if (view === "sector") await renderSector();
    else if (view === "ideas") await renderIdeas();
    else if (view === "analyzer") renderAnalyzer();
    else if (view === "backtest") renderBacktest();
    else if (view === "journal") await renderJournal();
    else if (view === "settings") renderSettings();
    success = true;
  } catch (e) {
    if (currentView === view && !background) {
      const why = (e && e.name === "AbortError") ? "The request timed out (data source slow or rate-limited). Try again." : (e && e.message) || "Unknown error.";
      $("#content").innerHTML = errorState(why);
    }
    if (background && currentView === view) console.warn("[intraday refresh error]", e?.message || e);
  }
  // Only schedule next refresh if the current one succeeded and we're still on intraday
  if (success && currentView === "intraday") refreshTimer = setTimeout(() => render(true), 15000);
}

async function renderIntraday() {
  const [rows, newsMap] = await Promise.all([api("/api/intraday"), api("/api/news?mode=intraday").catch(() => ({}))]);
  const live = rows.filter(r => r.signal).length;
  const stats = `<div class="stat-row">
      <div class="stat-card"><div class="k">Watchlist</div><div class="v">${rows.length}</div><div class="sub">symbols tracked</div></div>
      <div class="stat-card"><div class="k">Signals firing</div><div class="v pos">${live}</div><div class="sub">above VWAP &amp; SMA20</div></div>
      <div class="stat-card"><div class="k">Avg change</div><div class="v ${sign(avg(rows, 'changePct'))}">${fmt(avg(rows, 'changePct'))}%</div><div class="sub">today, watchlist</div></div>
    </div>`;
  const body = rows.map(r => `<tr>
      <td class="sym">${r.symbol}<span class="rm" onclick="removeSymbol('${r.symbol}')">${X}</span></td>
      <td class="num">${fmt(r.price)}</td>
      <td class="num ${sign(r.changePct)}">${arrow(r.changePct)} ${fmt(r.changePct)}%</td>
      <td class="num">${fmt(r.vwap)}</td><td class="num">${fmt(r.sma20)}</td>
      <td>${r.signal ? `<span class="pill green">${UP} Momentum</span>` : (r.aboveVwap ? '<span class="pill amber">Above VWAP</span>' : `<span class="pill gray">${DASH}</span>`)}</td>
      <td>${sparkline(r.sparkline)}</td>
      <td class="num"><span class="pill gray">${r.source || ''}</span></td></tr>`).join("");
  $("#content").innerHTML = `${stats}<div class="grid-2">
      <div class="card"><div class="card-pad section-head" style="margin:0;padding-bottom:0;"><h2>Momentum board</h2><span class="hint">price &gt; VWAP and &gt; SMA20</span></div>
        <table><thead><tr><th>Symbol</th><th class="num">Price</th><th class="num">Chg%</th><th class="num">VWAP</th><th class="num">SMA20</th><th>Signal</th><th>Trend</th><th class="num">Src</th></tr></thead>
        <tbody>${body || emptyRow(8)}</tbody></table></div>
      ${newsPanel(newsMap)}</div>`;
}

async function renderSwing() {
  const rows = await api("/api/swing");
  const setups = rows.filter(r => r.score >= 50).length;
  const stats = `<div class="stat-row">
      <div class="stat-card"><div class="k">Watchlist</div><div class="v">${rows.length}</div><div class="sub">symbols analysed</div></div>
      <div class="stat-card"><div class="k">Quality setups</div><div class="v pos">${setups}</div><div class="sub">score ${GE} 50</div></div>
      <div class="stat-card"><div class="k">Top score</div><div class="v">${rows[0]?.score ?? DASH}</div><div class="sub">${rows[0]?.symbol || ''}</div></div></div>`;
  const body = rows.map(r => `<tr>
      <td class="sym">${r.symbol}<span class="rm" onclick="removeSymbol('${r.symbol}')">${X}</span></td>
      <td class="num">${fmt(r.price)}</td>
      <td><div class="score"><div class="score-bar"><i style="width:${r.score}%"></i></div><span class="score-val">${r.score}</span></div></td>
      <td>${setupPill(r.setup)}</td>
      <td class="num ${sign(r.ret1m)}">${fmt(r.ret1m)}%</td>
      <td class="num ${sign(r.relStrength)}">${fmt(r.relStrength)}</td>
      <td class="num">${fmt(r.rsi, 0)}</td><td class="num">${fmt(r.atrPct)}%</td>
      <td>${sparkline(r.sparkline)}</td></tr>`).join("");
  $("#content").innerHTML = `${stats}<div class="card">
      <div class="card-pad section-head" style="margin:0;padding-bottom:0;"><h2>Swing setups</h2><span class="hint">ranked by setup quality score</span></div>
      <table><thead><tr><th>Symbol</th><th class="num">Price</th><th>Score</th><th>Setup</th><th class="num">1M %</th><th class="num">vs SPY</th><th class="num">RSI</th><th class="num">ATR%</th><th>Trend</th></tr></thead>
      <tbody>${body || emptyRow(9)}</tbody></table></div>`;
}

function setupPill(setup) {
  if (!setup || setup === "No setup") return '<span class="pill gray">No setup</span>';
  const s = setup.toLowerCase(); let cls = "gray";
  if (s.includes("uptrend") || s.includes("pullback") || s.includes("leads")) cls = "green";
  else if (s.includes("breakout")) cls = "amber"; else if (s.includes("extended")) cls = "red";
  return `<span class="pill ${cls}">${setup}</span>`;
}

function fwBar(v) {
  return `<div class="score"><div class="score-bar"><i style="width:${v || 0}%"></i></div><span class="score-val">${v ?? DASH}</span></div>`;
}

async function renderLongterm() {
  const fund = await api("/api/fundamental").catch(() => ({ _failed: true }));
  const fundFailed = fund && fund._failed;
  const fundRows = Array.isArray(fund) ? fund : [];
  const partialCount = fundRows.filter(r => r.partial).length;
  const avgComposite = avg(fundRows, 'compositeScore');
  const topPick = fundRows[0];
  const stats = `<div class="stat-row">
      <div class="stat-card"><div class="k">Ranked</div><div class="v">${fundRows.length}</div><div class="sub">symbols scored</div></div>
      <div class="stat-card"><div class="k">Avg composite</div><div class="v">${fmt(avgComposite, 0)}</div><div class="sub">0–100 quality</div></div>
      <div class="stat-card"><div class="k">Top pick</div><div class="v">${topPick?.symbol || DASH}</div><div class="sub">${topPick ? fmt(topPick.compositeScore, 0) + ' composite' : ''}</div></div></div>`;
  const fbody = fundRows.map(r => `<tr${r.partial ? ' class="row-partial"' : ''}>
      <td class="sym">${r.symbol}<div style="font-size:11px;color:var(--muted);font-weight:400">${r.name || ''}</div></td>
      <td class="num">${fmt(r.price)}</td>
      <td>${fwBar(r.compositeScore)}</td>
      <td class="num">${r.valuation ?? DASH}</td>
      <td class="num">${r.profitability ?? DASH}</td>
      <td class="num">${r.growth ?? DASH}</td>
      <td class="num">${r.health ?? DASH}</td>
      <td class="num">${r.momentum ?? DASH}</td>
      <td class="num">${fmtBig(r.marketCap)}</td></tr>`).join("");
  // Honest status note instead of a silent blank when the provider is blocked.
  let fundNote = '5 frameworks: valuation ' + MID + ' profitability ' + MID + ' growth ' + MID + ' health ' + MID + ' momentum';
  if (fundFailed) fundNote = '&#9888; Fundamentals provider unavailable (rate-limited). Retry shortly.';
  else if (partialCount) fundNote = `&#9888; ${partialCount} of ${fundRows.length} showing momentum only — fundamentals provider rate-limited`;
  $("#content").innerHTML = `${stats}
    <div class="card"><div class="card-pad section-head" style="margin:0;padding-bottom:0;"><h2>Fundamental ranking</h2><span class="hint">${fundNote}</span></div>
      <table><thead><tr><th>Symbol</th><th class="num">Price</th><th>Composite</th><th class="num">Value</th><th class="num">Profit</th><th class="num">Growth</th><th class="num">Health</th><th class="num">Mom</th><th class="num">Mkt Cap</th></tr></thead>
      <tbody>${fbody || emptyRow(9)}</tbody></table></div>`;
}

function heatBg(h) {
  if (h == null) return "var(--surface-2)";
  const hue = Math.max(0, Math.min(138, (h / 100) * 138)); // 0 red → 138 green
  return `linear-gradient(135deg, hsl(${hue} 60% 30%), hsl(${hue} 55% 36%))`;
}
function chk(v) { return v ? `<span class="pill green">&#10003;</span>` : `<span class="pill gray">${DASH}</span>`; }

async function renderSector() {
  const sectorData = await api("/api/sector").catch(() => ({ sectors: [], _failed: true }));
  const sectors = sectorData.sectors || [];
  const leader = sectors[0], laggard = sectors[sectors.length - 1];
  const hot = sectors.filter(s => (s.heat ?? 0) >= 60).length;
  const stats = `<div class="stat-row">
      <div class="stat-card"><div class="k">SPY benchmark</div><div class="v ${sign(sectorData.benchmark1m)}">${fmt(sectorData.benchmark1m)}%</div><div class="sub">1M ${MID} YTD ${fmt(sectorData.benchmarkYtd)}%</div></div>
      <div class="stat-card"><div class="k">Hottest</div><div class="v pos">${leader?.etf || DASH}</div><div class="sub">${leader ? leader.sector + ' · heat ' + fmt(leader.heat, 0) : ''}</div></div>
      <div class="stat-card"><div class="k">Coldest</div><div class="v neg">${laggard?.etf || DASH}</div><div class="sub">${laggard ? laggard.sector + ' · heat ' + fmt(laggard.heat, 0) : ''}</div></div>
      <div class="stat-card"><div class="k">Running hot</div><div class="v">${hot}</div><div class="sub">heat ${GE} 60 of ${sectors.length}</div></div></div>`;
  const heat = sectors.slice(0, 12).map(s => `<div class="heat-cell sym" data-chart="${s.etf}" style="background:${heatBg(s.heat)}">
      <div class="hs">${s.sector}</div><div class="he">${s.etf} ${MID} #${s.rank}</div>
      <div class="hv">${fmt(s.heat, 0)}</div><div class="hsub">1M ${s.ret1m >= 0 ? '+' : ''}${fmt(s.ret1m)}% ${MID} vs SPY ${fmt(s.rs3m)}</div></div>`).join("");
  const body = sectors.map(s => `<tr>
      <td class="sym" data-chart="${s.etf}">${s.etf}<div style="font-size:11px;color:var(--muted);font-weight:400">${s.sector}</div></td>
      <td>${fwBar(s.heat)}</td>
      <td class="num ${sign(s.ret1m)}">${fmt(s.ret1m)}%</td>
      <td class="num ${sign(s.ret3m)}">${fmt(s.ret3m)}%</td>
      <td class="num ${sign(s.ret6m)}">${fmt(s.ret6m)}%</td>
      <td class="num ${sign(s.rs3m)}">${fmt(s.rs3m)}</td>
      <td>${chk(s.above50ma)}</td><td>${chk(s.above200ma)}</td>
      <td class="num">${fmt(s.volRatio)}</td>
      <td data-hist="${s.etf}"><span class="muted" style="font-size:11px">…</span></td></tr>`).join("");
  $("#content").innerHTML = `${stats}
    <div class="section-head"><h2>Sector heat ${MID} composite momentum</h2><span class="hint">35 sector + thematic ETFs ${MID} hotter = stronger multi-timeframe momentum</span></div>
    <div class="heat-grid" style="margin-bottom:24px">${heat || (sectorData._failed ? errorState('Sector data source did not respond.') : '<div class="empty">No sector data</div>')}</div>
    <div class="card"><div class="card-pad section-head" style="margin:0;padding-bottom:0;"><h2>Rotation table</h2><span class="hint">heat ${MID} returns ${MID} relative strength ${MID} 12-week heat trend</span></div>
      <table><thead><tr><th>ETF</th><th>Heat</th><th class="num">1M</th><th class="num">3M</th><th class="num">6M</th><th class="num">vs SPY</th><th>&gt;50MA</th><th>&gt;200MA</th><th class="num">Vol</th><th>12wk trend</th></tr></thead>
      <tbody>${body || emptyRow(10)}</tbody></table></div>`;
  // lazy-load heat history for the rotation sparklines
  if (sectors.length) api("/api/sector/history").then(h => {
    if (currentView !== "sector") return;
    const hist = h.history || {};
    Object.entries(hist).forEach(([etf, vals]) => {
      const cell = document.querySelector(`[data-hist="${etf}"]`);
      if (!cell) return;
      const clean = (vals || []).filter(v => v != null);
      cell.innerHTML = clean.length >= 2 ? sparkline(clean) : `<span class="muted" style="font-size:11px">${DASH}</span>`;
    });
  }).catch(() => {});
}

/* -- ideas (master ranking) ------------------------------------------- */
function roleBadge(role) {
  const map = { "Leader": "green", "Pure Play": "red", "Picks & Shovels": "amber", "Toll Booth": "green", "Arms Dealer": "amber", "Second Derivative": "gray" };
  return `<span class="pill ${map[role] || "gray"}">${role}</span>`;
}
function stagePill(st) {
  const map = { "Early": "green", "Mid-Run": "amber", "Extended": "red", "Cooling": "red", "Neutral": "gray" };
  return `<span class="pill ${map[st] || "gray"}">${st || DASH}</span>`;
}
function techPill(r) {
  const map = { "Strong Buy": "green", "Buy": "green", "Neutral": "gray", "Sell": "red", "Strong Sell": "red" };
  return r ? `<span class="pill ${map[r] || "gray"}">${r}</span>` : `<span class="pill gray">${DASH}</span>`;
}
async function renderIdeas() {
  const data = await api("/api/ideas").catch(() => ({ sectors: [], _failed: true }));
  const sectors = data.sectors || [];
  const totalIdeas = sectors.reduce((a, s) => a + ((s.ideas && s.ideas.length) || 0), 0);
  const top = sectors[0];
  const stats = `<div class="stat-row">
      <div class="stat-card"><div class="k">Hot sectors</div><div class="v">${sectors.length}</div><div class="sub">scanned for ideas</div></div>
      <div class="stat-card"><div class="k">Ideas surfaced</div><div class="v">${totalIdeas}</div><div class="sub">across all roles</div></div>
      <div class="stat-card"><div class="k">Hottest</div><div class="v pos">${top?.etf || DASH}</div><div class="sub">${top ? top.name + ' · heat ' + fmt(top.heat, 0) : ''}</div></div></div>`;
  if (!sectors.length) {
    $("#content").innerHTML = stats + (data._failed ? errorState('Ideas engine did not respond.') : '<div class="empty">No ideas right now.</div>');
    return;
  }
  const blocks = sectors.map(s => {
    const rows = (s.ideas || []).map(it => `<tr>
      <td class="sym" data-chart="${it.ticker}">${it.ticker}${it.catchup ? ' <span class="pill green" style="font-size:9.5px;padding:1px 6px">Catch-up</span>' : ''}<div style="font-size:11px;color:var(--muted);font-weight:400">$${fmt(it.price)}${it.suggestedStop ? ` ${MID} stop $${fmt(it.suggestedStop)}` : ''}<span data-qual="${it.ticker}"></span></div></td>
      <td>${roleBadge(it.role)}</td>
      <td>${fwBar(it.finalScore)}</td>
      <td>${stagePill(it.runStage)}</td>
      <td class="num">${fmt(it.rsi, 0)}</td>
      <td>${techPill(it.techRating)}</td>
      <td class="num ${sign(it.roc4w)}">${fmt(it.roc4w)}%</td>
      <td class="num ${sign(it.rs4w)}">${fmt(it.rs4w)}</td>
      <td>${sparkline(it.sparkline)}</td></tr>`).join("");
    return `<div class="card" style="margin-bottom:18px"><div class="card-pad section-head" style="margin:0;padding-bottom:0;">
        <h2><span class="sym" data-chart="${s.etf}">${s.etf}</span> ${MID} ${s.name}</h2><span class="hint">#${s.rank} hottest ${MID} heat ${fmt(s.heat, 0)}</span></div>
      <table><thead><tr><th>Ticker</th><th>Role</th><th>Score</th><th>Stage</th><th class="num">RSI</th><th>Tech</th><th class="num">4w %</th><th class="num">vs SPY</th><th>Trend</th></tr></thead>
      <tbody>${rows || emptyRow(9)}</tbody></table></div>`;
  }).join("");
  $("#content").innerHTML = `${stats}<p class="muted" style="margin:0 2px 16px">Ideas grouped by the hottest sectors, each tagged by how it plays the theme. Analytical roles, not buy recommendations.</p>${blocks}`;
  // lazy fundamental quality (5-framework composite) for the listed tickers
  const tks = [...new Set(sectors.flatMap(s => (s.ideas || []).map(i => i.ticker)))];
  if (tks.length) api("/api/ideas/quality?tickers=" + encodeURIComponent(tks.join(","))).then(q => {
    if (currentView !== "ideas") return;
    Object.entries(q || {}).forEach(([tk, v]) => {
      document.querySelectorAll(`[data-qual="${tk}"]`).forEach(el => {
        if (v.quality == null) { el.textContent = ""; return; }
        const c = v.quality >= 60 ? "pos" : v.quality < 40 ? "neg" : "";
        el.innerHTML = ` ${MID} <span class="${c}" title="Fundamental quality (0-100)">Q ${Math.round(v.quality)}${v.partial ? "*" : ""}</span>`;
      });
    });
  }).catch(() => {});
}

/* -- analyzer (5-Floor scorecard) ------------------------------------- */
let _analyzerSym = "";
function renderAnalyzer() {
  const sym = _analyzerSym || "";
  $("#content").innerHTML = `
    <div class="analyzer-bar">
      <input id="az-input" type="text" placeholder="Enter a ticker (e.g. NVDA)…" value="${sym}" maxlength="8" autocomplete="off">
      <button id="az-go" class="btn primary">Analyze</button>
    </div>
    <div id="az-result">${sym ? "" : '<div class="empty"><div class="big">&#128300;</div>Enter a ticker to run the 5-Floor scorecard.</div>'}</div>`;
  const go = async () => {
    const t = $("#az-input").value.trim().toUpperCase();
    if (!t) return;
    _analyzerSym = t;
    $("#az-result").innerHTML = `<div class="skeleton"><span class="loader"></span> Analyzing ${t}…</div>`;
    let d; try { d = await api("/api/analyze/" + encodeURIComponent(t), { timeout: 30000 }); }
    catch (e) { $("#az-result").innerHTML = errorState("Couldn't analyze " + t + "."); return; }
    if (currentView === "analyzer") $("#az-result").innerHTML = analyzerCard(d);
  };
  $("#az-go").addEventListener("click", go);
  $("#az-input").addEventListener("keydown", e => { if (e.key === "Enter") go(); });
  if (sym) go();
}
function analyzerCard(d) {
  if (d.error) return `<div class="empty"><div class="big">&#9888;</div>${d.error} for ${d.symbol}.</div>`;
  const header = `<div class="az-head">
      <div><span class="az-sym sym" data-chart="${d.symbol}">${d.symbol}</span> <span class="muted">$${fmt(d.price)}</span></div>
      <div class="az-verdict">
        <span class="pill ${d.verdictColor}">${d.verdict}</span>
        <div class="az-comp"><div class="score-bar" style="width:120px"><i style="width:${d.composite || 0}%"></i></div><span class="score-val">${d.composite ?? DASH}</span></div>
        <span class="muted" style="font-size:12px">${d.greenFloors}/${d.availFloors} floors green</span>
      </div></div>`;
  const floors = (d.floors || []).map(f => {
    const sigs = f.signals.map(s => `<div class="az-sig"><span class="dot ${s.color}"></span><div><div class="az-sig-name">${s.name}</div><div class="az-sig-det">${s.detail}</div></div></div>`).join("");
    const sc = f.score == null ? `<span class="pill gray">N/A</span>` : `<span class="pill ${f.color}">${f.score}/5</span>`;
    return `<div class="card az-floor"><div class="card-pad">
        <div class="az-floor-head"><h3>${f.key} ${MID} ${f.name}</h3>${sc}</div>
        <div class="az-sigs">${sigs}</div></div></div>`;
  }).join("");
  return header + `<div class="az-grid">${floors}</div>
    <p class="muted" style="margin-top:14px">Deterministic 5-Floor scorecard from price &amp; volume. F3 (options) needs a live options feed, so it shows N/A on the cloud. Analytical only, not advice.</p>`;
}

/* -- backtest --------------------------------------------------------- */
let _backtestSym = "";
function renderBacktest() {
  const sym = _backtestSym || "";
  $("#content").innerHTML = `
    <div class="analyzer-bar">
      <input id="bt-input" type="text" placeholder="Enter a ticker (e.g. AAPL)…" value="${sym}" maxlength="8" autocomplete="off">
      <button id="bt-go" class="btn primary">Backtest</button>
    </div>
    <div id="bt-result">${sym ? "" : '<div class="empty"><div class="big">&#128202;</div>Enter a ticker to backtest the bullish signal over 5 years.</div>'}</div>`;
  const go = async () => {
    const t = $("#bt-input").value.trim().toUpperCase();
    if (!t) return;
    _backtestSym = t;
    $("#bt-result").innerHTML = `<div class="skeleton"><span class="loader"></span> Backtesting ${t}…</div>`;
    let d; try { d = await api("/api/backtest/" + encodeURIComponent(t), { timeout: 30000 }); }
    catch (e) { $("#bt-result").innerHTML = errorState("Couldn't backtest " + t + "."); return; }
    if (currentView === "backtest") $("#bt-result").innerHTML = backtestCard(d);
  };
  $("#bt-go").addEventListener("click", go);
  $("#bt-input").addEventListener("keydown", e => { if (e.key === "Enter") go(); });
  if (sym) go();
}
function backtestCard(d) {
  if (d.error) return `<div class="empty"><div class="big">&#9888;</div>${d.error} for ${d.symbol}.</div>`;
  const h10 = d.horizons.find(h => h.days === 10) || d.horizons[0] || {};
  const stats = `<div class="stat-row">
      <div class="stat-card"><div class="k">Signals fired</div><div class="v">${d.totalSignals}</div><div class="sub">over ${d.years}y</div></div>
      <div class="stat-card"><div class="k">Win rate (10d)</div><div class="v ${h10.winRate >= 50 ? 'pos' : 'neg'}">${fmt(h10.winRate, 0)}%</div><div class="sub">${h10.signals} samples</div></div>
      <div class="stat-card"><div class="k">Avg return (10d)</div><div class="v ${sign(h10.avgReturn)}">${fmt(h10.avgReturn)}%</div><div class="sub">per signal</div></div>
      <div class="stat-card"><div class="k">Edge vs hold</div><div class="v ${sign(h10.edge)}">${h10.edge >= 0 ? '+' : ''}${fmt(h10.edge)}%</div><div class="sub">vs buy &amp; hold 10d</div></div></div>`;
  const hz = d.horizons.map(h => h.signals ? `<div class="card"><div class="card-pad">
      <div class="az-floor-head"><h3>${h.days}-day forward</h3><span class="pill ${h.winRate >= 55 ? 'green' : h.winRate >= 45 ? 'gray' : 'red'}">${fmt(h.winRate, 0)}% win</span></div>
      <div class="bt-rows">
        <div><span class="muted">Avg return</span> <b class="${sign(h.avgReturn)}">${fmt(h.avgReturn)}%</b></div>
        <div><span class="muted">Avg win</span> <b class="pos">${fmt(h.avgWin)}%</b></div>
        <div><span class="muted">Avg loss</span> <b class="neg">${fmt(h.avgLoss)}%</b></div>
        <div><span class="muted">Edge vs hold</span> <b class="${sign(h.edge)}">${h.edge >= 0 ? '+' : ''}${fmt(h.edge)}%</b></div>
        <div><span class="muted">Best / worst</span> <b>${fmt(h.best)}% / ${fmt(h.worst)}%</b></div>
      </div></div></div>` : `<div class="card"><div class="card-pad"><h3>${h.days}-day</h3><div class="muted" style="margin-top:8px">No signals in range</div></div></div>`).join("");
  let eq = "";
  if (d.equity && d.equity.length > 1) {
    const data = d.equity, w = 600, hh = 90, p = 3;
    const mn = Math.min(...data), mx = Math.max(...data), rng = mx - mn || 1;
    const pts = data.map((v, i) => `${(p + i / (data.length - 1) * (w - 2 * p)).toFixed(1)},${(hh - p - (v - mn) / rng * (hh - 2 * p)).toFixed(1)}`).join(" ");
    const col = data[data.length - 1] >= data[0] ? "var(--green)" : "var(--red)";
    eq = `<div class="card" style="margin-top:16px"><div class="card-pad"><div class="section-head" style="margin:0 0 10px"><h2>Signal equity curve</h2><span class="hint">$100 compounded across every signal (${d.equityHorizon}-day holds) ${MID} ended $${fmt(data[data.length - 1], 0)}</span></div>
      <svg viewBox="0 0 ${w} ${hh}" preserveAspectRatio="none" style="width:100%;height:90px"><polyline points="${pts}" fill="none" stroke="${col}" stroke-width="2"/></svg></div></div>`;
  }
  const sigRows = (d.signals || []).map(s => `<tr><td>${s.date}</td><td class="num">$${fmt(s.price)}</td><td class="num ${sign(s.fwd)}">${s.fwd == null ? DASH : fmt(s.fwd) + '%'}</td></tr>`).join("");
  const sigTable = sigRows ? `<div class="card" style="margin-top:16px"><div class="card-pad section-head" style="margin:0;padding-bottom:0;"><h2>Recent signals</h2><span class="hint">${d.equityHorizon}-day forward outcome</span></div>
      <table><thead><tr><th>Date</th><th class="num">Price</th><th class="num">Fwd ${d.equityHorizon}d</th></tr></thead><tbody>${sigRows}</tbody></table></div>` : "";
  return `<div class="az-head"><div><span class="az-sym sym" data-chart="${d.symbol}">${d.symbol}</span> <span class="muted">$${fmt(d.price)}</span></div></div>
    <p class="muted" style="margin:-6px 0 16px">Signal: <strong>${d.signalDesc}</strong></p>
    ${stats}<div class="az-grid">${hz}</div>${eq}${sigTable}
    <p class="muted" style="margin-top:14px">Historical, reconstructed from price only. Overlapping signals aren't de-duplicated and past performance isn't predictive. Analytical only, not advice.</p>`;
}

/* -- settings --------------------------------------------------------- */
const ACCENTS = [
  { name: "Blue", v: "#4f7cff" }, { name: "Violet", v: "#7b6bff" },
  { name: "Green", v: "#1fd286" }, { name: "Amber", v: "#f5b13d" },
  { name: "Red", v: "#ff5468" }, { name: "Cyan", v: "#22b8cf" },
];
const FONT_SIZES = [{ name: "Compact", v: "sm" }, { name: "Default", v: "md" }, { name: "Large", v: "lg" }];

function getPref(k, d) { try { return localStorage.getItem(k) || d; } catch (e) { return d; } }
function setPref(k, v) { try { localStorage.setItem(k, v); } catch (e) {} }

function applyAccent(color) {
  document.documentElement.style.setProperty("--brand", color);
  document.documentElement.style.setProperty("--brand-soft", color + "22");
  setPref("accent", color);
}
function applyFontSize(size) {
  document.documentElement.dataset.fs = size;
  setPref("fontSize", size);
}
function initAppearance() {
  applyAccent(getPref("accent", "#4f7cff"));
  applyFontSize(getPref("fontSize", "md"));
}

function renderSettings() {
  const theme = document.documentElement.dataset.theme || "dark";
  const accent = getPref("accent", "#4f7cff");
  const fs = getPref("fontSize", "md");
  const seg = (opts, cur, fn, key) => opts.map(o =>
    `<button class="seg ${(o.v === cur || o.v === key) ? 'on' : ''}" data-${fn}="${o.v}">${o.name}</button>`).join("");
  $("#content").innerHTML = `
    <div class="settings-wrap">
      <div class="card setting-card">
        <div class="card-pad">
          <h2 class="set-h">Appearance</h2>
          <div class="setting-row"><div><div class="set-label">Theme</div><div class="set-desc">Dark or light interface</div></div>
            <div class="seg-group" id="seg-theme">${seg([{name:'Dark',v:'dark'},{name:'Light',v:'light'}], theme, 'theme')}</div></div>
          <div class="setting-row"><div><div class="set-label">Font size</div><div class="set-desc">Text density across tables and cards</div></div>
            <div class="seg-group" id="seg-fs">${seg(FONT_SIZES, fs, 'fs')}</div></div>
          <div class="setting-row"><div><div class="set-label">Accent color</div><div class="set-desc">Highlights, buttons and charts</div></div>
            <div class="swatches" id="swatches">${ACCENTS.map(a => `<button class="swatch ${a.v === accent ? 'on' : ''}" style="background:${a.v}" data-accent="${a.v}" title="${a.name}"></button>`).join("")}</div></div>
        </div>
      </div>
      <div class="card setting-card">
        <div class="card-pad">
          <h2 class="set-h">Connections</h2>
          <div class="setting-row">
            <div><div class="set-label">Interactive Brokers</div><div class="set-desc" id="ibkr-desc">Checking…</div></div>
            <div style="display:flex;gap:8px;align-items:center">
              <span id="ibkr-pill" class="pill gray">…</span>
              <button id="ibkr-connect" class="btn soft" style="display:none">Connect</button>
              <button id="ibkr-disconnect" class="btn soft" style="display:none">Disconnect</button>
            </div>
          </div>
          <div class="set-desc" id="ibkr-help" style="margin-top:8px;line-height:1.65"></div>
        </div>
      </div>
    </div>`;
  // wire appearance controls
  $("#seg-theme").addEventListener("click", e => { const v = e.target.dataset.theme; if (v) { applyTheme(v); renderSettings(); } });
  $("#seg-fs").addEventListener("click", e => { const v = e.target.dataset.fs; if (v) { applyFontSize(v); renderSettings(); } });
  $("#swatches").addEventListener("click", e => { const v = e.target.dataset.accent; if (v) { applyAccent(v); renderSettings(); } });
  // IBKR connection
  async function refreshIbkr() {
    const pill = $("#ibkr-pill"), desc = $("#ibkr-desc"), help = $("#ibkr-help"),
          cBtn = $("#ibkr-connect"), dBtn = $("#ibkr-disconnect");
    if (!pill) return;
    let s; try { s = await api("/api/ibkr/status"); } catch (e) { return; }
    const last = s.lastData ? new Date(s.lastData).toLocaleTimeString() : null;
    if (s.connected) {
      pill.className = "pill green";
      pill.textContent = s.mode === "local" ? "Connected" : "Live (bridge)";
      desc.textContent = (s.mode === "local" ? `Direct to IB Gateway on port ${s.port}` : "Streaming from your desktop bridge") + (last ? ` ${MID} last data ${last}` : "");
      cBtn.style.display = "none";
      dBtn.style.display = s.mode === "local" ? "inline-block" : "none";
      help.innerHTML = "";
    } else {
      pill.className = "pill gray"; pill.textContent = "Offline";
      desc.textContent = "No live IBKR feed";
      cBtn.style.display = "inline-block"; dBtn.style.display = "none";
      help.innerHTML = `IB Gateway runs on your PC, so live data needs one of these:<br>&bull; <strong>Running this app on your trading PC?</strong> Open IB Gateway &amp; log in, then click <strong>Connect</strong> (port ${s.port} ${MID} 4001 live / 4002 paper).<br>&bull; <strong>On the shared cloud site?</strong> The cloud can't reach your Gateway ${MID} run <code>bridge\\run_bridge.bat</code> on your PC to stream data up.`;
    }
  }
  const _ic = $("#ibkr-connect"), _id = $("#ibkr-disconnect");
  if (_ic) _ic.addEventListener("click", async () => {
    _ic.textContent = "Connecting…"; _ic.disabled = true;
    const s = await api("/api/ibkr/connect", { method: "POST", timeout: 20000 }).catch(() => ({}));
    _ic.textContent = "Connect"; _ic.disabled = false;
    if (!s.connected) toast("No local IB Gateway found — see the note below");
    refreshIbkr();
  });
  if (_id) _id.addEventListener("click", async () => {
    await api("/api/ibkr/disconnect", { method: "POST" }).catch(() => {});
    refreshIbkr();
  });
  refreshIbkr();
}

async function renderJournal() {
  const j = await api("/api/journal"); const s = j.summary;
  const stats = `<div class="stat-row">
      <div class="stat-card"><div class="k">Net P&amp;L</div><div class="v ${sign(s.netPnL)}">$${fmt(s.netPnL)}</div><div class="sub">${s.totalTrades} closed trades</div></div>
      <div class="stat-card"><div class="k">Win rate</div><div class="v">${fmt(s.winRate, 1)}%</div><div class="sub">${s.wins}W / ${s.losses}L</div></div>
      <div class="stat-card"><div class="k">Profit factor</div><div class="v">${s.profitFactor ?? DASH}</div><div class="sub">gross win / loss</div></div>
      <div class="stat-card"><div class="k">Expectancy</div><div class="v ${sign(s.expectancy)}">$${fmt(s.expectancy)}</div><div class="sub">per trade</div></div>
      <div class="stat-card"><div class="k">Avg R:R</div><div class="v">${s.avgRR ?? DASH}</div><div class="sub">avg win / avg loss</div></div></div>`;
  const toolbar = `<div class="toolbar">
      <button class="btn primary" id="sync-ibkr">&#8595; Sync IBKR trades</button>
      <label class="btn soft" style="cursor:pointer">&#8593; Import CSV<input type="file" id="csv-file" accept=".csv" hidden></label>
      <span class="hint">IBKR Flex / Activity export, or any trades CSV</span></div>`;
  const eq = j.equityCurve && j.equityCurve.length ? `<div class="card equity-wrap"><div class="section-head" style="margin:0 0 10px"><h2>Equity curve</h2><span class="hint">cumulative realised P&amp;L</span></div>${equityChart(j.equityCurve)}</div>` : "";
  const tbody = (j.trades || []).map(t => `<tr>
      <td class="sym">${t.symbol}</td><td><span class="pill ${t.direction === 'LONG' ? 'green' : 'amber'}">${t.direction}</span></td>
      <td class="num">${fmt(t.shares, 0)}</td><td class="num">${fmt(t.entryPrice)}</td><td class="num">${fmt(t.exitPrice)}</td>
      <td class="num ${sign(t.pnl)}">$${fmt(t.pnl)}</td><td class="num ${sign(t.pnlPct)}">${fmt(t.pnlPct)}%</td>
      <td style="font-size:11px;color:var(--muted)">${(t.exitTime || '').slice(0, 16)}</td></tr>`).join("");
  const main = (j.trades && j.trades.length)
    ? `<div class="card"><div class="card-pad section-head" style="margin:0;padding-bottom:0;"><h2>Closed trades</h2><span class="hint">FIFO-matched round trips</span></div>
         <table><thead><tr><th>Symbol</th><th>Dir</th><th class="num">Qty</th><th class="num">Entry</th><th class="num">Exit</th><th class="num">P&amp;L</th><th class="num">%</th><th>Closed</th></tr></thead><tbody>${tbody}</tbody></table></div>`
    : `<div class="card"><div class="empty"><div class="big">&#128211;</div><strong>No trades yet</strong><p style="margin-top:6px">Sync from IBKR or import a CSV to populate your journal.</p></div></div>`;
  $("#content").innerHTML = `${stats}${toolbar}${eq}${main}`;
  $("#sync-ibkr").addEventListener("click", async () => {
    try { const r = await api("/api/journal/sync-ibkr", { method: "POST" }); toast(`Synced ${r.added} new fills`); render(false); }
    catch (e) { toast("IBKR not connected"); }
  });
  $("#csv-file").addEventListener("change", async (e) => {
    const f = e.target.files[0]; if (!f) return; const fd = new FormData(); fd.append("file", f);
    try { const r = await api("/api/journal/import-csv", { method: "POST", body: fd }); toast(r.error ? r.error : `Imported ${r.added} fills`); render(false); }
    catch (err) { toast("Import failed"); }
  });
}

function equityChart(data) {
  const w = 800, h = 120, p = 4;
  const min = Math.min(0, ...data), max = Math.max(0, ...data), rng = (max - min) || 1;
  const pts = data.map((v, i) => `${(p + (i / Math.max(1, data.length - 1)) * (w - 2 * p)).toFixed(1)},${(h - p - ((v - min) / rng) * (h - 2 * p)).toFixed(1)}`);
  const col = data[data.length - 1] >= 0 ? "var(--green)" : "var(--red)";
  return `<svg class="equity" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none"><polygon points="${p},${h - p} ${pts.join(" ")} ${w - p},${h - p}" fill="${col}" opacity="0.1"/><polyline points="${pts.join(" ")}" fill="none" stroke="${col}" stroke-width="2"/></svg>`;
}

function newsPanel(map) {
  const items = [];
  Object.entries(map || {}).forEach(([sym, list]) => (list || []).forEach(n => items.push({ sym, ...n })));
  const rows = items.slice(0, 18).map(n => `<div class="news-item"><a href="${n.link}" target="_blank" rel="noopener">${n.title}</a>
      <div class="news-meta"><span class="news-sym">${n.sym}</span><span class="tag ${n.sentiment}">${n.sentiment}</span><span>${n.source || ''}</span></div></div>`).join("");
  return `<div class="card"><div class="card-pad section-head" style="margin:0;padding-bottom:6px;"><h2>Watchlist news</h2><span class="hint">RSS</span></div>
    <div class="news-list">${rows || '<div class="empty" style="padding:30px">No headlines yet</div>'}</div></div>`;
}

function avg(rows, key) {
  const vals = rows.map(r => r[key]).filter(v => v !== null && v !== undefined && !isNaN(v));
  return vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : 0;
}
function emptyRow(cols) { return `<tr><td colspan="${cols || 1}"><div class="empty"><div class="big">&#128269;</div>No data ${DASH} add a ticker to your watchlist to begin.</div></td></tr>`; }

/* -- boot ------------------------------------------------------------- */
initTheme();
initAppearance();
pollStatus();
setInterval(pollStatus, 10000);
switchView("intraday");
