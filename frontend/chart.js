/* ── Chart module — click any ticker → candlestick chart ──────────────
   Self-contained: depends only on TradingView Lightweight Charts (CDN) and
   the /api/candles endpoint. Attaches itself via event delegation, so app.js
   needs no changes. Daily / Weekly / Monthly with SMA/EMA overlays + volume,
   and synced RSI + MACD panes. Theme-aware (reads CSS variables). */
(function () {
  "use strict";

  const TF = [{ k: "D", n: "Daily" }, { k: "W", n: "Weekly" }, { k: "M", n: "Monthly" }];
  const MAS = [
    { id: "sma20", label: "SMA 20", type: "sma", p: 20, color: "#4f7cff", on: true },
    { id: "sma50", label: "SMA 50", type: "sma", p: 50, color: "#f5b13d", on: true },
    { id: "sma200", label: "SMA 200", type: "sma", p: 200, color: "#ff5468", on: false },
    { id: "ema9", label: "EMA 9", type: "ema", p: 9, color: "#22b8cf", on: true },
    { id: "ema20", label: "EMA 20", type: "ema", p: 20, color: "#7b6bff", on: false },
  ];

  const state = { symbol: null, tf: "D", candles: [], ind: {}, charts: [], live: null, changePct: null };
  MAS.forEach(m => (state.ind[m.id] = m.on));

  /* ── indicator math ───────────────────────────────────────────── */
  function sma(v, p) {
    const o = Array(v.length).fill(null); let s = 0;
    for (let i = 0; i < v.length; i++) { s += v[i]; if (i >= p) s -= v[i - p]; if (i >= p - 1) o[i] = s / p; }
    return o;
  }
  function ema(v, p) {
    const o = Array(v.length).fill(null), k = 2 / (p + 1); let prev = v[0];
    for (let i = 0; i < v.length; i++) { prev = i === 0 ? v[0] : v[i] * k + prev * (1 - k); o[i] = prev; }
    return o;
  }
  function rsi(v, p) {
    p = p || 14; const o = Array(v.length).fill(null); let g = 0, l = 0;
    for (let i = 1; i < v.length; i++) {
      const d = v[i] - v[i - 1], up = Math.max(d, 0), dn = Math.max(-d, 0);
      if (i <= p) { g += up; l += dn; if (i === p) { g /= p; l /= p; o[i] = 100 - 100 / (1 + (l === 0 ? 100 : g / l)); } }
      else { g = (g * (p - 1) + up) / p; l = (l * (p - 1) + dn) / p; o[i] = 100 - 100 / (1 + (l === 0 ? 100 : g / l)); }
    }
    return o;
  }
  function macd(v) {
    const e12 = ema(v, 12), e26 = ema(v, 26);
    const line = v.map((_, i) => e12[i] - e26[i]);
    const sig = ema(line, 9);
    const hist = line.map((x, i) => x - sig[i]);
    return { line, signal: sig, hist };
  }
  function lineData(times, arr) {
    const out = [];
    for (let i = 0; i < arr.length; i++) if (arr[i] != null && !isNaN(arr[i])) out.push({ time: times[i], value: +arr[i].toFixed(2) });
    return out;
  }

  /* ── theme colors from CSS vars ───────────────────────────────── */
  function colors() {
    const c = getComputedStyle(document.documentElement);
    const g = k => c.getPropertyValue(k).trim();
    return { green: g("--green"), red: g("--red"), text: g("--text-2"), grid: g("--border-2"),
             border: g("--border"), surface: g("--surface"), brand: g("--brand"), muted: g("--muted") };
  }

  /* ── overlay DOM (built once) ─────────────────────────────────── */
  function buildOverlay() {
    let el = document.getElementById("chart-overlay");
    if (el) return el;
    el = document.createElement("div");
    el.id = "chart-overlay";
    el.className = "chart-overlay";
    el.innerHTML =
      '<div class="chart-panel">' +
        '<div class="chart-head">' +
          '<div class="chart-title"><span id="ch-sym">—</span><span id="ch-meta" class="muted"></span></div>' +
          '<div class="chart-tf" id="ch-tf"></div>' +
          '<button class="chart-close" id="ch-close">&#10005;</button>' +
        '</div>' +
        '<div class="chart-inds" id="ch-inds"></div>' +
        '<div class="chart-body">' +
          '<div id="ch-price" class="ch-pane ch-price"></div>' +
          '<div class="ch-pane-label">RSI (14)</div><div id="ch-rsi" class="ch-pane ch-sub"></div>' +
          '<div class="ch-pane-label">MACD (12,26,9)</div><div id="ch-macd" class="ch-pane ch-sub"></div>' +
        '</div>' +
      '</div>';
    document.body.appendChild(el);
    el.addEventListener("click", e => { if (e.target === el) closeChart(); });
    el.querySelector("#ch-close").addEventListener("click", closeChart);
    // timeframe buttons
    const tf = el.querySelector("#ch-tf");
    TF.forEach(t => {
      const b = document.createElement("button");
      b.className = "seg"; b.textContent = t.n; b.dataset.tf = t.k;
      b.addEventListener("click", () => { state.tf = t.k; fetchAndRender(); });
      tf.appendChild(b);
    });
    // indicator toggles
    const inds = el.querySelector("#ch-inds");
    MAS.forEach(m => {
      const b = document.createElement("button");
      b.className = "ind-pill"; b.dataset.id = m.id; b.dataset.color = m.color;
      b.innerHTML = '<i style="background:' + m.color + '"></i>' + m.label;
      b.addEventListener("click", () => { state.ind[m.id] = !state.ind[m.id]; render(); });
      inds.appendChild(b);
    });
    document.addEventListener("keydown", e => { if (e.key === "Escape") closeChart(); });
    window.addEventListener("resize", () => { if (el.classList.contains("show")) render(); });
    return el;
  }

  function syncTf() {
    document.querySelectorAll("#ch-tf .seg").forEach(b => b.classList.toggle("on", b.dataset.tf === state.tf));
    document.querySelectorAll("#ch-inds .ind-pill").forEach(b => b.classList.toggle("on", !!state.ind[b.dataset.id]));
  }

  function destroyCharts() {
    state.charts.forEach(c => { try { c.remove(); } catch (e) {} });
    state.charts = [];
  }

  /* ── render the three panes ───────────────────────────────────── */
  function render() {
    if (!window.LightweightCharts) {
      const pe = document.getElementById("ch-price");
      if (pe) pe.innerHTML = '<div class="ch-empty">Chart library didn\'t load — check your connection and reopen.</div>';
      const re = document.getElementById("ch-rsi"), me = document.getElementById("ch-macd");
      if (re) re.innerHTML = ""; if (me) me.innerHTML = "";
      return;
    }
    syncTf();
    destroyCharts();
    const C = colors();
    const candles = state.candles;
    const priceEl = document.getElementById("ch-price"),
          rsiEl = document.getElementById("ch-rsi"),
          macdEl = document.getElementById("ch-macd");
    if (!priceEl) return;
    if (!candles.length) { priceEl.innerHTML = '<div class="ch-empty">No price data for ' + state.symbol + '</div>'; rsiEl.innerHTML = ""; macdEl.innerHTML = ""; return; }
    priceEl.innerHTML = ""; rsiEl.innerHTML = ""; macdEl.innerHTML = "";

    const W = priceEl.clientWidth || 800;
    const base = {
      layout: { background: { type: "solid", color: "transparent" }, textColor: C.text, fontFamily: "Inter, sans-serif" },
      grid: { vertLines: { color: C.grid }, horzLines: { color: C.grid } },
      rightPriceScale: { borderColor: C.border },
      timeScale: { borderColor: C.border, rightOffset: 4 },
      crosshair: { mode: 0 },
      handleScale: { axisPressedMouseMove: true },
    };
    const LC = window.LightweightCharts;
    const closes = candles.map(c => c.close);
    const times = candles.map(c => c.time);

    // price pane
    const pc = LC.createChart(priceEl, Object.assign({ width: W, height: priceEl.clientHeight || 330 }, base));
    const cs = pc.addCandlestickSeries({ upColor: C.green, downColor: C.red, borderUpColor: C.green, borderDownColor: C.red, wickUpColor: C.green, wickDownColor: C.red });
    cs.setData(candles.map(c => ({ time: c.time, open: c.open, high: c.high, low: c.low, close: c.close })));
    if (state.live != null) cs.createPriceLine({ price: state.live, color: C.brand, lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: "now" });
    const vol = pc.addHistogramSeries({ priceFormat: { type: "volume" }, priceScaleId: "vol" });
    vol.priceScale().applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    vol.setData(candles.map((c, i) => ({ time: c.time, value: c.volume, color: (i && closes[i] >= closes[i - 1]) ? C.green + "55" : C.red + "55" })));
    MAS.forEach(m => {
      if (!state.ind[m.id]) return;
      const arr = m.type === "sma" ? sma(closes, m.p) : ema(closes, m.p);
      const s = pc.addLineSeries({ color: m.color, lineWidth: 1.6, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
      s.setData(lineData(times, arr));
    });

    // RSI pane
    const rc = LC.createChart(rsiEl, Object.assign({ width: W, height: 110 }, base));
    const rs = rc.addLineSeries({ color: C.brand, lineWidth: 1.6, priceLineVisible: false, lastValueVisible: false });
    rs.setData(lineData(times, rsi(closes, 14)));
    [30, 70].forEach(lv => rs.createPriceLine({ price: lv, color: C.grid, lineWidth: 1, lineStyle: 2, axisLabelVisible: true }));

    // MACD pane
    const mc = LC.createChart(macdEl, Object.assign({ width: W, height: 120 }, base));
    const m = macd(closes);
    const mh = mc.addHistogramSeries({ priceLineVisible: false });
    mh.setData(times.map((t, i) => ({ time: t, value: +(m.hist[i] || 0).toFixed(3), color: (m.hist[i] >= 0 ? C.green : C.red) + "99" })));
    const ml = mc.addLineSeries({ color: C.brand, lineWidth: 1.5, priceLineVisible: false, lastValueVisible: false });
    ml.setData(lineData(times, m.line));
    const msig = mc.addLineSeries({ color: C.red, lineWidth: 1.3, priceLineVisible: false, lastValueVisible: false });
    msig.setData(lineData(times, m.signal));

    state.charts = [pc, rc, mc];
    // sync time scales across panes
    let syncing = false;
    const sync = src => r => {
      if (syncing || !r) return; syncing = true;
      state.charts.forEach(c => { if (c !== src) try { c.timeScale().setVisibleLogicalRange(r); } catch (e) {} });
      syncing = false;
    };
    state.charts.forEach(c => c.timeScale().subscribeVisibleLogicalRangeChange(sync(c)));
    pc.timeScale().fitContent();
  }

  async function fetchAndRender() {
    syncTf();
    const meta = document.getElementById("ch-meta");
    document.getElementById("ch-price").innerHTML = '<div class="ch-empty">Loading ' + state.symbol + ' …</div>';
    document.getElementById("ch-rsi").innerHTML = ""; document.getElementById("ch-macd").innerHTML = "";
    try {
      const r = await fetch("/api/candles/" + encodeURIComponent(state.symbol) + "?tf=" + state.tf);
      const d = await r.json();
      state.candles = d.candles || [];
      state.live = (typeof d.livePrice === "number") ? d.livePrice : null;
      state.changePct = (typeof d.changePct === "number") ? d.changePct : null;
      if (meta) {
        const priceTxt = state.live != null
          ? `<b>$${state.live.toFixed(2)}</b>` + (state.changePct != null ? ` <span style="color:${state.changePct >= 0 ? 'var(--green)' : 'var(--red)'}">${state.changePct >= 0 ? '+' : ''}${state.changePct.toFixed(2)}%</span>` : "") + " · "
          : "";
        meta.innerHTML = priceTxt + (state.candles.length ? state.candles.length + " bars" : "");
      }
      render();
    } catch (e) {
      document.getElementById("ch-price").innerHTML = '<div class="ch-empty">Couldn\'t load chart data.</div>';
    }
  }

  window.openChart = function (symbol) {
    if (!symbol) return;
    const el = buildOverlay();
    state.symbol = symbol.toUpperCase();
    document.getElementById("ch-sym").textContent = state.symbol;
    el.classList.add("show");
    fetchAndRender();
  };
  function closeChart() {
    const el = document.getElementById("chart-overlay");
    if (el) el.classList.remove("show");
    destroyCharts();
  }

  /* ── click delegation: any ticker symbol opens its chart ──────── */
  function initDelegation() {
    const content = document.getElementById("content");
    if (!content) return;
    content.addEventListener("click", e => {
      if (e.target.closest(".rm")) return;               // ✕ remove handled by app.js
      const dc = e.target.closest("[data-chart]");       // explicit symbol (e.g. sector ETFs)
      if (dc && dc.dataset.chart) { window.openChart(dc.dataset.chart); return; }
      const cell = e.target.closest("td.sym, .sym");
      if (!cell) return;
      const m = cell.textContent.trim().match(/^[A-Z][A-Z.\-]*/);
      if (m) window.openChart(m[0]);
    });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", initDelegation);
  else initDelegation();
})();
