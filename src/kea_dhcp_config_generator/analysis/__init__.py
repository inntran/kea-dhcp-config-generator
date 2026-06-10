"""Analysis and reporting module for kea-dhcp-config-generator.

Public API:
    generate_report(config: GlobalConfig, validation_result: ValidationResult | None = None) -> str
        Pure function that returns a human-readable analysis report as a string.
"""

from kea_dhcp_config_generator.analysis.report import generate_report

__all__ = ["generate_report"]
