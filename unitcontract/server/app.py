"""Loopback-only Flask viewer for the local contract ledger."""

from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Any

from flask import Flask, abort, jsonify, request, send_file

from unitcontract.ledger.storage import count_runs, get_findings, get_run, init_db, list_runs

_REPORT_TYPES = {
    "json": ("report.json", "application/json"),
    "md": ("report.md", "text/markdown"),
    "html": ("report.html", "text/html"),
}


def _page_args(default_limit: int = 50, max_limit: int = 500) -> tuple[int, int]:
    try:
        limit = int(request.args.get("limit", default_limit))
        offset = int(request.args.get("offset", 0))
    except (TypeError, ValueError):
        abort(400, description="limit and offset must be integers")
    limit = max(0, min(limit, max_limit))
    offset = max(0, offset)
    return limit, offset


def _home_html(
    runs: list[dict[str, Any]],
    latest: dict[str, Any] | None,
    db_path: str | Path,
) -> str:
    run_rows = []
    for run in runs:
        run_id = escape(run["run_id"])
        run_rows.append(
            "<tr>"
            f"<td><span class=\"mono\">{run_id}</span></td>"
            f"<td>{escape(run['contract_name'])}</td>"
            f"<td>{run['rows_total']}</td>"
            f"<td>{run['violation_rows']}</td>"
            f"<td>{run['warning_findings']}</td>"
            f"<td>{escape(run['created_at'] or '未启用')}</td>"
            f"<td><a href=\"/report/{run_id}/html\">html</a> · "
            f"<a href=\"/report/{run_id}/md\">md</a> · "
            f"<a href=\"/report/{run_id}/json\">json</a></td>"
            "</tr>"
        )
    runs_table = (
        "<table><thead><tr><th>Run ID</th><th>契约</th><th>行数</th>"
        "<th>违规行</th><th>警告</th><th>时间</th><th>下载</th></tr></thead>"
        f"<tbody>{''.join(run_rows)}</tbody></table>"
        if run_rows
        else '<p class="empty">尚无 run。先运行 <code>python -m unitcontract demo --out ./_demo_out</code>。</p>'
    )
    latest_html = ""
    findings_html = ""
    if latest is not None:
        summary = latest["summary"]
        latest_html = (
            '<section class="cards">'
            f'<div class="card"><strong>{summary["rows_total"]}</strong><span>总行数</span></div>'
            f'<div class="card"><strong>{summary["passed_rows"]}</strong><span>通过行数</span></div>'
            f'<div class="card"><strong>{summary["violation_rows"]}</strong><span>违规行数</span></div>'
            f'<div class="card"><strong>{summary["error_findings"]}</strong><span>错误条数</span></div>'
            f'<div class="card"><strong>{summary["warning_findings"]}</strong><span>警告条数</span></div>'
            "</section>"
            '<section class="panel"><h2>最近契约</h2>'
            f'<p class="mono">{escape(latest["contract_name"])} · {escape(latest["run_id"])}</p>'
            f'<p class="muted">contract sha256: <span class="mono">{escape(latest["contract_hash"])}</span></p>'
            f'<p><a href="/api/runs/{escape(latest["run_id"])}">查看 JSON 摘要</a></p></section>'
        )
        findings, total = get_findings(db_path, latest["run_id"], limit=8)
        if findings:
            rows = "".join(
                "<tr>"
                f"<td>{item['row']}</td><td>{escape(item['field'])}</td>"
                f"<td><span class=\"{escape(item['severity'])}\">{escape(item['code'])}</span></td>"
                f"<td>{escape(item['message'])}</td></tr>"
                for item in findings
            )
            findings_html = (
                f'<section class="panel"><h2>最近问题（前 {len(findings)} / {total} 条）</h2>'
                "<table><thead><tr><th>行</th><th>字段</th><th>错误码</th><th>说明</th></tr></thead>"
                f"<tbody>{rows}</tbody></table></section>"
            )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>量纲契约台</title>
