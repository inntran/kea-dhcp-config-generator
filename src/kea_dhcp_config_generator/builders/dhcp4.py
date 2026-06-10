"""DHCPv4 JSON builder — assembles a Kea-ready Dhcp4 JSON dict.

Public API:
    build(config: GlobalConfig, fingerprint_library: DHCPFingerprint | None = None) -> dict
        Returns {"Dhcp4": {...}} suitable for json.dumps(..., indent=2, ensure_ascii=False).

Key ordering follows Kea documentation examples (not alphabetical):
    Dhcp4 level:  valid-lifetime → renew-timer → rebind-timer → option-data
                  → client-classes → subnet4
    Subnet level: id → subnet → valid-lifetime → renew-timer → rebind-timer → client-classes
                  → option-data → pools → reservations
    Pool level:   pool → client-classes (only when set)
    Reservation:  hw-address → ip-address → [hostname] → [option-data]

Option fields on models (dns_servers, domain_name, ntp_servers, routers) are converted to
Kea option-data format at their respective scope; explicit option_data fields are merged
using merge-by-name semantics (most-specific wins).
"""

from difflib import get_close_matches

from kea_dhcp_config_generator.builders.options import merge_option_data
from kea_dhcp_config_generator.builders.pools import calculate_pool_range, parse_pool_range
from kea_dhcp_config_generator.fingerprints import DHCPFingerprint
from kea_dhcp_config_generator.models.input import (
    Dhcp4Config,
    GlobalConfig,
    HostReservationV4Model,
    OptionProfileModel,
    PoolV4Model,
    SubnetV4Model,
)
from kea_dhcp_config_generator.validation.errors import KeaConfigError


def build(config: GlobalConfig, fingerprint_library: DHCPFingerprint | None = None) -> dict:
    """Assemble a Kea-ready Dhcp4 JSON dict from a validated GlobalConfig.

    Args:
        config: Fully validated GlobalConfig (from models.input.parse()).
        fingerprint_library: Optional DHCPFingerprint instance. When provided,
            pool client-class names found in the library are resolved to
            top-level Kea client-classes entries (test or template-test).
            Never instantiated internally — always passed as a dependency.

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

    # --- Subnet list (client-classes immediately before subnet4, Kea-natural order) ---
    if dhcp4.subnets:
        client_classes = _collect_client_classes(dhcp4, fingerprint_library)
        subnet4, catchall_classes = _build_subnet4(dhcp4, config.option_profiles)
        # Generated CatchAll classes use `not member(<device class>)`, so they must
        # be evaluated AFTER the device classes — append them last. They are emitted
        # even when no device class resolved to a library rule (e.g. all-custom
        # guards), so client-classes may exist solely because of CatchAll entries.
        all_classes = client_classes + catchall_classes
        if all_classes:
            dhcp4_dict["client-classes"] = all_classes
        dhcp4_dict["subnet4"] = subnet4

    return {"Dhcp4": dhcp4_dict}


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _collect_client_classes(
    dhcp4: Dhcp4Config,
    fingerprint_library: DHCPFingerprint | None,
) -> list[dict]:
    """Build the top-level client-classes array for Kea Dhcp4 output.

    Scans all pools in YAML order (subnets top-to-bottom, pools top-to-bottom).
    For each pool.client_class:
      - Deduplicates by name (first occurrence wins; subsequent occurrences skipped)
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

    for subnet in dhcp4.subnets:
        name = subnet.client_class
        if name and name not in seen:
            seen.add(name)
            rule = fingerprint_library.lookup(name)
            if rule is not None:
                entry: dict = {"name": name}
                if "test" in rule:
                    entry["test"] = rule["test"]
                else:
                    entry["template-test"] = rule["template-test"]
                entries.append(entry)
        for pool in subnet.pools or []:
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


