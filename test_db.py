from sqlalchemy import create_engine, text
from config import DATABASE_URL
import sys

print(f"Testing connection to: {DATABASE_URL.split('@')[-1] if '@' in DATABASE_URL else 'LOCAL DB'}")

# Fix postgres:// -> postgresql:// for SQLAlchemy
if DATABASE_URL.startswith("postgres://"):
    print("WARNING: 'postgres://' is deprecated in SQLAlchemy. Auto-converting to 'postgresql://'...")
    url = DATABASE_URL.replace("postgres://", "postgresql://", 1)
else:
    url = DATABASE_URL

try:
    engine = create_engine(url)
    with engine.connect() as conn:
        result = conn.execute(text("SELECT 1"))
        print(f"Connection Successful! Result: {result.scalar()}")
except Exception as e:
    print(f"CONNECTION FAILED: {e}")
    sys.exit(1)
