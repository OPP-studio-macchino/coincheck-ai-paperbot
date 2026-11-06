"""Strict, offline 5-minute data inputs for the research lab.

A CSV's source label describes its claimed venue, not verified provenance.
The synthetic fixture exercises software only; it is not market evidence.
"""
from __future__ import annotations

import csv
import hashlib
import io
import math
import os
import random
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.strategy import Candle
from ai_lab.contracts import LabError, POLICY

UTC = timezone.utc
FIVE_MINUTES = timedelta(minutes=5)
BASE_COLUMNS = ("timestamp", "open", "high", "low", "close", "volume")
OPTIONAL_COLUMNS = {"source", "available_at"}
SOURCES = {"synthetic", "bitbank", "coincheck", "unknown"}


def _utc(value: str, code: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        raise LabError(code) from None
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise LabError(code)
    return parsed.astimezone(UTC)


@dataclass(frozen=True)
class Dataset:
    candles: tuple[Candle, ...]
    sha256: str
    source: str = "unknown"
    available_at: tuple[datetime, ...] = ()
    provenance_verified: bool = False

    def metadata(self) -> dict:
        return {
            "sha256": self.sha256,
            "rows": len(self.candles),
            "source": self.source,
            "provenance_verified": False,
            "market_evidence": False,
            "source_is_self_declared": True,
            "synthetic": self.source == "synthetic",
            "interval_minutes": 5,
            "start_utc": self.candles[0].start.isoformat() if self.candles else None,
            "end_utc": (self.candles[-1].start + FIVE_MINUTES).isoformat() if self.candles else None,
            "availability_assumption": "provided_or_bar_close; not independently verified",
        }


def validate_dataset(dataset: Dataset, *, as_of: datetime | None = None) -> None:
    """Validate in-memory inputs as well as CSV imports; fail closed on gaps."""
    cutoff = as_of or datetime.now(UTC)
    if cutoff.tzinfo is None or cutoff.utcoffset() != timedelta(0):
        raise LabError("as_of_must_be_utc")
    if not dataset.candles:
        raise LabError("dataset_empty")
    if len(dataset.candles) > POLICY["max_rows"]:
        raise LabError("dataset_row_limit")
    if dataset.source not in SOURCES:
        raise LabError("dataset_source_invalid")
    if dataset.available_at and len(dataset.available_at) != len(dataset.candles):
        raise LabError("availability_length_mismatch")
    previous = None
    for index, candle in enumerate(dataset.candles):
        stamp = candle.start
        if stamp.tzinfo is None or stamp.utcoffset() != timedelta(0):
            raise LabError("timestamp_must_be_utc")
        if stamp.second or stamp.microsecond or stamp.minute % 5:
            raise LabError("timestamp_not_5m_aligned")
        if previous is not None and stamp - previous != FIVE_MINUTES:
            raise LabError("duplicate_or_missing_or_unordered_bar")
        previous = stamp
        values = (candle.open, candle.high, candle.low, candle.close, candle.volume)
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in values):
            raise LabError("nonfinite_numeric_value")
        if min(values[:4]) <= 0 or candle.volume < 0:
            raise LabError("nonpositive_price_or_negative_volume")
        if candle.high < max(candle.open, candle.close, candle.low) or candle.low > min(candle.open, candle.close):
            raise LabError("malformed_ohlc")
        end = stamp + FIVE_MINUTES
        if end > cutoff:
            raise LabError("unconfirmed_bar")
        if dataset.available_at:
            available = dataset.available_at[index]
            if available.tzinfo is None or available.utcoffset() != timedelta(0):
                raise LabError("availability_must_be_utc")
            if available < end or available > cutoff:
                raise LabError("invalid_availability_time")


