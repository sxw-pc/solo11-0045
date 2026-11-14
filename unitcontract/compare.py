"""Side-by-side comparison of two overlay chains on one baseline and dataset.

Both sides run the unchanged read → validate → convert → audit pipeline; this
module only diffs the resulting contracts and conclusions. Output is a
deterministic JSON summary plus a Markdown rendering, written to a directory.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from unitcontract.contract.schema import TRACKED_FIELD_KEYS, Contract
from unitcontract.pipeline import RunResult, execute
from unitcontract.report.json_report import dump_json
from unitcontract.units.registry import UnitRegistry

COMPARE_SCHEMA_VERSION = "1"
ABSENT = "<absent>"


@dataclass
class CompareResult:
    """Both pipeline runs plus the diff summary derived from them."""

    left: RunResult
    right: RunResult
    summary: dict[str, Any]


def _side_dict(result: RunResult) -> dict[str, Any]:
    hashes = dict(result.overlay_hashes)
    return {
        "run_id": result.run_id,
        "contract_name": result.contract.name,
        "overlays": [
            {"name": label, "sha256": hashes.get(label)}
            for label in result.contract.overlays
        ],
    }


def diff_contract_fields(left: Contract, right: Contract) -> list[dict[str, Any]]:
    """Field-level spec differences between the two effective contracts."""
    diffs: list[dict[str, Any]] = []
    left_map, right_map = left.field_map, right.field_map
    ordered = list(left.field_names()) + [
        name for name in right.field_names() if name not in left_map
    ]
    for name in ordered:
        left_field = left_map.get(name)
        right_field = right_map.get(name)
        if left_field is None or right_field is None:
            diffs.append(
                {
                    "field": name,
                    "key": "field",
                    "left": "present" if left_field else ABSENT,
                    "right": "present" if right_field else ABSENT,
                    "left_source": left_field.source_for("field") if left_field else None,
                    "right_source": right_field.source_for("field") if right_field else None,
                }
            )
            continue
        left_dict = left_field.as_dict()
        right_dict = right_field.as_dict()
        for key in TRACKED_FIELD_KEYS:
            if left_dict[key] != right_dict[key]:
                diffs.append(
                    {
                        "field": name,
                        "key": key,
                        "left": left_dict[key],
                        "right": right_dict[key],
                        "left_source": left_field.source_for(key),
                        "right_source": right_field.source_for(key),
                    }
                )
    return diffs


def _row_statuses(result: RunResult) -> dict[int, str]:
    violated = {
        finding.row_number for finding in result.validation.error_findings
    }
    return {
        row.row_number: ("violated" if row.row_number in violated else "passed")
        for row in result.rows
    }


def _row_codes(result: RunResult) -> dict[int, tuple[str, ...]]:
    codes: dict[int, list[str]] = {}
    for finding in result.validation.findings:
        codes.setdefault(finding.row_number, []).append(finding.code)
    return {
        row.row_number: tuple(sorted(codes.get(row.row_number, ())))
        for row in result.rows
    }


def diff_validation(
    left: RunResult, right: RunResult, max_details: int = 50
) -> dict[str, Any]:
    """Row-level conclusion differences between the two validation runs."""
    left_status = _row_statuses(left)
    right_status = _row_statuses(right)
    left_codes = _row_codes(left)
    right_codes = _row_codes(right)
    row_numbers = [row.row_number for row in left.rows]
    status_mismatch = [
        number for number in row_numbers if left_status[number] != right_status[number]
    ]
    findings_mismatch = [
        number for number in row_numbers if left_codes[number] != right_codes[number]
    ]
    left_counts = left.validation.counts_by_code()
    right_counts = right.validation.counts_by_code()
    code_delta = {
        code: {
            "left": left_counts.get(code, 0),
            "right": right_counts.get(code, 0),
            "delta": right_counts.get(code, 0) - left_counts.get(code, 0),
        }
        for code in sorted(set(left_counts) | set(right_counts))
        if left_counts.get(code, 0) != right_counts.get(code, 0)
    }
    return {
        "rows_total": len(row_numbers),
        "rows_with_different_status": len(status_mismatch),
        "rows_with_different_findings": len(findings_mismatch),
        "status_mismatch_rows": status_mismatch[:max_details],
        "findings_mismatch_rows": findings_mismatch[:max_details],
        "left": {
            "passed_rows": left.validation.passed_rows,
            "violating_rows": left.validation.violating_rows,
            "warning_rows": left.validation.warning_rows,
            "error_findings": len(left.validation.error_findings),
            "warning_findings": len(left.validation.warning_findings),
            "code_counts": left_counts,
        },
        "right": {
            "passed_rows": right.validation.passed_rows,
            "violating_rows": right.validation.violating_rows,
            "warning_rows": right.validation.warning_rows,
            "error_findings": len(right.validation.error_findings),
            "warning_findings": len(right.validation.warning_findings),
            "code_counts": right_counts,
        },
        "code_count_delta": code_delta,
    }


def _canonical_values(result: RunResult) -> dict[tuple[int, str], str]:
    return {
        (record.row_number, record.field): record.canonical_value
        for record in result.conversion.records
    }


def diff_conversion(
    left: RunResult, right: RunResult, max_details: int = 50
) -> dict[str, Any]:
    """Differences in normalized output between the two conversion runs."""
    left_values = _canonical_values(left)
    right_values = _canonical_values(right)
    mismatches: list[dict[str, Any]] = []
    for key in sorted(set(left_values) | set(right_values)):
        left_value = left_values.get(key)
        right_value = right_values.get(key)
        if left_value != right_value:
            mismatches.append(
                {
                    "row": key[0],
                    "field": key[1],
                    "left": left_value if left_value is not None else ABSENT,
                    "right": right_value if right_value is not None else ABSENT,
                }
            )
    return {
        "left": {
            "records": len(left.conversion.records),
            "skipped": left.conversion.skipped,
        },
        "right": {
            "records": len(right.conversion.records),
            "skipped": right.conversion.skipped,
        },
        "value_mismatches": len(mismatches),
        "mismatched_pairs": mismatches[:max_details],
    }


def run_compare(
    contract_path: str | Path,
    data_path: str | Path,
    left_overlays: Iterable[str | Path] = (),
    right_overlays: Iterable[str | Path] = (),
    data_format: str = "auto",
    max_details: int = 50,
    registry: UnitRegistry | None = None,
) -> CompareResult:
    """Run both sides through the standard pipeline and diff the outcomes."""
    registry = registry or UnitRegistry.bundled()
    left = execute(
        contract_path,
        data_path,
        data_format=data_format,
        no_timestamp=True,
        registry=registry,
        overlay_paths=tuple(left_overlays),
    )
    right = execute(
        contract_path,
        data_path,
        data_format=data_format,
        no_timestamp=True,
        registry=registry,
        overlay_paths=tuple(right_overlays),
    )
    field_diffs = diff_contract_fields(left.contract, right.contract)
    summary: dict[str, Any] = {
        "schema_version": COMPARE_SCHEMA_VERSION,
        "baseline": {
            "contract_path": left.contract_path,
            "contract_hash": left.contract_hash,
        },
        "data": {
            "data_path": left.data_path,
            "data_hash": left.data_hash,
            "source_name": left.source_name,
            "data_format": left.data_format,
            "rows_total": left.validation.rows_total,
        },
        "left": _side_dict(left),
        "right": _side_dict(right),
        "field_diffs": field_diffs,
        "field_diff_count": len(field_diffs),
        "validation_diff": diff_validation(left, right, max_details=max_details),
        "conversion_diff": diff_conversion(left, right, max_details=max_details),
    }
    return CompareResult(left=left, right=right, summary=summary)


def _md_table(headers: list[str], rows: list[list[object]]) -> list[str]:
    def cell(value: object) -> str:
        text = "" if value is None else str(value)
        return text.replace("|", "\\|").replace("\n", " ")

    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(cell(item) for item in row) + " |")
    return lines


def render_compare_markdown(summary: dict[str, Any]) -> str:
    """Human-readable rendering of the compare summary."""
    lines: list[str] = [
        "# 契约 overlay 对比报告",
        "",
        "## 对比输入",
        "",
        f"- 基线契约: `{summary['baseline']['contract_path']}`",
        f"- 契约 SHA-256: `{summary['baseline']['contract_hash']}`",
        f"- 数据文件: `{summary['data']['source_name']}`",
        f"- 数据 SHA-256: `{summary['data']['data_hash']}`",
        f"- 总行数: {summary['data']['rows_total']}",
        "",
        "## 两侧 overlay 链",
        "",
    ]
    for side in ("left", "right"):
        overlays = summary[side]["overlays"]
        chain = " → ".join(f"`{item['name']}`" for item in overlays) or "（无 overlay）"
        lines.append(f"- {side}: {chain}（契约名 `{summary[side]['contract_name']}`）")
    lines.extend(["", "## 字段级口径差异", ""])
    field_diffs = summary["field_diffs"]
    if field_diffs:
        lines.extend(
            _md_table(
                ["字段", "键", "left", "right", "left 来源", "right 来源"],
                [
                    [
                        item["field"],
                        item["key"],
                        item["left"],
                        item["right"],
                        item.get("left_source") or "",
                        item.get("right_source") or "",
                    ]
                    for item in field_diffs
                ],
            )
        )
    else:
        lines.append("两侧契约字段口径完全一致。")
    validation = summary["validation_diff"]
    lines.extend(
        [
            "",
            "## validate 结论差异",
            "",
        ]
    )
    lines.extend(
        _md_table(
            ["指标", "left", "right"],
            [
                ["通过行数", validation["left"]["passed_rows"], validation["right"]["passed_rows"]],
                ["违规行数", validation["left"]["violating_rows"], validation["right"]["violating_rows"]],
                ["含警告行数", validation["left"]["warning_rows"], validation["right"]["warning_rows"]],
                ["error findings", validation["left"]["error_findings"], validation["right"]["error_findings"]],
                ["warning findings", validation["left"]["warning_findings"], validation["right"]["warning_findings"]],
            ],
        )
    )
    lines.extend(
        [
            "",
            f"- 行级通过/违规结论不一致行数: {validation['rows_with_different_status']}",
            f"- 行级错误码集合不一致行数: {validation['rows_with_different_findings']}",
        ]
    )
    if validation["status_mismatch_rows"]:
        rows_text = ", ".join(str(number) for number in validation["status_mismatch_rows"])
        lines.append(f"- 结论不一致的行（前 {len(validation['status_mismatch_rows'])} 条）: {rows_text}")
    if validation["code_count_delta"]:
        lines.extend(["", "### 错误码计数差异", ""])
        lines.extend(
            _md_table(
                ["错误码", "left", "right", "差值"],
                [
                    [code, item["left"], item["right"], item["delta"]]
                    for code, item in validation["code_count_delta"].items()
                ],
            )
        )
    conversion = summary["conversion_diff"]
    lines.extend(
        [
            "",
            "## convert 结论差异",
            "",
        ]
    )
    lines.extend(
        _md_table(
            ["指标", "left", "right"],
            [
                ["规范化记录数", conversion["left"]["records"], conversion["right"]["records"]],
                ["跳过换算数", conversion["left"]["skipped"], conversion["right"]["skipped"]],
            ],
        )
    )
    lines.extend(["", f"- 规范值不一致的 (行, 字段) 对数: {conversion['value_mismatches']}"])
    if conversion["mismatched_pairs"]:
        lines.extend(["", "### 规范值差异明细", ""])
        lines.extend(
            _md_table(
                ["行", "字段", "left", "right"],
                [
                    [item["row"], item["field"], item["left"], item["right"]]
                    for item in conversion["mismatched_pairs"]
                ],
            )
        )
    lines.append("")
    return "\n".join(lines)


def write_compare_reports(summary: dict[str, Any], out_dir: str | Path) -> dict[str, Path]:
    """Persist compare.json and compare.md into ``out_dir``."""
    directory = Path(out_dir)
    directory.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}
    json_path = directory / "compare.json"
    json_path.write_text(dump_json(summary), encoding="utf-8", newline="\n")
    written["json"] = json_path
    md_path = directory / "compare.md"
    md_path.write_text(render_compare_markdown(summary), encoding="utf-8", newline="\n")
    written["md"] = md_path
    return written
