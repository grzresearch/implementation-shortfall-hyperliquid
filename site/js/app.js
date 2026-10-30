// Application orchestration: load the universe, keep every book fresh within the
// API budget, stream the selected market, and render the table and the detail panel.
import { SWEEP, RATE, WS, SIZE_PRESETS, DEPTH_BANDS_BPS, PAPER, COARSE_MIN_SPAN_BPS, COARSE_NEEDED_BELOW_BPS } from './config.js';
import { RestClient, WsClient } from './api.js';
import { loadUniverse, refreshContexts, ctxFromRaw, effectiveBook, ASSET_CLASSES } from './universe.js';
import { feeRates, TIERS, STAKING, MAKER_REBATE_TIERS } from './fees.js';
import { parseBook, applyBbo, midOf, reachBps, chooseSigFigs } from './book.js';
import { createStore, loadProfile, saveProfile, normalizeProfile, loadTheme, saveTheme, readHash, writeHash } from './state.js';
import { createTable, rowModel } from './table.js';
import { createDetail } from './detail.js';
import { loadPaper } from './paper.js';
import { fmtSizeLabel, parseSizeInput } from './format.js';

const $ = (id) => document.getElementById(id);
const stackedLayout = window.matchMedia('(max-width: 1100px)');

const rest = new RestClient();
const ws = new WsClient({ onStatus: () => { statusDirty = true; } });
const store = createStore({
  profile: loadProfile(),
  sizeUsd: 100_000,
  bandBps: 25,
  search: '',
  dexFilter: null,
  classFilter: 'all',
  showDelisted: false,
  sort: { key: 'vol24h', dir: 'desc' },
  selected: readHash().market,
  precision: 'auto',
  clobView: 'chart',
  curveView: 'chart',
  side: 'BUY',
  theme: loadTheme(),
});

let universe = null;
let paper = null;
let visibleNames = [];
let liveNames = [];
let selectedUnsubs = [];
let selectedPollTimer = null;
let ctxTimer = null;
let role = 'leader'; // 'leader' runs the full sweep and the bbo feed; 'follower' tabs go slow
const bboUnsubs = new Map();
const coarseQueue = [];
let tableDirty = true, detailDirty = true, statusDirty = true;
let detailRaf = 0;
let lastDetailAt = 0;
let tickCount = 0;

let table, detail;

// ---------------------------------------------------------------------------
// Theme
// ---------------------------------------------------------------------------
function applyTheme() {
  const { theme } = store.get();
  if (theme === 'auto') delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = theme;
  $('theme-toggle').textContent = { auto: 'Theme: auto', light: 'Theme: light', dark: 'Theme: dark' }[theme];
}

// ---------------------------------------------------------------------------
// Filter row
// ---------------------------------------------------------------------------
function wireFilters() {
  $('theme-toggle').addEventListener('click', () => {
    const next = { auto: 'light', light: 'dark', dark: 'auto' }[store.get().theme];
    store.set({ theme: next });
    saveTheme(next);
    applyTheme();
  });

  const search = $('search');
  search.addEventListener('input', () => store.set({ search: search.value }));

  const classSel = $('class-filter');
  classSel.replaceChildren();
  const all = document.createElement('option'); all.value = 'all'; all.textContent = 'All asset classes'; classSel.append(all);
  for (const [id, c] of Object.entries(ASSET_CLASSES)) { const o = document.createElement('option'); o.value = id; o.textContent = c.label; classSel.append(o); }
  classSel.addEventListener('change', () => store.set({ classFilter: classSel.value }));

  const sizeInput = $('size-input');
  const sizeSel = $('size-presets');
  sizeSel.replaceChildren();
  for (const s of SIZE_PRESETS) { const o = document.createElement('option'); o.value = String(s); o.textContent = fmtSizeLabel(s); sizeSel.append(o); }
  const custom = document.createElement('option'); custom.value = ''; custom.textContent = 'custom'; custom.hidden = true; sizeSel.append(custom);
  sizeSel.value = String(store.get().sizeUsd);
  sizeInput.value = fmtSizeLabel(store.get().sizeUsd).replace('$', '');
  const commitSize = (v) => {
    if (!(v > 0)) { sizeInput.value = fmtSizeLabel(store.get().sizeUsd).replace('$', ''); return; }
    const size = Math.min(v, 1e11);
    store.set({ sizeUsd: size });
    sizeInput.value = fmtSizeLabel(size).replace('$', '');
    sizeSel.value = SIZE_PRESETS.includes(size) ? String(size) : '';
  };
  sizeInput.addEventListener('change', () => commitSize(parseSizeInput(sizeInput.value)));
  sizeInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); commitSize(parseSizeInput(sizeInput.value)); } });
  sizeSel.addEventListener('change', () => { if (sizeSel.value) commitSize(Number(sizeSel.value)); });

  const bandSel = $('band-select');
  bandSel.replaceChildren();
  for (const b of DEPTH_BANDS_BPS) { const o = document.createElement('option'); o.value = String(b); o.textContent = `${b} bps`; bandSel.append(o); }
  bandSel.value = String(store.get().bandBps);
  bandSel.addEventListener('change', () => store.set({ bandBps: Number(bandSel.value) }));

  const delisted = $('show-delisted');
  delisted.checked = store.get().showDelisted;
  delisted.addEventListener('change', () => store.set({ showDelisted: delisted.checked }));

  wireProfile();
}

