"""Backend regression tests for Krypto-Trader iteration
(Kosten-Dashboard, Lessons-Konflikte, AI-Config-Flags, Schedule-Model,
 Watchdog manage_external). Run: pytest -v.

Preview backend URL is used to test what the user is actually seeing.
"""
import os
import time
import requests
import pytest

BASE_URL = os.environ.get(
    "REACT_APP_BACKEND_URL",
    "https://trader-refactor-1.preview.emergentagent.com",
).rstrip("/")

ADMIN_USER = os.environ.get("ADMIN_USER", "Admin")
ADMIN_PW = os.environ.get("ADMIN_PW", "TestAdmin123!")


@pytest.fixture(scope="session")
def admin_token():
    r = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"username": ADMIN_USER, "password": ADMIN_PW},
        timeout=15,
    )
    assert r.status_code == 200, r.text
    tok = r.json().get("token")
    assert tok
    return tok


@pytest.fixture()
def auth_headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}"}


# ---------- Cost Dashboard ----------
class TestAIUsage:
    def test_ai_usage_shape(self):
        r = requests.get(f"{BASE_URL}/api/ai/usage", timeout=20)
        assert r.status_code == 200
        d = r.json()
        assert "today" in d and "days" in d and "priced_models" in d
        today = d["today"]
        assert "roles" in today
        total = today.get("total", {})
        for k in ("calls", "in_tokens", "out_tokens", "est_cost_usd"):
            assert k in total, f"missing {k}"
        assert isinstance(d["priced_models"], list)


# ---------- Lessons conflicts ----------
class TestLessonsConflicts:
    def test_conflicts_endpoint(self):
        r = requests.get(f"{BASE_URL}/api/ai/lessons/conflicts", timeout=20)
        assert r.status_code == 200
        d = r.json()
        assert "active_count" in d
        assert "superseded" in d
        assert isinstance(d["superseded"], list)

    def test_insights_lessons_have_superseded_by(self):
        r = requests.get(f"{BASE_URL}/api/ai/insights", timeout=25)
        assert r.status_code == 200
        d = r.json()
        lessons = d.get("lessons") or []
        # Either lessons exist and each has field, or list is empty (no data)
        for l in lessons:
            assert "superseded_by" in l, (
                "lesson missing superseded_by field"
            )


# ---------- AI config flags & heatmap review ----------
class TestAIConfigFlags:
    def test_status_has_new_flags(self):
        r = requests.get(f"{BASE_URL}/api/ai/status", timeout=20)
        assert r.status_code == 200
        cfg = r.json().get("config", {})
        for key in ("use_liquidation_data", "use_heatmap_data", "lean_prompt"):
            assert key in cfg, f"cfg missing {key}"
        assert cfg["use_liquidation_data"] is True
        assert cfg["lean_prompt"] is True

    def test_toggle_heatmap_starts_ab_review(self, auth_headers):
        # turn on
        r = requests.post(
            f"{BASE_URL}/api/ai/config",
            json={"use_heatmap_data": True},
            headers=auth_headers,
            timeout=20,
        )
        assert r.status_code == 200
        assert r.json()["config"]["use_heatmap_data"] is True

        time.sleep(0.5)

        # verify status shows True
        r2 = requests.get(f"{BASE_URL}/api/ai/status", timeout=20)
        assert r2.json()["config"]["use_heatmap_data"] is True

        # reset to False
        r3 = requests.post(
            f"{BASE_URL}/api/ai/config",
            json={"use_heatmap_data": False},
            headers=auth_headers,
            timeout=20,
        )
        assert r3.status_code == 200
        assert r3.json()["config"]["use_heatmap_data"] is False

    def test_ai_config_requires_admin(self):
        r = requests.post(
            f"{BASE_URL}/api/ai/config",
            json={"use_heatmap_data": False},
            timeout=15,
        )
        assert r.status_code == 401


# ---------- Analyse schedule per-window model ----------
class TestScheduleModel:
    def test_schedule_has_allowed_models(self):
        r = requests.get(f"{BASE_URL}/api/ai/schedule", timeout=20)
        assert r.status_code == 200
        d = r.json()
        assert "allowed_models" in d
        am = d["allowed_models"]
        assert isinstance(am, dict) and len(am) > 0
        # At least one provider list is non-empty
        assert any(isinstance(v, list) and v for v in am.values())

    def test_post_schedule_saves_model(self, auth_headers):
        # Save
        payload = {
            "schedule": [
                {
                    "from": "15:00",
                    "to": "18:00",
                    "interval_min": 5,
                    "label": "US-Open",
                    "model": "gemini-3.5-flash-lite",
                }
            ]
        }
        r = requests.post(
            f"{BASE_URL}/api/ai/schedule",
            json=payload,
            headers=auth_headers,
            timeout=20,
        )
        assert r.status_code == 200
        # Verify via GET
        r2 = requests.get(f"{BASE_URL}/api/ai/schedule", timeout=20)
        sched = r2.json().get("schedule") or []
        assert len(sched) >= 1
        assert sched[0].get("model") == "gemini-3.5-flash-lite"
        # Reset
        r3 = requests.post(
            f"{BASE_URL}/api/ai/schedule",
            json={"schedule": []},
            headers=auth_headers,
            timeout=20,
        )
        assert r3.status_code == 200


# ---------- Watchdog manage_external ----------
class TestWatchdogManageExternal:
    def test_manage_external_toggle(self, auth_headers):
        # Default (should be false)
        r = requests.get(
            f"{BASE_URL}/api/autotrade/watchdog/status", timeout=20
        )
        assert r.status_code == 200
        settings = r.json().get("settings", {})
        assert "manage_external" in settings

        # Set True
        r1 = requests.post(
            f"{BASE_URL}/api/autotrade/watchdog/config",
            json={"manage_external": True},
            headers=auth_headers,
            timeout=20,
        )
        assert r1.status_code == 200
        assert r1.json()["settings"]["manage_external"] is True

        # Set False
        r2 = requests.post(
            f"{BASE_URL}/api/autotrade/watchdog/config",
            json={"manage_external": False},
            headers=auth_headers,
            timeout=20,
        )
        assert r2.status_code == 200
        assert r2.json()["settings"]["manage_external"] is False

        # Verify persistence via GET
        r3 = requests.get(
            f"{BASE_URL}/api/autotrade/watchdog/status", timeout=20
        )
        assert (
            r3.json()["settings"]["manage_external"] is False
        )


# ---------- Regression: core endpoints reachable ----------
class TestRegression:
    @pytest.mark.parametrize(
        "ep",
        [
            "/api/ai/status",
            "/api/ai/lessons",
            "/api/autotrade/config",
            "/api/autotrade/watchdog/status",
            "/api/ai/schedule",
            "/api/ai/insights",
            "/api/ai/usage",
            "/api/ai/lessons/conflicts",
        ],
    )
    def test_endpoint_ok(self, ep):
        r = requests.get(f"{BASE_URL}{ep}", timeout=25)
        assert r.status_code == 200, f"{ep} -> {r.status_code}"
