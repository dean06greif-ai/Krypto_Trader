// Zentrale Zeit-Formatierung: ALLE Anzeigen laufen über Europe/Berlin
// (inkl. automatischer Sommer-/Winterzeit), unabhängig von der Browser-Zeitzone.
export const BERLIN_TZ = 'Europe/Berlin';

const safe = (ts, fn) => {
  if (!ts && ts !== 0) return '–';
  try {
    const d = new Date(ts);
    if (Number.isNaN(d.getTime())) return '–';
    return fn(d);
  } catch {
    return '–';
  }
};

export const fmtBerlin = (ts, opts = {}) =>
  safe(ts, (d) => d.toLocaleString('de-DE', { timeZone: BERLIN_TZ, ...opts }));

export const fmtBerlinDate = (ts, opts = {}) =>
  safe(ts, (d) => d.toLocaleDateString('de-DE', { timeZone: BERLIN_TZ, ...opts }));

export const fmtBerlinTime = (ts, opts = {}) =>
  safe(ts, (d) => d.toLocaleTimeString('de-DE', { timeZone: BERLIN_TZ, ...opts }));

// Kurzformat "TT.MM., HH:MM" für Listen/Verläufe
export const fmtBerlinShort = (ts) =>
  fmtBerlin(ts, { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
