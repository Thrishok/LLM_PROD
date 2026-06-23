"""Database layer: SQLModel engine + User model.

Uses Render Postgres in production (via DATABASE_URL) and falls back to a
local SQLite file for development so the app runs with zero setup.
"""

import os
from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel, Session, create_engine, select


def _database_url() -> str:
    """Resolve and normalise the connection string.

    Render exposes Postgres as ``postgres://...`` or ``postgresql://...``;
    SQLAlchemy needs an explicit driver, so we rewrite it to use psycopg2.
    With no DATABASE_URL set we use a local SQLite file.
    """
    url = os.getenv("DATABASE_URL")
    if not url:
        return "sqlite:///./app.db"

    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+psycopg2://", 1)
    elif url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return url


_DB_URL = _database_url()

# SQLite needs this flag for use across FastAPI's threadpool; Postgres ignores it.
_connect_args = {"check_same_thread": False} if _DB_URL.startswith("sqlite") else {}

engine = create_engine(_DB_URL, echo=False, pool_pre_ping=True, connect_args=_connect_args)


class User(SQLModel, table=True):
    """A user account.

    Accounts can be created two ways:
      - Google sign-in  -> ``google_sub`` is set, ``password_hash`` is None
      - Email/password  -> ``password_hash`` is set, ``google_sub`` is None
    The two link automatically when the same email is used for both.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(index=True, unique=True)
    google_sub: Optional[str] = Field(default=None, index=True)  # Google's user id
    password_hash: Optional[str] = Field(default=None)  # bcrypt hash for email login
    name: str = ""
    picture: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


def init_db() -> None:
    """Create tables on startup (no-op if they already exist)."""
    SQLModel.metadata.create_all(engine)


def get_user_by_email(email: str) -> Optional[User]:
    with Session(engine) as session:
        return session.exec(select(User).where(User.email == email)).first()


def create_email_user(email: str, password_hash: str, name: str) -> User:
    """Create a new email/password account."""
    with Session(engine) as session:
        user = User(email=email, password_hash=password_hash, name=name)
        session.add(user)
        session.commit()
        session.refresh(user)
        return user


def upsert_google_user(google_sub: str, email: str, name: str, picture: str) -> User:
    """Create or update a Google user, linking to an existing email account if present."""
    with Session(engine) as session:
        user = session.exec(select(User).where(User.google_sub == google_sub)).first()
        if user is None:
            user = session.exec(select(User).where(User.email == email)).first()

        if user is None:
            user = User(google_sub=google_sub, email=email, name=name, picture=picture)
            session.add(user)
        else:
            user.google_sub = google_sub
            user.email = email
            user.name = name or user.name
            user.picture = picture or user.picture

        session.commit()
        session.refresh(user)
        return user


# --------------------------------------------------------------------------- #
# Conversations + messages (persistent chat history, per user)
# --------------------------------------------------------------------------- #


class Conversation(SQLModel, table=True):
    """A chat thread belonging to one user."""

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(index=True)
    title: str = "New chat"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Message(SQLModel, table=True):
    """A single message within a conversation ("user" or "assistant")."""

    id: Optional[int] = Field(default=None, primary_key=True)
    conversation_id: int = Field(index=True)
    role: str  # "user" | "assistant"
    content: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


def create_conversation(user_id: int, title: str = "New chat") -> int:
    with Session(engine) as session:
        conv = Conversation(user_id=user_id, title=title)
        session.add(conv)
        session.commit()
        session.refresh(conv)
        return conv.id


def conversation_owner(conv_id: int, user_id: int) -> bool:
    with Session(engine) as session:
        conv = session.get(Conversation, conv_id)
        return bool(conv and conv.user_id == user_id)


def list_conversations(user_id: int) -> list[dict]:
    with Session(engine) as session:
        rows = session.exec(
            select(Conversation)
            .where(Conversation.user_id == user_id)
            .order_by(Conversation.updated_at.desc(), Conversation.id.desc())
        ).all()
        return [
            {"id": c.id, "title": c.title, "updated_at": c.updated_at.isoformat()}
            for c in rows
        ]


def get_conversation_owned(conv_id: int, user_id: int) -> Optional[dict]:
    with Session(engine) as session:
        conv = session.get(Conversation, conv_id)
        if not conv or conv.user_id != user_id:
            return None
        msgs = session.exec(
            select(Message).where(Message.conversation_id == conv_id).order_by(Message.id)
        ).all()
        return {
            "id": conv.id,
            "title": conv.title,
            "messages": [{"role": m.role, "content": m.content} for m in msgs],
        }


def get_history(conv_id: int) -> list[tuple]:
    """Return (role, content) pairs in order, for rebuilding the LLM context."""
    with Session(engine) as session:
        msgs = session.exec(
            select(Message).where(Message.conversation_id == conv_id).order_by(Message.id)
        ).all()
        return [(m.role, m.content) for m in msgs]


def append_turn(conv_id: int, user_text: str, assistant_text: str, title: Optional[str]) -> str:
    """Persist a user+assistant turn, bump updated_at, optionally set the title.

    Returns the conversation's current title.
    """
    with Session(engine) as session:
        session.add(Message(conversation_id=conv_id, role="user", content=user_text))
        session.add(Message(conversation_id=conv_id, role="assistant", content=assistant_text))
        conv = session.get(Conversation, conv_id)
        if conv:
            if title:
                conv.title = title
            conv.updated_at = datetime.now(timezone.utc)
            current_title = conv.title
        else:
            current_title = title or "New chat"
        session.commit()
        return current_title


def delete_conversation(conv_id: int, user_id: int) -> bool:
    with Session(engine) as session:
        conv = session.get(Conversation, conv_id)
        if not conv or conv.user_id != user_id:
            return False
        for m in session.exec(
            select(Message).where(Message.conversation_id == conv_id)
        ).all():
            session.delete(m)
        session.delete(conv)
        session.commit()
        return True
