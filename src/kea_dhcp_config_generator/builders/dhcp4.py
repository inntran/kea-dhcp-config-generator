"""DHCPv4 JSON builder — assembles a Kea-ready Dhcp4 JSON dict.

Public API:
    build(config: GlobalConfig) -> dict
        Returns {"Dhcp4": {...}} suitable for json.dumps(..., indent=2, ensure_ascii=False).

Key ordering follows Kea documentation examples (not alphabetical):
    Dhcp4 level:  valid-lifetime → renew-timer → rebind-timer → option-data → subnet4
    Subnet level: id → subnet → valid-lifetime → renew-timer → rebind-timer → client-class
                  → option-data → pools → reservations
    Pool level:   pool → client-classes (only when set)
    Reservation:  hw-address → ip-address → [hostname] → [option-data]

Option fields on models (dns_servers, domain_name, ntp_servers, routers) are converted to
Kea option-data format at their respective scope; explicit option_data fields are merged
using merge-by-name semantics (most-specific wins).
"""

from kea_dhcp_config_generator.builders.options import merge_option_data
from kea_dhcp_config_generator.builders.pools import calculate_pool_range, parse_pool_range
from kea_dhcp_config_generator.models.input import (
    Dhcp4Config,
    GlobalConfig,
    HostReservationV4Model,
    PoolV4Model,
    SubnetV4Model,
)


def build(config: GlobalConfig) -> dict:
    """Assemble a Kea-ready Dhcp4 JSON dict from a validated GlobalConfig.

    Args:
        config: Fully validated GlobalConfig (from models.input.parse()).

    Returns:
        dict with structure {"Dhcp4": {...}}.
        Serialize with: json.dumps(result, indent=2, ensure_ascii=False)

    Raises:
        ValueError: if config.dhcp4 is None.
    """
    if config.dhcp4 is None:
        raise ValueError("GlobalConfig has no dhcp4 section; cannot build DHCPv4 config")

    dhcp4 = config.dhcp4
    dhcp4_dict: dict = {}

    # --- Timers (Kea-natural order) ---
    if dhcp4.valid_lifetime is not None:
        dhcp4_dict["valid-lifetime"] = dhcp4.valid_lifetime
    if dhcp4.renew_timer is not None:
        dhcp4_dict["renew-timer"] = dhcp4.renew_timer
    if dhcp4.rebind_timer is not None:
        dhcp4_dict["rebind-timer"] = dhcp4.rebind_timer

    # --- Global option-data (scalar fields merged with explicit option-data) ---
    global_option_data = _scope_option_data(dhcp4)
    if global_option_data:
        dhcp4_dict["option-data"] = global_option_data

    # --- Subnet list ---
    if dhcp4.subnets:
        dhcp4_dict["subnet4"] = _build_subnet4(dhcp4)

    return {"Dhcp4": dhcp4_dict}


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _scalar_to_option_data(model: Dhcp4Config | SubnetV4Model) -> list[dict]:
    """Convert scalar option fields to a list of Kea option-data entries.

    Scalar fields and their Kea option names:
        dns_servers  → domain-name-servers  (option 6)
        domain_name  → domain-name          (option 15)
        ntp_servers  → ntp-servers          (option 42)
        routers      → routers              (option 3)

    Multiple values are joined as a comma-separated string per Kea conventions.
    """
    entries: list[dict] = []
    if model.dns_servers:
        entries.append({"name": "domain-name-servers", "data": ", ".join(model.dns_servers)})
    if model.domain_name:
        entries.append({"name": "domain-name", "data": model.domain_name})
    if model.ntp_servers:
        entries.append({"name": "ntp-servers", "data": ", ".join(model.ntp_servers)})
    if model.routers:
        entries.append({"name": "routers", "data": ", ".join(model.routers)})
    return entries


def _scope_option_data(model: Dhcp4Config | SubnetV4Model) -> list[dict]:
    """Merge a model's scalar option fields with its explicit option-data list.

    Scalar-derived entries are the base; explicit option-data entries override
    by name (most-specific-wins via merge_option_data).
    """
    scalar = _scalar_to_option_data(model)
    return merge_option_data(scalar, model.option_data)


