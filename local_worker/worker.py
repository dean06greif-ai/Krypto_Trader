"""Krypto_Trader – Lokaler Worker.

Rechnet Backtests, Optimierungen, Regime-Lab-Jobs und Kerzen-Downloads auf dem
eigenen Rechner und schickt die Ergebnisse an die Website. Es sind KEINE
Portfreigaben nötig: der Worker fragt den Server aktiv (Outbound-Polling).

Start (Windows):   python worker.py
Start (Linux/Mac): python3 worker.py

Beim ersten Start werden Server-URL und Worker-Token abgefragt und in
`worker_config.json` gespeichert (Token: Website → Ausführung → Lokal →
⚙ Verwalten → Token anzeigen).

Der Worker nutzt exakt dieselben Rechen-Module wie der Server (Ordner
`services/`, `strategies/`, `core/`, `models/` liegen im Paket) – die
Ergebnisse sind damit identisch zur Cloud-Ausführung.
"""
import argparse
import asyncio
import gzip
import json
import logging
import os
import platform
import sys
import time
import uuid
from pathlib import Path

VERSION = "1.7.0"

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "worker_config.json"
DEFAULT_DATA_DIR = BASE_DIR / "candle_data"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout),
              logging.FileHandler(BASE_DIR / "worker.log", encoding="utf-8")],
)
logger = logging.getLogger("worker")

POLL_INTERVAL = 2.0
PROGRESS_INTERVAL = 1.0   # schnelle Abbruch-Erkennung (Server meldet cancel im Response)

# Wird nach dem ersten Poll (Einstellungen vom Server) gesetzt – die
# Rechen-Module lesen ihre Grenzen aus Umgebungsvariablen beim Import,
# deshalb werden sie erst DANACH importiert.
MODULES = {}


# --------------------------------------------------------------- Konfiguration
def load_config() -> dict:
    cfg = {}
    if CONFIG_PATH.is_file():
        try:
            cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (ValueError, OSError) as e:
            logger.warning(f"worker_config.json unlesbar ({e}) – wird neu angelegt")
            cfg = {}
    return cfg


def save_config(cfg: dict):
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        val = input(f"{prompt}{suffix}: ").strip()
    except EOFError:
        val = ""
    return val or default


def ensure_config(args) -> dict:
    cfg = load_config()
    # Kompatibilität: ältere Configs nutzten den Schlüssel "server"
    if not cfg.get("base_url") and cfg.get("server"):
        cfg["base_url"] = cfg.pop("server")
    if args.server:
        cfg["base_url"] = args.server
    if args.token:
        cfg["token"] = args.token
    if args.name:
        cfg["name"] = args.name
    if not cfg.get("base_url"):
        cfg["base_url"] = ask("Server-URL (z.B. https://meine-app.example.com)")
    if not cfg.get("token"):
        cfg["token"] = ask("Worker-Token (Website → Ausführung → Lokal → Verwalten)")
    if not cfg.get("worker_id"):
        cfg["worker_id"] = uuid.uuid4().hex[:12]
    if not cfg.get("name"):
        cfg["name"] = ask("Anzeigename dieses Rechners", platform.node() or "Lokaler PC")
    cfg["base_url"] = cfg["base_url"].rstrip("/")
    if not cfg["base_url"] or not cfg.get("token"):
        logger.error("Server-URL und Token sind erforderlich. Abbruch.")
        sys.exit(1)
    save_config(cfg)
    return cfg


# --------------------------------------------------------------- Ressourcen
def resources() -> dict:
    info = {"platform": f"{platform.system()} {platform.release()}",
            "python": platform.python_version(),
            "cpu_count": os.cpu_count() or 1}
    try:
        import psutil
        vm = psutil.virtual_memory()
        info["ram_total_mb"] = round(vm.total / 1024 / 1024)
        info["ram_free_mb"] = round(vm.available / 1024 / 1024)
        info["cpu_percent"] = psutil.cpu_percent(interval=None)
    except Exception:  # noqa: BLE001 – psutil ist optional
        pass
    return info


def gpu_info() -> dict:
    try:
        from services import gpu_accel
        return gpu_accel.info()
    except Exception:  # noqa: BLE001
        return {"available": False}


def data_info() -> dict:
    cc = MODULES.get("candle_cache")
    if not cc:
        return {}
    try:
        symbols = cc.list_disk_symbols()
        return {"symbols": symbols, "count": len(symbols),
                "candles": sum(int(s.get("candles") or 0) for s in symbols),
                "dir": os.environ.get("CANDLE_CACHE_DIR", "")}
    except Exception as e:  # noqa: BLE001
        logger.debug(f"data_info: {e}")
        return {}


