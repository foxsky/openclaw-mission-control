"""Unit tests for ``mc_client.py``.

Validates the offline pieces — argparse wiring, evidence-JSON parsing,
env-var fallback resolution, and stdin-message handling. HTTP plumbing
is exercised via a fixture that stubs ``urllib.request.urlopen`` so the
tests stay fully offline.
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path
from typing import Any, cast
from unittest import mock

import pytest

# Same dynamic-import pattern as test_ingest_model_fallbacks.py.
_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "mc_client.py"
_spec = importlib.util.spec_from_file_location("mc_client", _SCRIPT)
assert _spec is not None and _spec.loader is not None
_module = importlib.util.module_from_spec(_spec)
sys.modules["mc_client"] = _module
_spec.loader.exec_module(_module)

build_parser = _module.build_parser
main = _module.main
HttpError = _module.HttpError
_parse_evidence = _module._parse_evidence


# --- evidence parsing ---


class TestParseEvidence:
    def test_none_returns_none(self) -> None:
        assert _parse_evidence(None) is None

    def test_empty_string_returns_none(self) -> None:
        assert _parse_evidence("   ") is None

    def test_valid_json_object(self) -> None:
        result = _parse_evidence('{"a": 1, "b": "x"}')
        assert result == {"a": 1, "b": "x"}

    def test_invalid_json_exits(self) -> None:
        with pytest.raises(SystemExit) as exc:
            _parse_evidence("{not json")
        assert "valid JSON" in str(exc.value)

    def test_json_array_rejected(self) -> None:
        with pytest.raises(SystemExit) as exc:
            _parse_evidence('[1, 2, 3]')
        assert "JSON object" in str(exc.value)


# --- argparse wiring ---


class TestArgparse:
    def test_task_read_requires_task(self) -> None:
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["task-read"])

    def test_task_read_minimal(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["task-read", "--task", "abc"])
        assert args.command == "task-read"
        assert args.task == "abc"

    def test_comment_create_requires_message(self) -> None:
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["comment-create", "--task", "abc"])

    def test_pipeline_event_state_constrained(self) -> None:
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(
                ["pipeline-event-create", "--task", "abc", "--state", "bogus_state"]
            )

    def test_pipeline_event_accepts_model_fallback(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            [
                "pipeline-event-create",
                "--task",
                "abc",
                "--state",
                "model_fallback",
                "--evidence",
                '{"from_model":"a","to_model":"b","reason":"x"}',
            ]
        )
        assert args.state == "model_fallback"
        assert args.evidence

    def test_review_event_role_constrained(self) -> None:
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(
                [
                    "review-event-create",
                    "--task",
                    "abc",
                    "--reviewer-role",
                    "operator",
                    "--verdict",
                    "pass",
                ]
            )

    def test_review_event_verdict_constrained(self) -> None:
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(
                [
                    "review-event-create",
                    "--task",
                    "abc",
                    "--reviewer-role",
                    "architect",
                    "--verdict",
                    "maybe",
                ]
            )

    def test_review_event_rejects_partial_not_in_real_schema(self) -> None:
        """Codex F3: 'partial' was wrongly in choices and not in MC schema."""
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(
                [
                    "review-event-create",
                    "--task",
                    "abc",
                    "--reviewer-role",
                    "architect",
                    "--verdict",
                    "partial",
                ]
            )

    def test_review_event_accepts_infra_blocked(self) -> None:
        """Codex F3: 'infra_blocked' was wrongly omitted; valid per MC schema."""
        parser = build_parser()
        args = parser.parse_args(
            [
                "review-event-create",
                "--task",
                "abc",
                "--reviewer-role",
                "devops",
                "--verdict",
                "infra_blocked",
            ]
        )
        assert args.verdict == "infra_blocked"


# --- env-var resolution ---


class TestEnvFallback:
    def test_token_required_when_neither_flag_nor_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("LOCAL_AUTH_TOKEN", raising=False)
        with pytest.raises(SystemExit) as exc:
            main(["task-read", "--task", "abc"])
        assert "LOCAL_AUTH_TOKEN" in str(exc.value)

    def test_board_required_when_neither_flag_nor_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LOCAL_AUTH_TOKEN", "tok")
        monkeypatch.delenv("BOARD_ID", raising=False)
        with pytest.raises(SystemExit) as exc:
            main(["task-read", "--task", "abc"])
        assert "BOARD_ID" in str(exc.value)


# --- HTTP layer (with stubbed urlopen) ---


class _StubResponse:
    def __init__(self, body: bytes, status: int = 200) -> None:
        self._body = body
        self.status = status

    def __enter__(self) -> "_StubResponse":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


class TestHttpFlow:
    def test_task_read_paginates_list_and_filters(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setenv("LOCAL_AUTH_TOKEN", "test-token")
        monkeypatch.setenv("BOARD_ID", "board-uuid")
        captured: dict[str, Any] = {"calls": 0, "urls": []}

        def fake_urlopen(req, timeout=None):
            captured["calls"] = int(captured["calls"]) + 1
            captured["urls"].append(req.full_url)
            captured["auth"] = req.headers.get("Authorization")
            body = {
                "items": [
                    {"id": "other-1", "title": "x"},
                    {"id": "abc", "title": "found-task"},
                    {"id": "other-2", "title": "y"},
                ],
                "total": 3,
            }
            return _StubResponse(json.dumps(body).encode())

        with mock.patch.object(_module.urllib.request, "urlopen", fake_urlopen):
            rc = main(["--base-url", "http://test", "task-read", "--task", "abc"])

        assert rc == 0
        assert captured["auth"] == "Bearer test-token"
        # Single page request because total=3 fit in one page
        assert captured["calls"] == 1
        assert captured["urls"][0].startswith(
            "http://test/api/v1/boards/board-uuid/tasks?limit=200&offset=0"
        )
        out = capsys.readouterr().out
        assert json.loads(out) == {"id": "abc", "title": "found-task"}

    def test_task_read_returns_404_when_not_found(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setenv("LOCAL_AUTH_TOKEN", "tok")
        monkeypatch.setenv("BOARD_ID", "b")

        def fake_urlopen(req, timeout=None):
            return _StubResponse(json.dumps({"items": [], "total": 0}).encode())

        with mock.patch.object(_module.urllib.request, "urlopen", fake_urlopen):
            rc = main(["--base-url", "http://test", "task-read", "--task", "missing"])

        assert rc == 2
        err = capsys.readouterr().err
        assert "HTTP 404" in err
        assert "missing" in err

    def test_comment_create_posts_message_payload(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setenv("LOCAL_AUTH_TOKEN", "tok")
        monkeypatch.setenv("BOARD_ID", "b")
        seen_data: dict[str, Any] = {}

        def fake_urlopen(req, timeout=None):
            seen_data["data"] = req.data
            seen_data["url"] = req.full_url
            seen_data["method"] = req.get_method()
            return _StubResponse(json.dumps({"id": "evt", "task_id": "t"}).encode())

        with mock.patch.object(_module.urllib.request, "urlopen", fake_urlopen):
            rc = main(
                [
                    "--base-url",
                    "http://test",
                    "comment-create",
                    "--task",
                    "t",
                    "--message",
                    "Hello",
                ]
            )

        assert rc == 0
        assert seen_data["method"] == "POST"
        assert seen_data["url"] == "http://test/api/v1/boards/b/tasks/t/comments"
        assert json.loads(seen_data["data"]) == {"message": "Hello"}

    def test_pipeline_event_includes_only_set_fields(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("LOCAL_AUTH_TOKEN", "tok")
        monkeypatch.setenv("BOARD_ID", "b")
        seen: dict[str, Any] = {}

        def fake_urlopen(req, timeout=None):
            seen["payload"] = json.loads(req.data)
            return _StubResponse(b"{}")

        with mock.patch.object(_module.urllib.request, "urlopen", fake_urlopen):
            rc = main(
                [
                    "--base-url",
                    "http://test",
                    "pipeline-event-create",
                    "--task",
                    "t",
                    "--state",
                    "committed",
                    "--commit-sha",
                    "abc1234",
                ]
            )

        assert rc == 0
        payload = seen["payload"]
        assert payload["state"] == "committed"
        assert payload["commit_sha"] == "abc1234"
        assert payload["source"] == "mc_client.py"
        # Optional fields not provided should be absent from payload
        assert "artifact_hash" not in payload
        assert "deploy_target" not in payload
        assert "evidence" not in payload

    def test_pipeline_model_fallback_carries_evidence(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("LOCAL_AUTH_TOKEN", "tok")
        monkeypatch.setenv("BOARD_ID", "b")
        seen: dict[str, Any] = {}

        def fake_urlopen(req, timeout=None):
            seen["payload"] = json.loads(req.data)
            return _StubResponse(b"{}")

        with mock.patch.object(_module.urllib.request, "urlopen", fake_urlopen):
            rc = main(
                [
                    "--base-url",
                    "http://test",
                    "pipeline-event-create",
                    "--task",
                    "t",
                    "--state",
                    "model_fallback",
                    "--evidence",
                    '{"from_model":"a","to_model":"b","reason":"timeout"}',
                ]
            )

        assert rc == 0
        payload = seen["payload"]
        assert payload["state"] == "model_fallback"
        assert payload["evidence"] == {
            "from_model": "a",
            "to_model": "b",
            "reason": "timeout",
        }

    def test_http_error_returns_code_2(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setenv("LOCAL_AUTH_TOKEN", "tok")
        monkeypatch.setenv("BOARD_ID", "b")
        import urllib.error

        def raise_http_error(req, timeout=None):
            err = urllib.error.HTTPError(
                req.full_url,
                409,
                "Conflict",
                cast(Any, {}),
                cast(Any, io.BytesIO(b'{"detail":"already exists"}')),
            )
            raise err

        with mock.patch.object(_module.urllib.request, "urlopen", raise_http_error):
            rc = main(
                [
                    "--base-url",
                    "http://test",
                    "comment-create",
                    "--task",
                    "t",
                    "--message",
                    "x",
                ]
            )

        assert rc == 2
        err = capsys.readouterr().err
        assert "HTTP 409" in err
        assert "already exists" in err


# --- stdin message handling ---


class TestStdinMessage:
    def test_stdin_dash_reads_message_from_stdin(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("LOCAL_AUTH_TOKEN", "tok")
        monkeypatch.setenv("BOARD_ID", "b")
        monkeypatch.setattr("sys.stdin", io.StringIO("body from stdin"))
        seen: dict[str, Any] = {}

        def fake_urlopen(req, timeout=None):
            seen["payload"] = json.loads(req.data)
            return _StubResponse(b"{}")

        with mock.patch.object(_module.urllib.request, "urlopen", fake_urlopen):
            rc = main(
                [
                    "--base-url",
                    "http://test",
                    "comment-create",
                    "--task",
                    "t",
                    "--message",
                    "-",
                ]
            )

        assert rc == 0
        assert seen["payload"]["message"] == "body from stdin"
