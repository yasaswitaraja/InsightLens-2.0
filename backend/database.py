
import os
from pathlib import Path
from functools import lru_cache

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Session


# Load environment variables from backend/.env
BACKEND_DIR = Path(__file__).resolve().parent
load_dotenv(BACKEND_DIR / ".env")


class Base(DeclarativeBase):
    """Base class for SQLAlchemy database models."""
    pass


@lru_cache(maxsize=1)
def get_engine():
    """Create and cache the Neon PostgreSQL engine."""

    raw_url = os.getenv("DATABASE_URL")

    if not raw_url:
        raise RuntimeError(
            "DATABASE_URL is missing. Check backend/.env"
        )

    # Use Psycopg 3 instead of the default PostgreSQL driver.
    database_url = make_url(raw_url)

    if database_url.drivername == "postgresql":
        database_url = database_url.set(
            drivername="postgresql+psycopg"
        )

    # Require encrypted connections to Neon.
    query = dict(database_url.query)
    query.setdefault("sslmode", "require")
    database_url = database_url.set(query=query)

    return create_engine(
        database_url,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=2,
        pool_recycle=300,
        connect_args={
            "prepare_threshold": None
        },
    )


def get_db():
    """FastAPI dependency for database sessions."""

    with Session(get_engine()) as db:
        yield db


def test_database_connection():
    """Verify that the application can connect to Neon."""

    with get_engine().connect() as connection:
        result = connection.execute(
            text(
                "SELECT current_database(), current_user, version()"
            )
        ).one()

        return {
            "status": "connected",
            "database": result[0],
            "user": result[1],
            "postgres_version": result[2].split(",")[0],
        }
