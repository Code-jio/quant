from fastapi.testclient import TestClient

from src.api import create_app
from src.api.security import SESSION_COOKIE_NAME


def test_protected_endpoint_requires_session():
    app = create_app()

    with TestClient(app) as client:
        response = client.get("/system/status")

    assert response.status_code == 401


def test_health_and_metrics_are_public_and_structured():
    app = create_app()

    with TestClient(app) as client:
        health = client.get("/health")
        metrics = client.get("/metrics")

    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert "X-Request-ID" in health.headers
    assert metrics.status_code == 200
    assert "quant_uptime_seconds" in metrics.text


def test_simulated_login_sets_cookie_and_allows_protected_endpoint():
    app = create_app()

    with TestClient(app) as client:
        login_response = client.post(
            "/auth/login",
            json={
                "username": "simulate",
                "password": "simulate",
                "broker_id": "SIM",
                "gateway_type": "simulated",
            },
        )
        assert login_response.status_code == 200
        assert login_response.json()["success"] is True
        assert SESSION_COOKIE_NAME in client.cookies

        status_response = client.get("/system/status")
        assert status_response.status_code == 200
        assert status_response.json()["gateway_status"] in {"connected", "trading", "stopped"}

        logout_response = client.post("/auth/logout")
        assert logout_response.status_code == 200
        assert logout_response.json()["success"] is True


def test_audit_events_are_available_after_login():
    app = create_app()

    with TestClient(app) as client:
        client.post(
            "/auth/login",
            json={
                "username": "simulate",
                "password": "simulate",
                "broker_id": "SIM",
                "gateway_type": "simulated",
            },
        )

        response = client.get("/audit/events?event_type=auth")

    assert response.status_code == 200
    events = response.json()["events"]
    assert any(event["action"] == "login" and event["status"] == "success" for event in events)
