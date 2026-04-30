"""Schemas for task pipeline event APIs."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import field_validator, model_validator
from sqlmodel import Field, SQLModel

# Required keys inside the ``evidence`` dict for ``state="model_fallback"``
# events. The shape matches OpenClaw's ``model.fallback_step`` trajectory
# event so payloads can be stored verbatim from gateway logs.
MODEL_FALLBACK_REQUIRED_EVIDENCE_KEYS: frozenset[str] = frozenset(
    {"from_model", "to_model", "reason"}
)

PipelineState = Literal[
    "code_changed",
    "committed",
    "built",
    "deployed",
    "live_build_verified",
    "runtime_verified",
    "qa_ready",
    # ``model_fallback`` is informational only; it captures a fallback chain
    # step recorded by the worker so reviewers see which models actually
    # produced the packet. It is never part of ``required_states`` and never
    # gates review readiness. Mirrors the OpenClaw 2026.4.27 first-class
    # ``model.fallback_step`` trajectory event (#71744).
    "model_fallback",
]

# States that are recorded for evidence-trail purposes only and must not
# participate in pipeline readiness gates. Anything in this set is excluded
# from required-state computation regardless of how it was registered.
INFORMATIONAL_PIPELINE_STATES: frozenset[str] = frozenset({"model_fallback"})


class TaskPipelineEventCreate(SQLModel):
    """Payload for recording one structured pipeline event."""

    state: PipelineState
    source: str = "api"
    commit_sha: str | None = None
    artifact_hash: str | None = None
    deploy_target: str | None = None
    live_sha: str | None = None
    evidence: dict[str, object] | None = Field(default=None)
    overwrite: bool = False

    @field_validator(
        "source",
        "commit_sha",
        "artifact_hash",
        "deploy_target",
        "live_sha",
        mode="before",
    )
    @classmethod
    def normalize_optional_text(cls, value: object) -> object | None:
        if value is None:
            return None
        if not isinstance(value, str):
            return value
        stripped = value.strip()
        return stripped or None

    @model_validator(mode="after")
    def _validate_model_fallback_evidence(self) -> TaskPipelineEventCreate:
        """``model_fallback`` events must carry the trajectory-event fields.

        The state itself is informational, but if a worker records one we
        require the evidence dict to include ``from_model``, ``to_model``,
        and ``reason`` so downstream reviewers can see which model produced
        the eventual packet without re-reading gateway logs.
        """
        if self.state != "model_fallback":
            return self
        evidence = self.evidence
        if not isinstance(evidence, dict) or not evidence:
            raise ValueError(
                "model_fallback events require an evidence dict containing "
                f"keys: {sorted(MODEL_FALLBACK_REQUIRED_EVIDENCE_KEYS)}."
            )
        missing = sorted(
            key for key in MODEL_FALLBACK_REQUIRED_EVIDENCE_KEYS if key not in evidence
        )
        if missing:
            raise ValueError(
                "model_fallback evidence is missing required keys: "
                f"{missing}. Required: "
                f"{sorted(MODEL_FALLBACK_REQUIRED_EVIDENCE_KEYS)}."
            )
        return self


class TaskPipelineEventRead(SQLModel):
    """Serialized pipeline event."""

    id: UUID
    board_id: UUID
    task_id: UUID
    agent_id: UUID | None
    state: str
    source: str
    commit_sha: str | None
    artifact_hash: str | None
    deploy_target: str | None
    live_sha: str | None
    evidence: dict[str, object] | None
    created_at: datetime


class TaskPipelineStateRead(SQLModel):
    """Current structured pipeline readiness for a task."""

    task_id: UUID
    required_states: list[str]
    present_states: list[str]
    missing_states: list[str]
    ready: bool
    events: list[TaskPipelineEventRead]
    # Most recent ``model_fallback`` event for this task, if any. Surfaced
    # inline so reviewers see which model actually produced the packet
    # without paging through ``events``. ``None`` when no fallback occurred.
    latest_fallback_step: TaskPipelineEventRead | None = None
