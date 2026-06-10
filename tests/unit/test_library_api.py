from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from kea_dhcp_config_generator import GenerationResult, generate, validate
from kea_dhcp_config_generator.validation.errors import SubnetConfigError


def _write_config(path: Path, content: str) -> Path:
    path.write_text(content)
    return path


def test_import_surface_and___all___without_cli():
    repo_root = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo_root / "src")
    script = (
        "import sys, kea_dhcp_config_generator as k; "
        "assert 'kea_dhcp_config_generator.cli' not in sys.modules; "
        "print('\\n'.join(sorted(k.__all__)))"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    assert result.stdout.splitlines() == [
        "ConfigError",
        "ConfigWarning",
        "FingerprintError",
        "GenerationResult",
        "KeaConfigError",
        "OptionDataError",
        "SubnetConfigError",
        "ValidationResult",
        "generate",
        "validate",
    ]
    assert result.stderr == ""

    assert callable(generate)
    assert callable(validate)
    assert GenerationResult.__name__ == "GenerationResult"


def test_generate_success_dual_stack(tmp_path: Path):
    cfg = _write_config(
        tmp_path / "config.yaml",
        "dhcp4:\n  subnets: []\n"
        "dhcp6:\n  subnets: []\n",
    )

    result = generate(cfg, output_dir=tmp_path)

    assert isinstance(result, GenerationResult)
    assert result.dhcp4_path is not None
    assert result.dhcp6_path is not None
    assert result.dhcp4_path.exists()
    assert result.dhcp6_path.exists()
    assert result.warnings == []


def test_generate_success_dhcp4_only(tmp_path: Path):
    cfg = _write_config(tmp_path / "config.yaml", "dhcp4:\n  subnets: []\n")

    result = generate(cfg, output_dir=tmp_path)

    assert result.dhcp4_path is not None
    assert result.dhcp4_path.exists()
    assert result.dhcp6_path is None


def test_generate_success_dhcp6_only(tmp_path: Path):
    cfg = _write_config(
        tmp_path / "config.yaml",
        "fingerprint_library_version: \"0.0.0-test-mismatch\"\n"
        "dhcp6:\n"
        "  subnets: []\n",
    )

    result = generate(cfg, output_dir=tmp_path)

    assert result.dhcp6_path is not None
    assert result.dhcp6_path.exists()
    assert result.dhcp4_path is None
    assert any("version mismatch" in warning.message for warning in result.warnings)


def test_generate_raises_typed_semantic_error(tmp_path: Path):
    cfg = _write_config(
        tmp_path / "config.yaml",
        "dhcp4:\n"
        "  subnets:\n"
        "    - subnet: 10.0.4.0/23\n"
        "    - subnet: 10.0.4.0/24\n",
    )

    with pytest.raises(ExceptionGroup) as excinfo:
        generate(cfg, output_dir=tmp_path)

    errors = excinfo.value.exceptions
    assert any(isinstance(error, SubnetConfigError) for error in errors)
    error = next(error for error in errors if isinstance(error, SubnetConfigError))
    assert error.message
    assert error.yaml_path == "dhcp4.subnets[1]"
    assert error.line is not None
    assert error.suggestion is not None


def test_generate_schema_failure_blocks_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from kea_dhcp_config_generator.builders import dhcp4 as dhcp4_builder

    real_build = dhcp4_builder.build

    def broken_build(*args, **kwargs):
        result = real_build(*args, **kwargs)
        result["Dhcp4"]["valid-lifetime"] = "forever"
        return result

    monkeypatch.setattr(dhcp4_builder, "build", broken_build)

    cfg = _write_config(tmp_path / "config.yaml", "dhcp4:\n  subnets: []\n")

    with pytest.raises(ExceptionGroup) as excinfo:
        generate(cfg, output_dir=tmp_path)

    assert any("valid-lifetime" in str(error) for error in excinfo.value.exceptions)
    assert not any(path.name.startswith("kea-dhcp4") for path in tmp_path.iterdir())


def test_validate_never_raises_for_validation_failures(tmp_path: Path):
    cfg = _write_config(
        tmp_path / "config.yaml",
        "dhcp4:\n"
        "  subnets:\n"
        "    - subnet: 10.0.4.0/23\n"
        "    - subnet: 10.0.4.0/24\n",
    )

    result = validate(cfg)

    assert not result.is_valid
    assert any(isinstance(error, SubnetConfigError) for error in result.errors)


def test_library_calls_are_silent(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    cfg = _write_config(tmp_path / "config.yaml", "dhcp4:\n  subnets: []\n")

    generate(cfg, output_dir=tmp_path)
    validate(cfg)

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_strict_promotes_warnings(tmp_path: Path):
    cfg = _write_config(
        tmp_path / "config.yaml",
        "dhcp4:\n"
        "  subnets:\n"
        "    - subnet: 10.0.1.0/24\n"
        "      pools:\n"
        "        - range: 10.0.1.10 - 10.0.1.50\n"
        "          client-class: Android_12_14\n"
        "        - range: 10.0.1.60 - 10.0.1.100\n"
        "          client-class: macOS\n",
    )

    result = validate(cfg, strict=True)
    assert not result.is_valid
    assert result.warnings == []
    assert any("catch-all" in error.message for error in result.errors)

    with pytest.raises(ExceptionGroup) as excinfo:
        generate(cfg, output_dir=tmp_path, strict=True)
    assert any("catch-all" in str(error) for error in excinfo.value.exceptions)


def test_run_semantic_validators_returns_library_no_module_state():
    """Reentrancy regression (codex 7.1 [P2]): the fingerprint library is returned
    directly from _run_semantic_validators, not stashed in module-level state, so
    interleaved calls cannot cross-contaminate each other's library/warnings."""
    import kea_dhcp_config_generator as api

    assert not hasattr(api, "_LAST_FINGERPRINT_LIBRARY")

    from io import StringIO

    from ruamel.yaml import YAML

    raw = YAML().load(StringIO("dhcp4:\n  subnets:\n    - subnet: 10.0.1.0/24\n"))
    config = api.input_models.parse(raw)
    errors, warnings, library = api._run_semantic_validators(config, raw)
    # Third element is the per-call library (built because dhcp4 is present).
    assert library is not None
    assert isinstance(errors, list)
    assert isinstance(warnings, list)
