"""Fail-closed reservations for one private generator state volume.

Explicit initialization never resets existing state. Every admitted send keeps
its lifetime attempt and full reservation after success, failure, or a crash.
Unknown prior-day charges also consume today's monetary exposure. There is no
refill/refund API. The owner must preserve this state volume with its key; it
does not constrain a hostile owner deleting or cloning the ledger.
"""
from __future__ import annotations

import contextlib
import os
import re
import sqlite3
import stat
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .contracts import LabError, canonical_json, digest, strict_json
from .phase2_policy import (
    MODEL, PRICE_HASH, RESERVATION_MICROUSD, ensure_price_current,
    normalize_usage, price_record, usage_cost,
)

SCHEMA_VERSION = 2
APPLICATION_ID = 0x43415032
STATE_KIND = "coincheck-ai-generator-budget-v2"
MARKER_NAME = ".phase2-state.json"
DB_NAME = "budget.sqlite3"
MAX_STATE_BYTES = 8 * 1024 * 1024
MAX_MONEY_LIMIT = 1_000_000_000  # $1,000; explicit initialization only.
MAX_CALL_LIMIT = 1024
LIMIT_KEYS = {"per_call_limit_microusd", "daily_limit_microusd", "total_limit_microusd",
              "daily_call_limit", "total_call_limit"}
RECORD_KEYS = {"request_hash", "context_hash", "status", "attempt_id", "model", "response_id",
               "usage", "budget_policy_hash", "price_hash", "reserved_microusd", "actual_microusd",
               "created_at_utc", "completed_at_utc", "candidate_hash", "response_hash"}

SCHEMA = """
CREATE TABLE metadata (
 id INTEGER PRIMARY KEY CHECK(id=1), policy_json TEXT NOT NULL,
 policy_hash TEXT NOT NULL, last_seen_utc TEXT NOT NULL);
CREATE TABLE reservations (
 request_hash TEXT PRIMARY KEY, context_hash TEXT NOT NULL, day_utc TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('RESERVED','COMPLETED','FAILED')),
 model TEXT NOT NULL, price_hash TEXT NOT NULL, budget_policy_hash TEXT NOT NULL,
 reserved_microusd INTEGER NOT NULL CHECK(reserved_microusd>0),
 actual_microusd INTEGER CHECK(actual_microusd>=0 AND actual_microusd<=reserved_microusd),
 created_at_utc TEXT NOT NULL, completed_at_utc TEXT,
 response_id TEXT, usage_json TEXT, candidate_hash TEXT, response_hash TEXT);
CREATE TABLE events (
 id INTEGER PRIMARY KEY, at_utc TEXT NOT NULL, kind TEXT NOT NULL,
 payload_json TEXT NOT NULL, previous_hash TEXT NOT NULL, event_hash TEXT NOT NULL);
CREATE TRIGGER metadata_policy_fixed BEFORE UPDATE OF policy_json,policy_hash ON metadata
 BEGIN SELECT RAISE(ABORT,'POLICY_IMMUTABLE'); END;
CREATE TRIGGER metadata_no_delete BEFORE DELETE ON metadata
 BEGIN SELECT RAISE(ABORT,'POLICY_IMMUTABLE'); END;
CREATE TRIGGER clock_monotonic BEFORE UPDATE OF last_seen_utc ON metadata
 WHEN NEW.last_seen_utc < OLD.last_seen_utc
 BEGIN SELECT RAISE(ABORT,'CLOCK_ROLLBACK'); END;
CREATE TRIGGER reservation_identity_fixed BEFORE UPDATE OF
 request_hash,context_hash,day_utc,model,price_hash,budget_policy_hash,reserved_microusd,created_at_utc
 ON reservations BEGIN SELECT RAISE(ABORT,'RESERVATION_IMMUTABLE'); END;
CREATE TRIGGER reservation_transition BEFORE UPDATE ON reservations
 WHEN NOT(OLD.status='RESERVED' AND NEW.status IN ('COMPLETED','FAILED'))
 BEGIN SELECT RAISE(ABORT,'RESERVATION_STATE'); END;
CREATE TRIGGER reservation_no_delete BEFORE DELETE ON reservations
 BEGIN SELECT RAISE(ABORT,'RESERVATION_IMMUTABLE'); END;
CREATE TRIGGER events_no_update BEFORE UPDATE ON events
 BEGIN SELECT RAISE(ABORT,'AUDIT_APPEND_ONLY'); END;
CREATE TRIGGER events_no_delete BEFORE DELETE ON events
 BEGIN SELECT RAISE(ABORT,'AUDIT_APPEND_ONLY'); END;
"""
EXPECTED_OBJECTS = {
    ("table", "metadata"), ("table", "reservations"), ("table", "events"),
    *(('trigger', name) for name in (
        "metadata_policy_fixed", "metadata_no_delete", "clock_monotonic",
        "reservation_identity_fixed", "reservation_transition", "reservation_no_delete",
        "events_no_update", "events_no_delete")),
}


