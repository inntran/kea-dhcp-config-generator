"""DHCPv6 JSON builder — assembles a Kea-ready Dhcp6 JSON dict.

Public API:
    build(config: GlobalConfig, fingerprint_library: DHCPFingerprint | None = None) -> dict
        Returns {"Dhcp6": {...}} suitable for json.dumps(..., indent=2, ensure_ascii=True).

Key ordering follows Kea documentation examples (not alphabetical):
    Dhcp6 level:  valid-lifetime → preferred-lifetime → renew-timer → rebind-timer
                  → option-data → client-classes → subnet6
    Subnet level: id → subnet → valid-lifetime → preferred-lifetime → renew-timer
                  → rebind-timer → option-data → pools → pd-pools → reservations
    NA pool:      pool → client-classes (only when set)
    PD pool:      prefix → delegated-len → client-classes (only when set)
    Reservation:  duid → ip-addresses → [hostname] → [option-data]

DHCPv6 differs from DHCPv4 in three Kea-native ways the builder honours:
  - NA pools serialise as {"pool": "<start> - <end>"}; PD pools as
    {"prefix": ..., "prefix-len": ..., "delegated-len": ...}.
  - Host reservations use "duid" + "ip-addresses" (a list), never hw-address.
  - The dns-servers convenience field maps to the DHCPv6 "dns-servers" option
    (option 23, space "dhcp6"), distinct from the DHCPv4 domain-name-servers.

Subnet IDs auto-assign as a 1-based YAML-order sequence independent from the
DHCPv4 sequence; the all-or-none explicit-ID rule applies per stack.
"""

from kea_dhcp_config_generator.builders.options import merge_option_data
from kea_dhcp_config_generator.builders.pools import (
    calculate_pool_range,
    parse_pool_range,
)
from kea_dhcp_config_generator.fingerprints import DHCPFingerprint
from kea_dhcp_config_generator.models.input import (
    Dhcp6Config,
    GlobalConfig,
    HostReservationV6Model,
    PoolV6NaModel,
    PoolV6PdModel,
    SubnetV6Model,
)


def build(config: GlobalConfig, fingerprint_library: DHCPFingerprint | None = None) -> dict:
    """Assemble a Kea-ready Dhcp6 JSON dict from a validated GlobalConfig.

    Args:
        config: Fully validated GlobalConfig (from models.input.parse()).
        fingerprint_library: Optional DHCPFingerprint instance. When provided,
            pool client-class names found in the library are resolved to
            top-level Kea client-classes entries (test or template-test).
            Never instantiated internally — always passed as a dependency.

    Returns:
        dict with structure {"Dhcp6": {...}}.
        Serialize with: json.dumps(result, indent=2, ensure_ascii=True)

    Raises:
        ValueError: if config.dhcp6 is None.
    """
    if config.dhcp6 is None:
        raise ValueError("GlobalConfig has no dhcp6 section; cannot build DHCPv6 config")

    dhcp6 = config.dhcp6
    dhcp6_dict: dict = {}

    # --- Timers (Kea-natural order) ---
    if dhcp6.valid_lifetime is not None:
        dhcp6_dict["valid-lifetime"] = dhcp6.valid_lifetime
    if dhcp6.preferred_lifetime is not None:
        dhcp6_dict["preferred-lifetime"] = dhcp6.preferred_lifetime
    if dhcp6.renew_timer is not None:
        dhcp6_dict["renew-timer"] = dhcp6.renew_timer
    if dhcp6.rebind_timer is not None:
        dhcp6_dict["rebind-timer"] = dhcp6.rebind_timer

    # --- Global option-data (scalar fields merged with explicit option-data) ---
    global_option_data = _scope_option_data(dhcp6)
    if global_option_data:
        dhcp6_dict["option-data"] = global_option_data

    # --- Subnet list (client-classes immediately before subnet6, Kea-natural order) ---
    if dhcp6.subnets:
        client_classes = _collect_client_classes(dhcp6, fingerprint_library)
        if client_classes:
            dhcp6_dict["client-classes"] = client_classes
        dhcp6_dict["subnet6"] = _build_subnet6(dhcp6)

    return {"Dhcp6": dhcp6_dict}


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _collect_client_classes(
    dhcp6: Dhcp6Config,
    fingerprint_library: DHCPFingerprint | None,
) -> list[dict]:
    """Build the top-level client-classes array for Kea Dhcp6 output.

    Scans all pools (NA and PD) in YAML order (subnets top-to-bottom, pools
    top-to-bottom). For each pool.client_class:
      - Deduplicates by name (first occurrence wins; subsequent skipped)
      - Looks up the name in fingerprint_library
      - If found: emits {"name": ..., "test": ...} or {"name": ..., "template-test": ...}
      - If not found or no library: skips top-level entry
        (bare name still appears in pool's client-classes list — unchanged)

    Returns empty list if fingerprint_library is None or no pools have client_class.
    """
    if not fingerprint_library:
        return []

    seen: set[str] = set()
    entries: list[dict] = []

    for subnet in dhcp6.subnets:
        for pool in (subnet.pools or []):
            name = pool.client_class
            if not name or name in seen:
                continue
            seen.add(name)
            rule = fingerprint_library.lookup(name)
            if rule is None:
                continue  # custom class: pool still has it; no top-level entry
            entry: dict = {"name": name}
            if "test" in rule:
                entry["test"] = rule["test"]
            else:
                entry["template-test"] = rule["template-test"]
            entries.append(entry)

    return entries


