"""Unit tests for builders/dhcp4.py — Core DHCPv4 Config Builder (Story 2.2).

Tests cover all 7 acceptance criteria:
    AC #1 — global DHCPv4 parameters appear at Dhcp4 top level with Kea hyphenated keys
    AC #2 — each subnet produces a correct subnet4 array entry
    AC #3 — subnets without explicit id receive sequential auto-assigned IDs from 1
    AC #4 — subnets with explicit id preserve that id in output
    AC #5 — pools with range: "auto" are resolved to explicit "start - end" strings
    AC #6 — pool client-class maps to "client-classes": [value] (list); never singular key
    AC #7 — key order matches Kea documentation natural ordering
"""

import pytest

from kea_dhcp_config_generator.builders.dhcp4 import build
from kea_dhcp_config_generator.models.input import GlobalConfig

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cfg(dhcp4_dict: dict) -> GlobalConfig:
    """Build a GlobalConfig from a raw dhcp4 dict."""
    return GlobalConfig.model_validate({"dhcp4": dhcp4_dict})


def _minimal_subnet(cidr: str = "10.0.1.0/24", **extra) -> dict:
    """Return a minimal valid subnet dict."""
    return {"subnet": cidr, **extra}


# ---------------------------------------------------------------------------
# AC #1 — Global DHCPv4 parameters appear at Dhcp4 top level
# ---------------------------------------------------------------------------


def test_global_timers_appear_with_hyphenated_keys():
    """AC #1: valid-lifetime, renew-timer, rebind-timer use Kea hyphenated keys."""
    config = _cfg({
        "valid-lifetime": 3600,
        "renew-timer": 900,
        "rebind-timer": 1800,
        "subnets": [_minimal_subnet()],
    })
    result = build(config)
    dhcp4 = result["Dhcp4"]

    assert dhcp4["valid-lifetime"] == 3600
    assert dhcp4["renew-timer"] == 900
    assert dhcp4["rebind-timer"] == 1800


def test_global_dns_servers_appear_in_option_data():
    """AC #1: dns-servers is converted to a domain-name-servers option-data entry."""
    config = _cfg({
        "dns-servers": ["8.8.8.8", "8.8.4.4"],
        "subnets": [_minimal_subnet()],
    })
    result = build(config)
    option_data = result["Dhcp4"]["option-data"]

    assert {"name": "domain-name-servers", "data": "8.8.8.8, 8.8.4.4"} in option_data


def test_global_domain_name_appears_in_option_data():
    """AC #1: domain-name is converted to a domain-name option-data entry."""
    config = _cfg({
        "domain-name": "example.com",
        "subnets": [_minimal_subnet()],
    })
    result = build(config)
    option_data = result["Dhcp4"]["option-data"]

    assert {"name": "domain-name", "data": "example.com"} in option_data


def test_global_ntp_servers_appear_in_option_data():
    """AC #1: ntp-servers is converted to a ntp-servers option-data entry."""
    config = _cfg({
        "ntp-servers": ["10.0.0.1"],
        "subnets": [_minimal_subnet()],
    })
    result = build(config)
    option_data = result["Dhcp4"]["option-data"]

    assert {"name": "ntp-servers", "data": "10.0.0.1"} in option_data


def test_global_routers_appear_in_option_data():
    """AC #1: routers is converted to a routers option-data entry."""
    config = _cfg({
        "routers": ["10.0.1.1"],
        "subnets": [_minimal_subnet()],
    })
    result = build(config)
    option_data = result["Dhcp4"]["option-data"]

    assert {"name": "routers", "data": "10.0.1.1"} in option_data