<link rel="stylesheet" href="/static/app.css">
</head>
<body>
<main>
<header><p class="eyebrow">LOCAL UNIT CONTRACT LEDGER</p>
<h1>量纲契约台</h1>
<p class="muted">本地离线字段量纲/单位契约校验、换算与差异审计台账。仅监听 loopback。</p></header>
<section class="headline"><h2>最近运行</h2><p class="muted">数据来自本地 SQLite 台账。</p></section>
{latest_html}
<section class="panel"><h2>Run 列表</h2>{runs_table}</section>
{findings_html}
<section class="panel"><h2>接口</h2><p class="mono">GET /api/runs</p>
<p class="mono">GET /api/runs/&lt;run_id&gt;?limit=50&amp;offset=0</p>
<p class="mono">GET /report/&lt;run_id&gt;/json|md|html</p></section>
<footer>无外部资源、无脚本、无网络请求。</footer>
</main>
</body>
</html>
"""


def create_app(
    db_path: str | Path,
    reports_dir: str | Path | None = None,
) -> Flask:
    package_dir = Path(__file__).resolve().parent
    app = Flask(
        __name__,
        static_folder=str(package_dir / "static"),
        static_url_path="/static",
    )
    app.config["LEDGER_DB"] = str(Path(db_path).resolve())
    app.config["REPORTS_DIR"] = str(Path(reports_dir).resolve()) if reports_dir else None
    init_db(app.config["LEDGER_DB"])

    @app.after_request
    def secure_headers(response: Any) -> Any:
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self'; img-src 'self'; "
            "base-uri 'none'; form-action 'none'"
        )
        return response

    @app.get("/")
    def home() -> str:
        runs = list_runs(app.config["LEDGER_DB"], limit=10)
        latest = get_run(app.config["LEDGER_DB"], runs[0]["run_id"]) if runs else None
        return _home_html(runs, latest, app.config["LEDGER_DB"])

    @app.get("/api/runs")
    def api_runs() -> Any:
        limit, offset = _page_args(default_limit=20, max_limit=200)
        runs = list_runs(app.config["LEDGER_DB"], limit=limit, offset=offset)
        return jsonify(
            {
                "runs": runs,
                "total": count_runs(app.config["LEDGER_DB"]),
                "limit": limit,
                "offset": offset,
            }
        )

    @app.get("/api/runs/<run_id>")
    def api_run(run_id: str) -> Any:
        run = get_run(app.config["LEDGER_DB"], run_id)
        if run is None:
            return jsonify({"error": "run_not_found", "run_id": run_id}), 404
        limit, offset = _page_args(default_limit=50, max_limit=500)
        findings, total = get_findings(app.config["LEDGER_DB"], run_id, limit=limit, offset=offset)
        payload = {
            "run": {
                key: value
                for key, value in run.items()
                if key not in {"report_dir", "summary", "contract"}
            },
            "summary": run["summary"],
            "contract": run["contract"],
            "findings": findings,
            "findings_total": total,
            "pagination": {"limit": limit, "offset": offset},
            "report_links": {
                "json": f"/report/{run_id}/json",
                "md": f"/report/{run_id}/md",
                "html": f"/report/{run_id}/html",
            },
        }
        return jsonify(payload)

    @app.get("/report/<run_id>/<fmt>")
    def download_report(run_id: str, fmt: str) -> Any:
        if fmt not in _REPORT_TYPES:
            return jsonify({"error": "unsupported_format", "supported": list(_REPORT_TYPES)}), 404
        run = get_run(app.config["LEDGER_DB"], run_id)
        if run is None:
            return jsonify({"error": "run_not_found", "run_id": run_id}), 404
        directory = app.config["REPORTS_DIR"] or run.get("report_dir")
        if not directory:
            return jsonify({"error": "reports_not_configured"}), 404
        filename, mimetype = _REPORT_TYPES[fmt]
        base = Path(directory)
        candidates = [
            base / filename,
            base / run_id / filename,
            base / "runs" / run_id / filename,
        ]
        report_path = next((path for path in candidates if path.is_file()), None)
        if report_path is None:
            return jsonify({"error": "report_missing", "path": filename}), 404
        return send_file(
            report_path,
            mimetype=mimetype,
            as_attachment=True,
            download_name=f"{run_id}.{fmt}",
        )

    @app.errorhandler(400)
    def bad_request(error: Any) -> Any:
        if request.path.startswith("/api/"):
            return jsonify({"error": "bad_request", "message": getattr(error, "description", "invalid request")}), 400
        return "<h1>400 Bad Request</h1>", 400

    @app.errorhandler(404)
    def not_found(error: Any) -> Any:
        if request.path.startswith("/api/"):
            return jsonify({"error": "not_found"}), 404
        return "<h1>404 Not Found</h1>", 404

    return app
