/* -- Trading Dashboard - frontend logic ------------------------------- */
const API = "";  // same origin
let currentView = "intraday";
let refreshTimer = null;

const DASH = "–";
const UP = "▲";
const DN = "▼";
const X = "✕";
const GE = "≥";
const MID = "·";

const VIEWS = {
  intraday:  { title: "Intraday",  sub: "Live momentum on your day-trading watchlist", wl: "intraday" },
  swing:     { title: "Swing",     sub: "Multi-day setups scored by trend, pullback & breakout", wl: "swing" },
  longterm:  { title: "Long-term", sub: "Sector rotation + fundamental quality ranking", wl: "longterm" },
  journal:   { title: "Journal",   sub: "Your IBKR trades, performance & review", wl: null },
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
  } finally {
    clearTimeout(t);
  }
}

function errorState(msg) {
  return `<div class="card"><div class="empty"><div class="big">&#9888;</div>
    <strong>Couldn't load data</strong>
    <p style="margin-top:6px;max-width:420px">${msg || 'The data source did not respond.'}</p>
    <button class="btn primary" style="margin-top:14px" onclick="render(true)">Retry</button>
  </div></div>`;
}

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg; t.classList.add("show");
  setTimeout(() => t.classList.remove("show"), 2600);
}

function sparkline(data, color) {
  if (!data || data.length < 2) return "";
  const w = 78, h = 26, p = 2;
  const min = Math.min(...data), max = Math.max(...data), rng = max - min || 1;
  const pts = data.map((v, i) => {
    const x = p + (i / (data.length - 1)) * (w - 2 * p);
    const y = h - p - ((v - min) / rng) * (h - 2 * p);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
  const up = data[data.length - 1] >= data[0];
  const c = color || (up ? "var(--green)" : "var(--red)");
  return `<svg class="spark" viewBox="0 0 ${w} ${h}"><polyline points="${pts}" fill="none" stroke="${c}" stroke-width="1.6" stroke-linejoin="round" stroke-linecap="round"/></svg>`;
}

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
  render();
}

$("#add-btn").addEventListener("click", addSymbol);
$("#symbol-input").addEventListener("keydown", e => { if (e.key === "Enter") addSymbol(); });
$("#refresh-btn").addEventListener("click", () => render(true));

async function addSymbol() {
  const inp = $("#symbol-input");
  const sym = inp.value.trim().toUpperCase();
  const wl = VIEWS[currentView].wl;
  if (!sym || !wl) return;
  await api(`/api/watchlist/${wl}/add`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ symbol: sym }) });
  inp.value = "";
  toast(`${sym} added`);
  render(true);
}

async function removeSymbol(sym) {
  const wl = VIEWS[currentView].wl;
  if (!wl) return;
  await api(`/api/watchlist/${wl}/remove`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ symbol: sym }) });
  toast(`${sym} removed`);
  render(true);
}

async function pollStatus() {
  try {
    const s = await api("/api/status");
    const lo = $("#logout-link");
    if (lo) lo.style.display = s.loginRequired ? "block" : "none";
    const el = $("#ibkr-status");
    if (s.ibkrConnected) {
      el.className = "status-pill on";
      el.querySelector(".label").textContent = `IBKR live ${MID} :${s.ibkrPort}`;
    } else {
      el.className = "status-pill off";
      el.querySelector(".label").textContent = "IBKR offline";
    }
  } catch (e) {}
}

function loading() { $("#content").innerHTML = `<div class="skeleton"><span class="loader"></span> Loading...</div>`; }

async function render(forced) {
  if (refreshTimer) { clearTimeout(refreshTimer); refreshTimer = null; }
  const view = currentView;
  try {
    if (view === "intraday") await renderIntraday();
    else if (view === "swing") await renderSwing();
    else if (view === "longterm") await renderLongterm();
    else if (view === "journal") await renderJournal();
  } catch (e) {
    if (currentView === view) {
      const why = (e && e.name === "AbortError")
        ? "The request timed out (the data source may be slow or rate-limited). Try again."
        : (e && e.message) || "Unknown error.";
      $("#content").innerHTML = errorState(why);
    }
  }
  if (currentView === "intraday") refreshTimer = setTimeout(() => render(), 15000);
}

