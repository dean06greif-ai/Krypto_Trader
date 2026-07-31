"""Regime-Lab: Regime-Analysen erstellen, speichern und für die
regime-gezielte Strategie-Suche wiederverwenden.

Kernidee (siehe Anforderungen):
- Für eine Konfiguration (Coins + Timeframe + Zeitraum + Regime-Einstellungen)
  werden Marktphasen gesucht und gespeichert – kombiniert über alle Coins UND
  je Coin einzeln, damit man vergleichen kann, ob Coins ähnliche Phasen haben.
- Die Analyse speichert je Coin einen komprimierten Kursverlauf + die
  Regime-Abschnitte, damit das Frontend die Phasen direkt am Chart anzeigen kann.
- Optionaler Holdout (train_pct < 100): Das Regime-Modell wird NUR auf dem
  Trainingsteil geclustert; der hintere Teil bleibt unangetastet für den
  finalen Walk-Forward-Test der zusammengestellten dynamischen Strategie.
- Klassifikation ist rein rückblickend (services.regime) -> kein Lookahead.
"""
import asyncio
import bisect
import logging
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

import aiohttp

from services import regime as rg
from services import regime_engine as eng
from services.backtester import JobCancelled

logger = logging.getLogger(__name__)

JOBS: Dict[str, Dict] = {}

CHART_MAX_POINTS = 1200
MAX_ANALYSES = 40


def create_job(kind: str, params: Dict) -> str:
    job_id = uuid.uuid4().hex[:12]
    JOBS[job_id] = {"id": job_id, "kind": kind, "status": "running", "progress": 0,
                    "phase": "Startet", "params": params, "cancel": False,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "result": None, "error": None}
    if len(JOBS) > 10:
        for k in list(JOBS.keys())[:-10]:
            JOBS.pop(k, None)
    return job_id


def running_job() -> Optional[Dict]:
    for j in JOBS.values():
        if j.get("status") == "running":
            return j
    return None


async def fetch_histories(symbols: List[str], days: int, timeframe: str,
                          job: Dict = None, end_ts: Dict[str, int] = None,
                          progress_span=(0, 10)) -> Dict[str, List[Dict]]:
    """Kerzen laden + auf den Timeframe aggregieren. Mit end_ts (aus einer
    gespeicherten Analyse) werden die Daten exakt auf den Analyse-Zeitraum
    geschnitten, damit spätere Läufe reproduzierbar bleiben."""
    from services.backtester import fetch_history
    from services.timeframes import aggregate_candles
    histories: Dict[str, List[Dict]] = {}
    p0, p1 = progress_span
    async with aiohttp.ClientSession() as session:
        for i, sym in enumerate(symbols):
            if job and job.get("cancel"):
                raise JobCancelled()
            if job:
                job["phase"] = f"Lade Daten: {sym}"
                job["progress"] = p0 + round(i / max(len(symbols), 1) * (p1 - p0))
            raw = await fetch_history(session, sym, days, job=job)
            candles = aggregate_candles(raw, timeframe)
            del raw
            if end_ts and end_ts.get(sym):
                candles = [c for c in candles if c["timestamp"] <= end_ts[sym]]
            if len(candles) > 100:
                histories[sym] = candles
    return histories