def validate_limits(value: dict) -> dict:
    if type(value) is not dict or set(value) != LIMIT_KEYS:
        raise LabError("PHASE2_BUDGET_INVALID")
    for key, amount in value.items():
        maximum = MAX_CALL_LIMIT if key.endswith("call_limit") else MAX_MONEY_LIMIT
        if type(amount) is not int or not 1 <= amount <= maximum:
            raise LabError("PHASE2_BUDGET_INVALID")
    if (not value["per_call_limit_microusd"] <= value["daily_limit_microusd"] <= value["total_limit_microusd"]
            or value["daily_call_limit"] > value["total_call_limit"]):
        raise LabError("PHASE2_BUDGET_INVALID")
    return dict(value)


def _utc(now=None) -> tuple[datetime, str]:
    value = datetime.now(timezone.utc) if now is None else now
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise LabError("PHASE2_CLOCK_INVALID")
    value = value.astimezone(timezone.utc)
    return value, value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_time(value: str) -> datetime:
    if type(value) is not str:
        raise LabError("PHASE2_BUDGET_CORRUPT")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if _utc(result)[1] != value:
            raise ValueError
        return result
    except (ValueError, TypeError, OverflowError):
        raise LabError("PHASE2_BUDGET_CORRUPT") from None


def _hash(value: str) -> str:
    if type(value) is not str or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise LabError("PHASE2_HASH_INVALID")
    return value


def _root(path: str | Path, *, create=False) -> Path:
    try:
        root = Path(path).absolute()
        if ".." in root.parts or any(p.is_symlink() for p in (root, *root.parents)):
            raise LabError("PHASE2_STATE_PATH")
        if not root.exists() and create:
            missing, current = [], root
            while not current.exists():
                missing.append(current)
                current = current.parent
            for directory in reversed(missing):
                try:
                    directory.mkdir(mode=0o700)
                except FileExistsError:
                    pass  # Another explicit initializer may have created it.
                info = directory.lstat()
                if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
                        or stat.S_IMODE(info.st_mode) != 0o700):
                    raise LabError("PHASE2_STATE_PERMISSIONS")
            if any(p.is_symlink() for p in (root, *root.parents)):
                raise LabError("PHASE2_STATE_PATH")
        info = root.stat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o700):
            raise LabError("PHASE2_STATE_PERMISSIONS")
        return root
    except LabError:
        raise
    except (OSError, ValueError, TypeError):
        raise LabError("PHASE2_STATE_PATH") from None


def _file_info(path: Path, *, max_bytes=MAX_STATE_BYTES):
    try:
        info = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > max_bytes):
            raise LabError("PHASE2_STATE_FILE")
        return info
    except LabError:
        raise
    except OSError:
        raise LabError("PHASE2_STATE_FILE") from None