async function renderIntraday() {
  loading();
  const [rows, newsMap] = await Promise.all([api("/api/intraday"), api("/api/news?mode=intraday").catch(() => ({}))]);
  const live = rows.filter(r => r.signal).length;
  const stats = `
    <div class="stat-row">
      <div class="stat-card"><div class="k">Watchlist</div><div class="v">${rows.length}</div><div class="sub">symbols tracked</div></div>
      <div class="stat-card"><div class="k">Signals firing</div><div class="v pos">${live}</div><div class="sub">above VWAP &amp; SMA20</div></div>
      <div class="stat-card"><div class="k">Avg change</div><div class="v ${sign(avg(rows, 'changePct'))}">${fmt(avg(rows, 'changePct'))}%</div><div class="sub">today, watchlist</div></div>
    </div>`;
  const body = rows.map(r => `
    <tr>
      <td class="sym">${r.symbol}<span class="rm" onclick="removeSymbol('${r.symbol}')">${X}</span></td>
      <td class="num">${fmt(r.price)}</td>
      <td class="num ${sign(r.changePct)}">${arrow(r.changePct)} ${fmt(r.changePct)}%</td>
      <td class="num">${fmt(r.vwap)}</td>
      <td class="num">${fmt(r.sma20)}</td>
      <td>${r.signal ? `<span class="pill green">${UP} Momentum</span>` : (r.aboveVwap ? '<span class="pill amber">Above VWAP</span>' : `<span class="pill gray">${DASH}</span>`)}</td>
      <td>${sparkline(r.sparkline)}</td>
      <td class="num"><span class="pill gray">${r.source || ''}</span></td>
    </tr>`).join("");
  $("#content").innerHTML = `
    ${stats}
    <div class="grid-2">
      <div class="card">
        <div class="card-pad section-head" style="margin:0;padding-bottom:0;"><h2>Momentum board</h2><span class="hint">price &gt; VWAP and &gt; SMA20</span></div>
        <table>
          <thead><tr><th>Symbol</th><th class="num">Price</th><th class="num">Chg%</th><th class="num">VWAP</th><th class="num">SMA20</th><th>Signal</th><th>Trend</th><th class="num">Src</th></tr></thead>
          <tbody>${body || emptyRow(8)}</tbody>
        </table>
      </div>
      ${newsPanel(newsMap)}
    </div>`;
}

async function renderSwing() {
  loading();
  const rows = await api("/api/swing");
  const setups = rows.filter(r => r.score >= 50).length;
  const stats = `
    <div class="stat-row">
      <div class="stat-card"><div class="k">Watchlist</div><div class="v">${rows.length}</div><div class="sub">symbols analysed</div></div>
      <div class="stat-card"><div class="k">Quality setups</div><div class="v pos">${setups}</div><div class="sub">score ${GE} 50</div></div>
      <div class="stat-card"><div class="k">Top score</div><div class="v">${rows[0]?.score ?? DASH}</div><div class="sub">${rows[0]?.symbol || ''}</div></div>
    </div>`;
  const body = rows.map(r => `
    <tr>
      <td class="sym">${r.symbol}<span class="rm" onclick="removeSymbol('${r.symbol}')">${X}</span></td>
      <td class="num">${fmt(r.price)}</td>
      <td><div class="score"><div class="score-bar"><i style="width:${r.score}%"></i></div><span class="score-val">${r.score}</span></div></td>
      <td>${setupPill(r.setup)}</td>
      <td class="num ${sign(r.ret1m)}">${fmt(r.ret1m)}%</td>
      <td class="num ${sign(r.relStrength)}">${fmt(r.relStrength)}</td>
      <td class="num">${fmt(r.rsi, 0)}</td>
      <td class="num">${fmt(r.atrPct)}%</td>
      <td>${sparkline(r.sparkline)}</td>
    </tr>`).join("");
  $("#content").innerHTML = `
    ${stats}
    <div class="card">
      <div class="card-pad section-head" style="margin:0;padding-bottom:0;"><h2>Swing setups</h2><span class="hint">ranked by setup quality score</span></div>
      <table>
        <thead><tr><th>Symbol</th><th class="num">Price</th><th>Score</th><th>Setup</th><th class="num">1M %</th><th class="num">vs SPY</th><th class="num">RSI</th><th class="num">ATR%</th><th>Trend</th></tr></thead>
        <tbody>${body || emptyRow(9)}</tbody>
      </table>
    </div>`;
}

