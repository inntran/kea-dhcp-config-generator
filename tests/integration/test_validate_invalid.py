from __future__ import annotations

import pytest

from kea_dhcp_config_generator import validate
from kea_dhcp_config_generator.validation.errors import (
    ConfigError,
    ConfigWarning,
    FingerprintError,
    SubnetConfigError,
)

from .conftest import INVALID_DIR, fixture_path

INVALID_FIXTURES = [
    ("overlapping_subnets", SubnetConfigError),
    ("pool_out_of_bounds", SubnetConfigError),
    ("pool_end_is_broadcast", SubnetConfigError),
    ("unknown_fingerprint", FingerprintError),
    ("missing_catch_all_class", ConfigWarning),
    ("duplicate_reservation_ip", SubnetConfigError),
    ("dangling_class_reference", FingerprintError),
]


@pytest.mark.parametrize("stem, expected_type", INVALID_FIXTURES)
def test_validate_reports_expected_diagnostic_type(
    stem: str,
    expected_type: type[ConfigError | ConfigWarning],
):
    result = validate(fixture_path(INVALID_DIR, stem))

    if stem == "missing_catch_all_class":
        assert result.is_valid is True
        assert any(isinstance(item, ConfigWarning) for item in result.warnings)
        warning = next(item for item in result.warnings if isinstance(item, ConfigWarning))
        assert warning.message
        assert warning.yaml_path
        assert warning.line is not None
        assert warning.suggestion

        strict_result = validate(fixture_path(INVALID_DIR, stem), strict=True)
        assert strict_result.is_valid is False
        assert strict_result.warnings == []
        return

    assert result.is_valid is False
    assert any(isinstance(error, expected_type) for error in result.errors)
    error = next(error for error in result.errors if isinstance(error, expected_type))
    assert error.message
    assert error.yaml_path
    assert error.line is not None
    _ = error.suggestion
