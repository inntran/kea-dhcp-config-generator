"""Unit tests for validation/errors.py."""

from pathlib import Path

import pytest

from kea_dhcp_config_generator import validate
from kea_dhcp_config_generator.validation.errors import (
    ConfigError,
    ConfigWarning,
    FingerprintError,
    KeaConfigError,
    OptionDataError,
    SubnetConfigError,
    ValidationResult,
)


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


# ---- Story 4.1 tests ----


def test_subnet_config_error_is_subclass_of_config_error():
    assert issubclass(SubnetConfigError, ConfigError)


def test_subnet_config_error_carries_all_fields():
    exc = SubnetConfigError(
        message="overlapping CIDRs",
        yaml_path="dhcp4.subnets[0]",
        line=10,
        suggestion="fix prefix length",
    )
    assert exc.message == "overlapping CIDRs"
    assert exc.yaml_path == "dhcp4.subnets[0]"
    assert exc.line == 10
    assert exc.suggestion == "fix prefix length"


def test_subnet_config_error_caught_as_config_error():
    with pytest.raises(ConfigError):
        raise SubnetConfigError(
            message="test", yaml_path="dhcp4.subnets[0]", line=None, suggestion=None
        )


def test_option_data_error_is_subclass_of_config_error():
    assert issubclass(OptionDataError, ConfigError)


def test_option_data_error_carries_all_fields():
    exc = OptionDataError(
        message="invalid option code",
        yaml_path="dhcp4.subnets[0].option-data[0]",
        line=5,
        suggestion=None,
    )
    assert exc.message == "invalid option code"
    assert exc.yaml_path == "dhcp4.subnets[0].option-data[0]"
    assert exc.line == 5
    assert exc.suggestion is None


def test_option_data_error_caught_as_config_error():
    with pytest.raises(ConfigError):
        raise OptionDataError(
            message="test", yaml_path="path", line=None, suggestion=None
        )


def test_fingerprint_error_is_subclass_of_config_error():
    assert issubclass(FingerprintError, ConfigError)


def test_fingerprint_error_carries_all_fields():
    exc = FingerprintError(
        message="unknown fingerprint class",
        yaml_path="dhcp4.subnets[0].client-class",
        line=20,
        suggestion="did you mean: MyClass?",
    )
    assert exc.message == "unknown fingerprint class"
    assert exc.yaml_path == "dhcp4.subnets[0].client-class"
    assert exc.line == 20
    assert exc.suggestion == "did you mean: MyClass?"


def test_fingerprint_error_caught_as_config_error():
    with pytest.raises(ConfigError):
        raise FingerprintError(
            message="test", yaml_path="path", line=None, suggestion=None
        )


def test_validation_result_defaults():
    result = ValidationResult()
    assert result.is_valid is True
    assert result.errors == []
    assert result.warnings == []
    assert result.validated_config is None


def test_validation_result_with_errors_is_invalid():
    err = ConfigError(message="bad", yaml_path="dhcp4", line=1, suggestion=None)
    result = ValidationResult(errors=[err])
    assert result.is_valid is False
    assert len(result.errors) == 1


def test_validation_result_invariant_is_derived_from_errors():
    result = ValidationResult(errors=[ConfigError("bad", "dhcp4", 1, None)])
    assert result.is_valid is False


def test_collect_all_pattern_accumulates_errors():
    errors = [
        ConfigError(message="first error", yaml_path="dhcp4.subnets[0]", line=1, suggestion=None),
        ConfigError(message="second error", yaml_path="dhcp4.subnets[1]", line=2, suggestion=None),
    ]
    raised_errors = []
    try:
        raise ExceptionGroup("Validation failed", errors)
    except* ConfigError as eg:
        raised_errors.extend(eg.exceptions)
    assert len(raised_errors) == 2
    assert raised_errors[0].message == "first error"
    assert raised_errors[1].message == "second error"


