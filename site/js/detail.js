// The selected-market panel: header, KPIs, CLOB, cost per order type, cost curve,
// paper benchmark and the fee derivation. Everything updates in place so tooltips,
// text selection and focus survive the frequent live refreshes.
import { fmtUsd, fmtBps, fmtPx, fmtPct, fmtSizeLabel, fmtInt } from './format.js';
import { midOf, spreadBpsOf } from './book.js';
import { orderTypeGroups } from './ordertypes.js';
import { paperFor } from './paper.js';
import { createClob } from './clob.js';
import { createCurve } from './curve.js';
import { ASSET_CLASSES } from './universe.js';
import { costMarker, costExplanation } from './table.js';
import { CURVE_SIZES, PAPER } from './config.js';

function el(tag, cls, parent, txt) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (txt != null) e.textContent = txt;
  parent?.append(e);
  return e;
}
function setText(node, txt) { if (node.textContent !== txt) node.textContent = txt; }
function setTitle(node, txt) { if ((node.title || '') !== (txt || '')) node.title = txt || ''; }
function setClass(node, cls) { if (node.className !== cls) node.className = cls; }

const NS = 'http://www.w3.org/2000/svg';
const BAR_W = 120, BAR_H = 10, BAR_MIN = 1.5;

/** Diverging mini stacked bar: negative components left of zero, positive right. */
function drawMiniBar(svgEl, parts, scale) {
  svgEl.replaceChildren();
  const mid = BAR_W / 2;
  const px = (v) => Math.max(BAR_MIN, (Math.abs(v) / scale) * (mid - 2)); // tiny components stay visible
  let posX = mid + 1, negX = mid - 1;
  for (const [cls, v] of parts) {
    if (!Number.isFinite(v) || Math.abs(v) < 1e-9) continue;
    const w = px(v);
    const r = document.createElementNS(NS, 'rect');
    r.setAttribute('y', 1); r.setAttribute('height', BAR_H - 2); r.setAttribute('rx', 2);
    r.classList.add(cls);
    if (v > 0) { r.setAttribute('x', posX); r.setAttribute('width', w); posX += w + 2; }
    else { negX -= w; r.setAttribute('x', negX); r.setAttribute('width', w); negX -= 2; }
    svgEl.append(r);
  }
  const zero = document.createElementNS(NS, 'line');
  zero.setAttribute('x1', mid); zero.setAttribute('x2', mid); zero.setAttribute('y1', 0); zero.setAttribute('y2', BAR_H);
  zero.classList.add('zero');
  svgEl.append(zero);
}

function miniBarEl() {
  const s = document.createElementNS(NS, 'svg');
  s.setAttribute('viewBox', `0 0 ${BAR_W} ${BAR_H}`);
  s.setAttribute('width', BAR_W); s.setAttribute('height', BAR_H);
  s.classList.add('minibar');
  s.setAttribute('aria-hidden', 'true');
  return s;
}

