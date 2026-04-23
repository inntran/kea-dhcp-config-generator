"""kea-dhcp-config-generator: Generate Kea DHCP configuration from declarative YAML."""

from __future__ import annotations

from pathlib import Path

from kea_dhcp_config_generator import loader
from kea_dhcp_config_generator.models import input as input_models
from kea_dhcp_config_generator.validation.errors import (
    ConfigError,
    ConfigWarning,
    KeaConfigError,
    ValidationResult,
)

__version__ = "0.1.0"


def _run_semantic_validators() -> tuple[list[ConfigError], list[ConfigWarning]]:
    """Run semantic validation layers.

    Story 4.1 only includes structural validation, so this currently returns no
    additional diagnostics. Later stories extend this to emit semantic errors
    and warnings.
    """
    return [], []


def _partition_exception_group(
    exc: BaseExceptionGroup,
) -> tuple[list[ConfigError], list[BaseException]]:
    """Split exception-group leaves into config errors vs unexpected exceptions."""
    config_errors: list[ConfigError] = []
    unexpected: list[BaseException] = []

    for child in exc.exceptions:
        if isinstance(child, ConfigError):
            config_errors.append(child)
            continue
        if isinstance(child, BaseExceptionGroup):
            nested_errors, nested_unexpected = _partition_exception_group(child)
            config_errors.extend(nested_errors)
            unexpected.extend(nested_unexpected)
            continue
        unexpected.append(child)

    return config_errors, unexpected


def validate(
    config_path: Path,
    strict: bool = False,
) -> ValidationResult:
    """Validate a YAML config file without writing any output files.

    Loads and structurally validates the config, then runs semantic validators.
    In Story 4.1, semantic validators are placeholders and return no diagnostics.

    Args:
        config_path: Path to the YAML config file.
        strict: When True, warnings are promoted to errors.

    Returns:
        ValidationResult with validated_config, is_valid, errors list, and warnings list.

    Raises:
        KeaConfigError: For fatal conditions (file not found, YAML syntax error,
            unexpected top-level type) and unexpected non-validation exceptions
            encountered while validating.
    """
    raw = loader.load(config_path)  # raises KeaConfigError on fatal conditions

    errors: list[ConfigError] = []
    warnings: list[ConfigWarning] = []

    validated_config = None
    try:
        validated_config = input_models.parse(raw)
    except ConfigError as exc:
        errors.append(exc)
    except BaseExceptionGroup as eg:
        group_errors, unexpected = _partition_exception_group(eg)
        errors.extend(group_errors)
        if unexpected:
            unexpected_group = ExceptionGroup("Unexpected validation failures", unexpected)
            raise KeaConfigError(
                f"Unexpected validation failure while validating {config_path}",
                path=str(config_path),
            ) from unexpected_group
    except Exception as eg:
        raise KeaConfigError(
            f"Unexpected validation failure while validating {config_path}",
            path=str(config_path),
        ) from eg

    semantic_errors, semantic_warnings = _run_semantic_validators()
    errors.extend(semantic_errors)
    warnings.extend(semantic_warnings)

    if strict and warnings:
        errors.extend(
            ConfigError(
                message=warning.message,
                yaml_path=warning.yaml_path,
                line=warning.line,
                suggestion=warning.suggestion,
            )
            for warning in warnings
        )

    return ValidationResult(
        errors=errors,
        warnings=warnings,
        validated_config=validated_config,
    )
