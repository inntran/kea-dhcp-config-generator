"""Unit tests for validation/errors.py."""

import pytest

from kea_dhcp_config_generator.validation.errors import KeaConfigError


def test_kea_config_error_is_exception():
    assert issubclass(KeaConfigError, Exception)


def test_kea_config_error_message():
    exc = KeaConfigError("something went wrong")
    assert "something went wrong" in str(exc)


def test_kea_config_error_can_be_raised():
    with pytest.raises(KeaConfigError, match="test error"):
        raise KeaConfigError("test error")


def test_kea_config_error_path_arg():
    exc = KeaConfigError("file missing", path="/etc/kea/config.yaml")
    assert exc.path == "/etc/kea/config.yaml"


def test_kea_config_error_path_defaults_to_none():
    exc = KeaConfigError("something went wrong")
    assert exc.path is None
