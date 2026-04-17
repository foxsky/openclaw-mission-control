from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.tasks import TaskUpdate
from scripts.normalize_board_delivery_contract import (
    BoardDeliveryContractManifest,
    NormalizationTaskPatch,
    summarize_patch,
)


def test_normalization_patch_requires_non_comment_updates() -> None:
    with pytest.raises(ValidationError, match="at least one non-comment task field"):
        NormalizationTaskPatch(
            task_id=uuid4(),
            update=TaskUpdate(comment="normalize this task"),
        )


def test_normalization_patch_summary_lists_changed_fields() -> None:
    patch = NormalizationTaskPatch(
        task_id=uuid4(),
        title="Track A.1",
        update=TaskUpdate(
            review_packet_type="frontend_ui",
            validation_target="http://192.168.2.60:3000",
            validation_target_kind="live_url",
            validation_target_scope="runtime",
            comment="Attach live review contract.",
        ),
    )

    summary = summarize_patch(patch)

    assert "Track A.1" in summary
    assert "review_packet_type" in summary
    assert "validation_target" in summary
    assert "comment" not in summary


def test_manifest_requires_tasks() -> None:
    with pytest.raises(ValidationError, match="at least one task patch"):
        BoardDeliveryContractManifest(
            board_id=uuid4(),
            tasks=[],
        )
