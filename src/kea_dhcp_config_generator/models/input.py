"""Pydantic v2 input models for kea-dhcp-config-generator.

Validates user YAML configuration (loaded by loader.py as a CommentedMap) into
strongly-typed Python models. The parse() function serves as the structural
validation entry point for the pipeline:

    CommentedMap (from loader.py)
        ↓ parse()
    GlobalConfig (Pydantic models)
        ↓ builders/
    BuiltConfig (Kea JSON dicts)

YAML key conventions:
  - Kea-native options use hyphenated keys (dns-servers, valid-lifetime, hw-address)
  - Tool-invented structural keys use snake_case (option_profiles, fingerprint_library_version)
  - Python attributes are always snake_case; Field(alias=) maps from hyphenated YAML keys
"""

from __future__ import annotations

import re
from ipaddress import IPv6Network
from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)
from ruamel.yaml.comments import CommentedMap

from kea_dhcp_config_generator.validation.errors import ConfigError

# ---------------------------------------------------------------------------
# ASCII string type
# ---------------------------------------------------------------------------


def _require_ascii(v: str) -> str:
    if not v.isascii():
        raise ValueError("must contain only ASCII characters")
    return v


AsciiStr = Annotated[str, AfterValidator(_require_ascii)]


# ---------------------------------------------------------------------------
# Duration parsing
# ---------------------------------------------------------------------------

_DURATION_RE = re.compile(r"^(\d+)(d|h|m|s)?$")
_DURATION_MULTIPLIERS = {"d": 86400, "h": 3600, "m": 60, "s": 1}


def _parse_duration(v: Any) -> int:
    """Convert a duration value to integer seconds.

    Accepts:
        int: returned unchanged (e.g. 3600 → 3600)
        str: with optional unit suffix — "24h" → 86400, "30m" → 1800, "86400" → 86400

    Raises:
        ValueError: if the string format is invalid; Pydantic wraps this in
            ValidationError which parse() converts to ConfigError.
    """
    if isinstance(v, bool):
        raise ValueError(f"Duration must be int or str, got {type(v).__name__}")
    if isinstance(v, int):
        if v < 0:
            raise ValueError(f"Duration must be non-negative, got {v}")
        return v
    if isinstance(v, str):
        m = _DURATION_RE.fullmatch(v.strip())
        if not m:
            raise ValueError(
                f"Invalid duration {v!r}. Expected integer seconds or a string like "
                f"'24h', '30m', '3600', '1d'. Supported units: d, h, m, s."
            )
        n, unit = int(m.group(1)), m.group(2) or "s"
        return n * _DURATION_MULTIPLIERS[unit]
    raise ValueError(f"Duration must be int or str, got {type(v).__name__}")


# ---------------------------------------------------------------------------
# YAML line extraction helpers (used by parse())
# ---------------------------------------------------------------------------


def _format_yaml_path(loc: tuple) -> str:
    """Convert a Pydantic error loc tuple to a dot-notation YAML path string.

    Examples:
        ("dhcp4", "valid_lifetime") → "dhcp4.valid_lifetime"
        ("dhcp4", "subnets", 0, "pools", 1) → "dhcp4.subnets[0].pools[1]"
    """
    if not loc:
        return "<config root>"
    parts: list[str] = []
    for key in loc:
        if isinstance(key, int):
            if parts:
                parts[-1] += f"[{key}]"
            else:
                parts.append(f"[{key}]")
        else:
            parts.append(str(key))
    return ".".join(parts)


