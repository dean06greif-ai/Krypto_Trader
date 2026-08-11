// Zentraler Toast-Wrapper (sonner) mit Dedupe für Fehler-Popups:
// Dieselbe Fehlermeldung poppt nur EINMAL auf (5-min-Fenster). Unterdrückte
// Wiederholungen landen stattdessen in den Website-Benachrichtigungen (Glocke).
import { toast as base } from 'sonner';

const API_URL = process.env.REACT_APP_BACKEND_URL;
const DEDUPE_MS = 5 * 60 * 1000;
const seen = new Map();

const isDuplicate = (key) => {
  const now = Date.now();
  for (const [k, t] of seen) { if (now - t > DEDUPE_MS) seen.delete(k); }
  const dup = now - (seen.get(key) || 0) < DEDUPE_MS;
  seen.set(key, now);
  return dup;
};

const pushToBell = (message) => {
  try {
    fetch(`${API_URL}/api/notifications`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title: 'Wiederholter Fehler', message: String(message), kind: 'error' }),
    }).catch(() => {});
  } catch (e) { /* still */ }
};

const toast = (...args) => base(...args);
Object.assign(toast, base);

toast.error = (message, opts) => {
  if (typeof message === 'string' && isDuplicate(`err:${message}`)) {
    pushToBell(message);
    return undefined;
  }
  return base.error(message, opts);
};

toast.warning = (message, opts) => {
  if (typeof message === 'string' && isDuplicate(`warn:${message}`)) {
    pushToBell(message);
    return undefined;
  }
  return base.warning(message, opts);
};

export { toast };
