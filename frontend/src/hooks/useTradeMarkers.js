import { useEffect, useRef, useState } from 'react';
import { createSeriesMarkers } from 'lightweight-charts';

const API_URL = process.env.REACT_APP_BACKEND_URL;

const fmt = (v) => (v == null ? '–' : Number(v).toLocaleString('de-DE', { maximumFractionDigits: 6 }));

/**
 * Trade-Overlay im Haupt-Chart:
 *  - offene Trades: Entry/SL/TP als Preislinien + Entry-Pfeil (verschwinden beim Schließen)
 *  - geschlossene Trades (optional per Toggle): Entry-Pfeil + Exit-Punkt (grün/rot nach PnL)
 *  - `tradeMapRef`: Bar-Zeit -> Trade-Infos für den Hover-Tooltip (zeigt Strategie)
 */
export default function useTradeMarkers(seriesRef, symbol, showClosed, barSec, barsLoaded) {
  const linesRef = useRef([]);
  const markersRef = useRef(null);
  const tradeMapRef = useRef({});
  const [counts, setCounts] = useState({ open: 0, closed: 0 });

  useEffect(() => {
    let cancelled = false;

    const clearLines = () => {
      const series = seriesRef.current;
      linesRef.current.forEach(l => {
        try { series && series.removePriceLine(l); } catch (_) { /* noop */ }
      });
      linesRef.current = [];
    };
    const clearMarkers = () => {
      try { markersRef.current && markersRef.current.setMarkers([]); } catch (_) { /* noop */ }
    };

    const toBar = (iso) => {
      const t = Math.floor(new Date(iso).getTime() / 1000);
      return Math.floor(t / barSec) * barSec;
    };

    const apply = (trades) => {
      const series = seriesRef.current;
      if (!series) return;
      clearLines();
      const mine = trades.filter(t => t.symbol === symbol);
      const open = mine.filter(t => t.status === 'open');
      const closed = mine.filter(t => t.status === 'closed');

      // sichtbare Zeitspanne der geladenen Kerzen (Marker außerhalb weglassen)
      let first = 0;
      let last = Infinity;
      try {
        const data = series.data();
        if (data.length) { first = data[0].time; last = data[data.length - 1].time + barSec; }
      } catch (_) { /* noop */ }

      const addLine = (price, color, style, title) => {
        if (!price) return;
        try {
          linesRef.current.push(series.createPriceLine({
            price, color, lineWidth: 1, lineStyle: style,
            axisLabelVisible: true, title,
          }));
        } catch (_) { /* noop */ }
      };

      open.forEach(t => {
        const sideColor = t.side === 'LONG' ? '#00FF66' : '#FF3366';
        const strat = (t.strategy_name || t.strategy_id || '').slice(0, 18);
        addLine(t.entry, sideColor, 0, `${t.side} ${strat}`);
        addLine(t.sl, '#FF3366', 2, 'SL');
        if (t.qty_remaining !== 0 && !t.tp1_hit) addLine(t.tp1, '#00C77F', 2, 'TP1');
        addLine(t.tpf, '#00FF66', 2, 'TP');
      });

      const markers = [];
      const map = {};
      const remember = (time, info) => { (map[time] = map[time] || []).push(info); };

      open.forEach(t => {
        const time = toBar(t.opened_at);
        if (time < first || time > last) return;
        markers.push({
          time,
          position: t.side === 'LONG' ? 'belowBar' : 'aboveBar',
          shape: t.side === 'LONG' ? 'arrowUp' : 'arrowDown',
          color: t.side === 'LONG' ? '#00FF66' : '#FF3366',
        });
        remember(time, {
          label: `${t.side} offen · ${t.strategy_name || t.strategy_id || '?'}`,
          detail: `Entry ${fmt(t.entry)} · SL ${fmt(t.sl)} · TP ${fmt(t.tpf)} · ${t.mode || ''}`,
        });
      });

      if (showClosed) {
        closed.forEach(t => {
          const strat = t.strategy_name || t.strategy_id || '?';
          const win = (t.realized_pnl || 0) >= 0;
          const tIn = toBar(t.opened_at);
          if (tIn >= first && tIn <= last) {
            markers.push({
              time: tIn,
              position: t.side === 'LONG' ? 'belowBar' : 'aboveBar',
              shape: t.side === 'LONG' ? 'arrowUp' : 'arrowDown',
              color: '#7C8CA3',
            });
            remember(tIn, {
              label: `${t.side} Entry (geschlossen) · ${strat}`,
              detail: `Entry ${fmt(t.entry)} · PnL ${fmt(t.realized_pnl)} $`,
            });
          }
          if (t.closed_at) {
            const tOut = toBar(t.closed_at);
            if (tOut >= first && tOut <= last) {
              markers.push({
                time: tOut,
                position: 'inBar',
                shape: win ? 'circle' : 'square',
                color: win ? '#00FF66' : '#FF3366',
              });
              remember(tOut, {
                label: `${t.side} Exit · ${strat}`,
                detail: `Exit ${fmt(t.exit_price)} · PnL ${fmt(t.realized_pnl)} $ (${t.result || '–'})`,
              });
            }
          }
        });
      }

      markers.sort((a, b) => a.time - b.time);
      try {
        if (!markersRef.current) markersRef.current = createSeriesMarkers(series, markers);
        else markersRef.current.setMarkers(markers);
      } catch (_) { /* Chart evtl. gerade neu aufgebaut */ }
      tradeMapRef.current = map;
      setCounts({ open: open.length, closed: closed.length });
    };

    const load = async () => {
      try {
        const res = await fetch(`${API_URL}/api/autotrade/trades?limit=200`);
        const d = await res.json();
        if (!cancelled) apply(d.trades || []);
      } catch (_) { /* Netz-Race beim Laden ignorieren */ }
    };

    load();
    const iv = setInterval(load, 30000);
    return () => {
      cancelled = true;
      clearInterval(iv);
      clearLines();
      clearMarkers();
      tradeMapRef.current = {};
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbol, showClosed, barSec, barsLoaded]);

  return { tradeMapRef, counts };
}
