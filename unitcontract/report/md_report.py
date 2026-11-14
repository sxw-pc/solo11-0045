"""Dependency-free Markdown report rendering."""

from __future__ import annotations

from unitcontract.contract.schema import source_label
from unitcontract.engine.auditor import catalog_for
from unitcontract.pipeline import RunResult

GOLDEN_NOTE = (
    "Golden 输出通过 `--no-timestamp` 生成；相同契约、相同输入与相同参数"
    "应得到逐字节一致的报告。时间戳是唯一允许变化的内容。"
)


def _cell(value: object) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", " ")


def _table(headers: list[str], rows: list[list[object]]) -> list[str]:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(_cell(item) for item in row) + " |")
    return lines


def _range_text(field_range: dict[str, object] | None) -> str:
    if not field_range:
        return "-"
    return f"[{field_range.get('min') or '-inf'}, {field_range.get('max') or '+inf'}]"


def _field_sources_lines(result: RunResult) -> list[str]:
    """Provenance extension section, rendered only when overlays were applied."""
    contract = result.contract
    if not contract.overlays:
        return []
    lines = [
        "",
        "## 字段来源（overlay 链）",
        "",
        "- 基线契约: `" + source_label(contract.source_name) + "`",
        "- 叠加顺序: " + " → ".join(f"`{label}`" for label in contract.overlays),
        "",
    ]
    rows: list[list[object]] = []
    for field in contract.fields:
        for key, source in field.key_sources:
            rows.append([field.field, key, source])
    lines.extend(_table(["字段", "键", "生效来源"], rows))
    return lines


def render_markdown(result: RunResult, max_details: int = 50) -> str:
    metadata = result.run_metadata()
    summary = result.audit.as_dict()
    lines: list[str] = [
        "# 量纲契约台运行报告",
        "",
        "## 运行参数",
        "",
        f"- Run ID: `{metadata['run_id']}`",
        f"- 契约名称: `{metadata['contract_name']}`",
        f"- 契约 SHA-256: `{metadata['contract_hash']}`",
        f"- 数据 SHA-256: `{metadata['data_hash']}`",
        f"- 数据文件: `{metadata['source_name']}`",
        f"- 数据格式: `{metadata['data_format']}`",
        f"- 时间戳: {metadata.get('generated_at', '未启用（--no-timestamp）')}",
        f"- 总行数: {metadata['rows_total']}",
        "",
        "## 契约摘要",
        "",
    ]
    contract_rows = [
        [
            field.field,
            field.dimension,
            field.canonical_unit,
            ", ".join(field.allowed_units),
            field.precision,
            field.rounding,
            _range_text(field.as_dict()["range"]),
        ]
        for field in result.contract.fields
    ]
    lines.extend(
        _table(
            ["字段", "量纲", "规范单位", "允许单位", "精度", "舍入", "范围"],
            contract_rows,
        )
    )
    lines.extend(_field_sources_lines(result))
    lines.extend(
        [
            "",
            "## 统计",
            "",
        ]
    )
    lines.extend(
        _table(
            ["指标", "值"],
            [
                ["总行数", summary["rows_total"]],
                ["通过行数（无 error）", summary["passed_rows"]],
                ["违规行数", summary["violation_rows"]],
                ["含警告行数", summary["warning_rows"]],
                ["error findings", summary["error_findings"]],
                ["warning findings", summary["warning_findings"]],
                ["规范化记录数", len(result.conversion.records)],
                ["跳过换算数", result.conversion.skipped],
            ],
        )
    )
    lines.extend(["", "## 错误码分布", ""])
    if summary["code_counts"]:
        code_rows = []
        for code, count in summary["code_counts"].items():
            entry = catalog_for(code)
            code_rows.append([code, entry["category"], count, entry["cause"], entry["suggestion"]])
        lines.extend(_table(["错误码", "类别", "数量", "原因", "建议"], code_rows))
    else:
        lines.append("未发现违规或警告。")

    lines.extend(["", "## 违规明细", ""])
    findings = result.validation.findings[:max_details]
    if findings:
        finding_rows = [
            [
                item.row_number,
                item.field,
                item.code,
                item.severity,
                item.raw_value or "",
                item.unit or "",
                item.message,
            ]
            for item in findings
        ]
        lines.extend(
            _table(["行", "字段", "错误码", "级别", "原始值", "单位", "说明"], finding_rows)
        )
        if len(result.validation.findings) > max_details:
            lines.append(
                f"\n仅显示前 {max_details} 条，共 {len(result.validation.findings)} 条；"
                "完整明细见 JSON 报告与 SQLite 台账。"
            )
    else:
        lines.append("无违规明细。")

    lines.extend(["", "## 规范化值", ""])
    normalized = result.conversion.records[:max_details]
    if normalized:
        normalized_rows = [
            [
                item.row_number,
                item.field,
                item.raw_value,
                item.source_unit,
                item.canonical_value,
                item.canonical_unit,
            ]
            for item in normalized
        ]
        lines.extend(_table(["行", "字段", "原始值", "原始单位", "换算值", "规范单位"], normalized_rows))
        if len(result.conversion.records) > max_details:
            lines.append(
                f"\n仅显示前 {max_details} 条，共 {len(result.conversion.records)} 条规范化记录。"
            )
    else:
        lines.append("无可换算记录。")

    lines.extend(
        [
            "",
            "## Golden 说明",
            "",
            GOLDEN_NOTE,
            "",
        ]
    )
    return "\n".join(lines)
