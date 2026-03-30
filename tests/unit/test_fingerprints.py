"""Unit tests for fingerprints/__init__.py — DHCPFingerprint catalog."""

import pytest

from kea_dhcp_config_generator.fingerprints import DHCPFingerprint


@pytest.fixture(scope="module")
def lib() -> DHCPFingerprint:
    """Shared DHCPFingerprint instance — load once per module for speed."""
    return DHCPFingerprint()


def test_instantiation_loads_at_least_ten_rules(lib):
    """AC #1 & #3: ≥10 rules loaded from rule files."""
    assert len(lib._rules) >= 10


def test_lookup_existing_rule_returns_dict(lib):
    """AC #2: lookup returns a dict (not None) for a known rule."""
    result = lib.lookup("iOS_14_17")
    assert result is not None
    assert isinstance(result, dict)


def test_lookup_result_has_required_keys(lib):
    """AC #2: returned dict contains all required fields."""
    result = lib.lookup("iOS_14_17")
    assert result["name"] == "iOS_14_17"
    assert "kea_version_min" in result
    assert "kea_version_max" in result
    assert "source" in result
    assert "validated_against" in result


def test_lookup_result_has_exactly_one_expression_key(lib):
    """AC #2 & #4: exactly one of test or template-test, never both."""
    result = lib.lookup("iOS_14_17")
    assert ("test" in result) != ("template-test" in result), (
        "Rule must have exactly one of 'test' or 'template-test'"
    )


def test_all_rules_have_exactly_one_expression_key(lib):
    """AC #4: invariant holds across all rules in the catalog."""
    for name, rule in lib._rules.items():
        has_test = "test" in rule
        has_template = "template-test" in rule
        assert has_test != has_template, (
            f"Rule '{name}': must have exactly one of 'test' or 'template-test', "
            f"got test={has_test}, template-test={has_template}"
        )


def test_lookup_nonexistent_returns_none(lib):
    """AC #5: lookup of unknown name returns None (never raises)."""
    assert lib.lookup("NonExistentRule_XYZ") is None


def test_lookup_nonexistent_does_not_raise(lib):
    """AC #5: no exception raised for missing rule."""
    try:
        lib.lookup("CompletelyFakeRule")
    except Exception as exc:  # noqa: BLE001
        pytest.fail(f"lookup() raised unexpectedly: {exc}")


def test_category_ios_covered(lib):
    """AC #3: ≥1 iOS rule exists."""
    ios_rules = [r for r in lib._rules if r.startswith("iOS_")]
    assert len(ios_rules) >= 1, "No iOS rules found"


def test_category_windows_covered(lib):
    """AC #3: ≥1 Windows rule exists."""
    win_rules = [r for r in lib._rules if r.startswith("Windows_")]
    assert len(win_rules) >= 1, "No Windows rules found"


def test_category_macos_covered(lib):
    """AC #3: ≥1 macOS rule exists."""
    mac_rules = [r for r in lib._rules if r.startswith("macOS")]
    assert len(mac_rules) >= 1, "No macOS rules found"


def test_category_android_covered(lib):
    """AC #3: ≥1 Android rule exists."""
    android_rules = [r for r in lib._rules if r.startswith("Android_")]
    assert len(android_rules) >= 1, "No Android rules found"


def test_category_routers_covered(lib):
    """AC #3: ≥1 home router rule exists."""
    router_rules = [r for r in lib._rules if "Router" in r or "router" in r.lower()]
    assert len(router_rules) >= 1, "No router rules found"


def test_category_iot_covered(lib):
    """AC #3: ≥1 IoT rule exists."""
    iot_rules = [r for r in lib._rules if "IoT" in r or "iot" in r.lower()]
    assert len(iot_rules) >= 1, "No IoT rules found"


def test_file_order_android_before_windows(lib):
    """AC #1: alphabetical file order preserved — Android rules appear before Windows."""
    rule_names = list(lib._rules.keys())
    android_indices = [i for i, n in enumerate(rule_names) if n.startswith("Android_")]
    windows_indices = [i for i, n in enumerate(rule_names) if n.startswith("Windows_")]
    assert android_indices and windows_indices
    assert min(android_indices) < min(windows_indices), (
        "android.yaml rules must appear before windows.yaml rules (alphabetical file order)"
    )


# ---- Story 3.2 tests ----

def test_global_config_has_fingerprint_library_version_field():
    """AC #1: GlobalConfig.fingerprint_library_version is an optional string field."""
    from kea_dhcp_config_generator.models.input import GlobalConfig
    fields = GlobalConfig.model_fields
    assert "fingerprint_library_version" in fields
    field = fields["fingerprint_library_version"]
    assert not field.is_required()  # optional


def test_version_pinning_match_no_warnings():
    """AC #2: matching version → no warnings on lib.warnings."""
    import importlib.metadata
    installed = importlib.metadata.version("kea-dhcp-config-generator")
    lib_pinned = DHCPFingerprint(pinned_version=installed)
    assert lib_pinned.warnings == []


def test_version_pinning_mismatch_emits_warning():
    """AC #3: mismatched version → one ConfigWarning on lib.warnings."""
    lib_bad = DHCPFingerprint(pinned_version="9.9.9")
    assert len(lib_bad.warnings) == 1
    warning = lib_bad.warnings[0]
    assert "9.9.9" in warning.message
    assert warning.yaml_path == "fingerprint_library_version"


def test_version_pinning_mismatch_message_contains_installed_version():
    """AC #3: warning message includes both pinned and installed version strings."""
    import importlib.metadata
    installed = importlib.metadata.version("kea-dhcp-config-generator")
    lib_bad = DHCPFingerprint(pinned_version="9.9.9")
    assert installed in lib_bad.warnings[0].message


def test_fuzzy_match_close_name_returns_suggestion(lib):
    """AC #4: fuzzy_match on a near-miss name returns the close rule."""
    result = lib.fuzzy_match("iOS_14_16")
    assert "iOS_14_17" in result


def test_fuzzy_match_transposition_typo(lib):
    """AC #5: fuzzy_match catches character transposition typos."""
    result = lib.fuzzy_match("Andriod_12_14")
    assert "Android_12_14" in result


def test_fuzzy_match_unrelated_returns_empty(lib):
    """AC #6: fuzzy_match returns empty list for completely unrelated names."""
    result = lib.fuzzy_match("CompletelyUnrelated")
    assert result == []


def test_fuzzy_match_returns_list(lib):
    """Type contract: fuzzy_match always returns a list (never None)."""
    result = lib.fuzzy_match("any_name")
    assert isinstance(result, list)