function wireProfile() {
  const btn = $('profile-btn');
  const pop = $('profile-popover');
  const tierSel = $('p-tier'), stakeSel = $('p-staking'), rebateSel = $('p-rebate'), refChk = $('p-referral'), builderIn = $('p-builder');
  for (const t of TIERS) { const o = document.createElement('option'); o.value = String(t.tier); o.textContent = `${t.label} · taker ${t.takerBps} / maker ${t.makerBps} bps`; tierSel.append(o); }
  for (const s of STAKING) { const o = document.createElement('option'); o.value = s.id; o.textContent = s.label; stakeSel.append(o); }
  for (const r of MAKER_REBATE_TIERS) { const o = document.createElement('option'); o.value = String(r.tier); o.textContent = r.label; rebateSel.append(o); }
  const sync = () => {
    const p = store.get().profile;
    tierSel.value = String(p.tier); stakeSel.value = p.staking; rebateSel.value = String(p.rebateTier);
    refChk.checked = Boolean(p.referral); builderIn.value = String(p.builderBps ?? 0);
    const tier = TIERS.find((t) => t.tier === p.tier) ?? TIERS[0];
    btn.textContent = `Profile: ${tier.label.split(' · ')[0]}${p.staking !== 'none' ? ' · staking' : ''}${p.referral ? ' · referral' : ''}${p.rebateTier ? ' · rebate' : ''}${p.builderBps ? ` · +${p.builderBps} bps builder` : ''}`;
  };
  const commit = () => {
    const profile = normalizeProfile({
      tier: Number(tierSel.value), staking: stakeSel.value, rebateTier: Number(rebateSel.value),
      referral: refChk.checked, builderBps: Number(builderIn.value),
    });
    store.set({ profile });
    saveProfile(profile);
    sync();
  };
  for (const c of [tierSel, stakeSel, rebateSel, refChk, builderIn]) c.addEventListener('change', commit);
  btn.addEventListener('click', () => {
    pop.hidden = !pop.hidden;
    btn.setAttribute('aria-expanded', String(!pop.hidden));
    if (!pop.hidden) {
      const r = btn.getBoundingClientRect();
      pop.style.top = `${r.bottom + 6 + window.scrollY}px`;
      pop.style.left = `${Math.max(8, Math.min(r.left + window.scrollX, window.innerWidth - pop.offsetWidth - 16))}px`;
    }
  });
  document.addEventListener('click', (e) => { if (!pop.hidden && !pop.contains(e.target) && e.target !== btn) { pop.hidden = true; btn.setAttribute('aria-expanded', 'false'); } });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !pop.hidden) { pop.hidden = true; btn.setAttribute('aria-expanded', 'false'); btn.focus(); } });
  sync();
}

