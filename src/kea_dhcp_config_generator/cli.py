"""CLI entry point for kea-confgen."""

from pathlib import Path

import typer

app = typer.Typer(
    name="kea-confgen",
    help="Generate Kea DHCPv4/DHCPv6 JSON configuration from a YAML source file.",
    add_completion=False,
)


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
    # Implementation added in Story 1.4
    typer.echo("Not yet implemented.", err=True)
    raise typer.Exit(code=2)
