// Hyperliquid API access: a REST client that respects the published weight budget
// and a WebSocket client with a subscription registry that survives reconnects.
import { API, RATE } from './config.js';

class WeightMeter {
  constructor(windowMs, cap) { this.windowMs = windowMs; this.cap = cap; this.events = []; }
  _prune(now) { while (this.events.length && this.events[0].t < now - this.windowMs) this.events.shift(); }
  used(now = Date.now()) { this._prune(now); return this.events.reduce((s, e) => s + e.w, 0); }
  canSpend(w, now = Date.now()) { return this.used(now) + w <= this.cap; }
  record(w, now = Date.now()) { this.events.push({ t: now, w }); }
}

export class RestClient {
  constructor({ url = API.info, softCap = RATE.softCap } = {}) {
    this.url = url;
    this.meter = new WeightMeter(RATE.windowMs, softCap);
    this.backoffUntil = 0;
    this.stats = { requests: 0, errors: 0, lastError: null, lastErrorAt: 0 };
  }

  weightFor(type) { return RATE.weights[type] ?? RATE.weights.default; }
  get usedWeight() { return this.meter.used(); }
  get budget() { return this.meter.cap; }
  get backedOff() { return Date.now() < this.backoffUntil; }
  canSpend(type) { return !this.backedOff && this.meter.canSpend(this.weightFor(type)); }

  async info(body) {
    const w = this.weightFor(body.type);
    this.meter.record(w);
    this.stats.requests++;
    let res;
    try {
      res = await fetch(this.url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    } catch (err) {
      this._fail(err.message);
      throw err;
    }
    if (res.status === 429) {
      this.backoffUntil = Date.now() + RATE.backoffOn429Ms;
      this._fail('HTTP 429 (rate limited), pausing the sweep');
      throw new Error('rate limited');
    }
    if (!res.ok) {
      this._fail(`HTTP ${res.status}`);
      throw new Error(`HTTP ${res.status} for ${body.type}`);
    }
    return res.json();
  }

  _fail(message) {
    this.stats.errors++;
    this.stats.lastError = message;
    this.stats.lastErrorAt = Date.now();
  }

  /** The most recent error, if it happened within the last minute. */
  get recentError() {
    return this.stats.lastError && Date.now() - this.stats.lastErrorAt < 60_000 ? this.stats.lastError : null;
  }

  perpDexs() { return this.info({ type: 'perpDexs' }); }
  spotMeta() { return this.info({ type: 'spotMeta' }); }
  metaAndAssetCtxs(dex) { return this.info(dex ? { type: 'metaAndAssetCtxs', dex } : { type: 'metaAndAssetCtxs' }); }
  l2Book(coin, nSigFigs = null) {
    const body = { type: 'l2Book', coin };
    if (nSigFigs) body.nSigFigs = nSigFigs;
    return this.info(body);
  }
}

function routeKeyOf(sub) {
  if (sub.coin) return `${sub.type}|${sub.coin}`;
  if (sub.dex) return `${sub.type}|dex:${sub.dex}`;
  return sub.type;
}

export class WsClient {
  constructor({ url = API.ws, onStatus = null } = {}) {
    this.url = url;
    this.onStatus = onStatus;
    this.subs = new Map();       // routeKey → { sub, handlers:Set }
    this.ws = null;
    this.status = 'idle';
    this.backoffMs = 1000;
    this.pingTimer = null;
    this.reconnectTimer = null;
    this.closedByUser = false;
    this.stats = { messages: 0, reconnects: 0, lastMessageAt: 0 };
  }

  _setStatus(s) { this.status = s; this.onStatus?.(s); }

  connect() {
    if (typeof WebSocket === 'undefined') return;
    this.closedByUser = false;
    clearTimeout(this.reconnectTimer);
    this._setStatus(this.stats.reconnects ? 'reconnecting' : 'connecting');
    const ws = new WebSocket(this.url);
    this.ws = ws;
    ws.onopen = () => {
      this.backoffMs = 1000;
      this._setStatus('live');
      for (const { sub } of this.subs.values()) this._send({ method: 'subscribe', subscription: sub });
      clearInterval(this.pingTimer);
      this.pingTimer = setInterval(() => this._send({ method: 'ping' }), 50_000);
    };
    ws.onmessage = (ev) => this._onMessage(ev);
    ws.onerror = () => { /* the close handler schedules the reconnect */ };
    ws.onclose = () => {
      clearInterval(this.pingTimer);
      if (this.ws !== ws) return;
      this.ws = null;
      if (this.closedByUser) { this._setStatus('closed'); return; }
      this._setStatus('reconnecting');
      this.stats.reconnects++;
      this.reconnectTimer = setTimeout(() => this.connect(), this.backoffMs);
      this.backoffMs = Math.min(this.backoffMs * 2, 30_000);
    };
  }

  close() {
    this.closedByUser = true;
    clearInterval(this.pingTimer);
    clearTimeout(this.reconnectTimer);
    this.ws?.close();
  }

  _send(obj) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(obj));
      return true;
    }
    return false;
  }

  _onMessage(ev) {
    let msg;
    try { msg = JSON.parse(ev.data); } catch { return; }
    this.stats.messages++;
    this.stats.lastMessageAt = Date.now();
    const { channel, data } = msg;
    if (!channel || channel === 'pong' || channel === 'subscriptionResponse') return;
    if (channel === 'error') { console.warn('[ws] error', data); return; }
    // Coin feeds route by coin; coin-less feeds (allMids per dex) go to every subscriber of that type.
    const targets = data?.coin
      ? [this.subs.get(`${channel}|${data.coin}`)].filter(Boolean)
      : [...this.subs.values()].filter((e) => e.sub.type === channel);
    for (const entry of targets) {
      for (const h of entry.handlers) {
        try { h(data, msg); } catch (err) { console.error('[ws] handler failed', err); }
      }
    }
  }

  /**
   * Subscribe to a feed. Returns a function that removes this handler and, when it
   * was the last one, sends the unsubscribe message.
   */
  subscribe(sub, handler) {
    const key = routeKeyOf(sub);
    let entry = this.subs.get(key);
    if (!entry) {
      entry = { sub, handlers: new Set() };
      this.subs.set(key, entry);
      this._send({ method: 'subscribe', subscription: sub });
    }
    entry.handlers.add(handler);
    return () => {
      const e = this.subs.get(key);
      if (!e) return;
      e.handlers.delete(handler);
      if (e.handlers.size === 0) {
        this.subs.delete(key);
        this._send({ method: 'unsubscribe', subscription: e.sub });
      }
    };
  }

  get subscriptionCount() { return this.subs.size; }
}
