/**
 * Farbschema der Regime.
 *
 * Engine v2 (ID = Trend-Index * 3 + Vola-Index):
 *   Abwärts  = Rottöne, Seitwärts = Gelbtöne, Aufwärts = Grüntöne
 *   Innerhalb einer Richtung wird die Farbe mit steigender Volatilität kräftiger
 *   (niedrig = blass, mittel = normal, hoch = intensiv).
 * Alte Cluster-Modelle (kmeans) haben keine feste Bedeutung je ID -> Fallback-Palette.
 */
export const REGIME_FALLBACK_COLORS = ['#30D158', '#FF453A', '#FFD60A', '#64D2FF',
  '#BF5AF2', '#FF9F0A', '#5E5CE6', '#FF6482', '#66D4CF', '#A2845E'];

// [niedrige Vola, mittlere Vola, hohe Vola]
const V2_COLORS = {
  down: ['#F08C8C', '#E03B3B', '#A8121B'],
  side: ['#F2E3A0', '#EBC034', '#C08A00'],
  up: ['#9BE3B0', '#2FCB6E', '#0E8F43'],
};

const TREND_KEYS = ['down', 'side', 'up'];

/** Farbe für eine Regime-ID der Engine v2 (0..8). */
export function v2RegimeColor(id) {
  const t = TREND_KEYS[Math.floor(id / 3)] || 'side';
  const v = Math.min(Math.max(id % 3, 0), 2);
  return V2_COLORS[t][v];
}

/**
 * Farbe für ein Regime. `regimes` ist die Regime-Liste des Modells; enthält sie
 * trend/vol (Engine v2), wird das semantische Schema genutzt.
 */
export function regimeColor(id, regimes) {
  const r = (regimes || []).find(x => x.id === id);
  if (r && r.trend && r.vol) {
    const t = TREND_KEYS.includes(r.trend) ? r.trend : 'side';
    const v = ['low', 'mid', 'high'].indexOf(r.vol);
    return V2_COLORS[t][v < 0 ? 1 : v];
  }
  if (r && (r.nnfx || r.stats?.score !== undefined)) return v2RegimeColor(id);
  return REGIME_FALLBACK_COLORS[id % REGIME_FALLBACK_COLORS.length];
}

/** Deckkraft der Chart-Bänder: höhere Vola = kräftiger. */
export function regimeOpacity(id, regimes) {
  const r = (regimes || []).find(x => x.id === id);
  const v = r?.vol ? ['low', 'mid', 'high'].indexOf(r.vol) : (id % 3);
  return [0.12, 0.18, 0.26][v < 0 ? 1 : v];
}
