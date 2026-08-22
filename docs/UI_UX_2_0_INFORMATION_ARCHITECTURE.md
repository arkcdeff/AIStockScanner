# UI/UX 2.0 Information Architecture

**Sprint:** 20.0  
**Date:** 2026-08-22  
**Scope:** Target product structure and navigation design; no implementation

## 1. Design Principles

1. Organize by user intent, not by service or migration history.
2. Give every advertised capability a truthful destination.
3. Separate data operations from market research.
4. Preserve one canonical page per domain and use drill-downs for detail.
5. Show provenance, freshness, coverage and replay/live state wherever conclusions are displayed.
6. Keep advanced analysis discoverable but progressively disclosed.
7. Respect RBAC in both route enforcement and navigation visibility.
8. Preserve existing URLs initially where practical; add redirects only when concrete compatibility requires them.

## 2. Primary Personas

| Persona | Primary needs |
|---|---|
| Analyst | Market regime, stock research, scanners, derivatives, opportunities and validation |
| Portfolio user | Watchlists, holdings, alerts, notifications and stock drill-downs |
| Data operator | Import, backfill, coverage, quality, pipeline and scheduler health |
| Administrator | Users, roles, permissions, sessions, provider/system status |

## 3. Proposed Product Map

```text
Overview
  Dashboard

Markets
  Market Overview
  Market Intelligence
  Equity Analytics
  Sectors & Breadth

Stocks
  Stock Explorer
  Stock360 Workspace
  Watchlists
  Scanners
    Scanner Library
    Run Scanner
    Results

Derivatives
  Derivatives Overview
  Futures Analytics
  Option Chain
  Options Analytics
  Exposure & GEX
  Options Opportunities
  Participant Positioning

Research
  Opportunity Board
  Backtesting
    New Backtest
    Strategies
    Run History
  Validation Lab
  AI Explanations

Portfolio
  Portfolio
  Alerts
  Notifications
    Inbox
    Delivery History
    Settings

Data & Operations
  Data Workspace
  Coverage & Quality
  Indicators
  Pipeline
  Scheduler

Administration
  Users
  Roles
  Permissions

Account
  Profile
  Preferences
  Sessions
```

## 4. Proposed Sidebar

The desktop sidebar should expose stable top-level workspaces. Secondary routes should use local subnavigation rather than creating an excessively long primary menu.

| Group | Primary items | Contextual/local items |
|---|---|---|
| Overview | Dashboard | None |
| Markets | Market Overview, Market Intelligence, Equity Analytics | Sectors, breadth, macro and global factors within Market Intelligence |
| Stocks | Stock Explorer, Watchlists, Scanners | Scanner Library, Run, Results; Stock360 opens from a selected symbol |
| Derivatives | Derivatives Overview, Options Opportunities | Futures, Option Chain, Options Analytics, Exposure/GEX, Positioning as workspace tabs |
| Research | Opportunity Board, Backtesting, Validation Lab, AI Explanations | Strategies and run history within Backtesting |
| Portfolio | Portfolio, Alerts, Notifications | Notification history/settings |
| Data & Operations | Data Workspace, Coverage & Quality, Pipeline | Indicators and Scheduler, based on operator permissions |
| Administration | Users, Roles, Permissions | Admin only |

### Mobile behavior

- Retain the existing off-canvas pattern.
- Keep the same grouping and ordering as desktop.
- Open each workspace with local horizontal scrolling tabs or a compact section selector.
- Do not hide essential filters behind hover-only interactions.

## 5. Canonical Route Proposal

These are target routes for implementation planning. Existing routes remain authoritative until a later implementation sprint approves changes.

| Target route | Purpose | Existing source to reuse |
|---|---|---|
| `/dashboard` | Decision-oriented overview | Existing dashboard summaries |
| `/markets` | Market overview and major indices | Dashboard/market intelligence data |
| `/market-intelligence` | Regime and factor workspace | Existing page and component services |
| `/analytics` | Equity rankings and score exploration | Existing analytics page/API |
| `/stocks` | Search/browse launcher | Existing stock list |
| `/stocks/{symbol}` | Canonical Stock360 workspace | Existing stock detail plus Stock360/fundamental/news components |
| `/scanners` | Scanner workspace landing | Existing `/scanner-library` |
| `/scanners/run` | Execute scanner | Existing `/scanner-run` |
| `/scanners/results` | Results/filter/export | Existing `/scanner-results/` |
| `/derivatives` | Derivatives summary | Existing Market Data derivative tabs |
| `/derivatives/futures` | Futures analytics | Existing futures partial/API |
| `/derivatives/options/chain` | Option chain | Existing option chain partial/API |
| `/derivatives/options/analytics` | PCR/max pain/OI walls | Existing analytics partial/API |
| `/derivatives/exposure` | GEX/DEX/gamma flip | Existing hidden partial/service |
| `/derivatives/opportunities` | Ranked actionable candidates | Existing opportunity services/partial |
| `/derivatives/positioning` | Participant/FII positioning | Existing participant components |
| `/opportunities` | Cross-domain opportunity board | Existing hidden board |
| `/backtests` | Backtesting workspace | Existing pages |
| `/validation` | Historical/walk-forward validation | Existing hidden validation lab |
| `/data` | Import/backfill operations | Existing `/market-data` operations |
| `/data/coverage` | Coverage, quality and certification | Existing coverage and certification services |
| `/pipeline` | Pipeline operations | Existing page |
| `/scheduler` | Scheduler operations | Existing page |