// Deployer chips are created once and updated in place, so focus survives a toggle.
const dexChips = new Map();
function buildDexChips() {
  const wrap = $('dex-chips');
  const { dexFilter, showDelisted } = store.get();
  const ensure = (key, onClick) => {
    let b = dexChips.get(key);
    if (!b) {
      b = document.createElement('button');
      b.type = 'button';
      b.addEventListener('click', onClick);
      wrap.append(b);
      dexChips.set(key, b);
    }
    return b;
  };
  const allBtn = ensure('*', () => store.set({ dexFilter: null }));
  allBtn.textContent = 'All';
  allBtn.title = 'Every deployer';
  allBtn.className = `chip-btn${!dexFilter ? ' active' : ''}`;
  allBtn.setAttribute('aria-pressed', String(!dexFilter));
  for (const d of universe.dexes) {
    const b = ensure(d.name || '*native*', () => {
      const cur = new Set(store.get().dexFilter ?? []);
      if (cur.has(d.name)) cur.delete(d.name); else cur.add(d.name);
      const patch = { dexFilter: cur.size ? cur : null };
      if (d.liveCount === 0 && !d.failed && cur.has(d.name)) patch.showDelisted = true;
      store.set(patch);
    });
    const dead = d.liveCount === 0 && !d.failed;
    b.textContent = d.failed ? `${d.label} · failed to load` : `${d.label} ${dead ? `· ${d.delistedCount} delisted` : d.liveCount}`;
    b.title = d.failed
      ? `${d.fullName} · the market list could not be loaded; retried every ${Math.round(SWEEP.ctxRefreshMs / 60000)} min`
      : `${d.fullName} · ${d.liveCount} live, ${d.delistedCount} delisted · quoted in ${d.quote ?? '?'}`;
    const active = dexFilter?.has(d.name) ?? false;
    b.className = `chip-btn${active ? ' active' : ''}${dead ? ' archived' : ''}${d.failed ? ' failed' : ''}`;
    b.setAttribute('aria-pressed', String(active));
  }
  const delisted = $('show-delisted');
  if (delisted.checked !== showDelisted) delisted.checked = showDelisted;
}

// ---------------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------------
function filteredRows() {
  const s = store.get();
  const q = s.search.trim().toUpperCase();
  const now = Date.now();
  const out = [];
  for (const m of universe.markets.values()) {
    if (m.isDelisted && !s.showDelisted) continue;
    if (s.dexFilter && !s.dexFilter.has(m.dex)) continue;
    if (s.classFilter !== 'all' && m.cls !== s.classFilter) continue;
    if (q && !(m.name.toUpperCase().includes(q) || m.dexFullName.toUpperCase().includes(q))) continue;
    out.push(rowModel(m, { bandBps: s.bandBps, sizeUsd: s.sizeUsd, fees: feeRates(m, s.profile), now }));
  }
  return out;
}

function renderTable() {
  if (!universe) return;
  const s = store.get();
  visibleNames = table.update(filteredRows(), { sort: s.sort, selected: s.selected, sizeUsd: s.sizeUsd, bandBps: s.bandBps });
  const total = s.showDelisted ? universe.markets.size : liveNames.length;
  $('table-count').textContent = `${visibleNames.length} of ${total} ${s.showDelisted ? 'markets (incl. delisted)' : 'live markets'}`;
  tableDirty = false;
}

function displayBookFor(m, precision) {
  if (!m) return null;
  if (precision === 'fine') return m.book;
  if (precision === 'auto') return effectiveBook(m);
  const n = Number(precision);
  const candidates = [m.bookAgg, m.bookCoarse].filter((b) => b && b.nSigFigs === n);
  if (!candidates.length) return null;
  return candidates.reduce((a, b) => (b.time > a.time ? b : a)); // the fresher of the two
}

function renderDetail() {
  if (!universe) return;
  const s = store.get();
  const market = s.selected ? universe.markets.get(s.selected) : null;
  detail.render({
    market,
    fineBook: market?.book ?? null,
    costBook: market ? effectiveBook(market) : null,
    displayBook: displayBookFor(market, s.precision),
    fees: market ? feeRates(market, s.profile) : null,
    sizeUsd: s.sizeUsd, paper,
    precision: s.precision, clobView: s.clobView, curveView: s.curveView, side: s.side,
  });
  detailDirty = false;
  lastDetailAt = performance.now();
}

function scheduleDetail() {
  if (detailRaf) return;
  detailRaf = requestAnimationFrame(() => {
    detailRaf = 0;
    if (performance.now() - lastDetailAt < 120) { detailDirty = true; return; }
    renderDetail();
  });
}

