import copy
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import pytest

from src.strategy import Direction, OffsetFlag, Order, OrderStatus, OrderType, Position
from src.trading.execution_adapter import (
    SimulationLedgerError,
    TrialRunSimulationAdapter,
    TrialRunSimulationLedger,
)
from src.trading.simulated_fill import apply_simulated_fill

from tests.helpers import RecordingGateway


def _source_order(*, order_id="R1", price=3130.0, volume=1, status=OrderStatus.CANCELLED):
    return Order(
        order_id=order_id,
        symbol="rb2510",
        direction=Direction.LONG,
        order_type=OrderType.LIMIT,
        price=price,
        volume=volume,
        status=status,
        offset=OffsetFlag.OPEN,
    )


def test_simulated_fill_keeps_gateway_order_and_position_books_unchanged():
    gateway = RecordingGateway()
    gateway.orders["EXTERNAL"] = _source_order(order_id="EXTERNAL")
    gateway.positions["rb2510_long"] = Position(
        symbol="rb2510", direction=Direction.LONG, volume=3, price=3120.0
    )
    ledger = TrialRunSimulationLedger()
    source = _source_order()
    simulated = ledger.create_entry_from_order(source)
    adapter = TrialRunSimulationAdapter(ledger)

    before_orders = copy.deepcopy(gateway.orders)
    before_positions = copy.deepcopy(gateway.positions)
    result = adapter.fill(simulated.order_id)

    assert gateway.orders == before_orders
    assert gateway.positions == before_positions
    assert result.trade.trade_id.startswith("SIM-")
    assert ledger.position_volume("rb2510") == 1
    assert result.trade.price == 3130.0
    assert result.order.status is OrderStatus.FILLED


def test_ledger_does_not_emit_gateway_callbacks_and_uses_stored_price():
    callbacks = []
    ledger = TrialRunSimulationLedger()
    source = _source_order(price=3142.5)
    order = ledger.create_entry_from_order(source)
    adapter = TrialRunSimulationAdapter(ledger, on_fill=callbacks.append)

    result = adapter.fill(order.order_id)

    assert result.trade.price == 3142.5
    assert callbacks == [result]
    assert ledger.orders[order.order_id].traded_volume == 1
    assert ledger.trades[result.trade.trade_id] == result.trade


@pytest.mark.parametrize("price", [float("nan"), float("inf"), 0.0])
def test_simulation_ledger_rejects_non_finite_or_non_positive_price(price):
    with pytest.raises(ValueError, match="fill price must be finite and positive"):
        TrialRunSimulationLedger().create_entry_from_order(_source_order(price=price))


def test_simulated_fill_rejects_non_current_or_unknown_order():
    ledger = TrialRunSimulationLedger()
    order = ledger.create_entry_from_order(_source_order())
    adapter = TrialRunSimulationAdapter(ledger)

    with pytest.raises(SimulationLedgerError) as exc_info:
        adapter.fill("OTHER")
    assert exc_info.value.failure_code == "order_not_owned_by_trial_run"

    ledger.current_order_id = "OTHER"
    with pytest.raises(SimulationLedgerError) as exc_info:
        adapter.fill(order.order_id)
    assert exc_info.value.failure_code == "order_not_current"


def test_concurrent_simulated_fill_records_exactly_one_trade():
    ledger = TrialRunSimulationLedger()
    order = ledger.create_entry_from_order(_source_order())
    adapter = TrialRunSimulationAdapter(ledger)
    barrier = threading.Barrier(8)

    def fill_once():
        barrier.wait()
        try:
            return adapter.fill(order.order_id).trade.trade_id
        except SimulationLedgerError as exc:
            return exc.failure_code

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(lambda _index: fill_once(), range(8)))

    assert sum(value.startswith("SIM-T-") for value in outcomes) == 1
    assert len(ledger.trades) == 1
    assert ledger.position_volume("rb2510") == 1


def test_legacy_helper_requires_isolated_adapter_and_preserves_finite_price_guard():
    class Engine:
        simulation_adapter = None

    with pytest.raises(SimulationLedgerError) as exc_info:
        apply_simulated_fill(Engine(), "SIM-E-1")
    assert exc_info.value.failure_code == "simulation_not_ready"

    ledger = TrialRunSimulationLedger()
    source = _source_order()
    synthetic = ledger.create_entry_from_order(source)
    engine = Engine()
    engine.simulation_adapter = TrialRunSimulationAdapter(ledger)

    with pytest.raises(ValueError, match="fill price must be finite and positive"):
        apply_simulated_fill(engine, synthetic.order_id, price=float("nan"))

    result = apply_simulated_fill(engine, synthetic.order_id)
    assert result.trade.price == source.price
