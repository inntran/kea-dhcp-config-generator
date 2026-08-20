"""Tests for HooksLibraryModel."""

import pytest
from pydantic import ValidationError

from kea_dhcp_config_generator.models.input import HooksLibraryModel


def test_minimal_hooks_library():
    """Hook library needs only library path."""
    data = {"library": "libdhcp_ha.so"}
    model = HooksLibraryModel(**data)
    assert model.library == "libdhcp_ha.so"
    assert model.parameters is None


def test_hooks_library_with_parameters():
    """Hook library can have arbitrary parameters object."""
    data = {
        "library": "libdhcp_ha.so",
        "parameters": {
            "high-availability": [
                {
                    "this-server-name": "dhcp1",
                    "mode": "hot-standby",
                    "peers": []
                }
            ]
        }
    }
    model = HooksLibraryModel(**data)
    assert model.library == "libdhcp_ha.so"
    assert model.parameters is not None
    assert "high-availability" in model.parameters


def test_library_required():
    """Library field is required."""
    data = {"parameters": {}}
    with pytest.raises(ValidationError) as exc:
        HooksLibraryModel(**data)
    assert "library" in str(exc.value).lower()


def test_mysql_hook_no_parameters():
    """MySQL hook minimal."""
    data = {"library": "libdhcp_mysql.so"}
    model = HooksLibraryModel(**data)
    assert model.library == "libdhcp_mysql.so"
