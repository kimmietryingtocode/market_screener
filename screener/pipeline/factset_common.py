"""Shared contracts for manual FactSet Summary imports."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass


PIPELINE_VERSION = "4.0.0"


@dataclass(frozen=True)
class ValidationIssue:
    source_file: str
    identifier: str | None
    severity: str
    code: str
    field: str | None
    message: str

    def to_record(self) -> dict:
        return asdict(self)


class FactSetParseError(ValueError):
    """Raised when a Summary report cannot be normalized."""

    def __init__(self, code: str, field: str | None, message: str):
        super().__init__(message)
        self.code = code
        self.field = field


def normalize_identifier(value: str) -> str:
    return value.strip().upper()


def normalize_label(value: str) -> str:
    value = value.strip().lower().rstrip(":")
    value = value.replace("\u2013", "-").replace("\u2014", "-")
    return re.sub(r"\s+", " ", value)


def parse_financial_number(value: str | None) -> float | None:
    """Parse FactSet numbers, percentages, multiples, and parenthesized negatives."""
    if value is None:
        return None
    text = value.strip()
    if not text or text.lower() in {"n/a", "na", "nm", "-"}:
        return None
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1].strip()
    is_percent = text.endswith("%")
    is_multiple = text.lower().endswith("x")
    if is_percent or is_multiple:
        text = text[:-1].strip()
    try:
        number = float(text.replace(",", "").replace("$", "").strip())
    except ValueError as exc:
        raise FactSetParseError(
            "INVALID_NUMBER", None, f"could not parse financial value {value!r}"
        ) from exc
    if negative:
        number = -number
    return number / 100 if is_percent else number