def _resolve_option_profile(
    name: str,
    profiles: dict[str, OptionProfileModel],
) -> OptionProfileModel:
    profile = profiles.get(name)
    if profile is not None:
        return profile
    suggestion = None
    if profiles:
        matches = get_close_matches(name, profiles.keys(), n=1)
        if matches:
            suggestion = f'did you mean "{matches[0]}"?'
    message = f'unknown option_profile "{name}"'
    if suggestion:
        message = f"{message} ({suggestion})"
    raise KeaConfigError(message)


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


def _subnet_used_pool_classes(subnet: SubnetV4Model) -> list[str]:
    """Distinct pool-guard class names in a subnet, in first-encounter YAML order.

    Custom (non-library) names are included — they are still classes a catch-all
    must exclude. The subnet-level client-class selector is intentionally excluded;
    only pool guards participate in pool-level catch-all coverage.
    """
    seen: set[str] = set()
    used: list[str] = []
    for pool in subnet.pools or []:
        name = pool.client_class
        if name and name not in seen:
            seen.add(name)
            used.append(name)
    return used


def _make_catchall_test(used_classes: list[str]) -> str:
    """Build the CatchAll test expression: `not member('A') and not member('B')`.

    Mirrors the Kea KB catch-all pattern (understanding-client-classification.md):
    the synthesized class matches exactly the clients that belong to none of the
    subnet's guarded classes.

    Class names are embedded inside single-quoted Kea expression string literals.
    Kea's eval lexer string rule is `'[^'\n]*'` with NO escape mechanism, so a
    name containing a single quote or newline cannot be represented and would
    produce an unparseable config. Reject such names with a clear error rather
    than emit broken output.
    """
    for name in used_classes:
        if "'" in name or "\n" in name:
            raise KeaConfigError(
                f"client-class name {name!r} cannot be used in a generated catch-all "
                "expression: Kea class-expression string literals cannot contain a "
                "single quote or newline. Rename the class."
            )
    return " and ".join(f"not member('{name}')" for name in used_classes)


def _referenced_class_names(dhcp4: Dhcp4Config) -> set[str]:
    """Every client-class name the user references anywhere in the dhcp4 config.

    Used to keep generated CatchAll names from colliding with a user's own class
    (e.g. a custom class literally named `CatchAll_1`), which would otherwise
    rebind the user's pool selector to the synthesized `not member(...)` class.
    """
    names: set[str] = set()
    for subnet in dhcp4.subnets:
        if subnet.client_class:
            names.add(subnet.client_class)
        for pool in subnet.pools or []:
            if pool.client_class:
                names.add(pool.client_class)
    return names


def _catchall_name(assigned_id: int | str, referenced: set[str]) -> str:
    """Return the reserved CatchAll class name for a subnet: `CatchAll_<id>`.

    `CatchAll_<id>` is a reserved name the builder owns. The subnet id makes it
    unique across subnets (Kea's client-class namespace is global). If the user
    has manually defined a class with this exact reserved name, reject the config
    rather than silently rebinding their selector to the generated class.
    """
    name = f"CatchAll_{assigned_id}"
    if name in referenced:
        raise KeaConfigError(
            f"client-class name {name!r} is reserved: the tool generates a "
            f"catch-all class named {name!r} for subnet id {assigned_id}. "
            "Rename your class."
        )
    return name