function setupPill(setup) {
  if (!setup || setup === "No setup") return '<span class="pill gray">No setup</span>';
  const s = setup.toLowerCase();
  let cls = "gray";
  if (s.includes("uptrend") || s.includes("pullback") || s.includes("leads")) cls = "green";
  else if (s.includes("breakout")) cls = "amber";
  else if (s.includes("extended")) cls = "red";
  return `<span class="pill ${cls}">${setup}</span>`;
}

async function renderLongterm() {
  loading();
  const [sectorData, fund] = await Promise.all([api("/api/sector"), api("/api/fundamental")]);
  const sectors = sectorData.sectors || [];
  const heat = sectors.map(s => {
    const v = s.ret1m ?? 0;
    const t = Math.max(-8, Math.min(8, v)) / 8;
    const bg = v >= 0
      ? `linear-gradient(135deg, hsl(155 65% ${42 - t * 14}%), hsl(155 60% ${48 - t * 12}%))`
      : `linear-gradient(135deg, hsl(4 70% ${56 + t * 12}%), hsl(4 65% ${50 + t * 10}%))`;
    return `<div class="heat-cell" style="background:${bg}">
      <div class="hs">${s.sector}</div><div class="he">${s.etf} ${MID} #${s.rank}</div>
      <div class="hv">${v >= 0 ? '+' : ''}${fmt(v)}%</div>
      <div class="hsub">1W ${fmt(s.ret1w)}% ${MID} 3M ${fmt(s.ret3m)}%</div>
    </div>`;
  }).join("");
  const fbody = fund.map(r => `
    <tr>
      <td class="sym">${r.symbol}<span class="rm" onclick="removeSymbol('${r.symbol}')">${X}</span><div style="font-size:11px;color:var(--muted);font-weight:400">${r.name || ''}</div></td>
      <td><div class="score"><div class="score-bar"><i style="width:${r.compositeScore || 0}%"></i></div><span class="score-val">${r.compositeScore ?? DASH}</span></div></td>
      <td class="num">${r.qualityScore ?? DASH}</td>
      <td class="num">${r.growthScore ?? DASH}</td>
      <td class="num">${r.valueScore ?? DASH}</td>
      <td class="num">${fmt(r.trailingPE)}</td>
      <td class="num ${sign(r.revenueGrowth)}">${fmt(r.revenueGrowth)}%</td>
      <td class="num ${sign(r.profitMargins)}">${fmt(r.profitMargins)}%</td>
      <td class="num">${fmtBig(r.marketCap)}</td>
      <td>${recPill(r.recommendation)}</td>
    </tr>`).join("");
  $("#content").innerHTML = `
    <div class="section-head"><h2>Sector heat ${MID} 1-month momentum</h2><span class="hint">SPY ${fmt(sectorData.benchmark1m)}% ${MID} green = leading</span></div>
    <div class="heat-grid" style="margin-bottom:26px">${heat || '<div class="empty">No sector data</div>'}</div>
    <div class="card">
      <div class="card-pad section-head" style="margin:0;padding-bottom:0;"><h2>Fundamental ranking</h2><span class="hint">quality ${MID} growth ${MID} value composite</span></div>
      <table>
        <thead><tr><th>Symbol</th><th>Composite</th><th class="num">Qual</th><th class="num">Grow</th><th class="num">Val</th><th class="num">P/E</th><th class="num">Rev Gr</th><th class="num">Margin</th><th class="num">Mkt Cap</th><th>Analysts</th></tr></thead>
        <tbody>${fbody || emptyRow(10)}</tbody>
      </table>
    </div>`;
}

