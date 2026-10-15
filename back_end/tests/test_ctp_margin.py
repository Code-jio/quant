from types import SimpleNamespace as NS

import pytest

from src.trading.ctp_margin import MarginRateBook


def make_book():
    specs = {"au2612": {"symbol": "au2612", "exchange": "SHFE", "product": "FUTURES", "size": 1000}}
    requests: list[tuple] = []
    td = NS(userid="fixture-user", brokerid="fixture-broker", reqid=10)
    def capture(kind, req, rid):
        requests.append((kind, req, rid))
        return 0
    td.reqQryInstrumentMarginRate = lambda req, rid: capture("instrument", req, rid)
    td.reqQryExchangeMarginRate = lambda req, rid: capture("exchange", req, rid)
    return MarginRateBook(specs), specs, td, requests


def reply(reqid=11, relative=0, **overrides):
    data = dict(
        InstrumentID="au2612", BrokerID="fixture-broker", InvestorID="fixture-user",
        HedgeFlag="1", ExchangeID="SHFE", IsRelative=relative,
        LongMarginRatioByMoney=0.12, ShortMarginRatioByMoney=0.15,
        LongMarginRatioByVolume=0, ShortMarginRatioByVolume=0,
    )
    data.update(overrides)
    return {"query": "instrument", "data": data, "error_id": 0, "reqid": reqid, "last": True}


def test_account_query_uses_exact_contract_and_waits_for_final_packet():
    book, specs, td, requests = make_book()
    assert book.request("au2612", "20261008")["status"] == "pending"
    assert book.dispatch(td, "20261008")
    assert requests == [("instrument", {
        "BrokerID": "fixture-broker", "InvestorID": "fixture-user", "InstrumentID": "au2612",
        "ExchangeID": "SHFE", "HedgeFlag": "1",
    }, 11)]
    book.on_response({**reply(), "last": False}, "20261008")
    assert not specs["au2612"].get("margin_rate")
    book.on_response({**reply(), "data": {}}, "20261008")
    state = book.request("au2612", "20261008")
    assert state["status"] == "ready"
    assert state["long_margin_rate"] == 0.12 and state["short_margin_rate"] == 0.15
    assert specs["au2612"]["margin_rate"] == 0.15  # conservative reservation
    assert not book.dispatch(td, "20261008")
    assert "InvestorID" not in str(state) and "fixture-user" not in str(state)


def test_relative_rates_wait_for_exchange_base_and_include_fixed_amounts():
    book, specs, td, requests = make_book()
    book.request("au2612", "20261008")
    book.dispatch(td, "20261008")
    book.on_response(reply(relative=1, LongMarginRatioByMoney=.02, ShortMarginRatioByMoney=.03), "20261008")
    assert not specs["au2612"].get("margin_rate")
    book.dispatch(td, "20261008")
    assert requests[-1][0] == "exchange" and "InvestorID" not in requests[-1][1]
    exchange = reply(reqid=12, LongMarginRatioByMoney=.1, ShortMarginRatioByMoney=.11,
                     LongMarginRatioByVolume=10, ShortMarginRatioByVolume=20)
    exchange["query"] = "exchange"
    book.on_response(exchange, "20261008")
    assert specs["au2612"]["margin_rate"] == pytest.approx(.14)
    assert specs["au2612"]["margin_per_lot"] == 20


@pytest.mark.parametrize("override", [
    {"InstrumentID": "au2702"}, {"InvestorID": "other"}, {"BrokerID": "other"},
    {"HedgeFlag": "3"}, {"ExchangeID": "DCE"}, {"IsRelative": None},
    {"LongMarginRatioByMoney": float("nan")}, {"ShortMarginRatioByMoney": -1},
    {"LongMarginRatioByVolume": float("inf")}, {"LongMarginRatioByMoney": None},
])
def test_invalid_or_wrong_account_response_never_unlocks_opening(override):
    book, specs, td, _ = make_book()
    book.request("au2612", "20261008")
    book.dispatch(td, "20261008")
    book.on_response(reply(**override), "20261008")
    assert book.request("au2612", "20261008")["status"] == "error"
    assert specs["au2612"].get("margin_rate") is None


def test_disconnect_and_day_rollover_invalidate_rates_and_late_callbacks():
    book, specs, td, _ = make_book()
    book.request("au2612", "20261008")
    book.dispatch(td, "20261008")
    book.on_response(reply(), "20261008")
    assert specs["au2612"]["margin_rate"] == .15
    book.invalidate()
    book.on_response(reply(), "20261008")
    assert specs["au2612"].get("margin_rate") is None
    book.request("au2612", "20261008")
    book.dispatch(td, "20261008")
    book.on_response(reply(reqid=12), "20261009")
    assert specs["au2612"].get("margin_rate") is None
    assert book.request("au2612", "20261009")["status"] == "pending"


def test_timeouts_failed_sends_empty_and_error_replies_fail_closed(monkeypatch):
    book, specs, td, _ = make_book()
    now = [100.0]
    monkeypatch.setattr("src.trading.ctp_margin.time.monotonic", lambda: now[0])
    book.request("au2612", "20261008")
    book.dispatch(td, "20261008")
    now[0] += 11
    book.dispatch(td, "20261008")
    assert book.request("au2612", "20261008")["status"] == "error"
    assert specs["au2612"].get("margin_rate") is None
    for mode in ("send", "empty", "error"):
        book.invalidate()
        td.reqQryInstrumentMarginRate = lambda *_: -2 if mode == "send" else 0
        book.request("au2612", "20261008")
        book.dispatch(td, "20261008")
        if mode != "send":
            book.on_response({**reply(reqid=td.reqid), "data": {}, "error_id": 1 if mode == "error" else 0}, "20261008")
        assert book.request("au2612", "20261008")["status"] == "error"


