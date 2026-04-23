"""Exception hierarchy for kea-dhcp-config-generator.

The full ConfigError / ConfigWarning dataclass infrastructure is added in Story 4.1.
This module is extended in-place; the KeaConfigError base class is stable.
"""

from dataclasses import dataclass, field
from typing import Any


class KeaConfigError(Exception):
    """Fatal configuration error raised by the loader and validation layers.

    Raised for conditions that prevent any further processing:
    missing file, YAML syntax error, unexpected top-level type.
    """

    def __init__(self, message: str, path: str | None = None) -> None:
        super().__init__(message)
        self.path = path


@dataclass
class ConfigError(Exception):
    """Structural or semantic validation error carrying YAML source location.

    Used in ExceptionGroup to collect all errors in a single validation pass.
    Story 4.1 adds subclasses (SubnetConfigError, OptionDataError, FingerprintError)
    and ConfigWarning. Do not add those subclasses here.
    """

    message: str
    yaml_path: str          # dot-notation path: "dhcp4.subnets[0].valid-lifetime"
    line: int | None        # 1-indexed YAML source line; None if unavailable
    suggestion: str | None  # optional fix guidance; None if not applicable

    def __post_init__(self) -> None:
        super().__init__(self.message)

    def __str__(self) -> str:
        if self.line is not None:
            base = f"Line {self.line}: {self.yaml_path} — {self.message}"
        else:
            base = f"{self.yaml_path} — {self.message}"
        if self.suggestion:
            return f"{base} (Suggestion: {self.suggestion})"
        return base


@dataclass
class SubnetConfigError(ConfigError):
    """Subnet-specific validation error (overlapping CIDRs, pool out of bounds, etc.)."""


@dataclass
class OptionDataError(ConfigError):
    """Option-data validation error."""


@dataclass
class FingerprintError(ConfigError):
    """Fingerprint rule name resolution error (unknown class, fuzzy match suggestion)."""


@dataclass
class ConfigWarning:
    """Non-fatal configuration warning (version mismatch, graceful degradation).

    Story 4.1 adds the full validation infrastructure (SubnetConfigError,
    OptionDataError, FingerprintError, ValidationResult). This story adds only
    ConfigWarning, which is needed by DHCPFingerprint for version pinning.
    """

    message: str
    yaml_path: str          # dot-notation: "fingerprint_library_version"
    line: int | None        # None when warning has no YAML source line
    suggestion: str | None  # optional fix guidance


@dataclass
class ValidationResult:
    """Aggregated result of a validation pass.

    is_valid is True iff errors is empty.
    warnings is non-fatal; does not affect is_valid.
    """

    errors: list[ConfigError] = field(default_factory=list)
    warnings: list[ConfigWarning] = field(default_factory=list)
    validated_config: Any | None = None
    is_valid: bool = field(init=False)

    def __post_init__(self) -> None:
        self.is_valid = len(self.errors) == 0
