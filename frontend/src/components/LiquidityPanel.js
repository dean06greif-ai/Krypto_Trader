import React, { useState, useEffect, useCallback, useRef } from 'react';
import { X, Fire, ArrowsVertical } from '@phosphor-icons/react';
import SafeOverlay from './SafeOverlay';
import './LiquidityPanel.css';

const API_URL = process.env.REACT_APP_BACKEND_URL;
const INTERVALS = ['15m', '30m', '1h', '4h'];

const usd = (v) => {
  if (v === null || v === undefined) return '–';
  const n = Number(v);
  if (Math.abs(n) >= 1e9) return `${(n / 1e9).toFixed(2)} Mrd`;
  if (Math.abs(n) >= 1e6) return `${(n / 1e6).toFixed(1)} Mio`;
  if (Math.abs(n) >= 1e3) return `${(n / 1e3).toFixed(0)}k`;
  return n.toFixed(0);
};
const px = (v) => (v === null || v === undefined ? '–' : Number(v).toLocaleString('de-DE',
  { maximumFractionDigits: Number(v) < 10 ? 4 : 2 }));

const LEVEL_LABEL = {
  swing_high: 'Swing-Hoch', swing_low: 'Swing-Tief',
  equal_highs: 'Equal Highs', equal_lows: 'Equal Lows',
};

