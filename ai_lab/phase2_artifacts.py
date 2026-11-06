"""Bounded, immutable handoff files. No HTTP, app, evaluator or DB imports."""
from __future__ import annotations

import os
import re
import secrets
import stat
from datetime import datetime, timedelta
from pathlib import Path

from .contracts import LabError, canonical_json, digest, strict_json, validate_candidate
from .proposal_context import (MAX_HANDOFF_BYTES, MODEL, detached, valid_hash,
                               validate_envelope)
from .phase2_policy import PRICE_HASH, RESERVATION_MICROUSD, normalize_usage, usage_cost

ARTIFACT_VERSION = "ai-lab-proposal-artifact-v1"
_ID = re.compile(r"resp_[A-Za-z0-9_-]{1,155}")


def _parent(path: Path, *, create: bool) -> tuple[int, str]:
    """Walk every parent with O_NOFOLLOW, retaining its descriptor for file IO."""
    path = Path(path)
    if ".." in path.parts or not path.name or path.name in (".", ".."):
        raise LabError("PHASE2_HANDOFF_PATH")
    absolute = path.absolute()
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = None
    try:
        descriptor = os.open(absolute.anchor, flags)
        for part in absolute.parts[1:-1]:
            if create:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=descriptor)
                except FileExistsError:
                    pass
            child = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        if create:
            parent_info = os.fstat(descriptor)
            if parent_info.st_mode & 0o077 or parent_info.st_uid != os.geteuid():
                raise LabError("PHASE2_OUTPUT_DIRECTORY_PRIVATE")
        return descriptor, absolute.name
    except (OSError, LabError):
        if descriptor is not None:
            os.close(descriptor)
        raise LabError("PHASE2_HANDOFF_PATH") from None


def read_json_file(path: Path) -> dict:
    """At most 64 KiB; nonsecret read-only handoff mounts may have mode 0444."""
    parent = stream = None
    try:
        parent, name = _parent(path, create=False)
        stream = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        before = os.fstat(stream)
        if (not stat.S_ISREG(before.st_mode) or before.st_mode & 0o022
                or before.st_size > MAX_HANDOFF_BYTES):
            raise LabError("PHASE2_HANDOFF_FILE")
        raw = bytearray()
        while len(raw) <= MAX_HANDOFF_BYTES:
            chunk = os.read(stream, min(8192, MAX_HANDOFF_BYTES + 1 - len(raw)))
            if not chunk:
                break
            raw.extend(chunk)
        after = os.fstat(stream)
        identity = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        if (identity(before) != identity(after) or len(raw) != before.st_size
                or len(raw) > MAX_HANDOFF_BYTES):
            raise LabError("PHASE2_HANDOFF_CHANGED")
        value = strict_json(bytes(raw), max_bytes=MAX_HANDOFF_BYTES)
        if not isinstance(value, dict):
            raise LabError("PHASE2_HANDOFF_OBJECT")
        return value
    except OSError:
        raise LabError("PHASE2_HANDOFF_UNREADABLE") from None
    finally:
        if stream is not None:
            os.close(stream)
        if parent is not None:
            os.close(parent)


def write_json_file(path: Path, value: dict) -> Path:
    """Atomically publish mode 0600 without replacing an existing inode."""
    safe = detached(value)
    body = (canonical_json(safe) + "\n").encode("utf-8")
    if len(body) > MAX_HANDOFF_BYTES:
        raise LabError("PHASE2_HANDOFF_SIZE")
    parent = stream = None
    temporary = None
    try:
        parent, name = _parent(path, create=True)
        temporary = ".pending-" + secrets.token_hex(16)
        stream = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=parent)
        with os.fdopen(stream, "wb", closefd=True) as handle:
            stream = None
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, name, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False)
        except FileExistsError:
            if read_json_file(path) != safe:
                raise LabError("PHASE2_HANDOFF_CONFLICT") from None
        os.unlink(temporary, dir_fd=parent)
        temporary = None
        os.fsync(parent)
        return Path(path)
    except OSError:
        raise LabError("PHASE2_HANDOFF_STORE") from None
    finally:
        if stream is not None:
            os.close(stream)
        if temporary is not None and parent is not None:
            try:
                os.unlink(temporary, dir_fd=parent)
            except OSError:
                pass
        if parent is not None:
            os.close(parent)


def read_envelope(path: Path) -> dict:
    return validate_envelope(read_json_file(path))


def _time(value):
    if not isinstance(value, str) or len(value) > 40:
        raise LabError("PHASE2_GENERATION_TIME")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise LabError("PHASE2_GENERATION_TIME") from None
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise LabError("PHASE2_GENERATION_TIME")
    return parsed


