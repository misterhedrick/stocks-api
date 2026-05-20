from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
import os
from types import SimpleNamespace
import uuid

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, func, inspect, select
from sqlalchemy.orm import sessionmaker

RUN_DB_TESTS = os.getenv("STOCKS_API_RUN_DB_INTEGRATION_TESTS") == "1"
TEST_DATABASE_URL = os.getenv(
    "STOCKS_API_INTEGRATION_DATABASE_URL",
    "postgresql+psycopg://stocks_api:stocks_api@127.0.0.1:5433/stocks_api_test",
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not RUN_DB_TESTS,
        reason="Set STOCKS_API_RUN_DB_INTEGRATION_TESTS=1 to run Postgres integration tests.",
    ),
]

os.environ["DATABASE_URL"] = TEST_DATABASE_URL

from app.db.models import (  # noqa: E402
    AiTradeReview,
    AuditLog,
    BrokerOrder,
    Fill,
    JobRun,
    OptionSelectionDiagnostic,
    OrderIntent,
    ReviewSnapshot,
    PositionSnapshot,
    Signal,
    Strategy,
    StrategyChangeSuggestion,
    TradeCase,
)
from app.services.ai_trade_review import (  # noqa: E402
    update_strategy_change_suggestion_review,
    write_ai_trade_reviews,
)
from app.services.review_snapshots import (  # noqa: E402
    create_or_update_post_market_review_snapshot,
)
from app.services.trading_reset import (  # noqa: E402
    RESET_TRADING_DATA_CONFIRMATION,
    run_trading_data_reset,
)


@pytest.fixture(scope="session")
def migrated_engine():
    config = Config("alembic.ini")
    command.upgrade(config, "head")
    engine = create_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture()
def db(migrated_engine):
    SessionLocal = sessionmaker(bind=migrated_engine, autoflush=False, autocommit=False)
    with SessionLocal() as session:
        _clear_runtime_tables(session)
        yield session
        session.rollback()
        _clear_runtime_tables(session)


def test_alembic_upgrade_head_creates_review_tables(migrated_engine) -> None:
    inspector = inspect(migrated_engine)
    table_names = set(inspector.get_table_names())

    assert "review_snapshots" in table_names
    assert "ai_trade_reviews" in table_names
    assert "strategy_change_suggestions" in table_names
    suggestion_columns = {
        column["name"]
        for column in inspector.get_columns("strategy_change_suggestions")
    }
    assert {"review_notes", "reviewed_at", "reviewed_by"} <= suggestion_columns


def test_review_snapshot_upserts_one_daily_post_market_row(db, monkeypatch) -> None:
    generated_at = datetime(2026, 5, 8, 21, 30, tzinfo=timezone.utc)
    monkeypatch.setattr(
        "app.services.review_snapshots.get_performance_review",
        lambda _db, limit: _performance_result(generated_at),
    )

    first = create_or_update_post_market_review_snapshot(
        db,
        review_date=date(2026, 5, 8),
        generated_at=generated_at,
    )
    second = create_or_update_post_market_review_snapshot(
        db,
        review_date=date(2026, 5, 8),
        generated_at=generated_at,
    )

    assert first.created is True
    assert second.created is False
    assert first.snapshot.id == second.snapshot.id
    assert db.scalar(select(func.count(ReviewSnapshot.id))) == 1


def test_ai_review_writer_and_suggestion_review_metadata_persist(db) -> None:
    trade_case = _insert_trade_case(db, realized_pl=Decimal("-25"))
    snapshot = ReviewSnapshot(
        review_date=date(2026, 5, 8),
        review_type="post_market",
        status="completed",
        generated_at=datetime(2026, 5, 8, 21, 30, tzinfo=timezone.utc),
        summary={"counts": {"signals": 1}},
        diagnostics={"summary": {"reason_counts": {"wide_spread": 2}}},
        rejected_outcomes={
            "shadow_market_movement": [
                {
                    "scanner_type": "moving_average",
                    "symbol": "SPY",
                    "directional_outcome": "would_have_helped",
                }
            ]
        },
    )
    db.add(snapshot)
    db.commit()

    result = write_ai_trade_reviews(db, limit=10)

    assert result.trade_cases_seen == 1
    assert result.reviews_created == 1
    assert db.scalar(select(func.count(AiTradeReview.id))) == 1
    assert db.scalar(select(func.count(StrategyChangeSuggestion.id))) >= 1
    suggestion = db.scalars(select(StrategyChangeSuggestion).limit(1)).first()

    reviewed = update_strategy_change_suggestion_review(
        db,
        suggestion_id=suggestion.id,
        status="approved",
        review_notes="Approved for later manual config planning.",
        reviewed_by="integration-test",
    )

    assert reviewed.suggestion.status == "approved"
    assert reviewed.suggestion.review_notes == "Approved for later manual config planning."
    assert reviewed.suggestion.reviewed_by == "integration-test"
    assert reviewed.suggestion.reviewed_at is not None