function renderStatus() {
  const now = Date.now();
  const fresh = liveNames.reduce((n, name) => n + (now - universe.markets.get(name).bookAt < SWEEP.freshMs ? 1 : 0), 0);
  $('st-rest').textContent = `REST ${rest.usedWeight}/${rest.budget} weight per min${rest.backedOff ? ' · backing off' : ''}`;
  $('st-ws').textContent = `WebSocket ${ws.status} · ${ws.subscriptionCount} subscriptions`;
  $('st-ws').dataset.state = ws.status;
  $('st-books').textContent = `Books ${fresh}/${liveNames.length} fresh (< ${Math.round(SWEEP.freshMs / 60000)} min)${role === 'follower' ? ' · slow sweep, another tab leads' : ''}`;
  const failed = universe.dexes.filter((d) => d.failed).map((d) => d.fullName);
  const err = rest.recentError ? `${rest.recentError} · ${rest.stats.errors} errors so far` : '';
  $('st-err').textContent = failed.length ? `${failed.join(', ')}: market list not loaded yet${err ? ' · ' + err : ''}` : err;
  statusDirty = false;
}

// ---------------------------------------------------------------------------
// Books: full precision plus an aggregated book for depth beyond 20 levels
// ---------------------------------------------------------------------------
/** Store a fine book unless it is older than the one we already have. */
function acceptBook(m, book, source) {
  if (m.book && book.time && m.book.time && book.time < m.book.time) return false;
  m.book = book; m.bookAt = Date.now(); m.bookSource = source;
  tableDirty = true;
  if (store.get().selected === m.name) scheduleDetail();
  return true;
}

function needsCoarse(m) {
  const b = m.book;
  if (!b) return true;
  const mid = midOf(b);
  if (!(mid > 0)) return false;
  return Math.min(reachBps(b.bids, mid), reachBps(b.asks, mid)) < COARSE_NEEDED_BELOW_BPS;
}

function coarseSigFigsFor(m) {
  const mid = midOf(m.book) || m.ctx?.mid;
  return chooseSigFigs(mid, COARSE_MIN_SPAN_BPS);
}

async function fetchCoarse(m) {
  const now = Date.now();
  if (m.coarseRequestedAt && now - m.coarseRequestedAt < 10_000 && m.bookCoarseAt < m.coarseRequestedAt) return; // one in flight
  const n = coarseSigFigsFor(m);
  m.coarseRequestedAt = now;
  try {
    const raw = await rest.l2Book(m.name, n);
    const book = parseBook(raw);
    book.nSigFigs = n;
    if (!m.bookCoarse || !(book.time < m.bookCoarse.time)) { m.bookCoarse = book; m.bookCoarseAt = Date.now(); }
    tableDirty = true;
    if (store.get().selected === m.name) scheduleDetail();
  } catch { statusDirty = true; }
}

async function fetchAgg(m, n) {
  try {
    const raw = await rest.l2Book(m.name, n);
    const book = parseBook(raw);
    book.nSigFigs = n;
    m.bookAgg = book;
    if (store.get().selected === m.name) scheduleDetail();
  } catch { statusDirty = true; }
}

function pickStalest(names, now, skip) {
  let best = null, bestAge = -1;
  for (const name of names) {
    if (name === skip) continue;
    const m = universe.markets.get(name);
    if (!m || m.isDelisted) continue;
    const age = now - Math.max(m.bookRequestedAt || 0, m.bookAt || 0);
    if (age > bestAge) { best = m; bestAge = age; }
  }
  return [best, bestAge];
}

function sweepTick() {
  if (!universe || !rest.canSpend('l2Book', RATE.sweepReserve)) return;
  const now = Date.now();
  const { selected } = store.get();
  while (coarseQueue.length) {
    const m = universe.markets.get(coarseQueue.shift());
    if (m && !m.isDelisted && m.name !== selected) { fetchCoarse(m); return; }
  }
  tickCount++;
  // Visible rows first; every n-th tick the hidden ones get their turn so a long filtered
  // list cannot starve the rest of the universe.
  const hiddenTurn = tickCount % SWEEP.hiddenShareEvery === 0;
  let [m, age] = pickStalest(hiddenTurn ? liveNames : visibleNames, now, selected);
  if (!m || age < SWEEP.minRefreshMs) {
    const [m2, age2] = pickStalest(hiddenTurn ? visibleNames : liveNames, now, selected);
    if (m2 && age2 > age) { m = m2; age = age2; }
  }
  if (!m || age < SWEEP.minRefreshMs) return;
  m.bookRequestedAt = now;
  rest.l2Book(m.name).then((raw) => {
    if (m.bookSource === 'ws' && store.get().selected === m.name) return;
    acceptBook(m, parseBook(raw), 'rest');
    if (needsCoarse(m)) coarseQueue.push(m.name);
    else m.bookCoarse = null; // the fine book now reaches far enough on its own
  }).catch(() => { statusDirty = true; });
}

