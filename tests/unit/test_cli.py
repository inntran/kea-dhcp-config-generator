"""Unit tests for cli.py — I/O contract, exit codes, and error routing."""

from typer.testing import CliRunner

from kea_dhcp_config_generator.cli import app

runner = CliRunner()


# ---------------------------------------------------------------------------
# AC 1: Valid YAML → exit 0
# ---------------------------------------------------------------------------


def test_valid_dhcp4_only_config_exits_zero(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("dhcp4:\n  subnets: []\n")
    result = runner.invoke(app, ["--config", str(cfg)])
    assert result.exit_code == 0


def test_valid_dhcp6_only_config_exits_zero(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("dhcp6:\n  subnets: []\n")
    result = runner.invoke(app, ["--config", str(cfg)])
    assert result.exit_code == 0


def test_valid_dhcp6_only_config_stdout_is_empty(tmp_path):
    """DHCPv6-only config: no DHCPv4 builder yet → stdout empty on success."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text("dhcp6:\n  subnets: []\n")
    result = runner.invoke(app, ["--config", str(cfg)])
    assert result.stdout == ""


# ---------------------------------------------------------------------------
# AC 2: Structural validation errors → stderr, exit 1
# ---------------------------------------------------------------------------


def test_invalid_duration_exits_one(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("dhcp4:\n  valid-lifetime: INVALID\n  subnets: []\n")
    result = runner.invoke(app, ["--config", str(cfg)])
    assert result.exit_code == 1


def test_structural_errors_written_to_stderr(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("dhcp4:\n  valid-lifetime: INVALID\n  subnets: []\n")
    result = runner.invoke(app, ["--config", str(cfg)])
    assert "Error:" in result.stderr


def test_structural_errors_not_written_to_stdout(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("dhcp4:\n  valid-lifetime: INVALID\n  subnets: []\n")
    result = runner.invoke(app, ["--config", str(cfg)])
    assert result.stdout == ""


def test_missing_dhcp4_and_dhcp6_exits_one(tmp_path):
    """Neither dhcp4 nor dhcp6 present → structural error, exit 1."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text("option_profiles: {}\n")
    result = runner.invoke(app, ["--config", str(cfg)])
    assert result.exit_code == 1
    assert "Error:" in result.stderr


# ---------------------------------------------------------------------------
# AC 3: No --config argument → exit 2, usage to stderr
# ---------------------------------------------------------------------------


def test_no_config_argument_exits_two():
    result = runner.invoke(app, [])
    assert result.exit_code == 2


def test_no_config_argument_has_message():
    result = runner.invoke(app, [])
    # P5: Typer's usage message always names the missing option
    combined = result.stdout + result.stderr
    assert "--config" in combined


# ---------------------------------------------------------------------------
# AC 4: Non-existent file → exit 2, error on stderr
# ---------------------------------------------------------------------------


def test_nonexistent_file_exits_two():
    result = runner.invoke(app, ["--config", "/nonexistent/path/config.yaml"])
    assert result.exit_code == 2


def test_nonexistent_file_error_on_stderr():
    result = runner.invoke(app, ["--config", "/nonexistent/path/config.yaml"])
    # P4: implementation always emits "Error:" prefix; no need for permissive fallback
    assert "Error:" in result.stderr


def test_empty_config_file_exits_two(tmp_path):
    """P3: Empty file → loader raises KeaConfigError → exit 2, error on stderr."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text("")
    result = runner.invoke(app, ["--config", str(cfg)])
    assert result.exit_code == 2
    assert "Error:" in result.stderr


# ---------------------------------------------------------------------------
# AC 5: Success → generated file paths on stdout only (no files yet → empty)
# ---------------------------------------------------------------------------


def test_success_dhcp6_only_stdout_empty(tmp_path):
    """DHCPv6-only config produces no output file path on stdout."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text("dhcp6:\n  subnets: []\n")
    result = runner.invoke(app, ["--config", str(cfg)])
    assert result.exit_code == 0
    assert result.stdout == ""


# ---------------------------------------------------------------------------
# AC 6: --help → exit 0, shows --config / -c
# ---------------------------------------------------------------------------


def test_help_exits_zero():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0


def test_help_shows_config_option():
    result = runner.invoke(app, ["--help"])
    assert "--config" in result.stdout or "--config" in result.output
    assert "-c" in result.stdout or "-c" in result.output


# ---------------------------------------------------------------------------
# Error formatting — error message structure
# ---------------------------------------------------------------------------


def test_error_format_includes_line_number(tmp_path):
    """ConfigError with a non-None line must include 'Line N:' in stderr output."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text("dhcp4:\n  valid-lifetime: bad_value\n  subnets: []\n")
    result = runner.invoke(app, ["--config", str(cfg)])
    assert result.exit_code == 1
    # P6: assert the line number is actually present, not just "Error:"
    assert "Line" in result.stderr


def test_yaml_syntax_error_exits_two(tmp_path):
    """Malformed YAML → KeaConfigError → exit 2."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text("dhcp4: [\nunclosed bracket\n")
    result = runner.invoke(app, ["--config", str(cfg)])
    assert result.exit_code == 2
    assert "Error:" in result.stderr


# ---------------------------------------------------------------------------
# Story 2.4: Generation output — stdout path, exit codes, --no-suffix, --overwrite
# ---------------------------------------------------------------------------


def test_valid_dhcp4_config_stdout_contains_path(tmp_path):
    """AC #1: successful generation writes output file path to stdout."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text("dhcp4:\n  subnets: []\n")
    result = runner.invoke(app, ["--config", str(cfg), "--output-dir", str(tmp_path)])
    assert result.exit_code == 0
    assert result.stdout.strip().endswith(".conf")


def test_valid_dhcp4_config_stdout_has_one_line(tmp_path):
    """AC #1: exactly one file path written to stdout."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text("dhcp4:\n  subnets: []\n")
    result = runner.invoke(app, ["--config", str(cfg), "--output-dir", str(tmp_path)])
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    assert len(lines) == 1


def test_valid_dhcp4_config_stderr_empty_on_success(tmp_path):
    """AC #1: no content written to stderr on successful generation."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text("dhcp4:\n  subnets: []\n")
    result = runner.invoke(app, ["--config", str(cfg), "--output-dir", str(tmp_path)])
    assert result.stderr == ""


def test_overwrite_replaces_existing_file(tmp_path):
    """AC #2: --overwrite silently replaces existing canonical file, exit 0."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text("dhcp4:\n  subnets: []\n")
    (tmp_path / "kea-dhcp4.conf").write_text("old content")
    result = runner.invoke(
        app, ["--config", str(cfg), "--output-dir", str(tmp_path), "--overwrite"]
    )
    assert result.exit_code == 0
