from collections.abc import Iterator
from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

from app.config import settings

_connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, pool_pre_ping=True, connect_args=_connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)  # conversation_id from the source
    dataset_group: Mapped[str] = mapped_column(String(64), index=True, default="UPLOAD")
    channel: Mapped[str] = mapped_column(String(32), default="agent chat")  # agent chat | voice call
    language: Mapped[str] = mapped_column(String(8), default="RU")  # RU | KZ | EN
    audio_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    # [{idx, timestamp, speaker, text}] - original timestamps preserved as-is
    transcript: Mapped[list] = mapped_column(JSON, default=list)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

    evaluations: Mapped[list["Evaluation"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan", order_by="Evaluation.id.desc()"
    )


class Checklist(Base):
    """Immutable versions: every save creates a new row; old evaluations keep their snapshot."""

    __tablename__ = "checklists"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    version: Mapped[int] = mapped_column(Integer, default=1)
    family: Mapped[str] = mapped_column(String(64), index=True)  # groups versions of the same checklist
    config: Mapped[dict] = mapped_column(JSON)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Evaluation(Base):
    __tablename__ = "evaluations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    checklist_id: Mapped[int] = mapped_column(ForeignKey("checklists.id"))
    checklist_snapshot: Mapped[dict] = mapped_column(JSON)
    batch_id: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|running|done|error
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    evaluator: Mapped[str] = mapped_column(String(32), default="mock")
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    items: Mapped[list] = mapped_column(JSON, default=list)  # AI result per criterion (never modified)
    result: Mapped[dict] = mapped_column(JSON, default=dict)  # scores computed from `items`
    final_items: Mapped[list] = mapped_column(JSON, default=list)  # items with reviewer decisions applied
    final_result: Mapped[dict] = mapped_column(JSON, default=dict)

    usage: Mapped[dict] = mapped_column(JSON, default=dict)  # tokens + cost_usd
    duration_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    conversation: Mapped[Conversation] = relationship(back_populates="evaluations")
    appeals: Mapped[list["Appeal"]] = relationship(
        back_populates="evaluation", cascade="all, delete-orphan", order_by="Appeal.id"
    )


class Appeal(Base):
    __tablename__ = "appeals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    evaluation_id: Mapped[int] = mapped_column(ForeignKey("evaluations.id", ondelete="CASCADE"), index=True)
    criterion_id: Mapped[str] = mapped_column(String(64))
    comment: Mapped[str] = mapped_column(Text)
    author: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="open")  # open | confirmed | changed
    original_item: Mapped[dict] = mapped_column(JSON)  # the disputed criterion result at filing time

    decision_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_item: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    reviewer: Mapped[str | None] = mapped_column(String(128), nullable=True)
    total_before: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_after: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    evaluation: Mapped[Evaluation] = relationship(back_populates="appeals")


def init_db() -> None:
    Base.metadata.create_all(engine)


def get_session() -> Iterator[Session]:
    with SessionLocal() as session:
        yield session
