"""Exception hierarchy for kea-dhcp-config-generator.

The full ConfigError / ConfigWarning dataclass infrastructure is added in Story 4.1.
This module is extended in-place; the KeaConfigError base class is stable.
"""

from dataclasses import dataclass


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