def test_global_all_params_combined():
    """AC #1: all global parameters together produce correct hyphenated keys + option-data."""
    config = _cfg({
        "valid-lifetime": 86400,
        "renew-timer": 21600,
        "rebind-timer": 43200,
        "dns-servers": ["1.1.1.1"],
        "domain-name": "corp.internal",
        "subnets": [_minimal_subnet()],
    })
    result = build(config)
    dhcp4 = result["Dhcp4"]

    assert dhcp4["valid-lifetime"] == 86400
    assert dhcp4["renew-timer"] == 21600
    assert dhcp4["rebind-timer"] == 43200
    names = [e["name"] for e in dhcp4["option-data"]]
    assert "domain-name-servers" in names
    assert "domain-name" in names


def test_global_none_timers_omitted():
    """AC #1: timers that are not set are omitted from the Dhcp4 dict."""
    config = _cfg({
        "valid-lifetime": 3600,
        "subnets": [_minimal_subnet()],
    })
    result = build(config)
    dhcp4 = result["Dhcp4"]

    assert "valid-lifetime" in dhcp4
    assert "renew-timer" not in dhcp4
    assert "rebind-timer" not in dhcp4


def test_no_option_data_key_when_empty():
    """AC #1: option-data key is omitted when no global options are defined."""
    config = _cfg({"subnets": [_minimal_subnet()]})
    result = build(config)
    dhcp4 = result["Dhcp4"]

    assert "option-data" not in dhcp4


# ---------------------------------------------------------------------------
# AC #2 — Each subnet produces a correct subnet4 entry
# ---------------------------------------------------------------------------


def test_subnet4_array_has_correct_fields():
    """AC #2: subnet4 entry contains subnet, id, pools, and option-data."""
    config = _cfg({
        "subnets": [
            {
                "subnet": "10.0.1.0/24",
                "dns-servers": ["8.8.8.8"],
                "pools": [{"range": "10.0.1.10 - 10.0.1.100"}],
            }
        ]
    })
    result = build(config)
    subnet4 = result["Dhcp4"]["subnet4"]

    assert len(subnet4) == 1
    entry = subnet4[0]
    assert entry["subnet"] == "10.0.1.0/24"
    assert "id" in entry
    assert "pools" in entry
    assert "option-data" in entry


def test_subnet4_pool_range_preserved():
    """AC #2: explicit pool range appears verbatim in the pools entry."""
    config = _cfg({
        "subnets": [
            {
                "subnet": "192.168.1.0/24",
                "pools": [{"range": "192.168.1.10 - 192.168.1.200"}],
            }
        ]
    })
    result = build(config)
    pool_entry = result["Dhcp4"]["subnet4"][0]["pools"][0]

    assert pool_entry["pool"] == "192.168.1.10 - 192.168.1.200"


def test_no_subnet4_key_when_no_subnets():
    """AC #2: subnet4 key is omitted when dhcp4 has no subnets."""
    config = _cfg({
        "valid-lifetime": 3600,
        "subnets": [],
    })
    result = build(config)

    assert "subnet4" not in result["Dhcp4"]


def test_multiple_subnets_all_appear():
    """AC #2: all subnets appear in subnet4 in YAML order."""
    config = _cfg({
        "subnets": [
            _minimal_subnet("10.0.1.0/24"),
            _minimal_subnet("10.0.2.0/24"),
            _minimal_subnet("10.0.3.0/24"),
        ]
    })
    result = build(config)
    subnets = result["Dhcp4"]["subnet4"]

    assert len(subnets) == 3
    assert subnets[0]["subnet"] == "10.0.1.0/24"
    assert subnets[1]["subnet"] == "10.0.2.0/24"
    assert subnets[2]["subnet"] == "10.0.3.0/24"


# ---------------------------------------------------------------------------
# AC #3 — Auto-ID assignment: sequential from 1, YAML order
# ---------------------------------------------------------------------------


def test_auto_id_assignment_sequential_from_one():
    """AC #3: subnets without explicit id receive sequential IDs starting from 1."""
    config = _cfg({
        "subnets": [
            _minimal_subnet("10.0.1.0/24"),
            _minimal_subnet("10.0.2.0/24"),
            _minimal_subnet("10.0.3.0/24"),
        ]
    })
    result = build(config)
    subnets = result["Dhcp4"]["subnet4"]

    assert subnets[0]["id"] == 1
    assert subnets[1]["id"] == 2
    assert subnets[2]["id"] == 3