def test_trading_reset_deletes_review_tables_in_real_postgres(db) -> None:
    _insert_trade_case(db, realized_pl=Decimal("10"))
    review = AiTradeReview(
        trade_case_id=db.scalars(select(TradeCase.id).limit(1)).first(),
        review_model="integration-test",
        review_status="generated",
        assessment={},
        raw_response={},
    )
    db.add(review)
    db.flush()
    db.add(
        StrategyChangeSuggestion(
            ai_trade_review_id=review.id,
            suggestion_type="monitor_strategy",
            status="pending",
            proposed_config_patch={},
        )
    )
    db.add(
        ReviewSnapshot(
            review_date=date(2026, 5, 8),
            review_type="post_market",
            status="completed",
            generated_at=datetime.now(timezone.utc),
        )
    )
    db.commit()

    result = run_trading_data_reset(
        db,
        dry_run=False,
        include_history=True,
        confirm=RESET_TRADING_DATA_CONFIRMATION,
    )

    assert result.deleted["strategy_change_suggestions"] == 1
    assert result.deleted["ai_trade_reviews"] == 1
    assert result.deleted["review_snapshots"] == 1
    assert result.deleted["trade_cases"] == 1
    assert db.scalar(select(func.count(StrategyChangeSuggestion.id))) == 0
    assert db.scalar(select(func.count(AiTradeReview.id))) == 0
    assert db.scalar(select(func.count(ReviewSnapshot.id))) == 0
    assert db.scalar(select(func.count(TradeCase.id))) == 0


def _performance_result(generated_at: datetime) -> SimpleNamespace:
    return SimpleNamespace(
        generated_at=generated_at,
        fills_seen=0,
        matched_round_trips=0,
        totals={"realized_pnl": "0"},
        by_strategy=[],
        by_symbol=[],
        open_positions=[],
        recent_round_trips=[],
        signal_summary={"signals_seen": 0},
        no_signal_summary={"reasons_seen": 0},
        option_selection_diagnostics={"diagnostics_seen": 0},
        rejected_preview_outcomes=[],
    )


def _insert_trade_case(db, *, realized_pl: Decimal) -> TradeCase:
    now = datetime(2026, 5, 8, 20, 30, tzinfo=timezone.utc)
    trade_case = TradeCase(
        strategy_id=None,
        entry_order_intent_id=None,
        entry_fill_id=None,
        exit_fill_id=None,
        symbol="SPY260501C00500000",
        underlying_symbol="SPY",
        quantity=Decimal("1"),
        entry_price=Decimal("1.00"),
        entry_time=now,
        exit_price=Decimal("0.75"),
        exit_time=now,
        realized_pl=realized_pl,
        realized_pl_percent=Decimal("-25") if realized_pl < 0 else Decimal("10"),
        is_open=False,
        context={
            "entry": {
                "signal": {
                    "market_context": {"strategy_type": "moving_average"},
                },
            },
        },
    )
    db.add(trade_case)
    db.commit()
    db.refresh(trade_case)
    return trade_case


def _clear_runtime_tables(db) -> None:
    for model in [
        StrategyChangeSuggestion,
        AiTradeReview,
        ReviewSnapshot,
        TradeCase,
        OptionSelectionDiagnostic,
        Fill,
        BrokerOrder,
        OrderIntent,
        Signal,
        PositionSnapshot,
        AuditLog,
        JobRun,
        Strategy,
    ]:
        db.query(model).delete()
    db.commit()
