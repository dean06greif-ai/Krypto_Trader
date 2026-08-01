"""Regressionstests für die 3 Verbesserungen (Iteration "Berlin/Clear/KI-Backtest"):

1. Zeitzone Europe/Berlin (Backend-Anteile)
2. Analyse-Löschen: neuer Scope "strategy" (eine Strategie über ALLE Coins) + Preview
3. Backtester/Optimizer für KI-erstellte Strategien:
   - normalize_rule_definition (Alias-Mapping, Validierung)
   - Parameter-Raum für Custom-Strategien (build_param_space / DEFAULT_PARAMS)
   - effective_definition (Optimizer-Overrides wirken in Fast- UND Referenzpfad)
"""
import os

import numpy as np
import pytest
import requests
from fastapi import HTTPException

from services.rule_definition import normalize_rule_definition, supported_summary
from strategies.custom_strategy import CustomStrategy, build_param_space

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "http://localhost:8001").rstrip("/")


# --------------------------------------------------------------------------
# Fix 3a: Regel-Normalisierung
# --------------------------------------------------------------------------
class TestNormalizeRuleDefinition:
    def test_valid_definition_unchanged(self):
        rd = {"timeframe": "5m",
              "indicators": {"rsi_period": 14, "ema_slow_period": 50},
              "long_rules": [{"indicator": "rsi", "op": "<", "value": 30}],
              "short_rules": [{"indicator": "rsi", "op": ">", "value": 70}]}
        norm, errors = normalize_rule_definition(rd)
        assert errors == []
        assert norm["timeframe"] == "5m"
        assert norm["long_rules"] == [{"indicator": "rsi", "op": "<", "value": 30}]
        assert norm["indicators"] == {"rsi_period": 14, "ema_slow_period": 50}

    def test_aliases_mapped(self):
        rd = {"long_rules": [
            {"indicator": "close", "op": "crosses_above", "value": "bollinger_upper"},
            {"indicator": "macd_line", "op": "above", "value": "macd_signal_line"},
        ], "short_rules": []}
        norm, errors = normalize_rule_definition(rd)
        assert errors == []
        assert norm["long_rules"][0] == {"indicator": "price", "op": "cross_above",
                                         "value": "bb_upper"}
        assert norm["long_rules"][1] == {"indicator": "macd", "op": ">",
                                         "value": "macd_signal"}

    def test_unsupported_indicator_removed_with_error(self):
        rd = {"long_rules": [
            {"indicator": "adx", "op": ">", "value": 25},
            {"indicator": "rsi", "op": "<", "value": 30},
        ]}
        norm, errors = normalize_rule_definition(rd)
        assert len(norm["long_rules"]) == 1
        assert norm["long_rules"][0]["indicator"] == "rsi"
        assert any("adx" in e for e in errors)

    def test_all_invalid_returns_none(self):
        rd = {"long_rules": [{"indicator": "supertrend", "op": "flips", "value": 1}]}
        norm, errors = normalize_rule_definition(rd)
        assert norm is None
        assert errors

    def test_numeric_string_value_and_bad_timeframe(self):
        rd = {"timeframe": "7h",
              "long_rules": [{"indicator": "rsi", "op": "<", "value": "30"}]}
        norm, errors = normalize_rule_definition(rd)
        assert norm["timeframe"] == "1m"
        assert norm["long_rules"][0]["value"] == 30.0
        assert any("7h" in e for e in errors)

    def test_unknown_period_keys_dropped(self):
        rd = {"indicators": {"rsi_period": 14, "adx_len": 14},
              "long_rules": [{"indicator": "rsi", "op": "<", "value": 30}]}
        norm, errors = normalize_rule_definition(rd)
        assert norm["indicators"] == {"rsi_period": 14}
        assert any("adx_len" in e for e in errors)

    def test_supported_summary_lists_engine(self):
        s = supported_summary()
        assert "rsi" in s and "cross_above" in s and "rsi_period" in s


# --------------------------------------------------------------------------
# Fix 3b: Parameter-Raum + effective_definition für Custom-Strategien
# --------------------------------------------------------------------------
DEF = {
    "id": "custom_test1", "name": "Test", "timeframe": "1m",
    "indicators": {"rsi_period": 14, "ema_slow_period": 50},
    "long_rules": [{"indicator": "rsi", "op": "<", "value": 30},
                   {"indicator": "macd_hist", "op": ">", "value": 0}],
    "short_rules": [{"indicator": "rsi", "op": ">", "value": 70}],
}


