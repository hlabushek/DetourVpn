"""Node country/city display metadata (migration 0020)."""

from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from pydantic import ValidationError
from sqlalchemy import Engine, inspect, text
from sqlalchemy.exc import DataError, IntegrityError

from vpn_api.db.connection import transaction
from vpn_api.nodes.catalog import NodeView
from vpn_api.nodes.contracts import ProvisionNodeInput
from vpn_api.tunnels.window_routes import WindowNodeView

ROOT = Path(__file__).resolve().parents[3]


def migration_config(engine: Engine) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.attributes["database_engine"] = engine
    return config


def provision(**extra: object) -> ProvisionNodeInput:
    return ProvisionNodeInput(idempotency_key=uuid4(), name="node-a", region="helsinki", **extra)  # type: ignore[arg-type]


def test_country_is_optional_and_normalised_to_iso_alpha2() -> None:
    assert provision().country_code is None
    assert provision(country_code="FI", city="Helsinki").country_code == "FI"
    for bad in ("fi", "FIN", "F", "F1", ""):
        with pytest.raises(ValidationError):
            provision(country_code=bad)
    with pytest.raises(ValidationError):
        provision(city="")


def test_views_default_to_unknown_country() -> None:
    node = NodeView(id=uuid4(), name="n", region="r", reason="available")
    assert (node.country_code, node.city) == (None, None)
    window = WindowNodeView(id=uuid4(), name="n", region="r", slots=1, occupied=0, available=True)
    assert window.country_code is None


@pytest.mark.postgres
def test_node_country_columns_constraint_and_rollback(engine: Engine) -> None:
    config = migration_config(engine)
    command.upgrade(config, "head")
    columns = {column["name"] for column in inspect(engine).get_columns("vpn_nodes")}
    assert {"country_code", "city"} <= columns

    def insert(country: str | None) -> None:
        with transaction(engine) as session:
            session.execute(
                text(
                    "INSERT INTO vpn_nodes (id, provisioning_key, name, region, country_code, "
                    "secret_digest) VALUES (:id, :key, 'n', 'r', :country, :digest)"
                ),
                {"id": uuid4(), "key": uuid4(), "country": country, "digest": "0" * 64},
            )

    insert(None)
    insert("DE")
    for bad in ("de", "D1", "1A"):
        with pytest.raises(IntegrityError):
            insert(bad)
    with pytest.raises(DataError):  # three letters do not even fit VARCHAR(2)
        insert("DEU")
    command.downgrade(config, "0019_account_deletion")
    columns = {column["name"] for column in inspect(engine).get_columns("vpn_nodes")}
    assert not {"country_code", "city"} & columns
    command.upgrade(config, "head")