def load_csv(path: str | Path, *, as_of: datetime | None = None) -> Dataset:
    file_path = Path(path)
    try:
        if file_path.stat().st_size > POLICY["max_history_bytes"]:
            raise LabError("dataset_byte_limit")
        raw = file_path.read_bytes()
        if len(raw) > POLICY["max_history_bytes"]:
            raise LabError("dataset_byte_limit")
        decoded = raw.decode("utf-8-sig")
    except (OSError, UnicodeError):
        raise LabError("dataset_unreadable") from None
    reader = csv.DictReader(io.StringIO(decoded), strict=True)
    try:
        headers = reader.fieldnames
    except csv.Error:
        raise LabError("csv_header_invalid") from None
    if not headers or len(set(headers)) != len(headers):
        raise LabError("csv_header_invalid")
    columns = set(headers)
    if not set(BASE_COLUMNS) <= columns or columns - set(BASE_COLUMNS) - OPTIONAL_COLUMNS:
        raise LabError("csv_columns_invalid")
    candles, available_at = [], []
    sources = set()
    try:
        for row in reader:
            if len(candles) >= POLICY["max_rows"]:
                raise LabError("dataset_row_limit")
            if None in row or any(value is None or value == "" for value in row.values()):
                raise LabError("csv_row_invalid")
            stamp = _utc(row["timestamp"], "timestamp_must_be_utc")
            try:
                values = [float(row[key]) for key in BASE_COLUMNS[1:]]
            except (TypeError, ValueError):
                raise LabError("csv_numeric_invalid") from None
            candles.append(Candle(stamp, *values))
            available_at.append(_utc(row["available_at"], "availability_must_be_utc") if "available_at" in row else stamp + FIVE_MINUTES)
            sources.add(row.get("source", "unknown"))
    except csv.Error:
        raise LabError("csv_invalid") from None
    if len(sources) > 1:
        raise LabError("mixed_dataset_sources")
    source = next(iter(sources), "unknown")
    result = Dataset(tuple(candles), hashlib.sha256(raw).hexdigest(), source, tuple(available_at), False)
    validate_dataset(result, as_of=as_of)
    return result


def chronological_split(dataset: Dataset, warmup_days: int = 50) -> dict[str, tuple[int, int]]:
    """Half-open row ranges. No random split and no holdout data in tuning.

    At least 50 calendar days are reserved for indicator initialization; edges
    are rounded forward to a UTC 4h boundary. Remaining complete 4h blocks are
    allocated 50% train, 25% development and the rest to sealed final holdout.
    """
    validate_dataset(dataset)
    if isinstance(warmup_days, bool) or not isinstance(warmup_days, int) or warmup_days < POLICY["warmup_days"]:
        raise LabError("warmup_too_short")
    start = dataset.candles[0].start
    minimum = start + timedelta(days=warmup_days)
    boundaries = [i for i, c in enumerate(dataset.candles) if c.start >= minimum and c.start.minute == 0 and c.start.hour % 4 == 0]
    if len(boundaries) < 13:
        raise LabError("dataset_too_short_for_split")
    first = boundaries[0]
    complete_end = len(dataset.candles)
    while complete_end > first and (dataset.candles[complete_end - 1].start + FIVE_MINUTES).hour % 4 != 0:
        complete_end -= 1
    while complete_end > first and (dataset.candles[complete_end - 1].start + FIVE_MINUTES).minute != 0:
        complete_end -= 1
    blocks = (complete_end - first) // 48
    if blocks < 12:
        raise LabError("dataset_too_short_for_split")
    train_end = first + (blocks // 2) * 48
    dev_end = train_end + (blocks // 4) * 48
    return {"warmup": (0, first), "train": (first, train_end), "development": (train_end, dev_end), "holdout": (dev_end, complete_end)}


def make_fixture(path: str | Path, days: int = 180, seed: int = 47) -> Dataset:
    """Write a deterministic synthetic fixture, never a price history claim."""
    if isinstance(days, bool) or not isinstance(days, int) or days < 1 or days * 288 > POLICY["max_rows"]:
        raise LabError("fixture_days_invalid")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise LabError("fixture_seed_invalid")
    rng = random.Random(seed)
    start = datetime(2020, 1, 1, tzinfo=UTC)
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow((*BASE_COLUMNS, "source", "available_at"))
    price = 8_000_000.0
    for index in range(days * 288):
        stamp = start + FIVE_MINUTES * index
        # Slow regimes and bounded oscillations deliberately cover varied paths.
        drift = (0.000015 if (index // (288 * 17)) % 3 != 2 else -0.000018)
        move = drift + 0.00016 * math.sin(index / 55) + rng.gauss(0, 0.00038)
        close = price * math.exp(move)
        wick = abs(rng.gauss(0, 0.00028))
        high = max(price, close) * (1 + wick)
        low = min(price, close) / (1 + wick)
        writer.writerow((stamp.isoformat(), *(format(v, ".10f") for v in (price, high, low, close, rng.uniform(.1, 3))), "synthetic", (stamp + FIVE_MINUTES).isoformat()))
        price = close
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="", dir=destination.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(output.getvalue())
        os.replace(temporary, destination)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return load_csv(destination)