let sweepTimer = null;
function startSweep() {
  clearInterval(sweepTimer);
  const pace = document.hidden ? SWEEP.hiddenTabTickMs : role === 'leader' ? SWEEP.tickMs : SWEEP.followerTickMs;
  sweepTimer = setInterval(sweepTick, pace);
}
document.addEventListener('visibilitychange', startSweep);

function startContextRefresh() {
  clearInterval(ctxTimer);
  const period = role === 'leader' ? SWEEP.ctxRefreshMs : SWEEP.followerCtxRefreshMs;
  ctxTimer = setInterval(async () => {
    if (!rest.canSpendMany('default', universe.dexes.length)) return; // try again next period
    const added = await refreshContexts(rest, universe);
    if (added.length) onMarketsAdded(added);
    buildDexChips();
    tableDirty = true; detailDirty = true; statusDirty = true;
  }, period);
}

function onMarketsAdded(names) {
  liveNames = [...universe.markets.values()].filter((m) => !m.isDelisted).map((m) => m.name);
  if (role === 'leader') for (const name of names) subscribeBbo(name);
}

// ---------------------------------------------------------------------------
// Multi-tab etiquette: one tab per browser profile leads (full sweep + bbo feed)
// ---------------------------------------------------------------------------
function setRole(next) {
  if (role === next) return;
  role = next;
  if (role === 'leader') for (const name of liveNames) subscribeBbo(name);
  startSweep();
  startContextRefresh();
  statusDirty = true;
}

function electRole() {
  const locks = navigator.locks;
  if (!locks?.request) return Promise.resolve('leader');
  const hold = () => new Promise(() => {}); // held for the lifetime of the tab
  return new Promise((resolve) => {
    locks.request(WS.lockName, { ifAvailable: true }, (lock) => {
      if (lock) { resolve('leader'); return hold(); }
      resolve('follower');
      // Queue for leadership: resolves when the leading tab goes away.
      locks.request(WS.lockName, () => { setRole('leader'); return hold(); }).catch(() => {});
      return undefined;
    }).catch(() => resolve('leader'));
  });
}

