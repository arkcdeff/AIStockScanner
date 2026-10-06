"""Derivatives Real Observation Capture Service — Milestone G.11-OPT-4.

Provides an operator-controlled, bounded framework for collecting and retaining
genuine market observations (spot, option contracts, futures contracts, interval
OI/price changes, capture-health metadata) to build historical depth.

CRITICAL DESIGN & SAFETY RULES:
  1. Disabled by default; requires explicit operator activation.
  2. Read-only analytics isolation: ZERO broker orders or trading execution.
  3. ZERO fabricated or synthetic market observations (NIFTY-FUT is strictly prohibited).
  4. Never label calendar expiries or models as provider-supplied live observations.
  5. Bounded session windows: respects market session boundaries (09:15 to 15:30 IST)
     and max duration.
  6. Rate-limited execution (<= 5 req/s) with bounded retries and exponential backoff.
  7. Bounded storage limits (default 500 MB) with clean stop on overflow.
  8. Deduplication across (instrument_key, timestamp) for both options and futures.
  9. Secret redaction on all logging, status, and telemetry.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Any

from app.core.config import settings
from app.providers.upstox_historical_provider import redact_secrets
from app.services.derivatives_observation_service import (
    FuturesObservation,
    FuturesObservationStore,
    format_ist,
)
from app.services.derivatives_scope import SUPPORTED_UNDERLYINGS

logger = logging.getLogger(__name__)

IST_OFFSET = timedelta(hours=5, minutes=30)

# G.11-OPT-5 (D1): chain source names that must never reach the real observation
# store. The provider returns a deterministic mock chain for explicitly-mocked
# tests and an UNAVAILABLE chain for provider outages; neither is a market
# observation.
_FORBIDDEN_CHAIN_SOURCES = {"UPSTOX_MOCK", "UNAVAILABLE", "MOCK", "TEST", "UNKNOWN"}


def _chain_is_real_observation(chain: Any) -> tuple[bool, str]:
    """Decide whether a fetched chain is a real provider observation.

    G.11-OPT-5 (defect D1): a synthetic or unavailable chain must never be
    persisted into the real snapshot tables under a live-sounding label.
    Returns ``(is_real, reject_reason)``.
    """
    source_name = str(getattr(chain, "source_name", "") or "").upper()
    if source_name in _FORBIDDEN_CHAIN_SOURCES:
        return False, f"NON_REAL_CHAIN_SOURCE_{source_name}"
    if "MOCK" in source_name or "TEST" in source_name or "UNAVAILABLE" in source_name:
        return False, f"NON_REAL_CHAIN_SOURCE_{source_name}"
    spot_provenance = str(getattr(chain, "spot_provenance", "") or "").upper()
    if spot_provenance in {"PROVIDER_ERROR", "UNAVAILABLE"}:
        return False, "CHAIN_NOT_A_REAL_OBSERVATION"
    quotes = list(getattr(chain, "calls", []) or []) + list(getattr(chain, "puts", []) or [])
    if not quotes:
        return False, "CHAIN_HAS_NO_QUOTES"
    return True, ""


class CaptureState(StrEnum):
    DISABLED = "DISABLED"
    IDLE = "IDLE"
    RUNNING = "RUNNING"
    STOPPED = "STOPPED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


@dataclass
class DerivativesCaptureConfig:
    """Validated configuration for an operator-controlled capture session."""

    underlying: str = "NIFTY"
    expiry: date | None = None
    strike_count: int = 10
    interval_seconds: int = 60
    start_time_ist: time = time(9, 15)
    end_time_ist: time = time(15, 30)
    max_duration_minutes: int = 375
    max_contracts: int = 100
    max_storage_bytes: int = 500 * 1024 * 1024  # 500 MB
    rate_limit_rps: float = 5.0
    allow_outside_market_hours: bool = False  # Strict default False; True only for authorized test sessions

    def validate(self) -> list[str]:
        errors: list[str] = []
        u_clean = self.underlying.strip().upper()
        if u_clean not in SUPPORTED_UNDERLYINGS:
            errors.append(
                f"Underlying '{self.underlying}' not supported. Must be one of {sorted(SUPPORTED_UNDERLYINGS)}"
            )
        if self.interval_seconds < 10 or self.interval_seconds > 3600:
            errors.append("interval_seconds must be between 10 and 3600 seconds")
        if self.max_duration_minutes < 1 or self.max_duration_minutes > 480:
            errors.append("max_duration_minutes must be between 1 and 480 minutes")
        if self.strike_count < 1 or self.strike_count > 50:
            errors.append("strike_count must be between 1 and 50 per side")
        if self.max_contracts < 2 or self.max_contracts > 200:
            errors.append("max_contracts must be between 2 and 200")
        if self.max_storage_bytes < 10 * 1024 * 1024:
            errors.append("max_storage_bytes must be at least 10 MB")
        return errors


@dataclass
class CaptureTelemetry:
    """Runtime metrics and health tracking for a capture session."""

    session_id: str = ""
    state: CaptureState = CaptureState.IDLE
    underlying: str = "NIFTY"
    expiry: str | None = None
    started_at_utc: str | None = None
    started_at_ist: str | None = None
    stopped_at_utc: str | None = None
    stopped_at_ist: str | None = None
    duration_seconds: float = 0.0
    requests_total: int = 0
    requests_successful: int = 0
    requests_failed: int = 0
    observations_captured: int = 0
    bytes_written: int = 0
    last_observation_utc: str | None = None
    last_observation_ist: str | None = None
    last_error: str | None = None
    storage_used_bytes: int = 0
    storage_limit_bytes: int = 500 * 1024 * 1024
    provider_id: str = "UPSTOX_LIVE"
    provider_status: str = "READY"
    is_authorized: bool = False
    is_running: bool = False

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["state"] = self.state.value if isinstance(self.state, CaptureState) else str(self.state)
        return data


class DerivativesCaptureService:
    """Singleton service orchestrating real observation capture sessions."""

    _instance: DerivativesCaptureService | None = None

    def __init__(self) -> None:
        self.futures_store = FuturesObservationStore()
        self._active_task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._lock = asyncio.Lock()
        self._current_config: DerivativesCaptureConfig = DerivativesCaptureConfig()
        self._telemetry = CaptureTelemetry(
            storage_limit_bytes=getattr(
                settings, "derivatives_capture_max_storage_bytes", 500 * 1024 * 1024
            )
        )
        self._refresh_storage_usage()

    @classmethod
    def get_instance(cls) -> DerivativesCaptureService:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @property
    def is_globally_enabled(self) -> bool:
        """Global configuration flag; strictly disabled by default."""
        return bool(getattr(settings, "derivatives_observation_capture_enabled", False))

    def _refresh_storage_usage(self) -> int:
        used = self.futures_store.get_storage_usage()
        self._telemetry.storage_used_bytes = used
        return used

    def get_status(self) -> dict[str, Any]:
        """Read-only capture status and health metrics."""
        self._refresh_storage_usage()
        status_dict = self._telemetry.to_dict()
        status_dict["globally_enabled"] = self.is_globally_enabled
        status_dict["active_task_running"] = (
            self._active_task is not None and not self._active_task.done()
        )
        # G.11-OPT-5 (D2): after an application restart the in-memory telemetry
        # resets to IDLE with zeroed counters while previously written JSONL
        # observations remain on disk. Derive the durable totals from the store
        # so status reflects disk truth; the worker itself never survives a
        # restart (state stays IDLE unless a task is actually running).
        try:
            disk = self.futures_store.summarize_history()
        except Exception as exc:  # noqa: BLE001 - status must never raise
            logger.warning("Could not summarize observation history: %s", type(exc).__name__)
            disk = {}
        if disk and (disk.get("total_observations") or disk.get("malformed_records")):
            status_dict["disk_history"] = disk
            if not status_dict.get("is_running"):
                status_dict["observations_on_disk"] = disk.get("total_observations")
                status_dict["last_observation_on_disk_utc"] = disk.get("last_observation_utc")
                status_dict["last_observation_on_disk_ist"] = disk.get("last_observation_ist")
                status_dict["malformed_records_on_disk"] = disk.get("malformed_records", 0)
        return status_dict

    async def start_capture(
        self,
        config: DerivativesCaptureConfig,
        *,
        authorized: bool = False,
        operator_username: str | None = None,
    ) -> tuple[bool, str, dict[str, Any]]:
        """Start a bounded, operator-controlled capture session.

        Requires explicit operator authorization (`authorized=True`).
        """
        if not authorized:
            return False, "AUTHORIZATION_REQUIRED: Operator permissions required to start capture", {}

        validation_errors = config.validate()
        if validation_errors:
            return False, f"INVALID_CONFIG: {'; '.join(validation_errors)}", {}

        async with self._lock:
            if self._active_task is not None and not self._active_task.done():
                return False, "CAPTURE_ALREADY_RUNNING: An observation capture session is already active", self.get_status()

            # Verify storage limit before starting
            used_bytes = self._refresh_storage_usage()
            if used_bytes >= config.max_storage_bytes:
                return (
                    False,
                    f"STORAGE_LIMIT_EXCEEDED: Storage usage ({used_bytes:,} bytes) exceeds limit ({config.max_storage_bytes:,} bytes)",
                    self.get_status(),
                )

            # Check market session window
            now_utc = datetime.now(UTC)
            now_ist = now_utc + IST_OFFSET
            current_time_ist = now_ist.time()

            if not config.allow_outside_market_hours:
                # Market bounds 09:15 to 15:30 IST on weekdays
                if now_ist.weekday() >= 5:
                    return False, "MARKET_CLOSED: Weekend capture requires allow_outside_market_hours=True", {}
                if current_time_ist < config.start_time_ist or current_time_ist > config.end_time_ist:
                    return (
                        False,
                        f"OUTSIDE_MARKET_HOURS: Current time ({current_time_ist.strftime('%H:%M:%S')} IST) outside bounds ({config.start_time_ist.strftime('%H:%M')} - {config.end_time_ist.strftime('%H:%M')} IST)",
                        {},
                    )

            session_id = f"cap_{uuid.uuid4().hex[:12]}"
            self._stop_event.clear()
            self._current_config = config

            self._telemetry = CaptureTelemetry(
                session_id=session_id,
                state=CaptureState.RUNNING,
                underlying=config.underlying.upper(),
                expiry=config.expiry.isoformat() if config.expiry else None,
                started_at_utc=now_utc.isoformat(),
                started_at_ist=format_ist(now_utc, with_date=True),
                storage_limit_bytes=config.max_storage_bytes,
                storage_used_bytes=used_bytes,
                is_authorized=True,
                is_running=True,
            )

            logger.info(
                redact_secrets(
                    f"Starting authorized observation capture [{session_id}] for {config.underlying} by operator {operator_username or 'unknown'}"
                )
            )

            self._active_task = asyncio.create_task(
                self._run_capture_loop(session_id, config)
            )

            return True, "CAPTURE_STARTED", self.get_status()

    async def stop_capture(
        self,
        *,
        authorized: bool = False,
        operator_username: str | None = None,
        reason: str = "OPERATOR_STOPPED",
    ) -> tuple[bool, str, dict[str, Any]]:
        """Gracefully stop the active capture session."""
        if not authorized:
            return False, "AUTHORIZATION_REQUIRED: Operator permissions required to stop capture", {}

        async with self._lock:
            if self._active_task is None or self._active_task.done():
                self._telemetry.state = CaptureState.STOPPED
                self._telemetry.is_running = False
                return True, "CAPTURE_NOT_ACTIVE", self.get_status()

            logger.info(
                redact_secrets(
                    f"Stopping capture session [{self._telemetry.session_id}] by {operator_username or 'operator'}: {reason}"
                )
            )
            self._stop_event.set()

            try:
                await asyncio.wait_for(asyncio.shield(self._active_task), timeout=5.0)
            except TimeoutError:
                logger.warning("Active capture task did not terminate within 5s; cancelling")
                self._active_task.cancel()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Error waiting for capture task stop: %s", exc)

            now_utc = datetime.now(UTC)
            self._telemetry.state = CaptureState.STOPPED
            self._telemetry.stopped_at_utc = now_utc.isoformat()
            self._telemetry.stopped_at_ist = format_ist(now_utc, with_date=True)
            self._telemetry.is_running = False
            self._refresh_storage_usage()

            return True, f"CAPTURE_STOPPED: {reason}", self.get_status()

    async def cleanup_storage(
        self,
        max_age_days: int = 7,
        *,
        authorized: bool = False,
    ) -> tuple[bool, str, dict[str, Any]]:
        """Operator-authorized cleanup of aged observations."""
        if not authorized:
            return False, "AUTHORIZATION_REQUIRED", {}
        result = self.futures_store.cleanup_older_than(max_age_days)
        self._refresh_storage_usage()
        return True, "CLEANUP_COMPLETED", result

    async def _run_capture_loop(
        self,
        session_id: str,
        config: DerivativesCaptureConfig,
    ) -> None:
        """Internal bounded worker loop with rate limiting, error isolation, and persistence."""
        from app.providers.upstox_live_provider import UpstoxLiveProvider
        from app.services.intraday_options_service import IntradayOptionsService

        provider = UpstoxLiveProvider()
        service = IntradayOptionsService()

        start_time = datetime.now(UTC)
        max_duration = timedelta(minutes=config.max_duration_minutes)
        min_sleep = max(0.2, 1.0 / config.rate_limit_rps)  # Respect rate limit

        consecutive_failures = 0
        max_consecutive_failures = 5

        try:
            while not self._stop_event.is_set():
                now_utc = datetime.now(UTC)
                elapsed = now_utc - start_time
                self._telemetry.duration_seconds = elapsed.total_seconds()

                # Check max duration
                if elapsed >= max_duration:
                    logger.info("Capture session [%s] reached max duration %s", session_id, max_duration)
                    self._telemetry.state = CaptureState.COMPLETED
                    break

                # Check market hours
                now_ist = now_utc + IST_OFFSET
                current_time_ist = now_ist.time()
                if not config.allow_outside_market_hours:
                    if current_time_ist > config.end_time_ist:
                        logger.info("Capture session [%s] reached market close (%s IST)", session_id, config.end_time_ist)
                        self._telemetry.state = CaptureState.COMPLETED
                        break

                # Check storage limits
                used_bytes = self._refresh_storage_usage()
                if used_bytes >= config.max_storage_bytes:
                    logger.warning("Capture session [%s] reached storage cap (%s bytes)", session_id, config.max_storage_bytes)
                    self._telemetry.state = CaptureState.STOPPED
                    self._telemetry.last_error = "STORAGE_CAP_REACHED"
                    break

                cycle_success = await self._capture_cycle(provider, service, config)

                if cycle_success:
                    consecutive_failures = 0
                    self._telemetry.requests_successful += 1
                else:
                    consecutive_failures += 1
                    self._telemetry.requests_failed += 1
                    if consecutive_failures >= max_consecutive_failures:
                        logger.error(
                            redact_secrets(
                                f"Capture session [{session_id}] encountered {consecutive_failures} consecutive failures; halting"
                            )
                        )
                        self._telemetry.state = CaptureState.FAILED
                        self._telemetry.last_error = f"CONSECUTIVE_FAILURES_{consecutive_failures}"
                        break

                # Sleep until next scheduled interval or stop event
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=max(min_sleep, float(config.interval_seconds)),
                    )
                except TimeoutError:
                    pass

        except asyncio.CancelledError:
            logger.info("Capture session [%s] cancelled", session_id)
            self._telemetry.state = CaptureState.STOPPED
        except Exception as exc:  # noqa: BLE001
            logger.error(redact_secrets(f"Capture session [{session_id}] failed: {exc}"))
            self._telemetry.state = CaptureState.FAILED
            self._telemetry.last_error = str(type(exc).__name__)
        finally:
            now_final = datetime.now(UTC)
            self._telemetry.stopped_at_utc = now_final.isoformat()
            self._telemetry.stopped_at_ist = format_ist(now_final, with_date=True)
            self._telemetry.is_running = False
            self._refresh_storage_usage()

    async def _capture_cycle(
        self,
        provider: Any,
        service: Any,
        config: DerivativesCaptureConfig,
    ) -> bool:
        """Perform one discrete capture cycle for options and futures."""
        u_clean = config.underlying.strip().upper()
        self._telemetry.requests_total += 1

        try:
            # 1. Resolve scope & real index spot
            scope = await service.get_scope(u_clean, config.expiry)
            if not scope or not scope.get("spot_price"):
                logger.warning("Capture cycle: unable to resolve spot price for %s", u_clean)
                self._telemetry.last_error = "UNABLE_TO_RESOLVE_SPOT"
                return False

            spot_price = Decimal(str(scope["spot_price"]))
            target_expiry = config.expiry
            if not target_expiry and scope.get("selected_expiry"):
                try:
                    target_expiry = date.fromisoformat(scope["selected_expiry"])
                except Exception:
                    pass

            # 2. Capture and persist the Options Chain snapshot only when it is a
            # real provider observation. G.11-OPT-5 (D1): mock or unavailable
            # chains are rejected here, and the chain's own source_name is kept
            # (the OPT-1 persistence layer stamps the caller's label over it
            # otherwise, which could disguise a synthetic chain as captured).
            opt_chain = await provider.get_live_option_chain(u_clean, expiry_date=target_expiry)
            is_real, reject_reason = _chain_is_real_observation(opt_chain)
            if not is_real:
                logger.warning(
                    redact_secrets(
                        f"Capture cycle: rejecting non-real chain for {u_clean} ({reject_reason})"
                    )
                )
                self._telemetry.last_error = reject_reason
                return False

            from app.services.option_chain_persistence_service import (
                OptionChainPersistenceService,
            )

            persistence = OptionChainPersistenceService()
            persist_result = await persistence.persist_snapshot(
                opt_chain,
                underlying_index_id=None,
            )
            if persist_result.status in {"PERSISTED", "DUPLICATE_IGNORED"}:
                if not persist_result.is_duplicate:
                    self._telemetry.observations_captured += len(opt_chain.calls) + len(
                        opt_chain.puts
                    )
            else:
                self._telemetry.last_error = f"PERSIST_REJECTED_{persist_result.quality_status}"

            # 3. Capture and persist Futures Quote using real instrument key from contract master
            from app.services.derivatives_scope import DerivativesContractMasterClient

            master_client = DerivativesContractMasterClient()
            futures_result = await master_client.get_futures_contracts(u_clean)
            if futures_result.is_available and futures_result.rows:
                contract = futures_result.rows[0]
                fut_quote = await provider.get_futures_quote(contract.instrument_key)
                if fut_quote:
                    obs_time = fut_quote.get("timestamp") or datetime.now(UTC)
                    fut_obs = FuturesObservation(
                        instrument_key=contract.instrument_key,
                        trading_symbol=contract.trading_symbol,
                        underlying=u_clean,
                        expiry=contract.expiry_date,
                        observed_at=obs_time,
                        received_at=datetime.now(UTC),
                        source="UPSTOX_LIVE_CAPTURE",
                        last_price=(
                            Decimal(str(fut_quote["last_price"]))
                            if fut_quote.get("last_price") is not None
                            else None
                        ),
                        previous_close=(
                            Decimal(str(fut_quote["previous_close"]))
                            if fut_quote.get("previous_close") is not None
                            else None
                        ),
                        day_open=(
                            Decimal(str(fut_quote["day_open"]))
                            if fut_quote.get("day_open") is not None
                            else None
                        ),
                        day_high=(
                            Decimal(str(fut_quote["day_high"]))
                            if fut_quote.get("day_high") is not None
                            else None
                        ),
                        day_low=(
                            Decimal(str(fut_quote["day_low"]))
                            if fut_quote.get("day_low") is not None
                            else None
                        ),
                        volume=fut_quote.get("volume"),
                        open_interest=fut_quote.get("open_interest"),
                        oi_day_high=fut_quote.get("oi_day_high"),
                        oi_day_low=fut_quote.get("oi_day_low"),
                        net_change=(
                            Decimal(str(fut_quote["net_change"]))
                            if fut_quote.get("net_change") is not None
                            else None
                        ),
                        total_buy_quantity=fut_quote.get("total_buy_quantity"),
                        total_sell_quantity=fut_quote.get("total_sell_quantity"),
                        underlying_spot=spot_price,
                        underlying_spot_observed_at=obs_time,
                        lot_size=contract.lot_size,
                    )
                    # Persist to append-only store with force_enabled=True because this session is explicitly operator-authorized
                    # G.11-OPT-5 (D3): only genuine appends count as observations;
                    # duplicates are idempotent successes but not new data.
                    appended, duplicate = self.futures_store.append_with_result(
                        fut_obs, force_enabled=True
                    )
                    if appended and not duplicate:
                        self._telemetry.observations_captured += 1

            now_obs = datetime.now(UTC)
            self._telemetry.last_observation_utc = now_obs.isoformat()
            self._telemetry.last_observation_ist = format_ist(now_obs)
            return True

        except Exception as exc:  # noqa: BLE001
            logger.warning(redact_secrets(f"Capture cycle error for {u_clean}: {exc}"))
            self._telemetry.last_error = str(type(exc).__name__)
            return False


def get_capture_service() -> DerivativesCaptureService:
    """Dependency provider returning singleton capture service."""
    return DerivativesCaptureService.get_instance()