def _read_marker(root: Path) -> dict:
    path = root / MARKER_NAME
    expected = _file_info(path, max_bytes=1024)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as stream:
            actual = os.fstat(stream.fileno())
            if (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino):
                raise LabError("PHASE2_STATE_CHANGED")
            marker = strict_json(stream.read(1025), max_bytes=1024)
    except LabError:
        raise
    except OSError:
        raise LabError("PHASE2_STATE_FILE") from None
    if (type(marker) is not dict or set(marker) != {"kind", "schema_version", "state_id", "policy_hash"}
            or marker["kind"] != STATE_KIND or type(marker["schema_version"]) is not int
            or marker["schema_version"] != SCHEMA_VERSION or type(marker["state_id"]) is not str
            or not re.fullmatch(r"[0-9a-f]{32}", marker["state_id"])):
        raise LabError("PHASE2_STATE_MARKER")
    _hash(marker["policy_hash"])
    return marker


def _create_file(path: Path, content: bytes = b"") -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


def _event(connection, at: str, kind: str, payload: dict):
    last = connection.execute("SELECT event_hash FROM events ORDER BY id DESC LIMIT 1").fetchone()
    previous = last[0] if last else "0" * 64
    value = {"at_utc": at, "kind": kind, "payload": payload, "previous_hash": previous}
    connection.execute("INSERT INTO events(at_utc,kind,payload_json,previous_hash,event_hash) VALUES(?,?,?,?,?)",
                       (at, kind, canonical_json(payload), previous, digest(value)))


def initialize(root: str | Path, limits: dict, *, now=None) -> dict:
    """Initialize an absent/empty private directory; never refill or overwrite."""
    limits = validate_limits(limits)
    instant, at = _utc(now)
    directory = _root(root, create=True)
    if any(directory.iterdir()):
        raise LabError("PHASE2_STATE_ALREADY_EXISTS")
    state_id = uuid.uuid4().hex
    policy = {
        "schema_version": SCHEMA_VERSION, "state_id": state_id, "initialized_at_utc": at,
        "limits": limits, "price": price_record(), "price_hash": PRICE_HASH,
        "reserved_per_attempt_microusd": RESERVATION_MICROUSD,
    }
    policy_hash = digest(policy)
    connection = None
    try:
        # O_EXCL prevents concurrent initializers overwriting a partly built DB.
        _create_file(directory / DB_NAME)
        connection = sqlite3.connect(directory / DB_NAME, isolation_level=None, timeout=5)
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("PRAGMA synchronous=FULL")
        connection.executescript(SCHEMA)
        connection.execute(f"PRAGMA application_id={APPLICATION_ID}")
        connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("INSERT INTO metadata VALUES(1,?,?,?)", (canonical_json(policy), policy_hash, at))
        _event(connection, at, "INITIALIZED", {"policy_hash": policy_hash})
        connection.execute("COMMIT")
        connection.close()
        connection = None
        marker = {"kind": STATE_KIND, "schema_version": SCHEMA_VERSION, "state_id": state_id,
                  "policy_hash": policy_hash}
        _create_file(directory / MARKER_NAME, (canonical_json(marker) + "\n").encode())
        descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except (OSError, sqlite3.Error):
        # Preserve partial state for diagnosis; never silently recreate it.
        raise LabError("PHASE2_BUDGET_INITIALIZE") from None
    finally:
        if connection is not None:
            connection.close()
    with Phase2Budget(directory) as budget:
        return budget.status(now=instant)


