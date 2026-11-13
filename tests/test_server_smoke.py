from __future__ import annotations

import json

from unitcontract.ledger.storage import save_run
from unitcontract.report import write_reports
from unitcontract.server.app import create_app


def build_client(result, tmp_path):
    report_dir = tmp_path / "reports" / result.run_id
    write_reports(result, report_dir)
    db_path = tmp_path / "ledger.db"
    save_run(result, db_path, report_dir=report_dir)
    app = create_app(db_path)
    app.config.update(TESTING=True)
    return app.test_client(), result.run_id


def test_home_and_static(result, tmp_path):
    client, run_id = build_client(result, tmp_path)
    response = client.get("/")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "量纲契约台" in html
    assert run_id in html
    assert 'href="/static/app.css"' in html
    assert "http://" not in html and "https://" not in html
    assert "<script" not in html
    assert "default-src 'self'" in response.headers["Content-Security-Policy"]

    css = client.get("/static/app.css")
    assert css.status_code == 200
    assert b"--accent" in css.data


def test_api_runs_and_details(result, tmp_path):
    client, run_id = build_client(result, tmp_path)
    listing = client.get("/api/runs")
    assert listing.status_code == 200
    body = listing.get_json()
    assert body["total"] == 1
    assert body["runs"][0]["run_id"] == run_id
    assert body["runs"][0]["contract_hash"] == result.contract_hash

    detail = client.get(f"/api/runs/{run_id}?limit=3&offset=2")
    assert detail.status_code == 200
    payload = detail.get_json()
    assert payload["run"]["run_id"] == run_id
    assert payload["summary"]["rows_total"] == 18
    assert payload["findings_total"] == 17
    assert len(payload["findings"]) == 3
    assert payload["findings"][0]["row"] == 3
    assert payload["pagination"] == {"limit": 3, "offset": 2}
    assert payload["report_links"]["html"].endswith("/html")

    missing = client.get("/api/runs/missing")
    assert missing.status_code == 404
    assert missing.get_json()["error"] == "run_not_found"

    bad = client.get("/api/runs?limit=abc")
    assert bad.status_code == 400
    assert bad.get_json()["error"] == "bad_request"


def test_report_downloads(result, tmp_path):
    client, run_id = build_client(result, tmp_path)
    for fmt in ("json", "md", "html"):
        response = client.get(f"/report/{run_id}/{fmt}")
        assert response.status_code == 200
        assert "attachment" in response.headers["Content-Disposition"]
        assert f"{run_id}.{fmt}" in response.headers["Content-Disposition"]
    payload = json.loads(client.get(f"/report/{run_id}/json").data)
    assert payload["schema_version"] == "1"
    assert client.get(f"/report/{run_id}/txt").status_code == 404
    assert client.get("/report/missing/html").status_code == 404


def test_home_with_empty_ledger(tmp_path):
    from unitcontract.ledger.storage import init_db

    db_path = tmp_path / "empty.db"
    init_db(db_path)
    app = create_app(db_path)
    app.config.update(TESTING=True)
    response = app.test_client().get("/")
    assert response.status_code == 200
    assert "尚无 run" in response.get_data(as_text=True)
