"""Command-line interface for the unit contract workbench."""

from __future__ import annotations

import argparse
import json
import socket
import sys
from pathlib import Path

from unitcontract.contract.schema import ContractValidationError
from unitcontract.demo import format_demo_summary, run_demo
from unitcontract.engine.merger import MergeError, MergeResult, SiteSpec, merge_sites
from unitcontract.engine.reader import DataReadError
from unitcontract.ledger.storage import save_run
from unitcontract.logging_utils import configure_logging
from unitcontract.pipeline import RunResult, execute
from unitcontract.report import write_reports

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _add_data_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--contract", required=True, help="contract YAML path")
    parser.add_argument("--data", required=True, help="CSV or JSONL data path")
    parser.add_argument(
        "--format", default="auto", choices=["auto", "csv", "jsonl"], dest="data_format"
    )


def _print_summary(result: RunResult, max_details: int = 10) -> None:
    summary = result.audit.as_dict()
    print(
        f"run_id={result.run_id} rows={summary['rows_total']} "
        f"passed={summary['passed_rows']} violating={summary['violation_rows']}"
    )
    print(
        f"errors={summary['error_findings']} warnings={summary['warning_findings']} "
        f"normalized={len(result.conversion.records)} skipped={result.conversion.skipped}"
    )
    if summary["code_counts"]:
        print("codes=" + " ".join(f"{code}:{count}" for code, count in summary["code_counts"].items()))
    for finding in result.validation.findings[:max_details]:
        print(
            f"  row={finding.row_number} field={finding.field} "
            f"code={finding.code} raw={finding.raw_value!r} unit={finding.unit!r}"
        )
    remaining = len(result.validation.findings) - max_details
    if remaining > 0:
        print(f"  ... {remaining} more finding(s); use report or ledger for full detail")


def _save_if_requested(result: RunResult, db_path: str | None, report_dir: str | None = None) -> None:
    if db_path:
        save_run(result, db_path, report_dir=report_dir)
        print(f"ledger={Path(db_path).resolve()}")


def command_demo(args: argparse.Namespace) -> int:
    out_dir = Path(args.out)
    configure_logging(args.log_file or out_dir / "unitcontract.log")
    output = run_demo(
        out_dir,
        total=args.rows,
        seed=args.seed,
        no_timestamp=args.no_timestamp,
        max_details=args.max_details,
    )
    print(format_demo_summary(output))
    return 0


def command_validate(args: argparse.Namespace) -> int:
    if args.log_file:
        configure_logging(args.log_file)
    result = execute(
        args.contract,
        args.data,
        data_format=args.data_format,
        no_timestamp=args.no_timestamp,
    )
    _print_summary(result, max_details=args.max_details)
    _save_if_requested(result, args.db)
    return 1 if result.validation.violating_rows else 0