def _scalar_to_option_data(model: Dhcp6Config | SubnetV6Model) -> list[dict]:
    """Convert DHCPv6 scalar option fields to Kea option-data entries.

    Scalar fields and their Kea DHCPv6 option names:
        dns_servers → dns-servers (option 23, space "dhcp6")

    Multiple values are joined as a comma-separated string per Kea conventions.
    """
    entries: list[dict] = []
    if model.dns_servers:
        entries.append({"name": "dns-servers", "data": ", ".join(model.dns_servers)})
    return entries


def _scope_option_data(model: Dhcp6Config | SubnetV6Model) -> list[dict]:
    """Merge a model's scalar option fields with its explicit option-data list.

    Scalar-derived entries are the base; explicit option-data entries override
    by name (most-specific-wins via merge_option_data).
    """
    scalar = _scalar_to_option_data(model)
    return merge_option_data(scalar, model.option_data)


def _resolve_na_pool_range(pool: PoolV6NaModel, subnet_cidr: str) -> str:
    """Return an NA pool range as 'start - end'.

    If pool.range == "auto", calculates from subnet_cidr (IPv6).
    Otherwise parses the explicit '<start> - <end>' string.
    """
    if pool.range == "auto":
        start, end = calculate_pool_range(subnet_cidr)
    else:
        start, end = parse_pool_range(pool.range)
    return f"{start} - {end}"


def _build_na_pool_entry(pool: PoolV6NaModel, subnet_cidr: str) -> dict:
    """Build a single Kea NA pool entry dict.

    Pool key order: pool → client-classes (Kea-natural).
    client-class on a pool maps to "client-classes": [value] (list form, Kea 3.x).
    """
    entry: dict = {"pool": _resolve_na_pool_range(pool, subnet_cidr)}
    if pool.client_class:
        entry["client-classes"] = [pool.client_class]
    return entry


def _build_pd_pool_entry(pool: PoolV6PdModel) -> dict:
    """Build a single Kea PD pool entry dict.

    Key order (Kea-natural): prefix → prefix-len → delegated-len → client-classes.
    """
    entry: dict = {
        "prefix": pool.prefix,
        "prefix-len": pool.prefix_len,
        "delegated-len": pool.delegated_len,
    }
    if pool.client_class:
        entry["client-classes"] = [pool.client_class]
    return entry


def _build_reservation_entry(reservation: HostReservationV6Model) -> dict:
    """Build a single Kea DHCPv6 host reservation dict.

    Key order (Kea-natural): duid → ip-addresses → hostname → option-data.
    ip-addresses is a list (DHCPv6 supports multiple addresses per host);
    omit it when no static address was given. Omit hostname/option-data when empty.
    """
    entry: dict = {"duid": reservation.duid}
    if reservation.ip_address is not None:
        entry["ip-addresses"] = [reservation.ip_address]
    if reservation.hostname is not None:
        entry["hostname"] = reservation.hostname
    option_data = merge_option_data([], reservation.option_data)
    if option_data:
        entry["option-data"] = option_data
    return entry


def _build_subnet6(dhcp6: Dhcp6Config) -> list[dict]:
    """Build the subnet6 list.

    Subnet ID rules (all-or-none), independent from the DHCPv4 sequence:
        - If ANY subnet has an explicit id, ALL subnets must have explicit IDs.
          Raises ValueError if the mix is detected (would cause collisions).
        - If NO subnet has an explicit id, IDs are auto-assigned sequentially
          from 1 in YAML order.

    Subnet key order (Kea-natural):
        id → subnet → valid-lifetime → preferred-lifetime → renew-timer
        → rebind-timer → option-data → pools → pd-pools → reservations
    """
    has_any_explicit = any(s.id is not None for s in dhcp6.subnets)
    if has_any_explicit:
        missing = [i + 1 for i, s in enumerate(dhcp6.subnets) if s.id is None]
        if missing:
            raise ValueError(
                f"Mixed subnet ID assignment: subnet(s) at position(s) {missing} "
                "lack an explicit 'id' while other subnets define one. "
                "Either assign explicit IDs to all subnets or to none."
            )

    subnets: list[dict] = []
    next_id = 1

    for subnet in dhcp6.subnets:
        if subnet.id is not None:
            assigned_id = subnet.id
        else:
            assigned_id = next_id
            next_id += 1

        # subnet.subnet is an IPv6Network; Kea wants the canonical CIDR string.
        subnet_cidr = str(subnet.subnet)
        subnet_dict: dict = {
            "id": assigned_id,
            "subnet": subnet_cidr,
        }

        if subnet.valid_lifetime is not None:
            subnet_dict["valid-lifetime"] = subnet.valid_lifetime
        if subnet.preferred_lifetime is not None:
            subnet_dict["preferred-lifetime"] = subnet.preferred_lifetime
        if subnet.renew_timer is not None:
            subnet_dict["renew-timer"] = subnet.renew_timer
        if subnet.rebind_timer is not None:
            subnet_dict["rebind-timer"] = subnet.rebind_timer

        subnet_option_data = _scope_option_data(subnet)
        if subnet_option_data:
            subnet_dict["option-data"] = subnet_option_data

        # Split the discriminated-union pool list into NA pools ("pools") and
        # PD pools ("pd-pools"), preserving YAML order within each kind.
        na_entries: list[dict] = []
        pd_entries: list[dict] = []
        for pool in subnet.pools:
            if isinstance(pool, PoolV6PdModel):
                pd_entries.append(_build_pd_pool_entry(pool))
            else:
                na_entries.append(_build_na_pool_entry(pool, subnet_cidr))
        if na_entries:
            subnet_dict["pools"] = na_entries
        if pd_entries:
            subnet_dict["pd-pools"] = pd_entries

        if subnet.reservations:
            subnet_dict["reservations"] = [
                _build_reservation_entry(r) for r in subnet.reservations
            ]

        subnets.append(subnet_dict)

    return subnets
