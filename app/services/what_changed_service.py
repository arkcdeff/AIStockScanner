"""What Changed Service — G.11-D-1 Foundational Deterministic Change Engine."""
from __future__ import annotations

from collections import Counter
from datetime import UTC, date, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bulk_block_deal import BulkBlockDeal
from app.models.corporate_action import CorporateAction
from app.models.daily_price import DailyPrice
from app.models.fundamental_corporate import CorporateAnnouncement
from app.models.indicator_result import IndicatorResult
from app.models.institutional_flow import InstitutionalFlow
from app.models.scan_definition import ScanDefinition
from app.models.scan_result import ScanResult
from app.repositories.what_changed_repository import WhatChangedRepository
from app.schemas.what_changed import (
    ChangeEvent,
    ChangeSignificance,
    ChangeType,
    WhatChangedResult,
)
from app.services.exchange_calendar_service import ExchangeCalendarService
from app.services.what_changed_config import (
    ADX_TREND_THRESHOLD,
    DEAL_VALUE_MAJOR_CRORE,
    DEFAULT_COMPARISON_WINDOW,
    DELIVERY_PCT_POINT_JUMP_THRESHOLD,
    DOMAIN_SORT_ORDER,
    INSTITUTIONAL_NET_FLOW_SIGNIFICANT_CRORE,
    PRICE_CHANGE_MAJOR_PCT,
    PRICE_CHANGE_NORMAL_PCT,
    PRICE_CHANGE_SIGNIFICANT_PCT,
    RSI_OVERBOUGHT_THRESHOLD,
    RSI_OVERSOLD_THRESHOLD,
    RSI_SIGNIFICANT_POINT_CHANGE,
    SIGNIFICANCE_SORT_ORDER,
    SUPPORTED_COMPARISON_WINDOWS,
    VOLUME_CHANGE_MAJOR_PCT,
    VOLUME_CHANGE_NORMAL_PCT,
    VOLUME_CHANGE_SIGNIFICANT_PCT,
)


