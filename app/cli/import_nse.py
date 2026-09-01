from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from app.db.session import get_session_factory
from app.services.market_data_service import MarketDataService
from app.services.nse_downloader import NSEDownloader, NSEDownloadError
from app.services.nse_import import (
    MODE_DATE_DOWNLOAD,
    MODE_LATEST_DOWNLOAD,
    NSEImportService,
)


def get_async_session():
    return get_session_factory()()


app = typer.Typer()
console = Console()


@app.command()
def file(
    path: Annotated[str, typer.Argument(help="Path to bhavcopy CSV or ZIP")],
) -> None:
    """Import local NSE bhavcopy CSV/ZIP."""
    async def _import_file() -> None:
        async with get_async_session() as session:
            job = await NSEImportService(session).import_files(path)
        console.print(
            f"status={job.status} read={job.rows_read} inserted={job.rows_inserted} "
            f"updated={job.rows_updated} rejected={job.rows_rejected}"
        )
        if job.error_message:
            console.print(f"error={job.error_message}", style="red")
        raise typer.Exit(0 if job.status == "completed" else 1)

    asyncio.run(_import_file())


@app.command()
def date(
    trade_date: Annotated[str, typer.Option(help="Trade date in YYYY-MM-DD format")],
) -> None:
    """Download and import NSE bhavcopy for a date."""
    async def _download_and_import() -> None:
        target_date: datetime | None = None
        raw_path: Path | None = None

        try:
            from datetime import date

            trade_date_val = date.fromisoformat(trade_date)
            target_date = datetime.combine(trade_date_val, datetime.min.time())

            with NSEDownloader() as downloader:
                raw_path, _ = downloader.download(trade_date_val)

            async with get_async_session() as session:
                job = await NSEImportService(session).import_files(
                    raw_path, mode=MODE_DATE_DOWNLOAD
                )
            console.print(
                f"status={job.status} date={target_date.date()} read={job.rows_read} "
                f"inserted={job.rows_inserted} updated={job.rows_updated} "
                f"rejected={job.rows_rejected}"
            )
            if job.error_message:
                console.print(f"error={job.error_message}", style="red")
            raise typer.Exit(0 if job.status == "completed" else 1)
        except NSEDownloadError as exc:
            console.print(f"download_error={exc}", style="red")
            raise typer.Exit(1) from exc
        except ValueError as exc:
            console.print(f"error={exc}", style="red")
            raise typer.Exit(1) from exc

    asyncio.run(_download_and_import())

@app.command()
def latest() -> None:
    """Download and import latest available NSE bhavcopy."""
    async def _download_and_import() -> None:
        target_date: datetime | None = None
        raw_path: Path | None = None

        try:
            with NSEDownloader() as downloader:
                trade_date_val, raw_path, _ = downloader.download_latest()
                target_date = datetime.combine(trade_date_val, datetime.min.time())

            async with get_async_session() as session:
                job = await NSEImportService(session).import_files(
                    raw_path, mode=MODE_LATEST_DOWNLOAD
                )
            console.print(
                f"status={job.status} date={target_date.date()} read={job.rows_read} "
                f"inserted={job.rows_inserted} updated={job.rows_updated} "
                f"rejected={job.rows_rejected}"
            )
            if job.error_message:
                console.print(f"error={job.error_message}", style="red")
            raise typer.Exit(0 if job.status == "completed" else 1)
        except NSEDownloadError as exc:
            console.print(f"download_error={exc}", style="red")
            raise typer.Exit(1) from exc

    asyncio.run(_download_and_import())

@app.command()
def range(
    start: Annotated[str, typer.Option(help="Start date in YYYY-MM-DD format")],
    end: Annotated[str, typer.Option(help="End date in YYYY-MM-DD format")],
    delay: Annotated[float, typer.Option(help="Delay between NSE requests in seconds")] = 1.0,
) -> None:
    """Download and import all dates missing within a date range."""
    from datetime import date

    async def _backfill() -> None:
        try:
            start_date = date.fromisoformat(start)
            end_date = date.fromisoformat(end)
        except ValueError as exc:
            console.print(f"error={exc}", style="red")
            raise typer.Exit(1) from exc
        async with get_async_session() as session:
            summary = await MarketDataService(
                session, request_delay=delay
            ).backfill(start_date, end_date)
        for key, value in summary.items():
            console.print(f"{key}={value}")
        failed = int(summary.get("failed", 0)) + int(summary.get("blocked", 0))
        raise typer.Exit(0 if failed == 0 else 1)

    asyncio.run(_backfill())

