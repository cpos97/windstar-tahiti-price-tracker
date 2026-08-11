"""Database models for cruises and price history."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Cruise(Base):
    __tablename__ = "cruises"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    css_selector: Mapped[str | None] = mapped_column(String(512), nullable=True)
    expected_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    # Tracked and priced like any other source, but shown as a comparison line
    # inside the real cards rather than getting a card of its own.
    is_benchmark: Mapped[bool | None] = mapped_column(Boolean, nullable=True, default=False)
    current_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    previous_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    lowest_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    highest_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_checked: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    cabin_last_checked: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    history: Mapped[list[PriceHistory]] = relationship(
        "PriceHistory",
        back_populates="cruise",
        cascade="all, delete-orphan",
        order_by="PriceHistory.checked_at.desc()",
    )


class PriceHistory(Base):
    __tablename__ = "price_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    cruise_id: Mapped[int] = mapped_column(ForeignKey("cruises.id", ondelete="CASCADE"), index=True)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    raw_text: Mapped[str | None] = mapped_column(String(255), nullable=True)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    cruise: Mapped[Cruise] = relationship("Cruise", back_populates="history")


class CabinAvailability(Base):
    __tablename__ = "cabin_availability"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    cruise_id: Mapped[int] = mapped_column(ForeignKey("cruises.id", ondelete="CASCADE"), index=True)
    category_code: Mapped[str] = mapped_column(String(16), nullable=False)
    category_name: Mapped[str] = mapped_column(String(120), nullable=False)
    available: Mapped[int] = mapped_column(Integer, nullable=False)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AlertLog(Base):
    __tablename__ = "alert_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    cruise_id: Mapped[int] = mapped_column(ForeignKey("cruises.id", ondelete="CASCADE"), index=True)
    old_price: Mapped[float] = mapped_column(Float, nullable=False)
    new_price: Mapped[float] = mapped_column(Float, nullable=False)
    sent_to: Mapped[str] = mapped_column(String(255), nullable=False)
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
