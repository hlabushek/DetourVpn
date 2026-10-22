from logging.config import fileConfig

from alembic import context
from sqlalchemy import Engine

from vpn_api.config import Settings
from vpn_api.db.connection import create_database_engine
from vpn_api.db.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)


def run_migrations_offline() -> None:
    context.configure(
        dialect_name="postgresql",
        target_metadata=Base.metadata,
        literal_binds=True,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    supplied = config.attributes.get("database_engine")
    if supplied is not None and not isinstance(supplied, Engine):
        raise TypeError("database_engine must be a SQLAlchemy Engine")
    engine = supplied if supplied is not None else create_database_engine(Settings())
    try:
        with engine.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=Base.metadata,
                compare_type=True,
                compare_server_default=True,
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        if supplied is None:
            engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
