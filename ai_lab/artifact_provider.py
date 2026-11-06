"""Exact-class, network-free provider for an already validated single candidate."""
from __future__ import annotations

from .contracts import LabError, digest, strict_json, canonical_json, validate_candidate
from .phase2_artifacts import validate_artifact
from .proposal_context import MAX_HANDOFF_BYTES, validate_envelope
from .provider import validate_context


class ArtifactProvider:
    __slots__ = ("_raw",)

    def __init__(self, artifact: dict):
        # Immutable bytes detach the candidate from caller mutation and file races.
        self._raw = canonical_json(validate_artifact(artifact))

    def _artifact(self) -> dict:
        return validate_artifact(strict_json(self._raw, max_bytes=MAX_HANDOFF_BYTES))

    def verify_envelope(self, expected: dict) -> None:
        validate_artifact(self._artifact(), expected_envelope=validate_envelope(expected))

    def verify_saved_trials(self, rows: list[dict], *, complete: bool = False) -> None:
        """A valid audit chain alone does not bind mutable SQLite state rows."""
        artifact = self._artifact()
        if len(rows) > 1 or (complete and len(rows) != 1):
            raise LabError("PHASE2_LEDGER_CANDIDATE_MISMATCH")
        states = {"RESERVED", "GENERATED", "REJECTED", "SHORTLISTED", "FAILED", "DUPLICATE"}
        for row in rows:
            if (type(row["trial_index"]) is not int or row["trial_index"] != 0
                    or row["status"] not in states
                    or (complete and row["status"] in {"RESERVED", "GENERATED"})):
                raise LabError("PHASE2_LEDGER_CANDIDATE_MISMATCH")
            if row["candidate"] is None:
                if row["candidate_hash"] is not None or row["status"] not in {"RESERVED", "FAILED"}:
                    raise LabError("PHASE2_LEDGER_CANDIDATE_MISMATCH")
                continue
            try:
                candidate = validate_candidate(strict_json(row["candidate"]))
            except LabError:
                raise LabError("PHASE2_LEDGER_CANDIDATE_MISMATCH") from None
            if (row["status"] == "RESERVED"
                    or candidate.strategy_hash != row["candidate_hash"]
                    or candidate.strategy_hash != artifact["candidate_hash"]
                    or candidate.to_dict() != artifact["candidate"]):
                raise LabError("PHASE2_LEDGER_CANDIDATE_MISMATCH")

    def metadata(self) -> dict:
        artifact = self._artifact()
        envelope = artifact["envelope"]
        return {"provider_id": "artifact-file-v1", "network_enabled": False,
                "artifact_hash": artifact["artifact_hash"],
                "candidate_hash": artifact["candidate_hash"],
                "envelope_hash": envelope["envelope_hash"],
                "request_hash": envelope["binding"]["request_hash"],
                "model_id": artifact["generation"]["model"],
                "generation_mode": artifact["generation"]["mode"],
                "producer_authentication": "not_provided; content hashes are not signatures",
                "generation_receipt": artifact["generation"]}

    def propose(self, context: dict, trial_index: int) -> dict:
        artifact = self._artifact()
        if type(trial_index) is not int or trial_index != 0:
            raise LabError("PHASE2_SINGLE_CANDIDATE_REQUIRED")
        if digest(validate_context(context)) != artifact["envelope"]["binding"]["context_hash"]:
            raise LabError("PHASE2_ARTIFACT_CONTEXT_MISMATCH")
        return artifact["candidate"]
