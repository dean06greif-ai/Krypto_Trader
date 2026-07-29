"""Regime-Lab Endpoints: Analysen erstellen/ansehen, Regime behalten/verwerfen,
Strategie-Suche je Regime, Zuordnungen bestätigen, dynamische Strategie bauen
und final per Walk-Forward auf dem Holdout testen."""
import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Dict

from fastapi import APIRouter, Depends, HTTPException

from core import state
from core.auth import require_admin
from core.state import scanner
from core.utils import _clean, _job_public, _watch_job_task
from services import regime_lab as lab
from services import regime_opt
from services.bitunix_trade import DEFAULT_COIN_CFG
from strategies.registry import registry as strategy_registry

logger = logging.getLogger(__name__)

router = APIRouter(tags=["regime-lab"])


def _guard_no_running():
    j = lab.running_job()
    if j:
        raise HTTPException(status_code=409,
                            detail=f"Es läuft bereits ein Regime-Lab-Job ({j['kind']})")


def _regime_id_or_400(body: Dict) -> int:
    rid = body.get("regime_id")
    if rid is None or not str(rid).lstrip("-").isdigit():
        raise HTTPException(status_code=400, detail="regime_id (Zahl) erforderlich")
    return int(rid)


async def _get_doc(aid: str) -> Dict:
    doc = await state.db.regime_analyses.find_one({"id": aid})
    if not doc:
        raise HTTPException(status_code=404, detail="Regime-Analyse nicht gefunden")
    return doc


def _slim_doc(doc: Dict) -> Dict:
    """Analyse-Dokument für den Worker-Payload: ohne Chart-Daten (unnötig groß)."""
    return {k: v for k, v in _clean(doc).items() if k != "chart"}


def _enqueue_local(kind_fn: str, job_id: str, body: Dict):
    from services import local_exec
    local_exec.enqueue_compute("regime_lab", job_id, {
        "kind": "regime_lab",
        "args": {"fn": kind_fn, "body": body,
                 "settings": dict(scanner.settings),
                 "default_cfg": dict(DEFAULT_COIN_CFG)},
        "custom_definitions": strategy_registry.list_custom_definitions(),
    })


def _check_local_available():
    from services import local_exec
    if not local_exec.worker_online():
        raise HTTPException(status_code=503,
                            detail="Kein lokaler Worker verbunden – Worker starten "
                                   "oder Cloud-Ausführung wählen")
    if not local_exec.worker_supports_regime_lab():
        raise HTTPException(status_code=409,
                            detail="Der verbundene lokale Worker ist veraltet und kennt "
                                   "Regime-Lab-Jobs noch nicht. Bitte das Worker-Paket neu "
                                   "herunterladen (Ausführung → Lokal → ⚙ Verwalten → "
                                   "Download) und den Worker neu starten – oder "
                                   "Cloud-Ausführung wählen.")


@router.post("/api/regime-lab/analyze")
async def start_analysis(body: Dict, _: bool = Depends(require_admin)):
    """Regime-Analyse starten: Regime für Coins/Timeframe/Zeitraum suchen und
    speichern – kombiniert über alle Coins und je Coin einzeln.
    execution=local rechnet auf dem lokalen Worker (empfohlen ab ~1000 Tagen)."""
    symbols = [s for s in (body.get("symbols") or []) if isinstance(s, str)]
    if not symbols:
        raise HTTPException(status_code=400, detail="Mindestens 1 Coin erforderlich")
    if (body.get("scope") or "both") not in ("both", "combined", "per_coin"):
        raise HTTPException(status_code=400, detail="scope muss both|combined|per_coin sein")
    if (body.get("engine") or "v2").lower() not in ("v2", "kmeans"):
        raise HTTPException(status_code=400, detail="engine muss v2|kmeans sein")
    _guard_no_running()
    execution = (body.get("execution") or "cloud").lower()
    params = {k: body.get(k) for k in
              ("symbols", "timeframe", "days", "scope", "max_regimes",
               "lookback_days", "min_share_pct", "confidence_min",
               "min_hold_days", "train_pct", "name", "engine", "engine_config")}
    params["execution"] = execution
    if execution == "local":
        _check_local_available()
        job_id = lab.create_job("analysis", params)
        _enqueue_local("analysis", job_id, body)
        return {"status": "started", "job_id": job_id, "execution": "local"}
    job_id = lab.create_job("analysis", params)
    task = asyncio.create_task(lab.run_analysis(job_id, body, state.db))
    _watch_job_task(task, lab.JOBS, job_id)
    return {"status": "started", "job_id": job_id}


