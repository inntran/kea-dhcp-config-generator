"""Unit tests for validation.output_schema — Story 4.4."""

import copy
import re
from importlib.resources import files

import pytest

from kea_dhcp_config_generator import loader
from kea_dhcp_config_generator.builders import dhcp4 as dhcp4_builder
from kea_dhcp_config_generator.fingerprints import DHCPFingerprint
from kea_dhcp_config_generator.models import input as input_models
from kea_dhcp_config_generator.validation import output_schema
from kea_dhcp_config_generator.validation.errors import ConfigError

# ---------------------------------------------------------------------------
# AC #1: schema files ship as package data
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["kea-dhcp4.json", "kea-dhcp6.json", "VERSION"])
def test_schema_files_are_package_data(name):
    """AC #1: all three _schema files are accessible via importlib.resources."""
    path = files("kea_dhcp_config_generator") / "_schema" / name
    assert path.is_file()
    assert path.read_text(encoding="utf-8")  # non-empty


# ---------------------------------------------------------------------------
# AC #2: VERSION file format and SCHEMA_VERSION constant
# ---------------------------------------------------------------------------


def test_version_file_format():
    """AC #2: VERSION matches semver-like format."""
    raw = (files("kea_dhcp_config_generator") / "_schema" / "VERSION").read_text(encoding="utf-8")
    assert re.match(r"^\d+\.\d+\.\d+\n?$", raw), f"unexpected VERSION content: {raw!r}"


def test_schema_version_constant_matches_file():
    """AC #2, AC #10: SCHEMA_VERSION constant equals trimmed VERSION file."""
    raw = (files("kea_dhcp_config_generator") / "_schema" / "VERSION").read_text(encoding="utf-8")
    assert raw.strip() == output_schema.SCHEMA_VERSION
    assert output_schema.SCHEMA_VERSION == "3.0.4"


# ---------------------------------------------------------------------------
# AC #3: valid builder output passes
# ---------------------------------------------------------------------------


def _build_valid_dhcp4(tmp_path) -> dict:
    """Build a representative valid Dhcp4 dict from a minimal real config."""
    yaml_text = (
        "dhcp4:\n"
        "  valid-lifetime: 4000\n"
        "  subnets:\n"
        "    - subnet: 10.0.1.0/24\n"
        "      pools:\n"
        "        - range: 10.0.1.10 - 10.0.1.50\n"
    )
    cfg = tmp_path / "config.yaml"
    cfg.write_text(yaml_text)
    raw = loader.load(cfg)
    config = input_models.parse(raw)
    return dhcp4_builder.build(config, fingerprint_library=DHCPFingerprint())


def test_validate_dhcp4_accepts_valid_builder_output(tmp_path):
    """AC #3: a real builder dict passes schema validation."""
    built = _build_valid_dhcp4(tmp_path)
    assert output_schema.validate_dhcp4(built) is None


def test_validate_dhcp6_accepts_valid_builder_output():
    """DHCPv6 schema (Story 5.2/5.3) accepts a real builder dict."""
    from kea_dhcp_config_generator.builders import dhcp6 as dhcp6_builder

    cfg = input_models.GlobalConfig.model_validate(
        {
            "dhcp6": {
                "subnets": [
                    {
                        "subnet": "2001:db8:1::/64",
                        "pools": [{"pool-type": "na", "range": "auto"}],
                        "reservations": [
                            {
                                "duid": "00:03:00:01:aa:bb:cc:dd:ee:ff",
                                "ip-address": "2001:db8:1::100",
                            }
                        ],
                    }
                ],
            }
        }
    )
    built = dhcp6_builder.build(cfg, fingerprint_library=DHCPFingerprint())
    assert output_schema.validate_dhcp6(built) is None


def test_validate_dhcp6_rejects_missing_root_key():
    """Missing Dhcp6 key → ConfigError."""
    with pytest.raises(ConfigError) as excinfo:
        output_schema.validate_dhcp6({})
    assert "Dhcp6" in excinfo.value.message


