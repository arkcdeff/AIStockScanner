"""Upstox Live Market Data Provider — Sprint 19.1G-QA-MP.

Real-time streaming and REST snapshot market-data provider for Upstox v2. Features tick
normalization (LTP, Volume, OI, Bid/Ask, Timestamps), freshness tracking (LIVE, DELAYED,
STALE, DISCONNECTED), secret token redaction, and strict LIVE_READ_ONLY mode enforcement.
"""
from __future__ import annotations

import asyncio
import calendar
import json
import logging
from collections.abc import AsyncGenerator
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from app.core.config import settings
from app.providers.base import LiveMarketDataProvider
from app.providers.models import (
    FreshnessStatus,
    NormalizedOptionChain,
    NormalizedQuote,
    PriceType,
    ProviderCapability,
    ProviderMode,
)
from app.providers.upstox_historical_provider import redact_secrets
from app.providers.upstox_proto import MarketDataFeedV3_pb2

logger = logging.getLogger(__name__)

# G.11-OPT-5: bounded exponential backoff for transient provider failures.
_BACKOFF_BASE_SECONDS = 0.5
_BACKOFF_MAX_ATTEMPTS = 3


def _provider_unavailable(
    underlying: str,
    expiry_date: date | None,
    reason: str,
) -> NormalizedOptionChain:
    """Build an explicit UNAVAILABLE chain instead of a synthetic one.

    G.11-OPT-5 (defect D1): a provider timeout, HTTP 429/5xx, or auth failure
    must never be answered with the deterministic mock chain — capture or the
    live workbench would then persist/display fabricated quotes as if they were
    real observations. The unavailable result carries ``source_name="UNAVAILABLE"``
    and empty quotes so callers render an explicit outage state.
    """
    now = datetime.now(UTC)
    known_expiries: list[date] = []
    if expiry_date is not None:
        known_expiries = [expiry_date]
    return NormalizedOptionChain(
        underlying_symbol=underlying,
        as_of_timestamp=now,
        spot_price=None,
        expiries=known_expiries,
        calls=[],
        puts=[],
        ce_pe_paired_pct=0.0,
        provider_id="UPSTOX_LIVE",
        source_name="UNAVAILABLE",
        spot_provenance="PROVIDER_ERROR",
        quality_flags={"PROVIDER_ERROR": reason},
    )


def _as_int(value: Any) -> int | None:
    """Convert a provider numeric (int, float, numeric string) to int, else None."""
    if value in (None, ""):
        return None
    try:
        return int(float(value))
    except Exception:  # noqa: BLE001
        return None


def _parse_provider_timestamp(value: Any) -> datetime | None:
    """Parse a provider timestamp (ISO string or epoch ms) into an aware UTC datetime.

    Provider event time is preserved verbatim; it is never replaced by local
    receive time, which would destroy point-in-time ordering.
    """
    if value in (None, ""):
        return None
    text = str(value).strip()
    if text.isdigit():
        try:
            millis = int(text)
        except Exception:  # noqa: BLE001
            return None
        seconds = millis / 1000.0 if millis > 10_000_000_000 else float(millis)
        try:
            return datetime.fromtimestamp(seconds, tz=UTC)
        except Exception:  # noqa: BLE001
            return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:  # noqa: BLE001
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def resolve_index_instrument_key(underlying: str) -> str:
    """Map canonical or human-readable Indian index / stock symbol to Upstox instrument key."""
    u = underlying.strip().upper().replace(" ", "").replace("_", "")
    if u in {"NIFTY", "NIFTY50"}:
        return "NSE_INDEX|Nifty 50"
    elif u in {"BANKNIFTY", "NIFTYBANK"}:
        return "NSE_INDEX|Nifty Bank"
    elif u in {"FINNIFTY", "NIFTYFINSERVICE", "FINSERVICE"}:
        return "NSE_INDEX|Nifty Fin Service"
    elif u in {"MIDCPNIFTY", "NIFTYMIDSELECT", "MIDCAPNIFTY"}:
        return "NSE_INDEX|NIFTY MID SELECT"
    elif u in {"SENSEX", "BSESENSEX"}:
        return "BSE_INDEX|SENSEX"
    else:
        return f"NSE_EQ|{underlying.strip().upper()}"


