"""Completeness rule for the Life Cycle Assessment tabs."""

from __future__ import annotations

from typing import Any, Mapping


# Installation Date is the only field that controls Complete / Incomplete.
COMPLETENESS_COLUMNS = ("Installation Date",)


def is_complete(row: Mapping[str, Any]) -> bool:
    """Return whether a Life Cycle row belongs in the Complete tab."""
    return all(row.get(column) not in (None, "") for column in COMPLETENESS_COLUMNS)
