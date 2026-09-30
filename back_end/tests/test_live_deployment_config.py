"""Static safety contract for the Docker live-trading deployment path.

These checks intentionally do not invoke Docker or a CTP endpoint.
"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _compose() -> str:
    return (ROOT / "docker-compose.yml").read_text(encoding="utf-8")


def _dockerignore() -> str:
    return (ROOT / "back_end" / ".dockerignore").read_text(encoding="utf-8")


def test_backend_docker_context_excludes_local_credentials_and_runtime_artifacts():
    dockerignore = _dockerignore()

    for pattern in (
        "config/*",
        "!config/config.example.json",
        "!config/config.production.example.json",
        ".env",
        ".env.*",
        "data/**/*.db",
        "data/**/*.db-wal",
        "data/**/*.db-shm",
        ".vntrader/",
        "**/ctp_flow_td/",
        "**/ctp_flow_md/",
        "*.con",
        "logs/",
        ".venv*/",
        ".pytest_cache/",
        ".coverage",
        "tests/",
    ):
        assert pattern in dockerignore

    # The production template is intentionally shipped; actual live config is
    # supplied read-only at runtime by docker-compose.
    assert "config.production.example.json" in dockerignore


def test_backend_is_internal_and_frontend_binds_only_to_loopback():
    compose = _compose()
    backend = compose.split("  frontend:", maxsplit=1)[0]

    assert "    expose:\n      - \"8000\"" in backend
    assert "    ports:" not in backend
    assert '      - "127.0.0.1:${FRONTEND_PORT:-8080}:80"' in compose


def test_compose_fixes_production_safety_switches_and_requires_cors_origin():
    compose = _compose()

    for line in (
        "      QUANT_ENV: production",
        '      QUANT_ALLOW_SYNTHETIC_DATA: "false"',
        '      QUANT_SESSION_COOKIE_SECURE: "true"',
        '      QUANT_RATE_LIMIT_ENABLED: "true"',
        "      QUANT_CORS_ORIGINS: ${QUANT_CORS_ORIGINS:?required}",
    ):
        assert line in compose

    assert "QUANT_RISK_MAX_ORDER_VOLUME" not in compose
    assert "QUANT_RISK_MAX_POSITION_VOLUME" not in compose
    assert ":-false}" not in compose
    assert ":-100}" not in compose
    assert ":-1000}" not in compose


def test_compose_keeps_live_configuration_read_only_and_persists_runtime_state():
    compose = _compose()

    assert (
        "${QUANT_LIVE_CONFIG_HOST_PATH:?required}:"
        "/run/secrets/quant-live-config.json:ro"
    ) in compose
    assert "      QUANT_LIVE_CONFIG_PATH: /run/secrets/quant-live-config.json" in compose
    for line in (
        "      QUANT_AUDIT_LOG_DIR: /app/runtime/audit",
        "      QUANT_SESSION_DB: /app/runtime/session/sessions.db",
        "      QUANT_LIVE_RISK_STATE_PATH: /app/runtime/risk/live_risk_state.json",
        "      QUANT_VNPY_RUNTIME_DIR: /app/.vntrader",
        "      - quant_audit:/app/runtime/audit",
        "      - quant_sessions:/app/runtime/session",
        "      - quant_live_risk:/app/runtime/risk",
        "      - quant_vnpy_flow:/app/.vntrader",
    ):
        assert line in compose


def test_internal_nginx_proxy_does_not_claim_tls_termination():
    nginx = (ROOT / "front_end" / "nginx.conf").read_text(encoding="utf-8").lower()

    assert "internal http upstream only" in nginx
    assert "listen 443" not in nginx
    assert "ssl_certificate" not in nginx
    assert "proxy_pass http://backend:8000" in nginx
