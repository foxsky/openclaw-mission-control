from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.api import tasks as tasks_api
from app.schemas.tasks import (
    TaskCreate,
    TaskRead,
    TaskUpdate,
    delivery_contract_missing_fields,
)


def test_task_update_accepts_rework_status() -> None:
    model = TaskUpdate(status="rework")
    assert model.status == "rework"


def test_task_update_accepts_cancelled_status() -> None:
    model = TaskUpdate(status="cancelled")
    assert model.status == "cancelled"


def test_status_filter_accepts_rework_and_cancelled() -> None:
    assert tasks_api._status_values("review,rework,cancelled") == [
        "review",
        "rework",
        "cancelled",
    ]


def test_status_filter_rejects_unknown_status() -> None:
    with pytest.raises(HTTPException) as exc:
        tasks_api._status_values("archived")

    assert exc.value.status_code == 422
    assert exc.value.detail == "Unsupported task status filter."


def test_task_create_accepts_control_plane_metadata() -> None:
    model = TaskCreate(
        title="T",
        review_packet_type="frontend_ui",
        validation_target="http://192.168.2.60:3000",
        validation_target_kind="live_url",
        validation_target_scope="review",
        operator_decision_required=True,
        operator_decision_summary="Awaiting operator pricing decision.",
    )
    assert model.review_packet_type == "frontend_ui"
    assert model.validation_target_kind == "live_url"
    assert model.validation_target_scope == "review"
    assert model.operator_decision_required is True


def test_task_update_accepts_control_plane_metadata() -> None:
    model = TaskUpdate(
        review_packet_type="mixed",
        validation_target="/shared/worktree",
        validation_target_kind="workspace",
        validation_target_scope="runtime",
        operator_decision_required=True,
        operator_decision_summary="Awaiting legal text from operator.",
    )
    assert model.review_packet_type == "mixed"
    assert model.validation_target_kind == "workspace"
    assert model.validation_target_scope == "runtime"
    assert model.operator_decision_required is True


def test_task_read_exposes_control_plane_metadata() -> None:
    model = TaskRead(
        id="00000000-0000-4000-8000-000000000001",
        board_id="00000000-0000-4000-8000-000000000002",
        title="T",
        status="inbox",
        priority="medium",
        created_by_user_id=None,
        in_progress_at=None,
        cancelled_at=None,
        created_at="2026-04-16T00:00:00Z",
        updated_at="2026-04-16T00:00:00Z",
        review_packet_type="content_copy",
        validation_target="pricing operator brief",
        validation_target_kind="other",
        validation_target_scope="all",
        operator_decision_required=True,
        operator_decision_summary="Awaiting operator commitments.",
    )
    assert model.review_packet_type == "content_copy"
    assert model.validation_target == "pricing operator brief"
    assert model.operator_decision_required is True


def test_delivery_contract_requires_review_packet_for_active_status() -> None:
    assert delivery_contract_missing_fields(
        status="in_progress",
        review_packet_type=None,
        validation_target=None,
        validation_target_kind=None,
        validation_target_scope=None,
    ) == ["review_packet_type"]


def test_delivery_contract_requires_validation_target_for_frontend_review() -> None:
    assert delivery_contract_missing_fields(
        status="review",
        review_packet_type="frontend_ui",
        validation_target=None,
        validation_target_kind=None,
        validation_target_scope=None,
    ) == [
        "validation_target",
        "validation_target_kind",
        "validation_target_scope",
    ]


def test_delivery_contract_allows_content_copy_without_validation_target() -> None:
    assert (
        delivery_contract_missing_fields(
            status="review",
            review_packet_type="content_copy",
            validation_target=None,
            validation_target_kind=None,
            validation_target_scope=None,
        )
        == []
    )
