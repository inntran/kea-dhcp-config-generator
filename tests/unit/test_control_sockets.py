"""Unit tests for ControlSocketModel."""

import pytest
from kea_dhcp_config_generator.models.input import ControlSocketModel
from pydantic import ValidationError


def test_unix_socket_minimal():
    """Unix socket needs only socket-name."""
    data = {"socket-type": "unix", "socket-name": "/var/run/kea/kea4-ctrl-socket"}
    model = ControlSocketModel(**data)
    assert model.socket_type == "unix"
    assert model.socket_name == "/var/run/kea/kea4-ctrl-socket"
    assert model.socket_address is None
    assert model.socket_port is None


def test_http_socket_with_address_and_port():
    """HTTP socket requires socket-address and socket-port."""
    data = {
        "socket-type": "http",
        "socket-address": "127.0.0.1",
        "socket-port": 8004
    }
    model = ControlSocketModel(**data)
    assert model.socket_type == "http"
    assert model.socket_address == "127.0.0.1"
    assert model.socket_port == 8004


def test_https_socket():
    """HTTPS socket similar to HTTP."""
    data = {
        "socket-type": "https",
        "socket-address": "0.0.0.0",
        "socket-port": 8443
    }
    model = ControlSocketModel(**data)
    assert model.socket_type == "https"


def test_invalid_socket_type():
    """Reject invalid socket type."""
    data = {"socket-type": "tcp", "socket-address": "127.0.0.1", "socket-port": 8004}
    with pytest.raises(ValidationError) as exc:
        ControlSocketModel(**data)
    assert "socket-type" in str(exc.value).lower()


def test_http_requires_address_or_port():
    """HTTP socket with only address is valid (port optional)."""
    data = {"socket-type": "http", "socket-address": "127.0.0.1"}
    model = ControlSocketModel(**data)
    assert model.socket_address == "127.0.0.1"
