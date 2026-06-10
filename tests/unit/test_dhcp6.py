"""Unit tests for builders/dhcp6.py — DHCPv6 Config Builder (Story 5.2).

Tests cover the acceptance criteria:
    AC #1 — dhcp6 with NA pools → Dhcp6.subnet6 with subnet/pools/id/option-data
    AC #2 — NA pool range: auto resolved via IPv6 range math
    AC #3 — PD pool → {"prefix", "prefix-len", "delegated-len"} (Kea PD format)
    AC #4 — DUID reservations → {"duid", "ip-addresses"} (not hw-address)
    AC #5 — option inheritance resolved via builders.options
    AC #7 — auto-assigned subnet IDs from 1 (independent of DHCPv4)
"""

import pytest

from kea_dhcp_config_generator.builders.dhcp6 import build
from kea_dhcp_config_generator.fingerprints import DHCPFingerprint
from kea_dhcp_config_generator.models.input import GlobalConfig
from kea_dhcp_config_generator.validation.errors import KeaConfigError


def _cfg(dhcp6_dict: dict) -> GlobalConfig:
    if any(key in dhcp6_dict for key in ("dhcp4", "dhcp6", "fingerprint_library_version")):
        return GlobalConfig.model_validate(dhcp6_dict)
    root = {"dhcp6": {k: v for k, v in dhcp6_dict.items() if k != "option_profiles"}}
    if "option_profiles" in dhcp6_dict:
        root["option_profiles"] = dhcp6_dict["option_profiles"]
    return GlobalConfig.model_validate(root)


def _na_pool(rng: str = "auto", **extra) -> dict:
    return {"pool-type": "na", "range": rng, **extra}


# ---------------------------------------------------------------------------
# AC #1 — top-level shape
# ---------------------------------------------------------------------------


def test_build_returns_dhcp6_key():
    config = _cfg({"subnets": [{"subnet": "2001:db8:1::/64"}]})
    result = build(config)
    assert "Dhcp6" in result
    assert "subnet6" in result["Dhcp6"]


def test_subnet_has_id_subnet_and_pools():
    config = _cfg(
        {
            "subnets": [
                {
                    "subnet": "2001:db8:1::/64",
                    "pools": [_na_pool("2001:db8:1::100 - 2001:db8:1::200")],
                }
            ],
        }
    )
    subnet = build(config)["Dhcp6"]["subnet6"][0]
    assert subnet["id"] == 1
    assert subnet["subnet"] == "2001:db8:1::/64"
    assert subnet["pools"] == [{"pool": "2001:db8:1::100 - 2001:db8:1::200"}]


def test_subnet_cidr_is_canonical_string():
    config = _cfg({"subnets": [{"subnet": "2001:db8:1::1/64"}]})  # host bits
    subnet = build(config)["Dhcp6"]["subnet6"][0]
    assert subnet["subnet"] == "2001:db8:1::/64"
    assert isinstance(subnet["subnet"], str)


def test_build_raises_without_dhcp6():
    config = GlobalConfig.model_validate({"dhcp4": {"subnets": [{"subnet": "10.0.1.0/24"}]}})
    with pytest.raises(ValueError, match="no dhcp6 section"):
        build(config)


# ---------------------------------------------------------------------------
# AC #2 — NA pool range: auto
# ---------------------------------------------------------------------------


def test_na_pool_auto_range():
    config = _cfg({"subnets": [{"subnet": "2001:db8:1::/64", "pools": [_na_pool("auto")]}]})
    subnet = build(config)["Dhcp6"]["subnet6"][0]
    # Uses the shared calculate_pool_range; last usable is before the all-ones host.
    assert subnet["pools"] == [{"pool": "2001:db8:1::1 - 2001:db8:1:0:ffff:ffff:ffff:fffe"}]


# ---------------------------------------------------------------------------
# AC #3 — PD pool format
# ---------------------------------------------------------------------------


def test_pd_pool_format():
    config = _cfg(
        {
            "subnets": [
                {
                    "subnet": "2001:db8::/48",
                    "pools": [
                        {
                            "pool-type": "pd",
                            "prefix": "2001:db8:1::",
                            "prefix-len": 48,
                            "delegated-len": 64,
                        }
                    ],
                }
            ],
        }
    )
    subnet = build(config)["Dhcp6"]["subnet6"][0]
    assert subnet["pd-pools"] == [{"prefix": "2001:db8:1::", "prefix-len": 48, "delegated-len": 64}]
    assert "pools" not in subnet  # only PD here


