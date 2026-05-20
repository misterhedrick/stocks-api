import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Date, DateTime, ForeignKey, Integer, Numeric, String, Text, Uuid, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class Strategy(TimestampMixin, Base):
    __tablename__ = "strategies"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(default=True, server_default=text("true"), nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        default=dict,
        server_default=text("'{}'::jsonb"),
        nullable=False,
    )

    signals: Mapped[list["Signal"]] = relationship(back_populates="strategy")
    order_intents: Mapped[list["OrderIntent"]] = relationship(back_populates="strategy")


class Signal(TimestampMixin, Base):
    __tablename__ = "signals"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    strategy_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("strategies.id", ondelete="SET NULL"),
        index=True,
    )
    symbol: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    underlying_symbol: Mapped[str | None] = mapped_column(String(16), index=True)
    signal_type: Mapped[str] = mapped_column(String(50), nullable=False)
    direction: Mapped[str] = mapped_column(String(20), nullable=False)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    rationale: Mapped[str | None] = mapped_column(Text)
    market_context: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        default=dict,
        server_default=text("'{}'::jsonb"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(30), default="new", server_default="new", nullable=False)
    rejected_reason: Mapped[str | None] = mapped_column(Text)
    preview_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    last_previewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_preview_error: Mapped[str | None] = mapped_column(Text)
    last_preview_error_code: Mapped[str | None] = mapped_column(String(120))
    preview_rejection_reasons: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    strategy: Mapped[Strategy | None] = relationship(back_populates="signals")
    order_intents: Mapped[list["OrderIntent"]] = relationship(back_populates="signal")


class OrderIntent(TimestampMixin, Base):
    __tablename__ = "order_intents"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    strategy_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("strategies.id", ondelete="SET NULL"),
        index=True,
    )
    signal_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("signals.id", ondelete="SET NULL"),
        index=True,
    )
    underlying_symbol: Mapped[str] = mapped_column(String(16), index=True, nullable=False)
    option_symbol: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    side: Mapped[str] = mapped_column(String(10), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    order_type: Mapped[str] = mapped_column(
        String(20),
        default="limit",
        server_default="limit",
        nullable=False,
    )
    limit_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    time_in_force: Mapped[str] = mapped_column(
        String(20),
        default="day",
        server_default="day",
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(30),
        default="previewed",
        server_default="previewed",
        nullable=False,
    )
    rationale: Mapped[str | None] = mapped_column(Text)
    preview: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        default=dict,
        server_default=text("'{}'::jsonb"),
        nullable=False,
    )
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    strategy: Mapped[Strategy | None] = relationship(back_populates="order_intents")
    signal: Mapped[Signal | None] = relationship(back_populates="order_intents")
    broker_orders: Mapped[list["BrokerOrder"]] = relationship(back_populates="order_intent")


class BrokerOrder(TimestampMixin, Base):
    __tablename__ = "broker_orders"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    order_intent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("order_intents.id", ondelete="SET NULL"),
        index=True,
    )
    alpaca_order_id: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    symbol: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    side: Mapped[str] = mapped_column(String(10), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    order_type: Mapped[str] = mapped_column(String(20), nullable=False)
    limit_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    filled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw_response: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        default=dict,
        server_default=text("'{}'::jsonb"),
        nullable=False,
    )

    order_intent: Mapped[OrderIntent | None] = relationship(back_populates="broker_orders")
    fills: Mapped[list["Fill"]] = relationship(back_populates="broker_order")