@router.get("/api/regime-lab/status/{job_id}")
async def job_status(job_id: str):
    job = lab.JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job nicht gefunden")
    return _job_public(job)


@router.get("/api/regime-lab/active")
async def active_job():
    j = lab.running_job()
    return {"active": _job_public(j) if j else None}


@router.post("/api/regime-lab/cancel/{job_id}")
async def cancel_job(job_id: str, _: bool = Depends(require_admin)):
    job = lab.JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job nicht gefunden")
    job["cancel"] = True
    if job.get("status") == "running":
        job["phase"] = "Wird abgebrochen..."
    return {"status": "cancelling"}


@router.get("/api/regime-lab/list")
async def list_analyses():
    rows = await state.db.regime_analyses.find(
        {}, {"chart": 0, "combined.per_symbol": 0, "per_coin": 0}) \
        .sort("created_at", -1).to_list(lab.MAX_ANALYSES)
    out = []
    for r in rows:
        r = _clean(r)
        comb = r.get("combined") or {}
        out.append({"id": r["id"], "name": r.get("name"), "symbols": r.get("symbols"),
                    "timeframe": r.get("timeframe"), "days": r.get("days"),
                    "scope": r.get("scope"), "settings": r.get("settings"),
                    "created_at": r.get("created_at"),
                    "n_regimes_combined": len((comb.get("model") or {}).get("regimes") or []),
                    "n_assignments": len(r.get("assignments") or {}),
                    "has_walkforward": bool(r.get("walkforward"))})
    return {"analyses": out}


@router.get("/api/regime-lab/engine/defaults")
async def engine_defaults():
    """Standard-Einstellungen + Erklärungen der Regime-Engine v2 (für die
    Oberfläche) inklusive der 9er-Taxonomie und der NNFX-Zuordnung."""
    from services import regime_engine as eng
    return {"engine": eng.ENGINE, **eng.engine_defaults()}


