"""
models.py
---------
SQLAlchemy ORM models.

Tables:
  - users       : registered accounts (local + OAuth)
  - user_resumes: links each uploaded resume to an owner user
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    String,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from database import Base


def _utcnow():
    return datetime.now(timezone.utc)


# ── User ──────────────────────────────────────────────────────────────────────

class User(Base):
    """
    Stores every registered user regardless of sign-in method.

    provider values:
      "local"   – email + password
      "google"  – Google Sign-In
      "github"  – GitHub OAuth
    """
    __tablename__ = "users"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )
    email = Column(String, unique=True, index=True, nullable=False)
    name = Column(String, nullable=False)

    # Nullable — OAuth users have no password
    hashed_password = Column(String, nullable=True)

    # Auth provider
    provider = Column(String, nullable=False, default="local")   # local | google | github
    provider_id = Column(String, nullable=True, index=True)      # OAuth subject ID

    # Profile
    avatar_url = Column(String, nullable=True)

    # Access control
    role = Column(String, nullable=False, default="user")        # user | admin
    is_active = Column(Boolean, nullable=False, default=True)

    # Timestamps (always UTC)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
    )

    # Relationships
    resumes = relationship("UserResume", back_populates="owner", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<User id={self.id} email={self.email} provider={self.provider}>"


# ── UserResume ────────────────────────────────────────────────────────────────

class UserResume(Base):
    """
    Ownership record: links a user to a resume stored in ChromaDB / uploads/.
    Used to enforce resource-level authorization on every resume-related endpoint.
    """
    __tablename__ = "user_resumes"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # The file ID used in ChromaDB and the /uploads directory (e.g. "abc123.pdf")
    resume_id = Column(String, nullable=False, index=True)
    # Original filename as uploaded by the user
    filename = Column(String, nullable=False, default="resume.pdf")
    uploaded_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)

    # Relationship back to owner
    owner = relationship("User", back_populates="resumes")

    def __repr__(self):
        return f"<UserResume user={self.user_id} resume={self.resume_id}>"
