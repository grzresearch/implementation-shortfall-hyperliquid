// The all-markets table: keyed rows, in-place cell updates, click-to-sort.
import { fmtUsd, fmtBps, fmtPx, fmtAge, fmtSizeLabel } from './format.js';
import { bookStats, execCost, reachBps, midOf } from './book.js';
import { ASSET_CLASSES, effectiveBook } from './universe.js';

/** Derive everything the table shows for one market. Cheap: ≤ 40 levels per side. */
export function rowModel(m, { bandBps, sizeUsd, fees, now }) {
  const book = effectiveBook(m);
  const twoSided = Boolean(book?.bids.length && book?.asks.length);
  const stats = book ? bookStats(book, [bandBps]) : null;
  const mid = book ? midOf(book) : NaN;
  const buy = twoSided ? execCost({ side: 'BUY', book, sizeUsd, takerBps: fees.takerBps }) : null;
  const sell = twoSided ? execCost({ side: 'SELL', book, sizeUsd, takerBps: fees.takerBps }) : null;
  return {
    m, name: m.name, base: m.base, dex: m.dex, dexLabel: m.dexLabel, cls: m.cls, quote: m.quote, isDelisted: m.isDelisted,
    // With one side missing the book's "mid" is just the other touch; prefer the exchange's mid then.
    mid: twoSided ? mid : (m.ctx?.mid ?? NaN),
    spreadBps: stats?.spreadBps ?? NaN,
    bidDepth: book?.bids.length ? stats.bidDepth[bandBps] : NaN,
    askDepth: book?.asks.length ? stats.askDepth[bandBps] : NaN,
    bidReach: book?.bids.length ? reachBps(book.bids, mid) : NaN,
    askReach: book?.asks.length ? reachBps(book.asks, mid) : NaN,
    bandBps,
    takerBps: fees.takerBps, makerBps: fees.makerBps,
    buyBps: buy?.totalBps ?? NaN, sellBps: sell?.totalBps ?? NaN,
    buyCov: buy?.coverage ?? NaN, sellCov: sell?.coverage ?? NaN,
    buyAgg: Boolean(buy?.deepestAgg), sellAgg: Boolean(sell?.deepestAgg),
    vol24h: m.ctx?.vol24h ?? NaN, oiUsd: m.ctx?.oiUsd ?? NaN, funding: m.ctx?.funding ?? NaN,
    ageMs: m.bookAt ? now - m.bookAt : NaN, live: m.bookSource === 'ws',
  };
}

/**
 * Cost cell semantics shared with the detail panel and the curve:
 *   exact          → "1.23"
 *   full fill, ends in an aggregated bucket → "≈ 1.23" (upper bound beyond the fine levels)
 *   partial fill, fine levels only          → "≥ 1.23" (cost of the fillable part, a lower bound)
 *   partial fill through aggregated buckets → "≈ 1.23" (fillable part at bucket edges, no strict bound)
 */
export function costMarker(cov, agg) {
  const partial = Number.isFinite(cov) && cov < 0.999;
  if (partial && !agg) return '≥ ';
  if (agg) return '≈ ';
  return '';
}
export function costExplanation(cov, agg) {
  const partial = Number.isFinite(cov) && cov < 0.999;
  if (partial && !agg) return `Only ${(cov * 100).toFixed(0)} % of the size is fillable in the visible book; cost of the fillable part, a lower bound for the whole order`;
  if (partial && agg) return `Only ${(cov * 100).toFixed(0)} % of the size is fillable in the visible book; the fillable part is priced at conservative aggregated-bucket edges, so this is an estimate, not a strict bound`;
  if (agg) return 'Reaches into aggregated buckets: exact inside the full-precision levels, upper bound beyond';
  return '';
}
const costText = (bps, cov, agg) => (Number.isFinite(bps) ? costMarker(cov, agg) + fmtBps(bps, 2) : '–');
const costClass = (cov, agg) => ((Number.isFinite(cov) && cov < 0.999) || agg ? 'partial' : '');

const depthText = (usd, reach, band) => {
  if (!Number.isFinite(usd)) return '–';
  return (Number.isFinite(reach) && reach < band ? '≥ ' : '') + fmtUsd(usd);
};
const depthTitle = (reach, band) => (Number.isFinite(reach) && reach < band
  ? `The visible book reaches only ${reach.toFixed(1)} bps from the mid; the band is not fully covered`
  : '');