class WhatChangedService:
    """Foundational deterministic change detection engine across historical sessions."""

    def __init__(
        self,
        session: AsyncSession,
        repository: WhatChangedRepository | None = None,
        calendar: ExchangeCalendarService | None = None,
    ) -> None:
        self.session = session
        self.repository = repository or WhatChangedRepository(session)
        self.calendar = calendar or ExchangeCalendarService()

    async def detect_changes(
        self,
        symbol: str,
        evaluation_date: date | None = None,
        comparison_window: int = DEFAULT_COMPARISON_WINDOW,
    ) -> WhatChangedResult:
        """Detect and classify deterministic changes for symbol between evaluation and comparison dates."""
        clean_symbol = symbol.upper().strip()
        stock = await self.repository.get_stock_by_symbol(clean_symbol)
        if not stock:
            raise ValueError(f"Stock symbol '{symbol}' not found")

        # 1. Resolve evaluation date
        if evaluation_date is None:
            latest_date = await self.repository.get_latest_price_date(stock.id)
            if latest_date is None:
                raise ValueError(f"No trading price history found for symbol '{clean_symbol}'")
            eval_d = latest_date
        else:
            eval_d = evaluation_date

        # 2. Validate comparison window
        if comparison_window not in SUPPORTED_COMPARISON_WINDOWS:
            raise ValueError(
                f"Unsupported comparison window {comparison_window}. Supported: {SUPPORTED_COMPARISON_WINDOWS}"
            )

        # 3. Resolve comparison date using trading session continuity
        prev_dates = await self.repository.get_previous_trading_dates(
            stock.id, strictly_before=eval_d, limit=comparison_window
        )
        if len(prev_dates) >= comparison_window:
            comp_d = prev_dates[comparison_window - 1]
        else:
            comp_d = self.calendar.nth_exchange_session_before(eval_d, comparison_window)

        # 4. Query domain facts for both dates (PIT-safe)
        curr_price = await self.repository.get_daily_price(stock.id, eval_d)
        prev_price = await self.repository.get_daily_price(stock.id, comp_d)

        curr_ind = await self.repository.get_indicator_result(stock.id, eval_d)
        prev_ind = await self.repository.get_indicator_result(stock.id, comp_d)

        curr_scans = await self.repository.get_scan_results(stock.id, eval_d)
        prev_scans = await self.repository.get_scan_results(stock.id, comp_d)

        deals = await self.repository.get_bulk_block_deals(
            clean_symbol, start_date=comp_d, end_date=eval_d, pit_cutoff=eval_d
        )
        announcements = await self.repository.get_corporate_announcements(
            stock.id, start_date=comp_d, end_date=eval_d, pit_cutoff=eval_d
        )
        actions = await self.repository.get_corporate_actions(
            stock.id, start_date=comp_d, end_date=eval_d, pit_cutoff=eval_d
        )

        curr_flows = await self.repository.get_institutional_flows(eval_d)
        prev_flows = await self.repository.get_institutional_flows(comp_d)

        # 5. Evaluate changes across each domain
        changes: list[ChangeEvent] = []
        changes.extend(self._evaluate_price(clean_symbol, eval_d, comp_d, prev_price, curr_price))
        changes.extend(self._evaluate_volume(clean_symbol, eval_d, comp_d, prev_price, curr_price))
        changes.extend(self._evaluate_delivery(clean_symbol, eval_d, comp_d, prev_price, curr_price))
        changes.extend(self._evaluate_technical_indicators(clean_symbol, eval_d, comp_d, prev_ind, curr_ind))
        changes.extend(self._evaluate_trend_and_regime(clean_symbol, eval_d, comp_d, prev_price, curr_price, prev_ind, curr_ind))
        changes.extend(self._evaluate_scanners(clean_symbol, eval_d, comp_d, prev_scans, curr_scans))
        changes.extend(self._evaluate_deals(clean_symbol, eval_d, comp_d, deals))
        changes.extend(self._evaluate_corporate_events(clean_symbol, eval_d, comp_d, actions, announcements))
        changes.extend(self._evaluate_institutional(clean_symbol, eval_d, comp_d, prev_flows, curr_flows))

        # 6. Sort changes deterministically: MAJOR -> SIGNIFICANT -> NORMAL -> MINOR, then domain, metric
        changes = self._sort_events(changes)

        # 7. Construct data quality summary & freshness metadata
        freshness = {
            "price_evaluation_date": curr_price.trade_date.isoformat() if curr_price else None,
            "price_comparison_date": prev_price.trade_date.isoformat() if prev_price else None,
            "indicator_evaluation_date": curr_ind.trade_date.isoformat() if curr_ind else None,
            "indicator_comparison_date": prev_ind.trade_date.isoformat() if prev_ind else None,
            "scanner_evaluation_date": curr_scans[0][0].scan_date.isoformat() if curr_scans else None,
            "scanner_comparison_date": prev_scans[0][0].scan_date.isoformat() if prev_scans else None,
            "indicators_available_for_eval": curr_ind is not None,
            "scanners_available_for_eval": len(curr_scans) > 0,
        }

        dq_summary = {
            "price_continuity": curr_price is not None and prev_price is not None,
            "indicator_continuity": curr_ind is not None and prev_ind is not None,
            "pit_safe": True,
            "missing_domains": [
                domain
                for domain, present in [
                    ("PRICE", curr_price is not None and prev_price is not None),
                    ("TECHNICAL", curr_ind is not None and prev_ind is not None),
                    ("SCANNER", len(curr_scans) > 0 or len(prev_scans) > 0),
                ]
                if not present
            ],
        }

        domain_counts = Counter(c.domain for c in changes)

        return WhatChangedResult(
            symbol=clean_symbol,
            evaluation_date=eval_d,
            comparison_date=comp_d,
            comparison_window_sessions=comparison_window,
            generated_at=datetime.now(UTC),
            pit_safe=True,
            total_changes=len(changes),
            major_changes=sum(1 for c in changes if c.significance == ChangeSignificance.MAJOR),
            significant_changes=sum(1 for c in changes if c.significance == ChangeSignificance.SIGNIFICANT),
            normal_changes=sum(1 for c in changes if c.significance == ChangeSignificance.NORMAL),
            minor_changes=sum(1 for c in changes if c.significance == ChangeSignificance.MINOR),
            data_quality_summary=dq_summary,
            domain_summaries=dict(domain_counts),
            freshness=freshness,
            changes=changes,
        )

    def _sort_events(self, changes: list[ChangeEvent]) -> list[ChangeEvent]:
        """Sort changes deterministically: MAJOR -> SIGNIFICANT -> NORMAL -> MINOR, then domain, metric, reason."""
        sorted_changes = list(changes)
        sorted_changes.sort(
            key=lambda c: (
                SIGNIFICANCE_SORT_ORDER.get(c.significance, 99),
                DOMAIN_SORT_ORDER.get(c.domain, 99),
                c.metric,
                c.reason,
            )
        )
        return sorted_changes

    # -----------------------------------------------------------------------
    # Domain-Specific Evaluation Helpers
    # -----------------------------------------------------------------------

    def _evaluate_price(
        self,
        symbol: str,
        eval_d: date,
        comp_d: date,
        prev: DailyPrice | None,
        curr: DailyPrice | None,
    ) -> list[ChangeEvent]:
        events: list[ChangeEvent] = []
        if prev is None and curr is not None:
            events.append(
                ChangeEvent(
                    symbol=symbol,
                    evaluation_date=eval_d,
                    comparison_date=comp_d,
                    domain="PRICE",
                    metric="close_price",
                    previous_value=None,
                    current_value=float(curr.close),
                    change_type=ChangeType.DATA_BECAME_AVAILABLE,
                    significance=ChangeSignificance.NORMAL,
                    source="NSE_DAILY_PRICE",
                    reason=f"Price data became available at {float(curr.close):.2f}",
                )
            )
            return events

        if prev is not None and curr is None:
            events.append(
                ChangeEvent(
                    symbol=symbol,
                    evaluation_date=eval_d,
                    comparison_date=comp_d,
                    domain="PRICE",
                    metric="close_price",
                    previous_value=float(prev.close),
                    current_value=None,
                    change_type=ChangeType.DATA_BECAME_UNAVAILABLE,
                    significance=ChangeSignificance.NORMAL,
                    source="NSE_DAILY_PRICE",
                    reason="Price data became unavailable for current session",
                )
            )
            return events

        if prev is None or curr is None:
            return events

        prev_close = float(prev.close)
        curr_close = float(curr.close)
        if prev_close <= 0:
            return events

        abs_diff = curr_close - prev_close
        pct_change = (abs_diff / prev_close) * 100.0

        if abs(pct_change) >= PRICE_CHANGE_NORMAL_PCT:
            if abs(pct_change) >= PRICE_CHANGE_MAJOR_PCT:
                sig = ChangeSignificance.MAJOR
            elif abs(pct_change) >= PRICE_CHANGE_SIGNIFICANT_PCT:
                sig = ChangeSignificance.SIGNIFICANT
            else:
                sig = ChangeSignificance.NORMAL

            chg_type = ChangeType.VALUE_INCREASE if pct_change > 0 else ChangeType.VALUE_DECREASE
            direction = "BULLISH" if pct_change > 0 else "BEARISH"
            reason = (
                f"Close price {'increased' if pct_change > 0 else 'decreased'} by "
                f"{abs(pct_change):.2f}% ({prev_close:.2f} -> {curr_close:.2f})"
            )

            events.append(
                ChangeEvent(
                    symbol=symbol,
                    evaluation_date=eval_d,
                    comparison_date=comp_d,
                    domain="PRICE",
                    metric="close_price",
                    previous_value=prev_close,
                    current_value=curr_close,
                    absolute_change=round(abs_diff, 4),
                    percentage_change=round(pct_change, 2),
                    change_type=chg_type,
                    significance=sig,
                    direction=direction,
                    source="NSE_DAILY_PRICE",
                    reason=reason,
                )
            )

        return events

    def _evaluate_volume(
        self,
        symbol: str,
        eval_d: date,
        comp_d: date,
        prev: DailyPrice | None,
        curr: DailyPrice | None,
    ) -> list[ChangeEvent]:
        events: list[ChangeEvent] = []
        if prev is None or curr is None:
            return events

        if prev.volume is None or curr.volume is None or prev.volume <= 0:
            return events

        prev_vol = int(prev.volume)
        curr_vol = int(curr.volume)
        abs_diff = curr_vol - prev_vol
        pct_change = (abs_diff / prev_vol) * 100.0

        if abs(pct_change) >= VOLUME_CHANGE_NORMAL_PCT:
            if pct_change >= VOLUME_CHANGE_MAJOR_PCT:
                sig = ChangeSignificance.MAJOR
            elif pct_change >= VOLUME_CHANGE_SIGNIFICANT_PCT:
                sig = ChangeSignificance.SIGNIFICANT
            else:
                sig = ChangeSignificance.NORMAL

            chg_type = ChangeType.VALUE_INCREASE if pct_change > 0 else ChangeType.VALUE_DECREASE
            reason = (
                f"Trading volume {'surged' if pct_change > 0 else 'dropped'} by "
                f"{abs(pct_change):.1f}% ({prev_vol:,} -> {curr_vol:,})"
            )

            events.append(
                ChangeEvent(
                    symbol=symbol,
                    evaluation_date=eval_d,
                    comparison_date=comp_d,
                    domain="VOLUME",
                    metric="volume",
                    previous_value=prev_vol,
                    current_value=curr_vol,
                    absolute_change=float(abs_diff),
                    percentage_change=round(pct_change, 1),
                    change_type=chg_type,
                    significance=sig,
                    source="NSE_DAILY_PRICE",
                    reason=reason,
                )
            )

        return events

    def _evaluate_delivery(
        self,
        symbol: str,
        eval_d: date,
        comp_d: date,
        prev: DailyPrice | None,
        curr: DailyPrice | None,
    ) -> list[ChangeEvent]:
        events: list[ChangeEvent] = []
        if prev is None or curr is None:
            return events

        p_deliv = float(prev.delivery_percentage) if prev.delivery_percentage is not None else None
        c_deliv = float(curr.delivery_percentage) if curr.delivery_percentage is not None else None

        if p_deliv is not None and c_deliv is None:
            events.append(
                ChangeEvent(
                    symbol=symbol,
                    evaluation_date=eval_d,
                    comparison_date=comp_d,
                    domain="DELIVERY",
                    metric="delivery_percentage",
                    previous_value=p_deliv,
                    current_value=None,
                    change_type=ChangeType.DATA_BECAME_UNAVAILABLE,
                    significance=ChangeSignificance.NORMAL,
                    source="NSE_DAILY_PRICE",
                    reason="Delivery percentage unavailable for current session",
                )
            )
            return events

        if p_deliv is None and c_deliv is not None:
            events.append(
                ChangeEvent(
                    symbol=symbol,
                    evaluation_date=eval_d,
                    comparison_date=comp_d,
                    domain="DELIVERY",
                    metric="delivery_percentage",
                    previous_value=None,
                    current_value=c_deliv,
                    change_type=ChangeType.DATA_BECAME_AVAILABLE,
                    significance=ChangeSignificance.NORMAL,
                    source="NSE_DAILY_PRICE",
                    reason=f"Delivery percentage became available at {c_deliv:.2f}%",
                )
            )
            return events

        if p_deliv is None or c_deliv is None:
            return events

        pt_diff = c_deliv - p_deliv
        if abs(pt_diff) >= DELIVERY_PCT_POINT_JUMP_THRESHOLD:
            chg_type = ChangeType.VALUE_INCREASE if pt_diff > 0 else ChangeType.VALUE_DECREASE
            events.append(
                ChangeEvent(
                    symbol=symbol,
                    evaluation_date=eval_d,
                    comparison_date=comp_d,
                    domain="DELIVERY",
                    metric="delivery_percentage",
                    previous_value=p_deliv,
                    current_value=c_deliv,
                    absolute_change=round(pt_diff, 2),
                    percentage_change=round((pt_diff / p_deliv) * 100.0, 1) if p_deliv > 0 else None,
                    change_type=chg_type,
                    significance=ChangeSignificance.SIGNIFICANT,
                    direction="BULLISH" if pt_diff > 0 else "NEUTRAL",
                    source="NSE_DAILY_PRICE",
                    reason=f"Delivery percentage shifted by {pt_diff:+.1f}% points ({p_deliv:.1f}% -> {c_deliv:.1f}%)",
                )
            )

        return events

    def _evaluate_technical_indicators(
        self,
        symbol: str,
        eval_d: date,
        comp_d: date,
        prev: IndicatorResult | None,
        curr: IndicatorResult | None,
    ) -> list[ChangeEvent]:
        events: list[ChangeEvent] = []
        if prev is None or curr is None:
            return events

        # RSI(14)
        if prev.rsi_14 is not None and curr.rsi_14 is not None:
            p_rsi = float(prev.rsi_14)
            c_rsi = float(curr.rsi_14)
            diff = c_rsi - p_rsi

            # Oversold transitions (threshold 30.0)
            if p_rsi > RSI_OVERSOLD_THRESHOLD and c_rsi <= RSI_OVERSOLD_THRESHOLD:
                events.append(
                    ChangeEvent(
                        symbol=symbol,
                        evaluation_date=eval_d,
                        comparison_date=comp_d,
                        domain="TECHNICAL",
                        metric="rsi_14",
                        previous_value=p_rsi,
                        current_value=c_rsi,
                        absolute_change=round(diff, 2),
                        previous_state="NORMAL",
                        current_state="OVERSOLD",
                        change_type=ChangeType.THRESHOLD_CROSSED,
                        significance=ChangeSignificance.SIGNIFICANT,
                        direction="BEARISH",
                        source="NSE_INDICATOR_RESULT",
                        reason=f"RSI(14) crossed below oversold threshold 30.0 ({p_rsi:.1f} -> {c_rsi:.1f})",
                    )
                )
            elif p_rsi <= RSI_OVERSOLD_THRESHOLD and c_rsi > RSI_OVERSOLD_THRESHOLD:
                events.append(
                    ChangeEvent(
                        symbol=symbol,
                        evaluation_date=eval_d,
                        comparison_date=comp_d,
                        domain="TECHNICAL",
                        metric="rsi_14",
                        previous_value=p_rsi,
                        current_value=c_rsi,
                        absolute_change=round(diff, 2),
                        previous_state="OVERSOLD",
                        current_state="NORMAL",
                        change_type=ChangeType.THRESHOLD_CROSSED,
                        significance=ChangeSignificance.SIGNIFICANT,
                        direction="BULLISH",
                        source="NSE_INDICATOR_RESULT",
                        reason=f"RSI(14) recovered above oversold threshold 30.0 ({p_rsi:.1f} -> {c_rsi:.1f})",
                    )
                )
            # Overbought transitions (threshold 70.0)
            elif p_rsi < RSI_OVERBOUGHT_THRESHOLD and c_rsi >= RSI_OVERBOUGHT_THRESHOLD:
                events.append(
                    ChangeEvent(
                        symbol=symbol,
                        evaluation_date=eval_d,
                        comparison_date=comp_d,
                        domain="TECHNICAL",
                        metric="rsi_14",
                        previous_value=p_rsi,
                        current_value=c_rsi,
                        absolute_change=round(diff, 2),
                        previous_state="NORMAL",
                        current_state="OVERBOUGHT",
                        change_type=ChangeType.THRESHOLD_CROSSED,
                        significance=ChangeSignificance.SIGNIFICANT,
                        direction="BULLISH",
                        source="NSE_INDICATOR_RESULT",
                        reason=f"RSI(14) crossed above overbought threshold 70.0 ({p_rsi:.1f} -> {c_rsi:.1f})",
                    )
                )
            elif p_rsi >= RSI_OVERBOUGHT_THRESHOLD and c_rsi < RSI_OVERBOUGHT_THRESHOLD:
                events.append(
                    ChangeEvent(
                        symbol=symbol,
                        evaluation_date=eval_d,
                        comparison_date=comp_d,
                        domain="TECHNICAL",
                        metric="rsi_14",
                        previous_value=p_rsi,
                        current_value=c_rsi,
                        absolute_change=round(diff, 2),
                        previous_state="OVERBOUGHT",
                        current_state="NORMAL",
                        change_type=ChangeType.THRESHOLD_CROSSED,
                        significance=ChangeSignificance.SIGNIFICANT,
                        direction="BEARISH",
                        source="NSE_INDICATOR_RESULT",
                        reason=f"RSI(14) fell back below overbought threshold 70.0 ({p_rsi:.1f} -> {c_rsi:.1f})",
                    )
                )
            elif abs(diff) >= RSI_SIGNIFICANT_POINT_CHANGE:
                events.append(
                    ChangeEvent(
                        symbol=symbol,
                        evaluation_date=eval_d,
                        comparison_date=comp_d,
                        domain="TECHNICAL",
                        metric="rsi_14",
                        previous_value=p_rsi,
                        current_value=c_rsi,
                        absolute_change=round(diff, 2),
                        change_type=ChangeType.VALUE_INCREASE if diff > 0 else ChangeType.VALUE_DECREASE,
                        significance=ChangeSignificance.NORMAL,
                        direction="BULLISH" if diff > 0 else "BEARISH",
                        source="NSE_INDICATOR_RESULT",
                        reason=f"RSI(14) shifted by {diff:+.1f} points ({p_rsi:.1f} -> {c_rsi:.1f})",
                    )
                )

        # ADX Trend Strength Regime
        if prev.adx is not None and curr.adx is not None:
            p_adx = float(prev.adx)
            c_adx = float(curr.adx)
            if p_adx < ADX_TREND_THRESHOLD and c_adx >= ADX_TREND_THRESHOLD:
                events.append(
                    ChangeEvent(
                        symbol=symbol,
                        evaluation_date=eval_d,
                        comparison_date=comp_d,
                        domain="TECHNICAL",
                        metric="adx_regime",
                        previous_value=p_adx,
                        current_value=c_adx,
                        previous_state="NON_TRENDING",
                        current_state="TRENDING",
                        change_type=ChangeType.STATE_CHANGED,
                        significance=ChangeSignificance.NORMAL,
                        source="NSE_INDICATOR_RESULT",
                        reason=f"ADX crossed above 25.0 ({p_adx:.1f} -> {c_adx:.1f}), signalling developing trend",
                    )
                )
            elif p_adx >= ADX_TREND_THRESHOLD and c_adx < ADX_TREND_THRESHOLD:
                events.append(
                    ChangeEvent(
                        symbol=symbol,
                        evaluation_date=eval_d,
                        comparison_date=comp_d,
                        domain="TECHNICAL",
                        metric="adx_regime",
                        previous_value=p_adx,
                        current_value=c_adx,
                        previous_state="TRENDING",
                        current_state="NON_TRENDING",
                        change_type=ChangeType.STATE_CHANGED,
                        significance=ChangeSignificance.NORMAL,
                        source="NSE_INDICATOR_RESULT",
                        reason=f"ADX fell below 25.0 ({p_adx:.1f} -> {c_adx:.1f}), signalling weakening trend",
                    )
                )

        # MACD Histogram Zero Crossover
        if prev.macd_hist is not None and curr.macd_hist is not None:
            p_hist = float(prev.macd_hist)
            c_hist = float(curr.macd_hist)
            if p_hist < 0 and c_hist >= 0:
                events.append(
                    ChangeEvent(
                        symbol=symbol,
                        evaluation_date=eval_d,
                        comparison_date=comp_d,
                        domain="TECHNICAL",
                        metric="macd_histogram",
                        previous_value=p_hist,
                        current_value=c_hist,
                        previous_state="NEGATIVE",
                        current_state="POSITIVE",
                        change_type=ChangeType.THRESHOLD_CROSSED,
                        significance=ChangeSignificance.SIGNIFICANT,
                        direction="BULLISH",
                        source="NSE_INDICATOR_RESULT",
                        reason=f"MACD histogram crossed above zero line ({p_hist:+.2f} -> {c_hist:+.2f})",
                    )
                )
            elif p_hist > 0 and c_hist <= 0:
                events.append(
                    ChangeEvent(
                        symbol=symbol,
                        evaluation_date=eval_d,
                        comparison_date=comp_d,
                        domain="TECHNICAL",
                        metric="macd_histogram",
                        previous_value=p_hist,
                        current_value=c_hist,
                        previous_state="POSITIVE",
                        current_state="NEGATIVE",
                        change_type=ChangeType.THRESHOLD_CROSSED,
                        significance=ChangeSignificance.SIGNIFICANT,
                        direction="BEARISH",
                        source="NSE_INDICATOR_RESULT",
                        reason=f"MACD histogram crossed below zero line ({p_hist:+.2f} -> {c_hist:+.2f})",
                    )
                )

        return events

    def _evaluate_trend_and_regime(
        self,
        symbol: str,
        eval_d: date,
        comp_d: date,
        prev_price: DailyPrice | None,
        curr_price: DailyPrice | None,
        prev_ind: IndicatorResult | None,
        curr_ind: IndicatorResult | None,
    ) -> list[ChangeEvent]:
        events: list[ChangeEvent] = []
        if prev_price is None or curr_price is None or prev_ind is None or curr_ind is None:
            return events

        # SuperTrend Direction Flip
        if prev_ind.supertrend_direction is not None and curr_ind.supertrend_direction is not None:
            p_dir = prev_ind.supertrend_direction
            c_dir = curr_ind.supertrend_direction
            if p_dir != c_dir:
                p_state = "BULLISH" if p_dir == 1 else "BEARISH"
                c_state = "BULLISH" if c_dir == 1 else "BEARISH"
                events.append(
                    ChangeEvent(
                        symbol=symbol,
                        evaluation_date=eval_d,
                        comparison_date=comp_d,
                        domain="TREND",
                        metric="supertrend",
                        previous_value=float(prev_ind.supertrend) if prev_ind.supertrend is not None else None,
                        current_value=float(curr_ind.supertrend) if curr_ind.supertrend is not None else None,
                        previous_state=p_state,
                        current_state=c_state,
                        change_type=ChangeType.STATE_CHANGED,
                        significance=ChangeSignificance.MAJOR,
                        direction=c_state,
                        source="NSE_INDICATOR_RESULT",
                        reason=f"SuperTrend flipped from {p_state} to {c_state}",
                    )
                )

        # Price vs EMA Crossings
        ema_checks = [
            ("price_vs_ema20", prev_ind.ema_20, curr_ind.ema_20, ChangeSignificance.SIGNIFICANT),
            ("price_vs_ema50", prev_ind.ema_50, curr_ind.ema_50, ChangeSignificance.SIGNIFICANT),
            ("price_vs_ema200", prev_ind.ema_200, curr_ind.ema_200, ChangeSignificance.MAJOR),
        ]

        p_close = float(prev_price.close)
        c_close = float(curr_price.close)

        for metric_name, prev_ema, curr_ema, sig in ema_checks:
            if prev_ema is not None and curr_ema is not None:
                p_ema_val = float(prev_ema)
                c_ema_val = float(curr_ema)
                p_state = "ABOVE" if p_close >= p_ema_val else "BELOW"
                c_state = "ABOVE" if c_close >= c_ema_val else "BELOW"

                if p_state != c_state:
                    direction = "BULLISH" if c_state == "ABOVE" else "BEARISH"
                    ema_label = metric_name.replace("price_vs_", "").upper()
                    events.append(
                        ChangeEvent(
                            symbol=symbol,
                            evaluation_date=eval_d,
                            comparison_date=comp_d,
                            domain="TREND",
                            metric=metric_name,
                            previous_value=p_close,
                            current_value=c_close,
                            previous_state=p_state,
                            current_state=c_state,
                            change_type=ChangeType.STATE_CHANGED,
                            significance=sig,
                            direction=direction,
                            source="NSE_INDICATOR_RESULT",
                            reason=f"Price crossed {c_state.lower()} {ema_label} ({p_close:.2f} -> {c_close:.2f} vs {c_ema_val:.2f})",
                        )
                    )

        return events

    def _evaluate_scanners(
        self,
        symbol: str,
        eval_d: date,
        comp_d: date,
        prev_scans: list[tuple[ScanResult, ScanDefinition]],
        curr_scans: list[tuple[ScanResult, ScanDefinition]],
    ) -> list[ChangeEvent]:
        events: list[ChangeEvent] = []
        prev_map = {sd.code: sd.name for _, sd in prev_scans}
        curr_map = {sd.code: sd.name for _, sd in curr_scans}

        # Newly matched scanners
        for code, name in curr_map.items():
            if code not in prev_map:
                events.append(
                    ChangeEvent(
                        symbol=symbol,
                        evaluation_date=eval_d,
                        comparison_date=comp_d,
                        domain="SCANNER",
                        metric=f"scanner_{code}",
                        previous_state="NOT_MATCHED",
                        current_state="MATCHED",
                        change_type=ChangeType.APPEARED,
                        significance=ChangeSignificance.SIGNIFICANT,
                        source="NSE_SCAN_RESULT",
                        reason=f"Stock newly matched scanner '{name}' ({code})",
                    )
                )

        # Scanners no longer matched
        for code, name in prev_map.items():
            if code not in curr_map:
                events.append(
                    ChangeEvent(
                        symbol=symbol,
                        evaluation_date=eval_d,
                        comparison_date=comp_d,
                        domain="SCANNER",
                        metric=f"scanner_{code}",
                        previous_state="MATCHED",
                        current_state="NOT_MATCHED",
                        change_type=ChangeType.DISAPPEARED,
                        significance=ChangeSignificance.NORMAL,
                        source="NSE_SCAN_RESULT",
                        reason=f"Stock no longer matches scanner '{name}' ({code})",
                    )
                )

        return events

    def _evaluate_deals(
        self,
        symbol: str,
        eval_d: date,
        comp_d: date,
        deals: list[BulkBlockDeal],
    ) -> list[ChangeEvent]:
        events: list[ChangeEvent] = []
        for deal in deals:
            val_crore = float(deal.deal_value) / 10_000_000.0 if deal.deal_value else 0.0
            sig = ChangeSignificance.MAJOR if val_crore >= DEAL_VALUE_MAJOR_CRORE else ChangeSignificance.SIGNIFICANT
            direction = "BULLISH" if deal.buy_sell.upper() == "BUY" else "BEARISH"
            events.append(
                ChangeEvent(
                    symbol=symbol,
                    evaluation_date=eval_d,
                    comparison_date=comp_d,
                    domain="DEAL",
                    metric=f"{deal.deal_type.lower()}_deal",
                    current_value=round(val_crore, 2),
                    change_type=ChangeType.APPEARED,
                    significance=sig,
                    direction=direction,
                    source="NSE_BULK_BLOCK",
                    reason=(
                        f"{deal.deal_type} Deal: {deal.client_name} {deal.buy_sell} {deal.quantity:,} shares "
                        f"@ ₹{float(deal.trade_price):.2f} (₹{val_crore:.2f} Cr)"
                    ),
                    metadata={
                        "deal_id": deal.id,
                        "deal_type": deal.deal_type,
                        "client_name": deal.client_name,
                        "buy_sell": deal.buy_sell,
                        "quantity": deal.quantity,
                        "trade_price": float(deal.trade_price),
                        "deal_value_crore": round(val_crore, 2),
                    },
                )
            )
        return events

    def _evaluate_corporate_events(
        self,
        symbol: str,
        eval_d: date,
        comp_d: date,
        actions: list[CorporateAction],
        announcements: list[CorporateAnnouncement],
    ) -> list[ChangeEvent]:
        events: list[ChangeEvent] = []
        for act in actions:
            events.append(
                ChangeEvent(
                    symbol=symbol,
                    evaluation_date=eval_d,
                    comparison_date=comp_d,
                    domain="CORPORATE",
                    metric=f"action_{act.action_type}",
                    change_type=ChangeType.APPEARED,
                    significance=ChangeSignificance.MAJOR,
                    source="NSE_CORPORATE_ACTION",
                    reason=f"Corporate Action: {act.action_type.upper()} (ex-date: {act.ex_date})",
                    metadata={
                        "action_type": act.action_type,
                        "ex_date": act.ex_date.isoformat(),
                        "notes": getattr(act, "notes", None),
                    },
                )
            )

        for ann in announcements:
            events.append(
                ChangeEvent(
                    symbol=symbol,
                    evaluation_date=eval_d,
                    comparison_date=comp_d,
                    domain="CORPORATE",
                    metric=f"announcement_{ann.category.lower()}",
                    change_type=ChangeType.APPEARED,
                    significance=ChangeSignificance.SIGNIFICANT,
                    source="NSE_CORPORATE_ANNOUNCEMENT",
                    reason=f"Corporate Announcement ({ann.category}): {ann.title}",
                    metadata={
                        "category": ann.category,
                        "announcement_date": ann.announcement_date.isoformat(),
                        "title": ann.title,
                    },
                )
            )
        return events

    def _evaluate_institutional(
        self,
        symbol: str,
        eval_d: date,
        comp_d: date,
        prev_flows: list[InstitutionalFlow],
        curr_flows: list[InstitutionalFlow],
    ) -> list[ChangeEvent]:
        events: list[ChangeEvent] = []
        prev_map = {f.investor_type: f for f in prev_flows}
        curr_map = {f.investor_type: f for f in curr_flows}

        for inv in ("FII", "DII"):
            p_flow = prev_map.get(inv)
            c_flow = curr_map.get(inv)
            if (
                p_flow is not None
                and c_flow is not None
                and p_flow.net_value is not None
                and c_flow.net_value is not None
            ):
                p_net = float(p_flow.net_value)
                c_net = float(c_flow.net_value)
                delta = c_net - p_net
                if abs(delta) >= INSTITUTIONAL_NET_FLOW_SIGNIFICANT_CRORE:
                    chg_type = ChangeType.VALUE_INCREASE if delta > 0 else ChangeType.VALUE_DECREASE
                    events.append(
                        ChangeEvent(
                            symbol=symbol,
                            evaluation_date=eval_d,
                            comparison_date=comp_d,
                            domain="INSTITUTIONAL",
                            metric=f"{inv.lower()}_net_flow",
                            previous_value=round(p_net, 2),
                            current_value=round(c_net, 2),
                            absolute_change=round(delta, 2),
                            change_type=chg_type,
                            significance=ChangeSignificance.NORMAL,
                            source="NSE_INSTITUTIONAL_FLOW",
                            reason=f"Institutional {inv} daily net flow shifted by ₹{delta:+.2f} Cr (₹{p_net:+.2f} Cr -> ₹{c_net:+.2f} Cr)",
                        )
                    )
        return events
