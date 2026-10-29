// CLOB visualisation: a cumulative depth chart (SVG) with a ladder twin.
import { fmtUsd, fmtBps, fmtPx, fmtQty, fmtInt, fmtSizeLabel } from './format.js';
import { cumulativeLevels, midOf, spreadBpsOf } from './book.js';

const NS = 'http://www.w3.org/2000/svg';
function svg(tag, attrs = {}, parent) {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'style') Object.assign(e.style, v);
    else e.setAttribute(k, v);
  }
  parent?.append(e);
  return e;
}
function el(tag, cls, parent, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  parent?.append(e);
  return e;
}
function text(parent, x, y, str, attrs = {}) {
  const t = svg('text', { x, y, ...attrs }, parent);
  t.textContent = str;
  return t;
}

export const PRECISIONS = [
  { value: 'auto', label: 'Combined · full precision + aggregated beyond' },
  { value: 'fine', label: 'Full precision · 20 levels only' },
  { value: '5', label: 'Aggregated · 5 significant figures' },
  { value: '4', label: 'Aggregated · 4 significant figures' },
  { value: '3', label: 'Aggregated · 3 significant figures' },
  { value: '2', label: 'Aggregated · 2 significant figures' },
];

function describePrecision(p, book) {
  if (p === 'fine') return '20 full-precision levels per side';
  if (p === 'auto') return book?.stitched ? 'full precision near the mid, aggregated buckets (≈) beyond' : '20 full-precision levels per side';
  return `aggregated to ${p} significant figures`;
}

const BPS_STEPS = [0.5, 1, 2, 5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000];

function niceStep(maxBps, targetTicks) {
  const raw = (2 * maxBps) / targetTicks;
  return BPS_STEPS.find((s) => s >= raw) ?? BPS_STEPS[BPS_STEPS.length - 1];
}

function niceUsdTicks(maxUsd, n = 4) {
  const raw = maxUsd / n;
  const mag = 10 ** Math.floor(Math.log10(raw || 1));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? mag * 10;
  const ticks = [];
  for (let v = step; v <= maxUsd; v += step) ticks.push(v);
  return ticks;
}