@app.command()
def missing(
    start: Annotated[str, typer.Option(help="Start date in YYYY-MM-DD format")],
    end: Annotated[str, typer.Option(help="End date in YYYY-MM-DD format")],
) -> None:
    """List trading dates within a range that are missing from the database."""
    from datetime import date

    async def _list_missing() -> None:
        try:
            start_date = date.fromisoformat(start)
            end_date = date.fromisoformat(end)
        except ValueError as exc:
            console.print(f"error={exc}", style="red")
            raise typer.Exit(1) from exc
        async with get_async_session() as session:
            dates = await MarketDataService(session).missing_dates(start_date, end_date)
        for trade_date in dates:
            console.print(trade_date.isoformat())
        console.print(f"total_missing={len(dates)}")
        raise typer.Exit(0 if not dates else 1)

    asyncio.run(_list_missing())

@app.command()
def status() -> None:
    """Show market-data status and scanner readiness."""
    async def _status() -> None:
        async with get_async_session() as session:
            service = MarketDataService(session)
            data_status = await service.data_status()
            readiness = await service.scanner_readiness()
        console.print(
            f"stocks={data_status['stocks']} daily_prices={data_status['daily_prices']} "
            f"first_trade_date={data_status['first_trade_date']} "
            f"last_trade_date={data_status['last_trade_date']}"
        )
        latest_import = data_status.get("latest_import")
        if latest_import:
            console.print(
                f"latest_import=#{latest_import['id']} "
                f"trade_date={latest_import['trade_date']} "
                f"mode={latest_import.get('mode')} format={latest_import.get('format')}"
            )
        else:
            console.print("latest_import=none")
        console.print(f"missing_dates={data_status['missing_count']}")
        console.print(
            "readiness="
            + ", ".join(
                f"{threshold}plus={count}"
                for threshold, count in readiness["readiness"].items()
            )
        )
        raise typer.Exit(0)

    asyncio.run(_status())


@app.command()
def fo_contracts(
    path: Annotated[str, typer.Argument(help="Path to derivative contract master CSV file")],
    as_of_date: Annotated[str | None, typer.Option(help="As-of publication date (YYYY-MM-DD)")] = None,
    dry_run: Annotated[bool, typer.Option(help="Validate without persisting to DB")] = False,
) -> None:
    """Import local NSE F&O contract master file."""
    from datetime import date

    from app.services.derivative_import import DerivativeContractImportService

    async def _import_fo() -> None:
        file_path = Path(path)
        if not file_path.exists():
            console.print(f"error=File not found: {path}", style="red")
            raise typer.Exit(1)

        eff_date = date.fromisoformat(as_of_date) if as_of_date else None
        content = file_path.read_text(encoding="utf-8")

        async with get_async_session() as session:
            service = DerivativeContractImportService(session)
            summary = await service.import_contract_master_file(
                content=content,
                as_of_date=eff_date,
                source_name="FO_CONTRACT_MASTER",
                source_reference=file_path.name,
                dry_run=dry_run,
            )
            if not dry_run:
                await session.commit()

        console.print(
            f"dry_run={dry_run} total_rows={summary.total_rows} imported={summary.imported_count} "
            f"skipped={summary.skipped_count} errors={summary.error_count} "
            f"unresolved_underlying={summary.unresolved_underlying_count} "
            f"quality_issues={summary.quality_issues_count}"
        )
        raise typer.Exit(0 if summary.error_count == 0 else 1)

    asyncio.run(_import_fo())


