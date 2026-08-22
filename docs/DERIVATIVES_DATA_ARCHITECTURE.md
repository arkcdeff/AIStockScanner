# Derivatives Data Architecture — Sprint 19.1D

## 1. Domain Objective & Canonical Identity

`DerivativeContract` provides a canonical identity model for NSE Index Futures, Stock Futures, Index Options, and Stock Options without placing derivative-specific columns onto `Stock` or `DailyPrice`.

### Invariants & Invariant Constraints

1. **Underlying Identity**: Every contract references **exactly one** underlying:
   - `underlying_stock_id`: linked to `Stock` for stock derivatives (e.g. RELIANCE, SBIN).
   - `underlying_index_id`: linked to `MarketIndex` for index derivatives (e.g. NIFTY, BANKNIFTY).
   - Database constraint: `(underlying_stock_id IS NOT NULL AND underlying_index_id IS NULL) OR (underlying_stock_id IS NULL AND underlying_index_id IS NOT NULL)`.
   - Polymorphic free-text underlying symbols or duplicate Stock rows for indices are strictly forbidden.

2. **Futures Invariants**:
   - `instrument_type = 'FUTURE'`
   - `strike_price = NULL`
   - `option_type = NULL`
   - `lot_size > 0`
   - `expiry_date` is required.

3. **Options Invariants**:
   - `instrument_type = 'OPTION'`
   - `strike_price > 0` (Decimal/Numeric, never float)
   - `option_type IN ('CE', 'PE')`
   - `lot_size > 0`
   - `expiry_date` is required.

4. **Canonical Identity & SHA-256 Idempotency**:
   - Contract identity is logically defined by: `(exchange, underlying, instrument_type, expiry_date, strike_price, option_type)`.
   - Fingerprint (`idempotency_hash`) is generated via SHA-256 canonical string hashing and enforced by a `UNIQUE` database constraint + `before_insert` event.
   - Repeated file imports skip duplicate records automatically (First-writer wins).

---

## 2. Derivative Daily Prices (19.1D-2 Futures EOD)

`DerivativeDailyPrice` stores end-of-day Facts for derivative contracts (strictly isolated from cash `DailyPrice` rows).

### Model Invariants & Constraints
- `UNIQUE(contract_id, trade_date)`
- `open_interest >= 0` (when present; NULL if missing, MISSING != ZERO)
- `change_in_open_interest` is signed (allows positive, negative, or zero)
- `volume_contracts >= 0`, `turnover >= 0`
- `settlement_price` stored separately from `close` and `last_price`
- `turnover` normalized to INR Rupees (converts `VAL_INLAKH` * 100,000)
- `underlying_value` stored separately from equity spot `DailyPrice`
- `available_at`: date-granularity EOD publication date for point-in-time filtering

---

## 3. Futures-Only Import Policy (19.1D-2)

- `DerivativeFuturesImportService` handles EOD futures ingestion.
- Standard parser `DerivativeBhavcopyParser` parses both Legacy and UDiFF F&O bhavcopies.
- Filters and imports `FUTURE` rows into `DerivativeDailyPrice`.
- Explicitly skips `OPTION` rows and reports `options_skipped` count cleanly.
- Unresolved underlyings generate `DataQualityIssue` (`DQ_DERIVATIVE_PRICE_UNKNOWN_CONTRACT`).
- No-trade rows (0 contracts / zero volume) do not fail OHLC bounds validation.

---

## 4. Historical Lot-Size Policy

- **Contract Specification**: Each `DerivativeContract` stores the actual historical `lot_size` applicable to that specific contract at creation/expiry.
- **Lot Size Changes**: `DerivativeLotSizeHistory` tracks effective date ranges (`effective_from`, `effective_to`) of lot-size adjustments for underlyings.
- **Calculations**: Future notional, turnover, GEX, and position-sizing modules query historical contract lot size rather than current lot size.

---

## 5. Expiry Policy & Expired Contracts

- **Actual Source Expiries**: Actual `expiry_date` values from official source files are stored as authoritative truth. Hardcoded "every Thursday" rule generators are NOT used to construct historical contracts.
- **Expired Retention**: Expired contracts are **never hard-deleted**. Contracts with `expiry_date < available_at` are imported with `lifecycle_status = 'EXPIRED'`.

---