export function createClob(root, { onPrecisionChange, onViewChange } = {}) {
  root.classList.add('clob');
  const head = el('div', 'viz-head', root);
  const titleWrap = el('div', 'viz-title', head);
  el('h3', null, titleWrap, 'Order book depth');
  const sub = el('span', 'viz-sub', titleWrap, '');
  const legend = el('div', 'legend', head);
  for (const [cls, label] of [['bid', 'Bids'], ['ask', 'Asks'], ['order', 'Your order']]) {
    const item = el('span', 'legend-item', legend);
    el('span', `swatch ${cls}`, item);
    el('span', null, item, label);
  }
  const controls = el('div', 'viz-controls', head);
  const viewSeg = el('div', 'seg', controls);
  const viewBtns = {};
  for (const [id, label] of [['chart', 'Chart'], ['ladder', 'Ladder']]) {
    const b = el('button', 'seg-btn', viewSeg, label);
    b.type = 'button';
    b.addEventListener('click', () => onViewChange?.(id));
    viewBtns[id] = b;
  }
  const precLabel = el('label', 'ctl', controls);
  el('span', 'ctl-label', precLabel, 'Precision');
  const precSel = el('select', null, precLabel);
  for (const p of PRECISIONS) { const o = el('option', null, precSel, p.label); o.value = p.value; }
  precSel.addEventListener('change', () => onPrecisionChange?.(precSel.value));

  const chartWrap = el('div', 'viz-chart depth-chart', root);
  const svgRoot = svg('svg', { class: 'depth-svg', role: 'img', tabindex: '0', 'aria-label': 'Cumulative order book depth around the mid price' }, chartWrap);
  const tip = el('div', 'viz-tip', chartWrap);
  tip.hidden = true;

  const ladderWrap = el('div', 'ladder', root);
  ladderWrap.hidden = true;
  const ladderSides = {};
  for (const [side, label] of [['bids', 'Bids'], ['asks', 'Asks']]) {
    const t = el('table', `ladder-side ${side}`, ladderWrap);
    const cap = el('caption', null, t, label);
    const thead = t.createTHead();
    const tr = thead.insertRow();
    for (const h of ['Price', 'Δ bps', 'Size', 'Total', 'Orders']) { const th = el('th', null, tr, h); th.scope = 'col'; }
    ladderSides[side] = { table: t, tbody: t.createTBody(), caption: cap };
  }
  const foot = el('p', 'viz-foot', root, '');

  let last = null;
  let width = 0;
  let hover = null; // { side:'bids'|'asks', idx }
  let geom = null;
  const hoverLayer = svg('g', { class: 'hover-layer' });

  const ro = new ResizeObserver(() => {
    const w = chartWrap.clientWidth;
    if (w && w !== width) { width = w; if (last) draw(); }
  });
  ro.observe(chartWrap);

  function levelAt(bps) {
    if (!geom) return null;
    const { bids, asks } = geom;
    if (bps >= 0) {
      if (!asks.length) return null;
      let idx = 0;
      for (let i = 0; i < asks.length; i++) if (asks[i].bps <= bps) idx = i; else break;
      return { side: 'asks', idx };
    }
    if (!bids.length) return null;
    let idx = 0;
    for (let i = 0; i < bids.length; i++) if (bids[i].bps >= bps) idx = i; else break;
    return { side: 'bids', idx };
  }

  function drawHover() {
    hoverLayer.replaceChildren();
    if (!hover || !geom) { tip.hidden = true; return; }
    const { x, y, top, plotH, mid, L, plotW } = geom;
    const lvls = geom[hover.side];
    const l = lvls[Math.min(hover.idx, lvls.length - 1)];
    if (!l) { tip.hidden = true; return; }
    const cx = x(l.bps), cy = y(l.cumUsd);
    svg('line', { x1: cx, x2: cx, y1: top, y2: top + plotH, class: 'crosshair' }, hoverLayer);
    svg('circle', { cx, cy, r: 5, class: `dot ${hover.side === 'bids' ? 'bid' : 'ask'}` }, hoverLayer);
    tip.replaceChildren();
    const sideLabel = hover.side === 'bids' ? 'Bid' : 'Ask';
    el('div', 'tip-title', tip, `${sideLabel} · ${l.agg ? '≈ ' : ''}${fmtPx(l.px)} · ${fmtBps(l.bps, 1)} bps from mid${l.agg ? ' · aggregated bucket' : ''}`);
    const rows = [
      ['Cumulative', `${fmtUsd(l.cumUsd)} · ${fmtQty(l.cumSz)}`],
      ['This level', `${fmtUsd(l.px * l.sz)} · ${fmtQty(l.sz)}`],
      ['Orders at level', fmtInt(l.n)],
    ];
    for (const [k, v] of rows) {
      const r = el('div', 'tip-row', tip);
      el('span', 'tip-val', r, v);
      el('span', 'tip-key', r, k);
    }
    tip.hidden = false;
    const tw = tip.offsetWidth || 160;
    const left = cx + 12 + tw > L + plotW ? cx - tw - 12 : cx + 12;
    tip.style.left = `${Math.max(0, left)}px`;
    tip.style.top = `${Math.max(0, Math.min(cy - 10, top + plotH - 80))}px`;
  }

  function draw() {
    const data = last;
    svgRoot.replaceChildren();
    geom = null;
    const W = Math.max(320, width || chartWrap.clientWidth || 600);
    const H = 300;
    const L = 64, R = 16, T = 22, B = 46;
    const plotW = W - L - R, plotH = H - T - B;
    svgRoot.setAttribute('viewBox', `0 0 ${W} ${H}`);
    svgRoot.setAttribute('width', W);
    svgRoot.setAttribute('height', H);
    const book = data?.book;
    const mid = midOf(book);
    if (!book || !(mid > 0) || (!book.bids.length && !book.asks.length)) {
      text(svgRoot, W / 2, H / 2, data?.market?.isDelisted ? 'Market delisted — no live book' : (data?.market ? 'Waiting for order book…' : 'Select a market'), { class: 'empty', 'text-anchor': 'middle' });
      foot.textContent = '';
      return;
    }
    const bids = cumulativeLevels(book.bids, mid);
    const asks = cumulativeLevels(book.asks, mid);
    const worstBid = bids.length ? -bids[bids.length - 1].bps : 0;
    const worstAsk = asks.length ? asks[asks.length - 1].bps : 0;
    const maxBps = Math.max(0.5, worstBid, worstAsk) * 1.06;
    const depthMax = Math.max(bids[bids.length - 1]?.cumUsd ?? 0, asks[asks.length - 1]?.cumUsd ?? 0);
    const maxUsd = Math.max(1, depthMax) * 1.08;
    const x = (bps) => L + ((bps + maxBps) / (2 * maxBps)) * plotW;
    const y = (usd) => T + plotH - (usd / maxUsd) * plotH;
    geom = { x, y, top: T, plotH, plotW, L, mid, bids, asks };

    // grid + axes
    const grid = svg('g', { class: 'grid' }, svgRoot);
    for (const v of niceUsdTicks(maxUsd)) {
      svg('line', { x1: L, x2: L + plotW, y1: y(v), y2: y(v) }, grid);
      text(grid, L - 8, y(v) + 4, fmtUsd(v, { digits: 1 }), { 'text-anchor': 'end', class: 'tick' });
    }
    svg('line', { x1: L, x2: L + plotW, y1: T + plotH, y2: T + plotH, class: 'axis' }, svgRoot);
    const step = niceStep(maxBps, 6);
    for (let b = -Math.floor(maxBps / step) * step; b <= maxBps; b += step) {
      if (Math.abs(b) < 1e-9) continue;
      const px = x(b);
      svg('line', { x1: px, x2: px, y1: T + plotH, y2: T + plotH + 4, class: 'axis' }, svgRoot);
      text(svgRoot, px, T + plotH + 16, `${b > 0 ? '+' : ''}${fmtBps(b, step < 1 ? 1 : 0)}`, { 'text-anchor': 'middle', class: 'tick' });
      text(svgRoot, px, T + plotH + 30, fmtPx(mid * (1 + b / 1e4)), { 'text-anchor': 'middle', class: 'tick muted' });
    }
    text(svgRoot, L + plotW, T + plotH + 42, 'distance from mid (bps) · price', { 'text-anchor': 'end', class: 'tick muted' });

    // depth steps
    const stepPath = (levels, sign) => {
      if (!levels.length) return { line: '', area: '' };
      let d = `M${x(0)},${y(0)}`;
      let prevCum = 0;
      for (const l of levels) {
        d += ` L${x(l.bps)},${y(prevCum)} L${x(l.bps)},${y(l.cumUsd)}`;
        prevCum = l.cumUsd;
      }
      const edge = sign > 0 ? maxBps : -maxBps;
      d += ` L${x(edge)},${y(prevCum)}`;
      return { line: d, area: `${d} L${x(edge)},${y(0)} Z` };
    };
    const bidPath = stepPath(bids, -1), askPath = stepPath(asks, +1);
    svg('path', { d: bidPath.area, class: 'area bid' }, svgRoot);
    svg('path', { d: askPath.area, class: 'area ask' }, svgRoot);
    svg('path', { d: bidPath.line, class: 'line bid' }, svgRoot);
    svg('path', { d: askPath.line, class: 'line ask' }, svgRoot);

    // mid line
    svg('line', { x1: x(0), x2: x(0), y1: T, y2: T + plotH, class: 'midline' }, svgRoot);
    const spread = spreadBpsOf(book);
    text(svgRoot, x(0), T - 8, `mid ${fmtPx(mid)}${Number.isFinite(spread) ? ` · spread ${fmtBps(spread, 2)} bps` : ''}`, { 'text-anchor': 'middle', class: 'label' });

    // order overlay
    const size = data.sizeUsd;
    if (size > 0) {
      if (size <= maxUsd) {
        svg('line', { x1: L, x2: L + plotW, y1: y(size), y2: y(size), class: 'orderline' }, svgRoot);
        text(svgRoot, L + plotW - 4, y(size) - 4, `order ${fmtSizeLabel(size)}`, { 'text-anchor': 'end', class: 'label muted' });
      }
      const overlay = (res, levels, cls, labelY, name) => {
        if (!res || !(res.filledUsd > 0) || !levels.length) return;
        const full = res.coverage >= 0.999;
        const endRaw = Number.isFinite(res.lastPx) ? 1e4 * (res.lastPx - mid) / mid : NaN;
        const reach = levels[levels.length - 1].bps;
        const beyond = !Number.isFinite(endRaw) || (cls === 'ask' ? endRaw > reach + 1e-9 : endRaw < reach - 1e-9);
        const endBps = full && !beyond ? endRaw : (cls === 'ask' ? maxBps : -maxBps);
        const x0 = x(levels[0].bps), x1 = x(endBps);
        svg('rect', { x: Math.min(x0, x1), y: T, width: Math.abs(x1 - x0), height: plotH, class: `band ${cls}` }, svgRoot);
        svg('line', { x1, x2: x1, y1: T, y2: T + plotH, class: `marker ${cls}` }, svgRoot);
        const label = full
          ? `${name}: VWAP ${res.deepestAgg ? '≈ ' : ''}${fmtPx(res.vwap)} · ${fmtBps(res.spreadBps + res.impactBps, 1)} bps${beyond ? ' · ends beyond this view' : ''}`
          : `${name}: ${(res.coverage * 100).toFixed(0)} % fillable in the visible book`;
        text(svgRoot, cls === 'ask' ? Math.min(x1 + 6, L + plotW - 4) : Math.max(x1 - 6, L + 4), labelY, label,
          { 'text-anchor': cls === 'ask' ? (x1 + 6 + 160 > L + plotW ? 'end' : 'start') : (x1 - 6 - 160 < L ? 'start' : 'end'), class: 'label' });
      };
      overlay(data.buy, asks, 'ask', T + 14, `Buy ${fmtSizeLabel(size)}`);
      overlay(data.sell, bids, 'bid', T + 30, `Sell ${fmtSizeLabel(size)}`);
    }

    svgRoot.append(hoverLayer);
    const hit = svg('rect', { x: L, y: T, width: plotW, height: plotH, class: 'hit' }, svgRoot);
    hit.addEventListener('pointermove', (ev) => {
      const r = svgRoot.getBoundingClientRect();
      const px = (ev.clientX - r.left) * (W / r.width);
      const bps = ((px - L) / plotW) * 2 * maxBps - maxBps;
      hover = levelAt(bps);
      drawHover();
    });
    hit.addEventListener('pointerleave', () => { hover = null; drawHover(); });
    drawHover();

    const sumSide = (levels, name, sign) => {
      if (!levels.length) return `${name}: empty`;
      const agg = levels.filter((l) => l.agg).length;
      return `${name} ${fmtUsd(levels[levels.length - 1].cumUsd)} in ${levels.length} levels${agg ? ` (${agg} aggregated)` : ''} to ${sign}${fmtBps(Math.abs(levels[levels.length - 1].bps), 1)} bps`;
    };
    foot.textContent = `${sumSide(bids, 'Bids', '−')} · ${sumSide(asks, 'Asks', '+')}`;
  }

  svgRoot.addEventListener('keydown', (ev) => {
    if (!geom) return;
    const order = [...geom.bids.map((_, i) => ({ side: 'bids', idx: i })).reverse(), ...geom.asks.map((_, i) => ({ side: 'asks', idx: i }))];
    if (!order.length) return;
    let pos = hover ? order.findIndex((o) => o.side === hover.side && o.idx === hover.idx) : -1;
    if (ev.key === 'ArrowRight') pos = Math.min(order.length - 1, pos + 1);
    else if (ev.key === 'ArrowLeft') pos = Math.max(0, pos < 0 ? geom.bids.length : pos - 1);
    else if (ev.key === 'Escape') { hover = null; drawHover(); return; }
    else return;
    ev.preventDefault();
    hover = order[pos];
    drawHover();
  });
  svgRoot.addEventListener('blur', () => { hover = null; drawHover(); });

  function drawLadder() {
    const book = last?.book;
    const mid = midOf(book);
    for (const side of ['bids', 'asks']) {
      const { tbody, caption } = ladderSides[side];
      tbody.replaceChildren();
      const levels = book && mid > 0 ? cumulativeLevels(book[side], mid) : [];
      const max = levels.length ? levels[levels.length - 1].cumUsd : 1;
      caption.textContent = `${side === 'bids' ? 'Bids' : 'Asks'} · ${levels.length} levels · ${fmtUsd(max)}`;
      for (const l of levels) {
        const tr = tbody.insertRow();
        const pct = Math.max(0, Math.min(100, (l.cumUsd / max) * 100));
        tr.style.setProperty('--w', `${pct}%`);
        if (l.agg) { tr.classList.add('agg'); tr.title = 'Aggregated bucket beyond the 20 full-precision levels (conservative edge price)'; }
        for (const v of [(l.agg ? '≈ ' : '') + fmtPx(l.px), fmtBps(l.bps, 1), fmtQty(l.sz), fmtUsd(l.cumUsd), fmtInt(l.n)]) tr.insertCell().textContent = v;
      }
    }
  }

  return {
    render(data) {
      last = data;
      sub.textContent = data?.market ? `${data.market.name} · ${describePrecision(data.precision ?? 'auto', data.book)}` : '';
      for (const [id, b] of Object.entries(viewBtns)) b.classList.toggle('active', id === (data?.view ?? 'chart'));
      precSel.value = data?.precision ?? 'auto';
      const isLadder = data?.view === 'ladder';
      chartWrap.hidden = isLadder;
      ladderWrap.hidden = !isLadder;
      if (isLadder) drawLadder(); else draw();
    },
    destroy() { ro.disconnect(); },
  };
}
