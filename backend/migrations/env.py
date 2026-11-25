
from logging.config import fileConfig

from alembic import context

from backend.database import Base, get_engine
from backend import models  # noqa: F401 - Register all models


# Alembic configuration
config = context.config

# Configure logging
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


# Register SQLAlchemy model metadata
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """
    Generate SQL migrations without opening a database connection.
    """

    engine = get_engine()

    context.configure(
        url=engine.url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """
    Run migrations against the configured Neon PostgreSQL database.
    """

    engine = get_engine()

    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )

        with context.begin_transaction():
            context.run_migrations()

    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
