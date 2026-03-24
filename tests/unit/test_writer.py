"""Unit tests for writer.py — filename logic, header, determinism, error cases."""

import json
from datetime import datetime

from kea_dhcp_config_generator import writer

_FIXED_TS = datetime(2026, 3, 21, 14, 30)  # for deterministic filename tests
_SIMPLE_DICT = {"Dhcp4": {"valid-lifetime": 3600}}


# ---------------------------------------------------------------------------
# AC #1: Default timestamped filename
# ---------------------------------------------------------------------------


def test_default_filename_contains_timestamp(tmp_path):
    """AC #1: default mode produces timestamped filename."""
    path = writer.write(_SIMPLE_DICT, "dhcp4", tmp_path, timestamp=_FIXED_TS)
    assert path.name == "kea-dhcp4-20260321-1430.conf"


def test_default_file_is_created(tmp_path):
    """AC #1: file is actually created on disk."""
    path = writer.write(_SIMPLE_DICT, "dhcp4", tmp_path, timestamp=_FIXED_TS)
    assert path.exists()


# ---------------------------------------------------------------------------
# AC #2: --overwrite
# ---------------------------------------------------------------------------


def test_overwrite_produces_canonical_filename(tmp_path):
    """AC #2: --overwrite → kea-dhcp4.conf."""
    path = writer.write(_SIMPLE_DICT, "dhcp4", tmp_path, overwrite=True)
    assert path.name == "kea-dhcp4.conf"


def test_overwrite_silently_replaces_existing_file(tmp_path):
    """AC #2: --overwrite does not raise when file already exists, new content written."""
    canonical = tmp_path / "kea-dhcp4.conf"
    canonical.write_text("old content")
    writer.write(_SIMPLE_DICT, "dhcp4", tmp_path, overwrite=True)
    doc = json.loads(canonical.read_text())
    assert "Dhcp4" in doc
    assert doc["Dhcp4"]["valid-lifetime"] == 3600


# ---------------------------------------------------------------------------
# AC #4: Determinism
# ---------------------------------------------------------------------------


def test_determinism_same_inputs_byte_identical(tmp_path):
    """AC #4: identical config + timestamp → byte-identical output files."""
    p1 = tmp_path / "run1"
    p1.mkdir()
    p2 = tmp_path / "run2"
    p2.mkdir()
    path1 = writer.write(_SIMPLE_DICT, "dhcp4", p1, timestamp=_FIXED_TS)
    path2 = writer.write(_SIMPLE_DICT, "dhcp4", p2, timestamp=_FIXED_TS)
    assert path1.read_bytes() == path2.read_bytes()


# ---------------------------------------------------------------------------
# AC #5: Auto-generated header
# ---------------------------------------------------------------------------


def test_header_key_present(tmp_path):
    """AC #5: top-level _kea-config-generator key in output JSON."""
    path = writer.write(_SIMPLE_DICT, "dhcp4", tmp_path, timestamp=_FIXED_TS)
    doc = json.loads(path.read_text())
    assert "_kea-config-generator" in doc


def test_header_warning_field_present(tmp_path):
    """AC #5: header contains non-empty warning field."""
    path = writer.write(_SIMPLE_DICT, "dhcp4", tmp_path, timestamp=_FIXED_TS)
    doc = json.loads(path.read_text())
    assert doc["_kea-config-generator"].get("warning")


# ---------------------------------------------------------------------------
# Additional correctness tests
# ---------------------------------------------------------------------------


def test_config_dict_preserved_in_output(tmp_path):
    """The original config dict is present under its top-level key."""
    path = writer.write(_SIMPLE_DICT, "dhcp4", tmp_path, timestamp=_FIXED_TS)
    doc = json.loads(path.read_text())
    assert "Dhcp4" in doc
    assert doc["Dhcp4"]["valid-lifetime"] == 3600


def test_key_order_header_before_config(tmp_path):
    """json.dumps must not sort keys — header key appears before Dhcp4."""
    path = writer.write(_SIMPLE_DICT, "dhcp4", tmp_path, timestamp=_FIXED_TS)
    doc = json.loads(path.read_text())
    keys = list(doc.keys())
    assert keys[0] == "_kea-config-generator"
    assert keys[1] == "Dhcp4"


def test_output_file_ends_with_newline(tmp_path):
    """POSIX: output file must end with a newline."""
    path = writer.write(_SIMPLE_DICT, "dhcp4", tmp_path, timestamp=_FIXED_TS)
    assert path.read_bytes().endswith(b"\n")


def test_output_is_valid_utf8(tmp_path):
    """Output file is valid UTF-8 (explicit encoding on write)."""
    path = writer.write(_SIMPLE_DICT, "dhcp4", tmp_path, timestamp=_FIXED_TS)
    path.read_text(encoding="utf-8")  # raises UnicodeDecodeError if not valid UTF-8