def _extract_line(raw: CommentedMap, loc: tuple) -> int | None:
    """Walk a CommentedMap using a Pydantic error loc and return the 1-indexed YAML line.

    Returns None if the path can't be followed or has no .lc metadata.
    Line numbers from ruamel.yaml are 0-indexed; we add 1 for user-facing display.

    Only the terminal key's line is returned; intermediate keys are used solely
    for navigation so that nested errors point to the erroneous field, not a parent.
    """
    node: Any = raw
    keys = list(loc)
    for i, key in enumerate(keys):
        if node is None:
            return None
        is_last = i == len(keys) - 1
        if isinstance(key, int):
            # List index — CommentedSeq has .lc.item(i) → (line, col)
            if is_last and hasattr(node, "lc") and hasattr(node.lc, "item"):
                try:
                    line, _ = node.lc.item(key)
                    return line + 1
                except (IndexError, AttributeError, TypeError):
                    pass
            if isinstance(node, list) and key < len(node):
                node = node[key]
            else:
                return None
        else:
            # String key — CommentedMap has .lc.key(key) → (line, col)
            if is_last and hasattr(node, "lc"):
                try:
                    line, _ = node.lc.key(key)
                    return line + 1
                except (KeyError, AttributeError, TypeError):
                    pass
            if isinstance(node, dict) and key in node:
                node = node[key]
            else:
                return None
    return None


# ---------------------------------------------------------------------------
# Option profile model
# ---------------------------------------------------------------------------


class OptionProfileModel(BaseModel):
    """Reusable named option set referenced by subnets (FR2)."""

    model_config = ConfigDict(populate_by_name=True)

    valid_lifetime: int | None = Field(None, alias="valid-lifetime")
    preferred_lifetime: int | None = Field(None, alias="preferred-lifetime")
    renew_timer: int | None = Field(None, alias="renew-timer")
    rebind_timer: int | None = Field(None, alias="rebind-timer")
    dns_servers: list[AsciiStr] = Field(default_factory=list, alias="dns-servers")
    domain_name: AsciiStr | None = Field(None, alias="domain-name")
    ntp_servers: list[AsciiStr] = Field(default_factory=list, alias="ntp-servers")
    routers: list[AsciiStr] = Field(default_factory=list)

    @field_validator(
        "valid_lifetime",
        "preferred_lifetime",
        "renew_timer",
        "rebind_timer",
        mode="before",
    )
    @classmethod
    def parse_duration_field(cls, v: Any) -> int | None:
        if v is None:
            return None
        return _parse_duration(v)


# ---------------------------------------------------------------------------
# Control Socket model
# ---------------------------------------------------------------------------


class ControlSocketModel(BaseModel):
    """Control socket configuration for API access (Kea 3.2.0+)."""

    model_config = ConfigDict(populate_by_name=True)

    socket_type: Literal["unix", "http", "https"] = Field(..., alias="socket-type")
    socket_name: AsciiStr | None = Field(None, alias="socket-name")
    socket_address: AsciiStr | None = Field(None, alias="socket-address")
    socket_port: int | None = Field(None, alias="socket-port", ge=1, le=65535)

    @model_validator(mode="after")
    def validate_socket_config(self) -> "ControlSocketModel":
        """Ensure unix sockets have socket-name; http(s) have address."""
        if self.socket_type == "unix" and not self.socket_name:
            raise ValueError("unix socket requires 'socket-name'")
        if self.socket_type in ("http", "https"):
            if not self.socket_address:
                raise ValueError(f"{self.socket_type} socket requires 'socket-address'")
        return self


# ---------------------------------------------------------------------------
# Hooks Library model
# ---------------------------------------------------------------------------


class HooksLibraryModel(BaseModel):
    """Hook library configuration (Kea 3.2.0+)."""

    model_config = ConfigDict(populate_by_name=True)

    library: AsciiStr = Field(...)
    parameters: dict[str, Any] | None = Field(None)


# ---------------------------------------------------------------------------
# Interfaces Config model
# ---------------------------------------------------------------------------


class InterfacesConfigModel(BaseModel):
    """Network interfaces configuration (Kea 3.2.0+)."""

    model_config = ConfigDict(populate_by_name=True)

    interfaces: list[AsciiStr] = Field(...)
    dhcp_socket_type: Literal["udp", "raw"] | None = Field(None, alias="dhcp-socket-type")
    outbound_interface: AsciiStr | None = Field(None, alias="outbound-interface")


