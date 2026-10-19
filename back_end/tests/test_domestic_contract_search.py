import pytest

from src.watch import search_contracts


def contract(symbol, exchange, **extra):
    return dict(symbol=symbol, name=symbol, exchange=exchange, product="FUTURES", source="ctp", **extra)


@pytest.mark.parametrize("symbol,exchange", [
    ("au2612", "SHFE"), ("ad2612", "SHFE"), ("bz2612", "DCE"),
    ("PL612", "CZCE"), ("SF612", "CZCE"), ("IF2612", "CFFEX"),
    ("IM2612", "CFFEX"), ("TL2612", "CFFEX"), ("sc2612", "INE"),
    ("ps2612", "GFEX"), ("pt2612", "GFEX"),
])
def test_domestic_futures_remain_searchable(symbol, exchange):
    rows = search_contracts(symbol, contracts={symbol: contract(symbol, exchange)})
    assert [row["symbol"] for row in rows] == [symbol]


@pytest.mark.parametrize("spec", [
    contract("AF2610", "CFFEX"), contract("EF2612", "CFFEX"),
    contract("GC2612", "COMEX"), contract("HSI2612", "HKFE"),
    dict(contract("AP612C6000", "CZCE"), product="OPTION"),
    dict(contract("SP au2612&au2702", "SHFE"), product="SPREAD"),
    dict(contract("au2612", "SHFE"), product=""),
    dict(contract("au2612", "SHFE"), source="historical"),
    contract("au2612", "SHFE", tradable=False),
    contract("au2612", "SHFE", is_trading=False),
    contract("au8888", "SHFE"), contract("au0000", "SHFE"),
    contract("au2612.COMEX", "SHFE"),
])
def test_non_domestic_or_non_futures_are_not_search_choices(spec):
    assert search_contracts("", contracts={spec["symbol"]: spec}) == []


def test_filter_runs_before_limit_and_keeps_search_metadata():
    specs = {f"AF26{i:02}": contract(f"AF26{i:02}", "CFFEX") for i in range(1, 13)}
    specs["au2612"] = contract("au2612", "SHFE")
    rows = search_contracts("", limit=1, contracts=specs)
    assert [row["symbol"] for row in rows] == ["au2612"]
    assert search_contracts("hj", contracts=specs)[0]["symbol"] == "au2612"
    assert search_contracts("", exchange="CFFEX", contracts=specs) == []


def test_chinese_product_search_works_when_counter_only_names_the_symbol():
    specs = {"au2612": contract("au2612", "SHFE")}
    rows = search_contracts("黄金", contracts=specs)
    assert [row["symbol"] for row in rows] == ["au2612"]
    assert rows[0]["name"] == "黄金"


def test_missing_gateway_does_not_fall_back_to_historical_symbols(monkeypatch):
    def unexpected_database():
        raise AssertionError("Trading search must not read the historical database")
    monkeypatch.setattr("src.data.DataManager", unexpected_database)
    assert search_contracts("") == []


def test_public_api_filters_before_limit_and_excludes_account_margin(monkeypatch):
    from fastapi.testclient import TestClient
    from src.api import create_app, trading_state
    from test_manual_trading import install_gateway, login

    gateway = install_gateway(monkeypatch)
    gateway.contract_specs = {
        "AF2610": contract("AF2610", "CFFEX"),
        "au2612": contract("au2612", "SHFE", margin_rate=.15),
    }
    try:
        with TestClient(create_app()) as client:
            login(client)
            result = client.get("/watch/search?limit=1").json()
            assert result["code"] == 0
            assert [row["symbol"] for row in result["data"]] == ["au2612"]
            assert "margin_rate" not in result["data"][0]
    finally:
        trading_state.clear_main()


def test_catalog_pages_have_stable_totals_and_product_filters(monkeypatch):
    from fastapi.testclient import TestClient
    from src.api import create_app, trading_state
    from test_manual_trading import install_gateway, login
    gateway = install_gateway(monkeypatch)
    gateway.contract_specs = {f"au26{i:02}": contract(f"au26{i:02}", "SHFE") for i in range(1, 13)}
    gateway.contract_specs["IF2612"] = contract("IF2612", "CFFEX")
    try:
        with TestClient(create_app()) as client:
            login(client)
            first = client.get("/watch/search?limit=5&product=AU").json()
            second = client.get("/watch/search?limit=5&offset=5&product=AU").json()
            assert first["total"] == second["total"] == 12
            assert len(first["data"]) == len(second["data"]) == 5
            assert not ({r["symbol"] for r in first["data"]} & {r["symbol"] for r in second["data"]})
            assert first["products"]["AU"] == "黄金"
            assert client.get("/watch/search?offset=10000").json()["data"] == []
    finally:
        trading_state.clear_main()