def test_mixed_na_and_pd_pools_split_into_two_keys():
    config = _cfg(
        {
            "subnets": [
                {
                    "subnet": "2001:db8::/48",
                    "pools": [
                        _na_pool("auto"),
                        {
                            "pool-type": "pd",
                            "prefix": "2001:db8:1::",
                            "prefix-len": 48,
                            "delegated-len": 64,
                        },
                    ],
                }
            ],
        }
    )
    subnet = build(config)["Dhcp6"]["subnet6"][0]
    assert "pools" in subnet
    assert "pd-pools" in subnet
    # Subnet key order: pools before pd-pools (Kea-natural)
    keys = list(subnet.keys())
    assert keys.index("pools") < keys.index("pd-pools")


# ---------------------------------------------------------------------------
# AC #4 — DUID reservations
# ---------------------------------------------------------------------------


def test_duid_reservation_format():
    config = _cfg(
        {
            "subnets": [
                {
                    "subnet": "2001:db8:1::/64",
                    "reservations": [
                        {
                            "duid": "00:01:00:01:52:13:52:13:08:00:27:58:f1:e8",
                            "ip-address": "2001:db8:1::100",
                            "hostname": "host6",
                        }
                    ],
                }
            ],
        }
    )
    res = build(config)["Dhcp6"]["subnet6"][0]["reservations"][0]
    assert res["duid"] == "00:01:00:01:52:13:52:13:08:00:27:58:f1:e8"
    assert res["ip-addresses"] == ["2001:db8:1::100"]
    assert res["hostname"] == "host6"
    assert "hw-address" not in res
    assert "ip-address" not in res


def test_duid_reservation_without_ip_omits_ip_addresses():
    config = _cfg(
        {
            "subnets": [
                {
                    "subnet": "2001:db8:1::/64",
                    "reservations": [{"duid": "00:03:00:01:aa:bb:cc:dd:ee:ff"}],
                }
            ],
        }
    )
    res = build(config)["Dhcp6"]["subnet6"][0]["reservations"][0]
    assert res == {"duid": "00:03:00:01:aa:bb:cc:dd:ee:ff"}


# ---------------------------------------------------------------------------
# AC #5 — option inheritance / scalar mapping
# ---------------------------------------------------------------------------


def test_global_dns_servers_map_to_dhcp6_option():
    config = _cfg({"dns-servers": ["2001:4860:4860::8888"], "subnets": []})
    dhcp6 = build(config)["Dhcp6"]
    assert dhcp6["option-data"] == [{"name": "dns-servers", "data": "2001:4860:4860::8888"}]


def test_subnet_option_data_emitted_at_subnet_scope():
    config = _cfg(
        {
            "dns-servers": ["2001:4860:4860::8888"],
            "subnets": [{"subnet": "2001:db8:1::/64", "dns-servers": ["2001:db8::53"]}],
        }
    )
    subnet = build(config)["Dhcp6"]["subnet6"][0]
    assert subnet["option-data"] == [{"name": "dns-servers", "data": "2001:db8::53"}]


def test_explicit_option_data_overrides_scalar_by_name():
    config = _cfg(
        {
            "subnets": [
                {
                    "subnet": "2001:db8:1::/64",
                    "dns-servers": ["2001:db8::53"],
                    "option-data": [{"name": "dns-servers", "data": "2001:db8::99"}],
                }
            ],
        }
    )
    subnet = build(config)["Dhcp6"]["subnet6"][0]
    assert subnet["option-data"] == [{"name": "dns-servers", "data": "2001:db8::99"}]


# ---------------------------------------------------------------------------
# Option profile inheritance
# ---------------------------------------------------------------------------


