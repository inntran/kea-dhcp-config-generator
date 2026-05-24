"""CLI entry point for kea-confgen."""

from pathlib import Path

import typer

from kea_dhcp_config_generator import loader, writer
from kea_dhcp_config_generator.builders import dhcp4 as dhcp4_builder
from kea_dhcp_config_generator.fingerprints import DHCPFingerprint
from kea_dhcp_config_generator.models import input as input_models
from kea_dhcp_config_generator.validation import semantic as semantic_validation
from kea_dhcp_config_generator.validation.errors import (
    ConfigError,
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
    semantic_errors = semantic_validation.validate_semantic(config_model, raw)
    if semantic_errors:
        try:
            raise ExceptionGroup("Semantic validation failed", semantic_errors)
        except* ConfigError as eg:
            for error in eg.exceptions:
                typer.echo(_format_error(error), err=True)
            raise typer.Exit(code=1) from None

    # Stage 3: Generation — DHCPv4 config build + output file write
    # stdout is reserved for generated file paths (one per line).
    try:
        if config_model.dhcp4 is not None:
            lib = DHCPFingerprint(pinned_version=config_model.fingerprint_library_version)
            for w in lib.warnings:
                typer.echo(f"Warning: {w.message}", err=True)  # stderr; never stdout
            built = dhcp4_builder.build(config_model, fingerprint_library=lib)
            output_path = writer.write(
                built,
                "dhcp4",
                output_dir,
                overwrite=overwrite,
            )
            typer.echo(str(output_path))  # stdout: generated file path
    except typer.Exit:
        raise
    except Exception as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=2) from None

    raise typer.Exit(code=0)