def command_convert(args: argparse.Namespace) -> int:
    result = execute(args.contract, args.data, data_format=args.data_format)
    lines = [
        json.dumps(record.as_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        for record in result.conversion.records
    ]
    content = "\n".join(lines) + ("\n" if lines else "")
    if args.out:
        output_path = Path(args.out)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(content, encoding="utf-8", newline="\n")
        print(
            f"wrote={output_path.resolve()} records={len(lines)} skipped={result.conversion.skipped}",
            file=sys.stderr,
        )
    else:
        sys.stdout.write(content)
    return 0


def _conflict_path(out_path: Path) -> Path:
    """Derive a conflict-list path from the merged output path."""
    if out_path.suffix:
        return out_path.with_suffix(".conflicts.json")
    return out_path.with_name(out_path.name + ".conflicts.json")


def _merge_conflict_document(result: MergeResult) -> dict[str, object]:
    return {
        "schema_version": "1",
        "summary": result.summary(),
        "conflicts": [conflict.as_dict() for conflict in result.conflicts],
        "precision_drift": [item.as_dict() for item in result.drift],
    }


def _derived_site_name(contract_path: str) -> str:
    """Use the contract's declared name, falling back to the file stem."""
    path = Path(contract_path)
    try:
        import yaml

        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        document = None
    if isinstance(document, dict):
        name = document.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
    return path.stem


def _merge_specs(args: argparse.Namespace) -> list[SiteSpec]:
    """Collect site pairs from ``--site``, ``--pair`` and positional forms.

    All forms may be mixed. ``--site NAME CONTRACT DATA`` carries an explicit
    label; the other forms derive one from the contract's declared name or file
    stem. Argument order is irrelevant to the merge result.
    """
    specs: list[SiteSpec] = [
        SiteSpec(
            name=name,
            contract_path=contract,
            data_path=data,
            data_format=args.data_format,
        )
        for name, contract, data in (args.sites or [])
    ]
    for contract, data in args.pairs or []:
        specs.append(
            SiteSpec(
                name=_derived_site_name(contract),
                contract_path=contract,
                data_path=data,
                data_format=args.data_format,
            )
        )
    positional = args.paths or []
    if len(positional) % 2:
        raise MergeError(
            "positional merge paths must be CONTRACT DATA pairs; got an odd count"
        )
    for index in range(0, len(positional), 2):
        contract, data = positional[index], positional[index + 1]
        specs.append(
            SiteSpec(
                name=_derived_site_name(contract),
                contract_path=contract,
                data_path=data,
                data_format=args.data_format,
            )
        )
    contracts = args.contracts or []
    datas = args.datas or []
    if bool(contracts) != bool(datas):
        raise MergeError("merge --contract and --data must be supplied together")
    if len(contracts) != len(datas):
        raise MergeError(
            "--contract and --data must be given the same number of times"
        )
    for contract, data in zip(contracts, datas):
        specs.append(
            SiteSpec(
                name=_derived_site_name(contract),
                contract_path=contract,
                data_path=data,
                data_format=args.data_format,
            )
        )
    return specs


def command_merge(args: argparse.Namespace) -> int:
    result = merge_sites(_merge_specs(args), business_key=args.key)

    lines = [
        json.dumps(item.as_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        for item in result.merged
    ]
    content = "\n".join(lines) + ("\n" if lines else "")

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(content, encoding="utf-8", newline="\n")

    conflict_path = Path(args.conflicts) if args.conflicts else _conflict_path(out_path)
    conflict_path.parent.mkdir(parents=True, exist_ok=True)
    conflict_path.write_text(
        json.dumps(
            _merge_conflict_document(result),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )

    summary = result.summary()
    print(
        f"wrote={out_path.resolve()} records={len(result.merged)} "
        f"conflicts={summary['conflicts']} drift={summary['precision_drift']} "
        f"sites_ok={summary['sites_ok']} sites_skipped={summary['sites_skipped']}",
        file=sys.stderr,
    )
    print(f"conflicts_file={conflict_path.resolve()}", file=sys.stderr)
    for site in result.skipped_sites:
        print(
            f"  skipped site={site.name} codes={','.join(site.codes)}",
            file=sys.stderr,
        )
    return 1 if result.conflicts_found else 0


def command_report(args: argparse.Namespace) -> int:
    out_dir = Path(args.out)
    configure_logging(args.log_file or out_dir / "unitcontract.log")
    result = execute(
        args.contract,
        args.data,
        data_format=args.data_format,
        no_timestamp=args.no_timestamp,
    )
    run_dir = out_dir / "runs" / result.run_id
    write_reports(result, run_dir, max_details=args.max_details)
    written = write_reports(result, out_dir, max_details=args.max_details)
    _print_summary(result, max_details=args.max_details)
    for fmt, path in written.items():
        print(f"report.{fmt}={path.resolve()}")
    _save_if_requested(result, args.db, report_dir=str(run_dir))
    return 0


def _port_is_available(host: str, port: int) -> bool:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((host, port))
        except OSError:
            return False
    return True


def command_serve(args: argparse.Namespace) -> int:
    if args.host not in LOOPBACK_HOSTS:
        print(
            f"refusing host {args.host!r}: only loopback hosts "
            f"{', '.join(sorted(LOOPBACK_HOSTS))} are permitted",
            file=sys.stderr,
        )
        return 2
    if not 1 <= args.port <= 65535:
        print("port must be between 1 and 65535", file=sys.stderr)
        return 2
    if not Path(args.db).exists():
        print(
            f"ledger not found: {Path(args.db).resolve()}; run the demo or report command first",
            file=sys.stderr,
        )
        return 2
    if not _port_is_available(args.host, args.port):
        print(
            f"port {args.port} is already in use on {args.host}; "
            "refusing to fall back to another interface",
            file=sys.stderr,
        )
        return 3
    log_file = args.log_file or Path(args.db).with_name("serve.log")
    configure_logging(log_file)
    from unitcontract.server.app import create_app

    app = create_app(args.db, reports_dir=args.reports_dir)
    print(f"Serving on http://{args.host}:{args.port} (loopback only)")
    try:
        app.run(host=args.host, port=args.port, debug=False, use_reloader=False)
    except OSError as exc:
        print(f"server failed to bind {args.host}:{args.port}: {exc}", file=sys.stderr)
        return 3
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m unitcontract",
        description="Offline field dimension/unit contract validator and ledger.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    demo = subparsers.add_parser("demo", help="generate synthetic data and run end to end")
    demo.add_argument("--out", default="./_demo_out", help="output directory")
    demo.add_argument("--rows", type=int, default=240, help="number of demo rows (>=18)")
    demo.add_argument("--seed", type=int, default=11, help="deterministic data seed")
    demo.add_argument("--no-timestamp", action="store_true", help="omit generated timestamps")
    demo.add_argument("--max-details", type=int, default=50, help="detail rows in md/html")
    demo.add_argument("--log-file", help="optional local log path")
    demo.set_defaults(func=command_demo)

    validate = subparsers.add_parser("validate", help="validate a dataset against a contract")
    _add_data_args(validate)
    validate.add_argument("--no-timestamp", action="store_true")
    validate.add_argument("--max-details", type=int, default=10)
    validate.add_argument("--db", help="optional SQLite ledger path")
    validate.add_argument("--log-file")
    validate.set_defaults(func=command_validate)

    convert = subparsers.add_parser("convert", help="emit normalized records as JSONL")
    _add_data_args(convert)
    convert.add_argument("--out", help="JSONL output path; stdout when omitted")
    convert.set_defaults(func=command_convert)

    merge = subparsers.add_parser(
        "merge",
        help="merge normalized records from multiple site contract/data pairs",
    )
    merge.add_argument(
        "--site",
        action="append",
        nargs=3,
        metavar=("NAME", "CONTRACT", "DATA"),
        dest="sites",
        help="site name plus its contract and data paths; repeat for each site",
    )
    merge.add_argument(
        "--pair",
        action="append",
        nargs=2,
        metavar=("CONTRACT", "DATA"),
        dest="pairs",
        help=(
            "contract and data paths without a site label; the contract's "
            "declared name is used as the site name"
        ),
    )
    merge.add_argument(
        "paths",
        nargs="*",
        metavar="CONTRACT_OR_DATA",
        help=(
            "positional contract/data pairs: CONTRACT DATA [CONTRACT DATA ...]; "
            "site names are derived from each contract"
        ),
    )
    merge.add_argument(
        "--contract",
        action="append",
        dest="contracts",
        help="contract path; repeat, paired positionally with --data",
    )
    merge.add_argument(
        "--data",
        action="append",
        dest="datas",
        help="data path; repeat, paired positionally with --contract",
    )
    merge.add_argument("--out", required=True, help="merged JSONL output path")
    merge.add_argument(
        "--conflicts",
        help="conflict list JSON path; defaults to <out>.conflicts.json",
    )
    merge.add_argument(
        "--key",
        default="record_id",
        help="business key column used to group records (default: record_id)",
    )
    merge.add_argument(
        "--format",
        default="auto",
        choices=["auto", "csv", "jsonl"],
        dest="data_format",
        help="data format for every site input",
    )
    merge.set_defaults(func=command_merge)

    report = subparsers.add_parser("report", help="run and write json/md/html reports")
    _add_data_args(report)
    report.add_argument("--out", default="./_report_out")
    report.add_argument("--no-timestamp", action="store_true")
    report.add_argument("--max-details", type=int, default=50)
    report.add_argument("--db", help="optional SQLite ledger path")
    report.add_argument("--log-file")
    report.set_defaults(func=command_report)

    serve = subparsers.add_parser("serve", help="start the loopback-only local viewer")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8731)
    serve.add_argument("--db", default="./_demo_out/ledger.db")
    serve.add_argument("--reports-dir", help="override stored report directory")
    serve.add_argument("--log-file")
    serve.set_defaults(func=command_serve)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except ContractValidationError as exc:
        print("contract validation failed:", file=sys.stderr)
        for issue in exc.issues:
            print(f"  {issue.location} [{issue.code}] {issue.message}", file=sys.stderr)
        return 2
    except DataReadError as exc:
        print(f"data read failed: {exc}", file=sys.stderr)
        return 2
    except FileNotFoundError as exc:
        print(f"file not found: {exc.filename or exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
