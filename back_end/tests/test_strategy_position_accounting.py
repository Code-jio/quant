from datetime import datetime

import pytest

from src.strategy import Direction, Position, StrategyBase, Trade


class PositionProbeStrategy(StrategyBase):
    def on_init(self):
        self.symbol = self.params.get("symbol", "rb2505")

    def on_bar(self, bar):
        return None


def trade(direction: Direction, price: float, volume: int) -> Trade:
    return Trade(
        trade_id=f"T-{direction.value}-{price}-{volume}",
        order_id="O",
        symbol="rb2505",
        direction=direction,
        price=price,
        volume=volume,
        trade_time=datetime(2026, 4, 30, 9, 30),
    )


def test_partial_close_keeps_remaining_long_cost_unchanged():
    strategy = PositionProbeStrategy("probe", {"symbol": "rb2505"})

    strategy.update_position("rb2505", trade(Direction.LONG, 100, 10))
    strategy.update_position("rb2505", trade(Direction.SHORT, 120, 5))

    pos = strategy.get_position("rb2505")
    assert pos.direction == Direction.LONG
    assert pos.volume == 5
    assert pos.cost == pytest.approx(100)


def test_reversal_uses_new_trade_price_as_cost_for_new_side():
    strategy = PositionProbeStrategy("probe", {"symbol": "rb2505"})

    strategy.update_position("rb2505", trade(Direction.LONG, 100, 10))
    strategy.update_position("rb2505", trade(Direction.SHORT, 90, 15))

    pos = strategy.get_position("rb2505")
    assert pos.direction == Direction.SHORT
    assert pos.volume == -5
    assert pos.cost == pytest.approx(90)


def test_strategy_position_source_overrides_internal_state_when_available():
    strategy = PositionProbeStrategy("probe", {"symbol": "rb2505"})
    strategy.update_position("rb2505", trade(Direction.LONG, 100, 1))
    strategy.set_position_source({
        "rb2505.short": Position(symbol="rb2505", direction=Direction.SHORT, volume=3, cost=88)
    })

    pos = strategy.get_position("rb2505")

    assert pos.direction == Direction.SHORT
    assert pos.volume == -3
    assert pos.cost == pytest.approx(88)
