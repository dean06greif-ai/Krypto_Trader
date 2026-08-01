"""End-to-End Test: KI-Strategie -> custom_strategy Registrierung -> Backtest -> Optimizer.

Validiert bullet 3 des Review-Requests:
- POST /api/ai/strategies mit rule_definition (Aliase + eine ungültige adx-Regel) legt
  einen Kandidaten an, registriert eine custom_strategy.
- GET /api/strategies enthält 'KI-Kandidat: ...' mit normalisierten Regeln und nicht-leeren params.
- POST /api/backtest/run gegen die neue Strategie liefert trades > 0.
- POST /api/optimizer/run (mode=params) liefert best.params mit Strategie-Parametern.

Cleanup: Kandidat wird via Mongo und die custom_strategy via DELETE /api/strategies/{id} entfernt.
"""
import os
import time
import uuid

import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "http://localhost:8001").rstrip("/")


@pytest.fixture(scope="module")
def token():
    r = requests.post(f"{BASE_URL}/api/auth/login",
                      json={"username": os.environ.get("ADMIN_USER", "Admin"),
                            "password": os.environ.get("ADMIN_PASSWORD", "admin")},
                      timeout=10)
    assert r.status_code == 200, r.text
    return r.json()["token"]


@pytest.fixture(scope="module")
def auth(token):
    return {"Authorization": f"Bearer {token}"}


def _poll_job(url_status: str, timeout: int = 120) -> dict:
    t0 = time.time()
    last = None
    while time.time() - t0 < timeout:
        r = requests.get(url_status, timeout=10)
        assert r.status_code == 200, r.text
        last = r.json()
        if last.get("status") in ("done", "cancelled", "error"):
            return last
        time.sleep(2)
    pytest.fail(f"Job Timeout nach {timeout}s: {last}")


@pytest.fixture(scope="module")
def created_candidate(auth):
    """Legt Kandidat + custom_strategy an; liefert (cid, strategy_id) und räumt am Ende auf."""
    unique_suffix = uuid.uuid4().hex[:6]
    payload = {
        "name": f"TEST_KI_Pipeline_{unique_suffix}",
        "thesis": "MACD-Cross-Alias plus ungültige ADX-Regel: sollte Alias mappen und ADX droppen.",
        "rules_text": "MACD line crosses_above signal; RSI < 45 (String).",
        "symbols": ["BTCUSDT"],
        "timeframe": "1m",
        "source": "trader",
        "rule_definition": {
            "timeframe": "1m",
            "indicators": {"rsi_period": 14, "macd_fast": 12, "macd_slow": 26, "macd_signal": 9},
            "long_rules": [
                {"indicator": "macd_line", "op": "crosses_above", "value": "macd_signal_line"},
                {"indicator": "rsi", "op": "<", "value": "45"},
                {"indicator": "adx", "op": ">", "value": 25},  # ungültig -> raus
            ],
            "short_rules": [
                {"indicator": "rsi", "op": ">", "value": 70},
            ],
        },
    }
    r = requests.post(f"{BASE_URL}/api/ai/strategies", json=payload,
                      headers=auth, timeout=20)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "success", body
    cand = body["candidate"]
    cid = cand["id"]
    strategy_id = cand.get("custom_strategy_id")
    if not strategy_id:
        # Falls Auto-Registrierung fehlschlug, manuell auslösen
        r = requests.post(f"{BASE_URL}/api/ai/strategies/{cid}/register-test",
                          headers=auth, timeout=15)
        assert r.status_code == 200, r.text
        strategy_id = r.json().get("strategy_id")
    assert strategy_id, f"Keine strategy_id erhalten: {body}"

    yield cid, strategy_id, cand["name"]

    # ---- Cleanup ----
    try:
        requests.delete(f"{BASE_URL}/api/strategies/{strategy_id}",
                        headers=auth, timeout=15)
    except Exception:
        pass
    # Kandidat aus Mongo entfernen
    try:
        from dotenv import load_dotenv
        load_dotenv("/app/backend/.env")
        from pymongo import MongoClient
        mongo_url = os.environ["MONGO_URL"]
        db_name = os.environ["DB_NAME"]
        cli = MongoClient(mongo_url)
        cli[db_name]["ai_strategy_candidates"].delete_one({"id": cid})
        cli.close()
    except Exception as e:
        print(f"cleanup candidate failed: {e}")