def test_mixed_subnet_ids_raises():
    """AC #3: mixing explicit and auto IDs raises ValueError (all-or-none rule)."""
    config = _cfg({
        "subnets": [
            _minimal_subnet("10.0.1.0/24"),            # no id
            {**_minimal_subnet("10.0.2.0/24"), "id": 99},  # explicit
            _minimal_subnet("10.0.3.0/24"),            # no id
        ]
    })
    with pytest.raises(ValueError, match="Mixed subnet ID assignment"):
        build(config)


def test_all_explicit_ids_accepted():
    """AC #3/#4: when all subnets have explicit IDs, each is used as-is."""
    config = _cfg({
        "subnets": [
            {**_minimal_subnet("10.0.1.0/24"), "id": 10},
            {**_minimal_subnet("10.0.2.0/24"), "id": 20},
            {**_minimal_subnet("10.0.3.0/24"), "id": 30},
        ]
    })
    result = build(config)
    subnets = result["Dhcp4"]["subnet4"]

    assert subnets[0]["id"] == 10
    assert subnets[1]["id"] == 20
    assert subnets[2]["id"] == 30


# ---------------------------------------------------------------------------
# AC #4 — Explicit subnet ID is preserved
# ---------------------------------------------------------------------------


def test_explicit_subnet_id_preserved():
    """AC #4: a subnet with id: 42 uses id 42 in the output."""
    config = _cfg({
        "subnets": [
            {**_minimal_subnet("10.0.5.0/24"), "id": 42},
        ]
    })
    result = build(config)
    assert result["Dhcp4"]["subnet4"][0]["id"] == 42


def test_explicit_id_one():
    """AC #4: explicit id=1 is preserved even though it matches the auto-seq default."""
    config = _cfg({
        "subnets": [
            {**_minimal_subnet("10.0.1.0/24"), "id": 1},
        ]
    })
    result = build(config)
    assert result["Dhcp4"]["subnet4"][0]["id"] == 1


# ---------------------------------------------------------------------------
# AC #5 — Auto pool range resolved from subnet CIDR
# ---------------------------------------------------------------------------


def test_auto_pool_range_slash24_no_skip():
    """AC #5: 'auto' range on /24 resolves to .1 - .254."""
    config = _cfg({
        "subnets": [
            {
                "subnet": "10.0.1.0/24",
                "pools": [{"range": "auto"}],
            }
        ]
    })
    result = build(config)
    pool = result["Dhcp4"]["subnet4"][0]["pools"][0]

    assert pool["pool"] == "10.0.1.1 - 10.0.1.254"


def test_auto_pool_range_with_skip():
    """AC #5: 'auto' with skip-start/skip-end adjusts the pool boundaries."""
    config = _cfg({
        "subnets": [
            {
                "subnet": "10.0.1.0/24",
                "pools": [{"range": "auto", "skip-start": 5, "skip-end": 2}],
            }
        ]
    })
    result = build(config)
    pool = result["Dhcp4"]["subnet4"][0]["pools"][0]

    assert pool["pool"] == "10.0.1.6 - 10.0.1.252"


def test_explicit_pool_range_passed_through():
    """AC #5: explicit range string is parsed and emitted verbatim as 'start - end'."""
    config = _cfg({
        "subnets": [
            {
                "subnet": "10.0.1.0/24",
                "pools": [{"range": "10.0.1.10 - 10.0.1.100"}],
            }
        ]
    })
    result = build(config)
    pool = result["Dhcp4"]["subnet4"][0]["pools"][0]

    assert pool["pool"] == "10.0.1.10 - 10.0.1.100"


