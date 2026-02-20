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
    """Create all tables if they don't exist yet."""
    Base.metadata.create_all(bind=engine)


def get_db():
    """FastAPI dependency injection — yields a DB session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