def test_validate_dhcp6_rejects_wrong_type_on_known_key():
    """A nested type error renders a Dhcp6-rooted path."""
    bad = {"Dhcp6": {"subnet6": [{"subnet": "2001:db8::/48", "id": "one"}]}}
    with pytest.raises(ConfigError) as excinfo:
        output_schema.validate_dhcp6(bad)
    assert excinfo.value.yaml_path == "Dhcp6.subnet6[0].id"


# ---------------------------------------------------------------------------
# AC #4: invalid dicts raise ConfigError with field-identifying detail
# ---------------------------------------------------------------------------


def test_validate_dhcp4_rejects_wrong_type_on_known_key(tmp_path):
    """AC #4: integer field with string value → ConfigError."""
    built = _build_valid_dhcp4(tmp_path)
    bad = copy.deepcopy(built)
    bad["Dhcp4"]["valid-lifetime"] = "forever"
    with pytest.raises(ConfigError) as excinfo:
        output_schema.validate_dhcp4(bad)
    err = excinfo.value
    assert err.line is None
    assert err.suggestion is None
    assert "valid-lifetime" in err.yaml_path
    assert "valid-lifetime" in err.message
    assert err.yaml_path == "Dhcp4.valid-lifetime"


def test_validate_dhcp4_rejects_missing_root_key():
    """AC #4: missing Dhcp4 key → ConfigError."""
    with pytest.raises(ConfigError) as excinfo:
        output_schema.validate_dhcp4({})
    assert "Dhcp4" in excinfo.value.message


def test_validate_dhcp4_rejects_wrong_type_on_root():
    """AC #4: Dhcp4 must be an object, not a string."""
    with pytest.raises(ConfigError) as excinfo:
        output_schema.validate_dhcp4({"Dhcp4": "not-an-object"})
    err = excinfo.value
    assert err.yaml_path == "Dhcp4"
    assert "object" in err.message


def test_validate_dhcp4_rejects_invalid_subnet_pool_type(tmp_path):
    """AC #4: nested error path is rendered with indexes."""
    built = _build_valid_dhcp4(tmp_path)
    bad = copy.deepcopy(built)
    bad["Dhcp4"]["subnet4"][0]["pools"][0]["pool"] = 42  # should be string
    with pytest.raises(ConfigError) as excinfo:
        output_schema.validate_dhcp4(bad)
    assert excinfo.value.yaml_path == "Dhcp4.subnet4[0].pools[0].pool"


# ---------------------------------------------------------------------------
# AC #5: jsonschema_rs is the backend (not pure-Python jsonschema)
# ---------------------------------------------------------------------------


def test_validator_is_jsonschema_rs():
    """AC #5: compiled validator comes from the jsonschema_rs Rust binding."""
    assert type(output_schema._DHCP4_VALIDATOR).__module__.startswith("jsonschema_rs")
    assert "Validator" in type(output_schema._DHCP4_VALIDATOR).__name__


# ---------------------------------------------------------------------------
# AC #6: validators are cached at module scope (compiled once)
# ---------------------------------------------------------------------------


def test_validator_is_module_scope_cached(tmp_path):
    """AC #6: repeated calls reuse the same validator object."""
    before = id(output_schema._DHCP4_VALIDATOR)
    output_schema.validate_dhcp4(_build_valid_dhcp4(tmp_path))
    output_schema.validate_dhcp4(_build_valid_dhcp4(tmp_path))
    after = id(output_schema._DHCP4_VALIDATOR)
    assert before == after


# ---------------------------------------------------------------------------
# AC #10: SCHEMA_VERSION is the single source of truth for other modules
# ---------------------------------------------------------------------------


def test_schema_version_is_string():
    assert isinstance(output_schema.SCHEMA_VERSION, str)
    assert output_schema.SCHEMA_VERSION  # non-empty
