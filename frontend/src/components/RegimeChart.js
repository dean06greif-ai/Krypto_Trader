import React, { useMemo, useState } from 'react';
import {
  ComposedChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer,
  ReferenceArea, ReferenceLine,
} from 'recharts';

import { regimeColor, regimeOpacity, REGIME_FALLBACK_COLORS } from '../lib/regimeColors';
import { fmtDateTime } from '../lib/time';

export const REGIME_COLORS = REGIME_FALLBACK_COLORS;

const fmtDate = (ts) => {
  const d = new Date(ts);
  return `${d.getDate()}.${d.getMonth() + 1}.${String(d.getFullYear()).slice(2)}`;
};

/**
 * Kursverlauf mit farbig hinterlegten Regime-Abschnitten.
 * prices: [[ts, close], ...] · segments: [{regime, from_ts, to_ts}]
 * regimes: [{id,label}] · trainEndTs: optionale Trennlinie Training/Holdout
 * liveSegments: optionales Band oben (unkorrigierte Live-Erkennung)
 */

// Anzeige-Segmente begrenzen, OHNE das Bild zu verfälschen: pro Zeit-"Pixel"
// (Bucket) gewinnt das Regime mit dem größten Zeitanteil (Dominanz-Bucketing).
// Vorher wurden schmale Abschnitte einfach in den VORGÄNGER gemerged – bei
// langen Zeiträumen (z.B. 2000 Tage) fraß ein Segment dutzende Nachfolger
// und halbe Charts wurden fälschlich einfarbig rot/grün.
const mergeForDisplay = (segments, minWidth) => {
  if (!segments || segments.length === 0) return [];
  // Wenige Segmente: exakt zeichnen, nur benachbarte gleiche verschmelzen.
  if (!minWidth || segments.length <= 320) {
    const out = [];
    for (const s of segments) {
      const last = out[out.length - 1];
      if (last && s.regime === last.regime) {
        last.to_ts = Math.max(last.to_ts, s.to_ts);
      } else {
        out.push({ ...s });
      }
    }
    return out;
  }
  const t0 = segments[0].from_ts;
  const t1 = segments[segments.length - 1].to_ts;
  const nb = Math.max(1, Math.min(2000, Math.ceil((t1 - t0) / minWidth)));
  const bw = (t1 - t0) / nb;
  const acc = Array.from({ length: nb }, () => ({}));
  for (const s of segments) {
    let b0 = Math.floor((s.from_ts - t0) / bw);
    let b1 = Math.floor((s.to_ts - t0) / bw);
    b0 = Math.max(0, Math.min(nb - 1, b0));
    b1 = Math.max(0, Math.min(nb - 1, b1));
    for (let b = b0; b <= b1; b++) {
      const bs = t0 + b * bw;
      const ov = Math.min(s.to_ts, bs + bw) - Math.max(s.from_ts, bs);
      if (ov > 0) acc[b][s.regime] = (acc[b][s.regime] || 0) + ov;
    }
  }
  const out = [];
  for (let b = 0; b < nb; b++) {
    const entries = Object.entries(acc[b]);
    if (!entries.length) continue;
    entries.sort((x, y) => y[1] - x[1]);
    const reg = Number(entries[0][0]);
    const bs = t0 + b * bw;
    const be = Math.min(bs + bw, t1);
    const last = out[out.length - 1];
    if (last && last.regime === reg && bs - last.to_ts < bw / 2) {
      last.to_ts = be;
    } else {
      out.push({ regime: reg, from_ts: bs, to_ts: be });
    }
  }
  return out;
};

