"""Small, dependency-free CSV and JSONL readers."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


class DataReadError(ValueError):
    def __init__(self, message: str, line_number: int | None = None) -> None:
        self.line_number = line_number
        prefix = f"line {line_number}: " if line_number is not None else ""
        super().__init__(prefix + message)


@dataclass(frozen=True)
class DataRow:
    row_number: int
    values: dict[str, Any]

    def cell(self, field: str) -> "FieldCell":
        """Resolve flat columns or the nested ``{value, unit}`` convention."""
        if field not in self.values:
            return FieldCell(None, None, False, False)
        raw = self.values[field]
        if isinstance(raw, dict):
            value = raw.get("value")
            unit = raw.get("unit")
            return FieldCell(
                value,
                unit,
                _has_value(value),
                unit is not None and str(unit).strip() != "",
            )
        unit = self.values.get(f"{field}__unit")
        return FieldCell(
            raw,
            unit,
            _has_value(raw),
            unit is not None and str(unit).strip() != "",
        )


@dataclass(frozen=True)
class FieldCell:
    raw_value: Any
    raw_unit: Any
    has_value: bool
    has_unit: bool

    @property
    def value_text(self) -> str:
        return "" if self.raw_value is None else str(self.raw_value).strip()

    @property
    def unit_text(self) -> str:
        return "" if self.raw_unit is None else str(self.raw_unit).strip()


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() != ""
    return True


def _blank_scalar(value: Any) -> bool:
    """True for ``None`` and whitespace-only text, False for real values."""
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    return False


def _blank_cell(value: Any) -> bool:
    """True when a raw cell carries neither a value nor a unit."""
    if isinstance(value, dict):
        return all(_blank_scalar(item) for item in value.values())
    return _blank_scalar(value)


def row_is_blank(values: dict[str, Any]) -> bool:
    """A record is blank when every raw cell is empty.

    The rule is deliberately format-independent: a CSV row of empty columns
    and a JSON object with only null/empty cells describe the same absence of
    data. Both readers drop such records before numbering, so blank rows never
    inflate ``rows_total``, ``skipped`` or ``normalized`` in one format only.
    """
    return all(_blank_cell(value) for value in values.values())


def detect_format(path: str | Path) -> str:
    suffix = Path(path).suffix.lower()
    if suffix == ".csv":
        return "csv"
    if suffix in {".jsonl", ".ndjson"}:
        return "jsonl"
    with Path(path).open("rb") as handle:
        first = handle.read(1)
    if first == b"{":
        return "jsonl"
    return "csv"


def _read_csv(path: Path) -> list[DataRow]:
    rows: list[DataRow] = []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames:
                raise DataReadError("CSV file has no header")
            headers = [header.strip() for header in reader.fieldnames]
            if len(set(headers)) != len(headers):
                raise DataReadError("CSV header contains duplicate column names")
            for raw_row in reader:
                values = {
                    str(key).strip(): value
                    for key, value in raw_row.items()
                    if key is not None
                }
                if row_is_blank(values):
                    continue
                rows.append(DataRow(len(rows) + 1, values))
    except OSError as exc:
        raise DataReadError(f"cannot read data file: {exc}") from exc
    except csv.Error as exc:
        raise DataReadError(f"invalid CSV: {exc}", reader.line_num) from exc
    return rows


def _read_jsonl(path: Path) -> list[DataRow]:
    rows: list[DataRow] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for physical_line, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise DataReadError(f"invalid JSON: {exc.msg}", physical_line) from exc
                if not isinstance(payload, dict):
                    raise DataReadError("each JSONL row must be an object", physical_line)
                if row_is_blank(payload):
                    continue
                rows.append(DataRow(len(rows) + 1, payload))
    except OSError as exc:
        raise DataReadError(f"cannot read data file: {exc}") from exc
    return rows


def read_rows(
    path: str | Path,
    data_format: str = "auto",
    limit: int | None = None,
) -> list[DataRow]:
    source = Path(path)
    fmt = detect_format(source) if data_format == "auto" else data_format.lower()
    if fmt == "csv":
        rows = _read_csv(source)
    elif fmt in {"jsonl", "ndjson"}:
        rows = _read_jsonl(source)
    else:
        raise DataReadError(f"unsupported data format: {data_format}")
    if limit is not None:
        if limit < 0:
            raise DataReadError("limit must be non-negative")
        rows = rows[:limit]
    return rows


def iter_rows(
    path: str | Path,
    data_format: str = "auto",
) -> Iterable[DataRow]:
    yield from read_rows(path, data_format=data_format)