# --------------------------------------------------------------- Umgebung
def apply_settings(settings: dict):
    """Server-Einstellungen in Umgebungsvariablen übersetzen (vor dem Import
    der Rechen-Module)."""
    settings = settings or {}
    cores = int(settings.get("cpu_cores") or 0)
    os.environ["SIM_WORKERS"] = str(cores)          # 0 = alle Kerne
    ram_mb = int(settings.get("ram_limit_mb") or 4096)
    # ~ 90 Byte pro Kerze im RAM-Cache (Messwert) -> Obergrenze ableiten
    os.environ["CANDLE_CACHE_MAX_CANDLES"] = str(max(int(ram_mb * 1024 * 1024 / 90), 100_000))
    data_dir = str(settings.get("data_dir") or "").strip() or str(DEFAULT_DATA_DIR)
    Path(data_dir).mkdir(parents=True, exist_ok=True)
    os.environ["CANDLE_CACHE_DIR"] = data_dir
    os.environ["CANDLE_CACHE_DISK"] = "1"
    os.environ["USE_GPU"] = "1" if settings.get("use_gpu") else "0"


def import_modules():
    """Rechen-Module laden (erst nach apply_settings!)."""
    if MODULES:
        return MODULES
    sys.path.insert(0, str(BASE_DIR))
    from services import backtester, candle_cache, optimizer, regime_lab, regime_opt
    from strategies.registry import registry
    MODULES.update({"bt": backtester, "opt": optimizer, "lab": regime_lab,
                    "regime_opt": regime_opt, "candle_cache": candle_cache,
                    "registry": registry})
    return MODULES


# --------------------------------------------------------------- HTTP
class Api:
    def __init__(self, base_url: str, token: str):
        self.base = base_url
        self.headers = {"X-Worker-Token": token, "Content-Type": "application/json"}
        self._session = None

    async def session(self):
        import aiohttp
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=180))
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def post(self, path: str, payload: dict, gzip_body: bool = False,
                   timeout: float = None):
        import aiohttp
        s = await self.session()
        url = f"{self.base}{path}"
        headers = dict(self.headers)
        raw = json.dumps(payload).encode("utf-8")
        if gzip_body:
            raw = gzip.compress(raw)
            headers["Content-Encoding"] = "gzip"
        kw = {}
        if timeout:
            kw["timeout"] = aiohttp.ClientTimeout(total=timeout)
        async with s.post(url, data=raw, headers=headers, **kw) as r:
            text = await r.text()
            if r.status >= 400:
                raise RuntimeError(f"{r.status} {text[:200]}")
            try:
                return json.loads(text)
            except ValueError:
                return {}


# --------------------------------------------------------------- Job-Ausführung
def _job_shell(job_id: str, params: dict = None) -> dict:
    return {"id": job_id, "status": "running", "progress": 0, "phase": "Startet",
            "params": params or {}, "cancel": False, "result": None, "error": None,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "execution": "local"}


async def _run_backtest(job_id: str, args: dict) -> dict:
    m = import_modules()
    bt, registry = m["bt"], m["registry"]
    bt.JOBS[job_id] = _job_shell(job_id)
    job = bt.JOBS[job_id]
    await bt.run_backtest(job_id, args["strategy_ids"], args["symbols"],
                          int(args["days"]), args["cfg"], registry,
                          args.get("settings") or {}, None,
                          args.get("strategy_configs") or {},
                          args.get("default_timeframe"),
                          args.get("date_from"), args.get("date_to"))
    return {"status": job.get("status") or "done", "error": job.get("error"),
            "result": job.get("result"),
            "export_trades": job.get("export_trades") or []}


async def _run_optimizer(job_id: str, args: dict) -> dict:
    m = import_modules()
    opt, registry = m["opt"], m["registry"]
    opt.JOBS[job_id] = _job_shell(job_id, args.get("body"))
    job = opt.JOBS[job_id]
    await opt.run_optimizer(job_id, args["body"], registry,
                            args.get("settings") or {},
                            args.get("default_cfg") or {}, None)
    return {"status": job.get("status") or "done", "error": job.get("error"),
            "result": job.get("result"), "best": job.get("best"),
            "export_trades": job.get("export_trades") or []}


async def _run_regime_lab(job_id: str, args: dict) -> dict:
    m = import_modules()
    lab, registry = m["lab"], m["registry"]
    fn = args.get("fn") or "analysis"
    body = args.get("body") or {}
    lab.JOBS[job_id] = _job_shell(job_id, body)
    job = lab.JOBS[job_id]
    if fn == "analysis":
        await lab.run_analysis(job_id, body, None)
    elif fn == "regime_opt":
        await m["regime_opt"].run_regime_optimizer(
            job_id, body, registry, args.get("settings") or {},
            args.get("default_cfg") or {}, None)
    elif fn == "walkforward":
        await m["regime_opt"].run_walkforward(
            job_id, body, registry, args.get("settings") or {},
            args.get("default_cfg") or {}, None)
    elif fn == "calibrate":
        await lab.run_calibration(job_id, body, None)
    else:
        raise RuntimeError(f"Unbekannter Regime-Lab-Job: {fn}")
    return {"status": job.get("status") or "done", "error": job.get("error"),
            "result": job.get("result")}


