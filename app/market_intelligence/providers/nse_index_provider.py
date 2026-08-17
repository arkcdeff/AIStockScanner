from __future__ import annotations

from datetime import date
from typing import Any

from app.data.nse_parser import load_bytes, parse_bhavcopy
from app.market_intelligence.providers.base import (
    MarketProvider,
    ProviderResult,
)
from app.services.nse_downloader import NSEDownloader, NSEDownloadError

# NSE indices only. SENSEX is a BSE index and is intentionally NOT listed
# here: the NSE bhavcopy provider cannot retrieve BSE data. SENSEX is
# handled by a separate BSE provider (Task 18.3+); until then it is
# reported as unavailable rather than fabricated from NSE data.
NSE_INDEX_CODES: tuple[str, ...] = (
    "NIFTY_50",
    "NIFTY_BANK",
    "NIFTY_FIN_SERV",
    "NIFTY_MIDCAP_100",
    "NIFTY_SMALLCAP_100",
    "NIFTY_NEXT_50",
)

# Kept for catalog/reference purposes; the NSE provider does NOT support it.
SENSEX_INDEX_CODE = "SENSEX"

INDEX_PROVIDER_SYMBOLS: dict[str, str] = {
    "NIFTY_50": "NIFTY50",
    "NIFTY_BANK": "BANKNIFTY",
    "NIFTY_FIN_SERV": "FINNIFTY",
    "NIFTY_MIDCAP_100": "NIFTIMIDCP100",
    "NIFTY_SMALLCAP_100": "NIFTISMALLCAP100",
    "NIFTY_NEXT_50": "NIFTINEXT50",
}


class NSEIndexProvider(MarketProvider):
    """Provider for Indian market indices (NSE-based).

    Fetches end-of-day index data from the official NSE bhavcopy.
    Respects NSE availability; returns appropriate provider statuses
    without bypassing anti-bot protections.
    """

    name: str = "nse_index"
    supports_indices: list[str] = list(NSE_INDEX_CODES)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _index_code_to_provider_symbol(code: str) -> str:
        return INDEX_PROVIDER_SYMBOLS.get(code, code)

    @staticmethod
    def _provider_symbol_to_index_code(symbol: str) -> str | None:
        for code, sym in INDEX_PROVIDER_SYMBOLS.items():
            if sym == symbol:
                return code
        return None

    # ------------------------------------------------------------------
    # Fetch
    # ------------------------------------------------------------------

    async def fetch(self, **kwargs: Any) -> ProviderResult:
        """Fetch index EOD data for the requested date.

        Supported kwargs:
            index_code  – one of the supported NIFTY/SENSEX codes
            trade_date  – date object (defaults to today)
        """
        index_code = kwargs.get("index_code")
        trade_date = kwargs.get("trade_date", date.today())

        if index_code == SENSEX_INDEX_CODE:
            # SENSEX is a BSE index; the NSE provider cannot retrieve it.
            # Return an honest unavailable result rather than fabricating data.
            return ProviderResult(
                provider_name=self.name,
                status="unavailable",
                freshness="missing",
                message=(
                    "SENSEX is a BSE index and is not available from the NSE "
                    "provider. Use a BSE source instead."
                ),
            )

        if index_code not in self.supports_indices:
            return ProviderResult(
                provider_name=self.name,
                status="failed",
                freshness="missing",
                message=f"Unsupported index code: {index_code}",
            )

        provider_symbol = self._index_code_to_provider_symbol(index_code)

        try:
            downloader = NSEDownloader()
        except Exception as exc:  # noqa: BLE001
            return ProviderResult(
                provider_name=self.name,
                status="failed",
                freshness="missing",
                message=f"NSE downloader init failed: {exc}",
            )

        try:
            path, meta = await downloader.download(trade_date)
        except NSEDownloadError as exc:
            message = exc.args[0] if exc.args else str(exc)
            if "403" in message:
                return ProviderResult(
                    provider_name=self.name,
                    status="blocked",
                    freshness="missing",
                    message=exc.args[0] if exc.args else str(exc),
                )
            if "404" in message or "holiday" in message.lower():
                return ProviderResult(
                    provider_name=self.name,
                    status="unavailable",
                    freshness="missing",
                    message=exc.args[0] if exc.args else str(exc),
                )
            return ProviderResult(
                provider_name=self.name,
                status="failed",
                freshness="missing",
                message=exc.args[0] if exc.args else str(exc),
            )
        except Exception as exc:  # noqa: BLE001
            return ProviderResult(
                provider_name=self.name,
                status="failed",
                freshness="missing",
                message=str(exc),
            )
        finally:
            await downloader.close()

        try:
            bhavcopy, file_name = load_bytes(path)
            records, _parse_errors = parse_bhavcopy(bhavcopy, file_name, {})
            # Filter records to only the index's provider symbol
            index_records = [r for r in records if r.symbol == provider_symbol]
            if not index_records:
                return ProviderResult(
                    provider_name=self.name,
                    status="unavailable",
                    freshness="missing",
                    message=f"No index data found for symbol {provider_symbol} on {trade_date}",
                )

            record = index_records[0]
            # Normalize to a price payload
            price_data = {
                "open": record.open,
                "high": record.high,
                "low": record.low,
                "close": record.close,
                "previous_close": record.prev_close,
                "volume": record.volume or 0,
                "trade_date": trade_date,
            }
            return ProviderResult(
                provider_name=self.name,
                status="success",
                freshness="live",
                data=price_data,
                observed_at=trade_date.isoformat(),
                fetched_at=date.today().isoformat(),
                market_date=trade_date.isoformat(),
            )
        except Exception as exc:  # noqa: BLE001
            return ProviderResult(
                provider_name=self.name,
                status="failed",
                freshness="missing",
                message=f"Failed to parse NSE bhavcopy: {exc}",
            )

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    async def health_check(self) -> ProviderResult:
        """Check NSE availability.

        Returns success/failed based on whether a recent bhavcopy
        can be fetched. Freshness is 'missing' since a one-off health
        check does not constitute live data.
        """
        try:
            downloader = NSEDownloader()
            # Try a very recent date; if it fails we treat as unavailable.
            try:
                await downloader.download(date.today())
                status = "success"
            except NSEDownloadError:
                status = "failed"
            finally:
                await downloader.close()
        except Exception:  # noqa: BLE001
            status = "failed"
        return ProviderResult(
            provider_name=self.name,
            status=status,
            freshness="missing",
            message="Health check performed",
        )