def test_expired_rate_is_not_used_while_refreshing(monkeypatch):
    book, specs, td, _ = make_book()
    now = [100.0]
    monkeypatch.setattr("src.trading.ctp_margin.time.monotonic", lambda: now[0])
    book.request("au2612", "20261008")
    book.dispatch(td, "20261008")
    book.on_response(reply(), "20261008")
    now[0] += 1801
    assert book.request("au2612", "20261008")["status"] == "pending"
    assert specs["au2612"].get("margin_rate") is None


def test_timer_expires_rates_even_without_new_requests(monkeypatch):
    book, specs, td, _ = make_book()
    now = [100.0]
    monkeypatch.setattr("src.trading.ctp_margin.time.monotonic", lambda: now[0])
    book.request("au2612", "20261008")
    book.dispatch(td, "20261008")
    book.on_response(reply(), "20261008")
    now[0] += 1801
    book.expire("20261008")
    assert specs["au2612"].get("margin_rate") is None
    assert book.dispatch(td, "20261008")
    book.on_response(reply(reqid=12), "20261008")
    assert specs["au2612"]["margin_rate"] == .15


def test_margin_api_requires_session_and_public_search_hides_account_terms(monkeypatch):
    from fastapi.testclient import TestClient
    from src.api import create_app, trading_state
    from test_manual_trading import install_gateway, login

    gateway = install_gateway(monkeypatch)
    book, specs, td, _ = make_book()
    gateway.contract_specs = specs
    gateway.request_margin_rate = lambda symbol: book.request(symbol, "20261008")
    try:
        with TestClient(create_app()) as client:
            assert client.get("/trading/margin-rate?symbol=au2612").status_code == 401
            login(client)
            assert client.get("/trading/margin-rate?symbol=au2612").json()["status"] == "pending"
            book.dispatch(td, "20261008")
            book.on_response(reply(), "20261008")
            assert client.get("/trading/margin-rate?symbol=au2612").json()["long_margin_rate"] == .12
            assert client.get("/trading/margin-rate?symbol=missing").status_code == 400
            # Search remains public even when a cookie is present; never return account terms there.
            result = client.get("/watch/search?query=au2612").json()["data"][0]
            assert not any("margin" in key for key in result)
            client.cookies.clear()
            result = client.get("/watch/search?query=au2612").json()["data"][0]
            assert not any("margin" in key for key in result)
            assert specs["au2612"]["margin_rate"] == .15
    finally:
        trading_state.clear_main()


def test_gateway_timer_serializes_margin_and_account_queries(monkeypatch):
    from src.trading.vnpy_gateway import VnpyGateway
    now = [100.0]
    monkeypatch.setattr("src.trading.vnpy_gateway.time.monotonic", lambda: now[0])
    book, specs, td, requests = make_book()
    gateway = VnpyGateway()
    gateway.contract_specs = specs
    gateway._margin_book = book
    gateway.connection_state.update(td=True, contracts=True)
    gateway.trading_day = "20261008"
    native = NS(td_api=td, md_api=NS(update_date=lambda: None),
                query_account=lambda: requests.append(("account", {}, 0)),
                query_position=lambda: requests.append(("position", {}, 0)))
    gateway._main_engine = NS(get_gateway=lambda _: native)
    book.request("au2612", "20261008")
    gateway._on_timer(None)
    now[0] += 2
    gateway._on_timer(None)
    now[0] += 2
    gateway._on_timer(None)
    assert [item[0] for item in requests] == ["account", "position", "instrument"]
    gateway._refresh_queue.append("account")
    now[0] += 2
    gateway._on_timer(None)
    assert len(requests) == 3  # outstanding margin response holds the queue


def test_many_margin_queries_do_not_starve_account_and_position_refresh(monkeypatch):
    from src.trading.vnpy_gateway import VnpyGateway
    now = [100.0]
    monkeypatch.setattr("src.trading.vnpy_gateway.time.monotonic", lambda: now[0])
    gateway = VnpyGateway()
    gateway.connection_state.update(td=True, contracts=True)
    gateway.trading_day = "20261008"
    book = gateway._margin_book
    for index in range(60):
        symbol = f"fixture{index}"
        gateway.contract_specs[symbol] = {"symbol": symbol, "exchange": "SHFE", "product": "FUTURES", "size": 10}
        book.request(symbol, gateway.trading_day)
    td = NS(userid="fixture-user", brokerid="fixture-broker", reqid=10,
            reqQryInstrumentMarginRate=lambda *_: 0)
    accounts: list[float] = []
    positions: list[float] = []
    native = NS(td_api=td, md_api=NS(update_date=lambda: None),
                query_account=lambda: accounts.append(now[0]),
                query_position=lambda: positions.append(now[0]))
    gateway._main_engine = NS(get_gateway=lambda _: native)
    margin_count = 0
    for step in range(50):
        now[0] = 100.0 + 2 * step
        gateway._on_timer(None)
        if book.pending:
            book.on_response(reply(reqid=td.reqid, InstrumentID=book.pending["symbol"]), gateway.trading_day)
            margin_count += 1
    assert margin_count >= 20
    assert len(accounts) >= 5 and len(positions) >= 5
    assert max(b - a for a, b in zip(accounts, accounts[1:])) <= 18
    assert max(b - a for a, b in zip(positions, positions[1:])) <= 18