class Fill(Base):
    __tablename__ = "fills"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    broker_order_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("broker_orders.id", ondelete="SET NULL"),
        index=True,
    )
    alpaca_fill_id: Mapped[str | None] = mapped_column(String(120), unique=True)
    symbol: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    side: Mapped[str] = mapped_column(String(10), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    filled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    raw_response: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        default=dict,
        server_default=text("'{}'::jsonb"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    broker_order: Mapped[BrokerOrder | None] = relationship(back_populates="fills")


class PositionSnapshot(Base):
    __tablename__ = "position_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    symbol: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    market_value: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    cost_basis: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    unrealized_pl: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    raw_position: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        default=dict,
        server_default=text("'{}'::jsonb"),
        nullable=False,
    )
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class JobRun(Base):
    __tablename__ = "job_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    job_name: Mapped[str] = mapped_column(String(120), index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    details: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        default=dict,
        server_default=text("'{}'::jsonb"),
        nullable=False,
    )
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class TradeCase(Base):
    __tablename__ = "trade_cases"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    strategy_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("strategies.id", ondelete="SET NULL"),
        index=True,
    )
    entry_order_intent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("order_intents.id", ondelete="SET NULL"),
        index=True,
    )
    entry_fill_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("fills.id", ondelete="SET NULL"),
        index=True,
    )
    exit_fill_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("fills.id", ondelete="SET NULL"),
        index=True,
    )
    symbol: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    underlying_symbol: Mapped[str | None] = mapped_column(String(16), index=True)
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    entry_price: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    entry_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    exit_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    exit_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    realized_pl: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    realized_pl_percent: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    is_open: Mapped[bool] = mapped_column(default=True, server_default=text("true"), nullable=False)
    context: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        default=dict,
        server_default=text("'{}'::jsonb"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    ai_reviews: Mapped[list["AiTradeReview"]] = relationship(back_populates="trade_case")


class AiTradeReview(Base):
    __tablename__ = "ai_trade_reviews"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    trade_case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("trade_cases.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    review_model: Mapped[str] = mapped_column(String(120), nullable=False)
    review_status: Mapped[str] = mapped_column(
        String(30),
        default="pending",
        server_default="pending",
        nullable=False,
    )
    assessment: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        default=dict,
        server_default=text("'{}'::jsonb"),
        nullable=False,
    )
    raw_response: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        default=dict,
        server_default=text("'{}'::jsonb"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    trade_case: Mapped[TradeCase] = relationship(back_populates="ai_reviews")
    suggestions: Mapped[list["StrategyChangeSuggestion"]] = relationship(back_populates="ai_trade_review")


class StrategyChangeSuggestion(Base):
    __tablename__ = "strategy_change_suggestions"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    ai_trade_review_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ai_trade_reviews.id", ondelete="SET NULL"),
        index=True,
    )
    strategy_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("strategies.id", ondelete="SET NULL"),
        index=True,
    )
    suggestion_type: Mapped[str] = mapped_column(String(60), index=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    proposed_config_patch: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        default=dict,
        server_default=text("'{}'::jsonb"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(30),
        default="pending",
        server_default="pending",
        nullable=False,
    )
    review_notes: Mapped[str | None] = mapped_column(Text)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    ai_trade_review: Mapped[AiTradeReview | None] = relationship(back_populates="suggestions")


class StrategyTuningDecision(Base):
    __tablename__ = "strategy_tuning_decisions"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    strategy_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("strategies.id", ondelete="SET NULL"),
        index=True,
    )
    scanner_type: Mapped[str] = mapped_column(String(80), index=True, nullable=False)
    symbol: Mapped[str] = mapped_column(String(16), index=True, nullable=False)
    decision_type: Mapped[str] = mapped_column(String(80), index=True, nullable=False)
    status: Mapped[str] = mapped_column(
        String(30),
        default="approved",
        server_default="approved",
        index=True,
        nullable=False,
    )
    description: Mapped[str | None] = mapped_column(Text)
    expected_effect: Mapped[str | None] = mapped_column(Text)
    proposed_config_patch: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        default=dict,
        server_default=text("'{}'::jsonb"),
        nullable=False,
    )
    evidence_snapshot_ids: Mapped[list[Any]] = mapped_column(
        JSONB,
        default=list,
        server_default=text("'[]'::jsonb"),
        nullable=False,
    )
    evidence_summary: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        default=dict,
        server_default=text("'{}'::jsonb"),
        nullable=False,
    )
    outcome_summary: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        default=dict,
        server_default=text("'{}'::jsonb"),
        nullable=False,
    )
    created_by: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class ReviewSnapshot(Base):
    __tablename__ = "paper_review_snapshots"
    __table_args__ = (
        UniqueConstraint("review_date", "review_type", name="uq_paper_review_snapshots_date_type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    review_date: Mapped[date] = mapped_column(Date, index=True, nullable=False)
    review_type: Mapped[str] = mapped_column(String(40), default="post_market", server_default="post_market", nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="completed", server_default="completed", nullable=False)
    window_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    window_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    summary: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        default=dict,
        server_default=text("'{}'::jsonb"),
        nullable=False,
    )
    signals: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        default=dict,
        server_default=text("'{}'::jsonb"),
        nullable=False,
    )
    previews: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        default=dict,
        server_default=text("'{}'::jsonb"),
        nullable=False,
    )
    orders: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        default=dict,
        server_default=text("'{}'::jsonb"),
        nullable=False,
    )
    fills: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        default=dict,
        server_default=text("'{}'::jsonb"),
        nullable=False,
    )
    diagnostics: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        default=dict,
        server_default=text("'{}'::jsonb"),
        nullable=False,
    )
    rejected_outcomes: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        default=dict,
        server_default=text("'{}'::jsonb"),
        nullable=False,
    )
    raw_payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        default=dict,
        server_default=text("'{}'::jsonb"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class OptionSelectionDiagnostic(Base):
    __tablename__ = "option_selection_diagnostics"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    signal_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("signals.id", ondelete="SET NULL"),
        index=True,
    )
    strategy_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("strategies.id", ondelete="SET NULL"),
        index=True,
    )
    strategy_name: Mapped[str | None] = mapped_column(String(120), index=True)
    underlying_symbol: Mapped[str] = mapped_column(String(16), index=True, nullable=False)
    scanner_type: Mapped[str | None] = mapped_column(String(80), index=True)
    preview_profile: Mapped[str | None] = mapped_column(String(80), index=True)
    candidate_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    reason_counts: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        default=dict,
        server_default=text("'{}'::jsonb"),
        nullable=False,
    )
    summary: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        default=dict,
        server_default=text("'{}'::jsonb"),
        nullable=False,
    )
    market_context: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        default=dict,
        server_default=text("'{}'::jsonb"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    event_type: Mapped[str] = mapped_column(String(120), index=True, nullable=False)
    entity_type: Mapped[str | None] = mapped_column(String(120), index=True)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), index=True)
    message: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        default=dict,
        server_default=text("'{}'::jsonb"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