@router.get("/api/regime-lab/run/{job_id}")
async def run_result(job_id: str):
    job = lab.JOBS.get(job_id)
    if job and job.get("result"):
        return {"result": job["result"]}
    doc = await state.db.regime_lab_runs.find_one({"id": job_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Ergebnis nicht gefunden")
    return {"result": _clean(doc).get("result")}


@router.get("/api/regime-lab/{aid}")
async def get_analysis(aid: str):
    doc = await _get_doc(aid)
    # Label-Migration: ältere Analysen auf die aktuelle Beschriftungs-Logik heben
    from services import regime as rg
    changed = False
    comb_model = ((doc.get("combined") or {}).get("model"))
    if comb_model and rg.relabel_regimes(comb_model):
        changed = True
    for pc in (doc.get("per_coin") or {}).values():
        if pc.get("model") and rg.relabel_regimes(pc["model"]):
            changed = True
    if changed:
        await state.db.regime_analyses.replace_one({"id": aid}, doc)
    return {"analysis": _clean(doc)}


@router.delete("/api/regime-lab/{aid}")
async def delete_analysis(aid: str, _: bool = Depends(require_admin)):
    res = await state.db.regime_analyses.delete_one({"id": aid})
    if not res.deleted_count:
        raise HTTPException(status_code=404, detail="Nicht gefunden")
    return {"status": "deleted"}


@router.post("/api/regime-lab/{aid}/keep")
async def keep_regime(aid: str, body: Dict, _: bool = Depends(require_admin)):
    """Regime behalten/verwerfen (nur Markierung – verworfene Regime werden bei
    der Strategie-Suche und beim Zusammenbau übersprungen)."""
    doc = await _get_doc(aid)
    key = f"{lab.scope_key(body.get('scope') or 'combined', body.get('symbol'))}:" \
          f"{_regime_id_or_400(body)}"
    kept = dict(doc.get("kept") or {})
    kept[key] = bool(body.get("keep", True))
    await state.db.regime_analyses.update_one({"id": aid}, {"$set": {"kept": kept}})
    return {"status": "success", "kept": kept}


@router.post("/api/regime-lab/{aid}/optimize")
async def start_regime_optimize(aid: str, body: Dict, _: bool = Depends(require_admin)):
    """Strategie-Discovery/Optimierung NUR für ein ausgewähltes Regime dieser
    Analyse (alle Optimizer-Einstellungen verfügbar)."""
    doc = await _get_doc(aid)
    rid = _regime_id_or_400(body)
    kept_key = f"{lab.scope_key(body.get('scope') or 'combined', body.get('symbol'))}:{rid}"
    if (doc.get("kept") or {}).get(kept_key) is False:
        raise HTTPException(status_code=400,
                            detail="Dieses Regime wurde verworfen – erst wieder auf "
                                   "'behalten' setzen")
    mode = body.get("mode") or "combo"
    if mode not in ("params", "discovery", "combo"):
        raise HTTPException(status_code=400, detail="mode muss params|discovery|combo sein")
    if mode == "params" and not strategy_registry.get(body.get("strategy_id") or ""):
        raise HTTPException(status_code=400, detail="Gültige strategy_id erforderlich")
    _guard_no_running()
    body["analysis_id"] = aid
    execution = (body.get("execution") or "cloud").lower()
    params = {k: body.get(k) for k in
              ("analysis_id", "scope", "symbol", "regime_id", "mode",
               "strategy_id", "timeframe", "objective", "iterations",
               "min_trades", "max_rules")}
    params["execution"] = execution
    if execution == "local":
        _check_local_available()
        job_id = lab.create_job("regime_opt", params)
        _enqueue_local("regime_opt", job_id, {**body, "analysis_doc": _slim_doc(doc)})
        return {"status": "started", "job_id": job_id, "execution": "local"}
    job_id = lab.create_job("regime_opt", params)
    task = asyncio.create_task(regime_opt.run_regime_optimizer(
        job_id, body, strategy_registry, scanner.settings, DEFAULT_COIN_CFG, state.db))
    _watch_job_task(task, lab.JOBS, job_id)
    return {"status": "started", "job_id": job_id}


@router.post("/api/regime-lab/{aid}/assign")
async def assign_regime_strategy(aid: str, body: Dict, _: bool = Depends(require_admin)):
    """Gefundene Strategie/Konfiguration für ein Regime bestätigen (oder mit
    remove=true wieder entfernen)."""
    doc = await _get_doc(aid)
    scope = body.get("scope") or "combined"
    rid = _regime_id_or_400(body)
    key = f"{lab.scope_key(scope, body.get('symbol'))}:{rid}"
    assignments = dict(doc.get("assignments") or {})
    if body.get("remove"):
        assignments.pop(key, None)
    else:
        cand = body.get("candidate") or {}
        model = lab.model_for(doc, scope, body.get("symbol")) or {}
        reg = next((r for r in model.get("regimes") or []
                    if r["id"] == rid), {})
        assignments[key] = {
            "regime_id": rid,
            "regime_label": reg.get("label"),
            "mode": cand.get("mode"),
            "strategy_id": cand.get("strategy_id"),
            "strategy_name": cand.get("strategy_name"),
            "definition": cand.get("definition"),
            "rules": cand.get("rules") or [],
            "trade_params": cand.get("trade_params") or {},
            "metrics": cand.get("metrics"),
            "validation": cand.get("validation"),
            "source_job_id": cand.get("source_job_id"),
            "assigned_at": datetime.now(timezone.utc).isoformat(),
        }
    await state.db.regime_analyses.update_one({"id": aid},
                                              {"$set": {"assignments": assignments}})
    return {"status": "success", "assignments": assignments}


@router.post("/api/regime-lab/{aid}/build")
async def build_dynamic(aid: str, body: Dict, _: bool = Depends(require_admin)):
    """Aus den bestätigten Regime-Strategien eine dynamische Strategie erzeugen
    (gleiches Format wie der Dynamik-Modus – Live-Umschaltung, Auto-Prüfung etc.
    funktionieren sofort)."""
    doc = await _get_doc(aid)
    scope = body.get("scope") or "combined"
    symbol = body.get("symbol")
    model = lab.model_for(doc, scope, symbol)
    if not model:
        raise HTTPException(status_code=400, detail="Kein Regime-Modell für diesen Bereich")
    assignments = regime_opt._assignment_items(doc, scope, symbol)
    if not assignments:
        raise HTTPException(status_code=400, detail="Keine bestätigten Regime-Strategien")
    sid = body.get("strategy_id")
    needs_base = any(not a.get("definition") for a in assignments.values())
    strategy = strategy_registry.get(sid or "")
    if not strategy:
        if needs_base:
            raise HTTPException(status_code=400,
                                detail="Basis-Strategie erforderlich (mind. ein Regime "
                                       "ohne eigene Regel-Definition)")
        sid = next(a.get("strategy_id") for a in assignments.values()
                   if a.get("definition")) or None
    # Regime ohne eigene Definition nutzen die Basis-Strategie; falls kein sid
    # aus der Registry, die erste Definition als Custom-Strategie registrieren.
    if not strategy_registry.get(sid or ""):
        first = next(a for a in assignments.values() if a.get("definition"))
        new_sid = f"custom_{uuid.uuid4().hex[:8]}"
        definition = {**first["definition"], "id": new_sid,
                      "name": (body.get("name") or "Regime-Lab") + " (Basis)",
                      "timeframe": doc.get("timeframe"),
                      "description": "Vom Regime-Lab erzeugte Basis-Strategie"}
        await state.db.custom_strategies.update_one({"id": new_sid},
                                                    {"$set": definition}, upsert=True)
        strategy_registry.upsert_custom(definition)
        sid = new_sid
    did = f"dyn_{uuid.uuid4().hex[:8]}"
    wf = (doc.get("walkforward") or {}).get(lab.scope_key(scope, symbol)) or {}
    dyn_doc = {"id": did,
               "name": body.get("name") or f"Regime-Lab: {doc.get('name')}",
               "strategy_id": sid,
               "symbols": [symbol] if scope == "per_coin" else doc.get("symbols") or [],
               "timeframe": doc.get("timeframe"),
               "model": model,
               "configs": {str(a["regime_id"]): a.get("trade_params") or {}
                           for a in assignments.values()},
               "fallback_config": {},
               "rule_variants": {},
               "sub_strategies": {str(a["regime_id"]):
                                  {"rules": a.get("rules") or [],
                                   "definition": a.get("definition")}
                                  for a in assignments.values() if a.get("definition")},
               "settings": {"confidence_min": (doc.get("settings") or {}).get("confidence_min"),
                            "min_hold_days": (doc.get("settings") or {}).get("min_hold_days"),
                            "auto_check_enabled": False, "auto_apply_enabled": False,
                            "check_interval_minutes": 60, "check_days": 30,
                            "source": "regime_lab", "analysis_id": aid},
               "verdict": wf.get("verdict") or {},
               "created_at": datetime.now(timezone.utc).isoformat(),
               "last_state": {}}
    await state.db.dynamic_strategies.replace_one({"id": did}, dyn_doc, upsert=True)
    return {"status": "success", "id": did, "strategy_id": sid,
            "regimes": sorted(assignments.keys())}


@router.post("/api/regime-lab/{aid}/build-nnfx")
async def build_nnfx(aid: str, body: Dict, _: bool = Depends(require_admin)):
    """NNFX-Framework anwenden: die erkannten Regime werden auf die drei
    NNFX-Regime (Trend / Seitwärts / Volatilität) gemappt und jedem Regime die
    passende NNFX-Strategie zugewiesen. Ergebnis ist eine dynamische Strategie,
    die im Live-/Paper-Betrieb automatisch die richtige Strategie aktiviert.

    Body: {scope, symbol?, name?, configs?: {regime_id: trade_params},
           strategy_overrides?: {trend|range|breakout: strategy_id}}
    """
    from services import regime_engine as eng
    from strategies.nnfx_strategies import NNFX_STRATEGY_BY_REGIME
    doc = await _get_doc(aid)
    scope = body.get("scope") or "combined"
    symbol = body.get("symbol")
    model = lab.model_for(doc, scope, symbol)
    if not model:
        raise HTTPException(status_code=400, detail="Kein Regime-Modell für diesen Bereich")
    if not model.get("engine"):
        raise HTTPException(status_code=400,
                            detail="NNFX benötigt eine Analyse mit der Regime-Engine v2")
    overrides = {k: v for k, v in (body.get("strategy_overrides") or {}).items()
                 if strategy_registry.get(v or "")}
    mapping, used = {}, {}
    kept = doc.get("kept") or {}
    key = lab.scope_key(scope, symbol)
    for r in model.get("regimes") or []:
        if kept.get(f"{key}:{r['id']}") is False:
            continue
        nnfx = r.get("nnfx") or eng.nnfx_regime(int(r["id"]))
        sid = overrides.get(nnfx) or NNFX_STRATEGY_BY_REGIME[nnfx]
        mapping[str(r["id"])] = sid
        used.setdefault(nnfx, sid)
    if not mapping:
        raise HTTPException(status_code=400, detail="Keine behaltenen Regime vorhanden")
    configs = {str(k): (v or {}) for k, v in (body.get("configs") or {}).items()}
    did = f"dyn_{uuid.uuid4().hex[:8]}"
    dyn_doc = {"id": did,
               "name": body.get("name") or f"NNFX: {doc.get('name')}",
               "framework": "nnfx",
               "strategy_id": mapping[sorted(mapping.keys())[0]],
               "regime_strategies": mapping,
               "symbols": [symbol] if scope == "per_coin" else doc.get("symbols") or [],
               "timeframe": doc.get("timeframe"),
               "model": model,
               "configs": configs,
               "fallback_config": {}, "rule_variants": {}, "sub_strategies": {},
               "settings": {"confidence_min": (doc.get("settings") or {}).get("confidence_min"),
                            "min_hold_days": (doc.get("settings") or {}).get("min_hold_days"),
                            "auto_check_enabled": False, "auto_apply_enabled": False,
                            "require_confirmation": bool(body.get("require_confirmation", True)),
                            "check_interval_minutes": 60, "check_days": 30,
                            "source": "regime_lab_nnfx", "analysis_id": aid},
               "verdict": {}, "created_at": datetime.now(timezone.utc).isoformat(),
               "last_state": {}}
    await state.db.dynamic_strategies.replace_one({"id": did}, dyn_doc, upsert=True)
    # Zusätzlich als Zuordnungen in der Analyse speichern -> der bestehende
    # Walk-Forward/Build-Weg kann die NNFX-Kombination direkt testen.
    if body.get("write_assignments", True):
        assignments = dict(doc.get("assignments") or {})
        now_iso = datetime.now(timezone.utc).isoformat()
        for rid_str, sid in mapping.items():
            reg = next((r for r in model.get("regimes") or []
                        if str(r["id"]) == rid_str), {})
            strat = strategy_registry.get(sid)
            assignments[f"{key}:{rid_str}"] = {
                "regime_id": int(rid_str), "regime_label": reg.get("label"),
                "mode": "params", "strategy_id": sid,
                "strategy_name": getattr(strat, "STRATEGY_NAME", sid),
                "definition": None, "rules": [],
                "trade_params": configs.get(rid_str) or {},
                "metrics": None, "validation": None,
                "source_job_id": None, "nnfx": reg.get("nnfx"),
                "assigned_at": now_iso}
        await state.db.regime_analyses.update_one({"id": aid},
                                                  {"$set": {"assignments": assignments}})
    return {"status": "success", "id": did, "regime_strategies": mapping,
            "nnfx_strategies": used}


@router.post("/api/regime-lab/{aid}/walkforward")
async def start_walkforward(aid: str, body: Dict, _: bool = Depends(require_admin)):
    """Finaler Walk-Forward: die zusammengestellte dynamische Strategie auf dem
    unangetasteten Holdout testen (kein Lookahead – identisch zum Live-Verhalten)."""
    doc = await _get_doc(aid)
    _guard_no_running()
    body["analysis_id"] = aid
    execution = (body.get("execution") or "cloud").lower()
    params = {k: body.get(k) for k in
              ("analysis_id", "scope", "symbol", "strategy_id")}
    params["execution"] = execution
    if execution == "local":
        _check_local_available()
        job_id = lab.create_job("walkforward", params)
        _enqueue_local("walkforward", job_id, {**body, "analysis_doc": _slim_doc(doc)})
        return {"status": "started", "job_id": job_id, "execution": "local"}
    job_id = lab.create_job("walkforward", params)
    task = asyncio.create_task(regime_opt.run_walkforward(
        job_id, body, strategy_registry, scanner.settings, DEFAULT_COIN_CFG, state.db))
    _watch_job_task(task, lab.JOBS, job_id)
    return {"status": "started", "job_id": job_id}
