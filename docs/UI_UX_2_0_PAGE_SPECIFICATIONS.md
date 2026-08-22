# UI/UX 2.0 Page Specifications

**Sprint:** 20.0  
**Date:** 2026-08-22  
**Status:** Target specifications; no production implementation

## 1. Shared Application Shell

### Purpose

Provide consistent navigation, context, status and responsive behavior.

### Required elements

- Permission-aware grouped sidebar.
- Mobile off-canvas navigation with identical hierarchy.
- Topbar with page title, global search, market/data status, theme control and account menu.
- Breadcrumbs only on nested detail pages.
- Shared flash, loading, empty, error and permission states.
- Skip link, visible focus states, keyboard-operable controls and semantic headings.

### Existing assets to reuse

`layouts/shell.html`, `components/sidebar.html`, `components/topbar.html`, `design-system.css`, `dashboard.css`, `shell.js`.

## 2. Dashboard

**Route:** `/dashboard`  
**Audience:** All authenticated product users with `dashboard.read`  
**Primary question:** What changed, what matters, and what needs action?

### Layout

1. Context strip: market date/status, source freshness, pipeline state.
2. Four concise KPIs: regime, priority opportunities, active alerts, portfolio/watchlist impact.
3. Opportunity and alert panels with direct drill-downs.
4. Market intelligence summary and data-health exceptions.
5. Recent operational activity.

### Actions

- Refresh summary.
- Open Market Intelligence.
- Open opportunity/stock details.
- Open alerts, pipeline or coverage when attention is required.

### Remove/consolidate

- Duplicate AI status/explanation tables.
- Full import and ranking tables that belong in dedicated workspaces.
- Generic "all-time" metrics without decision value.

### States

No market data, stale data, market closed, pipeline failed, no opportunities and permission-limited summaries.

## 3. Market Intelligence

**Route:** `/market-intelligence`  
**Permission:** `market_intelligence.read`; refresh requires `market_intelligence.refresh`

### Header

- Regime, composite score, confidence, completeness and evaluation time.
- Source/replay/freshness badges.
- Refresh action only for authorized users.

### Sections

- Indian indices and price action.
- Global/Asian markets and GIFT Nifty.
- Institutional flows and participant context.
- Breadth and sector rotation.
- Macro, event risk and external factors.
- Deterministic factor explanation and optional AI narrative.

### Interaction

Anchored local navigation; sections may load on demand. Factor cards expand to show inputs, weights, provenance, missing factors and limitations.

## 4. Stock Explorer

**Route:** `/stocks`  
**Permission:** `stocks.read`

### Purpose

Find a symbol and launch a research workflow.

### Content

- Search by symbol/company.
- Filters for sector, lifecycle/status and available data.
- Compact result table with latest date/price, overall score, freshness and watchlist state.
- Direct launch to Stock360.

### Actions

Open Stock360, add to watchlist and compare selected stocks when comparison is supported.

## 5. Stock360 Workspace

**Route:** `/stocks/{symbol}`  
**Permission:** `stocks.read`, with feature panels/actions additionally permission-aware

### Persistent header

- Symbol, company, sector, last price/change, latest data timestamp.
- Overall deterministic score, confidence/completeness and major flags.
- Watchlist, alert and portfolio actions.

### Local navigation

| Tab | Content |
|---|---|
| Summary | Research thesis, drivers, risks and missing data |
| Price & Technicals | Price chart, indicators, support/resistance, pivots and relative strength |
| Fundamentals | Corporate/fundamental cards and corporate actions |
| News & Policy | Relevant news/policy intelligence with source/time |
| Derivatives | Futures/options context for the symbol when available |
| Scanners & Opportunities | Scanner matches and ranked opportunities |
| AI Explanation | Explanation of deterministic results, never a substitute calculation |
| Ownership & Monitoring | Watchlists, portfolio, alerts and notifications |

### Requirements

- Lazy-load heavy tabs.
- Enforce each domain permission consistently for initial and partial renders.
- Never render missing fundamentals/derivatives as zero.

## 6. Scanner Workspace

### Scanner Library

**Target route:** `/scanners`  
Browse definitions by category, required lookback, active state and description. "Run" must pass the exact parameter expected by the run route.

### Run Scanner

**Target route:** `/scanners/run`  
Select scanner/date/universe, show readiness prerequisites, execute with `scanners.run`, and transition to results.

### Results

**Target route:** `/scanners/results`  
Expose existing filters for scanner, symbol/search, sector, date range, price, delivery, volume and sort. Include CSV/Excel/PDF controls and stable HTMX target IDs. Rows link to Stock360 while preserving scanner/date context.

## 7. Derivatives Workspace

**Target route:** `/derivatives`  
**Primary question:** What is the derivatives structure for this underlying and date?

### Context bar

Underlying, date/evaluation time, expiry, spot, source, coverage, market status and replay/live mode.

### Overview

Summarize futures buildup/rollover, PCR/max pain/walls, exposure regime, participant positioning and top opportunities. Every summary drills into a dedicated tab.

### Futures

- Contract curve and expiries.
- OI/price buildup classification.
- Basis, annualized basis and curve shape.
- Near/next/far OI share and rollover.

### Option Chain