function recPill(r) {
  if (!r) return `<span class="pill gray">${DASH}</span>`;
  const s = r.toLowerCase();
  if (s.includes("buy")) return `<span class="pill green">${r}</span>`;
  if (s.includes("sell") || s.includes("under")) return `<span class="pill red">${r}</span>`;
  return `<span class="pill amber">${r}</span>`;
}

async function renderJournal() {
  loading();
  const j = await api("/api/journal");
  const s = j.summary;
  const stats = `
    <div class="stat-row">
      <div class="stat-card"><div class="k">Net P&amp;L</div><div class="v ${sign(s.netPnL)}">$${fmt(s.netPnL)}</div><div class="sub">${s.totalTrades} closed trades</div></div>
      <div class="stat-card"><div class="k">Win rate</div><div class="v">${fmt(s.winRate, 1)}%</div><div class="sub">${s.wins}W / ${s.losses}L</div></div>
      <div class="stat-card"><div class="k">Profit factor</div><div class="v">${s.profitFactor ?? DASH}</div><div class="sub">gross win / loss</div></div>
      <div class="stat-card"><div class="k">Expectancy</div><div class="v ${sign(s.expectancy)}">$${fmt(s.expectancy)}</div><div class="sub">per trade</div></div>
      <div class="stat-card"><div class="k">Avg R:R</div><div class="v">${s.avgRR ?? DASH}</div><div class="sub">avg win / avg loss</div></div>
    </div>`;
  const toolbar = `
    <div class="toolbar">
      <button class="btn primary" id="sync-ibkr">&#8595; Sync IBKR trades</button>
      <label class="btn soft" style="cursor:pointer">&#8593; Import CSV<input type="file" id="csv-file" accept=".csv" hidden></label>
      <span class="hint" id="import-hint">IBKR Flex / Activity export, or any trades CSV</span>
    </div>`;
  const eq = j.equityCurve && j.equityCurve.length
    ? `<div class="card equity-wrap"><div class="section-head" style="margin:0 0 10px"><h2>Equity curve</h2><span class="hint">cumulative realised P&amp;L</span></div>${equityChart(j.equityCurve)}</div>`
    : "";
  const tbody = (j.trades || []).map(t => `
    <tr>
      <td class="sym">${t.symbol}</td>
      <td><span class="pill ${t.direction === 'LONG' ? 'green' : 'amber'}">${t.direction}</span></td>
      <td class="num">${fmt(t.shares, 0)}</td>
      <td class="num">${fmt(t.entryPrice)}</td>
      <td class="num">${fmt(t.exitPrice)}</td>
      <td class="num ${sign(t.pnl)}">$${fmt(t.pnl)}</td>
      <td class="num ${sign(t.pnlPct)}">${fmt(t.pnlPct)}%</td>
      <td style="font-size:11px;color:var(--muted)">${(t.exitTime || '').slice(0, 16)}</td>
    </tr>`).join("");
  const bySym = (j.bySymbol || []).map(b => `
    <tr><td class="sym">${b.symbol}</td><td class="num">${b.trades}</td><td class="num">${fmt(b.winRate,0)}%</td><td class="num ${sign(b.pnl)}">$${fmt(b.pnl)}</td></tr>`).join("");
  const main = (j.trades && j.trades.length)
    ? `<div class="card">
         <div class="card-pad section-head" style="margin:0;padding-bottom:0;"><h2>Closed trades</h2><span class="hint">FIFO-matched round trips</span></div>
         <table><thead><tr><th>Symbol</th><th>Dir</th><th class="num">Qty</th><th class="num">Entry</th><th class="num">Exit</th><th class="num">P&amp;L</th><th class="num">%</th><th>Closed</th></tr></thead><tbody>${tbody}</tbody></table>
       </div>`
    : `<div class="card"><div class="empty"><div class="big">&#128211;</div><strong>No trades yet</strong><p style="margin-top:6px">Sync from IBKR or import a CSV to populate your journal.</p></div></div>`;
  const side = (j.bySymbol && j.bySymbol.length)
    ? `<div class="card"><div class="card-pad section-head" style="margin:0;padding-bottom:0;"><h2>By symbol</h2></div>
        <table><thead><tr><th>Symbol</th><th class="num">Trades</th><th class="num">Win%</th><th class="num">P&amp;L</th></tr></thead><tbody>${bySym}</tbody></table></div>`
    : "";
  $("#content").innerHTML = `${stats}${toolbar}${eq}<div class="grid-2">${main}${side}</div>`;
  $("#sync-ibkr").addEventListener("click", async () => {
    try { const r = await api("/api/journal/sync-ibkr", { method: "POST" }); toast(`Synced ${r.added} new fills`); render(true); }
    catch (e) { toast("IBKR not connected"); }
  });
  $("#csv-file").addEventListener("change", async (e) => {
    const f = e.target.files[0]; if (!f) return;
    const fd = new FormData(); fd.append("file", f);
    try { const r = await api("/api/journal/import-csv", { method: "POST", body: fd });
      toast(r.error ? r.error : `Imported ${r.added} fills`); render(true); }
    catch (err) { toast("Import failed"); }
  });
}

