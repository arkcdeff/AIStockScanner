from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.alert import AlertEvent, AlertRule
from app.models.daily_price import DailyPrice
from app.models.indicator_result import IndicatorResult
from app.models.stock import Stock
from app.models.technical_analysis import TechnicalAnalysis
from app.models.watchlist import WatchlistStock
from app.repositories.alert import AlertEventRepository, AlertRuleRepository

RULE_TYPE_OVERALL_SCORE = "overall_score"
RULE_TYPE_RSI = "rsi"
RULE_TYPE_EMA_CROSS = "ema_cross"
RULE_TYPE_DELIVERY = "delivery"
RULE_TYPE_VOLUME = "volume"
RULE_TYPE_BREAKOUT = "breakout"
RULE_TYPE_SUPPORT_BROKEN = "support_broken"
RULE_TYPE_RESISTANCE_BROKEN = "resistance_broken"

EVENT_STATUS_TRIGGERED = "triggered"
EVENT_STATUS_ACKNOWLEDGED = "acknowledged"

_SUPPORTED_RULE_TYPES = {
    RULE_TYPE_OVERALL_SCORE,
    RULE_TYPE_RSI,
    RULE_TYPE_EMA_CROSS,
    RULE_TYPE_DELIVERY,
    RULE_TYPE_VOLUME,
    RULE_TYPE_BREAKOUT,
    RULE_TYPE_SUPPORT_BROKEN,
    RULE_TYPE_RESISTANCE_BROKEN,
}

_COMPARE_OPERATORS = {">", ">=", "<", "<=", "true", "false", "crosses_above", "crosses_below"}


class AlertEngineError(Exception):
    pass