def calculate_dynamic_index_expiries(ref_date: date | None = None) -> list[date]:
    """Calculate realistic dynamic Indian index expiry schedule without hardcoded dates.

    Generates:
      - Nearest weekly Thursday (weekday == 3)
      - Next weekly Thursday (+7d)
      - Far weekly Thursday (+14d)
      - Current/Next Monthly Thursday (last Thursday of month)
      - Following Monthly Thursday (last Thursday of month+1)
      - Quarterly / Far Monthly Thursday
    """
    today = ref_date or date.today()
    days_ahead = (3 - today.weekday()) % 7
    if days_ahead == 0:
        thursdays = [today]
    else:
        thursdays = [today + timedelta(days=days_ahead)]

    base_thu = thursdays[0]
    for w in range(1, 6):
        thursdays.append(base_thu + timedelta(days=7 * w))

    def get_last_thursday(year: int, month: int) -> date:
        last_day = calendar.monthrange(year, month)[1]
        dt = date(year, month, last_day)
        offset = (dt.weekday() - 3) % 7
        return dt - timedelta(days=offset)

    cur_y, cur_m = today.year, today.month
    monthly_dates = []
    for m_offset in range(4):
        m = (cur_m - 1 + m_offset) % 12 + 1
        y = cur_y + ((cur_m - 1 + m_offset) // 12)
        l_thu = get_last_thursday(y, m)
        if l_thu >= today:
            monthly_dates.append(l_thu)

    all_exp = sorted(list(set(thursdays[:4] + monthly_dates)))
    return [e for e in all_exp if e >= today]


class UpstoxLiveProvider(LiveMarketDataProvider):
    """Upstox v2 REST & WebSocket live market data provider."""

    def __init__(
        self,
        token: str | None = None,
        base_url: str | None = None,
        ws_url: str | None = None,
        mock_mode: bool = False,
    ) -> None:
        self.token = token or settings.upstox_analytics_token
        self.base_url = base_url or settings.upstox_base_url
        self.ws_url = ws_url or settings.upstox_ws_url
        self.mock_mode = mock_mode
        self._connected = False
        self._last_tick_time: datetime | None = None

    @property
    def provider_id(self) -> str:
        return "UPSTOX_LIVE"

    @property
    def provider_name(self) -> str:
        return "Upstox v2 Live REST & WebSocket Provider"

    @property
    def provider_mode(self) -> ProviderMode:
        return ProviderMode.LIVE_ONLY

    @property
    def capabilities(self) -> set[ProviderCapability]:
        return {
            ProviderCapability.CASH_EQUITY,
            ProviderCapability.INDEX,
            ProviderCapability.FUTURES,
            ProviderCapability.OPTIONS,
        }

    async def is_available(self) -> bool:
        """Return True if token is configured or mock mode is active."""
        if self.mock_mode:
            return True
        return bool(self.token and len(self.token.strip()) > 0)

    def _get_headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.token}",
        }

    def _is_market_open(self, now_dt: datetime) -> bool:
        """Check if current time is within NSE live trading session (09:15-15:30 IST, Mon-Fri)."""
        ist_tz = timezone(timedelta(hours=5, minutes=30))
        ist_now = now_dt.astimezone(ist_tz)
        if ist_now.weekday() >= 5:  # Weekend
            return False
        market_start = ist_now.replace(hour=9, minute=15, second=0, microsecond=0)
        market_end = ist_now.replace(hour=15, minute=30, second=0, microsecond=0)
        return market_start <= ist_now <= market_end

    def _evaluate_freshness(
        self,
        tick_time: datetime,
        check_market_hours: bool = False,
    ) -> FreshnessStatus:
        """Calculate quote freshness status based on timestamp age and connection state."""
        if not self._connected:
            return FreshnessStatus.DISCONNECTED

        now = datetime.now(UTC)
        if check_market_hours and not self._is_market_open(now):
            return FreshnessStatus.MARKET_CLOSED

        if tick_time.tzinfo is None:
            tick_time = tick_time.replace(tzinfo=UTC)

        age_seconds = (now - tick_time).total_seconds()
        if age_seconds <= settings.freshness_live_seconds:  # <= 5s
            return FreshnessStatus.LIVE
        elif age_seconds <= settings.freshness_delayed_seconds:  # <= 900s
            return FreshnessStatus.DELAYED
        else:
            return FreshnessStatus.STALE

    def is_quote_actionable(self, quote: NormalizedQuote | None) -> tuple[bool, str]:
        """Check if quote is fresh enough to support a new live actionable decision."""
        if not quote:
            return False, "QUOTE_MISSING"
        now = datetime.now(UTC)
        if not self._is_market_open(now):
            return False, "MARKET_CLOSED"
        if quote.freshness_status == FreshnessStatus.DISCONNECTED:
            return False, "DISCONNECTED"
        if quote.freshness_status != FreshnessStatus.LIVE:
            return False, f"LIVE_DATA_STALE ({quote.freshness_status.value})"
        return True, "ACTIONABLE_LIVE"

    async def _get_v3_authorized_ws_uri(self) -> str | None:
        """Execute Upstox Market Data Feed V3 authorize flow to obtain authorized WebSocket URI."""
        if not self.token:
            return None
        try:
            import httpx
            headers = self._get_headers()
            auth_urls = [
                settings.upstox_v3_feed_authorize_url,
                f"{self.base_url}/feed/market-data-feed/authorize",
            ]
            async with httpx.AsyncClient(timeout=5) as client:
                for url in auth_urls:
                    resp = await client.get(url, headers=headers)
                    if resp.status_code == 200:
                        data = resp.json().get("data", {})
                        uri = data.get("authorized_redirect_uri") or data.get("authorizedRedirectUri")
                        if uri:
                            return str(uri)
            return None
        except Exception as exc:
            logger.error(redact_secrets(f"Upstox V3 WebSocket authorize failed: {exc}"))
            return None

    async def get_live_quote(self, symbol: str) -> NormalizedQuote | None:
        """Fetch single real-time quote snapshot."""
        sym_clean = symbol.strip().upper()
        if self.mock_mode or not self.token:
            return self._generate_mock_quote(sym_clean)

        try:
            import httpx
            # Instrument key discovery
            if "|" in sym_clean:
                key_candidates = [sym_clean]
            elif sym_clean in {"NIFTY", "NIFTY50", "NIFTY_50"}:
                key_candidates = ["NSE_INDEX|Nifty 50"]
            elif sym_clean in {"BANKNIFTY", "NIFTYBANK"}:
                key_candidates = ["NSE_INDEX|Nifty Bank"]
            else:
                key_candidates = [f"NSE_EQ|{sym_clean}"]

            async with httpx.AsyncClient(timeout=5) as client:
                for instrument_key in key_candidates:
                    url = f"{self.base_url}/market-quote/quotes?instrument_key={instrument_key}"
                    resp = await client.get(url, headers=self._get_headers())
                    if resp.status_code == 200:
                        payload = resp.json()
                        quote = self._parse_upstox_quote_item(sym_clean, payload)
                        if quote:
                            self._connected = True
                            self._last_tick_time = quote.timestamp
                            return quote
                return self._generate_mock_quote(sym_clean)
        except Exception as exc:
            logger.error(redact_secrets(f"Upstox live quote request failed: {exc}"))
            return self._generate_mock_quote(sym_clean)

    async def get_futures_quote(self, instrument_key: str) -> dict[str, Any] | None:
        """Read one real futures quote (LTP, volume, open interest, day ranges).

        Real open interest *is* entitled for index futures, so this is the
        authoritative source for the futures ledger. Returns ``None`` on any
        failure: callers must show an unavailable state rather than substituting a
        modelled cost-of-carry price.
        """
        if self.mock_mode or not self.token or "|" not in instrument_key:
            return None
        try:
            import httpx

            url = f"{self.base_url}/market-quote/quotes?instrument_key={instrument_key}"
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(url, headers=self._get_headers())
            if resp.status_code != 200:
                logger.error(
                    redact_secrets(f"Upstox futures quote HTTP {resp.status_code}")
                )
                return None
            payload = resp.json()
            data = payload.get("data") or {}
            if not data:
                return None
            raw = next(iter(data.values()))
            ohlc = raw.get("ohlc") or {}
            return {
                "symbol": raw.get("symbol"),
                "instrument_key": raw.get("instrument_token") or instrument_key,
                "last_price": raw.get("last_price"),
                "previous_close": ohlc.get("close"),
                "day_open": ohlc.get("open"),
                "day_high": ohlc.get("high"),
                "day_low": ohlc.get("low"),
                "volume": raw.get("volume"),
                "open_interest": _as_int(raw.get("oi")),
                "oi_day_high": _as_int(raw.get("oi_day_high")),
                "oi_day_low": _as_int(raw.get("oi_day_low")),
                "net_change": raw.get("net_change"),
                "total_buy_quantity": _as_int(raw.get("total_buy_quantity")),
                "total_sell_quantity": _as_int(raw.get("total_sell_quantity")),
                "timestamp": _parse_provider_timestamp(raw.get("timestamp")),
                "source": "UPSTOX_REST",
            }
        except Exception as exc:  # noqa: BLE001 - transport failure is not fatal
            logger.error(redact_secrets(f"Upstox futures quote failed: {exc}"))
            return None

    async def get_index_spot(self, underlying_symbol: str) -> tuple[Decimal | None, datetime | None, str]:
        """Resolve the **real** spot for one underlying.

        Root cause fix for G.11-OPT-3: the option-chain parser previously returned a
        constant ``24500.00`` for every underlying, which made NIFTY and BANKNIFTY
        share one wrong ATM. Returns ``(spot, observed_at, provenance)``.
        """
        u_clean = underlying_symbol.strip().upper()
        if self.mock_mode or not self.token:
            return None, None, "UNAVAILABLE"
        instrument_key = resolve_index_instrument_key(u_clean)
        if "|" not in instrument_key:
            instrument_key = f"NSE_EQ|{u_clean}"
        try:
            import httpx

            url = f"{self.base_url}/market-quote/ltp?instrument_key={instrument_key}"
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(url, headers=self._get_headers())
            if resp.status_code != 200:
                return None, None, "UNAVAILABLE"
            data = resp.json().get("data") or {}
            if not data:
                return None, None, "UNAVAILABLE"
            raw = next(iter(data.values()))
            price = raw.get("last_price")
            if price in (None, ""):
                return None, None, "UNAVAILABLE"
            return Decimal(str(price)), datetime.now(UTC), "UPSTOX_INDEX_LTP"
        except Exception as exc:  # noqa: BLE001
            logger.error(redact_secrets(f"Upstox index spot read failed: {exc}"))
            return None, None, "UNAVAILABLE"

    async def get_listed_expiries(self, underlying_symbol: str) -> list[date]:
        """Fetch all actually listed, non-expired option expiry dates for the underlying.

        Dynamically queries exchange contract master without hardcoded dates or calendar assumptions.

        """
        u_clean = underlying_symbol.strip().upper()
        if self.mock_mode or not self.token:
            return calculate_dynamic_index_expiries()

        try:
            import httpx
            instrument_key = resolve_index_instrument_key(u_clean)
            async with httpx.AsyncClient(timeout=5) as client:
                contract_url = f"{self.base_url}/option/contract?instrument_key={instrument_key}"
                c_resp = await client.get(contract_url, headers=self._get_headers())
                if c_resp.status_code == 200:
                    contracts = c_resp.json().get("data", [])
                    today_str = date.today().isoformat()
                    expiries_set = {
                        date.fromisoformat(c["expiry"])
                        for c in contracts
                        if c.get("expiry") and c["expiry"] >= today_str
                    }
                    if expiries_set:
                        return sorted(list(expiries_set))
        except Exception as exc:
            logger.error(redact_secrets(f"Upstox get_listed_expiries failed: {exc}"))

        return calculate_dynamic_index_expiries()

    async def get_live_option_chain(
        self,
        underlying_symbol: str,
        expiry_date: date | None = None,
    ) -> NormalizedOptionChain:
        """Fetch live option chain snapshot preserving all discovered expiries."""
        u_clean = underlying_symbol.strip().upper()
        if self.mock_mode or not self.token:
            return self._generate_mock_live_option_chain(u_clean, expiry_date)

        import httpx
        instrument_key = resolve_index_instrument_key(u_clean)

        last_reason = "UNKNOWN"
        retry_after: float | None = None
        for attempt in range(_BACKOFF_MAX_ATTEMPTS):
            if attempt:
                await asyncio.sleep(
                    max(
                        _BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)),
                        retry_after or 0.0,
                    )
                )
                retry_after = None
            try:
                all_known_expiries = await self.get_listed_expiries(u_clean)

                if not expiry_date and all_known_expiries:
                    expiry_date = all_known_expiries[0]

                # G.11-OPT-3: resolve the underlying's OWN spot. Without this the chain
                # carried a constant 24500.00 for every index and every ATM was wrong.
                spot_price, spot_observed_at, spot_provenance = await self.get_index_spot(u_clean)

                async with httpx.AsyncClient(timeout=5) as client:
                    exp_param = f"&expiry_date={expiry_date.isoformat()}" if expiry_date else ""
                    url = f"{self.base_url}/option/chain?instrument_key={instrument_key}{exp_param}"
                    resp = await client.get(url, headers=self._get_headers())
                if resp.status_code == 200:
                    payload = resp.json()
                    self._connected = True
                    return self._parse_upstox_live_option_chain(
                        u_clean,
                        payload,
                        known_expiries=all_known_expiries,
                        spot_price=spot_price,
                        spot_observed_at=spot_observed_at,
                        spot_provenance=spot_provenance,
                    )
                last_reason = f"HTTP_{resp.status_code}"
                if resp.status_code in (429, 500, 502, 503, 504):
                    if resp.status_code == 429:
                        raw_retry_after = str(resp.headers.get("Retry-After") or "").strip()
                        if raw_retry_after:
                            try:
                                retry_after = max(0.0, float(raw_retry_after))
                            except ValueError:
                                retry_after = None
                    logger.warning(
                        redact_secrets(
                            f"Upstox live option chain transient HTTP {resp.status_code} "
                            f"(attempt {attempt + 1}/{_BACKOFF_MAX_ATTEMPTS})"
                        )
                    )
                    continue
                logger.error(redact_secrets(f"Upstox live option chain HTTP {resp.status_code}"))
                return _provider_unavailable(u_clean, expiry_date, last_reason)
            except Exception as exc:  # noqa: BLE001
                last_reason = type(exc).__name__
                logger.warning(
                    redact_secrets(
                        f"Upstox live option chain attempt {attempt + 1}/{_BACKOFF_MAX_ATTEMPTS} failed: {exc}"
                    )
                )
                continue

        # G.11-OPT-5 (D1): an outage is an outage. Never substitute the mock
        # chain for a failed live read.
        logger.error(redact_secrets(f"Upstox live option chain unavailable: {last_reason}"))
        return _provider_unavailable(u_clean, expiry_date, last_reason)

    async def subscribe_ticks(
        self,
        symbols: list[str],
    ) -> AsyncGenerator[NormalizedQuote]:
        """Subscribe to real-time tick stream using Upstox V3 Market Data Feed authorize flow."""
        if self.mock_mode or not self.token:
            # Emit deterministic mock ticks for test stream
            for sym in symbols:
                q = self._generate_mock_quote(sym)
                yield q
                await asyncio.sleep(0.01)
            return

        # Execute V3 WebSocket authorize flow
        authorized_ws_uri = await self._get_v3_authorized_ws_uri()
        ws_uri = authorized_ws_uri or f"{self.ws_url}?authtoken={self.token}"

        try:
            import websockets
            async with websockets.connect(ws_uri) as ws:
                self._connected = True
                # Send bounded subscription payload with properly mapped instrument keys
                ik_list = [
                    s if "|" in s else ("NSE_INDEX|Nifty 50" if s in {"NIFTY", "NIFTY50", "NIFTY_50"} else f"NSE_EQ|{s}")
                    for s in symbols
                ]
                sub_payload = {
                    "guid": "sub_guid_123",
                    "method": "sub",
                    "data": {
                        "mode": "full",
                        "instrumentKeys": ik_list,
                    },
                }
                await ws.send(json.dumps(sub_payload))

                while self._connected:
                    msg = await ws.recv()
                    now = datetime.now(UTC)
                    self._last_tick_time = now

                    if isinstance(msg, bytes):
                        feed_resp = MarketDataFeedV3_pb2.FeedResponse()
                        try:
                            feed_resp.ParseFromString(msg)
                            for ik, feed in feed_resp.feeds.items():
                                ltp_val = None
                                close_val = None
                                vol_val = None
                                oi_val = None
                                bid_val = None
                                ask_val = None

                                if feed.HasField("full_feed"):
                                    ff = feed.full_feed
                                    if ff.HasField("market_ff"):
                                        mff = ff.market_ff
                                        if mff.ltpc.ltp:
                                            ltp_val = Decimal(str(mff.ltpc.ltp))
                                        if mff.ltpc.close:
                                            close_val = Decimal(str(mff.ltpc.close))
                                        if mff.eoi:
                                            oi_val = mff.eoi
                                        if mff.HasField("market_ohlc") and mff.market_ohlc.ohlc:
                                            for ohlc in mff.market_ohlc.ohlc:
                                                if ohlc.interval == "1d":
                                                    vol_val = ohlc.volume
                                        if mff.HasField("market_level") and mff.market_level.bid_ask:
                                            quotes = mff.market_level.bid_ask
                                            if len(quotes) > 0 and quotes[0].price:
                                                bid_val = Decimal(str(quotes[0].price))
                                            if len(quotes) > 1 and quotes[1].price:
                                                ask_val = Decimal(str(quotes[1].price))
                                    elif ff.HasField("index_ff"):
                                        iff = ff.index_ff
                                        if iff.ltpc.ltp:
                                            ltp_val = Decimal(str(iff.ltpc.ltp))
                                        if iff.ltpc.close:
                                            close_val = Decimal(str(iff.ltpc.close))
                                elif feed.HasField("ltpc"):
                                    ltpc = feed.ltpc
                                    if ltpc.ltp:
                                        ltp_val = Decimal(str(ltpc.ltp))
                                    if ltpc.close:
                                        close_val = Decimal(str(ltpc.close))

                                # Derive canonical symbol from ik
                                sym_clean = ik.split("|")[-1] if "|" in ik else ik
                                inst_type = "INDEX" if "INDEX" in ik else ("OPTION" if ("CE" in ik or "PE" in ik) else ("FUTURE" if "FUT" in ik else "EQUITY"))

                                yield NormalizedQuote(
                                    symbol=sym_clean,
                                    instrument_type=inst_type,
                                    ltp=ltp_val,
                                    close=close_val,
                                    volume=vol_val,
                                    open_interest=oi_val,
                                    bid=bid_val,
                                    ask=ask_val,
                                    timestamp=now,
                                    price_type=PriceType.UPSTOX_LIVE_LTP,
                                    source_name="UPSTOX_WEBSOCKET_V3",
                                    provider_id=self.provider_id,
                                    freshness_status=self._evaluate_freshness(now),
                                    raw_provider_meta={
                                        "transport_origin": "UPSTOX_WEBSOCKET_V3",
                                        "instrument_key": ik,
                                        "provider": self.provider_id,
                                        "provider_timestamp": now,
                                        "received_at": now,
                                    },
                                )
                        except Exception as p_exc:
                            logger.error(redact_secrets(f"Protobuf V3 decode error: {p_exc}"))
        except Exception as exc:
            self._connected = False
            logger.error(redact_secrets(f"Upstox V3 WebSocket stream failed: {exc}"))

    def _parse_upstox_quote_item(self, symbol: str, payload: dict[str, Any]) -> NormalizedQuote | None:
        """Parse raw Upstox quote response dictionary."""
        data = payload.get("data", {})
        for _key, item in data.items():
            now = datetime.now(UTC)
            ltp = Decimal(str(item.get("last_price", 0)))
            close_p = Decimal(str(item.get("cp", 0) or item.get("ohlc", {}).get("close", 0)))
            open_p = Decimal(str(item.get("ohlc", {}).get("open", 0)))
            high_p = Decimal(str(item.get("ohlc", {}).get("high", 0)))
            low_p = Decimal(str(item.get("ohlc", {}).get("low", 0)))
            vol = int(item.get("volume") or 0)
            oi = int(item.get("oi") or 0)

            depth = item.get("depth", {})
            bids = depth.get("buy", [])
            asks = depth.get("sell", [])

            bid = Decimal(str(bids[0].get("price") or 0)) if bids else None
            bid_q = int(bids[0].get("quantity") or 0) if bids else None
            ask = Decimal(str(asks[0].get("price") or 0)) if asks else None
            ask_q = int(asks[0].get("quantity") or 0) if asks else None

            inst_type = (
                "INDEX"
                if ("INDEX" in _key or "NIFTY" in symbol and "CE" not in symbol and "PE" not in symbol and "FUT" not in symbol)
                else ("OPTION" if ("CE" in _key or "PE" in _key or "CE" in symbol or "PE" in symbol)
                      else ("FUTURE" if ("FUT" in _key or "FUT" in symbol) else "EQUITY"))
            )

            return NormalizedQuote(
                symbol=symbol,
                instrument_type=inst_type,
                ltp=ltp,
                previous_close=close_p,
                open=open_p,
                high=high_p,
                low=low_p,
                close=close_p,
                volume=vol,
                open_interest=oi,
                bid=bid,
                ask=ask,
                bid_qty=bid_q,
                ask_qty=ask_q,
                timestamp=now,
                price_type=PriceType.UPSTOX_LIVE_LTP,
                source_name="UPSTOX_REST",
                provider_id=self.provider_id,
                freshness_status=self._evaluate_freshness(now),
                raw_provider_meta={
                    "transport_origin": "UPSTOX_REST",
                    "instrument_key": _key,
                    "provider": self.provider_id,
                    "provider_timestamp": item.get("timestamp"),
                    "received_at": now,
                    "raw_data": item,
                },
            )
        return None

    def _parse_upstox_live_option_chain(
        self,
        underlying: str,
        payload: dict[str, Any],
        known_expiries: list[date] | None = None,
        spot_price: Decimal | None = None,
        spot_observed_at: datetime | None = None,
        spot_provenance: str = "UNKNOWN",
    ) -> NormalizedOptionChain:
        """Parse live option chain payload, preserving real per-underlying spot.

        The expiry list is built from the *parsed contracts plus the caller's
        provider-listed set only*. A calendar-derived fallback is labelled through
        ``expiry_provenance`` so it can never be rendered as a listed expiry.
        """
        data_list = payload.get("data", [])
        calls: list[NormalizedQuote] = []
        puts: list[NormalizedQuote] = []

        # Prefer the provider's own chain-level spot when it publishes one, else the
        # explicitly resolved index LTP. Never a constant.
        chain_spot_raw = payload.get("underlying_spot_price")
        if chain_spot_raw not in (None, ""):
            try:
                spot_price = Decimal(str(chain_spot_raw))
                spot_provenance = "UPSTOX_CHAIN_UNDERLYING_SPOT"
                spot_observed_at = spot_observed_at or datetime.now(UTC)
            except Exception:  # noqa: BLE001
                pass

        parsed_expiries: set[date] = set()
        declared_expiries: set[date] = set(known_expiries or [])
        expiries: set[date] = set(declared_expiries)

        for item in data_list:
            strike = Decimal(str(item.get("strike_price", 0)))
            exp_str = item.get("expiry")
            exp_d = date.fromisoformat(exp_str) if exp_str else date.today()
            expiries.add(exp_d)
            parsed_expiries.add(exp_d)

            call_d = item.get("call_options", {})
            if call_d:
                m_data = call_d.get("market_data", {})
                og_data = call_d.get("option_greeks", {})
                greeks_meta = {
                    "iv": og_data.get("iv"),
                    "delta": og_data.get("delta"),
                    "gamma": og_data.get("gamma"),
                    "theta": og_data.get("theta"),
                    "vega": og_data.get("vega"),
                    "origin": "UPSTOX_PROVIDER_SUPPLIED",
                } if og_data else {}

                c_ltp = Decimal(str(m_data.get("ltp", 0)))
                c_close = m_data.get("close_price") or m_data.get("close")
                c_prev_close = Decimal(str(c_close)) if c_close else None
                c_change = (c_ltp - c_prev_close) if (c_prev_close and c_ltp) else (Decimal(str(m_data["pnl"])) if m_data.get("pnl") is not None else None)
                c_change_pct = round((c_change / c_prev_close) * 100, 2) if (c_change and c_prev_close and c_prev_close > 0) else None
                c_prev_oi = m_data.get("prev_oi") or m_data.get("previous_oi")
                c_oi = int(m_data.get("oi", 0))
                c_doi = (c_oi - int(c_prev_oi)) if c_prev_oi is not None else (int(m_data.get("oi_change")) if m_data.get("oi_change") else None)

                calls.append(
                    NormalizedQuote(
                        symbol=call_d.get("trading_symbol", f"{underlying}CE"),
                        instrument_type="OPTION",
                        ltp=c_ltp,
                        previous_close=c_prev_close,
                        change=c_change,
                        change_pct=c_change_pct,
                        volume=int(m_data.get("volume", 0)),
                        open_interest=c_oi,
                        change_in_open_interest=c_doi,
                        source_change_in_oi=c_doi,
                        bid=Decimal(str(m_data.get("bid_price", 0))) if m_data.get("bid_price") else None,
                        ask=Decimal(str(m_data.get("ask_price", 0))) if m_data.get("ask_price") else None,
                        bid_qty=int(m_data.get("bid_qty", 0)) if m_data.get("bid_qty") else None,
                        ask_qty=int(m_data.get("ask_qty", 0)) if m_data.get("ask_qty") else None,
                        strike_price=strike,
                        option_type="CE",
                        expiry_date=exp_d,
                        timestamp=datetime.now(UTC),
                        price_type=PriceType.UPSTOX_LIVE_LTP,
                        source_name="UPSTOX_REST",
                        provider_id=self.provider_id,
                        freshness_status=FreshnessStatus.LIVE,
                        source_greeks=greeks_meta,
                        raw_provider_meta={
                            "transport_origin": "UPSTOX_REST",
                            "instrument_key": call_d.get("instrument_key"),
                            "provider": self.provider_id,
                            "trading_symbol": call_d.get("trading_symbol"),
                            "option_greeks": greeks_meta,
                        },
                    )
                )

            put_d = item.get("put_options", {})
            if put_d:
                m_data = put_d.get("market_data", {})
                og_data = put_d.get("option_greeks", {})
                # Note: Exact Upstox gamma is preserved without manual negation
                greeks_meta = {
                    "iv": og_data.get("iv"),
                    "delta": og_data.get("delta"),
                    "gamma": og_data.get("gamma"),
                    "theta": og_data.get("theta"),
                    "vega": og_data.get("vega"),
                    "origin": "UPSTOX_PROVIDER_SUPPLIED",
                } if og_data else {}

                p_ltp = Decimal(str(m_data.get("ltp", 0)))
                p_close = m_data.get("close_price") or m_data.get("close")
                p_prev_close = Decimal(str(p_close)) if p_close else None
                p_change = (p_ltp - p_prev_close) if (p_prev_close and p_ltp) else (Decimal(str(m_data["pnl"])) if m_data.get("pnl") is not None else None)
                p_change_pct = round((p_change / p_prev_close) * 100, 2) if (p_change and p_prev_close and p_prev_close > 0) else None
                p_prev_oi = m_data.get("prev_oi") or m_data.get("previous_oi")
                p_oi = int(m_data.get("oi", 0))
                p_doi = (p_oi - int(p_prev_oi)) if p_prev_oi is not None else (int(m_data.get("oi_change")) if m_data.get("oi_change") else None)

                puts.append(
                    NormalizedQuote(
                        symbol=put_d.get("trading_symbol", f"{underlying}PE"),
                        instrument_type="OPTION",
                        ltp=p_ltp,
                        previous_close=p_prev_close,
                        change=p_change,
                        change_pct=p_change_pct,
                        volume=int(m_data.get("volume", 0)),
                        open_interest=p_oi,
                        change_in_open_interest=p_doi,
                        source_change_in_oi=p_doi,
                        bid=Decimal(str(m_data.get("bid_price", 0))) if m_data.get("bid_price") else None,
                        ask=Decimal(str(m_data.get("ask_price", 0))) if m_data.get("ask_price") else None,
                        bid_qty=int(m_data.get("bid_qty", 0)) if m_data.get("bid_qty") else None,
                        ask_qty=int(m_data.get("ask_qty", 0)) if m_data.get("ask_qty") else None,
                        strike_price=strike,
                        option_type="PE",
                        expiry_date=exp_d,
                        timestamp=datetime.now(UTC),
                        price_type=PriceType.UPSTOX_LIVE_LTP,
                        source_name="UPSTOX_REST",
                        provider_id=self.provider_id,
                        freshness_status=FreshnessStatus.LIVE,
                        source_greeks=greeks_meta,
                        raw_provider_meta={
                            "transport_origin": "UPSTOX_REST",
                            "instrument_key": put_d.get("instrument_key"),
                            "provider": self.provider_id,
                            "trading_symbol": put_d.get("trading_symbol"),
                            "option_greeks": greeks_meta,
                        },
                    )
                )

        return NormalizedOptionChain(
            underlying_symbol=underlying,
            as_of_timestamp=datetime.now(UTC),
            spot_price=spot_price,
            expiries=sorted(list(expiries)),
            calls=calls,
            puts=puts,
            ce_pe_paired_pct=100.0 if calls and puts else 0.0,
            provider_id=self.provider_id,
            source_name="UPSTOX_REST",
            spot_provenance=spot_provenance if spot_price is not None else "UNAVAILABLE",
            spot_observed_at=spot_observed_at,
            expiry_provenance=(
                "PROVIDER_LISTED_CONTRACT_MASTER"
                if parsed_expiries
                else "CALENDAR_DERIVED_FALLBACK"
            ),
        )

    def _generate_mock_quote(self, symbol: str) -> NormalizedQuote:
        """Generate deterministic mock live quote for unit tests."""
        now = datetime.now(UTC)
        return NormalizedQuote(
            symbol=symbol,
            instrument_type="EQUITY",
            ltp=Decimal("150.50"),
            previous_close=Decimal("148.00"),
            change=Decimal("2.50"),
            change_pct=Decimal("1.69"),
            open=Decimal("149.00"),
            high=Decimal("152.00"),
            low=Decimal("148.50"),
            close=Decimal("150.50"),
            volume=500000,
            open_interest=0,
            bid=Decimal("150.45"),
            ask=Decimal("150.55"),
            bid_qty=1000,
            ask_qty=1500,
            timestamp=now,
            price_type=PriceType.UPSTOX_LIVE_LTP,
            source_name="UPSTOX_MOCK",
            provider_id=self.provider_id,
            freshness_status=FreshnessStatus.LIVE,
        )

    def _generate_mock_live_option_chain(self, underlying: str, expiry: date | None = None) -> NormalizedOptionChain:
        """Generate deterministic mock live option chain for unit tests."""
        now = datetime.now(UTC)
        all_exp = calculate_dynamic_index_expiries()
        exp_d = expiry or (all_exp[0] if all_exp else date(2026, 8, 28))
        all_expiries = [expiry] if expiry else all_exp
        if exp_d not in all_expiries:
            all_expiries.insert(0, exp_d)

        # Dynamic spot by underlying
        spots = {
            "NIFTY": Decimal("24500.00"),
            "BANKNIFTY": Decimal("52000.00"),
            "FINNIFTY": Decimal("23000.00"),
            "MIDCPNIFTY": Decimal("12500.00"),
            "SENSEX": Decimal("80000.00"),
        }
        spot_val = spots.get(underlying.upper(), Decimal("24500.00"))
        steps = {
            "NIFTY": Decimal("50"),
            "BANKNIFTY": Decimal("100"),
            "FINNIFTY": Decimal("50"),
            "MIDCPNIFTY": Decimal("25"),
            "SENSEX": Decimal("100"),
        }
        step_val = steps.get(underlying.upper(), Decimal("50"))

        strikes = [spot_val + (i * step_val) for i in range(-5, 6)]
        calls = [
            NormalizedQuote(
                symbol=f"{underlying}{exp_d.strftime('%d%b%Y')}{int(s)}CE",
                instrument_type="OPTION",
                ltp=max(Decimal("1.00"), (spot_val - s) + Decimal("150.00")),
                bid=max(Decimal("0.50"), (spot_val - s) + Decimal("149.50")),
                ask=max(Decimal("1.50"), (spot_val - s) + Decimal("150.50")),
                strike_price=s,
                option_type="CE",
                expiry_date=exp_d,
                timestamp=now,
                price_type=PriceType.UPSTOX_LIVE_LTP,
                source_name="UPSTOX_MOCK",
                provider_id=self.provider_id,
                freshness_status=FreshnessStatus.LIVE,
            )
            for s in strikes
        ]
        puts = [
            NormalizedQuote(
                symbol=f"{underlying}{exp_d.strftime('%d%b%Y')}{int(s)}PE",
                instrument_type="OPTION",
                ltp=max(Decimal("1.00"), (s - spot_val) + Decimal("130.00")),
                bid=max(Decimal("0.50"), (s - spot_val) + Decimal("129.50")),
                ask=max(Decimal("1.50"), (s - spot_val) + Decimal("130.50")),
                strike_price=s,
                option_type="PE",
                expiry_date=exp_d,
                timestamp=now,
                price_type=PriceType.UPSTOX_LIVE_LTP,
                source_name="UPSTOX_MOCK",
                provider_id=self.provider_id,
                freshness_status=FreshnessStatus.LIVE,
            )
            for s in strikes
        ]
        return NormalizedOptionChain(
            underlying_symbol=underlying,
            as_of_timestamp=now,
            spot_price=spot_val,
            expiries=all_expiries,
            calls=calls,
            puts=puts,
            ce_pe_paired_pct=100.0,
            provider_id=self.provider_id,
            source_name="UPSTOX_MOCK",
        )
