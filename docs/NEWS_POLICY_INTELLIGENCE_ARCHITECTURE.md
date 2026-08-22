# NEWS AND GOVERNMENT POLICY INTELLIGENCE ARCHITECTURE — SPRINT 19.1F

## 1. Domain Purpose & Principles

Sprint 19.1F establishes a point-in-time (PIT) safe news, government policy, RBI monetary policy, and SEBI regulatory intelligence layer.

### Primary Purpose
1. **Factual Foundation**: Store official disclosures, government policy changes, and news disclosures with strict source attribution.
2. **Point-in-Time Protection**: Enforce `available_at <= evaluation_datetime` to eliminate lookahead bias in historical strategy backtests and walk-forward simulations.
3. **Deterministic Context**: Classify events into transparent context buckets (`POSITIVE_CONTEXT`, `NEGATIVE_CONTEXT`, `MIXED`, `NEUTRAL`, `UNKNOWN`) without raw LLM hallucinated facts or premature trade recommendations.
4. **Event Risk Engine**: Expose factual event risk flags (`OVERNIGHT_EVENT_RISK`, `RESULTS_PENDING`, `BOARD_MEETING_PENDING`, `REGULATORY_EVENT`, `RBI_EVENT`, `POLICY_HEADWIND`, `POLICY_TAILWIND`) to power future BTST, Quality Pullback, and Stock 360° modules.

---

## 2. Source Trust Hierarchy

To protect factual integrity, source trust is explicitly modeled:
1. **`OFFICIAL_PRIMARY`**: Exchange filings (NSE/BSE corporate announcements), Gazette of India, PIB press releases, RBI notifications, SEBI circulars.
2. **`OFFICIAL_SECONDARY`**: Official ministry press summaries or government statistical releases.
3. **`LICENSED_NEWS`**: Formally ingested news feeds from authorized agency partners.
4. **`VALIDATED_LOCAL`**: Local CSV/JSON payload files used for testing and reproducible historical replays.
5. **`UNVERIFIED`**: Web or third-party reports.

---

## 3. Data Schema & Models

### `NewsEvent` (`app/models/news_policy.py`)
- `id`: Primary Key
- `headline`: Title/headline string
- `summary`: Short excerpt or summary metadata
- `source_name`, `source_type`, `source_reference`: Source provenance & trust rank
- `published_at`: When published by the source (timezone aware)
- `available_at`: When publicly knowable by our historical engine (`available_at <= evaluation_cutoff`)
- `effective_at`: When government policy actually takes effect
- `event_type`: `COMPANY`, `GOVERNMENT_POLICY`, `RBI_MONETARY`, `SEBI_REGULATORY`, `MACRO`
- `category`: Sub-category (`RESULTS`, `ORDER_WIN`, `TAXATION`, `CUSTOMS`, `PLI`, `REGULATORY`, etc.)
- `scope_type`: `STOCK`, `MULTI_STOCK`, `SECTOR`, `MARKET`, `MACRO`
- `deterministic_context`: `POSITIVE_CONTEXT`, `NEGATIVE_CONTEXT`, `MIXED`, `NEUTRAL`, `UNKNOWN`
- `event_severity`: `LOW`, `MEDIUM`, `HIGH`, `CRITICAL`
- `event_risk_flag`: `OVERNIGHT_EVENT_RISK`, `RESULTS_PENDING`, `BOARD_MEETING_PENDING`, `REGULATORY_EVENT`, etc.
- `stock_id`, `sector_id`, `market_index_id`: Nullable foreign keys for single-entity scope
- `corporate_announcement_id`, `econ_event_id`: Nullable links to existing 19.1E & Sprint 18 models to reuse facts without duplication
- `idempotency_hash`: SHA-256 fingerprint for deduplication

### `NewsEntityLink` (`app/models/news_policy.py`)
- Normalized table mapping multi-stock or multi-sector policy impacts (`impact_relation`: `DIRECT`, `INDIRECT`, `UNKNOWN`).

---

## 4. Point-in-Time Lookahead Bias Protection

All historical queries require `evaluation_datetime`:
```sql
SELECT * FROM news_events
WHERE stock_id = :stock_id
  AND available_at <= :evaluation_datetime
ORDER BY published_at DESC;
```
Any event published or made available after `:evaluation_datetime` is strictly excluded from historical snapshots.

---

## 5. Future System Integration Hooks

1. **Stock 360° (19.1G)**: `CompanyNewsPolicySnapshot` provides structured event context, policy headwinds/tailwinds, and risk flags.
2. **BTST Engine**: `overnight_event_risk` and pending results/board meeting flags prevent holding positions overnight into high-volatility scheduled disclosures.
3. **Quality Pullback Scanner**: Distinguishes temporary negative sentiment/events from structural fundamental degradation (from 19.1E).
