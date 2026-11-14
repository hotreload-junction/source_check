"""MariaDB schema, including the audit trail (AGENTS.md section 7).

Portable SQLAlchemy 2.0 declarative models: production runs on MariaDB, tests on in-memory SQLite.
Every claim verdict row stores evidence quote, fuzzy score, model and PROMPT_VERSION.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    JSON, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, DateTime,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)  # naive UTC, MariaDB DATETIME


class Base(DeclarativeBase):
    pass


class Analysis(Base):
    """One job. `result` holds the full contract `Result` once status == done (cache for demo)."""

    __tablename__ = "analyses"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    url: Mapped[str] = mapped_column(Text)
    normalized_url: Mapped[str] = mapped_column(String(512), index=True)
    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued|running|done|failed
    step: Mapped[str] = mapped_column(String(32), default="")
    message: Mapped[str] = mapped_column(String(255), default="")
    pct: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    claims: Mapped[list["ClaimRecord"]] = relationship(
        back_populates="analysis", cascade="all, delete-orphan"
    )
    sources: Mapped[list["SourceRecord"]] = relationship(
        back_populates="analysis", cascade="all, delete-orphan"
    )

    __table_args__ = (Index("ix_analyses_norm_status", "normalized_url", "status"),)


class ClaimRecord(Base):
    __tablename__ = "claims"

    pk: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    analysis_id: Mapped[str] = mapped_column(ForeignKey("analyses.id", ondelete="CASCADE"))
    claim_id: Mapped[str] = mapped_column(String(16))  # "c1"
    text: Mapped[str] = mapped_column(Text)
    char_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    char_end: Mapped[int | None] = mapped_column(Integer, nullable=True)

    analysis: Mapped[Analysis] = relationship(back_populates="claims")
    verdicts: Mapped[list["ClaimVerdictRecord"]] = relationship(
        back_populates="claim", cascade="all, delete-orphan"
    )

    __table_args__ = (UniqueConstraint("analysis_id", "claim_id", name="uq_claim_per_analysis"),)


class SourceRecord(Base):
    """A cited source with its fetch outcome. `text` is the fetched source text the quote is checked against."""

    __tablename__ = "sources"

    pk: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    analysis_id: Mapped[str] = mapped_column(ForeignKey("analyses.id", ondelete="CASCADE"))
    source_id: Mapped[str] = mapped_column(String(16))  # "s1"
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    domain: Mapped[str | None] = mapped_column(String(255), nullable=True)
    kind: Mapped[str] = mapped_column(String(16))  # link|named|telegram|social|self
    attribution_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    depth: Mapped[int] = mapped_column(Integer, default=1)
    parent_source_id: Mapped[str | None] = mapped_column(String(16), nullable=True)
    fetch_status: Mapped[str] = mapped_column(String(16), default="not_fetched")
    fetched_via: Mapped[str | None] = mapped_column(String(16), nullable=True)
    text: Mapped[str | None] = mapped_column(Text().with_variant(Text(length=2**24 - 1), "mysql"), nullable=True)

    analysis: Mapped[Analysis] = relationship(back_populates="sources")

    __table_args__ = (UniqueConstraint("analysis_id", "source_id", name="uq_source_per_analysis"),)


class ClaimVerdictRecord(Base):
    """AUDIT TRAIL: one row per (claim, source) verification with everything needed to reproduce it."""

    __tablename__ = "claim_verdicts"

    pk: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    claim_pk: Mapped[int] = mapped_column(ForeignKey("claims.pk", ondelete="CASCADE"))
    source_id: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # the page that was judged: Exa guesses are audited here too although they never become contract Sources
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    verdict: Mapped[str] = mapped_column(String(24))  # contract ClaimVerdict
    subtype: Mapped[str | None] = mapped_column(String(16), nullable=True)
    quote_original: Mapped[str | None] = mapped_column(Text, nullable=True)
    quote_translation: Mapped[str | None] = mapped_column(Text, nullable=True)
    language: Mapped[str | None] = mapped_column(String(8), nullable=True)
    fuzzy_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    llm_verdict: Mapped[str | None] = mapped_column(String(24), nullable=True)  # before the fuzzy downgrade
    explanation: Mapped[str] = mapped_column(Text, default="")
    model: Mapped[str] = mapped_column(String(128), default="")
    prompt_version: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    claim: Mapped[ClaimRecord] = relationship(back_populates="verdicts")


class DomainRecord(Base):
    """Infra signals per domain, cached across analyses (RDAP is slow)."""

    __tablename__ = "domains"

    domain: Mapped[str] = mapped_column(String(255), primary_key=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    analytics_ids: Mapped[list] = mapped_column(JSON, default=list)
    adsense_ids: Mapped[list] = mapped_column(JSON, default=list)
    checked_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class StepCacheRecord(Base):
    """Persistent step cache (app/cache.py): LLM completions, fetched pages, Exa results. A finished article replays
    from here byte for byte, independent of the AI router's own (short-lived) cache."""

    __tablename__ = "step_cache"

    kind: Mapped[str] = mapped_column(String(16), primary_key=True)
    key: Mapped[str] = mapped_column(String(64), primary_key=True)        # sha256 hex of the request
    value: Mapped[str] = mapped_column(Text().with_variant(Text(length=2**32 - 1), "mysql"))   # JSON text
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