def test_multiple_pools_all_resolved():
    """AC #5: multiple pools in a subnet are each resolved independently."""
    config = _cfg({
        "subnets": [
            {
                "subnet": "10.0.1.0/24",
                "pools": [
                    {"range": "10.0.1.10 - 10.0.1.50"},
                    {"range": "auto"},
                ],
            }
        ]
    })
    result = build(config)
    pools = result["Dhcp4"]["subnet4"][0]["pools"]

    assert len(pools) == 2
    assert pools[0]["pool"] == "10.0.1.10 - 10.0.1.50"
    assert pools[1]["pool"] == "10.0.1.1 - 10.0.1.254"


# ---------------------------------------------------------------------------
# AC #6 — Pool client-class maps to "client-classes" list form
# ---------------------------------------------------------------------------


def test_pool_client_class_emitted_as_list():
    """AC #6: client-class on a pool becomes "client-classes": ["value"] (list)."""
    config = _cfg({
        "subnets": [
            {
                "subnet": "10.0.1.0/24",
                "pools": [{"range": "auto", "client-class": "Windows_11"}],
            }
        ]
    })
    result = build(config)
    pool = result["Dhcp4"]["subnet4"][0]["pools"][0]

    assert pool["client-classes"] == ["Windows_11"]


def test_pool_client_class_singular_key_never_emitted():
    """AC #6: deprecated singular 'client-class' key is never emitted on pools."""
    config = _cfg({
        "subnets": [
            {
                "subnet": "10.0.1.0/24",
                "pools": [{"range": "auto", "client-class": "iOS_17"}],
            }
        ]
    })
    result = build(config)
    pool = result["Dhcp4"]["subnet4"][0]["pools"][0]

    assert "client-class" not in pool
    assert pool["client-classes"] == ["iOS_17"]


def test_pool_without_client_class_has_no_client_classes_key():
    """AC #6: pool with no client-class has neither 'client-class' nor 'client-classes'."""
    config = _cfg({
        "subnets": [
            {
                "subnet": "10.0.1.0/24",
                "pools": [{"range": "auto"}],
            }
        ]
    })
    result = build(config)
    pool = result["Dhcp4"]["subnet4"][0]["pools"][0]

    assert "client-class" not in pool
    assert "client-classes" not in pool


def test_multiple_pools_mixed_client_class():
    """AC #6: client-classes appears only on pools that have client-class set."""
    config = _cfg({
        "subnets": [
            {
                "subnet": "10.0.1.0/24",
                "pools": [
                    {"range": "10.0.1.10 - 10.0.1.100", "client-class": "Windows_11"},
                    {"range": "10.0.1.101 - 10.0.1.200"},
                ],
            }
        ]
    })
    result = build(config)
    pools = result["Dhcp4"]["subnet4"][0]["pools"]

    assert pools[0]["client-classes"] == ["Windows_11"]
    assert "client-classes" not in pools[1]


# ---------------------------------------------------------------------------
# AC #7 — Key order matches Kea-natural ordering
# ---------------------------------------------------------------------------


def test_dhcp4_top_level_key_order():
    """AC #7: Dhcp4 keys follow Kea-natural order: timers then option-data then subnet4."""
    config = _cfg({
        "valid-lifetime": 3600,
        "renew-timer": 900,
        "rebind-timer": 1800,
        "dns-servers": ["8.8.8.8"],
        "subnets": [_minimal_subnet()],
    })
    result = build(config)
    keys = list(result["Dhcp4"].keys())

    expected_order = ["valid-lifetime", "renew-timer", "rebind-timer", "option-data", "subnet4"]
    assert keys == expected_order


def test_subnet_key_order():
    """AC #7: subnet keys follow Kea-natural order: id, subnet, timers, option-data, pools."""
    config = _cfg({
        "subnets": [
            {
                "subnet": "10.0.1.0/24",
                "valid-lifetime": 7200,
                "renew-timer": 1800,
                "rebind-timer": 3600,
                "dns-servers": ["1.1.1.1"],
                "pools": [{"range": "auto"}],
            }
        ]
    })
    result = build(config)
    subnet_keys = list(result["Dhcp4"]["subnet4"][0].keys())

    expected_order = [
        "id", "subnet", "valid-lifetime", "renew-timer", "rebind-timer", "option-data", "pools"
    ]
    assert subnet_keys == expected_order


