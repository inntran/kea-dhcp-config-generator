"""kea-dhcp-config-generator: Generate Kea DHCP configuration from declarative YAML."""

from __future__ import annotations

from pathlib import Path

from ruamel.yaml.comments import CommentedMap

from kea_dhcp_config_generator import loader
from kea_dhcp_config_generator.builders import dhcp4 as dhcp4_builder
from kea_dhcp_config_generator.builders import dhcp6 as dhcp6_builder
from kea_dhcp_config_generator.fingerprints import DHCPFingerprint
from kea_dhcp_config_generator.models import input as input_models
from kea_dhcp_config_generator.validation import output_schema as output_schema_validation
from kea_dhcp_config_generator.validation import semantic as semantic_validation
from kea_dhcp_config_generator.validation.errors import (
    ConfigError,
    ConfigWarning,
    FingerprintError,
    GenerationResult,
    KeaConfigError,
    OptionDataError,
    SubnetConfigError,
    ValidationResult,
)

__version__ = "0.1.0"


def _build_fingerprint_library(
    config: input_models.GlobalConfig | None,
) -> DHCPFingerprint | None:
    """Build the shared fingerprint library when either stack is present."""
    if config is None:
        return None
    if config.dhcp4 is None and config.dhcp6 is None:
        return None
    return DHCPFingerprint(pinned_version=config.fingerprint_library_version)


def _run_semantic_validators(
    config: input_models.GlobalConfig | None,
    raw: CommentedMap,
) -> tuple[list[ConfigError], list[ConfigWarning], DHCPFingerprint | None]:
    """Run semantic validation layers (subnet checks + classification checks).

    Returns the errors, warnings, and the fingerprint library built for this
    call. The library is returned directly (not stashed in module state) so the
    function is reentrant and safe under concurrent validate()/generate() calls.
    """
    if config is None:
        return [], [], None

    errors: list[ConfigError] = list(semantic_validation.validate_semantic(config, raw))
    warnings: list[ConfigWarning] = []
    library = _build_fingerprint_library(config)
    if library is not None:
        warnings.extend(library.warnings)
    class_errors, class_warnings = semantic_validation.validate_classification(
        config, raw, library
    )
    errors.extend(class_errors)
    warnings.extend(class_warnings)
    return errors, warnings, library


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


def _promote_warnings_to_errors(warnings: list[ConfigWarning]) -> list[ConfigError]:
    """Convert warnings to config errors for strict mode."""
    return [
        ConfigError(
            message=warning.message,
            yaml_path=warning.yaml_path,
            line=warning.line,
            suggestion=warning.suggestion,
        )
        for warning in warnings
    ]


def _collect_validation_state(
    config_path: Path,
    strict: bool,
) -> tuple[
    input_models.GlobalConfig | None,
    list[ConfigError],
    list[ConfigWarning],
    DHCPFingerprint | None,
]:
    """Load, parse, and semantically validate a config without writing output."""
    raw = loader.load(config_path)

    errors: list[ConfigError] = []
    warnings: list[ConfigWarning] = []
    validated_config: input_models.GlobalConfig | None = None

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

    library: DHCPFingerprint | None = None
    if validated_config is not None:
        semantic_errors, semantic_warnings, library = _run_semantic_validators(
            validated_config, raw
        )
        errors.extend(semantic_errors)
        warnings.extend(semantic_warnings)

    if strict and warnings:
        errors.extend(_promote_warnings_to_errors(warnings))
        warnings = []

    return validated_config, errors, warnings, library


def _build_generated_outputs(
    config: input_models.GlobalConfig,
    library: DHCPFingerprint | None,
    output_dir: Path,
    overwrite: bool,
    warnings: list[ConfigWarning],
) -> GenerationResult:
    """Build, schema-validate, and write every requested output file."""
    built_outputs: list[tuple[dict, str]] = []
    if config.dhcp4 is not None:
        built_outputs.append(
            (dhcp4_builder.build(config, fingerprint_library=library), "dhcp4")
        )
    if config.dhcp6 is not None:
        built_outputs.append(
            (dhcp6_builder.build(config, fingerprint_library=library), "dhcp6")
        )

    schema_errors: list[ConfigError] = []
    for built, protocol in built_outputs:
        try:
            if protocol == "dhcp4":
                output_schema_validation.validate_dhcp4(built)
            else:
                output_schema_validation.validate_dhcp6(built)
        except ConfigError as exc:
            schema_errors.append(exc)

    if schema_errors:
        raise ExceptionGroup("Output schema validation failed", schema_errors)

    dhcp4_path: Path | None = None
    dhcp6_path: Path | None = None
    from kea_dhcp_config_generator import writer

    for built, protocol in built_outputs:
        output_path = writer.write(built, protocol, output_dir, overwrite=overwrite)
        if protocol == "dhcp4":
            dhcp4_path = output_path
        else:
            dhcp6_path = output_path

    return GenerationResult(
        dhcp4_path=dhcp4_path,
        dhcp6_path=dhcp6_path,
        warnings=warnings,
    )


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
    validated_config, errors, warnings, _library = _collect_validation_state(
        config_path, strict
    )
    return ValidationResult(
        errors=errors,
        warnings=warnings,
        validated_config=validated_config,
    )


def generate(
    config_path: Path,
    output_dir: Path = Path("."),
    overwrite: bool = False,
    strict: bool = False,
) -> GenerationResult:
    """Generate Kea JSON output files from a YAML config.

    Raises:
        KeaConfigError: For fatal conditions (file not found, YAML syntax error,
            unexpected top-level type) and unexpected non-validation exceptions.
        ExceptionGroup[ConfigError]: For structural, semantic, or schema validation
            failures. Each leaf carries message, yaml_path, line, and suggestion.
    """
    validated_config, errors, warnings, library = _collect_validation_state(
        config_path, strict
    )
    if errors:
        raise ExceptionGroup("Validation failed", errors)
    assert validated_config is not None
    generation_result = _build_generated_outputs(
        validated_config,
        library,
        output_dir,
        overwrite,
        warnings,
    )
    return generation_result


__all__ = [
    "generate",
    "validate",
    "GenerationResult",
    "ValidationResult",
    "ConfigError",
    "ConfigWarning",
    "KeaConfigError",
    "SubnetConfigError",
    "OptionDataError",
    "FingerprintError",
]