function equityChart(data) {
  const w = 800, h = 120, p = 4;
  const min = Math.min(0, ...data), max = Math.max(0, ...data), rng = (max - min) || 1;
  const pts = data.map((v, i) => {
    const x = p + (i / (Math.max(1, data.length - 1))) * (w - 2 * p);
    const y = h - p - ((v - min) / rng) * (h - 2 * p);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });
  const last = data[data.length - 1];
  const col = last >= 0 ? "var(--green)" : "var(--red)";
  const area = `${p},${h - p} ${pts.join(" ")} ${w - p},${h - p}`;
  return `<svg class="equity" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">
    <polygon points="${area}" fill="${col}" opacity="0.08"/>
    <polyline points="${pts.join(" ")}" fill="none" stroke="${col}" stroke-width="2"/>
  </svg>`;
}

function newsPanel(map) {
  const items = [];
  Object.entries(map || {}).forEach(([sym, list]) => (list || []).forEach(n => items.push({ sym, ...n })));
  const rows = items.slice(0, 18).map(n => `
    <div class="news-item">
      <a href="${n.link}" target="_blank" rel="noopener">${n.title}</a>
      <div class="news-meta"><span class="news-sym">${n.sym}</span><span class="tag ${n.sentiment}">${n.sentiment}</span><span>${n.source || ''}</span></div>
    </div>`).join("");
  return `<div class="card"><div class="card-pad section-head" style="margin:0;padding-bottom:6px;"><h2>Watchlist news</h2><span class="hint">RSS</span></div>
    <div class="news-list">${rows || '<div class="empty" style="padding:30px">No headlines yet</div>'}</div></div>`;
}

function avg(rows, key) {
  const vals = rows.map(r => r[key]).filter(v => v !== null && v !== undefined && !isNaN(v));
  return vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : 0;
}
function emptyRow(cols) {
  return `<tr><td colspan="${cols || 1}"><div class="empty"><div class="big">&#128269;</div>No data ${DASH} add a ticker to your watchlist to begin.</div></td></tr>`;
}

pollStatus();
setInterval(pollStatus, 10000);
switchView("intraday");
