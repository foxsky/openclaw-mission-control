"""Direct-contract tests for the shared env-file parser.

The parser is consumed by:
  - ``app.services.mc_gateway_subscriber.__main__`` (worker config)
  - (logically by ``backend/scripts/mc_hooks.py``, but that script
    inlines the same logic because it deploys standalone outside the
    MC backend package)

Per ``feedback_tdd_discipline``: each consumer's existing tests
already cover the parser transitively, but pinning the contract here
is cheap insurance against drift if a future caller is added.
"""

from __future__ import annotations

from pathlib import Path

from app.core.env_file import load_env_file, parse_env_value


class TestParseEnvValue:
    def test_unquoted_strips_inline_comment(self) -> None:
        assert parse_env_value("abc # prod") == "abc"

    def test_unquoted_strips_surrounding_whitespace(self) -> None:
        assert parse_env_value("   abc   ") == "abc"

    def test_double_quoted_preserves_inline_hash(self) -> None:
        assert parse_env_value('"abc#1"') == "abc#1"

    def test_single_quoted_preserves_inline_hash(self) -> None:
        assert parse_env_value("'abc#1'") == "abc#1"

    def test_double_quoted_unterminated_returns_remainder(self) -> None:
        # No closing quote: take everything after the opener. Operator
        # gets a value that's obviously wrong, not a silent empty string.
        assert parse_env_value('"abc') == "abc"

    def test_empty_yields_empty(self) -> None:
        assert parse_env_value("") == ""


class TestLoadEnvFile:
    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        assert load_env_file(str(tmp_path / "no-such")) == {}

    def test_skips_comments_and_blanks(self, tmp_path: Path) -> None:
        env = tmp_path / "env"
        env.write_text("# header\n\nKEY=value\n# trailing\n", encoding="utf-8")
        assert load_env_file(str(env)) == {"KEY": "value"}

    def test_handles_crlf(self, tmp_path: Path) -> None:
        env = tmp_path / "env"
        env.write_bytes(b"KEY=value\r\n")
        result = load_env_file(str(env))
        assert result == {"KEY": "value"}
        assert "\r" not in result["KEY"]

    def test_quoted_value_keeps_hash(self, tmp_path: Path) -> None:
        env = tmp_path / "env"
        env.write_text('TOKEN="abc#1"\n', encoding="utf-8")
        assert load_env_file(str(env))["TOKEN"] == "abc#1"

    def test_unquoted_value_drops_inline_comment(self, tmp_path: Path) -> None:
        env = tmp_path / "env"
        env.write_text("TOKEN=abc # prod\n", encoding="utf-8")
        assert load_env_file(str(env))["TOKEN"] == "abc"

    def test_blank_key_is_skipped(self, tmp_path: Path) -> None:
        env = tmp_path / "env"
        env.write_text("=orphan-value\nKEY=ok\n", encoding="utf-8")
        assert load_env_file(str(env)) == {"KEY": "ok"}

    def test_no_equals_is_skipped(self, tmp_path: Path) -> None:
        env = tmp_path / "env"
        env.write_text("just a line\nKEY=ok\n", encoding="utf-8")
        assert load_env_file(str(env)) == {"KEY": "ok"}
