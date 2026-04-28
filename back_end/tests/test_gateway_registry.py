from src.trading import SimulatedGateway, create_gateway
from src.trading.vnpy_gateway import VnpyGateway


def test_unknown_gateway_falls_back_to_simulated():
    gateway = create_gateway("unknown")

    assert isinstance(gateway, SimulatedGateway)


def test_ctp_alias_uses_vnpy_gateway():
    gateway = create_gateway("ctp")

    assert isinstance(gateway, VnpyGateway)


def test_vnpy_gateway_registered_directly():
    gateway = create_gateway("vnpy")

    assert isinstance(gateway, VnpyGateway)