async def _run_data_job(job_id: str, kind: str, params: dict, jobs: dict) -> dict:
    import aiohttp
    m = import_modules()
    cc = m["candle_cache"]
    job = jobs[job_id]
    if kind == "data_delete":
        cc.remove_symbol(params.get("symbol"))
        return {"status": "done", "summary": {"deleted": params.get("symbol")}}
    if kind == "data_update":
        symbols = [s["symbol"] for s in cc.list_disk_symbols()]
        days_map = {s["symbol"]: int(s.get("days") or 30) for s in cc.list_disk_symbols()}
    else:
        symbols = list(params.get("symbols") or [])
        days_map = {s: int(params.get("days") or 30) for s in symbols}
    if not symbols:
        return {"status": "done", "summary": {"symbols": 0, "note": "keine Daten vorhanden"}}
    done, total_candles = [], 0
    async with aiohttp.ClientSession() as session:
        for i, sym in enumerate(symbols):
            if job.get("cancel"):
                return {"status": "cancelled"}
            job["phase"] = f"Lade {sym} ({i + 1}/{len(symbols)})"
            job["progress"] = round(i / len(symbols) * 100)
            candles = await cc.get_candles(session, sym, days_map.get(sym, 30), job=job)
            cc.persist_symbol(sym)
            total_candles += len(candles)
            done.append(sym)
    return {"status": "done",
            "summary": {"symbols": len(done), "candles": total_candles,
                        "list": done}}


HANDLERS = {"backtest": _run_backtest, "optimizer": _run_optimizer,
            "regime_lab": _run_regime_lab}


def _run_coro_in_thread(coro):
    """Job-Coroutine in einem eigenen Thread mit eigenem Event-Loop ausführen.
    Wichtig: die Rechen-Jobs enthalten CPU-lastige (synchrone) Abschnitte –
    liefen sie im Haupt-Loop, würden Polling/Heartbeat blockieren und der
    Server würde den Worker fälschlich als getrennt einstufen (genau der
    'Worker entkoppelt sich bei vielen Kerzen'-Fehler)."""
    return asyncio.run(coro)


def _jobs_dict_for(kind: str):
    m = import_modules()
    return {"backtest": m["bt"].JOBS, "optimizer": m["opt"].JOBS,
            "regime_lab": m["lab"].JOBS}.get(kind)


