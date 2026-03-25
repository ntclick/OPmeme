# database/engine.py — SQLAlchemy engine, session factory, DB init

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from database.models import Base
from config import DATABASE_URL

# Fix postgres:// -> postgresql:// for SQLAlchemy compatibility (e.g. Neon, Vercel)
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(
    DATABASE_URL,
    echo=False,  # Set True to debug SQL queries
    connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {},
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db():
    """Create all tables if they don't exist yet, and migrate new columns."""
    Base.metadata.create_all(bind=engine)
    # Migrate: add new columns to existing tables (SQLite ALTER TABLE)
    _migrate_watched_coins()


def _migrate_watched_coins():
    """Add new columns to watched_coins if they don't exist (safe to re-run)."""
    from sqlalchemy import text, inspect
    insp = inspect(engine)
    if "watched_coins" not in insp.get_table_names():
        return
    existing = {col["name"] for col in insp.get_columns("watched_coins")}
    migrations = [
        ("deep_scanned", "BOOLEAN DEFAULT 0"),
        ("ai_analysis", "TEXT"),
        ("ai_scanned_at", "INTEGER"),
    ]
    with engine.begin() as conn:
        for col_name, col_def in migrations:
            if col_name not in existing:
                conn.execute(text(f"ALTER TABLE watched_coins ADD COLUMN {col_name} {col_def}"))
                print(f"  Migrated: watched_coins.{col_name}")


def get_db():
    """FastAPI dependency injection — yields a DB session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
