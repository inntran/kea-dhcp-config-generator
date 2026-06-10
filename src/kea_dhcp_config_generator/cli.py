"""CLI entry point for kea-confgen."""

from pathlib import Path

import typer

import kea_dhcp_config_generator as api
from kea_dhcp_config_generator import writer
from kea_dhcp_config_generator.analysis import report as analysis_report
from kea_dhcp_config_generator.validation.errors import (
    ConfigError,
    ConfigWarning,
    KeaConfigError,
    ValidationResult,
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
    analysis: bool = typer.Option(
        False,
        "--analysis",
        help="Generate analysis report alongside JSON (or analysis-only with --analysis-only).",
    ),
    analysis_only: bool = typer.Option(
        False,
        "--analysis-only",
        help="Print the analysis report to stdout and skip JSON generation.",
    ),
) -> None:
    """Generate Kea DHCPv4/DHCPv6 JSON configuration from YAML."""
    try:
        validated_config, errors, warnings, library = api._collect_validation_state(
            config, strict
        )
    except KeaConfigError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=2) from None
    except Exception as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=2) from None

    for warning in warnings:
        typer.echo(_format_warning(warning), err=True)

    if errors:
        for error in errors:
            typer.echo(_format_error(error), err=True)
        
        # If analysis or analysis_only requested, still produce report (even with errors)
        # but only if validated_config is not None (config parsed structurally).
        if (analysis or analysis_only) and validated_config is not None:
            validation_result = ValidationResult(
                errors=errors,
                warnings=warnings,
                validated_config=validated_config,
            )
            report = analysis_report.generate_report(validated_config, validation_result)
            typer.echo(report)
        
        raise typer.Exit(code=1) from None

    # If analysis_only or analysis requested, build and output the report.
    if analysis_only or analysis:
        validation_result = ValidationResult(
            errors=errors,
            warnings=warnings,
            validated_config=validated_config,
        )
        report = analysis_report.generate_report(validated_config, validation_result)
        
        # analysis_only: print to stdout, don't generate JSON
        if analysis_only:
            typer.echo(report)
            raise typer.Exit(code=0)
        
        # analysis (generation + analysis): continue to generate JSON below
    
    # Generate JSON output (normal path or generation+analysis path)
    try:
        try:
            result = api._build_generated_outputs(
                validated_config,
                library,
                output_dir,
                overwrite,
                warnings,
            )
        except* ConfigError as eg:
            for error in eg.exceptions:
                typer.echo(_format_error(error), err=True)
            raise typer.Exit(code=1) from None
    except typer.Exit:
        raise
    except KeaConfigError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=2) from None
    except Exception as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=2) from None

    # Output JSON file paths
    if result.dhcp4_path is not None:
        typer.echo(str(result.dhcp4_path))
    if result.dhcp6_path is not None:
        typer.echo(str(result.dhcp6_path))
    
    # If analysis flag (not just analysis_only), also generate and output analysis file path
    if analysis:
        validation_result = ValidationResult(
            errors=errors,
            warnings=warnings,
            validated_config=validated_config,
        )
        report = analysis_report.generate_report(validated_config, validation_result)
        analysis_path = writer.write_analysis(
            report, output_dir, overwrite=overwrite
        )
        typer.echo(str(analysis_path))
    
    raise typer.Exit(code=0)