class AlertService:
    """CRUD for alert rules plus the deterministic rule evaluation engine.

    The engine is designed to run immediately after the analytics stage of
    the EOD pipeline. It reads the latest computed state (technical analysis,
    indicator results, daily prices) for the scope of each active rule and
    persists matching ``AlertEvent`` rows. No notifications are sent yet.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.rule_repo = AlertRuleRepository(session)
        self.event_repo = AlertEventRepository(session)

    # ------------------------------------------------------------- rule CRUD

    async def create_rule(self, payload: dict[str, Any]) -> AlertRule:
        self._validate_rule(payload)
        rule = AlertRule(**{k: v for k, v in payload.items() if hasattr(AlertRule, k)})
        await self.rule_repo.add(rule)
        await self.session.commit()
        await self.session.refresh(rule)
        return rule

    async def update_rule(self, rule_id: int, payload: dict[str, Any]) -> AlertRule:
        rule = await self.rule_repo.get(rule_id)
        if rule is None:
            raise AlertEngineError(f"Alert rule {rule_id} not found")
        self._validate_rule(payload)
        for key, value in payload.items():
            if hasattr(AlertRule, key) and value is not None:
                setattr(rule, key, value)
        await self.session.commit()
        await self.session.refresh(rule)
        return rule

    async def delete_rule(self, rule_id: int) -> None:
        rule = await self.rule_repo.get(rule_id)
        if rule is None:
            raise AlertEngineError(f"Alert rule {rule_id} not found")
        await self.session.delete(rule)
        await self.session.commit()

    async def list_rules(self, *, offset: int = 0, limit: int = 100) -> list[AlertRule]:
        return await self.rule_repo.list_rules(offset=offset, limit=limit)

    async def get_rule(self, rule_id: int) -> AlertRule | None:
        return await self.rule_repo.get_with_details(rule_id)

    @staticmethod
    def _validate_rule(payload: dict[str, Any]) -> None:
        rule_type = payload.get("rule_type")
        if rule_type and rule_type not in _SUPPORTED_RULE_TYPES:
            raise AlertEngineError(f"Unsupported rule_type: {rule_type}")
        operator = payload.get("operator")
        if operator and operator not in _COMPARE_OPERATORS:
            raise AlertEngineError(f"Unsupported operator: {operator}")
        if rule_type in {
            RULE_TYPE_EMA_CROSS,
            RULE_TYPE_BREAKOUT,
            RULE_TYPE_SUPPORT_BROKEN,
            RULE_TYPE_RESISTANCE_BROKEN,
        } and operator not in {"true", "false", "crosses_above", "crosses_below"}:
            raise AlertEngineError(f"operator must be a signal operator for {rule_type}")

    # ----------------------------------------------------------- event reads

    async def list_recent_events(
        self,
        *,
        offset: int = 0,
        limit: int = 50,
        status: str | None = None,
        product_only: bool = False,
    ) -> list[AlertEvent]:
        return await self.event_repo.list_recent(
            offset=offset,
            limit=limit,
            status=status,
            product_only=product_only,
        )

    async def list_events_for_symbol(self, symbol: str, limit: int = 20) -> list[AlertEvent]:
        return await self.event_repo.list_for_symbol(symbol, limit=limit)

    async def count_pending(self, *, product_only: bool = False) -> int:
        return await self.event_repo.count_by_status(
            EVENT_STATUS_TRIGGERED, product_only=product_only
        )

    async def update_event_status(self, event_id: int, status: str) -> AlertEvent | None:
        event = await self.event_repo.get_with_details(event_id)
        if event is None:
            return None
        event.status = status
        await self.session.commit()
        await self.session.refresh(event)
        return event

    # -------------------------------------------------------------- engine

    async def run_for_trade_date(self, trade_date: date | None = None) -> dict[str, int]:
        """Evaluate every enabled rule against the latest computed state.

        Returns a summary of how many rules/stock pairs were checked and how
        many alert events were created.
        """
        rules = await self.rule_repo.list_active()
        if not rules:
            return {"checked": 0, "triggered": 0}

        evaluated: list[tuple[AlertRule, Stock, float, str]] = []
        checked = 0
        for rule in rules:
            stock_ids = await self._rule_stock_ids(rule)
            if not stock_ids:
                continue
            context = await self._load_eval_context(stock_ids)
            for stock_id in stock_ids:
                entry = self._evaluate(rule, stock_id, context)
                if entry is None:
                    continue
                checked += 1
                stock, trigger_value, message = entry
                if self._cooldown_active(rule):
                    continue
                evaluated.append((rule, stock, trigger_value, message))

        if evaluated:
            await self._persist(evaluated, trade_date)
        return {"checked": checked, "triggered": len(evaluated)}

    async def _rule_stock_ids(self, rule: AlertRule) -> list[int]:
        if rule.stock_id is not None:
            return [rule.stock_id]
        if rule.watchlist_id is not None:
            statement = select(WatchlistStock.stock_id).where(
                WatchlistStock.watchlist_id == rule.watchlist_id
            )
            return list(await self.session.scalars(statement))
        statement = select(Stock.id).where(Stock.is_active.is_(True))
        return list(await self.session.scalars(statement))

    async def _load_eval_context(self, stock_ids: list[int]) -> dict[str, Any]:
        analysis_map: dict[int, TechnicalAnalysis] = {}
        indicator_map: dict[int, tuple[IndicatorResult | None, IndicatorResult | None]] = {}
        price_map: dict[int, DailyPrice] = {}

        analyses = (
            await self.session.scalars(
                select(TechnicalAnalysis)
                .where(TechnicalAnalysis.stock_id.in_(stock_ids))
                .order_by(TechnicalAnalysis.stock_id, TechnicalAnalysis.trade_date)
            )
        ).all()
        grouped_analysis: dict[int, list[TechnicalAnalysis]] = {}
        for row in analyses:
            grouped_analysis.setdefault(row.stock_id, []).append(row)
        for stock_id, items in grouped_analysis.items():
            analysis_map[stock_id] = items[-1]

        indicators = (
            await self.session.scalars(
                select(IndicatorResult)
                .where(IndicatorResult.stock_id.in_(stock_ids))
                .order_by(IndicatorResult.stock_id, IndicatorResult.trade_date)
            )
        ).all()
        grouped_indicators: dict[int, list[IndicatorResult]] = {}
        for row in indicators:
            grouped_indicators.setdefault(row.stock_id, []).append(row)
        for stock_id, items in grouped_indicators.items():
            indicator_map[stock_id] = (items[-1], items[-2] if len(items) > 1 else None)

        prices = (
            await self.session.scalars(
                select(DailyPrice)
                .where(DailyPrice.stock_id.in_(stock_ids))
                .order_by(DailyPrice.stock_id, DailyPrice.trade_date)
            )
        ).all()
        grouped_prices: dict[int, list[DailyPrice]] = {}
        for row in prices:
            grouped_prices.setdefault(row.stock_id, []).append(row)
        for stock_id, items in grouped_prices.items():
            price_map[stock_id] = items[-1]

        stocks = (
            await self.session.scalars(
                select(Stock).where(Stock.id.in_(stock_ids))
            )
        ).all()
        stock_map = {stock.id: stock for stock in stocks}

        return {
            "analysis": analysis_map,
            "indicators": indicator_map,
            "prices": price_map,
            "stocks": stock_map,
        }

    def _evaluate(
        self,
        rule: AlertRule,
        stock_id: int,
        context: dict[str, Any],
    ) -> tuple[Stock, float, str] | None:
        analysis = context["analysis"].get(stock_id)
        price = context["prices"].get(stock_id)
        stock = context["stocks"].get(stock_id)
        latest_indicator, previous_indicator = context["indicators"].get(stock_id, (None, None))
        if stock is None or analysis is None:
            return None

        trigger_value: float | None = None
        triggered = False
        message = f"{rule.name}: {stock.symbol}"

        if rule.rule_type == RULE_TYPE_OVERALL_SCORE:
            trigger_value = float(analysis.overall_score)
            triggered = self._compare(rule.operator, trigger_value, rule.value)
            message += f" overall score {trigger_value} {rule.operator} {self._fmt(rule.value)}"
        elif rule.rule_type == RULE_TYPE_RSI:
            if latest_indicator is None or latest_indicator.rsi_14 is None:
                return None
            trigger_value = float(latest_indicator.rsi_14)
            triggered = self._compare(rule.operator, trigger_value, rule.value)
            message += f" RSI {trigger_value} {rule.operator} {self._fmt(rule.value)}"
        elif rule.rule_type == RULE_TYPE_DELIVERY:
            if price is None or price.delivery_percentage is None:
                return None
            trigger_value = float(price.delivery_percentage)
            triggered = self._compare(rule.operator, trigger_value, rule.value)
            message += f" delivery {trigger_value}% {rule.operator} {self._fmt(rule.value)}"
        elif rule.rule_type == RULE_TYPE_VOLUME:
            if analysis.relative_volume is None:
                return None
            trigger_value = float(analysis.relative_volume)
            triggered = self._compare(rule.operator, trigger_value, rule.value)
            message += (
                f" volume {trigger_value}x {rule.operator} {self._fmt(rule.value)}x of 20d avg"
            )
        elif rule.rule_type == RULE_TYPE_BREAKOUT:
            trigger_value = 1.0 if analysis.is_breakout else 0.0
            triggered = self._signal_true(rule.operator, analysis.is_breakout)
            message += " flagged as breakout" if triggered else " not a breakout"
        elif rule.rule_type == RULE_TYPE_SUPPORT_BROKEN:
            if price is None or analysis.support is None:
                return None
            trigger_value = float(price.close)
            triggered = float(price.close) < float(analysis.support)
            message += (
                f" closed {self._fmt(trigger_value)} below support "
                f"{self._fmt(float(analysis.support))}"
                if triggered
                else f" holding above support {self._fmt(float(analysis.support))}"
            )
        elif rule.rule_type == RULE_TYPE_RESISTANCE_BROKEN:
            if price is None or analysis.resistance is None:
                return None
            trigger_value = float(price.close)
            triggered = float(price.close) > float(analysis.resistance)
            message += (
                f" closed {self._fmt(trigger_value)} above resistance "
                f"{self._fmt(float(analysis.resistance))}"
                if triggered
                else f" below resistance {self._fmt(float(analysis.resistance))}"
            )
        elif rule.rule_type == RULE_TYPE_EMA_CROSS:
            if latest_indicator is None or previous_indicator is None:
                return None
            latest_20 = self._to_float(latest_indicator.ema_20)
            latest_50 = self._to_float(latest_indicator.ema_50)
            prev_20 = self._to_float(previous_indicator.ema_20)
            prev_50 = self._to_float(previous_indicator.ema_50)
            if None in (latest_20, latest_50, prev_20, prev_50):
                return None
            trigger_value = 1.0
            if rule.operator == "crosses_above":
                triggered = prev_20 <= prev_50 and latest_20 > latest_50
            elif rule.operator == "crosses_below":
                triggered = prev_20 >= prev_50 and latest_20 < latest_50
            else:
                triggered = self._signal_true(rule.operator, bool(latest_20 > latest_50))
            direction = (
                "crossed above"
                if triggered and rule.operator == "crosses_above"
                else "crossed below"
            )
            message += f" EMA20 {direction} EMA50" if triggered else " EMA20/EMA50 no cross"

        if not triggered or trigger_value is None:
            return None
        return stock, trigger_value, message

    @staticmethod
    def _compare(operator: str, actual: float, target: float | None) -> bool:
        if target is None:
            return False
        if operator == ">":
            return actual > target
        if operator == ">=":
            return actual >= target
        if operator == "<":
            return actual < target
        if operator == "<=":
            return actual <= target
        if operator == "true":
            return actual > 0
        if operator == "false":
            return actual <= 0
        return False

    @staticmethod
    def _signal_true(operator: str, flag: bool) -> bool:
        if operator == "true":
            return flag is True
        if operator == "false":
            return flag is False
        return flag is True

    @staticmethod
    def _to_float(value: float | None) -> float | None:
        if value is None:
            return None
        return float(value)

    @staticmethod
    def _fmt(value: float | None) -> str:
        if value is None:
            return "?"
        return f"{value:.2f}".rstrip("0").rstrip(".")

    def _cooldown_active(self, rule: AlertRule) -> bool:
        if not rule.cooldown_minutes or rule.cooldown_minutes <= 0 or rule.last_triggered is None:
            return False
        window = rule.last_triggered.replace(tzinfo=UTC)
        return datetime.now(UTC) - window < timedelta(minutes=rule.cooldown_minutes)

    async def _persist(
        self,
        entries: list[tuple[AlertRule, Stock, float, str]],
        trade_date: date | None,
    ) -> None:
        now = datetime.now(UTC)
        for rule, stock, trigger_value, message in entries:
            event = AlertEvent(
                alert_rule_id=rule.id,
                stock_id=stock.id,
                trade_date=trade_date,
                trigger_value=trigger_value,
                message=message,
                status=EVENT_STATUS_TRIGGERED,
            )
            self.session.add(event)
            rule.last_triggered = now
        await self.session.commit()
