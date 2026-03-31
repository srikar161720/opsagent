"""Unit tests for LogParser (Drain3 wrapper)."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.preprocessing.log_parser import LogParser


@pytest.fixture()
def parser(tmp_path: Path) -> LogParser:
    """Fresh LogParser with temp persistence directory."""
    return LogParser(persistence_path=str(tmp_path / "drain3"))


class TestLogParserParse:
    def test_parse_returns_tuple(self, parser: LogParser) -> None:
        tid, template = parser.parse("Service started on port 8080")
        assert isinstance(tid, int)
        assert isinstance(template, str)

    def test_first_template_id_is_zero(self, parser: LogParser) -> None:
        tid, _ = parser.parse("Service started on port 8080")
        assert tid == 0

    def test_template_ids_are_monotonic(self, parser: LogParser) -> None:
        tid1, _ = parser.parse("Service started on port 8080")
        tid2, _ = parser.parse("Connection timeout after 30 seconds")
        # tid2 should be >= tid1 (could be same if Drain3 merges, but typically different)
        assert tid2 >= tid1

    def test_similar_messages_share_template(self, parser: LogParser) -> None:
        parser.parse("Failed to connect after 3 retries")
        tid2, t2 = parser.parse("Failed to connect after 5 retries")
        # After seeing two similar messages, Drain3 should generalize
        assert "<*>" in t2

    def test_num_templates_grows(self, parser: LogParser) -> None:
        assert parser.num_templates == 0
        parser.parse("Message type A")
        assert parser.num_templates >= 1
        parser.parse("Completely different message B")
        assert parser.num_templates >= 2


class TestLogParserMatch:
    def test_match_returns_known_template(self, parser: LogParser) -> None:
        parser.parse("Failed to connect after 3 retries")
        parser.parse("Failed to connect after 5 retries")
        tid, template = parser.match("Failed to connect after 10 retries")
        assert tid >= 0
        assert "UNKNOWN" not in template

    def test_match_returns_unknown_for_new_message(self, parser: LogParser) -> None:
        parser.parse("Known message pattern here")
        tid, template = parser.match("Completely new unseen message that was never parsed before")
        assert tid == -1
        assert template == "UNKNOWN"

    def test_match_does_not_create_templates(self, parser: LogParser) -> None:
        parser.parse("Initial message for training")
        count_before = parser.num_templates
        parser.match("Something entirely new and different that nobody has seen")
        assert parser.num_templates == count_before


class TestLogParserGetTemplate:
    def test_get_template_reverse_lookup(self, parser: LogParser) -> None:
        tid, template = parser.parse("Service started on port 8080")
        assert parser.get_template(tid) == template

    def test_get_template_unknown_id(self, parser: LogParser) -> None:
        assert parser.get_template(999) == "UNKNOWN"


class TestLogParserPersistence:
    def test_save_creates_file(self, tmp_path: Path) -> None:
        persist_dir = str(tmp_path / "drain3_save_test")
        parser = LogParser(persistence_path=persist_dir)
        parser.parse("Test message for persistence")
        parser.save()
        assert (Path(persist_dir) / "drain3_state.bin").exists()


class TestLogParserWithHDFS:
    def test_hdfs_log_lines(self, parser: LogParser, sample_hdfs_log_lines: list[str]) -> None:
        """Parse sample HDFS log lines and verify templates are created."""
        for line in sample_hdfs_log_lines:
            tid, template = parser.parse(line)
            assert tid >= 0
            assert len(template) > 0
        assert parser.num_templates > 0
