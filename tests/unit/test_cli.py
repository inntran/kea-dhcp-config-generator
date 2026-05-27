"""Unit tests for cli.py — I/O contract, exit codes, and error routing."""

import pytest
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


# ---------------------------------------------------------------------------
# Story 4.2: Semantic validation errors → stderr, exit 1, no output file
# ---------------------------------------------------------------------------


def test_semantic_overlap_exits_one(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "dhcp4:\n"
        "  subnets:\n"
        "    - subnet: 10.0.4.0/23\n"
        "    - subnet: 10.0.4.0/24\n"
    )
    result = runner.invoke(app, ["--config", str(cfg), "--output-dir", str(tmp_path)])
    assert result.exit_code == 1
    assert "Error: Line" in result.stderr
    assert "10.0.4.0/23" in result.stderr
    assert "10.0.4.0/24" in result.stderr
    assert result.stdout == ""
    # No output file written.
    assert not (tmp_path / "kea-dhcp4.conf").exists()
    assert not any(p.name.startswith("kea-dhcp4-") for p in tmp_path.iterdir())


# ---------------------------------------------------------------------------
# Story 4.3: --strict flag, classification errors & catch-all warnings
# ---------------------------------------------------------------------------


_NO_CATCH_ALL_CFG = (
    "dhcp4:\n"
    "  subnets:\n"
    "    - subnet: 10.0.1.0/24\n"
    "      pools:\n"
    "        - range: 10.0.1.10 - 10.0.1.50\n"
    "          client-class: Android_12_14\n"
    "        - range: 10.0.1.60 - 10.0.1.100\n"
    "          client-class: macOS\n"
)


def test_strict_promotes_catch_all_warning_to_error(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(_NO_CATCH_ALL_CFG)
    result = runner.invoke(
        app,
        ["--config", str(cfg), "--output-dir", str(tmp_path), "--strict"],
    )
    assert result.exit_code == 1
    assert "Error:" in result.stderr
    assert "catch-all" in result.stderr
    assert result.stdout == ""
    assert not (tmp_path / "kea-dhcp4.conf").exists()


def test_no_strict_emits_catch_all_warning_but_succeeds(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(_NO_CATCH_ALL_CFG)
    result = runner.invoke(
        app,
        ["--config", str(cfg), "--output-dir", str(tmp_path), "--overwrite"],
    )
    assert result.exit_code == 0
    assert "Warning:" in result.stderr
    assert "catch-all" in result.stderr
    assert (tmp_path / "kea-dhcp4.conf").exists()


def test_unknown_class_exits_one(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "dhcp4:\n"
        "  subnets:\n"
        "    - subnet: 10.0.1.0/24\n"
        "      pools:\n"
        "        - range: auto\n"
        "          client-class: zZqXX_no_match_here\n"
    )
    result = runner.invoke(app, ["--config", str(cfg), "--output-dir", str(tmp_path)])
    assert result.exit_code == 1
    assert "Error:" in result.stderr
    assert "unknown client-class" in result.stderr
    assert result.stdout == ""


def test_strict_does_not_swallow_real_errors(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "dhcp4:\n"
        "  subnets:\n"
        "    - subnet: 10.0.1.0/24\n"
        "      pools:\n"
        "        - range: 10.0.1.10 - 10.0.1.50\n"
        "          client-class: zZqXX_no_match_here\n"
        "        - range: 10.0.1.60 - 10.0.1.100\n"
        "          client-class: macOS\n"
    )
    result = runner.invoke(
        app,
        ["--config", str(cfg), "--output-dir", str(tmp_path), "--strict"],
    )
    assert result.exit_code == 1
    assert "unknown client-class" in result.stderr
    assert "catch-all" in result.stderr


# ---------------------------------------------------------------------------
# Story 4.4: Output-schema validation — exit 1 on schema failure, no file written
# ---------------------------------------------------------------------------


def test_output_schema_failure_exits_one_and_writes_no_file(tmp_path, monkeypatch):
    """AC #7: a builder bug producing schema-invalid output blocks the write."""
    from kea_dhcp_config_generator import cli as cli_module

    real_build = cli_module.dhcp4_builder.build

    def broken_build(*args, **kwargs):
        result = real_build(*args, **kwargs)
        result["Dhcp4"]["valid-lifetime"] = "forever"  # schema violation
        return result

    monkeypatch.setattr(cli_module.dhcp4_builder, "build", broken_build)

    cfg = tmp_path / "config.yaml"
    cfg.write_text("dhcp4:\n  subnets: []\n")
    result = runner.invoke(
        app, ["--config", str(cfg), "--output-dir", str(tmp_path), "--overwrite"]
    )
    assert result.exit_code == 1
    assert "Error:" in result.stderr
    assert "valid-lifetime" in result.stderr
    assert result.stdout == ""
    assert not (tmp_path / "kea-dhcp4.conf").exists()
    assert not any(p.name.startswith("kea-dhcp4-") for p in tmp_path.iterdir())


def test_output_schema_happy_path_unchanged(tmp_path):
    """Regression: valid config still produces a file (schema validation is transparent)."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "dhcp4:\n"
        "  subnets:\n"
        "    - subnet: 10.0.1.0/24\n"
        "      pools:\n"
        "        - range: 10.0.1.10 - 10.0.1.50\n"
    )
    result = runner.invoke(
        app, ["--config", str(cfg), "--output-dir", str(tmp_path), "--overwrite"]
    )
    assert result.exit_code == 0
    assert (tmp_path / "kea-dhcp4.conf").exists()


@pytest.mark.skip(reason="DHCPv6 builder lands in Epic 5 (Story 5.2)")
def test_output_schema_collect_all_across_protocols(tmp_path):
    """AC #8 (deferred): when both protocols build, schema errors from both surface in one run."""
