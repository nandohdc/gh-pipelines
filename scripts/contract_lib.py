"""Shared helpers for the gh-pipelines static contract-test suite.

Extracted from the three contract test scripts so the helpers themselves are
independently testable and can produce SonarQube coverage data.
"""

from __future__ import annotations

from pathlib import Path


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def assert_contains(text: str, needle: str, message: str) -> None:
    assert needle in text, message


def assert_not_contains(text: str, needle: str, message: str) -> None:
    assert needle not in text, message


def assert_order(text: str, *needles: str) -> None:
    positions: list[int] = []
    for needle in needles:
        position = text.find(needle)
        assert position != -1, f"missing expected text: {needle!r}"
        positions.append(position)
    assert positions == sorted(positions), "expected ordering not found: " + " -> ".join(needles)


def step_block(text: str, step_name: str) -> str:
    marker = f"      - name: {step_name}\n"
    start = text.find(marker)
    assert start != -1, f"missing step {step_name!r}"
    next_start = text.find("\n      - name: ", start + len(marker))
    if next_start == -1:
        return text[start:]
    return text[start:next_start]
