"""Postgres is the system of record. SQLite works for local runs with the same schema."""
from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import JSON, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Opportunity(Base):
    """One real-world opportunity, however many alert sources reported it."""

    __tablename__ = "opportunities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sol_key: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)  # normalized solicitation number
    solicitation_number: Mapped[str | None] = mapped_column(String(128))
    title: Mapped[str] = mapped_column(String(512))
    agency: Mapped[str | None] = mapped_column(String(256))
    naics: Mapped[list] = mapped_column(JSON, default=list)
    set_aside: Mapped[str | None] = mapped_column(String(32))          # normalized code: NONE, SB, WOSB, ...
    set_aside_raw: Mapped[str | None] = mapped_column(String(256))
    contract_vehicle: Mapped[str | None] = mapped_column(String(128))
    response_deadline: Mapped[date | None] = mapped_column(Date)
    place_of_performance: Mapped[str | None] = mapped_column(String(256))
    description: Mapped[str | None] = mapped_column(Text)
    source_links: Mapped[list] = mapped_column(JSON, default=list)
    conflicts: Mapped[dict] = mapped_column(JSON, default=dict)         # field -> {source: value} when sources disagree
    merge_method: Mapped[str] = mapped_column(String(16), default="new")  # new | sol_number | fuzzy
    needs_review: Mapped[bool] = mapped_column(default=False)

    monday_item_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    sources: Mapped[list["SourceRef"]] = relationship(back_populates="opportunity", cascade="all, delete-orphan")
    scores: Mapped[list["Score"]] = relationship(back_populates="opportunity", cascade="all, delete-orphan",
                                                 order_by="Score.created_at")

    @property
    def latest_score(self) -> "Score | None":
        return self.scores[-1] if self.scores else None


class SourceRef(Base):
    """Every alert that mentioned the opportunity. Kept forever, never merged away."""

    __tablename__ = "source_refs"
    __table_args__ = (UniqueConstraint("message_id", "source", "source_id", name="uq_source_ref"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    opportunity_id: Mapped[int] = mapped_column(ForeignKey("opportunities.id"))
    source: Mapped[str] = mapped_column(String(32))          # govtribe | bidspeed | ebuy | idiq
    source_id: Mapped[str | None] = mapped_column(String(128))  # e.g. eBuy RFQ ID, or the sol# as printed
    message_id: Mapped[str] = mapped_column(String(256))
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    link: Mapped[str | None] = mapped_column(String(1024))
    raw_fields: Mapped[dict] = mapped_column(JSON, default=dict)

    opportunity: Mapped[Opportunity] = relationship(back_populates="sources")


class Score(Base):
    """A scoring run. Re-scoring appends a row, so history is auditable."""

    __tablename__ = "scores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    opportunity_id: Mapped[int] = mapped_column(ForeignKey("opportunities.id"))
    rubric_version: Mapped[str] = mapped_column(String(64))
    judge: Mapped[str] = mapped_column(String(64))           # "heuristic" or the Claude model id
    score: Mapped[float] = mapped_column(Float)
    tier: Mapped[int] = mapped_column(Integer)
    knockout: Mapped[str | None] = mapped_column(String(256))
    criteria: Mapped[dict] = mapped_column(JSON)              # id -> {level, points, evidence}
    rationale: Mapped[str] = mapped_column(Text)
    action: Mapped[str] = mapped_column(Text)
    alerted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    opportunity: Mapped[Opportunity] = relationship(back_populates="scores")


class ProcessedMessage(Base):
    """Makes intake idempotent: a message is parsed once, even if the hourly job overlaps."""

    __tablename__ = "processed_messages"

    message_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    source: Mapped[str | None] = mapped_column(String(32))
    opportunities_found: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class LlmCache(Base):
    """Same prompt + model + rubric version -> same judgment, run after run."""

    __tablename__ = "llm_cache"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    model: Mapped[str] = mapped_column(String(64))
    response: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


def make_session_factory(database_url: str):
    engine = create_engine(database_url, future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)