export function createDetail(root, handlers) {
  root.classList.add('detail-panel');
  const head = el('div', 'detail-head', root);
  const titleRow = el('div', 'detail-title', head);
  const h2 = el('h2', null, titleRow, '—');
  const badges = el('span', 'badges', titleRow);
  const badgeDex = el('span', 'badge dex', badges, '');
  const badgeCls = el('span', 'chip cls', badges, '');
  const badgeDelisted = el('span', 'chip delisted', badges, 'delisted');
  const liveDot = el('span', 'livedot', badges);
  const subline = el('p', 'detail-sub', head, 'Select a market in the table.');

  const kpis = el('div', 'kpis', root);
  const tiles = {};
  for (const [id, label] of [['mid', 'Mid'], ['spread', 'Spread (bps)'], ['vol', '24h volume'], ['oi', 'Open interest'], ['funding', 'Funding / 1h'], ['fees', 'Taker · maker (bps)']]) {
    const t = el('div', 'stat', kpis);
    el('div', 'label', t, label);
    tiles[id] = el('div', 'value', t, '–');
  }

  const clobCard = el('section', 'card viz-card', root);
  const clob = createClob(clobCard, { onPrecisionChange: handlers.onPrecisionChange, onViewChange: handlers.onClobViewChange });

  const otCard = el('section', 'card', root);
  const otHead = el('div', 'viz-head', otCard);
  const otTitle = el('div', 'viz-title', otHead);
  el('h3', null, otTitle, 'Cost per order type');
  const otSub = el('span', 'viz-sub', otTitle, '');
  const otLegend = el('div', 'legend', otHead);
  for (const [cls, label] of [['spread', 'Spread'], ['impact', 'Impact'], ['fee', 'Fee']]) {
    const item = el('span', 'legend-item', otLegend);
    el('span', `swatch ${cls}`, item);
    el('span', null, item, label);
  }
  const sideSeg = el('div', 'seg', el('div', 'viz-controls', otHead));
  const sideBtns = {};
  for (const [id, label] of [['BUY', 'Buy'], ['SELL', 'Sell']]) {
    const b = el('button', 'seg-btn', sideSeg, label);
    b.type = 'button';
    b.addEventListener('click', () => handlers.onSideChange?.(id));
    sideBtns[id] = b;
  }
  const otWrap = el('div', 'table-scroll', otCard);
  const otTable = el('table', 'data-table ordertypes', otWrap);
  const otHeadRow = otTable.createTHead().insertRow();
  for (const [h, cls] of [['Order type', 'left'], ['Liquidity', 'left'], ['Total bps', ''], ['Total $', ''], ['Spread', ''], ['Impact', ''], ['Fee', ''], ['Fillable', ''], ['Decomposition', 'left']]) {
    const th = el('th', cls || null, otHeadRow, h); th.scope = 'col';
  }
  const otBody = otTable.createTBody();
  const otRows = new Map(); // `${group}|${row}` → { tr, name, note, liq, total, usd, spread, impact, fee, fill, bar }
  const otEmpty = el('p', 'note', otCard, '');

  const curveCard = el('section', 'card viz-card', root);
  const curve = createCurve(curveCard, { onViewChange: handlers.onCurveViewChange });

  const paperCard = el('section', 'card', root);
  const paperHead = el('div', 'viz-head', paperCard);
  const paperTitle = el('div', 'viz-title', paperHead);
  el('h3', null, paperTitle, 'Paper benchmark');
  const paperSub = el('span', 'viz-sub', paperTitle, '');
  const paperWrap = el('div', 'table-scroll', paperCard);
  const paperNote = el('p', 'note', paperCard, '');

  const feeCard = el('details', 'card fee-derivation', root);
  el('summary', null, feeCard, 'How the fee is derived');
  const feeBody = el('div', 'fee-body', feeCard);
  const feeDl = el('dl', 'fee-steps', feeBody);
  const feeRows = {};
  for (const key of ['base', 'growth', 'scale', 'referral', 'aligned', 'builder', 'result']) {
    feeRows[key] = { dt: el('dt', null, feeDl, ''), dd: el('dd', null, feeDl, '') };
  }
  el('p', 'note', feeBody, 'Formula from the Hyperliquid fee documentation, calibrated as in docs/FEE_CALIBRATION.md. Growth mode and the deployer fee scale are read live from the exchange for every market.');

  let lastPaperKey = null;
  let lastMarketName = null;

  function renderFees(market, fees) {
    const s = fees.steps;
    const set = (key, dt, dd) => { setText(feeRows[key].dt, dt); setText(feeRows[key].dd, dd); };
    set('base', 'Base taker · maker', `${fmtBps(s.baseTaker)} · ${fmtBps(s.baseMaker)} bps (${s.tier.label}${s.staking.discount ? `, ${s.staking.label}` : ''}${s.rebate.makerBps != null ? `, ${s.rebate.label}` : ''})`);
    if (s.isHip3) {
      set('growth', '× growth mode', s.growthModeScale === 1 ? '1 (growth mode off)' : '0.1 (growth mode on: fees cut by 90 %)');
      set('scale', '× scaleIfHip3', `${s.scaleIfHip3.toFixed(4).replace(/\.?0+$/, '')} (deployer fee scale δ = ${market.deployerFeeScale}; deployer share ${(s.deployerShare * 100).toFixed(1)} %)`);
      set('aligned', '× aligned quote', s.aligned ? `taker ${s.alignedTakerScale.toFixed(3)} · rebate ${s.alignedRebateScale.toFixed(3)} (${market.quote} is an aligned quote asset)` : `1 (${market.quote} is not an aligned quote asset)`);
    } else {
      set('growth', 'HIP-3 scaling', 'none (validator-operated perp)');
      set('scale', '× scaleIfHip3', '1');
      set('aligned', '× aligned quote', '1 (USDC-quoted validator perp)');
    }
    set('referral', '× (1 − referral)', s.referral ? `${(1 - s.referral).toFixed(2)} (referred user, first $25M)` : '1 (no referral)');
    set('builder', '+ builder fee', `${fmtBps(s.builder)} bps on every fill`);
    set('result', '= effective', `taker ${fmtBps(fees.takerBps)} bps · maker ${fmtBps(fees.makerBps)} bps`);
  }

  function ensureOtRows(groups) {
    const wanted = new Set();
    for (const g of groups) {
      wanted.add(`group|${g.id}`);
      for (const r of g.rows) wanted.add(`${g.id}|${r.id}`);
    }
    const sameSet = otRows.size === wanted.size && [...wanted].every((k) => otRows.has(k));
    if (sameSet) return;
    otBody.replaceChildren();
    otRows.clear();
    for (const g of groups) {
      const gr = otBody.insertRow();
      gr.className = 'group';
      const td = gr.insertCell(); td.colSpan = 9; td.textContent = g.label;
      otRows.set(`group|${g.id}`, { tr: gr });
      for (const r of g.rows) {
        const tr = otBody.insertRow();
        const name = tr.insertCell(); name.className = 'left';
        const label = el('div', 'ot-label', name, r.label);
        const note = el('div', 'ot-note', name, r.note);
        const liqCell = tr.insertCell(); liqCell.className = 'left';
        const liq = el('span', `chip liq ${r.liquidity}`, liqCell, r.liquidity);
        const mk = (cls) => { const c = tr.insertCell(); c.className = cls; return c; };
        const total = mk('num total'), usd = mk('num'), spread = mk('num'), impact = mk('num'), fee = mk('num'), fill = mk('num');
        const barCell = mk('left');
        const bar = miniBarEl();
        barCell.append(bar);
        otRows.set(`${g.id}|${r.id}`, { tr, label, note, liq, total, usd, spread, impact, fee, fill, bar, barCell });
      }
    }
  }

  function renderOrderTypes(groups, side, sizeUsd) {
    ensureOtRows(groups);
    for (const g of groups) {
      // One bar scale per group, so a 25 bps scale-order improvement does not flatten the taker rows.
      let scale = 0;
      for (const r of g.rows.map((row) => row[side === 'BUY' ? 'buy' : 'sell']).filter(Boolean)) {
        const pos = [r.spreadBps, r.impactBps, r.feeBps].filter((v) => v > 0).reduce((a, b) => a + b, 0);
        const neg = [r.spreadBps, r.impactBps, r.feeBps].filter((v) => v < 0).reduce((a, b) => a - b, 0);
        scale = Math.max(scale, pos, neg);
      }
      scale = scale || 1;
      for (const r of g.rows) {
        const cells = otRows.get(`${g.id}|${r.id}`);
        const res = r[side === 'BUY' ? 'buy' : 'sell'];
        setText(cells.label, r.label);
        setText(cells.note, r.note);
        if (!res) {
          for (const c of [cells.total, cells.usd, cells.spread, cells.impact, cells.fee, cells.fill]) { setText(c, '–'); setTitle(c, ''); }
          setClass(cells.total, 'num total');
          setClass(cells.fill, 'num');
          cells.bar.hidden = true;
          continue;
        }
        cells.bar.hidden = false;
        const partial = res.coverage < 0.999;
        const marker = costMarker(res.coverage, res.deepestAgg);
        setText(cells.total, marker + fmtBps(res.totalBps));
        setClass(cells.total, `num total${marker ? ' partial' : ''}`);
        setTitle(cells.total, costExplanation(res.coverage, res.deepestAgg));
        setText(cells.usd, marker + fmtUsd(res.totalUsd));
        setText(cells.spread, fmtBps(res.spreadBps));
        setText(cells.impact, fmtBps(res.impactBps));
        setText(cells.fee, fmtBps(res.feeBps));
        setClass(cells.fill, `num${partial ? ' partial' : ''}`);
        setText(cells.fill, res.liquidity === 'maker' ? `queue ${fmtUsd(res.queueAheadUsd)}` : fmtPct(res.coverage * 100, 0));
        setTitle(cells.fill, res.liquidity === 'maker' ? 'Notional resting ahead of you at the touch' : 'Share of the order fillable within the visible book');
        drawMiniBar(cells.bar, [['spread', res.spreadBps], ['impact', res.impactBps], ['fee', res.feeBps]], scale);
      }
    }
    setText(otSub, `${side === 'BUY' ? 'Buy' : 'Sell'} ${fmtSizeLabel(sizeUsd)} · bps of filled notional vs. the arrival mid · opportunity cost excluded · ≈ reaches aggregated depth, ≥ partly fillable`);
  }

  function renderPaper(market, paper) {
    paperWrap.replaceChildren();
    const p = paper ? paperFor(paper, market.paperKey) : null;
    if (!paper) {
      paperSub.textContent = '';
      paperNote.textContent = 'Paper results (results/summary_stats.csv) are not available on this deployment.';
      return;
    }
    if (!p) {
      paperSub.textContent = PAPER.period;
      paperNote.textContent = `${market.name} was not part of the paper's 101-market sample.`;
      return;
    }
    paperSub.textContent = `${market.paperKey} · median total implementation shortfall (bps), buy / sell · ${PAPER.period}`;
    const t = el('table', 'data-table paper-table', paperWrap);
    const hr = t.createTHead().insertRow();
    const th0 = el('th', 'left', hr, 'Strategy'); th0.scope = 'col';
    for (const s of p.sizes) { const th = el('th', null, hr, fmtSizeLabel(s)); th.scope = 'col'; }
    const tb = t.createTBody();
    let nObs = 0;
    for (const strat of p.strategies) {
      const tr = tb.insertRow();
      const c0 = tr.insertCell(); c0.className = 'left'; c0.textContent = strat;
      for (const s of p.sizes) {
        const b = p.cell(strat, 'BUY', s), sl = p.cell(strat, 'SELL', s);
        nObs = Math.max(nObs, b?.nObs ?? 0);
        const c = tr.insertCell(); c.className = 'num';
        c.textContent = `${b ? fmtBps(b.median) : '–'} / ${sl ? fmtBps(sl.median) : '–'}`;
        if (b) c.title = `Buy: median ${fmtBps(b.median)} bps (p05 ${fmtBps(b.p05)}, p95 ${fmtBps(b.p95)}), fill ${fmtPct(b.completion, 0)}; spread ${fmtBps(b.spread)}, impact ${fmtBps(b.impact)}, fee ${fmtBps(b.fee)}, opportunity ${fmtBps(b.opp)}`;
      }
    }
    paperNote.textContent = `Includes opportunity cost and uses the paper's Tier 2 fee calibration; n ≈ ${fmtInt(nObs)} snapshots per cell.`;
  }

  return {
    render(ctx) {
      const { market, fineBook, costBook, displayBook, fees, sizeUsd, paper, precision, clobView, curveView, side } = ctx;
      if (!market) {
        setText(h2, '—');
        setText(subline, 'Select a market in the table.');
        return;
      }
      if (market.name !== lastMarketName) {
        lastMarketName = market.name;
        setText(h2, market.name);
        setText(badgeDex, market.dexLabel);
        badgeDex.dataset.dex = market.dex || 'hl';
        setText(badgeCls, ASSET_CLASSES[market.cls]?.label ?? market.cls);
      }
      badgeDelisted.hidden = !market.isDelisted;
      const streaming = Boolean(fineBook) && market.bookSource === 'ws';
      setClass(liveDot, `livedot${streaming ? ' on' : ''}`);
      setTitle(liveDot, streaming ? 'Order book streamed over WebSocket, top of book per block' : 'Order book from REST polling');
      const feeBits = market.isHip3
        ? `growth mode ${market.growthMode ? 'on' : 'off'} · fee scale δ ${Number.isFinite(market.deployerFeeScale) ? market.deployerFeeScale : '–'}${market.aligned ? ' · aligned quote' : ''}`
        : 'validator-operated perp';
      setText(subline, `${market.dexFullName} · quoted in ${market.quote} · ${feeBits} · max leverage ${market.maxLeverage ?? '–'}×`);

      const book = costBook;
      const twoSided = Boolean(book?.bids.length && book?.asks.length);
      const mid = twoSided ? midOf(book) : NaN;
      setText(tiles.mid, Number.isFinite(mid) ? fmtPx(mid) : (Number.isFinite(market.ctx?.mid) ? fmtPx(market.ctx.mid) : '–'));
      setText(tiles.spread, fmtBps(spreadBpsOf(fineBook ?? book)));
      setText(tiles.vol, fmtUsd(market.ctx?.vol24h));
      setText(tiles.oi, fmtUsd(market.ctx?.oiUsd));
      setText(tiles.funding, Number.isFinite(market.ctx?.funding) ? fmtPct(market.ctx.funding * 100, 4) : '–');
      setText(tiles.fees, `${fmtBps(fees.takerBps)} · ${fmtBps(fees.makerBps)}`);

      const groups = twoSided ? orderTypeGroups({ book, sizeUsd, fees }) : [];
      const marketRow = groups[0]?.rows[0];
      clob.render({ market, book: displayBook, sizeUsd, buy: marketRow?.buy, sell: marketRow?.sell, precision, view: clobView });
      for (const [id, b] of Object.entries(sideBtns)) b.classList.toggle('active', id === side);
      if (twoSided) {
        renderOrderTypes(groups, side, sizeUsd);
        otEmpty.textContent = '';
      } else {
        otBody.replaceChildren(); otRows.clear();
        setText(otSub, '');
        otEmpty.textContent = market.isDelisted ? 'Delisted market — no live book.' : (book ? 'The book is one-sided right now; costs need both a bid and an ask.' : 'Waiting for the order book…');
      }

      const paperM = paper ? paperFor(paper, market.paperKey) : null;
      const sizes = [...new Set([...CURVE_SIZES, ...(paperM?.sizes ?? [])])].sort((a, b) => a - b);
      curve.render({ book: twoSided ? book : null, fees, sizes, paper: paperM, paperPeriod: PAPER.period, sizeUsd, view: curveView });

      const key = `${market.name}|${paper ? 1 : 0}`;
      if (key !== lastPaperKey) { renderPaper(market, paper); lastPaperKey = key; }
      renderFees(market, fees);
    },
  };
}