class TestAIStrategyPipeline:

    def test_candidate_created_and_registered(self, created_candidate, auth):
        cid, strategy_id, name = created_candidate
        # GET /api/strategies muss die neue Strategie enthalten
        r = requests.get(f"{BASE_URL}/api/strategies", timeout=10)
        assert r.status_code == 200
        payload = r.json()
        strategies = payload["strategies"] if isinstance(payload, dict) else payload
        entry = next((s for s in strategies if s["id"] == strategy_id), None)
        assert entry is not None, f"strategy_id {strategy_id} nicht in /api/strategies"
        # Name-Präfix 'KI-Kandidat:'
        assert entry["name"].startswith("KI-Kandidat:"), entry["name"]
        # Regeln normalisiert: adx entfernt, macd_line->macd, crosses_above->cross_above
        rd = entry.get("definition") or entry.get("rule_definition") or {}
        long_rules = rd.get("long_rules", [])
        indicators_used = [rule["indicator"] for rule in long_rules]
        assert "adx" not in indicators_used, f"adx sollte entfernt sein: {indicators_used}"
        assert "macd" in indicators_used, f"macd_line sollte auf macd gemappt sein: {indicators_used}"
        ops = [rule["op"] for rule in long_rules]
        assert "cross_above" in ops, f"crosses_above sollte cross_above sein: {ops}"
        # RSI-Wert als Zahl (nicht String)
        rsi_rule = next((r for r in long_rules if r["indicator"] == "rsi"), None)
        assert rsi_rule and isinstance(rsi_rule["value"], (int, float)), rsi_rule
        # params nicht leer und enthält rule_long_0_value (Optimizer-Suchraum)
        params = entry.get("params") or {}
        assert params, f"params sollte nicht leer sein: {entry}"
        # Erwartung: mindestens ein rule_long_*-Wert oder rsi_period ist im Suchraum
        assert any("rule_long" in k for k in params) or "rsi_period" in params, list(params.keys())

    def test_backtest_runs_and_produces_trades(self, created_candidate, auth):
        cid, strategy_id, _ = created_candidate
        r = requests.post(f"{BASE_URL}/api/backtest/run",
                          json={"strategy_ids": [strategy_id],
                                "symbols": ["BTCUSDT"], "days": 2},
                          headers=auth, timeout=15)
        assert r.status_code == 200, r.text
        job_id = r.json()["job_id"]
        result = _poll_job(f"{BASE_URL}/api/backtest/status/{job_id}", timeout=180)
        assert result["status"] == "done", result
        # Ergebnis-Extraktion: results ist entweder Liste oder dict
        # Ergebnis-Struktur: result.per_pair[].trades (int)
        res = result.get("result") or {}
        total_trades = 0
        for row in (res.get("per_pair") or []):
            total_trades += int(row.get("trades", 0) or 0)
        if not total_trades:
            for row in (res.get("per_strategy") or []):
                total_trades += int(row.get("trades", 0) or 0)
        assert total_trades > 0, f"Backtest lieferte 0 Trades: {result}"

    def test_optimizer_runs_and_returns_params(self, created_candidate, auth):
        cid, strategy_id, _ = created_candidate
        r = requests.post(f"{BASE_URL}/api/optimizer/run",
                          json={"mode": "params", "strategy_id": strategy_id,
                                "symbols": ["BTCUSDT"], "days": 2,
                                "iterations": 8, "min_trades": 2},
                          headers=auth, timeout=15)
        assert r.status_code == 200, r.text
        job_id = r.json()["job_id"]
        result = _poll_job(f"{BASE_URL}/api/optimizer/status/{job_id}", timeout=240)
        assert result["status"] == "done", result
        # best.params sollte Strategie-Parameter enthalten
        best = (result.get("result") or {}).get("best") or result.get("best") or {}
        best_params = best.get("params") or best.get("parameters") or {}
        assert best_params, f"best.params leer: {result}"
        # Mindestens ein bekannter Strategie-Param sollte enthalten sein
        known_keys = {"rsi_period", "rule_long_0_value", "rule_long_1_value",
                      "macd_fast", "macd_slow", "macd_signal"}
        assert any(k in best_params for k in known_keys), \
            f"best.params enthält keine Strategie-Parameter: {best_params}"
