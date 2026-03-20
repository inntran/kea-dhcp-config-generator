"""Exception hierarchy for kea-dhcp-config-generator.

The full ConfigError / ConfigWarning dataclass infrastructure is added in Story 4.1.
This module is extended in-place; the KeaConfigError base class is stable.
"""


class KeaConfigError(Exception):
    """Fatal configuration error raised by the loader and validation layers.

    Raised for conditions that prevent any further processing:
    missing file, YAML syntax error, unexpected top-level type.
    """

    def __init__(self, message: str, path: str | None = None) -> None:
        super().__init__(message)
        self.path = path
