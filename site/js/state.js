// A tiny store plus the two pieces of persistence the page needs.
import { DEFAULT_PROFILE } from './config.js';

export function createStore(initial) {
  let state = { ...initial };
  const listeners = new Set();
  let scheduled = false;
  let pending = {};
  const flush = () => {
    scheduled = false;
    const changed = pending; pending = {};
    for (const l of listeners) { try { l(state, changed); } catch (err) { console.error(err); } }
  };
  return {
    get: () => state,
    set(patch) {
      let dirty = false;
      for (const [k, v] of Object.entries(patch)) {
        if (state[k] !== v) { dirty = true; pending[k] = true; }
      }
      if (!dirty) return;
      state = { ...state, ...patch };
      if (!scheduled) { scheduled = true; queueMicrotask(flush); }
    },
    subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn); },
  };
}

const PROFILE_KEY = 'hl-cost-monitor.profile.v1';
const THEME_KEY = 'hl-cost-monitor.theme.v1';

function readJson(key) {
  try { const raw = localStorage.getItem(key); return raw ? JSON.parse(raw) : null; } catch { return null; }
}
function writeJson(key, value) {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* private mode or blocked storage */ }
}

export function loadProfile() {
  const saved = readJson(PROFILE_KEY);
  return { ...DEFAULT_PROFILE, ...(saved && typeof saved === 'object' ? saved : {}) };
}
export function saveProfile(profile) { writeJson(PROFILE_KEY, profile); }

export function loadTheme() { const t = readJson(THEME_KEY); return t === 'light' || t === 'dark' ? t : 'auto'; }
export function saveTheme(theme) { writeJson(THEME_KEY, theme); }

export function readHash() {
  const h = location.hash.replace(/^#/, '');
  const params = new URLSearchParams(h);
  return { market: params.get('market') || null };
}
export function writeHash({ market }) {
  const params = new URLSearchParams();
  if (market) params.set('market', market);
  const next = '#' + params.toString();
  if (location.hash !== next) history.replaceState(null, '', next);
}
