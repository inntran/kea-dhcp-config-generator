"""CLI entry point for kea-confgen."""

from pathlib import Path

import typer

from kea_dhcp_config_generator import loader, writer
from kea_dhcp_config_generator.builders import dhcp4 as dhcp4_builder
from kea_dhcp_config_generator.builders import dhcp6 as dhcp6_builder
from kea_dhcp_config_generator.fingerprints import DHCPFingerprint
from kea_dhcp_config_generator.models import input as input_models
from kea_dhcp_config_generator.validation import (
    output_schema,
)
from kea_dhcp_config_generator.validation import (
    semantic as semantic_validation,
)
from kea_dhcp_config_generator.validation.errors import (
    ConfigError,
    ConfigWarning,
    KeaConfigError,
)

app = typer.Typer(
    name="kea-confgen",
    help="Generate Kea DHCPv4/DHCPv6 JSON configuration from a YAML source file.",
    add_completion=False,
)


def _format_error(error: ConfigError) -> str:
    """Format a ConfigError for human-readable stderr output."""
    if error.line is not None:
        base = f"Error: Line {error.line}: {error.yaml_path} — {error.message}"
    else:
        base = f"Error: {error.yaml_path} — {error.message}"
    if error.suggestion:
        base += f"\n  Suggestion: {error.suggestion}"  # P2: consistent 2-space indent
    return base


def _format_warning(warning: ConfigWarning) -> str:
    """Format a ConfigWarning for human-readable stderr output."""
    if warning.line is not None:
        base = f"Warning: Line {warning.line}: {warning.yaml_path} — {warning.message}"
    else:
        base = f"Warning: {warning.yaml_path} — {warning.message}"
    if warning.suggestion:
        base += f"\n  Suggestion: {warning.suggestion}"
    return base


@app.command()
def main(
    config: Path = typer.Option(
        ...,
        "--config",
        "-c",
        help="Path to the YAML configuration file.",
    ),
    overwrite: bool = typer.Option(
        False,
        "--overwrite",
        help="Write to canonical filename (kea-<protocol>.conf); overwrite if it exists.",
    ),
    output_dir: Path = typer.Option(
        Path("."),
        "--output-dir",
        hidden=True,
        help="Directory to write generated files (default: current directory).",
    ),
    strict: bool = typer.Option(
        False,
        "--strict",
        help="Promote configuration warnings (e.g. no catch-all pool) to errors.",
    ),
) -> None:
    """Generate Kea DHCPv4/DHCPv6 JSON configuration from YAML."""
    # Stage 1: Load YAML — file-not-found and YAML syntax errors → exit 2
    try:
        raw = loader.load(config)
    except KeaConfigError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=2) from None

    # Stage 2: Structural validation — field errors → exit 1
    # P1: outer try re-raises typer.Exit cleanly; converts any unexpected exception
    # (e.g. bare ValidationError from parse()'s defensive fallback) to exit 2.
    # Cannot mix except* and except in the same try block (PEP 654), so nested.
    try:
        try:
            config_model = input_models.parse(raw)
        except* ConfigError as eg:
            for error in eg.exceptions:
                typer.echo(_format_error(error), err=True)
            raise typer.Exit(code=1) from None
    except typer.Exit:
        raise
    except Exception as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=2) from None

    # Stage 2.5: Semantic validation — collect-all semantic errors → exit 1
    library: DHCPFingerprint | None = None
    if config_model.dhcp4 is not None or config_model.dhcp6 is not None:
        library = DHCPFingerprint(
            pinned_version=config_model.fingerprint_library_version
        )
        for w in library.warnings:
            if strict:
                continue  # promoted below alongside classification warnings
            typer.echo(_format_warning(w), err=True)

    semantic_errors: list[ConfigError] = list(
        semantic_validation.validate_semantic(config_model, raw)
    )
    class_errors, class_warnings = semantic_validation.validate_classification(
        config_model, raw, library
    )
    semantic_errors.extend(class_errors)

    if strict:
        promoted_sources: list[ConfigWarning] = list(class_warnings)
        if library is not None:
            promoted_sources.extend(library.warnings)
        semantic_errors.extend(
            ConfigError(
                message=w.message,
                yaml_path=w.yaml_path,
                line=w.line,
                suggestion=w.suggestion,
            )
            for w in promoted_sources
        )
    else:
        for w in class_warnings:
            typer.echo(_format_warning(w), err=True)

    if semantic_errors:
        try:
            raise ExceptionGroup("Semantic validation failed", semantic_errors)
        except* ConfigError as eg:
            for error in eg.exceptions:
                typer.echo(_format_error(error), err=True)
            raise typer.Exit(code=1) from None

    # Stage 3: Generation — build all → schema-validate all → write all.
    # stdout is reserved for generated file paths (one per line). No file is
    # written if any built dict fails output-schema validation (collect-all
    # across protocols before writing).
    try:
        try:
            built_outputs: list[tuple[dict, str]] = []
            if config_model.dhcp4 is not None:
                assert library is not None  # built in Stage 2.5 when dhcp4/dhcp6 set
                built_outputs.append(
                    (dhcp4_builder.build(config_model, fingerprint_library=library), "dhcp4")
                )
            if config_model.dhcp6 is not None:
                assert library is not None  # built in Stage 2.5 when dhcp4/dhcp6 set
                built_outputs.append(
                    (dhcp6_builder.build(config_model, fingerprint_library=library), "dhcp6")
                )

            schema_errors: list[ConfigError] = []
            for built, protocol in built_outputs:
                try:
                    if protocol == "dhcp4":
                        output_schema.validate_dhcp4(built)
                    elif protocol == "dhcp6":
                        output_schema.validate_dhcp6(built)
                    else:
                        raise ConfigError(
                            message=(
                                "Unsupported output protocol for schema "
                                f"validation: {protocol}"
                            ),
                            yaml_path="protocol",
                            line=None,
                            suggestion=None,
                        )
                except ConfigError as exc:
                    schema_errors.append(exc)
            if schema_errors:
                raise ExceptionGroup("Output schema validation failed", schema_errors)

            for built, protocol in built_outputs:
                output_path = writer.write(
                    built,
                    protocol,
                    output_dir,
                    overwrite=overwrite,
                )
                typer.echo(str(output_path))  # stdout: generated file path
        except* ConfigError as eg:
            for error in eg.exceptions:
                typer.echo(_format_error(error), err=True)
            raise typer.Exit(code=1) from None
    except typer.Exit:
        raise
    except Exception as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=2) from None

    raise typer.Exit(code=0)