@app.command()
def fo_futures(
    path: Annotated[str, typer.Argument(help="Path to NSE F&O EOD bhavcopy CSV file")],
    as_of_date: Annotated[str | None, typer.Option(help="As-of publication date (YYYY-MM-DD)")] = None,
    dry_run: Annotated[bool, typer.Option(help="Validate without persisting to DB")] = False,
) -> None:
    """Import local NSE Futures EOD bhavcopy file."""
    from datetime import date

    from app.services.derivative_futures_import import DerivativeFuturesImportService

    async def _import_futures() -> None:
        file_path = Path(path)
        if not file_path.exists():
            console.print(f"error=File not found: {path}", style="red")
            raise typer.Exit(1)

        eff_date = date.fromisoformat(as_of_date) if as_of_date else None
        content = file_path.read_text(encoding="utf-8")

        async with get_async_session() as session:
            service = DerivativeFuturesImportService(session)
            summary = await service.import_futures_bhavcopy(
                content=content,
                as_of_date=eff_date,
                source_name="FO_UDIFF_BHAVCOPY",
                source_reference=file_path.name,
                dry_run=dry_run,
            )

        console.print(
            f"dry_run={dry_run} total_rows={summary.total_rows} futures_rows={summary.futures_rows} "
            f"options_skipped={summary.options_skipped} imported={summary.imported_count} "
            f"unresolved_contracts={summary.unresolved_contracts} invalid_rows={summary.invalid_rows} "
            f"quality_issues={summary.quality_issues_count} duration_ms={summary.duration_ms}"
        )
        raise typer.Exit(0 if summary.invalid_rows == 0 else 1)

    asyncio.run(_import_futures())


@app.command()
def fo_options(
    path: Annotated[str, typer.Argument(help="Path to NSE F&O EOD bhavcopy CSV file")],
    as_of_date: Annotated[str | None, typer.Option(help="As-of publication date (YYYY-MM-DD)")] = None,
    dry_run: Annotated[bool, typer.Option(help="Validate without persisting to DB")] = False,
) -> None:
    """Import local NSE Options EOD bhavcopy file."""
    from datetime import date

    from app.services.derivative_options_import import DerivativeOptionsImportService

    async def _import_options() -> None:
        file_path = Path(path)
        if not file_path.exists():
            console.print(f"error=File not found: {path}", style="red")
            raise typer.Exit(1)

        eff_date = date.fromisoformat(as_of_date) if as_of_date else None
        content = file_path.read_text(encoding="utf-8")

        async with get_async_session() as session:
            service = DerivativeOptionsImportService(session)
            summary = await service.import_options_bhavcopy(
                content=content,
                as_of_date=eff_date,
                source_name="FO_UDIFF_BHAVCOPY",
                source_reference=file_path.name,
                dry_run=dry_run,
            )

        console.print(
            f"dry_run={dry_run} total_rows={summary.total_rows} options_rows={summary.options_rows} "
            f"futures_skipped={summary.futures_skipped} imported={summary.imported_count} "
            f"ce_rows={summary.ce_rows} pe_rows={summary.pe_rows} "
            f"unresolved_contracts={summary.unresolved_contracts} invalid_rows={summary.invalid_rows} "
            f"quality_issues={summary.quality_issues_count} duration_ms={summary.duration_ms}"
        )
        raise typer.Exit(0 if summary.invalid_rows == 0 else 1)

    asyncio.run(_import_options())


@app.command()
def fo_participant_oi(
    path: Annotated[str, typer.Argument(help="Path to NSE Participant-wise Open Interest CSV file")],
    report_date: Annotated[str | None, typer.Option(help="Report date (YYYY-MM-DD)")] = None,
    as_of_date: Annotated[str | None, typer.Option(help="As-of publication date (YYYY-MM-DD)")] = None,
) -> None:
    """Import local NSE Participant-wise Open Interest file."""
    from datetime import date

    from app.services.derivative_participant_import import DerivativeParticipantImportService

    async def _import_oi() -> None:
        file_path = Path(path)
        if not file_path.exists():
            console.print(f"error=File not found: {path}", style="red")
            raise typer.Exit(1)

        rep_date = date.fromisoformat(report_date) if report_date else None
        eff_date = date.fromisoformat(as_of_date) if as_of_date else rep_date
        content = file_path.read_text(encoding="utf-8")

        async with get_async_session() as session:
            service = DerivativeParticipantImportService(session)
            summary = await service.import_participant_oi(
                content=content,
                report_date=rep_date,
                filename=file_path.name,
                available_at=eff_date,
            )

        console.print(
            f"dataset={summary.dataset_code} report_date={summary.report_date} "
            f"parsed_records={summary.parsed_record_count} inserted={summary.inserted_count} "
            f"status={summary.status}"
        )
        raise typer.Exit(0)

    asyncio.run(_import_oi())


