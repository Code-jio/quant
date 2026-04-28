import pytest

from src.trading import create_gateway
from src.trading.errors import GatewayError
from src.trading.vnpy_gateway import VnpyGateway


def test_unknown_gateway_is_rejected():
    with pytest.raises(GatewayError):
        create_gateway("unknown")


def test_default_gateway_uses_vnpy_gateway():
    gateway = create_gateway()

    assert isinstance(gateway, VnpyGateway)


def test_ctp_alias_uses_vnpy_gateway():
    gateway = create_gateway("ctp")

    assert isinstance(gateway, VnpyGateway)


def test_vnpy_gateway_registered_directly():
    gateway = create_gateway("vnpy")

    assert isinstance(gateway, VnpyGateway)