# ---------------------------------------------------------------------------
# DHCPv4 pool model
# ---------------------------------------------------------------------------


class PoolV4Model(BaseModel):
    """IPv4 DHCP pool configuration (FR7, FR9)."""

    model_config = ConfigDict(populate_by_name=True)

    range: AsciiStr  # "auto" or "x.x.x.x - x.x.x.x"
    skip_start: int = Field(0, alias="skip-start")
    skip_end: int = Field(0, alias="skip-end")
    client_class: AsciiStr | None = Field(None, alias="client-class")


# ---------------------------------------------------------------------------
# DHCPv6 pool models (discriminated union on `pool-type`)
# ---------------------------------------------------------------------------


class PoolV6NaModel(BaseModel):
    """IPv6 non-temporary address (NA) pool (FR15).

    Discriminated-union member keyed on ``pool-type: na``. Mirrors PoolV4Model's
    ``range`` semantics ("auto" or "<start> - <end>") for IPv6 addresses.
    """

    model_config = ConfigDict(populate_by_name=True)

    pool_type: Literal["na"] = Field(alias="pool-type")
    range: AsciiStr  # "auto" or "<start> - <end>"
    client_class: AsciiStr | None = Field(None, alias="client-class")


class PoolV6PdModel(BaseModel):
    """IPv6 Prefix Delegation (PD) pool (FR16).

    Discriminated-union member keyed on ``pool-type: pd``. Carries the base
    ``prefix``/``prefix-len`` and the ``delegated-len`` handed to clients.
    """

    model_config = ConfigDict(populate_by_name=True)

    pool_type: Literal["pd"] = Field(alias="pool-type")
    prefix: AsciiStr
    prefix_len: int = Field(alias="prefix-len")
    delegated_len: int = Field(alias="delegated-len")
    client_class: AsciiStr | None = Field(None, alias="client-class")


PoolV6Model = Annotated[
    PoolV6NaModel | PoolV6PdModel,
    Field(discriminator="pool_type"),
]


# ---------------------------------------------------------------------------
# Host reservation models
# ---------------------------------------------------------------------------


class HostReservationV4Model(BaseModel):
    """MAC-based host reservation for DHCPv4 (FR11)."""

    model_config = ConfigDict(populate_by_name=True)

    hw_address: AsciiStr = Field(alias="hw-address")
    ip_address: AsciiStr = Field(alias="ip-address")
    hostname: AsciiStr | None = None
    option_data: list[dict[str, Any]] = Field(default_factory=list, alias="option-data")


class HostReservationV6Model(BaseModel):
    """DUID-based host reservation for DHCPv6 (FR17)."""

    model_config = ConfigDict(populate_by_name=True)

    duid: AsciiStr
    ip_address: AsciiStr | None = Field(None, alias="ip-address")
    hostname: AsciiStr | None = None
    option_data: list[dict[str, Any]] = Field(default_factory=list, alias="option-data")


# ---------------------------------------------------------------------------
# Subnet models
# ---------------------------------------------------------------------------


class SubnetV4Model(BaseModel):
    """DHCPv4 subnet with pools and reservations (FR6–FR13)."""

    model_config = ConfigDict(populate_by_name=True)

    subnet: AsciiStr  # CIDR: "10.0.1.0/24"
    id: int | None = Field(None, ge=1)  # auto-assigned by builder if absent (FR13)
    pools: list[PoolV4Model] = Field(default_factory=list)
    reservations: list[HostReservationV4Model] = Field(default_factory=list)
    option_profile: AsciiStr | None = None  # tool-invented key; no alias needed
    valid_lifetime: int | None = Field(None, alias="valid-lifetime")
    renew_timer: int | None = Field(None, alias="renew-timer")
    rebind_timer: int | None = Field(None, alias="rebind-timer")
    dns_servers: list[AsciiStr] = Field(default_factory=list, alias="dns-servers")
    domain_name: AsciiStr | None = Field(None, alias="domain-name")
    ntp_servers: list[AsciiStr] = Field(default_factory=list, alias="ntp-servers")
    routers: list[AsciiStr] = Field(default_factory=list)
    client_class: AsciiStr | None = Field(None, alias="client-class")
    option_data: list[dict[str, Any]] = Field(default_factory=list, alias="option-data")

    @field_validator("valid_lifetime", "renew_timer", "rebind_timer", mode="before")
    @classmethod
    def parse_duration_field(cls, v: Any) -> int | None:
        if v is None:
            return None
        return _parse_duration(v)


