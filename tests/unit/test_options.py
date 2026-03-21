"""Unit tests for builders/options.py — option inheritance and option-data merging."""

from kea_dhcp_config_generator.builders.options import merge_option_data, resolve_options

# ---------------------------------------------------------------------------
# resolve_options — scalar field merging (most-specific-wins)
# ---------------------------------------------------------------------------


def test_resolve_options_single_layer_passthrough():
    """A single layer is returned unchanged."""
    layer = {"dns-servers": ["8.8.8.8"], "valid-lifetime": 3600}
    result = resolve_options(layer)
    assert result == {"dns-servers": ["8.8.8.8"], "valid-lifetime": 3600}


def test_resolve_options_global_only_no_subnet_override():
    """AC #1: subnet inherits global DNS when no subnet override is present."""
    global_opts = {"dns-servers": ["8.8.8.8", "8.8.4.4"]}
    subnet_opts: dict = {}
    result = resolve_options(global_opts, subnet_opts)
    assert result["dns-servers"] == ["8.8.8.8", "8.8.4.4"]


def test_resolve_options_subnet_overrides_global():
    """AC #2: subnet-level value replaces global value (most-specific-wins)."""
    global_opts = {"dns-servers": ["8.8.8.8"], "valid-lifetime": 3600}
    subnet_opts = {"dns-servers": ["1.1.1.1"]}
    result = resolve_options(global_opts, subnet_opts)
    assert result["dns-servers"] == ["1.1.1.1"]
    # Non-overridden field is still inherited
    assert result["valid-lifetime"] == 3600


def test_resolve_options_three_layers():
    """Most-specific (last) layer wins across three scopes."""
    global_opts = {"valid-lifetime": 3600, "dns-servers": ["8.8.8.8"]}
    subnet_opts = {"valid-lifetime": 7200}
    pool_opts = {"valid-lifetime": 1800}
    result = resolve_options(global_opts, subnet_opts, pool_opts)
    assert result["valid-lifetime"] == 1800
    assert result["dns-servers"] == ["8.8.8.8"]


def test_resolve_options_empty_layers():
    """Empty layers produce an empty result."""
    result = resolve_options({}, {})
    assert result == {}


def test_resolve_options_no_layers():
    """No layers produces an empty result."""
    result = resolve_options()
    assert result == {}


def test_resolve_options_does_not_mutate_input():
    """resolve_options must not modify the input layer dicts."""
    base = {"dns-servers": ["8.8.8.8"]}
    override = {"dns-servers": ["1.1.1.1"]}
    base_copy = dict(base)
    override_copy = dict(override)
    resolve_options(base, override)
    assert base == base_copy
    assert override == override_copy


def test_resolve_options_does_not_mutate_nested_dicts():
    """resolve_options must not mutate nested dict objects inside input layers (F1)."""
    import copy

    base = {"opts": {"a": 1, "b": 2}}
    override = {"opts": {"a": 99}}
    base_before = copy.deepcopy(base)
    override_before = copy.deepcopy(override)
    result = resolve_options(base, override)
    # Inputs unchanged
    assert base == base_before
    assert override == override_before
    # Result is correctly merged
    assert result == {"opts": {"a": 99, "b": 2}}


# ---------------------------------------------------------------------------
# merge_option_data — merge-by-name semantics (AC #3)
# ---------------------------------------------------------------------------


def test_merge_option_data_override_replaces_base():
    """AC #3: override entry with same name replaces base entry (not appended)."""
    base = [{"name": "domain-name-servers", "data": "8.8.8.8"}]
    override = [{"name": "domain-name-servers", "data": "1.1.1.1"}]
    result = merge_option_data(base, override)
    assert len(result) == 1
    assert result[0]["data"] == "1.1.1.1"


def test_merge_option_data_new_override_entry_added():
    """Override entry with a new name is added to the result."""
    base = [{"name": "domain-name-servers", "data": "8.8.8.8"}]
    override = [{"name": "routers", "data": "10.0.1.1"}]
    result = merge_option_data(base, override)
    names = {e["name"] for e in result}
    assert names == {"domain-name-servers", "routers"}


def test_merge_option_data_base_only_no_override():
    """With no override entries, base is returned as-is."""
    base = [{"name": "domain-name-servers", "data": "8.8.8.8"}]
    result = merge_option_data(base, [])
    assert result == base


def test_merge_option_data_override_only_no_base():
    """With no base entries, override is returned as-is."""
    override = [{"name": "domain-name-servers", "data": "1.1.1.1"}]
    result = merge_option_data([], override)
    assert result == override


def test_merge_option_data_multiple_entries_partial_overlap():
    """Only the overlapping entry is replaced; non-overlapping entries are preserved."""
    base = [
        {"name": "domain-name-servers", "data": "8.8.8.8"},
        {"name": "domain-name", "data": "example.com"},
    ]
    override = [{"name": "domain-name-servers", "data": "1.1.1.1"}]
    result = merge_option_data(base, override)
    assert len(result) == 2
    by_name = {e["name"]: e for e in result}
    assert by_name["domain-name-servers"]["data"] == "1.1.1.1"
    assert by_name["domain-name"]["data"] == "example.com"


def test_merge_option_data_both_empty():
    """Two empty lists produce an empty result."""
    assert merge_option_data([], []) == []


def test_merge_option_data_missing_name_in_base_raises():
    """merge_option_data raises ValueError for base entries missing 'name' (F4)."""
    import pytest

    base = [{"code": 6, "data": "8.8.8.8"}]  # no 'name' key
    with pytest.raises(ValueError, match="missing required 'name' key"):
        merge_option_data(base, [])


def test_merge_option_data_missing_name_in_override_raises():
    """merge_option_data raises ValueError for override entries missing 'name' (F4)."""
    import pytest

    override = [{"code": 6, "data": "1.1.1.1"}]  # no 'name' key
    with pytest.raises(ValueError, match="missing required 'name' key"):
        merge_option_data([], override)
