"""Private, append-audited research ledger. Never connects to the Bot database."""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .contracts import LabError, POLICY, canonical_json, digest, validate_candidate

MARKER = "coincheck-ai-lab-workspace-v1\n"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_workspace(path: str | Path) -> Path:
    path = Path(path).absolute()
    if ".." in path.parts or any(p.is_symlink() for p in (path, *path.parents)):
        raise LabError("WORKSPACE_SYMLINK_OR_TRAVERSAL")
    marker = path / ".ai-lab-workspace"
    if path.exists():
        if not path.is_dir():
            raise LabError("WORKSPACE_NOT_DIRECTORY")
        if any(path.iterdir()) and not marker.is_file():
            raise LabError("WORKSPACE_NOT_RESEARCH")
        if marker.is_symlink() or (marker.exists() and marker.read_text() != MARKER):
            raise LabError("WORKSPACE_MARKER_INVALID")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path, 0o700)
    if not marker.exists():
        with marker.open("x", encoding="utf-8") as stream:
            stream.write(MARKER)
        os.chmod(marker, 0o600)
    return path


class Ledger:
    def __init__(self, workspace: str | Path):
        self.root = safe_workspace(workspace)
        self._lock = None
        path = self.root / "research.sqlite3"
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise LabError("LEDGER_PATH_INVALID")
        self.cn = sqlite3.connect(path, timeout=5, isolation_level=None)
        self.cn.row_factory = sqlite3.Row
        os.chmod(path, 0o600)
        self.cn.execute("PRAGMA foreign_keys=ON")
        self.cn.execute("PRAGMA journal_mode=DELETE")
        self.cn.execute("PRAGMA synchronous=FULL")
        self.cn.executescript("""
        CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT OR IGNORE INTO metadata VALUES ('schema_version','1');
        CREATE TABLE IF NOT EXISTS runs(
          run_id TEXT PRIMARY KEY, manifest TEXT NOT NULL, manifest_hash TEXT NOT NULL,
          status TEXT NOT NULL, started_at TEXT NOT NULL, ended_at TEXT, result TEXT);
        CREATE TABLE IF NOT EXISTS trials(
          run_id TEXT NOT NULL REFERENCES runs(run_id), trial_index INTEGER NOT NULL,
          status TEXT NOT NULL, candidate TEXT, candidate_hash TEXT,
          result TEXT, error_code TEXT, reserved_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          PRIMARY KEY(run_id,trial_index));
        CREATE TABLE IF NOT EXISTS final_tests(
          dataset_hash TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id),
          candidate_hash TEXT NOT NULL, start_epoch INTEGER NOT NULL, end_epoch INTEGER NOT NULL,
          status TEXT NOT NULL, result TEXT);
        CREATE TABLE IF NOT EXISTS events(
          id INTEGER PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id),
          at TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL,
          previous_hash TEXT NOT NULL, event_hash TEXT NOT NULL);
        CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
          BEGIN SELECT RAISE(ABORT, 'APPEND_ONLY'); END;
        CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
          BEGIN SELECT RAISE(ABORT, 'APPEND_ONLY'); END;
        CREATE TRIGGER IF NOT EXISTS run_manifest_fixed BEFORE UPDATE OF manifest,manifest_hash ON runs
          BEGIN SELECT RAISE(ABORT, 'MANIFEST_IMMUTABLE'); END;
        """)
        if self.cn.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()[0] != "1":
            raise LabError("LEDGER_SCHEMA_VERSION")

    def close(self):
        self.cn.close()

    @contextlib.contextmanager
    def single_writer(self):
        lock_path = self.root / ".writer.lock"
        if lock_path.is_symlink():
            raise LabError("LOCK_PATH_INVALID")
        with lock_path.open("a+") as stream:
            os.chmod(lock_path, 0o600)
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise LabError("RESEARCH_ALREADY_RUNNING") from None
            try:
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)

    @contextlib.contextmanager
    def tx(self):
        files=list(self.root.rglob("*"))
        if any(p.is_symlink() for p in files):
            raise LabError("WORKSPACE_SYMLINK_OR_TRAVERSAL")
        if sum(p.stat().st_size for p in files if p.is_file()) > POLICY["max_history_bytes"]:
            raise LabError("RESEARCH_STORAGE_LIMIT")
        self.cn.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self.cn.execute("ROLLBACK")
            raise
        else:
            self.cn.execute("COMMIT")

    def _event(self, run_id: str, kind: str, payload: dict):
        previous = self.cn.execute("SELECT event_hash FROM events ORDER BY id DESC LIMIT 1").fetchone()
        previous = previous[0] if previous else "0" * 64
        at = utc_now()
        value = {"run_id": run_id, "at": at, "kind": kind, "payload": payload, "previous_hash": previous}
        self.cn.execute("INSERT INTO events(run_id,at,kind,payload,previous_hash,event_hash) VALUES(?,?,?,?,?,?)",
                        (run_id, at, kind, canonical_json(payload), previous, digest(value)))

    def begin_run(self, run_id: str, manifest: dict) -> dict:
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", run_id):
            raise LabError("RUN_ID_INVALID")
        if not self.verify_events():
            raise LabError("AUDIT_CHAIN_INVALID")
        with self.tx():
            old = self.cn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
            if old:
                if old["manifest_hash"] != digest(manifest):
                    raise LabError("RESUME_MANIFEST_MISMATCH")
                return dict(old)
            self.cn.execute("INSERT INTO runs VALUES(?,?,?,?,?,?,?)",
                            (run_id, canonical_json(manifest), digest(manifest), "RUNNING", utc_now(), None, None))
            self._event(run_id, "RUN_CREATED", {"manifest_hash": digest(manifest)})
        return dict(self.cn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone())

    def trials(self, run_id: str) -> list[dict]:
        return [dict(x) for x in self.cn.execute("SELECT * FROM trials WHERE run_id=? ORDER BY trial_index", (run_id,))]

    def reserve_trial(self, run_id: str, index: int, limit: int) -> bool:
        if type(index) is not int or type(limit) is not int or not 0 <= index < limit <= POLICY["max_trials"]:
            raise LabError("TRIAL_LIMIT")
        with self.tx():
            run = self.cn.execute("SELECT status,manifest FROM runs WHERE run_id=?", (run_id,)).fetchone()
            if not run or run["status"] != "RUNNING":
                raise LabError("RUN_NOT_ACTIVE")
            if limit != json.loads(run["manifest"])["max_trials"]:
                raise LabError("TRIAL_MANIFEST_LIMIT")
            if self.cn.execute("SELECT 1 FROM trials WHERE run_id=? AND trial_index=?", (run_id,index)).fetchone():
                return False
            count = self.cn.execute("SELECT count(*) FROM trials WHERE run_id=?", (run_id,)).fetchone()[0]
            if count >= limit:
                raise LabError("TRIAL_LIMIT")
            now = utc_now()
            self.cn.execute("INSERT INTO trials VALUES(?,?,?,?,?,?,?,?,?)",
                            (run_id,index,"RESERVED",None,None,None,None,now,now))
            self._event(run_id,"TRIAL_RESERVED", {"trial_index": index})
            return True

    def candidate(self, run_id: str, index: int, candidate: dict, candidate_hash: str):
        checked = validate_candidate(candidate)
        if checked.strategy_hash != candidate_hash:
            raise LabError("CANDIDATE_HASH_MISMATCH")
        with self.tx():
            cursor = self.cn.execute("""UPDATE trials SET status='GENERATED',candidate=?,candidate_hash=?,updated_at=?
                         WHERE run_id=? AND trial_index=? AND status='RESERVED'""",
                         (canonical_json(candidate), candidate_hash, utc_now(), run_id,index))
            if cursor.rowcount != 1:
                raise LabError("TRIAL_TRANSITION")
            self._event(run_id,"CANDIDATE_SAVED", {"trial_index": index, "candidate_hash": candidate_hash})

    def record_input(self, run_id: str, index: int, context: dict):
        if len(canonical_json(context).encode()) > POLICY["max_json_bytes"]:
            raise LabError("GENERATOR_INPUT_SIZE")
        with self.tx():
            row = self.cn.execute("SELECT status FROM trials WHERE run_id=? AND trial_index=?",(run_id,index)).fetchone()
            if not row or row[0] != "RESERVED":
                raise LabError("TRIAL_TRANSITION")
            self._event(run_id,"GENERATOR_INPUT", {"trial_index":index,"input_hash":digest(context),"context":context})

    def finish_trial(self, run_id: str, index: int, status: str, result: dict | None = None, error_code: str | None = None):
        if status not in ("REJECTED", "SHORTLISTED", "FAILED", "DUPLICATE"):
            raise LabError("TRIAL_STATUS_INVALID")
        if error_code and not re.fullmatch(r"[A-Z0-9_]{1,80}", error_code):
            raise LabError("ERROR_CODE_INVALID")
        with self.tx():
            cursor = self.cn.execute("""UPDATE trials SET status=?,result=?,error_code=?,updated_at=?
                     WHERE run_id=? AND trial_index=? AND status IN ('RESERVED','GENERATED')""",
                     (status, canonical_json(result) if result is not None else None,
                      error_code,utc_now(),run_id,index))
            if cursor.rowcount != 1:
                raise LabError("TRIAL_TRANSITION")
            self._event(run_id,"TRIAL_FINISHED", {"trial_index": index, "status": status, "error_code": error_code})

    def reserve_final(self, run_id: str, dataset_hash: str, candidate_hash: str, start: int, end: int):
        if type(start) is not int or type(end) is not int or start >= end:
            raise LabError("HOLDOUT_INTERVAL")
        with self.tx():
            # Reservations survive failures. Adjacent snapshots cannot reuse overlapping final windows.
            existing = self.cn.execute("""SELECT * FROM final_tests WHERE dataset_hash=?
                  OR NOT(end_epoch<=? OR start_epoch>=?)""", (dataset_hash,start,end)).fetchone()
            if existing:
                if (existing["run_id"] == run_id and existing["candidate_hash"] == candidate_hash
                    and existing["dataset_hash"] == dataset_hash
                    and existing["start_epoch"] == start and existing["end_epoch"] == end
                    and existing["status"] == "COMPLETED"):
                    return json.loads(existing["result"])
                raise LabError("HOLDOUT_ALREADY_CONSUMED")
            self.cn.execute("INSERT INTO final_tests VALUES(?,?,?,?,?,?,?)",
                            (dataset_hash,run_id,candidate_hash,start,end,"RESERVED",None))
            self._event(run_id,"HOLDOUT_RESERVED", {"dataset_hash": dataset_hash, "candidate_hash": candidate_hash})
        return None

    def finish_final(self, run_id: str, dataset_hash: str, result: dict):
        with self.tx():
            cursor = self.cn.execute("UPDATE final_tests SET status='COMPLETED', result=? WHERE dataset_hash=? AND run_id=? AND status='RESERVED'",
                                    (canonical_json(result),dataset_hash,run_id))
            if cursor.rowcount != 1:
                raise LabError("HOLDOUT_TRANSITION")
            self._event(run_id,"HOLDOUT_FINISHED", {"result_hash": digest(result)})

    def finish_run(self, run_id: str, result: dict):
        with self.tx():
            cursor = self.cn.execute("UPDATE runs SET status='COMPLETED',ended_at=?,result=? WHERE run_id=? AND status='RUNNING'",
                                    (utc_now(), canonical_json(result),run_id))
            if cursor.rowcount != 1:
                raise LabError("RUN_TRANSITION")
            self._event(run_id,"RUN_COMPLETED", {"result_hash": digest(result)})

    def snapshot(self, run_id: str) -> dict:
        row = self.cn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if not row:
            raise LabError("RUN_NOT_FOUND")
        run = dict(row)
        run["manifest"] = json.loads(run["manifest"])
        run["result"] = json.loads(run["result"]) if run["result"] else None
        trials = self.trials(run_id)
        for trial in trials:
            for key in ("candidate", "result"):
                trial[key] = json.loads(trial[key]) if trial[key] else None
        finals = [dict(x) for x in self.cn.execute("SELECT * FROM final_tests WHERE run_id=?",(run_id,))]
        for final in finals:
            final["result"] = json.loads(final["result"]) if final["result"] else None
        return {"run": run, "trials": trials, "final_tests": finals, "event_chain_ok": self.verify_events()}

    def verify_events(self) -> bool:
        previous = "0" * 64
        for row in self.cn.execute("SELECT * FROM events ORDER BY id"):
            value = {"run_id":row["run_id"],"at":row["at"],"kind":row["kind"],
                     "payload":json.loads(row["payload"]),"previous_hash":previous}
            if row["previous_hash"] != previous or row["event_hash"] != digest(value):
                return False
            previous = row["event_hash"]
        return True
