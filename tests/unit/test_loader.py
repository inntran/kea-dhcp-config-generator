"""Unit tests for loader.py."""

import pytest
from ruamel.yaml.comments import CommentedMap

from kea_dhcp_config_generator import loader
from kea_dhcp_config_generator.validation.errors import KeaConfigError


def test_load_returns_commented_map(tmp_path):
    f = tmp_path / "config.yaml"
    f.write_text("subnets: []\n")
    result = loader.load(f)
    assert isinstance(result, CommentedMap)


def test_lc_metadata_accessible(tmp_path):
    f = tmp_path / "config.yaml"
    f.write_text("subnets: []\nvalid-lifetime: 3600\nnested:\n  key: value\n")
    data = loader.load(f)
    # .lc.key() returns (line, col); 0-indexed
    line, _ = data.lc.key("subnets")
    assert line == 0
    line2, _ = data.lc.key("valid-lifetime")
    assert line2 == 1
    # AC 1: .lc is accessible on nested CommentedMap nodes, not just the root
    nested = data["nested"]
    assert isinstance(nested, CommentedMap)
    line3, _ = nested.lc.key("key")
    assert line3 == 3


def test_anchor_resolved(tmp_path):
    f = tmp_path / "config.yaml"
    f.write_text(
        "defaults: &defs\n  valid-lifetime: 3600\nsubnet:\n  <<: *defs\n  subnet: 10.0.0.0/8\n"
    )
    data = loader.load(f)
    assert data["subnet"]["valid-lifetime"] == 3600
    # AC 2: .lc metadata accessible after anchor resolution
    line, _ = data.lc.key("subnet")
    assert line == 2  # "subnet:" is on line 2 (0-indexed)


def test_merge_keys_resolved(tmp_path):
    f = tmp_path / "config.yaml"
    f.write_text(
        "base: &base\n"
        "  dns-servers:\n"
        "    - 8.8.8.8\n"
        "  valid-lifetime: 7200\n"
        "subnet:\n"
        "  <<: *base\n"
        "  subnet: 192.168.1.0/24\n"
    )
    data = loader.load(f)
    assert data["subnet"]["valid-lifetime"] == 7200
    assert data["subnet"]["dns-servers"] == ["8.8.8.8"]
    # AC 2: .lc metadata accessible after merge key resolution
    line, _ = data.lc.key("subnet")
    assert line == 4  # "subnet:" is on line 4 (0-indexed)


def test_mac_address_loaded_as_string(tmp_path):
    f = tmp_path / "config.yaml"
    f.write_text("hw-address: aa:bb:cc:dd:ee:ff\n")
    data = loader.load(f)
    assert isinstance(data["hw-address"], str)
    assert data["hw-address"] == "aa:bb:cc:dd:ee:ff"


def test_invalid_yaml_raises_kea_config_error(tmp_path):
    f = tmp_path / "bad.yaml"
    f.write_text("key: [unclosed bracket\n")
    with pytest.raises(KeaConfigError, match="YAML syntax error"):
        loader.load(f)


def test_nonexistent_file_raises_kea_config_error(tmp_path):
    missing = tmp_path / "does_not_exist.yaml"
    with pytest.raises(KeaConfigError, match="not found"):
        loader.load(missing)


def test_non_mapping_top_level_raises_kea_config_error(tmp_path):
    f = tmp_path / "list.yaml"
    f.write_text("- item1\n- item2\n")
    with pytest.raises(KeaConfigError, match="mapping"):
        loader.load(f)
