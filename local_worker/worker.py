#!/usr/bin/env python3
"""Lokaler Worker für die Krypto-Trader-Website.

Verbindet sich per Outbound-Polling mit dem Server (keine Portfreigaben nötig)
und rechnet Backtests, Optimierungen, Regime-Lab-Jobs und Daten-Downloads
lokal. Verbindungsabbrüche (z.B. WinError 121 / Timeouts) werden automatisch
mit Backoff überbrückt – laufende Berechnungen laufen dabei einfach weiter
und Ergebnisse werden nach Wiederverbindung hochgeladen.

Start:  python worker.py --server https://<deine-website> --token <TOKEN>
        (Server/Token werden in worker_config.json gemerkt)
"""
import argparse
import asyncio
import gzip
import json
import logging
import os
import sys
import threading
import time
import uuid
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

VERSION = "1.8.0"
POLL_INTERVAL = 2.0
PROGRESS_INTERVAL = 2.0
RESULT_RETRIES = 90          # 90 x 5s = 7,5 Minuten Upload-Geduld
RESULT_RETRY_WAIT = 5.0
MAX_BACKOFF = 30.0
CONFIG_PATH = os.path.join(BASE_DIR, "worker_config.json")

logging.basicConfig(level=logging.INFO, datefmt="%H:%M:%S",
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("worker")

RUNNING = {}                 # job_id -> {"kind", "job"}
RUNNING_LOCK = threading.Lock()
_svc = {}
_last_settings = {}
_data_cache = (0.0, {})


# ---------------- Konfiguration ----------------
def load_config():
    try:
        with open(CONFIG_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def save_config(cfg):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, indent=2)
    except OSError as e:
        log.warning(f"Konfiguration konnte nicht gespeichert werden: {e}")


def setup():
    ap = argparse.ArgumentParser(description="Lokaler Krypto-Trader-Worker")
    ap.add_argument("--server", help="Basis-URL der Website (https://...)")
    ap.add_argument("--token", help="Worker-Token (Website → Lokal → Verwalten)")
    ap.add_argument("--name", help="Anzeigename dieses Rechners")
    ap.add_argument("--data-dir", help="Ordner für Kerzendaten")
    args = ap.parse_args()
    cfg = load_config()
    for k, v in (("server", args.server), ("token", args.token),
                 ("name", args.name), ("data_dir", args.data_dir)):
        if v:
            cfg[k] = v
    if not cfg.get("server"):
        cfg["server"] = input("Server-URL der Website (https://...): ").strip()
    if not cfg.get("token"):
        cfg["token"] = input("Worker-Token: ").strip()
    cfg.setdefault("name", os.environ.get("COMPUTERNAME")
                   or os.environ.get("HOSTNAME") or "Lokaler PC")
    cfg.setdefault("worker_id", uuid.uuid4().hex[:12])
    save_config(cfg)
    return cfg


def services():
    """Backend-Module lazy laden (nach dem Setzen der Umgebungsvariablen)."""
    if not _svc:
        from services import backtester as bt
        from services import optimizer as opt
        from services import regime_lab as rlab
        from services import regime_opt as ropt
        from services import candle_cache as cc
        from services import gpu_accel as gpu
        from strategies.registry import registry
        _svc.update(bt=bt, opt=opt, rlab=rlab, ropt=ropt, cc=cc, gpu=gpu,
                    registry=registry)
    return _svc


# ---------------- Server-Client ----------------
class Client:
    def __init__(self, server: str, token: str):
        import requests
        self.base = server.rstrip("/")
        self.headers = {"X-Worker-Token": token}
        self.sess = requests.Session()

    def post(self, path, body=None, data=None, headers=None, timeout=20):
        h = dict(self.headers)
        h.update(headers or {})
        r = self.sess.post(self.base + path, json=body, data=data,
                           headers=h, timeout=timeout)
        if r.status_code == 401:
            raise SystemExit("Worker-Token ungültig – bitte auf der Website "
                             "unter Lokal → Verwalten prüfen.")
        r.raise_for_status()
        return r.json()


# ---------------- Einstellungen vom Server ----------------
def apply_settings(st):
    global _last_settings
    if not st or st == _last_settings:
        return
    _last_settings = dict(st)
    cores = int(st.get("cpu_cores") or 0)
    os.environ["SIM_WORKERS"] = str(cores if cores > 0 else 0)
    os.environ["USE_GPU"] = "1" if st.get("use_gpu") else "0"
    cc = services()["cc"]
    ram = int(st.get("ram_limit_mb") or 4096)
    cc.MAX_CANDLES_IN_MEMORY = max(int(ram * 1_000_000 / 48), 100_000)
    if st.get("data_dir"):
        cc.CACHE_DIR = str(st["data_dir"])
    log.info(f"Einstellungen übernommen: Kerne={cores or 'alle'} · "
             f"RAM={ram}MB · GPU={'an' if st.get('use_gpu') else 'aus'}")


# ---------------- Jobs ----------------
def _mk_job(params):
    return {"status": "running", "progress": 0, "phase": "Startet",
            "params": params, "cancel": False, "result": None, "error": None,
            "best": None,
            "created_at": datetime.now(timezone.utc).isoformat()}


async def _run_compute(kind, job_id, payload, job):
    s = services()
    args = payload.get("args") or {}
    for d in payload.get("custom_definitions") or []:
        try:
            s["registry"].upsert_custom(d)
        except Exception as e:  # noqa: BLE001
            log.warning(f"Custom-Strategie: {e}")
    if kind == "backtest":
        s["bt"].JOBS[job_id] = job
        await s["bt"].run_backtest(
            job_id, args.get("strategy_ids") or [], args.get("symbols") or [],
            int(args.get("days") or 30), args.get("cfg") or {}, s["registry"],
            args.get("settings") or {}, None, args.get("strategy_configs") or {},
            args.get("default_timeframe"), args.get("date_from"),
            args.get("date_to"))
    elif kind == "optimizer":
        s["opt"].JOBS[job_id] = job
        await s["opt"].run_optimizer(job_id, args.get("body") or {},
                                     s["registry"], args.get("settings") or {},
                                     args.get("default_cfg") or {}, None)
    elif kind == "regime_lab":
        s["rlab"].JOBS[job_id] = job
        fn = args.get("fn")
        body = args.get("body") or {}
        if fn == "analysis":
            await s["rlab"].run_analysis(job_id, body, None)
        elif fn == "calibrate":
            await s["rlab"].run_calibration(job_id, body, None)
        elif fn == "regime_opt":
            await s["ropt"].run_regime_optimizer(
                job_id, body, s["registry"], args.get("settings") or {},
                args.get("default_cfg") or {}, None)
        elif fn == "walkforward":
            await s["ropt"].run_walkforward(
                job_id, body, s["registry"], args.get("settings") or {},
                args.get("default_cfg") or {}, None)
        else:
            raise RuntimeError(f"Unbekannte Regime-Lab-Funktion: {fn}")
    else:
        raise RuntimeError(f"Unbekannter Job-Typ: {kind}")


async def _run_data(kind, params, job):
    import aiohttp
    cc = services()["cc"]
    if kind == "data_delete":
        cc.remove_symbol(params.get("symbol"))
        job["summary"] = {"deleted": params.get("symbol")}
        job["status"] = "done"
        job["progress"] = 100
        return
    if kind == "data_download":
        symbols = params.get("symbols") or []
        days = int(params.get("days") or 30)
    else:  # data_update
        symbols = [r["symbol"] for r in cc.list_disk_symbols()]
        days = None
    total = 0
    async with aiohttp.ClientSession() as session:
        for i, sym in enumerate(symbols):
            if job.get("cancel"):
                job["status"] = "cancelled"
                job["phase"] = "Abgebrochen"
                return
            job["phase"] = f"Lade {sym} ({i + 1}/{len(symbols)})"
            job["progress"] = round(i / max(len(symbols), 1) * 100)
            d = days
            if d is None:
                meta = cc.disk_meta(sym)
                d = (max(int((time.time() * 1000 - meta["first_ts"])
                             / 86400000) + 1, 2) if meta else 30)
            candles = await cc.get_candles(session, sym, d, job=job)
            total += len(candles)
            await cc.persist_symbol_async(sym)
    job["summary"] = {"symbols": len(symbols), "candles": total}
    job["status"] = "done"
    job["progress"] = 100


def _result_payload(kind, job):
    out = {"kind": kind,
           "status": job.get("status") if job.get("status") in
           ("done", "error", "cancelled") else "error",
           "error": job.get("error")}
    if kind == "backtest":
        out["result"] = job.get("result")
        out["export_trades"] = job.get("export_trades") or []
    elif kind == "optimizer":
        out["result"] = job.get("result")
        out["best"] = job.get("best")
        out["export_trades"] = job.get("export_trades") or []
    elif kind == "regime_lab":
        out["result"] = job.get("result")
    else:
        out["summary"] = job.get("summary")
    return out


def _upload_result(client, job_id, payload):
    raw = gzip.compress(json.dumps(payload, default=str).encode())
    for attempt in range(1, RESULT_RETRIES + 1):
        try:
            client.post(f"/api/worker/job/{job_id}/result", data=raw,
                        headers={"Content-Encoding": "gzip",
                                 "Content-Type": "application/json"},
                        timeout=120)
            return True
        except SystemExit:
            raise
        except Exception as e:  # noqa: BLE001
            log.warning(f"Ergebnis-Upload fehlgeschlagen "
                        f"(Versuch {attempt}/{RESULT_RETRIES}): {e}")
            time.sleep(RESULT_RETRY_WAIT)
    log.error(f"Ergebnis für Job {job_id} konnte nicht hochgeladen werden")
    return False


def _progress_loop(client, job_id, job, stop):
    while not stop.is_set():
        try:
            resp = client.post(f"/api/worker/job/{job_id}/progress",
                               body={"progress": job.get("progress"),
                                     "phase": job.get("phase"),
                                     "best": job.get("best")}, timeout=10)
            if resp.get("cancel"):
                job["cancel"] = True
        except SystemExit:
            os._exit(1)
        except Exception:  # noqa: BLE001 – Poll-Loop meldet Verbindungsprobleme
            pass
        stop.wait(PROGRESS_INTERVAL)


def _job_thread(client, item):
    job_id, kind, payload = item["job_id"], item["kind"], item["payload"]
    job = _mk_job(payload)
    with RUNNING_LOCK:
        RUNNING[job_id] = {"kind": kind, "job": job}
    stop = threading.Event()
    threading.Thread(target=_progress_loop, args=(client, job_id, job, stop),
                     daemon=True).start()
    t0 = time.time()
    try:
        if kind in ("backtest", "optimizer", "regime_lab"):
            asyncio.run(_run_compute(kind, job_id, payload, job))
        else:
            asyncio.run(_run_data(kind, payload, job))
        if job.get("status") == "running":
            job["status"] = "done"
            job["progress"] = 100
    except Exception as e:  # noqa: BLE001
        log.exception(f"Job {job_id} fehlgeschlagen")
        job["status"] = "error"
        job["error"] = str(e)[:300]
    finally:
        stop.set()
    _upload_result(client, job_id, _result_payload(kind, job))
    with RUNNING_LOCK:
        RUNNING.pop(job_id, None)
    log.info(f"Job {job_id} ({kind}) beendet: {job.get('status')} · "
             f"{time.time() - t0:.0f}s")


# ---------------- Heartbeat / Hauptschleife ----------------
def _heartbeat(cfg, want_compute, want_data):
    global _data_cache
    res = {"cores": os.cpu_count() or 1}
    try:
        import psutil
        vm = psutil.virtual_memory()
        res["ram_mb"] = int(vm.total / 1e6)
        res["ram_free_mb"] = int(vm.available / 1e6)
    except Exception:  # noqa: BLE001
        pass
    try:
        gpu = services()["gpu"].info()
    except Exception:  # noqa: BLE001
        gpu = {"available": False}
    if time.time() - _data_cache[0] > 30:
        try:
            rows = services()["cc"].list_disk_symbols()
            _data_cache = (time.time(),
                           {"symbols": [r["symbol"] for r in rows],
                            "bytes": sum(r.get("bytes") or 0 for r in rows)})
        except Exception:  # noqa: BLE001
            _data_cache = (time.time(), {})
    with RUNNING_LOCK:
        running = list(RUNNING.keys())
    return {"worker_id": cfg["worker_id"], "name": cfg.get("name"),
            "version": VERSION, "resources": res, "gpu": gpu,
            "data": _data_cache[1], "running_jobs": running,
            "sim_workers": os.environ.get("SIM_WORKERS"),
            "want_compute": want_compute, "want_data": want_data}


def main():
    cfg = setup()
    os.environ.setdefault("CANDLE_CACHE_DIR",
                          cfg.get("data_dir") or os.path.join(BASE_DIR, "candle_data"))
    os.environ.setdefault("SIM_WORKERS", "0")   # 0 = alle Kerne
    client = Client(cfg["server"], cfg["token"])
    log.info(f"Worker {VERSION} gestartet · Server {cfg['server']} · "
             f"Name {cfg.get('name')} · Daten {os.environ['CANDLE_CACHE_DIR']}")
    backoff = POLL_INTERVAL
    offline_since = None
    while True:
        try:
            st = _last_settings or {}
            max_jobs = int(st.get("max_parallel_jobs") or 1)
            with RUNNING_LOCK:
                n_compute = sum(1 for v in RUNNING.values()
                                if not v["kind"].startswith("data_"))
                n_data = sum(1 for v in RUNNING.values()
                             if v["kind"].startswith("data_"))
            resp = client.post("/api/worker/poll",
                               body=_heartbeat(cfg, n_compute < max_jobs,
                                               n_data == 0), timeout=15)
            if offline_since:
                log.info(f"Verbindung zum Server wiederhergestellt "
                         f"(nach {time.time() - offline_since:.0f}s)")
                offline_since = None
            backoff = POLL_INTERVAL
            apply_settings(resp.get("settings"))
            for jid in resp.get("cancel_ids") or []:
                with RUNNING_LOCK:
                    r = RUNNING.get(jid)
                if r:
                    r["job"]["cancel"] = True
            item = resp.get("job")
            if item:
                log.info(f"Job übernommen: {item['job_id']} ({item['kind']})")
                threading.Thread(target=_job_thread, args=(client, item),
                                 daemon=True).start()
            time.sleep(POLL_INTERVAL)
        except KeyboardInterrupt:
            log.info("Worker beendet.")
            return
        except SystemExit:
            raise
        except Exception as e:  # noqa: BLE001 – NIE abstürzen, immer neu verbinden
            if offline_since is None:
                offline_since = time.time()
            log.warning(f"Verbindung zum Server fehlgeschlagen ({e}) – neuer "
                        f"Versuch in {backoff:.0f}s (laufende Jobs rechnen weiter)")
            time.sleep(backoff)
            backoff = min(backoff * 1.7, MAX_BACKOFF)


if __name__ == "__main__":
    main()