@app.command()
def fo_participant_volume(
    path: Annotated[str, typer.Argument(help="Path to NSE Participant-wise Trading Volume CSV file")],
    report_date: Annotated[str | None, typer.Option(help="Report date (YYYY-MM-DD)")] = None,
    as_of_date: Annotated[str | None, typer.Option(help="As-of publication date (YYYY-MM-DD)")] = None,
) -> None:
    """Import local NSE Participant-wise Trading Volume file."""
    from datetime import date

    from app.services.derivative_participant_import import DerivativeParticipantImportService

    async def _import_vol() -> None:
        file_path = Path(path)
        if not file_path.exists():
            console.print(f"error=File not found: {path}", style="red")
            raise typer.Exit(1)

        rep_date = date.fromisoformat(report_date) if report_date else None
        eff_date = date.fromisoformat(as_of_date) if as_of_date else rep_date
        content = file_path.read_text(encoding="utf-8")

        async with get_async_session() as session:
            service = DerivativeParticipantImportService(session)
            summary = await service.import_participant_volume(
                content=content,
                report_date=rep_date,
                filename=file_path.name,
                available_at=eff_date,
            )

        console.print(
            f"dataset={summary.dataset_code} report_date={summary.report_date} "
            f"parsed_records={summary.parsed_record_count} inserted={summary.inserted_count} "
            f"status={summary.status}"
        )
        raise typer.Exit(0)

    asyncio.run(_import_vol())


@app.command()
def fo_fii_derivatives(
    path: Annotated[str, typer.Argument(help="Path to NSE FII Derivatives Statistics CSV file")],
    report_date: Annotated[str | None, typer.Option(help="Report date (YYYY-MM-DD)")] = None,
    as_of_date: Annotated[str | None, typer.Option(help="As-of publication date (YYYY-MM-DD)")] = None,
) -> None:
    """Import local NSE FII Derivatives Statistics file."""
    from datetime import date

    from app.services.derivative_participant_import import DerivativeParticipantImportService

    async def _import_fii() -> None:
        file_path = Path(path)
        if not file_path.exists():
            console.print(f"error=File not found: {path}", style="red")
            raise typer.Exit(1)

        rep_date = date.fromisoformat(report_date) if report_date else None
        eff_date = date.fromisoformat(as_of_date) if as_of_date else rep_date
        content = file_path.read_text(encoding="utf-8")

        async with get_async_session() as session:
            service = DerivativeParticipantImportService(session)
            summary = await service.import_fii_derivatives_statistics(
                content=content,
                report_date=rep_date,
                filename=file_path.name,
                available_at=eff_date,
            )

        console.print(
            f"dataset={summary.dataset_code} report_date={summary.report_date} "
            f"parsed_records={summary.parsed_record_count} inserted={summary.inserted_count} "
            f"status={summary.status}"
        )
        raise typer.Exit(0)

    asyncio.run(_import_fii())


@app.command()
def financial_statements(
    path: Annotated[str, typer.Argument(help="Path to CSV file containing financial statements")],
) -> None:
    """Import local financial statement disclosures CSV file."""
    from app.services.fundamental_corporate_service import FundamentalCorporateService

    async def _import_stmts() -> None:
        file_path = Path(path)
        if not file_path.exists():
            console.print(f"error=File not found: {path}", style="red")
            raise typer.Exit(1)

        content = file_path.read_text(encoding="utf-8")
        async with get_async_session() as session:
            service = FundamentalCorporateService(session)
            res = await service.import_financial_statements(content)

        console.print(
            f"financial_statements processed={res['processed']} inserted={res['inserted']} updated={res['updated']}"
        )
        raise typer.Exit(0)

    asyncio.run(_import_stmts())


@app.command()
def corporate_announcements(
    path: Annotated[str, typer.Argument(help="Path to CSV file containing corporate announcements")],
) -> None:
    """Import local corporate announcements CSV file."""
    from app.services.fundamental_corporate_service import FundamentalCorporateService

    async def _import_anns() -> None:
        file_path = Path(path)
        if not file_path.exists():
            console.print(f"error=File not found: {path}", style="red")
            raise typer.Exit(1)

        content = file_path.read_text(encoding="utf-8")
        async with get_async_session() as session:
            service = FundamentalCorporateService(session)
            res = await service.import_corporate_announcements(content)

        console.print(f"corporate_announcements processed={res['processed']} inserted={res['inserted']}")
        raise typer.Exit(0)

    asyncio.run(_import_anns())