def test_pool_key_order_with_client_class():
    """AC #7: pool entry key order is pool → client-classes."""
    config = _cfg({
        "subnets": [
            {
                "subnet": "10.0.1.0/24",
                "pools": [{"range": "auto", "client-class": "Windows_11"}],
            }
        ]
    })
    result = build(config)
    pool_keys = list(result["Dhcp4"]["subnet4"][0]["pools"][0].keys())

    assert pool_keys == ["pool", "client-classes"]


def test_subnet_key_order_with_client_class():
    """AC #7: when client-class is set, it appears after rebind-timer and before option-data."""
    config = _cfg({
        "subnets": [
            {
                "subnet": "10.0.1.0/24",
                "valid-lifetime": 7200,
                "client-class": "VoIP",
                "dns-servers": ["1.1.1.1"],
            }
        ]
    })
    result = build(config)
    subnet_keys = list(result["Dhcp4"]["subnet4"][0].keys())

    # client-class sits between rebind-timer (absent here) and option-data
    assert subnet_keys.index("client-class") < subnet_keys.index("option-data")
    # id and subnet always lead
    assert subnet_keys[0] == "id"
    assert subnet_keys[1] == "subnet"


def test_pool_key_order_without_client_class():
    """AC #7: pool entry without client-class has only the 'pool' key."""
    config = _cfg({
        "subnets": [
            {
                "subnet": "10.0.1.0/24",
                "pools": [{"range": "auto"}],
            }
        ]
    })
    result = build(config)
    pool_keys = list(result["Dhcp4"]["subnet4"][0]["pools"][0].keys())

    assert pool_keys == ["pool"]


# ---------------------------------------------------------------------------
# Additional integration-style tests
# ---------------------------------------------------------------------------


def test_build_raises_when_no_dhcp4():
    """build() raises ValueError if GlobalConfig has no dhcp4 section."""
    config = GlobalConfig.model_validate({"dhcp6": {"subnets": []}})
    with pytest.raises(ValueError, match="no dhcp4 section"):
        build(config)


def test_result_top_level_key_is_Dhcp4():
    """build() always returns a dict with exactly one key: 'Dhcp4'."""
    config = _cfg({"subnets": [_minimal_subnet()]})
    result = build(config)

    assert list(result.keys()) == ["Dhcp4"]


def test_subnet_option_data_from_scalar_fields():
    """Subnet-level scalar option fields are converted to option-data at subnet scope."""
    config = _cfg({
        "subnets": [
            {
                "subnet": "10.0.1.0/24",
                "dns-servers": ["1.1.1.1"],
                "routers": ["10.0.1.1"],
            }
        ]
    })
    result = build(config)
    option_data = result["Dhcp4"]["subnet4"][0]["option-data"]

    names = {e["name"] for e in option_data}
    assert "domain-name-servers" in names
    assert "routers" in names


def test_global_explicit_option_data_included():
    """Explicit option-data entries at global scope appear in Dhcp4 option-data."""
    config = _cfg({
        "option-data": [{"name": "boot-file-name", "data": "pxelinux.0"}],
        "subnets": [_minimal_subnet()],
    })
    result = build(config)
    option_data = result["Dhcp4"]["option-data"]

    assert {"name": "boot-file-name", "data": "pxelinux.0"} in option_data


def test_explicit_option_data_overrides_scalar_same_name():
    """Explicit option-data entry with same name overrides scalar-derived entry."""
    config = _cfg({
        "dns-servers": ["8.8.8.8"],
        "option-data": [{"name": "domain-name-servers", "data": "1.1.1.1"}],
        "subnets": [_minimal_subnet()],
    })
    result = build(config)
    option_data = result["Dhcp4"]["option-data"]

    # Only one entry for domain-name-servers; explicit wins
    dns_entries = [e for e in option_data if e["name"] == "domain-name-servers"]
    assert len(dns_entries) == 1
    assert dns_entries[0]["data"] == "1.1.1.1"