export const COLUMNS = [
  { key: 'name', label: 'Market', align: 'left', sticky: true, get: (r) => r.name, text: () => '', cmp: 'string' },
  { key: 'mid', label: 'Mid', get: (r) => r.mid, text: (r) => fmtPx(r.mid) },
  { key: 'spreadBps', label: 'Spread', unit: 'bps', get: (r) => r.spreadBps, text: (r) => fmtBps(r.spreadBps, 2) },
  { key: 'bidDepth', label: 'Bid depth', unit: (o) => `within ${o.bandBps} bps`, get: (r) => r.bidDepth, text: (r) => depthText(r.bidDepth, r.bidReach, r.bandBps), title: (r) => depthTitle(r.bidReach, r.bandBps), cls: (r) => (Number.isFinite(r.bidReach) && r.bidReach < r.bandBps ? 'partial' : '') },
  { key: 'askDepth', label: 'Ask depth', unit: (o) => `within ${o.bandBps} bps`, get: (r) => r.askDepth, text: (r) => depthText(r.askDepth, r.askReach, r.bandBps), title: (r) => depthTitle(r.askReach, r.bandBps), cls: (r) => (Number.isFinite(r.askReach) && r.askReach < r.bandBps ? 'partial' : '') },
  { key: 'takerBps', label: 'Taker fee', unit: 'bps', get: (r) => r.takerBps, text: (r) => fmtBps(r.takerBps, 2) },
  { key: 'makerBps', label: 'Maker fee', unit: 'bps', get: (r) => r.makerBps, text: (r) => fmtBps(r.makerBps, 2) },
  { key: 'buyBps', label: (o) => `Buy ${fmtSizeLabel(o.sizeUsd)}`, unit: 'market · bps', get: (r) => r.buyBps, text: (r) => costText(r.buyBps, r.buyCov, r.buyAgg), title: (r) => costExplanation(r.buyCov, r.buyAgg), cls: (r) => costClass(r.buyCov, r.buyAgg) },
  { key: 'sellBps', label: (o) => `Sell ${fmtSizeLabel(o.sizeUsd)}`, unit: 'market · bps', get: (r) => r.sellBps, text: (r) => costText(r.sellBps, r.sellCov, r.sellAgg), title: (r) => costExplanation(r.sellCov, r.sellAgg), cls: (r) => costClass(r.sellCov, r.sellAgg) },
  { key: 'vol24h', label: '24h volume', get: (r) => r.vol24h, text: (r) => fmtUsd(r.vol24h) },
  { key: 'oiUsd', label: 'Open interest', get: (r) => r.oiUsd, text: (r) => fmtUsd(r.oiUsd) },
  { key: 'ageMs', label: 'Book age', get: (r) => r.ageMs, text: (r) => (r.live ? 'live' : fmtAge(r.ageMs)), cls: (r) => (r.live ? 'live' : '') },
];

function compare(col, dir) {
  const sign = dir === 'asc' ? 1 : -1;
  return (a, b) => {
    const va = col.get(a), vb = col.get(b);
    if (col.cmp === 'string') return sign * String(va).localeCompare(String(vb));
    const na = Number.isFinite(va), nb = Number.isFinite(vb);
    if (!na && !nb) return 0;
    if (!na) return 1;   // NaN always last
    if (!nb) return -1;
    return sign * (va - vb);
  };
}

