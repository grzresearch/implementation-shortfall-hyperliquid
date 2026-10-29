// Market-order cost versus order size: the paper's slippage curve, computed live.
import { fmtBps, fmtSizeLabel } from './format.js';
import { execCost } from './book.js';

const NS = 'http://www.w3.org/2000/svg';
function svg(tag, attrs = {}, parent) {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  parent?.append(e);
  return e;
}
function el(tag, cls, parent, txt) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (txt != null) e.textContent = txt;
  parent?.append(e);
  return e;
}
function text(parent, x, y, str, attrs = {}) { const t = svg('text', { x, y, ...attrs }, parent); t.textContent = str; return t; }

function niceTicks(max, n = 4) {
  const raw = max / n;
  const mag = 10 ** Math.floor(Math.log10(raw || 1));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? mag * 10;
  const ticks = [];
  for (let v = 0; v <= max + 1e-9; v += step) ticks.push(v);
  return ticks;
}

export function createCurve(root, { onViewChange } = {}) {
  root.classList.add('curve');
  const head = el('div', 'viz-head', root);
  const titleWrap = el('div', 'viz-title', head);
  el('h3', null, titleWrap, 'Market-order cost vs. size');
  const sub = el('span', 'viz-sub', titleWrap, 'spread + impact + taker fee, bps of filled notional');
  const legend = el('div', 'legend', head);
  for (const [cls, label] of [['ask line', 'Live buy'], ['bid line', 'Live sell'], ['ask hollow', 'Partly fillable (lower bound)'], ['paper', 'Paper median (NAIVE_MARKET)']]) {
    const item = el('span', 'legend-item', legend);
    el('span', `swatch ${cls}`, item);
    el('span', null, item, label);
  }
  const controls = el('div', 'viz-controls', head);
  const seg = el('div', 'seg', controls);
  const btns = {};
  for (const [id, label] of [['chart', 'Chart'], ['table', 'Table']]) {
    const b = el('button', 'seg-btn', seg, label);
    b.type = 'button';
    b.addEventListener('click', () => onViewChange?.(id));
    btns[id] = b;
  }
  const chartWrap = el('div', 'viz-chart curve-chart', root);
  const svgRoot = svg('svg', { class: 'curve-svg', role: 'img', 'aria-label': 'Execution cost in basis points against order size' }, chartWrap);
  const tip = el('div', 'viz-tip', chartWrap);
  tip.hidden = true;
  const tableWrap = el('div', 'curve-table', root);
  tableWrap.hidden = true;
  const table = el('table', 'data-table', tableWrap);
  const thead = table.createTHead();
  const hr = thead.insertRow();
  for (const h of ['Size', 'Live buy (bps)', 'Live sell (bps)', 'Paper buy (bps)', 'Paper sell (bps)']) { const th = el('th', null, hr, h); th.scope = 'col'; }
  const tbody = table.createTBody();

  let last = null, width = 0, series = null, geom = null, hoverIdx = -1;
  const hoverLayer = svg('g', { class: 'hover-layer' });
  const ro = new ResizeObserver(() => { const w = chartWrap.clientWidth; if (w && w !== width) { width = w; if (last) draw(); } });
  ro.observe(chartWrap);

  function compute() {
    const { book, fees, sizes, paper } = last;
    const pts = sizes.map((s) => {
      const buy = book ? execCost({ side: 'BUY', book, sizeUsd: s, takerBps: fees.takerBps }) : null;
      const sell = book ? execCost({ side: 'SELL', book, sizeUsd: s, takerBps: fees.takerBps }) : null;
      return {
        size: s,
        buy: buy?.totalBps ?? NaN, buyCov: buy?.coverage ?? NaN,
        sell: sell?.totalBps ?? NaN, sellCov: sell?.coverage ?? NaN,
        paperBuy: paper?.cell('NAIVE_MARKET', 'BUY', s)?.median ?? NaN,
        paperSell: paper?.cell('NAIVE_MARKET', 'SELL', s)?.median ?? NaN,
      };
    });
    return pts;
  }

  function drawHover() {
    hoverLayer.replaceChildren();
    if (hoverIdx < 0 || !geom || !series) { tip.hidden = true; return; }
    const p = series[hoverIdx];
    const { x, y, top, plotH, L, plotW } = geom;
    const cx = x(p.size);
    svg('line', { x1: cx, x2: cx, y1: top, y2: top + plotH, class: 'crosshair' }, hoverLayer);
    tip.replaceChildren();
    el('div', 'tip-title', tip, `Order ${fmtSizeLabel(p.size)}`);
    const rows = [
      ['ask', 'Live buy', p.buy, p.buyCov], ['bid', 'Live sell', p.sell, p.sellCov],
      ['paper', 'Paper buy', p.paperBuy, 1], ['paper', 'Paper sell', p.paperSell, 1],
    ];
    for (const [cls, k, v, cov] of rows) {
      if (!Number.isFinite(v)) continue;
      const r = el('div', 'tip-row', tip);
      el('span', `key ${cls}`, r);
      el('span', 'tip-val', r, `${Number.isFinite(cov) && cov < 0.999 ? '≥ ' : ''}${fmtBps(v, 2)} bps`);
      el('span', 'tip-key', r, k);
    }
    tip.hidden = false;
    const tw = tip.offsetWidth || 160;
    tip.style.left = `${cx + 12 + tw > L + plotW ? cx - tw - 12 : cx + 12}px`;
    tip.style.top = `${top + 8}px`;
  }

  function draw() {
    svgRoot.replaceChildren();
    geom = null;
    series = compute();
    const W = Math.max(320, width || chartWrap.clientWidth || 600);
    const H = 240;
    const L = 48, R = 16, T = 16, B = 36;
    const plotW = W - L - R, plotH = H - T - B;
    svgRoot.setAttribute('viewBox', `0 0 ${W} ${H}`);
    svgRoot.setAttribute('width', W);
    svgRoot.setAttribute('height', H);
    const vals = series.flatMap((p) => [p.buy, p.sell, p.paperBuy, p.paperSell]).filter(Number.isFinite);
    if (!vals.length) {
      text(svgRoot, W / 2, H / 2, 'Waiting for order book…', { class: 'empty', 'text-anchor': 'middle' });
      return;
    }
    const sizes = series.map((p) => p.size);
    const lx0 = Math.log10(sizes[0]), lx1 = Math.log10(sizes[sizes.length - 1]);
    const yMax = Math.max(...vals) * 1.12 || 1;
    const x = (s) => L + ((Math.log10(s) - lx0) / (lx1 - lx0 || 1)) * plotW;
    const y = (v) => T + plotH - (v / yMax) * plotH;
    geom = { x, y, top: T, plotH, L, plotW };

    const grid = svg('g', { class: 'grid' }, svgRoot);
    for (const v of niceTicks(yMax)) {
      svg('line', { x1: L, x2: L + plotW, y1: y(v), y2: y(v) }, grid);
      text(grid, L - 8, y(v) + 4, fmtBps(v, v < 10 ? 1 : 0), { 'text-anchor': 'end', class: 'tick' });
    }
    svg('line', { x1: L, x2: L + plotW, y1: T + plotH, y2: T + plotH, class: 'axis' }, svgRoot);
    const every = sizes.length > 9 ? 2 : 1;
    sizes.forEach((s, i) => {
      if (i % every) return;
      text(svgRoot, x(s), T + plotH + 16, fmtSizeLabel(s), { 'text-anchor': 'middle', class: 'tick' });
    });
    text(svgRoot, L - 8, T - 4, 'bps', { 'text-anchor': 'end', class: 'tick muted' });

    // current order size marker
    if (last.sizeUsd >= sizes[0] && last.sizeUsd <= sizes[sizes.length - 1]) {
      svg('line', { x1: x(last.sizeUsd), x2: x(last.sizeUsd), y1: T, y2: T + plotH, class: 'orderline' }, svgRoot);
    }

    const linePath = (key, covKey) => {
      let d = '', open = false;
      for (const p of series) {
        const full = Number.isFinite(p[key]) && p[covKey] >= 0.999;
        if (full) { d += `${open ? ' L' : 'M'}${x(p.size)},${y(p[key])}`; open = true; } else open = false;
      }
      return d;
    };
    svg('path', { d: linePath('sell', 'sellCov'), class: 'line bid' }, svgRoot);
    svg('path', { d: linePath('buy', 'buyCov'), class: 'line ask' }, svgRoot);
    for (const p of series) {
      for (const [key, covKey, cls] of [['sell', 'sellCov', 'bid'], ['buy', 'buyCov', 'ask']]) {
        if (!Number.isFinite(p[key])) continue;
        const full = p[covKey] >= 0.999;
        svg('circle', { cx: x(p.size), cy: y(p[key]), r: 4, class: `dot ${cls}${full ? '' : ' hollow'}` }, svgRoot);
      }
      for (const [key, cls] of [['paperSell', 'bid'], ['paperBuy', 'ask']]) {
        if (!Number.isFinite(p[key])) continue;
        const cx = x(p.size), cy = y(p[key]);
        svg('path', { d: `M${cx},${cy - 5} L${cx + 5},${cy} L${cx},${cy + 5} L${cx - 5},${cy} Z`, class: `diamond ${cls}` }, svgRoot);
      }
    }
    svgRoot.append(hoverLayer);
    const hit = svg('rect', { x: L, y: T, width: plotW, height: plotH, class: 'hit' }, svgRoot);
    hit.addEventListener('pointermove', (ev) => {
      const r = svgRoot.getBoundingClientRect();
      const px = (ev.clientX - r.left) * (W / r.width);
      let best = -1, bestD = Infinity;
      series.forEach((p, i) => { const d = Math.abs(x(p.size) - px); if (d < bestD) { bestD = d; best = i; } });
      hoverIdx = best;
      drawHover();
    });
    hit.addEventListener('pointerleave', () => { hoverIdx = -1; drawHover(); });
    drawHover();
  }

  function drawTable() {
    series = compute();
    tbody.replaceChildren();
    const cell = (v, cov) => (Number.isFinite(v) ? `${Number.isFinite(cov) && cov < 0.999 ? '≥ ' : ''}${fmtBps(v, 2)}` : '–');
    for (const p of series) {
      const tr = tbody.insertRow();
      for (const v of [fmtSizeLabel(p.size), cell(p.buy, p.buyCov), cell(p.sell, p.sellCov), cell(p.paperBuy, 1), cell(p.paperSell, 1)]) tr.insertCell().textContent = v;
    }
  }

  return {
    render(data) {
      last = data;
      sub.textContent = data.paper ? `spread + impact + taker fee · paper medians from ${data.paperPeriod}` : 'spread + impact + taker fee, bps of filled notional';
      for (const [id, b] of Object.entries(btns)) b.classList.toggle('active', id === (data.view ?? 'chart'));
      const isTable = data.view === 'table';
      chartWrap.hidden = isTable;
      tableWrap.hidden = !isTable;
      if (isTable) drawTable(); else draw();
    },
    destroy() { ro.disconnect(); },
  };
}
