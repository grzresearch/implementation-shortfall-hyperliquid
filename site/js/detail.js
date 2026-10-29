// The selected-market panel: header, KPIs, CLOB, cost per order type, cost curve,
// paper benchmark and the fee derivation.
import { fmtUsd, fmtBps, fmtPx, fmtPct, fmtSizeLabel, fmtInt } from './format.js';
import { midOf, spreadBpsOf } from './book.js';
import { orderTypeGroups } from './ordertypes.js';
import { paperFor } from './paper.js';
import { createClob } from './clob.js';
import { createCurve } from './curve.js';
import { ASSET_CLASSES } from './universe.js';
import { CURVE_SIZES, PAPER } from './config.js';

function el(tag, cls, parent, txt) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (txt != null) e.textContent = txt;
  parent?.append(e);
  return e;
}

const NS = 'http://www.w3.org/2000/svg';

/** Diverging mini stacked bar: negative components left of zero, positive right. */
function miniBar(parts, scale) {
  const W = 120, H = 10, mid = W / 2;
  const s = document.createElementNS(NS, 'svg');
  s.setAttribute('viewBox', `0 0 ${W} ${H}`);
  s.setAttribute('width', W); s.setAttribute('height', H);
  s.classList.add('minibar');
  s.setAttribute('aria-hidden', 'true');
  const px = (v) => (Math.abs(v) / scale) * (mid - 2);
  let posX = mid + 1, negX = mid - 1;
  for (const [cls, v] of parts) {
    if (!Number.isFinite(v) || Math.abs(v) < 1e-9) continue;
    const w = Math.max(0, px(v) - 2);
    const r = document.createElementNS(NS, 'rect');
    r.setAttribute('y', 1); r.setAttribute('height', H - 2);
    r.setAttribute('rx', 2);
    r.classList.add(cls);
    if (v > 0) { r.setAttribute('x', posX); r.setAttribute('width', w); posX += w + 2; }
    else { negX -= w; r.setAttribute('x', negX); r.setAttribute('width', w); negX -= 2; }
    s.append(r);
  }
  const zero = document.createElementNS(NS, 'line');
  zero.setAttribute('x1', mid); zero.setAttribute('x2', mid); zero.setAttribute('y1', 0); zero.setAttribute('y2', H);
  zero.classList.add('zero');
  s.append(zero);
  return s;
}