def test_subnet_with_no_pools_has_no_pools_key():
    """Subnet with no pools list has no 'pools' key in output."""
    config = _cfg({"subnets": [_minimal_subnet()]})
    result = build(config)
    subnet = result["Dhcp4"]["subnet4"][0]

    assert "pools" not in subnet


def test_subnet_with_no_options_has_no_option_data_key():
    """Subnet with no option overrides has no 'option-data' key."""
    config = _cfg({"subnets": [_minimal_subnet()]})
    result = build(config)
    subnet = result["Dhcp4"]["subnet4"][0]

    assert "option-data" not in subnet


# ---------------------------------------------------------------------------
# Story 2.3 — MAC-based Host Reservations (AC #1–#5 + edge cases)
# ---------------------------------------------------------------------------


def test_reservations_array_present_when_defined():
    """AC #1: subnet entry contains 'reservations' array when reservations defined."""
    config = _cfg({
        "subnets": [{
            "subnet": "192.168.1.0/24",
            "reservations": [
                {"hw-address": "aa:bb:cc:dd:ee:ff", "ip-address": "192.168.1.100"},
            ],
        }]
    })
    result = build(config)
    subnet = result["Dhcp4"]["subnet4"][0]

    assert "reservations" in subnet
    assert len(subnet["reservations"]) == 1


def test_reservation_kea_keys_hw_address_ip_address_hostname():
    """AC #2: hw-address, ip-address, hostname all appear with Kea hyphenated key names."""
    config = _cfg({
        "subnets": [{
            "subnet": "192.168.1.0/24",
            "reservations": [
                {
                    "hw-address": "1a:1b:1c:1d:1e:1f",
                    "ip-address": "192.168.1.100",
                    "hostname": "printer",
                },
            ],
        }]
    })
    result = build(config)
    entry = result["Dhcp4"]["subnet4"][0]["reservations"][0]

    assert entry["hw-address"] == "1a:1b:1c:1d:1e:1f"
    assert entry["ip-address"] == "192.168.1.100"
    assert entry["hostname"] == "printer"


def test_reservation_per_host_option_data_merge_by_name():
    """AC #3: per-host option-data is processed via merge_option_data (merge-by-name).

    Two entries with the same name collapse to one (most-specific wins), proving
    that merge_option_data is actually exercised rather than acting as a pass-through.
    """
    config = _cfg({
        "subnets": [{
            "subnet": "192.168.1.0/24",
            "reservations": [
                {
                    "hw-address": "aa:bb:cc:dd:ee:ff",
                    "ip-address": "192.168.1.100",
                    "option-data": [
                        {"name": "domain-name-servers", "data": "8.8.8.8"},
                        {"name": "domain-name-servers", "data": "1.1.1.1"},  # duplicate name
                    ],
                },
            ],
        }]
    })
    result = build(config)
    entry = result["Dhcp4"]["subnet4"][0]["reservations"][0]

    assert "option-data" in entry
    dns_entries = [e for e in entry["option-data"] if e["name"] == "domain-name-servers"]
    assert len(dns_entries) == 1  # merge-by-name deduplicated
    assert dns_entries[0]["data"] == "1.1.1.1"  # last-wins (most-specific)


def test_no_reservations_key_when_empty():
    """AC #4: no 'reservations' key in subnet output when subnet has no reservations."""
    config = _cfg({"subnets": [{"subnet": "10.0.0.0/24"}]})
    result = build(config)
    subnet = result["Dhcp4"]["subnet4"][0]

    assert "reservations" not in subnet