@app.command()
def shareholding_pattern(
    path: Annotated[str, typer.Argument(help="Path to CSV file containing shareholding patterns")],
) -> None:
    """Import local shareholding pattern CSV file."""
    from app.services.fundamental_corporate_service import FundamentalCorporateService

    async def _import_sh() -> None:
        file_path = Path(path)
        if not file_path.exists():
            console.print(f"error=File not found: {path}", style="red")
            raise typer.Exit(1)

        content = file_path.read_text(encoding="utf-8")
        async with get_async_session() as session:
            service = FundamentalCorporateService(session)
            res = await service.import_shareholding_patterns(content)

        console.print(f"shareholding_patterns processed={res['processed']} inserted={res['inserted']}")
        raise typer.Exit(0)

    asyncio.run(_import_sh())


@app.command()
def news_events(
    path: Annotated[str, typer.Argument(help="Path to CSV file containing news events")],
) -> None:
    """Import local news events CSV file."""
    from app.data.news_policy_parser import NewsPolicyParser
    from app.services.news_policy_service import NewsPolicyService

    async def _import_news() -> None:
        file_path = Path(path)
        if not file_path.exists():
            console.print(f"error=File not found: {path}", style="red")
            raise typer.Exit(1)

        content = file_path.read_text(encoding="utf-8")
        parsed = NewsPolicyParser.parse_csv(content, default_source_type="OFFICIAL_PRIMARY")
        async with get_async_session() as session:
            service = NewsPolicyService(session)
            inserted = await service.import_news_events(parsed)

        console.print(f"news_events processed={len(parsed)} inserted={inserted}")
        raise typer.Exit(0)

    asyncio.run(_import_news())


@app.command()
def policy_events(
    path: Annotated[str, typer.Argument(help="Path to CSV file containing government/regulatory policy events")],
) -> None:
    """Import local government policy and regulatory events CSV file."""
    from app.data.news_policy_parser import NewsPolicyParser
    from app.services.news_policy_service import NewsPolicyService

    async def _import_policy() -> None:
        file_path = Path(path)
        if not file_path.exists():
            console.print(f"error=File not found: {path}", style="red")
            raise typer.Exit(1)

        content = file_path.read_text(encoding="utf-8")
        parsed = NewsPolicyParser.parse_csv(content, default_source_type="OFFICIAL_PRIMARY")
        async with get_async_session() as session:
            service = NewsPolicyService(session)
            inserted = await service.import_news_events(parsed)

        console.print(f"policy_events processed={len(parsed)} inserted={inserted}")
        raise typer.Exit(0)

    asyncio.run(_import_policy())


@app.command()
def certify() -> None:
    """Print historical data coverage certification and domain readiness audit."""
    from app.services.historical_coverage_certification_service import (
        HistoricalCoverageCertificationService,
    )

    async def _certify() -> None:
        factory = get_session_factory()
        async with factory() as session:
            service = HistoricalCoverageCertificationService(session)
            report = await service.certify_coverage()

        console.print("[bold cyan]=== HISTORICAL DATA COVERAGE & VALIDATION READINESS ===[/bold cyan]")
        console.print(f"As of: {report.as_of_date}")
        console.print(f"Overall Holdout Readiness: [bold]{report.overall_holdout_readiness}[/bold]")
        console.print(f"PIT Status: {report.pit_status} | Idempotency: {report.idempotency_status} | Fixture Contamination: {report.fixture_contamination_status}\n")

        console.print(f"Stored Real Equities ({len(report.stored_equity_symbols)}): {report.stored_equity_symbols[:10]}")
        console.print(f"Stored Index Symbols ({len(report.stored_index_symbols)}): {report.stored_index_symbols}")
        console.print(f"Configured Reference Indices ({len(report.configured_reference_indices)}): {report.configured_reference_indices}")
        console.print(f"Synthetic Fixtures (Excluded) ({len(report.synthetic_fixture_symbols)}): {report.synthetic_fixture_symbols}\n")

        for d_name, d_rep in report.domains.items():
            status_style = "bold green" if d_rep.readiness == "READY" else ("bold yellow" if d_rep.readiness == "PARTIAL" else "bold red")
            console.print(f"Domain: [bold]{d_name:<20}[/bold] Readiness: [{status_style}]{d_rep.readiness:<12}[/{status_style}] Sessions: {d_rep.observed_sessions}/{d_rep.expected_sessions} ({d_rep.coverage_pct}%)")
            if d_rep.rejection_reason:
                console.print(f"  |- Reason: {d_rep.rejection_reason}", style="dim red")

    asyncio.run(_certify())


def main() -> None:
    app()


if __name__ == "__main__":
    main()