## 6. Source Registry & Official Source Policy

- **Source Registry**: `app/data/derivative_source_registry.py` maintains metadata for datasets (`FO_CONTRACT_MASTER`, `FO_UDIFF_BHAVCOPY`, `FO_SETTLEMENT`, `FO_NCL_OI`, `FO_COMBINED_OI`, `FO_PARTICIPANT_OI`, `FO_PARTICIPANT_VOLUME`, `FO_FII_DERIVATIVES`, `FO_BAN`, `FO_MWPL`).
- **Access Classifications**: `OFFICIAL_PUBLIC`, `OFFICIAL_RESTRICTED`, `LOCAL_VALIDATED_FILE`, `LICENSED_PROVIDER`.
- **Data Rights & Scraping Restrictions**:
  - **OIData is NOT a source.** Screenshots/UI layouts supplied are for product inspiration only.
  - Scraping commercial vendor data, bypassing anti-bot measures, or attempting unauthenticated 403 requests is strictly prohibited.
  - Local validated official files and public portal archives are supported.

---

## 7. Point-in-Time Semantics

- Date-granularity EOD cutoff semantics: `available_at = D` represents availability at the EOD cutoff of date $D$.
- `HistoricalQueryService.get_futures_history()`, `get_futures_curve()`, and `get_options_for_expiry()` apply `available_at <= as_of_date` filtering without look-ahead bias.

---

## 8. Options-Only Import Policy (19.1D-3)

- `DerivativeOptionsImportService` handles EOD options ingestion on top of generic `DerivativeDailyPrice`.
- `DerivativeBhavcopyParser` auto-detects both Legacy and UDiFF F&O bhavcopies, parses option records (`options_records`), and tracks `futures_skipped_count`.
- `OPTION` contract identity resolved via `(exchange, underlying_stock_id / underlying_index_id, 'OPTION', expiry_date, strike_price, option_type)`.
- Strike prices stored and queried with `Decimal` precision. Option types normalized to `'CE'` / `'PE'`.
- Illiquid / no-trade option rows (0 volume, positive/zero OI) are processed cleanly without false OHLC corruption warnings.

## 9. Historical Option Chain Reconstruction (19.1D-4)

- `HistoricalOptionChainService` reconstructs authoritative, point-in-time safe EOD historical option chains on demand without duplicating raw records into dedicated persistent option chain tables.
- Polymorphic underlying symbol resolution maps free-text symbols (`NIFTY`, `BANKNIFTY`, `RELIANCE`) to `MarketIndex`, `Stock`, or `SecurityAlias`.
- Query strategy: single SQL `JOIN` over `(DerivativeDailyPrice, DerivativeContract)` where `instrument_type == 'OPTION'`, `expiry_date == E`, `trade_date == T`, `available_at <= as_of_date` ordered by `strike_price ASC`, `option_type ASC`.
- Exact `Decimal` strike pairing: pairs CE and PE side observations into structured rows. Missing sides are preserved explicitly (`call = None` or `put = None`; MISSING != ZERO).
- Spot reference: surfaces official `source_underlying_value` from derivative daily prices. Fallbacks to `MarketIndexPrice.close` (indices) or `DailyPrice.close` (stocks). Reports `OPTION_CHAIN_INCONSISTENT_UNDERLYING` quality warning if strikes report differing spot values.
- ATM reference & Moneyness: calculates structural `atm_reference_strike` (nearest available strike price to spot). Classifies `moneyness` (`ITM`, `OTM`, `ATM`) per option side.
- Calendar DTE: calculates `calendar_days_to_expiry = (expiry_date - trade_date).days`.
- Metadata: provides completeness metrics (`total_strikes`, `paired_strikes`, `ce_only_strikes`, `pe_only_strikes`, `min_strike`, `max_strike`) and quality warnings.
- Presentation & REST API: exposed via `GET /api/v1/market-data/derivatives/options/chain`, `GET /api/v1/market-data/derivatives/options/expiries`, and HTMX partial `GET /market-data/derivatives/options/chain/table`.

## 10. Historical Futures OI, Buildup, Basis & Rollover Analytics (19.1D-5)