def test_multiple_reservations_in_yaml_order():
    """AC #5: multiple reservations appear as separate entries in YAML order."""
    config = _cfg({
        "subnets": [{
            "subnet": "192.168.1.0/24",
            "reservations": [
                {"hw-address": "aa:bb:cc:dd:ee:01", "ip-address": "192.168.1.101"},
                {"hw-address": "aa:bb:cc:dd:ee:02", "ip-address": "192.168.1.102"},
                {"hw-address": "aa:bb:cc:dd:ee:03", "ip-address": "192.168.1.103"},
            ],
        }]
    })
    result = build(config)
    reservations = result["Dhcp4"]["subnet4"][0]["reservations"]

    assert len(reservations) == 3
    assert reservations[0]["ip-address"] == "192.168.1.101"
    assert reservations[1]["ip-address"] == "192.168.1.102"
    assert reservations[2]["ip-address"] == "192.168.1.103"


def test_reservation_hostname_omitted_when_none():
    """Edge case: hostname is omitted from reservation entry when not provided."""
    config = _cfg({
        "subnets": [{
            "subnet": "192.168.1.0/24",
            "reservations": [
                {"hw-address": "aa:bb:cc:dd:ee:ff", "ip-address": "192.168.1.100"},
            ],
        }]
    })
    result = build(config)
    entry = result["Dhcp4"]["subnet4"][0]["reservations"][0]

    assert "hostname" not in entry


def test_reservation_option_data_omitted_when_empty():
    """Edge case: option-data key omitted when reservation has no option overrides."""
    config = _cfg({
        "subnets": [{
            "subnet": "192.168.1.0/24",
            "reservations": [
                {"hw-address": "aa:bb:cc:dd:ee:ff", "ip-address": "192.168.1.100"},
            ],
        }]
    })
    result = build(config)
    entry = result["Dhcp4"]["subnet4"][0]["reservations"][0]

    assert "option-data" not in entry


def test_reservation_key_order():
    """Key order: hw-address → ip-address → hostname → option-data (Kea-natural)."""
    config = _cfg({
        "subnets": [{
            "subnet": "192.168.1.0/24",
            "reservations": [
                {
                    "hw-address": "1a:1b:1c:1d:1e:1f",
                    "ip-address": "192.168.1.100",
                    "hostname": "printer",
                    "option-data": [{"name": "domain-name-servers", "data": "8.8.4.4"}],
                },
            ],
        }]
    })
    result = build(config)
    entry_keys = list(result["Dhcp4"]["subnet4"][0]["reservations"][0].keys())

    assert entry_keys == ["hw-address", "ip-address", "hostname", "option-data"]


def test_reservations_appear_after_pools_in_subnet():
    """Subnet key order: reservations appears after pools."""
    config = _cfg({
        "subnets": [{
            "subnet": "192.168.1.0/24",
            "pools": [{"range": "192.168.1.10 - 192.168.1.50"}],
            "reservations": [
                {"hw-address": "aa:bb:cc:dd:ee:ff", "ip-address": "192.168.1.100"},
            ],
        }]
    })
    result = build(config)
    subnet_keys = list(result["Dhcp4"]["subnet4"][0].keys())

    assert "pools" in subnet_keys
    assert "reservations" in subnet_keys
    assert subnet_keys.index("pools") < subnet_keys.index("reservations")


def test_reservation_does_not_inherit_subnet_options():
    """Reservation with no option-data emits no option-data even when subnet has options.

    Kea handles option inheritance at runtime; the builder must emit only
    reservation-scope options (scope-isolation rule).
    """
    config = _cfg({
        "subnets": [{
            "subnet": "192.168.1.0/24",
            "dns-servers": ["8.8.8.8"],
            "reservations": [
                {"hw-address": "aa:bb:cc:dd:ee:ff", "ip-address": "192.168.1.100"},
            ],
        }]
    })
    result = build(config)
    subnet = result["Dhcp4"]["subnet4"][0]

    # Subnet has option-data at its own scope
    assert "option-data" in subnet
    # Reservation must NOT inherit it
    reservation = subnet["reservations"][0]
    assert "option-data" not in reservation


