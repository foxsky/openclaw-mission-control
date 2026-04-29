"""Time-related helpers shared across backend modules."""

from __future__ import annotations

from datetime import UTC, datetime


def utcnow() -> datetime:
    """Return a naive UTC datetime without using deprecated datetime.utcnow()."""
    # Keep naive UTC values for compatibility with existing DB schema/queries.
    return datetime.now(UTC).replace(tzinfo=None)


def as_naive_utc(value: datetime) -> datetime:
    """Normalize an aware or naive datetime to a naive UTC datetime.

    Naive timestamps are treated as UTC (the project convention); aware
    timestamps are converted to UTC before stripping tzinfo so subtraction
    yields a true UTC delta even when inputs disagree on offset.
    """
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)
