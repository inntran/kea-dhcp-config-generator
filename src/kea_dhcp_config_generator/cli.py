"""CLI entry point for kea-confgen."""

from pathlib import Path

import typer

from kea_dhcp_config_generator import loader
from kea_dhcp_config_generator.models import input as input_models
from kea_dhcp_config_generator.validation.errors import ConfigError, KeaConfigError

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
            input_models.parse(raw)
        except* ConfigError as eg:
            for error in eg.exceptions:
                typer.echo(_format_error(error), err=True)
            raise typer.Exit(code=1) from None
    except typer.Exit:
        raise
    except Exception as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=2) from None

    # Stage 3: Generation (Epic 2+) — not yet implemented
    # stdout is reserved for generated file paths (one per line).
    # No files generated in this story → stdout is empty on success.
    raise typer.Exit(code=0)