def test_validate_valid_config_returns_valid(tmp_path: Path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("dhcp4:\n  subnets: []\n")
    result = validate(cfg)
    assert result.is_valid is True
    assert result.errors == []
    assert result.warnings == []
    assert result.validated_config is not None


def test_validate_invalid_config_returns_errors(tmp_path: Path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("dhcp4:\n  valid-lifetime: INVALID\n  subnets: []\n")
    result = validate(cfg)
    assert result.is_valid is False
    assert len(result.errors) > 0
    assert result.validated_config is None


def test_validate_collects_single_config_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("dhcp4:\n  subnets: []\n")

    import kea_dhcp_config_generator as api

    def _raise_single(_: object) -> None:
        raise ConfigError("single", "dhcp4", 1, None)

    monkeypatch.setattr(api.input_models, "parse", _raise_single)
    result = validate(cfg)
    assert result.is_valid is False
    assert len(result.errors) == 1
    assert result.errors[0].message == "single"


def test_validate_propagates_loader_kea_config_error(tmp_path: Path):
    missing = tmp_path / "missing.yaml"
    with pytest.raises(KeaConfigError):
        validate(missing)


def test_validate_strict_false_keeps_warnings_non_fatal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("dhcp4:\n  subnets: []\n")

    import kea_dhcp_config_generator as api

    monkeypatch.setattr(
        api,
        "_run_semantic_validators",
        lambda _config, _raw: (
            [],
            [
                ConfigWarning(
                    message="deprecated fingerprint",
                    yaml_path="dhcp4.client-classes[0]",
                    line=12,
                    suggestion="use new name",
                )
            ],
        ),
    )

    result = validate(cfg, strict=False)
    assert result.is_valid is True
    assert result.errors == []
    assert len(result.warnings) == 1



def test_validate_strict_true_promotes_warnings_to_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("dhcp4:\n  subnets: []\n")

    import kea_dhcp_config_generator as api

    monkeypatch.setattr(
        api,
        "_run_semantic_validators",
        lambda _config, _raw: (
            [],
            [
                ConfigWarning(
                    message="deprecated fingerprint",
                    yaml_path="dhcp4.client-classes[0]",
                    line=12,
                    suggestion="use new name",
                )
            ],
        ),
    )

    result = validate(cfg, strict=True)
    assert result.is_valid is False
    assert len(result.errors) == 1
    assert result.errors[0].message == "deprecated fingerprint"
    assert result.warnings == []


def test_validate_wraps_unexpected_non_configerror(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("dhcp4:\n  subnets: []\n")

    import kea_dhcp_config_generator as api

    def _raise_mixed_group(_: object) -> None:
        raise ExceptionGroup(
            "mixed validation failures",
            [
                ConfigError(message="known bad value", yaml_path="dhcp4", line=1, suggestion=None),
                ValueError("unexpected bug"),
            ],
        )

    monkeypatch.setattr(api.input_models, "parse", _raise_mixed_group)

    with pytest.raises(KeaConfigError, match="Unexpected validation failure"):
        validate(cfg)


# ---- Story 4.2 tests ----


def test_validate_surfaces_semantic_errors(tmp_path: Path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "dhcp4:\n"
        "  subnets:\n"
        "    - subnet: 10.0.4.0/23\n"
        "    - subnet: 10.0.4.0/24\n"
    )
    result = validate(cfg)
    assert result.is_valid is False
    assert any(isinstance(e, SubnetConfigError) for e in result.errors)
    assert result.warnings == []


def test_validate_skips_semantic_when_structural_fails(tmp_path: Path):
    """Structural failure leaves validated_config=None and skips semantic checks."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "dhcp4:\n"
        "  valid-lifetime: INVALID\n"
        "  subnets:\n"
        "    - subnet: 10.0.4.0/23\n"
        "    - subnet: 10.0.4.0/24\n"  # would overlap, but semantic should not run
    )
    result = validate(cfg)
    assert result.is_valid is False
    # All errors are structural; none should be a SubnetConfigError from the semantic pass.
    assert not any(isinstance(e, SubnetConfigError) for e in result.errors)


# ---- Story 4.3 tests ----


def test_validate_no_catch_all_emits_warning_only(tmp_path: Path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "dhcp4:\n"
        "  subnets:\n"
        "    - subnet: 10.0.1.0/24\n"
        "      pools:\n"
        "        - range: 10.0.1.10 - 10.0.1.50\n"
        "          client-class: Android_12_14\n"
        "        - range: 10.0.1.60 - 10.0.1.100\n"
        "          client-class: macOS\n"
    )
    result = validate(cfg)
    assert result.is_valid is True
    assert len(result.warnings) == 1
    assert result.errors == []


def test_validate_strict_promotes_catch_all_warning_to_error(tmp_path: Path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "dhcp4:\n"
        "  subnets:\n"
        "    - subnet: 10.0.1.0/24\n"
        "      pools:\n"
        "        - range: 10.0.1.10 - 10.0.1.50\n"
        "          client-class: Android_12_14\n"
        "        - range: 10.0.1.60 - 10.0.1.100\n"
        "          client-class: macOS\n"
    )
    result = validate(cfg, strict=True)
    assert result.is_valid is False
    assert len(result.errors) >= 1
    assert any("catch-all" in e.message for e in result.errors)


def test_validate_unknown_class_yields_fingerprint_error(tmp_path: Path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "dhcp4:\n"
        "  subnets:\n"
        "    - subnet: 10.0.1.0/24\n"
        "      pools:\n"
        "        - range: auto\n"
        "          client-class: zZqXX_no_match_here\n"
    )
    result = validate(cfg)
    assert result.is_valid is False
    assert any(isinstance(e, FingerprintError) for e in result.errors)
    # Errors are not affected by strict; behavior identical under strict=True.
    result_strict = validate(cfg, strict=True)
    assert result_strict.is_valid is False
    assert any(isinstance(e, FingerprintError) for e in result_strict.errors)


def test_validate_surfaces_version_mismatch_warning(tmp_path: Path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "fingerprint_library_version: \"0.0.0-test-mismatch\"\n"
        "dhcp4:\n"
        "  subnets: []\n"
    )
    result = validate(cfg)
    assert any("version mismatch" in w.message for w in result.warnings)
