"""Unit tests for scripts/contract_lib.py.

These tests exercise the five helper functions extracted from the contract
test suite. They are intentionally self-contained — no network access — and
use only the standard library plus pytest.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.contract_lib import (
    assert_contains,
    assert_not_contains,
    assert_order,
    read,
    step_block,
)


class TestRead:
    def test_reads_file_content(self, tmp_path: Path) -> None:
        f = tmp_path / "test.txt"
        f.write_text("hello world", encoding="utf-8")
        assert read(f) == "hello world"

    def test_reads_empty_file(self, tmp_path: Path) -> None:
        f = tmp_path / "empty.txt"
        f.write_text("", encoding="utf-8")
        assert read(f) == ""

    def test_reads_unicode(self, tmp_path: Path) -> None:
        f = tmp_path / "unicode.txt"
        content = "café 你好"
        f.write_text(content, encoding="utf-8")
        assert read(f) == content

    def test_reads_multiline(self, tmp_path: Path) -> None:
        f = tmp_path / "multi.txt"
        content = "line1\nline2\nline3\n"
        f.write_text(content, encoding="utf-8")
        assert read(f) == content


class TestAssertContains:
    def test_passes_when_found(self) -> None:
        assert_contains("hello world", "world", "should be found")

    def test_passes_when_needle_is_entire_text(self) -> None:
        assert_contains("exact", "exact", "identical text")

    def test_raises_when_not_found(self) -> None:
        with pytest.raises(AssertionError, match="missing text"):
            assert_contains("hello world", "xyz", "missing text")

    def test_raises_when_needle_is_empty(self) -> None:
        # An empty string is always "in" any string — this is Python behaviour.
        # The helper should not crash but should pass silently.
        assert_contains("anything", "", "empty needle passes trivially")

    def test_can_find_multiline_needle(self) -> None:
        text = "line one\nline two\nline three"
        assert_contains(text, "line two", "multiline needle")


class TestAssertNotContains:
    def test_passes_when_absent(self) -> None:
        assert_not_contains("hello world", "xyz", "should be absent")

    def test_raises_when_present(self) -> None:
        with pytest.raises(AssertionError, match="forbidden text"):
            assert_not_contains("hello world", "world", "forbidden text")

    def test_passes_when_text_is_empty(self) -> None:
        assert_not_contains("", "anything", "empty text has nothing")


class TestAssertOrder:
    SAMPLE = "the quick brown fox jumps"

    def test_passes_when_in_order(self) -> None:
        assert_order(self.SAMPLE, "the", "brown", "jumps")

    def test_passes_with_single_needle(self) -> None:
        assert_order(self.SAMPLE, "quick")

    def test_raises_when_out_of_order(self) -> None:
        with pytest.raises(AssertionError, match="expected ordering not found"):
            assert_order(self.SAMPLE, "jumps", "fox", "the")

    def test_raises_when_missing(self) -> None:
        with pytest.raises(AssertionError, match="missing expected text"):
            assert_order(self.SAMPLE, "the", "nonexistent")

    def test_passes_with_adjacent_needles(self) -> None:
        assert_order("a b c d", "a", "b")


class TestStepBlock:
    YAML = (
        "      - name: First step\n"
        "        run: echo first\n"
        "      - name: Second step\n"
        "        run: echo second\n"
        "        env:\n"
        "          X: 1\n"
        "      - name: Third step\n"
        "        run: echo third\n"
    )

    def test_extracts_middle_block(self) -> None:
        block = step_block(self.YAML, "Second step")
        assert "echo second" in block
        assert "echo first" not in block
        assert "echo third" not in block
        assert "Second step" in block

    def test_extracts_last_block(self) -> None:
        block = step_block(self.YAML, "Third step")
        assert "echo third" in block
        assert "Second step" not in block

    def test_extracts_first_block(self) -> None:
        block = step_block(self.YAML, "First step")
        assert "echo first" in block
        assert "echo second" not in block

    def test_raises_when_missing(self) -> None:
        with pytest.raises(AssertionError, match="missing step"):
            step_block(self.YAML, "Nonexistent step")

    def test_single_step_returns_remainder(self) -> None:
        single = "      - name: Only step\n        run: echo solo\n"
        block = step_block(single, "Only step")
        assert "echo solo" in block

    def test_extracted_block_starts_with_step_name(self) -> None:
        block = step_block(self.YAML, "Second step")
        assert block.startswith("      - name: Second step\n")