def _resolve_pool_range(pool: PoolV4Model, subnet_cidr: str) -> str:
    """Return the pool range as 'start - end'.

    If pool.range == "auto", calculates from subnet_cidr using ipaddress stdlib.
    Otherwise parses the explicit 'x.x.x.x - y.y.y.y' string.
    """
    if pool.range == "auto":
        start, end = calculate_pool_range(subnet_cidr, pool.skip_start, pool.skip_end)
    else:
        start, end = parse_pool_range(pool.range)
    return f"{start} - {end}"


def _build_pool_entry(pool: PoolV4Model, subnet_cidr: str) -> dict:
    """Build a single Kea pool entry dict.

    Pool key order: pool → client-classes (Kea-natural).
    client-class on a pool maps to "client-classes": [value] (list form, Kea 3.x).
    The deprecated singular "client-class" field is never emitted on pools.
    """
    entry: dict = {"pool": _resolve_pool_range(pool, subnet_cidr)}
    if pool.client_class:
        entry["client-classes"] = [pool.client_class]
    return entry


def _build_reservation_entry(reservation: HostReservationV4Model) -> dict:
    """Build a single Kea host reservation dict.

    Key order (Kea-natural): hw-address → ip-address → hostname → option-data
    Omit hostname if None; omit option-data if empty.
    """
    entry: dict = {
        "hw-address": reservation.hw_address,
        "ip-address": reservation.ip_address,
    }
    if reservation.hostname is not None:
        entry["hostname"] = reservation.hostname
    option_data = merge_option_data([], reservation.option_data)
    if option_data:
        entry["option-data"] = option_data
    return entry


def _build_subnet4(dhcp4: Dhcp4Config) -> list[dict]:
    """Build the subnet4 list.

    Subnet ID rules (all-or-none):
        - If ANY subnet has an explicit id, ALL subnets must have explicit IDs.
          Raises ValueError if the mix is detected (would cause collisions).
        - If NO subnet has an explicit id, IDs are auto-assigned sequentially
          from 1 in YAML order.

    Subnet key order (Kea-natural):
        id → subnet → valid-lifetime → renew-timer → rebind-timer → client-class
        → option-data → pools → reservations
    """
    # Validate all-or-none ID consistency.
    # If any subnet has an explicit id, all must — mixing is not allowed because
    # auto-assigned IDs starting from 1 would collide with user-assigned IDs.
    has_any_explicit = any(s.id is not None for s in dhcp4.subnets)
    if has_any_explicit:
        missing = [i + 1 for i, s in enumerate(dhcp4.subnets) if s.id is None]
        if missing:
            raise ValueError(
                f"Mixed subnet ID assignment: subnet(s) at position(s) {missing} "
                "lack an explicit 'id' while other subnets define one. "
                "Either assign explicit IDs to all subnets or to none."
            )

    subnets: list[dict] = []
    next_id = 1

    for subnet in dhcp4.subnets:
        if subnet.id is not None:
            assigned_id = subnet.id
        else:
            assigned_id = next_id
            next_id += 1

        subnet_dict: dict = {
            "id": assigned_id,
            "subnet": subnet.subnet,
        }

        # Per-subnet timers (omit if not set)
        if subnet.valid_lifetime is not None:
            subnet_dict["valid-lifetime"] = subnet.valid_lifetime
        if subnet.renew_timer is not None:
            subnet_dict["renew-timer"] = subnet.renew_timer
        if subnet.rebind_timer is not None:
            subnet_dict["rebind-timer"] = subnet.rebind_timer

        # Subnet-level client-class selector (string form; Kea 3.x subnet selector)
        if subnet.client_class:
            subnet_dict["client-class"] = subnet.client_class

        # Subnet-specific option-data (scalar + explicit at this scope only)
        subnet_option_data = _scope_option_data(subnet)
        if subnet_option_data:
            subnet_dict["option-data"] = subnet_option_data

        # Pools
        if subnet.pools:
            subnet_dict["pools"] = [
                _build_pool_entry(pool, subnet.subnet) for pool in subnet.pools
            ]

        # Reservations (after pools, Kea-natural order)
        if subnet.reservations:
            subnet_dict["reservations"] = [
                _build_reservation_entry(r) for r in subnet.reservations
            ]

        subnets.append(subnet_dict)

    return subnets