## 6. Workspace Architecture

### Dashboard

Purpose: answer "What requires attention now?" It should not reproduce every domain page.

Recommended order:

1. Market state and freshness strip.
2. Priority opportunities and alerts.
3. Watchlist/portfolio impact.
4. Operational exceptions.
5. Recent activity and drill-down links.

Remove or consolidate repeated AI tables, import details and large generic tables that already have dedicated pages.

### Market Intelligence

Use one summary header and local navigation:

- Regime.
- Indian and global markets.
- Institutional and participant flows.
- Breadth and sectors.
- Macro/external factors.
- AI explanation.

Each section must display evaluation time, source, completeness, confidence and limitations.

### Stock360

The symbol page becomes the canonical stock research record. Proposed local navigation:

- Summary.
- Price & Technicals.
- Fundamentals.
- News & Policy.
- Derivatives.
- Scanners & Opportunities.
- AI Explanation.
- Portfolio, Watchlist & Alerts.

The summary should synthesize rather than duplicate every panel. Heavy tables load on demand.

### Derivatives Workspace

Use a stable context bar containing underlying, trade date/evaluation time, expiry, source, replay/live state and coverage. Local tabs:

- Overview.
- Futures.
- Option Chain.
- Options Analytics.
- Exposure & GEX.
- Opportunities.
- Participant Positioning.

This removes derivative research from the ingestion-focused Market Data page.

### Data & Operations

Separate user goals:

- Data Workspace: upload, date/latest download, backfill and import history.
- Coverage & Quality: date coverage, readiness, issues and historical certification.
- Indicators: calculation and status.
- Pipeline: end-to-end runs and failures.
- Scheduler: configuration/status/manual run controls.

## 7. Cross-Page Context

The following context should remain visible while users drill down:

- Selected symbol or underlying.
- Evaluation/trade date.
- Expiry for derivative views.
- Data source and freshness.
- Replay, delayed, closed-market or live status.
- Confidence/completeness when displaying inferred intelligence.

Deep links should encode safe read-only context in query parameters. Mutations continue to use CSRF-protected POST operations.

## 8. Search and Launching

Global search should launch users into canonical destinations rather than returning disconnected snippets:

- Symbol result -> Stock360.
- Scanner result -> scanner result details or Stock360 with scanner context.
- Backtest run -> run detail.
- Alert/notification -> source entity.
- Data issue -> Coverage & Quality filtered to the affected date/symbol.

## 9. Permission Model for Navigation

| Capability | Navigation rule |
|---|---|
| Read page | Show only when the matching read permission is present |
| Mutating actions | Page may remain visible, but hide/disable actions without write/run permission and explain why |
| Admin | Use the same administrator predicate as page enforcement |
| Ownership | Show owned records by default; label admin override access |

Pipeline, Scheduler and Indicators should receive documented domain read semantics instead of relying implicitly on `stocks.read`.

## 10. State Standards

Every workspace should consistently support:

- Loading state for HTMX requests.
- Empty state explaining prerequisites and the next valid action.
- Error state with sanitized detail and retry path.
- Permission-denied state using the shared 403 behavior.
- Stale/partial/unavailable state without presenting missing data as zero.
- Source and replay/live badges using truthful labels.

## 11. Migration Strategy

1. Correct labels and dead/misleading destinations before visual redesign.
2. Introduce canonical full-page wrappers around existing partials.
3. Add local workspace navigation and deep-link state.
4. Move content gradually; do not duplicate business logic.
5. Preserve tested old routes until internal links and browser tests move to canonical routes.
6. Remove obsolete aliases only after evidence shows no external consumer requires them.

## 12. Success Criteria

- Every sidebar item opens a complete page matching its label.
- Every implemented P0 research capability is reachable in two interactions or fewer.
- No two primary sidebar items resolve to the same undifferentiated state.
- Operational actions are separated from analytical interpretation.
- All analytics expose date, source, freshness and replay/live state.
- Desktop, tablet and mobile preserve the same information hierarchy.
- RBAC visibility matches route enforcement.
