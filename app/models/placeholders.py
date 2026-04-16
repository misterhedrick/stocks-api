from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Strategy(Base):
    __tablename__ = 'strategies'

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(30), default='draft')
    description: Mapped[str | None] = mapped_column(Text(), nullable=True)


class Signal(Base):
    __tablename__ = 'signals'

    id: Mapped[int] = mapped_column(primary_key=True)
    strategy_id: Mapped[int | None] = mapped_column(ForeignKey('strategies.id'), nullable=True)
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    side: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(30), default='new')


class OrderIntent(Base):
    __tablename__ = 'order_intents'

    id: Mapped[int] = mapped_column(primary_key=True)
    signal_id: Mapped[int | None] = mapped_column(ForeignKey('signals.id'), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default='preview')
    created_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