# --------------------------------------------------------------- Worker-Loop
class Worker:
    def __init__(self, cfg: dict, api: Api):
        self.cfg = cfg
        self.api = api
        self.running = {}            # job_id -> asyncio.Task
        self.local_jobs = {}         # job_id -> job dict (für Daten-Jobs)
        self.settings = {}
        self.max_parallel = 1

    # ---- Fortschritt melden ----
    async def _report(self, job_id: str, job: dict):
        payload = {"progress": job.get("progress"), "phase": job.get("phase")}
        if job.get("best") is not None:
            payload["best"] = job["best"]
        try:
            res = await self.api.post(f"/api/worker/job/{job_id}/progress", payload,
                                      timeout=15)
            if res.get("cancel"):
                job["cancel"] = True
        except Exception as e:  # noqa: BLE001 – Netz-Aussetzer nicht fatal
            logger.debug(f"progress {job_id}: {e}")

    async def _progress_loop(self, job_id: str, job: dict, task: asyncio.Task):
        while not task.done():
            await asyncio.sleep(PROGRESS_INTERVAL)
            await self._report(job_id, job)

    async def _execute(self, item: dict):
        job_id, kind = item["job_id"], item["kind"]
        payload = item.get("payload") or {}
        logger.info(f"Job {job_id} ({kind}) gestartet")
        t0 = time.perf_counter()
        try:
            custom = payload.get("custom_definitions")
            if custom:
                import_modules()["registry"].load_custom(custom)
            if kind in HANDLERS:
                args = payload.get("args") or {}
                jobs = _jobs_dict_for(kind)
                task = asyncio.create_task(asyncio.to_thread(
                    _run_coro_in_thread, HANDLERS[kind](job_id, args)))
                # Job-Shell wird im Handler erzeugt -> kurz warten
                for _ in range(50):
                    if job_id in jobs:
                        break
                    await asyncio.sleep(0.05)
                job = jobs.get(job_id) or _job_shell(job_id)
                watcher = asyncio.create_task(self._progress_loop(job_id, job, task))
                out = await task
                watcher.cancel()
            else:
                job = _job_shell(job_id)
                self.local_jobs[job_id] = job
                task = asyncio.create_task(asyncio.to_thread(
                    _run_coro_in_thread,
                    _run_data_job(job_id, kind, payload, self.local_jobs)))
                watcher = asyncio.create_task(self._progress_loop(job_id, job, task))
                out = await task
                watcher.cancel()
                self.local_jobs.pop(job_id, None)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 – Fehler an den Server melden
            logger.exception(f"Job {job_id} ({kind}) fehlgeschlagen")
            out = {"status": "error", "error": f"{type(e).__name__}: {e}"[:300]}
        out["kind"] = kind
        dur = time.perf_counter() - t0
        logger.info(f"Job {job_id} ({kind}) {out.get('status')} in {dur:.1f}s")
        # Lebenszeichen vor dem (ggf. großen) Upload, damit der Server den Job
        # nicht als hängend einstuft.
        if out.get("status") == "done":
            job["phase"] = "Ergebnis wird übertragen..."
            await self._report(job_id, job)
        big = kind in ("backtest", "optimizer", "regime_lab")
        for attempt in range(3):
            try:
                await self.api.post(f"/api/worker/job/{job_id}/result", out,
                                    gzip_body=big)
                break
            except Exception as e:  # noqa: BLE001
                logger.warning(f"Ergebnis-Upload fehlgeschlagen ({attempt + 1}/3): {e}")
                await asyncio.sleep(3)

    def _cleanup(self):
        for jid in [j for j, t in self.running.items() if t.done()]:
            self.running.pop(jid, None)

    def _apply_cancel(self, cancel_ids):
        for jid in cancel_ids or []:
            for jobs in (self.local_jobs,
                         *(d for d in (_jobs_dict_for(k) for k in HANDLERS) if d)):
                if jid in jobs:
                    jobs[jid]["cancel"] = True

    async def run(self):
        logger.info(f"Lokaler Worker v{VERSION} – Server {self.cfg['base_url']}")
        # 1) Einstellungen holen (ohne Job anzunehmen), Umgebung setzen, Module laden.
        #    Server nicht erreichbar (z.B. Render-Kaltstart) -> geduldig neu versuchen
        #    statt abzustürzen.
        first = None
        while first is None:
            try:
                first = await self._poll(want=False)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"Server noch nicht erreichbar ({e}) – neuer Versuch in 5s")
                await asyncio.sleep(5)
        apply_settings(first.get("settings") or {})
        logger.info(f"Rechenkerne (SIM_WORKERS)={os.environ['SIM_WORKERS']} · "
                    f"Daten={os.environ['CANDLE_CACHE_DIR']}")
        import_modules()
        logger.info("Rechen-Module geladen – warte auf Jobs")
        while True:
            try:
                self._cleanup()
                free = len(self.running) < self.max_parallel
                data = await self._poll(want=free)
                self.settings = data.get("settings") or self.settings
                self.max_parallel = max(int(self.settings.get("max_parallel_jobs") or 1), 1)
                self._apply_cancel(data.get("cancel_ids"))
                job = data.get("job")
                if job:
                    self.running[job["job_id"]] = asyncio.create_task(self._execute(job))
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 – Reconnect statt Absturz
                logger.warning(f"Poll fehlgeschlagen: {e}")
                await asyncio.sleep(5)
            await asyncio.sleep(POLL_INTERVAL)

    async def _poll(self, want: bool) -> dict:
        body = {"worker_id": self.cfg["worker_id"], "name": self.cfg["name"],
                "version": VERSION, "resources": resources(), "gpu": gpu_info(),
                "data": data_info(), "running_jobs": list(self.running.keys()),
                "sim_workers": os.environ.get("SIM_WORKERS"),
                "want_compute": want, "want_data": want}
        return await self.api.post("/api/worker/poll", body, timeout=15)


async def amain(args):
    cfg = ensure_config(args)
    api = Api(cfg["base_url"], cfg["token"])
    worker = Worker(cfg, api)
    try:
        await worker.run()
    finally:
        await api.close()


def main():
    p = argparse.ArgumentParser(description=f"Krypto_Trader Local Worker v{VERSION}")
    p.add_argument("--server", "--url", dest="server",
                   help="Server-URL (überschreibt worker_config.json)")
    p.add_argument("--token", help="Worker-Token")
    p.add_argument("--name", help="Anzeigename dieses Rechners")
    p.add_argument("--version", action="store_true", help="Version ausgeben")
    args = p.parse_args()
    if args.version:
        print(VERSION)
        return
    try:
        asyncio.run(amain(args))
    except KeyboardInterrupt:
        logger.info("Beendet (Strg+C)")


if __name__ == "__main__":
    main()