class Phase2Budget:
    def __init__(self, root: str | Path):
        self.root = _root(root)
        self.path = self.root / DB_NAME
        self.marker = _read_marker(self.root)
        info = _file_info(self.path)
        self._identity = (info.st_dev, info.st_ino)
        self.connection = None
        try:
            self.connection = sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True,
                                              isolation_level=None, timeout=5)
            self.connection.row_factory = sqlite3.Row
            self.connection.execute("PRAGMA busy_timeout=5000")
            self.connection.execute("PRAGMA trusted_schema=OFF")
            self.connection.execute("PRAGMA synchronous=FULL")
            with self._transaction(write=False):
                pass
        except BaseException as exc:
            self.close()
            if isinstance(exc, (OSError, sqlite3.Error)):
                raise LabError("PHASE2_BUDGET_STORE") from None
            raise

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        if self.connection is not None:
            self.connection.close()
            self.connection = None

    def _paths(self):
        if _root(self.root) != self.root or _read_marker(self.root) != self.marker:
            raise LabError("PHASE2_STATE_CHANGED")
        info = _file_info(self.path)
        if (info.st_dev, info.st_ino) != self._identity:
            raise LabError("PHASE2_STATE_CHANGED")
        for suffix in ("-journal", "-wal", "-shm"):
            path = Path(str(self.path) + suffix)
            if path.exists() or path.is_symlink():
                _file_info(path)
                if suffix != "-journal":
                    raise LabError("PHASE2_STATE_FILE")

    @contextlib.contextmanager
    def _transaction(self, *, write=True):
        try:
            if self.connection is None:
                raise LabError("PHASE2_BUDGET_CLOSED")
            self._paths()
            self.connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            self._validate_store()
            yield
            self.connection.execute("COMMIT")
        except BaseException as exc:
            if self.connection is not None and self.connection.in_transaction:
                try:
                    self.connection.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
            if isinstance(exc, (sqlite3.Error, OSError)):
                raise LabError("PHASE2_BUDGET_STORE") from None
            raise

    def _validate_store(self):
        cn = self.connection
        if (cn.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID
                or cn.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION
                or cn.execute("PRAGMA journal_mode").fetchone()[0] != "delete"):
            raise LabError("PHASE2_BUDGET_SCHEMA")
        objects = {(row[0], row[1]) for row in cn.execute(
            "SELECT type,name FROM sqlite_master WHERE type IN ('table','trigger')")}
        if objects != EXPECTED_OBJECTS:
            raise LabError("PHASE2_BUDGET_SCHEMA")
        metadata = cn.execute("SELECT * FROM metadata").fetchall()
        if len(metadata) != 1:
            raise LabError("PHASE2_BUDGET_CORRUPT")
        row = metadata[0]
        policy = strict_json(row["policy_json"])
        if (type(policy) is not dict or set(policy) != {
                "schema_version", "state_id", "initialized_at_utc", "limits", "price",
                "price_hash", "reserved_per_attempt_microusd"}
                or type(policy["schema_version"]) is not int or policy["schema_version"] != SCHEMA_VERSION
                or policy["state_id"] != self.marker["state_id"]
                or policy["price_hash"] != PRICE_HASH or canonical_json(policy["price"]) != canonical_json(price_record())
                or type(policy["reserved_per_attempt_microusd"]) is not int
                or policy["reserved_per_attempt_microusd"] != RESERVATION_MICROUSD
                or digest(policy) != row["policy_hash"] or row["policy_hash"] != self.marker["policy_hash"]):
            raise LabError("PHASE2_BUDGET_POLICY_MISMATCH")
        self.policy = policy
        self.policy_hash = row["policy_hash"]
        self.limits = validate_limits(policy["limits"])
        initialized = _parse_time(policy["initialized_at_utc"])
        last_seen = _parse_time(row["last_seen_utc"])
        if last_seen < initialized:
            raise LabError("PHASE2_BUDGET_CORRUPT")
        self.last_seen_utc = row["last_seen_utc"]
        records = {item["request_hash"]: self._record(item) for item in cn.execute("SELECT * FROM reservations")}
        if (len(records) > self.limits["total_call_limit"]
                or sum(item["reserved_microusd"] for item in records.values()) > self.limits["total_limit_microusd"]):
            raise LabError("PHASE2_BUDGET_CORRUPT")
        previous, reconstructed = "0" * 64, {}
        events = cn.execute("SELECT * FROM events ORDER BY id").fetchall()
        if not events or len(events) > 1 + 2 * self.limits["total_call_limit"]:
            raise LabError("PHASE2_AUDIT_INVALID")
        last_time = initialized
        for number, event in enumerate(events, 1):
            at = _parse_time(event["at_utc"])
            payload = strict_json(event["payload_json"])
            value = {"at_utc": event["at_utc"], "kind": event["kind"], "payload": payload,
                     "previous_hash": previous}
            if (event["id"] != number or event["previous_hash"] != previous
                    or event["event_hash"] != digest(value) or at < last_time or at > last_seen):
                raise LabError("PHASE2_AUDIT_INVALID")
            if number == 1:
                if event["kind"] != "INITIALIZED" or payload != {"policy_hash": self.policy_hash} or at != initialized:
                    raise LabError("PHASE2_AUDIT_INVALID")
            elif event["kind"] == "RESERVED":
                self._validate_record(payload)
                key = payload["request_hash"]
                if key in reconstructed or payload["status"] != "RESERVED" or payload["created_at_utc"] != event["at_utc"]:
                    raise LabError("PHASE2_AUDIT_INVALID")
                reconstructed[key] = payload
            elif event["kind"] == "FINISHED":
                self._validate_record(payload)
                key, old = payload["request_hash"], reconstructed.get(payload["request_hash"])
                if (not old or old["status"] != "RESERVED" or payload["status"] not in {"COMPLETED", "FAILED"}
                        or payload["completed_at_utc"] != event["at_utc"]):
                    raise LabError("PHASE2_AUDIT_INVALID")
                for field in ("request_hash", "context_hash", "attempt_id", "model", "price_hash",
                              "budget_policy_hash", "reserved_microusd", "created_at_utc"):
                    if old[field] != payload[field]:
                        raise LabError("PHASE2_AUDIT_INVALID")
                reconstructed[key] = payload
            else:
                raise LabError("PHASE2_AUDIT_INVALID")
            previous, last_time = event["event_hash"], at
        if reconstructed != records:
            raise LabError("PHASE2_AUDIT_INVALID")

    def _record(self, row) -> dict:
        value = dict(row)
        day, usage = value.pop("day_utc"), value.pop("usage_json")
        value["usage"] = strict_json(usage) if usage is not None else None
        value["attempt_id"] = value["request_hash"]
        self._validate_record(value)
        if day != _parse_time(value["created_at_utc"]).date().isoformat():
            raise LabError("PHASE2_BUDGET_CORRUPT")
        return value

    def _validate_record(self, value: dict):
        if type(value) is not dict or set(value) != RECORD_KEYS:
            raise LabError("PHASE2_BUDGET_CORRUPT")
        for key in ("request_hash", "context_hash", "attempt_id", "price_hash", "budget_policy_hash"):
            _hash(value[key])
        if (value["attempt_id"] != value["request_hash"] or value["model"] != MODEL
                or value["price_hash"] != PRICE_HASH or value["budget_policy_hash"] != self.policy_hash
                or type(value["reserved_microusd"]) is not int or value["reserved_microusd"] != RESERVATION_MICROUSD):
            raise LabError("PHASE2_BUDGET_CORRUPT")
        created = _parse_time(value["created_at_utc"])
        optional = ("completed_at_utc", "response_id", "usage", "candidate_hash", "response_hash", "actual_microusd")
        if value["status"] == "RESERVED":
            if any(value[key] is not None for key in optional):
                raise LabError("PHASE2_BUDGET_CORRUPT")
            return
        if value["status"] not in {"COMPLETED", "FAILED"} or _parse_time(value["completed_at_utc"]) < created:
            raise LabError("PHASE2_BUDGET_CORRUPT")
        if value["status"] == "COMPLETED" and any(value[key] is None for key in optional):
            raise LabError("PHASE2_BUDGET_CORRUPT")
        if value["response_id"] is not None and (type(value["response_id"]) is not str
                or not re.fullmatch(r"resp_[A-Za-z0-9_-]{1,155}", value["response_id"])):
            raise LabError("PHASE2_RESPONSE_ID")
        for key in ("candidate_hash", "response_hash"):
            if value[key] is not None:
                _hash(value[key])
        if value["usage"] is not None:
            if type(value["usage"]) is not dict or set(value["usage"]) != {"input_tokens", "output_tokens", "total_tokens"}:
                raise LabError("PHASE2_USAGE_INVALID")
            actual = usage_cost(value["model"], value["usage"])
            if type(value["actual_microusd"]) is not int or value["actual_microusd"] != actual or actual > RESERVATION_MICROUSD:
                raise LabError("PHASE2_USAGE_INVALID")
        elif value["actual_microusd"] is not None:
            raise LabError("PHASE2_USAGE_INVALID")

    def _clock(self, now=None) -> tuple[datetime, str]:
        instant, at = _utc(now)
        if at < self.last_seen_utc:
            raise LabError("PHASE2_CLOCK_ROLLBACK")
        return instant, at

    def _one(self, request_hash: str):
        row = self.connection.execute("SELECT * FROM reservations WHERE request_hash=?", (request_hash,)).fetchone()
        return self._record(row) if row is not None else None

    def get(self, request_hash: str) -> dict | None:
        _hash(request_hash)
        with self._transaction(write=False):
            return self._one(request_hash)

    def reserve(self, request_hash: str, context_hash: str, *, now=None, enforce_pricing=True) -> dict:
        # Only the isolated offline fixture runner may set False; no CLI override
        # exists for the live path. Monetary and attempt caps always apply.
        if type(enforce_pricing) is not bool:
            raise LabError("PHASE2_BUDGET_INVALID")
        _hash(request_hash)
        _hash(context_hash)
        with self._transaction():
            existing = self._one(request_hash)
            if existing is not None:
                if existing["context_hash"] != context_hash:
                    raise LabError("PHASE2_REQUEST_CONFLICT")
                return {"new": False, "record": existing}
            instant, at = self._clock(now)
            if enforce_pricing:
                ensure_price_current(instant)
            day = instant.date().isoformat()
            totals = self._totals(day)
            if RESERVATION_MICROUSD > self.limits["per_call_limit_microusd"]:
                raise LabError("PHASE2_PER_CALL_LIMIT")
            if totals["total_attempts"] >= self.limits["total_call_limit"]:
                raise LabError("PHASE2_TOTAL_CALL_LIMIT")
            if totals["daily_attempts"] >= self.limits["daily_call_limit"]:
                raise LabError("PHASE2_DAILY_CALL_LIMIT")
            if totals["reserved_microusd"] + RESERVATION_MICROUSD > self.limits["total_limit_microusd"]:
                raise LabError("PHASE2_TOTAL_LIMIT")
            if totals["daily_exposure_microusd"] + RESERVATION_MICROUSD > self.limits["daily_limit_microusd"]:
                raise LabError("PHASE2_DAILY_LIMIT")
            self.connection.execute("""INSERT INTO reservations
                (request_hash,context_hash,day_utc,status,model,price_hash,budget_policy_hash,reserved_microusd,created_at_utc)
                VALUES(?,?,?,'RESERVED',?,?,?,?,?)""",
                (request_hash, context_hash, day, MODEL, PRICE_HASH, self.policy_hash, RESERVATION_MICROUSD, at))
            self.connection.execute("UPDATE metadata SET last_seen_utc=? WHERE id=1", (at,))
            record = self._one(request_hash)
            _event(self.connection, at, "RESERVED", record)
            return {"new": True, "record": record}

    def finish(self, request_hash: str, status: str, *, response_id=None, model=None,
               usage=None, candidate_hash=None, response_hash=None, now=None) -> dict:
        _hash(request_hash)
        if status not in {"COMPLETED", "FAILED"}:
            raise LabError("PHASE2_RESERVATION_STATE")
        if model is not None and model != MODEL:
            raise LabError("PHASE2_RESPONSE_MODEL")
        if status == "COMPLETED" and model != MODEL:
            raise LabError("PHASE2_RESPONSE_MODEL")
        normalized = normalize_usage(usage) if usage is not None else None
        actual = usage_cost(model, normalized) if normalized is not None else None
        with self._transaction():
            existing = self._one(request_hash)
            if existing is None or existing["status"] != "RESERVED":
                raise LabError("PHASE2_RESERVATION_STATE")
            _, at = self._clock(now)
            updated = dict(existing, status=status, response_id=response_id, usage=normalized,
                           actual_microusd=actual, candidate_hash=candidate_hash, response_hash=response_hash,
                           completed_at_utc=at)
            self._validate_record(updated)
            cursor = self.connection.execute("""UPDATE reservations SET status=?,response_id=?,usage_json=?,
                actual_microusd=?,candidate_hash=?,response_hash=?,completed_at_utc=?
                WHERE request_hash=? AND status='RESERVED'""",
                (status, response_id, canonical_json(normalized) if normalized is not None else None,
                 actual, candidate_hash, response_hash, at, request_hash))
            if cursor.rowcount != 1:
                raise LabError("PHASE2_RESERVATION_STATE")
            self.connection.execute("UPDATE metadata SET last_seen_utc=? WHERE id=1", (at,))
            _event(self.connection, at, "FINISHED", updated)
            return updated

    def _totals(self, day: str) -> dict:
        row = self.connection.execute("""SELECT COUNT(*),COALESCE(SUM(reserved_microusd),0),
            COALESCE(SUM(actual_microusd),0),
            COALESCE(SUM(CASE WHEN actual_microusd IS NULL THEN 1 ELSE 0 END),0),
            COALESCE(SUM(CASE WHEN day_utc=? THEN 1 ELSE 0 END),0),
            COALESCE(SUM(CASE WHEN day_utc=? THEN reserved_microusd ELSE 0 END),0),
            COALESCE(SUM(CASE WHEN day_utc<? AND actual_microusd IS NULL THEN reserved_microusd ELSE 0 END),0),
            COALESCE(SUM(CASE WHEN day_utc<? AND actual_microusd IS NOT NULL
                AND substr(completed_at_utc,1,10)=? THEN reserved_microusd ELSE 0 END),0)
            FROM reservations""", (day, day, day, day, day)).fetchone()
        return dict(zip(("total_attempts", "reserved_microusd", "actual_known_microusd", "unknown_cost_attempts",
                         "daily_attempts", "current_day_reserved_microusd", "carried_unknown_microusd",
                         "completed_today_carried_microusd"), row),
                    daily_exposure_microusd=row[5] + row[6] + row[7])

    def status(self, *, now=None) -> dict:
        instant, at = _utc(now)
        with self._transaction(write=False):
            try:
                ensure_price_current(instant)
                current = True
            except LabError:
                current = False
            return {
                "schema_version": SCHEMA_VERSION, "state_id": self.marker["state_id"],
                "limits": dict(self.limits), "budget_policy_hash": self.policy_hash, "price_hash": PRICE_HASH,
                "price": price_record(), "reserved_per_attempt_microusd": RESERVATION_MICROUSD,
                "price_current": current, "clock_ok": at >= self.last_seen_utc,
                "last_seen_utc": self.last_seen_utc, "day_utc": instant.date().isoformat(),
                "audit_chain_ok": True, **self._totals(instant.date().isoformat()),
            }