// ---------------------------------------------------------------------------
// Selection and streaming
// ---------------------------------------------------------------------------
function select(name, { userAction = false } = {}) {
  const prevName = store.get().selected;
  if (name === prevName && selectedUnsubs.length) {
    table.scrollTo(name);
    if (userAction && stackedLayout.matches) $('detail').scrollIntoView({ behavior: 'smooth', block: 'start' });
    return;
  }
  const prev = prevName ? universe.markets.get(prevName) : null;
  for (const u of selectedUnsubs) u();
  selectedUnsubs = [];
  clearInterval(selectedPollTimer); selectedPollTimer = null;
  if (prev && prev.bookSource === 'ws') prev.bookSource = 'rest';
  if (prev) prev.bookAgg = null;
  store.set({ selected: name });
  writeHash({ market: name });
  const m = universe.markets.get(name);
  detailDirty = true; tableDirty = true;
  if (!m) return;
  if (m.isDelisted && !store.get().showDelisted) store.set({ showDelisted: true });
  if (m.isDelisted) return;
  selectedUnsubs.push(ws.subscribe({ type: 'l2Book', coin: name }, (data) => {
    acceptBook(m, parseBook(data), 'ws');
  }));
  selectedUnsubs.push(ws.subscribe({ type: 'activeAssetCtx', coin: name }, (data) => {
    const ctx = ctxFromRaw(data?.ctx, Date.now());
    if (ctx) { m.ctx = ctx; tableDirty = true; scheduleDetail(); }
  }));
  startSelectedPolling();
  table.scrollTo(name);
  if (userAction && stackedLayout.matches) $('detail').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

/**
 * The selected market: the WebSocket delivers l2Book snapshots only every few seconds and
 * bbo per block, so the fine book is also refreshed over REST, together with the aggregated
 * books the stitched view and the manual precision need.
 */
function startSelectedPolling() {
  clearInterval(selectedPollTimer); selectedPollTimer = null;
  const { selected } = store.get();
  const m = selected ? universe.markets.get(selected) : null;
  if (!m || m.isDelisted) return;
  const poll = () => {
    if (store.get().selected !== m.name || !rest.canSpend('l2Book')) return;
    const precision = store.get().precision;
    const coarseN = coarseSigFigsFor(m);
    if (!m.book || Date.now() - m.bookAt > SWEEP.selectedPollMs - 500) {
      rest.l2Book(m.name).then((raw) => acceptBook(m, parseBook(raw), m.bookSource === 'ws' ? 'ws' : 'rest')).catch(() => { statusDirty = true; });
    }
    const manual = precision !== 'auto' && precision !== 'fine' ? Number(precision) : null;
    if (needsCoarse(m) || manual === coarseN) fetchCoarse(m);
    else if (m.bookCoarse) m.bookCoarse = null;
    if (manual != null && manual !== coarseN) fetchAgg(m, manual);
    else m.bookAgg = null;
  };
  poll();
  selectedPollTimer = setInterval(poll, SWEEP.selectedPollMs);
}

function subscribeBbo(name) {
  if (bboUnsubs.has(name)) return;
  const m = universe.markets.get(name);
  if (!m || m.isDelisted) return;
  bboUnsubs.set(name, ws.subscribe({ type: 'bbo', coin: name }, (data) => {
    if (!m.book || !Array.isArray(data?.bbo)) return;
    const next = applyBbo(m.book, data.bbo, Number(data.time) || 0);
    if (next !== m.book) {
      m.book = next;
      tableDirty = true;
      if (store.get().selected === m.name) scheduleDetail();
    }
  }));
}

function startStreams() {
  ws.connect();
  for (const d of universe.dexes) {
    const sub = d.name ? { type: 'allMids', dex: d.name } : { type: 'allMids' };
    ws.subscribe(sub, (data) => {
      const mids = data?.mids ?? {};
      for (const [coin, mid] of Object.entries(mids)) {
        const m = universe.markets.get(coin);
        if (!m) continue;
        const v = Number(mid);
        if (Number.isFinite(v)) { if (!m.ctx) m.ctx = ctxFromRaw({}, Date.now()); m.ctx.mid = v; }
      }
      tableDirty = true;
    });
  }
  if (role === 'leader') for (const name of liveNames) subscribeBbo(name);
}

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------
async function boot() {
  const banner = $('banner');
  try {
    applyTheme();
    table = createTable($('market-table'), {
      onSelect: (name) => select(name, { userAction: true }),
      onSort: (key) => {
        const { sort } = store.get();
        const dir = sort.key === key ? (sort.dir === 'desc' ? 'asc' : 'desc') : (key === 'name' ? 'asc' : 'desc');
        store.set({ sort: { key, dir } });
      },
    });
    detail = createDetail($('detail'), {
      onPrecisionChange: (p) => store.set({ precision: p }),
      onClobViewChange: (v) => store.set({ clobView: v }),
      onCurveViewChange: (v) => store.set({ curveView: v }),
      onSideChange: (s) => store.set({ side: s }),
    });
    wireFilters();
    universe = await loadUniverse(rest);
  } catch (err) {
    banner.hidden = false;
    banner.textContent = `Could not start the dashboard: ${err.message}. The page needs direct access to ${rest.url}.`;
    console.error(err);
    return;
  }
  liveNames = [...universe.markets.values()].filter((m) => !m.isDelisted).map((m) => m.name);
  role = await electRole();
  buildDexChips();
  const s = store.get();
  let selected = s.selected && universe.markets.has(s.selected) ? s.selected : null;
  if (!selected) {
    selected = liveNames.slice().sort((a, b) => (universe.markets.get(b).ctx?.vol24h ?? 0) - (universe.markets.get(a).ctx?.vol24h ?? 0))[0] ?? null;
  }
  renderTable();
  startStreams();
  select(selected);
  startSweep();
  startContextRefresh();
  loadPaper(PAPER.summaryCsv).then((p) => { paper = p; detailDirty = true; });

  store.subscribe((state, changed) => {
    if (changed.dexFilter || changed.showDelisted) buildDexChips();
    if (changed.precision) startSelectedPolling();
    tableDirty = true; detailDirty = true;
  });
  window.addEventListener('hashchange', () => {
    const { market } = readHash();
    if (market && market !== store.get().selected && universe.markets.has(market)) select(market, { userAction: true });
  });
  setInterval(() => {
    if (tableDirty) renderTable();
    if (detailDirty) renderDetail();
    renderStatus();
  }, 500);
  setInterval(() => { tableDirty = true; }, SWEEP.agePulseMs); // keeps the age column ticking even when nothing else changes
  $('st-updated').textContent = `universe loaded ${new Date().toLocaleTimeString()}`;
}

boot();
