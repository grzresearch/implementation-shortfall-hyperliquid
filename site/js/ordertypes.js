// The catalogue of Hyperliquid order types, each mapped onto the cost engine.
// Taker rows walk the live book (execCost); maker rows rest on it (makerCost).
import { LIMIT_OFFSET_BPS, SCALE_RANGE_BPS, TWAP_SUBORDER_SEC, TWAP_VARIANTS } from './config.js';
import { execCost, makerCost } from './book.js';

const SCALE_ORDERS = 5;

export const PAPER_STRATEGIES = ['NAIVE_MARKET', 'MARKET_CAP5', 'TWAP_5MIN', 'TWAP_30MIN', 'VWAP_30MIN'];

export function orderTypeGroups({ book, sizeUsd, fees }) {
  const taker = (opts) => ({
    buy: execCost({ side: 'BUY', book, sizeUsd, takerBps: fees.takerBps, ...opts }),
    sell: execCost({ side: 'SELL', book, sizeUsd, takerBps: fees.takerBps, ...opts }),
  });
  const maker = (opts = {}) => ({
    buy: makerCost({ side: 'BUY', book, sizeUsd, makerBps: fees.makerBps, ...opts }),
    sell: makerCost({ side: 'SELL', book, sizeUsd, makerBps: fees.makerBps, ...opts }),
  });
  const twapRows = TWAP_VARIANTS.map((v) => {
    const n = Math.max(1, Math.round((v.minutes * 60) / TWAP_SUBORDER_SEC));
    return {
      id: v.id, label: `${v.label} · ${n} sub-orders`, liquidity: 'taker', ...taker({ strategy: 'twap', nSlices: n }),
      note: 'Every sub-order walks the current book (static-book assumption, as in the paper); replenishment between slices is not modelled.',
    };
  });
  return [
    {
      id: 'taker', label: 'Aggressive · taker',
      rows: [
        { id: 'market', label: 'Market', liquidity: 'taker', ...taker({ strategy: 'market' }), note: 'Sweeps the visible book immediately. Identical to the paper’s NAIVE_MARKET.' },
        { id: 'limit_ioc', label: `Limit IOC · touch ± ${LIMIT_OFFSET_BPS} bps`, liquidity: 'taker', ...taker({ strategy: 'limit', limitBps: LIMIT_OFFSET_BPS }), note: 'Fills only the levels inside the limit, the rest is cancelled. A crossing GTC limit fills the same way and rests the remainder as maker.' },
      ],
    },
    {
      id: 'maker', label: 'Passive · maker (conditional on being filled)',
      rows: [
        { id: 'limit_gtc', label: 'Limit GTC at touch', liquidity: 'maker', ...maker(), note: 'Joins the queue at the best bid/ask. Against the arrival mid a passive fill earns the half-spread; adverse selection and non-fill risk are not priced.' },
        { id: 'alo', label: 'Post-only (ALO) at touch', liquidity: 'maker', ...maker(), note: 'Same economics as GTC at touch, but the order is rejected instead of crossing, so it can never pay taker fees.' },
        { id: 'chase', label: 'Chase', liquidity: 'maker', ...maker(), note: 'A post-only order that re-prices to track the touch. The spread capture holds only while the market does not run away from it.' },
        { id: 'scale', label: `Scale · ${SCALE_ORDERS} rungs over ${SCALE_RANGE_BPS} bps`, liquidity: 'maker', ...maker({ extraBps: SCALE_RANGE_BPS / 2 }), note: 'Ladder of limit orders from the touch into the book. If every rung fills, the average price improves by half the range.' },
      ],
    },
    {
      id: 'trigger', label: 'Trigger orders · activate at the mark price',
      rows: [
        { id: 'stop_market', label: 'Stop Market · Take Profit Market · Trailing Stop', liquidity: 'taker', ...taker({ strategy: 'market' }), note: 'Executes as a market order once triggered. The figure applies to the book at trigger time; the gap between the mark trigger and the book is extra.' },
        { id: 'stop_limit_rest', label: 'Stop Limit · Take Profit Limit (resting)', liquidity: 'maker', ...maker(), note: 'The limit is placed when the trigger hits. If it rests, maker economics apply.' },
        { id: 'stop_limit_cross', label: 'Stop Limit · Take Profit Limit (crossing)', liquidity: 'taker', ...taker({ strategy: 'limit', limitBps: LIMIT_OFFSET_BPS }), note: `If the limit crosses the book it fills like a limit IOC inside ${LIMIT_OFFSET_BPS} bps.` },
      ],
    },
    { id: 'twap', label: `Native TWAP · one sub-order every ${TWAP_SUBORDER_SEC} s, 3 % max slippage each`, rows: twapRows },
    {
      id: 'paper', label: 'Paper strategies · static book, as simulated in code/simulator.py',
      rows: [
        { id: 'NAIVE_MARKET', label: 'NAIVE_MARKET', liquidity: 'taker', ...taker({ strategy: 'market' }), note: 'Single sweep through the order book at arrival.' },
        { id: 'MARKET_CAP5', label: 'MARKET_CAP5 · 5 % of each level', liquidity: 'taker', ...taker({ strategy: 'cap5' }), note: 'Depth-aware throttling: takes at most 5 % of every level, so large orders stay partly unfilled.' },
        { id: 'TWAP_5MIN', label: 'TWAP_5MIN · 5 slices', liquidity: 'taker', ...taker({ strategy: 'twap', nSlices: 5 }), note: '5 equal slices over 5 minutes, each walking the arrival book.' },
        { id: 'TWAP_30MIN', label: 'TWAP_30MIN · 30 slices', liquidity: 'taker', ...taker({ strategy: 'twap', nSlices: 30 }), note: '30 equal slices over 30 minutes.' },
        { id: 'VWAP_30MIN', label: 'VWAP_30MIN', liquidity: 'taker', buy: null, sell: null, note: 'Needs the realised post-arrival volume profile, which only exists in hindsight. See the paper benchmark.' },
      ],
    },
  ];
}
