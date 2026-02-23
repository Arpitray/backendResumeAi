"""
database.py
-----------
Async SQLAlchemy engine, session factory, and get_db dependency.
All other modules import `get_db` and `Base` from here.
"""

import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set in environment variables.")

# Ensure the URL uses the asyncpg driver if it's a postgresql URL
if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

# Fix for Neon/asyncpg: replace sslmode=require with ssl=require
if "sslmode=require" in DATABASE_URL:
    DATABASE_URL = DATABASE_URL.replace("sslmode=require", "ssl=require")

# ── Engine ────────────────────────────────────────────────────────────────────
# pool_pre_ping=True automatically reconnects dropped connections
engine = create_async_engine(
    DATABASE_URL,
    echo=False,          # set True temporarily to debug SQL queries
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)

# ── Session factory ───────────────────────────────────────────────────────────
AsyncSessionLocal = sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)

# ── Base for ORM models ───────────────────────────────────────────────────────
Base = declarative_base()


# ── FastAPI dependency ────────────────────────────────────────────────────────
async def get_db() -> AsyncSession:
    """
    Yields an async database session.
    Connection errors (Postgres not running) surface as HTTP 503.
    All other errors (validation, logic, etc.) propagate unchanged.
    """
    from fastapi import HTTPException
    import asyncpg

    try:
        async with AsyncSessionLocal() as session:
            yield session
            await session.commit()
    except HTTPException:
        # FastAPI HTTP exceptions from endpoint code — pass through untouched
        raise
    except (OSError, asyncpg.exceptions.CannotConnectNowError,
            asyncpg.exceptions.InvalidPasswordError,
            ConnectionRefusedError) as e:
        # Genuine DB connection failures → 503
        raise HTTPException(
            status_code=503,
            detail=f"Database unavailable. Ensure PostgreSQL is running on port 5432. ({type(e).__name__})",
        )
    except Exception:
        # Everything else (bcrypt errors, validation errors, etc.) — rollback and re-raise as-is
        try:
            await session.rollback()
        except Exception:
            pass
        raise