- `HistoricalFuturesAnalyticsService` implements a deterministic, point-in-time historical futures analytics layer over raw `DerivativeContract` and `DerivativeDailyPrice` records without database mutations or table additions (ORM models remain at **49**).
- **Price Reference Fallback**: Settlement fallback hierarchy: 1. `settlement_price`, 2. `close`, 3. `last_price`. Output includes `price_used` and `price_source_field`.
- **Same-Contract Prior Observation**: Compares contract observation against prior available trading date (`trade_date < current trade_date`, `available_at <= as_of_date`). If missing, classifies as `INSUFFICIENT_HISTORY`.
- **Source vs Derived ΔOI**: Tracks `source_change_in_oi` and calculates `derived_change_in_oi = current_oi - previous_oi`. Flags `FUTURES_ANALYTICS_OI_CHANGE_MISMATCH` if disagreement occurs.
- **Buildup Classification**: Configurable noise thresholds (default `price_threshold_pct = 0.05%`, `oi_threshold_pct = 0.5%`). Pattern classifications: `LONG_BUILDUP` (Price UP + OI UP), `SHORT_BUILDUP` (Price DOWN + OI UP), `SHORT_COVERING` (Price UP + OI DOWN), `LONG_UNWINDING` (Price DOWN + OI DOWN), plus 5 flat/neutral states. Non-participant disclaimer included.
- **Spot & Basis**: Spot reference resolved via 1. `DerivativeDailyPrice.underlying_value`, 2. `MarketIndexPrice.close` (index) or `DailyPrice.close` (stock). Computes `basis_absolute`, `basis_pct`, and `annualized_simple_basis_pct` (for DTE > 0).
- **Futures Curve & Term Structure**: Queries available futures contracts ordered by `expiry_date ASC` (NEAR, NEXT, FAR_1, etc.). Classifies slope (`UPWARD_SLOPING`, `DOWNWARD_SLOPING`, `FLAT_CURVE`) and computes adjacent calendar spreads.
- **OI Distribution & Rollover**: Computes per-expiry `oi_share_pct`, `rollover_next_pct = next_oi / (near_oi + next_oi) * 100`, and `rollover_all_later_pct`. Flags `is_expiry_window = True` when `calendar_days_to_near_expiry <= 7`.
- **REST API & HTMX UI**: Exposed via REST endpoints `GET /api/v1/market-data/derivatives/futures/analytics` & `GET /api/v1/market-data/derivatives/futures/rollover` and HTMX partial `GET /market-data/derivatives/futures/analytics/table` rendering Futures Analytics tab on `/market-data`.

## 11. Point-in-Time Options Analytics (19.1D-6)

- `HistoricalOptionsAnalyticsService` provides transparent options analytics on top of reconstructed EOD option chains.
- **PCR Metrics**: Put-Call Ratio by Open Interest ($\text{PCR}_{OI}$) and Volume ($\text{PCR}_{Volume}$) with observation coverage metadata and safe zero-denominator handling.
- **Max Pain Algorithm**: Lot-size weighted payout minimization across candidate strikes with deterministic tie-breaking rules (nearest to spot, then lower strike).
- **OI Walls & Unwinding**: Static Call/Put Walls (highest valid $OI$), Fresh Call/Put Walls ($\Delta OI > 0$), and Call/Put Unwinding levels ($\Delta OI < 0$).
- **Term Structure Analytics**: Cross-expiry open-interest and volume distribution, front-expiry dominance, and nearest-expiry market share.
- **REST API & Web UI**: Exposed via `GET /api/v1/market-data/derivatives/options/analytics`, `GET /api/v1/market-data/derivatives/options/expiry-analytics`, and HTMX partial `GET /market-data/derivatives/options/analytics/cards`.

## 12. Participant-wise Derivatives Positioning (19.1D-7)

- **Persistence Models**:
  - `DerivativeParticipantPosition` (`derivative_participant_positions`): Stores normalized open-interest or trading-volume facts by participant category (`CLIENT`, `DII`, `FII`, `PRO`) and instrument bucket (`INDEX_FUTURE`, `STOCK_FUTURE`, `INDEX_OPTION_CALL`, `INDEX_OPTION_PUT`, `STOCK_OPTION_CALL`, `STOCK_OPTION_PUT`).
  - `FiiDerivativeStatistic` (`fii_derivative_statistics`): Stores summary activity for Foreign Institutional Investors (`FO_FII_DERIVATIVES`).
