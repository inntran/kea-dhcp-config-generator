"""YAML configuration loader.

Loads user YAML using ruamel.yaml round-trip mode (YAML 1.2) to preserve
.lc line/column metadata on all CommentedMap nodes. This metadata is threaded
downstream into every error message produced by the validation layers.
"""

from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.parser import ParserError
from ruamel.yaml.scanner import ScannerError

from kea_dhcp_config_generator.validation.errors import KeaConfigError


def load(path: Path) -> CommentedMap:
    """Load and return a YAML configuration file as a CommentedMap.

    Args:
        path: Path to the YAML configuration file.

    Returns:
        CommentedMap with .lc line/column metadata on all nodes.

    Raises:
        KeaConfigError: If the file does not exist, contains invalid YAML,
            or the top-level document is not a YAML mapping.
    """
    if not path.exists():
        raise KeaConfigError(f"Configuration file not found: {path}", path=str(path))

    yaml = YAML()  # round-trip mode; YAML 1.2 is default in ruamel.yaml >= 0.18
    try:
        with path.open() as fh:
            data = yaml.load(fh)
    except (ScannerError, ParserError) as exc:
        raise KeaConfigError(f"YAML syntax error in {path}: {exc}", path=str(path)) from exc
    except OSError as exc:
        raise KeaConfigError(
            f"Cannot read configuration file {path}: {exc}", path=str(path)
        ) from exc

    if data is None:
        raise KeaConfigError(f"Configuration file is empty: {path}", path=str(path))

    if not isinstance(data, CommentedMap):
        got = "YAML sequence (list)" if isinstance(data, CommentedSeq) else type(data).__name__
        raise KeaConfigError(
            f"Expected a YAML mapping at the top level of {path}, got {got}",
            path=str(path),
        )

    return data