class SubnetV6Model(BaseModel):
    """DHCPv6 subnet with NA pools, PD pools, and reservations (FR14–FR18).

    ``pools`` is a single list whose entries are a discriminated union of NA and
    PD pools keyed on ``pool-type``; there is no separate ``pd-pools`` field.
    ``subnet`` is stored as an ``ipaddress.IPv6Network`` so downstream builders
    and semantic validation can call ``.network_address``/``.subnets()`` directly.
    """

    model_config = ConfigDict(populate_by_name=True)

    subnet: IPv6Network  # IPv6 CIDR: "2001:db8::/48"
    id: int | None = Field(None, ge=1)
    pools: list[PoolV6Model] = Field(default_factory=list)
    reservations: list[HostReservationV6Model] = Field(default_factory=list)
    option_profile: AsciiStr | None = None
    valid_lifetime: int | None = Field(None, alias="valid-lifetime")
    preferred_lifetime: int | None = Field(None, alias="preferred-lifetime")
    renew_timer: int | None = Field(None, alias="renew-timer")
    rebind_timer: int | None = Field(None, alias="rebind-timer")
    dns_servers: list[AsciiStr] = Field(default_factory=list, alias="dns-servers")
    option_data: list[dict[str, Any]] = Field(default_factory=list, alias="option-data")

    @field_validator("subnet", mode="before")
    @classmethod
    def parse_ipv6_prefix(cls, v: Any) -> IPv6Network:
        """Coerce a YAML string into an IPv6Network, rejecting non-IPv6 prefixes.

        A bare address without an explicit ``/len`` is rejected (a /128 "subnet"
        is almost certainly a typo at the YAML layer). Host bits are tolerated
        (``strict=False``); Kea canonicalises later.
        """
        if isinstance(v, IPv6Network):
            return v
        if not isinstance(v, str):
            # ValueError (not TypeError) so Pydantic wraps it into ValidationError
            # and parse() can surface it through the collect-all ConfigError path.
            raise ValueError(
                f"must be a valid IPv6 prefix (e.g. '2001:db8::/48'), "
                f"got non-string {type(v).__name__}"
            )
        if "/" not in v:
            raise ValueError(f"must be a valid IPv6 prefix (e.g. '2001:db8::/48'), got {v!r}")
        try:
            return IPv6Network(v, strict=False)
        except ValueError as exc:  # AddressValueError/NetmaskValueError are subclasses
            raise ValueError(
                f"must be a valid IPv6 prefix (e.g. '2001:db8::/48'), got {v!r}"
            ) from exc

    @field_validator(
        "valid_lifetime", "preferred_lifetime", "renew_timer", "rebind_timer", mode="before"
    )
    @classmethod
    def parse_duration_field(cls, v: Any) -> int | None:
        if v is None:
            return None
        return _parse_duration(v)


# ---------------------------------------------------------------------------
# Protocol-level config models
# ---------------------------------------------------------------------------


class Dhcp4Config(BaseModel):
    """Global DHCPv4 configuration section."""

    model_config = ConfigDict(populate_by_name=True)

    valid_lifetime: int | None = Field(None, alias="valid-lifetime")
    renew_timer: int | None = Field(None, alias="renew-timer")
    rebind_timer: int | None = Field(None, alias="rebind-timer")
    dns_servers: list[AsciiStr] = Field(default_factory=list, alias="dns-servers")
    domain_name: AsciiStr | None = Field(None, alias="domain-name")
    ntp_servers: list[AsciiStr] = Field(default_factory=list, alias="ntp-servers")
    routers: list[AsciiStr] = Field(default_factory=list)
    option_data: list[dict[str, Any]] = Field(default_factory=list, alias="option-data")
    subnets: list[SubnetV4Model] = Field(default_factory=list)

    @field_validator("valid_lifetime", "renew_timer", "rebind_timer", mode="before")
    @classmethod
    def parse_duration_field(cls, v: Any) -> int | None:
        if v is None:
            return None
        return _parse_duration(v)


