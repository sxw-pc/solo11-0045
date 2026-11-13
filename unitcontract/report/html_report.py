"""Self-contained HTML report: inline local CSS, no scripts, no remote assets."""

from __future__ import annotations

from html import escape
from typing import Iterable

from unitcontract.engine.auditor import catalog_for
from unitcontract.pipeline import RunResult

REPORT_CSS = """
:root{--ink:#17211b;--muted:#5d6b63;--paper:#f6f7f2;--card:#fff;--line:#d9ded6;
--accent:#1d6b4f;--warn:#8a5a00;--bad:#a23a2a;--soft:#edf2ec}
*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);
font:15px/1.55 "Segoe UI",system-ui,sans-serif}
main{max-width:1180px;margin:0 auto;padding:32px 22px 56px}
header{border-bottom:3px solid var(--accent);padding-bottom:16px;margin-bottom:22px}
h1{font-size:28px;margin:0 0 6px}h2{font-size:20px;margin:30px 0 10px}
p.sub{color:var(--muted);margin:0}.meta{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));
gap:8px 24px;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px}
.meta div{overflow-wrap:anywhere}.label{color:var(--muted);font-size:12px;text-transform:uppercase;
letter-spacing:.04em}.mono{font-family:Consolas,monospace;font-size:13px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(145px,1fr));gap:12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.card strong{display:block;font-size:25px;line-height:1.2}.card span{color:var(--muted);font-size:13px}
.table-wrap{overflow-x:auto;background:var(--card);border:1px solid var(--line);border-radius:10px}
table{border-collapse:collapse;width:100%;font-size:13px}th,td{padding:8px 10px;
border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
th{background:var(--soft);font-weight:600;white-space:nowrap}tr:last-child td{border-bottom:0}
.error{color:var(--bad);font-weight:600}.warning{color:var(--warn);font-weight:600}
.note{color:var(--muted);font-size:13px;border-left:3px solid var(--accent);padding-left:12px}
footer{margin-top:34px;border-top:1px solid var(--line);padding-top:12px;color:var(--muted);font-size:12px}
@media (max-width:640px){main{padding:20px 12px}h1{font-size:23px}}
"""


def _e(value: object) -> str:
    return escape("" if value is None else str(value))


def _table(headers: list[str], rows: Iterable[Iterable[object]], css: str = "") -> str:
    head = "".join(f"<th>{_e(item)}</th>" for item in headers)
    body_rows: list[str] = []
    for row in rows:
        cells = "".join(f"<td>{_e(item)}</td>" for item in row)
        body_rows.append(f"<tr>{cells}</tr>")
    body = "".join(body_rows) if body_rows else (
        f'<tr><td colspan="{len(headers)}">无记录</td></tr>'
    )
    return (
        f'<div class="table-wrap"><table class="{css}"><thead><tr>{head}</tr></thead>'
        f"<tbody>{body}</tbody></table></div>"
    )


def _range_text(field_range: dict[str, object] | None) -> str:
    if not field_range:
        return "-"
    return f"[{field_range.get('min') or '-inf'}, {field_range.get('max') or '+inf'}]"


def render_html(result: RunResult, max_details: int = 50) -> str:
    meta = result.run_metadata()
    summary = result.audit.as_dict()
    meta_rows = [
        ("Run ID", meta["run_id"]),
        ("契约名称", meta["contract_name"]),
        ("契约 SHA-256", meta["contract_hash"]),
        ("数据 SHA-256", meta["data_hash"]),
        ("数据文件", meta["source_name"]),
        ("数据格式", meta["data_format"]),
        ("时间戳", meta.get("generated_at", "未启用（--no-timestamp）")),
    ]
    meta_html = "".join(
        f'<div><div class="label">{_e(label)}</div><div class="mono">{_e(value)}</div></div>'
        for label, value in meta_rows
    )
    cards = [
        ("总行数", summary["rows_total"]),
        ("通过行数", summary["passed_rows"]),
        ("违规行数", summary["violation_rows"]),
        ("错误条数", summary["error_findings"]),
        ("警告条数", summary["warning_findings"]),
        ("规范化记录", len(result.conversion.records)),
    ]
    cards_html = "".join(
        f'<div class="card"><strong>{_e(value)}</strong><span>{_e(label)}</span></div>'
        for label, value in cards
    )
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
    code_rows = []
    for code, count in summary["code_counts"].items():
        entry = catalog_for(code)
        code_rows.append([code, entry["category"], count, entry["cause"], entry["suggestion"]])
    findings = result.validation.findings[:max_details]
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
    normalized = result.conversion.records[:max_details]
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
    findings_note = ""
    if len(result.validation.findings) > max_details:
        findings_note = (
            f'<p class="note">仅显示前 {max_details} 条，共 '
            f"{len(result.validation.findings)} 条；完整明细见 JSON 与 SQLite 台账。</p>"
        )
    normalized_note = ""
    if len(result.conversion.records) > max_details:
        normalized_note = (
            f'<p class="note">仅显示前 {max_details} 条，共 '
            f"{len(result.conversion.records)} 条规范化记录。</p>"
        )
    return "\n".join(
        [
            "<!doctype html>",
            '<html lang="zh-CN">',
            "<head>",
            '<meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            "<title>量纲契约台运行报告</title>",
            f"<style>{REPORT_CSS}</style>",
            "</head>",
            "<body><main>",
            "<header><h1>量纲契约台运行报告</h1>",
            f'<p class="sub">契约 {_e(meta["contract_name"])} · Run {_e(meta["run_id"])}</p></header>',
            f'<section class="meta">{meta_html}</section>',
            '<h2>统计</h2>',
            f'<section class="cards">{cards_html}</section>',
            "<h2>契约摘要</h2>",
            _table(["字段", "量纲", "规范单位", "允许单位", "精度", "舍入", "范围"], contract_rows),
            "<h2>错误码分布</h2>",
            _table(["错误码", "类别", "数量", "原因", "建议"], code_rows),
            f"<h2>违规明细（前 {max_details} 条）</h2>",
            _table(["行", "字段", "错误码", "级别", "原始值", "单位", "说明"], finding_rows),
            findings_note,
            f"<h2>规范化值（前 {max_details} 条）</h2>",
            _table(["行", "字段", "原始值", "原始单位", "换算值", "规范单位"], normalized_rows),
            normalized_note,
            '<p class="note">Golden 输出通过 --no-timestamp 生成；相同契约、相同输入与参数'
            "应得到逐字节一致的报告。时间戳是唯一允许变化的内容。</p>",
            "<footer>本报告为本地离线生成；不含外部脚本、样式表或链接。</footer>",
            "</main></body></html>",
            "",
        ]
    )