def _generation(value: dict, envelope: dict, candidate_hash: str) -> dict:
    keys = {"mode", "request_hash", "context_hash", "status", "attempt_id", "model",
            "response_id", "usage", "budget_policy_hash", "price_hash", "reserved_microusd",
            "actual_microusd", "created_at_utc", "completed_at_utc", "candidate_hash", "response_hash"}
    if not isinstance(value, dict) or set(value) != keys:
        raise LabError("PHASE2_GENERATION_KEYS")
    if value["mode"] not in ("fixture", "openai_responses") or value["status"] != "COMPLETED" or value["model"] != MODEL:
        raise LabError("PHASE2_GENERATION_STATE")
    binding = envelope["binding"]
    if (value["request_hash"] != binding["request_hash"] or value["context_hash"] != binding["context_hash"]
            or value["attempt_id"] != value["request_hash"]
            or value["candidate_hash"] != candidate_hash):
        raise LabError("PHASE2_GENERATION_BINDING")
    if any(not valid_hash(value[key]) for key in
           ("attempt_id", "budget_policy_hash", "price_hash", "response_hash")):
        raise LabError("PHASE2_GENERATION_HASH")
    if value["price_hash"] != PRICE_HASH:
        raise LabError("PHASE2_GENERATION_PRICE")
    if not isinstance(value["response_id"], str) or not _ID.fullmatch(value["response_id"]):
        raise LabError("PHASE2_GENERATION_ID")
    usage = value["usage"]
    if not isinstance(usage, dict) or set(usage) != {"input_tokens", "output_tokens", "total_tokens"}:
        raise LabError("PHASE2_GENERATION_USAGE")
    normalize_usage(usage)
    reserved, actual = value["reserved_microusd"], value["actual_microusd"]
    if (type(reserved) is not int or type(actual) is not int
            or reserved != RESERVATION_MICROUSD
            or not 0 <= actual <= reserved
            or actual != usage_cost(MODEL, usage)):
        raise LabError("PHASE2_GENERATION_COST")
    if _time(value["created_at_utc"]) > _time(value["completed_at_utc"]):
        raise LabError("PHASE2_GENERATION_TIME")
    if value["mode"] == "fixture":
        if not value["response_id"].startswith("resp_fixture_"):
            raise LabError("PHASE2_FIXTURE_RECEIPT")
    elif (not value["response_id"].startswith("resp_")
          or value["response_id"].startswith("resp_fixture_")
          or usage["input_tokens"] <= 0 or reserved <= 0):
        raise LabError("PHASE2_OPENAI_RECEIPT")
    return value


def validate_artifact(value: dict, *, expected_envelope: dict | None = None) -> dict:
    safe = detached(value)
    keys = {"version", "envelope", "candidate", "candidate_hash", "generation", "artifact_hash"}
    if set(safe) != keys or safe["version"] != ARTIFACT_VERSION:
        raise LabError("PHASE2_ARTIFACT_KEYS")
    envelope = validate_envelope(safe["envelope"])
    if expected_envelope is not None and envelope != validate_envelope(expected_envelope):
        raise LabError("PHASE2_ARTIFACT_CONTEXT_MISMATCH")
    candidate = validate_candidate(safe["candidate"])
    if safe["candidate_hash"] != candidate.strategy_hash or safe["candidate"] != candidate.to_dict():
        raise LabError("PHASE2_ARTIFACT_CANDIDATE_HASH")
    generation = _generation(safe["generation"], envelope, candidate.strategy_hash)
    payload = {"version": safe["version"], "envelope": envelope, "candidate": candidate.to_dict(),
               "candidate_hash": candidate.strategy_hash, "generation": generation}
    if not valid_hash(safe["artifact_hash"]) or safe["artifact_hash"] != digest(payload):
        raise LabError("PHASE2_ARTIFACT_HASH_MISMATCH")
    return {**payload, "artifact_hash": safe["artifact_hash"]}


def save_candidate(root: Path, envelope: dict, candidate: dict, *, generation: dict) -> Path:
    safe_envelope = validate_envelope(envelope)
    safe_candidate = validate_candidate(candidate)
    payload = {"version": ARTIFACT_VERSION, "envelope": safe_envelope,
               "candidate": safe_candidate.to_dict(), "candidate_hash": safe_candidate.strategy_hash,
               "generation": generation}
    artifact = validate_artifact({**payload, "artifact_hash": digest(payload)})
    return write_json_file(Path(root) / (safe_envelope["binding"]["request_hash"] + ".json"), artifact)


def load_candidate(path: Path, *, expected_envelope: dict | None = None) -> dict:
    return validate_artifact(read_json_file(path), expected_envelope=expected_envelope)