class Dhcp6Config(BaseModel):
    """Global DHCPv6 configuration section."""

    model_config = ConfigDict(populate_by_name=True)

    valid_lifetime: int | None = Field(None, alias="valid-lifetime")
    preferred_lifetime: int | None = Field(None, alias="preferred-lifetime")
    renew_timer: int | None = Field(None, alias="renew-timer")
    rebind_timer: int | None = Field(None, alias="rebind-timer")
    dns_servers: list[AsciiStr] = Field(default_factory=list, alias="dns-servers")
    option_data: list[dict[str, Any]] = Field(default_factory=list, alias="option-data")
    subnets: list[SubnetV6Model] = Field(default_factory=list)

    @field_validator(
        "valid_lifetime", "preferred_lifetime", "renew_timer", "rebind_timer", mode="before"
    )
    @classmethod
    def parse_duration_field(cls, v: Any) -> int | None:
        if v is None:
            return None
        return _parse_duration(v)


# ---------------------------------------------------------------------------
# Top-level model
# ---------------------------------------------------------------------------


class GlobalConfig(BaseModel):
    """Top-level validated configuration model.

    At least one of dhcp4 or dhcp6 must be present (FR1).
    Tool-invented top-level keys use snake_case (no alias needed).
    """

    model_config = ConfigDict(populate_by_name=True)

    dhcp4: Dhcp4Config | None = None
    dhcp6: Dhcp6Config | None = None
    option_profiles: dict[str, OptionProfileModel] = Field(default_factory=dict)
    fingerprint_library_version: AsciiStr | None = None

    @model_validator(mode="after")
    def require_at_least_one_protocol(self) -> GlobalConfig:
        if self.dhcp4 is None and self.dhcp6 is None:
            raise ValueError(
                "At least one of 'dhcp4' or 'dhcp6' must be defined. "
                "Neither was found in the configuration."
            )
        return self


# ---------------------------------------------------------------------------
# Structural validation entry point
# ---------------------------------------------------------------------------


def parse(raw: CommentedMap) -> GlobalConfig:
    """Parse and validate a raw CommentedMap into a GlobalConfig.

    Collects ALL structural validation errors in a single pass (collect-all pattern).
    Pydantic v2 already collects all field-level errors; this function converts them
    to ConfigError instances enriched with YAML source line numbers from .lc metadata.

    Args:
        raw: CommentedMap returned by loader.load(). Must carry .lc line metadata.

    Returns:
        Fully validated GlobalConfig instance.

    Raises:
        ExceptionGroup: containing one ConfigError per structural validation failure.
            Each ConfigError carries a yaml_path and a YAML line number when available.
    """
    try:
        config = GlobalConfig.model_validate(raw)
    except ValidationError as exc:
        errors: list[ConfigError] = []
        for error in exc.errors():
            loc = error["loc"]
            yaml_path = _format_yaml_path(loc)
            line = _extract_line(raw, loc)
            msg = error["msg"]
            # Strip Pydantic's "Value error, " prefix only when it originates from
            # a user-defined validator (type == "value_error") for clean user output.
            if error.get("type") == "value_error" and msg.startswith("Value error, "):
                msg = msg[len("Value error, ") :]
            errors.append(
                ConfigError(
                    message=msg,
                    yaml_path=yaml_path,
                    line=line,
                    suggestion=None,
                )
            )
        if errors:
            raise ExceptionGroup("Structural validation failed", errors) from exc
        raise exc
    return config
