from fastapi.testclient import TestClient

from src.api import create_app
from src.api.security import SESSION_COOKIE_NAME


def test_protected_endpoint_requires_session():
    app = create_app()

    with TestClient(app) as client:
        response = client.get("/system/status")

    assert response.status_code == 401


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