- Strike-paired call/put table.
- Sticky strike and side headers on desktop.
- Moneyness, ATM marker, OI/change/volume/price/IV/Greeks where available.
- Mobile mode may use strike cards or horizontal table containment, never page-wide overflow.

### Options Analytics

- PCR OI/volume.
- Max pain and payout context.
- Static/fresh walls and unwinding.
- Expiry term structure and observation coverage.

### Exposure & GEX

- Estimated GEX/DEX, gamma flip and strike exposure.
- Clearly label estimation assumptions and incomplete contracts.
- No claim of live dealer positioning.

### Opportunities

- Ranked candidate cards/table with direction, contract, expiry, strike, moneyness, liquidity, expected move, score and rationale.
- `NO_TRADE` and `NONE` remain valid explicit outcomes.
- Filters must not imply recommendations beyond deterministic engine output.

### Participant Positioning

Participant OI/volume and FII derivative statistics with time windows and source completeness.

## 8. Opportunity Board

**Target route:** `/opportunities`

### Purpose

Unify equity and derivatives candidates without merging their score semantics.

### Content

- Separate equity and options sections.
- Rank, score components, confidence, freshness and validation status.
- Filters for asset type, date, direction, sector, expiry and minimum liquidity/confidence.
- Links to Stock360, Derivatives and Validation with context preserved.

## 9. Data Workspace

**Target route:** `/data`  
**Read:** documented data permission  
**Write:** `market_data.write`

### Content

- Data status and latest available dates by dataset.
- Upload, latest/date download and bounded backfill forms.
- Import history and sanitized failures.
- Clear source/format/mode, delivery enrichment and archival status.

### Boundary

Do not place option chains, futures analytics or opportunity research on this page.

## 10. Coverage & Quality

**Target route:** `/data/coverage`

### Content

- Calendar/date coverage by equity, futures, options, participants, fundamentals and intelligence datasets.
- Readiness thresholds and missing-session explanation.
- Data quality issues and historical certification.
- Filters by date, symbol, dataset and issue severity.

### Actions

Authorized users may launch a bounded backfill or open the relevant import record. The page itself is a complete shell page, not an HTMX fragment.

## 11. Pipeline and Scheduler

### Pipeline

**Route:** `/pipeline`  
Show current/last run, stages, counts, duration, failed stage and history. `pipeline.run` controls manual triggers.

### Scheduler

**Route:** `/scheduler`  
Show EOD, notification and summary jobs; enabled state, next/last run, retry/catch-up state and manual actions. `scheduler.run` controls execution.

The two pages cross-link but remain distinct: pipeline is execution history; scheduler is automation policy/status.

## 12. Backtesting

**Routes:** `/backtests`, `/backtests/strategies`, `/backtests/runs`, `/backtests/runs/{id}`

### Workspace

Create a run and show recent runs. Explain synchronous execution limitations until a worker queue exists.

### Strategies

Create/edit/deactivate strategies, with read-only behavior when `backtests.write` is absent.

### Run detail

Status, parameters, benchmark, metrics, equity/drawdown charts and trades. Preserve owner/admin access rules and label admin override context.

## 13. Validation Lab

**Target route:** `/validation`

### Purpose

Evaluate historical opportunity behavior and coverage independently from strategy backtesting.

### Content

- Dataset/strategy or opportunity selector.
- Evaluation period and walk-forward boundaries.
- Coverage certification and exclusions.
- Outcome metrics, distributions and limitations.
- Direct comparison between current score/rule and historical behavior where supported.

### Boundary

Do not label this page Backtesting. Backtesting simulates trades; validation evaluates research or opportunity behavior and data sufficiency.

## 14. Portfolio, Alerts and Notifications

### Portfolio

Portfolio selector, valuation/P&L summary, holdings, allocation and stock drill-down. Mutations require `portfolio.write`.

### Alerts

Rule builder, scope, cooldown, active state, recent events and manual run. Mutations require `alerts.write`.

### Notifications

- Inbox: actionable in-app items and unread state.
- History: delivery attempts/status, retry and detail.
- Settings: channels, provider tests, event preferences, quiet hours and daily summary.

## 15. Administration and Account

### Administration

Users, Roles and Permissions are separate local tabs under an admin workspace. All visibility and route access use the same admin predicate.

### Account

Profile, Preferences and Sessions remain in the account menu. Session revocation requires confirmation and clear current-session labeling.

## 16. Responsive and Accessibility Acceptance

- No page-wide horizontal overflow at 390px, 768px and desktop widths.
- Tables use contained scrolling or alternate mobile presentations.
- Forms have explicit labels, errors and input help.
- Color is never the sole signal for regime, score, status or P&L.
- Tabs, dialogs, dropdowns and off-canvas navigation are keyboard operable.
- HTMX swaps preserve focus or move it predictably.
- Charts have textual summaries and accessible labels.
- Loading indicators and disabled states prevent duplicate mutations.

## 17. Product Truth Requirements

- Use `LIVE` only when a certified live data path is active.
- Preserve explicit `REPLAY`, `MARKET_CLOSED`, delayed, stale and unavailable labels.
- Display `NO_TRADE` and `NONE` without replacing them with an apparently actionable contract.
- AI explains deterministic calculations; it does not silently replace scores, indicators or scanners.
- Missing values remain missing, not zero.