def _downsample(candles: List[Dict], max_pts: int = CHART_MAX_POINTS) -> List[List]:
    step = max(len(candles) // max_pts, 1)
    pts = [[int(c["timestamp"]), float(c["close"])] for c in candles[::step]]
    last = candles[-1]
    if pts and pts[-1][0] != int(last["timestamp"]):
        pts.append([int(last["timestamp"]), float(last["close"])])
    return pts


def _segments_payload(candles: List[Dict], labels: List) -> List[Dict]:
    out = []
    for (s, e, rid) in rg.segments_from_labels(labels):
        out.append({"regime": int(rid),
                    "from_ts": int(candles[s]["timestamp"]),
                    "to_ts": int(candles[min(e, len(candles) - 1)]["timestamp"]),
                    "bars": int(e - s)})
    return out


def _validation_payload(candles: List[Dict], labels: List, model: Dict) -> Optional[Dict]:
    """Logische Prüfung der erkannten Regime (nur Engine v2): passt jedes Label
    zum tatsächlichen Kursverlauf? Ergebnis wird mit der Analyse gespeichert."""
    if not rg.is_v2(model):
        return None
    try:
        return eng.validate_labels(candles, labels, model)
    except Exception as e:  # noqa: BLE001 – Prüfung darf die Analyse nie killen
        logger.warning(f"regime validation failed: {e}")
        return None


def _ideal_payload(candles: List[Dict], labels: List, model: Dict) -> Optional[Dict]:
    """Rückblick-Vergleich ("so lagen die Phasen wirklich") – NUR zur Anzeige,
    nutzt Zukunftssicht und wird nie für Backtests/Live verwendet."""
    if not rg.is_v2(model):
        return None
    try:
        mode = eng.norm_mode((model.get("config") or {}).get("regime_mode", 9))
        ideal = eng.ideal_labels(model, candles)
        return {"segments": _segments_payload(candles, ideal),
                "agreement": eng.agreement_with_ideal(labels, ideal, mode),
                "lookahead": True}
    except Exception as e:  # noqa: BLE001
        logger.warning(f"ideal labels failed: {e}")
        return None


def _current_payload(candles: List[Dict], model: Dict, timeframe: str,
                     conf_min: float, min_hold_days: float) -> Optional[Dict]:
    """Aktuelles Regime am Ende der Analysedaten (für die Anzeige im Regime-Lab)."""
    try:
        cur = rg.current_regime(model, candles, timeframe, conf_min, min_hold_days)
        return {k: v for k, v in cur.items() if k != "similarities"}
    except Exception as e:  # noqa: BLE001
        logger.warning(f"current regime failed: {e}")
        return None


def _validation_summary(per_symbol: Dict[str, Dict]) -> Dict:
    reps = [v.get("validation") for v in per_symbol.values() if v.get("validation")]
    if not reps:
        return {}
    bars = [r["violation_bars_pct"] for r in reps]
    accs = [r["direction_accuracy_pct"] for r in reps
            if r.get("direction_accuracy_pct") is not None]
    return {"symbols": len(reps),
            "violation_bars_pct": round(sum(bars) / len(bars), 2),
            "worst_violation_bars_pct": round(max(bars), 2),
            "direction_accuracy_pct": (round(sum(accs) / len(accs), 1) if accs else None),
            "avg_segment_days": round(sum(r["avg_segment_days"] for r in reps)
                                      / len(reps), 2),
            "passed": all(r["passed"] for r in reps)}


def _regime_usage(segments_by_sym: Dict[str, List[Dict]], timeframe: str) -> Dict:
    """Wie viele Bars/Tage entfallen je Regime auf die Analyse? (Plausibilitäts-Check)"""
    bpd = rg.bars_per_day(timeframe)
    usage: Dict[int, Dict] = {}
    for segs in segments_by_sym.values():
        for s in segs:
            u = usage.setdefault(s["regime"], {"bars": 0, "segments": 0})
            u["bars"] += s["bars"]
            u["segments"] += 1
    return {str(k): {"bars": v["bars"], "segments": v["segments"],
                     "days": round(v["bars"] / max(bpd, 1e-9), 1)}
            for k, v in usage.items()}


def _coin_similarity(histories: Dict[str, List[Dict]],
                     labels_map: Dict[str, List]) -> List[Dict]:
    """Anteil der Zeit, in der zwei Coins (unter dem kombinierten Modell) im
    selben Regime sind – hilft beim Finden von Coins mit ähnlichen Phasen."""
    ts_maps = {}
    for sym, candles in histories.items():
        labels = labels_map.get(sym) or []
        ts_maps[sym] = {int(candles[i]["timestamp"]): labels[i]
                        for i in range(len(labels)) if labels[i] is not None}
    syms = sorted(ts_maps.keys())
    out = []
    for i in range(len(syms)):
        for j in range(i + 1, len(syms)):
            a, b = ts_maps[syms[i]], ts_maps[syms[j]]
            common = a.keys() & b.keys()
            if not common:
                continue
            same = sum(1 for t in common if a[t] == b[t])
            out.append({"a": syms[i], "b": syms[j],
                        "agreement_pct": round(same / len(common) * 100, 1),
                        "bars": len(common)})
    out.sort(key=lambda x: -x["agreement_pct"])
    return out


def _symbol_payload(model: Dict, candles, timeframe: str, conf_min: float,
                    min_hold_days: float, with_ideal: bool):
    """CPU-lastige Auswertung EINES Symbols (läuft in einem Thread, damit der
    Event-Loop – und damit Worker-Heartbeat/API – nie blockiert)."""
    labels = rg.classify_series(model, candles, timeframe, conf_min, min_hold_days)
    entry = {"segments": _segments_payload(candles, labels),
             "validation": _validation_payload(candles, labels, model),
             "current": _current_payload(candles, model, timeframe,
                                         conf_min, min_hold_days),
             "ideal": (_ideal_payload(candles, labels, model)
                       if with_ideal else None)}
    return labels, entry


async def run_analysis(job_id: str, body: Dict, db):
    """Regime-Analyse-Job: Modelle clustern (kombiniert + je Coin), Abschnitte
    berechnen und alles als wiederverwendbare Analyse speichern."""
    job = JOBS[job_id]
    try:
        symbols = body.get("symbols") or []
        timeframe = body.get("timeframe") or "5m"
        days = int(min(max(int(body.get("days") or 180), 7), 5500))
        scope = body.get("scope") or "both"
        max_regimes = int(min(max(int(body.get("max_regimes") or 5), 2), 10))
        lookback_days = float(min(max(float(body.get("lookback_days") or 3), 0.5), 60))
        min_share = float(min(max(float(body.get("min_share_pct") or 5), 1), 30))
        conf_min = float(min(max(float(body.get("confidence_min") or 70), 50), 95)) / 100.0
        min_hold_days = float(min(max(float(body.get("min_hold_days") or 2), 0.25), 60))
        train_pct = float(min(max(float(body.get("train_pct") or 100), 50), 100))
        engine = (body.get("engine") or rg.DEFAULT_ENGINE).lower()
        engine_config = body.get("engine_config") or {}
        if engine == "v2":
            # Wechsel-Einstellungen der Oberfläche gelten auch für die Engine v2.
            # Wichtig: die Mindesthaltedauer wird bei aktiver automatischer
            # Anpassung NICHT aus der Oberfläche übernommen – sonst würde der
            # Standardwert (2 Tage) die zeitraum-abhängige Glättung aushebeln.
            auto_on = bool(engine_config.get("auto_adapt",
                                             eng.DEFAULT_CONFIG["auto_adapt"]))
            prof = str(engine_config.get("adapt_profile",
                                        eng.DEFAULT_CONFIG["adapt_profile"])).lower()
            engine_config = {**engine_config,
                             "confidence_min": engine_config.get("confidence_min", conf_min)}
            if not (auto_on and prof != "off"):
                engine_config["min_hold_days"] = engine_config.get("min_hold_days",
                                                                   min_hold_days)
            # Granularität (3/5/9) darf auch direkt im Body stehen
            if body.get("regime_mode") is not None:
                engine_config["regime_mode"] = eng.norm_mode(body["regime_mode"])
            if body.get("adapt_profile"):
                engine_config["adapt_profile"] = str(body["adapt_profile"]).lower()
            if body.get("auto_adapt") is not None:
                engine_config["auto_adapt"] = bool(body["auto_adapt"])
        with_ideal = bool(body.get("with_ideal", True))

        histories = await fetch_histories(symbols, days, timeframe, job)
        if not histories:
            raise RuntimeError("Zu wenig Daten für diesen Timeframe/Zeitraum")

        def stop():
            return bool(job.get("cancel"))

        bounds = {}
        train_hist = {}
        for sym, candles in histories.items():
            cut = int(len(candles) * train_pct / 100.0)
            cut = min(max(cut, 100), len(candles))
            train_hist[sym] = candles[:cut]
            bounds[sym] = {"start_ts": int(candles[0]["timestamp"]),
                           "end_ts": int(candles[-1]["timestamp"]),
                           "train_end_ts": (int(candles[cut - 1]["timestamp"])
                                            if cut < len(candles) else None),
                           "bars": len(candles)}

        combined = None
        if scope in ("both", "combined"):
            if stop():
                raise JobCancelled()
            job["phase"] = "Kombiniertes Regime-Modell clustern (alle Coins)"
            job["progress"] = 20
            model = await asyncio.to_thread(
                rg.detect_regimes, train_hist, timeframe, max_regimes,
                lookback_days, min_share, engine=engine,
                engine_config=engine_config)
            if model:
                labels_map, per_symbol = {}, {}
                for sym, candles in histories.items():
                    if stop():
                        raise JobCancelled()
                    job["phase"] = f"Regime klassifizieren: {sym}"
                    labels, entry = await asyncio.to_thread(
                        _symbol_payload, model, candles, timeframe,
                        conf_min, min_hold_days, with_ideal)
                    labels_map[sym] = labels
                    per_symbol[sym] = entry
                segs_by_sym = {s: v["segments"] for s, v in per_symbol.items()}
                combined = {"model": model, "per_symbol": per_symbol,
                            "usage": _regime_usage(segs_by_sym, timeframe),
                            "validation": _validation_summary(per_symbol),
                            "coin_similarity": _coin_similarity(histories, labels_map)}
        per_coin = {}
        if scope in ("both", "per_coin"):
            for i, (sym, candles) in enumerate(histories.items()):
                if stop():
                    raise JobCancelled()
                job["phase"] = f"Regime-Modell je Coin: {sym}"
                job["progress"] = 40 + round(i / max(len(histories), 1) * 50)
                model_s = await asyncio.to_thread(
                    rg.detect_regimes, {sym: train_hist[sym]}, timeframe,
                    max_regimes, lookback_days, min_share,
                    engine=engine, engine_config=engine_config)
                if not model_s:
                    per_coin[sym] = {"error": "Zu wenig Daten für dieses Coin-Modell"}
                    continue
                labels, entry = await asyncio.to_thread(
                    _symbol_payload, model_s, candles, timeframe,
                    conf_min, min_hold_days, with_ideal)
                segs = entry["segments"]
                per_coin[sym] = {"model": model_s, **entry,
                                 "usage": _regime_usage({sym: segs}, timeframe)}

        if not combined and not per_coin:
            raise RuntimeError("Regime konnten nicht bestimmt werden – Zeitraum erhöhen")

        job["phase"] = "Analyse speichern"
        job["progress"] = 95
        aid = f"ra_{uuid.uuid4().hex[:8]}"
        doc = {"id": aid,
               "name": body.get("name") or f"Regime-Analyse {timeframe} · {days}d",
               "symbols": list(histories.keys()), "timeframe": timeframe,
               "days": days, "scope": scope,
               "settings": {"max_regimes": max_regimes, "lookback_days": lookback_days,
                            "min_share_pct": min_share,
                            "confidence_min": round(conf_min * 100, 0),
                            "min_hold_days": min_hold_days, "train_pct": train_pct,
                            "engine": engine, "engine_config": engine_config,
                            "regime_mode": (eng.norm_mode(
                                engine_config.get("regime_mode",
                                                  eng.DEFAULT_REGIME_MODE))
                                if engine == "v2" else None)},
               "bounds": bounds,
               "chart": {sym: _downsample(c) for sym, c in histories.items()},
               "combined": combined, "per_coin": per_coin,
               "kept": {}, "assignments": {}, "walkforward": {},
               "created_at": datetime.now(timezone.utc).isoformat()}
        result = {"kind": "analysis", "analysis_id": aid}
        if db is not None:
            await persist_analysis(db, doc)
        else:
            # Lokaler Worker: kein DB-Zugriff – Dokument mit dem Ergebnis
            # zurückschicken, der Server persistiert es (persist_worker_result).
            result["analysis_doc"] = doc
        job["result"] = result
        job["status"] = "done"
        job["progress"] = 100
        job["phase"] = "Fertig"
    except JobCancelled:
        job["status"] = "cancelled"
        job["phase"] = "Abgebrochen"
    except Exception as e:  # noqa: BLE001 – Job-Fehler sauber melden
        logger.exception(f"regime analysis {job_id} failed")
        job["status"] = "error"
        job["error"] = str(e)[:300]
        job["phase"] = "Fehler"


# ---------------- Wissenschaftliche Kalibrierung ----------------
async def run_calibration(job_id: str, body: Dict, db):
    """Kalibrierungs-Job: Referenz-Regime (zentriert/HMM) berechnen und die
    Engine-Parameter daran messen/optimieren (services.regime_truth)."""
    job = JOBS[job_id]
    try:
        from services import regime_truth as rt
        symbols = body.get("symbols") or []
        timeframe = body.get("timeframe") or "15m"
        days = int(min(max(int(body.get("days") or 360), 30), 5500))
        source = (body.get("truth_source") or "centered").lower()
        engine_config = dict(body.get("engine_config") or {})
        if body.get("regime_mode") is not None:
            engine_config["regime_mode"] = eng.norm_mode(body["regime_mode"])

        histories = await fetch_histories(symbols, days, timeframe, job)
        if not histories:
            raise RuntimeError("Zu wenig Daten für diesen Timeframe/Zeitraum")

        def stop():
            return bool(job.get("cancel"))

        def prog(pct, phase):
            job["progress"] = int(min(max(pct, 0), 99))
            job["phase"] = str(phase)[:200]

        report = await asyncio.to_thread(rt.calibrate, histories, timeframe,
                                         engine_config, source, stop, prog)
        if report is None:
            raise JobCancelled()
        job["result"] = {"kind": "calibration", "report": report}
        job["status"] = "done"
        job["progress"] = 100
        job["phase"] = "Fertig"
        if db is not None:
            try:
                await db.regime_calibrations.insert_one(
                    {"id": job_id, "created_at": datetime.now(timezone.utc).isoformat(),
                     "report": report})
            except Exception as e:  # noqa: BLE001
                logger.warning(f"calibration persist failed: {e}")
    except JobCancelled:
        job["status"] = "cancelled"
        job["phase"] = "Abgebrochen"
    except Exception as e:  # noqa: BLE001
        logger.exception(f"regime calibration {job_id} failed")
        job["status"] = "error"
        job["error"] = str(e)[:300]
        job["phase"] = "Fehler"


# ---------------- Persistierung (auch für lokal berechnete Jobs) ----------------
async def persist_analysis(db, doc: Dict):
    await db.regime_analyses.replace_one({"id": doc["id"]}, doc, upsert=True)
    n = await db.regime_analyses.count_documents({})
    if n > MAX_ANALYSES:
        old = await db.regime_analyses.find().sort("created_at", 1) \
            .limit(n - MAX_ANALYSES).to_list(n)
        for o in old:
            await db.regime_analyses.delete_one({"id": o["id"]})


async def persist_worker_result(db, job_id: str, job: Dict):
    """Ergebnis eines auf dem lokalen Worker berechneten Regime-Lab-Jobs
    serverseitig speichern (der Worker hat keinen Datenbank-Zugriff)."""
    res = job.get("result") or {}
    kind = res.get("kind")
    if kind == "analysis" and res.get("analysis_doc"):
        await persist_analysis(db, res.pop("analysis_doc"))
    elif kind == "calibration":
        await db.regime_calibrations.insert_one(
            {"id": job_id, "created_at": datetime.now(timezone.utc).isoformat(),
             "report": res.get("report")})
    elif kind == "regime_opt":
        await db.regime_lab_runs.replace_one(
            {"id": job_id}, {"id": job_id, "result": res,
                             "created_at": res.get("created_at")}, upsert=True)
    elif kind == "walkforward":
        key = scope_key(res.get("scope") or "combined", res.get("symbol"))
        await db.regime_analyses.update_one(
            {"id": res.get("analysis_id")},
            {"$set": {f"walkforward.{key}":
                      {k: v for k, v in res.items() if k != "points"}}})


# ---------------- Wiederverwendung gespeicherter Analysen ----------------
def model_for(doc: Dict, scope: str, symbol: str = None) -> Optional[Dict]:
    if scope == "per_coin":
        return ((doc.get("per_coin") or {}).get(symbol) or {}).get("model")
    return ((doc.get("combined") or {}).get("model"))


def scope_key(scope: str, symbol: str = None) -> str:
    return f"per_coin:{symbol}" if scope == "per_coin" else "combined"


def regime_ranges(doc: Dict, scope: str, symbol: str, sym: str,
                  regime_id: int, only_train: bool = True) -> List[Dict]:
    """Gespeicherte Zeitbereiche eines Regimes für ein Symbol; optional auf den
    Trainingsteil geschnitten (der Holdout bleibt für den Walk-Forward unberührt)."""
    if scope == "per_coin":
        segs = ((doc.get("per_coin") or {}).get(symbol) or {}).get("segments") or []
    else:
        segs = (((doc.get("combined") or {}).get("per_symbol") or {})
                .get(sym) or {}).get("segments") or []
    train_end = (doc.get("bounds") or {}).get(sym, {}).get("train_end_ts")
    out = []
    for s in segs:
        if s["regime"] != regime_id:
            continue
        from_ts, to_ts = s["from_ts"], s["to_ts"]
        if only_train and train_end:
            if from_ts > train_end:
                continue
            to_ts = min(to_ts, train_end)
        out.append({"from_ts": from_ts, "to_ts": to_ts})
    return out


def segments_from_ranges(candles: List[Dict], ranges: List[Dict], regime_id: int,
                         warmup_bars: int) -> List[Dict]:
    """Zeitbereiche auf (beliebige, ggf. andere Timeframe-) Kerzen abbilden –
    inkl. Warmup-Vorlauf, damit Indikatoren korrekt anlaufen."""
    ts = [c["timestamp"] for c in candles]
    segs = []
    for r in ranges:
        s = bisect.bisect_left(ts, r["from_ts"])
        e = bisect.bisect_right(ts, r["to_ts"])
        if e - s < 10:
            continue
        w0 = max(s - warmup_bars, 0)
        segs.append({"regime": regime_id, "start_ts": candles[s]["timestamp"],
                     "candles": candles[w0:e], "n_bars": e - s})
    return segs