export function createDetail(root, handlers) {
  root.classList.add('detail-panel');
  const head = el('div', 'detail-head', root);
  const titleRow = el('div', 'detail-title', head);
  const h2 = el('h2', null, titleRow, '—');
  const badges = el('span', 'badges', titleRow);
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

  let lastKey = null;

  function renderFees(market, fees) {
    feeBody.replaceChildren();
    const s = fees.steps;
    const dl = el('dl', 'fee-steps', feeBody);
    const row = (k, v) => { el('dt', null, dl, k); el('dd', null, dl, v); };
    row('Base taker · maker', `${fmtBps(s.baseTaker)} · ${fmtBps(s.baseMaker)} bps (${s.tier.label}${s.staking.discount ? `, ${s.staking.label}` : ''}${s.rebate.makerBps != null ? `, ${s.rebate.label}` : ''})`);
    if (s.isHip3) {
      row('× growth mode', s.growthModeScale === 1 ? '1 (growth mode off)' : '0.1 (growth mode on: fees cut by 90 %)');
      row('× scaleIfHip3', `${s.scaleIfHip3.toFixed(4).replace(/\.?0+$/, '')} (deployer fee scale δ = ${market.deployerFeeScale}; deployer share ${(s.deployerShare * 100).toFixed(1)} %)`);
    } else {
      row('HIP-3 scaling', 'none (validator-operated perp)');
    }
    row('× (1 − referral)', s.referral ? `${(1 - s.referral).toFixed(2)} (referred user, first $25M)` : '1 (no referral)');
    if (s.isHip3) row('× aligned quote', s.aligned ? `taker ${s.alignedTakerScale.toFixed(3)} · rebate ${s.alignedRebateScale.toFixed(3)} (${market.quote} is an aligned quote asset)` : `1 (${market.quote} is not an aligned quote asset)`);
    row('+ builder fee', `${fmtBps(s.builder)} bps on every fill`);
    row('= effective', `taker ${fmtBps(fees.takerBps)} bps · maker ${fmtBps(fees.makerBps)} bps`);
    const p = el('p', 'note', feeBody);
    p.textContent = 'Formula from the Hyperliquid fee documentation, calibrated as in docs/FEE_CALIBRATION.md. Growth mode and the deployer fee scale are read live from the exchange for every market.';
  }

  function renderOrderTypes(groups, side, sizeUsd) {
    otBody.replaceChildren();
    for (const g of groups) {
      // One bar scale per group, so a 25 bps scale-order improvement does not flatten the taker rows.
      let scale = 0;
      for (const r of g.rows.map((row) => row[side === 'BUY' ? 'buy' : 'sell']).filter(Boolean)) {
        const pos = [r.spreadBps, r.impactBps, r.feeBps].filter((v) => v > 0).reduce((a, b) => a + b, 0);
        const neg = [r.spreadBps, r.impactBps, r.feeBps].filter((v) => v < 0).reduce((a, b) => a - b, 0);
        scale = Math.max(scale, pos, neg);
      }
      scale = scale || 1;
      const gr = otBody.insertRow();
      gr.className = 'group';
      const td = gr.insertCell(); td.colSpan = 9; td.textContent = g.label;
      for (const r of g.rows) {
        const res = r[side === 'BUY' ? 'buy' : 'sell'];
        const tr = otBody.insertRow();
        const name = tr.insertCell(); name.className = 'left';
        el('div', 'ot-label', name, r.label);
        el('div', 'ot-note', name, r.note);
        const liq = tr.insertCell(); liq.className = 'left';
        el('span', `chip liq ${r.liquidity}`, liq, r.liquidity);
        const cells = [];
        if (!res) {
          for (let i = 0; i < 6; i++) { const c = tr.insertCell(); c.className = 'num'; c.textContent = i === 5 ? '' : '–'; cells.push(c); }
          cells[5].className = 'left';
          continue;
        }
        const partial = res.coverage < 0.999;
        const pre = partial ? '≥ ' : (res.deepestAgg ? '≈ ' : '');
        const total = tr.insertCell(); total.className = `num total${partial ? ' partial' : ''}`; total.textContent = pre + fmtBps(res.totalBps);
        if (res.deepestAgg && !partial) total.title = 'Reaches into aggregated buckets: exact inside the full-precision levels, upper bound beyond';
        const usd = tr.insertCell(); usd.className = 'num'; usd.textContent = pre + fmtUsd(res.totalUsd);
        for (const v of [res.spreadBps, res.impactBps, res.feeBps]) { const c = tr.insertCell(); c.className = 'num'; c.textContent = fmtBps(v); }
        const fill = tr.insertCell(); fill.className = `num${partial ? ' partial' : ''}`;
        fill.textContent = res.liquidity === 'maker' ? `queue ${fmtUsd(res.queueAheadUsd)}` : fmtPct(res.coverage * 100, 0);
        fill.title = res.liquidity === 'maker' ? 'Notional resting ahead of you at the touch' : 'Share of the order fillable within the 20 visible levels';
        const bar = tr.insertCell(); bar.className = 'left';
        bar.append(miniBar([['spread', res.spreadBps], ['impact', res.impactBps], ['fee', res.feeBps]], scale));
      }
    }
    otSub.textContent = `${side === 'BUY' ? 'Buy' : 'Sell'} ${fmtSizeLabel(sizeUsd)} · bps of filled notional vs. the arrival mid · opportunity cost excluded · ≈ reaches aggregated depth`;
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
      const book = costBook;
      if (!market) {
        h2.textContent = '—';
        subline.textContent = 'Select a market in the table.';
        return;
      }
      h2.textContent = market.name;
      badges.replaceChildren();
      const dex = el('span', 'badge dex', badges, market.dexLabel); dex.dataset.dex = market.dex || 'hl';
      el('span', 'chip cls', badges, ASSET_CLASSES[market.cls]?.label ?? market.cls);
      if (market.isDelisted) el('span', 'chip delisted', badges, 'delisted');
      const liveDot = el('span', `livedot ${fineBook && market.bookSource === 'ws' ? 'on' : ''}`, badges);
      liveDot.title = market.bookSource === 'ws' ? 'Order book streamed over WebSocket' : 'Order book from the REST sweep';
      const feeBits = market.isHip3
        ? `growth mode ${market.growthMode ? 'on' : 'off'} · fee scale δ ${Number.isFinite(market.deployerFeeScale) ? market.deployerFeeScale : '–'}${market.aligned ? ' · aligned quote' : ''}`
        : 'validator-operated perp';
      subline.textContent = `${market.dexFullName} · quoted in ${market.quote} · ${feeBits} · max leverage ${market.maxLeverage ?? '–'}×`;

      const mid = midOf(book);
      tiles.mid.textContent = Number.isFinite(mid) ? fmtPx(mid) : (Number.isFinite(market.ctx?.mid) ? fmtPx(market.ctx.mid) : '–');
      tiles.spread.textContent = fmtBps(spreadBpsOf(fineBook ?? book));
      tiles.vol.textContent = fmtUsd(market.ctx?.vol24h);
      tiles.oi.textContent = fmtUsd(market.ctx?.oiUsd);
      tiles.funding.textContent = Number.isFinite(market.ctx?.funding) ? fmtPct(market.ctx.funding * 100, 4) : '–';
      tiles.fees.textContent = `${fmtBps(fees.takerBps)} · ${fmtBps(fees.makerBps)}`;

      const groups = book ? orderTypeGroups({ book, sizeUsd, fees }) : [];
      const marketRow = groups[0]?.rows[0];
      clob.render({ market, book: displayBook, sizeUsd, buy: marketRow?.buy, sell: marketRow?.sell, precision, view: clobView });
      for (const [id, b] of Object.entries(sideBtns)) b.classList.toggle('active', id === side);
      if (book) renderOrderTypes(groups, side, sizeUsd);
      else { otBody.replaceChildren(); otSub.textContent = market.isDelisted ? 'Delisted market — no live book.' : 'Waiting for the order book…'; }

      const paperM = paper ? paperFor(paper, market.paperKey) : null;
      const sizes = [...new Set([...CURVE_SIZES, ...(paperM?.sizes ?? [])])].sort((a, b) => a - b);
      curve.render({ book, fees, sizes, paper: paperM, paperPeriod: PAPER.period, sizeUsd, view: curveView });

      const key = `${market.name}|${paper ? 1 : 0}`;
      if (key !== lastKey) { renderPaper(market, paper); lastKey = key; }
      renderFees(market, fees);
    },
  };
}
