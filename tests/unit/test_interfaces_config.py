import pytest
from kea_dhcp_config_generator.models.input import InterfacesConfigModel
from pydantic import ValidationError


def test_interfaces_config_single_interface():
    """Single interface config."""
    data = {"interfaces": ["eth0"]}
    model = InterfacesConfigModel(**data)
    assert model.interfaces == ["eth0"]
    assert model.dhcp_socket_type is None
    assert model.outbound_interface is None


def test_interfaces_config_multiple_interfaces():
    """Multiple interfaces."""
    data = {"interfaces": ["eth0", "eth1"]}
    model = InterfacesConfigModel(**data)
    assert model.interfaces == ["eth0", "eth1"]


def test_interfaces_config_with_socket_type():
    """Interfaces with socket type."""
    data = {
        "interfaces": ["eth0"],
        "dhcp-socket-type": "udp"
    }
    model = InterfacesConfigModel(**data)
    assert model.dhcp_socket_type == "udp"


def test_interfaces_config_with_outbound():
    """Interfaces with outbound interface."""
    data = {
        "interfaces": ["eth0"],
        "outbound-interface": "eth1"
    }
    model = InterfacesConfigModel(**data)
    assert model.outbound_interface == "eth1"


def test_interfaces_required():
    """Interfaces list is required."""
    data = {}
    with pytest.raises(ValidationError) as exc:
        InterfacesConfigModel(**data)
    assert "interfaces" in str(exc.value).lower()