function RegimeChart({ title, prices, segments, idealSegments, liveSegments, regimes, model, trainEndTs, height = 190 }) {
  const [hidden, setHidden] = useState({});
  const data = useMemo(() => (prices || []).map(p => ({ t: p[0], c: p[1] })), [prices]);
  const span = data.length ? data[data.length - 1].t - data[0].t : 0;
  const minW = span / 300;
  const dispSegments = useMemo(() => mergeForDisplay(segments, minW), [segments, minW]);
  const dispLive = useMemo(() => mergeForDisplay(liveSegments, minW), [liveSegments, minW]);
  const dispIdeal = useMemo(() => mergeForDisplay(idealSegments, minW), [idealSegments, minW]);
  if (!data.length) return null;
  const [min, max] = data.reduce((a, p) => [Math.min(a[0], p.c), Math.max(a[1], p.c)],
    [Infinity, -Infinity]);
  const pad = (max - min) * 0.04;
  const band = (max - min + 2 * pad) * 0.07;
  const labelOf = (rid) => (regimes || []).find(r => r.id === rid)?.label || `Regime ${rid + 1}`;

  return (
    <div className="rl-chart" data-testid={`regime-chart-${title || 'chart'}`}>
      {title && <div className="rl-chart-title">{title}</div>}
      <ResponsiveContainer width="100%" height={height}>
        <ComposedChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
          {dispSegments.filter(s => !hidden[s.regime]).map((s, i) => (
            <ReferenceArea key={i} x1={s.from_ts} x2={s.to_ts}
              y1={min - pad} y2={max + pad}
              fill={regimeColor(s.regime, regimes, model)}
              fillOpacity={regimeOpacity(s.regime, regimes, model)} strokeOpacity={0} />
          ))}
          {dispLive.map((s, i) => (
            <ReferenceArea key={`live-${i}`} x1={s.from_ts} x2={s.to_ts}
              y1={max + pad - band} y2={max + pad}
              fill={regimeColor(s.regime, regimes, model)}
              fillOpacity={0.85} strokeOpacity={0} />
          ))}
          {dispIdeal.map((s, i) => (
            <ReferenceArea key={`ideal-${i}`} x1={s.from_ts} x2={s.to_ts}
              y1={min - pad} y2={min - pad + band}
              fill={regimeColor(s.regime, regimes, model)}
              fillOpacity={0.8} strokeOpacity={0} />
          ))}
          {trainEndTs && (
            <ReferenceLine x={trainEndTs} stroke="#ffa502" strokeDasharray="4 4"
              label={{ value: 'Holdout →', fill: '#ffa502', fontSize: 10, position: 'insideTopRight' }} />
          )}
          <XAxis dataKey="t" type="number" domain={['dataMin', 'dataMax']}
            tickFormatter={fmtDate} tick={{ fontSize: 10, fill: '#8b90a0' }}
            stroke="#262a38" />
          <YAxis domain={[min - pad, max + pad]} tick={{ fontSize: 10, fill: '#8b90a0' }}
            stroke="#262a38" width={62}
            tickFormatter={(v) => (v >= 1000 ? v.toFixed(0) : v.toPrecision(4))} />
          <Tooltip
            contentStyle={{ background: '#12141d', border: '1px solid #262a38', fontSize: 11 }}
            labelFormatter={(ts) => {
              const seg = (segments || []).find(s => ts >= s.from_ts && ts <= s.to_ts);
              return `${fmtDateTime(ts)}${seg ? ` · ${labelOf(seg.regime)}` : ''}`;
            }}
            formatter={(v) => [Number(v).toPrecision(6), 'Kurs']} />
          <Line dataKey="c" dot={false} stroke="#c9cddb" strokeWidth={1.4} isAnimationActive={false} />
        </ComposedChart>
      </ResponsiveContainer>
      <div className="rl-legend">
        {(regimes || []).map(r => (
          <button key={r.id}
            className={`rl-legend-item ${hidden[r.id] ? 'off' : ''}`}
            onClick={() => setHidden(h => ({ ...h, [r.id]: !h[r.id] }))}
            data-testid={`regime-legend-${r.id}`}
            title="Klicken zum Ein-/Ausblenden der Markierung">
            <span className="rl-dot" style={{ background: regimeColor(r.id, regimes, model) }} />
            #{r.id + 1} {r.label}
          </button>
        ))}
      </div>
    </div>
  );
}

export default React.memo(RegimeChart);