export default function LiquidityPanel({ symbol = 'BTCUSDT', onClose }) {
  const [interval_, setInterval_] = useState('1h');
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const priceLineRef = useRef(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const d = await fetch(`${API_URL}/api/liquidity/${symbol}?interval=${interval_}&bars=240`)
        .then(r => r.json());
      setData(d);
    } catch (e) { setData(null); }
    setLoading(false);
  }, [symbol, interval_]);

  useEffect(() => { load(); }, [load]);

  // Heatmap so scrollen, dass der aktuelle Preis mittig sichtbar ist
  useEffect(() => {
    if (!loading && priceLineRef.current) {
      priceLineRef.current.scrollIntoView({ block: 'center' });
    }
  }, [loading, data]);

  const hm = data?.heatmap || {};
  const price = hm.price;
  const buckets = [...(hm.buckets || [])].sort((a, b) => b.price_mid - a.price_mid);
  const levels = data?.levels?.levels || [];
  const market = data?.market || {};
  const ls = market.long_short || {};
  const liq5 = market.recent_liquidations_5m || {};
  const walls = market.orderbook_walls || {};
  const clusters = hm.clusters || {};

  return (
    <SafeOverlay className="lq-overlay" onClose={onClose}>
      <div className="lq-panel" onClick={e => e.stopPropagation()} data-testid="liquidity-panel">
        <div className="lq-header">
          <h2><Fire size={19} weight="fill" style={{ color: '#FF6B3D' }} />
            LIQUIDATIONS-HEATMAP &amp; LIQUIDITY-LEVEL · {symbol.replace('USDT', '')}</h2>
          <button className="lq-close" onClick={onClose} data-testid="liquidity-close">
            <X size={22} weight="bold" />
          </button>
        </div>

        <div className="lq-toolbar">
          <div className="lq-seg" data-testid="liquidity-interval-filter">
            {INTERVALS.map(i => (
              <button key={i} className={interval_ === i ? 'active' : ''}
                onClick={() => setInterval_(i)} data-testid={`liquidity-interval-${i}`}>{i}</button>
            ))}
          </div>
          <span className="lq-meta" data-testid="liquidity-meta">
            Preis <b>{px(price)}</b>
            {hm.oi_usd ? <> · Open Interest <b>{usd(hm.oi_usd)} USD</b></> : null}
            {hm.oi_calibrated ? <span className="lq-tag ok">OI-kalibriert</span>
              : <span className="lq-tag">Schätzung</span>}
          </span>
          <button className="lq-refresh" onClick={load} data-testid="liquidity-refresh">↻</button>
        </div>

        {loading && <div className="lq-empty">Lädt Börsendaten...</div>}
        {!loading && !buckets.length && (
          <div className="lq-empty" data-testid="liquidity-empty">
            Keine Heatmap-Daten für {symbol} verfügbar (Börsen-Quelle antwortet nicht).
          </div>
        )}

        {!loading && buckets.length > 0 && (
          <div className="lq-body">
            {/* Heatmap: Preis-Buckets von oben (teuer) nach unten (billig) */}
            <div className="lq-heatmap" data-testid="liquidity-heatmap">
              <div className="lq-col-title"><ArrowsVertical size={12} /> HEATMAP · geschätzte offene Liquidationen</div>
              {buckets.map((b, i) => {
                const above = b.price_mid > price;
                const crossed = i > 0 && buckets[i - 1].price_mid > price && b.price_mid < price;
                return (
                  <React.Fragment key={b.price_mid}>
                    {crossed && (
                      <div className="lq-price-line" ref={priceLineRef} data-testid="liquidity-price-line">
                        <span>AKTUELLER PREIS {px(price)}</span>
                      </div>
                    )}
                    <div className="lq-row" data-testid={`liquidity-bucket-${i}`}
                      title={`${px(b.price_low)} – ${px(b.price_high)} · Longs ${usd(b.long_usd)} / Shorts ${usd(b.short_usd)} USD`}>
                      <span className="lq-row-price mono">{px(b.price_mid)}</span>
                      <span className="lq-bar-wrap">
                        <span className={`lq-bar ${above ? 'short' : 'long'}`}
                          style={{ width: `${Math.max(b.intensity * 100, 2)}%`, opacity: 0.35 + b.intensity * 0.65 }} />
                      </span>
                      <span className="lq-row-usd mono">{usd(b.total_usd)}</span>
                    </div>
                  </React.Fragment>
                );
              })}
            </div>

            <div className="lq-side">
              <div className="lq-card" data-testid="liquidity-magnets">
                <div className="lq-col-title">MAGNET-CLUSTER</div>
                {(clusters.above_price || []).slice(0, 4).map(c => (
                  <div className="lq-line" key={`a${c.price}`}>
                    <span className="lq-dir short">▲ Shorts</span>
                    <span className="mono">{px(c.price)}</span>
                    <span className="lq-dist neg">{c.dist_pct > 0 ? '+' : ''}{c.dist_pct}%</span>
                    <span className="mono lq-usd">{usd(c.usd)}</span>
                  </div>
                ))}
                {(clusters.below_price || []).slice(0, 4).map(c => (
                  <div className="lq-line" key={`b${c.price}`}>
                    <span className="lq-dir long">▼ Longs</span>
                    <span className="mono">{px(c.price)}</span>
                    <span className="lq-dist pos">{c.dist_pct}%</span>
                    <span className="mono lq-usd">{usd(c.usd)}</span>
                  </div>
                ))}
                <div className="lq-hint">
                  Magnete sind wahrscheinliche Kursziele: dort liegen gestapelte Stop-/Liquidations-
                  Orders. SL nicht direkt davor legen.
                </div>
              </div>

              <div className="lq-card" data-testid="liquidity-levels">
                <div className="lq-col-title">UNBERÜHRTE LIQUIDITY-LEVEL</div>
                {levels.slice(0, 10).map(l => (
                  <div className="lq-line" key={`${l.type}${l.price}`}>
                    <span className={`lq-dir ${l.side === 'resistance' ? 'short' : 'long'}`}>
                      {LEVEL_LABEL[l.type] || l.type}
                    </span>
                    <span className="mono">{px(l.price)}</span>
                    <span className={`lq-dist ${l.dist_pct >= 0 ? 'neg' : 'pos'}`}>
                      {l.dist_pct > 0 ? '+' : ''}{l.dist_pct}%
                    </span>
                    <span className={`lq-strength ${l.strength}`}>{l.touches}x</span>
                  </div>
                ))}
                {levels.length === 0 && <div className="lq-hint">Keine unberührten Level gefunden.</div>}
              </div>

              <div className="lq-card" data-testid="liquidity-market">
                <div className="lq-col-title">BÖRSEN-KONTEXT (live)</div>
                <div className="lq-line"><span>Open Interest</span>
                  <span className="mono">{usd(market.oi_usd)} USD</span>
                  <span className="lq-dist">{market.oi_trend || '–'}</span></div>
                <div className="lq-line"><span>Long/Short (retail)</span>
                  <span className="mono">{ls.retail ?? '–'}</span>
                  <span className="lq-dist">Top {ls.top_trader_pos ?? '–'}</span></div>
                <div className="lq-line"><span>Liquidationen 5m</span>
                  <span className="mono pos">{usd(liq5.long_usd)} L</span>
                  <span className="mono neg">{usd(liq5.short_usd)} S</span></div>
                {liq5.cascade && <div className="lq-cascade">⚠ Liquidations-Kaskade aktiv</div>}
                <div className="lq-line"><span>Orderbook-Walls</span>
                  <span className="mono">{(walls.bids || []).length} Bid</span>
                  <span className="mono">{(walls.asks || []).length} Ask</span></div>
                <div className="lq-hint">
                  Bias: {ls.bias || 'keine Positionsdaten'} · Quellen: {(market.sources || []).join(', ') || '–'}
                </div>
              </div>
            </div>
          </div>
        )}
        <div className="lq-foot">
          Alle Daten stammen aus freien Börsen-Endpunkten (OKX, Binance, Bybit). Die Heatmap ist eine
          <b> Schätzung</b> aus Kerzen-Volumen, Open-Interest-Veränderung und typischen Hebelstufen –
          keine Börsen-Wahrheit. Der KI Trader nutzt genau diese Daten in seinen Analysen.
        </div>
      </div>
    </SafeOverlay>
  );
}