def test_option_profile_supplies_subnet_option_data_and_timer():
    config = _cfg(
        {
            "dns-servers": ["2001:4860:4860::8888"],
            "option_profiles": {
                "corporate": {
                    "dns-servers": ["2001:db8::53"],
                    "valid-lifetime": 7200,
                    "preferred-lifetime": 3600,
                }
            },
            "subnets": [{"subnet": "2001:db8:1::/64", "option_profile": "corporate"}],
        }
    )
    subnet = build(config)["Dhcp6"]["subnet6"][0]
    assert subnet["option-data"] == [{"name": "dns-servers", "data": "2001:db8::53"}]
    assert subnet["valid-lifetime"] == 7200
    assert subnet["preferred-lifetime"] == 3600


def test_inline_subnet_values_override_option_profile():
    config = _cfg(
        {
            "option_profiles": {
                "corporate": {
                    "dns-servers": ["2001:db8::53"],
                    "valid-lifetime": 7200,
                }
            },
            "subnets": [
                {
                    "subnet": "2001:db8:1::/64",
                    "option_profile": "corporate",
                    "dns-servers": ["2001:db8::99"],
                    "valid-lifetime": 3600,
                }
            ],
        }
    )
    subnet = build(config)["Dhcp6"]["subnet6"][0]
    assert subnet["option-data"] == [{"name": "dns-servers", "data": "2001:db8::99"}]
    assert subnet["valid-lifetime"] == 3600


def test_unknown_option_profile_raises():
    config = _cfg(
        {
            "option_profiles": {"corporate": {"dns-servers": ["2001:db8::53"]}},
            "subnets": [{"subnet": "2001:db8:1::/64", "option_profile": "corp"}],
        }
    )
    with pytest.raises(KeaConfigError, match="corp"):
        build(config)


# ---------------------------------------------------------------------------
# AC #6 (5.3 overlap) — pool client-classes list form + top-level entries
# ---------------------------------------------------------------------------


def test_pool_client_class_list_form_and_top_level_entry():
    config = _cfg(
        {
            "subnets": [
                {
                    "subnet": "2001:db8:1::/64",
                    "pools": [_na_pool("auto", **{"client-class": "iOS_17"})],
                }
            ],
        }
    )
    lib = DHCPFingerprint()
    # Inject a known rule so a top-level entry is emitted.
    lib._rules["iOS_17"] = {"name": "iOS_17", "test": "substring(option[60].hex,0,3) == 'iOS'"}
    dhcp6 = build(config, fingerprint_library=lib)["Dhcp6"]
    assert dhcp6["subnet6"][0]["pools"][0]["client-classes"] == ["iOS_17"]
    names = [c["name"] for c in dhcp6["client-classes"]]
    assert "iOS_17" in names


# ---------------------------------------------------------------------------
# AC #7 — auto subnet IDs
# ---------------------------------------------------------------------------


def test_auto_subnet_ids_sequential_from_one():
    config = _cfg(
        {
            "subnets": [
                {"subnet": "2001:db8:1::/64"},
                {"subnet": "2001:db8:2::/64"},
                {"subnet": "2001:db8:3::/64"},
            ],
        }
    )
    ids = [s["id"] for s in build(config)["Dhcp6"]["subnet6"]]
    assert ids == [1, 2, 3]


def test_explicit_ids_preserved():
    config = _cfg(
        {
            "subnets": [
                {"subnet": "2001:db8:1::/64", "id": 10},
                {"subnet": "2001:db8:2::/64", "id": 20},
            ],
        }
    )
    ids = [s["id"] for s in build(config)["Dhcp6"]["subnet6"]]
    assert ids == [10, 20]


def test_mixed_explicit_and_auto_ids_raises():
    config = _cfg(
        {
            "subnets": [
                {"subnet": "2001:db8:1::/64", "id": 10},
                {"subnet": "2001:db8:2::/64"},
            ],
        }
    )
    with pytest.raises(ValueError, match="Mixed subnet ID"):
        build(config)


def test_dhcp6_subnet_ids_independent_of_dhcp4():
    config = GlobalConfig.model_validate(
        {
            "dhcp4": {"subnets": [{"subnet": "10.0.1.0/24"}, {"subnet": "10.0.2.0/24"}]},
            "dhcp6": {"subnets": [{"subnet": "2001:db8:1::/64"}]},
        }
    )
    ids = [s["id"] for s in build(config)["Dhcp6"]["subnet6"]]
    assert ids == [1]  # starts at 1 regardless of DHCPv4 having 2 subnets