class TestCustomParamSpace:
    def test_space_contains_periods_and_thresholds(self):
        space = build_param_space(DEF)
        assert "rsi_period" in space
        assert "macd_fast" in space  # macd_hist genutzt
        assert "rule_long_0_value" in space   # RSI-Schwelle 30
        assert "rule_long_1_value" not in space  # 0-Schwelle nicht optimieren
        assert "rule_short_0_value" in space
        meta = space["rsi_period"]
        assert meta["min"] <= meta["value"] <= meta["max"] and meta["step"] > 0

    def test_default_params_instance_attribute(self):
        strat = CustomStrategy(dict(DEF))
        assert strat.DEFAULT_PARAMS  # nicht mehr leer -> Optimizer hat Suchraum
        from services.optimizer import strategy_param_space
        space = strategy_param_space(strat)
        assert "rsi_period" in space and len(space["rsi_period"]) > 1

    def test_effective_definition_identity_without_overrides(self):
        strat = CustomStrategy(dict(DEF))
        params = strat.get_params({})  # nur Defaults
        assert strat.effective_definition(params) is strat.definition
        assert strat.effective_definition(None) is strat.definition

    def test_effective_definition_applies_overrides(self):
        strat = CustomStrategy(dict(DEF))
        eff = strat.effective_definition({"rsi_period": 7, "rule_long_0_value": 25})
        assert eff["indicators"]["rsi_period"] == 7
        assert eff["long_rules"][0]["value"] == 25
        # Original bleibt unangetastet (Deep-Copy)
        assert strat.definition["indicators"]["rsi_period"] == 14
        assert strat.definition["long_rules"][0]["value"] == 30

    def test_fast_path_uses_overrides(self):
        """build_custom_provider (Fast-Path) muss Parameter-Overrides genauso
        anwenden wie der Referenzpfad (analyze)."""
        from services import fast_sim
        rng = np.random.default_rng(7)
        candles = []
        price = 100.0
        for i in range(400):
            price *= 1 + rng.normal(0, 0.004)
            candles.append({"timestamp": 1700000000000 + i * 60000,
                            "open": price, "high": price * 1.002,
                            "low": price * 0.998, "close": price, "volume": 10.0})
        d = {**DEF, "long_rules": [{"indicator": "rsi", "op": "<", "value": 45}],
             "short_rules": []}
        strat = CustomStrategy(d)
        fs = fast_sim.FastSeries(candles)
        base = fast_sim.build_custom_provider(strat, fs, {})
        loose = fast_sim.build_custom_provider(strat, fs, {
            "strategy_params": {"custom_test1": {"rule_long_0_value": 60}}})
        n_base = sum(1 for i in range(len(candles)) if base(i))
        n_loose = sum(1 for i in range(len(candles)) if loose(i))
        assert n_loose > n_base  # lockerere Schwelle -> mehr Signale
        # Referenzpfad liefert für die gleiche Kerze dasselbe Ergebnis
        params = strat.get_params({"strategy_params": {"custom_test1": {"rule_long_0_value": 60}}})
        res = strat.analyze(candles, "BTCUSDT", params)
        assert res is not None
        assert (res["signal_type"] == "LONG") == bool(loose(len(candles) - 1))


# --------------------------------------------------------------------------
# Fix 2: Clear-Scope "strategy" (Validierung, rein)
# --------------------------------------------------------------------------
class TestClearStrategyScope:
    def test_strategy_scope_filter(self):
        from routers.analytics import _validate_clear_request
        rng, scope, sig_f, trade_f, cutoff = _validate_clear_request(
            {"range": "all", "scope": "strategy", "strategy_id": "scalping_4_rules"})
        assert scope == "strategy" and cutoff is None
        assert sig_f == {"strategy_id": "scalping_4_rules"}
        assert trade_f == {"strategy_id": "scalping_4_rules"}
        assert "symbol" not in sig_f  # gilt für ALLE Coins

    def test_strategy_scope_requires_strategy_id(self):
        from routers.analytics import _validate_clear_request
        with pytest.raises(HTTPException):
            _validate_clear_request({"range": "all", "scope": "strategy"})

    def test_existing_scopes_unchanged(self):
        from routers.analytics import _validate_clear_request
        _, _, sig_f, _, _ = _validate_clear_request(
            {"range": "all", "scope": "coin_strategy", "symbol": "BTCUSDT",
             "strategy_id": "x"})
        assert sig_f == {"symbol": "BTCUSDT", "strategy_id": "x"}


# --------------------------------------------------------------------------
# API-Ebene (läuft gegen den lokalen Server)
# --------------------------------------------------------------------------
@pytest.fixture(scope="module")
def token():
    r = requests.post(f"{BASE_URL}/api/auth/login",
                      json={"username": os.environ.get("ADMIN_USER", "Admin"),
                            "password": os.environ.get("ADMIN_PASSWORD", "admin")},
                      timeout=10)
    assert r.status_code == 200, r.text
    return r.json()["token"]


class TestClearAPI:
    def test_preview_endpoint(self, token):
        r = requests.post(f"{BASE_URL}/api/analytics/clear/preview",
                          json={"range": "all", "scope": "strategy",
                                "strategy_id": "scalping_4_rules"},
                          headers={"Authorization": f"Bearer {token}"}, timeout=10)
        assert r.status_code == 200, r.text
        d = r.json()
        assert {"signals", "auto_trades", "total"} <= set(d)
        assert d["total"] == d["signals"] + d["auto_trades"]

    def test_preview_requires_admin(self):
        r = requests.post(f"{BASE_URL}/api/analytics/clear/preview",
                          json={"range": "all", "scope": "all"}, timeout=10)
        assert r.status_code in (401, 403)

    def test_clear_strategy_scope_roundtrip(self, token):
        r = requests.post(f"{BASE_URL}/api/analytics/clear",
                          json={"range": "hour", "scope": "strategy",
                                "strategy_id": "__no_such_strategy__"},
                          headers={"Authorization": f"Bearer {token}"}, timeout=30)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["status"] == "success" and d["scope"] == "strategy"
        assert d["deleted"]["signals"] == 0  # nichts von dieser Fake-Strategie

    def test_clear_invalid_scope_rejected(self, token):
        r = requests.post(f"{BASE_URL}/api/analytics/clear",
                          json={"range": "all", "scope": "bogus"},
                          headers={"Authorization": f"Bearer {token}"}, timeout=10)
        assert r.status_code == 400