- **Parsers & Ingestion**:
  - `ParticipantOpenInterestParser` & `ParticipantTradingVolumeParser`: Parse official NSE CSV reports (`fao_participant_oi.csv`, `fao_participant_vol.csv`). Normalizes participant categories, validates row total contracts, and checks market balance ($\text{Sum(Long)} = \text{Sum(Short)}$).
  - `FiiDerivativesStatisticsParser`: Parses buy/sell contracts, buy/sell amounts, and OI contracts/amounts for FIIs.
  - `DerivativeParticipantImportService`: Orchestrates raw file archival under `data/raw/nse/fo/YYYY/MM/`, checksum verification, idempotent upserts, and quality issue recording.
- **Analytics & Ratios**:
  - `HistoricalParticipantAnalyticsService` provides point-in-time snapshots, FII Index Futures Long/Short/Net (`Long - Short`), Long/Short Ratios, Day-over-Day changes, and category concentration shares.
  - Options positioning retains raw Call/Put Long/Short breakdown without simplistic directional score reduction.
- **REST API & Web UI**: Exposed via REST endpoints `GET /api/v1/market-data/derivatives/participants`, `GET /api/v1/market-data/derivatives/participants/history`, `GET /api/v1/market-data/derivatives/fii`, and HTMX partial `GET /market-data/derivatives/participants/cards` integrated into the Participant Positioning tab on `/market-data`.

## 13. Derivatives Coverage, Data Quality & Point-in-Time Safe Historical Replay (19.1D-8)

- **Platform Derivatives Coverage**:
  - `DerivativeCoverageService` assesses platform coverage across 7 dataset dimensions:
    1. Contract Master (`FO_CONTRACT_MASTER`)
    2. Futures Prices (`FO_FUTURES_EOD`)
    3. Options Prices (`FO_OPTIONS_EOD`)
    4. Participant Open Interest (`FO_PARTICIPANT_OI`)
    5. Participant Trading Volume (`FO_PARTICIPANT_VOLUME`)
    6. FII Derivatives Statistics (`FO_FII_DERIVATIVES`)
    7. Historical Option Chains (Derived from `FO_CONTRACT_MASTER` + `FO_OPTIONS_EOD`)
  - Status classification: `COMPLETE_FOR_IMPORTED_SCOPE`, `PARTIAL`, `NO_DATA`.
  - Missing session count is explicitly returned as `"UNKNOWN"` when trading calendar infrastructure is unprovided (prohibiting fake weekday calculations).
  - Scope descriptions explicitly demarcate imported scope from exchange-wide scope.

- **Data Quality & Monetary Units**:
  - `FiiDerivativeStatistic` amounts (`buy_amount`, `sell_amount`, `open_interest_amount`) are explicitly documented and returned in `INR_CRORE` as published by NSE India.
  - Parsers and services record structured `DataQualityIssue` rows (severity, exchange, symbol, trade_date, issue_code, description).

- **Point-in-Time Safe Historical Replay**:
  - `HistoricalDerivativesReplayService` orchestrates on-demand reconstruction of the complete historical derivatives knowledge state for symbol $U$ on date $T$ as of evaluation cutoff $A$.
  - Reuses existing D-4 option chain, D-5 futures analytics, D-6 options analytics, and D-7 participant analytics services without creating new raw database tables or persistent replay tables (ORM model count remains at **51**).
  - Zero lookahead bias: enforces `available_at <= as_of_date` filtering across all underlying tables and components.
  - Provenance audit trail: returns `ReplayProvenanceItem` entries capturing source name, source reference, data date, and point-in-time publication date (`available_at`).
  - Strict scope: Replay reconstructs *what was known* by cutoff $A$ for date $T$. It explicitly avoids look-ahead predictive metrics (next-day returns, future prices, or win-rate claims).

- **REST API & Web UI**:
  - Exposes REST endpoints:
    - `GET /api/v1/market-data/derivatives/coverage` (Platform 7-dimension coverage summary)
    - `GET /api/v1/market-data/derivatives/quality` (Data quality summary & issue counts)
    - `GET /api/v1/market-data/derivatives/replay` (Point-in-time safe historical replay state)
  - Exposes HTMX partial endpoint `GET /market-data/derivatives/replay/cards` rendering the **Historical Replay** tab on `/market-data`.
