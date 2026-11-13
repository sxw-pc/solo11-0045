"""JSON, Markdown and self-contained HTML report writers."""

from __future__ import annotations

from pathlib import Path

from unitcontract.pipeline import RunResult

from .html_report import render_html
from .json_report import REPORT_SCHEMA_VERSION, build_json_report, dump_json, render_json
from .md_report import render_markdown

REPORT_FORMATS = ("json", "md", "html")


def render_reports(result: RunResult, max_details: int = 50) -> dict[str, str]:
    return {
        "json": render_json(result),
        "md": render_markdown(result, max_details=max_details),
        "html": render_html(result, max_details=max_details),
    }


def write_reports(
    result: RunResult,
    out_dir: str | Path,
    max_details: int = 50,
) -> dict[str, Path]:
    directory = Path(out_dir)
    directory.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}
    for fmt, content in render_reports(result, max_details=max_details).items():
        path = directory / f"report.{fmt}"
        path.write_text(content, encoding="utf-8", newline="\n")
        written[fmt] = path
    return written


__all__ = [
    "REPORT_FORMATS",
    "REPORT_SCHEMA_VERSION",
    "build_json_report",
    "dump_json",
    "render_html",
    "render_json",
    "render_markdown",
    "render_reports",
    "write_reports",
]