export function createTable(table, { onSelect, onSort }) {
  const wrap = table.parentElement;
  const thead = table.createTHead();
  const headRow = thead.insertRow();
  const headCells = new Map();
  for (const col of COLUMNS) {
    const th = document.createElement('th');
    th.scope = 'col';
    th.dataset.key = col.key;
    if (col.align === 'left') th.classList.add('left');
    if (col.sticky) th.classList.add('sticky');
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'th-btn';
    const label = document.createElement('span'); label.className = 'th-label';
    const unit = document.createElement('span'); unit.className = 'th-unit';
    btn.append(label, unit);
    btn.addEventListener('click', () => onSort?.(col.key));
    th.append(btn);
    headRow.append(th);
    headCells.set(col.key, { th, label, unit });
  }
  const tbody = table.createTBody();
  const rows = new Map(); // name → { tr, cells: Map<key, td>, last: Map<key,string> }

  function ensureRow(r) {
    let entry = rows.get(r.name);
    if (entry) return entry;
    const tr = document.createElement('tr');
    tr.dataset.name = r.name;
    tr.tabIndex = 0;
    const cells = new Map();
    for (const col of COLUMNS) {
      const td = document.createElement('td');
      if (col.align === 'left') td.classList.add('left');
      if (col.sticky) td.classList.add('sticky');
      if (col.key === 'name') {
        const base = document.createElement('span'); base.className = 'mkt-base'; base.textContent = r.base;
        const dex = document.createElement('span'); dex.className = 'badge dex'; dex.dataset.dex = r.dex || 'hl'; dex.textContent = r.dexLabel;
        const cls = document.createElement('span'); cls.className = 'chip cls'; cls.textContent = ASSET_CLASSES[r.cls]?.label ?? r.cls;
        td.append(base, dex, cls);
        if (r.isDelisted) { const d = document.createElement('span'); d.className = 'chip delisted'; d.textContent = 'delisted'; td.append(d); }
      }
      tr.append(td);
      cells.set(col.key, td);
    }
    const select = () => onSelect?.(r.name);
    tr.addEventListener('click', select);
    tr.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); select(); } });
    entry = { tr, cells, last: new Map() };
    rows.set(r.name, entry);
    return entry;
  }

  return {
    /**
     * @param {Array} list row models already filtered
     * @param {object} opts { sort:{key,dir}, selected, sizeUsd, bandBps }
     * @returns {string[]} the market names in display order
     */
    update(list, opts) {
      const col = COLUMNS.find((c) => c.key === opts.sort.key) ?? COLUMNS[0];
      const sorted = list.slice().sort(compare(col, opts.sort.dir));
      for (const [key, { th, label, unit }] of headCells) {
        const c = COLUMNS.find((x) => x.key === key);
        const l = typeof c.label === 'function' ? c.label(opts) : c.label;
        const u = typeof c.unit === 'function' ? c.unit(opts) : (c.unit ?? '');
        if (label.textContent !== l) label.textContent = l;
        if (unit.textContent !== u) unit.textContent = u;
        th.setAttribute('aria-sort', key === opts.sort.key ? (opts.sort.dir === 'asc' ? 'ascending' : 'descending') : 'none');
      }
      // Rows are only moved when their position changes; a detached row loses focus and
      // Chromium then scrolls the container back to the top.
      const active = document.activeElement;
      const scrollTop = wrap ? wrap.scrollTop : 0;
      const keep = new Set(sorted.map((r) => r.name));
      for (const [name, entry] of rows) if (!keep.has(name) && entry.tr.parentNode) entry.tr.remove();
      sorted.forEach((r, i) => {
        const entry = ensureRow(r);
        entry.tr.classList.toggle('selected', r.name === opts.selected);
        entry.tr.classList.toggle('delisted', Boolean(r.isDelisted));
        entry.tr.setAttribute('aria-selected', r.name === opts.selected ? 'true' : 'false');
        for (const c of COLUMNS) {
          if (c.key === 'name') continue;
          const td = entry.cells.get(c.key);
          const text = c.text(r);
          if (entry.last.get(c.key) !== text) { td.textContent = text; entry.last.set(c.key, text); }
          const title = c.title ? c.title(r) : '';
          if ((td.title || '') !== title) td.title = title;
          const cls = c.cls ? c.cls(r) : '';
          const want = cls ? `num ${cls}` : 'num';
          if (td.className !== want) td.className = want;
        }
        const current = tbody.children[i];
        if (current !== entry.tr) tbody.insertBefore(entry.tr, current ?? null);
      });
      if (active && active !== document.body && !active.isConnected) { /* removed by a filter: nothing to restore */ }
      else if (active && active !== document.activeElement && active.isConnected) active.focus({ preventScroll: true });
      if (wrap && wrap.scrollTop !== scrollTop) wrap.scrollTop = scrollTop;
      return sorted.map((r) => r.name);
    },
    /** Scroll the table container (never the page) so the row is visible. */
    scrollTo(name) {
      const tr = rows.get(name)?.tr;
      if (!tr || !wrap || !tr.isConnected) return;
      const top = tr.offsetTop - (thead.offsetHeight || 0);
      const bottom = tr.offsetTop + tr.offsetHeight;
      if (top < wrap.scrollTop || bottom > wrap.scrollTop + wrap.clientHeight) {
        wrap.scrollTop = Math.max(0, tr.offsetTop - wrap.clientHeight / 2);
      }
    },
  };
}
