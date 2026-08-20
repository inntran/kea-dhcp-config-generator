"""Tests for LeaseDbModel (Kea 3.2.0+ lease database configuration)."""

import pytest
from pydantic import ValidationError

from kea_dhcp_config_generator.models.input import LeaseDbModel


def test_memfile_database_minimal():
    """Memfile database (default)."""
    data = {"type": "memfile"}
    model = LeaseDbModel(**data)
    assert model.type == "memfile"
    assert model.persist is None
    assert model.name is None


def test_memfile_database_with_persist():
    """Memfile with persist flag."""
    data = {
        "type": "memfile",
        "persist": True,
        "name": "/var/lib/kea/dhcp4.leases"
    }
    model = LeaseDbModel(**data)
    assert model.type == "memfile"
    assert model.persist is True
    assert model.name == "/var/lib/kea/dhcp4.leases"


def test_mysql_database():
    """MySQL database configuration."""
    data = {
        "type": "mysql",
        "name": "kea",
        "user": "kea",
        "password": "secret",
        "host": "localhost",
        "port": 3306
    }
    model = LeaseDbModel(**data)
    assert model.type == "mysql"
    assert model.host == "localhost"
    assert model.port == 3306


def test_postgresql_database():
    """PostgreSQL database configuration."""
    data = {
        "type": "postgresql",
        "name": "kea",
        "user": "kea",
        "password": "secret",
        "host": "db.example.com",
        "port": 5432
    }
    model = LeaseDbModel(**data)
    assert model.type == "postgresql"


def test_type_required():
    """Type field is required."""
    data = {"name": "kea"}
    with pytest.raises(ValidationError) as exc:
        LeaseDbModel(**data)
    assert "type" in str(exc.value).lower()


def test_invalid_type():
    """Reject invalid database type."""
    data = {"type": "sqlite"}
    with pytest.raises(ValidationError) as exc:
        LeaseDbModel(**data)
    assert "type" in str(exc.value).lower()


def test_port_range_validation():
    """Port must be in valid range."""
    data = {
        "type": "mysql",
        "host": "localhost",
        "port": 99999
    }
    with pytest.raises(ValidationError) as exc:
        LeaseDbModel(**data)
    assert "port" in str(exc.value).lower()