def _build_subnet4(
    dhcp4: Dhcp4Config,
    option_profiles: dict[str, OptionProfileModel],
) -> tuple[list[dict], list[dict]]:
    """Build the subnet4 list and any generated per-subnet CatchAll classes.

    Subnet ID rules (all-or-none):
        - If ANY subnet has an explicit id, ALL subnets must have explicit IDs.
          Raises ValueError if the mix is detected (would cause collisions).
        - If NO subnet has an explicit id, IDs are auto-assigned sequentially
          from 1 in YAML order.

    CatchAll generation (DHCPv4): when a subnet has both class-restricted pools
    and at least one unguarded pool, a per-subnet `CatchAll_<id>` class is
    synthesized with test `not member(<each used class>)` and attached to the
    formerly-unguarded pool(s). This stops the catch-all pool from also serving
    clients that match a restricted class (Kea KB
    understanding-client-classification.md:188-235). The generated class dicts are
    returned for the caller to append AFTER the device classes (member() requires
    its referents to be evaluated first).

    Subnet key order (Kea-natural):
        id → subnet → valid-lifetime → renew-timer → rebind-timer → client-classes
        → option-data → pools → reservations

    Returns:
        (subnet4_list, catchall_class_dicts)
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
    catchall_classes: list[dict] = []
    next_id = 1
    # Names the user references anywhere — used to reject a config that manually
    # uses a reserved `CatchAll_<id>` name (which the builder owns) rather than
    # silently rebinding the user's selector to the generated class.
    referenced_class_names = _referenced_class_names(dhcp4)

    for subnet in dhcp4.subnets:
        profile: OptionProfileModel | None = None
        if subnet.option_profile is not None:
            profile = _resolve_option_profile(subnet.option_profile, option_profiles)

        if subnet.id is not None:
            assigned_id = subnet.id
        else:
            assigned_id = next_id
            next_id += 1

        # Decide whether this subnet qualifies for a generated CatchAll: it must
        # have at least one guarded pool AND at least one unguarded pool. The
        # unguarded pool(s) then get guarded by the synthesized CatchAll class.
        used_classes = _subnet_used_pool_classes(subnet)
        has_unguarded_pool = any(not pool.client_class for pool in (subnet.pools or []))
        catchall_name: str | None = None
        if used_classes and has_unguarded_pool:
            catchall_name = _catchall_name(assigned_id, referenced_class_names)
            catchall_classes.append(
                {"name": catchall_name, "test": _make_catchall_test(used_classes)}
            )

        subnet_dict: dict = {
            "id": assigned_id,
            "subnet": subnet.subnet,
        }

        # Per-subnet timers (omit if not set)
        valid_lifetime = subnet.valid_lifetime
        renew_timer = subnet.renew_timer
        rebind_timer = subnet.rebind_timer
        if profile is not None:
            if valid_lifetime is None:
                valid_lifetime = profile.valid_lifetime
            if renew_timer is None:
                renew_timer = profile.renew_timer
            if rebind_timer is None:
                rebind_timer = profile.rebind_timer
        if valid_lifetime is not None:
            subnet_dict["valid-lifetime"] = valid_lifetime
        if renew_timer is not None:
            subnet_dict["renew-timer"] = renew_timer
        if rebind_timer is not None:
            subnet_dict["rebind-timer"] = rebind_timer

        # Subnet-level client-class selector. Kea 3.0 renamed the singular
        # "client-class" string to the list-form "client-classes"; the singular
        # form is deprecated (BaseNetworkParser::getClientClassesElem logs
        # DHCPSRV_CLIENT_CLASS_DEPRECATED and rejects setting both), so we emit
        # the list form only — consistent with pools and host reservations.
        if subnet.client_class:
            subnet_dict["client-classes"] = [subnet.client_class]

        # Subnet-specific option-data; option profiles contribute the base.
        subnet_option_data = _scope_option_data(subnet)
        if profile is not None:
            subnet_option_data = merge_option_data(
                _scalar_to_option_data(profile),
                subnet_option_data,
            )
        if subnet_option_data:
            subnet_dict["option-data"] = subnet_option_data

        # Pools. When a CatchAll class was synthesized for this subnet, attach it
        # to each unguarded pool so the catch-all range serves only unmatched
        # clients (Kea evaluates the generated `not member(...)` test).
        if subnet.pools:
            pool_entries: list[dict] = []
            for pool in subnet.pools:
                entry = _build_pool_entry(pool, subnet.subnet)
                if catchall_name is not None and not pool.client_class:
                    entry["client-classes"] = [catchall_name]
                pool_entries.append(entry)
            subnet_dict["pools"] = pool_entries

        # Reservations (after pools, Kea-natural order)
        if subnet.reservations:
            subnet_dict["reservations"] = [_build_reservation_entry(r) for r in subnet.reservations]

        subnets.append(subnet_dict)

    return subnets, catchall_classes
